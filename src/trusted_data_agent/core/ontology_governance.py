"""
ontology_governance — the glue that turns the pure lifecycle primitive
(`core/governance_lifecycle.py`) + the pure conformance validator
(`kgstore/conformance.py`) into a persisted, observable governance action over a
real KG (Tier 4.3).

Per transition it: reads the current lifecycle state, runs conformance (for
publish), asks the pure state machine to decide, persists the new state + pins a
version (publish) + writes the audit row, and emits the decision through the eval
observability substrate (an OTLP-shaped governance span; a signed Ed25519
attestation on publish). The decision logic stays in the pure primitives — this
module only does I/O + emission.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trusted_data_agent.core.governance_lifecycle import (
    ApprovalStatus,
    GovernancePolicy,
    LifecycleAction,
    LifecycleStatus,
    build_audit,
    validate_transition,
)
from trusted_data_agent.kgstore.conformance import check_conformance, ConformanceReport

logger = logging.getLogger("quart.app")


async def run_conformance(store) -> ConformanceReport:
    """Read the KG and validate it against the canonical ontology topology."""
    entities, relationships = await store.export_graph()  # complete, count-derived
    return check_conformance(entities, relationships)


async def run_transition(
    store,
    action,
    *,
    user_uuid: str,
    actor_tier: str = "admin",
    is_owner: bool = True,
    policy: Optional[GovernancePolicy] = None,
    semantic_version: Optional[str] = None,
    version_id: Optional[str] = None,
    override_nonconformant: bool = False,
) -> Dict[str, Any]:
    """Execute a lifecycle transition. Returns a result dict (always — denied
    transitions are audited + reported, never raised)."""
    action = LifecycleAction(action)
    if policy is None:
        from trusted_data_agent.kgstore import settings as kg_settings
        policy = kg_settings.get_ontology_governance_policy()

    state = store.get_lifecycle_state()
    cur_status = LifecycleStatus(state.get("lifecycle_status") or "draft")
    cur_approval = ApprovalStatus(state.get("approval_status") or "none")

    # Conformance is evaluated only when it gates (publish). The graph read here is
    # reused to pin the published snapshot — no second read.
    report: Optional[ConformanceReport] = None
    conformance: Optional[str] = None
    pub_entities = pub_relationships = None
    if action is LifecycleAction.PUBLISH:
        pub_entities, pub_relationships = await store.export_graph()  # complete, count-derived
        # The base's gate judges the BASE (PROP-008 A5 / §4.6): an extension is tier 1 with
        # its own gate (`core/trust/extensions`), so a draft someone authored on top of
        # the model must not make the model read partial. The snapshot pinned below keeps
        # everything the graph held — the released view removes the drafts itself.
        report = check_conformance(*_base_only(pub_entities, pub_relationships))
        conformance = report.status

    decision = validate_transition(
        action,
        current_status=cur_status,
        approval_status=cur_approval,
        policy=policy,
        actor_tier=actor_tier,
        is_owner=is_owner,
        conformance=conformance,
        override_nonconformant=override_nonconformant,
    )

    # Audit every attempt (allowed or denied).
    audit = build_audit(action, decision, actor_tier=actor_tier, is_owner=is_owner,
                        from_status=cur_status, from_approval=cur_approval, conformance=conformance)
    try:
        store.append_lifecycle_audit({**audit.to_dict(), "actor": user_uuid,
                                      "subject": ({"version": semantic_version} if semantic_version
                                                  and action is LifecycleAction.PUBLISH else None)})
    except Exception as e:
        logger.debug(f"ontology_governance: audit append failed: {e}")

    result: Dict[str, Any] = {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "action": action.value,
        "badge": decision.badge,
        "status": cur_status.value,
        "approval_status": cur_approval.value,
        "conformance": report.to_dict() if report else None,
    }

    attestation = None
    if decision.allowed:
        now = datetime.now(timezone.utc).isoformat()
        if action is LifecycleAction.PUBLISH:
            version_id = _pin_version(store, user_uuid, version_id, semantic_version,
                                      pub_entities or [], pub_relationships or [])
            store.set_lifecycle_state(
                lifecycle_status=LifecycleStatus.PUBLISHED.value,
                published_version_id=version_id,
                semantic_version=semantic_version,
                approval_status=ApprovalStatus.NONE.value,
                conformance_status=conformance,
                conformance_checked_at=now,
            )
            attestation = _attest_publish(store.kg_id, version_id, conformance, semantic_version)
            # Model B: the released view is materialised eagerly so a released-mode agent's
            # next turn reads it without paying the build. Fail-soft — the lazy path at
            # read time covers a failure here.
            try:
                from trusted_data_agent.core import kg_release as _rel
                _rel.ensure_released_shadow(store.kg_id, user_uuid)
            except Exception as _mx:  # noqa: BLE001
                logger.warning(f"ontology_governance: released view not materialised: {_mx}")
            result.update(status=LifecycleStatus.PUBLISHED.value, approval_status="none",
                          published_version_id=version_id, semantic_version=semantic_version,
                          attestation=attestation)
        elif action is LifecycleAction.DEPRECATE:
            store.set_lifecycle_state(lifecycle_status=LifecycleStatus.DEPRECATED.value, deprecated_at=now)
            result.update(status=LifecycleStatus.DEPRECATED.value)
        else:  # propose / approve / reject — approval/status updates only
            updates: Dict[str, Any] = {}
            if decision.next_approval is not None:
                updates["approval_status"] = decision.next_approval.value
                result["approval_status"] = decision.next_approval.value
            if decision.next_status is not None:
                updates["lifecycle_status"] = decision.next_status.value
                result["status"] = decision.next_status.value
            if updates:
                store.set_lifecycle_state(**updates)

    _emit_span(store.kg_id, action, decision, cur_status, conformance, actor_tier,
               user_uuid, result, attestation)
    return result


def _base_only(entities, relationships):
    """The walked layers of a graph: authored nodes and every edge touching them removed."""
    from trusted_data_agent.kgstore.types import is_extension_layer as _ext_layer
    auth = {(str(e.get("name")), str(e.get("entity_type"))) for e in (entities or [])
            if _ext_layer(e.get("properties"))}
    if not auth:
        return entities, relationships
    ents = [e for e in entities if (str(e.get("name")), str(e.get("entity_type"))) not in auth]
    rels = [r for r in (relationships or [])
            if (str(r.get("source_name")), str(r.get("source_type"))) not in auth
            and (str(r.get("target_name")), str(r.get("target_type"))) not in auth]
    return ents, rels


def _pin_version(store, user_uuid: str, version_id: Optional[str],
                 semantic_version: Optional[str], entities, relationships) -> Optional[str]:
    """Pin the published version: use the caller's version_id, else snapshot the
    already-read graph (falling back to the latest snapshot on dedup)."""
    if version_id:
        return version_id
    try:
        from trusted_data_agent.core import kg_version_db
        snap = kg_version_db.snapshot_kg(store.kg_id, user_uuid, entities, relationships,
                                         label=semantic_version)
        if snap:
            return snap["version_id"]
        versions = kg_version_db.list_versions(store.kg_id, user_uuid)
        return versions[-1]["version_id"] if versions else None
    except Exception as e:
        logger.debug(f"ontology_governance: version pin failed: {e}")
        return None


def _attest_publish(kg_id: str, version_id: Optional[str], conformance: Optional[str],
                    semantic_version: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        from trusted_data_agent.eval import online
        payload = f"{kg_id}|{version_id or ''}|{conformance or ''}|{semantic_version or ''}|publish"
        return online.sign_payload(payload)
    except Exception as e:
        logger.debug(f"ontology_governance: attestation failed: {e}")
        return None


def _emit_span(kg_id, action, decision, from_status, conformance, actor_tier,
               user_uuid, result, attestation) -> None:
    try:
        from trusted_data_agent.eval import online
        online.emit_governance_span({
            "name": "ontology.governance",
            "ontology.kg_id": kg_id,
            "governance.action": action.value,
            "governance.from_status": from_status.value,
            "governance.to_status": result.get("status"),
            "governance.allowed": decision.allowed,
            "governance.reason": decision.reason,
            "governance.badge": decision.badge,
            "governance.conformance": conformance,
            "governance.semantic_version": result.get("semantic_version"),
            "governance.published_version_id": result.get("published_version_id"),
            "actor.tier": actor_tier,
            "user.uuid": user_uuid,
            "attestation": attestation,
        })
    except Exception as e:
        logger.debug(f"ontology_governance: span emit failed: {e}")


# ── Extensions (PROP-008 A5 / §4.6, §13) ───────────────────────────────────────

async def publish_extension(store, extension_id: str, *, actor: str, actor_tier: str,
                            user_uuid: str, policy: Optional[GovernancePolicy] = None,
                            names: Optional[list] = None,
                            override_nonconformant: bool = False) -> Dict[str, Any]:
    """Publish an extension's unpublished nodes — the lifecycle act on tier 1.

    The decision is PURE (`core/trust/extensions.publish_decision`): an `agent:` actor is
    refused whatever the policy says, the person must hold an approver tier, and the
    Tier 4.3 conformance gate judges the composition base + extension. Allowed ⇒ every
    unpublished node of the extension (or the `names` given) is stamped
    `status: published` + `published_at` + `published_by` through GraphStore (the ONE
    write seam), an audit row is appended, and the released view is rebuilt so a
    released-mode agent reads the extension from its next turn. A refusal changes nothing.
    """
    try:
        from trusted_data_agent.core.trust import extensions as _ext
    except Exception as exc:  # noqa: BLE001 — the trust suite is removable
        return {"allowed": False, "code": "trust_suite_absent",
                "reason": f"publishing an extension needs the trust suite, which is not present ({exc})"}
    if policy is None:
        from trusted_data_agent.kgstore import settings as kg_settings
        policy = kg_settings.get_ontology_governance_policy()
    entities, relationships = await store.export_graph()
    counts = _ext.extensions_on_graph(entities).get(extension_id) or {"published": 0, "unpublished": 0}
    if not (counts["published"] or counts["unpublished"]):
        return {"allowed": False, "code": "not_found",
                "reason": f"no node of extension '{extension_id}' exists on this graph"}
    mutations = _ext.base_mutations(entities, relationships, extension_id)
    if mutations:
        return {"allowed": False, "code": "base_mutation",
                "reason": "an extension only adds, it never modifies the base (§3a): " + "; ".join(mutations[:3]),
                "mutations": mutations}
    report = _ext.extension_conformance(entities, relationships, extension_id)
    decision = _ext.publish_decision(actor, actor_tier, policy, report.status, override_nonconformant)
    now = datetime.now(timezone.utc).isoformat()
    wanted = {str(n) for n in (names or [])}
    candidates = [e for e in _ext.extension_nodes(entities, extension_id, published_only=False)
                  if str((e.get("properties") or {}).get("status") or "draft") != "published"
                  and (not wanted or str(e.get("name")) in wanted)]
    from trusted_data_agent.kgstore import types as _T
    waiting = [str(e.get("name")) for e in candidates
               if _T.awaits_landing(e.get("entity_type"), (e.get("properties") or {}).get("status"))]
    if decision["allowed"] and waiting:
        decision = {**decision, "allowed": False, "code": "not_landed",
                    "reason": "land it first, then publish — not yet on the warehouse: " + ", ".join(waiting[:5]),
                    "not_landed": waiting}
    published: list = []
    if decision["allowed"]:
        for e in candidates:
            try:
                node = store.get_entity_by_name(e.get("name"), e.get("entity_type"))
                if not node:
                    continue
                props = dict(node.get("properties") or {})
                props.update({"status": "published", "published_at": now, "published_by": actor})
                res = store.update_entity(node["id"], properties=props)
                if hasattr(res, "__await__"):
                    res = await res
                published.append(str(e.get("name")))
            except Exception as exc:  # noqa: BLE001 — one node's failure is named, not hidden
                logger.warning(f"publish_extension: could not stamp {e.get('name')}: {exc}")
        if not candidates:
            decision = {**decision, "code": "nothing_to_publish",
                        "reason": "every node of this extension is already published"}
    try:
        # what the act covered, by node type ({"entity": 1, "attribute": 9}) — the trail says it in words
        _types: Dict[str, int] = {}
        for _e in candidates:
            if str(_e.get("name")) in published:
                _t = str(_e.get("entity_type") or "node")
                _types[_t] = _types.get(_t, 0) + 1
        store.append_lifecycle_audit({
            "action": "publish_extension", "actor_tier": actor_tier, "is_owner": True,
            "actor": actor, "subject": {"extension_id": extension_id, "nodes": _types} if _types else None,
            "from_status": "draft", "to_status": "published" if published else None,
            "from_approval": "none", "to_approval": None, "conformance": report.status,
            "allowed": bool(decision["allowed"]), "reason": f"{extension_id}: {decision['reason']}",
            "badge": decision.get("badge")})
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"publish_extension: audit append failed: {exc}")
    if published:
        try:
            from trusted_data_agent.core import kg_release as _rel
            _rel.ensure_released_shadow(store.kg_id, user_uuid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"publish_extension: released view not rebuilt: {exc}")
    return {**decision, "extension_id": extension_id, "published": published,
            "conformance": report.to_dict(), "counts": counts}
