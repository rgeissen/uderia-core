"""Aggregation statistics: pass^k reliability, per-cohort/per-dimension rollups,
and bootstrap confidence intervals.

A *rep* (one execution of a case) passes iff every GATING verdict that applies
(passed in {True, False}) is True — non-gating signals (plan_adherence,
grounded_overlap, chain_integrity) inform but never fail a rep. A *case* passes
pass^k iff ALL k reps passed (τ-bench reliability semantics).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional


def _gating_map() -> dict:
    """dimension -> gating(bool), from the registered scorers."""
    from trusted_data_agent.eval.scorer import all_scorers
    return {s.dimension: s.gating for s in all_scorers()}


def _bootstrap_ci(values: list[float], iters: int = 1000, seed: int = 12345) -> tuple:
    """Percentile bootstrap 95% CI for the mean of 0/1 (or real) values.

    Deterministic (fixed seed) so CI gating is reproducible. Pure-Python LCG to
    avoid importing numpy/random global state.
    """
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0.0)
    mean = sum(values) / n
    if n == 1:
        return (mean, mean, mean)
    state = seed & 0xFFFFFFFF
    def _rand_idx():
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state % n
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += values[_rand_idx()]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * (iters - 1))]
    hi = means[int(0.975 * (iters - 1))]
    return (mean, lo, hi)


def aggregate_run(scores: list[dict], k: int, cases: Optional[list] = None) -> dict:
    """Roll a flat list of score rows into a run summary.

    scores: rows from EvalStore.scores_for_run (dimension, kind, passed, score,
            case_id, rep_index, cohort?) — cohort comes from the cases list.
    cases:  list of EvalCase (for cohort + profile_class grouping).
    """
    gating = _gating_map()
    case_cohort = {c.id: c.cohort for c in (cases or [])}
    case_class = {c.id: c.profile_class for c in (cases or [])}

    # rep_pass[(case_id, rep)] = bool ; also collect per-dimension pass rows
    rep_gating = defaultdict(list)          # (case,rep) -> [bool]
    dim_rows = defaultdict(list)            # dimension -> [bool]
    dim_cohort_rows = defaultdict(list)     # (cohort,dimension) -> [bool]
    for row in scores:
        passed = row.get("passed")
        dim = row.get("dimension")
        if passed is None:
            continue
        key = (row.get("case_id"), row.get("rep_index", 0))
        if gating.get(dim, True):
            rep_gating[key].append(bool(passed))
        dim_rows[dim].append(bool(passed))
        coh = case_cohort.get(row.get("case_id"), "default")
        dim_cohort_rows[(coh, dim)].append(bool(passed))

    # per-case: did ALL k reps pass (pass^k) and did the mean rep pass (pass^1-ish)
    case_reps = defaultdict(dict)           # case -> {rep: rep_passed}
    for (case_id, rep), bools in rep_gating.items():
        case_reps[case_id][rep] = all(bools)
    # also include cases that produced reps but no gating verdicts → treat as pass
    all_case_ids = {row.get("case_id") for row in scores}
    for case_id in all_case_ids:
        reps_seen = {row.get("rep_index", 0) for row in scores if row.get("case_id") == case_id}
        for rep in reps_seen:
            case_reps[case_id].setdefault(rep, True)

    case_passk = {}     # case -> 1.0 if all reps passed else 0.0
    rep_pass_values = []
    for case_id, reps in case_reps.items():
        vals = list(reps.values())
        case_passk[case_id] = 1.0 if all(vals) else 0.0
        rep_pass_values.extend(1.0 if v else 0.0 for v in vals)

    passk_values = list(case_passk.values())
    passk_mean, passk_lo, passk_hi = _bootstrap_ci(passk_values)

    per_dimension = {d: round(sum(v) / len(v), 4) for d, v in dim_rows.items() if v}
    per_cohort = defaultdict(dict)
    for (coh, dim), v in dim_cohort_rows.items():
        if v:
            per_cohort[coh][dim] = round(sum(v) / len(v), 4)

    # per-profile-class case pass^k (so no class is a second-class citizen)
    per_class = defaultdict(list)
    for case_id, pk in case_passk.items():
        per_class[case_class.get(case_id, "unknown")].append(pk)
    per_class_passk = {c: round(sum(v) / len(v), 4) for c, v in per_class.items() if v}

    # per-cohort case pass^k (the gate blocks on per-cohort regression)
    per_cohort_pk = defaultdict(list)
    for case_id, pk in case_passk.items():
        per_cohort_pk[case_cohort.get(case_id, "default")].append(pk)
    per_cohort_passk = {c: round(sum(v) / len(v), 4) for c, v in per_cohort_pk.items() if v}

    return {
        "k": k,
        "n_cases": len(case_passk),
        "pass_k": round(passk_mean, 4),                 # all-k-reps-pass rate
        "pass_k_ci": [round(passk_lo, 4), round(passk_hi, 4)],
        "rep_pass_rate": round(sum(rep_pass_values) / len(rep_pass_values), 4) if rep_pass_values else 0.0,
        "per_dimension": per_dimension,
        "per_cohort": dict(per_cohort),
        "per_cohort_pass_k": per_cohort_passk,
        "per_profile_class": per_class_passk,
        "case_pass_k": case_passk,                       # case_id -> 0/1 (for diffing)
    }
