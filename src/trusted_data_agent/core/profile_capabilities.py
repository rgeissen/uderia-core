"""ONE definition of what a profile's CLASS can actually reach.

WHY THIS MODULE EXISTS
Reported 2026-09-09: attesting `@PDOCS`, a Focus (`rag_focused`) agent, was refused
on production with *"the tool catalog is not classified on this instance — run the
profile's connection test (classification) first, then attest"*. It could never
succeed. A Focus agent does knowledge retrieval; it never initializes MCP, so it
never classifies, so its snapshot is empty forever:

  * the runtime  (`configuration_service`): needs_mcp = tool_enabled
                                            or (llm_only and useMcpTools)
  * the editor   (`configurationHandler.js`): "Focus and Coordinate never call MCP
                 directly" — neither the MCP field nor the classification card renders
  * `POST /v1/profiles/<id>/reclassify`: "genie and rag_focused profiles don't need
                 MCP" — it CLEARS the classification and returns 200 success

...while the trust layer asked a SIXTH, weaker question of its own — *"is an
`mcpServerId` stored?"* — which a Focus profile answers yes to routinely, because a
pack stamps the binding onto every member it ships and a class change leaves it
behind. Storing an id is not reaching a catalog.

The result was a closed loop: the gate demanded a surface the class cannot have and
named a remedy the class cannot perform, and running that remedy returned success
while leaving exactly the state that triggers the refusal (proven live: attest 409 →
reclassify 200 → attest 409, byte-identical). On production 555 profiles were held
in it, `@PDOCS` among them.

THE RULE: reachability is a property of the CLASS, and it is asked in one place.

DELIBERATELY NOT FOLDED IN: `agent_pack_manager.profile_needs_mcp` asks a different
question — does this MANIFEST entry need a binding resolved at import time (it also
honours `requires_mcp` and `mcpServerName`). That is about wiring a pack, not about
what a running agent reaches, and collapsing the two would change import behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# The stored profile classes. `conversation_with_tools` is NOT one of them — it is a
# reading the eval layer derives from `llm_only` + `useMcpTools`, never persisted.
TOOL_ENABLED = "tool_enabled"
LLM_ONLY = "llm_only"
RAG_FOCUSED = "rag_focused"
GENIE = "genie"


def profile_class(profile: Optional[Dict[str, Any]]) -> str:
    """The profile's stored class, defaulting exactly as the runtime does.

    `configuration_service` reads `profile.get('profile_type', 'tool_enabled')`, so a
    profile with no class recorded is treated as tool-bearing. Keeping that default
    means a malformed profile keeps the STRICTER reading — it can never be widened by
    accident into "reaches nothing".
    """
    return (profile or {}).get("profile_type") or TOOL_ENABLED


def consumes_mcp_catalog(profile: Optional[Dict[str, Any]]) -> bool:
    """Does this profile's class actually reach the MCP tool/prompt catalog?

    Optimize always does. Ideate does only through its capability toggle. Focus and
    Coordinate never call MCP directly — Focus retrieves, and a coordinator routes to
    experts that make their own calls.

    NB this asks about the CLASS, never about whether an `mcpServerId` happens to be
    stored: an inert binding left by a pack install or a class change is not a
    reachable surface, and treating it as one is the defect this module closes.
    """
    cls = profile_class(profile)
    if cls == TOOL_ENABLED:
        return True
    if cls == LLM_ONLY:
        return bool((profile or {}).get("useMcpTools"))
    return False
