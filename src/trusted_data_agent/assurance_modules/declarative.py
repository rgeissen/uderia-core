"""
T0 — declarative assessors. No third-party Python at all.

A manifest describes what to look for; the platform's own code does the looking. There is
nothing to sandbox because there is nothing to execute, which makes this the tier most
third-party assurance capability should be written in: domain compliance phrasing, a
required disclaimer, an industry vocabulary rule. Python is the escape hatch, not the
default.

Two forms today:

  * `patterns` — a `flag` family and an optional `clear` family of regexes over one field
    of the assessment context. A `flag` hit that no `clear` hit rescues is a finding.
  * `metric_expr` — a `safe_expression` over named counts derived from the context, banded
    by the pack's own tunable. Reuses the flow sandbox rather than a second evaluator.

Both are PURE, REPLAYABLE, DETERMINISTIC and RETURN_METRIC by construction, so their lane
eligibility is derived and correct without the pack asserting anything about itself.

REGEX SAFETY. A pattern is data, but a catastrophically-backtracking pattern is a denial
of service on the answer path. Patterns are compiled at LOAD (a bad one refuses the pack,
never a turn), capped in count and length, and screened for the nested-quantifier shape
that produces exponential backtracking.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.agent.assurance.capabilities import AssessorCapability as C
from trusted_data_agent.agent.assurance.contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain, plural)

#: A conservative screen for exponential backtracking: a quantified group whose body is
#: itself quantified, e.g. `(a+)+` or `(\d*)*`. It over-refuses rather than under-refuses,
#: which is the correct direction for something that runs on every turn.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*]")

MAX_PATTERNS = 64
MAX_PATTERN_CHARS = 500
#: How many spans a single finding may carry for masking. Bounded because an adversarial
#: pattern over a long answer could otherwise produce a very large payload.
MAX_SPANS = 200
#: What KIND of finding this is, from the platform's closed set. Classification is
#: assessment (the pack's job); the ACT that follows is policy (the platform's). A pack
#: says "this is personal data"; the platform's ladder decides that personal data is
#: redacted in enforce and merely noted in annotate.
FINDING_CATEGORIES = ("pii", "toxicity", "exfil")
#: How much of a match is carried as evidence. Enough to recognise, never enough to be a
#: quotation of the answer — the same privacy reasoning as the guardrail findings, which
#: report labels and never the matched value.
EVIDENCE_CHARS = 120
MAX_EVIDENCE = 3

#: Which context fields a declarative rule may read. `platform_authored` is absent for the
#: same reason it is not declarable anywhere: the envelope owns it.
READABLE_FIELDS = ("answer", "question", "chunks", "tool_outputs")


class DeclarativeSpecError(ValueError):
    """A manifest spec that cannot be turned into an assessor. Raised at LOAD."""


def _compile_patterns(raw: Any, where: str) -> List[Any]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise DeclarativeSpecError(f"{where} must be a list of patterns")
    if len(raw) > MAX_PATTERNS:
        raise DeclarativeSpecError(
            f"{where} declares {len(raw)} patterns; the cap is {MAX_PATTERNS}")
    out = []
    for p in raw:
        if not isinstance(p, str) or not p:
            raise DeclarativeSpecError(f"{where} contains a non-string pattern")
        if len(p) > MAX_PATTERN_CHARS:
            raise DeclarativeSpecError(
                f"{where} contains a pattern of {len(p)} chars; the cap is "
                f"{MAX_PATTERN_CHARS}")
        if _NESTED_QUANTIFIER.search(p):
            raise DeclarativeSpecError(
                f"{where} contains a nested quantifier ({p[:40]!r}) — that shape can "
                f"backtrack exponentially, and this runs on every turn")
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            raise DeclarativeSpecError(f"{where}: {p[:40]!r} is not a valid regex ({exc})")
    return out


def _field_text(ctx: AssessmentContext, field: str) -> str:
    v = getattr(ctx, field, "")
    if isinstance(v, (tuple, list)):
        return "\n".join(str(x) for x in v)
    return str(v or "")


class PatternAssessor(Assessor):
    """Flag a pattern family, optionally rescued by a second family."""

    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                             C.RETURNS_METRIC, C.RETURNS_EVIDENCE})

    def __init__(self, *, key: str, property: str, label: str, spec: Dict[str, Any],
                 subject_is_model_output: bool = False,
                 requires: Tuple[str, ...] = ("answer",),
                 field: str = "answer", summary_singular: str = "match",
                 category: str = "pii", span_label: str = ""):
        self.key = key
        self.property = property
        self.label = label or key
        self.subject_is_model_output = subject_is_model_output
        self.requires = frozenset(requires)
        if category not in FINDING_CATEGORIES:
            raise DeclarativeSpecError(
                f"{key}: finding_category {category!r} is not one of "
                f"{FINDING_CATEGORIES} — a pack classifies its finding; the platform's "
                f"ladder decides what happens to it")
        self._category = category
        #: What a masked span is LABELLED as, e.g. `[REDACTED:PHI]`.
        self._span_label = (span_label or label or key.split(".")[-1]).upper()[:24]
        if field not in READABLE_FIELDS:
            raise DeclarativeSpecError(
                f"{key}: field {field!r} is not readable — one of {READABLE_FIELDS}")
        self._field = field
        self._singular = summary_singular
        self._flag = _compile_patterns(spec.get("flag"), f"{key}.flag")
        self._clear = _compile_patterns(spec.get("clear"), f"{key}.clear")
        if not self._flag:
            raise DeclarativeSpecError(f"{key}: a pattern rule must declare `flag`")

    def assess(self, ctx: AssessmentContext) -> Finding:
        text = _field_text(ctx, self._field)
        if not text.strip():
            return abstain(self.property, self.key,
                           f"this turn carried no {self._field}")
        if any(c.search(text) for c in self._clear):
            return Finding(property=self.property, assessor=self.key, triggered=False,
                           severity=Severity.NONE,
                           summary="a clearing pattern matched",
                           metrics={"flag_hits": 0.0})

        hits, evidence, spans = 0, [], []
        for rx in self._flag:
            for m in rx.finditer(text):
                hits += 1
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(m.group(0)[:EVIDENCE_CHARS])
                # SPANS, for a gate that must MASK what was found. `(label, start, end,
                # value)` — the shape `primitives.redact_pii` already splices, so a
                # third-party detector drives the platform's own redaction rather than
                # bringing its own.
                #
                # ⚠ These carry the RAW matched value. They ride `payload`, which no
                # surface renders (`active_modules` emits summary/metrics/labels only) and
                # which the in-flight seam strips before anything is reported. Evidence —
                # the display form — stays capped and separate.
                if len(spans) < MAX_SPANS:
                    spans.append([self._span_label, m.start(), m.end(), m.group(0)])
        if not hits:
            return Finding(property=self.property, assessor=self.key, triggered=False,
                           severity=Severity.NONE, summary="no match",
                           metrics={"flag_hits": 0.0})
        return Finding(
            property=self.property, assessor=self.key, triggered=True,
            severity=Severity.CONCERN,
            summary=f"{plural(hits, self._singular)} in the {self._field}",
            evidence=tuple(evidence), metrics={"flag_hits": float(hits)},
            payload={"spans": spans, "category": self._category})


class MetricExprAssessor(Assessor):
    """A number, computed by the platform's own expression sandbox.

    The expression sees COUNTS, never raw text: `answer_chars`, `answer_words`,
    `chunk_count`, `tool_call_count`, `retrieval_top`, `retrieval_mean`, plus any counts
    the pack's own patterns produce. Handing it the answer string would make it a text
    processor with a sandbox around it, which is the thing this tier exists to avoid.
    """

    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC, C.RETURNS_METRIC})

    def __init__(self, *, key: str, property: str, label: str, expr: str,
                 metric_name: str = "value",
                 requires: Tuple[str, ...] = ("answer",),
                 subject_is_model_output: bool = False,
                 gate: Optional[float] = None, higher_is_better: bool = True):
        self.key = key
        self.property = property
        self.label = label or key
        self.requires = frozenset(requires)
        self.subject_is_model_output = subject_is_model_output
        self._metric = metric_name
        self._gate = gate
        self._higher = higher_is_better
        if not isinstance(expr, str) or not expr.strip():
            raise DeclarativeSpecError(f"{key}: metric_expr must be a non-empty string")
        self._expr = expr
        # Compile-check now, so a broken expression refuses the PACK rather than
        # abstaining silently on every turn forever.
        from trusted_data_agent.core.safe_expression import safe_eval
        try:
            safe_eval(self._expr, {k: 1.0 for k in _EXPR_NAMES})
        except Exception as exc:  # noqa: BLE001
            raise DeclarativeSpecError(f"{key}: metric_expr is not evaluable ({exc})")

    def assess(self, ctx: AssessmentContext) -> Finding:
        from trusted_data_agent.core.safe_expression import safe_eval
        vars_ = _expr_context(ctx)
        try:
            value = float(safe_eval(self._expr, vars_))
        except Exception as exc:  # noqa: BLE001
            return abstain(self.property, self.key, f"could not be computed: {exc}")
        triggered = False
        if self._gate is not None:
            triggered = (value < self._gate) if self._higher else (value > self._gate)
        return Finding(
            property=self.property, assessor=self.key, triggered=triggered,
            severity=Severity.CONCERN if triggered else Severity.NONE,
            summary=f"{self._metric} = {value:.3g}",
            metrics={self._metric: value})


_EXPR_NAMES = ("answer_chars", "answer_words", "question_words", "chunk_count",
               "tool_call_count", "retrieval_top", "retrieval_mean", "scope_count")


def _expr_context(ctx: AssessmentContext) -> Dict[str, float]:
    scores = list(ctx.retrieval_scores or ())
    words = len((ctx.answer or "").split())
    return {
        "answer_chars": float(len(ctx.answer or "")),
        "answer_words": float(words),
        "question_words": float(len((ctx.question or "").split())),
        "chunk_count": float(len(ctx.chunks or ())),
        "tool_call_count": float(len(ctx.tool_calls or ())),
        "retrieval_top": float(max(scores)) if scores else 0.0,
        "retrieval_mean": float(sum(scores) / len(scores)) if scores else 0.0,
        "scope_count": float(len(ctx.scope_databases or ())),
    }


def build(spec: Dict[str, Any], *, key: str, property: str, label: str,
          subject_is_model_output: bool = False,
          requires: Tuple[str, ...] = ("answer",)) -> Assessor:
    """One declarative assessor from its manifest entry. Raises on anything malformed."""
    if not isinstance(spec, dict):
        raise DeclarativeSpecError(f"{key}: spec must be an object")
    forms = [f for f in ("patterns", "metric_expr", "rubric") if spec.get(f)]
    if len(forms) != 1:
        raise DeclarativeSpecError(
            f"{key}: declare exactly one of patterns / metric_expr / rubric "
            f"(found {forms or 'none'})")

    if "patterns" in forms:
        return PatternAssessor(
            key=key, property=property, label=label, spec=spec["patterns"],
            subject_is_model_output=subject_is_model_output, requires=requires,
            field=spec.get("field", "answer"),
            summary_singular=spec.get("finding_noun", "match"),
            category=spec.get("finding_category", "pii"),
            span_label=spec.get("span_label", ""))

    if "metric_expr" in forms:
        return MetricExprAssessor(
            key=key, property=property, label=label, expr=spec["metric_expr"],
            metric_name=spec.get("metric_name", "value"), requires=requires,
            subject_is_model_output=subject_is_model_output,
            gate=spec.get("gate"), higher_is_better=spec.get("higher_is_better", True))

    # Rubrics are Phase 3 — a rubric assessor calls a model, so it needs the judge
    # machinery and its cost sink, not a new evaluator here.
    from .rubric import build_rubric
    return build_rubric(spec["rubric"], key=key, property=property, label=label,
                        requires=requires,
                        subject_is_model_output=subject_is_model_output)
