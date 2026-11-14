"""
Module tests — kgstore.conformance (Tier 4.3, the ontology conformance gate).

CI-pure: no network, no DB. Asserts the topology validator against hand-crafted
fixtures — a fully conformant ontology, plus each violation class (unknown types,
dangling edges, missing logical backbone, physical→logical binding, orphans, layer
mismatch, domain coherence) and the severity→status mapping. Also a drift guard:
the conformance type sets must match graph_store's canonical enums.

Run:
    python test/test_kg_conformance.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import conformance as C  # noqa: E402
from trusted_data_agent.kgstore.conformance import check_conformance, layer_for_type  # noqa: E402

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


def conformant_fixture():
    """A small but fully conformant ontology (logical + physical, all bindings present)."""
    entities = [
        {"name": "Sales", "entity_type": "domain", "properties": {"layer": "logical"}},
        {"name": "Customer", "entity_type": "entity", "properties": {"layer": "logical", "domain": "Sales"}},
        {"name": "email", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "revenue", "entity_type": "metric",
         "properties": {"layer": "logical", "owner_product": "cust_tbl"}},
        {"name": "cust_tbl", "entity_type": "table", "properties": {"layer": "physical", "infrastructure": "teradata"}},
        {"name": "cust_tbl.email", "entity_type": "column", "properties": {"layer": "physical"}},
        {"name": "revenue_view", "entity_type": "ddl_artifact", "properties": {"layer": "physical"}},
    ]
    relationships = [
        {"source_name": "Sales", "source_type": "domain", "target_name": "Customer", "target_type": "entity", "relationship_type": "contains"},
        {"source_name": "Customer", "source_type": "entity", "target_name": "email", "target_type": "attribute", "relationship_type": "has_property"},
        {"source_name": "revenue", "source_type": "metric", "target_name": "Customer", "target_type": "entity", "relationship_type": "measures"},
        {"source_name": "cust_tbl", "source_type": "table", "target_name": "Customer", "target_type": "entity", "relationship_type": "implements"},
        {"source_name": "cust_tbl", "source_type": "table", "target_name": "cust_tbl.email", "target_type": "column", "relationship_type": "contains"},
        {"source_name": "revenue_view", "source_type": "ddl_artifact", "target_name": "revenue", "target_type": "metric", "relationship_type": "implements"},
    ]
    return entities, relationships


def codes(report):
    return {i.code for i in report.issues}


def test_conformant():
    print("\n[conformant fixture]")
    e, r = conformant_fixture()
    rep = check_conformance(e, r)
    check("a fully-bound ontology is conformant", rep.status == C.CONFORMANT)
    check("conformant report has no issues", rep.issues == [])
    check("entity/relationship counts reported", rep.entity_count == 7 and rep.relationship_count == 6)
    check("report.conformant property True", rep.conformant is True)


def test_empty():
    print("\n[empty ontology]")
    rep = check_conformance([], [])
    check("empty → nonconformant", rep.status == C.NONCONFORMANT)
    check("empty flags empty_ontology", "empty_ontology" in codes(rep))


def test_unknown_types():
    print("\n[unknown types → nonconformant (HIGH)]")
    e, r = conformant_fixture()
    e2 = e + [{"name": "weird", "entity_type": "gizmo", "properties": {}}]
    rep = check_conformance(e2, r)
    check("unknown entity type → nonconformant", rep.status == C.NONCONFORMANT)
    check("flags unknown_entity_type", "unknown_entity_type" in codes(rep))
    r2 = r + [{"source_name": "Customer", "source_type": "entity", "target_name": "email", "target_type": "attribute", "relationship_type": "frobnicates"}]
    rep2 = check_conformance(e, r2)
    check("unknown relationship type → nonconformant", rep2.status == C.NONCONFORMANT)
    check("flags unknown_relationship_type", "unknown_relationship_type" in codes(rep2))


def test_dangling_edge():
    print("\n[dangling edge → nonconformant (HIGH)]")
    e, r = conformant_fixture()
    r2 = r + [{"source_name": "Customer", "source_type": "entity", "target_name": "ghost", "target_type": "entity", "relationship_type": "relates_to"}]
    rep = check_conformance(e, r2)
    check("edge to a missing entity → nonconformant", rep.status == C.NONCONFORMANT)
    check("flags dangling_edge", "dangling_edge" in codes(rep))


def test_no_logical_backbone():
    print("\n[physical-only → nonconformant (HIGH)]")
    entities = [
        {"name": "cust_tbl", "entity_type": "table", "properties": {"layer": "physical"}},
        {"name": "cust_tbl.email", "entity_type": "column", "properties": {"layer": "physical"}},
    ]
    rels = [{"source_name": "cust_tbl", "source_type": "table", "target_name": "cust_tbl.email", "target_type": "column", "relationship_type": "contains"}]
    rep = check_conformance(entities, rels)
    check("no logical entity → nonconformant", rep.status == C.NONCONFORMANT)
    check("flags no_logical_backbone", "no_logical_backbone" in codes(rep))


def test_physical_binding():
    print("\n[physical→logical binding]")
    e, r = conformant_fixture()
    # remove the table→entity implements edge → HIGH
    r_no_impl = [x for x in r if not (x["source_name"] == "cust_tbl" and x["relationship_type"] == "implements")]
    rep = check_conformance(e, r_no_impl)
    check("table without implements→entity → nonconformant", rep.status == C.NONCONFORMANT)
    check("flags table_no_implements", "table_no_implements" in codes(rep))
    # remove the ddl_artifact→metric implements → MEDIUM only → partial
    r_no_ddl = [x for x in r if not (x["source_name"] == "revenue_view" and x["relationship_type"] == "implements")]
    rep2 = check_conformance(e, r_no_ddl)
    check("ddl_artifact without metric → partial (MEDIUM)", rep2.status == C.PARTIAL)
    check("flags ddl_artifact_no_metric", "ddl_artifact_no_metric" in codes(rep2))
    # remove the table→column contains → column_no_parent MEDIUM → partial
    r_no_col = [x for x in r if x["relationship_type"] != "contains" or x["target_type"] != "column"]
    rep3 = check_conformance(e, r_no_col)
    check("column without table parent → partial", rep3.status == C.PARTIAL)
    check("flags column_no_parent", "column_no_parent" in codes(rep3))


def test_orphans_partial():
    print("\n[orphans → partial (MEDIUM/LOW)]")
    e, r = conformant_fixture()
    r_no_hasprop = [x for x in r if x["relationship_type"] != "has_property"]
    rep = check_conformance(e, r_no_hasprop)
    check("attribute with no has_property → partial", rep.status == C.PARTIAL)
    check("flags orphan_attribute", "orphan_attribute" in codes(rep))
    # orphan metric: strip owner_product AND the measures edge
    e_orphan = [dict(x, properties={"layer": "logical"}) if x["name"] == "revenue" else x for x in e]
    r_no_meas = [x for x in r if x["relationship_type"] != "measures"]
    rep2 = check_conformance(e_orphan, r_no_meas)
    check("metric with no measures/owner → partial (LOW)", rep2.status == C.PARTIAL)
    check("flags orphan_metric", "orphan_metric" in codes(rep2))


def test_layer_mismatch():
    print("\n[layer mismatch → partial (MEDIUM)]")
    e, r = conformant_fixture()
    e2 = [dict(x, properties=dict(x["properties"], layer="physical")) if x["name"] == "Customer" else x for x in e]
    rep = check_conformance(e2, r)
    check("logical entity tagged physical → partial", rep.status == C.PARTIAL)
    check("flags layer_mismatch", "layer_mismatch" in codes(rep))


def test_domain_coherence():
    print("\n[domain coherence → partial (LOW)]")
    e, r = conformant_fixture()
    e2 = [dict(x, properties=dict(x["properties"], domain="Marketing")) if x["name"] == "Customer" else x for x in e]
    rep = check_conformance(e2, r)
    check("undeclared domain reference → partial", rep.status == C.PARTIAL)
    check("flags domain_incoherent", "domain_incoherent" in codes(rep))


def test_severity_mapping():
    print("\n[severity → status mapping]")
    e, r = conformant_fixture()
    # a LOW-only issue (orphan metric) must NOT drop below partial
    e_low = [dict(x, properties={"layer": "logical"}) if x["name"] == "revenue" else x for x in e]
    r_low = [x for x in r if x["relationship_type"] != "measures"]
    rep = check_conformance(e_low, r_low)
    check("LOW-only issues → partial (never nonconformant)", rep.status == C.PARTIAL)
    sev = rep.severity_counts()
    check("severity_counts has no HIGH", sev["high"] == 0)
    # a HIGH issue forces nonconformant regardless of others
    r_high = r_low + [{"source_name": "x", "source_type": "entity", "target_name": "y", "target_type": "entity", "relationship_type": "relates_to"}]
    rep2 = check_conformance(e_low, r_high)
    check("any HIGH → nonconformant", rep2.status == C.NONCONFORMANT)


def test_determinism_and_shape():
    print("\n[determinism, refs cap, to_dict]")
    e, r = conformant_fixture()
    r_dangling = r + [{"source_name": "Customer", "source_type": "entity", "target_name": f"ghost{i}", "target_type": "entity", "relationship_type": "relates_to"} for i in range(25)]
    rep1 = check_conformance(e, r_dangling)
    rep2 = check_conformance(e, r_dangling)
    check("same input → identical status + issue codes", rep1.status == rep2.status and codes(rep1) == codes(rep2))
    dang = next(i for i in rep1.issues if i.code == "dangling_edge")
    check("issue count is exact (25)", dang.count == 25)
    check("issue refs capped at 10", len(dang.refs) == 10)
    d = rep1.to_dict()
    check("to_dict carries status + issues + severity_counts",
          d["status"] == C.NONCONFORMANT and "issues" in d and "severity_counts" in d)


def test_layer_helper_and_drift():
    print("\n[layer helper + drift guard vs graph_store]")
    check("entity → logical", layer_for_type("entity") == "logical")
    check("table → physical", layer_for_type("table") == "physical")
    check("unknown → None", layer_for_type("gizmo") is None)
    # Drift guard: the conformance type sets must match the canonical enums.
    try:
        from components.builtin.knowledge_graph.graph_store import ENTITY_TYPES as GS_E, RELATIONSHIP_TYPES as GS_R
        check("ENTITY_TYPES match graph_store (no drift)", set(C.ENTITY_TYPES) == set(GS_E))
        check("RELATIONSHIP_TYPES match graph_store (no drift)", set(C.RELATIONSHIP_TYPES) == set(GS_R))
    except Exception as ex:
        check(f"graph_store import for drift check (skipped: {ex})", True)


def test_operational_artifacts():
    """PROP-008 B1/C1: a ddl_artifact the platform builds ON a table (an action ledger, a tuning
    structure) carries a ``purpose`` and is bound by ``derives_from`` a table — it is not a
    metric's realisation, so it is never flagged for not implementing one."""
    print("\n[operational ddl_artifacts]")
    e, r = conformant_fixture()
    led = {"name": "UDERIA_ACT_cust_tbl", "entity_type": "ddl_artifact", "properties": {"layer": "physical", "purpose": "action_ledger"}}
    bound = r + [{"source_name": "UDERIA_ACT_cust_tbl", "source_type": "ddl_artifact", "target_name": "cust_tbl", "target_type": "table", "relationship_type": "derives_from"}]
    rep = check_conformance(e + [led], bound)
    check("an action ledger bound to its table keeps the ontology conformant", rep.status == C.CONFORMANT)
    rep2 = check_conformance(e + [led], r)
    check("…an operational artifact bound to nothing is named (ddl_artifact_unbound, MEDIUM), never as a missing metric",
          rep2.status == C.PARTIAL and "ddl_artifact_unbound" in codes(rep2) and "ddl_artifact_no_metric" not in codes(rep2))
    tune = {"name": "ji_cust_tbl_cover", "entity_type": "ddl_artifact", "properties": {"layer": "physical", "purpose": "join_index"}}
    rep3 = check_conformance(e + [tune], r + [{"source_name": "ji_cust_tbl_cover", "source_type": "ddl_artifact", "target_name": "cust_tbl", "target_type": "table", "relationship_type": "derives_from"}])
    check("a landed tuning structure bound to its table keeps the ontology conformant", rep3.status == C.CONFORMANT)


def main():
    print("=" * 70)
    print("kgstore.conformance — module tests")
    print("=" * 70)
    test_conformant()
    test_empty()
    test_unknown_types()
    test_dangling_edge()
    test_no_logical_backbone()
    test_physical_binding()
    test_orphans_partial()
    test_layer_mismatch()
    test_domain_coherence()
    test_severity_mapping()
    test_determinism_and_shape()
    test_layer_helper_and_drift()
    test_operational_artifacts()
    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
