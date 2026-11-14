"""Plan validation renames a planned tool the catalog does not have to the tool it meant.

FOUND LIVE (Optimize campaign, 2026-10-27): 12 of 188 saved plans named a tool the agent's
catalog lacked — `base_rowCount`, `TDA_DatabaseNavigator_search`, `TDA_ExecuteSQL` carrying a
SELECT, `base_query` — and paid 16 self-corrections (a 16K-token LLM call each) for a repair a
rule can make. Three readings, in order: a component name with an action suffix → the component;
a SELECT under an invented runner → the read tool (the query moved to `sql`); the one catalog
name within difflib 0.8. Anything else stands. CI-pure.
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

CATALOG = {"base_readQuery": None, "base_tableList": None, "base_columnDescription": None, "base_tableDescriptionn": None}


def validate(plan):
    exe = ex.PlanExecutor.__new__(ex.PlanExecutor)
    exe.meta_plan = copy.deepcopy(plan)
    exe.dependencies = {"STATE": {"mcp_tools": dict(CATALOG), "mcp_prompts": {}}}
    exe._log_system_event = lambda ev: None
    exe._format_sse_with_depth = lambda ev, *a, **k: ev
    exe.session_id = None
    p = pl.Planner.__new__(pl.Planner)
    p.executor = exe
    try:
        list(p._validate_and_correct_plan())
    except TypeError:
        p._validate_and_correct_plan()
    return exe.meta_plan


def ph(tool, args):
    return {"phase": 1, "goal": "g", "relevant_tools": [tool], "arguments": args}


print("\n── §1 the three readings")
r = pl.Planner._rename_unknown_tool
check("TDA_DatabaseNavigator_search → TDA_DatabaseNavigator", r("TDA_DatabaseNavigator_search", {"query_text": "x"}, CATALOG) == "TDA_DatabaseNavigator")
check("TDA_ExecuteSQL with a SELECT → base_readQuery", r("TDA_ExecuteSQL", {"sql": "SELECT 1"}, CATALOG) == "base_readQuery")
check("base_query with a SELECT in `query` → base_readQuery", r("base_query", {"query": "select count(*) from a.b"}, CATALOG) == "base_readQuery")
check("a near miss within 0.8 → the real name", r("base_tableDescriptionnn", {}, CATALOG) == "base_tableDescriptionn")
check("base_rowCount (no close name, no SQL) stands", r("base_rowCount", {"database_name": "d", "table_name": "t"}, CATALOG) is None)
check("a catalog name is never touched", r("base_readQuery", {"sql": "SELECT 1"}, CATALOG) is None)
check("an alias shape belongs to 3b", r("crm__base_readQuery", {"sql": "SELECT 1"}, CATALOG) is None)

print("\n── §2 inside plan validation")
out = validate([ph("TDA_ExecuteSQL", {"sql": "SELECT TOP 5 x FROM a.b"})])
check("the phase now names base_readQuery and keeps its SQL", out[0]["relevant_tools"] == ["base_readQuery"] and out[0]["arguments"].get("sql"))
out = validate([ph("base_query", {"query": "SELECT 1 FROM a.b"})])
check("a `query` argument under the renamed read tool moves to `sql`", out[0]["relevant_tools"] == ["base_readQuery"] and out[0]["arguments"].get("sql") == "SELECT 1 FROM a.b")
out = validate([ph("TDA_DatabaseNavigator_search", {"query_text": "Customers"})])
check("the component keeps its arguments", out[0]["relevant_tools"] == ["TDA_DatabaseNavigator"] and out[0]["arguments"] == {"query_text": "Customers"})
out = validate([ph("base_readQuery", {"sql": "SELECT 1"})])
check("a real tool is untouched", out[0]["relevant_tools"] == ["base_readQuery"])
out = validate([ph("TDA_FinalReport", {})])
check("a known component without a suffix is untouched", out[0]["relevant_tools"] == ["TDA_FinalReport"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
