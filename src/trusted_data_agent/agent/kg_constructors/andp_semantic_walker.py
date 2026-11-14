"""
andp_semantic_walker — pure, deterministic walker for an **AI-Native Data Product**
(ANDP) semantic module → Uderia Knowledge Graph entities/relationships.

WHAT THIS IS FOR
----------------
Any data product built to the AI-Native Data Product standard publishes a
*semantic module* — a machine-readable, agent-discoverable catalog of itself:

    data_product_map    module → database          ("start here" entry point)
    entity_metadata     one row per logical entity (+ ontology IRI)
    column_metadata     one row per column: meaning, PII/sensitivity, value codes
    table_relationship  the join graph

This walker turns that catalog into the platform's canonical two-layer KG
topology, so **every** standard-conformant product becomes consumable-with-trust
on Uderia with zero bespoke work.

PURITY CONTRACT
---------------
No I/O — no network, no DB, no GraphStore.  It takes an already-fetched
:class:`SemanticSnapshot` and returns two plain lists of dicts in the exact shape
``GraphStore.import_bulk(entities, relationships)`` consumes:

    entity       = {"name", "entity_type", "properties": {...}, "source", "source_detail"}
    relationship = {"source_name", "source_type", "target_name", "target_type",
                    "relationship_type", "cardinality"?, "metadata"?, "source"}

Same input → byte-identical output, ordering included.  This is the primary
unit-test target (``test/test_andp_semantic_walker.py``).

THE TYPE RULE (learned the hard way — do not "simplify" this)
-------------------------------------------------------------
**Meaning comes from the ontology; types come from the data dictionary.**

A product's ``column_metadata.data_type`` is *declared* by whoever authored the
catalog and drifts from the deployed schema.  On the reference NIBRS deployment
1,327 of 1,508 columns declare ``INTEGER``/``DATE`` while the deployed columns are
character — because the product deliberately lands its source losslessly as
characters.  A KG built naively on the declared type would mislead the planner
into casting and comparison errors on every one of those columns.

So: the **physical** column node carries ``datatype`` resolved from the data
dictionary (``DBC.ColumnsV``), and the **logical** attribute keeps the catalog's
``declared_type`` as the semantic fact it is.  Disagreements are counted in
:class:`WalkStats.type_mismatches` rather than silently reconciled — a divergence
is a finding about the product, not noise to hide.

NAME QUALIFICATION (correctness, not cosmetics)
-----------------------------------------------
``import_bulk`` resolves relationship endpoints by ``(name, entity_type)`` and the
SQLite backend upserts on ``UNIQUE(kg_id, name, entity_type)``.  Module names
("Domain", "Semantic") are identical across every product, so a graph holding two
products would silently merge them.  Canonical names are therefore qualified, with
a human ``label`` kept in ``properties``:

    database  : product name                    "NIBRS_Source"
    domain    : "<product>.<module>"            "NIBRS_Source.Domain"
    entity    : "<product>.<entity>"            "NIBRS_Source.Victim"
    attribute : "<product>.<entity>.<column>"   "NIBRS_Source.Victim.V4019"
    schema    : "<database>"                    "NIBRS_Source_Domain"
    table     : "<database>.<table>"            "NIBRS_Source_Domain.Victim"
    column    : "<database>.<table>.<column>"   "NIBRS_Source_Domain.Victim.V4019"

Physical names are already globally unique in a Teradata namespace, so they are
used verbatim — which is what makes the grounding gate's trusted scope resolve
against real, queryable identifiers.
"""

from __future__ import annotations

import json
import re
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("quart.app")

# Layers (mirrors industry_model_walker)
LAYER_LOGICAL = "logical"
LAYER_PHYSICAL = "physical"

#: Provenance marker stamped on every node/edge this walker produces.
SOURCE_TAG = "andp_semantic_constructor"

#: Physical infrastructure this walker realizes against.
INFRASTRUCTURE_TERADATA = "teradata"

#: Modules whose tables are the product's *business* data.  Other modules
#: (Semantic/Memory/Observability) are the product's self-description; they are
#: emitted as structure but never carry the business entities an analyst queries.
BUSINESS_MODULES = frozenset({"domain"})


class WalkError(RuntimeError):
    """Raised when the snapshot is unusable (missing/!dict payloads)."""


@dataclass
class WalkStats:
    """Drop/coverage diagnostics — surfaced to the caller, never swallowed."""

    entities_logical: int = 0
    attributes_logical: int = 0
    tables_physical: int = 0
    columns_physical: int = 0
    domains: int = 0
    #: ontology rows whose (database, table) matches no registered entity
    orphan_columns: int = 0
    #: ontology rows with no counterpart in the data dictionary
    columns_not_deployed: int = 0
    #: deployed columns with no ontology row (coverage gap in the product)
    columns_undocumented: int = 0
    #: declared type disagrees with the deployed type (see module docstring)
    type_mismatches: int = 0
    #: relationships whose endpoints could not be resolved
    relationships_dropped: int = 0
    #: columns carrying a decoded value enumeration
    columns_with_value_codes: int = 0
    pii_columns: int = 0

    # ── product-description layer ──
    glossary_terms: int = 0
    #: glossary terms attached to the entity/attribute they define (vs the product root)
    glossary_linked: int = 0
    query_recipes: int = 0
    #: recipes linked to at least one entity their SQL names
    recipes_linked: int = 0
    design_decisions: int = 0
    decisions_linked: int = 0
    naming_standards: int = 0
    families: int = 0        # repeating groups resolved from titles (one per family)
    references: int = 0      # description-named column dependencies recorded
    product_bridges: int = 0
    #: bridges whose declared target resolved to a real entity in this graph
    bridges_resolved: int = 0
    #: bridges whose declared target does NOT exist — reported, never invented
    bridges_unresolved: int = 0
    quality_metrics: int = 0
    quality_linked: int = 0
    modules_enriched: int = 0
    lineage_edges: int = 0
    #: lineage from an EXTERNAL artefact (a file, another system) — recorded as
    #: provenance on the target table rather than as an edge to an invented node
    lineage_external: int = 0
    #: lineage rows whose TARGET is not a table in this graph
    lineage_dropped: int = 0

    def as_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


@dataclass
class SemanticSnapshot:
    """An already-fetched ANDP semantic module.

    Every field is a list of plain dicts exactly as read from the product's own
    catalog tables (column names lower-cased by the fetcher).  ``physical_columns``
    is the data-dictionary read that makes the type rule enforceable.
    """

    product: str
    semantic_database: str
    data_product_map: List[Dict[str, Any]] = field(default_factory=list)
    entity_metadata: List[Dict[str, Any]] = field(default_factory=list)
    column_metadata: List[Dict[str, Any]] = field(default_factory=list)
    table_relationship: List[Dict[str, Any]] = field(default_factory=list)
    #: {(database, table, column): {"datatype": str, "nullable": bool}} from DBC.ColumnsV
    physical_columns: Dict[Tuple[str, str, str], Dict[str, Any]] = field(default_factory=dict)

    # ── the product's own description of itself (Memory + Observability) ──
    # A conformant product publishes far more than its schema.  These are first-class
    # graph content, not documentation: a proven query attached to the entity it
    # queries, or a quality reading attached to the table it measures, is exactly what
    # an agent needs when planning — and none of it can be inferred from the schema.
    business_glossary: List[Dict[str, Any]] = field(default_factory=list)
    query_cookbook: List[Dict[str, Any]] = field(default_factory=list)
    design_decisions: List[Dict[str, Any]] = field(default_factory=list)
    module_registry: List[Dict[str, Any]] = field(default_factory=list)
    naming_standards: List[Dict[str, Any]] = field(default_factory=list)
    data_lineage: List[Dict[str, Any]] = field(default_factory=list)
    quality_metrics: List[Dict[str, Any]] = field(default_factory=list)
    #: rows from the product's own cross-product crosswalk table(s), if any
    product_crosswalks: List[Dict[str, Any]] = field(default_factory=list)
    #: v2 consumer surfaces.  Optional by design: a product built to the earlier
    #: standard publishes neither, and must walk exactly as it did before.
    access_objects: List[Dict[str, Any]] = field(default_factory=list)
    trust_gate: Optional[Dict[str, Any]] = None

    #: free-form provenance shown on the database root (host, fetched_at, …)
    source_detail: str = ""


@dataclass
class WalkOptions:
    """Controls layers and granularity."""

    #: {"logical"} or {"logical", "physical"} — logical is always emitted because
    #: the physical layer `implements` it and its targets must exist.
    layers: frozenset = frozenset({LAYER_LOGICAL, LAYER_PHYSICAL})
    #: emit attribute/column nodes.  Off = entity-level graph only (much smaller).
    include_columns: bool = True
    #: emit the product's self-describing modules (Semantic/Memory/Observability)
    #: as structure.  Off = business Domain module only.
    include_non_business_modules: bool = True
    #: cap on decoded values kept per attribute (keeps node payloads bounded)
    max_value_codes: int = 60
    #: emit the product-description layer: glossary, query recipes, design
    #: decisions, naming standards, lineage and quality readings.
    include_product_knowledge: bool = True
    #: cap on characters kept from a recipe's SQL template (bounded node payload)
    max_sql_chars: int = 4000


# ─────────────────────────────────────────────────────────────────────────────
# pure helpers
# ─────────────────────────────────────────────────────────────────────────────

from .walker_util import _s, _flag, _norm_type, _jsonable  # noqa: E402,F401 — shared with the declared-model walker



def parse_value_codes(raw: Any, limit: int = 60) -> Optional[List[Dict[str, str]]]:
    """Parse ``allowed_values_json`` into a bounded [{value,label}] list.

    This is the single highest-value payload in the whole graph: it is what lets an
    agent know ``V4019 = 0`` means Female instead of guessing.  Parsing is
    defensive — a malformed catalog entry must degrade to "no codes", never raise.
    """
    if raw is None or raw == "":
        return None
    doc = raw
    if isinstance(doc, (str, bytes, bytearray)):
        try:
            doc = json.loads(doc)
        except (ValueError, TypeError):
            return None
    if not isinstance(doc, list):
        return None
    out: List[Dict[str, str]] = []
    for item in doc:
        if not isinstance(item, dict):
            continue
        value, label = _s(item.get("value")), _s(item.get("label"))
        if value == "" and label == "":
            continue
        # A value declared as an integer RANGE ("-5 to -9") is a set of codes with one
        # meaning — typically the codebook's missing/unknown family. Expanded to its
        # members so a predicate can name them; the range itself is kept as `range`
        # so nothing the product said is lost. Generic: any codebook, any range.
        m = _RANGE_RE.match(value)
        if m:
            first, last = int(m.group(1)), int(m.group(2))
            if abs(last - first) <= 20:
                step = 1 if last >= first else -1
                for v in range(first, last + step, step):  # in the DECLARED direction
                    out.append({"value": str(v), "label": label, "range": value})
                    if len(out) >= limit:
                        break
                continue
        out.append({"value": value, "label": label})
        if len(out) >= limit:
            break
    return out or None


_RANGE_RE = re.compile(r"^\s*(-?\d+)\s*(?:to|\.\.|–|-)\s*(-?\d+)\s*$")




def _first(row: Dict[str, Any], *keys: str) -> str:
    """First non-empty value among ``keys``.

    Catalog column names vary between products built to the same standard (the
    reference deployment has ``entity_description`` on one product and
    ``description`` on another, ``surrogate_key_column`` vs ``primary_key``, …).
    Reading through aliases keeps the walker working across that drift instead of
    silently emitting empty nodes.
    """
    for key in keys:
        value = _s(row.get(key))
        if value:
            return value
    return ""


def _entity_display(row: Dict[str, Any]) -> str:
    """The entity's business name, falling back to its table name."""
    return _first(row, "entity_name", "table_name")


# ─────────────────────────────────────────────────────────────────────────────
# the walk
# ─────────────────────────────────────────────────────────────────────────────


# ── Repeating groups, references — generic over any codebook ──────────────────────
_TITLE_NUM_RE = re.compile(r"(?:\s*-\s*|\s+|-)(\d{1,2})\s*$")


def parse_title_positions(description: Optional[str], column: str):
    """The TITLE of a column description and the position numbers it ends with.

    A codebook titles a column before it explains it: "OFFENDER(S) SUSPECTED OF USING
    1 - 1 - V20081 represents …". The title is the text before the clause that names
    the column itself (or before the first sentence when nothing does); trailing
    numbers on the title are POSITIONS of a repeating group — "… USING 1 - 1" is
    positions [1, 1], "AGE OF VICTIM-1" is [1]. Generic: no column prefix, no product
    name, no known family list. Returns (title_without_positions, positions)."""
    if not description:
        return "", []
    text = description.strip()
    col = (column or "").strip()
    # the clause that names the column ends the title
    if col:
        m = re.search(r"(?:^|\s+-\s+|\.\s+|:\s+)" + re.escape(col) + r"\b", text)
        if m and m.start() > 0:
            text = text[:m.start()]
    text = re.split(r"\.\s|:\s|\n", text, maxsplit=1)[0].strip()
    positions: List[int] = []
    while True:
        m = _TITLE_NUM_RE.search(text)
        if not m or len(text) - len(m.group(0)) < 3:
            break
        positions.insert(0, int(m.group(1)))
        text = text[:m.start()].rstrip(" -")
    return " ".join(text.split()[:14]), positions


def _resolve_families(entities, add_rel, stats) -> None:
    """Attributes of ONE entity whose titles are equal once their positions are stripped
    form a REPEATING GROUP: one fact stored once per slot. Each member is stamped with
    the family stem, its size, the number of position axes, and its siblings, so a
    consumer (the enrichment, the seeder) treats the family as one thing without a
    heuristic. A title with no positions, or no sibling, forms no family."""
    groups: Dict[tuple, List[tuple]] = {}
    for key, ent in entities.items():
        if ent.get("entity_type") != "attribute":
            continue
        props = ent.get("properties") or {}
        title, positions = props.get("title"), props.get("positions")
        if not title or not positions:
            continue
        owner = ent["name"].rpartition(".")[0]
        groups.setdefault((owner, str(title).lower()), []).append(key)
    for (owner, title), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda k: (entities[k]["properties"].get("positions") or [], entities[k]["name"]))
        axes = max(len(entities[k]["properties"].get("positions") or []) for k in members)
        leaves = [entities[k]["name"].rpartition(".")[2] for k in members]
        for k in members:
            p = entities[k]["properties"]
            p["family"] = title
            p["family_size"] = len(members)
            p["family_axes"] = axes
            p["family_members"] = leaves
        if stats is not None:
            stats.families = getattr(stats, "families", 0) + 1


def _resolve_references(entities, add_rel, stats) -> None:
    """A description that names ANOTHER column of the same entity by its exact
    identifier declares a dependency ("… for the offense listed in V20061", "… the
    victim shown in V40061"). Recorded as `refers_to` on the attribute so a consumer
    can see which column an attribute is qualified by. Exact-identifier matches only;
    never inferred from prose."""
    by_owner: Dict[str, Dict[str, str]] = {}
    for key, ent in entities.items():
        if ent.get("entity_type") == "attribute":
            nm = ent["name"]
            by_owner.setdefault(nm.rpartition(".")[0], {})[nm.rpartition(".")[2].lower()] = nm
    for key, ent in list(entities.items()):
        if ent.get("entity_type") != "attribute":
            continue
        props = ent.get("properties") or {}
        desc = props.get("description") or ""
        if not desc:
            continue
        nm = ent["name"]; owner = nm.rpartition(".")[0]; leaf = nm.rpartition(".")[2].lower()
        siblings = by_owner.get(owner, {})
        refs = sorted({siblings[t.lower()] for t in set(re.findall(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b", desc))
                       if t.lower() != leaf and t.lower() in siblings})
        if refs:
            # A property, deliberately NOT an edge: an edge is walked by the subgraph
            # extractor and rendered as a relationship line — measured, that pulled the
            # referenced columns into every subgraph and grew the prompt by a third for
            # nothing the agent could use. The fact belongs on the attribute's own line.
            props["refers_to"] = [r.rpartition(".")[2] for r in refs]
            if stats is not None:
                stats.references = getattr(stats, "references", 0) + len(refs)


def walk_andp_semantic(
    snapshot: SemanticSnapshot,
    opts: Optional[WalkOptions] = None,
    stats: Optional[WalkStats] = None,
    known_entities: Optional[Sequence[Tuple[str, str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Walk an ANDP semantic module into (entities, relationships).

    Raises:
        WalkError: if the snapshot has no product name or an unknown layer is asked for.
    """
    if not isinstance(snapshot, SemanticSnapshot):
        raise WalkError("snapshot must be a SemanticSnapshot")
    product = _s(snapshot.product)
    if not product:
        raise WalkError("snapshot.product is required (the data product's name)")

    opts = opts or WalkOptions()
    layers = frozenset(opts.layers or {LAYER_LOGICAL})
    unsupported = layers - {LAYER_LOGICAL, LAYER_PHYSICAL}
    if unsupported:
        raise WalkError(
            f"Unknown layer(s) {sorted(unsupported)}; supported: 'logical', 'physical'."
        )
    emit_physical = LAYER_PHYSICAL in layers
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
        # Drop empties so nodes carry only content that exists. False/0 are kept —
        # `is_pii=False` is a real assertion, not an absence.
        props = {k: _jsonable(v) for k, v in props.items()
                 if v not in (None, "", [], {})}
        entities[key] = {
            "name": name, "entity_type": etype, "properties": props,
            "source": src, "source_detail": src_detail,
        }

    def add_rel(sname: str, stype: str, tname: str, ttype: str, rtype: str,
                cardinality: Optional[str] = None,
                metadata: Optional[Dict[str, Any]] = None) -> None:
        rel = {
            "source_name": sname, "source_type": stype,
            "target_name": tname, "target_type": ttype,
            "relationship_type": rtype, "source": src,
        }
        if cardinality is not None:
            rel["cardinality"] = cardinality
        if metadata is not None:
            rel["metadata"] = _jsonable(metadata)
        relationships.append(rel)

    # ── the v2 consumer surfaces, indexed once ───────────────────────────────
    # `access_object` answers "which object should I actually query" — the fact a
    # name cannot carry, and the reason a PII-free projection is reachable at all.
    consumable_for: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in (snapshot.access_objects or []):
        if not isinstance(row, dict):
            continue
        entity_name = _s(row.get("represents_entity"))
        if not entity_name or not _flag(row.get("is_agent_consumable")):
            continue
        db = _s(row.get("database_name"))
        obj = _s(row.get("object_name"))
        if not obj:
            continue
        key = (entity_name.lower(), _s(row.get("access_role")).upper())
        consumable_for[key] = {
            "object": f"{db}.{obj}" if db else obj,
            "note": _s(row.get("access_note")) or None,
            "role": _s(row.get("access_role")).upper() or None,
            "resolves_to": _s(row.get("resolves_to_object")) or None,
        }

    # Documented columns per deployed object — the evidence that tells a genuine
    # 1:1 passthrough from a projection that drops columns.
    cols_per_object: Dict[str, set] = {}
    for row in (snapshot.column_metadata or []):
        if not isinstance(row, dict):
            continue
        _db, _tbl, _col = (_s(row.get("database_name")), _s(row.get("table_name")),
                           _s(row.get("column_name")))
        if not _tbl or not _col:
            continue
        cols_per_object.setdefault(f"{_db}.{_tbl}".lower() if _db else _tbl.lower(),
                                   set()).add(_col.lower())

    #: Access roles that expose FEWER columns than the object they derive from.
    #: `access_role` is an open vocabulary in the standard ("baseline BASE,
    #: PASSTHROUGH, COMPOSITE; extensions add"), and `resolves_to_object` is
    #: defined for *1:1* passthroughs — so a column-dropping view is not a
    #: PASSTHROUGH, and saying so is conformance, not extension for its own sake.
    PROJECTION_ROLES = {"RESTRICTED_PROJECTION", "PROJECTION"}

    def _is_projection(cand: Dict[str, Any]) -> bool:
        """Does this object expose fewer columns than the one it derives from?"""
        if (cand.get("role") or "") in PROJECTION_ROLES:
            return True
        # Defence in depth: a product may register a projection AS a passthrough
        # (this one did, and every consumer was then sent to a PII-free view).
        # Strict subset is the honest test — a partially-documented object is not
        # evidence of anything, so an unrelated column set decides nothing.
        src = (cand.get("resolves_to") or "").lower()
        own = cols_per_object.get((cand.get("object") or "").lower())
        base = cols_per_object.get(src)
        return bool(src and own and base and own < base)

    def _consumable(entity_name: str) -> Optional[Dict[str, Any]]:
        """The object a consumer should query for this entity.

        Precedence: a genuine 1:1 PASSTHROUGH (the sanctioned surface, same
        columns) -> BASE (the authoritative object) -> a restricted PROJECTION
        -> anything else.

        A projection is never preferred while a complete object exists. Pointing an
        analytical agent at an object that drops documented columns turns an
        answerable question into an error — or, worse, into a plausible number
        computed from whatever survived the projection. Who may read what is
        enforced by the database; this only decides what to DESCRIBE, and
        describing the restricted object as THE object fails toward a wrong
        answer instead of a clean denial.
        """
        low = entity_name.lower()
        cands = [v for (n, _r), v in consumable_for.items() if n == low]
        if not cands:
            return None
        complete = [c for c in cands if not _is_projection(c)]
        pool = sorted(complete or cands,
                      key=lambda c: 0 if (c.get("role") == "PASSTHROUGH") else 1)
        chosen = dict(pool[0])
        alts = [c["object"] for c in cands if c["object"] != chosen["object"]]
        if alts:
            chosen["alternatives"] = alts
        return chosen

    # ── root: the data product ───────────────────────────────────────────────
    modules = [r for r in snapshot.data_product_map if isinstance(r, dict)]
    gate = snapshot.trust_gate if isinstance(snapshot.trust_gate, dict) else None
    add_entity(product, "database", {
        "label": product,
        "product": product,
        "semantic_database": _s(snapshot.semantic_database),
        "standard": "ai_native_data_product",
        "module_count": len(modules),
        # The product's OWN verdict on itself.  Absent means "never validated",
        # which is not the same as trusted and must never render as trusted.
        "trust_status": (_s(gate.get("trust_status")) or None) if gate else None,
        "agent_use_allowed": _flag(gate.get("agent_use_allowed")) if gate else None,
        "validated_at": (_s(gate.get("completed_dts")) or None) if gate else None,
        "validation_evidence_expires": (_s(gate.get("evidence_expires_dts")) or None
                                        ) if gate else None,
    })

    # ── modules → logical domains, and their databases → physical schemas ────
    db_to_module: Dict[str, str] = {}
    for row in modules:
        module = _s(row.get("module_name"))
        database = _s(row.get("database_name"))
        if not module:
            continue
        is_business = module.lower() in BUSINESS_MODULES
        if not is_business and not opts.include_non_business_modules:
            continue
        if database:
            db_to_module[database.lower()] = module

        dom_key = f"{product}.{module}"
        add_entity(dom_key, "domain", {
            "label": module,
            "module": module,
            "database_name": database,
            "is_business_module": is_business,
            "primary_tables": _s(row.get("primary_tables")) or None,
        })
        add_rel(product, "database", dom_key, "domain", "contains")
        stats.domains += 1

        if emit_physical and database:
            add_entity(database, "schema", {
                "label": database, "module": module, "product": product,
            }, layer=LAYER_PHYSICAL, infrastructure=INFRASTRUCTURE_TERADATA)
            add_rel(database, "schema", dom_key, "domain", "implements")

    # ── entity_metadata → logical entities (+ physical tables) ───────────────
    # (database, table) → logical entity key, so columns and joins can resolve.
    table_to_entity: Dict[Tuple[str, str], str] = {}
    #: (db, table) lower-cased -> the PHYSICAL table node's real-cased key, so a
    #: column emitted outside the column_metadata loop can hang off the right node.
    table_to_physical: Dict[Tuple[str, str], str] = {}
    entity_to_module: Dict[str, str] = {}

    for row in snapshot.entity_metadata:
        if not isinstance(row, dict):
            continue
        display = _entity_display(row)
        database = _s(row.get("database_name"))
        table = _s(row.get("table_name"))
        if not display:
            continue
        module = _first(row, "module_name", "module") or db_to_module.get(database.lower(), "")
        if module and module.lower() not in BUSINESS_MODULES \
                and not opts.include_non_business_modules:
            continue

        # The product declares its own temporal contract here.  A bi-temporal
        # product wants `WHERE is_current = 1 AND is_deleted = 0`; a source image
        # has no such columns and that predicate would be an error.  Carrying the
        # declaration onto the node is what lets a consumer adapt instead of
        # assuming — see the source-image variant in the pack's primer.
        temporal = _first(row, "temporal_pattern")
        current_flag = _first(row, "current_flag_column")
        deleted_flag = _first(row, "deleted_flag_column")

        ent_key = f"{product}.{display}"
        add_entity(ent_key, "entity", {
            "label": display,
            "description": _first(row, "entity_description", "description") or None,
            "module": module or None,
            "primary_key": _first(row, "surrogate_key_column", "primary_key") or None,
            "business_key": _first(row, "natural_key_column", "business_key") or None,
            "temporal_pattern": temporal or None,
            "current_flag_column": current_flag or None,
            "deleted_flag_column": deleted_flag or None,
            "industry_standard": _first(row, "industry_standard") or None,
            "ontology_iri": _first(row, "ontology_iri") or None,
            "deployed_table": f"{database}.{table}" if database and table else None,
            "deployed_view": _first(row, "view_name") or None,
            # v2: the object metadata says to query, which is not always the base
            # table — a PII-free projection has the same grain and less exposure.
            "consumable_object": (_consumable(display) or {}).get("object"),
            "access_note": (_consumable(display) or {}).get("note"),
            # Other registered objects for the same entity (e.g. a PII-free
            # projection for a restricted role).  Recorded, never preferred.
            "access_alternatives": (_consumable(display) or {}).get("alternatives"),
        })
        stats.entities_logical += 1
        if module:
            entity_to_module[ent_key] = module
            add_rel(f"{product}.{module}", "domain", ent_key, "entity", "contains")
        else:
            add_rel(product, "database", ent_key, "entity", "contains")

        if database and table:
            table_to_entity[(database.lower(), table.lower())] = ent_key

        if emit_physical and database and table:
            tbl_key = f"{database}.{table}"
            add_entity(tbl_key, "table", {
                "label": table, "schema": database, "product": product,
                "primary_key": _s(row.get("primary_key")) or None,
            }, layer=LAYER_PHYSICAL, infrastructure=INFRASTRUCTURE_TERADATA)
            add_rel(database, "schema", tbl_key, "table", "contains")
            add_rel(tbl_key, "table", ent_key, "entity", "implements")
            table_to_physical[(database.lower(), table.lower())] = tbl_key
            stats.tables_physical += 1

    # ── column_metadata → logical attributes (+ physical columns) ────────────
    documented: set = set()
    if opts.include_columns:
        for row in snapshot.column_metadata:
            if not isinstance(row, dict):
                continue
            database = _s(row.get("database_name"))
            table = _s(row.get("table_name"))
            column = _s(row.get("column_name"))
            if not column or not table:
                continue

            ent_key = table_to_entity.get((database.lower(), table.lower()))
            if not ent_key:
                # Documented column whose table is registered nowhere in
                # entity_metadata — a real catalog gap in the product.
                stats.orphan_columns += 1
                continue

            documented.add((database.lower(), table.lower(), column.lower()))
            module = entity_to_module.get(ent_key, "")
            declared = _s(row.get("data_type"))
            phys = snapshot.physical_columns.get(
                (database.lower(), table.lower(), column.lower())
            )
            deployed_type = _s((phys or {}).get("datatype"))
            if phys is None:
                stats.columns_not_deployed += 1
            elif declared and deployed_type and _norm_type(declared) != _norm_type(deployed_type):
                stats.type_mismatches += 1

            is_pii = _flag(row.get("is_pii"))
            is_sensitive = _flag(row.get("is_sensitive"))
            classification = _s(row.get("data_classification")) or None
            codes = parse_value_codes(row.get("allowed_values_json"), opts.max_value_codes)
            if codes:
                stats.columns_with_value_codes += 1
            if is_pii:
                stats.pii_columns += 1

            attr_key = f"{ent_key}.{column}"
            _desc = _s(row.get("business_description")) or None
            _title, _positions = parse_title_positions(_desc, column)
            add_entity(attr_key, "attribute", {
                "label": column,
                "description": _desc,
                "module": module or None,
                "product": product,
                # The description's TITLE (its leading clause, before the column names
                # itself) and the trailing position numbers a repeating group carries
                # ("LOCATION TYPE - 2" → title "LOCATION TYPE", positions [2]). The
                # family itself is resolved after every attribute of the entity is
                # known (see `_resolve_families`).
                "title": _title or None,
                "positions": _positions or None,
                # the catalog's *declared* type — a semantic fact, never the
                # authority for query generation (see module docstring)
                "declared_type": declared or None,
                # ...and what is ACTUALLY deployed, carried on the logical node so a
                # consumer never has to join to the physical layer to learn it.
                # This is the difference that bites: V4019 declares INTEGER, deploys
                # as VARCHAR(2), and `WHERE V4019 = 0` then casts the column — which
                # silently matches every BLANK row as well as every real '0'.
                "deployed_type": deployed_type or None,
                "is_pii": is_pii,
                "is_sensitive": is_sensitive,
                "data_classification": classification,
                "is_required": _flag(row.get("is_required")),
                "allowed_values": codes,
                "property_iri": _s(row.get("property_iri")) or None,
                "deployed_column": f"{database}.{table}.{column}"
                                   if database and table else None,
            })
            add_rel(ent_key, "entity", attr_key, "attribute", "contains")  # ONTOLOGY §2.4: entity→attribute is `contains`
            stats.attributes_logical += 1

            if emit_physical and database and table:
                col_key = f"{database}.{table}.{column}"
                add_entity(col_key, "column", {
                    "label": column,
                    # AUTHORITATIVE type — from the data dictionary
                    "datatype": deployed_type or None,
                    "nullable": (phys or {}).get("nullable"),
                    "is_pii": is_pii,
                    "is_sensitive": is_sensitive,
                    "data_classification": classification,
                }, layer=LAYER_PHYSICAL, infrastructure=INFRASTRUCTURE_TERADATA)
                add_rel(f"{database}.{table}", "table", col_key, "column", "contains")
                add_rel(col_key, "column", attr_key, "attribute", "implements")
                stats.columns_physical += 1

        _resolve_families(entities, add_rel, stats)
        _resolve_references(entities, add_rel, stats)

        # Deployed but undocumented, over registered tables only.
        #
        # The product declares nothing about these columns, so they are NOT logical
        # attributes — admitting one would inflate the logical model and destroy the
        # undocumented-coverage finding. But they ARE deployed, and an agent that
        # cannot see them does not conclude "unknown": it invents column names and
        # burns a round-trip per guess. A conformant product may legitimately curate
        # only part of `column_metadata`, so this is the expected case, not an edge one.
        #
        # They therefore land on the PHYSICAL layer and only there — no `attribute`
        # node, no `implements` edge — carrying `documented: False` so a consumer can
        # always tell a column the product describes from one that merely exists.
        registered = {(d, t) for (d, t) in table_to_entity}
        for (db, tbl, col), phys_row in (snapshot.physical_columns or {}).items():
            if (db, tbl) not in registered or (db, tbl, col) in documented:
                continue
            stats.columns_undocumented += 1
            if not emit_physical:
                continue
            tbl_key = table_to_physical.get((db, tbl))
            if not tbl_key:
                continue
            row = phys_row if isinstance(phys_row, dict) else {}
            # Real-cased where the fetcher supplied it; the lower-cased key is the
            # honest fallback for any caller that predates that.
            col_name = _s(row.get("column")) or col
            col_key = f"{tbl_key}.{col_name}"
            add_entity(col_key, "column", {
                "label": col_name,
                "datatype": row.get("datatype") or None,
                "nullable": row.get("nullable"),
                "documented": False,
            }, layer=LAYER_PHYSICAL, infrastructure=INFRASTRUCTURE_TERADATA)
            add_rel(tbl_key, "table", col_key, "column", "contains")
            stats.columns_physical += 1

    # ── table_relationship → foreign_key edges (both namespaces) ─────────────
    for row in snapshot.table_relationship:
        if not isinstance(row, dict):
            continue
        s_db = _s(row.get("source_database") or row.get("from_database"))
        s_tbl = _s(row.get("source_table") or row.get("from_table"))
        t_db = _s(row.get("target_database") or row.get("to_database"))
        t_tbl = _s(row.get("target_table") or row.get("to_table"))
        s_col = _s(row.get("source_column") or row.get("from_column"))
        t_col = _s(row.get("target_column") or row.get("to_column"))
        if not s_tbl or not t_tbl:
            stats.relationships_dropped += 1
            continue

        s_ent = table_to_entity.get((s_db.lower(), s_tbl.lower())) \
            or _resolve_by_table(table_to_entity, s_tbl)
        t_ent = table_to_entity.get((t_db.lower(), t_tbl.lower())) \
            or _resolve_by_table(table_to_entity, t_tbl)
        if not s_ent or not t_ent:
            stats.relationships_dropped += 1
            continue

        meta = {
            "relationship_name": _first(row, "relationship_name") or None,
            "description": _first(row, "relationship_description",
                                  "relationship_meaning", "description") or None,
            "source_column": s_col or None,
            "target_column": t_col or None,
        }
        meta = {k: v for k, v in meta.items() if v}
        add_rel(s_ent, "entity", t_ent, "entity", "foreign_key",
                cardinality=_s(row.get("cardinality")) or None,
                metadata=meta or None)

        if emit_physical and s_db and t_db and s_col and t_col:
            s_key = f"{s_db}.{s_tbl}.{s_col}"
            t_key = f"{t_db}.{t_tbl}.{t_col}"
            if (s_key, "column") in entities and (t_key, "column") in entities:
                add_rel(s_key, "column", t_key, "column", "foreign_key",
                        cardinality=_s(row.get("cardinality")) or None)

    # ── the product-description layer ────────────────────────────────────────
    if opts.include_product_knowledge:
        _emit_product_knowledge(snapshot, opts, stats, product, entities,
                                table_to_entity, add_entity, add_rel, emit_physical)
        _emit_product_bridges(snapshot, stats, product, entities, table_to_entity,
                              known_entities, add_entity, add_rel)

    return list(entities.values()), relationships


def _emit_product_knowledge(snapshot, opts, stats, product, entities,
                            table_to_entity, add_entity, add_rel, emit_physical) -> None:
    """Emit what the product publishes ABOUT itself, wired to what it describes.

    An unattached node is a footnote; an attached one is context.  Every asset here
    is therefore linked to the entity, table or product it concerns, so the agent
    meets it while planning against that thing rather than having to go looking.
    """
    # Enrich the module (domain) nodes from the registry rather than emitting
    # duplicates — the domain node already exists from data_product_map.
    # Module names are cased differently between a product's own catalogs (the
    # reference product registers "DOMAIN" while its map says "Domain"), so resolve
    # case-insensitively rather than losing the enrichment to a casing mismatch.
    domain_by_lower = {
        name.split(".", 1)[1].lower(): (name, node)
        for (name, etype), node in entities.items()
        if etype == "domain" and "." in name
    }
    for row in snapshot.module_registry:
        if not isinstance(row, dict) or not _current(row):
            continue
        module = _first(row, "module_name")
        if not module:
            continue
        hit = domain_by_lower.get(module.lower())
        if not hit:
            continue
        node = hit[1]
        for prop, keys in (
            ("module_version", ("module_version",)),
            ("deployment_status", ("deployment_status",)),
            ("module_purpose", ("module_purpose",)),
            ("module_scope", ("module_scope",)),
            ("key_entities", ("key_entities",)),
            ("data_owner", ("data_owner",)),
            ("technical_owner", ("technical_owner",)),
            ("refresh_frequency", ("refresh_frequency",)),
            ("design_standard", ("design_standard_doc",)),
            ("design_standard_version", ("design_standard_version",)),
        ):
            value = _first(row, *keys)
            if value:
                node["properties"].setdefault(prop, value)
        stats.modules_enriched += 1

    # ── business glossary → business_concept, linked to what it defines ──
    for row in snapshot.business_glossary:
        if not isinstance(row, dict) or not _current(row):
            continue
        term = _first(row, "term", "term_name")
        if not term:
            continue
        key = f"{product}.term:{term}"
        add_entity(key, "business_concept", {
            "label": term,
            "definition": _first(row, "definition") or None,
            "abbreviation": _first(row, "term_abbreviation") or None,
            "term_category": _first(row, "term_category", "business_domain") or None,
            "business_context": _first(row, "business_context") or None,
            "synonyms": _first(row, "synonyms") or None,
            "related_terms": _first(row, "related_terms") or None,
            "source_reference": _first(row, "source_reference") or None,
            "source_module": _first(row, "source_module") or None,
        })
        stats.glossary_terms += 1

        related_table = _first(row, "related_table")
        related_column = _first(row, "related_column")
        ent_key = _entity_for_table(table_to_entity, related_table)
        if ent_key:
            # A term may define SEVERAL columns (a derived concept spans its predicate's
            # columns — "Digital Channel" names seven); the product writes them as one
            # delimited string, the same forms natural_key_column uses. One edge per
            # resolvable attribute; a term with none resolvable attaches to the entity,
            # so it is never dangling and never silently narrowed to its first column.
            _cols = [c.strip() for c in re.split(r"[,;|]+", str(related_column or "")) if c.strip()]
            _linked = [c for c in _cols if (f"{ent_key}.{c}", "attribute") in entities]
            for c in _linked:
                add_rel(key, "business_concept", f"{ent_key}.{c}", "attribute", "relates_to")
            if not _linked:
                add_rel(key, "business_concept", ent_key, "entity", "relates_to")
            stats.glossary_linked += 1
        else:
            add_rel(key, "business_concept", product, "database", "relates_to")

    # ── query cookbook → query_recipe, linked to the entities it queries ──
    for row in snapshot.query_cookbook:
        if not isinstance(row, dict) or not _current(row):
            continue
        title = _first(row, "recipe_title", "recipe_name", "business_question",
                       "recipe_id", "cookbook_id")
        if not title:
            continue
        sql = _first(row, "sql_template", "sql_query", "sql")
        key = f"{product}.recipe:{title}"
        add_entity(key, "query_recipe", {
            "label": title,
            "recipe_id": _first(row, "recipe_id") or None,
            "business_question": _first(row, "business_question") or None,
            "description": _first(row, "recipe_description",
                                  "expected_result_description") or None,
            "use_case": _first(row, "use_case") or None,
            "sql": sql[: opts.max_sql_chars] or None,
            "sql_truncated": bool(sql and len(sql) > opts.max_sql_chars) or None,
            "complexity": _first(row, "complexity") or None,
            "performance_notes": _first(row, "performance_notes") or None,
            "parameter_descriptions": _first(row, "parameter_descriptions") or None,
            "target_module": _first(row, "target_module") or None,
        })
        stats.query_recipes += 1

        # Link the recipe to the entities it queries.  A product that DECLARES its
        # targets (``target_tables``) is authoritative — always prefer a declaration
        # over inference; fall back to naming the tables in its SQL only when the
        # product says nothing.
        declared = _first(row, "target_tables", "target_table", "target_module")
        targets: List[str] = []
        if declared:
            for name in _split_list(declared):
                ent_key = _entity_for_table(table_to_entity, name)
                if ent_key and ent_key not in targets:
                    targets.append(ent_key)
        if not targets:
            targets = _entities_named_in(sql, table_to_entity)

        for ent_key in targets:
            add_rel(key, "query_recipe", ent_key, "entity", "depends_on")
        if targets:
            stats.recipes_linked += 1
        else:
            add_rel(key, "query_recipe", product, "database", "depends_on")

    # ── design decisions → design_decision, linked to what they shaped ──
    for row in snapshot.design_decisions:
        if not isinstance(row, dict) or not _current(row):
            continue
        title = _first(row, "decision_title", "title", "decision_id")
        if not title:
            continue
        key = f"{product}.decision:{title}"
        add_entity(key, "design_decision", {
            "label": title,
            "decision_id": _first(row, "decision_id") or None,
            "description": _first(row, "decision_description", "decision_taken",
                                  "description") or None,
            "context": _first(row, "context", "context_description") or None,
            "alternatives_considered": _first(row, "alternatives_considered") or None,
            "rationale": _first(row, "rationale", "decision_taken") or None,
            "consequences": _first(row, "consequences") or None,
            "status": _first(row, "decision_status", "status") or None,
            "category": _first(row, "decision_category") or None,
            "decided_by": _first(row, "decided_by") or None,
            "decided_date": row.get("decided_date") or row.get("decided_at") or None,
            "source_module": _first(row, "source_module", "module_name") or None,
        })
        stats.design_decisions += 1

        ent_key = _entity_for_table(table_to_entity, _first(row, "affects_table"))
        if ent_key:
            add_rel(key, "design_decision", ent_key, "entity", "relates_to")
            stats.decisions_linked += 1
        else:
            module = _first(row, "source_module", "module_name")
            target = (f"{product}.{module}", "domain") if module else None
            if target and target in entities:
                add_rel(key, "design_decision", target[0], "domain", "relates_to")
            else:
                add_rel(key, "design_decision", product, "database", "relates_to")

    # ── naming standards → naming_standard, governing the whole product ──
    # These are the rules a consumer must follow to write correct SQL (e.g. "domain
    # columns keep original source names"), so they hang off the product root where
    # any subgraph containing it will surface them.
    for row in snapshot.naming_standards:
        if not isinstance(row, dict) or not _current(row):
            continue
        stype = _first(row, "standard_type")
        svalue = _first(row, "standard_value")
        if not (stype or svalue):
            continue
        label = f"{stype}: {svalue}".strip(": ")
        key = f"{product}.standard:{label}"
        add_entity(key, "naming_standard", {
            "label": label,
            "standard_type": stype or None,
            "standard_value": svalue or None,
            "meaning": _first(row, "meaning") or None,
            "usage_guidance": _first(row, "usage_guidance") or None,
            "applies_to": _first(row, "applies_to") or None,
            "examples": _first(row, "examples") or None,
        })
        add_rel(key, "naming_standard", product, "database", "relates_to")
        stats.naming_standards += 1

    # ── data quality metrics → quality_metric, measuring what they read ──
    for row in snapshot.quality_metrics:
        if not isinstance(row, dict) or not _current(row):
            continue
        metric = _first(row, "metric_name")
        database = _first(row, "database_name")
        table = _first(row, "table_name")
        if not metric or not table:
            continue
        column = _first(row, "column_name")
        scope = f"{database}.{table}" + (f".{column}" if column else "")
        key = f"{product}.quality:{scope}:{metric}"
        add_entity(key, "quality_metric", {
            "label": metric,
            "metric_value": row.get("metric_value"),
            "metric_category": _first(row, "metric_category") or None,
            "quality_threshold": row.get("quality_threshold"),
            "is_threshold_met": _flag(row.get("is_threshold_met")),
            "sample_size": row.get("sample_size"),
            # raw, so _jsonable renders ISO-8601 rather than str(datetime)
            "measured_at": row.get("measured_dts") or None,
            "measures_scope": scope,
        })
        stats.quality_metrics += 1

        ent_key = table_to_entity.get((database.lower(), table.lower())) \
            or _entity_for_table(table_to_entity, table)
        if ent_key:
            add_rel(key, "quality_metric", ent_key, "entity", "measures")
            stats.quality_linked += 1
        elif emit_physical and (f"{database}.{table}", "table") in entities:
            add_rel(key, "quality_metric", f"{database}.{table}", "table", "measures")
            stats.quality_linked += 1
        else:
            add_rel(key, "quality_metric", product, "database", "measures")

    # ── lineage → derives_from edges between deployed tables ──
    # Direction: the TARGET derives from the SOURCE.  Emitted in the physical
    # namespace because lineage is about deployed objects, not meanings.
    for row in snapshot.data_lineage:
        if not isinstance(row, dict) or not _current(row):
            continue
        s_db, s_tbl = _first(row, "source_database"), _first(row, "source_table")
        t_db, t_tbl = _first(row, "target_database"), _first(row, "target_table")
        if not (s_tbl and t_tbl):
            stats.lineage_dropped += 1
            continue
        s_key, t_key = f"{s_db}.{s_tbl}", f"{t_db}.{t_tbl}"
        target_node = entities.get((t_key, "table")) if emit_physical else None
        if target_node is None:
            stats.lineage_dropped += 1
            continue

        meta = {k: v for k, v in {
            "job_name": _first(row, "job_name") or None,
            "transformation_type": _first(row, "transformation_type") or None,
            "source_system": _first(row, "source_system") or None,
        }.items() if v}

        if (s_key, "table") in entities:
            add_rel(t_key, "table", s_key, "table", "derives_from", metadata=meta or None)
            stats.lineage_edges += 1
            continue

        # The source is not a table in this graph.  In practice it is usually an
        # EXTERNAL artefact — the reference product loads from files
        # ("39270-0005-Data.tsv"), so ``source_database`` is NULL and there is no node
        # to point at.  Inventing one would fabricate a table that does not exist, so
        # the provenance is recorded ON the target it actually describes.
        origin = s_tbl if not s_db else s_key
        existing = target_node["properties"].get("derives_from_external")
        origins = list(existing) if isinstance(existing, list) else (
            [existing] if existing else [])
        if origin not in origins:
            origins.append(origin)
        target_node["properties"]["derives_from_external"] = origins
        for prop_key, value in (("load_job", meta.get("job_name")),
                                ("source_system", meta.get("source_system")),
                                ("transformation_type", meta.get("transformation_type"))):
            if value:
                target_node["properties"].setdefault(prop_key, value)
        stats.lineage_external += 1


def _emit_product_bridges(snapshot, stats, product, entities, table_to_entity,
                          known_entities, add_entity, add_rel) -> None:
    """Emit a product's DECLARED links to other data products — with their basis.

    A bridge is a *claim*, not a join.  The reference product declares two, and names
    their own mechanism: one is literally ``"Synthetic Crosswalk Key"``.  It also
    points at ``incident_Current`` / ``offender_Current`` — the ANDP bi-temporal
    convention — while the target product is a source image that deployed ``Incident``
    and ``Offender``, so the targets do not exist.  It was authored against an assumed
    product, never run against the real one.

    So the rule here is: **surface the claim, state its basis, and say whether its
    targets resolved.**  Never normalise ``X_Current`` → ``X`` (that is guessing), and
    never emit an edge to a node that does not exist.  A dangling declaration is a
    governance finding worth showing, not an error worth hiding.
    """
    for row in snapshot.product_crosswalks:
        if not isinstance(row, dict) or not _current(row):
            continue
        concept = _first(row, "semantic_bridge_concept", "bridge_concept",
                         "relationship_name", "crosswalk_name")
        src_ref = _first(row, "case_entity_type", "source_entity_type",
                         "source_entity", "source_table")
        tgt_tbl = _first(row, "nibrs_target_table", "target_table", "target_entity")
        if not concept and not (src_ref and tgt_tbl):
            continue
        label = concept or f"{src_ref} → {tgt_tbl}"

        src_ent = _entity_for_table(table_to_entity, src_ref)
        tgt_ent = (_entity_for_table(table_to_entity, tgt_tbl)
                   or _resolve_external(known_entities, tgt_tbl))
        resolved = bool(tgt_ent)
        if resolved:
            stats.bridges_resolved += 1
        else:
            stats.bridges_unresolved += 1

        key = f"{product}.bridge:{label}"
        add_entity(key, "product_bridge", {
            "label": label,
            "links": f"{src_ref} → {tgt_tbl}" if (src_ref and tgt_tbl) else None,
            # the basis is the point — never let a bridge read as a verified join
            "linkage_mechanism": _first(row, "linkage_mechanism", "link_basis",
                                        "mapping_method") or "undeclared",
            "mapping_notes": _first(row, "mapping_notes", "notes") or None,
            "source_reference": src_ref or None,
            "source_property": _first(row, "case_property_or_facet",
                                      "source_column") or None,
            "target_reference": tgt_tbl or None,
            "target_column": _first(row, "nibrs_target_column", "target_column") or None,
            "declared_in": _first(row, "_crosswalk_table") or None,
            "resolution": ("target resolved" if resolved
                           else f"target '{tgt_tbl}' not found in this graph"),
            "target_resolved": resolved,
        })
        stats.product_bridges += 1

        # attach to whatever genuinely exists — the bridge is still worth finding
        # from the side that resolves, even when the other end dangles
        if src_ent:
            add_rel(key, "product_bridge", src_ent, "entity", "relates_to")
        else:
            add_rel(key, "product_bridge", product, "database", "relates_to")
        if tgt_ent:
            add_rel(key, "product_bridge", tgt_ent, "entity", "relates_to")


def _resolve_external(known_entities, table: Any) -> Optional[str]:
    """Resolve a bridge target against entities from ANOTHER product in the graph.

    Populated when a product is added to an existing graph, so a cross-product link
    can resolve to the other product's entity.  Matching is on the entity's own label
    (its business name), never on a normalised guess.
    """
    name = _s(table)
    if not name or not known_entities:
        return None
    if "." in name:
        name = name.rpartition(".")[2]
    hits = [full for (full, label) in known_entities if label.lower() == name.lower()]
    return hits[0] if len(hits) == 1 else None


def _current(row: Dict[str, Any]) -> bool:
    """Honour the standard's soft-delete / SCD2 flags.

    Products use ``is_active`` (catalog tables) or ``is_current`` (the Type-2
    history tables in the Memory module).  Absent → current.
    """
    for key in ("is_active", "is_current"):
        if key in row:
            value = row.get(key)
            if value is None:
                continue
            if _s(value).lower() in ("0", "n", "no", "false", "f"):
                return False
    return True


def _split_list(raw: Any) -> List[str]:
    """Split a delimited catalog field ("a, b; c") into clean parts."""
    import re as _re
    return [p.strip() for p in _re.split(r"[,;|\n]+", _s(raw)) if p.strip()]


def _entity_for_table(table_to_entity: Dict[Tuple[str, str], str],
                      table: Any) -> Optional[str]:
    """Resolve a bare table reference to its logical entity, unambiguously only."""
    name = _s(table)
    if not name:
        return None
    if "." in name:                      # qualified "db.table"
        db, _, tbl = name.rpartition(".")
        hit = table_to_entity.get((db.lower(), tbl.lower()))
        if hit:
            return hit
        name = tbl
    return _resolve_by_table(table_to_entity, name)


def _entities_named_in(sql: str, table_to_entity: Dict[Tuple[str, str], str]
                       ) -> List[str]:
    """Entities whose table is referenced in ``sql`` — deterministic, no parsing.

    A whole-word, case-insensitive match on the table name.  Deliberately
    conservative: it links a recipe to tables it demonstrably names, and silently
    links nothing when it cannot tell, rather than guessing a relationship.
    """
    if not sql:
        return []
    import re as _re
    lowered = sql.lower()
    found: List[str] = []
    seen: set = set()
    for (_db, tbl), ent_key in table_to_entity.items():
        if ent_key in seen or not tbl:
            continue
        if _re.search(rf"\b{_re.escape(tbl)}\b", lowered):
            found.append(ent_key)
            seen.add(ent_key)
    return sorted(found)


def _resolve_by_table(table_to_entity: Dict[Tuple[str, str], str],
                      table: str) -> Optional[str]:
    """Resolve an entity by bare table name when the catalog omits the database.

    Only accepted when it is **unambiguous** — a bare name matching two tables in
    different databases would otherwise attach a join to the wrong entity, which is
    worse than dropping it.
    """
    needle = table.lower()
    hits = {ent for (db, tbl), ent in table_to_entity.items() if tbl == needle}
    return hits.pop() if len(hits) == 1 else None


def scope_databases(snapshot: SemanticSnapshot,
                    business_only: bool = True) -> List[str]:
    """The product's declared, queryable databases — the grounding gate's scope.

    This is what makes an answer refusable: the product itself declares where its
    data lives, so anything outside it is provably out of scope.
    """
    out: List[str] = []
    for row in snapshot.data_product_map:
        if not isinstance(row, dict):
            continue
        database = _s(row.get("database_name"))
        module = _s(row.get("module_name")).lower()
        if not database:
            continue
        if business_only and module not in BUSINESS_MODULES:
            continue
        if database not in out:
            out.append(database)
    return out
