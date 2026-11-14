"""Compute with Lineage — the closed set of deterministic operations over a turn's result sets
(spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3).

Pure. No I/O, no model. The agent decides WHAT to compute (an op and its parameters); this
module computes it, exactly, and hands back a **receipt** — the one thing a sandbox that runs
model-written code can never give: a figure that says where it came from.

    result = compute("sum", rows, column="miles")
    result["value"]     -> 149_133.0
    result["receipt"]   -> {"op": "sum", "column": "miles", "rows": 149, "row_count_used": 149,
                            "columns": [...], "value": 149133.0, ...}

Every op reads rows as a list of dicts (the MCP result shape). Numbers are parsed
tolerantly ("1,234.5", "$12", " 7 ") — a value that is not a number is NOT a number: it is
skipped by the numeric aggregates and counted by `count`, and the receipt says how many rows
each aggregate actually used (`row_count_used`), so a NULL-riddled column cannot pass as a
full population.

The op set is closed on purpose (`OPS`): sum · avg · min · max · count · rank · pivot ·
diff · join · pct_change · filter. A caller asking for anything else gets a recovery-shaped
`ComputeError` naming the set.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

OPS = ("sum", "avg", "min", "max", "count", "rank", "pivot", "diff", "join", "pct_change", "filter")
AGGREGATES = ("sum", "avg", "min", "max", "count")
FILTER_OPS = ("=", "!=", ">", "<", ">=", "<=", "contains", "startswith")

RECEIPT_FIELDS = ("op", "column", "by", "on", "n", "order", "where", "statements", "row_count",
                  "row_count_used", "columns", "value", "values", "result_rows")
#: The combine step's receipt (PROP-006): the executed statement, the statements it read, the
#: key normalizations the platform applied, and the result's size and completeness.
SQL_RECEIPT_FIELDS = ("op", "sql", "statements", "input_times", "normalizations", "row_count",
                      "result_rows", "columns", "truncated", "result_statement")

_NUM_RE = re.compile(r"^[\s$€£]*[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?\s*%?\s*$")


class ComputeError(ValueError):
    """A refusal the agent can act on — names what was asked and what is admissible."""


# ── value parsing ────────────────────────────────────────────────────────────
def as_number(v: Any) -> Optional[float]:
    """A tolerant numeric read: ints/floats pass; strings like "1,234.5", "$12", "7 %" parse;
    booleans, None, NaN/inf and prose do not."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip()
        if not s or not _NUM_RE.match(s):
            return None
        s = s.replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("%", "").strip()
        try:
            f = float(s)
            return f if math.isfinite(f) else None
        except ValueError:
            return None
    return None


def _clean(v: float) -> Any:
    """Integral floats render as ints (a count of 3 is 3, not 3.0); others keep 6 dp."""
    if v is None:
        return None
    if float(v).is_integer():
        return int(v)
    return round(float(v), 6)


def columns_of(rows: Iterable[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in seen:
                    seen.append(k)
    return seen


def numeric_columns(rows: List[Dict[str, Any]]) -> List[str]:
    """Columns where every non-null value parses as a number (and at least one does)."""
    out = []
    for c in columns_of(rows):
        vals = [r.get(c) for r in rows if isinstance(r, dict) and r.get(c) is not None]
        if vals and all(as_number(v) is not None for v in vals):
            out.append(c)
    return out


def resolve_column(rows: List[Dict[str, Any]], column: Optional[str], *, numeric: bool = True) -> str:
    """The column an op works on. Named → must exist (case-insensitive match tolerated).
    Absent → the ONE numeric column, when there is exactly one; otherwise a refusal naming
    the candidates — the component never guesses between two measures."""
    cols = columns_of(rows)
    if column:
        if column in cols:
            return column
        low = {c.lower(): c for c in cols}
        if column.lower() in low:
            return low[column.lower()]
        raise ComputeError(f"column '{column}' is not in the result set; columns: {', '.join(cols) or '(none)'}")
    cands = numeric_columns(rows) if numeric else cols
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ComputeError("no numeric column in the result set to compute over; name a column")
    raise ComputeError(f"more than one numeric column — name one: {', '.join(cands)}")


# ── the ops ───────────────────────────────────────────────────────────────────
def _values(rows: List[Dict[str, Any]], column: str) -> Tuple[List[float], int]:
    vals = [as_number(r.get(column)) for r in rows if isinstance(r, dict)]
    nums = [v for v in vals if v is not None]
    return nums, len(nums)


def _aggregate(op: str, rows: List[Dict[str, Any]], column: Optional[str]) -> Dict[str, Any]:
    if op == "count":
        col = column if column else None
        if col:
            col = resolve_column(rows, col, numeric=False)
            used = sum(1 for r in rows if isinstance(r, dict) and r.get(col) is not None)
        else:
            used = len(rows)
        return {"column": col, "value": used, "row_count_used": used}
    if not rows:
        # an empty result set is a legitimate answer: nothing to aggregate, said as None
        return {"column": column, "value": None, "row_count_used": 0}
    col = resolve_column(rows, column)
    nums, used = _values(rows, col)
    if not nums:
        return {"column": col, "value": None, "row_count_used": 0}
    if op == "sum":
        v = sum(nums)
    elif op == "avg":
        v = sum(nums) / len(nums)
    elif op == "min":
        v = min(nums)
    else:
        v = max(nums)
    return {"column": col, "value": _clean(v), "row_count_used": used}


def _rank(rows, column, n, order):
    if not rows:
        return {"column": column, "n": int(n) if n else 10, "order": (order or "desc").lower(), "result_rows": [],
                "row_count_used": 0, "values": []}
    col = resolve_column(rows, column)
    keyed = [(as_number(r.get(col)), r) for r in rows if isinstance(r, dict)]
    keyed = [(k, r) for k, r in keyed if k is not None]
    desc = (order or "desc").lower() != "asc"
    keyed.sort(key=lambda kr: kr[0], reverse=desc)
    n = int(n) if n else 10
    if n <= 0:
        raise ComputeError("n must be a positive integer")
    out = [dict(r, rank=i + 1) for i, (_, r) in enumerate(keyed[:n])]
    return {"column": col, "n": n, "order": "desc" if desc else "asc", "result_rows": out,
            "row_count_used": len(keyed), "values": [_clean(k) for k, _ in keyed[:n]]}


def _pivot(rows, column, by, agg):
    if not by:
        raise ComputeError("pivot needs `by` (the column to group on)")
    if not rows:
        return {"column": column, "by": by, "result_rows": [], "row_count_used": 0, "values": []}
    by_col = resolve_column(rows, by, numeric=False)
    agg = (agg or "sum").lower()
    if agg not in AGGREGATES:
        raise ComputeError(f"pivot agg must be one of {', '.join(AGGREGATES)}")
    col = None if agg == "count" and not column else resolve_column(rows, column)
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    order: List[Any] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        k = r.get(by_col)
        if k not in groups:
            groups[k] = []; order.append(k)
        groups[k].append(r)
    out = []
    for k in order:
        a = _aggregate(agg, groups[k], col)
        out.append({by_col: k, f"{agg}_{col}" if col else "count": a["value"], "rows": a["row_count_used"]})
    return {"column": col, "by": by_col, "result_rows": out, "row_count_used": sum(len(g) for g in groups.values()),
            "values": [o.get(f"{agg}_{col}" if col else "count") for o in out]}


def _filter(rows, where):
    """`where` = "column op value" — one predicate, closed operator set, no expressions."""
    if not where or not isinstance(where, str):
        raise ComputeError(f"filter needs `where` as 'column op value' with op in {', '.join(FILTER_OPS)}")
    m = re.match(r"^\s*([^\s<>=!]+)\s*(>=|<=|!=|=|>|<|contains|startswith)\s*(.+?)\s*$", where, re.I)
    if not m:
        raise ComputeError(f"cannot read `where` '{where}' — use 'column op value' with op in {', '.join(FILTER_OPS)}")
    col, op, raw = m.group(1), m.group(2).lower(), m.group(3).strip().strip("'\"")
    if not rows:
        return {"column": col, "where": f"{col} {op} {raw}", "result_rows": [], "row_count_used": 0}
    col = resolve_column(rows, col, numeric=False)
    want_num = as_number(raw)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        v = r.get(col)
        if op in ("contains", "startswith"):
            sv = "" if v is None else str(v)
            ok = (raw.lower() in sv.lower()) if op == "contains" else sv.lower().startswith(raw.lower())
        elif want_num is not None and as_number(v) is not None:
            a, b = as_number(v), want_num
            ok = {"=": a == b, "!=": a != b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
        else:
            sv = "" if v is None else str(v)
            ok = {"=": sv == raw, "!=": sv != raw, ">": sv > raw, "<": sv < raw, ">=": sv >= raw, "<=": sv <= raw}[op]
        if ok:
            out.append(r)
    return {"column": col, "where": f"{col} {op} {raw}", "result_rows": out, "row_count_used": len(out)}


def _pct_change(rows, column, order_by):
    """Change from the FIRST to the LAST row of `column`, rows ordered by `order_by`
    (or as given). Also the period-over-period series, so a trend answer has its lineage."""
    if not rows:
        return {"column": column, "value": None, "row_count_used": 0, "result_rows": []}
    col = resolve_column(rows, column)
    seq = [r for r in rows if isinstance(r, dict) and as_number(r.get(col)) is not None]
    if order_by:
        ob = resolve_column(rows, order_by, numeric=False)
        seq.sort(key=lambda r: (as_number(r.get(ob)) if as_number(r.get(ob)) is not None else float("inf"), str(r.get(ob))))
    if len(seq) < 2:
        return {"column": col, "value": None, "row_count_used": len(seq), "result_rows": []}
    series = []
    prev = None
    for r in seq:
        v = as_number(r.get(col))
        pct = None if prev in (None, 0) else _clean((v - prev) / abs(prev) * 100.0)
        series.append(dict(r, pct_change=pct)); prev = v
    first, last = as_number(seq[0].get(col)), as_number(seq[-1].get(col))
    total = None if first in (None, 0) else _clean((last - first) / abs(first) * 100.0)
    return {"column": col, "value": total, "row_count_used": len(seq), "result_rows": series,
            "values": [s.get("pct_change") for s in series if s.get("pct_change") is not None]}


def _join(rows_a, rows_b, on):
    if not on:
        raise ComputeError("join needs `on` (the shared column)")
    a_on = resolve_column(rows_a, on, numeric=False); b_on = resolve_column(rows_b, on, numeric=False)
    index: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows_b:
        if isinstance(r, dict):
            index.setdefault(r.get(b_on), []).append(r)
    out = []
    for r in rows_a:
        if not isinstance(r, dict):
            continue
        for m in index.get(r.get(a_on), []):
            merged = dict(r)
            for k, v in m.items():
                if k == b_on:
                    continue
                merged[k if k not in merged else f"{k}_b"] = v
            out.append(merged)
    return {"on": on, "result_rows": out, "row_count_used": len(out)}


def _diff(rows_a, rows_b, on, column):
    """Rows of A keyed by `on` compared with B: the per-key difference of `column`
    (A − B), plus keys present on one side only."""
    if not on:
        raise ComputeError("diff needs `on` (the key column present in both result sets)")
    a_on = resolve_column(rows_a, on, numeric=False); b_on = resolve_column(rows_b, on, numeric=False)
    ca = resolve_column(rows_a, column); cb = resolve_column(rows_b, column)
    bmap = {r.get(b_on): as_number(r.get(cb)) for r in rows_b if isinstance(r, dict)}
    out = []; seen = set()
    for r in rows_a:
        if not isinstance(r, dict):
            continue
        k = r.get(a_on); seen.add(k)
        va, vb = as_number(r.get(ca)), bmap.get(k)
        out.append({on: k, f"{ca}_a": _clean(va) if va is not None else None,
                    f"{cb}_b": _clean(vb) if vb is not None else None,
                    "diff": _clean(va - vb) if (va is not None and vb is not None) else None,
                    "only_in": None if k in bmap else "a"})
    for k, vb in bmap.items():
        if k not in seen:
            out.append({on: k, f"{ca}_a": None, f"{cb}_b": _clean(vb) if vb is not None else None, "diff": None, "only_in": "b"})
    return {"column": ca, "on": on, "result_rows": out, "row_count_used": len(out),
            "values": [o["diff"] for o in out if o["diff"] is not None]}


# ── the entry point ──────────────────────────────────────────────────────────
def compute(op: str, rows: List[Dict[str, Any]], *, column: Optional[str] = None, by: Optional[str] = None,
            n: Optional[int] = None, order: Optional[str] = None, where: Optional[str] = None,
            on: Optional[str] = None, agg: Optional[str] = None, order_by: Optional[str] = None,
            rows_b: Optional[List[Dict[str, Any]]] = None, statements: Optional[List[int]] = None) -> Dict[str, Any]:
    """Run ONE closed-set operation over `rows` (and `rows_b` for join/diff). Returns
    ``{"op", "value" | "result_rows", "receipt"}``; raises `ComputeError` (recovery-shaped)."""
    op = (op or "").strip().lower()
    if op not in OPS:
        raise ComputeError(f"unknown op '{op}' — the admissible operations are: {', '.join(OPS)}")
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if op in AGGREGATES:
        res = _aggregate(op, rows, column)
    elif op == "rank":
        res = _rank(rows, column, n, order)
    elif op == "pivot":
        res = _pivot(rows, column, by, agg)
    elif op == "filter":
        res = _filter(rows, where)
    elif op == "pct_change":
        res = _pct_change(rows, column, order_by)
    elif op == "join":
        if rows_b is None:
            raise ComputeError("join needs a second result set (`statement_b`)")
        res = _join(rows, [r for r in rows_b if isinstance(r, dict)], on)
    else:  # diff
        if rows_b is None:
            raise ComputeError("diff needs a second result set (`statement_b`)")
        res = _diff(rows, [r for r in rows_b if isinstance(r, dict)], on, column)
    receipt = {
        "op": op, "column": res.get("column"), "by": res.get("by"), "on": res.get("on"),
        "n": res.get("n"), "order": res.get("order"), "where": res.get("where"),
        "statements": list(statements or []), "row_count": len(rows) + (len(rows_b) if rows_b else 0),
        "row_count_used": res.get("row_count_used"), "columns": columns_of(rows),
        "value": res.get("value"), "values": res.get("values"),
        "result_rows": len(res["result_rows"]) if isinstance(res.get("result_rows"), list) else None,
    }
    receipt["receipt_hash"] = receipt_hash(receipt)
    return {"op": op, "value": res.get("value"), "result_rows": res.get("result_rows"), "receipt": receipt}


def receipt_content(receipt: Dict[str, Any]) -> str:
    """The canonical, signable content of ONE compute receipt — used by BOTH the EPC sealing
    site and offline verification (`provenance.verify_content`), so what is sealed and what is
    checked can never drift. Deterministic: sorted keys, compact, closed field set.

    A combine receipt (op "sql") has its OWN closed set — the statement that ran IS the
    derivation, so it is sealed. Every other receipt keeps ``RECEIPT_FIELDS`` byte for byte, so
    no receipt already in a provenance chain changes its hash (test_compute_sql_baseline.py)."""
    fields = SQL_RECEIPT_FIELDS if receipt.get("op") == "sql" else RECEIPT_FIELDS
    return json.dumps({k: receipt.get(k) for k in fields},
                      sort_keys=True, separators=(",", ":"), default=str)


def receipt_hash(receipt: Dict[str, Any]) -> str:
    return hashlib.sha256(receipt_content(receipt).encode("utf-8")).hexdigest()[:16]


def receipt_figures(receipt: Dict[str, Any]) -> List[float]:
    """Every figure a receipt vouches for: the scalar value and the per-row values."""
    out: List[float] = []
    v = receipt.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out.append(float(v))
    for x in receipt.get("values") or []:
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            out.append(float(x))
    return out


def describe(receipt: Dict[str, Any]) -> str:
    """One line a reader can act on: `SUM(miles) · 149 rows · statement 2`."""
    if receipt.get("op") == "sql":
        ins = receipt.get("statements") or []
        src = " + ".join(f"statement {n}" for n in ins) or "the turn's results"
        rows = receipt.get("result_rows")
        out = f"COMBINE {src} → {rows} row{'s' if rows != 1 else ''}"
        if receipt.get("result_statement"):
            out += f" · statement {receipt['result_statement']}"
        return out
    op = (receipt.get("op") or "").upper()
    col = receipt.get("column")
    head = f"{op}({col})" if col else op
    if receipt.get("by"):
        head += f" by {receipt['by']}"
    if receipt.get("where"):
        head += f" where {receipt['where']}"
    if receipt.get("on"):
        head += f" on {receipt['on']}"
    used = receipt.get("row_count_used")
    total = receipt.get("row_count")
    rows = f"{used:,} rows" if used is not None else ""
    if used is not None and total is not None and used != total:
        rows = f"{used:,} of {total:,} rows"
    sts = receipt.get("statements") or []
    st = ("statement " + ", ".join(str(s) for s in sts)) if sts else ""
    return " · ".join(p for p in (head, rows, st) if p)
