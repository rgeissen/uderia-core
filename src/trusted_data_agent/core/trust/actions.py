"""
PROP-008 OBDA-ACT — the ``action_type`` trust kind (increment B1).

An action type is a signed WRITE contract on the ontology (``dbdeploy/action_model.py``)
proven by the A1–A6 gate (``dbdeploy/action_validation.py``). This module mirrors
``products.py`` line for line in discipline — a signed write contract is the products
ratchet applied to a write:

* signable content = ``{manifest_hash, validation_report_hash}`` — the contract AND the
  proof it passed the gate, content-bound and id-free (the generic
  ``portability.capture``/``reinstate`` apply unchanged);
* five refusals, by status — ``invalid_manifest`` · ``unvalidated`` · ``stale_report``
  (the report validated a different manifest) · ``gate_blocked`` · ``unsigned_ingredient``
  (the ontology is not signed-and-intact: a signed write contract stands on a signed model);
* ``live_hash`` recomputes the content from the node's CURRENT manifest + stored report +
  the model facts the report's proof read from the graph AS THEY ARE NOW (``model_now`` —
  the target's resolution, its columns, the writable attributes' closed domains), so an
  edited manifest OR a change in the model under the action reads 🟠 CHANGED against its
  signature, never silently intact; a change elsewhere in the graph moves nothing.

READ-ONLY at the KG layer: nodes are loaded through ``composition.load_kg_graph`` (the
direct-SQL read ontology signing already uses); stamping the report on the node is the
caller's job through ``GraphStore``. Part of the removable Ontology Suite — consumers
outside ``core/trust/`` import defensively.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from . import composition as _comp
from . import store as _store
from .attestation import TrustState, resolve_state

logger = logging.getLogger("quart.app")

ACTION_KIND = "action_type"
MANIFEST_PROP = "manifest"
REPORT_PROP = "validation_report"


def action_artifact_id(kg_id: str, action_name: str) -> str:
    """``(kg_id, action name)``; action names are ``[a-z0-9_]{1,64}`` so ``::`` cannot
    collide (attestations are keyed by kind too, so a product of the same name is apart)."""
    return f"{kg_id}::{action_name}"


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


def version_hash_for(manifest_raw: Dict[str, Any], report: Dict[str, Any]) -> str:
    """The signable content hash — the PARSED manifest's hash (an author's key order or
    an omitted default never moves it) and the stored report hashed with the same
    canonical dumps as ``action_validation.report_hash``, so the two formulas agree by
    construction."""
    from trusted_data_agent.dbdeploy.action_model import manifest_hash, parse_manifest
    return _canon_hash({"manifest_hash": manifest_hash(parse_manifest(manifest_raw)),
                        "validation_report_hash": _canon_hash(report)})


def _load(kg_id: str, action_name: str, db_path: Optional[str] = None):
    """``(manifest, stored report, entities, relationships, error)`` — one graph read serves
    the node AND the model facts its proof depends on."""
    try:
        ents, rels = _comp.load_kg_graph(kg_id, db_path)
    except Exception as e:  # noqa: BLE001
        return None, None, None, None, f"could not load knowledge graph {kg_id!r}: {e}"
    node = next((e for e in ents if e.get("entity_type") == ACTION_KIND
                 and e.get("name") == action_name), None)
    if node is None:
        return None, None, ents, rels, f"no action_type named {action_name!r} on graph {kg_id!r}"
    props = node.get("properties") or {}
    manifest = props.get(MANIFEST_PROP)
    if not isinstance(manifest, dict):
        return None, None, ents, rels, f"action {action_name!r} carries no manifest — author it first"
    report = props.get(REPORT_PROP)
    return manifest, (report if isinstance(report, dict) else None), ents, rels, None


def load_action(kg_id: str, action_name: str, db_path: Optional[str] = None
                ) -> Tuple[Optional[Dict], Optional[Dict], Optional[str]]:
    """``(manifest, stored report, error)`` for one ``action_type`` node."""
    manifest, report, _e, _r, err = _load(kg_id, action_name, db_path)
    return manifest, report, err


def model_now(manifest: Dict[str, Any], report: Optional[Dict[str, Any]], ents, rels
              ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """``(the model facts today, what moved since the report recorded them)``. The facts are
    the ones the A1–A6 proof read from the graph (``action_types.model_facts`` — the SAME
    function validation runs); a change to any of them is drift, a change anywhere else in
    the graph is not. No report ⇒ nothing to drift from."""
    from trusted_data_agent.core.action_types import facts_drift, model_facts
    from trusted_data_agent.dbdeploy.action_model import parse_manifest
    index: Dict[Tuple[str, str], Dict] = {}
    for e in ents or []:
        index.setdefault((str(e.get("name") or "").lower(), str(e.get("entity_type") or "")), e)
    from trusted_data_agent.kgstore.types import unpublished_extension_names
    facts = model_facts(parse_manifest(manifest),
                        lambda name, et: index.get((str(name or "").lower(), et)), rels or [],
                        unpublished_extension_names(ents or []))
    return facts, (facts_drift(report.get("model_facts"), facts) if isinstance(report, dict) else [])


def _live_report(report: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """The stored proof with TODAY's model facts in place of the recorded ones — identical to
    the stored report exactly while the model under the action is unchanged."""
    from trusted_data_agent.core.action_types import facts_fingerprint
    return {**report, "model_facts": {"fingerprint": facts_fingerprint(facts), "facts": facts}}


def _reading(kg_id: str, name: str, db_path: Optional[str] = None):
    """``(live hash, model drift, manifest, entities)`` from ONE graph read."""
    manifest, report, ents, rels, err = _load(kg_id, name, db_path)
    if err or manifest is None or report is None:
        return None, [], manifest, ents
    try:
        facts, drift = model_now(manifest, report, ents, rels)
        return version_hash_for(manifest, _live_report(report, facts)), drift, manifest, ents
    except Exception:  # noqa: BLE001 — an unparseable manifest cannot be "intact"
        return None, [], manifest, ents


def live_hash(artifact_id: str, db_path: Optional[str] = None) -> Optional[str]:
    """The contract + its proof + the model facts the proof depends on AS THEY ARE NOW: an
    edited contract, or a change in the model under it, reads CHANGED against the signature."""
    kg_id, name = split_artifact_id(artifact_id)
    return _reading(kg_id, name, db_path)[0]


def ontology_state_for(kg_id: str, db_path: Optional[str] = None) -> TrustState:
    """The bottom-up ingredient: is the action's ontology signed-and-intact? Fails toward
    UNSIGNED (blocking) — the products rule, verbatim."""
    try:
        attested = _store.attested_hash("ontology", kg_id, db_path=db_path)
        chash, n = _comp.kg_graph_hash(kg_id, db_path)
        if not n:
            return TrustState.UNSIGNED
        return resolve_state(attested, chash)
    except Exception:  # noqa: BLE001
        return TrustState.UNSIGNED


def _model_ingredient(kg_id: str, manifest: Dict[str, Any], ents, db_path: Optional[str] = None
                      ) -> Tuple[TrustState, str]:
    """The model the action stands on, signed-and-intact or not: the base ontology AND — when
    the target is an authored entity — the extension that holds it (a write contract on an
    extension stands on that extension's signature too). Worst of the two, with the name of
    the one that blocks. Fails toward UNSIGNED."""
    base = ontology_state_for(kg_id, db_path=db_path)
    if base is not TrustState.TRUSTED:
        return base, "ontology"
    try:
        from trusted_data_agent.dbdeploy.action_model import parse_manifest
        from trusted_data_agent.kgstore.types import extension_id_of, is_authored
        entity = parse_manifest(manifest).entity
        node = next((e for e in ents or [] if e.get("entity_type") == "entity"
                     and str(e.get("name") or "").lower() == entity.lower()), None)
        props = (node or {}).get("properties") or {}
        if not is_authored(props):
            return base, "ontology"
        from . import extensions as _ext
        ext_id = extension_id_of(props)
        st = _ext.extension_state(kg_id, ext_id, db_path)
        return TrustState(st["state"]), _ext.extension_label(ext_id)
    except Exception:  # noqa: BLE001 — an ingredient not resolved blocks
        return TrustState.UNSIGNED, "extension"


def sign_action(kg_id: str, action_name: str, signer_uuid: Optional[str] = None,
                db_path: Optional[str] = None) -> Dict[str, Any]:
    """The earned-signature gate + sign, in ONE seam (REST, CI and the Designer call
    this). A refusal is machine-readable and changes nothing."""
    from .validators import action_type_validator
    manifest, report, ents, rels, err = _load(kg_id, action_name, db_path=db_path)
    if err:
        return {"signed": False, "reason": "not_found", "detail": err}
    try:
        _facts, drift = model_now(manifest, report, ents, rels)
    except Exception:  # noqa: BLE001 — an unparseable manifest is the validator's refusal to name
        drift = []
    state, label = _model_ingredient(kg_id, manifest, ents, db_path)
    verdict = action_type_validator(manifest=manifest, stored_report=report,
                                    ontology_state=state, ontology_label=label,
                                    model_drift=drift)
    if not verdict.signable:
        return {"signed": False, "reason": verdict.status, "validation": verdict.to_dict()}
    aid = action_artifact_id(kg_id, action_name)
    vhash = version_hash_for(manifest, report)
    env = _store.sign(ACTION_KIND, aid, vhash, verdict=verdict.to_dict(),
                      signer_uuid=signer_uuid, db_path=db_path)
    return {"signed": bool(env.get("signed")), "kind": ACTION_KIND, "artifact_id": aid,
            "version_hash": vhash,
            "validation": {"status": verdict.status, "signable": True, "note": verdict.note},
            "fingerprint": env.get("key_fingerprint")}


def action_state(kg_id: str, action_name: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """The badge reading for one action type (the products shape)."""
    aid = action_artifact_id(kg_id, action_name)
    attested = _store.attested_hash(ACTION_KIND, aid, db_path=db_path)
    lh, drift, manifest, ents = _reading(kg_id, action_name, db_path=db_path)
    if lh is None:
        state = TrustState.UNSIGNED if attested is None else TrustState.CHANGED
    else:
        state = resolve_state(attested, lh)
    signed_at = None
    if attested is not None:
        try:
            rec = _store.load_attestation(ACTION_KIND, aid, db_path=db_path)
            signed_at = float((rec or {}).get("signed_at") or 0) or None
        except Exception:  # noqa: BLE001
            signed_at = None
    # what moved in the model since the proof was built, in words — so a changed action says
    # whether its CONTRACT or the MODEL under it moved (validate again either way). A trusted
    # reading carries none: its signature covers today's facts, whatever the report says.
    # `stale` tells the two stale proofs apart: `model` (a fact moved) · `unrecorded` (the
    # proof predates the facts being recorded — nothing is known to have moved)
    from trusted_data_agent.core.action_types import UNRECORDED
    if state is TrustState.TRUSTED:
        drift = []
    stale = None if not drift else ("unrecorded" if drift == [UNRECORDED] else "model")
    out = {"artifact_id": aid, "state": state.value, "attested_hash": attested,
           "live_hash": lh, "signed_at": signed_at, "drift": drift, "stale": stale}
    # what the action stands on, when it is not trusted: signing waits for that model's own
    # signature, so a surface offers THAT act (Govern) instead of a sign that is refused
    if state is not TrustState.TRUSTED and isinstance(manifest, dict):
        try:
            ing, label = _model_ingredient(kg_id, manifest, ents, db_path)
            out["stands_on"] = {"state": ing.value, "label": label}
        except Exception:  # noqa: BLE001 — a reading never fails for a courtesy field
            pass
    return out
