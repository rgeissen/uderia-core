"""Read CLASS DEFINITIONS out of a published ontology.

A conformant data product declares the standard it implements — the ANDP catalogue
carries ``industry_standard`` on an entity and, where the producer went further, an
``ontology_iri`` naming the exact class. Those declarations are *references*: the model
they point at is published, versioned and authoritative, and until now the platform
stored the reference as an inert string and never followed it.

This module follows it. Given an ontology graph it returns, per class: the label, the
definition, its superclasses, and **the properties the standard says that class has** —
which is the fact an agent cannot obtain any other way. Deriving the same thing from a
product's data only ever yields what that corpus happens to contain: a class with no rows
is a class you never learn exists, and a property no row populates is a property you never
learn is missing. Reading the standard is how "this Hash object has no ``hashValue``"
becomes a conformance statement rather than the end of an unsuccessful search.

Deliberately narrow. This is NOT general ontology import — ``rdf_to_kg`` already covers
the round-trip case and documents why arbitrary foreign-graph mapping stays deferred
(precise mapping needs judgement). Here the target is the well-defined subset that answers
"what is this class and what does it carry": ``owl:Class`` / ``rdfs:Class``,
``rdfs:subClassOf``, ``rdfs:label``, ``rdfs:comment`` / ``skos:definition``, and the
properties reaching a class through ``rdfs:domain`` or an ``owl:Restriction``.

Pure: an rdflib Graph in, plain dicts out. No network, no I/O, no platform imports.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

try:
    from rdflib import Graph, URIRef, BNode
    from rdflib.namespace import OWL, RDF, RDFS, SKOS, SH
    HAS_RDFLIB = True
except ImportError:  # pragma: no cover - rdflib is a hard dep; this keeps import safe
    HAS_RDFLIB = False
    Graph = URIRef = BNode = None  # type: ignore


#: Predicates carrying a human definition, in preference order. `skos:definition` first:
#: where a standard supplies both, it is the curated one and `rdfs:comment` is often an
#: editorial note.
_DEFINITION_PREDS = ("skos_definition", "rdfs_comment")


def _pref_label(graph, subj) -> Optional[str]:
    for p in (RDFS.label, SKOS.prefLabel):
        for o in graph.objects(subj, p):
            s = str(o).strip()
            if s:
                return s
    return None


def _definition(graph, subj) -> Optional[str]:
    for p in (SKOS.definition, RDFS.comment):
        for o in graph.objects(subj, p):
            s = " ".join(str(o).split())
            if s:
                return s
    return None


def local_name(iri: str) -> str:
    """The trailing name of an IRI — `…#Hash` and `…/Hash` both give `Hash`."""
    s = str(iri)
    for sep in ("#", "/"):
        if sep in s:
            tail = s.rsplit(sep, 1)[-1]
            if tail:
                return tail
    return s


def _restriction_properties(graph, cls) -> List[Any]:
    """Properties a class carries through an ``owl:Restriction`` superclass.

    Widely used ontologies (UCO among them) express "this class has this property" as a
    restriction rather than a bare ``rdfs:domain``, so a reader that only follows domain
    sees a class with no properties at all.
    """
    out = []
    for sup in graph.objects(cls, RDFS.subClassOf):
        if not isinstance(sup, BNode):
            continue
        if (sup, RDF.type, OWL.Restriction) not in graph:
            continue
        for prop in graph.objects(sup, OWL.onProperty):
            out.append(prop)
    return out


def _shacl_properties(graph, cls) -> List[Any]:
    """Properties a class carries through SHACL — ``sh:property`` -> ``sh:path``.

    This is not an exotic third path: it is how UCO (and therefore CASE) declares every
    property on every class. The class is also declared an ``sh:NodeShape`` and its
    properties hang off property shapes. A reader that follows only ``rdfs:domain`` and
    ``owl:Restriction`` reads real UCO as a set of classes with NO properties at all —
    verified against the published ontology, where `types:Hash` returned an empty list
    until this path existed.

    Both bindings are followed: shapes on the class itself, and standalone shapes that
    name it with ``sh:targetClass`` (the same ontology may use either).
    """
    shapes = set()
    for sh_node in graph.objects(cls, SH.property):
        shapes.add(sh_node)
    for shape in graph.subjects(SH.targetClass, cls):
        for sh_node in graph.objects(shape, SH.property):
            shapes.add(sh_node)
    return sorted(shapes, key=str)


def _shacl_entry(graph, shape) -> Optional[Dict[str, Any]]:
    """One SHACL property shape -> a property entry, or None if it declares no path."""
    paths = [p for p in graph.objects(shape, SH.path) if isinstance(p, URIRef)]
    if not paths:
        return None
    prop = paths[0]
    def _one(pred):
        for o in graph.objects(shape, pred):
            return str(o)
        return None
    minc = _one(SH.minCount)
    entry = {
        "iri": str(prop),
        "name": local_name(prop),
        "label": _pref_label(graph, prop),
        "definition": _definition(graph, prop),
        # A shape constrains either a literal datatype or a node class; take whichever
        # it states. (`sh:class` is spelled `class_` in rdflib because `class` is a
        # keyword, and older rdflib releases do not define it at all.)
        "range": _one(SH.datatype) or (_one(getattr(SH, "class_")) if hasattr(SH, "class_") else None),
        # Cardinality is the point of reading SHACL rather than guessing: it is the
        # difference between "this class has a hashValue" and "hashValue is REQUIRED and
        # this object has none", which is a conformance finding rather than an absence.
        "required": (minc is not None and str(minc).strip() not in ("", "0")),
    }
    return entry


def _property_entry(graph, prop) -> Dict[str, Any]:
    ranges = [str(r) for r in graph.objects(prop, RDFS.range)]
    return {
        "iri": str(prop),
        "name": local_name(prop),
        "label": _pref_label(graph, prop),
        "definition": _definition(graph, prop),
        "range": ranges[0] if ranges else None,
    }


def extract_classes(graph: "Graph",
                    only: Optional[Iterable[str]] = None,
                    include_superclasses: bool = True) -> Dict[str, Dict[str, Any]]:
    """Class definitions from an ontology, keyed by class IRI.

    ``only`` restricts the result to the given class IRIs plus (when
    ``include_superclasses``) their named ancestors — a standard is often far larger than
    the part a product uses, and importing all of it would bury the product's own model.
    Passing ``None`` returns every named class.

    Each value: ``{iri, name, label, definition, subclass_of[], properties[]}``.
    """
    if not HAS_RDFLIB or graph is None:
        return {}

    named: Set[Any] = set()
    for t in (OWL.Class, RDFS.Class):
        for s in graph.subjects(RDF.type, t):
            if isinstance(s, URIRef):
                named.add(s)

    wanted: Optional[Set[str]] = None
    if only is not None:
        wanted = {str(x) for x in only}
        frontier = {s for s in named if str(s) in wanted}
        if include_superclasses:
            seen, queue = set(frontier), list(frontier)
            while queue:
                cur = queue.pop()
                for sup in graph.objects(cur, RDFS.subClassOf):
                    if isinstance(sup, URIRef) and sup in named and sup not in seen:
                        seen.add(sup)
                        queue.append(sup)
            frontier = seen
        named = frontier

    # property -> the classes it is declared on, via rdfs:domain
    by_domain: Dict[Any, List[Any]] = {}
    for prop in set(graph.subjects(RDFS.domain, None)):
        for dom in graph.objects(prop, RDFS.domain):
            by_domain.setdefault(dom, []).append(prop)

    out: Dict[str, Dict[str, Any]] = {}
    for cls in sorted(named, key=str):
        props = list(by_domain.get(cls, [])) + _restriction_properties(graph, cls)
        seen_p, entries = set(), []
        for p in sorted(props, key=str):
            if str(p) in seen_p:
                continue
            seen_p.add(str(p))
            e = _property_entry(graph, p)
            e["required"] = False           # neither path states cardinality
            entries.append(e)
        # SHACL last so a shape's cardinality wins over a bare domain declaration of the
        # same property; the same property is often shaped more than once (UCO adds a
        # deprecation-warning shape beside the real one), so dedupe and prefer the entry
        # that actually states a requirement.
        for shape in _shacl_properties(graph, cls):
            e = _shacl_entry(graph, shape)
            if not e:
                continue
            prev = next((x for x in entries if x["iri"] == e["iri"]), None)
            if prev is None:
                entries.append(e)
            else:
                # MERGE, never replace. UCO shapes the same property more than once — a
                # real shape and a deprecation-warning shape — and each carries only part
                # of the picture, so replacing loses whichever field the winner lacks.
                prev["required"] = bool(prev.get("required")) or bool(e.get("required"))
                for f in ("range", "label", "definition"):
                    if not prev.get(f) and e.get(f):
                        prev[f] = e[f]
        entries.sort(key=lambda x: x["iri"])
        out[str(cls)] = {
            "iri": str(cls),
            "name": local_name(cls),
            "label": _pref_label(graph, cls),
            "definition": _definition(graph, cls),
            "subclass_of": sorted(str(s) for s in graph.objects(cls, RDFS.subClassOf)
                                  if isinstance(s, URIRef)),
            "properties": entries,
        }
    return out


def inherited_properties(classes: Dict[str, Dict[str, Any]], iri: str) -> List[Dict[str, Any]]:
    """A class's own properties plus every named ancestor's, de-duplicated by IRI.

    What a consumer actually needs: an object of this class carries these, wherever in the
    hierarchy they were declared.
    """
    out: Dict[str, Dict[str, Any]] = {}
    seen: Set[str] = set()
    queue = [iri]
    while queue:
        cur = queue.pop(0)
        if cur in seen or cur not in classes:
            continue
        seen.add(cur)
        for p in classes[cur].get("properties") or []:
            out.setdefault(p["iri"], p)
        queue.extend(classes[cur].get("subclass_of") or [])
    return [out[k] for k in sorted(out)]
