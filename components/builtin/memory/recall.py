"""Deterministic recall ranking + context formatting. Pure — no I/O, no LLM, no tiktoken.

Memory is ranked, never filtered, by lexical overlap with the query (a *booster*, since
preferences like "answer concisely" rarely match the query lexically) blended with
confidence, source trust, and recall history. Charter #20: this is fully deterministic;
the LLM is never asked which memories are relevant.

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.3.
"""
from __future__ import annotations

import math
import re
from typing import Callable, List, Optional

from components.builtin.memory.types import MemoryConfig, MemoryItem, RecallResult

_STOPWORDS = frozenset((
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "her", "was",
    "one", "our", "out", "use", "how", "what", "when", "who", "why", "this", "that",
    "with", "from", "your", "have", "has", "will", "show", "give", "tell", "please",
))
_SOURCE_WEIGHT = {"explicit": 1.0, "manual": 0.85, "learned": 0.7}


def _tokenize(text: str) -> set:
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def score_item(item: MemoryItem, query_tokens: set) -> float:
    """Deterministic relevance score in [0, 1]. Stable and reproducible."""
    content_tokens = _tokenize(item.content)
    if query_tokens and content_tokens:
        overlap = len(query_tokens & content_tokens) / float(len(query_tokens))
    else:
        overlap = 0.0
    conf = max(0.0, min(1.0, float(item.confidence)))
    source_w = _SOURCE_WEIGHT.get(item.source, 0.7)
    recall_factor = 1.0 - 1.0 / (1.0 + max(0, int(item.recall_count)))
    return 0.50 * overlap + 0.25 * conf + 0.15 * source_w + 0.10 * recall_factor


def rank(
    candidates: List[MemoryItem],
    query: str,
    config: MemoryConfig,
    budget_tokens: int,
    token_estimator: Optional[Callable[[str], int]] = None,
) -> RecallResult:
    """Rank active candidates and return as many as fit ``config.max_items`` and the token
    budget. Ties broken deterministically by ``updated_at`` desc then ``id`` so output is
    reproducible (charter #20 / #15)."""
    est = token_estimator or _estimate_tokens
    active = [c for c in candidates if c.active]
    qtok = _tokenize(query)

    scored = sorted(
        active,
        key=lambda it: (
            round(score_item(it, qtok), 6),
            it.updated_at or "",
            it.id or "",
        ),
        reverse=True,
    )

    chosen: List[MemoryItem] = []
    used = 0
    for it in scored:
        if len(chosen) >= max(1, config.max_items):
            break
        cost = est(f"- {it.content}")
        if chosen and used + cost > budget_tokens:
            break
        chosen.append(it)
        used += cost
    return RecallResult(items=chosen, considered=len(active), scope=config.scope)


def format_block(result: RecallResult) -> str:
    """Render recalled items into a compact context block. Empty result ⇒ empty string."""
    if not result.items:
        return ""
    lines = ["[Remembered about this user]"]
    for it in result.items:
        lines.append(f"- {it.content}")
    return "\n".join(lines)
