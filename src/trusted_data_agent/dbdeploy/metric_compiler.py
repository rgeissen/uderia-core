"""
metric_compiler — deterministic compilation of a Databricks metric definition into
Teradata SQL (a SELECT, or a CREATE VIEW), for a requested slice of dimensions/measures.

Why this is correct (and ratio-safe) by construction: the compiled SQL always aggregates
the **base source table** at the requested grain — `SELECT <dims>, <measure exprs> FROM
<source> GROUP BY <dims>`. Because every measure expression (SUM, AVG, and ratios like
`SUM(a)/NULLIF(SUM(b),0)`) is evaluated against base rows within each group, there is no
re-aggregation of a pre-aggregate — so non-additive measures stay correct at any grain.
(The `additive` flag from the metric definition is therefore metadata, only relevant to a
hypothetical "re-aggregate a materialized cube" path, which this compiler avoids.)

Expression translation is Spark→Teradata via SQLGlot (`parse_one(read="spark").sql("teradata")`);
on failure the original expression is used as-is and a warning is recorded.

Pure: no I/O. The LLM (or a view-gen caller) only chooses the slice; this does the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class MetricCompileError(ValueError):
    pass


import re

_SIMPLE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def _qualified(dotted: str) -> str:
    """Quote a possibly db.table identifier (`order.order_header` → `"order"."order_header"`)."""
    return ".".join(_q(p) for p in str(dotted).split(".") if p)


# Spark date/time-bucket UNIT → Teradata TRUNC() format string.
_TD_TRUNC_FMT = {
    "YEAR": "YYYY", "YYYY": "YYYY", "YY": "YYYY",
    "QUARTER": "Q", "Q": "Q",
    "MONTH": "MM", "MON": "MM", "MM": "MM",
    "WEEK": "WW", "WW": "WW", "IW": "WW",
    "DAY": "DD", "DD": "DD", "DATE": "DD", "DOY": "DD",
}


def _unit_name(node) -> str:
    u = node.args.get("unit")
    if u is None:
        return "DAY"
    return (getattr(u, "name", None) or u.this if hasattr(u, "this") else str(u)).upper()


def _td_datefix(node):
    """Rewrite Spark date functions SQLGlot's Teradata dialect can't render
    (``TIMESTAMP_TRUNC``/``DATE_TRUNC``, ``DATEDIFF``, ``DATE_ADD``/``TS_OR_DS_ADD``)
    into valid Teradata before generation.  Applied bottom-up via ``transform``.
    """
    import sqlglot
    from sqlglot import exp

    def _td(sql_text: str):
        return sqlglot.parse_one(sql_text, read="teradata")

    def _render(child):
        # Fix the child's own descendants FIRST, then render — otherwise a nested
        # function (e.g. DATEDIFF inside a CAST) would be frozen un-transformed,
        # because `transform` is pre-order and we render here before it recurses.
        if child is None:
            return ""
        return child.transform(_td_datefix).sql(dialect="teradata")

    # SQLGlot maps Spark CAST → Teradata TRYCAST, but Teradata TRYCAST only accepts
    # CHAR/VARCHAR *input* (Error 9134 at query time on numeric/date casts). Spark
    # CAST is a hard cast anyway → use plain Teradata CAST, with TEXT→VARCHAR.
    if isinstance(node, (exp.TryCast, exp.Cast)):
        to = node.to
        target = ("VARCHAR(4000)" if to.this == exp.DataType.Type.TEXT
                  else to.sql(dialect="teradata"))
        return _td(f"CAST({_render(node.this)} AS {target})")

    # Standalone TEXT data type (defensive — e.g. outside a cast).
    if isinstance(node, exp.DataType) and node.this == exp.DataType.Type.TEXT:
        return exp.DataType.build("VARCHAR(4000)", dialect="teradata")

    # Teradata has no boolean literal — `TRUE`/`FALSE` are read as column names
    # (Error 5628). Map to BYTEINT 1/0.
    if isinstance(node, exp.Boolean):
        return exp.Literal.number(1 if node.this else 0)

    if isinstance(node, (exp.TimestampTrunc, exp.DateTrunc)):
        fmt = _TD_TRUNC_FMT.get(_unit_name(node), "DD")
        return _td(f"TRUNC(CAST({_render(node.this)} AS DATE), '{fmt}')")

    if isinstance(node, exp.DateDiff):
        unit = _unit_name(node)
        # DateDiff(this=end, expression=start); unwrap any TsOrDsToDate wrappers.
        end = _render(node.this.this if isinstance(node.this, exp.TsOrDsToDate) else node.this)
        start = _render(node.expression.this if isinstance(node.expression, exp.TsOrDsToDate) else node.expression)
        if unit in ("MONTH", "MON", "MM"):
            return _td(f"((EXTRACT(YEAR FROM {end}) * 12 + EXTRACT(MONTH FROM {end})) "
                       f"- (EXTRACT(YEAR FROM {start}) * 12 + EXTRACT(MONTH FROM {start})))")
        if unit in ("YEAR", "YYYY"):
            return _td(f"(EXTRACT(YEAR FROM {end}) - EXTRACT(YEAR FROM {start}))")
        # default: whole days via DATE subtraction
        return _td(f"(CAST({end} AS DATE) - CAST({start} AS DATE))")

    if isinstance(node, exp.TsOrDsAdd):
        return _td(f"(CAST({_render(node.this)} AS DATE) + {_render(node.expression)})")

    # Spark `unix_timestamp(ts)` → SQLGlot's STR_TO_UNIX, which Teradata has not got
    # (Error 3706 on four energy-utilities views: `Data Type "end_timestamp" does not
    # match a Defined Type name`).  Seconds since the epoch from the timestamp's own
    # parts — exact for what the metrics do with it (a difference of two timestamps).
    if isinstance(node, (exp.StrToUnix, exp.TimeToUnix)):
        ts = _render(node.this)
        return _td(f"((CAST({ts} AS DATE) - DATE '1970-01-01') * 86400 "
                   f"+ EXTRACT(HOUR FROM {ts}) * 3600 + EXTRACT(MINUTE FROM {ts}) * 60 "
                   f"+ EXTRACT(SECOND FROM {ts}))")

    # Spark `COUNT_IF(cond)` — Teradata has no such function (Error 3706, the parser
    # reads the argument as a type name: `Data Type "is_global_partner" does not match
    # a Defined Type name`).  The standard form: SUM(CASE WHEN cond THEN 1 ELSE 0 END).
    if isinstance(node, exp.CountIf):
        cond = _render(_boolify(node.this))
        return _td(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)")

    # An aggregate wrapped in another aggregate with nothing but a cast / parens
    # between them (`SUM(CAST(COUNT_IF(…) AS DOUBLE))` in the payments model):
    # Teradata refuses nested aggregation outright (Error 3568), and at ONE grain
    # the outer SUM/AVG/MIN/MAX of a single aggregated value is that value — so the
    # outer aggregate is dropped and the cast kept.  Only those four are identity
    # over one value; a COUNT of an aggregate (1) is not, and is left alone.
    if isinstance(node, (exp.Sum, exp.Avg, exp.Min, exp.Max)) and _wraps_aggregate(node.this):
        return node.this.transform(_td_datefix)

    # A flag column used as a bare predicate (`CASE WHEN is_critical_care THEN …`,
    # `WHEN is_active AND amount > 0`): Teradata has no boolean type — the flag is a
    # BYTEINT and must be compared.  Only a bare column in predicate position is
    # touched, recursively through AND / OR / NOT; a comparison is left alone.
    if isinstance(node, exp.If):
        node.set("this", _boolify(node.this))
        return node

    return node


def _wraps_aggregate(arg) -> bool:
    """True when ``arg`` — through parens / casts only — is itself an aggregate."""
    from sqlglot import exp
    while isinstance(arg, (exp.Paren, exp.Cast, exp.TryCast)):
        arg = arg.this
    return isinstance(arg, exp.AggFunc)


def _boolify(cond):
    """A bare column in a boolean position becomes `col = 1` (Teradata BYTEINT flag)."""
    from sqlglot import exp
    if isinstance(cond, exp.Column):
        return exp.EQ(this=cond, expression=exp.Literal.number(1))
    if isinstance(cond, (exp.And, exp.Or)):
        cond.set("this", _boolify(cond.this))
        cond.set("expression", _boolify(cond.expression))
        return cond
    if isinstance(cond, exp.Not):
        cond.set("this", _boolify(cond.this))
        return cond
    if isinstance(cond, exp.Paren):
        cond.set("this", _boolify(cond.this))
        return cond
    return cond

    return node


def translate_expr(expr: str, *, read: str = "spark", write: str = "teradata") -> Tuple[str, bool]:
    """Transpile a SQL expression fragment between dialects. Returns (sql, ok).

    SQLGlot's Teradata generator does not implement several Spark date functions
    (it would emit a bare ``TIMESTAMP_TRUNC(col, MONTH)`` — invalid Teradata), so
    we rewrite them to valid Teradata (``TRUNC``/``EXTRACT``/date arithmetic) via
    an AST pass before generation.
    """
    if not expr or not isinstance(expr, str):
        return "", False
    try:
        import sqlglot
        tree = sqlglot.parse_one(expr, read=read)
        if write == "teradata":
            tree = tree.transform(_td_datefix)
        # ``identify="safe"`` makes SQLGlot proactively quote identifiers that need it —
        # crucially Teradata **reserved words** used as column names (``language``,
        # ``order``, ``value`` …), which otherwise emit bare and fail at CREATE VIEW with
        # Error 3706. It draws on SQLGlot's maintained Teradata keyword set, so newly
        # reserved words are covered automatically; functions/keywords (SUM, CAST, DATE)
        # stay unquoted. This is the one identifier path that does NOT already flow through
        # the dialect emitter's ``_q`` (which quotes everything), so it's guarded here.
        identify = "safe" if write == "teradata" else False
        return tree.sql(dialect=write, identify=identify), True
    except Exception:
        # Fallback (sqlglot unavailable / unparseable fragment): a bare identifier that
        # happens to be a reserved word must still be quoted proactively so the view compiles.
        stripped = expr.strip()
        if _SIMPLE_IDENT_RE.match(stripped):
            return _q(stripped), True
        return expr, False


@dataclass
class MetricCompileResult:
    sql: str                                   # full statement (SELECT…; or CREATE VIEW…;)
    select_sql: str                            # the SELECT (without CREATE VIEW wrapper)
    dimensions: List[str] = field(default_factory=list)
    measures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def resolve_metric_from_entity(props: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """From a KG ``metric`` node's properties → (metric_def, source_table).

    metric_def = {dimensions, measures} (the enriched fields stamped by the walker).
    source_table = the Teradata base table to aggregate: derived from ``metric_source``
    (catalog.schema.table → schema.table) else ``owner_domain.owner_product``.
    """
    mdef = {"dimensions": props.get("dimensions") or [],
            "measures": props.get("measures") or []}
    source_table: Optional[str] = None
    src = props.get("metric_source")
    if src:
        parts = [p for p in str(src).split(".") if p]
        if len(parts) >= 2:
            source_table = f"{parts[-2]}.{parts[-1]}"
    if not source_table:
        od, op = props.get("owner_domain"), props.get("owner_product")
        if od and op:
            source_table = f"{od}.{op}"
    return mdef, source_table


def _fold(name: Any) -> str:
    """A name folded for matching: lower-case, letters+digits only — so a COLUMN name
    (`fi_product_revenue`) and a DISPLAY name (`Total Fi Product Revenue`) meet."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def closest_declared(requested: str, declared: List[str]) -> Optional[str]:
    """The declared name a requested one most plausibly meant, or None when nothing is
    close. Containment first (a column name inside a display name), then difflib on the
    folded forms at a floor that separates a naming slip from an unrelated concept."""
    import difflib
    req = _fold(requested)
    if not req or not declared:
        return None
    contained = [d for d in declared if req in _fold(d) or _fold(d) in req]
    if contained:
        return min(contained, key=lambda d: abs(len(_fold(d)) - len(req)))
    best, score = None, 0.0
    for d in declared:
        r = difflib.SequenceMatcher(None, req, _fold(d)).ratio()
        if r > score:
            best, score = d, r
    return best if score >= 0.6 else None


def no_measure_message(metric_def: Dict[str, Any], selected_measures: Optional[List[str]]) -> str:
    """The recovery-shaped refusal for a slice that selects no measure.

    The bare "a metric query requires at least one measure" was a dead end: the model had
    asked for `fi_product_revenue` (a column name) on a metric whose measures are display
    names, and the message told it neither that the name was unknown nor what the names
    are — it fell through to a second wasted tool round (prod session d9a60903). The
    `query_product` REJECT already names its admissible members; this one now does too,
    plus the closest declared name for each requested one, so the fix is one step."""
    declared = [str(m.get("name") or "") for m in (metric_def.get("measures") or []) if isinstance(m, dict) and m.get("name")]
    requested = [str(m).strip() for m in (selected_measures or []) if str(m).strip()]
    if not declared:
        return ("a metric query requires at least one measure, and this metric declares no "
                "measures — it cannot be compiled")
    listed = ", ".join(f"'{d}'" for d in declared)
    if not requested:
        return (f"a metric query requires at least one measure — none was requested; "
                f"declared measures: {listed}. Call compile_metric again with one or more "
                f"of these exact names (omit `measures` to take all of them)")
    msg = (f"a metric query requires at least one measure — none of the requested measure "
           f"name(s) {', '.join(repr(r) for r in requested)} is declared on this metric "
           f"(a column name is not a measure name); declared measures: {listed}")
    hints = []
    for r in requested:
        c = closest_declared(r, declared)
        if c:
            hints.append(f"'{r}' → closest declared: '{c}'")
    if hints:
        msg += "; " + "; ".join(hints)
    msg += ". Call compile_metric again with names from the declared list exactly as written"
    return msg


def compile_metric(
    metric_def: Dict[str, Any],
    *,
    source_table: str,
    selected_dimensions: Optional[List[str]] = None,
    selected_measures: Optional[List[str]] = None,
    as_view: Optional[Tuple[str, str]] = None,
) -> MetricCompileResult:
    """Compile a metric definition to Teradata SQL.

    Args:
        metric_def: {dimensions: [{name, expr}], measures: [{name, expr, additive?}], ...}
        source_table: the Teradata base table to aggregate (e.g. "order.order_header").
        selected_dimensions / selected_measures: optional case-insensitive name subsets
            (None = all). Selecting fewer dimensions = a coarser grain.
        as_view: (database, view_name) → wrap the SELECT in CREATE VIEW; else a bare SELECT.

    Raises:
        MetricCompileError: if no measures remain (a metric query needs ≥1 measure).
    """
    dims = list(metric_def.get("dimensions") or [])
    meas = list(metric_def.get("measures") or [])
    if selected_dimensions is not None:
        want = {d.strip().lower() for d in selected_dimensions}
        dims = [d for d in dims if (d.get("name") or "").lower() in want]
    if selected_measures is not None:
        want = {m.strip().lower() for m in selected_measures}
        meas = [m for m in meas if (m.get("name") or "").lower() in want]
    if not meas:
        raise MetricCompileError(no_measure_message(metric_def, selected_measures))

    warnings: List[str] = []
    select_items: List[str] = []
    group_items: List[str] = []

    for d in dims:
        expr, ok = translate_expr(d.get("expr") or d.get("name"))
        if not ok:
            warnings.append(f"dimension '{d.get('name')}': expression not transpiled, used as-is")
        select_items.append(f"{expr} AS {_q(d['name'])}")
        group_items.append(expr)

    for m in meas:
        expr, ok = translate_expr(m.get("expr"))
        if not ok:
            warnings.append(f"measure '{m.get('name')}': expression not transpiled, used as-is")
        select_items.append(f"{expr} AS {_q(m['name'])}")

    select_sql = "SELECT " + ", ".join(select_items) + f"\nFROM {_qualified(source_table)}"
    if group_items:
        select_sql += "\nGROUP BY " + ", ".join(group_items)

    if as_view:
        view_db, view_name = as_view
        sql = f"CREATE VIEW {_q(view_db)}.{_q(view_name)} AS\n{select_sql};"
    else:
        sql = select_sql + ";"

    return MetricCompileResult(
        sql=sql, select_sql=select_sql,
        dimensions=[d["name"] for d in dims], measures=[m["name"] for m in meas],
        warnings=warnings,
    )
