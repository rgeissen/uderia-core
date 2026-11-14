"""Model B — released-only grounding (Tier 4.3 §1a, built Sep 2026) + the rag_context fix.

Real SQLite (temp DB, real schema + real migration), the real GraphStore, the real
KnowledgeGraphHandler, the real kg_version_db, the real trust composition — no doubles
for the thing under test. CI-pure: no server, no model, no network.

  §1  no release ⇒ Model A byte-identical / honest live fallback for a released-mode agent
  §2  publish ⇒ the released view is materialised; idempotent; hidden from every list
  §3  the draft moves, the release does not; a NEW publish moves it
  §4  trust hashes the EFFECTIVE ingredient (released-mode badge ignores draft edits)
  §5  the contract signs `groundOn == released` and nothing else
  §6  the handler: reads route to the released view; writes are refused, recovery-shaped;
      Model A and fallback-live agents are never refused
  §7  the seams are wired (enrichment · trusted scope · tool reads · publish · deletes ·
      enumerators · editor carry-forward)
  §8  rag_context reports what it does (planner-owned) and reads no dead key

Run: PYTHONPATH=src python3 test/test_kg_model_b.py
"""
import asyncio
import inspect
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)

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

# ── fixture: one temp auth DB that every real module reads ─────────────────────
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.auth.database import _migrate_to_multi_kg
from trusted_data_agent.kgstore import factory as _factory
from trusted_data_agent.core import kg_version_db, kg_release as R
from components.builtin.knowledge_graph.graph_store import GraphStore

_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c)
_c.execute("CREATE TABLE IF NOT EXISTS kg_profile_assignments (kg_id TEXT, kg_owner_profile_id TEXT, "
           "assigned_profile_id TEXT, user_uuid TEXT, is_active INTEGER DEFAULT 0)")
_c.commit(); _c.close()
_ORIG_AUTH = getattr(APP_CONFIG, "AUTH_DB_PATH", None)
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False          # no model load in CI
_factory.reset_instance_cache()
# Pin every GraphStore in this process to the temp DB. The factory's per-profile resolution
# would otherwise consult the REAL auth DB for the fixture's fake user (and try to bootstrap
# a user_preferences row for it) — a CI test must never touch the deployment's database.
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})
kg_version_db._DB_PATH = Path(TMP); kg_version_db._SCHEMA_APPLIED = False; kg_version_db._ensure_schema()

U, P, KG = "u1", "p1", "kg-live"
ENTS = [{"name": "fitness_db.Customers", "entity_type": "entity", "properties": {"layer": "logical", "description": "a customer"}},
        {"name": "fitness_db.Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "fitness_db.Sales", "entity_type": "entity", "properties": {"layer": "logical"}}]
RELS = [{"source_name": "fitness_db.Customers", "target_name": "fitness_db.Customers.CustomerID", "relationship_type": "contains"}]
LIVE_PROFILE = {"id": P, "componentConfig": {"knowledge_graph": {"enabled": True}}}
REL_PROFILE = {"id": P, "componentConfig": {"knowledge_graph": {"enabled": True, "groundOn": "released"}}}

def live_store(): return GraphStore(P, U, kg_id=KG, db_path=TMP)
def count(kid):
    c = sqlite3.connect(TMP); n = c.execute("SELECT COUNT(*) FROM kg_entities WHERE kg_id=?", (kid,)).fetchone()[0]; c.close(); return n
def row(kid):
    c = sqlite3.connect(TMP); c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM kg_metadata WHERE kg_id=?", (kid,)).fetchone(); c.close(); return dict(r) if r else None
def publish(label):
    ents, rels = asyncio.run(live_store().export_graph())
    snap = kg_version_db.snapshot_kg(KG, U, ents, rels, label=label)
    vid = snap["version_id"] if snap else kg_version_db.list_versions(KG, U)[-1]["version_id"]
    live_store().set_lifecycle_state(lifecycle_status="published", published_version_id=vid, semantic_version=label)
    return vid

st = live_store(); st.set_kg_metadata("Fitness", is_active=True); st.import_bulk(ENTS, RELS)
SHADOW = R.shadow_id(KG)

def t_no_release():
    print("[§1] no release yet")
    check("a Model-A profile grounds live", R.resolve_grounding(KG, LIVE_PROFILE, U, TMP) == {"mode": "live"})
    check("absent config is Model A", R.ground_on_mode({}) == "live" and R.ground_on_mode(None) == "live")
    check("a stray value is Model A, never a half-mode", R.ground_on_mode({"componentConfig": {"knowledge_graph": {"groundOn": "RELEASE"}}}) == "live")
    g = R.resolve_grounding(KG, REL_PROFILE, U, TMP)
    check("a released-mode agent with no release falls back to live AND SAYS SO",
          g.get("mode") == "released" and g.get("fallback") == "live" and "no release" in g.get("reason", ""), g)
    check("nothing was materialised", R.shadow_state(KG, TMP) is None and count(SHADOW) == 0)
    check("effective_kg_id is the live graph", R.effective_kg_id(KG, REL_PROFILE, U, TMP) == KG)

def t_publish():
    print("[§2] publish materialises the released view")
    vid = publish("1.0.0")
    d = R.ensure_released_shadow(KG, U, TMP)
    check("the view is described", d and d["shadow_kg_id"] == SHADOW and d["version_id"] == vid and d["label"] == "1.0.0", d)
    check("the shadow holds exactly the released entities", count(SHADOW) == 3, count(SHADOW))
    r = row(SHADOW)
    check("the shadow row names what it is a release OF and the pinned version",
          r and r["released_of"] == KG and r["released_version_id"] == vid and r["is_active"] == 0, r)
    check("the shadow is NEVER the active graph of its owner", live_store().kg_id == KG)
    before = r["updated_at"]
    d2 = R.ensure_released_shadow(KG, U, TMP)
    check("ensure is idempotent (same version ⇒ no rebuild)", d2 == d and row(SHADOW)["updated_at"] == before)
    names = {g["kg_id"] for g in GraphStore.list_all_graphs(U, db_path=TMP)}
    check("list_all_graphs hides the shadow and keeps the live graph", KG in names and SHADOW not in names, names)
    g = R.resolve_grounding(KG, REL_PROFILE, U, TMP)
    check("a released-mode agent now grounds on the shadow",
          g.get("kg_id") == SHADOW and g.get("label") == "1.0.0" and g.get("lifecycle") == "published", g)
    check("a Model-A agent is untouched by the publish", R.resolve_grounding(KG, LIVE_PROFILE, U, TMP) == {"mode": "live"})

def t_draft_moves():
    print("[§3] the draft moves, the release does not")
    asyncio.run(live_store().add_entity("fitness_db.Products", "entity", {"layer": "logical"}))
    check("the live draft gained an entity", count(KG) == 4)
    R.ensure_released_shadow(KG, U, TMP)
    check("the released view did NOT", count(SHADOW) == 3)
    vid2 = publish("1.1.0")
    d = R.ensure_released_shadow(KG, U, TMP)
    check("a NEW publish rebuilds the view to the new version",
          d["version_id"] == vid2 and count(SHADOW) == 4 and row(SHADOW)["released_version_id"] == vid2, d)
    check("the shadow name follows the release label", "1.1.0" in row(SHADOW)["name"])

def t_trust():
    print("[§4] trust hashes the EFFECTIVE ingredient")
    from trusted_data_agent.core.trust import composition as C
    rel = C._ontology_ingredient(P, U, TMP, profile=REL_PROFILE)
    live = C._ontology_ingredient(P, U, TMP, profile=LIVE_PROFILE)
    check("both refs keep the LIVE kg_id (the attestation key)", rel["kg_id"] == KG and live["kg_id"] == KG)
    check("released-mode names the view it read", rel.get("released_view") == SHADOW and live.get("released_view") is None)
    check("with draft == release the two hashes agree", rel["content_hash"] == live["content_hash"])
    asyncio.run(live_store().add_entity("fitness_db.Draft_only", "entity", {"layer": "logical"}))
    rel2 = C._ontology_ingredient(P, U, TMP, profile=REL_PROFILE)
    live2 = C._ontology_ingredient(P, U, TMP, profile=LIVE_PROFILE)
    check("a draft edit moves the Model-A hash", live2["content_hash"] != live["content_hash"])
    check("…and leaves the released-mode hash UNCHANGED (the agent's behaviour did not change)",
          rel2["content_hash"] == rel["content_hash"])
    publish("1.2.0"); R.ensure_released_shadow(KG, U, TMP)
    rel3 = C._ontology_ingredient(P, U, TMP, profile=REL_PROFILE)
    check("a new publish moves the released-mode hash (its behaviour did change)",
          rel3["content_hash"] != rel["content_hash"] and rel3["content_hash"] == live2["content_hash"])

def t_contract():
    print("[§5] the contract signs groundOn")
    from trusted_data_agent.core.profile_config_contract import _canon_component_config as canon
    base = {"knowledge_graph": {"enabled": True, "intensity": "high"}, "chart": {"enabled": True}}
    check("existing shapes are byte-identical", canon(base) == ["chart", "knowledge_graph"])
    live = {"knowledge_graph": {"enabled": True, "groundOn": "live"}}
    check("groundOn=live hashes as absent", canon(live) == ["knowledge_graph"])
    rel = {"knowledge_graph": {"enabled": True, "groundOn": "released"}}
    check("groundOn=released is a signed capability token", "knowledge_graph:groundOn=released" in canon(rel))
    check("still order-independent", canon({"b": {"enabled": True}, "a": {"enabled": True}}) == ["a", "b"])

def t_handler():
    print("[§6] the handler — reads see the release, writes are refused")
    from components.builtin.knowledge_graph import handler as kgh
    from trusted_data_agent.core.config_manager import ConfigManager
    h = kgh.KnowledgeGraphHandler.__new__(kgh.KnowledgeGraphHandler)
    real = ConfigManager.get_profile
    holder = {"profile": REL_PROFILE}
    ConfigManager.get_profile = lambda self, pid, uid: holder["profile"]
    try:
        store, g = h._resolve_grounding_store(P, U, P)
        check("released-mode reads resolve to the shadow store", store.kg_id == SHADOW and g["mode"] == "released", (store.kg_id, g))
        holder["profile"] = LIVE_PROFILE
        store2, g2 = h._resolve_grounding_store(P, U, P)
        check("Model A reads resolve to the live store", store2.kg_id == KG and g2 == {"mode": "live"})
        ctx = {"profile_id": P, "user_uuid": U}
        holder["profile"] = REL_PROFILE
        for action, args in [("add_entity", {"entity_name": "X", "entity_type": "entity"}),
                             ("add_relationship", {"entity_name": "a", "target_entity": "b", "relationship_type": "contains"}),
                             ("declare_concept", {}), ("restore", {})]:
            p = asyncio.run(h.process({"action": action, **args}, ctx))
            md = p.metadata or {}
            check(f"'{action}' is refused for a released-mode agent, recovery-shaped",
                  md.get("refused") == "released_read_only" and "NEXT STEP" in (p.spec or {}).get("error", "")
                  and md.get("suppress_inline") is True and "1.2.0" in (p.spec or {}).get("error", ""), md)
        q = asyncio.run(h.process({"action": "query"}, ctx))
        stats = (q.metadata or {}).get("stats") or {}
        n = stats.get("entity_count") or stats.get("total_entities") or stats.get("entities")
        check("a READ (query stats) is served from the released view", n == 5, stats)
        holder["profile"] = LIVE_PROFILE
        p = asyncio.run(h.process({"action": "add_entity", "entity_name": "fitness_db.ViaTool", "entity_type": "entity"}, ctx))
        check("a Model-A agent's write is NOT refused", not (p.metadata or {}).get("refused"), p.metadata)
        # a released-mode agent whose ontology has NO release is on the live fallback ⇒ no refusal
        c = sqlite3.connect(TMP); c.execute("UPDATE kg_metadata SET published_version_id=NULL WHERE kg_id=?", (KG,)); c.commit(); c.close()
        R.drop_released_shadow(KG, U, TMP)
        holder["profile"] = REL_PROFILE
        p = asyncio.run(h.process({"action": "add_entity", "entity_name": "fitness_db.ViaTool2", "entity_type": "entity"}, ctx))
        check("release-only agent on the LIVE FALLBACK is not refused (there is no release to protect)",
              not (p.metadata or {}).get("refused"), p.metadata)
        publish("2.0.0")   # restore a release for later sections
    finally:
        ConfigManager.get_profile = real

def t_wiring():
    print("[§7] the seams")
    from components.builtin.knowledge_graph import handler as kgh
    hs = inspect.getsource(kgh.KnowledgeGraphHandler)
    check("enrichment, trusted scope and tool reads ALL route through the one resolver",
          hs.count("self._resolve_grounding_store(") >= 4, hs.count("self._resolve_grounding_store("))
    enr = inspect.getsource(kgh.KnowledgeGraphHandler.get_context_enrichment)
    check("the enrichment event carries `grounding`", '"grounding": _grounding' in enr)
    check("get_trusted_scope resolves through the same seam", "_resolve_grounding_store" in inspect.getsource(kgh.KnowledgeGraphHandler.get_trusted_scope))
    check("process() refuses writes through the one helper", "_released_write_refusal" in inspect.getsource(kgh.KnowledgeGraphHandler.process))
    # the property: every action whose handler WRITES the graph is in the set (the set may
    # grow — PROP-008 A2 added propose_entity / propose_attribute — never shrink below these)
    check("the write-action set covers every mutating action",
          frozenset({"add_entity", "add_relationship", "restore", "declare_concept"}) <= kgh._KG_WRITE_ACTIONS
          and {"propose_entity", "propose_attribute"} <= set(kgh._KG_WRITE_ACTIONS)
          and not ({"query", "visualize", "open", "get_context", "compile_metric"} & set(kgh._KG_WRITE_ACTIONS)))
    from trusted_data_agent.core import ontology_governance as OG
    gs = inspect.getsource(OG.run_transition)
    check("publish materialises the view eagerly", "ensure_released_shadow" in gs.split("LifecycleAction.PUBLISH:")[-1])
    rr = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
    check("KG delete drops the released view", "drop_released_shadow(resolved_kg_id" in rr)
    check("the published catalog excludes shadows", "lifecycle_status='published' AND released_of IS NULL" in rr)
    pm = open(os.path.join(ROOT, "src/trusted_data_agent/core/agent_pack_manager.py")).read()
    check("pack uninstall drops the released view", "drop_released_shadow(kg_id" in pm)
    check("the pack's legacy graph lookup never picks a shadow", "AND released_of IS NULL" in pm)
    gsrc = open(os.path.join(ROOT, "components/builtin/knowledge_graph/graph_store.py")).read()
    check("list_all_graphs hides shadows (SQL)", "km.user_uuid = ? AND km.released_of IS NULL" in gsrc)
    # delete cascade, functionally
    check("drop_released_shadow removes rows + entities", R.ensure_released_shadow(KG, U, TMP) and R.drop_released_shadow(KG, U, TMP)
          and R.shadow_state(KG, TMP) is None and count(SHADOW) == 0)
    check("dropping an absent view is a no-op, not an error", R.drop_released_shadow(KG, U, TMP) is False)
    js = open(os.path.join(ROOT, "static/js/handlers/configurationHandler.js")).read()
    check("the editor renders a Ground-on select on the knowledge_graph row only",
          "comp-groundon-select" in js and "comp.component_id === 'knowledge_graph' ?" in js)
    check("the editor save CARRIES FORWARD a stored groundOn when the select is absent",
          "const groundOn = groundSelect ? groundSelect.value : stored.groundOn;" in js)
    check("the editor writes the key only for 'released' (absent == live, byte-identical)",
          "if (groundOn === 'released') componentConfig[compId].groundOn = 'released';" in js)
    ui = open(os.path.join(ROOT, "static/js/ui.js")).read()
    check("the Live Status card renders the grounding row", "Grounded on" in ui and "gnd.mode === 'released'" in ui)
    rs = inspect.getsource(R)
    check("a materialisation failure warns once, never raises into a turn", "warn_once" in rs and "kg-release-materialise" in rs)

def t_rag_context():
    print("[§8] rag_context reports what it does")
    p = os.path.join(ROOT, "components/builtin/context_window/modules/rag_context/handler.py")
    src = open(p).read()
    code = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))
    check("the module reads no dead `ragConfig` key (code, not the comment recording its removal)",
          'get("ragConfig"' not in code and "ragConfig" not in code.replace("`ragConfig.collections` key no profile", ""))
    check("the module performs no retrieval of its own", "retrieve_examples(" not in src)
    check("it reports the planner as the owner", '"reason": "planner_owned"' in src)
    from components.builtin.context_window.module_registry import get_module_registry, reset_module_registry
    from components.builtin.context_window.base import AssemblyContext
    reset_module_registry(); reg = get_module_registry()
    reg.discover_modules()
    mod = reg.get_handler("rag_context")
    check("the module loads through the real registry", mod is not None)
    if mod is not None:
        ctx = AssemblyContext(profile_type="tool_enabled", profile_id=P, session_id="s", user_uuid=U,
                              session_data={"current_query": "q"}, profile_config={"ragCollections": ["3", "4"]})
        c = asyncio.run(mod.contribute(1000, ctx))
        check("contribute is a 0-token honest report", c.tokens_used == 0 and c.content == "" and c.metadata.get("reason") == "planner_owned", c.metadata)
        check("…that names the planner's scope", "2 configured" in c.metadata.get("planner_scope", ""), c.metadata)
        c2 = asyncio.run(mod.contribute(1000, AssemblyContext(profile_type="tool_enabled", profile_id=P, session_id="s", user_uuid=U,
                                                               session_data={"current_query": "q"}, profile_config={})))
        check("no filter ⇒ 'all accessible' scope", "all accessible" in c2.metadata.get("planner_scope", ""), c2.metadata)
    doc = open(os.path.join(ROOT, "docs/Architecture/CONTEXT_WINDOW_ARCHITECTURE.md")).read()
    check("the architecture doc states the planner owns champion retrieval", "planner_owned" in doc)

for _fn in (t_no_release, t_publish, t_draft_moves, t_trust, t_contract, t_handler, t_wiring, t_rag_context):
    run(_fn)
if _ORIG_AUTH is not None: APP_CONFIG.AUTH_DB_PATH = _ORIG_AUTH
try: os.remove(TMP)
except OSError: pass
print(f"\n=== KG Model B: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
