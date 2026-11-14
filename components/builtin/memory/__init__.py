"""Tier 2.2 — Managed Agent Memory (A7).

A named, governed, inspectable cross-session memory product. Recall is deterministic
and works across every IFOC engine; writes arrive via the explicit ``TDA_Memory`` tool,
opt-in background extraction, or manual REST/UI CRUD. Default-dormant (master switch OFF)
⇒ byte-identical baseline.

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md.
"""
from components.builtin.memory.types import (
    MemoryItem, MemoryConfig, MemoryKind, MemorySource, RecallResult,
)

__all__ = [
    "MemoryItem", "MemoryConfig", "MemoryKind", "MemorySource", "RecallResult",
]
