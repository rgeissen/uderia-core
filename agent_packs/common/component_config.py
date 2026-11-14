"""Shared component-enablement policy for Uderia agent packs.

Root cause this addresses: an imported profile with no ``componentConfig`` inherits
the platform's permissive per-type defaults — for EVERY profile type the six action
components (chart/canvas/dashboard/flow/knowledge_graph/scheduler) are enabled by
default, and each one's companion skill then auto-injects (~5.5K tokens/turn total).
Declaring ``componentConfig`` explicitly in a pack's manifest makes the imported
profile authoritative instead of inheriting those defaults.

Policy:
  * MEMORY is always enabled — cross-session recall of the user costs no per-turn
    action context and materially improves the assistant ("always at least memory").
  * The six ACTION components are disabled unless a pack opts one back in for a
    specific agent via ``extra_enabled`` (e.g. a data-modeling agent that genuinely
    uses ``knowledge_graph``).
"""

# Action components a pack must default explicitly OFF. The first seven carry a
# companion skill (auto-injected when the component is enabled for the profile);
# skill_library carries NONE by design (PROP-005 recorded exception — its quality
# layer is each progressive skill's own root) and is additionally relevance-gated.
# `find_tools` is deliberately NOT here (Oct 2026): the Tool Finder is the governed tool
# surface's own search, offered exactly when the agent's surface is governed — a pack that
# switched it off would ship an agent that holds tools with no way to bring one back.
ACTION_COMPONENTS = ("chart", "canvas", "dashboard", "flow", "knowledge_graph", "scheduler",
                     "database_navigator", "skill_library", "compute",
                     "explain_plan", "action", "obda_read", "obda_model")


def focused_component_config(memory: bool = True, extra_enabled=None) -> dict:
    """Return an explicit ``componentConfig`` dict for a focused pack profile.

    Args:
        memory: enable the memory component (default True — the "always at least
            memory" rule). Pass False only for an agent that must not retain memory.
        extra_enabled: iterable of action-component ids to KEEP enabled for this
            profile (e.g. ``["knowledge_graph"]``). Unknown ids are ignored.

    Returns:
        ``{component_id: {"enabled": bool}}`` for all seven builtin action/memory
        components — safe to drop straight into a manifest profile entry.
    """
    keep = {c for c in (extra_enabled or ()) if c in ACTION_COMPONENTS}
    cfg = {comp: {"enabled": comp in keep} for comp in ACTION_COMPONENTS}
    cfg["memory"] = {"enabled": bool(memory)}
    return cfg
