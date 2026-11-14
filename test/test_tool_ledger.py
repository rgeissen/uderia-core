"""The per-turn tool ledger — the ReAct lane reuses an identical successful call and disables a
tool that keeps failing, at the ONE chokepoint every ReAct tool call passes.

  [1] quiet outside a turn; TDA_* exempt; reuse can be switched off
  [2] reuse: an identical SUCCESSFUL call hands back the first result, a different call runs
  [3] a write between two reads invalidates every memoised read (count · insert · count)
  [4] breaker: N consecutive EXECUTION failures disable the tool for the turn; success resets;
      0 = off; the block is recovery-shaped
  [5] turn isolation: two concurrent turns never share a ledger
  [6] the chokepoint: through the real guarded coroutine — a reused call never reaches the tool,
      a failing tool is cut off after N, a platform refusal never counts as a failure
  [7] LangGraph's tool node runs a reply's tool calls concurrently (the "batched calls" pin)
  [8] the wiring: the chokepoint, the turn seam

CI-pure: no server, no network, no model. Run: python test/test_tool_ledger.py
"""
import asyncio
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.pop("TDA_TOOL_RESULT_REUSE", None)
os.environ.pop("TDA_TOOL_BREAKER_FAILURES", None)

from trusted_data_agent.core.config import APP_CONFIG            # noqa: E402
from trusted_data_agent.agent import turn_tool_ledger as L       # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


def _src(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


READ = ("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM db.t"})
OK = (json.dumps({"status": "success", "results": [{"n": 4812}]}), None)

print("\n[1] quiet outside a turn; framework tools exempt; reuse switchable")
check("no ledger: reuse is None", L.reuse(*READ) is None)
check("no ledger: breaker is None", L.breaker_block("base_readQuery") is None)
L.record_success(*READ, OK); L.record_failure("base_readQuery")       # no-ops, never raise
check("no ledger: stats empty", L.stats() == {})
tok = L.begin_turn()
L.record_success("TDA_FinalReport", {"x": 1}, OK)
check("TDA_* is never memoised", L.reuse("TDA_FinalReport", {"x": 1}) is None)
for _ in range(10):
    L.record_failure("TDA_Charting")
check("TDA_* never trips the breaker", L.breaker_block("TDA_Charting") is None)
L.end_turn(tok)
os.environ["TDA_TOOL_RESULT_REUSE"] = "0"
tok = L.begin_turn(); L.record_success(*READ, OK)
check("TDA_TOOL_RESULT_REUSE=0 switches reuse off", L.reuse(*READ) is None)
L.end_turn(tok); os.environ.pop("TDA_TOOL_RESULT_REUSE")

print("\n[2] reuse an identical successful call")
tok = L.begin_turn()
check("nothing to reuse before a call ran", L.reuse(*READ) is None)
L.record_success(*READ, OK)
check("the identical call hands back the first result", L.reuse(*READ) == OK)
check("argument order does not matter (one signature)",
      L.reuse("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM db.t"}) == OK)
check("a different statement is not a repeat", L.reuse("base_readQuery", {"sql": "SELECT 2"}) is None)
check("a different tool is not a repeat", L.reuse("base_tableList", READ[1]) is None)
check("the ledger counts what it reused", L.stats()["reused"] == 2)
L.end_turn(tok)
tok = L.begin_turn()
check("a new turn starts empty", L.reuse(*READ) is None)
L.end_turn(tok)

print("\n[3] a write invalidates every memoised read")
tok = L.begin_turn()
L.record_success(*READ, OK)
L.record_success("base_readQuery", {"sql": "INSERT INTO db.t VALUES (1)"}, ("done", None))
check("count · insert · count: the second count runs again", L.reuse(*READ) is None)
check("the write itself is never memoised",
      L.reuse("base_readQuery", {"sql": "INSERT INTO db.t VALUES (1)"}) is None)
L.record_success(*READ, OK)
L.record_success("base_writeQuery", {"sql": "SELECT 1"}, ("done", None))
check("a write-named tool invalidates too (by name when the statement reads)", L.reuse(*READ) is None)
check("is_write: by the statement", L.is_write("base_readQuery", {"sql": "DELETE FROM db.t"}) is True)
check("is_write: a read is not", L.is_write("base_readQuery", {"sql": "SELECT 1"}) is False)
L.end_turn(tok)

print("\n[4] the breaker")
_orig = getattr(APP_CONFIG, "TOOL_BREAKER_FAILURES", None)
check("the shipped ceiling is 4", L.breaker_failures() == 4)
tok = L.begin_turn()
for i in range(3):
    L.record_failure("base_readQuery")
check("three failures: still open", L.breaker_block("base_readQuery") is None)
L.record_failure("base_readQuery")
blk = L.breaker_block("base_readQuery")
check("the fourth trips it", isinstance(blk, dict) and blk.get("error") == "tool_disabled_for_turn", blk)
check("the block is recovery-shaped: status error, names the tool, says what to do",
      blk and blk.get("status") == "error" and "base_readQuery" in blk["error_message"]
      and "Answer with what you already have" in blk["error_message"], blk)
check("the block carries its reading", blk and blk["tool_breaker"] == {"tool_name": "base_readQuery", "failures": 4, "ceiling": 4}, blk)
check("another tool is untouched", L.breaker_block("base_tableList") is None)
check("stats name the tripped tool", L.stats()["tripped"] == ["base_readQuery"])
L.end_turn(tok)
tok = L.begin_turn()
for i in range(3):
    L.record_failure("base_readQuery")
L.record_success(*READ, OK)
L.record_failure("base_readQuery")
check("a success resets the streak (3 + ok + 1 = 1)", L.breaker_block("base_readQuery") is None)
L.end_turn(tok)
APP_CONFIG.TOOL_BREAKER_FAILURES = 0
tok = L.begin_turn()
for i in range(9):
    L.record_failure("base_readQuery")
check("ceiling 0 = off", L.breaker_block("base_readQuery") is None)
L.end_turn(tok)
APP_CONFIG.TOOL_BREAKER_FAILURES = 2
tok = L.begin_turn()
L.record_failure("x"); L.record_failure("x")
check("the config ceiling is read", L.breaker_block("x") is not None)
L.end_turn(tok)
if _orig is None:
    try:
        delattr(APP_CONFIG, "TOOL_BREAKER_FAILURES")
    except Exception:
        pass
else:
    APP_CONFIG.TOOL_BREAKER_FAILURES = _orig
os.environ["TDA_TOOL_BREAKER_FAILURES"] = "3"
check("the env ceiling is read when no config", L.breaker_failures() == 3)
os.environ.pop("TDA_TOOL_BREAKER_FAILURES")

print("\n[5] turn isolation")


async def _turn_a():
    tok = L.begin_turn()
    L.record_success(*READ, OK)
    await asyncio.sleep(0.05)
    out = L.reuse(*READ)
    L.end_turn(tok)
    return out


async def _turn_b():
    tok = L.begin_turn()
    await asyncio.sleep(0.02)
    out = L.reuse(*READ)
    L.end_turn(tok)
    return out


async def _both():
    return await asyncio.gather(asyncio.create_task(_turn_a()), asyncio.create_task(_turn_b()))


a, b = asyncio.run(_both())
check("turn A sees its own memo", a == OK)
check("turn B, concurrent, sees nothing of A", b is None)
check("after both, no ledger leaks onto the loop", L.stats() == {})

print("\n[6] through the real chokepoint")
from trusted_data_agent.agent import tool_enforcement as te   # noqa: E402

calls = []


async def original(**kw):
    calls.append(dict(kw))
    return json.dumps({"status": "success", "results": [{"n": len(calls)}]}), None


guarded = te._make_guarded_coroutine("base_readQuery", original, "sess-ledger", user_uuid="u")


async def _drive_reuse():
    tok = L.begin_turn()
    try:
        first = await guarded(sql="SELECT 1")
        second = await guarded(sql="SELECT 1")
        third = await guarded(sql="SELECT 2")
        return first, second, third
    finally:
        L.end_turn(tok)


first, second, third = asyncio.run(_drive_reuse())
check("the identical second call never reached the tool", len(calls) == 2, calls)
check("it got the first result, led by the reuse note",
      isinstance(second, tuple) and second[0].startswith("(Result reused") and '"n": 1' in second[0], second)
check("the first call's content is unchanged", '"n": 1' in first[0] and not first[0].startswith("("), first)
check("a different statement ran", '"n": 2' in third[0], third)

calls.clear()
_fail_n = {"n": 0}


async def failing(**kw):
    _fail_n["n"] += 1
    raise RuntimeError("Teradata error 3807: object does not exist")


guarded_f = te._make_guarded_coroutine("base_readQuery", failing, "sess-ledger2", user_uuid="u")


async def _drive_breaker():
    tok = L.begin_turn()
    try:
        outs = [await guarded_f(sql=f"SELECT {i}") for i in range(6)]
        return outs
    finally:
        L.end_turn(tok)


outs = asyncio.run(_drive_breaker())
check("the first four failures reached the tool and came back as errors",
      _fail_n["n"] == 4 and all(o[0].startswith("Error from ") for o in outs[:4]), (_fail_n, [o[0][:40] for o in outs]))
body5 = json.loads(outs[4][0])
check("the fifth call is the breaker's block, without a tool call", body5.get("error") == "tool_disabled_for_turn" and _fail_n["n"] == 4, body5)
check("and so is the sixth", json.loads(outs[5][0]).get("error") == "tool_disabled_for_turn")

_fail_n["n"] = 0


async def _drive_refusal():
    tok = L.begin_turn()
    try:
        outs = []
        for _ in range(6):   # a non-string sql is refused by the gate, never executed
            outs.append(await guarded_f(sql=123))
        return outs, L.stats()
    finally:
        L.end_turn(tok)


outs, st = asyncio.run(_drive_refusal())
check("a platform refusal never reaches the tool", _fail_n["n"] == 0)
check("… and never counts toward the breaker", st["tripped"] == [] and all("tool_disabled_for_turn" not in o[0] for o in outs), (st, outs[0][0][:80]))

check("_execution_failed: a raise the retries did not recover", te._execution_failed(("Error from base_readQuery: x", None), True))
check("_execution_failed: a server error payload", te._execution_failed((json.dumps({"status": "error", "error": "x"}), None), False))
check("_execution_failed: a success", not te._execution_failed((json.dumps({"status": "success"}), None), False))
check("_execution_failed: plain content", not te._execution_failed(("42 rows", None), False))

print("\n[7] LangGraph's tool node runs a reply's tool calls concurrently")
try:
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.prebuilt import ToolNode

    async def slow_a(x: int) -> str:
        await asyncio.sleep(0.3)
        return f"a{x}"

    async def slow_b(x: int) -> str:
        await asyncio.sleep(0.3)
        return f"b{x}"

    from langgraph.graph import StateGraph, MessagesState, START, END

    node = ToolNode([StructuredTool.from_function(coroutine=slow_a, name="slow_a", description="a"),
                     StructuredTool.from_function(coroutine=slow_b, name="slow_b", description="b")])
    g = StateGraph(MessagesState)
    g.add_node("tools", node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    app = g.compile()      # the node runs inside a graph, as create_react_agent runs it
    msg = AIMessage(content="", tool_calls=[{"name": "slow_a", "args": {"x": 1}, "id": "c1"},
                                            {"name": "slow_b", "args": {"x": 2}, "id": "c2"}])
    t0 = time.monotonic()
    res = asyncio.run(app.ainvoke({"messages": [msg]}))
    dt = time.monotonic() - t0
    outs = sorted(str(m.content) for m in res["messages"][1:])
    check("both tool calls answered", outs == ["a1", "b2"], outs)
    check("in parallel: two 0.3s tools finished well under 0.6s", dt < 0.5, f"{dt:.2f}s")
except Exception as e:  # noqa: BLE001
    check("langgraph tool node available", False, repr(e))

print("\n[8] the wiring")
_te = _src("src/trusted_data_agent/agent/tool_enforcement.py")
_es = _src("src/trusted_data_agent/agent/execution_service.py")
check("the chokepoint asks the breaker and the memo AFTER the gate",
      _te.index("sql_gate_before(tool_name, arguments, session_id, LANE_REACT)") < _te.index("_ledger.breaker_block(tool_name)") < _te.index("_ledger.reuse(tool_name, arguments)"))
check("the chokepoint records on the value the model will see",
      "_ledger.record_success(tool_name, arguments, _final)" in _te and "_ledger.record_failure(tool_name)" in _te)
check("a timeout counts as a failure", _te.index("except asyncio.TimeoutError:") < _te.index("_ledger.record_failure(tool_name)"))
check("the turn seam opens and closes the ledger beside the loop guard",
      "_tool_ledger.begin_turn()" in _es and "_tool_ledger.end_turn(_tool_ledger_token)" in _es)

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
