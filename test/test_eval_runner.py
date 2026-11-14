"""WS3 W3.3 — eval runner (pass^k), regression gate, and trace mining.

CI-pure: a fake executor returns synthetic run records, so the runner/gate/
aggregation are exercised end-to-end with no LLM and no app. Mining is tested
against a real (temp) session backend (WS1) to prove the tie-in.

Run:
    PYTHONPATH=src python test/test_eval_runner.py
"""
import os
import sys
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase  # noqa: E402
from trusted_data_agent.eval.store import EvalStore  # noqa: E402
from trusted_data_agent.eval.runner import run_dataset, report_markdown  # noqa: E402
from trusted_data_agent.eval.gate import gate_run  # noqa: E402

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run(coro):
    return _loop.run_until_complete(coro)


_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


# synthetic run records keyed by intent
def _good_optimize():
    return {"profile_type": "tool_enabled", "status": "success", "isValid": True,
            "final_summary_text": "ok",
            "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {}},
                                 "result": {"tool_output_summary": {"status": "success"}}}],
            "provenance_meta": {"sealed": True}, "provenance_chain": [{"step_type": "tool_call"}],
            "turn_cost": 0.01}


def _bad_optimize():
    return {"profile_type": "tool_enabled", "status": "success", "isValid": True,
            "final_summary_text": "deleted",
            "execution_trace": [{"action": {"tool_name": "base_writeQuery", "arguments": {}},
                                 "result": {"tool_output_summary": {"status": "success"}}}],
            "provenance_meta": {"sealed": True}, "provenance_chain": [{"step_type": "tool_call"}],
            "turn_cost": 0.01}


def _seed_dataset(store):
    store.create_dataset("core", "Core suite")
    store.add_case(EvalCase(id="c-core-1", query="read", profile_class="tool_enabled", cohort="core",
                            dataset_id="core",
                            assertions={"require_tools_subset": ["base_readQuery"]}))
    store.add_case(EvalCase(id="c-risky-1", query="safe?", profile_class="tool_enabled", cohort="risky",
                            dataset_id="core",
                            assertions={"require_tools_subset": ["base_readQuery"],
                                        "must_not_call": ["base_writeQuery"]}))


def _executor_factory(mapping):
    async def _exec(case):
        return mapping.get(case.id, _good_optimize)()  # default = the function, not a call
    return _exec


def t_runner_passk():
    store = EvalStore(Path(tempfile.mkdtemp(prefix="tda_eval_run_")) / "e.db")
    _seed_dataset(store)
    # all good → pass^k = 1.0
    ex = _executor_factory({"c-core-1": _good_optimize, "c-risky-1": _good_optimize})
    rid, summ = run(run_dataset(store, "core", ex, k=3, run_id="r-good"))
    check("runner: pass^k = 1.0 when all reps pass", summ["pass_k"] == 1.0)
    check("runner: k recorded", summ["k"] == 3)
    check("runner: per-cohort pass^k present", set(summ["per_cohort_pass_k"]) == {"core", "risky"})
    check("runner: per-profile-class present", "tool_enabled" in summ["per_profile_class"])
    check("runner: markdown report renders", "pass^3" in report_markdown(store, "r-good"))

    # risky cohort fails (calls forbidden tool) → its pass^k = 0
    ex2 = _executor_factory({"c-core-1": _good_optimize, "c-risky-1": _bad_optimize})
    rid2, summ2 = run(run_dataset(store, "core", ex2, k=2, run_id="r-bad"))
    check("runner: core cohort still 1.0", summ2["per_cohort_pass_k"]["core"] == 1.0)
    check("runner: risky cohort 0.0 (forbidden tool)", summ2["per_cohort_pass_k"]["risky"] == 0.0)
    return store


def t_gate():
    store = EvalStore(Path(tempfile.mkdtemp(prefix="tda_eval_gate_")) / "e.db")
    _seed_dataset(store)
    run(run_dataset(store, "core", _executor_factory({"c-core-1": _good_optimize, "c-risky-1": _good_optimize}),
                    k=1, run_id="baseline"))
    run(run_dataset(store, "core", _executor_factory({"c-core-1": _good_optimize, "c-risky-1": _bad_optimize}),
                    k=1, run_id="candidate"))

    g_ok = gate_run(store, "baseline", min_pass_k=0.9)
    check("gate: clean baseline passes", g_ok["passed"] is True)

    g_reg = gate_run(store, "candidate", baseline_run_id="baseline")
    check("gate: per-cohort regression blocks", g_reg["passed"] is False)
    check("gate: failure names the risky cohort",
          any("risky" in f for f in g_reg["failures"]))

    g_floor = gate_run(store, "candidate", cohort_thresholds={"risky": 0.9})
    check("gate: per-cohort absolute floor blocks", g_floor["passed"] is False)

    # over-fit smell warning on a perfect ≥10-case run
    store.create_dataset("big", "Big")
    for i in range(12):
        store.add_case(EvalCase(id=f"b{i}", query="q", profile_class="llm_only", cohort="default",
                                dataset_id="big", assertions={}))
    run(run_dataset(store, "big", _executor_factory({}), k=1, run_id="perfect"))
    g_perfect = gate_run(store, "perfect", min_pass_k=0.5)
    check("gate: 100% on >=10 cases warns over-fit",
          any("over-fit" in w or "contamination" in w for w in g_perfect["warnings"]))


def t_mining():
    # Isolate a real file session backend (WS1) and seed a failed turn.
    tmp = tempfile.mkdtemp(prefix="tda_eval_mine_")
    os.environ["TDA_SESSION_BACKEND"] = "file"
    os.environ["TDA_SESSIONS_DIR"] = tmp
    from trusted_data_agent.core.session_backends import reset_session_backend
    from trusted_data_agent.core import session_manager as sm
    reset_session_backend(None)
    run(sm._init_session_index())
    body = {
        "id": "sessX", "user_uuid": "u1", "name": "s", "profile_type": "tool_enabled",
        "created_at": "2026-06-21T00:00:00", "last_turn_data": {"workflow_history": [
            {"turn": 1, "user_query": "do the thing", "status": "error", "isValid": True,
             "error_message": "boom", "profile_type": "tool_enabled",
             "execution_trace": [{"action": {"tool_name": "base_readQuery"},
                                  "result": {"tool_output_summary": {"status": "error"}}}]},
            {"turn": 2, "user_query": "happy path", "status": "success", "isValid": True,
             "profile_type": "tool_enabled"},
        ]},
    }
    run(sm.save_session("u1", "sessX", body))

    from trusted_data_agent.eval.mining import mine_cases_from_sessions
    store = EvalStore(Path(tmp) / "eval.db")
    n = run(mine_cases_from_sessions(store, "mined-ds", user_uuid="u1", only_anomalous=True))
    check("mining: seeds the failed turn as a case", n == 1)
    cases = store.list_cases("mined-ds")
    check("mining: mined case carries source_turn_ref",
          cases and cases[0].source_turn_ref and cases[0].source_turn_ref["session_id"] == "sessX")
    check("mining: mined case prefilled require_tools_subset from the trace",
          cases and "base_readQuery" in cases[0].assertions.get("require_tools_subset", []))


def main():
    print("== WS3 runner + gate + mining (W3.3 / W3.0) ==")
    for t in (t_runner_passk, t_gate, t_mining):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
