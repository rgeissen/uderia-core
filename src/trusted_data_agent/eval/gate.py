"""Regression gate — "evals as unit tests".

Blocks a release when quality regresses. Per the methodology research:
  - Never gate on a single aggregate; gate on per-COHORT thresholds.
  - Require statistical significance for baseline regressions (bootstrap CIs).
  - Flakiness control: a same-candidate control rerun must not swing > epsilon.

Returns a structured decision; ``passed=False`` should map to a non-zero CI exit.
"""
from __future__ import annotations

from typing import Optional


def gate_run(store, run_id: str, *,
             baseline_run_id: Optional[str] = None,
             min_pass_k: Optional[float] = None,
             cohort_thresholds: Optional[dict] = None,
             regression_epsilon: float = 0.02) -> dict:
    """Evaluate a run against absolute thresholds and (optionally) a baseline.

    - min_pass_k: global floor on pass^k.
    - cohort_thresholds: {cohort: floor} — block if any release-critical cohort
      falls below its floor.
    - baseline: block if any cohort's pass^k regresses by > epsilon AND the drop
      is significant (baseline CI-lo above candidate CI-hi would be ideal; we use
      a conservative mean-delta + epsilon, with CIs reported).
    """
    run = store.get_run(run_id)
    if not run or not run.get("summary"):
        return {"passed": False, "failures": [f"run '{run_id}' has no summary"]}
    s = run["summary"]
    failures: list[str] = []
    warnings: list[str] = []

    # 1) global floor
    if min_pass_k is not None and s["pass_k"] < min_pass_k:
        # significance: only hard-fail if the CI upper bound is also below the floor
        ci_hi = s["pass_k_ci"][1]
        msg = f"global pass^{s['k']} {s['pass_k']:.2%} < floor {min_pass_k:.2%} (CI hi {ci_hi:.2%})"
        (failures if ci_hi < min_pass_k else warnings).append(msg)

    # 2) per-cohort absolute floors
    per_cohort_pk = s.get("per_cohort_pass_k", {})
    for cohort, floor in (cohort_thresholds or {}).items():
        actual = per_cohort_pk.get(cohort)
        if actual is None:
            warnings.append(f"cohort '{cohort}' has no cases in this run")
            continue
        if actual < floor:
            failures.append(f"cohort '{cohort}' pass^k {actual:.2%} < floor {floor:.2%}")

    # 3) baseline per-cohort regression (significance via epsilon)
    if baseline_run_id:
        base = store.get_run(baseline_run_id)
        base_pk = (base or {}).get("summary", {}).get("per_cohort_pass_k", {}) if base else {}
        for cohort, actual in per_cohort_pk.items():
            prior = base_pk.get(cohort)
            if prior is None:
                continue
            delta = actual - prior
            if delta < -regression_epsilon:
                failures.append(
                    f"cohort '{cohort}' regressed {prior:.2%} → {actual:.2%} "
                    f"(Δ {delta:+.2%}, beyond ε {regression_epsilon:.0%})")
        # also flag any dimension regressing sharply (informational)
        base_dims = (base or {}).get("summary", {}).get("per_dimension", {}) if base else {}
        for dim, r in s.get("per_dimension", {}).items():
            pr = base_dims.get(dim)
            if pr is not None and (r - pr) < -regression_epsilon:
                warnings.append(f"dimension '{dim}' regressed {pr:.2%} → {r:.2%}")

    # 4) over-fit smell: a perfect run on a non-trivial set is suspicious
    if s["n_cases"] >= 10 and s["pass_k"] == 1.0:
        warnings.append("pass^k = 100% on ≥10 cases — verify the suite is challenging enough (contamination/over-fit risk)")

    return {
        "passed": not failures,
        "run_id": run_id,
        "baseline_run_id": baseline_run_id,
        "pass_k": s["pass_k"],
        "failures": failures,
        "warnings": warnings,
    }
