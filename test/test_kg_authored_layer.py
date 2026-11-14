"""PROP-008 increment A1 — the authored layer survives a resync; the base layer is what is read-only.

Real SQLite (temp DB, real schema + real migration), the real GraphStore, the real sqlite
backend; no doubles for the thing under test. CI-pure.

  §1  the layer vocabulary is declared once (authored + observed are the kept layers)
  §2  replace_model_layer keeps the authored (and observed) layer, replaces the walked one,
      re-links a kept edge to the re-walked node by NAME, and NAMES a kept edge whose
      other end the new walk dropped
  §3  idempotent: replacing with the same walk twice changes nothing
  §4  clear_graph is a delete — it deletes the authored layer too
  §5  a subscribed graph: master OFF ⇒ every write refused (the pre-A1 rule, verbatim);
      master ON ⇒ authored-layer writes allowed, base-layer writes refused, whole-graph
      writes refused, the sync flag still owns the one legitimate whole-graph write AND
      a sync keeps the subscriber's authored layer
  §6  a node's layer is immutable through update_entity (re-layering is the hidden mutation)
  §7  the replace callers are wired through ONE operation; the delete callers still delete
  §8  a stray layer value is refused on an explicit write

Run: PYTHONPATH=src python3 test/test_kg_authored_layer.py
"""
import asyncio
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
def refused(fn):
    try: fn(); return False
    except KnowledgeGraphReadOnlyError: return True
def _run(coro): return asyncio.run(coro)

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.kgstore import factory as _factory
from trusted_data_agent.kgstore import types as T
from components.builtin.knowledge_graph.graph_store import GraphStore, KnowledgeGraphReadOnlyError
from trusted_data_agent.auth.database import _migrate_to_multi_kg

_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})

U, P = "u1", "p1"
def store(kg, **kw): return GraphStore(P, U, kg_id=kg, db_path=TMP, **kw)
def with_switch(value, fn):
    old = _OP._FORCED["act"]; _OP.force_act(value)
    try: return fn()
    finally: _OP.force_act(old)
def mark_subscribed(kg):
    c = sqlite3.connect(TMP)
    cols = {r[1] for r in c.execute("PRAGMA table_info(kg_metadata)")}
    if "is_subscribed" not in cols:
        c.execute("ALTER TABLE kg_metadata ADD COLUMN is_subscribed INTEGER NOT NULL DEFAULT 0")
    c.execute("UPDATE kg_metadata SET is_subscribed=1 WHERE kg_id=?", (kg,)); c.commit(); c.close()
def names(st): return sorted(e["name"] for e in _run(st.list_entities(limit=9999)))
def edges(st): return sorted((r["source_name"], r["target_name"], r["relationship_type"]) for r in _run(st.list_relationships(limit=9999)))

WALK1 = [{"name": "fitness_db.Customers", "entity_type": "entity", "properties": {"layer": "logical", "description": "v1"}},
         {"name": "fitness_db.Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
         {"name": "fitness_db.Sales", "entity_type": "entity", "properties": {"layer": "logical"}}]
RELS1 = [{"source_name": "fitness_db.Customers", "target_name": "fitness_db.Customers.CustomerID", "relationship_type": "contains"},
         {"source_name": "fitness_db.Sales", "target_name": "fitness_db.Customers", "relationship_type": "foreign_key"}]
WALK2 = [{"name": "fitness_db.Customers", "entity_type": "entity", "properties": {"layer": "logical", "description": "v2"}},
         {"name": "fitness_db.Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
         {"name": "fitness_db.Orders", "entity_type": "entity", "properties": {"layer": "logical"}}]
RELS2 = [{"source_name": "fitness_db.Customers", "target_name": "fitness_db.Customers.CustomerID", "relationship_type": "contains"},
         {"source_name": "fitness_db.Orders", "target_name": "fitness_db.Customers", "relationship_type": "foreign_key"}]

def seed_with_authored(kg):
    st = store(kg); st.set_kg_metadata("Fitness", is_active=False); st.import_bulk(WALK1, RELS1)
    lead = _run(st.add_entity("Lead", "entity", {"layer": T.AUTHORED_LAYER, "extension_id": "ext-u1"}))
    obs = _run(st.add_entity("obs: customers churn in Q4", "business_concept", {"layer": T.OBSERVED_LAYER}))
    cust = st.get_entity_by_name("fitness_db.Customers")["id"]
    sales = st.get_entity_by_name("fitness_db.Sales")["id"]
    _run(st.add_relationship(lead, cust, "relates_to"))    # authored → base (kept + re-linked)
    _run(st.add_relationship(lead, sales, "relates_to"))   # authored → a base node the next walk drops
    _run(st.add_relationship(obs, cust, "relates_to"))     # observed → base
    return st, lead, obs

def t_vocabulary():
    print("[§1] the layer vocabulary")
    check("four layers declared once", T.LAYERS == {"logical", "physical", "authored", "observed"})
    check("the kept layers are authored + observed", T.KEPT_LAYERS_ON_REPLACE == {"authored", "observed"})
    check("logical/physical are not kept (they ARE the walked model)", not (T.KEPT_LAYERS_ON_REPLACE & {"logical", "physical"}))

def t_replace_keeps_authored():
    print("[§2] replace keeps the authored layer, replaces the walked one")
    st, lead, obs = seed_with_authored("kg-r")
    before_cust = st.get_entity_by_name("fitness_db.Customers")["id"]
    res = st.replace_model_layer(WALK2, RELS2)
    check("authored node survives with its id", st.get_entity(lead) is not None and st.get_entity(lead)["properties"].get("layer") == "authored")
    check("observed node survives too (the one operation serves both layers)", st.get_entity(obs) is not None)
    check("the walked layer is the NEW walk", names(st) == sorted(["Lead", "obs: customers churn in Q4", "fitness_db.Customers", "fitness_db.Customers.CustomerID", "fitness_db.Orders"]), str(names(st)))
    check("the re-walked node carries the new content", st.get_entity_by_name("fitness_db.Customers")["properties"].get("description") == "v2")
    e = edges(st)
    check("the authored → base edge is re-linked by name", ("Lead", "fitness_db.Customers", "relates_to") in e, str(e))
    check("the observed → base edge is re-linked too", ("obs: customers churn in Q4", "fitness_db.Customers", "relates_to") in e)
    check("the walked edges are the new walk's", ("fitness_db.Orders", "fitness_db.Customers", "foreign_key") in e and ("fitness_db.Sales", "fitness_db.Customers", "foreign_key") not in e)
    check("a kept edge whose other end was dropped is gone — and NAMED", ("Lead", "fitness_db.Sales", "relates_to") not in e
          and any("fitness_db.Sales" in str(d) for d in res.get("kept_edges_dropped", [])), str(res))
    check("the result reports what happened", res.get("entities_kept") == 2 and res.get("kept_edges_relinked") == 2
          and res.get("entities_deleted") == 3, str(res))
    check("the re-linked edge targets the CURRENT node id (not the deleted one)",
          all(r["target_entity_id"] != before_cust for r in _run(st.list_relationships(limit=999)) if r["source_name"] == "Lead") or before_cust == st.get_entity_by_name("fitness_db.Customers")["id"])

def t_idempotent():
    print("[§3] idempotent")
    st, lead, obs = seed_with_authored("kg-i")
    st.replace_model_layer(WALK1, RELS1); n1, e1 = names(st), edges(st)
    st.replace_model_layer(WALK1, RELS1); n2, e2 = names(st), edges(st)
    check("same walk twice ⇒ same names, same edges", n1 == n2 and e1 == e2, f"{n1} vs {n2}")
    check("authored still there after both", st.get_entity(lead) is not None)

def t_clear_is_a_delete():
    print("[§4] clear_graph deletes everything, authored included")
    st, lead, obs = seed_with_authored("kg-c")
    st.clear_graph()
    check("nothing left", names(st) == [] and st.get_entity(lead) is None)

def t_subscribed():
    print("[§5] a subscribed graph")
    st, lead, obs = seed_with_authored("kg-s"); mark_subscribed("kg-s")
    cust = st.get_entity_by_name("fitness_db.Customers")["id"]
    # ── master OFF: the pre-A1 rule, verbatim ─────────────────────────────────────
    with_switch(False, lambda: check("off: even an authored add is refused",
        refused(lambda: _run(st.add_entity("Note", "business_concept", {"layer": "authored"})))))
    with_switch(False, lambda: check("off: an authored update is refused", refused(lambda: _run(st.update_entity(lead, {"status": "open"})))))
    with_switch(False, lambda: check("off: replace is refused", refused(lambda: st.replace_model_layer(WALK1, RELS1))))
    # ── master ON: the base layer is read-only, the authored layer is the subscriber's ──
    def on():
        check("on: an authored add is allowed", _run(st.add_entity("Note", "business_concept", {"layer": "authored", "extension_id": "ext-u1"})) > 0)
        check("on: a logical (base) add is refused", refused(lambda: _run(st.add_entity("fitness_db.Returns", "entity", {"layer": "logical"}))))
        check("on: an add with NO layer is refused (never silently stamped onto the base)", refused(lambda: _run(st.add_entity("Mystery", "entity", {}))))
        check("on: updating an authored node is allowed", _run(st.update_entity(lead, {"status": "open"})) is True)
        check("on: updating a base node is refused", refused(lambda: _run(st.update_entity(cust, {"description": "tampered"}))))
        check("on: deleting a base node is refused", refused(lambda: _run(st.delete_entity(cust))))
        rid = _run(st.add_relationship(lead, cust, "depends_on"))
        check("on: an edge sourced at an authored node is allowed (target may be base)", rid > 0)
        check("on: an edge sourced at a base node is refused", refused(lambda: _run(st.add_relationship(cust, lead, "depends_on"))))
        base_rel = next(r for r in _run(st.list_relationships(limit=999)) if r["source_name"] == "fitness_db.Customers")
        check("on: deleting a base-sourced edge is refused", refused(lambda: _run(st.delete_relationship(base_rel["id"]))))
        check("on: deleting an authored-sourced edge is allowed", _run(st.delete_relationship(rid)) is True)
        check("on: import_bulk is refused (whole-graph write)", refused(lambda: st.import_bulk(WALK1, RELS1)))
        check("on: clear_graph is refused", refused(lambda: st.clear_graph()))
        check("on: replace is refused without the sync flag", refused(lambda: st.replace_model_layer(WALK1, RELS1)))
        check("on: deleting an authored node is allowed", _run(st.delete_entity(_run(st.add_entity("Tmp", "business_concept", {"layer": "authored"})))) is True)
        # the sync is the one legitimate whole-graph write — and it keeps the subscriber's layer
        sync = store("kg-s", allow_subscribed_write=True)
        res = sync.replace_model_layer(WALK2, RELS2)
        check("on: the sync (flagged) replaces the base and KEEPS the subscriber's authored layer",
              st.get_entity(lead) is not None and "fitness_db.Orders" in names(st) and "fitness_db.Sales" not in names(st), str(res))
        check("on: the subscriber's Note survived the sync too", st.get_entity_by_name("Note") is not None)
    with_switch(True, on)

def t_layer_immutable():
    print("[§6] a node's layer is immutable through update")
    st = store("kg-l"); st.set_kg_metadata("L", is_active=False); st.import_bulk(WALK1, RELS1)
    cust = st.get_entity_by_name("fitness_db.Customers")["id"]
    def relayer():
        try: _run(st.update_entity(cust, {"layer": "authored"})); return False
        except (ValueError, KnowledgeGraphReadOnlyError): return True
    check("re-layering a base node into the authored layer is refused (owned graph too)", relayer())
    check("an update that restates the SAME layer is fine", _run(st.update_entity(cust, {"layer": "logical", "description": "x"})) is True)
    check("an update without a layer key is fine", _run(st.update_entity(cust, {"description": "y"})) is True)

def _fn_src(path, name):
    txt = open(os.path.join(ROOT, path)).read()
    m = re.search(rf"(?:async )?def {re.escape(name)}\(", txt)
    assert m, f"{name} not found in {path}"
    start = m.start()
    nxt = re.search(r"\n(?:async )?def |\n@rest_api_bp\.route|\nclass ", txt[m.end():])
    return txt[start: m.end() + (nxt.start() if nxt else len(txt))]

def t_callers_wired():
    print("[§7] one replace operation; the delete callers still delete")
    rr = "src/trusted_data_agent/api/rest_routes.py"
    au = _fn_src(rr, "kg_apply_update")
    check("apply-update routes through replace_model_layer and no longer clears", "replace_model_layer(" in au and "store.clear_graph()" not in au)
    check("apply-update's failure restore is the same operation", au.count("replace_model_layer(") >= 2)
    rc = _fn_src("src/trusted_data_agent/core/kg_subscription.py", "rebuild_copy")
    check("subscription rebuild routes through replace_model_layer", "replace_model_layer(" in rc and "clear_graph()" not in rc)
    pk = _fn_src("src/trusted_data_agent/core/agent_pack_manager.py", "_refresh_pack_kgs_in_place")
    check("pack in-place refresh routes through replace_model_layer", "replace_model_layer(" in pk and "store.clear_graph()" not in pk)
    if os.path.exists(os.path.join(ROOT, "src/trusted_data_agent/agent/kg_constructors/declarative_model.py")):  # distribution-aware (the Industry Data Model constructor ships with neither distribution)
        dm = _fn_src("src/trusted_data_agent/agent/kg_constructors/declarative_model.py", "redeploy_physical")
        check("declarative redeploy routes through replace_model_layer", "replace_model_layer(" in dm and "clear_graph()" not in dm)
    txt = open(os.path.join(ROOT, rr)).read()
    restore = txt[txt.index("get_version_graph(version_id, store.kg_id, user_uuid)"):][:600]
    check("version restore routes through replace_model_layer", "replace_model_layer(" in restore and "clear_graph()" not in restore)
    dc = _fn_src("src/trusted_data_agent/core/kg_subscription.py", "delete_copy")
    check("delete_copy still deletes (clear_graph)", "clear_graph()" in dc)
    rel = open(os.path.join(ROOT, "src/trusted_data_agent/core/kg_release.py")).read()
    check("the release shadow drop still deletes", rel.count("clear_graph()") >= 1)
    gs = open(os.path.join(ROOT, "components/builtin/knowledge_graph/graph_store.py")).read()
    check("the store exposes replace_model_layer with the kept layers as default", "def replace_model_layer(" in gs and "KEPT_LAYERS_ON_REPLACE" in gs)
    for b in ("sqlite_backend", "neo4j_backend", "base"):
        if not os.path.exists(os.path.join(ROOT, f"src/trusted_data_agent/kgstore/{b}.py")):
            continue  # a backend this distribution does not carry  # distribution-aware (the public core has no Neo4j)
        src = open(os.path.join(ROOT, f"src/trusted_data_agent/kgstore/{b}.py")).read()
        check(f"{b} implements or inherits replace_model_layer", "def replace_model_layer(" in src or b == "neo4j_backend")

def t_stray_layer():
    print("[§8] a stray layer is refused on an explicit write")
    st = store("kg-x"); st.set_kg_metadata("X", is_active=False)
    def bad():
        try: _run(st.add_entity("Weird", "entity", {"layer": "sideways"})); return False
        except ValueError: return True
    check("an unknown layer value is refused by add_entity", bad())

for t in (t_vocabulary, t_replace_keeps_authored, t_idempotent, t_clear_is_a_delete, t_subscribed,
          t_layer_immutable, t_callers_wired, t_stray_layer):
    run(t)
try: os.unlink(TMP)
except OSError: pass
print("=" * 70); print(f"  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
sys.exit(1 if FAIL else 0)
