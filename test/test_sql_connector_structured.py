#!/usr/bin/env python3
"""The shared SQL executor returns structured, bounded rows — and reads read-only on request.

WHY THIS GATE EXISTS
The Canvas SQL connector is the platform's one non-Teradata executor; the Database
Navigator's `sql_runner` delegates to it. It returned results as TAB-SEPARATED TEXT
(what the Canvas displays), and the runner parsed that text back into rows. Found
2026-09-24 on DataAgentBench's googlelocal reviews: a query returning 3 rows came back
as 7 — any value containing a newline or a tab split into extra rows or shifted
columns, and every value became a string. The connector also fetched the WHOLE result
before the runner truncated, so a SELECT * over a large table loaded it all into memory.

Now: the connector carries `columns` / `rows` (typed values) beside the unchanged display
text; `max_rows` fetches at most that many (+1 to detect truncation); `readonly` opens the
connection read-only at the DATABASE level — defence in depth behind the SELECT-only guard.
The Canvas path (no max_rows, no readonly) is byte-identical.

Run: python3 test/test_sql_connector_structured.py     (no server, no network)
"""
import asyncio
import logging
import os
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


from components.builtin.canvas.connectors.sql import SQLNativeConnector  # noqa: E402
from trusted_data_agent.dbcatalog.sql_runner import run_rows  # noqa: E402
from trusted_data_agent.dbcatalog.generic_provider import GenericCatalogProvider  # noqa: E402
from trusted_data_agent.dbcatalog.base import CatalogError  # noqa: E402

tmp = tempfile.mkdtemp()
DB = os.path.join(tmp, "t.db")
con = sqlite3.connect(DB)
con.execute("CREATE TABLE review (id INTEGER, rating REAL, text TEXT)")
con.executemany("INSERT INTO review VALUES (?,?,?)", [
    (1, 4.5, "great\nplace"), (2, 3.0, "tab\there"), (3, 5.0, "plain"),
    *[(i, 1.0, f"r{i}") for i in range(4, 104)],
])
con.commit()
con.close()
CREDS = {"driver": "sqlite", "database": DB}
conn = SQLNativeConnector()
go = asyncio.run

# ── [1] the Canvas path is unchanged (display text), and now also structured
r = go(conn.execute("SELECT id, text FROM review WHERE id <= 3 ORDER BY id", CREDS))
check("[1] display text is still produced", isinstance(r.result, str) and r.result.startswith("id\ttext"))
check("[1] structured columns", r.columns == ["id", "text"], str(r.columns))
check("[1] structured rows keep a newline / tab inside ONE value",
      r.rows == [[1, "great\nplace"], [2, "tab\there"], [3, "plain"]], str(r.rows))
check("[1] no row bound on the Canvas path", r.truncated is False and r.row_count == 3)

# ── [2] the runner reads the structured result: 3 rows stay 3, values keep their type
cols, rows = go(run_rows(CREDS, "SELECT id, rating, text FROM review WHERE id <= 3 ORDER BY id", 0))
check("[2] a value with a newline no longer splits into extra rows", len(rows) == 3, str(len(rows)))
check("[2] numbers stay numbers", rows[0][0] == 1 and rows[0][1] == 4.5, str(rows[0]))

# ── [3] max_rows bounds the fetch and reports truncation
r = go(conn.execute("SELECT id FROM review ORDER BY id", CREDS, max_rows=10))
check("[3] at most max_rows rows are returned", len(r.rows) == 10, str(len(r.rows)))
check("[3] truncation is reported", r.truncated is True)
r = go(conn.execute("SELECT id FROM review WHERE id <= 3", CREDS, max_rows=10))
check("[3] a result under the bound is not truncated", len(r.rows) == 3 and r.truncated is False)
cols, rows = go(run_rows(CREDS, "SELECT id FROM review", 7))
check("[3] the runner passes the bound through", len(rows) == 7, str(len(rows)))

# ── [4] a zero-row SELECT still names its columns
r = go(conn.execute("SELECT id, text FROM review WHERE id < 0", CREDS, max_rows=5))
check("[4] zero rows, columns known", r.columns == ["id", "text"] and r.rows == [], f"{r.columns} {r.rows}")

# ── [5] readonly holds at the database level
r = go(conn.execute("PRAGMA user_version = 7", CREDS, readonly=True))
check("[5] a writing PRAGMA on a read-only connection is refused by the database",
      bool(r.error) and "readonly" in r.error.replace("-", "").lower(), str(r.error))
check("[5] …and nothing changed", sqlite3.connect(DB).execute("PRAGMA user_version").fetchone()[0] == 0)
r = go(conn.execute("DELETE FROM review", CREDS, readonly=True))
check("[5] a DELETE on a read-only connection is refused by the database", bool(r.error))
check("[5] …and every row is still there", sqlite3.connect(DB).execute("SELECT COUNT(*) FROM review").fetchone()[0] == 103)

# ── [6] the provider's agent entry point: guard first, then a bounded read-only read
p = GenericCatalogProvider("conn:t", "t", CREDS)
cols, rows = go(p.run_select("SELECT id, text FROM review WHERE id <= 3 ORDER BY id", 5))
check("[6] run_select returns the rows", len(rows) == 3 and cols == ["id", "text"])
for bad in ("DELETE FROM review", "SELECT 1; DROP TABLE review", "UPDATE review SET rating = 0"):
    try:
        go(p.run_select(bad, 5))
        check(f"[6] refused: {bad}", False, "not refused")
    except CatalogError:
        check(f"[6] refused: {bad}", True)
try:
    go(p.run_select("PRAGMA user_version = 9", 5))
    check("[6] a writing PRAGMA through run_select is refused", False, "not refused")
except CatalogError as e:
    check("[6] a writing PRAGMA through run_select is refused (by the database: read-only)", "readonly" in str(e).replace("-", "").lower(), str(e))
check("[6] the database is unchanged", sqlite3.connect(DB).execute("PRAGMA user_version").fetchone()[0] == 0)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — structured, bounded, read-only on request"))
sys.exit(1 if FAIL else 0)
