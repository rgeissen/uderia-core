"""Per-session authoritative database catalog + SQL reference validation. Pure + deterministic."""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional, Set

from trusted_data_agent.agent.preflight import dialect as _dialect

# Reuse the grounding module's system-DB allowlist and FROM/JOIN db-qualifier regex so the
# preflight guard and the grounding gate classify database references identically (single
# source of truth — no divergence).
from trusted_data_agent.agent.grounding.primitives import SYSTEM_DBS, SQL_DB_RE

# { session_id: {"dbs": frozenset(lower-cased names), "ts": monotonic seconds} }
_CATALOG: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_TTL_SECONDS = 1800  # 30 min — databases rarely appear/disappear mid-session; stale-safe fallback


def _extract_db_names(result: Any) -> Set[str]:
    """Pull the database names out of a base_databaseList result payload (lower-cased)."""
    out: Set[str] = set()
    rows = result.get("results") or result.get("data") if isinstance(result, dict) else None
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict):
                name = (r.get("DatabaseName") or r.get("database_name")
                        or r.get("DataBaseName") or r.get("name"))
                if name:
                    out.add(str(name).strip().lower())
    return out


def _prune_expired_locked() -> None:
    """Drop TTL-expired entries. Entries are otherwise only evicted on read, so a session that is
    recorded then never re-read would linger forever; this bounds memory over long uptime. Caller
    must hold ``_LOCK``."""
    now = time.monotonic()
    stale = [sid for sid, e in _CATALOG.items() if now - e["ts"] > _TTL_SECONDS]
    for sid in stale:
        _CATALOG.pop(sid, None)


def record_catalog(session_id: str, result: Any) -> int:
    """Cache the AUTHORITATIVE catalog for a session from a base_databaseList(scope=all) result.
    Only a complete (scope=all) list may be recorded by the caller — a partial list would create
    false positives. Returns the number of databases cached (0 = nothing recorded)."""
    if not session_id:
        return 0
    dbs = _extract_db_names(result)
    if not dbs:
        return 0
    with _LOCK:
        _CATALOG[session_id] = {"dbs": frozenset(dbs), "ts": time.monotonic()}
        # Opportunistic sweep when the map grows past a soft bound (self-contained; no dependency
        # on a session-deletion caller).
        if len(_CATALOG) > 1000:
            _prune_expired_locked()
    return len(dbs)


def record_names(session_id: str, names: Any) -> int:
    """Cache a COMPLETE list of database / schema names for a session namespace — the Database
    Navigator's listing of a direct source (every schema the connection sees). Same contract as
    ``record_catalog``: a partial list must never be recorded."""
    if not session_id:
        return 0
    dbs = {str(n).strip().lower() for n in (names or []) if str(n or "").strip()}
    if not dbs:
        return 0
    with _LOCK:
        _CATALOG[session_id] = {"dbs": frozenset(dbs), "ts": time.monotonic()}
        if len(_CATALOG) > 1000:
            _prune_expired_locked()
    return len(dbs)


def _known_catalog(session_id: str) -> Optional[frozenset]:
    if not session_id:
        return None
    with _LOCK:
        entry = _CATALOG.get(session_id)
        if not entry:
            return None
        if time.monotonic() - entry["ts"] > _TTL_SECONDS:
            _CATALOG.pop(session_id, None)
            return None
        return entry["dbs"]


def has_catalog(session_id: str) -> bool:
    return _known_catalog(session_id) is not None


def extract_sql_db_refs(sql: str) -> List[str]:
    """Database qualifiers referenced in FROM/JOIN targets (lower-cased, deduped, order-stable).
    Prefers sqlglot for accuracy; falls back to the shared grounding regex if sqlglot is absent
    or the SQL doesn't parse."""
    if not sql or not isinstance(sql, str):
        return []
    refs: List[str] = []
    seen: Set[str] = set()

    def _add(name: Any) -> None:
        n = str(name or "").strip().lower()
        if n and n not in seen:
            seen.add(n)
            refs.append(n)

    parsed_ok = False
    try:
        import sqlglot
        from sqlglot import exp
        for stmt in sqlglot.parse(sql, read=_dialect.read()):
            if stmt is None:
                continue
            parsed_ok = True
            for tbl in stmt.find_all(exp.Table):
                db = tbl.args.get("db")
                if db is not None:
                    _add(getattr(db, "name", None) or str(db))
    except Exception:
        parsed_ok = False

    if not parsed_ok and not refs:
        for m in SQL_DB_RE.finditer(sql):
            _add(m.group(1))
    return refs


# Anchor at the start of a statement — string start OR after a `;` — so the regex fallback
# (used only when sqlglot is absent or can't parse) still catches a write buried in a batch
# such as `SELECT 1; DELETE FROM t`, not merely a leading write.
_SQL_WRITE_RE = re.compile(
    r"(?:^|;)\s*(?:insert|update|delete|merge|upsert|replace|truncate|drop|create|alter|"
    r"grant|revoke|rename|comment)\b", re.I)

# Statements that write FILES on the database server (or load them into tables) without being
# DML/DDL. sqlglot reads `SELECT … INTO OUTFILE` and `COPY … TO` as reads in every dialect,
# and a read-only transaction does not stop them — found 2026-09-24 while making the checks
# dialect-aware. Matched on the text with string literals and comments removed, so a value
# like 'copy that' or a path never decides the verdict.
_SQL_FILE_WRITE_RE = re.compile(
    r"\binto\s+(?:outfile|dumpfile)\b"
    r"|(?:^|;)\s*(?:copy|export|attach|load|unload|install)\b", re.I)
_SQL_LITERAL_OR_COMMENT_RE = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.S)


def _writes_files(sql: str) -> bool:
    return bool(_SQL_FILE_WRITE_RE.search(_SQL_LITERAL_OR_COMMENT_RE.sub("''", sql)))


def sql_is_write(sql: str) -> bool:
    """True if ``sql`` contains ANY write/DDL/DML-mutating statement.

    The flagship autonomous restriction ``deny_writes`` was a tool-NAME heuristic, so a
    write smuggled through a read-named tool (``base_readQuery(sql="DELETE …")``) passed.
    This classifies the statement itself — sqlglot first (the same parser this module
    already uses for scope refs), a leading-keyword regex as the fallback for SQL sqlglot
    cannot parse. Multi-statement: a write ANYWHERE in the batch makes the call a write.
    Pure; never raises. On total parse failure returns the regex verdict (fail toward
    classifying, since ``deny_writes`` is a restriction and a missed write is the costly
    direction)."""
    if not sql or not isinstance(sql, str):
        return False
    if _writes_files(sql):
        return True
    try:
        import sqlglot
        from sqlglot import exp
        parsed_any = False
        for stmt in sqlglot.parse(sql, read=_dialect.read()):
            if stmt is None:
                continue
            parsed_any = True
            if isinstance(stmt, (exp.Insert, exp.Update, exp.Delete, exp.Merge,
                                 exp.Drop, exp.Create, exp.Alter, exp.Grant)) \
                    or type(stmt).__name__ in (
                        "Replace", "Truncate", "Revoke", "RenameTable", "TruncateTable"):
                return True
            # A write NESTED in a read-shaped statement: a Postgres data-modifying CTE
            # (`WITH gone AS (DELETE … RETURNING 1) SELECT …`) is a SELECT at the top and a
            # DELETE inside — the top-level check alone passed it (found 2026-09-24, which
            # also meant `deny_writes` could be bypassed that way on a Postgres MCP server).
            if stmt.find(exp.Insert, exp.Update, exp.Delete, exp.Merge,
                         exp.Drop, exp.Create, exp.Alter) is not None:
                return True
            # `SELECT … INTO new_table` creates a table.
            if isinstance(stmt, exp.Select) and stmt.args.get("into") is not None:
                return True
            # A bare Command node wraps statements sqlglot doesn't model (TRUNCATE, GRANT
            # on some dialects) — fall back to the keyword check on its text.
            if isinstance(stmt, exp.Command) and _SQL_WRITE_RE.match(stmt.sql() or ""):
                return True
        if parsed_any:
            return False   # parsed cleanly and nothing wrote
    except Exception:
        pass
    return bool(_SQL_WRITE_RE.search(sql))


def nonexistent_db_refs(session_id: str, sql: str) -> List[str]:
    """Databases referenced by the SQL that are PROVABLY absent from the session's authoritative
    catalog (system databases excluded). Empty when no catalog is cached (fail-open) or every
    reference is known/system — so there are no false positives."""
    catalog = _known_catalog(session_id)
    if not catalog:
        return []
    return [db for db in extract_sql_db_refs(sql)
            if db not in SYSTEM_DBS and db not in catalog]


def clear_session(session_id: str) -> None:
    with _LOCK:
        _CATALOG.pop(session_id, None)


def _reset_all() -> None:
    """Test hook — clears every cached catalog."""
    with _LOCK:
        _CATALOG.clear()
