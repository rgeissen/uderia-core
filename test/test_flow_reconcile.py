#!/usr/bin/env python3
"""
Unit test for flow_db.reconcile_orphaned_runs().

Verifies that runs left in a non-terminal state (queued/running) by a crashed
process are settled to 'cancelled' on startup, while genuinely terminal runs
(complete/error/cancelled) are left untouched.

Runs against an isolated temp DB so the developer's tda_auth.db is never touched.

Usage:
    python test/test_flow_reconcile.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import flow_db as fdb  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def _seed_flow(flow_id):
    with fdb._get_conn() as conn:
        conn.execute(
            """INSERT INTO flows (flow_id, user_id, name, description, definition)
               VALUES (?, 'user-1', 'T', '', '{}')""",
            (flow_id,),
        )
        conn.commit()


def _mk_run(flow_id, status):
    run = fdb.create_run(flow_id, "user-1")
    if status != "queued":
        fdb.update_run_status(run["run_id"], status)
    return run["run_id"]


def main():
    print("=" * 70)
    print("FLOW RUN RECONCILIATION TEST")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp(prefix="flow_reconcile_")
    fdb._DB_PATH = Path(tmpdir) / "test.db"
    fdb._SCHEMA_APPLIED = False
    fdb._ensure_schema()
    # The base schema file predates the session-binding columns; production gets them
    # from the import-time _ensure_columns() ALTER, which ran against the REAL db before
    # this test re-pointed _DB_PATH. A fixture that skips the migration step tests a
    # database shape production never has (this suite sat red on exactly that).
    fdb._ensure_columns()

    _seed_flow("flow-1")
    runs = {
        "queued": _mk_run("flow-1", "queued"),
        "running": _mk_run("flow-1", "running"),
        "complete": _mk_run("flow-1", "complete"),
        "error": _mk_run("flow-1", "error"),
        "cancelled": _mk_run("flow-1", "cancelled"),
    }

    print("\n[reconcile]")
    count = fdb.reconcile_orphaned_runs()
    check(f"reconciled exactly 2 runs (got {count})", count == 2)

    final = {label: fdb.get_run(rid) for label, rid in runs.items()}
    check("queued → cancelled", final["queued"]["status"] == "cancelled")
    check("running → cancelled", final["running"]["status"] == "cancelled")
    check("complete untouched", final["complete"]["status"] == "complete")
    check("error untouched", final["error"]["status"] == "error")
    check("pre-cancelled untouched", final["cancelled"]["status"] == "cancelled")

    check("reconciled run has completed_at set",
          bool(final["running"]["completed_at"]))
    trace = final["queued"]["execution_trace"]
    check("reconciled run records interrupted reason in trace",
          isinstance(trace, list) and trace and trace[0].get("status") == "interrupted")

    print("\n[idempotency — second call is a no-op]")
    check("second reconcile flips 0 runs", fdb.reconcile_orphaned_runs() == 0)

    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
