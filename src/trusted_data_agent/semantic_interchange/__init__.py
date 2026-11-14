"""
semantic_interchange — bidirectional open-standards adapter for the Uderia KG
and semantic metrics (Tier 4.1, requirement T4).

Export a KG / metric to W3C standards (SKOS · OWL/RDFS · RDF Data Cube · PROV-O ·
DCAT) and parse them losslessly back into Uderia KG shape. Pure, deterministic,
zero-LLM. See docs/roadmap/TIER_4.1_OPEN_STANDARDS.md.

Public API:
    convert_kg(export_dict, vocab="rdf")    -> rdflib.Graph
    parse_kg(graph)                          -> (entities, relationships, warnings)
    convert_metric(metric_entity, kg_id=…)   -> rdflib.Graph
    parse_metric(graph)                      -> properties dict
    serialize / parse / round_trip           (from .serializers)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rdflib import Graph

from . import serializers
from .catalog import add_metric_catalog
from .kg_to_rdf import build_kg_graph
from .metric_to_qb import add_metric_dsd, metric_props_from_graph
from .rdf_to_kg import parse_graph
from .serializers import mime_for, normalize, parse, round_trip, serialize
from .vocab import QB, RDF, UDERIA, entity_uri

__all__ = [
    "convert_kg", "parse_kg", "convert_metric", "parse_metric",
    "serialize", "parse", "round_trip", "mime_for", "normalize",
    "VALID_FORMATS",
]

# Valid ?format= values. A format selects which entity layers to include: the
# full ("rdf"/"all") graph is a superset that is simultaneously valid SKOS / OWL
# / QB; the narrower values emit only the relevant layer.
VALID_FORMATS = ("rdf", "all", "skos", "owl")
_CONCEPT_TYPES = {"business_concept", "taxonomy", "domain"}


def _included_types(vocab: str) -> Optional[set]:
    """Return the set of entity_types to include, or None for 'everything'."""
    v = (vocab or "rdf").strip().lower()
    if v == "skos":
        return set(_CONCEPT_TYPES)
    if v == "owl":
        # logical layer = everything that is not purely physical
        return {"entity", "attribute", "foreign_key", "metric",
                "business_concept", "taxonomy", "domain", "database"}
    # rdf / all / unknown → everything
    return None


def _filter_export(export: Dict[str, Any], vocab: str) -> Dict[str, Any]:
    include = _included_types(vocab)
    if include is None:
        return export
    entities = [e for e in (export.get("entities") or [])
                if e.get("entity_type") in include]
    keep_names = {(e.get("name"), e.get("entity_type")) for e in entities}
    relationships = [
        r for r in (export.get("relationships") or [])
        if (r.get("source_name"), r.get("source_type")) in keep_names
        and (r.get("target_name"), r.get("target_type")) in keep_names
    ]
    out = dict(export)
    out["entities"] = entities
    out["relationships"] = relationships
    return out


def convert_kg(export: Dict[str, Any], vocab: str = "rdf") -> Graph:
    """Export v2.0 dict → rdflib.Graph. `vocab` ∈ {skos, owl, rdf/all}."""
    return build_kg_graph(_filter_export(export, vocab))


def parse_kg(graph: Graph) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """rdflib.Graph → (entities, relationships, warnings) for import_bulk."""
    return parse_graph(graph)


def convert_metric(
    metric_entity: Dict[str, Any],
    *,
    kg_id: str = "default",
    deployment: Optional[Dict[str, Any]] = None,
    include_dcat: bool = True,
    include_sql: bool = False,
) -> Graph:
    """One metric entity ({name, entity_type:'metric', properties}) → a focused
    QB + PROV (+ DCAT) graph carrying the uderia: round-trip layer."""
    name = metric_entity.get("name")
    props = metric_entity.get("properties") or {}
    # Reuse the KG export path so the uderia: lossless layer + QB structure are
    # emitted identically to a full-KG export.
    g = build_kg_graph({"kg_id": kg_id, "entities": [metric_entity], "relationships": []})
    # Add PROV lineage / SQL pointer (the KG path emits QB without deployment).
    add_metric_dsd(g, kg_id=kg_id, name=name, props=props,
                   deployment=deployment, include_sql=include_sql)
    if include_dcat:
        add_metric_catalog(g, kg_id=kg_id, name=name, props=props, deployment=deployment)
    return g


def parse_metric(graph: Graph) -> Optional[Dict[str, Any]]:
    """A QB/metric graph → the metric's {dimensions, measures, …} properties.

    Prefers the lossless uderia:properties literal; falls back to reconstructing
    {dimensions, measures} from the QB component triples.
    """
    dsd = None
    for s in graph.subjects(RDF.type, QB.DataStructureDefinition):
        dsd = s
        break
    if dsd is None:
        return None
    praw = graph.value(dsd, UDERIA.properties)
    if praw is not None:
        import json
        try:
            return json.loads(str(praw))
        except (ValueError, TypeError):
            pass
    return metric_props_from_graph(graph, dsd)
