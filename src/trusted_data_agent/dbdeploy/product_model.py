"""
OBDA-READ data-product manifest model — the signable content, parsed and M-rule-enforced.

The manifest is the producer-declared definition of a **data product**: a bounded,
consumable slice of the ontology — entities frozen to their deployed tables, the ONLY
joins assembly may use (with cardinalities), measures anchored to a grain, and an
attribute allowlist. Binding design: docs/roadmap/TIER_5.5_OBDA_SCOPE.md §2 (the
M-rules) and §3 (the closed filter-operator set). This module is **pure** — no I/O, no
LLM, no store access — so the CI gate exercises every rule directly.

Split of responsibility (deliberate):

* THIS module enforces everything decidable from the manifest ALONE — shape, internal
  referential integrity, the closed vocabularies. A violation raises
  :class:`ProductManifestError` with a machine-readable ``code`` (the scope's REJECT
  language: precise errors, never a guess or a silent narrowing).
* ``product_validation.py`` (the sign-time gate, V1–V7) checks everything that needs
  the OUTSIDE world — resolver agreement, deployed schema, join-graph tree property,
  expression transpile, the grain-safety matrix.

Canonicalization matches the Trust Membrane's (``core/trust/attestation.py``):
``sort_keys=True, separators=(",", ":"), default=str`` — so the manifest hash is
stable across processes and key orderings, and the signable content is byte-exact.

Additive by construction: nothing imports this module on any existing execution path;
a deployment with no products never touches it (charter #15).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── The closed vocabularies (scope §2.2 / §3 R2) ────────────────────────────

#: Product identity: unique per ontology, machine-safe.
PRODUCT_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")

#: Join cardinality, from → to. The from-side is the MANY side by declaration; a
#: ``1:N`` therefore cannot be expressed — the producer declares it from the other
#: side. This is what makes grain-safety (G1) decidable from the manifest alone.
JOIN_CARDINALITIES = frozenset({"N:1", "1:1"})

#: The closed filter-operator set (R2). No SQL fragment ever crosses the surface —
#: there is no expression operator, by construction, and that is a permanent property.
FILTER_OPS = frozenset({
    "eq", "ne", "in", "not_in", "between", "ge", "le", "gt", "lt",
    "contains", "starts_with", "ends_with", "is_null", "not_null",
})

#: Measure expressions arrive in Spark dialect (matching the shipped metric
#: definitions); the compiler transpiles per target. Other dialects are v1.1+.
MEASURE_DIALECTS = frozenset({"spark"})


class ProductManifestError(ValueError):
    """A manifest violated an M-rule. ``code`` is machine-readable (closed set below);
    ``message`` names the offending member and, where a near-miss exists, the closest
    declared name — rejections are self-correction food, never a guess."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


#: Every code this module can raise — a closed set, so callers (REST, Designer, the
#: sign-off) can branch on it and the gate can assert each one fires.
ERROR_CODES = frozenset({
    "bad_product_name", "bad_version", "missing_backend", "bad_description",
    "no_entities", "duplicate_entity", "bad_entity_name", "missing_deployed",
    "missing_key", "bad_key", "duplicate_attribute", "bad_attribute",
    "unknown_join_entity", "self_join", "missing_join_on", "bad_join_on",
    "missing_cardinality", "bad_cardinality", "role_required", "duplicate_role",
    "no_measures_or_attributes", "duplicate_measure", "bad_measure_name",
    "unknown_measure_anchor", "missing_measure_expr", "bad_measure_dialect",
    "unknown_member", "bad_filter_op", "bad_row_policy", "bad_manifest_shape",
    # §8 — the compositional ratchet (P1–P3)
    "composed_and_base", "bad_members", "unknown_member_product",
    "nested_composition", "member_backend_mismatch",
    "member_entity_collision", "member_measure_collision", "bridge_unknown_entity",
})


def _fail(code: str, message: str) -> None:
    raise ProductManifestError(code, message)


def _closest(name: str, candidates: List[str]) -> str:
    """The self-correction hint: '… did you mean X?' — empty when nothing is close."""
    hits = difflib.get_close_matches(name, candidates, n=2, cutoff=0.6)
    return f" — closest declared: {', '.join(hits)}" if hits else ""


# ── The model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProductAttribute:
    """One EXPOSED, dimension-eligible column of an entity. The list is an ALLOWLIST
    (M4): a column not listed does not exist to consumers — structural PII hiding."""
    name: str
    type: Optional[str] = None  # advisory ("date", "string", …); typing enforced by V-gate

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name}
        if self.type is not None:
            d["type"] = self.type
        return d


@dataclass(frozen=True)
class ProductEntity:
    name: str                      # logical entity name in the ontology
    deployed: str                  # resolver output, FROZEN at authoring time (M1)
    key: Tuple[str, ...]           # the entity's grain — REQUIRED (M2)
    attributes: Tuple[ProductAttribute, ...] = ()   # the allowlist (M4); may be empty

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "deployed": self.deployed,
                "key": list(self.key),
                "attributes": [a.to_dict() for a in self.attributes]}

    def attribute_names(self) -> Tuple[str, ...]:
        return tuple(a.name for a in self.attributes)


@dataclass(frozen=True)
class ProductJoin:
    """A declared edge — the ONLY paths assembly may use (J2). ``from`` is the MANY
    side; ``cardinality`` is from → to and REQUIRED (M3) — no inference at
    consumption time, ever."""
    from_entity: str
    to_entity: str
    on: Tuple[Tuple[str, str], ...]      # [(from_col, to_col), …] — REQUIRED (M3)
    cardinality: str                      # "N:1" | "1:1"
    role: Optional[str] = None            # REQUIRED iff >1 join between the same pair

    def to_dict(self) -> Dict[str, Any]:
        return {"from": self.from_entity, "to": self.to_entity,
                "on": [list(p) for p in self.on],
                "cardinality": self.cardinality, "role": self.role}


@dataclass(frozen=True)
class ProductMeasure:
    """Anchored to ONE entity = its grain (M5). The expression references only the
    anchor's columns — asserted at the V-gate (V4), where SQLGlot is available."""
    name: str
    anchor: str
    expr: str
    dialect: str = "spark"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "anchor": self.anchor,
                "expr": self.expr, "dialect": self.dialect}


@dataclass(frozen=True)
class ProductManifest:
    product: str
    version: int
    backend: str
    entities: Tuple[ProductEntity, ...]
    joins: Tuple[ProductJoin, ...] = ()
    measures: Tuple[ProductMeasure, ...] = ()
    #: which exposed attributes are filterable, and by which ops:
    #: {"entity.attribute": ("eq", "in", …)} — a subset of the allowlist.
    filters: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    #: v1: a fixed, producer-written predicate string or None (§2.5). Validated +
    #: transpiled at the V-gate; baked into every compiled query for the product.
    row_policy: Optional[str] = None
    description: str = ""

    # -- projections ---------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product": self.product,
            "version": self.version,
            "description": self.description,
            "backend": self.backend,
            "entities": [e.to_dict() for e in self.entities],
            "joins": [j.to_dict() for j in self.joins],
            "measures": [m.to_dict() for m in self.measures],
            "filters": {k: list(v) for k, v in self.filters.items()},
            "row_policy": self.row_policy,
        }

    def entity(self, name: str) -> Optional[ProductEntity]:
        for e in self.entities:
            if e.name == name:
                return e
        return None

    def entity_names(self) -> Tuple[str, ...]:
        return tuple(e.name for e in self.entities)


# ── Canonicalization (the signable content) ─────────────────────────────────

def canonical_json(manifest: "ProductManifest | Dict[str, Any]") -> str:
    """Canonical serialization — byte-stable across key orderings and processes.
    Matches the Trust Membrane's canonicalization discipline exactly, so the hash
    below is what the ``data_product`` attestation binds (scope §2.3)."""
    d = manifest.to_dict() if isinstance(manifest, ProductManifest) else manifest
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def manifest_hash(manifest: "ProductManifest | Dict[str, Any]") -> str:
    """sha256 over the canonical serialization — the manifest's content identity."""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


# ── Parsing + M-rule enforcement ────────────────────────────────────────────

def parse_manifest(raw: Dict[str, Any]) -> ProductManifest:
    """Parse a raw manifest dict, enforcing every rule decidable from the manifest
    alone. Raises :class:`ProductManifestError` (a REJECT — precise, machine-readable,
    never a guess); returns a validated, frozen :class:`ProductManifest`.

    The input dict is never mutated.
    """
    if not isinstance(raw, dict):
        _fail("bad_manifest_shape", f"manifest must be an object, got {type(raw).__name__}")

    # -- identity ------------------------------------------------------------
    product = raw.get("product")
    if not isinstance(product, str) or not PRODUCT_NAME_RE.match(product):
        _fail("bad_product_name",
              f"product name {product!r} must match [a-z0-9_]{{1,64}}")

    version = raw.get("version")
    # bool is an int subclass — reject it explicitly rather than signing version=True.
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _fail("bad_version", f"version must be an integer >= 1, got {version!r}")

    backend = raw.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        _fail("missing_backend", "backend is required — ONE deployed backend per product (M6)")

    description = raw.get("description") or ""
    if not isinstance(description, str):
        _fail("bad_description", "description must be a string")

    # -- entities ------------------------------------------------------------
    raw_entities = raw.get("entities")
    if not isinstance(raw_entities, list) or not raw_entities:
        _fail("no_entities", "a product declares at least one entity")

    entities: List[ProductEntity] = []
    seen_entities: set = set()
    for re_ in raw_entities:
        if not isinstance(re_, dict):
            _fail("bad_manifest_shape", "each entity must be an object")
        name = re_.get("name")
        if not isinstance(name, str) or not name.strip():
            _fail("bad_entity_name", f"entity name {name!r} must be a non-empty string")
        if name in seen_entities:
            _fail("duplicate_entity", f"entity {name!r} is declared twice")
        seen_entities.add(name)

        deployed = re_.get("deployed")
        if not isinstance(deployed, str) or not deployed.strip():
            _fail("missing_deployed",
                  f"entity {name!r}: 'deployed' is required — the resolver output, "
                  f"frozen at authoring time (M1)")

        key = re_.get("key")
        if not isinstance(key, list) or not key:
            _fail("missing_key",
                  f"entity {name!r}: 'key' (the grain) is REQUIRED (M2)")
        if not all(isinstance(k, str) and k.strip() for k in key):
            _fail("bad_key", f"entity {name!r}: every key column must be a non-empty string")

        attrs: List[ProductAttribute] = []
        seen_attrs: set = set()
        for ra in re_.get("attributes") or []:
            if not isinstance(ra, dict) or not isinstance(ra.get("name"), str) \
                    or not ra["name"].strip():
                _fail("bad_attribute",
                      f"entity {name!r}: each attribute needs a non-empty 'name'")
            if ra["name"] in seen_attrs:
                _fail("duplicate_attribute",
                      f"entity {name!r}: attribute {ra['name']!r} is listed twice")
            seen_attrs.add(ra["name"])
            a_type = ra.get("type")
            if a_type is not None and not isinstance(a_type, str):
                _fail("bad_attribute",
                      f"entity {name!r}.{ra['name']}: 'type' must be a string when present")
            attrs.append(ProductAttribute(name=ra["name"], type=a_type))

        entities.append(ProductEntity(
            name=name, deployed=deployed, key=tuple(key), attributes=tuple(attrs)))

    entity_names = [e.name for e in entities]

    # -- joins ---------------------------------------------------------------
    joins: List[ProductJoin] = []
    pair_roles: Dict[Tuple[str, str], List[Optional[str]]] = {}
    for rj in raw.get("joins") or []:
        if not isinstance(rj, dict):
            _fail("bad_manifest_shape", "each join must be an object")
        f, t = rj.get("from"), rj.get("to")
        for endpoint in (f, t):
            if endpoint not in entity_names:
                _fail("unknown_join_entity",
                      f"join references undeclared entity {endpoint!r}"
                      f"{_closest(str(endpoint), entity_names)}")
        if f == t:
            _fail("self_join", f"join from {f!r} to itself is not declarable in v1")

        on = rj.get("on")
        if not isinstance(on, list) or not on:
            _fail("missing_join_on",
                  f"join {f!r}→{t!r}: 'on' column pairs are REQUIRED (M3)")
        pairs: List[Tuple[str, str]] = []
        for p in on:
            if (not isinstance(p, (list, tuple)) or len(p) != 2
                    or not all(isinstance(c, str) and c.strip() for c in p)):
                _fail("bad_join_on",
                      f"join {f!r}→{t!r}: each 'on' entry is a [from_col, to_col] pair")
            pairs.append((p[0], p[1]))

        card = rj.get("cardinality")
        if card is None:
            _fail("missing_cardinality",
                  f"join {f!r}→{t!r}: 'cardinality' is REQUIRED (M3) — "
                  f"no inference at consumption time, ever")
        if card not in JOIN_CARDINALITIES:
            _fail("bad_cardinality",
                  f"join {f!r}→{t!r}: cardinality {card!r} must be one of "
                  f"{sorted(JOIN_CARDINALITIES)} (from → to; the from-side is the MANY side)")

        role = rj.get("role")
        if role is not None and (not isinstance(role, str) or not role.strip()):
            _fail("bad_manifest_shape", f"join {f!r}→{t!r}: 'role' must be a non-empty string")

        pair = (f, t) if f <= t else (t, f)   # unordered — two directions still collide
        pair_roles.setdefault(pair, []).append(role)
        joins.append(ProductJoin(from_entity=f, to_entity=t, on=tuple(pairs),
                                 cardinality=card, role=role))

    for pair, roles in pair_roles.items():
        if len(roles) > 1:
            if any(r is None for r in roles):
                _fail("role_required",
                      f"{len(roles)} joins between {pair[0]!r} and {pair[1]!r}: every "
                      f"one needs a distinct 'role' name (J1)")
            if len(set(roles)) != len(roles):
                _fail("duplicate_role",
                      f"joins between {pair[0]!r} and {pair[1]!r} reuse a role name — "
                      f"roles exist to disambiguate, so they must be distinct")

    # -- measures ------------------------------------------------------------
    measures: List[ProductMeasure] = []
    seen_measures: set = set()
    for rm in raw.get("measures") or []:
        if not isinstance(rm, dict):
            _fail("bad_manifest_shape", "each measure must be an object")
        m_name = rm.get("name")
        if not isinstance(m_name, str) or not m_name.strip():
            _fail("bad_measure_name", "each measure needs a non-empty 'name'")
        if m_name in seen_measures:
            _fail("duplicate_measure", f"measure {m_name!r} is declared twice")
        seen_measures.add(m_name)

        anchor = rm.get("anchor")
        if anchor not in entity_names:
            _fail("unknown_measure_anchor",
                  f"measure {m_name!r} anchors to undeclared entity {anchor!r}"
                  f"{_closest(str(anchor), entity_names)}")

        expr = rm.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            _fail("missing_measure_expr", f"measure {m_name!r}: 'expr' is required")

        dialect = rm.get("dialect", "spark")
        if dialect not in MEASURE_DIALECTS:
            _fail("bad_measure_dialect",
                  f"measure {m_name!r}: dialect {dialect!r} must be one of "
                  f"{sorted(MEASURE_DIALECTS)}")
        measures.append(ProductMeasure(name=m_name, anchor=anchor,
                                       expr=expr, dialect=dialect))

    # A product with neither measures nor any exposed attribute has nothing to
    # consume — an empty contract is a mis-declaration, not a minimal one.
    if not measures and not any(e.attributes for e in entities):
        _fail("no_measures_or_attributes",
              "a product must expose at least one measure or one attribute")

    # -- filters -------------------------------------------------------------
    exposed = {f"{e.name}.{a}" for e in entities for a in e.attribute_names()}
    filters: Dict[str, Tuple[str, ...]] = {}
    raw_filters = raw.get("filters") or {}
    if not isinstance(raw_filters, dict):
        _fail("bad_manifest_shape", "'filters' must be an object of attr → op list")
    for member, ops in raw_filters.items():
        if member not in exposed:
            _fail("unknown_member",
                  f"filter references {member!r}, which is not an exposed "
                  f"entity.attribute{_closest(str(member), sorted(exposed))}")
        if not isinstance(ops, list) or not ops:
            _fail("bad_filter_op", f"filter {member!r}: give a non-empty list of operators")
        bad = [o for o in ops if o not in FILTER_OPS]
        if bad:
            _fail("bad_filter_op",
                  f"filter {member!r}: operator(s) {bad} not in the closed set "
                  f"{sorted(FILTER_OPS)} — no SQL fragment ever crosses the surface (R2)")
        filters[member] = tuple(ops)

    # -- row policy ----------------------------------------------------------
    row_policy = raw.get("row_policy")
    if row_policy is not None and (not isinstance(row_policy, str) or not row_policy.strip()):
        _fail("bad_row_policy",
              "row_policy is either null or a fixed, producer-written predicate string (§2.5)")

    return ProductManifest(
        product=product, version=version, backend=backend.strip(),
        entities=tuple(entities), joins=tuple(joins), measures=tuple(measures),
        filters=filters, row_policy=row_policy, description=description,
    )


# ── §8 · composed products (the compositional ratchet, P1–P3) ───────────────
# A COMPOSED product declares member products + the bridging joins — nothing
# else. Consumption and validation run on the FLATTENED form through the SAME
# parse/validate/compile machinery as a base product (P1: composition preserved
# determinism and the gate proves it — no re-judgement). v1 stop-line: members
# are BASE products on the SAME ontology (no nested composition, no
# cross-ontology members — §13), refused with precise codes, never silently.

@dataclass(frozen=True)
class ComposedManifest:
    product: str
    version: int
    backend: str
    description: str
    members: Tuple[str, ...]                 # member product names, ≥ 2
    bridges: Tuple[ProductJoin, ...]         # the joins that stitch members together

    def to_dict(self) -> Dict[str, Any]:
        return {"product": self.product, "version": self.version,
                "backend": self.backend, "description": self.description,
                "members": list(self.members),
                "bridges": [b.to_dict() for b in self.bridges]}


def is_composed(raw: Any) -> bool:
    """A manifest with a non-empty ``members`` list is composed."""
    return isinstance(raw, dict) and bool(raw.get("members"))


def composed_manifest_hash(cm: "ComposedManifest | Dict[str, Any]") -> str:
    """Hash of the canonical COMPOSED form (what the producer authored). The flat
    form has its own hash (`manifest_hash` of the flattened manifest) — the
    validation report binds the flat form, the attestation binds this one."""
    d = cm.to_dict() if isinstance(cm, ComposedManifest) else cm
    return hashlib.sha256(json.dumps(
        d, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def parse_composed(raw: Dict[str, Any]) -> ComposedManifest:
    """Parse a composed manifest. Same REJECT discipline as ``parse_manifest``:
    machine-readable codes from the closed set, the input never mutated."""
    if not isinstance(raw, dict):
        _fail("bad_manifest_shape", f"manifest must be an object, got {type(raw).__name__}")
    if raw.get("entities") or raw.get("measures"):
        _fail("composed_and_base",
              "a composed product declares members + bridges ONLY — its entities "
              "and measures come from its members (the flatten)")

    product = raw.get("product")
    if not isinstance(product, str) or not PRODUCT_NAME_RE.match(product):
        _fail("bad_product_name",
              f"product name {product!r} must match [a-z0-9_]{{1,64}}")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _fail("bad_version", f"version must be an integer >= 1, got {version!r}")
    backend = raw.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        _fail("missing_backend", "backend is required — ONE deployed backend per product (M6)")
    description = raw.get("description") or ""
    if not isinstance(description, str):
        _fail("bad_description", "description must be a string")

    members = raw.get("members")
    if not isinstance(members, list) or len(members) < 2:
        _fail("bad_members", "a composed product declares at least TWO member products")
    seen = set()
    for m in members:
        if not isinstance(m, str) or not PRODUCT_NAME_RE.match(m):
            _fail("bad_members", f"member {m!r} must be a valid product name")
        if m == product:
            _fail("bad_members", f"a product cannot be its own member ({m!r})")
        if m in seen:
            _fail("bad_members", f"member {m!r} is declared twice")
        seen.add(m)

    raw_bridges = raw.get("bridges")
    if not isinstance(raw_bridges, list) or not raw_bridges:
        _fail("bad_members",
              "a composed product declares the bridging joins between its members "
              "(cardinality + on-pairs, as always — M3); without them the members "
              "cannot be assembled")
    bridges: List[ProductJoin] = []
    for rb in raw_bridges:
        if not isinstance(rb, dict):
            _fail("bad_manifest_shape", "each bridge must be an object")
        f, t = rb.get("from"), rb.get("to")
        if not isinstance(f, str) or not isinstance(t, str) or not f or not t:
            _fail("unknown_join_entity", "bridge 'from'/'to' must name entities")
        on = rb.get("on")
        if (not isinstance(on, list) or not on
                or not all(isinstance(pr, (list, tuple)) and len(pr) == 2
                           and all(isinstance(x, str) and x for x in pr) for pr in on)):
            _fail("bad_join_on",
                  f"bridge {f!r}→{t!r}: 'on' must be a non-empty list of "
                  f"[from_col, to_col] pairs (M3)")
        card = rb.get("cardinality")
        if card not in JOIN_CARDINALITIES:
            _fail("missing_cardinality" if card is None else "bad_cardinality",
                  f"bridge {f!r}→{t!r}: cardinality must be one of "
                  f"{sorted(JOIN_CARDINALITIES)} (M3 — never inferred)")
        role = rb.get("role")
        if role is not None and (not isinstance(role, str) or not role.strip()):
            _fail("bad_manifest_shape", f"bridge {f!r}→{t!r}: role must be a string")
        bridges.append(ProductJoin(from_entity=f, to_entity=t,
                                   on=tuple((a, b) for a, b in on),
                                   cardinality=card, role=role))
    return ComposedManifest(product=product, version=version,
                            backend=backend.strip(), description=description,
                            members=tuple(members), bridges=tuple(bridges))


def flatten_composed(cm: ComposedManifest,
                     member_manifests: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten a composed product into a BASE-form raw manifest — the input to the
    UNCHANGED parse/validate/compile pipeline (P1). Refuses with precise codes:
    a missing member, a nested composition, a backend mismatch, an entity or
    measure name collision (naming BOTH members), a bridge referencing an entity
    no member exposes. Inputs never mutated."""
    entities: List[Dict[str, Any]] = []
    joins: List[Dict[str, Any]] = []
    measures: List[Dict[str, Any]] = []
    ent_owner: Dict[str, str] = {}
    meas_owner: Dict[str, str] = {}
    for m in cm.members:
        raw = member_manifests.get(m)
        if not isinstance(raw, dict):
            _fail("unknown_member_product",
                  f"member product {m!r} was not found on this ontology")
        if is_composed(raw):
            _fail("nested_composition",
                  f"member {m!r} is itself composed — v1 composes BASE products "
                  f"only (the recorded stop-line); flatten it into base members")
        if str(raw.get("backend") or "").strip() != cm.backend:
            _fail("member_backend_mismatch",
                  f"member {m!r} is on backend {raw.get('backend')!r} but the "
                  f"composed product declares {cm.backend!r} — ONE backend (M6)")
        for e in raw.get("entities") or []:
            en = e.get("name")
            if en in ent_owner:
                _fail("member_entity_collision",
                      f"entity {en!r} is exposed by BOTH {ent_owner[en]!r} and "
                      f"{m!r} — rename in one member or drop one")
            ent_owner[en] = m
            entities.append(e)
        for j in raw.get("joins") or []:
            joins.append(j)
        for ms in raw.get("measures") or []:
            mn = ms.get("name")
            if mn in meas_owner:
                _fail("member_measure_collision",
                      f"measure {mn!r} is declared by BOTH {meas_owner[mn]!r} and "
                      f"{m!r} — rename in one member")
            meas_owner[mn] = m
            measures.append(ms)
    for b in cm.bridges:
        for end in (b.from_entity, b.to_entity):
            if end not in ent_owner:
                _fail("bridge_unknown_entity",
                      f"bridge {b.from_entity!r}→{b.to_entity!r}: {end!r} is not "
                      f"exposed by any member (members expose: "
                      f"{', '.join(sorted(ent_owner)) or '(none)'})")
        joins.append(b.to_dict())
    return {"product": cm.product, "version": cm.version, "backend": cm.backend,
            "description": cm.description, "entities": entities,
            "joins": joins, "measures": measures}


def load_flat_manifest(store, product_name: str):
    """Resolve a product to its FLAT raw manifest through an injected graph store
    (``get_entity_by_name`` is the only method used — pure but for that seam).
    Returns ``(flat_raw, composed_or_none, error_or_none)``:

    * base product   → ``(its manifest, None, None)``
    * composed       → ``(flattened manifest, ComposedManifest, None)``
    * failure        → ``(None, None, ProductManifestError)`` — the REJECT verbatim
    """
    node = store.get_entity_by_name(product_name, "data_product")
    if not node:
        return None, None, ProductManifestError(
            "unknown_member_product", f"no data product named {product_name!r}")
    raw = (node.get("properties") or {}).get("manifest")
    if not isinstance(raw, dict):
        return None, None, ProductManifestError(
            "bad_manifest_shape", f"product {product_name!r} carries no manifest")
    if not is_composed(raw):
        return raw, None, None
    try:
        cm = parse_composed(raw)
        members: Dict[str, Dict[str, Any]] = {}
        for m in cm.members:
            mn = store.get_entity_by_name(m, "data_product")
            mraw = ((mn or {}).get("properties") or {}).get("manifest")
            if isinstance(mraw, dict):
                members[m] = mraw
        return flatten_composed(cm, members), cm, None
    except ProductManifestError as e:
        return None, None, e
