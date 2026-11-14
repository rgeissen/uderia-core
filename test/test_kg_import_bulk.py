"""
import_bulk: batched single-transaction correctness + performance.

The per-row import (add_entity/add_relationship each opening a fresh connection
and committing — one fsync per row + two name lookups per edge) made a KG
subscribe/import take minutes for a large graph.  This proves the batched
rewrite is (a) semantically identical (upsert, case-insensitive/type-optional
name resolution, idempotent re-import) and (b) fast (one commit).

CI-pure: real SQLiteKGBackend over a temp DB.  Run:
    python test/test_kg_import_bulk.py
"""

import os
import sqlite3
import sys
import tempfile
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCHEMA_SQL = os.path.join(ROOT, "schema", "21_knowledge_graph.sql")
KG_ID = "kg-imp"

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


def fresh_backend():
    from trusted_data_agent.auth.database import _migrate_to_multi_kg
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    with open(SCHEMA_SQL) as f:
        conn.executescript(f.read())
    conn.commit()
    _migrate_to_multi_kg(conn)
    conn.commit()
    conn.close()
    backend = SQLiteKGBackend(config={"db_path": tmp.name})
    backend.initialize()
    return backend, tmp.name


def sample():
    entities = [
        {"name": "Customer", "entity_type": "entity", "properties": {"pk": "id"}},
        {"name": "Order", "entity_type": "entity"},
        {"name": "id", "entity_type": "attribute"},
        {"name": "revenue", "entity_type": "metric",
         "properties": {"dimensions": [{"name": "region"}], "measures": [{"name": "amt"}]}},
    ]
    relationships = [
        # typed both ends
        {"source_name": "Order", "source_type": "entity",
         "target_name": "Customer", "target_type": "entity",
         # Re-pinned 2026-08-25: fixtures used "references", which the KG Phase C write
         # chokepoint (Aug 2026, battery-gated in test_kg_phase_c) rejects — the edge
         # vocabulary is CLOSED (kgstore/types.RELATIONSHIP_TYPES). "relates_to" is the
         # canonical generic edge; the rejection behaviour itself is Phase C's gate.
         "relationship_type": "relates_to"},
        # type-optional source resolution + CASE-INSENSITIVE name ("customer")
        {"source_name": "revenue", "target_name": "customer",
         "relationship_type": "derives_from"},
        # 'contains' from entity to its attribute
        {"source_name": "Customer", "source_type": "entity",
         "target_name": "id", "target_type": "attribute",
         "relationship_type": "contains"},
    ]
    return entities, relationships


def test_batched_import_correct():
    print("\n[test_batched_import_correct]")
    be, path = fresh_backend()
    try:
        ents, rels = sample()
        res = be.import_bulk(KG_ID, "p1", "u1", ents, rels)
        check("entities_added == 4", res["entities_added"] == 4)
        check("relationships_added == 3 (incl. type-optional + NOCASE)",
              res["relationships_added"] == 3)

        stored = be.list_entities(KG_ID, None, 1000)
        types = Counter(e["entity_type"] for e in stored)
        check("all 4 entities persisted", len(stored) == 4)
        check("metric type present", types.get("metric", 0) == 1)
        m = next(e for e in stored if e["entity_type"] == "metric")
        check("metric properties (dimensions/measures) intact",
              bool(m["properties"].get("dimensions")) and bool(m["properties"].get("measures")))

        rel_rows = be.list_relationships(KG_ID, None, None, 1000)
        check("all 3 relationships persisted", len(rel_rows) == 3)
    finally:
        os.unlink(path)


def test_unresolved_relationship_skipped():
    print("\n[test_unresolved_relationship_skipped]")
    be, path = fresh_backend()
    try:
        ents = [{"name": "A", "entity_type": "entity"}]
        rels = [{"source_name": "A", "target_name": "Ghost",
                 "relationship_type": "relates_to"}]
        res = be.import_bulk(KG_ID, "p1", "u1", ents, rels)
        check("entity added", res["entities_added"] == 1)
        check("dangling relationship skipped (not counted)", res["relationships_added"] == 0)
    finally:
        os.unlink(path)


def test_reimport_idempotent():
    print("\n[test_reimport_idempotent]")
    be, path = fresh_backend()
    try:
        ents, rels = sample()
        be.import_bulk(KG_ID, "p1", "u1", ents, rels)
        # Re-import the SAME payload — upsert must not create duplicates.
        res2 = be.import_bulk(KG_ID, "p1", "u1", ents, rels)
        stored = be.list_entities(KG_ID, None, 1000)
        rel_rows = be.list_relationships(KG_ID, None, None, 1000)
        # Re-pinned 2026-08-25: KG Phase C changed the counter contract — `*_added`
        # reports rows actually INSERTED, `*_updated` the upserts, precisely so an
        # idempotent re-import no longer claims it "added" everything (that misreport
        # is what the old expectation pinned). Submitted totals ride `*_submitted`.
        check("re-import inserts nothing (upsert path reports updates, not adds)",
              res2["entities_added"] == 0 and res2["entities_updated"] == 4
              and res2["relationships_added"] == 0 and res2["relationships_updated"] == 3
              and res2["entities_submitted"] == 4)
        check("no duplicate entities after re-import", len(stored) == 4)
        check("no duplicate relationships after re-import", len(rel_rows) == 3)
    finally:
        os.unlink(path)


def test_large_import_is_fast():
    print("\n[test_large_import_is_fast]")
    be, path = fresh_backend()
    try:
        N = 4000
        ents = [{"name": f"e_{i:05d}", "entity_type": "entity"} for i in range(N)]
        rels = [{"source_name": f"e_{i:05d}", "source_type": "entity",
                 "target_name": f"e_{(i+1) % N:05d}", "target_type": "entity",
                 "relationship_type": "relates_to"} for i in range(N)]
        t0 = time.monotonic()
        res = be.import_bulk(KG_ID, "p1", "u1", ents, rels)
        dt = time.monotonic() - t0
        check(f"imported {N} entities + {N} edges", res["entities_added"] == N and res["relationships_added"] == N)
        # Single-transaction batch: this is trivially fast.  A generous ceiling —
        # the old per-row path (N*2 fsyncs) would blow well past this.
        check(f"batched import completes quickly ({dt:.2f}s < 5s)", dt < 5.0)
    finally:
        os.unlink(path)


def main():
    for fn in [test_batched_import_correct, test_unresolved_relationship_skipped,
               test_reimport_idempotent, test_large_import_is_fast]:
        fn()
    print(f"\n{'='*50}\nimport_bulk: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
