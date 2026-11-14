"""The turn's result sets — every data statement's rows, addressable by the statement number the
Live Status theatre shows (spec: BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3, Compute with Lineage).

ONE registry per session, reset at every turn start, fed at the two post-success seams every
data-running engine passes through (the MCP adapter for Optimize, `tool_enforcement` for the
ReAct lanes). The compute component reads from it — it never re-queries — and every receipt
it produces is recorded here too, so the end-of-turn seam can seal the receipts into the
provenance chain and stamp them on the persisted turn.

Never raises into a turn: every entry point is fail-open. Off (the master switch absent) ⇒
nothing is recorded and nothing changes.
"""
from __future__ import annotations

import contextvars
import threading
import time
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}
MAX_ROWS = 5000            # rows kept per statement (a compute over more is still exact up to here)
MAX_STATEMENTS = 40
SAMPLE_ROWS = 50           # rows carried on the persisted receipt for the lineage dialog
_DERIVED_TOOL = "compute_sql"   # = agent.compute_sql.DERIVED_TOOL (pinned equal by its gate)


#: The Optimize plan phase whose tool call is running (PROP-006 O1). Task-local: parallel phases
#: each run in their own task under asyncio.gather, so one phase never sees another's number.
_PHASE: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar("result_sets_phase", default=None)


def set_phase(phase: Optional[int]):
    """Mark the running plan phase; returns a token for ``reset_phase``."""
    return _PHASE.set(phase if isinstance(phase, int) else None)


def current_phase() -> Optional[int]:
    """The plan phase whose call is running (None outside an Optimize plan)."""
    return _PHASE.get()


def reset_phase(token) -> None:
    try:
        _PHASE.reset(token)
    except Exception:  # noqa: BLE001 — a token from another context: leave it, the next set wins
        pass


def for_phase(session_id: Optional[str], phase: int) -> Optional[Dict[str, Any]]:
    """The statement a plan phase produced (its latest) — how a plan written BEFORE anything ran
    names a result, since parallel phases finish in any order and statement numbers are only
    known afterwards (O1)."""
    for st in reversed(statements(session_id)):
        if st.get("phase") == phase:
            return st
    return None


def _now_iso() -> str:
    """When a statement's rows arrived (PROP-006 A14): two sources queried at different moments
    can describe different snapshots, so every statement — and every combine receipt — says when."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "COMPUTE_LINEAGE", False))
    except Exception:  # noqa: BLE001
        return False


def begin_turn(session_id: Optional[str], turn_id: Any = None) -> None:
    """Reset the session's registry at the start of a turn (the execution seam)."""
    if not session_id:
        return
    with _LOCK:
        _SESSIONS[session_id] = {"turn_id": turn_id, "started": time.time(), "statements": [], "receipts": []}
        # a bounded process-global footprint: forget the oldest idle sessions
        if len(_SESSIONS) > 500:
            for sid in sorted(_SESSIONS, key=lambda k: _SESSIONS[k]["started"])[:100]:
                _SESSIONS.pop(sid, None)


def _rows_of(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    for key in ("results", "data", "rows"):
        rows = result.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def _declared_columns(result: Any) -> List[str]:
    """The columns a ROW-LESS result declares (the MCP server's ``metadata.columns``): an empty
    SELECT is still a result — a phase that found no rows has a result its combine reads as
    empty (live: a zero-row fetch left ``p1`` unresolvable and a correction round invented a
    total). A result with no rows and no declared columns stays unregistered."""
    if not isinstance(result, dict) or result.get("status") == "error":
        return []
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    cols = meta.get("columns") if isinstance(meta, dict) else None
    out: List[str] = []
    for c in cols if isinstance(cols, list) else []:
        name = c.get("name") if isinstance(c, dict) else c
        if isinstance(name, str) and name.strip() and name not in out:
            out.append(name.strip())
    return out


def _statement_text(tool_name: str, arguments: Dict[str, Any]) -> str:
    args = arguments or {}
    sql = args.get("sql") or args.get("query")
    if isinstance(sql, str) and sql.strip():
        return sql.strip()
    parts = [f"{k}={v}" for k, v in args.items() if isinstance(v, (str, int, float)) and k != "session_id"]
    return f"{tool_name}({', '.join(parts)})"


def record(session_id: Optional[str], tool_name: str, arguments: Dict[str, Any], result: Any,
           source: str = "") -> Optional[int]:
    """After a tool result carrying rows: register it as the next statement of the turn and
    return its number (1-based, execution order). Framework `TDA_*` tools and row-less results
    are not statements. None when nothing was recorded."""
    try:
        if not enabled() or not session_id or not tool_name or str(tool_name).startswith("TDA_"):
            return None
        rows = _rows_of(result)
        declared = _declared_columns(result) if not rows else []
        if not rows and not declared:
            return None
        with _LOCK:
            reg = _SESSIONS.get(session_id)
            if reg is None:
                reg = _SESSIONS[session_id] = {"turn_id": None, "started": time.time(), "statements": [], "receipts": []}
            if len(reg["statements"]) >= MAX_STATEMENTS:
                return None
            n = len(reg["statements"]) + 1
            cols: List[str] = list(declared)
            for r in rows[:200]:
                for k in r.keys():
                    if k not in cols:
                        cols.append(k)
            entry = {
                "n": n, "tool": tool_name, "statement": _statement_text(tool_name, arguments)[:2000],
                "columns": cols, "row_count": len(rows), "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
                "ts": _now_iso(),
            }
            if _PHASE.get() is not None:
                entry["phase"] = _PHASE.get()
            # PROP-006 — which data source produced these rows (`mcp:<server>`, `conn:<id>`,
            # `combine`): lineage and Live Status name it, isolation rules are judged on it.
            if source:
                entry["source_id"] = str(source)
            # PROP-006 — a COMBINE result (the platform's own `compute_sql`, never an MCP tool)
            # names the statements it read, so lineage reaches through it to the sources, and
            # carries its own completeness (the engine's output cap). Absent for every other tool.
            if tool_name == _DERIVED_TOOL:
                if isinstance(result.get("inputs"), list):
                    entry["inputs"] = [int(x) for x in result["inputs"] if str(x).isdigit()]
                    # the sources its rows came from — a combine of a combine is judged (isolation)
                    # and traced (lineage) on the ORIGINAL sources, never on "combine"
                    orig: List[str] = []
                    for s in reg["statements"]:
                        if s.get("n") in entry["inputs"]:
                            for o in (s.get("origin_sources") or ([s["source_id"]] if s.get("source_id") else [])):
                                if o not in orig:
                                    orig.append(o)
                    if orig:
                        entry["origin_sources"] = orig
                if isinstance(result.get("columns"), list):
                    entry["columns"] = [str(c) for c in result["columns"]]
                entry["truncated"] = bool(result.get("truncated")) or entry["truncated"]
            reg["statements"].append(entry)
            return n
    except Exception:  # noqa: BLE001
        return None


def register(session_id: Optional[str], kind: str, statement: str, columns: List[str], rows: List[Dict[str, Any]],
             source: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Register rows that did NOT come from a tool call — an attachment's extracted table
    (S9) — as the next statement of the turn, so the compute component can operate on them
    and a figure quoted from them traces to where they came from. ``source`` rides on the
    statement (the lineage dialog draws the cited region from it). Same switch, same bounds,
    same numbering as ``record``. None when nothing was recorded."""
    try:
        if not enabled() or not session_id or not kind:
            return None
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            return None
        with _LOCK:
            reg = _SESSIONS.get(session_id)
            if reg is None:
                reg = _SESSIONS[session_id] = {"turn_id": None, "started": time.time(), "statements": [], "receipts": []}
            if len(reg["statements"]) >= MAX_STATEMENTS:
                return None
            n = len(reg["statements"]) + 1
            cols = [str(c) for c in (columns or [])]
            for r in rows[:200]:
                for k in r.keys():
                    if k not in cols:
                        cols.append(k)
            entry = {"n": n, "tool": str(kind), "statement": str(statement or kind)[:2000], "columns": cols,
                     "row_count": len(rows), "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
                     "ts": _now_iso()}
            if isinstance(source, dict):
                entry["source"] = dict(source)
            reg["statements"].append(entry)
            return n
    except Exception:  # noqa: BLE001
        return None


def mark_quoted(session_id: Optional[str], statement_numbers) -> None:
    """A figure in the delivered answer was QUOTED from these statements' rows (the lineage
    scan says which). The persisted stamp then carries a bounded sample of their rows, so the
    figure's underline opens on the row it came from after a reload — the same reach a
    receipted figure has. Rows of statements nothing was quoted from stay out of the record."""
    try:
        with _LOCK:
            reg = _SESSIONS.get(session_id or "")
            if not reg:
                return
            wanted = {int(n) for n in (statement_numbers or []) if isinstance(n, (int, float, str)) and str(n).lstrip("-").isdigit()}
            for s in reg["statements"]:
                if s.get("n") in wanted:
                    s["quoted"] = True
    except Exception:  # noqa: BLE001
        pass


def statements(session_id: Optional[str]) -> List[Dict[str, Any]]:
    with _LOCK:
        reg = _SESSIONS.get(session_id or "")
        return list(reg["statements"]) if reg else []


def get(session_id: Optional[str], n: Any = None) -> Optional[Dict[str, Any]]:
    """Statement `n` (1-based); `None`/"latest" ⇒ the most recent; negative ⇒ from the end."""
    sts = statements(session_id)
    if not sts:
        return None
    if n in (None, "", "latest", "last"):
        return sts[-1]
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    if i < 0:
        i = len(sts) + 1 + i
    if 1 <= i <= len(sts):
        return sts[i - 1]
    return None


def add_receipt(session_id: Optional[str], receipt: Dict[str, Any], sample_rows: Optional[List[Dict[str, Any]]] = None) -> None:
    """Record a compute receipt for the turn (the seal + stamp read them at the end)."""
    try:
        if not session_id:
            return
        with _LOCK:
            reg = _SESSIONS.get(session_id)
            if reg is None:
                reg = _SESSIONS[session_id] = {"turn_id": None, "started": time.time(), "statements": [], "receipts": []}
            entry = dict(receipt)
            if sample_rows:
                entry["sample_rows"] = [r for r in sample_rows[:SAMPLE_ROWS] if isinstance(r, dict)]
            reg["receipts"].append(entry)
    except Exception:  # noqa: BLE001
        pass


def receipts(session_id: Optional[str]) -> List[Dict[str, Any]]:
    with _LOCK:
        reg = _SESSIONS.get(session_id or "")
        return [dict(r) for r in reg["receipts"]] if reg else []


def stamp_for_turn(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The persisted `compute` stamp for the turn: the receipts (with their sample rows) and a
    compact index of the statements they can be traced to. None when the turn computed nothing
    (an absent stamp is the honest baseline)."""
    rs = receipts(session_id)
    sts = statements(session_id)
    # S9: a statement registered FROM an attachment (it carries `source`) is worth the record
    # even when nothing computed over it — a figure quoted from the image traces to it on
    # reload, and the lineage dialog draws the region from it. Tool statements alone are not.
    # A statement a delivered figure was QUOTED from (`mark_quoted`) is worth the record for the
    # same reason: the figure's underline must open on its row after a reload.
    if not rs and not any(s.get("source") or s.get("quoted") for s in sts):
        return None
    return {
        "receipts": rs,
        "statements": [{"n": s["n"], "tool": s["tool"], "statement": s["statement"][:400],
                        "columns": s["columns"], "row_count": s["row_count"],
                        **({"source": s["source"]} if s.get("source") else {}),
                        # PROP-006 — where the rows came from, when, and (for a combine) from which
                        # statements: the lineage dialog's second hop and its source line. Each key
                        # only when known, so a turn without them persists exactly as before.
                        **{k: s[k] for k in ("source_id", "origin_sources", "inputs", "ts", "phase")
                           if s.get(k) not in (None, "", [])},
                        **({"sample_rows": [dict(r) for r in s["rows"][:SAMPLE_ROWS]]}
                           if (s.get("source") or s.get("quoted")) else {})} for s in sts],
    }
