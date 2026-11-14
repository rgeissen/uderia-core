#!/usr/bin/env python3
"""The Problem Solver — the role that gets a question answered by several of the user's agents.

  §A  the role — active, read-only, the only role that asks; it reads what each agent is for,
      how trusted it is and how well it does, and what the user holds that no agent uses; it
      names agent creation (to explain it) but cannot submit it — held with the Agent Builder,
      the agent can
  §B  the grant — `asks` enters a role's digest only when granted, so every role that does not
      ask kept its digest (no signature moved for them); a malformed `asks` is refused
  §C  asking is the role's — an agent without it is refused before anything runs; with it the
      ask proceeds to the asking gates
  §D  the playbooks — routing a question across experts, a second opinion, what is missing;
      taught to this role only; find delivers them
  §E  the surfaces — the API marks the role that asks; the editor ties the ask list to it; the
      roles view says so

No LLM calls, no writes. Run: python test/test_platform_problem_solver.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-problem-solver-test")
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
reg = R.registry()
ps = reg.roles.get("problem-solver")

# ── §A the role ─────────────────────────────────────────────────────────────────
print("§A the role")
check("the Problem Solver is shipped and active", ps is not None and ps.active)
check("it is read-only and asks", ps.effects == ("read",) and ps.asks is True)
check("it is the only role that asks", [r.id for r in reg.roles.values() if r.asks] == ["problem-solver"])
for op_id in ("profiles.list", "profiles.overview", "trust.agents", "trust.agent", "evals.kpi_dashboard",
              "rag.collections", "knowledge_graph.list", "mcp.servers", "profiles.active_for_consumption"):
    check(f"it may call {op_id}", op_id in by and R.role_state([ps], op_id, by[op_id].effect) == (True, None))
check("it names agent creation but cannot submit it",
      ps.lists("profiles.create") and R.role_state([ps], "profiles.create", "write")[0] is True
      and R.role_state([ps], "profiles.create", "write")[1] is not None)
bld = reg.roles["agent-builder"]
check("…held with the Agent Builder, the agent can (submitted for approval)",
      R.role_state([ps, bld], "profiles.create", "write") == (True, None) and P.always_reviewed(by["profiles.create"]))
check("it changes nothing itself", not any(ps.lists(o.op) and R.role_state([ps], o.op, o.effect) == (True, None)
                                          for o in cat.operations if o.effect == "write"))
check("every person's act or out-of-role act it names is explained",
      all(isinstance(n.get("why"), str) and n["why"] for n in ps.not_offered)
      and any("profiles.create" in (n.get("ops") or []) for n in ps.not_offered))

# ── §B the grant ────────────────────────────────────────────────────────────────
print("§B the grant")


def _old_digest(role):
    body = {"status": role.status, "effects": sorted(role.effects),
            "operations": sorted(role.operations), "playbooks": sorted(role.playbooks)}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:12]


check("every role that does not ask keeps the digest it had (no signature moved for it)",
      all(R.digest(r) == _old_digest(r) for r in reg.roles.values() if not r.asks))
check("the asking grant is part of what the Problem Solver's digest covers", R.digest(ps) != _old_digest(ps))
problems = R._check({**json.loads((R.ROLES_DIR / "problem-solver.json").read_text()), "asks": "yes"}, "x")
check("a malformed grant is refused", any("asks must be true or false" in p for p in problems), problems)
check("no planned or unknown role asks", not R.may_ask([]) and not R.may_ask([reg.roles["guide"]]))

# ── §C asking is the role's ─────────────────────────────────────────────────────
print("§C asking is the role's")
import trusted_data_agent.components.settings as CS  # noqa: E402
from trusted_data_agent.core import platform_asking as A  # noqa: E402
_real = CS.get_component_settings
CS.get_component_settings = lambda: {"platform_ask_enabled": True}
calls = []


async def _route(app, method, path, user, body=None):
    calls.append(path)
    return 500, {}
try:
    guide = {"id": "p-g", "tag": "G", "componentConfig": {"platform": {"enabled": True, "roles": ["guide"]}},
             "authorityConfig": {"platformSurface": {"ask": ["p-x"]}}}
    r = asyncio.run(A.ask(guide, "@X", "q?", "u", app=object(), route=_route, poll_s=0))
    check("an agent without the role is refused, naming the Problem Solver, nothing run",
          not r["ok"] and "Problem Solver" in r["error"] and calls == [], r)
    solver = {**guide, "componentConfig": {"platform": {"enabled": True, "roles": ["problem-solver"]}}}
    r = asyncio.run(A.ask(solver, "@NOBODY-XYZ", "q?", "u-none", app=object(), route=_route, poll_s=0))
    check("with the role, the ask passes to the next gate (here: no such agent)",
          not r["ok"] and "Problem Solver" not in r["error"] and calls == [], r)
finally:
    CS.get_component_settings = _real

# ── §D the playbooks ────────────────────────────────────────────────────────────
print("§D the playbooks")
import platform_playbooks as PB  # noqa: E402
anchors = [PB.anchor_of(p) for p in PB.section_files()]
for a in ("solving/route", "solving/second-opinion", "solving/gaps"):
    check(f"{a} is shipped", a in anchors)
    check(f"{a} is taught to the Problem Solver only",
          [r.id for r in reg.roles.values() if r.teaches(R.PLAYBOOK_SKILL, a)] == ["problem-solver"])
text = (ROOT / "skills/builtin/platform-playbooks/library/solving/route.md").read_text()
check("the route playbook writes questions complete in themselves and names who answered",
      "complete in itself" in text and "Present an asked agent's answer as your own" in text)
gaps = (ROOT / "skills/builtin/platform-playbooks/library/solving/gaps.md").read_text()
check("the gaps playbook tells a missing agent from missing data, and never builds for missing data",
      "An agent is missing" in gaps and "The data is missing" in gaps and "Build an agent when the data itself is missing" in gaps)
found = [f["op"] for f in P.find("get a second opinion", "user", cat=cat, roles=[ps])["found"]]
check("find, scoped to the role, reaches its reads", all(R.role_state([ps], o, by[o].effect)[0] for o in found), found)

# ── §E the surfaces ─────────────────────────────────────────────────────────────
print("§E the surfaces")
ov = P.roles_overview(None, reg=reg, cat=cat, profiles=[])
row = next((x for x in ov["roles"] if x["id"] == "problem-solver"), {})
check("the roles API marks the role that asks", row.get("asks") is True
      and all(x.get("asks") is False for x in ov["roles"] if x["id"] != "problem-solver"))
ed = (ROOT / "static/js/handlers/configurationHandler.js").read_text()
check("the editor ties the ask list to a ticked role that asks",
      "cb.dataset.asks === '1'" in ed and "!on || !canAsk" in ed and "data-asks=" in ed)
view = (ROOT / "static/js/handlers/platformRolesView.js").read_text()
check("the roles view says a role asks other agents", "asks other agents" in view)
man = (ROOT / "docs/manuals/PLATFORM_ROLES.md").read_text()
check("the guide lists the role and says asking is its", "3.9 Problem Solver" in man and "Asking is the **Problem Solver**'s" in man)

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
