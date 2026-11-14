"""Mechanical SQL dialect corrections (pure, no I/O).

Teradata has no LIMIT clause, and its row-limiting ``TOP n`` belongs directly after
SELECT — a model that writes ``… ORDER BY x LIMIT 30`` or ``… ORDER BY x TOP 30`` gets
Error 3706 and, without this module, spends an LLM round to learn the placement.  The
rewrite here is the same class the product compiler's dialect seam already owns (TOP
form, never LIMIT): it moves the SAME n to the TOP position and strips the trailing
clause.  Syntax widens, meaning never.  Anything non-mechanical — no trailing clause, a
LIMIT with OFFSET, DISTINCT (Teradata refuses TOP with DISTINCT), an existing TOP, a
multi-statement request — returns None so the caller's LLM path judges it.

Two consumers share it so they cannot drift: the Optimize path's
``TeradataLimitStrategy`` (post-error self-correction) and the ReAct tool wrapper's
one-shot retry (``agent/tool_enforcement.py``).

The second correction here is the RESERVED ALIAS (Error 3707, "expected something like
a name … between the 'AS' keyword and the 'Month' keyword"): a model names a column after
a Teradata reserved word, and the fix is purely mechanical — quote the identifier
wherever the statement uses it as one (``AS "Month"``, ``ORDER BY "Month"``), and nothing
else. The error names the word, so no reserved-word list is kept here; a keyword the
statement uses AS a keyword (``EXTRACT(MONTH FROM …)``) is never an identifier and stays
untouched. Same two consumers.
"""
from __future__ import annotations

import re
from typing import Optional

#: The driver's message for this class of error. Broad on purpose: the 3706 text does
#: not always name LIMIT/TOP, and ``teradata_top_rewrite`` reads the SQL itself.
TERADATA_SYNTAX_ERROR_RE = re.compile(r"\b3706\b|Syntax error", re.IGNORECASE)

_LIMIT_TAIL_RE = re.compile(r"\s+LIMIT\s+(\d+)\s*;?\s*$", re.IGNORECASE)
_TOP_TAIL_RE = re.compile(r"\s+TOP\s+(\d+)\s*;?\s*$", re.IGNORECASE)
_SELECT_RE = re.compile(r"^(\s*SELECT\s+)(DISTINCT\s+)?", re.IGNORECASE)


def teradata_top_rewrite(sql) -> Optional[str]:
    """``SELECT TOP n …`` for a statement that ends in ``LIMIT n`` or a misplaced
    ``TOP n``; None when the rewrite would not be purely mechanical."""
    if not isinstance(sql, str) or not sql.strip():
        return None
    m = _LIMIT_TAIL_RE.search(sql) or _TOP_TAIL_RE.search(sql)
    if not m:
        return None
    n = m.group(1)
    head = sql[:m.start()]
    if ";" in head:
        return None                      # more than one statement — not mechanical
    if re.search(r"\bTOP\s+\d+", head, re.IGNORECASE):
        return None                      # already carries a TOP — the LLM judges it
    sm = _SELECT_RE.match(head)
    if not sm or sm.group(2):
        return None                      # not a SELECT, or DISTINCT
    if _outer_block_has_qualify(head):
        return None                      # TOP + QUALIFY in one query block is Error 6916
    return sm.group(1) + f"TOP {n} " + head[sm.end():]


def _outer_block_has_qualify(sql: str) -> bool:
    """True when the statement's OUTER query block carries a QUALIFY. Teradata refuses TOP
    and QUALIFY in the same block ("[Error 6916] Top N option is not supported with QUALIFY
    clause" — verified live, 20.00.22.31), so moving a row limit to TOP there trades one error
    for another. A QUALIFY inside a derived table is a different block and is accepted with a
    TOP outside it. Folding the limit into the QUALIFY is not an equivalent statement (both
    window functions are computed before the filter), so there is no mechanical rewrite."""
    code = _sql_code_mask(sql)
    depth = 0
    for m in re.finditer(r"[()]|\bQUALIFY\b", code, re.IGNORECASE):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            return True
    return False


def looks_like_teradata_syntax_error(text) -> bool:
    return bool(TERADATA_SYNTAX_ERROR_RE.search(str(text or "")))


#: Error 3110 — the database aborted the statement at the connection's request-time
#: ceiling (the MCP endpoint's ``request_timeout``, 110 s under the platform's 120 s), and
#: reports it as "aborted by the user" though nobody cancelled it. Observed on production
#: 2026-09-05: the model resent the identical statement and lost another 110 s.
TERADATA_ABORT_RE = re.compile(r"\b3110\b|aborted by the user", re.IGNORECASE)


def db_abort_hint(text) -> Optional[str]:
    """The recovery for a statement the database aborted at its time ceiling, or None
    when ``text`` is not that error. Both engines' tool-error seams append it, so the
    model learns that a verbatim resend cannot succeed and what changes the outcome."""
    if not TERADATA_ABORT_RE.search(str(text or "")):
        return None
    return ("This statement ran past the database's time ceiling and was aborted there (Error 3110 reads "
            "'aborted by the user', but nobody cancelled it) — a verbatim resend will be aborted again. "
            "NEXT STEP: make the scan cheaper — filter and bucket on the stored TEXT of a text-stored date "
            "(a year or a month is a fixed slice of it) instead of converting every row, drop per-row "
            "functions from the WHERE clause, or narrow the rows — then retry ONCE; if it cannot be narrowed, "
            "say the figure could not be computed within the budget rather than estimating it.")


#: Error 3706 / 3707 for a reserved word used as a name — the driver names the word as "the
#: '<word>' keyword". Observed on production and locally 2026-09-05 (`… END AS Month`), and
#: measured on Teradata 20.00 (2026-10-01) in the other positions a name takes: a table alias
#: (`SELECT input.x … input` → 3706 "between the 'SELECT' keyword and the 'input' keyword"), a
#: qualified column (`t.Count` → 3707 "between '.' and the 'Count' keyword") and a bare column
#: (`SELECT Threshold` → 3706). Only those three anchors — after `AS`, after `SELECT`, after
#: `.` — are read: the same wording after `(` names a word where a FUNCTION was expected
#: (production f93bef6d: `TABLE(TD_SYSFNLIB.DateRange(…))` → "between '(' and the 'TD_SYSFNLIB'
#: keyword"), which is not a reserved word and must reach the LLM correction. A message naming
#: a word that is not a name in the statement (a misplaced real keyword) is declined by the
#: rewrite: it quotes only words the parser reads as identifiers.
TERADATA_RESERVED_ALIAS_RE = re.compile(
    r"\b370[67]\b.*?between (?:the '(?:AS|SELECT)' keyword|'\.') and the '([A-Za-z_][A-Za-z0-9_]*)' keyword",
    re.IGNORECASE | re.DOTALL)


def reserved_alias_from_error(text) -> Optional[str]:
    """The reserved word an Error 3706/3707 names in a name position, else None (the name is
    historical: the first form seen was an alias)."""
    m = TERADATA_RESERVED_ALIAS_RE.search(str(text or ""))
    return m.group(1) if m else None


def _quote_identifier_textually(sql: str, word: str) -> Optional[str]:
    """Fallback when sqlglot is unavailable or cannot parse: quote the word after ``AS``
    and after ``ORDER/GROUP/PARTITION BY`` — the two positions an alias is used in."""
    as_re = re.compile(r"(\bAS\s+)(" + re.escape(word) + r")\b", re.IGNORECASE)
    if not as_re.search(sql):
        return None
    out = as_re.sub(lambda m: m.group(1) + '"' + m.group(2) + '"', sql)
    by_re = re.compile(r"(\b(?:ORDER|GROUP|PARTITION)\s+BY\s+)(" + re.escape(word) + r")\b", re.IGNORECASE)
    return by_re.sub(lambda m: m.group(1) + '"' + m.group(2) + '"', out)


def teradata_reserved_alias_rewrite(sql, error_text) -> Optional[str]:
    """The statement with the reserved word the error names quoted wherever the statement
    uses it as an IDENTIFIER (the alias and every reference to it); None when the error is
    not that class, the word does not occur as an identifier, the request holds more than
    one statement, or the result does not re-parse. Meaning never changes — a delimited
    identifier is the same name, spelled so the parser reads it as one."""
    word = reserved_alias_from_error(error_text)
    if not word or not isinstance(sql, str) or not sql.strip():
        return None
    if ";" in sql.strip().rstrip(";"):
        return None                                   # more than one statement — not mechanical
    try:
        import sqlglot
        from sqlglot import exp
        stmt = sqlglot.parse_one(sql, read="teradata")
        hit = False
        for ident in stmt.find_all(exp.Identifier):
            if ident.name.lower() == word.lower() and not ident.quoted:
                ident.set("quoted", True)
                hit = True
        if not hit:
            return None
        from trusted_data_agent.agent.preflight.textdates import _restore_charlen, _restore_substr
        _restore_substr(stmt)
        _restore_charlen(stmt)
        out = stmt.sql(dialect="teradata")
        sqlglot.parse_one(out, read="teradata")       # must re-parse clean
        return out if out.strip() != sql.strip() else None
    except Exception:
        return _quote_identifier_textually(sql, word)


# ── an ISO date written as a STRING, compared with a TIMESTAMP (Error 6760) ─────────────────
#
# `WHERE SaleDate >= '2026-08-25'` on a TIMESTAMP column: Teradata converts the string with the
# timestamp's format, finds no time part and answers "[Error 6760] Invalid timestamp". The
# statement is right in substance — the model had the right dates (fusion bench, 2026-09-24:
# three runs, the correction LLM then re-ran TDA_CurrentDate and the answer reported "30 dates
# analysed" instead of a sales count). A DATE literal compares with a TIMESTAMP as intended, so
# the fix is mechanical: `'2026-08-25'` → `DATE '2026-08-25'`.

_ISO_DATE_STRING = re.compile(r"(?<![\w'])(?<!DATE )(?<!DATE)(?<!TIMESTAMP )'(\d{4}-\d{2}-\d{2})'", re.IGNORECASE)


def looks_like_invalid_timestamp(text) -> bool:
    """True for Teradata's "the string is not a timestamp" error (6760)."""
    t = str(text or "")
    return bool(re.search(r"\bError 6760\b|\bInvalid timestamp\b", t, re.IGNORECASE))


_BARE_ISO_DATE = re.compile(r"(?<![\w.'\-])(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?![\w\-])")


def _sql_code_mask(sql: str) -> str:
    """``sql`` with string literals, quoted identifiers and comments blanked (same length), so a
    pattern only ever matches SQL code."""
    out, i, n = list(sql), 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == c and not (j + 1 < n and sql[j + 1] == c):
                    break
                j += 2 if sql[j] == c else 1
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def bare_iso_date_rewrite(sql, typed: bool = True) -> Optional[str]:
    """``sql`` with every UNQUOTED ``YYYY-MM-DD`` written as ``DATE 'YYYY-MM-DD'``; None when
    there is none, or the request holds more than one statement.

    Unquoted, ``2026-08-25`` is arithmetic in every SQL dialect (2026 − 8 − 25 = 1993) — the
    statement either errors or, against a numeric column, silently compares with 1993. Nobody
    writing a calendar date means that, so the fix is mechanical and needs no error to trigger
    it. Only a valid month/day is read as a date; text inside quotes or comments is never
    touched. ``typed`` writes the ANSI ``DATE 'YYYY-MM-DD'`` literal (Teradata, PostgreSQL, MySQL,
    DuckDB); ``typed=False`` writes the quoted string SQLite compares as ISO text."""
    if not isinstance(sql, str) or not sql.strip() or ";" in sql.strip().rstrip(";"):
        return None
    masked = _sql_code_mask(sql)
    spans = [(m.start(), m.end(), m.group(0)) for m in _BARE_ISO_DATE.finditer(masked)]
    if not spans:
        return None
    out = sql
    for a, b, lit in reversed(spans):
        out = out[:a] + (f"DATE '{lit}'" if typed else f"'{lit}'") + out[b:]
    return out


def teradata_iso_date_literal_rewrite(sql) -> Optional[str]:
    """``sql`` with every bare ``'YYYY-MM-DD'`` string literal written as ``DATE 'YYYY-MM-DD'``;
    None when there is none, or the request holds more than one statement. A literal already
    typed (``DATE '…'``, ``TIMESTAMP '…'``) is left alone."""
    if not isinstance(sql, str) or not sql.strip() or ";" in sql.strip().rstrip(";"):
        return None
    out = _ISO_DATE_STRING.sub(lambda m: f"DATE '{m.group(1)}'", sql)
    return out if out != sql else None


# ── Forms other dialects accept and Teradata refuses (pattern-library findings) ──────────
#
# Found while executing Loom's Teradata patterns on a live system (2026-10-01): a statement
# written in another dialect's habits fails for reasons that are purely syntactic. Each rule
# here was checked on Teradata 20.00.24.60 — the original form refused, the rewritten form
# returning the same rows — and each declines whenever the equivalence is not exact:
#
#   ``!=``                       Error 3707          → ``<>`` (identical meaning)
#   ``SELECT *, expr``           Error 3706 "SELECT * must have a FROM clause"
#                                                    → ``SELECT <each FROM source>.*, expr``,
#                                  in FROM order, by alias or by the name exactly as written:
#                                  a bare ``orders.*`` for ``FROM db.orders`` is ambiguous
#                                  (Error 3806), ``db.orders.*`` is not
#   ``DATE_TRUNC('month', x)``   Error 3706          → ``TRUNC(x, 'MM')`` for day / week
#                                  (ISO, Monday) / month / quarter / year. NOT for hour or
#                                  minute: ``TRUNC(ts, 'HH')`` returns the DATE, silently
#                                  dropping the time — those stay for the model to rewrite.
#
# Like the TOP and reserved-alias rewrites, this runs only AFTER Teradata refused the
# statement (a backend that accepts the original never sees it) and is retried once.

_TRUNC_FORMAT = {"day": "DD", "days": "DD", "week": "IW", "weeks": "IW", "month": "MM",
                 "months": "MM", "quarter": "Q", "quarters": "Q", "year": "YYYY", "years": "YYYY"}
_LEADING_STAR_RE = re.compile(
    r"^\s*SELECT\s+(?:DISTINCT\s+)?(?:TOP\s+\d+\s+(?:WITH\s+TIES\s+)?)?(\*)\s*,", re.IGNORECASE)


def _replace_spans(sql: str, spans) -> str:
    out = sql
    for a, b, text in sorted(spans, reverse=True):
        out = out[:a] + text + out[b:]
    return out


def _not_equal_spans(sql: str, code: str):
    return [(m.start(), m.end(), "<>") for m in re.finditer(r"!=", code)]


def _date_trunc_spans(sql: str, code: str):
    spans = []
    for m in re.finditer(r"\bDATE_TRUNC\s*\(", code, re.IGNORECASE):
        unit = re.match(r"\s*'(\w+)'\s*,", sql[m.end():])
        if not unit or unit.group(1).lower() not in _TRUNC_FORMAT:
            continue                                # an hour/minute unit has no TRUNC twin
        start, depth = m.end() + unit.end(), 1
        for j in range(start, len(code)):
            if code[j] == "(":
                depth += 1
            elif code[j] == ")":
                depth -= 1
                if depth == 0:
                    expr = sql[start:j].strip()
                    if expr:
                        spans.append((m.start(), j + 1,
                                      f"TRUNC({expr}, '{_TRUNC_FORMAT[unit.group(1).lower()]}')"))
                    break
    return spans


def _star_sources(sql: str) -> Optional[list]:
    """``['o', 'db.customers']`` — each FROM/JOIN source of the outer SELECT, by alias or by
    the name as written; None when any source cannot be named (an unaliased derived table) or
    the statement does not parse."""
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read="teradata")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(tree, exp.Select):
        return None
    frm = tree.args.get("from") or tree.args.get("from_")
    items = ([frm.this] if frm is not None else []) + [j.this for j in (tree.args.get("joins") or [])]
    names = []
    for it in items:
        alias = it.alias if hasattr(it, "alias") else ""
        if alias:
            names.append(alias)
        elif isinstance(it, exp.Table):
            names.append(".".join(p for p in (it.catalog, it.db, it.name) if p))
        else:
            return None
    return names or None


def _leading_star_span(sql: str, code: str):
    m = _LEADING_STAR_RE.match(code)
    if not m:
        return []
    sources = _star_sources(sql)
    if not sources:
        return []
    return [(m.start(1), m.end(1), ", ".join(f"{s}.*" for s in sources))]


#: `SELECT COUNT(DISTINCT *) [AS a] FROM <one source> [WHERE …]` — Teradata refuses the
#: `DISTINCT *` argument (Error 3706, measured 2026-10-01); the count over a `SELECT DISTINCT *`
#: derived table is the same number and runs. Declined for anything that is not exactly that
#: shape: another select item, a join (a DISTINCT * over a join names a column twice), GROUP BY,
#: HAVING, QUALIFY, ORDER BY, TOP, a set operation — there the meaning of the count is a choice.
_COUNT_DISTINCT_STAR_RE = re.compile(
    r"^\s*SELECT\s+COUNT\s*\(\s*DISTINCT\s*\*\s*\)(\s+(?:AS\s+)?[A-Za-z_]\w*|\s+AS\s+\"[^\"]+\")?"
    r"\s+FROM\s+(.+?)\s*;?\s*$", re.IGNORECASE | re.DOTALL)
_COUNT_DISTINCT_DECLINE = re.compile(
    r"\b(?:JOIN|GROUP\s+BY|HAVING|QUALIFY|ORDER\s+BY|UNION|INTERSECT|EXCEPT|MINUS|SAMPLE)\b",
    re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def _count_distinct_star(sql: str, code: str) -> Optional[str]:
    m = _COUNT_DISTINCT_STAR_RE.match(code)
    if not m:
        return None
    rest_code = m.group(2)
    w = _WHERE_RE.search(rest_code)
    from_part = rest_code[:w.start()] if w else rest_code
    if _COUNT_DISTINCT_DECLINE.search(rest_code) or "," in from_part or "(" in from_part:
        return None                                    # not one plain source — the count is a choice
    alias = (sql[m.start(1):m.end(1)] if m.group(1) else "").rstrip()
    rest = sql[m.start(2):m.end(2)].rstrip().rstrip(";").rstrip()
    return f"SELECT COUNT(*){alias} FROM (SELECT DISTINCT * FROM {rest}) AS d"


def teradata_syntax_rewrite(sql):
    """``(sql, changes)`` with every form above rewritten, or None when none applies (or the
    request holds more than one statement). Text inside quotes and comments is never read
    as code."""
    if not isinstance(sql, str) or not sql.strip() or ";" in sql.strip().rstrip(";"):
        return None
    code = _sql_code_mask(sql)
    counted = _count_distinct_star(sql, code)
    if counted:
        return counted, ["`COUNT(DISTINCT *)` → `COUNT(*)` over `SELECT DISTINCT *`"]
    changes, spans = [], []
    for label, found in (("`!=` → `<>`", _not_equal_spans(sql, code)),
                         ("`DATE_TRUNC` → `TRUNC`", _date_trunc_spans(sql, code)),
                         ("`SELECT *, …` → `SELECT <source>.*, …`", _leading_star_span(sql, code))):
        if found:
            spans += found
            changes.append(label)
    if not spans:
        return None
    out = _replace_spans(sql, spans)
    # A statement written in another dialect's habits often ends in LIMIT too; one retry must
    # fix both, or it fails on the remaining defect and the model pays a round anyway.
    topped = teradata_top_rewrite(out)
    if topped:
        out = topped
        changes.append("trailing `LIMIT n` → `SELECT TOP n`")
    return out, changes
