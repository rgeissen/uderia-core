"""MCP catalog provider — the profile's data-source tools, resolved to catalog ROLES.

No tool name is hard-coded. :func:`resolve_roles_by_shape` scores every AVAILABLE tool
against each role by the words in its name and description (``databaseList`` lists
databases; ``columnDescription`` describes columns; ``readQuery`` runs a SELECT) and
picks the argument names from the tool's own declared args — deterministic, zero
tokens, and it degrades honestly: a role with no matching tool is simply absent from
the provider's capabilities. The LLM-based derivation the KG constructor uses stays
available to a caller that wants it (pass ``roles`` explicitly).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set

from trusted_data_agent.agent.kg_constructors.database_context import (
    COLUMN_NAME_FIELDS, TABLE_NAME_FIELDS, TYPE_FIELDS, _DB_ARG_CANDIDATES,
    _TABLE_ARG_CANDIDATES, _call_first_nonempty, _find_field, _probe_two_args,
    _rows_from_result)

from .base import CatalogError, CatalogProvider
from .capabilities import CatalogCapability as C
from .sql_runner import jsonable, single_select_guard
from .types import ColumnInfo, DatabaseInfo, ObjectDetail, ObjectInfo, PreviewResult

logger = logging.getLogger("quart.app")

ROLE_KEYS = ("list_databases", "list_tables", "describe_columns", "table_ddl", "run_sql")

_DB_NAME_FIELDS = {"DatabaseName", "database_name", "DataBaseName", "name", "SchemaName", "schema_name"}
_KIND_FIELDS = {"TableKind", "table_kind", "Kind", "kind", "Type", "type", "ObjectType", "object_type"}
_COMMENT_FIELDS = {"CommentString", "comment", "Comment", "description", "Description"}
_SQL_ARG_CANDIDATES = ["sql", "query", "sql_query", "statement", "sql_statement"]


def _words(s: str) -> Set[str]:
    return set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(s or "")).lower()))


# words that make a tool a candidate for a role: (must-have any, bonus)
_ROLE_WORDS = {
    "list_databases":   ({"database", "databases", "schemas", "schema"}, {"list", "all"}),
    "list_tables":      ({"table", "tables", "objects"}, {"list"}),
    "describe_columns": ({"column", "columns", "describe", "description"}, {"desc", "info", "detail"}),
    "table_ddl":        ({"ddl", "show", "definition"}, {"table", "view"}),
    "run_sql":          ({"query", "sql", "select", "read"}, {"execute", "run", "read"}),
}
# a tool whose NAME carries any of these is an analysis / administration / security / plotting
# tool, not a catalog reader — `dba_tableSqlList` lists SQL that touched a table and
# `qlty_columnSummary` profiles a column's values; both carry the catalog word and both
# answered the wrong question on a real profile
_NOT_CATALOG = {"qlty", "quality", "dba", "sec", "security", "plot", "chart", "usage", "affinity",
                "space", "stats", "statistics", "summary", "impact", "preview", "missing", "negative",
                "deviation", "categories", "univariate", "permissions", "roles", "session", "version",
                "flow", "feature", "delay", "resusage", "write", "insert", "update", "delete", "dml"}


def _score(tool: Dict[str, Any], role: str) -> int:
    must, bonus = _ROLE_WORDS[role]
    nwords = _words(tool.get("name", ""))
    dwords = _words((tool.get("description") or "")[:200])
    if role != "run_sql" and (nwords & _NOT_CATALOG):
        return 0
    if role == "run_sql" and (nwords & {"write", "insert", "update", "delete", "ddl", "dml", "dba", "sec", "qlty", "plot", "usage", "space"}):
        return 0
    score = 0
    if nwords & must:
        score += 10
    if dwords & must:
        score += 3
    score += 2 * len(nwords & bonus) + len(dwords & bonus)
    # the canonical shape wins ties: `<x>List` for a lister, `<x>Description` for a describer
    if role == "list_tables" and "list" in nwords and (nwords & {"table", "tables"}):
        score += 5
    if role == "list_databases" and "list" in nwords and (nwords & {"database", "databases"}):
        score += 5
    if role == "describe_columns" and (nwords & {"description", "describe"}) and (nwords & {"column", "columns"}):
        score += 5
    if role == "run_sql" and (nwords & {"read", "query"}) and not (nwords & {"list", "table", "column"}):
        score += 3
    if role == "list_tables" and (nwords & {"column", "columns", "sql", "ddl"}):
        return 0
    if role == "list_databases" and (nwords & {"table", "tables", "column", "columns"}):
        return 0
    if role == "describe_columns" and (nwords & {"sql", "ddl", "list"}):
        return 0
    return score


def _pick_arg(args: List[str], candidates: List[str]) -> Optional[str]:
    low = {a.lower(): a for a in args or []}
    for c in candidates:
        if c.lower() in low:
            return low[c.lower()]
    return None


def resolve_roles_by_shape(available_tools: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, str]]]:
    """Pure: ``[{name, description, args?}]`` → ``{role: {tool, db_arg?, table_arg?, sql_arg?}|None}``."""
    out: Dict[str, Optional[Dict[str, str]]] = {k: None for k in ROLE_KEYS}
    for role in ROLE_KEYS:
        best, best_score = None, 0
        for t in available_tools or []:
            if not isinstance(t, dict) or not t.get("name") or str(t["name"]).startswith("TDA_"):
                continue
            s = _score(t, role)
            if s > best_score:
                best, best_score = t, s
        if best is None or best_score < 10:
            continue
        args = list(best.get("args") or [])
        entry: Dict[str, str] = {"tool": str(best["name"])}
        da = _pick_arg(args, _DB_ARG_CANDIDATES)
        ta = _pick_arg(args, _TABLE_ARG_CANDIDATES)
        sa = _pick_arg(args, _SQL_ARG_CANDIDATES)
        if da:
            entry["db_arg"] = da
        if ta:
            entry["table_arg"] = ta
        if sa:
            entry["sql_arg"] = sa
        out[role] = entry
    return out


async def available_tools_live(state: Dict[str, Any], server_id: Optional[str],
                               user_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
    """The profile's MCP tools with their LIVE argument names, straight from the server.

    The state snapshot (`available_tools_from_state`) is rebuilt from the persisted
    classification, which was taken when the profile was last classified — a server
    that has since renamed a parameter (`obj_name` → `table_name` on a redeployed
    Teradata MCP image) leaves the snapshot stale, and the adapter's realignment then
    faithfully sends the stale name. Reading the live schema costs one `list_tools`
    round trip and is cached per server for the process. Falls back to the snapshot on
    any failure (no client, no server id, a closed session)."""
    snapshot = available_tools_from_state(state)
    client = state.get("mcp_client") if isinstance(state, dict) else None
    if client is None:
        return snapshot
    # The client keys its sessions by ITS OWN server id (validated + defaulted by the
    # platform's resolver), not by the profile's mcpServerId — the same id the adapter uses.
    if user_uuid:
        try:
            from trusted_data_agent.core.config import get_user_mcp_server_id
            server_id = get_user_mcp_server_id(user_uuid) or server_id
        except Exception:  # noqa: BLE001
            pass
    if not server_id:
        return snapshot
    cached = _LIVE_SCHEMA.get(server_id)
    if cached is not None:
        return _merge_live(snapshot, cached)
    try:
        async with client.session(server_id) as sess:
            res = await sess.list_tools()
            live = {}
            for t in (getattr(res, "tools", None) or []):
                props = ((getattr(t, "inputSchema", None) or {}).get("properties") or {})
                live[t.name] = [k for k in props.keys() if k != "persist"]
            _LIVE_SCHEMA[server_id] = live
            return _merge_live(snapshot, live)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[dbcatalog/mcp] live schema unavailable for %s (%s: %s) — using the classification snapshot",
                       server_id, type(exc).__name__, exc)
        return snapshot


_LIVE_SCHEMA: Dict[str, Dict[str, List[str]]] = {}


def _merge_live(snapshot: List[Dict[str, Any]], live: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    out = []
    for t in snapshot:
        args = live.get(t["name"])
        if args is not None and args != t.get("args"):
            t = {**t, "args": args, "live": True}
        out.append(t)
    return out


def reconcile_state_schema(state: Dict[str, Any], tool_name: str, live_args: List[str]) -> bool:
    """Point the adapter's realignment at the LIVE argument names for one tool.

    `invoke_mcp_tool` canonicalises every synonym (`table_name` → `object_name`) and
    then realigns to whatever `STATE['mcp_tools'][tool].args` says the tool takes — so
    while that entry is stale, no caller can reach the parameter the server actually
    accepts. Rewriting the one entry's `args` (values kept as plain dicts, the shape
    `configuration_service` builds) makes the next call land. Returns True if changed."""
    try:
        tools = state.get("mcp_tools") if isinstance(state, dict) else None
        t = (tools or {}).get(tool_name)
        if t is None or not isinstance(getattr(t, "args", None), dict):
            return False
        if set(t.args.keys()) == set(live_args):
            return False
        t.args = {a: (t.args.get(a) or {"name": a}) for a in live_args}
        logger.warning("[dbcatalog/mcp] reconciled the stale argument schema of '%s' to the server's: %s "
                       "(re-classify the profile to persist it)", tool_name, live_args)
        return True
    except Exception:  # noqa: BLE001
        return False


def available_tools_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The profile's real MCP tools + their declared arg names, from the process STATE
    (the same read the KG constructor performs)."""
    structured = state.get("structured_tools", {}) or {}
    raw = state.get("mcp_tools", {}) or {}
    out: List[Dict[str, Any]] = []
    for cat, tools in structured.items():
        for t in tools or []:
            if not (isinstance(t, dict) and t.get("name")):
                continue
            name = str(t["name"])
            if name.startswith("TDA_") or cat == "Platform Tools" or str(cat).startswith("Platform: "):
                continue
            arg_names: List[str] = []
            rt = raw.get(name)
            try:
                if rt is not None and isinstance(getattr(rt, "args", None), dict):
                    arg_names = list(rt.args.keys())
            except Exception:  # noqa: BLE001
                arg_names = []
            out.append({"name": name, "description": t.get("description", ""), "args": arg_names})
    return out


class McpCatalogProvider(CatalogProvider):
    provider_kind = "mcp"

    def __init__(self, source_id: str, label: str, invoke: Callable, roles: Dict[str, Any],
                 dialect_hint: str = ""):
        self._id, self._label, self._invoke = source_id, label, invoke
        self._roles = {k: (roles.get(k) if isinstance(roles.get(k), dict) and roles[k].get("tool") else None)
                       for k in ROLE_KEYS}
        self._dialect = (dialect_hint or "").lower()
        self._resolved: Dict[str, Any] = {}   # arg names that actually worked

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    @property
    def roles(self) -> Dict[str, Any]:
        return dict(self._roles)

    def capabilities(self) -> Set[C]:
        caps: Set[C] = set()
        if self._roles.get("list_databases"):
            caps.add(C.LIST_DATABASES)
        if self._roles.get("list_tables"):
            caps.add(C.LIST_OBJECTS)
            caps.add(C.OBJECT_KINDS)
        if self._roles.get("describe_columns"):
            caps.add(C.COLUMNS)
        if self._roles.get("table_ddl"):
            caps.add(C.VIEW_TEXT)
        if self._roles.get("run_sql"):
            caps.update({C.PREVIEW, C.ROW_COUNT})
        return caps

    # ── reads ───────────────────────────────────────────────────────────────────

    async def list_databases(self) -> List[DatabaseInfo]:
        role = self._roles.get("list_databases")
        if not role:
            raise CatalogError("this MCP server exposes no tool that lists databases")
        rows: List[Dict[str, Any]] = []
        for args in ({"scope": "all"}, {}):
            try:
                rows = _rows_from_result(await self._invoke(role["tool"], args))
            except Exception:  # noqa: BLE001
                rows = []
            if rows:
                break
        out, seen = [], set()
        for r in rows:
            n = _find_field(r, _DB_NAME_FIELDS)
            if n and n not in seen:
                seen.add(n)
                out.append(DatabaseInfo(name=n, comment=_find_field(r, _COMMENT_FIELDS) or "",
                                        is_system=n.lower() in {"dbc", "syslib", "sysudtlib", "tdwm", "td_sysfnlib"}))
        return out

    async def list_objects(self, database: str) -> List[ObjectInfo]:
        role = self._roles.get("list_tables")
        if not role:
            raise CatalogError("this MCP server exposes no tool that lists tables")
        rows, used = await _call_first_nonempty(self._invoke, role["tool"], database, role.get("db_arg"),
                                                _DB_ARG_CANDIDATES, TABLE_NAME_FIELDS,
                                                trust_preferred=True)  # declared by the tool's schema
        if used:
            self._resolved["db_arg"] = used
        out, seen = [], set()
        for r in rows:
            n = _find_field(r, TABLE_NAME_FIELDS)
            if not n or n in seen:
                continue
            seen.add(n)
            k = (_find_field(r, _KIND_FIELDS) or "").strip().upper()
            kind = "view" if k.startswith("V") else ("table" if not k or k.startswith(("T", "O", "Q", "B")) else "other")
            out.append(ObjectInfo(database=database, name=n, kind=kind,
                                  comment=_find_field(r, _COMMENT_FIELDS) or ""))
        return out

    async def describe_object(self, database: str, name: str) -> ObjectDetail:
        objs = {o.name.lower(): o for o in await self.list_objects(database)}
        info = objs.get(name.lower()) or ObjectInfo(database=database, name=name)
        detail = ObjectDetail(info=info)
        role = self._roles.get("describe_columns")
        if not role:
            detail.unavailable.append("columns (no column-description tool on this server)")
            return detail
        rows, da, ta = await _probe_two_args(
            self._invoke, role["tool"], database, info.name,
            self._resolved.get("db_arg") or role.get("db_arg"), role.get("table_arg"),
            _DB_ARG_CANDIDATES, _TABLE_ARG_CANDIDATES, COLUMN_NAME_FIELDS,
            trust_preferred=True)   # the role's names come from the tool's declared schema
        cols = []
        for i, r in enumerate(rows):
            cn = _find_field(r, COLUMN_NAME_FIELDS)
            if not cn:
                continue
            cols.append(ColumnInfo(name=cn, data_type=(_find_field(r, TYPE_FIELDS) or "").strip(),
                                   position=i + 1, comment=_find_field(r, _COMMENT_FIELDS) or ""))
        detail.columns = cols
        detail.unavailable += ["indexes", "foreign keys", "statistics", "sizes"]
        if info.kind == "view" and self._roles.get("table_ddl"):
            try:
                ddl = self._roles["table_ddl"]
                res = await self._invoke(ddl["tool"], {ddl.get("db_arg") or da or "database_name": database,
                                                       ddl.get("table_arg") or ta or "table_name": info.name})
                txt = res if isinstance(res, str) else (res.get("results") or res.get("data") or res.get("ddl") or "")
                detail.view_text = txt if isinstance(txt, str) else str(txt)[:4000]
            except Exception:  # noqa: BLE001
                detail.unavailable.append("view text")
        return detail

    async def _sql(self, sql: str, max_rows: int):
        role = self._roles.get("run_sql")
        if not role:
            raise CatalogError("this MCP server exposes no read-only SQL tool")
        res = await self._invoke(role["tool"], {role.get("sql_arg") or "sql": single_select_guard(sql)})
        if isinstance(res, dict) and res.get("status") == "error":
            raise CatalogError(str(res.get("error") or res.get("message") or "query failed")[:300])
        rows = _rows_from_result(res)
        cols = list(rows[0].keys()) if rows else []
        return cols, [[jsonable(r.get(c)) for c in cols] for r in rows[:max_rows]]

    async def existing_functions(self, names) -> Optional[set]:
        """Only a Teradata server is asked (the dictionary read is Teradata's); anything else
        cannot tell."""
        if self._dialect and self._dialect != "teradata":
            return None
        from trusted_data_agent.dbcatalog.teradata_types import existing_functions_sql
        sql = existing_functions_sql(names)
        if not sql:
            return set()
        _cols, rows = await self._sql(sql, 1000)
        return {str(r[0]).strip().upper() for r in rows if r and r[0] is not None}

    async def preview(self, database: str, name: str, limit: int = 50,
                      columns: Optional[List[str]] = None) -> PreviewResult:
        limit = max(1, min(int(limit or 50), 500))
        colsql = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        forms = [f'SELECT TOP {limit} {colsql} FROM "{database}"."{name}"',
                 f'SELECT {colsql} FROM "{database}"."{name}" LIMIT {limit}']
        if "postgres" in self._dialect or "mysql" in self._dialect or "sqlite" in self._dialect:
            forms.reverse()
        last: Optional[Exception] = None
        for sql in forms:
            try:
                cols, rows = await self._sql(sql, limit)
                return PreviewResult(columns=cols, rows=rows, truncated=len(rows) >= limit, sql=sql)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise CatalogError(str(last) if last else "preview failed")

    async def row_count(self, database: str, name: str) -> Optional[int]:
        cols, rows = await self._sql(f'SELECT COUNT(*) AS n FROM "{database}"."{name}"', 1)
        try:
            return int(rows[0][0]) if rows and rows[0] else None
        except (TypeError, ValueError):
            return None
