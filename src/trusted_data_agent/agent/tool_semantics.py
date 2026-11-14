"""The tool surface's SECOND ranker — meaning, where the identifier tokens are blind.

`tool_surface.select` offers a tool whose name / category / description tokens overlap the
question (precise, 0 tokens, explainable word by word). It cannot bridge a paraphrase:
"how much disk is each table consuming" shares no identifier with `dba_tableSpace`, "any
blanks in the customer records" none with `qlty_missingValues`. Until Oct 2026 that gap
was the model's to close by calling `TDA_FindTools` — a round trip a weak tool-calling
model does not reliably make (the Celeris measurement). This module closes it
deterministically: each tool's identity text is embedded ONCE per catalog, the question
once per turn, and the tools nearest in meaning WIDEN the offered set under one rule
(`tool_surface.semantic_pick`). The lexical ranking stays authoritative; a semantic hit
carries its own reason and its own score on the chip.

Calibrated on the platform's real 62-tool catalog (test/fixtures/tool_catalog_calibration.json):
an ABSOLUTE cosine threshold does not separate — the right tool reads 0.20 on one paraphrase
("how busy was the box" → `dba_resusageSummary`) while noise reads 0.31 on another — so the
rule is relative to the question's own best match: a floor, a ratio of the best, a cap.

The performance contract (the `kgstore/semantic_index.py` precedent, at catalog scale):
  * ONE model in the process — the platform's own `SentenceTransformerProvider`, which shares
    chromadb's class-level model cache with the RAG retriever loaded at boot.
  * A COLD model never blocks a turn: the provider is resolved on a background thread
    (`warm()` at startup, or the first governed turn); until it is ready the lexical ranking
    stands alone and the stamp says `semantic: warming`.
  * Catalog vectors are keyed by a CONTENT signature (a changed description re-embeds; an
    unchanged catalog costs one query encode, ~5 ms warm).
  * Never raises into a turn: any failure returns None (lexical stands) and is logged once.
  * The embedder is INJECTABLE (`set_embedder`) so the rule is testable without a model.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("quart.app")

# The rule's constants — read by the stamp so a reader sees what decided (never silent)
FLOOR = 0.18        # below this a match is noise, whatever the ranking says
RATIO = 0.80        # keep tools within this share of the question's own best match
CAP = 5             # at most this many semantic additions per selection
_MAX_CATALOGS = 32  # cached catalog matrices per process (one per distinct enabled set)

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_lock = threading.Lock()
_embedder: Any = None          # an object with embed_texts(list[str]) / embed_query(str)
_embedder_state = "cold"       # cold · warming · ready · unavailable
_catalogs: Dict[str, Dict[str, Any]] = {}   # signature → {"names": [...], "matrix": ndarray}
_warned = False


def model_name() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return str(getattr(APP_CONFIG, "RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2")
    except Exception:  # noqa: BLE001
        return "all-MiniLM-L6-v2"


def state() -> str:
    """cold · warming · ready · unavailable — what the stamp reports."""
    return _embedder_state


def set_embedder(obj: Any) -> None:
    """Inject an embedder (tests, or a deployment with its own). None resets to cold."""
    global _embedder, _embedder_state, _catalogs
    with _lock:
        _embedder = obj
        _embedder_state = "ready" if obj is not None else "cold"
        _catalogs = {}


def _resolve_sync() -> None:
    global _embedder, _embedder_state, _warned
    try:
        from trusted_data_agent.vectorstore.embedding_providers import SentenceTransformerProvider
        p = SentenceTransformerProvider.get_cached(model_name())
        p.embed_query("warm")      # the model's own lazy init happens here, off the turn path
        with _lock:
            _embedder, _embedder_state = p, "ready"
    except Exception as e:  # noqa: BLE001
        with _lock:
            _embedder_state = "unavailable"
        if not _warned:
            _warned = True
            logger.warning(f"[tool-semantics] embedding model unavailable — the tool surface ranks by words only ({e})")


def warm() -> None:
    """Resolve the embedder on a daemon thread. Idempotent; never blocks the caller."""
    global _embedder_state
    with _lock:
        if _embedder_state != "cold":
            return
        _embedder_state = "warming"
    threading.Thread(target=_resolve_sync, name="tool-semantics-warm", daemon=True).start()


def ready() -> bool:
    return _embedder is not None and _embedder_state == "ready"


# ── identity text ───────────────────────────────────────────────────────────────────────────
def tool_text(name: str, category: str = "", description: str = "") -> str:
    """What a tool IS, in words the model sees too: the identifier split into words, its
    category, its description (`dba_tableSpace` → "dba table space: DBA. Space per table…")."""
    words = _CAMEL_RE.sub(" ", str(name or "").replace("_", " ")).lower().strip()
    return f"{words}: {category}. {description}".strip()


def _signature(names: Sequence[str], texts: Sequence[str]) -> str:
    h = hashlib.sha256()
    for n, t in zip(names, texts):
        h.update(n.encode("utf-8")); h.update(b"\x00"); h.update(t.encode("utf-8")); h.update(b"\x01")
    return h.hexdigest()[:24]


def _embed(texts: List[str]):
    import numpy as np
    emb = _embedder
    if hasattr(emb, "embed_texts"):
        vecs = emb.embed_texts(texts)
    else:
        vecs = emb(texts)
    m = np.asarray(vecs, dtype="float32")
    if m.ndim == 1:
        m = m.reshape(1, -1)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _catalog_matrix(names: List[str], texts: List[str]):
    sig = _signature(names, texts)
    with _lock:
        hit = _catalogs.get(sig)
    if hit is not None:
        return hit
    m = _embed(texts)
    entry = {"names": list(names), "matrix": m}
    with _lock:
        if len(_catalogs) >= _MAX_CATALOGS:
            _catalogs.pop(next(iter(_catalogs)))
        _catalogs[sig] = entry
    return entry


def similarity(question: str, catalog: Sequence[Any], *, enrichment_names: Sequence[str] = ()) -> Optional[Dict[str, float]]:
    """Cosine of the question (plus the entities the ontology named) to every tool's identity
    text — `{tool_name: similarity}`. None when the embedder is not ready (the first governed
    turn after a cold start warms it in the background) or anything fails; never raises."""
    q = " ".join([str(question or "")] + [str(n or "").split(".")[-1] for n in (enrichment_names or ())]).strip()
    if not q or not catalog:
        return None
    if not ready():
        warm()
        return None
    try:
        names = [str(getattr(t, "name", "") or "") for t in catalog]
        texts = [tool_text(getattr(t, "name", ""), getattr(t, "category", "") or "", getattr(t, "description", "") or "") for t in catalog]
        entry = _catalog_matrix(names, texts)
        qv = _embed([q])[0]
        sims = entry["matrix"] @ qv
        return {n: float(s) for n, s in zip(entry["names"], sims)}
    except Exception as e:  # noqa: BLE001
        global _warned
        if not _warned:
            _warned = True
            logger.warning(f"[tool-semantics] similarity failed — ranking by words only this turn ({e})")
        return None
