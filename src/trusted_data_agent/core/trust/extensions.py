"""
Ontology extensions — tier-1 attestation on a signed graph (PROP-008 A5 / §3a, §4.6).

A signed graph must stay extensible — by its publisher and by every subscriber — without
any extension moving the publisher's seal. The mechanism is the compositional ratchet the
platform already has (OBDA-READ §8: a contract stacked on an ontology, excluded from the
ontology's hash, signed on its own, with the base's attested hash inside its signature).
An extension is that shape one layer over:

  tier 0 · base model   the walked layers as published — `ontology`, `kg_content_hash`
                        (which EXCLUDES the authored layer, composition.py)
  tier 1 · extension    one `authored` layer per extender, keyed by `extension_id` —
                        THIS kind: signable content = {extension_hash, base_attested_hash}
  tier 2 · contracts    data products / action types, stacked on tier 0 or a PUBLISHED tier 1

**The one rule that keeps the tiers separable: an extension only adds, it never modifies.**
It may add nodes, and edges SOURCED at its nodes (a target may be a base node). It may not
change a base node, rename it, or remove a base edge. Enforced at the write chokepoint
(GraphStore, A1); re-checked here as the `base_mutation` refusal so a payload that arrived
by another door cannot be signed.

What a reading means, all free from `live_hash` recomputing the lineage:

  * base republished (re-signed), extension untouched ⇒ the extension reads
    **changed · base moved**; the extender re-validates and re-signs; the base's seal is
    untouched;
  * extension edited (a node published, a property changed) ⇒ **changed · extension
    edited**; only that extension flips — every other extender's tier stays green;
  * an agent grounded on base + extension(s) is green only when the base is intact, each
    extension is intact, and each extension's recorded base hash equals the base's
    current attested hash — the bottom-up rule with one more ingredient kind
    (composition._extension_ingredients names which moved).

Only PUBLISHED nodes are an extension's content: a draft is not yet a claim (it never
reaches a released-mode agent, never travels with a pack, never moves a signature). The
`design_decision` nodes are provenance ABOUT the extension, not its content — excluded.

Lifecycle, with the one rule §13 adds to Tier 4.3: a person with the approver tier
publishes; an `agent:<profile_id>` actor is refused WHATEVER the deployment's approval
policy says (`publish_decision`). A model change stays behind a human seam the way
`review` keeps a withheld answer.

READ-ONLY at the KG layer (the products.py precedent): loads through the same direct-SQL
read ontology signing uses; every node WRITE (publish stamps, dangling-edge stamps) goes
through GraphStore in the caller. Part of the removable Ontology Suite; every consumer
outside `core/trust/` imports defensively. Nothing on an existing execution path imports
this module — additive (charter #15).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import composition as _comp
from . import store as _store
from .attestation import TrustState, resolve_state

logger = logging.getLogger("quart.app")

EXTENSION_KIND = "ontology_extension"
_SEP = "::ext::"

#: Authored node types that are the extension's CONTENT (a design_decision is provenance).
_CONTENT_TYPES_EXCLUDED = frozenset({"design_decision"})


# ── identity ─────────────────────────────────────────────────────────────────

def extension_artifact_id(kg_id: str, extension_id: str) -> str:
    """`(kg_id, extension_id)` is the artifact identity. `::ext::` cannot collide with a
    data product's `::` (a product name is `[a-z0-9_]{1,64}`)."""
    return f"{kg_id}{_SEP}{extension_id}"


def split_artifact_id(artifact_id: str) -> Tuple[str, str]:
    kg_id, sep, ext = str(artifact_id).partition(_SEP)
    return (kg_id, ext) if sep else (kg_id, "")


def is_extension_artifact(artifact_id: str) -> bool:
    return _SEP in str(artifact_id)


def _types():
    from trusted_data_agent.kgstore import types as T
    return T


def extension_id_for(user_uuid) -> str:
    return _types().extension_id_for(user_uuid)


def extension_id_of(properties) -> str:
    return _types().extension_id_of(properties)


_UNSET = object()   # "not given" — distinct from a given None (nothing published)


# ── pure content ──────────────────────────────────────────────────────────────

def _canon_hash(d: Any) -> str:
    """Canonical JSON → sha256, through ``attestation.content_hash`` so an evidence bundle's
    content capture sees the bytes (PROP-009 §4.4: a private hash here made every export of
    this kind say "changed since it was signed"). Byte-identical for a dict; a string keeps
    its JSON encoding, which ``content_hash`` would otherwise hash verbatim."""
    from .attestation import content_hash
    if isinstance(d, str):
        return hashlib.sha256(json.dumps(d).encode("utf-8")).hexdigest()
    return content_hash(d)


def _name_key(e: Dict[str, Any]) -> Tuple[str, str]:
    return (str(e.get("name", "")), str(e.get("entity_type", "")))


def split_layers(entities: List[Dict], relationships: List[Dict]
                 ) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    """(base_entities, base_relationships, authored_entities, authored_relationships).

    The extension half is ``kgstore.types.is_extension_layer``: an authored node OR its
    realisation (the schema, tables and columns its landing wrote). A realisation is a
    fact of THIS deployment's warehouse and goes where its extension goes — never out
    with the base: a base listing used to carry a landed table as base, an UNPUBLISHED
    extension's included. An edge is the extension half's iff it TOUCHES one of its nodes
    — the additive-only rule makes every such edge sourced at an authored node, but the
    split keeps an edge that reaches one from the base out of the base too. Pure; the
    base half matches `kg_content_hash` for the extension layer (that hash also leaves
    out the stacked contracts, which this split keeps in the base half — they travel)."""
    T = _types()
    auth_keys = {_name_key(e) for e in (entities or []) if T.is_extension_layer(e.get("properties"))}
    base_e = [e for e in (entities or []) if _name_key(e) not in auth_keys]
    auth_e = [e for e in (entities or []) if _name_key(e) in auth_keys]
    base_r, auth_r = [], []
    for r in relationships or []:
        s = (str(r.get("source_name", "")), str(r.get("source_type", "")))
        t = (str(r.get("target_name", "")), str(r.get("target_type", "")))
        (auth_r if (s in auth_keys or t in auth_keys) else base_r).append(r)
    return base_e, base_r, auth_e, auth_r


def extension_nodes(entities: List[Dict], extension_id: str, *,
                    published_only: bool = True) -> List[Dict]:
    """The nodes of ONE extension — its content (published, non-decision) by default."""
    T = _types()
    out = []
    for e in entities or []:
        pr = e.get("properties") or {}
        if not T.is_authored(pr) or T.extension_id_of(pr) != extension_id:
            continue
        if e.get("entity_type") in _CONTENT_TYPES_EXCLUDED:
            continue
        if published_only and not T.is_published_authored(pr):
            continue
        out.append(e)
    return out


def extension_edges(relationships: List[Dict], nodes: Iterable[Dict]) -> List[Dict]:
    """The edges SOURCED at the given nodes (the additive-only rule: an extension owns
    exactly the edges that start on its nodes; a target may be a base node)."""
    keys = {_name_key(e) for e in nodes}
    return [r for r in (relationships or [])
            if (str(r.get("source_name", "")), str(r.get("source_type", ""))) in keys]


def base_mutations(entities: List[Dict], relationships: List[Dict],
                   extension_id: str) -> List[str]:
    """What an extension did to the BASE that it may not (§3a) — named, never a guess.

    Two shapes, both detectable from the composition alone: (1) a node of this extension
    SHADOWS a base node (same name and type on both layers — a rename-by-overlay); (2) an
    edge that STARTS at a base node and ENDS at a node of this extension — a base edge
    the extension added (its edges must be sourced at its own nodes). A write through
    GraphStore cannot produce either; a payload that arrived by another door can."""
    T = _types()
    # the base set is decided by each node's OWN layer — never by name: a shadow is a
    # base node and an authored twin of the same name, and a by-name split would let the
    # twin hide the very base node it overlays
    # a stacked contract (an action type acting on this extension's entity) is neither base
    # nor extension: its `acts_on` edge is the contract's, not a base edge into the extension
    base_keys = {_name_key(e) for e in (entities or [])
                 if not T.is_extension_layer(e.get("properties")) and not T.is_stacked_contract(e)}
    mine = extension_nodes(entities, extension_id, published_only=False)
    mine_keys = {_name_key(e) for e in mine}
    out: List[str] = []
    for k in sorted(mine_keys & base_keys):
        out.append(f"{k[0]} ({k[1]}) shadows a base node of the same name and type")
    for r in relationships or []:
        s = (str(r.get("source_name", "")), str(r.get("source_type", "")))
        t = (str(r.get("target_name", "")), str(r.get("target_type", "")))
        if s in base_keys and t in mine_keys:
            out.append(f"{s[0]} --[{r.get('relationship_type')}]--> {t[0]}: an edge sourced at a base node")
    return out


def extension_content_hash(entities: List[Dict], relationships: List[Dict],
                           extension_id: str) -> Optional[str]:
    """The content identity of one extension's PUBLISHED layer, or None when it has
    nothing published (nothing to sign, nothing to confirm). Order-independent — two
    copies of the same extension across an id remap hash the same (what lets a
    subscriber's reinstatement take). Every property is hashed: a re-deploy that moves a
    node's `deploy_receipt`, a resync that stamps `dangling_edges`, are real changes to
    what the extension says about itself — honest drift, never silent."""
    nodes = extension_nodes(entities, extension_id, published_only=True)
    if not nodes:
        return None
    ents = sorted(({"name": str(e.get("name", "")), "entity_type": str(e.get("entity_type", "")),
                    "properties": e.get("properties") or {}} for e in nodes),
                  key=lambda e: (e["entity_type"], e["name"],
                                 json.dumps(e["properties"], sort_keys=True, default=str)))
    # an edge to a node not yet published is invisible to every agent (the released view
    # drops it) — so it is not part of what the signature vouches for: drafting an attribute
    # under a published entity must not read as "extension edited"
    T = _types()
    unpublished = {_name_key(e) for e in (entities or [])
                   if T.is_authored(e.get("properties")) and not T.is_published_authored(e.get("properties"))}
    rels = sorted(({"relationship_type": str(r.get("relationship_type", "")),
                    "source_name": str(r.get("source_name", "")), "source_type": str(r.get("source_type", "")),
                    "target_name": str(r.get("target_name", "")), "target_type": str(r.get("target_type", ""))}
                   for r in extension_edges(relationships, nodes)
                   if (str(r.get("target_name", "")), str(r.get("target_type", ""))) not in unpublished),
                  key=lambda r: (r["relationship_type"], r["source_name"], r["source_type"],
                                 r["target_name"], r["target_type"]))
    return _canon_hash({"entities": ents, "relationships": rels})


def published_layer_hash(entities: List[Dict], relationships: List[Dict]) -> str:
    """ONE hash over every extension's published content on a graph — what the released
    view (Model B) is rebuilt on when it moves. Empty layer ⇒ the hash of nothing."""
    ids = sorted(extensions_on_graph(entities))
    return _canon_hash({i: extension_content_hash(entities, relationships, i) for i in ids})


def signable_content(extension_hash: Optional[str], base_attested_hash: Optional[str]) -> str:
    """The signable content: the extension's own hash AND the base's ATTESTED hash — the
    lineage. A base re-sign moves this, so the extension reads `changed · base moved`
    with the base's seal untouched (§3a)."""
    return _canon_hash({"extension_hash": extension_hash, "base_attested_hash": base_attested_hash})


def extensions_on_graph(entities: List[Dict]) -> Dict[str, Dict[str, int]]:
    """`{extension_id: {"published": n, "unpublished": m}}` over a graph's authored nodes
    (decisions excluded) — what the Govern dialog lists."""
    T = _types()
    out: Dict[str, Dict[str, int]] = {}
    for e in entities or []:
        pr = e.get("properties") or {}
        if not T.is_authored(pr) or e.get("entity_type") in _CONTENT_TYPES_EXCLUDED:
            continue
        cell = out.setdefault(T.extension_id_of(pr), {"published": 0, "unpublished": 0})
        cell["published" if T.is_published_authored(pr) else "unpublished"] += 1
    return out


def dangling_edges(entities: List[Dict], extension_id: str) -> List[str]:
    """The edges a resync DROPPED from this extension because the base no longer had the
    other end — stamped on the authored source node by `GraphStore.replace_model_layer`
    (`properties.dangling_edges`), so a finding the resync made is never lost. Named,
    never resolved here."""
    out: List[str] = []
    for e in extension_nodes(entities, extension_id, published_only=False):
        for d in (e.get("properties") or {}).get("dangling_edges") or []:
            out.append(str(d))
    return out


def extension_conformance(entities: List[Dict], relationships: List[Dict],
                          extension_id: str):
    """Conformance over the COMPOSITION base + this extension (other extenders' layers
    left out — another team's breakage never blocks this extension), keeping the issues
    that name a node of this extension, plus the dropped edges a resync recorded."""
    from trusted_data_agent.kgstore.conformance import (
        ConformanceIssue, ConformanceReport, MEDIUM, check_conformance, _status_from)
    base_e, base_r, _ae, _ar = split_layers(entities, relationships)
    mine = extension_nodes(entities, extension_id, published_only=False)
    mine_names = {str(e.get("name", "")) for e in mine}
    # the extension composes WITH its own realisation (the table and columns its landing
    # wrote, and the edges touching them) — the split moved those out of the base half
    real = _types().realisations_of(entities, mine_names)
    real_keys = {_name_key(e) for e in real}
    comp_keys = {_name_key(e) for e in base_e} | {_name_key(e) for e in mine} | real_keys
    real_r = [r for r in (relationships or [])
              if ((str(r.get("source_name", "")), str(r.get("source_type", ""))) in real_keys
                  or (str(r.get("target_name", "")), str(r.get("target_type", ""))) in real_keys)
              and (str(r.get("source_name", "")), str(r.get("source_type", ""))) in comp_keys
              and (str(r.get("target_name", "")), str(r.get("target_type", ""))) in comp_keys]
    comp_e = base_e + mine + real
    comp_r = base_r + extension_edges(relationships, mine) + [r for r in real_r if r not in base_r]
    report = check_conformance(comp_e, comp_r)
    issues = []
    for i in report.issues:
        if i.code == "empty_ontology" or any(
                any(n and n in ref for n in mine_names) for ref in (i.refs or [])):
            issues.append(i)
    dangling = dangling_edges(entities, extension_id)
    if dangling:
        issues.append(ConformanceIssue(
            "extension_dangling_edge", MEDIUM,
            "an extension edge pointed at a base node the base no longer has (dropped on resync)",
            len(dangling), dangling[:10]))
    return ConformanceReport(_status_from(issues), issues, len(comp_e), len(comp_r))


# ── lifecycle: who may publish ────────────────────────────────────────────────

def is_agent_actor(actor: Optional[str]) -> bool:
    return str(actor or "").startswith("agent:")


def publish_decision(actor: Optional[str], actor_tier: str, policy=None,
                     conformance: Optional[str] = None,
                     override_nonconformant: bool = False) -> Dict[str, Any]:
    """May THIS actor publish an extension? Pure. The Tier 4.3 publish gate (conformance ×
    policy) with the one rule §13 adds: **a person publishes; an agent only drafts** — an
    `agent:<profile_id>` actor is refused under EVERY policy, and the person must hold an
    approver tier even when the policy requires no approval for the ontology itself.

    The approver-tier person IS the approval here: the extension lifecycle has no
    propose/approve round of its own (an extension is one extender's layer — separation
    of duties would mean no solo extender could ever publish), so the Tier 4.3 primitive
    is asked with the approval already granted and judges conformance.
    """
    from trusted_data_agent.core.governance_lifecycle import (
        ApprovalStatus, GovernancePolicy, LifecycleAction, LifecycleStatus, validate_transition)
    policy = policy or GovernancePolicy()
    if is_agent_actor(actor):
        return {"allowed": False, "code": "agent_publish_refused",
                "reason": "an agent only drafts — publishing an extension is a person's act "
                          "whatever the approval policy says (PROP-008 §13)"}
    if actor_tier not in policy.approver_tiers:
        return {"allowed": False, "code": "not_an_approver",
                "reason": f"tier '{actor_tier}' is not an approver — an extension is published by "
                          f"a person with the approver tier ({', '.join(sorted(policy.approver_tiers))})"}
    d = validate_transition(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT,
                            approval_status=ApprovalStatus.APPROVED, policy=policy,
                            actor_tier=actor_tier, is_owner=True, conformance=conformance,
                            override_nonconformant=override_nonconformant)
    return {"allowed": d.allowed, "code": "published" if d.allowed else "blocked",
            "reason": d.reason, "badge": d.badge}


# ── the impure seams (one each) ───────────────────────────────────────────────

def load_graph(kg_id: str, db_path: Optional[str] = None
               ) -> Tuple[Optional[List[Dict]], Optional[List[Dict]], Optional[str]]:
    try:
        ents, rels = _comp.load_kg_graph(kg_id, db_path)
    except Exception as e:  # noqa: BLE001
        return None, None, f"could not load knowledge graph {kg_id!r}: {e}"
    return ents, rels, None


def graph_summary(kg_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Dict[str, Any]]]:
    """``{extension_id: {"published", "unpublished", "content_hash"}}`` for a stored graph —
    the only facts of the graph a badge reading needs — or None when it cannot be read.
    Cached at the graph's content revision (``kg_revision.memo_at_revision``), so a badge
    request no longer decodes a whole industry model per agent. Read-only: shared."""
    from . import kg_revision as _rev

    def _compute():
        ents, rels, err = load_graph(kg_id, db_path)
        if err or ents is None:
            return None
        return {ext: {**counts, "content_hash": extension_content_hash(ents, rels, ext)}
                for ext, counts in extensions_on_graph(ents).items()}
    return _rev.memo_at_revision(kg_id, db_path, "extension_summary", _compute,
                                 code_ver=_summary_code_version())


_SUMMARY_CODE_VER: Optional[str] = None


def _summary_code_version() -> str:
    """The code that decides the summary: this module (the extension rules), the KG row
    read/decode (composition), the canonical hash (attestation) and the layer predicates."""
    global _SUMMARY_CODE_VER
    if _SUMMARY_CODE_VER is None:
        import os
        from . import kg_revision as _rev
        here = os.path.dirname(os.path.abspath(__file__))
        _SUMMARY_CODE_VER = _rev.code_version(
            os.path.join(here, "extensions.py"), os.path.join(here, "composition.py"),
            os.path.join(here, "attestation.py"),
            os.path.join(here, "..", "..", "kgstore", "types.py"))
    return _SUMMARY_CODE_VER


def base_attested_hash(kg_id: str, db_path: Optional[str] = None) -> Optional[str]:
    return _store.attested_hash("ontology", kg_id, db_path=db_path)


def base_state(kg_id: str, db_path: Optional[str] = None) -> TrustState:
    """The bottom-up ingredient state: is the base signed-and-intact? Fails toward
    UNSIGNED (blocking) — the products.py rule, verbatim."""
    try:
        attested = base_attested_hash(kg_id, db_path)
        chash, n = _comp.kg_graph_hash(kg_id, db_path)
        if not n:
            return TrustState.UNSIGNED
        return resolve_state(attested, chash)
    except Exception:  # noqa: BLE001
        return TrustState.UNSIGNED


def extension_label(extension_id: str) -> str:
    """How an extension is named on a trust surface: "extension by <user>", the wording the
    ontology's Govern dialog uses. Falls back to a short id; never the raw ``ext-<uuid>``,
    which named the part in every answer's evidence until PROP-009's story caught it."""
    eid = str(extension_id or "")
    uid = eid[4:] if eid.startswith("ext-") else ""
    if uid:
        try:
            from trusted_data_agent.auth.database import get_db_session
            from trusted_data_agent.auth.models import User
            with get_db_session() as s:
                u = s.query(User).filter(User.id == uid).first()
                name = (getattr(u, "display_name", None) or getattr(u, "username", None)) if u else None
            if name:
                return f"extension by {name}"
        except Exception:  # noqa: BLE001 — a label is a courtesy, never a failure
            pass
        return f"extension {uid[:8]}"
    return f"extension {eid}" if eid else "extension"


def live_hash(artifact_id: str, db_path: Optional[str] = None) -> Optional[str]:
    """The badge's live hash: the extension's CURRENT published content + the base's
    CURRENT attested hash. Either moving flips a signed extension to CHANGED; nothing
    published reads unresolvable (never intact)."""
    kg_id, ext = split_artifact_id(artifact_id)
    if not ext:
        return None
    summary = graph_summary(kg_id, db_path)
    if summary is None:
        return None
    eh = (summary.get(ext) or {}).get("content_hash")
    if eh is None:
        return None
    return signable_content(eh, base_attested_hash(kg_id, db_path))


def extension_drift(kg_id: str, extension_id: str, db_path: Optional[str] = None,
                    *, entities=None, relationships=None, extension_hash=_UNSET) -> List[str]:
    """Which half moved since the signature — `base moved` · `extension edited` — read
    from the lineage the signature RECORDED (verdict.lineage). Empty when intact or
    unsigned; names both when both moved."""
    rec = _store.load_attestation(EXTENSION_KIND, extension_artifact_id(kg_id, extension_id),
                                  db_path=db_path)
    if not rec or not rec.get("signed"):
        return []
    lineage = ((rec.get("verdict") or {}).get("lineage") or {})
    if extension_hash is _UNSET:
        if entities is None:
            summary = graph_summary(kg_id, db_path)
            if summary is None:
                return ["extension unreadable"]
            extension_hash = (summary.get(extension_id) or {}).get("content_hash")
        else:
            extension_hash = extension_content_hash(entities, relationships, extension_id)
    out: List[str] = []
    if lineage.get("base_attested_hash") != base_attested_hash(kg_id, db_path):
        out.append("base moved")
    if lineage.get("extension_hash") != extension_hash:
        out.append("extension edited")
    return out


def extension_state(kg_id: str, extension_id: str, db_path: Optional[str] = None,
                    *, entities=None, relationships=None) -> Dict[str, Any]:
    """The badge reading for one extension (the products.py shape + `drift`)."""
    aid = extension_artifact_id(kg_id, extension_id)
    attested = _store.attested_hash(EXTENSION_KIND, aid, db_path=db_path)
    if entities is None:
        # The graph's revision-cached summary — never a whole-graph load per reading.
        eh = ((graph_summary(kg_id, db_path) or {}).get(extension_id) or {}).get("content_hash")
    else:
        eh = extension_content_hash(entities, relationships or [], extension_id)
    lh = signable_content(eh, base_attested_hash(kg_id, db_path)) if eh else None
    if lh is None:
        state = TrustState.UNSIGNED if attested is None else TrustState.CHANGED
    else:
        state = resolve_state(attested, lh)
    signed_at = None
    if attested is not None:
        try:
            rec = _store.load_attestation(EXTENSION_KIND, aid, db_path=db_path)
            signed_at = float((rec or {}).get("signed_at") or 0) or None
        except Exception:  # noqa: BLE001
            signed_at = None
    drift = (extension_drift(kg_id, extension_id, db_path, extension_hash=eh)
             if state is TrustState.CHANGED else [])
    return {"artifact_id": aid, "extension_id": extension_id, "state": state.value,
            "attested_hash": attested, "live_hash": lh, "signed_at": signed_at, "drift": drift}


def sign_extension(kg_id: str, extension_id: str, signer_uuid: Optional[str] = None,
                   db_path: Optional[str] = None) -> Dict[str, Any]:
    """The earned-signature gate + sign, in ONE seam (the REST route and the CI gate both
    call this). A refusal is machine-readable and changes nothing."""
    from .validators import ontology_extension_validator
    ents, rels, err = load_graph(kg_id, db_path)
    if err or ents is None:
        return {"signed": False, "reason": "not_found", "detail": err}
    eh = extension_content_hash(ents, rels, extension_id)
    counts = extensions_on_graph(ents).get(extension_id) or {"published": 0, "unpublished": 0}
    report = extension_conformance(ents, rels, extension_id) if eh else None
    verdict = ontology_extension_validator(
        extension_hash=eh, counts=counts,
        base_mutations=base_mutations(ents, rels, extension_id),
        conformance_report=report,
        base_state=base_state(kg_id, db_path))
    if not verdict.signable:
        return {"signed": False, "reason": verdict.status, "validation": verdict.to_dict()}
    bah = base_attested_hash(kg_id, db_path)
    vhash = signable_content(eh, bah)
    vd = verdict.to_dict()
    vd["lineage"] = {"extension_hash": eh, "base_attested_hash": bah}
    aid = extension_artifact_id(kg_id, extension_id)
    env = _store.sign(EXTENSION_KIND, aid, vhash, verdict=vd, signer_uuid=signer_uuid, db_path=db_path)
    return {"signed": bool(env.get("signed")), "kind": EXTENSION_KIND, "artifact_id": aid,
            "version_hash": vhash,
            "validation": {"status": verdict.status, "signable": True, "note": verdict.note},
            "fingerprint": env.get("key_fingerprint")}


# ── portability (claims travel with the graph; reinstate AFTER the base) ──────

def capture_claims(kg_id: str, entities: List[Dict], db_path: Optional[str] = None) -> Dict[str, Dict]:
    """`{extension_id: claim}` for every SIGNED extension on a graph — embedded beside the
    base's `trust` claim wherever a graph is published or exported."""
    from . import portability as _port
    out: Dict[str, Dict] = {}
    for ext in extensions_on_graph(entities):
        c = _port.capture(EXTENSION_KIND, extension_artifact_id(kg_id, ext), db_path=db_path)
        if c:
            out[ext] = c
    return out


def reinstate_claims(new_kg_id: str, claims: Optional[Dict[str, Dict]],
                     db_path: Optional[str] = None) -> Dict[str, bool]:
    """Re-establish captured extension claims on a copy — iff each copy's live hash equals
    what was signed (the honesty gate). The live hash binds the BASE's attested hash, so
    this must run AFTER the base's own claim was reinstated: on a copy whose base could
    not be reinstated no extension reinstates either, which is the bottom-up rule
    holding across the remap. A claim that does not take is REVOKED on the copy (a stale
    row from an earlier sync must not read signed)."""
    from . import portability as _port
    out: Dict[str, bool] = {}
    for ext, claim in (claims or {}).items():
        aid = extension_artifact_id(new_kg_id, ext)
        ok = _port.reinstate(EXTENSION_KIND, aid, claim, live_hash(aid, db_path=db_path), db_path=db_path)
        if not ok:
            try:
                _store.revoke(EXTENSION_KIND, aid, db_path=db_path)
            except Exception:  # noqa: BLE001
                pass
        out[ext] = bool(ok)
    return out


def portable_authored(entities: List[Dict], relationships: List[Dict], exporter_uuid: Optional[str]
                      ) -> Tuple[List[Dict], List[Dict]]:
    """What of the authored layer TRAVELS: the PUBLISHED nodes of every extension (+ the
    decisions related to them), their properties with an unstamped node stamped with the
    EXPORTER's extension id — on the consumer it is the publisher's layer, kept apart from
    the consumer's own. Drafts stay home. Pure; returns copies."""
    T = _types()
    pub = [dict(e, properties=dict(e.get("properties") or {}))
           for e in (entities or []) if T.is_published_authored(e.get("properties"))]
    keys = {_name_key(e) for e in pub}
    decisions = []
    for r in relationships or []:
        if r.get("relationship_type") != "relates_to" or r.get("source_type") != "design_decision":
            continue
        if (str(r.get("target_name", "")), str(r.get("target_type", ""))) in keys:
            decisions.append(str(r.get("source_name", "")))
    for e in entities or []:
        pr = e.get("properties") or {}
        if e.get("entity_type") == "design_decision" and T.is_authored(pr) and str(e.get("name")) in decisions:
            pub.append(dict(e, properties=dict(pr)))
            keys.add(_name_key(e))
    for e in pub:
        if not e["properties"].get(T.EXTENSION_ID_PROP):
            e["properties"][T.EXTENSION_ID_PROP] = T.extension_id_for(exporter_uuid)
    rels = [dict(r) for r in (relationships or [])
            if (str(r.get("source_name", "")), str(r.get("source_type", ""))) in keys]
    return pub, rels
