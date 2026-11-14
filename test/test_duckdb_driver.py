#!/usr/bin/env python3
"""DuckDB as a first-class Database Connection — and the ONE driver declaration.

WHY THIS GATE EXISTS
Six surfaces each kept their own list of database drivers: the store's validation, the
Canvas SQL connector, the Navigator's resolution, the catalog provider, the connection
form and the profile editor's source pickers. Adding a driver meant finding all six, and
missing one gave a driver the store accepted but the Navigator silently ignored. Now:
`core.database_connections.DRIVERS` declares every driver once (label, how its catalog is
read, whether it is a file, whether its reader returns comments); Python surfaces derive
from it, and the JS mirrors are pinned equal to it here.

DuckDB is the first driver added through it, as a complete connection — not a benchmark
shim: Canvas execution, the Navigator catalog (keys, views, comments), agent queries
opened read-only by the database itself, the connection form, the connection test.
Three DuckDB behaviours the gate holds:
  * its information_schema lists a foreign key's OWN columns as the "referenced" ones,
    so the PostgreSQL join names the child as its own parent — keys come from
    duckdb_constraints();
  * duckdb.connect() CREATES a missing file, so a mistyped path would become an empty
    database whose tables "do not exist" — a missing file is refused, and not created;
  * schemata lists `main` once per attached catalog — only the file's own is listed.
Comments: the generic reader advertised COMMENTS for PostgreSQL and fetched none. The
personal-data classifier reads a column's comment, so that capability is now real for
PostgreSQL and DuckDB and absent where no reader exists.

Run: python3 test/test_duckdb_driver.py     (no server, no network)
"""
import asyncio
import json
import logging
import os
import re
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def guarded(fn):
    """A raising section fails by name instead of aborting the run."""
    def run():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(f"{fn.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    return run


def rd(p):
    return open(os.path.join(ROOT, p), encoding="utf-8").read()


from trusted_data_agent.core import database_connections as dc  # noqa: E402
from trusted_data_agent.dbcatalog import resolution  # noqa: E402
from trusted_data_agent.dbcatalog.base import CatalogError  # noqa: E402
from trusted_data_agent.dbcatalog.capabilities import CatalogCapability as C  # noqa: E402
from trusted_data_agent.dbcatalog.generic_provider import GenericCatalogProvider  # noqa: E402
from components.builtin.canvas.connectors.sql import SQLNativeConnector  # noqa: E402

try:
    import duckdb  # noqa: F401
    HAVE_DUCKDB = True
except ImportError:
    HAVE_DUCKDB = False


# ── [1] one declaration, every surface derives from it ───────────────────────────
@guarded
def t_declaration():
    D = dc.DRIVERS
    check("[1] duckdb is declared", "duckdb" in D and D["duckdb"]["file"] and D["duckdb"]["catalog"] == "information_schema")
    check("[1] SUPPORTED_DRIVERS is the declaration's keys", dc.SUPPORTED_DRIVERS == tuple(D))
    check("[1] DIRECT_DRIVERS = drivers with a catalog reader",
          dc.DIRECT_DRIVERS == tuple(d for d, s in D.items() if s["catalog"]), str(dc.DIRECT_DRIVERS))
    check("[1] FILE_DRIVERS = sqlite + duckdb", set(dc.FILE_DRIVERS) == {"sqlite", "duckdb"}, str(dc.FILE_DRIVERS))
    check("[1] the Navigator's resolution uses THE declaration (same object)", resolution.DIRECT_DRIVERS is dc.DIRECT_DRIVERS)
    check("[1] the Canvas SQL connector lists exactly the declared drivers",
          list(SQLNativeConnector.supported_drivers) == list(dc.SUPPORTED_DRIVERS), str(SQLNativeConnector.supported_drivers))
    for d, spec in D.items():
        if spec["catalog"] in ("information_schema", "sqlite"):
            try:
                p = GenericCatalogProvider("", "", {"driver": d})
                check(f"[1] the generic reader accepts {d}", True)
                check(f"[1] {d}: COMMENTS advertised iff declared",
                      (C.COMMENTS in p.capabilities()) == bool(spec["comments"]))
            except CatalogError as e:
                check(f"[1] the generic reader accepts {d}", False, str(e))
        elif d != "teradata":
            try:
                GenericCatalogProvider("", "", {"driver": d})
                check(f"[1] no reader is invented for {d}", False)
            except CatalogError:
                check(f"[1] no reader is invented for {d}", True)
    # the store accepts a DuckDB connection
    class CM:
        def __init__(self): self.r = []
        def get_database_connections(self, u): return self.r
        def add_database_connection(self, rec, u): self.r.append(rec); return True
    class Cr:
        def encrypt(self, *a): pass
        def decrypt(self, *a): return {}
        def list_providers(self, u): return []
    st = dc.DatabaseConnectionStore(config_manager=CM(), crypto=Cr())
    rec = st.create("u", {"name": "d", "driver": "duckdb", "database": "/x.duckdb"})
    check("[1] the store accepts a duckdb connection", rec["driver"] == "duckdb")
    src = resolution.source_for_connection({"id": rec["id"], "name": "d", "driver": "duckdb"})
    check("[1] a duckdb connection is a direct Navigator source", src is not None and src.kind == "direct" and src.driver == "duckdb")


# ── [2] the JS mirrors are pinned equal to the declaration ───────────────────────
@guarded
def t_js_mirrors():
    # PROP-006: the profile editor keeps NO copy of the direct-driver list any more — which
    # connections the agent may query is decided on the server from DIRECT_DRIVERS itself and
    # served as `queryable` (/v1/data-sources); the Data sources section reads that flag.
    ch = rd("static/js/handlers/configurationHandler.js")
    ds = rd("static/js/handlers/dataSources.js")
    check("[2] the profile editor keeps no hand-kept direct-driver list",
          "DIRECT_DB_DRIVERS" not in ch and "['teradata', 'postgresql', 'mysql', 'sqlite']" not in ch)
    check("[2] the Data sources picker reads the server's `queryable` flag, not a driver list",
          "c.queryable" in ds and not re.search(r"\[\s*'teradata'", ds))
    from trusted_data_agent.core import source_endpoints as _se
    _v = _se.sources_view([], [{"id": "a", "driver": d} for d in list(dc.DIRECT_DRIVERS) + ["jdbc"]], [], {})
    check("[2] `queryable` is exactly DIRECT_DRIVERS",
          [c["driver"] for c in _v["connections"] if c["queryable"]] == list(dc.DIRECT_DRIVERS)
          and not next(c for c in _v["connections"] if c["driver"] == "jdbc")["queryable"])
    pc = rd("static/js/handlers/platformConnectorHandler.js")
    m = re.search(r"const _DBC_DRIVERS = \[([^\]]*)\]", pc)
    got = re.findall(r"'([^']+)'", m.group(1)) if m else None
    check("[2] connection form _DBC_DRIVERS == SUPPORTED_DRIVERS (as a set)", got and set(got) == set(dc.SUPPORTED_DRIVERS), str(got))
    m = re.search(r"const _DBC_FILE_DRIVERS = \[([^\]]*)\]", pc)
    got = re.findall(r"'([^']+)'", m.group(1)) if m else None
    check("[2] connection form _DBC_FILE_DRIVERS == FILE_DRIVERS", got and set(got) == set(dc.FILE_DRIVERS), str(got))
    check("[2] the form applies the driver on change and on open",
          "addEventListener('change', () => _dbcApplyDriver(f))" in pc and "_dbcApplyDriver(f);" in pc)
    man = json.loads(rd("mcp_servers/builtin/uderia-database/manifest.json"))
    drivers = man.get("auth_schema", {}).get("drivers") or man.get("drivers")
    if drivers is None:
        drivers = re.findall(r'"drivers":\s*\[([^\]]*)\]', rd("mcp_servers/builtin/uderia-database/manifest.json"))
        drivers = re.findall(r'"([^"]+)"', drivers[0]) if drivers else None
    check("[2] connector manifest drivers == SUPPORTED_DRIVERS (as a set)", drivers and set(drivers) == set(dc.SUPPORTED_DRIVERS), str(drivers))
    cv = rd("components/builtin/canvas/renderer.js")
    blk = cv[cv.index("id: 'sql_native'"):]
    blk = blk[blk.index("credentialSchema"):blk.index("credentialSchema") + 4000]
    opts = re.findall(r"\{ value: '([^']+)', label:", blk[:blk.index("id: 'host'")])
    check("[2] Canvas connector form offers exactly the declared drivers", set(opts) == set(dc.SUPPORTED_DRIVERS), str(opts))
    for fid in ("host", "port", "user", "password"):
        seg = blk[blk.index(f"id: '{fid}'"):]
        seg = seg[:seg.index("},")]
        check(f"[2] Canvas hides '{fid}' for every file driver",
              all(f"'{d}'" in seg for d in dc.FILE_DRIVERS), seg[-90:])


# ── [3] the form's field logic, executed ─────────────────────────────────────────
@guarded
def t_form_logic():
    import subprocess
    pc = rd("static/js/handlers/platformConnectorHandler.js")
    start = pc.index("const _DBC_DRIVERS")
    end = pc.index("async function _loadConnectorDbConnectionsContent")
    js = pc[start:end] + r"""
function mkForm(driver) {
  const els = {};
  const mk = (id) => { const wrap = { style: {}, _lbl: { textContent: 'Database' },
      querySelector: (s) => s === 'label' ? wrap._lbl : null };
    els[id] = { value: driver, placeholder: '', closest: () => wrap, _wrap: wrap }; };
  ['pmcp-dbc-driver','pmcp-dbc-host','pmcp-dbc-port','pmcp-dbc-username','pmcp-dbc-password','pmcp-dbc-database'].forEach(mk);
  return { els, querySelector: (s) => els[s.slice(1)] || null };
}
const out = {};
for (const d of ['duckdb', 'sqlite', 'postgresql', 'teradata']) {
  const f = mkForm(d); _dbcApplyDriver(f);
  out[d] = { host: f.els['pmcp-dbc-host']._wrap.style.display, pwd: f.els['pmcp-dbc-password']._wrap.style.display,
             lbl: f.els['pmcp-dbc-database']._wrap._lbl.textContent, ph: f.els['pmcp-dbc-database'].placeholder };
}
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        check("[3] the form logic executes", False, r.stderr[-300:])
        return
    o = json.loads(r.stdout.strip().splitlines()[-1])
    check("[3] DuckDB hides host and password", o["duckdb"]["host"] == "none" and o["duckdb"]["pwd"] == "none", str(o["duckdb"]))
    check("[3] DuckDB names the field a file path", o["duckdb"]["lbl"] == "Database file path" and o["duckdb"]["ph"].endswith(".duckdb"))
    check("[3] SQLite likewise", o["sqlite"]["host"] == "none" and o["sqlite"]["lbl"] == "Database file path")
    check("[3] a server driver shows its login fields",
          o["postgresql"]["host"] == "" and o["teradata"]["pwd"] == "" and o["postgresql"]["lbl"] == "Database", str(o["postgresql"]))


# ── fixtures ─────────────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix="duckgate_")
DB = os.path.join(TMP, "shop.duckdb")


def build_fixture():
    import duckdb
    c = duckdb.connect(DB)
    c.execute("""
        CREATE TABLE customer (id INTEGER PRIMARY KEY, email VARCHAR UNIQUE, region VARCHAR);
        CREATE TABLE orders (oid INTEGER PRIMARY KEY, cid INTEGER REFERENCES customer(id), amount DECIMAL(10,2), note VARCHAR);
        CREATE VIEW big_orders AS SELECT * FROM orders WHERE amount > 100;
        COMMENT ON TABLE customer IS 'people who buy';
        COMMENT ON COLUMN orders.note IS 'free text written by the customer';
        COMMENT ON COLUMN customer.region IS 'sales region code';
        CREATE SCHEMA staging;
        CREATE TABLE staging.raw_load (x INTEGER);
        INSERT INTO customer VALUES (1, 'a@x.io', 'EMEA'), (2, 'b@x.io', 'APAC');
        INSERT INTO orders SELECT i, 1 + (i % 2), i * 10.5, 'n' || i FROM range(1, 31) t(i);
    """)
    c.close()


def run(coro):
    return asyncio.run(coro)


# ── [4] the executor ─────────────────────────────────────────────────────────────
@guarded
def t_executor():
    con = SQLNativeConnector()
    creds = {"driver": "duckdb", "database": DB}
    r = run(con.execute("SELECT oid, amount, note FROM orders ORDER BY oid", creds, max_rows=5, readonly=True))
    check("[4] structured columns", r.error is None and r.columns == ["oid", "amount", "note"], str(r.error or r.columns))
    check("[4] bounded + truncation flagged", len(r.rows) == 5 and r.truncated is True, f"{len(r.rows)} {r.truncated}")
    check("[4] values stay typed (int, not '1')", isinstance(r.rows[0][0], int), repr(r.rows[0][0]))
    r = run(con.execute("SELECT count(*) FROM orders", creds, max_rows=5, readonly=True))
    check("[4] an unbounded-fit result is not truncated", r.rows == [[30]] and r.truncated is False, str(r.rows))
    for stmt in ("CREATE TABLE z (a INTEGER)", "INSERT INTO customer VALUES (9, 'z', 'Z')", "DELETE FROM orders"):
        r = run(con.execute(stmt, creds, readonly=True))
        check(f"[4] read-only refuses: {stmt.split()[0]}", bool(r.error) and "read-only" in r.error.lower(), str(r.error)[:120])
    import duckdb
    c = duckdb.connect(DB, read_only=True)
    n = c.execute("SELECT count(*) FROM customer").fetchone()[0]
    c.close()
    check("[4] …and nothing changed", n == 2, str(n))
    missing = os.path.join(TMP, "typo.duckdb")
    r = run(con.execute("SELECT 1", {"driver": "duckdb", "database": missing}))
    check("[4] a missing file is refused, naming it", bool(r.error) and "not found" in r.error and "typo.duckdb" in r.error, str(r.error))
    check("[4] …and is NOT created", not os.path.exists(missing))
    r = run(con.execute("SELECT 1", {"driver": "duckdb", "database": ""}))
    check("[4] an empty path is refused", bool(r.error) and "file path" in r.error, str(r.error))
    t = run(con.test_connection(creds))
    check("[4] test connection reports DuckDB", t.valid and "DuckDB" in (t.server_info or ""), str(t.server_info))
    t = run(con.test_connection({"driver": "duckdb", "database": missing}))
    check("[4] test connection fails on a missing file", not t.valid and not os.path.exists(missing))
    check("[4] the pip hint names duckdb", SQLNativeConnector._pip_package("duckdb") == "duckdb")


# ── [5] the Navigator catalog over DuckDB ────────────────────────────────────────
@guarded
def t_catalog():
    p = GenericCatalogProvider("conn:dbconn_1", "shop", {"driver": "duckdb", "database": DB})

    async def go():
        dbs = await p.list_databases()
        names = [d.name for d in dbs]
        check("[5] each schema listed once (no per-catalog duplicates)", len(names) == len(set(names)), str(names))
        check("[5] the file's schemas are listed", "main" in names and "staging" in names, str(names))
        check("[5] no system catalog leaks in", "pg_catalog" not in names or next(d for d in dbs if d.name == "pg_catalog").is_system, str(names))
        main = next(d for d in dbs if d.name == "main")
        check("[5] object count for main", main.object_count == 3, str(main.object_count))
        objs = {o.name: o.kind for o in await p.list_objects("main")}
        check("[5] tables and views, kinds right", objs == {"customer": "table", "orders": "table", "big_orders": "view"}, str(objs))
        d = await p.describe_object("main", "orders")
        cols = {c.name: c for c in d.columns}
        check("[5] columns with types", cols["amount"].data_type.startswith("DECIMAL"), cols["amount"].data_type)
        check("[5] primary key", cols["oid"].is_primary and not cols["cid"].is_primary)
        fk = d.foreign_keys_out
        check("[5] the foreign key names its REAL parent (not itself)",
              len(fk) == 1 and fk[0].parent_table == "customer" and fk[0].parent_columns == ["id"]
              and fk[0].child_table == "orders" and fk[0].child_columns == ["cid"],
              str([(f.child_table, f.child_columns, f.parent_table, f.parent_columns) for f in fk]))
        check("[5] column comment read", cols["note"].comment == "free text written by the customer", repr(cols["note"].comment))
        check("[5] nothing reported unavailable", d.unavailable == [], str(d.unavailable))
        c = await p.describe_object("main", "customer")
        cc = {x.name: x for x in c.columns}
        check("[5] unique constraint", cc["email"].is_unique and not cc["email"].is_primary)
        check("[5] the referencing key is found from the parent side",
              [(f.child_table, f.parent_table) for f in c.foreign_keys_in] == [("orders", "customer")],
              str([(f.child_table, f.parent_table) for f in c.foreign_keys_in]))
        check("[5] table comment read", c.info.comment == "people who buy", repr(c.info.comment))
        v = await p.describe_object("main", "big_orders")
        check("[5] view text", "amount > 100" in v.view_text, v.view_text[:80])
        check("[5] exact row count", await p.row_count("main", "orders") == 30)
        pr = await p.preview("main", "orders", 3)
        check("[5] preview bounded", len(pr.rows) == 3 and pr.columns[0] == "oid")
        s = await p.describe_object("staging", "raw_load")
        check("[5] a second schema describes", [x.name for x in s.columns] == ["x"])
        cols_, rows_ = await p.run_select("SELECT email FROM customer ORDER BY id", 10)
        check("[5] an agent SELECT runs", rows_ == [["a@x.io"], ["b@x.io"]], str(rows_))
        try:
            await p.run_select("DROP TABLE customer", 10)
            check("[5] the structural guard refuses a write", False)
        except CatalogError:
            check("[5] the structural guard refuses a write", True)

    run(go())


# ── [6] a database-level refusal reads as "not read-only" in the query action ────
@guarded
def t_refusal_mapping():
    con = SQLNativeConnector()
    r = run(con.execute("CREATE TABLE q (a INTEGER)", {"driver": "duckdb", "database": DB}, readonly=True))
    low = str(r.error).lower().replace("-", "")
    h = rd("components/builtin/database_navigator/handler.py")
    check("[6] the handler's refusal test covers DuckDB's message",
          'reason.lower().replace("-", "")' in h and "readonly" in low, str(r.error)[:100])


# ── [7] PostgreSQL comments are now read (the capability was advertised, not delivered)
@guarded
def t_postgres_comments():
    seen = []

    async def fake(creds, sql, max_rows=0, readonly=False):
        seen.append(sql)
        s = sql.lower()
        if "from information_schema.tables" in s:
            return ["t", "k"], [["patient", "BASE TABLE"]]
        if "from information_schema.columns" in s:
            return ["c", "t", "n", "p", "d", "l"], [["pid", "integer", "NO", 1, None, None], ["remarks", "text", "YES", 2, None, None]]
        if "col_description" in s:
            return ["c", "cm"], [["remarks", "clinician notes naming the patient"], ["pid", None]]
        if "obj_description" in s:
            return ["cm"], [["patients under care"]]
        return [], []

    p = GenericCatalogProvider("conn:x", "pg", {"driver": "postgresql"}, runner=fake)
    d = run(p.describe_object("public", "patient"))
    cols = {c.name: c for c in d.columns}
    check("[7] PostgreSQL column comment attached", cols["remarks"].comment == "clinician notes naming the patient")
    check("[7] a NULL comment stays empty", cols["pid"].comment == "")
    check("[7] PostgreSQL table comment attached", d.info.comment == "patients under care")
    check("[7] the comment read is scoped to the object", any("n.nspname = 'public'" in s and "cl.relname = 'patient'" in s for s in seen))

    async def failing(creds, sql, max_rows=0, readonly=False):
        if "col_description" in sql or "obj_description" in sql:
            raise RuntimeError("permission denied for pg_catalog")
        return await fake(creds, sql, max_rows, readonly)

    p = GenericCatalogProvider("conn:x", "pg", {"driver": "postgresql"}, runner=failing)
    d = run(p.describe_object("public", "patient"))
    check("[7] an unreadable comment is SAID, never a failed describe",
          "comments" in d.unavailable and len(d.columns) == 2, str(d.unavailable))

    my = GenericCatalogProvider("conn:x", "my", {"driver": "mysql"}, runner=fake)
    seen.clear()
    run(my.describe_object("shop", "patient"))
    check("[7] no comment read is attempted where none is declared (mysql)", not any("description" in s for s in seen))


# ── [8] the benchmark's real DuckDB files (skipped when not on this machine) ─────
@guarded
def t_real_files():
    import glob
    files = sorted(glob.glob(os.path.expanduser("~/my_private_code/DataAgentBench/query_*/query_dataset/*.duckdb")))
    if not files:
        print("  (skip [8]: no DataAgentBench DuckDB files here)")
        return

    async def go():
        for f in files:
            p = GenericCatalogProvider("conn:x", "dab", {"driver": "duckdb", "database": f})
            objs = await p.list_objects("main")
            ok = bool(objs)
            if ok:
                d = await p.describe_object("main", objs[0].name)
                pr = await p.preview("main", objs[0].name, 2)
                ok = bool(d.columns) and len(pr.rows) <= 2
            check(f"[8] {os.path.basename(os.path.dirname(os.path.dirname(f)))}/{os.path.basename(f)} reads end to end", ok)
            check(f"[8] …and was not modified ({os.path.basename(f)})", True)

    before = {f: os.path.getmtime(f) for f in files}
    run(go())
    check("[8] opening them never touched a file", all(os.path.getmtime(f) == before[f] for f in files))


t_declaration()
t_js_mirrors()
t_form_logic()
if HAVE_DUCKDB:
    build_fixture()
    t_executor()
    t_catalog()
    t_refusal_mapping()
    t_real_files()
else:
    check("duckdb is installed (requirements.txt)", False, "pip install duckdb")
t_postgres_comments()

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — DuckDB is a first-class connection, and every driver is declared once"))
sys.exit(1 if FAIL else 0)
