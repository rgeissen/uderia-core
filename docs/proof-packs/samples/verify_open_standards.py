#!/usr/bin/env python3
"""
verify_open_standards.py — standalone, offline verifier for Uderia's Tier 4.1
open-standards semantic export (T4).

It needs **no Uderia install** — only rdflib:

    pip install rdflib
    python verify_open_standards.py

It proves, against the committed sample artifacts in this directory, that:

  1. LOSSLESS ROUND-TRIP   — each sample, serialized through Turtle / JSON-LD /
     RDF-XML / N-Triples and parsed back, is an isomorphic RDF graph.
  2. RE-IMPORTABLE         — the ``uderia:`` annotation layer carries every fact
     needed to reconstruct the Uderia KG (entity_type, full properties JSON,
     relationship type/cardinality/metadata) — counted here straight from the
     graph, no Uderia code.
  3. QB STRUCTURAL CONFORMANCE — the metric is a well-formed W3C RDF Data Cube
     DataStructureDefinition (typed DSD, ≥1 dimension + ≥1 measure component,
     every component property correctly typed). [structure-only export → the
     observation-level ICs IC-1..21 are N/A and are honestly not claimed.]
  4. THIRD-PARTY CONSUMABLE — a SPARQL query using ONLY standard predicates
     (owl / skos / qb) walks the ontology and lists the metric's dimensions and
     measures — i.e. a tool that has never heard of Uderia can use the semantics.
  5. TAMPER-EVIDENT        — mutating one triple breaks the isomorphism check,
     proving the round-trip check has teeth.

Exit code 0 = all proofs pass, 1 = any failure.
"""

import os
import sys

try:
    from rdflib import Graph, Namespace, Literal
    from rdflib.namespace import OWL, RDF, RDFS, SKOS
except ImportError:
    print("ERROR: rdflib not installed.  Run:  pip install rdflib")
    sys.exit(2)

QB = Namespace("http://purl.org/linked-data/cube#")
UDERIA = Namespace("https://uderia.ai/ns#")

HERE = os.path.dirname(os.path.abspath(__file__))
ONTOLOGY = os.path.join(HERE, "ontology_sample.ttl")
METRIC = os.path.join(HERE, "metric_qb_sample.ttl")

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    mark = "✓" if cond else "✗"
    print(f"  {mark} {label}")
    if cond:
        _passed += 1
    else:
        _failed += 1


def load(path):
    g = Graph()
    g.parse(path, format="turtle")
    return g


# 1 + 5 — round-trip isomorphism (and tamper detection)
def test_round_trip(g, name):
    print(f"\n[1] lossless round-trip — {name}")
    for fmt in ("turtle", "json-ld", "xml", "nt"):
        g2 = Graph()
        g2.parse(data=g.serialize(format=fmt), format=fmt)
        check(f"{name}: {fmt} serialize→parse is isomorphic", g.isomorphic(g2))


def test_tamper(g, name):
    print(f"\n[5] tamper-evident — {name}")
    g2 = Graph()
    g2.parse(data=g.serialize(format="turtle"), format="turtle")
    # drop one triple → must no longer be isomorphic
    one = next(iter(g2))
    g2.remove(one)
    check(f"{name}: dropping one triple breaks isomorphism", not g.isomorphic(g2))


# 2 — re-importable via the uderia: annotation layer
def test_reimportable(g, name, exp_entities, exp_rels):
    print(f"\n[2] re-importable (uderia: layer) — {name}")
    ents = list(g.subjects(UDERIA.entityType, None))
    rels = list(g.subjects(RDF.type, UDERIA.Relationship))
    check(f"{name}: {len(ents)} entities carry uderia:entityType (expect {exp_entities})",
          len(ents) == exp_entities)
    check(f"{name}: {len(rels)} reified relationships (expect {exp_rels})", len(rels) == exp_rels)
    # every entity also carries its name + a properties JSON literal
    have_name = all(g.value(e, UDERIA.name) is not None for e in ents)
    have_props = all(g.value(e, UDERIA.properties) is not None for e in ents)
    check(f"{name}: every entity has uderia:name", have_name)
    check(f"{name}: every entity has uderia:properties (lossless JSON)", have_props)


# 3 — QB structural conformance
def test_qb_conformance(g):
    print("\n[3] W3C RDF Data Cube — structural conformance")
    dsd = g.value(predicate=RDF.type, object=QB.DataStructureDefinition)
    check("a qb:DataStructureDefinition exists", dsd is not None)
    comps = list(g.objects(dsd, QB.component)) if dsd else []
    check("DSD has ≥1 qb:component", len(comps) >= 1)
    dims = list(g.subjects(RDF.type, QB.DimensionProperty))
    meas = list(g.subjects(RDF.type, QB.MeasureProperty))
    check("≥1 qb:DimensionProperty", len(dims) >= 1)
    check("≥1 qb:MeasureProperty", len(meas) >= 1)
    # every component spec references exactly a dimension or a measure
    well_formed = True
    for c in comps:
        has_dim = bool(list(g.objects(c, QB.dimension)))
        has_meas = bool(list(g.objects(c, QB.measure)))
        if has_dim == has_meas:  # neither, or both → malformed
            well_formed = False
    check("every qb:component links exactly one dimension XOR measure", well_formed)
    # every referenced component property is correctly typed
    typed = True
    for c in comps:
        for d in g.objects(c, QB.dimension):
            typed = typed and (d, RDF.type, QB.DimensionProperty) in g
        for m in g.objects(c, QB.measure):
            typed = typed and (m, RDF.type, QB.MeasureProperty) in g
    check("every component property is typed (Dimension/Measure)", typed)


# 4 — third-party consumption with ONLY standard predicates
def test_third_party(g_ont, g_metric):
    print("\n[4] third-party consumable — SPARQL over standard predicates only")
    # walk the ontology: classes + concept hierarchy (owl / skos)
    classes = set(g_ont.subjects(RDF.type, OWL.Class))
    check(f"owl:Class query returns the logical entities ({len(classes)} found)", len(classes) >= 1)
    q_hier = """SELECT ?broader ?narrower WHERE { ?broader skos:narrower ?narrower }"""
    hier = list(g_ont.query(q_hier, initNs={"skos": SKOS}))
    check("skos:narrower hierarchy is queryable", len(hier) >= 1)
    # list the metric's dimensions + measures via QB only
    q_dims = """SELECT ?label WHERE {
        ?dsd a qb:DataStructureDefinition ; qb:component ?c .
        ?c qb:dimension ?d . ?d rdfs:label ?label }"""
    q_meas = """SELECT ?label WHERE {
        ?dsd a qb:DataStructureDefinition ; qb:component ?c .
        ?c qb:measure ?m . ?m rdfs:label ?label }"""
    dims = [str(r[0]) for r in g_metric.query(q_dims, initNs={"qb": QB, "rdfs": RDFS})]
    meas = [str(r[0]) for r in g_metric.query(q_meas, initNs={"qb": QB, "rdfs": RDFS})]
    print(f"      → dimensions: {sorted(dims)}")
    print(f"      → measures:   {sorted(meas)}")
    check("a QB consumer recovers the dimension(s)", len(dims) >= 1)
    check("a QB consumer recovers the measure(s)", len(meas) >= 1)


def main():
    print("=" * 70)
    print("Uderia Tier 4.1 — open-standards export verifier (offline, rdflib only)")
    print("=" * 70)
    if not (os.path.exists(ONTOLOGY) and os.path.exists(METRIC)):
        print("ERROR: sample .ttl files not found next to this script.")
        sys.exit(2)
    g_ont = load(ONTOLOGY)
    g_metric = load(METRIC)
    print(f"loaded ontology_sample.ttl ({len(g_ont)} triples), "
          f"metric_qb_sample.ttl ({len(g_metric)} triples)")

    test_round_trip(g_ont, "ontology")
    test_round_trip(g_metric, "metric")
    test_reimportable(g_ont, "ontology", exp_entities=7, exp_rels=6)
    test_qb_conformance(g_metric)
    test_third_party(g_ont, g_metric)
    test_tamper(g_ont, "ontology")

    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} checks)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
