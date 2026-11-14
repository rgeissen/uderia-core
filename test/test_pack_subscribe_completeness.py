"""Gate: a pack SUBSCRIBER gets the members a fork gets — knowledge-graph copies, the
skills recorded, the ledger naming them, the publisher's trust reinstated — and an
unsubscribe removes the copies it made. P1 of docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md.

  M1  each manifest graph is materialized for the subscriber's profile of the SAME TAG
      (owner tag as fallback); a graph for a tag the subscription does not carry is
      skipped and SAID, never silently dropped
  M2  the copy is keyed to the pack listing with source_kind=agent_pack, to the
      publisher's kg_id, hashed over the exported file's bytes
  M3  every other pack profile the publisher assigned the graph to gets an assignment on
      the subscriber's side — inactive unless that profile has no active graph
  M4  the pack's skills are collected for the ledger (never installed twice)
  M5  a materialization failure is a WARNING on the result, the others still land
  M6  the route wires it: materialize → ledger (kg_ids, skill_ids) → reinstate trust
  M7  unsubscribe deletes the agent_pack copies hosted by the removed profiles
  M8  the export entry carries the publisher's kg_id (what a sync will key on)

Run: python3 test/test_pack_subscribe_completeness.py
"""
from __future__ import annotations
import ast, asyncio, hashlib, inspect, json, os, sqlite3, sys, tempfile, types, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

PASS = FAIL = 0
def check(name, cond, detail=None):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL {name}" + (f": {detail!r}" if detail is not None else ""))

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_gs = types.ModuleType("components.builtin.knowledge_graph.graph_store")
class _GS:
    def __init__(self, *a, **k): pass
_gs.GraphStore = _GS
sys.modules.setdefault("components.builtin.knowledge_graph.graph_store", _gs)

from trusted_data_agent.core.agent_pack_manager import AgentPackManager as M  # noqa: E402
from trusted_data_agent.core import kg_subscription as K  # noqa: E402
from trusted_data_agent.core import sqlite_util as SU  # noqa: E402

def _zip(tmp, kgs, skills=(), member_trust=()):
    path = os.path.join(tmp, "pack.zip")
    manifest = {"name": "P", "knowledge_graphs": [], "skills": [{"skill_id": s} for s in skills],
                "member_trust": list(member_trust)}
    with zipfile.ZipFile(path, "w") as z:
        for i, kg in enumerate(kgs, 1):
            fn = f"knowledge_graphs/kg_{i}.json"
            z.writestr(fn, json.dumps({"name": kg["name"], "entities": kg.get("entities", [{"name": "E"}]), "relationships": []}))
            manifest["knowledge_graphs"].append({"ref": f"kg_{i}", "file": fn, "kg_id": kg.get("kg_id", f"pub-{i}"),
                                                 "profile_tag": kg.get("profile_tag"), "owner_profile_tag": kg.get("owner_profile_tag"),
                                                 "name": kg["name"], "database_name": "db", "description": "d",
                                                 "assigned_profiles": kg.get("assigned_profiles", [])})
        z.writestr("manifest.json", json.dumps(manifest))
    return path

def _db(tmp):
    path = os.path.join(tmp, "t.db"); c = sqlite3.connect(path)
    c.executescript("CREATE TABLE kg_profile_assignments (kg_id TEXT, kg_owner_profile_id TEXT, assigned_profile_id TEXT, user_uuid TEXT, is_active INTEGER, UNIQUE(kg_id, assigned_profile_id, user_uuid));")
    c.commit(); c.close(); return path

def _run(mgr, zip_path, db, tag_map, fail_names=()):
    calls = []
    async def fake_export(**kw): return zip_path
    mgr.export_pack = fake_export
    mgr._stamp_pack_kg_validation = lambda store, data: calls.append(("stamp", data.get("name")))
    orig = K.materialize_copy
    def fake_mat(conn, **kw):
        calls.append(("materialize", kw))
        if kw["export_data"]["kg_name"] in fail_names: raise RuntimeError("store down")
        return {"subscription_id": "s-" + kw["export_data"]["kg_name"], "local_kg_id": "local-" + kw["export_data"]["kg_name"],
                "kg_name": kw["export_data"]["kg_name"] + " (subscribed)", "active": True, "entities_added": 1, "relationships_added": 0}
    K.materialize_copy = fake_mat
    try:
        out = asyncio.new_event_loop().run_until_complete(mgr.materialize_subscription_members(
            subscriber_uuid="sub", publisher_uuid="pub", publisher_profile_ids=["p1"], pack_name="P",
            pack_listing_id="listing-1", tag_to_profile_id=tag_map, db_path=db))
    finally:
        K.materialize_copy = orig
    return out, calls

def t_materialize():
    tmp = tempfile.mkdtemp(); db = _db(tmp); mgr = M.__new__(M)
    zp = _zip(tmp, [
        {"name": "NIBRS graph", "kg_id": "pub-nibrs", "profile_tag": "NIBRS", "owner_profile_tag": "NIBRS",
         "assigned_profiles": [{"tag": "NIBRS", "is_active": True}, {"tag": "POLICE", "is_active": False}]},
        {"name": "CASE graph", "kg_id": "pub-case", "profile_tag": None, "owner_profile_tag": "CASEDF"},
        {"name": "Orphan graph", "kg_id": "pub-x", "profile_tag": "ZZZ", "owner_profile_tag": "ZZZ"},
    ], skills=("policing-data-product-consumer", "teradata-sql-expert"))
    out, calls = _run(mgr, zp, db, {"NIBRS": "s-nibrs", "CASEDF": "s-case", "POLICE": "s-police"})
    mats = [c[1] for c in calls if c[0] == "materialize"]
    check("M1 a graph lands on the subscriber's profile of the same tag", any(m["target_profile_id"] == "s-nibrs" and m["export_data"]["kg_name"] == "NIBRS graph" for m in mats))
    check("M1 the owner tag is the fallback when profile_tag is absent", any(m["target_profile_id"] == "s-case" for m in mats))
    check("M1 a graph for a tag the subscription lacks is skipped AND said", len(mats) == 2 and any("Orphan graph" in w and "@ZZZ" in w for w in out["warnings"]), out["warnings"])
    m = next(m for m in mats if m["target_profile_id"] == "s-nibrs")
    raw = zipfile.ZipFile(zp).read("knowledge_graphs/kg_1.json")
    check("M2 keyed to the pack listing as an agent_pack copy of the publisher's graph",
          m["source_kind"] == "agent_pack" and m["source_marketplace_id"] == "listing-1" and m["source_kg_id"] == "pub-nibrs")
    check("M2 hashed over the exported file's bytes", m["export_hash"] == hashlib.sha256(raw).hexdigest())
    c = sqlite3.connect(db)
    rows = c.execute("SELECT assigned_profile_id, is_active FROM kg_profile_assignments WHERE kg_id = 'local-NIBRS graph'").fetchall()
    check("M3 the other assigned pack profile gets an (inactive-or-first) assignment on the subscriber's side", ("s-police", 1) in rows, rows)
    check("M3 the target itself is not double-assigned here (the materializer did it)", all(r[0] != "s-nibrs" for r in rows), rows)
    check("M4 the pack's skills are collected for the ledger", out["skill_ids"] == ["policing-data-product-consumer", "teradata-sql-expert"])
    check("M2 ids and names returned for the ledger", out["kg_ids"] == ["local-NIBRS graph", "local-CASE graph"] and out["kgs_created"] == 2 and len(out["kg_names"]) == 2)
    check("M1 the validation stamp follows each copy", sum(1 for c in calls if c[0] == "stamp") == 2)
    check("the manifest rides along for the trust step", "knowledge_graphs" in out["manifest"] and "member_trust" in out["manifest"])

def t_failure_is_a_warning():
    tmp = tempfile.mkdtemp(); db = _db(tmp); mgr = M.__new__(M)
    zp = _zip(tmp, [{"name": "A", "profile_tag": "T"}, {"name": "B", "profile_tag": "T"}])
    out, calls = _run(mgr, zp, db, {"T": "s-t"}, fail_names=("A",))
    check("M5 a failed copy is a warning, the next still lands", out["kgs_created"] == 1 and out["kg_ids"] == ["local-B"] and any("'A'" in w for w in out["warnings"]), out)

def t_route_wiring():
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "agent_pack_routes.py")).read()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    sub = fns["subscribe_marketplace_agent_pack"]
    calls = [n for n in ast.walk(sub) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    names = [c.func.attr for c in calls]
    check("M6 the subscribe route materializes the members", "materialize_subscription_members" in names)
    rec = next((c for c in calls if c.func.attr == "_record_installation"), None)
    kws = {k.arg for k in rec.keywords} if rec else set()
    check("M6 ...the ledger names the copies and the skills", {"kg_ids", "kg_names", "skill_ids"} <= kws, kws)
    check("M6 ...and the publisher's trust is reinstated", "_reinstate_member_trust" in names)
    # ast.walk is breadth-first, not source order — order by line
    order = [c.func.attr for c in sorted(calls, key=lambda c: c.lineno)
             if c.func.attr in ("materialize_subscription_members", "_record_installation", "_reinstate_member_trust")]
    check("M6 in that order", order == ["materialize_subscription_members", "_record_installation", "_reinstate_member_trust"], order)
    clean = ast.get_source_segment(src, fns["_cleanup_subscriber_profiles"])
    check("M7 unsubscribe deletes the agent_pack copies hosted by the removed profiles",
          "delete_copy(" in clean and "source_kind = 'agent_pack'" in clean and "target_profile_id IN" in clean)
    exp = inspect.getsource(M.export_pack)
    check("M8 the export entry carries the publisher's kg_id", '"kg_id": _kg_meta["kg_id"],' in exp)

for fn in (t_materialize, t_failure_is_a_warning, t_route_wiring):
    try: fn()
    except Exception as e:
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
