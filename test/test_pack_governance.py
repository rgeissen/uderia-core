"""
Module tests — Agent-pack governance lifecycle (Tier 3.2 / P7).

CI-pure over a temp DB (no network). The pure state-machine is covered by
test_governance_lifecycle.py; this asserts the pack ORCHESTRATION:
  * lifecycle state persists (draft → published → deprecated),
  * the TRUST publish gate (unsigned pack → nonconformant; block-untrusted policy denies),
  * separation of duties on approval, audit rows, SLA tier, baseline (default policy = free).

Run:  python test/test_pack_governance.py
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.core import sqlite_util  # noqa: E402
from trusted_data_agent.core import pack_governance as PG  # noqa: E402
from trusted_data_agent.core.governance_lifecycle import GovernancePolicy  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite_util.connect(path)
    conn.execute(
        "CREATE TABLE agent_pack_installations "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner_user_id TEXT, manifest_json TEXT)")
    conn.execute("INSERT INTO agent_pack_installations (name, owner_user_id, manifest_json) "
                 "VALUES ('Test Pack', 'owner-1', '{}')")
    conn.commit()
    conn.close()
    return path


def _t(path, iid, action, **kw):
    return asyncio.run(PG.transition_pack(iid, action, db_path=path, **kw))


def main():
    print("=" * 70)
    print("Agent-pack governance (Tier 3.2 / P7) — module tests")
    print("=" * 70)
    path = _fresh_db()
    try:
        # initial state
        st = PG.get_pack_lifecycle(1, db_path=path)
        check("initial lifecycle = draft", st.get("lifecycle_status") == "draft")
        check("initial approval = none", st.get("approval_status") == "none")
        check("unsigned pack → trust_gate nonconformant", st.get("trust_gate") == "nonconformant")
        check("missing pack → {}", PG.get_pack_lifecycle(999, db_path=path) == {})

        # baseline: default policy publishes freely even though the pack is untrusted
        free = GovernancePolicy(require_approval=False, approver_tiers=frozenset({"admin"}),
                                block_nonconformant_publish=False)
        r = _t(path, 1, "publish", user_uuid="owner-1", is_owner=True, policy=free)
        check("default policy: untrusted pack publishes (baseline)", r["allowed"] and r["status"] == "published")
        check("publish persisted", PG.get_pack_lifecycle(1, db_path=path)["lifecycle_status"] == "published")

        # deprecate → deprecated + archive result present
        r = _t(path, 1, "deprecate", user_uuid="owner-1", is_owner=True)
        check("deprecate allowed", r["allowed"] and r["status"] == "deprecated")
        check("deprecate reports sessions_archived", "sessions_archived" in r)
        check("deprecate persisted", PG.get_pack_lifecycle(1, db_path=path)["lifecycle_status"] == "deprecated")

        # ── the TRUST publish gate ──
        path2 = _fresh_db()
        block = GovernancePolicy(require_approval=False, approver_tiers=frozenset({"admin"}),
                                 block_nonconformant_publish=True)
        r = _t(path2, 1, "publish", user_uuid="owner-1", is_owner=True, policy=block)
        check("block-untrusted policy DENIES an unsigned pack publish", not r["allowed"])
        check("denied publish stays draft", PG.get_pack_lifecycle(1, db_path=path2)["lifecycle_status"] == "draft")

        # ── separation of duties (require_approval) ──
        path3 = _fresh_db()
        appr = GovernancePolicy(require_approval=True, approver_tiers=frozenset({"admin"}),
                                block_nonconformant_publish=False)
        r = _t(path3, 1, "propose", user_uuid="owner-1", is_owner=True, policy=appr)
        check("propose under approval → pending", r["allowed"] and r["approval_status"] == "pending")
        # a non-approver (owner, developer tier) cannot approve
        r = _t(path3, 1, "approve", user_uuid="owner-1", is_owner=True, actor_tier="developer", policy=appr)
        check("non-approver tier cannot approve", not r["allowed"])
        # an admin approver (not the owner — separation of duties) can
        r = _t(path3, 1, "approve", user_uuid="admin-9", is_owner=False, actor_tier="admin", policy=appr)
        check("admin approver approves", r["allowed"] and r["approval_status"] == "approved")

        # ── SLA tier ──
        _t(path3, 1, "publish", user_uuid="owner-1", is_owner=True, policy=appr, sla_tier="gold")
        check("SLA tier persisted", PG.get_pack_lifecycle(1, db_path=path3)["sla_tier"] == "gold")

        # ── audit trail ──
        audit = PG.get_pack_audit(1, db_path=path3)
        check("audit rows recorded", len(audit) >= 3)
        check("audit captures a denied attempt", any(a["allowed"] == 0 for a in audit))

        # ── settings default reproduces baseline ──
        pol = PG.get_pack_governance_policy(db_path=path)
        check("default policy: no approval required", pol.require_approval is False)
        check("default policy: block-untrusted OFF (baseline)", pol.block_nonconformant_publish is False)
    finally:
        for p in (path,):
            try:
                os.remove(p)
            except Exception:
                pass

    print("\n" + "=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
