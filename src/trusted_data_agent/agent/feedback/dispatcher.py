"""Vote signal — the ONE dispatcher.

Every consequence of a user's upvote/downvote flows through here. There is exactly
one wire point into it (``session_manager.update_turn_feedback``); no engine, route
or store may implement vote logic of its own. Same one-seam discipline as
``assurance/inflight.augment`` and ``guardrails.runtime.scan_outbound`` — it is what
keeps a consumer's gate and its effect from drifting apart.

Contract (see docs/roadmap/VOTE_SIGNAL_PLAN.md §3.3):
  * Consumers run SEQUENTIALLY in registration order, each wrapped individually —
    one failing consumer never stops the others.
  * ``dispatch_vote`` NEVER raises. A vote is user feedback on an already-delivered
    answer; nothing here may surface as an error to the user.
  * Master-gated on ``APP_CONFIG.FEEDBACK_SIGNALS['enabled']`` — off means zero
    consumer calls, i.e. the pre-feature baseline.
  * Propagated votes (genie) stop at ``MAX_DEPTH``; past it only the depth-safe
    consumers (recording, never acting) still run.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")

# Fallback used when APP_CONFIG carries no FEEDBACK_SIGNALS block (older config, or a
# test importing this module standalone). core/config.py is the authoritative copy;
# this mirror exists so the dispatcher is importable and safe without the app.
DEFAULT_SIGNALS = {
    "enabled": True,
    "annotate_eval": True,
    "champion_upvote": True,
    "genie_propagation": True,
    "retrieval_feedback": True,
    "memory_deactivate": True,
    "judge_on_downvote": False,
}

# Registration order IS execution order (see consumers/__init__.py).
_CONSUMERS: list[tuple[str, Callable[[VoteEvent], Awaitable[None]]]] = []

# Consumers that only RECORD (never act on a store) and are therefore still safe to
# run on a deeply-propagated vote. Everything else stops at MAX_DEPTH.
_DEPTH_SAFE_CONSUMERS = frozenset({"annotation"})
MAX_DEPTH = 2

# Strong refs for scheduled dispatch tasks — a bare create_task may be GC'd mid-flight
# (the documented hazard `_dispatch_online_eval` guards against the same way).
_DISPATCH_TASKS: set = set()

_consumers_loaded = False


def register_consumer(name: str):
    """Register a vote consumer. Idempotent per name: re-registering (module reload)
    REPLACES in place, preserving execution order."""
    def _deco(fn: Callable[[VoteEvent], Awaitable[None]]):
        for i, (existing, _) in enumerate(_CONSUMERS):
            if existing == name:
                _CONSUMERS[i] = (name, fn)
                return fn
        _CONSUMERS.append((name, fn))
        return fn
    return _deco


def registered_consumers() -> list[str]:
    """Consumer names in execution order (introspection / tests)."""
    return [name for name, _ in _CONSUMERS]


def signal_enabled(key: str) -> bool:
    """Read one FEEDBACK_SIGNALS flag. Defensive: an absent block, an absent key or a
    malformed value degrades to the shipped default rather than raising — a config
    typo must never silently disable a protection nor crash a vote."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        block = getattr(APP_CONFIG, "FEEDBACK_SIGNALS", None)
        if isinstance(block, dict) and key in block:
            return bool(block[key])
    except Exception:  # noqa: BLE001 — config access must never break a vote
        pass
    return bool(DEFAULT_SIGNALS.get(key, False))


def _ensure_consumers_loaded() -> None:
    """Import the consumer package on first dispatch. Lazy so importing the dispatcher
    never drags in the app (and never risks an import cycle)."""
    global _consumers_loaded
    if _consumers_loaded:
        return
    _consumers_loaded = True
    try:
        import trusted_data_agent.agent.feedback.consumers  # noqa: F401
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"[feedback] consumer import failed: {e}", exc_info=True)


async def dispatch_vote(event: VoteEvent) -> None:
    """Run every applicable consumer for this vote. NEVER raises."""
    try:
        if not signal_enabled("enabled"):
            return
        _ensure_consumers_loaded()
        deep = event.depth >= MAX_DEPTH
        for name, fn in list(_CONSUMERS):
            if deep and name not in _DEPTH_SAFE_CONSUMERS:
                continue
            try:
                await fn(event)
            except Exception as e:  # noqa: BLE001 — isolate each consumer
                app_logger.warning(
                    f"[feedback] consumer '{name}' failed for "
                    f"{event.session_id}/turn {event.turn_id}: {e}", exc_info=True)
    except Exception as e:  # noqa: BLE001 — the dispatcher itself must never raise
        app_logger.error(f"[feedback] dispatch failed: {e}", exc_info=True)


def schedule_vote_dispatch(event: VoteEvent) -> Optional[asyncio.Task]:
    """Fire-and-forget the dispatch so the vote's HTTP response never waits on a
    consumer. Strong-refs the task and discards it on completion. Returns the task
    (tests assert it is tracked); returns None when no loop is running."""
    try:
        task = asyncio.ensure_future(dispatch_vote(event))
        _DISPATCH_TASKS.add(task)
        task.add_done_callback(_DISPATCH_TASKS.discard)
        return task
    except Exception as e:  # noqa: BLE001 — scheduling must never break the vote
        app_logger.error(f"[feedback] vote dispatch failed to schedule: {e}", exc_info=True)
        return None
