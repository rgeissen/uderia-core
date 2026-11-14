"""Corpus-grounding benchmark — dataset loader + validator (pure).

Loads a committed curated case set (``dataset/<name>.json``) and validates its structure so
a malformed dataset fails loudly (CI) rather than silently skewing the published numbers.

Each case:
  {
    "case_id":  "sup-01",
    "class":    "supported" | "unsupported" | "second_chance",
    "question": "the user's question (whitelisted like the chunks)",
    "answer":   "the assistant's answer to score",
    "chunks":   ["retrieved chunk text", ...],
    "second_chance": ["chunk text a targeted re-search would return", ...]  # class second_chance only
  }
"""
from __future__ import annotations

import json
import os

from trusted_data_agent.eval.benchmarks.corpus_grounding.report import CASE_CLASSES

_DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
_REQUIRED = ("case_id", "class", "answer")


def dataset_path(name: str) -> str:
    return os.path.join(_DATASET_DIR, f"{name}.json")


def load_dataset(name_or_path: str = "corpus_v1") -> dict:
    path = (name_or_path if (os.path.isabs(name_or_path) or os.path.exists(name_or_path))
            else dataset_path(name_or_path))
    with open(path) as f:
        data = json.load(f)
    validate_dataset(data)
    return data


def validate_dataset(data: dict) -> bool:
    """Raise AssertionError on any structural problem; return True when valid."""
    assert isinstance(data, dict) and isinstance(data.get("cases"), list) and data["cases"], \
        "dataset must be a dict with a non-empty 'cases' list"
    seen = set()
    for c in data["cases"]:
        for k in _REQUIRED:
            assert isinstance(c.get(k), str) and c[k].strip(), \
                f"case missing required field {k!r}: {c.get('case_id', c)}"
        assert c["class"] in CASE_CLASSES, \
            f"case {c['case_id']}: invalid class {c['class']!r} (one of {CASE_CLASSES})"
        assert c["case_id"] not in seen, f"duplicate case_id {c['case_id']!r}"
        assert isinstance(c.get("chunks", []), list), f"case {c['case_id']}: chunks must be a list"
        if c["class"] == "second_chance":
            assert c.get("second_chance"), \
                f"case {c['case_id']}: second_chance class requires a non-empty 'second_chance' list"
        seen.add(c["case_id"])
    return True


def class_counts(data: dict) -> dict:
    counts = {cls: 0 for cls in CASE_CLASSES}
    for c in data["cases"]:
        counts[c["class"]] += 1
    return counts
