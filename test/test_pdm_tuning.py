import json
"""PROP-008 C1 — the workload recommender (`dbdeploy/tuning.py`), CI-pure.

  §S  statement analysis: filters (eq / range / date_range), joins, groups, attributed through aliases
  §A  aggregation: per table and column, findings counted only against the tables they name
  §R  the rules: statistics (workload · no confidence · product join on a declared key · stale),
      the covering join index (the policing shape), partition on a date range — and every NO
  §D  determinism, the modelling finding, "no workload yet", the Teradata rendering
  §P  purity: the recommender writes nothing (no backend, no connection, no SQL execution)

Run: python test/test_pdm_tuning.py
"""
import ast
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.dbdeploy import tuning as T  # noqa: E402
from trusted_data_agent.dbdeploy.dialects.teradata import render_tuning  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def by_rule(out, rule):
    return [p for p in out["proposals"] if p["rule"] == rule]


# ── §S ──
print("[§S] statement analysis")
a = T.analyze_statement("SELECT i.ORI, COUNT(*) FROM nibrs.Incident i JOIN nibrs.Arrestee a "
                        "ON a.ORI = i.ORI AND a.INCNUM = i.INCNUM WHERE i.V20061 IN ('262','265') "
                        "AND i.INCDATE BETWEEN DATE '2023-01-01' AND DATE '2023-03-31' GROUP BY i.ORI")
check("both tables, qualified", a["tables"] == ["nibrs.incident", "nibrs.arrestee"], a["tables"])
check("the IN filter is attributed through the alias",
      {"table": "nibrs.incident", "column": "v20061", "kind": "in"} in a["filters"], a["filters"])
check("a range against DATE literals is a date_range",
      {"table": "nibrs.incident", "column": "incdate", "kind": "date_range"} in a["filters"])
check("the ON equalities are joins, never filters",
      not any(f["column"] in ("ori", "incnum") for f in a["filters"])
      and {"tables": ["nibrs.arrestee", "nibrs.incident"], "columns": ["ori", "ori"]} in a["joins"], a["joins"])
check("GROUP BY columns", {"table": "nibrs.incident", "column": "ori"} in a["groups"])
check("a bare column is attributed when one table is read",
      {"table": "t", "column": "x", "kind": "range"} in T.analyze_statement("SELECT 1 FROM t WHERE x > 5")["filters"])
check("an unparseable statement is evidence of nothing", T.analyze_statement("SELEC FROM WHERE")["tables"] == [])

# ── §A ──
print("[§A] aggregation")
W = [{"id": "s1", "sql": "SELECT COUNT(*) FROM db.big b JOIN db.small s ON s.k = b.k WHERE b.c1 = 'x'",
      "findings": [{"kind": "full_scan", "tables": ["db.big"]}]}]
ag = T.aggregate(W)
check("a finding that names its table counts only against that table",
      "s1" in ag["tables"]["db.big"]["findings"].get("full_scan", set())
      and not ag["tables"]["db.small"]["findings"].get("full_scan"))
ag2 = T.aggregate([{"id": "s1", "sql": W[0]["sql"], "findings": [{"kind": "no_confidence"}]}])
check("a finding with no tables counts against every table the statement read",
      all("s1" in ag2["tables"][t]["findings"]["no_confidence"] for t in ("db.big", "db.small")))
check("a statement without sql is ignored", T.aggregate([{"id": "x"}, None])["tables"] == {})

# ── §R ──
print("[§R] the rules")
# product join on a declared key → statistics on the key
pj = [{"id": "p1", "sql": "SELECT COUNT(*) FROM db.orders o JOIN db.customers c ON o.cust_id = c.cust_id",
       "findings": [{"kind": "product_join"}]}]
out = T.recommend(T.aggregate(pj), {"db.orders": {"key": ["order_id"]}, "db.customers": {"key": ["cust_id"]}})
props = by_rule(out, "stats_on_product_join_key")
check("a product-join finding on a declared key yields a statistics proposal",
      [(p["table"], p["columns"]) for p in props] == [("db.customers", ["cust_id"])], [(p["table"], p["columns"]) for p in props])
check("…only for the side whose declared key it is (orders' key is order_id)",
      not any(p["table"] == "db.orders" for p in props))
check("…and not when statistics already exist",
      not by_rule(T.recommend(T.aggregate(pj), {"db.customers": {"key": ["cust_id"], "stats": {"cust_id": 3}}}),
                  "stats_on_product_join_key"))

# workload columns
wl = [{"id": f"w{i}", "sql": f"SELECT * FROM db.t WHERE region = 'r{i}'"} for i in range(2)]
out = T.recommend(T.aggregate(wl), {})
check("a column filtered in two statements without statistics → statistics",
      [p["columns"] for p in by_rule(out, "stats_on_workload_columns")] == [["region"]])
check("…in ONE statement → nothing (below the threshold)",
      not T.recommend(T.aggregate(wl[:1]), {})["proposals"])
nc = [{"id": "n1", "sql": "SELECT * FROM db.t WHERE region = 'x'", "findings": [{"kind": "no_confidence"}]}]
check("…in one statement that planned with no confidence → statistics (that rule)",
      [p["rule"] for p in T.recommend(T.aggregate(nc), {})["proposals"]] == ["stats_where_no_confidence"])
check("fresh statistics → nothing", not T.recommend(T.aggregate(wl), {"db.t": {"stats": {"region": 2}}})["proposals"])
stale = T.recommend(T.aggregate(wl), {"db.t": {"stats": {"region": 120}}})["proposals"]
check("stale statistics on a used column → a recollect proposal",
      [(p["rule"], p.get("recollect")) for p in stale] == [("stats_stale", True)] and "120 days" in stale[0]["evidence"])

# the covering join index — the policing shape
NIB = {"nibrs.incident": {"key": ["ORI", "INCNUM"], "rows": 12_351_550, "columns_count": 393,
                          "column_types": {"INCDATE": "CHAR(11)"}}}
scan = [{"id": f"q{i}", "sql": f"SELECT COUNT(*) FROM nibrs.Incident i WHERE i.V20081 = '2' OR i.V20061 IN ('26{i}')",
         "findings": [{"kind": "full_scan", "tables": ["nibrs.Incident"]}]} for i in range(3)]
out = T.recommend(T.aggregate(scan), NIB)
ji = by_rule(out, "covering_join_index")
check("a wide, large table scanned for a few predicate columns → ONE covering join index", len(ji) == 1, out["proposals"])
check("…on the declared key plus exactly the predicate columns",
      ji and ji[0]["columns"] == ["ori", "incnum", "v20061", "v20081"] and ji[0]["key"] == ["ori", "incnum"], ji and ji[0]["columns"])
check("…the evidence names the scans, the rows and the columns",
      ji and "3 statements scanned all 12,351,550 rows" in ji[0]["evidence"] and "v20081" in ji[0]["evidence"])
check("…with ONE statistic proposed on the declared key as a group",
      [p["columns"] for p in out["proposals"] if p["purpose"] == "statistics" and len(p["columns"]) > 1] == [["ori", "incnum"]],
      [p["columns"] for p in out["proposals"]])
check("…and none when that multi-column statistic exists (any column order)",
      not [p for p in T.recommend(T.aggregate(scan), {"nibrs.incident": {**NIB["nibrs.incident"], "stats": {"INCNUM,ORI": 3}}})["proposals"]
           if p["purpose"] == "statistics" and len(p["columns"]) > 1])
check("…and none when an existing join index already covers those columns",
      not by_rule(T.recommend(T.aggregate(scan), {"nibrs.incident": {**NIB["nibrs.incident"],
          "join_indexes": [["ORI", "INCNUM", "V20061", "V20081", "V30071"]]}}), "covering_join_index"))
check("…none on a narrow table", not by_rule(T.recommend(T.aggregate(scan),
      {"nibrs.incident": {**NIB["nibrs.incident"], "columns_count": 12}}), "covering_join_index"))
check("…none on a small table", not by_rule(T.recommend(T.aggregate(scan),
      {"nibrs.incident": {**NIB["nibrs.incident"], "rows": 50_000}}), "covering_join_index"))
check("…none without a declared key (nothing to index on)", not by_rule(T.recommend(T.aggregate(scan),
      {"nibrs.incident": {**NIB["nibrs.incident"], "key": []}}), "covering_join_index"))
check("…none from a single scan", not by_rule(T.recommend(T.aggregate(scan[:1]), NIB), "covering_join_index"))

# partition on a date range
dr = [{"id": f"d{i}", "sql": f"SELECT COUNT(*) FROM db.sales WHERE sold_on BETWEEN DATE '2023-0{i+1}-01' AND DATE '2023-0{i+1}-28'"}
      for i in range(3)]
SALES = {"db.sales": {"rows": 5_000_000, "column_types": {"sold_on": "DATE"}}}
part = by_rule(T.recommend(T.aggregate(dr), SALES), "partition_on_date_filter")
check("a frequent date filter yields a partition proposal", [p["columns"] for p in part] == [["sold_on"]])
check("…marked as needing a rebuild — never landed by the platform", part and part[0]["landing"] == "rebuild")
check("…not on a text-stored date (it cannot be partitioned as one)",
      not by_rule(T.recommend(T.aggregate(dr), {"db.sales": {"rows": 5_000_000, "column_types": {"sold_on": "CHAR(11)"}}}),
                  "partition_on_date_filter"))
check("…not on a table already partitioned",
      not by_rule(T.recommend(T.aggregate(dr), {"db.sales": {**SALES["db.sales"], "partitioned": True}}), "partition_on_date_filter"))
check("…not from two statements", not by_rule(T.recommend(T.aggregate(dr[:2]), SALES), "partition_on_date_filter"))

# ── §D ──
print("[§D] determinism, findings, rendering")
o1, o2 = T.recommend(T.aggregate(scan), NIB), T.recommend(T.aggregate(list(reversed(scan))), NIB)
check("the same workload in another order gives the same proposals, ids and order",
      [p["id"] for p in o1["proposals"]] == [p["id"] for p in o2["proposals"]])
check("a proposal id is stable across runs and differs per rule/table/columns",
      T.proposal_id("r", "db.t", ["A", "b"]) == T.proposal_id("r", "DB.T", ["b", "a"])
      and T.proposal_id("r", "db.t", ["a"]) != T.proposal_id("r", "db.t", ["b"]))
check("the join index leads, then statistics, then partition",
      [p["purpose"] for p in o1["proposals"]][0] == "join_index")
mf = T.recommend(T.aggregate(scan), NIB, usage_missed=["nibrs.incident"])["findings"]
check("a read table the enrichment never showed is a MODELLING finding, never DDL",
      mf and mf[0]["kind"] == "modelling" and "not a tuning one" in mf[0]["text"])
none = T.review([], {})
check("no workload → 'no workload yet', nothing proposed", not none["proposals"] and "no workload yet" in none["note"])
check("review states the workload size", T.review(scan, NIB)["workload"] == {"statements": 3})
check("every rule is a record with a reason and its thresholds",
      all(r.rationale and r.thresholds and r.purpose in T.PURPOSES for r in T.RULES))

st = render_tuning(by_rule(T.recommend(T.aggregate(wl), {}), "stats_on_workload_columns")[0])
check("statistics render as COLLECT STATISTICS on the quoted table", st == ['COLLECT STATISTICS COLUMN ("region") ON "db"."t"'], st)
jr = render_tuning(ji[0]) if ji else []
check("the covering join index renders as one CREATE JOIN INDEX + statistics on its key",
      len(jr) == 2 and jr[0].startswith('CREATE JOIN INDEX "nibrs"."ji_incident_cover" AS SELECT "ori", "incnum", "v20061", "v20081" FROM "nibrs"."incident" PRIMARY INDEX ("ori", "incnum")')
      and jr[1].startswith('COLLECT STATISTICS COLUMN ("ori", "incnum") ON "nibrs"."ji_incident_cover"'), jr)
pr = render_tuning(part[0]) if part else []
check("a partition renders as a comment for the reader, never an executable statement",
      pr and all(x.startswith("--") for x in pr))

# ── §E ──
print("[§E] the evidence (tuning_evidence)")
import asyncio  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from trusted_data_agent.core import tuning_evidence as E  # noqa: E402
ents = [{"name": "N.Incident", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "ORI,INCNUM",
                                                                       "deployed_table": "N_Domain.Incident"}},
        {"name": "N_Domain.Incident", "entity_type": "table", "properties": {"layer": "physical"}},
        {"name": "Customers", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "CustomerID"}},
        {"name": "fit.Customers", "entity_type": "table", "properties": {"layer": "physical"}},
        {"name": "NoKey", "entity_type": "table", "properties": {"layer": "physical"}}]
rels = [{"relationship_type": "implements", "source_name": "fit.Customers", "source_type": "table",
         "target_name": "Customers", "target_type": "entity"}]
sc = E.graph_scope(ents, rels)
check("the scope: a physical table node, and an entity's deployed_table, each with the declared key",
      sc.get("n_domain.incident") == ["ORI", "INCNUM"] and sc.get("fit.customers") == ["CustomerID"], sc)
check("…a table a logical entity implements takes that entity's key; an unqualified name is no table",
      "nokey" not in sc)
turns = [("s1", {"turn": 1, "execution_trace": [
            {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM N_Domain.Incident WHERE V1 = '2'"}}, "result": {"status": "success"}},
            {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT * FROM other.T"}}, "result": {"status": "success"}},
            {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT X FROM N_Domain.Incident"}}, "result": {"status": "error"}},
            {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "EXPLAIN SELECT 1 FROM N_Domain.Incident"}}, "result": {"status": "success"}}]}),
         ("s2", {"turn": 3, "execution_trace": [
            {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*)  FROM N_Domain.Incident WHERE V1 = '2';"}}, "result": {"status": "success"}}]})]
wl = E.workload_from_turns(turns, sc.keys())
check("the workload: successful in-scope statements only — no error, no other table, no EXPLAIN",
      [w["sql"] for w in wl] == ["SELECT COUNT(*) FROM N_Domain.Incident WHERE V1 = '2'"] * 2, [w["sql"] for w in wl])
check("…one item per OCCURRENCE (frequency is evidence), normalized, named by session and turn",
      [w["id"] for w in wl] == ["s1:1:0", "s2:3:0"])
_real_scope = E._in_scope
E._in_scope = lambda sql, tables: True      # isolate the guards from the scope reader
try:
    _all = [w["sql"] for w in E.workload_from_turns(turns, sc.keys())]
finally:
    E._in_scope = _real_scope
check("…an explained query and a failed call are never workload, even where the scope reader admits them",
      not any(q.upper().startswith("EXPLAIN") for q in _all) and "SELECT X FROM N_Domain.Incident" not in _all, _all)
plan = {"steps": [{"id": "1", "inputs": ["N_Domain.Incident"]}, {"id": "2", "inputs": ["Spool 2", "fit.Customers"]}],
        "findings": [{"kind": "full_scan", "severity": "info", "steps": ["1"]},
                     {"kind": "no_confidence", "severity": "warn", "steps": ["2"]}]}
check("a finding names the BASE tables its steps read, never a spool",
      E.findings_with_tables(plan) == [{"kind": "full_scan", "severity": "info", "tables": ["n_domain.incident"]},
                                       {"kind": "no_confidence", "severity": "warn", "tables": ["fit.customers"]}])
now = datetime(2026, 10, 7, tzinfo=timezone.utc)
check("statistics age in days from the dictionary's timestamp", round(E._age_days("2026-09-07 00:00:00", now)) == 30
      and E._age_days(None, now) is None and E._age_days("garbage", now) is None)


class _Info:
    row_count = 12_000_000


class _Idx:
    def __init__(self, kind, cols):
        self.kind, self.columns = kind, cols


class _Col:
    def __init__(self, n, t):
        self.name, self.data_type, self.stats = n, t, {}


class _Det:
    info = _Info()
    columns = [_Col("ORI", "CHAR(9)"), _Col("INCNUM", "CHAR(12)"), _Col("V1", "CHAR(2)")]
    indexes = [_Idx("primary", ["ORI", "INCNUM"])]


class _Prov:
    async def describe_object(self, db, name):
        if name.lower() == "missing":
            raise RuntimeError("not in the dictionary")
        return _Det()


sent = []


async def _fake_rows(creds, sql, max_rows=0, readonly=False):
    sent.append(sql)
    if "JoinIndicesV" in sql:
        return ["jdb", "jname"], [["N_Domain", "ji1"]]
    if "DBC.ColumnsV" in sql:
        return ["columnname"], [["ORI"], ["INCNUM"], ["V1"]]
    if "IndexConstraintsV" in sql:
        return ["n"], [[0]]
    if "DBC.StatsV" in sql:
        return ["col", "ts"], [["ORI,INCNUM", "2026-10-01 00:00:00"], ["V1", "2026-05-01 00:00:00"]]
    return [], []


import trusted_data_agent.dbcatalog.sql_runner as _SR  # noqa: E402
_real = _SR.run_rows
_SR.run_rows = _fake_rows
try:
    facts = asyncio.run(E.gather_facts({}, {"n_domain.incident": [], "n_domain.missing": ["K"]}, provider=_Prov(), now=now))
finally:
    _SR.run_rows = _real
fi = facts.get("n_domain.incident") or {}
check("facts: rows, column count and types", fi.get("rows") == 12_000_000 and fi.get("columns_count") == 3
      and fi.get("column_types", {}).get("V1") == "CHAR(2)")
check("…no declared key → the primary index, and it says so", fi.get("key") == ["ORI", "INCNUM"] and fi.get("key_source") == "primary index")
check("…statistics as the dictionary names them, multi-column included, with their age",
      round(fi["stats"]["ORI,INCNUM"]) == 6 and round(fi["stats"]["V1"]) == 159, fi.get("stats"))
check("…the join indexes already on the table, with their columns", fi.get("join_indexes") == [["ORI", "INCNUM", "V1"]])
check("…and a table whose facts cannot be read is NAMED, never guessed",
      "n_domain.missing" not in facts and any("n_domain.missing" in u for u in facts["unavailable"]))
sys.path.insert(0, ROOT)  # components.* (the explain parser) lives at the project root
_EXPL = {
    "low": ["  1) First, we do an all-AMPs RETRIEVE step from n.incident by way of an all-rows scan with a",
            "     condition of (\"n.incident.V1 = '2'\") into Spool 1, which is built locally on the AMPs.",
            "     The size of Spool 1 is estimated with low confidence to be 5 rows (80 bytes).",
            "     The estimated time for this step is 4.00 seconds.",
            "  -> The contents of Spool 1 are sent back to the user. The total estimated time is 4.00 seconds."],
    "high": ["  1) First, we do an all-AMPs RETRIEVE step from n.incident by way of an all-rows scan with a",
             "     condition of (\"n.incident.V1 = '2'\") into Spool 1, which is built locally on the AMPs.",
             "     The size of Spool 1 is estimated with high confidence to be 5 rows (80 bytes).",
             "     The estimated time for this step is 6.00 seconds.",
             "  -> The contents of Spool 1 are sent back to the user. The total estimated time is 6.00 seconds."]}
_which = {"v": "low"}


async def _fake_explain(creds, sql, max_rows=0, readonly=False):
    return ["Explanation"], [[l] for l in _EXPL[_which["v"]]]
_SR.run_rows = _fake_explain
try:
    _it = [{"sql": "SELECT COUNT(*) FROM n.incident WHERE V1 = '2'"}]
    asyncio.run(E.explain_workload({}, _it))
    _lo = dict(_it[0])
    _which["v"] = "high"
    _it = [{"sql": "SELECT COUNT(*) FROM n.incident WHERE V1 = '2'"}]
    asyncio.run(E.explain_workload({}, _it))
    _hi = dict(_it[0])
finally:
    _SR.run_rows = _real
check("a plan estimated with low confidence is a guess; with high confidence it is not",
      _lo.get("guessed") is True and _hi.get("guessed") is False, (_lo.get("guessed"), _hi.get("guessed")))
check("the same plan with a different cost has the SAME shape (a cost estimate is not a plan change)",
      _lo.get("shape") and _lo.get("shape") == _hi.get("shape"))
_sh = E.plan_shape({"steps": [{"inputs": ["n.ji_x"], "access": ["by way of the primary index"]}]})
check("…a different access path is a different shape", _sh != _lo.get("shape"))
check("…and each carries its estimated seconds", _lo.get("est_seconds") == 4.0 and _hi.get("est_seconds") == 6.0,
      (_lo.get("est_seconds"), _hi.get("est_seconds")))
check("every statement the facts read sent is a dictionary SELECT",
      sent and all(q.strip().upper().startswith("SELECT") and "DBC." in q for q in sent), sent)

# ── §L ──
print("[§L] the lifecycle (tuning_proposals)")
import tempfile  # noqa: E402
from trusted_data_agent.core import tuning_proposals as TP  # noqa: E402
_db = os.path.join(tempfile.mkdtemp(), "t.db")
_ji = {"id": "tp_ji", "rule": "covering_join_index", "purpose": "join_index", "table": "n.incident",
       "columns": ["ORI", "INCNUM", "V1"], "key": ["ORI", "INCNUM"], "evidence": "2 full scans",
       "statements": ["s1:1:0"], "landing": "direct", "measure_sql": ["SELECT COUNT(*) FROM n.incident WHERE V1 = '2'"],
       "ddl": ['CREATE JOIN INDEX "n"."ji_incident_cover" AS SELECT "ORI", "INCNUM", "V1" FROM "n"."incident" PRIMARY INDEX ("ORI", "INCNUM")',
               'COLLECT STATISTICS COLUMN ("ORI", "INCNUM") ON "n"."ji_incident_cover"']}
_st = {"id": "tp_st", "rule": "stats_on_workload_columns", "purpose": "statistics", "table": "n.incident",
       "columns": ["V1"], "landing": "direct", "ddl": ['COLLECT STATISTICS COLUMN ("V1") ON "n"."incident"']}
_pt = {"id": "tp_pt", "rule": "partition_on_date_filter", "purpose": "partition", "table": "n.incident",
       "columns": ["D"], "landing": "rebuild", "ddl": ['-- rebuild required: ...']}
rows = TP.record_review("u1", "kg1", {"proposals": [_ji, _st, _pt]}, connection_id="c1", db_path=_db)
check("a review records every proposal as pending", sorted(r["status"] for r in rows) == ["pending"] * 3)
rid = {r["proposal_id"]: r["id"] for r in rows}
ran = []


async def _exec(sql):
    ran.append(sql)


async def _measure(sqls):
    return ({"seconds": 12.5, "unconfident": 1, "shapes": ["a", "b"]} if not ran
            else {"seconds": 3.0, "unconfident": 0, "shapes": ["a", "c"]})


def _refused(fn, code):
    try:
        r = fn()
        if asyncio.iscoroutine(r):
            asyncio.run(r)
    except TP.TuningError as e:
        return e.code == code
    return False


check("NOTHING lands without approval — a pending proposal is refused and nothing runs",
      _refused(lambda: TP.land("u1", rid["tp_ji"], actor="u1", execute=_exec, db_path=_db), "not_approved") and ran == [])
check("an agent never approves", _refused(lambda: TP.decide("u1", rid["tp_ji"], "approve", actor="agent:p1", db_path=_db), "person_required"))
check("a rebuild proposal cannot be approved (the act it offers cannot be performed)",
      _refused(lambda: TP.decide("u1", rid["tp_pt"], "approve", actor="u1", db_path=_db), "rebuild_required"))
check("another user's proposal is not found", _refused(lambda: TP.decide("u2", rid["tp_ji"], "approve", actor="u2", db_path=_db), "not_found"))
_res_holder = {}


def _lifecycle_rest():
    TP.decide("u1", rid["tp_st"], "reject", actor="u1", note="not now", db_path=_db)
    ok_row = TP.decide("u1", rid["tp_ji"], "approve", actor="u1", db_path=_db)
    check("a person approves; the decision names them", ok_row["status"] == "approved" and ok_row["decided_by"] == "u1")
    check("a decided proposal is decided once", _refused(lambda: TP.decide("u1", rid["tp_ji"], "reject", actor="u1", db_path=_db), "not_pending"))
    res = _res_holder['res'] = asyncio.run(TP.land("u1", rid["tp_ji"], actor="u1", execute=_exec, measure=_measure, db_path=_db))
    rc = res["receipt"]
    check("approved → landed: every statement executed, in order, one per call", res["status"] == "landed" and ran == _ji["ddl"])
    check("…measured before and after (the workload's estimate), with the guesses each rested on",
          rc["measured_before"] == 12.5 and rc["measured_after"] == 3.0
          and rc["unconfident_before"] == 1 and rc["unconfident_after"] == 0)
    check("…and how many workload plans now run differently", rc["plans_changed"] == 1)
    check("…the guesses are inside the signed content", '"unconfident_before":1' in TP.receipt_content(rc))
    check("…the receipt names the approver and the lander, and signs a closed field set",
          rc["approved_by"] == "u1" and rc["by"] == "u1" and "attestation" in rc
          and json.loads(TP.receipt_content({**rc, "presentation": "x"})) == json.loads(TP.receipt_content(rc)))
    check("landing twice is refused", _refused(lambda: TP.land("u1", rid["tp_ji"], actor="u1", execute=_exec, db_path=_db), "not_approved"))
    rows2 = TP.record_review("u1", "kg1", {"proposals": [dict(_ji, evidence="3 full scans")]}, db_path=_db)
    by = {r["proposal_id"]: r for r in rows2}
    check("a new review leaves decided proposals untouched", by["tp_ji"]["status"] == "landed" and by["tp_st"]["status"] == "rejected"
          and by["tp_ji"]["proposal"]["evidence"] == "2 full scans")
    check("…and EXPIRES a pending one it no longer supports, evidence kept",
          by["tp_pt"]["status"] == "expired" and by["tp_pt"]["proposal"].get("columns") == ["D"] and "no longer" in (by["tp_pt"]["note"] or ""))
    TP.record_review("u1", "kg1", {"proposals": [_pt]}, db_path=_db)
    check("…an expired proposal the workload asks for again is pending again",
          {r["proposal_id"]: r["status"] for r in TP.list_proposals("u1", "kg1", db_path=_db)}["tp_pt"] == "pending")


    async def _boom(sql):
        raise RuntimeError("[Error 3807] Object does not exist")
    TP.record_review("u1", "kg2", {"proposals": [_st]}, db_path=_db)
    r2 = TP.list_proposals("u1", "kg2", db_path=_db)[0]
    TP.decide("u1", r2["id"], "approve", actor="u1", db_path=_db)
    f = asyncio.run(TP.land("u1", r2["id"], actor="u1", execute=_boom, db_path=_db))
    check("a database refusal marks the proposal failed, named", f["status"] == "failed" and "3807" in (f["note"] or ""))
    again = TP.decide("u1", r2["id"], "approve", actor="u1", db_path=_db)
    check("…a failed landing is retried only after a person approves again", again["status"] == "approved"
          and _refused(lambda: TP.decide("u1", rid["tp_ji"], "approve", actor="u1", db_path=_db), "not_pending"))
    check("a landed join index is named as the object it created", bool(res) and TP.artifact_name(res) == "n.ji_incident_cover")


try:
    _lifecycle_rest()
except Exception as _e:  # a mutation that corrupts state must FAIL by name, not crash
    check(f'the lifecycle ran to the end (aborted: {type(_e).__name__}: {_e})', False)
res = _res_holder.get('res') or {}
from trusted_data_agent.core.trust.composition import kg_content_hash  # noqa: E402
_base = [{"name": "n.incident", "entity_type": "table", "properties": {"layer": "physical"}}]
_art = _base + [{"name": "n.ji_incident_cover", "entity_type": "ddl_artifact", "properties": {"purpose": "join_index"}}]
_rel = [{"relationship_type": "derives_from", "source_name": "n.ji_incident_cover", "source_type": "ddl_artifact",
         "target_name": "n.incident", "target_type": "table"}]
check("a landed tuning artifact does not move the ontology's seal", kg_content_hash(_base, []) == kg_content_hash(_art, _rel))
_view = _base + [{"name": "n.v", "entity_type": "ddl_artifact", "properties": {"purpose": "metric_view"}}]
check("…while any other ddl_artifact still does", kg_content_hash(_base, []) != kg_content_hash(_view, []))

# ── §R ──
print("[§R] the REST faces (wiring, on the source)")
rsrc = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
seg = rsrc[rsrc.index("def _tuning_gate("):rsrc.index('@rest_api_bp.route("/v1/knowledge-graph/authored/deploy"')]
check("every tuning route is gated by the OBDA-MODEL plane (the component's availability) + the KG tier", seg.count("gate = _tuning_gate(current_user)") == 3
      and "_assert_model_enabled()" in seg)
check("every route resolves the graph's ownership", seg.count("_kg_owner_profile_for_id(user_uuid") == 3)
check("a decision names the person from the request, never the body", 'actor = getattr(current_user, "username"' in seg
      and 'data.get("actor")' not in seg)
check("a landing only goes through the lifecycle's land() (the approval rule lives there)",
      "await _tp.land(" in seg and "execute_ddl" not in seg and seg.count("_execute_sql") == 1)
check("the review records proposals, never applies them", "record_review(" in seg and "land(" not in seg.split("async def kg_tuning_decide")[0])

# ── §P ──
print("[§P] the recommender writes nothing")
src = open(os.path.join(ROOT, "src/trusted_data_agent/dbdeploy/tuning.py")).read()
tree = ast.parse(src)
mods = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
       {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
check("no backend, connection, database driver or session import",
      not any(any(x in m for x in ("backend", "connection", "teradatasql", "sqlite", "session_manager", "factory"))
              for m in mods), mods)
import re as _re  # noqa: E402
check("no '(s)' placeholder in any rendered evidence text",
      not _re.search(r'f"[^"\n]*\(s\)', open(os.path.join(ROOT, "src/trusted_data_agent/dbdeploy/tuning.py")).read()))
check("no execute call anywhere in the module", ".execute(" not in src and "execute_ddl" not in src)
esrc = open(os.path.join(ROOT, "src/trusted_data_agent/core/tuning_evidence.py")).read()
check("the evidence sends only dictionary reads and EXPLAIN of a guarded SELECT",
      '"EXPLAIN " + body' in esrc and "single_select_guard(sql)" in esrc
      and not any(w in esrc for w in ("execute_ddl", "COLLECT STATISTICS", "CREATE JOIN", "INSERT ", "UPDATE ", "DELETE ")))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
