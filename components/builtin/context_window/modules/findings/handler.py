"""
Findings context module — the verified observations of earlier turns, re-read not recalled.

Reads each prior turn's ``findings`` (stamped at persistence by ``update_last_turn_data`` for
every engine; older turns are extracted on read, the same extractor) and renders one bounded
block, newest turn first. Condensation keeps fewer turns but NEVER drops the newest turn's
findings (the pin): a count the platform returned on the last turn must still be in front of
the model, or it recalls it from memory — the failure this module exists to prevent. The
metadata carries the turns shown and the text, so the Live Status snapshot is transparent and
the executor can hand the SAME text to the grounding gate.

Applies to the three engines that answer from data or knowledge (Optimize, Ideate incl. ReAct,
Focus); a Coordinate turn's experts run in their own sessions, each with its own findings.
Abstains (empty contribution) on the first turn and when no earlier turn returned rows.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ..base import AssemblyContext, Contribution, ContextModule
from ..token_estimator import estimate_tokens, tokens_to_chars

logger = logging.getLogger("quart.app")


class FindingsModule(ContextModule):
    """Contributes the session's verified results from earlier turns."""

    #: the newest turns whose findings survive condensation whole
    PINNED_TURNS = 1

    @property
    def module_id(self) -> str:
        return "findings"

    def applies_to(self, profile_type: str) -> bool:
        return profile_type in ("tool_enabled", "llm_only", "rag_focused")

    def _turns(self, ctx: AssemblyContext):
        from trusted_data_agent.agent import findings as _f
        wf = ((ctx.session_data or {}).get("last_turn_data") or {}).get("workflow_history") or []
        return _f.session_turns(wf, exclude_turn=ctx.turn_number)

    def _render(self, ctx: AssemblyContext, budget_tokens: int) -> Contribution:
        from trusted_data_agent.agent import findings as _f
        turns = self._turns(ctx)
        if not turns:
            return Contribution(content="", tokens_used=0,
                                metadata={"turns": 0, "reason": "no_findings"}, condensable=False)
        text = _f.format_block(turns, tokens_to_chars(budget_tokens), pinned_turns=self.PINNED_TURNS)
        if not text:
            return Contribution(content="", tokens_used=0,
                                metadata={"turns": 0, "reason": "no_findings"}, condensable=False)
        shown = text.count("\nTurn ") + (1 if "\nTurn " not in text and "Turn " in text else 0)
        return Contribution(
            content=text,
            tokens_used=estimate_tokens(text),
            metadata={"turns": shown, "turns_available": len(turns),
                      "findings": sum(len(fs) for _, fs in turns), "text": text},
            condensable=True,
        )

    async def contribute(self, budget: int, ctx: AssemblyContext) -> Contribution:
        if ctx.is_first_turn:
            return Contribution(content="", tokens_used=0,
                                metadata={"turns": 0, "reason": "first_turn"}, condensable=False)
        try:
            return self._render(ctx, budget)
        except Exception as exc:  # noqa: BLE001 — a module must never break a turn
            logger.debug(f"findings module: contribution skipped ({exc})")
            return Contribution(content="", tokens_used=0,
                                metadata={"turns": 0, "reason": "error"}, condensable=False)

    async def condense(self, content: str, target_tokens: int, ctx: AssemblyContext) -> Contribution:
        """Fewer turns, newest kept — the pinned turn survives whatever the target."""
        try:
            c = self._render(ctx, target_tokens)
            c.metadata = {**(c.metadata or {}), "condensed": True, "strategy": "fewer_turns"}
            return c
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"findings module: condense skipped ({exc})")
            return Contribution(content="", tokens_used=0, metadata={"condensed": True, "reason": "error"})

    async def purge(self, session_id: str, user_uuid: str) -> Dict[str, Any]:
        return {"purged": False, "reason": "Findings are the turn record's own; purge the session instead"}
