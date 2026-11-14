"""Preflight JOIN-key check — pure + deterministic, 0 tokens.

The class it closes (2026-09-04): an agent joined two tables on columns they merely share by
name (batch-header fields) instead of the key the product declares, and the near-cartesian
product ran for three hours, queueing every other request on the endpoint. The ontology
enrichment now NAMES a declared pair's join columns; this guard checks the SQL against the
same declaration before the database sees it.

Per session, the KG enrichment records ``join_keys`` — [{"tables": [leaf_a, leaf_b], "key":
[cols]}] — and every raw-SQL tool call is parsed (sqlglot): a JOIN between two declared tables
whose ON equalities do not cover every declared key column is a violation. No recorded keys,
an unparseable statement, a join between undeclared tables: no verdict (fail-open by
construction — the guard can only refuse a join it can PROVE is off-key).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.agent.preflight import dialect as _dialect

_KEYS: Dict[str, Dict[str, Any]] = {}   # session_id -> {"pairs": {frozenset(leaves): [key]}, "ts": monotonic}
_LOCK = threading.Lock()
_TTL_SECONDS = 1800


def record_join_keys(session_id: str, join_keys: Any) -> int:
    """Record the declared join keys for a session (union with what is already known —
    an out-of-domain turn's empty enrichment must not erase an earlier turn's keys).
    Returns the number of pairs known afterwards."""
    if not session_id or not isinstance(join_keys, list):
        return 0
    pairs: Dict[frozenset, List[str]] = {}
    for jk in join_keys:
        if not isinstance(jk, dict):
            continue
        tables = [str(t).strip().lower() for t in (jk.get("tables") or []) if str(t).strip()]
        key = [str(k).strip() for k in (jk.get("key") or []) if str(k).strip()]
        if len(tables) == 2 and key and tables[0] != tables[1]:
            pairs[frozenset(tables)] = key
    if not pairs:
        return len(known_pairs(session_id))
    with _LOCK:
        entry = _KEYS.get(session_id) or {"pairs": {}}
        entry["pairs"].update(pairs)
        entry["ts"] = time.monotonic()
        _KEYS[session_id] = entry
        if len(_KEYS) > 1000:
            now = time.monotonic()
            for sid in [s for s, e in _KEYS.items() if now - e["ts"] > _TTL_SECONDS]:
                _KEYS.pop(sid, None)
        return len(entry["pairs"])


def _entry(session_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        entry = _KEYS.get(session_id)
        if not entry:
            return None
        if time.monotonic() - entry["ts"] > _TTL_SECONDS:
            _KEYS.pop(session_id, None)
            return None
        return entry


def record_fanout(session_id: str, fanout_keys: Any) -> int:
    """Record the fan-out keys — columns two tables both hold as a FOREIGN key to one owner
    (union). Returns the number of columns known afterwards."""
    if not session_id or not isinstance(fanout_keys, list):
        return len(known_fanout(session_id))
    add: Dict[str, Dict[str, Any]] = {}
    for f in fanout_keys:
        if not isinstance(f, dict):
            continue
        col = str(f.get("column") or "").strip().lower()
        tables = {str(t).strip().lower() for t in (f.get("tables") or []) if str(t).strip()}
        if col and len(tables) >= 2:
            add[col] = {"owner": (str(f.get("owner")).strip().lower() if f.get("owner") else None), "tables": tables}
    if not add:
        return len(known_fanout(session_id))
    with _LOCK:
        entry = _KEYS.get(session_id) or {"pairs": {}}
        fo = entry.setdefault("fanout", {})
        for col, rec in add.items():
            cur = fo.setdefault(col, {"owner": rec["owner"], "tables": set()})
            cur["tables"] |= rec["tables"]
            cur["owner"] = cur["owner"] or rec["owner"]
        entry["ts"] = time.monotonic()
        _KEYS[session_id] = entry
        return len(fo)


def known_fanout(session_id: str) -> Dict[str, Dict[str, Any]]:
    if not session_id:
        return {}
    entry = _entry(session_id)
    return {c: {"owner": r["owner"], "tables": set(r["tables"])} for c, r in (entry or {}).get("fanout", {}).items()}


def known_pairs(session_id: str) -> Dict[frozenset, List[str]]:
    if not session_id:
        return {}
    entry = _entry(session_id)
    return dict(entry["pairs"]) if entry else {}


def record_domains(session_id: str, subject_areas: Any, class_bridges: Any = None) -> int:
    """Record the model's domain partition — ``{leaf: subject_area}`` — and its declared
    class-level bridges for a session (union, like the keys). Returns the number of
    tables whose area is known afterwards."""
    if not session_id or not isinstance(subject_areas, dict) or not subject_areas:
        return len(known_areas(session_id))
    areas = {str(k).strip().lower(): str(v).strip().lower()
             for k, v in subject_areas.items() if str(k).strip() and str(v).strip()}
    bridges = [b for b in (class_bridges or []) if isinstance(b, dict)]
    with _LOCK:
        entry = _KEYS.get(session_id) or {"pairs": {}}
        entry.setdefault("areas", {}).update(areas)
        known_names = {str(b.get("name")) for b in entry.setdefault("bridges", [])}
        entry["bridges"].extend(b for b in bridges if str(b.get("name")) not in known_names)
        entry["ts"] = time.monotonic()
        _KEYS[session_id] = entry
        return len(entry["areas"])


def known_areas(session_id: str) -> Dict[str, str]:
    entry = _entry(session_id) if session_id else None
    return dict(entry.get("areas") or {}) if entry else {}


def known_bridges(session_id: str) -> List[Dict[str, Any]]:
    entry = _entry(session_id) if session_id else None
    return list(entry.get("bridges") or []) if entry else []


def record_from_event(session_id: str, event: Any) -> int:
    """Record from a ``kg_enrichment_event`` (its ``enrichments[*].join_keys``, and the
    ``subject_areas`` / ``class_bridges`` partition beside them). Never raises."""
    try:
        if not isinstance(event, dict):
            return 0
        for enr in event.get("enrichments") or []:
            if not isinstance(enr, dict):
                continue
            if enr.get("join_keys"):
                record_join_keys(session_id, enr.get("join_keys"))
            if enr.get("fanout_keys"):
                record_fanout(session_id, enr.get("fanout_keys"))
            if enr.get("subject_areas"):
                record_domains(session_id, enr.get("subject_areas"), enr.get("class_bridges"))
        return len(known_pairs(session_id))
    except Exception:
        return 0


def sql_joins(sql: str) -> List[Dict[str, Any]]:
    """Pure: the JOINs in ``sql`` as [{"tables": [leaf_a, leaf_b], "on_columns": [col, ...]}]
    — the two tables an ON clause relates (by leaf name, lower) and every column named in
    its equalities. Empty when sqlglot is absent or the SQL does not parse."""
    out: List[Dict[str, Any]] = []
    if not sql or not isinstance(sql, str):
        return out
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return out
    try:
        stmts = sqlglot.parse(sql, read=_dialect.read())
    except Exception:
        return out
    for stmt in stmts:
        if stmt is None:
            continue
        for join in stmt.find_all(exp.Join):
            on = join.args.get("on")
            if on is None:
                continue
            # Qualifiers resolve against the join's OWN scope — the FROM/JOIN sources of the
            # SELECT that owns it. A base table maps to its leaf; a DERIVED table maps to
            # nothing: `(SELECT … GROUP BY k) AS orders JOIN (SELECT …) AS shipments` is a join
            # between two aggregates, and reading its aliases as the base tables of the same
            # name made a correct pre-aggregated join look like the fan-out it replaced
            # (2026-09-19). What the parse cannot attribute proves nothing (fail-open).
            alias_to_leaf: Dict[str, str] = {}
            owner = join.parent if isinstance(join.parent, exp.Select) else None
            sources = []
            if owner is not None:
                frm = owner.args.get("from_") or owner.args.get("from")
                if frm is not None:
                    sources.append(frm.this)
                sources.extend(j.this for j in (owner.args.get("joins") or []))
            else:
                sources.append(join.this)
            for src in sources:
                if isinstance(src, exp.Table):
                    leaf = str(src.name or "").lower()
                    if not leaf:
                        continue
                    alias_to_leaf.setdefault(leaf, leaf)
                    if src.alias:
                        alias_to_leaf[str(src.alias).lower()] = leaf
                elif isinstance(src, exp.Subquery) and src.alias:
                    alias_to_leaf[str(src.alias).lower()] = ""
            leaves: List[str] = []
            cols: List[str] = []
            by_table: Dict[str, List[str]] = {}
            for eq in on.find_all(exp.EQ):
                l, r = eq.left, eq.right
                if isinstance(l, exp.Column) and isinstance(r, exp.Column):
                    for c in (l, r):
                        q = str(c.table or "").lower()
                        leaf = alias_to_leaf.get(q, q) if q else ""
                        if leaf and leaf not in leaves:
                            leaves.append(leaf)
                        cols.append(str(c.name))
                        if leaf:
                            by_table.setdefault(leaf, []).append(str(c.name))
            # a bare-column ON (no qualifiers) relates the joined table to the FROM table;
            # without qualifiers the pair is not provable — leave it to the database
            if len(leaves) == 2:
                # `by_table`: which columns each SIDE named — a same-name key
                # (`ffp_member_id` on both tables) is only joined ON when BOTH sides
                # reference it; the flat `on_columns` cannot tell `A.ffp_member_id =
                # M.ffp_member_id` from `A.booking_segment_id = M.ffp_member_id`.
                out.append({"tables": leaves, "on_columns": cols, "by_table": by_table})
    return out


def join_violations(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """JOINs in ``sql`` between two tables with a DECLARED key whose ON equalities do not
    cover every key column. Each violation names the tables, the declared key, the columns
    used and the missing ones. Empty = nothing provable (never a false positive by design)."""
    pairs = known_pairs(session_id)
    if not pairs:
        return []
    out: List[Dict[str, Any]] = []
    for j in sql_joins(sql):
        key = pairs.get(frozenset(j["tables"]))
        if not key:
            continue
        used = {c.lower() for c in j["on_columns"]}
        missing = [k for k in key if k.lower() not in used]
        # Per SIDE (2026-09-19, prod session 0aa3a650 turn 1): the model joined
        # `mileage_accrual A ... ON A.booking_segment_id = M.ffp_member_id` — the key
        # column appears in the ON, on the WRONG side. A key is joined on only when
        # every side names every key column; a side the parse could not attribute
        # (no qualifier) proves nothing and is left alone, as before.
        by_table = j.get("by_table") or {}
        if not missing and by_table and len(by_table) == 2:
            for leaf, side_cols in by_table.items():
                side = {c.lower() for c in side_cols}
                for k in key:
                    if k.lower() not in side and k not in missing:
                        missing.append(k)
        if missing:
            out.append({"tables": j["tables"], "key": key, "used": sorted(used), "missing": missing})
    return out


def sql_scope_tables(sql: str) -> List[List[str]]:
    """Pure: for every SELECT scope in ``sql``, the leaf names (lower) of the tables it can
    RELATE — its own FROM/JOIN tables plus those of every enclosing SELECT (a correlated
    subquery relates its table to the outer one). One list per scope, sorted. A UNION's
    branches are separate scopes (two populations side by side relate nothing). Empty when
    sqlglot is absent or the SQL does not parse.

    This is the CROSS-domain question's unit: an ON clause is only one way to relate two
    tables — a CROSS JOIN, a comma list, an `ON 1=1`, a correlated EXISTS relate them just
    as surely, with no key at all. Seen live 2026-09-15: refused on the keyed join, the
    agent's self-correction wrote `v_Offence CROSS JOIN v_CaseObject` and reported "44
    exhibits per robbery"."""
    out: List[List[str]] = []
    if not sql or not isinstance(sql, str):
        return out
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return out
    try:
        stmts = sqlglot.parse(sql, read=_dialect.read())
    except Exception:
        return out

    def _own_tables(sel) -> List[str]:
        leaves: List[str] = []
        # sqlglot names the FROM arg `from_` (≥ 26) or `from` (older); read both
        frm = sel.args.get("from_") if sel.args.get("from_") is not None else sel.args.get("from")
        srcs = []
        if frm is not None:
            srcs.append(frm.this)
        for j in sel.args.get("joins") or []:
            srcs.append(j.this)
        for src in srcs:
            if isinstance(src, exp.Table):
                leaf = str(src.name or "").lower()
                if leaf and leaf not in leaves:
                    leaves.append(leaf)
        return leaves

    for stmt in stmts:
        if stmt is None:
            continue
        for sel in stmt.find_all(exp.Select):
            scope = list(_own_tables(sel))
            anc = sel.parent
            while anc is not None:
                if isinstance(anc, exp.Select):
                    for t in _own_tables(anc):
                        if t not in scope:
                            scope.append(t)
                anc = anc.parent
            if scope:
                out.append(sorted(scope))
    return out


def cross_domain_violations(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """Two tables the model places in DIFFERENT subject areas, RELATED in one SELECT scope
    with NO declared per-record key between them — the instance link a class-level
    correspondence forbids. "Related" is any co-presence in a scope (`sql_scope_tables`):
    a keyed JOIN, a CROSS JOIN, a comma list, a constant ON, a correlated subquery. Each
    violation names the tables, their areas and the bridge (if any) the model declares
    between those areas. Silent unless BOTH tables' areas are known (never a false
    positive: a graph declaring no areas yields nothing; a UNION of two single-area
    populations relates nothing)."""
    areas = known_areas(session_id)
    if not areas:
        return []
    pairs = known_pairs(session_id)
    bridges = known_bridges(session_id)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for scope in sql_scope_tables(sql):
        # Two tables are legitimately related when a CHAIN of declared keys connects them
        # inside this scope (Offence ⋈ Incident ⋈ ChainOfCustody with Incident↔ChainOfCustody
        # declared relates Offence to ChainOfCustody through declared keys). Union-find over
        # the declared pairs restricted to the scope's tables.
        comp = {t: t for t in scope}

        def _find(x: str) -> str:
            while comp[x] != x:
                comp[x] = comp[comp[x]]
                x = comp[x]
            return x

        for pr in pairs:
            ts = [t for t in pr if t in comp]
            if len(ts) == 2:
                comp[_find(ts[0])] = _find(ts[1])
        for i, a in enumerate(scope):
            for b in scope[i + 1:]:
                aa, ab = areas.get(a), areas.get(b)
                if not aa or not ab or aa == ab:
                    continue
                if _find(a) == _find(b):
                    continue  # connected by DECLARED keys — the model permits this relation
                if frozenset((a, b)) in seen:
                    continue
                seen.add(frozenset((a, b)))
                bridge = next((br for br in bridges
                               if {aa, ab} <= set(br.get("areas") or [])), None)
                out.append({"tables": [a, b], "areas": [aa, ab],
                            "bridge": (bridge or {}).get("name"), "basis": (bridge or {}).get("basis")})
    return out


def fanout_violations(session_id: str, sql: str) -> List[Dict[str, Any]]:
    """JOINs between two tables that BOTH hold the ON column as a foreign key to a third
    (owner) table, with no DECLARED pair between them — fact-to-fact through a dimension key.
    Each names the two tables, the column, the owner and the remedy. Empty = nothing provable
    (a pair the model declares, a side the parse cannot attribute, no recorded fan-out)."""
    fo = known_fanout(session_id)
    if not fo:
        return []
    pairs = known_pairs(session_id)
    out: List[Dict[str, Any]] = []
    for j in sql_joins(sql):
        a, b = j["tables"]
        if frozenset((a, b)) in pairs:
            continue                                  # a declared relationship between the two
        by_table = j.get("by_table") or {}
        if len(by_table) != 2:
            continue
        for col, rec in fo.items():
            if a in rec["tables"] and b in rec["tables"] \
                    and col in {c.lower() for c in by_table.get(a, [])} \
                    and col in {c.lower() for c in by_table.get(b, [])}:
                out.append({"tables": [a, b], "column": col, "owner": rec.get("owner")})
                break
    return out


def fanout_block_result(violations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recovery-shaped: names the fan-out and the two correct shapes; the query was NOT run."""
    v = violations[0]
    a, b = v["tables"]
    owner = v.get("owner") or "the table that owns the key"
    msg = (f"[Preflight] JOIN between {a} and {b} ON {v['column']} multiplies rows: both tables hold "
           f"{v['column']} as a FOREIGN key to {owner}, and neither owns the other — every {a} row of a key "
           f"value is paired with every {b} row of the same value, so a per-key total comes out many times too "
           f"large. The query was NOT executed. Either aggregate {a} and {b} separately per {v['column']} and join "
           f"the two aggregates, or join each of them to {owner} and read the other table's columns from a "
           f"separate query.")
    return {"status": "error", "preflight": True, "preflight_join": True, "preflight_fanout": True,
            "error": f"Fan-out join between {a} and {b} on {v['column']}", "data": msg, "violations": violations}


# ── the mechanical repair of a refused fan-out ─────────────────────────────────
#
# Prod 2026-09-19 (session 940f02fa, @IDAIRL on gemini-3.1-flash-lite): the guard refused
# `mileage_redemption ⋈ member_benefit ON ffp_member_id`, and the model's OWN first
# correction — `(SELECT k, SUM(m) … GROUP BY k) R JOIN member_benefit B` — passed the
# guard (correct arithmetic) but returned member 2 FIVE times under `TOP 5` (28 distinct
# tiers across its 149 benefit rows), so it spent one to two further rounds reducing the
# other side, one of them a 46K-token probe. The shape it wanted is mechanical: aggregate
# each holder at ITS OWN grain, then join the aggregates — and rank (TOP … ORDER BY) INSIDE
# the side the ORDER BY belongs to, so the TOP counts members, not the repeats the other
# side contributes. What stays the model's call is declined, not guessed: an expression
# mixing both tables, a bare COUNT(*) (it counted the multiplied rows — what it meant is
# unknowable), a third table, a subquery, a window. A decline is today's refusal.

_AGG_NAMES = {"SUM", "COUNT", "MIN", "MAX", "AVG"}


def _side_of(node, alias_to_side: Dict[str, str]) -> Optional[str]:
    """The ONE side every column of ``node`` belongs to ('a' | 'b'), None if none, or
    'mixed' / 'unknown' when it references both or a column the parse cannot attribute."""
    from sqlglot import exp
    sides = set()
    for c in node.find_all(exp.Column):
        q = str(c.table or "").lower()
        side = alias_to_side.get(q) if q else None
        if side is None:
            return "unknown"
        sides.add(side)
    if len(sides) > 1:
        return "mixed"
    return next(iter(sides)) if sides else None


def _strip_qualifiers(node):
    """A copy of ``node`` with every column's table qualifier removed (single-table scope)."""
    from sqlglot import exp
    node = node.copy()
    for c in node.find_all(exp.Column):
        c.set("table", None)
    return node


def rewrite_fanout_sql(session_id: str, sql: str) -> Optional[Dict[str, Any]]:
    """The mechanical correction of a statement the fan-out guard refused: each holder is
    aggregated at its own grain in a derived table (its key, its grouped columns, its
    aggregates, its own WHERE conjuncts), the two aggregates are joined on the key, and a
    `TOP n … ORDER BY` whose every term belongs to ONE side is pushed into that side so the
    TOP ranks that side's rows rather than the join's repeats. The model's GROUP BY grain is
    preserved exactly — a column the other side contributes ungrouped still repeats per key,
    and the result names that grain so the repeat is visible, never silent.

    Returns ``{"sql", "changes", "pair", "column", "grain", "top_pushed", "repeats_side"}`` or
    None whenever ANY judgement would be involved (declines, each today's refusal): more than
    one statement or fan-out, a CTE / subquery / UNION / window / `SELECT *`, a third table,
    a RIGHT/FULL join, an ON beyond the single key equality, a projection that is neither a
    bare column nor a bare aggregate, an aggregate or predicate mixing both tables, a bare
    `COUNT(*)`, a column the parse cannot attribute, GROUP BY on anything but columns,
    `TOP … WITH TIES/PERCENT`, or a rewrite that fails to re-parse or that the guard would
    still refuse. Never raises."""
    try:
        import sqlglot
        from sqlglot import exp
        viol = fanout_violations(session_id, sql)
        if len(viol) != 1:
            return None
        v = viol[0]
        key = str(v["column"]).lower()
        stmts = sqlglot.parse(sql, read="teradata")
        if len(stmts) != 1 or not isinstance(stmts[0], exp.Select):
            return None
        stmt = stmts[0]
        if stmt.args.get("with_") or stmt.args.get("with") or stmt.args.get("qualify") \
                or list(stmt.find_all(exp.Subquery)) or list(stmt.find_all(exp.Window)) \
                or list(stmt.find_all(exp.Star)) or len(list(stmt.find_all(exp.Select))) != 1:
            return None
        tables = list(stmt.find_all(exp.Table))
        joins = stmt.args.get("joins") or []
        from_ = stmt.args.get("from_") or stmt.args.get("from")
        if len(tables) != 2 or len(joins) != 1 or from_ is None or not isinstance(from_.this, exp.Table):
            return None
        join = joins[0]
        if not isinstance(join.this, exp.Table):
            return None
        side_kw = str(join.args.get("side") or "").upper()
        kind_kw = str(join.args.get("kind") or "").upper()
        if side_kw not in ("", "LEFT", "INNER") or kind_kw not in ("", "INNER"):
            return None
        t_a, t_b = from_.this, join.this
        leaf_a, leaf_b = str(t_a.name).lower(), str(t_b.name).lower()
        if {leaf_a, leaf_b} != set(v["tables"]):
            return None
        alias_a = str(t_a.alias or t_a.name)
        alias_b = str(t_b.alias or t_b.name)
        if alias_a.lower() == alias_b.lower():
            return None
        alias_to_side = {alias_a.lower(): "a", alias_b.lower(): "b", leaf_a: "a", leaf_b: "b"}
        # ON: exactly `X.key = Y.key`
        on = join.args.get("on")
        if not isinstance(on, exp.EQ) or not isinstance(on.left, exp.Column) or not isinstance(on.right, exp.Column):
            return None
        if {str(on.left.name).lower(), str(on.right.name).lower()} != {key} \
                or {alias_to_side.get(str(on.left.table or "").lower()), alias_to_side.get(str(on.right.table or "").lower())} != {"a", "b"}:
            return None
        lim = stmt.args.get("limit")
        if lim is not None:
            lo = lim.args.get("limit_options")
            if lim.args.get("offset") is not None or not isinstance(lim.expression, exp.Literal) \
                    or (lo is not None and (lo.args.get("percent") or lo.args.get("with_ties"))):
                return None

        # ── classify the projections: bare column or bare aggregate, one side each ───
        sides: Dict[str, Dict[str, Any]] = {
            "a": {"alias": alias_a, "table": t_a, "dims": [], "aggs": [], "where": []},
            "b": {"alias": alias_b, "table": t_b, "dims": [], "aggs": [], "where": []},
        }
        outer_select: List[Any] = []
        agg_alias_by_sql: Dict[str, Tuple[str, str]] = {}    # agg sql → (side, alias)
        proj_alias_side: Dict[str, str] = {}                  # output alias → side
        n_agg = 0

        def _register_agg(agg) -> Optional[Tuple[str, str]]:
            """Place an aggregate on its side (once per distinct text); returns (side, alias)."""
            nonlocal n_agg
            if not isinstance(agg, exp.AggFunc) or str(agg.sql_name()).upper() not in _AGG_NAMES:
                return None
            if isinstance(agg.this, exp.Star) or not list(agg.find_all(exp.Column)):
                return None                                   # COUNT(*) / COUNT(1): grain unknowable
            if list(agg.find_all(exp.AggFunc))[1:]:
                return None                                   # nested aggregate
            side = _side_of(agg, alias_to_side)
            if side not in ("a", "b"):
                return None
            k = agg.sql(dialect="teradata").lower()
            if k in agg_alias_by_sql:
                return agg_alias_by_sql[k]
            n_agg += 1
            name = f"agg_{n_agg}"
            sides[side]["aggs"].append(exp.alias_(_strip_qualifiers(agg), name))
            agg_alias_by_sql[k] = (side, name)
            return side, name

        for e in stmt.expressions:
            inner, out_alias = (e.this, str(e.alias)) if isinstance(e, exp.Alias) else (e, None)
            if isinstance(inner, exp.Column):
                side = _side_of(inner, alias_to_side)
                if side not in ("a", "b"):
                    return None
                col = str(inner.name)
                if col.lower() not in [d.lower() for d in sides[side]["dims"]]:
                    sides[side]["dims"].append(col)
                ref = exp.column(col, table=sides[side]["alias"])
                outer_select.append(exp.alias_(ref, out_alias) if out_alias else ref)
                if out_alias:
                    proj_alias_side[out_alias.lower()] = side
                continue
            placed = _register_agg(inner)
            if placed is None:
                return None
            side, name = placed
            if out_alias:
                # the model's own name is the derived column's name
                sides[side]["aggs"][-1] = exp.alias_(sides[side]["aggs"][-1].this, out_alias)
                agg_alias_by_sql[inner.sql(dialect="teradata").lower()] = (side, out_alias)
                name = out_alias
                proj_alias_side[out_alias.lower()] = side
            outer_select.append(exp.column(name, table=sides[side]["alias"]))
        if not outer_select:
            return None

        # ── GROUP BY: every term a column of one side → that side's dims ─────────────
        group = stmt.args.get("group")
        for g in (group.expressions if group is not None else []):
            if not isinstance(g, exp.Column):
                return None
            side = _side_of(g, alias_to_side)
            if side not in ("a", "b"):
                return None
            col = str(g.name)
            if col.lower() not in [d.lower() for d in sides[side]["dims"]]:
                sides[side]["dims"].append(col)

        # ── WHERE: each top-level AND conjunct belongs to one side (or to no column) ──
        outer_where: List[Any] = []
        where = stmt.args.get("where")
        if where is not None:
            conj = list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]
            for c in conj:
                if list(c.find_all(exp.AggFunc)):
                    return None
                side = _side_of(c, alias_to_side)
                if side in ("mixed", "unknown"):
                    return None
                if side is None:
                    outer_where.append(c.copy())
                else:
                    sides[side]["where"].append(_strip_qualifiers(c))

        # ── HAVING: aggregates mapped to their derived aliases, applied on the outer ──
        def _map_outer(node):
            """Aggregates → `side.alias`; columns → `side.col`; output aliases kept."""
            node = node.copy()
            placed_cols: List[int] = []
            for agg in list(node.find_all(exp.AggFunc)):
                placed = _register_agg(agg)
                if placed is None:
                    raise ValueError("unplaceable aggregate")
                sd, nm = placed
                ref = exp.column(nm, table=sides[sd]["alias"])
                placed_cols.append(id(ref))
                if agg is node:
                    return ref
                agg.replace(ref)
            for c in list(node.find_all(exp.Column)):
                if id(c) in placed_cols:
                    continue                                  # a derived aggregate column
                q = str(c.table or "").lower()
                if q:
                    sd = alias_to_side.get(q)
                    if sd is None:
                        raise ValueError("unknown qualifier")
                    if sd not in ("a", "b"):
                        raise ValueError("mixed")
                    if c.name.lower() not in [d.lower() for d in sides[sd]["dims"]] \
                            and c.name.lower() != key:
                        raise ValueError("ungrouped column")
                    c.set("table", exp.to_identifier(sides[sd]["alias"]))
                elif str(c.name).lower() not in proj_alias_side:
                    raise ValueError("bare column that is not an output alias")
            return node
        try:
            having = stmt.args.get("having")
            having_conj: List[Tuple[Optional[str], Any]] = []       # (side, original conjunct)
            if having is not None:
                hconj = list(having.this.flatten()) if isinstance(having.this, exp.And) else [having.this]
                for h in hconj:
                    hs = _side_of(h, alias_to_side)
                    if hs in ("mixed", "unknown"):
                        return None
                    having_conj.append((hs, h))
                    outer_where.append(_map_outer(h))
            # ── ORDER BY / TOP ───────────────────────────────────────────────────────
            order = stmt.args.get("order")
            order_terms = list(order.expressions) if order is not None else []
            order_sides = set()
            for o in order_terms:
                inner_o = o.this
                if isinstance(inner_o, exp.Column) and not inner_o.table and str(inner_o.name).lower() in proj_alias_side:
                    order_sides.add(proj_alias_side[str(inner_o.name).lower()])
                else:
                    sd = _side_of(inner_o, alias_to_side)
                    if sd not in ("a", "b"):
                        raise ValueError("order term not attributable")
                    order_sides.add(sd)
            mapped_order = [exp.Ordered(this=_map_outer(o.this), desc=o.args.get("desc"),
                                        nulls_first=o.args.get("nulls_first")) for o in order_terms]
        except ValueError:
            return None

        # The ranked side: TOP + ORDER BY pushed inside when every ORDER BY term is that
        # side's — INNER (with the other side's membership carried in, below) or LEFT with
        # the preserved side ranked; never the optional side of a LEFT join. A HAVING
        # conjunct on the ranked side moves inside with the TOP (the original filtered
        # before ranking); one on the OTHER side would filter the join's candidates after
        # the rank, so the TOP then stays on the outer statement.
        push_side = None
        if lim is not None and order_terms and len(order_sides) == 1:
            cand = next(iter(order_sides))
            if (side_kw != "LEFT" or cand == "a") and all(hs == cand for hs, _ in having_conj):
                push_side = cand
        if push_side is not None and having_conj:
            # the outer copies of the ranked side's HAVING are redundant once inside
            outer_where = outer_where[:len(outer_where) - len(having_conj)]

        # ── build the two derived tables ─────────────────────────────────────────────
        def _derived(side_key: str):
            sd = sides[side_key]
            tbl = sd["table"].copy()
            tbl.set("alias", None)
            cols = [key] + [d for d in sd["dims"] if d.lower() != key]
            sel = exp.select(*[exp.column(c) for c in cols], *sd["aggs"]).from_(tbl)
            for w in sd["where"]:
                sel = sel.where(w)
            if push_side == side_key and side_kw != "LEFT":
                # INNER: the original ranked only keys present on the other side (under
                # that side's own WHERE) — carry that membership in, or the TOP could pick
                # a key the join then drops and the result silently comes up short.
                other = sides["b" if side_key == "a" else "a"]
                o_tbl = other["table"].copy(); o_tbl.set("alias", None)
                member = exp.select(exp.column(key)).from_(o_tbl)
                for w in other["where"]:
                    member = member.where(w.copy())
                sel = sel.where(exp.In(this=exp.column(key), query=member.subquery()))
            sel = sel.group_by(*[exp.column(c) for c in cols])
            if push_side == side_key:
                for _, h in having_conj:
                    sel = sel.having(_strip_qualifiers(h))
                local = []
                for o in mapped_order:
                    t = o.this.copy()
                    for c in t.find_all(exp.Column):
                        c.set("table", None)
                    local.append(exp.Ordered(this=t, desc=o.args.get("desc"), nulls_first=o.args.get("nulls_first")))
                sel = sel.order_by(*local).limit(lim.expression.copy())
            return sel.subquery(sd["alias"])

        outer = exp.select(*outer_select).from_(_derived("a"))
        outer = outer.join(_derived("b"), on=exp.EQ(this=exp.column(key, table=alias_a), expression=exp.column(key, table=alias_b)),
                           join_type="LEFT" if side_kw == "LEFT" else "INNER")
        if stmt.args.get("distinct"):
            outer.set("distinct", exp.Distinct())
        for w in outer_where:
            outer = outer.where(w)
        if mapped_order:
            outer = outer.order_by(*mapped_order)
        if lim is not None and push_side is None:
            outer = outer.limit(lim.expression.copy())
        try:
            from trusted_data_agent.agent.preflight import textdates as _td
            _td._restore_substr(outer); _td._restore_charlen(outer); _td._restore_trycast(outer)
        except Exception:
            pass
        new_sql = outer.sql(dialect="teradata")
        try:
            re = sqlglot.parse(new_sql, read="teradata")
        except Exception:
            return None
        if len(re) != 1:
            return None
        # The rewrite must leave nothing the join guard would still refuse.
        if fanout_violations(session_id, new_sql) or join_violations(session_id, new_sql) \
                or cross_domain_violations(session_id, new_sql):
            return None
        a_name, b_name = leaf_a, leaf_b
        repeats = [s for s in ("a", "b") if [d for d in sides[s]["dims"] if d.lower() != key]]
        grain_cols = [key] + [d for s in ("a", "b") for d in sides[s]["dims"] if d.lower() != key]
        changes = [f"aggregated {a_name} per ({', '.join([key] + [d for d in sides['a']['dims'] if d.lower() != key])})"
                   + (f" with {', '.join(x.alias for x in sides['a']['aggs'])}" if sides["a"]["aggs"] else ""),
                   f"aggregated {b_name} per ({', '.join([key] + [d for d in sides['b']['dims'] if d.lower() != key])})"
                   + (f" with {', '.join(x.alias for x in sides['b']['aggs'])}" if sides["b"]["aggs"] else ""),
                   f"joined the two aggregates on {key}"]
        if push_side is not None:
            changes.append(f"TOP {lim.expression.sql()} with its ORDER BY moved inside {sides[push_side]['table'].name}'s aggregate, "
                           f"so it ranks {sides[push_side]['table'].name} rows, not the join's repeats")
        return {"sql": new_sql, "changes": changes, "pair": [a_name, b_name], "column": key,
                "grain": "one row per (" + ", ".join(grain_cols) + ")",
                "top_pushed": push_side is not None,
                "repeats_side": [sides[s]["table"].name for s in repeats]}
    except Exception:
        return None


def fanout_rewrite_note(fix: Dict[str, Any], owner: Optional[str] = None) -> str:
    """The model-facing sentence beside a rewritten result: what ran, at what grain, and —
    when a side still contributes ungrouped columns — that a key can repeat and how to get
    one row per key if that is what the question needs."""
    a, b = fix["pair"]
    tail = ""
    if fix.get("repeats_side"):
        who = " and ".join(fix["repeats_side"])
        tail = (f" {who} contributes ungrouped columns, so a {fix['column']} value can appear once per "
                f"distinct combination of them; if the question needs ONE row per {fix['column']}, aggregate "
                f"those columns per {fix['column']} in the derived table (MAX/MIN, or the latest by a date column)"
                + (f", or take the attribute from {owner}, which owns the key" if owner else "") + ".")
    return (f"[Uderia rewrote this query before it ran: a JOIN between {a} and {b} on {fix['column']} multiplies "
            f"rows (both hold it as a foreign key), so each table was aggregated at its own grain and the "
            f"aggregates were joined — {'; '.join(fix['changes'])}. Result grain: {fix['grain']}.{tail} "
            f"Write it this way next time. Executed SQL: {fix['sql']}]")


def cross_domain_block_result(violations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recovery-shaped: the join is an INSTANCE link the model does not declare; the
    class-level correspondence it does declare is named, so the agent can READ it."""
    v = violations[0]
    a, b = v["tables"]
    aa, ab = v["areas"]
    bridge = (f" The model declares a class-level correspondence between them "
              f"('{v['bridge']}'): read it and state the correspondence between KINDS; "
              f"it never pairs a specific {a} row with a specific {b} row."
              if v.get("bridge") else
              " The model declares no relationship between them at all.")
    msg = (f"[Preflight] JOIN between {a} ({aa}) and {b} ({ab}) crosses two subject areas that share "
           f"NO declared per-record key — a row-level link between them does not exist in the model, "
           f"so the join can only return coincidental matches or nothing.{bridge} "
           f"The query was NOT executed. Answer each area separately, or from the declared correspondence.")
    return {"status": "error", "preflight": True, "preflight_join": True,
            "preflight_cross_domain": True,
            "error": f"Cross-domain join between {a} ({aa}) and {b} ({ab}) has no declared key",
            "data": msg, "violations": violations}


def block_result(violations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recovery-shaped: names the declared key and what to change; the query was NOT run."""
    v = violations[0]
    a, b = v["tables"]
    key = ", ".join(v["key"])
    used = ", ".join(v["used"]) or "none"
    msg = (f"[Preflight] JOIN between {a} and {b} is not on their declared key ({key}); it joins on "
           f"({used}). Joining on columns the tables merely share by name multiplies rows without bound. "
           f"The query was NOT executed. Rewrite the join as ON {' AND '.join(f'{a}.{k} = {b}.{k}' for k in v['key'])}"
           f" (every key column) and retry.")
    return {"status": "error", "preflight": True, "preflight_join": True,
            "error": f"Join not on the declared key ({key}) between {a} and {b}", "data": msg,
            "violations": violations}


def _reset_all() -> None:
    with _LOCK:
        _KEYS.clear()
