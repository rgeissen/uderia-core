"""
Signature renewal after a sync that changed content (Oct 2026).

A knowledge repository that is kept current by the CDC sync changes under its signature:
every changed document is re-scanned by the ingest firewall (a finding the owner had
acknowledged on the OLD version comes back open on the new one), the repository's content
hash moves, and so does the hash of every agent that binds the repository. Until now that
left three human acts owed after every scheduled sync — re-acknowledge, re-sign the
repository, re-sign each agent — and a documentation corpus that legitimately stores
injection examples owed them daily.

This module is the owner's STANDING policy for that, per repository (two columns on
``collections``, set from the Task Scheduler's Platform Jobs tab):

  * ``sync_auto_ack_findings`` — acknowledge new injection findings on the documents a
    sync changed, with a generated reason that names the run and the policy;
  * ``sync_auto_resign`` — renew the repository's signature and then the signature of
    every agent that binds it, as a RENEWAL only: an artefact that was never signed is
    never signed here, and the validator still decides. Then the renewal is CARRIED:
    the seal of every installed pack that ships the repository or a renewed agent is
    renewed (its composition moved with them), and the pack's subscribers are re-synced
    through the publisher's own fan-out (``fan_out_pack_sync``), which reinstates the
    renewed attestations onto their copies by content hash. A subscriber's copy binds
    the publisher's collection by reference and has its OWN owner-scoped attestation,
    so without this step every content-changing sync left every subscriber's agent
    reading *changed* although the publisher's was renewed — the uderia_expert
    consumers re-signed @UDERIA by hand after every daily documentation sync.

Independently of either policy, an acknowledgement is CARRIED FORWARD across a re-ingest
when the document's finding labels did not widen (``carry_forward_acknowledgement``): the
judgement "these examples are legitimate" was made about those labels, and a re-ingest
that reports the same labels has not asked a new question. A widened label set is a new
question, and stays open unless the policy answers it.

What keeps the signature EARNED rather than asserted:

  * authority is the owner's, checked as the sign-off route checks it (approver tier;
    refused under separation of duties) — a policy cannot grant what the owner lacks;
  * the act is recorded as ``event = "renew"`` with ``verdict["renewal"]`` naming the
    run, the repository, the files and the policy — the attestation history says
    "renewed by the scheduled sync", never "signed by the owner";
  * every act and every refusal is a trust event in the owner's inbox (an automatic
    acknowledgement counts as work to notice — it is NOT a receipt);
  * the validator's refusal stands (an unscreened or unresolved corpus is not renewed),
    and the refusal is in the inbox with the reason, so the act owed is named.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import store as _store
from . import content as _content

logger = logging.getLogger("quart.app")

KNOWLEDGE_KIND = "knowledge"
AGENT_KIND = "agent"

#: the trust event types this module records
EVENT_AUTO_ACK = "corpus_finding_auto_ack"          # a finding acknowledged under the policy
EVENT_ACK_CARRIED = "corpus_finding_ack_carried"     # a prior acknowledgement carried forward
EVENT_RENEWED = "signature_renewed"                  # a signature re-issued under the policy
EVENT_RENEWAL_REFUSED = "signature_renewal_refused"  # the policy asked; the gate said no
EVENT_RENEWAL_CARRIED = "signature_renewal_carried"  # the renewal reached a pack's subscribers

PACK_KIND = "agent_pack"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the carry-forward rule (pure) ────────────────────────────────────────────

def carry_forward_acknowledgement(prior_scan: Optional[dict], new_scan: Optional[dict],
                                  *, prior_content_hash: Optional[str] = None,
                                  at: Optional[str] = None) -> Optional[dict]:
    """The new scan record with the prior acknowledgement carried onto it, or ``None``.

    Carried iff the prior scan was acknowledged, the new scan has findings, and the new
    label set is a subset of the prior one (the judgement covered these labels). A widened
    set, a new scan without findings, or a prior scan never acknowledged → ``None``.
    The carried record keeps the prior reason and time and adds ``acknowledged_carried``
    naming what it was carried from, so the scan manifest (and the signature it feeds)
    tells a carried acknowledgement from a fresh one.
    """
    if not isinstance(prior_scan, dict) or not isinstance(new_scan, dict):
        return None
    if not prior_scan.get("acknowledged"):
        return None
    new_labels = set(new_scan.get("injection") or [])
    if not new_labels:
        return None
    prior_labels = set(prior_scan.get("injection") or [])
    if not new_labels.issubset(prior_labels):
        return None
    if new_scan.get("acknowledged"):
        return None
    out = dict(new_scan)
    out["acknowledged"] = True
    out["acknowledged_reason"] = prior_scan.get("acknowledged_reason") or ""
    out["acknowledged_at"] = prior_scan.get("acknowledged_at")
    out["acknowledged_carried"] = {
        "at": at or _now_iso(),
        "from_content_hash": prior_content_hash,
        "labels": sorted(prior_labels),
    }
    return out


# ── the shared sign act for a knowledge repository ───────────────────────────

async def sign_knowledge_repository(collection_id, signer_uuid: str, *,
                                    event: str = "sign",
                                    provenance: Optional[dict] = None,
                                    db_path: Optional[str] = None) -> Dict[str, Any]:
    """Validate a knowledge repository and sign it — the ONE act behind the sign-off
    route and the scheduled renewal.

    With the guardrails master on the signature is EARNED: the ingest firewall's stored
    manifest gates it (100% screened, critical findings resolved). Returns
    ``{signed, reason, verdict, version_hash, fingerprint, envelope}``; ``reason`` is
    ``not_found``, the validator's status when it refused, or ``None``.
    """
    from .attestation import get_validator
    hashes, model, vhash, coll = _content.knowledge_content(str(collection_id))
    if hashes is None:
        return {"signed": False, "reason": "not_found", "verdict": {}, "version_hash": None,
                "fingerprint": None, "envelope": None}
    scan_manifest = None
    try:
        from trusted_data_agent.core.config import APP_CONFIG as _cfg
        if getattr(_cfg, "GUARDRAILS_ENABLED", False):
            from trusted_data_agent.core.collection_db import get_collection_db
            scan_manifest = get_collection_db().get_scan_manifest(int(collection_id))
    except Exception:  # noqa: BLE001
        scan_manifest = None
    sample_texts, scan_complete = await _content.knowledge_sample_texts(coll)
    verdict = get_validator(KNOWLEDGE_KIND)(
        hashes, embedding_model=model, sample_texts=sample_texts,
        scan_complete=scan_complete, scan_manifest=scan_manifest)
    vd = verdict.to_dict()
    if not verdict.signable:
        return {"signed": False, "reason": verdict.status, "verdict": vd,
                "version_hash": vhash, "fingerprint": None, "envelope": None}
    if provenance:
        vd = {**vd, "renewal": dict(provenance)}
    env = _store.sign(KNOWLEDGE_KIND, str(collection_id), vhash, verdict=vd,
                      signer_uuid=signer_uuid, db_path=db_path, event=event)
    return {"signed": bool(env.get("signed")), "reason": None, "verdict": vd,
            "version_hash": vhash, "fingerprint": env.get("key_fingerprint"),
            "envelope": env}


# ── authority ────────────────────────────────────────────────────────────────

def _owner_tier(user_uuid: str) -> Optional[str]:
    """The owner's profile tier, read while the ORM session is open (never after)."""
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_uuid).first()
            return getattr(user, "profile_tier", None) if user else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.renewal: owner tier lookup failed for {user_uuid}: {e}")
        return None


def signer_authority(user_uuid: str, *, tier: Optional[str] = None) -> Tuple[bool, str]:
    """May this owner sign? The sign-off route's rule, applied to the policy's owner:
    approver tier, and no self-signing under an approval policy. A policy can never
    grant what the owner could not do by hand."""
    from trusted_data_agent.kgstore.settings import get_ontology_governance_policy
    tier = tier if tier is not None else _owner_tier(user_uuid)
    if tier is None:
        return False, "the repository owner's account could not be resolved"
    policy = get_ontology_governance_policy()
    if tier not in policy.approver_tiers:
        return False, f"tier '{tier}' is not permitted to sign off"
    if policy.require_approval:
        return False, ("separation of duties: the owner cannot sign their own artifact "
                       "under the current approval policy")
    return True, ""


# ── which agents bind this repository ────────────────────────────────────────

def agents_bound_to(collection_id, user_uuid: str, *,
                    db_path: Optional[str] = None) -> List[Tuple[str, str, Dict]]:
    """``[(profile_id, tag, profile)]`` of the owner's agents that consume this knowledge
    repository — by the SAME ingredient rule the badge uses (``agent_ingredient_refs``),
    so a repository an agent is bound to but never retrieves from does not count."""
    from . import composition as _comp
    from trusted_data_agent.core.config_manager import get_config_manager
    want = (KNOWLEDGE_KIND, str(collection_id))
    out: List[Tuple[str, str, Dict]] = []
    try:
        profiles = get_config_manager().get_profiles(user_uuid) or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.renewal: profile listing failed for {user_uuid}: {e}")
        return out
    for p in profiles:
        pid = str((p or {}).get("id") or "")
        if not pid:
            continue
        try:
            refs = _comp.agent_ingredient_refs(pid, user_uuid, profile=p, db_path=db_path)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"trust.renewal: ingredient refs failed for {pid}: {e}")
            continue
        if any((r.kind, str(r.artifact_id)) == want for r in refs):
            out.append((pid, str(p.get("tag") or pid), p))
    return out


# ── which packs carry the repository, and who subscribes to them ─────────────

def packs_carrying(collection_id, agent_ids, user_uuid, *,
                   db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """``[{installation_id, name, listing_id}]`` of the owner's installed packs whose
    resources include the repository or one of ``agent_ids`` — the packs whose
    composition the renewal moved. ``listing_id`` is the pack's marketplace listing
    (``None`` when it was never published: nothing to carry to)."""
    from trusted_data_agent.core import sqlite_util
    from trusted_data_agent.core.agent_pack_db import _DEFAULT_DB_PATH
    agent_ids = [str(a) for a in (agent_ids or []) if a]
    out: List[Dict[str, Any]] = []
    try:
        conn = sqlite_util.connect(db_path or _DEFAULT_DB_PATH)
        try:
            marks = ",".join("?" * len(agent_ids)) if agent_ids else "NULL"
            rows = conn.execute(
                "SELECT DISTINCT i.id, i.name, m.id AS listing_id "
                "FROM agent_pack_resources r "
                "JOIN agent_pack_installations i ON i.id = r.pack_installation_id "
                "LEFT JOIN marketplace_agent_packs m ON m.source_installation_id = i.id "
                "WHERE i.owner_user_id = ? AND ("
                "  (r.resource_type = 'collection' AND CAST(r.resource_id AS TEXT) = ?)"
                f"  OR (r.resource_type = 'profile' AND r.resource_id IN ({marks}))) "
                "ORDER BY i.id",
                (str(user_uuid), str(collection_id), *agent_ids)).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.renewal: pack lookup failed for collection {collection_id}: {e}")
        return out
    for r in rows:
        out.append({"installation_id": str(r[0]), "name": r[1] or str(r[0]),
                    "listing_id": r[2]})
    return out


async def fan_out_subscribers(listing_id: str) -> Dict[str, int]:
    """Carry the renewal to a listing's subscribers — the publisher's OWN fan-out
    (the one the import route and ``POST /v1/agent-packs/<id>/sync-subscribers`` run),
    which re-materializes each copy and reinstates the publisher's attestations onto it
    by content hash. Never raises; ``{synced, failed}``."""
    try:
        from trusted_data_agent.api.agent_pack_routes import fan_out_pack_sync
        res = await fan_out_pack_sync(str(listing_id))
        return {"synced": int(res.get("synced") or 0), "failed": int(res.get("failed") or 0)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.renewal: subscriber fan-out for listing {listing_id} failed: {e}")
        return {"synced": 0, "failed": -1, "error": str(e)}


def renew_pack_seal(installation_id: str, user_uuid: str, *, provenance: Optional[dict],
                    db_path: Optional[str] = None) -> Dict[str, Any]:
    """Renew a pack's seal — the aggregate gate the sign-off route applies (every member
    🟢), as a RENEWAL only: a pack never sealed is not sealed here.
    ``{signed, reason, blocking?, version_hash?}``."""
    from . import pack as _pack
    from .attestation import agent_signable, composition_hash
    prior = _store.load_attestation(PACK_KIND, str(installation_id), db_path=db_path)
    if not (prior and prior.get("signed")):
        return {"signed": False, "reason": "never_signed"}
    refs, _own = _pack.member_refs(str(installation_id))
    if not refs:
        return {"signed": False, "reason": "no_members"}
    ok, blocking = agent_signable(refs)
    if not ok:
        labels = {(r.kind, str(r.artifact_id)): getattr(r, "label", None) for r in refs}
        return {"signed": False, "reason": "unsigned_members",
                "blocking": [{"kind": k, "artifact_id": a,
                              **({"label": labels[(k, a)]} if labels.get((k, a)) else {})}
                             for (k, a) in blocking]}
    comp = composition_hash(refs)
    verdict = {"renewal": dict(provenance)} if provenance else None
    env = _store.sign(PACK_KIND, str(installation_id), comp, verdict=verdict,
                      signer_uuid=user_uuid, db_path=db_path, event="renew")
    return {"signed": bool(env.get("signed")), "reason": None if env.get("signed") else "unsigned",
            "version_hash": comp}


# ── the post-sync act ────────────────────────────────────────────────────────

def _event(user_uuid: str, event_type: str, summary: str, *, severity: str,
           payload: dict, db_path: Optional[str] = None) -> None:
    """Record + broadcast (the standing rule every trust act follows). Never raises."""
    try:
        from trusted_data_agent.core import trust_events
        trust_events.record_event(user_uuid, event_type, summary, severity=severity,
                                  payload=payload, db_path=db_path)
        trust_events.broadcast_notice(user_uuid, event_type, summary, severity=severity)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"trust.renewal: event {event_type} not recorded: {e}")


def _badge_refresh(user_uuid: str, kind: str, act: str) -> None:
    """TC-36 — a recorded trust action broadcasts live so an open Trust Circle re-reads."""
    try:
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(user_uuid, "sign_off",
                                      f"{kind} {act} — trust badge updated", severity="info")
    except Exception:  # noqa: BLE001
        pass


def policy_of(collection: dict) -> Dict[str, bool]:
    return {"ack": bool(int((collection or {}).get("sync_auto_ack_findings") or 0)),
            "resign": bool(int((collection or {}).get("sync_auto_resign") or 0))}


async def renew_after_sync(collection: dict, sync_result: dict, user_uuid: str, *,
                           trigger: str = "scheduled",
                           db=None, db_path: Optional[str] = None,
                           tier: Optional[str] = None) -> Dict[str, Any]:
    """Apply the repository's standing policy after a sync. Pure bookkeeping when nothing
    changed or no policy is set; never raises (the sync's own result is never lost to a
    renewal failure). Returns the renewal record the sync result carries as ``renewal``.
    """
    policy = policy_of(collection)
    updated_ids = list(sync_result.get("updated_doc_ids") or [])
    out: Dict[str, Any] = {"policy": policy, "trigger": trigger, "acknowledged": [],
                           "knowledge": None, "agents": [], "packs": []}
    if not updated_ids or not (policy["ack"] or policy["resign"]):
        out["reason"] = "nothing_changed" if not updated_ids else "no_policy"
        return out
    try:
        return await _renew(collection, sync_result, user_uuid, updated_ids, policy, out,
                            trigger=trigger, db=db, db_path=db_path, tier=tier)
    except Exception as e:  # noqa: BLE001
        logger.error(f"trust.renewal: renewal after sync of collection "
                     f"{(collection or {}).get('id')} failed: {e}", exc_info=True)
        out["error"] = str(e)
        return out


async def _renew(collection, sync_result, user_uuid, updated_ids, policy, out, *,
                 trigger, db, db_path, tier):
    if db is None:
        from trusted_data_agent.core.collection_db import get_collection_db
        db = get_collection_db()
    cid = int(collection["id"])
    name = collection.get("name") or collection.get("collection_name") or str(cid)
    run_at = _now_iso()
    files = list(sync_result.get("updated_files") or [])
    provenance = {"by": f"{trigger}_sync", "collection_id": cid, "collection_name": name,
                  "run_at": run_at, "updated": len(updated_ids), "updated_files": files[:50],
                  "policy": "sync_auto_resign", "policy_owner": user_uuid}

    # 1. acknowledge new findings on the changed documents
    if policy["ack"]:
        for doc_id in updated_ids:
            doc = db.get_document_by_id(doc_id) or {}
            raw = doc.get("scan_json")
            if not raw:
                continue
            try:
                scan = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            labels = sorted(scan.get("injection") or [])
            if not labels or scan.get("acknowledged") or doc.get("quarantined"):
                continue
            filename = doc.get("filename") or doc_id
            reason = (f"Acknowledged automatically by the {trigger} sync of '{name}' at "
                      f"{run_at} under the owner's standing policy for this repository "
                      f"(auto-acknowledge findings on changed documents); labels: "
                      f"{', '.join(labels)}")
            if not db.acknowledge_document_finding(doc_id, reason):
                continue
            out["acknowledged"].append({"document_id": doc_id, "filename": filename,
                                        "labels": labels})
            _event(user_uuid, EVENT_AUTO_ACK,
                   f"Finding on '{filename}' acknowledged AUTOMATICALLY by the {trigger} "
                   f"sync of '{name}' (labels: {', '.join(labels)}) — the repository's "
                   f"standing policy; review it if these examples are not legitimate",
                   severity="info",
                   payload={"document_id": doc_id, "collection_id": cid, "labels": labels,
                            "automatic": True, "reason": reason}, db_path=db_path)

    # 2. renew the repository's signature, then its agents'
    if not policy["resign"]:
        return out
    prior = _store.load_attestation(KNOWLEDGE_KIND, str(cid), db_path=db_path)
    if not (prior and prior.get("signed")):
        out["knowledge"] = {"signed": False, "reason": "never_signed"}
        return out
    ok, why = signer_authority(user_uuid, tier=tier)
    if not ok:
        out["knowledge"] = {"signed": False, "reason": "not_permitted", "detail": why}
        _event(user_uuid, EVENT_RENEWAL_REFUSED,
               f"Signature of '{name}' NOT renewed after the {trigger} sync: {why} — "
               f"sign the repository and its agents by hand, or change the policy",
               severity="warning",
               payload={"kind": KNOWLEDGE_KIND, "artifact_id": str(cid), "reason": why,
                        "collection_id": cid}, db_path=db_path)
        return out
    res = await sign_knowledge_repository(cid, signer_uuid=user_uuid, event="renew",
                                          provenance=provenance, db_path=db_path)
    note = (res.get("verdict") or {}).get("note")
    out["knowledge"] = {"signed": res["signed"], "reason": res.get("reason"), "note": note,
                        "version_hash": res.get("version_hash")}
    if not res["signed"]:
        why = note or res.get("reason") or "the validator refused"
        _event(user_uuid, EVENT_RENEWAL_REFUSED,
               f"Signature of '{name}' NOT renewed after the {trigger} sync: {why}",
               severity="warning",
               payload={"kind": KNOWLEDGE_KIND, "artifact_id": str(cid),
                        "reason": res.get("reason"), "note": note, "collection_id": cid},
               db_path=db_path)
        return out
    _event(user_uuid, EVENT_RENEWED,
           f"Signature of '{name}' renewed by the {trigger} sync ({len(updated_ids)} "
           f"document(s) changed) — recorded as a renewal, not a sign-off",
           severity="info",
           payload={"kind": KNOWLEDGE_KIND, "artifact_id": str(cid), "collection_id": cid,
                    "version_hash": res.get("version_hash"), "updated_files": files[:50]},
           db_path=db_path)
    _badge_refresh(user_uuid, KNOWLEDGE_KIND, "renewed")

    from . import composition as _comp
    for pid, tag, profile in agents_bound_to(cid, user_uuid, db_path=db_path):
        prior_a = _store.load_attestation(AGENT_KIND, pid, db_path=db_path, owner_uuid=user_uuid)
        if not (prior_a and prior_a.get("signed")):
            out["agents"].append({"profile_id": pid, "tag": tag, "signed": False,
                                  "reason": "never_signed"})
            continue
        r = _comp.sign_agent(pid, user_uuid, signer_uuid=user_uuid, profile=profile,
                             db_path=db_path, event="renew", provenance=provenance)
        rec = {"profile_id": pid, "tag": tag, "signed": bool(r.get("signed"))}
        if r.get("blocking"):
            rec["reason"] = "unsigned_ingredients"
            rec["blocking"] = r["blocking"]
            names = ", ".join(b.get("label") or f"{b.get('kind')} {b.get('artifact_id')}"
                              for b in r["blocking"][:5])
            _event(user_uuid, EVENT_RENEWAL_REFUSED,
                   f"Signature of agent @{tag} NOT renewed after the {trigger} sync of "
                   f"'{name}': unsigned ingredients ({names}) — sign them, then the agent",
                   severity="warning",
                   payload={"kind": AGENT_KIND, "artifact_id": pid, "profile_tag": tag,
                            "blocking": r["blocking"], "collection_id": cid}, db_path=db_path)
        elif rec["signed"]:
            _event(user_uuid, EVENT_RENEWED,
                   f"Signature of agent @{tag} renewed by the {trigger} sync of '{name}' — "
                   f"recorded as a renewal, not a sign-off",
                   severity="info",
                   payload={"kind": AGENT_KIND, "artifact_id": pid, "profile_tag": tag,
                            "collection_id": cid}, db_path=db_path)
            _badge_refresh(user_uuid, AGENT_KIND, "renewed")
        else:
            rec["reason"] = "unsigned"
        out["agents"].append(rec)

    # 3. carry the renewal: the packs that ship the repository or a renewed agent — their
    #    seal (a renewal only), then their subscribers (the publisher's own fan-out, which
    #    reinstates the renewed attestations onto each subscriber's copy by content).
    renewed_agents = [a["profile_id"] for a in out["agents"] if a.get("signed")]
    for pack in packs_carrying(cid, renewed_agents, user_uuid, db_path=db_path):
        iid, pname = pack["installation_id"], pack.get("name") or pack["installation_id"]
        rec = {"installation_id": iid, "name": pname, "listing_id": pack.get("listing_id"),
               "seal": None, "subscribers": None}
        try:
            seal = renew_pack_seal(iid, user_uuid, provenance=provenance, db_path=db_path)
        except Exception as e:  # noqa: BLE001
            seal = {"signed": False, "reason": "error", "detail": str(e)}
        rec["seal"] = seal
        if seal.get("signed"):
            _event(user_uuid, EVENT_RENEWED,
                   f"Seal of agent pack '{pname}' renewed by the {trigger} sync of '{name}' — "
                   f"recorded as a renewal, not a sign-off",
                   severity="info",
                   payload={"kind": PACK_KIND, "artifact_id": iid, "collection_id": cid},
                   db_path=db_path)
            _badge_refresh(user_uuid, PACK_KIND, "renewed")
        elif seal.get("reason") == "unsigned_members":
            names = ", ".join(b.get("label") or f"{b.get('kind')} {b.get('artifact_id')}"
                              for b in (seal.get("blocking") or [])[:5])
            _event(user_uuid, EVENT_RENEWAL_REFUSED,
                   f"Seal of agent pack '{pname}' NOT renewed after the {trigger} sync of "
                   f"'{name}': unsigned members ({names}) — sign them, then the pack",
                   severity="warning",
                   payload={"kind": PACK_KIND, "artifact_id": iid, "collection_id": cid,
                            "blocking": seal.get("blocking")}, db_path=db_path)
        if pack.get("listing_id"):
            carried = await fan_out_subscribers(pack["listing_id"])
            rec["subscribers"] = carried
            if carried.get("failed", 0) > 0 or carried.get("error"):
                _event(user_uuid, EVENT_RENEWAL_REFUSED,
                       f"Renewal of '{name}' NOT carried to every subscriber of agent pack "
                       f"'{pname}' ({carried.get('synced', 0)} synced, "
                       f"{max(carried.get('failed', 0), 0)} failed"
                       f"{': ' + carried['error'] if carried.get('error') else ''}) — "
                       f"run Sync subscribers on the pack",
                       severity="warning",
                       payload={"kind": PACK_KIND, "artifact_id": iid, "collection_id": cid,
                                "listing_id": pack["listing_id"], **carried}, db_path=db_path)
            elif carried.get("synced", 0) > 0:
                _event(user_uuid, EVENT_RENEWAL_CARRIED,
                       f"Renewal of '{name}' carried to {carried['synced']} subscriber(s) of "
                       f"agent pack '{pname}' — their copies re-synced, the renewed "
                       f"signatures reinstated by content",
                       severity="info",
                       payload={"kind": PACK_KIND, "artifact_id": iid, "collection_id": cid,
                                "listing_id": pack["listing_id"], **carried}, db_path=db_path)
        out["packs"].append(rec)
    return out
