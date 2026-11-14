"""Reading class definitions out of a published ontology.

A product declares the standard it implements; the model that standard publishes is where
"a Hash carries hashMethod and hashValue" actually lives. These assertions cover the two
ways a standard says a class has a property — a bare `rdfs:domain`, and an
`owl:Restriction` superclass, which is what UCO and many others actually use. A reader
that follows only `domain` sees those classes as having no properties at all, which is
indistinguishable from an ontology that says nothing.

CI-pure: an inline Turtle document, no network.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_passed = _failed = 0
def check(name, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok  {name}")
    else: _failed += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

from trusted_data_agent.semantic_interchange import ontology_classes as oc
from rdflib import Graph

TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix ex:   <http://example.org/o#> .

ex:UcoObject a owl:Class ;
    rdfs:label "UCO Object" ;
    skos:definition "The base of everything." .

ex:Hash a owl:Class ;
    rdfs:label "Hash" ;
    rdfs:comment "editorial note, not the definition" ;
    skos:definition "A cryptographic digest of some content." ;
    rdfs:subClassOf ex:UcoObject ;
    rdfs:subClassOf [ a owl:Restriction ; owl:onProperty ex:hashValue ] ;
    rdfs:subClassOf [ a owl:Restriction ; owl:onProperty ex:hashMethod ] .

ex:hashValue  a owl:DatatypeProperty ; rdfs:label "hash value" ;
              skos:definition "The digest itself." ; rdfs:range rdfs:Literal .
ex:hashMethod a owl:DatatypeProperty ; rdfs:label "hash method" .

ex:File a owl:Class ;
    rdfs:label "File" ;
    rdfs:subClassOf ex:UcoObject .
ex:fileName a owl:DatatypeProperty ; rdfs:domain ex:File ; rdfs:label "file name" .

ex:Unrelated a rdfs:Class ; rdfs:label "Unrelated" .

# SHACL — how UCO (and therefore CASE) actually declares properties. Verified against
# the published ontology: types:Hash returns NO properties without this path.
ex:Digest a owl:Class , sh:NodeShape ;
    rdfs:label "Digest" ;
    sh:property [ sh:path ex:digestValue  ; sh:datatype rdfs:Literal ; sh:minCount 1 ] ,
                [ sh:path ex:digestNote   ] ,
                # the same property shaped twice, as UCO does (a real shape beside a
                # deprecation-warning shape): each carries only part of the picture
                [ sh:path ex:digestValue  ; sh:datatype rdfs:Literal ] .

# the other binding: a standalone shape naming its class with sh:targetClass
ex:Carved a owl:Class ; rdfs:label "Carved" .
ex:CarvedShape a sh:NodeShape ; sh:targetClass ex:Carved ;
    sh:property [ sh:path ex:offset ; sh:minCount 1 ] .
"""

def _g():
    g = Graph(); g.parse(data=TTL, format="turtle"); return g

C = "http://example.org/o#"

def t_classes_and_definitions():
    cls = oc.extract_classes(_g())
    # Derived from the fixture, not frozen: a pinned list breaks when the fixture grows
    # while the property it stands for — every declared class is found, nothing invented —
    # still holds perfectly.
    import re
    declared = {m.group(1) for m in re.finditer(r"ex:(\w+) a (?:owl|rdfs):Class", TTL)}
    found = {oc.local_name(k) for k in cls}
    check("every class the document declares is found", declared <= found,
          f"missing {sorted(declared - found)}")
    check("and no class is invented", found <= declared,
          f"extra {sorted(found - declared)}")
    h = cls[C + "Hash"]
    check("label read", h["label"] == "Hash")
    check("skos:definition wins over rdfs:comment",
          h["definition"] == "A cryptographic digest of some content.", h["definition"])
    check("named superclass captured", h["subclass_of"] == [C + "UcoObject"], str(h["subclass_of"]))
    check("the anonymous restriction is NOT a superclass",
          all(not s.startswith("_") for s in h["subclass_of"]))

def t_both_ways_a_property_is_declared():
    cls = oc.extract_classes(_g())
    hp = {p["name"] for p in cls[C + "Hash"]["properties"]}
    check("owl:Restriction properties are found (the UCO pattern)",
          hp == {"hashValue", "hashMethod"}, str(sorted(hp)))
    fp = {p["name"] for p in cls[C + "File"]["properties"]}
    check("rdfs:domain properties are found", fp == {"fileName"}, str(sorted(fp)))
    # Defensive: a mutation that drops the restriction path leaves this list EMPTY, and a
    # gate that raises there reports nothing instead of naming what broke.
    val = next((p for p in cls[C + "Hash"]["properties"] if p["name"] == "hashValue"), None)
    check("a property carries its definition",
          bool(val) and val["definition"] == "The digest itself.", str(val))
    check("a property carries its range",
          bool(val) and bool(val["range"]) and val["range"].endswith("Literal"), str(val))
    check("a class with no declared property reports none",
          cls[C + "Unrelated"]["properties"] == [])

def t_only_filter_and_superclass_closure():
    """A standard is far larger than the slice a product uses; importing all of it would
    bury the product's own model."""
    cls = oc.extract_classes(_g(), only=[C + "Hash"])
    names = {oc.local_name(k) for k in cls}
    check("the requested class is present", "Hash" in names)
    check("its named ancestor is pulled in", "UcoObject" in names)
    check("unrelated classes are NOT", "File" not in names and "Unrelated" not in names,
          str(sorted(names)))
    tight = oc.extract_classes(_g(), only=[C + "Hash"], include_superclasses=False)
    check("closure can be switched off", {oc.local_name(k) for k in tight} == {"Hash"})
    check("an unknown IRI yields nothing, it does not throw",
          oc.extract_classes(_g(), only=["http://example.org/o#Nope"]) == {})

def t_inherited_properties():
    """What a consumer needs: what an object of this class carries, wherever declared."""
    cls = oc.extract_classes(_g())
    names = [p["name"] for p in oc.inherited_properties(cls, C + "File")]
    check("own properties are included", "fileName" in names)
    cls2 = dict(cls)
    cls2[C + "UcoObject"] = {**cls[C + "UcoObject"],
                             "properties": [{"iri": C + "id", "name": "id",
                                             "label": None, "definition": None, "range": None}]}
    inh = [p["name"] for p in oc.inherited_properties(cls2, C + "File")]
    check("an ancestor's properties are inherited", set(inh) == {"fileName", "id"}, str(inh))
    check("a cycle or unknown parent does not hang",
          oc.inherited_properties(cls, "http://example.org/o#Nope") == [])

def t_deterministic_and_pure():
    g = _g()
    a, b = oc.extract_classes(g), oc.extract_classes(g)
    check("repeat extraction is identical", a == b)
    check("class order is stable", list(a) == sorted(a))
    check("property order within a class is stable",
          [p["iri"] for p in a[C + "Hash"]["properties"]] ==
          sorted(p["iri"] for p in a[C + "Hash"]["properties"]))
    before = len(g)
    oc.extract_classes(g)
    check("the input graph is not mutated", len(g) == before)
    check("an empty graph yields nothing", oc.extract_classes(Graph()) == {})
    check("a None graph yields nothing rather than raising", oc.extract_classes(None) == {})


def t_shacl_is_how_real_standards_declare_properties():
    """The path that matters. UCO — and therefore CASE — states every property this way;
    a reader following only rdfs:domain and owl:Restriction sees real UCO classes as
    having no properties at all. That is not a hypothetical: it is what this module did
    against the published ontology before this path existed."""
    cls = oc.extract_classes(_g())
    d = {p["name"]: p for p in cls[C + "Digest"]["properties"]}
    check("sh:property on the class is followed", set(d) == {"digestValue", "digestNote"},
          str(sorted(d)))
    check("sh:minCount marks a property REQUIRED", d.get("digestValue", {}).get("required") is True)
    check("absent minCount is not required", d.get("digestNote", {}).get("required") is False)
    # required is the point: it turns an absence into a conformance finding.
    check("a property shaped twice appears ONCE", len(cls[C + "Digest"]["properties"]) == 2)
    check("...and the duplicate shapes are MERGED, not replaced",
          d.get("digestValue", {}).get("required") is True
          and bool(d.get("digestValue", {}).get("range")), str(d.get("digestValue")))

def t_shacl_target_class_binding():
    """A shape may name its class instead of being it; both bindings occur in the wild."""
    cls = oc.extract_classes(_g())
    names = {p["name"]: p for p in cls[C + "Carved"]["properties"]}
    check("sh:targetClass shapes are followed", set(names) == {"offset"}, str(sorted(names)))
    check("their cardinality is read too", names.get("offset", {}).get("required") is True)


def main():
    print("=" * 70); print("Ontology class extraction — following the declared standard"); print("=" * 70)
    for t in (t_classes_and_definitions, t_both_ways_a_property_is_declared,
              t_only_filter_and_superclass_closure, t_inherited_properties,
              t_shacl_is_how_real_standards_declare_properties, t_shacl_target_class_binding,
              t_deterministic_and_pure):
        t()
    print(f"\n{_passed} passed, {_failed} failed  ({_passed+_failed} assertions)")
    return 1 if _failed else 0

if __name__ == "__main__":
    sys.exit(main())
