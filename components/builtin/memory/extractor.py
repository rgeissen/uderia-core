"""Background learned-extraction — the one place memory uses an LLM (charter #20).

Pure helpers only: build the extraction prompt and parse the model's JSON into candidate
``MemoryItem``s. The LLM call + cost accounting live in ``runtime.extract_and_store`` so this
stays unit-testable with a mocked model. Opt-in (``memoryConfig.mode == recall_write``).

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.4.
"""
from __future__ import annotations

from typing import List

from components.builtin.memory.types import KINDS, MemoryItem, MemorySource, infer_kind

_MAX_CANDIDATES = 3


def build_extraction_prompt(user_input: str, answer_text: str) -> str:
    """Prompt the model to surface ONLY durable, user-specific memories from one exchange."""
    answer_snippet = (answer_text or "")[:600]
    return (
        "You extract DURABLE memory about the USER from one turn of a data/analytics "
        "conversation — facts that should persist into FUTURE sessions.\n\n"
        "Store ONLY: stable user preferences (answer style, units, format), durable facts "
        "about the user or their work (role, primary database/domain, recurring goals).\n"
        "Do NOT store: this turn's specific question, the answer's data/numbers, anything "
        "transient or one-off, anything not about the user.\n\n"
        f"User said: {user_input}\n"
        f"Assistant answered (excerpt): {answer_snippet}\n\n"
        f"Return ONLY a JSON array of at most {_MAX_CANDIDATES} items, each "
        '{"content": "<short 1st-person fact>", "kind": "preference|fact|style", '
        '"mem_key": "<stable_snake_key>", "confidence": 0.0-1.0}. '
        'If there is nothing durable, return []. No prose.'
    )


def parse_candidates(llm_text: str) -> List[MemoryItem]:
    """Parse the model's response into learned ``MemoryItem``s. Tolerant — returns [] on junk."""
    from trusted_data_agent.agent.json_utils import robust_json_parse

    parsed = robust_json_parse(llm_text)
    if not isinstance(parsed, list):
        return []
    items: List[MemoryItem] = []
    for entry in parsed[:_MAX_CANDIDATES]:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content or len(content) > 400:
            continue
        kind = entry.get("kind")
        if kind not in KINDS:
            kind = infer_kind(content)
        items.append(MemoryItem(
            content=content,
            kind=kind,
            mem_key=(entry.get("mem_key") or None),
            source=MemorySource.LEARNED,
            confidence=entry.get("confidence", 0.7),
        ))
    return items
