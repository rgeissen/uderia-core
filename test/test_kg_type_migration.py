"""
CI-pure tests for the KG entity-type rename migration (entity/attribute logical
vs table/column physical).

Covers the three application surfaces of one shared mapping
(``trusted_data_agent.kgstore.migrations``):

* ``remap_entity_type`` — layer-aware, idempotent single-value map.
* ``migrate_kg_payload_types`` — import-payload remap (old marketplace / agent-pack
  exports → new model), incl. relationship endpoint types.
* ``migrate_entity_types_sqlite`` — in-place SQLite rename with the logical-first
  + layer-guarded ordering that makes a re-run a clean no-op.

Run:  python test/test_kg_type_migration.py
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.kgstore.migrations import (  # noqa: E402
    remap_entity_type,
    migrate_kg_payload_types,
    migrate_entity_types_sqlite,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


# ── 1. remap_entity_type — layer-aware + idempotent ──
def test_remap_unit():
    print("\n[1] remap_entity_type")
    # old → new
    check("logical table → entity", remap_entity_type("table", "logical") == "entity")
    check("logical column → attribute", remap_entity_type("column", "logical") == "attribute")
    check("discovery (no layer) table → entity", remap_entity_type("table", None) == "entity")
    check("discovery (no layer) column → attribute", remap_entity_type("column", None) == "attribute")
    check("physical_table → table", remap_entity_type("physical_table", "physical") == "table")
    check("physical_column → column", remap_entity_type("physical_column", "physical") == "column")
    # untouched
    check("database unchanged", remap_entity_type("database", None) == "database")
    check("metric unchanged", remap_entity_type("metric", "logical") == "metric")
    # idempotent (new model values are stable)
    check("entity stays entity", remap_entity_type("entity", "logical") == "entity")
    check("attribute stays attribute", remap_entity_type("attribute", None) == "attribute")
    check("physical table NOT re-logicalised", remap_entity_type("table", "physical") == "table")
    check("physical column NOT re-logicalised", remap_entity_type("column", "physical") == "column")


# ── 2. payload remap (old export → new), idempotent ──
def test_payload_remap():
    print("\n[2] migrate_kg_payload_types")
    ents = [
        {"name": "sales.order", "entity_type": "table", "properties": {"layer": "logical"}},
        {"name": "sales.order.id", "entity_type": "column", "properties": {"layer": "logical"}},
        {"name": "disc.tbl", "entity_type": "table", "properties": {}},          # discovery, no layer
        {"name": "wh.order", "entity_type": "physical_table", "properties": {"layer": "physical"}},
        {"name": "wh.order.id", "entity_type": "physical_column", "properties": {"layer": "physical"}},
    ]
    rels = [
        {"source_name": "wh.order", "source_type": "physical_table",
         "target_name": "sales.order", "target_type": "table", "relationship_type": "implements"},
        {"source_name": "sales.order", "source_type": "table",
         "target_name": "sales.order.id", "target_type": "column", "relationship_type": "contains"},
    ]
    migrate_kg_payload_types(ents, rels)
    got = {e["name"]: e["entity_type"] for e in ents}
    check("logical table → entity", got["sales.order"] == "entity")
    check("logical column → attribute", got["sales.order.id"] == "attribute")
    check("discovery table → entity", got["disc.tbl"] == "entity")
    check("physical_table → table", got["wh.order"] == "table")
    check("physical_column → column", got["wh.order.id"] == "column")
    # relationship endpoints: physical implements logical
    check("implements src physical_table → table", rels[0]["source_type"] == "table")
    check("implements tgt logical table → entity", rels[0]["target_type"] == "entity")
    check("contains src logical → entity", rels[1]["source_type"] == "entity")
    check("contains tgt logical → attribute", rels[1]["target_type"] == "attribute")
    # idempotent re-apply
    migrate_kg_payload_types(ents, rels)
    check("re-apply is a no-op", {e["name"]: e["entity_type"] for e in ents} == got)

    # a PARTIAL new-model batch (the views-only metric repair): the edge's target is a
    # physical table already in the store, NOT in the payload — a pre-rename export was
    # always a whole graph, so an absent endpoint is never renamed to `entity`
    # (that made every repaired view's derives_from edge unresolvable, Sep 2026).
    part_e = [{"name": "wh.v_orders", "entity_type": "ddl_artifact", "properties": {"layer": "physical"}}]
    part_r = [{"source_name": "wh.v_orders", "source_type": "ddl_artifact",
               "target_name": "wh.order", "target_type": "table", "relationship_type": "derives_from"},
              {"source_name": "wh.v_orders", "source_type": "ddl_artifact",
               "target_name": "sales.order_metrics", "target_type": "metric", "relationship_type": "implements"}]
    migrate_kg_payload_types(part_e, part_r)
    check("absent physical endpoint keeps its declared type (table stays table)", part_r[0]["target_type"] == "table")
    check("absent metric endpoint untouched", part_r[1]["target_type"] == "metric")
    # …while an endpoint PRESENT in the batch as logical is still renamed
    mixed_e = [{"name": "sales.order", "entity_type": "table", "properties": {"layer": "logical"}}]
    mixed_r = [{"source_name": "wh.order", "source_type": "physical_table",
                "target_name": "sales.order", "target_type": "table", "relationship_type": "implements"}]
    migrate_kg_payload_types(mixed_e, mixed_r)
    check("present logical endpoint still renamed, absent physical source unconditionally mapped",
          mixed_r[0]["target_type"] == "entity" and mixed_r[0]["source_type"] == "table")


# ── 3. in-place SQLite migration, idempotent ──
def test_sqlite_migration():
    print("\n[3] migrate_entity_types_sqlite")
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE kg_entities (id INTEGER PRIMARY KEY, entity_type TEXT, properties_json TEXT)")
    rows = [
        ("table", json.dumps({"layer": "logical"})),
        ("column", json.dumps({})),                          # discovery — no layer
        ("physical_table", json.dumps({"layer": "physical"})),
        ("physical_column", json.dumps({"layer": "physical"})),
        ("database", json.dumps({})),
        ("metric", json.dumps({"layer": "logical"})),
    ]
    c.executemany("INSERT INTO kg_entities (entity_type, properties_json) VALUES (?,?)", rows)
    c.commit()

    n1 = migrate_entity_types_sqlite(c)
    after = sorted(r[0] for r in c.execute("SELECT entity_type FROM kg_entities"))
    # 4 type renames + 2 layer backfills (attribute + database had no layer)
    check("6 rows touched on first run (4 rename + 2 layer backfill)", n1 == 6)
    check("types correct after migration",
          after == sorted(["entity", "attribute", "table", "column", "database", "metric"]))

    # layer backfill: the no-layer discovery node ("column"→"attribute") gets layer=logical
    import json as _json
    layers = {}
    for et, pj in c.execute("SELECT entity_type, properties_json FROM kg_entities"):
        layers[et] = (_json.loads(pj or "{}") or {}).get("layer")
    check("discovery attribute backfilled layer=logical", layers["attribute"] == "logical")
    check("logical entity backfilled/kept layer=logical", layers["entity"] == "logical")
    check("database backfilled layer=logical", layers["database"] == "logical")
    check("physical table keeps layer=physical", layers["table"] == "physical")

    n2 = migrate_entity_types_sqlite(c)
    after2 = sorted(r[0] for r in c.execute("SELECT entity_type FROM kg_entities"))
    check("second run is a no-op (idempotent)", n2 == 0)
    check("types unchanged on second run", after2 == after)
    c.close()


def main():
    for fn in [test_remap_unit, test_payload_remap, test_sqlite_migration]:
        fn()
    print(f"\n{'='*50}\nKG type migration: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
