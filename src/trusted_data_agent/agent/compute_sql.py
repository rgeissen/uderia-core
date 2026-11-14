"""The combine step — ONE read-only SELECT over the turn's result sets (PROP-006 phase 1).

`TDA_Compute` op "sql". Rows that data queries already returned this turn — each registered as a
statement in ``result_sets`` — are loaded into a per-call, in-process DuckDB as tables named
``s1``, ``s2``, … by statement number, and one SELECT runs over them. This is how results from
DIFFERENT data sources are joined or unioned: no single query can reach both.

Everything that can be decided from the data is decided HERE, by code (build brief invariant 9):

* **A2 — no new data access.** Only ``s<N>`` tables (and the statement's own CTEs) may be named;
  anything else is refused before DuckDB sees it. DuckDB additionally runs with external access
  disabled, extension auto-install/-load off and its configuration locked — a second, independent
  wall (a file path, a URL, ``read_csv``, ``COPY``, ``ATTACH``, ``INSTALL`` all fail there too).
* **A8 — complete inputs.** An input the registry cut (``truncated``) or a tool returned partially
  (a ``TOP n`` / ``LIMIT n`` the rows exactly fill) is incomplete; the caller applies the
  operator's policy (refuse by default).
* **A9 — join keys.** Before the join runs, each ``s<a>.x = s<b>.y`` equality is measured on the
  actual rows under a fixed ladder of normalizations; a clear winner is APPLIED (the ON clause is
  rewritten and the change named — 0 LLM rounds); none ⇒ the overlap table is reported so the
  model can judge. Keys repeated on both sides (rows multiply) are reported; after the run an
  empty join is reported.
* **A10 — types.** Columns are created from the value types the source returned; a string is never
  parsed into a number behind the model's back (a mismatch surfaces in the key report instead).
* **A11 — limits.** Inputs, output rows, memory and time are bounded; over a bound ⇒ a refusal that
  names the fix (aggregate or filter at the source).

Pure except for DuckDB; never touches the platform's databases or network.
"""
from __future__ import annotations

import json

import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The logical tool name the combine runs under at the one SQL checkpoint
#: (``tool_enforcement.guarded_sql_call``). Deliberately NOT ``TDA_*``: every protection exempts
#: framework tools, and this statement must pass the checkpoint. Declared once; Agent Authority,
#: the lane scanner, the honesty gate and the result-set registry read it from here.
DERIVED_TOOL = "compute_sql"
DIALECT = "duckdb"
SOURCE_ID = "combine"               # the preflight namespace (never shares a real DuckDB source's facts)

MAX_OUTPUT_ROWS = 5000              # = result_sets.MAX_ROWS: a combine result is a statement like any other
MAX_INPUT_ROWS = 200_000            # across every input of one call
MEMORY_LIMIT = "256MB"
TIMEOUT_SECONDS = 20.0
_THREADS = 2

#: The join-key ladder, least to most transformative. The FIRST step whose overlap reaches
#: ``KEY_MATCH_FLOOR`` of the smaller side's distinct keys wins — a deterministic, conservative
#: rule: a normalization is applied only when it demonstrably makes the keys meet.
KEY_LADDER = ("exact", "trimmed", "case_folded", "numeric", "affix_stripped")
KEY_MATCH_FLOOR = 0.5
_KEY_SAMPLE = 20_000                # distinct keys measured per side

_TABLE_RE = re.compile(r"^s([1-9]\d{0,2})$", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\b(?:TOP|LIMIT|FETCH\s+FIRST)\s+(\d+)\b", re.IGNORECASE)
_NUM_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
#: Scalar functions that read the ENGINE rather than the data (its settings, its environment,
#: its build). Table functions are already refused as non-``s<N>`` tables; these ride inside an
#: otherwise valid SELECT, so they are refused by name — the combine reads the turn's results only.
_DENIED_FUNCTIONS = frozenset({"current_setting", "getenv", "version", "pragma_version", "platform",
                               "current_database", "current_schema", "current_schemas", "current_catalog",
                               "current_user", "user", "session_user", "txid_current", "current_query"})


class CombineError(ValueError):
    """A refusal the model can act on: ``code`` is machine-readable, the message names the fix."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── A8 · completeness ─────────────────────────────────────────────────────────

def completeness(statement: Dict[str, Any]) -> Tuple[bool, str]:
    """``(complete, why_not)`` for one registered statement. Derived from what the registry
    already holds — no new field: the registry's own cut (``truncated``), or a row limit in the
    statement that the returned rows exactly fill (the source stopped, it did not run out)."""
    if statement.get("truncated"):
        return False, (f"statement {statement.get('n')} holds only the first {len(statement.get('rows') or [])} "
                       f"of {statement.get('row_count')} rows")
    text = str(statement.get("statement") or "")
    limits = [int(m) for m in _LIMIT_RE.findall(text)]
    if limits and statement.get("row_count") in limits:
        return False, (f"statement {statement.get('n')} returned exactly {statement.get('row_count')} rows, "
                       f"its own row limit — the source may hold more")
    return True, ""


# ── A2 · which tables the statement may name ──────────────────────────────────

_PHASE_TABLE_RE = re.compile(r"^p([1-9]\d{0,2})$", re.IGNORECASE)


def resolve_phase_tables(sql: str, by_phase) -> Tuple[str, Dict[str, int]]:
    """``p<N>`` tables — a plan phase's result, the only way a plan written before anything ran
    can name it (O1) — rewritten to the statement that phase produced (``s<M>``). ``by_phase`` maps
    a phase number to its statement. Returns ``(sql, {"p2": 3, …})``; an unknown phase is refused,
    naming the phases that have results. The rewrite is shown with the result, never silent."""
    if not isinstance(sql, str) or not re.search(r"\bp\d", sql, re.IGNORECASE):
        return sql, {}
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:  # noqa: BLE001 — the regular path reports the parse error
        return sql, {}
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    mapping: Dict[str, int] = {}
    for t in tree.find_all(exp.Table):
        name = (t.name or "").strip()
        m = _PHASE_TABLE_RE.match(name)
        if not m or name.lower() in ctes:
            continue
        st = by_phase(int(m.group(1)))
        if st is None:
            raise CombineError("no_such_phase", f"phase {m.group(1)} has no result this turn — name a phase "
                                                f"that ran a query, or its statement number (s1, s2, …)")
        mapping[name.lower()] = int(st["n"])
        alias = t.alias
        t.set("this", exp.to_identifier(f"s{st['n']}"))
        if not alias:
            t.set("alias", exp.TableAlias(this=exp.to_identifier(name)))
    if not mapping:
        return sql, {}
    return tree.sql(dialect=DIALECT), mapping


def referenced_statements(sql: str) -> List[int]:
    """The statement numbers the SELECT reads (``s<N>`` tables), in first-seen order. Raises
    ``CombineError`` for anything else a FROM/JOIN names — a file, a URL, a table function, a
    real table — and for anything but one read-only SELECT."""
    if not isinstance(sql, str) or not sql.strip():
        raise CombineError("no_sql", "op \"sql\" needs `sql`: ONE SELECT over the tables s1, s2, …")
    try:
        from trusted_data_agent.dbcatalog.sql_runner import single_select_guard
        single_select_guard(sql)
    except Exception as e:  # noqa: BLE001
        raise CombineError("not_a_select", f"{e} — the combine runs ONE read-only SELECT over s1, s2, …")
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception as e:  # noqa: BLE001
        raise CombineError("unparseable", f"the statement could not be parsed as DuckDB SQL ({e})")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out: List[int] = []
    bad: List[str] = []
    for t in tree.find_all(exp.Table):
        name = (t.name or "").strip()
        if t.args.get("db") or t.args.get("catalog"):
            bad.append(t.sql(dialect=DIALECT)); continue
        if name.lower() in ctes:
            continue
        m = _TABLE_RE.match(name)
        if not m:
            bad.append(t.sql(dialect=DIALECT) or "a table function"); continue
        n = int(m.group(1))
        if n not in out:
            out.append(n)
    funcs = set()
    for f in tree.find_all(exp.Func):
        # the name DuckDB will execute (sqlglot's class name can differ: version() → CurrentVersion)
        rendered = f.sql(dialect=DIALECT).split("(", 1)[0].strip().strip('"').lower()
        for fname in {rendered, (f.name or "").lower(), (f.sql_name() or "").lower()}:
            if fname in _DENIED_FUNCTIONS:
                funcs.add(fname)
    if funcs:
        raise CombineError("engine_introspection",
                           f"{', '.join(sorted(funcs))}() read the engine, not the data — the combine reads "
                           f"only this turn's results")
    if bad:
        raise CombineError("not_a_result_set",
                           f"the combine reads only this turn's results — tables s1, s2, … — never "
                           f"{', '.join(sorted(set(bad)))}")
    if not out:
        raise CombineError("no_result_set", "the statement names no result set — read at least one of s1, s2, …")
    return out


# ── A13 · isolation ───────────────────────────────────────────────────────────

def normalize_rules(rules: Any, origin: str) -> List[Dict[str, Any]]:
    """Operator isolation rules → ``[{"sources": set, "reason", "origin"}]``. A rule names two or
    more members whose results must never be combined: ENDPOINTS for a deployment rule
    (``mcp:host:port/path``, ``db:driver://host:port/db`` — ``core/source_endpoints``, so the rule
    holds for every user reaching that system) or source ids for an agent's own rule. A malformed
    rule is dropped (it can restrict nothing), never widened into a catch-all."""
    out = []
    for r in rules or []:
        if not isinstance(r, dict):
            continue
        members = r.get("endpoints") if r.get("endpoints") is not None else r.get("sources")
        srcs = {str(x).strip() for x in (members or []) if str(x).strip()}
        if len(srcs) >= 2:
            out.append({"sources": srcs, "reason": str(r.get("reason") or "").strip(), "origin": origin})
    return out


def origin_sources(statement: Dict[str, Any]) -> List[str]:
    """The data sources a statement's rows came from: its own source, or — for a combine result —
    every source its inputs came from (so combining a combine is judged on the original sources)."""
    orig = statement.get("origin_sources")
    if isinstance(orig, list) and orig:
        return [str(x) for x in orig]
    sid = str(statement.get("source_id") or "")
    return [sid] if sid else []


def isolation_refusal(sql: str, statements: Dict[int, Dict[str, Any]], rules: List[Dict[str, Any]],
                      session_id: str = "", endpoint_of=None) -> Optional[CombineError]:
    """The refusal a combine owes BEFORE it runs, or ``None``. Two rules, both code:

    * **operator rules** — the inputs' sources include two members of one rule ⇒ refused, naming
      the sources, the reason and who set it. Any combine counts (a UNION mixes the data too).
    * **the ontology's** — the SAME cross-domain rule the SQL checks apply
      (``preflight.joins.cross_domain_violations``): each part of the combine that relates inputs
      is expanded to the real tables those inputs read and judged unchanged. Silent when the
      ontology declares no subject areas (never a false positive)."""
    try:
        wanted = referenced_statements(sql)
    except CombineError:
        return None                      # the caller's own refusal names it
    # every identity each input carries: its source ids and — resolved lazily, only because a
    # rule exists — the endpoints behind them
    idents: Dict[int, set] = {}
    for n in wanted:
        ids = set(origin_sources(statements.get(n) or {}))
        if rules and endpoint_of is not None:
            ids |= {e for e in (endpoint_of(x) for x in list(ids)) if e}
        idents[n] = ids
    for rule in rules or []:
        hit = sorted({m for n in wanted for m in (idents[n] & rule["sources"])})
        if len(hit) >= 2:
            why = f" ({rule['reason']})" if rule.get("reason") else ""
            return CombineError("isolation_policy",
                                f"{' and '.join(hit)} must never be combined{why} — a rule set by the "
                                f"{rule.get('origin') or 'operator'}. Answer from each source separately.")
    try:
        from trusted_data_agent.agent.preflight import joins as _joins
        if not session_id or not _joins.known_areas(session_id):
            return None
        import sqlglot
        from sqlglot import exp
        real: Dict[str, List[str]] = {}
        for n in wanted:
            try:
                tree = sqlglot.parse_one(str((statements.get(n) or {}).get("statement") or ""), error_level=None)
                real[f"s{n}"] = sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name})
            except Exception:  # noqa: BLE001
                real[f"s{n}"] = []
        for scope in _joins.sql_scope_tables(sql):
            tables = sorted({t for name in scope for t in real.get(name, [])})
            if len(tables) < 2:
                continue
            v = _joins.cross_domain_violations(session_id, "SELECT 1 FROM " + ", ".join(tables))
            if v:
                data = _joins.cross_domain_block_result(v)["data"]
                return CombineError("cross_domain", data.replace("[Preflight] JOIN", "[Combine] A combine")
                                    .replace("The query was NOT executed", "The combine was NOT run"))
    except Exception:  # noqa: BLE001 — the ontology check fails open, exactly like the SQL checks
        return None
    return None


# ── A9 · join keys ────────────────────────────────────────────────────────────

def _common_affix(values: Sequence[str]) -> Tuple[str, str]:
    """A non-digit prefix/suffix shared by every value (``bid_123`` → ``bid_``); ``("", "")``
    when there is none. Only a consistent affix is ever stripped."""
    vals = [v for v in values if v]
    if len(vals) < 2:
        return "", ""
    pre = vals[0]
    for v in vals[1:]:
        while pre and not v.startswith(pre):
            pre = pre[:-1]
    pre = re.match(r"^\D*", pre).group(0) if pre else ""
    suf = vals[0]
    for v in vals[1:]:
        while suf and not v.endswith(suf):
            suf = suf[1:]
    suf = re.search(r"\D*$", suf).group(0) if suf else ""
    return pre, suf


def _normalize(values: Sequence[Any], step: str, affix: Tuple[str, str] = ("", "")) -> set:
    out = set()
    for v in values:
        if v is None:
            continue
        if step == "exact":
            out.add(v if not isinstance(v, float) or not v.is_integer() else int(v)); continue
        s = str(v).strip()
        if step == "trimmed":
            out.add(s)
        elif step == "case_folded":
            out.add(s.lower())
        elif step == "numeric":
            if _NUM_RE.match(s):
                f = float(s)
                out.add(int(f) if f.is_integer() else f)
        elif step == "affix_stripped":
            pre, suf = affix
            if pre and s.startswith(pre):
                s = s[len(pre):]
            if suf and s.endswith(suf):
                s = s[: len(s) - len(suf)]
            out.add(s.lower())
    return out


def _duck_expr(ref: str, step: str, affix: Tuple[str, str]) -> str:
    """The DuckDB expression that applies ladder ``step`` to column reference ``ref``."""
    txt = f"trim(CAST({ref} AS VARCHAR))"
    if step == "trimmed":
        return txt
    if step == "case_folded":
        return f"lower({txt})"
    if step == "numeric":
        return f"TRY_CAST({txt} AS DOUBLE)"
    if step == "affix_stripped":
        pre, suf = affix
        e = txt
        if pre:
            e = f"regexp_replace({e}, '^{re.escape(pre)}', '')"
        if suf:
            e = f"regexp_replace({e}, '{re.escape(suf)}$', '')"
        return f"lower({e})"
    return ref


def key_overlap(left: Sequence[Any], right: Sequence[Any]) -> Dict[str, Any]:
    """Measure how two key columns meet under every ladder step → ``{"ladder": [{step, overlap,
    ratio}], "winner": step|None, "affix": {...}}``. ``ratio`` = shared distinct keys over the
    smaller side's distinct keys. ``winner`` is the first step reaching ``KEY_MATCH_FLOOR``."""
    lv = list(dict.fromkeys(v for v in left if v is not None))[:_KEY_SAMPLE]
    rv = list(dict.fromkeys(v for v in right if v is not None))[:_KEY_SAMPLE]
    la = _common_affix([str(v).strip() for v in lv])
    ra = _common_affix([str(v).strip() for v in rv])
    ladder, winner = [], None
    for step in KEY_LADDER:
        a = _normalize(lv, step, la)
        b = _normalize(rv, step, ra)
        denom = min(len(a), len(b)) or 1
        shared = len(a & b)
        ratio = shared / denom
        ladder.append({"step": step, "overlap": shared, "ratio": round(ratio, 3)})
        if winner is None and ratio >= KEY_MATCH_FLOOR and a and b:
            winner = step
    return {"ladder": ladder, "winner": winner,
            "affix": {"left": list(la), "right": list(ra)},
            "left_distinct": len(lv), "right_distinct": len(rv),
            "left_repeats": len(left) > len(set(_normalize(left, "trimmed"))) if left else False,
            "right_repeats": len(right) > len(set(_normalize(right, "trimmed"))) if right else False}


def _join_equalities(tree) -> List[Tuple[Any, Any]]:
    """The ``col = col`` equalities inside JOIN … ON clauses whose two sides name different
    tables — the join keys code can measure. Anything else (USING, expressions) is left alone."""
    from sqlglot import exp
    out = []
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            l, r = eq.left, eq.right
            if isinstance(l, exp.Column) and isinstance(r, exp.Column) and l.table and r.table \
                    and l.table.lower() != r.table.lower():
                out.append((eq, (l, r)))
    return out


def plan_keys(sql: str, tables: Dict[str, Dict[str, Any]], aliases: Dict[str, str]) -> Tuple[str, List[Dict[str, Any]]]:
    """Measure every measurable join key and apply clear winners → ``(sql_to_run, reports)``.
    ``tables`` maps a table name (``s1``) to its statement; ``aliases`` maps an alias to a table
    name. A report per key: the two sides, the ladder, what was applied (or why nothing was)."""
    import sqlglot
    tree = sqlglot.parse_one(sql, read=DIALECT)
    reports: List[Dict[str, Any]] = []
    changed = False
    for eq, (l, r) in _join_equalities(tree):
        lt = aliases.get(l.table.lower(), l.table.lower())
        rt = aliases.get(r.table.lower(), r.table.lower())
        ls, rs = tables.get(lt), tables.get(rt)
        if not ls or not rs:
            continue
        lcol = _column_named(ls, l.name)
        rcol = _column_named(rs, r.name)
        if lcol is None or rcol is None:
            continue
        m = key_overlap([row.get(lcol) for row in ls.get("rows") or []],
                        [row.get(rcol) for row in rs.get("rows") or []])
        rep = {"left": f"{l.table}.{l.name}", "right": f"{r.table}.{r.name}",
               "ladder": m["ladder"], "applied": None,
               "many_to_many": bool(m["left_repeats"] and m["right_repeats"])}
        winner = m["winner"]
        if winner and winner != "exact":
            la, ra = tuple(m["affix"]["left"]), tuple(m["affix"]["right"])
            new = sqlglot.parse_one(f"{_duck_expr(l.sql(dialect=DIALECT), winner, la)} = "
                                    f"{_duck_expr(r.sql(dialect=DIALECT), winner, ra)}", read=DIALECT)
            eq.replace(new)
            rep["applied"] = winner
            changed = True
        elif winner is None:
            rep["unmatched"] = True
        reports.append(rep)
    return (tree.sql(dialect=DIALECT) if changed else sql), reports


def _column_named(statement: Dict[str, Any], name: str) -> Optional[str]:
    cols = statement.get("columns") or []
    for c in cols:
        if c == name:
            return c
    low = name.lower()
    for c in cols:
        if str(c).lower() == low:
            return c
    return None


def _aliases(sql: str) -> Dict[str, str]:
    import sqlglot
    from sqlglot import exp
    out = {}
    for t in sqlglot.parse_one(sql, read=DIALECT).find_all(exp.Table):
        if t.alias:
            out[t.alias.lower()] = t.name.lower()
    return out


# ── A10 · types ───────────────────────────────────────────────────────────────

def _duck_type(values: Sequence[Any]) -> str:
    kinds = set()
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            kinds.add("BOOLEAN")
        elif isinstance(v, int):
            kinds.add("BIGINT")
        elif isinstance(v, float):
            kinds.add("DOUBLE")
        else:
            kinds.add("VARCHAR")
    if not kinds:
        return "VARCHAR"
    if kinds == {"BIGINT", "DOUBLE"}:
        return "DOUBLE"
    return kinds.pop() if len(kinds) == 1 else "VARCHAR"


# A number written as text, exactly as a data source serialises one - never a code: no leading zero
# before another digit ("007" stays text), no spaces, no grouping.
_PLAIN_NUMBER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_BIGINT_MAX = 2 ** 63 - 1


def _numeric_text_type(values: Sequence[Any]) -> Optional[str]:
    """Some sources (the Teradata MCP server among them) return every value as JSON text
    (``"CustomerCount": "2"``). A column whose EVERY non-null value is a plain number - as text or
    already numeric - is loaded as a number: BIGINT when none has a decimal part and all fit,
    DOUBLE otherwise. ``None`` when any value is not a plain number (it stays text), when the column
    holds no text at all (nothing to convert) or no values. The caller names the conversion."""
    seen_text = seen_any = False
    decimal = False
    for v in values:
        if v is None:
            continue
        seen_any = True
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            decimal = decimal or isinstance(v, float)
            continue
        if not isinstance(v, str) or not _PLAIN_NUMBER_RE.match(v):
            return None
        seen_text = True
        if "." in v:
            decimal = True
        elif abs(int(v)) > _BIGINT_MAX:
            decimal = True
    if not (seen_any and seen_text):
        return None
    return "DOUBLE" if decimal else "BIGINT"


def _cell(v: Any, typ: str) -> Any:
    if v is None:
        return None
    if typ in ("BIGINT", "DOUBLE") and isinstance(v, str):
        return int(v) if typ == "BIGINT" else float(v)
    if typ == "VARCHAR" and not isinstance(v, str):
        return str(v)
    if typ == "DOUBLE" and isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    return v


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# ── run ───────────────────────────────────────────────────────────────────────

def _column_needs(sql: str) -> Optional[Dict[str, set]]:
    """For each ``s<N>`` table the SELECT reads, the column names referenced THROUGH it (a
    qualifier naming the table or its alias). Unqualified columns are left out — DuckDB resolves
    those itself. ``None`` when the statement cannot be parsed."""
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:  # noqa: BLE001
        return None
    alias_to_table: Dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        name = (t.name or "").lower()
        if _TABLE_RE.match(name):
            alias_to_table[(t.alias_or_name or name).lower()] = name
    needs: Dict[str, set] = {name: set() for name in alias_to_table.values()}
    for c in tree.find_all(exp.Column):
        q = (c.table or "").lower()
        if q and q in alias_to_table:
            needs[alias_to_table[q]].add((c.name or "").lower())
    return needs


MAX_REPAIR_POOL = 12            # results considered when re-pointing a statement (bounded search)


def _content_sig(st: Dict[str, Any]) -> tuple:
    """Two results with the same statement and the same rows are the same answer."""
    try:
        rows = json.dumps(st.get("rows") or [], sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        rows = str(st.get("rows"))
    return (" ".join(str(st.get("statement") or "").split()).lower(), tuple(st.get("columns") or []), rows)


def repair_table_assignment(sql: str, statements: Dict[int, Dict[str, Any]]):
    """Parallel calls finish in any order, and statement numbers follow the order results ARRIVED
    — so a model that numbers by call order can name the right results under the wrong numbers.
    When the SELECT does not bind as written but exactly ONE assignment of this turn's results to
    the names it uses makes every column it names resolve, the intent is unambiguous: return
    ``(rewritten_sql, {old: new})``. Assignments that differ only by picking a re-run of the SAME
    query (same statement, same rows) are one answer — the latest is taken. ``None`` when the
    statement already binds, when no or several genuinely different assignments would, or when the
    search would be unbounded. The CALLER re-checks isolation and completeness for the new inputs."""
    from itertools import permutations
    needs = _column_needs(sql)
    if not needs:
        return None
    names = sorted(needs)
    pool = sorted(f"s{n}" for n in statements)
    if not 1 <= len(names) <= 4 or len(pool) > MAX_REPAIR_POOL or not all(n in pool for n in names):
        return None
    cols = {name: {str(c).lower() for c in (statements[int(name[1:])].get("columns") or [])} for name in pool}

    def binds(assign: Dict[str, str]) -> bool:
        return all(needs[t] <= cols[assign[t]] for t in names)

    if binds({t: t for t in names}):
        return None                                   # the failure is not about which result is which
    fits = [dict(zip(names, p)) for p in permutations(pool, len(names)) if binds(dict(zip(names, p)))]
    if not fits:
        return None
    sigs = {tuple(_content_sig(statements[int(f[t][1:])]) for t in names) for f in fits}
    if len(sigs) != 1:
        return None                                   # genuinely different answers: never guessed
    best = max(fits, key=lambda f: tuple(int(f[t][1:]) for t in names))   # the latest re-run
    mapping = {old: new for old, new in best.items() if old != new}
    if not mapping:
        return None
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=DIALECT)
        unaliased = {(t.name or "").lower() for t in tree.find_all(exp.Table)
                     if not t.alias and (t.name or "").lower() in mapping}

        def _swap(node):
            if isinstance(node, exp.Table) and (node.name or "").lower() in mapping:
                node.set("this", exp.to_identifier(mapping[(node.name or "").lower()]))
            elif isinstance(node, exp.Column) and (node.table or "").lower() in unaliased:
                node.set("table", exp.to_identifier(mapping[(node.table or "").lower()]))
            return node
        return tree.transform(_swap).sql(dialect=DIALECT), mapping
    except Exception:  # noqa: BLE001
        return None


def tables_note(wanted: List[int], statements: Dict[int, Dict[str, Any]]) -> str:
    """What every table available this turn actually IS — the tool that produced it and its
    columns — so a failed combine can be fixed in one round (listing only the tables the failed
    statement named hides the one it meant). Numbers follow the order results arrived."""
    parts = []
    for n in sorted(statements):
        st = statements.get(n) or {}
        cols = ", ".join(str(c) for c in (st.get("columns") or [])[:12]) or "no columns"
        parts.append(f"s{n} = the result of {st.get('tool') or 'a query'} ({cols})")
    return ("Tables available: " + "; ".join(parts) +
            ". Statement numbers follow the order results ARRIVED, not the order the calls were made.")


def run(sql: str, statements: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Execute the combine over ``statements`` (number → registered statement) and return the
    result in the shape the SQL checkpoint expects: ``{"status": "success", "results": [rows],
    "columns", "row_count", "truncated", "inputs", "executed_sql", "key_reports", "findings"}``.
    Raises ``CombineError`` for a refusal decided before anything runs. Synchronous — the caller
    runs it off the event loop."""
    wanted = referenced_statements(sql)
    missing = [n for n in wanted if n not in statements]
    if missing:
        have = ", ".join(f"s{n}" for n in sorted(statements)) or "none yet"
        raise CombineError("no_such_statement",
                           f"{', '.join(f's{n}' for n in missing)} did not run this turn; available: {have}")
    total = sum(len(statements[n].get("rows") or []) for n in wanted)
    if total > MAX_INPUT_ROWS:
        raise CombineError("inputs_too_large",
                           f"the inputs hold {total} rows (limit {MAX_INPUT_ROWS}) — filter or aggregate at "
                           f"each source first, then combine the smaller results")
    tables = {f"s{n}": statements[n] for n in wanted}
    try:
        sql_to_run, key_reports = plan_keys(sql, tables, _aliases(sql))
    except Exception:  # noqa: BLE001 — measuring keys is advisory; the statement still runs as written
        sql_to_run, key_reports = sql, []

    import duckdb
    converted: List[str] = []           # text columns loaded as numbers - named in the result
    conn = duckdb.connect(":memory:", config={
        "enable_external_access": False, "autoinstall_known_extensions": False,
        "autoload_known_extensions": False, "memory_limit": MEMORY_LIMIT, "threads": _THREADS,
        "lock_configuration": True})
    timer = threading.Timer(TIMEOUT_SECONDS, conn.interrupt)
    try:
        # Tables WITH rows are typed from their values first; an EMPTY table (a phase that found
        # no rows — still a result) then borrows the type decided for a same-named column of
        # another table in this combine, so `IN (SELECT id FROM empty)` compares like with like
        # (found live: BIGINT vs VARCHAR on an empty side stopped an honest zero).
        seen_types: Dict[str, str] = {}
        ordered = sorted(tables.items(), key=lambda kv: 0 if (kv[1].get("rows") or []) else 1)
        for name, st in ordered:
            cols = list(st.get("columns") or [])
            rows = st.get("rows") or []
            types = []
            for c in cols:
                vals = [r.get(c) for r in rows]
                if not rows:
                    types.append(seen_types.get(str(c).lower(), "VARCHAR"))
                    continue
                num = _numeric_text_type(vals)
                if num:
                    converted.append(f"{name}.{c}")
                t = num or _duck_type(vals)
                types.append(t)
                seen_types.setdefault(str(c).lower(), t)
            conn.execute(f"CREATE TABLE {name} ({', '.join(f'{_quote(c)} {t}' for c, t in zip(cols, types))})")
            if rows:
                conn.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in cols)})",
                                 [[_cell(r.get(c), t) for c, t in zip(cols, types)] for r in rows])
        timer.start()
        try:
            cur = conn.execute(sql_to_run)
        except duckdb.InterruptException:
            raise CombineError("timed_out", f"the combine ran longer than {int(TIMEOUT_SECONDS)}s — "
                                            f"reduce the inputs at each source first")
        cols_out = [d[0] for d in (cur.description or [])]
        fetched = cur.fetchmany(MAX_OUTPUT_ROWS + 1)
    except CombineError:
        raise
    except Exception as e:  # noqa: BLE001 — a SQL error is the tool's error, handed to the model
        return {"status": "error", "error": f"{type(e).__name__}: {e}", "executed_sql": sql_to_run,
                "inputs": wanted, "key_reports": key_reports}
    finally:
        timer.cancel()
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    truncated = len(fetched) > MAX_OUTPUT_ROWS
    rows_out = [dict(zip(cols_out, r)) for r in fetched[:MAX_OUTPUT_ROWS]]
    findings: List[str] = []
    if converted:
        findings.append("loaded as numbers (every value was a plain number written as text): "
                        + ", ".join(converted))
    for rep in key_reports:
        if rep.get("applied"):
            findings.append(f"join key {rep['left']} = {rep['right']} matched only after "
                            f"{rep['applied'].replace('_', ' ')} normalization — applied")
        elif rep.get("unmatched"):
            best = max(rep["ladder"], key=lambda s: s["ratio"])
            findings.append(f"join key {rep['left']} = {rep['right']} barely overlaps "
                            f"(best: {best['step'].replace('_', ' ')}, {best['overlap']} shared keys) — "
                            f"check the key columns and their formats")
        if rep.get("many_to_many"):
            findings.append(f"join key {rep['left']} = {rep['right']} repeats on BOTH sides — rows "
                            f"multiply; aggregate one side per key first")
    if key_reports and not rows_out and all((tables[t].get("rows") for t in tables)):
        findings.append("the join returned no rows although every input has rows — the keys do not meet")
    return {"status": "success", "results": rows_out, "columns": cols_out,
            "row_count": len(rows_out), "truncated": truncated, "inputs": wanted,
            "executed_sql": sql_to_run, "key_reports": key_reports, "findings": findings}
