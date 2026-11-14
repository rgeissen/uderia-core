"""
catalog — DCAT wrapper helpers. Surfaces a metric as a discoverable
``dcat:Dataset`` (and its deployed view as a ``dcat:DataService``) so a catalog
crawler that speaks DCAT can find Uderia's semantic resources.

Pure: no I/O, no LLM.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from rdflib import Graph, Literal

from .vocab import DCAT, DCTERMS, RDF, entity_uri


def add_metric_catalog(
    graph: Graph,
    *,
    kg_id: str,
    name: str,
    props: Dict[str, Any],
    deployment: Optional[Dict[str, Any]] = None,
) -> None:
    """Add DCAT catalog triples for one metric (Dataset + optional DataService
    for the deployed, directly-queryable view)."""
    dsd = entity_uri(kg_id, name, "metric")
    graph.add((dsd, RDF.type, DCAT.Dataset))
    graph.add((dsd, DCTERMS.title, Literal(name)))
    desc = props.get("description")
    if desc:
        graph.add((dsd, DCTERMS.description, Literal(str(desc))))
    if deployment and deployment.get("view"):
        view_iri = entity_uri(kg_id, deployment["view"], "ddl_artifact")
        graph.add((view_iri, RDF.type, DCAT.DataService))
        graph.add((view_iri, DCTERMS.title, Literal(str(deployment["view"]))))
        graph.add((dsd, DCAT.distribution, view_iri))
