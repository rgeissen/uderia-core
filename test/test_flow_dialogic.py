#!/usr/bin/env python3
"""
PROP-007 increment 3 — the dialogic shapes: round history, the convergence exit, the
judge node, the canvas artefact node, and the four generators that use them
(iterative · debate · teacher–student · pair programming).

What a wrong implementation would get wrong, and this gate measures:
  · a loop that cannot stop early (runs every round although the judge passed)
  · a loop that stops on an UNREADABLE verdict (an abstention read as a pass)
  · round n that cannot see round n−1 (the old engine's gap)
  · a prompt whose names are not all defined → str.format fails → NO reference is
    substituted (the all-or-nothing trap)
  · a participant that loses its own memory between rounds (a new session per round)
  · debaters that run one after another although the flow is parallel
  · a run whose canvas does not advance a version per round
  · a loop that ran out of rounds but reads as a success
  · a scope WITHOUT an exit that no longer runs its fixed number of rounds (regression)

CI-pure: a scripted fake HTTP seam (one reply per call, chosen by the prompt), a fake
judge panel, a temporary flow DB and a stubbed canvas store. No server, no network.
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "test"))

from test_flow_topology_baseline import FakeHttp, _temp_flow_db, install_profiles  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


PROFILES = [
    {"id": "profile-a", "tag": "A", "profile_type": "llm_only"},
    {"id": "profile-b", "tag": "B", "profile_type": "llm_only"},
    {"id": "profile-c", "tag": "C", "profile_type": "llm_only"},
    {"id": "profile-t", "tag": "T", "profile_type": "llm_only"},
]


# ── a scripted HTTP seam: every query gets its own task, answered by `responder` ──
class ScriptedHttp(FakeHttp):
    def __init__(self, responder):
        super().__init__()
        self.responder = responder
        self.sessions = 0
        self.tasks = {}
        self.queries = []          # (session_id, profile_id, prompt)

    def _route(self, method, url, body):
        self.calls.append((method, url, body))
        if method == "POST" and url.endswith("/v1/sessions"):
            self.sessions += 1
            return self._Resp(201, {"session_id": f"sess-{self.sessions}"})
        if method == "POST" and "/query" in url:
            sid = url.split("/v1/sessions/")[1].split("/")[0]
            prompt = (body or {}).get("prompt", "")
            self.queries.append((sid, (body or {}).get("profile_id"), prompt))
            tid = f"task-{len(self.tasks) + 1}"
            self.tasks[tid] = self.responder(prompt, (body or {}).get("profile_id"), sid)
            return self._Resp(202, {"task_id": tid})
        if method == "GET" and "/v1/tasks/" in url:
            tid = url.rsplit("/", 1)[-1]
            return self._Resp(200, {
                "status": "complete", "result": (
                    {"final_answer_text": self.tasks[tid]["text"], "canvas_contents": self.tasks[tid].get("canvas_contents")}
                    if isinstance(self.tasks.get(tid), dict) else {"final_answer_text": self.tasks.get(tid, "")}),
                "events": [{"event_type": "token_update",
                            "event_data": {"turn_input": 10, "turn_output": 5, "cost_usd": 0.001}}],
            })
        return self._Resp(500, {"error": f"unrouted {method} {url}"})


def run_flow(definition, responder, panel_responses=None, canvas_store=None, parallel_probe=False):
    """Run `definition` through the REAL FlowExecutor. Returns (snapshot, events, http, probe)."""
    from trusted_data_agent.core import flow_executor as fe
    fdb = _temp_flow_db()
    restore = install_profiles(PROFILES)
    http = ScriptedHttp(responder)
    saved_aiohttp = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = http.module()
    saved_poll = fe.LLM_NODE_POLL_INTERVAL
    fe.LLM_NODE_POLL_INTERVAL = 0
    from trusted_data_agent.eval import judge as ej
    saved_build = ej.build_panel
    if panel_responses is not None:
        async def _fake_build(user_uuid=None, **kw):
            return ej.FakePanel(panel_responses)
        ej.build_panel = _fake_build
    from trusted_data_agent.core import canvas_db
    saved_canvas = {k: getattr(canvas_db, k) for k in ("create_canvas", "update_canvas", "get_canvas", "list_versions")}
    if canvas_store is not None:
        canvas_db.create_canvas = canvas_store.create
        canvas_db.update_canvas = canvas_store.update
        canvas_db.get_canvas = canvas_store.get
        canvas_db.list_versions = canvas_store.versions
    events = []

    async def emit(t, p):
        events.append((t, p))

    probe = {"inflight": 0, "max": 0}
    saved_run_node = fe.FlowExecutor._run_node
    if parallel_probe:
        async def _probing(self, node_id, scope_id, iteration, extra_ctx=None):
            probe["inflight"] += 1
            probe["max"] = max(probe["max"], probe["inflight"])
            await asyncio.sleep(0.01)
            try:
                return await saved_run_node(self, node_id, scope_id, iteration, extra_ctx)
            finally:
                probe["inflight"] -= 1
        fe.FlowExecutor._run_node = _probing
    try:
        flow = fdb.create_flow("user-1", "dialogic test", "", definition)
        run = fdb.create_run(flow["flow_id"], "user-1", {})
        rid = run["run_id"] if isinstance(run, dict) else run
        ex = fe.FlowExecutor(fe.FlowDefinition(flow["definition"]), "user-1", rid, emit)
        snap = asyncio.run(ex.run({"task": "TASK-BRIEF"}))
        return snap, events, http, probe, flow["definition"]
    finally:
        fe.FlowExecutor._run_node = saved_run_node
        restore()
        fe.LLM_NODE_POLL_INTERVAL = saved_poll
        ej.build_panel = saved_build
        for k, v in saved_canvas.items():
            setattr(canvas_db, k, v)
        if saved_aiohttp is not None:
            sys.modules["aiohttp"] = saved_aiohttp
        else:
            sys.modules.pop("aiohttp", None)


def fill(definition, fills):
    from trusted_data_agent.core import flow_topologies as topo
    topo.apply_fills(definition, fills, lambda r: r if any(p["id"] == r for p in PROFILES) else None)
    return definition


def node_by_label(d, label):
    return next(n for n in d["nodes"] if n.get("label") == label)


# ═══ 1. the pure primitives ═════════════════════════════════════════════════
def test_rounds_primitives():
    print("\n[1 flow_rounds — pure primitives]")
    from trusted_data_agent.core import flow_rounds as fr
    n1 = {"id": "n1", "label": "Debater 2", "data": {}}
    n2 = {"id": "n2", "label": "Judge", "data": {"output_as": "review"}}
    check("alias: output_as wins, else a slug of the label", fr.alias_of(n2) == "review" and fr.alias_of(n1) == "debater_2")
    long = "x" * (fr.HISTORY_ENTRY_CHARS + 500)
    t = fr.primary_text({"output": long})
    check("a participant's text is bounded and says how much was not carried",
          len(t) < len(long) and "500 more characters not carried forward" in t)
    check("a judge's feedback is its text (not its raw reply)", fr.primary_text({"feedback": "fix X", "output": "raw"}) == "fix X")
    ctx = fr.round_context([n1, n2], [], 0, 3)
    check("round 1: round/max_rounds set, history says it is the first round",
          ctx["round"] == 1 and ctx["max_rounds"] == 3 and ctx["history"] == fr.NO_HISTORY)
    check("round 1: every previous_<name> is DEFINED (empty) — str.format can never miss a name",
          ctx.get("previous_debater_2", None) == "" and ctx.get("previous_review", None) == "")
    try:
        "{task} {history} {previous_review} {previous_debater_2} {round}".format(task="t", **ctx)
        ok = True
    except KeyError:
        ok = False
    check("a template using the round names formats in round 1", ok)
    rounds = [fr.snapshot_round([n1, n2], {"n1": {"output": "arg one"}, "n2": {"feedback": "not yet"}})]
    ctx2 = fr.round_context([n1, n2], rounds, 1, 3)
    check("round 2 reads round 1: previous_<name> + the transcript",
          ctx2["previous_debater_2"] == "arg one" and ctx2["previous_review"] == "not yet"
          and "— Round 1 —" in ctx2["history"] and "Debater 2: arg one" in ctx2["history"])
    many = [[{"alias": "a", "label": "A", "text": f"r{i}"}] for i in range(1, 6)]
    h = fr.format_history(many, window=2)
    check("a history window keeps the last N rounds and COUNTS what it omits",
          "r4" in h and "r5" in h and "r1" not in h and "rounds 1–3 omitted" in h)
    al = fr.after_loop_context(rounds, {"converged": True, "exit_reason": "converged"})
    check("after the loop: transcript + an outcome sentence", "converged after 1 round" in al["outcome"] and "Round 1" in al["history"])
    al2 = fr.after_loop_context(rounds * 3, {"converged": False, "exit_reason": "max_rounds"})
    check("a loop that ran out of rounds says so (never reads as agreement)", "no agreement was reached in 3 rounds" in al2["outcome"])


def test_verdicts():
    print("\n[2 verdict parsing]")
    from trusted_data_agent.core import flow_rounds as fr
    v = fr.parse_agent_verdict("Good work, but cover edge cases.\nVERDICT: FAIL\nSCORE: 0.4")
    check("FAIL + score + the feedback without the marker lines",
          v["passed"] is False and v["score"] == 0.4 and v["feedback"] == "Good work, but cover edge cases.")
    v = fr.parse_agent_verdict("**VERDICT:** PASS\n**SCORE:** 9/10")
    check("markdown-bold markers + a /10 score", v["passed"] is True and abs(v["score"] - 0.9) < 1e-9)
    v = fr.parse_agent_verdict("Reply with VERDICT: PASS or VERDICT: FAIL ... \nmy view...\nVERDICT: FAIL")
    check("the LAST verdict line wins (a reply may quote the instructions)", v["passed"] is False)
    v = fr.parse_agent_verdict("Looks fine to me.")
    check("no verdict line → unknown, passed None (an abstention is never a pass)", v["passed"] is None and v["verdict"] == "unknown")
    v = fr.parse_agent_verdict('Assessment.\n{"verdict": "pass", "score": 0.8, "feedback": "ship it"}')
    check("a trailing JSON object is accepted as a fallback", v["passed"] is True and v["score"] == 0.8 and v["feedback"] == "ship it")
    v = fr.parse_agent_verdict("VERDICT: APPROVED\nSCORE: 85%")
    check("APPROVED counts as a pass, 85% as 0.85", v["passed"] is True and abs(v["score"] - 0.85) < 1e-9)
    pv = fr.panel_verdict(None, None, [], "no judges")
    check("a panel with no decided votes → unknown", pv["passed"] is None and pv["verdict"] == "unknown")


def test_exit_and_code():
    print("\n[3 exit condition · code extraction]")
    from trusted_data_agent.core import flow_rounds as fr
    from trusted_data_agent.core.safe_expression import safe_eval
    ew = {"node": "j", "condition": "passed == True"}
    check("exit holds on a pass", fr.evaluate_exit(ew, {"j": {"passed": True}}, safe_eval) == (True, None))
    check("exit does not hold on a fail", fr.evaluate_exit(ew, {"j": {"passed": False}}, safe_eval)[0] is False)
    check("exit does not hold on an abstention (passed None)", fr.evaluate_exit(ew, {"j": {"passed": None}}, safe_eval)[0] is False)
    stop, err = fr.evaluate_exit(ew, {}, safe_eval)
    check("a missing exit-node output never stops the loop, and is reported", stop is False and "no output" in err)
    stop, err = fr.evaluate_exit({"node": "j", "condition": "passed and ("}, {"j": {"passed": True}}, safe_eval)
    check("an unevaluable condition never stops the loop, and is reported", stop is False and err)
    cond = fr.threshold_condition(0.7)
    check("threshold: a pass below the threshold does not stop",
          fr.evaluate_exit({"node": "j", "condition": cond}, {"j": {"passed": True, "score": 0.5}}, safe_eval)[0] is False)
    check("threshold: a pass with no score still stops (a scoreless panel must not block forever)",
          fr.evaluate_exit({"node": "j", "condition": cond}, {"j": {"passed": True, "score": None}}, safe_eval)[0] is True)
    check("no exit_when → never stops early", fr.evaluate_exit(None, {"j": {"passed": True}}, safe_eval) == (False, None))
    code, lang = fr.extract_code("Here:\n```python\nprint(1)\n```\nUsage:\n```bash\nx\n```\nand the file:\n```python\ndef f():\n    return 2\n```")
    check("the LONGEST fenced block is the code, with its language", "def f()" in code and lang == "python")
    code, lang = fr.extract_code("no fences here", "sql")
    check("no fence → the whole reply, the given language", code == "no fences here\n" and lang == "sql")
    prose = "I have implemented the is_palindrome function in the canvas. It strips punctuation and compares."
    check("prose ABOUT code is not code (the live defect: a driver that saved the file elsewhere)",
          fr.code_from_reply(prose, "python") == (None, "python"))
    raw = "import re\n\ndef f(s):\n    s = re.sub('x', '', s)\n    return s == s[::-1]"
    check("an unfenced reply that IS code still counts", fr.code_from_reply(raw)[0].startswith("import re"))
    check("a fenced block wins over everything", "def f()" in fr.code_from_reply("Sure:\n```python\ndef f():\n    pass\n```")[0])


# ═══ 2. the generators ══════════════════════════════════════════════════════
_NAME_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def _check_prompt_names(key, d):
    """Every {name} in every prompt resolves at run time — the all-or-nothing trap."""
    from trusted_data_agent.core import flow_rounds as fr
    scope = (d.get("scopes") or [None])[0]
    members = set(scope["node_ids"]) if scope else set()
    by_id = {n["id"]: n for n in d["nodes"]}
    aliases_in_loop = {fr.alias_of(by_id[i]) for i in members}
    fwd = [e for e in d["edges"] if e.get("type") != "cycle"]
    bad = []
    for n in d["nodes"]:
        data = n.get("data") or {}
        texts = [data.get("prompt") or ""]
        for t in texts:
            for name in _NAME_RE.findall(t):
                ok = name in d.get("global_params", {})
                preds = [e["source"] for e in fwd if e["target"] == n["id"]]
                pred_names = set()
                for p in preds:
                    pn = by_id[p]
                    pred_names.add(fr.alias_of(pn))
                    pred_names.add("output")
                    if pn["type"] == "artefact":
                        pred_names |= {"code", "version", "language", "canvas_id"}
                ok = ok or name in pred_names
                if n["id"] in members:
                    ok = ok or name in ("round", "max_rounds", "history") or (
                        name.startswith("previous_") and name[len("previous_"):] in aliases_in_loop)
                elif any(p in members for p in preds):
                    ok = ok or name in ("history", "outcome", "rounds_completed", "converged", "exit_reason")
                if not ok:
                    bad.append(f"{n['label']}:{{{name}}}")
    check(f"{key}: every name in every prompt resolves at run time", not bad, ", ".join(bad))


def test_generators():
    print("\n[4 the four generators]")
    from trusted_data_agent.core import flow_topologies as topo
    from trusted_data_agent.core.flow_validation import validate_and_repair
    from trusted_data_agent.core.flow_executor import FlowDefinition
    expect = {
        "iterative": (["worker"], ["Worker", "Judge"]),
        "debate": (["debater", "debater", "debater", "synthesiser"], ["Debater 1", "Debater 2", "Debater 3", "Moderator"]),
        "teacher-student": (["student", "teacher"], ["Student", "Teacher"]),
        "pair-programming": (["driver", "navigator"], ["Driver", "Canvas", "Navigator"]),
    }
    for key, (open_roles, loop_labels) in expect.items():
        d, _ = topo.generate(key, {})
        check(f"{key}: available and generated", bool(d["nodes"]))
        check(f"{key}: open slots are exactly the agent roles", [s["role"] for s in topo.open_slots(d)] == open_roles,
              str([s["role"] for s in topo.open_slots(d)]))
        sc = d["scopes"][0]
        by_id = {n["id"]: n for n in d["nodes"]}
        check(f"{key}: the loop holds exactly the dialogic nodes", [by_id[i]["label"] for i in sc["node_ids"]] == loop_labels,
              str([by_id[i]["label"] for i in sc["node_ids"]]))
        back = next(e for e in d["edges"] if e["id"] == sc["back_edge_id"])
        check(f"{key}: the back edge is a cycle edge inside the loop", back["type"] == "cycle"
              and back["source"] in sc["node_ids"] and back["target"] in sc["node_ids"])
        ex = sc.get("exit_when") or {}
        check(f"{key}: the loop exits on a judge node's verdict", by_id.get(ex.get("node"), {}).get("type") == "judge"
              and ex.get("node") in sc["node_ids"] and "passed" in ex.get("condition", ""))
        check(f"{key}: max rounds = the parameter", sc["max_iterations"] == topo.default_params(topo.get_template(key))["max_rounds"])
        check(f"{key}: parallel (one session per participant, debaters concurrent)", d["settings"].get("parallelExecution") is True)
        check(f"{key}: FlowDefinition validates", FlowDefinition(d).validate() == [], str(FlowDefinition(d).validate()))
        errs, _c = validate_and_repair(d)
        check(f"{key}: validate_and_repair has no errors", errs == [], str(errs))
        _check_prompt_names(key, d)
    d, _ = topo.generate("iterative", {})
    check("iterative: the judge is pre-filled with the eval judge panel (not an agent choice)",
          node_by_label(d, "Judge")["data"].get("profile_id") == topo.JUDGE_PANEL)
    d, _ = topo.generate("teacher-student", {})
    t = node_by_label(d, "Teacher")
    check("teacher-student: the teacher is an agent judge (no panel default, agents only)",
          t["type"] == "judge" and not t["data"].get("profile_id") and "judge_panel" not in t["data"]["slot"]["accepts"])
    d, _ = topo.generate("debate", {"debaters": 4, "max_rounds": 5})
    check("debate: debaters and rounds follow the parameters",
          sum(1 for n in d["nodes"] if n["label"].startswith("Debater")) == 4 and d["scopes"][0]["max_iterations"] == 5)
    d, _ = topo.generate("pair-programming", {})
    cv = node_by_label(d, "Canvas")
    check("pair-programming: the canvas node reads the driver's reply and publishes `code`",
          cv["type"] == "artefact" and cv["data"]["source"] == "driver_reply" and cv["data"]["output_as"] == "code")
    check("the swarm stays a hand-off (not a flow)", not topo.is_available(topo.get_template("swarm")))


def test_layout_frames_the_loop():
    print("\n[5 layout frames the loop]")
    from trusted_data_agent.core import flow_topologies as topo
    from trusted_data_agent.core.flow_layout import apply_layout
    d, _ = topo.generate("debate", {})
    apply_layout(d)
    sc = d["scopes"][0]
    pos, size = sc.get("position"), sc.get("size")
    check("the scope gets a position and a size from its members", isinstance(pos, dict) and isinstance(size, dict))
    if pos and size:
        by_id = {n["id"]: n for n in d["nodes"]}
        xs = [by_id[i]["position"]["x"] for i in sc["node_ids"]]
        ys = [by_id[i]["position"]["y"] for i in sc["node_ids"]]
        left, right = pos["x"] - size["width"] / 2, pos["x"] + size["width"] / 2
        check("every member sits inside the frame",
              all(left < x < right for x in xs) and all(pos["y"] < y < pos["y"] + size["height"] for y in ys))
        out = [n for n in d["nodes"] if n["id"] not in sc["node_ids"] and n["type"] in ("start", "end")]
        check("start and end sit outside it", all(not (pos["y"] < n["position"]["y"] < pos["y"] + size["height"] - 10) for n in out))


# ═══ 3. executing the shapes ════════════════════════════════════════════════
def test_iterative_converges():
    print("\n[6 iterative — the loop stops on the judge's pass, round 2 reads round 1]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("iterative", {"max_rounds": 4})
    fill(d, {"worker": ["profile-a"], "judge": ["profile-t"]})        # an agent judge

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:                                       # the judge
            return "Needs a citation.\nVERDICT: FAIL\nSCORE: 0.3" if "draft-1" in prompt else "Good.\nVERDICT: PASS\nSCORE: 0.9"
        return "draft-2" if "Needs a citation." in prompt else "draft-1"
    snap, events, http, _p, fd = run_flow(d, responder)
    sc = next(iter(snap["scopes"].values()))
    check("stopped after 2 of 4 rounds", sc["rounds_completed"] == 2, str(sc))
    check("exit reason: converged", sc["converged"] is True and sc["exit_reason"] == "converged")
    worker_prompts = [q[2] for q in http.queries if "VERDICT:" not in q[2]]
    check("round 2's worker prompt carries round 1's draft AND the judge's feedback",
          len(worker_prompts) == 2 and "draft-1" in worker_prompts[1] and "Needs a citation." in worker_prompts[1])
    check("round 1's prompt has every name substituted (no raw {…} left)",
          "{" not in worker_prompts[0] and "first round" in worker_prompts[0], worker_prompts[0][:200])
    sess_by_profile = {}
    for sid, prof, _ in http.queries:
        sess_by_profile.setdefault(prof, set()).add(sid)
    check("each participant keeps ONE session across rounds (its own memory)",
          all(len(v) == 1 for v in sess_by_profile.values()) and http.sessions == 2, f"{sess_by_profile} sessions={http.sessions}")
    done = [p for t, p in events if t == "flow_scope_complete"]
    check("flow_scope_complete names the exit and the last verdict",
          done and done[0]["exit_reason"] == "converged" and done[0]["last_verdict"]["passed"] is True)
    rc = [p for t, p in events if t == "flow_scope_round_complete"]
    check("one round-complete event per round, the last saying it will stop",
          len(rc) == 2 and rc[0]["will_stop"] is False and rc[1]["will_stop"] is True)


def test_iterative_runs_out():
    print("\n[7 iterative — never passes: runs out, and SAYS so]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("iterative", {"max_rounds": 3})
    fill(d, {"worker": ["profile-a"], "judge": ["profile-t"]})
    snap, events, http, _p, _ = run_flow(d, lambda p, pr, s: "no.\nVERDICT: FAIL" if "VERDICT:" in p else "draft")
    sc = next(iter(snap["scopes"].values()))
    check("ran all 3 rounds", sc["rounds_completed"] == 3)
    check("not converged, exit reason max_rounds", sc["converged"] is False and sc["exit_reason"] == "max_rounds")


def test_abstaining_judge_never_stops():
    print("\n[8 an abstaining judge (no verdict line) never stops the loop]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("iterative", {"max_rounds": 2})
    fill(d, {"worker": ["profile-a"], "judge": ["profile-t"]})
    snap, events, _h, _p, _ = run_flow(d, lambda p, pr, s: "looks fine" if "VERDICT:" in p else "draft")
    sc = next(iter(snap["scopes"].values()))
    check("ran to the limit — an unreadable verdict is not a pass", sc["rounds_completed"] == 2 and sc["converged"] is False)
    check("…and a warning says why", any(t == "flow_node_warning" and "no VERDICT line" in p.get("message", "") for t, p in events))


def test_panel_judge():
    print("\n[9 the eval judge panel as the judge]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("iterative", {"max_rounds": 3})
    fill(d, {"worker": ["profile-a"]})                                  # judge stays judge_panel
    pass_json = '{"verdict": "pass", "score": 0.9, "evidence": ["ok"], "reasoning": "complete"}'
    snap, events, http, _p, _ = run_flow(d, lambda p, pr, s: "draft", panel_responses=[pass_json, pass_json])
    sc = next(iter(snap["scopes"].values()))
    check("a panel pass stops the loop in round 1", sc["rounds_completed"] == 1 and sc["converged"] is True)
    judge_out = next(v for k, v in snap["node_outputs"].items() if isinstance(v, dict) and v.get("judge") == "panel")
    check("the panel verdict carries passed/score/votes in the shared shape",
          judge_out["passed"] is True and judge_out["score"] == 0.9 and len(judge_out["votes"]) >= 1)
    check("no LLM query was made for the panel judge (it is not an agent)", all("VERDICT:" not in q[2] for q in http.queries))
    snap, events, _h, _p, _ = run_flow(d, lambda p, pr, s: "draft", panel_responses=[])
    sc = next(iter(snap["scopes"].values()))
    check("an empty panel → unknown verdict → the loop runs on, with a warning",
          sc["converged"] is False and any(t == "flow_node_warning" and "No judge models" in p.get("message", "") for t, p in events))


def test_debate_parallel_and_synthesis():
    print("\n[10 debate — debaters argue at once, read each other, a synthesiser writes it up]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("debate", {"debaters": 3, "max_rounds": 3})
    fill(d, {"debater": ["profile-a", "profile-b", "profile-c"], "judge": ["profile-t"], "synthesiser": ["profile-t"]})

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "They agree now.\nVERDICT: PASS" if "round 2" in prompt.lower() else "Still split.\nVERDICT: FAIL"
        if "debate on this question has ended" in prompt:
            return "FINAL"
        return f"{profile}-says-something"
    snap, events, http, probe, _ = run_flow(d, responder, parallel_probe=True)
    sc = next(iter(snap["scopes"].values()))
    check("converged in round 2", sc["rounds_completed"] == 2 and sc["converged"] is True, str(sc))
    check("the three debaters ran CONCURRENTLY within a round", probe["max"] >= 3, f"max in flight {probe['max']}")
    deb_r2 = [q for q in http.queries if "Debater 1 of 3" in q[2]][1][2]
    check("round 2: Debater 1 reads Debater 2 and 3's round-1 arguments and the moderator",
          "profile-b-says-something" in deb_r2 and "profile-c-says-something" in deb_r2 and "Still split." in deb_r2)
    synth = next(q[2] for q in http.queries if "debate on this question has ended" in q[2])
    check("the synthesiser reads the whole transcript and the outcome",
          "they converged after 2 rounds" in synth and "— Round 1 —" in synth and "— Round 2 —" in synth)
    mod = [q[2] for q in http.queries if "VERDICT:" in q[2]][0]
    check("the moderator reads this round's three arguments by name",
          all(f"Debater {i}:" in mod for i in (1, 2, 3)) and "profile-a-says-something" in mod)


class FakeCanvas:
    def __init__(self):
        self.canvases = {}

    def create(self, user_id, title, content, language=None, sources=None, session_id=None):
        cid = f"canvas-{len(self.canvases) + 1}"
        self.canvases[cid] = {"canvas_id": cid, "title": title, "versions": [content], "language": language}
        return {"canvas_id": cid}

    def update(self, canvas_id, user_id, content=None, language=None, **kw):
        c = self.canvases[canvas_id]
        if content is not None and content != c["versions"][-1]:
            c["versions"].append(content)
        return c

    def get(self, canvas_id, user_id):
        c = self.canvases.get(canvas_id)
        return dict(c, content=c["versions"][-1]) if c else None

    def versions(self, canvas_id, user_id):
        return list(self.canvases[canvas_id]["versions"])


def test_pair_programming_canvas():
    print("\n[11 pair programming — one canvas per run, a version per round]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("pair-programming", {"max_rounds": 3})
    fill(d, {"driver": ["profile-a"], "navigator": ["profile-t"]})
    store = FakeCanvas()

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "Approved.\nVERDICT: PASS" if "return a + b" in prompt else "Handle ints.\nVERDICT: FAIL"
        v2 = "Handle ints." in prompt
        return "```python\ndef add(a, b):\n    " + ("return a + b" if v2 else "pass") + "\n```\nDone."
    snap, events, http, _p, _ = run_flow(d, responder, canvas_store=store)
    sc = next(iter(snap["scopes"].values()))
    check("approved in round 2", sc["rounds_completed"] == 2 and sc["converged"] is True)
    check("exactly ONE canvas for the run", len(store.canvases) == 1)
    c = next(iter(store.canvases.values()))
    check("…with a version per round (2), holding only the code", len(c["versions"]) == 2
          and "return a + b" in c["versions"][1] and "Done." not in c["versions"][1] and c["language"] == "python")
    nav_r1 = [q[2] for q in http.queries if "VERDICT:" in q[2]][0]
    check("the navigator reviews the canvas code and its version", "def add(a, b)" in nav_r1 and "canvas version 1" in nav_r1)
    drv_r2 = [q[2] for q in http.queries if "VERDICT:" not in q[2]][1]
    check("round 2's driver reads the current code and the review",
          "def add(a, b)" in drv_r2 and "Handle ints." in drv_r2)


def test_pair_programming_prose_reply():
    print("\n[11b pair programming — a reply with no code is never saved as the code]")
    from trusted_data_agent.core import flow_topologies as topo
    from trusted_data_agent.core import flow_rounds as fr
    d, _ = topo.generate("pair-programming", {"max_rounds": 3})
    fill(d, {"driver": ["profile-a"], "navigator": ["profile-t"]})
    store = FakeCanvas()

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "Looks right.\nVERDICT: PASS" if "return s == s[::-1]" in prompt else "I see no code.\nVERDICT: FAIL"
        if "No code was received in the last round" in prompt:
            return "```python\ndef f(s):\n    return s == s[::-1]\n```"
        return "I have implemented the function in the canvas; it handles case and spaces."
    snap, events, http, _p, _ = run_flow(d, responder, canvas_store=store)
    sc = next(iter(snap["scopes"].values()))
    check("converged in round 2, after the driver was told no code arrived",
          sc["rounds_completed"] == 2 and sc["converged"] is True)
    nav_r1 = [q[2] for q in http.queries if "VERDICT:" in q[2]][0]
    check("round 1's navigator is told plainly that no code was received (not handed the prose as code)",
          "No code was received" in nav_r1 and "implemented the function in the canvas" not in nav_r1.split("THE CODE")[-1])
    check("the canvas never holds the prose: one canvas, one version, the code only",
          len(store.canvases) == 1 and all("implemented" not in v for c in store.canvases.values() for v in c["versions"])
          and len(next(iter(store.canvases.values()))["versions"]) == 1)
    # The live defect: a navigator told "no code was received" still PASSED. Code decides.
    store2 = FakeCanvas()

    def lenient(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "Fine.\nVERDICT: PASS"
        if "No code was received in the last round" in prompt:
            return "```python\ndef f(s):\n    return s == s[::-1]\n```"
        return "I have implemented the function in the canvas."
    snap2, events2, _h2, _p2, _ = run_flow(d, lenient, canvas_store=store2)
    sc2 = next(iter(snap2["scopes"].values()))
    check("a pass over a round with NO code does not end the loop (it ran on to the code round)",
          sc2["rounds_completed"] == 2 and sc2["converged"] is True)
    check("…and the reason is reported as a warning, not swallowed",
          any("received no code" in str((e[1] if isinstance(e, tuple) else e)) for e in events2))
    check("convergence_gap: none for a normal round", fr.convergence_gap({"a": {"code": "x"}}) is None)
    src = (ROOT / "src/trusted_data_agent/core/flow_topologies/pair_programming.py").read_text()
    check("the driver is told not to store the code with a tool; the navigator that the code is below",
          "Do not save the code with a canvas" in src and "do not look for it in a" in src)


def test_canvas_contents_reach_the_flow():
    print("\n[11c an agent's canvas code reaches the flow (the live root cause)]")
    from trusted_data_agent.components.utils import canvas_contents
    from trusted_data_agent.core import flow_executor as fe
    from trusted_data_agent.core import flow_topologies as topo
    pl = [{"component_id": "canvas", "spec": {"content": "def f():\n    return 1", "language": "python", "title": "Python Code"}},
          {"component_id": "chart", "spec": {"content": "x"}},
          {"component_id": "canvas", "spec": {"content": "   ", "language": "sql"}}]
    cc = canvas_contents(pl)
    check("canvas_contents: canvas payloads only, empty content skipped",
          cc == [{"title": "Python Code", "language": "python", "content": "def f():\n    return 1"}])
    folded = fe._fold_canvas_contents("I wrote the function.", cc)
    check("the fold appends the canvas code as a fenced block after the prose",
          folded.startswith("I wrote the function.") and "```python\ndef f():\n    return 1\n```" in folded)
    check("the fold never duplicates code the text already carries",
          fe._fold_canvas_contents("```python\ndef f():\n    return 1\n```", cc).count("def f()") == 1)
    check("no canvases ⇒ the text untouched", fe._fold_canvas_contents("plain", None) == "plain")
    src = (ROOT / "src/trusted_data_agent/agent/engines/conversation_engine.py").read_text()
    check("the conversation engine hands canvas_contents on its final payload",
          '"canvas_contents": canvas_contents(_payloads)' in src)

    d, _ = topo.generate("pair-programming", {"max_rounds": 3})
    fill(d, {"driver": ["profile-a"], "navigator": ["profile-t"]})
    store = FakeCanvas()

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "Approved.\nVERDICT: PASS" if "return s == s[::-1]" in prompt else "No code.\nVERDICT: FAIL"
        # the agent's auto-canvas moved the code out of the text — it rides canvas_contents only
        return {"text": "I implemented it; see the canvas.",
                "canvas_contents": [{"title": "Python Code", "language": "python",
                                     "content": "def f(s):\n    return s == s[::-1]"}]}
    snap, events, http, _p, _ = run_flow(d, responder, canvas_store=store)
    sc = next(iter(snap["scopes"].values()))
    check("converged in round 1: the driver's canvas code reached the artefact and the navigator",
          sc["rounds_completed"] == 1 and sc["converged"] is True)
    c = next(iter(store.canvases.values()), {"versions": [""]})
    check("…and the run's canvas holds that code (not the prose)",
          "return s == s[::-1]" in c["versions"][-1] and "see the canvas" not in c["versions"][-1])


def test_teacher_student():
    print("\n[12 teacher–student — the teacher teaches and decides]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("teacher-student", {"max_rounds": 3})
    fill(d, {"student": ["profile-a"], "teacher": ["profile-t"]})

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "Use the formula from step 2.\nVERDICT: FAIL" if "(round 1)" in prompt else "Well done.\nVERDICT: PASS"
        return "attempt-2" if "Use the formula from step 2." in prompt else "attempt-1"
    snap, events, http, _p, _ = run_flow(d, responder)
    sc = next(iter(snap["scopes"].values()))
    check("the student passed in round 2", sc["rounds_completed"] == 2 and sc["converged"] is True)
    teach = [q[2] for q in http.queries if "VERDICT:" in q[2]][0]
    check("the teacher prompt carries its teaching instruction", "teach the method" in teach)


def test_scope_without_exit_unchanged():
    print("\n[13 regression — a scope WITHOUT exit_when still runs its fixed rounds]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("iterative", {"max_rounds": 3})
    fill(d, {"worker": ["profile-a"], "judge": ["profile-t"]})
    d["scopes"][0].pop("exit_when")
    snap, events, _h, _p, _ = run_flow(d, lambda p, pr, s: "fine\nVERDICT: PASS" if "VERDICT:" in p else "draft")
    sc = next(iter(snap["scopes"].values()))
    check("all 3 rounds ran although the judge passed every time (no exit configured)", sc["rounds_completed"] == 3)
    check("exit reason max_rounds, has_exit False", sc["exit_reason"] == "max_rounds" and sc["has_exit"] is False)


def test_structural_wiring():
    print("\n[14 structural wiring]")
    src = (ROOT / "src/trusted_data_agent/core/flow_executor.py").read_text()
    check("the dispatcher knows the judge and artefact node types",
          'node_type == "judge"' in src and 'node_type == "artefact"' in src)
    check("a loop participant's session is pinned across rounds (priority before 'new')",
          src.index('_scope_session_id') < src.index('if session_context == "new"'))
    check("the agent judge goes through the ordinary LLM node path (slot refusal included)",
          "out = await _execute_llm_node(agent_node" in src)
    rjs = (ROOT / "components/builtin/flow/renderer.js").read_text()
    check("a cycle edge renders as the loop-back edge (a smoothstep between stacked nodes reads as a straight line)",
          "e.type === 'cycle' ? 'loopback' : 'smoothstep'" in rjs and "edgeTypes: _staticEdgeTypes," in rjs
          and "loopback: LoopBackEdge" in rjs)
    check("the loop-back path leaves the source downward, runs a side lane and enters the target from above",
          "Math.max(sx, tx) + (lane || LOOP_LANE_OFFSET)" in rjs and "`L ${tx} ${ty}`" in rjs)


def test_round_limit_edit():
    print("\n[15 the loop's round limit is editable after creation, clamped by the server]")
    from trusted_data_agent.core import flow_db
    from trusted_data_agent.api import flow_routes
    check("the clamp keeps a value in range and refuses junk",
          flow_db.clamp_scope_rounds(4) == 4 and flow_db.clamp_scope_rounds(99) == flow_db.SCOPE_MAX_ROUNDS
          and flow_db.clamp_scope_rounds(0) == flow_db.SCOPE_MIN_ROUNDS and flow_db.clamp_scope_rounds("x") == flow_db.SCOPE_MIN_ROUNDS
          and flow_db.clamp_scope_rounds("5") == 5)
    defn = {"nodes": [], "edges": [], "scopes": [{"id": "s1", "name": "Loop", "node_ids": [], "max_iterations": 3}]}
    saved = {}
    orig_get, orig_upd = flow_db.get_flow, flow_db.update_flow
    flow_db.get_flow = lambda fid, uid: {"flow_id": fid, "definition": defn}
    flow_db.update_flow = lambda fid, uid, definition=None, **kw: saved.update(d=definition) or {"definition": definition}
    try:
        flow_db.apply_patch("f", "u", {"op": "update_scope", "payload": {"scope_id": "s1", "updates": {"max_iterations": 50}}})
    finally:
        flow_db.get_flow, flow_db.update_flow = orig_get, orig_upd
    check("update_scope stores the clamped limit (a client cannot set 50)",
          saved.get("d", {}).get("scopes", [{}])[0].get("max_iterations") == flow_db.SCOPE_MAX_ROUNDS)
    check("the version history names the new limit",
          flow_routes._label_for_patch("update_scope", {"scope_id": "s1", "updates": {"max_iterations": 99}})
          == f"Set loop to up to {flow_db.SCOPE_MAX_ROUNDS} rounds")


def _hand_flow():
    """start → Writer → Reviewer(judge) → end, no loop yet."""
    return {
        "nodes": [{"id": "start", "type": "start", "label": "Start"},
                  {"id": "w", "type": "task_llm", "label": "Writer", "data": {"prompt": "Write about {task}. Round {round}. Feedback: {previous_review}", "profile_id": "profile-a", "output_as": "draft"}},
                  {"id": "j", "type": "judge", "label": "Reviewer", "data": {"profile_id": "profile-t", "rubric": "Is it good?", "output_as": "review"}},
                  {"id": "f", "type": "task_function", "label": "Score", "data": {"expression": "1"}},
                  {"id": "end", "type": "end", "label": "End"}],
        "edges": [{"id": "e1", "source": "start", "target": "w", "type": "normal"},
                  {"id": "e2", "source": "w", "target": "j", "type": "normal"},
                  {"id": "e3", "source": "j", "target": "end", "type": "normal"},
                  {"id": "e4", "source": "start", "target": "f", "type": "normal"},
                  {"id": "e5", "source": "f", "target": "end", "type": "normal"}],
        "scopes": [],
    }


def test_loop_editing():
    print("\n[16 loops made, edited and removed by hand — one rule set (core/flow_loops.py)]")
    import copy
    from trusted_data_agent.core import flow_loops as L
    d = _hand_flow()
    sc = L.make_loop(d, ["j", "w"], name="Draft")
    back = [e for e in d["edges"] if e.get("type") == "cycle"]
    check("a loop of Writer → Reviewer starts at Writer and closes back from Reviewer",
          sc["node_ids"][0] == "w" and len(back) == 1 and back[0]["source"] == "j" and back[0]["target"] == "w"
          and sc["back_edge_id"] == back[0]["id"])
    check("the stop rule defaults to the judge inside it", sc.get("exit_when") == {"node": "j", "condition": "passed == True"})
    d1 = _hand_flow(); one = L.make_loop(d1, ["w"])
    check("one step can repeat on its own (a back edge to itself, no stop rule)",
          one["node_ids"] == ["w"] and "exit_when" not in one and any(e["source"] == "w" and e["target"] == "w" for e in d1["edges"]))

    def refuses(code, fn):
        try:
            fn()
        except L.LoopError as exc:
            return exc.code == code and bool(exc.message)
        return False
    check("an empty selection is refused", refuses("empty_loop", lambda: L.make_loop(_hand_flow(), [])))
    check("Start / End cannot repeat", refuses("not_loopable", lambda: L.make_loop(_hand_flow(), ["start", "w"])))
    check("steps that do not follow one another are refused", refuses("not_connected", lambda: L.make_loop(_hand_flow(), ["w", "f"])))
    check("a step already in a loop is refused", refuses("already_in_loop", lambda: L.make_loop(copy.deepcopy(d), ["j"])))
    fan = _hand_flow(); fan["edges"].append({"id": "e9", "source": "w", "target": "f", "type": "normal"})
    check("a round that would end at two steps is refused (and says how to fix it)",
          refuses("ambiguous_exit", lambda: L.make_loop(fan, ["w", "j", "f"])))
    check("the round limit is clamped", L.make_loop(_hand_flow(), ["w", "j"], max_rounds=99)["max_iterations"] == L.SCOPE_MAX_ROUNDS)

    # update
    L.update_loop(d, sc["id"], {"name": "  Refine ", "max_iterations": 0, "history_window": 2})
    check("name, round limit and history window edit in place (clamped)",
          sc["name"] == "Refine" and sc["max_iterations"] == L.SCOPE_MIN_ROUNDS and sc["history_window"] == 2)
    L.update_loop(d, sc["id"], {"history_window": "all"})
    check("'every earlier round' removes the window", "history_window" not in sc)
    L.update_loop(d, sc["id"], {"exit_when": {"node": "j", "mode": "threshold", "threshold": 0.8}})
    check("a pass-with-score stop rule is built by the server",
          sc["exit_when"]["condition"] == "passed == True and (score is None or score >= 0.8)")
    L.update_loop(d, sc["id"], {"exit_when": {"node": "j", "mode": "expression", "expression": "score >= 0.5"}})
    check("a custom condition is kept as written once it evaluates", sc["exit_when"]["condition"] == "score >= 0.5")
    check("a custom condition that cannot evaluate is refused",
          refuses("bad_condition", lambda: L.update_loop(d, sc["id"], {"exit_when": {"node": "j", "mode": "expression", "expression": "score >>> 1"}})))
    check("a pass rule on a step that gives no verdict is refused",
          refuses("exit_not_a_judge", lambda: L.update_loop(d, sc["id"], {"exit_when": {"node": "w", "mode": "pass"}})))
    check("a decider outside the loop is refused",
          refuses("exit_outside_loop", lambda: L.update_loop(d, sc["id"], {"exit_when": {"node": "f", "mode": "pass"}})))
    check("a threshold outside 0..1 is refused",
          refuses("bad_threshold", lambda: L.update_loop(d, sc["id"], {"exit_when": {"node": "j", "mode": "threshold", "threshold": 3}})))
    check("members and the back edge cannot be rewritten in place",
          refuses("unsupported_update", lambda: L.update_loop(d, sc["id"], {"node_ids": ["w"]})))
    L.update_loop(d, sc["id"], {"exit_when": None})
    check("clearing the stop rule makes it a fixed-round loop", "exit_when" not in sc)

    # remove
    L.remove_loop(d, sc["id"])
    check("removing a loop keeps its steps and drops the back edge",
          not d["scopes"] and not any(e.get("type") == "cycle" for e in d["edges"]) and {"w", "j"} <= {n["id"] for n in d["nodes"]})

    # adopt (the TDA_Flow tool sends a whole scope)
    d2 = _hand_flow()
    ad = L.adopt_scope(d2, {"name": "Tool loop", "node_ids": ["w", "j"], "max_iterations": 50})
    check("a tool-sent scope without a back edge gets one, and its limit is clamped",
          ad["max_iterations"] == L.SCOPE_MAX_ROUNDS and any(e["id"] == ad["back_edge_id"] and e["type"] == "cycle" for e in d2["edges"]))

    # through apply_patch on a real flow DB: layout, refusals, update, delete
    fdb = _temp_flow_db()
    flow = fdb.create_flow("user-1", "hand loop", "", _hand_flow())
    fid = flow["flow_id"]
    upd, pos = fdb.apply_patch(fid, "user-1", {"op": "create_scope", "payload": {"node_ids": ["w", "j"], "name": "Hand"}})
    scs = upd["definition"]["scopes"]
    check("create_scope from a selection makes the loop, frames it and lays the flow out",
          len(scs) == 1 and scs[0].get("size") and scs[0].get("position") and pos)
    try:
        fdb.apply_patch(fid, "user-1", {"op": "create_scope", "payload": {"node_ids": ["w"]}})
        refused = False
    except fdb.LoopError as exc:
        refused = exc.code == "already_in_loop"
    check("a refused loop raises LoopError out of apply_patch (the route answers 400)", refused)
    upd, _ = fdb.apply_patch(fid, "user-1", {"op": "update_scope", "payload": {"scope_id": scs[0]["id"], "updates": {"max_iterations": 7}}})
    check("update_scope edits the stored loop", upd["definition"]["scopes"][0]["max_iterations"] == 7)
    upd, _ = fdb.apply_patch(fid, "user-1", {"op": "delete_scope", "payload": {"scope_id": scs[0]["id"]}})
    check("delete_scope removes the loop and its back edge",
          not upd["definition"]["scopes"] and not any(e.get("type") == "cycle" for e in upd["definition"]["edges"]))
    rsrc = (ROOT / "src/trusted_data_agent/api/flow_routes.py").read_text()
    check("the patch route answers a refused loop with 400 + its code",
          'except LoopError as exc:\n        return jsonify({"error": exc.message, "code": exc.code}), 400' in rsrc)
    hsrc = (ROOT / "components/builtin/flow/handler.py").read_text()
    check("the TDA_Flow tool returns a refused loop as a message the model can act on",
          "except LoopError as exc:\n            return self._error_payload(exc.message)" in hsrc)

    # a hand-made loop RUNS exactly like a template loop
    d3 = _hand_flow()
    d3["edges"] = [e for e in d3["edges"] if e["id"] not in ("e4", "e5")]
    d3["nodes"] = [n for n in d3["nodes"] if n["id"] != "f"]
    L.make_loop(d3, ["w", "j"], max_rounds=4)

    def responder(prompt, profile, sid):
        if "VERDICT:" in prompt:
            return "Add a source.\nVERDICT: FAIL" if "draft-1" in prompt else "Good.\nVERDICT: PASS"
        return "draft-2" if "Add a source." in prompt else "draft-1"
    snap, _e, _h, _p, _f = run_flow(d3, responder)
    hs = next(iter(snap["scopes"].values()))
    check("a hand-made loop runs: round 2 reads round 1's review and stops on the pass",
          hs["rounds_completed"] == 2 and hs["converged"] is True, str(hs))


def test_history_window_and_recent():
    print("\n[17 debate history window + recently used agents as suggestions]")
    from trusted_data_agent.core import flow_topologies as topo, flow_rounds
    from trusted_data_agent.core.flow_topologies import debate
    d, _ = topo.generate("debate", {"debaters": 2, "max_rounds": 4})
    check("the debate loop shows each round only the last rounds of argument",
          d["scopes"][0].get("history_window") == debate.DEBATE_HISTORY_WINDOW == 2)
    rounds = [[{"alias": "a", "label": "A", "text": f"round-{i}"}] for i in range(1, 5)]
    ctx = flow_rounds.round_context([], rounds, 4, 5, 2)
    check("the window keeps only the last two rounds in the prompt", "round-3" in ctx["history"] and "round-4" in ctx["history"] and "round-1" not in ctx["history"])
    check("after the loop the whole transcript is still read", "round-1" in flow_rounds.after_loop_context(rounds, {})["history"])

    profs = [{"id": "p1", "tag": "ONE", "profile_type": "llm_only"},
             {"id": "p2", "tag": "TWO", "profile_type": "rag_focused"},
             {"id": "p3", "tag": "THREE", "profile_type": "genie"}]
    sessions = [{"profile_tags_used": ["ONE", "TWO"], "profile_tag": "TWO"},
                {"profile_tags_used": ["GONE"], "profile_tag": "GONE"},
                {"profile_tags_used": [], "profile_tag": "THREE"},
                {"profile_tags_used": ["ONE"], "profile_tag": "ONE"}]
    rec = topo.recent_agents(sessions, profs)
    check("recent agents: newest first, each once, a deleted agent skipped", [p["id"] for p in rec] == ["p2", "p1", "p3"])
    out = topo.preview("pipeline", {"stages": 2}, None, profs, set(), rec)
    sug = out["prefill"]["suggestions"]
    check("recent agents appear as suggestions marked recent on open slots",
          sug and all(all(c.get("recent") for c in row) for row in sug.values()))
    check("…never as fills", not out["prefill"]["filled"] and len(out["open_slots"]) == 2)
    check("…only for classes the slot accepts (a coordinator is not offered to a stage)",
          all(c["id"] != "p3" for row in sug.values() for c in row) or all("genie" in (s.get("accepts") or []) for s in out["slots"]))
    src_one = {"id": "p1", "tag": "ONE", "profile_type": "llm_only"}
    out2 = topo.preview("pipeline", {"stages": 2}, src_one, profs, set(), rec)
    row = next(iter(out2["prefill"]["suggestions"].values()))
    check("the opening agent's suggestion comes first; recent ones follow, not duplicated",
          row[0]["id"] == "p1" and not row[0].get("recent") and [c["id"] for c in row].count("p1") == 1)
    from trusted_data_agent.core.flow_topologies.recent import add_recent_suggestions, PER_SLOT
    many = [{"id": f"x{i}", "tag": f"X{i}", "profile_type": "llm_only"} for i in range(9)]
    dd, _ = topo.generate("pipeline", {"stages": 1})
    sg = {}
    add_recent_suggestions(dd, sg, many)
    check("at most a few suggestions per slot", all(len(v) <= PER_SLOT for v in sg.values()) and sg)
    for n in dd["nodes"]:
        if n.get("data", {}).get("slot"):
            n["data"]["profile_id"] = "p1"
    sg2 = {}
    add_recent_suggestions(dd, sg2, many)
    check("a filled slot gets no suggestions", sg2 == {})


def test_tool_text_matches_registry():
    print("\n[18 the model is told what exists — tool text follows the registry]")
    import json
    from trusted_data_agent.core.flow_topologies import catalog
    ins = json.loads((ROOT / "components/builtin/flow/instructions.json").read_text())
    man = (ROOT / "components/builtin/flow/manifest.json").read_text()
    shipped = [r["key"] for r in catalog() if r.get("available") and r["key"] not in ("blank",) and not r.get("handoff")]
    for level in ("medium", "heavy"):
        txt = ins[level]
        missing = [k for k in shipped if f"'{k}'" not in txt]
        check(f"{level}: every available topology is named to the model", not missing, f"missing {missing}")
        check(f"{level}: no available shape is called 'not yet available'", "not yet available" not in txt)
        check(f"{level}: loops can be made, edited and removed", all(op in txt for op in ("create_scope", "update_scope", "delete_scope")))
    check("heavy names the judge and artefact node types", "judge {" in ins["heavy"] and "artefact {" in ins["heavy"])
    check("the manifest offers delete_scope and no longer claims members are editable in place",
          "'delete_scope'" in man and "(name, max_iterations, node_ids)" not in man)


if __name__ == "__main__":
    for fn in [test_rounds_primitives, test_verdicts, test_exit_and_code, test_generators,
               test_layout_frames_the_loop, test_iterative_converges, test_iterative_runs_out,
               test_abstaining_judge_never_stops, test_panel_judge, test_debate_parallel_and_synthesis,
               test_pair_programming_canvas, test_pair_programming_prose_reply, test_canvas_contents_reach_the_flow, test_teacher_student, test_scope_without_exit_unchanged,
               test_structural_wiring, test_round_limit_edit, test_loop_editing, test_history_window_and_recent, test_tool_text_matches_registry]:
        try:
            fn()
        except Exception as exc:  # a raising test is a NAMED failure, never an aborted run
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} completed without raising", False, repr(exc))
    print("\n" + "=" * 70)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
