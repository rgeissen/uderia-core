"""
Audit integrity — append-only audit tables and an external (SIEM) copy.

1. Append-only at the database. SQLite triggers on every audit table refuse
   UPDATE and refuse DELETE of any row younger than the retention period, so
   neither application code nor an ad-hoc SQL session can rewrite or remove
   recent audit history. The one permitted update is the database's own
   ``ON DELETE SET NULL`` on ``audit_logs.user_id`` when an account is deleted
   (the row, its action, time and details stay).

   Honest limit: whoever can run DDL on the database file can drop a trigger.
   The SIEM copy below is the control for that case: records already shipped
   off-host cannot be rewritten here.

2. Retention. ``TDA_AUDIT_RETENTION_DAYS`` (default and minimum 365). Purges
   only ever remove rows older than this; the triggers enforce it.

3. SIEM export. Every audit event is also written as one JSON object to:
   - syslog (RFC 5424 framing via the stdlib handler) when
     ``TDA_AUDIT_SYSLOG`` is set: ``udp://host:514``, ``tcp://host:6514`` or
     ``/dev/log``;
   - a JSON-lines file when ``TDA_AUDIT_JSONL`` names a path.
   Both are off by default. Export never blocks or fails the audited action.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
from typing import Iterable, Optional

logger = logging.getLogger("quart.app")

MIN_RETENTION_DAYS = 365

# table -> (timestamp column, columns an update may NOT change; None = no updates at all)
_AUDIT_TABLES = {
    "audit_logs": "timestamp",
    "sso_sync_events": "synced_at",
    "kg_lifecycle_audit": "created_at",
    "pack_lifecycle_audit": "created_at",
}


def retention_days() -> int:
    raw = os.environ.get("TDA_AUDIT_RETENTION_DAYS", "").strip()
    try:
        days = int(raw) if raw else MIN_RETENTION_DAYS
    except ValueError:
        logger.warning(f"Ignoring invalid TDA_AUDIT_RETENTION_DAYS={raw!r}")
        days = MIN_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, days)


def _trigger_sql(table: str, ts_col: str, days: int, columns: Optional[set] = None) -> list[str]:
    no_update = f"""
CREATE TRIGGER IF NOT EXISTS {table}_append_only_update
BEFORE UPDATE ON {table}
{_update_exception(table, columns)}
BEGIN
    SELECT RAISE(ABORT, 'audit records are append-only ({table})');
END"""
    no_recent_delete = f"""
CREATE TRIGGER IF NOT EXISTS {table}_retention_delete
BEFORE DELETE ON {table}
WHEN OLD.{ts_col} IS NULL OR julianday(OLD.{ts_col}) > julianday('now', '-{days} days')
BEGIN
    SELECT RAISE(ABORT, 'audit records are retained for {days} days ({table})');
END"""
    return [f"DROP TRIGGER IF EXISTS {table}_append_only_update",
            f"DROP TRIGGER IF EXISTS {table}_retention_delete",
            no_update, no_recent_delete]


def _update_exception(table: str, columns: Optional[set] = None) -> str:
    if table != "audit_logs":
        return ""
    # Only the account-deletion nulling of user_id is allowed; everything else stays —
    # including the event's structured detail where the database has that column.
    meta = "\n    AND NEW.metadata_json IS OLD.metadata_json" if columns is None or "metadata_json" in columns else ""
    return f"""WHEN NOT (
    NEW.user_id IS NULL AND OLD.user_id IS NOT NULL
    AND NEW.id IS OLD.id AND NEW.action IS OLD.action AND NEW.resource IS OLD.resource
    AND NEW.status IS OLD.status AND NEW.ip_address IS OLD.ip_address
    AND NEW.user_agent IS OLD.user_agent AND NEW.details IS OLD.details
    AND NEW.timestamp IS OLD.timestamp{meta}
)"""


def install_triggers(conn, tables: Optional[Iterable[str]] = None) -> list[str]:
    """
    Install (or refresh, with the current retention) the triggers on every
    listed audit table that exists in this database. Returns the tables done.
    """
    days = retention_days()
    wanted = list(tables) if tables is not None else list(_AUDIT_TABLES)
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    done = []
    for table in wanted:
        if table not in existing or table not in _AUDIT_TABLES:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for stmt in _trigger_sql(table, _AUDIT_TABLES[table], days, cols):
            conn.execute(stmt)
        done.append(table)
    conn.commit()
    return done


def install_all(db_path: str) -> list[str]:
    from trusted_data_agent.core import sqlite_util
    conn = sqlite_util.connect(db_path)
    try:
        done = install_triggers(conn)
        if done:
            logger.info(f"Audit integrity: append-only triggers on {', '.join(done)} "
                        f"(retention {retention_days()} days)")
        return done
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SIEM export
# ---------------------------------------------------------------------------

_siem_logger: Optional[logging.Logger] = None
_siem_configured = False


def _build_syslog_handler(target: str) -> Optional[logging.Handler]:
    if target.startswith("/"):
        return logging.handlers.SysLogHandler(address=target)
    proto, _, rest = target.partition("://")
    host, _, port = rest.rpartition(":")
    if not host:
        host, port = rest, "514"
    socktype = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    return logging.handlers.SysLogHandler(address=(host, int(port)), socktype=socktype)


def _siem() -> Optional[logging.Logger]:
    global _siem_logger, _siem_configured
    if _siem_configured:
        return _siem_logger
    _siem_configured = True
    syslog_target = os.environ.get("TDA_AUDIT_SYSLOG", "").strip()
    jsonl_path = os.environ.get("TDA_AUDIT_JSONL", "").strip()
    if not syslog_target and not jsonl_path:
        return None
    lg = logging.getLogger("uderia.audit.siem")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        if syslog_target:
            h = _build_syslog_handler(syslog_target)
            h.setFormatter(logging.Formatter("uderia-audit: %(message)s"))
            lg.addHandler(h)
        if jsonl_path:
            fh = logging.FileHandler(jsonl_path)
            fh.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(fh)
    except Exception as e:
        logger.error(f"Audit SIEM export could not be configured: {e}")
        return None
    _siem_logger = lg
    return lg


def export_event(record: dict) -> None:
    """Ship one audit record to the configured SIEM sinks. Never raises."""
    try:
        lg = _siem()
        if lg is None:
            return
        lg.info(json.dumps(record, default=str, sort_keys=True))
    except Exception as e:
        logger.debug(f"Audit SIEM export failed (event still stored): {e}")


def _reset_for_tests() -> None:
    global _siem_logger, _siem_configured
    if _siem_logger is not None:
        for h in list(_siem_logger.handlers):
            _siem_logger.removeHandler(h)
            h.close()
    _siem_logger = None
    _siem_configured = False
