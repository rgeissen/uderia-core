"""
The platform's record of every act an action performed, parked, observed or refused —
whatever face it came through (PROP-008 §6).

Before it, an act was recorded where its face kept state: a conversation turn stamped
`actions`, a flow node run, a scheduled task run, the review queue. An act through REST, the
MCP server or a dashboard tile with no session left no platform record at all (only the
warehouse ledger, which holds writes only and is not read by a trust surface), and Trust
History and the activity tally — both reading conversation turns — never saw an act nobody
watched (Act II rehearsal, Oct 2026). One append-only table, written at the seams where a
final record exists, read by every audit surface.

Fail-soft on write (a log that cannot be written never fails the act — the act's own record
and the warehouse ledger still hold it); the table is self-applying.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

#: The outcomes that are acts. `prepared` is not one — nothing happened yet.
LOGGED_OUTCOMES = ("performed", "parked", "observed", "refused")

_DDL = """
CREATE TABLE IF NOT EXISTS action_act_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              TEXT NOT NULL,
    user_uuid       TEXT NOT NULL,
    profile_id      TEXT,
    principal       TEXT,
    kg_id           TEXT,
    action          TEXT,
    version         INTEGER,
    outcome         TEXT NOT NULL,
    rows_affected   INTEGER,
    posture         TEXT,
    face            TEXT,
    invocation_hash TEXT,
    session_id      TEXT,
    turn_id         TEXT,
    confirmed_by    TEXT,
    approved_by     TEXT,
    review_id       TEXT,
    refusal         TEXT,
    approvers       TEXT
)"""


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None):
    conn = sqlite_util.connect(_db_path(db_path))
    conn.execute(_DDL)
    try:   # four eyes (PROP-008 §5.4a): who a parked write awaits, by account name
        conn.execute("ALTER TABLE action_act_log ADD COLUMN approvers TEXT")
    except Exception:  # noqa: BLE001 — column already exists
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_action_act_log_user ON action_act_log(user_uuid, at)")
    return conn


def profile_of(rec: Dict[str, Any]) -> Optional[str]:
    """The agent an act ran as — from its principal (``agent:<profile id>``), else None (a
    person acting with their own authority). Pure."""
    p = str((rec or {}).get("principal") or "")
    if p.startswith("agent:"):
        return p[len("agent:"):].split(" ", 1)[0] or None
    return (rec or {}).get("profile_id") or None


def log_act(user_uuid: str, rec: Dict[str, Any], face: Optional[str], *,
            db_path: Optional[str] = None) -> bool:
    """Append one act. Returns whether it was written; never raises."""
    rec = rec or {}
    out = rec.get("outcome")
    if not user_uuid or out not in LOGGED_OUTCOMES:
        return False
    try:
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO action_act_log (at, user_uuid, profile_id, principal, kg_id, action, version, "
                "outcome, rows_affected, posture, face, invocation_hash, session_id, turn_id, confirmed_by, "
                "approved_by, review_id, refusal, approvers) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), user_uuid, profile_of(rec), rec.get("principal"),
                 rec.get("kg_id"), rec.get("action"), rec.get("version"), out, rec.get("rows_affected"),
                 rec.get("posture"), face or rec.get("face"), rec.get("invocation_hash"),
                 (str(rec["session_id"]) if rec.get("session_id") else None),
                 (str(rec["turn_id"]) if rec.get("turn_id") is not None else None),
                 rec.get("confirmed_by"), rec.get("approved_by"), rec.get("review_id"),
                 (str(rec.get("refusal"))[:500] if rec.get("refusal") else None),
                 (", ".join(rec["approvers"]) if isinstance(rec.get("approvers"), (list, tuple))
                  and rec.get("approvers") else None)))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"action act log: not written ({e})")
        return False


def list_acts(user_uuid: str, *, profile_id: Optional[str] = None, limit: int = 200,
              db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """The caller's acts, newest first (optionally one agent's). Owner-scoped."""
    try:
        conn = _connect(db_path)
        try:
            q = "SELECT * FROM action_act_log WHERE user_uuid = ?"
            args: List[Any] = [user_uuid]
            if profile_id:
                q += " AND profile_id = ?"
                args.append(profile_id)
            q += " ORDER BY at DESC, id DESC LIMIT ?"
            args.append(max(1, min(int(limit), 1000)))
            cur = conn.execute(q, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"action act log: unreadable ({e})")
        return []


def delete_for_kg(user_uuid: str, kg_id: str, *, actions: Optional[List[str]] = None,
                  db_path: Optional[str] = None) -> int:
    """Remove one user's acts on one graph (a rehearsal reset). Returns the rows removed."""
    conn = _connect(db_path)
    try:
        q, args = "DELETE FROM action_act_log WHERE user_uuid = ? AND kg_id = ?", [user_uuid, kg_id]
        if actions:
            q += " AND action IN (" + ",".join("?" * len(actions)) + ")"
            args += list(actions)
        n = conn.execute(q, args).rowcount
        conn.commit()
        return n
    finally:
        conn.close()

