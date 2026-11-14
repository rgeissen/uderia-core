"""The workload recommender (PROP-008 §4.5) — pure, deterministic, zero LLM.

Optimisation is a LOOP over evidence the platform already keeps, never a deploy-time
guess. This module turns that evidence into PROPOSALS — never into a change:

    workload  = the statements the agents ran (each optionally with its EXPLAIN findings
                and estimated seconds) — read from the persisted turns by the caller
    facts     = what the warehouse holds per table: rows, column count and types, the
                declared key, statistics (with their age), existing join indexes

    analyze_statement(sql)        -> which columns one statement filters, joins, groups
    aggregate(workload)           -> per table and column: how often, how, in which statements
    recommend(agg, facts)         -> proposals, each naming its rule, its evidence in words,
                                     the statements it rests on, and how it would land

Every decision is a RULE RECORD (`RULES`) with its threshold and its reason, like the design
standard's rules — tunable, testable, never buried in a branch. A proposal is applied only
by a person's approval through the proposal lifecycle (`core/tuning_proposals.py`); nothing
here writes. DDL is rendered by the dialect (`dialects.teradata.render_tuning`) so the
decisions stay backend-neutral.

Purposes (``ddl_artifact.purpose`` once landed): ``statistics`` · ``join_index`` ·
``partition``. A partition change on a populated table needs a rebuild, so a partition
proposal is recorded with ``landing: "rebuild"`` and is not landed by the platform in v1.
A table the agents read that the enrichment never showed them is a MODELLING finding, not a
tuning one — reported apart, never proposed as DDL.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PURPOSES = ("statistics", "join_index", "partition")


@dataclass(frozen=True)
class TuningRule:
    id: str
    purpose: str
    rationale: str
    thresholds: Dict[str, Any] = field(default_factory=dict)


RULES: Tuple[TuningRule, ...] = (
    TuningRule("stats_on_workload_columns", "statistics",
               "A column the agents filter, join or group on, with no statistics, leaves the "
               "optimizer estimating row counts with no confidence.",
               {"min_statements": 2}),
    TuningRule("stats_where_no_confidence", "statistics",
               "EXPLAIN said it planned with no confidence on a table — statistics on the columns "
               "that statement used let it plan from facts.",
               {"min_statements": 1}),
    TuningRule("stats_on_product_join_key", "statistics",
               "A product join on a declared key is the optimizer guessing the join is tiny — "
               "statistics on both sides of the key correct the guess.",
               {"min_statements": 1}),
    TuningRule("stats_stale", "statistics",
               "Statistics older than the threshold on a column the workload still uses describe "
               "a table that has since changed.",
               {"max_age_days": 30, "min_statements": 1}),
    TuningRule("covering_join_index", "join_index",
               "A wide table the workload keeps scanning end to end for a small set of predicate "
               "columns is served by a narrow covering join index: the declared key plus those "
               "columns. It answers every reading of a predicate over them, and the optimizer "
               "picks it without the agent knowing.",
               {"min_columns": 40, "min_rows": 1_000_000, "min_scan_statements": 2, "max_index_columns": 32}),
    TuningRule("partition_on_date_filter", "partition",
               "A large table filtered by a date range in many statements reads only the "
               "partitions in range once it is partitioned by that date.",
               {"min_statements": 3, "min_rows": 1_000_000}),
)
RULE_BY_ID = {r.id: r for r in RULES}

_DATE_TYPES = ("DATE", "TIMESTAMP")


# ── statement analysis ─────────────────────────────────────────────────────────

def _dialect_read() -> str:
    try:
        from trusted_data_agent.agent.preflight import dialect as _d
        return _d.read()
    except Exception:  # noqa: BLE001
        return "teradata"


def analyze_statement(sql: str, dialect: Optional[str] = None) -> Dict[str, Any]:
    """Which base tables one statement reads and which of their columns it filters, joins and
    groups on. Tables are named as written (``db.table`` when qualified, lower-cased); a column
    is attributed only through a known alias, or bare when the statement reads one table.
    Empty when sqlglot is absent or the SQL does not parse — a statement that cannot be read
    is evidence of nothing."""
    out: Dict[str, Any] = {"tables": [], "filters": [], "joins": [], "groups": [], "selected": []}
    if not sql or not isinstance(sql, str):
        return out
    try:
        import sqlglot
        from sqlglot import exp
        stmts = sqlglot.parse(sql, read=dialect or _dialect_read())
    except Exception:  # noqa: BLE001
        return out
    for stmt in stmts:
        if stmt is None:
            continue
        ctes = {str(c.alias).lower() for c in stmt.find_all(exp.CTE) if c.alias}
        alias: Dict[str, str] = {}
        for t in stmt.find_all(exp.Table):
            leaf = str(t.name or "").lower()
            if not leaf or leaf in ctes:
                continue
            full = f"{str(t.db).lower()}.{leaf}" if t.db else leaf
            alias.setdefault(leaf, full)
            if t.alias:
                alias[str(t.alias).lower()] = full
            if full not in out["tables"]:
                out["tables"].append(full)
        single = out["tables"][0] if len(out["tables"]) == 1 else None

        def owner(col) -> Optional[str]:
            q = str(col.table or "").lower()
            if q:
                return alias.get(q)
            return single

        def add(bucket: str, col, kind: Optional[str] = None) -> None:
            t = owner(col)
            name = str(col.name or "")
            if not t or not name or name == "*":
                return
            rec = {"table": t, "column": name.lower()}
            if kind:
                rec["kind"] = kind
            if rec not in out[bucket]:
                out[bucket].append(rec)

        for where in stmt.find_all(exp.Where):
            for node in where.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
                                       exp.Between, exp.In, exp.Like):
                ranged = isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between))
                dated = ranged and any(isinstance(x, (exp.Date, exp.Cast, exp.CurrentDate, exp.TsOrDsToDate))
                                       or (isinstance(x, exp.Literal) and x.is_string
                                           and len(str(x.this)) >= 8 and str(x.this)[:4].isdigit())
                                       for x in node.find_all(exp.Expression) if x is not node)
                kind = "date_range" if dated else ("range" if ranged else
                                                    "in" if isinstance(node, exp.In) else
                                                    "like" if isinstance(node, exp.Like) else "eq")
                for col in node.find_all(exp.Column):
                    # a column compared with another column is a join predicate, not a filter
                    if isinstance(node, exp.EQ) and isinstance(node.left, exp.Column) \
                            and isinstance(node.right, exp.Column):
                        continue
                    add("filters", col, kind)
        for join in stmt.find_all(exp.Join):
            on = join.args.get("on")
            if on is None:
                continue
            for eq in on.find_all(exp.EQ):
                if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
                    a, b = owner(eq.left), owner(eq.right)
                    if a and b and a != b:
                        rec = {"tables": [a, b], "columns": [str(eq.left.name).lower(), str(eq.right.name).lower()]}
                        if rec not in out["joins"]:
                            out["joins"].append(rec)
        for grp in stmt.find_all(exp.Group):
            for col in grp.find_all(exp.Column):
                add("groups", col)
        sel = stmt if isinstance(stmt, exp.Select) else stmt.find(exp.Select)
        if sel is not None:
            for e in sel.expressions:
                for col in e.find_all(exp.Column):
                    add("selected", col)
    return out


# ── aggregation ────────────────────────────────────────────────────────────────

def aggregate(workload: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Per table: how many statements read it, the EXPLAIN finding kinds seen on statements
    that read it, and per column how often (and in which statements) it was filtered, joined,
    grouped — ``date_range`` counted apart. A workload item is ``{id, sql, findings?,
    est_seconds?, tables?}``; ``findings`` are the plan parser's (``kind`` + ``tables`` when
    the caller knows which base tables a step read)."""
    tables: Dict[str, Dict[str, Any]] = {}
    for item in workload or []:
        if not isinstance(item, dict) or not item.get("sql"):
            continue
        sid = str(item.get("id") or _sid(item["sql"]))
        a = analyze_statement(item["sql"])
        if not a["tables"]:
            continue
        finds = [f for f in (item.get("findings") or []) if isinstance(f, dict) and f.get("kind")]
        kinds = {f["kind"] for f in finds}
        for t in a["tables"]:
            cell = tables.setdefault(t, {"statements": set(), "findings": {}, "columns": {},
                                         "est_seconds": 0.0, "product_join_keys": set()})
            cell["statements"].add(sid)
            for f in finds:
                # a finding that names the tables it read counts only against those tables
                named = [str(x).lower() for x in (f.get("tables") or [])]
                if named and not any(n == t or n.split(".")[-1] == t.split(".")[-1] for n in named):
                    continue
                cell["findings"].setdefault(f["kind"], set()).add(sid)
            cell["est_seconds"] += float(item.get("est_seconds") or 0.0)

        def col(t: str, c: str) -> Dict[str, Any]:
            return tables[t]["columns"].setdefault(c, {"filter": set(), "join": set(), "group": set(),
                                                       "date_range": set(), "selected": set()})
        for f in a["filters"]:
            if f["table"] in tables:
                col(f["table"], f["column"])["filter"].add(sid)
                if f.get("kind") == "date_range":
                    col(f["table"], f["column"])["date_range"].add(sid)
        for j in a["joins"]:
            for t, c in zip(j["tables"], j["columns"]):
                if t in tables:
                    col(t, c)["join"].add(sid)
                    if "product_join" in kinds:
                        tables[t]["product_join_keys"].add(c)
        for g in a["groups"]:
            if g["table"] in tables:
                col(g["table"], g["column"])["group"].add(sid)
        for s in a["selected"]:
            if s["table"] in tables:
                col(s["table"], s["column"])["selected"].add(sid)
    return {"tables": tables}


def _sid(sql: str) -> str:
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()[:12]


# ── recommendation ─────────────────────────────────────────────────────────────

def _used(c: Dict[str, Any]) -> Set[str]:
    return set(c["filter"]) | set(c["join"]) | set(c["group"])


def _fact(facts: Dict[str, Any], table: str) -> Dict[str, Any]:
    f = (facts or {}).get(table)
    if f is None and "." in table:
        f = (facts or {}).get(table.split(".", 1)[1])
    if f is None and "." not in table:     # an unqualified name in the SQL: match the one table by leaf
        hits = [v for k, v in (facts or {}).items() if isinstance(k, str) and k.lower().endswith("." + table)]
        f = hits[0] if len(hits) == 1 else None
    return f if isinstance(f, dict) else {}


def _stats_age(fact: Dict[str, Any], column) -> Optional[float]:
    """Age in days of the statistics on exactly ``column`` (a name, or a list for a multi-column
    statistic, order-insensitive); None when there are none."""
    want = sorted(c.lower() for c in ([column] if isinstance(column, str) else column))
    for k, age in (fact.get("stats") or {}).items():
        cols = sorted(x.strip().lower() for x in (k if isinstance(k, (list, tuple)) else str(k).split(",")))
        if cols == want:
            return float(age) if age is not None else 0.0
    return None


def proposal_id(rule: str, table: str, columns: List[str]) -> str:
    raw = json.dumps([rule, table.lower(), sorted(c.lower() for c in columns)], separators=(",", ":"))
    return "tp_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _proposal(rule_id: str, table: str, columns: List[str], evidence: str, statements: Set[str],
              **extra: Any) -> Dict[str, Any]:
    r = RULE_BY_ID[rule_id]
    return {"id": proposal_id(rule_id, table, columns), "rule": rule_id, "purpose": r.purpose,
            "table": table, "columns": columns, "evidence": evidence, "rationale": r.rationale,
            "statements": sorted(statements), "landing": "direct", **extra}


def _n(n: int, word: str) -> str:
    """``1 statement`` / ``2 statements`` — never a ``(s)`` placeholder on a rendered surface."""
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _v(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def recommend(agg: Dict[str, Any], facts: Optional[Dict[str, Any]] = None,
              *, usage_missed: Iterable[str] = ()) -> Dict[str, Any]:
    """Proposals (never changes) and modelling findings. Deterministic: the same workload and
    facts give the same proposals, in the same order, with the same ids."""
    facts = facts or {}
    props: List[Dict[str, Any]] = []
    stats_done: Set[Tuple[str, str]] = set()

    def stats(rule_id: str, table: str, column: str, why: str, sids: Set[str], **extra: Any) -> None:
        key = (table, column)
        if key in stats_done:
            return
        stats_done.add(key)
        props.append(_proposal(rule_id, table, [column], why, sids, **extra))

    findings: List[Dict[str, Any]] = []
    for table in sorted(agg.get("tables") or {}):
        cell = agg["tables"][table]
        fact = _fact(facts, table)
        if fact.get("kind") == "view":
            # a view carries no statistics, index or partition of its own — the tuning belongs
            # to the tables it reads, and to whoever owns them; said, never proposed
            hot = sorted(((len(_used(v)), c) for c, v in (cell.get("columns") or {}).items() if _used(v)),
                         reverse=True)[:4]
            if not hot:
                continue              # the workload reads it but filters, joins and groups on nothing in it
            base = [b for b in (fact.get("base_tables") or []) if str(b).lower() != table.lower()]
            cols_txt = ", ".join(f"{c} ({_n(n, 'statement')})" for n, c in hot)
            findings.append({"kind": "view", "table": table, "base_tables": base, "columns": [c for _, c in hot],
                             "text": f"{table} is a view — the database keeps statistics and indexes on tables, so "
                                     f"the workload's columns ({cols_txt}) are tuned on "
                                     + (", ".join(base) if base else "the tables it reads")
                                     + ", by whoever owns them; nothing is proposed here"})
            continue
        types = {k.lower(): str(v).upper() for k, v in (fact.get("column_types") or {}).items()}
        key = [k.lower() for k in (fact.get("key") or [])]
        cols = cell["columns"]
        nc = cell["findings"].get("no_confidence", set())

        # product join on a declared key → statistics on the key (both sides arrive per table)
        r = RULE_BY_ID["stats_on_product_join_key"]
        for c in sorted(cell.get("product_join_keys") or ()):
            if c not in key:          # "on a declared key": no declared key, no claim
                continue
            sids = cols.get(c, {}).get("join", set()) & cell["findings"].get("product_join", set())
            if len(sids) >= r.thresholds["min_statements"] and _stats_age(fact, c) is None:
                stats(r.id, table, c, f"{_n(len(sids), 'statement')} joined on {c} and EXPLAIN chose a product "
                                      f"join; {table} has no statistics on {c}", sids)

        for c in sorted(cols):
            used = _used(cols[c])
            if not used:
                continue
            age = _stats_age(fact, c)
            if age is None:
                with_nc = used & nc
                r = RULE_BY_ID["stats_where_no_confidence"]
                if len(with_nc) >= r.thresholds["min_statements"]:
                    stats(r.id, table, c, f"{_n(len(with_nc), 'statement')} using {c} {_v(len(with_nc), 'was', 'were')} planned with no "
                                          f"confidence on {table}; no statistics on {c}", with_nc)
                    continue
                r = RULE_BY_ID["stats_on_workload_columns"]
                if len(used) >= r.thresholds["min_statements"]:
                    stats(r.id, table, c, f"{_n(len(used), 'statement')} {_v(len(used), 'filters, joins or groups', 'filter, join or group')} on {c}; "
                                          f"no statistics on it", used)
            else:
                r = RULE_BY_ID["stats_stale"]
                if age > r.thresholds["max_age_days"] and len(used) >= r.thresholds["min_statements"]:
                    stats(r.id, table, c, f"statistics on {c} are {_n(int(age), 'day')} old and "
                                          f"{_n(len(used), 'statement')} still {_v(len(used), 'uses', 'use')} it", used, recollect=True)

        # covering join index on a wide, large table the workload keeps scanning
        r = RULE_BY_ID["covering_join_index"]
        scans = cell["findings"].get("full_scan", set())
        rows = fact.get("rows") or 0
        ncols = fact.get("columns_count") or len(types)
        if (key and len(scans) >= r.thresholds["min_scan_statements"] and rows >= r.thresholds["min_rows"]
                and ncols >= r.thresholds["min_columns"]):
            pred = sorted({c for c in cols if cols[c]["filter"] & scans} - set(key))
            if pred and len(key) + len(pred) <= r.thresholds["max_index_columns"]:
                covered = any(set(key) | set(pred) <= {x.lower() for x in ji}
                              for ji in (fact.get("join_indexes") or []))
                if not covered:
                    p = _proposal(r.id, table, key + pred,
                                  f"{_n(len(scans), 'statement')} scanned all {_n(rows, 'row')} of {table} ({_n(ncols, 'column')}) "
                                  f"to filter on {_n(len(pred), 'column')}: {', '.join(pred)}",
                                  scans, key=key, predicate_columns=pred)
                    props.append(p)
                    # the index is planned from facts too: ONE statistic on the declared key as a
                    # whole (the optimizer reads a multi-column key's demographics as a group)
                    gk = ("__key__",)
                    if _stats_age(fact, key) is None and (table, gk) not in stats_done:
                        stats_done.add((table, gk))
                        props.append(_proposal("stats_on_workload_columns", table, list(key),
                                               f"the declared key ({', '.join(key)}) of {table} carries the "
                                               f"proposed covering index; no statistics on it as a group", scans))

        # partition by a date column filtered as a range
        r = RULE_BY_ID["partition_on_date_filter"]
        if rows >= r.thresholds["min_rows"] and not fact.get("partitioned"):
            for c in sorted(cols):
                dr = cols[c]["date_range"]
                if len(dr) >= r.thresholds["min_statements"] and any(types.get(c, "").startswith(t) for t in _DATE_TYPES):
                    props.append(_proposal(r.id, table, [c],
                                           f"{_n(len(dr), 'statement')} {_v(len(dr), 'filters', 'filter')} {table} ({_n(rows, 'row')}) by a range of {c}",
                                           dr, landing="rebuild",
                                           note="a partition change on a populated table needs a rebuild — "
                                                "recorded, not landed by the platform"))
                    break

    shown = set(agg.get("tables") or {})
    for t in sorted({str(x).lower() for x in usage_missed or ()}):
        if any(t == s or s.endswith("." + t.split(".")[-1]) for s in shown):
            findings.append({"kind": "modelling", "table": t,
                             "text": f"the agents read {t} but the knowledge graph's enrichment never showed it — "
                                     f"a modelling gap, not a tuning one"})
    order = {"join_index": 0, "statistics": 1, "partition": 2}
    props.sort(key=lambda p: (order[p["purpose"]], p["table"], p["columns"]))
    return {"proposals": props, "findings": findings,
            "rules": [{"id": r.id, "purpose": r.purpose, "thresholds": r.thresholds} for r in RULES]}


def review(workload: Iterable[Dict[str, Any]], facts: Optional[Dict[str, Any]] = None,
           *, usage_missed: Iterable[str] = ()) -> Dict[str, Any]:
    """aggregate + recommend, with the workload size stated (a fresh model reads "no workload yet")."""
    items = [w for w in (workload or []) if isinstance(w, dict) and w.get("sql")]
    out = recommend(aggregate(items), facts, usage_missed=usage_missed)
    out["workload"] = {"statements": len(items)}
    if not items:
        out["note"] = "no workload yet — nothing to recommend until the agents have run statements on this model"
    return out
