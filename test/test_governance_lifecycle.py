"""
Module tests — core.governance_lifecycle (Tier 4.3, the governed-product state machine).

CI-pure: no I/O, no LLM. Asserts every legal transition, every illegal one rejected,
the approval gate (pending blocks publish, separation of duties), the conformance
gate (nonconformant blocks / partial badge / override), default-policy direct
publish (today's behaviour), and the audit-record shape.

Run:
    python test/test_governance_lifecycle.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.governance_lifecycle import (  # noqa: E402
    ApprovalStatus,
    GovernancePolicy,
    LifecycleAction,
    LifecycleStatus,
    build_audit,
    validate_transition,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


NO_APPROVAL = GovernancePolicy(require_approval=False)
APPROVAL = GovernancePolicy(require_approval=True, approver_tiers=frozenset({"admin"}))
NO_BLOCK = GovernancePolicy(require_approval=False, block_nonconformant_publish=False)


def T(action, **kw):
    return validate_transition(action, **kw)


def test_propose():
    print("\n[propose]")
    d = T(LifecycleAction.PROPOSE, current_status=LifecycleStatus.DRAFT, policy=NO_APPROVAL)
    check("no-approval policy → auto-approved, stays draft",
          d.allowed and d.next_approval == ApprovalStatus.APPROVED and d.next_status == LifecycleStatus.DRAFT)
    d = T(LifecycleAction.PROPOSE, current_status=LifecycleStatus.DRAFT, policy=APPROVAL)
    check("approval policy → pending", d.allowed and d.next_approval == ApprovalStatus.PENDING)
    d = T(LifecycleAction.PROPOSE, current_status=LifecycleStatus.PUBLISHED, policy=APPROVAL)
    check("propose from published → blocked", not d.allowed)
    d = T(LifecycleAction.PROPOSE, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL)
    check("propose when already pending → blocked", not d.allowed)


def test_approve_reject():
    print("\n[approve / reject — separation of duties]")
    d = T(LifecycleAction.APPROVE, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL, actor_tier="admin", is_owner=False)
    check("approver, not owner → approved", d.allowed and d.next_approval == ApprovalStatus.APPROVED)
    d = T(LifecycleAction.APPROVE, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL, actor_tier="admin", is_owner=True)
    check("owner approving own → blocked (separation of duties)", not d.allowed and "separation" in d.reason)
    d = T(LifecycleAction.APPROVE, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL, actor_tier="user", is_owner=False)
    check("non-approver tier → blocked", not d.allowed)
    d = T(LifecycleAction.APPROVE, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.NONE, policy=APPROVAL, actor_tier="admin", is_owner=False)
    check("approve when nothing pending → blocked", not d.allowed)
    d = T(LifecycleAction.REJECT, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL, actor_tier="admin", is_owner=False)
    check("reject pending by approver → rejected", d.allowed and d.next_approval == ApprovalStatus.REJECTED)
    d = T(LifecycleAction.REJECT, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL, actor_tier="admin", is_owner=True)
    check("owner rejecting own → blocked", not d.allowed)


def test_publish_approval_gate():
    print("\n[publish — approval gate]")
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.PENDING, policy=APPROVAL, conformance="conformant")
    check("approval required + not approved → blocked", not d.allowed and "approval" in d.reason)
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT,
          approval_status=ApprovalStatus.APPROVED, policy=APPROVAL, conformance="conformant")
    check("approval required + approved + conformant → published",
          d.allowed and d.next_status == LifecycleStatus.PUBLISHED and d.next_approval == ApprovalStatus.NONE)


def test_publish_conformance_gate():
    print("\n[publish — conformance gate (the central gate)]")
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, policy=NO_APPROVAL, conformance="conformant")
    check("conformant → published, no badge", d.allowed and d.badge is None)
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, policy=NO_APPROVAL, conformance="partial")
    check("partial → published with partial_conformance badge", d.allowed and d.badge == "partial_conformance")
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, policy=NO_APPROVAL, conformance="nonconformant")
    check("nonconformant + block policy → blocked", not d.allowed and "non-conformant" in d.reason)
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, policy=NO_APPROVAL,
          conformance="nonconformant", override_nonconformant=True)
    check("nonconformant + override → published with override badge",
          d.allowed and d.badge == "nonconformant_override")
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, policy=NO_BLOCK, conformance="nonconformant")
    check("nonconformant + block-policy-off → published", d.allowed and d.badge == "nonconformant_override")


def test_publish_states():
    print("\n[publish — valid source states]")
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.PUBLISHED, policy=NO_APPROVAL, conformance="conformant")
    check("re-publish from published → allowed", d.allowed)
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DEPRECATED, policy=NO_APPROVAL, conformance="conformant")
    check("publish from deprecated → blocked", not d.allowed)


def test_deprecate():
    print("\n[deprecate]")
    d = T(LifecycleAction.DEPRECATE, current_status=LifecycleStatus.PUBLISHED)
    check("deprecate published → deprecated", d.allowed and d.next_status == LifecycleStatus.DEPRECATED)
    d = T(LifecycleAction.DEPRECATE, current_status=LifecycleStatus.DRAFT)
    check("deprecate draft → blocked", not d.allowed)


def test_default_policy_direct_publish():
    print("\n[default policy = today's behaviour]")
    # Default GovernancePolicy(): no approval, block nonconformant. A conformant draft
    # publishes directly with no proposal/approval — byte-identical to today's flow.
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, conformance="conformant")
    check("default policy: conformant draft publishes directly", d.allowed and d.next_status == LifecycleStatus.PUBLISHED)
    check("default policy requires no approval", GovernancePolicy().require_approval is False)


def test_unknown_action():
    print("\n[unknown action]")
    try:
        d = validate_transition("frobnicate", current_status=LifecycleStatus.DRAFT)
        check("unknown action handled (blocked or raises)", not d.allowed)
    except ValueError:
        check("unknown action raises ValueError", True)


def test_audit_and_enums():
    print("\n[audit record + enum serialization]")
    d = T(LifecycleAction.PUBLISH, current_status=LifecycleStatus.DRAFT, policy=NO_APPROVAL, conformance="partial")
    a = build_audit(LifecycleAction.PUBLISH, d, actor_tier="admin", is_owner=True,
                    from_status=LifecycleStatus.DRAFT, from_approval=ApprovalStatus.APPROVED, conformance="partial")
    check("audit records action/from/to", a.action == "publish" and a.from_status == "draft" and a.to_status == "published")
    check("audit records conformance + badge", a.conformance == "partial" and a.badge == "partial_conformance")
    check("audit allowed flag set", a.allowed is True)
    ad = build_audit(LifecycleAction.DEPRECATE,
                     T(LifecycleAction.DEPRECATE, current_status=LifecycleStatus.DRAFT),
                     actor_tier="user", is_owner=True,
                     from_status=LifecycleStatus.DRAFT, from_approval=ApprovalStatus.NONE)
    check("denied transition still audited", ad.allowed is False and ad.to_status is None)
    check("audit.to_dict serializable", isinstance(a.to_dict(), dict) and a.to_dict()["action"] == "publish")
    check("enums serialize as plain strings", LifecycleStatus.PUBLISHED.value == "published" and ApprovalStatus.PENDING == "pending")


def main():
    print("=" * 70)
    print("core.governance_lifecycle — module tests")
    print("=" * 70)
    test_propose()
    test_approve_reject()
    test_publish_approval_gate()
    test_publish_conformance_gate()
    test_publish_states()
    test_deprecate()
    test_default_policy_direct_publish()
    test_unknown_action()
    test_audit_and_enums()
    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
