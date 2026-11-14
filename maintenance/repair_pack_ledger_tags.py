#!/usr/bin/env python3
"""Repair agent-pack ledger rows whose recorded tag disagrees with the live profile.

Root cause (Sep 2026): the fresh-install path recorded profile ids in CREATION order
(non-genie first, coordinator last) zipped against tags in MANIFEST order, so any
manifest not listing its coordinator last mislabelled two profiles in
`agent_pack_resources`. An update_in_place then wrote each manifest entry onto the
wrong profile. The import is fixed; this script heals installations recorded before
the fix, using the SAME rule the importer now applies (`_heal_ledger_tags`):
the live tag is adopted only when it is one the pack's manifest ships.

Dry-run by default; `--apply` writes. Every change is printed.
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import sqlite_util
from trusted_data_agent.core.agent_pack_manager import AgentPackManager


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("TDA_AUTH_DB", "tda_auth.db"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = sqlite_util.connect(a.db)
    conn.row_factory = __import__("sqlite3").Row
    live = {}
    for uid, pj in conn.execute("SELECT user_id, preferences_json FROM user_preferences"):
        try:
            live[uid] = (json.loads(pj) or {}).get("profiles") or []
        except Exception:
            live[uid] = []
    changed = 0
    for inst in conn.execute("SELECT id, name, owner_user_id, manifest_json FROM agent_pack_installations"):
        try:
            tags = {p.get("tag") for p in (json.loads(inst["manifest_json"] or "{}").get("profiles") or [])}
        except Exception:
            tags = set()
        rows = [dict(r) for r in conn.execute(
            "SELECT id, resource_type, resource_id, resource_tag, resource_role FROM agent_pack_resources "
            "WHERE pack_installation_id = ?", (inst["id"],))]
        healed, notes = AgentPackManager._heal_ledger_tags(rows, live.get(inst["owner_user_id"], []), tags)
        if not notes:
            continue
        print(f"installation {inst['id']} ({inst['name']}, owner {inst['owner_user_id'][:8]}):")
        for n in notes:
            print("   ", n)
        coord = None
        for r in healed:
            if r["resource_type"] == "profile":
                if r["resource_role"] == "coordinator":
                    coord = (r["resource_tag"], r["resource_id"])
                if a.apply:
                    conn.execute("UPDATE agent_pack_resources SET resource_tag=?, resource_role=? WHERE id=?",
                                 (r["resource_tag"], r["resource_role"], r["id"]))
        if a.apply and coord:
            conn.execute("UPDATE agent_pack_installations SET coordinator_tag=?, coordinator_profile_id=? WHERE id=?",
                         (coord[0], coord[1], inst["id"]))
        changed += 1
    if a.apply:
        conn.commit()
    print(f"{changed} installation(s) {'repaired' if a.apply else 'would be repaired (dry run; pass --apply)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
