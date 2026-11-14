#!/usr/bin/env python3
"""
Module tests — REST activation firewall.

A profile may be driven through the REST API — as the user's DEFAULT profile OR an @TAG override —
only if it has been ACTIVATED (recorded in ``active_for_consumption_profile_ids``; activation
requires ALL of the profile's tests to pass). An unactivated / untested profile is rejected up
front with an honest 403 instead of falling through to a deferred-LLM placeholder and failing deep
in the planner with a misleading "model may be overloaded".

Internal orchestration is exempt: a genie coordinator drives its validated child profiles via the
SAME REST endpoints (genie_coordinator posts to /v1/sessions and /query), so genie slave session
CREATION (``genie_parent_session_id`` present) and genie slave QUERIES (target session is a
``genie_metadata.is_genie_slave`` session) bypass the firewall.

CI-pure (helper unit tests + source-level wiring assertions; no live server).

Run:  python test/test_rest_activation_firewall.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


# ============================================================ helper behaviour
print("\n[firewall] _assert_profile_activated: activated passes, everything else fails closed")
import trusted_data_agent.core.config_manager as cmmod  # noqa: E402
import trusted_data_agent.api.rest_routes as rr  # noqa: E402


class _FakeCM:
    def __init__(self, activated, profiles):
        self._a = activated
        self._p = profiles

    def get_profile(self, pid, uuid):
        return self._p.get(pid)

    def get_active_for_consumption_profile_ids(self, uuid):
        return self._a


_PROFILES = {"p-optim": {"tag": "OPTIM"}, "p-idggt": {"tag": "IDGGT"}}
_orig_get_cm = cmmod.get_config_manager


def _install(activated):
    cmmod.get_config_manager = lambda: _FakeCM(activated, _PROFILES)


try:
    _install(["p-idggt"])
    ok, err = rr._assert_profile_activated("p-idggt", "u1")
    check("activated profile passes", ok and err == {})

    ok, err = rr._assert_profile_activated("p-optim", "u1")
    check("unactivated profile BLOCKED with code=profile_not_activated",
          (not ok) and err.get("code") == "profile_not_activated")
    check("blocked error names the tag (@OPTIM) and says 'not activated'",
          "OPTIM" in err.get("error", "") and "not activated" in err.get("error", "").lower())
    check("blocked error carries profile_tag + profile_id for the caller",
          err.get("profile_tag") == "OPTIM" and err.get("profile_id") == "p-optim")

    ok, err = rr._assert_profile_activated("p-ghost", "u1")
    check("unknown profile -> profile_not_found", (not ok) and err.get("code") == "profile_not_found")

    ok, err = rr._assert_profile_activated(None, "u1")
    check("missing profile -> no_profile", (not ok) and err.get("code") == "no_profile")

    # fail CLOSED on unexpected error (safe default for a firewall)
    cmmod.get_config_manager = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    ok, err = rr._assert_profile_activated("p-idggt", "u1")
    check("fails CLOSED (deny) on unexpected error", (not ok) and err.get("code") == "activation_check_failed")
finally:
    cmmod.get_config_manager = _orig_get_cm


# ============================================================ endpoint wiring + genie exemptions (source-level)
print("\n[firewall] wired into all 4 REST entry points; genie orchestration exempted")
_src = open(os.path.join(os.path.dirname(__file__), "..", "src",
            "trusted_data_agent", "api", "rest_routes.py")).read()

check("firewall helper is defined", "def _assert_profile_activated(" in _src)
# 4 call sites: query, sessions POST, prompts/execute, execute-raw
check("firewall is invoked at >= 4 REST entry points",
      _src.count("_assert_profile_activated(") >= 5)  # 1 def + >=4 call sites
check("blocked requests return HTTP 403", _src.count("return jsonify(_fw_err), 403") >= 4)

# genie exemptions
check("query endpoint exempts genie SLAVE sessions (is_genie_slave)",
      "_is_genie_slave_session" in _src and 'get("genie_metadata", {}).get("is_genie_slave")' in _src)
check("query firewall only runs when NOT a genie slave session",
      "if not _is_genie_slave_session:" in _src)
check("sessions POST exempts genie slave CREATION (genie_parent_session_id)",
      "if not genie_parent_session_id:" in _src)

# the effective profile (override OR default) is what gets checked at the session/query points
check("query checks the EFFECTIVE profile (override or default)",
      "_assert_profile_activated(profile_id_to_use, user_uuid)" in _src)


# ============================================================ summary
print(f"\n{'=' * 60}\nREST activation firewall: {_passed} passed, {_failed} failed\n{'=' * 60}")
sys.exit(1 if _failed else 0)
