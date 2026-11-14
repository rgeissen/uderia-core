"""
kgstore.migrations — KG entity-type rename data migration (entity/attribute vs
table/column).

Background
----------
The KG model originally named the LOGICAL layer with physical terms
(``table``/``column``) and prefixed the PHYSICAL layer
(``physical_table``/``physical_column``).  The corrected ontology is:

    logical (LDM)   : entity   (was table),   attribute (was column)
    physical (PDM)  : table    (was physical_table), column (was physical_column)

``entity_type`` is a free-text column / property (no DB CHECK), so this is a
**data** migration, not a schema change.

Ordering & idempotency (the two things that make this safe)
-----------------------------------------------------------
* **Logical first.** Rename ``column``/``table`` (logical) → ``attribute``/``entity``
  BEFORE renaming ``physical_column``/``physical_table`` → ``column``/``table``,
  otherwise the freshly-renamed physical nodes would be caught by the logical pass.
* **Layer guard on the logical pass.** After a first migration the *physical* nodes
  ARE named ``table``/``column``.  Without a guard a second run would rename them to
  ``entity``/``attribute``.  The logical pass therefore only touches nodes whose
  ``layer`` is ``logical`` or absent (NULL) — physical nodes (``layer='physical'``)
  are excluded, so re-running is a clean no-op.

Three application surfaces, one mapping
---------------------------------------
* ``migrate_entity_types_sqlite(conn)`` — in-place SQLite (startup / per-config).
* ``Neo4jKGBackend._migrate_entity_types()`` — in-place Neo4j (mirrors this logic).
* ``migrate_kg_payload_types(entities, relationships)`` — pure remap for imported
  marketplace / agent-pack payloads (old exports → new model).  Applied at the
  ``GraphStore.import_bulk`` chokepoint, so every import path is covered.
"""

from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

# Physical names are unambiguous (no logical node was ever named physical_*),
# so they map unconditionally.
_PHYSICAL_RENAME = {"physical_table": "table", "physical_column": "column"}
# Logical names overlap with the *new* physical names, so they only map when the
# node is logical (layer == "logical" or absent).
_LOGICAL_RENAME = {"table": "entity", "column": "attribute"}

# Post-rename type → layer, for backfilling `properties.layer` on entities that
# predate the layer model (ONTOLOGY_ARCHITECTURE §2.2/§2.8 require it set).
_LOGICAL_TYPES = (
    "database", "domain", "entity", "attribute", "foreign_key",
    "business_concept", "taxonomy", "metric",
)
_PHYSICAL_TYPES = ("catalog", "schema", "table", "column", "ddl_artifact")


def remap_entity_type(etype: Optional[str], layer: Optional[str]) -> Optional[str]:
    """Map one ``entity_type`` from the old ontology to the new one.

    Layer-aware and idempotent: applying it to an already-migrated value is a no-op
    (a physical ``table``/``column`` carries ``layer='physical'`` and is left alone;
    a logical ``entity``/``attribute`` isn't in either map)."""
    if etype in _PHYSICAL_RENAME:
        return _PHYSICAL_RENAME[etype]
    if etype in _LOGICAL_RENAME and (layer is None or layer == "logical"):
        return _LOGICAL_RENAME[etype]
    return etype


def migrate_kg_payload_types(
    entities: Optional[List[Dict[str, Any]]],
    relationships: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rewrite an import payload's entity/relationship type names in place.

    Relationship endpoint types are remapped using the *endpoint entity's* layer
    (looked up by name from the same payload) so a logical ``table`` endpoint becomes
    ``entity`` while a physical ``table`` endpoint stays ``table``.  Idempotent —
    safe to apply to a payload that is already in the new model.
    """
    entities = entities or []
    relationships = relationships or []

    layer_by_name: Dict[str, Optional[str]] = {}
    for e in entities:
        if not isinstance(e, dict):
            continue
        props = e.get("properties") or {}
        layer_by_name[e.get("name")] = props.get("layer") if isinstance(props, dict) else None

    for e in entities:
        if not isinstance(e, dict):
            continue
        props = e.get("properties") or {}
        layer = props.get("layer") if isinstance(props, dict) else None
        e["entity_type"] = remap_entity_type(e.get("entity_type"), layer)

    # An endpoint the payload does not carry cannot be vouched for as logical. A
    # pre-rename export was always a WHOLE graph, so a name absent from the batch can
    # only come from a new-model PARTIAL import (e.g. the views-only metric repair,
    # whose `derives_from` targets are physical tables already in the store) — there
    # `table` means the physical table and renaming it to `entity` makes the edge
    # unresolvable. Only an endpoint present in the batch is layer-remapped.
    def _endpoint_type(etype, name):
        if name not in layer_by_name:
            # the physical rename is unambiguous (no logical node was ever physical_*)
            return _PHYSICAL_RENAME.get(etype, etype)
        return remap_entity_type(etype, layer_by_name.get(name))

    for r in relationships:
        if not isinstance(r, dict):
            continue
        if r.get("source_type") is not None:
            r["source_type"] = _endpoint_type(r["source_type"], r.get("source_name"))
        if r.get("target_type") is not None:
            r["target_type"] = _endpoint_type(r["target_type"], r.get("target_name"))

    normalize_edge_vocabulary(entities, relationships)
    return entities, relationships


# ---------------------------------------------------------------------------
# Edge vocabulary — ONE name for "entity has this attribute".
#
# ONTOLOGY_ARCHITECTURE §2.4 declares `contains` for entity→attribute and reserves
# `has_property` for entity→concept. Two of three constructors and every consumer
# (renderer TABLE SCHEMAS / JOINABLE COLUMNS, `_table_columns_map`, the extractor)
# read `contains`; the ANDP walker emitted `has_property` for the same fact, so on
# every ANDP-built graph the compact schema never rendered and 500-node
# enrichments of ~78K tokens went to the planner (measured, Aug 2026). Normalise at
# the two chokepoints — import payloads and the stored graph at startup — so the
# consumer side has exactly one edge to read.
# ---------------------------------------------------------------------------
ATTRIBUTE_EDGE = "contains"
_LEGACY_ATTRIBUTE_EDGES = ("has_property",)


def normalize_edge_vocabulary(
    entities: Optional[List[Dict[str, Any]]],
    relationships: Optional[List[Dict[str, Any]]],
) -> int:
    """Rewrite legacy entity→attribute edge names to :data:`ATTRIBUTE_EDGE` in place.

    Endpoint types come from the relationship row when present, else from the
    entity payload (by name). Only the entity→attribute pair is rewritten —
    `has_property` between an entity and a concept is left alone. Idempotent.
    Returns the number of edges rewritten.
    """
    entities = entities or []
    relationships = relationships or []
    type_by_name: Dict[str, str] = {}
    for e in entities:
        if isinstance(e, dict) and e.get("name"):
            type_by_name.setdefault(str(e["name"]).lower(), str(e.get("entity_type") or ""))
    n = 0
    for r in relationships:
        if not isinstance(r, dict) or r.get("relationship_type") not in _LEGACY_ATTRIBUTE_EDGES:
            continue
        st = r.get("source_type") or type_by_name.get(str(r.get("source_name") or "").lower())
        tt = r.get("target_type") or type_by_name.get(str(r.get("target_name") or "").lower())
        if st == "entity" and tt == "attribute":
            r["relationship_type"] = ATTRIBUTE_EDGE
            n += 1
    return n


def migrate_edge_vocabulary_sqlite(conn) -> int:
    """In-place SQLite normalisation of stored entity→attribute edges. Idempotent.

    A legacy edge whose pair already carries a canonical edge is deleted (the
    UNIQUE(kg_id, source, target, type) constraint would otherwise refuse the
    rename); every other one is renamed. Returns edges rewritten + removed.
    """
    legacy = ", ".join("?" for _ in _LEGACY_ATTRIBUTE_EDGES)
    base = f"""
        FROM kg_relationships r
        JOIN kg_entities s ON s.id = r.source_entity_id
        JOIN kg_entities t ON t.id = r.target_entity_id
        WHERE r.relationship_type IN ({legacy})
          AND s.entity_type = 'entity' AND t.entity_type = 'attribute'
    """
    cur = conn.cursor()
    dup_ids = [row[0] for row in cur.execute(
        "SELECT r.id " + base + """
          AND EXISTS (SELECT 1 FROM kg_relationships c
                      WHERE c.kg_id = r.kg_id AND c.source_entity_id = r.source_entity_id
                        AND c.target_entity_id = r.target_entity_id AND c.relationship_type = ?)
        """, (*_LEGACY_ATTRIBUTE_EDGES, ATTRIBUTE_EDGE)).fetchall()]
    removed = 0
    for i in range(0, len(dup_ids), 500):
        chunk = dup_ids[i:i + 500]
        cur.execute(f"DELETE FROM kg_relationships WHERE id IN ({','.join('?' for _ in chunk)})", chunk)
        removed += cur.rowcount
    ids = [row[0] for row in cur.execute("SELECT r.id " + base, _LEGACY_ATTRIBUTE_EDGES).fetchall()]
    renamed = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        cur.execute(f"UPDATE kg_relationships SET relationship_type = ? WHERE id IN ({','.join('?' for _ in chunk)})",
                    (ATTRIBUTE_EDGE, *chunk))
        renamed += cur.rowcount
    conn.commit()
    return renamed + removed


# JSON path used to read the per-entity layer out of properties_json (SQLite).
_LAYER_JSON = "json_extract(properties_json, '$.layer')"


def migrate_entity_types_sqlite(conn) -> int:
    """Entity-type rename + layer backfill + edge-vocabulary normalisation (idempotent)."""
    n = _migrate_entity_types_sqlite_inner(conn)
    try:
        n += migrate_edge_vocabulary_sqlite(conn)
    except Exception as exc:  # noqa: BLE001 — a missing table on a fresh DB is not a failure
        logger.debug(f"edge-vocabulary migration skipped: {exc}")
    return n


def _migrate_entity_types_sqlite_inner(conn) -> int:
    """In-place, idempotent rename of ``kg_entities.entity_type`` on a SQLite
    connection.  Relationship ``source_type``/``target_type`` are derived via JOIN
    from ``kg_entities.entity_type`` (not stored), so renaming entities is enough.

    Returns the number of rows updated (0 once migrated)."""
    try:
        conn.execute("SELECT entity_type FROM kg_entities LIMIT 1")
    except Exception:
        return 0  # kg_entities not present yet — nothing to migrate

    total = 0
    try:
        # 1) Logical first, guarded by layer so physical nodes are never caught
        #    (and re-running stays a no-op once physical nodes are named table/column).
        for old, new in (("column", "attribute"), ("table", "entity")):
            cur = conn.execute(
                f"UPDATE kg_entities SET entity_type=? "
                f"WHERE entity_type=? AND ({_LAYER_JSON} IS NULL OR {_LAYER_JSON}='logical')",
                (new, old),
            )
            total += cur.rowcount or 0
        # 2) Physical second (unambiguous names — no guard needed).
        for old, new in (("physical_column", "column"), ("physical_table", "table")):
            cur = conn.execute(
                "UPDATE kg_entities SET entity_type=? WHERE entity_type=?",
                (new, old),
            )
            total += cur.rowcount or 0
        # 3) Layer backfill — stamp properties.layer on nodes that predate the layer
        #    model (e.g. KGs from the pre-spec agent-discovery constructor). Only where
        #    absent, so it's idempotent and never overrides an explicit layer.
        total += _backfill_layer_sqlite(conn)
        conn.commit()
        if total:
            logger.info(f"[KG migration] entity-type rename + layer backfill: {total} rows updated")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[KG migration] entity-type rename skipped: {exc}")
    return total


def _backfill_layer_sqlite(conn) -> int:
    """Set ``properties.layer`` where absent: logical types → 'logical', physical
    types → 'physical'. Idempotent (only touches rows whose layer is NULL)."""
    updated = 0
    plan = [("logical", _LOGICAL_TYPES), ("physical", _PHYSICAL_TYPES)]
    for layer, types in plan:
        placeholders = ",".join("?" for _ in types)
        try:
            cur = conn.execute(
                f"UPDATE kg_entities "
                f"SET properties_json = json_set("
                f"    CASE WHEN properties_json IS NULL OR properties_json='' "
                f"         THEN '{{}}' ELSE properties_json END, '$.layer', ?) "
                f"WHERE entity_type IN ({placeholders}) "
                f"AND ({_LAYER_JSON} IS NULL)",
                (layer, *types),
            )
            updated += cur.rowcount or 0
        except Exception as exc:  # noqa: BLE001 — json1 unavailable / bad JSON: skip
            logger.debug(f"[KG migration] layer backfill ({layer}) skipped: {exc}")
    return updated


def migrate_entity_types_sqlite_path(db_path: str) -> int:
    """Open ``db_path`` and run :func:`migrate_entity_types_sqlite`."""
    import sqlite3
    try:
        conn = sqlite_util.connect(db_path)
    except Exception:
        return 0
    try:
        return migrate_entity_types_sqlite(conn)
    finally:
        conn.close()
