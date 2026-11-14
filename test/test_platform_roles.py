#!/usr/bin/env python3
"""
Platform roles — what an agent with TDA_Platform is FOR, across namespaces.

Contract: src/trusted_data_agent/core/platform_roles.py, components/builtin/platform/roles/*.json and the
Platform manifest's `roles` declaration.

  §A  the registry — every role file well-formed; the default role exists and is active; a
      person's act is never a role's effect; a planned role says what it still needs
  §B  active roles against the SERVED catalog — every pattern matches described operations,
      every playbook pattern matches a real playbook
  §C  resolution — the default when an agent names none; a planned or unknown role grants
      nothing (fail closed)
  §D  the surface — a role lists what it names, its effects decide what is callable, the
      operation's own policy still applies; find and call are scoped to the agent's roles
  §E  the signature — roles are capability: each held role hashed with a digest of what it grants
  §F  playbooks follow the role — find delivers only from the slice a role is taught

Run: PYTHONPATH=src python3 test/test_platform_roles.py   (no LLM calls, no writes)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "platform-roles-test")

from trusted_data_agent.core import platform_roles as R  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


# ── §A the registry ─────────────────────────────────────────────────────────────
print("§A the registry")
reg = R.load()
check("every role file is well-formed", reg.problems == [], reg.problems)
check("the roles live in the Platform component (its manifest declares where)",
      R.ROLES_DIR.is_dir() and R.ROLES_DIR == R.COMPONENT_DIR / "roles" and R.DECLARATION.get("definitions") == "roles/")
_manifest = json.loads((R.COMPONENT_DIR / "manifest.json").read_text())
check("the manifest declares the grant, the default and the playbook skill — the module reads them from there",
      _manifest["roles"]["grant"] == "componentConfig.platform.roles"
      and tuple(_manifest["roles"]["default"]) == R.DEFAULT_ROLES == ("guide",)
      and _manifest["roles"]["playbooks"] == R.PLAYBOOK_SKILL == "platform-playbooks")
check("no roles folder is left at the repository root", not (ROOT / "platform_roles").exists())
check("the default role exists and is active", all(reg.roles.get(r) and reg.roles[r].active for r in R.DEFAULT_ROLES))
check("ranks are unique (one work order)", len({r.rank for r in reg.roles.values()}) == len(reg.roles))
check("no role may call a person's act (effects are read/write only)",
      all(set(r.effects) <= {"read", "write"} for r in reg.roles.values()))
check("every planned role says which namespaces it still needs",
      all(r.needs for r in reg.roles.values() if not r.active), [r.id for r in reg.roles.values() if not r.active and not r.needs])
check("no shipped role carries a tier", all("tier" not in json.loads(f.read_text()) for f in R.ROLES_DIR.glob("*.json")))
check("the Agent Builder is active and may write", reg.roles["agent-builder"].active and "write" in reg.roles["agent-builder"].effects)
check("the nine roles are there — the plan's eight and the Problem Solver",
      set(reg.roles) == {"guide", "agent-builder", "knowledge-curator", "automation-builder", "quality-analyst",
                         "insight-builder", "publisher", "admin-monitor", "problem-solver"}, sorted(reg.roles))
import tempfile  # noqa: E402
bad_dir = Path(tempfile.mkdtemp(prefix="platform_roles_test_"))   # never inside the repository
try:
    (bad_dir / "x.json").write_text(json.dumps({"id": "y", "title": "X", "description": "d", "status": "active",
                                               "effects": ["read"], "operations": ["a.*"]}))
    (bad_dir / "z.json").write_text(json.dumps({"id": "z", "title": "Z", "description": "d", "status": "active",
                                               "effects": ["human_only"], "operations": ["a.*"]}))
    (bad_dir / "w.json").write_text(json.dumps({"id": "w", "title": "W", "description": "d", "status": "live",
                                               "tier": "admin", "effects": ["read"], "operations": ["!a.*"]}))
    bad = R.load(bad_dir)
    check("a malformed role is reported and left out (fail closed)", bad.roles == {} and len(bad.problems) >= 4, bad.problems)
    check("…a file whose id is not its name", any("must equal the file name" in p for p in bad.problems))
    check("…a person's act as an effect", any("never a role's effect" in p for p in bad.problems))
    check("…an unknown status, and patterns that only exclude",
          any("status must" in p for p in bad.problems) and any("inclusion pattern" in p for p in bad.problems), bad.problems)
    check("…a tier on a role: tiers gate operations, roles describe agents — never mixed",
          any("carries no tier" in p for p in bad.problems), bad.problems)
finally:
    for f in bad_dir.glob("*.json"):
        f.unlink()
    bad_dir.rmdir()

# ── §B active roles against the served catalog ─────────────────────────────────
print("§B active roles against the served catalog")
logging.disable(logging.CRITICAL)
from trusted_data_agent.main import app  # noqa: E402
logging.disable(logging.NOTSET)
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
import fnmatch  # noqa: E402

cat = build_catalog(app)
ops = [o.op for o in cat.operations]
for role in (r for r in reg.roles.values() if r.active):
    for pat in role.operations:
        bare = pat.lstrip("!")
        check(f"{role.id}: pattern {pat!r} matches described operations", any(fnmatch.fnmatchcase(o, bare) for o in ops))
    from trusted_data_agent.skills.manager import get_skill_manager  # noqa: E402
    from trusted_data_agent.skills.package import progressive_index  # noqa: E402
    for pb in role.playbooks:
        sid, _, glob = pb.partition(":")
        anchors = [e.get("anchor") for e in progressive_index(get_skill_manager().get_skill_manifest(sid) or {})]
        check(f"{role.id}: playbook pattern {pb!r} matches a real playbook", any(fnmatch.fnmatchcase(a or "", glob) for a in anchors), anchors)
guide = reg.roles["guide"]
covered = [o for o in ops if guide.lists(o)]
check("the Guide covers the described read namespaces it is for", {"profiles.overview", "trust.agents", "consumption.summary"} <= set(covered))
planned_missing = {}
described_ns = {o.split(".")[0] for o in ops}
for role in (r for r in reg.roles.values() if not r.active):
    planned_missing[role.id] = [n for n in role.needs if n.replace("-", "_") not in described_ns]
print("  planned roles still need: " + "; ".join(f"{k} → {', '.join(v) or 'nothing'}" for k, v in sorted(planned_missing.items())))

# ── §C resolution ───────────────────────────────────────────────────────────────
print("§C resolution")
check("an agent that names no role holds the default", R.granted_ids({}) == list(R.DEFAULT_ROLES)
      and R.granted_ids({"componentConfig": {"platform": {"enabled": True}}}) == list(R.DEFAULT_ROLES))
check("an empty list holds the default too", R.granted_ids({"componentConfig": {"platform": {"roles": []}}}) == list(R.DEFAULT_ROLES))
check("named roles are kept in order, once",
      R.granted_ids({"componentConfig": {"platform": {"roles": ["guide", "agent-builder", "guide"]}}}) == ["guide", "agent-builder"])
# Every shipped role is active now: a planned one is built for the check.
from dataclasses import replace as _rp  # noqa: E402
_reg_p = R.Registry(roles={**reg.roles, "later": _rp(reg.roles["guide"], id="later", status="planned")})
held = R.roles_of({"componentConfig": {"platform": {"roles": ["guide", "later", "no-such-role"]}}}, _reg_p)
check("a planned or unknown role grants nothing (fail closed)", [r.id for r in held] == ["guide"], [r.id for r in held])
check("only unknown roles: nothing at all", R.roles_of({"componentConfig": {"platform": {"roles": ["no-such-role"]}}}, reg) == [])

# ── §D the surface ──────────────────────────────────────────────────────────────
print("§D the surface")
G = [guide]
check("no role context lists everything (a caller that is not an agent)", R.role_state(None, "profiles.create", "write") == (True, None))
check("the Guide lists and may call a read it names", R.role_state(G, "profiles.overview", "read") == (True, None))
listed, why = R.role_state(G, "profiles.create", "write")
check("the Guide lists a write it names but may not call it, and says why", listed and why and "read only" in why, why)
check("an exclusion wins: the Guide does not list metric queries", R.role_state(G, "profiles.metric_query", "read")[0] is False)
check("a person's act named by the role is listed (so it can be named)", R.role_state(G, "trust.agent_sign_off", "human_only") == (True, None))
check("an operation in no role is not listed", R.role_state(G, "widgets.list", "read")[0] is False)

f = P.find("metric sql", "user", cat=cat, roles=G)
check("find is scoped to the roles: nothing outside them is found", all(not x["op"].startswith("profiles.metric") for x in f["found"]), f["found"][:2])
f = P.find("create an agent", "user", cat=cat, roles=G)
create = next((x for x in f["found"] if x["op"] == "profiles.create"), None)
check("…a write the Guide names is found and marked not callable — its role reads only",
      create and create["callable"] is False and "read only" in create.get("why_not", ""), create)
W = [R.Role(id="w", title="Writer", description="d", status="active", effects=("write",), operations=("profiles.*",))]
ov = next((x for x in P.find("agent overview", "user", cat=cat, roles=W)["found"] if x["op"] == "profiles.overview"), None)
check("…where the policy would allow it, the role is the reason (a write-only role naming a read)",
      ov and ov["callable"] is False and "outside this agent's role (Writer" in ov.get("why_not", ""), ov)
check("…no roles at all: nothing is found", P.find("my agents", "user", cat=cat, roles=[])["found"] == [])
_orig_tier = P.user_tier
P.user_tier = lambda uid: "user"
try:
    r = asyncio.run(P.call("profiles.metric_compile", {"profile_id": "x", "metric_name": "m"}, "u-x", cat=cat, app=app, roles=G))
    check("call: an operation outside the roles is refused before any route runs, naming the role",
          r["ok"] is False and "outside this agent's role (Guide)" in r["error"], r)
    r = asyncio.run(P.call("profiles.create", {}, "u-x", cat=cat, app=app, roles=G))
    check("call: a write is refused before any route runs — the Guide reads only", r["ok"] is False and "read only" in r["error"], r)
    r = asyncio.run(P.call("profiles.default", {}, "u-x", cat=cat, app=app, roles=W))
    check("call: an effect the role does not allow is refused, naming the role", r["ok"] is False and "write only" in r["error"], r)
finally:
    P.user_tier = _orig_tier

check("a role names an admin-only operation: a user's agent still cannot reach it (the tier gates the operation)",
      not any(x["op"] == "consumption.users" for x in P.find("every user's consumption", "user", cat=cat,
              roles=[R.Role(id="t", title="T", description="d", status="active", effects=("read",),
                            operations=("consumption.*",))])["found"]))

# ── §E the signature ────────────────────────────────────────────────────────────
print("§E the signature")
from trusted_data_agent.core.profile_config_contract import _canon_component_config  # noqa: E402

base = {"platform": {"enabled": True}}
_g = R.digest(reg.roles["guide"])
_b = R.digest(reg.roles["agent-builder"])
check("a role digests to a short hash", len(_g) == 12 and _g != _b)
check("an agent naming no role hashes its default role with its digest",
      _canon_component_config(base) == ["platform", f"platform:role=guide@{_g}"])
check("naming the default explicitly hashes the same (same capability, same hash)",
      _canon_component_config({"platform": {"enabled": True, "roles": ["guide"]}}) == _canon_component_config(base))
check("another role enters the hash with its digest",
      _canon_component_config({"platform": {"enabled": True, "roles": ["guide", "agent-builder"]}})
      == ["platform", f"platform:role=agent-builder@{_b}", f"platform:role=guide@{_g}"])
check("order does not move the hash",
      _canon_component_config({"platform": {"enabled": True, "roles": ["agent-builder", "guide"]}})
      == _canon_component_config({"platform": {"enabled": True, "roles": ["guide", "agent-builder"]}}))
check("roles on a disabled Platform grant nothing and hash nothing",
      _canon_component_config({"platform": {"enabled": False, "roles": ["agent-builder"]}}) == [])
check("an unknown role hashes as unknown (and grants nothing)",
      R.canonical({"platform": {"enabled": True, "roles": ["no-such-role"]}}) == ["platform:role=no-such-role@unknown"])
from dataclasses import replace as _replace  # noqa: E402
_wider = R.Registry(roles={**reg.roles, "guide": _replace(reg.roles["guide"], operations=reg.roles["guide"].operations + ("flows.run",))})
check("a role whose definition widens moves the signature of every agent holding it",
      R.canonical({"platform": {"enabled": True}}, _wider) != R.canonical({"platform": {"enabled": True}}, reg))
_effects = R.Registry(roles={**reg.roles, "guide": _replace(reg.roles["guide"], effects=("read", "write"))})
check("…so does a change of its effects", R.digest(_effects.roles["guide"]) != _g)
_status = R.Registry(roles={**reg.roles, "guide": _replace(reg.roles["guide"], status="planned")})
check("…and of its status", R.digest(_status.roles["guide"]) != _g)
_words = _replace(reg.roles["guide"], title="Another title", description="other words", rank=42, needs=("x",))
check("its title, description, needs and rank are words about it — the hash stays", R.digest(_words) == _g)
_reordered = _replace(reg.roles["guide"], operations=tuple(reversed(reg.roles["guide"].operations)))
check("the order of its patterns does not move the hash", R.digest(_reordered) == _g)

# ── §F playbooks follow the role ────────────────────────────────────────────────
print("§F playbooks follow the role")
from components.builtin.platform.handler import PlatformHandler, _DELIVERED  # noqa: E402

agent = {"id": "p-r", "tag": "PR", "profile_type": "llm_only",
         "componentConfig": {"platform": {"enabled": True}, "skill_library": {"enabled": True}},
         "skillsConfig": {"skills": [{"id": "platform-playbooks", "active": True}]}}
ctx = {"profile": agent, "user_uuid": "u-x", "session_id": "s-r", "current_turn_number": 1}
H = PlatformHandler()
_DELIVERED.clear()
check("the handler reads the agent's roles from its profile", [r.id for r in H._roles(ctx)] == ["guide"])
check("…and none when its profile cannot be read (fail closed)", H._roles({}) == [])
usage_only = R.Role(id="u", title="Usage", description="d", status="active", effects=("read",),
                    operations=("consumption.*",), playbooks=("platform-playbooks:usage/*",))
pb = H._playbook("which of my agents need signing", ["trust.agents"], ctx, None, [usage_only])
check("a playbook outside the role's slice is not delivered", pb is None or "text" not in pb, pb)
pb = H._playbook("how much have I used this period", ["consumption.summary"], ctx, None, [usage_only])
check("…one inside it is", pb and pb.get("anchor") == "usage/this-period" and pb.get("text"), pb and pb.get("anchor"))
pb = H._playbook("which of my agents need signing", ["trust.agents"], dict(ctx, session_id="s-r2"), None, [guide])
check("the Guide is taught the reading playbooks", pb and pb.get("anchor") == "trust/my-agents")
check("…not the building ones — it reads only", not guide.teaches("platform-playbooks", "building/create-agent")
      and reg.roles["agent-builder"].teaches("platform-playbooks", "building/create-agent"))

# ── §G choosing roles in the agent editor ───────────────────────────────────────
print("§G the agent editor")
import asyncio as _aio  # noqa: E402
from trusted_data_agent.auth.database import get_db_session  # noqa: E402
from trusted_data_agent.auth.models import User  # noqa: E402
from trusted_data_agent.auth.security import create_internal_token  # noqa: E402
with get_db_session() as _s:
    _u = _s.query(User).filter_by(is_active=True).first()
    _uid = _u.id if _u else None
if _uid:
    async def _roles_route():
        r = await app.test_client().get("/api/v1/platform/roles", headers={"Authorization": f"Bearer {create_internal_token(_uid)}"})
        return r.status_code, await r.get_json()
    st, body = _aio.run(_roles_route())
    ids = [x["id"] for x in (body or {}).get("roles", [])]
    check("GET /v1/platform/roles lists every role in work order, with status and effects",
          st == 200 and ids[:2] == ["guide", "agent-builder"] and len(ids) == len(reg.roles)
          and all({"title", "description", "status", "effects", "needs"} <= set(x) for x in body["roles"]), (st, ids))
    check("…with the default and the write modes the editor offers",
          body.get("default") == ["guide"] and body.get("write_modes") == ["off", "review", "allow"])
check("the route is itself described (a read)", cat.by_op.get("platform.roles") is not None and cat.by_op["platform.roles"].effect == "read")
cj = (ROOT / "static/js/handlers/configurationHandler.js").read_text()
blk = cj[cj.index("comp-platform-roles\" data-component-id=\"platform\""):cj.index("const _platformSync")]
check("the Platform row offers the roles and the write mode", "cpr-roles" in blk and "cpr-writes-select" in blk
      and 'value="off"' in blk and 'value="review"' in blk and 'value="allow"' in blk)
check("…says what each role grants is signed and the write mode is not",
      "What each role grants is part of the agent's signature; this setting is not." in blk)
check("…each role says what it reaches, and opens the same reading as the Roles tab in place",
      "roleCountsLine(role)" in cj and "cpr-role-more" in cj and "roleReachHTML(role)" in cj
      and "fetchPlatformRoles(true)" in cj)
check("…the editor reads the roles only through the shared view module (one fetch, one rule)",
      "fetch('/api/v1/platform/roles'" not in cj and "from './platformRolesView.js?v=" in cj)
check("…styled with theme variables only (no hard-coded colours)",
      "var(--border-secondary)" in blk and "var(--text-primary)" in blk and not __import__("re").search(r"text-(?:gray|purple|blue|red)-\d|bg-gray-\d", blk))
check("a planned role is shown, never selectable", "cb.dataset.planned === '1'" in cj and "data-planned=" in cj)
check("the write mode is offered only when a selected role may write",
      "cb.checked && cb.dataset.writes === '1'" in cj and "sel.disabled = !on || !canWrite" in cj)
check("roles are saved only once the list has loaded (a save before it keeps the stored roles)",
      "prow.dataset.loaded === '1'" in cj and "componentConfig[compId].roles = picked" in cj)
check("the write mode saves into authorityConfig.platformSurface and keeps every other authority key",
      "base.platformSurface = ps" in cj and __import__("re").search(
          r"profileData\.authorityConfig\s*\|\|\s*JSON\.parse\(JSON\.stringify\(liveP\.authorityConfig", cj) is not None)

# ── §H what the views show is what is enforced ──────────────────────────────────
print("§H the views")
from trusted_data_agent.core import platform_operations as P2  # noqa: E402
for role in reg.roles.values():
    reach = P2.role_reach(role, "admin", cat)
    ops = {r["op"]: r for r in reach}
    if role.active:
        mismatch = [o for o, r in ops.items()
                    if r["callable"] != P2.describe(cat.by_op[o], [role], "review")["callable"]]
        check(f"{role.id}: every operation the view shows callable is callable through TDA_Platform, and no other",
              not mismatch, mismatch[:5])
    else:
        check(f"{role.id}: a planned role shows nothing callable (it grants nothing)",
              not any(r["callable"] for r in reach) and all("planned" in r.get("why_not", "") or r.get("why_not") for r in reach))
    check(f"{role.id}: the view lists exactly what the role lists (never a `never`)",
          set(ops) == {o.op for o in cat.operations if o.effect != "never" and role.lists(o.op)})
_u = R.Role(id="u", title="U", description="d", status="active", effects=("read", "write"), operations=("trust.*",))
check("an admin-only operation is not counted for a user — the tier still decides",
      {r["op"] for r in P2.role_reach(_u, "user", cat)} < {r["op"] for r in P2.role_reach(_u, "admin", cat)})
_profiles = [{"id": "a1", "tag": "ONE", "componentConfig": {"platform": {"enabled": True}}},
             {"id": "a2", "tag": "TWO", "componentConfig": {"platform": {"enabled": True, "roles": ["guide", "agent-builder"]}}},
             {"id": "a3", "tag": "OFF", "componentConfig": {"platform": {"enabled": False, "roles": ["agent-builder"]}}}]
_real_tier = P2.user_tier
P2.user_tier = lambda u: "admin"
try:
    ov = P2.roles_overview("u-view", detail=True, reg=reg, cat=cat, profiles=_profiles)
finally:
    P2.user_tier = _real_tier
byid = {r["id"]: r for r in ov["roles"]}
check("an agent holding no role is counted under the default",
      [a["tag"] for a in byid["guide"]["agents"]] == ["ONE", "TWO"])
check("an agent with the Platform component off holds no role",
      [a["tag"] for a in byid["agent-builder"]["agents"]] == ["TWO"])
check("counts agree with the lists",
      all(r["counts"]["operations"] == len(r["operations"]) and r["counts"]["playbooks"] == len(r["playbooks"])
          and r["counts"]["agents"] == len(r["agents"])
          and r["counts"]["callable"] == sum(1 for x in r["operations"] if x["callable"]) for r in ov["roles"]))
check("each playbook names the roles taught it — the inverse of each role's slice",
      all(set(pb["taught_to"]) == {r.id for r in reg.roles.values() if r.teaches(R.PLAYBOOK_SKILL, pb["anchor"])}
          for pb in ov["playbooks"]) and len(ov["playbooks"]) >= 10)
check("a write a person always approves is marked so in the view",
      any(x.get("always_reviewed") == "it deletes" for x in byid["agent-builder"]["operations"]))
check("each role carries the digest its agents' signatures hold", all(r["digest"] == R.digest(reg.roles[r["id"]]) for r in ov["roles"]))
check("the declaration is the manifest's", ov["declaration"] == R.DECLARATION and ov["default"] == list(R.DEFAULT_ROLES))
check("without detail: counts only, no lists", "operations" not in P2.roles_overview("u-view", reg=reg, cat=cat, profiles=[])["roles"][0])
check("the route is described: one read of the caller's own (it names their agents), `detail` its only argument",
      cat.by_op["platform.roles"].effect == "read" and cat.by_op["platform.roles"].scope == "own"
      and set(cat.by_op["platform.roles"].query) == {"detail"})
from trusted_data_agent.components.manager import get_component_manager  # noqa: E402
_comps = {c.component_id: c.to_api_dict() for c in get_component_manager().get_all_components()}
check("the component list serves the Platform's roles declaration — and none for a component without roles",
      _comps["platform"]["roles"] == R.DECLARATION and _comps.get("chart", {}).get("roles") is None)

# ── §I each role's examples and boundaries — written per role, held to the catalog ─
print("§I examples and boundaries")
import fnmatch as _fnm  # noqa: E402
for role in (r for r in reg.roles.values() if r.active):
    listed = {o.op for o in cat.operations if o.effect != "never" and role.lists(o.op)}
    check(f"{role.id}: shows at least one example and one boundary", bool(role.examples) and bool(role.not_offered))
    must = sorted(o.op for o in cat.operations if role.lists(o.op) and (o.effect == "human_only" or o.scope == "cross_user"))
    covered = [p for n in role.not_offered for p in (n.get("ops") or [])]
    unexplained = [o for o in must if not any(_fnm.fnmatchcase(o, p) for p in covered)]
    check(f"{role.id}: every person's act and cross-user operation it lists is explained in its own boundaries",
          not unexplained, unexplained)
    stray = [p for p in covered if not any(_fnm.fnmatchcase(o, p) for o in listed)]
    check(f"{role.id}: every operation a boundary names is one the role lists", not stray, stray)
    bad_args = []
    for n in role.not_offered:
        for a in n.get("args") or []:
            op_id, arg = a.split(":")
            o = cat.by_op.get(op_id)
            if o is None or op_id not in listed or arg in (set(o.path_params) | set(o.query) | set(o.body)):
                bad_args.append(a)
    check(f"{role.id}: every argument a boundary names is left out of an operation the role uses", not bad_args, bad_args)
from dataclasses import replace as _rep  # noqa: E402
_g = reg.roles["guide"]
check("examples and boundaries are words about a role — the digest stays",
      R.digest(_rep(_g, examples=(), not_offered=())) == R.digest(_g))
_bad = R._check({"id": "x", "title": "X", "description": "d", "status": "active", "effects": ["read"],
                 "operations": ["a.b"], "examples": [{"ask": "q"}], "not_offered": [{"what": "w"}, {"what": "w", "why": "y", "args": ["nocolon"]}]}, "x.json")
check("a malformed example or boundary is refused when the roles load",
      any("example 0" in m for m in _bad) and any("not_offered 0" in m for m in _bad) and any("'op:argument'" in m for m in _bad), _bad)
_ov = P2.roles_overview("u-view", detail=True, reg=reg, cat=cat, profiles=[])
_bq = next(r for r in _ov["roles"] if r["id"] == "quality-analyst")
check("the view carries each role's examples and boundaries (the Roles tab and the editor read them)",
      _bq.get("examples") == [dict(e) for e in reg.roles["quality-analyst"].examples]
      and _bq.get("not_offered") == [dict(n) for n in reg.roles["quality-analyst"].not_offered])

print(f"\n{'ALL PASS' if not failed else 'FAILED'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
