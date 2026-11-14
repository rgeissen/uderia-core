#!/usr/bin/env python3
"""The "all users" session list carries its scope with the call, never by flipping a global.

``GET /sessions?all_users=true`` (routes.py, VIEW_ALL_SESSIONS) set the process-wide
``APP_CONFIG.SESSIONS_FILTER_BY_USER = False`` around an ``await`` and restored it after.
Every concurrent request read that flag during the window — another user's session LIST
and the backends' single-session LOAD (file ``_find_path`` / postgres ``load`` search
every user's sessions by id when it is off). One privileged list call unscoped the whole
process for its duration.

Now ``session_manager.get_all_sessions(..., filter_by_user=…)`` takes the scope for THIS
call (None = the deployment setting), and nothing outside ``core/config.py`` writes the
flag.

  §G  no assignment to SESSIONS_FILTER_BY_USER outside core/config.py (all of src/)
  §R  the all_users route passes filter_by_user=False and touches no global
  §C  get_all_sessions hands the per-call scope to the index (and the scan fallback),
      leaves the global unchanged while it awaits, and defaults to the deployment setting

Run:  PYTHONPATH=src:. python test/test_session_scope_per_call.py
"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
os.chdir(ROOT)
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


# §G ─────────────────────────────────────────────────────────────────────────────────
writers = []
for dp, _, fs in os.walk(os.path.join(ROOT, "src")):
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        if p.endswith(os.path.join("core", "config.py")):
            continue
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            if re.search(r"SESSIONS_FILTER_BY_USER\s*=(?!=)", line):
                writers.append(f"{os.path.relpath(p, ROOT)}:{i}")
check("nothing outside core/config.py writes SESSIONS_FILTER_BY_USER", not writers, ", ".join(writers))

# §R ─────────────────────────────────────────────────────────────────────────────────
routes = open(os.path.join(ROOT, "src/trusted_data_agent/api/routes.py"), encoding="utf-8").read()
m = re.search(r"if all_users:\n([\s\S]*?)\n    else:", routes)
check("the all_users branch is found", m is not None)
if m:
    body = m.group(1)
    check("the all_users list passes filter_by_user=False", "filter_by_user=False" in body)
    check("the all_users list writes no global (no APP_CONFIG assignment)",
          re.search(r"APP_CONFIG\.\w+\s*=(?!=)", body) is None)
    check("the all_users list still requires VIEW_ALL_SESSIONS (403 otherwise)",
          "Feature.VIEW_ALL_SESSIONS not in user_features" in body and "403" in body)

# §C ─────────────────────────────────────────────────────────────────────────────────
from trusted_data_agent.core import session_manager as SM  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

seen = []


async def _fake_index(user_uuid, include_archived=False, filter_by_user=True):
    await asyncio.sleep(0)  # yield, as the real backend does
    seen.append({"filter": filter_by_user, "global_during": APP_CONFIG.SESSIONS_FILTER_BY_USER})
    return []


_real = SM._query_session_index
_orig_global = APP_CONFIG.SESSIONS_FILTER_BY_USER
try:
    APP_CONFIG.SESSIONS_FILTER_BY_USER = True
    SM._query_session_index = _fake_index
    asyncio.run(SM.get_all_sessions("u1", filter_by_user=False))
    check("an explicit filter_by_user=False reaches the index", seen and seen[-1]["filter"] is False, str(seen))
    check("the global stays on while the privileged call awaits",
          seen and seen[-1]["global_during"] is True and APP_CONFIG.SESSIONS_FILTER_BY_USER is True)
    asyncio.run(SM.get_all_sessions("u1"))
    check("no argument = the deployment setting", seen[-1]["filter"] is True)
    APP_CONFIG.SESSIONS_FILTER_BY_USER = False
    asyncio.run(SM.get_all_sessions("u1"))
    check("no argument follows a deployment that disables filtering", seen[-1]["filter"] is False)
finally:
    SM._query_session_index = _real
    APP_CONFIG.SESSIONS_FILTER_BY_USER = _orig_global

import inspect  # noqa: E402
src = inspect.getsource(SM.get_all_sessions)
check("the scan fallback uses the per-call scope too",
      "filter_by_user=filter_by_user," in src and "filter_by_user=APP_CONFIG" not in src)

total = PASS + FAIL
print(f"{'FAILED' if FAIL else 'ALL PASS'} — {total} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
