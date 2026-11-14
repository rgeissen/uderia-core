"""Per-turn tally for assurance points whose seam has no turn context.

WHY A CONTEXTVAR
----------------
Three of the registry's points fire at seams that know nothing about the turn they
serve: the preflight guard runs inside the MCP adapter (session id only), retrieval
hygiene runs inside `retrieve_examples` (no session at all), and the retrieval
firewall runs inside a context-window module. Threading a turn handle through all
three would mean touching every caller of every one of them — and the platform has
already solved this exact shape once, for the Agent Principal: set once at the turn
boundary, read at the chokepoints, propagating into awaited coroutines and child
tasks so concurrent turns stay isolated without any signature change.

WHAT IT IS NOT
--------------
Not a metrics sink and not a log. It holds only what a reader needs to answer *"did
this protection run on my answer, and did it do anything?"* — counts and the few
labels that make a count meaningful. It never holds retrieved text, matched values,
or PII: the privacy invariant that governs the guardrail events governs this too.

FAIL-OPEN, ALWAYS
-----------------
Every function swallows its own errors. A tally that breaks a turn would be a strictly
worse outcome than a turn with no tally — the counts exist to describe the answer, not
to gate it.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Dict, Optional

app_logger = logging.getLogger("quart.app")

#: Per-turn accumulator. `None` outside a turn — a gate firing there records nothing
#: rather than leaking into whichever turn happens to run next.
_TALLY: contextvars.ContextVar[Optional[Dict[str, Dict[str, Any]]]] = \
    contextvars.ContextVar("uderia_assurance_tally", default=None)

#: Which gates the live drain has already announced. A SEPARATE contextvar, deliberately:
#: keeping it inside the tally's own dicts would put a private bookkeeping key into every
#: stamp `snapshot()` copies — and those stamps are persisted on the turn and read by the
#: audit. Progress bookkeeping must not be able to reach the record.
_SEEN: contextvars.ContextVar[Optional[set]] = \
    contextvars.ContextVar("uderia_assurance_progress_seen", default=None)


def begin_turn() -> None:
    """Open a fresh tally for the turn about to run.

    Called at the one dispatch seam every engine passes through. Setting a NEW dict
    (rather than clearing the old one) matters: a still-running task from a previous
    turn keeps writing to its own dict and cannot contaminate this one.
    """
    try:
        _TALLY.set({})
        _SEEN.set(set())
    except Exception:  # noqa: BLE001
        pass


def end_turn() -> None:
    """Close the tally. A gate that fires after this records nothing, which is correct
    — it did not act on the answer that was delivered."""
    try:
        _TALLY.set(None)
    except Exception:  # noqa: BLE001
        pass


def active() -> bool:
    try:
        return _TALLY.get() is not None
    except Exception:  # noqa: BLE001
        return False


def mark_ran(gate: str) -> None:
    """Record that `gate` ran on this turn, before knowing whether it found anything.

    Separate from `record()` because "ran and found nothing" and "did not run" are the
    two states the trace could not previously tell apart — the whole point of P1.
    """
    try:
        t = _TALLY.get()
        if t is None:
            return
        t.setdefault(gate, {})["ran"] = True
    except Exception:  # noqa: BLE001
        pass


def record(gate: str, **fields: Any) -> None:
    """Accumulate findings for `gate`.

    Integers add (so a gate firing on several tool calls or several documents reports
    the total); lists extend, de-duplicated and bounded, because a reader needs a few
    examples and a persisted turn should not carry an unbounded one; anything else is
    last-write-wins (a mode, a ceiling — a constant, not a count).
    """
    try:
        t = _TALLY.get()
        if t is None:
            return
        slot = t.setdefault(gate, {})
        slot["ran"] = True
        for k, v in fields.items():
            if isinstance(v, bool):
                slot[k] = bool(slot.get(k)) or v
            elif isinstance(v, int):
                slot[k] = int(slot.get(k) or 0) + v
            elif isinstance(v, (list, tuple, set)):
                cur = list(slot.get(k) or [])
                for item in v:
                    s = str(item)
                    if s not in cur:
                        cur.append(s)
                slot[k] = cur[:12]   # enough to name; never a full dump
            else:
                slot[k] = v
    except Exception:  # noqa: BLE001
        pass


def stamp(gate: str) -> Optional[dict]:
    """This gate's turn stamp, or `None` if it never ran.

    `None` is honest here and reads as `unknown` in the registry — deliberately NOT
    as a pass. A gate that had no opportunity to run is not the same as one that ran
    and found nothing, and only `mark_ran`/`record` can say the latter.
    """
    try:
        t = _TALLY.get()
        if not t:
            return None
        slot = t.get(gate)
        if not slot:
            return None
        return dict(slot)
    except Exception:  # noqa: BLE001
        return None


def drain_new() -> list:
    """Gates recorded since the last drain — the LIVE half of the tally.

    The tally exists so the end-of-turn stamps can be collected; nothing read it until the
    turn was over, which is why a mechanism running mid-turn was invisible. This returns
    what is NEW and marks it seen, so a caller can emit progress without re-announcing.

    Why a drain and not an inference: the alternative is for the executor to reason "a tool
    call returned without a denial, therefore authority and preflight passed". That is false
    the moment a mechanism is switched off or swallows an exception — it would report
    progress that never happened, which is the false clean this whole grammar exists to
    prevent. This reports what the mechanism itself recorded, or nothing.
    """
    try:
        t = _TALLY.get()
        if not t:
            return []
        seen = _SEEN.get()
        if seen is None:
            seen = set()
            _SEEN.set(seen)
        out = []
        for gate, fields in t.items():
            if gate in seen:
                continue
            seen.add(gate)
            out.append({"gate": gate,
                        "ran": fields.get("ran", True) is not False,
                        "acted": bool(fields.get("acted"))})
        return out
    except Exception:  # noqa: BLE001 — progress must never break a turn
        return []


def snapshot() -> Dict[str, dict]:
    """Every gate's stamp for this turn, for the engines' turn assembly."""
    try:
        t = _TALLY.get()
        return {k: dict(v) for k, v in (t or {}).items()}
    except Exception:  # noqa: BLE001
        return {}
