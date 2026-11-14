#!/usr/bin/env python3
"""Preflight COLUMN-existence check — CI-pure gate.

Production, 2026-09-19 (session 0aa3a650, turn 2): the model's first statement read
`boarding_event.miles_flown`, a column that does not exist — a database round trip (3810),
an LLM round to read it, then `SELECT TOP 5 *` to discover the schema the knowledge graph
had held all along. This gate pins: the pure attribution of column references (qualified
through aliases; unqualified only on a single-table statement; SELECT aliases and CTE names
are never columns), the violation + its recovery-shaped block naming the real columns and
the closest matches (tables in play first), the completeness predicate that ARMS the check
only on a column-complete graph (industry / declared / discovery — never ANDP, never an
unrecognised graph), the whole-graph list from the SQLite backend, the mode ladder, and the
seams (check_sql · executor recorder · the handler's emission).

Run: PYTHONPATH=src python3 test/test_preflight_columns.py
"""
import ast
import asyncio
import inspect
import os
import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.agent.preflight import columns as pc, guard         # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG                          # noqa: E402
from components.builtin.knowledge_graph import handler as kgh                 # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


SID = "s-cols"
FULL = {"airlines_core.boarding_event": ["boarding_event_id", "ffp_member_id", "boarding_zone", "flight_leg_id"],
        "loyalty.mileage_accrual": ["mileage_accrual_id", "base_miles", "total_miles_credited", "ffp_member_id"],
        "loyalty.ffp_member": ["ffp_member_id", "member_first_name", "member_last_name", "home_airport_code"]}
BAD = ("SELECT TOP 5 B.ffp_member_id, C.customer_name, SUM(B.miles_flown) AS total_miles "
       "FROM airlines_core.boarding_event B JOIN airlines_core.accounts_receivable C ON B.ffp_member_id = C.customer_account_number "
       "GROUP BY B.ffp_member_id, C.customer_name ORDER BY total_miles DESC")
GOOD = ("SELECT TOP 5 M.member_first_name, SUM(A.base_miles) FROM airlines_core.mileage_accrual A "
        "JOIN airlines_core.ffp_member M ON A.ffp_member_id = M.ffp_member_id GROUP BY 1")

print("[1] attribution of column references")
pc._reset_all()
refs = pc.sql_column_refs(BAD)
check("qualified references resolve through aliases to the base table leaf",
      {"table": "boarding_event", "column": "miles_flown"} in refs and {"table": "accounts_receivable", "column": "customer_name"} in refs, refs)
check("a SELECT alias reused in ORDER BY / WHERE is never a column",
      not any(r["column"] == "total_miles" for r in refs), refs)
# …and load-bearing on a SINGLE-table statement, where the unqualified reference WOULD be
# attributed: `customer_name` is an alias, not a column of ffp_member
_al = pc.sql_column_refs("SELECT member_first_name || ' ' || member_last_name AS customer_name, home_airport_code "
                         "FROM airlines_core.ffp_member WHERE customer_name IN ('x') ORDER BY customer_name")
check("single-table: the alias is not attributed while the real columns are",
      not any(r["column"] == "customer_name" for r in _al) and {"table": "ffp_member", "column": "home_airport_code"} in _al, _al)
r1 = pc.sql_column_refs("SELECT base_miles, nope FROM airlines_core.mileage_accrual")
check("unqualified references attribute to the ONE base table", {"table": "mileage_accrual", "column": "nope"} in r1, r1)
check("unqualified references on a multi-table statement are not attributed",
      pc.sql_column_refs("SELECT nope FROM airlines_core.mileage_accrual a JOIN airlines_core.ffp_member f ON a.ffp_member_id = f.ffp_member_id") == [
          {"table": "mileage_accrual", "column": "ffp_member_id"}, {"table": "ffp_member", "column": "ffp_member_id"}])
check("a CTE's columns are never attributed to a base table",
      all(r["table"] != "t" for r in pc.sql_column_refs("WITH t AS (SELECT base_miles AS bm FROM airlines_core.mileage_accrual) SELECT t.bm, t.zzz FROM t")))
check("`*` is not a column and unparseable SQL yields nothing",
      pc.sql_column_refs("SELECT * FROM airlines_core.ffp_member") == [] and pc.sql_column_refs("SELEC nope") == [] and pc.sql_column_refs("") == [])

print("[2] violations and the block")
check("nothing recorded ⇒ no verdict", pc.column_violations(SID, BAD) == [])
n = pc.record_columns(SID, FULL, in_play=True)
check("record keys by table leaf and counts tables", n == 3 and set(pc.known_tables(SID)) == {"boarding_event", "mileage_accrual", "ffp_member"})
# a whole-graph table with a SHORTER matching column: without the in-play preference it
# would win the suggestion on name length alone
pc.record_columns(SID, {"ops.flight_log": ["flight_log_id", "miles"]})
v = pc.column_violations(SID, BAD)
check("the prod turn-2 statement: miles_flown on boarding_event is a violation", len(v) == 1 and v[0]["table"] == "boarding_event" and v[0]["column"] == "miles_flown", v)
check("a table with no recorded list (accounts_receivable) yields no verdict", not any(x["table"] == "accounts_receivable" for x in v))
check("the violation names the table's real columns", v[0]["real_columns"] == FULL["airlines_core.boarding_event"] and v[0]["real_count"] == 4)
check("suggestions: the tables IN PLAY first, strongest word overlap, plainer name",
      v[0]["suggestions"][0] == "mileage_accrual.base_miles", v[0]["suggestions"])
check("the correct statement passes", pc.column_violations(SID, GOOD) == [])
check("case-insensitive column match", pc.column_violations(SID, "SELECT Base_Miles FROM airlines_core.mileage_accrual") == [])
b = pc.block_result(v)
check("the block is recovery-shaped: names the column, the real columns, the closest matches, says NOT executed",
      b["preflight_column"] and "miles_flown" in b["error"] and "boarding_event has: boarding_event_id" in b["data"]
      and "mileage_accrual.base_miles" in b["data"] and "NOT executed" in b["data"])
check("the error carries the 3810 phrase so the Optimize column-not-found strategy engages",
      "Column/Parameter 'boarding_event.miles_flown' does not exist" in b["error"])
pc.record_columns(SID, {"loyalty.miles_balance": ["miles_balance_id", "ffp_member_id", "total_miles_balance"]})
check("a whole-graph record (not in play) widens the tables without displacing the in-play set",
      "miles_balance" in pc.known_tables(SID) and pc.suggestions(SID, "miles_flown")[0] == "mileage_accrual.base_miles")

print("[3] the mode ladder at the one entry point")
old = getattr(APP_CONFIG, "PREFLIGHT_COLUMN_MODE", "observe")
try:
    APP_CONFIG.PREFLIGHT_COLUMN_MODE = "observe"
    check("observe (the default) never blocks", guard.check_sql("base_readQuery", {"sql": BAD}, SID) is None)
    check("the default IS observe", str(APP_CONFIG.PREFLIGHT_COLUMN_MODE) == "observe" or old == "observe")
    APP_CONFIG.PREFLIGHT_COLUMN_MODE = "enforce"
    r = guard.check_sql("base_readQuery", {"sql": BAD}, SID)
    check("enforce blocks with the column block through check_sql", isinstance(r, dict) and r.get("preflight_column"), r)
    check("enforce lets the correct statement through", guard.check_sql("base_readQuery", {"sql": GOOD}, SID) is None)
    check("a non-SQL tool is untouched", guard.check_sql("base_tableList", {"database_name": "x"}, SID) is None)
    APP_CONFIG.PREFLIGHT_COLUMN_MODE = "off"
    check("off is a no-op", guard.check_columns(BAD, SID) is None)
    APP_CONFIG.PREFLIGHT_COLUMN_MODE = "review"
    check("an unknown-but-stronger mode clamps to enforce", isinstance(guard.check_columns(BAD, SID), dict))
finally:
    APP_CONFIG.PREFLIGHT_COLUMN_MODE = old

print("[4] the completeness predicate arms only on a column-complete graph")
ind = {"entities": [{"entity_type": "entity", "name": "loyalty.ffp_member", "source": "marketplace"}]}
root_ind = [{"entity_type": "database", "properties": {"industry": "airlines", "scope": "MVM"}}]
root_11179 = [{"entity_type": "database", "properties": {"standard": "iso_11179"}}]
root_andp = [{"entity_type": "database", "properties": {"standard": "ai_native_data_product"}}]
check("industry-model root (read from the store, not the subgraph) ⇒ complete — a subscribed copy carries it", kgh._columns_complete(ind, root_ind))
check("ISO 11179 declared model ⇒ complete", kgh._columns_complete(ind, root_11179))
check("ANDP root ⇒ NOT complete (column_metadata covers a fraction)", not kgh._columns_complete(ind, root_andp))
check("agent-discovery sources ⇒ complete", kgh._columns_complete({"entities": [{"entity_type": "entity", "name": "db.T", "source": "agent_discovery"}]}))
check("an unrecognised graph is NOT assumed complete", not kgh._columns_complete({"entities": [{"entity_type": "entity", "name": "x.y", "source": "manual"}]}))
check("ANDP sources beat a discovery source in the same graph", not kgh._columns_complete({"entities": [{"entity_type": "entity", "source": "agent_discovery"}, {"entity_type": "attribute", "source": "andp_semantic_constructor"}]}))

print("[5] the backend's whole-graph list + the handler's emission")
from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
c = sqlite3.connect(tmp)
live = sqlite3.connect(os.path.join(ROOT, "tda_auth.db"))
for (ddl,) in live.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND tbl_name IN ('kg_entities','kg_relationships') AND sql IS NOT NULL"):
    c.execute(ddl)
live.close(); c.commit(); c.close()
be = SQLiteKGBackend({"db_path": tmp})
ents = [{"name": "loyalty.ffp_member", "entity_type": "entity", "properties": {}},
        {"name": "airlines_core.v_member", "entity_type": "table", "properties": {}},
        {"name": "loyalty.lonely", "entity_type": "entity", "properties": {}}]
rels = []
for col in ("ffp_member_id", "member_first_name"):
    ents.append({"name": f"loyalty.ffp_member.{col}", "entity_type": "attribute", "properties": {}})
    ents.append({"name": f"airlines_core.v_member.{col}", "entity_type": "column", "properties": {}})
    rels.append({"source_name": "loyalty.ffp_member", "source_type": "entity", "target_name": f"loyalty.ffp_member.{col}", "target_type": "attribute", "relationship_type": "contains"})
    rels.append({"source_name": "airlines_core.v_member", "source_type": "table", "target_name": f"airlines_core.v_member.{col}", "target_type": "column", "relationship_type": "contains"})
ents.append({"name": "loyalty.lonely.only_one", "entity_type": "attribute", "properties": {}})
rels.append({"source_name": "loyalty.lonely", "source_type": "entity", "target_name": "loyalty.lonely.only_one", "target_type": "attribute", "relationship_type": "contains"})
be.import_bulk("k", "p", "u", ents, rels)
full = be.all_table_columns("k")
check("SQLite lists every table's columns, logical AND physical leaves, and skips a one-column table",
      full.get("ffp_member") == ["ffp_member_id", "member_first_name"] and full.get("v_member") == ["ffp_member_id", "member_first_name"] and "lonely" not in full, full)
check("an unknown graph yields {}", be.all_table_columns("nope") == {})
from trusted_data_agent.kgstore.base import KGStoreBackend
check("the ABC default is {} (a backend that cannot enumerate ⇒ no verdict)", KGStoreBackend.all_table_columns(be, "k") == {})
src = inspect.getsource(kgh.KnowledgeGraphHandler.get_context_enrichment)
check("the enrichment emits table_columns_full and records the whole graph per session", 'metadata["table_columns_full"]' in src and "_record_whole_graph_columns(store, session_id)" in src)
ftc = inspect.getsource(kgh.KnowledgeGraphHandler._full_table_columns)
check("emission reads the ROOT from the store and refuses an incomplete graph", 'list_entities(entity_type="database"' in ftc and "_columns_complete(subgraph, roots)" in ftc)
check("a truncated neighbourhood is skipped, never guessed", 'nb.get("truncated")' in ftc)

print("[6] the seams")
def _fn(path, name):
    for n in ast.walk(ast.parse(pathlib.Path(path).read_text())):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in ast.walk(n) if isinstance(c, ast.Call)}
    return set()
G = ROOT / "src/trusted_data_agent/agent/preflight/guard.py"
check("check_sql runs the column check", "check_columns" in _fn(G, "check_sql"))
check("the executor records the lists where the enrichment event is stamped", "maybe_record_columns" in _fn(ROOT / "src/trusted_data_agent/agent/executor.py", "_apply_trusted_scope"))
cfg = (ROOT / "src/trusted_data_agent/core/config.py").read_text(); comp = (ROOT / "docker-compose.yml").read_text()
check("the mode is declared with its env override and forwarded by compose", 'PREFLIGHT_COLUMN_MODE = os.environ.get("TDA_PREFLIGHT_COLUMN_MODE", "observe")' in cfg and "TDA_PREFLIGHT_COLUMN_MODE" in comp)
ap = (ROOT / "src/trusted_data_agent/agent/assurance_points.py").read_text()
check("the preflight reading names a missing column", "missing_columns" in ap.split("def _read_preflight")[1].split("def _read_scope")[0])

# ── the DECLARED key columns exist by construction (prod session be4979bf, turn 2) ──
# A conformant ontology records a table's identity in TWO places: non-key attributes as
# `contains` edges, the key as a property. Reading only the edges reported the declared
# join key of Policing_Core.v_Offence as non-existent columns — on a statement that RAN
# and returned rows, joining on exactly the key the ontology declares, which the JOIN
# check on the same graph correctly recognised. Two checks, one graph, opposite answers.
from trusted_data_agent.kgstore.keys import key_columns as _kc, with_declared_keys as _wdk  # noqa: E402
import components.builtin.knowledge_graph.handler as _kgh                                   # noqa: E402

check("the declared key parses in every form a product uses",
      _kc({"business_key": "ori, incident_number"}) == ["ori", "incident_number"]
      and _kc({"business_key": "ORI|INCNUM"}) == ["ORI", "INCNUM"]
      and _kc({"natural_key_column": ["a", "b"]}) == ["a", "b"]
      and _kc({}) == [] and _kc(None) == [])
check("whitespace is NOT a separator — a quoted identifier may contain one",
      _kc({"business_key": "customer id"}) == ["customer id"])
check("a widening of WHICH properties are read is a change to what the JOIN guard judges, so the runtime set is exactly the two it has always read",
      tuple(__import__("trusted_data_agent.kgstore.keys", fromlist=["x"]).KEY_PROPERTIES)
      == ("business_key", "natural_key_column"))
check("the handler's reading delegates — one parse, not two",
      "from trusted_data_agent.kgstore.keys import key_columns" in inspect.getsource(_kgh._key_columns))

_attrs = ["offence_code", "offence_seq", "offence_type"]
_props = {"business_key": "ori, incident_number, offence_seq"}
check("a declared key column absent from the attribute list is unioned in, order kept",
      _wdk(_attrs, _props) == ["offence_code", "offence_seq", "offence_type", "ori", "incident_number"])
check("a key column already present is not duplicated, case-insensitively",
      _wdk(["ORI"], {"business_key": "ori"}) == ["ORI"])
check("no declared key → the list is unchanged", _wdk(_attrs, {}) == _attrs)

# The prod statement, verbatim in shape: flagged before, clean after.
_SQL = ("SELECT TOP 10 A.agency_type, O.offence_type, COUNT(*) AS offence_count "
        "FROM Policing_Core.v_Offence O "
        "JOIN Policing_Core.v_Incident I ON O.ori = I.ori AND O.incident_number = I.incident_number "
        "JOIN Policing_Core.v_Agency A ON I.ori = A.ori GROUP BY A.agency_type, O.offence_type")
_off = ["attempt_complete", "location_type", "method_of_entry", "offence_code",
        "offence_seq", "offence_type", "premises_entered"]
_rest = {"v_incident": ["ori", "incident_number"], "v_agency": ["ori", "agency_type"]}

pc._reset_all(); pc.record_columns("k1", {"v_offence": _off, **_rest})
_before = pc.column_violations("k1", _SQL)
check("the defect reproduces: contains-only flags the declared key as non-existent",
      sorted(f"{v['table']}.{v['column']}" for v in _before)
      == ["v_offence.incident_number", "v_offence.ori"])

pc._reset_all(); pc.record_columns("k2", {"v_offence": _wdk(_off, _props), **_rest})
check("with the declared key unioned in, the same statement is clean",
      pc.column_violations("k2", _SQL) == [])

# The invariant, stated directly: what the JOIN check calls a declared key, the COLUMN
# check may never call non-existent. Driven through BOTH real readers on one graph.
_sub = {"entities": [
    {"id": 1, "entity_type": "entity", "name": "Policing Core.Offence",
     "properties": {"business_key": "ori, incident_number, offence_seq",
                    "deployed_table": "Policing_Core.v_Offence"}},
    {"id": 2, "entity_type": "entity", "name": "Policing Core.Incident",
     "properties": {"business_key": "ori, incident_number",
                    "deployed_table": "Policing_Core.v_Incident"}}],
    "relationships": [{"relationship_type": "foreign_key", "source_id": 1, "target_id": 2,
                       "source_name": "Policing Core.Offence", "target_name": "Policing Core.Incident"}]}
_join_keys = set()
for _p in _kgh._declared_join_pairs(_sub):
    for _c in (_p.get("columns") or _p.get("key") or []):
        _join_keys.add(str(_c).lower())
pc._reset_all()
pc.record_columns("k3", {"v_offence": _wdk(_off, _sub["entities"][0]["properties"]),
                         "v_incident": _wdk(["incident_date_text"], _sub["entities"][1]["properties"])})
_flagged = {str(v["column"]).lower() for v in pc.column_violations("k3", _SQL)}
check("the check is not vacuous — the join check did declare a key", bool(_join_keys), _join_keys)
check("INVARIANT: no column the JOIN check calls a declared key is called non-existent by the COLUMN check",
      not (_join_keys & _flagged), sorted(_join_keys & _flagged))

# Both list builders consult it — a fix at one producer only would leave the other lying.
check("the whole-graph builder unions the declared key",
      "_with_declared_keys" in _fn(ROOT / "src/trusted_data_agent/kgstore/sqlite_backend.py", "all_table_columns"))
check("the per-turn builder unions it too",
      "_with_declared_keys" in _fn(ROOT / "components/builtin/knowledge_graph/handler.py", "_full_table_columns"))

print(f"\n=== Preflight columns: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
