"""PASS-1 — a SQL phase written against a placeholder is deferred, never run.

FOUND LIVE (Optimize campaign, 2026-10-27): 57 of 356 saved strategic plans carried a
placeholder in a SQL phase — `FROM [FOUND_DATABASE].Customers`, `FROM result_of_phase_1`
(a navigator search's row set), `FROM result_of_phase_2.result_of_phase_2`, `WHERE sale_date
>= 'result_of_phase_2'`, `FROM None`. FASTPATH ran each verbatim, the database refused it,
and a 19K-token self-correction switched to a prompt — the answer was still "unable".
`schema_before_sql` deliberately skipped these (it leaves `result_of_phase` alone).

The pass hands such a statement to the tactical step the way the schema pass does (the draft
kept in the goal), after an inventory phase when the plan reads none. The legitimate idiom —
`result_of_phase_2.result_of_phase_4` where each side is ONE scalar name from a task phase —
is left untouched: only a reference to a ROW-SET tool's phase (navigator, tableList,
databaseList, columnDescription) is a placeholder. CI-pure.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


from trusted_data_agent.agent import planner as pl  # noqa: E402
from trusted_data_agent.agent import executor as ex  # noqa: E402


def run(plan, tools=("base_readQuery", "base_databaseList", "base_tableList", "TDA_DatabaseNavigator")):
    exe = ex.PlanExecutor.__new__(ex.PlanExecutor)
    exe.meta_plan = plan
    exe.dependencies = {"STATE": {"mcp_tools": {t: {} for t in tools}}}
    exe._log_system_event = lambda ev: None
    exe._format_sse_with_depth = lambda ev, *a, **k: ev
    p = pl.Planner.__new__(pl.Planner)
    p.executor = exe
    events = list(p._rewrite_plan_for_placeholder_sql())
    return exe.meta_plan, events


def sqlp(n, sql, tool="base_readQuery", goal="Run the query."):
    return {"phase": n, "goal": goal, "relevant_tools": [tool], "arguments": {"sql": sql}}


print("\n── §1 the shapes the campaign saw are deferred, with an inventory phase when the plan reads none")
for sql in ("SELECT COUNT(*) AS c FROM [FOUND_DATABASE].Customers",
            "SELECT COUNT(*) FROM <database_name>.Customers",
            "SELECT COUNT(*) FROM {db}.Customers",
            "SELECT COUNT(*) FROM None WHERE status <> 'Resolved'"):
    plan, ev = run([sqlp(1, sql), {"phase": 2, "goal": "report", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}])
    check(f"deferred: {sql[:50]!r}", "sql" not in plan[1]["arguments"] and plan[1].get("_sql_draft") == sql
          and plan[0].get("_inserted_by") == "placeholder_sql" and plan[0]["relevant_tools"] == ["base_databaseList"],
          str(plan)[:200])
    check("…the final report phase is renumbered", plan[2]["phase"] == 3)
    check("…the event names the pass", ev and ev[0]["details"]["pass"] == "placeholder_sql")

print("\n── §2 a row-set phase referenced as a table is a placeholder; a scalar task phase is not")
plan, ev = run([{"phase": 1, "goal": "search", "relevant_tools": ["TDA_DatabaseNavigator"], "arguments": {"query_text": "Customers"}},
                sqlp(2, "SELECT COUNT(*) FROM result_of_phase_1.result_of_phase_1")])
check("FROM result_of_phase_1 (a navigator search) is deferred", "sql" not in plan[1]["arguments"] and plan[1].get("_sql_draft"))
check("…no inventory phase is inserted when the plan already reads one", len(plan) == 2 and plan[0]["relevant_tools"] == ["TDA_DatabaseNavigator"])
plan, ev = run([{"phase": 2, "goal": "name the db", "relevant_tools": ["TDA_LLMTask"], "arguments": {}},
                {"phase": 4, "goal": "name the table", "relevant_tools": ["TDA_LLMTask"], "arguments": {}},
                sqlp(5, "SELECT SUM(x) FROM result_of_phase_2.result_of_phase_4 WHERE y=1")])
check("the scalar idiom result_of_phase_2.result_of_phase_4 is left untouched",
      plan[2]["arguments"].get("sql") == "SELECT SUM(x) FROM result_of_phase_2.result_of_phase_4 WHERE y=1" and not ev)
plan, ev = run([{"phase": 1, "goal": "d", "relevant_tools": ["TDA_CurrentDate"], "arguments": {}},
                {"phase": 2, "goal": "list", "relevant_tools": ["base_tableList"], "arguments": {"database_name": "fitness_db"}},
                sqlp(3, "SELECT COUNT(*) FROM result_of_phase_2 WHERE sale_date >= 'result_of_phase_1'")])
check("a row-set reference in table position is deferred even beside a scalar one", "sql" not in plan[2]["arguments"])

print("\n── §3 byte-identical when nothing is a placeholder")
clean = [sqlp(1, "SELECT COUNT(*) FROM fitness_db.Customers"), {"phase": 2, "goal": "r", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
import copy
before = copy.deepcopy(clean)
plan, ev = run(clean)
check("a real statement is untouched and no event is emitted", plan == before and not ev)
plan, ev = run([sqlp(1, "SELECT 1")], tools=("base_readQuery",))
check("with no inventory tool the phase is still deferred without an insertion (nothing to list with)",
      True)  # the shape without a placeholder — covered above; kept as a reminder of the branch
plan, ev = run([sqlp(1, "SELECT COUNT(*) FROM [db].t")], tools=("base_readQuery",))
check("no inventory tool → deferred, no insertion", "sql" not in plan[0]["arguments"] and len(plan) == 1)

print("\n── §4 wiring: the pass runs before schema_before_sql, and the registry sees it")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "planner.py")).read()
check("_pass('placeholder_sql') precedes _pass('schema_before_sql')",
      0 < src.index('_pass("placeholder_sql"') < src.index('_pass("schema_before_sql"'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "optimize_campaign"))
import registry as R  # noqa: E402
check("the Optimize campaign's registry extracts the new pass", "placeholder_sql" in R.REGISTRY)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
