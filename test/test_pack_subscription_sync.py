"""Gate: a pack SUBSCRIPTION can be re-materialized from the publisher's live pack — in
place, ids preserved — by the subscriber (sync) and by the publisher's in-place update
(fan-out). P2 of docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md.

  B1  build_subscribed_profiles(existing_ids=) keeps a copy's id — a coordinator's
      children resolve to the PRESERVED ids (they are built first); absent, ids are minted
  B2  the same seam builds a ONE-profile pack (an agent shared alone): no minimum size
  K1  the materializer's update mode: an unchanged export hash keeps the copy (no rebuild,
      no stamp), a changed one is REBUILT in place (rebuild_copy, same local id, stamped),
      a graph with no copy is materialized, a copy the pack no longer ships is deleted
  K2  a failed rebuild is a warning, the previous copy still stands (the ledger names it)
  K3  update=False is the P1 path, byte-identical (no rebuild, no delete)
  S1  the sync: existing tags updated IN PLACE under their ids with the subscription's
      own facts kept out of the merge and re-stamped after; new tags added; dropped tags
      have their sessions archived, their copies removed, the profile removed; collection
      references reconciled and planners re-keyed; graphs synced in update mode; the
      ledger reconciled with is_owned=False rows for every member; synced_at stamped;
      trust reinstated
  S2  a weaker posture the publisher ships is REPORTED, never refused
  S3  a failure is stamped on the installation row as sync_error and re-raised
  S4  refusals carry their status: unknown listing 404, no grant 404, own pack 400,
      publisher missing a profile 409 (named)
  S5  a ONE-profile pack syncs (the "viable for agents" claim, proven)
  S6  a subscription that predates the ledger is recorded on its first sync
  S7  the fan-out reaches every grantee, a failing one never stops the next; scheduling
      needs a running loop and holds a strong reference
  S8  update_available: content stamp vs synced_at, never updated_at
  W   wiring: the sync route; both in-place update sites (import + fork) schedule the
      fan-out; publish + the listing update stamp content_updated_at and the SUBSCRIBE
      route does not; the migration exists and runs at init; browse and the installed
      list read the flag from the content stamp
  U   the UI: Sync on both marketplace card sites and the installed card, the chip, the
      handlers hit /sync; the two modules are version-pinned

Run: python3 test/test_pack_subscription_sync.py
"""
from __future__ import annotations
import ast, importlib, asyncio, hashlib, inspect, json, os, re, sqlite3, sys, tempfile, types, zipfile
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
    def __init__(self, *a, **k): self.args = a; self.kw = k
_gs.GraphStore = _GS
sys.modules.setdefault("components.builtin.knowledge_graph.graph_store", _gs)

from trusted_data_agent.core.agent_pack_manager import AgentPackManager as M  # noqa: E402
from trusted_data_agent.core import kg_subscription as K  # noqa: E402
from trusted_data_agent.core import config_manager as CM  # noqa: E402
from trusted_data_agent.core import collection_db as CDB  # noqa: E402
from trusted_data_agent.core import agent_pack_db as APDB  # noqa: E402
from trusted_data_agent.core import session_manager as SMOD  # noqa: E402
from trusted_data_agent.agent import rag_retriever as RR  # noqa: E402
from trusted_data_agent.api import agent_pack_routes as AR  # noqa: E402

def _run(coro):
    loop = asyncio.new_event_loop()
    try: return loop.run_until_complete(coro)
    finally: loop.close()

# ─────────────────────────────────────────────────────────────────────────── B: ids kept
class _FakeCM:
    def __init__(self, profiles):
        self.profiles = {p["id"]: p for p in profiles}
    def get_llm_configurations(self, user): return [{"id": "llm-sub"}]
    def get_profile(self, pid, user): return self.profiles.get(pid)
    def get_mcp_servers(self, user): return [{"id": "srv-sub", "name": "Teradata"}]
    def get_context_window_type(self, cid, user): return {"id": cid}
class _FakeCDB:
    def get_collection_by_id(self, cid): return None

def _build(pack_resources, publisher_profiles, existing_ids=None):
    orig_cm, orig_cdb = CM.get_config_manager, CDB.CollectionDatabase
    CM.get_config_manager = lambda: _FakeCM(publisher_profiles)
    CDB.CollectionDatabase = _FakeCDB
    try:
        return M.__new__(M).build_subscribed_profiles(
            pack_resources=pack_resources, publisher_user_id="pub", subscriber_user_id="sub",
            llm_config_id="llm-sub", mcp_server_id="srv-sub", source_installation_id=11,
            pack_published_at="2026-09-07T00:00:00+00:00", mcp_bindings=None, existing_ids=existing_ids)
    finally:
        CM.get_config_manager, CDB.CollectionDatabase = orig_cm, orig_cdb

def t_build_ids_kept():
    pub = [
        {"id": "pub-a", "tag": "A", "name": "A", "profile_type": "llm_only", "classification_mode": "light"},
        {"id": "pub-g", "tag": "G", "name": "G", "profile_type": "genie", "classification_mode": "light",
         "genieConfig": {"slaveProfiles": ["pub-a"], "maxIterations": 3}},
    ]
    res = [{"resource_type": "profile", "resource_id": "pub-a", "resource_tag": "A", "resource_role": "profile"},
           {"resource_type": "profile", "resource_id": "pub-g", "resource_tag": "G", "resource_role": "coordinator"}]
    built = _build(res, pub, existing_ids={"A": "profile-keep-a", "G": "profile-keep-g"})
    by = {p["tag"]: p for p in built}
    check("B1 an existing tag keeps its id", by["A"]["id"] == "profile-keep-a" and by["G"]["id"] == "profile-keep-g", {t: p["id"] for t, p in by.items()})
    check("B1 the coordinator's children resolve to the PRESERVED ids",
          by["G"].get("genieConfig", {}).get("slaveProfiles") == ["profile-keep-a"], by["G"].get("genieConfig"))
    check("B1 the subscription facts still ride along", by["A"].get("is_subscribed") is True and by["A"].get("source_pack_installation_id") == 11)
    partial = _build(res, pub, existing_ids={"A": "profile-keep-a"})
    byp = {p["tag"]: p for p in partial}
    check("B1 a tag with no kept id is minted, its coordinator still points at the kept child",
          byp["G"]["id"].startswith("profile-") and byp["G"]["id"] != "profile-keep-g"
          and byp["G"]["genieConfig"]["slaveProfiles"] == ["profile-keep-a"])
    minted = _build(res, pub)
    check("B1 absent existing_ids: every id minted (a fresh subscribe)", all(p["id"] != "profile-keep-a" and p["id"] != "profile-keep-g" for p in minted))
    one = _build([res[0]], pub[:1], existing_ids={"A": "profile-keep-a"})
    check("B2 a ONE-profile pack builds through the same seam, id kept", len(one) == 1 and one[0]["id"] == "profile-keep-a")

# ─────────────────────────────────────────────────────────── K: materializer update mode
def _kgdb(tmp, rows):
    path = os.path.join(tempfile.mkdtemp(dir=tmp), "t.db"); c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE kg_subscriptions (id TEXT PRIMARY KEY, user_uuid TEXT, source_kg_id TEXT, source_marketplace_id TEXT,
            target_profile_id TEXT, local_kg_id TEXT, enabled INTEGER, subscribed_at TEXT, last_synced_at TEXT,
            last_synced_export_hash TEXT, last_sync_error TEXT, source_kind TEXT NOT NULL DEFAULT 'kg');
        CREATE TABLE kg_metadata (kg_id TEXT, name TEXT, user_uuid TEXT, profile_id TEXT, is_active INTEGER, is_subscribed INTEGER, source_subscription_id TEXT);
        CREATE TABLE kg_profile_assignments (kg_id TEXT, kg_owner_profile_id TEXT, assigned_profile_id TEXT, user_uuid TEXT, is_active INTEGER, UNIQUE(kg_id, assigned_profile_id, user_uuid));
    """)
    for r in rows:
        c.execute("INSERT INTO kg_subscriptions VALUES (?,?,?,?,?,?,1,'t','t',?,NULL,?)",
                  (r["id"], "sub", r["src"], r["listing"], r["target"], r["local"], r["hash"], r.get("kind", "agent_pack")))
        c.execute("INSERT INTO kg_metadata VALUES (?,?,?,?,1,1,?)", (r["local"], r["name"], "sub", r["target"], r["id"]))
    c.commit(); c.close(); return path

def _zip(tmp, kgs):
    path = os.path.join(tmp, "pack.zip")
    manifest = {"name": "P", "knowledge_graphs": [], "skills": [{"skill_id": "s1"}], "member_trust": []}
    with zipfile.ZipFile(path, "w") as z:
        for i, kg in enumerate(kgs, 1):
            fn = f"knowledge_graphs/kg_{i}.json"
            z.writestr(fn, json.dumps({"name": kg["name"], "entities": kg.get("entities", [{"name": "E"}]), "relationships": []}))
            manifest["knowledge_graphs"].append({"ref": f"kg_{i}", "file": fn, "kg_id": kg["kg_id"], "profile_tag": kg["tag"],
                                                 "owner_profile_tag": kg["tag"], "name": kg["name"], "database_name": "db", "description": "d"})
        z.writestr("manifest.json", json.dumps(manifest))
    return path

def _materialize(mgr, zp, db, tag_map, update, fail_rebuild=()):
    calls = []
    async def fake_export(**kw): return zp
    mgr.export_pack = fake_export
    mgr._stamp_pack_kg_validation = lambda store, data: calls.append(("stamp", store.kw.get("kg_id"), data.get("name")))
    o_mat, o_reb, o_del, o_err = K.materialize_copy, K.rebuild_copy, K.delete_copy, K.record_sync_error
    def fake_mat(conn, **kw):
        calls.append(("materialize", kw["source_kg_id"], kw["export_hash"]))
        return {"subscription_id": "s-new", "local_kg_id": "local-new-" + kw["source_kg_id"], "kg_name": kw["export_data"]["kg_name"] + " (subscribed)",
                "active": True, "entities_added": 1, "relationships_added": 0}
    def fake_reb(conn, sub, export_data, export_hash):
        calls.append(("rebuild", sub["source_kg_id"], sub["local_kg_id"], export_hash))
        if sub["source_kg_id"] in fail_rebuild: raise RuntimeError("store down")
        conn.execute("UPDATE kg_subscriptions SET last_synced_export_hash = ? WHERE id = ?", (export_hash, sub["id"]))
        return {"entities_added": 1, "relationships_added": 0}
    def fake_del(conn, sub):
        calls.append(("delete", sub["source_kg_id"], sub["local_kg_id"]))
        conn.execute("DELETE FROM kg_subscriptions WHERE id = ?", (sub["id"],))
    def fake_err(conn, sid, msg): calls.append(("sync_error", sid, msg))
    K.materialize_copy, K.rebuild_copy, K.delete_copy, K.record_sync_error = fake_mat, fake_reb, fake_del, fake_err
    try:
        out = _run(mgr.materialize_subscription_members(
            subscriber_uuid="sub", publisher_uuid="pub", publisher_profile_ids=["p1"], pack_name="P",
            pack_listing_id="listing-1", tag_to_profile_id=tag_map, db_path=db, update=update))
    finally:
        K.materialize_copy, K.rebuild_copy, K.delete_copy, K.record_sync_error = o_mat, o_reb, o_del, o_err
    return out, calls

def t_materializer_update_mode():
    tmp = tempfile.mkdtemp()
    zp = _zip(tmp, [{"name": "One", "kg_id": "pub-1", "tag": "A"}, {"name": "Two", "kg_id": "pub-2", "tag": "B"},
                    {"name": "Three", "kg_id": "pub-3", "tag": "C"}])
    z = zipfile.ZipFile(zp)
    h1 = hashlib.sha256(z.read("knowledge_graphs/kg_1.json")).hexdigest()
    h2 = hashlib.sha256(z.read("knowledge_graphs/kg_2.json")).hexdigest()
    rows = [{"id": "s1", "src": "pub-1", "listing": "listing-1", "target": "s-a", "local": "local-1", "hash": h1, "name": "One (subscribed)"},
            {"id": "s2", "src": "pub-2", "listing": "listing-1", "target": "s-b", "local": "local-2", "hash": "stale", "name": "Two (subscribed)"},
            {"id": "s9", "src": "pub-gone", "listing": "listing-1", "target": "s-a", "local": "local-9", "hash": "x", "name": "Gone (subscribed)"},
            {"id": "sk", "src": "pub-1", "listing": "kg-listing", "target": "s-z", "local": "local-kg", "hash": "x", "name": "KG-marketplace copy", "kind": "kg"},
            {"id": "so", "src": "pub-1", "listing": "other-pack", "target": "s-z", "local": "local-other", "hash": "x", "name": "other pack's copy"}]
    db = _kgdb(tmp, rows); mgr = M.__new__(M)
    out, calls = _materialize(mgr, zp, db, {"A": "s-a", "B": "s-b", "C": "s-c"}, update=True)
    kinds = [c[0] for c in calls]
    check("K1 an unchanged hash keeps the copy: no rebuild, no stamp", not any(c[0] in ("rebuild", "materialize") and c[1] == "pub-1" for c in calls) and not any(c[0] == "stamp" and c[1] == "local-1" for c in calls))
    check("K1 ...and it is counted, the ledger still names it by its stored name", out["kgs_unchanged"] == 1 and "local-1" in out["kg_ids"] and "One (subscribed)" in out["kg_names"], out)
    reb = [c for c in calls if c[0] == "rebuild"]
    check("K1 a changed hash is REBUILT in place — same local id, the fresh hash", reb == [("rebuild", "pub-2", "local-2", h2)], reb)
    check("K1 ...and stamped after the rebuild", ("stamp", "local-2", "Two") in calls, [c for c in calls if c[0] == "stamp"])
    check("K1 a graph with no copy is materialized", [c for c in calls if c[0] == "materialize"] == [("materialize", "pub-3", hashlib.sha256(z.read("knowledge_graphs/kg_3.json")).hexdigest())])
    check("K1 a copy the pack no longer ships is deleted", [c for c in calls if c[0] == "delete"] == [("delete", "pub-gone", "local-9")])
    check("K1 a KG-marketplace copy and another pack's copy of the SAME source graph are never touched",
          not any(c[1] == "pub-1" and c[2] in ("local-kg", "local-other") for c in calls if c[0] in ("rebuild", "delete")))
    check("K1 counts", (out["kgs_rebuilt"], out["kgs_created"], out["kgs_removed"], out["kgs_unchanged"]) == (1, 1, 1, 1), out)
    check("K1 the ledger set = kept + rebuilt + new, in manifest order", out["kg_ids"] == ["local-1", "local-2", "local-new-pub-3"], out["kg_ids"])
    c = sqlite3.connect(db)
    check("K1 the rebuilt row carries the new hash (rebuild_copy's write, committed)", c.execute("SELECT last_synced_export_hash FROM kg_subscriptions WHERE id='s2'").fetchone()[0] == h2)
    check("K1 the deleted row is gone, the untouched ones remain", {r[0] for r in c.execute("SELECT id FROM kg_subscriptions").fetchall()} == {"s1", "s2", "sk", "so"})
    # K2 a failing rebuild
    db2 = _kgdb(tmp, rows[:2]); mgr2 = M.__new__(M)
    out2, calls2 = _materialize(mgr2, zp, db2, {"A": "s-a", "B": "s-b", "C": "s-c"}, update=True, fail_rebuild=("pub-2",))
    check("K2 a failed rebuild is a WARNING; the previous copy still stands in the ledger set; the error is recorded",
          any("'Two'" in w for w in out2["warnings"]) and "local-2" in out2["kg_ids"] and out2["kgs_rebuilt"] == 0
          and any(c[0] == "sync_error" and c[1] == "s2" for c in calls2), (out2, calls2))
    # K3 the P1 path
    db3 = _kgdb(tmp, rows[:3]); mgr3 = M.__new__(M)
    out3, calls3 = _materialize(mgr3, zp, db3, {"A": "s-a", "B": "s-b", "C": "s-c"}, update=False)
    check("K3 update=False never rebuilds or deletes and materializes every manifest graph (P1, byte-identical)",
          not any(c[0] in ("rebuild", "delete") for c in calls3) and sum(1 for c in calls3 if c[0] == "materialize") == 3
          and "kgs_rebuilt" not in out3, calls3)

# ───────────────────────────────────────────────────────────── S: the sync orchestration
class _SyncCM:
    """A config manager faithful in the dimension under test: the subscriber's profiles."""
    def __init__(self, profiles, publisher_ids=(), default=None):
        self.profiles = list(profiles); self.publisher_ids = set(publisher_ids); self.default = default
        self.updates = []; self.removed = []
    def get_profiles(self, user): return [dict(p) for p in self.profiles]
    def get_profile(self, pid, user):
        if pid in self.publisher_ids: return {"id": pid, "tag": "x"}
        return next((dict(p) for p in self.profiles if p["id"] == pid), None)
    def update_profile(self, pid, updates, user):
        self.updates.append((pid, dict(updates)))
        for p in self.profiles:
            if p["id"] == pid: p.update(updates); return True
        return False
    def add_profile(self, data, user): self.profiles.append(dict(data)); return True
    def remove_profile(self, pid, user): self.removed.append(pid); self.profiles = [p for p in self.profiles if p["id"] != pid]; return True
    def remove_profile_keys(self, pid, keys, user): return True
    def get_default_profile_id(self, user): return self.default
    def set_default_profile_id(self, v, user): self.default = v
    def get_llm_configurations(self, user): return [{"id": "llm-sub"}, {"id": "llm-2"}]
    def get_mcp_servers(self, user): return [{"id": "srv-sub", "name": "Teradata"}]

class _SyncManager:
    def __init__(self, posture_for=None, fail_materialize=False):
        self.calls = []; self.posture_for = posture_for or {}; self.fail_materialize = fail_materialize
    def build_subscribed_profiles(self, **kw):
        self.calls.append(("build", kw))
        out = []
        for r in kw["pack_resources"]:
            if r["resource_type"] != "profile": continue
            tag = r["resource_tag"]
            pid = (kw.get("existing_ids") or {}).get(tag) or f"profile-minted-{tag}"
            p = {"id": pid, "tag": tag, "name": tag, "profile_type": "llm_only", "llmConfigurationId": kw["llm_config_id"],
                 "mcpServerId": (kw.get("mcp_bindings") or {}).get(tag) or kw.get("mcp_server_id"),
                 "is_subscribed": True, "source_pack_installation_id": kw["source_installation_id"],
                 "subscribed_at": "NOW", "pack_published_at": kw.get("pack_published_at"),
                 "ragCollections": [7] if tag == "A" else []}
            if tag in self.posture_for: p["groundingConfig"] = self.posture_for[tag]
            out.append(p)
        return out
    def _upsert_pack_profile(self, cm, prof, profile_data, ledger_pid, user, tag_to_profile_id):
        self.calls.append(("upsert", prof["tag"], dict(profile_data), ledger_pid))
        tag_to_profile_id[prof["tag"]] = ledger_pid or profile_data["id"]
        if not ledger_pid: cm.add_profile(profile_data, user)
    async def materialize_subscription_members(self, **kw):
        self.calls.append(("materialize", kw))
        if self.fail_materialize: raise RuntimeError("export exploded")
        return {"manifest": {"member_trust": [{"kind": "agent"}], "knowledge_graphs": [], "skills": [{"skill_id": "sk-1"}]},
                "kg_ids": ["local-1", "local-2"], "kg_names": ["One (subscribed)", "Two (subscribed)"], "skill_ids": ["sk-1"],
                "kgs_created": 0, "kgs_rebuilt": 1, "kgs_unchanged": 1, "kgs_removed": 0, "warnings": ["w1"], "zip_path": None}
    def _record_installation(self, **kw): self.calls.append(("record", kw)); return 99
    def _reinstate_member_trust(self, manifest, installation_id): self.calls.append(("trust", installation_id)); return 5

class _FakePackDB:
    calls = []
    def __init__(self, *a, **k): pass
    def reconcile_pack_resources(self, iid, desired): _FakePackDB.calls.append((iid, desired)); return {"added": 1, "updated": 1, "removed": 1}

def _sync(profiles, pack_profiles, pack_collections=(), inst=None, listing=None, grant=True, publisher_missing=(),
          posture_for=None, fail_materialize=False, default=None, body=None):
    seams = {}
    calls = {"stamp": [], "coll": [], "delkg": [], "archive_p": [], "archive_c": [], "keyed": []}
    listing = listing or {"id": "L1", "name": "Pack", "source_installation_id": 11, "publisher_user_id": "pub",
                          "updated_at": "2026-09-07T10:00:00+00:00", "content_updated_at": "2026-09-07T09:00:00+00:00"}
    res = [{"resource_type": "profile", "resource_id": f"pub-{t}", "resource_tag": t, "resource_role": "coordinator" if t == "G" else "profile"} for t in pack_profiles]
    res += [{"resource_type": "collection", "resource_id": str(c), "resource_tag": f"col-{c}", "resource_role": "collection"} for c in pack_collections]
    cm = _SyncCM(profiles, publisher_ids={f"pub-{t}" for t in pack_profiles if t not in publisher_missing}, default=default)
    mgr = _SyncManager(posture_for=posture_for, fail_materialize=fail_materialize)
    async def _ap(pid, user): calls["archive_p"].append(pid); return {}
    async def _ac(cid, user): calls["archive_c"].append(cid); return {}
    patches = {
        (AR, "_sync_load_listing"): lambda pid: listing if pid == listing["id"] else None,
        (AR, "_sync_has_grant"): lambda u, s: grant,
        (AR, "_sync_publisher_resources"): lambda s: (res, {"description": "d", "version": "2.0.0", "author": "au", "coordinator_tag": "G" if "G" in pack_profiles else None, "pack_type": "bundle"}),
        (AR, "_sync_installation_row"): lambda u, s: inst,
        (AR, "_sync_stamp_installation"): lambda iid, **kw: calls["stamp"].append((iid, kw)),
        (AR, "_sync_collections"): lambda u, old, new: (calls["coll"].append((old, new)) or {"added": [c for c in new if c not in old], "removed": [c for c in old if c not in new]}),
        (AR, "_sync_delete_kg_copies_for_profiles"): lambda u, ids: (calls["delkg"].append(list(ids)) or len(ids)),
        (AR, "_key_planner_subscriptions"): lambda u, built: (calls["keyed"].append([p["tag"] for p in built]) or {7: "srv-sub"}),
        (AR, "_manager"): lambda: mgr,
        (CM, "get_config_manager"): lambda: cm,
        (APDB, "AgentPackDB"): _FakePackDB,
        (SMOD, "archive_sessions_by_profile"): _ap,
        (SMOD, "archive_sessions_by_collection"): _ac,
        (RR, "get_rag_retriever"): lambda: None,   # a keyed planner is loaded on demand — not here
    }
    saved = {k: getattr(*k) for k in patches}
    for (mod, name), v in patches.items(): setattr(mod, name, v)
    _FakePackDB.calls = []
    try:
        body = body or {}
        result = _run(AR.sync_pack_subscription("sub", listing["id"], **body))
        return result, cm, mgr, calls
    finally:
        for (mod, name), v in saved.items(): setattr(mod, name, v)

_EXISTING = [
    {"id": "id-a", "tag": "A", "profile_type": "llm_only", "is_subscribed": True, "source_pack_installation_id": 11,
     "llmConfigurationId": "llm-sub", "mcpServerId": "srv-sub", "subscribed_at": "2026-09-01T00:00:00+00:00",
     "groundingConfig": {"mode": "enforce"}},
    {"id": "id-b", "tag": "B", "profile_type": "llm_only", "is_subscribed": True, "source_pack_installation_id": 11,
     "llmConfigurationId": "llm-sub", "subscribed_at": "2026-09-01T00:00:00+00:00"},
    {"id": "id-own", "tag": "MINE", "profile_type": "llm_only"},
]
_INST = {"id": 13, "manifest": {"_source_installation_id": 11},
         "resources": [{"resource_type": "profile", "resource_id": "id-a", "resource_tag": "A", "resource_role": "profile", "is_owned": 0},
                       {"resource_type": "profile", "resource_id": "id-b", "resource_tag": "B", "resource_role": "profile", "is_owned": 0},
                       {"resource_type": "collection", "resource_id": "5", "resource_tag": "col-5", "resource_role": "collection", "is_owned": 0},
                       {"resource_type": "collection", "resource_id": "6", "resource_tag": "col-6", "resource_role": "collection", "is_owned": 0}]}

def t_sync_happy():
    r, cm, mgr, calls = _sync(_EXISTING, ["A", "C"], pack_collections=(6, 7), inst=_INST, posture_for={"A": {"mode": "enforce"}})
    build = next(c[1] for c in mgr.calls if c[0] == "build")
    check("S1 the copies' ids are handed to the builder (existing tags only)", build["existing_ids"] == {"A": "id-a"}, build["existing_ids"])
    check("S1 bindings derived from the copies: their LLM, their server per agent, the pack-level from the majority",
          build["llm_config_id"] == "llm-sub" and build["mcp_bindings"] == {"A": "srv-sub"} and build["mcp_server_id"] == "srv-sub", build)
    check("S1 the content stamp is what the copies are dated with", build["pack_published_at"] == "2026-09-07T09:00:00+00:00")
    ups = [c for c in mgr.calls if c[0] == "upsert"]
    a = next(c for c in ups if c[1] == "A")
    check("S1 an existing tag is updated IN PLACE under its ledger id", a[3] == "id-a")
    check("S1 ...the subscription's own facts are kept OUT of the merge",
          not ({"is_subscribed", "source_pack_installation_id", "subscribed_at", "pack_published_at"} & set(a[2].keys())), sorted(a[2].keys()))
    stamp = next(u for u in cm.updates if u[0] == "id-a")
    check("S1 ...and re-stamped after, the original subscribed_at kept",
          stamp[1].get("is_subscribed") is True and stamp[1].get("source_pack_installation_id") == 11
          and stamp[1].get("subscribed_at") == "2026-09-01T00:00:00+00:00" and stamp[1].get("pack_published_at") == "2026-09-07T09:00:00+00:00", stamp)
    c_ = next(c for c in ups if c[1] == "C")
    check("S1 a new tag is added (no ledger id) with its subscription facts", c_[3] is None and c_[2].get("is_subscribed") is True)
    check("S1 a dropped tag: sessions archived, its copies removed, the profile removed",
          calls["archive_p"] == ["id-b"] and calls["delkg"] == [["id-b"]] and cm.removed == ["id-b"], (calls["archive_p"], calls["delkg"], cm.removed))
    check("S1 the subscriber's own profile is untouched", any(p["id"] == "id-own" for p in cm.profiles) and "id-own" not in cm.removed)
    check("S1 collection references reconciled from the LEDGER's set to the pack's", calls["coll"] == [([5, 6], [6, 7])], calls["coll"])
    check("S1 ...sessions of a removed collection archived", calls["archive_c"] == ["5"], calls["archive_c"])
    check("S1 planners re-keyed from the built profiles", calls["keyed"] == [["A", "C"]])
    mat = next(c[1] for c in mgr.calls if c[0] == "materialize")
    check("S1 graphs synced in UPDATE mode, keyed to the listing, targets = the copies (kept + new ids)",
          mat["update"] is True and mat["pack_listing_id"] == "L1" and mat["tag_to_profile_id"] == {"A": "id-a", "C": "profile-minted-C"}, mat)
    iid, desired = _FakePackDB.calls[0]
    kinds = {(d["resource_type"], d["resource_id"]) for d in desired}
    check("S1 the ledger is reconciled on the subscriber's installation with EVERY member, none owned",
          iid == 13 and {("profile", "id-a"), ("profile", "profile-minted-C"), ("collection", "6"), ("collection", "7"),
                         ("knowledge_graph", "local-1"), ("knowledge_graph", "local-2"), ("skill", "sk-1")} <= kinds
          and all(d["is_owned"] is False for d in desired) and ("profile", "id-b") not in kinds, kinds)
    st = calls["stamp"]
    check("S1 synced_at stamped with the export's manifest (subscription facts kept) and no error",
          len(st) == 1 and st[0][0] == 13 and st[0][1].get("synced_at") and st[0][1]["manifest"].get("_source_installation_id") == 11
          and "member_trust" in st[0][1]["manifest"] and st[0][1].get("error") is None, st)
    check("S1 trust reinstated after the ledger names the members", mgr.calls[-1] == ("trust", 13))
    order = [c[0] for c in mgr.calls]
    check("S1 in that order: build → upserts → materialize → trust", order.index("build") < order.index("upsert") < order.index("materialize") < order.index("trust"), order)
    check("S1 the response says what happened",
          r["status"] == "synced" and r["profiles_updated"] == 1 and r["profiles_added"] == 1 and r["profiles_dropped"] == 1 and r["dropped_tags"] == ["B"]
          and r["collections_added"] == 1 and r["collections_removed"] == 1 and r["planner_keyed"] == 1
          and r["kgs_rebuilt"] == 1 and r["kgs_unchanged"] == 1 and r["kgs_removed"] == 1 and r["trust_reinstated"] == 5
          and r["skills_recorded"] == 1 and r["warnings"] == ["w1"] and r["posture_changes"] == [], r)
    check("S1 the body's overrides win over the derived bindings",
          _sync(_EXISTING, ["A"], inst=_INST, body={"llm_configuration_id": "llm-2", "mcp_bindings": {"A": "srv-x"}})[2].calls[0][1]["llm_config_id"] == "llm-2")
    r2, cm2, _, _ = _sync(_EXISTING, ["A"], inst=_INST, default="id-b")
    check("S1 a dropped default profile is cleared", cm2.default is None)

def t_sync_posture_reported():
    r, cm, mgr, calls = _sync(_EXISTING, ["A", "B"], inst=_INST, posture_for={"A": {"mode": "observe"}})
    check("S2 a weaker posture from the publisher is REPORTED, named per agent", r["status"] == "synced" and len(r["posture_changes"]) == 1 and r["posture_changes"][0].startswith("@A:"), r["posture_changes"])
    check("S2 ...and still applied (the publisher's copy is authoritative for a subscription)", any(c[0] == "upsert" and c[1] == "A" and c[2].get("groundingConfig", {}).get("mode") == "observe" for c in mgr.calls))

def t_sync_failure_stamp_calls():
    # the stamp seam receives error= on failure — and only that
    calls = []
    saved = (AR._sync_load_listing, AR._sync_has_grant, AR._sync_publisher_resources, AR._sync_installation_row, AR._sync_stamp_installation, AR._manager, CM.get_config_manager)
    listing = {"id": "L1", "name": "Pack", "source_installation_id": 11, "publisher_user_id": "pub", "updated_at": "t", "content_updated_at": "t"}
    AR._sync_load_listing = lambda pid: listing
    AR._sync_has_grant = lambda u, s: True
    AR._sync_publisher_resources = lambda s: ([{"resource_type": "profile", "resource_id": "pub-A", "resource_tag": "A", "resource_role": "profile"}], None)
    AR._sync_installation_row = lambda u, s: _INST
    AR._sync_stamp_installation = lambda iid, **kw: calls.append((iid, kw))
    AR._manager = lambda: _SyncManager(fail_materialize=True)
    CM.get_config_manager = lambda: _SyncCM(_EXISTING, publisher_ids={"pub-A"})
    try:
        o_coll, o_del, o_key = AR._sync_collections, AR._sync_delete_kg_copies_for_profiles, AR._key_planner_subscriptions
        AR._sync_collections = lambda u, o, n: {"added": [], "removed": []}
        AR._sync_delete_kg_copies_for_profiles = lambda u, ids: 0
        AR._key_planner_subscriptions = lambda u, b: {}
        o_ap, o_ac = SMOD.archive_sessions_by_profile, SMOD.archive_sessions_by_collection
        async def _n(*a): return {}
        SMOD.archive_sessions_by_profile = SMOD.archive_sessions_by_collection = _n
        try:
            try: _run(AR.sync_pack_subscription("sub", "L1"))
            except RuntimeError: pass
        finally:
            AR._sync_collections, AR._sync_delete_kg_copies_for_profiles, AR._key_planner_subscriptions = o_coll, o_del, o_key
            SMOD.archive_sessions_by_profile, SMOD.archive_sessions_by_collection = o_ap, o_ac
    finally:
        (AR._sync_load_listing, AR._sync_has_grant, AR._sync_publisher_resources, AR._sync_installation_row, AR._sync_stamp_installation, AR._manager, CM.get_config_manager) = saved
    check("S3 the failure is stamped on the installation row as sync_error, nothing else written",
          calls == [(13, {"error": "export exploded"})], calls)

def t_sync_refusals():
    def status(**kw):
        try: _sync(_EXISTING, ["A"], inst=_INST, **kw); return None
        except AR.PackSyncError as e: return (e.status, str(e), e.extra)
    saved = AR._sync_load_listing; AR._sync_load_listing = lambda pid: None
    try:
        try: _run(AR.sync_pack_subscription("sub", "NOPE")); check("S4 unknown listing 404", False)
        except AR.PackSyncError as e: check("S4 unknown listing 404", e.status == 404)
    finally: AR._sync_load_listing = saved
    check("S4 no grant is 404 (not subscribed)", status(grant=False)[0] == 404)
    check("S4 one's own pack is 400", status(listing={"id": "L1", "name": "Pack", "source_installation_id": 11, "publisher_user_id": "sub"})[0] == 400)
    st = status(publisher_missing=("A",))
    check("S4 a publisher missing a profile is 409 and NAMES it", st[0] == 409 and st[2].get("missing_profiles") == ["A"] and "@A" in st[1], st)

def t_sync_one_profile_pack():
    one = [{"id": "id-t", "tag": "T", "profile_type": "llm_only", "is_subscribed": True, "source_pack_installation_id": 11,
            "llmConfigurationId": "llm-sub", "subscribed_at": "s"}]
    inst = {"id": 21, "manifest": {"_source_installation_id": 11},
            "resources": [{"resource_type": "profile", "resource_id": "id-t", "resource_tag": "T", "resource_role": "profile", "is_owned": 0}]}
    r, cm, mgr, calls = _sync(one, ["T"], inst=inst)
    check("S5 a ONE-profile pack (an agent shared alone) syncs through the same path: updated in place, id kept",
          r["status"] == "synced" and r["profiles_updated"] == 1 and r["profiles_added"] == 0 and r["profiles_dropped"] == 0
          and any(c[0] == "upsert" and c[3] == "id-t" for c in mgr.calls), r)
    check("S5 ...with nothing invented for the members it lacks", calls["coll"] == [([], [])] and calls["delkg"] == [[]], calls)

def t_sync_no_ledger():
    r, cm, mgr, calls = _sync(_EXISTING[:1], ["A"], inst=None)
    rec = next((c[1] for c in mgr.calls if c[0] == "record"), None)
    check("S6 a subscription that predates the ledger is RECORDED on its first sync (is_owned False, every member)",
          rec is not None and rec["is_owned"] is False and rec["profile_ids"] == ["id-a"] and rec["kg_ids"] == ["local-1", "local-2"] and rec["skill_ids"] == ["sk-1"], rec)
    check("S6 ...then stamped synced_at on the new row, and trust reinstated there", calls["stamp"] == [(99, {"synced_at": calls["stamp"][0][1]["synced_at"]})] and ("trust", 99) in mgr.calls, calls["stamp"])
    check("S6 the pre-existing profile was still updated in place", any(c[0] == "upsert" and c[3] == "id-a" for c in mgr.calls))

def t_fan_out():
    saved = (AR._sync_load_listing, AR._sync_grantees, AR.sync_pack_subscription)
    seen = []
    async def fake_sync(user, pid, **kw):
        seen.append(user)
        if user == "u2": raise RuntimeError("boom")
        return {"status": "synced"}
    AR._sync_load_listing = lambda pid: {"id": pid, "source_installation_id": 11}
    AR._sync_grantees = lambda s: ["u1", "u2", "u3"]
    AR.sync_pack_subscription = fake_sync
    try:
        out = _run(AR.fan_out_pack_sync("L1"))
        check("S7 every grantee is reached, sequentially; a failing one never stops the next", seen == ["u1", "u2", "u3"] and out == {"synced": 2, "failed": 1}, (seen, out))
        check("S7 scheduling outside a running loop is a no-op (never raises)", AR.schedule_pack_subscriber_sync("L1") is None and AR.schedule_pack_subscriber_sync(None) is None)
        async def inside():
            t = AR.schedule_pack_subscriber_sync("L1")
            held = t in AR._PACK_SYNC_TASKS
            await t
            return held, t.result(), t in AR._PACK_SYNC_TASKS
        held, res, still = _run(inside())
        check("S7 scheduled inside a loop: a task, strongly held until done, released after", held and res["synced"] == 2 and not still)
    finally:
        AR._sync_load_listing, AR._sync_grantees, AR.sync_pack_subscription = saved

def t_update_available():
    ua = AR._update_available
    check("S8 content moved after the sync → available", ua("2026-09-07T10:00:00+00:00", "2026-09-07T09:00:00+00:00") is True)
    check("S8 synced after the content moved → not", ua("2026-09-07T09:00:00+00:00", "2026-09-07T10:00:00+00:00") is False)
    check("S8 equal → not (the sync took exactly that content)", ua("2026-09-07T09:00:00+00:00", "2026-09-07T09:00:00+00:00") is False)
    check("S8 absent either side → not (never an alarm the record cannot vouch for)", ua(None, "x") is False and ua("x", None) is False)
    check("S8 naive vs aware timestamps compare", ua("2026-09-07T10:00:00", "2026-09-07T09:00:00+00:00") is True)

# ────────────────────────────────────────────────────────────────────────── W: wiring
def t_wiring():
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "agent_pack_routes.py")).read()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    check("W the sync route exists on the listing", "sync_marketplace_agent_pack" in fns and '"/v1/marketplace/agent-packs/<pack_id>/sync"' in src)
    names = {n.func.id for n in ast.walk(fns["sync_marketplace_agent_pack"]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("W ...and calls the ONE core the fan-out calls", "sync_pack_subscription" in names)
    def sched_guarded(fn):
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "schedule_pack_subscriber_sync" for c in ast.walk(n)):
                if "updated_in_place" in ast.get_source_segment(src, n.test): return True
        return False
    check("W the import route fans out after an in-place update, and only then", sched_guarded(fns["import_agent_pack"]))
    check("W the fork route fans out after an in-place update, and only then", sched_guarded(fns["fork_marketplace_agent_pack"]))
    fan = ast.get_source_segment(src, fns["fan_out_pack_sync"])
    check("W the fan-out's population is the listing's grantees", "_sync_grantees(" in fan and "sync_pack_subscription(" in fan)
    pub = ast.get_source_segment(src, fns["publish_agent_pack"])
    check("W publish stamps content_updated_at", "content_updated_at" in pub)
    sub = ast.get_source_segment(src, fns["subscribe_marketplace_agent_pack"])
    check("W the SUBSCRIBE route bumps updated_at only — never the content stamp", "content_updated_at" not in sub and "updated_at = ?" in sub)
    browse = ast.get_source_segment(src, fns["browse_marketplace_agent_packs"])
    check("W browse derives update_available from the content stamp vs synced_at through the one helper",
          "_update_available(pack.get(\"content_updated_at\")" in browse and "synced_at" in browse)
    check("W ...never from updated_at", not re.search(r"_update_available\([^)]*updated_at\"\]", browse.replace("content_updated_at", "X")))
    lst = ast.get_source_segment(src, fns["list_agent_packs"])
    check("W the installed list reads the same flag for a subscribed copy", "_update_available(pack[\"content_updated_at\"]" in lst)
    msrc = open(os.path.join(ROOT, "src", "trusted_data_agent", "core", "agent_pack_manager.py")).read()
    check("W the listing update (the publisher's in-place update) stamps the content", "updated_at = ?, content_updated_at = ?" in msrc)
    check("W the materializer has its update mode; the builder its existing_ids", "update: bool = False" in msrc and "existing_ids: dict | None = None" in msrc)
    dsrc = open(os.path.join(ROOT, "src", "trusted_data_agent", "auth", "database.py")).read()
    check("W the columns are self-applying at init", "def _add_pack_subscription_sync_columns" in dsrc and "\n        _add_pack_subscription_sync_columns()" in dsrc
          and "ADD COLUMN content_updated_at" in dsrc and "ADD COLUMN synced_at" in dsrc and "ADD COLUMN sync_error" in dsrc)
    check("W ...backfilled from published_at (the honest lower bound), never updated_at", "content_updated_at = published_at" in dsrc)
    bat = open(os.path.join(ROOT, "test", "trust_battery.sh")).read()
    check("W the gate is in the battery", "test_pack_subscription_sync" in bat)

def t_ui():
    mh = open(os.path.join(ROOT, "static", "js", "handlers", "marketplaceHandler.js")).read()
    check("U Sync on the browse card and the My-Assets card (two sites, one builder)", mh.count("_packSyncButtonHtml(") >= 3 and "agent-pack-sync-btn" in mh)
    check("U the update-available chip beside Subscribed on both", mh.count("_packUpdateChipHtml(") >= 3 and "Update available" in mh)
    check("U the handler hits the sync route and reports posture changes", "/sync`" in mh and "posture_changes" in mh and "handleAgentPackSync" in mh)
    ah = open(os.path.join(ROOT, "static", "js", "handlers", "agentPackHandler.js")).read()
    check("U the installed subscribed card: Sync + chip + handler exported", "handleSyncAgentPack" in ah and ah.count("handleSyncAgentPack") >= 3 and "update_available" in ah and "/sync`" in ah)
    mainjs = open(os.path.join(ROOT, "static", "js", "main.js")).read()
    idx = open(os.path.join(ROOT, "templates", "index.html")).read()
    check("U marketplaceHandler is version-pinned where it is imported", re.search(r"marketplaceHandler\.js\?v=\d", mainjs) is not None)
    check("U agentPackHandler is version-pinned where it is loaded", re.search(r"agentPackHandler\.js'\) \}\}\?v=\d", idx) is not None)

def t_kg_card_pack_branch():
    """K — the KG card of a PACK-sourced copy follows the pack (D7): the list serves the
    pack's stamps, both card renderers route Sync to the PACK sync and offer no Unsubscribe,
    and the KG-marketplace sync/unsubscribe REFUSE an agent_pack row."""
    import sqlite3 as _sq
    KGR = importlib.import_module("trusted_data_agent.api.kg_marketplace_routes")
    def row(**k):
        c = _sq.connect(":memory:"); c.row_factory = _sq.Row
        cols = ", ".join(k); vals = ", ".join("?" * len(k))
        c.execute(f"CREATE TABLE t ({cols})"); c.execute(f"INSERT INTO t VALUES ({vals})", tuple(k.values()))
        return c.execute("SELECT * FROM t").fetchone()
    r = KGR.pack_managed_refusal(row(id="s1", source_marketplace_id="pack-L", source_kind="agent_pack"))
    check("K1 an agent_pack copy is refused by the KG-marketplace acts, naming the pack sync",
          r and r["error"] == "managed_by_agent_pack" and r["pack_listing_id"] == "pack-L" and "agent-packs/pack-L/sync" in r["message"], r)
    check("K1 a KG-listing copy is not refused", KGR.pack_managed_refusal(row(id="s2", source_marketplace_id="kg-L", source_kind="kg")) is None)
    check("K1 a legacy row without the column reads as a KG copy (never refused by accident)", KGR.pack_managed_refusal(row(id="s3", source_marketplace_id="kg-L")) is None)
    rsrc = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "kg_marketplace_routes.py")).read()
    tree = ast.parse(rsrc)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name, act in (("sync_marketplace_kg", "_read_export_with_hash"), ("unsubscribe_marketplace_kg", "_delete_subscription_local_kg")):
        calls = sorted(((n.lineno, n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")) for n in ast.walk(fns[name]) if isinstance(n, ast.Call)))
        names = [c[1] for c in calls]
        check(f"K2 {name} consults the refusal BEFORE it acts", "pack_managed_refusal" in names and act in names
              and names.index("pack_managed_refusal") < names.index(act), names)
    lsrc = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "rest_routes.py")).read()
    check("K3 the KG list serves source_kind and the pack's own stamps for a pack copy",
          'kg["source_kind"]' in lsrc and 'kg["pack_name"]' in lsrc and 'kg["pack_synced_at"]' in lsrc
          and "_kg_list_pack_subscription_context" in lsrc and "_update_available(row[\"content_updated_at\"], synced)" in lsrc)
    # the pack branch must LEAVE the loop body — the listing-hash comparison below it is the KG-listing copy's, not the pack's
    _ltree = ast.parse(lsrc)
    _pack_ifs = [n for n in ast.walk(_ltree) if isinstance(n, ast.If)
                 and "agent_pack" in ast.get_source_segment(lsrc, n.test)
                 and any('"pack_name"' in ast.get_source_segment(lsrc, b) for b in n.body)]
    check("K3 ...and never the KG-listing hash comparison for it (the pack branch ends in `continue`)",
          len(_pack_ifs) == 1 and isinstance(_pack_ifs[0].body[-1], ast.Continue),
          [type(b).__name__ for n in _pack_ifs for b in n.body])
    kh = open(os.path.join(ROOT, "static", "js", "handlers", "knowledgeGraphPanelHandler.js")).read()
    check("K4 both KG card renderers branch on the pack source", kh.count("kg.source_kind === 'agent_pack'") == 2)
    check("K4 both offer the PACK sync (one act, the marketplace module's) and not the KG-marketplace sync",
          "kg-pack-sync-btn" in kh and "kg-intel-pack-sync-btn" in kh and "window.syncAgentPackSubscription(" in kh and kh.count("data-pack-id=") == 2)
    # ── the agent-pack pill on the KG cards (the planner card's chip, same ledger) ──
    def _js_fn_body(fn_name):
        i = kh.find(f"function {fn_name}(")
        if i < 0:
            return ""
        b = kh[i:]
        j = b.find("\nfunction ", 10)
        return b if j < 0 else b[:j]
    check("the KG list stamps the caller's packs on every graph",
          '"knowledge_graph", str(kg.get("kg_id")' in lsrc, "no knowledge_graph pack lookup in kg_list_all")
    check("...scoped to the caller (one chip per pack, never per referencing account)",
          '"knowledge_graph", str(kg.get("kg_id")' in lsrc
          and "owner_user_id=user_uuid)" in lsrc[lsrc.index('"knowledge_graph", str(kg.get("kg_id")'):][:200])
    _pp = _js_fn_body("_packPills")
    check("one pill builder exists for the KG cards", bool(_pp) and "agent-pack-badge" in _pp)
    check("...rendering the pack name through the escaper", "_esc(p.name" in _pp)
    check("...and it reads kg.agent_packs", "kg.agent_packs" in _pp)
    for fn_name in ("createKnowledgeGraphCard", "createIntelligenceKGCard"):
        check(f"{fn_name} wears the pack pill beside the title",
              "${_packPills(kg)}" in _js_fn_body(fn_name))

    for fn_name in ("createKnowledgeGraphCard", "createIntelligenceKGCard"):
        body = kh[kh.index(f"function {fn_name}("):]
        body = body[:body.index("\nfunction ", 10)]
        check(f"K4 {fn_name}: a pack copy is never offered Unsubscribe on its own",
              "viaPack" in body and body.index("viaPack\n        ? `<span class=\"kg-pack-managed-note") < body.index("kg-unsubscribe-btn") if fn_name == "createKnowledgeGraphCard"
              else body.index("${viaPack ? `") < body.index("kg-intel-unsubscribe-btn"))
        check(f"K4 {fn_name}: the subline names the pack and the pack's last sync", "Subscribed via agent pack" in body and "Pack last synced" in body)
    check("K4 both dispatches reach the shared handler", kh.count("handleKgPanelPackSync(") >= 3)
    mh = open(os.path.join(ROOT, "static", "js", "handlers", "marketplaceHandler.js")).read()
    check("K5 the marketplace module publishes the pack-sync act for other surfaces", "window.syncAgentPackSubscription = " in mh and "handleAgentPackSync({ id: packListingId }, button)" in mh)
    mainjs = open(os.path.join(ROOT, "static", "js", "main.js")).read()
    check("K6 the KG handler is version-pinned where main.js imports it", re.search(r"knowledgeGraphPanelHandler\.js\?v=\d", mainjs) is not None)

def t_planner_card_pack_branch():
    """C — the PLANNER (and knowledge) card of a pack REFERENCE follows the pack (D7, the
    collection half): the pack chip is served once per CALLER (a reference is one collection
    id named by the publisher's ledger AND every subscriber's), the collection-marketplace
    unsubscribe REFUSES a reference held by one of the caller's pack subscriptions, and both
    card renderers offer "Removed with the pack" instead of an Unsubscribe of their own."""
    import sqlite3 as _sq
    from trusted_data_agent.core import agent_pack_db as _apdb
    tmp = tempfile.mkdtemp(); db = os.path.join(tmp, "t.db")
    c = _sq.connect(db)
    c.executescript("""
      CREATE TABLE agent_pack_installations (id INTEGER PRIMARY KEY, name TEXT, owner_user_id TEXT, manifest_json TEXT);
      CREATE TABLE agent_pack_resources (id INTEGER PRIMARY KEY, pack_installation_id INTEGER, resource_type TEXT, resource_id TEXT, resource_tag TEXT, resource_role TEXT, is_owned INTEGER);
      CREATE TABLE marketplace_agent_packs (id TEXT PRIMARY KEY, source_installation_id INTEGER);
      INSERT INTO agent_pack_installations VALUES (91, 'Policing Data Products', 'pub', '{}');
      INSERT INTO agent_pack_installations VALUES (92, 'Policing Data Products', 'sub', '{"_source_installation_id": 91}');
      INSERT INTO agent_pack_installations VALUES (93, 'Other Pack', 'sub', '{}');
      INSERT INTO agent_pack_resources VALUES (1, 91, 'collection', '382', 'planner_382', 'collection', 0);
      INSERT INTO agent_pack_resources VALUES (2, 92, 'collection', '382', 'planner_382', 'collection', 0);
      INSERT INTO agent_pack_resources VALUES (3, 93, 'collection', '500', 'planner_500', 'collection', 1);
      INSERT INTO marketplace_agent_packs VALUES ('listing-L', 91);
    """); c.commit(); c.close()
    pdb = _apdb.AgentPackDB.__new__(_apdb.AgentPackDB); pdb.db_path = db
    both = pdb.get_packs_for_resource("collection", "382")
    check("C1 unscoped, a referenced collection lists the pack once per account (the duplicate chip)", len(both) == 2, both)
    mine = pdb.get_packs_for_resource("collection", "382", owner_user_id="sub")
    check("C1 scoped to the caller it lists the pack ONCE", [p["id"] for p in mine] == [92], mine)
    check("C1 scoped to the publisher likewise once", [p["id"] for p in pdb.get_packs_for_resource("collection", "382", owner_user_id="pub")] == [91])
    check("C1 a stranger sees none", pdb.get_packs_for_resource("collection", "382", owner_user_id="zzz") == [])
    refs = pdb.subscription_packs_for_collection("sub", 382)
    check("C2 the caller's pack SUBSCRIPTION holding the collection as a reference is found, with its listing",
          refs == [{"installation_id": 92, "name": "Policing Data Products", "listing_id": "listing-L"}], refs)
    check("C2 an OWNED collection copy is not a reference", pdb.subscription_packs_for_collection("sub", 500) == [])
    check("C2 another user's reference is not the caller's", pdb.subscription_packs_for_collection("pub", 382) == [{"installation_id": 91, "name": "Policing Data Products", "listing_id": None}])
    RR = importlib.import_module("trusted_data_agent.api.rest_routes")
    r = RR.collection_pack_managed_refusal(refs)
    check("C3 a pack reference is refused by the collection-marketplace unsubscribe, naming the pack and its listing",
          r and r["error"] == "managed_by_agent_pack" and r["pack_listing_id"] == "listing-L" and "listing-L/subscribe" in r["message"] and r["pack_name"] == "Policing Data Products", r)
    check("C3 no pack reference → no refusal", RR.collection_pack_managed_refusal([]) is None and RR.collection_pack_managed_refusal(None) is None)
    rsrc = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "rest_routes.py")).read()
    tree = ast.parse(rsrc)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "unsubscribe_from_collection")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    ref_line = next((n.lineno for n in calls if isinstance(n.func, ast.Name) and n.func.id == "collection_pack_managed_refusal"), None)
    del_line = next((n.lineno for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == "delete"), None)
    # the 409 RETURN must precede the delete — a mutation that keeps the call early but moves
    # the `if _refusal: return` below the delete would pass a call-order check (it did)
    ret409 = next((n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)
                   and any(isinstance(e, ast.Constant) and e.value == 409 for e in n.value.elts)), None)
    check("C4 the unsubscribe route consults the refusal BEFORE it deletes the subscription", ref_line is not None and del_line is not None and ref_line < del_line, (ref_line, del_line))
    check("C4 ...and RETURNS 409 before the delete", ret409 is not None and ret409 < del_line, (ret409, del_line))
    check("C4 the collection list serves the caller's OWN packs", 'get_packs_for_resource("collection", str(coll["id"]), owner_user_id=user_uuid)' in rsrc)
    # C5 is EXECUTED: both card renderers run under a DOM stub (test/pack_collection_card_exec.mjs)
    # for a pack reference and for a plain marketplace subscription — a source-substring
    # check let `if (false && …)` through (recorded: the first version of this assertion).
    import subprocess as _sp
    _res = _sp.run(["node", os.path.join(ROOT, "test", "pack_collection_card_exec.mjs"), os.path.join(ROOT, "static", "js", "ui.js")],
                   capture_output=True, text=True, timeout=120)
    try:
        _cards = json.loads(_res.stdout.strip().splitlines()[-1])
    except Exception:
        _cards = {}
        check("C5 the card runner executes", False, (_res.returncode, _res.stderr[-300:]))
    for fn_name in ("createKnowledgeRepositoryCard", "createCollectionCard"):
        got = _cards.get(fn_name, {})
        check(f"C5 {fn_name}: a pack reference reads 'Removed with the pack' and is never offered Unsubscribe",
              got.get("packRef") == {"managed": True, "unsubscribe": False}, got)
        check(f"C5 {fn_name}: a plain marketplace subscription still offers Unsubscribe",
              got.get("plainSub") == {"managed": False, "unsubscribe": True}, got)

def t_pack_seal_everywhere():
    """The publisher's PACK seal reaches every copy — fork, subscribe AND sync — through ONE
    helper. Only the fork carried it before (Sep 2026): a subscriber's pack read unsigned."""
    import ast as _ast
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "agent_pack_routes.py")).read()
    tree = _ast.parse(src)
    def _calls_in(name):
        fn = next(n for n in _ast.walk(tree) if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == name)
        return {c.func.id for c in _ast.walk(fn) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)}
    for route in ("_sync_apply", "fork_marketplace_agent_pack", "subscribe_marketplace_agent_pack"):
        try:
            got = "_reinstate_pack_seal" in _calls_in(route)
        except StopIteration:
            got = None
        check(f"P1 {route} reinstates the publisher's pack seal", got is True, got)
    check("P2 no path reinstates the pack seal by hand (one helper)",
          src.count('capture("agent_pack"') == 1)
    import importlib
    m = importlib.import_module("trusted_data_agent.api.agent_pack_routes")
    from trusted_data_agent.core.trust import portability as _p, pack as _pk
    calls = []
    orig = (_p.capture, _p.reinstate, _pk.composition_hash_for)
    try:
        _p.capture = lambda kind, aid, *a, **k: {"version_hash": "h", "signature": "s", "src": aid}
        _p.reinstate = lambda kind, nid, claim, lh, *a, **k: calls.append((kind, nid, claim["src"], lh)) or lh == "h"
        _pk.composition_hash_for = lambda iid: "h" if iid == "33" else "other"
        check("P3 a matching copy takes the publisher's seal", m._reinstate_pack_seal(1, 33) is True)
        check("P3 the claim comes from the SOURCE and lands on the COPY", calls[-1][:3] == ("agent_pack", "33", "1"), calls)
        check("P4 a copy whose composition differs is refused by the honesty gate", m._reinstate_pack_seal(1, 34) is False)
        _p.capture = lambda *a, **k: None
        check("P5 an unsigned publisher pack reinstates nothing", m._reinstate_pack_seal(1, 33) is False)
        _p.capture = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        check("P6 a failure is soft (never breaks a sync)", m._reinstate_pack_seal(1, 33) is False)
    finally:
        _p.capture, _p.reinstate, _pk.composition_hash_for = orig


for fn in (t_build_ids_kept, t_materializer_update_mode, t_sync_happy, t_sync_posture_reported,
           t_sync_failure_stamp_calls, t_sync_refusals, t_sync_one_profile_pack, t_sync_no_ledger,
           t_fan_out, t_update_available, t_wiring, t_ui, t_kg_card_pack_branch, t_planner_card_pack_branch, t_pack_seal_everywhere):
    try: fn()
    except Exception as e:
        import traceback; traceback.print_exc()
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
