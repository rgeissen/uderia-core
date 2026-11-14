"""PROP-008 increment B1 — the `action_type` trust kind: authoring seam, earned signature,
ratchet, and exclusion from the ontology hash.

Real SQLite (temp DB, real schema + migration), the real GraphStore, the real trust store
(Ed25519 through the provenance key), the real A1–A6 gate, the real resolver; no doubles for
the thing under test. CI-pure (structural mode — the live probes are `test_action_validation`'s).

  §1  the vocabulary: `action_type` + `acts_on` registered; NOT agent-visible (not semantic);
      an RDF class; the kind registered with a validator and a live-hash leaf
  §2  authoring: a REJECT writes nothing; OBDA-MODEL · OBDA-ACT off refuses; an unknown or DRAFT target is
      refused; a published extension entity is a valid target; the acts_on edge is written;
      an edited manifest drops the stored report
  §3  the five refusals by status (invalid_manifest · unvalidated · stale_report ·
      gate_blocked · unsigned_ingredient) — each changes nothing
  §4  sign: structural → partial (verified_declared) naming A3/A6; the badge reads trusted
  §5  the ratchet: an edited manifest reads CHANGED (🟠); re-validate + re-sign → trusted
  §6  the ontology hash: action nodes, acts_on edges and the ledger artifact are excluded —
      authoring, validating and signing an action never moves the base signature
  §7  the enrichment never shows an action to the agent (B4 renders granted types only)
  §8  the model under the action (and the extension its target lives in — signing needs it signed): the report records the graph facts its proof read (the
      target's resolution, its columns, the writable attributes' closed domains) and the
      signature covers them — a change to one reads CHANGED naming it, signing refuses the
      stale proof, perform's refusal says why; a change ELSEWHERE in the graph (or a base
      re-sign) moves nothing; validation and the trust reading compute the facts with ONE
      function; a report from before the facts were recorded proves nothing (re-validate)

Run: PYTHONPATH=src:. python3 test/test_action_trust.py
"""
import asyncio
import copy
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
def _run(coro): return asyncio.run(coro)

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.kgstore import factory as _factory
from trusted_data_agent.kgstore import types as T
from trusted_data_agent.auth.database import _migrate_to_multi_kg
from trusted_data_agent.core import action_types as AT
from trusted_data_agent.core.trust import actions as A, store as S, composition as C, content as CT
from trusted_data_agent.core.trust.attestation import registered_kinds
from components.builtin.knowledge_graph.graph_store import GraphStore

_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False
_OP.force_act(True)
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})

U, P = "u1", "p1"
def store(kg): return GraphStore(P, U, kg_id=kg, db_path=TMP)
def base_hash(kg): return C.kg_graph_hash(kg, TMP)[0]
def sign_base(kg): return S.sign("ontology", kg, base_hash(kg), signer_uuid=U, db_path=TMP)

WALK = [{"name": "fitness_db", "entity_type": "database", "properties": {"layer": "logical"}},
        {"name": "Customers", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "CustomerID"}},
        {"name": "Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical", "deployed_column": "fitness_db.Customers.CustomerID"}},
        {"name": "Customers.LoyaltyTier", "entity_type": "attribute", "properties": {"layer": "logical", "deployed_column": "fitness_db.Customers.LoyaltyTier",
                                                                                     "allowed_values": [{"value": "bronze"}, {"value": "silver"}, {"value": "gold"}]}},
        {"name": "Customers.Status", "entity_type": "attribute", "properties": {"layer": "logical", "deployed_column": "fitness_db.Customers.Status"}},
        {"name": "fitness_db.Customers", "entity_type": "table", "properties": {"layer": "physical", "infrastructure": "teradata"}}]
RELS = [{"source_name": "fitness_db", "target_name": "Customers", "relationship_type": "contains"},
        {"source_name": "Customers", "target_name": "Customers.CustomerID", "relationship_type": "contains"},
        {"source_name": "Customers", "target_name": "Customers.LoyaltyTier", "relationship_type": "contains"},
        {"source_name": "Customers", "target_name": "Customers.Status", "relationship_type": "contains"},
        {"source_name": "fitness_db.Customers", "target_name": "Customers", "relationship_type": "implements"}]

RAW = {"action": "set_customer_tier", "version": 1, "backend": "teradata",
       "target": {"entity": "Customers", "deployed": "fitness_db.Customers", "key": ["CustomerID"]},
       "operation": "update",
       "attributes": [{"name": "LoyaltyTier", "type": "string", "required": True, "value_domain": ["bronze", "silver", "gold"]}],
       "conditions": {"pre": [{"id": "not_closed", "expr": "Status <> 'closed'"}]},
       "bound": {"max_rows": 1},
       "authority": {"required_role": "UDERIA_ACT_FITNESS", "posture": "review"}}

def seed(kg):
    st = store(kg); st.set_kg_metadata("Fitness", is_active=True); st.import_bulk(WALK, RELS); return st
def node(kg, name="set_customer_tier"): return store(kg).get_entity_by_name(name, "action_type")
def props(kg, name="set_customer_tier"): return dict((node(kg, name) or {}).get("properties") or {})
def set_props(kg, p, name="set_customer_tier"): _run(store(kg).update_entity(node(kg, name)["id"], properties=p))
def refusal(fn):
    try: _run(fn()); return None
    except AT.ActionTypeError as e: return e.code

def t_vocabulary():
    print("[§1] the vocabulary")
    check("action_type is a logical type", "action_type" in T.LOGICAL_TYPES and "action_type" in T.ACTION_TYPES)
    check("…and NOT semantic (never shown to the agent by default)", "action_type" not in T.SEMANTIC_TYPES)
    check("acts_on is a relationship type", "acts_on" in T.RELATIONSHIP_TYPES)
    from trusted_data_agent.semantic_interchange import vocab
    check("an RDF class for the standards export", "action_type" in str(getattr(vocab, "ENTITY_TYPE_CLASS", vocab.__dict__)))
    check("the trust kind is registered with a validator and a live-hash leaf",
          A.ACTION_KIND in registered_kinds() and A.ACTION_KIND in CT._LEAF)
    check("the artifact id round-trips", A.split_artifact_id(A.action_artifact_id("kgA", "x_y")) == ("kgA", "x_y"))

def t_authoring():
    print("[§2] authoring")
    st = seed("kg-a")
    _OP.force_act(False)
    check("OBDA-MODEL · OBDA-ACT off → refused, nothing written", refusal(lambda: AT.author_action(st, RAW)) == "act_off" and node("kg-a") is None)
    _OP.force_act(True)
    bad = copy.deepcopy(RAW); bad["operation"] = "delete"
    check("a REJECT carries its code and writes nothing", refusal(lambda: AT.author_action(st, bad)) == "bad_operation" and node("kg-a") is None)
    unk = copy.deepcopy(RAW); unk["target"]["entity"] = "Leads"
    check("an unknown target is refused", refusal(lambda: AT.author_action(st, unk)) == "unknown_target")
    _run(st.add_entity("Leads", "entity", {"layer": T.AUTHORED_LAYER, "status": "draft", "extension_id": "ext-u1"}))
    check("a DRAFT target is refused", refusal(lambda: AT.author_action(st, unk)) == "draft_target")
    pub = store("kg-a").get_entity_by_name("Leads", "entity")
    _run(store("kg-a").update_entity(pub["id"], properties={**pub["properties"], "status": T.AUTHORED_STATUS_PUBLISHED}))
    unk2 = copy.deepcopy(unk); unk2["action"] = "set_lead_tier"
    check("a PUBLISHED extension entity is a valid target", refusal(lambda: AT.author_action(st, unk2)) is None)
    res = _run(AT.author_action(st, RAW, actor="u1"))
    p = props("kg-a")
    check("the node is written with its canonical manifest + hash", res["created"] and p["manifest_hash"] == res["manifest_hash"] and p["manifest"]["action"] == "set_customer_tier")
    check("the inspector summary is flattened", p["operation"] == "update" and p["writable"] == "LoyaltyTier" and p["target_deployed"] == "fitness_db.Customers")
    rels = _run(store("kg-a").get_relationships(node("kg-a")["id"], "both"))
    check("the acts_on edge points at the target", any(r["relationship_type"] == "acts_on" and r["target_name"] == "Customers" for r in rels))
    _run(AT.validate(store("kg-a"), "set_customer_tier"))
    check("validate stamps a report", isinstance(props("kg-a").get("validation_report"), dict))
    _run(AT.author_action(st, RAW))
    check("re-authoring the SAME manifest keeps the report", isinstance(props("kg-a").get("validation_report"), dict))
    ed = copy.deepcopy(RAW); ed["bound"]["max_rows"] = 2
    r2 = _run(AT.author_action(st, ed))
    check("an edited manifest DROPS the stored report (update_entity merges — dropped explicitly)",
          r2["manifest_changed"] and props("kg-a").get("validation_report") is None)

def t_refusals():
    print("[§3] the five refusals")
    st = seed("kg-r"); _run(AT.author_action(st, RAW))
    r = A.sign_action("kg-r", "set_customer_tier", U, db_path=TMP)
    check("unvalidated", r.get("reason") == "unvalidated" and not r["signed"], r)
    _run(AT.validate(store("kg-r"), "set_customer_tier"))
    r = A.sign_action("kg-r", "set_customer_tier", U, db_path=TMP)
    check("unsigned_ingredient (the ontology is not signed)", r.get("reason") == "unsigned_ingredient", r)
    sign_base("kg-r")
    p = props("kg-r"); p["manifest"] = {**p["manifest"], "bound": {"max_rows": 5}}; set_props("kg-r", p)
    r = A.sign_action("kg-r", "set_customer_tier", U, db_path=TMP)
    check("stale_report (the report validated another manifest)", r.get("reason") == "stale_report", r)
    p = props("kg-r"); p["manifest"] = {**p["manifest"], "operation": "drop"}; set_props("kg-r", p)
    r = A.sign_action("kg-r", "set_customer_tier", U, db_path=TMP)
    check("invalid_manifest", r.get("reason") == "invalid_manifest", r)
    p["manifest"] = copy.deepcopy(RAW); p["manifest"]["target"]["deployed"] = "fitness_db.Other"; set_props("kg-r", p)
    _run(AT.validate(store("kg-r"), "set_customer_tier"))
    r = A.sign_action("kg-r", "set_customer_tier", U, db_path=TMP)
    check("gate_blocked names the blocker (A1 drift)", r.get("reason") == "gate_blocked" and "A1" in str(r["validation"]), r)
    check("no refusal wrote an attestation", S.attested_hash(A.ACTION_KIND, A.action_artifact_id("kg-r", "set_customer_tier"), db_path=TMP) is None)
    check("not_found for an unknown action", A.sign_action("kg-r", "nope", U, db_path=TMP)["reason"] == "not_found")

def t_sign_and_ratchet():
    print("[§4/§5] sign + the ratchet")
    st = seed("kg-s"); sign_base("kg-s")
    _run(AT.author_action(st, RAW)); _run(AT.validate(store("kg-s"), "set_customer_tier"))
    rep = props("kg-s")["validation_report"]
    check("structural report: A1 + A2 + A4 from the graph, A3/A6 unverified",
          {c["id"]: c["status"] for c in rep["checks"]} == {"A1": "pass", "A2": "pass", "A3": "unverified", "A4": "pass", "A5": "pass", "A6": "unverified"},
          [(c["id"], c["status"], c["detail"]) for c in rep["checks"]])
    check("A2 compared against the graph's closed domain (schema mode)", next(c for c in rep["checks"] if c["id"] == "A2")["mode"] == "schema")
    r = A.sign_action("kg-s", "set_customer_tier", U, db_path=TMP)
    check("signs as PARTIAL — verified_declared, naming A3/A6", r["signed"] and r["validation"]["status"] == "verified_declared"
          and "A3" in r["validation"]["note"] and "A6" in r["validation"]["note"], r)
    check("the badge reads trusted", A.action_state("kg-s", "set_customer_tier", TMP)["state"] == "trusted")
    check("the live hash is the content-hash of manifest + report", A.live_hash(A.action_artifact_id("kg-s", "set_customer_tier"), TMP) == r["version_hash"])
    ed = copy.deepcopy(RAW); ed["description"] = "edited"
    _run(AT.author_action(st, ed))
    check("an edited manifest reads CHANGED (🟠), never silently intact", A.action_state("kg-s", "set_customer_tier", TMP)["state"] == "changed")
    p = props("kg-s"); p["validation_report"] = rep; set_props("kg-s", p)
    check("…even with the OLD report re-attached (the report proves another manifest)",
          A.action_state("kg-s", "set_customer_tier", TMP)["state"] == "changed")
    _run(AT.validate(store("kg-s"), "set_customer_tier"))
    r = A.sign_action("kg-s", "set_customer_tier", U, db_path=TMP)
    p = props("kg-s"); good = p["validation_report"]
    p["validation_report"] = {**good, "mode": "live"}; set_props("kg-s", p)
    check("a different REPORT under the same manifest reads CHANGED (the proof is in the signature)",
          A.action_state("kg-s", "set_customer_tier", TMP)["state"] == "changed")
    p["validation_report"] = good; set_props("kg-s", p)
    check("re-validate + re-sign → trusted again", r["signed"] and A.action_state("kg-s", "set_customer_tier", TMP)["state"] == "trusted")
    reordered = dict(reversed(list(ed.items())))
    _run(AT.author_action(st, reordered))
    check("re-authoring the same contract in another key order changes nothing", A.action_state("kg-s", "set_customer_tier", TMP)["state"] == "trusted")
    check("the agent validator is clean for a fully live report",
          C and __import__("trusted_data_agent.core.trust.validators", fromlist=["x"]).action_type_validator(
              manifest=RAW, stored_report={**rep, "manifest_hash": __import__("trusted_data_agent.dbdeploy.action_model", fromlist=["x"]).manifest_hash(
                  __import__("trusted_data_agent.dbdeploy.action_model", fromlist=["x"]).parse_manifest(RAW)),
                  "checks": [{"id": i, "status": "pass"} for i in ("A1", "A2", "A3", "A4", "A5", "A6")], "mode": "live"},
              ontology_state="trusted").status == "verified_write")

def t_ontology_hash():
    print("[§6] the ontology hash")
    st = seed("kg-h"); h0 = base_hash("kg-h"); sign_base("kg-h")
    _run(AT.author_action(st, RAW)); _run(AT.validate(store("kg-h"), "set_customer_tier"))
    check("authoring + validating an action leaves the base hash byte-identical", base_hash("kg-h") == h0)
    nid = _run(st.add_entity("fitness_db.UDERIA_ACT_Customers", "ddl_artifact", {"layer": "physical", "purpose": "action_ledger"}))
    _run(st.add_relationship(nid, store("kg-h").get_entity_by_name("fitness_db.Customers", "table")["id"], "derives_from"))
    check("the registered action ledger artifact does not move it either", base_hash("kg-h") == h0)
    A.sign_action("kg-h", "set_customer_tier", U, db_path=TMP)
    from trusted_data_agent.core.trust.attestation import resolve_state
    check("the ontology badge stays trusted through it all", resolve_state(S.attested_hash("ontology", "kg-h", db_path=TMP), base_hash("kg-h")).value == "trusted")
    other = _run(st.add_entity("fitness_db.v_report", "ddl_artifact", {"layer": "physical", "purpose": "metric_view"}))
    check("…while a DIFFERENT ddl_artifact still moves it (the exclusion is narrow)", base_hash("kg-h") != h0)

def t_enrichment():
    print("[§7] the enrichment never shows an action")
    import ast, inspect
    from components.builtin.knowledge_graph import handler as H
    sub = {"entities": [{"id": 1, "name": "Customers", "entity_type": "entity"},
                        {"id": 2, "name": "set_customer_tier", "entity_type": "action_type"},
                        {"id": 3, "name": "Customers.Status", "entity_type": "attribute"}],
           "relationships": [{"source_id": 2, "target_id": 1, "source_name": "set_customer_tier", "target_name": "Customers", "relationship_type": "acts_on"},
                             {"source_id": 1, "target_id": 3, "source_name": "Customers", "target_name": "Customers.Status", "relationship_type": "contains"},
                             {"source_id": 2, "target_id": 3, "source_name": "set_customer_tier", "target_name": "Customers.Status", "relationship_type": "relates_to"}]}
    before = copy.deepcopy(sub)
    out = H._cut_action_types(sub)
    check("the action node leaves the subgraph", [e["name"] for e in out["entities"]] == ["Customers", "Customers.Status"])
    check("…with its acts_on edge AND any other edge naming it", [r["relationship_type"] for r in out["relationships"]] == ["contains"])
    check("the input is not mutated", sub == before)
    stray = {"entities": [{"id": 1, "name": "Customers", "entity_type": "entity"}],
             "relationships": [{"source_id": 9, "target_id": 1, "source_name": "x", "target_name": "Customers", "relationship_type": "acts_on"}]}
    check("an acts_on edge whose action node was not extracted is cut too", H._cut_action_types(stray)["relationships"] == [])
    check("a subgraph with no action is returned unchanged", H._cut_action_types(before["entities"][:1] and {"entities": before["entities"][:1], "relationships": []}) == {"entities": before["entities"][:1], "relationships": []})
    tree = ast.parse(inspect.getsource(H.KnowledgeGraphHandler.get_context_enrichment).lstrip().replace("\n    ", "\n") if False else open(H.__file__).read())
    enr = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "get_context_enrichment")
    calls = [c.func.id for c in ast.walk(enr) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    check("get_context_enrichment calls the cut (unconditionally, on the AST)", "_cut_action_types" in calls)
    check("action_type has an inspector colour", '"action_type": "#' in open(H.__file__).read())

def t_model_facts():
    print("[§8] the model under the action")
    from trusted_data_agent.core import action_perform as AP
    kg, name = "kg-m", "set_customer_tier"
    st = seed(kg); sign_base(kg)
    _run(AT.author_action(st, RAW)); _run(AT.validate(store(kg), name))
    r = A.sign_action(kg, name, U, db_path=TMP)
    rec = props(kg)["validation_report"].get("model_facts") or {}
    check("the report records the facts its proof read: resolution · columns · closed domains",
          rec.get("facts") == {"resolution": {"Customers": "fitness_db.Customers"},
                               "graph_domains": {"LoyaltyTier": ["bronze", "gold", "silver"]},
                               "entity_columns": ["CustomerID", "LoyaltyTier", "Status"]}
          and rec.get("fingerprint") == AT.facts_fingerprint(rec["facts"]), rec)
    m, rep, ents, rels, _ = A._load(kg, name, TMP)
    check("validation (through the store) and the trust reading (through the rows) compute the SAME facts",
          A.model_now(m, rep, ents, rels) == (rec["facts"], []))
    s0 = A.action_state(kg, name, TMP)
    check("signed over them: trusted, nothing drifted", r["signed"] and s0["state"] == "trusted" and s0["drift"] == [], s0)

    oid = _run(st.add_entity("Orders", "entity", {"layer": "logical"}))
    aid = _run(st.add_entity("Orders.Channel", "attribute", {"layer": "logical", "allowed_values": [{"value": "web"}, {"value": "store"}]}))
    _run(st.add_relationship(oid, aid, "contains"))
    check("a change ELSEWHERE in the graph moves nothing", A.action_state(kg, name, TMP)["state"] == "trusted")
    sign_base(kg)
    check("…nor does re-signing the base (the action binds its facts, not the model's signature)",
          A.action_state(kg, name, TMP)["state"] == "trusted")

    lt = store(kg).get_entity_by_name("Customers.LoyaltyTier", "attribute"); before = copy.deepcopy(lt["properties"])
    _run(store(kg).update_entity(lt["id"], properties={**before, "allowed_values": before["allowed_values"] + [{"value": "platinum"}]}))
    s1 = A.action_state(kg, name, TMP)
    check("a changed domain of a WRITTEN attribute reads CHANGED, naming it",
          s1["state"] == "changed" and s1["drift"] == ["the allowed values of LoyaltyTier changed"] and s1["stale"] == "model", s1)
    att = S.attested_hash(A.ACTION_KIND, A.action_artifact_id(kg, name), db_path=TMP)
    r2 = A.sign_action(kg, name, U, db_path=TMP)
    check("signing the stale proof is refused (stale_report, the drift named) and writes nothing",
          not r2["signed"] and r2["reason"] == "stale_report" and "LoyaltyTier" in str(r2["validation"])
          and S.attested_hash(A.ACTION_KIND, A.action_artifact_id(kg, name), db_path=TMP) == att, r2)
    words = AP._trust_state(kg, name, TMP)
    check("perform's refusal says why", words.startswith("changed — the model under it changed: the allowed values of LoyaltyTier"), words)
    _run(store(kg).update_entity(lt["id"], properties=before))
    check("the model back as it was → the signature applies again (exact, not sticky)",
          A.action_state(kg, name, TMP)["state"] == "trusted")

    cid = store(kg).get_entity_by_name("Customers", "entity")["id"]
    did = _run(st.add_entity("Customers.Nickname", "attribute", {"layer": T.AUTHORED_LAYER, "status": "draft", "extension_id": "ext-u1"}))
    _run(st.add_relationship(cid, did, "contains"))
    check("a DRAFT attribute on the target moves nothing (a draft is not in the model yet)",
          A.action_state(kg, name, TMP)["state"] == "trusted")
    eid = _run(st.add_entity("Customers.Email", "attribute", {"layer": "logical", "deployed_column": "fitness_db.Customers.Email"}))
    _run(st.add_relationship(cid, eid, "contains"))
    s2 = A.action_state(kg, name, TMP)
    check("a new attribute on the TARGET reads CHANGED, naming it", s2["state"] == "changed" and s2["drift"] == ["new attribute Email"], s2)
    _run(AT.validate(store(kg), name))
    check("…signing still waits for the edited model's own signature (the bottom-up rule)",
          A.sign_action(kg, name, U, db_path=TMP)["reason"] == "unsigned_ingredient")
    so = A.action_state(kg, name, TMP).get("stands_on")
    check("…and the reading says what it stands on, so a surface offers THAT signature",
          so == {"state": "changed", "label": "ontology"}, so)
    sign_base(kg); r3 = A.sign_action(kg, name, U, db_path=TMP)
    check("validate again + sign → trusted over the new model", r3["signed"] and A.action_state(kg, name, TMP)["state"] == "trusted")

    # an action signed before the facts were recorded: its signature covers a report without them
    p = props(kg); legacy = {k: v for k, v in p["validation_report"].items() if k != "model_facts"}
    p["validation_report"] = legacy; set_props(kg, p)
    S.sign(A.ACTION_KIND, A.action_artifact_id(kg, name), A.version_hash_for(p["manifest"], legacy), signer_uuid=U, db_path=TMP)
    s3 = A.action_state(kg, name, TMP)
    check("a report from before the facts were recorded proves nothing about today's model: CHANGED, re-validate",
          s3["state"] == "changed" and s3["drift"] == [AT.UNRECORDED] and s3["stale"] == "unrecorded", s3)
    check("…its refusal says so, not that the model moved", AP._trust_state(kg, name, TMP).startswith("changed — validated before the model facts"))
    check("…and is not signable", A.sign_action(kg, name, U, db_path=TMP)["reason"] == "stale_report")
    # an action on an EXTENSION entity stands on that extension's signature as well as the base's
    from trusted_data_agent.core.trust import extensions as X
    kx = "kg-x"; sx = seed(kx)
    lid = _run(sx.add_entity("Leads", "entity", {"layer": T.AUTHORED_LAYER, "status": T.AUTHORED_STATUS_PUBLISHED, "extension_id": "ext-u1"}))
    sign_base(kx)
    ents_x, _r = C.load_kg_graph(kx, TMP)
    lead_raw = {**copy.deepcopy(RAW), "target": {"entity": "Leads", "deployed": "x.Leads", "key": ["CustomerID"]}}
    st_x, label = A._model_ingredient(kx, lead_raw, ents_x, TMP)
    check("an action on an extension entity: the UNSIGNED extension blocks, named",
          st_x.value == "unsigned" and label.startswith("extension"), (st_x, label))
    xa = X.extension_artifact_id(kx, "ext-u1")
    S.sign(X.EXTENSION_KIND, xa, X.live_hash(xa, TMP), signer_uuid=U, db_path=TMP)
    st_y, _l = A._model_ingredient(kx, lead_raw, ents_x, TMP)
    check("…signed and intact → the action may stand on it", st_y.value == "trusted", st_y)
    st_z, lab_z = A._model_ingredient(kx, RAW, ents_x, TMP)
    check("…an action on a BASE entity reads the base alone", st_z.value == "trusted" and lab_z == "ontology")
    v = __import__("trusted_data_agent.core.trust.validators", fromlist=["x"]).action_type_validator(
        manifest=RAW, stored_report={**rep, "manifest_hash": __import__("trusted_data_agent.dbdeploy.action_model", fromlist=["x"]).manifest_hash(
            __import__("trusted_data_agent.dbdeploy.action_model", fromlist=["x"]).parse_manifest(RAW)), "signable": True},
        ontology_state="changed", ontology_label="extension by ann")
    check("…the refusal names the extension to sign", v.status == "unsigned_ingredient" and "extension by ann is changed" in v.note and "sign the extension by ann first" in v.note, v.note)
    was = {"resolution": {"Customers": "fitness_db.Customers"}, "graph_domains": {}, "entity_columns": ["A"]}
    check("a target that resolves elsewhere is named",
          AT.facts_drift({"facts": was}, {**was, "resolution": {"Customers": "fitness_db.Customers2"}})
          == ["Customers now resolves to fitness_db.Customers2 (was fitness_db.Customers)"])
    check("a removed attribute is named", AT.facts_drift({"facts": was}, {**was, "entity_columns": None}) == ["attribute A removed"])


for fn in (t_vocabulary, t_authoring, t_refusals, t_sign_and_ratchet, t_ontology_hash, t_enrichment, t_model_facts):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc(); check(f"{fn.__name__} raised", False, repr(exc))
try: os.unlink(TMP)
except OSError: pass
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
