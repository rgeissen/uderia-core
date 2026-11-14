"""
Binding OVERRIDES — the deployment's edits to the shipped binding table.

`__init__.bootstrap()` applies the shipped defaults first and then this layer on top, so
the answer to "what assesses answer support in flight?" is a row an operator changed, not
an import at a call site. That is the mechanism PROP-003's binding studio writes through.

THREE STATES, and conflating any two of them loses a real distinction:

    no row                  the shipped default applies
    row, assessor_key SET   the operator rebound this lane
    row, assessor_key NULL  the operator chose NOT ASSESSED — an explicit, audited act

`delete_override` therefore RESTORES THE DEFAULT; it is not the same act as writing a NULL
row, and a UI that offers only one of them cannot express "I want this lane silent".

FAIL DIRECTIONS (PROP-003 §8 R3). Reads fail open — a read that raises must not take the
admin panel down with it, so `load_overrides` returns `[]` and logs. Writes fail closed —
they are called only after `registry.bind` has already accepted the row, and a raising
write propagates so the REST layer can refuse rather than report a success that did not
persist.

`profile_id` is nullable and v1 always writes NULL (deployment-wide). The column exists
from day one so per-profile overlays stay an additive change rather than a migration of
live trust configuration.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

_DDL = """
CREATE TABLE IF NOT EXISTS assurance_bindings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property     TEXT NOT NULL,
    lane         TEXT NOT NULL,   -- Lane.name: IN_FLIGHT | IN_FLIGHT_TRIGGERED | POST_HOC
    assessor_key TEXT,            -- NULL = explicit unbind ("not assessed"), not "no row"
    profile_id   TEXT,            -- NULL = deployment-wide. v1 always NULL.
    bound_by     TEXT,            -- user uuid
    bound_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    note         TEXT,
    UNIQUE(property, lane, profile_id)
);
"""


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (APP_CONFIG.AUTH_DB_PATH or "tda_auth.db").replace("sqlite:///", "")


def _conn(db_path: Optional[str]):
    """Canonical access + self-applying schema (R1, R2)."""
    c = sqlite_util.connect(_db_path(db_path))
    c.execute(_DDL)
    c.commit()
    return c


def load_overrides(db_path: Optional[str] = None) -> List[Dict[str, object]]:
    """Every override row. Fails OPEN: `[]` on any error, logged.

    An override layer that cannot be read must leave the shipped defaults in force — that
    is a deployment running its shipped configuration, which is safe. Raising here would
    take down `bootstrap()`, and therefore every gate that resolves through the registry.
    """
    try:
        conn = _conn(db_path)
        try:
            rows = conn.execute(
                "SELECT property, lane, assessor_key, profile_id, bound_by, bound_at, note "
                "FROM assurance_bindings WHERE profile_id IS NULL "
                "ORDER BY property, lane").fetchall()
        finally:
            conn.close()
        return [{"property": r[0], "lane": r[1], "assessor_key": r[2], "profile_id": r[3],
                 "bound_by": r[4], "bound_at": r[5], "note": r[6]} for r in rows]
    except Exception as exc:  # noqa: BLE001 — see the fail-open rationale above
        logger.warning("assurance: could not read binding overrides (%s) — the shipped "
                       "defaults remain in force", exc)
        return []


def save_override(property: str, lane: str, assessor_key: Optional[str],
                  bound_by: Optional[str] = None, note: str = "",
                  db_path: Optional[str] = None) -> None:
    """Upsert one override. Fails CLOSED — the caller must surface a write failure."""
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO assurance_bindings (property, lane, assessor_key, profile_id, "
            "bound_by, note) VALUES (?, ?, ?, NULL, ?, ?) "
            "ON CONFLICT(property, lane, profile_id) DO UPDATE SET "
            "assessor_key=excluded.assessor_key, bound_by=excluded.bound_by, "
            "note=excluded.note, bound_at=CURRENT_TIMESTAMP",
            (property, lane, assessor_key, bound_by, note or ""))
        conn.commit()
    finally:
        conn.close()


def delete_override(property: str, lane: str, db_path: Optional[str] = None) -> bool:
    """Remove the row so the SHIPPED DEFAULT applies again. Returns whether one existed."""
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM assurance_bindings WHERE property = ? AND lane = ? "
            "AND profile_id IS NULL", (property, lane))
        conn.commit()
        return bool(getattr(cur, "rowcount", 0))
    finally:
        conn.close()


# ── control postures (PROP-003, third-party controls) ─────────────────────────
_DDL_POSTURE = """
CREATE TABLE IF NOT EXISTS assurance_control_postures (
    control_key TEXT PRIMARY KEY,
    posture     TEXT NOT NULL,
    set_by      TEXT,
    set_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    note        TEXT
);
"""


def control_posture(control_key: str, db_path: Optional[str] = None) -> Optional[str]:
    """The operator's posture for a control, or None = DORMANT (observe).

    Fails open to None, which is the direction that RECORDS rather than acts: unreadable
    configuration must never be the reason a control gains power.
    """
    try:
        conn = _conn(db_path)
        try:
            conn.execute(_DDL_POSTURE)
            row = conn.execute(
                "SELECT posture FROM assurance_control_postures WHERE control_key = ?",
                (control_key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("assurance: control posture unreadable (%s) — %s stays dormant",
                       exc, control_key)
        return None


def set_control_posture(control_key: str, posture: str, set_by: Optional[str] = None,
                        note: str = "", db_path: Optional[str] = None) -> None:
    """Persist a posture. Fails CLOSED — the caller must surface a write failure."""
    conn = _conn(db_path)
    try:
        conn.execute(_DDL_POSTURE)
        conn.execute(
            "INSERT INTO assurance_control_postures (control_key, posture, set_by, note) "
            "VALUES (?,?,?,?) ON CONFLICT(control_key) DO UPDATE SET "
            "posture=excluded.posture, set_by=excluded.set_by, note=excluded.note, "
            "set_at=CURRENT_TIMESTAMP", (control_key, posture, set_by, note or ""))
        conn.commit()
    finally:
        conn.close()


def clear_control_posture(control_key: str, db_path: Optional[str] = None) -> None:
    """Forget an operator's posture — the control returns to DORMANT.

    Distinct from setting `observe`, and deliberately so: the binding table already keeps
    "nobody has decided" apart from "somebody decided the default", because the two are
    different facts about a deployment. The same rule here means the studio can offer a
    real *restore*, and the deployment envelope stops carrying a row that no longer
    represents a decision.
    """
    conn = _conn(db_path)
    try:
        conn.execute(_DDL_POSTURE)
        conn.execute("DELETE FROM assurance_control_postures WHERE control_key = ?",
                     (control_key,))
        conn.commit()
    finally:
        conn.close()


def control_postures(db_path: Optional[str] = None) -> Dict[str, str]:
    try:
        conn = _conn(db_path)
        try:
            conn.execute(_DDL_POSTURE)
            return {r[0]: r[1] for r in conn.execute(
                "SELECT control_key, posture FROM assurance_control_postures")}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}


def get_override(property: str, lane: str,
                 db_path: Optional[str] = None) -> Optional[Dict[str, object]]:
    for row in load_overrides(db_path):
        if row["property"] == property and row["lane"] == lane:
            return row
    return None
