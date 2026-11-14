"""
Step 1 verification — deterministic Error 3802 ('Database X does not exist') recovery.

Covers the exact turn-6 failure of session 5fd9be3d (@OPGGT / Optimize): the planner
hallucinated `SELECT * FROM airport.station`; the `airport` database doesn't exist;
3802 fell through to a blind generic LLM retry. This proves 3802 now routes to a
DETERMINISTIC, KG-scope-aware recovery with ZERO LLM judgement.

IFOC no-regression: CorrectionHandler lives in the Optimize (tool_enabled)
Planner/Executor path only. These tests assert (a) 3802 is handled deterministically,
(b) table/column/other errors still route to their existing strategies unchanged
(no hijack), and (c) the deterministic path never calls the LLM.

Run:  PYTHONPATH=src python3 test/test_database_not_found_correction.py
"""
import asyncio
import sys

from trusted_data_agent.agent import phase_executor as pe


# ---------------------------------------------------------------- test doubles
class FakeExecutor:
    """Minimal stand-in exposing only what the deterministic strategy reads."""
    def __init__(self, scope_databases=None, tools=None):
        self.kg_enrichment_event = None
        if scope_databases is not None:
            self.kg_enrichment_event = {"enrichments": [{"scope_databases": list(scope_databases)}]}
        self.dependencies = {"STATE": {"mcp_tools": {t: object() for t in (tools or [])}}}
        self.correction_history = []
        self.source = "test"
        self.user_uuid = "u"
        self.session_id = "s"

    async def _call_llm_and_update_tokens(self, *a, **k):
        raise AssertionError("LLM must NOT be called on the deterministic 3802 recovery path")


def err_3802(db="airport"):
    return {
        "status": "error",
        "error": "Tool returned malformed JSON string",
        "data": (f"Error: (teradatasql.OperationalError) [Version 20.0.0.40] [Session 1272] "
                 f"[Teradata Database] [Error 3802] Database '{db}' does not exist.\n"
                 f"[SQL: SELECT * FROM {db}.station]"),
        "error_message": "",
    }


def failed_read(sql="SELECT * FROM airport.station"):
    return {"tool_name": "base_readQuery", "arguments": {"sql": sql}}


def first_strategy_name(handler, error_text):
    """Replicates CorrectionHandler's selection loop at the can_handle level
    (deterministic, no LLM) so we can assert routing without plumbing."""
    for s in handler.strategies:
        if s.can_handle(error_text):
            return s.__class__.__name__
    return None


# ------------------------------------------------------------------- run tests
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


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


print("\n[1] can_handle: matches 3802, rejects table/column errors (no hijack)")
strat = pe.DatabaseNotFoundStrategy(FakeExecutor())
check("matches 'Database X does not exist'", strat.can_handle("... [Error 3802] Database 'airport' does not exist."))
check("rejects table-not-found text", not strat.can_handle("Object 'foo.bar' does not exist"))
check("rejects column-not-found text", not strat.can_handle("Column 'baz' does not exist"))
check("rejects unrelated error", not strat.can_handle("Syntax error: unexpected token"))

print("\n[2] Priority 1: KG scope known + base_tableList available -> deterministic tableList")
ex = FakeExecutor(scope_databases=["airlines_mvm"], tools=["base_tableList", "base_databaseList", "base_readQuery"])
corrected, events = run(pe.DatabaseNotFoundStrategy(ex).generate_correction(failed_read(), err_3802()))
check("corrected tool is base_tableList", corrected and corrected.get("tool_name") == "base_tableList")
check("targets the KG-scoped database airlines_mvm", corrected.get("arguments", {}).get("database_name") == "airlines_mvm")
check("emits an auto_correction (not a workaround/error) event", events and events[0][0]["type"] == "auto_correction")
check("event summary names the deterministic recovery", "Deterministic recovery" in events[0][0]["details"]["summary"])

print("\n[3] Priority 1: multiple scope dbs -> deterministic (sorted) pick")
ex = FakeExecutor(scope_databases=["zeta_db", "alpha_db"], tools=["base_tableList"])
corrected, _ = run(pe.DatabaseNotFoundStrategy(ex).generate_correction(failed_read(), err_3802()))
check("picks sorted()[0] == 'alpha_db' deterministically", corrected.get("arguments", {}).get("database_name") == "alpha_db")

print("\n[4] Priority 2: no scope -> deterministic base_databaseList")
ex = FakeExecutor(scope_databases=None, tools=["base_databaseList", "base_readQuery"])
corrected, _ = run(pe.DatabaseNotFoundStrategy(ex).generate_correction(failed_read(), err_3802()))
check("corrected tool is base_databaseList", corrected and corrected.get("tool_name") == "base_databaseList")
check("uses scope=all", corrected.get("arguments", {}).get("scope") == "all")

print("\n[5] Priority 2: scope present but base_tableList tool absent -> base_databaseList")
ex = FakeExecutor(scope_databases=["airlines_mvm"], tools=["base_databaseList"])
corrected, _ = run(pe.DatabaseNotFoundStrategy(ex).generate_correction(failed_read(), err_3802()))
check("falls back to base_databaseList when tableList unavailable", corrected.get("tool_name") == "base_databaseList")

print("\n[6] Priority 3: no discovery tools -> delegates to GenericCorrectionStrategy (no regression below baseline)")
_sentinel = {"tool_name": "SENTINEL_GENERIC"}
_orig = pe.GenericCorrectionStrategy.generate_correction
async def _fake_generic(self, fa, er):
    return _sentinel, [("delegated", None)]
pe.GenericCorrectionStrategy.generate_correction = _fake_generic
try:
    ex = FakeExecutor(scope_databases=["airlines_mvm"], tools=[])  # no discovery tools at all
    corrected, events = run(pe.DatabaseNotFoundStrategy(ex).generate_correction(failed_read(), err_3802()))
    check("delegated to generic when no deterministic recovery possible", corrected is _sentinel)
finally:
    pe.GenericCorrectionStrategy.generate_correction = _orig

print("\n[7] Full CorrectionHandler integration: 3802 -> deterministic path, LLM never called")
ex = FakeExecutor(scope_databases=["airlines_mvm"], tools=["base_tableList", "base_databaseList"])
handler = pe.CorrectionHandler(ex)  # phase_state=None -> uses ex.correction_history
corrected, events = run(handler.attempt_correction(failed_read(), err_3802()))
check("handler returns deterministic base_tableList", corrected.get("tool_name") == "base_tableList")
check("correction_history recorded exactly 1 attempt", len(ex.correction_history) == 1)
check("history strategy == DatabaseNotFoundStrategy", ex.correction_history and ex.correction_history[0]["strategy"] == "DatabaseNotFoundStrategy")

print("\n[8] No-regression: strategy routing for other error classes is unchanged")
h = pe.CorrectionHandler(FakeExecutor())
check("3802 -> DatabaseNotFoundStrategy", first_strategy_name(h, "[Error 3802] Database 'airport' does not exist.") == "DatabaseNotFoundStrategy")
check("table error -> TableNotFoundStrategy", first_strategy_name(h, "Object 'db.tbl' does not exist") == "TableNotFoundStrategy")
check("column error -> ColumnNotFoundStrategy", first_strategy_name(h, "Column 'colx' does not exist") == "ColumnNotFoundStrategy")
check("unrelated error -> GenericCorrectionStrategy", first_strategy_name(h, "Some totally different failure") == "GenericCorrectionStrategy")


# ── TeradataLimitStrategy: LIMIT n → TOP n, mechanical, 0-LLM (dd40d071) ─────
import asyncio as _a
from trusted_data_agent.agent.phase_executor import TeradataLimitStrategy as _TL
_tl = _TL.__new__(_TL)

def _fix(sql, err="[Teradata Database] [Error 3706] Syntax error: expected something between"):
    if not _tl.can_handle(err):
        return "NO-HANDLE"
    c, ev = _a.run(_tl.generate_correction(
        {"tool_name": "base_readQuery", "arguments": {"sql": sql}}, {"data": err}))
    return (c or {}).get("arguments", {}).get("sql")

check("LIMIT 5 becomes SELECT TOP 5 (order-by preserved)",
      _fix("SELECT a, SUM(b) AS r FROM db.t GROUP BY a ORDER BY r DESC LIMIT 5")
      == "SELECT TOP 5 a, SUM(b) AS r FROM db.t GROUP BY a ORDER BY r DESC")
check("a trailing semicolon is absorbed",
      _fix("SELECT a FROM t LIMIT 10;") == "SELECT TOP 10 a FROM t")
check("no LIMIT in the SQL -> falls through to the LLM",
      _fix("SELECT a FROM t WHERE b=1") is None)
check("DISTINCT falls through (Teradata refuses TOP with DISTINCT)",
      _fix("SELECT DISTINCT a FROM t LIMIT 10") is None)
check("an existing TOP falls through (not mechanical)",
      _fix("SELECT TOP 3 a FROM t LIMIT 3") is None)
check("a non-3706 error is not handled", _fix("SELECT a FROM t LIMIT 5",
      err="[Error 2616] numeric overflow") == "NO-HANDLE")
check("non-string sql never raises",
      _a.run(_tl.generate_correction({"arguments": {"sql": None}}, {"data": "3706"}))[0] is None)
check("the strategy is registered BEFORE the generic fallback",
      "TeradataLimitStrategy(executor)" in open(
          "src/trusted_data_agent/agent/phase_executor.py").read().split(
          "GenericCorrectionStrategy(executor)")[0].split("self.strategies")[-1])

print(f"\n=== Step 1 results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
