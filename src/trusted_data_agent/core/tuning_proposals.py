"""Workload tuning proposals — the lifecycle (PROP-008 §4.5, increment C1c).

The recommender (``dbdeploy/tuning.py``) decides what the recorded workload asks for; the
evidence module (``core/tuning_evidence.py``) gathers what it decides from. This module
holds what happens next, and nothing here decides a proposal's content:

    pending ──approve (a person)──► approved ──land──► landed
       │                              │                └─► failed (the database refused;
       │                              │                    a person re-approves to retry)
       ├──reject (a person)──► rejected
       └──(no longer supported by the workload / older than EXPIRY_DAYS)──► expired

Rules, each gated in ``test/test_pdm_tuning.py``:

- **Nothing lands without approval.** ``land`` refuses any status but ``approved``.
- **A person approves, never an agent.** An actor named ``agent:…`` is refused. The REST
  layer additionally requires the graph's owner.
- **A rebuild is never landed by the platform.** A partition proposal (``landing:
  "rebuild"``) can be rejected or kept as advice; approving it is refused, because the
  act it would offer cannot be performed (the offered act must be the act that is owed).
- **Decisions survive a new review.** A re-review refreshes the evidence of a pending
  proposal and leaves an approved, rejected, landed or failed one untouched; a pending
  proposal the new review no longer supports is EXPIRED with its evidence kept.
- **Measured, not claimed.** Landing EXPLAINs the workload statements that motivated the
  proposal before and after, and the receipt carries both estimates (seconds).
- **Signed.** The receipt is Ed25519-signed over a closed field tuple with the deployment's
  provenance key (the model-change receipt precedent).

Storage: one self-applying table through ``sqlite_util`` (the platform's serialization
contract). Owner-scoped by ``user_id``.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

EXPIRY_DAYS = 30          # a pending proposal nobody decided expires (evidence kept)
MAX_MEASURE = 10          # workload statements EXPLAINed before/after a landing

STATUSES = ("pending", "approved", "rejected", "landed", "failed", "expired")
DECIDED = ("approved", "rejected", "landed", "failed")

_DDL = """
CREATE TABLE IF NOT EXISTS tuning_proposals (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    kg_id           TEXT NOT NULL,
    connection_id   TEXT,
    proposal_id     TEXT NOT NULL,
    rule            TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    landing         TEXT NOT NULL DEFAULT 'direct',
    status          TEXT NOT NULL DEFAULT 'pending',
    proposal_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    note            TEXT,
    receipt_json    TEXT,
    UNIQUE (user_id, kg_id, proposal_id)
)
"""


class TuningError(ValueError):
    """A refused lifecycle act. ``code`` is machine-readable; the message says what to do."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db(db_path: Optional[str]) -> str:
    return db_path or "tda_auth.db"


def _ensure(con) -> None:
    con.execute(_DDL)


def _row(r) -> Dict[str, Any]:
    (rid, user_id, kg_id, conn, pid, rule, purpose, table, landing, status, pj,
     created, updated, by, at, note, rj) = r
    p = json.loads(pj or "{}")
    return {"id": rid, "kg_id": kg_id, "connection_id": conn, "proposal_id": pid, "rule": rule,
            "purpose": purpose, "table": table, "landing": landing, "status": status,
            "proposal": p, "created_at": created, "updated_at": updated, "decided_by": by,
            "decided_at": at, "note": note, "receipt": json.loads(rj) if rj else None}


_COLS = ("id, user_id, kg_id, connection_id, proposal_id, rule, purpose, table_name, landing, "
         "status, proposal_json, created_at, updated_at, decided_by, decided_at, note, receipt_json")


def _get(con, user_id: str, rid: str) -> Optional[Dict[str, Any]]:
    r = con.execute(f"SELECT {_COLS} FROM tuning_proposals WHERE id = ? AND user_id = ?",
                    (rid, user_id)).fetchone()
    return _row(r) if r else None


def _stored(proposal: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "rule", "purpose", "table", "columns", "key", "evidence", "rationale",
            "statements", "landing", "ddl", "measure_sql", "count")
    return {k: proposal.get(k) for k in keep if k in proposal}


# ── record ────────────────────────────────────────────────────────────────────

def record_review(user_id: str, kg_id: str, review: Dict[str, Any], *,
                  connection_id: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Store a review's proposals. New → pending; pending → evidence refreshed; decided →
    untouched; expired → pending again (the workload asks for it again). A pending proposal
    the review no longer contains → expired, evidence kept. Returns the graph's proposals."""
    now = _now()
    want = {p["id"]: p for p in review.get("proposals") or [] if isinstance(p, dict) and p.get("id")}
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        have = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT proposal_id, id, status FROM tuning_proposals WHERE user_id = ? AND kg_id = ?",
            (user_id, kg_id)).fetchall()}
        for pid, p in want.items():
            body = json.dumps(_stored(p), sort_keys=True, default=str)
            if pid not in have:
                con.execute(
                    f"INSERT INTO tuning_proposals ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("tun_" + uuid.uuid4().hex[:12], user_id, kg_id, connection_id, pid, p.get("rule") or "",
                     p.get("purpose") or "", p.get("table") or "", p.get("landing") or "direct", "pending",
                     body, now, now, None, None, None, None))
                continue
            rid, status = have[pid]
            if status in ("pending", "expired"):
                con.execute("UPDATE tuning_proposals SET proposal_json = ?, status = 'pending', updated_at = ?, "
                            "connection_id = COALESCE(?, connection_id), note = NULL WHERE id = ?",
                            (body, now, connection_id, rid))
        for pid, (rid, status) in have.items():
            if pid not in want and status == "pending":
                con.execute("UPDATE tuning_proposals SET status = 'expired', updated_at = ?, note = ? WHERE id = ?",
                            (now, "the recorded workload no longer supports this proposal", rid))
        con.commit()
    finally:
        con.close()
    return list_proposals(user_id, kg_id, db_path=db_path)


def expire_stale(user_id: str, kg_id: Optional[str] = None, *, days: int = EXPIRY_DAYS,
                 db_path: Optional[str] = None) -> int:
    """Expire pending proposals nobody decided within ``days`` (evidence kept)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        q = "UPDATE tuning_proposals SET status = 'expired', note = ?, updated_at = ? " \
            "WHERE user_id = ? AND status = 'pending' AND updated_at < ?"
        args: List[Any] = [f"not decided within {days} days", _now(), user_id, cutoff]
        if kg_id:
            q += " AND kg_id = ?"
            args.append(kg_id)
        n = con.execute(q, args).rowcount
        con.commit()
        return int(n or 0)
    finally:
        con.close()


def list_proposals(user_id: str, kg_id: str, *, status: Optional[str] = None,
                   db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        q = f"SELECT {_COLS} FROM tuning_proposals WHERE user_id = ? AND kg_id = ?"
        args: List[Any] = [user_id, kg_id]
        if status:
            q += " AND status = ?"
            args.append(status)
        rows = [_row(r) for r in con.execute(q + " ORDER BY created_at, proposal_id", args).fetchall()]
    finally:
        con.close()
    order = {s: i for i, s in enumerate(("pending", "approved", "failed", "landed", "rejected", "expired"))}
    return sorted(rows, key=lambda r: (order.get(r["status"], 9), r["purpose"], r["table"]))


def get_proposal(user_id: str, rid: str, *, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        return _get(con, user_id, rid)
    finally:
        con.close()


# ── decide ────────────────────────────────────────────────────────────────────

def decide(user_id: str, rid: str, action: str, *, actor: str, note: Optional[str] = None,
           db_path: Optional[str] = None) -> Dict[str, Any]:
    """``approve`` or ``reject`` a pending proposal. A person decides — never an agent."""
    if action not in ("approve", "reject"):
        raise TuningError("bad_action", "action must be approve or reject")
    if not actor or str(actor).startswith("agent:"):
        raise TuningError("person_required",
                          "a tuning proposal is decided by a person — an agent may propose, never approve")
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        row = _get(con, user_id, rid)
        if not row:
            raise TuningError("not_found", "no such tuning proposal")
        # a FAILED landing is decided again: the person re-approves to retry, or rejects
        if row["status"] not in ("pending", "failed"):
            raise TuningError("not_pending", f"this proposal is {row['status']} — only a pending or failed proposal is decided")
        if action == "approve" and row["landing"] == "rebuild":
            raise TuningError("rebuild_required",
                              "this proposal needs the table rebuilt — the platform never rebuilds a table; "
                              "keep it as advice or reject it")
        status = "approved" if action == "approve" else "rejected"
        con.execute("UPDATE tuning_proposals SET status = ?, decided_by = ?, decided_at = ?, note = ?, "
                    "updated_at = ? WHERE id = ?", (status, actor, _now(), note, _now(), rid))
        con.commit()
        return _get(con, user_id, rid)
    finally:
        con.close()


# ── land ──────────────────────────────────────────────────────────────────────

def delete_proposal(user_id: str, rid: str, *, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Remove a proposal that LANDED NOTHING (pending · failed · rejected · expired). An
    approved or landed proposal is a record of a structure that exists — it is never deleted
    here (undo the structure in the warehouse first, by hand and on purpose)."""
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        row = _get(con, user_id, rid)
        if not row:
            raise TuningError("not_found", "no such tuning proposal")
        if row["status"] in ("approved", "landed"):
            raise TuningError("not_deletable", f"this proposal is {row['status']} — a structure it landed is not undone by deleting its record")
        con.execute("DELETE FROM tuning_proposals WHERE id = ? AND user_id = ?", (rid, user_id))
        con.commit()
        return row
    finally:
        con.close()


def receipt_content(receipt: Dict[str, Any]) -> str:
    """The CLOSED field tuple a tuning receipt is signed over."""
    fields = {k: receipt.get(k) for k in ("receipt_id", "kind", "kg_id", "proposal_id", "rule", "purpose",
                                           "table", "at", "by", "approved_by", "statements", "landed", "errors",
                                           "measured_before", "measured_after",
                                           "unconfident_before", "unconfident_after", "plans_changed")}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)


async def land(user_id: str, rid: str, *, actor: str,
               execute: Callable[[str], Awaitable[Any]],
               measure: Optional[Callable[[List[str]], Awaitable[Optional[float]]]] = None,
               db_path: Optional[str] = None) -> Dict[str, Any]:
    """Land an APPROVED proposal: measure, execute its statements one per round trip, measure
    again, sign the receipt. ``execute`` runs one statement; ``measure`` returns the summed
    estimated seconds of the given workload statements (``None`` = could not measure)."""
    row = get_proposal(user_id, rid, db_path=db_path)
    if not row:
        raise TuningError("not_found", "no such tuning proposal")
    if row["status"] != "approved":
        raise TuningError("not_approved",
                          f"this proposal is {row['status']} — nothing lands without a person's approval")
    if row["landing"] == "rebuild":
        raise TuningError("rebuild_required", "this proposal needs a rebuild — the platform never lands it")
    p = row["proposal"]
    stmts = [s for s in (p.get("ddl") or []) if isinstance(s, str) and s.strip() and not s.lstrip().startswith("--")]
    if not stmts:
        raise TuningError("nothing_to_land", "this proposal renders no statement")
    sample = list(p.get("measure_sql") or [])[:MAX_MEASURE]

    async def _m() -> Dict[str, Any]:
        """``{seconds, unconfident}`` — the summed estimate and the plan steps it rests on
        that were estimated WITHOUT statistics (a guess, not a measurement). ``measure`` may
        return a bare number (seconds only) or that dict."""
        none = {"seconds": None, "unconfident": None, "shapes": None}
        if not measure or not sample:
            return none
        try:
            v = await measure(sample)
        except Exception as e:  # noqa: BLE001 — a measurement never blocks the landing
            logger.info(f"tuning: measurement skipped: {str(e)[:160]}")
            return none
        if isinstance(v, dict):
            sec = v.get("seconds")
            return {"seconds": round(float(sec), 4) if sec is not None else None,
                    "unconfident": v.get("unconfident"), "shapes": v.get("shapes")}
        return {**none, "seconds": round(float(v), 4) if v is not None else None}

    before = await _m()
    landed: List[str] = []
    errors: List[List[str]] = []
    for s in stmts:
        try:
            await execute(s)
            landed.append(s)
        except Exception as e:  # noqa: BLE001
            errors.append([s, str(e)[:400]])
            break
    after = await _m() if not errors else {"seconds": None, "unconfident": None, "shapes": None}
    # how many workload statements now RUN differently (a shape change), not only cost differently
    sb, sa = before.get("shapes"), after.get("shapes")
    plans_changed = (sum(1 for x, y in zip(sb, sa) if x and y and x != y)
                     if isinstance(sb, list) and isinstance(sa, list) and len(sb) == len(sa) else None)
    receipt: Dict[str, Any] = {
        "receipt_id": "tr_" + uuid.uuid4().hex[:12], "kind": "tuning", "kg_id": row["kg_id"],
        "proposal_id": row["proposal_id"], "rule": row["rule"], "purpose": row["purpose"],
        "table": row["table"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": actor,
        "approved_by": row["decided_by"], "statements": stmts, "landed": landed, "errors": errors,
        "measured_before": before["seconds"], "measured_after": after["seconds"],
        "unconfident_before": before["unconfident"], "unconfident_after": after["unconfident"],
        "plans_changed": plans_changed,
        "measured_statements": len(sample),
    }
    try:
        from trusted_data_agent.eval import online
        receipt["attestation"] = online.sign_payload(receipt_content(receipt))
    except Exception as exc:  # noqa: BLE001
        logger.debug("tuning: receipt attestation unavailable: %s", exc)
        receipt["attestation"] = {"signed": False}
    status = "failed" if errors else "landed"
    con = sqlite_util.connect(_db(db_path))
    try:
        _ensure(con)
        con.execute("UPDATE tuning_proposals SET status = ?, receipt_json = ?, updated_at = ?, note = ? WHERE id = ?",
                    (status, json.dumps(receipt, default=str), _now(),
                     (errors[0][1] if errors else None), rid))
        con.commit()
    finally:
        con.close()
    try:
        from trusted_data_agent.eval import online
        online.emit_governance_span({
            "name": "ontology.tuning", "ontology.kg_id": row["kg_id"], "tuning.receipt_id": receipt["receipt_id"],
            "tuning.purpose": row["purpose"], "tuning.table": row["table"], "tuning.status": status,
            "tuning.measured_before": before["seconds"], "tuning.measured_after": after["seconds"],
            "attestation": receipt.get("attestation")})
    except Exception:  # noqa: BLE001
        pass
    return {**get_proposal(user_id, rid, db_path=db_path), "receipt": receipt}


# ── the physical record in the graph ──────────────────────────────────────────

def artifact_name(row: Dict[str, Any]) -> str:
    """The deployed identifier a landed proposal created (a join index), or a stable name
    for a statistic on a table (statistics are not dictionary objects of their own)."""
    p = row.get("proposal") or {}
    if row.get("purpose") == "join_index":
        for s in p.get("ddl") or []:
            if s.upper().startswith("CREATE JOIN INDEX"):
                ident = s.split()[3]
                return ident.replace('"', "")
    return f"{row.get('table')}#stats({','.join(p.get('columns') or [])})"


async def stamp_artifact(store, row: Dict[str, Any]) -> Optional[int]:
    """Record a LANDED proposal as a physical ``ddl_artifact`` node carrying its purpose, rule,
    evidence and before/after measurement, ``derives_from`` the table it tunes. Fail-soft: the
    landing happened whatever the graph write does. Tuning artifacts do not move the
    ontology's seal (``composition.kg_content_hash`` leaves them out)."""
    if row.get("status") != "landed":
        return None
    rc = row.get("receipt") or {}
    p = row.get("proposal") or {}
    props = {"layer": "physical", "infrastructure": "teradata", "purpose": row.get("purpose"),
             "rule": row.get("rule"), "evidence": p.get("evidence"), "columns": p.get("columns"),
             "proposal_id": row.get("proposal_id"), "landed_by": rc.get("by"), "approved_by": rc.get("approved_by"),
             "landed_at": rc.get("at"), "receipt_id": rc.get("receipt_id"),
             "measured_before": rc.get("measured_before"), "measured_after": rc.get("measured_after"),
             "unconfident_before": rc.get("unconfident_before"), "unconfident_after": rc.get("unconfident_after"),
             "plans_changed": rc.get("plans_changed"),
             "signed": bool((rc.get("attestation") or {}).get("signed"))}
    try:
        name = artifact_name(row)
        existing = store.get_entity_by_name(name, "ddl_artifact")
        if existing:
            await store.update_entity(existing["id"], properties=props)
            nid = existing["id"]
        else:
            nid = await store.add_entity(name, "ddl_artifact", properties=props, source="tuning")
        table = store.get_entity_by_name(row.get("table") or "", "table")
        if not table:
            # the graph's physical node may be named in the deployed case
            for e in await store.list_entities(entity_type="table", limit=100000) or []:
                if str(e.get("name", "")).lower() == str(row.get("table") or "").lower():
                    table = e
                    break
        if table:
            try:
                await store.add_relationship(nid, table["id"], "derives_from", source="tuning")
            except Exception:  # noqa: BLE001 — an existing edge
                pass
        return nid
    except Exception as e:  # noqa: BLE001
        logger.info(f"tuning: artifact node not written: {str(e)[:200]}")
        return None
