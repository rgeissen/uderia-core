"""
CI-pure tests for the database-context constructor's deterministic core
(trusted_data_agent.agent.kg_constructors.database_context).

Validates the canonical two-layer emission (logical entity/attribute + physical
table/column + implements), infrastructure derivation, physical-from-logical reuse
path, and the LLM tool-role parsing — all without a live DB or MCP server.

Run:  python test/test_database_context_constructor.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.kg_constructors.database_context import (  # noqa: E402
    derive_infrastructure, build_two_layer_graph, build_physical_layer_from_logical,
    parse_tool_roles, discover_schema_via_tools,
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


def types(d):
    from collections import Counter
    return Counter(e["entity_type"] for e in d["entities"])


def has_rel(d, sn, st, tn, tt, rt):
    return any(r["source_name"] == sn and r["source_type"] == st and r["target_name"] == tn
              and r["target_type"] == tt and r["relationship_type"] == rt
              for r in d["relationships"])


def has_ent(d, name, etype):
    return any(e["name"] == name and e["entity_type"] == etype for e in d["entities"])


# ── 1. infrastructure derivation (from the modal Target Database field) ──
def test_infrastructure():
    print("\n[1] derive_infrastructure")
    check("Teradata -> teradata", derive_infrastructure("Teradata") == "teradata")
    check("'Amazon Redshift' -> amazon_redshift", derive_infrastructure("Amazon Redshift") == "amazon_redshift")
    check("empty -> unknown", derive_infrastructure("") == "unknown")


# ── 2. two-layer build ──
def test_two_layer():
    print("\n[2] build_two_layer_graph — logical + physical + implements")
    tables = [
        {"name": "Customers", "columns": [
            {"name": "CustomerID", "datatype": "INTEGER", "is_primary_key": True},
            {"name": "Email", "datatype": "VARCHAR(100)"}]},
        {"name": "Sales", "columns": [{"name": "SaleID", "datatype": "INTEGER"},
                                      {"name": "CustomerID", "datatype": "INTEGER"}],
         "foreign_keys": [{"column": "CustomerID", "ref_table": "Customers", "ref_column": "CustomerID"}]},
    ]
    d = build_two_layer_graph("fitness_db", tables, "teradata")
    tc = types(d)
    check("1 database (logical root)", tc["database"] == 1)
    check("1 schema (physical root)", tc["schema"] == 1)
    check("2 entities (logical tables)", tc["entity"] == 2)
    check("2 physical tables", tc["table"] == 2)
    check("4 attributes (logical columns)", tc["attribute"] == 4)
    check("4 physical columns", tc["column"] == 4)
    # naming contract
    check("entity bare name", has_ent(d, "Customers", "entity"))
    check("physical table == deployed id", has_ent(d, "fitness_db.Customers", "table"))
    check("attribute qualified", has_ent(d, "Customers.CustomerID", "attribute"))
    check("physical column qualified", has_ent(d, "fitness_db.Customers.CustomerID", "column"))
    # implements wiring (physical -> logical)
    check("schema implements database",
          has_rel(d, "fitness_db", "schema", "fitness_db", "database", "implements"))
    check("table implements entity",
          has_rel(d, "fitness_db.Customers", "table", "Customers", "entity", "implements"))
    check("column implements attribute",
          has_rel(d, "fitness_db.Customers.CustomerID", "column", "Customers.CustomerID", "attribute", "implements"))
    # containment both layers
    check("database contains entity", has_rel(d, "fitness_db", "database", "Customers", "entity", "contains"))
    check("schema contains table", has_rel(d, "fitness_db", "schema", "fitness_db.Customers", "table", "contains"))
    check("entity contains attribute", has_rel(d, "Customers", "entity", "Customers.CustomerID", "attribute", "contains"))
    check("table contains column", has_rel(d, "fitness_db.Customers", "table", "fitness_db.Customers.CustomerID", "column", "contains"))
    # datatype lives on the PHYSICAL column, not the logical attribute (§2.4c)
    pcol = next(e for e in d["entities"] if e["name"] == "fitness_db.Customers.CustomerID")
    attr = next(e for e in d["entities"] if e["name"] == "Customers.CustomerID")
    check("physical column carries datatype", pcol["properties"].get("datatype") == "INTEGER")
    check("logical attribute has NO datatype", "datatype" not in attr["properties"])
    # every physical node infrastructure-tagged
    check("all physical nodes infrastructure-tagged",
          all(e["properties"].get("infrastructure") == "teradata"
              for e in d["entities"] if e["properties"].get("layer") == "physical"))
    # FK both layers
    check("logical FK entity->entity", has_rel(d, "Sales", "entity", "Customers", "entity", "foreign_key"))
    check("physical FK table->table", has_rel(d, "fitness_db.Sales", "table", "fitness_db.Customers", "table", "foreign_key"))


# ── 3. physical-from-logical reuse path ──
def test_physical_from_logical():
    print("\n[3] build_physical_layer_from_logical — reuse existing agent discovery")
    logical_e = [
        {"name": "fitness_db", "entity_type": "database", "properties": {"layer": "logical"}},
        {"name": "Customers", "entity_type": "entity", "properties": {"layer": "logical"}},
        {"name": "Customers.CustomerID", "entity_type": "attribute",
         "properties": {"layer": "logical", "data_type": "INTEGER"}},
    ]
    logical_r = [
        {"source_name": "fitness_db", "source_type": "database", "target_name": "Customers",
         "target_type": "entity", "relationship_type": "contains"},
        {"source_name": "Customers", "source_type": "entity", "target_name": "Customers.CustomerID",
         "target_type": "attribute", "relationship_type": "contains"},
    ]
    d = build_physical_layer_from_logical("fitness_db", logical_e, logical_r, "teradata")
    check("derives physical schema", has_ent(d, "fitness_db", "schema"))
    check("derives physical table", has_ent(d, "fitness_db.Customers", "table"))
    check("derives physical column", has_ent(d, "fitness_db.Customers.CustomerID", "column"))
    check("table implements entity", has_rel(d, "fitness_db.Customers", "table", "Customers", "entity", "implements"))
    check("column implements attribute",
          has_rel(d, "fitness_db.Customers.CustomerID", "column", "Customers.CustomerID", "attribute", "implements"))
    pcol = next(e for e in d["entities"] if e["name"] == "fitness_db.Customers.CustomerID")
    check("physical column inherits datatype from logical data_type", pcol["properties"].get("datatype") == "INTEGER")


# ── 4. LLM tool-role parsing (no hardcoded names) ──
def test_tool_role_parse():
    print("\n[4] parse_tool_roles")
    txt = ('```json\n{"list_tables": {"tool": "base_tableList", "db_arg": "database_name"},'
           ' "describe_columns": {"tool": "base_columnDescription", "db_arg": "database_name", "table_arg": "obj_name"},'
           ' "table_ddl": null}\n```')
    roles = parse_tool_roles(txt)
    check("list_tables resolved", roles["list_tables"]["tool"] == "base_tableList")
    check("describe_columns table_arg parsed", roles["describe_columns"]["table_arg"] == "obj_name")
    check("table_ddl null tolerated", roles["table_ddl"] is None)
    check("garbage -> empty", parse_tool_roles("no json here") == {})


# ── 5. deterministic discovery via injected invoke (no real MCP) ──
def test_discover_via_tools():
    print("\n[5] discover_schema_via_tools (fake invoke)")
    calls = []

    async def fake_invoke(tool, args):
        calls.append((tool, args))
        if tool == "LT":
            return {"results": [{"TableName": "Customers"}, {"TableName": "Sales"}]}
        if tool == "DC":
            tbl = args.get("obj_name")
            if tbl == "Customers":
                return {"results": [{"ColumnName": "CustomerID", "ColumnType": "INTEGER"},
                                    {"ColumnName": "Email", "ColumnType": "VARCHAR"}]}
            return {"results": [{"ColumnName": "SaleID", "ColumnType": "INTEGER"}]}
        return {"results": []}

    roles = {"list_tables": {"tool": "LT", "db_arg": "database_name"},
             "describe_columns": {"tool": "DC", "db_arg": "database_name", "table_arg": "obj_name"},
             "table_ddl": None}
    tables = asyncio.run(discover_schema_via_tools(fake_invoke, roles, "fitness_db"))
    check("2 tables discovered", len(tables) == 2)
    check("Customers has 2 columns", len(tables[0]["columns"]) == 2)
    check("column datatype captured", tables[0]["columns"][0]["datatype"] == "INTEGER")
    # builds end-to-end
    d = build_two_layer_graph("fitness_db", tables, "teradata")
    check("end-to-end yields physical columns", types(d)["column"] == 3)


# ── 6. arg-name probing: the LLM guesses the WRONG arg, the probe recovers ──
def test_arg_probing():
    print("\n[6] discover_schema_via_tools recovers from wrong LLM arg names")

    async def picky_invoke(tool, args):
        # This server ONLY accepts database_name / obj_name; anything else errors
        # (mirrors the real base_tableList rejecting 'database').
        if tool == "LT":
            if "database_name" not in args:
                return "Error calling tool 'LT': Unexpected keyword argument"
            return {"results": [{"TableName": "Customers"}]}
        if tool == "DC":
            if "obj_name" not in args or "database_name" not in args:
                return "Input validation error: 'obj_name' is a required property"
            return {"results": [{"ColumnName": "CustomerID", "DataType": "INTEGER"}]}
        return {"results": []}

    # LLM guessed WRONG arg names for BOTH tools (database / db / table) — the 2D
    # probe (seeded by the db-arg that worked for list_tables) must recover.
    roles = {"list_tables": {"tool": "LT", "db_arg": "database"},
             "describe_columns": {"tool": "DC", "db_arg": "db", "table_arg": "table"},
             "table_ddl": None}
    tables = asyncio.run(discover_schema_via_tools(picky_invoke, roles, "fitness_db"))
    check("recovered: 1 table despite wrong list db_arg", len(tables) == 1)
    check("recovered: columns despite wrong table_arg", len(tables[0]["columns"]) == 1)
    check("column datatype captured via probe", tables[0]["columns"][0]["datatype"] == "INTEGER")


def main():
    for fn in [test_infrastructure, test_two_layer, test_physical_from_logical,
               test_tool_role_parse, test_discover_via_tools, test_arg_probing]:
        fn()
    print(f"\n{'='*50}\nDB-context constructor: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
