"""
vocab — namespaces, IRI minting, and the Uderia↔RDF type maps for the
semantic-interchange (open-standards) export/import.

Pure: no I/O, no LLM. Everything here is deterministic so that the reverse
(import) is exact.

Design — dual-layer serialization:
  * **standard predicates** (SKOS / OWL / RDFS / QB / PROV / DCAT) carry the
    *interoperable* projection a third-party tool consumes;
  * a parallel **``uderia:`` annotation layer** carries the exact Uderia fact
    (entity_type, the full properties dict as a JSON literal, relationship
    detail) so a Uderia re-import is byte-faithful.

A pure-standards consumer ignores ``uderia:*`` and uses the standard triples;
``rdf_to_kg`` reads the ``uderia:*`` triples for a lossless reconstruction.
"""

from __future__ import annotations

import re
from typing import Optional

from rdflib import Namespace, URIRef
from rdflib.namespace import (
    DCAT,
    DCTERMS,
    OWL,
    PROV,
    RDF,
    RDFS,
    SKOS,
    XSD,
)

# --- Namespaces ------------------------------------------------------------

# RDF Data Cube — the W3C vocabulary for multidimensional (dimension+measure)
# data. Not built into rdflib, so define it here at its canonical IRI.
QB = Namespace("http://purl.org/linked-data/cube#")

# Uderia's own namespace — the round-trip annotation layer + fallbacks for
# relationship types that have no exact standard predicate.
UDERIA = Namespace("https://uderia.ai/ns#")

# Instance base. Per-KG/entity/relationship IRIs are minted under this root.
BASE = "https://uderia.ai/kg/"

# Convenience re-exports so callers import everything from one place.
__all__ = [
    "QB", "UDERIA", "BASE", "SKOS", "DCAT", "DCTERMS", "OWL", "PROV",
    "RDF", "RDFS", "XSD",
    "ENTITY_RDF_TYPE", "REL_PREDICATE", "STANDARD_REL_PREDICATES",
    "bind_namespaces", "slug", "kg_uri", "entity_uri", "relationship_uri",
    "metric_component_uri",
]


# --- Type maps -------------------------------------------------------------

# Uderia entity_type → the standard RDF class used for the interop projection.
# (Canonical enum: components/builtin/knowledge_graph/graph_store.py:48-53.)
ENTITY_RDF_TYPE = {
    # concept / taxonomy layer → SKOS
    "business_concept": SKOS.Concept,
    "taxonomy": SKOS.Concept,
    "domain": SKOS.Concept,
    # logical ontology → OWL/RDFS
    "entity": OWL.Class,
    "attribute": OWL.DatatypeProperty,
    "foreign_key": OWL.ObjectProperty,
    # semantic layer → RDF Data Cube
    "metric": QB.DataStructureDefinition,
    # product-description layer → the closest standard class.  A query recipe is an
    # executable way to obtain data (DCAT DataService); the rest are documented
    # concepts in the product's own descriptive scheme (SKOS).  Exact Uderia types
    # survive the round-trip via the parallel `uderia:` annotation layer.
    "query_recipe": DCAT.DataService,
    "design_decision": SKOS.Concept,
    "quality_metric": SKOS.Concept,
    "naming_standard": SKOS.Concept,
    "product_bridge": SKOS.Concept,
    # governed-consumption layer → DCAT.  An OBDA-READ data product is exactly what
    # dcat:Dataset models: a curated, published collection of data with declared
    # access; its manifest survives the round-trip via the `uderia:` layer.
    "data_product": DCAT.Dataset,
    # no standard class is an exact match for a signed write contract over one entity
    # (schema:Action describes an act performed, not a contract that permits one) — so it
    # is a UDERIA class, never a misused standard one (charter #1)
    "action_type": UDERIA.ActionType,
    # physical / structural resources → DCAT
    "database": DCAT.Dataset,
    "catalog": DCAT.Dataset,
    "schema": DCAT.Dataset,
    "table": DCAT.Dataset,
    "column": DCAT.Dataset,
    "ddl_artifact": DCAT.Dataset,
}

# Uderia relationship_type → standard predicate, used ONLY where the standard
# semantics are an exact match (no shortcuts — charter #1). Types without an
# exact standard predicate fall back to UDERIA[<type>] so we never misuse a
# standard predicate with the wrong domain/range.
STANDARD_REL_PREDICATES = {
    "contains": SKOS.narrower,
    "is_a": RDFS.subClassOf,
    "implements": PROV.wasDerivedFrom,
    "derives_from": PROV.wasDerivedFrom,
    # data-product membership: the product (dcat:Dataset) has the entity as a part —
    # dcterms:hasPart is the exact DCAT-family predicate for composition.
    "member": DCTERMS.hasPart,
}


def REL_PREDICATE(rel_type: str) -> URIRef:
    """The interop predicate for a relationship type (standard where exact,
    else a UDERIA-namespaced predicate)."""
    pred = STANDARD_REL_PREDICATES.get(rel_type)
    if pred is not None:
        return pred
    return UDERIA[f"rel_{rel_type}"]


# --- IRI minting (deterministic) ------------------------------------------

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slug(text: str) -> str:
    """Deterministic, URL-safe slug. Lossy by design (the real name is always
    also carried as a literal, so import never relies on reversing a slug)."""
    s = _SLUG_RE.sub("_", str(text).strip()).strip("_")
    return s or "_"


def kg_uri(kg_id: str) -> URIRef:
    return URIRef(f"{BASE}{slug(kg_id) or 'default'}")


def entity_uri(kg_id: str, name: str, entity_type: str) -> URIRef:
    return URIRef(f"{BASE}{slug(kg_id) or 'default'}/{slug(entity_type)}/{slug(name)}")


def relationship_uri(kg_id: str, source_name: str, rel_type: str, target_name: str) -> URIRef:
    return URIRef(
        f"{BASE}{slug(kg_id) or 'default'}/rel/"
        f"{slug(source_name)}-{slug(rel_type)}-{slug(target_name)}"
    )


def metric_component_uri(metric_uri: URIRef, kind: str, name: str) -> URIRef:
    """A QB component / dimension / measure property IRI under a metric DSD."""
    return URIRef(f"{str(metric_uri)}/{kind}/{slug(name)}")


def bind_namespaces(graph) -> None:
    """Bind readable prefixes on a graph for clean Turtle output."""
    graph.bind("skos", SKOS)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("qb", QB)
    graph.bind("dcat", DCAT)
    graph.bind("dcterms", DCTERMS)
    graph.bind("prov", PROV)
    graph.bind("uderia", UDERIA)
