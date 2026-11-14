#!/usr/bin/env python3
"""The Admin Monitor — the deployment in aggregates, for an administrator's agent, every reading
recorded; and no agent reads other users' conversations because a deployment lets people do so.

  §A  the descriptors — instance-wide readings are administrator-tier reads; filling in prices is
      not offered (it writes)
  §B  recording — an agent's administrator-tier operation is written to the audit log, naming
      the agent, the operation and the conversation; an ordinary operation is not; a failure to
      record never breaks the call
  §C  the session filter — `sessions_filtered_by_user()` is the one rule every session read uses:
      the deployment's switch, OR a request an agent makes (the middleware marks it); with the
      switch off a person may read across users, an agent still may not
  §D  the role — read-only; its deployment readings are administrator-tier, so another user's
      agent reaches only the scheduler's and providers' status; per-user usage and unattributed
      signatures are listed, explained, never callable

No LLM calls, no writes. Run: python test/test_platform_admin_monitor.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-admin-monitor-test")
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
ADMIN_READS = ("admin.stats", "admin.db_health", "admin.platform_jobs", "costs.coverage", "costs.models",
               "costs.analytics", "consumption.system_summary")
for op_id in ADMIN_READS:
    o = by.get(op_id)
    check(f"{op_id} is an administrator-tier read", o is not None and o.effect == "read" and o.tier == "admin",
          o and (o.effect, o.tier))
check("filling in missing prices is not offered (it writes the price list)", "fill" not in by["costs.coverage"].query)
check("the user directory is not offered", not any(o.path == "/api/v1/admin/users" for o in cat.operations))
check("cost analytics is the caller's own (an agent never reads across users through it)", by["costs.analytics"].scope == "own")

# ── §B recording ────────────────────────────────────────────────────────────────
print("§B recording")
import trusted_data_agent.auth.audit as AUD  # noqa: E402
calls = []
_real = AUD.log_audit_event
AUD.log_audit_event = lambda *a, **k: calls.append((a, k)) or True
try:
    P.record_admin_use("u-admin", by["admin.stats"], 200, agent={"id": "p-mon", "tag": "MON"}, session_id="s-1")
    a, k = calls[-1] if calls else ((), {})
    check("an administrator-tier operation is recorded, naming the agent, the operation and the conversation",
          calls and a[0] == "u-admin" and k.get("action") == "platform_agent_admin_operation"
          and "@MON" in k.get("details", "") and "admin.stats" in k.get("details", "")
          and k.get("metadata", {}).get("session_id") == "s-1", (a, k))
    AUD.log_audit_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit store down"))
    try:
        P.record_admin_use("u-admin", by["admin.stats"], 200)
        check("a failure to record never breaks the call", True)
    except Exception as e:  # noqa: BLE001
        check("a failure to record never breaks the call", False, e)
finally:
    AUD.log_audit_event = _real
import inspect  # noqa: E402
csrc = inspect.getsource(P.call)
check("call records every administrator-tier operation an agent runs, and only those",
      'if op.tier == "admin":' in csrc and "record_admin_use(" in csrc)

# ── §C the session filter ───────────────────────────────────────────────────────
print("§C session filter")
from trusted_data_agent.core.config import APP_CONFIG, sessions_filtered_by_user  # noqa: E402
_orig = APP_CONFIG.SESSIONS_FILTER_BY_USER
try:
    APP_CONFIG.SESSIONS_FILTER_BY_USER = False

    async def _probe(mark):
        async with app.test_request_context("/api/v1/sessions"):
            from quart import g
            if mark:
                g.agent_operation = "sessions.list"
            return sessions_filtered_by_user()
    check("switch off: a person's request reads across users (the deployment's choice)", asyncio.run(_probe(False)) is False)
    check("switch off: an agent's request still reads its user's own", asyncio.run(_probe(True)) is True)
    check("outside a request the deployment's switch decides", sessions_filtered_by_user() is False)
    APP_CONFIG.SESSIONS_FILTER_BY_USER = True
    check("switch on: every request is filtered", asyncio.run(_probe(False)) is True)
finally:
    APP_CONFIG.SESSIONS_FILTER_BY_USER = _orig
import subprocess  # noqa: E402
_raw = subprocess.run(["git", "grep", "-n", "APP_CONFIG.SESSIONS_FILTER_BY_USER", "--", "src"],
                      capture_output=True, text=True, cwd=ROOT).stdout.splitlines()
_reads = [l for l in _raw if "core/config.py" not in l and "``APP_CONFIG" not in l and "#" not in l.split(":", 2)[-1]]
check("no session read consults the switch directly — every one goes through the one rule", not _reads, _reads)
from trusted_data_agent.auth import middleware as MW  # noqa: E402
check("the middleware marks an agent's request for the rule", "g.agent_operation = user.agent_operation" in inspect.getsource(MW.get_current_user))

# ── §D the role ─────────────────────────────────────────────────────────────────
print("§D the role")
reg = R.registry()
mon = reg.roles["admin-monitor"]
check("the Admin Monitor is active and read-only", mon.active and mon.effects == ("read",))
reach = [o for o in cat.operations if mon.lists(o.op) and o.effect != "never"]
check("it changes nothing", not any(o.effect == "write" for o in reach))
user_tier_reach = sorted(o.op for o in reach if o.tier == "user" and P.availability(o)[0])
check("for a non-administrator's agent it reaches only the scheduler's and providers' status",
      user_tier_reach == ["llm.provider_health", "scheduler.status"], user_tier_reach)
for op_id in ("consumption.users", "trust.unassigned"):
    check(f"{op_id} (other users) is listed, explained, never callable",
          mon.lists(op_id) and P.availability(by[op_id])[0] is False
          and any(op_id in (n.get("ops") or []) for n in mon.not_offered))
check("the Admin Monitor is taught only the administrators' playbooks",
      all(p.split(":")[1].startswith("admin/") for p in mon.playbooks))
check("every shipped role is active", all(r.active for r in reg.roles.values()))

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
