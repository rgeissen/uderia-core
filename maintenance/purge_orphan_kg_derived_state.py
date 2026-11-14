#!/usr/bin/env python3
"""Purge derived KG state whose knowledge graph no longer exists.

WHY
A knowledge graph owns rows in tables keyed only on its `kg_id` — its version
history (`kg_versions`) and its retrieval telemetry (`kg_entity_usage`). Every
`DELETE FROM kg_metadata` site now purges both through ONE registry
(`core.kg_derived_state.purge_for_kg`), but rows written BEFORE that fix are still
there: on production, 2026-09-15, `kg_entity_usage` held 8,879 rows across 27
graphs, several of which had been uninstalled. Such rows are unreachable (every
reader scopes by a live kg_id), invisible on every surface, and grow forever.

WHAT IT DOES
For each table the registry names, finds rows whose `kg_id` has NO `kg_metadata`
row and deletes them. The orphan query is written as `NOT EXISTS (SELECT 1 FROM
kg_metadata ...)` on purpose: scoping the count THROUGH kg_metadata returns 0
exactly when the orphan exists (the shape that hid the versions orphans for
months — see test_kg_derived_state_cleanup).

A released "shadow" graph (`<kg_id>::released`, Model B) has its own kg_metadata
row and is therefore NOT an orphan; it dies with its live graph through the
normal deletion path.

SAFETY
Dry-run by default (reports only). `--apply` writes a timestamped backup of the
database next to it FIRST, then deletes. Only rows with no owning kg_metadata
row are ever touched; a table absent from the database is skipped. Safe to run
against a live instance: nothing reads or caches these rows by kg_id once the
graph is gone.

    python maintenance/purge_orphan_kg_derived_state.py                 # report
    python maintenance/purge_orphan_kg_derived_state.py --apply
    python maintenance/purge_orphan_kg_derived_state.py --db /path/tda_auth.db --apply
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def derived_tables():
    """The registry decides what "derived state" is — never a copy of the list here."""
    from trusted_data_agent.core.kg_derived_state import derived_tables as _dt
    return _dt()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def find_orphans(conn: sqlite3.Connection) -> Dict[str, Dict[str, int]]:
    """{table: {kg_id: row_count}} for every derived row whose graph is gone."""
    out: Dict[str, Dict[str, int]] = {}
    if not _table_exists(conn, "kg_metadata"):
        return out
    for table in derived_tables():
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT t.kg_id, COUNT(*) FROM {table} t "
            f"WHERE NOT EXISTS (SELECT 1 FROM kg_metadata m WHERE m.kg_id = t.kg_id) "
            f"GROUP BY t.kg_id ORDER BY COUNT(*) DESC"
        ).fetchall()
        if rows:
            out[table] = {kg_id: int(n) for kg_id, n in rows}
    return out


def purge(conn: sqlite3.Connection, orphans: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    """Delete exactly the orphaned rows reported by `find_orphans`. Returns rows per table."""
    removed: Dict[str, int] = {}
    for table, by_kg in orphans.items():
        n = 0
        for kg_id in by_kg:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE kg_id = ? "
                f"AND NOT EXISTS (SELECT 1 FROM kg_metadata m WHERE m.kg_id = ?)",
                (kg_id, kg_id),
            )
            n += cur.rowcount
        removed[table] = n
    conn.commit()
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--db", default=str(ROOT / "tda_auth.db"), help="path to tda_auth.db")
    ap.add_argument("--apply", action="store_true", help="delete (default: report only)")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: no database at {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(db), timeout=30)  # a live writer may hold the lock briefly
    try:
        orphans = find_orphans(conn)
        total = sum(sum(v.values()) for v in orphans.values())
        print(f"database: {db}")
        print(f"derived tables: {', '.join(derived_tables())}")
        if not orphans:
            print("no orphaned derived state — every row belongs to a live knowledge graph")
            return 0
        for table, by_kg in orphans.items():
            print(f"\n{table}: {sum(by_kg.values())} orphaned row(s) across {len(by_kg)} deleted graph(s)")
            for kg_id, n in list(by_kg.items())[:20]:
                print(f"    {kg_id}  {n}")
            if len(by_kg) > 20:
                print(f"    ... {len(by_kg) - 20} more")
        if not args.apply:
            print(f"\nDRY RUN — {total} row(s) would be removed. Re-run with --apply.")
            return 0
        backup = db.with_name(f"{db.name}.bak-orphan-kg-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(db, backup)
        print(f"\nbackup written: {backup}")
        removed = purge(conn, orphans)
        print("removed: " + ", ".join(f"{t}={n}" for t, n in removed.items()))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
