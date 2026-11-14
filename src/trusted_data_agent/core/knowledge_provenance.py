"""Knowledge source provenance — RAG Trust Hardening Wave 4 (P1, implements the OBDA-READ
scope's K3/O5 once, here).

Enriches retrieved-chunk records with the *source* facts a consumer needs to weigh them:

  * ``source_trust``       — the corpus's integrity-signature state (``signed`` 🟢 /
                             ``changed`` 🟠 / ``unsigned`` ⚪). K2's no-conflation rule:
                             this is a SOURCE-integrity guarantee, never a consumption
                             guarantee — the answer stays honestly monitored-lane.
  * ``verified_days_ago``  — staleness from the CDC metadata (``last_checked_at``);
                             ``sync_off`` True when CDC is disabled for the doc — honest,
                             never blank.
  * ``sensitivity``        — the collection's governance level (Wave 5 B2), when set.
  * ``collection_name``    — the repository's NAME (the record's ``name``, the convention
                             every retriever uses for this key — never its internal vector
                             store id). Set only when absent. Without it a reader saw a
                             document's title or file name standing in for the repository,
                             and one document under two labels read as two sources.

Everything here is read-side surfacing of records that already exist — no new tracking.
The Trust Membrane is imported defensively (the Ontology Suite is removable → the trust
field is simply absent). Fail-open throughout: enrichment failure returns the chunks
unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, List, Optional

logger = logging.getLogger("quart.app")


def _trust_state(collection_id) -> Optional[str]:
    """signed | changed | unsigned, or None when the trust suite is absent."""
    try:
        from trusted_data_agent.core.trust import store as trust_store
        from trusted_data_agent.core.trust.content import knowledge_content
        attested = trust_store.attested_hash("knowledge", str(collection_id))
        if not attested:
            return "unsigned"
        live = knowledge_content(str(collection_id))[2]
        return "signed" if attested == live else "changed"
    except Exception:
        return None  # suite absent / resolution failed → honest absence


def _days_ago(iso_ts: Optional[str]) -> Optional[int]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - ts).days)
    except Exception:
        return None


def enrich_chunks(chunks: Iterable[dict]) -> List[dict]:
    """Return NEW chunk dicts carrying the provenance fields. One trust/sensitivity
    resolution per distinct collection, one doc-row read per distinct document.
    Never raises; unresolvable fields are omitted (honest absence, never fabricated)."""
    chunks = [dict(c) for c in (chunks or []) if isinstance(c, dict)]
    if not chunks:
        return chunks
    try:
        from trusted_data_agent.core.collection_db import get_collection_db
        db = get_collection_db()

        coll_cache: dict = {}
        doc_cache: dict = {}
        for c in chunks:
            cid = c.get("collection_id")
            if cid is not None and cid not in coll_cache:
                info = {}
                st = _trust_state(cid)
                if st:
                    info["source_trust"] = st
                try:
                    coll = db.get_collection_by_id(int(cid)) or {}
                    if coll.get("sensitivity"):
                        info["sensitivity"] = coll["sensitivity"]
                    if coll.get("name"):
                        info["_repository_name"] = str(coll["name"])
                except Exception:
                    pass
                coll_cache[cid] = info
            doc_id = c.get("document_id")
            if doc_id and doc_id not in doc_cache:
                stale = {}
                try:
                    doc = db.get_document_by_id(str(doc_id)) or {}
                    days = _days_ago(doc.get("last_checked_at") or doc.get("updated_at"))
                    if days is not None:
                        stale["verified_days_ago"] = days
                    stale["sync_off"] = not bool(doc.get("sync_enabled"))
                except Exception:
                    pass
                doc_cache[doc_id] = stale
            _info = dict(coll_cache.get(cid) or {})
            _repo = _info.pop("_repository_name", None)
            c.update(_info)
            if _repo and not c.get("collection_name"):
                c["collection_name"] = _repo
            c.update(doc_cache.get(doc_id) or {})
        return chunks
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[KnowledgeProvenance] enrichment skipped (fail-open): {e}")
        return chunks


def sensitivity_note_html(chunks: Iterable[dict]) -> Optional[str]:
    """Wave 5 (B2) — the outbound sensitivity annotation: when the answer drew on a
    ``confidential`` repository, a theme-aware note says so (deterministic governance
    metadata, honestly labelled as such — no cryptographic claim). None when no
    confidential source was used."""
    try:
        names = sorted({str(c.get("collection_name") or c.get("source") or "a repository")
                        for c in (chunks or [])
                        if isinstance(c, dict)
                        and str(c.get("sensitivity") or "").lower() == "confidential"})
        if not names:
            return None
        from trusted_data_agent.agent import answer_surface as _ans
        return _ans.note("confidential",
                         f"This answer draws on confidential knowledge source(s): {', '.join(names[:3])}. "
                         "Handle accordingly.")
    except Exception:
        return None
