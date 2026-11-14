"""
OBDA-READ data-product trust (Phase A3) — content resolution + the earned-signature gate.

The 11th signable kind: ``data_product``. What its signature MEANS is stronger than the
other ten (scope §2.4): not merely "attested + intact" but **"the producer approved this
definition AND the platform mechanically verified that every request in its admissible
space compiles to deterministic, aggregation-safe SQL"**. That is enforced here:

* signable content = ``{manifest_hash, validation_report_hash}`` — the manifest AND the
  proof it passed the V1–V6 gate, content-bound and id-free (survives export/fork; the
  generic ``portability.capture``/``reinstate`` applies unchanged).
* the stored validation report must MATCH the manifest it validated (a stale report for
  an edited manifest cannot sign — S1 would otherwise be a rubber stamp), and must say
  ``signable`` (V-gate blockers block signing, mechanically).
* the bottom-up rule extends compositionally (§2.3): a product is signable only if its
  ontology is signed-and-intact — *a signed product contains only signed ingredients*,
  the same invariant as the agent shield.

READ-ONLY at the KG layer: this module loads product nodes via the same direct-SQL read
``composition.load_kg_graph`` already uses for ontology signing (the trust suite's
documented SQLite-backend scope); it never writes a node — stamping the validation
report onto the product node is the caller's job through ``GraphStore`` (the KG-store
abstraction owns entity writes).

Part of the removable Ontology Suite (``core/trust/``): consumers outside the suite
import defensively. Nothing on any existing execution path imports this module —
additive, byte-identical baseline (charter #15).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

from . import composition as _comp
from . import store as _store
from .attestation import TrustState, resolve_state

logger = logging.getLogger("quart.app")

PRODUCT_KIND = "data_product"

#: The properties keys on the ``data_product`` KG node this module reads.
MANIFEST_PROP = "manifest"
REPORT_PROP = "validation_report"


def product_artifact_id(kg_id: str, product_name: str) -> str:
    """Product identity for the attestation store: ``(kg_id, product name)`` (§2.3).
    The ``::`` separator cannot collide — product names are ``[a-z0-9_]{1,64}``."""
    return f"{kg_id}::{product_name}"


def split_artifact_id(artifact_id: str) -> Tuple[str, str]:
    kg_id, _, name = str(artifact_id).partition("::")
    return kg_id, name


def _canon_hash(d: Dict[str, Any]) -> str:
    """Canonical JSON → sha256, through ``attestation.content_hash`` so an evidence bundle's
    content capture sees the bytes (PROP-009 §4.4: a private hash here made every export of
    this kind say "changed since it was signed"). Byte-identical for a dict; a string keeps
    its JSON encoding, which ``content_hash`` would otherwise hash verbatim."""
    from .attestation import content_hash
    if isinstance(d, str):
        return hashlib.sha256(json.dumps(d).encode("utf-8")).hexdigest()
    return content_hash(d)


def version_hash_for(manifest_raw: Dict[str, Any], report: Dict[str, Any],
                     member_hashes: Optional[Dict[str, Optional[str]]] = None) -> str:
    """The signable content hash: manifest + the proof it passed (§2.3).

    For a COMPOSED product (§8 P1) the content additionally binds the members'
    ATTESTED version hashes — **product lineage**, offline-verifiable, and the
    P3 mechanism for free: a parent's re-sign moves its attested hash, so every
    composed child's live hash diverges from its attestation → 🟠 CHANGED
    (stale-ingredient), the same compositional recompute the agent shield does.
    Hashing the STORED report dict with the same canonical dumps as
    ``product_validation.report_hash`` makes the two formulas identical by
    construction."""
    from trusted_data_agent.dbdeploy.product_model import (
        composed_manifest_hash, is_composed, manifest_hash, parse_composed,
        parse_manifest)
    if is_composed(manifest_raw):
        return _canon_hash({
            "manifest_hash": composed_manifest_hash(parse_composed(manifest_raw)),
            "validation_report_hash": _canon_hash(report),
            "member_hashes": dict(sorted((member_hashes or {}).items())),
        })
    return _canon_hash({
        "manifest_hash": manifest_hash(parse_manifest(manifest_raw)),
        "validation_report_hash": _canon_hash(report),
    })


def member_attested_hashes(kg_id: str, members,
                           db_path: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Each member's CURRENT attested version hash — the lineage record. A member
    with no attestation maps to None (which P2 refuses before this ever signs)."""
    return {m: _store.attested_hash(PRODUCT_KIND, product_artifact_id(kg_id, m),
                                    db_path=db_path)
            for m in (members or ())}


def load_product(kg_id: str, product_name: str,
                 db_path: Optional[str] = None,
                 ) -> Tuple[Optional[Dict], Optional[Dict], Optional[str]]:
    """Load a product node's ``(manifest, stored validation report, error)``.
    The report may be ``None`` (authored but never validated) — the validator then
    blocks signing with the reason named."""
    try:
        ents, _rels = _comp.load_kg_graph(kg_id, db_path)
    except Exception as e:  # noqa: BLE001
        return None, None, f"could not load knowledge graph {kg_id!r}: {e}"
    node = next((e for e in ents
                 if e.get("entity_type") == PRODUCT_KIND
                 and e.get("name") == product_name), None)
    if node is None:
        return None, None, (f"no data_product named {product_name!r} on graph {kg_id!r}")
    props = node.get("properties") or {}
    manifest = props.get(MANIFEST_PROP)
    if not isinstance(manifest, dict):
        return None, None, (f"product {product_name!r} carries no manifest — "
                            f"author it before validating or signing")
    report = props.get(REPORT_PROP)
    return manifest, (report if isinstance(report, dict) else None), None


def live_hash(artifact_id: str, db_path: Optional[str] = None) -> Optional[str]:
    """The badge's live version hash — recomputed from the node's CURRENT manifest +
    stored report, same formula as signing. An edited manifest (or refreshed report)
    therefore reads 🟠 CHANGED against the attestation, never silently intact."""
    kg_id, name = split_artifact_id(artifact_id)
    manifest, report, err = load_product(kg_id, name, db_path=db_path)
    if err or manifest is None or report is None:
        return None   # unresolvable → the badge layer treats it as not-confirmable
    try:
        from trusted_data_agent.dbdeploy.product_model import is_composed, parse_composed
        mh = None
        if is_composed(manifest):
            # P3: recompute the lineage from the members' CURRENT attestations —
            # a re-signed parent moves this hash, flipping the child CHANGED.
            mh = member_attested_hashes(
                kg_id, parse_composed(manifest).members, db_path=db_path)
        return version_hash_for(manifest, report, member_hashes=mh)
    except Exception:  # noqa: BLE001 — an unparseable manifest cannot be "intact"
        return None


def ontology_state_for(kg_id: str, db_path: Optional[str] = None) -> TrustState:
    """The bottom-up ingredient state: is the product's ontology signed-and-intact?
    Fails toward UNSIGNED (blocking) — an unresolvable ontology must never read as a
    signed ingredient."""
    try:
        attested = _store.attested_hash("ontology", kg_id, db_path=db_path)
        chash, n_ents = _comp.kg_graph_hash(kg_id, db_path)
        olive = chash if n_ents else None
        if olive is None:
            return TrustState.UNSIGNED
        return resolve_state(attested, olive)
    except Exception:  # noqa: BLE001
        return TrustState.UNSIGNED


def sign_product(kg_id: str, product_name: str,
                 signer_uuid: Optional[str] = None,
                 db_path: Optional[str] = None) -> Dict[str, Any]:
    """The earned-signature gate + sign, in one seam (the A4 REST route and the CI
    gate both call THIS — one implementation, no drift).

    Refusals are machine-readable and never a guess; a refused sign changes nothing.
    """
    from .validators import data_product_validator
    from trusted_data_agent.dbdeploy.product_model import (
        ProductManifestError, flatten_composed, is_composed, parse_composed)

    manifest, report, err = load_product(kg_id, product_name, db_path=db_path)
    if err:
        return {"signed": False, "reason": "not_found", "detail": err}

    # §8 — a composed product resolves its members here (the one impure seam),
    # so the validator stays pure: it receives the FLAT form + each member's
    # trust STATE and applies P1/P2 as data.
    flat = None
    member_states = None
    mhashes = None
    if is_composed(manifest):
        try:
            cm = parse_composed(manifest)
            member_raw = {}
            for m in cm.members:
                mm, _mr, _me = load_product(kg_id, m, db_path=db_path)
                if mm is not None:
                    member_raw[m] = mm
            flat = flatten_composed(cm, member_raw)
            member_states = {m: product_state(kg_id, m, db_path=db_path)["state"]
                             for m in cm.members}
            mhashes = member_attested_hashes(kg_id, cm.members, db_path=db_path)
        except ProductManifestError as e:
            return {"signed": False, "reason": "invalid_manifest",
                    "detail": f"{e.code}: {e.message}"}

    verdict = data_product_validator(
        manifest=manifest, stored_report=report,
        ontology_state=ontology_state_for(kg_id, db_path=db_path),
        flat_manifest=flat, member_states=member_states)
    if not verdict.signable:
        return {"signed": False, "reason": verdict.status,
                "validation": verdict.to_dict()}

    artifact_id = product_artifact_id(kg_id, product_name)
    vhash = version_hash_for(manifest, report, member_hashes=mhashes)
    env = _store.sign(PRODUCT_KIND, artifact_id, vhash,
                      verdict=verdict.to_dict(), signer_uuid=signer_uuid,
                      db_path=db_path)
    return {
        "signed": bool(env.get("signed")),
        "kind": PRODUCT_KIND, "artifact_id": artifact_id,
        "version_hash": vhash,
        "validation": {"status": verdict.status, "signable": True,
                       "note": verdict.note},
        "fingerprint": env.get("key_fingerprint"),
    }


def product_state(kg_id: str, product_name: str,
                  db_path: Optional[str] = None) -> Dict[str, Any]:
    """The badge reading for one product: attested vs live → state. Used by the
    catalog (E4's enforce mode serves only 🟢 versions) and the trust popover."""
    artifact_id = product_artifact_id(kg_id, product_name)
    attested = _store.attested_hash(PRODUCT_KIND, artifact_id, db_path=db_path)
    lh = live_hash(artifact_id, db_path=db_path)
    if lh is None:
        # No confirmable live content: never-validated or unloadable. An attested
        # product whose content cannot be confirmed reads CHANGED (fail-safe, the
        # same direction as the artifact badge's _NO_LIVE_HASH rule), an unattested
        # one reads UNSIGNED.
        state = TrustState.UNSIGNED if attested is None else TrustState.CHANGED
    else:
        state = resolve_state(attested, lh)
    # WHEN it was signed, so a surface can say WHOSE signature is applying. A
    # re-validation never signs — but when edited content returns to the signed
    # bytes, the EXISTING attestation matches again and the row goes 🟢 with no
    # act by the operator. Without naming that signature, the reading is
    # indistinguishable from "validate signed it on my behalf", which is the
    # worst thing a trust surface can imply. Deliberately a second read rather
    # than reusing one load_attestation(): the TC-5 rule (an unsigned row must
    # never read 🟢) lives inside attested_hash and must keep flowing through
    # that ONE accessor — re-deriving it here is the duplicated-rule defect.
    signed_at = None
    if attested is not None:
        try:
            _rec = _store.load_attestation(PRODUCT_KIND, artifact_id, db_path=db_path)
            signed_at = float((_rec or {}).get("signed_at") or 0) or None
        except (TypeError, ValueError, Exception):  # never break a badge read
            signed_at = None
    return {"artifact_id": artifact_id, "state": state.value,
            "attested_hash": attested, "live_hash": lh, "signed_at": signed_at}
