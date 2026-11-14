"""
Persistence for file stores and files (``schema/45_file_stores.sql``). Every connection goes
through ``core.sqlite_util.connect`` (the process-wide serialization contract).

Store settings — including credentials — are sealed with ``crypto.seal_text`` under a context
naming the store, so a row copied to another store id does not open.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from trusted_data_agent.core import sqlite_util

from . import crypto
from .catalog import MASK, SECRET_KEYS  # the one declaration of secret fields
from .settings import PLATFORM_LOCAL_ID, _db_path

logger = logging.getLogger("quart.app")

_ENSURED: Dict[str, bool] = {}
_ENSURE_LOCK = threading.Lock()

SCHEMA = Path(__file__).resolve().parents[3] / "schema" / "45_file_stores.sql"

FILE_COLUMNS = (
    "id", "user_uuid", "store_config_id", "object_key", "filename", "media_type", "size_bytes",
    "content_hmac", "wrapped_key", "key_version", "source_kind", "source_anchor", "pair_id",
    "lifetime", "kept_at", "origin_kind", "scheduled_task_id", "scheduled_run_id", "flow_id",
    "flow_run_id", "flow_node_id", "component_id", "instance_id", "source_json", "profile_id",
    "profile_tag", "profile_type", "session_id", "session_title", "turn_id", "question", "bom_hash",
    "bom_json", "data_derived", "seen_at", "created_at", "deleted_at", "purge_after",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    conn = sqlite_util.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    """Apply the schema once per database per process (idempotent DDL)."""
    path = _db_path()
    if _ENSURED.get(path):
        return
    with _ENSURE_LOCK:
        if _ENSURED.get(path):
            return
        conn = sqlite_util.connect(path)
        try:
            conn.executescript(SCHEMA.read_text())
            cols = {r[1] for r in conn.execute("PRAGMA table_info(file_store_configs)").fetchall()}
            if "capacity_mb" not in cols:     # a table created before capacity governance
                conn.execute("ALTER TABLE file_store_configs ADD COLUMN capacity_mb INTEGER")
            fcols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
            for col in ("component_id", "instance_id", "source_json"):   # phase 3: component sources
                if col not in fcols:
                    conn.execute(f"ALTER TABLE files ADD COLUMN {col} TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_user_flow ON files (user_uuid, flow_id)")
            conn.commit()
        finally:
            conn.close()
        _ENSURED[path] = True


# ── store configurations ─────────────────────────────────────────────────────

def platform_local_config() -> Dict[str, Any]:
    from .settings import platform_local_capacity_mb
    return {"id": PLATFORM_LOCAL_ID, "name": "Platform local", "backend_type": "local", "config": {},
            "encrypt": True, "is_default": False, "builtin": True, "last_test_ok": None,
            "last_test_at": None, "last_test_message": None, "capacity_mb": platform_local_capacity_mb()}


def _store_row(r: sqlite3.Row, with_secrets: bool) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if r["config_encrypted"]:
        try:
            cfg = json.loads(crypto.open_text(r["config_encrypted"], f"store-config:{r['id']}"))
        except Exception:  # noqa: BLE001
            cfg = {"_unreadable": True}
    if not with_secrets:
        cfg = {k: (MASK if k in SECRET_KEYS and v else v) for k, v in cfg.items()}
    return {"id": r["id"], "name": r["name"], "backend_type": r["backend_type"], "config": cfg,
            "encrypt": bool(r["encrypt"]), "is_default": bool(r["is_default"]), "builtin": False,
            "capacity_mb": r["capacity_mb"] if "capacity_mb" in r.keys() else None,
            "last_test_ok": None if r["last_test_ok"] is None else bool(r["last_test_ok"]),
            "last_test_at": r["last_test_at"], "last_test_message": r["last_test_message"],
            "created_by": r["created_by"], "created_at": r["created_at"], "updated_at": r["updated_at"]}




def list_store_configs(with_secrets: bool = False) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM file_store_configs ORDER BY created_at").fetchall()
        stores = [_store_row(r, with_secrets) for r in rows]
    finally:
        conn.close()
    local = platform_local_config()
    local["is_default"] = not any(s["is_default"] for s in stores)
    return [local] + stores


def get_store_config(store_id: str, with_secrets: bool = True) -> Optional[Dict[str, Any]]:
    if store_id == PLATFORM_LOCAL_ID:
        local = platform_local_config()
        local["is_default"] = not any(s["is_default"] for s in list_store_configs()[1:])
        return local
    ensure_tables()
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM file_store_configs WHERE id=?", (store_id,)).fetchone()
        return _store_row(r, with_secrets) if r else None
    finally:
        conn.close()


def default_store_id() -> str:
    for s in list_store_configs():
        if s["is_default"]:
            return s["id"]
    return PLATFORM_LOCAL_ID


def save_store_config(cfg: Dict[str, Any], admin_uuid: Optional[str] = None) -> Dict[str, Any]:
    """Create (no id) or update a store. Secret fields left as the mask keep their value."""
    ensure_tables()
    sid = cfg.get("id") or f"fs-{uuid.uuid4().hex[:12]}"
    if sid == PLATFORM_LOCAL_ID:
        raise ValueError("the platform local store cannot be changed")
    existing = get_store_config(sid, with_secrets=True) if cfg.get("id") else None
    if cfg.get("id") and not existing:
        raise ValueError("store not found")
    merged = dict((existing or {}).get("config") or {})
    for k, v in (cfg.get("config") or {}).items():
        if k in SECRET_KEYS and v == MASK:
            continue
        merged[k] = v
    sealed = crypto.seal_text(json.dumps(merged), f"store-config:{sid}")
    name = (cfg.get("name") or (existing or {}).get("name") or "").strip()
    if not name:
        raise ValueError("a store needs a name")
    backend_type = cfg.get("backend_type") or (existing or {}).get("backend_type")
    encrypt = bool(cfg.get("encrypt", (existing or {}).get("encrypt", True)))
    capacity = cfg.get("capacity_mb", (existing or {}).get("capacity_mb"))
    if capacity in ("", 0, "0"):
        capacity = None
    if capacity is not None:
        try:
            capacity = int(float(capacity))
        except (TypeError, ValueError):
            raise ValueError("capacity_mb must be a number of MB (empty = no limit)")
        if capacity < 1:
            raise ValueError("capacity_mb must be positive (empty = no limit)")
    conn = _connect()
    try:
        if existing:
            conn.execute("UPDATE file_store_configs SET name=?, backend_type=?, config_encrypted=?, encrypt=?, capacity_mb=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, backend_type, sealed, int(encrypt), capacity, sid))
        else:
            conn.execute("INSERT INTO file_store_configs (id, name, backend_type, config_encrypted, encrypt, capacity_mb, created_by) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, name, backend_type, sealed, int(encrypt), capacity, admin_uuid))
        conn.commit()
    finally:
        conn.close()
    return get_store_config(sid, with_secrets=False)


def set_default_store(store_id: str) -> None:
    ensure_tables()
    conn = _connect()
    try:
        conn.execute("UPDATE file_store_configs SET is_default=0")
        if store_id != PLATFORM_LOCAL_ID:
            conn.execute("UPDATE file_store_configs SET is_default=1 WHERE id=?", (store_id,))
        conn.commit()
    finally:
        conn.close()


def record_test(store_id: str, ok: bool, message: str) -> None:
    if store_id == PLATFORM_LOCAL_ID:
        return
    conn = _connect()
    try:
        conn.execute("UPDATE file_store_configs SET last_test_ok=?, last_test_at=?, last_test_message=? WHERE id=?",
                     (int(ok), now_iso(), message[:500], store_id))
        conn.commit()
    finally:
        conn.close()


def delete_store_config(store_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM file_store_configs WHERE id=?", (store_id,))
        conn.commit()
    finally:
        conn.close()


def store_usage() -> Dict[str, Dict[str, int]]:
    """Per store: live files and bytes (Recently deleted included — its bytes are still held)."""
    ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute("SELECT store_config_id, COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS b "
                            "FROM files GROUP BY store_config_id").fetchall()
        return {r["store_config_id"]: {"files": int(r["n"]), "bytes": int(r["b"])} for r in rows}
    finally:
        conn.close()


# ── files ────────────────────────────────────────────────────────────────────

def _file(r: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    d = {k: r[k] for k in r.keys()}
    d["data_derived"] = bool(d.get("data_derived"))
    return d


def insert_file(row: Dict[str, Any]) -> None:
    ensure_tables()
    cols = [c for c in FILE_COLUMNS if c in row]
    conn = _connect()
    try:
        conn.execute(f"INSERT INTO files ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                     tuple(row[c] for c in cols))
        conn.commit()
    finally:
        conn.close()


def get_file(file_id: str, user_uuid: Optional[str] = None) -> Optional[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        if user_uuid is None:
            r = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        else:
            r = conn.execute("SELECT * FROM files WHERE id=? AND user_uuid=?", (file_id, user_uuid)).fetchone()
        return _file(r)
    finally:
        conn.close()


def update_file(file_id: str, **fields) -> None:
    if not fields:
        return
    bad = set(fields) - set(FILE_COLUMNS)
    if bad:
        raise ValueError(f"unknown file columns {sorted(bad)}")
    conn = _connect()
    try:
        conn.execute(f"UPDATE files SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     tuple(fields.values()) + (file_id,))
        conn.commit()
    finally:
        conn.close()


def delete_file_row(file_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.execute("DELETE FROM file_uses WHERE file_id=?", (file_id,))
        conn.commit()
    finally:
        conn.close()


def query_files(user_uuid: str, *, deleted: bool = False, session_id: Optional[str] = None,
                lifetime: Optional[str] = None, extra_where: str = "", params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    where = ["user_uuid=?", "deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"]
    args: List[Any] = [user_uuid]
    if session_id is not None:
        where.append("session_id=?")
        args.append(session_id)
    if lifetime is not None:
        where.append("lifetime=?")
        args.append(lifetime)
    if extra_where:
        where.append(extra_where)
        args.extend(params)
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT * FROM files WHERE {' AND '.join(where)} ORDER BY created_at DESC, rowid DESC",
                            tuple(args)).fetchall()
        return [_file(r) for r in rows]
    finally:
        conn.close()


def user_bytes(user_uuid: str) -> int:
    ensure_tables()
    conn = _connect()
    try:
        r = conn.execute("SELECT COALESCE(SUM(size_bytes),0) AS b FROM files WHERE user_uuid=?", (user_uuid,)).fetchone()
        return int(r["b"])
    finally:
        conn.close()


def files_due_for_purge(now: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM files WHERE deleted_at IS NOT NULL AND purge_after IS NOT NULL "
                            "AND purge_after <= ?", (now or now_iso(),)).fetchall()
        return [_file(r) for r in rows]
    finally:
        conn.close()


def all_files_for_user(user_uuid: str) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        return [_file(r) for r in conn.execute("SELECT * FROM files WHERE user_uuid=?", (user_uuid,)).fetchall()]
    finally:
        conn.close()


def files_in_store(store_id: str) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        return [_file(r) for r in conn.execute("SELECT * FROM files WHERE store_config_id=?", (store_id,)).fetchall()]
    finally:
        conn.close()


def all_wrapped_files() -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        return [_file(r) for r in conn.execute("SELECT id, wrapped_key FROM files WHERE wrapped_key IS NOT NULL").fetchall()]
    finally:
        conn.close()


def record_use(file_id: str, user_uuid: str, session_id: str, turn_id: Optional[int]) -> None:
    ensure_tables()
    conn = _connect()
    try:
        conn.execute("INSERT OR IGNORE INTO file_uses (file_id, user_uuid, session_id, turn_id) VALUES (?, ?, ?, ?)",
                     (file_id, user_uuid, session_id, turn_id))
        conn.commit()
    finally:
        conn.close()


def uses_of(file_id: str) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT session_id, turn_id, used_at FROM file_uses WHERE file_id=? ORDER BY used_at", (file_id,)).fetchall()]
    finally:
        conn.close()


def purge_after_from(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def store_bytes(store_id: str) -> int:
    ensure_tables()
    conn = _connect()
    try:
        r = conn.execute("SELECT COALESCE(SUM(size_bytes),0) AS b FROM files WHERE store_config_id=?", (store_id,)).fetchone()
        return int(r["b"])
    finally:
        conn.close()


def usage_by_user(limit: int = 10) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute("SELECT user_uuid, COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS b FROM files "
                            "GROUP BY user_uuid ORDER BY b DESC LIMIT ?", (limit,)).fetchall()
        return [{"user_uuid": r["user_uuid"], "files": int(r["n"]), "bytes": int(r["b"])} for r in rows]
    finally:
        conn.close()


def total_bytes() -> int:
    ensure_tables()
    conn = _connect()
    try:
        return int(conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM files").fetchone()[0])
    finally:
        conn.close()
