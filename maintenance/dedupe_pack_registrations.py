#!/usr/bin/env python3
"""Remove SUPERSEDED agent-pack registrations (duplicate ledger rows, one pack).

Why this exists
---------------
`create_and_install` — the publisher-registration path behind `build.py --register`
— used to INSERT a fresh `agent_pack_installations` row on every run. One set of
agents then ended up claimed by N ledger rows, and every profile/collection card
rendered N identical pack chips for a pack that was installed once.

Registration is idempotent now (`_find_own_registration`), so no NEW duplicates
appear. This script clears the ones already recorded.

What counts as a duplicate (all four must hold — anything else is left alone)
----------------------------------------------------------------------------
  1. same owner, same pack name as the row being kept;
  2. it OWNS nothing (`is_owned = 0` on every resource) — a row that created
     profiles is a real installation, never a bookkeeping artifact;
  3. every resource it claims is also claimed by the kept row — i.e. it is a
     strict subset, so deleting it removes no information;
  4. no marketplace listing points at it (`source_installation_id`).

The kept row is the marketplace listing's source when there is one, else the
newest. Dry-run by default; `--apply` writes, after a timestamped DB backup.

    python maintenance/dedupe_pack_registrations.py            # report only
    python maintenance/dedupe_pack_registrations.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "tda_auth.db"


def _resources(conn, inst_id: int) -> set[tuple[str, str]]:
    return {(r["resource_type"], str(r["resource_id"])) for r in conn.execute(
        "SELECT resource_type, resource_id FROM agent_pack_resources WHERE pack_installation_id = ?",
        (inst_id,))}


def _owns_anything(conn, inst_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) n FROM agent_pack_resources WHERE pack_installation_id = ? AND is_owned = 1",
        (inst_id,)).fetchone()
    return bool(row["n"])


def plan(conn) -> list[dict]:
    """Pure: what would be removed, and why each survivor was chosen."""
    listing_sources = {int(r["source_installation_id"]) for r in conn.execute(
        "SELECT source_installation_id FROM marketplace_agent_packs "
        "WHERE source_installation_id IS NOT NULL")}

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in conn.execute("SELECT * FROM agent_pack_installations ORDER BY id"):
        groups.setdefault((row["owner_user_id"], row["name"]), []).append(row)

    out = []
    for (owner, name), rows in groups.items():
        if len(rows) < 2:
            continue
        published = [r for r in rows if r["id"] in listing_sources]
        keep = published[0] if published else max(rows, key=lambda r: r["id"])
        keep_res = _resources(conn, keep["id"])
        for cand in rows:
            if cand["id"] == keep["id"]:
                continue
            res = _resources(conn, cand["id"])
            reasons = []
            if cand["id"] in listing_sources:
                reasons.append("a marketplace listing points at it")
            if _owns_anything(conn, cand["id"]):
                reasons.append("it OWNS resources (a real installation)")
            if not res <= keep_res:
                reasons.append(f"it claims {len(res - keep_res)} resource(s) the kept row does not")
            out.append({
                "owner": owner, "name": name,
                "remove": cand["id"], "remove_installed_at": cand["installed_at"],
                "keep": keep["id"], "keep_installed_at": keep["installed_at"],
                "keep_reason": "marketplace listing source" if published else "newest",
                "resources": len(res), "blocked_by": reasons,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"database not found: {db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        items = plan(conn)
        if not items:
            print("No duplicate pack registrations found.")
            return 0

        removable = [i for i in items if not i["blocked_by"]]
        blocked = [i for i in items if i["blocked_by"]]

        print(f"Duplicate registrations found: {len(items)} "
              f"({len(removable)} removable, {len(blocked)} left alone)\n")
        for i in items:
            verdict = "REMOVE" if not i["blocked_by"] else "KEEP  "
            print(f"  [{verdict}] '{i['name']}' installation {i['remove']} "
                  f"({i['remove_installed_at']}, {i['resources']} resources)")
            print(f"            superseded by {i['keep']} ({i['keep_reason']}, {i['keep_installed_at']})")
            for r in i["blocked_by"]:
                print(f"            left alone: {r}")

        if not args.apply:
            print("\nDry run — nothing written. Re-run with --apply to remove the rows marked REMOVE.")
            return 0
        if not removable:
            print("\nNothing removable.")
            return 0

        backup = db.with_suffix(f".backup-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(db, backup)
        print(f"\nBackup written: {backup}")

        for i in removable:
            conn.execute("DELETE FROM agent_pack_resources WHERE pack_installation_id = ?", (i["remove"],))
            conn.execute("DELETE FROM agent_pack_installations WHERE id = ?", (i["remove"],))
            print(f"  removed installation {i['remove']} ('{i['name']}')")
        conn.commit()
        print(f"\nDone — {len(removable)} superseded registration(s) removed. "
              f"No profile, collection, graph or skill was touched.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
