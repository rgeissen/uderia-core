"""
KG Store typed data classes **and the canonical KG vocabulary**.

Thin wrappers around the plain-dict API that GraphStore already returns so
callers can continue using dicts while typed code can use these classes.

THE VOCABULARY IS DEFINED HERE, ONCE
------------------------------------
Entity/relationship types were previously written out in three places
(``graph_store.py``, ``sqlite_backend.py``, ``conformance.py``) plus two copies of
the subgraph-expansion sets — each carrying a comment claiming it was "identical"
to another.  Three hand-maintained copies of one vocabulary drift the first time a
type is added, and the failure is silent: an entity validates in one layer and is
rejected (or, worse, never expanded into the agent's context) in the next.

This module is the lowest layer in the KG stack and depends on nothing, so it is
the correct home.  Every other module imports and re-exports from here.
**Do not re-introduce a literal type set anywhere else** — ``test_kg_vocabulary.py``
asserts identity (not equality) across the consumers, so a new copy fails CI.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Canonical KG vocabulary ─────────────────────────────────────────────────
#
# Logical structural backbone (LDM, infrastructure-neutral): `entity` = a logical
# relation (its meaning), `attribute` = a logical field.
LOGICAL_STRUCTURAL_TYPES = frozenset({
    "database", "domain", "entity", "attribute",
    "foreign_key", "business_concept", "taxonomy", "metric",
})

# Product-description layer (logical): what a governed data product publishes
# ABOUT itself — proven queries, recorded decisions, measured quality, and the
# conventions a consumer must follow.  Introduced by the AI-Native Data Product
# constructor (the richest conformant source of them) but source-neutral: any KG
# may carry them.
PRODUCT_DESCRIPTION_TYPES = frozenset({
    "query_recipe",      # a proven, executable query answering a business question
    "design_decision",   # a recorded decision + rationale that shaped the model
    "quality_metric",    # a measured data-quality signal about an entity
    "naming_standard",   # a convention a consumer must follow to query correctly
    "product_bridge",    # a DECLARED link between two data products (see below)
})

# A `product_bridge` is deliberately NOT a `business_concept`.  A glossary term is an
# authoritative definition; a bridge is a *claimed* mapping between two products whose
# basis may be anything from a real shared key to — as seen on the reference deployment
# — a self-declared "Synthetic Crosswalk Key" pointing at a table that was never
# deployed.  Filing unverified mappings among definitions would hide exactly the
# distinction a reader needs, so the bridge carries its own type and always states its
# `linkage_mechanism` and whether its targets resolved.

# Governed-consumption layer (logical): an OBDA-READ **data product** — a producer-declared,
# machine-validated, signable consumption contract over a subgraph of the ontology
# (entities + frozen deployed mappings + declared joins + measures + an exposure
# allowlist; the full manifest lives in the node's `properties`).  Deliberately NOT a
# `product_description` type: those describe an asset ABOUT itself; a `data_product`
# node IS the signable contract through which the asset is consumed.  Members are
# linked via `member` edges (data_product → entity).  Inert vocabulary until a product
# is authored — creating one is gated by the OBDA-READ feature (docs/roadmap/
# TIER_5.5_OBDA_SCOPE.md §2.1).
DATA_PRODUCT_TYPES = frozenset({"data_product"})

# OBDA-ACT (PROP-008 §5.1): an **action type** — a signed, machine-validated WRITE
# contract over ONE entity (target table frozen, grain, writable allowlist with value
# domains, pre/post conditions, a row bound, the DB role that holds exactly the grant).
# Linked to its target by `acts_on` (action_type → entity). Like `data_product` it is a
# contract STACKED ON the ontology, excluded from the ontology's content hash and signed
# on its own. Deliberately NOT in SEMANTIC_TYPES: an action reaches an agent's context
# only when that agent's grant names it (§2b — the grant is also the context filter, B4);
# until then it is visible in the inspector and invisible to every agent.
ACTION_TYPES = frozenset({"action_type"})

LOGICAL_TYPES = LOGICAL_STRUCTURAL_TYPES | PRODUCT_DESCRIPTION_TYPES | DATA_PRODUCT_TYPES | ACTION_TYPES

# Physical layer (PDM, infrastructure-specific): the deployed, queryable
# realizations.  Every entity carries a `layer` property ("logical" | "physical");
# physical entities also carry `infrastructure`.
PHYSICAL_TYPES = frozenset({"catalog", "schema", "table", "column", "ddl_artifact"})

# ── Layers ──────────────────────────────────────────────────────────────────
# Every entity carries `properties.layer`. The WALKED model (what a constructor
# re-derives from its source on a resync) is `logical` + `physical`. Two layers are
# NOT walked and must survive a resync:
#   * `authored` — nodes a person or an agent added on top of the model (PROP-008
#     OBDA-MODEL); keyed by `properties.extension_id`, one layer per extender, additive
#     only (an authored node may never modify a base node — §3a of the proposal);
#   * `observed` — assertions ABOUT the model (KG_OBSERVATION_LAYER; reserved here so
#     the ONE replace operation keeps both and neither feature carves the other out).
# `replace_model_layer` deletes only the walked layers and re-imports the fresh walk;
# `clear_graph` stays a delete and removes everything (a delete is a delete).
LOGICAL_LAYER = "logical"
PHYSICAL_LAYER = "physical"
AUTHORED_LAYER = "authored"
OBSERVED_LAYER = "observed"
LAYERS = frozenset({LOGICAL_LAYER, PHYSICAL_LAYER, AUTHORED_LAYER, OBSERVED_LAYER})
KEPT_LAYERS_ON_REPLACE = frozenset({AUTHORED_LAYER, OBSERVED_LAYER})

# ── The extension vocabulary (PROP-008 A5 / §3a) ───────────────────────────────
# An authored node belongs to ONE extension — `properties.extension_id`, one layer per
# EXTENDER (a person, or the agents acting for that person). Two teams can both extend a
# signed graph; their layers are hashed, signed, kept and replaced APART. A node written
# before the vocabulary existed carries no id and reads as the `local` extension — the
# honest name for "whoever authored on this instance" (every new write is stamped).
# An authored node's `status` walks drafted → validated → deployed → published; ONLY a
# `published` node is part of the extension's signable content, reaches a released-mode
# agent, or travels with a pack.
EXTENSION_ID_PROP = "extension_id"
DEFAULT_EXTENSION_ID = "local"
AUTHORED_STATUS_PUBLISHED = "published"


def extension_id_for(user_uuid) -> str:
    """The extension an actor writes to: keyed by the OWNING user — an agent drafts on its
    owner's extension (§13: an agent only drafts; the layer is the person's)."""
    u = str(user_uuid or "").strip()
    return f"ext-{u}" if u else DEFAULT_EXTENSION_ID


def extension_id_of(properties) -> str:
    """Which extension an authored node's properties place it in (``local`` when unstamped)."""
    try:
        v = (properties or {}).get(EXTENSION_ID_PROP)
    except AttributeError:
        v = None
    return str(v) if v else DEFAULT_EXTENSION_ID


def is_authored(properties) -> bool:
    try:
        return (properties or {}).get("layer") == AUTHORED_LAYER
    except AttributeError:
        return False


#: The mark the authored-layer deployer (PROP-008 A3) puts on every physical node it
#: writes for an authored node — the table, its columns, and a sibling schema it created.
REALISATION_SOURCE = "authored_deploy"


def unpublished_extension_names(entities) -> set:
    """PROP-008 §2b — every node that belongs to an UNPUBLISHED extension, by name. Pure.

    An extension reaches the model's views and an agent's context only once it is published,
    and an extension is more than its authored nodes: landing it writes PHYSICAL nodes too.
      · an authored node whose status is not ``published``                (the draft itself)
      · a node the deployer wrote (``authored_deploy``) whose ``realises`` names one of those
                                                                (its table and its columns)
      · a schema the deployer created that holds ONLY such tables        (the sibling database)
    Before this, a landed-but-unpublished Lead's table and columns showed in the model and
    could seed an agent's context by name — the concept leaked through its realisation."""
    ents = [e for e in (entities or []) if isinstance(e, dict) and e.get("name")]
    authored = set()
    for e in ents:
        pr = e.get("properties") or {}
        if pr.get("layer") == "authored" and (pr.get("status") or "draft") != "published":
            authored.add(str(e["name"]))
    if not authored:
        return set()
    realised = set()
    tables_by_schema: dict = {}
    for e in ents:
        pr = e.get("properties") or {}
        if not is_realisation(pr):
            continue
        if str(pr.get("realises") or "") in authored:
            realised.add(str(e["name"]))
        if e.get("entity_type") == "table" and "." in str(e["name"]):
            tables_by_schema.setdefault(str(e["name"]).rsplit(".", 1)[0], []).append(str(e["name"]))
    schemas = set()
    for e in ents:
        pr = e.get("properties") or {}
        if e.get("entity_type") == "schema" and is_realisation(pr):
            tbls = tables_by_schema.get(str(e["name"])) or []
            if tbls and all(t in realised for t in tbls):
                schemas.add(str(e["name"]))
    return authored | realised | schemas


def is_realisation(properties) -> bool:
    """A physical node the deployer wrote to REALISE an authored node — the extension's
    realisation, not the walked base (Act II rehearsal: landing the Lead moved the base
    ontology's seal and blocked its own publication as a 'base mutation')."""
    try:
        return bool((properties or {}).get(REALISATION_SOURCE))
    except AttributeError:
        return False


def is_extension_layer(properties) -> bool:
    """Not the base: an authored node or the physical realisation of one. THE predicate
    for every reader that decides what the base IS — its content hash, the base-only view
    a release pins, and the base-mutation check. `is_authored` stays the narrower question
    (drafting, publishing, an extension's own nodes)."""
    return is_authored(properties) or is_realisation(properties)


#: The ddl_artifacts that are a contract's own structure (an action's ledger, a landed tuning
#: structure) — stacked on the model, never part of it.
STACKED_ARTIFACT_PURPOSES = ("action_ledger", "statistics", "join_index", "partition")
#: Node types that are contracts STACKED ON the model (each signed on its own).
STACKED_ENTITY_TYPES = ("action_type", "data_product")


def is_stacked_contract(entity) -> bool:
    """An action type, a data product, or the structure one of them provisioned: not the
    base model and not an extension's content — a contract signed on its own that reads
    or writes the model. THE predicate for the base hash and the base-mutation check
    (Act II rehearsal: `record_lead --acts_on--> Lead` read as a base edge pointing into
    the extension, so an extension with actions could never be published again)."""
    try:
        et = entity.get("entity_type")
        if et in STACKED_ENTITY_TYPES:
            return True
        return et == "ddl_artifact" and (entity.get("properties") or {}).get("purpose") in STACKED_ARTIFACT_PURPOSES
    except AttributeError:
        return False


#: The authored lifecycle, in order. A later act never walks a node BACK: landing a
#: published node on another warehouse (or re-landing it) keeps it published — demoting it
#: to `deployed` silently unpublished the extension and voided its signature (Act II
#: rehearsal, Oct 2026).
AUTHORED_STATUS_ORDER = ("draft", "validated", "deployed", "published")


def advance_status(current, target: str) -> str:
    """The status after an act that brings a node to ``target``: ``target`` when it is
    later in the lifecycle than ``current``, else ``current`` unchanged. Never a demotion."""
    cur = str(current or "draft")
    rank = {s: i for i, s in enumerate(AUTHORED_STATUS_ORDER)}
    return target if rank.get(target, -1) > rank.get(cur, -1) else cur


#: The authored node types a landing realises (``authored_deploy``: an entity becomes a
#: table, an attribute a column). Only these have a `deployed` stage to pass through.
LANDABLE_AUTHORED_TYPES = ("entity", "attribute")


def awaits_landing(entity_type, status) -> bool:
    """THE publish-order rule: a landable node is published only after it has landed.

    Publishing an unlanded attribute puts a column into the released model that the
    warehouse does not have — every agent grounded on it then writes SQL that fails (Act II,
    Oct 2026: `closure_reason` offered for Publish while still a draft). The Govern dialog's
    note and ``publish_extension``'s refusal both read this one rule."""
    rank = {s: i for i, s in enumerate(AUTHORED_STATUS_ORDER)}
    return (str(entity_type or "") in LANDABLE_AUTHORED_TYPES
            and rank.get(str(status or "draft"), 0) < rank["deployed"])


def is_kept_on_replace(entity, keep_layers=None) -> bool:
    """THE rule of what a base update keeps (``replace_model_layer`` — apply-update, a
    subscription sync, an agent-pack refresh, a declarative redeploy, a version restore).

    A base update replaces only what a WALK produces. It keeps everything stacked on the
    model or extending it, which no walk ever produces:
      · the authored and observed layers                         (``keep_layers``)
      · an extension's REALISATION — the schema, tables and columns its landing wrote
        (``authored_deploy``); the extension is more than its authored nodes, and a walk
        that drops them leaves a concept the model can no longer locate
      · a stacked contract — an action type, a data product, the structure one provisioned;
        each is signed on its own and authored by a person, never walked
    Before this (Oct 2026), a routine update kept the Lead but deleted its landed table and
    columns, and deleted signed action types and data products outright. Pure."""
    try:
        pr = entity.get("properties") or {}
    except AttributeError:
        return False
    keep = KEPT_LAYERS_ON_REPLACE if keep_layers is None else keep_layers
    return pr.get("layer") in keep or is_realisation(pr) or is_stacked_contract(entity)


#: What makes a physical node an extension's realisation — carried onto a walked node of the
#: same name (a walk that reads the warehouse sees the landed table), so it stays the
#: extension's after a base update instead of passing as base.
REALISATION_MARKS = (REALISATION_SOURCE, "realises")


def realisations_of(entities, names) -> list:
    """The realisation nodes of the given authored node names: each node the deployer wrote
    whose ``realises`` is one of them, and a deployer-created schema whose realised tables
    are ALL among those (the same schema rule as ``unpublished_extension_names``). Pure."""
    wanted = {str(n) for n in (names or ())}
    ents = [e for e in (entities or []) if isinstance(e, dict) and e.get("name")]
    if not wanted:
        return []
    picked, picked_names = [], set()
    tables_by_schema: dict = {}
    for e in ents:
        pr = e.get("properties") or {}
        if not is_realisation(pr):
            continue
        if e.get("entity_type") == "table" and "." in str(e["name"]):
            tables_by_schema.setdefault(str(e["name"]).rsplit(".", 1)[0], []).append(str(e["name"]))
        if str(pr.get("realises") or "") in wanted:
            picked.append(e)
            picked_names.add(str(e["name"]))
    for e in ents:
        if e.get("entity_type") == "schema" and is_realisation(e.get("properties")):
            tbls = tables_by_schema.get(str(e["name"])) or []
            if tbls and all(t in picked_names for t in tbls) and str(e["name"]) not in picked_names:
                picked.append(e)
    return picked


def is_published_authored(properties) -> bool:
    return is_authored(properties) and str((properties or {}).get("status") or "draft") == AUTHORED_STATUS_PUBLISHED


def payload_extension_ids(entities) -> frozenset:
    """The extensions a payload CARRIES — the ones a resync replaces (`replace_model_layer`
    keeps every other extension untouched). Pure."""
    out = set()
    for e in entities or []:
        pr = (e or {}).get("properties") or {}
        if is_authored(pr):
            out.add(extension_id_of(pr))
    return frozenset(out)

ENTITY_TYPES = LOGICAL_TYPES | PHYSICAL_TYPES

# `implements` maps a physical entity to the logical entity it realizes
# (e.g. physical table → logical entity). One LDM may have many PDMs.
# `member` declares data-product membership (data_product → entity): which entities a
# signed consumption contract exposes.  NOT `contains` — containment is structural
# ownership (a domain owns its entities); membership is a governance claim over
# entities the product does not own.
RELATIONSHIP_TYPES = frozenset({
    "contains", "foreign_key", "is_a", "has_property",
    "measures", "derives_from", "depends_on", "relates_to",
    "implements", "member",
    # `acts_on` (action_type → entity): the one entity a signed write contract may touch
    "acts_on",
})

# ── Adaptive subgraph extraction ────────────────────────────────────────────
# Types the extractor walks outward through when discovering structure.
EXPANDABLE_STRUCTURAL_TYPES = frozenset({"entity", "foreign_key"})

# Neighbours of a discovered entity that get pulled into the agent's context
# subgraph.  The product-description types belong here: a proven query, a quality
# reading or a naming rule attached to an entity is precisely what the agent needs
# when planning against it.  Omitting a type here leaves its nodes visible in the
# inspector but **invisible to the agent**.  `data_product` belongs here too: an
# entity governed by a signed consumption contract is a fact the agent must meet
# while planning (query the product, don't free-form) — invisible governance is
# no governance.  Inert until a product exists on the graph.
SEMANTIC_TYPES = frozenset({
    "business_concept", "taxonomy", "metric", "domain",
}) | PRODUCT_DESCRIPTION_TYPES | DATA_PRODUCT_TYPES


@dataclass
class KGEntity:
    id: int
    name: str
    entity_type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    source: str = "manual"
    source_detail: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": self.properties,
            "source": self.source,
            "source_detail": self.source_detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class KGRelationship:
    id: int
    source_entity_id: int
    target_entity_id: int
    relationship_type: str
    source_name: str = ""
    source_type: str = ""
    target_name: str = ""
    target_type: str = ""
    cardinality: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "manual"
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "relationship_type": self.relationship_type,
            "cardinality": self.cardinality,
            "metadata": self.metadata,
            "source": self.source,
            "created_at": self.created_at,
        }


@dataclass
class KGBackendConfig:
    """User-facing configuration for a single KG backend instance."""

    id: str
    backend_type: str
    name: str
    config: Dict[str, Any] = field(default_factory=dict)
    is_default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "backend_type": self.backend_type,
            "name": self.name,
            "config": self.config,
            "is_default": self.is_default,
        }
