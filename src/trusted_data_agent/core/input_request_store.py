"""
The input-request store — one record per question an agent put to a person (PROP-011).

A question is a record with an id; a turn that stopped for it ends `input_required`. This
module owns the rows (`schema/46_input_requests.sql`, applied on connect like the review
queue) and the **resume binding**: the credential a person's answer must present to resume
the work. The binding is a signed ticket (HS256 under the platform's JWT secret — the same
key every other ticket of the platform uses) carrying the principal, the session, the turn,
a digest of the question and an expiry; it is consumed ONCE, by the status flip
`pending → accepted|declined|cancelled` in a single guarded UPDATE, so a second presentation,
another user's, another request's, or an expired one is refused. The REST and MCP faces never
accept a binding this server did not issue.

Nothing here decides WHETHER to ask — that is `agent/input_requests.py` (the ledger). Nothing
here runs a turn — that is the answer route. Every function fails quiet where a question must
not break the turn that raised it (`create` returns None), and loud where a person's act is
being validated (`consume` returns a reason).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.core import sqlite_util
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

SCHEMA = Path(__file__).resolve().parents[3] / "schema" / "46_input_requests.sql"
STATUSES = ("pending", "accepted", "declined", "cancelled", "expired", "superseded")
#: The three ways a person answers (MCP's response model): the values; no; not now.
DECISIONS = ("accept", "decline", "cancel")
RESUME_PURPOSE = "input_resume"
_COLS = ("id, user_uuid, session_id, turn, task_id, origin, raised_by, profile_id, profile_tag, source, "
         "parent_id, kind, items_json, why, default_json, digest, status, answer_json, answered_by, "
         "answered_via, answered_at, superseded_by, expires_at, created_at")


def _db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite_util.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    conn.commit()
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def digest_of(kind: str, items: List[Dict[str, Any]], why: Optional[str]) -> str:
    """The canonical question, hashed — what the resume binding is tied to, so an answer to a
    question whose text or options changed underneath is refused."""
    canon = json.dumps({"kind": kind, "items": items, "why": why or ""}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    for k in ("items_json", "default_json", "answer_json"):
        raw = d.pop(k, None)
        key = k[:-5]
        try:
            d[key] = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            d[key] = None
    d["items"] = d.get("items") or []
    return d


# ── writing ──────────────────────────────────────────────────────────────────

@_signals.announces("needs_input", "created", user="user_uuid", ids=("id",), profile="profile_id")
def create(*, user_uuid: str, kind: str, items: List[Dict[str, Any]], why: Optional[str],
           session_id: Optional[str], turn: Any, task_id: Optional[str], origin: str,
           raised_by: str, profile_id: Optional[str], profile_tag: Optional[str], source: Optional[str],
           parent_id: Optional[str] = None, default: Any = None, expires_in_hours: Optional[float] = None,
           status: str = "pending", answered_by: Optional[str] = None,
           db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Persist a question; return its record (with its `resume` binding) or None when the store
    is unavailable — a question that cannot be recorded is not raised."""
    try:
        rid = "ir-" + uuid.uuid4().hex[:20]
        now = _now()
        exp = _iso(now + timedelta(hours=float(expires_in_hours))) if expires_in_hours else None
        dg = digest_of(kind, items, why)
        try:
            t = int(turn) if turn is not None and str(turn).strip() != "" else None
        except (TypeError, ValueError):
            t = None
        conn = _connect(db_path)
        try:
            conn.execute(
                f"INSERT INTO input_requests ({_COLS}) VALUES ({','.join('?' * 24)})",
                (rid, user_uuid, session_id, t, task_id, origin, raised_by, profile_id, profile_tag, source,
                 parent_id, kind, json.dumps(items), why, json.dumps(default) if default is not None else None,
                 dg, status, None, answered_by, None, _iso(now) if status != "pending" else None, None, exp,
                 _iso(now)))
            conn.commit()
            r = conn.execute(f"SELECT {_COLS} FROM input_requests WHERE id = ?", (rid,)).fetchone()
        finally:
            conn.close()
        rec = _row(r)
        rec["resume"] = mint_resume(rec)
        return rec
    except Exception as e:  # noqa: BLE001 — the store must never break the turn
        logger.warning(f"input-requests: create failed: {e}")
        return None


def mint_resume(rec: Dict[str, Any]) -> str:
    """The signed, principal-bound, request-bound, expiring binding an answer must present."""
    import jwt
    from trusted_data_agent.auth.security import JWT_ALGORITHM, JWT_SECRET_KEY
    exp = rec.get("expires_at")
    try:
        exp_dt = datetime.fromisoformat(exp) if exp else _now() + timedelta(days=30)
    except Exception:  # noqa: BLE001
        exp_dt = _now() + timedelta(days=30)
    payload = {"purpose": RESUME_PURPOSE, "rid": rec["id"], "sub": rec["user_uuid"],
               "sid": rec.get("session_id"), "turn": rec.get("turn"), "digest": rec["digest"],
               "iat": _now(), "exp": exp_dt, "jti": secrets.token_urlsafe(12)}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_resume(token: Optional[str], rec: Dict[str, Any], user_uuid: str) -> Optional[str]:
    """None when the binding is this server's, for this request, for this person, unexpired and
    the question unchanged; else the reason (a sentence)."""
    if not token:
        return "no resume binding presented"
    import jwt
    from trusted_data_agent.auth.security import JWT_ALGORITHM, JWT_SECRET_KEY
    try:
        p = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return "the resume binding has expired"
    except jwt.InvalidTokenError:
        return "the resume binding was not issued by this server"
    if p.get("purpose") != RESUME_PURPOSE:
        return "not a resume binding"
    if p.get("rid") != rec.get("id"):
        return "the resume binding is for another question"
    if p.get("sub") != user_uuid or rec.get("user_uuid") != user_uuid:
        return "the resume binding belongs to another person"
    if p.get("digest") != rec.get("digest"):
        return "the question changed since the binding was issued"
    return None


@_signals.announces("needs_input", "updated", user="user_uuid", ids=("rid",), when=lambda r: bool(r and r[0]))
def consume(rid: str, *, user_uuid: str, decision: str, answer: Any, answered_via: str,
            answered_by: Optional[str] = None, db_path: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Flip `pending → accepted|declined|cancelled` ONCE. `(record, None)` on success; `(None,
    reason)` when the question is not pending any more (answered, expired, superseded) or is
    not this person's. Single use is the guarded UPDATE: two answers race, one wins."""
    if decision not in DECISIONS:
        return None, f"decision must be one of {list(DECISIONS)}"
    new_status = {"accept": "accepted", "decline": "declined", "cancel": "cancelled"}[decision]
    try:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "UPDATE input_requests SET status = ?, answer_json = ?, answered_by = ?, answered_via = ?, answered_at = ? "
                "WHERE id = ? AND user_uuid = ? AND status = 'pending' "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (new_status, json.dumps(answer) if answer is not None else None, answered_by or user_uuid,
                 answered_via, _iso(_now()), rid, user_uuid, _iso(_now())))
            conn.commit()
            if cur.rowcount != 1:
                r = conn.execute(f"SELECT {_COLS} FROM input_requests WHERE id = ?", (rid,)).fetchone()
                if r is None:
                    return None, "no such question"
                rec = _row(r)
                if rec["user_uuid"] != user_uuid:
                    return None, "this question is not yours to answer"
                if rec["status"] == "pending":
                    _expire_row(conn, rid)
                    return None, "this question has expired"
                return None, f"this question is already {rec['status']}"
            r = conn.execute(f"SELECT {_COLS} FROM input_requests WHERE id = ?", (rid,)).fetchone()
            return _row(r), None
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: consume failed: {e}")
        return None, "the question store is unavailable"


def _expire_row(conn: sqlite3.Connection, rid: str) -> None:
    conn.execute("UPDATE input_requests SET status = 'expired' WHERE id = ? AND status = 'pending'", (rid,))
    conn.commit()


def supersede(rid: str, *, by: str, db_path: Optional[str] = None) -> bool:
    """The person moved on, or a parent's answer reached the child by another turn."""
    try:
        conn = _connect(db_path)
        try:
            cur = conn.execute("UPDATE input_requests SET status = 'superseded', superseded_by = ?, answered_at = ? "
                               "WHERE id = ? AND status = 'pending'", (by, _iso(_now()), rid))
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: supersede failed: {e}")
        return False


@_signals.announces("needs_input", "updated", user="user_uuid", ids=("session_id",))
def supersede_pending_for_session(session_id: str, user_uuid: str, *, by: str,
                                  db_path: Optional[str] = None) -> List[str]:
    """Every pending question of this session, superseded — a new turn that does not answer
    the pending question means the person moved on (the composer rule, PROP-011 §4.5)."""
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute("SELECT id FROM input_requests WHERE session_id = ? AND user_uuid = ? AND status = 'pending'",
                                (session_id, user_uuid)).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                conn.execute(f"UPDATE input_requests SET status = 'superseded', superseded_by = ?, answered_at = ? "
                             f"WHERE id IN ({','.join('?' * len(ids))}) AND status = 'pending'",
                             (by, _iso(_now()), *ids))
                conn.commit()
            return ids
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: supersede-for-session failed: {e}")
        return []


def expire_due(db_path: Optional[str] = None, *, return_rows: bool = False):
    """Flip every pending question past its expiry — the platform job and the lazy read. With
    `return_rows` the expired records come back (the job tells each unattended run's channel)."""
    try:
        conn = _connect(db_path)
        try:
            now = _iso(_now())
            rows = []
            if return_rows:
                rows = [_row(r) for r in conn.execute(
                    f"SELECT {_COLS} FROM input_requests WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?",
                    (now,)).fetchall()]
            cur = conn.execute("UPDATE input_requests SET status = 'expired' WHERE status = 'pending' "
                               "AND expires_at IS NOT NULL AND expires_at <= ?", (now,))
            conn.commit()
            if return_rows:
                for r in rows:
                    r["status"] = "expired"
                return rows
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: expiry failed: {e}")
        return [] if return_rows else 0


def stats_for_user(user_uuid: str, *, profile_id: Optional[str] = None, days: int = 30,
                   db_path: Optional[str] = None) -> Dict[str, Any]:
    """The scoreboard's readings (PROP-011 §4.8): questions asked, answered, declined, expired
    and the median time to answer — for one agent or all of them — over the last `days`."""
    expire_due(db_path)
    out: Dict[str, Any] = {"asked": 0, "pending": 0, "accepted": 0, "declined": 0, "cancelled": 0,
                           "expired": 0, "superseded": 0, "by_raiser": {}, "median_answer_seconds": None, "days": days}
    try:
        conn = _connect(db_path)
        try:
            since = _iso(_now() - timedelta(days=max(1, int(days))))
            q = "SELECT status, raised_by, created_at, answered_at FROM input_requests WHERE user_uuid = ? AND created_at >= ?"
            args: List[Any] = [user_uuid, since]
            if profile_id:
                q += " AND profile_id = ?"
                args.append(profile_id)
            secs = []
            for status, raised_by, created, answered in conn.execute(q, args).fetchall():
                out["asked"] += 1
                if status in out:
                    out[status] += 1
                out["by_raiser"][raised_by or "?"] = out["by_raiser"].get(raised_by or "?", 0) + 1
                if status == "accepted" and created and answered:
                    try:
                        secs.append((datetime.fromisoformat(answered) - datetime.fromisoformat(created)).total_seconds())
                    except Exception:  # noqa: BLE001
                        pass
            if secs:
                secs.sort()
                out["median_answer_seconds"] = int(secs[len(secs) // 2])
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: stats failed: {e}")
    return out


# ── reading ──────────────────────────────────────────────────────────────────

def get(rid: str, user_uuid: Optional[str] = None, *, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One record (the caller's, when `user_uuid` is given); an expired pending one is flipped
    on read. No `resume` — the binding is issued once, to the turn that raised it, and to the
    owner's own reads (`get_for_owner`)."""
    try:
        conn = _connect(db_path)
        try:
            r = conn.execute(f"SELECT {_COLS} FROM input_requests WHERE id = ?", (rid,)).fetchone()
            if r is None:
                return None
            rec = _row(r)
            if user_uuid is not None and rec["user_uuid"] != user_uuid:
                return None
            if rec["status"] == "pending" and rec.get("expires_at") and rec["expires_at"] <= _iso(_now()):
                _expire_row(conn, rid)
                rec["status"] = "expired"
            return rec
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: get failed: {e}")
        return None


def get_for_owner(rid: str, user_uuid: str, *, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The owner's own read carries a fresh binding (the chat card and a REST client that lost
    the first one answer with it); only the owner, only while pending."""
    rec = get(rid, user_uuid, db_path=db_path)
    if rec and rec["status"] == "pending":
        rec["resume"] = mint_resume(rec)
    return rec


def list_for_user(user_uuid: str, *, status: Optional[str] = "pending", session_id: Optional[str] = None,
                  limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    expire_due(db_path)
    try:
        conn = _connect(db_path)
        try:
            q = f"SELECT {_COLS} FROM input_requests WHERE user_uuid = ?"
            args: List[Any] = [user_uuid]
            if status and status != "all":
                q += " AND status = ?"
                args.append(status)
            if session_id:
                q += " AND session_id = ?"
                args.append(session_id)
            q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
            args.append(int(limit))
            return [_row(r) for r in conn.execute(q, args).fetchall()]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"input-requests: list failed: {e}")
        return []


def pending_for_session(session_id: str, user_uuid: str, *, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rows = list_for_user(user_uuid, status="pending", session_id=session_id, limit=1, db_path=db_path)
    return rows[0] if rows else None


def pending_count(user_uuid: str, *, db_path: Optional[str] = None) -> int:
    expire_due(db_path)
    try:
        conn = _connect(db_path)
        try:
            r = conn.execute("SELECT COUNT(*) FROM input_requests WHERE user_uuid = ? AND status = 'pending'",
                             (user_uuid,)).fetchone()
            return int(r[0]) if r else 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0


def public_view(rec: Dict[str, Any], *, with_resume: bool = False) -> Dict[str, Any]:
    """What a face (chat card, REST task, Inbox) is handed: the question, never the digest."""
    keys = ("id", "session_id", "turn", "task_id", "origin", "raised_by", "profile_id", "profile_tag", "source",
            "parent_id", "kind", "items", "why", "default", "status", "answer", "answered_by", "answered_via",
            "answered_at", "superseded_by", "expires_at", "created_at")
    out = {k: rec.get(k) for k in keys}
    if with_resume and rec.get("resume"):
        out["resume"] = rec["resume"]
    return out
