"""
KG semantic seeding — the second recall channel (#9), its performance contract, and
the event that reports it.

WHAT CHANGED, AND WHY THIS GATE CHANGED WITH IT
This channel shipped opt-in and OFF, and the gate's first job was proving the disabled
path was byte-identical. It is now ON by default, because the reason it could not be
was measured and removed: the search was a per-entity Python cosine loop (57.6 ms and
49 MB on a 4,000-entity graph) and is now one float32 matmul (0.086 ms, 6.1 MB) —
`test/performance/kg_semantic_bench.py` prints the table. Enabling a retrieval channel
by default is a promise about three things, so the gate asserts all three:

  * RECALL      a synonym the lexical seeder cannot match ("customers" → an entity
                named `Party`) surfaces and ranks, without ever overriding an exact
                lexical match — semantic widens recall, it does not override precision.
  * COST        one build per (graph, version), persisted so a restart re-loads instead
                of re-embedding, memory bounded in BYTES, and a COLD graph never blocks
                a turn — it returns nothing and warms in the background.
  * HONESTY     the event distinguishes the three ways this contributes nothing (off /
                still warming / ran and found nothing), and counts as a contribution
                only an entity that was BOTH found by meaning and actually injected.

Disabling it (`KG_SEMANTIC_SEEDING=false`) must still be a true no-op — an operator who
turns it off gets the lexical behaviour exactly, which is also what a machine without
numpy gets.

Run:  PYTHONPATH=src python3 test/test_kg_phase_f.py        (no server, no model)
"""
import asyncio
import inspect
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import semantic_index as si    # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG          # noqa: E402
from components.builtin.knowledge_graph import handler as kgh  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _vec(text):
    """Deterministic 3-dim 'meaning': [customer-ish, sales-ish, product-ish]."""
    t = (text or "").lower()
    v = [0.0, 0.0, 0.0]
    for word, axis in (("customer", 0), ("client", 0), ("party", 0),
                       ("sales", 1), ("order", 1), ("revenue", 1),
                       ("product", 2), ("item", 2)):
        if word in t:
            v[axis] += 1.0
    return v if any(v) else [0.01, 0.01, 0.01]


class FakeEmbedder:
    def embed_texts(self, texts):
        return [_vec(t) for t in texts]

    def embed_query(self, q):
        return _vec(q)


class BoomEmbedder:
    def embed_texts(self, t):
        raise RuntimeError("embedder down")

    def embed_query(self, q):
        raise RuntimeError("embedder down")


ENTITIES = [
    {"id": 1, "name": "Party",   "entity_type": "entity",
     "properties": {"label": "Party", "description": "A customer or client account"}},
    {"id": 2, "name": "Order",   "entity_type": "entity",
     "properties": {"label": "Order", "description": "A sales order line"}},
    {"id": 3, "name": "Product", "entity_type": "entity",
     "properties": {"label": "Product", "description": "An item for sale"}},
]


def reset(tmp):
    si._INDEX_CACHE.clear()
    si._BUILDING.clear()
    APP_CONFIG.KG_SEMANTIC_INDEX_DIR = tmp


def warm_blocking(kg, sig, ents=None):
    return si.warm(kg, lambda: (ents if ents is not None else ENTITIES), sig,
                   FakeEmbedder(), blocking=True)


_prev_flag = getattr(APP_CONFIG, "KG_SEMANTIC_SEEDING", True)
_prev_dir = getattr(APP_CONFIG, "KG_SEMANTIC_INDEX_DIR", ".cache/kg_semantic")
TMP = tempfile.mkdtemp(prefix="kgsem-gate-")

try:
    # ── defaults ─────────────────────────────────────────────────────────────
    print("\n[default] on, and honest about numpy")
    reset(TMP)
    # Read the SHIPPED declaration, not the live attribute: this gate mutates the flag
    # all the way down, so asserting `is_enabled()` here would assert the value the line
    # above had just assigned — a check that cannot fail (it did not, when the default
    # was mutated back to off).
    _cfg_src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                 "trusted_data_agent", "core", "config.py")).read()
    check("the shipped default is ON",
          "os.environ.get('TDA_KG_SEMANTIC_SEEDING', 'true')" in _cfg_src)
    check("and it is overridable without a code change",
          "TDA_KG_SEMANTIC_SEEDING" in _cfg_src and "TDA_KG_SEMANTIC_INDEX_DIR" in _cfg_src)
    APP_CONFIG.KG_SEMANTIC_SEEDING = True
    _np, si._np = si._np, None
    check("no numpy ⇒ the channel reports itself off, never an error",
          si.is_enabled() is False
          and si.semantic_hits("kg", "customers", lambda: ENTITIES, "v1", FakeEmbedder()) == [])
    si._np = _np

    # ── recall ───────────────────────────────────────────────────────────────
    print("[recall] meaning finds what the words cannot")
    reset(TMP)
    warm_blocking("kg1", "v1")
    hits = si.semantic_hits("kg1", "how many customers do we have?", lambda: ENTITIES,
                            "v1", FakeEmbedder(), top_k=5)
    names = [n for n, _ in hits]
    check("a synonym query surfaces the lexically-unmatched entity", "Party" in names, hits)
    check("hits are ordered by cosine", names and names[0] == "Party", hits)
    check("an unrelated entity below threshold is not returned", "Product" not in names, hits)
    sales = si.semantic_hits("kg1", "revenue and sales", lambda: ENTITIES, "v1", FakeEmbedder())
    check("a different query surfaces a different entity", sales and sales[0][0] == "Order", sales)
    check("cosine scores are in [0,1]", all(-1e-6 <= s <= 1.0 + 1e-6 for _, s in hits), hits)
    check("top_k is honoured",
          len(si.semantic_hits("kg1", "customer sales product", lambda: ENTITIES, "v1",
                               FakeEmbedder(), top_k=1, min_score=-1.0)) == 1)
    check("min_score filters", si.semantic_hits("kg1", "customers", lambda: ENTITIES, "v1",
                                                FakeEmbedder(), min_score=1.01) == [])

    # ── ranking: widen recall, never override precision ──────────────────────
    print("[ranking] semantic never outranks an exact lexical match")
    check("_SEMANTIC_SCORE_MAX sits between a prefix (+40) and an exact identifier (+100)",
          40 <= kgh._SEMANTIC_SCORE_MAX < 100)
    exact = {"name": "customers_table", "entity_type": "entity", "properties": {"label": "customers"}}
    syn = {"name": "Party", "entity_type": "entity", "properties": {"label": "Party"}}
    ranked = kgh._rank_seeds([syn, exact], "customers", limit=15, semantic_scores={"Party": 1.0})
    check("an exact lexical match outranks a perfect semantic-only match",
          ranked[0]["name"] == "customers_table", [e["name"] for e in ranked])
    noise = {"name": "zzz", "entity_type": "entity", "properties": {}}
    ranked2 = kgh._rank_seeds([noise, syn], "customers", limit=15, semantic_scores={"Party": 0.9})
    check("a strong semantic match beats an unrelated node",
          ranked2[0]["name"] == "Party", [e["name"] for e in ranked2])
    check("empty semantic_scores ranks identically to none",
          [e["name"] for e in kgh._rank_seeds(ENTITIES, "party", limit=15)]
          == [e["name"] for e in kgh._rank_seeds(ENTITIES, "party", limit=15, semantic_scores={})])

    # ── cost: a cold graph must never make a turn wait ───────────────────────
    print("[cost] cold degrades to lexical; one build; bounded; persisted")
    reset(TMP)
    check("a graph with no index reports cold", si.index_state("kgC", "v1") == "cold")
    t0 = time.perf_counter()
    first = si.semantic_hits("kgC", "customers", lambda: (time.sleep(0.4) or ENTITIES),
                             "v1", FakeEmbedder())
    elapsed = time.perf_counter() - t0
    check("a cold index returns NOTHING rather than blocking the turn", first == [], first)
    check("and returns immediately (the 0.4 s build is off the turn)", elapsed < 0.2, f"{elapsed:.3f}s")
    builds = {"n": 0}

    def counting():
        builds["n"] += 1
        time.sleep(0.2)
        return ENTITIES

    reset(TMP)
    for _ in range(5):
        si.semantic_hits("kgD", "customers", counting, "v1", FakeEmbedder())
    time.sleep(0.6)
    check("five concurrent cold queries start ONE build", builds["n"] == 1, builds["n"])
    check("and the index is ready afterwards", si.index_state("kgD", "v1") == "ready")

    # Concurrency is bounded in BOTH directions: one build per graph (above) and a
    # ceiling on builds across graphs — an estate where several cold graphs are queried
    # at once must not put N embedding threads on the CPU a live turn is sharing.
    reset(TMP)
    live = {"n": 0, "peak": 0}
    _lk = __import__("threading").Lock()

    class Concurrent(FakeEmbedder):
        def embed_texts(self, texts):
            with _lk:
                live["n"] += 1
                live["peak"] = max(live["peak"], live["n"])
            time.sleep(0.2)
            with _lk:
                live["n"] -= 1
            return [_vec(t) for t in texts]

    for i in range(6):
        si.semantic_hits(f"kgB{i}", "customers", lambda: ENTITIES, f"s{i}", Concurrent())
    deadline = time.time() + 8
    while time.time() < deadline and any(
            si.index_state(f"kgB{i}", f"s{i}") != "ready" for i in range(6)):
        time.sleep(0.05)
    check("six cold graphs at once never exceed the build ceiling",
          live["peak"] <= si._MAX_CONCURRENT_BUILDS, live["peak"])
    check("and every one of them still gets built (queued, not dropped)",
          all(si.index_state(f"kgB{i}", f"s{i}") == "ready" for i in range(6)))

    reset(TMP)
    warm_blocking("kgE", "v1")
    rebuilt = {"n": 0}

    def counting2():
        rebuilt["n"] += 1
        return ENTITIES

    si.semantic_hits("kgE", "customers", counting2, "v1", FakeEmbedder())
    check("a warm (kg, signature) is reused — no rebuild", rebuilt["n"] == 0)
    warm_blocking("kgE", "v2")
    check("a new signature (rebuild/import) supersedes the old",
          len([k for k in si._INDEX_CACHE if k[0] == "kgE"]) == 1)

    # Memory must scale with GRAPHS QUERIED, not with users: the cache is keyed by
    # (graph, version) and is process-global, so a hundred people asking one agent
    # questions share ONE matrix. A per-user key would multiply a 30 MB graph by the
    # size of the tenant.
    reset(TMP)
    warm_blocking("kgShared", "v1")
    _one = sum(v["bytes"] for v in si._INDEX_CACHE.values())
    for u in range(25):
        si.semantic_hits("kgShared", f"user {u} asks something", lambda: ENTITIES, "v1",
                         FakeEmbedder())
    check("many users on one graph hold ONE matrix, not one each",
          sum(v["bytes"] for v in si._INDEX_CACHE.values()) == _one
          and len(si._INDEX_CACHE) == 1, list(si._INDEX_CACHE))

    # Idle graphs are GIVEN BACK. The byte ceiling only reclaims under pressure (it runs
    # when a new index is admitted), so a working set that goes quiet would hold its
    # memory indefinitely — and reclaiming is nearly free to undo because the matrix is
    # persisted: the next question re-loads it instead of re-embedding.
    reset(TMP)
    for g in range(3):
        warm_blocking(f"kgIdle{g}", "v1")
    _held = sum(v["bytes"] for v in si._INDEX_CACHE.values())
    check("three graphs are resident to begin with", _held > 0 and len(si._INDEX_CACHE) == 3)
    for e in si._INDEX_CACHE.values():
        e["used"] -= (si._IDLE_EVICT_SECONDS + 60)      # nothing has asked them anything
    si.semantic_hits("kgIdle0", "customers", lambda: ENTITIES, "v1", FakeEmbedder())
    check("a working set that went idle is released",
          not [k for k in si._INDEX_CACHE if k[0].startswith("kgIdle")]
          or sum(v["bytes"] for v in si._INDEX_CACHE.values()) < _held,
          list(si._INDEX_CACHE))
    # ...but a graph still being asked things is NOT taken away
    reset(TMP)
    warm_blocking("kgHot", "v1")
    warm_blocking("kgCold", "v1")
    for k, v in si._INDEX_CACHE.items():
        if k[0] == "kgCold":
            v["used"] -= (si._IDLE_EVICT_SECONDS + 60)
    si.semantic_hits("kgHot", "customers", lambda: ENTITIES, "v1", FakeEmbedder())
    _live = {k[0] for k in si._INDEX_CACHE}
    check("an idle graph is dropped while a live one is kept",
          "kgHot" in _live and "kgCold" not in _live, _live)
    # and reclaiming costs a reload, never a re-embed
    def _no_reembed():
        raise AssertionError("re-embedded a graph that was only reclaimed")
    check("a reclaimed graph is re-loaded from disk, not re-embedded",
          [n for n, _ in si.semantic_hits("kgCold", "customers", _no_reembed, "v1",
                                          FakeEmbedder())] == ["Party"])

    # memory ceiling is bytes, not graph count
    reset(TMP)
    prev_max = si._CACHE_MAX_BYTES
    si._CACHE_MAX_BYTES = 1          # one byte: every add must evict down to one graph
    for i in range(4):
        warm_blocking(f"kgM{i}", "s")
    check("the resident cache is bounded by BYTES", len(si._INDEX_CACHE) == 1,
          list(si._INDEX_CACHE))
    si._CACHE_MAX_BYTES = prev_max

    # RESIDENT MEMORY MEANS QUERIED. A build triggered by a graph WRITE has nobody
    # waiting on it — holding its matrix would charge memory for a graph that may never
    # be asked anything (a deployment importing ten agent packs would carry ten).
    reset(TMP)
    si.warm("kgW", lambda: ENTITIES, "v1", FakeEmbedder(), blocking=True, retain=False)
    check("a WRITE-built index is not held in memory",
          not [k for k in si._INDEX_CACHE if k[0] == "kgW"], list(si._INDEX_CACHE))
    check("but it is on disk, so the state reads ready rather than cold",
          si.index_state("kgW", "v1") == "ready")
    check("and the first question is answered semantically from disk",
          [n for n, _ in si.semantic_hits("kgW", "customers", lambda: ENTITIES, "v1",
                                          FakeEmbedder())] == ["Party"])
    check("which is when it becomes resident",
          [k for k in si._INDEX_CACHE if k[0] == "kgW"] != [])
    # ...and a QUERY-triggered build still retains, or every question would re-read disk
    reset(TMP)
    si.warm("kgR", lambda: ENTITIES, "v1", FakeEmbedder(), blocking=True)
    check("a query-triggered build stays resident",
          [k for k in si._INDEX_CACHE if k[0] == "kgR"] != [])
    # releasing is conditional on the matrix actually reaching disk — dropping an
    # unpersisted one would throw the embedding work away and re-pay it next query
    clean2 = tempfile.mkdtemp(prefix="kgsem-nopersist-")
    reset(clean2)
    si.warm("kgU2", lambda: ENTITIES, None, FakeEmbedder(), blocking=True, retain=False)
    check("an unpersistable index is kept in memory rather than lost",
          [k for k in si._INDEX_CACHE if k[0] == "kgU2"] != [], list(si._INDEX_CACHE))
    shutil.rmtree(clean2, ignore_errors=True)

    # ...and the WRITE PATH must actually ask for that. Executed, not grepped: the
    # release rule tested above is inert if the one caller that needs it never passes
    # the flag (removing it from graph_store escaped an earlier version of this gate).
    from components.builtin.knowledge_graph.graph_store import GraphStore as _GS

    class _StoreStub:
        kg_id = "kgWire"
        _db_path = "tda_auth.db"

        def all_entity_dicts(self):
            return ENTITIES
    # Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
    # a double unfaithful in the dimension under test measures nothing (Sep 2026).
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
    from kg_double_fidelity import double_mismatches as _dm
    from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
    check("_StoreStub double matches the real GraphStore calling convention", not _dm(_RealGS, _StoreStub), _dm(_RealGS, _StoreStub))

    _seen = {}
    _real_warm = si.warm
    si.warm = lambda *a, **k: _seen.update(k) or True
    try:
        _GS.warm_semantic_index(_StoreStub())
    finally:
        si.warm = _real_warm
    check("the graph-write path asks for the index to be released after persisting",
          _seen.get("retain") is False, _seen)

    # persistence: a restart must not re-embed
    reset(TMP)
    warm_blocking("kgP", "v1")

    def must_not_run():
        raise AssertionError("re-embedded after restart")

    si._INDEX_CACHE.clear()
    si._BUILDING.clear()
    restored = si.semantic_hits("kgP", "customers", must_not_run, "v1", FakeEmbedder())
    check("a restart loads the persisted matrix instead of re-embedding",
          [n for n, _ in restored] == ["Party"], restored)
    # Crash debris must be collected, or a killed process leaves a half-written pair
    # nothing ever removes — junk that only grows, on a feature whose claim is bounded
    # resource use. Age-guarded so a LIVE writer's temp file is never taken from it.
    _stale = os.path.join(TMP, f"{si._sig_key('x')[:0]}"
                          + __import__("hashlib").sha256(b"kgP").hexdigest()[:16]
                          + ".deadbeefdeadbeef.npy.tmp")
    open(_stale, "w").write("junk")
    os.utime(_stale, (0, 0))                       # abandoned an hour+ ago
    _live = _stale.replace("deadbeefdeadbeef", "beefdeadbeefdead")
    open(_live, "w").write("junk")                 # written just now
    si._prune_old_versions("kgP", si._sig_key("v1"))
    check("an abandoned temp file is collected", not os.path.exists(_stale))
    check("a live writer's temp file is left alone", os.path.exists(_live))
    os.remove(_live)
    files = os.listdir(TMP)
    check("exactly one version's files are kept per graph",
          len([f for f in files if f.endswith(".npy")]) == len({f.split(".")[0] for f in files
                                                               if f.endswith(".npy")}), files)
    clean = tempfile.mkdtemp(prefix="kgsem-unsigned-")
    reset(clean)
    si.warm("kgU", lambda: ENTITIES, None, FakeEmbedder(), blocking=True)
    check("an unsigned graph (no signature) is never persisted", os.listdir(clean) == [],
          os.listdir(clean))
    shutil.rmtree(clean, ignore_errors=True)
    reset(TMP)

    # ── resilience ───────────────────────────────────────────────────────────
    print("[resilience] never raises into retrieval")
    reset(TMP)
    check("a failing embedder yields no hits",
          si.semantic_hits("kgX", "customers", lambda: ENTITIES, "s", BoomEmbedder()) == [])
    check("a store that raises yields no hits",
          si.semantic_hits("kgY", "customers", must_not_run, "s", FakeEmbedder()) == [])
    reset(TMP)
    warm_blocking("kgZ", "v1")

    class WrongDims(FakeEmbedder):
        def embed_query(self, q):
            return [1.0] * 99          # a changed embedding model under a built index

    check("a dimension mismatch is refused, not searched",
          si.semantic_hits("kgZ", "customers", lambda: ENTITIES, "v1", WrongDims()) == [])
    check("and the unusable index is EVICTED so it can be rebuilt "
          "(returning [] alone is what a swallowed matmul error also does)",
          ("kgZ", si._sig_key("v1")) not in si._INDEX_CACHE, list(si._INDEX_CACHE))
    check("its PERSISTED copy is discarded too — otherwise the next query reloads the "
          "same unusable matrix and the graph stays unsearchable for good",
          not any(os.path.exists(f) for f in si._paths("kgZ", si._sig_key("v1"))))
    check("an entity with no text never breaks the build",
          si.warm("kgN", lambda: [{"id": 9, "name": "", "properties": {}}], "v1",
                  FakeEmbedder(), blocking=True) is True)

    # ── disabled is a true no-op ─────────────────────────────────────────────
    print("[disabled] an operator who turns it off gets the lexical behaviour exactly")
    reset(TMP)
    warm_blocking("kg1", "v1")
    APP_CONFIG.KG_SEMANTIC_SEEDING = False
    check("disabled ⇒ no hits even with a warm index and an embedder",
          si.semantic_hits("kg1", "customers", lambda: ENTITIES, "v1", FakeEmbedder()) == [])
    check("disabled ⇒ the state reads off", si.index_state("kg1", "v1") == "off")
    check("disabled ⇒ warm() does no work", si.warm("kgOff", must_not_run, "v1",
                                                    FakeEmbedder(), blocking=True) is False)
    APP_CONFIG.KG_SEMANTIC_SEEDING = True

    # ── the event: what the operator is told ─────────────────────────────────
    print("[event] the three absences are distinguishable, and only injected hits count")
    hcls = [v for k, v in vars(kgh).items()
            if inspect.isclass(v) and hasattr(v, "_semantic_scores")][0]
    h = hcls.__new__(hcls)

    # The fake's calling convention is pinned METHOD-BY-METHOD to the real GraphStore:
    # `get_entity_by_name` is SYNC there. The first version of this fake declared it
    # async — faithful to the wrong convention — and so could never catch the bare
    # `await` that made every bridging turn on production report "semantic seeding
    # did not run" (Sep 2026). A fake unfaithful in the dimension under test measures
    # nothing.
    from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
    check("the REAL GraphStore.get_entity_by_name is sync (the convention the fake pins)",
          not inspect.iscoroutinefunction(_RealGS.get_entity_by_name))

    class Store:
        kg_id = "kgEv"

        def __init__(self, ents):
            self._by_name = {e["name"]: e for e in ents}

        def get_entity_by_name(self, n):
            return self._by_name.get(n)
    # Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
    # a double unfaithful in the dimension under test measures nothing (Sep 2026).
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
    from kg_double_fidelity import double_mismatches as _dm
    from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
    check("Store double matches the real GraphStore calling convention", not _dm(_RealGS, Store), _dm(_RealGS, Store))


    _real_sig = kgh._kg_signature
    _real_emb = si._resolve_embedder
    kgh._kg_signature = lambda kid: "v1"          # hermetic: no DB read
    si._resolve_embedder = lambda: FakeEmbedder()  # hermetic: no model load
    try:
        reset(TMP)
        si.warm("kgEv", lambda: ENTITIES, "v1", FakeEmbedder(), blocking=True)
        # the pool already holds Party lexically ⇒ semantics CORROBORATED, bridged nothing
        pool = [dict(ENTITIES[0])]
        rep = {}
        asyncio.run(h._semantic_scores(Store(ENTITIES), "customers", pool, {1}, report=rep))
        check("a hit the lexical pool already held is not counted as a bridge",
              rep["bridged"] == [] and rep["hits"] >= 1, rep)
        # The same property where an id CANNOT settle it: a pool entity carrying no id
        # is deduped by NAME or not at all, so this is the case that proves the name
        # check is load-bearing rather than shadowed by the id guard.
        pool_noid = [{"name": "Party", "properties": {}}]
        rep_noid = {}
        asyncio.run(h._semantic_scores(Store(ENTITIES), "customers", pool_noid, set(),
                                       report=rep_noid))
        check("an id-less pool entity is still recognised as already found",
              rep_noid["bridged"] == [] and len(pool_noid) == 1, (rep_noid, pool_noid))
        check("state is ready when the index is built", rep["state"] == "ready", rep)
        # an empty pool ⇒ Party is reached ONLY by meaning
        pool2, rep2 = [], {}
        asyncio.run(h._semantic_scores(Store(ENTITIES), "customers", pool2, set(), report=rep2))
        check("an entity only meaning found is reported as a bridge",
              [b["name"] for b in rep2["bridged"]] == ["Party"], rep2)
        check("a bridging turn reads ready — never 'error' (the production defect)",
              rep2["state"] == "ready", rep2)
        check("and it is added to the candidate pool",
              [e["name"] for e in pool2] == ["Party"], pool2)
        check("the bridge carries its similarity", isinstance(rep2["bridged"][0]["score"], float))
        # cold ⇒ 'building', which is NOT 'ran and found nothing'.
        # A graph that was never indexed — reset() clears memory, and a graph that HAS
        # been indexed correctly loads from disk and reports ready (that is the whole
        # point of persisting it), so reusing kgEv here would measure the opposite.
        reset(TMP)

        class ColdStore(Store):
            kg_id = "kgEvNeverIndexed"
        # Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
        # a double unfaithful in the dimension under test measures nothing (Sep 2026).
        import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
        from kg_double_fidelity import double_mismatches as _dm
        from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
        check("ColdStore double matches the real GraphStore calling convention", not _dm(_RealGS, ColdStore), _dm(_RealGS, ColdStore))

        rep3 = {}
        asyncio.run(h._semantic_scores(ColdStore(ENTITIES), "customers", [], set(), report=rep3))
        check("a cold graph reports building/cold, never a bare empty result",
              rep3["state"] in ("building", "cold") and rep3["bridged"] == [], rep3)
        # off ⇒ 'off'
        APP_CONFIG.KG_SEMANTIC_SEEDING = False
        rep4 = {}
        asyncio.run(h._semantic_scores(Store(ENTITIES), "customers", [], set(), report=rep4))
        check("disabled reports off", rep4["state"] == "off", rep4)
        APP_CONFIG.KG_SEMANTIC_SEEDING = True
    finally:
        kgh._kg_signature = _real_sig
        si._resolve_embedder = _real_emb

    # ── wiring ───────────────────────────────────────────────────────────────
    print("[wiring] the seam is connected end to end")
    ssrc = inspect.getsource(hcls._semantic_scores)
    check("the handler gates on is_enabled() and unions hits into the pool",
          "is_enabled()" in ssrc and "results.append(ent)" in ssrc)
    check("the search runs off the event loop (asyncio.to_thread)", "asyncio.to_thread(" in ssrc)
    seed_src = inspect.getsource(hcls._search_entities_for_query)
    check("the search seam passes semantic_scores to ranking",
          "semantic_scores=semantic_scores" in seed_src)
    check("the search seam fills the seeding report", "report=seeding_report" in seed_src)
    enr_src = inspect.getsource(hcls.get_context_enrichment)
    check("the enrichment collects the report and puts it on the event",
          "seeding_report" in enr_src and 'metadata["semantic_seeding"]' in enr_src)
    check("the enrichment routes the contribution rule (not a private copy)",
          "_semantic_contribution(" in enr_src)
    _rep = {"state": "ready", "hits": 3,
            "bridged": [{"name": "Party", "score": 0.8}, {"name": "Ghost", "score": 0.7}]}
    _contrib = kgh._semantic_contribution(_rep, {"Party", "Order"})
    check("only hits that survived into the prompt are reported as contributions",
          [b["name"] for b in _contrib["bridged"]] == ["Party"], _contrib)
    check("a hit that ranking or the budget dropped is counted, not hidden",
          _contrib["bridged_dropped"] == 1, _contrib)
    check("the state and hit count are carried through verbatim",
          _contrib["state"] == "ready" and _contrib["hits"] == 3, _contrib)
    check("a report with no bridges yields no contributions and no phantom drops",
          kgh._semantic_contribution({"state": "ready", "hits": 0}, set())
          == {"state": "ready", "hits": 0, "bridged": [], "bridged_dropped": 0})

finally:
    APP_CONFIG.KG_SEMANTIC_SEEDING = _prev_flag
    APP_CONFIG.KG_SEMANTIC_INDEX_DIR = _prev_dir
    si._INDEX_CACHE.clear()
    si._BUILDING.clear()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n=== KG semantic seeding: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
