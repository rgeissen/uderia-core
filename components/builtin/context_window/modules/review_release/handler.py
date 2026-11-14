"""Released Reviews context module — HITL release-to-conversation.

When the ``review`` enforcement mode withholds an answer, the original is parked
for human disposition. Plain *approve* is audit-only (the original stays in the
record and the capsule). But the owner can EXPLICITLY choose **release into the
conversation** — and this module is how that choice takes effect:

- **Forward-only, append-only.** The sealed, delivered turn is never rewritten
  (its Ed25519 seal stays valid). The released original is injected into the
  context of SUBSEQUENT turns, so the agent can actually use and discuss what
  the owner released — closing the "I cannot see the original" honesty gap
  without touching history.
- **Labelled as a human decision.** The injected block states who released it
  and from which turn, so the model (and the context-window snapshot) can never
  mistake it for ordinary conversation content.
- **Self-gating.** No released items for this session → empty contribution →
  byte-identical baseline. Activated for every profile class via the
  registry-default merge (no CWT edits needed).

Companion: the outbound guardrail's per-session released-value allowlist
(``guardrails/runtime._released_allowlist``) stops the released values being
re-redacted, so the agent can state what it can now see.
"""
from __future__ import annotations

import logging

from ..base import AssemblyContext, Contribution, ContextModule
from ..token_estimator import estimate_tokens

logger = logging.getLogger("quart.app")

_HEADER = (
    "[Owner-released after human review]\n"
    "The following content was withheld by a trust guard on an earlier turn of THIS "
    "conversation and then EXPLICITLY released into it by the owner (a recorded human "
    "decision). Treat it as normal, available conversation content:\n"
)


class ReviewReleaseModule(ContextModule):
    """Injects owner-released withheld originals into subsequent turns' context."""

    @property
    def module_id(self) -> str:
        return "review_release"

    def applies_to(self, profile_type: str) -> bool:
        # Any engine class can have an answer withheld for review.
        return True

    async def contribute(self, budget: int, ctx: AssemblyContext) -> Contribution:
        try:
            session_id = getattr(ctx, "session_id", None) or (ctx.session_data or {}).get("session_id")
            if not (ctx.user_uuid and session_id):
                return Contribution(content="", tokens_used=0,
                                    metadata={"released": 0, "reason": "no_session"},
                                    condensable=True)
            from trusted_data_agent.core import review_queue
            released = review_queue.released_originals_for_session(ctx.user_uuid, session_id)
            if not released:
                return Contribution(content="", tokens_used=0,
                                    metadata={"released": 0, "reason": "none_released"},
                                    condensable=True)

            src_label = {"guardrail_outbound": "outbound guardrail", "grounding": "grounding gate"}
            parts = [_HEADER]
            included = 0
            used = estimate_tokens(_HEADER)
            for item in released:
                entry = (f"--- released from turn {item.get('turn_id') or '?'} "
                         f"(withheld by the {src_label.get(item.get('source'), item.get('source'))}) ---\n"
                         f"{(item.get('original_text') or '').strip()}\n")
                cost = estimate_tokens(entry)
                if included > 0 and used + cost > budget:
                    break  # budget-aware; oldest releases win (they anchor the thread)
                parts.append(entry)
                used += cost
                included += 1

            content = "\n".join(parts)
            return Contribution(
                content=content,
                tokens_used=estimate_tokens(content),
                metadata={
                    "released": included,
                    "total_released": len(released),
                    # Transparency (charter #19): the snapshot shows WHICH turns' releases
                    # entered context — never a silent injection.
                    "release_turns": [i.get("turn_id") for i in released[:included]],
                },
                condensable=True,
            )
        except Exception as e:
            logger.warning(f"review_release module failed open (no injection): {e}")
            return Contribution(content="", tokens_used=0,
                                metadata={"released": 0, "error": str(e)}, condensable=True)

    async def condense(self, content: str, target_tokens: int, ctx: AssemblyContext) -> Contribution:
        """Condense by dropping the newest releases first (keep the header + oldest)."""
        if not content:
            return Contribution(content="", tokens_used=0, metadata={"condensed": True})
        blocks = content.split("\n--- released from")
        kept = blocks[0]
        for b in blocks[1:]:
            candidate = kept + "\n--- released from" + b
            if estimate_tokens(candidate) > target_tokens:
                break
            kept = candidate
        return Contribution(content=kept, tokens_used=estimate_tokens(kept),
                            metadata={"condensed": True}, condensable=True)
