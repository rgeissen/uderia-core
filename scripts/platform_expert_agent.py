#!/usr/bin/env python3
"""Provision the Platform Expert agent (@PLATFORM) on an instance, through its REST API.

    python scripts/platform_expert_agent.py ensure [--llm <llm config id>] [--rest]
    python scripts/platform_expert_agent.py ensure --tag BUILDER --roles guide,agent-builder --writes review
    python scripts/platform_expert_agent.py status
    python scripts/platform_expert_agent.py teardown

    UDERIA_BASE (default http://localhost:5050) · UDERIA_USER / UDERIA_PASS (default admin/admin)

The agent answers questions about Uderia itself — the user's agents, their trust state, the
user's usage — through TDA_Platform, following the platform-playbooks progressive skill:

  class        Ideate (llm_only), no MCP server — Ideate + tools via its components, the lane
               the live probe measured reliable (docs/Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md §15)
  roles        guide (components/builtin/platform/roles/guide.json) — explains the estate, reads only
  components   platform (TDA_Platform) + skill_library (TDA_SkillLibrary) + memory; every other
               action component explicitly off (agent_packs/common/component_config.py)
  skills       platform-playbooks active (its root on every turn, playbooks read on demand);
               platform-expert follows the platform component as its companion
  model        --llm, else the LLM of the user's default agent

`ensure` creates or updates the agent by its tag and runs its configuration checks. `--rest`
also opens it to REST use (appending it to the consumption list — read, append, verify). It
never signs the agent: that is the owner's act.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent_packs.common.component_config import focused_component_config  # noqa: E402

BASE = os.environ.get("UDERIA_BASE", "http://localhost:5050").rstrip("/")
USER = os.environ.get("UDERIA_USER", "admin")
PASS = os.environ.get("UDERIA_PASS", "admin")
TAG = "PLATFORM"
_NAMES = {"PLATFORM": ("Platform Expert", "Answers questions about Uderia itself — your agents, their trust state and "
                       "your usage — through the platform's own operations, following the platform playbooks. Reads "
                       "only: a person's acts are named, never done."),
          "BUILDER": ("Agent Builder", "Builds agents from what you have — class, model, data source — and submits "
                      "each for your approval in the Assurance Inbox; nothing is created until you approve.")}


def spec(llm_id: str, tag: str = TAG, roles=("guide",), writes: str = "off") -> dict:
    components = focused_component_config(memory=True, extra_enabled=["skill_library"])
    # The agent's roles (components/builtin/platform/roles/<id>.json) — the Guide explains the estate, reads only.
    components["platform"] = {"enabled": True, "intensity": "medium", "roles": list(roles)}
    name, description = _NAMES.get(tag, (tag.title(), f"A platform agent with the roles {', '.join(roles)}."))
    out = {
        "tag": tag,
        "name": name,
        "description": description,
        "profile_type": "llm_only",
        "useMcpTools": False,
        "mcpServerId": None,
        "llmConfigurationId": llm_id,
        "componentConfig": components,
        "skillsConfig": {"skills": [{"id": "platform-playbooks", "active": True}]},
        "knowledgeConfig": {"enabled": False, "collections": []},
        "ragCollections": [],
        "classification_mode": "light",
        "contextWindowTypeId": "cwt-default-balanced",
    }
    # The write mode is AUTHORITY (authorityConfig.platformSurface.writes): off · review · allow.
    if writes != "off":
        out["authorityConfig"] = {"platformSurface": {"writes": writes}}
    return out


def req(method, path, token=None, body=None, timeout=300):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            d = json.loads(raw)
        except ValueError:
            d = {"__body__": raw[:400]}
        d["__http_error__"] = e.code
        return d


def login() -> str:
    d = req("POST", "/api/v1/auth/login", body={"username": USER, "password": PASS})
    if "token" not in d:
        sys.exit(f"login failed: {json.dumps(d)[:300]}")
    return d["token"]


def profiles(token):
    d = req("GET", "/api/v1/profiles", token)
    return d.get("profiles") or [], d.get("default_profile_id"), d.get("active_for_consumption_profile_ids") or []


def ensure(token, llm_id=None, rest=False, tag: str = TAG, roles=("guide",), writes: str = "off") -> int:
    profs, default_id, active = profiles(token)
    if not llm_id:
        default = next((p for p in profs if p.get("id") == default_id), None)
        llm_id = (default or {}).get("llmConfigurationId")
        if not llm_id:
            sys.exit("no --llm given and the default agent has no LLM configuration")
    body = spec(llm_id, tag, roles, writes)
    existing = next((p for p in profs if p.get("tag") == tag), None)
    if existing:
        merged = dict(existing)
        merged.update(body)
        if writes != "off":
            merged["authorityConfig"] = {**(existing.get("authorityConfig") or {}),
                                         "platformSurface": {"writes": writes}}
        r = req("PUT", f"/api/v1/profiles/{existing['id']}", token, merged)
        pid, verb = existing["id"], "updated"
    else:
        r = req("POST", "/api/v1/profiles", token, body)
        pid, verb = (r.get("profile") or {}).get("id"), "created"
    if r.get("__http_error__") or not pid:
        sys.exit(f"could not save @{tag}: {json.dumps(r)[:400]}")
    print(f"@{tag} {verb} · {pid} · LLM {llm_id} · roles {', '.join(roles)} · writes {writes}")
    t = req("POST", f"/api/v1/profiles/{pid}/test", token, {})
    bad = {k: v.get("message") for k, v in (t.get("results") or t.get("test_results") or {}).items()
           if isinstance(v, dict) and v.get("status") == "error"}
    print("  configuration checks: " + ("pass" if not bad and not t.get("__http_error__") else f"FAILED {bad or t}"))
    comps = req("GET", f"/api/v1/profiles/{pid}/components", token).get("components") or []
    on = sorted(c.get("component_id") for c in comps if c.get("profile_enabled"))
    print(f"  components on: {', '.join(on)}")
    skills = req("GET", f"/api/v1/profiles/{pid}/skills", token).get("skills") or []
    print("  skills: " + ", ".join(f"{s.get('id')}{' (active)' if s.get('active') else ''}" for s in skills
                                    if s.get("active") or s.get("enabled")))
    if rest and pid not in active:
        r = req("POST", "/api/v1/profiles/set_active_for_consumption", token, {"profile_ids": active + [pid]})
        _, _, after = profiles(token)
        if after != active + [pid]:
            sys.exit(f"the REST list did not take @{tag} in order: {r}")
        print("  opened to REST use (appended to the consumption list)")
    print(f"  not signed — signing @{tag} is the owner's act (Trust)")
    return 0


def status(token) -> int:
    profs, default_id, active = profiles(token)
    p = next((p for p in profs if p.get("tag") == TAG), None)
    if not p:
        print(f"@{TAG} absent")
        return 1
    tr = req("GET", f"/api/v1/trust/agent/{p['id']}", token)
    print(f"@{TAG} · {p['id']} · REST {'open' if p['id'] in active else 'closed'} · "
          f"trust {((tr.get('trust') or {}).get('state')) or '?'}")
    return 0


def teardown(token) -> int:
    profs, _, _ = profiles(token)
    p = next((p for p in profs if p.get("tag") == TAG), None)
    if not p:
        print(f"@{TAG} absent")
        return 0
    r = req("DELETE", f"/api/v1/profiles/{p['id']}", token)
    print(f"@{TAG} delete " + ("ok" if not r.get("__http_error__") else json.dumps(r)[:200]))
    return 0


def main(argv) -> int:
    if not argv or argv[0] not in ("ensure", "status", "teardown"):
        sys.exit(__doc__)
    token = login()
    if argv[0] == "ensure":
        llm = argv[argv.index("--llm") + 1] if "--llm" in argv else None
        tag = argv[argv.index("--tag") + 1].lstrip("@").upper() if "--tag" in argv else TAG
        roles = tuple(argv[argv.index("--roles") + 1].split(",")) if "--roles" in argv else ("guide",)
        writes = argv[argv.index("--writes") + 1] if "--writes" in argv else "off"
        if writes not in ("off", "review", "allow"):
            sys.exit("--writes must be off, review or allow")
        return ensure(token, llm, rest="--rest" in argv, tag=tag, roles=roles, writes=writes)
    return status(token) if argv[0] == "status" else teardown(token)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
