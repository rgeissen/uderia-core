"""Pure helpers shared by the warehouse-reading KG walkers (declared model, ANDP).

Values arrive off a database cursor in driver types; these coerce them for the graph.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Optional


def _s(value: Any) -> str:
    """Trim to a clean string; Teradata CHAR columns arrive space-padded."""
    if value is None:
        return ""
    return str(value).strip()


def _flag(value: Any) -> Optional[bool]:
    """Coerce a BYTEINT/str flag to bool.  Unknown → None (never a false 'no')."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = _s(value).lower()
    if text in ("1", "y", "yes", "true", "t"):
        return True
    if text in ("0", "n", "no", "false", "f"):
        return False
    return None


def _norm_type(raw: Any) -> str:
    """Normalize a declared type for comparison against the dictionary type.

    Compares only the *base* type name (``VARCHAR(2)`` → ``VARCHAR``); a length
    difference is not a meaning difference and would inflate the mismatch count.
    """
    text = _s(raw).upper()
    if not text:
        return ""
    # Drop every parenthesised precision/length, not only a trailing one — the
    # dictionary composes ``TIMESTAMP WITH TIME ZONE`` while a declaration reads
    # ``TIMESTAMP(6) WITH TIME ZONE``; splitting at the first "(" compared
    # ``TIMESTAMP`` against ``TIMESTAMP WITH TIME ZONE`` and counted a divergence
    # that is not one. The time-zone qualifier IS meaning, so it is kept.
    bare = re.sub(r"\([^)]*\)", " ", text).split()
    base = bare[0] if bare else ""
    if base and "WITH TIME ZONE" in text:
        base += " WITH TIME ZONE"
    return base


def _jsonable(value: Any) -> Any:
    """Coerce a DB-native value into something JSON can carry.

    Property values come straight off a database cursor, so they arrive as driver
    types — Teradata returns ``Decimal`` for numerics and ``date``/``datetime`` for
    temporals, neither of which ``json.dumps`` accepts.  The graph store serializes
    properties to JSON, so an unconverted value fails the whole import.

    Applied centrally (in ``add_entity`` / ``add_rel``) rather than at each call
    site: a per-field fix only protects the fields someone remembered.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        # int when exact, so a count reads as 20 rather than 20.0
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)
