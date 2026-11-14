"""Vote consumer — coordinator vote propagation (per-engine: ``genie``).

A coordinator does not answer; an expert does. When exactly ONE expert produced the
answer the user rated, that rating belongs to the expert's turn — where it flows into
that expert's OWN engine mechanism (an Optimize expert's champion case, a Focus
expert's retrieval feedback) through the normal dispatcher. That is the whole design:
propagation needs no genie-specific engine code because it re-enters the one seam.

**The propagation rule is deliberately narrow.** With two or more experts the answer
is a synthesis and attribution is ambiguous — crediting or blaming all of them would
manufacture signal the user never gave. Nothing is propagated in that case; the
coordinator turn's own annotation already records what the human actually said.

Never widen this rule without a way to attribute the failure to one expert.
"""
from __future__ import annotations

import logging

from trusted_data_agent.agent.feedback.dispatcher import register_consumer, signal_enabled
from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")


def eligible_slave(slaves) -> dict | None:
    """The ONE expert a vote may propagate to, or None.

    Pure — the whole propagation rule in one testable function:
      * exactly one expert was consulted, and
      * it succeeded, and
      * it is addressable (its child session AND the turn within it are known).

    A coordinator turn persisted before ``slave_turn`` existed is NOT addressable and
    yields None: propagation is skipped rather than aimed at a guessed turn number.
    """
    if not isinstance(slaves, list) or len(slaves) != 1:
        return None
    s = slaves[0]
    if not isinstance(s, dict):
        return None
    if s.get("status") != "success":
        return None
    if not s.get("slave_session_id") or s.get("slave_turn") is None:
        return None
    return s


@register_consumer("genie")
async def genie(event: VoteEvent) -> None:
    if not signal_enabled("genie_propagation"):
        return
    if event.profile_type != "genie":
        return
    # Only a user-originated vote propagates. A vote that ARRIVED by propagation must
    # not hop onward to a sibling — the dispatcher's depth stop is the backstop, this
    # is the rule.
    if event.depth != 0:
        return

    from trusted_data_agent.eval.types import RunRecord

    slave = eligible_slave(RunRecord(event.turn).slaves_invoked())
    if not slave:
        return

    from trusted_data_agent.core import session_manager

    # Re-enters the SAME seam, so the expert's own engine consumer does the work.
    # A cleared vote propagates too: the expert's store must not keep a demotion the
    # user has retracted.
    await session_manager.update_turn_feedback(
        event.user_uuid, slave["slave_session_id"], int(slave["slave_turn"]),
        event.vote, _depth=event.depth + 1)
    app_logger.info(
        f"[feedback] coordinator vote {event.vote!r} propagated to expert "
        f"{slave.get('profile_tag')} ({slave['slave_session_id']} turn {slave['slave_turn']})")
