"""Date-text recognition shared by the visual components (pure, no I/O).

A warehouse hands a chart its dates as TEXT in whatever form the driver renders —
Teradata's ``31-OCT-2023`` (space-padded when the column is CHAR), ISO ``2023-10-31``,
``Oct 31, 2023``, ``2023Q4``, ``10/31/2023`` — and a line chart must run left-to-right
in TIME order regardless of the order the rows arrived in (``TOP 30 … ORDER BY date
DESC``, or a text column sorted lexically, where ``31-AUG`` sorts after ``30-SEP``).

``date_key`` turns one such string into a sortable tuple, or None.  It mirrors the
catalogue in ``components/builtin/dashboard/renderer.js`` (``_DATE_FORMS``) so the two
visual components order a temporal axis identically; keep the two in step.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"


def _yr(y: str) -> int:
    n = int(y)
    if len(y) == 2:
        return 2000 + n if n < 70 else 1900 + n
    return n


def _mon(s: str) -> int:
    return _MON[s[:3].lower()]


Key = Tuple[int, int, int, int, int, int]

# (regex, builder) — the builder receives the match and the per-column slash order.
_FORMS = [
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?$", re.I),
     lambda m, o: (int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0), int(m[6] or 0))),
    (re.compile(r"^(\d{4})-(\d{2})$"), lambda m, o: (int(m[1]), int(m[2]), 1, 0, 0, 0)),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), lambda m, o: (int(m[1]), int(m[2]), int(m[3]), 0, 0, 0)),
    (re.compile(rf"^(\d{{1,2}})[-\s./]{_MON_RE}[-\s./](\d{{2}}|\d{{4}})$", re.I),
     lambda m, o: (_yr(m[3]), _mon(m[2]), int(m[1]), 0, 0, 0)),
    (re.compile(rf"^{_MON_RE}[-\s./]?(\d{{1,2}}),?[-\s./](\d{{4}})$", re.I),
     lambda m, o: (int(m[3]), _mon(m[1]), int(m[2]), 0, 0, 0)),
    (re.compile(rf"^{_MON_RE}[-\s./]?(\d{{4}})$", re.I),
     lambda m, o: (int(m[2]), _mon(m[1]), 1, 0, 0, 0)),
    (re.compile(r"^(\d{1,2})[/-](\d{4})$"), lambda m, o: (int(m[2]), int(m[1]), 1, 0, 0, 0)),
    (re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$"),
     lambda m, o: ((_yr(m[3]), int(m[2]), int(m[1]), 0, 0, 0) if o == "DMY"
                   else (_yr(m[3]), int(m[1]), int(m[2]), 0, 0, 0))),
    (re.compile(r"^(\d{4})[-\s]?Q([1-4])$", re.I), lambda m, o: (int(m[1]), (int(m[2]) - 1) * 3 + 1, 1, 0, 0, 0)),
    (re.compile(r"^Q([1-4])(?:[-\s/']?(\d{2}|\d{4}))?$", re.I),
     lambda m, o: (_yr(m[2]) if m[2] else 2000, (int(m[1]) - 1) * 3 + 1, 1, 0, 0, 0)),
    (re.compile(r"^(?:H|S)([12])[-\s]?(\d{4})$", re.I), lambda m, o: (int(m[2]), (int(m[1]) - 1) * 6 + 1, 1, 0, 0, 0)),
    (re.compile(r"^(\d{4})-?W(\d{2})$", re.I), lambda m, o: (int(m[1]), 1, 1 + (int(m[2]) - 1) * 7, 0, 0, 0)),
    (re.compile(rf"^{_MON_RE}$", re.I), lambda m, o: (2000, _mon(m[1]), 1, 0, 0, 0)),
    (re.compile(r"^(\d{4})$"), lambda m, o: (int(m[1]), 1, 1, 0, 0, 0) if 1800 <= int(m[1]) <= 2200 else None),
]

_SLASH = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$")


def slash_order(values: Iterable) -> str:
    """'DMY' when any slash date's first part exceeds 12 (and none proves month-first),
    else 'MDY' — decided once per column from the evidence, as the renderer does."""
    dmy = mdy = False
    for v in values:
        m = _SLASH.match(str(v or "").strip())
        if not m:
            continue
        if int(m[1]) > 12:
            dmy = True
        if int(m[2]) > 12:
            mdy = True
    return "DMY" if dmy and not mdy else "MDY"


def date_key(value, order: str = "MDY") -> Optional[Key]:
    """A sortable (y, m, d, hh, mm, ss) for a recognised date string, else None."""
    if value is None:
        return None
    s = " ".join(str(value).split())
    if not s:
        return None
    for rx, build in _FORMS:
        m = rx.match(s)
        if m:
            try:
                return build(m, order)
            except (KeyError, ValueError):
                return None
    return None


def is_date_column(values: Iterable, sample: int = 60) -> bool:
    """True when every non-empty sampled value is a recognised date form."""
    seen = 0
    for v in values:
        s = " ".join(str(v).split()) if v is not None else ""
        if not s:
            continue
        seen += 1
        if date_key(s) is None:
            return False
        if seen >= sample:
            break
    return seen > 0


def sort_chronological(rows: List[dict], field: str) -> List[dict]:
    """Rows in ascending date order on ``field``; unparseable rows keep their relative
    order and go last; stable on ties. Returns the SAME list object when the field is
    absent or not a date column (nothing is ever silently reordered)."""
    if not rows or not field:
        return rows
    values = [r.get(field) for r in rows]
    if not is_date_column(values):
        return rows
    order = slash_order(values)
    keyed = [(date_key(v, order), i, r) for i, (v, r) in enumerate(zip(values, rows))]
    keyed.sort(key=lambda t: (t[0] is None, t[0] or (0, 0, 0, 0, 0, 0), t[1]))
    return [r for _, _, r in keyed]


# ── Shape naming (Teradata FORMAT) ─────────────────────────────────────────────
# `date_key` answers "what date is this?"; `date_shape` answers "what FORMAT would a
# Teradata CAST need to read it?" — the question the preflight text-date guard asks of
# a sampled value. Only shapes a `CAST(col AS DATE FORMAT '…')` can express are named;
# a quarter or a bare month has no FORMAT and reports None.
_ISO_SHAPE = "YYYY-MM-DD"
_SHAPE_FORMS = [
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), lambda m, o: _ISO_SHAPE),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?"), lambda m, o: "YYYY-MM-DDBHH:MI:SS"),
    (re.compile(r"^(\d{4})/(\d{2})/(\d{2})$"), lambda m, o: "YYYY/MM/DD"),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), lambda m, o: "YYYYMMDD" if 1800 <= int(m[1]) <= 2200 else None),
    (re.compile(rf"^(\d{{2}})([-\s/.]){_MON_RE}\2(\d{{2}}|\d{{4}})$", re.I),
     lambda m, o: f"DD{m[2]}MMM{m[2]}{'YYYY' if len(m[4]) == 4 else 'YY'}"),
    (re.compile(r"^(\d{2})([/.-])(\d{2})\2(\d{2}|\d{4})$"),
     lambda m, o: (f"DD{m[2]}MM{m[2]}" if o == "DMY" else f"MM{m[2]}DD{m[2]}") + ("YYYY" if len(m[4]) == 4 else "YY")),
]


def date_shape(value, order: str = "MDY") -> Optional[str]:
    """The Teradata FORMAT string that reads ``value`` (``'31-OCT-2023'`` → ``'DD-MMM-YYYY'``,
    ``'2023-10-31'`` → ``'YYYY-MM-DD'``, ``'10/31/2023'`` → ``'MM/DD/YYYY'``), or None when
    the text is not a date or its shape has no FORMAT."""
    if value is None:
        return None
    s = " ".join(str(value).split())
    if not s:
        return None
    for rx, build in _SHAPE_FORMS:
        m = rx.match(s)
        if m:
            return build(m, order)
    return None


def column_shape(values: Iterable, sample: int = 60) -> Optional[str]:
    """ONE Teradata FORMAT that reads every non-empty sampled value, else None (mixed
    shapes or none recognised — nothing is asserted about a column the evidence does
    not settle)."""
    vals = [v for v in values if v is not None and " ".join(str(v).split())]
    if not vals:
        return None
    vals = vals[:sample]
    order = slash_order(vals)
    shapes = {date_shape(v, order) for v in vals}
    if len(shapes) != 1:
        return None
    return next(iter(shapes))


def is_iso_shape(shape: Optional[str]) -> bool:
    """Only an ISO-ordered text (year, month, day, zero-padded) sorts and compares as a
    date does — the one shape on which a string comparison is not a defect."""
    return bool(shape) and shape.startswith(_ISO_SHAPE)


_FMT_TOKENS = [("YYYY", "%Y"), ("YY", "%y"), ("MMM", "%b"), ("MM", "%m"), ("DD", "%d"),
               ("HH", "%H"), ("MI", "%M"), ("SS", "%S"), ("B", " ")]
_FMT_TOKEN_RE = re.compile("|".join(re.escape(t) for t, _ in _FMT_TOKENS))


_RX_TOKENS = [("YYYY", "[0-9]{4}"), ("YY", "[0-9]{2}"), ("MMM", "[A-Za-z]{3}"), ("MM", "[0-9]{2}"),
              ("DD", "[0-9]{2}"), ("HH", "[0-9]{2}"), ("MI", "[0-9]{2}"), ("SS", "[0-9]{2}"), ("B", " ")]
_RX_TOKEN_RE = re.compile("|".join(re.escape(t) for t, _ in _RX_TOKENS) + "|.")


def format_regex(fmt: Optional[str]) -> Optional[str]:
    """The anchored pattern a value must match to be readable in ``fmt``
    (``'DD-MMM-YYYY'`` → ``'^[0-9]{2}-[A-Za-z]{3}-[0-9]{4}$'``). Teradata's TRYCAST does not
    accept a FORMAT phrase (Error 3706, measured), so a text date that may hold non-date
    values is read through a CASE guarded by this pattern instead."""
    if not fmt:
        return None
    table = dict(_RX_TOKENS)

    def _lit(ch: str) -> str:   # only what a regex reads specially; a hyphen or slash stays readable
        return ch if (ch.isalnum() or ch in "-/: ") else "\\" + ch
    body = _RX_TOKEN_RE.sub(lambda m: table.get(m.group(0), _lit(m.group(0))), str(fmt).strip().upper())
    return "^" + body + "$"


def guarded_cast(col: str, fmt: str) -> str:
    """``CAST(CASE WHEN REGEXP_SIMILAR(TRIM(col), pattern, 'i') = 1 THEN TRIM(col) END AS DATE
    FORMAT fmt)`` — a value outside the shape reads as NULL instead of failing the whole
    statement with Error 2666. The constructors' probes use it on a bounded sample (it is
    the definition of "unparseable"); what the MODEL is told to write comes from
    ``date_read_form`` below, because this form is the slow one — measured 2026-09-05 on a
    12.35M-row table: past the 110 s endpoint ceiling where a plain CAST took 18 s. The
    fallback of ``safe_cast`` for shapes it cannot rebuild."""
    return (f"CAST(CASE WHEN REGEXP_SIMILAR(TRIM({col}), '{format_regex(fmt)}', 'i') = 1 "
            f"THEN TRIM({col}) END AS DATE FORMAT '{fmt}')")


def format_to_strftime(fmt: Optional[str]) -> Optional[str]:
    """A Teradata FORMAT as the strftime pattern sqlglot reports for a
    ``CAST(x AS DATE FORMAT '…')`` (``'DD-MMM-YYYY'`` → ``'%d-%b-%Y'``)."""
    if not fmt:
        return None
    table = dict(_FMT_TOKENS)
    return _FMT_TOKEN_RE.sub(lambda m: table[m.group(0)], str(fmt).strip().upper())


# ── Which columns are text-stored dates ─────────────────────────────────────────
# ONE predicate for every consumer: the KG enrichment's warning line, the preflight
# guard's recorded column list, and every constructor's source validation.
_DATE_NAME_RE = re.compile(r"(^|[^a-z])(date|datetime|timestamp|dt|dts)([^a-z]|$)|date$|_dt$|_dts$", re.IGNORECASE)
#: English words that END in "date" without being one — a column called `update` or
#: `candidate` is not a date; `INCDATE` / `ARRDATE` (identifier style) are.
_NOT_DATE_WORDS = {
    "update", "validate", "invalidate", "candidate", "mandate", "sedate", "predate", "postdate",
    "backdate", "outdate", "antedate", "liquidate", "consolidate", "accommodate", "intimidate",
    "elucidate", "dilapidate", "inundate", "depredate", "oxidate", "elongate", "delegate",
}
_DATE_TYPES = ("DATE", "TIMESTAMP", "DATETIME", "TIME")
#: Deployed types on which a numeric comparison is an implicit CAST.
_CHAR_TYPES = ("CHAR", "VARCHAR", "CLOB", "CV", "CF", "TEXT", "STRING")
_CHAR_WIDTH_RE = re.compile(r"\((\d+)\)")
#: The narrowest text a calendar date fits in (`20231031`, `10/31/23`). A one- or
#: two-character column whose description merely MENTIONS a date (`REPORT DATE
#: INDICATOR`, `INCIDENT DATE HOUR`) holds a flag or an hour, not a date.
MIN_DATE_WIDTH = 8


def looks_like_date_attr(props: dict) -> bool:
    """Does this attribute hold a DATE? Decided from what the product declares — the
    declared catalog type, the column's own name, or its description saying 'date'."""
    props = props or {}
    declared = str(props.get("declared_type") or "").strip().upper()
    if declared and any(declared.startswith(t) for t in _DATE_TYPES):
        return True
    for key in ("label", "column", "name", "title"):
        v = str(props.get(key) or "")
        if not v or v.strip().lower() in _NOT_DATE_WORDS:
            continue
        if _DATE_NAME_RE.search(v):
            return True
    desc = str(props.get("description") or "")
    return bool(re.search(r"\bdate\b", desc, re.IGNORECASE))


def is_char_type(deployed) -> bool:
    """Is the column deployed as characters, whatever the catalog declares?"""
    t = str(deployed or "").strip().upper()
    return bool(t) and any(t.startswith(c) or t == c for c in _CHAR_TYPES)


def is_text_date_attr(props: dict) -> bool:
    """A DATE deployed as characters WIDE ENOUGH to hold one."""
    props = props or {}
    dep = props.get("deployed_type")
    if not is_char_type(dep) or not looks_like_date_attr(props):
        return False
    m = _CHAR_WIDTH_RE.search(str(dep))
    return not m or int(m.group(1)) >= MIN_DATE_WIDTH


# ── the fields of a shape: what a text slice can legitimately pick out ──────────
_FIELD_TOKEN_RE = re.compile(r"YYYY|YY|MMM|MM|DD|HH|MI|SS")
_MONTH_NAMES = "JANFEBMARAPRMAYJUNJULAUGSEPOCTNOVDEC"


def shape_fields(fmt) -> List[Tuple[str, int, int]]:
    """The fields of a FORMAT with their 1-based text positions — ``'DD-MMM-YYYY'`` →
    ``[('DD', 1, 2), ('MMM', 4, 3), ('YYYY', 8, 4)]``. Every token is fixed-width, so the
    positions are exact for every value in the shape."""
    s = str(fmt or "").strip().upper()
    return [(m.group(0), m.start() + 1, m.end() - m.start()) for m in _FIELD_TOKEN_RE.finditer(s)]


def shape_width(fmt) -> int:
    return len(str(fmt or "").strip())


def slice_aligned(fmt, start, length=None) -> Optional[bool]:
    """Does ``SUBSTR(value, start, length)`` on text in ``fmt`` pick out WHOLE fields?

    True when the slice begins on a field's first character and ends on a field's last —
    spanning consecutive fields and their separators is fine (``SUBSTR(x, 4, 8)`` on
    DD-MMM-YYYY is ``OCT-2023``, a month-year); False when it cuts a field or ends on a
    separator (``SUBSTR(x, 1, 7)`` is ``31-OCT-``: day-and-month, not a period). None when
    the shape or the slice is unknown. A length of None reads to the end of the text."""
    fields = shape_fields(fmt)
    if not fields or not isinstance(start, int) or start < 1:
        return None
    if length is not None and (not isinstance(length, int) or length < 1):
        return None
    width = shape_width(fmt)
    end = width if length is None else min(start + length - 1, width)
    starts = {s for _, s, _ in fields}
    ends = {s + l - 1 for _, s, l in fields}
    return start in starts and end in ends


def text_period_forms(col: str, fmt) -> Dict[str, str]:
    """The cheap, CORRECT text forms a proven shape admits without converting a single
    row: one slice per field, the month-year slice, and — for a month NAME — the
    expression that turns it into a number (``JAN`` → 1) so it can be ordered.
    Empty for a shape with no fields."""
    fields = shape_fields(fmt)
    if not fields:
        return {}
    pos = {tok: (s, l) for tok, s, l in fields}
    out: Dict[str, str] = {}
    year = pos.get("YYYY") or pos.get("YY")
    month = pos.get("MM") or pos.get("MMM")
    day = pos.get("DD")
    if year:
        out["year"] = f"SUBSTR({col}, {year[0]}, {year[1]})"
    if month:
        out["month"] = f"SUBSTR({col}, {month[0]}, {month[1]})"
        if "MMM" in pos:
            out["month_number"] = (f"(POSITION(UPPER(SUBSTR({col}, {month[0]}, {month[1]})) IN "
                                   f"'{_MONTH_NAMES}') + 2) / 3")
    if day:
        out["day"] = f"SUBSTR({col}, {day[0]}, {day[1]})"
    if year and month:
        a, b = sorted([year, month])
        if b[0] <= a[0] + a[1] + 1:      # adjacent (at most one separator between them)
            out["month_year"] = f"SUBSTR({col}, {a[0]}, {b[0] + b[1] - a[0]})"
    return out


def text_period_advice(col: str, fmt) -> str:
    """One sentence naming the cheap text forms for ``col`` in ``fmt`` — shared by the
    preflight refusal and the ontology enrichment so the two never disagree. Measured on
    the reference warehouse (12.35M-row Incident, 2026-09-05): a month bucket by text slice
    with a text year filter ran in 15 s where the row-by-row guarded conversion in both
    SELECT and WHERE exceeded the endpoint's 110 s ceiling."""
    forms = text_period_forms(col, fmt)
    if not forms:
        return ""
    parts = []
    if "year" in forms:
        parts.append(f"year = {forms['year']} (a year filter is {forms['year']} = '2023')")
    if "month" in forms:
        if "month_number" in forms:
            parts.append(f"month name = {forms['month']}, month number = {forms['month_number']}")
        else:
            parts.append(f"month = {forms['month']}")
    if "month_year" in forms:
        iso = is_iso_shape(fmt)
        parts.append(f"month-year = {forms['month_year']}" + (" (sorts chronologically)" if iso else
                                                              " (order it by year then month number, not as text)"))
    return ("For a period bucket or a year/month filter the text itself is correct on this shape and "
            "costs no per-row conversion: " + "; ".join(parts) +
            ". Use the CAST only for a real date comparison, a date range or a chronological ORDER BY "
            "of individual dates — it converts every scanned row and can exceed the query time ceiling on a large table.")


_MONTH_MAP = ("CASE UPPER({m}) WHEN 'JAN' THEN '01' WHEN 'FEB' THEN '02' WHEN 'MAR' THEN '03' "
              "WHEN 'APR' THEN '04' WHEN 'MAY' THEN '05' WHEN 'JUN' THEN '06' WHEN 'JUL' THEN '07' "
              "WHEN 'AUG' THEN '08' WHEN 'SEP' THEN '09' WHEN 'OCT' THEN '10' WHEN 'NOV' THEN '11' "
              "WHEN 'DEC' THEN '12' END")


def length_guarded_cast(col: str, fmt: str) -> str:
    """``CAST(CASE WHEN CHAR_LENGTH(TRIM(col)) = <width> THEN TRIM(col) END AS DATE FORMAT fmt)``
    — skips blanks and values of the wrong length at the cost of a plain CAST (measured:
    19.6 s vs 17.8 s plain over 12.35M rows). For a column whose sampled values ALL read
    in the shape: a same-length non-date would still raise 2666, and none was seen."""
    w = shape_width(fmt)
    return f"CAST(CASE WHEN CHAR_LENGTH(TRIM({col})) = {w} THEN TRIM({col}) END AS DATE FORMAT '{fmt}')"


def safe_cast(col: str, fmt: str) -> str:
    """The regex-free SAFE read of a text date that holds non-date values: rebuild the ISO
    text from the shape's fields and ``TRYCAST`` it — junk, a bad month name, '31-FEB',
    blanks all read as NULL (TRYCAST semantics measured live). Costs ~3× a plain CAST
    (52 s vs 18 s over 12.35M rows) where the regex guard exceeded 170 s. Falls back to
    ``guarded_cast`` for a shape it cannot rebuild (two-digit year, a time part)."""
    fields = {tok: (s, l) for tok, s, l in shape_fields(fmt)}
    if "YYYY" not in fields or "DD" not in fields or not ("MM" in fields or "MMM" in fields) \
            or any(t in fields for t in ("YY", "HH", "MI", "SS")):
        return guarded_cast(col, fmt)
    t = f"TRIM({col})"
    if is_iso_shape(fmt) and str(fmt).strip().upper() == _ISO_SHAPE:
        return f"TRYCAST({t} AS DATE)"
    y = f"SUBSTR({t}, {fields['YYYY'][0]}, 4)"
    d = f"SUBSTR({t}, {fields['DD'][0]}, 2)"
    if "MM" in fields:
        m = f"SUBSTR({t}, {fields['MM'][0]}, 2)"
    else:
        m = _MONTH_MAP.format(m=f"SUBSTR({t}, {fields['MMM'][0]}, 3)")
    return f"TRYCAST({y} || '-' || {m} || '-' || {d} AS DATE)"


def date_read_form(col: str, fmt: str, unparseable=0.0, sampled: bool = False) -> str:
    """THE read of a text-stored date the model is told to write — ONE chooser for the
    ontology enrichment, the preflight refusal and the mechanical rewrite, ranked by the
    measured cost on the reference warehouse (12.35M rows, 2026-09-05):
      * a measured share of non-date values → ``safe_cast`` (regex-free TRYCAST, ~52 s);
      * a SAMPLED clean share (nothing outside the shape seen, but not proven) →
        ``length_guarded_cast`` (~20 s, skips blanks and short junk);
      * an exact clean share → a plain CAST (~18 s).
    The regex guard (>170 s — past the endpoint's 110 s ceiling) is never recommended."""
    try:
        bad = float(unparseable or 0.0)
    except (TypeError, ValueError):
        bad = 0.0
    if bad > 0.0:
        return safe_cast(col, fmt)
    if sampled:
        return length_guarded_cast(col, fmt)
    return f"CAST({col} AS DATE FORMAT '{fmt}')"
