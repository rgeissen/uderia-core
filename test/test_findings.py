"""Findings — the verified results of earlier turns, re-read not recalled.

  A  the extractor: Optimize traces and ReAct data results → one shape; framework calls,
     failures and row-less results carry none; single-row values, multi-row samples; bounds
  B  the block: header + rule, newest turn first, raw numbers, budget drops OLDER turns,
     the newest turn is pinned under any budget
  C  the context module: abstains on turn one and with nothing to say; renders prior turns,
     never the current one; condenses to fewer turns with the pin kept; its metadata carries
     the text shown; applies to the three data/knowledge engines
  D  persistence: every engine's turn is stamped at the ONE seam (update_last_turn_data); a
     lane that stamped its own is left alone; the module reads the stamp back
  E  the grounding gate vouches for what the platform showed (live and post-hoc readers)
  F  the delivery: every engine appends the same block after the KG enrichment; the UI names it

CI-pure. Run: python test/test_findings.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
logging.disable(logging.CRITICAL)

from trusted_data_agent.agent import findings as F     # noqa: E402

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


def trace_entry(tool, args, result):
    return {"action": {"tool_name": tool, "arguments": args}, "result": result}


TRACE = [
    trace_entry("TDA_SystemLog", {"message": "Tool Surface"}, {"status": "info"}),
    trace_entry("base_readQuery", {"sql": "SELECT COUNT(*) AS customer_count FROM Sales.Customers"},
                {"status": "success", "results": [{"customer_count": 4812}], "metadata": {"row_count": 1}}),
    trace_entry("base_readQuery", {"sql": "SELECT region, SUM(amount) AS total FROM Sales.Orders GROUP BY 1"},
                {"status": "success", "results": [{"region": "EMEA", "total": 1203400.5}, {"region": "APAC", "total": 998000}],
                 "metadata": {"row_count": 2, "executed_sql": "SELECT region, SUM(amount) AS total FROM Sales.Orders GROUP BY 1 ORDER BY 2 DESC"}}),
    trace_entry("base_readQuery", {"sql": "SELECT 1 FROM nowhere"}, {"status": "error", "error": "3807"}),
    trace_entry("base_tableList", {"database_name": "Sales"}, {"status": "success", "results": [{"TableName": "Customers"}, {"TableName": "Orders"}, {"TableName": "Products"}]}),
    trace_entry("base_columnDescription", {"database_name": "Sales", "object_name": "Orders"}, {"status": "success", "results": "not a list"}),
    trace_entry("TDA_FinalReport", {"report": "…"}, {"status": "success", "results": [{"x": 1}]}),
]

print("\n[A] the extractor")
fs = F.from_execution_trace(TRACE)
check("framework calls, failures and row-less results carry no finding", [f["tool"] for f in fs] == ["base_readQuery", "base_readQuery", "base_tableList"], fs)
c = fs[0]
check("a single-row result carries its values", c["rows"] == 1 and c["values"] == {"customer_count": "4812"}, c)
check("the key is the statement", c["key"].startswith("SELECT COUNT(*) AS customer_count"), c)
r = fs[1]
check("a multi-row result carries columns and the first row", r["rows"] == 2 and r["columns"] == ["region", "total"] and r["sample"]["region"] == "EMEA" and "values" not in r, r)
check("the executed statement wins over the asked one", "ORDER BY 2 DESC" in r["key"], r["key"])
t = fs[2]
check("a non-SQL call is keyed by the object it addressed", t["key"] == "database_name=Sales" and t["rows"] == 3, t)
big = [trace_entry("base_readQuery", {"sql": f"SELECT {i}"}, {"status": "success", "results": [{"v": i}]}) for i in range(40)]
check("bounded per turn", len(F.from_execution_trace(big)) == F.MAX_FINDINGS_PER_TURN)
wide = trace_entry("base_readQuery", {"sql": "SELECT *"}, {"status": "success", "results": [{f"c{i}": i for i in range(30)}]})
w = F.from_execution_trace([wide])[0]
check("a wide single row: columns capped, no values (too wide to quote)", len(w["columns"]) == F.MAX_COLUMNS and "values" not in w, w)
check("never raises on garbage", F.from_execution_trace([None, 3, {"action": "x"}, {"action": {}, "result": {"results": [1, 2]}}]) == [])

react = [{"results": [{"n": 17}], "metadata": {"tool_name": "base_readQuery", "row_count": 1, "arguments": {"sql": "SELECT COUNT(*) AS n FROM T"}}},
         {"results": [{"a": 1}, {"a": 2}], "metadata": {"tool_name": "base_readQuery", "row_count": 250, "truncated": True,
                                                         "arguments": {"sql": "SELECT a FROM T"}, "executed_sql": "SELECT TOP 250 a FROM T"}},
         {"results": [{"a": 1}], "metadata": {"tool_name": "TDA_Charting"}}]
rf = F.from_data_results(react)
check("ReAct data results → the same shape", [f["tool"] for f in rf] == ["base_readQuery", "base_readQuery"] and rf[0]["values"] == {"n": "17"}, rf)
check("a truncated capture keeps the TRUE row count", rf[1]["rows"] == 250 and rf[1]["key"].startswith("SELECT TOP 250"), rf[1])
check("extract_turn prefers the stamp", F.extract_turn({"findings": [{"tool": "x", "rows": 1}], "execution_trace": TRACE}) == [{"tool": "x", "rows": 1}])
check("extract_turn falls back to the trace", len(F.extract_turn({"execution_trace": TRACE})) == 3)
check("extract_turn falls back to data results", len(F.extract_turn({"execution_trace": [], "data_results": react})) == 2)

print("\n[B] the block")
turns = [(1, F.from_execution_trace(TRACE)), (2, F.from_data_results(react))]
block = F.format_block(turns, 10_000)
check("header and the re-read rule lead", block.startswith(F.BLOCK_HEADER + "\n" + F.BLOCK_RULE), block[:120])
check("newest turn first", block.index("Turn 2:") < block.index("Turn 1:"))
check("raw numbers, never formatted", "4812" in block and "4,812" not in block and "1203400.5" in block, block)
check("a single-row value reads as name = value", "customer_count = 4812" in block)
check("a multi-row result reads as rows · columns · first row", "2 rows · columns: region, total · first row: region = EMEA" in block, block)
tiny = F.format_block(turns, 10)
check("under a tiny budget the newest turn is pinned, the older one dropped", "Turn 2:" in tiny and "Turn 1:" not in tiny, tiny)
mid = F.format_block(turns, len(F.format_turn(2, turns[1][1])) + len(F.BLOCK_HEADER) + len(F.BLOCK_RULE) + 40)
check("a budget that fits one turn shows one", "Turn 2:" in mid and "Turn 1:" not in mid, mid)
check("nothing to say → empty", F.format_block([(1, [])], 1000) == "" and F.format_block([], 1000) == "")
check("session_turns skips invalid turns and the current one",
      [t for t, _ in F.session_turns([{"turn": 1, "execution_trace": TRACE}, {"turn": 2, "isValid": False, "execution_trace": TRACE},
                                       {"turn": 3, "execution_trace": TRACE}], exclude_turn=3)] == [1])

print("\n[C] the context module")
from components.builtin.context_window.module_registry import get_module_registry   # noqa: E402
from components.builtin.context_window.base import AssemblyContext                   # noqa: E402

reg = get_module_registry()
mods = reg.discover_modules()
check("registered by discovery", "findings" in mods)
defn = mods["findings"]
mod = reg.get_handler("findings")
check("the registry built the handler", mod is not None and mod.module_id == "findings")
check("applies to the three data/knowledge engines, not a coordinator",
      all(mod.applies_to(t) for t in ("tool_enabled", "llm_only", "rag_focused")) and not mod.applies_to("genie"))
check("manifest: condensable, not purgeable, priority between knowledge and memory",
      defn.manifest_raw["capabilities"]["condensable"] is True and defn.manifest_raw["capabilities"]["purgeable"] is False
      and defn.manifest_raw["defaults"]["priority"] == 68, defn.manifest_raw)

sd = {"last_turn_data": {"workflow_history": [
    {"turn": 1, "user_query": "how many customers?", "execution_trace": TRACE},
    {"turn": 2, "user_query": "by region", "findings": F.from_data_results(react)},
    {"turn": 3, "user_query": "current", "execution_trace": TRACE},
]}}


def ctx(turn, first=False, data=sd, ptype="tool_enabled"):
    return AssemblyContext(profile_type=ptype, profile_id="p", session_id="s", user_uuid="u",
                           session_data=data, turn_number=turn, is_first_turn=first)


c1 = asyncio.run(mod.contribute(4000, ctx(1, first=True)))
check("turn one abstains", c1.content == "" and c1.metadata["reason"] == "first_turn")
c0 = asyncio.run(mod.contribute(4000, ctx(2, data={"last_turn_data": {"workflow_history": [{"turn": 1, "execution_trace": []}]}})))
check("nothing returned rows → abstains", c0.content == "" and c0.metadata["reason"] == "no_findings")
c3 = asyncio.run(mod.contribute(4000, ctx(3)))
check("renders the earlier turns, never the current one", "Turn 2:" in c3.content and "Turn 1:" in c3.content and "Turn 3:" not in c3.content, c3.content)
check("tokens measured, metadata carries the text and the counts",
      c3.tokens_used > 0 and c3.metadata["text"] == c3.content and c3.metadata["turns"] == 2 and c3.metadata["findings"] == 5, c3.metadata)
c3s = asyncio.run(mod.condense(c3.content, 20, ctx(3)))
check("condensing keeps the newest turn (pinned) and drops the older",
      "Turn 2:" in c3s.content and "Turn 1:" not in c3s.content and c3s.metadata["condensed"] is True, c3s.content)
cb = asyncio.run(mod.contribute(4000, ctx(2)))
check("a ReAct-stamped turn reads back through the module", "Turn 1:" in cb.content and "customer_count = 4812" in cb.content)
check("purge is refused: findings are the record's own", asyncio.run(mod.purge("s", "u"))["purged"] is False)

print("\n[D] persistence at the one seam")
from trusted_data_agent.core.session_backends.factory import reset_session_backend   # noqa: E402
from trusted_data_agent.core.session_backends.file_backend import FileSessionBackend   # noqa: E402

_tmp = tempfile.mkdtemp(prefix="findings_")
reset_session_backend(FileSessionBackend(_tmp))
from trusted_data_agent.core import session_manager as SM   # noqa: E402

U, S = "user-f", "sess-f"
asyncio.run(SM.save_session(U, S, {"id": S, "user_uuid": U, "session_history": [], "chat_object": [],
                                  "last_turn_data": {"workflow_history": []}, "license_info": {"provider": "Anthropic"}, "name": "t"}))
asyncio.run(SM.update_last_turn_data(U, S, {"turn": 1, "user_query": "q", "final_summary_text": "a", "status": "success",
                                           "profile_type": "tool_enabled", "execution_trace": TRACE, "model": "m"}))
asyncio.run(SM.update_last_turn_data(U, S, {"turn": 2, "user_query": "q2", "final_summary_text": "a2", "status": "success",
                                           "profile_type": "conversation_with_tools", "execution_trace": [],
                                           "findings": F.from_data_results(react), "model": "m"}))
asyncio.run(SM.update_last_turn_data(U, S, {"turn": 3, "user_query": "q3", "final_summary_text": "a3", "status": "success",
                                           "profile_type": "llm_only", "execution_trace": [], "model": "m"}))
wf = asyncio.run(SM.read_session(U, S))["last_turn_data"]["workflow_history"]
check("an Optimize turn is stamped from its trace", len(wf[0].get("findings") or []) == 3, wf[0].get("findings"))
check("a ReAct turn's own stamp is kept", wf[1]["findings"][0]["values"] == {"n": "17"})
check("an Ideate turn without tools carries an empty stamp (never missing)", wf[2].get("findings") == [])
cm = asyncio.run(mod.contribute(4000, ctx(4, data={"last_turn_data": {"workflow_history": wf}})))
check("the module reads the stamps back", "Turn 1:" in cm.content and "Turn 2:" in cm.content and cm.metadata["turns"] == 2, cm.metadata)

print("\n[E] the grounding gate vouches for what the platform showed")
from trusted_data_agent.eval.types import RunRecord   # noqa: E402

rr = RunRecord({"user_query": "and in EMEA?", "findings_context": c3.content})
vn = rr.vouched_numbers()
check("the post-hoc reader vouches the shown figures", {"4812", "17", "250"} <= vn, vn)
check("… not an unrelated one", "9999" not in vn)
rr2 = RunRecord({"user_query": "x", "grounding": {"context_numbers": ["4812"]}})
check("the persisted gate copy vouches the same way", "4812" in rr2.vouched_numbers())
_rt = _src("src/trusted_data_agent/agent/grounding/runtime.py")
check("the live gate stamps the findings' numbers into context_numbers",
      'turn.get("findings_context")' in _rt and 'event["context_numbers"] = _ctx' in _rt)
_ex = _src("src/trusted_data_agent/agent/executor.py")
check("the executor hands the gate the text it showed", 'trace_extra["findings_context"] = self._get_findings_context()' in _ex)

print("\n[F] the delivery: one block, every engine, after the KG enrichment")
for rel in ("src/trusted_data_agent/agent/engines/ideate_engine.py",
            "src/trusted_data_agent/agent/engines/focus_engine.py",
            "src/trusted_data_agent/agent/engines/conversation_engine.py",
            "src/trusted_data_agent/agent/planner.py",
            "src/trusted_data_agent/agent/engines/optimize_engine.py"):
    s = _src(rel)
    check(f"{os.path.basename(rel)} appends the session blocks (memory, then findings)", "_session_context_blocks()" in s)
check("the executor caches the module's contribution", 'assembled_context.contributions.get("findings")' in _ex and "_cw_findings_text" in _ex)
check("the ReAct turn stamps its findings from its captured rows",
      '"findings": _findings_mod.from_data_results(result.get("data_results") or [])' in _src("src/trusted_data_agent/agent/engines/conversation_engine.py"))
check("the UI names and colours the module",
      "findings: 'Findings'" in _src("static/js/handlers/contextPanelHandler.js") and "findings: '#0ea5e9'" in _src("static/js/handlers/contextWindowHandler.js"))

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
