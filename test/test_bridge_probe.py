"""
CI-pure tests for the declared-cross-product-link probe.

A ``product_bridge`` is a CLAIM. The probe's job is to turn it into evidence when
that is possible and to say plainly why not when it isn't — never to imply a
verified join. Fixtures mirror the real reference deployment, including the bridge
whose target (`incident_Current`) was never deployed.

Run: python3 test/test_bridge_probe.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from components.builtin.knowledge_graph.bridge_probe import (  # noqa: E402
    build_probe_sql,
    index_entities,
    resolve_bridge_probe,
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


def ent(name, label, deployed_table):
    return {"name": name, "entity_type": "entity",
            "properties": {"label": label, "deployed_table": deployed_table}}


ENTITIES = [
    ent("NIBRS_Source.Incident", "Incident", "NIBRS_Source_Domain.Incident"),
    ent("NIBRS_Source.Offender", "Offender", "NIBRS_Source_Domain.Offender"),
    ent("CASE_Source.Investigative Action", "InvestigativeAction",
        "CASE_Source_Domain.investigative_action"),
]


def bridge(**props):
    base = {"label": props.get("label", "B"), "linkage_mechanism": "Shared Key"}
    base.update(props)
    return {"name": f"CASE_Source.bridge:{base['label']}", "entity_type": "product_bridge",
            "properties": base}


# ── 1. the SQL is readable evidence, not a bare count ───────────────────────
def t_probe_sql():
    overlap, sample = build_probe_sql("db.t1.col_a", "db2.t2.col_b")
    for frag in ("source_distinct", "target_distinct", "overlapping_values"):
        check(f"overlap query reports {frag}", frag in overlap,
              "a bare intersection count is unreadable without its denominators")
    check("identifiers are quoted", '"db"."t1"' in overlap and '"col_a"' in overlap)
    check("both sides appear", '"db2"."t2"' in overlap and '"col_b"' in overlap)
    check("sample query is bounded", "TOP 10" in sample)
    check("sample returns the shared values", "shared_value" in sample)

    check("an unqualified identifier yields no SQL",
          build_probe_sql("just_a_column", "db.t.c") == (None, None),
          "a probe must not be compiled against a name it cannot address")
    check("a two-part identifier yields no SQL",
          build_probe_sql("db.t", "db.t2") == (None, None))


# ── 2. a resolvable bridge compiles ─────────────────────────────────────────
def t_resolvable():
    by_name, by_label = index_entities(ENTITIES)
    p = resolve_bridge_probe(bridge(
        label="Action To Incident",
        source_reference="InvestigativeAction", source_property="action_id",
        target_reference="Incident", target_column="INCNUM",
        linkage_mechanism="Shared Key"), by_name, by_label)

    check("probe compiles", p.probeable and not p.blocked, p.blocked or "")
    check("source resolved to its deployed column",
          p.source_identifier == "CASE_Source_Domain.investigative_action.action_id",
          str(p.source_identifier))
    check("target resolved across products",
          p.target_identifier == "NIBRS_Source_Domain.Incident.INCNUM",
          str(p.target_identifier))
    check("the declared basis is carried, not dropped",
          p.linkage_mechanism == "Shared Key")
    check("evidence is not allowed to upgrade the basis",
          any("does not upgrade" in n for n in p.notes),
          "overlap is evidence about values, not proof the link is meaningful")
    check("serializes for the tool payload", p.as_dict()["probeable"] is True)


# ── 3. the honest blockers — the reference deployment's actual case ─────────
def t_missing_target_is_the_answer():
    """The real bridge points at `incident_Current`, which was never deployed."""
    by_name, by_label = index_entities(ENTITIES)
    p = resolve_bridge_probe(bridge(
        label="Investigation To Incident",
        source_reference="InvestigativeAction", source_property="action_id",
        target_reference="incident_Current", target_column="incident_number",
        linkage_mechanism="Synthetic Crosswalk Key"), by_name, by_label)

    check("not probeable", not p.probeable)
    check("blocked names the missing target",
          p.blocked and "incident_Current" in p.blocked, p.blocked or "")
    check("blocked explains there is nothing to measure",
          p.blocked and "nothing to measure" in p.blocked)
    check("no SQL is emitted for a target that does not exist",
          p.overlap_sql is None and p.sample_sql is None)
    check("the declared basis survives the block",
          p.linkage_mechanism == "Synthetic Crosswalk Key",
          "the basis is the most decision-relevant fact even when unprobeable")


def t_other_blockers():
    by_name, by_label = index_entities(ENTITIES)
    no_col = resolve_bridge_probe(bridge(
        label="No Column", source_reference="InvestigativeAction",
        target_reference="Incident"), by_name, by_label)
    check("a bridge naming no column is blocked, not guessed",
          not no_col.probeable and "column" in (no_col.blocked or ""))

    no_src = resolve_bridge_probe(bridge(
        label="Ghost Source", source_reference="NotAnEntity", source_property="x",
        target_reference="Incident", target_column="INCNUM"), by_name, by_label)
    check("a missing source is blocked and named",
          not no_src.probeable and "NotAnEntity" in (no_src.blocked or ""))

    undeployed = resolve_bridge_probe(bridge(
        label="Undeployed", source_reference="Logical Only", source_property="a",
        target_reference="Incident", target_column="INCNUM"),
        *index_entities(ENTITIES + [{"name": "X.Logical Only", "entity_type": "entity",
                                     "properties": {"label": "Logical Only"}}]))
    check("an entity with no deployed table cannot be probed",
          not undeployed.probeable and "deployed" in (undeployed.blocked or ""))


# ── 4. a facet-style reference names its own column ─────────────────────────
def t_facet_reference():
    by_name, by_label = index_entities(ENTITIES)
    p = resolve_bridge_probe(bridge(
        label="Facet", source_reference="InvestigativeAction",
        source_property="EmailAddressFacet.emailAddressValue",
        target_reference="Offender", target_column="OFFENDER_ID"), by_name, by_label)
    check("the last segment of a facet path is the column",
          p.source_identifier
          == "CASE_Source_Domain.investigative_action.emailAddressValue",
          str(p.source_identifier))


# ── 5. ambiguity is declined, never resolved arbitrarily ───────────────────
def t_ambiguous_label_not_guessed():
    """Two products may both have an 'Incident'. Attaching a probe to the wrong
    product's table is worse than declining to probe."""
    dupes = ENTITIES + [ent("OTHER.Incident", "Incident", "OTHER_Domain.Incident")]
    by_name, by_label = index_entities(dupes)
    check("an ambiguous label is left out of the index", "incident" not in by_label)

    p = resolve_bridge_probe(bridge(
        label="Ambiguous", source_reference="InvestigativeAction",
        source_property="action_id", target_reference="Incident",
        target_column="INCNUM"), by_name, by_label)
    check("an ambiguous target is blocked rather than guessed", not p.probeable)

    # a fully-qualified reference stays resolvable even when the bare label is not
    ok = resolve_bridge_probe(bridge(
        label="Qualified", source_reference="CASE_Source.Investigative Action",
        source_property="action_id", target_reference="NIBRS_Source.Incident",
        target_column="INCNUM"), by_name, by_label)
    check("a fully-qualified reference still resolves", ok.probeable, ok.blocked or "")


def t_index_only_logical_entities():
    mixed = ENTITIES + [{"name": "NIBRS_Source_Domain.Incident", "entity_type": "table",
                         "properties": {"label": "Incident"}}]
    by_name, _ = index_entities(mixed)
    check("physical tables are not indexed as entities",
          "NIBRS_Source_Domain.Incident" not in by_name,
          "a bridge resolves to logical entities; the deployed table comes from them")


# ── 6. a type_correspondence is a CLASS-level correspondence, not a probeable link ──
def t_type_correspondence():
    """A ``type_correspondence`` (offence kinds ↔ evidence kinds) has no per-record
    key: it is READ from its basis, never measured — and never mistaken for a bridge
    whose target is 'missing'."""
    by_name, by_label = index_entities(ENTITIES)
    p = resolve_bridge_probe(bridge(
        label="Offence To Evidence",
        from_object_class="Offence", to_object_class="CaseObject",
        basis="Robbery offences correspond to classes of digital evidence.",
        linkage_mechanism="type_correspondence"), by_name, by_label)
    check("a type_correspondence is not probeable at the row level", not p.probeable)
    check("it is named a CLASS-level correspondence, not a missing/absent target",
          p.blocked and "CLASS-level correspondence" in p.blocked
          and "does not exist" not in p.blocked, p.blocked or "")
    check("it points the agent to READ and state the correspondence from its basis",
          p.blocked and "from its basis" in (p.blocked or ""))
    check("no SQL is compiled for a class correspondence",
          p.overlap_sql is None and p.sample_sql is None)
    check("the mechanism is carried as type_correspondence",
          p.linkage_mechanism == "type_correspondence")


for fn in [t_probe_sql, t_resolvable, t_missing_target_is_the_answer, t_other_blockers,
           t_facet_reference, t_ambiguous_label_not_guessed, t_index_only_logical_entities,
           t_type_correspondence]:
    # A raising test is reported as a NAMED failure rather than aborting the run: a
    # gate that dies without saying what broke is half a gate, and it hides every
    # assertion after it.
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
