"""One WARNING per (key, process) for a failure that would otherwise repeat per turn.

A channel that silently degrades to DEBUG can be dead for weeks (the KG semantic
seeding channel was, Sep 2026 — its failure was logged at DEBUG on every bridging turn
and nobody saw a line). A channel that logs WARNING on every turn is the opposite
failure: a broken embedder would write one line per question and train the operator to
grep past it. This helper is the middle: the FIRST occurrence per process is a WARNING
naming the cause; repeats go to DEBUG. Bounded — the seen-set is capped so a key that
carries an unbounded component (an id in the message) cannot grow it forever.
"""
from __future__ import annotations

import logging
import threading

_SEEN: set = set()
_LOCK = threading.Lock()
_CAP = 2048


def warn_once(logger: logging.Logger, key: str, message: str) -> bool:
    """WARNING the first time ``key`` is seen in this process, DEBUG afterwards.

    Returns True when the WARNING was emitted (tests pin the once-ness on this)."""
    with _LOCK:
        first = key not in _SEEN
        if first:
            if len(_SEEN) >= _CAP:
                _SEEN.clear()
            _SEEN.add(key)
    if first:
        logger.warning(message)
    else:
        logger.debug(message)
    return first


def _reset_for_tests() -> None:
    with _LOCK:
        _SEEN.clear()
