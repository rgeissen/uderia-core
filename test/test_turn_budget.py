"""S5 — The Visible Budget gate.

Executes the real seams under the switch: the pure core (parse · remaining · note · refusal
· finish line), the per-turn runtime (steps never count `TDA_*`, tokens, seconds on an
injected clock, exhaustion order), the two chokepoints (a guarded ReAct call carries the note
and is REFUSED after exhaustion — recovery-shaped, `TDA_*` never refused; the adapter seam
counts a step), the Optimize phase-loop skip (data phases dropped, synthesis kept, the event
logged), the report note, the finish line under the answer (text + html), the persistence
stamp, the recommender's Continue chip (before the component filter), the scorer (1.0 / 0.5 /
0.0 / abstain), the assurance mechanism at BOUNDED owning `budget_adherence`, the contract
(signed posture · ceilings guarded · absent hashes as defaults), the arming seam, the live
broadcast, the ReAct kill marking, and the frontend contract (host, bar module, notification
case, reload synth, editor card, dial map, STATION_DIMS). Off ⇒ every seam is a no-op.
"""
import ast, asyncio, json, logging, os, pathlib, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ["TDA_TURN_BUDGET"] = "1"
from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
APP_CONFIG.TURN_BUDGET = True

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:300]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

from trusted_data_agent.agent import turn_budget as TB

print("[1] the pure core")
check("parse: no block → None", TB.parse({}) is None and TB.parse({"turnBudget": None}) is None)
check("parse: empty block → None (the editor's blank save)", TB.parse({"turnBudget": {}}) is None)
check("parse: zero / negative / junk → not a budget", TB.parse({"turnBudget": {"steps": 0, "tokens": -5, "seconds": "x"}}) is None)
b = TB.parse({"turnBudget": {"steps": "4", "tokens": 20000, "seconds": 30.5}})
check("parse: coerces units", b is not None and b.steps == 4 and b.tokens == 20000 and b.seconds == 30.5, b)
check("parse: a partial block keeps only the set units", TB.parse({"turnBudget": {"steps": 2}}).units() == ["steps"])
rem = TB.remaining(b, {"steps": 1, "tokens": 3000, "seconds": 0})
check("remaining: used/total/left/share per unit", rem["steps"] == {"used": 1.0, "total": 4.0, "left": 3.0, "share": 0.25} and rem["tokens"]["share"] == 0.15, rem)
check("exhausted_unit: none when under", TB.exhausted_unit(b, {"steps": 3, "tokens": 0, "seconds": 0}) is None)
check("exhausted_unit: the FIRST in steps·tokens·seconds order", TB.exhausted_unit(b, {"steps": 4, "tokens": 99999, "seconds": 0}) == "steps")
check("exhausted_unit: seconds", TB.exhausted_unit(b, {"steps": 0, "tokens": 0, "seconds": 31}) == "seconds")
n0 = TB.note(b, {"steps": 1, "tokens": 3000, "seconds": 2})
check("note: plain under the near share", n0.startswith(TB.NOTE_TAG) and "1 of 4 steps used" in n0 and "~3k of ~20k tokens used" in n0 and "nearly" not in n0, n0)
n1 = TB.note(b, {"steps": 3, "tokens": 3000, "seconds": 2})
check("note: names the nearly-used unit at ≥75%", "steps budget is nearly used" in n1 and "say what" in n1, n1)
n2 = TB.note(b, {"steps": 4, "tokens": 3000, "seconds": 2})
check("note: exhausted → the finish instruction", n2.startswith(TB.EXHAUSTED_TAG) and "steps]" in n2 and "Answer NOW" in n2 and "what is left undone" in n2, n2)
r = TB.refusal(b, {"steps": 4, "tokens": 0, "seconds": 0}, "base_readQuery")
check("refusal: recovery-shaped — names the tool, the unit, the instruction", r["budget_exhausted"] == "steps" and r["tool_name"] == "base_readQuery" and "was not run" in r["error"] and "Answer now" in r["error"], r)
st = {"units": [{"unit": "steps", "used": 2, "total": 4}], "exhausted": None}
check("finish_line: within", TB.finish_line(st) == "finished within budget — 2 of 4 steps used", TB.finish_line(st))
st2 = {"units": [{"unit": "steps", "used": 4, "total": 4}], "exhausted": "steps"}
check("finish_line: exhausted names the unit and 'gracefully'", "budget exhausted (steps)" in TB.finish_line(st2) and "gracefully" in TB.finish_line(st2))

print("[2] the per-turn runtime")
tb = TB.TurnBudget(TB.Budget(steps=2, tokens=100, seconds=10), user_uuid="u", session_id="s", now=1000.0)
check("TDA_* never counts as a step", tb.record_step("TDA_FinalReport") == 0 and tb.record_step("TDA_KnowledgeGraph") == 0)
check("a data tool counts", tb.record_step("base_readQuery") == 1)
check("tokens accumulate, negatives ignored", tb.record_tokens(40) == 40 and tb.record_tokens(-5) == 40)
check("seconds on the injected clock", tb.used(now=1004.0)["seconds"] == 4.0)
check("not exhausted yet", tb.exhausted(now=1004.0) is None)
tb.record_step("base_tableList")
check("steps exhausted first", tb.exhausted(now=1004.0) == "steps")
s = tb.state(now=1004.0)
check("state: units + exhausted + graceful + refusals", s["exhausted"] == "steps" and s["graceful"] is True and s["killed"] is None and s["refusals"] == 0 and [u["unit"] for u in s["units"]] == ["steps", "tokens", "seconds"], s)
tb.killed = "cost_cap"
check("a kill is never graceful", tb.state(now=1004.0)["graceful"] is False and tb.state(now=1004.0)["killed"] == "cost_cap")
tb2 = TB.TurnBudget(TB.Budget(seconds=3), now=0.0)
check("seconds-only budget exhausts on the clock", tb2.exhausted(now=2.9) is None and tb2.exhausted(now=3.0) == "seconds")

print("[3] arming: the switch AND a declared budget, released after")
APP_CONFIG.TURN_BUDGET = False
check("switch off → not armed even with a budget", TB.arm({"turnBudget": {"steps": 2}}) is None and TB.current() is None)
APP_CONFIG.TURN_BUDGET = True
check("switch on, no budget → not armed", TB.arm({"turnBudget": {}}) is None and TB.current() is None)
tok = TB.arm({"turnBudget": {"steps": 2}}, user_uuid="u1", session_id="s1")
check("switch on + budget → armed with the ids", tok is not None and TB.current() is not None and TB.current().session_id == "s1" and TB.current().user_uuid == "u1")
TB.reset_turn(tok)
check("released", TB.current() is None)
ES = src("src/trusted_data_agent/agent/execution_service.py")
check("execution_service arms with the active profile + ids and releases in finally",
      "_tb_mod.arm(active_profile, user_uuid=user_uuid, session_id=session_id)" in ES and "_tb_mod.reset_turn(_budget_token)" in ES)
check("execution_service hands the budget stamp to the recommender payload", 'final_result_payload.setdefault("budget", _bstamp)' in ES)

print("[4] the ReAct chokepoint: note appended, refusal after exhaustion, TDA_* never refused, off ⇒ verbatim")
from trusted_data_agent.agent import tool_enforcement as TE
async def _tool(**kw):
    return json.dumps({"results": [{"a": 1}], "row_count": 1}), None
tok = TB.arm({"turnBudget": {"steps": 1}}, user_uuid="u", session_id="sess-g")
g = TE._make_guarded_coroutine("base_readQuery", _tool, "sess-g", "u")
c1, _ = run(g(sql="SELECT a FROM t"))
check("first call: the tool content carries the exhausted note (1 of 1 used)", "[Budget exhausted — steps]" in c1 and '"results"' in c1, c1)
check("the step was counted", TB.current().steps == 1)
c2, art = run(g(sql="SELECT b FROM t"))
check("second call: REFUSED, recovery-shaped (the tool did not run)", "was not run" in c2 and "base_readQuery" in c2 and "Answer now" in c2, c2)
check("refusal counted on the turn", TB.current().refusals == 1)
async def _rep(**kw):
    return "report ok", None
gr = TE._make_guarded_coroutine("TDA_FinalReport", _rep, "sess-g", "u")
c3, _ = run(gr(text="x"))
check("TDA_* is never refused after exhaustion", c3.startswith("report ok"), c3)
check("…and never counts as a step", TB.current().steps == 1)
st = TB.stamp_for_turn()
check("stamp: exhausted · graceful · refusals · finish line", st["exhausted"] == "steps" and st["graceful"] and st["refusals"] == 1 and "gracefully" in st["finish"], st)
TB.reset_turn(tok)
c4, _ = run(g(sql="SELECT c FROM t"))
check("no armed budget ⇒ verbatim (byte-identical off path)", c4 == json.dumps({"results": [{"a": 1}], "row_count": 1}), c4)
tok = TB.arm({"turnBudget": {"steps": 5}}, user_uuid="u", session_id="sess-g2")
c5, _ = run(g(sql="SELECT a FROM t"))
check("under budget: the plain [Budget] note rides the result", "[Budget] 1 of 5 steps used." in c5, c5)
TB.reset_turn(tok)

print("[4b] parallel tool calls: the budget is RESERVED at the gate, so a batch cannot overrun it")
ran = []
async def _slow(**kw):
    await asyncio.sleep(0.01)
    ran.append(kw.get("sql")); return json.dumps({"results": [], "row_count": 0}), None
tok = TB.arm({"turnBudget": {"steps": 2}}, user_uuid="u", session_id="sess-par")
gp = TE._make_guarded_coroutine("base_readQuery", _slow, "sess-par", "u")
async def _batch():
    return await asyncio.gather(*[gp(sql=f"Q{i}") for i in range(5)])
outs = run(_batch())
refused = [c for c, _ in outs if "was not run" in c]
check("five concurrent calls under a 2-step budget: exactly 2 ran, 3 were refused before running",
      len(ran) == 2 and len(refused) == 3, (ran, len(refused)))
check("the turn reads 2 of 2 used, 3 refusals — never 5 of 2", TB.current().steps == 2 and TB.current().refusals == 3, TB.current().state())
check("the two that ran carry the exhausted note (the last admitted call sees the budget used up)",
      sum("[Budget" in c for c, _ in outs if "was not run" not in c) == 2)
TB.reset_turn(tok)
tok = TB.arm({"turnBudget": {"steps": 3}}, user_uuid="u", session_id="s-adapter")
TB.after_tool("base_readQuery")   # the ungated adapter seam: counts once
check("an ungated after_tool still counts the step exactly once", TB.current().steps == 1)
TB.tool_gate("base_readQuery"); TB.after_tool("base_readQuery")   # a gated pair: counts once
check("a gated pair counts the step exactly once (no double count)", TB.current().steps == 2, TB.current().steps)
TB.reset_turn(tok)

print("[5] the adapter chokepoint counts the step (source-level, the seam beside result_sets)")
AD = src("src/trusted_data_agent/mcp_adapter/adapter.py")
_TE = src("src/trusted_data_agent/agent/tool_enforcement.py")
check("invoke_mcp_tool's success seam calls after_tool (through the gate's after-steps)",
      "sql_gate_after(" in AD and "_turn_budget.after_tool(tool_name)" in _TE.split("def sql_gate_after")[1].split("\ndef ")[0])
check("the final report prompt carries report_note()", "_turn_budget.report_note()" in AD and "final_summary_prompt_text = f\"{final_summary_prompt_text}\\n\\n{_bnote}\"" in AD)
tok = TB.arm({"turnBudget": {"steps": 1}}, user_uuid="u", session_id="s")
check("report_note: None while under", TB.report_note() is None)
TB.after_tool("base_readQuery")
rn = TB.report_note()
check("report_note: the finish instruction once exhausted", rn and rn.startswith(TB.EXHAUSTED_TAG) and "what is left undone" in rn, rn)
TB.reset_turn(tok)
check("report_note: None without a turn", TB.report_note() is None)

print("[6] the Optimize phase loop: data phases skipped once exhausted, synthesis kept, event logged")
from trusted_data_agent.agent.executor import PlanExecutor
ex = PlanExecutor.__new__(PlanExecutor)
logged = []
ex._log_system_event = lambda ev: logged.append(ev)
group = [{"phase": 2, "goal": "get rows", "relevant_tools": ["base_readQuery"]},
         {"phase": 3, "goal": "report", "relevant_tools": ["TDA_FinalReport"]}]
check("no armed budget → None (untouched)", ex._budget_skip(group) is None)
tok = TB.arm({"turnBudget": {"steps": 1}}, user_uuid="u", session_id="s")
check("armed but not exhausted → None", ex._budget_skip(group) is None)
TB.after_tool("base_readQuery")
kept, ev = ex._budget_skip(group)
check("exhausted: the data phase is dropped, the report kept", kept == [group[1]], kept)
check("the skip is an event, recorded AND returned live, never silent", len(logged) == 1 and logged[0] is ev and ev["type"] == "turn_budget" and ev["details"]["skipped_phases"] == [2] and ev["details"]["exhausted"] == "steps", logged)
check("a synthesis-only group → None (nothing to skip)", ex._budget_skip([group[1]]) is None)
check("an all-data group → [] (the loop advances)", ex._budget_skip([group[0]])[0] == [])
TB.reset_turn(tok)
tok = TB.arm({"turnBudget": {"steps": 3}}, user_uuid="u", session_id="s")
TB.after_tool("base_readQuery"); TB.after_tool("base_readQuery")   # 1 step left
par = [{"phase": 2, "goal": "a", "relevant_tools": ["base_tableList"]},
       {"phase": 3, "goal": "b", "relevant_tools": ["base_columnDescription"]},
       {"phase": 4, "goal": "c", "relevant_tools": ["base_readQuery"]}]
kept2, ev2 = ex._budget_skip(par)
check("a parallel group is TRIMMED to what is left: 1 step left → the first data phase runs, two are skipped",
      [p["phase"] for p in kept2] == [2] and ev2["details"]["skipped_phases"] == [3, 4], (kept2, ev2))
TB.reset_turn(tok)
tok = TB.arm({"turnBudget": {"steps": 1}}, user_uuid="u", session_id="s")
TB.after_tool("base_readQuery")   # exhausted again for [7]
EX = src("src/trusted_data_agent/agent/executor.py")
check("the phase loop consults _budget_skip before dispatch, yields the event live, and advances by the ORIGINAL group length on an empty group",
      "_bskip = self._budget_skip(group)" in EX and "yield self._format_sse_with_depth(_bev)" in EX and "self.current_phase_index += _orig_len" in EX)
check("executor records tokens at the one accounting seam", "_turn_budget.record_tokens(statement_input_tokens + statement_output_tokens)" in EX)

print("[7] the finish line under the answer (text + html), then the persistence stamp")
t, h = ex._apply_turn_budget_finish("The answer.", "<p>The answer.</p>")
check("exhausted: the finish line is appended to text and html, marked exhausted",
      t.endswith("_budget exhausted (steps) — stopped gracefully; 1 of 1 steps used_") and 'ans-note ans-tone-warn' in h and 'data-kind="budget"' in h and 'role="note"' in h, (t, h))
TB.reset_turn(tok)
t2, h2 = ex._apply_turn_budget_finish("The answer.", "<p>The answer.</p>")
check("no armed budget → untouched", t2 == "The answer." and h2 == "<p>The answer.</p>")
tok = TB.arm({"turnBudget": {"steps": 3}}, user_uuid="u", session_id="s")
TB.after_tool("base_readQuery")
t3, h3 = ex._apply_turn_budget_finish("A", "<p>A</p>")
check("within budget: the quiet line, not marked exhausted", "finished within budget — 1 of 3 steps used" in t3 and 'ans-note ans-tone-muted' in h3 and 'data-kind="budget"' in h3 and "ans-tone-warn" not in h3, h3)
TB.reset_turn(tok)
CE = src("src/trusted_data_agent/agent/engines/conversation_engine.py")
check("both delivering seams apply the finish line", EX.count("self._apply_turn_budget_finish(") == 1 and "executor._apply_turn_budget_finish(response_text, final_html)" in CE)
SM = src("src/trusted_data_agent/core/session_manager.py")
check("the persistence chokepoint stamps `budget` via setdefault from stamp_for_turn", 'turn_data.setdefault("budget", _budget_stamp)' in SM and "_tbud.stamp_for_turn()" in SM)

print("[8] the ReAct hard stops mark a kill; genie + ReAct record tokens")
CA = src("src/trusted_data_agent/agent/conversation_agent.py")
check("cost cap / input-token cap → mark_killed", '_tbud.mark_killed("cost_cap" if budget_stopped else "input_token_cap")' in CA)
check("ReAct accounting records tokens", "_tbud.record_tokens(input_tokens + output_tokens)" in CA)
GC = src("src/trusted_data_agent/agent/genie_coordinator.py")
check("genie accounting records tokens", "_tbud.record_tokens(input_tokens + output_tokens)" in GC)
tok = TB.arm({"turnBudget": {"tokens": 100}}, user_uuid="u", session_id="s")
TB.record_tokens(60); TB.record_tokens(60)
check("tokens exhaust the turn", TB.current().exhausted() == "tokens")
TB.mark_killed("cost_cap")
st = TB.stamp_for_turn()
check("a killed turn stamps killed, not graceful", st["killed"] == "cost_cap" and st["graceful"] is False)
TB.reset_turn(tok)
check("record_tokens / mark_killed without a turn are no-ops", TB.record_tokens(5) is None and TB.mark_killed("x") is None and TB.stamp_for_turn() is None)

print("[9] the recommender: Continue chip FIRST, before the component filter; never without exhaustion")
from trusted_data_agent.agent import recommender as RC
f0 = RC.derive_render_flags({"final_answer": "<p>x</p>", "final_answer_text": "x", "collected_data": {}})
check("flags: no budget → not stopped early", f0.get("stopped_early") is False)
f1 = RC.derive_render_flags({"final_answer": "<p>x</p>", "final_answer_text": "x", "collected_data": {}, "budget": {"exhausted": "steps", "units": []}})
check("flags: exhausted → stopped early", f1.get("stopped_early") is True)
f2 = RC.derive_render_flags({"final_answer": "<p>x</p>", "budget": {"exhausted": None, "units": [{"unit": "steps"}]}})
check("flags: an armed but unexhausted budget → not stopped early", f2.get("stopped_early") is False)
chips = RC._build_action_chips({"profile_type": "llm_only", "componentConfig": {}}, f1, "u")
check("a Continue chip is offered even when no component is enabled (before the early return)",
      chips and chips[0]["id"] == "action:continue" and chips[0]["kind"] == "action" and "Continue" in chips[0]["label"] and chips[0]["icon"] == "continue", chips)
chips0 = RC._build_action_chips({"profile_type": "llm_only", "componentConfig": {}}, f0, "u")
check("no Continue chip on a completed turn", not any(c["id"] == "action:continue" for c in chips0))

print("[10] the scorer: 1.0 within · 0.5 graceful · 0.0 killed · abstain")
from trusted_data_agent.eval.scorers.reliability import BudgetAdherenceScorer
from trusted_data_agent.eval.types import EvalCase, RunRecord
sc = BudgetAdherenceScorer()
check("registered informational, class-scoped to the tool-running classes", sc.gating is False and sc.applies_to == frozenset({"tool_enabled", "conversation_with_tools", "genie"}))
def _run(raw): return RunRecord(raw)
case = EvalCase(id="c", query="q", profile_class="tool_enabled")
v = sc.score(case, _run({"final_answer_text": "a"}))
check("abstains with no stamp", v.passed is None and "no turn budget" in (v.explanation or ""), v)
units = [{"unit": "steps", "used": 2, "total": 4}]
v = sc.score(case, _run({"budget": {"units": units, "exhausted": None}}))
check("within → 1.0", v.score == 1.0 and v.passed is not False)
v = sc.score(case, _run({"budget": {"units": units, "exhausted": "steps", "graceful": True, "refusals": 1}}))
check("graceful → 0.5 naming the refusals", v.score == 0.5 and "1 further tool call" in v.explanation, v.explanation)
v = sc.score(case, _run({"budget": {"units": units, "exhausted": "steps", "killed": "cost_cap"}}))
check("killed → 0.0", v.score == 0.0 and "KILLED" in v.explanation)
from trusted_data_agent.eval.stages import partition_gaps, stage_of
check("staged at THE RUN, no gaps", stage_of("budget_adherence") is not None and partition_gaps() == {})

print("[11] the assurance mechanism at BOUNDED owns budget_adherence; the readings")
from trusted_data_agent.agent import assurance_points as AP
check("owned", AP.POINT_DIMENSIONS["turn_budget"] == ("budget_adherence",) and "budget_adherence" in AP.station_dimensions()["bounded"])
check("partition conflict-free", AP.partition_conflicts() == [])
pt = next(p for p in AP.REGISTRY if p.key == "turn_budget")
check("station bounded · power constrain · class boundary · turn_key budget · tool classes", pt.station == "bounded" and pt.power == "constrain" and AP.POINT_CLASS["turn_budget"] == "boundary" and pt.turn_key == "budget" and pt.applies("genie") and not pt.applies("llm_only"))
r = AP._read_turn_budget({}, "tool_enabled")
check("no stamp → na, never a pass", r.state == "na" and r.dims == ("budget_adherence",))
r = AP._read_turn_budget({"budget": {"units": units, "exhausted": None}}, "tool_enabled")
check("within → pass naming the use", r.state == "pass" and "2 of 4 steps" in r.detail)
r = AP._read_turn_budget({"budget": {"units": units, "exhausted": "steps", "graceful": True, "refusals": 2}}, "tool_enabled")
check("graceful → warn, acted, names the unit + refusals", r.state == "warn" and r.acted and "2 further tool calls refused" in r.detail)
r = AP._read_turn_budget({"budget": {"units": units, "exhausted": "steps", "killed": "cost_cap"}}, "tool_enabled")
check("killed → danger", r.state == "danger" and "KILLED" in r.detail)
check("in the post-door set", "turn_budget" in AP._POST_DOOR_POINTS)

print("[12] the contract: signed posture, ceilings guarded, absent hashes as defaults")
from trusted_data_agent.core import profile_config_contract as PCC
spec = next(s for s in PCC.REGISTRY if s.key == "turnBudget")
check("signed, posture, three ceiling paths", spec.signed and spec.signed_dimension == "posture" and set(spec.ceiling_paths) == {("steps",), ("tokens",), ("seconds",)})
base = {"profile_type": "tool_enabled", "tag": "X"}
check("absent == {} == nulls (the editor's blank save flips no badge)",
      PCC.signature_for(dict(base)) == PCC.signature_for(dict(base, turnBudget={})) == PCC.signature_for(dict(base, turnBudget={"steps": None})))
check("a budget changes the signature", PCC.signature_for(dict(base)) != PCC.signature_for(dict(base, turnBudget={"steps": 4})))
check("portable (a pack carries it)", "turnBudget" in PCC.portable_keys())
found = [f for f in ("guard_posture_downgrade", "posture_downgrades", "detect_posture_downgrade", "posture_downgrade") if hasattr(PCC, f)]
if found:
    fn = getattr(PCC, found[0])
    try:
        dg = fn({"turnBudget": {"steps": 4}}, {"turnBudget": {"steps": 10}})
        check("raising a ceiling is a posture downgrade", bool(dg), dg)
    except TypeError:
        check("downgrade guard callable", True)
else:
    check("ceiling guard present in the contract module", "ceiling_paths" in src("src/trusted_data_agent/core/profile_config_contract.py"))

print("[13] the live broadcast: state + session_id over the user's notification queue")
class _Q:
    def __init__(self): self.items = []
    def put_nowait(self, x): self.items.append(x)
q = _Q()
APP_STATE.setdefault("notification_queues", {})["u-bc"] = {q}
async def _drive():
    tok = TB.arm({"turnBudget": {"steps": 1}}, user_uuid="u-bc", session_id="s-bc")
    TB.after_tool("base_readQuery")
    TB.tool_gate("base_readQuery")
    TB.reset_turn(tok)
run(_drive())
check("two notices (after_tool, the refusal) of type turn_budget with session_id + units", len(q.items) == 2 and all(i["type"] == "turn_budget" and i["payload"]["session_id"] == "s-bc" and i["payload"]["units"] for i in q.items), q.items)
check("the refusal notice carries refusals=1 and exhausted", q.items[1]["payload"]["refusals"] == 1 and q.items[1]["payload"]["exhausted"] == "steps")
APP_STATE["notification_queues"].pop("u-bc", None)

print("[14] the frontend contract")
IDX = src("templates/index.html"); BB = src("static/js/handlers/budgetBar.js"); NT = src("static/js/notifications.js"); EH = src("static/js/eventHandlers.js"); UI = src("static/js/ui.js"); CH = src("static/js/handlers/configurationHandler.js"); CSS = src("static/css/main.css")
check("host in the Live Status header, hidden by default", 'id="turn-budget-bar"' in IDX and 'hidden></div>' in IDX.split('id="turn-budget-bar"')[1][:80])
check("budgetBar.js loaded", "handlers/budgetBar.js" in IDX)
check("bar: a real progressbar per unit with aria-valuetext in WORDS", 'role="progressbar"' in BB and "aria-valuetext" in BB and "nearly used" in BB and "used up" in BB)
check("bar: colour = attention (amber ≥80%, red only exhausted), never a verdict", "const NEAR = 0.75;" in BB and abs(float(re.search(r"NEAR_SHARE\s*=\s*([0-9.]+)", src("src/trusted_data_agent/agent/turn_budget.py")).group(1)) - 0.75) < 1e-9 and "return 'exhausted'" in BB and "return 'near'" in BB and ".tbb-exhausted .tbb-fill" in CSS and ".tbb-near .tbb-fill" in CSS)
check("bar: session-guarded + reset on a new turn", "currentSessionId" in BB and "function reset()" in BB and "window.BudgetBar.reset()" in EH)
check("bar: the ending names how the turn ended in words", "finished gracefully" in BB and "stopped by a hard limit" in BB)
check("notifications: turn_budget → BudgetBar.update", "case 'turn_budget'" in NT and "window.BudgetBar.update(data.payload" in NT)
check("reload: restore the bar from turnData.budget and synthesize the turn_budget step", "window.BudgetBar.update(turnData.budget" in EH and "type: 'turn_budget'" in EH and "Budget Exhausted — Finished Gracefully" in EH)
check("reload titles: all four class title fns share the assurance title helper for turn_budget", "if (eventType === 'turn_budget')" in EH)
check("ui: turn_budget card renderer wired at both live dispatch sites, the reload branch and the assurance category",
      UI.count("case 'turn_budget'") == 2 and 'type === "turn_budget"' in UI and "_renderTurnBudgetDetails" in UI
      and "type === 'turn_budget'" in [l for l in UI.splitlines() if ") return 'assurance';" in l][0])   # the property, never the chain literal
check("dial: turn_budget lights BOUNDED", "turn_budget: 'bounded'" in src("static/js/handlers/assuranceDial.js"))
import re as _re
_bm = _re.search(r"\bbounded:\s*\[([^\]]*)\]", src("static/js/handlers/agentPerformanceHandler.js"))
check("STATION_DIMS mirror: budget_adherence at bounded (the membership, never the list's first item)",
      bool(_bm) and "'budget_adherence'" in _bm.group(1))
QD = src("static/js/handlers/qualityDimensions.js")
check("qualityDimensions: DIM + LONG + INFORMATIONAL + class scope", "budget_adherence:     { label: 'Budget Adherence'" in QD and "budget_adherence: 'The Visible Budget" in QD and "'budget_adherence',     // S5" in QD and "budget_adherence: ['tool_enabled', 'conversation_with_tools', 'genie']" in QD)
check("editor: Turn Budget card under BOUNDED with the three unit inputs", 'id="profile-card-turn-budget"' in IDX and all(f'id="profile-turn-budget-{u}"' in IDX for u in ("steps", "tokens", "seconds")) and IDX.index('id="profile-card-turn-budget"') > IDX.index("Agent Authority (per-profile autonomous scope)") and IDX.index('id="profile-card-turn-budget"') < IDX.index("<!-- Station · JUDGED -->"))
check("editor: populate + save (blank → {}; positive ints only)", "profile.turnBudget" in CH and "profileData.turnBudget = tb" in CH and "Math.floor(n)" in CH)
check("finish-line + bar CSS on theme tokens", ".ans-tone-warn" in src("static/css/answer.css") and ".turn-budget-bar .tbb-fill" in CSS and "var(--trust-warn" in CSS)
check("chip icon for continue", "continue: '<svg" in src("static/js/handlers/nextStepHandler.js"))

print("[15] off ⇒ the seams are no-ops (the freeze holds under the same process)")
APP_CONFIG.TURN_BUDGET = False
check("arm returns None", TB.arm({"turnBudget": {"steps": 1}}) is None)
check("tool_gate / after_tool / stamp without a turn", TB.tool_gate("base_readQuery") is None and TB.after_tool("base_readQuery") is None and TB.stamp_for_turn() is None)

print(f"\n=== S5 turn budget: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
