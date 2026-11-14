"""
Step 3 verification — preflight SQL schema grounding at the MCP seam.

Reproduces turn-6: `SELECT * FROM airport.station` where `airport` is not a real database.
Proves the guard catches a PROVABLY-nonexistent database reference before execution, while
being regression-safe by construction:

  * no cached catalog                          -> no-op (fail-open)
  * valid database (incl. a real NON-KG db)    -> not flagged (no false positives)
  * system database (DBC)                      -> not flagged
  * only scope=all lists are cached            -> a partial (scope=user) list never mis-flags
  * default mode 'observe'                     -> logs only, NEVER blocks (byte-identical)
  * non-SQL tool calls                         -> untouched (Ideate/Focus never affected)
  * malformed input                            -> fail-open

Cross-step: the block result is 3802-shaped, so the Step-1 DatabaseNotFoundStrategy engages.

Run:  PYTHONPATH=src python3 test/test_preflight_schema.py
"""
import re
import sys

from trusted_data_agent.agent.preflight import catalog, guard
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.agent.phase_executor import RECOVERABLE_TOOL_ERRORS


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


def catalog_result(*names):
    return {"status": "success", "results": [{"DatabaseName": n} for n in names]}


SID = "sess-preflight"


def reset(mode="observe"):
    catalog._reset_all()
    APP_CONFIG.PREFLIGHT_SQL_MODE = mode


print("\n[1] extract_sql_db_refs: FROM/JOIN qualifiers via sqlglot (+regex fallback)")
check("simple FROM", catalog.extract_sql_db_refs("SELECT * FROM airport.station") == ["airport"])
refs = catalog.extract_sql_db_refs("SELECT * FROM airlines_mvm.awb a JOIN airport.gate g ON a.x=g.y")
check("FROM + JOIN both captured", set(refs) == {"airlines_mvm", "airport"})
check("unqualified table -> no db refs", catalog.extract_sql_db_refs("SELECT * FROM awb") == [])
check("empty/None safe", catalog.extract_sql_db_refs("") == [] and catalog.extract_sql_db_refs(None) == [])

print("\n[2] catalog record/lookup — only scope=all is authoritative")
reset()
check("no catalog initially", not catalog.has_catalog(SID))
guard.maybe_record_catalog("base_databaseList", {"scope": "user"}, catalog_result("airlines_mvm"), SID)
check("scope=user NOT cached (would risk false positives)", not catalog.has_catalog(SID))
guard.maybe_record_catalog("base_databaseList", {"scope": "all"},
                           catalog_result("airlines_mvm", "fitness_db", "DBC"), SID)
check("scope=all cached", catalog.has_catalog(SID))
guard.maybe_record_catalog("base_readQuery", {"scope": "all"}, catalog_result("x"), SID + "2")
check("non-databaseList tool never caches", not catalog.has_catalog(SID + "2"))

print("\n[3] nonexistent_db_refs — no false positives (the regression-safety core)")
check("hallucinated 'airport' flagged", catalog.nonexistent_db_refs(SID, "SELECT * FROM airport.station") == ["airport"])
check("valid KG db not flagged", catalog.nonexistent_db_refs(SID, "SELECT * FROM airlines_mvm.awb") == [])
check("valid NON-KG db (fitness_db) not flagged", catalog.nonexistent_db_refs(SID, "SELECT * FROM fitness_db.c") == [])
check("system db (DBC) not flagged", catalog.nonexistent_db_refs(SID, "SELECT * FROM DBC.TablesV") == [])
check("no catalog -> never flags (fail-open)", catalog.nonexistent_db_refs("unknown-sess", "SELECT * FROM airport.x") == [])

print("\n[4] guard.check_sql — mode ladder (default observe never blocks)")
reset("observe")
guard.maybe_record_catalog("base_databaseList", {"scope": "all"}, catalog_result("airlines_mvm"), SID)
check("observe: hallucinated ref -> None (executes anyway)",
      guard.check_sql("base_readQuery", {"sql": "SELECT * FROM airport.station"}, SID) is None)

reset("enforce")
guard.maybe_record_catalog("base_databaseList", {"scope": "all"}, catalog_result("airlines_mvm"), SID)
blk = guard.check_sql("base_readQuery", {"sql": "SELECT * FROM airport.station"}, SID)
check("enforce: hallucinated ref -> block result", isinstance(blk, dict) and blk.get("status") == "error")
check("enforce: block marked preflight", blk and blk.get("preflight") is True)
check("enforce: valid ref -> None (not blocked)",
      guard.check_sql("base_readQuery", {"sql": "SELECT * FROM airlines_mvm.awb"}, SID) is None)
check("enforce: no catalog -> None (fail-open)",
      guard.check_sql("base_readQuery", {"sql": "SELECT * FROM airport.x"}, "sess-empty") is None)
check("enforce: non-SQL call -> None (Ideate/Focus untouched)",
      guard.check_sql("base_tableList", {"database_name": "airlines_mvm"}, SID) is None)

reset("off")
guard.maybe_record_catalog("base_databaseList", {"scope": "all"}, catalog_result("airlines_mvm"), SID)
check("off: never blocks",
      guard.check_sql("base_readQuery", {"sql": "SELECT * FROM airport.station"}, SID) is None)

print("\n[5] cross-step: block result is 3802-shaped -> Step-1 DatabaseNotFoundStrategy engages")
blk = guard.block_result(["airport"])
pat = RECOVERABLE_TOOL_ERRORS["database_not_found"]
combined = f"{blk.get('data','')} {blk.get('error','')}"
check("block error matches database_not_found regex", bool(re.search(pat, combined, re.IGNORECASE)))
check("block does NOT match table_not_found regex",
      not re.search(RECOVERABLE_TOOL_ERRORS["table_not_found"], combined, re.IGNORECASE))

print("\n[6] fail-open on malformed input (never breaks a turn)")
reset("enforce")
guard.maybe_record_catalog("base_databaseList", {"scope": "all"}, catalog_result("airlines_mvm"), SID)
check("None arguments -> None", guard.check_sql("base_readQuery", None, SID) is None)
check("non-str sql -> None", guard.check_sql("base_readQuery", {"sql": 42}, SID) is None)
check("empty sql -> None", guard.check_sql("base_readQuery", {"sql": "   "}, SID) is None)
check("missing session_id -> None", guard.check_sql("base_readQuery", {"sql": "SELECT * FROM airport.x"}, None) is None)

# restore default so we never leave the process in enforce
APP_CONFIG.PREFLIGHT_SQL_MODE = "observe"

print(f"\n=== Step 3 results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
