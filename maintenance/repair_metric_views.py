#!/usr/bin/env python3
"""Land the current metric-compiler output on every deployed industry model — VIEWS
ONLY, no table rebuild, no data reload.

Why: a metric view is derived (compiled from the source model's Spark expression).
When the compiler learns to render an expression Teradata previously refused (the
sixteen views the rename redeploys skipped — ``unix_timestamp()`` and bare BYTEINT
flags), the fix has to reach the deployed models; a full redeploy is hours per model
for something that takes seconds.  Per model this script calls the constructor's own
views-only path (``POST /v1/knowledge-graph/redeploy-metric-views``), which
``REPLACE VIEW``s every metric view (atomic — a definition Teradata still refuses
leaves the existing view standing) and patches the graph's physical layer to what
exists afterwards; then, with ``--republish``, republishes the listing so every
subscriber's copy receives the new ``ddl_artifact`` nodes through the normal
fan-out sync.

Runs INSIDE the Uderia container (auth DB + localhost:5050).  Dry-run by default
(plans through the endpoint's own ``dry_run`` — lists the views the target is
missing, changes nothing); ``--apply`` executes.  Idempotent.

    docker exec -e UDERIA_ADMIN_PASSWORD=… uderia-v1-23 \\
        python3 /app/maintenance/repair_metric_views.py [--apply] [--republish] \\
        [--missing-only] [--only healthcare energy_utilities …]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("UDERIA_BASE", "http://localhost:5050/api/v1")
DB = os.environ.get("UDERIA_AUTH_DB", "/app/tda_auth.db")


def call(method, path, tok=None, body=None, timeout=1800):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {}


def deployed_models(conn):
    """Publisher graphs (never subscribed copies) that were DEPLOYED (a deploy block)."""
    out = []
    for kg_id, name, prof, user, desc in conn.execute(
            "SELECT kg_id, name, profile_id, user_uuid, source_descriptor_json FROM kg_metadata "
            "WHERE COALESCE(is_subscribed,0)=0 AND source_descriptor_json IS NOT NULL"):
        try:
            d = json.loads(desc)
        except Exception:  # noqa: BLE001
            continue
        if d.get("strategy") != "industry_model" or not d.get("deploy"):
            continue
        out.append({"kg_id": kg_id, "name": name, "profile_id": prof, "user_uuid": user,
                    "industry": d.get("industry"), "scope": d.get("model_size"),
                    "root": (d["deploy"].get("root_database") or (d.get("options") or {}).get("db_name")),
                    "target": d["deploy"].get("deployment_target_id"), "views_on": bool(d["deploy"].get("deploy_metric_views"))})
    return out


def listing(conn, kg_id):
    return conn.execute(
        "SELECT id, name, visibility FROM marketplace_knowledge_graphs WHERE kg_id = ?", (kg_id,)).fetchone()


def wait_synced(conn, marketplace_id, expect_hash, timeout=900):
    t0 = time.time()
    rows = []
    while time.time() - t0 < timeout:
        rows = conn.execute(
            "SELECT last_synced_export_hash, last_sync_error FROM kg_subscriptions WHERE source_marketplace_id = ?",
            (marketplace_id,)).fetchall()
        if not rows or all(h == expect_hash for h, _ in rows):
            return True, rows
        if any(err for _, err in rows):
            return False, rows
        time.sleep(5)
    return False, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run through the endpoint's own dry_run)")
    ap.add_argument("--republish", action="store_true", help="after a repair, republish the listing so subscriber copies sync")
    ap.add_argument("--missing-only", action="store_true", help="only create views the target lacks (default: replace all)")
    ap.add_argument("--only", nargs="*", default=None, help="industry names (e.g. healthcare)")
    ap.add_argument("--target", default=None, help="deployment_target_id (default: the recorded one)")
    ap.add_argument("--admin", default=os.environ.get("UDERIA_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("UDERIA_ADMIN_PASSWORD"))
    args = ap.parse_args()

    if not args.password:
        print("UDERIA_ADMIN_PASSWORD (or --password) is required (the dry run also asks the server)", file=sys.stderr)
        return 2
    s, j = call("POST", "/auth/login", body={"username": args.admin, "password": args.password})
    if s != 200:
        print("login failed", s, j, file=sys.stderr)
        return 2
    tok = j["token"]

    conn = sqlite3.connect(DB)
    models = deployed_models(conn)
    if args.only:
        models = [m for m in models if m["industry"] in set(args.only)]
    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {len(models)} deployed industry model(s)"
          f"{' (missing views only)' if args.missing_only else ''}")

    failures = 0
    total_created = total_refused = total_missing = 0
    for m in models:
        t0 = time.time()
        body = {"kg_id": m["kg_id"], "missing_only": args.missing_only, "dry_run": not args.apply}
        if args.target:
            body["deployment_target_id"] = args.target
        s, j = call("POST", "/knowledge-graph/redeploy-metric-views", tok, body)
        mv = j.get("metric_views") or {}
        if s != 200:
            print(f"\n{m['root']:28s} kg={m['kg_id'][:8]}  FAILED ({s}): {j.get('message')}", flush=True)
            failures += 1
            continue
        missing = mv.get("missing_before") or []
        total_missing += len(missing)
        head = (f"\n{m['root']:28s} kg={m['kg_id'][:8]}  views in model {mv.get('views_in_model')}, "
                f"missing on target {len(missing)}, not compilable {len(mv.get('not_compiled') or [])}")
        if not args.apply:
            print(head + f", would run {len(mv.get('planned') or [])}", flush=True)
            for v in missing:
                print(f"    missing: {v}")
            continue
        created, refused = mv.get("created") or [], mv.get("refused") or []
        total_created += len(created); total_refused += len(refused)
        print(head + f" -> created/replaced {len(created)}, refused {len(refused)}, "
                     f"ghosts removed {len(mv.get('ghosts_removed') or [])} in {time.time()-t0:.0f}s", flush=True)
        for v in missing:
            print(f"    was missing: {v}  ->  {'LANDED' if v in created else 'still refused'}")
        for r in refused:
            print(f"    refused: {r['view']}: {r['reason']}")
        if mv.get("status") == "partial":
            failures += 1
            for ident, err in mv.get("errors") or []:
                print(f"    ERROR {ident}: {err}")
        if args.republish:
            if not created and not (mv.get("ghosts_removed") or []):
                print("    graph unchanged — no republish needed")
                continue
            lst = listing(conn, m["kg_id"])
            if not lst:
                print("    no listing — nothing to republish")
                continue
            s, j = call("POST", f"/knowledge-graph/{m['profile_id']}/publish", tok,
                        {"kg_id": m["kg_id"], "name": lst[1], "visibility": lst[2]})
            if s not in (200, 201):
                print(f"    REPUBLISH FAILED ({s}): {j}", flush=True); failures += 1; continue
            conn2 = sqlite3.connect(DB)
            new_hash = conn2.execute("SELECT export_content_hash FROM marketplace_knowledge_graphs WHERE id=?",
                                     (lst[0],)).fetchone()[0]
            ok, rows = wait_synced(conn2, lst[0], new_hash)
            print(f"    republished ({lst[0][:8]}); subscribers synced={ok} ({len(rows)})", flush=True)
            if not ok:
                failures += 1
                for h, err in rows:
                    print(f"      sync state hash={str(h)[:8]} error={err}")
            conn2.close()
    conn.close()
    if args.apply:
        print(f"\ncreated/replaced {total_created}, refused {total_refused}, were missing {total_missing}, failures {failures}")
    else:
        print(f"\n{total_missing} view(s) missing across the deployed models (dry run — nothing changed; add --apply)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
