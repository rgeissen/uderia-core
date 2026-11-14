"""Marking figures and claims IN an answer's rendered HTML — text only, never inside a tag.

One tokenizer for every marker that wraps words of a delivered answer (the compute-lineage
figure marks, the grounding gate's unsupported-claim marks). A tag is read WITH its quoted
attribute values: a value may itself carry markup — a component's `data-spec` is JSON holding
the tile's HTML (`'{"content": "<table>…"}'`) — so the old guard "no `>` before the next `<`"
could not tell an attribute from text and wrapped a figure inside the JSON, breaking the
component (session 42eb166c turn 2: "Render error: Expected ',' or '}'").

`mask_tags` swaps every tag for a letters-only placeholder (a digit would read as a figure),
the caller wraps what is left — text — and `unmask` restores the tags byte for byte.
"""
from __future__ import annotations

import re
from typing import List, Tuple

TAG_RE = re.compile(r"""<(?:[^<>"']|"[^"]*"|'[^']*')*>""")
_TAG_SENTINEL = "\x01"


def _letters(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(97 + r) + out
    return out


def mask(pattern: "re.Pattern", html: str, sentinel: str) -> Tuple[str, List[str]]:
    kept: List[str] = []

    def _sub(m):
        kept.append(m.group(0))
        return f"{sentinel}{_letters(len(kept) - 1)}{sentinel}"
    return pattern.sub(_sub, html), kept


def unmask(html: str, kept: List[str], sentinel: str) -> str:
    def _idx(k: str) -> int:
        n = 0
        for ch in k:
            n = n * 26 + (ord(ch) - 96)
        return n - 1
    return re.sub(re.escape(sentinel) + r"([a-z]+)" + re.escape(sentinel), lambda m: kept[_idx(m.group(1))], html)


def mask_tags(html: str) -> Tuple[str, List[str]]:
    """Every tag (attributes and their values included) out of the way: what is left is text."""
    return mask(TAG_RE, html, _TAG_SENTINEL)


def unmask_tags(html: str, kept: List[str]) -> str:
    return unmask(html, kept, _TAG_SENTINEL)
