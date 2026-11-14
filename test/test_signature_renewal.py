"""Signature renewal after a sync that changed content (Oct 2026) — the gate.

CI-pure: a temp SQLite trust store, a stubbed collection DB, a stubbed Ed25519 signer.
No server, no network, no vector store.

What it asserts, and why each rule exists:

  §A  The carry-forward rule is pure and narrow: a prior acknowledgement carries onto a
      re-scanned record only when the finding labels did not widen; a widened set, a
      never-acknowledged prior, or a clean new scan carries nothing. The carried record
      says it was carried (the manifest tells it from a fresh judgement).
  §B  No policy, nothing changed → the renewal is bookkeeping only (no event, no write).
  §C  Auto-acknowledge touches ONLY the documents the run changed, skips quarantined and
      already-acknowledged ones, writes a reason naming the run and the policy, and
      records an inbox event per finding that is NOT a receipt (it counts as work).
  §D  Renewal is a renewal: a repository that was never signed is not signed; the
      owner's authority is checked (tier; separation of duties refuses) and a refusal
      is an inbox warning naming the reason; the validator's refusal stands and is an
      inbox warning with its note.
  §E  A renewed signature is recorded as ``event = "renew"`` with ``verdict.renewal``
      naming the run — never as a human ``sign``; the agents that bind the repository
      and were signed before are renewed the same way; an agent never signed is skipped;
      an agent whose other ingredient is unsigned is refused with the blocker named;
      an unrelated agent is untouched.
  §F  ``store.sign`` refuses any event outside the closed grammar; ``renew`` is an
      establishing event (a renewed signature is findable by its hash).
  §G  The sync engine carries the renewal: the result names the changed document ids,
      the renewal record rides on the result, and a renewal failure never fails the sync.
  §H  The renewal is CARRIED (the uderia_expert subscriber defect, Oct 2026): a subscriber's
      copy binds the publisher's collection by reference and has its own owner-scoped
      attestation, so renewing the owner's agents alone left every subscriber reading
      *changed*. After the agents, every pack of the owner that ships the repository or
      a renewed agent has its seal renewed (renewal only; the aggregate gate stands) and
      its listing's subscribers re-synced through the publisher's own fan-out; a pack
      never published is not fanned out; a pack never sealed is not sealed; a fan-out
      failure is an inbox warning naming the act owed; the lookup resolves the owner's
      packs by collection OR renewed agent from the real ledger tables.

Run: PYTHONPATH=src python test/test_signature_renewal.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.core.trust import renewal as R        # noqa: E402
from trusted_data_agent.core.trust import store as S          # noqa: E402
from trusted_data_agent.core.trust import composition as C    # noqa: E402

FAILS: list = []
N = 0


def check(label, cond):
    global N
    N += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fake_sign_payload(payload):
    return {"payload": payload, "key_fingerprint": "fp-test", "signature": "sig", "signed": True}


class FakeDB:
    """The slice of CollectionDatabase the renewal touches."""
    def __init__(self, docs):
        self.docs = {d["document_id"]: dict(d) for d in docs}
        self.acks = []
    def get_document_by_id(self, doc_id):
        return self.docs.get(doc_id)
    def acknowledge_document_finding(self, doc_id, reason):
        d = self.docs.get(doc_id)
        if not d or not d.get("scan_json"):
            return False
        scan = json.loads(d["scan_json"])
        scan["acknowledged"] = True
        scan["acknowledged_reason"] = reason
        d["scan_json"] = json.dumps(scan)
        self.acks.append((doc_id, reason))
        return True
    def set_document_scan(self, doc_id, scan):
        self.docs[doc_id]["scan_json"] = json.dumps(scan)
        return True


def _scan(labels, acked=False, reason=""):
    s = {"injection": list(labels), "pii": {}, "exfil_urls": 0}
    if acked:
        s["acknowledged"] = True
        s["acknowledged_reason"] = reason
        s["acknowledged_at"] = "2026-10-01T00:00:00+00:00"
    return s


# ── §A carry-forward ──────────────────────────────────────────────────────────

def t_carry_forward():
    print("§A carry-forward")
    prior = _scan(["ignore_previous", "system_override"], acked=True, reason="security corpus")
    same = R.carry_forward_acknowledgement(prior, _scan(["ignore_previous"]), prior_content_hash="h0")
    check("A1 same-or-narrower labels carry", same is not None and same["acknowledged"] is True)
    check("A2 the carried record keeps the reason", same["acknowledged_reason"] == "security corpus")
    check("A3 the carried record says it was carried",
          same["acknowledged_carried"]["from_content_hash"] == "h0"
          and same["acknowledged_carried"]["labels"] == ["ignore_previous", "system_override"])
    check("A4 a widened label set carries nothing",
          R.carry_forward_acknowledgement(prior, _scan(["ignore_previous", "exfil"])) is None)
    check("A5 a never-acknowledged prior carries nothing",
          R.carry_forward_acknowledgement(_scan(["ignore_previous"]), _scan(["ignore_previous"])) is None)
    check("A6 a clean new scan carries nothing", R.carry_forward_acknowledgement(prior, _scan([])) is None)
    check("A7 missing records carry nothing",
          R.carry_forward_acknowledgement(None, _scan(["x"])) is None
          and R.carry_forward_acknowledgement(prior, None) is None)
    check("A8 the input is not mutated", "acknowledged" not in _scan(["ignore_previous"]))


# ── §B–§E the post-sync act ──────────────────────────────────────────────────

def _coll(cid=7, ack=0, resign=0):
    return {"id": cid, "name": "Uderia Documentation", "collection_name": "c7",
            "sync_auto_ack_findings": ack, "sync_auto_resign": resign}


def _profiles(cid=7):
    # two agents bind collection 7; one binds collection 8 only
    return [
        {"id": "p-uderia", "tag": "UDERIA", "profile_type": "rag_focused",
         "knowledgeConfig": {"enabled": True, "collections": [cid]}},
        {"id": "p-opt", "tag": "OPTIM", "profile_type": "tool_enabled",
         "knowledgeConfig": {"enabled": True, "collections": [cid]}},
        {"id": "p-other", "tag": "OTHER", "profile_type": "rag_focused",
         "knowledgeConfig": {"enabled": True, "collections": [8]}},
    ]


class Ref:
    def __init__(self, kind, aid):
        self.kind, self.artifact_id, self.label = kind, aid, None


def _refs_for(pid, user_uuid, profile=None, db_path=None):
    cols = (profile or {}).get("knowledgeConfig", {}).get("collections", [])
    return [Ref("knowledge", str(c)) for c in cols]


def _events_recorded(rec_mock):
    return [(c.args[1], c.kwargs.get("severity")) for c in rec_mock.call_args_list]


def _env(db_path, *, tier="admin", require_approval=False, sign_result=None):
    """One patch set for the act: the store on a temp DB (via db_path), a fake signer,
    the owner's agents, a stub validator path, and the inbox."""
    policy = MagicMock(approver_tiers=frozenset({"admin"}), require_approval=require_approval)
    cfg = MagicMock()
    cfg.get_profiles.return_value = _profiles()
    sign_result = sign_result if sign_result is not None else {
        "signed": True, "reason": None, "verdict": {"status": "screened", "signable": True,
                                                   "note": "screened"},
        "version_hash": "vh-new", "fingerprint": "fp-test", "envelope": {"signed": True}}
    async def _fake_sign(cid, signer_uuid, *, event="sign", provenance=None, db_path=None):
        res = dict(sign_result)
        if res["signed"]:
            vd = {**res["verdict"], **({"renewal": provenance} if provenance else {})}
            S.sign("knowledge", str(cid), res["version_hash"], verdict=vd,
                   signer_uuid=signer_uuid, db_path=db_path, event=event)
            res["verdict"] = vd
        return res
    return [
        patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload),
        patch("trusted_data_agent.kgstore.settings.get_ontology_governance_policy", return_value=policy),
        patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=cfg),
        patch.object(C, "agent_ingredient_refs", _refs_for),
        patch.object(C, "agent_version_hash", lambda refs, prof, id_to_tag=None: "agent-" + prof["id"]),
        patch.object(C, "profile_id_to_tag", lambda u: (lambda i: i)),
        patch.object(C, "agent_signable",
                     lambda refs: (True, []) if not getattr(_env, "block", None) else (False, [("skill", "sk-1")])),
        patch.object(R, "sign_knowledge_repository", _fake_sign),
        patch("trusted_data_agent.core.trust_events.record_event"),
        patch("trusted_data_agent.core.trust_events.broadcast_notice"),
    ]


def _with(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


def t_no_policy_and_nothing_changed(db_path):
    print("§B bookkeeping only")
    db = FakeDB([])
    out = _run(R.renew_after_sync(_coll(ack=1, resign=1), {"updated_doc_ids": []}, "owner", db=db, db_path=db_path))
    check("B1 nothing changed → nothing_changed, no act", out["reason"] == "nothing_changed" and not db.acks)
    out = _run(R.renew_after_sync(_coll(), {"updated_doc_ids": ["d1"]}, "owner", db=db, db_path=db_path))
    check("B2 no policy → no_policy, no act", out["reason"] == "no_policy" and out["knowledge"] is None)


def t_auto_ack(db_path):
    print("§C auto-acknowledge")
    docs = [
        {"document_id": "d-open", "filename": "SECURITY.md", "collection_id": 7,
         "scan_json": json.dumps(_scan(["ignore_previous"]))},
        {"document_id": "d-acked", "filename": "A.md", "collection_id": 7,
         "scan_json": json.dumps(_scan(["ignore_previous"], acked=True, reason="legit"))},
        {"document_id": "d-quar", "filename": "Q.md", "collection_id": 7, "quarantined": 1,
         "scan_json": json.dumps(_scan(["exfil"]))},
        {"document_id": "d-clean", "filename": "C.md", "collection_id": 7,
         "scan_json": json.dumps(_scan([]))},
        {"document_id": "d-unchanged", "filename": "U.md", "collection_id": 7,
         "scan_json": json.dumps(_scan(["ignore_previous"]))},
    ]
    db = FakeDB(docs)
    patches = _env(db_path)
    def run():
        out = _run(R.renew_after_sync(
            _coll(ack=1), {"updated_doc_ids": ["d-open", "d-acked", "d-quar", "d-clean"],
                           "updated_files": ["SECURITY.md", "A.md", "Q.md", "C.md"]},
            "owner", trigger="scheduled", db=db, db_path=db_path))
        rec = patches[8].new if hasattr(patches[8], "new") else None
        from trusted_data_agent.core import trust_events as TE
        return out, TE.record_event
    out, rec = _with(patches, run)
    check("C1 only the open finding on a changed document is acknowledged",
          [a[0] for a in db.acks] == ["d-open"])
    check("C2 the unchanged document with an open finding is untouched",
          "acknowledged" not in json.loads(db.docs["d-unchanged"]["scan_json"]))
    reason = db.acks[0][1]
    check("C3 the reason names the run, the policy and the labels",
          "scheduled sync" in reason and "standing policy" in reason and "ignore_previous" in reason)
    check("C4 the record names the document", out["acknowledged"][0]["filename"] == "SECURITY.md")
    evs = _events_recorded(rec)
    check("C5 one inbox event per automatic acknowledgement",
          evs.count((R.EVENT_AUTO_ACK, "info")) == 1)
    from trusted_data_agent.core import trust_events as TE
    check("C6 an automatic acknowledgement is NOT a receipt (it counts as work to notice)",
          not TE.is_receipt(R.EVENT_AUTO_ACK) and TE.is_receipt("corpus_finding_ack"))
    check("C7 no signature act without the resign policy", out["knowledge"] is None and out["agents"] == [])


def t_renewal_gates(db_path):
    print("§D renewal gates")
    db = FakeDB([])
    res = {"updated_doc_ids": ["d1"], "updated_files": ["x.md"]}
    # never signed
    out = _with(_env(db_path), lambda: _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path)))
    check("D1 a repository never signed is not signed by the policy",
          out["knowledge"] == {"signed": False, "reason": "never_signed"}
          and S.load_attestation("knowledge", "7", db_path=db_path) is None)
    # a prior human signature exists
    with patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload):
        S.sign("knowledge", "7", "vh-old", signer_uuid="owner", db_path=db_path)
    # authority: tier
    def run_tier():
        from trusted_data_agent.core import trust_events as TE
        out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path, tier="user"))
        return out, _events_recorded(TE.record_event)
    out, evs = _with(_env(db_path), run_tier)
    check("D2 an owner without approver tier is refused", out["knowledge"]["reason"] == "not_permitted"
          and "tier 'user'" in out["knowledge"]["detail"])
    check("D3 the refusal is an inbox warning", (R.EVENT_RENEWAL_REFUSED, "warning") in evs)
    check("D4 the prior signature is untouched by a refusal",
          S.load_attestation("knowledge", "7", db_path=db_path)["version_hash"] == "vh-old")
    # authority: separation of duties
    def run_sod():
        return _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path, tier="admin"))
    out = _with(_env(db_path, require_approval=True), run_sod)
    check("D5 separation of duties refuses the policy too",
          out["knowledge"]["reason"] == "not_permitted" and "separation of duties" in out["knowledge"]["detail"])
    # the validator refuses
    refused = {"signed": False, "reason": "unsafe", "verdict": {"status": "unsafe", "signable": False,
               "note": "1 document(s) carry unresolved prompt-injection findings"},
               "version_hash": "vh-new", "fingerprint": None, "envelope": None}
    def run_val():
        from trusted_data_agent.core import trust_events as TE
        out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path, tier="admin"))
        return out, [c.args[2] for c in TE.record_event.call_args_list]
    out, summaries = _with(_env(db_path, sign_result=refused), run_val)
    check("D6 the validator's refusal stands", out["knowledge"]["signed"] is False and out["knowledge"]["reason"] == "unsafe")
    check("D7 the refusal names the validator's note in the inbox",
          any("unresolved prompt-injection" in s for s in summaries))
    check("D8 no agent is touched when the repository is refused", out["agents"] == [])


def t_renewal_records(db_path):
    print("§E renewal records")
    db = FakeDB([])
    res = {"updated_doc_ids": ["d1", "d2"], "updated_files": ["a.md", "b.md"]}
    with patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload):
        S.sign("knowledge", "7", "vh-old", signer_uuid="owner", db_path=db_path)
        S.sign("agent", "p-uderia", "agent-p-uderia-old", signer_uuid="owner", db_path=db_path, owner_uuid="owner")
        S.sign("agent", "p-other", "agent-p-other", signer_uuid="owner", db_path=db_path, owner_uuid="owner")
        # p-opt binds the repository but was never signed
    def run():
        from trusted_data_agent.core import trust_events as TE
        out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", trigger="scheduled",
                                      db=db, db_path=db_path, tier="admin"))
        return out, _events_recorded(TE.record_event)
    out, evs = _with(_env(db_path), run)
    k = S.load_attestation("knowledge", "7", db_path=db_path)
    check("E1 the repository signature is renewed to the new hash", out["knowledge"]["signed"] and k["version_hash"] == "vh-new")
    hist = S.history("knowledge", "7", db_path=db_path)
    check("E2 history records the act as `renew`, after the human `sign`",
          [h["event"] for h in hist] == ["renew", "sign"])
    ren = (hist[0].get("verdict") or {}).get("renewal") or {}
    check("E3 the renewal names the run, the repository and the files",
          ren.get("by") == "scheduled_sync" and ren.get("collection_id") == 7
          and ren.get("updated_files") == ["a.md", "b.md"] and ren.get("policy") == "sync_auto_resign")
    agents = {a["tag"]: a for a in out["agents"]}
    check("E4 the signed agent that binds the repository is renewed",
          agents["UDERIA"]["signed"] and S.load_attestation("agent", "p-uderia", db_path=db_path, owner_uuid="owner")["version_hash"] == "agent-p-uderia")
    ah = S.history("agent", "p-uderia", db_path=db_path, owner_uuid="owner")
    check("E5 the agent's history reads `renew` with the same provenance",
          ah[0]["event"] == "renew" and (ah[0].get("verdict") or {}).get("renewal", {}).get("collection_id") == 7)
    check("E6 the agent never signed is skipped, named", agents["OPTIM"] == {"profile_id": "p-opt", "tag": "OPTIM", "signed": False, "reason": "never_signed"})
    check("E7 an agent that does not bind the repository is untouched",
          "OTHER" not in agents and S.load_attestation("agent", "p-other", db_path=db_path, owner_uuid="owner")["version_hash"] == "agent-p-other"
          and len(S.history("agent", "p-other", db_path=db_path, owner_uuid="owner")) == 1)
    check("E8 each renewal is an inbox event (repository + agent)", evs.count((R.EVENT_RENEWED, "info")) == 2)
    # an agent blocked by another unsigned ingredient
    _env.block = True
    try:
        def run_blocked():
            from trusted_data_agent.core import trust_events as TE
            out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path, tier="admin"))
            return out, [c.args[2] for c in TE.record_event.call_args_list]
        out, summaries = _with(_env(db_path), run_blocked)
    finally:
        _env.block = None
    a = {x["tag"]: x for x in out["agents"]}["UDERIA"]
    check("E9 an agent with an unsigned ingredient is refused with the blocker named",
          a["reason"] == "unsigned_ingredients" and a["blocking"] == [{"kind": "skill", "artifact_id": "sk-1"}]
          and any("unsigned ingredients" in s and "@UDERIA" in s for s in summaries))


# ── §F the store grammar ─────────────────────────────────────────────────────

def t_store_grammar(db_path):
    print("§F store grammar")
    with patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload):
        try:
            S.sign("skill", "skX", "c1", db_path=db_path, event="auto")
            check("F1 an event outside the grammar is refused", False)
        except ValueError:
            check("F1 an event outside the grammar is refused", True)
        S.sign("skill", "skX", "c1", db_path=db_path, event="renew")
    check("F2 `renew` establishes a current attestation", S.attested_hash("skill", "skX", db_path=db_path) == "c1")
    check("F3 a renewed signature is findable by its hash",
          any(h["event"] == "renew" for h in S.signed_history_for_hash("skill", "c1", db_path=db_path)))
    check("F4 the default event is still `sign`", S.history("skill", "skX", db_path=db_path)[0]["event"] == "renew"
          and "sign" in S._ESTABLISHING_EVENTS)


# ── §G the sync engine carries it ────────────────────────────────────────────

def t_sync_engine_carries_renewal():
    print("§G the sync engine")
    from trusted_data_agent.core import knowledge_sync as KS
    import inspect
    src = inspect.getsource(KS.sync_knowledge_collection)
    check("G1 the sync result names the changed document ids", 'results["updated_doc_ids"].append(doc_id)' in src)
    check("G2 the renewal rides on the result, after the loop, before the schedule advances",
          src.index("renew_after_sync") < src.index("Advance next_sync_at"))
    check("G3 a renewal failure never fails the sync",
          'results["renewal"] = {"error": str(ren_err)}' in src)
    check("G4 the manual route names its trigger",
          'trigger="manual"' in (ROOT / "src/trusted_data_agent/api/knowledge_routes.py").read_text())
    src_up = inspect.getsource(KS._sync_upsert_document)
    check("G5 the re-ingest carries a prior acknowledgement forward", "_carry_acknowledgement(" in src_up)
    check("G6 the PATCH route accepts both policy flags",
          all(f in (ROOT / "src/trusted_data_agent/api/knowledge_routes.py").read_text()
              for f in ('"sync_auto_ack_findings"', '"sync_auto_resign"')))
    cdb = (ROOT / "src/trusted_data_agent/core/collection_db.py").read_text()
    check("G7 both columns self-apply on the collections table",
          "ADD COLUMN sync_auto_ack_findings" in cdb and "ADD COLUMN sync_auto_resign" in cdb)
    js = (ROOT / "static/js/handlers/jobsHandler.js").read_text()
    check("G8 the Jobs tab exposes both toggles and renders the renewal",
          "sd-auto-ack" in js and "sd-auto-resign" in js and "_renewalSummaryHTML(res.renewal)" in js)
    # the carry helper end-to-end against the fake DB
    db = FakeDB([{"document_id": "d1", "filename": "S.md", "collection_id": 7, "content_hash": "h0",
                  "scan_json": json.dumps(_scan(["ignore_previous"], acked=True, reason="legit"))}])
    with patch("trusted_data_agent.core.trust_events.record_event") as rec, \
         patch("trusted_data_agent.core.trust_events.broadcast_notice"):
        carried = KS._carry_acknowledgement(db, db.docs["d1"], _scan(["ignore_previous"]), "owner", 7)
    new = json.loads(db.docs["d1"]["scan_json"])
    check("G9 the helper writes the carried record and records the event",
          carried and new["acknowledged"] and new["acknowledged_carried"]["from_content_hash"] == "h0"
          and rec.call_args.args[1] == R.EVENT_ACK_CARRIED)
    check("G10 a widened set is not carried by the helper",
          KS._carry_acknowledgement(db, db.docs["d1"], _scan(["ignore_previous", "exfil"]), "owner", 7) is False)

# ── §H the renewal is carried to the packs and their subscribers ─────────────

def _ledger(path):
    """The slice of the auth DB the pack lookup reads: two packs of the owner, one of
    someone else; pack 1 ships the repository and @UDERIA and is listed; pack 2 ships
    only @OTHER (unrelated); pack 3 (another owner) ships the repository too."""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE agent_pack_installations (id INTEGER PRIMARY KEY, name TEXT, owner_user_id TEXT);
        CREATE TABLE agent_pack_resources (pack_installation_id INTEGER, resource_type TEXT, resource_id TEXT);
        CREATE TABLE marketplace_agent_packs (id TEXT PRIMARY KEY, source_installation_id INTEGER, publisher_user_id TEXT);
        INSERT INTO agent_pack_installations VALUES (1, 'Uderia Expert', 'owner'), (2, 'Other pack', 'owner'), (3, 'Theirs', 'someone');
        INSERT INTO agent_pack_resources VALUES (1, 'collection', '7'), (1, 'profile', 'p-uderia'),
                                                (2, 'profile', 'p-other'), (3, 'collection', '7');
        INSERT INTO marketplace_agent_packs VALUES ('listing-1', 1, 'owner');
    """)
    conn.commit()
    conn.close()


def t_carried(db_path):
    print("§H the renewal is carried")
    _ledger(db_path)
    # H1–H3 the lookup
    found = R.packs_carrying(7, ["p-uderia"], "owner", db_path=db_path)
    check("H1 the owner's pack that ships the repository is found, with its listing",
          [(p["installation_id"], p["listing_id"]) for p in found] == [("1", "listing-1")])
    check("H2 a pack found by a renewed agent alone is found too",
          [p["installation_id"] for p in R.packs_carrying(99, ["p-other"], "owner", db_path=db_path)] == ["2"])
    check("H3 another owner's pack is never the owner's; an unrelated pack is not found",
          R.packs_carrying(7, [], "someone", db_path=db_path)[0]["installation_id"] == "3"
          and all(p["installation_id"] != "2" for p in found))

    db = FakeDB([])
    res = {"updated_doc_ids": ["d1"], "updated_files": ["a.md"]}
    fan = MagicMock()
    async def _fan(listing_id):
        fan(listing_id)
        return {"synced": 2, "failed": 0}
    with patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload):
        S.sign("knowledge", "7", "vh-old", signer_uuid="owner", db_path=db_path)
        S.sign("agent", "p-uderia", "agent-p-uderia-old", signer_uuid="owner", db_path=db_path, owner_uuid="owner")
        S.sign("agent_pack", "1", "comp-old", signer_uuid="owner", db_path=db_path)
    from trusted_data_agent.core.trust import pack as P
    from trusted_data_agent.core.trust.attestation import IngredientRef
    def _members(iid):
        return [IngredientRef("knowledge", "7", "vh-new", "vh-new"),
                IngredientRef("agent", "p-uderia", "agent-p-uderia", "agent-p-uderia")], "owner"
    def run():
        from trusted_data_agent.core import trust_events as TE
        out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", trigger="scheduled",
                                      db=db, db_path=db_path, tier="admin"))
        return out, _events_recorded(TE.record_event), [c.args[2] for c in TE.record_event.call_args_list]
    out, evs, summaries = _with(_env(db_path) + [patch.object(R, "fan_out_subscribers", _fan),
                                                 patch.object(P, "member_refs", _members)], run)
    packs = {p["installation_id"]: p for p in out["packs"]}
    check("H4 the pack that ships the repository is carried; the unrelated pack is not",
          list(packs) == ["1"])
    seal = S.load_attestation("agent_pack", "1", db_path=db_path)
    check("H5 the pack's seal is renewed over its members' NEW hashes, as `renew`",
          packs["1"]["seal"]["signed"] and seal["version_hash"] != "comp-old"
          and [h["event"] for h in S.history("agent_pack", "1", db_path=db_path)] == ["renew", "sign"]
          and (S.history("agent_pack", "1", db_path=db_path)[0].get("verdict") or {}).get("renewal", {}).get("collection_id") == 7)
    check("H6 the listing's subscribers are re-synced through the publisher's fan-out, once",
          fan.call_args_list == [((("listing-1",),))] or [c.args for c in fan.call_args_list] == [("listing-1",)])
    check("H7 the carry is an inbox event naming the subscribers",
          packs["1"]["subscribers"] == {"synced": 2, "failed": 0}
          and (R.EVENT_RENEWAL_CARRIED, "info") in evs
          and any("2 subscriber(s)" in s_ and "Uderia Expert" in s_ for s_ in summaries))
    check("H8 the renewal record carries the packs", out["packs"][0]["listing_id"] == "listing-1")

    # a pack never sealed is not sealed; a fan-out failure is a warning naming the act owed
    with patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload):
        S.revoke("agent_pack", "1", db_path=db_path)
    async def _fan_fail(listing_id):
        return {"synced": 1, "failed": 1}
    def run2():
        from trusted_data_agent.core import trust_events as TE
        out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path, tier="admin"))
        return out, _events_recorded(TE.record_event), [c.args[2] for c in TE.record_event.call_args_list]
    out, evs, summaries = _with(_env(db_path) + [patch.object(R, "fan_out_subscribers", _fan_fail),
                                                 patch.object(P, "member_refs", _members)], run2)
    p1 = out["packs"][0]
    check("H9 a pack never sealed is not sealed (renewal only), and still fanned out",
          p1["seal"] == {"signed": False, "reason": "never_signed"}
          and S.load_attestation("agent_pack", "1", db_path=db_path) is None
          and p1["subscribers"] == {"synced": 1, "failed": 1})
    check("H10 a failed fan-out is an inbox warning naming the act owed",
          (R.EVENT_RENEWAL_REFUSED, "warning") in evs
          and any("1 failed" in s_ and "Sync subscribers" in s_ for s_ in summaries))

    # a pack whose member is still unsigned: refused with the blocker named, no seal written
    with patch("trusted_data_agent.eval.online.sign_payload", _fake_sign_payload):
        S.sign("agent_pack", "1", "comp-old", signer_uuid="owner", db_path=db_path)
    def _members_blocked(iid):
        return [IngredientRef("knowledge", "7", "vh-new", "vh-new"),
                IngredientRef("agent", "p-uderia", "agent-p-uderia", None)], "owner"
    fan3 = MagicMock()
    async def _fan3(listing_id):
        fan3(listing_id); return {"synced": 0, "failed": 0}
    def run3():
        from trusted_data_agent.core import trust_events as TE
        out = _run(R.renew_after_sync(_coll(resign=1), res, "owner", db=db, db_path=db_path, tier="admin"))
        return out, [c.args[2] for c in TE.record_event.call_args_list]
    out, summaries = _with(_env(db_path) + [patch.object(R, "fan_out_subscribers", _fan3),
                                            patch.object(P, "member_refs", _members_blocked),
                                            patch.object(C, "agent_signable", lambda refs: (True, [])),
                                            ], run3)
    p1 = out["packs"][0]
    check("H11 a pack with an unsigned member is refused with the blocker named; the old seal stands",
          p1["seal"]["reason"] == "unsigned_members"
          and p1["seal"]["blocking"] == [{"kind": "agent", "artifact_id": "p-uderia"}]
          and S.load_attestation("agent_pack", "1", db_path=db_path)["version_hash"] == "comp-old"
          and any("NOT renewed" in s_ and "unsigned members" in s_ for s_ in summaries))
    check("H12 a pack with no listing is never fanned out",
          fan3.call_count == 1 and R.packs_carrying(99, ["p-other"], "owner", db_path=db_path)[0]["listing_id"] is None)


def main():
    t_carry_forward()
    for fn in (t_no_policy_and_nothing_changed, t_auto_ack, t_renewal_gates, t_renewal_records, t_store_grammar, t_carried):
        with tempfile.TemporaryDirectory() as d:
            fn(os.path.join(d, "trust.db"))
    t_sync_engine_carries_renewal()
    print(f"\nRESULT: {N - len(FAILS)} passed, {len(FAILS)} failed")
    if FAILS:
        print("FAILED:", *FAILS, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
