"""Did the agent read the library section before it ran that section's SQL — and did the SQL run?

The measurement SKILL_ARCHITECTURE §4a asks for ("nothing verifies a read happened"): a
progressive skill whose sections hold executed SQL records, per statement, the database
functions it calls (``index[].requires``). A statement the AGENT runs that calls one of those
functions as a table operator is a use of what that section teaches. For each such statement
the turn records two facts:

* **read before** — a section teaching that operator was opened (``TDA_SkillLibrary`` read)
  earlier in the same turn;
* **ran** — the statement's own tool call did not report a failure.

Four cells follow (read·ran, read·failed, unread·ran, unread·failed). Unread·failed is the case
the library exists for; the read·ran / unread·ran split is what decides whether forcing a read
would pay (if unread statements run as well as read ones, a forced read only burns tokens).

Only TABLE OPERATORS count — a function called where a table is expected
(``FROM NPATH(…)``, ``FROM TABLE(MonitorSession(…))``,
``EXECUTE FUNCTION [INTO [VOLATILE] ART(…)] [COLUMNS(…)] TD_DFFT(…)``). Scalar functions a
section happens to use (``TRUNC``, ``LENGTH``, ``TO_CHAR``) appear in almost any statement;
counting them would attribute ordinary SQL to whichever section used the same builtin. The
cost of that precision: sections that teach only scalar functions (casting, validation) are
not measured here, and the record says nothing about them.

Pure, deterministic, 0 tokens, never raises.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

SKILL_LIBRARY_TOOL = "TDA_SkillLibrary"

CELLS = ("read_ran", "read_failed", "unread_ran", "unread_failed")

# One balanced parenthesis group, up to three levels deep (enough for ART(...) / COLUMNS(...)).
_PAREN = r"\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
_OPERATOR = re.compile(
    r"(?:\b(?:FROM|JOIN)(?:\s+TABLE\s*\()?"
    r"|\bEXECUTE\s+FUNCTION(?:\s+INTO\s+(?:VOLATILE\s+)?ART\s*" + _PAREN +
    r"|\s+COLUMNS\s*" + _PAREN + r")*)"
    r"\s*(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*\(",
    re.IGNORECASE)


def _mask(sql: str) -> str:
    """Code only — string literals and comments blanked, so a quoted ``FROM npath(`` is not code."""
    try:
        from trusted_data_agent.agent.sql_dialect import _sql_code_mask
        return _sql_code_mask(sql)
    except Exception:  # noqa: BLE001
        return sql


#: SQL functions whose ARGUMENTS contain the keyword FROM (``EXTRACT(MONTH FROM ts)``,
#: ``TRIM(LEADING '0' FROM x)``, ``SUBSTRING(x FROM 2)``). Fixed grammar, not a data list: a
#: ``FROM`` inside one of them is an argument keyword, never a table position.
_KEYWORD_ARG_FUNCTIONS = frozenset({"EXTRACT", "TRIM", "SUBSTRING", "SUBSTR", "POSITION", "OVERLAY"})
_IDENT_BEFORE = re.compile(r"([A-Za-z_]\w*)\s*$")


def _inside_keyword_arg_function(code: str, pos: int) -> bool:
    depth = 0
    for j in range(pos - 1, -1, -1):
        ch = code[j]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                m = _IDENT_BEFORE.search(code[:j])
                return bool(m) and m.group(1).upper() in _KEYWORD_ARG_FUNCTIONS
            depth -= 1
    return False


def table_operators(sql: str) -> Set[str]:
    """Upper-cased names of functions this statement calls in a table position."""
    if not isinstance(sql, str) or not sql.strip():
        return set()
    code = _mask(sql)
    return {m.group(1).upper() for m in _OPERATOR.finditer(code)
            if not _inside_keyword_arg_function(code, m.start())}


def operator_sections(index: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    """``{FUNCTION: [anchors whose executed statements call it]}`` from a progressive index."""
    out: Dict[str, List[str]] = {}
    for entry in index or []:
        if not isinstance(entry, dict):
            continue
        anchor = entry.get("anchor")
        if not isinstance(anchor, str) or not anchor:
            continue
        for fns in (entry.get("requires") or {}).values():
            for fn in fns or []:
                lst = out.setdefault(str(fn).upper(), [])
                if anchor not in lst:
                    lst.append(anchor)
    return out


def _statement(call: Dict[str, Any]) -> Optional[str]:
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    sql = args.get("sql") or args.get("query")
    return sql if isinstance(sql, str) and sql.strip() else None


def _read_anchor(call: Dict[str, Any]) -> Optional[str]:
    """The anchor a successful ``TDA_SkillLibrary`` read delivered, else None.
    ``list_sections`` is not a read, and a refused read delivered no content."""
    if call.get("tool_name") != SKILL_LIBRARY_TOOL:
        return None
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    if str(args.get("action") or "read").lower() != "read":
        return None
    res = call.get("result")
    if isinstance(res, dict) and (res.get("error") or res.get("status") == "error"
                                  or res.get("success") is False):
        return None
    anchor = args.get("anchor")
    return anchor.strip() if isinstance(anchor, str) and anchor.strip() else None


def _failed(call: Dict[str, Any]) -> bool:
    try:
        from trusted_data_agent.agent.honesty.primitives import call_failed
        return call_failed(call)
    except Exception:  # noqa: BLE001
        res = call.get("result")
        return isinstance(res, dict) and (res.get("status") == "error" or res.get("success") is False)


def record(tool_calls: Iterable[Dict[str, Any]],
           indexes: Dict[str, Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """The turn's read → ran record, or None when there is nothing to report.

    ``tool_calls`` — the turn's canonical ``{tool_name, arguments, result}`` calls, IN ORDER
    (the order is what makes "read before" decidable). ``indexes`` — ``{skill_id: progressive
    index}`` for the agent's active progressive skills.
    """
    by_op: Dict[str, List[str]] = {}
    for idx in (indexes or {}).values():
        for fn, anchors in operator_sections(idx).items():
            lst = by_op.setdefault(fn, [])
            lst.extend(a for a in anchors if a not in lst)
    if not by_op:
        return None

    read: List[str] = []                     # anchors opened so far, in order
    statements: List[Dict[str, Any]] = []
    for i, call in enumerate(tool_calls or []):
        if not isinstance(call, dict):
            continue
        anchor = _read_anchor(call)
        if anchor:
            if anchor not in read:
                read.append(anchor)
            continue
        sql = _statement(call)
        if sql is None:
            continue
        ops = sorted(op for op in table_operators(sql) if op in by_op)
        if not ops:
            continue
        sections = sorted({a for op in ops for a in by_op[op]})
        statements.append({
            "position": i,
            "tool": call.get("tool_name"),
            "operators": ops,
            "sections": sections,
            "read_before": [a for a in read if a in sections],
            "ran": not _failed(call),
        })

    used = {a for s in statements for a in s["sections"]}
    read_unused = [a for a in read if a not in used]
    if not statements and not read:
        return None

    cells = {c: 0 for c in CELLS}
    per_section: Dict[str, Dict[str, int]] = {}
    for s in statements:
        cell = ("read" if s["read_before"] else "unread") + ("_ran" if s["ran"] else "_failed")
        cells[cell] += 1
        for a in s["sections"]:
            per_section.setdefault(a, {c: 0 for c in CELLS})[cell] += 1
    return {
        "skills": sorted((indexes or {}).keys()),
        "statements": statements,
        "cells": cells,
        "sections": per_section,
        "reads": read,
        "read_unused": read_unused,
    }


def read_first_rate(rec: Optional[Dict[str, Any]]) -> Optional[float]:
    """Of the statements that used a library operator, the share preceded by a read. None when
    no statement used one — a turn that never touched the library has no rate, not a perfect one."""
    cells = (rec or {}).get("cells") or {}
    total = sum(int(cells.get(c) or 0) for c in CELLS)
    if not total:
        return None
    return (int(cells.get("read_ran") or 0) + int(cells.get("read_failed") or 0)) / total


def describe(rec: Optional[Dict[str, Any]]) -> str:
    """One sentence naming the four cells — the same words wherever the record is shown."""
    c = (rec or {}).get("cells") or {}
    total = sum(int(c.get(k) or 0) for k in CELLS)
    if not total:
        n = len((rec or {}).get("reads") or [])
        return (f"{n} section(s) read; no statement used a library operator"
                if n else "no statement used a library operator")
    read = int(c.get("read_ran") or 0) + int(c.get("read_failed") or 0)
    parts = [f"{read} of {total} statement(s) using a library operator came after a read"]
    parts.append(f"after a read: {c.get('read_ran', 0)} ran, {c.get('read_failed', 0)} failed")
    parts.append(f"without one: {c.get('unread_ran', 0)} ran, {c.get('unread_failed', 0)} failed")
    return "; ".join(parts)
