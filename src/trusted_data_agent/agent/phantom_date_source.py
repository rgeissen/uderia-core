"""A date range the planner reads from a table that does not exist.

After a ``TDA_DateRange`` phase, a planner writing raw SQL sometimes treats the phase's OUTPUT
as a database object instead of using the dates it holds::

    WHERE SaleDate BETWEEN (SELECT MIN(date) FROM (SELECT CAST(date AS DATE) AS date
                                                   FROM TDA_DateRange_result) AS d)
                       AND (SELECT MAX(date) FROM … TDA_DateRange_result …)
    WHERE SaleDate >= (SELECT MIN(v) FROM (SELECT CAST(value AS DATE) AS v
                                          FROM TABLE(CAST(? AS JSON))) AS d)
    WHERE SaleDate IN (SELECT date FROM result_of_phase_2)

Seen on the fusion bench, 2026-09-24 — two different invented shapes for the same question on
two runs, both ending "I cannot provide the total". No database holds such an object, and a
query tool binds no ``?``. The intent is unambiguous: the lower bound is the range's start and
the upper its end. The turn already knows both, so the rewrite is mechanical and costs no LLM
round.

Pure: text in, text out. Only a subquery that reads from a PHANTOM source is touched — a
``TDA_*`` name, a phase reference, or a ``?`` bind — so a real ``MIN(order_date)`` subquery over
a real table is never rewritten. Anything this module does not recognise is left exactly as
written.
"""
import re
from typing import List, Optional, Tuple

#: A source no database holds: a framework tool's name, a plan phase's result, a ``?`` bind.
_PHANTOM = re.compile(r"\bTDA_\w+|\bresult_of_phase_\d+\b|\bphase_\d+_result\b|\?", re.IGNORECASE)
#: …read FROM (or JOINed), possibly through any chain of wrapping calls — `TABLE(`, `CAST(`,
#: `UNNEST(` (fusion bench run 4), whatever the model reaches for next.
_FROM_PHANTOM = re.compile(r"\b(?:FROM|JOIN)\b[\s(]*(?:[A-Za-z_][\w.]*\s*\(\s*)*(\bTDA_\w+|\bresult_of_phase_\d+\b|\bphase_\d+_result\b|\?)",
                           re.IGNORECASE)


def _balanced(sql: str, open_at: int) -> Optional[int]:
    """Index of the ``)`` closing the ``(`` at ``open_at``, skipping string literals."""
    depth, i, n = 0, open_at, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = sql.find("'", i + 1)
            while j != -1 and j + 1 < n and sql[j + 1] == "'":
                j = sql.find("'", j + 2)
            if j == -1:
                return None
            i = j
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _mask(sql: str) -> str:
    """``sql`` with every string literal's CONTENT blanked (same length), so no pattern ever
    matches text a user quoted — offsets stay valid for editing the original."""
    out, i, n = list(sql), 0, len(sql)
    while i < n:
        if sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and not (j + 1 < n and sql[j + 1] == "'"):
                    break
                j += 2 if sql[j] == "'" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _subqueries(sql: str) -> List[Tuple[int, int]]:
    """Every ``( SELECT … )`` span in ``sql``, outermost first."""
    out = []
    for m in re.finditer(r"\(\s*SELECT\b", sql, re.IGNORECASE):
        end = _balanced(sql, m.start())
        if end is not None:
            out.append((m.start(), end))
    return out


#: A date GENERATOR the model invented in place of the range (`FROM TABLE(TD_SYSFNLIB.
#: TD_RANGE_DATE(…))`, fusion bench run 3). Only read inside a MIN/MAX or IN subquery, and the
#: executor only asks when a TDA_DateRange ran this turn — there, a bound over a generated
#: table can only mean the range's own bound.
_FROM_GENERATOR = re.compile(r"\b(?:FROM|JOIN)\b[\s(]*TABLE\s*\(", re.IGNORECASE)


def _reads_phantom(body: str) -> bool:
    return bool(_FROM_PHANTOM.search(body) or _FROM_GENERATOR.search(body))


def rewrite(sql: str, start: str, end: str) -> Tuple[str, List[str]]:
    """``(sql, changes)``. ``changes`` is empty when nothing was recognised — the SQL is then
    returned byte-identical."""
    if (not isinstance(sql, str) or not start or not end
            or not (_PHANTOM.search(_mask(sql)) or _FROM_GENERATOR.search(_mask(sql)))):
        return sql, []
    s_lit, e_lit = f"DATE '{start}'", f"DATE '{end}'"
    changes: List[str] = []

    # 1. `col IN (SELECT … FROM <phantom>)` — membership in the range IS the range.
    while True:
        m = None
        masked = _mask(sql)
        for mm in re.finditer(r"([A-Za-z_][\w.\"]*)\s+IN\s*(\(\s*SELECT\b)", masked, re.IGNORECASE):
            close = _balanced(sql, mm.start(2))
            if close is not None and _reads_phantom(masked[mm.start(2):close + 1]):
                m = (mm, close)
                break
        if m is None:
            break
        mm, close = m
        col = mm.group(1)
        sql = sql[:mm.start()] + f"{col} BETWEEN {s_lit} AND {e_lit}" + sql[close + 1:]
        changes.append(f"{col} IN (… phantom date source …) → {col} BETWEEN {s_lit} AND {e_lit}")

    # 2. A scalar MIN/MAX subquery over a phantom source → the range's start/end.
    while True:
        target = None
        masked = _mask(sql)
        for a, b in _subqueries(masked):
            body = masked[a:b + 1]
            agg = re.match(r"\(\s*SELECT\s+(MIN|MAX)\s*\(", body, re.IGNORECASE)
            if agg and _reads_phantom(body):
                target = (a, b, agg.group(1).upper())
                break
        if target is None:
            break
        a, b, fn = target
        lit = s_lit if fn == "MIN" else e_lit
        sql = sql[:a] + lit + sql[b + 1:]
        changes.append(f"(SELECT {fn}(…) FROM <phantom>) → {lit}")

    return sql, changes


def still_phantom(sql: str) -> bool:
    """True when ``sql`` still reads from a phantom source after ``rewrite`` (a shape it does not
    recognise) — the caller may then name the problem instead of sending it to a database."""
    return isinstance(sql, str) and bool(_FROM_PHANTOM.search(_mask(sql)))


def phantom_names(sql: str) -> List[str]:
    """The phantom sources ``sql`` reads FROM/JOIN, in order, de-duplicated (``?`` shown as
    ``a ? bind``). Empty when it reads none."""
    if not isinstance(sql, str):
        return []
    out: List[str] = []
    for m in _FROM_PHANTOM.finditer(_mask(sql)):
        name = "a ? bind" if m.group(1) == "?" else m.group(1)
        if name not in out:
            out.append(name)
    return out


def refusal_message(sql: str) -> Optional[str]:
    """Recovery-shaped refusal for a statement that still reads from a phantom source, or
    ``None``. No database holds such an object, so sending it costs a round trip and an error
    the model then has to read; naming it here costs neither."""
    names = phantom_names(sql)
    if not names:
        return None
    return (f"the statement reads FROM {', '.join(names)}, which is not a table — it is a plan "
            f"step's output (or an unbound parameter), and no database holds it. NEXT STEP: write "
            f"the values that step produced directly into the statement — dates as DATE "
            f"'YYYY-MM-DD' literals (e.g. col BETWEEN DATE '<start>' AND DATE '<end>' using the "
            f"date-range step's start_date and end_date), lists as IN (...) literals.")


#: A BOUND of the range written as a name — `'result_of_phase_2_start'`, `result_of_phase_2.end_date`,
#: `TDA_DateRange.start` (fusion bench run 4, 2026-09-24: the quoted form reached the database as
#: a string and the turn could not answer). Only start/end words are read — any other suffix is
#: not a bound, and is left as written.
_BOUND_REF = re.compile(
    r"'?\b(?:result_of_phase_\d+|phase_\d+_result|TDA_DateRange(?:_result)?)[._]"
    r"(start|end|from|to|begin|lower|upper)(?:_date)?\b'?",
    re.IGNORECASE)
_START_WORDS = {"start", "from", "begin", "lower"}


def rewrite_bound_refs(sql: str, start: str, end: str) -> Tuple[str, List[str]]:
    """``(sql, changes)``: every named range bound → ``DATE '<start>'`` / ``DATE '<end>'``,
    quoted or not. Byte-identical with no changes when there is none."""
    if not isinstance(sql, str) or not start or not end:
        return sql, []
    changes: List[str] = []

    def _sub(m):
        quoted_l, quoted_r = m.group(0).startswith("'"), m.group(0).endswith("'")
        if quoted_l != quoted_r:          # half a quote belongs to something else — leave it
            return m.group(0)
        lit = f"DATE '{start}'" if m.group(1).lower() in _START_WORDS else f"DATE '{end}'"
        changes.append(f"{m.group(0)} → {lit}")
        return lit

    out = _BOUND_REF.sub(_sub, sql)
    return (out, changes) if changes else (sql, [])
