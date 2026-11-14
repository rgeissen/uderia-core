"""
Derived state dies with its knowledge graph — ONE registry, every deletion site.

History, because the shape of the defect is the point:

  * `kg_version_db.delete_versions_for_kg` had ZERO callers for months — every deleted
    KG left its snapshots behind (fixed 2026-09-08, one hand-written call per site).
  * `kg_entity_usage` (Phase E, Aug 2026) was a SECOND table keyed only on kg_id, and
    the per-site fix simply did not know about it: a week later production held 8,879
    usage rows, part of them for graphs that no longer existed. A per-site call list is
    a hand-maintained allow-list — the next derived table is forgotten at N sites.

So the guard is now two-sided and both halves are structural:
  (1) the REGISTRY (`core.kg_derived_state.PURGERS`) must cover every table keyed on
      kg_id that is not classified here with a reason — a new kg_id-keyed table fails
      this gate until it is either purged or deliberately retained;
  (2) every `DELETE FROM kg_metadata` site must call `purge_for_kg` — scoped to the
      ENCLOSING FUNCTION, not the file: the file-scoped scan passed a rollback in
      kg_subscription.py that purged nothing because the same file purged elsewhere.

Plus the behaviour: a real round trip on a temp SQLite (both tables purged, another
graph untouched), fail-soft per table (one failing purger never stops the other), and
the maintenance script that clears rows written before the fix.

Run: python3 test/test_kg_derived_state_cleanup.py
"""
import ast
import os
import re
import sqlite3
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "maintenance"))

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


SEARCH_ROOTS = [os.path.join(ROOT, "src", "trusted_data_agent"), os.path.join(ROOT, "components")]


def _py_files():
    for root in SEARCH_ROOTS:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(dirpath, f)


# ---------------------------------------------------------------------------
# (1) the registry covers every kg_id-keyed table that is not deliberately retained
# ---------------------------------------------------------------------------

#: kg_id-keyed tables that are NOT derived state, each with the reason it is not
#: purged by the registry. A table missing from BOTH this map and the registry fails
#: the gate — that is the whole point.
CLASSIFIED = {
    "kg_metadata": "the owning row itself — its DELETE is the deletion site",
    "kg_entities": "graph content — the backend's clear_graph() owns it (SQLite/Neo4j)",
    "kg_relationships": "graph content — the backend's clear_graph() owns it (SQLite/Neo4j)",
    "kg_lifecycle_audit": "an AUDIT trail; it outlives its subject by design",
    "kg_profile_assignments": "a BINDING (graph x agent), removed beside the metadata row by each site that "
                              "established one; a rollback / shadow drop never created any",
    "kg_subscriptions": "the subscription's own row, keyed on local_kg_id and removed by delete_copy by "
                        "subscription id — the subscription lifecycle owns it",
    "kg_content_rev": "a MONOTONIC content counter (trust.kg_revision): deleting it would let a graph "
                      "re-created under the same kg_id climb back to a revision a hash was cached at",
}
#: legacy / one-shot migration copies of graph tables, never live
IGNORED_DDL = {"kg_entities_new", "kg_metadata_new", "kg_relationships_new"}


def _kg_id_keyed_tables():
    """Every CREATE TABLE in schema/ + src/ + components/ whose columns include kg_id."""
    found = {}
    roots = [os.path.join(ROOT, "schema")] + SEARCH_ROOTS
    pat = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(kg_[a-z_]+)\s*\((.*?)\)\s*;?", re.I | re.S)
    for root in roots:
        for dirpath, _d, files in os.walk(root):
            for f in files:
                if not (f.endswith(".py") or f.endswith(".sql")):
                    continue
                src = open(os.path.join(dirpath, f), encoding="utf-8").read()
                for m in pat.finditer(src):
                    name, body = m.group(1), m.group(2)
                    body = re.sub(r"--[^\n]*", "", body)  # a comment naming kg_id is not a column
                    if re.search(r"\bkg_id\b", body):
                        found.setdefault(name, os.path.relpath(os.path.join(dirpath, f), ROOT))
    return found


def t_registry_covers_every_kg_id_keyed_table():
    from trusted_data_agent.core import kg_derived_state as kds
    reg = set(kds.derived_tables())
    check("the registry is non-empty", reg, reg)
    check("the registry names the versions table", "kg_versions" in reg, reg)
    check("the registry names the usage table (the table the per-site list forgot)",
          "kg_entity_usage" in reg, reg)
    keyed = _kg_id_keyed_tables()
    check("the DDL scan is not vacuous", len(keyed) >= 5, keyed)
    unclassified = sorted(t for t in keyed if t not in reg and t not in CLASSIFIED and t not in IGNORED_DDL)
    check("every kg_id-keyed table is either purged by the registry or classified with a reason",
          not unclassified, unclassified)
    both = sorted(reg & set(CLASSIFIED))
    check("no table is both purged and classified as retained", not both, both)
    missing_ddl = sorted(t for t in reg if t not in keyed)
    check("every registry table has DDL somewhere (a purger for a phantom table is a typo)",
          not missing_ddl, missing_ddl)


# ---------------------------------------------------------------------------
# (2) a temp-DB round trip through the REAL primitives
# ---------------------------------------------------------------------------

def _temp_db(monkey):
    """Point BOTH stores at one temp database; returns its path."""
    from trusted_data_agent.core import kg_version_db as kv
    from trusted_data_agent.kgstore import usage
    d = tempfile.mkdtemp(prefix="kgds-")
    path = os.path.join(d, "tda_auth.db")
    monkey.append(("kv._DB_PATH", kv, "_DB_PATH", kv._DB_PATH))
    monkey.append(("kv._SCHEMA_APPLIED", kv, "_SCHEMA_APPLIED", kv._SCHEMA_APPLIED))
    monkey.append(("usage._db_path", usage, "_db_path", usage._db_path))
    from pathlib import Path
    kv._DB_PATH = Path(path)
    kv._SCHEMA_APPLIED = False
    kv._ensure_schema()
    usage._db_path = lambda: path
    return path


def _restore(monkey):
    for _label, mod, attr, val in reversed(monkey):
        setattr(mod, attr, val)


def _counts(path, kg_id):
    conn = sqlite3.connect(path)
    try:
        v = conn.execute("SELECT COUNT(*) FROM kg_versions WHERE kg_id=?", (kg_id,)).fetchone()[0]
        u = conn.execute("SELECT COUNT(*) FROM kg_entity_usage WHERE kg_id=?", (kg_id,)).fetchone()[0]
        return v, u
    finally:
        conn.close()


def t_round_trip_purges_both_tables_and_only_for_that_kg():
    monkey = []
    try:
        path = _temp_db(monkey)
        from trusted_data_agent.core import kg_version_db as kv
        from trusted_data_agent.kgstore import usage
        from trusted_data_agent.core import kg_derived_state as kds
        for kg in ("kg-A", "kg-B"):
            kv.snapshot_kg(kg, "u1", [{"id": f"{kg}-e1", "name": "Incident", "entity_type": "entity"}], [], label="v1")
            usage.record(kg, ["Incident", "Offence"], "seeded")
            usage.record(kg, ["Incident"], "used")
        check("fixture: A has a version and usage rows", _counts(path, "kg-A") == (1, 2), _counts(path, "kg-A"))
        check("fixture: B has a version and usage rows", _counts(path, "kg-B") == (1, 2), _counts(path, "kg-B"))
        r = kds.purge_for_kg("kg-A")
        check("purge reports what it removed per table", r["removed"] == {"kg_versions": 1, "kg_entity_usage": 2,
                                                                       "kg_content_hash_cache": 0,
                                                                       "kg_content_memo": 0}, r)
        check("purge reports no errors on the happy path", r["errors"] == {}, r)
        check("A's versions AND usage are gone", _counts(path, "kg-A") == (0, 0), _counts(path, "kg-A"))
        check("B is untouched", _counts(path, "kg-B") == (1, 2), _counts(path, "kg-B"))
        r2 = kds.purge_for_kg("kg-A")
        check("a second purge is a clean no-op", r2["removed"] == {"kg_versions": 0, "kg_entity_usage": 0,
                                                                  "kg_content_hash_cache": 0,
                                                                  "kg_content_memo": 0} and not r2["errors"], r2)
        r3 = kds.purge_for_kg("")
        check("an empty kg_id purges nothing (never a wildcard delete)", r3 == {"removed": {}, "errors": {}}, r3)
        check("B still untouched after the empty-id call", _counts(path, "kg-B") == (1, 2), _counts(path, "kg-B"))
    finally:
        _restore(monkey)


def t_fail_soft_per_table():
    """One failing purger must not stop the others, and the failure is NAMED."""
    monkey = []
    try:
        path = _temp_db(monkey)
        from trusted_data_agent.kgstore import usage
        from trusted_data_agent.core import kg_derived_state as kds
        usage.record("kg-C", ["Incident"], "rendered")

        def boom(_kg):
            raise RuntimeError("disk on fire")
        monkey.append(("kds.PURGERS", kds, "PURGERS", kds.PURGERS))
        kds.PURGERS = (kds._Purger("kg_versions", boom),) + tuple(p for p in kds.PURGERS if p.table == "kg_entity_usage")
        try:
            r = kds.purge_for_kg("kg-C")
        except Exception as exc:  # noqa: BLE001
            check("purge_for_kg never raises", False, repr(exc))
            return
        check("the failing table is named in errors", "kg_versions" in r["errors"] and "disk on fire" in r["errors"]["kg_versions"], r)
        check("the OTHER table was still purged", r["removed"].get("kg_entity_usage") == 1, r)
        check("...and its rows are really gone", _counts(path, "kg-C")[1] == 0, _counts(path, "kg-C"))
    finally:
        _restore(monkey)


# ---------------------------------------------------------------------------
# (3) every deletion site calls the registry — scoped to the enclosing function
# ---------------------------------------------------------------------------

DELETE_RE = re.compile(r"DELETE\s+FROM\s+kg_metadata\b", re.I)


def _calls_in(node, name):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and f.attr == name) or (isinstance(f, ast.Name) and f.id == name):
                return True
    return False


def _deletion_sites():
    """[(relpath, function name, node)] for every function whose string literals delete a kg_metadata row."""
    sites = []
    for path in _py_files():
        src = open(path, encoding="utf-8").read()
        if not DELETE_RE.search(src):
            continue
        tree = ast.parse(src)
        parents = {}
        docstrings = set()
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
            body = getattr(parent, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(body[0].value)  # prose ABOUT the code is not the code
        seen = set()
        for n in ast.walk(tree):
            if n in docstrings:
                continue
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and DELETE_RE.search(n.value):
                fn = parents.get(n)
                while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = parents.get(fn)
                key = (path, fn.name if fn else "<module>", fn.lineno if fn else 0)
                if key in seen:
                    continue
                seen.add(key)
                sites.append((os.path.relpath(path, ROOT), key[1], fn if fn else tree))
    return sites


def t_every_deletion_site_calls_the_registry_purge():
    sites = _deletion_sites()
    check("the scan finds the known deletion sites (non-vacuous; 6 as of 2026-09-15)", len(sites) >= 6,
          [(p, f) for p, f, _ in sites])
    missing = [(p, f) for p, f, node in sites if not _calls_in(node, "purge_for_kg")]
    check("every function that deletes a kg_metadata row calls purge_for_kg in that SAME function",
          not missing, missing)
    direct = [(p, f) for p, f, node in sites
              if _calls_in(node, "delete_versions_for_kg") or _calls_in(node, "delete_for_kg")]
    check("no deletion site calls a table primitive directly — the registry is the one mechanism",
          not direct, direct)
    files = {p for p, _f, _n in sites}
    for expected in ("src/trusted_data_agent/api/rest_routes.py",
                     "src/trusted_data_agent/core/agent_pack_manager.py",
                     "src/trusted_data_agent/core/kg_subscription.py",
                     "src/trusted_data_agent/api/kg_marketplace_routes.py",
                     "src/trusted_data_agent/core/kg_release.py"):
        check(f"deletion site still present: {os.path.basename(expected)}", expected in files, sorted(files))
    sub = [f for p, f, _n in sites if p.endswith("kg_subscription.py")]
    check("kg_subscription.py has TWO sites (materialize rollback + delete_copy) — the file-scoped scan saw one",
          len(sub) == 2, sub)


def t_the_purge_calls_are_guarded_at_the_sites():
    """`purge_for_kg` never raises, but the IMPORT can (a partial deploy); the sites guard it."""
    for p, f, node in _deletion_sites():
        guarded = any(isinstance(n, ast.Try) and _calls_in(n, "purge_for_kg") for n in ast.walk(node))
        check(f"{os.path.basename(p)}:{f} guards the purge with try/except", guarded)


# ---------------------------------------------------------------------------
# (4) the maintenance script clears rows written before the fix
# ---------------------------------------------------------------------------

def t_maintenance_script_finds_and_purges_only_orphans():
    import purge_orphan_kg_derived_state as script
    d = tempfile.mkdtemp(prefix="kgds-orph-")
    path = os.path.join(d, "tda_auth.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE kg_metadata (kg_id TEXT PRIMARY KEY, user_uuid TEXT, name TEXT);
        CREATE TABLE kg_versions (version_id TEXT PRIMARY KEY, kg_id TEXT NOT NULL, user_id TEXT, payload TEXT,
                                  compressed INTEGER, content_hash TEXT, entity_count INTEGER, rel_count INTEGER,
                                  version_num INTEGER, label TEXT, created_at TEXT);
        CREATE TABLE kg_entity_usage (kg_id TEXT NOT NULL, entity_name TEXT NOT NULL, seeded_count INTEGER,
                                      rendered_count INTEGER, used_count INTEGER, last_used_at TEXT,
                                      PRIMARY KEY (kg_id, entity_name));
        INSERT INTO kg_metadata VALUES ('live', 'u', 'Live'), ('live::released', 'u', 'Live (released)');
        INSERT INTO kg_versions (version_id, kg_id, version_num) VALUES ('v1','live',1), ('v2','gone',1), ('v3','gone',2);
        INSERT INTO kg_entity_usage (kg_id, entity_name) VALUES ('live','Incident'), ('live::released','Incident'),
                                                              ('gone','Incident'), ('gone','Offence'), ('gone2','X');
    """)
    conn.commit()
    orphans = script.find_orphans(conn)
    check("script reports the orphaned versions by kg", orphans.get("kg_versions") == {"gone": 2}, orphans)
    check("script reports the orphaned usage by kg", orphans.get("kg_entity_usage") == {"gone": 2, "gone2": 1}, orphans)
    check("a released shadow with its own metadata row is NOT an orphan",
          "live::released" not in orphans.get("kg_entity_usage", {}), orphans)
    removed = script.purge(conn, orphans)
    check("script removes exactly the orphans", removed == {"kg_versions": 2, "kg_entity_usage": 3}, removed)
    live_v = conn.execute("SELECT COUNT(*) FROM kg_versions WHERE kg_id='live'").fetchone()[0]
    live_u = conn.execute("SELECT COUNT(*) FROM kg_entity_usage WHERE kg_id IN ('live','live::released')").fetchone()[0]
    check("live rows survive", (live_v, live_u) == (1, 2), (live_v, live_u))
    check("a second scan finds nothing", script.find_orphans(conn) == {}, script.find_orphans(conn))
    check("the script takes its table list FROM the registry, never a copy",
          script.derived_tables() == __import__("trusted_data_agent.core.kg_derived_state", fromlist=["x"]).derived_tables())
    conn.close()


def t_no_orphans_can_be_hidden_by_the_audit_shape():
    """The query shape that hid the versions orphans for months, asserted as a lesson:
    counting through kg_metadata returns 0 exactly when the orphan exists, so the
    script must count the derived table on its own and NOT EXISTS the metadata."""
    src = open(os.path.join(ROOT, "maintenance", "purge_orphan_kg_derived_state.py"), encoding="utf-8").read()
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    check("the orphan query uses NOT EXISTS over kg_metadata", "NOT EXISTS (SELECT 1 FROM kg_metadata" in body)
    check("...and never scopes the derived table THROUGH kg_metadata",
          "WHERE kg_id IN (SELECT kg_id FROM kg_metadata" not in body)


for fn in (t_registry_covers_every_kg_id_keyed_table,
           t_round_trip_purges_both_tables_and_only_for_that_kg,
           t_fail_soft_per_table,
           t_every_deletion_site_calls_the_registry_purge,
           t_the_purge_calls_are_guarded_at_the_sites,
           t_maintenance_script_finds_and_purges_only_orphans,
           t_no_orphans_can_be_hidden_by_the_audit_shape):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
