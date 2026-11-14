"""Tier 1.3 P2 guardrails — value types.

Pure data shapes for the guardrail policy engine. No I/O, no platform deps, so both the
policy (``agent/guardrails/policy.py``) and its tests stay CI-pure. Mirrors the grounding
gate's type design (``agent/grounding/types.py``).

See docs/roadmap/TIER_1.3_GUARDRAILS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from trusted_data_agent.core import posture_modes as _modes


class Direction(str, Enum):
    INBOUND = "inbound"     # user text / tool results entering the model
    OUTBOUND = "outbound"   # the model's answer leaving to the user


class Category(str, Enum):
    PROMPT_INJECTION = "prompt_injection"   # inbound
    PII = "pii"                             # outbound
    TOXICITY = "toxicity"                   # outbound
    EXFIL_URL = "exfil_url"                 # outbound (RAG Trust Hardening Wave 3, X1)


class Action(str, Enum):
    PASS = "pass"          # nothing found / observe-only → answer untouched
    ANNOTATE = "annotate"  # finding(s) present; answer kept, note attached
    REDACT = "redact"      # PII masked in the answer (answer modified, delivered)
    BLOCK = "block"        # enforce + injection/toxicity hit → content withheld


# Valid modes, lowest→highest enforcement. Single source: core.posture_modes — a local
# copy is a weakening hazard (``review`` degraded to ``observe`` wherever a hand-rolled
# raw-mode guard was missing, e.g. the inbound direction).
MODES = _modes.LADDER


@dataclass(frozen=True)
class GuardrailConfig:
    """Resolved guardrail configuration for one direction of one turn — the profile
    ``guardrailConfig`` merged with the admin master switch by the adapter. Defaults
    reproduce a no-alteration baseline (``observe``: detect + emit, never change content)."""
    mode: str = "observe"
    check_injection: bool = True   # inbound
    check_pii: bool = True         # outbound
    check_toxicity: bool = True    # outbound
    check_exfil: bool = True       # outbound (exfil-shaped URLs, Wave 3 X1/X3)

    def normalized_mode(self) -> str:
        return _modes.normalize(self.mode or "observe", allowed=MODES,
                                context="guardrailConfig.mode")


@dataclass(frozen=True)
class Finding:
    """One detected issue. ``excerpt`` is a short, already-masked-safe snippet for display —
    never the raw PII value (PII excerpts carry only the label)."""
    category: Category
    label: str
    excerpt: str = ""


@dataclass(frozen=True)
class GuardrailVerdict:
    direction: Direction
    action: Action
    reason: str
    findings: Tuple[Finding, ...] = ()
    # When the action modified the content (REDACT) or withheld it (BLOCK), ``text`` is the
    # content to use downstream; ``None`` ⇒ caller keeps the original (PASS/ANNOTATE).
    text: Optional[str] = None
    annotation_md: Optional[str] = None
    mode: str = "observe"

    @property
    def blocked(self) -> bool:
        return self.action == Action.BLOCK

    @property
    def modified(self) -> bool:
        return self.action in (Action.REDACT, Action.BLOCK)

    @property
    def flagged(self) -> bool:
        return self.action != Action.PASS

    def categories(self) -> Tuple[str, ...]:
        return tuple(sorted({f.category.value for f in self.findings}))

    def to_event(self) -> dict:
        """Compact, JSON-safe payload for the ``guardrail`` SSE event + persisted turn record
        (plan reload / audit, charter #17). Carries labels/categories, never raw PII values."""
        return {
            "direction": self.direction.value,
            "action": self.action.value,
            "reason": self.reason,
            "mode": self.mode,
            "categories": list(self.categories()),
            "findings": [
                {"category": f.category.value, "label": f.label, "excerpt": f.excerpt}
                for f in self.findings
            ],
            "annotation_md": self.annotation_md,
        }


def passing(direction: Direction, mode: str = "observe", reason: str = "clean") -> GuardrailVerdict:
    """A clean PASS verdict (nothing found / gate off / abstain)."""
    return GuardrailVerdict(direction=direction, action=Action.PASS, reason=reason, mode=mode)
