"""Retrieval-anomaly monitoring — index-poisoning detection (RAG_TRUST_HARDENING Wave 5 B1).

An adversarial document crafted to be retrieved for MANY unrelated queries (an embedding
"hub") is invisible to per-chunk pattern matching — its text can be individually benign.
This module detects the *statistical* signature instead: per document, retrieval
**frequency** × **query diversity** (how lexically dispersed the queries that matched it
are). A high-frequency, high-diversity document is a suspicious hub worth human review.

Pure analysis over **persisted turns** (the workflow histories' `knowledge_retrieval_event`
chunks + the turns' queries) — no new logging, bounded by the caller's session window.
Honest cap (stated in every payload): this detects statistical anomalies, not semantic
poisoning per se — disposition is always the human's (never auto-quarantine, stop-line #4).
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Dict, Iterable, List

# A document must be retrieved at least this often before diversity means anything.
MIN_RETRIEVALS = 4
# Fraction of query PAIRS that share not a single content word, above which the
# matching queries are "unrelated". Same-topic queries virtually always share their
# topic word (refund, invoice, …), so this stays low for topical documents while a
# hub retrieved across unrelated domains approaches 1.0. Crisper and more
# interpretable than mean Jaccard distance, which reads high even for short
# same-topic queries.
DIVERSITY_THRESHOLD = 0.75

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{3,}")

NOTE = ("Statistical detection only: a high-frequency, high-diversity document is a "
        "suspicious retrieval hub, not proven poisoning — review it and decide "
        "(quarantine is one click; nothing is auto-quarantined).")


def _words(text: str) -> frozenset:
    return frozenset(w.lower() for w in _WORD_RE.findall(text or ""))


def _disjoint_pair_ratio(word_sets: List[frozenset]) -> float:
    """Fraction of query pairs with ZERO shared content words (the diversity metric)."""
    pairs = list(combinations(range(len(word_sets)), 2))
    if not pairs:
        return 0.0
    disjoint = sum(1 for i, j in pairs if not (word_sets[i] & word_sets[j]))
    return round(disjoint / len(pairs), 4)


def collect_retrievals(turns: Iterable[dict]) -> Dict[str, dict]:
    """Fold persisted turns into per-document retrieval stats:
    ``{document_id: {count, queries, filename, collections}}``. Turn shape is the
    stored workflow-history entry (``user_query`` + ``knowledge_retrieval_event``)."""
    docs: Dict[str, dict] = {}
    for t in turns or []:
        if not isinstance(t, dict):
            continue
        query = str(t.get("user_query") or "")
        ev = t.get("knowledge_retrieval_event") or {}
        chunks = ev.get("chunks") or []
        seen_this_turn = set()
        for c in chunks:
            if not isinstance(c, dict):
                continue
            doc_id = c.get("document_id")
            if not doc_id or doc_id in seen_this_turn:
                continue  # count a doc once per turn
            seen_this_turn.add(doc_id)
            rec = docs.setdefault(str(doc_id), {
                "count": 0, "queries": [], "filename": None, "collections": set()})
            rec["count"] += 1
            if query:
                rec["queries"].append(query)
            if not rec["filename"]:
                rec["filename"] = c.get("source") or c.get("filename")
            if c.get("collection_id") is not None:
                rec["collections"].add(c.get("collection_id"))
    return docs


def analyze(turns: Iterable[dict], *, min_retrievals: int = MIN_RETRIEVALS,
            diversity_threshold: float = DIVERSITY_THRESHOLD) -> dict:
    """The full analysis: per-doc stats + the flagged hubs. Pure + deterministic."""
    docs = collect_retrievals(turns)
    rows = []
    flagged = []
    for doc_id, rec in docs.items():
        word_sets = [_words(q) for q in rec["queries"] if q]
        diversity = _disjoint_pair_ratio(word_sets) if len(word_sets) >= 2 else 0.0
        row = {"document_id": doc_id, "filename": rec.get("filename"),
               "retrievals": rec["count"], "query_diversity": diversity,
               "collections": sorted(rec["collections"]),
               "sample_queries": rec["queries"][:5]}
        rows.append(row)
        if rec["count"] >= int(min_retrievals) and diversity >= float(diversity_threshold):
            flagged.append({**row, "why": (
                f"retrieved on {rec['count']} turn(s); {diversity:.0%} of the query "
                f"pairs share not a single content word — a hub signature")})
    rows.sort(key=lambda r: (-r["retrievals"], -r["query_diversity"]))
    return {
        "documents_analyzed": len(rows),
        "turns_analyzed": sum(1 for t in (turns or []) if isinstance(t, dict)),
        "min_retrievals": int(min_retrievals),
        "diversity_threshold": float(diversity_threshold),
        "flagged": flagged,
        "top_documents": rows[:20],
        "note": NOTE,
    }
