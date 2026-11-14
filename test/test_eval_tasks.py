"""WS3 — Task Transparency Classification Framework (CI-pure).

Run:
    PYTHONPATH=src python test/test_eval_tasks.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase, RunRecord            # noqa: E402
from trusted_data_agent.eval.flow_types import FlowRunRecord            # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for                  # noqa: E402
from trusted_data_agent.eval.tasks import classify_tasks, TASK_CLASSES  # noqa: E402
import trusted_data_agent.eval.scorers as _s                            # noqa: E402,F401

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _prov(*step_types, ts_base="2026-06-22T10:00:0"):
    """Build a minimal provenance chain from step_type names (sequential timestamps)."""
    return [{"step_type": st, "timestamp": f"{ts_base}{i}", "content_summary": st}
            for i, st in enumerate(step_types)]


def run_dim(case, turn):
    rec = FlowRunRecord(turn) if case.profile_class == "flow" else RunRecord(turn)
    return {v.dimension: v for v in (s.score(case, rec) for s in scorers_for(case.profile_class))}


# ── classification per profile class ────────────────────────────────────────────
def t_ideate():
    ts = classify_tasks(RunRecord({"profile_type": "llm_only",
        "provenance_chain": _prov("query_intake", "profile_resolve", "llm_call", "llm_response", "turn_complete")}))
    check("Ideate = 1 reasoning task", ts.total == 1 and ts.by_class()["reasoning"] == 1)
    check("Ideate has no parallelism", ts.parallel_efficiency is None)


def t_focus():
    ts = classify_tasks(RunRecord({"profile_type": "rag_focused",
        "provenance_chain": _prov("query_intake", "profile_resolve", "rag_search", "rag_results",
                                  "rag_synthesis", "llm_response", "turn_complete")}))
    check("Focus = retrieval + reasoning (2 tasks)",
          ts.total == 2 and ts.by_class()["retrieval"] == 1 and ts.by_class()["reasoning"] == 1)


def t_optimize_planning_collapse():
    # strategic_plan + 3 rewrite passes must collapse to ONE planning task; the
    # per-phase tactical_decision is NOT a task (FASTPATH routing, no LLM call).
    ts = classify_tasks(RunRecord({"profile_type": "tool_enabled",
        "provenance_chain": _prov("query_intake", "profile_resolve", "strategic_plan",
                                  "plan_rewrite", "plan_rewrite", "plan_rewrite",
                                  "tactical_decision", "tool_call", "tool_result", "turn_complete")}))
    bc = ts.by_class()
    check("Optimize planning collapses to 1 task", bc["planning"] == 1)
    check("tactical_decision is NOT a reasoning task (FASTPATH routing)", bc["reasoning"] == 0)
    check("Optimize total = 2 (planning + tool, no tactical)", ts.total == 2 and bc["tool"] == 1)


def _prov_named(*pairs):
    """Provenance steps with explicit (step_type, content_summary)."""
    return [{"step_type": st, "timestamp": f"2026-06-22T10:00:0{i}", "content_summary": cs}
            for i, (st, cs) in enumerate(pairs)]


def t_synthesis_and_scaffolding_tools():
    # TDA_FinalReport = answer synthesis → reasoning; data tool → tool; framework
    # scaffolding (TDA_SystemLog/TDA_CurrentDate) → not a task.
    ts = classify_tasks(RunRecord({"profile_type": "tool_enabled",
        "provenance_chain": _prov_named(
            ("strategic_plan", "Plan: 2 phases"),
            ("tactical_decision", "Phase 1: base_readQuery"),
            ("tool_call", "Call: base_readQuery"), ("tool_result", "Result: base_readQuery (ok)"),
            ("tool_call", "Call: TDA_CurrentDate"), ("tool_result", "Result: TDA_CurrentDate (ok)"),
            ("tactical_decision", "Phase 2: TDA_FinalReport"),
            ("tool_call", "Call: TDA_FinalReport"), ("tool_result", "Result: TDA_FinalReport (ok)"))}))
    bc = ts.by_class()
    check("final report classified as reasoning (synthesis)", bc["reasoning"] == 1)
    check("data tool classified as tool", bc["tool"] == 1)
    check("framework scaffolding (TDA_CurrentDate) not a task", ts.total == 3)
    check("matches planner+tool+synthesis shape", ts.summary() == "3 tasks (1 planning · 1 tool · 1 reasoning)")


def t_correction_is_rework():
    ts = classify_tasks(RunRecord({"profile_type": "tool_enabled",
        "provenance_chain": _prov("strategic_plan", "tool_call", "error:timeout",
                                  "self_correction", "tool_call", "tool_result")}))
    check("correction tasks captured (error + self_correction)", ts.by_class()["correction"] == 2)


def t_react_reasoning_supplement():
    # ReAct records tools in the chain but reasoning in conversation_agent_events.
    turn = {"profile_type": "conversation_with_tools",
            "provenance_chain": _prov("agent_tool_call", "agent_tool_result",
                                      "agent_tool_call", "agent_tool_result"),
            "conversation_agent_events": [
                {"type": "conversation_llm_step", "payload": {}},
                {"type": "conversation_llm_step", "payload": {}},
                {"type": "conversation_llm_step", "payload": {}}]}
    ts = classify_tasks(RunRecord(turn))
    check("ReAct counts tool tasks from chain", ts.by_class()["tool"] == 2)
    check("ReAct supplements reasoning from events", ts.by_class()["reasoning"] == 3)


def t_genie_coordination():
    ts = classify_tasks(RunRecord({"profile_type": "genie",
        "provenance_chain": _prov("coordinator_synthesis"),
        "genie_metadata": {"slaves_invoked": [{"tag": "FOC"}, {"tag": "OPT"}]}}))
    check("Genie counts each expert as a coordination task", ts.by_class()["coordination"] == 2)


# ── flow-agent parity + real parallelism ────────────────────────────────────────
def _flow(nodes, status="complete"):
    return {"status": status, "cost_usd": 0.01, "node_runs": nodes}


def t_flow_classification():
    ts = classify_tasks(FlowRunRecord(_flow([
        {"node_id": "n1", "node_type": "task_tool", "status": "success",
         "started_at": "2026-06-22T10:00:00", "completed_at": "2026-06-22T10:00:01"},
        {"node_id": "n2", "node_type": "task_llm", "status": "success",
         "started_at": "2026-06-22T10:00:02", "completed_at": "2026-06-22T10:00:03"},
        {"node_id": "end", "node_type": "end", "status": "success"}])))
    check("Flow classifies nodes (tool + reasoning), excludes end", ts.total == 2)
    check("Flow sequential nodes → no speedup", ts.parallel_efficiency is None)


def t_flow_parallel_efficiency():
    # two overlapping tool nodes (parallel branch) then one llm node.
    ts = classify_tasks(FlowRunRecord(_flow([
        {"node_id": "a", "node_type": "task_tool", "status": "success",
         "started_at": "2026-06-22T10:00:00", "completed_at": "2026-06-22T10:00:03"},
        {"node_id": "b", "node_type": "task_tool", "status": "success",
         "started_at": "2026-06-22T10:00:00.5", "completed_at": "2026-06-22T10:00:03.5"},
        {"node_id": "c", "node_type": "task_llm", "status": "success",
         "started_at": "2026-06-22T10:00:04", "completed_at": "2026-06-22T10:00:06"}])))
    check("Flow detects parallel branch (maxConc=2)", ts.max_concurrency == 2)
    check("Flow parallel_efficiency = 3 tasks / 2 waves = 1.5x", ts.parallel_efficiency == 1.5)
    check("Flow marks overlapping nodes parallel",
          sum(1 for t in ts.tasks if t.mode == "parallel") == 2)


# ── orchestrator structural parallelism (Optimize) ──────────────────────────────
def t_orchestrator_wave():
    turn = {"profile_type": "tool_enabled",
            "provenance_chain": _prov("strategic_plan", "tool_call", "tool_result"),
            "execution_trace": [{"action": {"tool_name": "TDA_SystemOrchestration",
                                            "orchestrator_type": "date_range",
                                            "wrapped_tool_calls": [{"tool": "x"}, {"tool": "y"}, {"tool": "z"}]}}]}
    ts = classify_tasks(RunRecord(turn))
    check("Orchestrator wave adds structural concurrency", ts.max_concurrency >= 3)
    check("Orchestrator yields a parallel speedup", ts.parallel_efficiency is not None and ts.parallel_efficiency > 1.0)


# ── scorer wiring + honesty ─────────────────────────────────────────────────────
def t_scorer_wiring():
    for cls in ("llm_only", "rag_focused", "tool_enabled", "genie", "conversation_with_tools", "flow"):
        check(f"tasks KPI present for {cls}", "tasks" in {s.dimension for s in scorers_for(cls)})
    check("steps (planner) only for Optimize",
          "steps" in {s.dimension for s in scorers_for("tool_enabled")}
          and all("steps" not in {s.dimension for s in scorers_for(c)}
                  for c in ("llm_only", "rag_focused", "genie", "conversation_with_tools", "flow")))
    par = {c for c in ("llm_only", "rag_focused", "tool_enabled", "genie", "conversation_with_tools", "flow")
           if "parallel_efficiency" in {s.dimension for s in scorers_for(c)}}
    check("parallel_efficiency only for parallel-capable classes + flow",
          par == {"tool_enabled", "genie", "flow"})


def t_tasks_banding_and_steps():
    # tasks gates by max_tasks; steps reads the planned phase count.
    opt = {"profile_type": "tool_enabled", "status": "success", "isValid": True,
           "final_summary_text": "ok",
           "raw_llm_plan": {"phases": [{"phase": 1}, {"phase": 2}]},
           "provenance_chain": _prov("strategic_plan", "tool_call", "tool_result",
                                     "tool_call", "tool_result")}
    res = run_dim(EvalCase(id="x", query="q", profile_class="tool_enabled",
                           assertions={"max_tasks": 2}), opt)
    check("steps = planned phase count (2)", res["steps"].score == 2.0)
    check("tasks gated by max_tasks: 3 tasks > 2 → danger/fail",
          res["tasks"].score == 3.0 and res["tasks"].band == "danger" and res["tasks"].passed is False)


def t_tasks_breakdown_evidence():
    # The composition split is exposed as structured evidence on the tasks verdict
    # (for the chip + Agent Performance UX) — for any applicable class.
    res = run_dim(EvalCase(id="x", query="q", profile_class="tool_enabled"),
                  {"profile_type": "tool_enabled", "status": "success", "isValid": True,
                   "final_summary_text": "ok",
                   "provenance_chain": _prov("strategic_plan", "tool_call", "tool_result",
                                             "tool_call", "tool_result")})
    check("tasks verdict carries breakdown evidence",
          res["tasks"].evidence == ["1 planning", "2 tool"])


def t_composition_aggregates_in_span():
    from trusted_data_agent.eval.types import Verdict, ScoreKind
    from trusted_data_agent.eval.online import build_quality_span
    from trusted_data_agent.eval.analysis import online_quality_by_agent

    def _span(parts):
        v = [Verdict(dimension="tasks", kind=ScoreKind.METRIC, passed=None,
                     score=float(sum(int(p.split()[0]) for p in parts)), evidence=parts)]
        return build_quality_span(session_id="s", turn_id=1, profile_class="tool_enabled",
                                  verdicts=v, sampled=True, anomalous=False,
                                  attestation={"signed": False}, started_at=0.0,
                                  profile_tag="OPT", profile_id="p", user_uuid="u")
    agg = online_quality_by_agent([_span(["1 planning", "3 tool"]), _span(["1 planning", "1 tool"])])
    comp = agg["OPT"]["task_composition"]
    check("span carries per-class task counts + by_agent averages them",
          comp == {"planning": 1.0, "tool": 2.0})


def t_parallel_efficiency_honest_abstain():
    res = run_dim(EvalCase(id="x", query="q", profile_class="tool_enabled"),
                  {"profile_type": "tool_enabled", "status": "success", "isValid": True,
                   "final_summary_text": "ok",
                   "provenance_chain": _prov("strategic_plan", "tool_call", "tool_result")})
    check("parallel_efficiency abstains when sequential (never fabricates 1.0)",
          res["parallel_efficiency"].passed is None and res["parallel_efficiency"].score is None)


def main():
    for fn in (t_ideate, t_focus, t_optimize_planning_collapse, t_synthesis_and_scaffolding_tools,
               t_correction_is_rework,
               t_react_reasoning_supplement, t_genie_coordination, t_flow_classification,
               t_flow_parallel_efficiency, t_orchestrator_wave, t_scorer_wiring,
               t_tasks_banding_and_steps, t_tasks_breakdown_evidence,
               t_composition_aggregates_in_span, t_parallel_efficiency_honest_abstain):
        fn()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    check("all TASK_CLASSES distinct", len(set(TASK_CLASSES)) == len(TASK_CLASSES))
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
