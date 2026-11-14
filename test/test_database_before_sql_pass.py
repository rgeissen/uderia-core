"""PASS-3 — a SQL phase naming a database the turn has no reason to believe in is deferred.

FOUND LIVE (Optimize campaign, 2026-10-27): the cold lane invented database names —
`DEMO_Customer360_db.Customers`, `sales_db.sales_data`, `customer_db.customers JOIN
ticket_db.service_tickets` — in plans that had just SEARCHED the inventory with the navigator;
each ran into "database does not exist", a paid self-correction, "unable". Offline replay over
122 distinct saved plans: the pass would defer 26, of which 24 ended wrong or declined.

Two deterministic readings: with a session catalog, `preflight.catalog.nonexistent_db_refs`
names the database the source does not have; without one, a plan that searches an inventory
and then writes SQL against a database name is deferred, while a direct statement with no
search before it (E1: `fitness_db.Customers`) and any champion-guided plan are left alone.
CI-pure.
"""
import copy
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
from trusted_data_agent.agent.preflight import catalog as CAT  # noqa: E402


def run(plan, session_id=None, guided=False, rag=None, champion_dbs="unset"):
    exe = ex.PlanExecutor.__new__(ex.PlanExecutor)
    exe.meta_plan = copy.deepcopy(plan)
    exe.dependencies = {"STATE": {"mcp_tools": {t: {} for t in ("base_readQuery", "TDA_DatabaseNavigator", "base_tableList")}}}
    exe._log_system_event = lambda ev: None
    exe._format_sse_with_depth = lambda ev, *a, **k: ev
    exe.session_id = session_id
    exe.rag_case_count = rag
    p = pl.Planner.__new__(pl.Planner)
    p.executor = exe
    p._champion_guided = guided
    if champion_dbs != "unset":
        p._champion_databases = champion_dbs
    ev = list(p._rewrite_plan_for_database_before_sql())
    return exe.meta_plan, ev


def search(n):
    return {"phase": n, "goal": "search", "relevant_tools": ["TDA_DatabaseNavigator"], "arguments": {"query_text": "Customers"}}


def sqlp(n, sql):
    return {"phase": n, "goal": "query", "relevant_tools": ["base_readQuery"], "arguments": {"sql": sql}}


print("\n── §1 cold session: a search followed by SQL against an invented database is deferred")
plan, ev = run([search(1), sqlp(2, "SELECT COUNT(*) FROM DEMO_Customer360_db.Customers")])
check("deferred: the statement leaves the arguments, the draft stays", "sql" not in plan[1]["arguments"] and plan[1]["_sql_draft"])
check("the goal names the database it doubted", "DEMO_Customer360_db" in plan[1]["goal"])
check("the event names the pass", ev and ev[0]["details"]["pass"] == "database_before_sql")
plan, ev = run([sqlp(1, "SELECT COUNT(*) FROM fitness_db.Customers")])
check("a direct statement with no search before it is left alone (E1)", plan[0]["arguments"].get("sql") and not ev)
plan, ev = run([sqlp(1, "SELECT COUNT(*) FROM fitness_db.Customers"), search(2)])
check("a search AFTER the statement does not count", plan[0]["arguments"].get("sql") and not ev)
plan, ev = run([search(1), sqlp(2, "SELECT COUNT(*) FROM Customers")])
check("SQL with no database name at all is not this pass's business", plan[1]["arguments"].get("sql") and not ev)

print("\n── §2 with a session catalog: a database the source does not have is deferred, a real one is not")
CAT.record_names("pass3-session", ["fitness_db", "DBC"])
plan, ev = run([sqlp(1, "SELECT COUNT(*) FROM DEMO_Customer360_db.Customers")], session_id="pass3-session")
check("an unknown database is deferred even without a search", "sql" not in plan[0]["arguments"] and "DEMO_Customer360_db" in plan[0]["goal"])
plan, ev = run([search(1), sqlp(2, "SELECT COUNT(*) FROM fitness_db.Customers")], session_id="pass3-session")
check("a database the catalog knows is left alone even after a search", plan[1]["arguments"].get("sql") and not ev)
CAT.clear_session("pass3-session")

print("\n── §3 a champion-guided plan: the champions' databases are the ones to believe in")
plan, ev = run([sqlp(1, "SELECT COUNT(*) FROM DEMO_Customer360_db.Customers")], guided=True, champion_dbs={"fitness_db"})
check("guided, a database no champion names → deferred (the model's prior past a proven example)",
      "sql" not in plan[0]["arguments"] and "DEMO_Customer360_db" in plan[0]["goal"] and "fitness_db" in plan[0]["goal"])
plan, ev = run([sqlp(1, "SELECT COUNT(*) FROM fitness_db.Customers")], guided=True, champion_dbs={"fitness_db"})
check("guided, the champions' database → untouched", plan[0]["arguments"].get("sql") and not ev)
plan, ev = run([search(1), sqlp(2, "SELECT COUNT(*) FROM nowhere_db.t")], guided=True)
check("guided with unknown champion databases → stands down", plan[1]["arguments"].get("sql") and not ev)
plan, ev = run([search(1), sqlp(2, "SELECT COUNT(*) FROM nowhere_db.t")], rag=2, champion_dbs=set())
check("a champion whose SQL names no database → stands down", plan[1]["arguments"].get("sql") and not ev)
plan, ev = run([sqlp(1, "SELECT x FROM result_of_phase_1.t")], guided=True, champion_dbs={"fitness_db"})
check("a phase reference is the placeholder pass's, not this one's", plan[0]["arguments"].get("sql") and not ev)
deferred = [search(1), {"phase": 2, "goal": "q", "relevant_tools": ["base_readQuery"], "arguments": {}, "_sql_draft": "SELECT 1 FROM x.y"}]
plan, ev = run(deferred)
check("a phase another pass already deferred is skipped", plan == deferred and not ev)

from trusted_data_agent.agent import schema_facts as SF  # noqa: E402
case = {"full_case_data": {"successful_strategy": {"phases": [{"arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Customers c JOIN fitness_db.Sales s ON 1=1"}}]}}}
check("databases_in_case reads the champion's databases", SF.databases_in_case(case) == {"fitness_db"})

print("\n── §4 wiring and registry")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "planner.py")).read()
check("runs after placeholder_sql and before schema_before_sql",
      src.index('_pass("placeholder_sql"') < src.index('_pass("database_before_sql"') < src.index('_pass("schema_before_sql"'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "optimize_campaign"))
import registry as R  # noqa: E402
check("the campaign's registry extracts it", "database_before_sql" in R.REGISTRY)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
