"""
PROP-008 OBDA-ACT — run ONE compiled write in ONE transaction (increment B2).

Binding design: docs/proposals/PROP-008-act-business-operating-model.md §5.4 — "a failed
precondition or a postcondition violation under any acting posture ROLLS BACK (the
write and its post-check run in one transaction) and is reported as a refusal, never as
a partial success".

``run_compiled(conn, compiled, …)`` takes a DB-API connection (``teradatasql`` live,
``sqlite3`` in CI — both use ``?`` markers) and, inside one transaction:

1. evaluates every precondition (``SELECT COUNT(*)`` over the keyed rows) — the first
   that does not hold ends the run, nothing written;
2. reads the before-image of the keyed rows;
3. executes the compiled statement and takes the driver's affected-row count;
4. evaluates every postcondition (affected rows, then counts over the keyed rows);
5. appends the ledger row (the platform ledger, when the action has one);
6. commits only when everything held AND ``commit`` is true; otherwise rolls back.

It decides NOTHING about who may act or in which posture — that is B3's authority and
posture layer, which calls this. ``commit=False`` executes everything and rolls back
(the dry run a ``review`` posture or an exit test uses). The outcome names every check
with what it observed, so a refusal is exact and a receipt can carry it.

Fails closed: any driver error rolls back and is reported as the outcome's ``error``;
the connection's autocommit mode is restored whatever happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trusted_data_agent.dbdeploy.action_compiler import Check, CompiledWrite


@dataclass
class WriteOutcome:
    action: str
    invocation_hash: str
    statement_hash: str
    committed: bool = False
    rolled_back: bool = False
    rows_affected: Optional[int] = None
    checks: List[Dict[str, Any]] = field(default_factory=list)
    before: List[Dict[str, Any]] = field(default_factory=list)
    refusal: Optional[str] = None       # the first check that did not hold, in words
    refused_check: Optional[str] = None
    error: Optional[str] = None
    ledger_written: bool = False

    @property
    def held(self) -> bool:
        return self.refusal is None and self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "invocation_hash": self.invocation_hash,
                "statement_hash": self.statement_hash, "committed": self.committed,
                "rolled_back": self.rolled_back, "rows_affected": self.rows_affected,
                "checks": self.checks, "refusal": self.refusal,
                "refused_check": self.refused_check, "error": self.error,
                "ledger_written": self.ledger_written}


def _scalar(cur) -> int:
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_compiled(conn, c: CompiledWrite, *, principal: str, posture: str,
                 commit: bool = True, pre_only: bool = False) -> WriteOutcome:
    """``pre_only``: evaluate the preconditions and stop — nothing executed, the
    transaction rolled back (a ``review`` posture checks the write is still admissible
    before parking it for the owner)."""
    out = WriteOutcome(action=c.action, invocation_hash=c.invocation_hash,
                       statement_hash=c.statement_hash)
    had_autocommit = hasattr(conn, "autocommit")
    prev = conn.autocommit if had_autocommit else None
    if had_autocommit:
        conn.autocommit = False
    cur = conn.cursor()

    def evaluate(chk: Check, phase: str) -> bool:
        if chk.kind == "rows_affected":
            observed = out.rows_affected if out.rows_affected is not None else -1
        else:
            cur.execute(chk.sql, list(chk.params))
            observed = _scalar(cur)
        ok = chk.holds(observed)
        out.checks.append({"phase": phase, "id": chk.id, "source": chk.source, "kind": chk.kind,
                           "op": chk.op, "expect": chk.expect, "observed": observed, "holds": ok})
        if not ok and out.refusal is None:
            out.refused_check = chk.id
            out.refusal = (f"{phase}condition {chk.id} did not hold — {chk.words} "
                           f"(observed {observed}, expected {chk.op} {chk.expect})")
        return ok

    try:
        for chk in c.pre:
            if not evaluate(chk, "pre"):
                break
        if out.refusal is None and not pre_only:
            cur.execute(c.before_sql, list(c.before_params))
            rows = cur.fetchall() or []
            out.before = [dict(zip(c.before_columns, r)) for r in rows]
            cur.execute(c.sql, list(c.params))
            rc = getattr(cur, "rowcount", -1)
            out.rows_affected = rc if isinstance(rc, int) and rc >= 0 else None
            for chk in c.post:
                if not evaluate(chk, "post"):
                    break
        if out.refusal is None and c.ledger_sql and not pre_only:
            cur.execute(c.ledger_sql, list(c.ledger_params(
                principal=principal, posture=posture, rows_affected=out.rows_affected,
                before=out.before)))
            out.ledger_written = True
        if out.refusal is None and commit and not pre_only:
            conn.commit()
            out.committed = True
        else:
            conn.rollback()
            out.rolled_back = True
            out.ledger_written = out.ledger_written and out.committed
    except Exception as exc:  # noqa: BLE001 — fail closed: nothing is left half-written
        out.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        out.rolled_back = True
        out.committed = False
        out.ledger_written = False
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
        if had_autocommit:
            try:
                conn.autocommit = prev
            except Exception:  # noqa: BLE001
                pass
    return out
