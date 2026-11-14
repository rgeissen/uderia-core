"""
Regression: KG marketplace publish / RDF export must snapshot the WHOLE graph.

Root cause of "subscribed KG is missing metrics": the export sites fetched with a
fixed limit (publish used 10000, default list_relationships used 200) and
``list_entities`` is ``ORDER BY entity_type`` alphabetically.  Once a large model
exceeds the cap, the alphabetically-last types (``metric``, ``schema``, ``table``,
``taxonomy``) are the ones truncated — so metrics silently vanish from the export,
and the subscriber faithfully materializes the truncated data.

The fix: ``GraphStore.export_graph()`` sizes the fetch to the live counts so no
graph size can truncate.  This test proves (a) the truncation mechanism is real,
and (b) export_graph recovers every type.

CI-pure: real SQLiteKGBackend over a temp DB.  Run:
    python test/test_kg_export_completeness.py
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (components/)

from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend  # noqa: E402
from components.builtin.knowledge_graph.graph_store import GraphStore  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCHEMA_SQL = os.path.join(ROOT, "schema", "21_knowledge_graph.sql")

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


KG_ID = "kg-export"


def build_backend():
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

    # A graph large enough that a fixed cap truncates, with the alphabetically-last
    # types being the FEW that get cut:  ~600 'attribute' + 600 'column' (sort first)
    # so a cap of 1000 drops everything after — including the handful of 'metric'
    # and 'table' nodes.  Mirrors an industry model's shape (many attrs/cols, few
    # metrics) so the truncation lands exactly on metrics.
    entities = []
    for i in range(600):
        entities.append({"name": f"attr_{i:04d}", "entity_type": "attribute"})
    for i in range(600):
        entities.append({"name": f"col_{i:04d}", "entity_type": "column"})
    for i in range(20):
        entities.append({"name": f"tbl_{i:02d}", "entity_type": "table"})
    METRICS = [f"metric_{i:02d}" for i in range(8)]
    for m in METRICS:
        entities.append({"name": m, "entity_type": "metric",
                         "properties": {"dimensions": [{"name": "d"}], "measures": [{"name": "m"}]}})
    # A relationship count that exceeds the old default-200 truncation.
    relationships = [
        {"source_name": f"col_{i:04d}", "source_type": "column",
         "target_name": f"attr_{i:04d}", "target_type": "attribute",
         "relationship_type": "implements"}
        for i in range(600)
    ]
    backend.import_bulk(kg_id=KG_ID, profile_id="p1", user_uuid="u1",
                        entities=entities, relationships=relationships)
    return backend, tmp.name, len(entities), len(relationships), METRICS


class _StoreStub:
    """Minimal shim exposing the three async methods export_graph() relies on,
    delegating to a real backend — so we exercise the actual GraphStore.export_graph
    logic without the profile→backend resolution chain."""
    def __init__(self, backend):
        self._b = backend
        self.kg_id = KG_ID

    async def get_stats(self):
        return self._b.get_stats(KG_ID)

    async def list_entities(self, entity_type=None, limit=200):
        return self._b.list_entities(KG_ID, entity_type, limit)

    async def list_relationships(self, entity_id=None, relationship_type=None, limit=200):
        return self._b.list_relationships(KG_ID, entity_id, relationship_type, limit)
# Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
# a double unfaithful in the dimension under test measures nothing (Sep 2026).
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from kg_double_fidelity import double_mismatches as _dm
from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
check("_StoreStub double matches the real GraphStore calling convention", not _dm(_RealGS, _StoreStub))


def test_fixed_limit_truncates_metrics():
    print("\n[test_fixed_limit_truncates_metrics]  (reproduces the reported bug)")
    backend, path, total_ent, total_rel, metrics = build_backend()
    try:
        # The old publish path fetched a fixed slice ordered by entity_type.
        truncated = backend.list_entities(KG_ID, None, 1000)  # < total_ent
        types = Counter(e["entity_type"] for e in truncated)
        check("fixed limit returns fewer than total", len(truncated) < total_ent)
        check("BUG: 'metric' entities are dropped by the alphabetical truncation",
              types.get("metric", 0) == 0)
        check("BUG: 'table' entities are dropped too", types.get("table", 0) == 0)
        # Old default relationships limit (200) also truncated.
        rel_trunc = backend.list_relationships(KG_ID, None, None, 200)
        check("BUG: default-200 relationship limit truncates edges",
              len(rel_trunc) == 200 and total_rel > 200)
    finally:
        os.unlink(path)


def test_export_graph_is_complete():
    print("\n[test_export_graph_is_complete]  (the fix)")
    backend, path, total_ent, total_rel, metrics = build_backend()
    try:
        stub = _StoreStub(backend)
        entities, relationships = asyncio.run(GraphStore.export_graph(stub))
        check("export_graph returns ALL entities", len(entities) == total_ent)
        check("export_graph returns ALL relationships", len(relationships) == total_rel)
        names = {e["name"] for e in entities}
        check("every metric present in export", all(m in names for m in metrics))
        types = Counter(e["entity_type"] for e in entities)
        check("metric type count intact (8)", types.get("metric", 0) == 8)
        check("table type count intact (20)", types.get("table", 0) == 20)
        # metric definitions (dimensions/measures) survive in properties
        a_metric = next(e for e in entities if e["entity_type"] == "metric")
        props = a_metric.get("properties") or {}
        check("metric definition (dimensions/measures) carried in properties",
              bool(props.get("dimensions")) and bool(props.get("measures")))
    finally:
        os.unlink(path)


def main():
    for fn in [test_fixed_limit_truncates_metrics, test_export_graph_is_complete]:
        fn()
    print(f"\n{'='*50}\nKG export completeness: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
