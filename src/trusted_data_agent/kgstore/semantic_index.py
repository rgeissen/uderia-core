"""Semantic seeding index for KG retrieval — the SECOND recall channel.

The lexical seeder (`handler._search_entities_for_query`) matches the query against
entity names and property text. It is precise but cannot bridge a vocabulary gap:
a question about "customers" against an entity called `Party`, or "sales" against
`Order`, finds nothing. This index embeds each entity's `label + description + name`
once and returns the query's nearest entities by cosine, so a synonym still surfaces
the right node. The lexical ranking stays authoritative; semantic hits only WIDEN
the candidate pool and contribute a bounded score (see the call site).

ON by default (`APP_CONFIG.KG_SEMANTIC_SEEDING`). It was opt-in while the search was
a per-entity Python cosine loop — a shape that could not be defaulted on, and the
measurements say why (`test/performance/kg_semantic_bench.py`, 384-dim vectors):

    entities   search (loop)   resident (loop)      search (now)   resident (now)
       1,000        15.1 ms          12.3 MB           0.062 ms          1.5 MB
       4,000        59.9 ms          49.4 MB           0.086 ms          6.1 MB
      20,000       275.4 ms         246.9 MB           0.426 ms         30.7 MB

A list of Python floats costs ~24 bytes per component plus pointers; one float32
matrix costs 4. That 8x is what made four cached graphs a gigabyte of scalars, and the
~700x is one BLAS matmul replacing 20,000 interpreted dot products.

The performance contract, in the order a turn meets it:

  * SEARCH is one matmul. Vectors live in ONE C-contiguous float32 matrix,
    L2-normalised at build time, so cosine IS `matrix @ query` and the top-k comes
    from `argpartition` (linear) rather than sorting every row.
  * A COLD index never blocks a turn. Embedding 3,748 entities measures ~3.6 s
    (plus ~10 s if this process has not loaded the model yet) — so a graph with no
    index yet returns NO hits, lexical seeding stands unchanged, and the build runs
    on a background thread. One build per (graph, version): concurrent turns join
    the one in flight instead of starting their own.
  * A RESTART re-loads, it does not re-embed. The matrix is persisted per
    (kg_id, signature); a process that has never seen the graph reads it back in
    ~20 ms on the first query instead of paying the 3.6 s again.
  * The recurring floor is the QUERY encode (~5 ms warm, through the platform's own
    provider), not the search — worth stating plainly, because optimising the search
    further optimises 0.08 ms of a turn that is about to spend seconds in an LLM.

Design invariants (unchanged):
  * The embedder is INJECTABLE (an object with `embed_texts` / `embed_query`, e.g. the
    platform's `SentenceTransformerProvider`) so it is testable without loading a model
    and swappable per deployment; resolved lazily only when enabled and none injected.
  * Vectors are keyed by a CONTENT signature, so a rebuild / import supersedes the
    index (never stale) rather than needing an invalidation call.
  * Never raises into retrieval: any failure returns no hits (lexical seeding stands).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

try:
    import numpy as _np
except Exception:  # noqa: BLE001 — numpy absent ⇒ the whole channel is off, never an error
    _np = None

# (kg_id, sig_key) -> {"names": [str], "matrix": np.ndarray(n, d) float32 L2-normalised,
#                      "bytes": int, "used": float}
_INDEX_CACHE: Dict[tuple, Dict[str, Any]] = {}
_BUILDING: set = set()                    # keys with a build in flight (never build twice)
_LOCK = threading.RLock()                 # guards _INDEX_CACHE and _BUILDING

#: Resident ceiling for ALL cached graphs together. Bytes, not graph count: a count
#: bounds nothing when one graph is 30 MB and the next is 300 KB.
_CACHE_MAX_BYTES = 256 * 1024 * 1024
#: Do not embed an unbounded estate on one turn.
_MAX_ENTITIES = 20000
#: Embed in batches so peak memory is the batch, not the graph — the provider hands
#: back Python lists (~24 bytes a component), which is 8x the float32 they become.
_EMBED_BATCH = 512
#: Give a graph's matrix back when nothing has asked it anything for this long. The
#: byte ceiling below only reclaims under PRESSURE — it runs when a new index is
#: admitted — so a working set that goes quiet would hold its memory indefinitely.
#: Reclaiming is nearly free to undo here precisely because the matrix is persisted: a
#: later question reads it back in ~20 ms rather than re-embedding.
_IDLE_EVICT_SECONDS = 1800
#: Concurrent BUILDS across all graphs. One build per graph is already guaranteed by
#: `_BUILDING`; this bounds the other direction — an estate where several cold graphs
#: are queried at once must not put N embedding threads on the CPU a live turn is
#: sharing. Queued builds wait their turn; a turn never waits for either.
_MAX_CONCURRENT_BUILDS = 2
_BUILD_SLOTS = threading.Semaphore(_MAX_CONCURRENT_BUILDS)


def is_enabled() -> bool:
    """True when the channel is configured on AND numpy is importable.

    numpy absence is a capability fact, not a failure: without it there is no fast
    search to run, and a slow one is worse than none — retrieval stays lexical.
    """
    if _np is None:
        return False
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "KG_SEMANTIC_SEEDING", True))
    except Exception:  # noqa: BLE001
        return False


def graph_signature(kg_id: str, db_path: Optional[str] = None):
    """A graph's content version: ``(row count, max updated_at)``.

    THE definition, used by the write side (build the index when the graph changes) and
    the read side (look it up). Two definitions would be the worst possible bug here —
    a build keyed one way and a lookup keyed the other rebuilds on every single query
    and never hits. One indexed query; fails soft to None (which matches no cached
    index, so a signature that cannot be read degrades to no semantic seeding rather
    than to a stale one).
    """
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.core import sqlite_util
        path = db_path or (getattr(APP_CONFIG, "AUTH_DB_PATH", "") or "tda_auth.db")
        path = path.replace("sqlite:///", "") or "tda_auth.db"
        conn = sqlite_util.connect(path)
        try:
            row = conn.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM kg_entities WHERE kg_id=?",
                (kg_id,)).fetchone()
            return (row[0], row[1]) if row else None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def _resolve_embedder():
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.vectorstore.embedding_providers import SentenceTransformerProvider
    model = getattr(APP_CONFIG, "RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    return SentenceTransformerProvider.get_cached(model)


def _entity_text(e: Dict) -> str:
    props = e.get("properties") or {}
    leaf = str(e.get("name") or "").rpartition(".")[2]
    parts = [leaf, str(props.get("label") or ""), str(props.get("description") or ""),
             str(props.get("business_meaning") or "")]
    return " ".join(p for p in parts if p).strip()


# ── persistence ───────────────────────────────────────────────────────────────

def _sig_key(signature) -> str:
    """A short stable key for a graph version. ``None`` (signature unavailable) gets
    its own key so an unversioned graph still caches in memory but is never written to
    disk under a name a later version could collide with."""
    return hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:16]


def _index_dir() -> Optional[str]:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        d = getattr(APP_CONFIG, "KG_SEMANTIC_INDEX_DIR", ".cache/kg_semantic")
        if not d:
            return None
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001
        return None


def _paths(kg_id: str, sig_key: str) -> Optional[Tuple[str, str]]:
    d = _index_dir()
    if not d:
        return None
    safe = hashlib.sha256(str(kg_id).encode("utf-8")).hexdigest()[:16]
    stem = os.path.join(d, f"{safe}.{sig_key}")
    return f"{stem}.npy", f"{stem}.json"


def _save(kg_id: str, sig_key: str, names: List[str], matrix) -> bool:
    """Persist the version's matrix, then drop this graph's OTHER versions.

    Written temp-then-rename so a crash mid-write can never leave a half matrix that
    the next process would load as though it were complete. Returns whether it landed —
    the caller releases a write-built matrix from memory only when it can be read back.
    """
    p = _paths(kg_id, sig_key)
    if not p:
        return False
    npy, meta = p
    try:
        tmp_npy, tmp_meta = npy + ".tmp", meta + ".tmp"
        with open(tmp_npy, "wb") as fh:
            _np.save(fh, matrix, allow_pickle=False)
        with open(tmp_meta, "w", encoding="utf-8") as fh:
            json.dump({"names": names, "kg_id": str(kg_id), "dims": int(matrix.shape[1])}, fh)
        os.replace(tmp_npy, npy)
        os.replace(tmp_meta, meta)
        _prune_old_versions(kg_id, sig_key)
        return True
    except Exception as exc:  # noqa: BLE001 — a cache that cannot be written still works
        logger.debug(f"KG semantic index: persist skipped (kg={kg_id}): {exc}")
        return False


#: A temp file older than this was abandoned by a process that died mid-write; a live
#: one is replaced within milliseconds. Age-guarded rather than unconditional so this
#: can never delete a concurrent writer's file out from under it.
_STALE_TMP_SECONDS = 3600


def _prune_old_versions(kg_id: str, keep_sig: str) -> None:
    """Drop this graph's superseded versions, and any abandoned temp file.

    Without the second half a killed process leaves a half-written `.tmp` pair that
    nothing ever collects — junk that only grows, on a feature whose whole claim is
    bounded resource use.
    """
    d = _index_dir()
    if not d:
        return
    safe = hashlib.sha256(str(kg_id).encode("utf-8")).hexdigest()[:16]
    now = time.time()
    try:
        for fn in os.listdir(d):
            if not fn.startswith(f"{safe}."):
                continue
            path = os.path.join(d, fn)
            superseded = not fn.startswith(f"{safe}.{keep_sig}.")
            if fn.endswith(".tmp"):
                try:
                    if now - os.path.getmtime(path) < _STALE_TMP_SECONDS:
                        continue          # a live writer's file — leave it alone
                except OSError:
                    continue
            elif not superseded:
                continue
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def _discard_persisted(kg_id: str, sig_key: str) -> None:
    """Remove one version's persisted files (an index that can no longer be used)."""
    p = _paths(kg_id, sig_key)
    if not p:
        return
    for f in p:
        try:
            os.remove(f)
        except OSError:
            pass


def _load(kg_id: str, sig_key: str) -> Optional[Dict[str, Any]]:
    p = _paths(kg_id, sig_key)
    if not p:
        return None
    npy, meta = p
    try:
        if not (os.path.exists(npy) and os.path.exists(meta)):
            return None
        with open(meta, "r", encoding="utf-8") as fh:
            names = json.load(fh).get("names") or []
        matrix = _np.load(npy, allow_pickle=False)
        # A names/rows disagreement means a truncated or mismatched pair: discard it
        # rather than search a matrix whose row i is not names[i].
        if matrix.ndim != 2 or len(names) != matrix.shape[0]:
            return None
        return _entry(names, _np.ascontiguousarray(matrix, dtype=_np.float32))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"KG semantic index: load skipped (kg={kg_id}): {exc}")
        return None


# ── build / search ────────────────────────────────────────────────────────────

def _entry(names: List[str], matrix) -> Dict[str, Any]:
    return {"names": names, "matrix": matrix,
            "bytes": int(getattr(matrix, "nbytes", 0)), "used": time.monotonic()}


def _normalize(matrix):
    """L2-normalise rows in place so cosine is a plain dot product at query time.

    A zero row would divide by zero and poison the whole matmul with NaN, so those
    norms are floored to 1 — the row then scores 0 against every query, which is the
    honest answer for an entity whose text carried no signal.
    """
    norms = _np.linalg.norm(matrix, axis=1, keepdims=True)
    _np.maximum(norms, 1e-12, out=norms)
    matrix /= norms
    return matrix


def _build(entities: List[Dict], embedder) -> Optional[Dict[str, Any]]:
    names, texts = [], []
    for e in entities:
        t = _entity_text(e)
        if t:
            names.append(str(e.get("name")))
            texts.append(t)
    if not texts:
        return _entry([], _np.zeros((0, 1), dtype=_np.float32))
    blocks = []
    for i in range(0, len(texts), _EMBED_BATCH):
        chunk = embedder.embed_texts(texts[i:i + _EMBED_BATCH])
        blocks.append(_np.asarray(chunk, dtype=_np.float32))
    matrix = blocks[0] if len(blocks) == 1 else _np.vstack(blocks)
    if matrix.ndim != 2 or matrix.shape[0] != len(names):
        return None
    return _entry(names, _normalize(_np.ascontiguousarray(matrix, dtype=_np.float32)))


def _sweep_idle() -> int:
    """Release matrices nothing has queried for `_IDLE_EVICT_SECONDS`. Returns MB freed.

    Runs opportunistically on access rather than from a timer thread: the cache holds a
    handful of entries, so the scan is free, and a process with no knowledge-graph
    traffic has no working set to shed anyway. Honest limit — a process that goes
    COMPLETELY idle keeps what it last held until something touches the index again.
    """
    cutoff = time.monotonic() - _IDLE_EVICT_SECONDS
    freed = 0
    with _LOCK:
        for k in [k for k, v in _INDEX_CACHE.items() if v["used"] < cutoff]:
            freed += _INDEX_CACHE.pop(k)["bytes"]
    if freed:
        logger.info(f"KG semantic index: released {freed / 1e6:.1f}MB idle for "
                    f">{_IDLE_EVICT_SECONDS // 60} min (re-loads from disk on demand)")
    return freed


def _remember(key: tuple, entry: Dict[str, Any]) -> None:
    """Cache an index, shed idle graphs, then evict LRU past the byte ceiling."""
    _sweep_idle()
    with _LOCK:
        for k in [k for k in list(_INDEX_CACHE) if k[0] == key[0] and k != key]:
            _INDEX_CACHE.pop(k, None)          # other versions of the same graph
        _INDEX_CACHE[key] = entry
        total = sum(v["bytes"] for v in _INDEX_CACHE.values())
        while total > _CACHE_MAX_BYTES and len(_INDEX_CACHE) > 1:
            oldest = min(_INDEX_CACHE, key=lambda k: _INDEX_CACHE[k]["used"])
            total -= _INDEX_CACHE.pop(oldest)["bytes"]


def _build_and_cache(key: tuple, kg_id: str, sig_key: str, list_entities_fn, embedder,
                     persist: bool, retain: bool = True) -> Optional[Dict[str, Any]]:
    try:
        with _BUILD_SLOTS:
            return _build_locked(key, kg_id, sig_key, list_entities_fn, embedder,
                                 persist, retain)
    except Exception as exc:  # noqa: BLE001 — a failed build must never reach a turn
        logger.warning(f"KG semantic index build failed (kg={kg_id}): {exc}")
        return None
    finally:
        with _LOCK:
            _BUILDING.discard(key)


def _build_locked(key: tuple, kg_id: str, sig_key: str, list_entities_fn, embedder,
                  persist: bool, retain: bool) -> Optional[Dict[str, Any]]:
    # A graph that was waiting for a build slot may have been indexed meanwhile (a
    # persisted matrix read back by a query); rebuilding it would burn the slot twice.
    with _LOCK:
        if key in _INDEX_CACHE:
            return _INDEX_CACHE[key]
    entities = list(list_entities_fn() or [])[:_MAX_ENTITIES]
    t0 = time.monotonic()
    entry = _build(entities, embedder)
    if entry is None:
        return None
    _remember(key, entry)
    saved = False
    if persist and entry["names"]:
        saved = _save(kg_id, sig_key, entry["names"], entry["matrix"])
    # RESIDENT MEMORY MEANS QUERIED. A build triggered by a graph WRITE has nobody
    # waiting on it, so holding its matrix would charge memory for a graph that may
    # never be asked anything — a deployment that imports ten agent packs at startup
    # would carry ten matrices nothing reads. Persist, then release: the first real
    # question reads it back from disk in ~20 ms.
    # Only when it genuinely reached disk — releasing an unpersisted matrix would
    # throw the embedding work away and make the next query pay for it again.
    if not retain and saved:
        with _LOCK:
            _INDEX_CACHE.pop(key, None)
    logger.info(
        f"KG semantic index built: kg={kg_id} entities={len(entry['names'])} "
        f"{entry['bytes'] / 1e6:.1f}MB in {time.monotonic() - t0:.2f}s"
        f"{'' if (retain or not saved) else ' (persisted, not held in memory)'}")
    return entry


def warm(kg_id: str, list_entities_fn, signature=None, embedder=None,
         blocking: bool = False, retain: bool = True) -> bool:
    """Ensure an index exists for this graph version.

    Returns True when one is already resident. Otherwise starts ONE background build
    (a second caller joins it rather than starting another) and returns False, so the
    caller proceeds without it. ``blocking=True`` builds inline — for tests and for a
    deliberate pre-warm, never from a turn.

    ``retain=False`` says the caller is PREPARING the index rather than about to use it
    (a graph write, a pre-deploy backfill): it is persisted and then released, so
    resident memory stays a statement about which graphs are actually being queried.
    """
    if not kg_id or not is_enabled():
        return False
    sig_key = _sig_key(signature)
    key = (kg_id, sig_key)
    with _LOCK:
        if key in _INDEX_CACHE:
            return True
        if key in _BUILDING:
            return False
        _BUILDING.add(key)
    try:
        embedder = embedder or _resolve_embedder()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _BUILDING.discard(key)
        # A graph with no embedder never gets an index — every turn on it is lexical-only
        # with nothing saying why. WARNING once per (graph, process); the persist/load
        # failures below stay DEBUG because a cache that cannot be written still works.
        from trusted_data_agent.core.log_once import warn_once as _warn_once
        _warn_once(logger, f"kg-semantic-no-embedder:{kg_id}",
                   f"KG semantic index: no embedder for kg={kg_id} — semantic seeding "
                   f"stays off for this graph ({type(exc).__name__}: {exc})")
        return False
    persist = signature is not None
    if blocking:
        return _build_and_cache(key, kg_id, sig_key, list_entities_fn, embedder,
                                persist, retain) is not None
    threading.Thread(
        target=_build_and_cache,
        args=(key, kg_id, sig_key, list_entities_fn, embedder, persist, retain),
        name=f"kg-semantic-index-{str(kg_id)[:8]}", daemon=True).start()
    return False


def index_state(kg_id: str, signature=None) -> str:
    """``ready`` | ``building`` | ``cold`` | ``off`` — what the event reports.

    ``ready`` means *the next question will be answered semantically*, which is true
    whether the matrix is resident or merely persisted: a write-built index is released
    from memory on purpose, and reporting that as ``cold`` would tell an operator the
    index is still being built when it is finished and one query away from loading.
    """
    if not is_enabled():
        return "off"
    sig_key = _sig_key(signature)
    key = (kg_id, sig_key)
    with _LOCK:
        if key in _INDEX_CACHE:
            return "ready"
        if key in _BUILDING:
            return "building"
    p = _paths(kg_id, sig_key)
    try:
        if p and os.path.exists(p[0]) and os.path.exists(p[1]):
            return "ready"
    except Exception:  # noqa: BLE001
        pass
    return "cold"


def semantic_hits(
    kg_id: str,
    query: str,
    list_entities_fn,
    signature=None,
    embedder=None,
    top_k: int = 10,
    min_score: float = 0.35,
) -> List[Tuple[str, float]]:
    """[(entity_name, cosine)] for the query's nearest entities. Empty when disabled,
    while the index is still warming, on any failure, or when nothing clears
    ``min_score``.

    ``list_entities_fn`` is a zero-arg callable returning the KG's entity dicts (so this
    module never opens a store itself); ``signature`` scopes the cache to a graph
    version; ``embedder`` overrides the platform provider (tests inject a fake).
    """
    if not query or not kg_id or not is_enabled():
        return []
    try:
        sig_key = _sig_key(signature)
        key = (kg_id, sig_key)
        _sweep_idle()          # a shifting working set sheds here, not only on a build
        with _LOCK:
            idx = _INDEX_CACHE.get(key)
            if idx is not None:
                idx["used"] = time.monotonic()
        if idx is None:
            # A previous process already paid for this version — read it back rather
            # than re-embed. Only then fall through to a background build.
            idx = _load(kg_id, sig_key)
            if idx is not None:
                _remember(key, idx)
            else:
                warm(kg_id, list_entities_fn, signature, embedder)
                return []
        names = idx["names"]
        if not names:
            return []
        embedder = embedder or _resolve_embedder()
        q = _np.asarray(embedder.embed_query(query), dtype=_np.float32).ravel()
        if q.shape[0] != idx["matrix"].shape[1]:
            # The configured embedding model changed under a built index. Evicting it
            # from memory is not enough: the PERSISTED copy would be loaded again on the
            # next query and mismatch again, so the graph would stay unsearchable for
            # good. Discard both — the next query then finds nothing and rebuilds with
            # the model now configured.
            logger.warning(
                f"KG semantic index: embedding dimension changed under kg={kg_id} "
                f"(index {idx['matrix'].shape[1]}d, query {q.shape[0]}d) — "
                f"discarding it; it will rebuild with the configured model")
            with _LOCK:
                _INDEX_CACHE.pop(key, None)
            _discard_persisted(kg_id, sig_key)
            return []
        qn = float(_np.linalg.norm(q))
        if qn == 0.0:
            return []
        sims = idx["matrix"] @ (q / qn)              # rows are already unit-length
        k = min(int(top_k), sims.shape[0])
        if k <= 0:
            return []
        part = _np.argpartition(-sims, k - 1)[:k]    # linear top-k, no full sort
        part = part[_np.argsort(-sims[part])]
        return [(names[i], float(sims[i])) for i in part if sims[i] >= min_score]
    except Exception as exc:  # noqa: BLE001 — never break retrieval
        from trusted_data_agent.core.log_once import warn_once as _warn_once
        _warn_once(logger, f"kg-semantic-hits:{kg_id}",
                   f"KG semantic seeding failed (kg={kg_id}): {type(exc).__name__}: {exc}")
        return []
