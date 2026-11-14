"""
TDA_Platform — Uderia's own operations as one tool (platform operations, phase 2: reads).

The agent FINDS operations by describing what it wants and CALLS one by its op id. Everything
it may find and call is derived from the route descriptors (`api/operations.py`) under one
policy (`core/platform_operations.py`); nothing here lists use cases or re-implements a route:
a call runs the real route in-process as the turn's user, so the REST layer's auth, ownership
checks and governance apply exactly as they do to any client.

  * HEADLESS — no renderer; the result is for the model (`spec`), `metadata.suppress_inline`.
  * OFF UNTIL TURNED ON — `profile_defaults.enabled_for` is empty: no existing agent gains the
    tool (or its companion skill, or a signature ingredient) until its owner enables it. A
    call that arrives for an agent where it is not enabled (a replayed plan) is refused here too.
  * A person's acts (`human_only`) are found and named, never called; writes and reads of other
    users' data are found and not offered yet.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trusted_data_agent.components.base import BaseComponentHandler, ComponentRenderPayload, RenderTarget

logger = logging.getLogger("quart.app")

ACTIONS = ("find", "call", "ask")
# (session, turn, anchor) a playbook was delivered in — sent once per turn, pointed to after.
_DELIVERED: set = set()
_OP_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
# The names a planner writes for this tool's arguments when it plans blind (seen live on the
# Optimize lane: operation · op_id · search_terms · arguments) → the contract's names.
_ALIASES = {
    "op": ("op", "op_id", "operation_id", "operation_name"),
    "query": ("query", "search", "search_terms", "terms", "q"),
    "args": ("args", "arguments", "params", "parameters"),
    "fields": ("fields", "columns", "keys"),
    "agent": ("agent", "to", "target", "target_agent", "ask_agent"),
    "question": ("question", "prompt", "message"),
}
_KNOWN = {"action", "operation", "mode"} | {n for names in _ALIASES.values() for n in names}


def normalize(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """The contract's arguments from whatever names arrived. Deterministic, never guesses an
    operation: it only renames, infers the action from what is present (an op id → call, a
    query → find), moves a top-level operation argument (``profile_id``) into ``args`` and
    ``fields`` out of it."""
    a = dict(arguments or {})
    out: Dict[str, Any] = {}
    for canon, names in _ALIASES.items():
        for n in names:
            if a.get(n) not in (None, "", [], {}):
                out[canon] = a[n]
                break
    action = str(a.get("action") or a.get("mode") or "").strip().lower()
    operation = a.get("operation")
    if isinstance(operation, str) and operation.strip():
        o = operation.strip()
        if o.lower() in ACTIONS and not action:
            action = o.lower()
        elif _OP_ID.match(o) and "op" not in out:
            out["op"] = o
        elif "query" not in out and o.lower() not in ACTIONS:
            out["query"] = o
    if isinstance(out.get("query"), list):
        out["query"] = " ".join(str(x) for x in out["query"])
    if action not in ACTIONS:
        action = "call" if out.get("op") else ("find" if out.get("query") else action)
    out["action"] = action
    args = out.get("args")
    # A call's arguments written into `query` as a JSON object (seen live:
    # query='{"profile_id": "@TCOPT"}' on a call) — they ARE the arguments.
    if action == "call" and args in (None, "", {}) and isinstance(out.get("query"), str):
        try:
            parsed = json.loads(out["query"])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            args = parsed
            out.pop("query", None)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass
    if isinstance(args, dict):
        args = dict(args)
        if "fields" in args and "fields" not in out:
            out["fields"] = args.pop("fields")
    extra = {k: v for k, v in a.items() if k not in _KNOWN}
    if extra and action == "call":
        args = dict(args) if isinstance(args, dict) else ({} if args in (None, "") else args)
        if isinstance(args, dict):
            for k, v in extra.items():
                args.setdefault(k, v)
    if action == "ask":
        # ask takes an agent and a question; a model may put them in args, or write the
        # question as `query` and the agent as `profile_id` (the call convention it knows)
        if isinstance(args, dict):
            for k in ("agent", "profile_id", "tag"):
                if args.get(k) and "agent" not in out:
                    out["agent"] = args[k]
            for k in ("question", "prompt", "query"):
                if args.get(k) and "question" not in out:
                    out["question"] = args[k]
        if "agent" not in out:
            for k in ("profile_id", "tag"):
                if a.get(k):
                    out["agent"] = a[k]
                    break
        if "question" not in out and out.get("query"):
            out["question"] = out.pop("query")
        out.pop("args", None)
        return out
    out.pop("args", None)
    if args not in (None, "", {}):
        out["args"] = args
    return out


class PlatformHandler(BaseComponentHandler):

    @property
    def component_id(self) -> str:
        return "platform"

    @property
    def tool_name(self) -> str:
        return "TDA_Platform"

    @property
    def is_deterministic(self) -> bool:
        # NOT on the Optimize lane's component fast path: a call depends on what an earlier
        # find returned, which only the tactical planner sees — the fast path would pass the
        # strategic plan's arguments, written before any find ran (invented op ids, live).
        return False

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        arguments = normalize(arguments)
        action = arguments.get("action")
        if action not in ACTIONS:
            return False, ("TDA_Platform takes action ('find', 'call' or 'ask'), query (find), op and args (call), "
                           "fields (call), agent and question (ask) — say action 'find' with a query, or 'call' with an op")
        if action == "find" and not str(arguments.get("query") or "").strip():
            return False, "find requires 'query': a few words naming what you want to see or do"
        if action == "call" and not str(arguments.get("op") or "").strip():
            return False, "call requires 'op': an op id exactly as 'find' returned it"
        if action == "ask" and not (str(arguments.get("agent") or "").strip() and str(arguments.get("question") or "").strip()):
            return False, "ask requires 'agent' (an @TAG) and 'question' (what to ask it, in full sentences)"
        return True, ""

    def _payload(self, action: str, spec: Dict[str, Any], events=None) -> ComponentRenderPayload:
        meta = {"tool_name": self.tool_name, "action": action, "suppress_inline": True}
        if spec.get("error"):
            meta["error"] = spec["error"]
        if events:
            meta["_component_llm_events"] = list(events)
        return ComponentRenderPayload(
            component_id=self.component_id, render_target=RenderTarget.INLINE,
            spec=spec, html="", title="Platform", metadata=meta)

    @staticmethod
    def _enabled_here(context: Dict[str, Any]) -> bool:
        pid, uid = context.get("profile_id"), context.get("user_uuid")
        if not (pid and uid):
            return False
        try:
            from trusted_data_agent.components import enablement
            from trusted_data_agent.components.manager import get_component_manager
            from trusted_data_agent.core.config_manager import get_config_manager
            profile = get_config_manager().get_profile(pid, uid) or {}
            comp = get_component_manager().get_component("platform")
            return enablement.is_enabled(comp, profile, uid)
        except Exception as e:  # noqa: BLE001 — fail closed: this tool is opt-in
            logger.debug(f"[platform] enablement check failed: {e}")
            return False

    def _playbook(self, query: str, found_ops, context: Dict[str, Any],
                  exact_anchor: Optional[str] = None, roles=None) -> Optional[Dict[str, Any]]:
        """The playbook that covers this find, read for the agent — or None.

        Selection moves from the model to the platform here (measured: on a weak model the
        playbook was skipped in up to 3 of 4 turns, and every skipped turn began with find).
        Only within what the agent already holds: its ACTIVE progressive skills, the version it
        is pinned to, its anchor grant. The read goes through the skill library's own read
        path, so the turn records it as a read (`skill_section_loaded`) exactly like a
        TDA_SkillLibrary call — citation honesty and the attribution see no difference. A
        playbook already delivered in this turn is pointed to, not sent again. Never raises.
        """
        try:
            from components.builtin.skill_library.handler import SkillLibraryHandler
            from trusted_data_agent.core import platform_operations as ops
            from trusted_data_agent.skills.package import safe_library_relpath
            from trusted_data_agent.core.platform_roles import playbook_allowed
            lib = SkillLibraryHandler()
            profile = lib._resolve_profile(context)
            if not profile:
                return None
            sections, owner = [], {}
            for sid in lib._progressive_candidates(profile):
                patterns = lib._allowed_patterns(profile, sid)
                _, skill_dir = lib._skill_source(sid, profile)
                if not skill_dir:
                    continue
                for e in lib._index(sid, profile):
                    anchor = e.get("anchor") or ""
                    rel = safe_library_relpath(e.get("file", ""))
                    if not anchor or anchor in owner or rel is None or not lib._anchor_allowed(anchor, patterns):
                        continue
                    if roles is not None and not playbook_allowed(roles, sid, anchor):
                        continue   # outside the slice the agent's roles are taught
                    path = Path(skill_dir) / "library" / rel
                    if not path.is_file():
                        continue
                    sec = ops.playbook_section(anchor, e.get("title"), e.get("summary"),
                                               path.read_text(encoding="utf-8", errors="replace"),
                                               cat=ops.catalog())
                    if sec:
                        sections.append(sec)
                        owner[anchor] = sid
            if exact_anchor is not None:   # named exactly (a call that gave a playbook's anchor as its op)
                if exact_anchor not in owner:
                    return None
                best, candidates = exact_anchor, [exact_anchor]
            else:
                best, candidates = ops.rank_playbooks(query, sections, list(found_ops or []))
            if not best:
                return {"candidates": candidates} if candidates else None
            sid, turn = context.get("session_id"), context.get("current_turn_number")
            key = (sid, turn, best) if sid and turn is not None else None
            if key and key in _DELIVERED:
                return {"anchor": best, "already": True}
            payload = lib._read(owner[best], best, profile)
            if (payload.spec or {}).get("error"):
                return None
            if key:
                if len(_DELIVERED) > 2048:
                    _DELIVERED.clear()
                _DELIVERED.add(key)
            md = payload.metadata or {}
            return {"anchor": best, "skill_id": owner[best], "text": md.get("text", ""),
                    "events": md.get("_component_llm_events") or []}
        except Exception as e:  # noqa: BLE001 — a find never fails for want of a playbook
            logger.debug(f"[platform] playbook delivery skipped: {e}")
            return None

    @staticmethod
    def _roles(context: Dict[str, Any]):
        """The calling agent's ACTIVE roles (platform_roles) — its default role when it names
        none; an empty list when its profile cannot be read (fail closed: nothing listed)."""
        from trusted_data_agent.core import platform_roles
        try:
            from components.builtin.skill_library.handler import SkillLibraryHandler
            profile = SkillLibraryHandler._resolve_profile(context)
        except Exception:  # noqa: BLE001
            profile = None
        if not profile:
            return []
        return platform_roles.roles_of(profile)

    @staticmethod
    def _writes_and_agent(context: Dict[str, Any]):
        """The agent's write mode (off · review · allow — off when its profile cannot be read)
        and who it is, for a submitted request's record."""
        from trusted_data_agent.core import platform_operations as ops
        try:
            from components.builtin.skill_library.handler import SkillLibraryHandler
            profile = SkillLibraryHandler._resolve_profile(context)
        except Exception:  # noqa: BLE001
            profile = None
        if not profile:
            return "off", None
        return ops.write_mode(profile), {"id": profile.get("id"), "tag": profile.get("tag")}

    @staticmethod
    def _attach_playbook(spec: Dict[str, Any], pb: Optional[Dict[str, Any]], lead: str):
        """Put a delivered (or pointed-to) playbook into the answer; → the read event, if any."""
        if pb and pb.get("text"):
            spec["playbook"] = {"anchor": pb["anchor"], "skill_id": pb["skill_id"],
                                "instruction": lead + " Follow it: it says which operations to call and how to answer.",
                                "text": pb["text"]}
            return pb["events"]
        if pb and pb.get("already"):
            spec["playbook"] = {"anchor": pb["anchor"], "instruction": "Already delivered in this turn — follow it."}
        elif pb and pb.get("candidates"):
            spec["playbooks"] = {"candidates": pb["candidates"],
                                 "instruction": ("More than one playbook may cover this: read the one that fits "
                                                 "with TDA_SkillLibrary(action='read', anchor=...).")}
        return None

    @staticmethod
    def _as_object(value: Any) -> Tuple[Dict[str, Any], str]:
        if value in (None, ""):
            return {}, ""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return {}, "args must be an object of argument names to values"
        if not isinstance(value, dict):
            return {}, "args must be an object of argument names to values"
        return value, ""

    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        from trusted_data_agent.core import platform_operations as ops

        arguments = normalize(arguments)
        action = arguments.get("action") or ""
        if not self._enabled_here(context):
            return self._payload(action, {"error": "This agent may not use the platform tool — its owner has not turned "
                                                    "it on (Agent profile → Capabilities → Platform). Do not retry; tell the user."})
        ok, why = self.validate_arguments(arguments)
        if not ok:   # every lane, not only the ones that validate before process
            return self._payload(action, {"action": action, "error": why})
        user_uuid = context.get("user_uuid")
        roles = self._roles(context)
        writes, agent = self._writes_and_agent(context)
        if action == "ask":
            from trusted_data_agent.core import platform_asking
            try:
                from components.builtin.skill_library.handler import SkillLibraryHandler
                asker = SkillLibraryHandler._resolve_profile(context) or {}
            except Exception:  # noqa: BLE001
                asker = {}
            if not asker:
                return self._payload("ask", {"action": "ask", "error": "the asking agent could not be read — nothing was asked"})
            out = await platform_asking.ask(asker, arguments.get("agent"), arguments.get("question"), user_uuid,
                                            session_id=context.get("session_id"),
                                            turn_id=context.get("current_turn_number"))
            return self._payload("ask", {"action": "ask", **out})
        if action == "find":
            import asyncio
            query = str(arguments.get("query") or "")
            tier = await asyncio.to_thread(ops.user_tier, user_uuid)
            out = ops.find(query, tier, roles=roles, writes=writes)
            spec = {"action": "find", "query": arguments.get("query"), **out}
            from trusted_data_agent.core import platform_asking
            try:
                from components.builtin.skill_library.handler import SkillLibraryHandler
                _asker = SkillLibraryHandler._resolve_profile(context)
            except Exception:  # noqa: BLE001
                _asker = None
            hint = await asyncio.to_thread(platform_asking.find_hint, _asker, roles, user_uuid)
            if hint:
                spec["can_ask"] = hint
            pb = await asyncio.to_thread(self._playbook, query, [f["op"] for f in out.get("found", [])],
                                         context, None, roles)
            events = self._attach_playbook(spec, pb, "A playbook covers this — it has been read for you.")
            return self._payload("find", spec, events)

        op_name = str(arguments.get("op") or "").strip()
        if "/" in op_name:
            # An op id never holds "/"; a playbook anchor does (seen live: a call of
            # 'agents/inspect'). The model meant to read it — hand it over instead of a dead end.
            import asyncio
            pb = await asyncio.to_thread(self._playbook, "", [], context, op_name, roles)
            if pb and (pb.get("text") or pb.get("already")):
                spec = {"action": "call", "op": op_name}
                events = self._attach_playbook(
                    spec, pb, f"'{op_name}' is a playbook, not an operation — it has been read for you.")
                return self._payload("call", spec, events)
        args, err = self._as_object(arguments.get("args"))
        if err:
            return self._payload("call", {"action": "call", "op": arguments.get("op"), "error": err})
        fields = arguments.get("fields")
        if isinstance(fields, str):
            try:
                fields = json.loads(fields)
            except ValueError:
                fields = [f.strip() for f in fields.split(",") if f.strip()]
        out = await ops.call(str(arguments.get("op") or ""), args, user_uuid,
                             fields=fields if isinstance(fields, list) else None, roles=roles,
                             writes=writes, agent=agent, session_id=context.get("session_id"),
                             turn_id=context.get("current_turn_number"))
        return self._payload("call", {"action": "call", **out})
