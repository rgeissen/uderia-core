"""Ingest firewall — corpus screening at document ingestion (RAG_TRUST_HARDENING Wave 2 §2.1).

ONE screening function called at every document-ingestion path (upload, CDC re-ingest,
agent-pack import). Reuses the shipped detectors (`detect_prompt_injection`,
`detect_pii`, `detect_exfil_urls`) — never re-implemented. Per document it records:

  * injection pattern LABELS               (the critical class — gates signing, G2)
  * PII **categories + counts**            (labels only — values are NEVER stored, I2)
  * exfiltration-shaped URL count

**Observe-only at ingest** (I2): a finding never blocks an upload — the owner's corpus is
the owner's. Findings feed visibility (the manifest / Corpus Health panel), the earned
signature (G1/G2), and quarantine (§2.3). Master-gated on ``GUARDRAILS_ENABLED`` (I4):
off ⇒ no scans, byte-identical behavior. Every store is fail-open — a scan failure never
affects ingestion.

Retrieval-time note (deliberate deviation from the scope's I3 *optimization* wording):
the retrieval firewall keeps LIVE pattern matching — the chunks are already in memory and
the regex is cheaper than a per-chunk DB lookup for stored flags. Stored flags power the
manifest, signing, and quarantine instead; the protection is identical either way.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from trusted_data_agent.agent.guardrails.primitives import (
    detect_prompt_injection, detect_pii, detect_exfil_urls,
)

logger = logging.getLogger("quart.app")

SCAN_VERSION = 1

# Extensions whose bytes decode to meaningful text (full-fidelity scan). Anything else
# (pdf/docx/binary) is scanned best-effort over the decodable fragments and labelled so.
_TEXTY_EXTS = frozenset({"txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml",
                         "html", "htm", "xml", "rst", "log", "sql", "py", "js", "ts"})


def _enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "GUARDRAILS_ENABLED", False))
    except Exception:
        return False


def screen_text(text: str) -> dict:
    """Screen one text for the three finding classes. Pure, deterministic, 0-token.
    PII is reported as ``{label: count}`` — the values themselves are never returned."""
    text = text or ""
    injection = sorted({label for label, _ in detect_prompt_injection(text)})
    pii_counts: dict = {}
    for label, _s, _e, _v in detect_pii(text):
        pii_counts[label] = pii_counts.get(label, 0) + 1
    exfil = detect_exfil_urls(text)
    return {"injection": injection, "pii": pii_counts, "exfil_urls": len(exfil)}


def screen_if_flagged(text: str) -> Optional[dict]:
    """Read-time variant for inspection surfaces (the chunk browser): screen one chunk
    and return the findings dict only when something was found — ``None`` means clean.
    Same detectors as ingest; labels/counts only, matched values never returned."""
    s = screen_text(text)
    if s["injection"] or s["pii"] or s["exfil_urls"]:
        return s
    return None


def screen_document(filename: str, content) -> dict:
    """Screen a document's content (str or bytes) → the stored scan record.

    ``mode``: ``text`` (full-fidelity — the content was real text) or
    ``binary_best_effort`` (bytes of a non-text format decoded lossily; embedded plain
    text is still screened, but the record says so honestly — charter #19)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="ignore")
        mode = "text" if ext in _TEXTY_EXTS else "binary_best_effort"
    else:
        text = str(content or "")
        mode = "text"
    findings = screen_text(text[:2_000_000])  # bound pathological inputs
    return {
        "v": SCAN_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        **findings,
    }


def scan_and_store(document_id: str, filename: str, content,
                   collection_id: Optional[int] = None) -> Optional[dict]:
    """The one-liner every ingestion path calls after persisting a document row:
    master-gated screen + store on ``knowledge_documents.scan_json``. Returns the scan
    (or None when gated/failed). NEVER raises and never blocks ingestion (I2)."""
    try:
        if not _enabled():
            return None
        scan = screen_document(filename, content)
        from trusted_data_agent.core.collection_db import get_collection_db
        get_collection_db().set_document_scan(document_id, scan)
        if scan.get("injection"):
            logger.warning(
                f"[IngestFirewall] document '{filename}' ({document_id}) matched "
                f"prompt-injection pattern(s): {', '.join(scan['injection'])} — recorded "
                f"(observe-only; ingestion unaffected)")
            _record_ingest_event(document_id, filename, collection_id, scan)
        return scan
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[IngestFirewall] scan skipped (fail-open): {e}")
        return None


def _record_ingest_event(document_id, filename, collection_id, scan):
    """An injection finding at ingest is a trust event (labels only, never content).
    Best-effort: the inbox row is visibility, never a gate."""
    try:
        from trusted_data_agent.core import trust_events
        # The ingestion paths don't all carry a user context; resolve the collection
        # owner so the event lands in the right inbox.
        user_uuid = None
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            if collection_id is not None:
                coll = get_collection_db().get_collection_by_id(int(collection_id))
                user_uuid = (coll or {}).get("user_id")
        except Exception:
            pass
        if not user_uuid:
            return
        _labels = scan.get("injection") or []
        summary = (f"Ingest firewall: document '{filename}' matched prompt-injection "
                   f"{'pattern' if len(_labels) == 1 else 'patterns'}: "
                   f"{', '.join(_labels)} — recorded (observe-only; consider quarantining)")
        trust_events.record_event(
            user_uuid, "corpus_ingest_injection", summary, severity="warning",
            payload={"document_id": document_id, "collection_id": collection_id,
                     "labels": scan.get("injection") or []})
        trust_events.broadcast_notice(user_uuid, "corpus_ingest_injection", summary,
                                      severity="warning")
    except Exception:
        pass
