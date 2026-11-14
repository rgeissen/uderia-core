"""
conformance — deterministic, zero-LLM validator that checks whether a knowledge
graph conforms to the canonical **ontology topology** (the foundation document
``docs/Architecture/ONTOLOGY_ARCHITECTURE.md``).

This is the central quality gate of Tier 4.3 ("ontology as a governed product"):
a graph cannot become a *governed ontology product* unless it is a valid ontology
model. The validator is pure (no I/O, no LLM) and returns a structured report —
``conformant`` / ``partial`` / ``nonconformant`` — with **typed, counted issues**
(honest about *what* is wrong, never a bare boolean).

Topology rules enforced (ONTOLOGY_ARCHITECTURE §2.2/§2.8):
  * layer model — logical (`entity`/`attribute`/…) vs physical (`table`/`column`/…),
  * the logical backbone exists,
  * physical → logical binding via `implements` (table→entity, ddl_artifact→metric)
    and physical containment (`contains`: table→column),
  * domain coherence,
  * no dangling edges, no unknown types.

The canonical type sets mirror ``graph_store.ENTITY_TYPES`` / ``RELATIONSHIP_TYPES``
(the single definition site); ``test_kg_conformance`` asserts they stay in sync so
drift is caught in CI rather than silently diverging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --- Canonical topology: imported, never re-declared (see kgstore/types.py) ---
from .types import (  # noqa: E402,F401  (re-exported for callers)
    ENTITY_TYPES,
    LOGICAL_TYPES,
    PHYSICAL_TYPES,
    RELATIONSHIP_TYPES,
)

# Severity → which status it forces.
HIGH, MEDIUM, LOW = "high", "medium", "low"

# Status constants (consumed by the governance lifecycle gate).
CONFORMANT = "conformant"
PARTIAL = "partial"
NONCONFORMANT = "nonconformant"

_MAX_REFS = 10  # cap example refs per issue so a report stays bounded


@dataclass
class ConformanceIssue:
    code: str
    severity: str            # high | medium | low
    message: str
    count: int = 0
    refs: List[str] = field(default_factory=list)  # example offending names (capped)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message,
                "count": self.count, "refs": self.refs}


@dataclass
class ConformanceReport:
    status: str                       # conformant | partial | nonconformant
    issues: List[ConformanceIssue] = field(default_factory=list)
    entity_count: int = 0
    relationship_count: int = 0

    @property
    def conformant(self) -> bool:
        return self.status == CONFORMANT

    def severity_counts(self) -> Dict[str, int]:
        out = {HIGH: 0, MEDIUM: 0, LOW: 0}
        for i in self.issues:
            out[i.severity] = out.get(i.severity, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "entity_count": self.entity_count,
            "relationship_count": self.relationship_count,
            "severity_counts": self.severity_counts(),
            "issues": [i.to_dict() for i in self.issues],
        }


def layer_for_type(entity_type: str) -> Optional[str]:
    """Canonical layer of an entity type (deterministic), or None if unknown."""
    if entity_type in LOGICAL_TYPES:
        return "logical"
    if entity_type in PHYSICAL_TYPES:
        return "physical"
    return None


def check_conformance(
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> ConformanceReport:
    """Validate a KG against the canonical ontology topology. Pure + deterministic."""
    entities = entities or []
    relationships = relationships or []
    issues: List[ConformanceIssue] = []

    # Empty ontology — nothing to govern as a product.
    if not entities:
        issues.append(ConformanceIssue("empty_ontology", HIGH,
                                       "the ontology has no entities", 0, []))
        return ConformanceReport(_status_from(issues), issues, 0, len(relationships))

    # Indexes.
    names_by_type: Dict[str, set] = {}
    all_names: set = set()
    for e in entities:
        n, t = e.get("name"), e.get("entity_type")
        if n is None:
            continue
        all_names.add(n)
        names_by_type.setdefault(t, set()).add(n)

    # Build directed edge maps keyed by (rel_type): incoming/outgoing by node name.
    out_edges: List[Tuple[str, str, str, str, str]] = []  # (rtype, sname, stype, tname, ttype)
    for r in relationships:
        out_edges.append((r.get("relationship_type"), r.get("source_name"), r.get("source_type"),
                          r.get("target_name"), r.get("target_type")))

    def has_out(src_name: str, rtype: str, target_type: Optional[str] = None) -> bool:
        for rt, sn, st, tn, tt in out_edges:
            if sn == src_name and rt == rtype and (target_type is None or tt == target_type):
                return True
        return False

    def has_in(tgt_name: str, rtype: str, source_type: Optional[str] = None) -> bool:
        for rt, sn, st, tn, tt in out_edges:
            if tn == tgt_name and rt == rtype and (source_type is None or st == source_type):
                return True
        return False

    # 1. Unknown types (HIGH).
    _collect(issues, "unknown_entity_type", HIGH, "entity has a type outside the canonical vocabulary",
             [e.get("name") for e in entities if e.get("entity_type") not in ENTITY_TYPES])
    _collect(issues, "unknown_relationship_type", HIGH, "relationship has a type outside the canonical vocabulary",
             [f"{r.get('source_name')}-{r.get('relationship_type')}-{r.get('target_name')}"
              for r in relationships if r.get("relationship_type") not in RELATIONSHIP_TYPES])

    # 2. Layer tagging — a present `layer` property must match the type's canonical layer (MEDIUM).
    mism = []
    from .types import KEPT_LAYERS_ON_REPLACE as _KEPT   # PROP-008: authored / observed sit ABOVE the walked model
    for e in entities:
        canon = layer_for_type(e.get("entity_type"))
        lyr = (e.get("properties") or {}).get("layer")
        if lyr and str(lyr) in _KEPT:
            continue   # a draft or an observation is not a mis-tagged walked node
        if canon and lyr and str(lyr) != canon:
            mism.append(e.get("name"))
    _collect(issues, "layer_mismatch", MEDIUM,
             "entity's `layer` property contradicts its type's canonical layer", mism)

    # 3. Logical backbone — at least one logical entity must exist (HIGH).
    logical_present = any(layer_for_type(e.get("entity_type")) == "logical" for e in entities)
    if not logical_present:
        issues.append(ConformanceIssue("no_logical_backbone", HIGH,
                                       "no logical-layer entity exists (a physical realization with no model)", 0, []))

    # 4. Physical → logical binding.
    #    table implements an entity (HIGH); ddl_artifact implements a metric (MEDIUM);
    #    column is contained by a table (MEDIUM).
    _collect(issues, "table_no_implements", HIGH,
             "physical `table` does not `implements` any logical `entity`",
             [n for n in names_by_type.get("table", set()) if not has_out(n, "implements", "entity")])
    # a ddl_artifact with a ``purpose`` (an action ledger, a tuning structure — PROP-008 B1/C1) is
    # an operational object the platform built ON a table, not a metric's realisation: it is
    # bound by ``derives_from`` a table; only a metric view must ``implements`` a metric
    purposeful = {e.get("name") for e in entities
                  if e.get("entity_type") == "ddl_artifact" and (e.get("properties") or {}).get("purpose")}
    _collect(issues, "ddl_artifact_no_metric", MEDIUM,
             "physical `ddl_artifact` does not `implements` a `metric`",
             [n for n in names_by_type.get("ddl_artifact", set()) - purposeful if not has_out(n, "implements", "metric")])
    _collect(issues, "ddl_artifact_unbound", MEDIUM,
             "operational `ddl_artifact` (a ledger or a tuning structure) does not `derives_from` a `table`",
             [n for n in purposeful if not has_out(n, "derives_from", "table")])
    _collect(issues, "column_no_parent", MEDIUM,
             "physical `column` is not `contains`-ed by a `table`",
             [n for n in names_by_type.get("column", set()) if not has_in(n, "contains", "table")])

    # 5. Attributes/metrics attach to the logical model.
    _collect(issues, "orphan_attribute", MEDIUM,
             "logical `attribute` has no `contains` edge from an `entity`",
             [n for n in names_by_type.get("attribute", set())
              if not (has_in(n, "contains", "entity") or has_in(n, "has_property", "entity"))])
    orphan_metrics = []
    for e in entities:
        if e.get("entity_type") != "metric":
            continue
        n = e.get("name")
        props = e.get("properties") or {}
        if not has_out(n, "measures") and not (props.get("owner_product") or props.get("metric_source")):
            orphan_metrics.append(n)
    _collect(issues, "orphan_metric", LOW,
             "`metric` has neither a `measures` edge nor an owner/source property", orphan_metrics)

    # 6. Domain coherence (LOW) — an entity's `domain` property should name a declared `domain` node.
    declared_domains = names_by_type.get("domain", set())
    if declared_domains:
        bad = [e.get("name") for e in entities
               if (e.get("properties") or {}).get("domain")
               and (e.get("properties") or {}).get("domain") not in declared_domains]
        _collect(issues, "domain_incoherent", LOW,
                 "entity references a `domain` that is not a declared domain node", bad)

    # 7. No dangling edges (HIGH) — every endpoint resolves to an existing entity.
    dangling = []
    for r in relationships:
        if r.get("source_name") not in all_names or r.get("target_name") not in all_names:
            dangling.append(f"{r.get('source_name')}-{r.get('relationship_type')}-{r.get('target_name')}")
    _collect(issues, "dangling_edge", HIGH,
             "relationship endpoint does not resolve to an existing entity", dangling)

    return ConformanceReport(_status_from(issues), issues, len(entities), len(relationships))


def _collect(issues: List[ConformanceIssue], code: str, severity: str, message: str,
             offenders: List[Any]) -> None:
    offenders = [o for o in offenders if o is not None]
    if offenders:
        issues.append(ConformanceIssue(code, severity, message, len(offenders),
                                       [str(o) for o in offenders[:_MAX_REFS]]))


def _status_from(issues: List[ConformanceIssue]) -> str:
    if any(i.severity == HIGH for i in issues):
        return NONCONFORMANT
    if issues:
        return PARTIAL
    return CONFORMANT
