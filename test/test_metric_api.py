"""
Module tests — the governed metric semantic core (Tier 4.2 / T3).

CI-pure: exercises `metric_api.resolve_metric_semantics` + `list_metric_catalog` against a
fake in-memory ontology (no DB, no LLM, no warehouse). Asserts the four deployed realizations
(view · base_table · column · none), slice compilation, the catalog projection, access-scope
derivation, and honest errors. This is the SAME core the AI `compile_metric` tool and the BI
REST API both consume — so these assertions cover both faces.

Run:  python test/test_metric_api.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # for components.*

from components.builtin.knowledge_graph.metric_api import (  # noqa: E402
    resolve_metric_semantics, list_metric_catalog, MetricSemantics,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


class FakeStore:
    """A tiny ontology: logical metrics/entities + a deployed physical layer linked by
    `implements` edges — enough to exercise every resolution path deterministically."""

    kg_id = "kg-test-1"

    def __init__(self):
        # id -> entity
        self._by_id = {}
        # (name, entity_type) -> entity  AND  name -> entity (type-agnostic lookup)
        self._by_name = {}
        # entity_id -> [relationship dicts]
        self._rels = {}
        self._build()

    def _ent(self, eid, name, etype, props=None):
        e = {"id": eid, "name": name, "entity_type": etype, "properties": props or {}}
        self._by_id[eid] = e
        self._by_name[(name, etype)] = e
        self._by_name.setdefault(name, e)
        self._rels.setdefault(eid, [])
        return e

    def _rel(self, src_id, rtype, sname, stype, tname, ttype):
        r = {"relationship_type": rtype, "source_name": sname, "source_type": stype,
             "target_name": tname, "target_type": ttype}
        # attach to the source entity id (get_relationships is keyed by id)
        self._rels.setdefault(src_id, []).append(r)
        # also visible from the target entity's id (direction="both")
        tgt = self._by_name.get((tname, ttype))
        if tgt:
            self._rels.setdefault(tgt["id"], []).append(r)

    def _build(self):
        # 1) VIEW realization — a ddl_artifact metric view implements the metric.
        self._ent(1, "avg_order_value", "metric", {
            "description": "Average order value",
            "metric_source": "cat.sales.order_header",
            "dimensions": [{"name": "order_date", "expr": "order_date"}],
            "measures": [{"name": "avg_value", "expr": "AVG(total_amount)", "additive": False}],
        })
        self._ent(10, "sales_deploy.avg_order_value_v", "ddl_artifact",
                  {"infrastructure": "teradata", "source_table": "sales_deploy.order_header"})
        self._rel(10, "implements", "sales_deploy.avg_order_value_v", "ddl_artifact",
                  "avg_order_value", "metric")

        # 2) BASE_TABLE realization — metric measures a logical table that IS deployed.
        self._ent(2, "revenue_total", "metric", {
            "metric_source": "cat.sales.order_header",
            "measures": [{"name": "revenue", "expr": "SUM(total_amount)", "additive": True}],
            "dimensions": [{"name": "region", "expr": "region"}],
        })
        self._ent(3, "order_header", "entity", {})
        self._ent(11, "sales_deploy.order_header", "table", {"infrastructure": "teradata"})
        self._rel(2, "measures", "revenue_total", "metric", "order_header", "entity")
        self._rel(11, "implements", "sales_deploy.order_header", "table", "order_header", "entity")

        # 3) COLUMN realization — a physical column implements the metric directly (read, no agg).
        self._ent(4, "customer_tier", "metric", {
            "dimensions": [{"name": "region", "expr": "region"}],
            "measures": [],   # a column passthrough carries no aggregate
        })
        self._ent(12, "sales_deploy.customer.tier", "column",
                  {"infrastructure": "teradata", "source_table": "sales_deploy.customer"})
        self._rel(12, "implements", "sales_deploy.customer.tier", "column",
                  "customer_tier", "metric")

        # 4) NONE — un-enriched metric (no parsed definition, no deployment).
        self._ent(5, "orphan_metric", "metric", {})

    # ── the GraphStore surface metric_api / resolver use ──
    def get_entity_by_name(self, name, entity_type=None):
        if entity_type is not None:
            return self._by_name.get((name, entity_type))
        return self._by_name.get(name)

    async def get_relationships(self, entity_id, direction="both"):
        return list(self._rels.get(entity_id, []))

    async def list_entities(self, entity_type=None, limit=1000):
        out = [e for e in self._by_id.values()
               if entity_type is None or e["entity_type"] == entity_type]
        return out[:limit]


def main():
    print("=" * 70)
    print("Governed metric semantic core (Tier 4.2 / T3) — module tests")
    print("=" * 70)
    store = FakeStore()

    async def run():
        # ── 1) VIEW realization + aggregate slice compile ──
        v = await resolve_metric_semantics(store, "avg_order_value")
        check("view metric found", v.found and not v.error)
        check("realization = view", v.realization == "view")
        check("deployed view resolved", v.deployed_view == "sales_deploy.avg_order_value_v")
        check("source table = deployed base", v.source_table == "sales_deploy.order_header")
        check("compilable", v.compilable is True)
        check("compiled SQL is a SELECT", v.compiled_sql and v.compiled_sql.strip().upper().startswith("SELECT"))
        check("compiled SQL aggregates the base table", "sales_deploy" in v.compiled_sql and "GROUP BY" in v.compiled_sql.upper())
        check("access scope = deployed db", v.access_scope == ["sales_deploy"])
        check("definition carries dims+measures", len(v.dimensions) == 1 and len(v.measures) == 1)

        # slice: select only the measure (no dimension) → coarser grain, no GROUP BY
        vs = await resolve_metric_semantics(store, "avg_order_value", dimensions=[], measures=["avg_value"])
        check("empty-dimension slice compiles", vs.compiled_sql and "GROUP BY" not in vs.compiled_sql.upper())
        check("slice selected the measure", vs.selected_measures == ["avg_value"])
        # The shared core carries the recovery-shaped refusal, so the BI face inherits it
        # (define once, consume everywhere) — the metric is FOUND, no SQL is emitted, and
        # the error names every declared measure rather than the bare "needs a measure"
        # dead end the model hit in prod (d9a60903).
        vbad = await resolve_metric_semantics(store, "avg_order_value", measures=["order_value"])
        check("a wrong measure name → found, no SQL, an error naming the declared measures",
              vbad.found and not vbad.compiled_sql and "'avg_value'" in (vbad.error or "")
              and "column name is not a measure name" in (vbad.error or ""))
        check("an unrelated requested name is offered NO closest match (never a false hint)",
              "closest declared" not in (vbad.error or ""))
        vslip = await resolve_metric_semantics(store, "avg_order_value", measures=["Avg Value"])
        check("a naming slip IS offered its closest declared name",
              "closest declared: 'avg_value'" in (vslip.error or ""))
        check("slice selected no dimensions", vs.selected_dimensions == [])

        # ── 2) BASE_TABLE realization (metric --measures--> deployed table) ──
        b = await resolve_metric_semantics(store, "revenue_total")
        check("base_table metric found", b.found)
        check("realization = base_table", b.realization == "base_table")
        check("base_table source resolved", b.source_table == "sales_deploy.order_header")
        check("base_table compilable", b.compilable is True)
        check("base_table SQL targets deployed table", "sales_deploy" in (b.compiled_sql or "") and "order_header" in (b.compiled_sql or ""))

        # ── 3) COLUMN realization — read directly, no aggregation ──
        c = await resolve_metric_semantics(store, "customer_tier")
        check("column metric found", c.found)
        check("realization = column", c.realization == "column")
        check("deployed column resolved", c.deployed_column == "sales_deploy.customer.tier")
        check("column metric is NOT compilable (read, not aggregate)", c.compilable is False)
        check("column read SQL has no GROUP BY", c.compiled_sql and "GROUP BY" not in c.compiled_sql.upper())
        check("column read selects the column", "tier" in (c.compiled_sql or ""))
        check("column selected measure = the column", c.selected_measures == ["tier"])

        # ── 4) NONE — un-enriched metric → honest error, still found ──
        o = await resolve_metric_semantics(store, "orphan_metric")
        check("orphan metric is found", o.found is True)
        check("orphan metric not deployed", o.is_deployed is False and o.realization == "none")
        check("orphan metric yields a compile error", bool(o.error) and o.compiled_sql is None)
        check("orphan metric not compilable", o.compilable is False)

        # ── missing metric → not found ──
        m = await resolve_metric_semantics(store, "does_not_exist")
        check("missing metric → found False", m.found is False)
        check("missing metric → error set", bool(m.error))

        # ── empty name → error, not found ──
        e0 = await resolve_metric_semantics(store, "   ")
        check("blank name → error", e0.found is False and bool(e0.error))

        # ── catalog: no-SQL summary of every metric, stable order ──
        cat = await list_metric_catalog(store)
        names = [r["name"] for r in cat]
        check("catalog lists all 4 metrics", len(cat) == 4)
        check("catalog sorted by name", names == sorted(names))
        row = next(r for r in cat if r["name"] == "avg_order_value")
        check("catalog row has counts", row["dimension_count"] == 1 and row["measure_count"] == 1)
        check("catalog row marks queryable", row["queryable"] is True)
        check("catalog row carries realization", row["realization"] == "view")
        check("catalog row carries access scope", row["access_scope"] == ["sales_deploy"])
        orphan_row = next(r for r in cat if r["name"] == "orphan_metric")
        check("catalog orphan not queryable", orphan_row["queryable"] is False)

        # ── compile_slice=False (catalog path) does no SQL work ──
        nc = await resolve_metric_semantics(store, "avg_order_value", compile_slice=False)
        check("no-compile path skips SQL", nc.compiled_sql is None and nc.found)
        check("no-compile path still resolves deployment", nc.realization == "view")

        # ── to_dict shape (the BI response body) ──
        d = v.to_dict()
        check("to_dict has definition block", "definition" in d and "dimensions" in d["definition"])
        check("to_dict has deployment block", d["deployment"]["realization"] == "view")
        check("to_dict has compiled block", d["compiled"]["sql"] is not None)
        check("to_dict has access_scope", d["access_scope"] == ["sales_deploy"])

    asyncio.run(run())

    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
