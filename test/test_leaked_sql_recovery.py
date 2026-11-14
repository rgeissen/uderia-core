"""A leaked SELECT becomes a query (agent/leaked_sql.py + ConversationAgentExecutor._recover_leaked_sql).

Prod (session 86ac6b28 turns 9–13, Celeris): the model answered "Show me one email from the
case" with a fenced SELECT and called no tool, five turns running. The recovery runs that
statement through the bound read tool — the same guarded tool object a model call reaches —
emits the loop's own tool events, and answers with the rows. Zero LLM tokens.

CI-pure. Run: python test/test_leaked_sql_recovery.py
"""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


from trusted_data_agent.agent import leaked_sql as ls  # noqa: E402

PROD_T9 = "```sql\nSELECT TOP 1 \n    subject, \n    from_address, \n    to_address, \n    body, \n    sent_time \nFROM Policing_Core.v_EmailMessage\n```"
PROD_T13 = "```sql\nSELECT \n    file_name, \n    file_extension \nFROM Policing_Core.v_File\n```"

print("\n[1] sole_sql_block — the answer IS one read statement")
check("prod turn 9 is recognised", (ls.sole_sql_block(PROD_T9) or "").startswith("SELECT TOP 1"))
check("prod turn 13 is recognised", (ls.sole_sql_block(PROD_T13) or "").startswith("SELECT"))
check("a short lead-in is tolerated", ls.sole_sql_block("Here is the query:\n" + PROD_T13) is not None)
check("a trailing model-gap footer is tolerated",
      ls.sole_sql_block(PROD_T9 + "\n\n_Not in the model: actual. An Actual can be proposed as an extension of the model._") is not None)
check("an unlabelled fence that is plainly a SELECT counts", ls.sole_sql_block("```\nselect 1 from dual\n```") is not None)
check("an unlabelled fence of non-SQL does not", ls.sole_sql_block("```\nprint('hi')\n```") is None)
check("a SELECT inside a long explanation is NOT the answer",
      ls.sole_sql_block("Robbery is offence 120. " * 20 + PROD_T13 + " This counts rows." * 10) is None)
check("two blocks are not the answer", ls.sole_sql_block(PROD_T9 + "\n\n" + PROD_T13) is None)
check("prose with no block → None", ls.sole_sql_block("There are 179,193 robberies.") is None)
check("a trailing semicolon is stripped", not (ls.sole_sql_block("```sql\nSELECT 1;\n```") or ";").endswith(";"))

print("\n[2] read-only, single statement")
check("a write is never run", ls.sole_sql_block("```sql\nDELETE FROM Policing_Core.v_File\n```") is None)
check("a write hidden behind a CTE is never run",
      ls.sole_sql_block("```sql\nWITH x AS (SELECT 1 a) UPDATE t SET c = 1\n```") is None)
check("two statements are never run", ls.sole_sql_block("```sql\nSELECT 1; SELECT 2\n```") is None)
check("leading comments do not hide the verb", ls.is_single_read_statement("-- top row\nSELECT 1"))
check("INSERT is refused", not ls.is_single_read_statement("INSERT INTO t VALUES (1)"))

print("\n[3] find_sql_tool")


class _T:
    def __init__(self, name, args):
        self.name, self.args = name, args


tools = [_T("TDA_KnowledgeGraph", {"action": {}, "sql": {}}), _T("base_readQuery", {"sql": {}, "persist": {}}),
         _T("other_read", {"sql": {}})]
check("base_readQuery is preferred", ls.find_sql_tool(tools).name == "base_readQuery")
check("else the first non-component tool with a sql arg", ls.find_sql_tool([tools[0], tools[2]]).name == "other_read")
check("a component tool never counts, even with a sql arg", ls.find_sql_tool([tools[0]]) is None)
check("no tools → None", ls.find_sql_tool([]) is None)
check("ran_data_tool ignores TDA_ tools", not ls.ran_data_tool(["TDA_Dashboard"]) and ls.ran_data_tool(["TDA_Dashboard", "base_readQuery"]))

print("\n[4] rows read back and rendered")
OUT = json.dumps({"status": "success", "results": [
    {"subject": "Re: shipment", "from_address": "a@x.org", "to_address": "b@x.org", "body": "line1\nline2 | pipe", "sent_time": "2018-11-14 09:22:15"}],
    "metadata": {"tool_name": "base_readQuery", "row_count": 1}})
rows, rc = ls.rows_from_output_text("some prefix " + OUT)
check("rows parsed from the read tool's shape", rows is not None and rows[0]["subject"] == "Re: shipment")
check("declared row_count read", rc == 1)
md = ls.render_rows_markdown(rows, rc)
check("one plain sentence, then a table", md.startswith("The query returned 1 row.") and "| subject |" in md)
check("newlines and pipes inside a cell are made safe", "line1 line2 \\| pipe" in md)
check("no rows → says so", ls.render_rows_markdown([], 0) == "The query ran and returned no rows.")
many = [{"n": i} for i in range(40)]
md2 = ls.render_rows_markdown(many, 1000)
check("bounded: 25 rows shown, the count stated", "the first 25 are shown" in md2
      and len([l for l in md2.splitlines() if l.startswith("| ")]) == 27)   # header + separator + 25 rows
check("unreadable output → None rows, never a guess", ls.rows_from_output_text("plain text") == (None, None))
ans = ls.recovered_answer("SELECT 1", rows, 1, None)
check("the delivered answer carries the rows and the statement that ran", "| subject |" in ans and "```sql\nSELECT 1\n```" in ans)
err = ls.recovered_answer("SELECT 1", None, None, "[Error 3802] Database 'x' does not exist.")
check("a failed run says so and shows no rows", err.startswith("Running this query failed: [Error 3802]") and "|" not in err)

print("\n[5] the agent method on a stub executor — events, tools_used, the guarded tool object")
from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor as CAE  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402


class _FakeReadTool:
    name = "base_readQuery"
    args = {"sql": {}, "persist": {}, "row_limit": {}}

    def __init__(self, out):
        self.out, self.calls = out, []

    async def ainvoke(self, call):
        self.calls.append(call)
        return ToolMessage(content=self.out, tool_call_id=call["id"], name=self.name)


def stub(tool):
    st = types.SimpleNamespace(mcp_tools=[tool], session_id="s1", tool_start_times={}, provenance=None,
                               collected=[], captured=[])

    async def _emit(et, payload):
        st.collected.append((et, payload))

    async def _ind(*a):
        pass
    st._emit_event = _emit
    st._emit_status_indicator = _ind
    st._with_source = lambda p, name, completed=False: p
    st._capture_data_result = lambda name, out, rw=None: st.captured.append(name)
    for n in ("_tool_output_text", "_tool_output_refusal", "_tool_output_is_error", "_tool_output_row_count",
              "_tool_output_metadata", "_short_tool_error"):
        setattr(st, n, getattr(CAE, n))
    st._recover_leaked_sql = types.MethodType(CAE._recover_leaked_sql, st)
    return st


t = _FakeReadTool(OUT)
st = stub(t)
used = []
out = asyncio.run(st._recover_leaked_sql(PROD_T9, used))
check("the statement ran through the BOUND tool object (ToolCall shape with id)",
      len(t.calls) == 1 and t.calls[0]["type"] == "tool_call" and t.calls[0]["args"]["sql"].startswith("SELECT TOP 1"), t.calls)
check("tools_used records the data tool", used == ["base_readQuery"], used)
kinds = [k for k, _ in st.collected]
check("the loop's own tool events are emitted (invoked → completed)", kinds == ["conversation_tool_invoked", "conversation_tool_completed"], kinds)
done = dict(st.collected)["conversation_tool_completed"]
check("completed event: success, row count, metadata, marked recovered",
      done["success"] is True and done.get("result_rows") == 1 and done.get("metadata", {}).get("tool_name") == "base_readQuery"
      and done.get("recovered") == "leaked_sql", done)
check("the answer is the rows, not the SQL alone", out.startswith("The query returned 1 row.") and "a@x.org" in out)
check("data result captured for downstream components", st.captured == ["base_readQuery"])
check("idempotent: a second call on the same executor does nothing", asyncio.run(st._recover_leaked_sql(PROD_T13, used)) == PROD_T13 and len(t.calls) == 1)

t2 = _FakeReadTool('{"status": "error", "data": "[Error 3807] Object \'Policing_Core.v_Nope\' does not exist."}')
st2 = stub(t2)
out2 = asyncio.run(st2._recover_leaked_sql(PROD_T13, []))
done2 = dict(st2.collected)["conversation_tool_completed"]
check("a failed run is reported as a failed call (success False) and the answer says so",
      done2["success"] is False and out2.startswith("Running this query failed: [Error 3807]"), (done2, out2[:80]))

t3 = _FakeReadTool(OUT)
st3 = stub(t3)
same = asyncio.run(st3._recover_leaked_sql(PROD_T9, ["base_readQuery"]))
check("when a data tool already ran, the SQL in the text is left alone", same == PROD_T9 and not t3.calls)
st4 = stub(t3)
same4 = asyncio.run(st4._recover_leaked_sql("There are 179,193 robberies.", []))
check("prose is untouched", same4 == "There are 179,193 robberies." and not t3.calls)


class _Raises(_FakeReadTool):
    async def ainvoke(self, call):
        raise RuntimeError("Agent Authority: write denied")


st5 = stub(_Raises(OUT))
out5 = asyncio.run(st5._recover_leaked_sql(PROD_T9, []))
check("a raising tool becomes a failed call, never an unhandled error", "Agent Authority: write denied" in out5
      and dict(st5.collected)["conversation_tool_completed"]["success"] is False)

print("\n[6] wiring: before auto-canvas, after the component recovery")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "conversation_agent.py")).read()
i_c, i_s, i_a = src.find("await self._recover_leaked_component_calls(final_response, tools_used)"), \
    src.find("await self._recover_leaked_sql(final_response, tools_used)"), src.find("final_response = self._auto_canvas_code_blocks(final_response)")
check("called in the final-response pipeline", i_s != -1)
check("after the component recovery and BEFORE auto-canvas (which would file the SQL as a canvas)", i_c < i_s < i_a)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
