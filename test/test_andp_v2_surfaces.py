"""
CI-pure tests for the v2 consumer surfaces the constructor reads.

No database, no network: a fake cursor replays the *real* shapes observed on the
reference deployment — Fitness360 publishes a `column_catalogue`
view over the dictionary with `column_metadata` EMPTY, while NIBRS_Source/
CASE_Source (built to the earlier standard) publish only `column_metadata`.

The defect this gate exists to prevent: reading only `column_metadata` returns
names and types and NO meaning for a conformant v2 product — silently, which is
worse than an error, because a plausible-looking empty reads as "thin product".

Run: python3 test/test_andp_v2_surfaces.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.kg_constructors.andp_semantic_source import (  # noqa: E402
    fetch_access_objects,
    fetch_column_catalogue,
    fetch_snapshot,
    fetch_trust_gate,
)
from trusted_data_agent.agent.kg_constructors.andp_semantic_walker import (  # noqa: E402
    SemanticSnapshot,
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


# ── the fake cursor ─────────────────────────────────────────────────────────────

CATALOGUE_COLS = ["catalogue_database", "catalogue_table", "column_name",
                  "ordinal_position", "data_type", "data_type_source", "is_nullable",
                  "is_required", "business_description", "description_source",
                  "is_documented", "is_pii", "is_sensitive", "data_classification",
                  "allowed_values_json"]

METADATA_COLS = ["database_name", "table_name", "column_name", "business_description",
                 "is_pii", "is_sensitive", "data_classification", "is_required",
                 "data_type", "allowed_values_json", "is_active"]


def cat_row(table, col, documented=1, pii=0, desc="a documented column",
            values=None, classification=None):
    return [("F360_Domain", table, col, 1, "VARCHAR(20)", "dictionary", "Y", 1,
             desc if documented else None,
             "comment" if documented else "none",
             documented, pii, 0, classification, values)]


class FakeCursor:
    """Replays a product's shape.  `tables` maps DatabaseName -> {TableName: rows}."""

    def __init__(self, tables):
        self.tables = tables
        self.description = []
        self._rows = []
        self.seen = []

    def execute(self, sql, params=None):
        self.seen.append(sql)
        s = " ".join(sql.split())
        # table existence probe
        if "DBC.TablesV" in s:
            m = re.search(r"DatabaseName\s*=\s*'([^']*)'", s)
            db = m.group(1) if m else ""
            names = list(self.tables.get(db, {}).keys())
            self.description = [("TableName",)]
            self._rows = [(n,) for n in names]
            return
        if s.upper().startswith("SELECT * FROM"):
            ref = s.split()[3].rstrip(";")
            db, tbl = ref.split(".", 1)
            rows = self.tables.get(db, {}).get(tbl)
            if rows is None:
                raise RuntimeError(f"no such table {ref}")
            cols, data = rows
            self.description = [(c,) for c in cols]
            self._rows = list(data)
            return
        if "DBC.ColumnsV" in s:
            self.description = [("DatabaseName",), ("TableName",), ("ColumnName",),
                                ("ColumnType",), ("ColumnLength",),
                                ("DecimalTotalDigits",), ("DecimalFractionalDigits",),
                                ("Nullable",)]
            self._rows = [("F360_Domain", "ServiceTicket_H", "ticket_id", "CV", 20,
                           None, None, "Y")]
            return
        self.description = []
        self._rows = []

    def fetchall(self):
        return self._rows


def v2_tables(extra_metadata=None):
    return {
        "F360_Semantic": {
            "data_product_map": (["module_name", "database_name"],
                                 [("Domain", "F360_Domain")]),
            "entity_metadata": (["entity_name", "database_name", "table_name"],
                                [("Service Ticket", "F360_Domain", "ServiceTicket_H")]),
            "column_metadata": (METADATA_COLS, extra_metadata or []),
            "table_relationship": (["from_table", "to_table"], []),
            "column_catalogue": (CATALOGUE_COLS,
                                 cat_row("ServiceTicket_H", "ticket_id")
                                 + cat_row("ServiceTicket_H", "scratch_col", documented=0)),
        }
    }


# ── the tests ───────────────────────────────────────────────────────────────────

def t_catalogue_is_read_when_present():
    cur = FakeCursor(v2_tables())
    rows = fetch_column_catalogue(cur, "F360_Semantic")
    check("the v2 catalogue is read at all", len(rows) == 1, f"got {len(rows)}")
    r = rows[0]
    check("catalogue_database is mapped to database_name",
          r.get("database_name") == "F360_Domain", r)
    check("catalogue_table is mapped to table_name",
          r.get("table_name") == "ServiceTicket_H", r)
    check("the description survives the mapping",
          r.get("business_description") == "a documented column", r)


def t_undocumented_ungoverned_column_is_not_a_logical_attribute():
    cur = FakeCursor(v2_tables())
    rows = fetch_column_catalogue(cur, "F360_Semantic")
    names = [r["column_name"] for r in rows]
    check("an undocumented, ungoverned column is excluded",
          "scratch_col" not in names, names)


def t_governance_alone_keeps_an_undocumented_column():
    """PII on an undocumented column must never be dropped — fail safe."""
    tables = v2_tables()
    tables["F360_Semantic"]["column_catalogue"] = (
        CATALOGUE_COLS, cat_row("ServiceTicket_H", "ssn", documented=0, pii=1))
    rows = fetch_column_catalogue(FakeCursor(tables), "F360_Semantic")
    check("an undocumented but PII-flagged column is kept",
          [r["column_name"] for r in rows] == ["ssn"], rows)


def t_absent_catalogue_returns_empty_not_error():
    tables = v2_tables()
    del tables["F360_Semantic"]["column_catalogue"]
    rows = fetch_column_catalogue(FakeCursor(tables), "F360_Semantic")
    check("a v1 product (no catalogue) yields [] rather than raising", rows == [])


def t_snapshot_prefers_the_catalogue_and_says_so():
    snap = fetch_snapshot(FakeCursor(v2_tables()), "F360",
                          semantic_database="F360_Semantic",
                          include_product_knowledge=False)
    check("the snapshot carries the catalogue's column",
          [r["column_name"] for r in snap.column_metadata] == ["ticket_id"],
          snap.column_metadata)
    check("the column source is reported honestly",
          "column_catalogue" in snap.source_detail, snap.source_detail)


def t_curated_row_the_catalogue_missed_is_kept():
    """The catalogue is scoped to registered databases; a curated row outside it
    must not vanish — losing a governance fact is the one unacceptable direction."""
    curated = [("Other_Domain", "Legacy", "old_col", "still described", 1, 0,
                "CONFIDENTIAL", 1, "INTEGER", None, 1)]
    snap = fetch_snapshot(FakeCursor(v2_tables(extra_metadata=curated)), "F360",
                          semantic_database="F360_Semantic",
                          include_product_knowledge=False)
    names = sorted(r["column_name"] for r in snap.column_metadata)
    check("a curated row the catalogue does not cover survives",
          names == ["old_col", "ticket_id"], names)
    check("the mixed source is reported", "curated" in snap.source_detail,
          snap.source_detail)


def t_v1_product_is_untouched():
    """No catalogue: the snapshot must be exactly what it was before this path."""
    tables = v2_tables(extra_metadata=[("F360_Domain", "ServiceTicket_H", "ticket_id",
                                        "curated meaning", 1, 1, "CONFIDENTIAL", 1,
                                        "INTEGER", None, 1)])
    del tables["F360_Semantic"]["column_catalogue"]
    snap = fetch_snapshot(FakeCursor(tables), "F360",
                          semantic_database="F360_Semantic",
                          include_product_knowledge=False)
    check("a v1 product still reads its curated rows",
          [r["business_description"] for r in snap.column_metadata] == ["curated meaning"],
          snap.column_metadata)
    check("a v1 product reports column_metadata as its source",
          "columns: column_metadata" in snap.source_detail, snap.source_detail)



# ── v2 consumer surfaces: access objects and the trust gate ─────────────────────

ACCESS_COLS = ["access_object_id", "database_name", "object_name", "access_role",
               "represents_entity", "object_grain", "is_agent_consumable",
               "resolves_to_object", "access_note", "is_active"]

GATE_COLS = ["product_prefix", "producer_id", "run_id", "trust_status",
             "agent_use_allowed", "completed_dts", "evidence_expires_dts"]


def with_v2_surfaces(access_rows=None, gate_rows=None):
    t = v2_tables()
    t["F360_Semantic"]["data_product_map"] = (
        ["module_name", "database_name"],
        [("Domain", "F360_Domain"), ("Observability", "F360_Observability")])
    t["F360_Semantic"]["access_object"] = (ACCESS_COLS, access_rows if access_rows is not None else [
        (1, "F360_Domain", "ServiceTicket_H", "BASE", "Service Ticket", "one row per ticket",
         1, None, "carries PII-classified columns", 1),
        (2, "F360_Domain", "ServiceTicket_H_NoPII", "RESTRICTED_PROJECTION", "Service Ticket",
         "one row per ticket", 1, "F360_Domain.ServiceTicket_H", "PII-free projection", 1),
    ])
    t["F360_Observability"] = {"validation_latest": (GATE_COLS, gate_rows if gate_rows is not None else [
        ("F360", "uderia_v2_uplift", "RUN-1", "DEGRADED", 1,
         "2026-08-18 10:00:00", "2026-11-16 10:00:00")])}
    return t


def t_access_objects_are_read():
    rows = fetch_access_objects(FakeCursor(with_v2_surfaces()), "F360_Semantic")
    check("both access objects are read", len(rows) == 2, rows)


def t_inactive_access_object_is_excluded():
    rows = fetch_access_objects(FakeCursor(with_v2_surfaces(access_rows=[
        (1, "F360_Domain", "Old_View", "PASSTHROUGH", "Service Ticket", "g", 1, None, "", 0)])),
        "F360_Semantic")
    check("a deactivated access object is not offered", rows == [], rows)


def t_trust_gate_matches_the_product():
    gate = fetch_trust_gate(FakeCursor(with_v2_surfaces()), "F360_Observability", "F360")
    check("the gate row is found", bool(gate), gate)
    check("its verdict survives", gate.get("trust_status") == "DEGRADED", gate)


def t_absent_gate_is_none_not_trusted():
    t = with_v2_surfaces()
    del t["F360_Observability"]["validation_latest"]
    gate = fetch_trust_gate(FakeCursor(t), "F360_Observability", "F360")
    check("a product with no gate yields None, never a pass", gate is None, gate)


def t_walker_puts_the_gate_on_the_product_and_the_object_on_the_entity():
    snap = fetch_snapshot(FakeCursor(with_v2_surfaces()), "F360",
                          semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    root = next((e for e in ents if e["entity_type"] == "database"), None)
    check("the product root carries the verdict",
          (root or {}).get("properties", {}).get("trust_status") == "DEGRADED", root)
    check("the product root carries the use flag",
          (root or {}).get("properties", {}).get("agent_use_allowed") is True, root)
    ent = next((e for e in ents if e["entity_type"] == "entity"), None)
    props = (ent or {}).get("properties", {})
    check("the entity is pointed at the COMPLETE object, not the PII-free projection",
          props.get("consumable_object") == "F360_Domain.ServiceTicket_H", props)
    check("the projection is still recorded as an alternative",
          "F360_Domain.ServiceTicket_H_NoPII"
          in (props.get("access_alternatives") or []), props)


def t_restricted_projection_never_wins_over_a_complete_object():
    """A projection is an alternative, never the default.

    This gate previously asserted the OPPOSITE — that the projection wins — and
    passed while the shipped product answered "how many victims are female?" with
    a number computed from 10 sample rows.  The base object had been declared
    non-consumable, the projection was preferred, the PII-classified column it
    removes did not exist there, and the failure landed as a plausible total.
    Preferring an object that drops documented columns fails toward a wrong
    answer; preferring the complete one fails toward a database denial.
    """
    # The projection is listed FIRST and carries no column evidence, so the
    # declared role is the only thing that can demote it.  Listing it second let
    # insertion order pick the base object and the rule went untested — a mutation
    # that deleted the role check passed.
    both = [
        (2, "F360_Domain", "ServiceTicket_H_NoPII", "RESTRICTED_PROJECTION",
         "Service Ticket", "one row per ticket", 1,
         "F360_Domain.ServiceTicket_H", "PII-free", 1),
        (1, "F360_Domain", "ServiceTicket_H", "BASE", "Service Ticket",
         "one row per ticket", 1, None, "base object", 1),
    ]
    snap = fetch_snapshot(FakeCursor(with_v2_surfaces(access_rows=both)), "F360",
                          semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    ent = next(e for e in ents if e["entity_type"] == "entity")
    check("the complete object is preferred over the projection",
          ent["properties"].get("consumable_object")
          == "F360_Domain.ServiceTicket_H", ent["properties"])


def t_a_genuine_passthrough_is_still_preferred():
    """The original rule survives where it is right.

    A 1:1 passthrough exposes the same columns, so it answers the same questions;
    the standard defines `resolves_to_object` for exactly that case, and a product
    that publishes one means it as the sanctioned surface.
    """
    both = [
        (1, "F360_Domain", "ServiceTicket_H", "BASE", "Service Ticket",
         "one row per ticket", 1, None, "base object", 1),
        (2, "F360_Domain", "ServiceTicket_V", "PASSTHROUGH", "Service Ticket",
         "one row per ticket", 1, "F360_Domain.ServiceTicket_H", "1:1 view", 1),
    ]
    snap = fetch_snapshot(FakeCursor(with_v2_surfaces(access_rows=both)), "F360",
                          semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    ent = next(e for e in ents if e["entity_type"] == "entity")
    check("a column-equivalent passthrough is still the default",
          ent["properties"].get("consumable_object")
          == "F360_Domain.ServiceTicket_V", ent["properties"])


def t_a_projection_mislabelled_as_passthrough_is_detected_by_its_columns():
    """Defence in depth: the label is not trusted when the columns contradict it.

    The shipped catalogue registered a column-dropping view AS a PASSTHROUGH.  A
    foreign product may do the same, so the walker also checks the evidence: an
    object whose documented columns are a STRICT SUBSET of the object it resolves
    to is a projection, whatever it calls itself.
    """
    t = with_v2_surfaces(access_rows=[
        (1, "F360_Domain", "ServiceTicket_H", "BASE", "Service Ticket",
         "one row per ticket", 1, None, "base object", 1),
        (2, "F360_Domain", "ServiceTicket_H_NoPII", "PASSTHROUGH", "Service Ticket",
         "one row per ticket", 1, "F360_Domain.ServiceTicket_H", "PII-free", 1),
    ])
    # base: ticket_id + customer_name.  the "passthrough": ticket_id only.
    t["F360_Semantic"]["column_catalogue"] = (
        CATALOGUE_COLS,
        cat_row("ServiceTicket_H", "ticket_id")
        + cat_row("ServiceTicket_H", "customer_name", pii=1)
        + cat_row("ServiceTicket_H_NoPII", "ticket_id"))
    snap = fetch_snapshot(FakeCursor(t), "F360", semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    ent = next(e for e in ents if e["entity_type"] == "entity")
    check("a subset-of-columns 'passthrough' is treated as a projection",
          ent["properties"].get("consumable_object")
          == "F360_Domain.ServiceTicket_H", ent["properties"])


def t_a_lone_projection_is_still_used():
    """When it is the ONLY registered object, it is what the consumer has."""
    only = [(2, "F360_Domain", "ServiceTicket_H_NoPII", "RESTRICTED_PROJECTION",
             "Service Ticket", "one row per ticket", 1,
             "F360_Domain.ServiceTicket_H", "PII-free", 1)]
    snap = fetch_snapshot(FakeCursor(with_v2_surfaces(access_rows=only)), "F360",
                          semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    ent = next(e for e in ents if e["entity_type"] == "entity")
    check("a lone projection is still offered rather than nothing",
          ent["properties"].get("consumable_object")
          == "F360_Domain.ServiceTicket_H_NoPII", ent["properties"])


def t_base_is_used_when_it_is_the_only_consumable_object():
    only_base = [(1, "F360_Domain", "ServiceTicket_H", "BASE", "Service Ticket",
                  "one row per ticket", 1, None, "no PII declared", 1)]
    snap = fetch_snapshot(FakeCursor(with_v2_surfaces(access_rows=only_base)), "F360",
                          semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    ent = next(e for e in ents if e["entity_type"] == "entity")
    check("a lone base object is still pointed at",
          ent["properties"].get("consumable_object") == "F360_Domain.ServiceTicket_H",
          ent["properties"])


def t_no_gate_means_no_claim_on_the_node():
    t = with_v2_surfaces()
    del t["F360_Observability"]["validation_latest"]
    snap = fetch_snapshot(FakeCursor(t), "F360", semantic_database="F360_Semantic")
    ents, _ = walk_andp_semantic(snap)
    root = next(e for e in ents if e["entity_type"] == "database")
    check("an unvalidated product asserts no trust status",
          root["properties"].get("trust_status") is None, root["properties"])


def t_v1_product_emits_neither_surface():
    """The whole point of tolerating both generations."""
    t = v2_tables()
    del t["F360_Semantic"]["column_catalogue"]
    snap = fetch_snapshot(FakeCursor(t), "F360", semantic_database="F360_Semantic")
    check("a v1 product has no access objects", snap.access_objects == [])
    check("a v1 product has no trust gate", snap.trust_gate is None)


for fn in [t_catalogue_is_read_when_present,
           t_undocumented_ungoverned_column_is_not_a_logical_attribute,
           t_governance_alone_keeps_an_undocumented_column,
           t_absent_catalogue_returns_empty_not_error,
           t_snapshot_prefers_the_catalogue_and_says_so,
           t_curated_row_the_catalogue_missed_is_kept,
           t_v1_product_is_untouched,
           t_access_objects_are_read, t_inactive_access_object_is_excluded,
           t_trust_gate_matches_the_product, t_absent_gate_is_none_not_trusted,
           t_walker_puts_the_gate_on_the_product_and_the_object_on_the_entity,
           t_no_gate_means_no_claim_on_the_node, t_v1_product_emits_neither_surface,
           t_restricted_projection_never_wins_over_a_complete_object,
           t_a_genuine_passthrough_is_still_preferred,
           t_a_projection_mislabelled_as_passthrough_is_detected_by_its_columns,
           t_a_lone_projection_is_still_used,
           t_base_is_used_when_it_is_the_only_consumable_object]:
    try:
        fn()
    except Exception as exc:  # a gate that dies without naming what broke is half a gate
        FAIL += 1
        FAILURES.append(f"{fn.__name__} RAISED: {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
