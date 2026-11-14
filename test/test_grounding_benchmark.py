"""Tier 1.4 — anti-hallucination benchmark scoring core (CI-pure, 0-token, no LLM).

Validates the confusion-matrix + headline math that BOTH the admin UI/API and the
published proof report render from. Uses the exact 36-case set from the design mock so
the numbers stay pinned to what we publish.

Run:  python test/test_grounding_benchmark.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.benchmarks.grounding.report import (  # noqa: E402
    BenchmarkCase, aggregate, render_markdown,
    IN_SCOPE, OUT_OF_SCOPE, AMBIGUOUS,
)
from trusted_data_agent.eval.benchmarks.grounding.dataset import (  # noqa: E402
    load_dataset, validate_dataset, class_counts,
)

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _case(cid, cls, decision, hall, cost=0.0):
    return BenchmarkCase(case_id=cid, case_class=cls, decision=decision,
                         hallucinated=hall, cost_usd=cost)


def _mock_36():
    """The design-doc mock: TP=11, FP=1, FN=2, TN=22 (total 36)."""
    cases = []
    # 11 out-of-scope hallucinations the gate blocks → TP
    for i in range(11):
        cases.append(_case(f"oos{i}", OUT_OF_SCOPE, "block", True, cost=0.01))
    # 1 in-scope grounded answer the gate would block → FP (false block)
    cases.append(_case("fp0", IN_SCOPE, "block", False, cost=0.01))
    # 2 in-scope hallucinations the gate passes → FN (missed)
    cases += [_case("fn0", IN_SCOPE, "pass", True, 0.01), _case("fn1", IN_SCOPE, "pass", True, 0.01)]
    # 22 in-scope grounded answers the gate passes → TN
    for i in range(22):
        cases.append(_case(f"tn{i}", IN_SCOPE, "pass", False, cost=0.01))
    return cases


def t_confusion_matrix():
    r = aggregate(_mock_36())
    check("matrix: TP=11 FP=1 FN=2 TN=22", (r.tp, r.fp, r.fn, r.tn) == (11, 1, 2, 22))
    check("total = 36", r.total == 36)


def t_headline_numbers():
    r = aggregate(_mock_36())
    check("containment (recall) = 11/13", abs(r.containment - 11/13) < 1e-9)
    check("precision = 11/12", abs(r.precision - 11/12) < 1e-9)
    check("baseline hallucination rate = 13/36", abs(r.baseline_hallucination_rate - 13/36) < 1e-9)
    check("delivered unflagged observe = 13/36 (= baseline)", abs(r.delivered_unflagged_observe - 13/36) < 1e-9)
    check("delivered unflagged enforce = FN/total = 2/36", abs(r.delivered_unflagged_enforce - 2/36) < 1e-9)
    check("reduction_points = (13-2)/36", abs(r.reduction_points - 11/36) < 1e-9)
    check("withheld_rate (block-only hallucinations) = 11/36", abs(r.withheld_rate - 11/36) < 1e-9)
    check("cost summed = 36 * 0.01", abs(r.total_cost_usd - 0.36) < 1e-9)


def t_caught_policy():
    # annotate counts as caught by default; block-only policy reclassifies it
    cases = [_case("a", OUT_OF_SCOPE, "annotate", True), _case("b", OUT_OF_SCOPE, "block", True)]
    r_default = aggregate(cases)                       # caught = {block, annotate}
    r_strict = aggregate(cases, caught_decisions={"block"})
    check("default policy: annotate+block both caught → TP=2", r_default.tp == 2 and r_default.fn == 0)
    check("strict policy: annotate not caught → TP=1, FN=1", r_strict.tp == 1 and r_strict.fn == 1)
    check("withheld_rate is block-only regardless of policy", abs(r_default.withheld_rate - 0.5) < 1e-9)


def t_per_class():
    r = aggregate(_mock_36())
    check("per_class in_scope n = 25 (1 FP + 2 FN + 22 TN)", r.per_class[IN_SCOPE]["n"] == 25)
    check("per_class out_of_scope n = 11, all caught",
          r.per_class[OUT_OF_SCOPE]["n"] == 11 and r.per_class[OUT_OF_SCOPE]["caught"] == 11)
    check("per_class in_scope false-block (fp) = 1", r.per_class[IN_SCOPE]["fp"] == 1)


def t_edges():
    check("empty → zero matrix, None metrics", (lambda r: r.total == 0 and r.containment is None
          and r.precision is None and r.reduction_points is None)(aggregate([])))
    # all grounded + all pass → perfect (no hallucinations, no false blocks)
    allclean = aggregate([_case(f"c{i}", IN_SCOPE, "pass", False) for i in range(5)])
    check("all-clean: TN=5, containment None (no positives), no false blocks",
          allclean.tn == 5 and allclean.containment is None and allclean.fp == 0)
    # gate never acts but hallucinations exist → containment 0
    miss = aggregate([_case("m", OUT_OF_SCOPE, "pass", True)])
    check("never-acts on a hallucination → containment 0, FN=1", miss.containment == 0.0 and miss.fn == 1)


def t_render():
    md = render_markdown(aggregate(_mock_36()), meta={"kg": "fitness_db", "profile": "@OPTIM", "date": "2026-06-27"})
    check("render: has matrix + headline + limits + per-class sections",
          "TP 11" in md and "Trigger recall" in md and "Limits (published, not hidden)" in md and "Per-class" in md)
    check("render: names the scope_faithfulness oracle (not answer_groundedness)",
          "scope_faithfulness" in md and "answer_groundedness" not in md)
    check("render: net-effect line shows observe→judge-backed-enforce drop",
          "36% (observe)" in md and "6% (judge-backed enforce)" in md)
    check("render: limits name the FN/FP counts", "2 False Negative" in md and "1 False Positive" in md)
    check("to_dict round-trips matrix", aggregate(_mock_36()).to_dict()["matrix"] == {"tp": 11, "fp": 1, "fn": 2, "tn": 22})


def t_trigger_pinned_to_gate():
    # The benchmark's `trigger_decision` must equal the live gate's enforce TRIGGER
    # (needs_judge) — so the published matrix scores exactly what production does.
    from trusted_data_agent.agent.grounding.gate import GroundingGate
    from trusted_data_agent.agent.grounding.types import GroundingConfig, GroundingInputs
    from trusted_data_agent.eval.benchmarks.grounding.report import trigger_decision
    scope = frozenset({"fitness_db"})

    def call(db):
        return {"arguments": {"database_name": db, "sql": "SELECT 1"}}

    scenarios = [
        # label,                tool_calls,                answer,                          data
        ("in scope",            (call("fitness_db"),),     "ok",                            "rows=5"),
        ("out-of-scope call",   (call("finance_ledger"),), "ok",                            "rows=5"),
        ("answer-ref drift",    (call("fitness_db"),),     "see finance_ledger.gl_entry",   "rows=5"),
        ("prose db drift",      (call("fitness_db"),),     "in the 'restaurants_mvm_test' database",  "rows=5"),
        ("ungrounded number",   (call("fitness_db"),),     "avg 230",                       "count 5"),
        ("grounded number",     (call("fitness_db"),),     "5 orders",                      "count 5"),
    ]
    gate = GroundingGate(GroundingConfig(mode="enforce"))
    drift = []
    for label, calls, ans, data in scenarios:
        v = gate.evaluate(GroundingInputs(answer_text=ans, scope_databases=scope,
                                          tool_calls=calls, grounding_text=data))
        td = trigger_decision(v.out_of_scope_dbs, v.answer_oos_refs)
        gate_trigger = "flag" if v.needs_judge else "pass"
        if td != gate_trigger:
            drift.append(f"{label}: gate_trigger={gate_trigger} helper={td}")
    check("trigger_decision is PINNED to the real gate's needs_judge (no drift)", not drift)
    for d in drift:
        print(f"       {d}")
    check("trigger: out-of-scope tool call → flag", trigger_decision(["finance_ledger"], ()) == "flag")
    check("trigger: answer-ref drift only → flag", trigger_decision([], ("finance_db",)) == "flag")
    check("trigger: clean → pass", trigger_decision([], ()) == "pass")


def t_dataset_fitness_db():
    data = load_dataset("fitness_db")
    check("dataset: loads + validates", validate_dataset(data) is True)
    check("dataset: declares scope ['fitness_db']", data["scope"] == ["fitness_db"])
    cc = class_counts(data)
    check("dataset: class balance 12/18/6 (6 drift-eliciting oos cases added)",
          (cc[IN_SCOPE], cc[OUT_OF_SCOPE], cc[AMBIGUOUS]) == (12, 18, 6))
    check("dataset: 36 cases, unique ids", len(data["cases"]) == 36
          and len({c["case_id"] for c in data["cases"]}) == 36)
    check("dataset: every case has a non-empty query + notes",
          all(c["query"].strip() and c.get("notes", "").strip() for c in data["cases"]))


def t_dataset_validation_rejects_bad():
    bad = [
        ({"cases": []}, "empty cases"),
        ({"scope": ["x"], "cases": [{"case_id": "a", "class": "bogus", "query": "q"}]}, "invalid class"),
        ({"scope": ["x"], "cases": [{"case_id": "a", "class": "in_scope"}]}, "missing query"),
        ({"scope": ["x"], "cases": [{"case_id": "a", "class": "in_scope", "query": "q"},
                                    {"case_id": "a", "class": "in_scope", "query": "q2"}]}, "duplicate id"),
    ]
    ok = True
    for data, why in bad:
        try:
            validate_dataset(data); ok = False; print(f"       did not reject: {why}")
        except AssertionError:
            pass
    check("dataset validator rejects malformed datasets (empty/badclass/missing/dupe)", ok)


def main():
    for t in (t_confusion_matrix, t_headline_numbers, t_caught_policy, t_per_class, t_edges, t_render,
              t_trigger_pinned_to_gate, t_dataset_fitness_db, t_dataset_validation_rejects_bad):
        print(f"\n{t.__name__}:")
        t()
    print(f"\n{'='*48}\nGrounding benchmark core: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
