"""Eval runner — score run records, and run a dataset (pass^k) via a pluggable
executor.

The executor decouples scoring from execution: tests inject a fake executor that
returns synthetic run records; production injects one that drives the real
``execute_query`` pipeline. ``include_judges`` adds the (LLM) judge layer (W3.2);
by default only deterministic + metric scorers run (CI-pure).
"""
from __future__ import annotations

import logging

_log = logging.getLogger("quart.app")

import time
import inspect
from typing import Awaitable, Callable, Optional

from trusted_data_agent.eval.types import EvalCase, RunRecord, ScoreKind, Verdict
from trusted_data_agent.eval.scorer import scorers_for
from trusted_data_agent.eval import scorers as _scorers  # noqa: F401 (registers built-ins)
from trusted_data_agent.eval.aggregate import aggregate_run

# executor(case) -> run-record turn dict (sync or async)
Executor = Callable[[EvalCase], "dict | Awaitable[dict]"]
# judge_fn(case, run) -> list[Verdict] (async) — supplied by W3.2
JudgeFn = Callable[[EvalCase, RunRecord], Awaitable[list]]


async def score_run_record(case: EvalCase, run, *, include_judges: bool = False,
                           judge_fn: Optional[JudgeFn] = None) -> list[Verdict]:
    """Run every applicable scorer for a case against a run record.

    Flow-agent cases (``profile_class == "flow"``) wrap the run in a
    FlowRunRecord and dispatch only the flow scorers; everything else uses the
    turn-shaped RunRecord."""
    if case.profile_class == "flow":
        from trusted_data_agent.eval.flow_types import FlowRunRecord
        rr = run if isinstance(run, FlowRunRecord) else FlowRunRecord(
            run if isinstance(run, dict) else getattr(run, "raw", {}))
    else:
        rr = run if isinstance(run, RunRecord) else RunRecord(run)
    # The sync loop ALWAYS runs only deterministic + metric scorers. Judge verdicts
    # come solely from judge_fn — otherwise the judge scorers' abstaining sync
    # score() would duplicate every dimension the panel also returns.
    # ISOLATED per scorer. This was a bare comprehension, so ONE scorer raising lost every
    # other verdict on the turn — and because the online hook is fire-and-forget, such a
    # failure would surface only as a Trust Circle that never settles, with nothing in the
    # log connecting the two.
    #
    # HONEST SCOPE: this was found while chasing exactly that symptom, and it was NOT the
    # cause (the eval sink shows both reported turns scored all 20 dimensions; the real
    # fault was a temporal-dead-zone throw in the dial's renderer). It is a latent hole
    # that had not yet been hit, closed on its own merits rather than as that fix.
    #
    # The judge layer immediately below has had this guarantee from the start — "judges
    # must never break a run" — and the deterministic layer was simply never given it.
    #
    # A raising scorer becomes an abstention that NAMES the failure, rather than
    # vanishing: a dimension that disappears silently is indistinguishable from one that
    # was never applicable, which is the reading this codebase bans everywhere else.
    verdicts = []
    for _s in scorers_for(case.profile_class,
                          kinds=(ScoreKind.DETERMINISTIC, ScoreKind.METRIC)):
        try:
            verdicts.append(_s.score(case, rr))
        except Exception as _e:  # noqa: BLE001 — one scorer must never lose the pass
            _log.warning("scorer %s failed on case %s: %s", _s.dimension, case.id, _e,
                         exc_info=True)
            verdicts.append(Verdict(
                dimension=_s.dimension, kind=_s.kind, passed=None,
                explanation=f"could not be measured: {type(_e).__name__}: {_e}"))
    if include_judges and judge_fn is not None:
        try:
            verdicts.extend(await judge_fn(case, rr))
        except Exception as e:  # judges must never break a run
            verdicts.append(Verdict(dimension="judge_error", kind=ScoreKind.JUDGE,
                                    passed=None, explanation=f"judge layer failed: {e}"))
    return verdicts


async def _call_executor(executor: Executor, case: EvalCase) -> dict:
    res = executor(case)
    if inspect.isawaitable(res):
        res = await res
    return res or {}


async def run_dataset(store, dataset_id: str, executor: Executor, *, k: int = 1,
                      run_id: Optional[str] = None, baseline_run_id: Optional[str] = None,
                      include_judges: bool = False, judge_fn: Optional[JudgeFn] = None,
                      cohort: Optional[str] = None) -> tuple[str, dict]:
    """Execute each case k times, score every rep, persist, and aggregate (pass^k)."""
    ds = store.get_dataset(dataset_id)
    if not ds:
        raise ValueError(f"dataset '{dataset_id}' not found")
    cases = store.list_cases(dataset_id, cohort=cohort)
    run_id = run_id or f"run-{dataset_id}-{int(time.time())}"
    store.create_run(run_id, dataset_id, ds["version"], k=k, baseline_run_id=baseline_run_id)

    for case in cases:
        for rep in range(k):
            try:
                run_record = await _call_executor(executor, case)
            except Exception as e:
                run_record = {"status": "error", "isValid": False,
                              "error_message": f"executor failed: {e}",
                              "profile_type": case.profile_class}
            verdicts = await score_run_record(case, run_record,
                                              include_judges=include_judges, judge_fn=judge_fn)
            for v in verdicts:
                store.add_score(run_id, case.id, rep, v)

    summary = aggregate_run(store.scores_for_run(run_id), k, cases)
    store.finish_run(run_id, summary)
    return run_id, summary


def report_markdown(store, run_id: str) -> str:
    """Human-readable run report (mirrors the perf-harness convention)."""
    run = store.get_run(run_id)
    if not run or not run.get("summary"):
        return f"# Eval run {run_id}\n(no summary)\n"
    s = run["summary"]
    lines = [f"# Eval run `{run_id}`",
             f"- dataset `{run['dataset_id']}` v{run['dataset_version']}, k={s['k']}, cases={s['n_cases']}",
             f"- **pass^{s['k']} = {s['pass_k']:.2%}**  (95% CI {s['pass_k_ci'][0]:.2%}–{s['pass_k_ci'][1]:.2%})",
             f"- rep pass rate = {s['rep_pass_rate']:.2%}", ""]
    if s.get("per_profile_class"):
        lines.append("## pass^k by profile class")
        for cls, pk in sorted(s["per_profile_class"].items()):
            lines.append(f"- {cls}: {pk:.2%}")
        lines.append("")
    if s.get("per_cohort_pass_k"):
        lines.append("## pass^k by cohort")
        for coh, pk in sorted(s["per_cohort_pass_k"].items()):
            lines.append(f"- {coh}: {pk:.2%}")
        lines.append("")
    if s.get("per_dimension"):
        lines.append("## pass rate by dimension")
        for d, r in sorted(s["per_dimension"].items(), key=lambda x: x[1]):
            lines.append(f"- {d}: {r:.2%}")
    return "\n".join(lines) + "\n"
