"""
!extract extension — Structured data extraction.

Pulls numbers, percentages, and entity names from the LLM answer
using deterministic regex patterns. No LLM call required.

Tier: Simple (SimpleExtension) — only needs answer_text.

Usage:
    !extract              → all data types     {numbers, percentages, entities}
    !extract:numbers      → numbers only       {numbers:[{label,value,unit}]}
    !extract:percentages  → percentages only
    !extract:entities     → entity names only

  Scalar selectors (return ONE bare value — ideal for a flow decision that
  wants `extract > 10` instead of `extract['numbers'][0]['value'] > 10`):
    !extract:first        → first number
    !extract:last         → last number
    !extract:max          → largest number
    !extract:min          → smallest number
    !extract:sum          → sum of all numbers

  Custom regex capture (precise, deterministic — beats positional indexing):
    !extract:/(\\d+)\\s+products/   → the captured group, coerced to a number
                                     when numeric, else the matched string.
                                     None if no match.
"""

import re

from trusted_data_agent.extensions.base import SimpleExtension
from trusted_data_agent.extensions.helpers.regex import (
    NUMBER_WITH_UNIT,
    PERCENTAGE,
    IDENTIFIER,
    ENTITY_STOPWORDS,
)
from trusted_data_agent.extensions.helpers.numbers import (
    number_values,
    select_number,
    NUMBER_SELECTORS,
)

_TYPE_FILTERS = {"numbers", "percentages", "entities"}


def _is_regex_param(param):
    return bool(param) and len(param) >= 2 and param.startswith("/") and param.endswith("/")


class ExtractExtension(SimpleExtension):

    name = "extract"
    description = "Extracts numbers, percentages, entities — or one precise value via selector/regex"
    # Type filters + scalar selectors are validated against this set; a /regex/
    # param is validated separately in validate_param().
    allowed_params = _TYPE_FILTERS | NUMBER_SELECTORS

    def validate_param(self, param=None):
        if param is None or param in self.allowed_params:
            return True, None
        if _is_regex_param(param):
            try:
                re.compile(param[1:-1])
                return True, None
            except re.error as exc:
                return False, f"Invalid regex {param!r}: {exc}"
        valid = ", ".join(sorted(self.allowed_params))
        return False, (
            f"Invalid parameter {param!r}. Valid: {valid}, or a /regex/ pattern."
        )

    def transform(self, answer_text, param=None):
        # --- Custom regex capture — precise single value ---
        if _is_regex_param(param):
            return self._regex_capture(answer_text, param[1:-1])

        # --- Scalar selector — reduce all numbers to one bare value ---
        if param in NUMBER_SELECTORS:
            return select_number(number_values(answer_text), param)

        # --- Numbers (label-anchored, with unit) ---
        numbers = []
        seen_values = set()
        for match in NUMBER_WITH_UNIT.finditer(answer_text):
            label = match.group(1).strip()
            raw_value = match.group(2).replace(",", "")
            unit = (match.group(3) or "").strip()
            try:
                value = float(raw_value)
            except ValueError:
                continue
            key = (label.lower(), value)
            if key not in seen_values:
                seen_values.add(key)
                numbers.append({"label": label, "value": value, "unit": unit})

        # Fallback: include bare numbers the label-anchored pattern misses — e.g.
        # "3 * 5 is 15" or any arithmetic/terse answer with no letter-label before
        # the digits. Without this, !extract:numbers returns [] for those. Bare
        # numbers carry an empty label; values already captured WITH a label are
        # not duplicated.
        _labeled_values = {n["value"] for n in numbers}
        for _v in number_values(answer_text):
            if _v not in _labeled_values:
                numbers.append({"label": "", "value": _v, "unit": ""})

        # --- Percentages ---
        percentages = []
        for match in PERCENTAGE.finditer(answer_text):
            try:
                percentages.append(float(match.group(1)))
            except ValueError:
                continue
        percentages = sorted(set(percentages))

        # --- Entities ---
        entities = sorted(set(
            m.group(1)
            for m in IDENTIFIER.finditer(answer_text)
            if m.group(1) not in ENTITY_STOPWORDS
        ))

        full_output = {
            "numbers": numbers,
            "percentages": percentages,
            "entities": entities,
            "source_length": len(answer_text),
        }

        # Type filter (numbers / percentages / entities)
        if param and param in full_output:
            return {param: full_output[param]}
        return full_output

    @staticmethod
    def _regex_capture(answer_text, pattern):
        """Return the first capture group (or whole match) of `pattern`,
        coerced to a number when it looks numeric. None when no match."""
        match = re.search(pattern, answer_text)
        if not match:
            return None
        value = match.group(1) if match.groups() else match.group(0)
        if value is None:
            return None
        try:
            return float(value.replace(",", ""))
        except (ValueError, AttributeError):
            return value
