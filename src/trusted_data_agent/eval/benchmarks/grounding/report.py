"""Tier 1.4 — anti-hallucination benchmark: pure scoring & aggregation core.

Measures the production grounding system under judge-backed enforce (Tier 1.1). The
deterministic gate no longer blocks — in ``enforce`` it DEFERS to the ``scope_faithfulness``
judge whenever a scope TRIGGER fires (an out-of-scope tool call OR an out-of-scope identifier
surfaced in the answer — the oos08 class). So the thing scored here is the cheap deterministic
**trigger's coverage** of judge-confirmed drift: of all governance hallucinations (the
ground-truth judge label), what share does the trigger surface to the judge (recall), and
how efficiently (precision = judge-call efficiency). A drift the trigger misses is delivered
unflagged — the honest False-Negative limit; an over-trigger costs only a judge call, never a
wrong verdict (False Positive). Both are reported, not just the win (charter #19).

Single observe run yields the baseline answer + the gate's recorded trigger signal
(``needs_judge`` / its event fields) + the judge ground-truth label.

Pure: no I/O, no LLM. The CI-pure trusted core that BOTH the admin UI/API and the
externally-published markdown report render from, so the in-platform view and the cited
proof can never disagree (single source of truth).

See docs/roadmap/TIER_1.4_BENCHMARK.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

# Dataset case classes (the dataset's `class` field).
IN_SCOPE = "in_scope"
OUT_OF_SCOPE = "out_of_scope"
AMBIGUOUS = "ambiguous"
CASE_CLASSES = (IN_SCOPE, OUT_OF_SCOPE, AMBIGUOUS)

# Decisions that count as "the gate did NOT silently hand the user a hallucination".
# flag  = the deterministic trigger fired → the scope_faithfulness judge adjudicates (drift
#         is catchable). block/annotate = the judge's downstream rulings (kept for the pure
#         aggregation tests + the strict block-only view). 'pass' = delivered as-is.
CAUGHT_DECISIONS = frozenset({"flag", "block", "annotate"})


@dataclass(frozen=True)
class BenchmarkCase:
    """One scored benchmark query. ``decision`` is the grounding-gate signal from the observe
    run — under judge-backed enforce that is the deterministic TRIGGER (``flag`` = the judge
    would adjudicate, ``pass`` = delivered as-is). ``hallucinated`` is the ground-truth label
    from the ``scope_faithfulness`` judge (an independent oracle of governance drift)."""
    case_id: str
    case_class: str                       # in_scope | out_of_scope | ambiguous
    decision: str                         # pass | flag  (legacy: annotate | block)
    hallucinated: bool                    # judge ground truth
    query: str = ""
    out_of_scope_dbs: tuple = ()
    numeric_fidelity: Optional[float] = None
    cost_usd: float = 0.0


def trigger_decision(out_of_scope_dbs, answer_oos_refs=()) -> str:
    """The gate's enforce TRIGGER, recomputed from an ``observe``-run gate event.

    Under judge-backed enforce (Tier 1.1) the gate never blocks deterministically — it sets
    ``needs_judge`` and DEFERS to the ``scope_faithfulness`` judge whenever a scope trigger
    fires: an out-of-scope **tool call** OR an out-of-scope identifier surfaced in the
    **answer** (the oos08 governance-leak class the tool signal alone misses). This is the
    gate's own ``needs_judge`` derived from its event fields; the unit test **pins it to the
    live gate** so it cannot drift. ``flag`` = trigger fired; ``pass`` = no trigger. Numbers
    never trigger (numeric fidelity is observe-only)."""
    fired = bool(out_of_scope_dbs) or bool(answer_oos_refs)
    return "flag" if fired else "pass"


def _div(n: int, d: int) -> Optional[float]:
    return (n / d) if d else None


@dataclass(frozen=True)
class BenchmarkResult:
    total: int
    tp: int
    fp: int
    fn: int
    tn: int
    containment: Optional[float]                  # recall = TP/(TP+FN) — the headline catch rate
    precision: Optional[float]                    # TP/(TP+FP) — when it acts, how often it's right
    baseline_hallucination_rate: Optional[float]  # (TP+FN)/total — the ungated problem size
    delivered_unflagged_observe: Optional[float]  # baseline: all hallucinations reach the user unflagged
    delivered_unflagged_enforce: Optional[float]  # FN/total: only the uncaught reach the user unflagged
    withheld_rate: Optional[float]                # block-only hallucinations withheld / total (strict)
    per_class: dict
    total_cost_usd: float
    caught_decisions: tuple

    @property
    def reduction_points(self) -> Optional[float]:
        """Net effect: percentage-points drop in hallucinated answers reaching the user
        unflagged, observe → enforce."""
        if self.delivered_unflagged_observe is None or self.delivered_unflagged_enforce is None:
            return None
        return self.delivered_unflagged_observe - self.delivered_unflagged_enforce

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "matrix": {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn},
            "containment": self.containment,
            "precision": self.precision,
            "baseline_hallucination_rate": self.baseline_hallucination_rate,
            "delivered_unflagged_observe": self.delivered_unflagged_observe,
            "delivered_unflagged_enforce": self.delivered_unflagged_enforce,
            "reduction_points": self.reduction_points,
            "withheld_rate": self.withheld_rate,
            "per_class": self.per_class,
            "total_cost_usd": self.total_cost_usd,
            "caught_decisions": list(self.caught_decisions),
        }


def aggregate(cases: Iterable[BenchmarkCase],
              caught_decisions: Iterable[str] = CAUGHT_DECISIONS) -> BenchmarkResult:
    """Build the confusion matrix + headline metrics from scored cases.

    ``caught_decisions`` controls which gate verdicts count as "caught" (the gate acted to
    contain): default ``{block, annotate}`` (the user was not handed an unflagged
    hallucination). Pass ``{block}`` for the strict withheld-only view.
    """
    caught_decisions = frozenset(caught_decisions)
    tp = fp = fn = tn = 0
    block_caught_hall = 0
    total_cost = 0.0
    per_class: dict = {}

    for c in cases:
        caught = c.decision in caught_decisions
        total_cost += c.cost_usd or 0.0
        pc = per_class.setdefault(
            c.case_class, dict(n=0, caught=0, hallucinated=0, tp=0, fp=0, fn=0, tn=0))
        pc["n"] += 1
        pc["caught"] += int(caught)
        pc["hallucinated"] += int(c.hallucinated)
        if c.hallucinated and caught:
            tp += 1; pc["tp"] += 1
        elif (not c.hallucinated) and caught:
            fp += 1; pc["fp"] += 1
        elif c.hallucinated and not caught:
            fn += 1; pc["fn"] += 1
        else:
            tn += 1; pc["tn"] += 1
        if c.hallucinated and c.decision == "block":
            block_caught_hall += 1

    total = tp + fp + fn + tn
    return BenchmarkResult(
        total=total, tp=tp, fp=fp, fn=fn, tn=tn,
        containment=_div(tp, tp + fn),
        precision=_div(tp, tp + fp),
        baseline_hallucination_rate=_div(tp + fn, total),
        delivered_unflagged_observe=_div(tp + fn, total),
        delivered_unflagged_enforce=_div(fn, total),
        withheld_rate=_div(block_caught_hall, total),
        per_class=per_class,
        total_cost_usd=round(total_cost, 6),
        caught_decisions=tuple(sorted(caught_decisions)),
    )


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{round(x * 100):d}%"


def render_markdown(result: BenchmarkResult, meta: Optional[dict] = None) -> str:
    """Render the externally-published proof report (charter #19 — honest about limits)."""
    meta = meta or {}
    head = (f"KG: {meta.get('kg', '?')} · profile: {meta.get('profile', '?')} · "
            f"{result.total} cases · models: {meta.get('models', '?')} · {meta.get('date', '?')}")
    lines = [
        "# Grounding Gate — Anti-Hallucination Benchmark",
        "",
        head,
        "",
        "## Deterministic trigger as a recall filter for the `scope_faithfulness` judge (independent ground truth)",
        "",
        "| | judge: drifted | judge: faithful |",
        "|---|---|---|",
        f"| **trigger FIRES → judge adjudicates** | TP {result.tp} | FP {result.fp} |",
        f"| **trigger PASSES → delivered as-is** | FN {result.fn} | TN {result.tn} |",
        "",
        "## Headline",
        "",
        f"- **Trigger recall:** {_pct(result.containment)} — of all judge-confirmed drifts, this share reaches the judge to be caught",
        f"- **Trigger precision (judge-call efficiency):** {_pct(result.precision)} — when it fires, a real drift is present this often",
        f"- **Baseline drift rate:** {_pct(result.baseline_hallucination_rate)} — the ungated agent on this set",
        f"- **Net effect:** governance drift reaching the user unadjudicated **drops from "
        f"{_pct(result.delivered_unflagged_observe)} (observe) → {_pct(result.delivered_unflagged_enforce)} (judge-backed enforce)**"
        f" ({_pct(result.reduction_points)} reduction)",
        "",
        "## Limits (published, not hidden)",
        "",
        f"- **{result.fn} False Negative(s)** — drifts the deterministic trigger misses (delivered unflagged; the honest gap)",
        f"- **{result.fp} False Positive(s)** — faithful answers that triggered the judge (extra judge-call cost, never a wrong verdict)",
        "",
        "## Per-class",
        "",
        "| class | n | caught | hallucinated |",
        "|---|---|---|---|",
    ]
    for cls in CASE_CLASSES:
        pc = result.per_class.get(cls)
        if pc:
            lines.append(f"| {cls} | {pc['n']} | {pc['caught']} | {pc['hallucinated']} |")
    lines += [
        "",
        f"_Cost: ${result.total_cost_usd:.4f} · caught = {{{', '.join(result.caught_decisions)}}}_",
        "",
    ]
    return "\n".join(lines)
