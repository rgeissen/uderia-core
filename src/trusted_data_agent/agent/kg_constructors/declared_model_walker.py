"""
declared_model_walker — pure, deterministic walker for an **ISO/IEC 11179 metadata
registry** → Uderia Knowledge Graph entities/relationships.

WHAT THIS IS FOR
----------------
A model that stores its metadata as an ISO/IEC 11179 registry (five tables in a
catalog database — see agent_packs/policing-data-products/core-model/10-catalog.sql)
publishes a machine-readable, agent-discoverable description of itself:

    object_class       one row per entity          (11179 Object Class)
    data_element       one row per attribute        (11179 Data Element)
    value_domain       one row per code-set         (11179 Value Domain)
    permissible_value  one row per decoded code      (11179 Permissible Value)
    association        one row per relationship      (11179 association layer, ISO 19763)

This walker turns that standard registry into the platform's canonical two-layer KG
topology, so a declared model becomes consumable-with-trust on Uderia. The interchange
contract is a recognised standard, not a bespoke format.

PURITY CONTRACT
---------------
No I/O.  It takes an already-fetched :class:`DeclaredModelSnapshot` and returns two
plain lists of dicts in the exact shape ``GraphStore.import_bulk`` consumes.  Same
input → byte-identical output, ordering included.  This is the unit-test target.

THE TYPE RULE (reused from andp_semantic_walker — do not "simplify")
--------------------------------------------------------------------
Meaning comes from the catalog (``declared_type``); the deployed type is resolved from
``DBC.ColumnsV`` (``snapshot.physical_columns``).  The logical attribute keeps the
declared type as the semantic fact it is; the physical column carries the deployed
type; a divergence is COUNTED, never silently reconciled.  Works for VIEWS too, which
is what makes the zero-copy NIBRS view layer type-correct.

RELATIONSHIP MAPPING (association.relationship_kind → in-vocabulary edges)
-------------------------------------------------------------------------
    pk_fk_join / bridge  → ``foreign_key`` edge (entity → entity)
    is_a                 → ``is_a`` edge (subtype → supertype)
    type_correspondence  → a ``product_bridge`` NODE + ``relates_to`` edges.  A
                           cross-domain KIND link is not an instance join and has no
                           shared key by design; a product_bridge is exactly how the
                           platform models a DECLARED cross-product mapping that states
                           its basis and whether its targets resolved (kgstore/types.py).
                           NEVER emitted as a join.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Generic, stable helpers — imported so the type-rule normalisation and the
# JSON-coercion (Decimal/date → import_bulk-safe) can never drift from the ANDP walker.
from .walker_util import _flag, _jsonable, _norm_type, _s

logger = logging.getLogger("quart.app")

LAYER_LOGICAL = "logical"
LAYER_PHYSICAL = "physical"
INFRASTRUCTURE_TERADATA = "teradata"

#: Provenance marker stamped on every node/edge this walker produces.
SOURCE_TAG = "declared_model_constructor"

#: subject_area values, in a stable display order.
_SUBJECT_ORDER = ("crime", "evidence", "shared")


class WalkError(RuntimeError):
    """Raised when the snapshot is unusable (no model name)."""


@dataclass
class WalkStats:
    """Drop/coverage diagnostics — surfaced to the caller, never swallowed."""

    entities_logical: int = 0
    attributes_logical: int = 0
    tables_physical: int = 0
    columns_physical: int = 0
    domains: int = 0
    #: data_element rows whose deployed column is absent from DBC.ColumnsV
    columns_not_deployed: int = 0
    #: declared type disagrees with the deployed type (see the type rule)
    type_mismatches: int = 0
    #: attributes carrying a decoded value enumeration
    columns_with_value_codes: int = 0
    decode_pairs: int = 0            # code/label column pairs linked by source_column
    pii_columns: int = 0
    foreign_keys: int = 0
    is_a_edges: int = 0
    type_correspondences: int = 0
    #: type-correspondence bridges whose BOTH ends resolved to real entities
    bridges_resolved: int = 0
    #: bridges whose declared ends do not both resolve — reported, never invented
    bridges_unresolved: int = 0
    #: associations whose join/is_a endpoints could not be resolved
    associations_dropped: int = 0

    def as_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


@dataclass
class DeclaredModelSnapshot:
    """An already-fetched ISO/IEC 11179 registry.

    Every list is plain dicts exactly as read from the catalog tables (keys
    lower-cased by the fetcher).  ``physical_columns`` is the DBC.ColumnsV read that
    makes the type rule enforceable.
    """

    model_name: str                                   # logical root display, e.g. "Policing Core"
    catalog_database: str = ""                         # where the 11179 tables live
    object_classes: List[Dict[str, Any]] = field(default_factory=list)
    data_elements: List[Dict[str, Any]] = field(default_factory=list)
    value_domains: List[Dict[str, Any]] = field(default_factory=list)
    permissible_values: List[Dict[str, Any]] = field(default_factory=list)
    associations: List[Dict[str, Any]] = field(default_factory=list)
    #: {(database, object, column) lower-cased: {"datatype": str, "nullable": bool, "column": realcase}}
    physical_columns: Dict[Tuple[str, str, str], Dict[str, Any]] = field(default_factory=dict)
    source_detail: str = ""


@dataclass
class WalkOptions:
    include_columns: bool = True
    include_physical: bool = True
    max_value_codes: int = 60


# ─────────────────────────────────────────────────────────────────────────────
# pure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _split_object(deployed_object: str, default_db: str) -> Optional[Tuple[str, str]]:
    """``"Policing_Core.v_incident"`` → ``("Policing_Core", "v_incident")``.

    An unqualified name takes ``default_db``.  Returns None when there is no object.
    """
    parts = [p.strip().strip('"') for p in _s(deployed_object).split(".") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return (default_db, parts[0]) if default_db else None
    return (parts[-2], parts[-1])


def _codes_by_domain(rows: Sequence[Dict[str, Any]], limit: int
                     ) -> Dict[str, List[Dict[str, str]]]:
    """Group permissible_value rows into ``{value_domain_id: [{value,label}, ...]}``.

    Ordered by the row's ``ordinal`` when present, else input order, and bounded so a
    node payload stays small.  This is the single highest-value enrichment: it is what
    lets an agent know ``V4019 = '0'`` means Female.
    """
    grouped: Dict[str, List[Tuple[Any, Dict[str, str]]]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        vd = _s(row.get("value_domain_id"))
        code = _s(row.get("code"))
        if not vd or code == "":
            continue
        ordv = row.get("ordinal")
        ordk = (ordv if isinstance(ordv, (int, float)) else i, i)
        grouped.setdefault(vd, []).append(
            (ordk, {"value": code, "label": _s(row.get("meaning"))})
        )
    out: Dict[str, List[Dict[str, str]]] = {}
    for vd, items in grouped.items():
        items.sort(key=lambda t: t[0])
        out[vd] = [pair for _, pair in items[:limit]]
    return out


def _subject_sort_key(area: str) -> Tuple[int, str]:
    a = _s(area).lower()
    return (_SUBJECT_ORDER.index(a) if a in _SUBJECT_ORDER else len(_SUBJECT_ORDER), a)


# ─────────────────────────────────────────────────────────────────────────────
# the walk
# ─────────────────────────────────────────────────────────────────────────────

def walk_declared_model(
    snapshot: DeclaredModelSnapshot,
    opts: Optional[WalkOptions] = None,
    stats: Optional[WalkStats] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Walk an ISO/IEC 11179 registry into (entities, relationships).

    Raises:
        WalkError: if the snapshot carries no model name.
    """
    if not isinstance(snapshot, DeclaredModelSnapshot):
        raise WalkError("snapshot must be a DeclaredModelSnapshot")
    model = _s(snapshot.model_name)
    if not model:
        raise WalkError("snapshot.model_name is required")

    opts = opts or WalkOptions()
    emit_physical = bool(opts.include_physical)
    stats = stats if stats is not None else WalkStats()
    src, src_detail = SOURCE_TAG, _s(snapshot.source_detail)

    entities: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
    relationships: List[Dict[str, Any]] = []

    def add_entity(name: str, etype: str, props: Dict[str, Any],
                   layer: str = LAYER_LOGICAL,
                   infrastructure: Optional[str] = None) -> None:
        key = (name, etype)
        if key in entities:
            return
        props = dict(props)
        props.setdefault("layer", layer)
        if infrastructure:
            props.setdefault("infrastructure", infrastructure)
        # Drop empties; keep False/0 (is_pii=False is a real assertion, not an absence).
        props = {k: _jsonable(v) for k, v in props.items()
                 if v not in (None, "", [], {})}
        entities[key] = {"name": name, "entity_type": etype, "properties": props,
                         "source": src, "source_detail": src_detail}

    def add_rel(sname: str, stype: str, tname: str, ttype: str, rtype: str,
                cardinality: Optional[str] = None,
                metadata: Optional[Dict[str, Any]] = None) -> None:
        rel = {"source_name": sname, "source_type": stype,
               "target_name": tname, "target_type": ttype,
               "relationship_type": rtype, "source": src}
        if cardinality:
            rel["cardinality"] = cardinality
        if metadata:
            rel["metadata"] = _jsonable({k: v for k, v in metadata.items()
                                         if v not in (None, "", [], {})})
        relationships.append(rel)

    codes_by_domain = _codes_by_domain(snapshot.permissible_values, opts.max_value_codes)
    vd_by_id = {_s(r.get("value_domain_id")): r
                for r in snapshot.value_domains if isinstance(r, dict)}

    # ── root: the logical model ──────────────────────────────────────────────
    add_entity(model, "database", {
        "label": model,
        "standard": "iso_11179",
        "catalog_database": _s(snapshot.catalog_database) or None,
    })

    # ── subject areas → logical domains ──────────────────────────────────────
    areas = sorted({_s(r.get("subject_area")).lower()
                    for r in snapshot.object_classes
                    if isinstance(r, dict) and _s(r.get("subject_area"))},
                   key=_subject_sort_key)
    for area in areas:
        dom_key = f"{model}.{area}"
        add_entity(dom_key, "domain", {"label": area, "subject_area": area})
        add_rel(model, "database", dom_key, "domain", "contains")
        stats.domains += 1

    # ── physical schemas (one per distinct deployed database) ────────────────
    core_db = _s(snapshot.catalog_database).rsplit("_", 1)[0] if snapshot.catalog_database else ""
    schemas: set = set()

    def _ensure_schema(db: str) -> None:
        if not emit_physical or not db or db in schemas:
            return
        add_entity(db, "schema", {"label": db}, layer=LAYER_PHYSICAL,
                   infrastructure=INFRASTRUCTURE_TERADATA)
        schemas.add(db)

    # ── object_class → logical entities (+ physical tables) ──────────────────
    #: (db, object) lower-cased → the logical entity key, so attributes resolve.
    obj_to_entity: Dict[Tuple[str, str], str] = {}
    for row in snapshot.object_classes:
        if not isinstance(row, dict):
            continue
        ocid = _s(row.get("object_class_id")) or _s(row.get("name"))
        if not ocid:
            continue
        area = _s(row.get("subject_area")).lower()
        object_kind = _s(row.get("object_kind")).lower() or "table"
        deployed = _s(row.get("deployed_object"))
        parts = _split_object(deployed, core_db) if deployed else None

        ent_key = f"{model}.{ocid}"
        add_entity(ent_key, "entity", {
            "label": _s(row.get("name")) or ocid,
            "description": _s(row.get("definition")) or None,
            "subject_area": area or None,
            "object_kind": object_kind,
            "business_key": _s(row.get("natural_key")) or None,
            "grain": _s(row.get("grain")) or None,
            "temporal_pattern": _s(row.get("temporal_pattern")) or None,
            "source_lineage": _s(row.get("source_lineage")) or None,
            "ontology_iri": _s(row.get("standard_class_iri")) or None,
            # what source_validation and the enrichment query for this entity
            "deployed_table": deployed or None,
        })
        stats.entities_logical += 1
        if area:
            add_rel(f"{model}.{area}", "domain", ent_key, "entity", "contains")
        else:
            add_rel(model, "database", ent_key, "entity", "contains")

        # A logical supertype (AbstractParty) has no deployed object — no physical layer.
        if parts and object_kind != "logical":
            db, obj = parts
            obj_to_entity[(db.lower(), obj.lower())] = ent_key
            if emit_physical:
                _ensure_schema(db)
                tbl_key = f"{db}.{obj}"
                add_entity(tbl_key, "table", {"label": obj, "schema": db,
                                              "object_kind": object_kind},
                           layer=LAYER_PHYSICAL, infrastructure=INFRASTRUCTURE_TERADATA)
                add_rel(db, "schema", tbl_key, "table", "contains")
                add_rel(tbl_key, "table", ent_key, "entity", "implements")
                stats.tables_physical += 1

    # ── data_element → logical attributes (+ physical columns) ───────────────
    if opts.include_columns:
        for row in snapshot.data_elements:
            if not isinstance(row, dict):
                continue
            ocid = _s(row.get("object_class_id"))
            prop = _s(row.get("property_name"))
            if not ocid or not prop:
                continue
            ent_key = f"{model}.{ocid}"
            if (ent_key, "entity") not in entities:
                continue  # attribute of an entity that was not declared — skip

            oc = next((r for r in snapshot.object_classes
                       if isinstance(r, dict) and _s(r.get("object_class_id")) == ocid), {})
            parts = _split_object(_s(oc.get("deployed_object")), core_db)

            declared = _s(row.get("declared_type"))
            deployed_type = ""
            phys = None
            if parts:
                db, obj = parts
                phys = snapshot.physical_columns.get((db.lower(), obj.lower(), prop.lower()))
            if phys is not None:
                deployed_type = _s(phys.get("datatype"))
            else:
                # The registry seeds deployed_type from DBC; use it when the live read
                # did not cover this column (a logical-only entity, or an unbound view).
                deployed_type = _s(row.get("deployed_type"))
                if parts:
                    stats.columns_not_deployed += 1
            if declared and deployed_type and _norm_type(declared) != _norm_type(deployed_type):
                stats.type_mismatches += 1

            is_pii = _flag(row.get("is_pii"))
            if is_pii:
                stats.pii_columns += 1
            vd_id = _s(row.get("value_domain_id")) or None
            codes = codes_by_domain.get(vd_id) if vd_id else None
            if codes:
                stats.columns_with_value_codes += 1
            vd_row = vd_by_id.get(vd_id or "", {})

            attr_key = f"{ent_key}.{prop}"
            deployed_col = f"{parts[0]}.{parts[1]}.{prop}" if parts else None
            add_entity(attr_key, "attribute", {
                "label": prop,
                "description": _s(row.get("definition")) or None,
                "declared_type": declared or None,       # the meaning
                "deployed_type": deployed_type or None,   # what is ACTUALLY deployed
                "is_pii": is_pii,
                "data_classification": _s(row.get("sensitivity")) or None,
                "semantic_type": _s(row.get("semantic_type")) or None,
                "allowed_values": codes,
                "value_domain": vd_id,
                "value_domain_scheme": _s(vd_row.get("standard_scheme_iri")) or None,
                "property_iri": _s(row.get("standard_property_iri")) or None,
                "source_column": _s(row.get("source_column")) or None,
                "deployed_column": deployed_col,
            })
            add_rel(ent_key, "entity", attr_key, "attribute", "contains")
            stats.attributes_logical += 1

            if emit_physical and parts:
                db, obj = parts
                col_key = f"{db}.{obj}.{prop}"
                add_entity(col_key, "column", {
                    "label": prop,
                    "datatype": deployed_type or None,   # AUTHORITATIVE — the dictionary
                    "nullable": (phys or {}).get("nullable"),
                    "is_pii": is_pii,
                    "data_classification": _s(row.get("sensitivity")) or None,
                }, layer=LAYER_PHYSICAL, infrastructure=INFRASTRUCTURE_TERADATA)
                add_rel(f"{db}.{obj}", "table", col_key, "column", "contains")
                add_rel(col_key, "column", attr_key, "attribute", "implements")
                stats.columns_physical += 1

    # ── the two-column decode, declared structurally ─────────────────────────
    # A coded field is registered as a PAIR: the raw code column carrying the value
    # domain, and a codeless decoded-label column beside it. The registry links them by
    # the ONE fact both share — `source_column` (the same source field, decoded once) —
    # so the label's own value set (the domain's labels) is derivable without a second
    # declaration. Stamped on the label: `decoded_from` (the code column) and
    # `decoded_labels`; on the code: `decoded_as` (the label column). Read by the
    # enrichment (the label line shows its values) and the preflight value guard (a
    # city name on a Yes/No label, a label on a code column — both provable).
    _by_src: Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = {}
    for (k, et), ent in entities.items():
        if et != "attribute":
            continue
        props = ent.get("properties") or {}
        src = _s(props.get("source_column"))
        owner = k.rpartition(".")[0]
        if src and owner:
            _by_src.setdefault((owner, src.lower()), []).append((k, props))
    for _pair in _by_src.values():
        if len(_pair) != 2:
            continue
        coded = [(k, pr) for k, pr in _pair if pr.get("allowed_values")]
        plain = [(k, pr) for k, pr in _pair if not pr.get("allowed_values")]
        if len(coded) != 1 or len(plain) != 1:
            continue
        (ck, cprops), (lk, lprops) = coded[0], plain[0]
        labels = [str(c.get("label")) for c in (cprops.get("allowed_values") or [])
                  if isinstance(c, dict) and c.get("label") not in (None, "")]
        if not labels:
            continue
        lprops["decoded_from"] = ck.rpartition(".")[2]
        lprops["decoded_labels"] = labels
        cprops["decoded_as"] = lk.rpartition(".")[2]
        stats.decode_pairs += 1

    # ── association → foreign_key / is_a / product_bridge ────────────────────
    for row in snapshot.associations:
        if not isinstance(row, dict):
            continue
        kind = _s(row.get("relationship_kind")).lower()
        from_id = _s(row.get("from_object_class"))
        to_id = _s(row.get("to_object_class"))
        if not from_id or not to_id or not kind:
            continue
        from_key, to_key = f"{model}.{from_id}", f"{model}.{to_id}"
        from_ok = (from_key, "entity") in entities
        to_ok = (to_key, "entity") in entities
        meta = {
            "relationship_name": _s(row.get("association_id")) or None,
            "description": _s(row.get("definition")) or None,
            "from_columns": _s(row.get("from_columns")) or None,
            "to_columns": _s(row.get("to_columns")) or None,
            "join_sql": _s(row.get("join_sql")) or None,
            "standard_relation_iri": _s(row.get("standard_relation_iri")) or None,
        }
        card = _s(row.get("cardinality")) or None

        if kind in ("pk_fk_join", "bridge"):
            if from_ok and to_ok:
                add_rel(from_key, "entity", to_key, "entity", "foreign_key",
                        cardinality=card, metadata=meta)
                stats.foreign_keys += 1
            else:
                stats.associations_dropped += 1
        elif kind == "is_a":
            if from_ok and to_ok:
                add_rel(from_key, "entity", to_key, "entity", "is_a", metadata=meta)
                stats.is_a_edges += 1
            else:
                stats.associations_dropped += 1
        elif kind == "type_correspondence":
            # A DECLARED cross-domain KIND link — never an instance join. Modelled as a
            # product_bridge that states its basis and whether its ends resolved, so an
            # unresolved bridge is a governance finding rather than an invented edge.
            bkey = f"{model}.bridge.{_s(row.get('association_id')) or f'{from_id}__{to_id}'}"
            resolved = from_ok and to_ok
            add_entity(bkey, "product_bridge", {
                "label": _s(row.get("association_id")) or f"{from_id} <-> {to_id}",
                "linkage_mechanism": "type_correspondence",
                "basis": _s(row.get("basis")) or None,
                "definition": _s(row.get("definition")) or None,
                "from_object_class": from_id,
                "to_object_class": to_id,
                # What the correspondence BINDS, as data (registry from_values/to_values):
                # the codes of the from-side column and the values of the to-side column
                # it covers. Absent on a registry that predates the columns — a prose-only
                # basis then remains the only statement of membership.
                "from_columns": _s(row.get("from_columns")) or None,
                "to_columns": _s(row.get("to_columns")) or None,
                "from_values": [v.strip() for v in _s(row.get("from_values")).split(",") if v.strip()] or None,
                "to_values": [v.strip() for v in _s(row.get("to_values")).split(",") if v.strip()] or None,
                "standard_relation_iri": _s(row.get("standard_relation_iri")) or None,
                "source_declaration": _s(row.get("source_declaration")) or None,
                "target_resolved": resolved,
            })
            if from_ok:
                add_rel(bkey, "product_bridge", from_key, "entity", "relates_to")
            if to_ok:
                add_rel(bkey, "product_bridge", to_key, "entity", "relates_to")
            stats.type_correspondences += 1
            if resolved:
                stats.bridges_resolved += 1
            else:
                stats.bridges_unresolved += 1
        else:
            stats.associations_dropped += 1

    return list(entities.values()), relationships
