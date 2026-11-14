"""
The terminal / interrupted split — ONE rule for every reader of a turn's or a task's status.

PROP-011 (needs input) adds a state that is neither an answer nor a failure: a turn that
**stopped to ask** the person something (`input_required`; `auth_required` reserved for the
connector flows of phase 3). A2A v1.0 calls these *interrupted* states and keeps them apart
from the *terminal* ones; this module is that split for Uderia.

Two vocabularies are in use and both are kept (nothing is renamed):

  * a TURN RECORD (`workflow_history[i]["status"]`) says `success · failed · error · cancelled`
  * a BACKGROUND TASK (`APP_STATE["background_tasks"][id]["status"]`) says
    `pending · processing · complete · error · cancelled` (+ `completed · failed` on two older
    routes)

Before this module, every poller held its own tuple of terminal strings (`("complete",
"failed", "error")`, `{"complete","error","cancelled"}`, …) and a new state would have made
each of them wait forever. A reader asks ONE question here — `is_settled` (nothing more will
happen to this record without a person), `is_terminal`, `is_interrupted`, `is_failure` — and
never compares against a string list of its own. The gate greps for such lists.
"""

from __future__ import annotations

from typing import Any, FrozenSet

#: A person is needed before the record moves again. Resumable; never a failure.
INTERRUPTED: FrozenSet[str] = frozenset({"input_required", "auth_required"})

#: Turn-record outcomes that are final.
TURN_TERMINAL: FrozenSet[str] = frozenset({"success", "failed", "error", "cancelled"})

#: Background-task outcomes that are final (every spelling the routes have used).
TASK_TERMINAL: FrozenSet[str] = frozenset({"complete", "completed", "success", "failed", "error", "cancelled"})

#: Outcomes a reader counts as a failed turn (a turn that stopped to ask is NOT one).
FAILURE: FrozenSet[str] = frozenset({"failed", "error"})

#: A task still being worked on.
TASK_ACTIVE: FrozenSet[str] = frozenset({"pending", "processing", "cancelling"})


def _s(status: Any) -> str:
    return str(status or "").strip().lower()


def is_interrupted(status: Any) -> bool:
    """The record waits for a person (`input_required`, `auth_required`)."""
    return _s(status) in INTERRUPTED


def is_terminal(status: Any) -> bool:
    """Final, in either vocabulary."""
    return _s(status) in TURN_TERMINAL or _s(status) in TASK_TERMINAL


def is_settled(status: Any) -> bool:
    """Nothing more happens to this record on its own: terminal OR interrupted. THE question
    a poller asks — a loop that waits on `is_terminal` alone waits forever on a question."""
    return is_terminal(status) or is_interrupted(status)


def is_failure(status: Any) -> bool:
    """A turn or task that failed. An interrupted record is not a failure, and neither is a
    cancelled one (a person stopped it)."""
    return _s(status) in FAILURE


def is_success(status: Any) -> bool:
    """A turn or task that produced its answer."""
    return _s(status) in ("success", "complete", "completed")


def is_active(status: Any) -> bool:
    """A background task still being worked on."""
    return _s(status) in TASK_ACTIVE
