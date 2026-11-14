"""
Neo4j KG Store backend.

Uses the official `neo4j` Python driver (sync).  If the package is absent the
module still imports and the backend is registered; only actual driver calls
will raise ImportError at runtime.

Node model:
  (:KGEntity {
      kg_id, entity_id,       # platform-issued integer ID
      name, entity_type,
      properties,             # JSON string
      source, source_detail,
      created_at, updated_at
  })

  (:KGEntityCounter {kg_id})  # monotonic counter for entity_id generation
  (:KGRelCounter    {kg_id})  # monotonic counter for relationship_id generation

Relationship model:
  (source)-[:KG_RELATIONSHIP {
      kg_id, rel_id,
      relationship_type, cardinality,
      metadata,               # JSON string
      source,
      created_at
  }]->(target)

Registered as 'neo4j' on module import.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .base import KGStoreBackend
from .capabilities import KGStoreCapability
from .factory import register_backend

try:
    import networkx as nx
    _HAS_NX = True
except Exception:  # noqa: BLE001
    nx = None
    _HAS_NX = False

logger = logging.getLogger("quart.app")

try:
    from neo4j import GraphDatabase  # type: ignore
    from neo4j.exceptions import ServiceUnavailable, AuthError  # type: ignore
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False
    GraphDatabase = None  # type: ignore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Neo4jKGBackend(KGStoreBackend):
    """
    Neo4j-backed knowledge graph storage.

    A single backend instance handles a specific Neo4j server/database.
    All operations are scoped to `kg_id` via node properties and indices.

    Configuration keys (in the config dict):
      uri      - Bolt URI, e.g. "bolt://localhost:7687"  (default: bolt://localhost:7687)
      username - Neo4j username (default: "neo4j")
      password - Neo4j password (default: "neo4j")
      database - Target database name (default: "neo4j")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._uri = self._config.get("uri", "bolt://localhost:7687")
        self._username = self._config.get("username", "neo4j")
        self._password = self._config.get("password", "neo4j")
        self._database = self._config.get("database", "neo4j")
        self._driver = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def initialize(self) -> None:
        if not HAS_NEO4J:
            logger.warning(
                "neo4j package not installed; Neo4j backend unavailable. "
                "Install with: pip install neo4j"
            )
            return
        self._driver = GraphDatabase.driver(
            self._uri,
            auth=(self._username, self._password),
        )
        self._ensure_indices()
        self._migrate_entity_types()

    def _migrate_entity_types(self) -> None:
        """In-place, idempotent entity-type rename (logical table/column →
        entity/attribute; physical physical_table/physical_column → table/column).

        ``layer`` lives inside the JSON ``properties`` string, so the layer-guarded
        logical rename is computed in Python (mirrors the SQLite migration). Edges
        carry no endpoint type, so renaming nodes is sufficient. Safe to re-run."""
        import json as _json
        from .migrations import remap_entity_type
        try:
            with self._session() as session:
                rows = session.run(
                    "MATCH (e:KGEntity) "
                    "WHERE e.entity_type IN ['column','table','physical_column','physical_table'] "
                    "RETURN e.kg_id AS kg_id, e.entity_id AS eid, "
                    "e.entity_type AS et, e.properties AS props"
                ).data()
                updated = 0
                for r in rows:
                    try:
                        props = _json.loads(r.get("props") or "{}")
                    except Exception:
                        props = {}
                    layer = props.get("layer") if isinstance(props, dict) else None
                    new_et = remap_entity_type(r.get("et"), layer)
                    if new_et != r.get("et"):
                        session.run(
                            "MATCH (e:KGEntity {kg_id: $kg_id, entity_id: $eid}) "
                            "SET e.entity_type = $new",
                            kg_id=r["kg_id"], eid=r["eid"], new=new_et,
                        )
                        updated += 1
                if updated:
                    logger.info(f"[KG migration] Neo4j entity-type rename: {updated} nodes")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[KG migration] Neo4j entity-type rename skipped: {exc}")

    def shutdown(self) -> None:
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

    def _ensure_indices(self) -> None:
        """Create indices for fast lookups if they don't exist yet."""
        try:
            with self._driver.session(database=self._database) as session:
                session.run(
                    "CREATE INDEX kg_entity_id IF NOT EXISTS "
                    "FOR (e:KGEntity) ON (e.kg_id, e.entity_id)"
                )
                session.run(
                    "CREATE INDEX kg_entity_name IF NOT EXISTS "
                    "FOR (e:KGEntity) ON (e.kg_id, e.name)"
                )
                session.run(
                    "CREATE INDEX kg_rel_id IF NOT EXISTS "
                    "FOR ()-[r:KG_RELATIONSHIP]-() ON (r.kg_id, r.rel_id)"
                )
        except Exception as exc:
            logger.warning("Neo4j index creation skipped: %s", exc)

    def _session(self):
        if not self._driver:
            raise RuntimeError(
                "Neo4j driver not initialised. Call initialize() first "
                "or check that the neo4j package is installed."
            )
        return self._driver.session(database=self._database)

    # ── Capabilities ──────────────────────────────────────────────────────

    def capabilities(self) -> Set[KGStoreCapability]:
        caps = {
            KGStoreCapability.ADD_ENTITY,
            KGStoreCapability.GET_ENTITY,
            KGStoreCapability.DELETE_ENTITY,
            KGStoreCapability.LIST_ENTITIES,
            KGStoreCapability.ADD_RELATIONSHIP,
            KGStoreCapability.DELETE_RELATIONSHIP,
            KGStoreCapability.LIST_RELATIONSHIPS,
            KGStoreCapability.IMPORT_BULK,
            KGStoreCapability.CLEAR_GRAPH,
            KGStoreCapability.GET_STATS,
            KGStoreCapability.GRAPH_ALGORITHMS,
            KGStoreCapability.NATIVE_GRAPH_QUERIES,
            KGStoreCapability.FULL_TEXT_SEARCH,
        }
        if _HAS_NX:
            caps.add(KGStoreCapability.ADAPTIVE_SUBGRAPH)  # real adaptive over a Cypher-built DiGraph
        return caps

    # ── Connectivity check ────────────────────────────────────────────────

    def test_connection(self) -> Dict[str, Any]:
        if not HAS_NEO4J:
            return {"success": False, "message": "neo4j package not installed"}
        if not self._driver:
            # Try to initialise on demand
            try:
                self.initialize()
            except Exception as exc:
                return {"success": False, "message": f"Initialisation failed: {exc}"}
        try:
            with self._session() as session:
                result = session.run("RETURN 1 AS ok")
                record = result.single()
                if record and record["ok"] == 1:
                    return {"success": True, "message": "Neo4j connection OK"}
            return {"success": False, "message": "Unexpected response from Neo4j"}
        except ServiceUnavailable as exc:
            return {"success": False, "message": f"Neo4j unavailable: {exc}"}
        except AuthError as exc:
            return {"success": False, "message": f"Neo4j auth error: {exc}"}
        except Exception as exc:
            return {"success": False, "message": f"Neo4j error: {exc}"}

    # ── ID counter helpers ────────────────────────────────────────────────

    def _next_entity_id(self, session, kg_id: str) -> int:
        result = session.run(
            """
            MERGE (c:KGEntityCounter {kg_id: $kg_id})
            ON CREATE SET c.last_id = 0
            SET c.last_id = c.last_id + 1
            RETURN c.last_id AS next_id
            """,
            kg_id=kg_id,
        )
        return result.single()["next_id"]

    def _next_rel_id(self, session, kg_id: str) -> int:
        result = session.run(
            """
            MERGE (c:KGRelCounter {kg_id: $kg_id})
            ON CREATE SET c.last_id = 0
            SET c.last_id = c.last_id + 1
            RETURN c.last_id AS next_id
            """,
            kg_id=kg_id,
        )
        return result.single()["next_id"]

    # ── Entity helpers ────────────────────────────────────────────────────

    @staticmethod
    def _record_to_entity(record: Any) -> Dict[str, Any]:
        e = dict(record["e"])
        props = e.get("properties")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        e["properties"] = props or {}
        # Expose 'id' as an alias for entity_id so callers using either key work.
        if "entity_id" in e and "id" not in e:
            e["id"] = e["entity_id"]
        return e

    # ── Entity CRUD ───────────────────────────────────────────────────────

    def add_entity(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        name: str,
        entity_type: str,
        properties: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        source_detail: Optional[str] = None,
    ) -> int:
        now = _now()
        props_json = json.dumps(properties or {})
        with self._session() as session:
            with session.begin_transaction() as tx:
                # Upsert: if name+type already exist in kg, update; else create
                existing = tx.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id, name: $name, entity_type: $entity_type}) "
                    "RETURN e.entity_id AS eid",
                    kg_id=kg_id, name=name, entity_type=entity_type,
                ).single()
                if existing:
                    eid = existing["eid"]
                    tx.run(
                        "MATCH (e:KGEntity {kg_id: $kg_id, entity_id: $eid}) "
                        "SET e.properties = $props, e.updated_at = $now",
                        kg_id=kg_id, eid=eid, props=props_json, now=now,
                    )
                    return eid
                eid = self._next_entity_id(tx, kg_id)
                tx.run(
                    """
                    CREATE (e:KGEntity {
                        kg_id: $kg_id, entity_id: $eid,
                        name: $name, entity_type: $entity_type,
                        properties: $props,
                        source: $source, source_detail: $source_detail,
                        created_at: $now, updated_at: $now
                    })
                    """,
                    kg_id=kg_id, eid=eid, name=name, entity_type=entity_type,
                    props=props_json, source=source, source_detail=source_detail,
                    now=now,
                )
                return eid

    def get_entity(self, kg_id: str, entity_id: int) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            result = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id, entity_id: $eid}) RETURN e",
                kg_id=kg_id, eid=entity_id,
            )
            record = result.single()
            return self._record_to_entity(record) if record else None

    def get_entity_by_name(
        self,
        kg_id: str,
        name: str,
        entity_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            if entity_type:
                result = session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id, entity_type: $et}) "
                    "WHERE toLower(e.name) = toLower($name) RETURN e LIMIT 1",
                    kg_id=kg_id, name=name, et=entity_type,
                )
            else:
                result = session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id}) "
                    "WHERE toLower(e.name) = toLower($name) RETURN e LIMIT 1",
                    kg_id=kg_id, name=name,
                )
            record = result.single()
            return self._record_to_entity(record) if record else None

    def search_entities(
        self,
        kg_id: str,
        query_text: str,
        limit: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Parity with SQLite: match name AND serialized properties (label, description,
        # decoded values). Name-only search silently halved recall vs SQLite.
        q = query_text.lower()
        et_clause = " AND e.entity_type = $et" if entity_type else ""
        with self._session() as session:
            result = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) "
                f"WHERE (toLower(e.name) CONTAINS $q OR toLower(toString(e.properties)) CONTAINS $q){et_clause} "
                "RETURN e LIMIT $lim",
                kg_id=kg_id, q=q, et=entity_type, lim=limit,
            )
            return [self._record_to_entity(r) for r in result]

    def list_entities(
        self,
        kg_id: str,
        entity_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._session() as session:
            if entity_type:
                result = session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id, entity_type: $et}) "
                    "RETURN e ORDER BY e.name LIMIT $lim",
                    kg_id=kg_id, et=entity_type, lim=limit,
                )
            else:
                result = session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id}) "
                    "RETURN e ORDER BY e.name LIMIT $lim",
                    kg_id=kg_id, lim=limit,
                )
            return [self._record_to_entity(r) for r in result]

    def update_entity(
        self,
        kg_id: str,
        entity_id: int,
        properties: Dict[str, Any],
    ) -> bool:
        with self._session() as session:
            result = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id, entity_id: $eid}) "
                "SET e.properties = $props, e.updated_at = $now "
                "RETURN e.entity_id",
                kg_id=kg_id, eid=entity_id,
                props=json.dumps(properties),
                now=_now(),
            )
            return result.single() is not None

    def delete_entity(self, kg_id: str, entity_id: int) -> bool:
        with self._session() as session:
            result = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id, entity_id: $eid}) "
                "DETACH DELETE e RETURN $eid AS deleted",
                kg_id=kg_id, eid=entity_id,
            )
            return result.single() is not None

    # ── Relationship CRUD ─────────────────────────────────────────────────

    def add_relationship(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        cardinality: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
    ) -> int:
        now = _now()
        with self._session() as session:
            with session.begin_transaction() as tx:
                # Check for existing relationship
                existing = tx.run(
                    """
                    MATCH (src:KGEntity {kg_id: $kg_id, entity_id: $src_id})
                    MATCH (tgt:KGEntity {kg_id: $kg_id, entity_id: $tgt_id})
                    MATCH (src)-[r:KG_RELATIONSHIP {
                        kg_id: $kg_id,
                        relationship_type: $rtype
                    }]->(tgt)
                    RETURN r.rel_id AS rid
                    """,
                    kg_id=kg_id, src_id=source_entity_id, tgt_id=target_entity_id,
                    rtype=relationship_type,
                ).single()
                if existing:
                    return existing["rid"]
                rid = self._next_rel_id(tx, kg_id)
                tx.run(
                    """
                    MATCH (src:KGEntity {kg_id: $kg_id, entity_id: $src_id})
                    MATCH (tgt:KGEntity {kg_id: $kg_id, entity_id: $tgt_id})
                    CREATE (src)-[:KG_RELATIONSHIP {
                        kg_id: $kg_id, rel_id: $rid,
                        relationship_type: $rtype,
                        cardinality: $card,
                        metadata: $meta,
                        source: $source,
                        created_at: $now
                    }]->(tgt)
                    """,
                    kg_id=kg_id, src_id=source_entity_id, tgt_id=target_entity_id,
                    rid=rid, rtype=relationship_type,
                    card=cardinality, meta=json.dumps(metadata or {}),
                    source=source, now=now,
                )
                return rid

    def get_relationships(
        self,
        kg_id: str,
        entity_id: int,
        direction: str = "both",
    ) -> List[Dict[str, Any]]:
        with self._session() as session:
            if direction == "outgoing":
                cypher = (
                    "MATCH (src:KGEntity {kg_id: $kg_id, entity_id: $eid})"
                    "-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->(tgt:KGEntity) "
                    "RETURN r, "
                    "src.entity_id AS source_id, tgt.entity_id AS target_id, "
                    "src.name AS source_name, tgt.name AS target_name, "
                    "src.entity_type AS source_type, tgt.entity_type AS target_type"
                )
            elif direction == "incoming":
                cypher = (
                    "MATCH (src:KGEntity)-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->"
                    "(tgt:KGEntity {kg_id: $kg_id, entity_id: $eid}) "
                    "RETURN r, "
                    "src.entity_id AS source_id, tgt.entity_id AS target_id, "
                    "src.name AS source_name, tgt.name AS target_name, "
                    "src.entity_type AS source_type, tgt.entity_type AS target_type"
                )
            else:  # both
                cypher = (
                    "MATCH (src:KGEntity {kg_id: $kg_id})-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->"
                    "(tgt:KGEntity {kg_id: $kg_id}) "
                    "WHERE src.entity_id = $eid OR tgt.entity_id = $eid "
                    "RETURN r, "
                    "src.entity_id AS source_id, tgt.entity_id AS target_id, "
                    "src.name AS source_name, tgt.name AS target_name, "
                    "src.entity_type AS source_type, tgt.entity_type AS target_type"
                )
            result = session.run(cypher, kg_id=kg_id, eid=entity_id)
            rows = []
            for rec in result:
                r = dict(rec["r"])
                r["source_entity_id"] = rec["source_id"]
                r["target_entity_id"] = rec["target_id"]
                r["source_name"] = rec["source_name"]
                r["target_name"] = rec["target_name"]
                r["source_type"] = rec["source_type"]
                r["target_type"] = rec["target_type"]
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        r["metadata"] = json.loads(meta)
                    except Exception:
                        r["metadata"] = {}
                rows.append(r)
            return rows

    def list_relationships(
        self,
        kg_id: str,
        entity_id: Optional[int] = None,
        relationship_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._session() as session:
            conditions = ["r.kg_id = $kg_id"]
            params: Dict[str, Any] = {"kg_id": kg_id, "lim": limit}
            if entity_id is not None:
                conditions.append("(src.entity_id = $eid OR tgt.entity_id = $eid)")
                params["eid"] = entity_id
            if relationship_type:
                conditions.append("r.relationship_type = $rtype")
                params["rtype"] = relationship_type
            where = " AND ".join(conditions)
            cypher = (
                "MATCH (src:KGEntity {kg_id: $kg_id})"
                "-[r:KG_RELATIONSHIP]->"
                "(tgt:KGEntity {kg_id: $kg_id}) "
                f"WHERE {where} "
                "RETURN r, "
                "src.entity_id AS source_id, tgt.entity_id AS target_id, "
                "src.name AS source_name, tgt.name AS target_name, "
                "src.entity_type AS source_type, tgt.entity_type AS target_type "
                "LIMIT $lim"
            )
            result = session.run(cypher, **params)
            rows = []
            for rec in result:
                r = dict(rec["r"])
                r["source_entity_id"] = rec["source_id"]
                r["target_entity_id"] = rec["target_id"]
                r["source_name"] = rec["source_name"]
                r["target_name"] = rec["target_name"]
                r["source_type"] = rec["source_type"]
                r["target_type"] = rec["target_type"]
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        r["metadata"] = json.loads(meta)
                    except Exception:
                        r["metadata"] = {}
                rows.append(r)
            return rows

    def delete_relationship(self, kg_id: str, relationship_id: int) -> bool:
        with self._session() as session:
            result = session.run(
                "MATCH ()-[r:KG_RELATIONSHIP {kg_id: $kg_id, rel_id: $rid}]->() "
                "DELETE r RETURN $rid AS deleted",
                kg_id=kg_id, rid=relationship_id,
            )
            return result.single() is not None

    # ── Bulk operations ───────────────────────────────────────────────────

    def import_bulk(
        self,
        kg_id: str,
        profile_id: str,
        user_uuid: str,
        entities: List[Dict],
        relationships: List[Dict],
    ) -> Dict[str, int]:
        entities_added = 0
        relationships_added = 0
        for ent in entities:
            try:
                self.add_entity(
                    kg_id=kg_id,
                    profile_id=profile_id,
                    user_uuid=user_uuid,
                    name=ent.get("name", ""),
                    entity_type=ent.get("entity_type", "business_concept"),
                    properties=ent.get("properties", {}),
                    source=ent.get("source", "import"),
                    source_detail=ent.get("source_detail"),
                )
                entities_added += 1
            except Exception as exc:
                logger.warning(f"Neo4j bulk import: failed to add entity '{ent.get('name')}': {exc}")
        for rel in relationships:
            try:
                rtype = rel.get("relationship_type", "relates_to")
                # Always resolve by name first during bulk import: numeric IDs
                # from another backend (e.g. SQLite row IDs) are meaningless in
                # Neo4j — the newly-added entities have different IDs here.
                src = None
                tgt = None
                if rel.get("source_name"):
                    src_ent = self.get_entity_by_name(
                        kg_id, rel["source_name"], rel.get("source_type")
                    )
                    src = src_ent["entity_id"] if src_ent else None
                if src is None:
                    src = rel.get("source_entity_id") or rel.get("source_id")
                if rel.get("target_name"):
                    tgt_ent = self.get_entity_by_name(
                        kg_id, rel["target_name"], rel.get("target_type")
                    )
                    tgt = tgt_ent["entity_id"] if tgt_ent else None
                if tgt is None:
                    tgt = rel.get("target_entity_id") or rel.get("target_id")
                if src is not None and tgt is not None:
                    self.add_relationship(
                        kg_id=kg_id,
                        profile_id=profile_id,
                        user_uuid=user_uuid,
                        source_entity_id=int(src),
                        target_entity_id=int(tgt),
                        relationship_type=rtype,
                        cardinality=rel.get("cardinality"),
                        metadata=rel.get("metadata"),
                        source=rel.get("source", "import"),
                    )
                    relationships_added += 1
                else:
                    logger.warning(
                        f"Neo4j bulk import: could not resolve entities for "
                        f"{rel.get('source_name', src)} --[{rtype}]--> "
                        f"{rel.get('target_name', tgt)}"
                    )
            except Exception as exc:
                logger.warning(f"Neo4j bulk import: failed to add relationship: {exc}")
        return {
            "entities_added": entities_added,
            "relationships_added": relationships_added,
        }

    def clear_graph(self, kg_id: str) -> Dict[str, int]:
        with self._session() as session:
            # Count before delete
            counts = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) "
                "OPTIONAL MATCH (e)-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->() "
                "RETURN count(DISTINCT e) AS ec, count(r) AS rc",
                kg_id=kg_id,
            ).single()
            ec = counts["ec"] if counts else 0
            rc = counts["rc"] if counts else 0
            session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) DETACH DELETE e",
                kg_id=kg_id,
            )
            session.run(
                "MATCH (c:KGEntityCounter {kg_id: $kg_id}) DELETE c",
                kg_id=kg_id,
            )
            session.run(
                "MATCH (c:KGRelCounter {kg_id: $kg_id}) DELETE c",
                kg_id=kg_id,
            )
            return {"entities_deleted": ec, "relationships_deleted": rc}

    # ── Graph statistics ──────────────────────────────────────────────────

    def get_stats(self, kg_id: str) -> Dict[str, Any]:
        with self._session() as session:
            ec = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) RETURN count(e) AS n",
                kg_id=kg_id,
            ).single()["n"]
            rc = session.run(
                "MATCH ()-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->() RETURN count(r) AS n",
                kg_id=kg_id,
            ).single()["n"]
            et_rows = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) "
                "RETURN e.entity_type AS et, count(e) AS n",
                kg_id=kg_id,
            )
            entity_types = {row["et"]: row["n"] for row in et_rows}
            rt_rows = session.run(
                "MATCH ()-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->() "
                "RETURN r.relationship_type AS rt, count(r) AS n",
                kg_id=kg_id,
            )
            relationship_types = {row["rt"]: row["n"] for row in rt_rows}
            return {
                "status": "success",
                "total_entities": ec,
                "total_relationships": rc,
                "entity_types": entity_types,
                "relationship_types": relationship_types,
                "networkx_available": False,
                "density": (rc / (ec * (ec - 1))) if ec > 1 else 0,
            }

    # ── Graph algorithms (basic subgraph extraction via Cypher) ───────────

    def extract_subgraph(
        self,
        kg_id: str,
        entity_ids: List[int],
        depth: int = 2,
        max_nodes: int = 50,
    ) -> Dict[str, List]:
        with self._session() as session:
            result = session.run(
                """
                MATCH (seed:KGEntity {kg_id: $kg_id})
                WHERE seed.entity_id IN $ids
                CALL apoc.path.subgraphNodes(seed, {
                    maxLevel: $depth,
                    limit: $max_nodes
                })
                YIELD node
                WHERE node:KGEntity AND node.kg_id = $kg_id
                WITH collect(DISTINCT node) AS nodes
                UNWIND nodes AS n
                OPTIONAL MATCH (n)-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->(m:KGEntity {kg_id: $kg_id})
                WHERE m IN nodes
                RETURN nodes, collect(DISTINCT r) AS rels, collect(DISTINCT m) AS targets
                """,
                kg_id=kg_id, ids=entity_ids, depth=depth, max_nodes=max_nodes,
            )
            record = result.single()
            if not record:
                return {"entities": [], "relationships": []}
            entities = [self._record_to_entity_from_node(n) for n in record["nodes"]]
            rels = [self._record_to_rel(r) for r in record["rels"]]
            return {"entities": entities, "relationships": rels}

    def _to_networkx(self, kg_id: str):
        """Build a NetworkX DiGraph for kg_id from Cypher reads — the working graph the
        shared adaptive algorithm operates on. Node key = entity_id (the SQLite contract);
        node attrs mirror the SQLite _get_graph node; edge attrs carry the relationship."""
        G = nx.DiGraph()
        with self._session() as session:
            for rec in session.run("MATCH (e:KGEntity {kg_id: $kg_id}) RETURN e", kg_id=kg_id):
                e = self._record_to_entity(rec)
                G.add_node(e["id"], name=e.get("name"), entity_type=e.get("entity_type"),
                           properties=e.get("properties") or {}, source=e.get("source"),
                           source_detail=e.get("source_detail"),
                           created_at=e.get("created_at"), updated_at=e.get("updated_at"))
            for rec in session.run(
                "MATCH (src:KGEntity {kg_id: $kg_id})-[r:KG_RELATIONSHIP]->(tgt:KGEntity {kg_id: $kg_id}) "
                "RETURN r, src.entity_id AS s, tgt.entity_id AS t", kg_id=kg_id):
                r = dict(rec["r"])
                s_id, t_id = rec["s"], rec["t"]
                if s_id in G and t_id in G:
                    G.add_edge(s_id, t_id, rel_id=r.get("id") or r.get("relationship_id"),
                               relationship_type=r.get("relationship_type"),
                               cardinality=r.get("cardinality"),
                               metadata=r.get("metadata") if isinstance(r.get("metadata"), dict) else {},
                               source=r.get("source"), created_at=r.get("created_at"))
        return G

    def extract_subgraph_adaptive(
        self,
        kg_id: str,
        seed_entity_ids: List[int],
        query_entity_ids: Optional[List[int]] = None,
        max_nodes: int = 500,
        query_terms: Optional[List[str]] = None,
        per_table_cap: Optional[int] = None,
    ) -> Dict[str, List]:
        # True parity, not a BFS stub: build a DiGraph from Cypher and run the SAME pure
        # adaptive algorithm SQLite uses. One implementation, two data sources. Falls back
        # to plain BFS only when NetworkX is unavailable (the capability is then withheld).
        if _HAS_NX:
            try:
                from .sqlite_backend import _adaptive_subgraph_from_graph
                return _adaptive_subgraph_from_graph(
                    self._to_networkx(kg_id), seed_entity_ids, query_entity_ids,
                    max_nodes, query_terms, per_table_cap)
            except Exception as exc:  # noqa: BLE001 — never fail enrichment; degrade to BFS
                logger.warning(f"Neo4j adaptive extraction fell back to BFS: {exc}")
        return self.extract_subgraph(kg_id, seed_entity_ids, depth=3, max_nodes=max_nodes)

    def get_full_graph(self, kg_id: str, max_nodes: int = 100) -> Dict[str, List]:
        with self._session() as session:
            nodes = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) RETURN e LIMIT $lim",
                kg_id=kg_id, lim=max_nodes,
            )
            entities = [self._record_to_entity(r) for r in nodes]
            if not entities:
                return {"entities": [], "relationships": []}
            # Use "id" (the aliased field) for consistency with SQLite contract
            ids = [e["id"] for e in entities]
            rels_result = session.run(
                "MATCH (src:KGEntity {kg_id: $kg_id})-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->"
                "(tgt:KGEntity {kg_id: $kg_id}) "
                "WHERE src.entity_id IN $ids AND tgt.entity_id IN $ids "
                "RETURN r, "
                "src.entity_id AS source_id, tgt.entity_id AS target_id, "
                "src.name AS source_name, tgt.name AS target_name, "
                "src.entity_type AS source_type, tgt.entity_type AS target_type",
                kg_id=kg_id, ids=ids,
            )
            rels = []
            for rec in rels_result:
                r = dict(rec["r"])
                # Use source_id/target_id keys to match SQLite contract (graph-spec renderer)
                r["source_id"] = rec["source_id"]
                r["target_id"] = rec["target_id"]
                r["source_entity_id"] = rec["source_id"]
                r["target_entity_id"] = rec["target_id"]
                r["source_name"] = rec["source_name"]
                r["target_name"] = rec["target_name"]
                r["source_type"] = rec["source_type"]
                r["target_type"] = rec["target_type"]
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        r["metadata"] = json.loads(meta)
                    except Exception:
                        r["metadata"] = {}
                rels.append(r)
            return {"entities": entities, "relationships": rels}

    @staticmethod
    def _entity_facets(e: Dict[str, Any]):
        """(division, domain) facet keys (lower-cased) for an entity dict."""
        props = e.get("properties") or {}
        div = props.get("division"); dom = props.get("domain")
        if e.get("entity_type") == "domain":
            lvl = props.get("level"); nm = e.get("name")
            if lvl == "division" and not div: div = nm
            elif lvl == "domain" and not dom: dom = nm
        return (str(div).strip().lower() if div else None,
                str(dom).strip().lower() if dom else None)

    def get_typed_subgraph(
        self, kg_id: str, types: Optional[List[str]] = None, max_nodes: int = 500,
        focus: Optional[List[int]] = None, project_dependencies: bool = False,
        divisions: Optional[List[str]] = None, domains: Optional[List[str]] = None,
    ) -> Dict[str, List]:
        """Seed → connectivity-expand → fill (detail lens + focus lens, one engine).

        1. SEED — ``focus`` entity ids (focus+context: zoom/search/click/trace) seed the
           budget; else a type-stratified set of highest-degree nodes per selected type
           (every type represented).
        2. EXPAND — fill the remaining budget by walking outward from the seed, adding the
           neighbours most connected to the visible set (BFS waves) → a CONNECTED,
           dependency-coherent subgraph rather than scattered hubs.
        3. FILL — top up any unused budget (disconnected remainder) with highest-degree
           leftovers so the budget is honoured.
        """
        with self._session() as session:
            if types:
                sel_types = list(types)
            else:
                tq = session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id}) "
                    "RETURN DISTINCT e.entity_type AS t",
                    kg_id=kg_id,
                )
                sel_types = [r["t"] for r in tq if r["t"]]
            if not sel_types:
                return {"entities": [], "relationships": [], "facets": {}}

            # Facet catalog over the full KG (properties stored as JSON string →
            # parse + count in Python).  Only present facets are surfaced.
            facet_div: Dict[str, int] = {}
            facet_dom: Dict[str, int] = {}
            for rec in session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) RETURN e", kg_id=kg_id):
                d, m = self._entity_facets(self._record_to_entity(rec))
                if d: facet_div[d] = facet_div.get(d, 0) + 1
                if m: facet_dom[m] = facet_dom.get(m, 0) + 1
            facets: Dict[str, Dict[str, int]] = {}
            if facet_div: facets["division"] = facet_div
            if facet_dom: facets["domain"] = facet_dom

            dset = {str(d).strip().lower() for d in divisions if str(d).strip()} if divisions else None
            mset = {str(d).strip().lower() for d in domains if str(d).strip()} if domains else None

            entities: List[Dict[str, Any]] = []
            seen: set = set()

            def _absorb(res):
                for rec in res:
                    e = self._record_to_entity(rec)
                    if e["id"] not in seen:
                        seen.add(e["id"])
                        entities.append(e)
                        yield e["id"]

            # 1) SEED — focus ids, else stratified high-degree per type.
            if focus:
                list(_absorb(session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id}) WHERE e.entity_id IN $f RETURN e",
                    kg_id=kg_id, f=list(focus))))
            if not entities:
                seed_per_type = max(1, max(1, max_nodes // 2) // len(sel_types))
                for t in sel_types:
                    list(_absorb(session.run(
                        "MATCH (e:KGEntity {kg_id: $kg_id, entity_type: $t}) "
                        "OPTIONAL MATCH (e)-[r:KG_RELATIONSHIP {kg_id: $kg_id}]-() "
                        "WITH e, count(r) AS deg ORDER BY deg DESC, e.entity_id ASC "
                        "LIMIT $lim RETURN e",
                        kg_id=kg_id, t=t, lim=seed_per_type)))
            if not entities:
                return {"entities": [], "relationships": [], "facets": facets}

            # 2) EXPAND by connectivity (BFS waves) — neighbours most-connected to the
            #    visible set, restricted to the selected types.
            frontier = list(seen)
            while len(seen) < max_nodes and frontier:
                remaining = max_nodes - len(seen)
                res = session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id})-[:KG_RELATIONSHIP {kg_id: $kg_id}]-"
                    "(nbr:KGEntity {kg_id: $kg_id}) "
                    "WHERE e.entity_id IN $frontier AND NOT nbr.entity_id IN $seen "
                    "AND nbr.entity_type IN $sel "
                    "WITH nbr, count(*) AS conns ORDER BY conns DESC, nbr.entity_id ASC "
                    "LIMIT $lim RETURN nbr AS e",
                    kg_id=kg_id, frontier=frontier, seen=list(seen), sel=sel_types, lim=remaining)
                newly = list(_absorb(res))
                if not newly:
                    break
                frontier = newly

            # 3) FILL any unused budget with highest-degree leftovers (disconnected parts).
            if len(seen) < max_nodes:
                list(_absorb(session.run(
                    "MATCH (e:KGEntity {kg_id: $kg_id}) "
                    "WHERE e.entity_type IN $sel AND NOT e.entity_id IN $seen "
                    "OPTIONAL MATCH (e)-[r:KG_RELATIONSHIP {kg_id: $kg_id}]-() "
                    "WITH e, count(r) AS deg ORDER BY deg DESC, e.entity_id ASC "
                    "LIMIT $lim RETURN e",
                    kg_id=kg_id, sel=sel_types, seen=list(seen),
                    lim=max_nodes - len(seen))))

            # Facet filter (division/domain) — post-filter in Python since properties
            # are stored as a JSON string (no clean Cypher predicate).
            if dset is not None or mset is not None:
                kept = []
                for e in entities:
                    d, m = self._entity_facets(e)
                    if dset is not None and (d is None or d not in dset):
                        continue
                    if mset is not None and (m is None or m not in mset):
                        continue
                    kept.append(e)
                entities = kept
                seen = {e["id"] for e in entities}

            ids = list(seen)
            rels_result = session.run(
                "MATCH (src:KGEntity {kg_id: $kg_id})-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->"
                "(tgt:KGEntity {kg_id: $kg_id}) "
                "WHERE src.entity_id IN $ids AND tgt.entity_id IN $ids "
                "RETURN r, src.entity_id AS source_id, tgt.entity_id AS target_id, "
                "src.name AS source_name, tgt.name AS target_name, "
                "src.entity_type AS source_type, tgt.entity_type AS target_type",
                kg_id=kg_id, ids=ids,
            )
            rels = []
            for rec in rels_result:
                r = dict(rec["r"])
                r["source_id"] = rec["source_id"]
                r["target_id"] = rec["target_id"]
                r["source_entity_id"] = rec["source_id"]
                r["target_entity_id"] = rec["target_id"]
                r["source_name"] = rec["source_name"]
                r["target_name"] = rec["target_name"]
                r["source_type"] = rec["source_type"]
                r["target_type"] = rec["target_type"]
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        r["metadata"] = json.loads(meta)
                    except Exception:
                        r["metadata"] = {}
                rels.append(r)

            # Relationship projection (link aggregation): when a type filter hides the
            # nodes a relationship runs through, lift it up to the nearest VISIBLE
            # ancestor via the containment chain (*0..6 contains), so the derived
            # dependency between visible nodes shows (table↔table FK from column↔column;
            # domain↔domain from column→table→domain; etc.). Only when types is filtered.
            if project_dependencies and types and len(ids) <= 2500:
                existing = {(rr["source_id"], rr["target_id"], rr["relationship_type"]) for rr in rels}
                proj = session.run(
                    "MATCH (a:KGEntity {kg_id:$kg_id})-[r:KG_RELATIONSHIP {kg_id:$kg_id}]->"
                    "(b:KGEntity {kg_id:$kg_id}) "
                    "WHERE r.relationship_type <> 'contains' "
                    "AND (NOT a.entity_id IN $chosen OR NOT b.entity_id IN $chosen) "
                    "MATCH (la:KGEntity {kg_id:$kg_id})"
                    "-[:KG_RELATIONSHIP*0..6 {kg_id:$kg_id, relationship_type:'contains'}]->(a) "
                    "WHERE la.entity_id IN $chosen "
                    "MATCH (lb:KGEntity {kg_id:$kg_id})"
                    "-[:KG_RELATIONSHIP*0..6 {kg_id:$kg_id, relationship_type:'contains'}]->(b) "
                    "WHERE lb.entity_id IN $chosen AND la.entity_id <> lb.entity_id "
                    "RETURN la.entity_id AS source_id, la.name AS source_name, "
                    "la.entity_type AS source_type, lb.entity_id AS target_id, "
                    "lb.name AS target_name, lb.entity_type AS target_type, "
                    "r.relationship_type AS rt, count(*) AS cnt, "
                    # The real underlying endpoints (e.g. the join columns) so the derived
                    # table↔table edge can answer "which column connects these?" even when
                    # those columns are outside the render budget.
                    "collect(DISTINCT {`from`: a.name, `to`: b.name})[0..25] AS via",
                    kg_id=kg_id, chosen=ids,
                )
                for rec in proj:
                    key = (rec["source_id"], rec["target_id"], rec["rt"])
                    if key in existing:
                        continue
                    existing.add(key)
                    rels.append({
                        "source_id": rec["source_id"], "target_id": rec["target_id"],
                        "source_entity_id": rec["source_id"], "target_entity_id": rec["target_id"],
                        "source_name": rec["source_name"], "target_name": rec["target_name"],
                        "source_type": rec["source_type"], "target_type": rec["target_type"],
                        "relationship_type": rec["rt"], "cardinality": None,
                        "metadata": {"derived": True, "count": rec["cnt"], "via": rec["via"]},
                    })
            return {"entities": entities, "relationships": rels, "facets": facets}

    def get_schema_overview(self, kg_id: str) -> Dict[str, List]:
        """Type-level meta-graph (overview lens) — entity-type nodes + aggregated
        type→type relationships, always tiny + complete."""
        with self._session() as session:
            nrows = session.run(
                "MATCH (e:KGEntity {kg_id: $kg_id}) "
                "RETURN e.entity_type AS t, count(*) AS c ORDER BY t",
                kg_id=kg_id)
            nodes = [{"type": r["t"] or "unknown", "count": r["c"]} for r in nrows]
            erows = session.run(
                "MATCH (s:KGEntity {kg_id: $kg_id})-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->"
                "(t:KGEntity {kg_id: $kg_id}) "
                "RETURN s.entity_type AS st, r.relationship_type AS rt, t.entity_type AS tt, "
                "count(*) AS c ORDER BY st, rt, tt",
                kg_id=kg_id)
            edges = [{"source_type": r["st"] or "unknown",
                      "relationship_type": r["rt"] or "unknown",
                      "target_type": r["tt"] or "unknown", "count": r["c"]} for r in erows]
            return {"nodes": nodes, "edges": edges}

    def find_shortest_path(
        self, kg_id: str, source_id: int, target_id: int
    ) -> Optional[List[int]]:
        with self._session() as session:
            result = session.run(
                """
                MATCH (src:KGEntity {kg_id: $kg_id, entity_id: $src_id}),
                      (tgt:KGEntity {kg_id: $kg_id, entity_id: $tgt_id})
                MATCH path = shortestPath((src)-[:KG_RELATIONSHIP*]->(tgt))
                RETURN [n IN nodes(path) | n.entity_id] AS ids
                """,
                kg_id=kg_id, src_id=source_id, tgt_id=target_id,
            )
            record = result.single()
            return record["ids"] if record else None

    def get_connected_entities(self, kg_id: str, entity_id: int) -> Dict[str, List[int]]:
        with self._session() as session:
            out = session.run(
                "MATCH (:KGEntity {kg_id: $kg_id, entity_id: $eid})"
                "-[:KG_RELATIONSHIP {kg_id: $kg_id}]->(tgt:KGEntity {kg_id: $kg_id}) "
                "RETURN tgt.entity_id AS id",
                kg_id=kg_id, eid=entity_id,
            )
            inc = session.run(
                "MATCH (src:KGEntity {kg_id: $kg_id})"
                "-[:KG_RELATIONSHIP {kg_id: $kg_id}]->(:KGEntity {kg_id: $kg_id, entity_id: $eid}) "
                "RETURN src.entity_id AS id",
                kg_id=kg_id, eid=entity_id,
            )
            return {
                "outgoing": [r["id"] for r in out],
                "incoming": [r["id"] for r in inc],
            }

    def get_node_neighborhood(self, kg_id: str, entity_id: int,
                              max_neighbors: int = 500) -> Dict[str, Any]:
        """COMPLETE 1-hop neighbourhood (all neighbours + connecting edges), uncapped by
        the render budget — the consistency guarantee for node focus. Capped at
        max_neighbors (highest-degree kept) with an honest `truncated` flag."""
        with self._session() as session:
            total = session.run(
                "MATCH (n:KGEntity {kg_id:$kg_id, entity_id:$eid})"
                "-[:KG_RELATIONSHIP {kg_id:$kg_id}]-(m:KGEntity {kg_id:$kg_id}) "
                "RETURN count(DISTINCT m) AS c", kg_id=kg_id, eid=entity_id,
            ).single()
            total_degree = (total and total["c"]) or 0
            # The focus node + its neighbours (highest-degree first), capped.
            ids = [entity_id]
            nrec = session.run(
                "MATCH (n:KGEntity {kg_id:$kg_id, entity_id:$eid})"
                "-[:KG_RELATIONSHIP {kg_id:$kg_id}]-(m:KGEntity {kg_id:$kg_id}) "
                "WITH DISTINCT m, COUNT { (m)-[:KG_RELATIONSHIP {kg_id:$kg_id}]-() } AS deg "
                "RETURN m.entity_id AS id ORDER BY deg DESC LIMIT $lim",
                kg_id=kg_id, eid=entity_id, lim=max_neighbors,
            )
            ids.extend(r["id"] for r in nrec)
            # Entities for the focus node + its neighbours.
            erec = session.run(
                "MATCH (e:KGEntity {kg_id:$kg_id}) WHERE e.entity_id IN $ids RETURN e",
                kg_id=kg_id, ids=ids,
            )
            entities = [self._record_to_entity(r) for r in erec]
            # Induced edges among them.
            rels_result = session.run(
                "MATCH (src:KGEntity {kg_id: $kg_id})-[r:KG_RELATIONSHIP {kg_id: $kg_id}]->"
                "(tgt:KGEntity {kg_id: $kg_id}) "
                "WHERE src.entity_id IN $ids AND tgt.entity_id IN $ids "
                "RETURN r, src.entity_id AS source_id, tgt.entity_id AS target_id, "
                "src.name AS source_name, tgt.name AS target_name, "
                "src.entity_type AS source_type, tgt.entity_type AS target_type",
                kg_id=kg_id, ids=ids,
            )
            rels = []
            for rec in rels_result:
                r = dict(rec["r"])
                r["source_id"] = rec["source_id"]; r["target_id"] = rec["target_id"]
                r["source_entity_id"] = rec["source_id"]; r["target_entity_id"] = rec["target_id"]
                r["source_name"] = rec["source_name"]; r["target_name"] = rec["target_name"]
                r["source_type"] = rec["source_type"]; r["target_type"] = rec["target_type"]
                meta = r.get("metadata")
                if isinstance(meta, str):
                    try:
                        r["metadata"] = json.loads(meta)
                    except Exception:
                        r["metadata"] = {}
                rels.append(r)
            return {"entities": entities, "relationships": rels,
                    "truncated": total_degree > max_neighbors, "total_degree": total_degree}

    def get_entity_importance(self, kg_id: str) -> Dict[int, float]:
        """Approximate PageRank via Cypher degree centrality."""
        with self._session() as session:
            result = session.run(
                """
                MATCH (e:KGEntity {kg_id: $kg_id})
                OPTIONAL MATCH (e)-[out:KG_RELATIONSHIP {kg_id: $kg_id}]->()
                OPTIONAL MATCH ()-[inc:KG_RELATIONSHIP {kg_id: $kg_id}]->(e)
                RETURN e.entity_id AS eid,
                       count(DISTINCT out) + count(DISTINCT inc) AS degree
                """,
                kg_id=kg_id,
            )
            rows = {r["eid"]: float(r["degree"]) for r in result}
            if not rows:
                return {}
            max_deg = max(rows.values()) or 1.0
            return {eid: deg / max_deg for eid, deg in rows.items()}

    def detect_cycles(self, kg_id: str) -> List[List[int]]:
        """Detect cycles using Cypher. Returns list of cycle node ID lists."""
        # Simplified: detect any path that leads back to start (depth-limited)
        with self._session() as session:
            result = session.run(
                """
                MATCH path = (n:KGEntity {kg_id: $kg_id})-[:KG_RELATIONSHIP*2..6]->(n)
                RETURN [node IN nodes(path) | node.entity_id] AS cycle
                LIMIT 10
                """,
                kg_id=kg_id,
            )
            return [r["cycle"] for r in result]

    def invalidate_cache(self, kg_id: str) -> None:
        """No-op: Neo4j backend has no client-side cache."""

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _record_to_entity_from_node(node: Any) -> Dict[str, Any]:
        e = dict(node)
        props = e.get("properties")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        e["properties"] = props or {}
        return e

    @staticmethod
    def _record_to_rel(rel: Any) -> Dict[str, Any]:
        r = dict(rel)
        meta = r.get("metadata")
        if isinstance(meta, str):
            try:
                r["metadata"] = json.loads(meta)
            except Exception:
                r["metadata"] = {}
        return r


# ── Registration ──────────────────────────────────────────────────────────────

register_backend("neo4j", Neo4jKGBackend)
