"""
AgentDiscoveryStrategy — capability descriptor for the existing live-database,
LLM-assisted KG constructor (``discovery_mode`` "tool" / "prompt").

Status (M2): this registers the *capability* of the agent-discovery path so the
registry is complete and the KG-instance update lifecycle can ask "does this KG's
strategy support resync?" (it does not — agent discovery against a live DB is
non-deterministic, so re-sync/diff is not offered).

The agent-discovery *orchestration* currently lives inline in
``rest_routes.py::kg_generate`` (session creation + multi-turn agent discovery +
trace parsing + phase 3/3.5/4).  It is intentionally left in place: it depends on
a live LLM + MCP server and cannot be exercised in CI, so a faithful mechanical
extraction is deferred until the declarative path's integration coverage gives us
a safety net.  ``construct()`` therefore raises if called directly — the endpoint
runs the inline path for these modes (additive dispatch: only "industry_model" is
routed through the strategy).
"""

from __future__ import annotations

from .base import ConstructorContext, ConstructorResult, KGConstructorStrategy
from .registry import register


@register
class AgentDiscoveryStrategy(KGConstructorStrategy):
    discovery_modes = ("tool", "prompt")
    supports_resync = False
    label = "Live database discovery (agent)"

    async def construct(self, ctx: ConstructorContext) -> ConstructorResult:  # pragma: no cover
        raise NotImplementedError(
            "Agent-discovery construction runs inline in kg_generate; it is not "
            "yet routed through the strategy interface. See module docstring."
        )
