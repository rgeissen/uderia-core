"""
Graph Store — Thin delegate wrapper over KGStoreBackend.

The public interface is identical to the original implementation so all
existing callers (rest_routes.py, kg_marketplace_routes.py, handler.py,
agent code) continue to work without modification.

Architecture (after abstraction):
    GraphStore(profile_id, user_uuid, [kg_id], [db_path])
      ├── KG metadata  ← kg_metadata table in SQLite (always local)
      │   set_kg_metadata / get_kg_metadata / rename_kg / set_active_kg /
      │   list_all_graphs — these manage the kg_id ↔ name mapping and are
      │   intentionally NOT delegated to the backend because the metadata
      │   table must remain queryable by profile-management code regardless
      │   of which storage backend holds the actual entity/relationship data.
      │
      └── KGStoreBackend  ← pluggable (SQLite, Neo4j, …)
          Entity CRUD, Relationship CRUD, Bulk ops, Stats, Graph algorithms

KnowledgeGraphReadOnlyError is kept in this module so existing callers that
catch it by name (`except KnowledgeGraphReadOnlyError`) continue to work.
"""

import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

logger = logging.getLogger("quart.app")

# Valid entity types (re-exported for callers)
#   Logical (LDM, infrastructure-neutral): database, domain, entity, attribute,
#     foreign_key, business_concept, taxonomy, metric.  `entity` = a logical
#     relation (its meaning), `attribute` = a logical field.
#   Physical (PDM, infrastructure-specific): catalog, schema, table, column,
#     ddl_artifact.  `table`/`column` are the deployed, queryable realizations.
#     Every entity also carries a `layer` property ("logical" | "physical");
#     physical entities carry `infrastructure`.
#   Product-description layer (logical): assets a governed data product publishes
#     ABOUT itself — proven queries, recorded decisions, measured quality, and the
#     conventions a consumer must follow.
#
# The vocabulary itself is defined ONCE in ``trusted_data_agent.kgstore.types`` and
# imported here — it used to be written out separately in three modules, which
# drifts silently the first time a type is added.
from trusted_data_agent.kgstore.types import (  # noqa: E402,F401  (re-exported)
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
)
from trusted_data_agent.kgstore.types import (  # noqa: E402
    LAYERS as _LAYERS,
    AUTHORED_LAYER as _AUTHORED_LAYER,
    KEPT_LAYERS_ON_REPLACE as _KEPT_LAYERS_ON_REPLACE,
)

# Keep these for backwards compat (used by some import paths).
# The logical structural backbone is `entity` (was `table`) + `foreign_key`.
from trusted_data_agent.kgstore.types import (  # noqa: E402,F401  (re-exported)
    EXPANDABLE_STRUCTURAL_TYPES as _EXPANDABLE_STRUCTURAL_TYPES,
    SEMANTIC_TYPES as _SEMANTIC_TYPES,
)
from trusted_data_agent.components import signals as _signals  # noqa: E402

_MAX_JOIN_DISCOVERY_ROUNDS = 3


class KnowledgeGraphReadOnlyError(RuntimeError):
    """
    Raised when a write is attempted on a subscribed (live-tracked) KG.

    Subscribed KGs are point-in-time copies of a publisher's marketplace
    listing.  They are rebuilt on every publisher republish, so subscriber
    edits would be silently lost on the next sync.  The user-visible action
    is "Convert to Fork" — `kg_marketplace_routes` exposes that path.

    Sync, unsubscribe, and other system operations bypass this gate by
    using raw SQL rather than the GraphStore API.
    """
    pass


class GraphStore:
    """
    Thin delegate wrapper over KGStoreBackend, scoped to a single knowledge graph.

    Multiple KGs can exist per (profile_id, user_uuid) pair; only one is active
    at a time.  When kg_id is omitted, the active KG is resolved lazily on first
    use — profile_id is used as a final fallback for backwards compatibility.

    KG metadata (name, database_name, description, is_active, subscription state)
    is always stored in SQLite via the kg_metadata table, regardless of which
    backend holds entity and relationship data.
    """

    def __init__(
        self,
        profile_id: str,
        user_uuid: str,
        kg_id: Optional[str] = None,
        db_path: Optional[str] = None,
        kg_store_config_id: Optional[str] = None,
        allow_subscribed_write: bool = False,
    ):
        self.profile_id = profile_id
        self.user_uuid = user_uuid
        self._kg_id = kg_id
        self._kg_store_config_id = kg_store_config_id
        # The marketplace SYNC path rebuilds a subscribed graph from the publisher's
        # export — the one legitimate write to a read-only subscription. Without
        # this flag clear_graph()/import_bulk() raised on every sync (found in the
        # Aug-2026 KG assessment: both manual sync and republish fan-out failed).
        self._allow_subscribed_write = bool(allow_subscribed_write)

        if db_path:
            self._db_path = db_path
        else:
            try:
                from trusted_data_agent.core.config import APP_CONFIG
                self._db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
            except Exception:
                self._db_path = "tda_auth.db"

        # Lazy-resolved backend — set on first call to _get_backend()
        self._backend = None

    # ── Backend resolution ────────────────────────────────────────────────

    def _get_backend(self):
        """Return the configured KGStoreBackend for this profile, initialising if needed."""
        if self._backend is not None:
            return self._backend
        try:
            from trusted_data_agent.kgstore.factory import get_backend_for_profile
            # If no explicit config_id was passed at construction, look up
            # kg_metadata.kg_store_config_id so the KG always routes to the
            # backend it was *created* on, not the profile's current default.
            effective_config_id = self._kg_store_config_id
            if not effective_config_id:
                try:
                    conn = self._get_conn()
                    try:
                        GraphStore._ensure_kg_metadata_table(conn)
                        row = conn.execute(
                            "SELECT kg_store_config_id FROM kg_metadata "
                            "WHERE profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                            (self.profile_id, self.user_uuid),
                        ).fetchone()
                        if row:
                            effective_config_id = row["kg_store_config_id"]
                    finally:
                        conn.close()
                except Exception:
                    pass  # Non-fatal: falls through to profile/default resolution
            self._backend = get_backend_for_profile(
                self.profile_id, self.user_uuid,
                config_id=effective_config_id,
            )
        except Exception as exc:
            logger.warning(f"KG backend resolution failed ({exc}); falling back to SQLite")
            from trusted_data_agent.kgstore.factory import get_default_sqlite_backend
            self._backend = get_default_sqlite_backend()
        return self._backend

    # ── kg_id resolution ──────────────────────────────────────────────────

    @property
    def kg_id(self) -> str:
        if self._kg_id is not None:
            return self._kg_id
        active = self._resolve_active_kg_id()
        self._kg_id = active if active else self.profile_id
        return self._kg_id

    def _resolve_active_kg_id(self) -> Optional[str]:
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            row = conn.execute(
                "SELECT kg_id FROM kg_metadata "
                "WHERE profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                (self.profile_id, self.user_uuid),
            ).fetchone()
            return row["kg_id"] if row else None
        finally:
            conn.close()

    # ── SQLite connection helper (metadata ops only) ──────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite_util.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _is_subscribed(self) -> bool:
        try:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT is_subscribed FROM kg_metadata WHERE kg_id = ?",
                    (self.kg_id,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return False  # Pre-migration install — treat as writable
        return bool(row is not None and row["is_subscribed"])

    def _write_mode(self) -> str:
        """``"all"`` — every write is this caller's right (an owned graph, or the sync flag);
        ``"authored_only"`` — a SUBSCRIBED graph under OBDA-MODEL: the base layer is read-only and
        only the subscriber's authored layer is writable (PROP-008 A1 / §3a). Raises when a
        subscribed graph is written with OBDA-MODEL · OBDA-ACT off — the pre-OBDA-MODEL/OBDA-ACT rule, byte for byte."""
        if getattr(self, "_allow_subscribed_write", False):
            return "all"
        if not self._is_subscribed():
            return "all"
        try:
            from trusted_data_agent.core import obda_planes as _planes
            if _planes.plane_available("model"):
                return "authored_only"
        except Exception:  # noqa: BLE001
            pass
        raise KnowledgeGraphReadOnlyError(
            f"Cannot modify subscribed knowledge graph (kg_id={self.kg_id}). "
            "Convert to a fork via the marketplace if you need to edit it."
        )

    def _assert_writable(self) -> None:
        """Raise KnowledgeGraphReadOnlyError unless a WHOLE-graph write is permitted
        (import_bulk / clear_graph / replace_model_layer). A subscribed graph's authored
        layer is writable node by node (see ``_assert_layer_writable``), never wholesale."""
        if self._write_mode() != "all":
            raise KnowledgeGraphReadOnlyError(
                f"Cannot rewrite subscribed knowledge graph (kg_id={self.kg_id}) as a whole: "
                "its base layer is read-only here; only its authored layer may be edited."
            )

    @staticmethod
    def _refuse_base(what: str) -> None:
        raise KnowledgeGraphReadOnlyError(
            f"Cannot {what} on the base layer of a subscribed knowledge graph — the base is the "
            "publisher's; add it on your authored layer (properties.layer = 'authored') or "
            "propose the change upstream through the marketplace."
        )

    def _assert_layer_writable(self, layer: Optional[str], what: str,
                               properties: Optional[Dict[str, Any]] = None) -> None:
        """Node-level rule: under ``authored_only`` a write must target the authored layer —
        and (PROP-008 A5 / §3a) the CALLER'S OWN extension: on a subscribed graph the
        publisher's published extension arrived with the base and is the publisher's tier,
        signed by them; editing it here would break a seal this user does not own. A node
        stamped with another extender's ``extension_id`` is refused; an unstamped node
        (written before the vocabulary) stays writable."""
        if self._write_mode() != "authored_only":
            return
        if layer != _AUTHORED_LAYER:
            self._refuse_base(what)
        try:
            from trusted_data_agent.kgstore import types as _T
            ext = (properties or {}).get(_T.EXTENSION_ID_PROP)
            mine = _T.extension_id_for(self.user_uuid)
            if ext and str(ext) != mine:
                raise KnowledgeGraphReadOnlyError(
                    f"Cannot {what} on another extender's layer (extension '{ext}') of a subscribed "
                    f"knowledge graph — that layer is theirs and signed by them; add to your own "
                    f"extension ('{mine}') or propose the change upstream.")
        except KnowledgeGraphReadOnlyError:
            raise
        except Exception:  # noqa: BLE001 — an unreadable vocabulary never widens a write
            pass

    def _entity_layer(self, entity_id: int) -> Optional[str]:
        ent = self.get_entity(entity_id)
        return (ent.get("properties") or {}).get("layer") if ent else None

    def _entity_props(self, entity_id: int) -> Dict[str, Any]:
        ent = self.get_entity(entity_id)
        return dict((ent.get("properties") or {})) if ent else {}

    # ── Entity CRUD ───────────────────────────────────────────────────────

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    async def add_entity(
        self,
        name: str,
        entity_type: str,
        properties: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        source_detail: Optional[str] = None,
    ) -> int:
        if entity_type not in ENTITY_TYPES:
            raise ValueError(
                f"Invalid entity_type '{entity_type}'. Must be one of: {ENTITY_TYPES}"
            )
        layer = (properties or {}).get("layer")
        if layer is not None and layer not in _LAYERS:
            raise ValueError(f"Invalid layer '{layer}'. Must be one of: {sorted(_LAYERS)}")
        # An add with NO layer is stamped logical/physical by the backend — i.e. onto the
        # base — so under authored_only it is refused rather than silently landing there.
        self._assert_layer_writable(layer, "add an entity", properties)
        # The backend's add is an UPSERT by (name, type): adding an existing name on
        # another layer would RE-LAYER that node — the hidden mutation update_entity
        # already refuses (A1), reached through the other door. An extension only adds
        # (PROP-008 §3a), so the add is refused and the collision named.
        if layer is not None:
            try:
                existing = self._get_backend().get_entity_by_name(self.kg_id, name, entity_type)
            except Exception:  # noqa: BLE001 — a failed probe never blocks a legitimate add
                existing = None
            cur = ((existing or {}).get("properties") or {}).get("layer")
            if existing and cur and cur != layer:
                raise ValueError(
                    f"'{name}' ({entity_type}) already exists on the {cur} layer — a node's layer is "
                    f"immutable; an extension adds a new name beside the base, it never overlays one")
        return self._get_backend().add_entity(
            kg_id=self.kg_id,
            profile_id=self.profile_id,
            user_uuid=self.user_uuid,
            name=name,
            entity_type=entity_type,
            properties=properties,
            source=source,
            source_detail=source_detail,
        )

    def get_entity(self, entity_id: int) -> Optional[Dict[str, Any]]:
        return self._get_backend().get_entity(self.kg_id, entity_id)

    def get_entity_by_name(
        self,
        name: str,
        entity_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._get_backend().get_entity_by_name(self.kg_id, name, entity_type)

    async def search_entities(
        self,
        query_text: str,
        limit: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._get_backend().search_entities(self.kg_id, query_text, limit, entity_type)

    async def list_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        return self._get_backend().list_entities(self.kg_id, entity_type, limit)

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    async def update_entity(self, entity_id: int, properties: Dict[str, Any] = None, name: str = None, entity_type: str = None) -> bool:
        current = self._entity_layer(entity_id)
        new_layer = (properties or {}).get("layer")
        if new_layer is not None:
            if new_layer not in _LAYERS:
                raise ValueError(f"Invalid layer '{new_layer}'. Must be one of: {sorted(_LAYERS)}")
            # A node's layer is immutable: re-layering a base node into the authored layer
            # (or back) is the hidden mutation — it would change what a resync keeps.
            if current is not None and new_layer != current:
                raise ValueError(f"A node's layer is immutable (is '{current}', update says '{new_layer}')")
        self._assert_layer_writable(current, "update an entity", self._entity_props(entity_id))
        return self._get_backend().update_entity(self.kg_id, entity_id, properties, name=name, entity_type=entity_type)

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    async def delete_entity(self, entity_id: int) -> bool:
        self._assert_layer_writable(self._entity_layer(entity_id), "delete an entity", self._entity_props(entity_id))
        return self._get_backend().delete_entity(self.kg_id, entity_id)

    # ── Relationship CRUD ─────────────────────────────────────────────────

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    async def add_relationship(
        self,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        cardinality: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
    ) -> int:
        if relationship_type not in RELATIONSHIP_TYPES:
            raise ValueError(
                f"Invalid relationship_type '{relationship_type}'. "
                f"Must be one of: {RELATIONSHIP_TYPES}"
            )
        # Additive-only (§3a): an edge is the authored layer's iff it is SOURCED at an
        # authored node; its target may be a base node.
        self._assert_layer_writable(self._entity_layer(source_entity_id), "add a relationship",
                                    self._entity_props(source_entity_id))
        return self._get_backend().add_relationship(
            kg_id=self.kg_id,
            profile_id=self.profile_id,
            user_uuid=self.user_uuid,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relationship_type=relationship_type,
            cardinality=cardinality,
            metadata=metadata,
            source=source,
        )

    async def get_relationships(
        self,
        entity_id: int,
        direction: str = "both",
    ) -> List[Dict[str, Any]]:
        return self._get_backend().get_relationships(self.kg_id, entity_id, direction)

    async def list_relationships(
        self,
        entity_id: Optional[int] = None,
        relationship_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        return self._get_backend().list_relationships(
            self.kg_id, entity_id, relationship_type, limit
        )

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    async def delete_relationship(self, relationship_id: int) -> bool:
        if self._write_mode() == "authored_only":
            rel = self._get_backend().get_relationship(self.kg_id, relationship_id)
            src_layer = self._entity_layer(rel["source_entity_id"]) if rel else None
            self._assert_layer_writable(src_layer, "delete a relationship",
                                        self._entity_props(rel["source_entity_id"]) if rel else None)
        return self._get_backend().delete_relationship(self.kg_id, relationship_id)

    # ── Bulk operations ───────────────────────────────────────────────────

    async def export_graph(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Complete, un-truncatable export of the whole graph (entities + relationships).

        The fetch limits are derived from the live counts (with headroom) so no
        graph size can silently truncate the export.  This is the ONLY correct way
        to snapshot a KG for marketplace publish / agent-pack export / RDF export —
        never call ``list_entities()`` with a magic limit for a full export: those
        queries are ``ORDER BY entity_type`` alphabetically, so once a large model
        (e.g. a 15k-entity industry model) exceeds the cap, the alphabetically-last
        types (``metric``, ``schema``, ``table``, ``taxonomy``) are the ones dropped.
        """
        stats = await self.get_stats()
        n_ent = int(stats.get("total_entities", 0) or 0)
        n_rel = int(stats.get("total_relationships", 0) or 0)
        # +1000 headroom absorbs any rows added between the count and the fetch.
        entities = await self.list_entities(limit=n_ent + 1000)
        relationships = await self.list_relationships(limit=n_rel + 1000)
        return entities, relationships

    @_signals.announces("knowledge_graph", "imported", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    def import_bulk(
        self,
        entities: List[Dict],
        relationships: List[Dict],
    ) -> Dict[str, int]:
        self._assert_writable()
        # Single chokepoint for the entity-type rename: normalises any old-model
        # (table/column/physical_*) payload — e.g. a marketplace or agent-pack
        # export created before the rename — to the new ontology. Layer-aware and
        # idempotent, so new-model payloads (and freshly-walked KGs) pass through
        # unchanged.
        try:
            from trusted_data_agent.kgstore.migrations import migrate_kg_payload_types
            entities, relationships = migrate_kg_payload_types(entities, relationships)
        except Exception:  # noqa: BLE001
            pass  # Never let normalisation block an import
        result = self._get_backend().import_bulk(
            kg_id=self.kg_id,
            profile_id=self.profile_id,
            user_uuid=self.user_uuid,
            entities=entities,
            relationships=relationships,
        )
        # The graph just changed, so its semantic index is stale — build the new one
        # HERE, at the moment the content is written, rather than making the first
        # question of the day pay for it. This is the single chokepoint every write
        # path reaches (constructors, marketplace install/fork/sync, agent-pack import,
        # PDM deploy), which is why the type migration above lives here too.
        self.warm_semantic_index()
        return result

    def all_entity_dicts(self) -> List[Dict]:
        """Every entity of this graph, for consumers that must see the whole model
        (the semantic index). Explicit limit: the backend default is 200, which would
        silently index an alphabetic slice."""
        return self._get_backend().list_entities(self.kg_id, limit=999999) or []

    def warm_semantic_index(self) -> None:
        """Build this graph's semantic index for its CURRENT content, in the background.

        Called after a bulk write so the index tracks the graph rather than trailing it.
        Deliberately NOT called from the single-entity CRUD methods: an agent adding
        entities one at a time would re-embed the whole graph per call, and the lazy
        path already handles that case correctly — the signature moves, the next query
        finds a cold index and rebuilds once.

        Fire-and-forget and fail-soft in both directions: a graph write must never fail
        because an index could not be built, and a disabled channel makes this a no-op.
        """
        try:
            from trusted_data_agent.kgstore import semantic_index as _sem
            if not _sem.is_enabled():
                return
            # retain=False: this is a WRITE, not a question. The matrix is persisted
            # and released, so a deployment that imports several graphs does not carry
            # a matrix for every one of them — resident memory stays a statement about
            # which graphs are actually being queried.
            _sem.warm(self.kg_id, self.all_entity_dicts,
                      _sem.graph_signature(self.kg_id, self._db_path), retain=False)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"KG semantic index warm skipped (kg={self.kg_id}): {exc}")

    @_signals.announces("knowledge_graph", "cleared", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="graph")
    def clear_graph(self) -> Dict[str, int]:
        self._assert_writable()
        return self._get_backend().clear_graph(self.kg_id)

    def replace_model_layer(
        self,
        entities: List[Dict],
        relationships: List[Dict],
        keep_layers=None,
    ) -> Dict[str, Any]:
        """Replace the WALKED model (logical + physical) with a fresh walk and KEEP the
        layers a walk never produces — `authored` (PROP-008 OBDA-MODEL) and `observed`
        (KG_OBSERVATION_LAYER). THE operation every resync path uses instead of
        ``clear_graph()`` + ``import_bulk()``: apply-update, subscription sync, agent-pack
        in-place refresh, declarative redeploy, version restore. ``clear_graph`` stays a
        delete (uninstall, owner-initiated clear) and removes everything.

        What is kept is ONE rule, ``kgstore.types.is_kept_on_replace``: the authored and
        observed layers, an extension's realisation (the schema, tables and columns its
        landing wrote) and the stacked contracts (action types, data products). A walked
        node that shares a realisation's name (a walk that reads the warehouse sees the
        landed table) takes the realisation's marks, so it stays the extension's.

        A kept edge whose other end the new walk dropped is reported in
        ``kept_edges_dropped`` — a finding, never a silent loss — AND stamped on the
        authored node it concerns (``properties.dangling_edges``, PROP-008 A5): the edge's
        own authored source, or the node a realisation realises. A realisation whose
        concept is gone is named in ``realisations_orphaned``.

        **Extension-aware (A5 / §3a):** a payload that CARRIES an extension (authored
        nodes stamped with an ``extension_id``) REPLACES that extension — the publisher's
        published layer arriving with a resync updates the consumer's copy of it — while
        every other extension (the consumer's own) is kept untouched. A walk carries no
        authored node, so nothing authored is replaced by a plain resync.
        """
        self._assert_writable()
        keep = _KEPT_LAYERS_ON_REPLACE if keep_layers is None else frozenset(keep_layers)
        try:
            from trusted_data_agent.kgstore.migrations import migrate_kg_payload_types
            entities, relationships = migrate_kg_payload_types(entities, relationships)
        except Exception:  # noqa: BLE001
            pass
        replaced_extensions = self._drop_carried_extensions(entities)
        entities, marks_carried = self._carry_realisation_marks(entities)
        result = self._get_backend().replace_model_layer(
            kg_id=self.kg_id, profile_id=self.profile_id, user_uuid=self.user_uuid,
            entities=entities, relationships=relationships, keep_layers=keep,
        )
        result = dict(result or {})
        result["extensions_replaced"] = sorted(replaced_extensions)
        result["realisation_marks_carried"] = marks_carried
        result["dangling_stamped"] = self._stamp_dangling(result.get("kept_edges_dropped_detail") or [])
        result["realisations_orphaned"] = self._orphaned_realisations()
        self.warm_semantic_index()
        return result

    def merge_authored_layer(self, entities: List[Dict], relationships: List[Dict]) -> Dict[str, Any]:
        """Install an EXTENSION onto this graph (PROP-008 A5): the carried extensions
        replace their namesakes, every other layer — base and the other extensions —
        is untouched. The payload may hold authored nodes only; a base node in it is
        refused (an extension only adds). Targets of its edges resolve by name against
        the base. The one operation the marketplace's extension install uses."""
        from trusted_data_agent.kgstore import types as _T
        bad = [e.get("name") for e in (entities or []) if not _T.is_authored(e.get("properties"))]
        if bad:
            raise ValueError(f"an extension payload may carry authored nodes only; refused: {bad[:5]}")
        if self._write_mode() == "authored_only":
            mine = _T.extension_id_for(self.user_uuid)
            foreign = sorted(x for x in _T.payload_extension_ids(entities) if x != mine)
            # on a subscribed copy another extender's layer may be INSTALLED (it is theirs,
            # signed by them) but never edited node by node — the install is whole-layer
            logger.info(f"merge_authored_layer: installing extensions {foreign or [mine]} on {self.kg_id}")
        try:
            from trusted_data_agent.kgstore.migrations import migrate_kg_payload_types
            entities, relationships = migrate_kg_payload_types(entities, relationships)
        except Exception:  # noqa: BLE001
            pass
        replaced = self._drop_carried_extensions(entities)
        res = dict(self._get_backend().import_bulk(
            kg_id=self.kg_id, profile_id=self.profile_id, user_uuid=self.user_uuid,
            entities=entities, relationships=relationships) or {})
        res["extensions_replaced"] = sorted(replaced)
        self.warm_semantic_index()
        return res

    def _drop_carried_extensions(self, entities: List[Dict]) -> set:
        """Delete the nodes of every extension the payload carries, so the backend's
        keep-by-layer keeps only the extensions the payload does NOT carry. Edges cascade
        with their nodes; the payload's own edges are re-imported by name."""
        try:
            from trusted_data_agent.kgstore import types as _T
            carried = set(_T.payload_extension_ids(entities))
        except Exception:  # noqa: BLE001
            return set()
        if not carried:
            return set()
        backend = self._get_backend()
        replaced = set()   # only layers that were really THERE — a first install replaces nothing
        for e in backend.list_entities(self.kg_id, limit=999999) or []:
            pr = e.get("properties") or {}
            if pr.get("layer") == _AUTHORED_LAYER and _T.extension_id_of(pr) in carried:
                try:
                    backend.delete_entity(self.kg_id, e["id"])
                    replaced.add(_T.extension_id_of(pr))
                except Exception:  # noqa: BLE001
                    pass
        return replaced

    def _carry_realisation_marks(self, entities: List[Dict]) -> tuple:
        """A payload node that shares (name, type) with a realisation on this graph takes the
        realisation's marks (``REALISATION_MARKS``): the walk's content wins, the node stays
        the extension's. Without it the upsert replaced the marks and the landed table passed
        as base — visible while its extension was unpublished, and lost on the next update.
        Returns ``(entities, carried_count)``; the caller's payload is never mutated."""
        from trusted_data_agent.kgstore import types as _T
        try:
            have = {}
            for e in self._get_backend().list_entities(self.kg_id, limit=999999) or []:
                pr = e.get("properties") or {}
                if _T.is_realisation(pr):
                    have[(str(e.get("name") or "").lower(), str(e.get("entity_type") or ""))] = \
                        {k: pr[k] for k in _T.REALISATION_MARKS if k in pr}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"replace_model_layer: realisations not read, marks not carried: {exc}")
            return entities, 0
        if not have:
            return entities, 0
        out, n = [], 0
        for e in entities or []:
            marks = have.get((str(e.get("name") or "").lower(), str(e.get("entity_type") or "")))
            if marks and not _T.is_realisation(e.get("properties")):
                e = dict(e, properties={**(e.get("properties") or {}), **marks})
                n += 1
            out.append(e)
        return out, n

    def _orphaned_realisations(self) -> List[str]:
        """Realisations whose ``realises`` names no node any more — a landed table whose
        concept is gone. Kept (the warehouse object is real), named so it is never silent."""
        from trusted_data_agent.kgstore import types as _T
        try:
            ents = self._get_backend().list_entities(self.kg_id, limit=999999) or []
        except Exception:  # noqa: BLE001
            return []
        names = {str(e.get("name")) for e in ents}
        return sorted(str(e.get("name")) for e in ents
                      if _T.is_realisation(e.get("properties"))
                      and (e.get("properties") or {}).get("realises")
                      and str((e.get("properties") or {}).get("realises")) not in names)

    def _stamp_dangling(self, dropped_detail: List[Dict[str, Any]]) -> int:
        """Stamp each dropped kept edge on the AUTHORED node it concerns (dedup) — its own
        authored source, or the node a realisation endpoint realises — so the resync's
        finding travels with the concept. Fail-soft: a stamp that cannot be
        written is logged, never a failed resync."""
        from trusted_data_agent.kgstore import types as _T
        n = 0
        backend = self._get_backend()

        def _concerned(d):
            # the edge's own authored source; else the authored node a realisation endpoint
            # realises (a landed column whose base table the walk dropped concerns the
            # attribute it realises)
            src = backend.get_entity_by_name(self.kg_id, d.get("source_name"), d.get("source_type"))
            if src and (src.get("properties") or {}).get("layer") == _AUTHORED_LAYER:
                return src
            for nm, ty in ((d.get("source_name"), d.get("source_type")), (d.get("target_name"), d.get("target_type"))):
                node = backend.get_entity_by_name(self.kg_id, nm, ty) if nm else None
                pr = (node or {}).get("properties") or {}
                if _T.is_realisation(pr) and pr.get("realises"):
                    for et in ("entity", "attribute"):
                        owner = backend.get_entity_by_name(self.kg_id, pr["realises"], et)
                        if owner and (owner.get("properties") or {}).get("layer") == _AUTHORED_LAYER:
                            return owner
            return None

        for d in dropped_detail or []:
            try:
                ent = _concerned(d)
                if not ent:
                    continue
                props = dict(ent.get("properties") or {})
                have = list(props.get("dangling_edges") or [])
                label = d.get("label") or f"{d.get('source_name')} --[{d.get('relationship_type')}]--> {d.get('target_name')}"
                if label in have:
                    continue
                props["dangling_edges"] = have + [label]
                backend.update_entity(self.kg_id, ent["id"], props)
                n += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"replace_model_layer: could not stamp a dropped edge: {exc}")
        return n

    # ── Graph algorithms ──────────────────────────────────────────────────

    def extract_subgraph(
        self,
        entity_ids: List[int],
        depth: int = 2,
        max_nodes: int = 50,
    ) -> Dict[str, List]:
        return self._get_backend().extract_subgraph(
            self.kg_id, entity_ids, depth, max_nodes
        )

    def extract_subgraph_adaptive(
        self,
        seed_entity_ids: List[int],
        query_entity_ids: Optional[List[int]] = None,
        max_nodes: int = 500,
        **scope,
    ) -> Dict[str, List]:
        be = self._get_backend()
        if scope:
            import inspect as _inspect
            try:
                params = _inspect.signature(be.extract_subgraph_adaptive).parameters
                scope = {k: v for k, v in scope.items() if k in params}
            except (ValueError, TypeError):
                scope = {}
        if scope:
            return be.extract_subgraph_adaptive(
                self.kg_id, seed_entity_ids, query_entity_ids, max_nodes, **scope)
        return be.extract_subgraph_adaptive(
            self.kg_id, seed_entity_ids, query_entity_ids, max_nodes
        )

    def get_full_graph(self, max_nodes: int = 100) -> Dict[str, List]:
        return self._get_backend().get_full_graph(self.kg_id, max_nodes)

    def get_typed_subgraph(self, types=None, max_nodes: int = 500, focus=None,
                           project_dependencies: bool = False,
                           divisions=None, domains=None) -> Dict[str, List]:
        """Seed → connectivity-expand → fill subgraph (detail lens + focus lens).

        project_dependencies: lift hidden-level relationships to derived edges between
        visible nodes (the "show dependencies" overlay) — default OFF (heavy; requested
        on zoom-in / expand).
        divisions/domains: optional facet filters (by the division/domain properties);
        the result also carries a ``facets`` catalog of the values present in the KG."""
        return self._get_backend().get_typed_subgraph(
            self.kg_id, types, max_nodes, focus, project_dependencies,
            divisions=divisions, domains=domains)

    def get_schema_overview(self) -> Dict[str, List]:
        """Type-level meta-graph for the overview lens (always tiny + complete)."""
        return self._get_backend().get_schema_overview(self.kg_id)

    def all_table_columns(self) -> Dict[str, List[str]]:
        """{table leaf: [column leaves]} for the whole graph (see the backend); {} when the
        backend cannot enumerate."""
        be = self._get_backend()
        fn = getattr(be, "all_table_columns", None)
        return fn(self.kg_id) if callable(fn) else {}

    def get_node_neighborhood(self, entity_id: int, max_neighbors: int = 500) -> Dict[str, Any]:
        """COMPLETE 1-hop neighbourhood of a node (all neighbours + connecting edges),
        uncapped by the render budget — the consistency guarantee for node focus."""
        return self._get_backend().get_node_neighborhood(self.kg_id, entity_id, max_neighbors)

    def find_shortest_path(
        self,
        source_id: int,
        target_id: int,
    ) -> Optional[List[int]]:
        return self._get_backend().find_shortest_path(self.kg_id, source_id, target_id)

    def get_connected_entities(self, entity_id: int) -> Dict[str, List[int]]:
        return self._get_backend().get_connected_entities(self.kg_id, entity_id)

    def get_entity_importance(self) -> Dict[int, float]:
        return self._get_backend().get_entity_importance(self.kg_id)

    def detect_cycles(self) -> List[List[int]]:
        return self._get_backend().detect_cycles(self.kg_id)

    async def get_stats(self) -> Dict[str, Any]:
        return self._get_backend().get_stats(self.kg_id)

    #: An UNPUBLISHED authored node (PROP-008 §2b) — the rule the handler's
    #: ``_unpublished_authored`` states, in SQL. A draft belongs to its author's Drafts
    #: section; the model's own figures and views leave it out until it is published.
    DRAFT_SQL = ("json_extract({a}properties_json, '$.layer') = 'authored' AND "
                 "COALESCE(json_extract({a}properties_json, '$.status'), 'draft') != 'published'")

    async def unpublished_names(self) -> set:
        """The names of every node of an unpublished extension — drafts AND what landing them
        wrote (``kgstore.types.unpublished_extension_names``, the ONE rule). SQLite reads only
        the candidate rows; another backend scans its nodes. Empty when nothing is unpublished."""
        from trusted_data_agent.kgstore.types import unpublished_extension_names
        try:
            be = self._get_backend()
            from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend
            if isinstance(be, SQLiteKGBackend):
                conn = be._get_conn()
                try:
                    rows = conn.execute(
                        "SELECT name, entity_type, properties_json FROM kg_entities WHERE kg_id = ? AND ("
                        "json_extract(properties_json, '$.layer') = 'authored' OR "
                        "json_extract(properties_json, '$.authored_deploy'))", (self.kg_id,)).fetchall()
                finally:
                    conn.close()
                ents = []
                for name, etype, pj in rows:
                    try:
                        props = json.loads(pj or "{}")
                    except (TypeError, ValueError):
                        props = {}
                    ents.append({"name": name, "entity_type": etype, "properties": props})
                return unpublished_extension_names(ents)
            return unpublished_extension_names(await self.list_entities(limit=999999))
        except Exception as exc:  # noqa: BLE001 — an unknown set hides only the drafts themselves
            logger.debug(f"unpublished_names skipped: {exc}")
            return set()

    @staticmethod
    def unpublished_rows(conn, kg_id: str) -> List[tuple]:
        """``[(id, entity_type)]`` of every node of an unpublished extension in ``kg_id`` — the
        ONE rule (``unpublished_extension_names``) over the candidate rows only. SQLite."""
        from trusted_data_agent.kgstore.types import unpublished_extension_names
        rows = conn.execute(
            "SELECT id, name, entity_type, properties_json FROM kg_entities WHERE kg_id = ? AND ("
            "json_extract(properties_json, '$.layer') = 'authored' OR "
            "json_extract(properties_json, '$.authored_deploy'))", (kg_id,)).fetchall()
        ents = []
        for rid, name, etype, pj in rows:
            try:
                props = json.loads(pj or "{}")
            except (TypeError, ValueError):
                props = {}
            ents.append({"id": rid, "name": name, "entity_type": etype, "properties": props})
        hidden = unpublished_extension_names(ents)
        return [(e["id"], e["entity_type"]) for e in ents if e["name"] in hidden]

    @staticmethod
    def count_touching(conn, kg_id: str, ids: List[int]) -> int:
        """Relationships in ``kg_id`` with an end among ``ids`` (bounded batches)."""
        if not ids:
            return 0
        seen = set()
        for k in range(0, len(ids), 400):
            chunk = ids[k:k + 400]
            q = ",".join("?" * len(chunk))
            for (rid,) in conn.execute(f"SELECT id FROM kg_relationships WHERE kg_id = ? AND "
                                       f"(source_entity_id IN ({q}) OR target_entity_id IN ({q}))",
                                       (kg_id, *chunk, *chunk)).fetchall():
                seen.add(rid)
        return len(seen)

    async def draft_counts(self) -> Dict[str, Any]:
        """``{entities, relationships, entity_types}`` of every unpublished extension — its
        drafts AND what landing them wrote — i.e. what the model's own figures subtract."""
        out: Dict[str, Any] = {"entities": 0, "relationships": 0, "entity_types": {}}
        try:
            be = self._get_backend()
            from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend
            if isinstance(be, SQLiteKGBackend):
                conn = be._get_conn()
                try:
                    hid = self.unpublished_rows(conn, self.kg_id)
                    for _, t in hid:
                        out["entity_types"][t] = out["entity_types"].get(t, 0) + 1
                    out["entities"] = len(hid)
                    out["relationships"] = self.count_touching(conn, self.kg_id, [i for i, _ in hid])
                finally:
                    conn.close()
                return out
            names = await self.unpublished_names()
            if not names:
                return out
            ents = await self.list_entities(limit=999999)
            ids = set()
            for e in ents or []:
                if e.get("name") in names:
                    ids.add(e.get("id"))
                    t = e.get("entity_type") or "unknown"
                    out["entity_types"][t] = out["entity_types"].get(t, 0) + 1
            out["entities"] = len(ids)
            rels = await self.list_relationships(limit=999999)
            out["relationships"] = sum(1 for r in rels or []
                                       if {r.get("source_entity_id"), r.get("target_entity_id")} & ids)
        except Exception as exc:  # noqa: BLE001 — unknown drafts subtract nothing
            logger.debug(f"draft_counts skipped: {exc}")
        return out

    # ── KG Metadata (always SQLite) ───────────────────────────────────────

    @staticmethod
    def _ensure_kg_metadata_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kg_metadata (
                kg_id         TEXT PRIMARY KEY,
                profile_id    TEXT NOT NULL,
                user_uuid     TEXT NOT NULL,
                name          TEXT NOT NULL,
                database_name TEXT,
                description   TEXT,
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE kg_metadata ADD COLUMN description TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE kg_metadata ADD COLUMN kg_store_config_id TEXT")
        except Exception:
            pass
        try:
            # Re-syncable source descriptor stamped by declarative constructors.
            # Stored on metadata (NOT a graph entity) so clear_graph()/update can't wipe it.
            conn.execute("ALTER TABLE kg_metadata ADD COLUMN source_descriptor_json TEXT")
        except Exception:
            pass
        try:
            # Source-validation stamp: WHEN the graph was last validated against its
            # source data and what that covered. On metadata, never on a node — a
            # timestamp on a node would move the signed content hash of an unchanged graph.
            conn.execute("ALTER TABLE kg_metadata ADD COLUMN source_validation_json TEXT")
        except Exception:
            pass
        # Ontology-as-governed-product lifecycle (Tier 4.3) — additive, self-applying.
        for ddl in (
            "ALTER TABLE kg_metadata ADD COLUMN lifecycle_status TEXT DEFAULT 'draft'",
            "ALTER TABLE kg_metadata ADD COLUMN semantic_version TEXT",
            "ALTER TABLE kg_metadata ADD COLUMN published_version_id TEXT",
            "ALTER TABLE kg_metadata ADD COLUMN approval_status TEXT DEFAULT 'none'",
            "ALTER TABLE kg_metadata ADD COLUMN deprecated_at TEXT",
            "ALTER TABLE kg_metadata ADD COLUMN superseded_by TEXT",
            "ALTER TABLE kg_metadata ADD COLUMN conformance_status TEXT",
            "ALTER TABLE kg_metadata ADD COLUMN conformance_checked_at TEXT",
            # Model B (Tier 4.3 §1a): a released view is a shadow graph `<kg_id>::released`
            # whose row names the graph it is a release OF and the pinned version.
            "ALTER TABLE kg_metadata ADD COLUMN released_of TEXT",
            "ALTER TABLE kg_metadata ADD COLUMN released_version_id TEXT",
            # PROP-008 A5: the released view = published base + PUBLISHED extensions; this
            # records which extension layer the shadow holds, so a publish rebuilds it.
            "ALTER TABLE kg_metadata ADD COLUMN released_extensions_hash TEXT",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kg_lifecycle_audit (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                kg_id         TEXT NOT NULL,
                action        TEXT NOT NULL,
                actor_tier    TEXT,
                is_owner      INTEGER,
                from_status   TEXT,
                to_status     TEXT,
                from_approval TEXT,
                to_approval   TEXT,
                conformance   TEXT,
                allowed       INTEGER,
                reason        TEXT,
                badge         TEXT,
                created_at    TEXT NOT NULL
            )
            """
        )
        # who acted and what the act covered (Oct 2026): the tier alone could not tell two
        # publishes apart. Additive columns — the append-only triggers guard UPDATE/DELETE only.
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(kg_lifecycle_audit)").fetchall()}
        for _c in ("actor", "subject"):
            if _c not in _cols:
                conn.execute(f"ALTER TABLE kg_lifecycle_audit ADD COLUMN {_c} TEXT")
        conn.commit()
        try:
            from trusted_data_agent.core.audit_integrity import install_triggers
            install_triggers(conn, ["kg_lifecycle_audit"])
        except Exception as e:
            logger.error(f"kg_lifecycle_audit triggers not installed: {e}")

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="metadata")
    def set_kg_metadata(
        self,
        name: str,
        database_name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: bool = True,
        kg_store_config_id: Optional[str] = None,
    ) -> str:
        import uuid as _uuid
        if self._kg_id is None:
            self._kg_id = str(_uuid.uuid4())
        kg_id = self._kg_id
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            if is_active:
                conn.execute(
                    "UPDATE kg_metadata SET is_active=0, updated_at=? "
                    "WHERE profile_id=? AND user_uuid=?",
                    (now, self.profile_id, self.user_uuid),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO kg_metadata
                    (kg_id, profile_id, user_uuid, name, database_name, description,
                     kg_store_config_id, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT created_at FROM kg_metadata WHERE kg_id=?), ?
                ), ?)
                """,
                (kg_id, self.profile_id, self.user_uuid, name, database_name, description,
                 kg_store_config_id, 1 if is_active else 0, kg_id, now, now),
            )
            conn.commit()
            return kg_id
        finally:
            conn.close()

    def set_source_descriptor(self, descriptor: Optional[Dict[str, Any]]) -> bool:
        """Persist the re-syncable source descriptor onto this KG's metadata row.

        Returns True if a row was updated.  Must be called AFTER set_kg_metadata
        (the metadata row must exist).  Stored as JSON on kg_metadata so that
        clear_graph()/update never destroys it (unlike a graph entity would).
        """
        import json as _json
        now = datetime.now(timezone.utc).isoformat()
        payload = _json.dumps(descriptor) if descriptor is not None else None
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            cur = conn.execute(
                "UPDATE kg_metadata SET source_descriptor_json=?, updated_at=? WHERE kg_id=?",
                (payload, now, self.kg_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    @staticmethod
    def source_label(desc: Optional[Dict[str, Any]]) -> Optional[str]:
        """The one-line name of a graph's re-syncable source, by descriptor shape: an
        industry model reads `industry/size@ref`; an ANDP product reads its product name(s)
        (it used to read "None/None@main" — the label assumed one constructor's shape)."""
        if not isinstance(desc, dict) or not desc:
            return None
        strat = str(desc.get("strategy") or desc.get("discovery_mode") or "")
        if strat == "andp_semantic" or desc.get("product") or desc.get("products"):
            prods = desc.get("products") if isinstance(desc.get("products"), list) else [desc]
            names = [str((p or {}).get("product") or "").strip() for p in prods if isinstance(p, dict)]
            names = [n for n in names if n]
            return ("ANDP: " + ", ".join(names)) if names else "ANDP product"
        return f"{desc.get('industry')}/{desc.get('model_size')}@{desc.get('repo_ref', 'main')}"

    def set_source_validation(self, summary: Optional[Dict[str, Any]],
                              validated_at: Optional[str] = None) -> bool:
        """Persist (or clear, with None) the source-validation stamp — ``validated_at`` plus
        the counts the card states. Must be called AFTER set_kg_metadata. ``validated_at``
        defaults to now (a validation that just ran); a pack copy passes the PUBLISHER's
        timestamp — the graph was validated there, at that time, never here."""
        import json as _json
        now = validated_at or datetime.now(timezone.utc).isoformat()
        payload = None
        if summary is not None:
            payload = _json.dumps({"validated_at": now, **{k: v for k, v in summary.items() if k != "validated_at"}})
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            cur = conn.execute(
                "UPDATE kg_metadata SET source_validation_json=? WHERE kg_id=?",
                (payload, self.kg_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_source_validation(self) -> Optional[Dict[str, Any]]:
        """The source-validation stamp for this KG ({validated_at, counts…}) or None."""
        import json as _json
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            row = conn.execute(
                "SELECT source_validation_json FROM kg_metadata WHERE kg_id=?", (self.kg_id,),
            ).fetchone()
            if not row or not row[0]:
                return None
            try:
                return _json.loads(row[0])
            except Exception:
                return None
        finally:
            conn.close()

    def get_source_descriptor(self) -> Optional[Dict[str, Any]]:
        """Return the re-syncable source descriptor for this KG (or None)."""
        import json as _json
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            row = conn.execute(
                "SELECT source_descriptor_json FROM kg_metadata WHERE kg_id=?",
                (self.kg_id,),
            ).fetchone()
            if not row or not row[0]:
                return None
            try:
                return _json.loads(row[0])
            except Exception:
                return None
        finally:
            conn.close()

    def get_kg_metadata(self) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            kg_id = self._kg_id
            if kg_id:
                row = conn.execute(
                    "SELECT kg_id, name, database_name, is_active, created_at, updated_at, kg_store_config_id "
                    "FROM kg_metadata WHERE kg_id=?",
                    (kg_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT kg_id, name, database_name, is_active, created_at, updated_at, kg_store_config_id "
                    "FROM kg_metadata "
                    "WHERE profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                    (self.profile_id, self.user_uuid),
                ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── Governed-product lifecycle (Tier 4.3) ─────────────────────────────
    _LIFECYCLE_COLS = (
        "lifecycle_status", "semantic_version", "published_version_id",
        "approval_status", "deprecated_at", "superseded_by",
        "conformance_status", "conformance_checked_at",
    )

    def get_lifecycle_state(self) -> Dict[str, Any]:
        """Return this KG's lifecycle fields (with safe defaults for legacy rows)."""
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            row = conn.execute(
                f"SELECT {', '.join(self._LIFECYCLE_COLS)} FROM kg_metadata WHERE kg_id=?",
                (self.kg_id,),
            ).fetchone()
            state = dict(row) if row else {}
            state.setdefault("lifecycle_status", None)
            state["lifecycle_status"] = state.get("lifecycle_status") or "draft"
            state["approval_status"] = state.get("approval_status") or "none"
            return state
        finally:
            conn.close()

    @_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",), profile="self.profile_id", aspect="lifecycle")
    def set_lifecycle_state(self, **fields: Any) -> bool:
        """Update the supplied lifecycle columns (only known columns are written)."""
        cols = [k for k in fields if k in self._LIFECYCLE_COLS]
        if not cols:
            return False
        now = datetime.now(timezone.utc).isoformat()
        assignments = ", ".join(f"{c}=?" for c in cols) + ", updated_at=?"
        values = [fields[c] for c in cols] + [now, self.kg_id]
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            cur = conn.execute(
                f"UPDATE kg_metadata SET {assignments} WHERE kg_id=?", values
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def append_lifecycle_audit(self, record: Dict[str, Any]) -> None:
        """Append one transition-attempt audit row (allowed or denied)."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            conn.execute(
                """INSERT INTO kg_lifecycle_audit
                   (kg_id, action, actor_tier, is_owner, from_status, to_status,
                    from_approval, to_approval, conformance, allowed, reason, badge, created_at,
                    actor, subject)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.kg_id, record.get("action"), record.get("actor_tier"),
                 1 if record.get("is_owner") else 0, record.get("from_status"),
                 record.get("to_status"), record.get("from_approval"), record.get("to_approval"),
                 record.get("conformance"), 1 if record.get("allowed") else 0,
                 record.get("reason"), record.get("badge"), now,
                 record.get("actor"),
                 json.dumps(record["subject"]) if record.get("subject") is not None else None),
            )
            conn.commit()
        finally:
            conn.close()

    def get_lifecycle_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the audit trail (newest first)."""
        conn = self._get_conn()
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            rows = conn.execute(
                "SELECT * FROM kg_lifecycle_audit WHERE kg_id=? ORDER BY id DESC LIMIT ?",
                (self.kg_id, int(limit)),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["subject"] = json.loads(d["subject"]) if d.get("subject") else None
                except (TypeError, ValueError):
                    d["subject"] = None
                out.append(d)
            return out
        finally:
            conn.close()

    @staticmethod
    def rename_kg(
        kg_id: str,
        new_name: str,
        description: Optional[str] = None,
        db_path: Optional[str] = None,
        database_name: Optional[str] = None,
    ) -> bool:
        if not db_path:
            try:
                from trusted_data_agent.core.config import APP_CONFIG
                db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
            except Exception:
                db_path = "tda_auth.db"
        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            now = datetime.now(timezone.utc).isoformat()
            sets, args = ["name=?"], [new_name]
            if description is not None:
                sets.append("description=?"); args.append(description)
            if database_name is not None:
                sets.append("database_name=?"); args.append(database_name)
            sets.append("updated_at=?"); args.append(now)
            cursor = conn.execute(
                f"UPDATE kg_metadata SET {', '.join(sets)} WHERE kg_id=?", (*args, kg_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    @staticmethod
    def set_active_kg(kg_id: str, db_path: Optional[str] = None) -> bool:
        if not db_path:
            try:
                from trusted_data_agent.core.config import APP_CONFIG
                db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
            except Exception:
                db_path = "tda_auth.db"
        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            row = conn.execute(
                "SELECT profile_id, user_uuid FROM kg_metadata WHERE kg_id=?",
                (kg_id,),
            ).fetchone()
            if not row:
                return False
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE kg_metadata SET is_active=0, updated_at=? "
                "WHERE profile_id=? AND user_uuid=?",
                (now, row["profile_id"], row["user_uuid"]),
            )
            conn.execute(
                "UPDATE kg_metadata SET is_active=1, updated_at=? WHERE kg_id=?",
                (now, kg_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def activate_for_profile(
        kg_id: str,
        assigned_profile_id: str,
        user_uuid: str,
        db_path: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """Canonical KG activation — the SINGLE source of truth for "which KG is
        active for a profile".

        Makes ``kg_id`` the SOLE active assignment for ``assigned_profile_id``
        (deactivates every other assignment, then activates — inserting the
        assignment row if it doesn't exist), and when the profile is the KG's
        owner, syncs ``kg_metadata.is_active`` so the two activation tables never
        drift.  Used by both constructors and the assignments/activate endpoint;
        never hand-roll this in SQL elsewhere.

        Pass an existing ``conn`` to participate in a caller's transaction (the
        caller then commits); otherwise a connection is opened and committed here.
        """
        own_conn = conn is None
        if own_conn:
            if not db_path:
                try:
                    from trusted_data_agent.core.config import APP_CONFIG
                    db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
                except Exception:
                    db_path = "tda_auth.db"
            conn = sqlite_util.connect(db_path)
        try:
            GraphStore._ensure_kg_metadata_table(conn)
            now = datetime.now(timezone.utc).isoformat()

            # Resolve the KG's owner (for the metadata sync + assignment insert).
            meta = conn.execute(
                "SELECT profile_id FROM kg_metadata WHERE kg_id=? AND user_uuid=?",
                (kg_id, user_uuid),
            ).fetchone()
            owner_profile_id = (meta[0] if meta else assigned_profile_id)

            # 1) Assignments: deactivate all for the profile, then activate this KG.
            conn.execute(
                "UPDATE kg_profile_assignments SET is_active=0 "
                "WHERE assigned_profile_id=? AND user_uuid=?",
                (assigned_profile_id, user_uuid),
            )
            cur = conn.execute(
                "UPDATE kg_profile_assignments SET is_active=1 "
                "WHERE kg_id=? AND assigned_profile_id=? AND user_uuid=?",
                (kg_id, assigned_profile_id, user_uuid),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO kg_profile_assignments "
                    "(kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (kg_id, owner_profile_id, assigned_profile_id, user_uuid),
                )
                conn.execute(
                    "UPDATE kg_profile_assignments SET is_active=1 "
                    "WHERE kg_id=? AND assigned_profile_id=? AND user_uuid=?",
                    (kg_id, assigned_profile_id, user_uuid),
                )

            # 2) Owner metadata: kg_metadata.is_active is the OWNER's active flag —
            # only sync it when the profile activating is the owner.
            if owner_profile_id == assigned_profile_id:
                conn.execute(
                    "UPDATE kg_metadata SET is_active=0, updated_at=? "
                    "WHERE profile_id=? AND user_uuid=?",
                    (now, assigned_profile_id, user_uuid),
                )
                conn.execute(
                    "UPDATE kg_metadata SET is_active=1, updated_at=? WHERE kg_id=?",
                    (now, kg_id),
                )

            if own_conn:
                conn.commit()
            return True
        finally:
            if own_conn:
                conn.close()

    @staticmethod
    def _load_all_kg_metadata(
        user_uuid: str,
        conn: sqlite3.Connection,
    ) -> Dict[str, Dict[str, Any]]:
        GraphStore._ensure_kg_metadata_table(conn)
        rows = conn.execute(
            "SELECT kg_id, profile_id, name, database_name, is_active "
            "FROM kg_metadata WHERE user_uuid=?",
            (user_uuid,),
        ).fetchall()
        return {row["kg_id"]: dict(row) for row in rows}

    # ── Cross-profile enumeration ─────────────────────────────────────────

    @staticmethod
    def _graph_stats(conn, kid: str, db_path: str) -> Dict[str, Any]:
        """A graph's figures for its card — drafts apart, the model's own counts — a pure
        function of its rows, so it is served at the graph's content revision
        (``trust.kg_revision.memo_at_revision``): the listing re-scanned every entity of
        every graph (a ``json_extract`` over ~245K rows per industry model) on each call.
        Read-only: the cached value is shared."""
        def _compute():
            _hidden = GraphStore.unpublished_rows(conn, kid)
            _hidden_ids = [i for i, _ in _hidden]
            rel_count = conn.execute(
                "SELECT COUNT(*) FROM kg_relationships WHERE kg_id = ?", (kid,),
            ).fetchone()[0] - GraphStore.count_touching(conn, kid, _hidden_ids)
            type_rows = conn.execute(
                "SELECT entity_type, COUNT(*) AS cnt "
                "FROM kg_entities WHERE kg_id = ? GROUP BY entity_type",
                (kid,),
            ).fetchall()
            entity_types = {r[0]: r[1] for r in type_rows}
            for _, _t in _hidden:
                entity_types[_t] = entity_types.get(_t, 0) - 1
            entity_types = {k: v for k, v in entity_types.items() if v > 0}
            rel_type_rows = conn.execute(
                "SELECT relationship_type, COUNT(*) AS cnt "
                "FROM kg_relationships WHERE kg_id = ? GROUP BY relationship_type",
                (kid,),
            ).fetchall()
            return {"drafts": len(_hidden), "rel_count": rel_count, "entity_types": entity_types,
                    "relationship_types": {r[0]: r[1] for r in rel_type_rows}}
        try:
            from trusted_data_agent.core.trust import kg_revision as _rev
        except Exception:  # noqa: BLE001 — the trust suite is removable; compute directly
            return _compute()
        return _rev.memo_at_revision(kid, db_path, "card_stats", _compute,
                                     code_ver=GraphStore._stats_code_version())

    _STATS_CODE_VER: Optional[str] = None

    @staticmethod
    def _stats_code_version() -> str:
        if GraphStore._STATS_CODE_VER is None:
            import os as _os
            from trusted_data_agent.core.trust import kg_revision as _rev
            import trusted_data_agent.kgstore as _ks
            GraphStore._STATS_CODE_VER = _rev.code_version(
                _os.path.abspath(__file__),
                _os.path.join(_os.path.dirname(_ks.__file__), "types.py"))
        return GraphStore._STATS_CODE_VER

    @staticmethod
    def list_all_graphs(
        user_uuid: str,
        db_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List all knowledge graphs for a user across all profiles."""
        if not db_path:
            try:
                from trusted_data_agent.core.config import APP_CONFIG
                db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
            except Exception:
                db_path = "tda_auth.db"

        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            GraphStore._ensure_kg_metadata_table(conn)

            meta_rows = conn.execute(
                """
                SELECT km.kg_id, km.profile_id, km.name AS kg_name, km.database_name,
                       km.description, km.is_active, km.created_at, km.updated_at,
                       km.kg_store_config_id, km.source_descriptor_json, km.source_validation_json,
                       km.lifecycle_status, km.semantic_version, km.conformance_status,
                       COUNT(e.id) AS entity_count
                FROM kg_metadata km
                LEFT JOIN kg_entities e ON e.kg_id = km.kg_id
                WHERE km.user_uuid = ? AND km.released_of IS NULL
                GROUP BY km.kg_id
                ORDER BY km.name COLLATE NOCASE ASC
                """,
                (user_uuid,),
            ).fetchall()
            # the model's figures are the model's: every node of an unpublished extension (its
            # drafts and what landing them wrote) is counted apart — the ONE rule, see below

            results: List[Dict[str, Any]] = []
            for row in meta_rows:
                kid = row["kg_id"]

                _st = GraphStore._graph_stats(conn, kid, db_path)
                _drafts = _st["drafts"]
                rel_count = _st["rel_count"]
                entity_types = dict(_st["entity_types"])
                relationship_types = dict(_st["relationship_types"])

                # Re-syncable iff a source descriptor was stamped by a declarative
                # constructor. Surfaces the Update button on the card.
                _src_desc = None
                try:
                    _raw = row["source_descriptor_json"]
                    if _raw:
                        import json as _json
                        _src_desc = _json.loads(_raw)
                except Exception:
                    _src_desc = None

                _src_val = None
                try:
                    if row["source_validation_json"]:
                        import json as _json
                        _src_val = _json.loads(row["source_validation_json"])
                except Exception:
                    _src_val = None

                results.append({
                    "kg_id": kid,
                    "profile_id": row["profile_id"],
                    "kg_name": row["kg_name"],
                    "database_name": row["database_name"],
                    "description": row["description"],
                    "is_active": bool(row["is_active"]),
                    "total_entities": int(row["entity_count"] or 0) - _drafts,
                    "draft_entities": _drafts,
                    "total_relationships": rel_count,
                    "entity_types": entity_types,
                    "relationship_types": relationship_types,
                    "first_created": row["created_at"],
                    "last_updated": row["updated_at"],
                    "kg_store_config_id": row["kg_store_config_id"],
                    "is_resyncable": bool(_src_desc),
                    # WHEN the graph was last validated against its source + what that covered
                    # (None = never) — drives the card's Source-validated badge.
                    "source_validation": _src_val,
                    "source_label": GraphStore.source_label(_src_desc),
                    # Governed-product lifecycle (Tier 4.3) — drives the card badges.
                    "lifecycle_status": (row["lifecycle_status"] or "draft"),
                    "semantic_version": row["semantic_version"],
                    "conformance_status": row["conformance_status"],
                })

            return results
        finally:
            conn.close()
