"""
CI-pure tests for the ANDP semantic-module KG walker.

No database, no network — the walker is pure by contract, so every behaviour that
matters is provable here.  The fixtures mirror the *real* catalog shapes observed
on the reference deployment (NIBRS_Source / CASE_Source), including the column-name
drift between two products built to the same standard.

Run: python3 test/test_andp_semantic_walker.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.kg_constructors.andp_semantic_walker import (  # noqa: E402
    LAYER_LOGICAL,
    LAYER_PHYSICAL,
    SemanticSnapshot,
    WalkError,
    WalkOptions,
    WalkStats,
    parse_value_codes,
    scope_databases,
    walk_andp_semantic,
)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


def ents_by_type(entities, etype):
    return [e for e in entities if e["entity_type"] == etype]


def find_entity(entities, name, etype):
    for e in entities:
        if e["name"] == name and e["entity_type"] == etype:
            return e
    return None


def rels_of(relationships, rtype):
    return [r for r in relationships if r["relationship_type"] == rtype]


# ─────────────────────────────────────────────────────────────────────────────
# fixtures — shaped exactly like the real catalogs
# ─────────────────────────────────────────────────────────────────────────────

def nibrs_snapshot(**over):
    """NIBRS-shaped: entity_description/module_name/surrogate_key_column, no IRIs."""
    snap = SemanticSnapshot(
        product="NIBRS_Source",
        semantic_database="NIBRS_Source_Semantic",
        data_product_map=[
            {"module_name": "Domain", "database_name": "NIBRS_Source_Domain",
             "primary_tables": "Incident, Victim", "is_active": 1},
            {"module_name": "Semantic", "database_name": "NIBRS_Source_Semantic",
             "is_active": 1},
        ],
        entity_metadata=[
            {"entity_name": "Victim", "entity_description": "One row per victim",
             "module_name": "Domain", "database_name": "NIBRS_Source_Domain",
             "table_name": "Victim", "surrogate_key_column": "V4001",
             "natural_key_column": "INCNUM", "industry_standard": "NIBRS",
             "temporal_pattern": "NONE", "is_active": 1},
            {"entity_name": "Incident", "entity_description": "One row per incident",
             "module_name": "Domain", "database_name": "NIBRS_Source_Domain",
             "table_name": "Incident", "is_active": 1},
        ],
        column_metadata=[
            {"database_name": "NIBRS_Source_Domain", "table_name": "Victim",
             "column_name": "V4019", "business_description": "SEX OF VICTIM",
             "is_pii": 1, "is_sensitive": 1, "data_classification": "CONFIDENTIAL",
             "is_required": 1, "data_type": "INTEGER",
             "allowed_values_json": json.dumps(
                 [{"value": "0", "label": "Female"}, {"value": "1", "label": "Male"}]),
             "is_active": 1},
            {"database_name": "NIBRS_Source_Domain", "table_name": "Incident",
             "column_name": "INCNUM", "business_description": "Incident number",
             "is_pii": 0, "is_sensitive": 0, "data_classification": "PUBLIC",
             "data_type": "VARCHAR(12)", "is_active": 1},
            # the FK's source column — without it the physical mirror cannot be
            # emitted, because its endpoint node would not exist
            {"database_name": "NIBRS_Source_Domain", "table_name": "Victim",
             "column_name": "INCNUM", "business_description": "Incident number",
             "is_pii": 0, "is_sensitive": 0, "data_classification": "PUBLIC",
             "data_type": "VARCHAR(12)", "is_active": 1},
        ],
        table_relationship=[
            {"relationship_name": "victim_to_incident",
             "relationship_description": "Victim to its incident",
             "source_database": "NIBRS_Source_Domain", "source_table": "Victim",
             "source_column": "INCNUM",
             "target_database": "NIBRS_Source_Domain", "target_table": "Incident",
             "target_column": "INCNUM", "cardinality": "N:1", "is_active": 1},
        ],
        business_glossary=[
            {"term": "NIBRS", "definition": "FBI incident-based reporting standard",
             "term_category": "REFERENCE_CODE", "is_active": 1},
            {"term": "Victim", "definition": "A person harmed in an incident",
             "related_table": "Victim", "related_column": "V4019", "is_active": 1},
            # a DERIVED concept spans several columns — written as one delimited string,
            # the form products use; one of them does not exist on the table
            {"term": "Identified Victim", "definition": "A victim row with sex and incident known",
             "related_table": "Victim", "related_column": "V4019, INCNUM; NOPE", "is_active": 1},
        ],
        query_cookbook=[
            {"recipe_id": "QC-1", "recipe_title": "Count victims by sex",
             "sql_template": "SELECT V4019, COUNT(*) FROM Victim GROUP BY 1",
             "complexity": "LOW", "is_active": 1},
            {"recipe_id": "QC-2", "recipe_title": "Declared target wins",
             "sql_template": "SELECT * FROM Victim", "target_tables": "Incident",
             "is_active": 1},
        ],
        design_decisions=[
            {"decision_id": "DD-1", "decision_title": "Lossless VARCHAR landing",
             "rationale": "Preserve the source image", "affects_table": "Victim",
             "decision_status": "ACCEPTED", "is_current": 1},
            {"decision_id": "DD-2", "decision_title": "Superseded choice",
             "rationale": "old", "is_current": 0},
        ],
        module_registry=[
            # deliberately UPPERCASE: products case module names differently between
            # their own catalogs, and the enrichment must survive that
            {"module_name": "DOMAIN", "module_version": "1.0.0",
             "deployment_status": "DEPLOYED", "data_owner": "Data Team", "is_current": 1},
        ],
        naming_standards=[
            {"standard_type": "PATTERN", "standard_value": "source-name-preserved",
             "meaning": "Domain columns keep original names",
             "usage_guidance": "Join column_metadata for labels", "is_active": 1},
        ],
        data_lineage=[
            # external file source — no source_database, so no node to point at
            {"source_table": "39270-0004-Data.tsv", "target_database": "NIBRS_Source_Domain",
             "target_table": "Victim", "job_name": "load_nibrs_victim",
             "source_system": "ICPSR", "is_active": 1},
            # table-to-table lineage inside the product
            {"source_database": "NIBRS_Source_Domain", "source_table": "Incident",
             "target_database": "NIBRS_Source_Domain", "target_table": "Victim",
             "job_name": "derive", "is_active": 1},
        ],
        quality_metrics=[
            {"database_name": "NIBRS_Source_Domain", "table_name": "Victim",
             "metric_name": "completeness", "metric_value": 1.0,
             "metric_category": "COMPLETENESS", "is_threshold_met": 1, "is_active": 1},
        ],
        physical_columns={
            # deployed reality: characters, NOT the declared INTEGER
            ("nibrs_source_domain", "victim", "v4019"):
                {"datatype": "VARCHAR(2)", "nullable": True},
            ("nibrs_source_domain", "incident", "incnum"):
                {"datatype": "VARCHAR(12)", "nullable": False},
            ("nibrs_source_domain", "victim", "incnum"):
                {"datatype": "VARCHAR(12)", "nullable": False},
            # deployed but undocumented
            ("nibrs_source_domain", "victim", "V4018"):
                {"datatype": "VARCHAR(3)", "nullable": True},
        },
        source_detail="test",
    )
    for k, v in over.items():
        setattr(snap, k, v)
    return snap


def case_snapshot():
    """CASE-shaped: carries ontology_iri/property_iri, no primary_tables."""
    return SemanticSnapshot(
        product="CASE_Source",
        semantic_database="CASE_Source_Semantic",
        data_product_map=[
            {"module_name": "Domain", "database_name": "CASE_Source_Domain",
             "data_product_name": "CASE_Source", "is_active": 1},
        ],
        entity_metadata=[
            {"entity_name": "Provenance Record", "entity_description": "Chain of custody",
             "module_name": "Domain", "database_name": "CASE_Source_Domain",
             "table_name": "provenance", "view_name": "v_chain_of_custody",
             "industry_standard": "CASE 1.5.0",
             "ontology_iri": "case-investigation:ProvenanceRecord", "is_active": 1},
        ],
        column_metadata=[
            {"database_name": "CASE_Source_Domain", "table_name": "provenance",
             "column_name": "exhibit_number",
             "business_description": "Evidence exhibit number",
             "is_pii": 0, "is_sensitive": 1, "data_classification": "CONFIDENTIAL",
             "data_type": "VARCHAR(100)",
             "property_iri": "case-investigation:exhibitNumber", "is_active": 1},
        ],
        table_relationship=[],
        physical_columns={
            ("case_source_domain", "provenance", "exhibit_number"):
                {"datatype": "VARCHAR(100)", "nullable": False},
        },
        source_detail="test",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. topology
# ─────────────────────────────────────────────────────────────────────────────
def t_topology():
    ents, rels = walk_andp_semantic(nibrs_snapshot())

    root = find_entity(ents, "NIBRS_Source", "database")
    check("root database emitted", root is not None)
    check("root is logical", root and root["properties"]["layer"] == LAYER_LOGICAL)
    check("root declares the standard",
          root and root["properties"].get("standard") == "ai_native_data_product")

    check("module -> domain node",
          find_entity(ents, "NIBRS_Source.Domain", "domain") is not None)
    dom = find_entity(ents, "NIBRS_Source.Domain", "domain")
    check("business module flagged", dom and dom["properties"].get("is_business_module") is True)
    sem_dom = find_entity(ents, "NIBRS_Source.Semantic", "domain")
    check("non-business module not flagged business",
          sem_dom and sem_dom["properties"].get("is_business_module") is False,
          "False must survive the empty-drop filter")

    check("logical entity emitted",
          find_entity(ents, "NIBRS_Source.Victim", "entity") is not None)
    check("logical attribute emitted",
          find_entity(ents, "NIBRS_Source.Victim.V4019", "attribute") is not None)
    check("physical schema emitted",
          find_entity(ents, "NIBRS_Source_Domain", "schema") is not None)
    check("physical table emitted",
          find_entity(ents, "NIBRS_Source_Domain.Victim", "table") is not None)
    check("physical column emitted",
          find_entity(ents, "NIBRS_Source_Domain.Victim.V4019", "column") is not None)

    impl = rels_of(rels, "implements")
    pairs = {(r["source_name"], r["target_name"]) for r in impl}
    check("table implements entity",
          ("NIBRS_Source_Domain.Victim", "NIBRS_Source.Victim") in pairs)
    check("column implements attribute",
          ("NIBRS_Source_Domain.Victim.V4019", "NIBRS_Source.Victim.V4019") in pairs)
    check("schema implements domain",
          ("NIBRS_Source_Domain", "NIBRS_Source.Domain") in pairs)


# ─────────────────────────────────────────────────────────────────────────────
# 2. THE TYPE RULE — meaning from ontology, types from the dictionary
# ─────────────────────────────────────────────────────────────────────────────
def t_type_rule():
    stats = WalkStats()
    ents, _ = walk_andp_semantic(nibrs_snapshot(), stats=stats)

    col = find_entity(ents, "NIBRS_Source_Domain.Victim.V4019", "column")
    check("physical type comes from the dictionary",
          col and col["properties"]["datatype"] == "VARCHAR(2)",
          f"got {col and col['properties'].get('datatype')}")
    check("physical node carries no declared_type",
          col and "declared_type" not in col["properties"])

    attr = find_entity(ents, "NIBRS_Source.Victim.V4019", "attribute")
    check("logical attribute keeps the declared type",
          attr and attr["properties"]["declared_type"] == "INTEGER")
    check("logical attribute carries no dictionary datatype",
          attr and "datatype" not in attr["properties"])

    check("mismatch counted, not hidden", stats.type_mismatches == 1,
          f"got {stats.type_mismatches}")
    check("agreeing column is not a mismatch", stats.type_mismatches == 1)


def t_type_length_is_not_a_mismatch():
    snap = nibrs_snapshot()
    snap.column_metadata[1]["data_type"] = "VARCHAR(99)"   # same base type, other length
    stats = WalkStats()
    walk_andp_semantic(snap, stats=stats)
    check("length difference is not a meaning difference", stats.type_mismatches == 1,
          f"got {stats.type_mismatches} (only V4019 should mismatch)")


# ─────────────────────────────────────────────────────────────────────────────
# 3. governance payload — PII / classification / value codes
# ─────────────────────────────────────────────────────────────────────────────
def t_governance_payload():
    stats = WalkStats()
    ents, _ = walk_andp_semantic(nibrs_snapshot(), stats=stats)

    attr = find_entity(ents, "NIBRS_Source.Victim.V4019", "attribute")
    col = find_entity(ents, "NIBRS_Source_Domain.Victim.V4019", "column")
    for label, node in (("attribute", attr), ("column", col)):
        check(f"{label} carries is_pii", node and node["properties"].get("is_pii") is True)
        check(f"{label} carries classification",
              node and node["properties"].get("data_classification") == "CONFIDENTIAL")

    pub = find_entity(ents, "NIBRS_Source.Incident.INCNUM", "attribute")
    check("is_pii False survives (a real assertion, not an absence)",
          pub and pub["properties"].get("is_pii") is False)

    codes = attr["properties"].get("allowed_values")
    check("decoded values attached", isinstance(codes, list) and len(codes) == 2)
    check("decoded value content correct",
          codes and codes[0] == {"value": "0", "label": "Female"})
    check("pii counted", stats.pii_columns == 1)
    check("value-coded columns counted", stats.columns_with_value_codes == 1)


def t_value_code_parsing():
    check("parses a JSON string",
          parse_value_codes('[{"value":"1","label":"Male"}]') == [{"value": "1", "label": "Male"}])
    check("accepts an already-parsed list",
          parse_value_codes([{"value": "1", "label": "Male"}]) == [{"value": "1", "label": "Male"}])
    check("malformed JSON degrades to None", parse_value_codes("{not json") is None)
    check("non-list JSON degrades to None", parse_value_codes('{"a":1}') is None)
    check("empty degrades to None", parse_value_codes("") is None)
    check("None degrades to None", parse_value_codes(None) is None)
    big = [{"value": str(i), "label": f"L{i}"} for i in range(100)]
    check("bounded by limit", len(parse_value_codes(json.dumps(big), limit=10)) == 10)


# ─────────────────────────────────────────────────────────────────────────────
# 4. the temporal contract the product declares
# ─────────────────────────────────────────────────────────────────────────────
def t_temporal_declaration():
    ents, _ = walk_andp_semantic(nibrs_snapshot())
    ent = find_entity(ents, "NIBRS_Source.Victim", "entity")
    check("source-image temporal pattern carried",
          ent and ent["properties"].get("temporal_pattern") == "NONE")
    check("no current-flag invented when the product declares none",
          ent and "current_flag_column" not in ent["properties"])

    snap = nibrs_snapshot()
    snap.entity_metadata[0].update({
        "temporal_pattern": "BITEMPORAL",
        "current_flag_column": "is_current",
        "deleted_flag_column": "is_deleted",
    })
    ents2, _ = walk_andp_semantic(snap)
    ent2 = find_entity(ents2, "NIBRS_Source.Victim", "entity")
    check("bitemporal declaration carried",
          ent2 and ent2["properties"].get("current_flag_column") == "is_current")


# ─────────────────────────────────────────────────────────────────────────────
# 5. relationships
# ─────────────────────────────────────────────────────────────────────────────
def t_relationships():
    stats = WalkStats()
    ents, rels = walk_andp_semantic(nibrs_snapshot(), stats=stats)

    fks = rels_of(rels, "foreign_key")
    logical = [r for r in fks if r["source_type"] == "entity"]
    physical = [r for r in fks if r["source_type"] == "column"]
    check("logical FK emitted", len(logical) == 1)
    check("logical FK endpoints resolved",
          logical and logical[0]["source_name"] == "NIBRS_Source.Victim"
          and logical[0]["target_name"] == "NIBRS_Source.Incident")
    check("FK carries cardinality", logical and logical[0].get("cardinality") == "N:1")
    check("FK carries its description",
          logical and logical[0]["metadata"]["description"] == "Victim to its incident")
    check("physical FK mirrored", len(physical) == 1)
    check("nothing dropped on a clean fixture", stats.relationships_dropped == 0)


def t_unresolvable_relationship_is_dropped_and_counted():
    snap = nibrs_snapshot()
    snap.table_relationship.append({
        "source_database": "NIBRS_Source_Domain", "source_table": "Victim",
        "target_database": "OTHER_DB", "target_table": "NotRegistered",
        "is_active": 1,
    })
    stats = WalkStats()
    _, rels = walk_andp_semantic(snap, stats=stats)
    check("unresolvable FK dropped", len(rels_of(rels, "foreign_key")) == 2,
          "1 logical + 1 physical from the valid row only")
    check("drop counted", stats.relationships_dropped == 1)


def t_physical_fk_needs_existing_column_nodes():
    """A join on *undocumented* columns must not create dangling physical endpoints.

    The tables resolve, so the logical FK is real and is emitted.  The columns were
    never documented, so no column nodes exist for them — mirroring the FK into the
    physical namespace would produce relationship endpoints pointing at nothing,
    which ``import_bulk`` cannot resolve.
    """
    snap = nibrs_snapshot()
    snap.table_relationship = [{
        "relationship_name": "undocumented_join",
        "source_database": "NIBRS_Source_Domain", "source_table": "Victim",
        "source_column": "GHOST_A",
        "target_database": "NIBRS_Source_Domain", "target_table": "Incident",
        "target_column": "GHOST_B", "is_active": 1,
    }]
    ents, rels = walk_andp_semantic(snap)
    fks = rels_of(rels, "foreign_key")
    check("logical FK still emitted for undocumented columns",
          len([r for r in fks if r["source_type"] == "entity"]) == 1)
    check("physical FK suppressed when its column nodes do not exist",
          len([r for r in fks if r["source_type"] == "column"]) == 0)

    names = {(e["name"], e["entity_type"]) for e in ents}
    endpoints = {(r["source_name"], r["source_type"]) for r in rels} | \
                {(r["target_name"], r["target_type"]) for r in rels}
    check("no dangling relationship endpoint", endpoints <= names,
          str(sorted(endpoints - names)[:5]))


def t_ambiguous_bare_table_is_not_guessed():
    """A bare table name matching two databases must NOT be attached to one at random."""
    snap = nibrs_snapshot()
    snap.entity_metadata.append(
        {"entity_name": "VictimCopy", "module_name": "Domain",
         "database_name": "OTHER_DB", "table_name": "Victim", "is_active": 1})
    snap.table_relationship = [{
        "source_table": "Victim", "target_table": "Incident", "is_active": 1,
    }]
    stats = WalkStats()
    _, rels = walk_andp_semantic(snap, stats=stats)
    check("ambiguous bare-name join dropped rather than mis-attached",
          len(rels_of(rels, "foreign_key")) == 0)
    check("ambiguity counted", stats.relationships_dropped == 1)


# ─────────────────────────────────────────────────────────────────────────────
# 6. coverage diagnostics
# ─────────────────────────────────────────────────────────────────────────────
def t_coverage_stats():
    stats = WalkStats()
    walk_andp_semantic(nibrs_snapshot(), stats=stats)
    check("undocumented deployed column counted", stats.columns_undocumented == 1,
          f"got {stats.columns_undocumented}")
    check("entities counted", stats.entities_logical == 2)
    check("attributes counted", stats.attributes_logical == 3)
    check("physical tables counted", stats.tables_physical == 2)


def t_orphan_column_counted():
    snap = nibrs_snapshot()
    snap.column_metadata.append({
        "database_name": "NIBRS_Source_Domain", "table_name": "NotRegistered",
        "column_name": "X", "is_active": 1,
    })
    stats = WalkStats()
    ents, _ = walk_andp_semantic(snap, stats=stats)
    check("orphan column counted", stats.orphan_columns == 1)
    check("orphan column not emitted",
          find_entity(ents, "NIBRS_Source.NotRegistered.X", "attribute") is None)


def t_column_not_deployed_counted():
    snap = nibrs_snapshot()
    snap.physical_columns.pop(("nibrs_source_domain", "victim", "v4019"))
    stats = WalkStats()
    walk_andp_semantic(snap, stats=stats)
    check("documented-but-not-deployed counted", stats.columns_not_deployed == 1)


# ─────────────────────────────────────────────────────────────────────────────
# 7. options
# ─────────────────────────────────────────────────────────────────────────────
def t_options():
    ents, _ = walk_andp_semantic(
        nibrs_snapshot(), WalkOptions(layers=frozenset({LAYER_LOGICAL})))
    check("logical-only emits no physical nodes",
          not ents_by_type(ents, "table") and not ents_by_type(ents, "column")
          and not ents_by_type(ents, "schema"))
    check("logical-only still emits entities", len(ents_by_type(ents, "entity")) == 2)

    ents2, _ = walk_andp_semantic(nibrs_snapshot(), WalkOptions(include_columns=False))
    check("include_columns=False drops attributes", not ents_by_type(ents2, "attribute"))
    check("include_columns=False keeps tables", len(ents_by_type(ents2, "table")) == 2)

    ents3, _ = walk_andp_semantic(
        nibrs_snapshot(), WalkOptions(include_non_business_modules=False))
    check("business-only drops the Semantic module domain",
          find_entity(ents3, "NIBRS_Source.Semantic", "domain") is None)
    check("business-only keeps the Domain module",
          find_entity(ents3, "NIBRS_Source.Domain", "domain") is not None)


def t_value_code_cap_option():
    snap = nibrs_snapshot()
    snap.column_metadata[0]["allowed_values_json"] = json.dumps(
        [{"value": str(i), "label": f"L{i}"} for i in range(50)])
    ents, _ = walk_andp_semantic(snap, WalkOptions(max_value_codes=5))
    attr = find_entity(ents, "NIBRS_Source.Victim.V4019", "attribute")
    check("max_value_codes honoured", len(attr["properties"]["allowed_values"]) == 5)


# ─────────────────────────────────────────────────────────────────────────────
# 8. catalog drift across products built to the same standard
# ─────────────────────────────────────────────────────────────────────────────
def t_catalog_drift():
    ents, _ = walk_andp_semantic(case_snapshot())
    ent = find_entity(ents, "CASE_Source.Provenance Record", "entity")
    check("CASE entity emitted", ent is not None)
    check("ontology IRI carried when present",
          ent and ent["properties"].get("ontology_iri") == "case-investigation:ProvenanceRecord")
    check("view name carried",
          ent and ent["properties"].get("deployed_view") == "v_chain_of_custody")

    attr = find_entity(ents, "CASE_Source.Provenance Record.exhibit_number", "attribute")
    check("property IRI carried when present",
          attr and attr["properties"].get("property_iri") == "case-investigation:exhibitNumber")

    nib, _ = walk_andp_semantic(nibrs_snapshot())
    nib_ent = find_entity(nib, "NIBRS_Source.Victim", "entity")
    check("absent IRI does not become an empty property",
          nib_ent and "ontology_iri" not in nib_ent["properties"])
    check("NIBRS aliases resolved (entity_description)",
          nib_ent and nib_ent["properties"].get("description") == "One row per victim")
    check("NIBRS aliases resolved (surrogate_key_column)",
          nib_ent and nib_ent["properties"].get("primary_key") == "V4001")


def t_product_knowledge_emitted_and_linked():
    """The product-description layer is only useful if it is ATTACHED to what it
    describes — an unlinked node is a footnote the agent never meets."""
    stats = WalkStats()
    ents, rels = walk_andp_semantic(nibrs_snapshot(), stats=stats)

    # glossary → business_concept, linked to the attribute it defines
    term = find_entity(ents, "NIBRS_Source.term:Victim", "business_concept")
    check("glossary term emitted", term is not None)
    check("glossary definition carried",
          term and term["properties"]["definition"] == "A person harmed in an incident")
    linked = {(r["source_name"], r["target_name"]) for r in rels_of(rels, "relates_to")}
    check("glossary term links to the attribute it defines",
          ("NIBRS_Source.term:Victim", "NIBRS_Source.Victim.V4019") in linked)
    check("unlinkable term falls back to the product root",
          ("NIBRS_Source.term:NIBRS", "NIBRS_Source") in linked)
    # multi-column related_column: one edge per resolvable column, never narrowed to the first
    check("a term declaring several columns links to EACH resolvable one",
          ("NIBRS_Source.term:Identified Victim", "NIBRS_Source.Victim.V4019") in linked
          and ("NIBRS_Source.term:Identified Victim", "NIBRS_Source.Victim.INCNUM") in linked)
    check("an unresolvable column in the list is not invented and does not force the entity fallback",
          ("NIBRS_Source.term:Identified Victim", "NIBRS_Source.Victim.NOPE") not in linked
          and ("NIBRS_Source.term:Identified Victim", "NIBRS_Source.Victim") not in linked)

    # query recipe → linked to the entity its SQL names
    recipe = find_entity(ents, "NIBRS_Source.recipe:Count victims by sex", "query_recipe")
    check("query recipe emitted", recipe is not None)
    check("recipe carries its SQL", recipe and "SELECT V4019" in recipe["properties"]["sql"])
    deps = {(r["source_name"], r["target_name"]) for r in rels_of(rels, "depends_on")}
    check("recipe linked to the entity its SQL names",
          ("NIBRS_Source.recipe:Count victims by sex", "NIBRS_Source.Victim") in deps)

    # a DECLARED target beats the SQL heuristic
    check("declared target_tables wins over SQL inference",
          ("NIBRS_Source.recipe:Declared target wins", "NIBRS_Source.Incident") in deps
          and ("NIBRS_Source.recipe:Declared target wins", "NIBRS_Source.Victim") not in deps)

    # design decision → linked to what it shaped; superseded one excluded
    adr = find_entity(ents, "NIBRS_Source.decision:Lossless VARCHAR landing", "design_decision")
    check("design decision emitted", adr is not None)
    check("decision rationale carried",
          adr and adr["properties"]["rationale"] == "Preserve the source image")
    check("decision linked to the entity it affects",
          ("NIBRS_Source.decision:Lossless VARCHAR landing", "NIBRS_Source.Victim") in linked)
    check("superseded decision (is_current=0) excluded",
          find_entity(ents, "NIBRS_Source.decision:Superseded choice",
                      "design_decision") is None)

    # naming standard → governs the product
    std = find_entity(ents, "NIBRS_Source.standard:PATTERN: source-name-preserved",
                      "naming_standard")
    check("naming standard emitted", std is not None)
    check("naming standard carries its guidance",
          std and "column_metadata" in std["properties"]["usage_guidance"])

    # quality metric → measures the entity
    qm = find_entity(ents,
                     "NIBRS_Source.quality:NIBRS_Source_Domain.Victim:completeness",
                     "quality_metric")
    check("quality metric emitted", qm is not None)
    check("quality metric keeps its value", qm and qm["properties"]["metric_value"] == 1.0)
    measures = {(r["source_name"], r["target_name"]) for r in rels_of(rels, "measures")}
    check("quality metric measures the entity",
          (qm["name"], "NIBRS_Source.Victim") in measures if qm else False)

    check("stats count the layer",
          stats.glossary_terms == 3 and stats.query_recipes == 2
          and stats.design_decisions == 1 and stats.naming_standards == 1
          and stats.quality_metrics == 1,
          str(stats.as_dict()))


def t_module_registry_enriches_across_casing():
    """The registry cases module names differently from the product map; enrichment
    must survive that rather than silently doing nothing."""
    stats = WalkStats()
    ents, _ = walk_andp_semantic(nibrs_snapshot(), stats=stats)
    dom = find_entity(ents, "NIBRS_Source.Domain", "domain")
    check("domain enriched from a differently-cased registry row",
          dom and dom["properties"].get("module_version") == "1.0.0",
          str(dom and dom["properties"]))
    check("owner carried", dom and dom["properties"].get("data_owner") == "Data Team")
    check("enrichment counted", stats.modules_enriched == 1)
    check("registry does not create a duplicate domain node",
          len(ents_by_type(ents, "domain")) == 2)


def t_lineage_internal_vs_external():
    """Table-to-table lineage becomes an edge; an EXTERNAL file source becomes
    provenance on the target, because inventing a node for a .tsv would fabricate a
    table that does not exist."""
    stats = WalkStats()
    ents, rels = walk_andp_semantic(nibrs_snapshot(), stats=stats)

    derives = {(r["source_name"], r["target_name"]) for r in rels_of(rels, "derives_from")}
    check("table-to-table lineage becomes an edge (target derives from source)",
          ("NIBRS_Source_Domain.Victim", "NIBRS_Source_Domain.Incident") in derives)
    check("internal lineage counted", stats.lineage_edges == 1)

    victim = find_entity(ents, "NIBRS_Source_Domain.Victim", "table")
    check("external source recorded as provenance on the target",
          victim and victim["properties"].get("derives_from_external")
          == ["39270-0004-Data.tsv"])
    check("load job carried", victim and victim["properties"].get("load_job")
          == "load_nibrs_victim")
    check("external lineage counted separately, not as a drop",
          stats.lineage_external == 1 and stats.lineage_dropped == 0)
    check("no node invented for the external file",
          find_entity(ents, "39270-0004-Data.tsv", "table") is None)


def t_product_knowledge_can_be_disabled():
    ents, _ = walk_andp_semantic(
        nibrs_snapshot(), WalkOptions(include_product_knowledge=False))
    for etype in ("business_concept", "query_recipe", "design_decision",
                  "naming_standard", "quality_metric"):
        check(f"'{etype}' suppressed when product knowledge is off",
              not ents_by_type(ents, etype))
    check("structure still emitted", len(ents_by_type(ents, "entity")) == 2)


def t_db_native_values_are_json_safe():
    """Property values arrive as driver types (Teradata returns Decimal for numerics,
    date/datetime for temporals).  The graph store serializes properties to JSON, so
    an unconverted value fails the ENTIRE import — caught live before this test existed."""
    from datetime import date as _date, datetime as _dt
    from decimal import Decimal as _D
    snap = nibrs_snapshot()
    snap.quality_metrics = [{
        "database_name": "NIBRS_Source_Domain", "table_name": "Victim",
        "metric_name": "completeness", "metric_value": _D("0.9975"),
        "quality_threshold": _D("0.95"), "sample_size": _D("1391643"),
        "measured_dts": _dt(2026, 8, 12, 10, 30), "is_active": 1,
    }]
    snap.design_decisions = [{
        "decision_id": "DD-9", "decision_title": "Dated decision",
        "decided_date": _date(2026, 7, 1), "is_current": 1,
    }]
    ents, rels = walk_andp_semantic(snap)
    try:
        json.dumps({"e": ents, "r": rels})
        check("the whole payload is JSON-serializable", True)
    except TypeError as exc:
        check("the whole payload is JSON-serializable", False, str(exc))

    qm = find_entity(ents,
                     "NIBRS_Source.quality:NIBRS_Source_Domain.Victim:completeness",
                     "quality_metric")
    check("fractional Decimal becomes float",
          qm and qm["properties"]["metric_value"] == 0.9975)
    check("whole Decimal becomes int, not 1391643.0",
          qm and qm["properties"]["sample_size"] == 1391643
          and isinstance(qm["properties"]["sample_size"], int))
    check("datetime becomes an ISO string",
          qm and qm["properties"]["measured_at"].startswith("2026-08-12T"))


def t_product_bridges_state_their_basis():
    """A declared cross-product link is a CLAIM, not a join.

    The reference product declares one whose own `linkage_mechanism` reads
    "Synthetic Crosswalk Key" and whose target (`incident_Current`) was never
    deployed — it was authored against an assumed bi-temporal product. The bridge is
    therefore surfaced WITH its basis and its resolution status, and no edge is
    emitted to a node that does not exist.
    """
    snap = nibrs_snapshot()
    snap.product_crosswalks = [
        {"semantic_bridge_concept": "Investigation To Incident",
         "case_entity_type": "Victim", "case_property_or_facet": "INCNUM",
         "nibrs_target_table": "incident_Current", "nibrs_target_column": "incident_number",
         "linkage_mechanism": "Synthetic Crosswalk Key",
         "mapping_notes": "Links evidence to incident report",
         "_crosswalk_table": "nibrs_crosswalk", "is_active": 1},
        {"semantic_bridge_concept": "Resolvable Link",
         "case_entity_type": "Victim", "nibrs_target_table": "Incident",
         "linkage_mechanism": "Shared Key", "is_active": 1},
    ]
    stats = WalkStats()
    ents, rels = walk_andp_semantic(snap, stats=stats)

    b = find_entity(ents, "NIBRS_Source.bridge:Investigation To Incident", "product_bridge")
    check("bridge emitted", b is not None)
    check("bridge states its basis verbatim",
          b and b["properties"]["linkage_mechanism"] == "Synthetic Crosswalk Key")
    check("bridge reports its dangling target",
          b and "not found" in b["properties"]["resolution"]
          and b["properties"]["target_resolved"] is False)
    check("bridge records where it was declared",
          b and b["properties"]["declared_in"] == "nibrs_crosswalk")
    check("no edge invented to the missing target",
          not any(r["target_name"] == "incident_Current" for r in rels))
    check("bridge still attaches to the side that DOES resolve",
          any(r["source_name"] == b["name"] and r["target_name"] == "NIBRS_Source.Victim"
              for r in rels_of(rels, "relates_to")))

    ok = find_entity(ents, "NIBRS_Source.bridge:Resolvable Link", "product_bridge")
    check("resolvable bridge marked resolved",
          ok and ok["properties"]["target_resolved"] is True)
    check("resolvable bridge links to its target",
          any(r["source_name"] == ok["name"] and r["target_name"] == "NIBRS_Source.Incident"
              for r in rels_of(rels, "relates_to")))
    check("resolution counted honestly",
          stats.product_bridges == 2 and stats.bridges_resolved == 1
          and stats.bridges_unresolved == 1, str(stats.as_dict()))

    check("an undeclared basis is named, never blank",
          walk_andp_semantic(nibrs_snapshot(product_crosswalks=[
              {"semantic_bridge_concept": "X", "case_entity_type": "Victim",
               "nibrs_target_table": "Incident", "is_active": 1}]))[0]
          and find_entity(walk_andp_semantic(nibrs_snapshot(product_crosswalks=[
              {"semantic_bridge_concept": "X", "case_entity_type": "Victim",
               "nibrs_target_table": "Incident", "is_active": 1}]))[0],
              "NIBRS_Source.bridge:X", "product_bridge")["properties"]["linkage_mechanism"]
          == "undeclared")


def t_bridge_resolves_across_products():
    """In a graph already holding another product, a bridge target can resolve to it."""
    snap = nibrs_snapshot()
    snap.product_crosswalks = [{
        "semantic_bridge_concept": "To Evidence", "case_entity_type": "Victim",
        "nibrs_target_table": "provenance", "linkage_mechanism": "Shared Key",
        "is_active": 1}]
    # entities already in the graph, from a previously-added product
    known = [("CASE_Source.Provenance Record", "provenance")]
    stats = WalkStats()
    ents, rels = walk_andp_semantic(snap, stats=stats, known_entities=known)
    b = find_entity(ents, "NIBRS_Source.bridge:To Evidence", "product_bridge")
    check("cross-product target resolves", b and b["properties"]["target_resolved"] is True)
    check("edge points at the other product's entity",
          any(r["target_name"] == "CASE_Source.Provenance Record"
              for r in rels_of(rels, "relates_to")))
    check("without the other product it would dangle",
          walk_andp_semantic(snap, stats=WalkStats())[0]
          and find_entity(walk_andp_semantic(snap, stats=WalkStats())[0],
                          "NIBRS_Source.bridge:To Evidence",
                          "product_bridge")["properties"]["target_resolved"] is False)


def t_two_products_do_not_collide():
    """The reason names are qualified: one graph may hold two products."""
    a, _ = walk_andp_semantic(nibrs_snapshot())
    b, _ = walk_andp_semantic(case_snapshot())
    a_keys = {(e["name"], e["entity_type"]) for e in a}
    b_keys = {(e["name"], e["entity_type"]) for e in b}
    check("no key collision across products", not (a_keys & b_keys),
          f"colliding: {sorted(a_keys & b_keys)[:5]}")


# ─────────────────────────────────────────────────────────────────────────────
# 9. contract guarantees
# ─────────────────────────────────────────────────────────────────────────────
def t_determinism_and_purity():
    snap = nibrs_snapshot()
    before = json.dumps({
        "map": snap.data_product_map, "ent": snap.entity_metadata,
        "col": snap.column_metadata, "rel": snap.table_relationship,
    }, sort_keys=True)
    e1, r1 = walk_andp_semantic(snap)
    e2, r2 = walk_andp_semantic(snap)
    after = json.dumps({
        "map": snap.data_product_map, "ent": snap.entity_metadata,
        "col": snap.column_metadata, "rel": snap.table_relationship,
    }, sort_keys=True)
    check("deterministic output", json.dumps(e1) == json.dumps(e2)
          and json.dumps(r1) == json.dumps(r2))
    check("input not mutated", before == after)


def t_import_bulk_shape():
    ents, rels = walk_andp_semantic(nibrs_snapshot())
    # Read the vocabulary from its single source rather than restating it — a
    # hardcoded copy here would pass while the real contract had moved on.
    from trusted_data_agent.kgstore.types import ENTITY_TYPES, RELATIONSHIP_TYPES
    valid_e, valid_r = ENTITY_TYPES, RELATIONSHIP_TYPES
    check("every entity has the required keys",
          all({"name", "entity_type", "properties", "source"} <= set(e) for e in ents))
    check("every entity_type is valid",
          all(e["entity_type"] in valid_e for e in ents),
          str({e["entity_type"] for e in ents} - valid_e))
    check("every relationship_type is valid",
          all(r["relationship_type"] in valid_r for r in rels),
          str({r["relationship_type"] for r in rels} - valid_r))
    check("every entity carries a layer",
          all(e["properties"].get("layer") in (LAYER_LOGICAL, LAYER_PHYSICAL) for e in ents))
    check("physical entities carry infrastructure",
          all(e["properties"].get("infrastructure") == "teradata"
              for e in ents if e["properties"]["layer"] == LAYER_PHYSICAL))
    names = {(e["name"], e["entity_type"]) for e in ents}
    check("entity keys are unique", len(names) == len(ents))
    endpoints = {(r["source_name"], r["source_type"]) for r in rels} | \
                {(r["target_name"], r["target_type"]) for r in rels}
    check("every relationship endpoint exists as an entity",
          endpoints <= names, str(sorted(endpoints - names)[:5]))


def t_errors():
    try:
        walk_andp_semantic(SemanticSnapshot(product="", semantic_database="x"))
        check("empty product rejected", False, "no raise")
    except WalkError:
        check("empty product rejected", True)
    try:
        walk_andp_semantic(nibrs_snapshot(), WalkOptions(layers=frozenset({"quantum"})))
        check("unknown layer rejected", False, "no raise")
    except WalkError:
        check("unknown layer rejected", True)
    try:
        walk_andp_semantic({"not": "a snapshot"})
        check("bad snapshot rejected", False, "no raise")
    except WalkError:
        check("bad snapshot rejected", True)


def t_empty_catalog_degrades():
    ents, rels = walk_andp_semantic(
        SemanticSnapshot(product="Empty_Source", semantic_database="Empty_Semantic"))
    check("empty product still yields its root", len(ents) == 1
          and ents[0]["entity_type"] == "database")
    check("empty product yields no relationships", rels == [])


# ─────────────────────────────────────────────────────────────────────────────
# 10. trusted scope — what makes a refusal provable
# ─────────────────────────────────────────────────────────────────────────────
def t_scope_databases():
    snap = nibrs_snapshot()
    check("business-only scope", scope_databases(snap) == ["NIBRS_Source_Domain"])
    check("all-module scope includes the semantic db",
          scope_databases(snap, business_only=False)
          == ["NIBRS_Source_Domain", "NIBRS_Source_Semantic"])
    check("empty map yields empty scope",
          scope_databases(SemanticSnapshot(product="P", semantic_database="S")) == [])


def t_multi_product_descriptor():
    """A graph holding N products must resync ALL of them.

    A KG stores exactly ONE source descriptor, so appending a second product would
    otherwise overwrite the first — and the next resync would rebuild only the last
    product, silently DROPPING the other's nodes from the graph.
    """
    from trusted_data_agent.agent.kg_constructors.andp_semantic import _merge_descriptor

    a = {"discovery_mode": "andp_semantic", "product": "A", "connection_id": "c1"}
    one = _merge_descriptor(None, a)
    check("a single product keeps the flat shape", one.get("product") == "A"
          and "products" not in one)

    two = _merge_descriptor(one, {"discovery_mode": "andp_semantic", "product": "B"})
    check("a second product produces a products list",
          [p["product"] for p in two["products"]] == ["A", "B"])
    check("the multi-product descriptor still names the mode",
          two.get("discovery_mode") == "andp_semantic")

    again = _merge_descriptor(two, {"product": "A", "connection_id": "c2"})
    check("re-adding a product replaces rather than duplicates it",
          sorted(p["product"] for p in again["products"]) == ["A", "B"])
    check("the replacement is the NEW entry",
          next(p for p in again["products"] if p["product"] == "A")["connection_id"] == "c2")

    legacy = _merge_descriptor({"product": "Legacy"}, {"product": "New"})
    check("a pre-existing flat descriptor is preserved, not dropped",
          sorted(p["product"] for p in legacy["products"]) == ["Legacy", "New"])



# ─────────────────────────────────────────────────────────────────────────────
# 6b. deployed-but-undocumented columns reach the PHYSICAL layer
#
# The product curates `column_metadata`; a conformant product may curate only part
# of it. Before this, an undocumented deployed column was counted and discarded, so
# the agent saw a table with one known column and invented the rest — measured on a
# live product: five runs, five wrong column guesses, one wasted round-trip each.
# ─────────────────────────────────────────────────────────────────────────────
def t_undocumented_column_emitted_physical_only():
    snap = nibrs_snapshot()   # fixture carries deployed-but-undocumented Victim.V4018
    ents, rels = walk_andp_semantic(snap)

    col = find_entity(ents, "NIBRS_Source_Domain.Victim.V4018", "column")
    check("undocumented deployed column is emitted", col is not None)
    if col:
        p = col.get("properties") or {}
        check("it carries the DEPLOYED type", p.get("datatype") == "VARCHAR(3)",
              f"got {p.get('datatype')}")
        check("it is marked undocumented", p.get("documented") is False,
              f"got {p.get('documented')}")
        check("it is on the physical layer", p.get("layer") == "physical",
              f"got {p.get('layer')}")

    # ...and ONLY there: no logical attribute, no implements edge.
    check("no logical attribute is invented for it",
          find_entity(ents, "NIBRS_Source.Victim.V4018", "attribute") is None)
    implements = [r for r in rels if r.get("relationship_type") == "implements"
                  and "V4018" in str(r.get("source_name", ""))]
    check("no implements edge (there is nothing to implement)", not implements,
          f"got {implements}")

    contains = [r for r in rels if r.get("relationship_type") == "contains"
                and r.get("target_name") == "NIBRS_Source_Domain.Victim.V4018"]
    check("it hangs off its physical table", len(contains) == 1
          and contains[0].get("source_name") == "NIBRS_Source_Domain.Victim",
          f"got {contains}")


def t_undocumented_column_absent_without_physical_layer():
    """Logical-only walks are unchanged — the physical layer is where these live."""
    ents, _ = walk_andp_semantic(nibrs_snapshot(),
                                 WalkOptions(layers=frozenset({"logical"})))
    check("no physical column node when the physical layer is off",
          find_entity(ents, "NIBRS_Source_Domain.Victim.V4018", "column") is None)
    names = {e["name"] for e in ents}
    check("and no stray attribute either", "NIBRS_Source.Victim.V4018" not in names)


def t_undocumented_column_does_not_disturb_the_logical_model():
    """The logical model must be byte-identical with and without the emission."""
    snap = nibrs_snapshot()
    ents, rels = walk_andp_semantic(snap)
    logical = sorted((e["name"], e["entity_type"]) for e in ents
                     if (e.get("properties") or {}).get("layer") != "physical")

    snap2 = nibrs_snapshot()
    snap2.physical_columns.pop(("nibrs_source_domain", "victim", "V4018"))
    ents2, _ = walk_andp_semantic(snap2)
    logical2 = sorted((e["name"], e["entity_type"]) for e in ents2
                      if (e.get("properties") or {}).get("layer") != "physical")
    check("logical model unchanged by an undocumented deployed column",
          logical == logical2,
          f"delta={set(logical) ^ set(logical2)}")


def t_undocumented_column_on_unregistered_table_is_ignored():
    """A deployed table the product never registers stays out of the graph."""
    snap = nibrs_snapshot()
    snap.physical_columns[("nibrs_source_domain", "ghost_table", "c1")] = {
        "datatype": "VARCHAR(9)", "nullable": True}
    stats = WalkStats()
    ents, _ = walk_andp_semantic(snap, stats=stats)
    names = {e["name"] for e in ents}
    check("no node for a column of an unregistered table",
          not any("ghost_table" in n for n in names))
    # The node is already impossible (no physical table to hang it off), so the
    # registered-table guard earns its place HERE: without it the coverage
    # statistic silently counts columns of tables the product never registered.
    check("nor is it counted against coverage", stats.columns_undocumented == 1,
          f"got {stats.columns_undocumented}")


for fn in [
    t_undocumented_column_emitted_physical_only,
    t_undocumented_column_absent_without_physical_layer,
    t_undocumented_column_does_not_disturb_the_logical_model,
    t_undocumented_column_on_unregistered_table_is_ignored,
    t_multi_product_descriptor,
    t_topology, t_type_rule, t_type_length_is_not_a_mismatch, t_governance_payload,
    t_value_code_parsing, t_temporal_declaration, t_relationships,
    t_unresolvable_relationship_is_dropped_and_counted,
    t_physical_fk_needs_existing_column_nodes,
    t_ambiguous_bare_table_is_not_guessed, t_coverage_stats, t_orphan_column_counted,
    t_column_not_deployed_counted, t_options, t_value_code_cap_option,
    t_catalog_drift, t_product_knowledge_emitted_and_linked,
    t_module_registry_enriches_across_casing, t_lineage_internal_vs_external,
    t_product_knowledge_can_be_disabled, t_db_native_values_are_json_safe,
    t_product_bridges_state_their_basis, t_bridge_resolves_across_products,
    t_two_products_do_not_collide, t_determinism_and_purity,
    t_import_bulk_shape, t_errors, t_empty_catalog_degrades, t_scope_databases,
]:
    fn()

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
