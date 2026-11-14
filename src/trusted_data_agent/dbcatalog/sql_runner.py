"""Structured-row SQL runner over the platform's Database Connection store.

ONE seam every direct provider reads through. Teradata runs natively on
``teradatasql`` (rows come back typed, so the dictionary's numbers stay numbers);
every other driver delegates to the Canvas SQL connector — the SAME executor the
Canvas and the metric query endpoint already trust — and parses its tab-separated
result into rows. Read-only by construction: :func:`single_select_guard` refuses
anything but ONE SELECT before a preview or count ever runs.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import decimal
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

from .base import CatalogError

_TD_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dbcatalog-td")

Rows = Tuple[List[str], List[List[Any]]]


def jsonable(v: Any) -> Any:
    """Coerce a driver value into something JSON can carry, losing nothing legible."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()[:64]
    return str(v)


def single_select_guard(sql: str, dialect: str = "", allow_explain: bool = False) -> str:
    """Return the trimmed statement iff it is exactly ONE SELECT; raise otherwise.

    The navigator only ever runs SQL it composed itself (previews, counts, the
    dictionary reads), but the guard is structural so a future caller cannot smuggle a
    write through the same runner. Mirrors the metric query endpoint's rule and the
    preflight write classifier.

    ``allow_explain`` (the agent's query path only) also admits ``EXPLAIN <one SELECT>``:
    the explained statement must pass this same guard. ``EXPLAIN ANALYZE`` executes the
    statement and is refused."""
    body = (sql or "").strip().rstrip(";").strip()
    if not body:
        raise CatalogError("empty statement")
    m = re.match(r"^explain\b\s*", body, re.IGNORECASE)
    if m and allow_explain:
        inner = body[m.end():]
        if re.match(r"^analy[sz]e\b", inner, re.IGNORECASE):
            raise CatalogError("refusing EXPLAIN ANALYZE — it executes the statement")
        single_select_guard(inner, dialect)
        return body
    if ";" in body:
        raise CatalogError("refusing to run more than one statement")
    if not re.match(r"^(select|with|show|pragma|help)\b", body, re.IGNORECASE):
        raise CatalogError("refusing to run a non-SELECT statement")
    try:
        from trusted_data_agent.agent.preflight.catalog import sql_is_write
        if dialect:
            # classify in the statement's OWN dialect (a Postgres data-modifying CTE, a DuckDB
            # construct) — not the default Teradata parser
            from trusted_data_agent.agent.preflight import dialect as _pd
            with _pd.using(dialect):
                wrote = sql_is_write(body)
        else:
            wrote = sql_is_write(body)
        if wrote:
            raise CatalogError("refusing to run a statement that writes")
    except CatalogError:
        raise
    except Exception:  # noqa: BLE001 — the classifier is a second net, not a dependency
        pass
    return body


# One live connection per (host, user, database), reused across dictionary reads —
# teradatasql.connect costs ~1-2 s, and a describe issues six reads. A connection is
# used under its own lock (the driver is not safe for concurrent cursors); a failed read
# drops it so the next call reconnects rather than reusing a dead socket.
_TD_CONNS: Dict[tuple, Any] = {}
_TD_LOCKS: Dict[tuple, Any] = {}
_TD_META_LOCK = __import__("threading").Lock()
_IDLE_CLOSE_SECONDS = 600


def _td_key(creds: Dict[str, Any]) -> tuple:
    return (str(creds.get("host", "")), str(creds.get("username") or creds.get("user", "")),
            str(creds.get("database", "")), str(creds.get("logmech", "")))


def _td_connect(creds: Dict[str, Any]):
    import teradatasql  # type: ignore[import]
    kwargs = {"host": creds.get("host", ""),
              "user": creds.get("username") or creds.get("user", ""),
              "password": creds.get("password", "")}
    if creds.get("database"):
        kwargs["database"] = creds["database"]
    if creds.get("logmech"):
        kwargs["logmech"] = creds["logmech"]
    return teradatasql.connect(**kwargs)


def _td_drop(key: tuple) -> None:
    with _TD_META_LOCK:
        conn = _TD_CONNS.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _teradata_rows(creds: Dict[str, Any], sql: str, max_rows: int) -> Rows:
    import threading
    key = _td_key(creds)
    with _TD_META_LOCK:
        lock = _TD_LOCKS.setdefault(key, threading.Lock())
    with lock:
        conn_entry = _TD_CONNS.get(key)
        conn = conn_entry[0] if conn_entry else None
        if conn is None:
            conn = _td_connect(creds)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                if not cur.description:
                    _TD_CONNS[key] = (conn, __import__("time").monotonic())
                    return [], []
                cols = [str(d[0]).strip() for d in cur.description]
                out: List[List[Any]] = []
                done = False
                while not done:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        out.append([jsonable(v) for v in row])
                        if max_rows and len(out) >= max_rows:
                            done = True
                            break
            _TD_CONNS[key] = (conn, __import__("time").monotonic())
            return cols, out
        except Exception:
            _TD_CONNS.pop(key, None)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            raise


def close_idle_connections(max_idle: int = _IDLE_CLOSE_SECONDS) -> int:
    """Close dictionary connections idle longer than ``max_idle`` seconds (opportunistic;
    called by the runner after each read). Returns the number closed."""
    import time as _t
    now = _t.monotonic()
    with _TD_META_LOCK:
        stale = [k for k, (c, ts) in _TD_CONNS.items() if now - ts > max_idle]
        entries = [(k, _TD_CONNS.pop(k)) for k in stale]
    for _k, (c, _ts) in entries:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
    return len(entries)


async def run_rows(creds: Dict[str, Any], sql: str, max_rows: int = 0, *, readonly: bool = False) -> Rows:
    """Execute ``sql`` under ``creds`` → ``(columns, rows)``. ``max_rows`` 0 = all.

    ``readonly`` asks the connector to open the connection read-only at the database level
    (non-Teradata drivers; Teradata reads through the dictionary account's own grants)."""
    driver = str(creds.get("driver") or "teradata").strip().lower()
    if driver == "teradata":
        try:
            import teradatasql  # noqa: F401
        except ImportError as exc:
            raise CatalogError("teradatasql is not installed — run: pip install teradatasql") from exc
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(_TD_EXECUTOR, _teradata_rows, creds, sql, max_rows)
        except CatalogError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CatalogError(_friendly_db_error(exc)) from exc
        finally:
            close_idle_connections()

    # Every other driver: the Canvas connector (asyncpg / aiomysql / aiosqlite / JDBC).
    from components.builtin.canvas.connectors import get_connector
    connector = get_connector("sql_native")
    if connector is None:
        raise CatalogError("SQL execution connector unavailable")
    run_creds = dict(creds)
    if run_creds.get("username") and not run_creds.get("user"):
        run_creds["user"] = run_creds["username"]
    result = await connector.execute(sql, run_creds, max_rows=max_rows, readonly=readonly)
    if result.error:
        raise CatalogError(_friendly_db_error(result.error))
    # The structured result, when the connector provides one: typed values, and a value
    # containing a tab or newline stays ONE value. The text parse below split such a value
    # into extra rows / shifted columns and turned every value into a string.
    if getattr(result, "columns", None) is not None and getattr(result, "rows", None) is not None:
        rows_s = [[jsonable(v) for v in r] for r in result.rows]
        return [str(c) for c in result.columns], (rows_s[:max_rows] if max_rows else rows_s)
    text = result.result or ""
    lines = text.split("\n")
    if not lines or not lines[0] or lines[0].startswith("Query executed successfully"):
        return [], []
    cols = lines[0].split("\t")
    rows: List[List[Any]] = []
    for ln in lines[1:]:
        if ln == "":
            continue
        rows.append([None if v == "None" else v for v in ln.split("\t")])
        if max_rows and len(rows) >= max_rows:
            break
    return cols, rows


def friendly_db_error(exc: Any) -> str:
    """Trim a driver traceback to the sentence an operator can act on — THE one wording of a
    warehouse error on every surface (a catalog read, a landing, the action ledger). The
    driver appends its Go stack ("at gosqldriver/…") and tags ("[SQLState 42000]"); neither
    is said."""
    msg = str(exc)
    m = re.search(r"\[Error (\d+)\]((?:\s*\[[^\]]*\])*)\s*(.+?)(?:\n|$)", msg)
    if m:
        return f"database error {m.group(1)}: {m.group(3).strip()}"
    first = msg.strip().splitlines()[0] if msg.strip() else "database error"
    return first[:300]


_friendly_db_error = friendly_db_error


def rows_as_dicts(cols: List[str], rows: List[List[Any]]) -> List[Dict[str, Any]]:
    keys = [c.strip().lower() for c in cols]
    return [dict(zip(keys, r)) for r in rows]
