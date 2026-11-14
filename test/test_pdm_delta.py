"""
test_pdm_delta — PROP-008 increment A3: the delta deployer.

  §1  the landing rule: owned root ⇒ the model's database; not owned / unknown ⇒ UDERIA_EXT_<root>
  §2  the plan is a pure function of (before, after): a new entity → CREATE under the standard
      (key NOT NULL, UPI from the declared key, bounded COMMENT, statistics), soft RI on the
      attribute that names the parent's key, a new attribute on a base entity → nullable ALTER
      ADD, the type map, every refusal and warning named, determinism
  §3  idempotent: a landed delta re-plans as a no-op; a half-landed delta resumes with only
      what is missing
  §4  a drop is refused without force, and only ever over the graph's own realisations
  §5  the faithful fake backend: a dictionary it mutates, the executed statements it reports;
      a dry run performs no I/O; a failed statement is recorded and the rest still run
  §6  the orchestrator on the REAL GraphStore: drafts → plan in words → landed → physical nodes
      + implements + the authored node deployed with its sealed receipt → a re-run is a no-op
      → a removed node's realisation is refused, then dropped with force
  §7  REST wiring on the AST: the master gate (409 act_off), the owner, the target, the snapshot
  §8  live, under TDA_LIVE_TERADATA=1: a real landing on the local scratch database, readback,
      re-run no-op, cleanup

Run: PYTHONPATH=src python3 test/test_pdm_delta.py
"""
import asyncio
import ast
import json
import os
import re
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
def _run(x): return asyncio.run(x) if asyncio.iscoroutine(x) else x

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.dbdeploy import delta as D
from trusted_data_agent.dbdeploy.backend import DeploymentBackend
from trusted_data_agent.dbdeploy.capabilities import DeploymentCapability, REQUIRED_CAPABILITIES
from trusted_data_agent.dbdeploy.types import DeployResult

# ── fixtures (plain dicts — the planner is pure) ──────────────────────────────
LEAD = {"name": "fitness_db.Lead", "props": {"business_key": "lead_id", "description": "an open line of enquiry on a customer"},
        "attributes": [{"name": "fitness_db.Lead.lead_id", "props": {"data_type": "int"}},
                       {"name": "fitness_db.Lead.status", "props": {"data_type": "VARCHAR(20)"}},
                       {"name": "fitness_db.Lead.owner", "props": {}},
                       {"name": "fitness_db.Lead.CustomerID", "props": {"data_type": "int"}}],
        "parent": "fitness_db.Customers", "deployed": None}
FACTS = {"fitness_db.Customers": {"deployed": "fitness_db.Customers", "key": ["CustomerID"]}}
TIER = {"name": "fitness_db.Customers.loyalty_tier", "props": {"data_type": "string"}, "entity": "fitness_db.Customers"}
DICT_BASE = {"databases": {"fitness_db"}, "tables": {"fitness_db.Customers": {"columns": {"CustomerID": "I", "email": "CV"}, "foreign_keys": []}}}
OWNED = D.landing_for("fitness_db", True)
SIBLING = D.landing_for("fitness_db", False)

def plan(authored=None, facts=None, dictionary=None, landing=OWNED, **kw):
    return D.plan_delta(authored if authored is not None else {"entities": [LEAD], "attributes": [TIER]},
                        facts if facts is not None else FACTS, dictionary if dictionary is not None else DICT_BASE, landing, **kw)
def sqls(p, kind=None): return [c.sql for c in p.pending() if kind is None or c.kind == kind]


print("\n[§1] the landing rule")
def t_landing():
    check("owned ⇒ the model's own database", OWNED.database == "fitness_db" and OWNED.rule == "owned_root")
    check("not owned ⇒ the platform sibling", SIBLING.database == "UDERIA_EXT_fitness_db" and SIBLING.rule == "platform_sibling")
    check("unknown ownership never lands in a database we could not prove is ours", D.landing_for("x", None).database == "UDERIA_EXT_x")
run(t_landing)


print("\n[§2] the plan is a pure function of (before, after)")
def t_plan():
    p = plan()
    tbl = sqls(p, "table")
    check("a new entity becomes one CREATE under the standard", len(tbl) == 1 and tbl[0].startswith('CREATE MULTISET TABLE "fitness_db"."Lead", NO FALLBACK'), tbl)
    check("the key column is NOT NULL, the others nullable", '"lead_id" INTEGER NOT NULL' in tbl[0] and re.search(r'"status" VARCHAR\(20\) CHARACTER SET LATIN,\n', tbl[0]) is not None, tbl[0])
    check("the declared key is the unique primary index, and the rule is named", 'UNIQUE PRIMARY INDEX ( "lead_id" )' in tbl[0] and p.pi_rule == "declared_key")
    check("an undeclared type lands as the standard's VARCHAR, and says so", '"owner" VARCHAR(4000)' in tbl[0] and any("owner: no data type" in w for w in p.warnings), p.warnings)
    fk = sqls(p, "foreign_key")
    check("soft RI on the attribute that names the parent's key", len(fk) == 1 and 'ADD FOREIGN KEY ( "CustomerID" ) REFERENCES WITH NO CHECK OPTION "fitness_db"."Customers" ( "CustomerID" )' in fk[0], fk)
    cm = sqls(p, "comment")
    check("the description is a bounded table COMMENT", len(cm) == 1 and "an open line of enquiry" in cm[0])
    st = sqls(p, "statistics")
    check("statistics on the PI and the FK column", len(st) == 2 and '( "lead_id" )' in st[0] and '( "CustomerID" )' in st[1], st)
    col = sqls(p, "column")
    check("a new attribute on a base entity is a nullable ALTER ADD (existing rows exist)", len(col) == 1 and col[0] == 'ALTER TABLE "fitness_db"."Customers" ADD "loyalty_tier" VARCHAR(4000) CHARACTER SET LATIN;', col)
    check("the plan names what each node realises", p.realises["fitness_db.Lead"] == "fitness_db.Lead" and p.realises["fitness_db.Lead.status"] == "fitness_db.Lead.status" and p.realises[TIER["name"]] == "fitness_db.Customers.loyalty_tier")
    check("statements run in phase order: table, ALTER, comment, FK, statistics", [c.kind for c in p.pending()] == ["table", "column", "comment", "foreign_key", "statistics", "statistics"], [c.kind for c in p.pending()])
    check("the same inputs plan the same statements (determinism)", [c.sql for c in plan().pending()] == [c.sql for c in p.pending()])
    check("the preview reads in words", "1 table in fitness_db (the model's own database)" in p.summary() and "1 column added" in p.summary() and "1 soft-RI reference" in p.summary(), p.summary())
    # the sibling landing
    ps = plan(landing=SIBLING, dictionary={"databases": {"fitness_db"}, "tables": DICT_BASE["tables"]})
    check("not owned ⇒ the table lands in the sibling, the database is provisioned first", sqls(ps, "table")[0].startswith('CREATE MULTISET TABLE "UDERIA_EXT_fitness_db"."Lead"') and ps.requires_database and "will be created first" in ps.summary())
    check("…and the FK still points at the model's own parent table", '"fitness_db"."Customers"' in sqls(ps, "foreign_key")[0])
    # no key
    nokey = dict(LEAD, props={"description": "x"}); pn = plan({"entities": [nokey], "attributes": []})
    check("no declared key ⇒ NO PRIMARY INDEX, named", "NO PRIMARY INDEX" in sqls(pn, "table")[0] and any("NO PRIMARY INDEX" in w for w in pn.warnings) and pn.pi_rule == "no_key")
    # no attribute names the parent's key
    nofk = dict(LEAD, attributes=LEAD["attributes"][:3]); pf = plan({"entities": [nofk], "attributes": []})
    check("no attribute names the parent's key ⇒ no RI, and the warning says which key", not sqls(pf, "foreign_key") and any("names the parent's key (CustomerID)" in w for w in pf.warnings), pf.warnings)
    pu = plan({"entities": [LEAD], "attributes": []}, facts={"fitness_db.Customers": {"deployed": "", "key": ["CustomerID"]}})
    check("a parent that is not deployed ⇒ no RI, named", not sqls(pu, "foreign_key") and any("is not deployed" in w for w in pu.warnings))
    pb = plan({"entities": [], "attributes": [dict(TIER, entity="fitness_db.Nowhere")]}, facts={"fitness_db.Nowhere": {"deployed": "", "key": []}})
    check("a column on an undeployed base entity is refused, with the reason", pb.is_noop and pb.refused() and "is not deployed" in pb.refused()[0].reason)
    pm = plan({"entities": [], "attributes": [TIER]}, dictionary={"databases": {"fitness_db"}, "tables": {}})
    check("a column on a base table the dictionary lacks is refused (never a CREATE of a base table)", pm.is_noop and "not on the target's dictionary" in pm.refused()[0].reason)
    bad = {"name": "fitness_db.Bad name", "props": {}, "attributes": [{"name": "fitness_db.Bad name.x", "props": {}}], "parent": None, "deployed": None}
    pbad = plan({"entities": [bad], "attributes": []})
    check("an identifier that is not plain is never emitted", pbad.is_noop and any("not a plain identifier" in w for w in pbad.warnings))
    check("a table needs at least one column", plan({"entities": [dict(LEAD, attributes=[])], "attributes": []}).is_noop)
    tm = plan({"entities": [dict(LEAD, attributes=[{"name": "fitness_db.Lead.lead_id", "props": {"data_type": "bigint"}},
                                                     {"name": "fitness_db.Lead.when", "props": {"data_type": "timestamp"}},
                                                     {"name": "fitness_db.Lead.ok", "props": {"data_type": "boolean"}}])], "attributes": []})
    t = sqls(tm, "table")[0]
    check("the type map is the standard's", '"lead_id" BIGINT NOT NULL' in t and '"when" TIMESTAMP(6) WITH TIME ZONE' in t and '"ok" BYTEINT' in t, t)
run(t_plan)


print("\n[§3] idempotent: a landed delta is a no-op; a half-landed one resumes")
def t_idempotent():
    landed = {"databases": {"fitness_db"}, "tables": {
        "fitness_db.Customers": {"columns": {"CustomerID": "I", "email": "CV", "loyalty_tier": "CV"}, "foreign_keys": []},
        "fitness_db.Lead": {"columns": {"lead_id": "I", "status": "CV", "owner": "CV", "CustomerID": "I"},
                            "foreign_keys": [("CustomerID", "fitness_db.Customers", "CustomerID")]}}}
    p = plan(dictionary=landed)
    check("every realisation exists ⇒ nothing pending", p.is_noop and not p.refused(), [c.to_dict() for c in p.pending()])
    check("…and the summary says so", "nothing to land" in p.summary() and "already exists" in p.summary(), p.summary())
    check("…the existing realisations are still named (for the stamping)", {c.identifier for c in p.existing()} >= {"fitness_db.Lead", "fitness_db.Lead.status", "fitness_db.Customers.loyalty_tier", "fitness_db.Lead.CustomerID"})
    half = json.loads(json.dumps({"databases": ["fitness_db"], "tables": landed["tables"]}))
    half["tables"]["fitness_db.Lead"]["columns"].pop("owner"); half["tables"]["fitness_db.Lead"]["foreign_keys"] = []
    half["tables"]["fitness_db.Customers"]["columns"].pop("loyalty_tier")
    ph = plan(dictionary=half)
    kinds = [(c.kind, c.identifier) for c in ph.pending()]
    check("a half-landed delta resumes with exactly what is missing: one ADD, the RI, the base column",
          kinds == [("column", "fitness_db.Lead.owner"), ("column", "fitness_db.Customers.loyalty_tier"), ("foreign_key", "fitness_db.Lead.CustomerID")], kinds)
    check("…no CREATE, no statistics on a table it did not create", not sqls(ph, "table") and not sqls(ph, "statistics"))
    check("a landed entity keeps its identifier on a re-plan (the realisation, not the landing)",
          plan({"entities": [dict(LEAD, deployed="UDERIA_EXT_fitness_db.Lead")], "attributes": []},
               dictionary={"databases": {"fitness_db", "UDERIA_EXT_fitness_db"}, "tables": {**DICT_BASE["tables"], "UDERIA_EXT_fitness_db.Lead": {"columns": {"lead_id": "I"}, "foreign_keys": []}}}
               ).pending()[0].identifier == "UDERIA_EXT_fitness_db.Lead.status")
    check("the dictionary is matched case-insensitively (Teradata folds)", plan(dictionary={"databases": {"FITNESS_DB"}, "tables": {"FITNESS_DB.LEAD": {"columns": {"LEAD_ID": "I", "STATUS": "CV", "OWNER": "CV", "CUSTOMERID": "I"}, "foreign_keys": []}, "fitness_db.customers": {"columns": {"customerid": "I", "LOYALTY_TIER": "CV"}, "foreign_keys": []}}}).pending()[0].kind == "foreign_key")
run(t_idempotent)


print("\n[§4] a drop is refused without force, over the graph's own realisations only")
def t_drops():
    rm = [{"kind": "table", "identifier": "fitness_db.Old", "node": "fitness_db.Old"},
          {"kind": "column", "identifier": "fitness_db.Customers.gone", "node": "fitness_db.Customers.gone"}]
    p = plan({"entities": [], "attributes": []}, removed=rm)
    check("without force: both refused, the reason names force_drop", p.is_noop and len(p.refused()) == 2 and all("force_drop" in c.reason for c in p.refused()))
    pf = plan({"entities": [], "attributes": []}, removed=rm, force_drop=True)
    check("with force: DROP TABLE and ALTER … DROP, last in order", sqls(pf) == ['DROP TABLE "fitness_db"."Old";', 'ALTER TABLE "fitness_db"."Customers" DROP "gone";'] and all(c.phase == D.PHASE_DROP for c in pf.pending()), sqls(pf))
    check("a drop never comes from anything but the removed list", not any(c.kind.startswith("drop_") for c in plan().pending()))
run(t_drops)


# ── the faithful fake: a dictionary it MUTATES, the statements it reports ─────
class FakeTD(DeploymentBackend):
    """Holds a dictionary {db: {table: {columns, fks}}}, mutates it as DDL runs, reports
    every executed statement, and refuses a configured identifier."""
    def __init__(self, dbs=None, owned=None, fail_on=()):
        self.dbs = {k: {t: {"columns": dict(v.get("columns") or {}), "foreign_keys": list(v.get("foreign_keys") or [])} for t, v in tabs.items()} for k, tabs in (dbs or {}).items()}
        self.owned = dict(owned or {}); self.fail_on = set(fail_on); self.sql = []; self.ensured = []
    @property
    def backend_type(self): return "fake_td"
    @property
    def dialect(self): return "teradata"   # a faithful fake of the Teradata dictionary speaks Teradata (C3)
    def capabilities(self): return set(REQUIRED_CAPABILITIES) | {DeploymentCapability.CREATE_DATABASE}
    async def database_owned(self, name): return self.owned.get(name)
    async def database_exists(self, name): return name in self.dbs
    async def ensure_database(self, name, *, parent=None, perm_bytes=None):
        self.ensured.append(name)
        if name in self.dbs: return False
        self.dbs[name] = {}; return True
    async def describe_tables(self, database, tables=None):
        return {f"{database}.{t}": v for t, v in self.dbs.get(database, {}).items() if not tables or t in tables}
    async def execute_ddl(self, plan, *, dry_run=False, stop_on_error=True, include_statistics=True, replace_existing=False):
        r = DeployResult(dry_run=dry_run, generated_sql=plan.sql_script())
        if dry_run:
            r.planned = [f"{s.kind}:{s.identifier}" for s in plan.ordered()]; r.success = True; return r
        for st in plan.ordered():
            if st.identifier in self.fail_on:
                r.errors.append((st.identifier, "[Error 3524] The user does not have CREATE TABLE access"))
                if stop_on_error: r.success = False; return r      # Teradata's path: stop (and clean up)
                continue
            mt = re.search(r'"([^"]+)"\."([^"]+)"', st.sql)
            if mt and not st.sql.startswith("CREATE") and mt.group(2) not in self.dbs.get(mt.group(1), {}):
                r.errors.append((st.identifier, f"[Error 3807] Object '{mt.group(1)}.{mt.group(2)}' does not exist."))
                if stop_on_error: r.success = False; return r
                continue
            self.sql.append(st.sql)
            m = re.match(r'CREATE MULTISET TABLE "([^"]+)"\."([^"]+)"', st.sql)
            if m:
                cols = dict(re.findall(r'\n    "([^"]+)" ([A-Z]+)', st.sql))
                self.dbs.setdefault(m.group(1), {})[m.group(2)] = {"columns": cols, "foreign_keys": []}
            m = re.match(r'ALTER TABLE "([^"]+)"\."([^"]+)" ADD "([^"]+)" ([A-Z]+)', st.sql)
            if m: self.dbs[m.group(1)][m.group(2)]["columns"][m.group(3)] = m.group(4)
            m = re.match(r'ALTER TABLE "([^"]+)"\."([^"]+)" ADD FOREIGN KEY \( "([^"]+)" \) REFERENCES (?:WITH NO CHECK OPTION )?"([^"]+)"\."([^"]+)" \( "([^"]+)" \)', st.sql)
            if m: self.dbs[m.group(1)][m.group(2)]["foreign_keys"].append((m.group(3), f"{m.group(4)}.{m.group(5)}", m.group(6)))
            m = re.match(r'DROP TABLE "([^"]+)"\."([^"]+)"', st.sql)
            if m: self.dbs.get(m.group(1), {}).pop(m.group(2), None)
            m = re.match(r'ALTER TABLE "([^"]+)"\."([^"]+)" DROP "([^"]+)"', st.sql)
            if m: self.dbs[m.group(1)][m.group(2)]["columns"].pop(m.group(3), None)
            r.executed.append((st.kind, st.identifier))
        r.success = not r.errors
        return r


print("\n[§5] the faithful fake backend and execute_delta")
def t_fake():
    be = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER", "email": "VARCHAR"}}}}, owned={"fitness_db": True})
    p = plan()
    res = _run(be.execute_delta(p, dry_run=True))
    check("a dry run performs no I/O and reports nothing executed", res.dry_run and res.success and not be.sql and not res.landed)
    res = _run(be.execute_delta(p))
    check("every pending statement ran, in order, and is reported", [k for k, _ in res.landed] == [c.kind for c in p.pending()] and len(be.sql) == len(p.pending()) and res.executed_sql == be.sql)
    check("the dictionary now holds the table, its columns and its RI", "Lead" in be.dbs["fitness_db"] and set(be.dbs["fitness_db"]["Lead"]["columns"]) == {"lead_id", "status", "owner", "CustomerID"} and be.dbs["fitness_db"]["Lead"]["foreign_keys"] == [("CustomerID", "fitness_db.Customers", "CustomerID")])
    check("…and the base table its new column", "loyalty_tier" in be.dbs["fitness_db"]["Customers"]["columns"])
    p2 = D.plan_delta({"entities": [LEAD], "attributes": [TIER]}, FACTS, {"databases": set(be.dbs), "tables": _run(be.describe_tables("fitness_db"))}, OWNED)
    check("re-planned against the mutated dictionary: a no-op", p2.is_noop)
    bad = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER"}}}}, owned={"fitness_db": True}, fail_on={"fitness_db.Lead"})
    res = _run(bad.execute_delta(plan()))
    check("a failed statement is recorded and the rest still run (resumable, never cleaned up)", not res.success and res.errors and res.errors[0][0] == "fitness_db.Lead" and ("column", "fitness_db.Customers.loyalty_tier") in res.landed, res.to_dict())
run(t_fake)


# ── the real GraphStore ───────────────────────────────────────────────────────
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.kgstore import factory as _factory
from components.builtin.knowledge_graph.graph_store import GraphStore
from trusted_data_agent.auth.database import _migrate_to_multi_kg
_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP); _c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read()); _migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP; APP_CONFIG.KG_SEMANTIC_SEEDING = False
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})
U, P, KG = "u1", "p1", "kg_a3"
ST = GraphStore(P, U, kg_id=KG, db_path=TMP)
ST.set_kg_metadata(KG, "a3", "delta fixture", is_active=True)
_run(ST.import_bulk(
    [{"name": "fitness_db", "entity_type": "database", "properties": {"layer": "logical"}},
     {"name": "fitness_db.Customers", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "CustomerID"}},
     {"name": "fitness_db.Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
     {"name": "fitness_db", "entity_type": "schema", "properties": {"layer": "physical", "infrastructure": "teradata"}},
     {"name": "fitness_db.Customers", "entity_type": "table", "properties": {"layer": "physical", "infrastructure": "teradata"}},
     {"name": "fitness_db.Customers.CustomerID", "entity_type": "column", "properties": {"layer": "physical", "infrastructure": "teradata"}}],
    [{"source_name": "fitness_db", "source_type": "database", "target_name": "fitness_db.Customers", "target_type": "entity", "relationship_type": "contains"},
     {"source_name": "fitness_db.Customers", "source_type": "entity", "target_name": "fitness_db.Customers.CustomerID", "target_type": "attribute", "relationship_type": "contains"},
     {"source_name": "fitness_db", "source_type": "schema", "target_name": "fitness_db.Customers", "target_type": "table", "relationship_type": "contains"},
     {"source_name": "fitness_db.Customers", "source_type": "table", "target_name": "fitness_db.Customers.CustomerID", "target_type": "column", "relationship_type": "contains"},
     {"source_name": "fitness_db.Customers", "source_type": "table", "target_name": "fitness_db.Customers", "target_type": "entity", "relationship_type": "implements"},
     {"source_name": "fitness_db.Customers.CustomerID", "source_type": "column", "target_name": "fitness_db.Customers.CustomerID", "target_type": "attribute", "relationship_type": "implements"}]))
from trusted_data_agent.agent.kg_constructors import authored_deploy as AD
def with_switch(value, fn):
    old = _OP._FORCED["act"]; _OP.force_act(value)
    try: return fn()
    finally: _OP.force_act(old)
def ent(name, etype=None): return ST.get_entity_by_name(name, etype)
def rels_of(name, rt=None):
    e = ST.get_entity_by_name(name)
    return [r for r in _run(ST.list_relationships(entity_id=e["id"], limit=999)) if rt is None or r["relationship_type"] == rt] if e else []

def draft_lead():
    """The A2 shapes, written as propose_entity writes them."""
    cust = ent("fitness_db.Customers", "entity")
    did = _run(ST.add_entity(name="decision.dec_a3", entity_type="design_decision", properties={"layer": "authored", "decision_id": "dec_a3", "rule": "declared"}, source="authored"))
    lid = _run(ST.add_entity(name="fitness_db.Lead", entity_type="entity", properties={"layer": "authored", "status": "draft", "authored_by": "u1", "decision_id": "dec_a3", "business_key": "lead_id", "description": "an open line of enquiry"}, source="authored", source_detail="u1"))
    _run(ST.add_relationship(did, lid, "relates_to", source="authored"))
    for a, t in (("lead_id", "int"), ("status", "VARCHAR(20)"), ("CustomerID", "int")):
        aid = _run(ST.add_entity(name=f"fitness_db.Lead.{a}", entity_type="attribute", properties={"layer": "authored", "status": "draft", "data_type": t}, source="authored"))
        _run(ST.add_relationship(lid, aid, "contains", source="authored"))
    _run(ST.add_relationship(lid, cust["id"], "relates_to", metadata={"kind": "belongs_to"}, source="authored"))
    tid = _run(ST.add_entity(name="fitness_db.Customers.loyalty_tier", entity_type="attribute", properties={"layer": "authored", "status": "draft", "data_type": "string"}, source="authored"))
    _run(ST.add_relationship(cust["id"], tid, "contains", source="authored"))
with_switch(True, draft_lead)


print("\n[§6] the orchestrator on the real graph")
def t_orchestrator():
    a = _run(AD.read_authored(ST))
    check("the authored layer is read as the planner's input: one entity with its attributes and parent, one base attribute",
          len(a["entities"]) == 1 and a["entities"][0]["parent"] == "fitness_db.Customers" and sorted(x["name"].split(".")[-1] for x in a["entities"][0]["attributes"]) == ["CustomerID", "lead_id", "status"]
          and len(a["attributes"]) == 1 and a["attributes"][0]["entity"] == "fitness_db.Customers", a)
    facts = _run(AD.gather_facts(ST, a["base_entities"]))
    check("the base entity's realisation is read through the resolver, its key through the one reader", facts["fitness_db.Customers"] == {"deployed": "fitness_db.Customers", "key": ["CustomerID"]}, facts)
    be = FakeTD({"fitness_db": {"Customers": {"columns": {"CustomerID": "INTEGER"}}}}, owned={"fitness_db": False})
    dry = with_switch(True, lambda: _run(AD.deploy_authored(ST, be, actor="u1", dry_run=True)))
    check("a dry run plans, says where and why in words, and runs nothing", dry["status"] == "dry_run" and dry["landing"]["database"] == "UDERIA_EXT_fitness_db" and "platform-owned sibling" in dry["summary"] and not be.sql and not be.ensured, dry.get("summary"))
    # A4: a preview IS the validation act — the node moves to validated (never to deployed)
    _lead = ent("fitness_db.Lead", "entity")["properties"]
    check("…and a dry run VALIDATES the node against the design standard, never deploys it",
          _lead.get("status") == "validated" and _lead.get("validated_at") and not _lead.get("deployed_at") and dry.get("validated") == ["fitness_db.Lead"], _lead)
    info = with_switch(True, lambda: _run(AD.deploy_authored(ST, be, actor="u1")))
    check("the landing database was provisioned first, then everything landed", info["status"] == "success" and be.ensured == ["UDERIA_EXT_fitness_db"] and "Lead" in be.dbs["UDERIA_EXT_fitness_db"], info.get("result"))
    lead = ent("fitness_db.Lead", "entity"); pr = lead["properties"]
    check("the authored entity is deployed, with its realisation", pr.get("status") == "deployed" and pr.get("deployed_table") == "UDERIA_EXT_fitness_db.Lead", pr)
    rc = pr.get("deploy_receipt") or {}
    check("…and carries its receipt: id, landing, who, sealed-or-not", rc.get("receipt_id", "").startswith("mc_") and rc.get("landing") == "UDERIA_EXT_fitness_db" and rc.get("by") == "u1" and "signed" in rc, rc)
    check("the full receipt is a closed canonical content and the attestation rides beside it",
          info["receipt"]["kind"] == "model_change" and D.receipt_content(info["receipt"]) == D.receipt_content({**info["receipt"], "attestation": "x"}) and "attestation" in info["receipt"])
    check("the receipt names every statement by kind, identifier and hash", all(set(s) == {"kind", "identifier", "node", "sql_hash"} for s in info["receipt"]["statements"]) and len(info["receipt"]["statements"]) == len(be.sql))
    pt = ent("UDERIA_EXT_fitness_db.Lead", "table")
    check("a physical table node exists for the landing, stamped as the deploy's own", pt and pt["properties"].get("layer") == "physical" and pt["properties"].get(AD.DEPLOY_SOURCE) is True and pt["properties"].get("realises") == "fitness_db.Lead", pt)
    check("…implementing the authored entity", any(r["relationship_type"] == "implements" and r["target_name"] == "fitness_db.Lead" for r in rels_of("UDERIA_EXT_fitness_db.Lead")))
    check("…contained by the landing's schema node", ent("UDERIA_EXT_fitness_db", "schema") is not None and any(r["relationship_type"] == "contains" and r["source_name"] == "UDERIA_EXT_fitness_db" for r in rels_of("UDERIA_EXT_fitness_db.Lead")))
    pc = ent("UDERIA_EXT_fitness_db.Lead.status", "column")
    check("a physical column per attribute, implementing it; the attribute deployed", pc and any(r["relationship_type"] == "implements" and r["target_name"] == "fitness_db.Lead.status" for r in rels_of("UDERIA_EXT_fitness_db.Lead.status")) and ent("fitness_db.Lead.status", "attribute")["properties"].get("deployed_column") == "UDERIA_EXT_fitness_db.Lead.status")
    check("the soft RI is a foreign_key edge between the physical columns", any(r["relationship_type"] == "foreign_key" and r["target_name"] == "fitness_db.Customers.CustomerID" for r in rels_of("UDERIA_EXT_fitness_db.Lead.CustomerID")))
    bc = ent("fitness_db.Customers.loyalty_tier", "column")
    check("the base column landed under the EXISTING physical table and its attribute is deployed", bc and any(r["relationship_type"] == "contains" and r["source_name"] == "fitness_db.Customers" for r in rels_of("fitness_db.Customers.loyalty_tier")) and ent("fitness_db.Customers.loyalty_tier", "attribute")["properties"].get("status") == "deployed")
    n_sql = len(be.sql)
    again = with_switch(True, lambda: _run(AD.deploy_authored(ST, be, actor="u1")))
    check("a re-run is a no-op: nothing planned, nothing executed, the landing unchanged", again["status"] == "noop" and len(be.sql) == n_sql and again["landing"]["database"] == "UDERIA_EXT_fitness_db", again.get("summary"))
    # the authored node is removed → its realisation is a drop candidate
    _run(ST.delete_entity(ent("fitness_db.Lead", "entity")["id"]))
    a2 = _run(AD.read_authored(ST))
    check("a realisation whose authored node is gone is read as removed — the table (its columns go with it, never listed twice)", [(r["kind"], r["identifier"]) for r in a2["removed"]] == [("table", "UDERIA_EXT_fitness_db.Lead")], a2["removed"])
    ref = with_switch(True, lambda: _run(AD.deploy_authored(ST, be, actor="u1")))
    check("…refused without force, naming it", ref["status"] == "noop" and ref["plan"]["refused"] and "force_drop" in ref["plan"]["refused"][0]["reason"] and len(be.sql) == n_sql)
    forced = with_switch(True, lambda: _run(AD.deploy_authored(ST, be, actor="u1", force_drop=True)))
    check("…dropped with force, and the physical nodes go with it (the table and its columns)", forced["status"] == "success" and "Lead" not in be.dbs["UDERIA_EXT_fitness_db"] and ent("UDERIA_EXT_fitness_db.Lead", "table") is None and ent("UDERIA_EXT_fitness_db.Lead.status", "column") is None, forced.get("result"))
run(t_orchestrator)


print("\n[§7] REST wiring on the AST")
def t_rest():
    src = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "kg_authored_deploy"), None)
    check("the endpoint exists", fn is not None)
    body = ast.get_source_segment(src, fn) if fn else ""
    check("it is routed and auth-gated", '/v1/knowledge-graph/authored/deploy' in src and any("require_auth" in ast.get_source_segment(src, d) for d in (fn.decorator_list if fn else [])))
    check("the OBDA-MODEL plane gates it (the component's availability, one assert)", "_assert_model_enabled()" in body)
    check("the graph is the caller's own, the target a resolved Database Connection", "_kg_owner_profile_for_id(user_uuid, kg_id)" in body and "_resolve_deployment_target(current_user, user_uuid, target_id)" in body)
    i_snap, i_dep = body.find("snapshot_kg(kg_id"), body.find("deploy_authored(store, backend")
    check("a snapshot precedes a landing, never a dry run", 0 < i_snap < i_dep and "if not dry_run:" in body[:i_snap])
    check("the request never chooses the landing database (provisioning knobs are not a landing)",
          'data.get("root_database")' not in body and 'data.get("landing' not in body and 'data.get("database_name")' not in body
          and 'data.get("target_database")' not in body and "sibling_parent=" in body)
run(t_rest)


print("\n[§8] live Teradata (TDA_LIVE_TERADATA=1)")
if os.environ.get("TDA_LIVE_TERADATA", "").strip().lower() not in ("1", "true", "yes", "on"):
    print("  skipped — set TDA_LIVE_TERADATA=1 to land a real table on the local scratch database")
else:
    def t_live():
        """Two real landings on the shared local Teradata, each in a database this test
        creates and drops (20 MB — the shared scratch database agent_test is full and is
        another graph's physical layer; it is never touched): the OWNED root, and the
        platform SIBLING of a root that does not exist."""
        import uuid
        sys.path.insert(0, os.path.join(ROOT, "agent_packs/policing-data-products/scripts"))
        import tdrun  # noqa: E402
        from trusted_data_agent.dbdeploy import TeradataDeploymentBackend
        c = tdrun.creds()
        be = TeradataDeploymentBackend({"host": c["host"], "username": c["user"], "password": c["password"],
                                        "database_parent": "dbc", "database_perm_bytes": 20_000_000})
        tag = uuid.uuid4().hex[:6]
        root, nowhere = f"A3ROOT_{tag}", f"A3NOWHERE_{tag}"
        parent, child = "A3Parent", "A3Lead"
        _run(be.initialize())
        made = []
        try:
            check("a tiny root database is provisioned for the test (idempotent ensure)", _run(be.ensure_database(root)) is True); made.append(root)
            _run(be._execute_sql(f'CREATE MULTISET TABLE "{root}"."{parent}", NO FALLBACK ("pid" INTEGER NOT NULL, "name" VARCHAR(40) CHARACTER SET LATIN) UNIQUE PRIMARY INDEX ("pid");'))
            owned = _run(be.database_owned(root))
            check("the deploy login owns the root it created (the dictionary says so)", owned is True, owned)
            check("…and a database that does not exist is not owned", _run(be.database_owned(nowhere)) is False)
            def graph(kg, rootdb, with_parent):
                S = GraphStore(P, U, kg_id=kg, db_path=TMP); S.set_kg_metadata(kg, kg, "a3 live", is_active=False)
                ents = [{"name": rootdb, "entity_type": "database", "properties": {"layer": "logical"}}]
                rels = []
                if with_parent:
                    ents.append({"name": f"{rootdb}.{parent}", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "pid", "deployed_table": f"{rootdb}.{parent}"}})
                    rels.append({"source_name": rootdb, "source_type": "database", "target_name": f"{rootdb}.{parent}", "target_type": "entity", "relationship_type": "contains"})
                _run(S.import_bulk(ents, rels))
                def mk():
                    lid = _run(S.add_entity(name=f"{rootdb}.{child}", entity_type="entity", properties={"layer": "authored", "status": "draft", "business_key": "lead_id", "description": "a live A3 landing"}, source="authored"))
                    for a, t in (("lead_id", "int"), ("status", "VARCHAR(20)"), ("pid", "int")):
                        aid = _run(S.add_entity(name=f"{rootdb}.{child}.{a}", entity_type="attribute", properties={"layer": "authored", "status": "draft", "data_type": t}, source="authored"))
                        _run(S.add_relationship(lid, aid, "contains", source="authored"))
                    if with_parent:
                        par = S.get_entity_by_name(f"{rootdb}.{parent}", "entity")
                        _run(S.add_relationship(lid, par["id"], "relates_to", metadata={"kind": "belongs_to"}, source="authored"))
                with_switch(True, mk)
                return S
            # 1. the owned root
            S1 = graph(f"kg_live_root_{tag}", root, True)
            info = with_switch(True, lambda: _run(AD.deploy_authored(S1, be, actor="live")))
            check("the authored entity landed in the model's own database", info["status"] == "success" and info["landing"]["rule"] == "owned_root" and info["landing"]["database"] == root and ("table", f"{root}.{child}") in [tuple(x) for x in info["result"]["landed"]], info.get("result"))
            rb = _run(be.describe_tables(root, [child])); t = rb.get(f"{root}.{child}") or {}
            check("readback: the table, its three columns and the soft RI are in the dictionary", {c.lower() for c in t.get("columns", {})} == {"lead_id", "status", "pid"} and any(fk[0].lower() == "pid" and fk[1].lower() == f"{root}.{parent}".lower() for fk in t.get("foreign_keys", [])), t)
            check("the receipt is sealed with the provenance key", bool((info["receipt"].get("attestation") or {}).get("signed")), info["receipt"].get("attestation"))
            again = with_switch(True, lambda: _run(AD.deploy_authored(S1, be, actor="live")))
            check("a re-run against the live dictionary is a no-op", again["status"] == "noop", again.get("summary"))
            # 2. a root nobody owns → the platform sibling, provisioned first
            S2 = graph(f"kg_live_sib_{tag}", nowhere, False)
            info2 = with_switch(True, lambda: _run(AD.deploy_authored(S2, be, actor="live", sibling_parent="dbc", sibling_perm_bytes=20_000_000)))
            sib = f"UDERIA_EXT_{nowhere}"; made.append(sib)
            check("a root the login does not own lands in the platform sibling, created first", info2["status"] == "success" and info2["landing"]["rule"] == "platform_sibling" and info2.get("database_created") is True and _run(be.database_exists(sib)), info2.get("landing"))
            rb2 = _run(be.describe_tables(sib, [child]))
            check("readback on the sibling: the table is there, with no RI (there was no parent)", f"{sib}.{child}" in rb2 and not rb2[f"{sib}.{child}"]["foreign_keys"], rb2)
        finally:
            for db in made:
                r = _run(be.drop_database(db))
                print("  cleanup:", db, "dropped" if r.success else r.errors)
    run(t_live)

try: os.remove(TMP)
except Exception: pass
print(f"\n=== PDM delta (A3) results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
