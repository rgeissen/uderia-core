"""
CI-pure tests for the KG deterministic resolver (logical → deployed-physical).

No live DB: a fake store implements the two GraphStore primitives the resolver
uses (get_entity_by_name + async get_relationships). Asserts the deterministic
logical→physical resolution, capability annotation, reference-vs-deployed
preference, and — critically — graceful absence (logical-only KG → no deployment),
which is what keeps the knowledge graph additive.

Run:  python test/test_kg_resolver.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (for `components`)

from components.builtin.knowledge_graph.resolver import (  # noqa: E402
    resolve_deployed_table, resolve_metric_deployment, resolve_capabilities,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


def run(coro):
    return asyncio.run(coro)


class FakeStore:
    """Minimal GraphStore stand-in: get_entity_by_name (sync) + get_relationships (async)."""

    def __init__(self, entities, rels):
        self._by_key = {(e["name"], e["entity_type"]): e for e in entities}
        self._by_name = {}
        for e in entities:
            self._by_name.setdefault(e["name"], e)
        self._rels_by_id = {}
        for r in rels:
            self._rels_by_id.setdefault(r["source_entity_id"], []).append(r)
            self._rels_by_id.setdefault(r["target_entity_id"], []).append(r)

    def get_entity_by_name(self, name, entity_type=None):
        if entity_type is not None:
            return self._by_key.get((name, entity_type))
        return self._by_name.get(name)

    async def get_relationships(self, entity_id, direction="both"):
        return self._rels_by_id.get(entity_id, [])
# Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
# a double unfaithful in the dimension under test measures nothing (Sep 2026).
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from kg_double_fidelity import double_mismatches as _dm
from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
check("FakeStore double matches the real GraphStore calling convention", not _dm(_RealGS, FakeStore))


def _ent(eid, name, etype, **props):
    return {"id": eid, "name": name, "entity_type": etype, "properties": props}


def _rel(src, src_t, sid, tgt, tgt_t, tid, rtype):
    return {"source_entity_id": sid, "target_entity_id": tid,
            "source_name": src, "source_type": src_t,
            "target_name": tgt, "target_type": tgt_t,
            "relationship_type": rtype}


def _deployed_graph():
    """Logical table + metric, each with a deployed Teradata physical realization."""
    entities = [
        _ent(1, "sales.order", "entity"),
        _ent(2, "sales.order_metric", "metric", measures_count=3,
             measures=[{"name": "n", "expr": "COUNT(*)"}]),
        _ent(3, "wh.order", "table", infrastructure="teradata"),
        _ent(4, "wh.order_metric_view", "ddl_artifact",
             infrastructure="teradata", source_table="wh.order", artifact_kind="metric_view"),
    ]
    rels = [
        _rel("wh.order", "table", 3, "sales.order", "entity", 1, "implements"),
        _rel("sales.order_metric", "metric", 2, "sales.order", "entity", 1, "measures"),
        _rel("wh.order_metric_view", "ddl_artifact", 4, "sales.order_metric", "metric", 2, "implements"),
    ]
    return FakeStore(entities, rels)


def test_deployed_table():
    print("\n[test_deployed_table]")
    st = _deployed_graph()
    dt = run(resolve_deployed_table(st, "sales.order"))
    check("logical table resolves to deployed physical table", dt is not None and dt.identifier == "wh.order")
    check("infrastructure carried", dt.infrastructure == "teradata")


def test_metric_deployment_via_view():
    print("\n[test_metric_deployment_via_view]")
    st = _deployed_graph()
    dep = run(resolve_metric_deployment(st, "sales.order_metric"))
    check("metric resolves its deployed view", dep.view == "wh.order_metric_view")
    check("metric resolves its deployed base table (from the view artifact)", dep.base_table == "wh.order")
    check("realization kind == view", dep.kind == "view")
    check("is_deployed True", dep.is_deployed is True)


def _column_metric_graph():
    """A metric realized as a DIRECT COLUMN MAPPING (column implements metric)."""
    entities = [
        _ent(1, "finance.accounts", "entity"),
        _ent(2, "finance.account_balance", "metric", measures_count=1,
             measures=[{"name": "balance", "expr": "balance"}]),
        _ent(3, "wh.accounts", "table", infrastructure="teradata"),
        _ent(4, "wh.accounts.balance", "column", infrastructure="teradata"),
    ]
    rels = [
        _rel("wh.accounts", "table", 3, "finance.accounts", "entity", 1, "implements"),
        _rel("finance.account_balance", "metric", 2, "finance.accounts", "entity", 1, "measures"),
        _rel("wh.accounts.balance", "column", 4, "finance.account_balance", "metric", 2, "implements"),
    ]
    return FakeStore(entities, rels)


def test_metric_deployment_via_column():
    print("\n[test_metric_deployment_via_column]")
    st = _column_metric_graph()
    dep = run(resolve_metric_deployment(st, "finance.account_balance"))
    check("realization kind == column", dep.kind == "column")
    check("metric resolves its deployed column", dep.column == "wh.accounts.balance")
    check("base table derived from column identifier", dep.base_table == "wh.accounts")
    check("no view for a column-mapped metric", dep.view is None)
    check("is_deployed True", dep.is_deployed is True)
    check("infrastructure carried", dep.infrastructure == "teradata")


def test_capabilities_column_metric():
    print("\n[test_capabilities_column_metric]")
    st = _column_metric_graph()
    caps = run(resolve_capabilities(st, st.get_entity_by_name("finance.account_balance", "metric")))
    check("metric capability: deployed column", caps.metric_column == "wh.accounts.balance")
    check("metric capability: realization == column", caps.metric_realization == "column")
    check("column-mapped metric is read, NOT compiled", caps.compilable is False)
    check("metric capability: base table", caps.metric_base_table == "wh.accounts")


def test_capabilities_carry_declared_names():
    """A compilable metric is compiled BY ITS DECLARED NAMES, never by a column name — so
    the capability that feeds the enrichment must carry them (prod d9a60903: with no names
    shown the model asked for a column and hit a dead-end refusal)."""
    print("\n[test_capabilities_carry_declared_names]")
    from components.builtin.knowledge_graph.resolver import _declared_names
    st = _column_metric_graph()
    ent = dict(st.get_entity_by_name("finance.account_balance", "metric"))
    ent["properties"] = dict(ent.get("properties") or {})
    ent["properties"]["measures"] = [{"name": "Total Balance", "expr": "SUM(balance)"},
                                     {"name": "Row Count", "expr": "COUNT(*)"},
                                     {"name": "Total Balance", "expr": "dup"}]
    ent["properties"]["dimensions"] = [{"name": "Account Type", "expr": "acct_type"}, "Region"]
    caps = run(resolve_capabilities(st, ent))
    check("measure NAMES carried in declared order, de-duplicated",
          caps.metric_measures == ["Total Balance", "Row Count"])
    check("dimension NAMES carried (dict and bare-string shapes both)",
          caps.metric_dimensions == ["Account Type", "Region"])
    check("no definition → empty lists, never None",
          _declared_names(None) == [] and _declared_names([{"expr": "x"}]) == [] and _declared_names([" "]) == [])
    tbl = run(resolve_capabilities(st, {"entity_type": "entity", "name": "nope"}))
    check("a non-metric entity carries no name lists", tbl.metric_measures == [] and tbl.metric_dimensions == [])


def test_metric_deployment_via_table():
    print("\n[test_metric_deployment_via_table]")
    # A materialized physical table implementing the metric (no view, no column).
    entities = [
        _ent(1, "sales.daily", "entity"),
        _ent(2, "sales.daily_total", "metric", measures_count=1, measures=[{"name": "t", "expr": "SUM(amt)"}]),
        _ent(3, "wh.daily_total_mat", "table", infrastructure="teradata"),
    ]
    rels = [_rel("wh.daily_total_mat", "table", 3, "sales.daily_total", "metric", 2, "implements")]
    st = FakeStore(entities, rels)
    dep = run(resolve_metric_deployment(st, "sales.daily_total"))
    check("realization kind == table", dep.kind == "table")
    check("materialized table surfaced as base table", dep.base_table == "wh.daily_total_mat")
    check("no view / no column", dep.view is None and dep.column is None)


def test_view_precedence_over_column():
    print("\n[test_view_precedence_over_column]")
    # Both a view AND a column implement the metric → the computed view wins.
    entities = [
        _ent(1, "finance.accounts", "entity"),
        _ent(2, "finance.account_balance", "metric", measures=[{"name": "b", "expr": "balance"}]),
        _ent(3, "wh.balance_view", "ddl_artifact", infrastructure="teradata",
             source_table="wh.accounts", artifact_kind="metric_view"),
        _ent(4, "wh.accounts.balance", "column", infrastructure="teradata"),
    ]
    rels = [
        _rel("wh.balance_view", "ddl_artifact", 3, "finance.account_balance", "metric", 2, "implements"),
        _rel("wh.accounts.balance", "column", 4, "finance.account_balance", "metric", 2, "implements"),
    ]
    st = FakeStore(entities, rels)
    dep = run(resolve_metric_deployment(st, "finance.account_balance"))
    check("view takes precedence over column", dep.kind == "view" and dep.view == "wh.balance_view")


def test_metric_deployment_fallback_to_measured_table():
    print("\n[test_metric_deployment_fallback_to_measured_table]")
    # No ddl_artifact view — only the measures edge → resolve via the logical table.
    entities = [
        _ent(1, "sales.order", "entity"),
        _ent(2, "sales.order_metric", "metric", measures_count=1, measures=[{"name": "n", "expr": "COUNT(*)"}]),
        _ent(3, "wh.order", "table", infrastructure="teradata"),
    ]
    rels = [
        _rel("wh.order", "table", 3, "sales.order", "entity", 1, "implements"),
        _rel("sales.order_metric", "metric", 2, "sales.order", "entity", 1, "measures"),
    ]
    st = FakeStore(entities, rels)
    dep = run(resolve_metric_deployment(st, "sales.order_metric"))
    check("no view → base table via measured logical table", dep.base_table == "wh.order")
    check("no view surfaced", dep.view is None)
    check("realization kind == base_table", dep.kind == "base_table")


def test_capabilities_metric_and_table():
    print("\n[test_capabilities_metric_and_table]")
    st = _deployed_graph()
    mcaps = run(resolve_capabilities(st, st.get_entity_by_name("sales.order_metric", "metric")))
    check("metric capability: deployed view", mcaps.metric_view == "wh.order_metric_view")
    check("metric capability: deployed base table", mcaps.metric_base_table == "wh.order")
    check("metric capability: realization == view", mcaps.metric_realization == "view")
    check("metric capability: compilable", mcaps.compilable is True)
    tcaps = run(resolve_capabilities(st, st.get_entity_by_name("sales.order", "entity")))
    check("table capability: deployed_as", tcaps.deployed_as == "wh.order")
    check("table is not compilable", tcaps.compilable is False)


def test_prefers_deployed_over_reference():
    print("\n[test_prefers_deployed_over_reference]")
    # Dual-layer (deprecated): a Databricks reference PDM + a Teradata deploy both implement the table.
    entities = [
        _ent(1, "sales.order", "entity"),
        _ent(2, "dbx.sales.order", "table", infrastructure="databricks_unity_catalog"),
        _ent(3, "wh.order", "table", infrastructure="teradata"),
    ]
    rels = [
        _rel("dbx.sales.order", "table", 2, "sales.order", "entity", 1, "implements"),
        _rel("wh.order", "table", 3, "sales.order", "entity", 1, "implements"),
    ]
    st = FakeStore(entities, rels)
    dt = run(resolve_deployed_table(st, "sales.order"))
    check("prefers the deployed Teradata layer over the Databricks reference", dt.identifier == "wh.order")


def test_graceful_absence_logical_only():
    print("\n[test_graceful_absence_logical_only]")
    # Logical-only KG: NO physical layer at all. Resolution must return None/empty,
    # so the knowledge graph stays additive (engines degrade to no enrichment).
    entities = [
        _ent(1, "sales.order", "entity"),
        _ent(2, "sales.order_metric", "metric", measures_count=2, measures=[{"name": "n", "expr": "COUNT(*)"}]),
    ]
    rels = [_rel("sales.order_metric", "metric", 2, "sales.order", "entity", 1, "measures")]
    st = FakeStore(entities, rels)
    check("undeployed table → None", run(resolve_deployed_table(st, "sales.order")) is None)
    dep = run(resolve_metric_deployment(st, "sales.order_metric"))
    check("undeployed metric → not deployed", dep.is_deployed is False)
    caps = run(resolve_capabilities(st, st.get_entity_by_name("sales.order", "entity")))
    check("undeployed table caps: nothing deployed", caps.deployed_as is None)
    mcaps = run(resolve_capabilities(st, st.get_entity_by_name("sales.order_metric", "metric")))
    check("undeployed metric still flagged compilable (definition present, no view)", mcaps.compilable is True)
    check("undeployed metric has no deployed view/base", mcaps.metric_view is None and mcaps.metric_base_table is None)


def test_missing_entity():
    print("\n[test_missing_entity]")
    st = _deployed_graph()
    check("unknown table → None", run(resolve_deployed_table(st, "no.such")) is None)
    check("unknown metric → empty deployment", run(resolve_metric_deployment(st, "no.such")).is_deployed is False)


def main():
    for fn in [
        test_deployed_table,
        test_metric_deployment_via_view,
        test_metric_deployment_via_column,
        test_capabilities_column_metric,
        test_capabilities_carry_declared_names,
        test_metric_deployment_via_table,
        test_view_precedence_over_column,
        test_metric_deployment_fallback_to_measured_table,
        test_capabilities_metric_and_table,
        test_prefers_deployed_over_reference,
        test_graceful_absence_logical_only,
        test_missing_entity,
    ]:
        fn()
    print(f"\n{'='*50}\nKG resolver: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
