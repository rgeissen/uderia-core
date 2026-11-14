"""Universal vote signal — the upvote/downvote a user gives a turn, turned into a
platform-wide learning signal.

Design + execution plan: docs/roadmap/VOTE_SIGNAL_PLAN.md

Public surface (import from here, never from the submodules):
    VoteEvent               — the immutable vote transition
    dispatch_vote(event)    — run every consumer; never raises
    schedule_vote_dispatch  — fire-and-forget the above
    register_consumer(name) — decorator used by consumers/*.py
"""
from trusted_data_agent.agent.feedback.types import VoteEvent, VOTE_DOWN, VOTE_UP
from trusted_data_agent.agent.feedback.dispatcher import (
    dispatch_vote,
    register_consumer,
    registered_consumers,
    schedule_vote_dispatch,
    signal_enabled,
)

__all__ = [
    "VoteEvent", "VOTE_UP", "VOTE_DOWN",
    "dispatch_vote", "schedule_vote_dispatch",
    "register_consumer", "registered_consumers", "signal_enabled",
]
