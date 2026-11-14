"""Repeating groups, description references, range codes, module/product stamps — the
ANDP walker reads them from what ANY product declares (2026-09-04).

Class, not instance: nothing here names a real product's columns. The fixture is a
synthetic codebook with the SHAPES every denormalised source shares — one fact stored
once per slot, a description that names the column it qualifies, a value declared as a
range. A title without positions forms no family; a product declaring none of this is
unchanged apart from the two stamps every node now carries.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.agent.kg_constructors.andp_semantic_walker import (  # noqa: E402
    SemanticSnapshot, WalkOptions, WalkStats, walk_andp_semantic, parse_title_positions, parse_value_codes)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

def col(table, name, desc, values=None):
    return {"database_name": "Prod_Domain", "table_name": table, "column_name": name,
            "business_description": desc, "is_pii": 0, "is_sensitive": 0, "data_classification": "PUBLIC",
            "data_type": "VARCHAR(2)", "allowed_values_json": json.dumps(values) if values else None, "is_active": 1}

def snapshot():
    cols = []
    # a two-axis repeating group: position × slot
    for pos, code in ((1, "08"), (2, "09"), (3, "10")):
        for slot in (1, 2, 3):
            cols.append(col("Rec", f"V20{code}{slot}", f"DEVICE USED {pos} - {slot} - V20{code}{slot} is device {pos} for the item in V2006{slot}.",
                            [{"value": "2", "label": "Computer"}, {"value": "-5 to -9", "label": "Missing/unknown"}]))
    # a one-axis group
    for slot in (1, 2, 3):
        cols.append(col("Rec", f"V2011{slot}", f"PLACE TYPE - {slot} - V2011{slot} is the place of the item in V2006{slot}."))
        cols.append(col("Rec", f"V2006{slot}", f"ITEM CODE - {slot} - V2006{slot} is the code of item {slot}."))
    # a title without positions: no family
    cols.append(col("Rec", "V1006", "TOTAL COUNT - V1006 is the number of items."))
    cols.append(col("Rec", "STATE", "STATE CODE - the reporting state."))
    return SemanticSnapshot(product="Prod", semantic_database="Prod_Semantic",
        data_product_map=[{"module_name": "Domain", "database_name": "Prod_Domain", "primary_tables": "Rec", "is_active": 1}],
        entity_metadata=[{"entity_name": "Rec", "entity_description": "One row per record", "module_name": "Domain",
                          "database_name": "Prod_Domain", "table_name": "Rec", "natural_key_column": "ID", "is_active": 1}],
        column_metadata=cols, table_relationship=[], business_glossary=[], query_cookbook=[], design_decisions=[],
        module_registry=[], naming_standards=[], data_lineage=[], quality_metrics=[])

def t_title_positions():
    check("two-axis title", parse_title_positions("DEVICE USED 1 - 2 - V20082 is device 1 for item 2.", "V20082") == ("DEVICE USED", [1, 2]),
          parse_title_positions("DEVICE USED 1 - 2 - V20082 is device 1 for item 2.", "V20082"))
    check("one-axis title, hyphenated", parse_title_positions("AGE OF PERSON-1 - V40181 is the numeric age.", "V40181") == ("AGE OF PERSON", [1]),
          parse_title_positions("AGE OF PERSON-1 - V40181 is the numeric age.", "V40181"))
    check("no positions", parse_title_positions("TOTAL COUNT - V1006 is the number of items.", "V1006") == ("TOTAL COUNT", []), None)
    check("a bare number is not a title", parse_title_positions("1 - V1 x", "V1")[1] == [] or parse_title_positions("1 - V1 x", "V1")[0] != "", None)
    check("empty description", parse_title_positions(None, "X") == ("", []), None)

def t_range_codes():
    out = parse_value_codes(json.dumps([{"value": "2", "label": "Computer"}, {"value": "-5 to -9", "label": "Missing"}]))
    vals = [c["value"] for c in out]
    check("a range expands to its members", vals == ["2", "-5", "-6", "-7", "-8", "-9"], vals)
    check("each member keeps the label and the declared range", all(c.get("range") == "-5 to -9" and c["label"] == "Missing" for c in out[1:]), out[1:2])
    check("a plain value is untouched", parse_value_codes(json.dumps([{"value": "A", "label": "x"}])) == [{"value": "A", "label": "x"}], None)
    check("an absurd range is not expanded", [c["value"] for c in parse_value_codes(json.dumps([{"value": "1 to 5000", "label": "n"}]))] == ["1 to 5000"], None)

def t_walker_families_and_references():
    stats = WalkStats(); ents, rels = walk_andp_semantic(snapshot(), WalkOptions(include_columns=True, layers=["logical"]), stats=stats)
    by = {e["name"]: e for e in ents}
    a = by["Prod.Rec.V20082"]["properties"]
    check("a member carries its family stem", a.get("family") == "device used", a.get("family"))
    check("a member knows its size and axes", a.get("family_size") == 9 and a.get("family_axes") == 2, (a.get("family_size"), a.get("family_axes")))
    check("a member lists its siblings in position order", a.get("family_members")[:3] == ["V20081", "V20082", "V20083"] and len(a["family_members"]) == 9, a.get("family_members"))
    check("positions are recorded", a.get("positions") == [1, 2], a.get("positions"))
    p = by["Prod.Rec.V20112"]["properties"]
    check("a one-axis family", p.get("family") == "place type" and p.get("family_size") == 3 and p.get("family_axes") == 1, (p.get("family"), p.get("family_size"), p.get("family_axes")))
    check("a title without positions forms no family", "family" not in by["Prod.Rec.V1006"]["properties"] and "family" not in by["Prod.Rec.STATE"]["properties"], None)
    check("families are counted", stats.families == 3, stats.families)  # device used, place type, item code
    check("a description that names a sibling records the reference", a.get("refers_to") == ["V20062"], a.get("refers_to"))
    check("a reference is a property, never an edge the extractor would walk", not any(r["relationship_type"] == "depends_on" for r in rels), None)
    check("a column never references itself", "V1006" not in (by["Prod.Rec.V1006"]["properties"].get("refers_to") or []), None)
    check("every attribute is stamped with module and product", all(e["properties"].get("module") == "Domain" and e["properties"].get("product") == "Prod" for e in ents if e["entity_type"] == "attribute"), None)
    check("range codes reach the node", any(c.get("value") == "-7" for c in a.get("allowed_values") or []), a.get("allowed_values"))

def t_additive_only():
    """A product declaring none of these shapes gets only the two stamps — nothing else moves."""
    snap = snapshot(); snap.column_metadata = [col("Rec", "STATE", "STATE CODE - the reporting state."), col("Rec", "ID", "Record id")]
    stats = WalkStats(); ents, rels = walk_andp_semantic(snap, WalkOptions(include_columns=True, layers=["logical"]), stats=stats)
    attrs = [e for e in ents if e["entity_type"] == "attribute"]
    check("no family, no reference, no range on a plain product", not any(k in e["properties"] for e in attrs for k in ("family", "refers_to", "positions")) and stats.families == 0 and stats.references == 0, [e["properties"] for e in attrs])
    check("no depends_on edge invented", not any(r["relationship_type"] == "depends_on" for r in rels), None)

for fn in (t_title_positions, t_range_codes, t_walker_families_and_references, t_additive_only):
    try:
        fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
