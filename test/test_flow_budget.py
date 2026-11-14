#!/usr/bin/env python3
"""
Unit tests for the Flow per-run token budget guard.

Verifies FlowExecutor halts before launching the next step once accumulated
tokens cross max_tokens_per_run, marks the run 'error' with a budget reason, and
never trips when no budget is set or the flow stays under it.

Stubs the inner _execute_node (so real token accumulation in _run_node happens)
and neutralises DB writes — no database required.

Usage:
    python test/test_flow_budget.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import flow_executor as fe  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def _node(nid, ntype="task_function"):
    return {"id": nid, "type": ntype, "label": nid, "data": {"expression": "1"}}


def _edge(src, tgt):
    return {"id": f"e-{src}-{tgt}", "source": src, "target": tgt, "type": "normal"}


def _linear_def():
    """start → A → B → C → end (each task node 'costs' 600 tokens)."""
    return {
        "nodes": [_node("start", "start"), _node("A"), _node("B"),
                  _node("C"), _node("end", "end")],
        "edges": [_edge("start", "A"), _edge("A", "B"),
                  _edge("B", "C"), _edge("C", "end")],
        "scopes": [],
        "global_params": {},
    }


async def _run_with_budget(budget, cost_per_node=600):
    """Run the linear flow with stubbed token-costing nodes; return (status, events, executed)."""
    orig_execute = fe._execute_node
    orig_update = fe.update_run_status
    orig_append = fe.append_node_run

    executed = []
    status_holder = {}

    async def fake_execute_node(node, state_ctx, user_id, run_id, event_emitter, **kwargs):
        executed.append(node["id"])
        if node.get("type") in ("start", "end", "merge"):
            return {"output": state_ctx}
        return {"output": "x", "tokens_input": cost_per_node, "tokens_output": 0}

    def fake_update(run_id, status, **kwargs):
        status_holder["status"] = status

    fe._execute_node = fake_execute_node
    fe.update_run_status = fake_update
    fe.append_node_run = lambda *a, **k: None
    try:
        fd = fe.FlowDefinition(_linear_def())
        events = []

        async def emit(event_type, payload):
            events.append((event_type, payload))

        ex = fe.FlowExecutor(fd, "user-1", "run-b", emit,
                            caller_session_id="s-1", node_session_mode="individual",
                            max_tokens_per_run=budget)
        await ex.run()
        return status_holder.get("status"), events, executed, ex
    finally:
        fe._execute_node = orig_execute
        fe.update_run_status = orig_update
        fe.append_node_run = orig_append


def test_budget_trips_and_halts():
    print("\n[budget — trips after the level that crosses it, halts the rest]")
    status, events, executed, ex = asyncio.run(_run_with_budget(1000, cost_per_node=600))
    # start(0) → A(600, under) → B(1200, OVER → halt before C)
    check("final status is 'error'", status == "error")
    check("A and B executed", "A" in executed and "B" in executed)
    check("C never executed (halted)", "C" not in executed)
    check("end never executed (halted)", "end" not in executed)
    err_events = [p for (t, p) in events if t == "flow_run_error"]
    check("emitted flow_run_error with budget_exceeded flag",
          bool(err_events) and err_events[-1].get("budget_exceeded") is True)


def test_under_budget_completes():
    print("\n[budget — generous budget completes normally]")
    status, events, executed, ex = asyncio.run(_run_with_budget(100000, cost_per_node=600))
    check("final status is 'complete'", status == "complete")
    check("all nodes executed", all(n in executed for n in ["A", "B", "C", "end"]))
    check("emitted flow_run_complete",
          any(t == "flow_run_complete" for (t, _) in events))


def test_no_budget_never_trips():
    print("\n[budget — None budget is unbounded]")
    status, events, executed, ex = asyncio.run(_run_with_budget(None, cost_per_node=10**9))
    check("final status is 'complete' despite huge cost", status == "complete")
    check("_over_budget() is False with no budget", ex._over_budget() is False)


def test_budget_from_settings():
    print("\n[budget — read from definition.settings when arg omitted]")
    d = _linear_def()
    d["settings"] = {"max_tokens_per_run": 777}
    fd = fe.FlowDefinition(d)

    async def emit(event_type, payload):
        return None

    ex = fe.FlowExecutor(fd, "user-1", "run-s", emit,
                        caller_session_id="s-1", node_session_mode="individual")
    check("settings budget picked up (777)", ex.max_tokens_per_run == 777)

    # Explicit arg overrides settings.
    ex2 = fe.FlowExecutor(fd, "user-1", "run-s2", emit,
                         caller_session_id="s-1", node_session_mode="individual",
                         max_tokens_per_run=50)
    check("explicit arg overrides settings (50)", ex2.max_tokens_per_run == 50)


def main():
    print("=" * 70)
    print("FLOW PER-RUN TOKEN BUDGET GUARD — TESTS")
    print("=" * 70)
    test_budget_trips_and_halts()
    test_under_budget_completes()
    test_no_budget_never_trips()
    test_budget_from_settings()
    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
