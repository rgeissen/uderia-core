"""Vote signal — the pure event type.

A ``VoteEvent`` is an immutable snapshot of ONE vote transition on ONE turn. It is
the only thing a feedback consumer ever receives, so it must carry everything a
consumer needs WITHOUT reaching back into the session store (the session lock is
already released by the time consumers run).

Pure: no I/O, no app imports, no side effects — importable standalone.

Design notes (verified against real session files, not assumed):
  * ``profile_id`` is deliberately ABSENT. A ``workflow_history`` turn carries
    ``profile_tag`` and ``profile_type`` but NOT ``profile_id``; the session-level
    ``profile_id``/``last_executed_profile_id`` describe the session's CURRENT
    profile, which is not necessarily the one that produced an older (or @TAG
    overridden) turn. A field that silently lies is worse than an absent one, so a
    consumer that genuinely needs the id resolves it from ``profile_tag`` itself.
  * ``turn`` is a deep copy taken inside the session transaction. Consumers may
    read it freely; mutating it affects nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# The three legal vote values. ``None`` means the user CLEARED their vote — a real
# transition consumers must handle, never a "no vote happened" signal.
VOTE_UP = "up"
VOTE_DOWN = "down"


@dataclass(frozen=True)
class VoteEvent:
    """One vote transition on one turn."""

    user_uuid: str
    session_id: str
    turn_id: int
    vote: Optional[str]        # "up" | "down" | None (cleared)
    prev_vote: Optional[str]   # the value BEFORE this update (same domain)
    turn: dict                 # deep-copied workflow_history turn (post-update)
    profile_tag: Optional[str] = None   # per-turn, authoritative (may be None on old turns)
    profile_type: Optional[str] = None  # llm_only | rag_focused | tool_enabled | genie | …
    depth: int = 0             # genie propagation depth; 0 = user-originated
    propagated: bool = False   # True when re-dispatched by the genie consumer

    # ── derived, no I/O ──────────────────────────────────────────────────────
    @property
    def score(self) -> int:
        """The vote as the numeric score the RAG case store speaks (1 / -1 / 0)."""
        if self.vote == VOTE_UP:
            return 1
        if self.vote == VOTE_DOWN:
            return -1
        return 0

    @property
    def is_up(self) -> bool:
        return self.vote == VOTE_UP

    @property
    def is_down(self) -> bool:
        return self.vote == VOTE_DOWN

    @property
    def is_cleared(self) -> bool:
        return self.vote is None

    @property
    def was_down(self) -> bool:
        """The PREVIOUS state was a downvote — the trigger for restore-style
        consumers (a store that deactivated something on the downvote must undo
        that when the user retracts or flips it)."""
        return self.prev_vote == VOTE_DOWN

    @property
    def changed(self) -> bool:
        """False when the endpoint re-sent an identical vote. Consumers must be
        idempotent regardless; this simply lets them skip cheaply."""
        return self.vote != self.prev_vote
