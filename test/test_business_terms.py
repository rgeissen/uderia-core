"""
A proposal in a person's words lands — nobody has to know a column (PROP-008 OBDA-MODEL).

Found Oct 2026 on production: the model-gap form asked an investigator for attribute names,
and the landing needed identifiers — "Assigned investigator" would have been skipped as "not a
plain identifier", and the reference to what the concept belongs to was created only if one
attribute was spelled exactly like that entity's physical key. The investigator knows the
business, not the warehouse.

  §A  the words become identifiers in the model's own naming style; the words stay as labels
  §B  the platform adds the concept's identifier and the reference to its parent — the
      reference named and typed like the parent's declared key
  §C  idempotent: the Draft it re-runs the same proposal and nothing changes or doubles
  §D  the planner never asks a warehouse to declare a reference on a VIEW (the column lands,
      the model keeps the relationship); a table parent still gets its foreign key
  §E  the suggestion: plain terms only — never an identifier or the reference the platform
      adds; an entity outside the model is dropped
  §F  the wiring: propose_entity runs it for every face; labels and the platform's additions
      survive into the draft and the listing; the suggestion call is booked to its turn

Run: python test/test_business_terms.py
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail is not None else ""))


def src(p):
    return open(os.path.join(ROOT, p)).read()


from trusted_data_agent.agent.kg_constructors import business_terms as B  # noqa: E402

SNAKE = ["incident_number", "offence_code", "ori", "object_id"]
PASCAL = ["IncidentNumber", "OffenceCode"]

print("[§A] words → the model's identifiers; the words stay as labels")
check("a snake_case model reads as snake", B.naming_style(SNAKE) == "snake")
check("a PascalCase model reads as pascal", B.naming_style(PASCAL) == "pascal")
check("a model that says nothing defaults to snake", B.naming_style([]) == "snake" and B.naming_style(["id", "x"]) == "snake")
check("'Assigned investigator' → assigned_investigator (snake)", B.identifier_for("Assigned investigator", "snake") == "assigned_investigator")
check("'Assigned investigator' → AssignedInvestigator (pascal)", B.identifier_for("Assigned investigator", "pascal") == "AssignedInvestigator")
check("an identifier in the model's style is kept", B.identifier_for("incident_number", "snake") == "incident_number")
check("an agent's LeadID in a snake model is lead_id", B.identifier_for("LeadID", "snake") == "lead_id")
check("punctuation and digits are handled; never an identifier starting with a digit",
      B.identifier_for("Follow-up action", "snake") == "follow_up_action" and B.identifier_for("2nd reviewer", "snake") == "_2nd_reviewer")
r = B.complete("Lead", [{"name": "Summary"}, {"name": "Assigned investigator"}, {"name": "Closure reason"}], model_names=SNAKE)
names = [a["name"] for a in r["attributes"]]
check("every attribute is an identifier the landing accepts", all(B.is_identifier(n) for n in names), names)
check("the person's words are the labels",
      [a["label"] for a in r["attributes"] if not a.get("added")] == ["Summary", "Assigned investigator", "Closure reason"], r)
check("the same words twice are one attribute", len(B.complete("X", [{"name": "Status"}, {"name": "status"}])["attributes"]) == 2)

print("[§B] the platform adds the identifier and the reference")
check("no identifier named ⇒ the concept's own identifier is added FIRST and is the key",
      names[0] == "lead_id" and r["key"] == ["lead_id"] and r["attributes"][0]["added"] == "key"
      and r["attributes"][0]["label"] == "Lead identifier", r)
check("…said in words for the proposal row", {"what": "key", "label": "Lead identifier"} in r["added"])
r2 = B.complete("Lead", [{"name": "LeadID"}, {"name": "Summary"}], model_names=SNAKE)
check("an attribute that IS the concept's identifier (LeadID) is the key — none added",
      r2["key"] == ["lead_id"] and not any(a.get("added") == "key" for a in r2["attributes"]))
r3 = B.complete("Lead", [{"name": "Summary"}], key=["Summary"], model_names=SNAKE)
check("a key the person named stands", r3["key"] == ["summary"] and not any(a.get("added") for a in r3["attributes"]))
r4 = B.complete("Lead", [{"name": "Summary"}], parent_name="Policing Core.CaseObject", parent_key=["object_id"],
                parent_key_types={"object_id": "VARCHAR(255)"}, model_names=SNAKE)
ref = [a for a in r4["attributes"] if a.get("added") == "reference"]
check("belonging to an entity ⇒ the reference is added, named EXACTLY like its declared key",
      len(ref) == 1 and ref[0]["name"] == "object_id", r4)
check("…typed like the parent's key (a foreign key must match it)", ref and ref[0]["data_type"] == "VARCHAR(255)")
check("…and labelled in words", ref and ref[0]["label"] == "The case object it belongs to")
r5 = B.complete("Lead", [{"name": "object_id"}], parent_name="X.CaseObject", parent_key=["object_id"], model_names=SNAKE)
check("a reference already named is not added twice", sum(a["name"] == "object_id" for a in r5["attributes"]) == 1)
r6 = B.complete("Warrant", [{"name": "issued at"}], parent_name="J.Person", parent_key=["PersonId"], model_names=PASCAL)
check("a PascalCase model: WarrantId, IssuedAt, PersonId",
      [a["name"] for a in r6["attributes"]] == ["WarrantId", "IssuedAt", "PersonId"], r6)
check("no parent key known ⇒ no reference invented", not any(a.get("added") == "reference"
      for a in B.complete("Lead", [{"name": "x"}], parent_name="X.Y", parent_key=[])["attributes"]))
check("odd input never raises", B.complete("", [None, {}, {"name": "  "}, {"name": "%%"}])["attributes"] is not None)

print("[§C] idempotent — the Draft it re-runs the same proposal")
again = B.complete("Lead", [{k: a[k] for k in ("name", "data_type", "label", "added") if a.get(k)} for a in r4["attributes"]],
                   key=r4["key"], parent_name="Policing Core.CaseObject", parent_key=["object_id"],
                   parent_key_types={"object_id": "VARCHAR(255)"}, model_names=SNAKE)
check("same names, same key", [a["name"] for a in again["attributes"]] == [a["name"] for a in r4["attributes"]]
      and again["key"] == r4["key"], again)
check("labels kept (not re-derived from the identifier)", [a["label"] for a in again["attributes"]] == [a["label"] for a in r4["attributes"]])
check("the platform's additions keep their marker", [a.get("added") for a in again["attributes"]] == [a.get("added") for a in r4["attributes"]])

print("[§D] the planner and a parent that is a view")
from trusted_data_agent.dbdeploy import delta as D  # noqa: E402
LEAD = {"name": "M.Lead", "props": {"business_key": "lead_id"},
        "attributes": [{"name": "M.Lead.lead_id", "props": {"data_type": "VARCHAR(255)"}},
                       {"name": "M.Lead.object_id", "props": {"data_type": "VARCHAR(255)"}}],
        "parent": "M.CaseObject", "deployed": None}
FACTS = {"M.CaseObject": {"deployed": "Core.v_CaseObject", "key": ["object_id"]}}
SIB = D.landing_for("Core", False)
view_dict = {"databases": {"Core"}, "tables": {"Core.v_CaseObject": {"columns": {"object_id": "CV"}, "foreign_keys": [], "kind": "view"}}}
p = D.plan_delta({"entities": [LEAD], "attributes": []}, FACTS, view_dict, SIB)
check("a view parent ⇒ no foreign key statement", not [c for c in p.pending() if c.kind == "foreign_key"])
check("…the column still lands", any("object_id" in (c.sql or "") for c in p.pending() if c.kind == "table"))
check("…and the reason is said", any("realised as a view" in w and "recorded in the model" in w for w in p.warnings), p.warnings)
# Act II on production: closure_reason added to the LANDED Lead — the preview repeated the Lead's
# view-parent note as if it were this change's finding; the note belongs to the table's creation
LANDED = {**LEAD, "deployed": "Core.Lead", "attributes": LEAD["attributes"] + [{"name": "M.Lead.closure_reason", "props": {"data_type": "VARCHAR(255)"}}]}
landed_dict = {"databases": {"Core"}, "tables": {**view_dict["tables"], "Core.Lead": {"columns": {"lead_id": "CV", "object_id": "CV"}, "foreign_keys": [], "kind": "table"}}}
pl = D.plan_delta({"entities": [LANDED], "attributes": []}, FACTS, landed_dict, SIB)
NOKEY = {"M.CaseObject": {"deployed": "Core.v_CaseObject", "key": []}}
pk = D.plan_delta({"entities": [LANDED], "attributes": []}, NOKEY, landed_dict, SIB)
check("…nor any other parent-reference note (no key · not deployed · no attribute names the key) — they belong to the table's creation",
      not any("no soft-RI" in w or "no foreign key" in w for w in pk.warnings), pk.warnings)
check("a column added to a table that EXISTS: one ADD COLUMN, and the view-parent note is not repeated",
      [c.identifier for c in pl.pending()] == ["Core.Lead.closure_reason"] and not any("realised as a view" in w for w in pl.warnings), (pl.warnings, [c.identifier for c in pl.pending()]))
tbl_dict = {"databases": {"Core"}, "tables": {"Core.v_CaseObject": {"columns": {"object_id": "CV"}, "foreign_keys": [], "kind": "table"}}}
p2 = D.plan_delta({"entities": [LEAD], "attributes": []}, FACTS, tbl_dict, SIB)
check("a table parent ⇒ the foreign key is declared", any(c.kind == "foreign_key" for c in p2.pending()))
p3 = D.plan_delta({"entities": [LEAD], "attributes": []}, FACTS, {"databases": {"Core"}, "tables": {}}, SIB)
check("an unknown kind keeps the earlier behaviour (no silent change for older dictionaries)",
      any(c.kind == "foreign_key" for c in p3.pending()))
check("both backends report what each object IS",
      "TableKind FROM DBC.TablesV" in src("src/trusted_data_agent/dbdeploy/teradata_backend.py")
      and "table_type FROM information_schema.tables" in src("src/trusted_data_agent/dbdeploy/postgresql_backend.py"))

print("[§E] the suggestion — plain terms only")
from trusted_data_agent.agent import proposal_suggest as PS  # noqa: E402
ents = [{"name": "Policing Core.CaseObject", "leaf": "CaseObject", "description": "an evidence object"},
        {"name": "Policing Core.Incident", "leaf": "Incident", "description": ""}]
v = PS.validate({"records": ["Summary", "Lead ID", "lead_identifier", "Case object", "Status", "Summary", "x" * 200, ""],
                 "belongs_to": "CaseObject"}, "Lead", ents)
check("the parent is matched to the model's entity by its name", v["belongs_to"] == "Policing Core.CaseObject")
check("the identifier and the reference are dropped — the platform adds them",
      "Lead ID" not in v["records"] and "lead_identifier" not in v["records"] and "Case object" not in v["records"], v)
check("duplicates and empties dropped; long terms bounded", v["records"][:2] == ["Summary", "Status"]
      and all(len(t) <= PS.MAX_WORDS_LEN for t in v["records"]) and "" not in v["records"], v)
check("an entity outside the model is dropped", PS.validate({"records": [], "belongs_to": "Nowhere"}, "Lead", ents)["belongs_to"] is None)
check("an unreadable answer is an empty suggestion", PS.validate("not json", "Lead", ents) == {"records": [], "belongs_to": None})
check("at most MAX_RECORDS terms", len(PS.validate({"records": [f"T{i} word" for i in range(30)]}, "Lead", ents)["records"]) == PS.MAX_RECORDS)
pr = PS.build_prompt("Lead", "a line of enquiry", "which leads are open?", ents)
check("the prompt asks for business terms, not identifiers, and lists only the model's entities",
      "business terms" in pr and "Do NOT include an identifier" in pr and "- CaseObject: an evidence object" in pr)

print("[§F] the wiring")
h = src("components/builtin/knowledge_graph/handler.py")
check("propose_entity completes every proposal in code (every face: agent, form, REST)",
      "_done = _bt.complete(name, attrs," in h and "parent_key_types=_pkey_types" in h and 'key = list(_done["key"])' in h)
check("the parent key's TYPE is read from the graph (deployed, else declared)",
      'store.get_entity_by_name(f"{parent[\'name\']}.{_pk}")' in h and '_pp.get("deployed_type") or _pp.get("declared_type")' in h)
check("labels and the platform's additions survive into the Draft it and onto the drafted attribute",
      '("name", "data_type", "label", "added")' in h and '"label": a["label"]' in h and '"added_by_platform": a["added"]' in h)
check("the listing returns them (Govern / Workspace can say the person's words)",
      '"label": pr.get("label"), "data_type": pr.get("data_type"),' in h and '"added_by_platform": pr.get("added_by_platform")' in h)
check("the proposal row says what it records in words, and what the platform adds",
      "It records {words(own)}" in h and '"The platform adds "' in h)
ps = src("src/trusted_data_agent/agent/proposal_suggest.py")
check("the suggestion is booked to its turn through the ONE post-turn booking (test_post_turn_usage)",
      "book_post_turn_call(" in ps and 'event_type="proposal_suggestion_complete"' in ps
      and "update_turn_token_counts(" not in ps and "ConsumptionManager" not in ps)
rr = src("src/trusted_data_agent/api/rest_routes.py")
check("the suggestion route answers 403 while the OBDA-MODEL plane is off for the caller (the component's availability)", '@rest_api_bp.route("/v1/knowledge-graph/propose/suggest"' in rr
      and 0 < rr.find("_assert_model_enabled()", rr.find("def kg_propose_suggest")) < rr.find("def kg_propose_suggest") + 1500)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
