"""The ReAct lane bounds each data-tool call, and a whole-turn cap names what ran long.

Why (2026-09-03, production): a NIBRS join on the full extract ran past the
conversation engine's 300 s whole-turn cap and the user was told "The AI model did not
respond within 300 seconds … the Google provider". The model had answered in a second;
the warehouse was still working. The ReAct lane's MCP calls were never under the
adapter's per-call timeout at all (only the Optimize lane's were), so nothing shorter
than the whole-turn cap could fire, and nothing could say WHAT had run long.

CI-pure. Run: python3 test/test_react_tool_bound.py
"""
import asyncio
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


def t_per_call_bound():
    from trusted_data_agent.agent import tool_enforcement as te

    async def slow(**kw):
        await asyncio.sleep(0.4)
        return "content", None

    async def fast(**kw):
        return json.dumps({"status": "success", "rows": 1}), None

    te.tool_call_timeout = lambda: 0.05          # the bound is one function, read per call
    guarded = te._make_guarded_coroutine("base_readQuery", slow, "sess-1")
    content, artifact = asyncio.run(guarded(sql="SELECT COUNT(*) FROM NIBRS_Source_Domain.Victim v JOIN NIBRS_Source_Domain.Incident i ON i.INCNUM = v.INCNUM"))
    body = json.loads(content)
    check("a call past the bound returns the content_and_artifact shape", artifact is None and isinstance(body, dict), (content[:80], artifact))
    check("it is an error the agent can act on, not an exception", body.get("status") == "error" and body.get("timed_out") is True, body)
    check("it names the tool and the bound", "base_readQuery" in body.get("error", "") and "0s budget" in body.get("error", ""), body.get("error"))
    check("it says the DATABASE was still working, not the model", "still running on the database" in body.get("error", ""), body.get("error"))
    check("it is recovery-shaped: tells the agent to narrow, and how", "Narrow it" in body.get("error", "") and "full primary index" in body.get("error", ""), body.get("error"))
    check("it forbids estimating in place of an answer", "rather than estimating" in body.get("error", ""), body.get("error"))

    guarded_fast = te._make_guarded_coroutine("base_readQuery", fast, "sess-1")
    content, _ = asyncio.run(guarded_fast(sql="SELECT 1"))
    check("a call inside the bound is untouched", json.loads(content).get("status") == "success", content)


def t_bound_is_the_adapters():
    import importlib
    from trusted_data_agent.agent import tool_enforcement as te
    importlib.reload(te)
    os.environ["TDA_MCP_TOOL_TIMEOUT"] = "77"
    try:
        from trusted_data_agent.mcp_adapter import adapter
        expected = float(adapter._MCP_TOOL_TIMEOUT)
        check("the ReAct bound IS the adapter's TDA_MCP_TOOL_TIMEOUT (one number, both lanes)",
              te.tool_call_timeout() == expected, (te.tool_call_timeout(), expected))
    except Exception as exc:  # noqa: BLE001 — adapter import may be heavy in CI; the env contract still holds
        check("without the adapter the env var is the contract", te.tool_call_timeout() == 77.0, (str(exc)[:80], te.tool_call_timeout()))
    finally:
        os.environ.pop("TDA_MCP_TOOL_TIMEOUT", None)


def t_cap_message_names_what_ran():
    from trusted_data_agent.agent.engines.conversation_engine import turn_cap_message
    m = turn_cap_message(300, ("base_readQuery", time.time() - 250), "Google")
    check("with a tool in flight the cap names the tool", "base_readQuery" in m, m)
    check("… and how long it had been running", "250s and counting" in m or "249s and counting" in m or "251s and counting" in m, m)
    check("… and does NOT blame the model", "did not respond" not in m and "The model had answered" in m, m)
    check("… and is recovery-shaped", "Narrow the query" in m and "full key" in m, m)
    m2 = turn_cap_message(300, None, "Google")
    check("with nothing in flight the old provider message is kept (it is then true)", "did not respond within 300 seconds" in m2 and "Google" in m2, m2)


def t_agent_bookkeeping_is_wired():
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "agent", "conversation_agent.py")).read()
    check("the agent records the tool in flight on on_tool_start", "self.inflight_tool = (tool_name, time.time())" in src, "")
    check("… and clears it on on_tool_end", 'elif event_kind == "on_tool_end":\n                    self.inflight_tool = None' in src, "")
    check("… and initialises it so a turn with no tool call reads None", "self.inflight_tool = None  #" in src, "")
    eng = open(os.path.join(ROOT, "src", "trusted_data_agent", "agent", "engines", "conversation_engine.py")).read()
    check("the engine reads it at the cap", 'getattr(agent, "inflight_tool", None)' in eng, "")
    check("the old blanket provider blame no longer lives in the handler", eng.count("did not respond within") == 1, eng.count("did not respond within"))


def t_result_size_bound():
    """A ReAct tool result is re-sent on every later step; a SELECT * on a 1,500-column
    table cost one turn 2.88M input tokens under the time cap. The model-facing content
    is bounded with a recovery-shaped marker; the artifact half is never touched."""
    import asyncio, json, os
    from trusted_data_agent.agent import tool_enforcement as te
    big = "x" * 100_000
    out = te.bound_result_content("base_readQuery", big, limit=10_000)
    check("an over-long result is cut to the bound plus a marker", out.startswith("x" * 10_000) and "TRUNCATED by the platform" in out, len(out))
    check("the marker names the size seen and the size there was", "10,000 of 100,000" in out, out[-200:])
    check("the marker tells the agent what to do", "select only the columns" in out.lower() or "narrow the query" in out.lower(), out[-160:])
    check("a result within the bound is byte-identical", te.bound_result_content("t", "short") == "short", None)
    check("non-string content is untouched", te.bound_result_content("t", {"rows": [1]}) == {"rows": [1]}, None)
    os.environ["TDA_REACT_TOOL_RESULT_MAX_CHARS"] = "5000"
    try:
        check("the bound is configured by env", te.tool_result_max_chars() == 5000, te.tool_result_max_chars())
        check("the bound has a floor", (os.environ.__setitem__("TDA_REACT_TOOL_RESULT_MAX_CHARS", "10") or te.tool_result_max_chars()) >= 2000, te.tool_result_max_chars())
    finally:
        os.environ.pop("TDA_REACT_TOOL_RESULT_MAX_CHARS", None)
    # through the REAL guarded coroutine: content bounded, artifact whole
    async def original(**kw):
        return ("y" * 60_000, {"artifact": "whole"})
    guarded = te._make_guarded_coroutine("base_readQuery", original, "sess", user_uuid="u")
    content, artifact = asyncio.run(guarded(sql="SELECT 1"))
    check("the guarded call bounds the content half", len(content) < 60_000 and "TRUNCATED" in content, len(content))
    check("the guarded call leaves the artifact whole", artifact == {"artifact": "whole"}, artifact)


def t_turn_input_token_budget():
    """The loop re-sends its scratchpad on every step, so cost grows with the square of the
    steps and the TIME cap does not bound it. A per-turn input-token budget, asked before
    each model call, ends the run with a named stop that keeps what was produced."""
    import ast, os, inspect
    from trusted_data_agent.agent import conversation_agent as ca
    check("the budget has a generous default", ca.turn_input_token_budget() >= 100_000, ca.turn_input_token_budget())
    os.environ["TDA_REACT_TURN_INPUT_TOKEN_BUDGET"] = "12345"
    try:
        check("the budget is configured by env", ca.turn_input_token_budget() == 12345, ca.turn_input_token_budget())
    finally:
        os.environ.pop("TDA_REACT_TURN_INPUT_TOKEN_BUDGET", None)
    src = inspect.getsource(ca)
    check("the budget is asked at model start, before the call is paid for",
          "turn_input_token_budget()" in src and "on_chat_model_start" in src and src.index("on_chat_model_start") < src.index("_turn_budget = turn_input_token_budget()"), None)
    check("a stopped turn says so in the answer, naming the budget", "reached the per-turn budget of" in src, None)
    check("the stop is a named field on the result", '"token_budget_stopped": token_budget_stopped' in src, None)
    check("the stop tells the user how to recover", "fewer columns" in src and "a row limit, an aggregate" in src, None)


for fn in (t_per_call_bound, t_bound_is_the_adapters, t_cap_message_names_what_ran, t_agent_bookkeeping_is_wired, t_result_size_bound, t_turn_input_token_budget):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
