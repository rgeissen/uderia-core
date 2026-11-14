"""
rdf_to_kg — IMPORT an ``rdflib.Graph`` back into Uderia KG shape:
``(entities[], relationships[], warnings[])`` ready for ``GraphStore.import_bulk``.

Two paths:
  * **primary (exact):** when the graph carries the ``uderia:`` annotation layer
    (our own export, or a third-party edit that preserved it), every Uderia fact
    — entity_type, the full properties dict, relationship type/cardinality/
    metadata — is read straight back. Lossless.
  * **fallback (best-effort):** a foreign graph with only standard predicates is
    mapped heuristically (owl:Class→entity, skos:narrower→contains, …). Ambiguous
    by nature (prov:wasDerivedFrom ← implements|derives_from; dcat:Dataset ← any
    physical type); whatever can't be placed is recorded in ``warnings`` — honest,
    never silent (charter #19).

Pure: no I/O, no LLM.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, PROV, RDF, RDFS, SKOS

from .metric_to_qb import metric_props_from_graph
from .vocab import DCAT, QB, UDERIA

# Standard rdf:type → Uderia entity_type (fallback only; lossy/ambiguous).
_RDF_TYPE_TO_ENTITY = {
    OWL.Class: "entity",
    OWL.DatatypeProperty: "attribute",
    OWL.ObjectProperty: "foreign_key",
    SKOS.Concept: "business_concept",
    QB.DataStructureDefinition: "metric",
    DCAT.Dataset: "table",
}
# Standard predicate → Uderia relationship_type (fallback only).
_PRED_TO_REL = {
    SKOS.narrower: "contains",
    RDFS.subClassOf: "is_a",
    PROV.wasDerivedFrom: "derives_from",
}


def parse_graph(graph: Graph) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Reconstruct (entities, relationships, warnings) from an RDF graph."""
    warnings: List[str] = []
    has_uderia_entities = bool(list(graph.subjects(UDERIA.entityType, None)))
    has_uderia_rels = (None, RDF.type, UDERIA.Relationship) in graph

    if has_uderia_entities:
        entities, iri_index = _entities_from_uderia(graph)
    else:
        entities, iri_index = _entities_fallback(graph, warnings)

    if has_uderia_rels:
        relationships = _relationships_from_uderia(graph)
    else:
        relationships = _relationships_fallback(graph, iri_index, warnings)

    return entities, relationships, warnings


# --- primary (uderia layer) -----------------------------------------------

def _entities_from_uderia(graph: Graph):
    entities: List[Dict[str, Any]] = []
    iri_index: Dict[URIRef, Tuple[str, str]] = {}
    for subj, _, etype_lit in graph.triples((None, UDERIA.entityType, None)):
        etype = str(etype_lit)
        name = _first_str(graph, subj, [UDERIA.name, RDFS.label, SKOS.prefLabel])
        if not name:
            continue
        props: Dict[str, Any] = {}
        praw = graph.value(subj, UDERIA.properties)
        if praw is not None:
            try:
                props = json.loads(str(praw))
            except (ValueError, TypeError):
                props = {}
        ent = {
            "name": name,
            "entity_type": etype,
            "properties": props,
            "source": _opt_str(graph, subj, UDERIA.source) or "imported",
        }
        sd = _opt_str(graph, subj, UDERIA.sourceDetail)
        if sd is not None:
            ent["source_detail"] = sd
        entities.append(ent)
        if isinstance(subj, URIRef):
            iri_index[subj] = (name, etype)
    return entities, iri_index


def _relationships_from_uderia(graph: Graph) -> List[Dict[str, Any]]:
    rels: List[Dict[str, Any]] = []
    for r in graph.subjects(RDF.type, UDERIA.Relationship):
        rtype = _opt_str(graph, r, UDERIA.relType)
        sname = _opt_str(graph, r, UDERIA.relSourceName)
        stype = _opt_str(graph, r, UDERIA.relSourceType)
        tname = _opt_str(graph, r, UDERIA.relTargetName)
        ttype = _opt_str(graph, r, UDERIA.relTargetType)
        if not (rtype and sname and stype and tname and ttype):
            continue
        rel: Dict[str, Any] = {
            "source_name": sname, "source_type": stype,
            "target_name": tname, "target_type": ttype,
            "relationship_type": rtype,
            "source": _opt_str(graph, r, UDERIA.relSourceProvenance) or "imported",
        }
        card = _opt_str(graph, r, UDERIA.cardinality)
        if card is not None:
            rel["cardinality"] = card
        mraw = graph.value(r, UDERIA.relMetadata)
        if mraw is not None:
            try:
                rel["metadata"] = json.loads(str(mraw))
            except (ValueError, TypeError):
                rel["metadata"] = {}
        rels.append(rel)
    return rels


# --- fallback (foreign graph, standard predicates only) -------------------

def _entities_fallback(graph: Graph, warnings: List[str]):
    entities: List[Dict[str, Any]] = []
    iri_index: Dict[URIRef, Tuple[str, str]] = {}
    # KG/ontology roots represent the graph itself, not an entity — never import.
    roots = set(graph.subjects(RDF.type, SKOS.ConceptScheme))
    roots |= set(graph.subjects(RDF.type, OWL.Ontology))
    roots |= set(graph.subjects(UDERIA.kgId, None))
    seen: set = set()
    for subj, _, rtype in graph.triples((None, RDF.type, None)):
        if not isinstance(subj, URIRef) or subj in seen or subj in roots:
            continue
        etype = _RDF_TYPE_TO_ENTITY.get(rtype)
        if etype is None:
            continue
        seen.add(subj)
        name = _first_str(graph, subj, [RDFS.label, SKOS.prefLabel]) or _local_name(subj)
        props: Dict[str, Any] = {}
        if etype == "metric":
            props = metric_props_from_graph(graph, subj)
        desc = _first_str(graph, subj, [SKOS.definition])
        if desc:
            props["description"] = desc
        entities.append({
            "name": name, "entity_type": etype,
            "properties": props, "source": "imported",
        })
        iri_index[subj] = (name, etype)
    if not entities:
        warnings.append("no recognizable entities found (no uderia: layer and no standard rdf:type matches)")
    return entities, iri_index


def _relationships_fallback(graph: Graph, iri_index, warnings: List[str]) -> List[Dict[str, Any]]:
    rels: List[Dict[str, Any]] = []
    unmapped = 0
    for pred, rtype in _PRED_TO_REL.items():
        for s, _, o in graph.triples((None, pred, None)):
            si, oi = iri_index.get(s), iri_index.get(o)
            if not si or not oi:
                unmapped += 1
                continue
            rels.append({
                "source_name": si[0], "source_type": si[1],
                "target_name": oi[0], "target_type": oi[1],
                "relationship_type": rtype, "source": "imported",
            })
    if unmapped:
        warnings.append(f"{unmapped} standard-predicate edge(s) skipped: endpoints not resolvable to entities")
    return rels


# --- helpers ---------------------------------------------------------------

def _first_str(graph: Graph, subj, preds) -> Optional[str]:
    for p in preds:
        v = graph.value(subj, p)
        if v is not None:
            return str(v)
    return None


def _opt_str(graph: Graph, subj, pred) -> Optional[str]:
    v = graph.value(subj, pred)
    return str(v) if v is not None else None


def _local_name(uri: URIRef) -> str:
    s = str(uri)
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s or str(uri)
