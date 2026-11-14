"""
TDA_FindTools — the Governed Tool Surface's search (S6).

An agent whose surface is governed is OFFERED the tools that could answer this turn and holds
the rest; this tool searches the HELD set with the surface's own scorer and WIDENS the surface
for the rest of the turn. Design contract:
  * HEADLESS — no renderer, `spec` carries the found tools, `metadata.suppress_inline`.
  * STRUCTURALLY INERT — `is_profile_relevant()` is True only when the profile's
    `toolSurface.mode` is `governed` (no deployment switch); every other profile never sees it.
  * NEVER WIDENS PAST AUTHORITY — the denied set is not searchable; a denied tool the query
    names is reported as `denied_matching` (a named finding on the surface), never returned.
  * The act is recorded on the turn (`tool_surface.widened`, `finds`) and broadcast live.
"""
import logging
from typing import Any, Dict, Tuple

from trusted_data_agent.components.base import BaseComponentHandler, ComponentRenderPayload, RenderTarget

logger = logging.getLogger("quart.app")


class FindToolsHandler(BaseComponentHandler):

    @property
    def component_id(self) -> str:
        return "find_tools"

    @property
    def tool_name(self) -> str:
        return "TDA_FindTools"

    @property
    def is_deterministic(self) -> bool:
        return True

    def is_profile_relevant(self, profile_config: Dict[str, Any]) -> bool:
        """The profile asks for a governed surface — else inert."""
        try:
            from trusted_data_agent.agent import tool_surface as _ts
            return _ts.armed_governed(profile_config)
        except Exception:  # noqa: BLE001
            return False

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        if not str(arguments.get("query") or "").strip():
            return False, "query is required: a few words naming the capability you need"
        return True, ""

    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        from trusted_data_agent.agent import tool_surface as _ts
        q = str(arguments.get("query") or "").strip()
        r = _ts.run_find(q)
        found = r.get("found") or []
        if found:
            names = ", ".join(t["name"] for t in found)
            text = (f"Found {len(found)} tool(s) for '{q}' — added to your tool set for this turn: {names}. "
                    f"Call them directly now.")
        elif r.get("note"):
            text = r["note"]
        else:
            text = f"No held tool matches '{q}'."
            if r.get("closest"):
                text += f" Closest held tools: {', '.join(r['closest'])} — rephrase once with those words, or say what is missing."
            if r.get("denied_matching"):
                text += f" Denied by your authority (never available): {', '.join(r['denied_matching'])}."
        if found and r.get("denied_matching"):
            text += f" Also matching but denied by your authority: {', '.join(r['denied_matching'])}."
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={"query": q, "found": found, "held_remaining": r.get("held_remaining", 0),
                  "denied_matching": r.get("denied_matching") or [], "closest": r.get("closest") or [], "message": text},
            html="",
            title="Tool Finder",
            metadata={"tool_name": self.tool_name, "suppress_inline": True, "message": text,
                      "found": [t["name"] for t in found]},
        )
