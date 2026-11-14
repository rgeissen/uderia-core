#!/usr/bin/env python3
"""
Verify an Uderia evidence bundle — offline, without Uderia installed (PROP-009 §4.4).

    pip install cryptography
    python verify_evidence.py bundle.json --fingerprint <sha256 of the deployment's public key>

What it checks, and nothing else:

  attestations  For each signed attestation: the Ed25519 signature over its payload
                (``<kind>|<version_hash>``) with the public key the bundle carries; that the
                key's fingerprint is the one the record names; and, when the bundle carries
                the signed content, that sha256(content) equals the signed version hash. An
                attestation whose content was not included is checked for its signature only,
                and says so.
  audit pack    When the bundle embeds a signed audit pack (``audit_pack`` +
                ``audit_pack_attestation``): that sha256 of its canonical JSON equals the signed
                ``content_sha256`` and the Ed25519 signature over that hash verifies.
  ledger        When the bundle carries the evidence ledger's proof: the checkpoint's Ed25519
                signature; every carried entry's hash, and — for a complete segment — that the
                entries link without a gap up to the signed tip; that each attestation's record
                and each answer's chain tip and bill of materials re-derive the digest of the
                entry they were entered with. This proves the records were in the ledger when
                the bundle was exported and have not been edited since.
  turns         For each answer: the provenance chain — every step's hash, its link to the
                previous step, and its Ed25519 signature; the chain's root, tip and step count;
                and, when the turn carries a bill of materials, that the sealed ``trust.bom``
                step is exactly the digest of that bill of materials; when it carries the
                question and answer, that they hash to the sealed ``query_intake`` and
                ``turn_complete`` steps (their first 4,096 characters, which is what is sealed).

The fingerprint: a bundle carries the public keys it was signed with, so on its own it only
proves the record is internally consistent. Pass ``--fingerprint`` with the deployment's key
fingerprint, obtained SEPARATELY (from the deployment's operator, not from the bundle), to also
prove WHO signed it. Without it the verdict says the signer is unconfirmed.

Exit status: 0 when everything checked verifies, 1 when anything fails, 2 for an unreadable
bundle. What a signature cannot prove: that an answer is right — only that the record of how
it was produced is the one the deployment wrote and that it is unchanged.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys

FORMAT = "uderia.evidence.bundle"
GENESIS_HASH = "0" * 64


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _load_key(pem: str):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_public_key(pem.encode("utf-8") if isinstance(pem, str) else pem)


def _fingerprint(key) -> str:
    from cryptography.hazmat.primitives import serialization
    raw = key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def _verify(key, sig_b64: str, message: str) -> bool:
    try:
        key.verify(base64.b64decode(sig_b64), message.encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001 — any failure is "does not verify"
        return False


def bom_canonical(bom) -> str:
    """Byte-identical to ``trust.evidence.bom_canonical``."""
    def _clean(v):
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items() if not str(k).startswith("_")}
        if isinstance(v, list):
            return [_clean(x) for x in v]
        return v
    return json.dumps(_clean(bom), sort_keys=True, separators=(",", ":"), default=str)


def bom_step_content(bom) -> str:
    parts = []
    for k in ("agent", "deployment"):
        if isinstance(bom.get(k), dict):
            parts.append(bom[k])
    parts.extend(p for p in (bom.get("parts") or []) if isinstance(p, dict))
    return f"bom:v1:{_sha256(bom_canonical(bom))}:{len(parts)}"


def check_attestation(a: dict, trusted_fp: str | None) -> dict:
    out = {"kind": a.get("kind"), "artifact_id": a.get("artifact_id"), "label": a.get("label"),
           "ok": False, "notes": []}
    if not a.get("signed") or not a.get("signature"):
        out["notes"].append("recorded without a signature — nothing to verify")
        out["ok"] = None
        return out
    try:
        key = _load_key(a.get("signer_public_key") or "")
    except Exception:  # noqa: BLE001
        out["notes"].append("the bundle carries no readable public key for this signature")
        return out
    fp = _fingerprint(key)
    if a.get("signer_fingerprint") and fp != a["signer_fingerprint"]:
        out["notes"].append("the carried key is not the key the record names")
        return out
    payloads = [f"{a.get('kind')}|{a.get('version_hash')}"]
    if a.get("legacy_payload"):
        payloads.append(a["legacy_payload"])
    if not any(_verify(key, a["signature"], p) for p in payloads):
        out["notes"].append("the signature does not verify")
        return out
    c = a.get("content") or {}
    if c.get("included"):
        if _sha256(c.get("data") or "") != a.get("version_hash"):
            out["notes"].append("the included content does NOT hash to the signed version")
            return out
        out["notes"].append("signature verifies and the content hashes to the signed version")
    else:
        out["notes"].append("signature verifies; content not included"
                            + (f" ({c.get('reason')})" if c.get("reason") else ""))
    out["signer_confirmed"] = (trusted_fp == fp) if trusted_fp else None
    if trusted_fp and trusted_fp != fp:
        out["notes"].append("signed by a key OTHER than the fingerprint you supplied")
        out["ok"] = False
        return out
    out["ok"] = True
    return out


# The chain seals the question (query_intake) and the answer (turn_complete) as the SHA-256
# of their first CONTENT_MAX_LEN characters. An exported text is checked against them, never
# only shown: an edited answer must not read as verified.
CONTENT_MAX_LEN = 4096


def check_receipt_text(receipt, chain: list, notes: list) -> list:
    if not isinstance(receipt, dict):
        return []
    errors, matched = [], []
    for field, step_type, label in (("user_query", "query_intake", "question"),
                                    ("answer", "turn_complete", "answer")):
        text = receipt.get(field)
        step = next((s for s in chain if s.get("step_type") == step_type), None)
        if not isinstance(text, str) or step is None:
            continue
        if field == "answer" and receipt.get("answer_not_sealed"):
            notes.append(f"the answer text is NOT checked: {receipt['answer_not_sealed']}")
            continue
        if _sha256(text[:CONTENT_MAX_LEN]) != step.get("content_hash"):
            errors.append(f"the {label} text does not match what was sealed")
            continue
        matched.append(label)
        if len(text) > CONTENT_MAX_LEN:
            notes.append(f"only the first {CONTENT_MAX_LEN} characters of the {label} are sealed and checked")
    if matched:
        notes.append(f"the {' and '.join(matched)} text {'match' if len(matched) > 1 else 'matches'} what was sealed")
    return errors


def check_turn(t: dict, keys: dict, trusted_fp: str | None) -> dict:
    out = {"session_id": t.get("session_id"), "turn": t.get("turn"), "ok": False, "notes": []}
    chain = t.get("provenance_chain") or []
    meta = t.get("provenance_meta") or {}
    if not chain:
        out["ok"] = None
        out["notes"].append("this answer carries no sealed chain")
        return out
    fp = meta.get("key_fingerprint")
    pem = keys.get(fp)
    if not pem:
        out["notes"].append("the bundle carries no public key for this chain")
        return out
    key = _load_key(pem)
    if _fingerprint(key) != fp:
        out["notes"].append("the carried key is not the key the chain names")
        return out
    prev = meta.get("previous_turn_tip_hash") or GENESIS_HASH
    errors = []
    for i, s in enumerate(chain):
        expect_prev = prev if i == 0 else chain[i - 1].get("chain_hash")
        if s.get("previous_hash") != expect_prev:
            errors.append(f"step {i}: broken link")
        h = _sha256(f"{s.get('step_index')}:{s.get('step_type')}:{s.get('content_hash')}:{s.get('previous_hash')}")
        if s.get("chain_hash") != h:
            errors.append(f"step {i}: hash mismatch")
        if not s.get("signature") or not _verify(key, s["signature"], s.get("chain_hash") or ""):
            errors.append(f"step {i}: signature does not verify")
    if meta.get("chain_tip_hash") != chain[-1].get("chain_hash"):
        errors.append("tip does not match the last step")
    if meta.get("chain_root_hash") != chain[0].get("chain_hash"):
        errors.append("root does not match the first step")
    if meta.get("step_count") != len(chain):
        errors.append("step count does not match")
    bom = t.get("bom")
    bom_steps = [s for s in chain if s.get("step_type") == "trust.bom"]
    if bom_steps:
        if not isinstance(bom, dict):
            errors.append("a bill of materials was sealed but the bundle carries none")
        elif _sha256(bom_step_content(bom)) != bom_steps[0].get("content_hash"):
            errors.append("the bill of materials does not match what was sealed")
        else:
            out["notes"].append("the bill of materials matches what was sealed")
    errors.extend(check_receipt_text(t.get("receipt"), chain, out["notes"]))
    if errors:
        out["notes"].extend(errors)
        return out
    out["notes"].insert(0, f"chain of {len(chain)} steps verifies")
    if trusted_fp and trusted_fp != fp:
        out["notes"].append("signed by a key OTHER than the fingerprint you supplied")
        return out
    out["signer_confirmed"] = (trusted_fp == fp) if trusted_fp else None
    out["ok"] = True
    return out


def check_audit_pack(pack, att: dict, keys: dict, trusted_fp: str | None) -> dict:
    out = {"ok": False, "notes": []}
    if not isinstance(att, dict) or not att.get("signed") or not att.get("signature"):
        out["ok"] = None
        out["notes"].append("the audit pack is not signed")
        return out
    canonical = json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if _sha256(canonical) != att.get("content_sha256"):
        out["notes"].append("the audit pack does NOT hash to the signed content hash")
        return out
    pem = keys.get(att.get("key_fingerprint"))
    if not pem:
        out["notes"].append("the bundle carries no public key for the audit pack")
        return out
    key = _load_key(pem)
    if _fingerprint(key) != att.get("key_fingerprint") or not _verify(key, att["signature"], att["content_sha256"]):
        out["notes"].append("the audit pack signature does not verify")
        return out
    if trusted_fp and trusted_fp != att.get("key_fingerprint"):
        out["notes"].append("signed by a key OTHER than the fingerprint you supplied")
        return out
    out["notes"].append("the audit pack hashes to its signed content hash and the signature verifies")
    out["ok"] = True
    return out


LEDGER_VERSION = "ledger:v1"


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _entry_hash(e: dict) -> str:
    return _sha256(f"{LEDGER_VERSION}:{int(e['seq'])}:{e['entry_type']}:{e['ref']}:{e['digest']}:{e['prev_hash']}")


def check_ledger(bundle: dict, keys: dict, trusted_fp: str | None) -> dict:
    out = {"ok": False, "notes": []}
    led = bundle.get("ledger") or {}
    cp = led.get("checkpoint") or {}
    if not cp.get("signed") or not cp.get("signature"):
        out["ok"] = None
        out["notes"].append("the ledger checkpoint is not signed")
        return out
    payload = f"{LEDGER_VERSION}:checkpoint:{cp.get('seq')}:{cp.get('tip_hash')}"
    pem = keys.get(cp.get("key_fingerprint"))
    if not pem:
        out["notes"].append("the bundle carries no public key for the ledger checkpoint")
        return out
    key = _load_key(pem)
    if _fingerprint(key) != cp.get("key_fingerprint") or not _verify(key, cp["signature"], payload):
        out["notes"].append("the ledger checkpoint signature does not verify")
        return out
    errors = []
    entries = led.get("entries") or []
    by_ref = {}
    for i, e in enumerate(entries):
        if _entry_hash(e) != e.get("entry_hash"):
            errors.append(f"ledger entry {e.get('seq')}: hash does not recompute")
        if led.get("complete_segment") and i:
            prev = entries[i - 1]
            if e.get("seq") != prev.get("seq") + 1 or e.get("prev_hash") != prev.get("entry_hash"):
                errors.append(f"ledger entry {e.get('seq')}: gap or broken link")
        by_ref[e.get("ref")] = e
    if led.get("complete_segment") and entries:
        if entries[-1].get("seq") != cp.get("seq") or entries[-1].get("entry_hash") != cp.get("tip_hash"):
            errors.append("the ledger segment does not end at the signed checkpoint")
    elif not led.get("complete_segment"):
        out["notes"].append("chain to the checkpoint not carried — each record's own entry checked")
    covered = 0
    for a in bundle.get("attestations") or []:
        rec, hid = a.get("ledger_record"), a.get("history_id")
        if not rec or not hid:
            continue
        e = by_ref.get(f"h:{hid}")
        if e is None:
            errors.append(f"{a.get('kind')} {a.get('label') or a.get('artifact_id')}: not in the ledger")
            continue
        same = (rec.get("kind") == a.get("kind") and str(rec.get("artifact_id")) == str(a.get("artifact_id"))
                and rec.get("version_hash") == a.get("version_hash") and (rec.get("signature") or None) == (a.get("signature") or None))
        if not same or _sha256(_canonical(rec)) != e.get("digest"):
            errors.append(f"{a.get('kind')} {a.get('label') or a.get('artifact_id')}: differs from what the ledger recorded")
            continue
        covered += 1
    for t in bundle.get("turns") or []:
        ref = t.get("ledger_ref")
        if not ref:
            continue
        e = by_ref.get(ref)
        if e is None:
            errors.append(f"answer {t.get('session_id')} #{t.get('turn')}: not in the ledger")
            continue
        tip = (t.get("provenance_meta") or {}).get("chain_tip_hash") or ""
        bd = _sha256(bom_canonical(t["bom"])) if isinstance(t.get("bom"), dict) else ""
        if _sha256(f"turn:v1:{tip}:{bd}") != e.get("digest"):
            errors.append(f"answer {t.get('session_id')} #{t.get('turn')}: differs from what the ledger recorded")
            continue
        covered += 1
    if errors:
        out["notes"].extend(errors)
        return out
    if trusted_fp and trusted_fp != cp.get("key_fingerprint"):
        out["notes"].append("signed by a key OTHER than the fingerprint you supplied")
        return out
    out["notes"].insert(0, f"{covered} record(s) match their ledger entries under the signed "
                           f"checkpoint at entry {cp.get('seq')}")
    out["ok"] = True
    return out


def verify_bundle(bundle: dict, trusted_fp: str | None = None) -> dict:
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ValueError("not an Uderia evidence bundle")
    keys = {k.get("fingerprint"): k.get("public_key_pem") for k in bundle.get("keys") or []
            if isinstance(k, dict)}
    atts = [check_attestation(a, trusted_fp) for a in bundle.get("attestations") or []]
    turns = [check_turn(t, keys, trusted_fp) for t in bundle.get("turns") or []]
    audit = (check_audit_pack(bundle.get("audit_pack"), bundle.get("audit_pack_attestation"), keys, trusted_fp)
             if "audit_pack" in bundle else None)
    ledger = check_ledger(bundle, keys, trusted_fp) if bundle.get("ledger") else None
    checked = [r for r in atts + turns + ([audit] if audit else []) + ([ledger] if ledger else [])
               if r["ok"] is not None]
    failed = [r for r in checked if r["ok"] is False]
    return {"attestations": atts, "turns": turns, "audit_pack": audit, "ledger": ledger,
            "checked": len(checked), "failed": len(failed),
            "signer_confirmed": bool(trusted_fp) and not failed,
            "ok": not failed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle")
    ap.add_argument("--fingerprint", help="the deployment's public-key fingerprint, obtained separately")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)
    try:
        with open(args.bundle, encoding="utf-8") as f:
            bundle = json.load(f)
        res = verify_bundle(bundle, args.fingerprint)
    except Exception as e:  # noqa: BLE001
        print(f"unreadable bundle: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        for r in res["attestations"]:
            mark = {True: "ok  ", False: "FAIL", None: "--  "}[r["ok"]]
            print(f"{mark} {r['kind']} {r.get('label') or r['artifact_id']}: {'; '.join(r['notes'])}")
        for r in res["turns"]:
            mark = {True: "ok  ", False: "FAIL", None: "--  "}[r["ok"]]
            print(f"{mark} answer {r['session_id']} #{r['turn']}: {'; '.join(r['notes'])}")
        if res.get("audit_pack"):
            r = res["audit_pack"]
            mark = {True: "ok  ", False: "FAIL", None: "--  "}[r["ok"]]
            print(f"{mark} audit pack: {'; '.join(r['notes'])}")
        if res.get("ledger"):
            r = res["ledger"]
            mark = {True: "ok  ", False: "FAIL", None: "--  "}[r["ok"]]
            print(f"{mark} ledger: {'; '.join(r['notes'])}")
        # "Not confirmed" must not read as a key problem when the key was fine and a record
        # failed for another reason; a record signed by another key says so on its own line.
        if res["signer_confirmed"]:
            who = "signer confirmed against the fingerprint you supplied"
        elif args.fingerprint:
            who = (f"signer not confirmed for the whole file: {res['failed']} record(s) did not verify "
                   "(see above)")
        else:
            who = "signer NOT confirmed — pass --fingerprint with the deployment's key fingerprint"
        print(f"\n{res['checked']} checked, {res['failed']} failed · {who}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
