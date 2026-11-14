"""The SQL dialect and source of the call being checked — and which recorded facts may judge it.

Every preflight check parses a statement and compares it with facts recorded in the session:
the ontology's tables, columns, keys and value domains, the MCP catalog's database list,
the date shapes and values learned from earlier results. Before P3 both were Teradata-only
and session-wide: a statement was parsed as Teradata whatever its source, and a PostgreSQL
statement from the Database Navigator was judged against the Teradata ontology's facts —
under ``enforce`` a ``public.business`` could be refused as a nonexistent database.

Two rules, stated once here:

* **Parse in the call's dialect.** ``read()`` names the dialect (sqlglot's name) every
  check parses and renders snippets with. The default is Teradata, so a caller that sets
  nothing — the MCP adapter, the ReAct wrapper — behaves exactly as before.
* **A fact judges only statements against the source it came from.** Teradata calls share
  the session's facts, as before: the ontology describes Teradata, and the MCP catalog is
  the Teradata server's. A call against any other source reads and writes its OWN facts,
  under ``ns(session_id)`` = ``<session>::<source>`` — what that source's results taught.

Deterministic rewrites that emit Teradata syntax (TOP, CAST … FORMAT, reserved-alias
quoting) stay gated to Teradata by their callers; they never run under another dialect.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

TERADATA = "teradata"

# Connection driver → sqlglot dialect (only the names that differ).
_SQLGLOT = {"postgresql": "postgres", "psycopg2": "postgres", "pg": "postgres",
            "mariadb": "mysql", "mssql": "tsql", "sqlserver": "tsql"}

_DIALECT: ContextVar[str] = ContextVar("preflight_dialect", default=TERADATA)
_SOURCE: ContextVar[str] = ContextVar("preflight_source", default="")


def normalize(dialect: Optional[str]) -> str:
    d = str(dialect or TERADATA).strip().lower() or TERADATA
    return _SQLGLOT.get(d, d)


def read() -> str:
    """The sqlglot dialect the current call is parsed and rendered in."""
    return _DIALECT.get()


def is_teradata() -> bool:
    return _DIALECT.get() == TERADATA


def source() -> str:
    return _SOURCE.get()


@contextmanager
def using(dialect: Optional[str], source_id: str = "") -> Iterator[None]:
    """Check (and learn from) one call in its own dialect and source."""
    t1 = _DIALECT.set(normalize(dialect))
    t2 = _SOURCE.set(str(source_id or ""))
    try:
        yield
    finally:
        _DIALECT.reset(t1)
        _SOURCE.reset(t2)


def ns(session_id: str) -> str:
    """The key the current call's facts live under. Teradata shares the session's facts
    (the ontology and the MCP catalog describe Teradata); any other source keeps its own.

    PROP-006 phase 2 (O5/O6): a call to one of the agent's ADDITIONAL MCP servers keeps its own
    facts even on Teradata — two warehouses are two sources, and one server's database list must
    never answer for the other's. Only the turn's additional servers are keyed this way, so the
    primary server (and every single-server agent) shares the session's facts exactly as before."""
    if not session_id:
        return session_id
    if is_teradata():
        src = source()
        if not (src.startswith("mcp:") and src[4:] and src[4:] in _additional_server_ids()):
            return session_id
        suffix = f"::{src}"
        return session_id if session_id.endswith(suffix) else f"{session_id}{suffix}"
    suffix = f"::{source() or read()}"
    return session_id if session_id.endswith(suffix) else f"{session_id}{suffix}"   # idempotent


def _additional_server_ids():
    try:
        from trusted_data_agent.core.tool_identity import turn_server_ids
        return turn_server_ids()
    except Exception:  # noqa: BLE001
        return ()


def additional_mcp_scope(server_id: Optional[str]):
    """The source scope for a call to an ADDITIONAL MCP server (the dialect unchanged); a no-op
    for any other call. Shared by the Optimize and ReAct lanes."""
    import contextlib
    if server_id and server_id in _additional_server_ids():
        return using(read(), f"mcp:{server_id}")
    return contextlib.nullcontext()


def server_scope(server_id: Optional[str]):
    """The source scope for a call to ``server_id`` whatever scope is current: an ADDITIONAL
    server's own facts, or — for the primary — the session's (clearing an outer additional
    scope, which ``additional_mcp_scope`` would leave in place). Used when a call is re-routed
    from one server to another (``agent/source_routing.py``)."""
    if server_id and server_id in _additional_server_ids():
        return using(read(), f"mcp:{server_id}")
    return using(read(), "")


def limit_clause(n: int, select_list: str) -> str:
    """``SELECT <select_list>`` bounded to ``n`` rows in the current dialect."""
    if is_teradata():
        return f"SELECT TOP {int(n)} {select_list}"
    return f"SELECT {select_list}"


def limit_suffix(n: int) -> str:
    """The trailing bound for a non-Teradata dialect ('' on Teradata, whose TOP leads)."""
    return "" if is_teradata() else f" LIMIT {int(n)}"
