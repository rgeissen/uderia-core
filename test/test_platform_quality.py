#!/usr/bin/env python3
"""The Quality Analyst — evaluation, assurance and session reads described for agents, and the
containment that keeps an agent on the caller's own data whatever the caller's tier.

  §A  the descriptors — reads; scoring a turn again is a write (with judges it spends tokens);
      a policy backtest is a read (it replays, changes nothing); acknowledging, deciding,
      withdrawing and resetting are a person's acts
  §B  what is NOT on offer — every argument that widens to other users (`scope`, `user`,
      `all_users`): refused by bind
  §C  containment — a request an agent makes (a token bound to one operation) never carries a
      cross-user feature: an administrator's agent asking for another user's conversation by id
      gets not-found, where the administrator's own session reaches it
  §D  the instance-wide evaluation store — its datasets, cases, mining and gate now require the
      same feature as its runs (four routes had none); session details require a user
  §E  the role and the Guide

No LLM calls, no writes. Run: python test/test_platform_quality.py
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-quality-test")
logging.disable(logging.CRITICAL)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.core import platform_roles as R  # noqa: E402

app = create_app()
cat = build_catalog(app)
by = cat.by_op

# ── §A descriptors ──────────────────────────────────────────────────────────────
print("§A descriptors")
EFFECTS = {
    "sessions.list": "read", "sessions.details": "read", "sessions.analytics": "read", "sessions.context_analytics": "read",
    "evals.online": "read", "evals.online_dashboard": "read", "evals.kpi_dashboard": "read", "evals.trust_behavior": "read",
    "evals.library_outcomes": "read", "evals.agent_timeline": "read", "evals.agent_runs": "read", "evals.turn_score": "read",
    "evals.scoring_thresholds": "read", "evals.backtest": "read", "evals.score_turn": "write",
    "assurance.vocabulary": "read", "assurance.inbox": "read", "assurance.inbox_count": "read", "assurance.reviews": "read",
    "assurance.trust_history": "read", "assurance.agent_story": "read", "assurance.trust_trip": "read",
    "assurance.inbox_ack": "human_only", "assurance.inbox_ack_derived": "human_only", "assurance.review_decide": "human_only",
    "assurance.review_withdraw": "human_only", "assurance.trust_trip_reset": "human_only",
}
for op_id, eff in EFFECTS.items():
    check(f"{op_id} is described as {eff}", op_id in by and by[op_id].effect == eff, by.get(op_id) and by[op_id].effect)
check("scoring a turn again follows the agent's write mode (it spends, it changes nothing)",
      P.always_reviewed(by["evals.score_turn"]) is None and "spends" in by["evals.score_turn"].notes)
check("every quality operation is the caller's own (or the deployment's vocabulary)",
      all(by[o].scope in ("own", "instance") for o in EFFECTS))
check("the offline evaluation store is not described for agents (instance-wide, other users' turns)",
      not any(o in by for o in ("evals.runs", "evals.datasets", "evals.annotations", "evals.diff", "evals.gate")))

# ── §B not on offer ─────────────────────────────────────────────────────────────
print("§B not on offer")
for op_id in EFFECTS:
    o = by[op_id]
    accepted = set(o.path_params) | set(o.query) | set(o.body)
    check(f"{op_id} offers no argument that reaches other users", not (accepted & {"scope", "user", "all_users"}),
          sorted(accepted & {"scope", "user", "all_users"}))
placed, err = P.bind(by["sessions.list"], {"all_users": True})
check("bind refuses sessions.list(all_users=…)", placed is None and "does not take" in (err or ""), err)
placed, err = P.bind(by["evals.kpi_dashboard"], {"scope": "system"})
check("bind refuses evals.kpi_dashboard(scope=system)", placed is None and "does not take" in (err or ""), err)

# ── §C containment ──────────────────────────────────────────────────────────────
print("§C containment")
from trusted_data_agent.auth import features as F  # noqa: E402
admin = SimpleNamespace(id="a", profile_tier="admin", tier="admin", is_admin=True)
agent_admin = SimpleNamespace(id="a", profile_tier="admin", tier="admin", is_admin=True, agent_operation="sessions.details")
check("a person who is an administrator keeps every cross-user feature",
      all(F.user_has_feature(admin, f) for f in F.CROSS_USER_FEATURES) and F.CROSS_USER_FEATURES <= F.get_user_features(admin))
check("the same administrator acting through an agent holds none of them",
      not any(F.user_has_feature(agent_admin, f) for f in F.CROSS_USER_FEATURES)
      and not (F.CROSS_USER_FEATURES & F.get_user_features(agent_admin)))
check("…and keeps every other feature of the tier",
      (F.get_user_features(admin) - F.CROSS_USER_FEATURES) == F.get_user_features(agent_admin))
from trusted_data_agent.auth import middleware as MW  # noqa: E402
msrc = inspect.getsource(MW.get_current_user)
check("the middleware marks a user authenticated by an operation token",
      "user.agent_operation =" in msrc and "payload.get('op_scope') is not None" in msrc)

# Behavioural, through the real route: an administrator's agent cannot read another user's
# conversation by id. The session store is replaced by a fixture (one conversation of another
# user, attributable by the index), so the check never depends on what this instance holds.
from trusted_data_agent.auth.database import get_db_session  # noqa: E402
from trusted_data_agent.auth.models import User  # noqa: E402
from trusted_data_agent.core import session_manager as _SM  # noqa: E402
from trusted_data_agent.auth.security import create_internal_token  # noqa: E402
with get_db_session() as _s:
    _admin = next((u.id for u in _s.query(User).filter(User.is_active.is_(True)).all()
                   if (getattr(u, "profile_tier", "") or "").lower() == "admin"), None)
check("an active administrator exists for the containment check", _admin is not None)
if _admin:
    OTHER, SID, MINE = "someone-else", "sess-of-someone-else", "sess-of-the-admin"
    _store = {(OTHER, SID): {"id": SID, "user_uuid": OTHER, "name": "theirs"},
              (_admin, MINE): {"id": MINE, "user_uuid": _admin, "name": "mine"}}
    _real_read, _real_owner = _SM.read_session, _SM.find_session_owner

    async def _read(user_uuid, session_id):
        return dict(_store[(user_uuid, session_id)]) if (user_uuid, session_id) in _store else None

    async def _owner(session_id):
        return next((u for (u, s) in _store if s == session_id), None)
    _SM.read_session, _SM.find_session_owner = _read, _owner
    try:
        async def _as_person(sid):
            r = await app.test_client().get(f"/api/v1/sessions/{sid}/details",
                                            headers={"Authorization": f"Bearer {create_internal_token(_admin)}"})
            return r.status_code
        check("precondition: the administrator in person reaches the other user's conversation (the route widens)",
              asyncio.run(_as_person(SID)) == 200)
        _qa = [R.registry().roles["quality-analyst"]]
        r = asyncio.run(P.call("sessions.details", {"session_id": SID}, _admin, roles=_qa))
        check("…the same administrator's agent gets not-found — no cross-user widening through an agent",
              r.get("ok") is False and r.get("status") == 404, {k: r.get(k) for k in ("ok", "status", "error")})
        r2 = asyncio.run(P.call("sessions.details", {"session_id": MINE}, _admin, fields=["name"], roles=_qa))
        check("…while its own conversation is read", r2.get("ok") is True and "mine" in str(r2.get("result")),
              {k: r2.get(k) for k in ("ok", "status", "error")})
    finally:
        _SM.read_session, _SM.find_session_owner = _real_read, _real_owner

# ── §D the evaluation store and session details ─────────────────────────────────
print("§D gates")
from trusted_data_agent.api import rest_routes as RR  # noqa: E402
for fn in ("evals_datasets", "evals_cases", "evals_mine", "evals_gate", "evals_runs" if hasattr(RR, "evals_runs") else "evals_datasets"):
    src = inspect.getsource(getattr(RR, fn))
    check(f"{fn} requires the evaluation-store feature before reading or writing the store",
          "_eval_store_gate(current_user)" in src
          and src.index("_eval_store_gate(current_user)") < src.index("get_eval_store()"))
dsrc = inspect.getsource(RR.get_session_details)
check("session details require a user before the owner fallback can run",
      "if not user_uuid:" in dsrc and dsrc.index("if not user_uuid:") < dsrc.index("find_session_owner"))

# ── §E the role and the Guide ───────────────────────────────────────────────────
print("§E the role")
reg = R.registry()
qa, guide = reg.roles["quality-analyst"], reg.roles["guide"]
check("the Quality Analyst is active", qa.active and set(qa.effects) == {"read", "write"})
for op_id, eff in EFFECTS.items():
    listed, why = R.role_state([qa], op_id, eff)
    check(f"the Quality Analyst lists {op_id}", listed, why)
qa_writes = sorted(o.op for o in cat.operations if o.effect == "write" and qa.lists(o.op))
check("its only change is scoring a turn again — it composes, curates and automates nothing",
      qa_writes == ["evals.score_turn"], qa_writes)
for op_id in ("sessions.list", "assurance.inbox", "assurance.reviews", "evals.kpi_dashboard", "evals.agent_timeline"):
    check(f"the Guide reads {op_id}", R.role_state([guide], op_id, "read") == (True, None))
check("the Guide still changes nothing", guide.effects == ("read",))
check("the Guide is taught the inbox playbook, not the rescoring one",
      guide.teaches("platform-playbooks", "quality/inbox") and not guide.teaches("platform-playbooks", "quality/rescore"))
found = [f["op"] for f in P.find("which checks fail most across my agents", "user", cat=cat, roles=[qa])["found"]]
check("find, scoped to the Quality Analyst, finds the scoreboard among its first two", "evals.kpi_dashboard" in found[:2], found)

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
