"""
design_rules_verify — execute every design rule's verification statement on a target and
record it (PROP-008 A4, §4.4 (1); a second backend's rule set since C3).

The design standard (``dbdeploy/design_standard.py``) is a set of RULE RECORDS; each carries
the statement that proves its vocabulary executes on a target version.  This tool runs them
on a scratch database it creates and drops, and with ``--write`` records the result beside
the module (``design_standard_verification.json``, the teradata-patterns shape: system · on ·
sha256 per statement) — the record ``rules_report`` reads to call a rule ``verified``.

Nothing here is a claim about a database until the database has said it.  A statement the
target refuses is reported by rule and NOT recorded.

    PYTHONPATH=src python3 scripts/design_rules_verify.py            # run, report
    PYTHONPATH=src python3 scripts/design_rules_verify.py --write    # run, report, record
    PYTHONPATH=src python3 scripts/design_rules_verify.py --backend postgresql --write

Teradata target: the platform's encrypted connection store through ``tdrun.creds()`` (the
policing pack's helper), or TDP_HOST / TDP_USER / TDP_PASSWORD when set.  PostgreSQL target:
PGHOST / PGPORT / PGUSER / PGPASSWORD / PGDATABASE.  Each backend writes its OWN record
(``rule_set_for(backend).verification_file``) — one never vouches for the other.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent_packs/policing-data-products/scripts"))

from trusted_data_agent.dbdeploy.design_standard import (  # noqa: E402
    RULES, SCRATCH, SCRATCH_CHILD, SCRATCH_NOPI, VERIFICATION_FILE, rule_set_for, statement_sha,
)


def substitute(sql: str, db: str) -> str:
    return (sql.replace(SCRATCH_CHILD, f'"{db}"."DR_CHILD"').replace(SCRATCH_NOPI, f'"{db}"."DR_NOPI"')
               .replace(SCRATCH, f'"{db}"."DR_SCRATCH"').replace("{{scratch_db}}", db).replace("{{scratch_table}}", "DR_SCRATCH"))


async def verify_rules(execute: Callable[[str], Any], db: str, *, system: str, rule_set=None) -> Dict[str, Any]:
    """Run every rule's statements on ``db`` (already provisioned) in a dependency-safe order
    (the scratch tables are created by the rules that declare them, so the ``multiset`` rule
    runs first).  Returns ``{"system", "on", "rules": {id: {"statements": [sha…]}}, "failed": {id: err}}``."""
    rs = rule_set or rule_set_for("teradata")
    first = list(rs.verify_first)
    order = first + [r.id for r in rs.rules if r.id not in first]
    by_id = rs.by_id
    out: Dict[str, Any] = {"standard": rs.version, "backend": rs.backend,
                           "system": system, "on": time.strftime("%Y-%m-%d"), "rules": {}, "failed": {}}
    for rid in order:
        rule = by_id[rid]
        done: List[str] = []
        for stmt in rule.verification:
            sql = substitute(stmt, db)
            try:
                r = execute(sql)
                if asyncio.iscoroutine(r):
                    await r
                done.append(statement_sha(stmt))
            except Exception as exc:  # noqa: BLE001
                out["failed"][rid] = f"{sql[:80]}… → {str(exc)[:160]}"
                break
        if rid not in out["failed"] and done:
            out["rules"][rid] = {"statements": done}
    return out


def _creds():
    if all(os.environ.get(k) for k in ("TDP_HOST", "TDP_USER", "TDP_PASSWORD")):
        return {"host": os.environ["TDP_HOST"], "user": os.environ["TDP_USER"], "password": os.environ["TDP_PASSWORD"]}
    import tdrun  # noqa: E402
    return tdrun.creds()


async def _teradata_target(args):
    from trusted_data_agent.dbdeploy import TeradataDeploymentBackend
    c = _creds()
    be = TeradataDeploymentBackend({"host": c["host"], "username": c["user"], "password": c["password"],
                                    "database_parent": "dbc", "database_perm_bytes": args.perm})
    await be.initialize()
    return be, (await be.system_version() or "Teradata (version not read)")


async def _postgres_target(args):
    from trusted_data_agent.dbdeploy import PostgresDeploymentBackend
    be = PostgresDeploymentBackend({"host": os.environ.get("PGHOST", "localhost"), "port": os.environ.get("PGPORT", "5432"),
                                    "username": os.environ.get("PGUSER"), "password": os.environ.get("PGPASSWORD"),
                                    "database": os.environ.get("PGDATABASE", "postgres")})
    return be, (await be.system_version() or "PostgreSQL (version not read)")


async def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--write", action="store_true", help="record the verification beside the standard")
    ap.add_argument("--backend", default="teradata", choices=["teradata", "postgresql"])
    ap.add_argument("--perm", type=int, default=20_000_000, help="PERM for the Teradata scratch database (bytes)")
    args = ap.parse_args(argv)
    rs = rule_set_for(args.backend)
    be, system = await (_postgres_target(args) if args.backend == "postgresql" else _teradata_target(args))
    db = f"DRV_{uuid.uuid4().hex[:6]}"
    await be.ensure_database(db)
    try:
        rec = await verify_rules(be._execute_sql, db, system=system, rule_set=rs)
    finally:
        r = await be.drop_database(db)
        print("cleanup:", db, "dropped" if r.success else r.errors)
    print(f"system: {system} · standard {rs.version}")
    for rid in [x.id for x in rs.rules]:
        st = "verified" if rid in rec["rules"] else f"FAILED — {rec['failed'].get(rid, 'not run')}"
        print(f"  {rid:24s} {st}")
    if rec["failed"]:
        print(f"\n{len(rec['failed'])} rule(s) refused by the target — not recorded")
    if args.write:
        if rec["failed"]:
            print("--write refused: every rule must execute before the record is written")
            return 2
        with open(rs.verification_file, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in rec.items() if k != "failed"}, fh, indent=1, sort_keys=True)
        print(f"recorded → {rs.verification_file}")
    return 1 if rec["failed"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
