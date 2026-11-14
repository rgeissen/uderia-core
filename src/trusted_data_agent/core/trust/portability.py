"""
Marketplace trust portability (Tier 5.2, Phase C) — trust that survives publish/subscribe.

Trust travels with **content, not ids**. Signatures are content-bound
(``store._sign_payload_str = kind|version_hash``), so an attestation survives a marketplace
id remap: **capture** it at publish, **reinstate** it against the materialized copy. The
reinstate only "takes" if the copy's live content hash equals what was signed — so a
re-generated or tampered copy stays ⚪/🟠, never blindly 🟢 (the honesty gate).

Uniform across **every** marketplace surface (skills, extensions, KGs, collections, packs,
component instances): each embeds ``capture()`` under a ``trust`` envelope key at publish
and calls ``reinstate_kind()`` after materializing content. Aggregates (packs) reinstate
their members first, then themselves — same helper, recursively.

Because the signer fingerprint travels in the claim and the signature is content-bound,
this is cross-instance-ready: a future remote consumer verifies with the publisher's public
key and no re-architecture. This module is pure/deterministic and never raises.
"""

from __future__ import annotations

from typing import Optional

from . import store as _store
from . import content as _content
from .attestation import resolve_state, TrustState


def capture(kind: str, artifact_id: str, db_path: Optional[str] = None,
            owner_uuid: Optional[str] = None) -> Optional[dict]:
    """The portable trust claim for a signed artefact, or None if unsigned.

    Embed this under a ``trust`` key in whatever the marketplace publishes.
    """
    rec = _store.load_attestation(kind, artifact_id, db_path=db_path, owner_uuid=owner_uuid)
    if not rec or not rec.get("signed"):
        return None
    return {
        "kind": kind,
        "version_hash": rec.get("version_hash"),
        "signature": rec.get("signature"),
        "signer_fingerprint": rec.get("signer_fingerprint"),
        "signer_uuid": rec.get("signer_uuid"),
        # cross-instance verification; decoded for legacy rows that stored raw PEM
        # bytes — a captured claim must always be JSON-serializable (pack manifests)
        "signer_public_key": (rec["signer_public_key"].decode("utf-8", "strict")
                              if isinstance(rec.get("signer_public_key"),
                                            (bytes, bytearray))
                              else rec.get("signer_public_key")),
        "verdict": rec.get("verdict"),
        "signed_at": rec.get("signed_at"),
    }


def reinstate(kind: str, new_id: str, claim: Optional[dict], live_hash: Optional[str],
              db_path: Optional[str] = None, owner_uuid: Optional[str] = None) -> bool:
    """Re-establish a captured claim on a materialized copy — **iff** the copy's content is
    byte-identical to what was signed. Returns True if the copy is now signed 🟢.

    The copied attestation preserves the original signature/fingerprint/signer, so the copy
    verifies exactly as the original did (content-bound signature).
    """
    if not claim or not live_hash:
        return False
    if claim.get("version_hash") != live_hash or not claim.get("signature"):
        return False
    _store.record_attestation(
        kind, new_id, claim["version_hash"],
        verdict=claim.get("verdict"),
        signer_uuid=claim.get("signer_uuid"),
        signer_fingerprint=claim.get("signer_fingerprint"),
        signature=claim.get("signature"),
        signed=True,
        signed_at=claim.get("signed_at"),
        signer_public_key=claim.get("signer_public_key"),
        db_path=db_path,
        owner_uuid=owner_uuid,       # the copy's owner (agent kinds are owner-scoped)
        event="reinstate",
    )
    return True


def reinstate_kind(kind: str, new_id: str, claim: Optional[dict],
                   db_path: Optional[str] = None) -> bool:
    """``reinstate()`` computing the copy's live hash via the canonical content module —
    the one call a leaf-kind marketplace flow makes after materializing content."""
    return reinstate(kind, new_id, claim, _content.live_hash(kind, new_id), db_path=db_path)


def claim_state(claim: Optional[dict], live_hash: Optional[str]) -> str:
    """Resolve a claim against live content **without persisting** — for a marketplace-card
    "publisher signed" badge. 'trusted' iff the claim's hash matches the live content."""
    if not claim:
        return TrustState.UNSIGNED.value
    return resolve_state(claim.get("version_hash"), live_hash).value
