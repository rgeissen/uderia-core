"""
Embedding models load from the local cache without asking the hub — ONE rule.

Every client-side embedder in the platform is a SentenceTransformer behind ChromaDB's
``SentenceTransformerEmbeddingFunction``. By default that constructor asks huggingface.co
whether each file of the model is still current (a HEAD per file) EVERY time a process
starts, even when the model is fully cached. When the hub is down — it answered 503 for
hours on 2026-11-03 — the client retries five times per file with exponential backoff
before it falls back to the cache: the production boot took 136 s to serve, every second
of it waiting on a third party for a model that was already on disk.

The rule: a model whose snapshot is in the local cache loads with ``local_files_only``
(no network at all); a model that is NOT cached downloads as before. Decided in code
from the cache's own state, per model, at load time — never a process-wide switch
(``HF_HUB_OFFLINE`` would also refuse the FIRST download of a model a user configures
later). The ``--offline`` launch flag still exists for air-gapped hosts; this module
simply makes the common case — cached model, hub unreachable or slow — cost nothing.

Every construction site goes through :func:`sentence_transformer_embedding_function`
(gate: ``test/test_embedding_offline.py`` pins the sites).
"""
from __future__ import annotations

import logging
import threading
import time
import os
from typing import Any, Dict

logger = logging.getLogger("quart.app")

#: The files whose presence in the snapshot says "this model can load offline". A
#: sentence-transformers model needs its ST layout (``modules.json``) and the transformer
#: config; a snapshot holding both was fully downloaded (the hub writes the small files last).
_SENTINEL_FILES = ("config.json", "modules.json")


def hf_repo_id(model_name: str) -> str:
    """The hub repository a sentence-transformers model name resolves to — a bare name
    (``all-MiniLM-L6-v2``) lives under the ``sentence-transformers`` organisation, as the
    library itself assumes; a qualified name (``BAAI/bge-small-en``) is already a repo id."""
    name = str(model_name or "").strip()
    if not name:
        return name
    if "/" in name or os.path.isdir(name):
        return name
    return f"sentence-transformers/{name}"


def model_is_cached(model_name: str) -> bool:
    """Whether the model's snapshot is in the local HuggingFace cache (the files that
    say so are present as real files — a cached 404 is not a cache hit). False on any
    error: a wrong answer here only costs the hub round-trip it would have cost anyway."""
    name = str(model_name or "").strip()
    if not name:
        return False
    if os.path.isdir(name):
        return True   # a local model directory needs no hub at all
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:  # noqa: BLE001 — no hub client, nothing to decide
        return False
    repo = hf_repo_id(name)
    try:
        for fname in _SENTINEL_FILES:
            hit = try_to_load_from_cache(repo, fname)
            if not isinstance(hit, str) or not os.path.isfile(hit):
                return False
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"embedding cache probe for {repo} failed: {e}")
        return False


def load_kwargs(model_name: str) -> Dict[str, Any]:
    """The extra constructor arguments for ONE model: ``local_files_only=True`` when its
    snapshot is cached (no hub request at all), nothing otherwise (download as before).
    When the process already runs offline (``HF_HUB_OFFLINE``), the library decides —
    nothing to add."""
    if str(os.environ.get("HF_HUB_OFFLINE", "")).strip().lower() in ("1", "true", "yes", "on"):
        return {}
    return {"local_files_only": True} if model_is_cached(model_name) else {}


_LOAD_LOCK = threading.RLock()   # see sentence_transformer_embedding_function


def sentence_transformer_embedding_function(model_name: str, **extra: Any):
    """THE constructor for a client-side embedder — ChromaDB's wrapper, with the cache
    rule applied. Resolved at call time so a test that patches the wrapper still sees
    its patch."""
    from chromadb.utils import embedding_functions
    kwargs = load_kwargs(model_name)
    if kwargs:
        logger.debug(f"Embedding model {model_name}: cached — loading without the hub")
    kwargs.update(extra)
    # ONE model load at a time, process-wide. Two threads constructing the same SentenceTransformer
    # at the same moment (the knowledge retriever and the tool ranker, both warming at start) made
    # torch raise "Cannot copy out of meta tensor; no data!" for BOTH on production, 12 Nov 2026 —
    # and the knowledge lane stayed down until a restart. The lock removes the race; the single
    # retry covers a load that still finds the lazily-initialised state half-built.
    with _LOAD_LOCK:
        try:
            return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name, **kwargs)
        except (RuntimeError, NotImplementedError) as e:
            if "meta tensor" not in str(e):
                raise
            logger.warning(f"Embedding model {model_name}: torch meta-tensor state on load — retrying once")
            time.sleep(1.0)
            return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name, **kwargs)
