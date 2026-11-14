"""
Gate — schema facts for SQL written before the schema was known (multi-source plan, stage 3.0).

Found by the stage-0 bench (Optimize answered 0 of 39 on an agent with no knowledge graph): the
planner wrote `SUM(sale_total)` against a table whose columns are `TotalAmount`, `SaleDate`…;
the database refused it; the correction LLM answered "describe the table"; the describe call
SUCCEEDED and the retry loop took that success as the phase's answer — so the query was never
retried with the real columns and the report said the column did not exist.

  [1] schema_facts pure functions (tables a statement reads, the role tool, result shapes)
  [2] fetch_columns — the session first (0 calls), else the agent's describe tool; recorded;
      a table it could not read is NAMED, never claimed
  [3] the correction strategies read the facts when no knowledge graph has them
      (column: real columns → mechanical fix or facts to the LLM; table: the real table list);
      nothing read ⇒ nothing added (the behaviour before)
  [4] the REAL retry loop (`PhaseExecutor._execute_tool`) driven end to end:
      A — a column error is corrected with the database's columns and the statement retried;
      B — a correction that stands a describe call in for the failed statement: the describe
          result becomes facts and the ORIGINAL statement is retried — never the phase's answer
  [5] wiring on the AST

Run: python test/test_schema_facts.py   (no server, no network)
"""
import ast
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent import schema_facts as sf  # noqa: E402
from trusted_data_agent.agent import phase_executor as pe  # noqa: E402
from trusted_data_agent.agent.preflight import columns as pcols  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {str(detail)[:500]}" if detail else ""))


def run(coro):
    return _LOOP.run_until_complete(coro)


_LOOP = asyncio.new_event_loop()


async def _no_session(*a, **k):
    return None
pe.session_manager.get_session = _no_session

SALES_COLS = ["SaleID", "CustomerID", "SaleDate", "TotalAmount", "SalesPersonID"]
TABLE_ROWS = [{"DataBaseName": "ms_fixture_sales", "TableName": "SaleDetails"},
              {"DataBaseName": "ms_fixture_sales", "TableName": "Sales"}]


class FakeMCP:
    """Scripted `invoke_mcp_tool`: a describe returns the table's columns, a table list the
    tables, a SQL call answers from a rule; every call is recorded."""
    def __init__(self, sql_rule=None, columns=None):
        self.calls = []
        self.sql_rule = sql_rule or (lambda sql: {"status": "success", "results": [{"x": 1}]})
        self.columns = columns if columns is not None else {"sales": SALES_COLS}

    async def __call__(self, STATE, command, **kw):
        self.calls.append(json.loads(json.dumps(command)))
        name = sf._base(command.get("tool_name", ""))
        args = command.get("arguments") or {}
        if name == sf.COLUMN_TOOL:
            t = str(args.get("table_name") or args.get("object_name") or "").lower()
            cols = self.columns.get(t)
            if cols is None:
                return {"status": "error", "error": f"Object '{t}' does not exist"}, 0, 0
            return {"status": "success", "results": [{"TableName": t, "ColumnName": c, "CType": None}
                                                      for c in cols]}, 0, 0
        if name == sf.TABLE_LIST_TOOL:
            return {"status": "success", "results": TABLE_ROWS}, 0, 0
        return self.sql_rule(args.get("sql")), 0, 0


class FakeExecutor:
    def __init__(self, tools=("base_readQuery", "base_columnDescription", "base_tableList"),
                 llm_responses=None, session_id="sf-session"):
        self.kg_enrichment_event = None
        self.dependencies = {"STATE": {"mcp_tools": {t: object() for t in tools}, "mcp_prompts": {}}}
        self.correction_history = []
        self.source = "test"
        self.user_uuid = "u"
        self.session_id = session_id
        self.active_profile_id = "p"
        self.original_user_input = "What was the total sales amount in 2023?"
        self.workflow_goal_prompt = ""
        self.llm_prompts = []
        self.llm_responses = list(llm_responses or [])
        self._last_call_metadata = {}
        self.turn_input_tokens = 0
        self.turn_output_tokens = 0
        self.current_phase_index = 0
        self.execution_depth = 0
        self.workflow_state = {}
        self.turn_action_history = []
        self.last_tool_output = None
        self.provenance = None
        self.is_synthesis_from_history = False
        self.events = []

        class _R:
            def get_tactical_self_correction_prompt(self_):
                return "{tool_definition}{failed_command}{error_message}{user_question}{tools_context}{prompts_context}"

            def get_tactical_self_correction_column_error_prompt(self_):
                return "{user_question}{tool_name}{failed_arguments}{invalid_column_name}{tools_context}{prompts_context}"

            def get_tactical_self_correction_table_error_prompt(self_):
                return "{user_question}{tool_name}{failed_arguments}{invalid_table_name}{database_name}{tools_context}{prompts_context}"
        self.prompt_resolver = _R()

    def _check_cancellation(self):
        pass

    def _get_correction_catalog(self):
        return "tools", "prompts"

    def _get_phase_state(self, n):
        return {"correction_history": []}

    def _log_system_event(self, e, *a, **k):
        self.events.append(e)

    def _format_sse_with_depth(self, data, event=None):
        return data

    def _add_to_structured_data(self, x):
        pass

    async def _call_llm_and_update_tokens(self, prompt=None, **k):
        self.llm_prompts.append(prompt)
        return (self.llm_responses.pop(0) if self.llm_responses else '{"arguments": {"sql": "SELECT 1"}}'), 10, 5


def fresh_session(name):
    pcols._COLS.pop(name, None)
    return name


# =================================================================================== [1]
print("[1] pure functions")
check("qualified tables read in order",
      sf.sql_tables("SELECT * FROM ms_fixture_sales.Sales s JOIN ms_fixture_sales.SaleDetails d ON s.SaleID=d.SaleID")
      == [("ms_fixture_sales", "Sales"), ("ms_fixture_sales", "SaleDetails")])
check("an unqualified table carries no database", sf.sql_tables("SELECT a FROM Sales") == [(None, "Sales")])
check("a CTE name is not a table",
      sf.sql_tables("WITH t AS (SELECT * FROM db.Sales) SELECT * FROM t") == [("db", "Sales")])
check("no SQL → no tables", sf.sql_tables(None) == [] and sf.sql_tables("   ") == [])
from trusted_data_agent.core import tool_identity as _ti  # noqa: E402
_ti.set_turn_aliases(("crm",))   # a multi-server turn declares its aliases; base_name reads them
check("the role tool prefers the plain (first-server) name",
      sf.tool_for("base_columnDescription", ["crm__base_columnDescription", "base_columnDescription"])
      == "base_columnDescription")
check("…else a qualified one", sf.tool_for("base_columnDescription", ["crm__base_columnDescription"])
      == "crm__base_columnDescription")
check("…else none", sf.tool_for("base_columnDescription", ["base_readQuery"]) is None)
check("columns read from the describe rows, without repeats",
      sf.columns_from_result({"status": "success", "results": [{"ColumnName": "A"}, {"column_name": "B"},
                                                               {"ColumnName": "A"}]}) == ["A", "B"])
check("an error result has no columns", sf.columns_from_result({"status": "error", "results": [{"ColumnName": "A"}]}) == [])
check("a describe standing in for a failed SQL call is recognised",
      sf.is_discovery_standin({"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}},
                              {"tool_name": "base_columnDescription", "arguments": {}}))
check("…a SQL-to-SQL correction is not",
      not sf.is_discovery_standin({"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}},
                                  {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 2"}}))
check("…nor a correction of a call that carried no SQL",
      not sf.is_discovery_standin({"tool_name": "base_tableList", "arguments": {"database_name": "d"}},
                                  {"tool_name": "base_columnDescription", "arguments": {}}))
check("a qualified describe counts as discovery", sf.is_discovery_tool("crm__base_columnDescription"))
facts = sf.discovery_facts_text({"tool_name": "base_columnDescription",
                                 "arguments": {"database_name": "db", "table_name": "Sales"}},
                                {"status": "success", "results": [{"ColumnName": c} for c in SALES_COLS]})
check("a describe result reads as the table's columns", facts == "- db.Sales: " + ", ".join(SALES_COLS), facts)

# =================================================================================== [2]
print("[2] fetch_columns")
exe = FakeExecutor(session_id=fresh_session("sf-2a"))
mcp = FakeMCP()
pe.mcp_adapter.invoke_mcp_tool = mcp  # the module schema_facts imports lazily is the same object
import trusted_data_agent.mcp_adapter.adapter as _adp  # noqa: E402
_real_invoke = _adp.invoke_mcp_tool
_adp.invoke_mcp_tool = mcp
cols, notes = run(sf.fetch_columns(exe, [("ms_fixture_sales", "sales")]))
check("an unknown table is read through the describe tool", cols == {"ms_fixture_sales.sales": SALES_COLS}, cols)
check("…one call, with the database and the table", len(mcp.calls) == 1
      and mcp.calls[0]["arguments"].get("database_name") == "ms_fixture_sales"
      and "sales" in mcp.calls[0]["arguments"].values(), mcp.calls)
check("…and recorded for the session", sf.known_columns("sf-2a", "sales") == SALES_COLS)
cols, _ = run(sf.fetch_columns(exe, [("ms_fixture_sales", "Sales")]))
check("a table the session knows is read for free (no second call)", len(mcp.calls) == 1 and cols)
cols, notes = run(sf.fetch_columns(exe, [("ms_fixture_sales", "nosuch")]))
check("a table the database does not describe is NAMED, never claimed",
      cols == {} and len(notes) == 1 and "nosuch" in notes[0], notes)
exe_nt = FakeExecutor(tools=("base_readQuery",), session_id=fresh_session("sf-2b"))
cols, notes = run(sf.fetch_columns(exe_nt, [("db", "t")]))
check("no describe tool → nothing read, the reason named", cols == {} and "no column-description tool" in notes[0])
cols, notes = run(sf.fetch_columns(exe, [(None, "Orphan")]))
check("an unqualified table with no default database is named, not guessed",
      cols == {} and "not named" in notes[0], notes)

# =================================================================================== [3]
print("[3] the correction strategies read the facts")
ERR5628 = {"status": "error", "data": "[Error 5628] Column sale_total not found in ms_fixture_sales.sales.",
           "error_message": ""}
FAILED = {"tool_name": "base_readQuery",
          "arguments": {"sql": "SELECT SUM(sale_total) AS t FROM ms_fixture_sales.sales WHERE EXTRACT(YEAR FROM sale_date)=2023"}}
exe = FakeExecutor(session_id=fresh_session("sf-3a"),
                   llm_responses=['{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) FROM ms_fixture_sales.sales"}}'])
mcp.calls.clear()
corrected, events = run(pe.ColumnNotFoundStrategy(exe).generate_correction(FAILED, ERR5628))
check("the database's real columns reach the correction prompt",
      exe.llm_prompts and sf.FACTS_HEADER in exe.llm_prompts[0] and "TotalAmount" in exe.llm_prompts[0], exe.llm_prompts)
check("…with the directive to rewrite the SAME statement", "Rewrite the failing statement" in exe.llm_prompts[0])
check("…the read is a named step (0 LLM)",
      any((ev[0].get("details") or {}).get("correction_type") == "schema_facts_read" for ev in events))
check("…and the correction is the rewritten statement", corrected["arguments"]["sql"].startswith("SELECT SUM(TotalAmount)"))

exe = FakeExecutor(session_id=fresh_session("sf-3b"))
fa = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT totalamount FROM ms_fixture_sales.sales"}}
corrected, events = run(pe.ColumnNotFoundStrategy(exe).generate_correction(
    fa, {"status": "error", "data": "Column totalamount not found in ms_fixture_sales.sales."}))
check("a unique close match against the database's columns is fixed mechanically (no LLM)",
      not exe.llm_prompts and corrected["arguments"]["sql"] == "SELECT TotalAmount FROM ms_fixture_sales.sales",
      (exe.llm_prompts, corrected))
check("…and says it came from the database's own columns",
      any("the database's own columns" in str(ev[0].get("details")) for ev in events))

exe = FakeExecutor(tools=("base_readQuery",), session_id=fresh_session("sf-3c"))
run(pe.ColumnNotFoundStrategy(exe).generate_correction(FAILED, ERR5628))
check("nothing could be read → the prompt carries no facts and no directive (the behaviour before)",
      exe.llm_prompts and sf.FACTS_HEADER not in exe.llm_prompts[0] and "Rewrite the failing" not in exe.llm_prompts[0])

exe = FakeExecutor(session_id=fresh_session("sf-3d"))
exe.kg_enrichment_event = {"enrichments": [{"table_columns": {"ms_fixture_sales.sales": ["TotalAmount", "SaleDate"]}}]}
mcp.calls.clear()
run(pe.ColumnNotFoundStrategy(exe).generate_correction(FAILED, ERR5628))
check("a knowledge graph's facts take precedence — the database is not read", mcp.calls == [])

exe = FakeExecutor(session_id=fresh_session("sf-3e"))
fa_t = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT * FROM ms_fixture_sales.orders"}}
corrected, events = run(pe.TableNotFoundStrategy(exe).generate_correction(
    fa_t, {"status": "error", "data": "Object 'ms_fixture_sales.orders' does not exist."}))
check("a missing table: the database's real table list reaches the prompt",
      exe.llm_prompts and "SaleDetails" in exe.llm_prompts[0] and sf.FACTS_HEADER in exe.llm_prompts[0], exe.llm_prompts)

# =================================================================================== [4]
print("[4] the real retry loop")


async def _drive(exe, action):
    phase = {"phase": 2, "goal": "sum the sales", "relevant_tools": ["base_readQuery"]}
    out = []
    async for ev in pe.PhaseExecutor(exe)._execute_tool(action, phase, is_fast_path=True):
        out.append(ev)
    return out


def sql_rule(sql):
    if "sale_total" in (sql or ""):
        return {"status": "error", "error": "db error", "data": ERR5628["data"], "error_message": ""}
    return {"status": "success", "results": [{"total_sales_amount": 4242.0}]}


# A: column error → facts → rewritten statement → success
mcp.sql_rule = sql_rule
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4a"), llm_responses=[
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) AS total_sales_amount FROM ms_fixture_sales.sales"}}'])
run(_drive(exe, json.loads(json.dumps(FAILED))))
check("A: the phase answers with the rewritten statement's rows",
      exe.last_tool_output.get("status") == "success"
      and exe.last_tool_output["results"][0].get("total_sales_amount") == 4242.0, exe.last_tool_output)
check("A: one LLM round", len(exe.llm_prompts) == 1)

# B: the correction substitutes a describe call for the failed statement (a generic error, so
#    no fact-reading strategy runs first). The describe result must become facts.
def sql_rule_b(sql):
    if "sale_total" in (sql or ""):
        return {"status": "error", "error": "db error", "data": "an error the strategies do not recognise", "error_message": ""}
    return {"status": "success", "results": [{"total_sales_amount": 4242.0}]}


mcp.sql_rule = sql_rule_b
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4b"), llm_responses=[
    '{"tool_name": "base_columnDescription", "arguments": {"database_name": "ms_fixture_sales", "table_name": "sales"}}',
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) AS total_sales_amount FROM ms_fixture_sales.sales"}}'])
run(_drive(exe, json.loads(json.dumps(FAILED))))
check("B: the describe result is NOT the phase's answer — the statement is retried and answers",
      exe.last_tool_output.get("status") == "success"
      and (exe.last_tool_output.get("results") or [{}])[0].get("total_sales_amount") == 4242.0, exe.last_tool_output)
check("B: the calls were statement → describe → statement",
      [sf._base(c["tool_name"]) for c in mcp.calls] == ["base_readQuery", "base_columnDescription", "base_readQuery"],
      [c["tool_name"] for c in mcp.calls])
check("B: the second correction was given the describe result as facts",
      len(exe.llm_prompts) == 2 and "TotalAmount" in exe.llm_prompts[1] and "SCHEMA FACTS" in exe.llm_prompts[1]
      and "Rewrite the failing statement" in exe.llm_prompts[1],
      exe.llm_prompts[-1][:400] if exe.llm_prompts else None)
check("B: the retry is a named step",
      any((e.get("details") or {}).get("correction_type") == "schema_facts_retry" for e in exe.events))
check("B: the describe's columns are recorded for the session", sf.known_columns("sf-4b", "sales") == SALES_COLS)

# B': the second correction proposes discovery AGAIN → it is not run as the statement's retry
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4c"), llm_responses=[
    '{"tool_name": "base_columnDescription", "arguments": {"database_name": "ms_fixture_sales", "table_name": "sales"}}',
    '{"tool_name": "base_tableList", "arguments": {"database_name": "ms_fixture_sales"}}'])
run(_drive(exe, json.loads(json.dumps(FAILED))))
check("B': a second discovery proposal is not run in place of the statement",
      [sf._base(c["tool_name"]) for c in mcp.calls] == ["base_readQuery", "base_columnDescription"],
      [c["tool_name"] for c in mcp.calls])

# a plain SQL-to-SQL correction is unchanged (no retry machinery)
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4d"), llm_responses=[
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) AS total_sales_amount FROM ms_fixture_sales.sales"}}'])
run(_drive(exe, json.loads(json.dumps(FAILED))))
check("a statement that succeeds after a SQL correction is not retried again",
      [sf._base(c["tool_name"]) for c in mcp.calls] == ["base_readQuery", "base_readQuery"]
      and not any((e.get("details") or {}).get("correction_type") == "schema_facts_retry" for e in exe.events))
_adp.invoke_mcp_tool = _real_invoke

# B'': a stand-in proposed on the LAST attempt still gets its retry (the stand-in's attempt is
#       granted back once)
mcp.sql_rule = sql_rule_b
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4e"), llm_responses=[
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(sale_total) FROM ms_fixture_sales.sales"}}',
    '{"tool_name": "base_columnDescription", "arguments": {"database_name": "ms_fixture_sales", "table_name": "sales"}}',
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) AS total_sales_amount FROM ms_fixture_sales.sales"}}'])
run(_drive(exe, json.loads(json.dumps(FAILED))))
check("B'': a describe stand-in on the last attempt still ends in the statement's retry",
      [sf._base(c["tool_name"]) for c in mcp.calls] == ["base_readQuery", "base_readQuery", "base_columnDescription", "base_readQuery"]
      and exe.last_tool_output.get("status") == "success", [c["tool_name"] for c in mcp.calls])
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4f"), llm_responses=[
    '{"tool_name": "base_columnDescription", "arguments": {"database_name": "ms_fixture_sales", "table_name": "sales"}}',
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(sale_total) FROM ms_fixture_sales.sales"}}',
    '{"tool_name": "base_columnDescription", "arguments": {"database_name": "ms_fixture_sales", "table_name": "sales"}}',
    '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}'])
run(_drive(exe, json.loads(json.dumps(FAILED))))
check("…the extra attempt is granted once, never again (the loop stays bounded)",
      len(mcp.calls) <= 4, [c["tool_name"] for c in mcp.calls])

# the table strategy: an UNQUALIFIED table this turn listed in exactly one database
exe = FakeExecutor(session_id=fresh_session("sf-4g"))
exe.workflow_state = {"result_of_phase_3": [{"status": "success", "results": TABLE_ROWS}]}
fa_u = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(x) FROM Sales WHERE y JOIN saledetails d ON 1=1"}}
corrected, events = run(pe.TableNotFoundStrategy(exe).generate_correction(
    fa_u, {"status": "error", "data": "[Error 3807] Object 'Sales' does not exist."}))
check("an unqualified table this turn listed in one database is qualified (0 LLM)",
      not exe.llm_prompts and corrected["arguments"]["sql"].startswith("SELECT SUM(x) FROM ms_fixture_sales.Sales WHERE"),
      corrected)
check("…only the table the error named", "JOIN saledetails" in corrected["arguments"]["sql"])
exe = FakeExecutor(session_id=fresh_session("sf-4h"))
exe.workflow_state = {"result_of_phase_1": [{"status": "success", "results": TABLE_ROWS + [
    {"DataBaseName": "other_db", "TableName": "Sales"}]}]}
run(pe.TableNotFoundStrategy(exe).generate_correction(
    fa_u, {"status": "error", "data": "[Error 3807] Object 'Sales' does not exist."}))
check("…a table two databases hold is not guessed; the tables seen go to the prompt as facts",
      exe.llm_prompts and "other_db" in exe.llm_prompts[0] and "ms_fixture_sales" in exe.llm_prompts[0])
check("…and no placeholder a model could copy as a database name",
      "the specified database" not in exe.llm_prompts[0])

# no table listing this turn, only a database listing: the user databases' tables become facts
mcp.calls.clear()
exe = FakeExecutor(session_id=fresh_session("sf-4i"))
exe.workflow_state = {"result_of_phase_1": [{"status": "success", "results": [
    {"DatabaseName": "ms_fixture_sales", "DBType": "DataBase"},
    {"DatabaseName": "TD_SYSXML", "DBType": "DataBase"},
    {"DatabaseName": "DBC", "DBType": "DataBase"},
    {"DatabaseName": "ms_fixture_sales_agent", "DBType": "User"}]}]}
run(pe.TableNotFoundStrategy(exe).generate_correction(
    {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(x) FROM sales_table"}},
    {"status": "error", "data": "[Error 3807] Object 'sales_table' does not exist."}))
check("no table listing: the listed USER database's tables are read and given as facts",
      exe.llm_prompts and "SaleDetails" in exe.llm_prompts[0] and "ms_fixture_sales" in exe.llm_prompts[0], exe.llm_prompts)
check("…system databases and user accounts are never read",
      [c["arguments"].get("database_name") for c in mcp.calls] == ["ms_fixture_sales"], mcp.calls)

# a reference dotted to a reference is a qualified name built from two steps
from trusted_data_agent.agent import executor as ex  # noqa: E402
px = ex.PlanExecutor.__new__(ex.PlanExecutor)
px.workflow_state = {"result_of_phase_2": [{"status": "success", "results": [{"response": "ms_fixture_sales"}]}],
                     "result_of_phase_4": [{"status": "success", "results": [{"response": "Sales"}]}]}
out = pe_args = None
try:
    out = px._resolve_arguments({"sql": "SELECT SUM(x) FROM result_of_phase_2.result_of_phase_4 WHERE y=1"})
except Exception as exc:  # noqa: BLE001
    out = {"error": repr(exc)}
check("result_of_phase_2.result_of_phase_4 resolves to database.table",
      (out or {}).get("sql") == "SELECT SUM(x) FROM ms_fixture_sales.Sales WHERE y=1", out)
px.workflow_state["result_of_phase_4"] = [{"status": "success", "results": [{"a": 1}, {"a": 2}]}]
out = px._resolve_arguments({"sql": "SELECT SUM(x) FROM result_of_phase_2.result_of_phase_4"})
check("…never when a side is not one scalar (left as written)", "result_of_phase_2.result_of_phase_4" in out.get("sql", ""), out)

# =================================================================================== [4b]
print("[4b] the plan pass: schema before SQL")
from trusted_data_agent.agent import planner as pl  # noqa: E402


def _plan_pass(plan, exe, guided=False, champion_tables="unset"):
    exe.meta_plan = plan
    p = pl.Planner.__new__(pl.Planner)
    p.executor = exe
    p._champion_guided = guided
    if champion_tables != "unset":
        p._champion_tables = champion_tables
    list(p._rewrite_plan_for_schema_before_sql())
    return exe.meta_plan


def _q13_plan():
    return [
        {"phase": 1, "goal": "Identify the sales table", "relevant_tools": ["base_tableList"],
         "arguments": {"database_name": "ms_fixture_sales"}},
        {"phase": 2, "goal": "Sum the sales for 2023", "relevant_tools": ["base_readQuery"],
         "arguments": {"sql": "SELECT SUM(sale_total) FROM ms_fixture_sales.sales WHERE EXTRACT(YEAR FROM sale_date)=2023"}},
        {"phase": 3, "goal": "Report", "relevant_tools": ["TDA_FinalReport"],
         "arguments": {"data": "result_of_phase_2"}},
    ]


exe = FakeExecutor(session_id=fresh_session("sf-5a"))
exe.rag_case_count = None
plan = _plan_pass(_q13_plan(), exe)
check("a describe phase is inserted before the statement",
      [p["relevant_tools"][0] for p in plan] == ["base_tableList", "base_columnDescription", "base_readQuery", "TDA_FinalReport"],
      [p["relevant_tools"] for p in plan])
check("…for the statement's own table",
      plan[1]["arguments"] == {"database_name": "ms_fixture_sales", "table_name": "sales"})
check("…the statement's SQL is left to the tactical step, the draft kept as its intent",
      "sql" not in plan[2]["arguments"] and plan[2].get("_sql_draft", "").startswith("SELECT SUM(sale_total)")
      and "real tables and columns" in plan[2]["goal"])
check("…phases renumbered and later references follow",
      [p["phase"] for p in plan] == [1, 2, 3, 4] and plan[3]["arguments"]["data"] == "result_of_phase_3", plan)
check("…and the rewrite is a named plan step",
      any((e.get("details") or {}).get("pass") == "schema_before_sql" for e in exe.events))

exe = FakeExecutor(session_id=fresh_session("sf-5b"))
exe.rag_case_count = None
exe.kg_enrichment_event = {"enrichments": [{"table_columns": {"ms_fixture_sales.Sales": SALES_COLS}}]}
before = _q13_plan()
check("a knowledge graph that knows the table: the plan is untouched",
      _plan_pass(_q13_plan(), exe) == before)

exe = FakeExecutor(session_id=fresh_session("sf-5c"))
exe.rag_case_count = None
pcols.record_columns("sf-5c", {"sales": SALES_COLS}, origin="dictionary")
check("a table the session already described: untouched", _plan_pass(_q13_plan(), exe) == before)

exe = FakeExecutor(session_id=fresh_session("sf-5d"))
exe.rag_case_count = 1
check("a champion-guided plan whose champion tables are unknown: untouched", _plan_pass(_q13_plan(), exe) == before)
exe.rag_case_count = None
check("…and the planner's own champion flag is honoured too", _plan_pass(_q13_plan(), exe, guided=True) == before)
check("a champion that read the statement's table vouches for it: untouched",
      _plan_pass(_q13_plan(), exe, guided=True, champion_tables={"sales"}) == before)
plan = _plan_pass(_q13_plan(), exe, guided=True, champion_tables={"customers"})
check("a champion that read OTHER tables does not vouch for this one: described",
      [p["relevant_tools"][0] for p in plan][:3] == ["base_tableList", "base_columnDescription", "base_readQuery"], plan)
_case = {"case_id": "c", "full_case_data": {"successful_strategy": {"phases": [
    {"arguments": {"sql": "SELECT a FROM db.Sales s JOIN db.Customers c ON s.k=c.k"}},
    {"arguments": {"nested": [{"sql": "SELECT 1 FROM other.T"}]}}]}}}
check("a champion's tables are read from every SQL string in the case",
      sf.tables_in_case(_case) == {"sales", "customers", "t"}, sf.tables_in_case(_case))

exe = FakeExecutor(tools=("base_readQuery", "base_tableList"), session_id=fresh_session("sf-5e"))
exe.rag_case_count = None
check("an agent with no describe tool: untouched", _plan_pass(_q13_plan(), exe) == before)

exe = FakeExecutor(session_id=fresh_session("sf-5f"))
exe.rag_case_count = None
two = [{"phase": 1, "goal": "a", "relevant_tools": ["base_readQuery"],
        "arguments": {"sql": "SELECT * FROM db.T1 JOIN db.T2 ON T1.k=T2.k"}},
       {"phase": 2, "goal": "b", "relevant_tools": ["base_readQuery"],
        "arguments": {"sql": "SELECT COUNT(*) FROM db.T1"}}]
plan = _plan_pass(two, exe)
check("one describe per table, never twice across phases",
      [p["arguments"].get("table_name") for p in plan if p["relevant_tools"][0] == "base_columnDescription"] == ["T1", "T2"],
      plan)
check("…every statement that read an unknown table goes to the tactical step",
      all("sql" not in p["arguments"] for p in plan if p["relevant_tools"][0] == "base_readQuery"))

exe = FakeExecutor(session_id=fresh_session("sf-5g"))
exe.rag_case_count = None
ref = [{"phase": 1, "goal": "a", "relevant_tools": ["TDA_Compute"], "arguments": {}},
       {"phase": 2, "goal": "b", "relevant_tools": ["base_readQuery"],
        "arguments": {"sql": "result_of_phase_1"}},
       {"phase": 3, "goal": "c", "relevant_tools": ["base_readQuery"],
        "arguments": {"sql": {"source": "result_of_phase_1", "key": "sql"}}},
       {"phase": 4, "goal": "d", "relevant_tools": ["base_readQuery"],
        "arguments": {"sql": "SELECT 1 FROM Unqualified"}},
       {"phase": 5, "goal": "e", "relevant_tools": ["base_readQuery"],
        "arguments": {"sql": "SELECT * FROM db.T WHERE id IN (result_of_phase_1)"}}]
check("a runtime reference, a governed statement or an unqualified table: untouched",
      _plan_pass(json.loads(json.dumps(ref)), exe) == ref)

exe = FakeExecutor(session_id=fresh_session("sf-5h"))
exe.rag_case_count = None
wide = [{"phase": 1, "goal": "a", "relevant_tools": ["base_readQuery"],
         "arguments": {"sql": "SELECT * FROM d.A, d.B, d.C, d.D, d.E, d.F"}}]
plan = _plan_pass(wide, exe)
check("at most MAX_TABLES describes per plan",
      sum(1 for p in plan if p["relevant_tools"][0] == "base_columnDescription") == sf.MAX_TABLES)

print("[4c] the inserted describe settles on the turn's own table list")
exe = FakeExecutor(session_id=fresh_session("sf-6a"))
exe.workflow_state = {"result_of_phase_1": [{"status": "success", "results": TABLE_ROWS}]}
a, note = sf.settle_describe_args(exe, {"database_name": "ms_fixture_sales", "table_name": "sales_data"})
check("a guessed table the listing plainly means is described instead",
      a["table_name"] == "Sales" and note and "sales_data" in note, (a, note))
a, note = sf.settle_describe_args(exe, {"database_name": "MS_FIXTURE_SALES", "table_name": "sale"})
check("…singular/plural and case", a["table_name"] == "Sales" and note)
a, note = sf.settle_describe_args(exe, {"database_name": "ms_fixture_sales", "table_name": "Sales"})
check("…a listed table is left as written", a["table_name"] == "Sales" and note is None)
a, note = sf.settle_describe_args(exe, {"database_name": "ms_fixture_sales", "table_name": "SaleDetailSales"})
check("…two candidates (Sales, SaleDetails) are not guessed between",
      a["table_name"] == "SaleDetailSales" and note is None, (a, note))
a, note = sf.settle_describe_args(exe, {"database_name": "ms_fixture_sales", "table_name": "orders"})
check("…no candidate: left as written", a["table_name"] == "orders" and note is None)
exe.workflow_state = {}
a, note = sf.settle_describe_args(exe, {"database_name": "ms_fixture_sales", "table_name": "sales_data"})
check("…no listing this turn: left as written", a["table_name"] == "sales_data" and note is None)

print("[4d] scheduling: the inserted describe waits for the discovery before it")
from trusted_data_agent.agent.executor import PlanExecutor as _PX  # noqa: E402
_plan = [{"phase": 1, "relevant_tools": ["base_tableList"], "arguments": {"database_name": "d"}},
         {"phase": 2, "relevant_tools": ["base_columnDescription"], "arguments": {"database_name": "d", "table_name": "t"},
          "_inserted_by": "schema_before_sql"},
         {"phase": 3, "relevant_tools": ["base_columnDescription"], "arguments": {"database_name": "d", "table_name": "u"}}]
check("an inserted describe depends on the table list before it",
      _PX._phase_discovery_deps(_plan[1], _plan) == {1}, _PX._phase_discovery_deps(_plan[1], _plan))
check("…a describe the planner wrote itself is scheduled exactly as before",
      _PX._phase_discovery_deps(_plan[2], _plan) == set())

# =================================================================================== [5]
print("[5] wiring")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent",
                        "phase_executor.py")).read()
tree = ast.parse(src)


def _fn(name):
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _calls(node):
    return {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            for n in ast.walk(node) if isinstance(n, ast.Call)}


check("the retry loop marks a discovery stand-in", "is_discovery_standin" in _calls(_fn("_execute_tool")))
check("…and records the discovery before retrying", "record_discovery" in _calls(_fn("_execute_tool")))
_col = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ColumnNotFoundStrategy")
check("the column strategy reads the database's facts", "_read_schema_facts" in _calls(_col))
_tab = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "TableNotFoundStrategy")
check("the table strategy reads the database's table list", "fetch_tables" in _calls(_tab))
_pl = ast.parse(open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent",
                                  "planner.py")).read())
_passes = [n.args[0].value for n in ast.walk(_pl) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_pass"
           and n.args and isinstance(n.args[0], ast.Constant)]
_se = _fn("_execute_standard_phase") if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_execute_standard_phase" for n in ast.walk(tree)) else None
_src_se = ast.get_source_segment(src, _se) if _se else src
check("an inserted describe phase settles its table before it runs", "settle_describe_args" in _src_se)
check("a withdrawn statement never takes FASTPATH",
      'phase.get("_sql_draft")' in _src_se and "is_fast_path_candidate = False" in _src_se.split('phase.get("_sql_draft")', 1)[1][:400])
_guard = [n for n in ast.walk(_se) if isinstance(n, ast.If)
          and 'phase.get("_sql_draft") and is_standard_success' in (ast.get_source_segment(src, n.test) or "").replace("'", '"')]
check("in a withdrawn-statement phase a discovery action does not complete the phase (its block ends in continue)",
      len(_guard) == 1 and isinstance(_guard[0].body[-1], ast.Continue),
      [type(g.body[-1]).__name__ for g in _guard])
check("the planner runs schema_before_sql after date_range_wiring and before text_date_cast",
      "schema_before_sql" in _passes
      and _passes.index("date_range_wiring") < _passes.index("schema_before_sql") < _passes.index("text_date_cast"),
      _passes)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
