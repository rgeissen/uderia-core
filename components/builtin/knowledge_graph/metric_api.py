"""
metric_api — the governed metric **semantic core** (Tier 4.2 / T3).

A metric is *defined once* in the ontology (a KG ``metric`` node carrying its
dimensions/measures/source, parsed from the source model) and *consumed everywhere*:

* **AI** — the ``TDA_KnowledgeGraph`` ``compile_metric`` action (the agent picks a slice).
* **BI** — the governed REST metric API (`/v1/profiles/<id>/metrics/…`) a BI tool calls.

Both consumers route through **this one module**, so the definition, the deployed-target
resolution, and the compiled SQL can never drift between the AI and BI faces. It is:

* **Pure & deterministic** — no LLM, no I/O beyond the ``GraphStore`` + the pure
  ``metric_compiler``. One correct compilation per (metric, slice).
* **Capability-aware** — resolves the *deployed* realization (computed view · direct
  column · materialized table · base-table aggregation) via :mod:`resolver`, so the
  emitted SQL targets the real, queryable object, not the logical name.
* **Gracefully absent** — a logical-only KG (no deployed layer) still yields the
  definition + a compiled SELECT against the logical source; nothing raises.

This module owns *semantics*, not transport or governance: the REST layer adds
auth/tier-gating/ownership + the ontology's trust/conformance surfacing on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trusted_data_agent.dbdeploy.metric_compiler import (
    compile_metric, resolve_metric_from_entity, MetricCompileError,
)
from .resolver import resolve_metric_deployment, MetricDeployment


@dataclass
class MetricSemantics:
    """The full, consumer-agnostic semantic picture of one metric (± a requested slice).

    ``found`` False ⇒ no such metric node. ``error`` set ⇒ a definite, user-facing reason
    the slice could not be compiled (e.g. no parsed definition); the definition fields may
    still be populated for inspection.
    """
    name: str
    found: bool = False
    error: Optional[str] = None

    # ── the definition (define once) ──
    description: Optional[str] = None
    metric_source: Optional[str] = None          # logical source (catalog.schema.table)
    dimensions: List[Dict[str, Any]] = field(default_factory=list)   # [{name, expr}]
    measures: List[Dict[str, Any]] = field(default_factory=list)     # [{name, expr, additive}]

    # ── the deployed realization ──
    realization: str = "none"                    # view | column | table | base_table | none
    deployed_view: Optional[str] = None          # queryable metric view
    deployed_column: Optional[str] = None        # direct column ("<db>.<table>.<col>")
    source_table: Optional[str] = None           # deployed base table to aggregate / that the column lives on
    infrastructure: Optional[str] = None
    is_deployed: bool = False
    compilable: bool = False                     # aggregate definition present (not a column passthrough)

    # ── the compiled slice (consume everywhere) ──
    compiled_sql: Optional[str] = None
    selected_dimensions: List[str] = field(default_factory=list)
    selected_measures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ── governed access scope (the deployed databases this metric touches) ──
    access_scope: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        """The catalog-row projection — enough for a BI tool to browse without compiling."""
        return {
            "name": self.name,
            "description": self.description,
            "dimension_count": len(self.dimensions),
            "measure_count": len(self.measures),
            "realization": self.realization,
            "queryable": self.is_deployed,
            "compilable": self.compilable,
            "deployed_view": self.deployed_view,
            "deployed_column": self.deployed_column,
            "source_table": self.source_table,
            "infrastructure": self.infrastructure,
            "access_scope": self.access_scope,
        }

    def to_dict(self) -> Dict[str, Any]:
        """The full metric response (definition + deployment + compiled slice + scope)."""
        return {
            "name": self.name,
            "found": self.found,
            "error": self.error,
            "definition": {
                "description": self.description,
                "metric_source": self.metric_source,
                "dimensions": self.dimensions,
                "measures": self.measures,
            },
            "deployment": {
                "realization": self.realization,
                "deployed_view": self.deployed_view,
                "deployed_column": self.deployed_column,
                "source_table": self.source_table,
                "infrastructure": self.infrastructure,
                "is_deployed": self.is_deployed,
                "compilable": self.compilable,
            },
            "compiled": {
                "sql": self.compiled_sql,
                "dimensions": self.selected_dimensions,
                "measures": self.selected_measures,
                "warnings": self.warnings,
            },
            "access_scope": self.access_scope,
        }


def _db_of(identifier: Optional[str]) -> Optional[str]:
    """The leading database qualifier of a deployed ``db.table[.col]`` identifier."""
    if not identifier:
        return None
    parts = [p for p in str(identifier).split(".") if p]
    return parts[0] if len(parts) >= 2 else None


def _access_scope(dep: MetricDeployment, source_table: Optional[str]) -> List[str]:
    """The deployed databases a metric reads from — its governed access scope. Empty for a
    logical-only metric (no deployed realization to scope)."""
    dbs = set()
    for ident in (dep.view, dep.column, dep.base_table, source_table):
        db = _db_of(ident)
        if db:
            dbs.add(db)
    return sorted(dbs)


def _column_read_sql(dep: MetricDeployment, mdef: Dict[str, Any], source_table: str,
                     selected_dimensions: Optional[List[str]]) -> str:
    """A direct-column metric is *read*, not aggregated — emit a plain SELECT (no GROUP BY).
    Selected/defined dimensions ride along as columns. Mirrors the AI-side handler exactly."""
    metric_col = (dep.column or "").rsplit(".", 1)[-1]
    dims = [d for d in (mdef.get("dimensions") or []) if d.get("name")]
    if selected_dimensions is not None:
        want = {str(d).strip().lower() for d in selected_dimensions}
        dims = [d for d in dims if d["name"].strip().lower() in want]
    select_items = [f"{(d.get('expr') or d['name'])} AS {d['name']}" for d in dims]
    select_items.append(metric_col)
    return f"SELECT {', '.join(select_items)} FROM {source_table};"


async def resolve_metric_semantics(
    store: Any,
    metric_name: str,
    *,
    dimensions: Optional[List[str]] = None,
    measures: Optional[List[str]] = None,
    compile_slice: bool = True,
) -> MetricSemantics:
    """Resolve a metric's full semantics (± compile a requested slice).

    ``dimensions``/``measures`` are optional case-insensitive name subsets (``None`` = all;
    ``[]`` = none — a coarser grain / measure selection). ``compile_slice=False`` returns the
    definition + deployment only (the catalog path — no SQL work).

    Never raises for a *known* condition (missing metric, un-enriched definition, un-deployed):
    those come back as ``found=False`` / ``error=…`` so both the REST layer and the component
    can render an honest result.
    """
    name = (metric_name or "").strip()
    out = MetricSemantics(name=name)
    if not name:
        out.error = "a metric name is required"
        return out

    entity = store.get_entity_by_name(name, "metric")
    if not entity or entity.get("entity_type") != "metric":
        out.error = f"no metric named '{name}' in this ontology"
        return out
    out.found = True

    props = entity.get("properties") or {}
    out.description = props.get("description")
    out.metric_source = props.get("metric_source")
    mdef, logical_source = resolve_metric_from_entity(props)
    out.dimensions = list(mdef.get("dimensions") or [])
    out.measures = list(mdef.get("measures") or [])

    # Deployed realization (walk `implements` to the physical layer).
    dep = await resolve_metric_deployment(store, name)
    out.realization = dep.kind
    out.deployed_view = dep.view
    out.deployed_column = dep.column
    out.infrastructure = dep.infrastructure
    out.is_deployed = dep.is_deployed
    out.source_table = dep.base_table or logical_source
    out.compilable = bool(out.measures) and dep.kind != "column"
    out.access_scope = _access_scope(dep, out.source_table)

    if not compile_slice:
        return out

    # ── compile the requested slice ──
    if dep.kind == "column" and dep.column:
        if not out.source_table:
            out.error = f"could not resolve a source table for metric '{name}'"
            return out
        out.compiled_sql = _column_read_sql(dep, mdef, out.source_table, dimensions)
        out.selected_dimensions = [d["name"] for d in out.dimensions
                                   if dimensions is None
                                   or d["name"].strip().lower() in {str(x).strip().lower() for x in dimensions}]
        out.selected_measures = [dep.column.rsplit(".", 1)[-1]]
        return out

    if not out.measures:
        _formula = str(props.get("formula") or "").strip()
        out.error = (f"metric '{name}' has no parsed definition, so it cannot be compiled"
                     + (f". Its declared formula is: {_formula} — implement it directly in SQL "
                        f"with your data query tool (join through shared columns as needed)"
                        if _formula else " (rebuild the ontology with metrics enabled)"))
        return out
    if not out.source_table:
        out.error = f"could not resolve a source table for metric '{name}'"
        return out
    try:
        cr = compile_metric(mdef, source_table=out.source_table,
                            selected_dimensions=dimensions, selected_measures=measures)
    except MetricCompileError as exc:
        out.error = str(exc)
        return out
    out.compiled_sql = cr.sql
    out.selected_dimensions = cr.dimensions
    out.selected_measures = cr.measures
    out.warnings = cr.warnings
    return out


async def list_metric_catalog(store: Any, *, limit: int = 2000) -> List[Dict[str, Any]]:
    """The metric catalog for a KG — every ``metric`` node as a summary row (no SQL compiled).
    Deterministic, stable order (by name)."""
    try:
        nodes = await store.list_entities(entity_type="metric", limit=limit)
    except Exception:
        nodes = []
    rows: List[Dict[str, Any]] = []
    for n in nodes or []:
        nm = n.get("name")
        if not nm:
            continue
        sem = await resolve_metric_semantics(store, nm, compile_slice=False)
        if sem.found:
            rows.append(sem.summary())
    rows.sort(key=lambda r: (r.get("name") or "").lower())
    return rows
