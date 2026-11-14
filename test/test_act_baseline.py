"""PROP-008 increment 0 — the ACT baseline freeze.

With the master switch OFF (the shipped default) the platform must be byte-identical to
the pre-OBDA-MODEL/OBDA-ACT platform, and that must stay true through every later increment. This suite
is the reference every ACT increment is held to: it only ever GROWS (an increment adds
"with the master off, X is unchanged"), and a row may be restated, never deleted.

  §1  the switch exists, defaults off, is read from the env
  §2  the grants are dropped at the ONE parse seam while off; parsed + round-tripped when on
  §3  the sealed authority stamp of a turn with no grant is byte-identical to the pre-OBDA-MODEL/OBDA-ACT shape
  §4  signatures: the posture/capability projection of a profile carrying the grants does not
      move with the switch, and no ACT key enters any signature while off
  §5  the ontology content hash of a real graph is the same with the switch on and off
  §6  a subscribed graph refuses EVERY write while off (the pre-A1 rule, kept verbatim)
  §7  no action component is reachable while off
  §8  one seam: no module outside agent_authority reads the grant keys

Run: PYTHONPATH=src python3 test/test_act_baseline.py
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

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.core import agent_authority as A
from trusted_data_agent.core import profile_config_contract as C
from trusted_data_agent.kgstore import factory as _factory
from components.builtin.knowledge_graph.graph_store import GraphStore, KnowledgeGraphReadOnlyError
from trusted_data_agent.core.trust import composition as COMP
from trusted_data_agent.auth.database import _migrate_to_multi_kg

# ── fixture: one temp auth DB, every GraphStore pinned to it (the test_kg_model_b idiom) ──
_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c)
_c.commit(); _c.close()
def _ensure_subscribed_column():
    # kg_metadata is created lazily by the store on first use; the subscription stamp is
    # a later self-applying ALTER (schema/37) — add it here once the table exists.
    c = sqlite3.connect(TMP)
    cols = {r[1] for r in c.execute("PRAGMA table_info(kg_metadata)")}
    if "is_subscribed" not in cols:
        c.execute("ALTER TABLE kg_metadata ADD COLUMN is_subscribed INTEGER NOT NULL DEFAULT 0")
    c.commit(); c.close()
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})

U, P = "u-act", "p-act"
ENTS = [{"name": "fitness_db.Customers", "entity_type": "entity", "properties": {"layer": "logical"}},
        {"name": "fitness_db.Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "fitness_db.Sales", "entity_type": "entity", "properties": {"layer": "logical"}}]
RELS = [{"source_name": "fitness_db.Customers", "target_name": "fitness_db.Customers.CustomerID", "relationship_type": "contains"},
        {"source_name": "fitness_db.Sales", "target_name": "fitness_db.Customers", "relationship_type": "foreign_key"}]
GRANTS = {"modelAuthoring": True, "actions": {"types": ["close_lead", "record_lead"], "exclusive": True}}
PRE_ACT_STAMP_KEYS = ["allowed_connectors", "autonomous", "data_surface", "denied_tools", "deny_writes", "max_cost_per_run"]

def store(kg): return GraphStore(P, U, kg_id=kg, db_path=TMP)
def with_switch(value, fn):
    old = _OP._FORCED["act"]
    _OP.force_act(value)
    try: return fn()
    finally: _OP.force_act(old)

def t_switch():
    print("[§1] no switch — availability is the component's")
    from trusted_data_agent.core.config import APP_CONFIG as _C
    check("APP_CONFIG carries no ACT_ENABLED master", not hasattr(_C, "ACT_ENABLED"))
    src = open(os.path.join(ROOT, "src/trusted_data_agent/core/config.py")).read()
    check("config.py reads no TDA_ACT env", 'os.environ.get("TDA_ACT"' not in src)
    compose = open(os.path.join(ROOT, "docker-compose.yml")).read()
    check("compose forwards no TDA_ACT", "TDA_ACT" not in compose)
    check("the write half's availability is the OBDA-ACT / OBDA-MODEL components' (one reader, core/obda_planes)",
          _OP.PLANES == {"read": "obda_read", "model": "obda_model", "act": "action"} and callable(_OP.plane_enabled))

def t_grants_at_the_seam():
    print("[§2] the grants are dropped at the one parse seam while off")
    base = A.parse_scope({})
    off = A.parse_scope(GRANTS, planes={"read": True, "model": False, "act": False})
    check("off: a config carrying both grants parses to the baseline scope", off == base)
    check("off: a full config with grants keeps every pre-OBDA-MODEL/OBDA-ACT field",
          with_switch(False, lambda: A.parse_scope({"deny_writes": True, **GRANTS})).deny_writes is True)
    on = with_switch(True, lambda: A.parse_scope(GRANTS))
    check("on: modelAuthoring parsed", on.model_authoring is True)
    check("on: the action list parsed as a frozenset", on.actions_allowed == frozenset({"close_lead", "record_lead"}))
    check("on: exclusive parsed", on.actions_exclusive is True)
    check("on: an exclusive write surface is a restriction", on.is_restricted)
    check("on: an additive (non-exclusive) list is not", not with_switch(True, lambda: A.parse_scope({"actions": {"types": ["x"]}})).is_restricted)
    check("on: round-trips through the sealed stamp", A.scope_from_stamp(on.to_dict()) == on)
    check("on: described in words, exclusive only", A.describe_scope(on) == ["write surface only: 2 action types"])
    check("malformed actions is no grant", with_switch(True, lambda: A.parse_scope({"actions": "yes"})).actions_allowed is None)
    check("scope_for_profile honours the switch", with_switch(False, lambda: A.scope_for_profile({"authorityConfig": GRANTS})) == base)

def t_stamp_shape():
    print("[§3] the sealed authority stamp is byte-identical without a grant")
    d = A.AgentScope().to_dict()
    check("baseline stamp carries exactly the pre-OBDA-MODEL/OBDA-ACT keys", sorted(d.keys()) == PRE_ACT_STAMP_KEYS, str(sorted(d.keys())))
    d2 = A.parse_scope(GRANTS, planes={"read": True, "model": False, "act": False}).to_dict()
    check("off: a config with grants stamps the pre-OBDA-MODEL/OBDA-ACT keys only", sorted(d2.keys()) == PRE_ACT_STAMP_KEYS)
    full = with_switch(True, lambda: A.parse_scope({"deny_writes": True, "dataSurface": {"products": ["p"], "exclusive": True}}))
    check("on without grants: still the pre-OBDA-MODEL/OBDA-ACT keys (grants emitted only when set)", sorted(full.to_dict().keys()) == PRE_ACT_STAMP_KEYS)
    stamp = with_switch(True, lambda: A.parse_scope(GRANTS)).to_dict()
    check("on with grants: the two keys appear and nothing else changes",
          sorted(stamp.keys()) == sorted(PRE_ACT_STAMP_KEYS + ["actions", "model_authoring"]))
    # the build_authority_stamp seam reads to_dict — a pre-OBDA-MODEL/OBDA-ACT stamp must deserialize unchanged
    legacy = {k: v for k, v in A.AgentScope().to_dict().items()}
    check("a pre-OBDA-MODEL/OBDA-ACT stamp reads back with no grant", A.scope_from_stamp(legacy) == A.AgentScope())

def t_signatures():
    print("[§4] signatures do not move with the switch")
    prof = {"id": P, "profile_type": "llm_only", "authorityConfig": GRANTS,
            "groundingConfig": {"mode": "enforce"}}
    sig_off = with_switch(False, lambda: C.signature_for(prof, "posture"))
    sig_on = with_switch(True, lambda: C.signature_for(prof, "posture"))
    check("posture signature identical on/off", sig_off == sig_on)
    cap_off = with_switch(False, lambda: C.signature_for(prof, "capability"))
    cap_on = with_switch(True, lambda: C.signature_for(prof, "capability"))
    check("capability signature identical on/off", cap_off == cap_on)
    flat = repr(sig_off) + repr(cap_off)
    check("no ACT key enters a signature", "modelAuthoring" not in flat and "close_lead" not in flat)
    check("authorityConfig stays unsigned (the contract's own rule)", not C._BY_KEY["authorityConfig"].signed)

def t_content_hash():
    print("[§5] the ontology content hash is switch-independent")
    st = store("kg-base"); st.set_kg_metadata("Fitness", is_active=True); st.import_bulk(ENTS, RELS)
    h_off = with_switch(False, lambda: COMP.kg_graph_hash("kg-base", TMP))
    h_on = with_switch(True, lambda: COMP.kg_graph_hash("kg-base", TMP))
    check("same hash, same count, on and off", h_off == h_on and h_off[1] == 3, str((h_off, h_on)))
    e, r = COMP.load_kg_graph("kg-base", TMP)
    check("direct formula agrees", COMP.kg_content_hash(e, r) == h_off[0])

def t_subscribed_refuses_everything_off():
    print("[§6] a subscribed graph refuses every write while off")
    st = store("kg-sub"); st.set_kg_metadata("Subscribed", is_active=False); st.import_bulk(ENTS, RELS)
    _ensure_subscribed_column()
    c = sqlite3.connect(TMP); c.execute("UPDATE kg_metadata SET is_subscribed=1 WHERE kg_id='kg-sub'"); c.commit(); c.close()
    def refused(fn):
        try: fn(); return False
        except KnowledgeGraphReadOnlyError: return True
    ent = st.get_entity_by_name("fitness_db.Customers")
    def _run(coro): return asyncio.run(coro)
    with_switch(False, lambda: check("add_entity refused (even an authored one)",
        refused(lambda: _run(st.add_entity("Lead", "entity", {"layer": "authored"})))))
    with_switch(False, lambda: check("update_entity refused", refused(lambda: _run(st.update_entity(ent["id"], {"x": 1})))))
    with_switch(False, lambda: check("delete_entity refused", refused(lambda: _run(st.delete_entity(ent["id"])))))
    with_switch(False, lambda: check("add_relationship refused", refused(lambda: _run(st.add_relationship(ent["id"], ent["id"], "relates_to")))))
    with_switch(False, lambda: check("import_bulk refused", refused(lambda: st.import_bulk(ENTS, RELS))))
    with_switch(False, lambda: check("clear_graph refused", refused(lambda: st.clear_graph())))
    check("the sync flag still permits the one legitimate write",
          GraphStore(P, U, kg_id="kg-sub", db_path=TMP, allow_subscribed_write=True).import_bulk(ENTS, RELS).get("entities_added") is not None)

def t_no_action_component_off():
    print("[§7] no action component reaches a catalog while off")
    d = os.path.join(ROOT, "components", "builtin", "action")
    if not os.path.isdir(d):
        check("the action component does not exist yet (increment B4 adds it, inert)", True)
        return
    import json
    man = json.load(open(os.path.join(d, "manifest.json")))
    check("the action component is enabled for no profile type by default", man.get("enabled_for") in ([], None))
    hsrc = open(os.path.join(d, "handler.py")).read()
    check("the action component declares profile relevance on the graph binding, never a master", "profile_has_graph" in hsrc and "is_profile_relevant" in hsrc and "ACT_ENABLED" not in hsrc)

def t_one_seam():
    print("[§8] one seam: only agent_authority reads the grant keys")
    offenders = []
    for base, _, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if not f.endswith(".py"): continue
            p = os.path.join(base, f)
            if p.endswith("agent_authority.py"): continue
            txt = open(p, encoding="utf-8", errors="ignore").read()
            if re.search(r'["\']modelAuthoring["\']', txt) or re.search(r'c\.get\(["\']actions["\']\)', txt):
                offenders.append(os.path.relpath(p, ROOT))
    check("no other backend module reads `modelAuthoring` / the `actions` grant", not offenders, str(offenders))
    src = open(os.path.join(ROOT, "src/trusted_data_agent/core/agent_authority.py")).read()
    check("parse_scope drops the grants under the master", "ACT_ENABLED" in src.split("def parse_scope")[1].split("def scope_from_stamp")[0])

def t_replace_is_byte_identical_without_authored_nodes():
    print("[§9] A1: on a graph with no authored/observed node, replace == clear + import (any switch state)")
    rels2 = [{"source_name": "fitness_db.Sales", "target_name": "fitness_db.Customers", "relationship_type": "foreign_key"}]
    a = store("kg-rep-a"); a.set_kg_metadata("A", is_active=False); a.import_bulk(ENTS, RELS)
    b = store("kg-rep-b"); b.set_kg_metadata("B", is_active=False); b.import_bulk(ENTS, RELS)
    new_ents = [dict(e) for e in ENTS[:1]] + [{"name": "fitness_db.Sales", "entity_type": "entity", "properties": {"layer": "logical"}}]
    with_switch(False, lambda: (a.clear_graph(), a.import_bulk(new_ents, rels2)))
    with_switch(False, lambda: b.replace_model_layer(new_ents, rels2))
    ha = COMP.kg_graph_hash("kg-rep-a", TMP); hb = COMP.kg_graph_hash("kg-rep-b", TMP)
    check("off: identical content hash and count", ha == hb and ha[1] == 2, str((ha, hb)))
    with_switch(True, lambda: b.replace_model_layer(new_ents, rels2))
    check("on: still identical (the keep is a property of the layer, not of the switch)", COMP.kg_graph_hash("kg-rep-b", TMP) == ha)

for t in (t_switch, t_grants_at_the_seam, t_stamp_shape, t_signatures, t_content_hash,
          t_subscribed_refuses_everything_off, t_no_action_component_off, t_one_seam,
          t_replace_is_byte_identical_without_authored_nodes):
    run(t)
try: os.unlink(TMP)
except OSError: pass
print("=" * 70); print(f"  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
sys.exit(1 if FAIL else 0)
