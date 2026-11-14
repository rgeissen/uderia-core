"""
Module tests — semantic_interchange (Tier 4.1, open-standards export/import).

CI-pure: no network, no DB, no server. Asserts the KG→RDF type mapping
(SKOS/OWL/QB/DCAT/PROV), the uderia: round-trip annotation layer, the lossless
full round-trip (export → serialize → parse → reconstruct) across all four
serializations, graph isomorphism, metric ↔ RDF Data Cube, the best-effort
foreign-graph fallback (with honest warnings), vocab-layer filtering,
determinism, and input non-mutation.

Run:
    python test/test_semantic_interchange.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rdflib import Graph, Literal  # noqa: E402
from rdflib.namespace import OWL, PROV, RDF, RDFS, SKOS  # noqa: E402

from trusted_data_agent.semantic_interchange import (  # noqa: E402
    convert_kg,
    convert_metric,
    parse_kg,
    parse_metric,
    parse,
    round_trip,
    serialize,
)
from trusted_data_agent.semantic_interchange import serializers  # noqa: E402
from trusted_data_agent.semantic_interchange.vocab import (  # noqa: E402
    DCAT,
    ENTITY_RDF_TYPE,
    QB,
    REL_PREDICATE,
    UDERIA,
    entity_uri,
    slug,
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


def sample_export():
    """A fixture exercising every entity type and every relationship type."""
    return {
        "kg_id": "demo_kg",
        "kg_name": "Demo KG",
        "kg_description": "interchange fixture",
        "entities": [
            {"name": "Sales", "entity_type": "domain", "properties": {"description": "selling"}, "source": "manual"},
            {"name": "Revenue", "entity_type": "business_concept", "properties": {"description": "money in"}, "source": "manual"},
            {"name": "RegionTaxonomy", "entity_type": "taxonomy", "properties": {}, "source": "manual"},
            {"name": "Customer", "entity_type": "entity",
             "properties": {"layer": "logical", "domain": "Sales", "description": "a buyer"}, "source": "manual"},
            {"name": "email", "entity_type": "attribute",
             "properties": {"layer": "logical", "semantic_type": "email"}, "source": "manual"},
            {"name": "fk_cust_order", "entity_type": "foreign_key", "properties": {"layer": "logical"}, "source": "manual"},
            {"name": "order_summary", "entity_type": "metric",
             "properties": {"metric_source": "cat.order.order_header",
                            "owner_domain": "order", "owner_product": "order_header",
                            "dimensions": [{"name": "order_date", "expr": "order_date"}],
                            "measures": [{"name": "total", "expr": "SUM(amount)", "additive": True},
                                         {"name": "avg_amt", "expr": "AVG(amount)", "additive": False}]},
             "source": "manual"},
            {"name": "warehouse", "entity_type": "database", "properties": {"layer": "physical"}, "source": "manual"},
            {"name": "main_cat", "entity_type": "catalog", "properties": {"layer": "physical", "infrastructure": "teradata"}, "source": "manual"},
            {"name": "order", "entity_type": "schema", "properties": {"layer": "physical", "infrastructure": "teradata"}, "source": "manual"},
            {"name": "order.order_header", "entity_type": "table",
             "properties": {"layer": "physical", "infrastructure": "teradata"}, "source": "manual"},
            {"name": "order.order_header.amount", "entity_type": "column",
             "properties": {"layer": "physical", "datatype": "DECIMAL(15,2)"}, "source": "manual"},
            {"name": "order.order_summary", "entity_type": "ddl_artifact",
             "properties": {"layer": "physical", "artifact_kind": "metric_view"}, "source": "manual"},
        ],
        "relationships": [
            {"source_name": "Sales", "source_type": "domain", "target_name": "Customer", "target_type": "entity",
             "relationship_type": "contains", "cardinality": "1:N", "metadata": {"evidence": "x"}, "source": "manual"},
            {"source_name": "Customer", "source_type": "entity", "target_name": "email", "target_type": "attribute",
             "relationship_type": "has_property", "source": "manual"},
            {"source_name": "RegionTaxonomy", "source_type": "taxonomy", "target_name": "Revenue", "target_type": "business_concept",
             "relationship_type": "is_a", "source": "manual"},
            {"source_name": "order_summary", "source_type": "metric", "target_name": "Customer", "target_type": "entity",
             "relationship_type": "measures", "source": "manual"},
            {"source_name": "order.order_summary", "source_type": "ddl_artifact", "target_name": "order_summary", "target_type": "metric",
             "relationship_type": "implements", "source": "manual"},
            {"source_name": "order.order_summary", "source_type": "ddl_artifact", "target_name": "order.order_header", "target_type": "table",
             "relationship_type": "derives_from", "source": "manual"},
            {"source_name": "order.order_header", "source_type": "table", "target_name": "Customer", "target_type": "entity",
             "relationship_type": "implements", "source": "manual"},
            {"source_name": "Customer", "source_type": "entity", "target_name": "Revenue", "target_type": "business_concept",
             "relationship_type": "relates_to", "source": "manual"},
            {"source_name": "email", "source_type": "attribute", "target_name": "Customer", "target_type": "entity",
             "relationship_type": "depends_on", "source": "manual"},
        ],
    }


def by_name(items):
    return {(e["name"], e["entity_type"]): e for e in items}


def rel_key(r):
    return (r["source_name"], r["source_type"], r["relationship_type"], r["target_name"], r["target_type"])


# --------------------------------------------------------------------------
def test_vocab():
    print("\n[vocab — namespaces, type maps, IRI minting]")
    check("QB namespace is the canonical W3C Data Cube IRI",
          str(QB) == "http://purl.org/linked-data/cube#")
    check("entity → owl:Class", ENTITY_RDF_TYPE["entity"] == OWL.Class)
    check("attribute → owl:DatatypeProperty", ENTITY_RDF_TYPE["attribute"] == OWL.DatatypeProperty)
    check("metric → qb:DataStructureDefinition", ENTITY_RDF_TYPE["metric"] == QB.DataStructureDefinition)
    check("table → dcat:Dataset", ENTITY_RDF_TYPE["table"] == DCAT.Dataset)
    check("contains → skos:narrower (exact standard)", REL_PREDICATE("contains") == SKOS.narrower)
    check("is_a → rdfs:subClassOf", REL_PREDICATE("is_a") == RDFS.subClassOf)
    check("implements → prov:wasDerivedFrom", REL_PREDICATE("implements") == PROV.wasDerivedFrom)
    check("relates_to → uderia: fallback (no standard misuse)",
          REL_PREDICATE("relates_to") == UDERIA["rel_relates_to"])
    check("slug deterministic + url-safe", slug("Order Header!") == "Order_Header" and slug("a b") == slug("a b"))
    check("entity_uri deterministic", entity_uri("k", "N", "entity") == entity_uri("k", "N", "entity"))


def test_export_structure():
    print("\n[export — standard projection + uderia annotation layer]")
    exp = sample_export()
    g = convert_kg(exp)
    kid = "demo_kg"
    cust = entity_uri(kid, "Customer", "entity")
    rev = entity_uri(kid, "Revenue", "business_concept")
    em = entity_uri(kid, "email", "attribute")
    metr = entity_uri(kid, "order_summary", "metric")

    check("Customer typed owl:Class", (cust, RDF.type, OWL.Class) in g)
    check("Revenue typed skos:Concept", (rev, RDF.type, SKOS.Concept) in g)
    check("Revenue carries skos:prefLabel", (rev, SKOS.prefLabel, Literal("Revenue")) in g)
    check("Revenue in a concept scheme", any(True for _ in g.objects(rev, SKOS.inScheme)))
    check("email typed owl:DatatypeProperty", (em, RDF.type, OWL.DatatypeProperty) in g)
    check("has_property → email rdfs:domain Customer", (em, RDFS.domain, cust) in g)
    check("metric typed qb:DataStructureDefinition", (metr, RDF.type, QB.DataStructureDefinition) in g)
    # uderia round-trip layer
    check("uderia:entityType present on Customer", (cust, UDERIA.entityType, Literal("entity")) in g)
    check("uderia:name present on Customer", (cust, UDERIA.name, Literal("Customer")) in g)
    props_lit = g.value(cust, UDERIA.properties)
    check("uderia:properties is the exact JSON dict",
          props_lit is not None and json.loads(str(props_lit)).get("domain") == "Sales")
    # direct interop triple + reified lossless relationship
    sales = entity_uri(kid, "Sales", "domain")
    check("direct skos:narrower edge Sales→Customer", (sales, SKOS.narrower, cust) in g)
    check("reified uderia:Relationship node exists",
          any(True for _ in g.subjects(RDF.type, UDERIA.Relationship)))
    n_rel_nodes = len(list(g.subjects(RDF.type, UDERIA.Relationship)))
    check("one reified node per relationship (9)", n_rel_nodes == 9)


def test_metric_qb():
    print("\n[metric — RDF Data Cube + PROV + DCAT]")
    metric = next(e for e in sample_export()["entities"] if e["entity_type"] == "metric")
    g = convert_metric(metric, kg_id="demo_kg",
                       deployment={"view": "order.order_summary", "base_table": "order.order_header",
                                   "infrastructure": "teradata"})
    dsd = entity_uri("demo_kg", "order_summary", "metric")
    dims = list(g.subjects(RDF.type, QB.DimensionProperty))
    meas = list(g.subjects(RDF.type, QB.MeasureProperty))
    comps = list(g.objects(dsd, QB.component))
    check("1 qb:DimensionProperty", len(dims) == 1)
    check("2 qb:MeasureProperty", len(meas) == 2)
    check("3 qb:component specs on the DSD", len(comps) == 3)
    check("measure expr preserved as rdfs:comment",
          any(str(o) == "SUM(amount)" for _, _, o in g.triples((None, RDFS.comment, None))))
    check("non-additive flag preserved (uderia:additive false present)",
          (None, UDERIA.additive, Literal(False)) in g)
    check("PROV lineage: metric wasDerivedFrom base table",
          (dsd, PROV.wasDerivedFrom, entity_uri("demo_kg", "order.order_header", "table")) in g)
    check("DCAT: metric is a dcat:Dataset", (dsd, RDF.type, DCAT.Dataset) in g)
    check("DCAT: deployed view is a dcat:DataService",
          (entity_uri("demo_kg", "order.order_summary", "ddl_artifact"), RDF.type, DCAT.DataService) in g)
    # parse back
    props = parse_metric(g)
    check("parse_metric recovers dimension", props and props["dimensions"][0]["name"] == "order_date")
    check("parse_metric recovers measures + additive",
          props and {m["name"]: m.get("additive") for m in props["measures"]} == {"total": True, "avg_amt": False})

    # A column-mapped metric carries column-level PROV lineage (the direct realization).
    gc = convert_metric(metric, kg_id="demo_kg",
                        deployment={"view": None, "column": "wh.order_header.amount",
                                    "base_table": "wh.order_header", "infrastructure": "teradata"})
    check("PROV lineage: column-mapped metric wasDerivedFrom the column",
          (dsd, PROV.wasDerivedFrom, entity_uri("demo_kg", "wh.order_header.amount", "column")) in gc)


def test_full_round_trip():
    print("\n[round-trip — export → serialize → parse → reconstruct (lossless)]")
    exp = sample_export()
    src_ents = by_name(exp["entities"])
    src_rels = {rel_key(r): r for r in exp["relationships"]}
    for ser in ("turtle", "jsonld", "xml", "nt"):
        g2 = round_trip(convert_kg(exp), ser)
        ents, rels, warns = parse_kg(g2)
        ents_idx = by_name(ents)
        check(f"[{ser}] entity count preserved (13)", len(ents) == 13)
        check(f"[{ser}] relationship count preserved (9)", len(rels) == 9)
        check(f"[{ser}] no warnings (uderia layer present)", warns == [])
        # every source entity reconstructed with exact type + properties
        ok_ent = all(
            k in ents_idx and ents_idx[k]["properties"] == v["properties"]
            for k, v in src_ents.items()
        )
        check(f"[{ser}] every entity name/type/properties identical", ok_ent)
        # relationships incl. cardinality + metadata
        rels_idx = {rel_key(r): r for r in rels}
        contains = rels_idx.get(("Sales", "domain", "contains", "Customer", "entity"))
        check(f"[{ser}] relationship set identical", set(rels_idx) == set(src_rels))
        check(f"[{ser}] cardinality + metadata survive",
              contains and contains.get("cardinality") == "1:N"
              and contains.get("metadata") == {"evidence": "x"})


def test_graph_isomorphism():
    print("\n[round-trip — RDF graph isomorphism]")
    g = convert_kg(sample_export())
    for ser in ("turtle", "jsonld", "xml", "nt"):
        check(f"[{ser}] serialize→parse is isomorphic", g.isomorphic(round_trip(g, ser)))


def test_fallback_foreign_graph():
    print("\n[import — foreign graph (no uderia layer): best-effort + honest warnings]")
    # A minimal foreign ontology using ONLY standard predicates.
    ttl = """
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
    @prefix ex: <http://example.org/> .
    ex:Animal a owl:Class ; rdfs:label "Animal" .
    ex:Dog a owl:Class ; rdfs:label "Dog" ; rdfs:subClassOf ex:Animal .
    ex:Color a skos:Concept ; skos:prefLabel "Color" ; skos:definition "a hue" .
    """
    g = parse(ttl, "turtle")
    ents, rels, warns = parse_kg(g)
    idx = by_name(ents)
    check("foreign owl:Class → entity", ("Dog", "entity") in idx and ("Animal", "entity") in idx)
    check("foreign skos:Concept → business_concept", ("Color", "business_concept") in idx)
    check("foreign skos:definition → properties.description",
          idx[("Color", "business_concept")]["properties"].get("description") == "a hue")
    check("foreign rdfs:subClassOf → is_a relationship",
          any(r["relationship_type"] == "is_a" and r["source_name"] == "Dog" for r in rels))
    check("fallback path leaves no spurious warnings here", warns == [])


def test_fallback_warns_on_unmapped():
    print("\n[import — foreign edge with unresolvable endpoint warns (never silent)]")
    ttl = """
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix ex: <http://example.org/> .
    ex:Dog a owl:Class ; rdfs:label "Dog" ; rdfs:subClassOf ex:UnknownThing .
    """
    _, rels, warns = parse_kg(parse(ttl, "turtle"))
    check("unresolvable subClassOf produced no relationship", rels == [])
    check("unmapped edge recorded a warning", any("skipped" in w for w in warns))


def test_vocab_filtering():
    print("\n[format — vocab-layer filtering]")
    exp = sample_export()
    skos_ents, _, _ = parse_kg(round_trip(convert_kg(exp, "skos"), "turtle"))
    types_in_skos = {e["entity_type"] for e in skos_ents}
    check("skos format → concept types only",
          types_in_skos and types_in_skos <= {"business_concept", "taxonomy", "domain"})
    owl_ents, _, _ = parse_kg(round_trip(convert_kg(exp, "owl"), "turtle"))
    owl_types = {e["entity_type"] for e in owl_ents}
    check("owl format excludes pure-physical types",
          "table" not in owl_types and "column" not in owl_types and "entity" in owl_types)
    rdf_ents, _, _ = parse_kg(round_trip(convert_kg(exp, "rdf"), "turtle"))
    check("rdf format → everything (13)", len(rdf_ents) == 13)


def test_determinism_and_purity():
    print("\n[determinism + input non-mutation]")
    exp = sample_export()
    before = json.dumps(exp, sort_keys=True)
    t1 = serialize(convert_kg(exp), "turtle")
    t2 = serialize(convert_kg(sample_export()), "turtle")
    check("same input → identical Turtle bytes", t1 == t2)
    check("convert_kg does not mutate its input", json.dumps(exp, sort_keys=True) == before)


def test_edge_cases():
    print("\n[edge cases — empty KG, serializer validation]")
    g = convert_kg({"kg_id": "empty", "entities": [], "relationships": []})
    ents, rels, warns = parse_kg(round_trip(g, "turtle"))
    check("empty KG → no entities, no relationships", ents == [] and rels == [])
    check("empty KG graph still has the typed scheme root", len(g) >= 1)
    check("serializers.normalize maps ttl alias", serializers.normalize("ttl") == "ttl")
    check("serializers.mime_for jsonld", serializers.mime_for("jsonld") == "application/ld+json")
    try:
        serializers.normalize("yaml")
        raised = False
    except ValueError:
        raised = True
    check("unsupported serialization raises ValueError", raised)


def main():
    print("=" * 70)
    print("semantic_interchange — module tests")
    print("=" * 70)
    test_vocab()
    test_export_structure()
    test_metric_qb()
    test_full_round_trip()
    test_graph_isomorphism()
    test_fallback_foreign_graph()
    test_fallback_warns_on_unmapped()
    test_vocab_filtering()
    test_determinism_and_purity()
    test_edge_cases()
    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
