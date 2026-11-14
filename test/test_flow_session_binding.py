#!/usr/bin/env python3
"""
Tests for Flow session binding.

Two parts:
  1. _resolve_node_session — the per-node session-resolution matrix, including
     the priority-0 `session_context="new"` branch that lets a single node opt
     out of an in-session (compact) run.
  2. Handler wiring — FlowComponentHandler._handle_execute passes
     caller_session_id (from context) and node_session_mode (from flow settings)
     into execute_flow, so LLM-triggered runs bind to the chat session like the
     REST path.

Usage:
    python test/test_flow_session_binding.py
"""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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


# ── Part 1: resolution matrix ───────────────────────────────────────────────────

def test_resolution_matrix():
    print("\n[_resolve_node_session — priority matrix]")
    r = fe._resolve_node_session

    # Priority 0: "new" beats compact+caller (the whole point of the fix).
    check("new + compact + caller → fresh (None)",
          r({"session_context": "new"}, "compact", "sess-A") is None)
    check("new + individual → fresh (None)",
          r({"session_context": "new"}, "individual", "sess-A") is None)
    check("new + caller fallback → fresh (None)",
          r({"session_context": "new"}, "weird", "sess-A") is None)

    # Priority 1: compact + caller → pin to caller.
    check("compact + caller → caller",
          r({}, "compact", "sess-A") == "sess-A")

    # Priority 2: individual → fresh per node, regardless of caller.
    check("individual + caller → fresh (None)",
          r({}, "individual", "sess-A") is None)
    check("individual + no caller → fresh (None)",
          r({}, "individual", None) is None)

    # Priority 3: pinned + explicit session_id → that session.
    check("pinned + session_id → that session",
          r({"session_context": "pinned", "session_id": "sess-X"}, "compact", None) == "sess-X")
    check("pinned without session_id falls through to caller",
          r({"session_context": "pinned"}, "compact", "sess-A") == "sess-A")

    # Priority 4/5: caller fallback, else fresh.
    check("non-standard mode + caller → caller (fallback)",
          r({}, "weird", "sess-A") == "sess-A")
    check("compact + no caller → fresh (None)",
          r({}, "compact", None) is None)


# ── Part 2: handler wiring ──────────────────────────────────────────────────────

def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "flow_handler_under_test", ROOT / "components" / "builtin" / "flow" / "handler.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FlowComponentHandler


def test_handler_passes_session_binding():
    print("\n[handler _handle_execute — passes caller_session_id + node_session_mode]")
    import trusted_data_agent.core.flow_executor as fe_mod
    import trusted_data_agent.core.flow_db as fdb_mod

    orig_execute = fe_mod.execute_flow
    orig_get = fdb_mod.get_flow
    captured = {}

    async def fake_execute_flow(**kwargs):
        captured.update(kwargs)
        return "run-xyz"

    fe_mod.execute_flow = fake_execute_flow
    # Flow whose settings request individual mode → handler should forward it.
    fdb_mod.get_flow = lambda flow_id, user: {
        "flow_id": flow_id, "name": "T",
        "definition": {"settings": {"node_session_mode": "individual"}},
    }
    try:
        Handler = _load_handler()
        h = Handler()
        # The handler gained await-to-completion after this suite was written:
        # _handle_execute now polls _await_run_result(run_id) to a terminal status —
        # and the fake execute_flow writes no run row, so the poll runs the full
        # FLOW_EXECUTE_WAIT_TIMEOUT and the suite read as a hang. The subject HERE is
        # the session-binding kwargs, not the poll (which is bounded in production and
        # returns an honest "timeout"); stub it terminal.
        async def _fake_await(run_id, user_uuid):
            return {"status": "complete", "outputs": [],
                    "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}
        h._await_run_result = _fake_await
        ctx = {"session_id": "sess-123", "user_uuid": "u1", "event_handler": None}
        asyncio.run(h._handle_execute({"flow_id": "f1"}, "u1", ctx))
        check("caller_session_id forwarded from context", captured.get("caller_session_id") == "sess-123")
        check("node_session_mode forwarded from flow settings", captured.get("node_session_mode") == "individual")

        # Default settings → compact.
        captured.clear()
        fdb_mod.get_flow = lambda flow_id, user: {"flow_id": flow_id, "name": "T", "definition": {}}
        asyncio.run(h._handle_execute({"flow_id": "f1"}, "u1", ctx))
        check("defaults to compact when no setting", captured.get("node_session_mode") == "compact")
        check("still binds caller session by default", captured.get("caller_session_id") == "sess-123")

        # No session in context → None (graceful; falls back to new shared session downstream).
        captured.clear()
        asyncio.run(h._handle_execute({"flow_id": "f1"}, "u1", {"user_uuid": "u1"}))
        check("missing session_id → None (no crash)", captured.get("caller_session_id") is None)
    finally:
        fe_mod.execute_flow = orig_execute
        fdb_mod.get_flow = orig_get


def main():
    print("=" * 70)
    print("FLOW SESSION BINDING — RESOLUTION MATRIX + HANDLER WIRING")
    print("=" * 70)
    test_resolution_matrix()
    test_handler_passes_session_binding()
    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
