"""
CI-pure tests for the declared-model (ISO/IEC 11179 registry) KG walker.

No database, no network — the walker is pure by contract.  The fixture mirrors the
real registry shape (Policing_Core_Semantic) and exercises the full mapping: the type
rule, decoded value codes, PII, and every association kind including the cross-domain
type_correspondence → product_bridge.

Run: python3 test/test_declared_model_walker.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.kg_constructors.declared_model_walker import (  # noqa: E402
    DeclaredModelSnapshot,
    WalkError,
    WalkOptions,
    WalkStats,
    walk_declared_model,
)
from trusted_data_agent.kgstore.types import (  # noqa: E402
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
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


def find(entities, name, etype):
    for e in entities:
        if e["name"] == name and e["entity_type"] == etype:
            return e
    return None


def rels_of(rels, rtype):
    return [r for r in rels if r["relationship_type"] == rtype]


# ─────────────────────────────────────────────────────────────────────────────
# fixture — a small but complete ISO/IEC 11179 registry
# ─────────────────────────────────────────────────────────────────────────────

def snapshot(**over):
    snap = DeclaredModelSnapshot(
        model_name="Policing Core",
        catalog_database="Policing_Core_Semantic",
        object_classes=[
            {"object_class_id": "Incident", "name": "Incident", "subject_area": "crime",
             "object_kind": "view", "definition": "One incident (ORI, INCNUM)",
             "natural_key": "ORI,INCNUM", "standard_class_iri": "nc:Incident",
             "deployed_object": "Policing_Core.v_incident"},
            {"object_class_id": "Victim", "name": "Victim", "subject_area": "crime",
             "object_kind": "view", "definition": "One victim within an incident",
             "natural_key": "ORI,INCNUM,V4006", "deployed_object": "Policing_Core.v_victim"},
            {"object_class_id": "Offence", "name": "Offence", "subject_area": "crime",
             "object_kind": "view", "deployed_object": "Policing_Core.v_offence"},
            {"object_class_id": "EvidenceObject", "name": "Evidence Object",
             "subject_area": "evidence", "object_kind": "table",
             "standard_class_iri": "uco-observable:ObservableObject",
             "deployed_object": "Policing_Core.evidence_object"},
            # a logical supertype — no deployed object, no physical layer
            {"object_class_id": "AbstractParty", "name": "Party", "subject_area": "shared",
             "object_kind": "logical", "definition": "A person in any role"},
        ],
        data_elements=[
            {"object_class_id": "Incident", "property_name": "INCIDENT_STATE",
             "data_element_id": "Incident.INCIDENT_STATE", "declared_type": "VARCHAR",
             "value_domain_id": "state", "semantic_type": "category",
             "source_column": "NIBRS_Source_Domain.Incident.STATE"},
            {"object_class_id": "Victim", "property_name": "SEX",
             "data_element_id": "Victim.SEX", "declared_type": "INTEGER",  # declared vs deployed
             "value_domain_id": "sex", "is_pii": 1, "sensitivity": "CONFIDENTIAL",
             "standard_property_iri": "nc:PersonSexCode"},
            {"object_class_id": "EvidenceObject", "property_name": "OBJECT_ID",
             "data_element_id": "EvidenceObject.OBJECT_ID", "declared_type": "VARCHAR",
             "semantic_type": "identifier"},
        ],
        value_domains=[
            {"value_domain_id": "state", "name": "FBI state code",
             "standard_scheme_iri": "http://example/nibrs/state"},
            {"value_domain_id": "sex", "name": "Person sex"},
        ],
        permissible_values=[
            {"value_domain_id": "sex", "code": "0", "meaning": "Female", "ordinal": 1},
            {"value_domain_id": "sex", "code": "1", "meaning": "Male", "ordinal": 2},
            {"value_domain_id": "state", "code": "42", "meaning": "Texas", "ordinal": 1},
        ],
        associations=[
            {"association_id": "incident_has_victim", "from_object_class": "Victim",
             "to_object_class": "Incident", "relationship_kind": "pk_fk_join",
             "cardinality": "N:1", "from_columns": "ORI,INCNUM", "to_columns": "ORI,INCNUM",
             "join_sql": "v.ORI = i.ORI AND v.INCNUM = i.INCNUM"},
            {"association_id": "victim_is_party", "from_object_class": "Victim",
             "to_object_class": "AbstractParty", "relationship_kind": "is_a",
             "standard_relation_iri": "rdfs:subClassOf"},
            {"association_id": "offence_artefact", "from_object_class": "Offence",
             "to_object_class": "EvidenceObject", "relationship_kind": "type_correspondence",
             "basis": "computer-equipment offence implies disk/registry artefact classes",
             "from_columns": "offence_code", "to_columns": "primary_type",
             "from_values": "120, 220", "to_values": "uco-observable:Disk,uco-observable:WindowsRegistryKey",
             "source_declaration": "CASE DD-CONCEPT-001"},
            # unresolved: a join whose target entity does not exist -> dropped
            {"association_id": "dangling_fk", "from_object_class": "Incident",
             "to_object_class": "NoSuchEntity", "relationship_kind": "pk_fk_join"},
            # unresolved type-correspondence: kept as a bridge, reported unresolved
            {"association_id": "ghost_bridge", "from_object_class": "Offence",
             "to_object_class": "GhostEntity", "relationship_kind": "type_correspondence",
             "basis": "declared but its target was never modelled"},
        ],
        physical_columns={
            ("policing_core", "v_incident", "incident_state"):
                {"datatype": "VARCHAR(2)", "nullable": True, "column": "INCIDENT_STATE"},
            ("policing_core", "v_victim", "sex"):
                {"datatype": "VARCHAR(2)", "nullable": True, "column": "SEX"},
            ("policing_core", "evidence_object", "object_id"):
                {"datatype": "VARCHAR(255)", "nullable": False, "column": "OBJECT_ID"},
        },
        source_detail="test",
    )
    for k, v in over.items():
        setattr(snap, k, v)
    return snap


# ─────────────────────────────────────────────────────────────────────────────
# [1] structure — database, domains, entities, physical layer
# ─────────────────────────────────────────────────────────────────────────────
stats = WalkStats()
ents, rels = walk_declared_model(snapshot(), WalkOptions(), stats)

check("root database node", find(ents, "Policing Core", "database") is not None)
check("domain crime", find(ents, "Policing Core.crime", "domain") is not None)
check("domain evidence", find(ents, "Policing Core.evidence", "domain") is not None)
check("domain shared", find(ents, "Policing Core.shared", "domain") is not None)
check("stats.domains == 3", stats.domains == 3, stats.domains)

inc = find(ents, "Policing Core.Incident", "entity")
check("entity Incident", inc is not None)
check("entity carries ontology_iri", inc and inc["properties"].get("ontology_iri") == "nc:Incident")
check("entity carries deployed_table", inc and inc["properties"].get("deployed_table") == "Policing_Core.v_incident")
check("entity contains domain edge",
      any(r["source_name"] == "Policing Core.crime" and r["target_name"] == "Policing Core.Incident"
          and r["relationship_type"] == "contains" for r in rels))

# physical layer
check("physical table v_incident",
      find(ents, "Policing_Core.v_incident", "table") is not None)
check("table implements entity",
      any(r["source_name"] == "Policing_Core.v_incident" and r["target_name"] == "Policing Core.Incident"
          and r["relationship_type"] == "implements" for r in rels))
check("schema node emitted", find(ents, "Policing_Core", "schema") is not None)
check("stats.tables_physical == 4", stats.tables_physical == 4, stats.tables_physical)

# logical supertype has NO physical realization
check("AbstractParty entity exists", find(ents, "Policing Core.AbstractParty", "entity") is not None)
check("AbstractParty has no table", find(ents, "Policing_Core.abstractparty", "table") is None)

# ─────────────────────────────────────────────────────────────────────────────
# [2] the type rule + decoded values + PII
# ─────────────────────────────────────────────────────────────────────────────
sex = find(ents, "Policing Core.Victim.SEX", "attribute")
check("attribute SEX exists", sex is not None)
check("declared_type kept as meaning", sex and sex["properties"].get("declared_type") == "INTEGER")
check("deployed_type from dictionary", sex and sex["properties"].get("deployed_type") == "VARCHAR(2)")
check("type mismatch counted (INTEGER vs VARCHAR)", stats.type_mismatches == 1, stats.type_mismatches)
check("SEX is_pii True", sex and sex["properties"].get("is_pii") is True)
check("stats.pii_columns == 1", stats.pii_columns == 1, stats.pii_columns)

codes = sex["properties"].get("allowed_values") if sex else None
check("SEX carries decoded values", codes and {"value": "0", "label": "Female"} in codes)
check("value codes ordered", codes and [c["value"] for c in codes] == ["0", "1"])
check("stats.columns_with_value_codes == 2", stats.columns_with_value_codes == 2, stats.columns_with_value_codes)

state = find(ents, "Policing Core.Incident.INCIDENT_STATE", "attribute")
check("aligned declared/deployed type is no mismatch",
      state and state["properties"].get("deployed_type") == "VARCHAR(2)")

# physical column carries the AUTHORITATIVE deployed type + implements the attribute
col = find(ents, "Policing_Core.v_victim.SEX", "column")
check("physical column datatype authoritative", col and col["properties"].get("datatype") == "VARCHAR(2)")
check("column implements attribute",
      any(r["source_name"] == "Policing_Core.v_victim.SEX" and r["relationship_type"] == "implements"
          for r in rels))

# ─────────────────────────────────────────────────────────────────────────────
# [3] associations — foreign_key, is_a, type_correspondence → product_bridge
# ─────────────────────────────────────────────────────────────────────────────
fk = rels_of(rels, "foreign_key")
check("one foreign_key edge (dangling dropped)", len(fk) == 1, len(fk))
check("foreign_key carries join_sql", fk and (fk[0].get("metadata") or {}).get("join_sql"))
check("stats.foreign_keys == 1", stats.foreign_keys == 1, stats.foreign_keys)

isa = rels_of(rels, "is_a")
check("one is_a edge", len(isa) == 1, len(isa))
check("is_a points subtype -> supertype",
      isa and isa[0]["source_name"] == "Policing Core.Victim"
      and isa[0]["target_name"] == "Policing Core.AbstractParty")
check("stats.is_a_edges == 1", stats.is_a_edges == 1)

bridges = [e for e in ents if e["entity_type"] == "product_bridge"]
check("two product_bridge nodes", len(bridges) == 2, len(bridges))
resolved = find(ents, "Policing Core.bridge.offence_artefact", "product_bridge")
check("a resolved bridge carries what it BINDS as data — the codes and values, split "
      "and trimmed, never only the prose basis",
      resolved["properties"].get("from_values") == ["120", "220"]
      and resolved["properties"].get("to_values") == ["uco-observable:Disk", "uco-observable:WindowsRegistryKey"]
      and resolved["properties"].get("from_columns") == "offence_code", resolved["properties"])
_ghost = find(ents, "Policing Core.bridge.ghost_bridge", "product_bridge")
check("a registry without the columns yields None membership, not an empty list",
      _ghost["properties"].get("from_values") is None and _ghost["properties"].get("to_values") is None,
      _ghost["properties"])
check("resolved bridge exists", resolved is not None)
check("bridge marked type_correspondence",
      resolved and resolved["properties"].get("linkage_mechanism") == "type_correspondence")
check("bridge carries basis", resolved and resolved["properties"].get("basis"))
check("resolved bridge target_resolved True", resolved and resolved["properties"].get("target_resolved") is True)
check("resolved bridge has two relates_to",
      len([r for r in rels_of(rels, "relates_to") if r["source_name"] == "Policing Core.bridge.offence_artefact"]) == 2)
ghost = find(ents, "Policing Core.bridge.ghost_bridge", "product_bridge")
check("ghost bridge kept as finding", ghost is not None)
check("ghost bridge target_resolved False", ghost and ghost["properties"].get("target_resolved") is False)
check("ghost bridge has ONE relates_to (only resolved end)",
      len([r for r in rels_of(rels, "relates_to") if r["source_name"] == "Policing Core.bridge.ghost_bridge"]) == 1)

check("stats.type_correspondences == 2", stats.type_correspondences == 2)
check("stats.bridges_resolved == 1", stats.bridges_resolved == 1)
check("stats.bridges_unresolved == 1", stats.bridges_unresolved == 1)
check("stats.associations_dropped == 1 (dangling fk)", stats.associations_dropped == 1, stats.associations_dropped)

# NEVER an instance join across the products
check("no cross-domain foreign_key",
      not any(r["relationship_type"] == "foreign_key"
              and {r["source_name"], r["target_name"]} ==
              {"Policing Core.Offence", "Policing Core.EvidenceObject"} for r in rels))

# ─────────────────────────────────────────────────────────────────────────────
# [4] vocabulary compliance — every emitted type is in the canonical set
# ─────────────────────────────────────────────────────────────────────────────
bad_ent = {e["entity_type"] for e in ents} - ENTITY_TYPES
check("all entity types in vocabulary", not bad_ent, bad_ent)
bad_rel = {r["relationship_type"] for r in rels} - RELATIONSHIP_TYPES
check("all relationship types in vocabulary", not bad_rel, bad_rel)

# ─────────────────────────────────────────────────────────────────────────────
# [5] determinism + options + guards
# ─────────────────────────────────────────────────────────────────────────────
e1, r1 = walk_declared_model(snapshot(), WalkOptions(), WalkStats())
e2, r2 = walk_declared_model(snapshot(), WalkOptions(), WalkStats())
check("deterministic entities", e1 == e2)
check("deterministic relationships", r1 == r2)

e_np, _ = walk_declared_model(snapshot(), WalkOptions(include_physical=False), WalkStats())
check("include_physical=False drops tables", not [e for e in e_np if e["entity_type"] == "table"])
check("include_physical=False drops columns", not [e for e in e_np if e["entity_type"] == "column"])
check("include_physical=False keeps entities", [e for e in e_np if e["entity_type"] == "entity"])

e_nc, _ = walk_declared_model(snapshot(), WalkOptions(include_columns=False), WalkStats())
check("include_columns=False drops attributes", not [e for e in e_nc if e["entity_type"] == "attribute"])

# The type rule compares BASE types, and the time-zone qualifier is meaning: a
# declared TIMESTAMP(6) WITH TIME ZONE against the dictionary's TIMESTAMP WITH TIME
# ZONE is NOT a divergence (it was counted as one while the normaliser split at the
# first "(" — 2 phantom mismatches on the real core model).
from trusted_data_agent.agent.kg_constructors.andp_semantic_walker import _norm_type  # noqa: E402
check("norm: precision inside the type is dropped, the tz qualifier kept",
      _norm_type("TIMESTAMP(6) WITH TIME ZONE") == _norm_type("TIMESTAMP WITH TIME ZONE") == "TIMESTAMP WITH TIME ZONE")
check("norm: a tz timestamp is still distinct from a plain one",
      _norm_type("TIMESTAMP(6)") != _norm_type("TIMESTAMP(6) WITH TIME ZONE"))
check("norm: length/charset are not meaning", _norm_type("VARCHAR(2) CHARACTER SET LATIN") == "VARCHAR")

try:
    walk_declared_model(snapshot(model_name=""), WalkOptions(), WalkStats())
    check("empty model_name raises", False)
except WalkError:
    check("empty model_name raises", True)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"declared_model walker: {PASS} passed, {FAIL} failed")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL: {f}")
    print(f"{'='*60}")
    sys.exit(1 if FAIL else 0)
