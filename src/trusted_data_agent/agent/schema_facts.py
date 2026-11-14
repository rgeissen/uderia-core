"""Schema facts for SQL the agent wrote before it knew the schema (multi-source plan, stage 3.0).

The Optimize planner writes each data phase's SQL at planning time. With no knowledge graph and
no learned plan it guesses table and column names; the database refuses the statement, and until
now the correction step asked the LLM what to do — which answered "describe the table", and that
describe call then ended the phase as if it were the answer.

This module is the one place that answers "what does this table really hold?" for a statement:

* ``sql_tables``        — the (database, table) pairs a statement reads (CTE names excluded);
* ``known_columns``     — what the session already knows (the preflight column store: the
                          knowledge graph's lists and every describe recorded before), 0 calls;
* ``fetch_columns``     — the rest, read from the database through the agent's own
                          column-description tool (routed like any call, so a multi-server agent
                          reads the server that holds the table), recorded for the session;
* ``fetch_tables``      — a database's table list through the agent's table-list tool;
* ``facts_text``        — the facts as the correction prompt reads them.

Nothing here writes SQL: the facts are handed to the correction step, which rewrites the SAME
statement. Every read fails soft — no facts means the pre-existing behaviour.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

app_logger = logging.getLogger("quart.app")

COLUMN_TOOL = "base_columnDescription"
TABLE_LIST_TOOL = "base_tableList"
MAX_TABLES = 4          # a correction reads at most this many tables
MAX_COLUMNS_SHOWN = 80  # per table, in the prompt

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)", re.IGNORECASE)
_CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z_][\w]*)\s+AS\s*\(", re.IGNORECASE)
_COLUMN_KEYS = ("ColumnName", "column_name", "COLUMN_NAME", "Column", "column", "name", "Name")
_TABLE_KEYS = ("TableName", "table_name", "TABLE_NAME", "Table", "table", "name", "Name")


from trusted_data_agent.core.tool_identity import base_name  # the one tool-name rule


def _base(name: str) -> str:
    return base_name(str(name or ""))


def sql_tables(sql: Any) -> List[Tuple[Optional[str], str]]:
    """The tables a statement reads, as ``(database or None, table)``, in order, without
    repeats and without the statement's own CTE names. Pure."""
    if not isinstance(sql, str) or not sql.strip():
        return []
    out: List[Tuple[Optional[str], str]] = []
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read="teradata")
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for t in tree.find_all(exp.Table):
            name = t.name
            if not name or (not t.db and name.lower() in ctes):
                continue
            pair = (t.db or None, name)
            if pair not in out:
                out.append(pair)
        return out
    except Exception:  # noqa: BLE001 — the regex reading below
        pass
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(sql)}
    for m in _TABLE_REF_RE.finditer(sql):
        ref = m.group(1)
        db, _, tbl = ref.rpartition(".")
        if not db and tbl.lower() in ctes:
            continue
        pair = (db or None, tbl)
        if pair not in out:
            out.append(pair)
    return out


def tool_for(role_tool: str, available: Iterable[str]) -> Optional[str]:
    """The agent's own tool for a role (its base name equals ``role_tool``): the plain name
    first (the first server's), else a qualified one. None when the agent has no such tool."""
    names = [str(n) for n in (available or [])]
    if role_tool in names:
        return role_tool
    hits = sorted(n for n in names if base_name(n) == role_tool)
    return hits[0] if hits else None


def _rows(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict) or result.get("status") == "error":
        return []
    rows = result.get("results")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _pick(row: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def columns_from_result(result: Any) -> List[str]:
    """The column names a column-description result lists, in order, without repeats."""
    out: List[str] = []
    for r in _rows(result):
        c = _pick(r, _COLUMN_KEYS)
        if c and c not in out:
            out.append(c)
    return out


def tables_from_result(result: Any) -> List[str]:
    out: List[str] = []
    for r in _rows(result):
        t = _pick(r, _TABLE_KEYS)
        if t and t not in out:
            out.append(t)
    return out


def kg_table_columns(executor) -> Dict[str, List[str]]:
    """``{'db.table': [col, ...]}`` from the turn's knowledge-graph enrichment event; {} when no
    graph (or no schema) was in play. The one reading of the graph's per-turn table lists."""
    ev = getattr(executor, "kg_enrichment_event", None) or {}
    out: Dict[str, List[str]] = {}
    for enr in (ev.get("enrichments") or []) if isinstance(ev, dict) else []:
        for tbl, cols in ((enr or {}).get("table_columns") or {}).items():
            if isinstance(cols, list):
                out[str(tbl)] = [str(c) for c in cols]
    return out


def known_columns(session_id: Optional[str], table: str) -> Optional[List[str]]:
    """The complete column list the session already holds for a table (by its leaf name), or
    None. The knowledge graph's lists and every earlier describe land here."""
    if not session_id or not table:
        return None
    try:
        from trusted_data_agent.agent.preflight import columns as _pc
        entry = _pc._entry(session_id)
        if not entry:
            return None
        leaf = str(table).rpartition(".")[2].strip().lower()
        cols = (entry.get("display") or {}).get(leaf)
        return list(cols) if cols else None
    except Exception:  # noqa: BLE001
        return None


def _record(session_id: Optional[str], columns_by_table: Dict[str, List[str]]) -> None:
    if not session_id or not columns_by_table:
        return
    try:
        from trusted_data_agent.agent.preflight import columns as _pc
        _pc.record_columns(session_id, columns_by_table, origin="dictionary")
    except Exception:  # noqa: BLE001
        pass


def _label(db: Optional[str], table: str) -> str:
    return f"{db}.{table}" if db else table


async def _invoke(executor, tool: str, arguments: Dict[str, Any]) -> Any:
    from trusted_data_agent.mcp_adapter import adapter as mcp_adapter
    result, _, _ = await mcp_adapter.invoke_mcp_tool(
        executor.dependencies["STATE"], {"tool_name": tool, "arguments": arguments},
        user_uuid=executor.user_uuid, session_id=executor.session_id,
        profile_id=getattr(executor, "active_profile_id", None))
    return result


def _column_args(db: str, table: str) -> Dict[str, Any]:
    """The table under every name the column tool may know it by — the same synonym set the
    column orchestrator passes; the adapter keeps the one the tool's schema declares."""
    args: Dict[str, Any] = {"database_name": db}
    try:
        from trusted_data_agent.core.config import AppConfig
        syn = AppConfig.ARGUMENT_SYNONYM_MAP.get("object_name", {"object_name", "table_name"})
    except Exception:  # noqa: BLE001
        syn = {"object_name", "table_name"}
    for s in syn:
        args[s] = table
    return args


def _available_tools(executor) -> List[str]:
    try:
        return list(((executor.dependencies or {}).get("STATE") or {}).get("mcp_tools") or {})
    except Exception:  # noqa: BLE001
        return []


async def fetch_columns(executor, tables: List[Tuple[Optional[str], str]],
                        default_db: Optional[str] = None) -> Tuple[Dict[str, List[str]], List[str]]:
    """The real columns of each table: from the session first, else read through the agent's
    column-description tool. Returns ``({"db.table": [cols]}, notes)`` — ``notes`` names a table
    that could not be read (so the facts never claim more than was read). Never raises."""
    found: Dict[str, List[str]] = {}
    notes: List[str] = []
    session_id = getattr(executor, "session_id", None)
    tool = tool_for(COLUMN_TOOL, _available_tools(executor))
    fetched: Dict[str, List[str]] = {}
    for db, table in (tables or [])[:MAX_TABLES]:
        db = db or default_db
        label = _label(db, table)
        cols = known_columns(session_id, table)
        if cols:
            found[label] = cols
            continue
        if not tool or not db:
            notes.append(f"{label}: no column-description tool" if not tool
                         else f"{label}: its database is not named in the statement")
            continue
        try:
            result = await _invoke(executor, tool, _column_args(db, table))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label}: could not be read ({str(exc)[:120]})")
            continue
        cols = columns_from_result(result)
        if cols:
            found[label] = cols
            fetched[label] = cols
        else:
            err = result.get("error") if isinstance(result, dict) else None
            notes.append(f"{label}: " + (f"could not be read ({str(err)[:160]})" if err
                                         else "the database returned no columns (it may not exist)"))
    _record(session_id, fetched)
    return found, notes


async def fetch_tables(executor, database: Optional[str]) -> Optional[List[str]]:
    """The tables of one database through the agent's table-list tool, or None."""
    if not database:
        return None
    tool = tool_for(TABLE_LIST_TOOL, _available_tools(executor))
    if not tool:
        return None
    try:
        result = await _invoke(executor, tool, {"database_name": database})
    except Exception:  # noqa: BLE001
        return None
    tables = tables_from_result(result)
    return tables or None


def facts_text(columns_by_table: Dict[str, List[str]], notes: Optional[List[str]] = None,
               tables_by_db: Optional[Dict[str, List[str]]] = None) -> str:
    """The facts as the correction prompt reads them; "" when there are none."""
    lines: List[str] = []
    for label, cols in (columns_by_table or {}).items():
        shown = cols[:MAX_COLUMNS_SHOWN]
        more = f" (+{len(cols) - len(shown)} more)" if len(cols) > len(shown) else ""
        lines.append(f"- {label}: {', '.join(shown)}{more}")
    for db, tables in (tables_by_db or {}).items():
        lines.append(f"- tables in {db}: {', '.join(tables)}")
    for n in notes or []:
        lines.append(f"- (not read) {n}")
    return "\n".join(lines)


FACTS_HEADER = ("--- SCHEMA FACTS (read from the database just now — use ONLY these names) ---")
FACTS_DIRECTIVE = (
    "These are the real tables and columns. Rewrite the failing statement for the SAME tool "
    "using only these names and return it as {\"tool_name\": \"<the same tool>\", "
    "\"arguments\": {...}}. Do not call a tool that lists or describes tables or columns — "
    "the facts are above. If a value the question needs is in none of these columns, answer "
    "with FINAL_ANSWER: and say which column is missing.")


def is_discovery_tool(tool_name: Any) -> bool:
    try:
        from trusted_data_agent.agent.plan_compiler import DISCOVERY_TOOLS
    except Exception:  # noqa: BLE001
        return False
    return base_name(str(tool_name or "")) in DISCOVERY_TOOLS


def is_discovery_standin(failed_action: Any, corrected_action: Any) -> bool:
    """Did a correction replace a failed SQL call with a discovery call? Then the discovery
    result is facts for the statement, and the statement must be retried — not answered by
    the discovery result."""
    if not isinstance(failed_action, dict) or not isinstance(corrected_action, dict):
        return False
    sql = (failed_action.get("arguments") or {}).get("sql")
    return (isinstance(sql, str) and bool(sql.strip())
            and is_discovery_tool(corrected_action.get("tool_name"))
            and not is_discovery_tool(failed_action.get("tool_name")))


def _arg(action: Dict[str, Any], *names: str) -> Optional[str]:
    args = action.get("arguments") or {}
    for n in names:
        v = args.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def discovery_facts_text(action: Any, result: Any) -> str:
    """The facts a successful discovery call established, as the correction prompt reads them."""
    if not isinstance(action, dict):
        return ""
    base = base_name(str(action.get("tool_name") or ""))
    db = _arg(action, "database_name", "db_name")
    if base == TABLE_LIST_TOOL:
        tables = tables_from_result(result)
        return facts_text({}, tables_by_db={db or "the database": tables}) if tables else ""
    cols = columns_from_result(result)
    if cols:
        table = _arg(action, "table_name", "object_name", "obj_name") or "the table"
        return facts_text({_label(db, table): cols})
    return ""


def record_discovery(session_id: Optional[str], action: Any, result: Any) -> None:
    """Record a successful describe call's columns for the session (so the correction that
    follows, and every later statement, reads them for free)."""
    if not isinstance(action, dict):
        return
    table = _arg(action, "table_name", "object_name", "obj_name")
    cols = columns_from_result(result)
    if table and cols and base_name(str(action.get("tool_name") or "")) == COLUMN_TOOL:
        _record(session_id, {table: cols})


_DB_KEYS = ("DataBaseName", "DatabaseName", "database_name", "DATABASE_NAME", "Database", "database")


def tables_seen(executor) -> Dict[str, List[str]]:
    """``{database: [table, ...]}`` from every table list a phase of THIS turn already returned
    (``workflow_state``). What the turn has seen, not what exists."""
    out: Dict[str, List[str]] = {}
    state = getattr(executor, "workflow_state", None) or {}

    def _walk(v, depth=0):
        if depth > 4:
            return
        if isinstance(v, list):
            for x in v:
                _walk(x, depth + 1)
        elif isinstance(v, dict):
            rows = v.get("results")
            if isinstance(rows, list):
                for r in rows:
                    if isinstance(r, dict):
                        db, t = _pick(r, _DB_KEYS), _pick(r, ("TableName", "table_name", "TABLE_NAME"))
                        if db and t and t not in out.setdefault(db, []):
                            out[db].append(t)
    for k, v in state.items():
        if str(k).startswith("result_of_phase_"):
            _walk(v)
    return out


def database_for_table(executor, table: str) -> Optional[Tuple[str, str]]:
    """The ONE database this turn's table lists place ``table`` in, as ``(database, the table's
    real spelling)``; None when none or several do."""
    leaf = str(table or "").rpartition(".")[2].lower()
    hits = [(db, t) for db, ts in tables_seen(executor).items() for t in ts if t.lower() == leaf]
    return hits[0] if len(hits) == 1 else None


def qualify_table(sql: str, table: str, database: str, real: str) -> Optional[str]:
    """``sql`` with the unqualified table ``table`` (at FROM/JOIN) written as
    ``database.real``; None when there is no such reference."""
    pat = re.compile(r"(\b(?:FROM|JOIN)\s+)" + re.escape(table) + r"(?![\w.])", re.IGNORECASE)
    new, n = pat.subn(lambda m: f"{m.group(1)}{database}.{real}", sql)
    return new if n else None


def _norm_table(t: str) -> str:
    t = re.sub(r"[^a-z0-9]", "", str(t or "").lower())
    return t[:-1] if len(t) > 3 and t.endswith("s") else t


def settle_describe_args(executor, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """An inserted describe phase names the table the PLAN guessed. When this turn has already
    listed the database's tables and the guess is not among them, the one listed table it
    plainly means (the same name in another case or plural, or the only one it contains / is
    contained in) is described instead. Returns ``(arguments, note)`` — note is None when
    nothing changed. Several candidates, or none, leave the guess as written."""
    args = dict(arguments or {})
    db = args.get("database_name")
    table = args.get("table_name")
    if not isinstance(db, str) or not isinstance(table, str) or not table:
        return args, None
    seen = next((ts for d, ts in tables_seen(executor).items() if d.lower() == db.lower()), None)
    if not seen or any(t.lower() == table.lower() for t in seen):
        return args, None
    want = _norm_table(table)
    hits = [t for t in seen if _norm_table(t) == want]
    if not hits:
        hits = [t for t in seen if _norm_table(t) and (_norm_table(t) in want or want in _norm_table(t))]
    if len(hits) != 1:
        return args, None
    args["table_name"] = hits[0]
    return args, f"the plan named '{table}'; this turn's table list for {db} holds '{hits[0]}' — described that"


MAX_LISTED_DATABASES = 3


def databases_seen(executor) -> List[str]:
    """The USER databases a database listing of THIS turn returned (system databases and user
    accounts excluded), in order. What the turn has seen, not what exists."""
    try:
        from trusted_data_agent.agent.grounding.primitives import SYSTEM_DBS
        system = {str(d).lower() for d in SYSTEM_DBS}
    except Exception:  # noqa: BLE001
        system = set()
    out: List[str] = []
    state = getattr(executor, "workflow_state", None) or {}

    def _walk(v, depth=0):
        if depth > 4:
            return
        if isinstance(v, list):
            for x in v:
                _walk(x, depth + 1)
        elif isinstance(v, dict) and isinstance(v.get("results"), list):
            for r in v["results"]:
                if not isinstance(r, dict) or _pick(r, ("TableName", "table_name")):
                    continue
                db = _pick(r, _DB_KEYS)
                kind = str(r.get("DBType") or r.get("db_type") or "").lower()
                if (db and db.lower() not in system and kind != "user"
                        and not db.lower().startswith("td_") and db not in out):
                    out.append(db)
    for k, v in state.items():
        if str(k).startswith("result_of_phase_"):
            _walk(v)
    return out


def databases_in_case(case: Any) -> set:
    """The database names (lower-case) every SQL string in a champion case reads — the databases
    that case's success vouches for. Same walk as ``tables_in_case``."""
    out: set = set()

    def _walk(v, depth=0):
        if depth > 12:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "sql" and isinstance(x, str):
                    out.update(db.lower() for db, _ in sql_tables(x) if db)
                else:
                    _walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                _walk(x, depth + 1)
    _walk(case)
    return out


def tables_in_case(case: Any) -> set:
    """The table leaves (lower-case) every SQL string anywhere in a champion case reads — the
    tables that case's success vouches for. A walk over any shape, so a change in how a case
    stores its plan never silently empties it."""
    out: set = set()

    def _walk(v, depth=0):
        if depth > 12:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "sql" and isinstance(x, str):
                    out.update(t.lower() for _, t in sql_tables(x))
                else:
                    _walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                _walk(x, depth + 1)
    _walk(case)
    return out
