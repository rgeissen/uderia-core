"""Pure data types for the agent-memory subsystem. No I/O, no LLM — safe to import anywhere.

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# --- Vocabularies (deterministic taxonomies; never free-form) -------------------------

KINDS = ("preference", "fact", "style")
SOURCES = ("explicit", "learned", "manual")
MODES = ("off", "recall", "recall_write")
SCOPES = ("profile", "global")


class MemoryKind:
    PREFERENCE = "preference"
    FACT = "fact"
    STYLE = "style"


class MemorySource:
    EXPLICIT = "explicit"   # agent stored it because the user asked ("remember …")
    LEARNED = "learned"     # background extraction inferred it
    MANUAL = "manual"       # user added it directly via the Memory panel / REST


def normalize_kind(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in KINDS else MemoryKind.FACT


# Deterministic kind inference (intelligent-component principle #1): the component
# classifies kind from the content so the LLM doesn't have to — it only supplies content.
import re as _re

_STYLE_RE = _re.compile(
    r"\b(concise|brief|terse|verbose|detailed|bullet|bullets|format|tone|spelling|"
    r"markdown|table|step[- ]?by[- ]?step|wording|style)\b", _re.I)
_PREF_RE = _re.compile(
    r"\b(prefer|prefers|always|never|like|likes|want|wants|favou?rite|default to|"
    r"metric|imperial|units?|currency|timezone|language)\b", _re.I)


def infer_kind(content: str) -> str:
    """Classify a memory's kind deterministically from its content. Style cues win over
    preference cues; everything else is a fact."""
    text = content or ""
    if _STYLE_RE.search(text):
        return MemoryKind.STYLE
    if _PREF_RE.search(text):
        return MemoryKind.PREFERENCE
    return MemoryKind.FACT


def resolve_kind(provided: Optional[str], content: str) -> str:
    """Honour an explicit valid kind; otherwise infer it from the content."""
    v = (provided or "").strip().lower()
    return v if v in KINDS else infer_kind(content)


# Deterministic global-scope intent detection (intelligent-component principle #1):
# small/unreliable models often ignore an explicit `scope` arg even when the user clearly
# asked to remember something globally. The component reads the user's request and forces
# global scope on unambiguous cross-agent phrasing — so "global" never silently goes local.
_GLOBAL_SCOPE_RE = _re.compile(
    r"\bglobal(ly)?\b\s*(agent\s+)?(memor|scope)"   # "global memory", "global agent memory"
    r"|\bglobally\b"                                  # "remember … globally"
    r"|\b(all|every)\s+(my\s+|the\s+)?agents?\b"      # "all my agents", "every agent"
    r"|\bshared\s+(agent\s+)?memor"                    # "shared memory"
    r"|\bacross\s+(all\s+)?(my\s+)?agents?\b",          # "across all agents"
    _re.I)


def detect_global_scope(user_text: Optional[str]) -> bool:
    """True if the user's request explicitly asks to remember globally / for all agents."""
    return bool(user_text and _GLOBAL_SCOPE_RE.search(user_text))


def normalize_source(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in SOURCES else MemorySource.MANUAL


@dataclass
class MemoryItem:
    """One durable memory. ``id``/timestamps are assigned by the store on persist."""
    content: str
    kind: str = MemoryKind.FACT
    source: str = MemorySource.MANUAL
    mem_key: Optional[str] = None          # normalized key for dedup/update-in-place
    profile_id: Optional[str] = None        # None = user-global; set = profile-scoped
    confidence: float = 1.0
    active: bool = True
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    id: Optional[str] = None
    recall_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_recalled_at: Optional[str] = None

    def __post_init__(self):
        self.kind = normalize_kind(self.kind)
        self.source = normalize_source(self.source)
        try:
            self.confidence = max(0.0, min(1.0, float(self.confidence)))
        except (TypeError, ValueError):
            self.confidence = 1.0
        if self.mem_key:
            self.mem_key = str(self.mem_key).strip().lower()[:120] or None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "content": self.content, "kind": self.kind,
            "source": self.source, "mem_key": self.mem_key, "profile_id": self.profile_id,
            "confidence": self.confidence, "active": self.active,
            "session_id": self.session_id, "turn_id": self.turn_id,
            "recall_count": self.recall_count, "created_at": self.created_at,
            "updated_at": self.updated_at, "last_recalled_at": self.last_recalled_at,
        }


@dataclass
class MemoryConfig:
    """Resolved per-profile memory config (admin master switch already folded in)."""
    mode: str = "off"            # off | recall | recall_write
    scope: str = "profile"       # profile | global  (which items to recall)
    max_items: int = 8           # cap on items injected per turn

    @property
    def recall_enabled(self) -> bool:
        return self.mode in ("recall", "recall_write")

    @property
    def extraction_enabled(self) -> bool:
        return self.mode == "recall_write"


@dataclass
class RecallResult:
    """Output of deterministic recall ranking."""
    items: List[MemoryItem] = field(default_factory=list)
    considered: int = 0          # candidates examined
    scope: str = "profile"

    @property
    def count(self) -> int:
        return len(self.items)
