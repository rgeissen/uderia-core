"""
Findings — the verified observations a session's tool calls returned, kept as a bounded block
the model RE-READS rather than recalls.

A finding is a fact the platform observed, never one the model stated: which tool ran, on what
(the statement or the object it addressed), how many rows came back, which columns, and — for
a single-row result such as a count or an aggregate — the values themselves. Extracted
DETERMINISTICALLY from the turn record (zero LLM tokens) at the moment the turn is persisted
(``session_manager.update_last_turn_data`` stamps ``turn["findings"]`` for every engine), then
delivered on the following turns by the ``findings`` context-window module, which keeps the
newest turn's findings whole under condensation (the pinned floor) and hands the text it showed
to the grounding gate — a figure the platform itself put in front of the model is a figure the
turn's inputs vouch for, not an "ungrounded number".

Two sources, one shape:
  * Optimize — ``execution_trace`` entries ``{action: {tool_name, arguments}, result: {status,
    results, metadata}}`` (``TDA_*`` framework calls and failed calls carry no finding);
  * ReAct — the lane's ``data_results`` (``{results, metadata: {tool_name, row_count,
    arguments, executed_sql}}``), the rows ``_capture_data_result`` kept.

Numbers are written raw (``4812``, never ``4,812``) so the gate's number normaliser matches
them one for one.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

#: bounds — a finding is a line, a turn is a handful of lines, never a dump
MAX_FINDINGS_PER_TURN = 12
MAX_COLUMNS = 12
MAX_VALUE_COLUMNS = 8
MAX_VALUE_CHARS = 60
MAX_KEY_CHARS = 140
#: the header the module emits — ONE string, read by the module, the executor and the tests
BLOCK_HEADER = "[Verified results from earlier turns in this session]"
BLOCK_RULE = ("These values were returned by tools earlier in this conversation. Re-read them "
              "here rather than recalling them; if you need a figure that is not listed, run the "
              "tool again rather than estimating.")


def _framework(tool_name: Any) -> bool:
    return not tool_name or str(tool_name).startswith("TDA_")


def _clip(v: Any, n: int) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str) if not isinstance(v, (int, float)) else str(v)
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _key_of(arguments: Any, executed_sql: Optional[str] = None) -> str:
    """What the call addressed: the statement that ran, else the object the arguments name."""
    if isinstance(executed_sql, str) and executed_sql.strip():
        return _clip(executed_sql.strip(), MAX_KEY_CHARS)
    if not isinstance(arguments, dict):
        return ""
    sql = arguments.get("sql")
    if isinstance(sql, str) and sql.strip():
        return _clip(sql.strip(), MAX_KEY_CHARS)
    parts = []
    for k in ("database_name", "db_name", "database", "object_name", "table_name", "table",
              "column_name", "column", "query", "name"):
        v = arguments.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            parts.append(f"{k}={_clip(v, 40)}")
    return ", ".join(parts)[:MAX_KEY_CHARS]


def _rows_of(result: Any) -> Optional[List[dict]]:
    if not isinstance(result, dict):
        return None
    for key in ("results", "rows", "data"):
        v = result.get(key)
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    return None


def _finding(tool_name: str, key: str, rows: List[dict], row_count: Optional[int] = None) -> Dict[str, Any]:
    cols: List[str] = []
    for r in rows[:50]:
        for c in r.keys():
            if c not in cols:
                cols.append(str(c))
    n = int(row_count) if isinstance(row_count, int) and row_count >= len(rows) else len(rows)
    f: Dict[str, Any] = {"tool": str(tool_name), "key": key, "rows": n, "columns": cols[:MAX_COLUMNS]}
    if n == 1 and rows and len(rows[0]) <= MAX_VALUE_COLUMNS:
        f["values"] = {str(k): _clip(v, MAX_VALUE_CHARS) for k, v in rows[0].items()}
    elif rows and len(rows[0]) <= MAX_VALUE_COLUMNS:
        f["sample"] = {str(k): _clip(v, MAX_VALUE_CHARS) for k, v in rows[0].items()}
    return f


def from_execution_trace(trace: Iterable[Any]) -> List[Dict[str, Any]]:
    """Findings of an Optimize turn's trace. A framework call, a failed call, a call with no row
    list, carries none. Deterministic; bounded; never raises."""
    out: List[Dict[str, Any]] = []
    try:
        for entry in trace or []:
            if len(out) >= MAX_FINDINGS_PER_TURN:
                break
            if not isinstance(entry, dict):
                continue
            action = entry.get("action") or {}
            result = entry.get("result")
            if not isinstance(action, dict):
                continue
            tool = action.get("tool_name")
            if _framework(tool) or not isinstance(result, dict):
                continue
            if str(result.get("status") or "").lower() in ("error", "failed", "failure"):
                continue
            rows = _rows_of(result)
            if rows is None:
                continue
            md = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            key = _key_of(action.get("arguments"), md.get("executed_sql"))
            out.append(_finding(str(tool), key, rows, md.get("row_count")))
    except Exception:  # noqa: BLE001 — an extractor must never break a save
        return out
    return out


def from_data_results(data_results: Iterable[Any]) -> List[Dict[str, Any]]:
    """Findings of a ReAct turn's captured data results (the formatter's ``collected_data`` shape)."""
    out: List[Dict[str, Any]] = []
    try:
        for d in data_results or []:
            if len(out) >= MAX_FINDINGS_PER_TURN:
                break
            if not isinstance(d, dict):
                continue
            md = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            tool = md.get("tool_name")
            if _framework(tool):
                continue
            rows = _rows_of(d)
            if rows is None:
                continue
            key = _key_of(md.get("arguments"), md.get("executed_sql"))
            out.append(_finding(str(tool), key, rows, md.get("row_count")))
    except Exception:  # noqa: BLE001
        return out
    return out


def extract_turn(turn: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The findings of one persisted turn record, from whichever source it carries."""
    if not isinstance(turn, dict):
        return []
    stamped = turn.get("findings")
    if isinstance(stamped, list):
        return [f for f in stamped if isinstance(f, dict)]
    found = from_execution_trace(turn.get("execution_trace") or [])
    if not found and isinstance(turn.get("data_results"), list):
        found = from_data_results(turn.get("data_results"))
    return found


# ── rendering ─────────────────────────────────────────────────────────────────────

def format_finding(f: Dict[str, Any]) -> str:
    tool = f.get("tool", "?")
    key = f.get("key") or ""
    n = f.get("rows", 0)
    head = f"- {tool}" + (f" ({key})" if key else "") + f": {n} row" + ("" if n == 1 else "s")
    vals = f.get("values")
    if isinstance(vals, dict) and vals:
        return head + " · " + ", ".join(f"{k} = {v}" for k, v in vals.items())
    cols = f.get("columns") or []
    tail = f" · columns: {', '.join(cols)}" if cols else ""
    sample = f.get("sample")
    if isinstance(sample, dict) and sample:
        tail += " · first row: " + ", ".join(f"{k} = {v}" for k, v in sample.items())
    return head + tail


def format_turn(turn_number: Any, findings: List[Dict[str, Any]]) -> str:
    lines = [f"Turn {turn_number}:"]
    lines.extend(format_finding(f) for f in findings)
    return "\n".join(lines)


def format_block(turns: List[tuple], char_budget: int, pinned_turns: int = 1) -> str:
    """The block for the context window: ``turns`` is ``[(turn_number, findings), …]`` in
    session order. Newest turns are kept first; the newest ``pinned_turns`` are kept whole
    even when the budget is exhausted (the pin — a verified figure from the last turn must
    survive condensation, or the model recalls it from memory instead). Empty when there is
    nothing to say."""
    body = [(t, fs) for t, fs in turns if fs]
    if not body:
        return ""
    header = BLOCK_HEADER + "\n" + BLOCK_RULE
    rendered: List[str] = []
    used = len(header)
    for idx, (t, fs) in enumerate(reversed(body)):
        text = format_turn(t, fs)
        if idx >= pinned_turns and used + len(text) + 2 > max(0, char_budget):
            break
        rendered.append(text)
        used += len(text) + 2
    if not rendered:
        return ""
    return header + "\n\n" + "\n\n".join(rendered)      # newest turn first


def session_turns(workflow_history: Any, exclude_turn: Any = None) -> List[tuple]:
    """``[(turn_number, findings), …]`` for a session's valid turns, oldest first."""
    out: List[tuple] = []
    for i, t in enumerate(workflow_history if isinstance(workflow_history, list) else []):
        if not isinstance(t, dict) or t.get("isValid") is False:
            continue
        num = t.get("turn") or t.get("turn_number") or (i + 1)
        if exclude_turn is not None and num == exclude_turn:
            continue
        fs = extract_turn(t)
        if fs:
            out.append((num, fs))
    return out
