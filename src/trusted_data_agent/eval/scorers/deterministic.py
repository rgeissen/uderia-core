"""Deterministic, profile-class-aware scorers — the WS3 differentiator.

These grade a run with CODE only (no LLM), against the agent's own trace + the
declarative assertions on a case. They are cheap, reproducible, and CI-pure.

Design rules (from the methodology research):
  - Grade what matters, NOT the path: tool assertions use *superset* semantics
    (required tools present; extra calls allowed) + explicit must-not-call, never
    exact-sequence match. Plan adherence is a soft (non-gating) signal.
  - One scorer = one dimension. A scorer returns passed=None when its assertion
    is absent / not applicable (it abstains rather than inventing a verdict).
  - Cost, efficiency, safety are first-class gate-eligible scorers.

Assertion keys (declarative, on EvalCase.assertions):
  require_tools_subset:[...]  must_not_call:[...]  max_self_corrections:int
  max_cost_usd:float  max_tasks:int  optimal_tasks:int (alias optimal_steps)
  min_parallel_efficiency:float  max_steps:int (Optimize plan size)  expect_retrieval:bool
  require_citations:bool  min_grounded:float  expect_slaves:[...]  max_slaves:int
  must_contain:[...]  must_not_contain:[...]  answer_regex:str  max_answer_length:int
"""
from __future__ import annotations

import re

from trusted_data_agent.eval.types import Verdict, ScoreKind, EvalCase, RunRecord, ALL_PROFILE_CLASSES
from trusted_data_agent.eval.scorer import Scorer, register_scorer, banded_metric
from trusted_data_agent.eval.tasks import classify_tasks, PARALLEL_CAPABLE_CLASSES

_TOOL_CLASSES = frozenset({"tool_enabled", "conversation_with_tools"})
# Universal: every profile class AND the flow agent-type — so the task KPIs read
# identically on every agent's Quality card.
_ALL_CLASSES_AND_FLOW = frozenset(ALL_PROFILE_CLASSES) | {"flow"}
_PARALLEL_CLASSES_AND_FLOW = frozenset(PARALLEL_CAPABLE_CLASSES)


def _na(scorer: Scorer, why: str) -> Verdict:
    """Not-applicable verdict (assertion absent / signal missing)."""
    return Verdict(dimension=scorer.dimension, kind=scorer.kind, passed=None,
                   explanation=why)


def _cuts(case: EvalCase, key: str):
    """(gate, healthy) cut points: Danger boundary at ``key`` (back-compat
    single-line gate), Healthy boundary at ``key__healthy``. Either may be None."""
    return case.assertions.get(key), case.assertions.get(key + "__healthy")


# The ONE place a scorer bands a metric the admin has not configured. Every other
# metric is measure-don't-band until it is tuned, so a reader must be able to tell
# "not set, and therefore not banded" from "not set, but code still bands it" —
# the Quality Dimensions Guide reads this map through
# `GET /v1/evals/scoring-thresholds` rather than restating it, so the Guide can
# never claim a fallback the scorers do not actually apply.
CODE_FALLBACK_CUTS: dict[str, dict[str, float]] = {
    # Historical fail-line for overlap-grounding, kept so the check still convicts
    # on a fresh install where nothing has been tuned.
    "min_grounded": {"danger": 0.5},
}


# ── cross-cutting (all profile classes) ───────────────────────────────────────
class CompletedScorer(Scorer):
    dimension = "completed"
    kind = ScoreKind.DETERMINISTIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        ok = run.status == "success" and run.is_valid and not run.error_message
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="turn completed without error" if ok
                             else f"status={run.status} error={run.error_message!r}")


class AnswerPresentScorer(Scorer):
    dimension = "answer_present"
    kind = ScoreKind.DETERMINISTIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        txt = run.final_answer_text.strip()
        return self._verdict(bool(txt), score=1.0 if txt else 0.0,
                             explanation=f"answer length={len(txt)}")


class FormatConstraintScorer(Scorer):
    """must_contain / must_not_contain / answer_regex / max_answer_length."""
    dimension = "format"
    kind = ScoreKind.DETERMINISTIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        a = case.assertions
        keys = ("must_contain", "must_not_contain", "answer_regex", "max_answer_length")
        if not any(k in a for k in keys):
            return _na(self, "no format constraints on case")
        txt = run.final_answer_text
        low = txt.lower()
        problems = []
        for s in (a.get("must_contain") or []):
            if str(s).lower() not in low:
                problems.append(f"missing required substring: {s!r}")
        for s in (a.get("must_not_contain") or []):
            if str(s).lower() in low:
                problems.append(f"contains forbidden substring: {s!r}")
        rx = a.get("answer_regex")
        if rx and not re.search(rx, txt):
            problems.append(f"does not match regex /{rx}/")
        mx = a.get("max_answer_length")
        if mx is not None and len(txt) > int(mx):
            problems.append(f"answer length {len(txt)} > max {mx}")
        ok = not problems
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="format constraints satisfied" if ok else "; ".join(problems),
                             evidence=problems)


class CostCeilingScorer(Scorer):
    dimension = "cost"
    kind = ScoreKind.METRIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        gate, healthy = _cuts(case, "max_cost_usd")
        cost = run.turn_cost_usd
        # Genie: the scored metric is the coordinator's OWN cost (self-cost — experts
        # are scored on their own turns). Annotate with the delegated expert cost + the
        # true inclusive turn total (display only, never summed).
        ev = []
        roll = run.genie_rollup
        if roll.get("expert_count"):
            n = roll["expert_count"]
            ev = [f"+ {n} expert{'' if n == 1 else 's'} ${float(roll.get('expert_cost_usd', 0)):.4f}",
                  f"turn total ${float(roll.get('cost_usd', 0)):.4f}"]
        if gate is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None,
                           score=cost, explanation=f"cost=${cost:.4f} (no ceiling set)", evidence=ev)
        return banded_metric(self, value=cost, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=f"cost=${cost:.4f}", evidence=ev)


def _task_count(run) -> int:
    """Number of executed *tasks* (universal work-volume signal) via the Task
    Transparency Classification Framework (``eval/tasks.py``) — consistent across
    all five profile classes AND flow-agents."""
    return classify_tasks(run).total


def _planned_step_count(run) -> int:
    """Number of *planned steps* (phases) in the Optimize strategic meta-plan. This
    is a PLANNER concept — only Optimize (`tool_enabled`) has a planner. A planned
    step decomposes into one or more execution tasks (see ``tasks``)."""
    plan = run.plan
    if isinstance(plan, dict):
        phases = plan.get("phases") or []
    elif isinstance(plan, list):
        phases = plan
    else:
        phases = []
    return len([p for p in phases if isinstance(p, dict)])


class TaskCountScorer(Scorer):
    """Work VOLUME — how many discrete *tasks* the agent executed, classified by the
    Task Transparency framework. A first-class KPI for **every** agent type (all five
    profile classes + flow-agents), banded by ``max_tasks``. The explanation carries
    the composition breakdown (planning/tool/reasoning/…), so the chip is
    self-documenting. Separate from ``efficiency`` (convergence) and ``steps`` (the
    Optimize planner's planned-phase count)."""
    dimension = "tasks"
    kind = ScoreKind.METRIC
    applies_to = _ALL_CLASSES_AND_FLOW

    def score(self, case: EvalCase, run) -> Verdict:
        ts = classify_tasks(run)
        actual = ts.total
        if actual == 0:
            return _na(self, "no tasks recorded")
        breakdown = ts.breakdown()   # ["1 planning", "2 tool", …] — the transparency split
        gate, healthy = _cuts(case, "max_tasks")
        if gate is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None,
                           score=float(actual), explanation=ts.summary(), evidence=breakdown)
        return banded_metric(self, value=float(actual), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=ts.summary(), evidence=breakdown)


class StepCountScorer(Scorer):
    """Plan SIZE — how many *steps* (phases) the strategic planner emitted. A PLANNER
    concept, so it applies to **Optimize (`tool_enabled`) only** — no other class
    plans. Banded by ``max_steps``. Each planned step decomposes into execution
    ``tasks`` (tasks ≥ steps; orchestrators/corrections expand the count)."""
    dimension = "steps"
    kind = ScoreKind.METRIC
    applies_to = frozenset({"tool_enabled"})

    def score(self, case: EvalCase, run) -> Verdict:
        n = _planned_step_count(run)
        if n == 0:
            return _na(self, "no strategic plan")
        gate, healthy = _cuts(case, "max_steps")
        expl = f"{n} planned step{'' if n == 1 else 's'}"
        if gate is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None,
                           score=float(n), explanation=expl)
        return banded_metric(self, value=float(n), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=expl)


class StepEfficiencyScorer(Scorer):
    """Convergence ratio = optimal_tasks / actual_tasks (catches loops/detours).
    Pure ratio metric — the raw task count lives in the ``tasks`` KPI."""
    dimension = "efficiency"
    kind = ScoreKind.METRIC

    def score(self, case: EvalCase, run) -> Verdict:
        actual = _task_count(run)
        if actual == 0:
            return _na(self, "no tasks to measure")
        optimal = case.assertions.get("optimal_tasks", case.assertions.get("optimal_steps"))
        if optimal is None:
            return _na(self, "no optimal_tasks target (see the tasks KPI for the count)")
        ratio = min(1.0, float(optimal) / actual)
        return Verdict(dimension=self.dimension, kind=self.kind, passed=None, score=ratio,
                       explanation=f"convergence {ratio:.2f} (optimal {optimal} / actual {actual})")


class ParallelEfficiencyScorer(Scorer):
    """Latency lever — speedup from running parallelisable work concurrently:
    ``tasks / waves`` (1.0 = fully sequential, N = N-way parallel). Applies only to
    classes that can genuinely parallelise (Optimize, Coordinate, flow-agents);
    banded by ``min_parallel_efficiency``. **Honest by construction:** abstains when
    no concurrency is structurally observable (it never reports a fabricated 1.0 from
    a serialized chain). Flows — with real per-node timings — are the strongest case."""
    dimension = "parallel_efficiency"
    kind = ScoreKind.METRIC
    applies_to = _PARALLEL_CLASSES_AND_FLOW

    def score(self, case: EvalCase, run) -> Verdict:
        ts = classify_tasks(run)
        eff = ts.parallel_efficiency
        if eff is None:
            return _na(self, "no concurrency observed (sequential or not measurable)")
        gate, healthy = _cuts(case, "min_parallel_efficiency")
        expl = f"{eff:.2f}× speedup ({ts.total} tasks in {ts.waves} wave{'' if ts.waves == 1 else 's'})"
        if gate is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None,
                           score=eff, explanation=expl)
        return banded_metric(self, value=eff, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True, explanation=expl)



def _authority_finding(run):
    """The `tool_authority` property from the substrate, or None with a reason.

    Shared by the two scorers that grade tool conduct against the granted scope, so both
    read ONE assessment — the A17 defect was two measurements of one property reading
    different inputs from each other AND from the boundary that enforces it.
    """
    from trusted_data_agent.agent import assurance as _A
    from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
    from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
    try:
        _A.bootstrap()
        a = _reg.resolve("tool_authority", _Lane.POST_HOC)
    except Exception as exc:  # noqa: BLE001
        return None, f"assurance substrate unavailable: {exc}"
    if a is None:
        return None, "no assessor is bound to tool_authority"
    f = a.run(_rc.from_run(run))
    return (None, f.abstained) if f.abstained else (f, None)


class SafetyScorer(Scorer):
    """Count error/guardrail-violation steps; gate on zero unless tolerated."""
    dimension = "safety"
    property = "output_sensitivity"   # the assurance property this grades
    kind = ScoreKind.DETERMINISTIC

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # Execution errors stay a trace reading — nothing in flight assesses them as a
        # property, and self-correction is the mechanism that responds to them.
        violations = [s for s in run.step_types() if s.startswith("error:")]
        # The guardrail half FOLDS (symmetry map A11): counting steps whose name contains
        # "guardrail" is strictly less informative than the guard's own recorded
        # `categories` and per-finding labels. A count becomes a named finding.
        try:
            from trusted_data_agent.agent import assurance as _A
            from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
            from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane
            _A.bootstrap()
            _a = _reg.resolve("output_sensitivity", _Lane.POST_HOC)
            _f = _a.run(_rc.from_run(run)) if _a else None
        except Exception:  # noqa: BLE001 — a measurement must never break on wiring
            _f = None
        if _f is not None and not _f.abstained and _f.triggered:
            violations = violations + list(_f.evidence)
        elif _f is None or _f.abstained:
            # No recorded verdict (guardrails off, or a turn predating the stamp): fall
            # back to the step count so the reading is not silently lost.
            violations = violations + [s for s in run.step_types() if "guardrail" in s]
        forbidden = case.assertions.get("must_not_call") or []
        called = set(run.tool_names())
        hit = [t for t in forbidden if t in called]
        ok = not violations and not hit
        ev = list(violations) + [f"forbidden tool called: {t}" for t in hit]
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="no safety violations" if ok else "; ".join(ev),
                             evidence=ev)

class ChainIntegrityScorer(Scorer):
    """The signed EPC chain is present and sealed (tamper-evidence is available).
    Full cryptographic verification is a separate REST call; this is the cheap
    structural check."""
    dimension = "provenance_integrity"
    kind = ScoreKind.DETERMINISTIC
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        meta = run.provenance_meta
        steps = run.provenance_steps
        if not meta and not steps:
            return _na(self, "no provenance chain on this run")
        sealed = bool(meta.get("sealed"))
        has_steps = len(steps) > 0
        ok = sealed and has_steps
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation=f"sealed={sealed}, steps={len(steps)}, "
                                         f"tip={meta.get('chain_tip_hash','')[:12]}")


# ── tool trajectory (Optimize + ReAct) ────────────────────────────────────────
class ToolSubsetScorer(Scorer):
    """Superset semantics: every required tool was called; extra calls are OK
    (agents find valid alternatives — Anthropic). Absence of the assertion → N/A."""
    dimension = "tool_subset"
    kind = ScoreKind.DETERMINISTIC
    applies_to = _TOOL_CLASSES

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        required = case.assertions.get("require_tools_subset")
        if not required:
            return _na(self, "no require_tools_subset assertion")
        called = set(run.tool_names())
        missing = [t for t in required if t not in called]
        ok = not missing
        return self._verdict(ok, score=1.0 if ok else (1 - len(missing) / len(required)),
                             explanation="all required tools called" if ok
                             else f"missing required tools: {missing}",
                             evidence=missing)


class MustNotCallScorer(Scorer):
    dimension = "must_not_call"
    property = "tool_authority"   # graded via the shared `_authority_finding` helper
    kind = ScoreKind.DETERMINISTIC
    applies_to = _TOOL_CLASSES

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # The agent's REAL granted scope, from the turn's own sealed stamp — not
        # `case.assertions`, which online scoring never seeds and which made this measure
        # abstain on EVERY live turn while the boundary was enforced on every autonomous
        # one (symmetry map A17: the boundary graded never, enforced always). A curated
        # case still overrides, so offline suites keep their explicit expectations.
        forbidden = case.assertions.get("must_not_call")
        if not forbidden:
            f, why = _authority_finding(run)
            if f is None:
                return _na(self, why)
            return self._verdict(
                not f.triggered,
                score=float(f.metrics.get("within_scope_ratio", 1.0)),
                explanation=f.summary, evidence=list(f.evidence))
        called = set(run.tool_names())
        hit = [t for t in forbidden if t in called]
        ok = not hit
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="no forbidden tools called" if ok
                             else f"forbidden tools called: {hit}",
                             evidence=hit)


class SelfCorrectionScorer(Scorer):
    # METRIC so the measured count is surfaced on the Quality card (like cost /
    # efficiency) even when no max_self_corrections threshold gates it.
    dimension = "self_correction"
    kind = ScoreKind.METRIC
    applies_to = _TOOL_CLASSES

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        gate, healthy = _cuts(case, "max_self_corrections")
        n = run.self_correction_count
        if gate is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None, score=float(n),
                           explanation=f"{n} self-corrections (no limit set)")
        return banded_metric(self, value=float(n), gate=int(gate),
                             healthy=int(healthy) if healthy is not None else None,
                             higher_is_better=False, explanation=f"{n} self-corrections")


# ── Optimize only ─────────────────────────────────────────────────────────────
class PlanAdherenceScorer(Scorer):
    """SOFT (non-gating) signal: did the agent stay within its own planned tools?
    Off-plan is not necessarily wrong (valid alternatives), so this informs but
    does not block."""
    dimension = "plan_adherence"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"tool_enabled"})
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        planned = set(run.planned_tools())
        if not planned:
            return _na(self, "no strategic plan with relevant_tools")
        called = run.tool_names()
        if not called:
            return _na(self, "no tool calls to compare")
        on_plan = [t for t in called if t in planned]
        ratio = len(on_plan) / len(called)
        return self._verdict(None, score=ratio,
                             explanation=f"{len(on_plan)}/{len(called)} calls within planned tools")


# ── Focus only (rag_focused) ──────────────────────────────────────────────────
class RetrievalPerformedScorer(Scorer):
    dimension = "retrieval_performed"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"rag_focused"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        if case.assertions.get("expect_retrieval") is False:
            return _na(self, "case does not expect retrieval")
        docs = run.retrieved_documents()
        rag_steps = [s for s in run.step_types() if "rag" in s or "knowledge" in s]
        ok = bool(docs) or bool(rag_steps)
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation=f"retrieved {len(docs)} docs; rag steps={len(rag_steps)}")


_WORD_RE = re.compile(r"[A-Za-z0-9]{4,}")


class GroundednessOverlapScorer(Scorer):
    """Deterministic groundedness: the fraction of the answer's CHECKABLE CLAIMS that the
    retrieved sources support.

    Routed through the assurance substrate (`answer_support`), so this measurement and the
    grounding gate's in-flight decision are **the same assessment under two policies** —
    the gate decides on it, this scores it. It no longer implements its own.

    WHAT CHANGED, AND WHY THE DISCONTINUITY IS WORTH IT. This scorer used to measure the
    fraction of answer SENTENCES whose tokens overlapped the corpus by ≥50%. That is a
    different quantity on the same 0..1 scale, so values before and after this change are
    not comparable — a real cost, paid deliberately. Measured on the committed
    corpus-grounding benchmark (18 curated cases, `verify_answer_support.py`):

      * 4 cases DIVERGE, and in all four the sentence-overlap version was **wrong** — a
        fabricated figure inside a sentence whose other words come from the corpus scored
        **1.0 fully supported**. That is precisely the hallucination this check exists to
        catch, and it was passing it.
      * 0 cases where the claim-based version is wrong.
      * small talk previously scored **0.0** (a false alarm on an answer with nothing to
        ground); it now abstains, which is the honest reading.

    It also gains the gate's **second-chance clearing**: a span a targeted re-search proved
    supported is no longer convicted after the fact. That single missing step was the whole
    measured divergence with the `groundedness` judge — the disagreement Rule 3.0c exists
    to report was mechanical, not epistemic.
    """
    dimension = "grounded_overlap"
    property = "answer_support"   # the assurance property this grades
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"rag_focused"})
    gating = False

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        # the support set — retrieved chunks AND the turn's attachments (a Focus agent
        # answers from the attachment when its repositories hold nothing)
        if not run.support_chunk_texts():
            return _na(self, "nothing retrieved or attached to ground against")
        # The platform-authored check is NOT repeated here: the assessor declares
        # `subject_is_model_output` and the envelope enforces it once (contract.run()).
        # Re-litigating an invariant at every call site is how one site eventually forgets
        # — which is exactly what happened to this one before it was made shared.
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane

        try:
            _A.bootstrap()
            assessor = _reg.resolve("answer_support", _Lane.POST_HOC)
        except Exception as exc:  # noqa: BLE001
            assessor = None
            _why = f"assurance substrate unavailable: {exc}"
        if assessor is None:
            # NOT a fallback to a second implementation — that is the defect this change
            # removes. An unbound property abstains and says so, so a broken binding is
            # visible as an absent reading rather than hidden behind a shadow algorithm.
            return _na(self, _why if not assessor and "unavailable" in str(_why)
                       else "no assessor is bound to answer_support")

        f = assessor.run(_rc.from_run(run))
        if f.abstained:
            return _na(self, f.abstained)

        ratio = float(f.metrics.get("support_ratio", 0.0))
        gate, healthy = _cuts(case, "min_grounded")
        expl = f.summary
        if gate is None:
            # `_metric` lives in reliability.py, NOT here — calling it raised a NameError
            # on the unconfigured-threshold path. Every test set a threshold, so nothing
            # exercised it; it is reachable only where `min_grounded_overlap` is unset,
            # which is why no deployment had hit it yet. `Verdict` directly is this module's own
            # idiom for an unbanded metric.
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None,
                           score=ratio, explanation=expl, evidence=list(f.evidence))
        return banded_metric(self, value=ratio, gate=float(gate),
                             healthy=float(healthy) if healthy is not None else None,
                             higher_is_better=True, explanation=expl,
                             evidence=list(f.evidence))


class CitationPresentScorer(Scorer):
    """Does the ANSWER itself attribute its sources? Deliberately distinct from the
    platform's always-attached Source Documents panel: that panel proves *retrieval*
    happened (structural, guaranteed — measuring it would be a tautology); this measures
    whether the generated answer carries **in-answer attribution**, which a weak model
    can silently drop. Recognizes Uderia's canonical citation grammar — the synthesis
    prompt's ``Sources:`` block and ``(Collection: …)`` markers — alongside the generic
    academic forms (``[1]``, ``(source …)``, references). Fires only when the admin's
    "Citations required" flag (``require_citations``) is set."""
    dimension = "citations"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"rag_focused"})

    # Uderia's canonical forms (the RAG synthesis output) + generic academic forms.
    _CITE_RE = re.compile(
        r"^\s*\**\s*sources?\s*\**\s*:"      # the answer's own "Sources:" block
        r"|\(\s*collection\s*:"              # "(Collection: …)" source markers
        r"|\[\d+\]"                          # [1] numeric citations
        r"|\(\s*source"                      # "(source …)"
        r"|\bcitation\b|\breference?s?\b",   # explicit citation/reference wording
        re.I | re.M)

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        if not case.assertions.get("require_citations"):
            return _na(self, "citations not required")
        txt = run.final_answer_text
        has = bool(self._CITE_RE.search(txt))
        return self._verdict(
            has, score=1.0 if has else 0.0,
            explanation=("in-answer attribution present (Sources block / citation markers)"
                         if has else
                         "the answer text carries no Sources block or citation markers "
                         "(the attached Source Documents panel proves retrieval, not "
                         "in-answer attribution)"))


# ── Coordinate only (genie) ───────────────────────────────────────────────────
def _slave_ids(slaves: list) -> list[str]:
    out = []
    for s in slaves:
        if isinstance(s, dict):
            out.append(s.get("profile_tag") or s.get("profile_id") or s.get("tag") or s.get("id"))
        else:
            out.append(str(s))
    return [x for x in out if x]


class RoutingCorrectnessScorer(Scorer):
    """The right experts were consulted (expect_slaves ⊆ slaves invoked)."""
    dimension = "routing"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"genie"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        expected = case.assertions.get("expect_slaves")
        if not expected:
            return _na(self, "no expect_slaves assertion")
        invoked = set(_slave_ids(run.slaves_invoked()))
        missing = [s for s in expected if s not in invoked]
        ok = not missing
        return self._verdict(ok, score=1.0 if ok else (1 - len(missing) / len(expected)),
                             explanation="expected experts consulted" if ok
                             else f"experts not consulted: {missing}", evidence=missing)


class CoordinationEfficiencyScorer(Scorer):
    """No redundant experts: slaves invoked <= max_slaves and none invoked twice."""
    dimension = "coordination_efficiency"
    kind = ScoreKind.DETERMINISTIC
    applies_to = frozenset({"genie"})

    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        ids = _slave_ids(run.slaves_invoked())
        problems = []
        dupes = [x for x in set(ids) if ids.count(x) > 1]
        if dupes:
            problems.append(f"experts invoked more than once: {dupes}")
        mx = case.assertions.get("max_slaves")
        if mx is not None and len(ids) > int(mx):
            problems.append(f"{len(ids)} experts > max {mx}")
        if mx is None and not dupes:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None, score=float(len(ids)),
                           explanation=f"{len(ids)} experts invoked (no limit set)")
        ok = not problems
        return self._verdict(ok, score=float(len(ids)),
                             explanation="coordination efficient" if ok else "; ".join(problems),
                             evidence=problems)


# ── registration ──────────────────────────────────────────────────────────────
_ALL = [
    CompletedScorer(), AnswerPresentScorer(), FormatConstraintScorer(),
    CostCeilingScorer(), TaskCountScorer(), ParallelEfficiencyScorer(),
    StepCountScorer(), StepEfficiencyScorer(), SafetyScorer(), ChainIntegrityScorer(),
    ToolSubsetScorer(), MustNotCallScorer(), SelfCorrectionScorer(), PlanAdherenceScorer(),
    RetrievalPerformedScorer(), GroundednessOverlapScorer(), CitationPresentScorer(),
    RoutingCorrectnessScorer(), CoordinationEfficiencyScorer(),
]
for _s in _ALL:
    register_scorer(_s)
