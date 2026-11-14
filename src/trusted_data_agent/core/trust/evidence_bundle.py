"""
Portable evidence bundles (PROP-009 §4.4) — a self-contained file anyone can verify with the
standalone verifier (``scripts/verify_evidence.py`` or the static verifier page), without
Uderia installed and without asking this server.

A bundle carries, per attestation: the record as it was signed (from the attestation history,
so a later re-sign never changes what an answer's evidence says), the signature, the public
key it was signed with and its fingerprint, and — up to a size limit (decision §9.2) — the
**exact bytes** whose sha256 is the signed version hash. Per answer: the sealed provenance
chain and the bill of materials, so the chain and the ``trust.bom`` step re-derive offline.

The bytes come from ONE source: ``attestation.content_capture``. The badge's own resolvers
compute the content hash through ``content_hash``; while a capture is open, ``content_hash``
records what it hashed. The bundle keeps the recorded bytes whose digest equals the signed
hash — so what a bundle carries is, by construction, what the signature binds. Content that
cannot be reproduced byte for byte here (an artefact changed since it was signed, a large
graph above the limit, a cached hash) is named per entry with its reason, never invented.

Answer text and trust stamps are included only on request (``include_receipt``) and only for
the owner's own answers; nothing else from a session — an export never widens what the owner
can already read.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trusted_data_agent.core.trust import store as _store
from trusted_data_agent.core.trust import attestation as _att
from trusted_data_agent.core.trust.evidence import all_parts

logger = logging.getLogger("quart.app")

FORMAT = "uderia.evidence.bundle"
VERSION = 1
DEFAULT_LIMIT_BYTES = 256 * 1024


def _sealed_answer(t: Dict[str, Any]):
    """The answer text the chain's ``turn_complete`` step sealed, and whether it did.

    Optimize keeps two answer fields and seals one of them; the other engines keep
    ``final_summary_text``. The receipt carries the field the chain covers, so a verifier
    can re-hash it (PROP-009 §7: the exported answer is checked, never only shown).
    """
    from trusted_data_agent.core.provenance import CONTENT_MAX_LEN
    cands = [t.get(k) for k in ("final_summary", "final_summary_text") if isinstance(t.get(k), str)]
    step = next((s for s in (t.get("provenance_chain") or [])
                 if isinstance(s, dict) and s.get("step_type") == "turn_complete"), None)
    if step:
        for c in cands:
            if hashlib.sha256(c[:CONTENT_MAX_LEN].encode("utf-8")).hexdigest() == step.get("content_hash"):
                return c, True
    return (cands[0] if cands else None), False

def content_limit() -> int:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        v = int(getattr(APP_CONFIG, "EVIDENCE_CONTENT_LIMIT_BYTES", DEFAULT_LIMIT_BYTES))
        return max(0, v)
    except Exception:  # noqa: BLE001
        return DEFAULT_LIMIT_BYTES


# ── the signed bytes ─────────────────────────────────────────────────────────

def _resolve_live(kind: str, artifact_id: str, owner_uuid: Optional[str]) -> None:
    """Run the badge's own resolver for this artefact, so its content_hash calls are captured."""
    if kind == "agent":
        from trusted_data_agent.core.trust import composition as _comp
        prof = _comp._load_profile(artifact_id, owner_uuid, None)
        if prof:
            _comp._resolve_profile_trust_detail(artifact_id, owner_uuid, profile=prof)
        return
    if kind == "agent_deployment":
        from trusted_data_agent.core.trust import composition as _comp
        _comp.resolve_deployment(artifact_id, owner_uuid)
        return
    if kind == "agent_pack":
        from trusted_data_agent.core.trust import pack as _pack
        refs = _pack.member_refs(artifact_id)
        refs = refs[0] if isinstance(refs, tuple) else refs
        if refs:
            _att.composition_hash(refs)
        return
    if kind == "ontology":
        # The graph hash is cached; the bytes are only reproduced by computing it afresh.
        from trusted_data_agent.core.trust import composition as _comp
        ents, rels = _comp.load_kg_graph(artifact_id, None)
        _comp.kg_content_hash(ents, rels)
        return
    from trusted_data_agent.core.trust import content as _content
    _content.live_hash(kind, artifact_id)


def signed_content(kind: str, artifact_id: str, version_hash: Optional[str],
                   owner_uuid: Optional[str], limit: Optional[int]) -> Dict[str, Any]:
    """``{included, data?, bytes, reason?}`` — the exact bytes that hash to ``version_hash``,
    when the artefact as it is now still produces them and they are within ``limit``
    (``None`` = no limit, the explicit "content on request")."""
    if not version_hash:
        return {"included": False, "reason": "nothing was signed"}
    if limit == 0:
        return {"included": False, "reason": "content not requested"}
    cap = _att.ContentCapture(limit=limit)
    try:
        with _att.content_capture(cap):
            _resolve_live(kind, artifact_id, owner_uuid)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"evidence bundle: content for {kind}/{artifact_id} unavailable: {e}")
        return {"included": False, "reason": "the artefact could not be read here"}
    hit = cap.seen.get(version_hash)
    if hit is None:
        return {"included": False,
                "reason": "the artefact has changed since it was signed (or is gone); its signed "
                          "content is not held here — only its hash is"}
    size, raw = hit
    if raw is None:
        return {"included": False, "bytes": size,
                "reason": f"larger than the bundle limit ({size} bytes > {cap.limit} bytes); "
                          "request it explicitly to include it"}
    return {"included": True, "encoding": "utf-8", "bytes": size, "data": raw}


# ── keys ─────────────────────────────────────────────────────────────────────

def _keys(fingerprints: Iterable[str], carried: Dict[str, str]) -> List[Dict[str, Any]]:
    known = {k.get("fingerprint"): k for k in _store.list_keys()}
    out = []
    for fp in sorted({f for f in fingerprints if f}):
        k = known.get(fp) or {}
        pem = k.get("public_key_pem") or carried.get(fp)
        if not pem:
            try:
                from trusted_data_agent.core import provenance
                from trusted_data_agent.core.provenance import get_key_fingerprint
                if get_key_fingerprint() == fp:
                    raw = provenance.get_provenance_public_key_pem()
                    pem = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            except Exception:  # noqa: BLE001
                pem = None
        out.append({"fingerprint": fp, "public_key_pem": pem,
                    "status": k.get("status") or ("carried" if fp in carried else None)})
    return out


def _attestation_entry(rec: Dict[str, Any], label: Optional[str], owner_uuid: Optional[str],
                       limit: Optional[int], source: str) -> Dict[str, Any]:
    pk = rec.get("signer_public_key")
    if isinstance(pk, bytes):
        pk = pk.decode("utf-8")
    e = {"kind": rec.get("kind"), "artifact_id": str(rec.get("artifact_id")), "label": label,
         "source": source, "history_id": rec.get("history_id") or rec.get("id"),
         "event": rec.get("event"),
         "version_hash": rec.get("version_hash"), "signed": bool(rec.get("signed")),
         "signature": rec.get("signature") or None, "signer_fingerprint": rec.get("signer_fingerprint"),
         "signer_public_key": pk, "signer_uuid": rec.get("signer_uuid"),
         "signed_at": rec.get("signed_at"),
         "payload": f"{rec.get('kind')}|{rec.get('version_hash')}",
         "legacy_payload": f"{rec.get('kind')}|{rec.get('artifact_id')}|{rec.get('version_hash')}"}
    e["content"] = signed_content(e["kind"], e["artifact_id"], e["version_hash"], owner_uuid, limit)
    # PROP-009 §4.5: the history row's canonical record, so the ledger entry it was entered
    # with can be re-derived offline.
    hid = e.get("history_id")
    hrow = rec if rec.get("event") and rec.get("recorded_at") is not None else (
        _store.history_entry(hid) if hid else None)
    if hrow:
        from trusted_data_agent.core.trust import ledger as _ledger
        e["ledger_record"] = _ledger.history_record(hrow)
    return e


# ── the ledger proof ─────────────────────────────────────────────────────────

#: The longest contiguous ledger segment a bundle carries to PROVE its records sit in the
#: chain under the signed checkpoint. Beyond it the bundle carries each record's own entry and
#: says the chain proof is the server's (GET /v1/evidence/ledger).
LEDGER_SEGMENT_CAP = 20000


def _ledger_ref(owner_uuid, sid, turn):
    from trusted_data_agent.core.trust import ledger as _ledger
    return _ledger.turn_ref(owner_uuid, sid, turn) if owner_uuid and turn is not None else None


def ledger_section(refs: List[str]) -> Optional[Dict[str, Any]]:
    """The signed checkpoint and the entries that prove ``refs`` are in the chain under it."""
    from trusted_data_agent.core.trust import ledger as _ledger
    try:
        conn = _store._connect()
        try:
            cp = _ledger.checkpoint(conn)
            own = _ledger.entries(conn, refs=refs) if refs else []
            if not own:
                return {"checkpoint": cp, "entries": [], "complete_segment": True,
                        "note": "no ledger entry covers the records in this bundle"}
            first = min(e["seq"] for e in own)
            if cp["seq"] - first + 1 <= LEDGER_SEGMENT_CAP:
                return {"checkpoint": cp, "entries": _ledger.entries(conn, since_seq=first),
                        "complete_segment": True}
            return {"checkpoint": cp, "entries": own, "complete_segment": False,
                    "note": "the chain from these records to the checkpoint is too long to carry; "
                            "each record's own entry is included, and the server proves the chain"}
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a bundle stands without its ledger proof
        logger.debug(f"evidence bundle: ledger section unavailable: {e}")
        return None


# ── custody (PROP-009 §4.6) ──────────────────────────────────────────────────

def _custody() -> Dict[str, Any]:
    try:
        from trusted_data_agent.core.signing import get_signer
        d = get_signer().describe()
        out = {"signer": d.get("signer"), "key_on_host": bool(d.get("key_on_host"))}
    except Exception:  # noqa: BLE001
        out = {"signer": "local", "key_on_host": True}
    try:
        from trusted_data_agent.core.trust import anchoring as _anchoring
        out["anchoring"] = _anchoring.config()["sink"] if _anchoring.enabled() else None
    except Exception:  # noqa: BLE001
        out["anchoring"] = None
    return out


def _limits(custody: Dict[str, Any]) -> str:
    base = "A signature proves what was recorded and that it is unchanged — not that an answer is right. "
    if not custody.get("key_on_host"):
        tail = ("The signing key is held outside this host: the server can ask for signatures but "
                "cannot read the key, so it cannot quietly re-sign history.")
    else:
        tail = ("The signing key is held on this host, so its administrator could rewrite and "
                "re-sign history.")
    if custody.get("anchoring"):
        tail += (" Ledger checkpoints are also published outside this host "
                 f"({custody['anchoring']}), so a rewrite would conflict with what was published.")
    return base + tail


# ── the bundle ───────────────────────────────────────────────────────────────

def build(owner_uuid: str, *, turns: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
          artifacts: Optional[List[Tuple[str, str]]] = None, content: str = "limit",
          labels: Optional[Dict[Tuple[str, str], str]] = None,
          title: Optional[str] = None, include_receipt: bool = False) -> Dict[str, Any]:
    """Build a bundle.

    ``turns``     ``[(session_id, turn_dict)]`` already loaded and owner-checked by the caller.
                  Each turn brings its chain, its bill of materials, and the attestation of
                  every part AS IT WAS SIGNED when the answer was given (by history row).
    ``artifacts`` ``[(kind, artifact_id)]`` — their CURRENT attestations (owner-scoped).
    ``content``   ``"limit"`` (default, decision §9.2) · ``"all"`` (explicit request) · ``"none"``.
    ``include_receipt`` adds, per answer, the question, the answer and its trust stamps (the
                  former answer receipt) — the owner's own answers only, so nothing widens.
    """
    limit = {"all": None, "none": 0}.get(content, content_limit())
    labels = labels or {}
    atts: List[Dict[str, Any]] = []
    seen = set()
    turn_out = []
    for sid, t in turns or []:
        bom = t.get("bom") if isinstance(t.get("bom"), dict) else None
        turn_out.append({"session_id": sid, "turn": t.get("turn"),
                         "answered_at": t.get("timestamp"),
                         "profile_tag": (bom or {}).get("profile_tag") or t.get("profile_tag"),
                         "bom": bom,
                         "provenance_chain": t.get("provenance_chain"),
                         "provenance_meta": t.get("provenance_meta"),
                         "parts_recorded": bom is not None,
                         "ledger_ref": _ledger_ref(owner_uuid, sid, t.get("turn"))})
        if include_receipt:
            answer, sealed = _sealed_answer(t)
            turn_out[-1]["receipt"] = {
                "user_query": t.get("user_query"),
                "answer": answer,
                "trust": {"grounding": t.get("grounding"), "guardrail_outbound": t.get("guardrail"),
                          "guardrail_inbound": t.get("guardrail_inbound"),
                          "governed_data": t.get("obda"), "honesty": t.get("honesty")}}
            if not sealed and answer:
                # Say so rather than let a verifier report an older record as altered.
                turn_out[-1]["receipt"]["answer_not_sealed"] = (
                    "the sealed chain does not cover this answer text (an older record)")
        for p in all_parts(bom):
            hid = p.get("history_id")
            if not hid or ("h", hid) in seen:
                continue
            seen.add(("h", hid))
            h = _store.history_entry(hid)
            if h:
                atts.append(_attestation_entry(h, p.get("label"), owner_uuid, limit, "as_signed_when_answered"))
    for kind, aid in artifacts or []:
        rec = _store.load_attestation(kind, aid, owner_uuid=owner_uuid)
        if not rec or ("c", kind, str(aid)) in seen:
            continue
        seen.add(("c", kind, str(aid)))
        atts.append(_attestation_entry(rec, labels.get((kind, str(aid))), owner_uuid, limit, "current"))
    try:
        from trusted_data_agent.core.provenance import get_key_fingerprint
        deployment_fp = get_key_fingerprint()
    except Exception:  # noqa: BLE001
        deployment_fp = None
    fps = {a.get("signer_fingerprint") for a in atts}
    fps |= {(t.get("provenance_meta") or {}).get("key_fingerprint") for t in turn_out}
    fps.add(deployment_fp)   # an embedded audit pack is signed with the deployment key
    carried = {a["signer_fingerprint"]: a["signer_public_key"] for a in atts
               if a.get("signer_fingerprint") and a.get("signer_public_key")}
    refs = [f"h:{a['history_id']}" for a in atts if a.get("history_id")]
    refs += [t["ledger_ref"] for t in turn_out if t.get("ledger_ref")]
    ledger = ledger_section(refs)
    custody = _custody()
    return {
        "format": FORMAT, "version": VERSION, "title": title, "ledger": ledger,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deployment_key_fingerprint": deployment_fp,
        "content_policy": {"mode": content, "limit_bytes": limit},
        "custody": custody,
        "keys": _keys(fps, carried),
        "attestations": atts,
        "turns": turn_out,
        "how_to_verify": (
            "Run scripts/verify_evidence.py on this file, or open the Uderia evidence verifier page "
            "and drop it in. Supply the deployment's public-key fingerprint from a separate source "
            "(its operator): the keys in this file prove the record is consistent, the fingerprint "
            "proves who signed it."),
        "limits": _limits(custody),
    }
