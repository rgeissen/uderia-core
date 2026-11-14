"""Preflight VALUE-existence check — pure + deterministic, 0 tokens.

The class it closes (2026-09-16, prod session 9788cdd9 turn 4): asked "how many robberies
in Chicago", the NIBRS analyst never used the city column and filled the gap from
memory — ``BH020 = '031'``, Cook County's FIPS code, where ``BH020`` is the UCR county
code (Chicago's agencies carry ``'16'``) and ``'031'`` occurs in ZERO rows of the table.
The query succeeded, returned 0, and the answer read "there were none". The honesty
gate was correctly silent (the same session had proven the table holds robberies), so
the defect is a hallucinated LITERAL in a valid column — a claim about the filter that
no relation-level check can see. Locally the same shape read ``core_city = 'Chicago'``
on a decoded Yes/No label.

Two evidence sources, two verdicts, one module:

* **PROVEN** — the ontology declares the column's value domain (its decoded codes, or
  the labels of the code it decodes) and the literal is in neither. Provable from the
  registry alone, no probe; refused under ``enforce`` naming the domain (and, when the
  literal is the LABEL of a code, the code to use or the label column to filter).
* **UNVERIFIED** — the column carries no declared domain (a name, a free code) and
  nothing this session has shown the literal occurs in it. Never refused pre-execution
  (a city name is a legitimate first-time filter); instead an EMPTY result on such a
  filter is annotated after execution, recovery-shaped: a zero on an unverified value
  is a claim about the filter before it is a claim about the world, and the note names
  the probe that settles it. The honesty gate reads that stamp at answer time.

Learning: every value a result row carries for a recorded column is remembered for the
session, so a value the data itself showed is never questioned again. Fail-open by
construction: no recorded domains, an unparseable statement, an unresolvable column,
an empty/NULL literal → no verdict.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.agent.preflight import dialect as _dialect

_LOCK = threading.Lock()
_TTL_SECONDS = 1800
#: A domain larger than this is not carried — it is assumed incomplete and the column is
#: treated as having NO declared domain (never proven-absent on a truncated list).
MAX_DOMAIN = 500
#: Observed values remembered per (session, table, column).
_MAX_OBSERVED = 2000

# session_id -> {"tables": {leaf: {col_lower: {"table","column","values":{norm:display},
#                "kind": "code"|"label", "pair": other_col|None}}},
#                "seen": {(leaf, col_lower): {norm,...}}, "ts": monotonic}
_STORE: Dict[str, Dict[str, Any]] = {}


def _leaf(name: str) -> str:
    return str(name or "").strip().strip('"').rpartition(".")[2].lower()


def _norm(v: Any) -> str:
    """Comparison form of a literal or a stored value: trimmed, case-folded (Teradata's
    default NOT CASESPECIFIC comparison and blank padding)."""
    return " ".join(str(v).split()).lower()


_RANGE_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s+(?:to|through|-|–)\s+-?\d+(?:\.\d+)?\s*$", re.IGNORECASE)
_NEG_INT_RE = re.compile(r"^\s*-\d+\s*$")


def closed_domain(values: Dict[Any, Any]) -> bool:
    """A declared value set is PROOF of absence only when it is a CLOSED enumeration.

    Two shapes a codebook declares that are not: a RANGE entry (`'91 to 818'` — the NIBRS
    second/third offence slots, where the first slot lists all 50 codes and the others
    the range they fall in) and a set of NOTHING BUT missing-value codes (`BH020` = the
    UCR county code declares only `-6`, its "not applicable"; the real values are any
    county number). Both read live as false positives on the first run (2026-09-16:
    `V20062 = '120'` refused; `BH020 = '170'`, a real value, refused). A domain
    carrying either is OPEN and the column is treated as having no declared domain —
    it may still be judged UNVERIFIED after an empty result, never proven-absent.
    """
    if not isinstance(values, dict) or not values:
        return False
    if len(values) > MAX_DOMAIN:
        return False
    keys = [str(k) for k in values.keys()]
    if any(_RANGE_RE.match(k) for k in keys):
        return False
    if all(_NEG_INT_RE.match(k) for k in keys):
        return False
    return True


def _entry(session_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        e = _STORE.get(session_id)
        if not e:
            return None
        if time.monotonic() - e["ts"] > _TTL_SECONDS:
            _STORE.pop(session_id, None)
            return None
        return e


def record_value_domains(session_id: str, entries: Any) -> int:
    """Record the declared value domains the enrichment names for a session (union —
    an out-of-domain turn's empty enrichment must not erase an earlier turn's).
    ``entries``: [{"table": deployed_or_logical, "columns": [{"name", "values": {code:
    label}, "kind": "code"|"label", "pair": other_column}]}]. Returns the number of
    columns known afterwards."""
    if not session_id or not isinstance(entries, list):
        return 0
    tables: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        table = str(ent.get("table") or "").strip()
        leaf = _leaf(table)
        if not leaf:
            continue
        for c in ent.get("columns") or []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "").strip()
            vals = c.get("values")
            if not name or not isinstance(vals, dict) or not closed_domain(vals):
                continue
            tables.setdefault(leaf, {})[name.lower()] = {
                "table": table, "column": name,
                "values": {_norm(k): str(v) for k, v in vals.items() if _norm(k)},
                "kind": str(c.get("kind") or "code"),
                "pair": (str(c.get("pair")) if c.get("pair") else None),
            }
    with _LOCK:
        e = _STORE.get(session_id) or {"tables": {}, "seen": {}}
        for leaf, cols in tables.items():
            e["tables"].setdefault(leaf, {}).update(cols)
        e["ts"] = time.monotonic()
        _STORE[session_id] = e
        if len(_STORE) > 1000:
            now = time.monotonic()
            for sid in [s for s, x in _STORE.items() if now - x["ts"] > _TTL_SECONDS]:
                _STORE.pop(sid, None)
        return sum(len(v) for v in e["tables"].values())


def record_from_event(session_id: str, event: Any) -> int:
    """Record from a ``kg_enrichment_event`` (its ``enrichments[*].value_domains``)."""
    try:
        if not isinstance(event, dict):
            return 0
        n = 0
        for enr in event.get("enrichments") or []:
            if isinstance(enr, dict) and enr.get("value_domains"):
                n = record_value_domains(session_id, enr.get("value_domains"))
        return n
    except Exception:
        return 0


def known_domains(session_id: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    e = _entry(session_id)
    return {k: dict(v) for k, v in (e or {}).get("tables", {}).items()} if e else {}


def observe_values(session_id: str, leaf: str, column: str, values: Any) -> int:
    """Remember values the data itself returned for a column (any column, domain or
    not): a value seen in a result row is never questioned again this session."""
    if not session_id or not isinstance(values, (list, tuple)):
        return 0
    norms = {_norm(v) for v in values if v is not None and str(v).strip()}
    if not norms:
        return 0
    with _LOCK:
        e = _STORE.get(session_id) or {"tables": {}, "seen": {}}
        key = (_leaf(leaf), str(column).lower())
        s = e["seen"].setdefault(key, set())
        if len(s) < _MAX_OBSERVED:
            s.update(list(norms)[: _MAX_OBSERVED - len(s)])
        e["ts"] = time.monotonic()
        _STORE[session_id] = e
        return len(s)


def _seen(session_id: str, leaf: str, column: str, norm: str) -> bool:
    e = _entry(session_id)
    if not e:
        return False
    return norm in (e.get("seen", {}).get((_leaf(leaf), str(column).lower())) or set())


# ── SQL analysis ──────────────────────────────────────────────────────────────
def _parse(sql: str):
    try:
        import sqlglot
        return [s for s in sqlglot.parse(sql, read=_dialect.read()) if s is not None]
    except Exception:
        return []


def _statement_tables(stmt) -> Dict[str, str]:
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


def _unwrap_column(node):
    """The column under UPPER()/LOWER()/TRIM() wrappers (case/blank-insensitive
    comparisons are the normal form and change nothing about which value is meant)."""
    from sqlglot import exp
    cur = node
    for _ in range(4):
        if isinstance(cur, exp.Column):
            return cur
        if isinstance(cur, (exp.Upper, exp.Lower, exp.Trim)):
            cur = cur.this
            continue
        if isinstance(cur, exp.Paren):
            cur = cur.this
            continue
        return None
    return None


def _literal_text(node) -> Optional[str]:
    from sqlglot import exp
    if isinstance(node, exp.Literal):
        return str(node.this)
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return "-" + str(node.this.this)
    return None


def _scope_tables(node) -> Dict[str, str]:
    """alias/leaf -> leaf for the tables of the SELECT that OWNS a predicate — its own
    FROM and JOINs, not those of an enclosing or nested query. An unqualified column in
    a subquery (`… ori IN (SELECT ori FROM v_Agency WHERE core_city = 'Chicago')`) is
    resolved against v_Agency alone; judged against the whole statement's tables it read
    as ambiguous and was skipped (the first live run, 2026-09-16)."""
    from sqlglot import exp
    sel = node.find_ancestor(exp.Select)
    if sel is None:
        return {}
    out: Dict[str, str] = {}
    roots = [sel.args.get("from_") or sel.args.get("from")] + list(sel.args.get("joins") or [])
    for r in roots:
        if r is None:
            continue
        for tbl in r.find_all(exp.Table):
            if tbl.find_ancestor(exp.Select) is not sel:
                continue                       # a table of a nested subquery
            leaf = str(tbl.name or "").lower()
            if not leaf:
                continue
            out[leaf] = leaf
            if tbl.alias:
                out[str(tbl.alias).lower()] = leaf
    return out


def equality_literals(sql: str) -> List[Dict[str, Any]]:
    """Every ``col = 'lit'`` / ``col IN ('a','b')`` predicate: [{"table_ref", "column",
    "literal", "snippet", "tables"}]. ``table_ref`` is the alias/table the column names,
    or None when unqualified; ``tables`` is the owning SELECT's own scope (alias -> leaf),
    falling back to the whole statement's. Fail-open: an unparseable statement yields []."""
    from sqlglot import exp
    out: List[Dict[str, Any]] = []
    for stmt in _parse(sql or ""):
        stmt_tables = _statement_tables(stmt)
        for node in stmt.find_all(exp.EQ, exp.In):
            tables = _scope_tables(node) or stmt_tables
            pairs: List[Tuple[Any, Any]] = []
            if isinstance(node, exp.EQ):
                pairs = [(node.left, node.right), (node.right, node.left)]
            else:
                pairs = [(node.this, x) for x in (node.expressions or [])]
            for colnode, litnode in pairs:
                col = _unwrap_column(colnode)
                lit = _literal_text(litnode)
                if col is None or lit is None:
                    continue
                ref = str(col.table or "").lower() or None
                out.append({"table_ref": ref, "column": str(col.name), "literal": lit,
                            "snippet": node.sql(dialect=_dialect.read())[:120], "tables": tables,
                            "stmt_tables": stmt_tables})
    return out


def _resolve(f: Dict[str, Any], domains: Dict[str, Dict[str, Dict[str, Any]]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(leaf, domain entry) for a predicate's column, or None when it cannot be pinned
    to ONE recorded table (an unqualified column two named tables carry is not judged)."""
    tables = f.get("tables") or {}
    cname = str(f["column"]).lower()
    if f.get("table_ref"):
        leaf = tables.get(f["table_ref"]) or (f.get("stmt_tables") or {}).get(f["table_ref"]) or f["table_ref"]
        d = (domains.get(leaf) or {}).get(cname)
        return (leaf, d) if d else None
    hits = [(leaf, (domains.get(leaf) or {}).get(cname)) for leaf in set(tables.values())]
    hits = [(l, d) for l, d in hits if d]
    return hits[0] if len(hits) == 1 else None


def value_violations(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """PROVEN findings: a literal compared for equality against a column whose declared
    value domain (codes, or a label column's labels) contains neither it nor anything the
    data returned this session. Each carries the domain and — when the literal is the
    LABEL of one of the column's codes, or the CODE of its label column — the exact
    remedy. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        domains = known_domains(session_id)
        if not domains:
            return out
        for f in equality_literals(sql):
            lit = _norm(f["literal"])
            if not lit:
                continue
            hit = _resolve(f, domains)
            if not hit:
                continue
            leaf, d = hit
            if lit in d["values"] or _seen(session_id, leaf, d["column"], lit):
                continue
            finding = {"table": d["table"], "leaf": leaf, "column": d["column"], "literal": f["literal"],
                       "kind": d["kind"], "snippet": f["snippet"],
                       "domain": dict(list(d["values"].items())[:MAX_DOMAIN])}
            # the cross case: the literal names a LABEL on a code column (or a code on a
            # label column) — the most common wrong guess, and the one with an exact fix
            if d["kind"] == "code":
                for code, label in d["values"].items():
                    if _norm(label) == lit:
                        finding["cross"] = {"code": code, "label": label, "pair": d.get("pair")}
                        break
            else:
                pair_d = (domains.get(leaf) or {}).get(str(d.get("pair") or "").lower())
                if pair_d and lit in pair_d["values"]:
                    finding["cross"] = {"code": f["literal"], "label": pair_d["values"][lit], "pair": d.get("pair")}
            out.append(finding)
    except Exception:
        return []
    return out


def unverified_literals(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """Equality literals on columns WITHOUT a declared domain that nothing this session
    has shown to occur in that column — the ones an empty result cannot vouch for."""
    out: List[Dict[str, Any]] = []
    try:
        e = _entry(session_id)
        if not e:
            return out        # armed by the ontology: a session that recorded nothing is not judged
        domains = e.get("tables", {})
        for f in equality_literals(sql):
            lit = _norm(f["literal"])
            if not lit:
                continue
            tables = f.get("tables") or {}
            if f.get("table_ref"):
                leaf = tables.get(f["table_ref"]) or (f.get("stmt_tables") or {}).get(f["table_ref"]) or f["table_ref"]
            else:
                leaves = set(tables.values())
                if len(leaves) != 1:
                    continue
                leaf = next(iter(leaves))
            if (domains.get(leaf) or {}).get(str(f["column"]).lower()):
                continue          # a declared domain is the PROVEN check's business
            if _seen(session_id, leaf, f["column"], lit):
                continue
            out.append({"leaf": leaf, "column": f["column"], "literal": f["literal"], "snippet": f["snippet"]})
    except Exception:
        return []
    return out


# ── learning from results ─────────────────────────────────────────────────────
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_\.\"]+)", re.IGNORECASE)


def _tables_in_sql(sql: str) -> List[str]:
    stmts = _parse(sql)
    if stmts:
        leaves = set()
        for s in stmts:
            leaves.update(_statement_tables(s).values())
        return sorted(leaves)
    return sorted({_leaf(m.group(1)) for m in _TABLE_RE.finditer(sql or "")})


def _rows_of(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    for key in ("results", "data", "rows"):
        rows = result.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def learn_from_result(session_id: str, sql: str, result: Any, max_rows: int = 200) -> int:
    """After a query ran: every column value the rows carry is an observed value for
    every table the statement names that has that column recorded — or, for a
    single-table statement, for that table. Returns the number of (column, value)
    observations recorded. Never raises."""
    n = 0
    try:
        if not session_id:
            return 0
        rows = _rows_of(result)[:max_rows]
        if not rows:
            return 0
        leaves = _tables_in_sql(sql or "")
        if not leaves:
            return 0
        cols: Dict[str, List[Any]] = {}
        for r in rows:
            for k, v in r.items():
                if isinstance(k, str):
                    cols.setdefault(k, []).append(v)
        domains = known_domains(session_id)
        for cname, vals in cols.items():
            targets = [leaf for leaf in leaves if cname.lower() in (domains.get(leaf) or {})]
            if not targets and len(leaves) == 1:
                targets = leaves
            for leaf in targets:
                n += observe_values(session_id, leaf, cname, vals)
    except Exception:
        return n
    return n


def is_empty_result(result: Any) -> bool:
    """A successful result carrying no rows, or a single aggregate row that is zero."""
    if not isinstance(result, dict) or result.get("status") == "error" or result.get("success") is False:
        return False
    rows = None
    for key in ("results", "data", "rows"):
        if isinstance(result.get(key), list):
            rows = result[key]
            break
    if rows is None:
        return False
    if not rows:
        return True
    if len(rows) == 1 and isinstance(rows[0], dict) and len(rows[0]) == 1:
        try:
            return float(list(rows[0].values())[0]) == 0.0
        except (TypeError, ValueError):
            return False
    return False


# ── the model-facing texts ────────────────────────────────────────────────────
def _domain_text(d: Dict[str, str], kind: str, limit: int = 12) -> str:
    items = list(d.items())
    shown = items[:limit]
    if kind == "code":
        body = ", ".join(f"'{k}'={v}" if v and _norm(v) != k else f"'{k}'" for k, v in shown)
    else:
        body = ", ".join(f"'{v}'" for _, v in shown)
    more = f" … ({len(items) - limit} more)" if len(items) > limit else ""
    return body + more


def block_result(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recovery-shaped: names the column, its declared values, and the exact fix when
    the literal is a label on a code column (or a code on a label column). The query
    was NOT run."""
    lead = findings[0]
    col, tbl, lit = lead["column"], lead["table"], lead["literal"]
    dom = _domain_text(lead["domain"], lead["kind"])
    cross = lead.get("cross")
    if cross and lead["kind"] == "code":
        fix = (f" '{lit}' is the decoded LABEL of code '{cross['code']}': filter {col} = '{cross['code']}'"
               + (f", or filter the label column {cross['pair']} = '{cross['label']}'" if cross.get("pair") else "")
               + ".")
    elif cross:
        fix = (f" '{lit}' is a CODE; this column carries the decoded labels: filter {col} = '{cross['label']}'"
               + (f", or filter the code column {cross['pair']} = '{lit}'" if cross.get("pair") else "")
               + ".")
    else:
        fix = (f" '{lit}' is not one of them, so the filter could only return an empty result — that is "
               f"not evidence that nothing matches, it is a value the column never holds. Filter on one "
               f"of the declared values, or on the attribute that actually carries the concept (a name, "
               f"a description), never on a value remembered from elsewhere.")
    what = "; ".join(sorted({f["snippet"] for f in findings}))
    msg = (f"[Preflight] {col} on {tbl} carries the declared {'codes' if lead['kind'] == 'code' else 'labels'} "
           f"{dom}. This statement filters {what}.{fix} The query was NOT executed; rewrite the filter and retry.")
    return {"status": "error", "preflight": True, "preflight_value": True,
            "error": f"Filter value '{lit}' is not a declared value of {col}",
            "data": msg, "violations": findings}


NOTE_MARKER = "[Preflight:value]"


def empty_result_note(unverified: List[Dict[str, Any]]) -> str:
    """The note stamped on an EMPTY result whose filter rests on an unverified literal."""
    lead = unverified[0]
    preds = "; ".join(sorted({u["snippet"] for u in unverified}))
    col, leaf, lit = lead["column"], lead["leaf"], lead["literal"]
    return (f"{NOTE_MARKER} 0 rows — but nothing in this turn established that '{lit}' occurs in {col} "
            f"({leaf} declares no value list for it, and no result row has shown it). A zero on an "
            f"unverified value is a claim about the FILTER, not about the data: it means '{lit}' matched "
            f"nothing, not that nothing exists. Before reporting an absence, check the value: "
            f"{_dialect.limit_clause(20, f'{col}, COUNT(*) AS n')} FROM {leaf} GROUP BY 1 ORDER BY 2 DESC"
            f"{_dialect.limit_suffix(20)} — or filter on the "
            f"attribute that carries the concept by NAME rather than a code remembered from elsewhere. "
            f"Filtered: {preds}.")


def _reset_all() -> None:
    with _LOCK:
        _STORE.clear()
