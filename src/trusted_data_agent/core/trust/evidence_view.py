"""
Evidence read model (PROP-009 §4.3 / §5.1) — one answer's evidence: every signed part it
used, each read twice — **when answered** (from the sealed bill of materials) and **now**
(resolved live) — plus the turn's own chain verification.

Rules this module holds (and its gate asserts):

* **When answered comes only from the sealed record** — the turn's ``bom`` stamp, which the
  chain's ``trust.bom`` step binds. It is never re-resolved: re-resolving would show today's
  state as yesterday's fact (the retroactive-falsification defect the integrity stamp was
  already fixed for).
* **Now is always resolved live**, through the same rules the badge uses
  (``ingredient_state``, ``resolve_profile_trust``, ``content.live_hash``).
* **Absence is said.** A turn from before evidence recording reads *"parts not recorded"*,
  never an empty tree; a part that cannot be found now reads *not found now*, never
  *unsigned*; a turn whose session is gone reads *"session deleted — content not retained"*
  from the content-free index record.
* **Owner-scoped by construction:** the caller passes only its own session and its own uuid;
  the index is read with that uuid.

Pure-ish: the session is loaded by the caller (it is async); everything here is synchronous
and never raises for a malformed record.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from trusted_data_agent.core.trust import store as _store
from trusted_data_agent.core.trust import evidence_index as _index
from trusted_data_agent.core.trust.evidence import all_parts

logger = logging.getLogger("quart.app")

NOT_RECORDED = "parts not recorded — this answer predates evidence recording"
SESSION_GONE = "session deleted — content not retained"

#: Group order in the evidence tree (PROP-009 §5.1).
GROUPS = ("agent", "deployment", "ingredients", "data", "acts")
GROUP_LABEL = {
    "agent": "Agent",
    "deployment": "Deployment",
    "ingredients": "What the agent is made of",
    "data": "Data read",
    "acts": "Acts performed",
}
KIND_LABEL = {
    "agent": "agent", "agent_deployment": "deployment", "ontology": "ontology",
    "ontology_extension": "ontology extension", "skill": "skill",
    "knowledge": "knowledge repository", "planner": "planner repository",
    "data_product": "data product", "action_type": "action", "extension": "extension",
    "assurance_module": "assurance module", "flow": "flow", "dashboard": "dashboard",
    "canvas": "canvas",
}


def find_turn(session_data: Optional[Dict[str, Any]], turn_no: int) -> Optional[Dict[str, Any]]:
    if not isinstance(session_data, dict):
        return None
    wf = (session_data.get("last_turn_data") or {}).get("workflow_history") or []
    match = None
    for t in wf:
        if isinstance(t, dict) and str(t.get("turn")) == str(turn_no):
            match = t   # the last record for a turn number wins (a regenerate's sibling
            #            is a separate record; the adopted one is valid)
            if t.get("isValid", True):
                break
    return match


# ── per-part "when answered" ─────────────────────────────────────────────────

def _signer(history_id: Optional[int], db_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not history_id:
        return None
    h = _store.history_entry(history_id, db_path=db_path)
    if not h:
        return None
    fp = h.get("signer_fingerprint")
    return {"history_id": history_id, "event": h.get("event"),
            "signer_uuid": h.get("signer_uuid"), "signed_at": h.get("signed_at"),
            "key_fingerprint": fp,
            "key_status": _store.key_status(fp, db_path=db_path) if fp else None}


def _then(part: Dict[str, Any], db_path: Optional[str]) -> Dict[str, Any]:
    out = {"state": part.get("state"), "live_hash": part.get("live_hash"),
           "attested_hash": part.get("attested_hash"), "history_id": part.get("history_id")}
    if part.get("unresolved"):
        out["unresolved"] = True
    sg = _signer(part.get("history_id"), db_path)
    if sg:
        out["signer"] = sg
    return out


# ── per-part "now" ────────────────────────────────────────────────────────────

def _leaf_now(kind: str, artifact_id: str, owner_uuid: str,
              db_path: Optional[str]) -> Dict[str, Any]:
    """A part that is no longer (or never was) one of the agent's live ingredients:
    resolved directly through the canonical per-kind content hash."""
    from trusted_data_agent.core.trust import content as _content
    from trusted_data_agent.core.trust.attestation import resolve_state
    live = _content.live_hash(kind, artifact_id)
    attested = _store.attested_hash(kind, artifact_id, db_path=db_path, owner_uuid=owner_uuid)
    if live is None:
        return {"state": "not_found", "live_hash": None, "attested_hash": attested}
    if not attested:
        return {"state": "unsigned", "live_hash": live, "attested_hash": None}
    return {"state": resolve_state(attested, live).value, "live_hash": live,
            "attested_hash": attested}


def _current_agent(profile_id: Optional[str], owner_uuid: str,
                   db_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not profile_id:
        return None
    try:
        from trusted_data_agent.core.trust import composition as _comp
        prof = _comp._load_profile(str(profile_id), owner_uuid, None)
        if not prof:
            return None
        return _comp._resolve_profile_trust_detail(str(profile_id), owner_uuid,
                                                   profile=prof, db_path=db_path)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"evidence: current agent unresolvable ({profile_id}): {e}")
        return None


def _now(part: Dict[str, Any], cur: Optional[Dict[str, Any]], owner_uuid: str,
         db_path: Optional[str]) -> Dict[str, Any]:
    kind = part.get("kind")
    aid = str(part.get("artifact_id"))
    if kind == "agent":
        if cur is None:
            return {"state": "not_found", "live_hash": None, "attested_hash": None}
        t = cur["trust"]
        return {"state": getattr(getattr(t, "composition_state", None), "value", None),
                "live_hash": cur.get("agent_live"), "attested_hash": cur.get("agent_attested")}
    if kind == "agent_deployment":
        if cur is None:
            return {"state": "not_found", "live_hash": None, "attested_hash": None}
        dep = cur.get("deployment") or {}
        try:
            from trusted_data_agent.core.trust import deployment as _dep
            att = _store.attested_hash(_dep.DEPLOYMENT_KIND, aid, db_path=db_path,
                                       owner_uuid=owner_uuid)
        except Exception:  # noqa: BLE001
            att = None
        return {"state": dep.get("state"), "live_hash": dep.get("hash"), "attested_hash": att,
                "drift": dep.get("drift") or []}
    if cur is not None and kind not in ("data_product", "action_type"):
        from trusted_data_agent.core.trust.attestation import ingredient_state
        for r in cur.get("refs") or []:
            if r.kind == kind and str(r.artifact_id) == aid:
                out = {"state": ingredient_state(r).value, "live_hash": r.live_hash or None,
                       "attested_hash": r.attested_hash}
                if r.unresolved:
                    out["unresolved"] = True
                return out
        out = _leaf_now(kind, aid, owner_uuid, db_path)
        out["no_longer_used"] = True
        return out
    if kind in ("data_product", "action_type") and "::" not in aid:
        return {"state": "not_found", "live_hash": None, "attested_hash": None}
    return _leaf_now(kind, aid, owner_uuid, db_path)


def _status(then: Dict[str, Any], now: Dict[str, Any]) -> str:
    """How the part relates to what the answer used — one word, read beside the pair."""
    if now.get("state") == "not_found":
        return "not_found"
    if now.get("no_longer_used"):
        return "no_longer_used"
    a, b = then.get("live_hash"), now.get("live_hash")
    if a and b:
        return "unchanged" if a == b else "changed_since"
    return "unknown"


# ── the tree ──────────────────────────────────────────────────────────────────

def _entry(part: Dict[str, Any], role: str, cur, owner_uuid, db_path) -> Dict[str, Any]:
    then = _then(part, db_path)
    now = _now(part, cur, owner_uuid, db_path)
    e = {"role": role, "kind": part.get("kind"),
         "kind_label": KIND_LABEL.get(part.get("kind"), part.get("kind")),
         "artifact_id": str(part.get("artifact_id")), "label": part.get("label"),
         "then": then, "now": now, "status": _status(then, now)}
    if part.get("summary"):
        e["summary"] = part["summary"]
    if part.get("required") is not None:
        e["required"] = bool(part.get("required"))
    return e


def _data_entry(ref: Dict[str, Any], cur, owner_uuid, db_path) -> Dict[str, Any]:
    then = {"state": ref.get("state"), "version": ref.get("version"),
            "row_count": ref.get("row_count")}
    now = _now(ref, cur, owner_uuid, db_path)
    return {"role": "data", "kind": "data_product", "kind_label": "data product",
            "artifact_id": ref["artifact_id"], "label": ref.get("label"), "then": then,
            "now": now, "status": "not_found" if now.get("state") == "not_found" else "recorded"}


def _act_entry(ref: Dict[str, Any], cur, owner_uuid, db_path) -> Dict[str, Any]:
    then = {"state": "performed", "version": ref.get("version"),
            "rows_affected": ref.get("rows_affected"), "posture": ref.get("posture"),
            "live_hash": ref.get("manifest_hash")}
    now = _now(ref, cur, owner_uuid, db_path)
    return {"role": "act", "kind": "action_type", "kind_label": "action",
            "artifact_id": ref["artifact_id"], "label": ref.get("label"), "then": then,
            "now": now, "status": "not_found" if now.get("state") == "not_found" else "recorded"}


def _name(e: Dict[str, Any]) -> str:
    """How a part is named in a sentence — the deployment has no artefact name of its own."""
    return "the deployment surface" if e.get("role") == "deployment" else str(e.get("label"))


def _summary(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    signed_parts = [e for e in entries if e["role"] in ("agent", "deployment", "ingredient")
                    and not (e["role"] == "deployment" and e.get("required") is False)]
    not_signed = [e for e in signed_parts if e["then"].get("state") != "trusted"]
    changed = [e for e in entries if e["status"] == "changed_since"]
    gone = [e for e in entries if e["status"] == "not_found"]
    n = len(entries)
    words = [f"{n} part{'s' if n != 1 else ''}"]
    if not signed_parts:
        pass
    elif not not_signed:
        words.append("all signed when answered")
    else:
        words.append(f"{len(not_signed)} not signed-and-intact when answered")
    if changed:
        words.append(f"{len(changed)} changed since")
    if gone:
        words.append(f"{len(gone)} not found now")
    attention = ("warn" if (not_signed or changed or gone) else "pass")
    return {"parts": n, "not_signed_when_answered": [_name(e) for e in not_signed],
            "changed_since": [_name(e) for e in changed],
            "not_found_now": [_name(e) for e in gone],
            "attention": attention, "words": " · ".join(words)}


def _chain(turn: Dict[str, Any]) -> Dict[str, Any]:
    from trusted_data_agent.core import provenance as _prov
    data = {"provenance_chain": turn.get("provenance_chain"),
            "provenance_meta": turn.get("provenance_meta")}
    meta = turn.get("provenance_meta") or {}
    fp = meta.get("key_fingerprint")
    if not data["provenance_chain"]:
        return {"valid": None, "note": "this turn carries no provenance chain",
                "key_fingerprint": fp}
    try:
        # The key the chain names (PROP-009 §4.6): after a rotation or a custody switch an
        # older answer still verifies against the key that signed it, never the current one.
        named = _store.public_key_pem(fp) if fp else None
        c = _prov.verify_chain(data, named.encode("utf-8") if isinstance(named, str) else named)
        v = _prov.verify_content(data, turn)
    except Exception as e:  # noqa: BLE001
        return {"valid": None, "note": f"verification could not run: {e}", "key_fingerprint": fp}
    if c.get("valid") is False or v.get("valid") is False:
        valid = False
    elif c.get("valid") is True:
        valid = True
    else:
        valid = None
    return {"valid": valid, "chain_valid": c.get("valid"), "content_valid": v.get("valid"),
            "errors": (c.get("errors") or []) + (v.get("mismatches") or []),
            "steps": c.get("step_count"), "verified_steps": v.get("verified"),
            "chain_tip_hash": meta.get("chain_tip_hash"), "key_fingerprint": fp,
            "key_status": _store.key_status(fp) if fp else None}


def turn_view(owner_uuid: str, session_id: str, turn_no: int,
              session_data: Optional[Dict[str, Any]],
              db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The evidence for one answer, or None when this owner has no such turn at all."""
    turn = find_turn(session_data, turn_no)
    record = None
    session_gone = False
    if turn is None:
        record = _index.turn_record(owner_uuid, session_id, turn_no, db_path=db_path)
        if record is None:
            return None
        session_gone = True
        bom = record.get("bom")
    else:
        bom = turn.get("bom") if isinstance(turn.get("bom"), dict) else None

    out: Dict[str, Any] = {"session_id": session_id, "turn": int(turn_no), "recorded": bom is not None}
    if session_gone:
        out["session"] = {"available": False, "note": SESSION_GONE}
        out["chain"] = {"valid": None, "note": SESSION_GONE,
                        "chain_tip_hash": record.get("chain_tip_hash"),
                        "key_fingerprint": record.get("key_fingerprint")}
        out["answered_at"] = record.get("answered_at")
    else:
        out["session"] = {"available": True,
                          "archived": bool((session_data or {}).get("archived"))}
        out["chain"] = _chain(turn)
        out["answered_at"] = turn.get("timestamp")
    if bom is None:
        out["note"] = NOT_RECORDED
        out["summary"] = {"parts": 0, "attention": "na", "words": NOT_RECORDED}
        out["groups"] = []
        return out

    out["profile_id"] = bom.get("profile_id")
    out["profile_tag"] = bom.get("profile_tag")
    out["resolved"] = bom.get("resolved")
    out["badge_when_answered"] = bom.get("badge")
    cur = _current_agent(bom.get("profile_id"), owner_uuid, db_path)

    entries: List[Dict[str, Any]] = []
    for p in all_parts(bom):
        role = {"agent": "agent", "agent_deployment": "deployment"}.get(p.get("kind"),
                                                                        "ingredient")
        entries.append(_entry(p, role, cur, owner_uuid, db_path))
    src = turn if turn is not None else {}
    if turn is not None:
        for ref in _index.data_product_refs(src, bom):
            entries.append(_data_entry(ref, cur, owner_uuid, db_path))
        for ref in _index.action_refs(src):
            entries.append(_act_entry(ref, cur, owner_uuid, db_path))
    else:
        out["data_and_acts_note"] = ("data read and acts performed are in the turn record, "
                                     "which was not retained")

    role_group = {"agent": "agent", "deployment": "deployment", "ingredient": "ingredients",
                  "data": "data", "act": "acts"}
    groups = []
    for g in GROUPS:
        items = [e for e in entries if role_group[e["role"]] == g]
        if items:
            groups.append({"key": g, "label": GROUP_LABEL[g], "items": items})
    out["groups"] = groups
    out["summary"] = _summary(entries)
    return out

ESTATE_ORDER = {"changed": 0, "unreadable": 1, "retired_key": 2, "unsigned": 3, "trusted": 4}


def estate_row(rec: Dict[str, Any], state_now: Optional[str], label: Optional[str],
               usage_counts: Optional[Dict[str, Any]], key_status: Optional[str],
               signer_name: Optional[str] = None) -> Dict[str, Any]:
    """One current signature as the estate list shows it. ``state_now`` is the badge's own
    reading of the artefact NOW (trusted / changed / unsigned); a signature whose content
    cannot be read now reads ``changed`` there (fail closed) and is named here as such."""
    flags = []
    if state_now == "changed":
        flags.append("changed")
    if key_status == "retired":
        flags.append("retired_key")
    if not rec.get("signed"):
        flags.append("not_signed")
    worst = ("changed" if "changed" in flags else
             "retired_key" if "retired_key" in flags else
             "unsigned" if "not_signed" in flags else "trusted")
    return {"kind": rec.get("kind"), "kind_label": KIND_LABEL.get(rec.get("kind"), rec.get("kind")),
            "artifact_id": str(rec.get("artifact_id")), "label": label or str(rec.get("artifact_id")),
            "state_now": state_now, "signed": bool(rec.get("signed")),
            "signed_at": rec.get("signed_at"), "signer_uuid": rec.get("signer_uuid"),
            "signer_name": signer_name, "key_fingerprint": rec.get("signer_fingerprint"),
            "key_status": key_status, "history_id": rec.get("history_id"),
            "version_hash": rec.get("version_hash"),
            "answers": (usage_counts or {}).get("answers"),
            "since_current": (usage_counts or {}).get("since_current"),
            "flags": flags, "worst": worst}


def estate_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Worst-first, in words: '24 signatures · 3 changed since signed · 1 made with a retired key'."""
    n = len(rows)
    changed = sum(1 for r in rows if "changed" in r["flags"])
    retired = sum(1 for r in rows if "retired_key" in r["flags"])
    words = [f"{n} signature{'s' if n != 1 else ''}"]
    if changed:
        words.append(f"{changed} changed since signed")
    if retired:
        words.append(f"{retired} made with a retired key")
    if n and not changed and not retired:
        words.append("all intact")
    return {"signatures": n, "changed": changed, "retired_key": retired,
            "attention": "warn" if (changed or retired) else ("pass" if n else "na"),
            "words": " · ".join(words)}


def sort_estate(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (ESTATE_ORDER.get(r["worst"], 9),
                                       -(r.get("signed_at") or 0)))


def ledger_words(v: Dict[str, Any]) -> Dict[str, Any]:
    """The ledger's reading in words, worst first: a break or a missing checkpoint is a
    danger; an edited, removed or written-around record is named; otherwise 'intact'."""
    if v.get("intact") is None:
        return {"words": "ledger could not be read", "attention": "na"}
    n = v.get("entries", 0)
    bad = []
    if v.get("breaks_count"):
        bad.append(f"{v['breaks_count']} broken link{'s' if v['breaks_count'] != 1 else ''}")
    if v.get("edited_count"):
        bad.append(f"{v['edited_count']} record{'s' if v['edited_count'] != 1 else ''} edited after entry")
    if v.get("removed_count"):
        bad.append(f"{v['removed_count']} record{'s' if v['removed_count'] != 1 else ''} removed")
    if v.get("unledgered_count"):
        bad.append(f"{v['unledgered_count']} record{'s' if v['unledgered_count'] != 1 else ''} written around it")
    lost = [c for c in v.get("checkpoints") or [] if not c.get("ok")]
    if lost:
        bad.append(f"{len(lost)} earlier checkpoint{'s' if len(lost) != 1 else ''} no longer reached")
    if bad:
        return {"words": "ledger NOT intact — " + " · ".join(bad), "attention": "danger"}
    since = (f" · {v['backfilled']} entered when it began" if v.get("backfilled") else "")
    words = f"ledger intact · {n} entr{'ies' if n != 1 else 'y'}{since}"
    an = v.get("anchoring") or {}
    if an.get("enabled") and an.get("last_ok") is False:
        return {"words": f"{words} · the last anchor failed: {an.get('last_error') or 'unknown error'}",
                "attention": "warn"}
    if an.get("enabled") and an.get("anchors"):
        words += f" · {an['anchors']} checkpoint{'s' if an['anchors'] != 1 else ''} published outside this host"
    return {"words": words, "attention": "pass" if n else "na"}
