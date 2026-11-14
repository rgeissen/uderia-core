"""
metric_to_qb — a Uderia ``metric`` node ↔ W3C RDF Data Cube (QB) + PROV-O.

A metric node carries ``dimensions: [{name, expr}]`` and
``measures: [{name, expr, additive}]`` (stamped by the industry-model walker,
see agent/kg_constructors/industry_model_walker.py). QB models exactly this:
a ``qb:DataStructureDefinition`` whose components are ``qb:DimensionProperty`` /
``qb:MeasureProperty``. Lineage (the deployed view derived from a base table)
maps to ``prov:wasDerivedFrom``.

Pure: no I/O, no LLM. The lossless backbone (the full properties dict as a
``uderia:properties`` JSON literal) is added by ``kg_to_rdf``; this module adds
the *interoperable* QB/PROV projection. ``metric_props_from_graph`` reconstructs
the dimension/measure structure from the QB triples (used as a fallback when the
``uderia:`` layer is absent).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from rdflib import Graph, Literal, URIRef

from .vocab import (
    PROV,
    QB,
    RDF,
    RDFS,
    UDERIA,
    entity_uri,
    metric_component_uri,
)


def add_metric_dsd(
    graph: Graph,
    *,
    kg_id: str,
    name: str,
    props: Dict[str, Any],
    deployment: Optional[Dict[str, Any]] = None,
    include_sql: bool = False,
) -> URIRef:
    """Add the QB DataStructureDefinition + dimension/measure components + PROV
    lineage for one metric. Returns the DSD IRI.

    `deployment` (optional): {"view": str|None, "column": str|None,
    "base_table": str|None, "infrastructure": str|None} from
    resolver.resolve_metric_deployment. A column-mapped metric (the value lives
    directly in a column) adds column-level PROV lineage.
    `include_sql`: when True, attach the deployed view's compiled-SQL pointer as
    a uderia annotation (the SQL itself is dialect-specific, not portable RDF).
    """
    dsd = entity_uri(kg_id, name, "metric")
    graph.add((dsd, RDF.type, QB.DataStructureDefinition))

    dimensions: List[Dict[str, Any]] = list(props.get("dimensions") or [])
    measures: List[Dict[str, Any]] = list(props.get("measures") or [])

    for d in dimensions:
        dname = d.get("name")
        if not dname:
            continue
        dim_prop = metric_component_uri(dsd, "dimension", dname)
        graph.add((dim_prop, RDF.type, QB.DimensionProperty))
        graph.add((dim_prop, RDFS.label, Literal(dname)))
        expr = d.get("expr")
        if expr:
            graph.add((dim_prop, RDFS.comment, Literal(str(expr))))
        comp = metric_component_uri(dsd, "comp_dim", dname)
        graph.add((comp, RDF.type, QB.ComponentSpecification))
        graph.add((comp, QB.dimension, dim_prop))
        graph.add((dsd, QB.component, comp))

    for m in measures:
        mname = m.get("name")
        if not mname:
            continue
        meas_prop = metric_component_uri(dsd, "measure", mname)
        graph.add((meas_prop, RDF.type, QB.MeasureProperty))
        graph.add((meas_prop, RDFS.label, Literal(mname)))
        expr = m.get("expr")
        if expr:
            graph.add((meas_prop, RDFS.comment, Literal(str(expr))))
        if "additive" in m:
            graph.add((meas_prop, UDERIA.additive, Literal(bool(m.get("additive")))))
        comp = metric_component_uri(dsd, "comp_meas", mname)
        graph.add((comp, RDF.type, QB.ComponentSpecification))
        graph.add((comp, QB.measure, meas_prop))
        graph.add((dsd, QB.component, comp))

    # PROV lineage — the metric is derived from its base table; the deployed
    # view (if any) is derived from the metric definition.
    if deployment:
        base_table = deployment.get("base_table")
        if base_table:
            base_iri = entity_uri(kg_id, base_table, "table")
            graph.add((dsd, PROV.wasDerivedFrom, base_iri))
        # Direct column mapping: the metric is realized by a specific column →
        # column-level lineage (more precise than the owning table alone).
        column = deployment.get("column")
        if column:
            graph.add((dsd, PROV.wasDerivedFrom, entity_uri(kg_id, column, "column")))
        view = deployment.get("view")
        if view:
            view_iri = entity_uri(kg_id, view, "ddl_artifact")
            graph.add((view_iri, PROV.wasDerivedFrom, dsd))
            if include_sql and deployment.get("compiled_sql"):
                graph.add((view_iri, UDERIA.compiledSql, Literal(str(deployment["compiled_sql"]))))

    return dsd


def metric_props_from_graph(graph: Graph, dsd: URIRef) -> Dict[str, Any]:
    """Reconstruct {dimensions, measures} from the QB triples of one DSD.

    Used as a fallback when the lossless ``uderia:properties`` literal is absent
    (e.g. a graph hand-authored against the standard). When the uderia layer is
    present, rdf_to_kg uses that instead — it is exact.
    """
    dims: List[Dict[str, Any]] = []
    meas: List[Dict[str, Any]] = []
    for comp in graph.objects(dsd, QB.component):
        for dim_prop in graph.objects(comp, QB.dimension):
            dims.append(_prop_dict(graph, dim_prop, want_additive=False))
        for meas_prop in graph.objects(comp, QB.measure):
            meas.append(_prop_dict(graph, meas_prop, want_additive=True))
    return {"dimensions": dims, "measures": meas}


def _prop_dict(graph: Graph, prop: URIRef, *, want_additive: bool) -> Dict[str, Any]:
    label = graph.value(prop, RDFS.label)
    comment = graph.value(prop, RDFS.comment)
    out: Dict[str, Any] = {"name": str(label) if label is not None else None}
    if comment is not None:
        out["expr"] = str(comment)
    if want_additive:
        add = graph.value(prop, UDERIA.additive)
        if add is not None:
            out["additive"] = bool(add.toPython()) if hasattr(add, "toPython") else bool(add)
    return out
