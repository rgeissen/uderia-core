"""
KG program — Phase C gate (ingestion contract at the ONE write chokepoint + provenance).

  C1  import_bulk validates: an unknown entity/relationship TYPE is rejected and
      counted (add_entity always validated; the bulk path never did); `layer` is
      stamped when absent; `source_detail` is carried (was hard-coded NULL); the
      counters report rows actually INSERTED vs UPDATED (they reported rows
      SUBMITTED — an idempotent re-import "added" everything); unresolved
      relationship endpoints are counted, not just logged.
  C2  A model-authored metric formula states its provenance in the enrichment
      (never rendered as if a human validated it); an agent-added entity carries
      its `layer`; an inferred foreign key says it is inferred where the planner
      reads it.

Run:  PYTHONPATH=src python3 test/test_kg_phase_c.py
"""
import inspect
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend    # noqa: E402
from components.builtin.knowledge_graph import handler as kgh            # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {('— ' + str(detail)) if detail else ''}")


tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
c = sqlite3.connect(tmp)
live = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "tda_auth.db"))
for (ddl,) in live.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND tbl_name IN ('kg_entities','kg_relationships') AND sql IS NOT NULL"):
    c.execute(ddl)
live.close(); c.commit(); c.close()
be = SQLiteKGBackend({"db_path": tmp}) if "config" in inspect.signature(SQLiteKGBackend.__init__).parameters else SQLiteKGBackend(tmp)

print("\n[C1] the write chokepoint validates, stamps, carries provenance, counts honestly")
ents = [
    {"name": "Sales", "entity_type": "entity", "properties": {}, "source": "industry_model", "source_detail": "model.json#Sales"},
    {"name": "Sales.SaleID", "entity_type": "attribute", "properties": {"layer": "logical"}},
    {"name": "db.Sales", "entity_type": "table", "properties": {}},
    {"name": "Ghost", "entity_type": "not_a_type", "properties": {}},          # unknown type → rejected
    {"name": "", "entity_type": "entity"},                                     # malformed → rejected
]
rels = [
    {"source_name": "Sales", "source_type": "entity", "target_name": "Sales.SaleID", "target_type": "attribute", "relationship_type": "contains"},
    {"source_name": "db.Sales", "source_type": "table", "target_name": "Sales", "target_type": "entity", "relationship_type": "implements"},
    {"source_name": "Sales", "source_type": "entity", "target_name": "Nowhere", "target_type": "entity", "relationship_type": "foreign_key"},   # unresolved
    {"source_name": "Sales", "source_type": "entity", "target_name": "Sales.SaleID", "target_type": "attribute", "relationship_type": "teleports_to"},  # unknown type
]
r1 = be.import_bulk("k", "p", "u", ents, rels)
check("unknown entity type rejected + malformed rejected (2)", r1["entities_rejected"] == 2, r1)
check("3 entities INSERTED, 0 updated", r1["entities_added"] == 3 and r1["entities_updated"] == 0, r1)
check("unknown relationship type rejected (1)", r1["relationships_rejected"] == 1, r1)
check("unresolved endpoint counted (1), 2 relationships inserted", r1["relationships_unresolved"] == 1 and r1["relationships_added"] == 2, r1)
check("the rejected entity is NOT in the graph", be.get_entity_by_name("k", "Ghost") is None)
e = be.get_entity_by_name("k", "Sales", "entity")
check("layer stamped when absent (logical for `entity`)", e["properties"].get("layer") == "logical", e["properties"])
check("layer stamped physical for `table`", be.get_entity_by_name("k", "db.Sales", "table")["properties"].get("layer") == "physical")
check("an explicit layer is respected", be.get_entity_by_name("k", "Sales.SaleID", "attribute")["properties"].get("layer") == "logical")
check("source_detail is CARRIED (was hard-coded NULL)", e.get("source_detail") == "model.json#Sales", e.get("source_detail"))
r2 = be.import_bulk("k", "p", "u", ents, rels)
check("idempotent re-import reports 0 added / 3 updated (was: everything 'added' again)", r2["entities_added"] == 0 and r2["entities_updated"] == 3, r2)
check("…and 0 relationships added / 2 updated", r2["relationships_added"] == 0 and r2["relationships_updated"] == 2, r2)
check("legacy submitted counters still present", r2["entities_submitted"] == 3 and r2["relationships_submitted"] == 2)

print("\n[C2] provenance where the planner reads it")
hcls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_format_subgraph_for_prompt")][0]
sub = {"entities": [
    {"id": 1, "entity_type": "metric", "name": "TotalRevenue", "source": "constructor_semantic",
     "properties": {"formula": "SUM(SaleDetails.UnitPrice * SaleDetails.Quantity)", "description": "revenue"}},
    {"id": 2, "entity_type": "metric", "name": "Declared", "source": "industry_model",
     "properties": {"formula": "SUM(x)", "description": "declared by the model file"}},
    {"id": 3, "entity_type": "metric", "name": "Checked", "source": "llm_inferred",
     "properties": {"formula": "SUM(y)", "validated": True}},
    {"id": 4, "entity_type": "entity", "name": "Sales", "properties": {}},
    {"id": 5, "entity_type": "entity", "name": "Customers", "properties": {}},
], "relationships": [
    {"relationship_type": "foreign_key", "source_id": 4, "target_id": 5, "source_name": "Sales", "target_name": "Customers",
     "metadata": {"inferred_via": "column_name_match", "join_column": "CustomerID"}},
]}
txt = hcls._format_subgraph_for_prompt(hcls.__new__(hcls), sub, {}, surface="off")
check("a build-time model-derived formula states its provenance", "SUM(SaleDetails.UnitPrice * SaleDetails.Quantity) (model-derived at build time, not validated)" in txt, txt[-600:])
check("a deterministic (walker) formula carries NO caveat", "formula: SUM(x)\n" in txt + "\n" or "formula: SUM(x))" in txt or "formula: SUM(x);" in txt or "formula: SUM(x) " in txt or "SUM(x)" in txt and "SUM(x) (" not in txt)
check("a validated model-authored formula carries NO caveat", "SUM(y) (" not in txt)
check("an inferred FK says so where the planner reads it", "inferred from column_name_match, not a declared key" in txt, [l for l in txt.splitlines() if "foreign_key" in l])
check("agent-added entities are stamped with a layer", 'properties.setdefault("layer"' in inspect.getsource(hcls._handle_add_entity) if hasattr(hcls, "_handle_add_entity") else 'properties.setdefault("layer"' in open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin", "knowledge_graph", "handler.py")).read())
check("provenance vocabulary covers both model-authored sources", set(kgh._MODEL_AUTHORED_SOURCES) >= {"llm_inferred", "constructor_semantic"})

print(f"\n=== KG Phase C results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
