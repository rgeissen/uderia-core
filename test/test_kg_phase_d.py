"""
KG program — Phase D gate (storage hygiene).

  D1  get_stats() answers "has cycles" with a bounded find_cycle (it enumerated EVERY
      simple cycle, on the export path); detect_cycles() is bounded; the in-memory graph
      cache is LRU-bounded (it kept every graph ever touched alive forever).
  D2  The ANDP source binds the request-supplied product name (two sites interpolated it).
  D3  The trusted-scope cache the grounding gate reads is keyed on a content signature, so
      a rebuild/import invalidates it (it lived until process restart); extraction runs
      off the event loop.
  D4  apply-update restores the pre-sync graph when the import fails (it left an empty graph).
  D5  schema/21 declares the live shape (kg_id-scoped), idempotently.

Run:  PYTHONPATH=src python3 test/test_kg_phase_d.py
"""
import inspect
import os
import sqlite3
import sys
import re
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import sqlite_backend as sb            # noqa: E402
from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend  # noqa: E402
from components.builtin.knowledge_graph import handler as kgh          # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {('— ' + str(detail)) if detail else ''}")


def fresh_backend():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    c = sqlite3.connect(tmp)
    c.executescript(open(os.path.join(os.path.dirname(__file__), "..", "schema", "21_knowledge_graph.sql")).read())
    c.commit(); c.close()
    return SQLiteKGBackend({"db_path": tmp}) if "config" in inspect.signature(SQLiteKGBackend.__init__).parameters else SQLiteKGBackend(tmp)


print("\n[D5] schema/21 is the live shape and bootstraps a working backend")
be = fresh_backend()
r = be.import_bulk("k1", "p", "u", [{"name": "A", "entity_type": "entity"}, {"name": "B", "entity_type": "entity"}],
                   [{"source_name": "A", "source_type": "entity", "target_name": "B", "target_type": "entity", "relationship_type": "foreign_key"}])
check("a backend on the regenerated schema imports (kg_id-scoped UNIQUE)", r["entities_added"] == 2 and r["relationships_added"] == 1, r)
sql = open(os.path.join(os.path.dirname(__file__), "..", "schema", "21_knowledge_graph.sql")).read()
check("schema declares UNIQUE(kg_id, name, entity_type), not the pre-kg_id constraint", "UNIQUE(kg_id, name, entity_type)" in sql.replace(" ", "").replace("UNIQUE(kg_id,name,entity_type)", "UNIQUE(kg_id, name, entity_type)") or "kg_id, name, entity_type" in sql)
check("…idempotent (IF NOT EXISTS everywhere)", sql.count("CREATE TABLE IF NOT EXISTS") == 2 and "CREATE INDEX IF NOT EXISTS" in sql)

print("\n[D1] bounded cycle detection; LRU-bounded graph cache")
be.import_bulk("k1", "p", "u", [], [{"source_name": "B", "source_type": "entity", "target_name": "A", "target_type": "entity", "relationship_type": "foreign_key"}])
st = be.get_stats("k1")
check("get_stats reports has_cycles=True on a 2-cycle", st.get("has_cycles") is True, st)
check("get_stats no longer enumerates every simple cycle", "list(nx.simple_cycles(G))[:1]" not in inspect.getsource(be.get_stats) and "_has_cycle(G)" in inspect.getsource(be.get_stats))
check("detect_cycles is bounded (islice), never a full enumeration", "islice(nx.simple_cycles(G), 20)" in inspect.getsource(be.detect_cycles))
# a dense mutual-FK graph that would explode a full enumeration finishes instantly
big = fresh_backend()
ents = [{"name": f"T{i}", "entity_type": "entity"} for i in range(14)]
rels = [{"source_name": f"T{i}", "source_type": "entity", "target_name": f"T{j}", "target_type": "entity", "relationship_type": "foreign_key"}
        for i in range(14) for j in range(14) if i != j]
big.import_bulk("k2", "p", "u", ents, rels)
import time
t0 = time.perf_counter(); st2 = big.get_stats("k2"); dt = time.perf_counter() - t0
check(f"get_stats on a 14-node complete digraph (astronomically many cycles) returns in {dt:.2f}s", st2.get("has_cycles") is True and dt < 5.0, dt)
for i in range(sb._GRAPH_CACHE_MAX + 3):
    big.import_bulk(f"g{i}", "p", "u", [{"name": "X", "entity_type": "entity"}], [])
    big._get_graph(f"g{i}")
check(f"graph cache bounded at {sb._GRAPH_CACHE_MAX} (LRU)", len(big._graph_cache) == sb._GRAPH_CACHE_MAX, len(big._graph_cache))
check("the most recently used graph survives eviction", f"g{sb._GRAPH_CACHE_MAX + 2}" in big._graph_cache and "g0" not in big._graph_cache)

print("\n[D2] ANDP source binds the product name")
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "kg_constructors", "andp_semantic_source.py")).read()
check("registry lookup is parameterised", "product_name = ?" in src and "product_name = '{product}'" not in src)
check("dictionary scan is parameterised", 'DatabaseName LIKE ?' in src and "LIKE '{product}%'" not in src)

print("\n[D3] trusted-scope cache invalidates on rebuild; extraction off the event loop")
hsrc = open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin", "knowledge_graph", "handler.py")).read()
check("scope cache entries carry a content signature", "self._TRUSTED_SCOPE_CACHE[kg_id] = (_sig, result)" in hsrc and "_hit[0] == _sig" in hsrc)
# The signature is (row count, max updated_at), so it moves on any write. It now lives
# beside the semantic index it also keys — ONE definition for the read side (look the
# index up) and the write side (build it when the graph changes); two would key a build
# one way and a lookup the other, so every query would rebuild and never hit. Asserted
# as the PROPERTY at its home, plus the absence of a second copy: the previous form
# pinned the SQL literal to handler.py and broke when the duplication was removed.
_sisrc = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                           "kgstore", "semantic_index.py")).read()
check("the graph signature reads (count, max updated_at) — changes on any write",
      "SELECT COUNT(*), MAX(updated_at) FROM kg_entities WHERE kg_id=?" in _sisrc)
check("the handler keeps no second copy of it, it delegates",
      "SELECT COUNT(*), MAX(updated_at)" not in hsrc and "graph_signature(kg_id)" in hsrc)
try:
    from trusted_data_agent.kgstore.semantic_index import graph_signature as _gs
    check("an unknown graph signs as (0, None) rather than raising", _gs("no-such-kg") == (0, None))
except Exception as _e:
    check("an unknown graph signs as (0, None) rather than raising", False, str(_e))
check("both extractions run via asyncio.to_thread", hsrc.count("await asyncio.to_thread(\n            store.extract_subgraph_adaptive") + hsrc.count("await asyncio.to_thread(\n                    store.extract_subgraph_adaptive") == 2)
check("_kg_signature fails soft (None never matches a cached signature)", kgh._kg_signature("no-such-kg") in (None, (0, None)))

print("\n[D4] apply-update restores the prior graph when the import fails")
rsrc = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "api", "rest_routes.py")).read()
# The property, not the call name: the failure branch writes the in-memory pre-sync copy
# (cur_e, cur_r) back through whichever ONE write the happy path used, and reports it.
# (A1 moved the write from clear+import_bulk to replace_model_layer; the property held.)
_fn = rsrc[rsrc.index("async def kg_apply_update("):]
_fn = _fn[:_fn.index("\nasync def ", 10)] if "\nasync def " in _fn[10:] else _fn
_restore = re.search(r"except Exception as _imp_err:(.*?)return jsonify", _fn, re.S)
check("import wrapped; failure restores the pre-sync copy and reports it",
      _restore is not None
      and re.search(r"store\.(replace_model_layer|import_bulk)\(cur_e, cur_r\)", _restore.group(1)) is not None
      and '"restored_previous_graph": restored' in _fn)

print(f"\n=== KG Phase D results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
