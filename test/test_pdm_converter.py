"""
M1 module tests — pure dbdeploy converter (physical_model + Teradata dialect).

CI-pure: no network, no DB.  Asserts golden Teradata DDL, the KG physical delta
(implements/contains/foreign_key + naming invariant), the datatype map, the
PRIMARY INDEX rule, soft RI, COLLECT STATISTICS, comment escaping, division
filtering, and determinism — against a hand-crafted fixture.

Run:
    python test/test_pdm_converter.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (for `components` real-contract check)

from trusted_data_agent.dbdeploy import convert  # noqa: E402
from trusted_data_agent.dbdeploy.converter import resolve_dialect, UnknownDialect  # noqa: E402
from trusted_data_agent.dbdeploy.physical_model import extract_physical_model  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "industry_model_pdm_sample.json")

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


def load_model():
    with open(FIXTURE) as f:
        return json.load(f)


def conv(**kw):
    # These golden-DDL tests assert the per-domain namespace mechanics (datatype
    # map, PI, comments, FK identifiers).  Those mechanics are namespace-independent,
    # so they pin flat_namespace=False; the flat (canonical) model is covered by
    # test_flat_namespace() + test_industry_model_pdm + test_pdm_constructor_deploy.
    kw.setdefault("flat_namespace", False)
    kw.setdefault("comments", "all")   # comment-mechanic tests assert full COMMENT ON
    return convert(load_model(), industry="acme", flavour="mvm", **kw)


def find_entity(delta, name, etype):
    return next((e for e in delta.entities if e["name"] == name and e["entity_type"] == etype), None)


def find_rel(delta, sn, st, tn, tt, rt):
    return next((r for r in delta.relationships
                 if r["source_name"] == sn and r["source_type"] == st
                 and r["target_name"] == tn and r["target_type"] == tt
                 and r["relationship_type"] == rt), None)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_physical_model():
    print("\n[test_physical_model]")
    pm = extract_physical_model(load_model(), industry="acme", flavour="mvm")
    check("catalog read from conventions (not assumed)", pm.deployment_catalog == "acme_mvm")
    check("one database per domain", {d.name for d in pm.databases} == {"sales", "crm"})
    tnames = {(t.domain, t.name) for t in pm.tables}
    check("all tables extracted", tnames == {("sales", "order"), ("sales", "order_line"),
                                             ("sales", "event_log"), ("crm", "customer")})
    ol = next(t for t in pm.tables if t.name == "order_line")
    check("composite PK parsed", ol.pk_columns == ["order_id", "line_no"])
    ev = next(t for t in pm.tables if t.name == "event_log")
    check("no-PK table has empty pk_columns", ev.pk_columns == [])
    check("resolved FKs = 2 (order->customer, order_line->order)", len(pm.fks) == 2)
    check("dangling FK counted (void.nope)", pm.fks_dropped_dangling == 1)
    check("type not inferred from name (calorie-style): is_paid is BOOLEAN",
          next(c for c in next(t for t in pm.tables if t.name == "order").columns
               if c.name == "is_paid").dbx_type.upper() == "BOOLEAN")


def test_databases_named_not_ddl():
    print("\n[test_databases_named_not_ddl]")
    r = conv()
    # Provisioning is a backend capability now — NO raw CREATE DATABASE in the plan.
    check("no CREATE DATABASE in DDL plan", len(r.ddl.by_kind("database")) == 0)
    check("domain databases named on result", set(r.databases) == {"sales", "crm"})
    check("root_database defaults to the model's Uderia name (model_naming), NOT the Databricks catalog",
          r.root_database == "acme_core" and r.physical_model.deployment_catalog == "acme_mvm")
    check("summary counts named databases", r.as_summary()["databases"] == 2)
    r2 = conv(root_database="custom_root")
    check("root_database override honored", r2.root_database == "custom_root")


def test_ddl_tables():
    print("\n[test_ddl_tables]")
    r = conv()
    script = r.ddl.sql_script()
    check("4 CREATE TABLE", len(r.ddl.by_kind("table")) == 4)
    check("MULTISET / NO FALLBACK", 'CREATE MULTISET TABLE "sales"."order", NO FALLBACK' in script)
    check("reserved word 'order' quoted as database+table", '"sales"."order"' in script)


def test_primary_index_rule():
    print("\n[test_primary_index_rule]")
    script = conv().ddl.sql_script()
    check("single-col UPI", 'UNIQUE PRIMARY INDEX ( "order_id" )' in script)
    check("composite UPI (full PK set)", 'UNIQUE PRIMARY INDEX ( "order_id", "line_no" )' in script)
    check("NoPI for keyless table", "NO PRIMARY INDEX;" in script)


def test_datatype_map():
    print("\n[test_datatype_map]")
    script = conv().ddl.sql_script()
    check("BOOLEAN -> BYTEINT NOT NULL DEFAULT 0", '"is_paid" BYTEINT NOT NULL DEFAULT 0' in script)
    check("TIMESTAMP -> TIMESTAMP(6) WITH TIME ZONE", '"order_ts" TIMESTAMP(6) WITH TIME ZONE' in script)
    check("DATE -> DATE", '"order_date" DATE' in script)
    check("DECIMAL(18,2) preserved", '"total_amount" DECIMAL(18,2)' in script)
    check("STRING -> VARCHAR(4000) LATIN", '"note" VARCHAR(4000) CHARACTER SET LATIN' in script)
    check("ARRAY -> JSON (unsupported fallback)", '"tags_list" JSON' in script)
    check("PK column NOT NULL", '"order_id" BIGINT NOT NULL' in script)
    check("INTEGER mapping", '"qty" INTEGER' in script)


def test_comments():
    print("\n[test_comments]")
    script = conv().ddl.sql_script()
    check("COMMENT ON TABLE", 'COMMENT ON TABLE "sales"."order" IS \'Order header record.\';' in script)
    check("COMMENT ON COLUMN with apostrophe escaped (don''t)",
          "COMMENT ON COLUMN \"sales\".\"order\".\"note\" IS 'Free text note with an apostrophe: don''t.';" in script)


def test_comment_levels():
    print("\n[test_comment_levels]")
    # DB COMMENTs are ~89% of the DDL and Teradata DDL is per-statement serial, so
    # `comments` controls how much to emit (the full text always lives on the KG).
    n_all = len(conv(comments="all").ddl.by_kind("comment"))
    n_table = len(conv(comments="table").ddl.by_kind("comment"))
    n_none = len(conv(comments="none").ddl.by_kind("comment"))
    check("comments=all emits table + column comments", n_all > n_table > 0)
    check("comments=table emits ONE comment per documented table (no column comments)", n_table == 4)
    check("comments=none emits zero COMMENT statements", n_none == 0)
    # default is 'none' — fast deploy; descriptions still on the KG nodes
    default = convert(load_model(), industry="acme", flavour="mvm", flat_namespace=False)
    check("default comments level is 'none'", len(default.ddl.by_kind("comment")) == 0)
    check("KG physical_table still carries the description regardless of comment level",
          any(e["entity_type"] == "table" and e["properties"].get("description")
              for e in conv(comments="none").kg_delta.entities))


def test_comment_truncation():
    print("\n[test_comment_truncation]")
    # Teradata caps COMMENT at 255 chars (Error 5550). Long descriptions must be clipped.
    long_desc = "X" * 600
    model = {
        "model_requirements": {"model_conventions": {"deployment_catalog": "t_mvm"}},
        "model": {"domains": [{"name": "d", "division": "operations", "database_name": "d",
            "description": long_desc,
            "products": [{"name": "tbl", "table_name": "tbl", "primary_key": "id", "description": long_desc,
                "attributes": [{"name": "id", "column_name": "id", "type": "BIGINT", "tags": "primary_key",
                                "description": long_desc}]}]}]}}
    r = convert(model, industry="t", flavour="mvm", flat_namespace=False, comments="all")
    # 1) The DB COMMENT respects Teradata's hard 255-char cap (Error 5550).
    for stmt in r.ddl.by_kind("comment"):
        body = stmt.sql.split(" IS '", 1)[1].rsplit("';", 1)[0]
        check(f"comment ≤ 255 chars ({stmt.identifier})", len(body.replace("''", "'")) <= 255)
    check("at least one comment emitted", len(r.ddl.by_kind("comment")) >= 1)
    # 2) NO CONTENT LOSS: the full untruncated description is preserved on the physical KG nodes.
    by_type = {}
    for e in r.kg_delta.entities:
        by_type.setdefault(e["entity_type"], []).append(e)
    ptable = next(e for e in by_type["table"] if e["name"] == "d.tbl")
    pcol = next(e for e in by_type["column"] if e["name"] == "d.tbl.id")
    check("physical_table carries FULL description (untruncated)",
          ptable["properties"].get("description") == long_desc)
    check("physical_column carries FULL description (untruncated)",
          pcol["properties"].get("description") == long_desc)


def test_foreign_keys_soft_ri():
    print("\n[test_foreign_keys_soft_ri]")
    r = conv()
    script = r.ddl.sql_script()
    check("intra-domain soft RI FK",
          'ALTER TABLE "sales"."order_line" ADD FOREIGN KEY ( "order_id" ) '
          'REFERENCES WITH NO CHECK OPTION "sales"."order" ( "order_id" );' in script)
    check("cross-domain soft RI FK (sales.order -> crm.customer)",
          'ALTER TABLE "sales"."order" ADD FOREIGN KEY ( "customer_id" ) '
          'REFERENCES WITH NO CHECK OPTION "crm"."customer" ( "customer_id" );' in script)
    # ghost_id is a real column (kept); only its dangling FK *constraint* is dropped.
    check("dangling FK constraint NOT emitted", 'FOREIGN KEY ( "ghost_id" )' not in script and '"void"' not in script)
    check("ghost_id column still created", '"ghost_id" BIGINT' in script)
    check("2 FK statements", len(r.ddl.by_kind("foreign_key")) == 2)


def test_collect_statistics():
    print("\n[test_collect_statistics]")
    script = conv().ddl.sql_script()
    check("stats on single-col PI", 'COLLECT STATISTICS COLUMN ( "order_id" ) ON "sales"."order";' in script)
    check("stats on FK column", 'COLLECT STATISTICS COLUMN ( "customer_id" ) ON "sales"."order";' in script)
    check("stats on composite PI", 'COLLECT STATISTICS COLUMN ( "order_id", "line_no" ) ON "sales"."order_line";' in script)


def test_kg_physical_delta():
    print("\n[test_kg_physical_delta]")
    d = conv().kg_delta
    # naming invariant: physical node name == deployed identifier
    check("catalog node", find_entity(d, "acme_core", "catalog") is not None)
    check("catalog implements logical database",
          find_rel(d, "acme_core", "catalog", "acme_core", "database", "implements") is not None)
    check("schema node 'sales'", find_entity(d, "sales", "schema") is not None)
    check("schema implements logical domain",
          find_rel(d, "sales", "schema", "sales", "domain", "implements") is not None)
    check("catalog contains schema",
          find_rel(d, "acme_core", "catalog", "sales", "schema", "contains") is not None)
    pt = find_entity(d, "sales.order", "table")
    check("physical_table name == deployed id (db.table)", pt is not None)
    check("physical_table implements logical table",
          find_rel(d, "sales.order", "table", "sales.order", "entity", "implements") is not None)
    check("schema contains physical_table",
          find_rel(d, "sales", "schema", "sales.order", "table", "contains") is not None)
    pc = find_entity(d, "sales.order.order_id", "column")
    check("physical_column name == deployed id (db.table.col)", pc is not None)
    check("physical_column carries Teradata datatype", pc and pc["properties"].get("datatype") == "BIGINT")
    check("physical_column implements logical column",
          find_rel(d, "sales.order.order_id", "column", "sales.order.order_id", "attribute", "implements") is not None)
    check("physical FK column-level in TD namespace",
          find_rel(d, "sales.order_line.order_id", "column",
                   "sales.order.order_id", "column", "foreign_key") is not None)
    check("all physical entities layered + infra-tagged",
          all(e["properties"].get("layer") == "physical" and e["properties"].get("infrastructure") == "teradata"
              for e in d.entities))


def test_kg_delta_matches_real_contract():
    print("\n[test_kg_delta_matches_real_contract]")
    d = conv().kg_delta
    # Best-effort: assert our types are a subset of the real GraphStore enums.
    try:
        from components.builtin.knowledge_graph.graph_store import ENTITY_TYPES, RELATIONSHIP_TYPES
    except Exception as e:  # pragma: no cover - keep CI-pure if import is heavy
        print(f"  (skipped real-contract import: {e})")
        return
    etypes = {e["entity_type"] for e in d.entities}
    rtypes = {r["relationship_type"] for r in d.relationships}
    check("entity types valid", etypes <= ENTITY_TYPES)
    check("relationship types valid", rtypes <= RELATIONSHIP_TYPES)


def test_warnings():
    print("\n[test_warnings]")
    w = conv().warnings
    check("array unsupported warned", any("tags_list" in x and "JSON" in x for x in w))
    check("dangling FK warned", any("dangling" in x for x in w))


def test_load_plan():
    print("\n[test_load_plan]")
    lp = conv().load
    check("4 load specs", len(lp.tables) == 4)
    order = next(t for t in lp.tables if t.target_table == "order")
    check("load spec carries typed columns", ("is_paid", "BYTEINT") in order.columns)
    check("load spec carries pk", order.pk_columns == ["order_id"])
    check("csv_ref unset (pure converter)", order.csv_ref is None)


def test_division_filter():
    print("\n[test_division_filter]")
    r = conv(divisions=["operations"])
    script = r.ddl.sql_script()
    check("only operations databases", r.databases == ["sales"])
    check("crm excluded", '"crm"' not in script)
    check("cross-domain FK to crm dropped (filtered)",
          any("filtered" in x for x in r.warnings))
    check("intra-domain FK survives", 'REFERENCES WITH NO CHECK OPTION "sales"."order"' in script)


def test_determinism():
    print("\n[test_determinism]")
    a, b = conv(), conv()
    check("DDL identical across runs", a.ddl.sql_script() == b.ddl.sql_script())
    check("KG entities identical", a.kg_delta.entities == b.kg_delta.entities)
    check("KG relationships identical", a.kg_delta.relationships == b.kg_delta.relationships)


def test_dialect_resolution():
    print("\n[test_dialect_resolution]")
    check("teradata resolves", resolve_dialect("teradata").name == "teradata")
    try:
        resolve_dialect("snowflake")
        check("unknown dialect raises", False)
    except UnknownDialect:
        check("unknown dialect raises", True)


def test_flat_namespace():
    print("\n[test_flat_namespace]")
    # Canonical model: ONE flat physical database; division/domain are logical-only.
    r = conv(flat_namespace=True)
    script = r.ddl.sql_script()
    check("model flagged flat", r.physical_model.flat is True)
    check("single physical database (the root)", set(r.databases) == {"acme_core"})
    check("no per-domain schema databases", not ({"sales", "crm"} & set(r.databases)))
    check("tables live in the one flat database",
          'CREATE MULTISET TABLE "acme_core"."order", NO FALLBACK' in script)
    check("FK references resolve inside the flat database",
          'REFERENCES WITH NO CHECK OPTION "acme_core"."customer"' in script
          or 'REFERENCES WITH NO CHECK OPTION "acme_core"."order"' in script)
    # KG: exactly one schema node (the root); domains are NOT physical containers.
    schemas = [e for e in r.kg_delta.entities if e["entity_type"] == "schema"]
    check("exactly one schema node", len(schemas) == 1 and schemas[0]["name"] == "acme_core")
    schema_implements_domain = [rel for rel in r.kg_delta.relationships
                                if rel["relationship_type"] == "implements"
                                and rel["target_type"] == "domain"]
    check("schema does NOT implement any domain (logical-only)", schema_implements_domain == [])
    check("catalog contains the single schema",
          find_rel(r.kg_delta, "acme_core", "catalog", "acme_core", "schema", "contains") is not None)
    # physical_table keeps its logical link via the original domain.table name
    check("physical_table implements the logical domain.table",
          find_rel(r.kg_delta, "acme_core.order", "table", "sales.order", "entity", "implements") is not None)


def test_flat_collision_prefix():
    print("\n[test_flat_collision_prefix]")
    # Two domains reuse the same table name → flat model domain-prefixes ONLY the
    # collision (the validated ECM tie-breaker); logical implements links survive.
    def _dom(name, div):
        return {"name": name, "division": div, "database_name": name, "products": [
            {"name": "shared", "table_name": "shared", "primary_key": "id", "attributes": [
                {"name": "id", "column_name": "id", "type": "BIGINT", "tags": "primary_key"}]}]}
    model = {"model_requirements": {"model_conventions": {"deployment_catalog": "x_mvm"}},
             "model": {"domains": [_dom("a", "operations"), _dom("b", "business")]}}
    r = convert(model, industry="x", flavour="mvm")  # flat default
    script = r.ddl.sql_script()
    check("collision recorded", set(r.physical_model.flat_collisions) == {"a.shared", "b.shared"})
    check("domain-prefixed deployed names", '"x_core"."a_shared"' in script and '"x_core"."b_shared"' in script)
    check("both in the single flat database", set(r.databases) == {"x_core"})
    check("prefixed physical_table still implements logical a.shared",
          find_rel(r.kg_delta, "x_core.a_shared", "table", "a.shared", "entity", "implements") is not None)


def main():
    for fn in [
        test_physical_model,
        test_databases_named_not_ddl,
        test_ddl_tables,
        test_primary_index_rule,
        test_datatype_map,
        test_comments,
        test_comment_levels,
        test_comment_truncation,
        test_foreign_keys_soft_ri,
        test_collect_statistics,
        test_kg_physical_delta,
        test_kg_delta_matches_real_contract,
        test_warnings,
        test_load_plan,
        test_division_filter,
        test_determinism,
        test_dialect_resolution,
        test_flat_namespace,
        test_flat_collision_prefix,
    ]:
        fn()
    print(f"\n{'='*50}\nM1 converter: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
