"""Vote consumer — stop recalling facts learned from a rejected answer.

The learned extractor distils durable facts about the user out of a turn. When the
user then rejects that turn, those facts were inferred from an answer they disagreed
with, and recalling them into every future session propagates the mistake — quietly,
and for as long as the memory lives.

Applies to every engine (the extractor runs behind them all).

Two boundaries make this safe:
  * **Deactivate, never delete** (R4). The row stays visible and re-activatable in
    the Memory manager, so a user can see what their downvote paused and undo it.
  * **``learned`` only.** A fact the user explicitly asked the agent to remember, or
    typed in themselves, is not the agent's inference — a downvote on the turn it
    happened in must not erase it. Only machine-inferred facts are touched.

Reversible (R5): retracting or flipping a downvote re-activates exactly what it paused.
"""
from __future__ import annotations

import asyncio
import logging

from trusted_data_agent.agent.feedback.dispatcher import register_consumer, signal_enabled
from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")

LEARNED = "learned"


def desired_active(event: VoteEvent) -> bool | None:
    """Should this vote pause the turn's learned memories, resume them, or neither?

    Pure — the whole rule, so the reversibility contract is testable without a DB:
      * a downvote pauses them;
      * clearing or flipping AWAY from a downvote resumes them;
      * a plain upvote does nothing (there is no "force-learn" here — an upvote is
        not a request to remember, and treating it as one would put facts the user
        never asked for into every future session).
    """
    if event.is_down:
        return False
    if event.was_down:      # down → up, or down → cleared
        return True
    return None


@register_consumer("memory")
async def memory(event: VoteEvent) -> None:
    if not signal_enabled("memory_deactivate"):
        return
    active = desired_active(event)
    if active is None:
        return

    from components.builtin.memory.store import MemoryStore

    changed = await asyncio.to_thread(
        MemoryStore().set_active_by_source,
        event.user_uuid, session_id=event.session_id, turn_id=event.turn_id,
        source=LEARNED, active=active)
    if changed:
        app_logger.info(
            f"[feedback] {'resumed' if active else 'paused'} {changed} learned "
            f"memory item(s) from {event.session_id}/turn {event.turn_id}")
