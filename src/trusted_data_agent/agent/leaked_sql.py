"""A leaked SELECT becomes a query — the pure half.

A weak tool-calling model (prod: Celeris on @PXPROD, session 86ac6b28 turns 9–13) sometimes
answers a data question with the SQL it MEANT to run, as a fenced code block, and calls no
tool. The user receives a statement instead of rows. Whether the answer IS a single read
statement, which bound tool can run it, and how its rows read back as a table are all facts
the platform can decide — so it runs the statement through the SAME guarded tool the model
would have called (Agent Authority, preflight and every rewrite apply) and renders the rows.
No model call: the recovery costs zero tokens and cannot itself hallucinate.

Only the SOLE-statement shape is recovered: one fenced ``sql`` block (or an unlabelled block
that is plainly a SELECT) with at most a short lead-in/lead-out around it, exactly one
read-only statement, and no data tool run this turn. A SELECT the model SHOWED beside the
rows it already fetched, or a block inside a longer explanation, is left alone.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_FENCE_RE = re.compile(r"```[ \t]*(\w+)?[ \t]*\r?\n(.*?)```", re.DOTALL)
_LEAD_RE = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/\s*)*", re.DOTALL)
_READ_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

#: text allowed OUTSIDE the block for the block to still count as "the answer"
MAX_SURROUNDING_CHARS = 240
#: rows rendered back (the tool's own row limit still bounds what was fetched)
RENDER_ROW_CAP = 25
CELL_CAP = 300


def _strip_leading_comments(sql: str) -> str:
    m = _LEAD_RE.match(sql or "")
    return (sql or "")[m.end():] if m else (sql or "")


def is_single_read_statement(sql: str) -> bool:
    """Exactly one SELECT/WITH statement, nothing mutating. A trailing ``;`` is allowed;
    a second statement is not (a multi-statement text is not "the query")."""
    body = _strip_leading_comments(sql or "").strip()
    if not body:
        return False
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        return False
    if not _READ_START_RE.match(body):
        return False
    try:
        from trusted_data_agent.agent.preflight.catalog import sql_is_write
        if sql_is_write(body):
            return False
    except Exception:  # noqa: BLE001 — classifier unavailable: the keyword test above stands
        pass
    return True


def sole_sql_block(response: str) -> Optional[str]:
    """The statement when the response IS one SQL block (and at most a short lead-in or
    lead-out around it); None otherwise. Deterministic."""
    text = response or ""
    blocks = list(_FENCE_RE.finditer(text))
    if len(blocks) != 1:
        return None
    m = blocks[0]
    lang = (m.group(1) or "").lower()
    sql = (m.group(2) or "").strip()
    if not sql:
        return None
    if lang not in ("sql", "") and lang != "teradata":
        return None
    if lang == "" and not _READ_START_RE.match(_strip_leading_comments(sql)):
        return None
    outside = (text[:m.start()] + text[m.end():]).strip()
    if len(outside) > MAX_SURROUNDING_CHARS:
        return None
    if not is_single_read_statement(sql):
        return None
    return sql.rstrip().rstrip(";").rstrip()


def _tool_arg_names(tool: Any) -> List[str]:
    try:
        args = getattr(tool, "args", None)
        if isinstance(args, dict):
            return list(args.keys())
    except Exception:  # noqa: BLE001
        pass
    try:
        schema = getattr(tool, "args_schema", None)
        fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None) or {}
        return list(fields.keys())
    except Exception:  # noqa: BLE001
        return []


def find_sql_tool(tools: List[Any]) -> Optional[Any]:
    """The bound tool that takes a ``sql`` argument: ``base_readQuery`` when bound (the
    platform's read tool), else the first non-component tool with a ``sql`` arg. None when
    the agent has no such tool — then there is nothing to run and nothing is recovered."""
    from trusted_data_agent.core.tool_identity import base_name
    # the platform's read tool, under any server alias (crm__base_readQuery is still it)
    for t in tools or []:
        if base_name(getattr(t, "name", "") or "") == "base_readQuery":
            return t
    for t in tools or []:
        name = getattr(t, "name", "") or ""
        if name.startswith("TDA_"):
            continue
        if "sql" in _tool_arg_names(t):
            return t
    return None


def ran_data_tool(tools_used: List[str]) -> bool:
    """A non-component tool already ran this turn (then a SELECT in the text is the model
    SHOWING its query, not a query it failed to run)."""
    return any(n and not str(n).startswith("TDA_") for n in (tools_used or []))


def rows_from_output_text(text: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[int]]:
    """``(rows, declared_row_count)`` read from a data tool's JSON text. Rows are a list of
    dicts under ``results``/``data``/``rows`` (the read tool's shape) — None when the text
    is not readable as that shape (never a guess)."""
    if not text:
        return None, None
    try:
        start = text.find("{")
        obj = json.loads(text[start:]) if start >= 0 else None
    except Exception:  # noqa: BLE001
        obj = None
    if not isinstance(obj, dict):
        return None, None
    rows = None
    for key in ("results", "data", "rows"):
        v = obj.get(key)
        if isinstance(v, list) and all(isinstance(r, dict) for r in v):
            rows = v
            break
    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    rc = meta.get("row_count") if isinstance(meta, dict) else None
    try:
        rc = int(rc) if rc is not None else None
    except Exception:  # noqa: BLE001
        rc = None
    return rows, rc


def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    return s if len(s) <= CELL_CAP else s[:CELL_CAP - 1] + "…"


def render_rows_markdown(rows: List[Dict[str, Any]], row_count: Optional[int] = None) -> str:
    """A markdown table of the rows (bounded) under one plain sentence saying how many came
    back — the shape the answer would have had if the model had read them."""
    n = len(rows or [])
    total = row_count if isinstance(row_count, int) and row_count >= n else n
    if n == 0:
        return "The query ran and returned no rows."
    shown = rows[:RENDER_ROW_CAP]
    cols = list(shown[0].keys())
    for r in shown[1:]:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    head = "| " + " | ".join(_cell(c) for c in cols) + " |"
    sep = "|" + "|".join(" --- " for _ in cols) + "|"
    body = ["| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in shown]
    lead = (f"The query returned {total:,} row{'s' if total != 1 else ''}"
            + (f"; the first {len(shown)} are shown." if total > len(shown) else "."))
    return "\n".join([lead, "", head, sep, *body])


def recovered_answer(sql: str, rows: Optional[List[Dict[str, Any]]], row_count: Optional[int],
                     error: Optional[str]) -> str:
    """The delivered answer after the recovery. Success: the rows, then the statement that
    produced them (so the reader sees what ran). Failure: the statement and the error —
    never a fabricated row."""
    fence = f"```sql\n{sql}\n```"
    if error:
        return (f"Running this query failed: {error.strip()[:400]}\n\n{fence}")
    if rows is None:
        return (f"The query ran, but its result could not be read back as rows.\n\n{fence}")
    return f"{render_rows_markdown(rows, row_count)}\n\n_Query run:_\n{fence}"
