"""Preflight text-date check — pure + deterministic, 0 tokens.

The class it closes (2026-09-04): a DATE the product deploys as characters (`INCDATE
CHAR(11)`, holding `31-OCT-2023`) was compared as a string (`INCDATE >= '2023-01-01'`),
bucketed with `SUBSTR(INCDATE, 1, 7)` (day-and-month, not a month) and ordered bare
(`ORDER BY INCDATE DESC` — every 31st first). Nothing errored; the "trend" was no such
thing. The ontology already knows WHICH columns are text-stored dates (the enrichment
stamps them as ``text_date_columns``); this guard checks a raw-SQL call against that
list before the database sees it.

What the ontology cannot know is the SHAPE the text actually holds — the product's own
declaration for INCDATE says MM/DD/YYYY and the data holds DD-MON-YYYY — so the shape is
LEARNED from results as they pass the chokepoint (any row set carrying the column), and
held process-wide: a data property, not a session one.

The verdict ladder, per finding:
  * a bare lexical use (range comparison, ORDER BY, MIN/MAX, SUBSTR/LEFT bucket, an
    implicit conversion against a DATE expression or EXTRACT) is correct ONLY when the
    stored text is ISO (year, month, day). Shape known and ISO → pass; known and not
    ISO → a PROVEN violation naming the FORMAT; unknown → UNVERIFIED, and the refusal
    names the one probe that settles it (the guard learns from that probe's rows);
  * an equality against a date-shaped literal is correct when literal and column share
    a shape (equality needs no order); unknown → unverified;
  * ``CAST(col AS DATE FORMAT '…')`` is the RIGHT form — checked only against a KNOWN
    shape (a mismatched FORMAT is a proven violation naming the right one); with the
    shape unknown it passes, because the database's Error 2666 already teaches it and
    the model followed the instruction it was given;
  * ``CAST(col AS DATE)`` with no FORMAT, GROUP BY on the bare column, ``IS NULL``,
    ``<> ''`` are never findings.

No recorded columns, an unparseable statement, an unqualified column two recorded
tables both carry: no verdict (fail-open by construction).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.agent.preflight import dialect as _dialect

from trusted_data_agent.components import dateforms as _df

_COLS: Dict[str, Dict[str, Any]] = {}      # session_id -> {"tables": {leaf: {col_lower: info}}, "ts"}
_SHAPES: Dict[Tuple[str, str], Dict[str, Any]] = {}   # (leaf, col_lower) -> {"format", "sample", "ts"}
_LOCK = threading.Lock()
_TTL_SECONDS = 1800
_SHAPE_TTL_SECONDS = 6 * 3600
_MAX_SHAPES = 5000

_LEXICAL_KINDS = ("range_literal", "date_expr", "substr", "order", "minmax", "implicit_cast")
_ALL_KINDS = _LEXICAL_KINDS + ("equality_literal", "cast_format", "cast_plain")


def _leaf(name: str) -> str:
    return str(name or "").strip().strip('"').rpartition(".")[2].lower()


# ── recording ──────────────────────────────────────────────────────────────────
def record_text_dates(session_id: str, entries: Any) -> int:
    """Record the text-stored date columns the enrichment names for a session (union —
    an out-of-domain turn's empty enrichment must not erase an earlier turn's columns).
    ``entries``: [{"table": qualified_or_logical, "columns": [{"name", "type"} | "name"]}].
    Returns the number of columns known afterwards."""
    if not session_id or not isinstance(entries, list):
        return 0
    tables: Dict[str, Dict[str, Dict[str, str]]] = {}
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        table = str(ent.get("table") or "").strip()
        leaf = _leaf(table)
        if not leaf:
            continue
        for c in ent.get("columns") or []:
            name, ctype, shape, sample, unparseable, unp_sampled = (
                (c.get("name"), c.get("type"), c.get("shape"), c.get("sample"), c.get("unparseable"), c.get("unparseable_sampled"))
                if isinstance(c, dict) else (c, "", None, None, None, None))
            name = str(name or "").strip()
            if not name:
                continue
            tables.setdefault(leaf, {})[name.lower()] = {
                "column": name, "table": table, "type": str(ctype or "").strip()}
            # The ontology's own sampled shape (a constructor validated it against the
            # source) seeds the store — a prior, never an override: a shape learned from
            # this process's result rows is fresher and wins.
            if shape and not known_shape(leaf, name):
                with _LOCK:
                    _SHAPES[(leaf, name.lower())] = {"format": str(shape), "sample": str(sample or ""),
                                                     "ts": time.monotonic(), "source": "ontology",
                                                     # the sampled share of non-blank values that are NOT
                                                     # dates in this shape — >0 means TRYCAST, never CAST
                                                     "unparseable": float(unparseable or 0.0),
                                                     # a sampled zero is not a proof → guarded form
                                                     "unparseable_sampled": bool(unp_sampled)}
            # The DATA SPAN the constructor measured (`date_range`) rides beside the shape:
            # a fact only the ontology can carry (a result row set never proves an
            # extent), so it is set once and never learned. It is what lets the guard
            # refuse a filter on a period the data does not cover (a champion's `= '2023'`
            # rewritten to the CURRENT year returned no rows and read as "no robberies").
            span = c.get("span") if isinstance(c, dict) else None
            if isinstance(span, (list, tuple)) and len(span) == 2 and all(span):
                with _LOCK:
                    e = _SHAPES.get((leaf, name.lower()))
                    if e is not None and not e.get("span"):
                        e["span"] = [str(span[0]), str(span[1])]
                        e["span_sampled"] = bool(c.get("span_sampled"))
    if not tables:
        return sum(len(v) for v in known_columns(session_id).values())
    with _LOCK:
        entry = _COLS.get(session_id) or {"tables": {}}
        for leaf, cols in tables.items():
            entry["tables"].setdefault(leaf, {}).update(cols)
        entry["ts"] = time.monotonic()
        _COLS[session_id] = entry
        if len(_COLS) > 1000:
            now = time.monotonic()
            for sid in [s for s, e in _COLS.items() if now - e["ts"] > _TTL_SECONDS]:
                _COLS.pop(sid, None)
        return sum(len(v) for v in entry["tables"].values())


def known_columns(session_id: str) -> Dict[str, Dict[str, Dict[str, str]]]:
    if not session_id:
        return {}
    with _LOCK:
        entry = _COLS.get(session_id)
        if not entry:
            return {}
        if time.monotonic() - entry["ts"] > _TTL_SECONDS:
            _COLS.pop(session_id, None)
            return {}
        return {k: dict(v) for k, v in entry["tables"].items()}


def record_from_event(session_id: str, event: Any) -> int:
    """Record from a ``kg_enrichment_event`` (its ``enrichments[*].text_date_columns``).
    Never raises."""
    try:
        if not isinstance(event, dict):
            return 0
        for enr in event.get("enrichments") or []:
            if isinstance(enr, dict) and enr.get("text_date_columns"):
                record_text_dates(session_id, enr.get("text_date_columns"))
        return sum(len(v) for v in known_columns(session_id).values())
    except Exception:
        return 0


def learn_shape(leaf: str, column: str, values: Any) -> Optional[str]:
    """Learn the ONE shape ``values`` share (None when the evidence does not settle it);
    remembered process-wide — the shape belongs to the data, not to a session."""
    fmt = _df.column_shape(values if isinstance(values, (list, tuple)) else [])
    if not fmt:
        return None
    sample = next((" ".join(str(v).split()) for v in values if v is not None and str(v).strip()), "")
    with _LOCK:
        prev = _SHAPES.get((_leaf(leaf), str(column).lower()))
        if prev and prev.get("format") != fmt:
            logger_msg = (f"[preflight:textdate] {leaf}.{column}: result rows show FORMAT '{fmt}' "
                          f"where the {prev.get('source') or 'learned'} shape said '{prev.get('format')}' — data wins")
            try:
                import logging
                logging.getLogger("quart.app").warning(logger_msg)
            except Exception:
                pass
        _SHAPES[(_leaf(leaf), str(column).lower())] = {"format": fmt, "sample": sample, "ts": time.monotonic(),
                                                       "source": "result"}
        if len(_SHAPES) > _MAX_SHAPES:
            now = time.monotonic()
            for k in [k for k, e in _SHAPES.items() if now - e["ts"] > _SHAPE_TTL_SECONDS]:
                _SHAPES.pop(k, None)
    return fmt


def known_shape(leaf: str, column: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        e = _SHAPES.get((_leaf(leaf), str(column).lower()))
        if not e:
            return None
        if time.monotonic() - e["ts"] > _SHAPE_TTL_SECONDS:
            _SHAPES.pop((_leaf(leaf), str(column).lower()), None)
            return None
        return dict(e)


# ── SQL analysis ───────────────────────────────────────────────────────────────
def _parse(sql: str):
    try:
        import sqlglot
        return [s for s in sqlglot.parse(sql, read=_dialect.read()) if s is not None]
    except Exception:
        return []


def _statement_tables(stmt) -> Dict[str, str]:
    """alias/leaf (lower) -> leaf (lower) for every table the statement names."""
    from sqlglot import exp
    out: Dict[str, str] = {}
    for tbl in stmt.find_all(exp.Table):
        leaf = str(tbl.name or "").lower()
        if not leaf:
            continue
        out[leaf] = leaf
        if tbl.alias:
            out[str(tbl.alias).lower()] = leaf
    return out


def _is_date_expr(node) -> bool:
    """Does ``node`` evaluate to a DATE/TIMESTAMP — a DATE literal, CURRENT_DATE,
    ADD_MONTHS(…), a CAST to DATE, a FORMAT cast — anywhere inside it?"""
    from sqlglot import exp
    kinds = (exp.CurrentDate, exp.CurrentTimestamp, exp.AddMonths, exp.DateAdd, exp.DateSub,
             exp.StrToDate, exp.TsOrDsToDate, exp.Date, exp.DateTrunc)
    for n in [node] + list(node.find_all(*kinds, exp.Cast)):
        if isinstance(n, kinds):
            return True
        if isinstance(n, exp.Cast) and n.to is not None and n.to.this in (
                exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ,
                exp.DataType.Type.DATETIME):
            return True
    return False


def _literal_beside(node, parent) -> Optional[Tuple[str, str]]:
    """The date-shaped STRING literal compared against ``node`` in ``parent`` (a comparison,
    BETWEEN or IN), else None."""
    from sqlglot import exp
    if isinstance(parent, exp.Between):
        for k in ("low", "high"):
            lit = _literal_shape(parent.args.get(k))
            if lit:
                return lit
        return None
    if isinstance(parent, exp.In):
        for e in parent.expressions or []:
            lit = _literal_shape(e)
            if lit:
                return lit
        return None
    if isinstance(parent, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
        other = parent.right if parent.left is node else parent.left
        return _literal_shape(other)
    return None


def _literal_shape(node) -> Optional[Tuple[str, str]]:
    from sqlglot import exp
    if isinstance(node, exp.Literal) and node.is_string:
        fmt = _df.date_shape(node.this)
        return (str(node.this), fmt) if fmt else None
    return None


def _cast_is_date(node) -> bool:
    from sqlglot import exp
    return isinstance(node, exp.Cast) and node.to is not None and node.to.this in (
        exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.DATETIME)


def sql_text_date_refs(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """Pure: every use of a RECORDED text-date column in ``sql`` that has a date-typed
    meaning, classified by kind (see module doc). Empty when nothing is recorded, sqlglot
    is absent or the statement does not parse."""
    _, findings = _analyze(session_id, sql)
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in findings]


def _analyze(session_id: str, sql: str):
    """The analysis behind ``sql_text_date_refs`` WITH the AST handles (``_col``, ``_parent``,
    ``_stmt``) a rewrite needs — one walk, two consumers, so the refusal and the rewrite
    can never disagree about what a statement does."""
    cols = known_columns(session_id)
    out: List[Dict[str, Any]] = []
    stmts: list = []
    if not cols or not sql or not isinstance(sql, str):
        return stmts, out
    try:
        from sqlglot import exp
    except Exception:
        return stmts, out
    transparent = (exp.Trim, exp.Upper, exp.Lower, exp.Paren, exp.Coalesce)
    comparisons = (exp.GT, exp.GTE, exp.LT, exp.LTE)
    stmts = _parse(sql)
    for stmt in stmts:
        aliases = _statement_tables(stmt)
        present = [leaf for leaf in set(aliases.values()) if leaf in cols]
        if not present:
            continue
        # A SELECT alias of a cast over a recorded column is that cast wherever the alias
        # is referenced (Teradata lets a WHERE name a select-list alias): the live turn
        # compared `incident_date BETWEEN '2023-01-01' AND …` — a DATE beside bare strings.
        alias_cast: Dict[str, Tuple[Any, Dict[str, str], str]] = {}
        for al in stmt.find_all(exp.Alias):
            inner = al.this
            if not isinstance(inner, exp.StrToDate):
                continue
            for ic in inner.find_all(exp.Column):
                icn, iq = str(ic.name or "").lower(), str(ic.table or "").lower()
                il = aliases.get(iq, iq) if iq else next((l for l in present if icn in cols[l]), None)
                if il in cols and icn in cols[il]:
                    alias_cast[str(al.alias or "").lower()] = (inner, cols[il][icn], il)
                    break
        for col in stmt.find_all(exp.Column):
            cname = str(col.name or "").lower()
            q = str(col.table or "").lower()
            if not q and cname in alias_cast and col.parent is not None and not isinstance(col.parent, exp.Alias):
                _cast, info, leaf = alias_cast[cname]
                lit = _literal_beside(col, col.parent)
                if lit:
                    out.append({"column": info["column"], "table": info["table"], "leaf": leaf, "type": info["type"],
                                "literal": lit[0], "literal_format": lit[1], "format": None, "kind": "date_expr_literal",
                                "snippet": col.parent.sql(dialect=_dialect.read()),
                                "_col": col, "_node": col, "_parent": col.parent, "_stmt": stmt})
                continue
            if q:
                leaf = aliases.get(q, q)
                if leaf not in cols or cname not in cols[leaf]:
                    continue
            else:
                owners = [leaf for leaf in present if cname in cols[leaf]]
                if len(owners) != 1:
                    continue
                leaf = owners[0]
            info = cols[leaf][cname]
            node, parent = col, col.parent
            while parent is not None and isinstance(parent, transparent):
                node, parent = parent, parent.parent
            if parent is None:
                continue
            f: Dict[str, Any] = {"column": info["column"], "table": info["table"], "leaf": leaf,
                                 "type": info["type"], "literal": None, "literal_format": None,
                                 "format": None, "snippet": parent.sql(dialect=_dialect.read()),
                                 "_col": col, "_node": node, "_parent": parent, "_stmt": stmt}
            kind: Optional[str] = None
            if isinstance(parent, exp.StrToDate):
                kind, f["format"] = "cast_format", str((parent.args.get("format") or exp.Literal.string("")).this)
                # …and a DATE this cast produces CARRIES ITS FORMAT: a bare string literal
                # beside it is converted with that FORMAT, so '2023-01-01' next to a
                # DD-MMM-YYYY cast is an invalid date (Error 2665, measured live). The
                # literal must be typed: DATE '2023-01-01'.
                gp = parent.parent
                lit = _literal_beside(parent, gp) if gp is not None else None
                if lit:
                    out.append({**f, "kind": "date_expr_literal", "literal": lit[0], "literal_format": lit[1],
                                "snippet": gp.sql(dialect=_dialect.read()), "_node": parent, "_parent": gp})
            elif _cast_is_date(parent):
                kind = "cast_plain"
            elif isinstance(parent, (exp.Substring, exp.Left, exp.Right)):
                kind = "substr"
                f["slice"] = _slice_of(parent)
            elif isinstance(parent, exp.Ordered):
                kind = "order"
            elif isinstance(parent, (exp.Max, exp.Min)):
                kind = "minmax"
            elif isinstance(parent, exp.Extract):
                kind = "implicit_cast"
            elif isinstance(parent, exp.Between):
                lits = [_literal_shape(parent.args.get(k)) for k in ("low", "high")]
                lits = [l for l in lits if l]
                if lits:
                    kind, (f["literal"], f["literal_format"]) = "range_literal", lits[0]
                elif any(_is_date_expr(parent.args[k]) for k in ("low", "high") if parent.args.get(k) is not None):
                    kind = "date_expr"
            elif isinstance(parent, exp.In):
                lits = [_literal_shape(e) for e in (parent.expressions or [])]
                lits = [l for l in lits if l]
                if lits:
                    kind, (f["literal"], f["literal_format"]) = "equality_literal", lits[0]
            elif isinstance(parent, comparisons + (exp.EQ, exp.NEQ)):
                other = parent.right if parent.left is node else parent.left
                lit = _literal_shape(other)
                if lit:
                    kind = "equality_literal" if isinstance(parent, (exp.EQ, exp.NEQ)) else "range_literal"
                    f["literal"], f["literal_format"] = lit
                elif other is not None and _is_date_expr(other):
                    kind = "date_expr"
            if kind:
                f["kind"] = kind
                out.append(f)
    return stmts, out


def _slice_of(parent) -> Optional[Dict[str, int]]:
    """The literal (start, length) a SUBSTR/LEFT/RIGHT takes, or None when either is not
    a literal integer (a computed slice is the model's judgement, judged as before)."""
    try:
        from sqlglot import exp

        def _int(node):
            return int(node.this) if isinstance(node, exp.Literal) and node.is_int else None
        if isinstance(parent, exp.Substring):
            start = _int(parent.args.get("start"))
            ln = parent.args.get("length")
            length = _int(ln) if ln is not None else None
            if start is None or (ln is not None and length is None):
                return None
            return {"start": start, "length": length}
        n = _int(parent.args.get("expression"))
        if n is None:
            return None
        if isinstance(parent, exp.Left):
            return {"start": 1, "length": n}
        return None   # RIGHT: a CHAR column is blank-padded, so the slice's position is unknowable
    except Exception:
        return None


_YEAR_RE = re.compile(r"^\d{4}$")


def _year_bounds_of(node) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """The (lo_year, hi_year) a value expression CERTAINLY denotes, or None when it is not
    decidable without judgement: a 4-digit literal (string or int), a date-shaped literal,
    ``DATE '…'``, ``CURRENT_DATE`` / ``CURRENT_TIMESTAMP``, ``EXTRACT(YEAR FROM
    CURRENT_DATE)``, and a CAST / TRIM / parenthesis around any of these. ``ADD_MONTHS``,
    intervals, columns and parameters → None (no verdict)."""
    try:
        import datetime as _dt
        from sqlglot import exp
        cur = node
        while isinstance(cur, (exp.Paren, exp.Trim, exp.Upper, exp.Lower, exp.Cast, exp.TryCast)):
            cur = cur.this
        if isinstance(cur, exp.Literal):
            if cur.is_int:
                y = int(cur.this)
                return (y, y) if 1000 <= y <= 9999 else None
            txt = str(cur.this or "").strip()
            if _YEAR_RE.match(txt):
                return (int(txt), int(txt))
            iso = _iso_literal(txt) if _df.date_shape(txt) else None
            if iso and _YEAR_RE.match(iso[:4]):
                return (int(iso[:4]), int(iso[:4]))
            return None
        if isinstance(cur, (exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentDatetime)):
            y = _dt.date.today().year
            return (y, y)
        if isinstance(cur, exp.Extract) and str(getattr(cur.this, "this", cur.this) or "").upper() == "YEAR":
            inner = cur.expression
            while isinstance(inner, (exp.Paren, exp.Cast, exp.TryCast)):
                inner = inner.this
            if isinstance(inner, (exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentDatetime)):
                y = _dt.date.today().year
                return (y, y)
        return None
    except Exception:
        return None


def _selected_years(f: Dict[str, Any], fmt: Optional[str]) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """The years a finding's comparison certainly SELECTS, or None. Only a year-valued or
    date-valued use of the column is judged: the aligned YEAR slice of the shape, a date
    cast (with or without FORMAT), EXTRACT(YEAR …), or the bare text beside a date-shaped
    literal; the value on the other side must resolve through ``_year_bounds_of``."""
    try:
        from sqlglot import exp
        kind, node, parent = f["kind"], f["_node"], f["_parent"]
        if kind == "substr":
            sl = f.get("slice") or {}
            yr = next(((s, l) for t, s, l in _df.shape_fields(fmt) if t == "YYYY"), None)
            if not yr or (sl.get("start"), sl.get("length")) != yr:
                return None
            cur = parent
        elif kind in ("cast_format", "cast_plain", "implicit_cast"):
            if kind == "implicit_cast" and str(getattr(parent.this, "this", parent.this) or "").upper() != "YEAR":
                return None
            cur = parent
        elif kind in ("range_literal", "equality_literal", "date_expr"):
            cur = node
        else:
            return None
        # climb through what keeps the value year- or date-valued
        while True:
            up = cur.parent
            if up is None:
                return None
            if isinstance(up, exp.Extract):
                if str(getattr(up.this, "this", up.this) or "").upper() != "YEAR":
                    return None
                cur = up
                continue
            if isinstance(up, (exp.Paren, exp.Trim, exp.Cast, exp.TryCast, exp.Upper, exp.Lower)):
                cur = up
                continue
            break
        comp = up
        if isinstance(comp, exp.Between):
            lo, hi = _year_bounds_of(comp.args.get("low")), _year_bounds_of(comp.args.get("high"))
            if lo is None or hi is None:
                return None
            return (lo[0], hi[1])
        if isinstance(comp, exp.In):
            bs = [_year_bounds_of(e) for e in (comp.expressions or [])]
            if not bs or any(b is None for b in bs):
                return None
            return (min(b[0] for b in bs), max(b[1] for b in bs))
        if not isinstance(comp, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return None
        col_left = comp.left is cur
        other = comp.right if col_left else comp.left
        b = _year_bounds_of(other)
        if b is None:
            return None
        if isinstance(comp, exp.EQ):
            return b
        greater = isinstance(comp, (exp.GT, exp.GTE)) if col_left else isinstance(comp, (exp.LT, exp.LTE))
        return (b[0], None) if greater else (None, b[1])
    except Exception:
        return None


def _span_years(span: Any) -> Optional[Tuple[int, int]]:
    try:
        lo, hi = str(span[0])[:4], str(span[1])[:4]
        if _YEAR_RE.match(lo) and _YEAR_RE.match(hi):
            return (int(lo), int(hi))
    except Exception:
        pass
    return None


def _out_of_span(f: Dict[str, Any], shape: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A filter that certainly selects years the column's MEASURED span excludes — the
    data holds no rows for it, so the statement can only return an empty result. Judged
    only with a known shape AND a known span (both stamped by a validated ontology)."""
    span = (shape or {}).get("span")
    yrs = _span_years(span) if span else None
    if not yrs:
        return None
    sel = _selected_years(f, (shape or {}).get("format"))
    if not sel or (sel[0] is None and sel[1] is None):
        return None
    lo, hi = sel
    if (lo is not None and lo > yrs[1]) or (hi is not None and hi < yrs[0]):
        return {"selected": [lo, hi], "span": [str(span[0]), str(span[1])],
                "sampled": bool((shape or {}).get("span_sampled"))}
    return None


def _verdict(f: Dict[str, Any], shape: Optional[Dict[str, Any]]) -> Optional[str]:
    """'violation' | 'unverified' | None(pass) for one finding against the known shape."""
    kind = f["kind"]
    fmt = (shape or {}).get("format")
    # A period outside the data's measured span is a PROVEN finding whatever the form:
    # the aligned year slice, the right CAST, EXTRACT(YEAR) — all correct in shape, all
    # empty in fact. Judged first so a rewrite that fixes the shape can never make it
    # pass (the re-check runs this verdict again and declines).
    if fmt and kind != "date_expr_literal":
        oos = _out_of_span(f, shape)
        if oos:
            f["out_of_span"] = oos
            return "violation"
    if kind == "cast_plain":
        return None
    if kind == "date_expr_literal":
        return "violation"          # a FORMAT-bearing DATE beside a bare string literal: Error 2665, measured
    if kind == "cast_format":
        if not fmt:
            return None
        return None if (_df.format_to_strftime(fmt) or "").lower() == str(f.get("format") or "").lower() else "violation"
    if not fmt:
        return "unverified"
    if kind == "equality_literal":
        return None if f.get("literal_format") == fmt else "violation"
    if kind == "range_literal":
        return None if (_df.is_iso_shape(fmt) and _df.is_iso_shape(f.get("literal_format"))) else "violation"
    if kind == "substr":
        # A slice is a bucket; it is correct exactly when it picks out WHOLE fields of
        # the proven shape — SUBSTR(col, 4, 3) on DD-MMM-YYYY IS the month, SUBSTR(col,
        # 8, 4) the year — and wrong when it cuts across them (SUBSTR(col, 1, 7) is
        # day-and-month). Measured 2026-09-05: the aligned slice is the CHEAP correct
        # form (15 s over 12.35M rows where the per-row conversion blew the 110 s
        # ceiling), so refusing it drove the model to the expensive form, then to nothing.
        sl = f.get("slice") or {}
        start, length = sl.get("start"), sl.get("length")
        aligned = _df.slice_aligned(fmt, start, length)
        if aligned is None:
            return None if _df.is_iso_shape(fmt) else "violation"
        return None if aligned else "violation"
    return None if _df.is_iso_shape(fmt) else "violation"


def text_date_violations(session_id: str, sql: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(proven violations, unverified findings) for ``sql`` — each carrying the column,
    table, kind, the offending snippet and (when known) the sampled shape + FORMAT."""
    proven: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    # judged WITH the AST handles (the span verdict reads the comparison around the
    # column); the handles are stripped from what is reported, as `sql_text_date_refs` does
    for f in _analyze(session_id, sql)[1]:
        shape = known_shape(f["leaf"], f["column"])
        v = _verdict(f, shape)
        if v is None:
            continue
        g = {k: val for k, val in f.items() if not k.startswith("_")}
        g["shape"] = (shape or {}).get("format")
        g["sample"] = (shape or {}).get("sample")
        g["unparseable"] = (shape or {}).get("unparseable") or 0.0
        g["unparseable_sampled"] = bool((shape or {}).get("unparseable_sampled"))
        (proven if v == "violation" else unverified).append(g)
    return proven, unverified


# ── learning from results ──────────────────────────────────────────────────────
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_\.\"]+)", re.IGNORECASE)


def _tables_in_sql(sql: str) -> List[str]:
    stmts = _parse(sql)
    if stmts:
        leaves = set()
        for s in stmts:
            leaves.update(_statement_tables(s).values())
        return sorted(leaves)
    return sorted({_leaf(m.group(1)) for m in _TABLE_RE.finditer(sql or "")})


def learn_from_result(session_id: str, sql: str, result: Any, max_rows: int = 200) -> List[Tuple[str, str, str]]:
    """After a query ran: for every recorded text-date column of a table the statement
    names whose values appear in the result rows, learn the shape. Returns
    [(leaf, column, format)] learned. Never raises."""
    learned: List[Tuple[str, str, str]] = []
    try:
        cols = known_columns(session_id)
        if not cols or not isinstance(result, dict):
            return learned
        rows = result.get("results")
        if not isinstance(rows, list):
            return learned
        rows = [r for r in rows[:max_rows] if isinstance(r, dict)]
        if not rows:
            return learned
        keys = {k.lower(): k for r in rows for k in r.keys() if isinstance(k, str)}
        for leaf in _tables_in_sql(sql or ""):
            for cname, info in (cols.get(leaf) or {}).items():
                key = keys.get(cname)
                if not key:
                    continue
                fmt = learn_shape(leaf, info["column"], [r.get(key) for r in rows])
                if fmt:
                    learned.append((leaf, info["column"], fmt))
    except Exception:
        return learned
    return learned


# ── the mechanical rewrite ─────────────────────────────────────────────────────
#: Literal shapes a rewrite may convert to a DATE literal without guessing. A slash
#: date is left alone: `01/02/2023` is the 2nd of January or the 1st of February, and
#: a rewrite that picks one silently is worse than a refusal that says so.
_REWRITABLE_LITERAL_SHAPES = ("YYYY-MM-DD", "YYYY/MM/DD", "YYYYMMDD", "DD-MMM-YYYY", "DD-MMM-YY",
                              "DD MMM YYYY", "DD MMM YY", "DD/MMM/YYYY", "DD/MMM/YY", "DD.MMM.YYYY")


def _iso_literal(text: str) -> Optional[str]:
    k = _df.date_key(text)
    return f"{k[0]:04d}-{k[1]:02d}-{k[2]:02d}" if k else None


def _cast_node(col_sql: str, fmt: str, unparseable=0.0, sampled: bool = False):
    """The read of the column as a DATE — ``dateforms.date_read_form``, ONE chooser with the
    enrichment and the refusal: a plain CAST when the share of non-date values is an
    exact zero, the cheap length guard when that zero was only sampled, the regex-free
    TRYCAST rebuild when non-date values were measured."""
    import sqlglot
    return sqlglot.parse_one(_df.date_read_form(col_sql, fmt, unparseable, sampled), read="teradata")


def _restore_trycast(stmt):
    """sqlglot parses ``TRYCAST(x AS DATE FORMAT '…')`` to a *safe* StrToDate and regenerates
    it as a plain CAST — which would silently re-arm Error 2666 on a statement whose
    author had already made it safe. Render every safe cast verbatim as TRYCAST."""
    from sqlglot import exp
    for n in list(stmt.find_all(exp.StrToDate)):
        if not n.args.get("safe"):
            continue
        fmt_node = n.args.get("format")
        td_fmt = fmt_node.sql(dialect="teradata").strip("'") if fmt_node is not None else ""
        # sqlglot holds the strftime form; the Teradata renderer gives the FORMAT back
        rendered = n.sql(dialect="teradata")           # CAST(x AS DATE FORMAT 'DD-MMM-YYYY')
        inner = rendered[len("CAST("):-1] if rendered.startswith("CAST(") and rendered.endswith(")") else None
        if inner:
            n.replace(exp.Anonymous(this="TRYCAST", expressions=[exp.Var(this=inner)]))


def _restore_substr(stmt):
    """sqlglot renders every substring as ``SUBSTRING(x, a, b)``, which Teradata accepts only
    in its ``FROM … FOR`` form; ``SUBSTR(x, a, b)`` is valid for both spellings' meaning."""
    from sqlglot import exp
    for n in list(stmt.find_all(exp.Substring)):
        parts = [n.this] + [n.args[k] for k in ("start", "length") if n.args.get(k) is not None]
        n.replace(exp.Anonymous(this="SUBSTR", expressions=[p.copy() for p in parts]))


def _restore_charlen(stmt):
    """sqlglot renders ``CHAR_LENGTH`` as ``LENGTH``; the length guard is emitted as
    ``CHAR_LENGTH`` (the form measured on Teradata) and is rendered back verbatim."""
    from sqlglot import exp
    for n in list(stmt.find_all(exp.Length)):
        n.replace(exp.Anonymous(this="CHAR_LENGTH", expressions=[n.this.copy()]))


def rewrite_sql(session_id: str, sql: str) -> Optional[Dict[str, Any]]:
    """The mechanical correction of a statement the guard would refuse: every PROVEN
    finding whose fix needs no judgement is applied — the column wrapped in
    ``CAST(col AS DATE FORMAT '<learned shape>')``, a date literal beside it turned into a
    ``DATE '…'`` literal, a mismatched FORMAT replaced. Returns ``{"sql", "changes"}`` or
    None whenever ANY judgement would be involved: shape unknown, a SUBSTR/LEFT bucket
    (which period the model meant is its call), an ambiguous slash literal, more than one
    statement, or a rewrite that does not re-parse clean. Never raises."""
    try:
        import sqlglot
        from sqlglot import exp
        stmts, findings = _analyze(session_id, sql)
        if len(stmts) != 1 or not findings:
            return None
        todo = []
        for f in findings:
            shape = known_shape(f["leaf"], f["column"])
            v = _verdict(f, shape)
            if v is None:
                continue
            if v == "unverified" or f["kind"] == "substr":
                return None
            todo.append((f, (shape or {}).get("format")))
        if not todo:
            return None
        changes: List[str] = []
        for f, fmt in todo:
            col, node, parent = f["_col"], f["_node"], f["_parent"]
            if f["kind"] == "date_expr_literal":
                before = parent.sql(dialect="teradata")
                lits = ([parent.args.get("low"), parent.args.get("high")] if isinstance(parent, exp.Between)
                        else list(parent.expressions or []) if isinstance(parent, exp.In)
                        else [parent.right if parent.left is node else parent.left])
                for lit in lits:
                    if isinstance(lit, exp.Literal) and lit.is_string and _df.date_shape(lit.this):
                        if _df.date_shape(lit.this) not in _REWRITABLE_LITERAL_SHAPES:
                            return None
                        iso = _iso_literal(lit.this)
                        if not iso:
                            return None
                        lit.replace(sqlglot.parse_one(f"DATE '{iso}'", read="teradata"))
                changes.append(f"{before} → {parent.sql(dialect='teradata')}")
                continue
            shape_info = known_shape(f["leaf"], f["column"]) or {}
            bad, sampled = float(shape_info.get("unparseable") or 0.0), bool(shape_info.get("unparseable_sampled"))
            safe = bad > 0.0 or sampled
            if f["kind"] == "cast_format":
                if safe:
                    # a mismatched FORMAT on a column holding non-date values → the guarded form
                    guarded = _cast_node(parent.this.sql(dialect="teradata"), fmt, bad, sampled)
                    parent.replace(guarded)
                    changes.append(f"{f['snippet']} → {guarded.sql(dialect='teradata')}")
                    continue
                parent.set("format", exp.Literal.string(_df.format_to_strftime(fmt)))
                changes.append(f"{f['snippet']} → {parent.sql(dialect='teradata')}")
                continue
            before = parent.sql(dialect="teradata")
            wrapped = _cast_node(col.sql(dialect="teradata"), fmt, bad, sampled)
            node.replace(wrapped)
            if f["kind"] in ("range_literal", "equality_literal"):
                lits = []
                if isinstance(parent, exp.Between):
                    lits = [parent.args.get("low"), parent.args.get("high")]
                elif isinstance(parent, exp.In):
                    lits = list(parent.expressions or [])
                else:
                    lits = [parent.right if parent.left is wrapped else parent.left]
                for lit in lits:
                    if not (isinstance(lit, exp.Literal) and lit.is_string):
                        continue
                    if _df.date_shape(lit.this) not in _REWRITABLE_LITERAL_SHAPES:
                        return None
                    iso = _iso_literal(lit.this)
                    if not iso:
                        return None
                    lit.replace(sqlglot.parse_one(f"DATE '{iso}'", read="teradata"))
            changes.append(f"{before} → {parent.sql(dialect='teradata')}")
        stmt = stmts[0]
        _restore_substr(stmt)
        _restore_charlen(stmt)
        _restore_trycast(stmt)
        new_sql = stmt.sql(dialect="teradata")
        if not _parse(new_sql):
            return None
        # The rewrite must leave nothing the guard would still refuse.
        if any(_verdict(g, known_shape(g["leaf"], g["column"])) for g in _analyze(session_id, new_sql)[1]):
            return None
        return {"sql": new_sql, "changes": changes,
                "columns": sorted({f"{f['table']}.{f['column']}" for f, _ in todo})}
    except Exception:
        return None


# ── the refusal ────────────────────────────────────────────────────────────────
_KIND_TEXT = {
    "range_literal": "compares the column as a STRING against a date literal",
    "equality_literal": "tests the column for equality against a literal in another shape (it can never match)",
    "date_expr": "compares the column against a DATE expression, which converts it with the session default format",
    "substr": "slices the column's text ACROSS its fields (on 31-OCT-2023, SUBSTR(…, 1, 7) is '31-OCT-': day-and-month, not a period)",
    "order": "orders the column as a STRING (every 31st first)",
    "minmax": "takes MIN/MAX of the column as a STRING",
    "implicit_cast": "extracts a date part with an implicit conversion under the session default format",
    "cast_format": "casts the column with a FORMAT that does not match its shape",
    "date_expr_literal": "compares the DATE it casts to against a bare string literal, which Teradata converts with the cast's own FORMAT ('2023-01-01' is not a DD-MMM-YYYY: Error 2665) — write DATE '2023-01-01'",
}


def probe_sql(f: Dict[str, Any]) -> str:
    c, t = f["column"], f["table"]
    return f"SELECT TOP 3 {c} FROM {t} WHERE {c} IS NOT NULL AND TRIM({c}) <> ''"


def _cast_form(f: Dict[str, Any]) -> str:
    fmt = f.get("shape")
    if not fmt:
        return f"CAST({f['column']} AS DATE FORMAT '<the sampled shape>')"
    return _df.date_read_form(f["column"], fmt, f.get("unparseable"), bool(f.get("unparseable_sampled")))


def block_result(proven: List[Dict[str, Any]], unverified: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recovery-shaped: names the column, what the statement does to it, the shape when it
    is known and the exact rewrite — or the one probe that settles the shape. The query
    was NOT run."""
    proven = sorted(proven, key=lambda g: 0 if g.get("out_of_span") else 1)
    lead = (proven or unverified)[0]
    col, tbl, typ = lead["column"], lead["table"], lead.get("type") or "characters"
    cast = _cast_form(lead)
    if proven and lead.get("out_of_span"):
        oos = lead["out_of_span"]
        lo, hi = oos["span"]
        slo, shi = oos["selected"]
        period = (f"the year {slo}" if slo is not None and slo == shi
                  else f"years from {slo} on" if shi is None
                  else f"years up to {shi}" if slo is None
                  else f"the years {slo}-{shi}")
        yr = next(((s_, l_) for t_, s_, l_ in _df.shape_fields(lead.get("shape")) if t_ == "YYYY"), None)
        example = (f"SUBSTR({col}, {yr[0]}, {yr[1]}) = '{lo[:4]}'" if yr
                   else f"{cast} BETWEEN DATE '{lo}' AND DATE '{hi}'")
        msg = (f"[Preflight] {col} on {tbl} is a DATE stored as {typ} text whose values span "
               f"{lo} to {hi}{' (sampled)' if oos.get('sampled') else ''}. This statement filters it to "
               f"{period} — outside that span — so the data holds NO rows for it and the query could "
               f"only return an empty result; that is not evidence that nothing happened, it is a period "
               f"the extract does not cover. The query was NOT executed. Query within {lo[:4]}"
               f"{'' if lo[:4] == hi[:4] else '-' + hi[:4]} instead (e.g. {example}), and say in the "
               f"answer which period the figures cover.")
        err = f"Text-stored date {col} filtered to a period outside its data span ({lo} to {hi})"
        return {"status": "error", "preflight": True, "preflight_text_date": True,
                "error": err, "data": msg,
                "violations": proven, "unverified": unverified}
    if proven:
        what = "; ".join(sorted({f"{f['snippet']} {_KIND_TEXT.get(f['kind'], 'uses the column as text')}" for f in proven}))
        bad = float(lead.get("unparseable") or 0.0)
        bad_s = (f" — and {round(bad * 100, 1)}% of its non-blank values are NOT dates in that shape, so a "
                 f"plain CAST fails (Error 2666): cast only values matching the shape, as shown below" if bad > 0 else "")
        advice = _df.text_period_advice(col, lead.get("shape"))
        msg = (f"[Preflight] {col} on {tbl} is a DATE stored as {typ} text; its values look like "
               f"'{lead.get('sample') or '?'}' (FORMAT '{lead.get('shape')}'){bad_s}. This statement {what}. "
               f"The query was NOT executed. {advice} "
               f"Rewrite every date comparison, chronological ORDER BY and MIN/MAX on {col} to use "
               f"{cast} — e.g. {cast} >= DATE '2023-01-01', ORDER BY {cast} — and retry.")
        err = f"Text-stored date {col} used as if it were a DATE"
    else:
        what = "; ".join(sorted({f"{f['snippet']} {_KIND_TEXT.get(f['kind'], 'uses the column as text')}" for f in unverified}))
        msg = (f"[Preflight] {col} on {tbl} is a DATE stored as {typ} text, and its stored shape is not "
               f"yet known — a string comparison, ORDER BY, MIN/MAX or prefix bucket on it is correct only "
               f"if the text is ISO (YYYY-MM-DD), which nothing has established. This statement {what}. "
               f"The query was NOT executed. Run {probe_sql(lead)} first; the guard learns the shape "
               f"from that result and then names the exact FORMAT for CAST({col} AS DATE FORMAT '…'). "
               f"Retry the statement with that CAST on every comparison, ORDER BY, MIN/MAX and period bucket.")
        err = f"Text-stored date {col} used as if it were a DATE (shape not yet sampled)"
    return {"status": "error", "preflight": True, "preflight_text_date": True,
            "error": err, "data": msg,
            "violations": proven, "unverified": unverified}


def _reset_all() -> None:
    with _LOCK:
        _COLS.clear()
        _SHAPES.clear()
