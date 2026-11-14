#!/usr/bin/env python3
"""
Unit tests for the task_llm node retry wrapper.

Verifies _execute_llm_node retries _execute_llm_node_once on transient
RuntimeError, honours per-node `retries`, emits flow_node_retry events, and
re-raises after exhausting attempts.

Stubs _execute_llm_node_once and zeroes the backoff so tests are instant.

Usage:
    python test/test_flow_retry.py
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


async def _run(node_data, fail_times, raise_cls=RuntimeError):
    """Drive the retry wrapper with a stub that fails `fail_times` then succeeds."""
    orig_once = fe._execute_llm_node_once
    orig_backoff = fe.LLM_NODE_RETRY_BACKOFF
    fe.LLM_NODE_RETRY_BACKOFF = 0  # instant
    calls = {"n": 0}
    events = []

    async def fake_once(node, state_ctx, user_id, run_id, event_emitter, **kw):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise raise_cls(f"transient #{calls['n']}")
        return {"output": "ok", "tokens_input": 1, "tokens_output": 1}

    async def emit(event_type, payload):
        events.append((event_type, payload))

    fe._execute_llm_node_once = fake_once
    try:
        node = {"id": "n1", "type": "task_llm", "data": node_data}
        try:
            result = await fe._execute_llm_node(node, {}, "user-1", "run-1", emit)
            return ("ok", result, calls["n"], events)
        except Exception as exc:  # noqa: BLE001
            return ("raised", exc, calls["n"], events)
    finally:
        fe._execute_llm_node_once = orig_once
        fe.LLM_NODE_RETRY_BACKOFF = orig_backoff


def test_succeeds_after_transient_failure():
    print("\n[retry — default 1 retry recovers a single transient failure]")
    outcome, result, n_calls, events = asyncio.run(_run({}, fail_times=1))
    check("eventually succeeded", outcome == "ok")
    check("made exactly 2 attempts", n_calls == 2)
    check("emitted one flow_node_retry event",
          sum(1 for (t, _) in events if t == "flow_node_retry") == 1)


def test_first_try_success_no_retry():
    print("\n[retry — first-try success makes no retry, no event]")
    outcome, result, n_calls, events = asyncio.run(_run({}, fail_times=0))
    check("succeeded", outcome == "ok")
    check("exactly 1 attempt", n_calls == 1)
    check("no flow_node_retry event",
          not any(t == "flow_node_retry" for (t, _) in events))


def test_retries_zero_fails_fast():
    print("\n[retry — retries=0 preserves fail-fast]")
    outcome, exc, n_calls, events = asyncio.run(_run({"retries": 0}, fail_times=5))
    check("raised", outcome == "raised")
    check("exactly 1 attempt (no retry)", n_calls == 1)
    check("re-raised the RuntimeError", isinstance(exc, RuntimeError))


def test_exhausts_and_reraises():
    print("\n[retry — exhausts retries then re-raises last error]")
    outcome, exc, n_calls, events = asyncio.run(_run({"retries": 2}, fail_times=10))
    check("raised after exhausting", outcome == "raised")
    check("made retries+1 = 3 attempts", n_calls == 3)
    check("emitted 2 retry events (between the 3 attempts)",
          sum(1 for (t, _) in events if t == "flow_node_retry") == 2)
    check("final exception is the last transient", isinstance(exc, RuntimeError))


def test_invalid_retries_falls_back_to_default():
    print("\n[retry — non-int retries falls back to default]")
    outcome, result, n_calls, events = asyncio.run(_run({"retries": "abc"}, fail_times=1))
    check("default applied → recovered after 1 retry", outcome == "ok" and n_calls == 2)


def main():
    print("=" * 70)
    print("FLOW task_llm RETRY WRAPPER — TESTS")
    print("=" * 70)
    test_succeeds_after_transient_failure()
    test_first_try_success_no_retry()
    test_retries_zero_fails_fast()
    test_exhausts_and_reraises()
    test_invalid_retries_falls_back_to_default()
    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
