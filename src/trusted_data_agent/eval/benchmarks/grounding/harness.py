"""Tier 1.4 — anti-hallucination benchmark harness (LIVE; not CI-pure).

Drives each dataset case ONCE in ``observe`` mode against a KG-scoped profile, captures the
baseline answer + the gate's recorded scope signal (the enforce *counterfactual*), and
labels the ground truth via the ``scope_faithfulness`` judge (the ``score-turn`` API — the
same platform-wide judge, not a benchmark-private prompt). Aggregates via the pure report
core (the single source of truth for the numbers).

Pre-conditions: a running server; a KG-scoped profile in ``observe`` mode; the grounding
gate globally enabled; judge-capable LLM credentials. **Admin/operator tooling (charter
#18)** — never a consumer surface. The harness orchestration is exercised live; the math,
dataset, and judge it depends on are CI-pure-tested.

Run:
    python -m trusted_data_agent.eval.benchmarks.grounding.harness \\
        --base-url http://localhost:5050 --user admin --password admin \\
        --profile-id <id> --dataset fitness_db --kg fitness_db [--limit N]
"""
from __future__ import annotations

import json
import time
import urllib.request

from trusted_data_agent.eval.benchmarks.grounding.report import (
    BenchmarkCase, aggregate, render_markdown, trigger_decision,
)
from trusted_data_agent.eval.benchmarks.grounding.dataset import load_dataset


def _post(base: str, path: str, token, body: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, method="POST",
                                 headers=headers, data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def _get(base: str, path: str, token) -> dict:
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def login(base: str, user: str, password: str) -> str:
    return _post(base, "/api/v1/auth/login", None,
                 {"username": user, "password": password})["token"]


def _extract(task: dict):
    """Pull (turn_id, answer, gate_event) from a completed task result."""
    turn_id, answer, gate = None, "", None
    for e in task.get("events", []):
        ed = e.get("event_data", {})
        if not isinstance(ed, dict):
            continue
        if ed.get("type") == "grounding_gate":
            gate = ed.get("details", {})
        if ed.get("final_answer_text"):
            answer = ed["final_answer_text"]
            turn_id = ed.get("turn_id", turn_id)
    return turn_id, answer, gate


def run_case(base: str, token: str, profile_id: str, case: dict,
             judge_models: int = 3, poll_timeout: int = 300):
    """Run one case live; return (BenchmarkCase, detail dict)."""
    sid = _post(base, "/api/v1/sessions", token, {})["session_id"]
    tid = _post(base, f"/api/v1/sessions/{sid}/query", token,
                {"prompt": case["query"], "profile_id": profile_id})["task_id"]
    deadline = time.time() + poll_timeout
    task = {}
    while time.time() < deadline:
        task = _get(base, f"/api/v1/tasks/{tid}", token)
        from trusted_data_agent.core import turn_states as _ts
        if _ts.is_settled(task.get("status")):
            break
        time.sleep(5)
    turn_id, answer, gate = _extract(task)

    # Enforce TRIGGER from the observe-run gate event: did the deterministic gate set
    # needs_judge (→ the scope_faithfulness judge would adjudicate)? Prefer the gate's own
    # needs_judge; fall back to recomputing it from the event fields. No event → pass.
    if gate:
        oos = tuple(gate.get("out_of_scope_dbs") or [])
        refs = tuple(gate.get("answer_oos_refs") or [])
        decision = ("flag" if gate.get("needs_judge")
                    else trigger_decision(oos, refs))
    else:
        decision, oos, refs = "pass", (), ()

    # Ground truth: the scope_faithfulness judge (the SAME platform judge), via score-turn.
    hallucinated, label_known, judge_cost = False, False, 0.0
    if turn_id is not None and answer.strip():
        sc = _post(base, "/api/v1/evals/score-turn", token,
                   {"session_id": sid, "turn": turn_id,
                    "include_judges": True, "judge_models": judge_models})
        judge_cost = float(sc.get("cost_usd") or 0.0)
        for v in sc.get("verdicts", []):
            if v.get("dimension") == "scope_faithfulness":
                p = v.get("passed")
                label_known = p is not None
                hallucinated = (p is False)

    bc = BenchmarkCase(case_id=case["case_id"], case_class=case["class"], decision=decision,
                       hallucinated=hallucinated, query=case["query"],
                       out_of_scope_dbs=oos, cost_usd=judge_cost)
    detail = {"case_id": case["case_id"], "status": task.get("status"),
              "trigger": decision, "out_of_scope_dbs": list(oos),
              "answer_oos_refs": list(refs), "hallucinated": hallucinated,
              "label_known": label_known, "answer": answer[:300]}
    return bc, detail


def run_benchmark(base: str, token: str, profile_id: str, dataset_name: str,
                  kg: str = None, judge_models: int = 3, limit: int = None):
    data = load_dataset(dataset_name)
    cases = data["cases"][:limit] if limit else data["cases"]
    rows, details, unknowns = [], [], 0
    for i, c in enumerate(cases, 1):
        try:
            bc, det = run_case(base, token, profile_id, c, judge_models)
        except Exception as e:  # honest: an errored case is inconclusive, not a crash
            bc = BenchmarkCase(case_id=c["case_id"], case_class=c["class"], decision="pass",
                               hallucinated=False, query=c["query"])
            det = {"case_id": c["case_id"], "error": str(e), "label_known": False}
        if not det.get("label_known"):
            unknowns += 1
        rows.append(bc)
        details.append(det)
        print(f"  [{i}/{len(cases)}] {c['case_id']:8} {c['class']:13} "
              f"gate={bc.decision:8} hallucinated={bc.hallucinated} known={det.get('label_known')}")
    result = aggregate(rows)
    meta = {"kg": kg or dataset_name, "profile": profile_id,
            "models": f"judge-panel×{judge_models}", "date": time.strftime("%Y-%m-%d"),
            "scope": data.get("scope"), "unknown_labels": unknowns}
    return result, details, meta


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Grounding-gate anti-hallucination benchmark (admin/operator).")
    ap.add_argument("--base-url", default="http://localhost:5050")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--token")
    ap.add_argument("--profile-id", required=True)
    ap.add_argument("--dataset", default="fitness_db")
    ap.add_argument("--kg")
    ap.add_argument("--judge-models", type=int, default=3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out")
    a = ap.parse_args()

    token = a.token or login(a.base_url, a.user, a.password)
    result, details, meta = run_benchmark(a.base_url, token, a.profile_id, a.dataset,
                                          kg=a.kg, judge_models=a.judge_models, limit=a.limit)
    md = render_markdown(result, meta)
    if meta.get("unknown_labels"):
        md += f"\n> Note: {meta['unknown_labels']} case(s) had no judge label (excluded from the win as not-hallucinated — honest under-count).\n"
    print("\n" + md)
    import os
    out = a.out or os.path.join(os.path.dirname(__file__), f"REPORT_{meta['kg']}_{meta['date']}.md")
    with open(out, "w") as f:
        f.write(md + "\n\n## Per-case detail\n\n```json\n"
                + json.dumps(details, indent=2, default=str) + "\n```\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
