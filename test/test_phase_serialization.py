"""
Step 4 verification — role-aware phase serialization (discovery before execution).

Reproduces turn-6's plan: phase 1 = KG discovery ('identify the table'), phase 2 =
`SELECT * FROM airport.station`. The scheduler grouped them into ONE parallel wave because
phase 2 carried a hardcoded name and no result_of_phase_N reference, so the query could
never consume the discovery. This proves the discovery→execution rule now serializes them,
while NOT regressing legitimate parallelism.

Optimize-only: only tool_enabled builds multi-phase plans, so this cannot affect any other
IFOC class. Tests call the pure classmethods directly.

Run:  PYTHONPATH=src python3 test/test_phase_serialization.py
"""
import sys

from trusted_data_agent.agent.executor import PlanExecutor as PE


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def groups_as_nums(meta_plan):
    return [[p.get("phase") for p in g] for g in PE._build_phase_groups(meta_plan)]


def kg_query(n):
    return {"phase": n, "goal": "identify the table", "relevant_tools": ["TDA_KnowledgeGraph"],
            "arguments": {"action": "query", "query_text": "airport station"}}


def tablelist(n, db="airlines_mvm"):
    return {"phase": n, "goal": "list tables", "relevant_tools": ["base_tableList"],
            "arguments": {"database_name": db}}


def sql(n, s="SELECT * FROM airport.station"):
    return {"phase": n, "goal": "run query", "relevant_tools": ["base_readQuery"], "arguments": {"sql": s}}


def final(n):
    return {"phase": n, "goal": "synthesize", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}


print("\n[1] THE fix: turn-6 plan (KG discovery -> SQL -> report) is fully serialized")
plan = [kg_query(1), sql(2), final(3)]
groups = groups_as_nums(plan)
check(f"groups == [[1],[2],[3]] (got {groups})", groups == [[1], [2], [3]])
check("phase 1 and phase 2 are NOT in the same parallel wave",
      not any(1 in g and 2 in g for g in groups))

print("\n[2] _phase_discovery_deps unit — execution after discovery depends on it")
check("base_readQuery after KG-query -> dep {1}", PE._phase_discovery_deps(sql(2), plan) == {1})
check("base_readQuery after base_tableList -> dep {1}",
      PE._phase_discovery_deps(sql(2), [tablelist(1), sql(2)]) == {1})
check("sql-arg phase (no base_readQuery tool) still counts as execution",
      PE._phase_discovery_deps(
          {"phase": 2, "relevant_tools": ["some_tool"], "arguments": {"sql": "SELECT 1 FROM airport.x"}},
          [kg_query(1)]) == {1})
check("execution with NO preceding discovery -> {}",
      PE._phase_discovery_deps(sql(2), [sql(1, "SELECT 1"), sql(2)]) == set())
check("discovery phase itself is not 'execution' -> {}",
      PE._phase_discovery_deps(tablelist(2), [kg_query(1), tablelist(2)]) == set())

print("\n[3] no-regression: independent queries with NO discovery stay parallel")
plan = [sql(1, "SELECT COUNT(*) FROM airlines_mvm.awb"), sql(2, "SELECT COUNT(*) FROM airlines_mvm.crew")]
groups = groups_as_nums(plan)
check(f"two independent SQL phases share one wave (got {groups})", groups == [[1, 2]])

print("\n[4] no-regression: two discovery phases (no execution) stay parallel")
plan = [{"phase": 1, "relevant_tools": ["base_databaseList"], "arguments": {"scope": "all"}},
        tablelist(2)]
groups = groups_as_nums(plan)
check(f"discovery+discovery share one wave (got {groups})", groups == [[1, 2]])

print("\n[5] discovery flushed, then multiple independent queries re-group in parallel")
plan = [kg_query(1), sql(2, "SELECT * FROM airlines_mvm.a"), sql(3, "SELECT * FROM airlines_mvm.b")]
groups = groups_as_nums(plan)
check(f"[[1],[2,3]] — queries parallel AFTER the discovery (got {groups})", groups == [[1], [2, 3]])

print("\n[6] no-regression: existing dependency detection still works")
# explicit result_of_phase_N reference serializes (unchanged behavior)
dep_plan = [sql(1, "SELECT * FROM airlines_mvm.a"),
            {"phase": 2, "relevant_tools": ["base_readQuery"],
             "arguments": {"sql": "SELECT * FROM {result_of_phase_1}"}}]
check("result_of_phase_1 reference still serializes", groups_as_nums(dep_plan) == [[1], [2]])
# visualization implicit dependency still serializes
viz_plan = [sql(1, "SELECT * FROM airlines_mvm.a"),
            {"phase": 2, "relevant_tools": ["TDA_Charting"], "arguments": {"chart_type": "bar"}}]
check("visualization phase still serializes after data", groups_as_nums(viz_plan) == [[1], [2]])
# terminal phase still runs alone
check("terminal report still isolated", groups_as_nums([sql(1), final(2)]) == [[1], [2]])

print(f"\n=== Step 4 results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
