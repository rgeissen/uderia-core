"""
Semantic-seeding index benchmark — the numbers behind the module docstring's
performance contract, reproducible on any machine, with NO LLM and NO server.

Measures the three costs a turn actually pays, and the one it used to pay:

  1. SEARCH   the per-query cost of finding the nearest entities, comparing the
              shipped float32-matmul path against the per-entity Python cosine loop
              this module used before (`--legacy`), at real graph sizes.
  2. MEMORY   resident bytes per cached graph, both representations.
  3. BUILD    the one-time cold cost of embedding a graph, and the restart cost of
              loading the persisted matrix instead (`--real` uses the platform's own
              SentenceTransformer provider; the default uses a synthetic embedder so
              the search/memory numbers are comparable on a machine with no model).

Run:  PYTHONPATH=src python3 test/performance/kg_semantic_bench.py
      PYTHONPATH=src python3 test/performance/kg_semantic_bench.py --real --sizes 3748
"""
import argparse
import math
import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import numpy as np  # noqa: E402

from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.kgstore import semantic_index as si  # noqa: E402

DIMS = 384
WORDS = ("incident victim offender arrestee property crime offense code date location "
         "agency report sex race age ethnicity resident status injury weapon "
         "relationship clearance recovered value customer order invoice product").split()


def _entities(n, seed=7):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        desc = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(6, 25)))
        out.append({"name": f"src.Table{i % 40}.C{i}",
                    "properties": {"label": f"Column {i}", "description": desc}})
    return out


class SyntheticEmbedder:
    """Deterministic vectors of the right shape — isolates search/memory cost from
    model inference, which `--real` measures separately."""

    def __init__(self, dims=DIMS):
        self.dims = dims

    def embed_texts(self, texts):
        rnd = random.Random(1)
        return [[rnd.random() for _ in range(self.dims)] for _ in texts]

    def embed_query(self, query):
        rnd = random.Random(2)
        return [rnd.random() for _ in range(self.dims)]


def _legacy_search(vectors, norms, qv, qn, top_k=10):
    """The pre-numpy path, verbatim in shape: one interpreted dot product per entity,
    then a full sort. Kept here so the comparison is measured, not asserted."""
    def cos(a, na, b, nb):
        if na == 0.0 or nb == 0.0:
            return 0.0
        return sum(x * y for x, y in zip(a, b)) / (na * nb)
    scored = [(i, cos(qv, qn, vectors[i], norms[i])) for i in range(len(vectors))]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


def bench_search(n, legacy=True, reps=20):
    emb = SyntheticEmbedder()
    entities = _entities(n)
    tmp = tempfile.mkdtemp(prefix="kgsem-bench-")
    prev_dir = getattr(APP_CONFIG, "KG_SEMANTIC_INDEX_DIR", None)
    APP_CONFIG.KG_SEMANTIC_INDEX_DIR = tmp
    APP_CONFIG.KG_SEMANTIC_SEEDING = True
    try:
        si._INDEX_CACHE.clear(); si._BUILDING.clear()
        t = time.perf_counter()
        si.warm(f"bench{n}", lambda: entities, f"sig{n}", emb, blocking=True)
        build_s = time.perf_counter() - t

        entry = si._INDEX_CACHE[(f"bench{n}", si._sig_key(f"sig{n}"))]
        t = time.perf_counter()
        for _ in range(reps):
            si.semantic_hits(f"bench{n}", "how many victims are female", lambda: entities,
                             f"sig{n}", emb)
        new_ms = (time.perf_counter() - t) * 1000 / reps
        new_mb = entry["bytes"] / 1e6

        # restart: drop memory, read the persisted matrix back
        si._INDEX_CACHE.clear()
        t = time.perf_counter()
        si.semantic_hits(f"bench{n}", "how many victims are female",
                         lambda: (_ for _ in ()).throw(AssertionError("re-embedded!")),
                         f"sig{n}", emb)
        load_ms = (time.perf_counter() - t) * 1000

        old_ms = old_mb = float("nan")
        if legacy:
            vectors = emb.embed_texts([si._entity_text(e) for e in entities])
            norms = [math.sqrt(sum(x * x for x in v)) for v in vectors]
            qv = emb.embed_query("q"); qn = math.sqrt(sum(x * x for x in qv))
            t = time.perf_counter()
            _legacy_search(vectors, norms, qv, qn)
            old_ms = (time.perf_counter() - t) * 1000
            old_mb = len(vectors) * (sys.getsizeof([0.0] * DIMS) + DIMS * 24) / 1e6

        return dict(n=n, build_s=build_s, new_ms=new_ms, new_mb=new_mb,
                    load_ms=load_ms, old_ms=old_ms, old_mb=old_mb)
    finally:
        APP_CONFIG.KG_SEMANTIC_INDEX_DIR = prev_dir
        shutil.rmtree(tmp, ignore_errors=True)
        si._INDEX_CACHE.clear(); si._BUILDING.clear()


def bench_real(n):
    """Cold build + per-query encode with the platform's actual embedding provider."""
    from trusted_data_agent.vectorstore.embedding_providers import SentenceTransformerProvider
    t = time.perf_counter()
    prov = SentenceTransformerProvider.get_cached(
        getattr(APP_CONFIG, "RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    prov.embed_query("warm the model")
    load_s = time.perf_counter() - t

    texts = [si._entity_text(e) for e in _entities(n)]
    t = time.perf_counter()
    for i in range(0, len(texts), si._EMBED_BATCH):
        prov.embed_texts(texts[i:i + si._EMBED_BATCH])
    embed_s = time.perf_counter() - t

    t = time.perf_counter()
    for _ in range(10):
        prov.embed_query("how many victims are female")
    query_ms = (time.perf_counter() - t) * 1000 / 10
    return dict(n=n, load_s=load_s, embed_s=embed_s, query_ms=query_ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="1000,4000,20000")
    ap.add_argument("--real", action="store_true",
                    help="also measure the real embedding provider (loads the model)")
    ap.add_argument("--no-legacy", action="store_true")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    print(f"\nSEARCH + MEMORY  ({DIMS}-dim vectors, synthetic embedder)\n")
    print(f"{'entities':>9} | {'legacy/query':>13} {'legacy RAM':>11} | "
          f"{'now/query':>10} {'now RAM':>8} {'speedup':>8} | {'build':>7} {'restart':>8}")
    print("-" * 96)
    for n in sizes:
        r = bench_search(n, legacy=not args.no_legacy)
        speed = (r["old_ms"] / r["new_ms"]) if r["new_ms"] and r["old_ms"] == r["old_ms"] else float("nan")
        print(f"{r['n']:>9} | {r['old_ms']:>10.1f} ms {r['old_mb']:>8.1f} MB | "
              f"{r['new_ms']:>7.3f} ms {r['new_mb']:>5.1f} MB {speed:>7.0f}x | "
              f"{r['build_s']:>5.2f} s {r['load_ms']:>6.1f} ms")

    if args.real:
        print(f"\nREAL EMBEDDING PROVIDER ({getattr(APP_CONFIG, 'RAG_EMBEDDING_MODEL', '?')})\n")
        for n in sizes:
            r = bench_real(n)
            print(f"  n={r['n']:>6}  model load {r['load_s']:>5.2f}s  "
                  f"cold embed {r['embed_s']:>6.2f}s  per-query encode {r['query_ms']:>6.1f} ms")
    print()


if __name__ == "__main__":
    main()
