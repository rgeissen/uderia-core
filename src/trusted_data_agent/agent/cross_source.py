"""Multi-source implementation plan, stage 3b — the cross-source plan shape, decided by code.

A statement that reads tables held by DIFFERENT MCP servers runs nowhere (the routing layer
refuses it before anything is sent). Until now the only way out was an LLM self-correction, which
can return ONE action — so the model swapped in a query over one server, the phase counted as
done, and the report stated a confident wrong number for the whole (the 3a bench: 10 of 11
cross-server failures had exactly this shape).

This module decides the split deterministically, 0 LLM rounds:

* **one fetch per server** — the columns the statement uses of that server's table, that
  table's own WHERE conjuncts pushed down (only when every join is INNER — a filter on the
  optional side of an outer join changes meaning when moved), nothing else;
* **the ORIGINAL statement's logic** — its joins, aggregates, GROUP BY, ORDER BY, row limit — run
  unchanged over the fetched rows by the existing combine step (`TDA_Compute` op "sql",
  `agent/compute_sql.py`): isolation rules, completeness, key measurement and the sealed receipt
  all apply as for any combine. This module never writes a join the statement did not contain.

What it will NOT split, it declines (returns None) and says why — the caller falls back (the
planner withdraws the statement to the tactical step with the split named; the phase executor
ends the phase with the refusal, never with a one-server substitute). Declined on purpose:

* a table it cannot place (unqualified, or its holder unknown / ambiguous);
* two tables of ONE server joined in the same statement (v1: one table per server);
* a nested SELECT other than ``col IN (SELECT <one column> FROM <one table> …)``;
* CTEs, UNION, ``SELECT *``, a column it cannot attribute to one table.

Pure: sqlglot only. The Teradata → DuckDB rendering of the combine is sqlglot's (``TOP n`` becomes
``LIMIT n``); each fetch is rendered back in the source dialect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: The machine-readable marker a spanning refusal carries (``code`` on the error result), so the
#: phase executor recognises it without reading prose.
SPANS_CODE = "spans_servers"
COMBINE_DIALECT = "duckdb"
MAX_FETCHES = 6

import re as _re
#: A table that is an earlier PHASE's result, written into a raw-SQL statement: `s1`/`p1` (the
#: combine step's own vocabulary) or `result_of_phase_1`. Told to query each server separately
#: and then combine, a planner writes the combine as SQL over the previous phase's rows and sends
#: it to the database, which refuses it ("Object 's1' does not exist"). Such a statement IS a
#: combine: the real tables are fetched, the phase result is read as `p<N>`.
_PHASE_TABLE_RE = _re.compile(r"^(?:[sp](\d{1,3})|result_of_phase_(\d{1,3}))$", _re.IGNORECASE)
_PHASE_SERVER = "phase:"


def phase_ref_number(name: Any) -> Optional[int]:
    m = _PHASE_TABLE_RE.match(str(name or "").strip())
    return int(m.group(1) or m.group(2)) if m else None


def references_phase_result(sql: Any, dialect: str = "teradata") -> bool:
    """True when a FROM/JOIN/IN-subquery table of ``sql`` names a phase result."""
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=dialect)
        return any(not t.db and phase_ref_number(t.name) is not None for t in tree.find_all(exp.Table))
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class Fetch:
    server_id: str
    database: str
    table: str            # the leaf name
    alias: str            # the name the combine keeps for it
    sql: str              # the single-server SELECT, source dialect
    columns: Tuple[str, ...]
    kind: str = "table"   # "table" (a FROM/JOIN table) | "in_subquery" (an IN (...) source)


@dataclass(frozen=True)
class Split:
    fetches: Tuple[Fetch, ...]
    _combine: str         # DuckDB SQL naming each fetch as __f<i>__ and each phase result as __ph<N>__
    phase_refs: Tuple[int, ...] = ()   # the earlier phases the statement reads as tables

    def combine_sql(self, names: Sequence[str], phase_name: Callable[[int], str] = lambda n: f"p{n}") -> str:
        """The combine statement with fetch ``i`` read as ``names[i]`` (``p3`` in a plan written
        before anything ran, ``s7`` at run time) and phase result ``N`` as ``phase_name(N)``."""
        if len(names) != len(self.fetches):
            raise ValueError("one name per fetch")
        out = self._combine
        for i, n in enumerate(names):
            out = out.replace(f"__f{i}__", str(n))
        for n in self.phase_refs:
            out = out.replace(f"__ph{n}__", phase_name(n))
        return out

    @property
    def servers(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for f in self.fetches:
            if f.server_id not in seen:
                seen.append(f.server_id)
        return tuple(seen)


def describe(split: Split, label_of: Callable[[str], str] = lambda s: s) -> str:
    """One sentence naming where each table is."""
    parts = [f"{f.database}.{f.table} is on server '{label_of(f.server_id)}'" for f in split.fetches]
    return "; ".join(parts)


class _Decline(Exception):
    pass


def only_phase_results(sql: Any, dialect: str = "teradata") -> bool:
    """True when EVERY table ``sql`` reads is a phase/statement result (``s1``, ``p2``,
    ``result_of_phase_3``) — a combine written as raw SQL, which no database can run: it belongs
    to the combine step (``TDA_Compute`` op sql), where those names resolve."""
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=dialect)
        tables = [t for t in tree.find_all(exp.Table)]
        return bool(tables) and all(not t.db and phase_ref_number(t.name) is not None for t in tables)
    except Exception:  # noqa: BLE001
        return False


def _lower(s: Any) -> str:
    return str(s or "").strip().lower()


def split_statement(sql: str, server_of: Callable[[str], Optional[str]],
                    columns_of: Optional[Dict[str, Sequence[str]]] = None,
                    dialect: str = "teradata") -> Optional[Split]:
    """Split ``sql`` into one fetch per server plus a combine, or None (nothing to split, or a
    shape this module declines). ``server_of(database)`` names the ONE server holding a
    database (None when unknown or ambiguous). ``columns_of`` (``{"db.table": [cols]}``) lets an
    unqualified column be attributed when exactly one table of the statement has it."""
    try:
        return _split(sql, server_of, columns_of or {}, dialect)
    except _Decline:
        return None
    except Exception:  # noqa: BLE001 — a parse failure is a decline, never an error
        return None


def why_declined(sql: str, server_of: Callable[[str], Optional[str]],
                 columns_of: Optional[Dict[str, Sequence[str]]] = None,
                 dialect: str = "teradata") -> Optional[str]:
    """The reason ``split_statement`` returns None, for the record; None when it would split."""
    try:
        _split(sql, server_of, columns_of or {}, dialect)
        return None
    except _Decline as d:
        return str(d)
    except Exception as exc:  # noqa: BLE001
        return f"the statement could not be parsed ({type(exc).__name__})"


def _split(sql: str, server_of, columns_of: Dict[str, Sequence[str]], dialect: str) -> Optional[Split]:
    import sqlglot
    from sqlglot import exp

    if not isinstance(sql, str) or not sql.strip():
        raise _Decline("no statement")
    tree = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(tree, exp.Select):
        raise _Decline("only a plain SELECT is split (no UNION, no CTE)")
    if tree.args.get("with") or tree.args.get("with_"):
        raise _Decline("a statement with a CTE is not split")
    if any(isinstance(e, exp.Star) for e in tree.expressions) or any(
            isinstance(e, exp.Column) and isinstance(e.this, exp.Star) for e in tree.expressions):
        raise _Decline("SELECT * is not split — name the columns")

    # ── the outer tables: FROM + JOINs, every join with an ON ──
    from_ = tree.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        raise _Decline("the FROM must name a table")
    joins = list(tree.args.get("joins") or [])
    outer_tables: List[exp.Table] = [from_.this]
    all_inner = True
    for j in joins:
        if not isinstance(j.this, exp.Table):
            raise _Decline("a derived table in a JOIN is not split")
        if j.args.get("on") is None:
            raise _Decline("a join without an ON condition is not split")
        side = _lower(j.side)
        if side and side != "inner":
            all_inner = False
        outer_tables.append(j.this)

    # ── nested SELECTs: only `col IN (SELECT one_column FROM one_table …)` ──
    in_subqueries: List[Tuple[exp.In, exp.Select]] = []
    for sel in tree.find_all(exp.Select):
        if sel is tree:
            continue
        parent_in = sel.parent
        while parent_in is not None and isinstance(parent_in, (exp.Subquery, exp.Paren)):
            parent_in = parent_in.parent
        if not isinstance(parent_in, exp.In):
            raise _Decline("only an IN (SELECT …) subquery is split")
        in_subqueries.append((parent_in, sel))

    def _place(t: exp.Table) -> Tuple[str, str, str]:
        db, name = _lower(t.db), _lower(t.name)
        if not db and phase_ref_number(t.name) is not None:
            return "", name, f"{_PHASE_SERVER}{phase_ref_number(t.name)}"   # an earlier phase's rows
        if not db:
            raise _Decline(f"table {t.name} is not qualified with its database")
        sid = server_of(db)
        if not sid:
            raise _Decline(f"no single server is known to hold {db}")
        return db, name, str(sid)

    placed: Dict[int, Tuple[str, str, str]] = {}      # id(table node) → (db, leaf, server)
    for t in outer_tables:
        placed[id(t)] = _place(t)
    sub_tables: Dict[int, exp.Table] = {}
    for _in, sel in in_subqueries:
        sf = sel.args.get("from_")
        if (sf is None or not isinstance(sf.this, exp.Table) or sel.args.get("joins")
                or sel.args.get("with") or sel.args.get("with_")):
            raise _Decline("an IN subquery must read one table")
        if len(sel.expressions) != 1:
            raise _Decline("an IN subquery must select one column")
        placed[id(sf.this)] = _place(sf.this)
        sub_tables[id(sel)] = sf.this

    servers = {v[2] for v in placed.values()}
    phase_refs = sorted({int(v[2][len(_PHASE_SERVER):]) for v in placed.values() if v[2].startswith(_PHASE_SERVER)})
    real_servers = {v for v in servers if not v.startswith(_PHASE_SERVER)}
    if not phase_refs and len(real_servers) < 2:
        return None                                  # nothing to split
    by_server: Dict[str, List[int]] = {}
    for t in outer_tables:
        sid = placed[id(t)][2]
        if not sid.startswith(_PHASE_SERVER):
            by_server.setdefault(sid, []).append(id(t))
    if any(len(v) > 1 for v in by_server.values()):
        raise _Decline("two tables of one server in the same statement (not split in this version)")

    # ── attribute every column of the outer statement to one outer table ──
    alias_of: Dict[str, int] = {}                     # qualifier (lower) → id(table)
    for t in outer_tables:
        alias_of[_lower(t.alias or t.name)] = id(t)
        alias_of[_lower(t.name)] = alias_of.get(_lower(t.name), id(t))
    select_aliases = {_lower(e.alias) for e in tree.expressions if isinstance(e, exp.Alias)}

    def _table_cols(tid: int) -> set:
        db, leaf, _ = placed[tid]
        for k, v in columns_of.items():
            kd, _, kl = _lower(k).rpartition(".")
            if kl == leaf and (not kd or kd == db):
                return {_lower(c) for c in v}
        return set()

    def _attribute(col: exp.Column, scope_tables: List[int]) -> Optional[int]:
        q = _lower(col.table)
        if q:
            tid = alias_of.get(q)
            if tid is None or tid not in scope_tables:
                raise _Decline(f"column qualifier '{col.table}' names no table of the statement")
            return tid
        if len(scope_tables) == 1:
            return scope_tables[0]
        if _lower(col.name) in select_aliases and col.find_ancestor(exp.Order, exp.Group, exp.Having) is not None:
            return None                              # an expression alias, not a table column
        holders = [tid for tid in scope_tables if _lower(col.name) in _table_cols(tid)]
        if len(holders) == 1:
            return holders[0]
        raise _Decline(f"column '{col.name}' is not qualified and could belong to several tables")

    needed: Dict[int, List[str]] = {id(t): [] for t in outer_tables}

    def _need(tid: int, name: str) -> None:
        if name not in needed[tid]:
            needed[tid].append(name)

    outer_ids = [id(t) for t in outer_tables]
    subquery_nodes = [sel for _, sel in in_subqueries]
    for col in tree.find_all(exp.Column):
        if any(col.find_ancestor(exp.Select) is s for s in subquery_nodes):
            continue                                  # judged with its subquery below
        tid = _attribute(col, outer_ids)
        if tid is not None:
            _need(tid, col.name)

    # ── WHERE: push a one-table conjunct down (INNER joins only), keep the rest ──
    pushed: Dict[int, List[exp.Expression]] = {tid: [] for tid in outer_ids}
    kept: List[exp.Expression] = []
    where = tree.args.get("where")
    conjuncts = list(where.this.flatten()) if where is not None and isinstance(where.this, exp.And) \
        else ([where.this] if where is not None else [])
    for c in conjuncts:
        if any(isinstance(n, exp.In) and any(isinstance(x, (exp.Select, exp.Subquery)) for x in (n.args.get("expressions") or []) + [n.args.get("query")] if x is not None)
               for n in [c] + list(c.find_all(exp.In))):
            kept.append(c)
            continue
        refs = set()
        for col in c.find_all(exp.Column):
            tid = _attribute(col, outer_ids)
            if tid is not None:
                refs.add(tid)
        if len(refs) == 1 and all_inner:
            pushed[next(iter(refs))].append(c)
        else:
            kept.append(c)

    # ── the fetches ──
    fetches: List[Fetch] = []
    names: List[str] = []
    outer_name: Dict[int, str] = {}                  # id(table) → its name in the combine
    for t in outer_tables:
        tid = id(t)
        db, leaf, sid = placed[tid]
        alias = t.alias or t.name
        if sid.startswith(_PHASE_SERVER):
            outer_name[tid] = f"__ph{sid[len(_PHASE_SERVER):]}__"
            continue                                  # an earlier phase's rows: nothing to fetch
        cols = needed[tid]
        if not cols:
            raise _Decline(f"the statement uses no column of {t.db}.{t.name}")
        sel = exp.select(*[exp.column(c, table=alias) for c in cols]).from_(
            exp.Table(this=exp.to_identifier(t.name), db=exp.to_identifier(t.db),
                      alias=exp.TableAlias(this=exp.to_identifier(alias))))
        if pushed[tid]:
            cond = pushed[tid][0]
            for extra in pushed[tid][1:]:
                cond = exp.and_(cond, extra)
            sel = sel.where(cond.copy())
        fetches.append(Fetch(sid, t.db, t.name, alias, sel.sql(dialect=dialect), tuple(cols)))
        outer_name[tid] = f"__f{len(fetches) - 1}__"
        names.append(outer_name[tid])
    sub_index: Dict[int, int] = {}
    sub_phase: Dict[int, str] = {}
    for _in, sel in in_subqueries:
        st = sub_tables[id(sel)]
        db, leaf, sid = placed[id(st)]
        if sid.startswith(_PHASE_SERVER):
            sub_phase[id(sel)] = f"__ph{sid[len(_PHASE_SERVER):]}__"   # read as written, over the phase rows
            continue
        proj = sel.expressions[0]
        col = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(proj, exp.Distinct) and proj.expressions:
            col = proj.expressions[0]
        if not isinstance(col, exp.Column):
            raise _Decline("an IN subquery must select a plain column")
        out_name = proj.alias if isinstance(proj, exp.Alias) and proj.alias else col.name
        for c in sel.find_all(exp.Column):
            if c.table and _lower(c.table) not in (_lower(st.alias), _lower(st.name)):
                raise _Decline("an IN subquery may not reference the outer statement")
        fetches.append(Fetch(sid, st.db, st.name, st.alias or st.name, sel.sql(dialect=dialect), (out_name,), "in_subquery"))
        sub_index[id(sel)] = len(fetches) - 1
        names.append(f"__f{len(fetches) - 1}__")
    if len(fetches) > MAX_FETCHES:
        raise _Decline(f"more than {MAX_FETCHES} sources in one statement")

    # ── the combine: the original statement over the fetches ──
    combine = tree.copy()
    c_outer = [combine.args["from_"].this] + [j.this for j in (combine.args.get("joins") or [])]
    for t, orig in zip(c_outer, outer_tables):
        t.replace(exp.Table(this=exp.to_identifier(outer_name[id(orig)]),
                            alias=exp.TableAlias(this=exp.to_identifier(t.alias or t.name))))
    # the kept conjuncts first (the pushed ones are gone), THEN the nested selects they still
    # carry are pointed at their fetches — nested selects keep document order across the copy
    if where is not None:
        if kept:
            cond = kept[0].copy()
            for extra in kept[1:]:
                cond = exp.and_(cond, extra.copy())
            combine.set("where", exp.Where(this=cond))
        else:
            combine.set("where", None)
    nested_copy = [s for s in combine.find_all(exp.Select) if s is not combine]
    if len(nested_copy) != len(in_subqueries):
        raise _Decline("the nested selects could not be matched to their fetches")
    for (_in, orig), sel in zip(in_subqueries, nested_copy):
        if id(orig) in sub_phase:
            sf2 = sel.args.get("from_")
            sf2.this.replace(exp.Table(this=exp.to_identifier(sub_phase[id(orig)]),
                                       alias=sf2.this.args.get("alias")))
            continue
        fi = sub_index[id(orig)]
        col = fetches[fi].columns[0]
        sel.replace(exp.select(exp.column(col)).from_(exp.Table(this=exp.to_identifier(f"__f{fi}__"))))
    return Split(tuple(fetches), combine.sql(dialect=COMBINE_DIALECT), tuple(phase_refs))
