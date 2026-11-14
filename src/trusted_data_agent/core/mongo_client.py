"""
MongoDB execution — the ONE place a MongoDB connection is opened and a statement runs.
The Canvas connector and the Database Navigator's catalog provider both call it, so a
statement means the same thing on every surface. Synchronous (pymongo); callers run it on
a worker thread. What a statement may contain is decided by ``core.mongo_query``.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from . import mongo_query as MQ

logger = logging.getLogger("quart.app")

#: A statement's server-side time limit (the database stops it, not only the caller).
MAX_TIME_MS = 60_000
_CLIENT_CACHE_MAX = 16
_PRIV_TTL_S = 600

_lock = threading.Lock()
_clients: "OrderedDict[str, Any]" = OrderedDict()
_privileges: Dict[str, Tuple[float, Dict[str, Any]]] = {}


class MongoConnectionError(RuntimeError):
    pass


def client_for(creds: Dict[str, Any]):
    """A pooled MongoClient for a connection (keyed by address + login; the password hashed)."""
    try:
        import pymongo
    except ImportError as exc:
        raise MongoConnectionError("pymongo not installed. Run: pip install pymongo") from exc
    key = MQ.credential_key(creds)
    with _lock:
        c = _clients.get(key)
        if c is not None:
            _clients.move_to_end(key)
            return c
    uri, kw = MQ.build_client_kwargs(creds)
    c = pymongo.MongoClient(uri, **kw)
    with _lock:
        _clients[key] = c
        while len(_clients) > _CLIENT_CACHE_MAX:
            _k, old = _clients.popitem(last=False)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
    return c


def forget(creds: Dict[str, Any]) -> None:
    """Drop a connection's pooled client and cached rights (after its settings changed)."""
    key = MQ.credential_key(creds)
    with _lock:
        c = _clients.pop(key, None)
        _privileges.pop(key, None)
    if c is not None:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def database_of(creds: Dict[str, Any], spec: Optional[Dict[str, Any]] = None) -> str:
    """The database a statement runs in: the connection's, or the statement's own when the
    connection names none. A statement naming a DIFFERENT database than its connection is
    refused — the connection is the scope an owner granted."""
    conn_db = str(creds.get("database") or "").strip()
    want = str((spec or {}).get("database") or "").strip()
    if conn_db and want and want != conn_db:
        raise MQ.MongoQueryError(f"this connection is scoped to database '{conn_db}' — the statement names "
                                 f"'{want}'; drop 'database' or use a connection for '{want}'")
    db = want or conn_db
    if not db:
        raise MQ.MongoQueryError("the connection names no database — add \"database\": \"<name>\" to the statement")
    return db


def run_statement(creds: Dict[str, Any], statement: Any, *, max_rows: int = 0, readonly: bool = True,
                  max_time_ms: int = MAX_TIME_MS) -> Dict[str, Any]:
    """Run ONE statement → ``{columns, rows, truncated, spec, text, elapsed_ms}``. ``readonly``
    applies the read-only check before anything is sent; ``max_rows`` bounds the result on the
    server (one more row is fetched to detect truncation)."""
    t0 = time.monotonic()
    spec = MQ.prepare(statement, readonly=readonly)
    db = database_of(creds, spec)
    pipeline = MQ.bounded(spec["pipeline"], max_rows + 1 if max_rows else 0)
    client = client_for(creds)
    cur = client[db][spec["collection"]].aggregate(pipeline, maxTimeMS=int(max_time_ms), allowDiskUse=False)
    try:
        docs = list(cur)
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    truncated = bool(max_rows) and len(docs) > max_rows
    if max_rows:
        docs = docs[:max_rows]
    cols, rows = MQ.documents_to_rows(docs)
    return {"columns": cols, "rows": rows, "truncated": truncated, "spec": spec, "text": MQ.to_text(spec),
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def login_rights(creds: Dict[str, Any], *, refresh: bool = False) -> Dict[str, Any]:
    """What the connection's login may do → ``{can_write, reason, user}`` (cached 10 min).
    A probe that fails reports ``can_write: None`` with the reason — unknown is never read
    as read-only."""
    key = MQ.credential_key(creds)
    now = time.monotonic()
    if not refresh:
        hit = _privileges.get(key)
        if hit and now - hit[0] < _PRIV_TTL_S:
            return hit[1]
    try:
        st = client_for(creds).admin.command({"connectionStatus": 1, "showPrivileges": True})
        out = MQ.classify_privileges(st, str(creds.get("database") or "").strip() or None)
    except Exception as exc:  # noqa: BLE001
        out = {"can_write": None, "user": "", "reason": f"the login's rights could not be read ({str(exc)[:160]})"}
    _privileges[key] = (now, out)
    return out


def server_version(creds: Dict[str, Any]) -> str:
    return str(client_for(creds).server_info().get("version") or "")
