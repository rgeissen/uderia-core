"""Tier 1.1 — judge-backed enforce decision (pure, CI-testable).

The benchmark (Tier 1.4) proved the deterministic gate is the wrong *enforce* mechanism:
a tool-call breach over-blocks honest refusals (false positives) and misses answer-drift
that made no out-of-scope tool call (false negatives). So the deterministic gate is
observe-only and, in ``enforce`` mode, merely *triggers* the family's LLM judge
(``GroundingVerdict.needs_judge``; scope → ``scope_faithfulness``, corpus →
``groundedness`` — RAG_TRUST_HARDENING.md §1.2 C2). This module turns the judge's ruling
into the final verdict — the one place the block/annotate/pass decision lives.

The function is pure (no I/O, no LLM): the executor owns building the judge panel, running
the judge with a timeout, and attributing its cost; it then passes the judge's boolean
ruling here. That keeps the enforcement *policy* fully unit-testable without a live model.

Decision policy (research-backed: abstention is desirable, never punish honest refusals):
  - judge says UNFAITHFUL (``passed is False``) → BLOCK: withhold the answer.
  - judge says FAITHFUL (``passed is True``)  → PASS: answer intact (a true breach the
    deterministic trigger flagged but the judge cleared — e.g. an honest "not in scope").
  - judge ABSTAINS / unavailable / timed out (``passed is None``) → ANNOTATE: keep the
    answer, attach the soft note naming the findings. Fail-safe: on uncertainty
    we never withhold (charter #8/#15) and never silently break a turn.

See docs/roadmap/TIER_1.1_GROUNDING_GATE.md (§5, §14), TIER_1.4_BENCHMARK.md, and
RAG_TRUST_HARDENING.md §1.
"""
from __future__ import annotations

from typing import Optional

from trusted_data_agent.agent.grounding.gate import GroundingGate
from trusted_data_agent.agent.grounding.types import Decision, GroundingVerdict


def resolve_enforced_verdict(deterministic: GroundingVerdict,
                             judge_passed: Optional[bool],
                             judge_reason: str = "",
                             judged: bool = True) -> GroundingVerdict:
    """Fold the family judge's ruling into the deterministic trigger verdict.

    ``deterministic`` is the gate's enforce verdict (always ``PASS`` + ``needs_judge``;
    carries the scope findings AND the corpus findings — whichever family triggered).
    ``judge_passed`` is the judge consensus: ``False`` unfaithful, ``True`` faithful,
    ``None`` abstain/unavailable. Returns a NEW verdict; never mutates the input.

    ``judged`` records whether a real panel actually adjudicated (#37). It is NOT the
    same as "we tried": an empty panel, a timeout or an exception all reach here with
    ``judge_passed=None``, and reporting those as judged would claim an adjudication
    that never happened. A genuine ABSTENTION is `judged=True, judge_passed=None` —
    a judge looked and could not decide, which is a different state worth telling
    apart from "no judge ran".
    """
    out_of_scope = tuple(deterministic.out_of_scope_dbs or ())
    answer_refs = tuple(deterministic.answer_oos_refs or ())
    scope_sorted = tuple(deterministic.scope_databases or ())
    numeric_fidelity = deterministic.numeric_fidelity
    ungrounded = tuple(deterministic.ungrounded_numbers or ())
    # Wave 1 carry-through: the corpus family's findings survive adjudication so the
    # card/event renders them identically pre- and post-judge.
    _family = dict(
        ground=getattr(deterministic, "ground", "scope"),
        judge_family=getattr(deterministic, "judge_family", "scope_faithfulness"),
        unsupported_claims=tuple(getattr(deterministic, "unsupported_claims", ()) or ()),
        second_chance_cleared=tuple(getattr(deterministic, "second_chance_cleared", ()) or ()),
        claims_checked=int(getattr(deterministic, "claims_checked", 0) or 0),
    )

    numeric_concern = bool(getattr(deterministic, "numeric_concern", False))

    if judge_passed is True:
        # The judge cleared it — a flagged-but-faithful answer (commonly an honest
        # "that data isn't in scope / the knowledge base"). Deliver it unchanged —
        # UNLESS the deterministic pass also found a numeric-fidelity concern: the
        # scope judge rules on drift, never on numbers, so an unadjudicated signal
        # keeps its annotate-mode disclosure (enforce ⊇ annotate). The scope
        # findings themselves are NOT annotated — the judge cleared exactly those.
        if numeric_concern and ungrounded:
            annotation = GroundingGate._annotation(
                (), ungrounded, (), scope_sorted, Decision.ANNOTATE)
            return GroundingVerdict(
                Decision.ANNOTATE,
                "enforce — judge: faithful · ungrounded figures annotated",
                out_of_scope_dbs=out_of_scope, ungrounded_numbers=ungrounded,
                scope_databases=scope_sorted, numeric_fidelity=numeric_fidelity,
                annotation_md=annotation, answer_oos_refs=answer_refs,
                numeric_concern=True, judged=judged, judge_passed=True, **_family)
        return GroundingVerdict(
            Decision.PASS, "enforce — judge: faithful",
            out_of_scope_dbs=out_of_scope, ungrounded_numbers=ungrounded,
            scope_databases=scope_sorted, numeric_fidelity=numeric_fidelity,
            answer_oos_refs=answer_refs, numeric_concern=numeric_concern,
            # #37 — the adjudication is recorded as a fact. A cleared escalation is the
            # strongest evidence the gate works, and it used to be visible only by
            # parsing this verdict's own prose.
            judged=judged, judge_passed=True, **_family)

    decision = Decision.BLOCK if judge_passed is False else Decision.ANNOTATE
    reason = ("enforce — judge: drifted" if judge_passed is False
              else "enforce — judge unavailable, annotated (fail-safe)")
    if judge_passed is False and _family["judge_family"] == "groundedness":
        reason = "enforce — judge: unsupported by the knowledge base"
    # The judge's ruling is what a withheld answer must lead with (#42), so the fold
    # hands the composed reason to the annotation builder rather than leaving the
    # reader with the deterministic trigger's wide net.
    annotation = GroundingGate._annotation(
        out_of_scope, ungrounded, answer_refs, scope_sorted, decision,
        cf=_corpus_stub(_family),
        judge_reason=(f"{reason}: {judge_reason}" if judge_reason else reason))
    return GroundingVerdict(
        decision, (f"{reason}: {judge_reason}" if judge_reason else reason),
        out_of_scope_dbs=out_of_scope, ungrounded_numbers=ungrounded,
        scope_databases=scope_sorted, numeric_fidelity=numeric_fidelity,
        annotation_md=annotation, answer_oos_refs=answer_refs,
        numeric_concern=numeric_concern,
        # `judged` is True even when the panel ABSTAINED: a judge was consulted and
        # could not decide, which is a different (and reportable) state from "no judge
        # ran at all". `judge_passed` carries the None that distinguishes them.
        judged=judged, judge_passed=judge_passed, **_family)


def _corpus_stub(family: dict):
    """A minimal CorpusFindings view for annotation building (spans only)."""
    if not family.get("unsupported_claims"):
        return None
    from trusted_data_agent.agent.grounding.corpus import CorpusFindings
    claims = family["unsupported_claims"]
    return CorpusFindings(claims_checked=family.get("claims_checked", 0),
                          unsupported_identifiers=tuple(claims))
