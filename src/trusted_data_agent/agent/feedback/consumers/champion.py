"""Vote consumer — the RAG champion case (planner repositories).

The pre-existing mechanism, moved behind the dispatcher unchanged: a vote on a turn
that was SEEDED as a RAG case propagates to that case's score, where the retrieval
filter (``user_feedback_score >= 0``) and the champion election consume it.

Keyed on the turn carrying a ``case_id`` — NOT on profile type. The RAG worker
(``main.py``) stamps ``case_id`` on whatever turn it seeded, which is not exclusively
``tool_enabled``; keying on the case is therefore both the accurate rule and the
engine-agnostic one.
"""
from __future__ import annotations

import logging

from trusted_data_agent.agent.feedback.dispatcher import register_consumer, signal_enabled
from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")


@register_consumer("champion")
async def champion(event: VoteEvent) -> None:
    from trusted_data_agent.core.config import APP_CONFIG, APP_STATE

    if not getattr(APP_CONFIG, "RAG_ENABLED", False):
        return

    case_id = event.turn.get("case_id")
    if not case_id:
        # No seeded case for this turn — nothing to score. NEVER call the retriever
        # speculatively: update_case_feedback scans every collection for the file.
        return

    retriever = APP_STATE.get("rag_retriever_instance")
    if not retriever:
        return

    await retriever.update_case_feedback(case_id, event.score)
    app_logger.info(
        f"[feedback] case {case_id} scored {event.score} "
        f"(turn {event.turn_id}, session {event.session_id})")

    # An explicit human endorsement outranks the automatic demotion heuristic: reset
    # the consecutive replay-failure counter so a champion the user has just vouched
    # for is not retired by failures it already recovered from. The counter's reset
    # path already exists — this is the same "guided success" signal, sourced from a
    # human instead of an execution outcome.
    #
    # Deliberately upvote-only: a downvote must NOT increment the counter here. That
    # counter means "this champion was replayed and the run really failed"; a turn the
    # user disliked may well have executed perfectly, and conflating the two would let
    # taste retire a working plan through a mechanism built for broken ones. The
    # downvote's own effect — exclusion via the score — is already complete.
    if event.is_up and signal_enabled("champion_upvote"):
        try:
            await retriever.record_champion_replay_outcome(case_id, succeeded=True)
        except Exception as e:  # noqa: BLE001 — promotion is best-effort
            app_logger.debug(f"[feedback] replay-counter reset skipped for {case_id}: {e}")
