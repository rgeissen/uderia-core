"""PROP-008 increment A2 — the normalisation advisor and the two propose actions.

Real SQLite rows for the probes (the advisor's SQL is dialect-neutral and runs on sqlite
in CI), the real GraphStore over a temp KG DB, the real handler methods; the only double
is the probe runner, faithful in the dimension under test (it returns list-of-dicts rows
from a real sqlite fixture). CI-pure.

  §1  the six rules on real rows: FD (must) · multiplicity · hidden identity (with the
      candidate-key floor) · shared reference · lifecycle · closed enumeration · attribute
  §2  precedence: a declaration wins over the inferred reading and the contradiction is a
      finding; a MUST is never reinstated by a declaration
  §3  no rows ⇒ provisional with the reason; every probe failed ⇒ unknown
  §4  the model's residue: it may choose where the rules left it open, never overrule a must
  §5  propose_attribute / propose_entity through the real handler + real store:
      master OFF ⇒ refusal, nothing written · verdict without a draft · a draft lands on the
      AUTHORED layer with its design_decision · an entity verdict refuses an attribute draft
      · propose_entity drafts entity + attributes + key + the belongs-to link · no duplicate
  §6  an autonomous principal without the modelAuthoring grant is refused; with it, admitted
  §7  the new actions are write actions (the released-mode refusal covers them)
  §8  context cost: with the master OFF no word of propose_* reaches a tool description or
      an instruction level; ON they do; the 'act' gate key resolves to the switch
  §9  A1 integration: a resync (replace_model_layer) keeps the drafts

Run: PYTHONPATH=src python3 test/test_kg_normalisation.py
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def run(fn):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")
def _run(x): return asyncio.run(x) if asyncio.iscoroutine(x) else x  # GraphStore mixes sync + async

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.kgstore import factory as _factory
from trusted_data_agent.agent.kg_constructors import normalisation as N
from components.builtin.knowledge_graph.graph_store import GraphStore
from trusted_data_agent.auth.database import _migrate_to_multi_kg

# ── the data fixture: 40 customers × 2 rows (two emails each) ──────────────────
DATA = sqlite3.connect(":memory:")
DATA.executescript("create table Customers(cid int, email text, phone text, city text, zip text, "
                   "status_code text, status_label text, note text);")
_rows = []
for i in range(1, 41):
    city = ["Boston", "Austin", "Denver", "Miami"][i % 4]
    z = {"Boston": "02101", "Austin": "73301", "Denver": "80201", "Miami": "33101"}[city]
    st = "A" if i % 2 else "B"
    for j in range(2):
        _rows.append((i, f"u{i}{'b' if j else ''}@x", str(i), city, z, st, "Active" if st == "A" else "Blocked", f"n{i}"))
DATA.executemany("insert into Customers values(?,?,?,?,?,?,?,?)", _rows)
def run_sql(sql):
    cur = DATA.execute(sql); cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
async def arun_sql(sql): return run_sql(sql)
COLS = ["email", "phone", "city", "zip", "status_code", "status_label", "note"]
ENT = {"name": "fitness_db.Customers", "key": ["cid"], "deployed": "Customers"}

def verdict_for(col, **extra):
    cand = {"name": col, "column": col, **extra}
    plan = N.probe_plan(cand, ENT, other_columns=COLS)
    return N.advise(cand, ENT, N.apply_probes(plan, {k: run_sql(s) for k, s in plan.items()}))

# ── the KG fixture ─────────────────────────────────────────────────────────────
_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})
U, P, KG = "u1", "p1", "kg_act"
ST = GraphStore(P, U, kg_id=KG, db_path=TMP)
ST.set_kg_metadata(KG, "act", "advisor fixture", is_active=True)
_run(ST.import_bulk(
    [{"name": "fitness_db", "entity_type": "database", "properties": {"layer": "logical"}},
     {"name": "fitness_db.Customers", "entity_type": "entity",
      "properties": {"layer": "logical", "business_key": "cid", "deployed_table": "Customers"}},
     *[{"name": f"fitness_db.Customers.{c}", "entity_type": "attribute", "properties": {"layer": "logical"}}
       for c in ["cid"] + COLS],
     {"name": "fitness_db.Sales", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "sid"}},
     {"name": "fitness_db.Sales.city", "entity_type": "attribute", "properties": {"layer": "logical"}},
     {"name": "fitness_db.Orders", "entity_type": "entity", "properties": {"layer": "logical"}},
     {"name": "fitness_db.Orders.city", "entity_type": "attribute", "properties": {"layer": "logical"}}],
    [{"source_name": "fitness_db", "target_name": "fitness_db.Customers", "relationship_type": "contains"},
     *[{"source_name": "fitness_db.Customers", "target_name": f"fitness_db.Customers.{c}", "relationship_type": "contains"}
       for c in ["cid"] + COLS],
     {"source_name": "fitness_db.Sales", "target_name": "fitness_db.Sales.city", "relationship_type": "contains"},
     {"source_name": "fitness_db.Orders", "target_name": "fitness_db.Orders.city", "relationship_type": "contains"}]))

from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler, _KG_WRITE_ACTIONS
H = KnowledgeGraphHandler.__new__(KnowledgeGraphHandler)
H._get_store = lambda ctx: ST
async def _runner(ctx): return arun_sql
H._act_probe_runner = _runner
# Model B's released-write refusal reaches the ConfigManager (no users in this temp DB); it is
# covered by test_kg_model_b — here only its COVERAGE of the new actions is asserted (§7).
H._released_write_refusal = lambda action, context: None
CTX = {"profile_id": P, "user_uuid": U, "session_id": "s1"}
def with_switch(value, fn):
    old = _OP._FORCED["act"]; _OP.force_act(value)
    try: return fn()
    finally: _OP.force_act(old)
def names(): return sorted(e["name"] for e in _run(ST.list_entities(limit=9999)))
def by_name(n): return ST.get_entity_by_name(n)
def propose(action, **args): return _run(H.process({"action": action, **args}, CTX))


print("\n[§1] the six rules on real rows")
def t_rules():
    v = verdict_for("email")
    check("FD: two emails per customer ⇒ entity, a must", v.decision == "entity" and v.must and v.rule == "functional_dependency", v.to_dict())
    v = verdict_for("city")
    check("hidden identity: city determines zip + status ⇒ entity", v.decision == "entity" and v.rule == "hidden_identity", v.to_dict())
    v = verdict_for("phone")
    check("candidate-key floor: a column one-to-one with the key is an attribute, not a hidden entity",
          v.decision == "attribute", v.to_dict())
    v = verdict_for("status_code")
    check("closed enumeration from the measured value set ⇒ value domain, never an entity",
          v.decision == "value_domain" and v.rule == "closed_enumeration", v.to_dict())
    v = verdict_for("note")
    check("nothing fires ⇒ attribute, measured, not provisional", v.decision == "attribute" and not v.provisional and v.rule is None, v.to_dict())
    v = N.advise({"name": "AGE OF VICTIM-2", "positions": [2], "family": "AGE OF VICTIM"}, {"name": "Victim"}, {})
    check("multiplicity: a repeating-group position ⇒ entity with a sequence key", v.decision == "entity" and v.rule == "multiplicity", v.to_dict())
    v = N.advise({"name": "tags", "data_type": "ARRAY<STRING>"}, {"name": "Post"}, {})
    check("multiplicity: a list-valued type ⇒ entity", v.decision == "entity" and v.rule == "multiplicity", v.to_dict())
    v = N.advise({"name": "region_code", "referenced_by": ["Sales", "Orders", "Returns"]}, {"name": "Customers"}, {})
    check("shared reference: referenced from several entities ⇒ entity", v.decision == "entity" and v.rule == "shared_reference", v.to_dict())
    v = N.advise({"name": "address_history"}, {"name": "Customers"}, {})
    check("independent lifecycle ⇒ entity", v.decision == "entity" and v.rule == "independent_lifecycle", v.to_dict())
    v = N.advise({"name": "lead_status", "values": {"open": "Open", "closed": "Closed"}}, {"name": "Lead"}, {})
    check("closed enumeration declared with labels ⇒ value domain even with no rows", v.decision == "value_domain", v.to_dict())
    check("every verdict names its reasons", all(verdict_for(c).reasons for c in COLS))
run(t_rules)

print("\n[§2] precedence: a declaration wins; a contradiction is a finding; a must stands")
def t_precedence():
    v = verdict_for("zip", declared="attribute")
    check("declared attribute beats inferred hidden identity", v.decision == "attribute" and v.rule == "declared", v.to_dict())
    check("…and the disagreement is a finding, not silence", any("declared 'attribute'" in f for f in v.findings), v.findings)
    v = verdict_for("note", declared="entity")
    check("declared entity beats inferred attribute", v.decision == "entity" and v.rule == "declared", v.to_dict())
    check("…and when NO rule fired the declaration is not a contradiction (no finding, the reason says so)",
          not v.findings and any("nothing measured or structural contradicts" in r for r in v.reasons), v.to_dict())
    v = N.advise({"name": "Lead"}, {"name": "CaseObject"}, {}, )
    v2 = N.advise({"name": "Lead", "declared": "entity"}, {"name": "CaseObject"}, {})
    check("a brand-new declared entity: provisional, no finding, 'nothing in the data describes it yet'",
          v2.decision == "entity" and v2.provisional and not v2.findings and any("describes it yet" in r for r in v2.reasons), v2.to_dict())
    v = verdict_for("email", declared="attribute")
    check("a declaration does NOT reinstate an attribute the data refutes (FD is a must)",
          v.decision == "entity" and v.must, v.to_dict())
    check("…and the contradiction names the data", any("contradicted" in f for f in v.findings), v.findings)
    v = verdict_for("note", declared="attribute")
    check("a declaration that agrees raises confidence", v.decision == "attribute" and v.confidence >= 0.9 and not v.findings, v.to_dict())
run(t_precedence)

print("\n[§3] no rows ⇒ provisional; probes all failed ⇒ unknown")
def t_provisional():
    v = N.advise({"name": "Lead"}, {"name": "CaseObject"}, {})
    check("no rows ⇒ attribute, provisional, the reason stated",
          v.decision == "attribute" and v.provisional and any("no rows" in r for r in v.reasons), v.to_dict())
    v = N.advise({"name": "x", "column": "x"}, ENT, {"rows": None, "errors": ["rows", "fd", "distinct", "values"]})
    check("every probe failed ⇒ unknown (below the evidence floor)", v.decision == "unknown" and v.provisional, v.to_dict())
    plan = N.probe_plan({"name": "email", "column": "email"}, ENT)
    check("probe plan built only for a column that exists", set(plan) >= {"rows", "fd", "distinct", "values"})
    check("no plan for a brand-new attribute", N.probe_plan({"name": "new"}, ENT) == {})
    check("no plan without a deployed table or key", N.probe_plan({"name": "email", "column": "email"}, {"name": "X", "key": []}) == {})
run(t_provisional)

print("\n[§4] the model's residue")
def t_overrule():
    v = N.overrule(verdict_for("email"), "attribute")
    check("the model cannot overrule a must", v.decision == "entity" and v.must and any("refused" in f for f in v.findings), v.to_dict())
    v = N.overrule(N.advise({"name": "x", "column": "x"}, ENT, {"rows": None, "errors": ["fd"]}), "entity")
    check("where the rules left it open the model may choose", v.decision == "entity" and v.rule == "chosen", v.to_dict())
    v = N.overrule(verdict_for("status_code"), "entity")
    check("a non-must rule still stands; the choice is recorded, not taken", v.decision == "value_domain" and any("not taken" in f for f in v.findings), v.to_dict())
    check("an unknown choice is ignored", N.overrule(verdict_for("note"), "table").decision == "attribute")
run(t_overrule)

print("\n[§5] propose_attribute / propose_entity through the real handler + real store")
def t_handler():
    before = names()
    r = with_switch(False, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="loyalty_tier", draft=True))
    check("plane OFF ⇒ refusal naming the plane", "error" in r.spec and "OBDA-MODEL is not enabled" in r.spec["error"], r.spec)
    check("…and nothing written", names() == before)
    r = with_switch(False, lambda: propose("propose_entity", entity_name="Lead", draft=True))
    check("master OFF ⇒ propose_entity refused too", "error" in r.spec and names() == before)

    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="loyalty_tier",
                                          values=["gold", "silver", "bronze"]))
    check("ON, no draft: the verdict comes back (a vocabulary ⇒ value domain)", r.spec.get("verdict", {}).get("decision") == "value_domain", r.spec)
    check("…nothing written without draft=true", names() == before and r.spec.get("drafted") is False)
    check("…the row names the node a draft creates (the page reads its state NOW on every render)",
          "data-kg-node='fitness_db.Customers.loyalty_tier'" in r.html, r.html[:300])
    check("…and the result says what is owed: offer the draft (it was NOT drafted)",
          r.spec.get("next", "").startswith("NOT drafted yet") and "offer to draft" in r.spec["next"], r.spec.get("next"))
    check("…the row body carries the verdict in words", "value domain" in r.html and "<ul" in r.html)
    check("…the row opens on the answer (a proposal is a visual)", r.metadata.get("answer_surface", {}).get("open") is True)

    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="loyalty_tier",
                                          values=["gold", "silver", "bronze"], draft=True))
    node = by_name("fitness_db.Customers.loyalty_tier")
    check("draft=true writes the attribute", r.spec.get("drafted") is True and node is not None, r.spec)
    check("…and the result tells the agent it is ALREADY drafted — the next acts are the person's, never a second offer",
          r.spec.get("next", "").startswith("ALREADY DRAFTED") and "Do NOT offer to draft it again" in r.spec["next"]
          and "Govern" in r.spec["next"], r.spec.get("next"))
    p = (node or {}).get("properties") or {}
    check("…on the AUTHORED layer, as a draft, with provenance",
          p.get("layer") == "authored" and p.get("status") == "draft" and p.get("authored_by") == U and p.get("decision_id", "").startswith("dec_"), p)
    check("…with the value domain carried", p.get("allowed_values") == ["gold", "silver", "bronze"])
    dec = [e for e in _run(ST.list_entities(limit=9999)) if e["entity_type"] == "design_decision"]
    check("a design_decision node records the verdict", len(dec) == 1 and dec[0]["properties"].get("rule") == "closed_enumeration", dec)
    check("…on the authored layer with evidence + decided_by", dec[0]["properties"].get("layer") == "authored"
          and dec[0]["properties"].get("evidence") and dec[0]["properties"].get("decided_by") == U, dec[0]["properties"])
    rels = {(x["source_name"], x["target_name"], x["relationship_type"]) for x in _run(ST.list_relationships(limit=9999))}
    check("the entity contains the draft; the decision relates to both",
          ("fitness_db.Customers", "fitness_db.Customers.loyalty_tier", "contains") in rels
          and (dec[0]["name"], "fitness_db.Customers.loyalty_tier", "relates_to") in rels
          and (dec[0]["name"], "fitness_db.Customers", "relates_to") in rels, sorted(rels)[:6])
    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="loyalty_tier", draft=True))
    check("a second draft of the same name is refused, not duplicated", "already exists" in r.spec.get("note", "") and names().count("fitness_db.Customers.loyalty_tier") == 1)

    n0 = names()
    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="email", draft=True))
    check("an existing column is MEASURED (the probe runner ran)", r.metadata.get("measured") is True, r.metadata)
    check("the FD must refuses the attribute draft and points at propose_entity",
          r.spec["verdict"]["decision"] == "entity" and r.spec["verdict"]["must"] and "propose_entity" in r.spec["note"] and names() == n0, r.spec)

    r = with_switch(True, lambda: propose("propose_entity", entity_name="Lead", relates_to="fitness_db.Customers",
                                          attributes=["lead_id", {"name": "status", "data_type": "VARCHAR(20)"}, "owner"],
                                          key=["lead_id"], description="an open line of enquiry", draft=True))
    ent = by_name("fitness_db.Lead")
    check("propose_entity drafts the entity under the parent's root", r.spec.get("drafted") is True and ent is not None, r.spec)
    check("…an entity drafted says the same (one rule for both proposals)", r.spec.get("next", "").startswith("ALREADY DRAFTED"), r.spec.get("next"))
    pe = (ent or {}).get("properties") or {}
    check("…authored, draft, key recorded", pe.get("layer") == "authored" and pe.get("status") == "draft" and pe.get("business_key") == "lead_id", pe)
    check("…declared entity ⇒ provisional (no rows) and the verdict says so", r.spec["verdict"]["provisional"] and r.spec["verdict"]["decision"] == "entity", r.spec["verdict"])
    for a in ("lead_id", "status", "owner"):
        check(f"…attribute {a} drafted on the authored layer", ((by_name(f"fitness_db.Lead.{a}") or {}).get("properties") or {}).get("layer") == "authored")
    rels = {(x["source_name"], x["target_name"], x["relationship_type"]) for x in _run(ST.list_relationships(limit=9999))}
    check("…contains edges + the belongs-to link to the parent",
          ("fitness_db.Lead", "fitness_db.Lead.status", "contains") in rels and ("fitness_db.Lead", "fitness_db.Customers", "relates_to") in rels)
    check("…no base node was touched", by_name("fitness_db.Customers")["properties"].get("layer") == "logical")
    r = with_switch(True, lambda: propose("propose_entity", entity_name="Lead", relates_to="fitness_db.Customers", draft=True))
    check("the same entity twice is refused", "already exists" in r.spec.get("note", "") and names().count("fitness_db.Lead") == 1)
    r = with_switch(True, lambda: propose("propose_entity", entity_name="Ghost", relates_to="fitness_db.Nope", draft=True))
    check("an unknown parent is refused by name", "Nope" in r.spec.get("error", ""))
run(t_handler)

print("\n[§6] the modelAuthoring grant")
def t_grant():
    from trusted_data_agent.core.agent_authority import AgentPrincipal, AgentScope, set_current_principal
    tok = set_current_principal(AgentPrincipal(owner_uuid=U, profile_id=P, scope=AgentScope(autonomous=True)))
    try:
        n0 = names()
        r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="tier2", values=["a", "b"], draft=True))
        check("an autonomous agent without the grant is refused", "modelAuthoring" in r.spec.get("error", "") and names() == n0, r.spec)
    finally:
        set_current_principal(None)
    tok = set_current_principal(AgentPrincipal(owner_uuid=U, profile_id=P, scope=AgentScope(autonomous=True, model_authoring=True)))
    try:
        r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="tier2", values=["a", "b"], draft=True))
        check("with the grant the draft lands, attributed to the agent",
              r.spec.get("drafted") is True and (by_name("fitness_db.Customers.tier2")["properties"].get("authored_by") == f"agent:{P}"), r.spec)
    finally:
        set_current_principal(None)
run(t_grant)

print("\n[§7] the new actions are write actions")
def t_write_actions():
    check("propose_* ∈ _KG_WRITE_ACTIONS (the released-mode refusal covers them)",
          {"propose_entity", "propose_attribute"} <= set(_KG_WRITE_ACTIONS))
    ok, msg = H.validate_arguments({"action": "propose_attribute", "entity": "x"})
    check("validation names the missing attribute_name", not ok and "attribute_name" in msg)
    ok, msg = H.validate_arguments({"action": "propose_entity"})
    check("validation names the missing entity_name", not ok and "entity_name" in msg)
    check("both actions are valid actions", H.validate_arguments({"action": "propose_entity", "entity_name": "L"})[0])
run(t_write_actions)

print("\n[§8] context cost: the proposal surface is the OBDA-MODEL component's own")
def t_context_cost():
    """The knowledge-graph tool advertises no proposal action or argument on ANY switch —
    propose_* is TDA_ModelProposal (the obda_model component), offered exactly where that
    component is enabled; the knowledge-graph tool keeps the actions as unadvertised aliases."""
    from trusted_data_agent.components.manager import ComponentManager
    m = json.load(open(os.path.join(ROOT, "components/builtin/knowledge_graph/manifest.json")))
    i = json.load(open(os.path.join(ROOT, "components/builtin/knowledge_graph/instructions.json")))
    mm = json.load(open(os.path.join(ROOT, "components/builtin/obda_model/manifest.json")))
    mi = json.load(open(os.path.join(ROOT, "components/builtin/obda_model/instructions.json")))
    td = m["tool_definition"]
    for on in (False, True):
        blob = json.dumps(with_switch(on, lambda: ComponentManager._gated_tool_definition(td, {})))
        check(f"KG tool, switch {'ON' if on else 'OFF'}: no propose_* word in any tool description",
              "propose" not in blob and "authored" not in blob, blob[:200])
        for level in ("medium", "heavy"):
            t = with_switch(on, lambda: ComponentManager.apply_instruction_gates(i[level], {}))
            check(f"KG instructions[{level}], switch {'ON' if on else 'OFF'}: not a word of proposing", "propose" not in t)
    on_blob = json.dumps(mm["tool_definition"])
    check("OBDA-MODEL tool: the actions and every argument are described, unwrapped",
          mm["tool_definition"]["name"] == "TDA_ModelProposal" and "propose_attribute" in on_blob
          and all(a in mm["tool_definition"]["args"] for a in
                  ("attribute_name", "draft", "data_type", "values", "key", "relates_to", "from_attribute", "decision", "declared"))
          and "[[gate:" not in on_blob)
    for level in ("medium", "heavy"):
        check(f"OBDA-MODEL instructions[{level}]: the paragraph, unwrapped",
              "propose_attribute" in mi[level] and "Never overrule a must" in mi[level] and "[[gate:" not in mi[level])
    check("the 'act' gate key is the agent's OBDA-ACT plane", with_switch(False, lambda: ComponentManager._instruction_gate_open("act", {"id": "p"})) is False
          and with_switch(True, lambda: ComponentManager._instruction_gate_open("act", {"id": "p"})) is True
          and ComponentManager._instruction_gate_open("act", None) is False)
run(t_context_cost)

def t_drafts_never_render():
    """§2b: an authored node competes for the planner's context only once PUBLISHED — the
    drafts §2–§5 wrote (fitness_db.Lead, Customers.loyalty_tier) must never seed and must
    never survive subgraph assembly; a published one may."""
    import ast
    from components.builtin.knowledge_graph import handler as HM
    check("a draft is unpublished-authored; a published authored node is not; a walked node is not",
          HM._unpublished_authored({"properties": {"layer": "authored", "status": "draft"}})
          and HM._unpublished_authored({"properties": {"layer": "authored"}})
          and not HM._unpublished_authored({"properties": {"layer": "authored", "status": "published"}})
          and not HM._unpublished_authored({"properties": {"layer": "logical"}}) and not HM._unpublished_authored({}))
    lead = by_name("fitness_db.Lead"); cust = by_name("fitness_db.Customers")
    sub = {"entities": [lead, cust, {"id": -1, "name": "x", "properties": {"layer": "authored", "status": "published"}}],
           "relationships": [{"source_id": lead["id"], "target_id": cust["id"], "source_name": lead["name"], "target_name": cust["name"]},
                             {"source_id": cust["id"], "target_id": -1, "source_name": cust["name"], "target_name": "x"}]}
    cut = HM._cut_unpublished_authored(dict(sub))
    check("the cut removes the draft AND the edge that names it, keeps the walked node and the published one",
          [e["name"] for e in cut["entities"]] == ["fitness_db.Customers", "x"] and len(cut["relationships"]) == 1 and cut["relationships"][0]["target_name"] == "x")
    # the seeder, executed: 'Lead' names the draft exactly — it must not come back as a seed
    seeds = with_switch(True, lambda: _run(H._search_entities_for_query(ST, "open leads", seeding_report={})))
    check("the seeder never offers a draft as a seed", not any((e.get("properties") or {}).get("layer") == "authored" for e in seeds), [e["name"] for e in seeds])
    src = open(os.path.join(ROOT, "components/builtin/knowledge_graph/handler.py")).read()
    tree = ast.parse(src)
    def body(name):
        fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
        return ast.get_source_segment(src, fn) if fn else ""
    check("both seams read the ONE predicate: the seeder filters by it, assembly cuts by it",
          # both read the graph's whole unpublished-extension set (drafts + what landing them wrote)
          "_unpublished_authored(e, _hidden)" in body("_search_entities_for_query")
          and "_cut_unpublished_authored(subgraph, await _unpublished_names_of(store))" in body("get_context_enrichment"))
    a = body("get_context_enrichment")
    check("the assembly cut sits after the product cut and before the event is built", 0 < a.find('_obda_mode == "off"') < a.find("_cut_unpublished_authored(subgraph, ") < a.find("entity_types = list("))
run(t_drafts_never_render)

print("\n[§9] A1 integration: a resync keeps the drafts")
def t_resync():
    walk_e = [e for e in _run(ST.list_entities(limit=9999)) if (e.get("properties") or {}).get("layer") == "logical"]
    walk_r = [r for r in _run(ST.list_relationships(limit=9999))
              if all(((by_name(n) or {}).get("properties") or {}).get("layer") == "logical" for n in (r["source_name"], r["target_name"]))]
    ents = [{"name": e["name"], "entity_type": e["entity_type"], "properties": e["properties"]} for e in walk_e]
    rels = [{"source_name": r["source_name"], "target_name": r["target_name"], "relationship_type": r["relationship_type"]} for r in walk_r]
    res = with_switch(True, lambda: _run(ST.replace_model_layer(ents, rels)))
    check("the drafts survive the resync", by_name("fitness_db.Lead") is not None and by_name("fitness_db.Customers.loyalty_tier") is not None, res)
    check("…and the decisions do", any(e["entity_type"] == "design_decision" for e in _run(ST.list_entities(limit=9999))))
    rels2 = {(x["source_name"], x["target_name"], x["relationship_type"]) for x in _run(ST.list_relationships(limit=9999))}
    check("…the contains edge from the re-walked entity to the draft is re-linked",
          ("fitness_db.Customers", "fitness_db.Customers.loyalty_tier", "contains") in rels2 and res.get("kept_edges_relinked", 0) >= 1, res)
run(t_resync)

print("\n[§9b] a draft never degrades the base ontology's conformance")
def t_conformance():
    from trusted_data_agent.kgstore import conformance as C
    ents = _run(ST.list_entities(limit=9999)); rels = _run(ST.list_relationships(limit=9999))
    rep = C.check_conformance(ents, rels) if hasattr(C, "check_conformance") else C.validate(ents, rels)
    issues = rep.get("issues", []) if isinstance(rep, dict) else getattr(rep, "issues", [])
    names = [getattr(i, "code", None) or (i.get("code") if isinstance(i, dict) else None) for i in issues]
    lm = [i for i, c in zip(issues, names) if c == "layer_mismatch"]
    check("no authored node is reported as a layer mismatch", not lm, str(lm)[:300])
run(t_conformance)


print("\n[§10] the authored listing and the two REST seams (A2 UX half)")
def t_authored_listing():
    out = _run(KnowledgeGraphHandler.authored_nodes(ST))
    names_ = [n["name"] for n in out["nodes"]]
    check("every draft is listed, entities before attributes, decisions apart",
          "fitness_db.Lead" in names_ and "fitness_db.Customers.loyalty_tier" in names_
          and names_.index("fitness_db.Lead") < names_.index("fitness_db.Customers.loyalty_tier")
          and all(n["entity_type"] != "design_decision" for n in out["nodes"]) and out["decisions"], out)
    lead = next(n for n in out["nodes"] if n["name"] == "fitness_db.Lead")
    check("a draft carries its state, author and the decision that produced it (rule + evidence)",
          lead["status"] == "draft" and lead["authored_by"] and lead["decision"] and lead["decision"]["rule"] == "declared"
          and lead["decision"]["evidence"], lead)
    check("no base node is listed", all((by_name(n["name"]) or {}).get("properties", {}).get("layer") == "authored" for n in out["nodes"]))
    # A3: an entity's own attributes travel WITH it — one draft, listed once, its attributes named on the row
    check("an authored entity folds its attributes into its row; they are not drafts of their own",
          not any(n["name"].startswith("fitness_db.Lead.") for n in out["nodes"])
          # belonging to Customers adds the reference named like its declared key (business_terms)
          and sorted(a["leaf"] for a in lead.get("attributes") or [])
          == sorted([a for a in ["lead_id", "owner", "status"] if by_name(f"fitness_db.Lead.{a}")] + ["cid"]),
          lead.get("attributes"))
    check("…the reference to the parent is the platform's, named like its key and marked so",
          [a.get("added_by_platform") for a in lead.get("attributes") or [] if a["leaf"] == "cid"] == ["reference"],
          lead.get("attributes"))
    check("…while an attribute authored on a BASE entity is a draft of its own", "fitness_db.Customers.loyalty_tier" in names_ and out["count"] == len(out["nodes"]))
    check("a declared entity is never explained as 'an attribute of' its parent", not any("an attribute of" in str(r) for r in lead["decision"]["evidence"]), lead["decision"]["evidence"])
    import ast as _ast
    src = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
    tree = _ast.parse(src)
    fns = {f.name: f for f in _ast.walk(tree) if isinstance(f, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
    check("POST /v1/knowledge-graph/propose exists and runs the handler's own process()",
          "kg_propose" in fns and "process" in {c.func.attr for c in _ast.walk(fns["kg_propose"]) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)})
    # The route runs its listing in a worker thread (off the event loop): the route must
    # hand off to `_kg_authored_sync`, and THAT must read the one listing.
    check("GET /v1/knowledge-graph/authored exists and hands its listing to the worker",
          "kg_authored" in fns and "_kg_authored_sync" in {n.id for n in _ast.walk(fns["kg_authored"]) if isinstance(n, _ast.Name)})
    check("…which reads the ONE listing",
          "_kg_authored_sync" in fns and "authored_nodes" in {c.func.attr for c in _ast.walk(fns["_kg_authored_sync"]) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)})
    check("both seams resolve the graph through the owner lookup (the caller's own graphs only)",
          all("_kg_owner_profile_for_id" in {c.func.id for c in _ast.walk(fns[k]) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)} for k in ("kg_propose", "kg_authored")))
    # the row carries the act only when a draft is OWED
    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="tier3", values=["x", "y"]))
    check("a draftable, undrafted proposal carries the one-click act with its args and graph id",
          "kg-proposal-draft" in r.html and "data-kg-args=" in r.html and f"data-kg-id='{KG}'" in r.html and '&quot;action&quot;: &quot;propose_attribute&quot;' in r.html, r.html[:400])
    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="tier3", values=["x", "y"], draft=True))
    check("a drafted row carries NO act (Rule 8.5)", r.spec.get("drafted") is True and "kg-proposal-draft" not in r.html)
    r = with_switch(True, lambda: propose("propose_attribute", entity="fitness_db.Customers", attribute_name="email"))
    check("a proposal the advisor refuses as an attribute carries no attribute act", "kg-proposal-draft" not in r.html and r.spec["verdict"]["decision"] == "entity")
run(t_authored_listing)

print(f"\n=== KG normalisation (A2) results: {PASS} passed, {FAIL} failed ===")
try: os.unlink(TMP)
except OSError: pass
sys.exit(1 if FAIL else 0)
