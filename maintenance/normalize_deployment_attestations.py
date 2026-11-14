#!/usr/bin/env python3
"""Normalize deployment attestations that were taken over a polluted classification record.

WHY
The DEPLOYMENT half of an agent's trust envelope hashes the profile's persisted
classification snapshot — the MCP tool/prompt surface the agent can reach. For a
while that record also carried the platform's own runtime-injected categories
("System Tools" = the TDA_* framework tools, "Component Tools" = the action
components), leaked into it by readers that enriched a live reference and by a
save path that persisted APP_STATE as-is. Those are platform tools, not the
agent's MCP surface, and since 2026-09-22 the reader
(`trust.deployment.catalog_from_classification`) ignores them.

An attestation taken while the record was polluted therefore holds a catalog
with platform names in it. Against the corrected reader it reads 🟠 `changed`
with drift such as "tools removed: TDA_Canvas, TDA_Charting, …" — a drift that
was never a deployment change (measured 2026-09-22: 10 of 57 production
attestations). This script rewrites exactly those attestations to the catalog the
corrected reader computes — and ONLY when the sole difference between what was
attested and what is live is platform tools in the catalog. Any other change
(model, connectors, a real MCP tool appearing) is genuine drift and is left for
the owner to re-attest.

WHAT IT CHANGES
For each qualifying `agent_deployment` attestation: the stored signature's
catalog loses its platform tool names, `version_hash` is recomputed over that
signature, and the row is re-signed with this instance's provenance key. The
original signer and signing time are PRESERVED (the owner's statement of intent
is unchanged — only the platform noise it never chose is removed). A note is
appended to the verdict so the rewrite is visible in the record.

Nothing in `user_preferences` is touched, so it is safe against a running
server (the config cache is not involved). A timestamped backup of the DB is
taken before any write.

USAGE (inside the container, where the platform registries load):
    docker cp maintenance/normalize_deployment_attestations.py <c>:/tmp/n.py
    docker exec -w /app <c> python3 /tmp/n.py            # dry run (default)
    docker exec -w /app <c> python3 /tmp/n.py --apply
Locally:  PYTHONPATH=src python3 maintenance/normalize_deployment_attestations.py [--db PATH] [--apply]
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
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.core import sqlite_util  # noqa: E402
from trusted_data_agent.core.trust import deployment as dep, store  # noqa: E402
from trusted_data_agent.core.config_manager import get_config_manager  # noqa: E402


def _platform_tool_names() -> set:
    """Every tool name the platform injects at runtime — never part of an MCP surface."""
    from trusted_data_agent.mcp_adapter.adapter import CLIENT_SIDE_TOOLS
    names = {t["name"] for t in CLIENT_SIDE_TOOLS}
    try:
        from trusted_data_agent.components.manager import get_component_manager
        names |= {t["name"] for t in get_component_manager().get_tool_definitions({})}
    except Exception as e:  # noqa: BLE001
        print(f"warning: component tool names unavailable ({e}); framework tools only")
    return names


def _bare(entry: str) -> str:
    return entry.split("(", 1)[0]


def _owners(con) -> dict:
    out: dict = {}
    for uid, pj in con.execute("SELECT user_id, preferences_json FROM user_preferences"):
        for p in (json.loads(pj or "{}").get("profiles") or []):
            if p.get("id"):
                out.setdefault(p["id"], []).append(uid)
    return out


def classify(prior: dict, live: dict, platform: set):
    """Return ("normalize", live_signature) when the ONLY difference is platform tool
    names in the catalog; ("consistent", None) when nothing differs; else ("drift", why)."""
    if prior == live:
        return "consistent", None
    p_cat, l_cat = prior.get("catalog"), live.get("catalog")
    if not isinstance(p_cat, dict) or not isinstance(l_cat, dict):
        return "drift", "catalog unresolved on one side"
    rest_p = {k: v for k, v in prior.items() if k != "catalog"}
    rest_l = {k: v for k, v in live.items() if k != "catalog"}
    if rest_p != rest_l:
        return "drift", "a surface other than the catalog differs (model / connectors / bindings)"
    if set(p_cat.get("prompts") or []) != set(l_cat.get("prompts") or []):
        return "drift", "prompt catalog differs"
    before, after = set(p_cat.get("tools") or []), set(l_cat.get("tools") or [])
    moved = (before ^ after)
    if not moved:
        return "drift", "catalogs equal but signatures differ (unexpected)"
    foreign = sorted(_bare(t) for t in moved if _bare(t) not in platform)
    if foreign:
        return "drift", f"genuine MCP tool change: {', '.join(foreign[:6])}"
    return "normalize", live


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=None, help="auth DB path (default: the instance's)")
    ap.add_argument("--apply", action="store_true", help="write the rewrite (backup taken first)")
    args = ap.parse_args()

    db = store._db_path(args.db)
    platform = _platform_tool_names()
    cm = get_config_manager()
    todo, drift, consistent = [], [], 0
    with sqlite_util.connect(db) as con:
        owners = _owners(con)
        users = {r[0]: r[1] for r in con.execute("SELECT id, username FROM users")}
    # Read through the store so an older DB is migrated to owner-scoped rows first
    # (PROP-009): a deployment attestation belongs to ONE account, its owner_uuid.
    rows = [(r["artifact_id"], r["version_hash"], r.get("verdict_json"), r.get("signer_uuid"),
             r.get("signed_at"), r.get("owner_uuid"))
            for r in store.list_current(db_path=db, kinds=[dep.DEPLOYMENT_KIND])]
    for pid, vh, vj, signer, signed_at, row_owner in rows:
        verdict = json.loads(vj or "{}") or {}
        prior = verdict.get("signature") or {}
        for uid in ([row_owner] if row_owner else owners.get(pid, [])):
            prof = cm.get_profile(pid, uid)
            if not prof:
                continue
            live = dep.deployment_signature(prof, uid, db_path=db)
            kind, payload = classify(prior, live, platform)
            label = f"{users.get(uid, uid)[:18]:18} {str(prof.get('tag', '?')):10} {pid}"
            if kind == "consistent":
                consistent += 1
            elif kind == "normalize":
                todo.append((pid, uid, label, verdict, live, signer, signed_at))
            else:
                drift.append((label, payload))
            break  # one owner resolves the row; a shared bootstrap id is reported once

    print(f"deployment attestations: {len(rows)}  consistent: {consistent}  "
          f"normalizable: {len(todo)}  genuine drift (left alone): {len(drift)}")
    for label, why in drift:
        print(f"  drift   {label}  — {why}")
    for pid, uid, label, verdict, live, signer, signed_at in todo:
        removed = sorted(_bare(t) for t in set((verdict.get('signature') or {}).get('catalog', {}).get('tools') or [])
                         - set(live.get('catalog', {}).get('tools') or []))
        print(f"  rewrite {label}  — drop {len(removed)} platform tool(s): {', '.join(removed[:5])}"
              + (" …" if len(removed) > 5 else ""))
    if not todo:
        return 0
    if not args.apply:
        print("\ndry run — re-run with --apply to write.")
        return 0

    backup = Path(db).with_name(f"{Path(db).name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(db, backup)
    print(f"\nbackup: {backup}")
    stamp = datetime.now().strftime("%Y-%m-%d")
    for pid, uid, label, verdict, live, signer, signed_at in todo:
        new_verdict = dict(verdict)
        new_verdict["signature"] = live
        new_verdict["note"] = ((verdict.get("note") or "deployment surface attested on this instance")
                               + f" · normalized {stamp}: platform tools removed from the attested catalog")
        env = store.sign(dep.DEPLOYMENT_KIND, pid, dep.deployment_hash(live),
                         verdict=new_verdict, signer_uuid=signer, signed_at=signed_at, db_path=db,
                         owner_uuid=uid)
        print(f"  wrote   {label}  signed={bool(env.get('signed'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
