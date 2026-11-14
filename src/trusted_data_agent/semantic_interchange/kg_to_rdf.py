"""
kg_to_rdf — EXPORT a Uderia KG (the export v2.0 dict: entities[] +
relationships[]) into an ``rdflib.Graph`` using SKOS / OWL / RDFS / QB / DCAT /
PROV for interoperability, plus a parallel ``uderia:`` annotation layer for
lossless re-import (see vocab.py).

Pure: no I/O, no LLM, deterministic IRIs.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from rdflib import Graph, Literal, URIRef

from .metric_to_qb import add_metric_dsd
from .vocab import (
    DCAT,
    DCTERMS,
    ENTITY_RDF_TYPE,
    OWL,
    RDF,
    RDFS,
    REL_PREDICATE,
    SKOS,
    UDERIA,
    bind_namespaces,
    entity_uri,
    kg_uri,
    relationship_uri,
)

_CONCEPT_TYPES = {"business_concept", "taxonomy", "domain"}


def build_kg_graph(export: Dict[str, Any]) -> Graph:
    """Convert an export v2.0 dict to an rdflib Graph (the full ontology)."""
    g = Graph()
    bind_namespaces(g)

    kg_id = str(export.get("kg_id") or "default")
    scheme = kg_uri(kg_id)
    # The KG as a concept scheme + ontology + catalog dataset (multi-typed so
    # SKOS, OWL and DCAT consumers each find a recognizable root).
    g.add((scheme, RDF.type, SKOS.ConceptScheme))
    g.add((scheme, RDF.type, OWL.Ontology))
    g.add((scheme, RDF.type, DCAT.Dataset))
    g.add((scheme, UDERIA.kgId, Literal(kg_id)))
    if export.get("kg_name"):
        g.add((scheme, DCTERMS.title, Literal(str(export["kg_name"]))))
    if export.get("kg_description"):
        g.add((scheme, DCTERMS.description, Literal(str(export["kg_description"]))))

    for ent in export.get("entities") or []:
        _add_entity(g, kg_id, scheme, ent)

    for rel in export.get("relationships") or []:
        _add_relationship(g, kg_id, rel)

    return g


def _add_entity(g: Graph, kg_id: str, scheme: URIRef, ent: Dict[str, Any]) -> None:
    name = ent.get("name")
    etype = ent.get("entity_type")
    if not name or not etype:
        return
    uri = entity_uri(kg_id, name, etype)
    props = ent.get("properties") or {}

    # Standard type (interop projection).
    g.add((uri, RDF.type, ENTITY_RDF_TYPE.get(etype, UDERIA.Entity)))
    # Human label across vocabularies.
    g.add((uri, RDFS.label, Literal(name)))
    if etype in _CONCEPT_TYPES:
        g.add((uri, SKOS.prefLabel, Literal(name)))
        g.add((uri, SKOS.inScheme, scheme))

    desc = props.get("description") or props.get("label")
    if desc and str(desc) != name:
        g.add((uri, DCTERMS.description, Literal(str(desc))))
        if etype in _CONCEPT_TYPES:
            g.add((uri, SKOS.definition, Literal(str(desc))))

    # --- uderia: lossless round-trip layer ---
    g.add((uri, UDERIA.entityType, Literal(etype)))
    g.add((uri, UDERIA.name, Literal(name)))
    if ent.get("source"):
        g.add((uri, UDERIA.source, Literal(str(ent["source"]))))
    if ent.get("source_detail"):
        g.add((uri, UDERIA.sourceDetail, Literal(str(ent["source_detail"]))))
    if props:
        # The entire properties dict as one canonical JSON literal → arbitrary
        # nested structure (dimensions/measures/layer/infrastructure/…) survives
        # the round-trip exactly. sort_keys makes the serialization stable.
        g.add((uri, UDERIA.properties, Literal(json.dumps(props, sort_keys=True, default=str))))

    # Metric → full QB DataStructureDefinition (interop).
    if etype == "metric":
        add_metric_dsd(g, kg_id=kg_id, name=name, props=props)


def _add_relationship(g: Graph, kg_id: str, rel: Dict[str, Any]) -> None:
    sname, stype = rel.get("source_name"), rel.get("source_type")
    tname, ttype = rel.get("target_name"), rel.get("target_type")
    rtype = rel.get("relationship_type")
    if not (sname and stype and tname and ttype and rtype):
        return
    src = entity_uri(kg_id, sname, stype)
    tgt = entity_uri(kg_id, tname, ttype)

    # Direct interop triple (standard predicate where exact, else uderia:rel_*).
    g.add((src, REL_PREDICATE(rtype), tgt))
    # has_property → the attribute's rdfs:domain is the owning entity (OWL).
    if rtype == "has_property":
        g.add((tgt, RDFS.domain, src))

    # --- uderia: reified lossless layer (carries type, cardinality, metadata) ---
    ruri = relationship_uri(kg_id, sname, rtype, tname)
    g.add((ruri, RDF.type, UDERIA.Relationship))
    g.add((ruri, UDERIA.relType, Literal(rtype)))
    g.add((ruri, UDERIA.relSource, src))
    g.add((ruri, UDERIA.relTarget, tgt))
    g.add((ruri, UDERIA.relSourceName, Literal(sname)))
    g.add((ruri, UDERIA.relSourceType, Literal(stype)))
    g.add((ruri, UDERIA.relTargetName, Literal(tname)))
    g.add((ruri, UDERIA.relTargetType, Literal(ttype)))
    if rel.get("cardinality"):
        g.add((ruri, UDERIA.cardinality, Literal(str(rel["cardinality"]))))
    if rel.get("source"):
        g.add((ruri, UDERIA.relSourceProvenance, Literal(str(rel["source"]))))
    meta = rel.get("metadata")
    if meta:
        g.add((ruri, UDERIA.relMetadata, Literal(json.dumps(meta, sort_keys=True, default=str))))
