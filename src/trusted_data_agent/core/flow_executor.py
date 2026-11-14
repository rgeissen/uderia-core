"""
Flow Executor — Graph execution engine for the Flow component.

Architecture:
  - Uses NetworkX DiGraph for graph algorithms (cycle detection, topological sort)
  - Supports directed graphs WITH cycles via named, bounded scope containers
  - Emits SSE events per node/edge for live canvas animation
  - LLM task nodes execute via REST API (POST /api/v1/sessions/<id>/query) so that
    chat log updates, cost tracking, and notifications all work automatically
  - State propagates via global_params (flow-level) + predecessor outputs (edge-level)

Graph topology:
  - Any node may belong to a scope (cycle segment)
  - A scope defines: which nodes form the cycle, the back-edge, and max_iterations
  - Scopes can be nested (future) — Phase 1 handles top-level scopes
  - Non-scoped nodes execute in topological order
  - Scoped nodes execute as a bounded iteration block
"""

import html as _html
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("quart.app")

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    logger.warning("NetworkX not installed — flow executor will be limited")

from trusted_data_agent.core.flow_db import (
    append_node_run,
    create_run,
    get_flow,
    list_runs,
    update_run_status,
)
from trusted_data_agent.core.safe_expression import UnsafeExpressionError, safe_eval
from trusted_data_agent.core.flow_refs import rewrite_refs
from trusted_data_agent.core import flow_rounds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_ITERATIONS = 10   # Fallback if scope doesn't specify
LLM_NODE_TIMEOUT = 300        # Seconds before an LLM task node times out
LLM_NODE_POLL_INTERVAL = 2    # Seconds between task-status polls
DEFAULT_LLM_NODE_RETRIES = 1  # Retries (beyond the first attempt) on transient LLM node failure
LLM_NODE_RETRY_BACKOFF = 1.5  # Base seconds for exponential retry backoff
MAX_PARALLEL_NODES = 8        # Max nodes executed concurrently within one dependency level


class FlowNeedsInput(Exception):
    """PROP-011 phase 3 — a node's agent stopped to ask the person. Not a RuntimeError (the node
    retry wrapper must not retry it): the run pauses `input_required` at this node and resumes
    there with the answer."""

    def __init__(self, node_id, question: dict, session_id: str, task_id: str, text: str):
        self.node_id, self.question, self.session_id, self.task_id, self.text = node_id, question, session_id, task_id, text
        super().__init__(f"node={node_id} needs input: {text}")


class FlowCancelled(Exception):
    """Raised inside a node executor when the flow was cancelled mid-flight.

    Distinct from a node *error*: a node that aborts because the user cancelled
    the run must NOT be recorded as a failure (which would mark the whole run
    `error`). `_run_node` catches this separately, records the node as
    `cancelled`, and lets the run loop settle to the `cancelled` terminal state.
    """


# ---------------------------------------------------------------------------
# Live-run registry (cancellation support)
# ---------------------------------------------------------------------------

# Registry of in-flight executors, keyed by run_id. Populated in `execute_flow`
# before the run task is launched and cleared when that task settles. This is the
# bridge that lets a cancel request reach the live instance and set its
# cooperative `_cancelled` flag — without it, cancellation only flips the DB
# status while the background task keeps executing every remaining node.
_ACTIVE_EXECUTORS: Dict[str, "FlowExecutor"] = {}


def get_active_executor(run_id: str) -> Optional["FlowExecutor"]:
    """Return the live executor for a run, or None if it isn't currently running."""
    return _ACTIVE_EXECUTORS.get(run_id)


# ---------------------------------------------------------------------------
# Per-run pub/sub for live SSE animation
# ---------------------------------------------------------------------------
#
# This registry lives in the executor (not the API layer) because it belongs to
# the run lifecycle the executor owns — mirroring `_ACTIVE_EXECUTORS`. The
# canonical run emitter (built in `execute_flow`) publishes here for EVERY run
# regardless of initiator (chat LLM tool call, REST /execute, scheduler, …), so
# the per-run `/stream` SSE endpoint sees the same events no matter who started
# the run. `flow_routes.stream_run_endpoint` imports these names by reference.
#
# Simple in-memory pub/sub (per-process; fine for single-worker Quart).
_run_subscribers: dict = {}    # run_id → list of asyncio.Queue (items: (seq, message))
_run_event_buffer: dict = {}   # run_id → list[(seq, message)] for catch-up replay
_run_event_seq: dict = {}      # run_id → monotonic event counter
_RUN_BUFFER_MAX = 1000


async def _expire_run_buffer(run_id: str, delay: float = 60.0) -> None:
    """Drop a finished run's event buffer after a grace period so a client that
    connects just after the run ends can still replay the full sequence."""
    await asyncio.sleep(delay)
    _run_event_buffer.pop(run_id, None)
    _run_event_seq.pop(run_id, None)


def _broadcast_flow_event(run_id: str, event_type: str, payload: dict) -> None:
    """Push an event to all SSE subscribers for this run AND buffer it for replay.

    The browser opens the per-run SSE stream only *after* it learns the run_id, so
    the first events (flow_run_start, flow_node_start — the running spinner) are
    emitted before any subscriber exists. Buffering + replay-on-connect ensures
    a late subscriber still sees them; without it a quick run shows only the
    final checkmark.
    """
    message = json.dumps({"type": event_type, "payload": payload})
    seq = _run_event_seq.get(run_id, 0) + 1
    _run_event_seq[run_id] = seq

    buf = _run_event_buffer.setdefault(run_id, [])
    buf.append((seq, message))
    if len(buf) > _RUN_BUFFER_MAX:
        del buf[:len(buf) - _RUN_BUFFER_MAX]

    for q in _run_subscribers.get(run_id, []):
        try:
            q.put_nowait((seq, message))
        except asyncio.QueueFull:
            pass

    # Schedule buffer cleanup once the run reaches a terminal state.
    if event_type in ("flow_run_complete", "flow_run_error"):
        try:
            asyncio.create_task(_expire_run_buffer(run_id))
        except RuntimeError:
            _run_event_buffer.pop(run_id, None)
            _run_event_seq.pop(run_id, None)


def cancel_run(run_id: str) -> bool:
    """Signal a live run to stop cooperatively.

    Sets the executor's `_cancelled` flag so the run loop halts before launching
    the next dependency level / scope iteration. Returns True if a live executor
    was found (False means the run already finished or never registered, in which
    case only the DB status needs updating).
    """
    executor = _ACTIVE_EXECUTORS.get(run_id)
    if executor is not None:
        executor.cancel()
        return True
    return False


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------

# Matches a Flowise-style "$name" parameter reference (e.g. $customer_count, $flow).
# Python's eval cannot use "$" in identifiers, so we strip the sigil before eval,
# leaving the bare name that already exists in the node's state context.
_DOLLAR_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _normalize_param_refs(expr: str) -> str:
    """Convert Flowise-style `$name` references to bare `name` for Python eval.

    Lets users reference flow parameters / upstream outputs with the familiar
    `$param` syntax in Fn expressions and decision conditions. A bare `name`
    (no sigil) keeps working unchanged.
    """
    if not expr or "$" not in expr:
        return expr
    return _DOLLAR_REF.sub(r"\1", expr)


def _substitute_dollar_values(template: str, ctx: Dict[str, Any]) -> str:
    """Replace `$name` references in a text template with their values from ctx.

    Used for LLM prompt templates so users can interpolate flow parameters with
    the familiar Flowise `$param` syntax. Unknown names are left untouched.
    """
    if not template or "$" not in template:
        return template

    def _repl(m):
        name = m.group(1)
        return str(ctx[name]) if name in ctx else m.group(0)

    return _DOLLAR_REF.sub(_repl, template)


# Strips internal diagnostic prefixes (e.g. "[FlowNode] node=node-abc execution
# error: ") from an error string so the surfaced message reads cleanly. The full
# raw error is still logged at the raise/catch sites for diagnosability.
_FLOWNODE_PREFIX_RE = re.compile(
    r"^\[FlowNode\]\s*(?:node=\S+\s*)?(?:execution error:\s*)?",
    re.IGNORECASE,
)


def _clean_error_message(msg: Optional[str]) -> str:
    """Return a user-facing error string with internal `[FlowNode]` noise removed."""
    if not msg:
        return "Unknown error"
    cleaned = _FLOWNODE_PREFIX_RE.sub("", str(msg)).strip()
    return cleaned or str(msg)


# Matches a string whose ENTIRE value is a single `$name` reference. Such a value
# is replaced with the raw context value (any type — list/dict/number preserved),
# unlike an embedded `$name` inside a larger string, which is text-substituted.
_WHOLE_DOLLAR_REF = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


def _resolve_arg_value(value: Any, ctx: Dict[str, Any]) -> Any:
    """Resolve `$name` / `{name}` references inside a tool argument value.

    - A value that is exactly `$name` resolves to the raw context value (type
      preserved), so a tool arg can be a number, list, or the full output dict.
    - Any other string has embedded `$name` substituted then `{name}` formatted
      (best-effort; unresolved tokens are left intact).
    - dicts/lists are resolved recursively; other types pass through unchanged.
    """
    if isinstance(value, str):
        whole = _WHOLE_DOLLAR_REF.match(value)
        if whole and whole.group(1) in ctx:
            return ctx[whole.group(1)]
        resolved = _substitute_dollar_values(value, ctx)
        try:
            resolved = resolved.format(**ctx)
        except (KeyError, IndexError, ValueError):
            pass
        return resolved
    if isinstance(value, dict):
        return {k: _resolve_arg_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_arg_value(v, ctx) for v in value]
    return value


def _substitute_brace_refs(text: str, ctx: Dict[str, Any]) -> str:
    """Rewrite `{ref}` / `$name` reference forms to their resolved context VALUE
    (numeric-coerced literal) before safe_eval, via the shared resolver
    (`flow_refs.rewrite_refs`). Rescues the brace / node-label forms a model
    naturally writes — `{Get Customer Count.customer_count} <= 100` → `100 <= 100`.
    Unresolved references are left untouched, so legitimate set/dict literals and
    unknown names are preserved. The SAME core powers authoring-time canonicalisation
    (flow_validation) — one mechanism, no drift."""
    return rewrite_refs(
        text,
        lambda name: repr(_coerce_scalar(ctx[name])) if name in ctx else None,
    )


def _load_global_variables(user_id: str) -> Dict[str, Any]:
    """Load a user's cross-flow global variables (Flowise-style $vars).

    Seeded as the lowest-precedence layer of every node's context. Fails open
    (returns {}) so a variable-store hiccup never breaks flow execution.
    """
    try:
        from trusted_data_agent.core.flow_db import get_flow_variables_dict
        return get_flow_variables_dict(user_id) or {}
    except Exception as exc:
        logger.warning(f"[FlowExecutor] could not load global variables: {exc}")
        return {}


def _resolve_node_session(
    node_data: Dict[str, Any],
    node_session_mode: str,
    caller_session_id: Optional[str],
) -> Optional[str]:
    """Decide which session a task_llm node runs in.

    Returns a session id to pin to, or ``None`` to create a fresh session.

    Priority (highest first):
      0. per-node ``session_context="new"``  → fresh session (beats every mode)
      1. compact mode + caller session        → pin to the caller's session
      2. ``individual`` mode                   → fresh session per node
      3. per-node ``"pinned"`` + ``session_id``→ that specific session
      4. caller session (fallback)             → pin to the caller's session
      5. otherwise                             → fresh session

    The priority-0 ``"new"`` branch is what lets a single node opt out of an
    in-session run even when the whole flow is pinned to the caller (compact).
    """
    # A participant inside a loop keeps ONE session across its rounds (stamped by the
    # executor after its first round) — its own earlier turns are real memory, the
    # other participants' turns reach it only through round history.
    scope_sid = node_data.get("_scope_session_id")
    if isinstance(scope_sid, str) and scope_sid:
        return scope_sid
    session_context = node_data.get("session_context")
    if session_context == "new":
        return None
    if node_session_mode == "compact" and caller_session_id:
        return caller_session_id
    if node_session_mode == "individual":
        return None
    if session_context == "pinned" and node_data.get("session_id"):
        return node_data.get("session_id")
    if caller_session_id:
        return caller_session_id
    return None


def _resolve_profile_ref(ref: Optional[str], user_id: str) -> Optional[str]:
    """Resolve a node's `profile_id` to a real profile ID.

    The platform trains the LLM to use friendly tags (`@OPTIM`/`OPTIM`), so it
    naturally writes a tag into a task_llm node's `profile_id` — but the REST
    query body needs a real ID (`profile-…`). Resolve tag → ID so the node runs
    under the intended profile (instead of silently falling back to the session
    default). Real IDs pass through unchanged; the node keeps showing the
    friendly tag while executing correctly. Fails open (returns the input) so a
    lookup miss degrades to the cascade rather than breaking the run.
    """
    if not ref or not isinstance(ref, str):
        return None
    r = ref.strip()
    if not r:
        return None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profiles = get_config_manager().get_profiles(user_uuid=user_id) or []
    except Exception as exc:
        # Config unavailable (transient) — fail open so we never break a run.
        logger.warning(f"[FlowNode] profile resolution unavailable for {ref!r}: {exc}")
        return r
    if any(p.get("id") == r for p in profiles):
        return r                                   # already a valid real ID
    tag = r.lstrip("@").strip().upper()
    for p in profiles:
        if str(p.get("tag", "")).strip().upper() == tag:
            return p.get("id")                     # tag → real ID
    # Neither a valid ID nor a known tag. The LLM sometimes hallucinates a
    # profile-shaped ID (e.g. ...444005 — one digit off the real ...444002), or
    # an unknown tag. Drop it so the node falls back to the flow default rather
    # than carrying a broken reference that resolves to nothing.
    logger.warning(f"[FlowNode] unresolvable profile reference {ref!r} dropped → inherit default")
    return None


def _apply_output_alias(node: Dict[str, Any], output: Any) -> Any:
    """Also publish a node's primary `output` under its user-chosen `output_as` key.

    Lets fan-in / downstream nodes reference a result by a stable name instead of
    colliding on the generic `output` key. No-op when output_as is unset/blank or
    the node produced no `output`.
    """
    output_as = (node.get("data") or {}).get("output_as")
    if output_as and isinstance(output, dict) and "output" in output:
        alias = str(output_as).strip()
        if alias:
            return {**output, alias: output["output"]}
    return output


def _coerce_scalar(v: Any) -> Any:
    """Coerce a numeric-looking string to int/float so a column referenced by name
    compares numerically (`$customer_count <= 100`). SQL/MCP results routinely
    return numbers as strings; the raw value stays available via output[0]['col']."""
    if isinstance(v, str):
        s = v.strip()
        if s and s.lstrip("-").isdigit():
            try:
                return int(s)
            except ValueError:
                pass
        # decimals like "1234.50"
        if s.lstrip("-").replace(".", "", 1).isdigit():
            try:
                return float(s)
            except ValueError:
                pass
    return v


class FlowState:
    """
    Mutable execution state threaded through node executions.

    Two layers:
      global_params  — flow-level dict, readable by all nodes
      node_outputs   — per-node outputs, keyed by node_id
    """

    def __init__(self, global_params: Dict[str, Any]):
        self.global_params: Dict[str, Any] = dict(global_params)
        self.node_outputs: Dict[str, Any] = {}
        # Round history per loop (PROP-007 increment 3): scope_id → [round → [{alias,label,text}]]
        self.scope_rounds: Dict[str, List[List[Dict[str, str]]]] = {}
        # How each loop ended: scope_id → {rounds_completed, converged, exit_reason, ...}
        self.scope_meta: Dict[str, Dict[str, Any]] = {}
        self.tokens_input: int = 0
        self.tokens_output: int = 0
        self.cost_usd: float = 0.0

    def resolve_for_node(self, node_id: str, predecessors: List[str]) -> Dict[str, Any]:
        """Merge global params + all predecessor outputs into a single context dict."""
        ctx: Dict[str, Any] = dict(self.global_params)
        for pred_id in predecessors:
            pred_out = self.node_outputs.get(pred_id, {})
            if isinstance(pred_out, dict):
                ctx.update(pred_out)
        # Column shortcuts (intelligent-component resolution): expose the first row's
        # columns of any predecessor `output` that is a list of row dicts (a
        # task_tool / SQL result) as top-level, numeric-coerced names. This lets a
        # node reference a value the way the model naturally writes it —
        # `$customer_count <= 100` — without knowing `output` is a row list or
        # hand-writing `int(output[0]['customer_count'])`. setdefault keeps explicit
        # params / output keys authoritative; the raw row stays at output[0][...].
        for pred_id in predecessors:
            pred_out = self.node_outputs.get(pred_id)
            rows = pred_out.get("output") if isinstance(pred_out, dict) else None
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                for col, val in rows[0].items():
                    if isinstance(col, str):
                        ctx.setdefault(col, _coerce_scalar(val))
        return ctx

    def record_output(self, node_id: str, output: Any) -> None:
        self.node_outputs[node_id] = output

    def snapshot(self) -> Dict[str, Any]:
        snap = {
            "global_params": self.global_params,
            "node_outputs": {k: v for k, v in self.node_outputs.items()},
        }
        if self.scope_rounds or self.scope_meta:
            snap["scopes"] = {sid: {**self.scope_meta.get(sid, {}), "rounds": self.scope_rounds.get(sid, [])}
                              for sid in set(self.scope_rounds) | set(self.scope_meta)}
        return snap


# ---------------------------------------------------------------------------
# Flow definition parser
# ---------------------------------------------------------------------------

class FlowDefinition:
    """
    Parses and validates a raw flow definition dict.

    Expected structure:
    {
      "nodes": [{"id", "type", "label", "data": {...}, "scope_id": optional}],
      "edges": [{"id", "source", "target", "type": "normal|error|conditional|cycle",
                 "condition": optional_expression}],
      "scopes": [{"id", "name", "node_ids": [...], "back_edge_id", "max_iterations": int}],
      "global_params": {"key": "value", ...}
    }
    """

    def __init__(self, definition: Dict[str, Any]):
        self.nodes: List[Dict] = definition.get("nodes", [])
        self.edges: List[Dict] = definition.get("edges", [])
        self.scopes: List[Dict] = definition.get("scopes", [])
        self.global_params: Dict[str, Any] = definition.get("global_params", {})
        self.settings: Dict[str, Any] = definition.get("settings", {}) or {}

        self._node_map: Dict[str, Dict] = {n["id"]: n for n in self.nodes}
        self._scope_map: Dict[str, Dict] = {s["id"]: s for s in self.scopes}
        self._node_scope: Dict[str, str] = {}  # node_id → scope_id
        for scope in self.scopes:
            for nid in scope.get("node_ids", []):
                self._node_scope[nid] = scope["id"]

    def get_node(self, node_id: str) -> Optional[Dict]:
        return self._node_map.get(node_id)

    def get_scope(self, scope_id: str) -> Optional[Dict]:
        return self._scope_map.get(scope_id)

    def scope_of(self, node_id: str) -> Optional[str]:
        return self._node_scope.get(node_id)

    def scope_nodes(self, scope_id: str) -> List[str]:
        scope = self._scope_map.get(scope_id)
        return scope.get("node_ids", []) if scope else []

    def forward_edges(self) -> List[Dict]:
        """All edges except cycle back-edges."""
        back_edge_ids = {s.get("back_edge_id") for s in self.scopes if s.get("back_edge_id")}
        return [e for e in self.edges if e["id"] not in back_edge_ids]

    def build_graph(self) -> Optional[Any]:
        """Build a NetworkX DiGraph from forward edges only."""
        if not HAS_NETWORKX:
            return None
        G = nx.DiGraph()
        for node in self.nodes:
            G.add_node(node["id"])
        for edge in self.forward_edges():
            G.add_edge(edge["source"], edge["target"], **edge)
        return G

    def prune_orphan_edges(self) -> List[str]:
        """Drop edges whose source or target node no longer exists.

        Such edges can be left behind by an LLM that wired a connection to a node
        it never actually created. ReactFlow already ignores them on the canvas;
        pruning here lets the flow execute its valid subgraph instead of failing
        hard at validation. Returns the ids of the removed edges (for logging).
        """
        node_ids = {n["id"] for n in self.nodes}
        removed = [
            e.get("id")
            for e in self.edges
            if e.get("source") not in node_ids or e.get("target") not in node_ids
        ]
        if removed:
            self.edges = [
                e for e in self.edges
                if e.get("source") in node_ids and e.get("target") in node_ids
            ]
        return removed

    def validate(self) -> List[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        node_ids = {n["id"] for n in self.nodes}

        for edge in self.edges:
            if edge.get("source") not in node_ids:
                errors.append(f"Edge '{edge['id']}': source node '{edge.get('source')}' not found")
            if edge.get("target") not in node_ids:
                errors.append(f"Edge '{edge['id']}': target node '{edge.get('target')}' not found")

        for scope in self.scopes:
            for nid in scope.get("node_ids", []):
                if nid not in node_ids:
                    errors.append(f"Scope '{scope['id']}': node '{nid}' not found")
            if scope.get("back_edge_id"):
                edge_ids = {e["id"] for e in self.edges}
                if scope["back_edge_id"] not in edge_ids:
                    errors.append(f"Scope '{scope['id']}': back_edge_id not found")

        return errors


# ---------------------------------------------------------------------------
# Node executors
# ---------------------------------------------------------------------------

async def _cancel_spawned_task(base_url: str, task_id: str, user_id: str) -> None:
    """Best-effort: ask the REST task this node spawned to cancel itself.

    When a flow is cancelled mid-flight, the node's underlying session query is a
    separate background asyncio task on the server. Without this it would keep
    running (and accruing cost) until it finished on its own or hit the node
    timeout. POSTing to /v1/tasks/<id>/cancel actually `.cancel()`s that task.
    Fails open — a cancel-propagation hiccup must never mask the cancellation
    itself (the flow stops launching further work regardless).
    """
    try:
        import aiohttp
        from trusted_data_agent.auth.security import create_internal_token
        headers = {"Authorization": f"Bearer {create_internal_token(user_id)}"}
        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{base_url}/v1/tasks/{task_id}/cancel",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                logger.info(
                    f"[FlowNode] propagated cancel to task={task_id} → HTTP {resp.status}"
                )
    except Exception as exc:
        logger.warning(f"[FlowNode] could not propagate cancel to task={task_id}: {exc}")


async def _execute_llm_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    run_id: str,
    event_emitter,
    caller_session_id: Optional[str] = None,
    node_session_mode: str = "compact",
    caller_profile_id: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Execute a task_llm node with bounded retries on transient failure.

    The submit+poll cycle lives in `_execute_llm_node_once`; this wrapper retries
    it on `RuntimeError` (transient provider/network hiccups, task errors, poll
    timeouts) so a single failure no longer aborts the whole run. Retry count is
    per-node (`node.data.retries`, default `DEFAULT_LLM_NODE_RETRIES`) with
    exponential backoff; `retries=0` keeps the original fail-fast behaviour. Each
    retry emits a `flow_node_retry` SSE event for canvas visibility.
    """
    data = node.get("data", {})
    try:
        retries = max(0, int(data.get("retries", DEFAULT_LLM_NODE_RETRIES)))
    except (TypeError, ValueError):
        retries = DEFAULT_LLM_NODE_RETRIES

    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        # Don't start (or retry) once the flow has been cancelled.
        if cancel_check is not None and cancel_check():
            raise FlowCancelled(f"node={node.get('id')} cancelled before attempt {attempt + 1}")
        try:
            return await _execute_llm_node_once(
                node, state_ctx, user_id, run_id, event_emitter,
                caller_session_id=caller_session_id,
                node_session_mode=node_session_mode,
                caller_profile_id=caller_profile_id,
                cancel_check=cancel_check,
            )
        except RuntimeError as exc:
            last_exc = exc
            if attempt >= retries:
                break
            backoff = LLM_NODE_RETRY_BACKOFF * (2 ** attempt)
            logger.warning(
                f"[FlowNode] node={node.get('id')} attempt {attempt + 1}/{retries + 1} "
                f"failed ({exc}); retrying in {backoff:.1f}s"
            )
            if event_emitter is not None:
                try:
                    await event_emitter("flow_node_retry", {
                        "run_id": run_id,
                        "node_id": node.get("id"),
                        "attempt": attempt + 1,
                        "max_attempts": retries + 1,
                        "error": str(exc),
                    })
                except Exception:
                    pass
            await asyncio.sleep(backoff)
    raise last_exc


async def _execute_llm_node_once(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    run_id: str,
    event_emitter,
    caller_session_id: Optional[str] = None,
    node_session_mode: str = "compact",
    caller_profile_id: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Execute a task_llm node via the REST API (POST /api/v1/sessions/<id>/query).

    Routes through the full execution pipeline so that:
      - Messages appear in the session chat log immediately via rest_task_complete notification
      - Cost and token tracking apply correctly
      - The execution is tagged as source='flow'

    Profile resolution cascade (highest → lowest priority):
      1. Node's own profile_id (explicit per-node override)
      2. caller_profile_id (profile of the session that triggered the flow)
      3. User's default profile (determined by execute_query endpoint)

    Node data fields:
      - prompt: str (may contain {variable} references resolved from state_ctx)
      - profile_id: str  (optional per-node profile override)
      - session_context: "new" (force a fresh session, overrides mode) |
                         "pinned" (use the explicit session_id below)
      - session_id: str (used when session_context == "pinned")
    """
    import aiohttp
    from trusted_data_agent.auth.security import create_internal_token
    from trusted_data_agent.core.config import APP_STATE

    data = node.get("data", {})
    prompt_template = data.get("prompt", "")

    # Resolve parameter placeholders from state context.
    # Supports both {variable} (str.format) and Flowise-style $variable references.
    prompt = _substitute_dollar_values(prompt_template, state_ctx)
    try:
        prompt = prompt.format(**state_ctx)
    except (KeyError, IndexError, ValueError):
        pass  # leave unresolved {tokens} (e.g. literal braces) untouched

    # task_prompt nodes carry an MCP prompt invocation instead of free text. The
    # /query endpoint requires a non-empty `prompt`, so synthesise one when only a
    # prompt_name is set; the executor runs the MCP prompt via active_prompt_name.
    mcp_prompt_name = (data.get("prompt_name") or "").strip()
    if mcp_prompt_name and not prompt.strip():
        prompt = f"Execute prompt: {mcp_prompt_name}"

    # Profile cascade: node-level override → caller session profile → user default.
    # Resolve a tag (e.g. "OPTIM"/"@OPTIM") to a real profile ID — the LLM writes
    # tags, but the REST query body needs an ID or it silently uses the default.
    node_profile_id = _resolve_profile_ref(data.get("profile_id"), user_id)
    # PROP-007 §4.2 — a REQUIRED slot whose profile is unset or unresolvable is OPEN,
    # and an open slot is refused HERE, before the cascade can fall back to the
    # caller's/default profile. The fallback is right for an ordinary node the author
    # left blank; for a slot it would silently run the role under an agent the
    # consumer never chose. Lazy import: flow_topologies imports this module.
    if node_profile_id is None:
        from trusted_data_agent.core.flow_topologies.slots import (
            FlowSlotUnfilledError, describe_slot, is_required_slot)
        if is_required_slot(node):
            raise FlowSlotUnfilledError([describe_slot(node)])
    effective_profile_id = node_profile_id or caller_profile_id  # None → execute_query uses user default

    pinned_session_id = _resolve_node_session(data, node_session_mode, caller_session_id)

    # Build internal auth header — short-lived JWT with internal=True marker
    internal_token = create_internal_token(user_id)
    auth_headers = {
        "Authorization": f"Bearer {internal_token}",
        "Content-Type": "application/json",
    }

    port = APP_STATE.get("server_port", 5050)
    base_url = f"http://127.0.0.1:{port}/api"

    # ---------------------------------------------------------------------------
    # Step 1: Resolve or create the session via REST
    # ---------------------------------------------------------------------------
    _resume = (node.get("data") or {}).get("_resume_answer")   # PROP-011: the answer re-enters the node's own session
    if _resume and _resume.get("session_id"):
        pinned_session_id = _resume["session_id"]
    if pinned_session_id:
        session_id = pinned_session_id
        logger.info(
            f"[FlowNode] node={node.get('id')} mode={node_session_mode} "
            f"reusing session={session_id} caller_session={caller_session_id}"
        )
    else:
        # Create a new session via POST /api/v1/sessions
        create_body: Dict[str, Any] = {}
        if effective_profile_id:
            create_body["profile_id"] = effective_profile_id

        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{base_url}/v1/sessions",
                json=create_body,
                headers=auth_headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201):
                    err_text = await resp.text()
                    raise RuntimeError(
                        f"[FlowNode] failed to create session: HTTP {resp.status} — {err_text}"
                    )
                resp_json = await resp.json()

        session_id = resp_json.get("session_id")
        if not session_id:
            raise RuntimeError(f"[FlowNode] session creation returned no session_id: {resp_json}")

        logger.info(
            f"[FlowNode] node={node.get('id')} created new session={session_id} "
            f"profile={effective_profile_id} caller_session={caller_session_id}"
        )

    # ---------------------------------------------------------------------------
    # Step 2: Submit the query via POST /api/v1/sessions/<id>/query
    # ---------------------------------------------------------------------------
    query_body: Dict[str, Any] = {
        "prompt": prompt,
        "source": "flow",
    }
    if _resume:
        # the re-entry: the structured block is the prompt, the person's line is what they see,
        # the binding rides under the engine's internal token (the public route keeps it only then)
        query_body["prompt"] = _resume.get("user_input") or prompt
        query_body["display_message"] = _resume.get("display_message")
        query_body["input_answer"] = _resume.get("input_answer")
    if effective_profile_id:
        query_body["profile_id"] = effective_profile_id
    if mcp_prompt_name:
        # Deterministic MCP prompt execution — same fields the resource-panel
        # prompt invoke uses. $name / {name} references in args are resolved.
        query_body["prompt_name"] = mcp_prompt_name
        query_body["prompt_arguments"] = _resolve_arg_value(data.get("prompt_arguments") or {}, state_ctx)
    # Inline extensions attached to this task_llm node (the slot rail) run through
    # the SAME post-processing path the conversation uses (the "!" trigger /
    # `extensions` query field) — no in-process re-runner. Slots are {id, param};
    # the query expects {name, param} (name == activation_name == extension_id).
    _ext_slots = data.get("extensions") or []
    if _ext_slots:
        query_body["extensions"] = [
            {"name": s.get("id"), "param": s.get("param")}
            for s in _ext_slots if isinstance(s, dict) and s.get("id")
        ]

    logger.info(
        f"[FlowNode] running node={node.get('id')} type={node.get('type')} "
        f"prompt={repr(prompt[:80])} session={session_id} "
        f"profile={node_profile_id or '(caller)'} effective={effective_profile_id}"
    )

    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{base_url}/v1/sessions/{session_id}/query",
            json=query_body,
            headers=auth_headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 202:
                err_text = await resp.text()
                raise RuntimeError(
                    f"[FlowNode] query submission failed: HTTP {resp.status} — {err_text}"
                )
            task_resp = await resp.json()

    task_id = task_resp.get("task_id")
    if not task_id:
        raise RuntimeError(f"[FlowNode] query response returned no task_id: {task_resp}")

    logger.info(f"[FlowNode] node={node.get('id')} polling task={task_id}")

    # ---------------------------------------------------------------------------
    # Step 3: Poll GET /api/v1/tasks/<task_id> until terminal status
    # ---------------------------------------------------------------------------
    from trusted_data_agent.core import turn_states as _turn_states   # settled = terminal or interrupted (PROP-011)
    poll_interval = LLM_NODE_POLL_INTERVAL
    deadline = asyncio.get_event_loop().time() + LLM_NODE_TIMEOUT

    task_data: Dict[str, Any] = {}
    while True:
        # Cooperative cancellation: if the flow was cancelled while this node's
        # query is in flight, stop polling immediately AND ask the spawned task to
        # cancel itself, instead of waiting up to LLM_NODE_TIMEOUT for it to finish.
        if cancel_check is not None and cancel_check():
            await _cancel_spawned_task(base_url, task_id, user_id)
            raise FlowCancelled(f"node={node.get('id')} cancelled (task={task_id})")

        if asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(
                f"LLM node '{node.get('id')}' timed out after {LLM_NODE_TIMEOUT}s "
                f"(task={task_id})"
            )

        # Refresh the internal token every poll in case it approaches expiry
        auth_headers["Authorization"] = f"Bearer {create_internal_token(user_id)}"

        async with aiohttp.ClientSession() as http:
            async with http.get(
                f"{base_url}/v1/tasks/{task_id}",
                headers=auth_headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    task_data = await resp.json()
                else:
                    logger.debug(
                        f"[FlowNode] task poll HTTP {resp.status} for task={task_id}, retrying"
                    )

        status = task_data.get("status", "")
        if _turn_states.is_settled(status):
            break

        await asyncio.sleep(poll_interval)

    # ---------------------------------------------------------------------------
    # Step 4: Extract result
    # ---------------------------------------------------------------------------
    result_payload = task_data.get("result") or {}
    final_text = result_payload.get("final_answer_text", "")
    final_text = _fold_canvas_contents(final_text, result_payload.get("canvas_contents"))

    if _turn_states.is_failure(task_data.get("status")):
        err_msg = result_payload.get("error", "Unknown error")
        raise RuntimeError(f"[FlowNode] node={node.get('id')} execution error: {err_msg}")
    if _turn_states.is_interrupted(task_data.get("status")) or (isinstance(result_payload, dict) and result_payload.get("input_request")):
        # PROP-011 phase 3: the node's agent stopped to ask. The run PAUSES on the question —
        # recorded `input_required` with its outputs so far — and resumes at this node when the
        # person answers (`resume_flow_for_input`); never carried on as text, never an error.
        _q = result_payload.get("input_request") or {}
        _qt = " ".join(str(i.get("question") or "") for i in (_q.get("items") or [])) or "a question for the user"
        raise FlowNeedsInput(node.get("id"), _q, session_id, task_id, _qt)

    logger.info(f"[FlowNode] node={node.get('id')} completed final_text={repr(final_text[:120])}")

    # Extract token counts and cost from task events.
    #   token_update carries: statement_* (this LLM call), turn_* (cumulative
    #   this turn), total_* (cumulative session), cost_usd (THIS call only).
    # Tokens: take the LAST turn_* (turn-scoped — covers every call in the turn
    #   incl. any LLM extension, and does NOT bleed in prior nodes that share a
    #   serialized session, which total_* would). Fall back to total_* defensively.
    # Cost: SUM cost_usd across all events. cost_usd is per-statement, so keeping
    #   only the last (the old behaviour) dropped every call but the final one —
    #   and when an LLM extension ran, its token_update is last, so the node
    #   reported ONLY the extension's cost and silently dropped the main LLM cost.
    tokens_in = 0
    tokens_out = 0
    cost = 0.0
    for event in task_data.get("events", []):
        if event.get("event_type") == "token_update":
            ed = event.get("event_data", {})
            tokens_in = ed.get("turn_input", ed.get("total_input", tokens_in))
            tokens_out = ed.get("turn_output", ed.get("total_output", tokens_out))
            cost += ed.get("cost_usd", 0) or 0

    # Surface extension results (already run by the query pipeline; their tokens
    # are included in the turn totals above) under their name, so a downstream
    # decision can route on them — e.g. condition: "boolean_check". Failed
    # extensions surface as None. LLM-backed extensions also carry their own
    # token/cost, captured into _extensions so the canvas can show them as a
    # distinct step instead of folding them invisibly into the node total.
    ext_out: Dict[str, Any] = {}
    ext_meta: List[Dict[str, Any]] = []
    for _name, _r in (result_payload.get("extension_results") or {}).items():
        if isinstance(_r, dict):
            ext_out[_name] = _r.get("content") if _r.get("success") else None
            _ti = _r.get("extension_input_tokens", 0) or 0
            _to = _r.get("extension_output_tokens", 0) or 0
            _ec = _r.get("extension_cost_usd", 0) or 0
            # Only LLM-backed extensions consume tokens/cost — list those so the
            # node badge attributes them. Cheap (non-LLM) extensions stay silent.
            if _ti or _to or _ec:
                ext_meta.append({
                    "name": _name,
                    "success": bool(_r.get("success")),
                    "tokens_input": _ti,
                    "tokens_output": _to,
                    "cost_usd": _ec,
                })

    return {
        "output": final_text,
        "session_id": session_id,
        "task_id": task_id,
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "cost_usd": cost,
        "_extensions": ext_meta,
        **ext_out,
    }


async def _execute_function_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    run_id: str,
    event_emitter,
) -> Dict[str, Any]:
    """
    Execute a task_function node.
    Phase 1: supports inline Python expressions via restricted eval.
    Phase 2: will support named function references.
    """
    data = node.get("data", {})
    expression = data.get("expression", "")

    if not expression:
        return {"output": None}

    # AST-allowlisted evaluation — see safe_expression.py. `{ref}` brace/label forms
    # are resolved to literals first, then `$name` refs normalised to bare names, so
    # all resolve against state_ctx.
    try:
        result = safe_eval(_normalize_param_refs(_substitute_brace_refs(expression, state_ctx)), state_ctx)
        return {"output": result}
    except UnsafeExpressionError as exc:
        raise RuntimeError(
            f"Function node '{node['id']}' rejected unsafe expression: {exc}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Function node '{node['id']}' expression error: {exc}") from exc


async def _execute_decision_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    run_id: Optional[str] = None,
    event_emitter=None,
    **_,
) -> Dict[str, Any]:
    """
    Execute a decision node. Evaluates a boolean condition.
    Returns {"output": True|False} which edges use to route.
    """
    data = node.get("data", {})
    condition = data.get("condition", "True")

    # AST-allowlisted evaluation — see safe_expression.py. Resolve `{ref}` brace /
    # node-label forms to literals first (rescues the references models naturally
    # write), then normalise `$name`.
    try:
        result = bool(safe_eval(_normalize_param_refs(_substitute_brace_refs(condition, state_ctx)), state_ctx))
        return {"output": result, "condition_result": result}
    except Exception as exc:
        # A malformed/rejected condition must not silently masquerade as a
        # legitimate False route — surface it as a warning event so the bad
        # condition is visible on the canvas, then default False to keep the
        # run alive.
        logger.warning(f"Decision node '{node['id']}' condition error: {exc} — defaulting False")
        if event_emitter is not None:
            try:
                # Match the executor's emit convention (all event names carry the
                # `flow_` prefix; the SSE broadcast path does not re-prefix).
                await event_emitter("flow_node_warning", {
                    "run_id": run_id,
                    "node_id": node.get("id"),
                    "node_type": "decision",
                    "message": f"Condition could not be evaluated ({exc}); routed to False.",
                })
            except Exception:
                pass
        return {"output": False, "condition_result": False}


async def _execute_extension_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    caller_profile_id: Optional[str] = None,
    caller_session_id: Optional[str] = None,
    **_,
) -> Dict[str, Any]:
    """
    Execute a task_extension node.

    Calls a registered post-processing extension (e.g. boolean-check, decision,
    classify, extract, json) against the upstream node's output, producing
    deterministic structured data that downstream decision nodes can evaluate.

    Node data fields:
      extension_id  — which extension to run (e.g. "boolean_check")
      param         — optional parameter passed to the extension (e.g. "strict")
      input_field   — state_ctx key to use as answer_text (default: "output")
    """
    from trusted_data_agent.extensions.manager import get_extension_manager
    from trusted_data_agent.extensions.base import LLMExtension
    from trusted_data_agent.extensions.models import ExtensionContext

    data = node.get("data", {})
    extension_id = data.get("extension_id", "")
    param = data.get("param") or None
    input_field = data.get("input_field") or "output"

    if not extension_id:
        raise RuntimeError(f"Extension node '{node['id']}' has no extension_id configured")

    # --- Resolve upstream input ---
    raw_input = state_ctx.get(input_field, state_ctx)
    if isinstance(raw_input, dict):
        answer_text = raw_input.get("output", str(raw_input))
    else:
        answer_text = str(raw_input) if raw_input is not None else ""

    # --- Resolve LLM config from profile (needed by LLMExtension) ---
    llm_config_id = None
    provider = None
    model = None
    if caller_profile_id:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            cm = get_config_manager()
            profile = cm.get_profile(caller_profile_id, user_uuid=user_id)
            if profile:
                llm_config_id = profile.get("llmConfigurationId")
                llm_cfg = cm.get_llm_configuration(llm_config_id) if llm_config_id else None
                if llm_cfg:
                    provider = llm_cfg.get("provider")
                    model = llm_cfg.get("model_name") or llm_cfg.get("model")
        except Exception as exc:
            logger.warning(f"Extension node '{node['id']}': could not resolve LLM config — {exc}")

    # --- Build minimal ExtensionContext ---
    ctx = ExtensionContext(
        answer_text=answer_text,
        answer_html=answer_text,
        original_query=data.get("label", extension_id),
        clean_query=data.get("label", extension_id),
        session_id=caller_session_id or "flow",
        turn_id=0,
        user_uuid=user_id,
        llm_config_id=llm_config_id,
        provider=provider,
        model=model,
    )

    # --- Get extension ---
    manager = get_extension_manager()
    ext = manager.get_extension(extension_id)
    if ext is None:
        raise RuntimeError(f"Extension '{extension_id}' not found — is it installed?")

    # --- Inject LLM config for LLMExtension subclasses ---
    if isinstance(ext, LLMExtension) and getattr(ext, "requires_llm", False):
        ext._user_uuid = user_id
        ext._llm_config_id = llm_config_id
        ext._provider = provider
        ext._model = model
        ext._total_input_tokens = 0
        ext._total_output_tokens = 0
        ext._total_cost_usd = 0.0
        if not llm_config_id:
            logger.warning(
                f"Extension node '{node['id']}': LLMExtension '{extension_id}' has no "
                "llm_config_id — ensure the triggering profile has an LLM configuration"
            )

    # --- Execute ---
    result = await ext.execute(ctx, param)

    # --- Build output: prefer parsed data dict, fall back to raw content ---
    if result.success and result.content is not None:
        if isinstance(result.content, dict):
            output = result.content
        else:
            output = result.content
    else:
        output = None

    return {
        "output": output,
        "extension_id": extension_id,
        "extension_success": result.success,
        "extension_error": result.error,
    }


async def _execute_tool_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    caller_session_id: Optional[str] = None,
    caller_profile_id: Optional[str] = None,
    **_,
) -> Dict[str, Any]:
    """Execute a task_tool node — a deterministic tool call with NO LLM round-trip.

    Invokes a named tool directly through the same dispatcher the agent uses
    (`mcp_adapter.invoke_mcp_tool`), which covers MCP data-source tools, builtin
    TDA_ tools (e.g. TDA_CurrentDate), and component tools (TDA_Charting, …). This
    is the flow's "do an action" primitive: fixed, free (no tokens for pure data
    tools), and repeatable — unlike a task_llm node that must be prompted to call
    the tool and may not.

    Node data fields:
      tool_name   — the tool to invoke (e.g. "base_readQuery", "TDA_CurrentDate")
      arguments   — dict of arguments; string values support `$name` / `{name}`
                    references resolved from the node's state context
      profile_id  — optional; selects the MCP server/connection (tag or real id).
                    Falls back to the flow default / caller profile.
      output_as   — optional alias for the result (handled by _apply_output_alias)
    """
    from trusted_data_agent.core.config import APP_STATE
    from trusted_data_agent.mcp_adapter import adapter as mcp_adapter

    data = node.get("data", {})
    tool_name = (data.get("tool_name") or "").strip()
    if not tool_name:
        raise RuntimeError(f"Tool node '{node['id']}' has no tool_name configured")

    resolved_args = _resolve_arg_value(data.get("arguments") or {}, state_ctx)
    if not isinstance(resolved_args, dict):
        raise RuntimeError(
            f"Tool node '{node['id']}' arguments must be an object, got {type(resolved_args).__name__}"
        )

    profile_id = _resolve_profile_ref(data.get("profile_id"), user_id) or caller_profile_id
    command = {"tool_name": tool_name, "arguments": resolved_args}

    logger.info(
        f"[FlowNode] tool node={node.get('id')} invoking tool={tool_name} "
        f"args_keys={list(resolved_args.keys())} profile={profile_id or '(default)'}"
    )

    result, tokens_in, tokens_out = await mcp_adapter.invoke_mcp_tool(
        APP_STATE, command,
        user_uuid=user_id,
        session_id=caller_session_id,
        profile_id=profile_id,
    )

    # Surface tool-reported failures as node errors so error edges / retries apply.
    if isinstance(result, dict) and result.get("status") == "error":
        err = result.get("error") or result.get("message") or "tool returned an error"
        raise RuntimeError(f"Tool '{tool_name}' failed: {err}")

    # Normalise the result envelope to a usable `output`. invoke_mcp_tool returns
    # {"status":"success","results":[...],"metadata":{...}} for data tools; expose
    # the rows as `output` (downstream nodes index them) while keeping the full
    # envelope under `_raw` for nodes that need metadata.
    output: Any = result
    if isinstance(result, dict) and "results" in result:
        rows = result.get("results")
        output = rows if rows else result

    return {
        "output": output,
        "tool_name": tool_name,
        "tokens_input": tokens_in or 0,
        "tokens_output": tokens_out or 0,
        "cost_usd": 0.0,
        "_raw": result,
    }


async def _execute_action_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    caller_session_id: Optional[str] = None,
    caller_profile_id: Optional[str] = None,
    run_id: Optional[str] = None,
    **_,
) -> Dict[str, Any]:
    """Execute a task_action node — a SIGNED action performed by the node's agent (PROP-008 §6).

    No LLM round-trip and no confirmation: the flow runs unattended, so the action's posture
    decides (enforce writes · review parks for the owner · observe records), the agent's grant
    is checked, and a performed act carries its own signed receipt. Everything goes through
    ``action_perform.perform_unattended`` — the same seam as the scheduler and the dashboard.

    Node data fields:
      action      — the action type's name (on the agent's active graph)
      invocation  — {"key": ..., "values": {...}}; string values resolve `$name` / `{name}`
                    references from upstream outputs
      profile_id  — the agent that acts (tag or id); falls back to the flow default
      connection_id — optional; else the agent's action connection
    A refused act is a node ERROR (so error edges and retries apply); parked and observed
    are successful outcomes the next node can branch on (``outcome``)."""
    import asyncio as _asyncio
    from trusted_data_agent.core import action_perform as AP
    from trusted_data_agent.core.config_manager import get_config_manager

    data = node.get("data", {})
    name = (data.get("action") or "").strip()
    if not name:
        raise RuntimeError(f"Action node '{node['id']}' has no action configured")
    invocation = _resolve_arg_value(data.get("invocation") or {}, state_ctx)
    if not isinstance(invocation, dict):
        raise RuntimeError(f"Action node '{node['id']}' invocation must be an object")
    profile_id = _resolve_profile_ref(data.get("profile_id"), user_id) or caller_profile_id
    profile = None
    if profile_id:
        try:
            profile = get_config_manager().get_profile(profile_id, user_id) or None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[FlowNode] action node profile lookup failed: {exc}")
    rec = await _asyncio.to_thread(
        AP.perform_unattended, user_id, profile, name, invocation, face="flow",
        connection_id=(data.get("connection_id") or None),
        session_id=caller_session_id, turn_id=run_id)
    logger.info(f"[FlowNode] action node={node.get('id')} action={name} outcome={rec.get('outcome')}")
    if rec.get("outcome") == "refused":
        raise RuntimeError(f"Action '{name}' refused ({rec.get('refused_check')}): {rec.get('refusal')}")
    keep = ("action", "version", "outcome", "rows_affected", "invocation_hash", "review_id",
            "posture", "principal", "face", "signed_receipt", "note")
    out = {k: rec.get(k) for k in keep if rec.get(k) is not None}
    out["description"] = AP.describe(rec)
    return {"output": out, "action": name, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}


def _render_template(template: str, state_ctx: Dict[str, Any]) -> str:
    """The prompt substitution every LLM-backed node uses: `$name`, then `{name}`."""
    text = _substitute_dollar_values(template or "", state_ctx)
    try:
        text = text.format(**state_ctx)
    except (KeyError, IndexError, ValueError):
        pass
    return text


async def _execute_judge_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    run_id: str,
    event_emitter,
    caller_session_id: Optional[str] = None,
    node_session_mode: str = "compact",
    caller_profile_id: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """A judge node (PROP-007 §6.1) — returns a VERDICT: {passed, score, verdict,
    feedback, output, judge}. Two fills, one output shape:

      • `judge_panel` → the eval judge panel (`build_panel` + `consensus`), the SAME
        panel and rubric discipline the quality layer uses; billed to the run ledger
        and the user's consumption.
      • an agent → the ordinary LLM node path (slot refusal, profile, session, cost all
        unchanged) with the verdict instructions appended; the reply is parsed
        deterministically. A teacher or a navigator is an agent judge: its feedback is
        the teaching / the review, its verdict decides when the loop stops.

    An abstention (no verdict line, or a panel with no models) is `passed: None` —
    never a pass, so a loop keeps going rather than stopping on an unreadable verdict."""
    from trusted_data_agent.core.flow_topologies.slots import JUDGE_PANEL
    data = node.get("data", {}) or {}
    rubric = (data.get("rubric") or "Is the work complete, correct and responsive to the brief?").strip()
    material = _render_template(data.get("prompt") or "{output}", state_ctx)

    if (data.get("profile_id") or "").strip() == JUDGE_PANEL:
        from trusted_data_agent.eval.judge import (build_panel, consensus, parse_judge_response, JUDGE_SYSTEM)
        panel = await build_panel(user_uuid=user_id)
        if len(panel) == 0:
            if event_emitter is not None:
                try:
                    await event_emitter("flow_node_warning", {
                        "run_id": run_id, "node_id": node.get("id"), "node_type": "judge",
                        "message": "No judge models are available (no LLM configuration with credentials "
                                   "is on the judge panel) — the verdict is unknown, so the loop does not stop on it.",
                    })
                except Exception:
                    pass
            v = flow_rounds.panel_verdict(None, None, [], "no judge models available")
            return {**v, "output": v["feedback"], "judge": "panel", "votes": [],
                    "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}
        user_msg = f"DIMENSION: {rubric}\n\nMATERIAL:\n{material}"
        raw = await panel.judge(JUDGE_SYSTEM, user_msg)
        parsed = [parse_judge_response(r) for r in raw]
        passed, score, evidence, expl = consensus(parsed)
        v = flow_rounds.panel_verdict(passed, score, evidence, expl)
        in_tok = sum(int(u[2] or 0) for u in getattr(panel, "usage", []))
        out_tok = sum(int(u[3] or 0) for u in getattr(panel, "usage", []))
        cost = 0.0
        try:
            from trusted_data_agent.core.cost_manager import get_cost_manager
            cm = get_cost_manager()
            for prov, mdl, i, o in getattr(panel, "usage", []):
                cost += cm.calculate_cost(prov, mdl, int(i or 0), int(o or 0))
        except Exception as exc:
            logger.debug(f"[FlowJudge] cost calculation skipped: {exc}")
        if in_tok or out_tok:
            # The same consumption sink the quality layer's judges bill through, so the
            # user's totals include the panel's tokens (a flow has no turn row of its own).
            try:
                from trusted_data_agent.auth.database import get_db_session
                from trusted_data_agent.auth.consumption_manager import ConsumptionManager
                with get_db_session() as db:
                    ConsumptionManager(db).update_turn_tokens(
                        user_id=user_id, session_id=caller_session_id or f"flow-run:{run_id}", turn_number=0,
                        additional_input_tokens=in_tok, additional_output_tokens=out_tok,
                        additional_cost_usd_cents=int((cost or 0) * 1000000))
            except Exception as exc:
                logger.debug(f"[FlowJudge] consumption skipped: {exc}")
        votes = [{"model": m, "verdict": p["verdict"], "score": p["score"]}
                 for m, p in zip(getattr(panel, "models", []), parsed)]
        return {**v, "output": v["feedback"], "judge": "panel", "votes": votes,
                "tokens_input": in_tok, "tokens_output": out_tok, "cost_usd": cost}

    # An agent judge: the ordinary LLM node path with the verdict format appended.
    prompt = (f"{data.get('instructions') or 'You are reviewing work produced in a multi-agent flow.'}\n\n"
              f"Criteria: {rubric}\n\n{material}\n\n{flow_rounds.VERDICT_INSTRUCTIONS}")
    agent_node = {**node, "data": {**data, "prompt": prompt}}
    out = await _execute_llm_node(agent_node, {}, user_id, run_id, event_emitter,
                                  caller_session_id=caller_session_id, node_session_mode=node_session_mode,
                                  caller_profile_id=caller_profile_id, cancel_check=cancel_check)
    v = flow_rounds.parse_agent_verdict(str(out.get("output") or ""))
    if v["verdict"] == "unknown" and event_emitter is not None:
        try:
            await event_emitter("flow_node_warning", {
                "run_id": run_id, "node_id": node.get("id"), "node_type": "judge",
                "message": "The judging agent gave no VERDICT line — read as no verdict (the loop does not stop on it).",
            })
        except Exception:
            pass
    return {**out, **v, "output": v["feedback"] or out.get("output"), "raw_reply": out.get("output"), "judge": "agent"}


# A pair-programming run writes its code into ONE canvas per (run, node): the first
# round creates it, each later round records a new version (the canvas keeps them).
_ARTEFACT_CANVASES: Dict[tuple, str] = {}


def _fold_canvas_contents(text: str, contents: Any) -> str:
    """A flow node's output is what the agent PRODUCED — including code it put into a
    canvas, which the turn's clean text no longer holds (found live: a pair-programming
    driver wrote its code every round and the flow saw only the prose around it). Each
    canvas is appended as a fenced block, once, unless the text already carries it."""
    text = text or ""
    for c in contents or []:
        if not isinstance(c, dict):
            continue
        code = str(c.get("content") or "").strip()
        if not code or code in text:
            continue
        text = (text.rstrip() + "\n\n" if text.strip() else "") + f"```{c.get('language') or ''}\n{code}\n```"
    return text


async def _execute_artefact_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    run_id: str,
    event_emitter,
    **_,
) -> Dict[str, Any]:
    """An artefact node (PROP-007 §6.3) — binds the round's code to a canvas.

    Reads the driver's reply (`data.source`, default `output`), keeps the longest fenced
    block, and writes it to the run's canvas: created on the first round, a new version
    on each later one (the canvas de-duplicates an unchanged round). Outputs the code
    (for the navigator to review), the canvas id and the version number. Nothing new
    is stored outside the canvas."""
    from trusted_data_agent.core import canvas_db
    data = node.get("data", {}) or {}
    src = data.get("source") or "output"
    raw = state_ctx.get(src)
    if raw is None:
        raw = state_ctx.get("output", "")
    code, lang = flow_rounds.code_from_reply(str(raw or ""), data.get("language"))
    key = (run_id, node.get("id"))
    canvas_id = _ARTEFACT_CANVASES.get(key)
    if code is None:
        # No code in the reply (prose about code). Keep the last real version untouched
        # and tell the next round so — never save the prose as if it were the file.
        def _current():
            c = canvas_db.get_canvas(canvas_id, user_id) if canvas_id else None
            n = len(canvas_db.list_versions(canvas_id, user_id) or []) if canvas_id else 0
            return (c or {}).get("content") or "", n
        prev, version = await asyncio.to_thread(_current)
        note = flow_rounds.NO_CODE_NOTE
        text = (prev.rstrip() + "\n\n" + note) if prev.strip() else note
        return {"output": text, "code": text, "language": lang, "canvas_id": canvas_id, "version": version,
                "lines": prev.count("\n"), "no_code": True}

    def _write():
        nonlocal canvas_id
        if canvas_id and canvas_db.get_canvas(canvas_id, user_id):
            canvas_db.update_canvas(canvas_id, user_id, content=code, language=lang)
        else:
            title = f"{data.get('title') or node.get('label') or 'Pair programming'} · run {str(run_id)[:8]}"
            c = canvas_db.create_canvas(user_id, title, code, lang, None, None)
            canvas_id = (c or {}).get("canvas_id")
        versions = canvas_db.list_versions(canvas_id, user_id) if canvas_id else []
        return len(versions or [])

    version = await asyncio.to_thread(_write)
    if canvas_id:
        _ARTEFACT_CANVASES[key] = canvas_id
    return {"output": code, "code": code, "language": lang, "canvas_id": canvas_id, "version": version,
            "lines": code.count("\n")}


# ---------------------------------------------------------------------------
# Files nodes (PROP-010 §4.10) — deterministic, no LLM
# ---------------------------------------------------------------------------

_FILE_TEXT_EXT = {"md", "markdown", "txt", "sql", "py", "js", "ts", "yaml", "yml", "xml", "ttl", "nt", "rdf", "sh", "r", "css"}
_FILE_INPUT_TEXT_LIMIT = 200_000
_FILE_INPUT_ROW_LIMIT = 10_000


async def _execute_file_input_node(node: Dict, state_ctx: Dict[str, Any], user_id: str,
                                   caller_session_id: Optional[str] = None, **_) -> Dict[str, Any]:
    """A File input node — reads a file kept in Files and emits it for the next nodes.

    A table (CSV, TSV, XLSX) is emitted as rows — the same shape a tool node's query gives, so
    `$column` shortcuts and every downstream node read it the same way; JSON as its value; a
    document or text file as its text. The file is read through the file service (the one read
    path), from the store it was written to. Node data: ``file_id`` (required)."""
    from trusted_data_agent.filestore import formats as _fmt
    from trusted_data_agent.filestore import service as _files
    data = node.get("data", {}) or {}
    file_id = (data.get("file_id") or "").strip()
    if not file_id:
        raise RuntimeError(f"File input node '{node.get('label') or node['id']}' has no file chosen")
    try:
        row = _files.owned(file_id, user_id, include_deleted=False)
    except _files.FilesError:
        raise RuntimeError(f"The file chosen for '{node.get('label') or node['id']}' is no longer in Files "
                           f"({data.get('filename') or file_id}) — choose another in the inspector")
    raw = await asyncio.to_thread(_files.read_bytes, row)
    ext = _files.ext_of(row["filename"])
    out: Dict[str, Any] = {"file_id": row["id"], "filename": row["filename"], "tokens_input": 0,
                           "tokens_output": 0, "cost_usd": 0.0}
    if ext in ("csv", "tsv", "xlsx"):
        header, rows = await asyncio.to_thread(_fmt.read_table, raw, ext)
        recs = [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)} for r in rows[:_FILE_INPUT_ROW_LIMIT]]
        out.update({"output": recs, "columns": header, "row_count": len(rows),
                    "truncated": len(rows) > _FILE_INPUT_ROW_LIMIT})
    elif ext in ("json", "jsonld"):
        try:
            out["output"] = json.loads(raw.decode("utf-8-sig"))
        except ValueError as e:
            raise RuntimeError(f"{row['filename']} is not valid JSON: {e}")
    elif ext in _FILE_TEXT_EXT or ext in ("pdf", "docx"):
        if ext in ("pdf", "docx"):
            from trusted_data_agent.llm.document_upload import DocumentUploadHandler
            text = (await asyncio.to_thread(DocumentUploadHandler().extract_text_from_bytes, raw, row["filename"])).get("content", "")
        else:
            text = raw.decode("utf-8", errors="replace")
        out.update({"output": text[:_FILE_INPUT_TEXT_LIMIT], "chars": len(text),
                    "truncated": len(text) > _FILE_INPUT_TEXT_LIMIT})
    else:
        raise RuntimeError(f"A flow can't read .{ext or '?'} files — choose a table, JSON, text or document file")
    if caller_session_id:
        await asyncio.to_thread(_files.record_use, row["id"], user_id, caller_session_id, None)
    return out


def _file_output_bytes(value: Any, fmt: str) -> Tuple[bytes, str]:
    """``(bytes, ext)`` for a File output node's input. ``auto``: rows → CSV, an object → JSON,
    text → Markdown."""
    from trusted_data_agent.filestore import formats as _fmt
    if fmt == "auto":
        if isinstance(value, list) and value and all(isinstance(r, (dict, list, tuple)) for r in value):
            fmt = "csv"
        elif isinstance(value, (dict, list)):
            fmt = "json"
        else:
            fmt = "md"
    if fmt == "csv":
        rows = value if isinstance(value, list) else [value]
        if rows and isinstance(rows[0], dict):
            header: List[str] = []
            for r in rows:
                for k in (r if isinstance(r, dict) else {}):
                    if k not in header:
                        header.append(str(k))
            body = [["" if (r or {}).get(h) is None else str(r.get(h)) for h in header] for r in rows]
        else:
            seq = [list(r) if isinstance(r, (list, tuple)) else [r] for r in rows]
            header, body = [str(c) for c in (seq[0] if seq else [])], [[str(c) for c in r] for r in seq[1:]]
        return _fmt.rows_to_csv(header, body), "csv"
    if fmt == "json":
        return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8"), "json"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return text.encode("utf-8"), ("txt" if fmt == "txt" else "md")


async def _execute_file_output_node(node: Dict, state_ctx: Dict[str, Any], user_id: str, run_id: str,
                                    caller_profile_id: Optional[str] = None,
                                    caller_session_id: Optional[str] = None, **_) -> Dict[str, Any]:
    """A File output node — keeps its input as a file in Files.

    Reads ``data.source`` (default ``output``) from the context, writes it as ``data.format``
    (``auto`` · ``csv`` · ``md`` · ``json`` · ``txt``) under ``data.filename`` (a template:
    ``$name`` / ``{name}`` from the context, plus ``{date}``, ``{time}`` and ``{run}``), and
    passes its input through unchanged. The file's origin is the flow run: it carries the flow,
    the run and the node, and is placed by the node's agent (else the flow's)."""
    from datetime import datetime, timezone
    from trusted_data_agent.filestore import service as _files
    from trusted_data_agent.core import flow_db
    data = node.get("data", {}) or {}
    src = data.get("source") or "output"
    value = state_ctx.get(src)
    if value is None:
        value = state_ctx.get("output")
    if value is None or value == "" or value == []:
        raise RuntimeError(f"File output node '{node.get('label') or node['id']}' has nothing to keep — "
                           f"'{src}' is empty")
    fmt = (data.get("format") or "auto").lower()
    if fmt not in ("auto", "csv", "md", "json", "txt"):
        fmt = "auto"
    payload, ext = _file_output_bytes(value, fmt)
    now = datetime.now(timezone.utc)
    ctx = {**state_ctx, "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H%M"), "run": str(run_id).split("-")[-1][:8]}
    stem = _render_template(data.get("filename") or f"{node.get('label') or 'flow output'} {{date}}", ctx)
    stem = re.sub(r"\.(csv|md|markdown|json|txt)$", "", stem.strip(), flags=re.I) or "flow_output"
    run = await asyncio.to_thread(flow_db.get_run, run_id) or {}
    flow = (await asyncio.to_thread(flow_db.get_flow, run["flow_id"], user_id) if run.get("flow_id") else None) or {}
    profile_id = _resolve_profile_ref(data.get("profile_id"), user_id) or caller_profile_id
    try:
        row = await asyncio.to_thread(
            _files.put_file, user_uuid=user_id, data=payload, filename=f"{_files.slug(stem, 80)}.{ext}",
            source_kind="flow", origin_kind="flow_run", profile_id=profile_id, flow_id=run.get("flow_id"),
            flow_run_id=run_id, flow_node_id=node.get("id"), session_id=caller_session_id,
            source_anchor=f"flow:{run_id}:{node.get('id')}",
            source_json={"flow_name": flow.get("name"), "node_label": node.get("label"),
                         "run_started_at": run.get("started_at") or run.get("created_at")})
    except _files.FilesError as e:
        raise RuntimeError(f"Files refused the output: {e.message}")
    return {"output": value, "file_id": row["id"], "filename": row["filename"], "size_bytes": row.get("size_bytes"),
            "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}


async def _execute_node(
    node: Dict,
    state_ctx: Dict[str, Any],
    user_id: str,
    run_id: str,
    event_emitter,
    caller_session_id: Optional[str] = None,
    node_session_mode: str = "compact",
    caller_profile_id: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Dispatch to the correct executor based on node type."""
    node_type = node.get("type", "task_llm")

    if node_type in ("task_llm", "task_prompt"):
        # task_prompt is an MCP-prompt invocation — same REST execution path as
        # task_llm (LLM-driven), differing only in the query body (prompt_name).
        return await _execute_llm_node(node, state_ctx, user_id, run_id, event_emitter, caller_session_id=caller_session_id, node_session_mode=node_session_mode, caller_profile_id=caller_profile_id, cancel_check=cancel_check)
    elif node_type == "judge":
        return await _execute_judge_node(node, state_ctx, user_id, run_id, event_emitter, caller_session_id=caller_session_id, node_session_mode=node_session_mode, caller_profile_id=caller_profile_id, cancel_check=cancel_check)
    elif node_type == "artefact":
        return await _execute_artefact_node(node, state_ctx, user_id, run_id, event_emitter)
    elif node_type == "task_function":
        return await _execute_function_node(node, state_ctx, user_id, run_id, event_emitter)
    elif node_type == "file_input":
        return await _execute_file_input_node(node, state_ctx, user_id, caller_session_id=caller_session_id)
    elif node_type == "file_output":
        return await _execute_file_output_node(node, state_ctx, user_id, run_id, caller_profile_id=caller_profile_id,
                                               caller_session_id=caller_session_id)
    elif node_type == "decision":
        return await _execute_decision_node(node, state_ctx, run_id=run_id, event_emitter=event_emitter)
    elif node_type == "task_extension":
        return await _execute_extension_node(node, state_ctx, user_id, caller_profile_id=caller_profile_id, caller_session_id=caller_session_id)
    elif node_type == "task_tool":
        return await _execute_tool_node(node, state_ctx, user_id, caller_session_id=caller_session_id, caller_profile_id=caller_profile_id)
    elif node_type == "task_action":
        return await _execute_action_node(node, state_ctx, user_id, caller_session_id=caller_session_id, caller_profile_id=caller_profile_id, run_id=run_id)
    elif node_type in ("start", "merge"):
        return {"output": state_ctx}
    elif node_type == "end":
        return {"output": state_ctx, "final": True}
    else:
        logger.warning(f"Unknown node type '{node_type}' — treating as passthrough")
        return {"output": state_ctx}


# ---------------------------------------------------------------------------
# Execution sequencer
# ---------------------------------------------------------------------------

def _build_execution_order(flow_def: FlowDefinition) -> List[Any]:
    """
    Build an ordered execution plan as a list of execution units.

    Each unit is either:
      - ("node", node_id)         — single node to execute
      - ("scope", scope_id)       — bounded iteration block

    Rules:
      - Non-scoped nodes execute per topological order
      - Scoped nodes execute as a group (scope unit) at the position of
        the first node in the scope per topological order
    """
    if not HAS_NETWORKX:
        # Fallback: execute nodes in definition order, scopes first
        order = []
        seen_scopes: Set[str] = set()
        for node in flow_def.nodes:
            scope_id = flow_def.scope_of(node["id"])
            if scope_id:
                if scope_id not in seen_scopes:
                    order.append(("scope", scope_id))
                    seen_scopes.add(scope_id)
            else:
                order.append(("node", node["id"]))
        return order

    G = flow_def.build_graph()
    try:
        topo = list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible:
        # Graph has cycles even in forward edges — unusual but handle gracefully
        logger.warning("Flow graph has cycles in forward edges — using definition order")
        topo = [n["id"] for n in flow_def.nodes]

    order = []
    seen_scopes: Set[str] = set()
    for node_id in topo:
        scope_id = flow_def.scope_of(node_id)
        if scope_id:
            if scope_id not in seen_scopes:
                order.append(("scope", scope_id))
                seen_scopes.add(scope_id)
        else:
            order.append(("node", node_id))
    return order


def _compute_partial_targets(flow_def: FlowDefinition, start_node_id: str, mode: str) -> Set[str]:
    """Node set for a partial run.

    mode='single'     → just the start node.
    mode='downstream' → the start node and everything reachable from it via
                        forward edges (so a re-run propagates downstream).
    """
    if mode == "single":
        return {start_node_id}
    fwd: Dict[str, List[str]] = {}
    for e in flow_def.edges:
        fwd.setdefault(e.get("source"), []).append(e.get("target"))
    seen = {start_node_id}
    stack = [start_node_id]
    while stack:
        cur = stack.pop()
        for tgt in fwd.get(cur, []):
            if tgt and tgt not in seen:
                seen.add(tgt)
                stack.append(tgt)
    return seen


def _partial_execution_order(flow_def: FlowDefinition, targets: Set[str]) -> List[Any]:
    """Topologically-ordered ('node', id) units restricted to `targets`.

    Partial runs flatten scopes (each targeted node runs standalone) — sufficient
    for re-running specific nodes without re-entering loop machinery.
    """
    if HAS_NETWORKX:
        G = flow_def.build_graph()
        try:
            topo = list(nx.topological_sort(G))
        except Exception:
            topo = [n["id"] for n in flow_def.nodes]
    else:
        topo = [n["id"] for n in flow_def.nodes]
    return [("node", nid) for nid in topo if nid in targets]


def _build_execution_levels(flow_def: FlowDefinition) -> List[List[Any]]:
    """Group execution units into dependency *levels* for parallel execution.

    Returns a list of levels; each level is a list of ("node", id) / ("scope", id)
    units whose dependencies are all satisfied by earlier levels. Units WITHIN a
    level have no dependency on each other and may run concurrently.

    A scope collapses to a single super-unit (its internal nodes keep their own
    sequential, iterative semantics inside `_run_scope`). Edges fully internal to
    one scope do not create cross-unit dependencies.

    Falls back to one-unit-per-level (i.e. fully sequential, preserving the
    historical behaviour) when NetworkX is unavailable or topological layering is
    unsupported.
    """
    sequential = [[unit] for unit in _build_execution_order(flow_def)]
    if not HAS_NETWORKX:
        return sequential
    if not hasattr(nx, "topological_generations"):
        return sequential

    # Map each node to its unit key; scope nodes share their scope's key.
    def _unit_key(node_id: str) -> str:
        scope_id = flow_def.scope_of(node_id)
        return f"scope:{scope_id}" if scope_id else f"node:{node_id}"

    # Build a DiGraph over unit keys from forward edges (cross-unit only).
    U = nx.DiGraph()
    for node in flow_def.nodes:
        U.add_node(_unit_key(node["id"]))
    for edge in flow_def.forward_edges():
        src_u = _unit_key(edge["source"]) if flow_def.get_node(edge["source"]) else None
        tgt_u = _unit_key(edge["target"]) if flow_def.get_node(edge["target"]) else None
        if src_u and tgt_u and src_u != tgt_u:
            U.add_edge(src_u, tgt_u)

    try:
        generations = list(nx.topological_generations(U))
    except Exception as exc:  # cycle in the unit graph, etc.
        logger.warning(f"[FlowExecutor] level layering failed ({exc}); running sequentially")
        return sequential

    def _key_to_unit(key: str) -> Any:
        kind, _, ident = key.partition(":")
        return (kind, ident)

    # Deterministic order within each level for stable trace / replay.
    levels = [sorted(gen) for gen in generations]
    return [[_key_to_unit(k) for k in level] for level in levels if level]


def _partial_execution_levels(flow_def: FlowDefinition, targets: Set[str]) -> List[List[Any]]:
    """Dependency levels for a partial run, restricted to `targets`.

    Partial runs flatten scopes (each targeted node runs standalone), mirroring
    `_partial_execution_order`. Independent targeted nodes in the same level run
    concurrently.
    """
    ordered = _partial_execution_order(flow_def, targets)  # [("node", id), ...]
    sequential = [[unit] for unit in ordered]
    if not HAS_NETWORKX or not hasattr(nx, "topological_generations"):
        return sequential

    target_units = {nid for (_, nid) in ordered}
    G = nx.DiGraph()
    for nid in target_units:
        G.add_node(nid)
    for edge in flow_def.forward_edges():
        if edge["source"] in target_units and edge["target"] in target_units:
            G.add_edge(edge["source"], edge["target"])
    try:
        generations = list(nx.topological_generations(G))
    except Exception as exc:
        logger.warning(f"[FlowExecutor] partial level layering failed ({exc}); running sequentially")
        return sequential
    return [[("node", nid) for nid in sorted(gen)] for gen in generations if gen]


def _active_successors(
    flow_def: FlowDefinition,
    node_id: str,
    node_output: Dict[str, Any],
    excluded_scope_nodes: Optional[Set[str]] = None,
) -> List[str]:
    """
    Determine which successor nodes are activated after a node executes.

    For decision nodes: only the edges matching the condition result are activated.
    For all other nodes: all non-cycle, non-error forward edges are activated.
    Cycle back-edges are excluded (handled by scope executor).
    Nodes inside excluded_scope_nodes are excluded (inter-scope isolation).
    """
    back_edge_ids = {s.get("back_edge_id") for s in flow_def.scopes if s.get("back_edge_id")}
    node_type = flow_def.get_node(node_id).get("type") if flow_def.get_node(node_id) else None
    condition_result = node_output.get("condition_result")

    active = []
    for edge in flow_def.edges:
        if edge.get("source") != node_id:
            continue
        if edge["id"] in back_edge_ids:
            continue
        if edge.get("type") == "error":
            continue  # Error edges only activate on failure (handled in caller)
        if node_type == "decision":
            # Activate edge matching condition result
            edge_condition = edge.get("condition")
            if edge_condition == "true" and condition_result is True:
                active.append(edge["target"])
            elif edge_condition == "false" and condition_result is False:
                active.append(edge["target"])
            elif edge_condition is None:
                active.append(edge["target"])
        else:
            active.append(edge["target"])

    if excluded_scope_nodes:
        active = [n for n in active if n not in excluded_scope_nodes]
    return active


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

class FlowExecutor:
    """
    Executes a flow definition, emitting SSE events for live canvas updates.

    Usage:
        executor = FlowExecutor(flow_def, user_id, run_id, event_emitter)
        await executor.run(input_params)
    """

    def __init__(
        self,
        flow_def: FlowDefinition,
        user_id: str,
        run_id: str,
        event_emitter,  # async callable(event_type: str, payload: dict)
        caller_session_id: Optional[str] = None,
        node_session_mode: str = "compact",
        caller_profile_id: Optional[str] = None,
        partial: Optional[Dict[str, Any]] = None,
        max_tokens_per_run: Optional[int] = None,
    ):
        self.flow_def = flow_def
        self.user_id = user_id
        self.run_id = run_id
        self.emit = event_emitter
        self.caller_session_id = caller_session_id  # session the user submitted from
        self.node_session_mode = node_session_mode   # 'compact' | 'individual'
        self.caller_profile_id = caller_profile_id  # profile of the triggering session (fallback)
        # Partial run: {"targets": [...], "seed_outputs": {...}, "start_node_id", "mode"}.
        # None = full run from the start node.
        self.partial = partial
        # Per-run token budget (input + output). Explicit arg wins; otherwise read
        # from the flow's own settings. None / 0 = unbounded.
        resolved_budget = (
            max_tokens_per_run
            if max_tokens_per_run is not None
            else flow_def.settings.get("max_tokens_per_run")
        )
        try:
            self.max_tokens_per_run: Optional[int] = (
                int(resolved_budget) if resolved_budget else None
            )
        except (TypeError, ValueError):
            self.max_tokens_per_run = None
        # Context precedence (lowest → highest): global variables (cross-flow) →
        # flow parameters (this flow) → predecessor node outputs.
        self.state = FlowState({**_load_global_variables(user_id), **(flow_def.global_params or {})})
        self._cancelled = False
        # Set in run(): whether independent units (and a loop's independent nodes) run concurrently.
        self._parallel = bool(flow_def.settings.get("parallelExecution", False))
        # One session per loop participant, kept across its rounds (individual mode).
        self._scope_sessions: Dict[str, str] = {}
        # Node that raised the unrecovered exception which aborted the run. Stamped
        # in `_run_node` just before re-raising, read in `run()`'s error handler so
        # the `flow_run_error` event can point the canvas at the failed node (and
        # the frontend can offer a one-click "retry from failed node").
        self._failed_node_id: Optional[str] = None

    def cancel(self) -> None:
        self._cancelled = True

    def _over_budget(self) -> bool:
        """True once accumulated tokens exceed the per-run budget (if any).

        Cooperative, not pre-emptive: an in-flight LLM call cannot be cancelled
        mid-flight, so this is checked between dependency levels and between scope
        iterations to stop *launching* further work once the budget is blown.
        """
        if not self.max_tokens_per_run:
            return False
        return (self.state.tokens_input + self.state.tokens_output) > self.max_tokens_per_run

    async def _create_shared_session(self) -> Optional[str]:
        """Create one shared session for compact-mode flow runs with no caller session.

        Defensive fallback only — `execute_flow` now resolves and binds the run
        session up front (reused or auto-provisioned), so `caller_session_id` is
        normally already set by the time `run()` reaches here. Delegates to the
        shared module helper so there is a single session-provisioning path.
        """
        return await _provision_run_session(self.user_id, self.caller_profile_id)

    async def run(self, input_params: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute the full flow. Returns final state dict."""
        if input_params:
            self.state.global_params.update(input_params)

        # Partial run: seed prior node outputs so upstream values are in scope,
        # then restrict execution to the targeted node(s).
        if self.partial:
            seed = self.partial.get("seed_outputs") or {}
            if seed:
                self.state.node_outputs.update(seed)

        update_run_status(self.run_id, "running")
        await self.emit("flow_run_start", {
            "run_id": self.run_id,
            "global_params": self.state.global_params,
            "partial": bool(self.partial),
            "target_nodes": (self.partial or {}).get("targets"),
        })

        if self.partial and self.partial.get("targets"):
            execution_levels = _partial_execution_levels(self.flow_def, set(self.partial["targets"]))
        else:
            execution_levels = _build_execution_levels(self.flow_def)
        execution_trace = []

        # Decision-aware activation. A node runs only when at least one incoming
        # forward edge has been activated by its source. Decision nodes activate
        # only the edge matching their condition_result (via _active_successors),
        # so the untaken branch is pruned instead of every topological node
        # running unconditionally. Seeded with entry nodes (no incoming forward
        # edge). For non-decision flows every forward edge always activates, so
        # all nodes stay active and behaviour is unchanged.
        _incoming = {}
        for _e in self.flow_def.forward_edges():
            _incoming.setdefault(_e.get("target"), []).append(_e)
        active_nodes = {n["id"] for n in self.flow_def.nodes if not _incoming.get(n["id"])}
        # Partial runs target an explicit node set — don't prune them by activation
        # (the user asked for exactly those nodes; predecessor outputs are seeded).
        gate_by_activation = not (self.partial and self.partial.get("targets"))

        def _unit_node_ids(unit_type, unit_id):
            return self.flow_def.scope_nodes(unit_id) if unit_type == "scope" else [unit_id]

        # Parallel-vs-serialize execution (author choice, surfaced in the flow
        # header only when a parallel path exists). A single linear conversation
        # cannot hold concurrent turns, so the two safe modes are:
        #   • serialize (default): run units one at a time in a shared session —
        #     coherent conversation, correct turn numbers, nodes see each other.
        #   • parallelize: run levels concurrently but give each node its OWN
        #     session (no shared writer → no turn-number race). Data still flows
        #     between nodes via output / output_as, which are session-independent.
        parallel_execution = bool(self.flow_def.settings.get("parallelExecution", False))
        self._parallel = parallel_execution
        # The flow's execution mode is authoritative over session sharing, so the
        # corrupting "shared session + concurrent writers" combination can never
        # be selected:
        #   • serialize  → ONE shared session ("driver" — the caller's chat when
        #     in-session, else a single created session); nodes run sequentially.
        #     Keeps everything in one session; no sub-session spawning.
        #   • parallelize→ each node gets its OWN session; levels run concurrently.
        # Context flows between nodes via flow parameters (output / output_as /
        # global params), never via shared conversation — so isolation costs nothing.
        self.node_session_mode = "individual" if parallel_execution else "compact"

        # Flow-level default profile: a task_llm node with no explicit profile_id
        # inherits this (tag resolved → real ID). Lets the author set the execution
        # profile ONCE for the whole flow instead of relying on the LLM to stamp
        # every node. Precedence: node.profile_id → flow default → session caller
        # → user default. Per-node overrides still win.
        _flow_default = _resolve_profile_ref(self.flow_def.settings.get("defaultProfileId"), self.user_id)
        if _flow_default:
            self.caller_profile_id = _flow_default

        # Compact mode + no caller session → create one shared session for all LLM nodes
        if self.node_session_mode == "compact" and not self.caller_session_id:
            self.caller_session_id = await self._create_shared_session()

        # Bound concurrency so a wide level doesn't fan out unbounded LLM calls.
        sem = asyncio.Semaphore(MAX_PARALLEL_NODES)

        async def _run_unit(unit_type: str, unit_id: str):
            """Execute one unit; returns (trace_entries, exception_or_None)."""
            async with sem:
                try:
                    if unit_type == "node":
                        entry = await self._run_node(unit_id, scope_id=None, iteration=0)
                        return [entry], None
                    elif unit_type == "scope":
                        return await self._run_scope(unit_id), None
                    return [], None
                except Exception as exc:  # node with no error path raised
                    return [], exc

        budget_exceeded = False
        try:
            for level in execution_levels:
                if self._cancelled:
                    break
                if self._over_budget():
                    budget_exceeded = True
                    break
                # Partition this level into units to run vs. units to skip,
                # based on decision-aware activation. A unit runs if any of its
                # nodes is active; otherwise it is the untaken branch of a
                # decision (or downstream of one) and is recorded as skipped
                # without executing — and without propagating activation further.
                run_units, skip_units = [], []
                for (ut, uid) in level:
                    nids = _unit_node_ids(ut, uid)
                    if (not gate_by_activation) or any(nid in active_nodes for nid in nids):
                        run_units.append((ut, uid))
                    else:
                        skip_units.append((ut, uid))

                for (ut, uid) in skip_units:
                    for nid in _unit_node_ids(ut, uid):
                        node = self.flow_def.get_node(nid) or {}
                        execution_trace.append({
                            "node_id": nid,
                            "node_type": node.get("type"),
                            "status": "skipped",
                        })
                        await self.emit("flow_node_skipped", {
                            "run_id": self.run_id,
                            "node_id": nid,
                            "node_type": node.get("type"),
                            "label": node.get("label", nid),
                        })

                # Serialize: run units one at a time (shared session stays
                # coherent). Parallelize: run the level's independent units
                # concurrently — each node already has its own session, so there
                # is no shared writer to corrupt. Independent siblings of a
                # failing node still complete; the run aborts after the level
                # settles, mirroring the platform's parallel-phase philosophy.
                if parallel_execution:
                    results = await asyncio.gather(
                        *(_run_unit(ut, uid) for (ut, uid) in run_units)
                    )
                else:
                    results = []
                    for (ut, uid) in run_units:
                        results.append(await _run_unit(ut, uid))
                first_exc = None
                for entries, exc in results:
                    execution_trace.extend(entries)
                    if exc is not None and first_exc is None:
                        first_exc = exc
                if first_exc is not None:
                    raise first_exc

                # Propagate activation from the nodes that actually ran. Decision
                # nodes activate only the edge matching their condition_result;
                # all others activate every forward (non-error) successor.
                for (ut, uid) in run_units:
                    for nid in _unit_node_ids(ut, uid):
                        out = self.state.node_outputs.get(nid, {})
                        for tgt in _active_successors(
                            self.flow_def, nid, out if isinstance(out, dict) else {}
                        ):
                            active_nodes.add(tgt)
                # A level may have pushed us over budget — stop before the next one.
                if self._over_budget():
                    budget_exceeded = True
                    break

            total_tokens = self.state.tokens_input + self.state.tokens_output
            if budget_exceeded:
                final_status = "error"
                reason = (
                    f"Token budget exceeded: {total_tokens} > {self.max_tokens_per_run}. "
                    f"Execution halted before the next step."
                )
                execution_trace.append({"status": "budget_exceeded", "reason": reason})
            elif self._cancelled:
                final_status = "cancelled"
            else:
                final_status = "complete"

            update_run_status(
                self.run_id,
                final_status,
                output_result=self.state.snapshot(),
                execution_trace=execution_trace,
                tokens_input=self.state.tokens_input,
                tokens_output=self.state.tokens_output,
                cost_usd=self.state.cost_usd,
            )
            if budget_exceeded:
                await self.emit("flow_run_error", {
                    "run_id": self.run_id,
                    "error": reason,
                    "budget_exceeded": True,
                    "tokens_input": self.state.tokens_input,
                    "tokens_output": self.state.tokens_output,
                    "cost_usd": self.state.cost_usd,
                })
            else:
                await self.emit("flow_run_complete", {
                    "run_id": self.run_id,
                    "status": final_status,
                    "output": self.state.snapshot(),
                    "tokens_input": self.state.tokens_input,
                    "tokens_output": self.state.tokens_output,
                    "cost_usd": self.state.cost_usd,
                })
        except FlowNeedsInput as ni:
            # PROP-011 phase 3 — the run PAUSES: its outputs so far are persisted (the resume
            # seeds from them), its status reads `input_required`, and the person's answer
            # resumes it at the node that asked. Said to the canvas as a pause, not an error.
            logger.info(f"Flow run {self.run_id} is waiting for the user's input at node {ni.node_id}")
            execution_trace.append({"status": "input_required", "node_id": ni.node_id, "question": ni.text})
            update_run_status(self.run_id, "input_required", output_result=self.state.snapshot(),
                              execution_trace=execution_trace, tokens_input=self.state.tokens_input,
                              tokens_output=self.state.tokens_output, cost_usd=self.state.cost_usd)
            await self.emit("flow_run_complete", {
                "run_id": self.run_id, "status": "input_required", "node_id": ni.node_id,
                "question": ni.question, "output": self.state.snapshot(),
                "tokens_input": self.state.tokens_input, "tokens_output": self.state.tokens_output,
                "cost_usd": self.state.cost_usd})
            return self.state.snapshot()
        except Exception as exc:
            logger.error(f"Flow run {self.run_id} failed: {exc}", exc_info=True)
            # Persist the partial state (node outputs + tokens/cost), not just the
            # status. Without the snapshot, a failed run loses its node_outputs and
            # a later "retry from failed node" partial run cannot seed the
            # successful upstream outputs (see `_latest_run_node_outputs`); the
            # already-spent tokens/cost would also go unaccounted.
            update_run_status(
                self.run_id,
                "error",
                output_result=self.state.snapshot(),
                execution_trace=execution_trace,
                tokens_input=self.state.tokens_input,
                tokens_output=self.state.tokens_output,
                cost_usd=self.state.cost_usd,
            )
            await self.emit("flow_run_error", {
                "run_id": self.run_id,
                "error": _clean_error_message(str(exc)),
                "node_id": self._failed_node_id,
                "tokens_input": self.state.tokens_input,
                "tokens_output": self.state.tokens_output,
                "cost_usd": self.state.cost_usd,
            })
            raise

        return self.state.snapshot()

    async def _run_node(
        self,
        node_id: str,
        scope_id: Optional[str],
        iteration: int,
        extra_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a single node and record results."""
        node = self.flow_def.get_node(node_id)
        if not node:
            logger.warning(f"Node '{node_id}' not found in flow definition")
            return {"node_id": node_id, "status": "skipped"}

        node_type = node.get("type", "task_llm")
        predecessors = [
            e["source"] for e in self.flow_def.forward_edges()
            if e["target"] == node_id
        ]
        state_ctx = self.state.resolve_for_node(node_id, predecessors)
        if extra_ctx:
            # Round history inside a loop — reserved names win over upstream outputs.
            state_ctx.update(extra_ctx)
        elif scope_id is None:
            # A node right after a loop reads its whole transcript and how it ended.
            for _p in predecessors:
                _sid = self.flow_def.scope_of(_p)
                if _sid and _sid in self.state.scope_meta:
                    state_ctx.update(flow_rounds.after_loop_context(
                        self.state.scope_rounds.get(_sid, []), self.state.scope_meta.get(_sid, {})))
                    break
        if scope_id is not None and node_id in self._scope_sessions:
            node = {**node, "data": {**(node.get("data") or {}), "_scope_session_id": self._scope_sessions[node_id]}}
        # PROP-011 phase 3: a resumed run re-runs the node that asked with the person's answer
        # bound — the node's own session, the structured block as its prompt, the binding armed
        _ra = getattr(self, "resume_answer", None)
        if _ra and self.partial and node_id == self.partial.get("start_node_id"):
            node = {**node, "data": {**(node.get("data") or {}), "_resume_answer": _ra}}
            self.resume_answer = None   # once
        started_at = datetime.now(timezone.utc).isoformat()

        await self.emit("flow_node_start", {
            "run_id": self.run_id,
            "node_id": node_id,
            "node_type": node_type,
            "scope_id": scope_id,
            "iteration": iteration,
            "label": node.get("label", node_id),
        })

        try:
            output = await _execute_node(node, state_ctx, self.user_id, self.run_id, self.emit, caller_session_id=self.caller_session_id, node_session_mode=self.node_session_mode, caller_profile_id=self.caller_profile_id, cancel_check=lambda: self._cancelled)
            completed_at = datetime.now(timezone.utc).isoformat()

            # Named output (Flowise-style): also publish the primary result under a
            # user-chosen key so downstream/fan-in nodes can reference it by name
            # instead of colliding on the generic "output" key.
            output = _apply_output_alias(node, output)
            if (scope_id is not None and self.node_session_mode == "individual"
                    and isinstance(output, dict) and output.get("session_id")):
                self._scope_sessions.setdefault(node_id, output["session_id"])

            self.state.record_output(node_id, output)
            tok_in = output.get("tokens_input", 0)
            tok_out = output.get("tokens_output", 0)
            cost = output.get("cost_usd", 0.0)
            self.state.tokens_input += tok_in
            self.state.tokens_output += tok_out
            self.state.cost_usd += cost

            append_node_run(
                run_id=self.run_id,
                node_id=node_id,
                node_type=node_type,
                scope_id=scope_id,
                iteration=iteration,
                status="success",
                input_state=state_ctx,
                output_state=output,
                task_id=output.get("task_id"),
                session_id=output.get("session_id"),
                tokens_input=tok_in,
                tokens_output=tok_out,
                started_at=started_at,
                completed_at=completed_at,
            )

            await self.emit("flow_node_complete", {
                "run_id": self.run_id,
                "node_id": node_id,
                "node_type": node_type,
                "scope_id": scope_id,
                "iteration": iteration,
                "status": "success",
                "output_preview": str(output.get("output", ""))[:200],
                "tokens_input": tok_in,
                "tokens_output": tok_out,
                "cost_usd": cost,
                # LLM-backed post-processing extensions that ran inside this node,
                # each with its own token/cost — surfaced so the canvas attributes
                # them as a distinct step rather than folding them into the node.
                "extensions": output.get("_extensions", []),
                # A judge's verdict / an artefact's canvas — drawn on the node live.
                **({"verdict": {"passed": output.get("passed"), "score": output.get("score"),
                                "verdict": output.get("verdict"), "judge": output.get("judge")}}
                   if node_type == "judge" else {}),
                **({"canvas_id": output.get("canvas_id"), "version": output.get("version")}
                   if node_type == "artefact" else {}),
                # a File node's file — drawn on the node live, with Open in Files
                **({"file_id": output.get("file_id"), "filename": output.get("filename")}
                   if node_type in ("file_input", "file_output") else {}),
            })

            return {
                "node_id": node_id,
                "node_type": node_type,
                "scope_id": scope_id,
                "iteration": iteration,
                "status": "success",
                "started_at": started_at,
                "completed_at": completed_at,
            }

        except FlowNeedsInput as ni:
            # PROP-011: the node asked — NOT a failure. Record the node `input_required` with the
            # task and session the answer re-enters, tell the canvas, and let the run pause.
            completed_at = datetime.now(timezone.utc).isoformat()
            try:
                append_node_run(run_id=self.run_id, node_id=node_id, node_type=node_type, scope_id=scope_id,
                                iteration=iteration, status="input_required", input_state=state_ctx, output_state=None,
                                error_msg=f"waiting for input: {ni.text}"[:500], task_id=ni.task_id,
                                session_id=ni.session_id, started_at=started_at, completed_at=completed_at)
            except Exception as rec_exc:  # noqa: BLE001
                logger.warning(f"[FlowNode] could not record the waiting node {node_id}: {rec_exc}")
            try:
                await self.emit("flow_node_input_required", {
                    "run_id": self.run_id, "node_id": node_id, "node_type": node_type, "scope_id": scope_id,
                    "iteration": iteration, "label": node.get("label", node_id), "question": ni.question,
                    "session_id": ni.session_id})
            except Exception:  # noqa: BLE001
                pass
            self._needs_input = ni
            raise

        except FlowCancelled:
            # Cancelled mid-flight — NOT a failure. Record the node as cancelled,
            # surface a per-node event so the canvas clears its spinner, and return
            # without raising so the run loop settles to `cancelled` (not `error`).
            # The bookkeeping is best-effort: a DB/emit hiccup (e.g. the run row was
            # already removed) must never escalate a cancellation into an `error`.
            completed_at = datetime.now(timezone.utc).isoformat()
            try:
                append_node_run(
                    run_id=self.run_id,
                    node_id=node_id,
                    node_type=node_type,
                    scope_id=scope_id,
                    iteration=iteration,
                    status="cancelled",
                    input_state=state_ctx,
                    output_state=None,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            except Exception as rec_exc:
                logger.warning(f"[FlowNode] could not record cancelled node {node_id}: {rec_exc}")
            try:
                await self.emit("flow_node_cancelled", {
                    "run_id": self.run_id,
                    "node_id": node_id,
                    "node_type": node_type,
                    "scope_id": scope_id,
                    "iteration": iteration,
                    "label": node.get("label", node_id),
                })
            except Exception:
                pass
            return {"node_id": node_id, "node_type": node_type, "status": "cancelled"}

        except Exception as exc:
            completed_at = datetime.now(timezone.utc).isoformat()
            # Surface a clean message (internal `[FlowNode]` prefixes stripped);
            # the full raw error stays in the logs for diagnosis.
            logger.warning(f"[FlowNode] node={node_id} failed: {exc}")
            error_msg = _clean_error_message(str(exc))

            # Check for error edges (alternative paths on failure)
            error_targets = [
                e["target"] for e in self.flow_def.edges
                if e.get("source") == node_id and e.get("type") == "error"
            ]

            append_node_run(
                run_id=self.run_id,
                node_id=node_id,
                node_type=node_type,
                scope_id=scope_id,
                iteration=iteration,
                status="error",
                input_state=state_ctx,
                output_state=None,
                error_msg=error_msg,
                started_at=started_at,
                completed_at=completed_at,
            )

            await self.emit("flow_node_error", {
                "run_id": self.run_id,
                "node_id": node_id,
                "scope_id": scope_id,
                "iteration": iteration,
                "error": error_msg,
                "has_error_path": bool(error_targets),
            })

            if error_targets:
                # Route to error path instead of raising
                self.state.record_output(node_id, {"error": error_msg, "output": None})
                return {"node_id": node_id, "status": "error_routed", "error": error_msg}
            # Unrecovered failure — remember which node aborted the run so the
            # run-level handler can point the canvas at it for a targeted retry.
            self._failed_node_id = node_id
            raise

    async def _run_scope(self, scope_id: str) -> List[Dict[str, Any]]:
        """
        Execute a loop (scope) with bounded rounds.

        A round runs the loop's nodes in dependency order — nodes with no dependency
        between them run concurrently when the flow is parallel (debaters argue at
        once). Every node is handed ROUND HISTORY (`round`, `max_rounds`,
        `previous_<name>`, `history` — flow_rounds.round_context), so round n reads
        what round n−1 produced; nothing is carried implicitly.

        The loop stops on the first of:
          1. its convergence exit — `scope.exit_when = {"node", "condition"}` holds
             against that node's output after a round (a judge's verdict);
          2. `max_iterations` rounds;
          3. the per-run token budget;
          4. a node failing without an error path;
          5. cancellation.
        How it ended is recorded (`state.scope_meta`) and emitted on
        `flow_scope_complete` as `exit_reason` / `converged` — a loop that ran out of
        rounds without a pass says so instead of reading as a success.
        A scope without `exit_when` behaves exactly as before: a fixed number of rounds.
        """
        scope = self.flow_def.get_scope(scope_id)
        if not scope:
            logger.warning(f"Scope '{scope_id}' not found")
            return []

        max_iterations = int(scope.get("max_iterations", DEFAULT_MAX_ITERATIONS) or DEFAULT_MAX_ITERATIONS)
        node_ids = scope.get("node_ids", [])
        scope_name = scope.get("name", scope_id)
        all_scope_node_ids = set(node_ids)
        exit_when = scope.get("exit_when") if isinstance(scope.get("exit_when"), dict) else None
        window = scope.get("history_window")

        # Dependency levels within the scope (forward edges only; the back edge closes
        # the loop between rounds, never inside one).
        back_edge_ids = {s.get("back_edge_id") for s in self.flow_def.scopes}
        internal = [e for e in self.flow_def.edges
                    if e["source"] in all_scope_node_ids and e["target"] in all_scope_node_ids
                    and e["id"] not in back_edge_ids and e.get("type") != "cycle"]
        levels: List[List[str]] = []
        if HAS_NETWORKX:
            G_scope = nx.DiGraph()
            G_scope.add_nodes_from(node_ids)
            G_scope.add_edges_from((e["source"], e["target"]) for e in internal)
            try:
                levels = [sorted(g, key=node_ids.index) for g in nx.topological_generations(G_scope)]
            except nx.NetworkXUnfeasible:
                levels = [[n] for n in node_ids]
        else:
            levels = [[n] for n in node_ids]
        scope_nodes = [self.flow_def.get_node(n) for n in node_ids if self.flow_def.get_node(n)]

        rounds = self.state.scope_rounds.setdefault(scope_id, [])
        trace = []
        await self.emit("flow_scope_start", {
            "run_id": self.run_id,
            "scope_id": scope_id,
            "scope_name": scope_name,
            "max_iterations": max_iterations,
            "node_ids": node_ids,
            "exit_when": exit_when,
        })

        exit_reason, converged, verdict, completed = "max_rounds", False, None, 0
        for iteration in range(max_iterations):
            if self._cancelled:
                exit_reason = "cancelled"
                break
            # Halt a runaway loop as soon as it crosses the per-run token budget,
            # rather than letting it run out all max_iterations.
            if self._over_budget():
                logger.info(
                    f"Scope '{scope_id}' halted at iteration {iteration} — token budget exceeded"
                )
                exit_reason = "budget"
                break

            await self.emit("flow_scope_iteration", {
                "run_id": self.run_id,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "iteration": iteration,
                "round": iteration + 1,
                "max_iterations": max_iterations,
            })

            ctx = flow_rounds.round_context(scope_nodes, rounds, iteration, max_iterations, window)
            round_outputs: Dict[str, Any] = {}
            iteration_failed = False
            for level in levels:
                if self._cancelled:
                    break
                async def _one(nid: str):
                    try:
                        return nid, await self._run_node(nid, scope_id=scope_id, iteration=iteration, extra_ctx=ctx), None
                    except Exception as exc:
                        return nid, None, exc
                if self._parallel and len(level) > 1:
                    results = await asyncio.gather(*(_one(n) for n in level))
                else:
                    results = []
                    for n in level:
                        results.append(await _one(n))
                        if results[-1][2] is not None or (results[-1][1] or {}).get("status") == "error_routed":
                            break
                for nid, entry, exc in results:
                    if exc is not None:
                        logger.error(f"Scope '{scope_id}' iteration {iteration} failed at node '{nid}': {exc}")
                        iteration_failed = True
                        continue
                    trace.append(entry)
                    if nid in self.state.node_outputs:
                        round_outputs[nid] = self.state.node_outputs[nid]
                    if entry.get("status") == "error_routed":
                        iteration_failed = True
                if iteration_failed:
                    break

            if iteration_failed:
                exit_reason = "error"
                break
            if self._cancelled:
                exit_reason = "cancelled"
                break

            rounds.append(flow_rounds.snapshot_round(scope_nodes, round_outputs))
            completed = iteration + 1
            stop, err = flow_rounds.evaluate_exit(exit_when, round_outputs, safe_eval)
            gap = flow_rounds.convergence_gap(round_outputs) if stop else None
            if gap:
                stop, err = False, gap
            if exit_when:
                jo = round_outputs.get(exit_when.get("node")) or {}
                verdict = {"passed": jo.get("passed"), "score": jo.get("score"), "verdict": jo.get("verdict")}
            if err:
                try:
                    await self.emit("flow_node_warning", {
                        "run_id": self.run_id, "node_id": (exit_when or {}).get("node"),
                        "node_type": "scope", "message": f"Loop '{scope_name}': {err} — continuing to the next round.",
                    })
                except Exception:
                    pass
            await self.emit("flow_scope_round_complete", {
                "run_id": self.run_id, "scope_id": scope_id, "scope_name": scope_name,
                "round": completed, "max_iterations": max_iterations,
                "verdict": verdict, "will_stop": bool(stop),
            })
            if stop:
                exit_reason, converged = "converged", True
                break

        meta = {"rounds_completed": completed, "max_rounds": max_iterations, "converged": converged,
                "exit_reason": exit_reason, "last_verdict": verdict, "has_exit": bool(exit_when)}
        self.state.scope_meta[scope_id] = meta
        await self.emit("flow_scope_complete", {
            "run_id": self.run_id,
            "scope_id": scope_id,
            "scope_name": scope_name,
            "iterations_completed": completed,
            "max_iterations": max_iterations,
            "converged": converged,
            "exit_reason": exit_reason,
            "last_verdict": verdict,
        })

        return trace


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _latest_run_node_outputs(flow_id: str, user_id: str) -> Dict[str, Any]:
    """Node outputs from the most recent run that produced any (for partial-run seeding)."""
    try:
        for r in list_runs(flow_id, user_id, limit=10):
            out = r.get("output_result")
            if isinstance(out, dict) and out.get("node_outputs"):
                return out["node_outputs"]
    except Exception as exc:
        logger.warning(f"[FlowExecute] could not load seed outputs: {exc}")
    return {}


async def _provision_run_session(user_id: str, caller_profile_id: Optional[str] = None,
                                 name: Optional[str] = None) -> Optional[str]:
    """Create a fresh chat session for a flow run via the REST API.

    Uses POST /api/v1/sessions (not session_manager directly) so the endpoint's own
    new_session_created broadcast updates the sidebar immediately — the same path the
    compact-mode shared session has always used. The desired session name is passed in
    the create body so the endpoint applies it BEFORE that single broadcast; this
    function deliberately does NOT rename or re-broadcast afterwards (doing so emitted
    a SECOND new_session_created for the same session id, which the sidebar rendered as
    a duplicate card). Fail-open: returns None on any error so a run is never blocked by
    session provisioning.
    """
    try:
        import aiohttp
        from trusted_data_agent.auth.security import create_internal_token
        from trusted_data_agent.core.config import APP_STATE

        internal_token = create_internal_token(user_id)
        port = APP_STATE.get("server_port", 5050)
        base_url = f"http://127.0.0.1:{port}/api"
        headers = {"Authorization": f"Bearer {internal_token}", "Content-Type": "application/json"}
        create_body: Dict[str, Any] = {}
        if caller_profile_id:
            create_body["profile_id"] = caller_profile_id
        # Name the session up front so the endpoint's single new_session_created emit
        # carries it (sidebar reads "<flow>" instead of "New Chat"). No post-create
        # rename + re-emit here — that was the source of the duplicate sidebar card.
        if name:
            create_body["name"] = name
        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{base_url}/v1/sessions", json=create_body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201):
                    logger.warning(
                        f"[FlowExecutor] run-session provisioning failed: HTTP {resp.status} — {await resp.text()}"
                    )
                    return None
                resp_json = await resp.json()
        session_id = resp_json.get("session_id")
        logger.info(f"[FlowExecutor] provisioned run session={session_id}")
        return session_id
    except Exception as exc:
        logger.warning(f"[FlowExecutor] Could not provision run session: {exc}")
        return None


def _render_flow_run_card_html(flow_name: str, status: str, run_id: str, flow_id: str,
                               tin: int, tout: int, cost: float) -> str:
    """Theme-aware, monochrome rich card for the UI session_history (display only)."""
    status_color = {
        "complete": "var(--teradata-orange, #F15F22)",
        "error": "#ef4444",
        "cancelled": "var(--text-muted, #94a3b8)",
    }.get(status, "var(--text-muted, #94a3b8)")
    return (
        f'<div class="flow-run-card" data-flow-id="{flow_id}" data-flow-run-id="{run_id}" '
        f'style="border:1px solid var(--border-primary,#334155);border-radius:8px;'
        f'padding:10px 12px;background:var(--card-bg,#1e293b);">'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="font-weight:600;color:var(--text-primary,#e2e8f0);">Flow Run</span>'
        f'<span style="color:{status_color};font-size:0.85em;text-transform:uppercase;">{status}</span>'
        f'</div>'
        f'<div style="color:var(--text-muted,#94a3b8);font-size:0.9em;margin-top:4px;">{_html.escape(str(flow_name))}</div>'
        f'<div style="color:var(--text-muted,#94a3b8);font-size:0.82em;margin-top:6px;">'
        f'{tin + tout:,} tokens &middot; ${cost:.4f}</div>'
        # Open the flow split panel to inspect run state + per-node output — the ONE
        # shared open control every component card uses (static/js/handlers/
        # panelOpenControl.js): one delegated click, label and aria state follow the
        # live panel. The card's --sp-accent (set from the flow manifest) colours it.
        f'<div style="margin-top:10px;border-top:1px solid var(--border-secondary,rgba(148,163,184,0.15));padding-top:8px;">'
        f'<button type="button" class="sp-open-ctl" data-sp-open="flow" data-sp-open-id="{_html.escape(str(flow_id), quote=True)}" '
        f'data-sp-open-name="{_html.escape(str(flow_name), quote=True)}" aria-controls="flow-split-panel" '
        f'aria-expanded="false" aria-label="Open {_html.escape(str(flow_name), quote=True)} in the side panel">'
        f'<svg class="sp-open-ctl-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/>'
        f'<path d="M14 4v16"/></svg><span class="sp-open-ctl-label">View in Canvas</span>'
        f'<span class="sp-open-ctl-arrow" aria-hidden="true">&rarr;</span></button>'
        f'</div>'
        f'</div>'
    )


async def _record_flow_run_turn(user_id: str, session_id: str, flow_id: str,
                                flow_name: str, run_id: str, source: str = "rest",
                                profile_tag: Optional[str] = None) -> None:
    """Write a DISPLAY-ONLY cohesive flow-run turn into the bound session.

    UI history (session_history) gets a rich card via html_content; the LLM
    chat_object gets only a compact pointer (content) that references run_id so the
    model can pull full node-level detail on demand via TDA_Flow(get_run) — pull, not
    push. Cost is display-only: turn_cost / turn_*_tokens are 0 so the session's
    per-turn accounting is NEVER double-counted (the LLM nodes already counted their
    tokens via their own /query calls). The run's true aggregate is carried in the
    separate flow_total_cost / flow_tokens_* display fields, sourced from the run
    ledger (correct even in individual node-session mode). Fail-open.

    `source` is the transport that triggered the run (default 'rest' — the only path
    that records this turn today; conversation-triggered runs never reach here). It
    stamps the turn so the chat log shows the transport badge ("Rest Call") ALONGSIDE
    the "Flow" origin badge, differentiating REST-run flows from conversation-run ones.
    """
    from trusted_data_agent.core import session_manager
    from trusted_data_agent.core.flow_db import get_run

    run = get_run(run_id) or {}
    status = run.get("status", "complete")
    tin = run.get("tokens_input", 0) or 0
    tout = run.get("tokens_output", 0) or 0
    cost = run.get("cost_usd", 0.0) or 0.0

    pointer = (
        f"[Flow Run] '{flow_name}' {status} — {tin + tout} tokens, ${cost:.4f}. "
        f"Node-level detail is recorded under run_id '{run_id}' (TDA_Flow action 'get_run', "
        f"where the Flow component is enabled for the agent)."
    )
    card_html = _render_flow_run_card_html(flow_name, status, run_id, flow_id, tin, tout, cost)

    # User-side label turn → gives a proper turn pair + clickable avatar + turn_number.
    # source stamps the transport badge ("Rest Call"); origin stamps "Flow"; profile_tag
    # stamps the profile chip (the flow's effective run profile) — all three render on
    # the user message and stack, so the flow-run turn looks like every other "You" turn.
    await session_manager.add_message_to_histories(
        user_uuid=user_id, session_id=session_id, role="user",
        content=f"Flow: {flow_name}",
        html_content=f'<div style="color:var(--text-muted,#94a3b8);">Flow run: {_html.escape(str(flow_name))}</div>',
        source=source, origin="flow", profile_tag=profile_tag,
    )
    # Assistant turn: rich card in the UI, compact pointer in the LLM chat_object.
    await session_manager.add_message_to_histories(
        user_uuid=user_id, session_id=session_id, role="assistant",
        content=pointer, html_content=card_html, source=source, origin="flow", profile_tag=profile_tag,
    )
    # workflow_history entry → makes the turn reload-able + carries the flow link.
    # Accounting fields are 0 (display-only); flow_* fields carry the real aggregate.
    await session_manager.update_last_turn_data(user_id, session_id, {
        "status": status,
        "source": source,
        "origin": "flow",
        "profile_tag": profile_tag,
        "profile_type": "flow",
        "user_query": f"Flow: {flow_name}",
        "final_summary": pointer,
        "flow_id": flow_id,
        "flow_run_id": run_id,
        "flow_total_cost": cost,
        "flow_tokens_input": tin,
        "flow_tokens_output": tout,
        "turn_input_tokens": 0,
        "turn_output_tokens": 0,
        "turn_cost": 0.0,
        "session_cost_usd": cost,
        "provider": "flow",
        "model": "flow-run",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Surface the flow's MAIN orchestrating profile in the session card as the PRIMARY
    # badge — alongside the per-node LLM profiles that update_models_used recorded during
    # execution (kept, just after this one). Without this, the card shows only the node
    # profiles and omits the profile that runs the flow.
    await session_manager.ensure_primary_profile_tag(user_id, session_id, profile_tag)


async def execute_flow(
    flow_id: str,
    user_id: str,
    input_params: Optional[Dict] = None,
    event_emitter=None,
    caller_session_id: Optional[str] = None,
    node_session_mode: str = "compact",
    caller_profile_id: Optional[str] = None,
    start_node_id: Optional[str] = None,
    run_mode: str = "full",
    max_tokens_per_run: Optional[int] = None,
    record_run_turn: bool = True,
    run_turn_source: str = "rest",
    resume_answer: Optional[Dict[str, Any]] = None,   # PROP-011: {user_input, display_message, input_answer, session_id}
) -> str:
    """
    Execute a flow asynchronously.

    Creates a run record, loads the flow definition, and runs the executor.
    Returns the run_id immediately; execution runs in background.

    event_emitter: async callable(event_type: str, payload: dict)
                   Called for all execution events. Can be None for headless runs.
    max_tokens_per_run: optional per-run token ceiling (input + output). When None,
                   falls back to the flow's own settings.max_tokens_per_run.
    """
    flow = get_flow(flow_id, user_id)
    if not flow:
        raise ValueError(f"Flow '{flow_id}' not found")

    definition = flow.get("definition", {})
    if not isinstance(definition, dict):
        raise ValueError("Invalid flow definition")

    # PROP-008 §6 — refuse the run before any node runs if an action node's agent may not
    # perform it (the grant may have been revoked since the flow was saved)
    from trusted_data_agent.core.flow_validation import check_action_nodes
    check_action_nodes(definition, user_id)
    flow_def = FlowDefinition(definition)
    # Heal dangling edges (e.g. an edge wired to a node that was never created)
    # so the flow runs its valid subgraph instead of failing hard at validation.
    pruned = flow_def.prune_orphan_edges()
    if pruned:
        logger.warning(f"Flow '{flow_id}': pruned {len(pruned)} orphan edge(s) before run: {pruned}")
    errors = flow_def.validate()
    if errors:
        raise ValueError(f"Flow validation errors: {'; '.join(errors)}")

    # Partial run (run-from-node / run-single-node): seed from the latest run and
    # restrict execution to the targeted node(s).
    partial = None
    if start_node_id and run_mode in ("single", "downstream"):
        if not flow_def.get_node(start_node_id):
            raise ValueError(f"Node '{start_node_id}' not found")
        targets = _compute_partial_targets(flow_def, start_node_id, run_mode)
        partial = {
            "targets": list(targets),
            "seed_outputs": _latest_run_node_outputs(flow_id, user_id),
            "start_node_id": start_node_id,
            "mode": run_mode,
        }

    # PROP-007 §4.2 — refuse UP FRONT when a required slot that this run would reach
    # is still open (unfilled, or filled with a reference that resolves to nothing).
    # No run record is created, nothing executes; the error names every open slot.
    # The per-node check in _execute_llm_node_once stays as the structural guard.
    from trusted_data_agent.core.flow_topologies.slots import FlowSlotUnfilledError, open_slots
    _open = open_slots(definition,
                       resolver=lambda r: _resolve_profile_ref(r, user_id),
                       only_node_ids=set(partial["targets"]) if partial else None)
    if _open:
        raise FlowSlotUnfilledError(_open, flow_id)

    # Resolve the flow's EFFECTIVE run profile (and its tag) — the profile the run's
    # nodes default to: the caller's profile when a session is explicitly provided,
    # otherwise the flow's own default (`settings.defaultProfileId`). This is the
    # umbrella profile shown on the flow-run turn (so it carries a profile tag like
    # every other "You" turn) AND the profile the persistent runs session is created
    # under (so the session header matches). Deliberately NOT the session's stored
    # profile, which for a headless run would be the *user* default, not the flow's.
    _settings = (definition.get("settings") or {})
    _flow_default_profile_id = _resolve_profile_ref(_settings.get("defaultProfileId"), user_id)
    effective_profile_id = caller_profile_id or _flow_default_profile_id
    run_profile_tag = None
    if effective_profile_id:
        try:
            # Use get_profiles() + match by id — the same source _resolve_profile_ref
            # uses (profiles reliably carry a `tag` key there).
            from trusted_data_agent.core.config_manager import get_config_manager
            _profs = get_config_manager().get_profiles(user_uuid=user_id) or []
            _p = next((p for p in _profs if p.get("id") == effective_profile_id), {})
            run_profile_tag = _p.get("tag") or _p.get("profile_tag")
        except Exception as exc:
            logger.debug(f"[FlowExecute] could not resolve run profile tag: {exc}")

    # Bind the run to a session, following the standard REST session lifecycle
    # (create-then-consume) — NO reuse of any prior session:
    #   • caller session provided (explicit `session_id` / "Run in Chat") → consume it.
    #   • otherwise ("Run Flow" / headless) → provision a FRESH session dedicated to
    #     THIS run, under the flow's effective profile. Each run is isolated in its own
    #     session; it never writes into an earlier one. A client that WANTS runs to
    #     share a session does the REST-correct thing and passes its own `session_id`.
    # Fail-open — a provisioning hiccup leaves the run unbound rather than blocking.
    if caller_session_id:
        effective_session_id = caller_session_id
    else:
        effective_session_id = await _provision_run_session(
            user_id, effective_profile_id, name=(flow.get("name") or "Flow")
        )

    run = create_run(flow_id, user_id, input_params, session_id=effective_session_id)
    run_id = run["run_id"]

    # Canonical run emitter — the SINGLE source of truth for live run events.
    #
    # Every run, regardless of who started it (chat LLM tool call, REST /execute,
    # scheduler, …), fans out each event the same way:
    #   (a) the per-run SSE stream  → drives the open canvas's blow-by-blow
    #       animation (with replay buffer for late subscribers); and
    #   (b) the per-user notification channel → an always-on broadcast the open
    #       canvas uses to DISCOVER a run it didn't start (run-lifecycle only, to
    #       keep the shared channel light — the granular node/scope events stay on
    #       the per-run stream). This is the same channel KG animation listens on.
    #
    # Callers no longer choose a bus — that initiator-specific divergence was the
    # root cause of conversation-triggered runs never animating the open canvas.
    from trusted_data_agent.core.config import APP_STATE

    _LIFECYCLE_EVENTS = ("flow_run_start", "flow_run_complete", "flow_run_error")

    async def _canonical_emitter(event_type: str, payload: dict):
        # Stamp identity so every consumer can route/guard by flow_id + run_id.
        enriched = {**(payload or {}), "flow_id": flow_id, "run_id": run_id}

        # (a) per-run stream — full granular animation
        _broadcast_flow_event(run_id, event_type, enriched)

        # (b) per-user notification channel — run lifecycle only (discovery)
        if event_type in _LIFECYCLE_EVENTS:
            queues = APP_STATE.get("notification_queues", {}).get(user_id, set())
            note = {"type": event_type, "payload": enriched}
            for q in list(queues):
                try:
                    q.put_nowait(note)
                except Exception:
                    pass

        # (c) optional extra observer (tests / future hooks) — additive, not a bus
        if event_emitter is not None:
            try:
                await event_emitter(event_type, enriched)
            except Exception as exc:
                logger.warning(f"[FlowExecute] extra event observer failed: {exc}")

    emitter = _canonical_emitter

    executor = FlowExecutor(flow_def, user_id, run_id, emitter, caller_session_id=effective_session_id, node_session_mode=node_session_mode, caller_profile_id=caller_profile_id, partial=partial, max_tokens_per_run=max_tokens_per_run)
    executor.resume_answer = resume_answer   # PROP-011: the start node re-runs with the answer

    # Register the live executor so a cancel request can reach it (set its
    # cooperative `_cancelled` flag). Deregister when the run task settles —
    # success, error, or cancellation — so the map never leaks finished runs.
    _ACTIVE_EXECUTORS[run_id] = executor

    # Fire and forget — caller tracks via run_id. After the run settles, write a
    # cohesive display-only flow-run turn into the bound session so the run is
    # monitorable in the session-scoped UX. Suppressed for chat-triggered runs
    # (record_run_turn=False) which already produce the agent turn + per-node turns.
    _flow_name = flow.get("name") or "Flow"

    async def _run_and_record():
        try:
            await executor.run(input_params)
        finally:
            # Deregister the moment the run settles — BEFORE writing the cohesive turn —
            # so an already-finished run reports signalled=False on cancel and the brief
            # turn-write window doesn't keep the executor "live".
            _ACTIVE_EXECUTORS.pop(run_id, None)
            if record_run_turn and effective_session_id:
                try:
                    await _record_flow_run_turn(user_id, effective_session_id, flow_id, _flow_name, run_id, source=run_turn_source, profile_tag=run_profile_tag)
                except Exception as exc:
                    logger.warning(f"[FlowExecute] could not record flow-run turn for {run_id}: {exc}")
            # WS3 online flow eval — tail-sampled, deterministic-only quality scoring
            # of the completed run. Off by default (ONLINE_EVAL_ENABLED); fully wrapped,
            # never affects the run. A flow is its own agent in the Agent Performance lens.
            try:
                from trusted_data_agent.eval.online import evaluate_flow_run_online
                await evaluate_flow_run_online(
                    user_uuid=user_id, flow_id=flow_id, run_id=run_id,
                    flow_name=_flow_name, profile_tag=run_profile_tag)
            except Exception as exc:
                logger.warning(f"[FlowExecute] online flow eval skipped for {run_id}: {exc}")
            # Name the fresh per-run session after the flow, re-asserted AFTER the run —
            # an LLM node's turn triggers the platform's session auto-naming, which would
            # otherwise rename it after the LLM output. Only for a freshly-provisioned run
            # session (never a caller-provided chat session).
            if not caller_session_id and effective_session_id:
                try:
                    from trusted_data_agent.core import session_manager as _sm
                    await _sm.update_session_name(user_id, effective_session_id, _flow_name)
                except Exception:
                    pass

    task = asyncio.create_task(_run_and_record())

    # Backstop deregister (idempotent) in case the wrapper is cancelled before its finally.
    def _deregister(_task, _run_id=run_id):
        _ACTIVE_EXECUTORS.pop(_run_id, None)

    task.add_done_callback(_deregister)

    return run_id


async def resume_flow_for_input(paused_run_id: str, node_id: str, user_id: str, resumption: Dict[str, Any]) -> Optional[str]:
    """PROP-011 phase 3 — the person answered the question a flow node raised: resume the flow
    from that node (a downstream partial run seeded from the paused run's outputs) with the
    answer bound to the node's own session. Returns the new run id, or None."""
    from trusted_data_agent.core.flow_db import get_run
    run = get_run(paused_run_id)
    if not run or run.get("user_id") != user_id or run.get("status") != "input_required":
        return None
    try:
        return await execute_flow(
            run["flow_id"], user_id, input_params=run.get("input_params") or None,
            caller_session_id=run.get("session_id"), start_node_id=node_id, run_mode="downstream",
            resume_answer={"user_input": resumption.get("user_input"), "display_message": resumption.get("display_message"),
                           "input_answer": resumption.get("input_answer"), "session_id": resumption.get("session_id")},
            record_run_turn=True, run_turn_source="rest")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[FlowExecute] resuming run {paused_run_id} at {node_id} failed: {exc}")
        return None


def generate_empty_flow() -> Dict[str, Any]:
    """Return a minimal valid flow definition with start/end nodes."""
    start_id = f"node-{uuid.uuid4().hex[:8]}"
    end_id = f"node-{uuid.uuid4().hex[:8]}"
    edge_id = f"edge-{uuid.uuid4().hex[:8]}"
    return {
        "nodes": [
            {"id": start_id, "type": "start", "label": "Start",
             "position": {"x": 250, "y": 50}, "data": {}},
            {"id": end_id, "type": "end", "label": "End",
             "position": {"x": 250, "y": 300}, "data": {}},
        ],
        "edges": [
            {"id": edge_id, "source": start_id, "target": end_id, "type": "normal"},
        ],
        "scopes": [],
        "global_params": {},
        # New flows default to auto-layout ON so the diagram stays tidy and re-fits as
        # the panel resizes. Existing flows keep their stored setting (frontend reads
        # settings.autoLayout, defaulting to false when absent).
        "settings": {"autoLayout": True},
    }
