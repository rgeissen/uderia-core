#!/usr/bin/env python3
"""Agents asking other agents — TDA_Platform's `ask` (core/platform_asking.py).

  §A  the deployment's settings — off by default; limits clamped to their ranges
  §B  every refusal, before anything runs — asking off, no question, an unknown agent, the agent
      itself, an agent not on the asking agent's list, a chain at its depth limit, a turn at its
      ask limit; and an agent that is not activated (refused by the real session route) — each a
      sentence the asking agent can pass on
  §C  an ask — a session for the asked agent through the session route, who asked recorded on it
      (depth + 1) BEFORE the question is sent, the question through the query route, the answer
      from the task; a slow answer is left running and said so; a failed record asks nothing
  §D  the tool — `ask` with agent and question, from the names a model writes
  §E  the contract — the settings route is a read, changing them an administrator's act; adding
      an agent to the list is a widening the downgrade guard reports; the list is this
      instance's ids, so a pack never carries it and an import keeps what the instance held
  §F  the surfaces — the editor writes the list only once it was shown; the admin tab is
      registered and loaded

No LLM calls, no writes. Run: python test/test_platform_asking.py
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
os.environ.setdefault("SECRET_KEY", "platform-asking-test")
logging.disable(logging.CRITICAL)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


from trusted_data_agent.core import platform_asking as A  # noqa: E402
import trusted_data_agent.components.settings as CS  # noqa: E402
import trusted_data_agent.core.config_manager as CM  # noqa: E402
from trusted_data_agent.core import session_manager as SM  # noqa: E402

STORE: dict = {}
CS.get_component_settings = lambda: dict(STORE)
CS.save_component_settings = lambda upd, admin: STORE.update(upd)

# ── §A settings ─────────────────────────────────────────────────────────────────
print("§A settings")
d = A.settings()
check("off by default, with safe limits", d == {"enabled": False, "max_depth": 1, "max_per_turn": 3, "timeout_s": 180}, d)
out = A.save_settings({"enabled": True, "max_depth": 9, "max_per_turn": 0, "timeout_s": "abc", "other": 1}, "admin")
check("limits are clamped to their ranges; an unknown name is ignored",
      out == {"enabled": True, "max_depth": 3, "max_per_turn": 1, "timeout_s": 180} and "other" not in STORE, out)
check("stored as component settings", STORE.get("platform_ask_enabled") is True and STORE.get("platform_ask_max_depth") == 3)
CS.get_component_settings = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
check("unreadable settings read as off", A.settings()["enabled"] is False)
CS.get_component_settings = lambda: dict(STORE)

# ── fixtures ────────────────────────────────────────────────────────────────────
PROFILES = {
    "p-ask": {"id": "p-ask", "tag": "ASKER", "authorityConfig": {"platformSurface": {"ask": ["p-sales"]}},
              "componentConfig": {"platform": {"enabled": True, "roles": ["problem-solver"]}}},
    "p-guide": {"id": "p-guide", "tag": "GUIDE", "authorityConfig": {"platformSurface": {"ask": ["p-sales"]}},
                "componentConfig": {"platform": {"enabled": True, "roles": ["guide"]}}},
    "p-sales": {"id": "p-sales", "tag": "SALES", "name": "Sales"},
    "p-hr": {"id": "p-hr", "tag": "HR", "name": "HR"},
}


class _FakeCM:
    def get_profiles(self, u):
        return list(PROFILES.values()) if u == "u" else []

    def get_profile(self, pid, u):
        return PROFILES.get(pid) if u == "u" else None


CM.get_config_manager = lambda: _FakeCM()
RECORDED = []


async def _record(u, sid, info):
    RECORDED.append((u, sid, info))
SM.set_platform_asked_by = _record
DEPTH = {"v": 0}


async def _depth(u, sid):
    return DEPTH["v"]
A._depth = _depth


def make_route(task_states=("complete",), session_status=201, answer="Revenue was 4.2M in Q3."):
    calls = []
    states = list(task_states)

    async def route(app, method, path, user, body=None):
        calls.append((method, path, body))
        if path == "/api/v1/sessions":
            if session_status >= 300:
                return session_status, {"error": "Profile is not activated", "code": "profile_not_activated"}
            return session_status, {"session_id": "s-child"}
        if path.endswith("/query"):
            return 202, {"task_id": "t-1"}
        st = states.pop(0) if states else "processing"
        return 200, {"status": st, "result": {"final_answer_text": answer}} if st == "complete" else {"status": st}
    return route, calls


def run(target, question="What was revenue in Q3?", *, route=None, session_id="s-parent", turn=1, asker="p-ask"):
    return asyncio.run(A.ask(PROFILES[asker], target, question, "u", session_id=session_id, turn_id=turn,
                             app=object(), route=route or make_route()[0], poll_s=0))


# ── §B refusals ─────────────────────────────────────────────────────────────────
print("§B refusals")
STORE.clear()
r = run("@SALES")
check("asking off: refused, naming where an administrator turns it on",
      not r["ok"] and "turned off" in r["error"] and "Administration" in r["error"], r)
A.save_settings({"enabled": True, "max_depth": 1, "max_per_turn": 2, "timeout_s": 30}, "admin")
route, calls = make_route()
r = run("@SALES", route=route, asker="p-guide")
check("an agent without a role that asks: refused, naming the Problem Solver, nothing run",
      not r["ok"] and "Problem Solver" in r["error"] and calls == [], r)
for target, question, want in (("@SALES", "  ", "needs 'question'"), ("@NOPE", "q?", "no agent of yours"),
                               ("@ASKER", "q?", "does not ask itself"), ("@HR", "q?", "may not ask @HR")):
    r = run(target, question, route=route)
    check(f"{target} / {question.strip() or 'no question'}: refused — {want}", not r["ok"] and want in r["error"], r)
check("…and nothing was run for any of them", calls == [], calls)
r = run("@HR", "q?")
check("the refusal names whom it may ask", "it may ask: @SALES" in r["error"], r)
DEPTH["v"] = 1
route, calls = make_route()
r = run("@SALES", route=route)
check("a conversation that was itself asked, at the depth limit: refused, nothing run",
      not r["ok"] and "chains of 1" in r["error"] and calls == [], r)
DEPTH["v"] = 0
r = run("@SALES", route=make_route()[0], session_id="s-limit", turn=7)
r = run("@SALES", route=make_route()[0], session_id="s-limit", turn=7)
r3 = run("@SALES", route=make_route()[0], session_id="s-limit", turn=7)
check("a turn at its ask limit: refused", r["ok"] and not r3["ok"] and "limit" in r3["error"], r3)
check("…another turn may ask again", run("@SALES", session_id="s-limit", turn=8)["ok"])
route, calls = make_route(session_status=403)
r = run("@SALES", route=route, session_id="s-act")
check("an asked agent that is not activated: refused by the session route, said in words",
      not r["ok"] and "not activated for REST use" in r["error"] and len(calls) == 1, r)

# ── §C an ask ───────────────────────────────────────────────────────────────────
print("§C an ask")
RECORDED.clear()
route, calls = make_route(task_states=("processing", "processing", "complete"))
r = run("@SALES", route=route, session_id="s-run")
check("the answer comes back, naming who answered and their conversation",
      r["ok"] and r["answer"] == "Revenue was 4.2M in Q3." and r["agent"] == "@SALES" and r["session_id"] == "s-child", r)
check("…with the instruction to say who answered", "say that @SALES answered" in r.get("instruction", ""))
check("a session for the asked agent, then its question, then the task — through the routes",
      [c[:2] for c in calls[:2]] == [("POST", "/api/v1/sessions"), ("POST", "/api/v1/sessions/s-child/query")]
      and calls[0][2] == {"profile_id": "p-sales"} and calls[1][2] == {"prompt": "What was revenue in Q3?", "profile_id": "p-sales", "source": "ask"}
      and all(c[1] == "/api/v1/tasks/t-1" for c in calls[2:]), calls)
check("who asked is recorded on the asked conversation, one level deeper",
      RECORDED and RECORDED[-1][1] == "s-child" and RECORDED[-1][2]["depth"] == 1
      and RECORDED[-1][2]["session_id"] == "s-run" and RECORDED[-1][2]["tag"] == "ASKER", RECORDED)
r = run("@SALES", route=make_route(answer="x" * (A.ANSWER_CHARS + 50))[0], session_id="s-long")
check("a long answer is shortened, and says where the rest is", len(r["answer"]) == A.ANSWER_CHARS and "shortened" in r["note"])
r = run("@SALES", route=make_route(task_states=("failed",))[0], session_id="s-fail")
check("a failed turn is reported as the asked agent's failure", not r["ok"] and "could not answer" in r["error"])
A.save_settings({"timeout_s": 30}, "admin")
_mono = A.time.monotonic
_t = {"v": 0.0}


def _tick():
    _t["v"] += 20.0
    return _t["v"]
A.time.monotonic = _tick
try:
    r = run("@SALES", route=make_route(task_states=())[0], session_id="s-slow")
finally:
    A.time.monotonic = _mono
check("a slow answer is left running — said so, not asked again", not r["ok"] and r.get("status") == "running"
      and "do not ask again" in r["error"] and r["session_id"] == "s-child", r)


async def _no_record(u, sid, info):
    raise LookupError("gone")
SM.set_platform_asked_by = _no_record
route, calls = make_route()
r = run("@SALES", route=route, session_id="s-norec")
check("when who asked cannot be recorded, the question is not sent", not r["ok"] and len(calls) == 1, calls)
SM.set_platform_asked_by = _record

# ── the find hint ───────────────────────────────────────────────────────────────
print("§C2 the find hint")
from trusted_data_agent.core import platform_roles as _R  # noqa: E402
PROFILES["p-sales"]["description"] = "Answers questions about sales figures."
_ps_roles = _R.roles_of(PROFILES["p-ask"])
h = A.find_hint(PROFILES["p-ask"], _ps_roles, "u")
check("an agent that may ask meets the agents it may ask, with what each is for, in every find",
      h and h["agents"] == [{"agent": "@SALES", "name": "Sales", "for": "Answers questions about sales figures."}]
      and "instead of answering from memory" in h["instruction"], h)
check("no hint without a role that asks", A.find_hint(PROFILES["p-guide"], _R.roles_of(PROFILES["p-guide"]), "u") is None)
check("no hint with an empty list", A.find_hint({**PROFILES["p-ask"], "authorityConfig": {}}, _ps_roles, "u") is None)
_on = dict(STORE); STORE["platform_ask_enabled"] = False
check("no hint while the deployment does not allow asking", A.find_hint(PROFILES["p-ask"], _ps_roles, "u") is None)
STORE.clear(); STORE.update(_on)
hsrc = (ROOT / "components/builtin/platform/handler.py").read_text()
check("the tool's find carries the hint", 'spec["can_ask"] = hint' in hsrc)

# ── §D the tool ─────────────────────────────────────────────────────────────────
print("§D the tool")
from components.builtin.platform.handler import normalize, PlatformHandler  # noqa: E402
h = PlatformHandler()
for args, label in (({"action": "ask", "agent": "@SALES", "question": "q?"}, "agent and question"),
                    ({"action": "ask", "profile_id": "@SALES", "query": "q?"}, "profile_id and query"),
                    ({"action": "ask", "args": {"agent": "@SALES", "question": "q?"}}, "inside args"),
                    ({"action": "ask", "to": "@SALES", "prompt": "q?"}, "to and prompt")):
    n = normalize(args)
    check(f"ask from {label}", n.get("action") == "ask" and n.get("agent") == "@SALES" and n.get("question") == "q?", n)
check("ask without a question is refused by validation", not h.validate_arguments({"action": "ask", "agent": "@SALES"})[0])
import json  # noqa: E402
man = json.loads((ROOT / "components/builtin/platform/manifest.json").read_text())
check("the tool contract names ask, agent and question",
      "'ask'" in man["tool_definition"]["args"]["action"]["description"]
      and {"agent", "question"} <= set(man["tool_definition"]["args"]))
check("the component stays off for every agent until its owner turns it on", man["profile_defaults"]["enabled_for"] == [])

# ── §E the contract ─────────────────────────────────────────────────────────────
print("§E the contract")
from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
cat = build_catalog(create_app())
check("reading the settings is a read", cat.by_op["platform.asking"].effect == "read")
o = cat.by_op["platform.asking_set"]
check("changing them is an administrator's act, never callable by an agent",
      o.effect == "human_only" and o.tier == "admin" and P.availability(o)[0] is False)
from trusted_data_agent.core import profile_config_contract as C  # noqa: E402
old = {"authorityConfig": {"platformSurface": {"writes": "review", "ask": ["p-sales"]}}}
new = {"authorityConfig": {"platformSurface": {"writes": "review", "ask": ["p-sales", "p-hr"]}}}
fields = [g.path for g in C.find_downgrades(old, new)]
check("adding an agent to the list is a widening", "platformSurface.ask" in fields, fields)
check("removing one is not", "platformSurface.ask" not in [g.path for g in C._authority_downgrades(new["authorityConfig"], old["authorityConfig"])])
m = C.project_to_manifest(new)
check("a pack never carries the list (this instance's ids)",
      "ask" not in ((m.get("authorityConfig") or {}).get("platformSurface") or {}) and
      ((m.get("authorityConfig") or {}).get("platformSurface") or {}).get("writes") == "review", m)
target = {"authorityConfig": {"platformSurface": {"writes": "off", "ask": ["p-sales"]}}}
C.apply_from_manifest({"authorityConfig": {"platformSurface": {"writes": "review"}}}, target)
ps = target["authorityConfig"]["platformSurface"]
check("an import keeps the list this instance held, and takes the rest", ps.get("ask") == ["p-sales"] and ps.get("writes") == "review", ps)
check("asking reads the list from authority, not capability",
      A.askable(PROFILES["p-ask"]) == ["p-sales"] and A.askable({"componentConfig": {"platform": {"ask": ["x"]}}}) == [])

# ── §F the surfaces ─────────────────────────────────────────────────────────────
print("§F the surfaces")
ed = (ROOT / "static/js/handlers/configurationHandler.js").read_text()
check("the editor shows the list only while asking is on, and writes it only then",
      "askBox.dataset.enabled === '1'" in ed and "/api/v1/platform/asking" in ed and "cpr-ask-cb" in ed)
check("the editor says a non-activated agent cannot be asked yet", "not activated, cannot be asked yet" in ed)
adm = (ROOT / "static/js/handlers/platformAdmin.js").read_text()
check("the admin page registers the Agent asking tab for the Platform component",
      "window.ComponentAdminTabs.platform" in adm and "'Agent asking'" in adm)
check("…and the page loads it", "js/handlers/platformAdmin.js" in (ROOT / "templates/index.html").read_text())
check("no emoji in the admin tab", all(ord(ch) < 0x2190 or 0x2190 <= ord(ch) < 0x2600 for ch in adm))

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
