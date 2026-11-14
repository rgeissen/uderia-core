"""CI-pure tests for KG scope-database derivation — the deployed scope that drives
the eval scope-adherence scorer and the Tier 1.1 grounding gate.

Two paths:
  - primary: deployed physical identifiers (industry-model KG with a deployed PDM)
  - fallback: the logical `database` root node (agent-discovery KG, logical-only) —
    so the grounding gate also protects discovery KGs (ONTOLOGY_ARCHITECTURE / TIER_1.1 §3e),
    while NEVER widening an industry-model KG's precise physical scope.

Run:  python test/test_kg_scope_derivation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (for `components`)

from components.builtin.knowledge_graph.handler import (  # noqa: E402
    _derive_scope_databases, _derive_full_kg_scope_dbs,
)

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


class _Cap:
    def __init__(self, deployed_as=None, metric_view=None, metric_base_table=None):
        self.deployed_as = deployed_as
        self.metric_view = metric_view
        self.metric_base_table = metric_base_table


def t_primary_physical():
    caps = {"campaign": _Cap(deployed_as="restaurants_mvm_test.campaign"),
            "rev": _Cap(metric_view="restaurants_mvm_test.v_revenue")}
    ents = [{"entity_type": "database", "name": "restaurants_mvm", "properties": {}}]
    check("primary: deployed scope from physical identifiers (fallback NOT used)",
          _derive_scope_databases(caps, ents) == ["restaurants_mvm_test"])
    check("primary: multiple identifiers across dbs, deduped + sorted",
          _derive_scope_databases({"a": _Cap(deployed_as="db1.t1"),
                                   "b": _Cap(deployed_as="db2.t2"),
                                   "c": _Cap(deployed_as="db1.t3")}, []) == ["db1", "db2"])
    check("primary: metric_base_table counts as scope",
          _derive_scope_databases({"m": _Cap(metric_base_table="finance_db.gl")}, []) == ["finance_db"])
    check("primary: bare identifier (no '.') is ignored",
          _derive_scope_databases({"x": _Cap(deployed_as="noschema")}, []) == [])


def t_fallback_discovery():
    # discovery KG: no physical capabilities → fall back to the database root node
    ents = [{"entity_type": "database", "name": "fitness_db", "properties": {"layer": "logical"}},
            {"entity_type": "entity", "name": "Products", "properties": {}},
            {"entity_type": "attribute", "name": "price", "properties": {}}]
    check("fallback: scope from database node NAME when no physical layer",
          _derive_scope_databases({}, ents) == ["fitness_db"])
    check("fallback: database_name property preferred over node name",
          _derive_scope_databases({}, [{"entity_type": "database", "name": "Display Name",
                                        "properties": {"database_name": "real_db"}}]) == ["real_db"])
    check("fallback: multiple database roots → both",
          _derive_scope_databases({}, [{"entity_type": "database", "name": "db_a", "properties": {}},
                                       {"entity_type": "database", "name": "db_b", "properties": {}}])
          == ["db_a", "db_b"])
    check("fallback: only the database node contributes (entity/attribute ignored)",
          _derive_scope_databases({}, [{"entity_type": "entity", "name": "finance", "properties": {}},
                                       {"entity_type": "database", "name": "fitness_db", "properties": {}}])
          == ["fitness_db"])


def t_no_widening_and_robust():
    # industry-model KG WITH physical scope: the database root node must NOT widen it
    caps = {"x": _Cap(deployed_as="restaurants_mvm_test.x")}
    ents = [{"entity_type": "database", "name": "restaurants_mvm", "properties": {}}]
    check("no-widen: physical scope present → database node NOT added",
          _derive_scope_databases(caps, ents) == ["restaurants_mvm_test"])
    check("empty: no physical + no database node → [] (gate abstains)",
          _derive_scope_databases({}, [{"entity_type": "entity", "name": "X", "properties": {}}]) == [])
    check("robust: blank database name ignored",
          _derive_scope_databases({}, [{"entity_type": "database", "name": "  ", "properties": {}}]) == [])
    check("robust: None inputs → []",
          _derive_scope_databases(None, None) == [])


def t_full_kg_trusted_scope():
    # The always-on trusted scope: deployed dbs from the FULL KG's physical `table` nodes
    # (qualified db.table names) — query-independent, so the gate is never blind.
    tables = [{"name": "fitness_db.Customers"}, {"name": "fitness_db.Sales"},
              {"name": "fitness_db.Products"}]
    check("full_kg_scope: single-db KG → {fitness_db}",
          _derive_full_kg_scope_dbs(tables) == {"fitness_db"})
    check("full_kg_scope: multi-db KG → all dbs",
          _derive_full_kg_scope_dbs([{"name": "a.t1"}, {"name": "b.t2"}, {"name": "a.t3"}]) == {"a", "b"})
    check("full_kg_scope: unqualified table names ignored",
          _derive_full_kg_scope_dbs([{"name": "NoSchema"}, {"name": ""}]) == set())
    check("full_kg_scope: empty / None → empty",
          _derive_full_kg_scope_dbs([]) == set() and _derive_full_kg_scope_dbs(None) == set())
    # The fix in one assertion: a fitness_db trusted scope catches a restaurants_mvm_test drift
    from trusted_data_agent.agent.grounding.primitives import scope_diff
    call = {"arguments": {"database_name": "restaurants_mvm_test",
                          "sql": "SELECT * FROM restaurants_mvm_test.finance_journal_entry"}}
    check("always-on fitness_db scope flags the restaurants_mvm_test drift (the missed case)",
          scope_diff({"fitness_db"}, [call]) == {"restaurants_mvm_test"})


def t_scope_entities_include_bridges():
    """The scope's self-description names every in-scope CONCEPT an agent may name in
    prose — including a declared product_bridge. Without it, an agent citing the core
    model's bridge by name ("Policing Core.bridge.Offence__CaseObject") read to the
    grounding trigger as reaching into a database called `core`."""
    from components.builtin.knowledge_graph.handler import _derive_scope_entities
    ents = [{"entity_type": "entity", "name": "Policing Core.Offence"},
            {"entity_type": "product_bridge", "name": "Policing Core.bridge.Offence__CaseObject"},
            {"entity_type": "attribute", "name": "Policing Core.Offence.offence_code"},
            {"entity_type": "table", "name": "Policing_Core.v_Offence"}]
    got = _derive_scope_entities(ents)
    check("bridge names are in-scope concepts", "Policing Core.bridge.Offence__CaseObject" in got)
    check("entities still are", "Policing Core.Offence" in got)
    check("attributes and physical tables are not (they are not concepts an answer names)",
          not any(n.endswith("offence_code") or n.startswith("Policing_Core.") for n in got))


def main():
    for t in (t_primary_physical, t_fallback_discovery, t_no_widening_and_robust,
              t_full_kg_trusted_scope, t_scope_entities_include_bridges):
        print(f"\n{t.__name__}:")
        t()
    print(f"\n{'='*44}\nKG scope derivation: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
