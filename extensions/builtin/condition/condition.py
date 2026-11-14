"""
!condition extension — Deterministic boolean test on the answer.

A free, reliable alternative to the LLM-backed !boolean_check for the common
case where the routing signal is a number threshold, a substring, or a regex
match. No LLM call → no cost, no latency, no "Not Applicable" surprises.

Returns a BARE boolean so a flow decision node reads `condition` directly.

Tier: Simple (SimpleExtension) — only needs answer_text.

Usage (param is REQUIRED — it is the rule to test):
    Numeric threshold — compares ONE number from the answer to a value.
    An optional selector picks which number (default: first):
        !condition:>10           → first number > 10
        !condition:max>=100      → largest number >= 100
        !condition:min<5         → smallest number < 5
        !condition:sum>=1000     → sum of numbers >= 1000
        !condition:last==0       → last number == 0
      operators: >  >=  <  <=  ==  !=  (= is treated as ==)
      selectors: first last max min sum

    Substring presence (case-insensitive):
        !condition:contains=error

    Regex presence:
        !condition:/out of stock/i
"""

import re
from typing import Optional

from trusted_data_agent.extensions.base import SimpleExtension
from trusted_data_agent.extensions.helpers.numbers import (
    number_values,
    select_number,
    NUMBER_SELECTORS,
)

# [selector] <op> <number>  e.g. "max >= 100", ">10", "last==0"
_THRESHOLD = re.compile(
    r"^\s*(first|last|max|min|sum)?\s*(>=|<=|==|!=|=|>|<)\s*(-?\d[\d,]*(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
_CONTAINS_PREFIX = "contains="


def _is_regex_param(param):
    # /pattern/ or /pattern/flags  (flags = trailing i/m/s letters)
    return bool(param) and param.startswith("/") and "/" in param[1:]


def _split_regex(param):
    """Split '/pattern/flags' → (pattern, re.flags). Supports i, m, s."""
    body = param[1:]
    idx = body.rfind("/")
    pattern, raw_flags = body[:idx], body[idx + 1:]
    flags = 0
    if "i" in raw_flags:
        flags |= re.IGNORECASE
    if "m" in raw_flags:
        flags |= re.MULTILINE
    if "s" in raw_flags:
        flags |= re.DOTALL
    return pattern, flags


class ConditionExtension(SimpleExtension):

    name = "condition"
    description = "Deterministic boolean test (threshold / contains / regex) — no LLM"
    allowed_params = None  # free-form rule; validated in validate_param()

    def validate_param(self, param: Optional[str] = None):
        if not param:
            return False, (
                "condition requires a rule, e.g. '>10', 'max>=100', "
                "'contains=error', or '/regex/'."
            )
        if _is_regex_param(param):
            pattern, _ = _split_regex(param)
            try:
                re.compile(pattern)
                return True, None
            except re.error as exc:
                return False, f"Invalid regex {param!r}: {exc}"
        if param.lower().startswith(_CONTAINS_PREFIX):
            return True, None
        if _THRESHOLD.match(param):
            return True, None
        return False, (
            f"Invalid condition {param!r}. Use a threshold ('>10', 'max>=100'), "
            f"'contains=TEXT', or '/regex/'."
        )

    def transform(self, answer_text, param=None):
        # Regex presence
        if _is_regex_param(param):
            pattern, flags = _split_regex(param)
            return re.search(pattern, answer_text, flags) is not None

        # Substring presence (case-insensitive)
        if param.lower().startswith(_CONTAINS_PREFIX):
            needle = param[len(_CONTAINS_PREFIX):].strip().lower()
            return needle in answer_text.lower()

        # Numeric threshold
        m = _THRESHOLD.match(param)
        if not m:
            return False
        selector = (m.group(1) or "first").lower()
        op = m.group(2)
        threshold = float(m.group(3).replace(",", ""))
        value = select_number(number_values(answer_text), selector)
        if value is None:
            return False
        return self._compare(value, op, threshold)

    @staticmethod
    def _compare(value, op, threshold):
        if op == ">":
            return value > threshold
        if op == ">=":
            return value >= threshold
        if op == "<":
            return value < threshold
        if op == "<=":
            return value <= threshold
        if op in ("==", "="):
            return value == threshold
        if op == "!=":
            return value != threshold
        return False
