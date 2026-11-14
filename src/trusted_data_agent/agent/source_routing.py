"""PROP-006 phase 2, B10 — send a multi-server agent's call to the server that holds its data.

An agent with several MCP servers sees the same tool on each (`base_readQuery`,
`nibrs__base_readQuery`) with the same description, so the model has nothing to choose by. In
the first live run it sent a `fitness_db` count to the NIBRS server, the database refused the
login (Error 3523) and the model answered "no permission" instead of trying the other server.

Code decides where it safely can:

* **What a server holds** is what its own login can read — the server's database-list tool
  (resolved by role, never by a hard-coded name) returns exactly the databases its login may
  read (measured: the fitness login lists ``fitness_db`` and none of NIBRS's, and the reverse).
  Learned per (user, server) from a probe, from the turn's own database-list results, and from
  what each call's outcome proved (a success holds, a 3523/3802 does not). Cached 30 minutes.
* **Before a call** (``decide_before``): a statement naming a database the called server does
  not hold goes to the ONE other server of the agent that holds it, and the re-route is named.
  Several holders → the call is refused, naming them (the model chooses; code never guesses).
* **After a failed call** (``decide_after``): an error that says "this server cannot read that"
  (3523/3524/3802, and PostgreSQL's permission-denied / schema-does-not-exist — never 3807, a
  missing table in a database the login does read) on a READ is sent
  once to the server that holds the data — or, when nothing is known, to the only other server
  that offers the same tool.
* **Never** a write (a write on another warehouse is a different act), never a server known not
  to hold the data, never more than one re-route per call, never a single-server agent (nothing
  is armed, every function is a no-op).

Both tool lanes use this module: the Optimize lane in ``adapter.invoke_mcp_tool``, the ReAct
lane in ``tool_enforcement.wrap_langchain_mcp_tools``.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("quart.app")

HOLDINGS_TTL = 1800.0          # a server's database list rarely changes within half an hour
FAILED_PROBE_TTL = 120.0       # a server whose list could not be read is not re-probed at once
PROBE_TIMEOUT = 8.0

# The catalog (stage 3a): each server's TABLES, and their columns when the catalog is small, read
# once with the database list and cached with it — so the planner names real tables on the right
# server instead of inventing them. Bounded: a server with many user databases or many tables
# keeps the database list only (the describe pass and the correction strategies still read what
# a statement needs).
CATALOG_MAX_DBS = 6              # user databases per server whose tables are read
CATALOG_MAX_TABLES = 40          # tables per server kept in the catalog
CATALOG_COLUMN_TABLES = 16       # columns are read only when a server has at most this many tables
CATALOG_MAX_COLUMNS_SHOWN = 30   # per table, in the note
CATALOG_TIMEOUT = 15.0

STAND, REROUTE, REFUSE = "stand", "reroute", "refuse"


def enabled() -> bool:
    """Deployment switch (default on): ``APP_CONFIG.MCP_SOURCE_ROUTING`` / env
    ``TDA_MCP_SOURCE_ROUTING``. Off ⇒ a multi-server agent's calls go where the model sent them."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        v = getattr(APP_CONFIG, "MCP_SOURCE_ROUTING", None)
        if v is not None:
            return bool(v)
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("TDA_MCP_SOURCE_ROUTING", "1").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------------------------
# What a call reads
# ---------------------------------------------------------------------------------------------

_SQL_ARGS = ("sql", "query", "sql_query", "statement", "sql_statement")
_DB_ARGS = ("database_name", "databasename", "db_name", "database", "schema_name", "schema")


def _system_dbs() -> frozenset:
    try:
        from trusted_data_agent.agent.grounding.primitives import SYSTEM_DBS
        return frozenset(str(d).lower() for d in SYSTEM_DBS)
    except Exception:  # noqa: BLE001
        return frozenset({"dbc", "syslib", "sysudtlib", "td_sysfnlib", "sys_calendar"})


# Teradata's own utility/installation databases every login can list — never where a user's data
# lives. Left out of what the MODEL is told (they crowded the real databases past the cap:
# sqlj, sysjdbc, td_sysxml, … listed ahead of nibrs_source_domain); routing still counts them.
_NOTE_ONLY_INTERNAL = frozenset({
    "sqlj", "sysjdbc", "sysxml", "td_sysxml", "td_sysgpl", "td_analytics_db", "tdbcmgmt", "dbcmngr",
    "sysuif", "sysadmin", "systemfe", "tdstats", "tdqcd", "td_serverdb", "tdpuser", "crashdumps",
    "lockinglogger", "external_ap", "tdmaps", "tdwm", "sysbar", "sysspatial", "syslib", "sysudtlib",
    "td_sysfnlib", "td_ms_sample_db", "sys_mgmt", "tdql",
})


def statement_of(arguments: Any) -> Optional[str]:
    if not isinstance(arguments, dict):
        return None
    for k in _SQL_ARGS:
        v = arguments.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def referenced_databases(arguments: Any) -> Tuple[str, ...]:
    """The databases a call reads (lower-case, system catalogs excluded — every login reads
    those): the statement's qualified FROM/JOIN targets and a database-naming argument."""
    out: List[str] = []
    sql = statement_of(arguments)
    if sql:
        try:
            from trusted_data_agent.agent.preflight.catalog import extract_sql_db_refs
            out.extend(extract_sql_db_refs(sql))
        except Exception:  # noqa: BLE001
            pass
    if isinstance(arguments, dict):
        for k, v in arguments.items():
            if str(k).lower() in _DB_ARGS and isinstance(v, str) and v.strip():
                out.append(v.strip().lower())
    sysdb = _system_dbs()
    seen, res = set(), []
    for d in out:
        d = d.strip().strip('"').lower()
        if d and d not in sysdb and d not in seen:
            seen.add(d)
            res.append(d)
    return tuple(res)


def is_write_call(arguments: Any) -> bool:
    sql = statement_of(arguments)
    if not sql:
        return False
    try:
        from trusted_data_agent.agent.preflight.catalog import sql_is_write
        return bool(sql_is_write(sql))
    except Exception:  # noqa: BLE001
        return True   # unsure ⇒ never move it


# The database says "this login cannot read that" — not a syntax error, not a timeout, and NOT
# 3807 "Object 'db.t' does not exist": that names a missing TABLE in a database the login does
# read (lacking rights is 3523, a missing database 3802). Counting it poisoned the holdings —
# observed live: a guessed `fitness_db.customer` taught the router the fitness server does not
# hold fitness_db.
_NOT_HELD_RE = re.compile(
    r"\[Error (?:3523|3524|3802)\]"
    r"|does not have \w+ access to"
    r"|Database '[^']+' does not exist"
    r"|permission denied for (?:schema|table|relation|database)"
    r"|(?:schema|database) \"[^\"]+\" does not exist",
    re.I)
_ERR_DB_RE = re.compile(
    r"access to ([A-Za-z_][\w$#]*)\.|Database '([^']+)' does not exist"
    r"|(?:schema|database) \"([^\"]+)\" does not exist", re.I)


def not_held_error(text: Any) -> bool:
    return isinstance(text, str) and bool(_NOT_HELD_RE.search(text))


def databases_in_error(text: Any) -> Tuple[str, ...]:
    if not isinstance(text, str):
        return ()
    out = []
    for m in _ERR_DB_RE.finditer(text):
        d = next((g for g in m.groups() if g), None)
        if d and d.lower() not in out:
            out.append(d.lower())
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# What each server holds — per (user, server), process-wide
# ---------------------------------------------------------------------------------------------

@dataclass
class _Holdings:
    dbs: Optional[frozenset] = None      # the server's own complete list (None = not read)
    users: frozenset = frozenset()       # entries the list marks as a USER (a login's own space)
    yes: set = field(default_factory=set)   # proven readable by a successful call
    no: set = field(default_factory=set)    # proven unreadable by the database's refusal
    ts: float = 0.0
    probe_failed_at: float = 0.0
    unreachable: str = ""                   # the probe did not answer (the reason), "" otherwise
    tables: Optional[Dict[str, Tuple[str, ...]]] = None   # database → its tables (None = not read)
    columns: Dict[str, Tuple[str, ...]] = field(default_factory=dict)   # "db.table" → columns
    catalog_failed_at: float = 0.0


_LOCK = threading.Lock()
_HOLDINGS: Dict[Tuple[str, str], _Holdings] = {}
_PROBE_LOCKS: Dict[Tuple[str, str], asyncio.Lock] = {}
_LIST_TOOL: Dict[str, Tuple[str, Dict[str, Any]]] = {}   # server id → (tool, args)
_ROLES: Dict[str, Dict[str, Any]] = {}   # server id → resolved catalog roles (list_tables, …)


def _key(user_uuid: Optional[str], server_id: str) -> Tuple[str, str]:
    return (str(user_uuid or ""), str(server_id))


def _entry(user_uuid, server_id, create: bool = False) -> Optional[_Holdings]:
    k = _key(user_uuid, server_id)
    with _LOCK:
        h = _HOLDINGS.get(k)
        if h is not None and h.dbs is not None and time.monotonic() - h.ts > HOLDINGS_TTL:
            h.dbs = None
            h.tables, h.columns = None, {}
        if h is None and create:
            h = _HOLDINGS[k] = _Holdings(ts=time.monotonic())
        return h


def learn_list(user_uuid, server_id, names: Iterable[str], users: Iterable[str] = ()) -> int:
    """Record a server's COMPLETE database list (its login's own view). ``users`` = the entries
    the list marks as a user (a login's own space, rarely where data lives) — kept for routing,
    left out of what the model is told."""
    dbs = frozenset(str(n).strip().lower() for n in (names or ()) if str(n or "").strip())
    if not server_id or not dbs:
        return 0
    h = _entry(user_uuid, server_id, create=True)
    with _LOCK:
        h.dbs, h.ts = dbs, time.monotonic()
        h.users = frozenset(str(u).strip().lower() for u in (users or ()) if str(u or "").strip())
        h.no -= dbs
    return len(dbs)


def user_spaces(user_uuid, server_id) -> frozenset:
    h = _entry(user_uuid, server_id)
    return h.users if h else frozenset()


def learn_outcome(user_uuid, server_id, databases: Sequence[str], held: bool) -> None:
    """What one call proved: the databases it read are (or are not) readable on that server."""
    if not server_id or not databases:
        return
    h = _entry(user_uuid, server_id, create=True)
    with _LOCK:
        for d in databases:
            d = str(d).lower()
            (h.yes if held else h.no).add(d)
            (h.no if held else h.yes).discard(d)


def holds(user_uuid, server_id, database: str) -> Optional[bool]:
    """True / False when known, None when nothing tells."""
    h = _entry(user_uuid, server_id)
    if h is None:
        return None
    d = str(database).lower()
    with _LOCK:
        if d in h.no:
            return False
        if d in h.yes:
            return True
        if h.dbs is not None:
            return d in h.dbs
    return None


def holder_of(user_uuid, database: str, server_ids: Iterable[str]) -> Optional[str]:
    """The ONE server of ``server_ids`` known to hold ``database`` (``holds`` True), or None when
    none or several are — the planner's placement rule for a cross-source split (stage 3b)."""
    hs = [str(s) for s in server_ids if s and holds(user_uuid, s, database) is True]
    return hs[0] if len(hs) == 1 else None


def holds_all(user_uuid, server_id, databases: Sequence[str]) -> Optional[bool]:
    vals = [holds(user_uuid, server_id, d) for d in databases]
    if any(v is False for v in vals):
        return False
    if vals and all(v is True for v in vals):
        return True
    return None


def known_list(user_uuid, server_id) -> Optional[frozenset]:
    h = _entry(user_uuid, server_id)
    return h.dbs if h else None


def _reset_all() -> None:   # tests
    with _LOCK:
        _HOLDINGS.clear()
        _PROBE_LOCKS.clear()
        _LIST_TOOL.clear()


def _users_from_result(result: Any) -> List[str]:
    """The entries a database list marks as a USER (Teradata's DBType / kind)."""
    rows = result.get("results") if isinstance(result, dict) else result
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            kind = str(r.get("DBType") or r.get("DBKind") or r.get("kind") or "").strip().lower()
            n = r.get("DatabaseName") or r.get("database_name") or r.get("DataBaseName") or r.get("name")
            if n and kind in ("user", "u"):
                out.append(str(n))
    return out


def _names_from_result(result: Any) -> Optional[List[str]]:
    rows = None
    if isinstance(result, dict):
        rows = result.get("results") if isinstance(result.get("results"), list) else result.get("data")
    elif isinstance(result, list):
        rows = result
    if not isinstance(rows, list):
        return None
    names = []
    for r in rows:
        if isinstance(r, dict):
            n = (r.get("DatabaseName") or r.get("database_name") or r.get("DataBaseName")
                 or r.get("SchemaName") or r.get("schema_name") or r.get("name"))
            if n:
                names.append(str(n))
    return names or None


async def _probe(user_uuid, server_id) -> Optional[List[str]]:
    """Read one server's database list through its own database-list tool (found by role)."""
    from trusted_data_agent.core.configuration_service import pooled_mcp_client
    from trusted_data_agent.dbcatalog.mcp_provider import resolve_roles_by_shape
    client = pooled_mcp_client(server_id, user_uuid)
    async with client.session(server_id) as sess:
        spec = _LIST_TOOL.get(server_id)
        if spec is None:
            listed = await sess.list_tools()
            tools = []
            for t in (getattr(listed, "tools", None) or []):
                props = ((getattr(t, "inputSchema", None) or {}).get("properties") or {})
                tools.append({"name": t.name, "description": getattr(t, "description", "") or "",
                              "args": list(props.keys())})
            roles = resolve_roles_by_shape(tools)
            _ROLES[server_id] = roles
            role = roles.get("list_databases")
            if not role:
                return None
            args_of = next((t["args"] for t in tools if t["name"] == role["tool"]), [])
            spec = (role["tool"], {"scope": "all"} if "scope" in args_of else {})
            _LIST_TOOL[server_id] = spec
        res = await sess.call_tool(spec[0], spec[1])
    if getattr(res, "isError", False):
        return None
    text = "".join(getattr(c, "text", "") or "" for c in (getattr(res, "content", None) or []))
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    names = _names_from_result(parsed)
    return (names, _users_from_result(parsed)) if names else None


def _parsed(res: Any) -> Any:
    if getattr(res, "isError", False):
        return None
    text = "".join(getattr(c, "text", "") or "" for c in (getattr(res, "content", None) or []))
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(parsed, list):
        parsed = {"status": "success", "results": parsed}
    return parsed if isinstance(parsed, dict) else None


def catalog_databases(user_uuid, server_id) -> List[str]:
    """The user databases of a server whose tables the catalog reads (system catalogs and the
    login's own space left out, as in the note). [] when the list is unknown."""
    lst = known_list(user_uuid, server_id)
    if not lst:
        return []
    sysdb = _system_dbs() | _NOTE_ONLY_INTERNAL
    users = user_spaces(user_uuid, server_id)
    return sorted(d for d in lst if d not in sysdb and d not in users)


async def _probe_catalog(user_uuid, server_id, databases: Sequence[str]):
    """Read the tables of each database (and their columns when there are few) through the
    server's own list-tables / describe-columns tools, resolved by role. Returns
    ``(tables_by_db, columns_by_table)`` or None when the server has no list-tables tool."""
    from trusted_data_agent.core.configuration_service import pooled_mcp_client
    from trusted_data_agent.dbcatalog.mcp_provider import resolve_roles_by_shape
    from trusted_data_agent.agent.schema_facts import tables_from_result, columns_from_result, _rows
    client = pooled_mcp_client(server_id, user_uuid)
    async with client.session(server_id) as sess:
        roles = _ROLES.get(server_id)
        if roles is None:
            listed = await sess.list_tools()
            tools = []
            for t in (getattr(listed, "tools", None) or []):
                props = ((getattr(t, "inputSchema", None) or {}).get("properties") or {})
                tools.append({"name": t.name, "description": getattr(t, "description", "") or "",
                              "args": list(props.keys())})
            roles = _ROLES[server_id] = resolve_roles_by_shape(tools)
        lt, dc = roles.get("list_tables"), roles.get("describe_columns")
        if not lt or not lt.get("db_arg"):
            return None
        tables_by_db: Dict[str, Tuple[str, ...]] = {}
        total = 0
        for db in list(databases)[:CATALOG_MAX_DBS]:
            parsed = _parsed(await sess.call_tool(lt["tool"], {lt["db_arg"]: db}))
            if parsed is None:
                continue
            # a list tool may answer for more than the database asked: keep that database's rows
            rows = _rows(parsed)
            own = [r for r in rows if str(r.get("DataBaseName") or r.get("DatabaseName")
                                          or r.get("database_name") or db).lower() == db.lower()]
            names = tables_from_result({"results": own})[: max(0, CATALOG_MAX_TABLES - total)]
            tables_by_db[db] = tuple(names)
            total += len(names)
        columns: Dict[str, Tuple[str, ...]] = {}
        if dc and dc.get("db_arg") and dc.get("table_arg") and 0 < total <= CATALOG_COLUMN_TABLES:
            for db, names in tables_by_db.items():
                for t in names:
                    parsed = _parsed(await sess.call_tool(
                        dc["tool"], {dc["db_arg"]: db, dc["table_arg"]: t}))
                    cols = columns_from_result(parsed) if parsed else []
                    if cols:
                        columns[f"{db}.{t}"] = tuple(cols)
    return tables_by_db, columns


async def _ensure_catalog(user_uuid, sid) -> None:
    """The server's catalog, once per list (same TTL); a failure leaves it unread and is not
    retried for FAILED_PROBE_TTL. Never raises."""
    h = _entry(user_uuid, sid)
    if h is None or h.dbs is None or h.tables is not None:
        return
    if time.monotonic() - h.catalog_failed_at < FAILED_PROBE_TTL:
        return
    dbs = catalog_databases(user_uuid, sid)
    if not dbs or len(dbs) > CATALOG_MAX_DBS:
        with _LOCK:
            h.tables = {}          # too many (or none): the database list is all the note says
        return
    try:
        got = await asyncio.wait_for(_probe_catalog(user_uuid, sid, dbs), timeout=CATALOG_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info(f"[source routing] catalog of server {sid} not readable: "
                    f"{type(exc).__name__}: {str(exc)[:160]}")
        got = None
    with _LOCK:
        if got is None:
            h.catalog_failed_at = time.monotonic()
        else:
            h.tables, h.columns = got
    if got is not None:
        logger.info(f"[source routing] server {sid} catalog: "
                    f"{sum(len(v) for v in got[0].values())} table(s), {len(got[1])} with columns")


def learn_catalog(user_uuid, server_id, tables_by_db: Dict[str, Sequence[str]],
                  columns: Optional[Dict[str, Sequence[str]]] = None) -> None:
    """Record a server's catalog directly (tests, and any caller that already read it)."""
    h = _entry(user_uuid, server_id, create=True)
    with _LOCK:
        h.tables = {str(d).lower(): tuple(v) for d, v in (tables_by_db or {}).items()}
        h.columns = {str(k): tuple(v) for k, v in (columns or {}).items()}


def catalog_of(user_uuid, server_id) -> Tuple[Optional[Dict[str, Tuple[str, ...]]], Dict[str, Tuple[str, ...]]]:
    h = _entry(user_uuid, server_id)
    if h is None:
        return None, {}
    with _LOCK:
        return (dict(h.tables) if h.tables is not None else None), dict(h.columns)


def holdings_for_turn() -> Dict[str, Dict[str, Any]]:
    """Plan stage 3a — what each of this turn's servers holds, structured:
    ``{alias or "primary": {"server_id", "label", "databases": [...], "tables": {db: [...]}|None,
    "columns": {"db.table": [...]}}}``. {} when routing is not active."""
    if not active():
        return {}
    user = turn_user()
    out: Dict[str, Dict[str, Any]] = {}
    for s in turn_servers():
        tables, columns = catalog_of(user, s.server_id)
        out[s.alias or "primary"] = {
            "server_id": str(s.server_id), "label": label_of(s.server_id),
            "databases": catalog_databases(user, s.server_id),
            "tables": {d: list(v) for d, v in tables.items()} if tables else None,
            "columns": {k: list(v) for k, v in columns.items()}}
    return out


def turn_catalog_columns() -> Dict[str, List[str]]:
    """``{"db.table": [columns]}`` over every server of the turn — what the session's column
    store is seeded with, so the describe pass and the correction strategies know these tables."""
    out: Dict[str, List[str]] = {}
    for h in holdings_for_turn().values():
        for k, v in (h.get("columns") or {}).items():
            out[k] = list(v)
    return out


async def ensure_lists(user_uuid, server_ids: Iterable[str]) -> None:
    """Make sure each server's list is known (probing the ones that are not, concurrently).
    Never raises; a server whose list cannot be read stays unknown."""
    async def _one(sid):
        if not sid:
            return
        h = _entry(user_uuid, sid)
        if h is not None and h.dbs is None and time.monotonic() - h.probe_failed_at < FAILED_PROBE_TTL:
            if h.unreachable:            # still down: this turn is told too (a recent probe said so)
                mark_unavailable(sid, h.unreachable)
            return
        if h is not None and h.dbs is not None:
            return
        lock = _PROBE_LOCKS.setdefault(_key(user_uuid, sid), asyncio.Lock())
        async with lock:
            h = _entry(user_uuid, sid)
            if h is not None and h.dbs is not None:
                return
            down = ""
            try:
                names = await asyncio.wait_for(_probe(user_uuid, sid), timeout=PROBE_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                down = f"did not answer within {PROBE_TIMEOUT:.0f} s"
                logger.info(f"[source routing] server {sid}: {down}")
                names = None
            except Exception as exc:  # noqa: BLE001 — the connection failed: the server is down
                down = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.info(f"[source routing] database list of server {sid} not readable: {down}")
                names = None
            if names:
                if isinstance(names, tuple):
                    names, users = names
                else:
                    users = ()
                n = learn_list(user_uuid, sid, names, users)
                h = _entry(user_uuid, sid, create=True)   # takes _LOCK itself: never inside it
                with _LOCK:
                    h.unreachable = ""
                logger.info(f"[source routing] server {sid} holds {n} database(s)")
            else:
                h = _entry(user_uuid, sid, create=True)
                with _LOCK:
                    h.probe_failed_at = time.monotonic()
                    h.unreachable = down
                if down:
                    mark_unavailable(sid, down)
    await asyncio.gather(*(_one(s) for s in dict.fromkeys(server_ids or ())), return_exceptions=True)


async def ensure_catalogs(user_uuid, server_ids: Iterable[str]) -> None:
    """Each (reachable) server's catalog, concurrently. Never raises."""
    await asyncio.gather(*(_ensure_catalog(user_uuid, s) for s in dict.fromkeys(server_ids or ()) if s),
                         return_exceptions=True)


# ---------------------------------------------------------------------------------------------
# The turn's routing table (armed at the execution seam, per turn)
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    server_id: str
    alias: Optional[str]      # None = the primary server
    tool_name: str            # the name the agent sees
    label: str                # the server's display name


_TURN: "contextvars.ContextVar[Optional[Tuple[str, tuple, list, dict]]]" = contextvars.ContextVar(
    "source_routing_turn", default=None)


def set_turn(user_uuid: Optional[str], servers) -> Any:
    """Arm the agent's servers (``AgentServer`` tuple from ``agent_mcp_servers.servers_of``).
    The third member is the turn's re-route ledger, the fourth its record of servers that could
    not be reached (``server id → details``, plus which were already announced) — mutable, so a
    tool coroutine running in a child task records into what its parent armed (context variables
    copy the REFERENCE into child tasks; a ``.set`` there would be invisible to the parent)."""
    return _TURN.set((str(user_uuid or ""), tuple(servers or ()), [], {"down": {}, "told": set()}))


def record_reroute(called: str, info: Dict[str, Any]) -> None:
    t = _TURN.get()
    if t is not None:
        t[2].append((called, info))


def take_reroute(called: str) -> Optional[Dict[str, Any]]:
    """The oldest re-route of a call to ``called`` not yet reported (the ReAct lane's tool-end
    event names the server that really answered)."""
    t = _TURN.get()
    if t is None:
        return None
    for i, (name, info) in enumerate(t[2]):
        if name == called:
            del t[2][i]
            return info
    return None


def reset_turn(token) -> None:
    try:
        _TURN.reset(token)
    except (ValueError, LookupError, RuntimeError):
        pass


def mark_unavailable(server_id: Optional[str], reason: Any) -> bool:
    """Record that one of this turn's servers could not be reached — from the turn-start probe
    or from a lane that could not load its tools. ONE record per turn, whatever found it; True
    when newly recorded. A server that is not one of this turn's (or no turn armed): nothing."""
    t = _TURN.get()
    if t is None or not server_id:
        return False
    srv = next((s for s in t[1] if str(s.server_id) == str(server_id)), None)
    if srv is None:
        return False
    down = t[3]["down"]
    if str(server_id) in down:
        return False
    down[str(server_id)] = unavailable_event(str(server_id), srv.alias, reason)["details"]
    return True


def note_unreachable(server_id: Optional[str], reason: Any) -> None:
    """A call to one of this turn's servers failed in TRANSPORT (the server did not answer).
    Recorded for this turn (the one record both lanes read), and the server's cached database
    list is dropped so the NEXT turn's start finds it down — a list cached before the server went
    away would otherwise hide it for up to ``HOLDINGS_TTL`` (found live, stage 1)."""
    if not server_id or not active():
        return
    mark_unavailable(server_id, reason)
    h = _entry(turn_user(), server_id, create=True)   # takes _LOCK itself: never inside it
    with _LOCK:
        h.dbs = None
        h.unreachable = str(reason or "the server did not answer")[:240]
        h.probe_failed_at = time.monotonic()


def unavailable_servers() -> Tuple[Dict[str, Any], ...]:
    """The details of every server this turn could not reach (Live Status payload shape)."""
    t = _TURN.get()
    return tuple(t[3]["down"].values()) if t else ()


def is_unavailable(server_id: Optional[str]) -> bool:
    t = _TURN.get()
    return bool(t) and str(server_id or "") in t[3]["down"]


def take_unavailable_events() -> List[Dict[str, Any]]:
    """The Live Status steps for unreachable servers not yet announced in this turn — each
    server once, whichever lane asks first (both lanes call this at turn start)."""
    t = _TURN.get()
    if t is None:
        return []
    out = []
    for sid, det in t[3]["down"].items():
        if sid not in t[3]["told"]:
            t[3]["told"].add(sid)
            out.append({"step": "MCP Server Unavailable", "type": "mcp_server_unavailable",
                        "details": dict(det)})
    return out


def turn_sources_note() -> Optional[str]:
    """Everything the model is told about a multi-server agent's sources this turn: which tools
    reach which server and what each holds (the reachable ones), and which could not be reached.
    ONE text for both lanes and every prompt path (the Optimize planner's context-window module
    and its fallback, the plan gate, the ReAct agent). None for a single-server agent."""
    down = unavailable_servers()
    notes = [servers_note(skip=[d.get("server_id") for d in down]),
             unavailable_note(down) if down else None]
    return "\n\n".join(n for n in notes if n) or None


def turn_user() -> Optional[str]:
    t = _TURN.get()
    return t[0] if t else None


def turn_servers() -> tuple:
    t = _TURN.get()
    return t[1] if t else ()


def active() -> bool:
    """Routing applies: the switch is on and this turn's agent has two or more servers."""
    return len(turn_servers()) > 1 and enabled()


def label_of(server_id: Optional[str]) -> str:
    try:
        from trusted_data_agent.core.agent_mcp_servers import server_label
        return server_label(server_id) or str(server_id or "")
    except Exception:  # noqa: BLE001
        return str(server_id or "")


def siblings(base: str, exclude_server: Optional[str], available=None,
             arguments: Optional[Dict[str, Any]] = None) -> List[Candidate]:
    """The agent's OTHER servers that offer the tool ``base`` and on which the agent may call it
    (the server's own selection, Agent Authority on the name the agent would call — fail-closed;
    ``available`` narrows to names the lane can actually run)."""
    out = []
    for s in turn_servers():
        if str(s.server_id) == str(exclude_server or "") or not s.selects_tool(base):
            continue
        name = s.name_for(base)
        if available is not None and name not in available:
            continue
        try:
            from trusted_data_agent.core.agent_authority import enforce_tool
            allowed, _reason = enforce_tool(name, arguments=arguments)
            if not allowed:
                continue
        except Exception:  # noqa: BLE001 — authority unreadable ⇒ never route there
            continue
        out.append(Candidate(str(s.server_id), s.alias, name, label_of(s.server_id)))
    return out


def learn_from_result(tool_name: str, arguments: Any, result: Any, source: str) -> None:
    """What a successful call of this turn proves about its server: the server's own database
    list (the same tool and arguments the probe uses), or that the databases a statement read
    are readable there. Only on a routing turn; never raises."""
    try:
        if not active() or not isinstance(source, str) or not source.startswith("mcp:"):
            return
        sid, user = source[4:], turn_user()
        if not sid or not isinstance(result, dict) or result.get("status", "success") != "success":
            return
        from trusted_data_agent.core.tool_identity import base_name
        spec = _LIST_TOOL.get(sid)
        if spec and base_name(tool_name) == spec[0] and all(
                str((arguments or {}).get(k, "")).lower() == str(v).lower() for k, v in spec[1].items()):
            names = _names_from_result(result)
            if names:
                learn_list(user, sid, names, _users_from_result(result))
            return
        dbs = referenced_databases(arguments)
        if dbs and statement_of(arguments):
            learn_outcome(user, sid, dbs, held=True)
    except Exception:  # noqa: BLE001
        pass


class profile_call_scope:
    """Arm ONE profile's MCP servers for a call made OUTSIDE a turn (a dashboard tile's MCP
    replay): the profile's first server as the call's server (a per-call STATE overlay, the @TAG
    overlay's pattern — never the user's currently active server), its additional servers' alias
    map and names, and routing. ``with profile_call_scope(profile, user, STATE) as state: …``
    A profile without an MCP server yields ``STATE`` unchanged and arms nothing."""

    def __init__(self, profile, user_uuid, state):
        self.profile, self.user, self.state = profile or {}, user_uuid, state
        self.tokens = []

    def __enter__(self):
        from trusted_data_agent.core.agent_mcp_servers import primary_of
        primary = primary_of(self.profile)
        if not primary:
            return self.state
        from collections import ChainMap
        from trusted_data_agent.core.configuration_service import pooled_mcp_client
        from trusted_data_agent.core import agent_mcp_servers as ams
        from trusted_data_agent.core import tool_identity as ti
        # no catalog is loaded for this profile outside a turn: candidates are judged by the
        # profile's own server selection (``_no_catalog``), never by another profile's catalog
        overlay = {"mcp_server_id": str(primary), "mcp_client": pooled_mcp_client(str(primary), self.user),
                   "_no_catalog": True}
        extra = ams.additional_of(self.profile)
        if extra:
            names = {}
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                names = {str(sv.get("id")): str(sv.get("name") or sv.get("id"))
                         for sv in (get_config_manager().get_mcp_servers(self.user) or []) if isinstance(sv, dict)}
            except Exception:  # noqa: BLE001
                pass
            self.tokens = [("ti", ti.set_turn_servers({s.alias: s.server_id for s in extra})),
                           ("ams", ams.set_turn_source_labels(str(primary), names)),
                           ("sr", set_turn(self.user, ams.servers_of(self.profile)))]
        return ChainMap(overlay, self.state)

    def __exit__(self, *exc):
        from trusted_data_agent.core import agent_mcp_servers as ams
        from trusted_data_agent.core import tool_identity as ti
        for kind, tok in reversed(self.tokens):
            {"ti": ti.reset_turn_servers, "ams": ams.reset_turn_source_labels, "sr": reset_turn}[kind](tok)
        return False


def spans_servers(tool_names: Sequence[str], arguments_list: Sequence[Any]) -> bool:
    """True when several calls cannot run as ONE statement on one server: their tools name
    different servers, or the servers known to hold each call's databases have nothing in
    common. The SQL-consolidation rewrite must not merge such calls — a statement joining two
    warehouses runs nowhere (observed live: a fitness count and a NIBRS count merged into one
    SELECT that failed on both). Unknown holdings never block; a single-server agent never
    spans."""
    if not active():
        return False
    try:
        from trusted_data_agent.core.tool_identity import parse
        if len({parse(t).alias for t in tool_names if t}) > 1:
            return True
        user, common = turn_user(), None
        for args in arguments_list or ():
            dbs = referenced_databases(args)
            if not dbs:
                continue
            can = {str(s.server_id) for s in turn_servers() if holds_all(user, s.server_id, dbs) is not False}
            common = can if common is None else (common & can)
        return common is not None and not common
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------------------------
# A server that cannot be reached (B9)
# ---------------------------------------------------------------------------------------------

def unavailable_event(server_id: str, alias: Optional[str], reason: Any) -> Dict[str, Any]:
    """The Live Status step for one of the agent's servers that could not be reached."""
    label = label_of(server_id)
    return {"step": "MCP Server Unavailable", "type": "mcp_server_unavailable",
            "details": {"server": label, "server_id": str(server_id), "alias": alias,
                        "reason": str(reason or "")[:240],
                        "summary": f"{label} could not be reached — its tools are not offered in this turn"}}


def unavailable_note(entries: Sequence[Dict[str, Any]]) -> Optional[str]:
    """What the model is told when one or more of the agent's servers could not be reached, so
    an answer never reads a missing source as missing data."""
    names = [f"'{e.get('server')}'" + (f" (tools named {e.get('alias')}__…)" if e.get("alias") else "")
             for e in entries or () if e]
    if not names:
        return None
    which = names[0] if len(names) == 1 else ", ".join(names)
    return (f"[Data sources] This agent's MCP server {which} could not be reached in this turn, so "
            f"its data cannot be read. If the question needs that data, say that this source is "
            f"currently unavailable — never answer as if the data did not exist.")


SERVERS_NOTE_MAX_DBS = 12


def servers_note(skip: Iterable[str] = ()) -> Optional[str]:
    """What the model is told about a multi-server agent's servers: which tools reach which
    server, and the databases each server's login can read (from the cached lists — never a
    probe here). None for a single-server agent or with routing off. The databases a login reads
    by virtue of being a database user (system catalogs) are left out."""
    if not active():
        return None
    user, skip = turn_user(), {str(s) for s in (skip or ())}
    sysdb = _system_dbs() | _NOTE_ONLY_INTERNAL
    rows = []
    for s in turn_servers():
        if str(s.server_id) in skip:
            continue
        who = "tools without a prefix" if s.primary else f"tools named {s.alias}__…"
        lst = known_list(user, s.server_id)
        if lst is None:
            held = "databases not known yet"
        else:
            users = user_spaces(user, s.server_id)
            dbs = sorted(d for d in lst if d not in sysdb and d not in users)
            if not dbs:   # a login whose only space is its own: name it rather than claim none
                dbs = sorted(d for d in lst if d not in sysdb)
            held = ("databases: " + ", ".join(dbs[:SERVERS_NOTE_MAX_DBS])
                    + (f" (+{len(dbs) - SERVERS_NOTE_MAX_DBS} more)" if len(dbs) > SERVERS_NOTE_MAX_DBS else "")
                    if dbs else "no databases beyond the system catalogs")
        rows.append(f"- {who} reach server '{label_of(s.server_id)}' — {held}")
        rows.extend(_catalog_lines(user, s.server_id))
    # A multi-server agent with only ONE server left reachable (the others skipped as down) still
    # gets the list: without it the model does not know what it can still read (found live —
    # with one server down the planner invented a database). ``active()`` above already means
    # the agent has two or more servers.
    if not rows:
        return None
    head = ("[Data sources] This agent reads several MCP servers. Call the tool of the server that "
            "holds the data (a call naming another server's database is re-routed, and says so):")
    if any(r.startswith("    ") for r in rows):
        head += ("\nThe tables (and columns) listed were read from each server's own dictionary: use "
                 "these names exactly — a table not listed does not exist on that server. One statement "
                 "reads the tables of ONE server; data held on two servers is fetched from each server "
                 "separately.")
    return head + "\n" + "\n".join(rows)


def _catalog_lines(user, server_id) -> List[str]:
    """The server's tables (with columns when read), one indented line per table, bounded."""
    tables, columns = catalog_of(user, server_id)
    out: List[str] = []
    for db in sorted(tables or {}):
        for t in (tables or {}).get(db) or ():
            cols = columns.get(f"{db}.{t}")
            if cols:
                shown = list(cols)[:CATALOG_MAX_COLUMNS_SHOWN]
                more = f", +{len(cols) - len(shown)} more" if len(cols) > len(shown) else ""
                out.append(f"    {db}.{t} ({', '.join(shown)}{more})")
            else:
                out.append(f"    {db}.{t}")
    return out


def unreachable_text(server_id: Optional[str], called: str) -> str:
    """The error a call gets when its server stops answering mid-turn (recovery-shaped)."""
    label = label_of(server_id)
    return (f"Server '{label}' ({called}) could not be reached — its data cannot be read in this "
            f"turn. Do not retry it; answer from the agent's other servers and say that this source "
            f"was unavailable.")


# ---------------------------------------------------------------------------------------------
# The decisions (pure given the holdings above)
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    action: str
    to: Optional[Candidate] = None
    databases: Tuple[str, ...] = ()
    candidates: Tuple[Candidate, ...] = ()
    why: str = ""


def decide_before(user_uuid, target_server: str, databases: Sequence[str],
                  candidates: Sequence[Candidate], write: bool = False) -> Route:
    """Before the call: go to the one other server that holds the data the called one lacks."""
    dbs = tuple(databases or ())
    if not dbs or write or not candidates:
        return Route(STAND, databases=dbs)
    if holds_all(user_uuid, target_server, dbs) is not False:
        return Route(STAND, databases=dbs)
    holders = tuple(c for c in candidates if holds_all(user_uuid, c.server_id, dbs) is True)
    if len(holders) == 1:
        return Route(REROUTE, holders[0], dbs, holders, "holds")
    if len(holders) > 1:
        return Route(REFUSE, None, dbs, holders, "several")
    # one statement over databases that live on DIFFERENT servers runs nowhere: say so before
    # anything is sent, naming where each database is (only when every one is known to be held)
    split = split_by_server(user_uuid, dbs, candidates, target_server)
    if split:
        return Route(REFUSE, None, dbs, tuple(split.values()), "split")
    return Route(STAND, databases=dbs)


def split_by_server(user_uuid, databases, candidates, target_server) -> Dict[str, Candidate]:
    """``{database: the one candidate holding it}`` when EVERY database is held by exactly one of
    the agent's servers (the called one included, as a pseudo-candidate) and no server holds them
    all; otherwise {} (unknown or ambiguous never refuses)."""
    pool = list(candidates)
    if target_server and all(c.server_id != str(target_server) for c in pool):
        pool.append(Candidate(str(target_server), None, "", label_of(target_server)))
    out: Dict[str, Candidate] = {}
    for d in databases or ():
        hs = [c for c in pool if holds(user_uuid, c.server_id, d) is True]
        if len(hs) != 1:
            return {}
        out[d] = hs[0]
    if len({c.server_id for c in out.values()}) < 2:
        return {}
    return out


def decide_after(user_uuid, target_server: str, error_text: Any, databases: Sequence[str],
                 candidates: Sequence[Candidate], write: bool = False, unreachable: bool = False) -> Route:
    """After a failed call: send a refused READ once to the server that can read the data.
    ``unreachable`` (the server did not answer at all): only a server KNOWN to hold the data is
    tried — nothing is guessed, since the called server never said whether it holds it."""
    if write or not candidates:
        return Route(STAND)
    if unreachable:
        dbs = tuple(databases or ())
        holders = tuple(c for c in candidates if dbs and holds_all(user_uuid, c.server_id, dbs) is True)
        if len(holders) == 1:
            return Route(REROUTE, holders[0], dbs, holders, "unreachable")
        return Route(STAND, databases=dbs)
    if not not_held_error(error_text):
        return Route(STAND)
    dbs = tuple(databases or ()) or databases_in_error(error_text)
    if dbs:
        learn_outcome(user_uuid, target_server, dbs, held=False)
        holders = tuple(c for c in candidates if holds_all(user_uuid, c.server_id, dbs) is True)
        if len(holders) == 1:
            return Route(REROUTE, holders[0], dbs, holders, "holds")
        if len(holders) > 1:
            return Route(REFUSE, None, dbs, holders, "several")
        unknown = tuple(c for c in candidates if holds_all(user_uuid, c.server_id, dbs) is None)
    else:
        unknown = tuple(candidates)
    if len(unknown) == 1:
        return Route(REROUTE, unknown[0], dbs, unknown, "only_other")
    if unknown:
        return Route(REFUSE, None, dbs, unknown, "several")
    return Route(STAND, databases=dbs)


# ---------------------------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------------------------

def _dbs_words(dbs: Sequence[str]) -> str:
    return ", ".join(dbs) if dbs else "the data this call reads"


def reroute_note(route: Route, from_label: str, called_name: str) -> str:
    to = route.to
    if route.why == "holds":
        return (f"[Routing] Uderia ran this call on server '{to.label}' ({to.tool_name}), which can "
                f"read {_dbs_words(route.databases)}; the tool you called ({called_name}) reaches "
                f"server '{from_label}', which cannot. For {_dbs_words(route.databases)} call "
                f"{to.tool_name} directly.")
    if route.why == "planned":
        return (f"[Routing] Uderia ran this call on server '{to.label}' ({to.tool_name}), the server "
                f"the plan chose for this step; the tool called ({called_name}) reaches server "
                f"'{from_label}'.")
    if route.why == "unreachable":
        return (f"[Routing] Server '{from_label}' could not be reached; Uderia ran this call on server "
                f"'{to.label}' ({to.tool_name}), which also holds {_dbs_words(route.databases)}.")
    return (f"[Routing] Server '{from_label}' refused this call ({called_name}); Uderia ran it on the "
            f"agent's other server '{to.label}' ({to.tool_name}).")


def refusal_text(route: Route, from_label: str, called_name: str) -> str:
    if route.why == "split":
        where = "; ".join(f"{d} is on server '{c.label}'" + (f" ({c.tool_name})" if c.tool_name else "")
                          for d, c in zip(route.databases, route.candidates))
        return (f"This statement reads databases on different MCP servers ({where}). No single "
                f"server can run it. Query each server with its own tool, then combine the results.")
    names = ", ".join(f"{c.tool_name} (server '{c.label}')" for c in route.candidates)
    return (f"Server '{from_label}' ({called_name}) cannot read {_dbs_words(route.databases)}. "
            f"Several of this agent's servers can: {names}. Call the one whose data the question "
            f"is about.")


# ---------------------------------------------------------------------------------------------
# One decision for both tool lanes (implementation plan stage 1)
#
# The Optimize lane (adapter.invoke_mcp_tool) and the ReAct lane (tool_enforcement's wrapped
# coroutine) run a call in different ways, but WHERE it runs is one decision: these functions
# make it, and build the one re-route record both lanes stamp. A lane keeps only how it runs
# the call and what it records afterwards. What legitimately differs between the lanes is
# declared in ``SourceLane`` — never hand-coded at a call site (the ``SqlLane`` precedent in
# tool_enforcement). Scheduling (parallel phases, per-server concurrency, merging within one
# server) is NOT decided here: it belongs to the Optimize planner and phase executor.
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceLane:
    """What differs between the tool lanes when a multi-server agent's call is routed.

    ``plan_bound``: a call may carry the server its PLAN chose (``planned_server``) and routing
    honours it — the Optimize planner decides where a phase runs; the ReAct lane has no plan.
    """
    name: str
    plan_bound: bool


SOURCE_LANE_OPTIMIZE = SourceLane("optimize", plan_bound=True)
SOURCE_LANE_REACT = SourceLane("react", plan_bound=False)


@dataclass(frozen=True)
class CallDecision:
    """Where one call runs. ``refusal`` (REFUSE): the text the caller returns instead of running
    it (before the call) or adds to the failed result (after it). ``info`` (REROUTE): the
    re-route record, the one shape both lanes stamp."""
    action: str
    to: Optional[Candidate] = None
    databases: Tuple[str, ...] = ()
    refusal: Optional[str] = None
    info: Optional[Dict[str, Any]] = None
    #: a "split" refusal names where each database is (``database → server id``) so the phase
    #: executor can decide the split itself (stage 3b) instead of asking an LLM
    split: Optional[Dict[str, str]] = None


_STAND = CallDecision(STAND)


def current_phase() -> Optional[int]:
    """The Optimize plan phase whose call is running (None outside a plan)."""
    try:
        from trusted_data_agent.agent.result_sets import current_phase as _cp
        return _cp()
    except Exception:  # noqa: BLE001
        return None


def reroute_info(route: Route, from_label: str, called: str) -> Dict[str, Any]:
    """The re-route record: the tool the model called, the server it would have reached, where
    the call went, the note the agent reads, and the plan phase it ran in (parallel phases
    record into one ledger — each entry names its own phase)."""
    return {"called": called, "from_label": from_label, "databases": list(route.databases),
            "to": route.to.tool_name, "server_id": route.to.server_id,
            "note": reroute_note(route, from_label, called), "phase": current_phase()}


def routed_source(info: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The source stamp of a call Uderia re-routed: the server that really answered, named by
    the tool it ran as, plus what the model called and the server that would have got it. Both
    lanes stamp this ONE shape (the Optimize result's ``mcp_source``, the ReAct event's
    ``source``). ``fallback``: the stamp to extend when the answering server cannot be named."""
    try:
        from trusted_data_agent.core.agent_mcp_servers import call_source as _cs
        src = _cs(info.get("to"), info.get("server_id")) or (dict(fallback) if fallback else None)
    except Exception:  # noqa: BLE001
        src = dict(fallback) if fallback else None
    if not src:
        return None
    try:
        from trusted_data_agent.core.tool_identity import parse as _tp
        src["alias"] = _tp(info.get("to") or "").alias
    except Exception:  # noqa: BLE001
        pass
    src.update({"reason": "routed", "called": info.get("called"),
                "from_server": info.get("from_label"), "databases": list(info.get("databases") or [])})
    return src


def call_routable(tool_name: Optional[str], own_server: Optional[str]) -> bool:
    """A call routing may act on: a multi-server turn with routing on, an MCP tool (never a
    ``TDA_`` framework tool, never a platform connector's tool) with a known server."""
    if not tool_name or str(tool_name).startswith("TDA_") or not own_server or not active():
        return False
    try:
        from trusted_data_agent.core.platform_connector_registry import get_connector_for_tool as _gc
        if _gc(tool_name):
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _candidates(tool_name: str, own_server: str, arguments: Dict[str, Any],
                available) -> List[Candidate]:
    from trusted_data_agent.core.tool_identity import base_name
    return siblings(base_name(tool_name), own_server, available, arguments)


async def plan_before(tool_name: str, own_server: Optional[str], arguments: Any, *,
                      lane: SourceLane, available=None,
                      planned_server: Optional[str] = None) -> CallDecision:
    """Before the call. ``available``: the tool names the lane can actually run (None = judge by
    each server's own selection). ``planned_server`` (a plan-bound lane only): the server the
    plan chose for this call — honoured unless that server is known not to hold the data."""
    try:
        if not call_routable(tool_name, own_server):
            return _STAND
        args = dict(arguments or {}) if isinstance(arguments, dict) else {}
        dbs = referenced_databases(args)
        if is_write_call(args):
            return CallDecision(STAND, databases=dbs)
        cands = _candidates(tool_name, own_server, args, available)
        if not cands:
            return CallDecision(STAND, databases=dbs)
        user = turn_user()
        from_label = label_of(own_server)
        if lane.plan_bound and planned_server and str(planned_server) != str(own_server):
            planned = next((c for c in cands if c.server_id == str(planned_server)), None)
            if planned is not None and (not dbs or holds_all(user, planned.server_id, dbs) is not False):
                r = Route(REROUTE, planned, dbs, (planned,), "planned")
                return CallDecision(REROUTE, planned, dbs, info=reroute_info(r, from_label, tool_name))
        if not dbs:
            return CallDecision(STAND)
        await ensure_lists(user, [own_server] + [c.server_id for c in cands])
        r = decide_before(user, own_server, dbs, cands, False)
        if r.action == REFUSE:
            split = ({d: c.server_id for d, c in zip(r.databases, r.candidates)}
                     if r.why == "split" else None)
            return CallDecision(REFUSE, None, dbs, refusal=refusal_text(r, from_label, tool_name), split=split)
        if r.action == REROUTE:
            return CallDecision(REROUTE, r.to, dbs, info=reroute_info(r, from_label, tool_name))
        return CallDecision(STAND, databases=dbs)
    except Exception as exc:  # noqa: BLE001 — routing must never break a call
        logger.warning(f"[source routing] decision skipped for '{tool_name}': {exc}")
        return _STAND


async def plan_after(tool_name: str, own_server: Optional[str], arguments: Any, error_text: Any, *,
                     lane: SourceLane, available=None, unreachable: bool = False) -> CallDecision:
    """After a failed call: a READ the called server cannot run ("cannot read that", or the
    server did not answer) goes once to the agent's server that can."""
    try:
        if not call_routable(tool_name, own_server):
            return _STAND
        args = dict(arguments or {}) if isinstance(arguments, dict) else {}
        if is_write_call(args) or not (unreachable or not_held_error(error_text)):
            return _STAND
        cands = _candidates(tool_name, own_server, args, available)
        if not cands:
            return _STAND
        user = turn_user()
        dbs = referenced_databases(args)
        if unreachable and dbs:
            await ensure_lists(user, [c.server_id for c in cands])
        r = decide_after(user, own_server, error_text, dbs, cands, False, unreachable=unreachable)
        from_label = label_of(own_server)
        if r.action == REROUTE:
            return CallDecision(REROUTE, r.to, r.databases, info=reroute_info(r, from_label, tool_name))
        if r.action == REFUSE:
            return CallDecision(REFUSE, None, r.databases, refusal=refusal_text(r, from_label, tool_name))
        return CallDecision(STAND, databases=r.databases)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[source routing] retry decision skipped for '{tool_name}': {exc}")
        return _STAND
