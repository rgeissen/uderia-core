"""Database health: corruption made loud, and the daily verified backup.

FOUND (production, 2026-10-29): ``tda_auth.db`` went ``file is not a database`` at 11:05 UTC
and the platform ran on the damaged file for eight hours. Every unreadable read was handled
correctly — ``auth.database.invalidate_on_unreadable_file`` dropped the pool so the next
request reconnected — but only as an ERROR log line; logins failed, and nobody was told. The
recovery point was an upgrade backup that happened to exist.

Two duties, one module:

* **Loud.** ``report_unreadable`` is called by that hook on every unreadable-file error. It
  never touches the database (it was just found unreadable): it records the state in memory,
  logs CRITICAL (the first time, then every ``ALERT_INTERVAL_S``), and pushes a ``db_health``
  notice over every open notification stream — the app banner in each connected browser.
* **Backed up.** ``run_backup`` is the daily platform job (``platform-db-backup``): a
  consistent copy while the app runs (``schema_ledger.vacuum_copy`` — ``VACUUM INTO`` + a
  ``quick_check`` of the copy), the newest ``backup_keep()`` kept in ``backup_dir()``. The copy
  reads every page of the live file, so it is also the daily integrity read: a damaged page
  fails it, and a failure is reported like any unreadable read. The backups are finished files
  — the place an off-host copy (a NAS job) should read, never the live database.

``status()`` is what the admin endpoint (``GET /v1/admin/db-health``) shows.
"""
from __future__ import annotations

import datetime
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("quart.app")

ALERT_INTERVAL_S = 600          # a still-unreadable database is re-announced every 10 minutes
DEFAULT_KEEP = 7                # daily backups kept
BACKUP_PREFIX = "tda_auth-"
NOTICE_TYPE = "db_health"

_LOCK = threading.Lock()
_STATE = {
    "state": "ok",              # ok · unreadable
    "first_seen": None, "last_seen": None, "count": 0, "last_error": None,
    "last_alert_at": 0.0,
    "last_backup": None,        # {at, path, bytes, seconds, ok, error?}
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _db_path() -> Path:
    from trusted_data_agent.auth.database import DATABASE_URL
    from trusted_data_agent.core import schema_ledger
    return schema_ledger._path(DATABASE_URL)


def backup_dir() -> Path:
    """``TDA_DB_BACKUP_DIR``, else ``backups/db`` beside the database."""
    env = (os.environ.get("TDA_DB_BACKUP_DIR") or "").strip()
    return Path(env) if env else _db_path().parent / "backups" / "db"


def backup_keep() -> int:
    try:
        return max(1, int(os.environ.get("TDA_DB_BACKUP_KEEP") or DEFAULT_KEEP))
    except ValueError:
        return DEFAULT_KEEP


# ── loud ─────────────────────────────────────────────────────────────────────────────────────

def _summary(error: str) -> str:
    return ("The platform database is unreadable (" + (error or "unknown error")[:80] + "). "
            "Requests that need it are failing — an administrator must restore it from the latest "
            "verified backup.")


def _broadcast(summary: str) -> None:
    """Push the notice to every open notification stream. No database read: the queues live in
    memory, keyed by the user each stream belongs to."""
    try:
        from trusted_data_agent.core.config import APP_STATE
        from trusted_data_agent.core.trust_events import broadcast_notice
        for user_uuid in list((APP_STATE.get("notification_queues") or {}).keys()):
            broadcast_notice(user_uuid, "db_unreadable", summary, severity="danger",
                             notice_type=NOTICE_TYPE, extra={"state": "unreadable"})
    except Exception:  # noqa: BLE001 — an alert must never raise into the failing request
        pass


def report_unreadable(error: str) -> None:
    """The database could not be read (called by the pool's error hook and by a failed backup).
    Records it; alerts the first time and every ``ALERT_INTERVAL_S`` after. Never raises."""
    try:
        now = time.monotonic()
        with _LOCK:
            _STATE["count"] += 1
            _STATE["last_seen"] = _now_iso()
            _STATE["last_error"] = (error or "")[:300]
            if _STATE["state"] != "unreadable":
                _STATE["state"] = "unreadable"
                _STATE["first_seen"] = _STATE["last_seen"]
            due = (now - _STATE["last_alert_at"]) >= ALERT_INTERVAL_S or _STATE["last_alert_at"] == 0.0
            if due:
                _STATE["last_alert_at"] = now
            count = _STATE["count"]
        if due:
            logger.critical(f"DATABASE UNREADABLE ({count} failed reads since {_STATE['first_seen']}): "
                            f"{(error or '')[:160]} — restore from {backup_dir()}")
            _broadcast(_summary(error))
    except Exception:  # noqa: BLE001
        pass


def report_readable() -> None:
    """A verified read of the whole file (a successful backup) clears the unreadable state."""
    with _LOCK:
        if _STATE["state"] != "ok":
            logger.warning(f"Database readable again (after {_STATE['count']} failed reads)")
        _STATE.update({"state": "ok", "first_seen": None, "count": 0, "last_alert_at": 0.0})


def status() -> dict:
    with _LOCK:
        s = {k: v for k, v in _STATE.items() if k != "last_alert_at"}
    s["backup_dir"] = str(backup_dir())
    s["backup_keep"] = backup_keep()
    s["backups"] = [{"path": str(p), "bytes": p.stat().st_size} for p in _backups(backup_dir())]
    return s


# ── backed up ────────────────────────────────────────────────────────────────────────────────

def _backups(dest: Path) -> list:
    if not dest.is_dir():
        return []
    return sorted(dest.glob(f"{BACKUP_PREFIX}*.db"), reverse=True)


def run_backup(db: Optional[str | Path] = None, dest: Optional[str | Path] = None,
               keep: Optional[int] = None, now: Optional[datetime.datetime] = None) -> dict:
    """The daily verified backup. Returns ``{path, bytes, seconds, kept, pruned}``; raises (after
    recording and reporting the failure) when the copy cannot be taken or fails its check."""
    from trusted_data_agent.core import schema_ledger
    source = Path(db) if db is not None else _db_path()
    dest = Path(dest) if dest is not None else backup_dir()
    keep = keep if keep is not None else backup_keep()
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = dest / f"{BACKUP_PREFIX}{stamp}.db"
    t0 = time.monotonic()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        need = source.stat().st_size
        free = shutil.disk_usage(dest).free
        if free < need * 1.1:
            raise OSError(f"{free // 1_000_000} MB free in {dest}, a backup of {need // 1_000_000} MB needs more")
        if target.exists():
            raise FileExistsError(str(target))
        schema_ledger.vacuum_copy(source, target)
    except Exception as e:
        with _LOCK:
            _STATE["last_backup"] = {"at": _now_iso(), "path": str(target), "ok": False, "error": str(e)[:300]}
        report_unreadable(f"daily backup failed: {e}")
        raise
    pruned = []
    for old in _backups(dest)[keep:]:
        try:
            old.unlink()
            pruned.append(old.name)
        except OSError:
            pass
    result = {"path": str(target), "bytes": target.stat().st_size,
              "seconds": round(time.monotonic() - t0, 1), "kept": len(_backups(dest)), "pruned": pruned}
    with _LOCK:
        _STATE["last_backup"] = {"at": _now_iso(), "ok": True, **{k: result[k] for k in ("path", "bytes", "seconds")}}
    report_readable()
    logger.info(f"Database backup verified: {target} ({result['bytes'] // 1_000_000} MB, {result['seconds']} s)")
    return result
