"""Gate: core/kg_subscription — ONE materializer for KG-listing and agent-pack subscriptions.

P0 of docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md: the three primitives were lifted verbatim
out of api/kg_marketplace_routes.py so the pack path can reuse them. The rules:

  S1  materialize: metadata first, is_subscribed set AFTER import_bulk (the store's
      read-only gate must not block the materialization itself), the subscription row
      carries source_kind, an assignment is made — ACTIVE unless the target profile
      already has an active graph (a subscriber's own is never displaced)
  S2  materialize failure: the metadata row it created is removed, the error raised
  S3  rebuild: clear then import through the one legitimate write (allow_subscribed_write),
      the "(subscribed)" suffix kept, sync stamps bumped, activation untouched
  S4  delete: graph data through the store, metadata rows gone, SQL fallback when the store
      cannot be reached
  S5  the routes DELEGATE — no second copy of any primitive survives in the routes
  S6  the two fan-outs are scoped by source_kind

Hermetic: a temp SQLite with the real 37_kg_subscriptions.sql applied, a fake GraphStore
injected as the module the core imports, the trust seam stubbed.

Run: python3 test/test_kg_subscription_core.py
"""
from __future__ import annotations
import ast, inspect, os, sqlite3, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

PASS = FAIL = 0
def check(name, cond, detail=None):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL {name}" + (f": {detail!r}" if detail is not None else ""))

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# ── hermetic seams, installed BEFORE the core module is imported ──
CALLS = []
class FakeGraphStore:
    """Faithful in the dimensions under test: set_kg_metadata WRITES the row the
    materializer later flips to is_subscribed; import_bulk can be made to fail;
    clear_graph refuses a subscribed graph unless allow_subscribed_write."""
    db_path = None
    fail_import = False
    fail_clear = False
    def __init__(self, profile_id, user_uuid, kg_id=None, kg_store_config_id=None, allow_subscribed_write=False):
        self.profile_id, self.user_uuid, self.kg_id = profile_id, user_uuid, kg_id
        self.allow = allow_subscribed_write
    def set_kg_metadata(self, name, database_name=None, description=None, is_active=True):
        CALLS.append(("set_kg_metadata", self.kg_id, name, is_active))
        c = sqlite3.connect(FakeGraphStore.db_path)
        c.execute("INSERT INTO kg_metadata (kg_id, profile_id, user_uuid, name, description, database_name, is_active, is_subscribed) VALUES (?,?,?,?,?,?,?,0)",
                  (self.kg_id, self.profile_id, self.user_uuid, name, description, database_name, 1 if is_active else 0))
        c.commit(); c.close()
    def import_bulk(self, entities, relationships):
        c = sqlite3.connect(FakeGraphStore.db_path)
        subscribed = c.execute("SELECT is_subscribed FROM kg_metadata WHERE kg_id = ?", (self.kg_id,)).fetchone()
        c.close()
        CALLS.append(("import_bulk", self.kg_id, len(entities), bool(subscribed and subscribed[0]), self.allow))
        if FakeGraphStore.fail_import: raise RuntimeError("import exploded")
        return {"entities_added": len(entities), "relationships_added": len(relationships)}
    def clear_graph(self):
        CALLS.append(("clear_graph", self.kg_id, self.allow))
        if FakeGraphStore.fail_clear: raise RuntimeError("backend down")
    def replace_model_layer(self, entities, relationships, keep_layers=None):
        # PROP-008 A1: the sync replaces the PUBLISHER's layer through ONE operation
        # (keeps the subscriber's authored layer). Faithful in the dimension under test:
        # refuses like import_bulk, fails like import_bulk.
        c = sqlite3.connect(FakeGraphStore.db_path)
        subscribed = c.execute("SELECT is_subscribed FROM kg_metadata WHERE kg_id = ?", (self.kg_id,)).fetchone()
        c.close()
        CALLS.append(("replace_model_layer", self.kg_id, len(entities), bool(subscribed and subscribed[0]), self.allow))
        if FakeGraphStore.fail_import: raise RuntimeError("import exploded")
        return {"entities_added": len(entities), "relationships_added": len(relationships)}
# Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
# a double unfaithful in the dimension under test measures nothing (Sep 2026).
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from kg_double_fidelity import double_mismatches as _dm
from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
check("FakeGraphStore double matches the real GraphStore calling convention", not _dm(_RealGS, FakeGraphStore), _dm(_RealGS, FakeGraphStore))

_gs_mod = types.ModuleType("components.builtin.knowledge_graph.graph_store"); _gs_mod.GraphStore = FakeGraphStore
sys.modules["components.builtin.knowledge_graph.graph_store"] = _gs_mod
_port = types.ModuleType("trusted_data_agent.core.trust.portability"); _port.reinstate_kind = lambda *a, **k: False
_store = types.ModuleType("trusted_data_agent.core.trust.store"); _store.revoke = lambda *a, **k: CALLS.append(("revoke",) + a)
import trusted_data_agent.core.trust as _trust_pkg  # noqa: E402
sys.modules["trusted_data_agent.core.trust.portability"] = _port; sys.modules["trusted_data_agent.core.trust.store"] = _store
_trust_pkg.portability = _port; _trust_pkg.store = _store

from trusted_data_agent.core import kg_subscription as K  # noqa: E402

def _db():
    path = os.path.join(tempfile.mkdtemp(prefix="kgsub_"), "t.db")
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE kg_metadata (kg_id TEXT PRIMARY KEY, profile_id TEXT, user_uuid TEXT, name TEXT, description TEXT,
        database_name TEXT, is_active INTEGER DEFAULT 1, updated_at TEXT, kg_store_config_id TEXT);
    CREATE TABLE kg_profile_assignments (kg_id TEXT, kg_owner_profile_id TEXT, assigned_profile_id TEXT, user_uuid TEXT,
        is_active INTEGER, UNIQUE(kg_id, assigned_profile_id, user_uuid));
    CREATE TABLE kg_entities (kg_id TEXT); CREATE TABLE kg_relationships (kg_id TEXT);
    CREATE TABLE marketplace_knowledge_graphs (id TEXT PRIMARY KEY, name TEXT);
    """)
    for stmt in open(os.path.join(ROOT, "schema", "37_kg_subscriptions.sql")).read().split(";"):
        s = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if not s: continue
        try: c.execute(s)
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower(): raise
    c.commit(); c.close()
    FakeGraphStore.db_path = path
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    return path, conn

EXPORT = {"kg_name": "Policing — NIBRS", "kg_description": "d", "kg_database_name": "NIBRS_Source_Domain",
          "entities": [{"name": "Incident"}, {"name": "Victim"}], "relationships": [{"a": 1}], "trust": None}

def t_materialize():
    path, conn = _db(); CALLS.clear()
    r = K.materialize_copy(conn, db_path=path, user_uuid="sub-u", target_profile_id="p-nibrs", source_kg_id="pub-kg",
                           source_marketplace_id="pack-listing", source_kind=K.SOURCE_AGENT_PACK, export_data=dict(EXPORT), export_hash="h1")
    conn.commit()
    row = conn.execute("SELECT * FROM kg_subscriptions").fetchone()
    check("S1 the subscription row is written with its source_kind", row and row["source_kind"] == "agent_pack" and row["source_marketplace_id"] == "pack-listing" and row["source_kg_id"] == "pub-kg")
    meta = conn.execute("SELECT * FROM kg_metadata WHERE kg_id = ?", (r["local_kg_id"],)).fetchone()
    check("S1 the copy is marked subscribed and points at its subscription", meta and meta["is_subscribed"] == 1 and meta["source_subscription_id"] == r["subscription_id"])
    check("S1 is_subscribed is set AFTER import_bulk (the read-only gate never sees the materialization)",
          any(c[0] == "import_bulk" and c[3] is False for c in CALLS))
    check("S1 the name carries the (subscribed) suffix once", r["kg_name"] == "Policing — NIBRS (subscribed)" and meta["name"] == r["kg_name"])
    asg = conn.execute("SELECT * FROM kg_profile_assignments WHERE kg_id = ?", (r["local_kg_id"],)).fetchone()
    check("S1 assigned to the target profile, ACTIVE (no active graph there)", asg and asg["assigned_profile_id"] == "p-nibrs" and asg["is_active"] == 1 and r["active"] is True)
    e2 = dict(EXPORT, entities=[{"name": "X"}]); K.materialize_copy(conn, db_path=path, user_uuid="sub-u", target_profile_id="p-other", source_kg_id="k2",
        source_marketplace_id="kg-listing", source_kind=K.SOURCE_KG, export_data=e2, export_hash="h2"); conn.commit()
    check("S1 ...tagged in place on the entities handed to the store", e2["entities"][0].get("source") == "marketplace")
    # a target with an ACTIVE graph already: install inactive, never displace
    conn.execute("INSERT INTO kg_metadata (kg_id, profile_id, user_uuid, name, is_active, is_subscribed) VALUES ('own','p-busy','sub-u','own',1,0)"); conn.commit()
    r3 = K.materialize_copy(conn, db_path=path, user_uuid="sub-u", target_profile_id="p-busy", source_kg_id="k3", source_marketplace_id="l3",
                            source_kind=K.SOURCE_KG, export_data=dict(EXPORT), export_hash="h3"); conn.commit()
    asg3 = conn.execute("SELECT is_active FROM kg_profile_assignments WHERE kg_id = ?", (r3["local_kg_id"],)).fetchone()
    check("S1 a subscriber's own active graph is never displaced (copy installed inactive)", r3["active"] is False and asg3["is_active"] == 0)

def t_materialize_failure():
    path, conn = _db(); CALLS.clear(); FakeGraphStore.fail_import = True
    try:
        raised = False
        try: K.materialize_copy(conn, db_path=path, user_uuid="u", target_profile_id="p", source_kg_id="k", source_marketplace_id="l",
                                source_kind=K.SOURCE_KG, export_data=dict(EXPORT), export_hash="h")
        except RuntimeError: raised = True
        check("S2 an import failure is raised, not swallowed", raised)
        check("S2 ...and the metadata row it created is gone", conn.execute("SELECT COUNT(*) FROM kg_metadata").fetchone()[0] == 0)
        check("S2 ...and no subscription row was written", conn.execute("SELECT COUNT(*) FROM kg_subscriptions").fetchone()[0] == 0)
    finally:
        FakeGraphStore.fail_import = False

def t_rebuild():
    path, conn = _db(); CALLS.clear()
    r = K.materialize_copy(conn, db_path=path, user_uuid="u", target_profile_id="p", source_kg_id="k", source_marketplace_id="l",
                           source_kind=K.SOURCE_AGENT_PACK, export_data=dict(EXPORT), export_hash="h1"); conn.commit()
    conn.execute("UPDATE kg_profile_assignments SET is_active = 0 WHERE kg_id = ?", (r["local_kg_id"],)); conn.commit()  # subscriber deactivated it
    sub = conn.execute("SELECT * FROM kg_subscriptions WHERE id = ?", (r["subscription_id"],)).fetchone()
    CALLS.clear()
    out = K.rebuild_copy(conn, sub, dict(EXPORT, kg_name="Policing — NIBRS v2", entities=[{"name": "A"}, {"name": "B"}, {"name": "C"}]), "h2"); conn.commit()
    # PROP-008 A1: the rebuild is ONE replace (the subscriber's authored layer kept), never clear + import
    check("S3 rebuild is one replace of the publisher's layer", [c[0] for c in CALLS][:1] == ["replace_model_layer"] and out["entities_added"] == 3
          and not any(c[0] in ("clear_graph", "import_bulk") for c in CALLS))
    check("S3 ...through the one legitimate write (allow_subscribed_write)", CALLS[0][4] is True)
    meta = conn.execute("SELECT name FROM kg_metadata WHERE kg_id = ?", (r["local_kg_id"],)).fetchone()
    check("S3 the (subscribed) suffix is kept on the refreshed name", meta["name"] == "Policing — NIBRS v2 (subscribed)")
    sub2 = conn.execute("SELECT * FROM kg_subscriptions WHERE id = ?", (r["subscription_id"],)).fetchone()
    check("S3 sync stamps bumped, error cleared", sub2["last_synced_export_hash"] == "h2" and sub2["last_sync_error"] is None)
    asg = conn.execute("SELECT is_active FROM kg_profile_assignments WHERE kg_id = ?", (r["local_kg_id"],)).fetchone()
    check("S3 the subscriber's activation choice survives a sync", asg["is_active"] == 0)
    check("S3 an unsigned source revokes the copy's attestation", any(c[0] == "revoke" for c in CALLS))
    K.record_sync_error(conn, r["subscription_id"], "boom")
    check("S3 record_sync_error stamps the row", conn.execute("SELECT last_sync_error FROM kg_subscriptions WHERE id = ?", (r["subscription_id"],)).fetchone()[0] == "boom")

def t_delete():
    path, conn = _db(); CALLS.clear()
    r = K.materialize_copy(conn, db_path=path, user_uuid="u", target_profile_id="p", source_kg_id="k", source_marketplace_id="l",
                           source_kind=K.SOURCE_AGENT_PACK, export_data=dict(EXPORT), export_hash="h1"); conn.commit()
    sub = conn.execute("SELECT * FROM kg_subscriptions WHERE id = ?", (r["subscription_id"],)).fetchone()
    K.delete_copy(conn, sub); conn.commit()
    gone = all(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE {k} = ?", (v,)).fetchone()[0] == 0
               for t, k, v in (("kg_metadata", "kg_id", r["local_kg_id"]), ("kg_profile_assignments", "kg_id", r["local_kg_id"]), ("kg_subscriptions", "id", r["subscription_id"])))
    check("S4 delete removes the copy's metadata, assignment and subscription", gone and any(c[0] == "clear_graph" for c in CALLS))
    # store unreachable -> SQL fallback, rows still gone
    r2 = K.materialize_copy(conn, db_path=path, user_uuid="u", target_profile_id="p", source_kg_id="k", source_marketplace_id="l",
                            source_kind=K.SOURCE_KG, export_data=dict(EXPORT), export_hash="h1"); conn.commit()
    sub2 = conn.execute("SELECT * FROM kg_subscriptions WHERE id = ?", (r2["subscription_id"],)).fetchone()
    FakeGraphStore.fail_clear = True
    try: K.delete_copy(conn, sub2); conn.commit()
    finally: FakeGraphStore.fail_clear = False
    check("S4 a store that cannot be reached falls back to SQL and the rows are still gone",
          conn.execute("SELECT COUNT(*) FROM kg_subscriptions").fetchone()[0] == 0)

def t_routes_delegate():
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "kg_marketplace_routes.py")).read()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    def calls(fn): return {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    check("S5 the subscribe route materializes through the shared primitive", "materialize_copy" in calls(fns["subscribe_marketplace_kg"]))
    check("S5 ...and no longer builds the copy itself", "import_bulk" not in calls(fns["subscribe_marketplace_kg"]) and "set_kg_metadata" not in calls(fns["subscribe_marketplace_kg"]))
    check("S5 the rebuild helper delegates", "rebuild_copy" in calls(fns["_rebuild_subscription_kg"]) and "import_bulk" not in calls(fns["_rebuild_subscription_kg"]))
    check("S5 the delete helper delegates", "delete_copy" in calls(fns["_delete_subscription_local_kg"]) and "clear_graph" not in calls(fns["_delete_subscription_local_kg"]))
    check("S6 the KG fan-out reads KG-listing rows only", "AND source_kind = 'kg'" in ast.get_source_segment(src, fns["_fanout_auto_sync"]))
    check("S6 the unpublish cascade reads KG-listing rows only", "source_marketplace_id = ? AND source_kind = 'kg'" in ast.get_source_segment(src, fns["unpublish_knowledge_graph"]))
    check("S6 the schema declares source_kind with the legacy default", "ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'kg'" in open(os.path.join(ROOT, "schema", "37_kg_subscriptions.sql")).read())

for fn in (t_materialize, t_materialize_failure, t_rebuild, t_delete, t_routes_delegate):
    try: fn()
    except Exception as e:
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
