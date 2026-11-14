"""Tier 1.1 grounding gate — value types.

Pure data shapes for the synchronous grounding gate. No I/O, no platform deps, so
both the gate (``agent/grounding/gate.py``) and its tests stay CI-pure.
See docs/roadmap/TIER_1.1_GROUNDING_GATE.md (§5).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from trusted_data_agent.core import posture_modes as _modes


class Decision(str, Enum):
    PASS = "pass"          # in scope / nothing to check / abstain / observe-only
    ANNOTATE = "annotate"  # out-of-scope or ungrounded numbers; answer kept, note attached
    BLOCK = "block"        # enforce + scope breach beyond threshold; claim withheld


# Valid gate modes, lowest→highest enforcement. Single source: core.posture_modes —
# see that module for why a local copy is a weakening hazard (``review`` degraded to
# ``observe`` on any path lacking a hand-rolled raw-mode guard).
MODES = _modes.LADDER


@dataclass(frozen=True)
class GroundingConfig:
    """Resolved gate configuration for a turn — the profile ``groundingConfig`` merged
    with admin thresholds by the adapter (increment 3). Defaults reproduce today's
    behaviour (``observe``: measure, never alter the answer)."""
    mode: str = "observe"
    # NB: after the benchmark redesign the live GroundingGate no longer blocks deterministically on
    # these two — the judge makes the block/pass call. They are retained because the eval-side
    # `scope_adherence` scorer still BANDS on `max_out_of_scope_dbs` (they're not dead config, just
    # not read by GroundingGate.evaluate). See TIER_1.4_BENCHMARK.md.
    allow_out_of_scope: bool = False
    max_out_of_scope_dbs: int = 0       # eval scorer fail-line for out-of-scope DB count
    min_numeric_fidelity: float = 1.0   # annotate when grounded-number ratio is below this
    # RAG Trust Hardening Wave 1 (§1.5): the corpus-support trigger family's mode.
    # None ⇒ inherit ``mode`` (one ladder governs both families); a profile may set
    # groundingConfig.corpus.mode to override the corpus family independently.
    corpus_mode: Optional[str] = None

    def normalized_mode(self) -> str:
        return _modes.normalize(self.mode or "observe", allowed=MODES,
                                context="groundingConfig.mode")

    def normalized_corpus_mode(self) -> str:
        return _modes.normalize(self.corpus_mode or self.mode or "observe", allowed=MODES,
                                context="groundingConfig.corpus.mode")


@dataclass(frozen=True)
class GroundingInputs:
    """Everything the gate needs, extracted from the turn by the adapter."""
    answer_text: str = ""
    scope_databases: frozenset = frozenset()
    tool_calls: tuple = ()
    grounding_text: str = ""
    # In-scope concept/entity names — the allowlist the recall-first answer-reference
    # trigger checks against, so an in-scope table named in prose is recognized and an
    # unrecognized one trips the judge. Empty ⇒ trigger degrades to database-only.
    scope_entities: frozenset = frozenset()
    # RAG Trust Hardening Wave 1: the turn's retrieved chunk texts (empty ⇒ the
    # corpus-support family abstains, C3) and the user's question (part of the
    # support whitelist — the user's own words are never "unsupported").
    retrieved_chunks: tuple = ()
    question: str = ""
    # Numbers the turn's inputs vouch for (the user's own words, KG-declared codes and
    # spans) — never "ungrounded" (RunRecord.vouched_numbers).
    vouched_numbers: frozenset = frozenset()
    # The turn's results WITHOUT catalog calls — what the answer-reference trigger treats as
    # values the data returned. None ⇒ ``grounding_text`` (callers that build inputs by hand).
    value_text: Optional[str] = None


@dataclass(frozen=True)
class GroundingVerdict:
    decision: Decision
    reason: str
    out_of_scope_dbs: tuple = ()
    ungrounded_numbers: tuple = ()
    scope_databases: tuple = ()
    numeric_fidelity: Optional[float] = None
    annotation_md: Optional[str] = None
    # Tiered enforce (post-benchmark architecture): the deterministic gate never BLOCKS;
    # in enforce mode it sets needs_judge when a deterministic TRIGGER fires (out-of-scope
    # tool call OR the answer references out-of-scope data), and the executor runs the
    # scope_faithfulness JUDGE to make the real block/pass call. answer_oos_refs are the
    # out-of-scope identifiers found in the answer text (the oos08-class trigger).
    needs_judge: bool = False
    # #37 — `needs_judge` is the PRE-adjudication TRIGGER, and the fold that applies the
    # judge's ruling produces a fresh verdict that does not carry it. So a turn a judge
    # demonstrably ruled on persisted `needs_judge: false`, and the only way to tell a
    # judge had run was to parse the reason prose. `judged` records the adjudication
    # itself: a fact, stamped where it happens, rather than a string to be re-read.
    judged: bool = False
    judge_passed: Optional[bool] = None   # True faithful · False unfaithful · None abstained
    answer_oos_refs: tuple = ()
    # Enforce ⊇ annotate (the monotonicity contract): numeric fidelity below the
    # configured floor is a signal the scope judge does NOT adjudicate, so it must
    # keep its annotate-mode disclosure on the enforce rung instead of silently
    # vanishing there. Carried on the verdict so the judge fold (enforce.py) can
    # re-attach the numeric note even when the judge clears the scope trigger.
    numeric_concern: bool = False
    # ── RAG Trust Hardening Wave 1: the corpus-support family (§1.1: one gate, two
    # trigger families). ``ground`` names which family produced findings/triggers
    # ("scope" | "corpus" | "both"); ``judge_family`` names which judge adjudicates a
    # needs_judge escalation ("scope_faithfulness" | "groundedness").
    ground: str = "scope"
    judge_family: str = "scope_faithfulness"
    # ── the judge's FULL ruling, so the post-hoc lane can read it instead of paying to
    # re-run the same judge class on the same text (symmetry map A3/A4).
    #
    # `judge_passed` alone was not enough to fold on: the scoring layer's verdict carries a
    # SCORE and the per-model panel VOTES, so reading a bare boolean would have been a
    # downgrade dressed as a saving — the second reading really did tell the consumer
    # something the first could not. Recording them makes the read lossless, which is what
    # turns the fold from a trade into a strict improvement.
    judge_score: Optional[float] = None
    judge_votes: Optional[tuple] = None
    unsupported_claims: tuple = ()       # spans unsupported after second-chance (T1+T2+T3)
    second_chance_cleared: tuple = ()    # spans cleared by the C1 targeted retrieval
    claims_checked: int = 0

    @property
    def blocked(self) -> bool:
        return self.decision == Decision.BLOCK

    @property
    def flagged(self) -> bool:
        return self.decision in (Decision.ANNOTATE, Decision.BLOCK)

    def to_event(self) -> dict:
        """Compact, JSON-safe payload for the ``grounding_gate`` SSE event + eval span +
        persisted turn record (plan reload / audit, charter #17)."""
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "out_of_scope_dbs": list(self.out_of_scope_dbs),
            "scope_databases": list(self.scope_databases),
            "ungrounded_numbers": list(self.ungrounded_numbers),
            "numeric_fidelity": self.numeric_fidelity,
            "annotation_md": self.annotation_md,
            "needs_judge": self.needs_judge,
            "judged": self.judged,
            "judge_passed": self.judge_passed,
            "answer_oos_refs": list(self.answer_oos_refs),
            "numeric_concern": self.numeric_concern,
            "ground": self.ground,
            "judge_family": self.judge_family,
            "judge_score": self.judge_score,
            "judge_votes": list(self.judge_votes) if self.judge_votes else None,
            "unsupported_claims": list(self.unsupported_claims),
            "second_chance_cleared": list(self.second_chance_cleared),
            "claims_checked": self.claims_checked,
        }
