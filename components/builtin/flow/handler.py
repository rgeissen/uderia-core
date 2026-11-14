"""
Flow Component Handler.

Processes TDA_Flow tool calls into flow canvas render payloads.
Delegates CRUD and execution to flow_db.py and flow_executor.py.
"""

import asyncio
import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)

logger = logging.getLogger("quart.app")

# ── LLM-invoked execute: await-to-completion ──────────────────────────────────
# When the LLM calls TDA_Flow(action="execute") in a chat turn, the user expects
# the turn to report the flow's result. But execute_flow() is fire-and-forget
# (returns a run_id immediately so the canvas can animate and long runs don't
# block). So _handle_execute polls the run to a terminal status and returns the
# final node outputs in the tool result — the planner's next phase (e.g.
# TDA_FinalReport) can then report the real numbers. The REST /execute endpoint
# and the canvas Run button call execute_flow directly and stay async; only this
# LLM tool path awaits. The canonical emitter still feeds the per-run SSE stream,
# so an open canvas animates live while we poll. On timeout we fall back to the
# async "initiated, still running" behaviour.
FLOW_EXECUTE_WAIT_TIMEOUT = 120.0   # seconds to await an LLM-invoked run
FLOW_EXECUTE_POLL_INTERVAL = 1.5    # seconds between run-status polls

# ── Snapshot debounce ─────────────────────────────────────────────────────────
# When the LLM calls multiple primitive ops in rapid succession (add_node,
# remove_edge, add_edge × 2), we want only ONE version snapshot — not four.
# Each call to _schedule_snapshot cancels any pending snapshot for that flow
# and reschedules it 2 seconds later.
_snapshot_tasks: Dict[str, Any] = {}


def _flow_words(flow: Dict[str, Any], suffix: str = "") -> str:
    """The row summary for a flow: its name and its size in words (the key fact on the
    line — a collapsed row is never a label without content)."""
    d = (flow or {}).get("definition") or {}
    n = len(d.get("nodes") or [])
    m = len(d.get("edges") or [])
    # the row is the ONE place the flow's name and size are stated (the card inside the row
    # shows what the row does not: the description, the node types, the actions)
    base = f"{(flow or {}).get('name') or 'Flow'} · {n} node{'' if n == 1 else 's'} · {m} edge{'' if m == 1 else 's'}"
    return f"{base} · {suffix}" if suffix else base


def _schedule_snapshot(flow_id: str, user_uuid: str, delay: float = 2.0) -> None:
    """Debounce snapshot_flow so burst ops produce exactly one snapshot."""
    from trusted_data_agent.core.flow_db import snapshot_flow

    existing = _snapshot_tasks.get(flow_id)
    if existing and not existing.done():
        existing.cancel()

    async def _delayed():
        try:
            await asyncio.sleep(delay)
            snapshot_flow(flow_id, user_uuid)
            logger.info(f"[FlowHandler] debounced snapshot created for {flow_id}")
        except asyncio.CancelledError:
            raise  # superseded by a newer burst op — expected, not an error
        except Exception as e:
            logger.warning(f"[FlowHandler] debounced snapshot failed: {e}")
        finally:
            # Only clear if we are still the registered task (a newer op may have
            # already replaced us).
            if _snapshot_tasks.get(flow_id) is task:
                _snapshot_tasks.pop(flow_id, None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called outside an event loop (e.g. a sync test) — skip silently;
        # an explicit snapshot still happens on the next create/insert path.
        return
    # Keep a strong reference in _snapshot_tasks so the task is not garbage
    # collected mid-flight (asyncio only holds a weak reference to it).
    task = loop.create_task(_delayed())
    _snapshot_tasks[flow_id] = task


class FlowComponentHandler(BaseComponentHandler):
    """
    Handler for the TDA_Flow component.

    Supports all graph mutation actions (add_node, add_edge, create_scope, etc.)
    plus execution actions (execute, get_run, cancel_run).

    Every mutating action returns an inline card payload that renders in chat
    with an "Open in Canvas" button. Clicking it opens the React Flow canvas
    in the flow-split-panel (same pattern as KnowledgeGraph and Scheduler).
    """

    @property
    def component_id(self) -> str:
        return "flow"

    @property
    def tool_name(self) -> str:
        return "TDA_Flow"

    # Opt into the generic per-instance export/import contract (see base.py).
    supports_instance_io = True

    @staticmethod
    def _find_str_by_key(value: Any, key: str) -> Optional[str]:
        """Find the first non-empty string stored under `key` anywhere in `value`.

        Only a value sitting under the named key counts as a hit — arbitrary nested
        strings (e.g. a payload's "status": "success") are ignored. Mirrors the
        executor's _find_value_by_key but constrained to string results.
        """
        if isinstance(value, dict):
            direct = value.get(key)
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
            for v in value.values():
                found = FlowComponentHandler._find_str_by_key(v, key)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = FlowComponentHandler._find_str_by_key(item, key)
                if found:
                    return found
        return None

    @staticmethod
    def _extract_id_string(value: Any, key: str) -> Optional[str]:
        """Coerce a flow_id / run_id argument to a plain string.

        A string value passes through directly; a wrapped structure is searched for
        the id under `key` (e.g. a create payload's metadata.flow_id /
        spec.flow.flow_id). Returns None when no such id exists — the caller then
        leaves the original value untouched so downstream produces a clean
        'not found' / 'required' error instead of a crash. Deterministic for the
        create→execute chain: every copy of the id in the payload is identical.
        """
        if isinstance(value, str):
            return value.strip() or None
        return FlowComponentHandler._find_str_by_key(value, key)

    async def process(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any],
    ) -> ComponentRenderPayload:
        tool_args = arguments
        action = tool_args.get("action", "")
        user_uuid = context.get("user_uuid", "")

        # Coerce flow_id / run_id to a plain string. The OPTIM (tool_enabled)
        # planner chains phases (e.g. create → execute) and references a prior
        # phase's result with a placeholder. A *keyless* placeholder
        # ({"source": "result_of_phase_N"}) makes the executor fall back to its
        # generic single-value unwrap, which can't reduce a component payload to
        # one value and so passes the ENTIRE result structure (a list/dict)
        # through as the argument — which then reaches get_flow() → SQLite bind
        # error ("type 'list' is not supported"). The real id IS inside that
        # structure (metadata.flow_id / spec.flow.flow_id), so dig it out here and
        # every action stays robust to how the id arrived.
        for _ref_key in ("flow_id", "run_id"):
            _ref_val = tool_args.get(_ref_key)
            if _ref_val is not None and not isinstance(_ref_val, str):
                _coerced = self._extract_id_string(_ref_val, _ref_key)
                if _coerced:
                    logger.warning(
                        f"[FlowHandler] Coerced non-string {_ref_key} "
                        f"({type(_ref_val).__name__}) to '{_coerced}' for action '{action}'."
                    )
                    tool_args = {**tool_args, _ref_key: _coerced}

        try:
            if action == "create":
                return await self._handle_create(tool_args, user_uuid, context)
            elif action == "open":
                return await self._handle_open(tool_args, user_uuid)
            elif action == "get":
                return await self._handle_get(tool_args, user_uuid)
            elif action == "list":
                return await self._handle_list(tool_args, user_uuid)
            elif action == "update":
                return await self._handle_update(tool_args, user_uuid)
            elif action == "delete":
                return await self._handle_delete(tool_args, user_uuid)
            elif action in ("add_node", "remove_node", "update_node",
                            "add_edge", "remove_edge",
                            "create_scope", "update_scope", "delete_scope",
                            "update_global_params"):
                return await self._handle_patch(action, tool_args, user_uuid)
            elif action == "insert_node":
                return await self._handle_insert_node(tool_args, user_uuid)
            elif action == "execute":
                return await self._handle_execute(tool_args, user_uuid, context)
            elif action == "get_run":
                return await self._handle_get_run(tool_args, user_uuid)
            elif action == "cancel_run":
                return await self._handle_cancel_run(tool_args, user_uuid)
            else:
                return self._error_payload(f"Unknown action: '{action}'")
        except Exception as exc:
            logger.error(f"FlowComponentHandler error ({action}): {exc}", exc_info=True)
            return self._error_payload(str(exc))

    # ------------------------------------------------------------------
    # CRUD handlers
    # ------------------------------------------------------------------

    def _canonicalize_node_profile(self, obj: Dict, user_uuid: str) -> None:
        """Resolve a `profile_id` tag (e.g. "OPTIM"/"@OPTIM") to a real profile ID, in place.

        The LLM writes friendly tags into a task_llm node's `profile_id`, but the
        inspector's PROFILE ID dropdown lists real IDs — so a stored tag renders
        as "— select profile —" (looks unset), and execution would rely on the
        runtime fallback. Resolving at authoring time stores a canonical ID, so
        the dropdown reflects the choice AND runtime needs no fallback. Same
        pattern as the add_edge label resolution. Handles `profile_id` nested
        under `data` (full nodes) or at the top level (update dicts). No-op when
        absent or already a real ID; fails open via _resolve_profile_ref.
        """
        if not isinstance(obj, dict):
            return
        from trusted_data_agent.core.flow_executor import _resolve_profile_ref
        data = obj.get("data")
        if isinstance(data, dict) and data.get("profile_id"):
            data["profile_id"] = _resolve_profile_ref(data["profile_id"], user_uuid)
        if obj.get("profile_id"):
            obj["profile_id"] = _resolve_profile_ref(obj["profile_id"], user_uuid)

    @staticmethod
    def _user_profiles(user_uuid: str) -> List[Dict[str, Any]]:
        """The user's profiles as plain dicts (the same source _resolve_profile_ref
        reads). Fails open to [] — pre-fill is a convenience, never a blocker."""
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            return list(get_config_manager().get_profiles(user_uuid=user_uuid) or [])
        except Exception as exc:
            logger.debug(f"[FlowHandler] profiles unavailable for pre-fill: {exc}")
            return []

    async def _handle_create(self, args: Dict, user_uuid: str, context: Dict = None) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import create_flow
        from trusted_data_agent.core.flow_executor import generate_empty_flow, FlowDefinition, _resolve_profile_ref
        from trusted_data_agent.core import flow_topologies as topo

        description = args.get("description") or ""
        topology = (args.get("topology") or "").strip() or None
        _creator_profile = (context or {}).get("profile_id")
        prefill_report = None
        fill_report = None

        if topology and not args.get("definition"):
            # PROP-007 — create FROM a topology template through the ONE generator the
            # REST endpoint and the picker use. A COPY stamped definition.template.
            _resolver = lambda r: _resolve_profile_ref(r, user_uuid)  # noqa: E731
            template = topo.get_template(topology)
            try:
                params = dict(args.get("params") or {})
                profiles = self._user_profiles(user_uuid)
                coordinator = next((p for p in profiles if p.get("id") == _creator_profile), None)
                experts = topo.experts_of(coordinator, profiles) if coordinator else []
                if template and experts:
                    # §7 — the picker pre-fills from a coordinator's experts; the tool
                    # path does the same when the creating agent IS a coordinator.
                    params = topo.adapt_params(template, len(experts), params)
                definition, fill_report = topo.generate(topology, params, None, _resolver)
                if template and experts:
                    prefill_report = topo.apply_prefill(definition, template, experts)
                # Explicit fills (slot node id or role → @TAG) win over the pre-fill.
                fill_report = topo.apply_fills(definition, args.get("fills") or {}, _resolver)
            except topo.FlowTemplateUnavailable as exc:
                return self._error_payload(
                    f"Flow not created — {exc}. Available topologies: "
                    + ", ".join(t["key"] for t in topo.catalog() if t.get("available"))
                    + ". Omit 'topology' for a blank flow."
                )
            source_tag = (coordinator or {}).get("tag") if experts else None
            name = args.get("name") or topo.default_name(topology, source_tag)
        else:
            name = args.get("name") or "Untitled Flow"
            definition = args.get("definition") or generate_empty_flow()
        if args.get("global_params"):
            definition.setdefault("global_params", {}).update(args["global_params"])

        # Default execution profile = the profile that created the flow (the active
        # session profile). task_llm nodes without an explicit per-node profile_id
        # inherit it at run time (executor cascade). The author can change it via
        # the header. This is why a freshly-built flow "just runs" under a sensible
        # profile instead of every node depending on the LLM stamping one.
        if _creator_profile:
            settings = definition.setdefault("settings", {})
            settings.setdefault("defaultProfileId", _creator_profile)

        # Store canonical profile IDs (resolve any tag the LLM wrote) so the
        # inspector dropdown reflects each node's profile and runtime is exact.
        for _n in definition.get("nodes", []):
            self._canonicalize_node_profile(_n, user_uuid)

        # Enforce graph integrity at authoring time. The REST create endpoint
        # validates, but the LLM tool path used to persist broken graphs
        # silently (e.g. edges wired to branch nodes the model never created —
        # 6 edges / 4 nodes). Return an actionable error so the model fixes it
        # in the same turn instead of leaving a dangling-edge canvas warning.
        errors = FlowDefinition(definition).validate()
        if errors:
            detail = "; ".join(errors[:8])
            return self._error_payload(
                f"Flow not created — the definition has dangling edges: {detail}. "
                "Every edge must connect nodes that exist in the 'nodes' list. "
                "Add the missing node(s) — for a decision flow that means creating "
                "BOTH branch nodes — or remove the edges that point to nodes you "
                "did not create, then call create again."
            )

        # Canonicalise + validate node value references. Resolvable references are
        # rewritten to clean syntax in place (so the canvas never shows the broken
        # form); a reference to a value the upstream step can't produce is rejected
        # so the model fixes it this turn instead of shipping a silently-wrong decision.
        from trusted_data_agent.core.flow_validation import validate_and_repair
        ref_errors, _ = validate_and_repair(definition)
        if ref_errors:
            return self._error_payload(
                "Flow not created — fix the node value references. "
                + " ".join(ref_errors[:5])
            )

        flow = create_flow(user_uuid, name, description, definition)
        # Snapshot the initial state so version v1 exists from the very start
        try:
            from trusted_data_agent.core.flow_db import snapshot_flow
            snapshot_flow(flow["flow_id"], user_uuid, label="v1")
        except Exception as e:
            logger.warning(f"[FlowHandler] initial snapshot failed: {e}")
        payload = self._flow_canvas_payload(flow, action="create", user_uuid=user_uuid)
        if topology:
            payload.metadata["topology"] = topology
            if prefill_report is not None:
                payload.metadata["prefill"] = prefill_report
            if fill_report is not None and (fill_report.get("rejected") or fill_report.get("ignored_keys")):
                payload.metadata["fills"] = fill_report
        return payload

    @staticmethod
    def _open_slots_note(open_slots: List[Dict[str, Any]]) -> str:
        """Recovery-shaped: what is open, and the exact call that fills it."""
        from trusted_data_agent.core.flow_topologies import slot_refusal_message
        return (slot_refusal_message(open_slots)
                + " To fill one: action='update_node', flow_id, node_id=<the slot's node_id>, "
                  "node_updates={data:{profile_id:'@TAG'}}.")

    async def _handle_open(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        """Surface an existing flow's canvas from any session.

        Unlike 'get' (which returns a silent payload for the LLM to read node
        IDs), 'open' returns a visible canvas payload with action='open'. The
        renderer renders an inline "Open in Canvas" card and — when canvas split
        mode is active — auto-opens the split panel in the *current* session.
        This is what lets a user open, in session B, a flow created in session A.
        """
        from trusted_data_agent.core.flow_db import get_flow

        flow_id = args.get("flow_id")
        if not flow_id:
            return self._error_payload("flow_id is required for 'open'")

        flow = get_flow(flow_id, user_uuid)
        if not flow:
            return self._error_payload(f"Flow '{flow_id}' not found")

        return self._flow_canvas_payload(flow, action="open")

    async def _handle_get(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import get_flow

        flow_id = args.get("flow_id")
        if not flow_id:
            return self._error_payload("flow_id is required for 'get'")

        flow = get_flow(flow_id, user_uuid)
        if not flow:
            return self._error_payload(f"Flow '{flow_id}' not found")

        # Build a next-action hint so the LLM knows to use insert_node
        # rather than calling individual primitive ops after seeing node IDs.
        definition = flow.get("definition", {})
        nodes = definition.get("nodes", [])
        node_hints = [
            {"id": n["id"], "type": n.get("type"), "label": n.get("label")}
            for n in nodes
        ]
        edges = definition.get("edges", [])
        edge_hints = [
            {"id": e["id"], "source": e.get("source"), "target": e.get("target"),
             "type": e.get("type", "normal")}
            for e in edges
        ]
        start_id = next((n["id"] for n in nodes if n.get("type") == "start"), None)
        end_id   = next((n["id"] for n in nodes if n.get("type") == "end"),   None)

        hint = (
            f"IMPORTANT: Now use action='insert_node' — do NOT call add_node/remove_edge/add_edge separately. "
            f"Example: action='insert_node', flow_id='{flow_id}', "
            f"after_node_id='{start_id or 'start'}', node={{type, label, data}}. "
            f"The backend rewires all edges automatically."
        )

        # Return a silent (hidden) payload — no chat card appears.
        # The LLM sees _nodes and _next_action_hint in its tool result and
        # should proceed directly with insert_node.
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "silent_get",
                "flow_id": flow_id,
                "_nodes": node_hints,
                "_edges": edge_hints,
                "_next_action_hint": hint,
            },
            title="",
            metadata={"action": "get", "flow_id": flow_id},
        )

    async def _handle_list(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import list_flows

        flows = list_flows(user_uuid, include_templates=True)
        # Only render visibly in chat when the user explicitly asked to see their flows.
        # The LLM never sets show_in_chat, so its internal list calls stay silent.
        show = args.get("show_in_chat", False)
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={"view": "list" if show else "silent_list", "flows": flows},
            title=f"{len(flows)} flow{'' if len(flows) == 1 else 's'}",
            metadata={"action": "list", "count": len(flows)},
        )

    async def _handle_update(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import update_flow

        flow_id = args.get("flow_id")
        if not flow_id:
            return self._error_payload("flow_id is required for 'update'")

        updated = update_flow(
            flow_id, user_uuid,
            name=args.get("name"),
            description=args.get("description"),
        )
        if not updated:
            return self._error_payload(f"Flow '{flow_id}' not found")
        return self._flow_canvas_payload(updated, action="update")

    async def _handle_delete(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import delete_flow

        flow_id = args.get("flow_id")
        if not flow_id:
            return self._error_payload("flow_id is required for 'delete'")

        deleted = delete_flow(flow_id, user_uuid)
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={"deleted": deleted, "flow_id": flow_id},
            title="Flow deleted",
            metadata={"action": "delete"},
        )

    # ------------------------------------------------------------------
    # Graph patch handler — all structural mutations route here
    # ------------------------------------------------------------------

    def _resolve_node_ref(self, ref, nodes):
        """Resolve a node reference to a real node ID.

        The whole component trains the LLM never to invent UUIDs (insert_node
        accepts 'start'/'end'/labels), so the model naturally passes labels to
        add_edge too — where they used to dangle. This resolves a reference the
        same way: real ID → 'start'/'end' shorthand → exact label → type. Uses
        exact matches only (no substring heuristic, so 'end' won't match
        'Send report'). Returns the original ref unchanged if nothing matches —
        the caller validates and emits an actionable error.
        """
        if not ref:
            return ref
        if any(n.get("id") == ref for n in nodes):
            return ref
        r = str(ref).strip().lower()
        if r in ("start", "begin", "beginning"):
            m = next((n["id"] for n in nodes if n.get("type") == "start"), None)
            if m:
                return m
        if r in ("end", "finish", "terminal"):
            m = next((n["id"] for n in nodes if n.get("type") == "end"), None)
            if m:
                return m
        for n in nodes:
            label = (n.get("label") or n.get("data", {}).get("label") or "").strip().lower()
            if label and label == r:
                return n["id"]
        for n in nodes:
            if n.get("type", "").lower() == r:
                return n["id"]
        return ref

    async def _handle_patch(self, action: str, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import LoopError, apply_patch, snapshot_flow

        flow_id = args.get("flow_id")
        if not flow_id:
            return self._error_payload("flow_id is required for graph mutations")

        # Map action → patch op and payload
        op_map = {
            "add_node":           ("add_node", {"node": args.get("node", {})}),
            "remove_node":        ("remove_node", {"node_id": args.get("node_id")}),
            "update_node":        ("update_node", {"node_id": args.get("node_id"),
                                                    "updates": args.get("node_updates", {})}),
            "add_edge":           ("add_edge", {"edge": args.get("edge", {})}),
            "remove_edge":        ("remove_edge", {"edge_id": args.get("edge_id")}),
            "create_scope":       ("create_scope", {"scope": args.get("scope", {})}),
            "update_scope":       ("update_scope", {"scope_id": args.get("scope_id"),
                                                     "updates": args.get("scope_updates", {})}),
            "delete_scope":       ("delete_scope", {"scope_id": args.get("scope_id")}),
            "update_global_params": ("update_global_params", {"params": args.get("global_params", {})}),
        }

        if action not in op_map:
            return self._error_payload(f"Unknown patch action: '{action}'")

        op, payload = op_map[action]

        # Store canonical profile IDs on node mutations — resolve any tag the LLM
        # wrote ("OPTIM") to a real ID so the inspector dropdown reflects it.
        if action == "add_node":
            self._canonicalize_node_profile(payload.get("node"), user_uuid)
        elif action == "update_node":
            self._canonicalize_node_profile(payload.get("updates"), user_uuid)

        # Edge integrity: resolve label/shorthand references to real IDs (so the
        # LLM never needs UUIDs for edges either), then refuse to wire an edge to
        # a node that still doesn't exist. apply_patch(add_edge) appends
        # unconditionally, so without this the LLM can dangle an edge incrementally.
        if action == "add_edge":
            from trusted_data_agent.core.flow_db import get_flow
            _flow = get_flow(flow_id, user_uuid)
            if not _flow:
                return self._error_payload(f"Flow '{flow_id}' not found")
            _nodes = _flow.get("definition", {}).get("nodes", [])
            _node_ids = {n["id"] for n in _nodes}
            _edge = payload.get("edge", {})
            for _end in ("source", "target"):
                _edge[_end] = self._resolve_node_ref(_edge.get(_end), _nodes)
            payload["edge"] = _edge
            _missing = [n for n in (_edge.get("source"), _edge.get("target"))
                        if n and n not in _node_ids]
            if _missing:
                return self._error_payload(
                    f"Cannot add edge — node(s) {_missing} do not exist in this flow. "
                    "Pass an existing node's label or ID (or 'start'/'end'), or create "
                    "the node first (add_node / insert_node), then connect it."
                )

        try:
            updated, _ = apply_patch(flow_id, user_uuid, {"op": op, "payload": payload})
        except LoopError as exc:
            return self._error_payload(exc.message)
        if not updated:
            return self._error_payload(f"Flow '{flow_id}' not found")

        # Debounced snapshot — coalesces rapid burst ops into one version entry
        _schedule_snapshot(flow_id, user_uuid)

        # Return a silent patch payload — the frontend fires a CustomEvent to
        # update the live canvas in-place.  No inline chat card is rendered.
        definition = updated.get("definition", {})
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "silent_patch",
                "flow": updated,
                "patch": {"op": op, "payload": payload},
            },
            title=_flow_words(updated, "updated"),
            metadata={
                "action": action,
                "flow_id": flow_id,
                "node_count": len(definition.get("nodes", [])),
                "edge_count": len(definition.get("edges", [])),
            },
        )

    # ------------------------------------------------------------------
    # High-level graph operations — one LLM call, backend does the surgery
    # ------------------------------------------------------------------

    async def _handle_insert_node(
        self, args: Dict, user_uuid: str
    ) -> ComponentRenderPayload:
        """
        Insert a new node into an existing edge in one atomic operation.

        The LLM provides:
          - flow_id
          - node  (type, label, data)  — id and position auto-computed
          - after_node_id  — insert after this node (removes its outgoing edge
                             and replaces it with two new edges through the
                             new node)

        Backend does: remove old edge → add node → add edge1 → add edge2.
        Frontend receives a single 'insert_node' patch event and animates
        everything atomically.
        """
        from trusted_data_agent.core.flow_db import (
            apply_patch, get_flow, snapshot_flow,
        )

        flow_id = args.get("flow_id")
        after_node_id = args.get("after_node_id")
        node_spec = dict(args.get("node") or {})

        if not flow_id:
            return self._error_payload("flow_id is required for 'insert_node'")

        flow = get_flow(flow_id, user_uuid)
        if not flow:
            return self._error_payload(f"Flow '{flow_id}' not found")

        definition = flow.get("definition") or {}
        nodes = definition.get("nodes", [])
        edges = definition.get("edges", [])

        # ── Resolve after_node_id ─────────────────────────────────────────────
        # The LLM often passes a type name ("start"), a label ("Start"), or a
        # made-up placeholder ("start-node-id") rather than the real UUID.
        # We resolve to the actual node ID so edge surgery can proceed.
        def _resolve_node_ref(ref):
            if not ref:
                return None
            # 1. Exact ID match
            if any(n["id"] == ref for n in nodes):
                return ref
            lower = ref.lower().replace("-", " ").replace("_", " ")
            # 2. Friendly type keywords
            type_map = {
                "start": "start", "beginning": "start", "first": "start",
                "end": "end", "last": "end", "finish": "end", "terminal": "end",
            }
            for keyword, node_type in type_map.items():
                if keyword in lower:
                    match = next((n["id"] for n in nodes if n.get("type") == node_type), None)
                    if match:
                        return match
            # 3. Label match (case-insensitive)
            for n in nodes:
                if n.get("label", "").lower() == lower.strip():
                    return n["id"]
            # 4. Type match (e.g. ref="task_llm")
            for n in nodes:
                if n.get("type", "").lower() == ref.lower():
                    return n["id"]
            return ref  # return as-is; will fail gracefully below

        after_node_id = _resolve_node_ref(args.get("after_node_id"))
        # ─────────────────────────────────────────────────────────────────────
        new_id = node_spec.get("id") or f"node-{uuid.uuid4().hex[:8]}"
        node_spec["id"] = new_id
        # Strip any position the LLM may have passed — _auto_layout owns all positions.
        node_spec.pop("position", None)
        # Resolve any profile_id tag the LLM wrote to a real ID (inspector + runtime).
        self._canonicalize_node_profile(node_spec, user_uuid)

        removed_edge_id = None
        edge1 = None
        edge2 = None
        next_node_id = None

        if after_node_id:
            # Find the first outgoing normal edge from after_node_id
            outgoing = [e for e in edges if e["source"] == after_node_id
                        and e.get("type", "normal") not in ("cycle",)]
            if outgoing:
                old_edge = outgoing[0]
                removed_edge_id = old_edge["id"]
                next_node_id = old_edge["target"]

                edge1 = {"id": f"edge-{uuid.uuid4().hex[:8]}",
                         "source": after_node_id, "target": new_id, "type": "normal"}
                edge2 = {"id": f"edge-{uuid.uuid4().hex[:8]}",
                         "source": new_id, "target": next_node_id, "type": "normal"}

                # Each apply_patch call runs _auto_layout — positions are always correct.
                apply_patch(flow_id, user_uuid,
                            {"op": "remove_edge", "payload": {"edge_id": removed_edge_id}})
                apply_patch(flow_id, user_uuid,
                            {"op": "add_node", "payload": {"node": node_spec}})
                apply_patch(flow_id, user_uuid,
                            {"op": "add_edge", "payload": {"edge": edge1}})
                updated, _ = apply_patch(flow_id, user_uuid,
                                         {"op": "add_edge", "payload": {"edge": edge2}})
            else:
                # No outgoing edge — just append the node
                node_spec.setdefault("position", {"x": 250, "y": 300})
                updated, _ = apply_patch(flow_id, user_uuid,
                                         {"op": "add_node", "payload": {"node": node_spec}})
        else:
            # No anchor — plain add
            node_spec.setdefault("position", {"x": 250, "y": 300})
            updated, _ = apply_patch(flow_id, user_uuid,
                                     {"op": "add_node", "payload": {"node": node_spec}})

        if not updated:
            return self._error_payload(f"Flow '{flow_id}' not found after patch")

        try:
            snapshot_flow(flow_id, user_uuid)
        except Exception as e:
            logger.warning(f"[FlowHandler] snapshot_flow failed: {e}")

        definition = updated.get("definition", {})
        # Include final _auto_layout positions so the client can animate
        # every node to its correct position in one pass — no reconcile needed.
        node_positions = {
            n["id"]: n["position"]
            for n in definition.get("nodes", [])
            if "position" in n
        }
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "silent_patch",
                "flow": updated,
                "patch": {
                    "op": "insert_node",
                    "payload": {
                        "node": node_spec,
                        "after_node_id": after_node_id,
                        "next_node_id": next_node_id,
                        "removed_edge_id": removed_edge_id,
                        "edge1": edge1,
                        "edge2": edge2,
                        "node_positions": node_positions,
                        "all_edges": definition.get("edges", []),
                    },
                },
            },
            title=_flow_words(flow, "node inserted"),
            metadata={
                "action": "insert_node",
                "flow_id": flow_id,
                "node_count": len(definition.get("nodes", [])),
                "edge_count": len(definition.get("edges", [])),
            },
        )

    # ------------------------------------------------------------------
    # Execution handlers
    # ------------------------------------------------------------------

    async def _handle_execute(
        self, args: Dict, user_uuid: str, context: Dict
    ) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_executor import execute_flow
        from trusted_data_agent.core.flow_db import get_flow

        flow_id = args.get("flow_id")
        if not flow_id:
            return self._error_payload("flow_id is required for 'execute'")

        flow = get_flow(flow_id, user_uuid)
        if not flow:
            return self._error_payload(f"Flow '{flow_id}' not found")

        # Live run events are delivered by the canonical emitter built inside
        # execute_flow — it fans every event to the per-run SSE stream AND the
        # per-user notification channel, so the open canvas animates a run started
        # from chat exactly as it does one started from its own Run button. The
        # handler no longer wires a bus-specific emitter (that initiator-specific
        # divergence was the root cause of conversation-triggered runs not showing).

        # Run nodes in the originating chat session (compact default) so the run
        # inherits and contributes to that conversation — matching the REST
        # /execute path and the documented Session Context Model. A flow can opt
        # the whole run out via settings.node_session_mode="individual", and any
        # node can opt out via data.session_context="new".
        caller_session_id = context.get("session_id")
        settings = (flow.get("definition") or {}).get("settings") or {}
        node_session_mode = settings.get("node_session_mode", "compact")

        from trusted_data_agent.core.flow_topologies import FlowSlotUnfilledError
        try:
            run_id = await execute_flow(
                flow_id=flow_id,
                user_id=user_uuid,
                input_params=args.get("input_params"),
                caller_session_id=caller_session_id,
                node_session_mode=node_session_mode,
                # Chat-triggered runs already produce the agent turn that called TDA_Flow
                # plus per-node turns in this session — a cohesive run turn would
                # triple-represent. The run turn is for headless/REST triggers only.
                record_run_turn=False,
            )
        except FlowSlotUnfilledError as exc:
            # PROP-007 §4.2 — nothing ran; name the open slots and how to fill them.
            return self._error_payload(self._open_slots_note(exc.open_slots))

        # Await the run to a terminal status so this chat turn can report the
        # actual result (see the module-level note on await-to-completion). The
        # canvas animates live via the SSE stream while we poll.
        run_result = await self._await_run_result(run_id, user_uuid)

        meta = {
            "action": "execute",
            "flow_id": flow_id,
            "run_id": run_id,
            "run_status": run_result["status"],
        }
        if run_result["status"] == "complete":
            meta["result"] = run_result["outputs"]          # [{node, output}, …]
            meta["tokens_input"] = run_result["tokens_input"]
            meta["tokens_output"] = run_result["tokens_output"]
            meta["cost_usd"] = run_result["cost_usd"]
        elif run_result["status"] == "error":
            meta["error"] = run_result.get("error") or "flow run failed"
        elif run_result["status"] == "timeout":
            # Still running past the wait window — fall back to async semantics.
            meta["run_status"] = "running"
            meta["note"] = (
                "The flow is still running; results were not available within the "
                "wait window. The run continues in the background."
            )

        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "canvas",
                "flow": flow,
                "definition": flow.get("definition", {}),
                "run_id": run_id,
                "execution_mode": True,
            },
            title=_flow_words(flow, f"run {meta.get('run_status', 'started')}"
                                    + (f" · ${meta['cost_usd']:.4f}" if isinstance(meta.get("cost_usd"), (int, float)) and meta.get("cost_usd") else "")),
            metadata=meta,
        )

    async def _await_run_result(self, run_id: str, user_uuid: str) -> Dict[str, Any]:
        """Poll a flow run to a terminal status, returning a compact result dict.

        Returns {"status": "complete"|"error"|"timeout"|"cancelled", ...}. On
        "complete" includes `outputs` ([{node, output}] using node labels) and
        token/cost totals; on "error" includes `error`. Reads the flow definition
        once to map node ids → labels for a human-meaningful result.
        """
        from trusted_data_agent.core.flow_db import get_run, get_flow

        waited = 0.0
        run = None
        while waited < FLOW_EXECUTE_WAIT_TIMEOUT:
            await asyncio.sleep(FLOW_EXECUTE_POLL_INTERVAL)
            waited += FLOW_EXECUTE_POLL_INTERVAL
            r = get_run(run_id)
            if r and r.get("user_id") == user_uuid and \
                    r.get("status") in ("complete", "error", "cancelled"):
                run = r
                break

        if run is None:
            return {"status": "timeout"}

        status = run.get("status")
        if status != "complete":
            return {
                "status": status,
                "error": run.get("error_msg") or run.get("error"),
            }

        # Map node ids → labels so the LLM reports "Get Customer Count: …" rather
        # than an opaque node uuid. node_outputs values are full executor output
        # dicts; surface the meaningful `output` field when present.
        label_by_id: Dict[str, str] = {}
        try:
            flow = get_flow(run.get("flow_id"), user_uuid) or {}
            for n in (flow.get("definition") or {}).get("nodes", []):
                if isinstance(n, dict) and n.get("id"):
                    label_by_id[n["id"]] = n.get("label") or n.get("id")
        except Exception:
            pass

        node_outputs = ((run.get("output_result") or {}).get("node_outputs")) or {}
        outputs = []
        for node_id, out in node_outputs.items():
            label = label_by_id.get(node_id, node_id)
            value = out.get("output") if isinstance(out, dict) and "output" in out else out
            # Skip structural passthrough nodes whose output is just the state ctx.
            if isinstance(value, dict) and set(value.keys()) <= {"global_params", "node_outputs"}:
                continue
            outputs.append({"node": label, "output": value})

        return {
            "status": "complete",
            "outputs": outputs,
            "tokens_input": run.get("tokens_input") or 0,
            "tokens_output": run.get("tokens_output") or 0,
            "cost_usd": run.get("cost_usd") or 0.0,
        }

    async def _handle_get_run(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import get_run, get_node_runs

        run_id = args.get("run_id")
        if not run_id:
            return self._error_payload("run_id is required for 'get_run'")

        run = get_run(run_id)
        if not run or run.get("user_id") != user_uuid:
            return self._error_payload(f"Run '{run_id}' not found")

        node_runs = get_node_runs(run_id)
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={"view": "run_status", "run": run, "node_runs": node_runs},
            title=(f"run {run_id[:8]} · {run.get('status') or 'unknown'} · "
                   f"{sum(1 for r in (node_runs or []) if (r or {}).get('status') in ('complete', 'success'))}/{len(node_runs or [])} nodes"),
            metadata={"action": "get_run", "status": run.get("status")},
        )

    async def _handle_cancel_run(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        from trusted_data_agent.core.flow_db import get_run, update_run_status

        run_id = args.get("run_id")
        if not run_id:
            return self._error_payload("run_id is required for 'cancel_run'")

        run = get_run(run_id)
        if not run or run.get("user_id") != user_uuid:
            return self._error_payload(f"Run '{run_id}' not found")

        update_run_status(run_id, "cancelled")
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={"cancelled": True, "run_id": run_id},
            title="Run cancelled",
            metadata={"action": "cancel_run"},
        )

    # ------------------------------------------------------------------
    # Canvas payload builder
    # ------------------------------------------------------------------

    def _flow_canvas_payload(
        self,
        flow: Dict[str, Any],
        action: str = "open",
        patch: Optional[Dict] = None,
        user_uuid: Optional[str] = None,
    ) -> ComponentRenderPayload:
        """Build the sub-window payload that renders the React Flow canvas."""
        flow_id = flow["flow_id"]
        definition = flow.get("definition", {})

        # PROP-007: a templated flow carries its stamp + open-slot checklist on the
        # record (the canvas counts them down); the LLM is told what is still open
        # and how to fill it, so a 'create' from a topology never reads as done.
        from trusted_data_agent.core.flow_topologies import decorate_flow_record
        if user_uuid:
            from trusted_data_agent.core.flow_executor import _resolve_profile_ref
            flow = decorate_flow_record(flow, lambda r: _resolve_profile_ref(r, user_uuid))
        else:
            flow = decorate_flow_record(flow)
        open_slots = flow.get("open_slots") or []
        suffix = "created" if action == "create" else action.replace("_", " ")
        if open_slots:
            n = len(open_slots)
            suffix += f" · {n} slot{'' if n == 1 else 's'} to fill"
        metadata = {
            "action": action,
            "flow_id": flow_id,
            "node_count": len(definition.get("nodes", [])),
            "edge_count": len(definition.get("edges", [])),
            "scope_count": len(definition.get("scopes", [])),
        }
        if flow.get("template_id"):
            metadata["template_id"] = flow["template_id"]
        if open_slots:
            metadata["open_slots"] = open_slots
            metadata["note"] = self._open_slots_note(open_slots)

        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "canvas",
                "flow": flow,
                "definition": definition,
                "action": action,
                "patch": patch,
            },
            title=_flow_words(flow, suffix),
            metadata=metadata,
        )

    def _error_payload(self, message: str) -> ComponentRenderPayload:
        """Return a silent payload — error text goes to the LLM tool result, not the chat UI."""
        return ComponentRenderPayload(
            component_id="flow",
            render_target=RenderTarget.INLINE,
            spec={"view": "silent_error", "error": message},
            title="",
            metadata={"error": True, "message": message},
        )

    # ------------------------------------------------------------------
    # Instance portability (export / import) — see BaseComponentHandler
    # ------------------------------------------------------------------
    #
    # A flow definition carries cross-references that are per-user IDs (profiles)
    # and environment-specific names (MCP tools / prompts). Export emits profile
    # references as TAGS (the same friendly tags the LLM authors with), so the
    # file is portable; import remaps tags → the importing user's real profile IDs
    # via a mapping the dialog gathers from the dry-run preflight. Tool/prompt
    # names are not remappable (you can't substitute base_readQuery) — they are
    # surfaced as informational refs, and the executor already degrades gracefully
    # (_resolve_profile_ref fallback + orphan-edge pruning + node warnings).

    @staticmethod
    def _iter_profile_slots(definition: Dict):
        """Yield (container, key) pairs where a profile reference is stored, so the
        same walk drives both export (id→tag) and import (tag→id). This is the only
        flow-specific part; the remap primitives live in instance_portability."""
        settings = definition.get("settings")
        if isinstance(settings, dict) and settings.get("defaultProfileId"):
            yield settings, "defaultProfileId"
        for node in definition.get("nodes", []):
            if not isinstance(node, dict):
                continue
            if node.get("profile_id"):
                yield node, "profile_id"
            data = node.get("data")
            if isinstance(data, dict) and data.get("profile_id"):
                yield data, "profile_id"

    async def list_instances(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        from trusted_data_agent.core.flow_db import list_flows
        user_uuid = context.get("user_uuid", "")
        return [
            {"id": f.get("flow_id"), "name": f.get("name"), "updated_at": f.get("updated_at")}
            for f in (list_flows(user_uuid, include_templates=False) or [])
        ]

    async def export_instance(self, instance_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        from trusted_data_agent.core.flow_db import get_flow
        from trusted_data_agent.components import instance_portability as ip

        user_uuid = context.get("user_uuid", "")
        flow = get_flow(instance_id, user_uuid)
        if not flow:
            raise ValueError(f"Flow '{instance_id}' not found")

        definition = deepcopy(flow.get("definition") or {})
        inv = ip.build_profile_inventory(user_uuid)

        # Profile IDs → portable tags (shared resolver records each ref for the dialog).
        ref_profiles: Dict[str, Dict] = {}
        for container, key in self._iter_profile_slots(definition):
            tag = ip.export_profile_ref(container.get(key), inv, ref_profiles)
            if tag is not None:
                container[key] = tag

        # Tool / prompt references — informational (not remappable).
        tool_refs, prompt_refs = [], []
        for node in definition.get("nodes", []):
            data = node.get("data") if isinstance(node, dict) else None
            if not isinstance(data, dict):
                continue
            if node.get("type") == "task_tool" and data.get("tool_name"):
                tool_refs.append(data["tool_name"])
            if node.get("type") == "task_prompt" and data.get("prompt_name"):
                prompt_refs.append(data["prompt_name"])

        envelope = {
            "uderia_artifact": "component_instance",
            "component_id": "flow",
            "schema_version": ip.PORTABILITY_SCHEMA_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "name": flow.get("name") or "Flow",
            "description": flow.get("description") or "",
            "spec": {"definition": definition},
            "refs": {
                "profiles": list(ref_profiles.values()),
                "tools": sorted(set(tool_refs)),
                "prompts": sorted(set(prompt_refs)),
            },
            "summary": {
                "nodes": len(definition.get("nodes", [])),
                "edges": len(definition.get("edges", [])),
                "scopes": len(definition.get("scopes", [])),
            },
        }
        return ip.stamp_content_hash(envelope)

    async def import_instance(
        self,
        payload: Dict[str, Any],
        context: Dict[str, Any],
        *,
        mode: str = "create",
        target_id: Optional[str] = None,
        mapping: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        from trusted_data_agent.core.flow_db import create_flow, update_flow, list_flows, get_flow
        from trusted_data_agent.components import instance_portability as ip

        user_uuid = context.get("user_uuid", "")
        if (payload or {}).get("component_id") != "flow":
            raise ValueError("This file is not a flow export")
        definition = deepcopy((payload.get("spec") or {}).get("definition") or {})
        if not isinstance(definition, dict) or "nodes" not in definition:
            raise ValueError("Flow export is missing its definition")

        inv = ip.build_profile_inventory(user_uuid)
        ref_profiles = (payload.get("refs") or {}).get("profiles") or []

        # ---- Preflight: report what auto-maps and what needs a human decision ----
        if dry_run:
            s = payload.get("summary") or {}
            return {
                "status": "preflight",
                "component_id": "flow",
                "name": payload.get("name") or "Imported Flow",
                "description": payload.get("description") or "",
                "summary": s,
                "summary_text": f"{s.get('nodes', 0)} nodes · {s.get('edges', 0)} edges",
                # 'update' is the marketplace subscribe-sync path (in-place); the file
                # import dialog only offers create.
                "modes": ["create", "update"],
                "profiles": ip.preflight_profiles(ref_profiles, inv),
                "candidates": inv["candidates"],
                "tools": (payload.get("refs") or {}).get("tools") or [],
                "prompts": (payload.get("refs") or {}).get("prompts") or [],
            }

        # ---- Commit: remap profile tags → real IDs (shared resolver) ----
        warnings: List[str] = []
        for container, key in self._iter_profile_slots(definition):
            val = container.get(key)
            resolved = ip.resolve_profile_ref(val, mapping, inv)
            if resolved:
                container[key] = resolved
            elif key == "defaultProfileId" and inv["default_id"]:
                container[key] = inv["default_id"]
            else:
                container.pop(key, None)
                warnings.append(f"Unmapped profile '{val}' — node will inherit the flow default")

        settings = definition.setdefault("settings", {})
        if not settings.get("defaultProfileId") and inv["default_id"]:
            settings["defaultProfileId"] = inv["default_id"]
        if (payload.get("refs") or {}).get("tools"):
            warnings.append("Referenced tools must exist on the mapped profile(s) for tool nodes to run.")

        # In-place re-materialization (marketplace subscribe-sync): preserve the
        # instance id so the subscriber's references stay valid.
        if mode == "update" and target_id:
            existing = get_flow(target_id, user_uuid)
            if not existing:
                raise ValueError(f"Flow '{target_id}' not found for update")
            name = payload.get("name") or existing.get("name")
            update_flow(target_id, user_uuid, name=name, description=payload.get("description") or "", definition=definition)
            return {"status": "success", "mode": "update", "flow_id": target_id, "instance_id": target_id, "name": name, "warnings": warnings}

        # Create a fresh flow (file import / install / fork).
        name = ip.dedupe_name(payload.get("name") or "Imported Flow",
                              {f.get("name") for f in (list_flows(user_uuid) or [])})
        created = create_flow(user_uuid, name, payload.get("description") or "", definition)
        return {"status": "success", "mode": "create", "flow_id": created["flow_id"], "instance_id": created["flow_id"], "name": name, "warnings": warnings}
