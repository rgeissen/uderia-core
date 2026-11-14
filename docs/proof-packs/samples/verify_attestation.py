#!/usr/bin/env python3
"""
Offline verifier for a Uderia eval **attestation** (proof-pack A6).

A Uderia eval verdict is cryptographically bound to the exact trajectory it
graded: the signed payload is `turn_tip_hash | verdict_digest | profile_class`,
signed with the same Ed25519 provenance key that seals the turn's Execution
Provenance Chain. Tampering with any verdict changes `verdict_digest`, and the
signature no longer verifies.

This script needs only the public key — no Uderia install, no private key, no
network. It reproduces four independent checks against the committed real sample:

  1. recompute `verdict_digest` from the span's verdict events  → matches stored
  2. recompute the public-key fingerprint                       → matches stored
  3. verify the Ed25519 signature over the bound payload        → VERIFIED
  4. tamper test: altering the payload                          → signature REJECTS

Usage:
    pip install cryptography
    python verify_attestation.py            # uses the committed sample + pubkey
    python verify_attestation.py <span.json> <provenance_key.pub>
"""
import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

HERE = Path(__file__).resolve().parent


def verdict_digest(span: dict) -> str:
    """Canonical SHA-256 over the span's verdict events (mirrors eval/online.py)."""
    arr = [
        {"d": e["attributes"]["dimension"], "p": e["attributes"]["passed"], "s": e["attributes"]["score"]}
        for e in span["events"]
        if e["name"] == "verdict"
    ]
    return hashlib.sha256(json.dumps(arr, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    span_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "attestation_sample.json"
    pub_path = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "provenance_key.pub"

    span = json.loads(span_path.read_text())
    att = span["attestation"]
    pub = serialization.load_pem_public_key(pub_path.read_bytes())
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fp = hashlib.sha256(raw).hexdigest()

    digest = verdict_digest(span)
    payload = f"{att['turn_tip_hash']}|{att['verdict_digest']}|{att['profile_class']}"
    sig = base64.b64decode(att["signature"])

    ok = True

    c1 = digest == att["verdict_digest"]
    print(f"1. recomputed verdict_digest == stored : {c1}  ({digest[:16]}…)")
    ok &= c1

    c2 = fp == att["key_fingerprint"]
    print(f"2. public-key fingerprint == attestation: {c2}  ({fp[:16]}…)")
    ok &= c2

    try:
        pub.verify(sig, payload.encode())
        print("3. Ed25519 signature over payload      : VERIFIED")
    except Exception as exc:  # noqa: BLE001
        print(f"3. Ed25519 signature over payload      : FAILED ({exc})")
        ok = False

    try:
        pub.verify(sig, payload.replace(att["profile_class"], "tampered").encode())
        print("4. tamper test                         : FAILED — verified a tampered payload!")
        ok = False
    except Exception:
        print("4. tamper test                         : signature REJECTS altered payload (correct)")

    print()
    print("RESULT:", "ATTESTATION VALID — verdict is cryptographically bound to the trajectory"
          if ok else "ATTESTATION INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
