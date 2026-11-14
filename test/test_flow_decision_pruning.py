#!/usr/bin/env python3
"""
Regression test for Flow decision-branch pruning.

Before this fix, `_active_successors()` was dead code and `run()` executed every
topological node unconditionally — so BOTH branches of a decision ran regardless
of the condition (observed live: a decision evaluating False still executed its
True branch). This drives the real FlowExecutor.run() with `_run_node` stubbed
and asserts that only the matching branch executes, while shared downstream nodes
(End) still run, and non-decision flows skip nothing.

Usage:
    python test/test_flow_decision_pruning.py
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


def _decision_def():
    """start → count → decision → (true: reorder | false: healthy) → end."""
    return {
        "nodes": [
            {"id": "start", "type": "start", "label": "Start"},
            {"id": "count", "type": "task_llm", "label": "Count", "data": {"prompt": "x"}},
            {"id": "dec", "type": "decision", "label": "Any?", "data": {"condition": "c"}},
            {"id": "reorder", "type": "task_llm", "label": "Reorder", "data": {"prompt": "x"}},
            {"id": "healthy", "type": "task_llm", "label": "Healthy", "data": {"prompt": "x"}},
            {"id": "end", "type": "end", "label": "End"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "count", "type": "normal"},
            {"id": "e2", "source": "count", "target": "dec", "type": "normal"},
            {"id": "e3", "source": "dec", "target": "reorder", "type": "conditional", "condition": "true"},
            {"id": "e4", "source": "dec", "target": "healthy", "type": "conditional", "condition": "false"},
            {"id": "e5", "source": "reorder", "target": "end", "type": "normal"},
            {"id": "e6", "source": "healthy", "target": "end", "type": "normal"},
        ],
        "scopes": [],
        "global_params": {},
    }


async def _drive(definition, decision_results):
    """Run the real executor with _run_node stubbed; return (ran, trace)."""
    orig_update = fe.update_run_status
    captured = {}

    def _cap(run_id, status, **kw):
        captured.update(kw)
        captured["status"] = status

    fe.update_run_status = _cap
    try:
        fd = fe.FlowDefinition(definition)

        async def _noop_emit(event_type, payload):
            return None

        ex = fe.FlowExecutor(fd, "u", "r", _noop_emit,
                             caller_session_id="s", node_session_mode="individual")
        ran = []

        async def fake_run_node(node_id, scope_id, iteration):
            ran.append(node_id)
            node = fd.get_node(node_id) or {}
            if node.get("type") == "decision":
                cr = decision_results.get(node_id)
                ex.state.record_output(node_id, {"output": cr, "condition_result": cr})
            else:
                ex.state.record_output(node_id, {"output": f"out-{node_id}"})
            return {"node_id": node_id, "status": "success"}

        ex._run_node = fake_run_node
        await ex.run()
        return ran, captured.get("execution_trace", [])
    finally:
        fe.update_run_status = orig_update


def _skipped(trace):
    return {e["node_id"] for e in trace if e.get("status") == "skipped"}


def test_decision_true_prunes_false_branch():
    print("\n[decision TRUE — runs true branch, skips false branch]")
    ran, trace = asyncio.run(_drive(_decision_def(), {"dec": True}))
    check("true branch (reorder) ran", "reorder" in ran)
    check("false branch (healthy) did NOT run", "healthy" not in ran)
    check("healthy recorded as skipped", "healthy" in _skipped(trace))
    check("shared End still ran", "end" in ran)


def test_decision_false_prunes_true_branch():
    print("\n[decision FALSE — runs false branch, skips true branch]")
    ran, trace = asyncio.run(_drive(_decision_def(), {"dec": False}))
    check("false branch (healthy) ran", "healthy" in ran)
    check("true branch (reorder) did NOT run", "reorder" not in ran)
    check("reorder recorded as skipped", "reorder" in _skipped(trace))
    check("shared End still ran", "end" in ran)


def test_no_decision_skips_nothing():
    print("\n[linear flow — nothing is skipped]")
    lin = {
        "nodes": [{"id": "s", "type": "start"}, {"id": "a", "type": "task_llm", "data": {"prompt": "x"}},
                  {"id": "e", "type": "end"}],
        "edges": [{"id": "1", "source": "s", "target": "a", "type": "normal"},
                  {"id": "2", "source": "a", "target": "e", "type": "normal"}],
        "scopes": [], "global_params": {},
    }
    ran, trace = asyncio.run(_drive(lin, {}))
    check("all three nodes ran", set(ran) == {"s", "a", "e"})
    check("nothing skipped", _skipped(trace) == set())


def main():
    print("=" * 70)
    print("FLOW DECISION-BRANCH PRUNING — REGRESSION TESTS")
    print("=" * 70)
    test_decision_true_prunes_false_branch()
    test_decision_false_prunes_true_branch()
    test_no_decision_skips_nothing()
    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
