"""Agent Memory component handler (TDA_Memory).

Deterministic, 0-token storage. The tool is only ever invoked when the component is enabled
for the profile (standard component gating), so this delegates straight to ``MemoryStore`` —
no re-resolution of governance. See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.4.
"""
import logging
from typing import Any, Dict, Tuple

from trusted_data_agent.components.base import (
    BaseComponentHandler, ComponentRenderPayload, RenderTarget,
)

logger = logging.getLogger("quart.app")


class MemoryComponentHandler(BaseComponentHandler):
    @property
    def component_id(self) -> str:
        return "memory"

    @property
    def tool_name(self) -> str:
        return "TDA_Memory"

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        action = (arguments.get("action") or "").strip().lower()
        if action == "remember" and not (arguments.get("content") or "").strip():
            return False, "remember requires 'content'"
        if action == "forget" and not (arguments.get("query") or "").strip():
            return False, "forget requires 'query'"
        return True, ""

    async def process(self, arguments: Dict[str, Any],
                      context: Dict[str, Any]) -> ComponentRenderPayload:
        from components.builtin.memory.store import MemoryStore
        from components.builtin.memory.types import (
            MemoryItem, MemorySource, resolve_kind, detect_global_scope)

        action = (arguments.get("action") or "").strip().lower()
        user_uuid = context.get("user_uuid", "")
        profile_id = context.get("profile_id") or None
        session_id = context.get("session_id")
        turn_id = context.get("current_turn_number")
        store = MemoryStore()

        spec: Dict[str, Any] = {"action": action}
        message = ""
        try:
            if action == "remember":
                # Default is THIS agent (per-agent memory); an explicit global/shared/all
                # scope — OR unambiguous global phrasing in the user's request that the LLM
                # may have ignored (deterministic safety net) — makes it user-wide.
                scope_global = (arguments.get("scope") or "agent").strip().lower() in (
                    "global", "shared", "all") or detect_global_scope(context.get("user_query"))
                _content = (arguments.get("content") or "").strip()
                item = MemoryItem(
                    content=_content,
                    kind=resolve_kind(arguments.get("kind"), _content),
                    source=MemorySource.EXPLICIT,
                    profile_id=None if scope_global else profile_id,
                    session_id=session_id,
                    turn_id=str(turn_id) if turn_id is not None else None,
                )
                saved = store.add(user_uuid, item)
                spec["item"] = saved.to_dict()
                spec["scope"] = "global" if scope_global else "profile"
                message = "Saved to agent memory."

            elif action == "forget":
                from components.builtin.memory import runtime
                n = runtime.forget(user_uuid, arguments.get("query") or "", profile_id=profile_id)
                spec["removed"] = n
                message = f"Removed {n} memory item(s)." if n else "No matching memory found."

            else:  # list_memories
                # The agent lists what it RECALLS — the shared global pool plus its own
                # (the configured scope, as recall and forget read it), never another
                # agent's local memories: those reached this agent's context through here.
                scope = "profile"
                try:
                    from trusted_data_agent.core.config_manager import get_config_manager
                    _prof = get_config_manager().get_profile(profile_id, user_uuid) if profile_id else None
                    if ((((_prof or {}).get("componentConfig") or {}).get("memory") or {})
                            .get("scope") == "global"):
                        scope = "global"
                except Exception:  # noqa: BLE001 — fall back to the narrower scope
                    pass
                items = store.list_for_recall(user_uuid, profile_id, scope=scope)
                spec["items"] = [it.to_dict() for it in items]
                spec["count"] = len(items)
                message = f"{len(items)} item(s) in agent memory."
        except Exception as e:
            logger.warning(f"[memory] handler {action} failed: {e}")
            spec["error"] = str(e)
            message = "Memory operation failed."

        # High-level stats for the inline card (so it conveys something interesting,
        # consistent with the other component cards). Best-effort — never fails the op.
        # The counts are also folded into the message so the card always shows them
        # even if only the message line renders.
        try:
            all_items = store.list_items(user_uuid, include_inactive=False)
            by_kind: Dict[str, int] = {}
            g = a = 0
            for it in all_items:
                by_kind[it.kind] = by_kind.get(it.kind, 0) + 1
                if it.profile_id is None:
                    g += 1
                elif it.profile_id == profile_id:
                    a += 1
            total = len(all_items)
            local = total - g  # all agent-scoped (across agents)
            spec["stats"] = {"total": total, "global": g, "local": local,
                             "agent": a, "kinds": by_kind}
            counts = f"{total} {'memory' if total == 1 else 'memories'} · {g} global · {local} local"
            if action == "remember":
                message = f"Saved to {'global' if spec.get('scope') == 'global' else 'this agent'} memory · {counts}"
            elif action == "forget":
                message = f"Removed {spec.get('removed', 0)} · {counts}"
            else:  # list_memories
                message = counts
        except Exception:
            pass

        spec["message"] = message
        return ComponentRenderPayload(
            component_id="memory",
            render_target=RenderTarget.INLINE,
            spec=spec,
            title=message or "Agent Memory",
            tts_text=message,
            metadata={"tool_name": "TDA_Memory", "action": action},
        )
