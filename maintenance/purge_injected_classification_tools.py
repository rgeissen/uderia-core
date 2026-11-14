#!/usr/bin/env python3
"""Remove runtime-injected TDA_* tools from persisted classification snapshots.

WHY
`classification_results` is the record of the MCP tool/prompt surface a profile
can reach. The adapter is explicit that platform tools must never enter it:

    "CLIENT_SIDE_TOOLS (TDA_*) are always-active platform tools, not MCP server
     tools. They must not be sent to the LLM for classification or stored in
     classification_results."

They leaked in anyway. `load_profile_classification_into_state` assigned the
stored dicts straight onto APP_STATE, and because `config_manager.load_config`
serves an in-memory cache those dicts were the SAME OBJECTS as the persisted
profile. The loader then injected two categories into them —

    APP_STATE['structured_tools']["System Tools"]     = ...   # 7 framework tools
    APP_STATE['structured_tools']["Component Tools"]  = ...   # component tools

— mutating the record, and the next save of that user's config wrote them to
disk. Measured live 2026-09-08: two profiles on the SAME MCP server, classified
two seconds apart, persisted 63 vs 48 tools (delta = exactly those two
categories).

The DEPLOYMENT half of the trust envelope hashes this snapshot
(`trust.deployment.catalog_from_classification`), so an agent whose snapshot
gained or lost the injected block read `changed` with
"tools removed: TDA_Canvas, TDA_Charting, …" over a drift that was never a
deployment change. It hit Optimize agents hardest because their activation path
differs from the Ideate one, so the two classes disagreed about the same server.

The leak itself is fixed at the root (the loader now deep-copies). This script
repairs snapshots written BEFORE that fix.

WHAT IT DOES
Deletes the "System Tools" and "Component Tools" categories from every stored
`classification_results.tools`. Nothing else is touched. The runtime is
unaffected: both categories are re-injected into APP_STATE on every activation,
so removing them from the record changes what is REMEMBERED, never what runs.

REFUSALS
A profile whose snapshot would be left with no tools AND no prompts is SKIPPED
and reported: an empty snapshot reads as "never classified" to the loader
(`total_tools == 0 → return False`), which would silently demand a fresh
classification. Correcting the record must not disarm the profile.

TRUST
An agent already attested over a polluted snapshot will read `changed` after the
repair — honestly, because the record did change. Re-attest it (Trust → attest
deployment). That is a one-time cost: with the root fix in place the snapshot no
longer moves on its own.

STOP THE SERVER FIRST (or restart it immediately after)
`config_manager.load_config` serves an in-memory cache, so a running instance holds
the PRE-repair profiles. This script edits the database directly; the next save of a
cached user config — any profile write, `set_active_for_consumption` included — writes
the stale, still-polluted copy straight back over the repair. Measured 2026-09-08: a
repair applied under a live server was undone by the very next activation call.

    docker stop <container>   # or stop the local process
    python maintenance/purge_injected_classification_tools.py --apply
    docker start <container>

    python maintenance/purge_injected_classification_tools.py            # report
    python maintenance/purge_injected_classification_tools.py --apply
    python maintenance/purge_injected_classification_tools.py --db /path/tda_auth.db --apply
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

#: The two categories the runtime injects. Deliberately a literal pair rather
#: than "anything starting with TDA_": a category is the unit the loader writes,
#: and matching on a name prefix would also strip a genuine MCP tool that merely
#: happens to be called TDA_something.
INJECTED = ("System Tools", "Component Tools")


def _entries(section) -> int:
    if isinstance(section, dict):
        return sum(len(v) for v in section.values() if isinstance(v, list))
    if isinstance(section, list):
        return len(section)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "tda_auth.db"))
    ap.add_argument("--apply", action="store_true",
                    help="write the repair (a timestamped backup is taken first)")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"no such database: {db}")
        return 2

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT user_id, preferences_json FROM user_preferences").fetchall()

    repaired, skipped, updates = [], [], {}
    for uid, pj in rows:
        try:
            prefs = json.loads(pj or "{}")
        except Exception:
            continue
        dirty = False
        for p in prefs.get("profiles", []):
            cr = p.get("classification_results")
            if not isinstance(cr, dict):
                continue
            tools = cr.get("tools")
            if not isinstance(tools, dict):
                continue
            present = [k for k in INJECTED if k in tools]
            if not present:
                continue
            kept = {k: v for k, v in tools.items() if k not in INJECTED}
            if _entries(kept) == 0 and _entries(cr.get("prompts")) == 0:
                skipped.append((uid, p.get("tag"), present,
                                "would leave the snapshot empty"))
                continue
            removed = sum(len(tools[k]) for k in present if isinstance(tools[k], list))
            cr["tools"] = kept
            dirty = True
            repaired.append((uid, p.get("tag"), p.get("profile_type"), present, removed))
        if dirty:
            updates[uid] = json.dumps(prefs)

    print(f"database: {db}")
    print(f"profiles to repair: {len(repaired)}   skipped: {len(skipped)}")
    for uid, tag, ptype, present, removed in sorted(repaired, key=lambda r: (r[0], str(r[1]))):
        print(f"  user={uid[:8]} @{tag!s:14s} {ptype!s:14s} drop {present} (-{removed} tools)")
    for uid, tag, present, why in skipped:
        print(f"  SKIP user={uid[:8]} @{tag!s:14s} {present} — {why}")

    if not repaired:
        print("\nnothing to do.")
        return 0
    if not args.apply:
        print("\ndry run — re-run with --apply to write.")
        return 0

    backup = db.with_name(f"{db.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(db, backup)
    print(f"\nbackup: {backup}")
    for uid, blob in updates.items():
        conn.execute("UPDATE user_preferences SET preferences_json = ? WHERE user_id = ?",
                     (blob, uid))
    conn.commit()
    print(f"applied to {len(updates)} user record(s), {len(repaired)} profile(s).")
    print("Re-attest any agent that now reads `changed` — the record was corrected.")
    print("RESTART the instance if it was running: it caches user configs in memory and "
          "the next profile save would write the pre-repair copy back over this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
