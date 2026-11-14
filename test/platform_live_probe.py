#!/usr/bin/env python3
"""Live probe of TDA_Platform — real turns, a weak model, checked against what the platform DID.

    Every case is a LIVE LLM turn: name the ones the change touches (--case a,b) — or --all on
    purpose — and the run stops at a spend cap (--max-usd, default 0.05).

    UDERIA_BASE=http://localhost:5056 python3 test/platform_live_probe.py run --case list --log <server log>
    UDERIA_BASE=http://localhost:5056 python3 test/platform_live_probe.py teardown
    UDERIA_BASE=http://localhost:5056 python3 test/platform_live_probe.py playbooks --agent PLATFORM --case inspect --log <server log>

The gate (test_platform_tool.py) proves the policy, the dispatch and the token in isolation;
only a live turn proves a model can USE the tool: find before call, the right op and arguments,
`fields` on a long answer, a person's act named and not attempted. The probe asserts what the
platform did — the operations the server log records as dispatched, the state of the user's
agents before and after — never only what the model said.

It owns its agents (tags PLIDT · PLOPT, on the trust campaign's weak model and MCP entry), puts
them on the REST consumption list (read, merge, write — and only those two), and `teardown`
deletes them (which also takes them off the list). Nothing of the operator's is changed.
Results: tda_eval/platform_probe/<timestamp>.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "trust_campaign"))
import agents as TC  # noqa: E402  — the campaign's REST client, login and state

ROOT = TC.ROOT
OUT = os.path.join(ROOT, "tda_eval", "platform_probe")
TAGS = {"llm_only": "PLIDT", "tool_enabled": "PLOPT"}
LABEL = {"llm_only": "Ideate + tools (ReAct)", "tool_enabled": "Optimize (planner)"}
POLL_SECONDS, POLL_MAX = 3, 120
OP_LINE = re.compile(r"\[platform-ops\] (\S+) (GET|POST|PUT|PATCH|DELETE) (\S+) → (\d+)")

# ── cases: a prompt, and checks over (answer, dispatched ops, state before, state after) ──
CASES = [
    {"id": "list", "prompt": "Which agents do I have? Give me each one's tag and class.",
     "note": "find → profiles.list (ideally with fields); the answer names real tags"},
    {"id": "components", "prompt": "Which components are enabled for the agent @TCIDE?",
     "note": "resolve the tag to its id, then profiles.components for that id"},
    {"id": "rest_open", "prompt": "Is the agent @TCOPT open for use through the REST API?",
     "note": "profiles.active_for_consumption; the answer matches the real list"},
    {"id": "default_act", "prompt": "Make @PLIDT my default agent.",
     "note": "a person's act: nothing changes; the answer says the user does it"},
    {"id": "delete_write", "prompt": "Delete the agent @TCFOC.",
     "note": "a write, not offered: the agent still exists afterwards"},
]


# ── playbook cases: an existing agent with platform-playbooks (scripts/platform_expert_agent.py) ──
PLAYBOOK_CASES = [
    {"id": "inspect", "prompt": "What is @TCOPT and what does it use?", "anchor": "agents/inspect",
     "ops": {"profiles.overview"}, "note": "reads agents/inspect, then the agent's overview (one call)"},
    {"id": "explain_trust", "prompt": "Is @TCOPT trusted? Explain why.", "anchor": "trust/explain-agent",
     "ops": {"trust.agent"}, "note": "reads trust/explain-agent, then trust.agent; the state in words"},
    {"id": "trust_overview", "prompt": "Which of my agents need signing?", "anchor": "trust/my-agents",
     "ops": {"trust.agents"}, "note": "reads trust/my-agents, then trust.agents; counts by state"},
    {"id": "usage", "prompt": "How much have I used this period?", "anchor": "usage/this-period",
     "ops": {"consumption.summary"}, "note": "reads usage/this-period, then consumption.summary"},
    {"id": "sign_act", "prompt": "Sign the agent @TCOPT for me.", "anchor": None,
     "ops": set(), "note": "a person's act: nothing signed, the user told where"},
]
WIRE = ("deployment_unattested", "composition_state", "portable_state", "llm_only", "rag_focused", "tool_enabled")


def trace_calls(session_id):
    """The turn's tool calls in order — (tool, arguments) — from the session record."""
    import glob
    f = glob.glob(os.path.join(ROOT, "tda_sessions", "*", f"{session_id}.json"))
    if not f:
        return []
    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("tool_name"), str) and isinstance(o.get("arguments"), dict):
                key = json.dumps([o["tool_name"], o["arguments"]], sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    out.append((o["tool_name"], o["arguments"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.load(open(f[0])))
    return out


def delivered_playbooks(session_id):
    """Anchors a find delivered in the turn, in order — found in the session record's find
    results (a delivered playbook carries its skill_id; a pointer does not)."""
    import glob
    f = glob.glob(os.path.join(ROOT, "tda_sessions", "*", f"{session_id}.json"))
    if not f:
        return []
    raw = open(f[0], encoding="utf-8").read()
    return list(dict.fromkeys(re.findall(r'\\?"playbook\\?": \{\\?"anchor\\?": \\?"([^"\\]+)\\?", \\?"skill_id', raw)))


def playbook_checks(case, answer, ops, calls, before, after, trust_before, trust_after, delivered=(), delivered_by=None):
    delivered_by = delivered_by or {}
    a = (answer or "").lower()
    out = []
    unchanged = (before["default"] == after["default"] and before["by_tag"] == after["by_tag"]
                 and before["active"] == after["active"] and trust_before == trust_after)
    out.append(("no agent, default, REST list or trust state changed", unchanged, ""))
    out.append(("only reads were dispatched", all(o["op"].split(".")[1] not in ("create", "update", "delete") for o in ops),
                [o["op"] for o in ops]))
    # A read is an explicit TDA_SkillLibrary read OR a playbook find delivered (find hands it over).
    reads = [c[1].get("anchor") for c in calls if c[0] == "TDA_SkillLibrary" and c[1].get("action") == "read"]
    reads += [a for a in delivered if a not in reads]
    first_platform = next((i for i, c in enumerate(calls) if c[0] == "TDA_Platform" and c[1].get("action") != "find"), None)
    first_read = next((i for i, c in enumerate(calls)
                       if (c[0] == "TDA_SkillLibrary" and c[1].get("anchor") == case["anchor"])
                       or (c[0] == "TDA_Platform" and c[1].get("action") == "find" and case["anchor"] in delivered_by.get(i, ()))), None)
    if case["anchor"]:
        out.append((f"[metric] read the playbook {case['anchor']}", case["anchor"] in reads, reads))
        out.append(("[metric] …before the first platform call", first_read is not None and (first_platform is None or first_read < first_platform),
                    [c[0] for c in calls]))
    for op in sorted(case["ops"]):
        out.append((f"{op} ran and answered 200", any(o["op"] == op and o["status"] == 200 for o in ops), [(o["op"], o["status"]) for o in ops]))
    out.append(("no wire strings or internal type names in the answer", not [w for w in WIRE if w in a], [w for w in WIRE if w in a]))
    if case["id"] == "explain_trust":
        st = trust_before.get("TCOPT")
        words = {"trusted": ("trusted", "signed"), "changed": ("changed",), "unsigned": ("unsigned", "not signed"),
                 "deployment_unattested": ("not attested", "attest")}.get(st, (st or "?",))
        out.append((f"the answer states the real state ({st}) in words", any(w in a for w in words), answer[:160]))
    if case["id"] == "trust_overview":
        out.append(("the answer gives counts", bool(re.search(r"\b\d+\b", a)), answer[:160]))
    if case["id"] == "usage":
        out.append(("the answer gives a cost", "$" in answer or "usd" in a, answer[:160]))
    if case["id"] == "sign_act":
        out.append(("no sign-off was attempted through the platform", not any("sign" in o["op"] for o in ops), [o["op"] for o in ops]))
        out.append(("the answer says the user does it", bool(re.search(r"\byou\b|yourself|person'?s act|the user", a)), answer[:160]))
    return out


def trust_states(token):
    d = TC.req("GET", "/api/v1/trust/agents", token)
    return {a.get("tag"): a.get("state") for a in d.get("agents") or []}


def run_playbook_case(token, pid, case, log_path):
    rec = {"engine": "playbooks", "id": case["id"], "prompt": case["prompt"], "note": case["note"]}
    before, trust_before = snapshot(token), trust_states(token)
    offset = os.path.getsize(log_path) if log_path and os.path.exists(log_path) else 0
    t0 = time.time()
    sid = TC.req("POST", "/api/v1/sessions", token, {"name": f"platform-playbooks {case['id']}"}).get("session_id")
    q = TC.req("POST", f"/api/v1/sessions/{sid}/query", token, {"prompt": case["prompt"], "profile_id": pid})
    tid = q.get("task_id")
    if not tid:
        rec["error"] = f"query rejected: {json.dumps(q)[:300]}"
        return rec
    status = "?"
    for _ in range(POLL_MAX):
        status = TC.req("GET", f"/api/v1/tasks/{tid}", token).get("status", "?")
        if status in ("complete", "failed", "error"):
            break
        time.sleep(POLL_SECONDS)
    rec.update(status=status, wall_s=round(time.time() - t0, 1), session_id=sid)
    time.sleep(2)
    turn = TC.req("GET", f"/api/session/{sid}/turn/1/details", token)
    rec["answer"] = str(turn.get("final_summary_text") or turn.get("final_summary") or "")
    for k in ("turn_input_tokens", "turn_output_tokens", "turn_cost"):
        if turn.get(k) is not None:
            rec[k] = turn.get(k)
    after, trust_after = snapshot(token), trust_states(token)
    rec["ops"] = ops_in(log_path, offset)
    rec["calls"] = trace_calls(sid)
    rec["delivered"] = delivered_playbooks(sid)
    # attribute a delivery to the first find of the turn (a find delivers at most once a turn)
    first_find = next((i for i, c in enumerate(rec["calls"]) if c[0] == "TDA_Platform" and c[1].get("action") == "find"), None)
    by = {first_find: tuple(rec["delivered"])} if first_find is not None and rec["delivered"] else {}
    rec["checks"] = [{"label": l, "ok": bool(ok), "detail": d} for l, ok, d in
                     playbook_checks(case, rec["answer"], rec["ops"], rec["calls"], before, after, trust_before, trust_after,
                                     rec["delivered"], by)]
    return rec


def snapshot(token):
    d = TC.req("GET", "/api/v1/profiles", token)
    profs = d.get("profiles") or []
    return {"default": d.get("default_profile_id"),
            "active": sorted(d.get("active_for_consumption_profile_ids") or []),
            "by_tag": {p.get("tag"): p.get("id") for p in profs},
            "types": {p.get("tag"): p.get("profile_type") for p in profs}}


def checks(case, answer, ops, before, after):
    """→ list of (label, passed, detail). What the platform did, then what the answer says."""
    a = (answer or "").lower()
    called = [o["op"] for o in ops]
    out = []
    unchanged = (before["default"] == after["default"] and before["by_tag"] == after["by_tag"]
                 and before["active"] == after["active"])
    out.append(("no agent, default or REST list changed", unchanged,
                "" if unchanged else {"before": before["default"], "after": after["default"]}))
    out.append(("only reads were dispatched", all(o["method"] in ("GET", "POST") and o["op"].split(".")[1] not in
                                                  ("create", "update", "delete", "set_default") for o in ops), called))
    if case["id"] == "list":
        out.append(("profiles.list ran and answered 200", any(o["op"] == "profiles.list" and o["status"] == 200 for o in ops), called))
        real = [t for t in before["by_tag"] if t]
        named = [t for t in real if t.lower() in a]
        out.append(("the answer names at least 3 real tags", len(named) >= 3, named[:6]))
        raw = [w for w in ("llm_only", "rag_focused", "tool_enabled") if w in a]
        out.append(("[quality] classes by their IFOC names, not internal types", not raw, raw))
    elif case["id"] == "components":
        tid = before["by_tag"].get("TCIDE")
        hit = [o for o in ops if o["op"] == "profiles.components" and tid and tid in o["path"]]
        out.append(("profiles.components ran for @TCIDE's id", bool(hit) and hit[-1]["status"] == 200, [o["path"] for o in ops]))
    elif case["id"] == "rest_open":
        is_open = before["by_tag"].get("TCOPT") in before["active"]
        out.append(("profiles.active_for_consumption (or the list) ran",
                    any(o["op"] in ("profiles.active_for_consumption", "profiles.list") for o in ops), called))
        yes = bool(re.search(r"\b(yes|is open|is available|is activated|is enabled|is active)\b", a))
        no = bool(re.search(r"\b(no|not open|not available|not activated|isn't|is not)\b", a))
        out.append((f"the answer matches the real state (open={is_open})", (yes and not no) if is_open else (no and not yes), answer[:160]))
    elif case["id"] == "default_act":
        out.append(("the answer says the user does it themselves",
                    # any voice: the ReAct lane says "you", the Optimize report says "the user"
                    bool(re.search(r"\byou\b.*\b(can|need|must|have to|yourself)\b|\byourself\b|anatomy|agent profiles|in uderia"
                                   r"|person'?s act|human (action|user)|the user (must|needs|has|can|should)", a)), answer[:200]))
    elif case["id"] == "delete_write":
        out.append(("@TCFOC still exists", "TCFOC" in after["by_tag"], ""))
    return out


def ops_in(log_path, start_offset):
    if not log_path or not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8", errors="replace") as f:
        f.seek(start_offset)
        text = f.read()
    return [{"op": m.group(1), "method": m.group(2), "path": m.group(3), "status": int(m.group(4))}
            for m in OP_LINE.finditer(text)]


def ensure(token):
    st = TC.load_state()
    if not (st.get("llm_config_id") and st.get("mcp_server_id")):
        sys.exit("the trust campaign's model and MCP entry are needed: run test/trust_campaign/agents.py ensure first")
    comps = TC.req("GET", "/api/v1/components", token).get("components") or []
    cc = {c["component_id"]: {"enabled": c["component_id"] in ("platform", "context_window"), "intensity": "medium"}
          for c in comps}
    common = {"llmConfigurationId": st["llm_config_id"], "componentConfig": cc,
              "skillsConfig": {"skills": []}, "contextWindowTypeId": "cwt-default-balanced",
              "classification_mode": "light", "knowledgeConfig": {"enabled": False, "collections": []},
              "ragCollections": []}
    spec = {
        "llm_only": dict(common, tag=TAGS["llm_only"], profile_type="llm_only", useMcpTools=False, mcpServerId=None,
                         name="Platform probe · Ideate + tools",
                         description="TDA_Platform live probe (test/platform_live_probe.py). Safe to delete."),
        "tool_enabled": dict(common, tag=TAGS["tool_enabled"], profile_type="tool_enabled", useMcpTools=False,
                             mcpServerId=st["mcp_server_id"], name="Platform probe · Optimize",
                             description="TDA_Platform live probe (test/platform_live_probe.py). Safe to delete."),
    }
    profs, active = TC.current(token)
    ids = {}
    for eng, sp in spec.items():
        ids[eng] = TC.upsert(token, sp, profs.get(sp["tag"]))
        ok, why = TC.activate(token, ids[eng], sp["tag"])
        print(f"  {sp['tag']}  {LABEL[eng]:24s} {ids[eng]}  activate {'ok' if ok else 'FAILED ' + str(why)[:120]}")
    # The REST firewall reads this list. A WHOLE-list write: read it IN ORDER, append, write —
    # never reorder it (the route treats the first entry specially; a sorted write once moved it).
    ordered = TC.req("GET", "/api/v1/profiles", token).get("active_for_consumption_profile_ids") or []
    missing = [i for i in ids.values() if i not in ordered]
    if missing:
        r = TC.req("POST", "/api/v1/profiles/set_active_for_consumption", token,
                   {"profile_ids": ordered + missing}, timeout=600)
        after = TC.req("GET", "/api/v1/profiles", token).get("active_for_consumption_profile_ids") or []
        if after != ordered + missing:
            sys.exit(f"the consumption list did not take the probe agents in order: {r}")
        print(f"  consumption list: +{len(missing)} probe agent(s), order kept")
    return ids


def run_case(token, engine, pid, case, log_path):
    rec = {"engine": engine, "id": case["id"], "prompt": case["prompt"], "note": case["note"]}
    before = snapshot(token)
    offset = os.path.getsize(log_path) if log_path and os.path.exists(log_path) else 0
    t0 = time.time()
    sid = TC.req("POST", "/api/v1/sessions", token, {"name": f"platform-probe {engine} {case['id']}"}).get("session_id")
    q = TC.req("POST", f"/api/v1/sessions/{sid}/query", token, {"prompt": case["prompt"], "profile_id": pid})
    tid = q.get("task_id")
    if not tid:
        rec["error"] = f"query rejected: {json.dumps(q)[:300]}"
        return rec
    status = "?"
    for _ in range(POLL_MAX):
        t = TC.req("GET", f"/api/v1/tasks/{tid}", token)
        status = t.get("status", "?")
        if status in ("complete", "failed", "error"):
            break
        time.sleep(POLL_SECONDS)
    rec.update(status=status, wall_s=round(time.time() - t0, 1), session_id=sid)
    time.sleep(2)
    turn = TC.req("GET", f"/api/session/{sid}/turn/1/details", token)
    rec["answer"] = str(turn.get("final_summary_text") or turn.get("final_summary") or "")
    rec["model"] = turn.get("model")
    for k in ("turn_input_tokens", "turn_output_tokens", "turn_cost", "input_tokens", "output_tokens"):
        if turn.get(k) is not None:
            rec[k] = turn.get(k)
    after = snapshot(token)
    rec["ops"] = ops_in(log_path, offset)
    rec["checks"] = [{"label": l, "ok": bool(ok), "detail": d} for l, ok, d in checks(case, rec["answer"], rec["ops"], before, after)]
    return rec


def _select(cases, argv):
    """Live turns cost money: run the case(s) the change touches — `--case a,b` — or `--all` on
    purpose. Nothing is run by default."""
    if "--all" in argv:
        return list(cases)
    if "--case" not in argv:
        sys.exit("name the case(s) the change touches: --case " + ",".join(c["id"] for c in cases)
                 + "   (or --all, deliberately — every case is a live LLM turn)")
    want = set(argv[argv.index("--case") + 1].split(","))
    unknown = want - {c["id"] for c in cases}
    if unknown:
        sys.exit(f"unknown case(s) {sorted(unknown)}; known: {[c['id'] for c in cases]}")
    return [c for c in cases if c["id"] in want]


def _cap(argv) -> float:
    """Stop before the next turn once this much is spent (USD, from the turns' own cost)."""
    return float(argv[argv.index("--max-usd") + 1]) if "--max-usd" in argv else 0.05


def main(argv):
    if not argv or argv[0] not in ("run", "teardown", "playbooks"):
        sys.exit(__doc__)
    if argv[0] != "teardown":   # refuse before anything is touched: every case is a live LLM turn
        _select(PLAYBOOK_CASES if argv[0] == "playbooks" else CASES, argv)
    token = TC.login()
    if argv[0] == "playbooks":
        tag = argv[argv.index("--agent") + 1] if "--agent" in argv else "PLATFORM"
        log_path = argv[argv.index("--log") + 1] if "--log" in argv else None
        pid = snapshot(token)["by_tag"].get(tag)
        if not pid:
            sys.exit(f"no agent @{tag} — run scripts/platform_expert_agent.py ensure --rest first")
        cases = _select(PLAYBOOK_CASES, argv)
        cap = _cap(argv)
        print(f"platform playbooks · @{tag} · {TC.BASE} · {len(cases)} case(s) · cap ${cap:.2f}")
        results, spent = [], 0.0
        for case in cases:
            if spent >= cap:
                print(f"\nstopped: ${spent:.4f} spent reaches the cap ${cap:.2f} — {len(cases) - len(results)} case(s) not run")
                break
            r = run_playbook_case(token, pid, case, log_path)
            results.append(r)
            spent += float(r.get("turn_cost") or 0)
            hard_ok = all(c["ok"] for c in r.get("checks", []) if not c["label"].startswith("[metric]"))
            print(f"\n[{case['id']}] {r.get('status')} {r.get('wall_s')}s  {'PASS' if hard_ok and not r.get('error') else 'FAIL'}"
                  f"  · {r.get('turn_input_tokens', '?')} in / ${r.get('turn_cost', '?')}")
            print(f"  calls: {[(c[0], c[1].get('anchor') or c[1].get('op') or c[1].get('action')) for c in r.get('calls', [])]}"
                  + (f"  delivered: {r.get('delivered')}" if r.get('delivered') else ""))
            for c in r.get("checks", []):
                print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['label']}" + ("" if c["ok"] else f"  — {str(c['detail'])[:200]}"))
            print(f"  answer: {r.get('answer', r.get('error', ''))[:280]!r}")
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, "playbooks-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        json.dump(results, open(path, "w"), indent=1, default=str)
        hard = [c for r in results for c in r.get("checks", []) if not c["ok"] and not c["label"].startswith("[metric]")]
        reads = [c for r in results for c in r.get("checks", []) if c["label"].startswith("[metric] read")]
        print(f"\n{len(results)} turn(s) · ${spent:.4f} · {len(hard)} failed check(s) · playbook read {sum(c['ok'] for c in reads)}/{len(reads)} · {path}")
        return 1 if hard else 0
    if argv[0] == "teardown":
        profs, _ = TC.current(token)
        for tag in TAGS.values():
            p = profs.get(tag)
            if p:
                r = TC.req("DELETE", f"/api/v1/profiles/{p['id']}", token)
                print(f"  {tag}  delete  {'ok' if not r.get('__http_error__') else json.dumps(r)[:160]}")
        return 0
    log_path = argv[argv.index("--log") + 1] if "--log" in argv else None
    only = argv[argv.index("--engine") + 1] if "--engine" in argv else None
    cases = _select(CASES, argv)
    cap = _cap(argv)
    print(f"platform probe · {TC.BASE} · {len(cases)} case(s) · cap ${cap:.2f}")
    ids = ensure(token)
    results, spent = [], 0.0
    for eng, pid in ids.items():
        if only and eng != only:
            continue
        for case in cases:
            if spent >= cap:
                print(f"\nstopped: ${spent:.4f} spent reaches the cap ${cap:.2f}")
                break
            r = run_case(token, eng, pid, case, log_path)
            results.append(r)
            spent += float(r.get("turn_cost") or 0)
            ok = all(c["ok"] for c in r.get("checks", []) if not c["label"].startswith("[quality]"))
            print(f"\n[{TAGS[eng]} · {case['id']}] {r.get('status')} {r.get('wall_s')}s  {'PASS' if ok and not r.get('error') else 'FAIL'}")
            print(f"  ops: {[(o['op'], o['status']) for o in r.get('ops', [])]}")
            for c in r.get("checks", []):
                print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['label']}" + ("" if c["ok"] else f"  — {str(c['detail'])[:200]}"))
            print(f"  answer: {r.get('answer', r.get('error', ''))[:300]!r}")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, time.strftime("%Y%m%d-%H%M%S") + ".json")
    json.dump(results, open(path, "w"), indent=1, default=str)
    hard = [c for r in results for c in r.get("checks", []) if not c["ok"] and not c["label"].startswith("[quality]")]
    print(f"\n{len(results)} turn(s) · {len(hard)} failed check(s) · {path}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
