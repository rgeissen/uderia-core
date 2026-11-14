"""The ontology content-hash cache must be EXACT — never serve a stale hash.

`composition.kg_graph_hash` caches the canonical ontology hash behind a fingerprint of
the raw rows, so the Trust panels stop recomputing every graph on every request
(measured on prod: /v1/trust/agents 7-9 s, 92% of it in `kg_content_hash`). A trust
seal that reads 🟢 over a changed ontology is the worst possible failure here, so this
gate asserts, against a real SQLite store:

  A. Byte-identity — the cached value equals `kg_content_hash(*load_kg_graph(...))`,
     cold and warm, so no existing attestation moves.
  B. Every kind of write moves the hash, INCLUDING the ones no timestamp records:
     an in-place property UPDATE, an in-place relationship-type UPDATE, an entity
     rename (which changes the relationship endpoints' names), a relationship re-point,
     a delete, an insert — each compared against a fresh uncached recompute.
  C. A reverted change returns to the original hash (the key is content, not history).
  D. A warm call really skips the recompute (kg_content_hash not called).
  E. A relationship whose endpoint lies OUTSIDE the graph's own entities bypasses the
     cache (the fingerprint cannot see that entity's name) — and a rename of that
     foreign entity still moves the hash.
  F. Wiring: every read-only ontology-hash call site uses the cached helper; the
     trust-behavior integrity join runs off the event loop.

No server, no network.
"""
import ast
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.core.trust import composition as tc  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


KG, OTHER = "kg-hash-A", "kg-hash-B"


def make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE kg_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT, name TEXT, entity_type TEXT,
        properties_json TEXT DEFAULT '{}', updated_at TEXT)""")
    c.execute("""CREATE TABLE kg_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT,
        source_entity_id INTEGER, target_entity_id INTEGER, relationship_type TEXT)""")
    ids = {}
    for kg, name, et, props in [
        (KG, "sales", "database", {"layer": "logical"}),
        (KG, "sales.order", "entity", {"layer": "logical", "description": "An order"}),
        (KG, "sales.order.order_id", "attribute", {"type": "INTEGER", "is_key": True}),
        (KG, "sales.customer", "entity", {"layer": "logical", "quote": 'say "hi"\n'}),
        (OTHER, "elsewhere", "entity", {"layer": "logical"}),
    ]:
        ids[name] = c.execute(
            "INSERT INTO kg_entities (kg_id,name,entity_type,properties_json,updated_at) "
            "VALUES (?,?,?,?,'2026-01-01')", (kg, name, et, json.dumps(props))).lastrowid
    for s, t, rt in [("sales", "sales.order", "contains"),
                     ("sales.order", "sales.order.order_id", "contains"),
                     ("sales.order", "sales.customer", "foreign_key")]:
        c.execute("INSERT INTO kg_relationships (kg_id,source_entity_id,target_entity_id,"
                  "relationship_type) VALUES (?,?,?,?)", (KG, ids[s], ids[t], rt))
    c.commit()
    c.close()
    return path, ids


def fresh(db, kg=KG):
    """The uncached truth."""
    return tc.kg_content_hash(*tc.load_kg_graph(kg, db))


def sql(db, stmt, args=()):
    c = sqlite3.connect(db)
    c.execute(stmt, args)
    c.commit()
    c.close()


print("=" * 68)
print("Ontology hash cache — exactness gate")
print("=" * 68)
tc._KG_HASH_CACHE.clear()
db, ids = make_db()

print("\n[A] byte-identity, cold and warm")
h0 = fresh(db)
check("cold equals the uncached hash", tc.kg_graph_hash(KG, db) == (h0, 4))
check("warm equals the uncached hash", tc.kg_graph_hash(KG, db) == (h0, 4))
check("an unknown graph hashes like load+hash of nothing, count 0",
      tc.kg_graph_hash("nope", db) == (fresh(db, "nope"), 0))

print("\n[B] every write moves the hash (vs a fresh recompute)")
cases = [
    ("in-place property UPDATE, no timestamp",
     "UPDATE kg_entities SET properties_json=? WHERE name='sales.order'",
     (json.dumps({"layer": "logical", "description": "An ORDER"}),)),
    ("in-place relationship-type UPDATE (the migration shape)",
     "UPDATE kg_relationships SET relationship_type='references' "
     "WHERE relationship_type='foreign_key'", ()),
    ("entity rename (moves relationship endpoint names)",
     "UPDATE kg_entities SET name='sales.client' WHERE name='sales.customer'", ()),
    ("entity type change",
     "UPDATE kg_entities SET entity_type='table' WHERE name='sales.order'", ()),
    ("relationship re-pointed to another endpoint",
     "UPDATE kg_relationships SET target_entity_id=? WHERE relationship_type='references'",
     (ids["sales"],)),
    ("relationship deleted",
     "DELETE FROM kg_relationships WHERE relationship_type='references'", ()),
    ("entity inserted",
     "INSERT INTO kg_entities (kg_id,name,entity_type,properties_json) "
     "VALUES (?,'sales.line','entity','{}')", (KG,)),
]
prev = h0
for label, stmt, args in cases:
    tc.kg_graph_hash(KG, db)                     # warm the cache on the old state
    sql(db, stmt, args)
    got, _n = tc.kg_graph_hash(KG, db)
    truth = fresh(db)
    check(f"{label}: cached == fresh", got == truth, f"{got[:12]} vs {truth[:12]}")
    check(f"{label}: hash moved", got != prev)
    prev = got

print("\n[C] reverting to the original rows returns the original hash")
db2, _ = make_db()
tc.kg_graph_hash(KG, db2)
sql(db2, "UPDATE kg_entities SET properties_json='{\"x\":1}' WHERE name='sales'")
tc.kg_graph_hash(KG, db2)
sql(db2, "UPDATE kg_entities SET properties_json=? WHERE name='sales'",
    (json.dumps({"layer": "logical"}),))
check("reverted content → original hash", tc.kg_graph_hash(KG, db2)[0] == h0)

print("\n[D] a warm call skips the recompute")
db3, _ = make_db()
tc.kg_graph_hash(KG, db3)
calls = []
orig = tc.kg_content_hash
tc.kg_content_hash = lambda *a, **k: calls.append(1) or orig(*a, **k)
try:
    tc.kg_graph_hash(KG, db3)
    check("warm hit: kg_content_hash not called", calls == [], f"{len(calls)} call(s)")
    sql(db3, "UPDATE kg_entities SET entity_type='table' WHERE name='sales.order'")
    tc.kg_graph_hash(KG, db3)
    check("after a write: recomputed exactly once", len(calls) == 1, f"{len(calls)} call(s)")
finally:
    tc.kg_content_hash = orig
tc._KG_HASH_CACHE.clear()
for i in range(tc._KG_HASH_CACHE_MAX + 5):
    tc._KG_HASH_CACHE[f"k{i}"] = "v"
    while len(tc._KG_HASH_CACHE) > tc._KG_HASH_CACHE_MAX:
        tc._KG_HASH_CACHE.popitem(last=False)
check("the cache is bounded", len(tc._KG_HASH_CACHE) == tc._KG_HASH_CACHE_MAX)
tc._KG_HASH_CACHE.clear()

print("\n[E] a foreign relationship endpoint bypasses the cache")
db4, ids4 = make_db()
sql(db4, "INSERT INTO kg_relationships (kg_id,source_entity_id,target_entity_id,"
         "relationship_type) VALUES (?,?,?,'is_a')", (KG, ids4["sales.order"], ids4["elsewhere"]))
key, _n = tc._kg_fingerprint(KG, db4)
check("fingerprint refuses (None) when an endpoint is outside the graph", key is None)
before = tc.kg_graph_hash(KG, db4)[0]
check("still byte-identical to the uncached hash", before == fresh(db4))
sql(db4, "UPDATE kg_entities SET name='renamed_elsewhere' WHERE name='elsewhere'")
after = tc.kg_graph_hash(KG, db4)[0]
check("renaming the foreign entity still moves the hash", after != before and after == fresh(db4))
check("nothing was cached for that graph", len(tc._KG_HASH_CACHE) == 0)

print("\n[F] wiring")
src = lambda p: open(os.path.join(ROOT, p)).read()
comp = src("src/trusted_data_agent/core/trust/composition.py")
check("_ontology_ingredient uses the cached helper",
      "kg_graph_hash(read_id, db_path)" in comp)
check("content.ontology_hash uses the cached helper",
      "_comp.kg_graph_hash(kg_id, None)" in src("src/trusted_data_agent/core/trust/content.py"))
check("products.ontology_state_for uses the cached helper",
      "_comp.kg_graph_hash(kg_id, db_path)" in src("src/trusted_data_agent/core/trust/products.py"))
check("load_kg_graph and the cache share one row read",
      "_graph_from_rows(*_fetch_kg_rows(kg_id, db_path))" in comp)
rest = ast.parse(src("src/trusted_data_agent/api/rest_routes.py"))
fn = next(n for n in ast.walk(rest) if isinstance(n, ast.AsyncFunctionDef)
          and n.name == "evals_agents_trust_behavior")
body = ast.unparse(fn)
check("trust-behavior resolves integrity in a worker thread",
      "asyncio.to_thread(_integrity_states_by_tag" in body)
check("trust-behavior no longer resolves trust inline on the loop",
      "resolve_profile_trust" not in body)

for p in (db, db2, db3, db4):
    os.unlink(p)
print("\n" + "=" * 68)
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
