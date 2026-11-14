#!/usr/bin/env python3
"""The legacy resource routes are authenticated and read the agent asked for — the
"anonymous GET /tools returned the whole catalog" gate.

``/tools``, ``/prompts``, ``/connectors`` and ``/resources`` (api_bp, routes.py) carried no
``@require_auth`` — on prod an anonymous request got the full tool catalog (200, 95 KB) —
and read the PROCESS-GLOBAL ``APP_STATE`` catalog: whichever agent was last loaded, with
THAT agent's on/off flags, so the panel's Tools figure belonged to another agent whenever
one had been loaded since. They now require the token and go through the ONE mechanism,
``rest_routes.get_profile_resources`` (``?profile_id=`` or the caller's default agent).

Structural (no server): the decorator on each route, the per-agent helper they share, no
global-catalog read left in them, and the panel's default path off the legacy routes.
A live check is the deploy verification: ``curl -s -o /dev/null -w '%{http_code}' …/tools`` → 401.

Run:  python test/test_resource_routes_auth.py
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


src = open(os.path.join(ROOT, "src/trusted_data_agent/api/routes.py")).read()
ROUTES = ("/tools", "/prompts", "/connectors", "/resources")
for path in ROUTES:
    m = re.search(r'@api_bp\.route\("' + re.escape(path) + r'"\)\n(@[^\n]+\n)*async def (\w+)\(current_user\):', src)
    check(f"GET {path} is declared", m is not None)
    if not m:
        continue
    decos = m.group(0)
    check(f"GET {path} carries @require_auth and receives current_user", "@require_auth" in decos)
    # the view body, up to the next route
    body_start = m.end()
    nxt = re.search(r"\n(?:@|async def |def )", src[body_start:])
    body = src[body_start:body_start + nxt.start()] if nxt else src[body_start:]
    check(f"GET {path} reads no process-global catalog",
          "APP_STATE" not in body and "structured_tools" not in body and "structured_prompts" not in body
          and "active_profile_id" not in body)
    if path != "/resources":
        check(f"GET {path} goes through the per-agent payload", "_profile_resources_payload(current_user)" in body)

check("the shared helper serves ?profile_id= or the caller's default agent through get_profile_resources",
      'request.args.get("profile_id") or get_config_manager().get_default_profile_id(current_user.id)' in src
      and "from trusted_data_agent.api.rest_routes import get_profile_resources" in src)
check("a Focus / Coordinate / Ideate-without-tools agent keeps the metadata shape the panel reads",
      '_SPECIAL_PROFILE_TYPES = ("rag_focused", "genie", "llm_only")' in src
      and 'return {kind: {}, **{k: payload[k] for k in _SPECIAL_META_KEYS if k in payload}}' in src)

main_js = open(os.path.join(ROOT, "static/js/main.js")).read()
check("the panel's default agent reads /api/v1/profiles/<id>/resources, not the legacy routes",
      "const fetchId = profileId || window.configState?.defaultProfileId || null;" in main_js
      and "fetch('/tools'" not in main_js and "fetch('/prompts'" not in main_js and "fetch('/connectors'" not in main_js)

total = _passed + _failed
print(f"{'FAILED' if _failed else 'ALL PASS'} — {total} assertions ({_passed} passed, {_failed} failed)")
sys.exit(1 if _failed else 0)
