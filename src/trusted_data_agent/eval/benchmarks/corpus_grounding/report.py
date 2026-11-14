"""RAG Trust Hardening — corpus-grounding benchmark: pure scoring & aggregation core.

Measures the Wave 1 corpus-support trigger (``agent/grounding/corpus.py``) — the knowledge
twin of the anti-hallucination benchmark (Tier 1.4). Where that benchmark needs a live
``scope_faithfulness`` judge to label ground truth, THIS one does not: the corpus trigger is
**deterministic given (answer, chunks, question)**, so the whole benchmark is CI-pure and
**reproducible offline with zero tokens** — a stronger proof than a judge-labelled run.

Three case classes, each pinning a different property of the trigger:

  * ``supported``     — the answer's claims ARE in the retrieved chunks → the trigger MUST
                        NOT fire. False positives here are the cost that erodes trust in an
                        enforce mode, so the false-positive rate is a headline number.
  * ``unsupported``   — the answer asserts specifics the chunks do NOT carry → the trigger
                        MUST fire (recall). A miss is a confident hallucination delivered
                        unflagged — the honest false-negative limit.
  * ``second_chance`` — unsupported on the FIRST retrieval, but a targeted second-chance
                        search of the corpus DOES support it → after second-chance the
                        trigger MUST NOT fire. This isolates the false-positive-killer's value.

Headline metrics (all reported, never just the win — charter #19):
  recall               = of ``unsupported`` cases, the fraction the trigger fired on.
  false_positive_rate  = of ``supported`` cases, the fraction the trigger WRONGLY fired on.
  second_chance_recovery = of ``second_chance`` cases, the fraction correctly cleared.
  net_precision        = TP / (TP + FP) across supported+unsupported (trigger quality).

Pure: no I/O, no LLM. The single source of truth the CI test and the published proof pack
both render from — the in-repo view and the cited numbers can never disagree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
SECOND_CHANCE = "second_chance"
CASE_CLASSES = (SUPPORTED, UNSUPPORTED, SECOND_CHANCE)


@dataclass(frozen=True)
class CorpusCase:
    """One scored benchmark case. ``triggered`` is the DETERMINISTIC corpus-trigger result
    (after any second-chance clearing); ``expected_trigger`` is the ground truth from the
    case class (unsupported → True, supported / second_chance → False)."""
    case_id: str
    case_class: str
    triggered: bool
    expected_trigger: bool
    unsupported_spans: Tuple[str, ...] = ()
    second_chance_cleared: Tuple[str, ...] = ()

    @property
    def correct(self) -> bool:
        return self.triggered == self.expected_trigger


def expected_for_class(case_class: str) -> bool:
    """Ground truth: only ``unsupported`` cases should trigger."""
    return case_class == UNSUPPORTED


def _div(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


@dataclass
class CorpusReport:
    total: int = 0
    by_class: dict = field(default_factory=dict)
    recall: Optional[float] = None
    false_positive_rate: Optional[float] = None
    second_chance_recovery: Optional[float] = None
    net_precision: Optional[float] = None
    accuracy: Optional[float] = None
    false_negatives: List[str] = field(default_factory=list)
    false_positives: List[str] = field(default_factory=list)


def aggregate(cases: Iterable[CorpusCase]) -> CorpusReport:
    cases = list(cases)
    r = CorpusReport(total=len(cases))
    for cls in CASE_CLASSES:
        r.by_class[cls] = {"n": 0, "correct": 0}

    tp = fp = 0
    n_unsupported = n_supported = n_secondchance = 0
    for c in cases:
        cell = r.by_class.setdefault(c.case_class, {"n": 0, "correct": 0})
        cell["n"] += 1
        cell["correct"] += 1 if c.correct else 0
        if c.case_class == UNSUPPORTED:
            n_unsupported += 1
            if c.triggered:
                tp += 1
            else:
                r.false_negatives.append(c.case_id)
        elif c.case_class == SUPPORTED:
            n_supported += 1
            if c.triggered:
                fp += 1
                r.false_positives.append(c.case_id)
        elif c.case_class == SECOND_CHANCE:
            n_secondchance += 1
            if c.triggered:
                fp += 1
                r.false_positives.append(c.case_id)

    r.recall = _div(tp, n_unsupported)
    r.false_positive_rate = _div(fp, n_supported + n_secondchance)
    r.second_chance_recovery = _div(
        sum(1 for c in cases if c.case_class == SECOND_CHANCE and not c.triggered),
        n_secondchance)
    r.net_precision = _div(tp, tp + fp)
    r.accuracy = _div(sum(1 for c in cases if c.correct), len(cases))
    return r


def render_markdown(report: CorpusReport, *, dataset_name: str = "corpus_v1") -> str:
    """The published report body — the same numbers the CI test asserts."""
    def pct(v):
        return "—" if v is None else f"{v*100:.1f}%"
    lines = [
        f"# Corpus-Grounding Benchmark — `{dataset_name}`",
        "",
        "> Deterministic, CI-pure, **zero-token**, reproducible offline. Measures the Wave 1 "
        "corpus-support trigger (`agent/grounding/corpus.py`) — the knowledge twin of the "
        "anti-hallucination benchmark (Tier 1.4). No live model, no server: the trigger is a "
        "pure function of (answer, retrieved chunks, question).",
        "",
        f"- **Cases:** {report.total}",
        f"- **Recall** (unsupported claims flagged): **{pct(report.recall)}** — a miss is a "
        "confident hallucination delivered unflagged (the honest false-negative limit).",
        f"- **False-positive rate** (supported answers wrongly flagged): **{pct(report.false_positive_rate)}** "
        "— the cost that would erode trust in enforce mode.",
        f"- **Second-chance recovery** (unsupported-on-first-retrieval, cleared by the targeted "
        f"corpus re-search): **{pct(report.second_chance_recovery)}** — the false-positive killer.",
        f"- **Net precision:** {pct(report.net_precision)} · **Accuracy:** {pct(report.accuracy)}",
        "",
        "| Class | n | Correct |",
        "|---|---:|---:|",
    ]
    for cls in CASE_CLASSES:
        cell = report.by_class.get(cls, {"n": 0, "correct": 0})
        lines.append(f"| `{cls}` | {cell['n']} | {cell['correct']}/{cell['n']} |")
    if report.false_negatives:
        lines += ["", f"**False negatives** (unsupported, missed): {', '.join(report.false_negatives)}"]
    if report.false_positives:
        lines += ["", f"**False positives** (supported, wrongly flagged): {', '.join(report.false_positives)}"]
    lines += ["", "_Reproduce:_ `python -m trusted_data_agent.eval.benchmarks.corpus_grounding.harness`"]
    return "\n".join(lines)
