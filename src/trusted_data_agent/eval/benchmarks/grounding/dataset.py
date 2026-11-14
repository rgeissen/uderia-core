"""Tier 1.4 — benchmark dataset loader + validator (pure).

Loads a committed curated query set (``dataset/<kg>.json``) and validates its structure
so a malformed dataset fails loudly (CI) rather than silently skewing the published
numbers. Used by the harness (live run) and by the dataset test.

See docs/roadmap/TIER_1.4_BENCHMARK.md §3.
"""
from __future__ import annotations

import json
import os

from trusted_data_agent.eval.benchmarks.grounding.report import CASE_CLASSES

_DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
_REQUIRED_CASE_FIELDS = ("case_id", "class", "query")


def dataset_path(name: str) -> str:
    return os.path.join(_DATASET_DIR, f"{name}.json")


def load_dataset(name_or_path: str) -> dict:
    """Load a dataset by KG name (``"fitness_db"``) or explicit path. Validates structure."""
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
    assert isinstance(data.get("scope"), list) and data["scope"], \
        "dataset must declare a non-empty 'scope' (the KG-declared deployed databases)"
    seen = set()
    for c in data["cases"]:
        for k in _REQUIRED_CASE_FIELDS:
            assert isinstance(c.get(k), str) and c[k].strip(), \
                f"case is missing required field {k!r}: {c.get('case_id', c)}"
        assert c["class"] in CASE_CLASSES, \
            f"case {c['case_id']}: invalid class {c['class']!r} (must be one of {CASE_CLASSES})"
        assert c["case_id"] not in seen, f"duplicate case_id {c['case_id']!r}"
        seen.add(c["case_id"])
    return True


def class_counts(data: dict) -> dict:
    counts = {cls: 0 for cls in CASE_CLASSES}
    for c in data["cases"]:
        counts[c["class"]] += 1
    return counts
