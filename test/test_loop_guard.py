"""The in-flight loop guard — Part D's row that saves the run rather than the reading.

Pure: no server, no network. Every case drives the real guard.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core.config import APP_CONFIG          # noqa: E402
from trusted_data_agent.agent import loop_guard as LG          # noqa: E402
from trusted_data_agent.agent.assurance.assessors.tool_repetition import (  # noqa: E402
    signature)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


_ORIG = dict(getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {}) or {})


def run(ceiling, calls):
    t = dict(_ORIG)
    t.pop("max_tool_repeats", None)
    if ceiling is not None:
        t["max_tool_repeats"] = ceiling
    APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = t
    tok = LG.begin_turn()
    out = [LG.check_and_record(n, a) for n, a in calls]
    LG.end_turn(tok)
    return out


SQL = ("base_readQuery", {"sql": "SELECT 1"})

print("\n[1] off unless configured — the shipped default is byte-identical")
check("no ceiling → nothing is blocked", all(b is None for b in run(None, [SQL] * 6)))

print("\n[2] it blocks the (N+1)th, never the retry")
r = run(2, [SQL] * 5)
check("the first two run", r[0] is None and r[1] is None)
check("…and the rest are blocked", all(b is not None for b in r[2:]))
# A self-correction retry carries the SAME signature. A guard tripping on the second call
# would break the platform's own recovery path.
check("a single retry is never blocked", run(2, [SQL, SQL])[1] is None)

print("\n[3] what is NOT a repeat")
check("an orchestrator iterating arguments runs freely",
      all(b is None for b in
          run(2, [("dba_resusage", {"date": f"2026-08-0{i}"}) for i in range(1, 7)])))
check("TDA_ framework tools are exempt (the agent's own reporting)",
      all(b is None for b in run(2, [("TDA_FinalReport", {"r": "x"})] * 5)))

print("\n[4] the block is RECOVERY-SHAPED, not a silent failure")
b = run(2, [SQL] * 3)[2]
check("it carries an error status", b.get("status") == "error")
check("…names the cause machine-readably", b.get("error") == "repeated_identical_call")
# A bare failure reads as a broken tool and invites a retry of the very call being stopped.
check("…and TELLS the agent it is repeating, so it can change course",
      "identical arguments" in (b.get("error_message") or "")
      and "change the arguments" in (b.get("error_message") or ""))
check("…and carries the counts for the trace",
      (b.get("loop_guard") or {}).get("repeats") == 3)

print("\n[5] fails quiet — a guard that cannot resolve its config invents no enforcement")
check("outside a turn nothing is blocked", LG.check_and_record(*SQL) is None)

print("\n[6] concurrent turns never share a ledger")


async def _concurrent():
    async def turn(n):
        tok = LG.begin_turn()
        out = []
        for _ in range(n):
            await asyncio.sleep(0)      # force interleaving
            out.append(LG.check_and_record(*SQL))
        LG.end_turn(tok)
        return out
    return await asyncio.gather(turn(3), turn(3))


t = dict(_ORIG); t["max_tool_repeats"] = 2
APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = t
a, b2 = asyncio.run(_concurrent())
check("each turn counts only its own calls",
      [x is None for x in a] == [True, True, False]
      and [x is None for x in b2] == [True, True, False])

print("\n[7] ONE definition of 'the same call'")
# Two definitions would let the guard break a run on a repeat the Scorecard does not
# report — a control acting on evidence the record contradicts.
check("the guard uses the assessor's signature, not its own",
      "from trusted_data_agent.agent.assurance.assessors.tool_repetition import signature"
      in open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                           "agent", "loop_guard.py")).read())
check("…and argument ORDER does not change a signature",
      signature("t", {"a": 1, "b": 2}) == signature("t", {"b": 2, "a": 1}))
check("…while different arguments differ",
      signature("t", {"a": 1}) != signature("t", {"a": 2}))

print("\n[8] chokepoint PARITY — the ReAct wrapper reaches the same battery (GATE-H1)")
# The loop guard + tool-result firewall previously ran only on the MCP-adapter path. The
# ReAct engine — the one most prone to self-loops and whose output re-enters context — must
# reach them through its LangChain tool wrapper too. Drive the real wrapper.
import json as _json
from trusted_data_agent.agent import tool_enforcement as TE


async def _drive_wrapped(ceiling, n):
    t = dict(_ORIG); t["max_tool_repeats"] = ceiling
    APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = t
    calls = {"n": 0}

    async def _original(**kwargs):
        calls["n"] += 1
        return {"status": "success", "results": [{"v": 1}]}

    guarded = TE._make_guarded_coroutine("base_readQuery", _original, "sess-x", "user-x")
    tok = LG.begin_turn()
    outs = []
    for _ in range(n):
        outs.append(await guarded(sql="SELECT 1"))
    LG.end_turn(tok)
    return outs, calls["n"]


_outs, _ran = asyncio.run(_drive_wrapped(2, 4))
# The (N+1)th identical call is blocked BEFORE the tool runs — the block rides the
# content_and_artifact (content, artifact) shape as JSON content.
def _is_block(o):
    try:
        return _json.loads(o[0]).get("error") == "repeated_identical_call"
    except Exception:
        return False
check("wrapper: first 2 identical ReAct calls run", not _is_block(_outs[0]) and not _is_block(_outs[1]))
check("wrapper: the 3rd+ are loop-guard blocked", _is_block(_outs[2]) and _is_block(_outs[3]))
check("wrapper: the blocked calls never reached the tool", _ran == 2)
# The wrapper wires all four controls in ONE place (no drift copy at the adapter).
_te_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                            "agent", "tool_enforcement.py")).read()
check("wrapper: loop guard + firewall + SQL-write classify all present",
      "loop_guard_block(" in _te_src and "scan_result(" in _te_src
      and "classify_sql_write(" in _te_src)

print("\n[9] in-flight per-run budget cap reaches ReAct + genie (GATE-H4)")
# The cap had exactly one call site (PlanExecutor); the ReAct engine and the genie
# coordinator escaped it → post-hoc flagging only. Both now check before each model call.
_conv = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                          "agent", "conversation_agent.py")).read()
_genie = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                           "agent", "genie_coordinator.py")).read()
check("ReAct checks budget before a model call",
      "check_budget_before_call(self._run_cost_usd)" in _conv and "budget_stopped" in _conv)
check("genie checks budget before a model call",
      "check_budget_before_call(self._run_cost_usd)" in _genie)
check("both accumulate the per-call cost they cap on",
      "self._run_cost_usd +=" in _conv and "self._run_cost_usd +=" in _genie)

APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = _ORIG
print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
