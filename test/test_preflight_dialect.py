#!/usr/bin/env python3
"""P3 — the SQL checks speak the call's dialect, and a fact judges only its own source.

WHY THIS GATE EXISTS
Before P3 every preflight check parsed as Teradata and read facts recorded session-wide.
Two consequences, both found by reading the code and confirmed here:
  * a statement from another source was judged against the Teradata ontology's facts — a
    Database Navigator query on PostgreSQL referencing ``public.business`` could be refused
    as a nonexistent database, a column it really has as "does not exist";
  * a MySQL statement (backtick identifiers) parsed as Teradata yielded NOTHING, so every
    check went silent on it; and the empty-result note told the agent to run
    ``SELECT TOP 20 …`` — Teradata syntax — on any database.
P3 adds ``agent/preflight/dialect.py``: the call's dialect and source, set once by the
guarded executor; facts scoped by source (Teradata keeps sharing the ontology's facts, so its
behaviour is unchanged); and a direct source's OWN dictionary arming the checks where there
is no ontology (the Database Navigator's describe and query).

Run: python3 test/test_preflight_dialect.py     (no server, no network)
"""
import ast
import asyncio
import logging
import os
import re
import sqlite3
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
    def run():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            check(f"{fn.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}")
    return run


from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.agent.preflight import (dialect as D, guard as G, catalog as CA,  # noqa: E402
                                                columns as CO, values as V)
go = asyncio.run
PF = os.path.join(ROOT, "src/trusted_data_agent/agent/preflight")
MODES = ("PREFLIGHT_SQL_MODE", "PREFLIGHT_COLUMN_MODE", "PREFLIGHT_VALUE_MODE", "PREFLIGHT_JOIN_MODE",
         "PREFLIGHT_TEXT_DATE_MODE")
_saved = {m: getattr(APP_CONFIG, m, None) for m in MODES}
for m in MODES:
    setattr(APP_CONFIG, m, "enforce")


def reset():
    for mod in (CA, CO, V):
        mod._reset_all()


# ── [1] the dialect context ─────────────────────────────────────────────────────
@guarded
def t_context():
    check("[1] the default dialect is Teradata (callers that set nothing are unchanged)", D.read() == "teradata")
    check("[1] a driver name becomes sqlglot's", D.normalize("postgresql") == "postgres" and D.normalize("MariaDB") == "mysql")
    with D.using("postgresql", "conn:p"):
        check("[1] using() sets dialect and source", D.read() == "postgres" and D.source() == "conn:p")
        check("[1] a non-Teradata source keeps its own facts", D.ns("s1") == "s1::conn:p")
        check("[1] the mapping is idempotent (a check that calls a check maps once)", D.ns(D.ns("s1")) == "s1::conn:p")
        with D.using("sqlite"):
            check("[1] no source → the dialect names the namespace", D.ns("s1") == "s1::sqlite")
    check("[1] using() restores the default", D.read() == "teradata" and D.source() == "")
    check("[1] Teradata shares the session's facts (the ontology's)", D.ns("s1") == "s1")
    with D.using("teradata", "conn:td"):
        check("[1] …a Teradata direct source too", D.ns("s1") == "s1")
    check("[1] the row bound speaks the dialect",
          D.limit_clause(20, "a") == "SELECT TOP 20 a" and D.limit_suffix(20) == "")
    with D.using("duckdb"):
        check("[1] …LIMIT elsewhere", D.limit_clause(20, "a") == "SELECT a" and D.limit_suffix(20) == " LIMIT 20")


# ── [2] a Teradata fact never judges another source ─────────────────────────────
@guarded
def t_scoping():
    reset()
    CA.record_catalog("s2", {"status": "success", "results": [{"DatabaseName": "DWH"}]})
    CO.record_columns("s2", {"DWH.business": ["id", "city"]})
    sql_db = "SELECT * FROM public.business"
    blocked = G.check_sql("base_readQuery", {"sql": sql_db}, "s2")
    check("[2] on Teradata, an unknown database is refused (unchanged)",
          isinstance(blocked, dict) and "does not exist" in blocked.get("error", ""), str(blocked))
    with D.using("postgres", "conn:p"):
        check("[2] the same statement against a PostgreSQL source is NOT judged by Teradata's catalog",
              G.check_sql("database_navigator.query", {"sql": sql_db}, "s2") is None)
    sql_col = "SELECT stars FROM business"
    b = G.check_sql("base_readQuery", {"sql": sql_col}, "s2")
    check("[2] on Teradata, a column the ontology does not list is refused (unchanged)",
          isinstance(b, dict) and b.get("preflight_column") and "the knowledge graph" in b.get("data", ""), str(b))
    with D.using("sqlite", "conn:s"):
        check("[2] …a SQLite source's same-named table is NOT judged by it",
              G.check_sql("database_navigator.query", {"sql": sql_col}, "s2") is None)


# ── [3] parsed in its own dialect ───────────────────────────────────────────────
@guarded
def t_parse():
    sql = "SELECT `city`, `stars` FROM business WHERE `city` = 'Tampa'"
    check("[3] as Teradata, a MySQL statement yields nothing (the pre-P3 silence)", CO.sql_column_refs(sql) == [])
    with D.using("mysql"):
        refs = sorted((r["table"], r["column"]) for r in CO.sql_column_refs(sql))
        lits = [(f["column"], f["literal"]) for f in V.equality_literals(sql)]
    check("[3] in its own dialect its columns are read", refs == [("business", "city"), ("business", "stars")], str(refs))
    check("[3] …and its filter literal", lits == [("city", "Tampa")], str(lits))
    with D.using("postgres"):
        check("[3] write classification follows the dialect (a Postgres data-modifying CTE)",
              CA.sql_is_write("WITH w AS (INSERT INTO t VALUES (1) ON CONFLICT DO NOTHING RETURNING 1) SELECT * FROM w"))
    from trusted_data_agent.dbcatalog.sql_runner import single_select_guard
    from trusted_data_agent.dbcatalog.base import CatalogError
    try:
        single_select_guard("WITH w AS (DELETE FROM t RETURNING 1) SELECT * FROM w", "postgres")
        check("[3] the navigator's guard classifies in the statement's dialect", False)
    except CatalogError:
        check("[3] the navigator's guard classifies in the statement's dialect", True)
    check("[3] …and restores the default afterwards", D.read() == "teradata")
    # the one write verdict the dialect changes: MySQL `SELECT … INTO @var` (a target a read
    # has no business naming) — Teradata's parser missed it, MySQL's sees it
    stmt = "SELECT `a` INTO @v FROM `t`"
    check("[3] under Teradata the MySQL INTO target was invisible", not CA.sql_is_write(stmt))
    try:
        single_select_guard(stmt, "mysql")
        check("[3] under MySQL the navigator refuses the INTO target", False)
    except CatalogError:
        check("[3] under MySQL the navigator refuses the INTO target", True)


# ── [3b] statements that write FILES are writes, in every dialect ────────────────
@guarded
def t_file_writes():
    from trusted_data_agent.dbcatalog.sql_runner import single_select_guard
    from trusted_data_agent.dbcatalog.base import CatalogError
    writes = ["SELECT `a` FROM `t` INTO OUTFILE '/tmp/x'", "SELECT a FROM t INTO DUMPFILE '/tmp/x'",
              "COPY (SELECT 1) TO '/tmp/x'", "SELECT * FROM t; COPY t FROM '/tmp/x'",
              "ATTACH 'x.db' AS x", "EXPORT DATABASE '/tmp/d'", "LOAD DATA INFILE '/f' INTO TABLE t",
              "INSTALL httpfs"]
    for w in writes:
        for d in ("teradata", "mysql", "postgres", "duckdb"):
            with D.using(d):
                ok = CA.sql_is_write(w)
            check(f"[3b] a file write is a write ({d}): {w[:40]}", ok)
    reads = ["SELECT note FROM t WHERE note = 'copy that; attach it'", "SELECT a -- copy\nFROM t",
             "SELECT copy_count, load_time FROM loads", "SELECT 'it''s; export' AS x FROM t"]
    for r in reads:
        check(f"[3b] a read that only mentions the words stays a read: {r[:40]!r}", not CA.sql_is_write(r))
    try:
        single_select_guard("SELECT a FROM t INTO OUTFILE '/tmp/x'", "mysql")
        check("[3b] the navigator refuses SELECT … INTO OUTFILE", False)
    except CatalogError:
        check("[3b] the navigator refuses SELECT … INTO OUTFILE", True)


# ── [4] a direct source's own dictionary arms the checks ────────────────────────
@guarded
def t_dictionary():
    reset()
    with D.using("postgres", "conn:p"):
        n = G.record_source_dictionary("s4", databases=["public", "sales"], table="business", columns=["id", "city", "stars"])
        check("[4] the schema list and the column list are recorded", n == {"databases": 2, "tables": 1}, str(n))
        b = G.check_sql("database_navigator.query", {"sql": "SELECT reviews FROM public.business"}, "s4")
        check("[4] a column the dictionary does not list is refused, naming the dictionary",
              isinstance(b, dict) and b.get("preflight_column") and "the database's own dictionary" in b.get("data", "")
              and "stars" in b.get("data", ""), str(b)[:300])
        b = G.check_sql("database_navigator.query", {"sql": "SELECT city FROM archive.business"}, "s4")
        check("[4] a schema the source does not have is refused", isinstance(b, dict) and "archive" in b.get("error", ""), str(b))
        check("[4] a real column in a real schema passes",
              G.check_sql("database_navigator.query", {"sql": "SELECT city, stars FROM public.business"}, "s4") is None)
    check("[4] those facts are invisible to a Teradata call in the same session",
          G.check_sql("base_readQuery", {"sql": "SELECT reviews FROM business"}, "s4") is None)
    with D.using("teradata", "conn:td"):
        n = G.record_source_dictionary("s4", databases=["x"], table="business", columns=["id"])
    check("[4] a Teradata source never records dictionary facts over the ontology's", n == {"databases": 0, "tables": 0})
    check("[4] …so the Teradata namespace stayed empty", CO.known_tables("s4") == {} and not CA.has_catalog("s4"))


# ── [5] learned values are scoped; the note speaks the dialect ──────────────────
@guarded
def t_values():
    reset()
    with D.using("postgres", "conn:p"):
        G.maybe_learn_values("database_navigator.query", {"sql": "SELECT city FROM business"},
                             {"status": "success", "results": [{"city": "Tampa"}, {"city": "Reno"}]}, "s5")
        note = G.empty_result_note("database_navigator.query", {"sql": "SELECT COUNT(*) AS n FROM business WHERE city = 'Atlantis'"},
                                   {"status": "success", "results": [{"n": 0}]}, "s5")
        check("[5] an empty result on an unseen value gets the note", note is not None, str(note))
        check("[5] …whose probe is PostgreSQL, not Teradata",
              note and "LIMIT 20" in note["note"] and "TOP 20" not in note["note"], note and note["note"][-240:])
        check("[5] a value the source's own rows showed is not questioned",
              G.empty_result_note("database_navigator.query", {"sql": "SELECT COUNT(*) AS n FROM business WHERE city = 'Tampa'"},
                                  {"status": "success", "results": [{"n": 0}]}, "s5") is None)
    check("[5] PostgreSQL's learned values did not arm the Teradata namespace",
          G.empty_result_note("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM business WHERE city = 'Atlantis'"},
                              {"status": "success", "results": [{"n": 0}]}, "s5") is None)
    V.observe_values("s5", "business", "city", ["Tampa"])
    note = G.empty_result_note("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM business WHERE city = 'Atlantis'"},
                               {"status": "success", "results": [{"n": 0}]}, "s5")
    check("[5] on Teradata the probe still reads TOP (unchanged)", note and "SELECT TOP 20" in note["note"])


# ── [6] the guarded executor sets the context for the whole call ────────────────
@guarded
def t_guarded():
    reset()
    from trusted_data_agent.agent.tool_enforcement import guarded_sql_call
    CA.record_catalog("s6", {"status": "success", "results": [{"DatabaseName": "DWH"}]})
    seen = {}

    async def execute(args):
        seen["dialect"], seen["ns"] = D.read(), D.ns("s6")
        return {"status": "success", "results": [{"n": 0}], "columns": ["n"]}
    out = go(guarded_sql_call("database_navigator.query", {"sql": "SELECT COUNT(*) AS n FROM public.business WHERE city = 'X'"},
                              execute, session_id="s6", dialect="postgresql", source_id="conn:p"))
    check("[6] a PostgreSQL call is not refused by the Teradata catalog", out["outcome"] == "success", str(out.get("result"))[:200])
    check("[6] the executor ran inside the call's dialect and source", seen == {"dialect": "postgres", "ns": "s6::conn:p"}, str(seen))
    check("[6] the context is released after the call", D.read() == "teradata" and D.source() == "")
    out = go(guarded_sql_call("database_navigator.query", {"sql": "SELECT COUNT(*) AS n FROM public.business"},
                              execute, session_id="s6"))
    check("[6] the default (Teradata) call is refused exactly as before", out["outcome"] == "refused")


# ── [7] the navigator, end to end on SQLite ─────────────────────────────────────
@guarded
def t_navigator():
    reset()
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "shop.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE business (id INTEGER, city TEXT, stars REAL)")
    c.executemany("INSERT INTO business VALUES (?,?,?)", [(i, "Tampa" if i % 2 else "Reno", 4.0) for i in range(20)])
    c.commit()
    c.close()

    class Store:
        conns = [{"id": "c1", "name": "shop", "driver": "sqlite", "database": db, "is_default": True}]
        def list(self, u): return self.conns
        def resolve(self, u, i): return next((dict(x) for x in self.conns if x["id"] == i), None)

    from trusted_data_agent.dbcatalog import service as svcmod, cache as CH
    from trusted_data_agent.components.manager import get_component_manager
    svcmod._SERVICE = svcmod.NavigatorService(store=Store(), cache=CH.CatalogCache())
    h = get_component_manager().get_component("database_navigator").handler
    prof = {"id": "p", "profile_type": "llm_only",
            "componentConfig": {"database_navigator": {"enabled": True, "query": {"enabled": True}}}}
    ctx = {"user_uuid": "u", "profile_id": "p", "profile_config": prof, "session_id": "s7",
           "STATE": {"structured_tools": {}, "mcp_tools": {}}}
    s = go(h.process({"action": "query", "sql": "SELECT reviews FROM business"}, ctx)).spec
    check("[7] before describe, an unknown column reaches the database (no facts yet)",
          s.get("error") == "query_failed" and "reviews" in s.get("message", ""), str(s.get("message"))[:200])
    go(h.process({"action": "describe", "database": "main", "object_name": "business"}, ctx))
    with D.using("sqlite", "conn:c1"):
        check("[7] describe recorded the dictionary's column list for this source",
              CO.known_tables(D.ns("s7")).get("business") == {"id", "city", "stars"}, str(CO.known_tables(D.ns("s7"))))
    s = go(h.process({"action": "query", "sql": "SELECT reviews FROM business"}, ctx)).spec
    check("[7] after describe the same statement is refused before it runs, naming the real columns",
          s.get("error") == "refused" and "the database's own dictionary" in s.get("message", "")
          and "city" in s.get("message", ""), str(s.get("message"))[:300])
    s = go(h.process({"action": "query", "sql": "SELECT COUNT(*) AS n FROM business WHERE city = 'Atlantis'"}, ctx)).spec
    check("[7] a zero on an unseen value carries a SQLite probe",
          not s.get("error") and any("LIMIT 20" in n and "TOP" not in n for n in (s.get("notes") or [])), str(s.get("notes")))
    s = go(h.process({"action": "query", "sql": "SELECT city, COUNT(*) AS n FROM business GROUP BY city ORDER BY city"}, ctx)).spec
    check("[7] a correct statement runs", not s.get("error") and s.get("rows") == [["Reno", 10], ["Tampa", 10]], str(s.get("rows")))


# ── [8] structure: every analysis parse follows the dialect; rewrites stay Teradata ─
@guarded
def t_structure():
    analysis = {"catalog.py": ["extract_sql_db_refs", "sql_is_write"], "columns.py": ["sql_column_refs"],
                "joins.py": ["sql_joins", "sql_scope_tables"], "values.py": ["_parse"], "textdates.py": ["_parse"]}
    for f, fns in analysis.items():
        src = open(os.path.join(PF, f)).read()
        tree = ast.parse(src)
        for fn in fns:
            node = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn), None)
            body = ast.get_source_segment(src, node) if node else ""
            check(f"[8] {f}:{fn} parses in the call's dialect",
                  node is not None and "_dialect.read()" in body and 'read="teradata"' not in body, fn)
    g = open(os.path.join(PF, "guard.py")).read()
    tree = ast.parse(g)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and any(a.arg == "session_id" for a in n.args.args):
            seg = ast.get_source_segment(g, n)
            check(f"[8] guard.{n.name} reads the facts of THIS call's source", "_dialect.ns(session_id)" in seg)
    te = open(os.path.join(ROOT, "src/trusted_data_agent/agent/tool_enforcement.py")).read()
    check("[8] the Teradata-only rewrites stay gated on the call's dialect",
          "if lane.mechanical_fixes and dialect == _TERADATA:" in te.split("def sql_gate_before")[1].split("\ndef ")[0]
          and "if dialect == _TERADATA:\n        before_sql" in te)
    check("[8] the guarded executor wraps the whole call in its dialect",
          "with _pd.using(dialect, source_id):" in te)


for t in (t_context, t_scoping, t_parse, t_file_writes, t_dictionary, t_values, t_guarded, t_navigator, t_structure):
    t()
for m, v in _saved.items():
    setattr(APP_CONFIG, m, v)
reset()
print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)  — each statement is checked as what it is, against its own source")
sys.exit(1 if FAIL else 0)
