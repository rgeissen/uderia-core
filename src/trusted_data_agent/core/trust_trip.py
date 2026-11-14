"""Trust Trip — the resettable leg on the Trust Circle (marker store).

The speedometer model: the odometer (the whole record) never resets; a *trip
reset* writes a per-agent MARKER, and the trip lens is a pure read-time filter
("readings since the marker"). Design: docs/roadmap/TRUST_TRIP_RESET.md.

THE INVARIANTS THIS MODULE OWNS
-------------------------------
- **A marker, never a deletion (W1).** This store only ever appends. There is
  deliberately no update and no delete: spans, turns, verdicts and incidents are
  untouched, and past markers remain as the leg logbook. A "reset" that could be
  edited afterwards would be a resettable audit trail — the exact thing a trust
  surface must not have.
- **An audited act (W2).** Every marker records who set it, when (server clock,
  never the client's), the operator's reason, and the agent's integrity state at
  that moment (W8) — so the story can narrate *why* a leg began from a record
  rather than memory, and a reset can never be performed silently.
- **Read fail-open, write fail-loud.** Reads degrade to "no markers" (a lens
  that cannot resolve must fall back to the whole record, never break a
  surface). The WRITE is different: unlike ``trust_events`` — where recording
  rides on some other action that must not be disturbed — here the reset IS the
  action, so a write failure raises and the endpoint reports it honestly.

NOT part of the removable Ontology Suite (``core/trust/``): like
``trust_events``, the trip lens reads execution measurements that exist without
the trust membrane, so it must survive the suite's removal.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

# Append-only by design: no UPDATE, no DELETE statement exists in this module.
_DDL = """
CREATE TABLE IF NOT EXISTS trust_trip_markers (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    created_at_ns   INTEGER NOT NULL,
    user_id         TEXT NOT NULL,
    profile_id      TEXT,
    profile_tag     TEXT NOT NULL,
    note            TEXT,
    integrity_state TEXT
);
"""

#: The operator's reason is displayed on trust surfaces — bounded so a paste
#: cannot become the panel.
NOTE_MAX_CHARS = 280


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None):
    conn = sqlite_util.connect(_db_path(db_path))
    conn.execute(_DDL)
    conn.commit()
    return conn


def _row_to_marker(r) -> Dict:
    return {"id": r[0], "created_at": r[1], "created_at_ns": int(r[2]),
            "profile_id": r[3], "profile_tag": r[4], "note": r[5],
            "integrity_state": r[6]}


def reset_trip(user_uuid: str, profile_tag: str, *,
               profile_id: Optional[str] = None,
               note: Optional[str] = None,
               integrity_state: Optional[str] = None,
               db_path: Optional[str] = None) -> Dict:
    """Start a new leg for one agent — append a marker, return it.

    Raises on a store failure (the reset IS the act; see the module docstring)
    and on an empty tag (a marker that matches no spans is not a leg).
    ``created_at_ns`` is the server clock in epoch nanoseconds — the same unit
    as a span's ``start_time_unix_nano``, so the lens filter is one comparison.
    """
    # Whitespace first, THEN the sigil, then whitespace again — "  @ " must
    # normalize to empty, not to a lone "@" that would match no span ever.
    tag = (profile_tag or "").strip().lstrip("@").strip()
    if not tag:
        raise ValueError("a trip reset needs the agent's tag")
    clean_note = (note or "").strip()[:NOTE_MAX_CHARS] or None
    marker = {
        "id": str(uuid.uuid4()),
        "created_at": _now_iso(),
        "created_at_ns": time.time_ns(),
        "profile_id": profile_id,
        "profile_tag": tag,
        "note": clean_note,
        "integrity_state": integrity_state,
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO trust_trip_markers (id, created_at, created_at_ns, user_id,"
            " profile_id, profile_tag, note, integrity_state) VALUES (?,?,?,?,?,?,?,?)",
            (marker["id"], marker["created_at"], marker["created_at_ns"], user_uuid,
             marker["profile_id"], marker["profile_tag"], marker["note"],
             marker["integrity_state"]))
        conn.commit()
    finally:
        conn.close()
    return marker


def latest_markers(user_uuid: str, db_path: Optional[str] = None) -> Dict[str, Dict]:
    """The CURRENT leg boundary per agent: ``{tag: marker}``, latest marker wins.

    Fail-open to ``{}`` — a lens that cannot resolve falls back to the whole
    record; it must never break the surface reading it. ``leg_n`` counts the
    agent's markers so a surface can say "leg 3" without a second query.
    """
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id, created_at, created_at_ns, profile_id, profile_tag,"
                " note, integrity_state FROM trust_trip_markers WHERE user_id = ?"
                " ORDER BY created_at_ns ASC", (user_uuid,)).fetchall()
        finally:
            conn.close()
        out: Dict[str, Dict] = {}
        counts: Dict[str, int] = {}
        for r in rows:                      # ascending ⇒ the last write per tag wins
            m = _row_to_marker(r)
            counts[m["profile_tag"]] = counts.get(m["profile_tag"], 0) + 1
            m["leg_n"] = counts[m["profile_tag"]]
            out[m["profile_tag"]] = m
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust-trip: latest_markers failed (lens falls back to whole record): {e}")
        return {}


def list_markers(user_uuid: str, profile_tag: Optional[str] = None, *,
                 limit: int = 50, db_path: Optional[str] = None) -> List[Dict]:
    """The leg logbook, newest first. Fail-open to ``[]``."""
    try:
        tag = (profile_tag or "").lstrip("@").strip() or None
        conn = _connect(db_path)
        try:
            q = ("SELECT id, created_at, created_at_ns, profile_id, profile_tag,"
                 " note, integrity_state FROM trust_trip_markers WHERE user_id = ?")
            args: list = [user_uuid]
            if tag:
                q += " AND profile_tag = ?"
                args.append(tag)
            q += " ORDER BY created_at_ns DESC LIMIT ?"
            args.append(int(limit))
            rows = conn.execute(q, args).fetchall()
        finally:
            conn.close()
        return [_row_to_marker(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust-trip: list_markers failed: {e}")
        return []


# ── the lens filter — ONE pure implementation (W4) ───────────────────────────
def span_ns(span: dict) -> Optional[int]:
    """A span's start time in epoch ns, or None when the span does not carry one
    (an untimed span can never prove it belongs to a leg, so it stays out)."""
    v = (span or {}).get("start_time_unix_nano")
    return int(v) if isinstance(v, (int, float)) else None


def spans_since(spans: list, markers_by_tag: Dict[str, Dict]) -> list:
    """The trip subset: spans belonging to a MARKED agent's current leg.

    This is the whole lens, as one pure function, so the endpoint's second
    aggregation pass runs the SAME ``online_quality_by_agent`` over this subset
    and the trip cell's shape is identical to the total cell's by construction
    (W4 — never a second aggregator). Spans of unmarked agents are excluded
    entirely: they have no leg, and their trip reading IS their whole record,
    which the total cell already carries.
    """
    out = []
    for s in spans or []:
        attrs = (s or {}).get("attributes") or {}
        tag = (attrs.get("agent.profile_tag") or "").lstrip("@")
        m = markers_by_tag.get(tag)
        if not m:
            continue
        t = span_ns(s)
        if t is not None and t >= int(m.get("created_at_ns") or 0):
            out.append(s)
    return out


def spans_between(spans: list, tag: str, start_ns: Optional[int],
                  end_ns: int) -> list:
    """One agent's spans inside ``[start_ns, end_ns)`` — the PREVIOUS leg (W7).

    ``start_ns=None`` means "from the start of the retained window" (a first
    reset has pre-history but no earlier marker). End-exclusive so the boundary
    span belongs to exactly one leg.
    """
    tag = (tag or "").lstrip("@")
    out = []
    for s in spans or []:
        attrs = (s or {}).get("attributes") or {}
        if (attrs.get("agent.profile_tag") or "").lstrip("@") != tag:
            continue
        t = span_ns(s)
        if t is None:
            continue
        if (start_ns is None or t >= int(start_ns)) and t < int(end_ns):
            out.append(s)
    return out
