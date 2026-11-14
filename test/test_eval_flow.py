"""WS3 phase-2 — flow-agent evaluation (FlowRunRecord + flow scorers + dispatch).

A flow is a distinct *agent type* (not a profile class): it's graded as a whole
(run completed, nodes succeeded, required/forbidden nodes, cost, steps) regardless
of how many profiles its nodes used. CI-pure.

Run:
    PYTHONPATH=src python test/test_eval_flow.py
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval.types import EvalCase, ScoreKind  # noqa: E402
from trusted_data_agent.eval.flow_types import FlowRunRecord  # noqa: E402
from trusted_data_agent.eval.scorer import scorers_for  # noqa: E402
from trusted_data_agent.eval.runner import score_run_record  # noqa: E402
import trusted_data_agent.eval.scorers as _scorers  # noqa: E402,F401 (registers)

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _run(status="complete", nodes=None, cost=0.02, trace=None):
    return {"run_id": "frun-1", "flow_id": "flow-1", "status": status, "cost_usd": cost,
            "tokens_input": 100, "tokens_output": 50, "output_result": "done",
            "execution_trace": trace or [], "node_runs": nodes or []}


def _node(node_id, status="success", node_type="task_tool", label=None, err=None):
    n = {"node_id": node_id, "node_type": node_type, "status": status}
    if label:
        n["label"] = label
    if err:
        n["error_msg"] = err
    return n


def _verdicts(case, run):
    return _loop.run_until_complete(score_run_record(case, run))


def _by_dim(verdicts):
    return {v.dimension: v for v in verdicts}


# ── record accessors ───────────────────────────────────────────────────────────
def t_record():
    r = FlowRunRecord(_run(nodes=[_node("a"), _node("b", "error", err="boom"),
                                  _node("c", "skipped")]))
    check("record: completed", r.completed is True)
    check("record: executed excludes skipped", len(r.executed_nodes()) == 2)
    check("record: failed nodes", len(r.failed_nodes()) == 1)
    check("record: error_message from failed node", r.error_message == "boom")
    check("record: cost", r.cost_usd == 0.02)
    check("record: tokens", r.total_tokens == 150)
    check("record: identifiers include id+type", "a" in r.executed_identifiers() and "task_tool" in r.executed_identifiers())
    cancelled = FlowRunRecord(_run(status="cancelled"))
    check("record: cancelled not valid", cancelled.is_valid is False and cancelled.completed is False)


# ── scorers ────────────────────────────────────────────────────────────────────
def t_completed():
    case = EvalCase(id="c", query="run flow", profile_class="flow", flow_id="flow-1")
    good = _by_dim(_verdicts(case, _run(nodes=[_node("a")])))
    check("flow_completed: clean run passes", good["flow_completed"].passed is True)
    check("flow_nodes_succeeded: clean run passes", good["flow_nodes_succeeded"].passed is True)
    bad = _by_dim(_verdicts(case, _run(status="error", nodes=[_node("a", "error", err="x")])))
    check("flow_completed: errored run fails", bad["flow_completed"].passed is False)
    check("flow_nodes_succeeded: node error fails", bad["flow_nodes_succeeded"].passed is False)


def t_nodes_reached():
    case = EvalCase(id="c", query="q", profile_class="flow", flow_id="flow-1",
                    assertions={"require_nodes": ["a", "b"]})
    ok = _by_dim(_verdicts(case, _run(nodes=[_node("a"), _node("b")])))
    check("flow_nodes_reached: subset present passes", ok["flow_nodes_reached"].passed is True)
    miss = _by_dim(_verdicts(case, _run(nodes=[_node("a")])))
    check("flow_nodes_reached: missing node fails", miss["flow_nodes_reached"].passed is False)
    # absent assertion -> abstain
    na = _by_dim(_verdicts(EvalCase(id="c2", query="q", profile_class="flow"), _run(nodes=[_node("a")])))
    check("flow_nodes_reached: no assertion -> abstain", na["flow_nodes_reached"].passed is None)


def t_forbidden():
    case = EvalCase(id="c", query="q", profile_class="flow", flow_id="flow-1",
                    assertions={"forbid_nodes": ["error_branch"]})
    wrong = _by_dim(_verdicts(case, _run(nodes=[_node("a"), _node("error_branch")])))
    check("flow_forbidden_nodes: wrong branch fails", wrong["flow_forbidden_nodes"].passed is False)
    right = _by_dim(_verdicts(case, _run(nodes=[_node("a")])))
    check("flow_forbidden_nodes: clean passes", right["flow_forbidden_nodes"].passed is True)


def t_cost_steps():
    case = EvalCase(id="c", query="q", profile_class="flow", flow_id="flow-1",
                    assertions={"max_cost_usd": 0.05, "max_steps": 2})
    trace = [{"node_id": "a"}, {"node_id": "b"}, {"node_id": "c"}]
    v = _by_dim(_verdicts(case, _run(cost=0.10, nodes=[_node("a")], trace=trace)))
    check("flow_cost: over ceiling fails", v["flow_cost"].passed is False)
    check("flow_efficiency: over step budget fails (3>2)", v["flow_efficiency"].passed is False)
    v2 = _by_dim(_verdicts(case, _run(cost=0.01, nodes=[_node("a")], trace=[{"node_id": "a"}])))
    check("flow_cost: under ceiling passes", v2["flow_cost"].passed is True)
    check("flow_efficiency: under budget passes", v2["flow_efficiency"].passed is True)


# ── dispatch isolation ─────────────────────────────────────────────────────────
def t_dispatch():
    flow_dims = {s.dimension for s in scorers_for("flow")}
    check("dispatch: flow gets flow scorers", "flow_completed" in flow_dims and "flow_nodes_succeeded" in flow_dims)
    check("dispatch: flow excludes turn scorers", "completed" not in flow_dims and "tool_subset" not in flow_dims)
    # Universal task KPIs cross over to flows (consistency across all agent types).
    check("dispatch: flow gets universal task KPIs", "tasks" in flow_dims and "parallel_efficiency" in flow_dims)
    tool_dims = {s.dimension for s in scorers_for("tool_enabled")}
    check("dispatch: tool_enabled excludes flow scorers", "flow_completed" not in tool_dims)
    check("dispatch: tool_enabled keeps cross-cutting", "completed" in tool_dims)
    # no judge scorers for flows
    check("dispatch: flows have no judges", not scorers_for("flow", kinds=[ScoreKind.JUDGE]))


def t_runner_wraps_flow():
    # score_run_record must wrap a raw dict in FlowRunRecord for a flow case
    case = EvalCase(id="c", query="q", profile_class="flow", flow_id="flow-1")
    verdicts = _verdicts(case, _run(nodes=[_node("a")]))
    dims = {v.dimension for v in verdicts}
    # Flow verdicts = flow_* scorers + the universal task KPIs; never turn-shaped scorers.
    _UNIVERSAL = {"tasks", "parallel_efficiency"}
    check("runner: flow case yields only flow + task-KPI verdicts",
          dims and all(d.startswith("flow_") or d in _UNIVERSAL for d in dims))


# ── online flow eval ───────────────────────────────────────────────────────────
def t_flow_span():
    from trusted_data_agent.eval import online
    case = EvalCase(id="c", query="q", profile_class="flow", flow_id="flow-1")
    verdicts = _verdicts(case, _run(nodes=[_node("a")]))
    att = online.sign_attestation("flow:frun-1", verdicts, "flow")
    span = online.build_flow_quality_span(
        run_id="frun-1", flow_id="flow-1", flow_name="My Flow", profile_tag="OPTIM",
        verdicts=verdicts, sampled=True, anomalous=False, attestation=att, started_at=1.0,
        user_uuid="u1")
    a = span["attributes"]
    check("flow span: name", span["name"] == "agent.flow.eval")
    check("flow span: agent.kind=flow", a["agent.kind"] == "flow")
    check("flow span: carries flow_id + user", a["agent.flow_id"] == "flow-1" and a["user.uuid"] == "u1")
    check("flow span: per-dimension attrs", any(k.startswith("eval.dim.flow_") for k in a))


def t_by_flow():
    from trusted_data_agent.eval.analysis import online_quality_by_flow, online_quality_by_agent
    spans = [
        {"attributes": {"agent.kind": "flow", "agent.flow_id": "flow-1", "agent.flow_name": "F1",
                        "eval.gating_failures": 0, "eval.dim.flow_completed.passed": True}},
        {"attributes": {"agent.kind": "flow", "agent.flow_id": "flow-1", "agent.flow_name": "F1",
                        "eval.gating_failures": 1, "eval.dim.flow_completed.passed": False}},
        {"attributes": {"agent.profile_tag": "OPTIM", "eval.gating_failures": 0}},  # a profile turn
    ]
    by_flow = online_quality_by_flow(spans)
    check("by_flow: flow-1 aggregated", by_flow["flow-1"]["turns"] == 2 and by_flow["flow-1"]["failures"] == 1)
    check("by_flow: pass_rate 0.5", by_flow["flow-1"]["pass_rate"] == 0.5)
    check("by_flow: per-dim flow_completed 0.5", by_flow["flow-1"]["dimensions"]["flow_completed"] == 0.5)
    # flow spans must NOT pollute the profile by_agent
    by_agent = online_quality_by_agent(spans)
    check("by_agent: excludes flow spans", "flow-1" not in by_agent and "OPTIM" in by_agent)


def t_flow_online_hook():
    from trusted_data_agent.eval import online
    from trusted_data_agent.core.config import APP_CONFIG
    import trusted_data_agent.core.flow_db as flow_db
    mem = online.MemoryExporter()
    online.set_exporter(mem)
    orig_enabled = APP_CONFIG.ONLINE_EVAL_ENABLED
    orig_get_run = getattr(flow_db, "get_run", None)
    orig_get_nodes = getattr(flow_db, "get_node_runs", None)
    try:
        APP_CONFIG.ONLINE_EVAL_ENABLED = True
        flow_db.get_run = lambda rid: _run(status="error", nodes=None)
        flow_db.get_node_runs = lambda rid: [_node("a", "error", err="boom")]
        r = _loop.run_until_complete(online.evaluate_flow_run_online(
            user_uuid="u1", flow_id="flow-1", run_id="frun-1", flow_name="F1", profile_tag="OPTIM"))
        check("flow hook: anomalous run sampled", r is not None and r["anomalous"] is True)
        check("flow hook: agent_kind flow", r.get("agent_kind") == "flow")
        check("flow hook: failed (errored run)", r["passed"] is False)
        check("flow hook: exported one flow span", len(mem.spans) == 1 and mem.spans[0]["name"] == "agent.flow.eval")
        # disabled -> no-op
        APP_CONFIG.ONLINE_EVAL_ENABLED = False
        r2 = _loop.run_until_complete(online.evaluate_flow_run_online(
            user_uuid="u1", flow_id="flow-1", run_id="frun-1"))
        check("flow hook: disabled -> None", r2 is None)
    finally:
        APP_CONFIG.ONLINE_EVAL_ENABLED = orig_enabled
        online.set_exporter(None)
        if orig_get_run:
            flow_db.get_run = orig_get_run
        if orig_get_nodes:
            flow_db.get_node_runs = orig_get_nodes


def main():
    print("== WS3 phase-2 flow-agent evaluation (CI-pure) ==")
    for t in (t_record, t_completed, t_nodes_reached, t_forbidden, t_cost_steps,
              t_dispatch, t_runner_wraps_flow, t_flow_span, t_by_flow, t_flow_online_hook):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
