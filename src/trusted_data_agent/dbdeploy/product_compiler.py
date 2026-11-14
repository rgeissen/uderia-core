"""
OBDA-READ product compiler — fan-out-safe, deterministic compilation of a request against
a signed data-product manifest (Phase B1).

Binding design: docs/roadmap/TIER_5.5_OBDA_SCOPE.md §3 (the request contract) and §4
(the J/G/A/D rules — this module is their transcription). The one hard algorithm of
the whole surface, and the reason "signed = trustworthy blindly" can be real: ask for
two measures at different grains through a 1:N join and a naïve flat join silently
MULTIPLIES one of them — a confidently wrong number. Here each measure is aggregated
at its own anchor's grain and the sub-aggregates are joined on the full dimension
tuple (A1/A2), so the multiplied number is *impossible to produce*, and a request
that would need it is REJECTED naming the offending hop (G2), never approximated.

Contract properties, all CI-pinned:

* **Pure + byte-stable (D3):** ``compile_request(manifest, request)`` is a
  deterministic function — same inputs → the same SQL string, across calls and
  processes. No timestamps, no randomness, no dict-order leakage.
* **Deterministic row order (R4):** every statement ends in ORDER BY — the request's
  ``order_by`` first, then every selected dimension ascending as the tiebreak. Same
  request → same rows in the same order.
* **No SQL fragment ever crosses the surface (R2):** filters are typed bound
  literals from the closed operator set, escaped here; there is no expression
  parameter, by construction — a permanent property.
* **REJECT, never guess (R1/G2):** unknown members name the closest declared
  alternatives; grain conflicts name the 1:N hop. Rejections are self-correction
  food for an LLM and honest UX for BI.

Reuses the shipped expression layer (``translate_expr`` — every hard-won Teradata
fix applies), the shipped identifier quoting, and the shipped TRUNC formats. Pure —
no I/O, no store, no LLM. Nothing on any existing execution path imports it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from trusted_data_agent.dbdeploy.metric_compiler import (
    _TD_TRUNC_FMT, _q, _qualified, translate_expr)
from trusted_data_agent.dbdeploy.product_model import (
    FILTER_OPS, ProductManifest)

# ── the request contract's closed vocabularies (§3) ─────────────────────────

TIME_GRAINS = ("YEAR", "QUARTER", "MONTH", "WEEK", "DAY")
DEFAULT_LIMIT = 1_000
MAX_LIMIT = 100_000

#: ops that take no value / two values / a list — for literal arity validation
_NO_VALUE_OPS = frozenset({"is_null", "not_null"})
_TWO_VALUE_OPS = frozenset({"between"})
_LIST_OPS = frozenset({"in", "not_in"})
_LIKE_OPS = frozenset({"contains", "starts_with", "ends_with"})
_CMP_SQL = {"eq": "=", "ne": "<>", "ge": ">=", "le": "<=", "gt": ">", "lt": "<"}


class ProductRequestError(ValueError):
    """A REJECT (§0): precise, machine-readable, never a guess or a silent
    narrowing. ``code`` is from the closed taxonomy below."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


REQUEST_ERROR_CODES = frozenset({
    "unknown_member", "grain_conflict", "bad_request_shape", "bad_filter",
    "bad_time_grain", "bad_order_by", "bad_limit", "empty_request",
    "role_path_unknown", "role_required", "not_filterable",
})


def _fail(code: str, message: str) -> None:
    raise ProductRequestError(code, message)


def _closest(name: str, candidates: Sequence[str]) -> str:
    hits = difflib.get_close_matches(str(name), list(candidates), n=2, cutoff=0.5)
    return f" — closest declared: {', '.join(hits)}" if hits else ""


def normalize_order_by(raw: Any) -> List[Dict[str, str]]:
    """Coerce the COMMON LLM order_by shapes onto the canonical [{field, dir}].

    Observed live (session 174af2a6): a model sent `"customer_count DESC"` — the
    natural SQL spelling — and the dict-only reader RAISED (`'str' object has no
    attribute 'get'`) instead of rejecting; the component-routing except then
    forwarded a perfectly valid governed request to the MCP server, which
    answered "Unknown tool". Same doctrine as normalize_filters: syntax widens,
    meaning never — field names and directions still face the strict
    bad_order_by REJECT checks unchanged.

    Handled: the canonical list of {field, dir} (as-is) · a bare dict · a bare
    string "field" / "field asc" / "field desc" (case-insensitive; a leading
    '-' means desc) · a [field, dir] pair · a list of any of these. Anything
    else becomes a stringified field entry so the strict REJECT names it
    precisely — this function never raises.
    """
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: List[Dict[str, str]] = []
    for ob in items:
        if isinstance(ob, dict):
            out.append({"field": ob.get("field"), "dir": str(ob.get("dir") or "asc")})
            continue
        if isinstance(ob, (list, tuple)) and len(ob) == 2:
            out.append({"field": str(ob[0]), "dir": str(ob[1])})
            continue
        s = str(ob).strip()
        direction = "asc"
        if s.startswith("-"):
            s, direction = s[1:].strip(), "desc"
        parts = s.split()
        if len(parts) == 2 and parts[1].lower() in ("asc", "desc"):
            s, direction = parts[0], parts[1].lower()
        out.append({"field": s, "dir": direction})
    return out


# ── parsed request ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DimRef:
    """A requested dimension ``entity.attr`` — optionally role-qualified
    ``entity@role.attr`` to select one of several declared join paths (J1)."""
    entity: str
    attr: str
    role: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.entity}.{self.attr}"

    @property
    def out_name(self) -> str:
        # the OUTPUT column name is stable and role-free — the role selects the
        # path, it does not rename the concept
        return f"{self.entity}.{self.attr}"


@dataclass(frozen=True)
class CompiledRequest:
    sql: str                       # the full statement, ORDER BY + ';'
    measures: Tuple[str, ...]      # output measure names, request order
    dimensions: Tuple[str, ...]    # output dimension keys, request order
    anchors: Tuple[str, ...]       # anchor entities involved (sorted)
    request_hash: str              # sha256 over the canonical request (R5)
    warnings: Tuple[str, ...] = ()


_DIM_RE = re.compile(r"^([A-Za-z_][\w]*)(?:@([\w]+))?\.([A-Za-z_][\w]*)$")


def _parse_dim(token: str, manifest: ProductManifest) -> DimRef:
    m = _DIM_RE.match(str(token))
    if not m:
        _fail("bad_request_shape",
              f"dimension {token!r} must be 'entity.attribute' "
              f"(optionally 'entity@role.attribute')")
    ent, role, attr = m.group(1), m.group(2), m.group(3)
    e = manifest.entity(ent)
    if e is None:
        _fail("unknown_member",
              f"dimension references undeclared entity {ent!r}"
              f"{_closest(ent, manifest.entity_names())}")
    if attr not in e.attribute_names():
        _fail("unknown_member",
              f"{ent!r} does not expose attribute {attr!r}"
              f"{_closest(attr, e.attribute_names())} (the allowlist governs — M4)")
    if role is not None:
        roles = {j.role for j in manifest.joins
                 if role and (j.from_entity == ent or j.to_entity == ent)}
        if role not in roles:
            _fail("role_path_unknown",
                  f"{ent!r} has no declared join role {role!r}"
                  f"{_closest(role, [r for r in roles if r])}")
    return DimRef(entity=ent, attr=attr, role=role)


# ── the join tree (J1/J2) ───────────────────────────────────────────────────

def _path(manifest: ProductManifest, start: str, goal: str,
          role: Optional[str] = None) -> Optional[List[Any]]:
    """The unique join path start→goal (V3 guarantees tree-ness at sign time; a
    role narrows parallel edges to the selected one). Returns the hop list
    (ProductJoin per hop, in traversal order) or None."""
    if start == goal:
        return []
    adj: Dict[str, List[Tuple[str, Any]]] = {}
    for j in manifest.joins:
        if role is not None and j.role is not None and j.role != role \
                and (goal in (j.from_entity, j.to_entity)):
            continue   # a role-qualified dim excludes the OTHER parallel edges to it
        adj.setdefault(j.from_entity, []).append((j.to_entity, j))
        adj.setdefault(j.to_entity, []).append((j.from_entity, j))
    parent: Dict[str, Any] = {start: None}
    queue = [start]
    while queue:
        node = queue.pop(0)
        if node == goal:
            break
        for (nbr, j) in adj.get(node, ()):
            if nbr not in parent:
                parent[nbr] = (node, j)
                queue.append(nbr)
    if goal not in parent:
        return None
    hops: List[Any] = []
    node = goal
    while parent[node] is not None:
        prev, j = parent[node]
        hops.append((prev, node, j))
        node = prev
    return list(reversed(hops))


def _hop_is_grain_safe(prev: str, nxt: str, j: Any) -> bool:
    """Traversing prev→nxt: safe iff the hop cannot multiply prev's rows —
    the declared many-side (``from``) → one-side is safe; 1:1 both ways (G1)."""
    if j.cardinality == "1:1":
        return True
    return j.from_entity == prev and j.to_entity == nxt


def _grain_check(manifest: ProductManifest, anchor: str, dim: DimRef) -> None:
    """G2 — REJECT a (measure-anchor, dimension) pair whose path fans out,
    NAMING the offending hop; never silently compute the multiplied number.
    Also J1's ambiguity rule: a BARE dimension whose path crosses a pair with
    parallel role-qualified joins must name its role — picking one silently
    would make the answer depend on traversal order, the opposite of a
    deterministic contract."""
    hops = _path(manifest, anchor, dim.entity, role=dim.role)
    if hops is None:
        _fail("grain_conflict",
              f"no declared join path from anchor {anchor!r} to {dim.entity!r} "
              f"(assembly uses declared joins ONLY — J2)")
    pair_counts: Dict[Tuple[str, str], int] = {}
    for j in manifest.joins:
        p = (j.from_entity, j.to_entity) if j.from_entity <= j.to_entity \
            else (j.to_entity, j.from_entity)
        pair_counts[p] = pair_counts.get(p, 0) + 1
    for (prev, nxt, j) in hops:
        p = (prev, nxt) if prev <= nxt else (nxt, prev)
        if dim.role is None and pair_counts.get(p, 0) > 1:
            roles = sorted(jj.role for jj in manifest.joins
                           if {jj.from_entity, jj.to_entity} == {prev, nxt}
                           and jj.role)
            _fail("role_required",
                  f"dimension {dim.key!r}: {len(roles)} declared join paths exist "
                  f"between {prev!r} and {nxt!r} — qualify the role "
                  f"({' | '.join(f'{dim.entity}@{r}.{dim.attr}' for r in roles)}); "
                  f"picking one silently would not be deterministic (J1)")
        if not _hop_is_grain_safe(prev, nxt, j):
            _fail("grain_conflict",
                  f"measure at {anchor!r} × dimension {dim.key!r}: the hop "
                  f"{prev}→{nxt} is 1:N (declared {j.from_entity}→{j.to_entity} "
                  f"N:1) — joining it would multiply the measure's rows; the "
                  f"published admissible map already excludes this pair (G3)")


# ── literal rendering (R2 — typed bound literals, escaped HERE) ─────────────

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _literal(value: Any, attr_type: Optional[str]) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    t = (attr_type or "").lower()
    if t in ("date",) and _DATE_RE.match(s):
        return f"DATE '{s}'"
    if t in ("int", "integer", "bigint", "number", "decimal", "float", "double") \
            and _NUM_RE.match(s):
        return s
    return "'" + s.replace("'", "''") + "'"


def _like_literal(value: Any, op: str) -> str:
    """LIKE pattern with the user's wildcards NEUTRALIZED (R2): % _ and the escape
    char are escaped, so a value can never smuggle a pattern."""
    s = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    body = {"contains": f"%{s}%", "starts_with": f"{s}%", "ends_with": f"%{s}"}[op]
    return "'" + body.replace("'", "''") + "' ESCAPE '\\'"


# ── the compiler ────────────────────────────────────────────────────────────

def _canonical_request(request: Dict[str, Any]) -> str:
    return json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)


def request_hash(request: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_request(request).encode("utf-8")).hexdigest()


def _dim_sql(manifest: ProductManifest, dim: DimRef,
             time_grain: Dict[str, str]) -> str:
    """The dimension's SQL expression on its entity alias, with the requested
    time grain applied through the SHIPPED TRUNC formats (A5)."""
    base = f"{_q(dim.entity)}.{_q(dim.attr)}"
    grain = time_grain.get(dim.key)
    if grain:
        if grain == "DAY":
            return f"CAST({base} AS DATE)"
        fmt = _TD_TRUNC_FMT[grain]
        return f"TRUNC(CAST({base} AS DATE), '{fmt}')"
    return base


def normalize_filters(raw: Any) -> List[Dict[str, Any]]:
    """Coerce the COMMON LLM filter shapes onto the canonical [{attr, op, value}].

    Observed live: a model sent `{"Products.ProductType": "shoes"}` and paid an
    LLM self-correction round to be told "each filter is an object {attr, op,
    value}" — a purely MECHANICAL reshaping. Absorbing shape variance is the
    component's job (Component → LLM priority 1); the closed operator set and
    every semantic rule (filterability, grain, arity) still apply unchanged to
    the normalized form, so this widens ACCEPTED SYNTAX, never accepted meaning.

    Handled: the canonical list (returned as-is, per-item) · a bare dict
    {attr: value} (one eq filter per pair; a list value becomes `in`) · an item
    {attr, value} with no op (eq; list value → in) · an [attr, value] pair.
    Anything else passes through untouched for _filter_sql's strict REJECT.
    """
    if not raw:
        return []
    if isinstance(raw, dict) and "attr" not in raw:
        # a bare {attr: value, ...} dict — one filter per pair. (The first version
        # wrapped the dict into a one-item list first, which made this branch
        # unreachable; the gate's multi-pair case caught it.)
        return [{"attr": k, "op": "in" if isinstance(v, (list, tuple)) else "eq",
                 "value": list(v) if isinstance(v, (list, tuple)) else v}
                for k, v in raw.items()]
    items = [raw] if isinstance(raw, dict) else raw
    if not isinstance(items, (list, tuple)):
        return [items]                   # strict path rejects with the precise message
    out = []
    for f in items:
        if isinstance(f, dict) and "attr" in f and "op" not in f and "value" in f:
            v = f["value"]
            out.append({"attr": f["attr"],
                        "op": "in" if isinstance(v, (list, tuple)) else "eq",
                        "value": list(v) if isinstance(v, (list, tuple)) else v})
        elif isinstance(f, (list, tuple)) and len(f) == 2:
            k, v = f
            out.append({"attr": k, "op": "in" if isinstance(v, (list, tuple)) else "eq",
                        "value": list(v) if isinstance(v, (list, tuple)) else v})
        elif isinstance(f, dict) and "attr" not in f and len(f) == 1:
            (k, v), = f.items()          # [{"Products.ProductType": "shoes"}]
            out.append({"attr": k, "op": "in" if isinstance(v, (list, tuple)) else "eq",
                        "value": list(v) if isinstance(v, (list, tuple)) else v})
        else:
            out.append(f)                # canonical or unknown — strict path decides
    return out


def _filter_sql(manifest: ProductManifest, flt: Dict[str, Any]) -> Tuple[DimRef, str]:
    """One structured filter → (owner dim ref, predicate SQL on the entity alias)."""
    if not isinstance(flt, dict):
        _fail("bad_filter", "each filter is an object {attr, op, value}")
    dim = _parse_dim(flt.get("attr"), manifest)
    op = flt.get("op")
    allowed = manifest.filters.get(dim.key)
    if allowed is None:
        _fail("not_filterable",
              f"{dim.key!r} is not declared filterable"
              f"{_closest(dim.key, sorted(manifest.filters))}")
    if op not in FILTER_OPS:
        _fail("bad_filter", f"operator {op!r} is not in the closed set (R2)")
    if op not in allowed:
        _fail("not_filterable",
              f"{dim.key!r} allows only {sorted(allowed)} — not {op!r}")
    ent = manifest.entity(dim.entity)
    a_type = next((a.type for a in ent.attributes if a.name == dim.attr), None)
    col = f"{_q(dim.entity)}.{_q(dim.attr)}"
    val = flt.get("value")

    if op in _NO_VALUE_OPS:
        return dim, f"{col} IS {'NOT ' if op == 'not_null' else ''}NULL"
    if op in _TWO_VALUE_OPS:
        if not isinstance(val, (list, tuple)) or len(val) != 2:
            _fail("bad_filter", f"'between' takes exactly two values, got {val!r}")
        return dim, (f"{col} BETWEEN {_literal(val[0], a_type)} "
                     f"AND {_literal(val[1], a_type)}")
    if op in _LIST_OPS:
        if not isinstance(val, (list, tuple)) or not val:
            _fail("bad_filter", f"{op!r} takes a non-empty list, got {val!r}")
        items = ", ".join(_literal(v, a_type) for v in val)
        return dim, f"{col} {'NOT IN' if op == 'not_in' else 'IN'} ({items})"
    if op in _LIKE_OPS:
        if val is None:
            _fail("bad_filter", f"{op!r} needs a value")
        return dim, f"{col} LIKE {_like_literal(val, op)}"
    return dim, f"{col} {_CMP_SQL[op]} {_literal(val, a_type)}"


def _join_clause(manifest: ProductManifest, anchor: str,
                 needed: List[DimRef]) -> Tuple[str, List[str]]:
    """The anchor's FROM + INNER JOIN tree covering every needed entity, along
    the unique (role-selected) declared paths. Returns (sql, warnings)."""
    a_ent = manifest.entity(anchor)
    sql = f"FROM {_qualified(a_ent.deployed)} AS {_q(anchor)}"
    joined = {anchor}
    warnings: List[str] = []
    for dim in needed:
        hops = _path(manifest, anchor, dim.entity, role=dim.role) or []
        for (prev, nxt, j) in hops:
            if nxt in joined:
                continue
            n_ent = manifest.entity(nxt)
            # on-pairs are declared from→to; orient to the traversal direction
            conds = []
            for (fc, tc) in j.on:
                left = (f"{_q(j.from_entity)}.{_q(fc)}")
                right = (f"{_q(j.to_entity)}.{_q(tc)}")
                conds.append(f"{left} = {right}")
            sql += (f"\n  JOIN {_qualified(n_ent.deployed)} AS {_q(nxt)} "
                    f"ON {' AND '.join(conds)}")
            joined.add(nxt)
    return sql, warnings


def compile_request(manifest: ProductManifest,
                    request: Dict[str, Any]) -> CompiledRequest:
    """Compile one request against a manifest — the whole §3/§4 contract.

    Aggregate form: ``measures`` (≥1) + optional dimensions/time_grain/filters/
    order_by/limit. Entity-read form (§3.1): ``entity`` + ``attributes`` — a plain
    projection of ONE entity, no joins, no aggregation.
    """
    if not isinstance(request, dict):
        _fail("bad_request_shape", "the request must be an object")
    if request.get("entity") is not None:
        return _compile_entity_read(manifest, request)

    # -- measures -------------------------------------------------------------
    m_names = request.get("measures")
    if not isinstance(m_names, list) or not m_names:
        _fail("empty_request",
              "a request selects at least one measure (or uses the entity-read form)")
    by_name = {ms.name: ms for ms in manifest.measures}
    measures = []
    for n in m_names:
        if n not in by_name:
            _fail("unknown_member",
                  f"unknown measure {n!r}{_closest(n, list(by_name))}")
        measures.append(by_name[n])

    # -- dimensions + grains --------------------------------------------------
    dims = [_parse_dim(t, manifest) for t in (request.get("dimensions") or [])]
    seen: set = set()
    for d in dims:
        if d.key in seen:
            _fail("bad_request_shape", f"dimension {d.key!r} is selected twice")
        seen.add(d.key)
    time_grain: Dict[str, str] = {}
    for k, g in (request.get("time_grain") or {}).items():
        if g not in TIME_GRAINS:
            _fail("bad_time_grain",
                  f"time grain {g!r} must be one of {list(TIME_GRAINS)}")
        d = _parse_dim(k, manifest)
        ent = manifest.entity(d.entity)
        a_type = next((a.type for a in ent.attributes if a.name == d.attr), None)
        if (a_type or "").lower() != "date":
            _fail("bad_time_grain",
                  f"time_grain applies to date-typed dimensions only — "
                  f"{d.key!r} is {a_type or 'untyped'}")
        if d.key not in seen:
            _fail("bad_time_grain",
                  f"time_grain names {d.key!r}, which is not a selected dimension")
        time_grain[d.key] = g

    # -- filters (dimensional too — the grain rule applies, A4) ---------------
    filters = [(dim, sql) for (dim, sql) in
               (_filter_sql(manifest, f) for f in normalize_filters(request.get("filters")))]

    # -- grain safety per anchor (G1/G2) --------------------------------------
    anchors = sorted({ms.anchor for ms in measures})
    for anchor in anchors:
        for d in dims:
            _grain_check(manifest, anchor, d)
        for (fdim, _s) in filters:
            _grain_check(manifest, anchor, fdim)

    # -- per-anchor sub-aggregates (A1) ---------------------------------------
    subs: Dict[str, str] = {}
    warnings: List[str] = []
    for anchor in anchors:
        needed = dims + [fd for (fd, _s) in filters]
        join_sql, w = _join_clause(manifest, anchor, needed)
        warnings.extend(w)
        select_items = []
        group_items = []
        for i, d in enumerate(dims):
            expr = _dim_sql(manifest, d, time_grain)
            select_items.append(f"{expr} AS {_q(f'd{i}')}")
            group_items.append(expr)
        for ms in (m for m in measures if m.anchor == anchor):
            expr, ok = translate_expr(ms.expr, read=ms.dialect,
                                      write=manifest.backend)
            if not ok:
                warnings.append(f"measure {ms.name!r}: expression used as-is")
            select_items.append(f"{expr} AS {_q(ms.name)}")
        where = ("\nWHERE " + "\n  AND ".join(s for (_d, s) in filters)) \
            if filters else ""
        group = ("\nGROUP BY " + ", ".join(group_items)) if group_items else ""
        subs[anchor] = ("SELECT " + ", ".join(select_items) + "\n"
                        + join_sql + where + group)

    # -- assembly (A2/A3) -----------------------------------------------------
    n_dims = len(dims)
    if len(anchors) == 1:
        # A3: the flat form — byte-compatible in spirit with the shipped metric
        # compiler; the outer projection restores the declared output names.
        inner = subs[anchors[0]]
        outer_cols = [f"{_q(f'd{i}')} AS {_q(dims[i].out_name)}"
                      for i in range(n_dims)]
        outer_cols += [_q(ms.name) for ms in measures]
        body = (f"SELECT {', '.join(outer_cols)}\nFROM (\n{_indent(inner)}\n) AS "
                f"{_q('a0')}")
    else:
        # A2: FULL OUTER JOIN the sub-aggregates on the full dimension tuple,
        # NULL-safe, dimensions COALESCEd — a slice present for one measure and
        # absent for the other renders honestly (measure NULL), never dropped.
        aliases = [f"a{i}" for i in range(len(anchors))]
        parts = [f"(\n{_indent(subs[a])}\n) AS {_q(aliases[i])}"
                 for i, a in enumerate(anchors)]
        joins = parts[0]
        for i in range(1, len(parts)):
            conds = []
            for di in range(n_dims):
                l = f"{_q(aliases[0])}.{_q(f'd{di}')}"
                r = f"{_q(aliases[i])}.{_q(f'd{di}')}"
                conds.append(f"({l} = {r} OR ({l} IS NULL AND {r} IS NULL))")
            on = " AND ".join(conds) if conds else "1=1"
            joins += f"\nFULL OUTER JOIN {parts[i]} ON {on}"
        outer_cols = []
        for di in range(n_dims):
            co = ", ".join(f"{_q(a)}.{_q(f'd{di}')}" for a in aliases)
            outer_cols.append(f"COALESCE({co}) AS {_q(dims[di].out_name)}")
        for ms in measures:
            ai = aliases[anchors.index(ms.anchor)]
            outer_cols.append(f"{_q(ai)}.{_q(ms.name)} AS {_q(ms.name)}")
        body = f"SELECT {', '.join(outer_cols)}\nFROM {joins}"

    # -- deterministic order + limit (R3/R4) ----------------------------------
    out_fields = [d.out_name for d in dims] + [ms.name for ms in measures]
    order_terms: List[str] = []
    for ob in normalize_order_by(request.get("order_by")):
        f_name, direction = ob.get("field"), (ob.get("dir") or "asc").lower()
        if f_name not in out_fields:
            _fail("bad_order_by",
                  f"order_by references {f_name!r}, which is not a selected "
                  f"output field{_closest(f_name, out_fields)}")
        if direction not in ("asc", "desc"):
            _fail("bad_order_by", f"dir {direction!r} must be asc or desc")
        order_terms.append(f"{_q(f_name)} {direction.upper()}")
    for d in dims:   # the tiebreak: every selected dimension ascending (R4)
        term = f"{_q(d.out_name)} ASC"
        if not any(t.startswith(_q(d.out_name) + " ") for t in order_terms):
            order_terms.append(term)
    order_sql = ("\nORDER BY " + ", ".join(order_terms)) if order_terms else ""

    limit = request.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 \
            or limit > MAX_LIMIT:
        _fail("bad_limit", f"limit must be an integer in [1, {MAX_LIMIT}]")

    body, tail = _apply_limit(manifest.backend, body, limit)
    sql = f"{body}{order_sql}{tail};"
    return CompiledRequest(
        sql=sql,
        measures=tuple(ms.name for ms in measures),
        dimensions=tuple(d.out_name for d in dims),
        anchors=tuple(anchors),
        request_hash=request_hash(request),
        warnings=tuple(warnings),
    )


def _compile_entity_read(manifest: ProductManifest,
                         request: Dict[str, Any]) -> CompiledRequest:
    """§3.1 — the non-aggregate degenerate case: a plain projection of ONE
    entity's exposed attributes. No joins, no aggregation; multi-entity
    row-level joins are v1.1 (the stop-line)."""
    ent_name = request.get("entity")
    ent = manifest.entity(ent_name)
    if ent is None:
        _fail("unknown_member",
              f"unknown entity {ent_name!r}"
              f"{_closest(ent_name, manifest.entity_names())}")
    attrs = request.get("attributes") or [a.name for a in ent.attributes]
    if not attrs:
        _fail("empty_request", f"{ent_name!r} exposes no attributes to read")
    for a in attrs:
        if a not in ent.attribute_names():
            _fail("unknown_member",
                  f"{ent_name!r} does not expose {a!r}"
                  f"{_closest(a, ent.attribute_names())}")
    filters = []
    for f in normalize_filters(request.get("filters")):
        dim, sql_pred = _filter_sql(manifest, f)
        if dim.entity != ent_name:
            _fail("grain_conflict",
                  f"an entity read of {ent_name!r} cannot filter on "
                  f"{dim.key!r} — cross-entity reads are v1.1")
        filters.append(sql_pred)
    where = ("\nWHERE " + "\n  AND ".join(filters)) if filters else ""
    limit = request.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 \
            or limit > MAX_LIMIT:
        _fail("bad_limit", f"limit must be an integer in [1, {MAX_LIMIT}]")
    cols = ", ".join(f"{_q(ent_name)}.{_q(a)} AS {_q(f'{ent_name}.{a}')}"
                     for a in attrs)
    order = "\nORDER BY " + ", ".join(f"{_q(f'{ent_name}.{a}')} ASC" for a in attrs)
    body = (f"SELECT {cols}\nFROM {_qualified(ent.deployed)} AS {_q(ent_name)}"
            f"{where}")
    body, tail = _apply_limit(manifest.backend, body, limit)
    sql = f"{body}{order}{tail};"
    return CompiledRequest(
        sql=sql, measures=(),
        dimensions=tuple(f"{ent_name}.{a}" for a in attrs),
        anchors=(ent_name,), request_hash=request_hash(request))


def _indent(sql: str, pad: str = "  ") -> str:
    return "\n".join(pad + line for line in sql.splitlines())


def _apply_limit(backend: str, body: str, limit: int) -> Tuple[str, str]:
    """The row-cap, per dialect (the D2 seam): **Teradata has no LIMIT** — it uses
    ``SELECT TOP n`` on the outermost select (valid with ORDER BY); every other
    backend gets a trailing ``LIMIT n``. Returns (body, tail)."""
    if (backend or "").lower() == "teradata":
        assert body.startswith("SELECT ")
        return "SELECT TOP " + str(limit) + body[len("SELECT"):], ""
    return body, f"\nLIMIT {limit}"


# ── the admissible map (§3.2 / G3) ──────────────────────────────────────────

def admissible_map(manifest: ProductManifest) -> Dict[str, Any]:
    """The complete validity map, DERIVED FROM THE SAME grain function the
    compiler enforces (divergence is structurally impossible — asserted in CI
    anyway): every measure × dimension pair safe/rejected, filterable ops."""
    matrix: Dict[str, Dict[str, str]] = {}
    for ms in manifest.measures:
        row: Dict[str, str] = {}
        for e in manifest.entities:
            for a in e.attributes:
                d = DimRef(entity=e.name, attr=a.name)
                try:
                    _grain_check(manifest, ms.anchor, d)
                    row[d.key] = "safe"
                except ProductRequestError as ex:
                    row[d.key] = f"rejected:{ex.message}"
        matrix[ms.name] = row
    return {
        "measures": [ms.name for ms in manifest.measures],
        "dimensions": [f"{e.name}.{a.name}"
                       for e in manifest.entities for a in e.attributes],
        "grain_matrix": matrix,
        "filters": {k: list(v) for k, v in manifest.filters.items()},
        "time_grains": list(TIME_GRAINS),
    }
