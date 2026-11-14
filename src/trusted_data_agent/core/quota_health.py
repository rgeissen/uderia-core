"""Consumption enforcement that could not run — fail-open, but never silent.

When the per-turn consumption check itself fails (e.g. its database is unavailable) the
executor lets the turn proceed (fail-open: an infrastructure hiccup must not lock every user
out) and counts the bypass in ``APP_STATE["consumption_enforcement_bypasses"]``. Until now the
count lived only in memory and a per-turn Live Status warning the end user saw — the operator,
who can act, saw nothing.

``report_bypass`` makes it operator-visible: a CRITICAL log line and a live banner to every
connected ADMIN, on the first bypass and then at most every ``ALERT_INTERVAL_S`` while it
continues. ``status()`` reports the running state. Never raises.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("quart.app")

ALERT_INTERVAL_S = 15 * 60
EVENT_TYPE = "consumption_enforcement_bypassed"

_LOCK = threading.Lock()
_STATE = {"count": 0, "first_seen": None, "last_seen": None, "last_error": None,
          "last_alert_at": 0.0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary(count: int, error: str) -> str:
    return (f"Consumption limits could not be checked {count} time(s) since "
            f"{_STATE['first_seen'] or 'startup'} ({(error or 'unknown error')[:80]}). "
            "Turns are proceeding without quota enforcement — check the consumption database.")


def _admin_queue_owners() -> list:
    """Connected users (open notification streams) who are admins. A failed lookup —
    plausible while the database is the problem — yields none: the log line still fires."""
    try:
        from trusted_data_agent.core.config import APP_STATE
        connected = list((APP_STATE.get("notification_queues") or {}).keys())
        if not connected:
            return []
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        with get_db_session() as s:
            rows = s.query(User).filter(User.id.in_(connected)).all()
            return [u.id for u in rows if getattr(u, "is_admin", False)]
    except Exception:  # noqa: BLE001
        return []


def report_bypass(user_uuid: str, error: str) -> None:
    """One fail-open bypass happened. Records it; alerts admins the first time and every
    ``ALERT_INTERVAL_S`` after. Never raises."""
    try:
        now = time.monotonic()
        with _LOCK:
            _STATE["count"] += 1
            _STATE["last_seen"] = _now_iso()
            _STATE["last_error"] = (error or "")[:300]
            if _STATE["first_seen"] is None:
                _STATE["first_seen"] = _STATE["last_seen"]
            due = _STATE["last_alert_at"] == 0.0 or (now - _STATE["last_alert_at"]) >= ALERT_INTERVAL_S
            if due:
                _STATE["last_alert_at"] = now
            count = _STATE["count"]
        if not due:
            return
        summary = _summary(count, error)
        logger.critical(f"CONSUMPTION ENFORCEMENT BYPASSED: {summary} (last user {user_uuid})")
        from trusted_data_agent.core.trust_events import broadcast_notice
        for admin in _admin_queue_owners():
            broadcast_notice(admin, EVENT_TYPE, summary, severity="danger")
    except Exception:  # noqa: BLE001 — an alert must never raise into the turn
        pass


def status() -> dict:
    with _LOCK:
        return {k: v for k, v in _STATE.items() if k != "last_alert_at"}


def _reset_for_tests() -> None:
    with _LOCK:
        _STATE.update({"count": 0, "first_seen": None, "last_seen": None,
                       "last_error": None, "last_alert_at": 0.0})
