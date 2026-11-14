"""
resolver — deterministic logical → deployed-physical resolution for the knowledge
graph (the "ontology" support layer).

The knowledge graph carries a LOGICAL model (meaning: `entity`/`attribute`) and,
where a PDM has been deployed, a PHYSICAL layer (`table`/`column`/`ddl_artifact`,
each tagged with an ``infrastructure`` and linked to its logical counterpart by an
``implements`` edge — see ONTOLOGY_ARCHITECTURE §2.4b/§2.4c).

This module resolves a logical entity to its **deployed, queryable** physical
identifier by walking those ``implements`` edges. It is:

* **Deterministic** — one correct answer per entity; the LLM never traverses.
* **Backend-agnostic** — pure ``GraphStore`` API (``get_entity_by_name`` +
  ``get_relationships``), no raw SQL, no I/O beyond the store.
* **Gracefully absent** — returns ``None`` / an empty deployment when the entity
  has no deployed realization (a logical-only KG), so callers degrade to "no
  enrichment". The knowledge graph stays strictly additive: delete it and the
  engines behave identically.

Consumers (the KG component's enrichment formatter + ``compile_metric`` / ``query``
actions) call this so every engine receives already-resolved, capability-aware
knowledge — they never script the traversal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

# Infrastructures that describe a *reference* model (declared, not actually
# deployed/queryable). When several physical layers implement the same logical
# node, a real deployed layer is preferred over these.
_REFERENCE_INFRA = {"databricks_unity_catalog"}


@dataclass
class DeployedTable:
    """A logical table's deployed physical realization."""
    identifier: str            # e.g. "restaurants_mvm_test.campaign" (byte-identical to the deployed table)
    infrastructure: str        # e.g. "teradata"


@dataclass
class MetricDeployment:
    """A logical metric's deployed realization, if any.

    A metric is realized physically in one of several ways (``kind``):

    * ``"view"``    — a computed ``ddl_artifact`` metric view (aggregated on read).
    * ``"column"``  — a **direct mapping to an existing column** (the value already
                      lives in a table column; read it directly, no aggregation).
    * ``"table"``   — a materialized/precomputed physical table.
    * ``"base_table"`` — no direct realization, but the metric ``measures`` a logical
                      table that IS deployed → compile from that base table.
    * ``"none"``    — un-deployed (logical-only KG).
    """
    kind: str = "none"
    view: Optional[str] = None          # deployed metric view (ddl_artifact name), directly queryable
    column: Optional[str] = None        # deployed column the metric maps to directly ("<db>.<table>.<col>")
    base_table: Optional[str] = None    # deployed base table the metric aggregates / the column lives on
    infrastructure: Optional[str] = None

    @property
    def is_deployed(self) -> bool:
        return bool(self.view or self.column or self.base_table)


@dataclass
class EntityCapabilities:
    """Capability-aware annotation for one logical entity, surfaced in the
    enrichment so the engine can self-direct (e.g. "query this", "compile this")."""
    deployed_as: Optional[str] = None        # deployed/queryable identifier
    infrastructure: Optional[str] = None
    metric_view: Optional[str] = None        # metrics: deployed view to query directly
    metric_column: Optional[str] = None      # metrics: column the metric maps to directly (read it)
    metric_base_table: Optional[str] = None  # metrics: deployed base table
    metric_realization: Optional[str] = None # metrics: "view" | "column" | "table" | "base_table"
    compilable: bool = False                 # metrics: aggregate definition → compile_metric works
    # The declared measure/dimension NAMES (the compile_metric contract): a metric is
    # compiled by these names, never by a column name — so the enrichment must show
    # them or the model has nothing to copy (prod d9a60903 asked for a column).
    metric_measures: List[str] = field(default_factory=list)
    metric_dimensions: List[str] = field(default_factory=list)

    @property
    def has_any(self) -> bool:
        return bool(self.deployed_as or self.metric_view or self.metric_column
                    or self.metric_base_table or self.compilable)


def _prefer(impls: List[Tuple[str, Optional[str], Optional[dict]]]):
    """From candidate physical implementers, prefer a genuinely-deployed layer over
    a reference one (deterministic, stable order)."""
    if not impls:
        return None
    deployed = [i for i in impls if (i[1] or "") not in _REFERENCE_INFRA]
    return (deployed or impls)[0]


async def _implementers(
    store: Any,
    logical_entity: dict,
    source_type: Optional[str] = None,
    infrastructure: Optional[str] = None,
) -> List[Tuple[str, Optional[str], Optional[dict]]]:
    """Physical nodes that ``implements`` the given logical entity.

    Returns ``[(physical_name, infrastructure, physical_entity), ...]`` filtered by
    optional ``source_type`` (e.g. ``table``/``ddl_artifact``) and
    ``infrastructure``.
    """
    eid = logical_entity.get("id")
    if eid is None:
        return []
    rels = await store.get_relationships(eid, "both")
    out: List[Tuple[str, Optional[str], Optional[dict]]] = []
    seen = set()
    for r in rels or []:
        if (r.get("relationship_type") == "implements"
                and r.get("target_name") == logical_entity.get("name")
                and (source_type is None or r.get("source_type") == source_type)):
            pname = r.get("source_name")
            if not pname or pname in seen:
                continue
            seen.add(pname)
            phys = store.get_entity_by_name(pname, r.get("source_type"))
            infra = ((phys or {}).get("properties") or {}).get("infrastructure")
            if infrastructure is None or infra == infrastructure:
                out.append((pname, infra, phys))
    return out


async def resolve_deployed_table(
    store: Any, logical_table_name: str, infrastructure: Optional[str] = None
) -> Optional[DeployedTable]:
    """Resolve a logical table → its deployed physical table identifier, or ``None``."""
    ent = store.get_entity_by_name(logical_table_name, "entity")
    if not ent:
        return None
    chosen = _prefer(await _implementers(store, ent, source_type="table",
                                         infrastructure=infrastructure))
    if not chosen:
        return None
    name, infra, _ = chosen
    return DeployedTable(identifier=name, infrastructure=infra or "")


def _base_table_of_column(column_identifier: str) -> Optional[str]:
    """Derive the owning ``<db>.<table>`` from a deployed ``<db>.<table>.<col>``
    identifier (the byte-identical naming invariant guarantees simple dotted parts)."""
    if not column_identifier:
        return None
    parts = column_identifier.split(".")
    return ".".join(parts[:-1]) if len(parts) >= 2 else None


async def resolve_metric_deployment(store: Any, metric_name: str) -> MetricDeployment:
    """Resolve a logical metric → its deployed physical realization.

    Resolution order (each path a distinct ``kind``):
    1. ``view``   — a ``ddl_artifact`` (computed metric view) ``implements`` the metric.
    2. ``column`` — a physical ``column`` ``implements`` the metric directly (the value
                    already lives in that column; read it, no aggregation).
    3. ``table``  — a physical ``table`` ``implements`` the metric (materialized).
    4. ``base_table`` — the metric ``measures`` a logical table that IS deployed →
                    compile from that base table.
    Returns an empty (``kind="none"``) result when nothing is deployed.
    """
    ent = store.get_entity_by_name(metric_name, "metric")
    if not ent:
        return MetricDeployment()

    # 1. Computed view (ddl_artifact) implementing this metric.
    chosen = _prefer(await _implementers(store, ent, source_type="ddl_artifact"))
    if chosen:
        view_name, infra, art = chosen
        base = ((art or {}).get("properties") or {}).get("source_table")
        return MetricDeployment(kind="view", view=view_name, base_table=base, infrastructure=infra)

    # 2. Direct column mapping — a physical column implementing this metric.
    chosen = _prefer(await _implementers(store, ent, source_type="column"))
    if chosen:
        col_name, infra, col = chosen
        base = (((col or {}).get("properties") or {}).get("source_table")
                or _base_table_of_column(col_name))
        return MetricDeployment(kind="column", column=col_name, base_table=base, infrastructure=infra)

    # 3. Materialized physical table implementing this metric.
    chosen = _prefer(await _implementers(store, ent, source_type="table"))
    if chosen:
        tbl_name, infra, _ = chosen
        return MetricDeployment(kind="table", base_table=tbl_name, infrastructure=infra)

    # 4. Fallback: metric --measures--> logical table → its deployed physical table.
    eid = ent.get("id")
    rels = await store.get_relationships(eid, "both") if eid is not None else []
    for r in rels or []:
        if (r.get("relationship_type") == "measures"
                and r.get("source_name") == metric_name
                and r.get("target_type") == "entity"):
            dt = await resolve_deployed_table(store, r["target_name"])
            if dt:
                return MetricDeployment(kind="base_table", base_table=dt.identifier,
                                        infrastructure=dt.infrastructure)
    return MetricDeployment()


def _declared_names(items: Any) -> List[str]:
    """The names of a metric's declared measures/dimensions, in declared order; tolerant
    of the walker's dict shape ({name, expr}) and a bare string list."""
    out: List[str] = []
    for it in items or []:
        n = (it.get("name") if isinstance(it, dict) else it)
        n = str(n or "").strip()
        if n and n not in out:
            out.append(n)
    return out


async def resolve_capabilities(store: Any, entity: dict) -> EntityCapabilities:
    """Capability-aware annotation for one logical entity (dispatches by type).

    Pure resolution — surfaces *what is deployed* and *what can be done* so the
    engine self-directs; it never decides or executes anything.
    """
    etype = entity.get("entity_type")
    caps = EntityCapabilities()
    if etype == "entity":
        dt = await resolve_deployed_table(store, entity.get("name"))
        if dt:
            caps.deployed_as = dt.identifier
            caps.infrastructure = dt.infrastructure
    elif etype == "metric":
        dep = await resolve_metric_deployment(store, entity.get("name"))
        caps.metric_view = dep.view
        caps.metric_column = dep.column
        caps.metric_base_table = dep.base_table
        caps.metric_realization = dep.kind if dep.is_deployed else None
        caps.infrastructure = dep.infrastructure
        # Compilable iff the metric carries an aggregate definition AND is not a direct
        # column passthrough (a column-mapped metric is *read*, not compiled).
        props = entity.get("properties") or {}
        has_def = bool(props.get("measures")) or bool(props.get("measures_count"))
        caps.compilable = has_def and dep.kind != "column"
        caps.metric_measures = _declared_names(props.get("measures"))
        caps.metric_dimensions = _declared_names(props.get("dimensions"))
    return caps
