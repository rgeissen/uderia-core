#!/usr/bin/env python3
"""Pre-build the semantic index for knowledge graphs that already exist.

WHY THIS EXISTS
Semantic seeding embeds a graph's entities once and searches them by meaning. Graphs
written from now on are indexed at WRITE time — `GraphStore.import_bulk` warms the
index, so a constructor run, a marketplace install/fork/sync, an agent-pack import or a
PDM deploy all leave the graph immediately searchable.

A graph that ALREADY EXISTS never passes through that hook, so it is NOT migrated by it.
It self-heals instead: the first question against it finds no index, is answered by the
lexical channel exactly as before, and builds the index in the background — so the gap
is exactly ONE query per graph, once, ever (the matrix is persisted, so a restart
re-loads it rather than re-embedding).

That is usually fine and needs no action. Run this when you would rather not spend even
that one query — typically right after a deployment, before users arrive.

WHAT IT DOES NOT DO
It writes nothing to the graph. The index is a derived artefact kept in
`KG_SEMANTIC_INDEX_DIR`; `kg_entities`, `kg_relationships` and `kg_metadata` are only
read. No trust attestation moves, because the Trust-Membrane content hash is computed
from those rows and they are untouched.

Usage:
    PYTHONPATH=src python3 maintenance/warm_kg_semantic_indexes.py            # dry run
    PYTHONPATH=src python3 maintenance/warm_kg_semantic_indexes.py --apply
    PYTHONPATH=src python3 maintenance/warm_kg_semantic_indexes.py --apply --active-only
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.core import sqlite_util                       # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG                 # noqa: E402
from trusted_data_agent.kgstore import semantic_index as si           # noqa: E402


def graphs(active_only: bool):
    db = (getattr(APP_CONFIG, "AUTH_DB_PATH", "") or "tda_auth.db").replace("sqlite:///", "")
    conn = sqlite_util.connect(db or "tda_auth.db")
    try:
        sql = ("SELECT m.kg_id, m.name, m.profile_id, m.user_uuid, COUNT(e.id) "
               "FROM kg_metadata m LEFT JOIN kg_entities e ON e.kg_id = m.kg_id ")
        if active_only:
            sql += "WHERE m.is_active = 1 "
        sql += "GROUP BY m.kg_id, m.name, m.profile_id, m.user_uuid ORDER BY COUNT(e.id) DESC"
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="build the indexes (default: dry run)")
    ap.add_argument("--active-only", action="store_true", help="only graphs currently bound to a profile")
    args = ap.parse_args()

    if not si.is_enabled():
        print("Semantic seeding is OFF (or numpy is unavailable) — nothing to build.")
        print("  Enable it: Administration -> App Config -> AI & Knowledge -> Knowledge Graph Retrieval")
        return 0

    rows = graphs(args.active_only)
    if not rows:
        print("No knowledge graphs found.")
        return 0

    print(f"{len(rows)} graph(s){' (active only)' if args.active_only else ''}:\n")
    todo = []
    for kg_id, name, profile_id, user_uuid, n in rows:
        sig = si.graph_signature(kg_id)
        state = si.index_state(kg_id, sig)
        print(f"  {str(name or '(unnamed)')[:38]:40} {n:>6} entities   index: {state}")
        if state != "ready" and n:
            todo.append((kg_id, name, profile_id, user_uuid, n, sig))

    if not todo:
        print("\nEvery graph already has a current index — nothing to do.")
        return 0
    if not args.apply:
        print(f"\n{len(todo)} graph(s) would be indexed. Re-run with --apply.")
        return 0

    from components.builtin.knowledge_graph.graph_store import GraphStore
    print(f"\nBuilding {len(todo)} index(es) — one at a time, so this never competes "
          f"with a live turn for the CPU:\n")
    ok = 0
    for kg_id, name, profile_id, user_uuid, n, sig in todo:
        store = GraphStore(profile_id, user_uuid)
        t = time.perf_counter()
        # blocking on purpose: this script IS the foreground work, and building serially
        # keeps the cost predictable instead of racing N threads.
        # retain=False: prepare the index on disk without accumulating every
        # graph's matrix in this process — the 11.5k-entity graph alone is 18 MB.
        built = si.warm(kg_id, store.all_entity_dicts, sig, blocking=True, retain=False)
        mark = "ok  " if built else "FAIL"
        print(f"  {mark} {str(name or '(unnamed)')[:38]:40} {n:>6} entities  "
              f"{time.perf_counter() - t:6.2f}s")
        ok += 1 if built else 0
    print(f"\n{ok} of {len(todo)} built. They persist under "
          f"{getattr(APP_CONFIG, 'KG_SEMANTIC_INDEX_DIR', '.cache/kg_semantic')}, so a "
          f"restart re-loads them.")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
