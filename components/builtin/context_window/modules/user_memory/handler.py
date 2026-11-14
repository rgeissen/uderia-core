"""User Memory context module (Tier 2.2 — A7).

Recalls durable cross-session facts/preferences about the user and injects them into the
context window for EVERY profile type (charter #14). Deterministic ranking; abstains cleanly
(empty contribution) when the memory component is disabled for the profile. The actual store
+ ranking live in the memory component backend (``components.builtin.memory.runtime``); this
module is the thin context-window delivery seam.

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.3.
"""
from __future__ import annotations

import logging

from ..base import AssemblyContext, Contribution, ContextModule
from ..token_estimator import estimate_tokens

logger = logging.getLogger("quart.app")


class UserMemoryModule(ContextModule):
    """Injects the user's recalled agent memory ("[Remembered about this user]")."""

    @property
    def module_id(self) -> str:
        return "user_memory"

    def applies_to(self, profile_type: str) -> bool:
        # All engine classes benefit from recalling durable user facts.
        return True

    async def contribute(self, budget: int, ctx: AssemblyContext) -> Contribution:
        try:
            from components.builtin.memory import runtime

            profile = dict(ctx.profile_config or {})
            if "id" not in profile and ctx.profile_id:
                profile["id"] = ctx.profile_id
            query = (ctx.session_data or {}).get("current_query", "") or ""

            result, block = runtime.recall_for_turn(
                user_uuid=ctx.user_uuid,
                profile=profile,
                profile_type=ctx.profile_type,
                query=query,
                budget_tokens=budget,
                token_estimator=estimate_tokens,
            )
            if not block:
                reason = "no_memory" if result.considered == 0 else "disabled_or_empty"
                return Contribution(content="", tokens_used=0,
                                    metadata={"recalled": 0, "reason": reason}, condensable=True)
            return Contribution(
                content=block,
                tokens_used=estimate_tokens(block),
                metadata={
                    "recalled": result.count,
                    "considered": result.considered,
                    "scope": result.scope,
                    # Item contents so the Live-Status snapshot is transparent (charter #19).
                    "items": [it.content for it in result.items],
                },
                condensable=True,
            )
        except Exception as e:
            logger.debug(f"[user_memory] contribute failed (fail-safe, empty): {e}")
            return Contribution(content="", tokens_used=0,
                                metadata={"recalled": 0, "reason": "error"}, condensable=True)

    async def condense(self, content: str, target_tokens: int,
                      ctx: AssemblyContext) -> Contribution:
        """Drop the lowest-ranked lines (they arrive already ranked high→low) to fit."""
        try:
            lines = content.split("\n")
            header, items = lines[0], lines[1:]
            kept, used = [header], estimate_tokens(header)
            for line in items:
                t = estimate_tokens(line)
                if used + t > target_tokens:
                    break
                kept.append(line); used += t
            out = "\n".join(kept) if len(kept) > 1 else ""
            return Contribution(content=out, tokens_used=estimate_tokens(out),
                                metadata={"condensed": True}, condensable=True)
        except Exception:
            return Contribution(content="", tokens_used=0, metadata={"condensed": True})
