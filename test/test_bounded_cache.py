"""Module tests for the R1.4 BoundedLRUCache + the bounded live caches.

Verifies dict-compatible behavior, LRU eviction semantics, the on_evict hook,
and that the caches that grow with load (llm_instance_pool, tts_clients_by_user,
chromadb _coll_cache) are actually bounded — a soak run that would grow without
limit stays flat.

Run:
    PYTHONPATH=src python test/test_bounded_cache.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.bounded_cache import BoundedLRUCache  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


def t_dict_surface():
    c = BoundedLRUCache(max_size=10)
    c["a"] = 1
    check("setitem/getitem", c["a"] == 1)
    check("contains", "a" in c and "z" not in c)
    check("len", len(c) == 1)
    check("get default", c.get("z", 99) == 99)
    check("setdefault new", c.setdefault("b", 2) == 2 and c["b"] == 2)
    check("setdefault existing", c.setdefault("a", 999) == 1)
    check("pop", c.pop("b") == 2 and "b" not in c)
    check("pop missing default", c.pop("zz", None) is None)
    del c["a"]
    check("delitem", "a" not in c)
    c["x"] = 10
    check("keys/values/items", list(c.keys()) == ["x"] and list(c.values()) == [10]
          and list(c.items()) == [("x", 10)])
    c.clear()
    check("clear", len(c) == 0)


def t_lru_eviction():
    c = BoundedLRUCache(max_size=3)
    for k in ("a", "b", "c", "d"):
        c[k] = k
    check("evicts to bound (len == 3)", len(c) == 3)
    check("LRU evicted oldest (a gone)", "a" not in c)
    check("recent retained (b,c,d)", all(k in c for k in ("b", "c", "d")))
    check("eviction counter", c.evictions == 1)


def t_lru_recency():
    c = BoundedLRUCache(max_size=3)
    for k in ("a", "b", "c"):
        c[k] = k
    _ = c["a"]            # touch 'a' -> most-recently-used
    c["d"] = "d"          # evicts least-recently-used, which is now 'b'
    check("touched key survives ('a' kept)", "a" in c)
    check("untouched LRU evicted ('b' gone)", "b" not in c)


def t_on_evict_hook():
    evicted = []
    c = BoundedLRUCache(max_size=2, on_evict=lambda k, v: evicted.append((k, v)))
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3            # evicts ('a', 1)
    check("on_evict fired with evicted entry", evicted == [("a", 1)])
    # a raising hook must not break insertion
    c2 = BoundedLRUCache(max_size=1, on_evict=lambda k, v: (_ for _ in ()).throw(RuntimeError("x")))
    c2["a"] = 1
    c2["b"] = 2
    check("raising on_evict is swallowed", "b" in c2 and len(c2) == 1)


def t_soak_flat():
    c = BoundedLRUCache(max_size=512)
    max_seen = 0
    for i in range(100_000):
        c[f"k{i}"] = i
        if i % 5000 == 0:
            max_seen = max(max_seen, len(c))
    check("soak: 100k inserts stay bounded (len == 512)", len(c) == 512)
    check("soak: never exceeded bound mid-run", max_seen <= 512)
    check("soak: evictions == inserts - max (99488)", c.evictions == 100_000 - 512)


def t_live_pools_bounded():
    # The real APP_STATE pools that grow with users must be BoundedLRUCache.
    from trusted_data_agent.core.config import APP_STATE
    check("llm_instance_pool is bounded",
          isinstance(APP_STATE.get("llm_instance_pool"), BoundedLRUCache))
    check("tts_clients_by_user is bounded",
          isinstance(APP_STATE.get("tts_clients_by_user"), BoundedLRUCache))
    # chromadb backend per-collection cache is bounded
    from trusted_data_agent.vectorstore.chromadb_backend import ChromaDBBackend
    b = ChromaDBBackend()
    check("chromadb _coll_cache is bounded",
          isinstance(b._coll_cache, BoundedLRUCache))


def main():
    print("== BoundedLRUCache + live-cache bounding tests ==")
    for t in (t_dict_surface, t_lru_eviction, t_lru_recency, t_on_evict_hook,
              t_soak_flat, t_live_pools_bounded):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
