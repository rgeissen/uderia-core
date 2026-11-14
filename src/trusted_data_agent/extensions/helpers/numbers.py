"""
Numeric extraction + selection helpers for deterministic extensions.

Shared by !extract (scalar selectors + regex capture), !condition (threshold
checks), and !format (number placeholders) so a flow author gets the SAME
value regardless of which one they reach for. All deterministic — no LLM.
"""
from __future__ import annotations

import re
from typing import List, Optional

from trusted_data_agent.extensions.helpers.regex import (
    NUMBER_WITH_UNIT,
    IDENTIFIER,
    ENTITY_STOPWORDS,
)

# Bare-number fallback — catches values NUMBER_WITH_UNIT misses because they
# carry no label/separator (e.g. "... below 20"). Thousands-grouped form is
# tried first so "1,234" matches whole rather than as "1".
_PLAIN_NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")

NUMBER_SELECTORS = {"first", "last", "max", "min", "sum"}


def labeled_values(text: str) -> List[float]:
    """Numeric values from the label-anchored NUMBER_WITH_UNIT pattern — the
    same set !extract:numbers exposes — de-duplicated, in document order."""
    vals: List[float] = []
    seen = set()
    for m in NUMBER_WITH_UNIT.finditer(text):
        raw = m.group(2).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        key = (m.group(1).strip().lower(), v)
        if key not in seen:
            seen.add(key)
            vals.append(v)
    return vals


def number_values(text: str) -> List[float]:
    """EVERY number in the answer, in document order. Used by scalar selectors,
    !condition, and !format — these want the full set (e.g. both the count and
    the threshold in "12 products below 20"), not just the label-anchored ones
    that NUMBER_WITH_UNIT finds. For precision over a specific number, use a
    /regex/ capture instead."""
    out: List[float] = []
    for m in _PLAIN_NUMBER.finditer(text):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def select_number(values: List[float], selector: str) -> Optional[float]:
    """Reduce a list of numbers to one via a named selector. None if empty."""
    if not values:
        return None
    if selector == "first":
        return values[0]
    if selector == "last":
        return values[-1]
    if selector == "max":
        return max(values)
    if selector == "min":
        return min(values)
    if selector == "sum":
        return sum(values)
    return None


def entity_names(text: str) -> List[str]:
    """UPPER_CASE identifier names (same set !extract:entities exposes)."""
    return sorted(set(
        m.group(1)
        for m in IDENTIFIER.finditer(text)
        if m.group(1) not in ENTITY_STOPWORDS
    ))


def format_number(v: float) -> str:
    """Render a float as an int when it has no fractional part (12, not 12.0)."""
    return str(int(v)) if float(v).is_integer() else str(v)
