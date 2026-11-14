"""
governance_lifecycle — the pure, storage-agnostic governance state machine shared
by governed products (Tier 4.3 ontologies now; Tier 3.2 agent packs later).

It answers one question deterministically: *given the current state, a requested
action, the governance policy, the actor, and (for publish) the conformance level —
is this transition allowed, and what is the resulting state?* No I/O, no LLM. The
artifact-specific glue persists the resulting state and writes the audit row.

Lifecycle:  draft ──propose──► (pending if approval required) ──approve──► [approved]
                  └──────────────── publish (conformance + policy gate) ◄───┘
                                          ▼
                                     published ──deprecate──► deprecated

Separation of duties: when approval is required, the approver must be in an approver
tier AND must not be the owner. Default policy (no approval required, don't block
non-conformant) reproduces today's "owner publishes directly" behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional


class LifecycleStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ApprovalStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class LifecycleAction(str, Enum):
    PROPOSE = "propose"
    APPROVE = "approve"
    REJECT = "reject"
    PUBLISH = "publish"
    DEPRECATE = "deprecate"


# Conformance levels (mirror kgstore.conformance) — the publish gate consumes these.
CONFORMANT = "conformant"
PARTIAL = "partial"
NONCONFORMANT = "nonconformant"


@dataclass(frozen=True)
class GovernancePolicy:
    """Admin-set platform policy (default = today's behaviour: no approval, no block)."""
    require_approval: bool = False
    approver_tiers: FrozenSet[str] = frozenset({"admin"})
    block_nonconformant_publish: bool = True


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    next_status: Optional[LifecycleStatus] = None
    next_approval: Optional[ApprovalStatus] = None
    badge: Optional[str] = None          # e.g. "partial_conformance" stamped on publish


@dataclass
class AuditRecord:
    """The shape the glue persists for every attempted transition (allowed or not)."""
    action: str
    actor_tier: str
    is_owner: bool
    from_status: str
    to_status: Optional[str]
    from_approval: str
    to_approval: Optional[str]
    conformance: Optional[str]
    allowed: bool
    reason: str
    badge: Optional[str] = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def validate_transition(
    action: LifecycleAction,
    *,
    current_status: LifecycleStatus,
    approval_status: ApprovalStatus = ApprovalStatus.NONE,
    policy: GovernancePolicy = GovernancePolicy(),
    actor_tier: str = "admin",
    is_owner: bool = True,
    conformance: Optional[str] = None,
    override_nonconformant: bool = False,
) -> Decision:
    """Pure transition decision. Returns Decision(allowed, reason, next_status, next_approval, badge)."""
    action = LifecycleAction(action)
    current_status = LifecycleStatus(current_status)
    approval_status = ApprovalStatus(approval_status)

    if action is LifecycleAction.PROPOSE:
        return _propose(current_status, approval_status, policy, is_owner)
    if action is LifecycleAction.APPROVE:
        return _approve(approval_status, policy, actor_tier, is_owner)
    if action is LifecycleAction.REJECT:
        return _reject(approval_status, policy, actor_tier, is_owner)
    if action is LifecycleAction.PUBLISH:
        return _publish(current_status, approval_status, policy, conformance, override_nonconformant)
    if action is LifecycleAction.DEPRECATE:
        return _deprecate(current_status, is_owner)
    return Decision(False, f"unknown action '{action}'")


def _propose(status, approval, policy, is_owner) -> Decision:
    if status is not LifecycleStatus.DRAFT:
        return Decision(False, f"can only propose from draft (current: {status.value})")
    if approval is ApprovalStatus.PENDING:
        return Decision(False, "already pending approval")
    if policy.require_approval:
        return Decision(True, "submitted for approval", LifecycleStatus.DRAFT, ApprovalStatus.PENDING)
    # No approval policy → auto-approved, ready to publish.
    return Decision(True, "no approval required — ready to publish",
                    LifecycleStatus.DRAFT, ApprovalStatus.APPROVED)


def _approve(approval, policy, actor_tier, is_owner) -> Decision:
    if approval is not ApprovalStatus.PENDING:
        return Decision(False, f"nothing to approve (approval status: {approval.value})")
    if actor_tier not in policy.approver_tiers:
        return Decision(False, f"tier '{actor_tier}' is not an approver")
    if is_owner:
        return Decision(False, "separation of duties: the owner cannot approve their own ontology")
    return Decision(True, "approved", None, ApprovalStatus.APPROVED)


def _reject(approval, policy, actor_tier, is_owner) -> Decision:
    if approval is not ApprovalStatus.PENDING:
        return Decision(False, f"nothing to reject (approval status: {approval.value})")
    if actor_tier not in policy.approver_tiers:
        return Decision(False, f"tier '{actor_tier}' is not an approver")
    if is_owner:
        return Decision(False, "separation of duties: the owner cannot reject their own ontology")
    return Decision(True, "rejected", None, ApprovalStatus.REJECTED)


def _publish(status, approval, policy, conformance, override) -> Decision:
    if status not in (LifecycleStatus.DRAFT, LifecycleStatus.PUBLISHED):
        return Decision(False, f"can only publish from draft or re-publish (current: {status.value})")
    # Gate 1 — approval.
    if policy.require_approval and approval is not ApprovalStatus.APPROVED:
        return Decision(False, "approval required before publishing")
    # Gate 2 — conformance (the central quality gate).
    badge = None
    if conformance == NONCONFORMANT:
        if policy.block_nonconformant_publish and not override:
            return Decision(False, "blocked: ontology is non-conformant to the canonical topology")
        badge = "nonconformant_override"
    elif conformance == PARTIAL:
        badge = "partial_conformance"
    # publish consumes the approval; subsequent edits re-enter the draft/propose cycle.
    return Decision(True, "published", LifecycleStatus.PUBLISHED, ApprovalStatus.NONE, badge)


def _deprecate(status, is_owner) -> Decision:
    if status is not LifecycleStatus.PUBLISHED:
        return Decision(False, f"can only deprecate a published ontology (current: {status.value})")
    return Decision(True, "deprecated", LifecycleStatus.DEPRECATED, None)


def build_audit(
    action: LifecycleAction,
    decision: Decision,
    *,
    actor_tier: str,
    is_owner: bool,
    from_status: LifecycleStatus,
    from_approval: ApprovalStatus,
    conformance: Optional[str] = None,
) -> AuditRecord:
    """Construct the audit record for a transition attempt (the glue stamps the timestamp)."""
    return AuditRecord(
        action=LifecycleAction(action).value,
        actor_tier=actor_tier,
        is_owner=is_owner,
        from_status=LifecycleStatus(from_status).value,
        to_status=decision.next_status.value if decision.next_status else None,
        from_approval=ApprovalStatus(from_approval).value,
        to_approval=decision.next_approval.value if decision.next_approval else None,
        conformance=conformance,
        allowed=decision.allowed,
        reason=decision.reason,
        badge=decision.badge,
    )
