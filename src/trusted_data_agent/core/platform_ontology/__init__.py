"""Platform Ontology — the per-user-segmented situational view of a user's world.

Tier 5 Step 1 (the "Ontology" nav). A **projection over truth**: the user's
segment graph is *derived on read* from the existing source-of-truth tables
(profiles, LLM/MCP configs, collections, knowledge graphs, agent memory) — never
a parallel authoritative store, so it can never drift.

See docs/roadmap/TIER_5_PLATFORM_ONTOLOGY.md §5/§5a.
"""

from trusted_data_agent.core.platform_ontology.projection import (
    build_segment,
    build_multi_user_segment,
    _list_users,
    PLATFORM_ENTITY_COLORS,
)

__all__ = ["build_segment", "build_multi_user_segment", "_list_users", "PLATFORM_ENTITY_COLORS"]
