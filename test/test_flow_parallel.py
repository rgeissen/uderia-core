#!/usr/bin/env python3
"""
Unit tests for Flow parallel (dependency-level) execution.

Covers:
  - _build_execution_levels       — scope-aware topological layering
  - _partial_execution_levels     — node-only layering for partial runs
  - End-to-end concurrency proof   — independent branches actually overlap
  - Ordering / sequential parity   — linear flows keep their exact order

Usage:
    python test/test_flow_parallel.py
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


def _diamond_def():
    """start → B, start → C, B → D, C → D, D → end."""
    return {
        "nodes": [_node("start", "start"), _node("B"), _node("C"),
                  _node("D"), _node("end", "end")],
        "edges": [_edge("start", "B"), _edge("start", "C"),
                  _edge("B", "D"), _edge("C", "D"), _edge("D", "end")],
        "scopes": [],
        "global_params": {},
    }


def _linear_def():
    """start → A → B → end."""
    return {
        "nodes": [_node("start", "start"), _node("A"), _node("B"), _node("end", "end")],
        "edges": [_edge("start", "A"), _edge("A", "B"), _edge("B", "end")],
        "scopes": [],
        "global_params": {},
    }


# ── Level builder ───────────────────────────────────────────────────────────────

def test_levels_linear():
    print("\n[levels — linear flow is one unit per level]")
    fd = fe.FlowDefinition(_linear_def())
    levels = fe._build_execution_levels(fd)
    check("4 levels", len(levels) == 4)
    check("each level has 1 unit", all(len(lv) == 1 for lv in levels))
    order = [uid for lv in levels for (_, uid) in lv]
    check("topological order start..end", order == ["start", "A", "B", "end"])


def test_levels_diamond():
    print("\n[levels — diamond fans out then back in]")
    fd = fe.FlowDefinition(_diamond_def())
    levels = fe._build_execution_levels(fd)
    flat = [[uid for (_, uid) in lv] for lv in levels]
    check("level 0 = [start]", flat[0] == ["start"])
    check("level 1 = [B, C] (parallel, sorted)", flat[1] == ["B", "C"])
    check("level 2 = [D]", flat[2] == ["D"])
    check("level 3 = [end]", flat[3] == ["end"])


def test_levels_scope_collapse():
    print("\n[levels — scoped nodes collapse to one scope unit]")
    d = _diamond_def()
    d["scopes"] = [{"id": "scope-1", "name": "loop", "node_ids": ["B", "C"],
                    "max_iterations": 3}]
    fd = fe.FlowDefinition(d)
    levels = fe._build_execution_levels(fd)
    units = [u for lv in levels for u in lv]
    scope_units = [u for u in units if u[0] == "scope"]
    check("exactly one scope unit", len(scope_units) == 1)
    check("scope unit id is scope-1", scope_units and scope_units[0][1] == "scope-1")
    check("B and C not present as standalone node units",
          not any(u == ("node", "B") or u == ("node", "C") for u in units))


def test_partial_levels():
    print("\n[partial levels — node-only, restricted to targets]")
    fd = fe.FlowDefinition(_diamond_def())
    targets = {"B", "C", "D"}
    levels = fe._partial_execution_levels(fd, targets)
    flat = [[uid for (_, uid) in lv] for lv in levels]
    check("B and C share the first level", sorted(flat[0]) == ["B", "C"])
    check("D in its own later level", flat[-1] == ["D"])
    check("start/end excluded",
          all(uid not in ("start", "end") for lv in levels for (_, uid) in lv))


# ── End-to-end concurrency proof ────────────────────────────────────────────────

async def _drive_concurrency(definition):
    """Run a flow with _run_node stubbed to measure peak concurrency.

    Concurrency is opt-in (settings.parallelExecution): serialize is the default
    so a shared session stays coherent. This proof exercises the parallel path,
    so it enables the setting; the linear case still cannot overlap regardless.
    """
    definition = dict(definition)
    definition["settings"] = {**definition.get("settings", {}), "parallelExecution": True}
    # Neutralise DB writes / status updates for an isolated executor test.
    orig_update = fe.update_run_status
    fe.update_run_status = lambda *a, **k: None
    try:
        fd = fe.FlowDefinition(definition)

        async def _noop_emit(event_type, payload):
            return None

        ex = fe.FlowExecutor(fd, "user-1", "run-test", _noop_emit,
                             caller_session_id="s-1", node_session_mode="individual")

        state = {"active": 0, "peak": 0}

        async def fake_run_node(node_id, scope_id, iteration):
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.05)
            state["active"] -= 1
            return {"node_id": node_id, "status": "success"}

        ex._run_node = fake_run_node
        await ex.run()
        return state["peak"]
    finally:
        fe.update_run_status = orig_update


def test_concurrency_diamond():
    print("\n[concurrency — diamond middle runs B and C in parallel]")
    peak = asyncio.run(_drive_concurrency(_diamond_def()))
    check(f"peak concurrency >= 2 (got {peak})", peak >= 2)


def test_concurrency_linear():
    print("\n[concurrency — linear flow never overlaps]")
    peak = asyncio.run(_drive_concurrency(_linear_def()))
    check(f"peak concurrency == 1 (got {peak})", peak == 1)


def main():
    print("=" * 70)
    print("FLOW PARALLEL EXECUTION — LEVEL BUILDER + CONCURRENCY TESTS")
    print("=" * 70)
    test_levels_linear()
    test_levels_diamond()
    test_levels_scope_collapse()
    test_partial_levels()
    test_concurrency_diamond()
    test_concurrency_linear()
    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
