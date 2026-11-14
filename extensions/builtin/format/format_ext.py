"""
!format extension — Deterministic template rendering.

Composes a string from the answer and the values extracted from it, with no
LLM call. Useful in a flow to turn prose into a clean label/message — e.g.
"There are 12 products below 20" + !format → "Reorder alert: 12 items".

Tier: Simple (SimpleExtension) — only needs answer_text.

Usage (param is the template; placeholders are substituted):
    !format:Reorder {n0} items

  Placeholders:
    {answer}            → the full answer text
    {n0} {n1} ...       → numbers by position (in document order)
    {nfirst} {nlast}    → first / last number
    {nmax} {nmin} {nsum}→ largest / smallest / sum of numbers
    {e0} {e1} ...       → UPPER_CASE entity names by position

  Unknown placeholders are left untouched (so typos are visible, not silently
  dropped). Integer-valued numbers render without a trailing ".0".
"""

import re

from trusted_data_agent.extensions.base import SimpleExtension
from trusted_data_agent.extensions.helpers.numbers import (
    number_values,
    select_number,
    entity_names,
    format_number,
)

_TOKEN = re.compile(r"\{([a-z0-9_]+)\}", re.IGNORECASE)
_NAMED_SELECTORS = {"first", "last", "max", "min", "sum"}


class FormatExtension(SimpleExtension):

    name = "format"
    description = "Render a template from the answer + extracted values — deterministic, no LLM"
    content_type = "text/plain"
    allowed_params = None  # the template is free-form

    def validate_param(self, param=None):
        if not param:
            return False, (
                "format requires a template, e.g. 'Reorder {n0} items' "
                "(placeholders: {answer}, {n0}, {nmax}, {nsum}, {e0} …)."
            )
        return True, None

    def transform(self, answer_text, param=None):
        nums = number_values(answer_text)
        ents = entity_names(answer_text)

        def repl(match):
            key = match.group(1).lower()
            if key == "answer":
                return answer_text
            if key.startswith("n"):
                rest = key[1:]
                if rest.isdigit():
                    i = int(rest)
                    return format_number(nums[i]) if i < len(nums) else match.group(0)
                if rest in _NAMED_SELECTORS:
                    v = select_number(nums, rest)
                    return format_number(v) if v is not None else match.group(0)
            if key.startswith("e") and key[1:].isdigit():
                i = int(key[1:])
                return ents[i] if i < len(ents) else match.group(0)
            return match.group(0)

        return _TOKEN.sub(repl, param)
