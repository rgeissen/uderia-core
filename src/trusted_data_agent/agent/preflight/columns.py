"""Preflight COLUMN-existence check — pure + deterministic, 0 tokens.

The class it closes (2026-09-19, prod session 0aa3a650): a weak model's first statement
on a follow-up was `SELECT … B.miles_flown FROM airlines_core.boarding_event B …` — a column
that does not exist — which cost a database round trip (Error 3810), an LLM round to read
it, and then a `SELECT TOP 5 *` to discover the real columns. The knowledge graph held every
column of every table the whole time; only the CAPPED render had been shown.

Per session, the KG enrichment records ``table_columns_full`` — {table_leaf: [every column
leaf]} for the tables in the subgraph, taken from the loaded graph and recorded ONLY when
the graph's constructor is known to list all columns (an industry model, a declared model,
an agent-discovery graph; never an ANDP graph whose `column_metadata` covers a fraction).
Every raw-SQL call is parsed (sqlglot): a column the statement attributes to a recorded
table that the table does not have is a violation NAMING the table's real columns and the
closest matches across the tables in play. Anything the parse cannot attribute — an
unqualified column in a multi-table statement, a SELECT alias reused in WHERE, a table the
session has no complete list for — is no verdict (fail-open by construction).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

from trusted_data_agent.agent.preflight import dialect as _dialect

_COLS: Dict[str, Dict[str, Any]] = {}   # session_id -> {"tables": {leaf: set(cols_lower)}, "display": {leaf: [cols]}, "in_play": set(leaves), "ts": monotonic}
_LOCK = threading.Lock()
_TTL_SECONDS = 1800
_MAX_SUGGESTIONS = 6


#: Where a table's complete column list came from — named in the refusal, so a model (and a
#: reader) knows what vouches for "this column does not exist".
ORIGIN_TEXT = {"ontology": "the knowledge graph", "dictionary": "the database's own dictionary"}


def record_columns(session_id: str, table_columns_full: Any, in_play: bool = False,
                   origin: str = "ontology") -> int:
    """Record the complete column lists (union with what is known). ``in_play``: these are
    the tables the CURRENT turn's enrichment rendered — a suggestion from one of them beats
    one from the rest of the graph. ``origin``: what vouches for completeness — the ontology
    (the KG enrichment) or the source's own dictionary (the Database Navigator's describe).
    Returns the number of tables known afterwards."""
    if not session_id or not isinstance(table_columns_full, dict):
        return len(known_tables(session_id))
    add: Dict[str, List[str]] = {}
    for tbl, cols in table_columns_full.items():
        leaf = str(tbl or "").rpartition(".")[2].strip().lower()
        if not leaf or not isinstance(cols, (list, tuple)) or not cols:
            continue
        add[leaf] = [str(c).strip() for c in cols if str(c).strip()]
    if not add:
        return len(known_tables(session_id))
    with _LOCK:
        entry = _COLS.get(session_id) or {"tables": {}, "display": {}, "in_play": set()}
        for leaf, cols in add.items():
            entry["tables"][leaf] = {c.lower() for c in cols}
            entry["display"][leaf] = list(cols)
            entry.setdefault("origin", {})[leaf] = origin
        if in_play:
            entry["in_play"] = set(add)                    # THIS turn's tables replace the last turn's
        entry["ts"] = time.monotonic()
        _COLS[session_id] = entry
        if len(_COLS) > 1000:
            now = time.monotonic()
            for sid in [s for s, e in _COLS.items() if now - e["ts"] > _TTL_SECONDS]:
                _COLS.pop(sid, None)
        return len(entry["tables"])


def _entry(session_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        entry = _COLS.get(session_id)
        if not entry:
            return None
        if time.monotonic() - entry["ts"] > _TTL_SECONDS:
            _COLS.pop(session_id, None)
            return None
        return entry


def known_tables(session_id: str) -> Dict[str, set]:
    if not session_id:
        return {}
    entry = _entry(session_id)
    return dict(entry["tables"]) if entry else {}


def record_from_event(session_id: str, event: Any) -> int:
    """Record from a ``kg_enrichment_event`` (its ``enrichments[*].table_columns_full``)."""
    n = 0
    try:
        for enr in (event or {}).get("enrichments") or []:
            if isinstance(enr, dict) and isinstance(enr.get("table_columns_full"), dict):
                n = record_columns(session_id, enr["table_columns_full"], in_play=True)
    except Exception:  # noqa: BLE001
        return 0
    return n


def _tokens(ident: str) -> set:
    return {t.lower() for t in re.split(r"[\s_\-\.]+", str(ident or "")) if len(t) >= 3}


def suggestions(session_id: str, column: str, prefer_table: Optional[str] = None) -> List[str]:
    """Columns across the session's recorded tables that share a word with `column`
    (`miles_flown` → `mileage_accrual.base_miles`, `mileage_accrual.total_miles_credited`),
    the named table's own matches first. Bounded."""
    entry = _entry(session_id)
    if not entry:
        return []
    want = _tokens(column)
    if not want:
        return []
    in_play = entry.get("in_play") or set()
    scored = []
    for leaf, cols in entry["display"].items():
        for c in cols:
            hit = len(want & _tokens(c))
            if hit:
                # the named table first, then the tables this turn's enrichment rendered,
                # then the strongest word overlap, then the shorter (plainer) name
                scored.append((0 if leaf == (prefer_table or "").lower() else 1,
                               0 if leaf in in_play else 1, -hit, len(c), f"{leaf}.{c}"))
    scored.sort()
    return [s[4] for s in scored[:_MAX_SUGGESTIONS]]


def sql_column_refs(sql: str) -> List[Dict[str, Any]]:
    """Pure: the column references of ``sql`` that can be ATTRIBUTED to a base table —
    [{"table": leaf_lower, "column": name}] — qualified references resolved through the
    statement's aliases, unqualified ones only when the statement reads exactly one base
    table and defines no CTE or derived table. SELECT aliases and CTE names are never
    columns. Empty when sqlglot is absent or the SQL does not parse."""
    out: List[Dict[str, Any]] = []
    if not sql or not isinstance(sql, str):
        return out
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return out
    try:
        stmts = sqlglot.parse(sql, read=_dialect.read())
    except Exception:
        return out
    for stmt in stmts:
        if stmt is None:
            continue
        ctes = {str(c.alias).lower() for c in stmt.find_all(exp.CTE) if c.alias}
        derived = any(isinstance(sq.parent, (exp.From, exp.Join)) for sq in stmt.find_all(exp.Subquery))
        aliases = {str(a.alias).lower() for a in stmt.find_all(exp.Alias) if a.alias}
        alias_to_leaf: Dict[str, str] = {}
        base: List[str] = []
        for tbl in stmt.find_all(exp.Table):
            leaf = str(tbl.name or "").lower()
            if not leaf or leaf in ctes:
                continue
            alias_to_leaf[leaf] = leaf
            if tbl.alias:
                alias_to_leaf[str(tbl.alias).lower()] = leaf
            if leaf not in base:
                base.append(leaf)
        single = base[0] if (len(base) == 1 and not ctes and not derived) else None
        seen = set()
        for col in stmt.find_all(exp.Column):
            name = str(col.name or "")
            if not name or name == "*" or name.lower() in aliases:
                continue
            q = str(col.table or "").lower()
            if q:
                leaf = alias_to_leaf.get(q)
                if not leaf:
                    continue                      # a qualifier we cannot attribute (a CTE, a derived table)
            elif single:
                leaf = single
            else:
                continue
            key = (leaf, name.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"table": leaf, "column": name})
    return out


def column_violations(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """Column references attributed to a table whose COMPLETE column list the session knows
    and that does not contain the column. Each names the table, the column, the table's
    real columns (bounded) and the closest matches in play. Empty = nothing provable."""
    tables = known_tables(session_id)
    if not tables:
        return []
    entry = _entry(session_id) or {}
    out: List[Dict[str, Any]] = []
    for ref in sql_column_refs(sql):
        cols = tables.get(ref["table"])
        if cols is None:
            continue
        if ref["column"].lower() in cols:
            continue
        real = list((entry.get("display") or {}).get(ref["table"]) or [])
        out.append({"table": ref["table"], "column": ref["column"],
                    "origin": ((entry.get("origin") or {}).get(ref["table"]) or "ontology"),
                    "real_columns": real[:40], "real_count": len(real),
                    "suggestions": suggestions(session_id, ref["column"], prefer_table=ref["table"])})
    return out


def block_result(violations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recovery-shaped: names the missing column, the table's real columns and the closest
    matches; the query was NOT run. The message carries `Column/Parameter '…' does not
    exist` so the Optimize column-not-found strategy engages exactly as on a real 3810."""
    v = violations[0]
    shown = ", ".join(v["real_columns"])
    more = f" (+{v['real_count'] - len(v['real_columns'])} more)" if v["real_count"] > len(v["real_columns"]) else ""
    sugg = ("; closest matches in the tables in play: " + ", ".join(v["suggestions"])) if v.get("suggestions") else ""
    who = ORIGIN_TEXT.get(v.get("origin") or "ontology", ORIGIN_TEXT["ontology"])
    msg = (f"[Preflight] Column/Parameter '{v['table']}.{v['column']}' does not exist — {who} "
           f"lists every column of {v['table']} and '{v['column']}' is not one of them. The query was NOT "
           f"executed. {v['table']} has: {shown}{more}{sugg}. Use a column the table has, or the table "
           f"that has the column, and retry.")
    return {"status": "error", "preflight": True, "preflight_column": True,
            "error": f"Column/Parameter '{v['table']}.{v['column']}' does not exist", "data": msg,
            "violations": violations}


def _reset_all() -> None:
    with _LOCK:
        _COLS.clear()
