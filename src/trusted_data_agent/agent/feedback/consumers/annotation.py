"""Vote consumer — the human label the eval layer can read.

A vote is free human ground truth on an already-instrumented turn. Recording it as
an ``eval_annotations`` row makes it available to judge calibration (Cohen's κ needs
(human, machine) pairs) and to threshold calibration — a turn every deterministic
scorer graded HEALTHY that the user downvoted is the loudest possible signal that
the quality bands are miscalibrated for this deployment.

Applies to EVERY engine and every transition, including a cleared vote (a retraction
is itself a label, and the row is what makes it visible).

Append-only by design: a flip writes a NEW row rather than mutating the old one, so
the sequence of what the user believed over time is preserved. Readers take the
latest row per (session, turn).

``judge_passed`` freezes the ruling the human SAW at the moment they voted — never
re-derived later, because a re-score could flip the verdict and silently rewrite
what the human was reacting to. No score on screen ⇒ ``None``, which is the honest
value, not a pass.
"""
from __future__ import annotations

import asyncio
import logging

from trusted_data_agent.agent.feedback.dispatcher import register_consumer, signal_enabled
from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")

# One label per transition. The reader keys on these exact strings.
LABEL_UP = "user_upvote"
LABEL_DOWN = "user_downvote"
LABEL_CLEARED = "user_vote_cleared"
VOTE_LABELS = frozenset({LABEL_UP, LABEL_DOWN, LABEL_CLEARED})

# Suffix marking a vote that reached this turn by genie propagation rather than by
# the user clicking on THIS turn. Same human judgement, one hop removed — recorded
# so calibration can weight or exclude it rather than mistaking it for a direct label.
PROPAGATED_SUFFIX = ":propagated"


def label_for(vote) -> str:
    """The annotation label for a vote value. Pure."""
    if vote == "up":
        return LABEL_UP
    if vote == "down":
        return LABEL_DOWN
    return LABEL_CLEARED


def reviewer_for(user_uuid: str) -> str:
    """The annotation's reviewer key. Carries the FULL uuid — it is the scoping
    filter every read uses, and a truncation would make one user's votes matchable
    against another's."""
    return f"user:{user_uuid}"


def vote_from_label(label) -> str | None:
    """Inverse of :func:`label_for` — the vote a stored label represents, or None
    for a cleared vote / a non-vote annotation. Pure; the read side's one decoder."""
    base = str(label or "").split(":")[0] if label else ""
    if base == LABEL_UP:
        return "up"
    if base == LABEL_DOWN:
        return "down"
    return None


def is_vote_label(label) -> bool:
    return str(label or "").split(":")[0] in VOTE_LABELS


def latest_votes(user_uuid: str, limit: int = 2000) -> dict:
    """``{(session_id, str(turn)): "up"|"down"}`` — ONE user's current vote per turn.

    The read side of this consumer, and deliberately in the same module as the
    writer: the label vocabulary is encoded and decoded in one place, so the two
    cannot drift. Strictly scoped to the given user (R8) — a vote is that user's
    own preference and is never readable as another user's.

    Rows arrive newest-first, so the first row seen per turn is the current state;
    a cleared vote yields NO key (absent, never a stale up/down).
    """
    if not user_uuid:
        return {}
    try:
        from trusted_data_agent.eval.store import get_eval_store
        rows = get_eval_store().list_annotations(
            limit=limit, reviewer=reviewer_for(user_uuid))
    except Exception as e:  # noqa: BLE001 — a read failure degrades to "no votes"
        app_logger.warning(f"[feedback] vote read failed: {e}")
        return {}
    out: dict = {}
    seen: set = set()
    for row in rows:
        if not is_vote_label(row.get("label")):
            continue
        key = (row.get("session_id"), str(row.get("turn")))
        if key in seen:
            continue
        seen.add(key)
        vote = vote_from_label(row.get("label"))
        if vote:
            out[key] = vote
    return out


def _record(event: VoteEvent) -> None:
    """Blocking: sink read + SQLite insert. Runs in a worker thread."""
    from trusted_data_agent.eval.sink import find_turn_span, span_passed
    from trusted_data_agent.eval.store import get_eval_store

    span = find_turn_span(event.session_id, event.turn_id, user_uuid=event.user_uuid)
    judge_passed = span_passed(span)

    label = label_for(event.vote)
    if event.propagated:
        label += PROPAGATED_SUFFIX

    note = f"conversation panel vote on {event.profile_type or 'unknown'} turn"
    if event.depth:
        note += f" (propagated, depth={event.depth})"

    get_eval_store().add_annotation(
        reviewer=reviewer_for(event.user_uuid),
        label=label,
        note=note,
        session_id=event.session_id,
        turn=int(event.turn_id),
        judge_passed=judge_passed,
    )


@register_consumer("annotation")
async def annotation(event: VoteEvent) -> None:
    if not signal_enabled("annotate_eval"):
        return
    # Both the sink read and the store write are synchronous SQLite/filesystem work.
    # They go to a thread so the dispatcher never blocks the event loop (EvalStore
    # opens via sqlite_util, which serialises under the process-wide lock).
    await asyncio.to_thread(_record, event)
    app_logger.debug(
        f"[feedback] annotated {event.session_id}/turn {event.turn_id} as {event.vote}")
