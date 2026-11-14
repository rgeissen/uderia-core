#!/usr/bin/env python3
"""
platform-playbooks — the progressive skill that teaches the use cases of TDA_Platform.

The skill carries the use cases; the component stays generic. That only holds while the two
agree, so this gate ties them together:

  §A  the package — the skill manager loads it as progressive, the payload validates (every
      index entry's file present, sha256 matching), the index and the root's section list
      are what `scripts/platform_playbooks.py build` produces (never hand-edited)
  §B  every playbook has a title, a summary, a "Use for" line and its steps
  §C  every op id a playbook names exists in the SERVED catalog; every op named as a call
      step is one an agent may call at the user tier; every op named but not called is a
      person's act — a renamed route or a changed policy cannot silently break a playbook
  §D  the skill is registered, active, and no component's companion (it is chosen, not
      auto-assigned)
  §E  choosing a playbook for a find — deterministic, on the real playbooks: the right one or
      none (never one on a word every playbook shares; never one of two equals)
  §F  delivering it — find hands the agent the playbook through the skill library's own read
      path: recorded as a read (citation honesty sees it), once per turn, inside the agent's
      anchor grant, only for an agent that holds the skill

Run: PYTHONPATH=src python3 test/test_platform_playbooks.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SECRET_KEY", "platform-playbooks-test")

import platform_playbooks as PB  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


# ── §A the package ──────────────────────────────────────────────────────────────
print("§A the package")
from trusted_data_agent.skills import package as SP  # noqa: E402
from trusted_data_agent.skills.manager import get_skill_manager  # noqa: E402

sm = get_skill_manager()
manifest = sm.get_skill_manifest("platform-playbooks")
check("the skill manager loads it", manifest is not None)
check("…as a progressive skill", SP.is_progressive(manifest))
files = sm.get_skill_library_files("platform-playbooks") if manifest else {}
check("its library files are served by the index", len(files) == len(PB.section_files()) >= 4, (len(files), len(PB.section_files())))
errs = SP.validate_progressive_payload(manifest, files) if manifest else ["no manifest"]
check("the payload validates (files present, sha256 matching, no duplicate anchors)", errs == [], errs)
check("skill.json's index is exactly what build produces",
      json.loads(PB.MANIFEST.read_text())["uderia"]["progressive"]["index"] == PB.build_index())
check("the root's section list is exactly what build produces (run: scripts/platform_playbooks.py build)",
      PB.ROOT_MD.read_text(encoding="utf-8") == PB.built_root(PB.build_index()))
root = PB.ROOT_MD.read_text(encoding="utf-8")
check("the root names every anchor (the model can only choose what it sees)",
      all(f"`{e['anchor']}`" in root for e in PB.build_index()))
import subprocess  # noqa: E402
try:
    _ignored = subprocess.run(["git", "check-ignore", *[str(f) for f in PB.section_files()]], cwd=ROOT,
                              capture_output=True, text=True).stdout.split()
    check("no playbook file is ignored by git (a clean checkout must hold every indexed file)", _ignored == [], _ignored)
except FileNotFoundError:
    pass   # no git in this environment — the payload check above still holds
check("the root states the fetch discipline", "read that playbook **before**" in root and "Never answer from memory" in root)
check("the root names the reader tool", "TDA_SkillLibrary(action='read'" in root)

# ── §B every playbook ───────────────────────────────────────────────────────────
print("§B every playbook")
for path in PB.section_files():
    text = path.read_text(encoding="utf-8")
    title, summary = PB.title_and_summary(text)
    a = PB.anchor_of(path)
    check(f"{a}: title and one-line summary", bool(title) and bool(summary) and len(summary) <= 200, (title, summary))
    check(f"{a}: says what it is for", "**Use for:**" in text)
    check(f"{a}: has steps", "## Steps" in text and "call `" in text)
    check(f"{a}: no emoji", all(ord(ch) < 0x2600 or ch in "—–→·…" for ch in text), [ch for ch in text if ord(ch) >= 0x2600][:5])

# ── §C op ids against the served catalog ────────────────────────────────────────
print("§C op ids against the served catalog")
logging.disable(logging.CRITICAL)
from trusted_data_agent.main import app  # noqa: E402
logging.disable(logging.NOTSET)
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402

cat = build_catalog(app)
by = cat.by_op
named_all, called_all = set(), set()
for path in PB.section_files() + [PB.ROOT_MD]:
    named, called = PB.op_refs(path.read_text(encoding="utf-8"))
    a = PB.anchor_of(path) if path != PB.ROOT_MD else "root"
    unknown = sorted(n for n in named if n not in by)
    check(f"{a}: every op id it names is in the served catalog", not unknown, unknown)
    # A user-tier agent may be handed any playbook — except the administrators' (admin/), which
    # only the Admin Monitor is taught (checked below), and whose reads are administrator-tier.
    _tiers = ("user", "admin") if a.startswith("admin/") else ("user",)
    not_callable = sorted(c for c in called if c in by and not (P.availability(by[c])[0] and by[c].tier in _tiers))
    check(f"{a}: every op it calls may be called by an agent at the user tier", not not_callable,
          [(c, P.availability(by[c])[1], by[c].tier) for c in not_callable])
    other = sorted(n for n in named - called if n in by and by[n].effect != "human_only")
    check(f"{a}: an op it names without calling is a person's act", not other, other)
    named_all |= named
    called_all |= called
check("the playbooks call at least the operations of their use cases",
      {"profiles.overview", "trust.agent", "trust.agents", "consumption.summary"} <= called_all,
      sorted(called_all))
check("the gate sees the person's acts it should", {"trust.agent_sign_off", "trust.agent_attest_deployment"} <= named_all - called_all)
check("the op-id pattern ignores field names and anchors",
      PB.op_refs("`state` `trust/explain-agent` call `trust.agent` `trust.agent_sign_off`")
      == ({"trust.agent", "trust.agent_sign_off"}, {"trust.agent"}))

from trusted_data_agent.core import platform_roles as _PR  # noqa: E402
_admin_taught = sorted(r.id for r in _PR.load().roles.values()
                       if any(r.teaches("platform-playbooks", PB.anchor_of(f)) for f in PB.section_files()
                              if PB.anchor_of(f).startswith("admin/")))
check("only the Admin Monitor is taught the administrators' playbooks", _admin_taught == ["admin-monitor"], _admin_taught)

# ── §D registration ─────────────────────────────────────────────────────────────
print("§D registration")
reg = json.loads((ROOT / "skills/skill_registry.json").read_text())
entry = [s for s in reg["skills"] if s.get("skill_id") == "platform-playbooks"]
check("registered once, active", len(entry) == 1 and entry[0].get("status") == "active", entry)
check("no component's companion: an agent is GIVEN the playbooks, they never auto-assign", entry and "component_id" not in entry[0])

# ── §E choosing a playbook for a find ───────────────────────────────────────────
print("§E choosing a playbook")
sections = []
for path in PB.section_files():
    _t = path.read_text(encoding="utf-8")
    _ti, _su = PB.title_and_summary(_t)
    sections.append(P.playbook_section(PB.anchor_of(path), _ti, _su, _t, cat=cat))
check("every playbook is read as a playbook (it calls platform operations)",
      all(sections) and len(sections) == len(PB.section_files()) >= 6, len(sections))
CHANGES = {"building/create-agent", "building/configure-agent", "knowledge/add-documents", "knowledge/clean-up",
           "automation/create-flow", "automation/run-flow", "automation/schedule", "quality/rescore",
           "insight/dashboards", "insight/canvases", "publishing/bundle", "building/bind-graph",
           "knowledge/graph-governance", "solving/gaps"}
check("a playbook that submits a change is marked as one; a reading one is not",
      {s["anchor"] for s in sections if s["changes"]} == CHANGES,
      sorted(s["anchor"] for s in sections if s["changes"]))
check("a section that calls no operation is not a playbook", P.playbook_section("x", "X", "x", "# X\n\nno calls here") is None)
EXPECT = {
    "agent components": "agents/inspect", "agent skills": "agents/inspect", "TCOPT components": "agents/inspect",
    "trust state of an agent": "trust/explain-agent", "is @TCOPT trusted? explain why": "trust/explain-agent",
    "why is @UDERIA changed": "trust/explain-agent", "sign the agent": "trust/explain-agent",
    "which of my agents need signing": "trust/my-agents", "trust overview": "trust/my-agents",
    "how much have I used this period": "usage/this-period", "my usage": "usage/this-period",
    "am I close to my limits": "usage/this-period",
    "create an agent": "building/create-agent", "build me a new agent": "building/create-agent",
    "build an agent that answers from our documentation": "building/create-agent",
    # a request to change an agent reads its nouns like a question about it — the verb decides
    "add the chart component to @SALES": "building/configure-agent",
    "turn off dashboards for my agent": "building/configure-agent",
    "add a skill to my agent": "building/configure-agent",
    "let @COORD also consult @HR": "building/configure-agent",
    "which components does @SALES have": "agents/inspect",
    # the Knowledge Curator's playbooks
    "add the handbook to the Policies repository": "knowledge/add-documents",
    "create a repository for our policies": "knowledge/add-documents",
    "is my Policies repository healthy": "knowledge/health",
    "which documents in Sales are rated badly": "knowledge/health",
    "quarantine the vendor FAQ": "knowledge/clean-up",
    "remove the old terms from Policies": "knowledge/clean-up",
    "sync the documentation repository": "knowledge/clean-up",
    "what does the Policies repository find for travel expenses": "knowledge/search",
    "does my documentation cover SSO": "knowledge/search",
    # the Automation Builder's playbooks
    "create a pipeline of three agents": "automation/create-flow",
    "run the quarterly answer flow": "automation/run-flow",
    "every weekday at 9 ask @SALES for yesterday's orders": "automation/schedule",
    "pause the morning report": "automation/schedule",
    "what is scheduled": "automation/status",
    "did the morning report run": "automation/status",
    # the Quality Analyst's playbooks
    "which checks fail most": "quality/whats-failing",
    "why is @SALES failing": "quality/whats-failing",
    "when did quality drop": "quality/regression",
    "did my change break @COORD": "quality/regression",
    "what needs my attention": "quality/inbox",
    "anything waiting for my approval": "quality/inbox",
    "score the last turn of that conversation again": "quality/rescore",
    "what would enforce grounding have done to @SALES": "quality/rescore",
    # the Insight Builder's playbooks
    "make a dashboard called Weekly Sales": "insight/dashboards",
    "refresh the revenue tile": "insight/dashboards",
    "which data products are on the Sales graph": "insight/data-products",
    "what SQL would revenue by region last quarter run": "insight/data-products",
    "rename the SQL draft canvas": "insight/canvases",
    "go back to the previous version of my report canvas": "insight/canvases",
    # the Publisher's playbooks
    "bundle @SALES and @DOCS into a pack": "publishing/bundle",
    "is Sales Assist ready to publish": "publishing/readiness",
    "what blocks my pack": "publishing/readiness",
    "which packs are about policing": "publishing/marketplace",
    "what would the Policing pack need to run here": "publishing/marketplace",
    # the Admin Monitor's playbooks
    "how is the deployment doing": "admin/health",
    "when was the last backup": "admin/health",
    "which models run without a price": "admin/costs",
    "what are we spending": "admin/costs",
    # solving problems across agents
    "why did claims rise where incidents fell": "solving/route", "who can answer this question": "solving/route",
    "get a second opinion": "solving/second-opinion", "check this answer with @FINANCE": "solving/second-opinion",
    "what am I missing to answer this": "solving/gaps", "nobody can answer this": "solving/gaps",
    # knowledge graphs
    "what is in my Policing graph": "knowledge/graphs", "what is Customer connected to": "knowledge/graphs",
    "is my ontology conformant": "knowledge/graph-governance", "save the graph before I change it": "knowledge/graph-governance",
    "let @SALES use the Retail graph": "building/bind-graph", "which agents have the Policing graph": "building/bind-graph",
    # seen live as find queries: a tag, and an op id given as the query
    "is @TCOPT trusted": "trust/explain-agent", "trust state of @TCOPT": "trust/explain-agent",
    "trust.agents": "trust/my-agents", "inspect @TCOPT": "agents/inspect", "sign agent @TCOPT": "trust/explain-agent",
    # no playbook covers these — nothing is sent
    "make an agent my default": None, "open to REST use": None, "delete an agent": None,
    # the trust overview lists every one of the user's agents (with its state) — a fair playbook for it
    "which agents do I have": "trust/my-agents",
    # the SQL behind a measure is what the data-products playbook explains (it compiles, never runs)
    "metric sql": "insight/data-products",
}
for q, want in EXPECT.items():
    found = [f["op"] for f in P.find(q, "user", cat=cat)["found"]]
    got = P.rank_playbooks(q, sections, found)[0]
    check(f"{q!r} → {want}", got == want, got)
two = [{"anchor": "a", "title": "Alpha report", "summary": "", "use_for": "", "calls": ["x.y"]},
       {"anchor": "b", "title": "Alpha report", "summary": "", "use_for": "", "calls": ["x.z"]},
       {"anchor": "c", "title": "Other", "summary": "", "use_for": "", "calls": ["x.w"]},
       {"anchor": "d", "title": "Unrelated", "summary": "", "use_for": "", "calls": ["x.v"]}]
check("two equal playbooks: neither is sent, both are named", P.rank_playbooks("alpha", two, []) == (None, ["a", "b"]))
check("…find's top operation breaks the tie", P.rank_playbooks("alpha", two, ["x.z"])[0] == "b")
check("…its first operation counts more than its second", P.rank_playbooks("alpha", two, ["x.z", "x.y"])[0] == "b")
_tagged = [dict(two[0], use_for='"is @X fine"'), dict(two[1]), two[2], two[3]]
check("a query that is an op id selects the one playbook that calls it",
      P.rank_playbooks("x.z", two, [])[0] == "b" and P.rank_playbooks("x.v", two, [])[0] == "d")
check("a query naming one agent favours the playbook about one agent", P.rank_playbooks("alpha @X", _tagged, [])[0] == "a")

# ── §F delivering it ────────────────────────────────────────────────────────────
print("§F delivering it")
import asyncio  # noqa: E402
from components.builtin.platform.handler import PlatformHandler, _DELIVERED  # noqa: E402
from trusted_data_agent.agent.honesty.primitives import sections_read  # noqa: E402

PlatformHandler._enabled_here = staticmethod(lambda c: True)
_orig_tier = P.user_tier
P.user_tier = lambda uid: "user"
H = PlatformHandler()
agent = {"id": "p-x", "tag": "PX", "profile_type": "llm_only",
         "componentConfig": {"platform": {"enabled": True}, "skill_library": {"enabled": True}},
         "skillsConfig": {"skills": [{"id": "platform-playbooks", "active": True}]}}


def find_as(profile, query, session="s-1", turn=1):
    return asyncio.run(H.process({"action": "find", "query": query},
                                 {"profile": profile, "user_uuid": "u-x", "session_id": session, "current_turn_number": turn}))


try:
    _DELIVERED.clear()
    r = find_as(agent, "which of my agents need signing")
    pb = r.spec.get("playbook") or {}
    check("find hands over the playbook that covers it", pb.get("anchor") == "trust/my-agents"
          and "# Trust across my agents" in (pb.get("text") or "") and "read for you" in pb.get("instruction", ""), pb.get("anchor"))
    check("…with the operations find found, unchanged", r.spec.get("found") and r.spec["found"][0]["op"] == "trust.agents")
    evts = r.metadata.get("_component_llm_events") or []
    check("…recorded as a read: the skill library's own event", any(e.get("type") == "skill_section_loaded" for e in evts), evts)
    check("…which citation honesty counts as read", sections_read([], [{"payload": e} for e in evts]) == {"trust/my-agents"})
    r2 = find_as(agent, "trust overview")
    check("in the same turn it is pointed to, not sent again",
          (r2.spec.get("playbook") or {}).get("anchor") == "trust/my-agents" and "text" not in r2.spec["playbook"]
          and not r2.metadata.get("_component_llm_events"), r2.spec.get("playbook"))
    r3 = find_as(agent, "trust overview", turn=2)
    check("…and sent again in the next turn", "text" in (r3.spec.get("playbook") or {}))
    r4 = find_as(agent, "delete an agent")
    check("no playbook covers it: none is sent", "playbook" not in r4.spec and not r4.metadata.get("_component_llm_events"), r4.spec.get("playbook"))
    granted = dict(agent, skillsConfig={"skills": [{"id": "platform-playbooks", "active": True, "anchors": ["usage/*"]}]})
    r5 = find_as(granted, "which of my agents need signing", session="s-2")
    check("outside the agent's anchor grant nothing is sent", "playbook" not in r5.spec, r5.spec.get("playbook"))
    r6 = find_as(granted, "how much have I used this period", session="s-2")
    check("…inside it, it is", (r6.spec.get("playbook") or {}).get("anchor") == "usage/this-period")
    bare = dict(agent, skillsConfig={"skills": []})
    r7 = find_as(bare, "which of my agents need signing", session="s-3")
    check("an agent without the playbooks gets plain find results", "playbook" not in r7.spec and r7.spec.get("found"))
    inactive = dict(agent, skillsConfig={"skills": [{"id": "platform-playbooks", "active": False}]})
    check("…as does one where the skill is not active", "playbook" not in find_as(inactive, "trust overview", session="s-4").spec)
    rc = asyncio.run(H.process({"action": "call", "op": "agents/inspect", "args": {"profile_id": "@X"}},
                               {"profile": agent, "user_uuid": "u-x", "session_id": "s-5", "current_turn_number": 1}))
    check("a call naming a playbook's anchor hands the playbook over (not 'unknown operation')",
          (rc.spec.get("playbook") or {}).get("anchor") == "agents/inspect" and "# Inspect an agent" in rc.spec["playbook"].get("text", "")
          and "is a playbook, not an operation" in rc.spec["playbook"]["instruction"] and not rc.spec.get("error"), rc.spec)
    check("…recorded as a read", any(e.get("type") == "skill_section_loaded" for e in (rc.metadata.get("_component_llm_events") or [])))
    rn = asyncio.run(H.process({"action": "call", "op": "nope/nothing"},
                               {"profile": agent, "user_uuid": "u-x", "session_id": "s-5", "current_turn_number": 1}))
    check("…an anchor the agent does not hold stays an unknown operation", "unknown operation" in (rn.spec.get("error") or ""), rn.spec)
finally:
    P.user_tier = _orig_tier

print(f"\n{'ALL PASS' if not failed else 'FAILED'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
