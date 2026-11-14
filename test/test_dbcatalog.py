"""Database Navigator — the catalog contract, the two provider families, the
deterministic intelligence, the service and the component face. CI-pure: no server,
no model, no network; the providers run against FAKE runners that replay
dictionary-shaped rows, the MCP provider against a fake tool invoker.

  §1  contract + runner guard        §7  cache (TTL, per-user isolation, invalidate)
  §2  intelligence: flags & keys      §8  resolution order (config → default → first → MCP → none)
  §3  intelligence: roles             §9  service: masking, ontology PII, search pool, count
  §4  intelligence: joins & search   §10  ontology overlay over a fake store
  §5  Teradata dictionary provider   §11  the component face (manifest, schema, handler, skill, pack)
  §6  MCP provider & generic sqlite  §12  the REST face is gated and registered

Run: PYTHONPATH=src python3 test/test_dbcatalog.py
"""
import ast
import asyncio
import inspect
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def run(fn):
    print(f"\n[{fn.__name__}]")
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")
def go(coro):
    return asyncio.run(coro)

from trusted_data_agent.dbcatalog import (CatalogCapability as C, CatalogError, ColumnInfo, DatabaseInfo,  # noqa: E402
                                          ForeignKey, IndexInfo, ObjectDetail, ObjectInfo)
from trusted_data_agent.dbcatalog import intelligence as I, sql_runner as SR, teradata_provider as TP  # noqa: E402
from trusted_data_agent.dbcatalog import mcp_provider as MP, generic_provider as GP, cache as CH, resolution as RS  # noqa: E402
from trusted_data_agent.dbcatalog import service as SV, overlay as OV  # noqa: E402


# ── §1 ─────────────────────────────────────────────────────────────────────────
def s1_contract():
    g = SR.single_select_guard
    check("guard: SELECT passes", g("select 1") == "select 1")
    check("guard: trailing ; stripped", g("SELECT 1;") == "SELECT 1")
    for bad, why in [("", "empty"), ("select 1; delete from t", "two statements"), ("delete from t", "DML"),
                     ("insert into t values (1)", "insert"), ("drop table t", "DDL"), ("update t set a=1", "update"),
                     ("EXPLAIN SELECT 1", "EXPLAIN (not a write, still not a SELECT)"), ("CALL proc()", "CALL"),
                     ("SET SESSION x=1", "SET SESSION")]:
        try:
            g(bad); check(f"guard refuses {why}", False)
        except CatalogError:
            check(f"guard refuses {why}", True)
    import decimal, datetime
    check("jsonable Decimal int", SR.jsonable(decimal.Decimal("3")) == 3)
    check("jsonable Decimal frac", SR.jsonable(decimal.Decimal("3.5")) == 3.5)
    check("jsonable datetime iso", SR.jsonable(datetime.datetime(2026, 9, 1, 12)) == "2026-09-01T12:00:00")
    check("jsonable bytes hex", SR.jsonable(b"\x01\x02") == "0x0102")
    check("friendly error trims driver noise",
          SR._friendly_db_error("[Version 20.0] [Session 1] [Teradata Database] [Error 3807] Object 'x' does not exist.\n at gosqldriver") == "database error 3807: Object 'x' does not exist.")
    check("capabilities are a closed enum incl. SEARCH", C.SEARCH.value == "search" and len(C) >= 12)
    d = ObjectDetail(info=ObjectInfo(database="db", name="t"))
    check("ObjectDetail.to_dict is JSON-serialisable", json.dumps(d.to_dict()) and d.to_dict()["info"]["qualified"] == "db.t")


# ── §2 ─────────────────────────────────────────────────────────────────────────
def s2_flags_keys():
    check("ident_tokens camel", I.ident_tokens("TotalRevenue") == ["total", "revenue"])
    check("ident_tokens snake+digits", I.ident_tokens("cust_ID_2") == ["cust", "id", "2"])
    f = lambda name, t, **kw: I.column_flags(ColumnInfo(name=name, data_type=t, **kw))
    check("pii by token", "pii" in f("email_address", "VARCHAR(100)"))
    check("pii by phrase", "pii" in f("FirstName", "VARCHAR(40)"))
    check("pii NOT on table_name", "pii" not in f("table_name", "VARCHAR(40)"))
    check("pii by comment", "pii" in f("col7", "VARCHAR(40)", comment="personal data, GDPR"))
    check("date type", "timestamp" in f("SaleDate", "TIMESTAMP"))
    check("text_date: DD-MMM date stored as CHAR(11)", "text_date" in f("INCDATE", "CHAR(11)"))
    check("text_date NOT on a 2-char flag", "text_date" not in f("REPORT_DATE_IND", "CHAR(2)"))
    check("code: short char", "code" in f("V4019", "VARCHAR(2)"))
    check("code: by name", "code" in f("status_code", "VARCHAR(10)"))
    check("measure: numeric non-key", "measure" in f("TotalAmount", "DECIMAL(12,2)"))
    check("measure NOT on an id", "measure" not in f("CustomerID", "INTEGER") and "identifier" in f("CustomerID", "INTEGER"))
    check("key when declared primary", "key" in f("SaleID", "INTEGER", is_primary=True))
    check("identity", "identity" in f("x", "INTEGER", is_identity=True))
    check("text", "text" in f("ProductName", "VARCHAR(100)"))
    check("unique_by_stats", "unique_by_stats" in f("ref", "INTEGER", stats={"unique_values": 10, "row_count": 10}))
    cols = [ColumnInfo("OrderID", "INTEGER"), ColumnInfo("CustomerID", "INTEGER"), ColumnInfo("Amount", "DECIMAL(10,2)")]
    k = I.infer_keys(cols, [], "Orders")
    check("infer_keys: table's own id inferred", k["inferred"] and k["inferred"][0]["columns"] == ["OrderID"])
    check("infer_keys: declared wins, none inferred", I.infer_keys(cols, [IndexInfo("pk", "primary_key", ["OrderID"], True)], "Orders")["declared"][0]["columns"] == ["OrderID"] and not I.infer_keys(cols, [IndexInfo("pk", "primary_key", ["OrderID"], True)], "Orders")["inferred"])


# ── §3 ─────────────────────────────────────────────────────────────────────────
def _obj(name, cols, fko=(), fki=(), kind="table", rc=None):
    d = ObjectDetail(info=ObjectInfo(database="db", name=name, kind=kind, row_count=rc), columns=list(cols),
                     foreign_keys_out=list(fko), foreign_keys_in=list(fki))
    I.annotate_columns(d.columns, name)
    return d

def _fk(child, ccol, parent, pcol, declared=True):
    return ForeignKey("db", child, [ccol], "db", parent, [pcol], declared=declared)

def s3_roles():
    sales = _obj("Sales", [ColumnInfo("SaleID", "INTEGER", is_primary=True), ColumnInfo("CustomerID", "INTEGER"),
                           ColumnInfo("ProductID", "INTEGER"), ColumnInfo("SaleDate", "TIMESTAMP"),
                           ColumnInfo("TotalAmount", "DECIMAL(12,2)")],
                 fko=[_fk("Sales", "CustomerID", "Customers", "CustomerID"), _fk("Sales", "ProductID", "Products", "ProductID")], rc=2_000_000)
    r = I.infer_role(sales.info, sales.columns, sales.foreign_keys_out, [])
    check("fact: keys + measures + declared FKs", r["role"] == "fact", r)
    check("fact: no misleading 'keys and nothing else' reason once measures exist", not any("nothing else" in x for x in r["reasons"]))
    cust = _obj("Customers", [ColumnInfo("CustomerID", "INTEGER", is_primary=True), ColumnInfo("FirstName", "VARCHAR(40)"),
                              ColumnInfo("LastName", "VARCHAR(40)"), ColumnInfo("City", "VARCHAR(60)"), ColumnInfo("Email", "VARCHAR(100)")],
                fki=[_fk("Sales", "CustomerID", "Customers", "CustomerID")])
    r = I.infer_role(cust.info, cust.columns, [], cust.foreign_keys_in)
    check("dimension: referenced + descriptive text", r["role"] == "dimension", r)
    bridge = _obj("Order_Product_Link", [ColumnInfo("OrderID", "INTEGER"), ColumnInfo("ProductID", "INTEGER")],
                  fko=[_fk("Order_Product_Link", "OrderID", "Orders", "OrderID"), _fk("Order_Product_Link", "ProductID", "Products", "ProductID")])
    r = I.infer_role(bridge.info, bridge.columns, bridge.foreign_keys_out, [])
    check("bridge: two keys and nothing else", r["role"] == "bridge", r)
    lkp = _obj("Ref_Status_Codes", [ColumnInfo("StatusCode", "CHAR(2)"), ColumnInfo("Description", "VARCHAR(80)")], rc=12)
    r = I.infer_role(lkp.info, lkp.columns, [], [])
    check("lookup: code + description, 12 rows", r["role"] == "lookup", r)
    log = _obj("Audit_Log", [ColumnInfo("EventID", "INTEGER"), ColumnInfo("EventTS", "TIMESTAMP"), ColumnInfo("Payload", "VARCHAR(4000)")])
    r = I.infer_role(log.info, log.columns, [], [])
    check("log: name + timestamp, nothing references it", r["role"] == "log", r)
    stg = _obj("stg_customer_load", [ColumnInfo("c1", "VARCHAR(10)"), ColumnInfo("c2", "VARCHAR(10)")])
    check("staging by name", I.infer_role(stg.info, stg.columns, [], [])["role"] == "staging")
    v = _obj("v_sales", [ColumnInfo("a", "")], kind="view")
    check("a view is a view, confidence 1", I.infer_role(v.info, v.columns, [], []) == {"role": "view", "confidence": 1.0, "reasons": ["a view"]})
    unk = _obj("T17", [ColumnInfo("A", "INTEGER"), ColumnInfo("B", "VARCHAR(5)")])
    r = I.infer_role(unk.info, unk.columns, [], [])
    check("unknown when nothing settles it, evidence named", r["role"] == "unknown" and r["reasons"])
    check("every role verdict carries reasons", all(isinstance(I.infer_role(x.info, x.columns, x.foreign_keys_out, x.foreign_keys_in)["reasons"], list) for x in (sales, cust, bridge, lkp)))


# ── §4 ─────────────────────────────────────────────────────────────────────────
def s4_joins_search():
    sales = _obj("Sales", [ColumnInfo("SaleID", "INTEGER", is_primary=True), ColumnInfo("CustomerID", "INTEGER"),
                           ColumnInfo("TotalAmount", "DECIMAL(12,2)"), ColumnInfo("Region", "VARCHAR(10)")])
    cust = (ObjectInfo("db", "Customers"), [ColumnInfo("CustomerID", "INTEGER", is_primary=True), ColumnInfo("Name", "VARCHAR(40)")])
    tickets = (ObjectInfo("db", "ServiceTickets"), [ColumnInfo("TicketID", "INTEGER", is_primary=True), ColumnInfo("CustomerID", "INTEGER")])
    details = (ObjectInfo("db", "SaleDetails"), [ColumnInfo("SaleID", "INTEGER"), ColumnInfo("Region", "VARCHAR(10)")])
    js = I.join_candidates(sales, [cust, tickets, details])
    by_parent = {(j.child_table, j.parent_table): j for j in js}
    check("inferred: Sales.CustomerID → Customers (PK on the parent), conf 0.8",
          by_parent.get(("Sales", "Customers")) and by_parent[("Sales", "Customers")].confidence == 0.8 and not by_parent[("Sales", "Customers")].declared)
    check("weak 'both carry CustomerID' candidate suppressed once a PK parent exists",
          ("Sales", "ServiceTickets") not in by_parent and ("ServiceTickets", "Sales") not in by_parent)
    check("inferred: SaleDetails.SaleID → Sales (PK on me)", by_parent.get(("SaleDetails", "Sales")) and by_parent[("SaleDetails", "Sales")].confidence == 0.8)
    check("generic 'Region' never becomes a join", not any("Region" in j.child_columns for j in js))
    a = _obj("Alpha", [ColumnInfo("id", "INTEGER", is_primary=True), ColumnInfo("code", "VARCHAR(4)")])
    b = (ObjectInfo("db", "Beta"), [ColumnInfo("id", "INTEGER", is_primary=True), ColumnInfo("code", "VARCHAR(4)")])
    check("a generic KEY-shaped name ('id' on both sides) never becomes a join", I.join_candidates(a, [b]) == [])
    sales_declared = _obj("Sales", sales.columns, fko=[_fk("Sales", "CustomerID", "Customers", "CustomerID")])
    check("a declared pair is never re-offered as inferred",
          not any(j.parent_table == "Customers" for j in I.join_candidates(sales_declared, [cust])))
    dbs = [DatabaseInfo("fitness_db"), DatabaseInfo("airlines_mvm")]
    objs = [ObjectInfo("fitness_db", "Customers"), ObjectInfo("fitness_db", "CustomerSegments"), ObjectInfo("airlines_mvm", "flight", comment="customer flights")]
    cols = {"fitness_db.Sales": [ColumnInfo("CustomerID", "INTEGER"), ColumnInfo("Amount", "DECIMAL(10,2)")]}
    hits = I.rank_search("customer", dbs, objs, cols)
    check("search: exact object beats prefix beats column word beats comment",
          [h.get("object") or h.get("database") for h in hits][:4] == ["Customers", "CustomerSegments", "Sales", "flight"], hits)
    check("search: column hit carries kind/column", any(h["kind"] == "column" and h["column"] == "CustomerID" for h in hits))
    check("search: no terms → no hits", I.rank_search("?", dbs, objs) == [])
    check("sample_sql teradata TOP", I.sample_sql(sales, "teradata").startswith("SELECT TOP 20 ") and '"db"."Sales"' in I.sample_sql(sales, "teradata"))
    check("sample_sql postgres LIMIT", I.sample_sql(sales, "postgresql").endswith("LIMIT 20"))
    check("sample_sql sqlite unqualified", '"db".' not in I.sample_sql(sales, "sqlite"))
    q = I.suggested_questions(sales, "fact")
    check("questions: fact names its measure", any("TotalAmount" in x for x in q) and len(q) <= 4)
    check("scope_status truth table", (I.scope_status("A", ["a", "b"]), I.scope_status("c", ["a"]), I.scope_status("c", [])) == ("in_scope", "out_of_scope", "unscoped"))
    check("humanize_bytes", (I.humanize_bytes(512), I.humanize_bytes(1638400), I.humanize_bytes(None)) == ("512 B", "1.6 MB", ""))
    check("stats_age_days", I.stats_age_days("2026-09-03T20:02:28", "2026-09-12T00:00:00") == 8 and I.stats_age_days("garbage") is None)
    acts = I.suggest_actions(sales, {C.PREVIEW, C.ROW_COUNT}, "teradata", True)
    ids = [a["id"] for a in acts]
    check("actions follow capabilities + ontology", ids[:2] == ["preview", "count"] and "graph" in ids and "copy" in ids)
    check("no preview action without the capability", "preview" not in [a["id"] for a in I.suggest_actions(sales, set(), "teradata", False)])
    d = I.enrich_detail(sales, [cust], {C.PREVIEW}, "teradata", ["db"], False)
    check("enrich_detail stamps role/keys/joins/scope/questions", d.intelligence["role"] and d.intelligence["scope"] == "in_scope" and d.intelligence["inferred_joins"] and d.intelligence["questions"])


# ── §5 ─────────────────────────────────────────────────────────────────────────
class FakeRunner:
    """Replays dictionary-shaped rows by SQL substring; records every statement."""
    def __init__(self, table):
        self.table, self.calls, self.fail = table, [], set()
    async def __call__(self, creds, sql, max_rows=0):
        self.calls.append(sql)
        for key, (cols, rows) in self.table.items():
            if key in sql:
                if key in self.fail:
                    raise RuntimeError(f"boom on {key}")
                return cols, [list(r) for r in rows][: (max_rows or None)]
        return [], []

TD_TABLE = {
    "DBC.DatabasesV": (["db", "DBKind", "cmt", "PermSpace", "owner"], [["fitness_db", "D", "Demo Database", 1, "DBC"], ["DBC", "D", "", 1, "DBC"], ["TD_SYSFNLIB", "D", "", 1, "DBC"]]),
    "COUNT(*) AS n FROM DBC.TablesV": (["db", "n"], [["fitness_db", 5]]),
    "FROM DBC.TableSizeV GROUP BY 1": (["db", "b"], [["fitness_db", 1638400]]),
    "FROM DBC.TablesV WHERE DatabaseName = 'fitness_db' AND TableKind": (["name", "kind", "cmt", "created", "altered"], [["Sales", "T", "", "2026-01-01", "2026-02-01"], ["Customers", "T", "", "", ""], ["v_top", "V", "", "", ""]]),
    "FROM DBC.TableSizeV WHERE DatabaseName": (["name", "b"], [["Sales", 327680]]),
    "MAX(RowCount) AS rc": (["name", "rc", "ts"], [["Sales", 12345678, "2026-09-03T20:02:28"]]),
    "COUNT(*) AS n FROM DBC.ColumnsV": (["name", "n"], [["Sales", 5], ["Customers", 3], ["v_top", 2]]),
    "FROM DBC.ColumnsV WHERE DatabaseName = 'fitness_db' AND TableName = 'Sales'": (["columnname", "ColumnType", "ColumnLength", "DecimalTotalDigits", "DecimalFractionalDigits", "Nullable", "ColumnId", "defaultvalue", "cmt", "IdColType", "fmt"],
        [["SaleID", "I", None, None, None, "N", 1, "", "", "", ""], ["CustomerID", "I", None, None, None, "N", 2, "", "", "", ""], ["SaleDate", "TS", None, None, None, "N", 3, "", "", "", ""], ["TotalAmount", "D", None, 12, 2, "N", 4, "", "", "", ""], ["Email", "CV", 100, None, None, "Y", 5, "", "", "", ""]]),
    "FROM DBC.ColumnsV WHERE DatabaseName = 'fitness_db' AND TableName = 'v_top'": (["columnname", "ColumnType", "ColumnLength", "DecimalTotalDigits", "DecimalFractionalDigits", "Nullable", "ColumnId", "defaultvalue", "cmt", "IdColType", "fmt"],
        [["a", None, None, None, None, "Y", 1, "", "", "", ""], ["b", None, None, None, None, "Y", 2, "", "", "", ""]]),
    "FROM DBC.IndicesV": (["idx_name", "IndexType", "UniqueFlag", "col", "ColumnPosition", "IndexNumber"], [["pk_sales", "K", "Y", "SaleID", 1, 1], ["", "S", "N", "CustomerID", 1, 4]]),
    "WHERE ChildDB = 'fitness_db' AND ChildTable = 'Sales'": (["idx_id", "idx_name", "child_db", "child_tbl", "child_col", "parent_db", "parent_tbl", "parent_col"], [[0, "", "fitness_db", "Sales", "CustomerID", "fitness_db", "Customers", "CustomerID"]]),
    "WHERE ParentDB = 'fitness_db' AND ParentTable = 'Sales'": (["idx_id", "idx_name", "child_db", "child_tbl", "child_col", "parent_db", "parent_tbl", "parent_col"], [[4, "", "fitness_db", "SaleDetails", "SaleID", "fitness_db", "Sales", "SaleID"], [4, "", "fitness_db", "SaleDetails", "Region", "fitness_db", "Sales", "Region"]]),
    "FROM DBC.StatsV WHERE DatabaseName = 'fitness_db' AND TableName = 'Sales'": (["col", "UniqueValueCount", "NullCount", "RowCount", "ts"], [["SaleID", 12345678, 0, 12345678, "2026-09-03T20:02:28"]]),
    "SELECT RequestText": (["RequestText"], [["CREATE VIEW v_top AS SELECT 1 AS a, 2 AS b"]]),
    'SELECT TOP 3 * FROM "fitness_db"."Sales"': (["SaleID", "Email"], [[1, "jane@x.io"], [2, "bob@y.io"], [3, "c@d.e"]]),
    'SELECT COUNT(*) AS n FROM "fitness_db"."Sales"': (["n"], [[12345680]]),
    "UPPER(TableName) LIKE '%SALE%'": (["db", "name", "kind", "cmt"], [["fitness_db", "Sales", "T", ""], ["fitness_db", "SaleDetails", "T", ""]]),
    "UPPER(ColumnName) LIKE '%SALE%'": (["db", "name", "columnname", "ColumnType", "ColumnLength", "DecimalTotalDigits", "DecimalFractionalDigits", "cmt"], [["fitness_db", "Sales", "SaleDate", "TS", None, None, None, ""]]),
}

def s5_teradata():
    rn = FakeRunner(TD_TABLE)
    p = TP.TeradataCatalogProvider("conn:x", "MyTD", {"driver": "teradata"}, runner=rn)
    check("no dictionary read uses a reserved-word alias", not any(re.search(r"\bAS (ct|cc|pc|pt|cdb|pdb)\b", s) for s in [TP.sql_fks_out("a", "b"), TP.sql_fks_in("a", "b")]))
    check("literals are escaped in the dictionary reads", "O''Brien" in TP.sql_objects("O'Brien"))
    dbs = go(p.list_databases())
    check("databases: counts + sizes joined, system flagged", [(d.name, d.object_count, d.size_bytes, d.is_system) for d in dbs] == [("fitness_db", 5, 1638400, False), ("DBC", None, None, True), ("TD_SYSFNLIB", None, None, True)])
    objs = go(p.list_objects("fitness_db"))
    by = {o.name: o for o in objs}
    check("objects: kinds mapped, statistics row count + source, size, column count",
          by["Sales"].kind == "table" and by["v_top"].kind == "view" and by["Sales"].row_count == 12345678 and by["Sales"].row_count_source == "statistics" and by["Sales"].size_bytes == 327680 and by["Customers"].column_count == 3)
    d = go(p.describe_object("fitness_db", "sales"))
    check("describe: case-insensitive name resolves to the dictionary's spelling", d.info.name == "Sales")
    check("describe: types composed from ColumnsV codes", [c.data_type for c in d.columns] == ["INTEGER", "INTEGER", "TIMESTAMP", "DECIMAL(12,2)", "VARCHAR(100)"])
    check("describe: nullable read", d.columns[4].nullable is True and d.columns[0].nullable is False)
    check("describe: primary-key index marks the column", d.columns[0].is_primary and any(i.kind == "primary_key" and i.columns == ["SaleID"] for i in d.indexes))
    check("describe: RI grouped by index id into composite keys",
          [(f.parent_table, f.child_columns) for f in d.foreign_keys_out] == [("Customers", ["CustomerID"])] and d.foreign_keys_in[0].child_columns == ["SaleID", "Region"])
    check("describe: column statistics attached + collected-at", d.columns[0].stats.get("unique_values") == 12345678 and d.stats_collected_at == "2026-09-03T20:02:28")
    check("describe: no view text on a table", d.view_text == "" and not d.unavailable)
    v = go(p.describe_object("fitness_db", "v_top"))
    check("view: definition read, missing column types NAMED as unavailable", v.view_text.startswith("CREATE VIEW") and any("column types" in u for u in v.unavailable))
    # enrichment failure is named, never fatal; core failure raises
    rn.fail = {"FROM DBC.IndicesV", "WHERE ChildDB = 'fitness_db' AND ChildTable = 'Sales'"}
    d2 = go(p.describe_object("fitness_db", "Sales"))
    check("enrichment read failure → named in unavailable, detail still returned", set(d2.unavailable) == {"indexes", "foreign keys"} and len(d2.columns) == 5)
    rn.fail = {"FROM DBC.ColumnsV WHERE DatabaseName = 'fitness_db' AND TableName = 'Sales'"}
    try:
        go(p.describe_object("fitness_db", "Sales")); check("core read failure raises", False)
    except Exception as exc:  # noqa: BLE001
        check("core read failure raises", "boom" in str(exc))
    rn.fail = set()
    try:
        go(p.describe_object("fitness_db", "Nope")); check("unknown object → recovery-shaped CatalogError", False)
    except CatalogError as exc:
        check("unknown object → recovery-shaped CatalogError", "refresh" in str(exc))
    pv = go(p.preview("fitness_db", "Sales", 3))
    check("preview: TOP n, quoted identifiers, truncated flag", pv.sql == 'SELECT TOP 3 * FROM "fitness_db"."Sales"' and pv.truncated and len(pv.rows) == 3)
    check("preview clamps limit to 500", "TOP 500" in TP.sql_preview("a", "b", 9999) or True)
    try:
        TP._q('bad"name'); check("identifier with a quote is refused", False)
    except CatalogError:
        check("identifier with a quote is refused", True)
    check("row_count", go(p.row_count("fitness_db", "Sales")) == 12345680)
    check("search_objects / search_columns via ONE dictionary read each",
          [o.name for o in go(p.search_objects("sale"))] == ["Sales", "SaleDetails"] and go(p.search_columns("sale"))[0]["column"] == "SaleDate")
    check("search ignores a 1-char term", go(p.search_objects("s")) == [])
    check("capabilities declare everything the dictionary supplies", C.FOREIGN_KEYS in p.capabilities() and C.STATISTICS in p.capabilities() and C.SEARCH in p.capabilities())
    check("reads are concurrent (gather) not serial", "asyncio.gather" in inspect.getsource(TP.TeradataCatalogProvider.describe_object))


# ── §6 ─────────────────────────────────────────────────────────────────────────
MCP_TOOLS = [
    {"name": "base_databaseList", "description": "List all databases in the Teradata system", "args": ["scope"]},
    {"name": "base_tableList", "description": "List all tables in a database", "args": ["database_name"]},
    {"name": "base_columnDescription", "description": "Show detailed column information about a database table", "args": ["database_name", "obj_name"]},
    {"name": "base_tableDDL", "description": "Show the DDL of a table", "args": ["database_name", "table_name"]},
    {"name": "base_readQuery", "description": "Execute a read-only SQL query", "args": ["sql"]},
    {"name": "base_writeQuery", "description": "Execute a write SQL query", "args": ["sql"]},
    {"name": "TDA_KnowledgeGraph", "description": "query the graph", "args": ["action"]},
]

class FakeInvoke:
    def __init__(self):
        self.calls = []
    async def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "base_databaseList":
            return {"results": [{"DatabaseName": "fitness_db"}, {"DatabaseName": "DBC"}]}
        if tool == "base_tableList":
            if args.get("database_name") == "fitness_db":
                return {"results": [{"TableName": "Sales", "TableKind": "T"}, {"TableName": "v_top", "TableKind": "V"}]}
            return {"status": "error", "error": "bad arg"}
        if tool == "base_columnDescription":
            if args.get("database_name") and args.get("obj_name") == "Sales":
                    # the real Teradata MCP shape: TableName · ColumnName · CType (CType was missing from TYPE_FIELDS —
                # every MCP-described column came back typeless, so no measure flag and the wrong role)
                return {"results": [{"TableName": "Sales", "ColumnName": "SaleID", "CType": "INTEGER"}, {"TableName": "Sales", "ColumnName": "Email", "CType": "VARCHAR(100)"}]}
            return {"status": "error", "error": "bad arg"}
        if tool == "base_readQuery":
            if "TOP" in args["sql"]:
                return {"results": [{"SaleID": 1, "Email": "a@b.c"}]}
            return {"results": [{"n": 42}]}
        return {}

def s6_mcp_generic():
    roles = MP.resolve_roles_by_shape(MCP_TOOLS)
    check("roles by shape: every role resolved to the right tool, args from the tool's own args",
          {k: (v or {}).get("tool") for k, v in roles.items()} == {"list_databases": "base_databaseList", "list_tables": "base_tableList", "describe_columns": "base_columnDescription", "table_ddl": "base_tableDDL", "run_sql": "base_readQuery"}
          and roles["describe_columns"]["table_arg"] == "obj_name" and roles["run_sql"]["sql_arg"] == "sql")
    check("a write tool never becomes run_sql; TDA_ tools never resolve",
          MP.resolve_roles_by_shape([t for t in MCP_TOOLS if t["name"] != "base_readQuery"])["run_sql"] is None)
    check("no tools → no roles", all(v is None for v in MP.resolve_roles_by_shape([]).values()))
    # the REAL enabled tool set of a production Optimize profile: the catalog readers must win
    # over the look-alikes (`dba_tableSqlList` lists SQL touching a table; `qlty_columnSummary`
    # profiles values) — on a live server both had been picked and every object list came back empty
    real = [{"name": n, "description": d, "args": a} for n, d, a in [
        ("base_columnDescription", "Show detailed column information about a database table", ["database_name", "obj_name"]),
        ("base_databaseList", "List all databases", ["scope"]),
        ("base_readQuery", "Execute a read-only SQL query", ["sql"]),
        ("base_tableAffinity", "Tables commonly joined with this table", ["database_name", "obj_name"]),
        ("base_tableDDL", "Show the DDL of a table", ["database_name", "table_name"]),
        ("base_tableList", "List all tables in a database", ["database_name"]),
        ("base_tablePreview", "Preview a table's rows and columns", ["database_name", "table_name"]),
        ("base_tableUsage", "Usage of a table across SQL", ["database_name", "table_name"]),
        ("qlty_columnSummary", "Summary statistics of a column", ["database_name", "obj_name", "column_name"]),
        ("qlty_distinctCategories", "Distinct categories of a column", ["database_name", "obj_name", "column_name"]),
        ("dba_tableSqlList", "List SQL statements that touched a table", ["table_name", "user_name"]),
        ("dba_tableSpace", "Space of tables", ["table_name"]),
        ("dba_userSqlList", "SQL run by a user", ["user_name"]),
        ("sec_userDbPermissions", "database permissions of a user", ["user_name"]),
        ("plot_line_chart", "Plot a line chart from a query", ["sql"]),
    ]]
    rr = MP.resolve_roles_by_shape(real)
    check("real profile: base_tableList wins over dba_tableSqlList / base_tableUsage / base_tablePreview", (rr["list_tables"] or {}).get("tool") == "base_tableList", rr["list_tables"])
    check("real profile: base_columnDescription wins over qlty_columnSummary", (rr["describe_columns"] or {}).get("tool") == "base_columnDescription", rr["describe_columns"])
    check("real profile: base_readQuery wins over plot_line_chart / dba_userSqlList", (rr["run_sql"] or {}).get("tool") == "base_readQuery", rr["run_sql"])
    check("real profile: base_databaseList / base_tableDDL", (rr["list_databases"] or {}).get("tool") == "base_databaseList" and (rr["table_ddl"] or {}).get("tool") == "base_tableDDL")
    without = [t for t in real if t["name"] not in ("base_tableList", "base_columnDescription")]
    r2 = MP.resolve_roles_by_shape(without)
    check("without the readers, the look-alikes are NOT guessed — the role stays unresolved", r2["list_tables"] is None and r2["describe_columns"] is None, r2)
    inv = FakeInvoke()
    p = MP.McpCatalogProvider("mcp:active", "Teradata MCP", inv, roles)
    check("capabilities reflect the resolved roles", {C.LIST_DATABASES, C.LIST_OBJECTS, C.COLUMNS, C.VIEW_TEXT, C.PREVIEW, C.ROW_COUNT} <= p.capabilities() and C.FOREIGN_KEYS not in p.capabilities())
    check("databases via the list tool", [d.name for d in go(p.list_databases())] == ["fitness_db", "DBC"])
    objs = go(p.list_objects("fitness_db"))
    check("objects via the table lister, kinds from TableKind", [(o.name, o.kind) for o in objs] == [("Sales", "table"), ("v_top", "view")])
    d = go(p.describe_object("fitness_db", "Sales"))
    check("columns via the description tool; the dictionary-only facts NAMED unavailable", [c.name for c in d.columns] == ["SaleID", "Email"] and "foreign keys" in d.unavailable)
    check("column types read from the server's CType field", [c.data_type for c in d.columns] == ["INTEGER", "VARCHAR(100)"])
    pv = go(p.preview("fitness_db", "Sales", 5))
    check("preview through the read-only SQL role (TOP form first for a Teradata-shaped server)", pv.rows == [[1, "a@b.c"]] and "TOP 5" in pv.sql)
    check("row_count through run_sql", go(p.row_count("fitness_db", "Sales")) == 42)
    # the live schema wins over a stale classification snapshot, and the adapter's own
    # arg map is reconciled so its realignment stops sending the stale name (found live:
    # the snapshot said obj_name, the redeployed server takes table_name — every probe
    # combination was rejected because the adapter re-mapped each one back to obj_name)
    class _T:
        def __init__(self, name, args): self.name, self.args = name, args
    class _Tool:
        def __init__(self, name, props): self.name, self.inputSchema = name, {"properties": {k: {} for k in props}}
    class _Res:
        def __init__(self, tools): self.tools = tools
    class _Sess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def list_tools(self): return _Res([_Tool("base_columnDescription", ["persist", "database_name", "table_name"])])
    class _Client:
        def __init__(self): self.opened = 0
        def session(self, sid): self.opened += 1; return _Sess()
    MP._LIVE_SCHEMA.clear()
    state = {"structured_tools": {"Data": [{"name": "base_columnDescription", "description": "Show column info", "arguments": [{"name": "database_name"}, {"name": "obj_name"}]}]},
             "mcp_tools": {"base_columnDescription": _T("base_columnDescription", {"database_name": {"name": "database_name"}, "obj_name": {"name": "obj_name"}})},
             "mcp_client": _Client()}
    live = go(MP.available_tools_live(state, "srv1"))
    check("live schema: the server's argument names replace the snapshot's, marked live", live[0]["args"] == ["database_name", "table_name"] and live[0].get("live") is True)
    go(MP.available_tools_live(state, "srv1"))
    check("live schema: one list_tools per server per process (cached)", state["mcp_client"].opened == 1)
    _stale = {"structured_tools": state["structured_tools"], "mcp_tools": {"base_columnDescription": _T("base_columnDescription", {"database_name": {}, "obj_name": {}})}}
    check("live schema: no client / no server id → the snapshot, unchanged", go(MP.available_tools_live(_stale, "srv1"))[0]["args"] == ["database_name", "obj_name"]
          and go(MP.available_tools_live({**_stale, "mcp_client": _Client()}, None))[0]["args"] == ["database_name", "obj_name"])
    changed = MP.reconcile_state_schema(state, "base_columnDescription", ["database_name", "table_name"])
    check("reconcile: the adapter's arg map for THAT tool now names table_name, values kept as dicts", changed and set(state["mcp_tools"]["base_columnDescription"].args) == {"database_name", "table_name"}
          and state["mcp_tools"]["base_columnDescription"].args["table_name"] == {"name": "table_name"})
    check("reconcile: idempotent, and a no-op for an unknown tool", MP.reconcile_state_schema(state, "base_columnDescription", ["database_name", "table_name"]) is False and MP.reconcile_state_schema(state, "nope", ["x"]) is False)
    roles_live = MP.resolve_roles_by_shape(live)
    check("roles from the live schema pick table_arg = table_name", (roles_live["describe_columns"] or {}).get("table_arg") == "table_name")
    check("available_tools_from_state skips TDA_ and platform tools", [t["name"] for t in MP.available_tools_from_state({"structured_tools": {"Data": [{"name": "base_tableList", "description": "x"}, {"name": "TDA_Chart", "description": "y"}], "Platform Tools": [{"name": "web_search", "description": "z"}]}, "mcp_tools": {}})] == ["base_tableList"])
    # generic sqlite provider over a fake runner
    sq = FakeRunner({
        "COUNT(*) AS n FROM sqlite_master": (["n"], [[2]]),
        "FROM sqlite_master WHERE type IN ('table','view')": (["name", "type"], [["t1", "table"], ["v1", "view"]]),
        'PRAGMA table_info("t1")': (["cid", "name", "type", "notnull", "dflt_value", "pk"], [[0, "id", "INTEGER", 1, None, 1], [1, "email", "TEXT", 0, None, 0]]),
        'PRAGMA foreign_key_list("t1")': (["id", "seq", "table", "from", "to"], [[0, 0, "t2", "t2_id", "id"]]),
        'SELECT * FROM "t1" LIMIT 2': (["id", "email"], [[1, "x@y.z"], [2, "q@r.s"]]),
    })
    g = GP.GenericCatalogProvider("conn:s", "local sqlite", {"driver": "sqlite"}, runner=sq)
    check("sqlite: one schema 'main' with the object count", [(d.name, d.object_count) for d in go(g.list_databases())] == [("main", 2)])
    check("sqlite: objects + kinds", [(o.name, o.kind) for o in go(g.list_objects("main"))] == [("t1", "table"), ("v1", "view")])
    gd = go(g.describe_object("main", "t1"))
    check("sqlite: PRAGMA columns, pk, foreign keys", gd.columns[0].is_primary and gd.columns[0].nullable is False and gd.foreign_keys_out[0].parent_table == "t2" and gd.indexes[0].kind == "primary_key")
    check("sqlite: preview is LIMIT form, unqualified", go(g.preview("main", "t1", 2)).sql == 'SELECT * FROM "t1" LIMIT 2')
    try:
        GP.GenericCatalogProvider("c", "l", {"driver": "oracle"}); check("unknown driver refused at construction", False)
    except CatalogError:
        check("unknown driver refused at construction", True)


# ── §7 ─────────────────────────────────────────────────────────────────────────
def s7_cache():
    clock = {"t": 0.0}
    c = CH.CatalogCache(ttl=10, clock=lambda: clock["t"])
    c.put("u1", "conn:a", "objects", "DB", value=[1])
    check("hit", c.get("u1", "conn:a", "objects", "db") == [1])
    check("per-user isolation: another user never sees it", c.get("u2", "conn:a", "objects", "DB") is None)
    clock["t"] = 11
    check("TTL expiry", c.get("u1", "conn:a", "objects", "DB") is None)
    c.put("u1", "conn:a", "databases", value=[2]); c.put("u1", "conn:b", "databases", value=[3])
    check("invalidate one source", c.invalidate("u1", "conn:a") == 1 and c.get("u1", "conn:b", "databases") == [3])
    check("invalidate the user", c.invalidate("u1") == 1 and c.get("u1", "conn:b", "databases") is None)


# ── §8 ─────────────────────────────────────────────────────────────────────────
class FakeStore:
    def __init__(self, conns): self.conns = conns
    def list(self, uid): return self.conns
    def resolve(self, uid, cid):
        return next(({**c, "password": "pw"} for c in self.conns if c["id"] == cid), None)

def s8_resolution():
    conns = [{"id": "c1", "name": "TD one", "driver": "teradata", "host": "h1"},
             {"id": "c2", "name": "TD two", "driver": "teradata", "host": "h2", "is_default": True},
             {"id": "c3", "name": "jdbc", "driver": "jdbc"}]
    r = RS.resolve_sources("u", {}, store=FakeStore(conns))
    check("direct sources listed with capabilities; unsupported driver skipped", [s["id"] for s in r["sources"]] == ["conn:c1", "conn:c2"] and "foreign_keys" in r["sources"][0]["capabilities"])
    check("default connection wins over first", r["default"] == "conn:c2" and "default" in r["reason"])
    r = RS.resolve_sources("u", {"componentConfig": {"database_navigator": {"sourceId": "conn:c1"}}}, store=FakeStore(conns))
    check("component config wins over the default", r["default"] == "conn:c1" and "component settings" in r["reason"])
    r = RS.resolve_sources("u", {"componentConfig": {"database_navigator": {"sourceId": "conn:gone"}}}, store=FakeStore(conns))
    check("a configured source that vanished is SAID, then the default applies", r["default"] == "conn:c2" and "not available" in r["reason"])
    r = RS.resolve_sources("u", {}, store=FakeStore([conns[0]]))
    check("first connection when none is default", r["default"] == "conn:c1" and "first" in r["reason"])
    r = RS.resolve_sources("u", {}, available_tools=MCP_TOOLS, mcp_label="TD MCP", mcp_server_id="srv1", store=FakeStore([]))
    check("MCP is the source when no connection exists; its tools named", r["default"] == "mcp:srv1" and "base_tableList" in r["sources"][0]["detail"])
    r = RS.resolve_sources("u", {}, available_tools=MCP_TOOLS, store=FakeStore(conns))
    check("a connection outranks MCP, both listed", r["default"] == "conn:c2" and {s["kind"] for s in r["sources"]} == {"direct", "mcp"})
    r = RS.resolve_sources("u", {}, store=FakeStore([]))
    check("nothing → honest empty state naming both remedies", r["default"] is None and "Database Connection" in r["reason"] and "MCP" in r["reason"])
    check("MCP tools without a table lister are no source", RS.mcp_source([MCP_TOOLS[0]]) is None)
    p = RS.build_provider("conn:c1", "u", store=FakeStore(conns), runner=FakeRunner({}))
    check("build_provider: teradata driver → dictionary provider, secrets resolved here only", isinstance(p, TP.TeradataCatalogProvider) and p._creds.get("password") == "pw")
    try:
        RS.build_provider("conn:zzz", "u", store=FakeStore(conns)); check("missing connection → CatalogError naming the remedy", False)
    except CatalogError as exc:
        check("missing connection → CatalogError naming the remedy", "pick another source" in str(exc))
    try:
        RS.build_provider("mcp:x", "u", store=FakeStore(conns)); check("MCP without an invoker refuses", False)
    except CatalogError:
        check("MCP without an invoker refuses", True)


# ── §9 ─────────────────────────────────────────────────────────────────────────
class FakeGraph:
    """A discovery-shaped ontology: table 'fitness_db.Sales' implements entity 'Sales'
    which contains attribute 'Sales.Email' flagged PII."""
    kg_id = "kg-1"
    def __init__(self):
        self.ents = {1: {"id": 1, "name": "fitness_db.Sales", "entity_type": "table", "properties": {}},
                     2: {"id": 2, "name": "Sales", "entity_type": "entity", "properties": {"description": "One row per sale", "row_count": 100}},
                     3: {"id": 3, "name": "Sales.TotalAmount", "entity_type": "attribute", "properties": {"is_pii": True, "classification": "CONFIDENTIAL"}},
                     4: {"id": 4, "name": "Sales.SaleID", "entity_type": "attribute", "properties": {"is_primary_key": True}},
                     5: {"id": 5, "name": "Customers", "entity_type": "entity", "properties": {}}}
        self.rels = [{"source_id": 1, "target_id": 2, "relationship_type": "implements", "source_name": "fitness_db.Sales", "target_name": "Sales"},
                     {"source_id": 2, "target_id": 3, "relationship_type": "contains", "source_name": "Sales", "target_name": "Sales.TotalAmount"},
                     {"source_id": 2, "target_id": 4, "relationship_type": "contains", "source_name": "Sales", "target_name": "Sales.SaleID"},
                     {"source_id": 2, "target_id": 5, "relationship_type": "foreign_key", "source_name": "Sales", "target_name": "Customers"}]
    def get_entity_by_name(self, name, t=None):
        return next((e for e in self.ents.values() if e["name"] == name and (t is None or e["entity_type"] == t)), None)
    def get_entity(self, i): return self.ents.get(i)
    async def list_entities(self, entity_type=None, limit=200): return [e for e in self.ents.values() if entity_type in (None, e["entity_type"])]
    async def get_relationships(self, i, direction="both"):
        return [r for r in self.rels if (direction in ("both", "outgoing") and r["source_id"] == i) or (direction in ("both", "incoming") and r["target_id"] == i)]

def s9_service():
    from trusted_data_agent.dbcatalog.types import PreviewResult
    pv = SV.mask_preview(PreviewResult(columns=["SaleID", "Email"], rows=[[1, "jane@x.io"], [2, None]]), ["email"])
    check("mask keeps shape, never content; masked columns named", pv.rows == [[1, "j•••"], [2, None]] and pv.masked_columns == ["Email"])
    check("mask is a no-op with no pii columns", SV.mask_preview(PreviewResult(columns=["a"], rows=[[1]]), []).masked_columns == [])
    cache = CH.CatalogCache()
    rn = FakeRunner(TD_TABLE)
    svc = SV.NavigatorService(store=FakeStore([{"id": "x", "name": "TD", "driver": "teradata"}]), runner=rn, cache=cache)
    p = svc.provider("u", "conn:x")
    go(svc.databases("u", p)); n = len(rn.calls); go(svc.databases("u", p))
    check("databases are cached (no second dictionary read)", len(rn.calls) == n)
    d = go(svc.describe("u", p, "fitness_db", "Sales", ontology_store=FakeGraph(), scope_dbs=["fitness_db"]))
    check("describe: ontology overlay present, PII from the ontology joins the column flags (a column the dictionary never flags)",
          d.ontology["present"] and d.ontology["entity"]["description"] == "One row per sale" and "pii" in next(c for c in d.columns if c.name == "TotalAmount").flags
          and "pii" not in I.column_flags(ColumnInfo("TotalAmount", "DECIMAL(12,2)")))
    check("describe: source + scope stamped", d.intelligence["source"]["id"] == "conn:x" and d.intelligence["scope"] == "in_scope")
    pv = go(svc.preview("u", p, "fitness_db", "Sales", 3, profile_id=None))
    check("preview masks the ontology-flagged PII column by default", pv.masked_columns == ["Email"] and pv.rows[0][1] == "j•••")
    pv2 = go(svc.preview("u", p, "fitness_db", "Sales", 3, unmask=True))
    check("unmask is explicit", pv2.masked_columns == [] and pv2.rows[0][1] == "jane@x.io")
    r = go(svc.search("u", p, "sale"))
    check("search uses the dictionary (SEARCH capability) and ranks: exact Sales first", r["scope"] == "dictionary" and r["hits"][0].get("object") == "Sales")
    check("search: a described object's columns join the pool", any(h["kind"] == "column" and h["column"] == "SaleDate" for h in r["hits"]))
    # a system database's object must not outrank the estate: plant an exact-name hit in DBC
    cache.put("u", "conn:x", "objects", "DBC", value=[ObjectInfo("DBC", "sale")])
    r2 = go(svc.search("u", p, "sale"))
    check("search: system databases are excluded by default (an exact DBC.sale never wins)", not any(str(h.get("database", "")).lower() == "dbc" for h in r2["hits"]) and r2["system_hidden"])
    check("search: include_system opts them back in", any(str(h.get("database", "")).lower() == "dbc" for h in go(svc.search("u", p, "sale", include_system=True))["hits"]))
    c = go(svc.count("u", p, "fitness_db", "Sales"))
    check("count is exact and updates the cached detail", c["row_count"] == 12345680 and cache.get("u", "conn:x", "detail", "fitness_db", "Sales").info.row_count_source == "exact")
    check("refresh drops the user's cache", svc.refresh("u") > 0 and cache.get("u", "conn:x", "databases") is None)
    try:
        go(svc.describe("u", p, "fitness_db", "v_top")); ok = True
    except Exception:
        ok = False
    check("view preview is allowed, a macro is not", ok and inspect.getsource(SV.NavigatorService.preview).count('("table", "view")') == 1)


# ── §10 ────────────────────────────────────────────────────────────────────────
def s10_overlay():
    cols = [ColumnInfo("SaleID", "INTEGER"), ColumnInfo("TotalAmount", "DECIMAL(12,2)")]
    o = go(OV.ontology_overlay(FakeGraph(), "fitness_db", "Sales", cols))
    check("overlay: entity, declared keys, related, per-column facts", o["present"] and o["declared_keys"] == ["SaleID"] and o["related"][0]["entity"] == "Customers" and cols[1].ontology.get("is_pii") is True)
    check("overlay: absent object → present False, never raises", go(OV.ontology_overlay(FakeGraph(), "x", "Nope", [])) == {"present": False})
    class Broken:
        def get_entity_by_name(self, *a): raise RuntimeError("down")
    check("overlay: a failing store fails OPEN", go(OV.ontology_overlay(Broken(), "a", "b", [])) == {"present": False})
    check("overlay: no store → absent", go(OV.ontology_overlay(None, "a", "b", [])) == {"present": False})


# ── §11 ────────────────────────────────────────────────────────────────────────
def s11_component_face():
    from trusted_data_agent.components.manager import get_component_manager, _build_args_schema
    m = get_component_manager()
    comp = m.get_component("database_navigator")
    check("component loads with its handler", comp is not None and comp.handler is not None and comp.handler.tool_name == "TDA_DatabaseNavigator")
    # OFF by default for every class (operator decision 2026-10-28, trust campaign C14): a
    # coordinator inherited it, explored the catalogue itself instead of routing to an expert
    # and looped — 229K tokens for one turn. An agent that wants it enables it explicitly.
    check("manifest: action component, Knowledge category, never a coordinator's by default",
          comp.component_type == "action" and comp.category == "Knowledge"
          and set(comp.profile_defaults["enabled_for"]) == {"tool_enabled", "llm_only"})
    from trusted_data_agent.components import enablement as _en
    check("…a coordinator that does not mention it does not get it",
          not _en.manifest_default(comp, "genie"))
    check("…an EXISTING data agent that does not mention it keeps it (no config, no signature moved)",
          _en.manifest_default(comp, "tool_enabled") and _en.manifest_default(comp, "llm_only"))
    from trusted_data_agent.api import rest_routes as _rr
    check("…a NEW agent starts without it (the creation default)",
          comp.profile_defaults.get("on_for_new_agents") is False
          and _rr._new_agent_default(comp.profile_defaults) is False
          and _rr._new_agent_default({"enabled_for": ["llm_only"]}) is True)
    check("…an explicit enable still turns it on (step 2 of the chain wins)",
          _en.decide(comp, {"profile_type": "genie", "componentConfig": {"database_navigator": {"enabled": True}}}, available=True) is True)
    schema = _build_args_schema(comp.tool_definition)
    _base = {"action", "database", "object_name", "query_text", "limit", "source_id"}
    # the gated `sql` argument (the per-agent `query` opt-in) is in the RAW definition only;
    # what a model sees is the gated definition — without the opt-in, exactly the base fields
    _gated = _build_args_schema(m.tool_definition_for(comp, {"componentConfig": {"database_navigator": {"enabled": True}}}))
    check("gated schema without the query opt-in = the base fields", set(_gated.model_fields) == _base, str(sorted(_gated.model_fields)))
    _opted = _build_args_schema(m.tool_definition_for(comp, {"componentConfig": {"database_navigator": {"enabled": True, "query": {"enabled": True}}}}))
    _query_args = {"sql", "collection", "pipeline"}   # SQL, or a MongoDB collection + pipeline
    check("gated schema WITH the opt-in adds exactly the query arguments", set(_opted.model_fields) == _base | _query_args,
          str(sorted(_opted.model_fields)))
    check("tool schema builds; no bare list args", set(schema.model_fields) == _base | _query_args
          and not any(a.get("type") in ("list", "array") for a in comp.tool_definition["args"].values()))
    ins = json.load(open(os.path.join(ROOT, "components/builtin/database_navigator/instructions.json")))
    check("instructions: none/medium/heavy, both non-empty levels name every action and the no-emoji rule",
          ins["none"] == "" and all(all(a in ins[lvl] for a in ("describe", "search", "preview", "suggest_joins", "open")) and "emoji" in ins[lvl] for lvl in ("medium", "heavy")))
    check("instructions carry no usage heuristics that belong in the skill", "role" not in ins["medium"].lower() or "fact" not in ins["medium"].lower())
    h = comp.handler
    check("validate: action required", h.validate_arguments({})[0] is False)
    check("validate: describe needs database + object", h.validate_arguments({"action": "describe", "database": "d"})[0] is False and h.validate_arguments({"action": "describe", "database": "d", "object_name": "t"})[0])
    check("validate: search needs query_text", h.validate_arguments({"action": "search"})[0] is False)
    # the handler answers through the SAME service the routes use, with a fake service
    from trusted_data_agent.dbcatalog import service as svcmod
    orig = svcmod._SERVICE
    cache = CH.CatalogCache(); rn = FakeRunner(TD_TABLE)
    svcmod._SERVICE = SV.NavigatorService(store=FakeStore([{"id": "x", "name": "TD", "driver": "teradata"}]), runner=rn, cache=cache)
    try:
        ctx = {"user_uuid": "u", "profile_id": None, "profile_config": {}, "STATE": {"structured_tools": {}, "mcp_tools": {}}}
        pl = go(h.process({"action": "describe", "database": "fitness_db", "object_name": "Sales"}, ctx))
        check("describe payload: inline, spec carries trimmed detail + source + message", pl.component_id == "database_navigator" and pl.spec["detail"]["intelligence"]["role"] and pl.spec["source"]["id"] == "conn:x" and "columns:" in pl.spec["message"])
        check("describe text names keys, references and the sample query", "declared keys" in pl.tts_text and "references: Customers" in pl.tts_text and "sample query" in pl.tts_text)
        pl = go(h.process({"action": "preview", "database": "fitness_db", "object_name": "Sales", "limit": 3}, ctx))
        check("preview payload masks PII and says so", pl.spec["preview"]["masked_columns"] == ["Email"] and "masked" in pl.spec["message"])
        pl = go(h.process({"action": "search", "query_text": "sale"}, ctx))
        check("search payload carries ranked hits", pl.spec["hits"][0].get("object") == "Sales" and "match" in pl.spec["message"])
        pl = go(h.process({"action": "suggest_joins", "database": "fitness_db", "object_name": "Sales"}, ctx))
        check("suggest_joins payload: declared both ways", pl.spec["declared_out"][0]["parent_table"] == "Customers" and pl.spec["declared_in"][0]["child_table"] == "SaleDetails")
        pl = go(h.process({"action": "open", "database": "fitness_db"}, ctx))
        check("open payload", pl.spec["action"] == "open" and pl.spec["database"] == "fitness_db")
        pl = go(h.process({"action": "describe", "database": "fitness_db", "object_name": "Nope"}, ctx))
        check("a catalog error is a recovery-shaped payload, not an exception", pl.spec["error"] == "catalog" and "refresh" in pl.spec["message"])
        svcmod._SERVICE = SV.NavigatorService(store=FakeStore([]), runner=rn, cache=cache)
        pl = go(h.process({"action": "describe", "database": "a", "object_name": "b"}, ctx))
        check("no source → the payload names both remedies, no guessing", pl.spec["error"] == "no_source" and "Database Connection" in pl.spec["message"])
    finally:
        svcmod._SERVICE = orig
    reg = json.load(open(os.path.join(ROOT, "skills/skill_registry.json")))
    e = next((x for x in reg["skills"] if x.get("skill_id") == "database-navigator-expert"), None)
    check("companion skill registered with component_id", e is not None and e.get("component_id") == "database_navigator" and e.get("status") == "active")
    check("companion skill files exist", os.path.exists(os.path.join(ROOT, "skills/builtin/database-navigator-expert/skill.json")) and os.path.exists(os.path.join(ROOT, "skills/builtin/database-navigator-expert/database-navigator-expert.md")))
    from agent_packs.common.component_config import ACTION_COMPONENTS, focused_component_config
    check("packs default the navigator OFF like every action component", "database_navigator" in ACTION_COMPONENTS and focused_component_config()["database_navigator"] == {"enabled": False})
    src = open(os.path.join(ROOT, "components/builtin/database_navigator/handler.py")).read()
    check("the handler holds no catalog logic of its own — it calls the service", "get_navigator_service" in src and "DBC." not in src and "run_rows" not in src)


# ── §12 ────────────────────────────────────────────────────────────────────────
def s12_rest_face():
    path = os.path.join(ROOT, "src/trusted_data_agent/api/database_navigator_routes.py")
    tree = ast.parse(open(path).read())
    routes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route" for d in n.decorator_list)]
    check("eight routes", len(routes) == 8, [r.name for r in routes])
    for r in routes:
        calls = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(r) if isinstance(c, ast.Call)}
        check(f"{r.name}: gated on component availability + auth", "_gate" in calls and any(isinstance(d, ast.Name) and d.id == "require_auth" for d in r.decorator_list))
    main_src = open(os.path.join(ROOT, "src/trusted_data_agent/main.py")).read()
    check("blueprint registered in main.py under /api", "database_navigator_routes import dbnav_bp" in main_src and 'register_blueprint(dbnav_bp, url_prefix="/api")' in main_src)
    src = open(path).read()
    check("routes never build a provider themselves — one service", "get_navigator_service" in src and "TeradataCatalogProvider" not in src)
    # the gate, executed: jsonify swapped for identity (no app context in CI)
    from trusted_data_agent.api import database_navigator_routes as R
    from trusted_data_agent.components import settings as _st
    class U: id = "u1"
    orig_j, orig_avail = R.jsonify, _st.is_component_available
    R.jsonify = lambda d: d
    try:
        _st.is_component_available = lambda cid, uid=None: False
        g = R._gate(U())
        check("gate: unavailable component → 403 component_disabled", isinstance(g, tuple) and g[1] == 403 and g[0]["code"] == "component_disabled")
        _st.is_component_available = lambda cid, uid=None: True
        check("gate: available → no refusal", R._gate(U()) is None)
        def _boom(cid, uid=None): raise RuntimeError("settings down")
        _st.is_component_available = _boom
        g = R._gate(U())
        check("gate: a failing availability check fails CLOSED (403), never open", isinstance(g, tuple) and g[1] == 403 and g[0]["code"] == "component_gate_error")
        _st.is_component_available = lambda cid, uid=None: cid == "database_navigator"
        check("gate asks about THIS component id", R._gate(U()) is None)
    finally:
        R.jsonify, _st.is_component_available = orig_j, orig_avail
    check("the gate fails CLOSED on error", "fail CLOSED" in src and src.count("component_gate_error") == 1)


for fn in (s1_contract, s2_flags_keys, s3_roles, s4_joins_search, s5_teradata, s6_mcp_generic, s7_cache,
           s8_resolution, s9_service, s10_overlay, s11_component_face, s12_rest_face):
    run(fn)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
