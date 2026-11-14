"""S7 — The Outcome Loop gate.

Executes the real seams under the switch: the pure core (parse · the revisable-kinds truth
table · should_revise incl. the bound and the block rule · the note · cleared), the loop driver
with an injected gate + reviser (clears → the revision delivered with ITS gate event; does not
clear → the annotated FIRST draft delivered, never a second silent try; the bound respected;
an empty revision; a blocked draft never revised; both drafts recorded; the revision's cost
recorded), the executor's grounding entrypoint (ONE seam every engine calls: the gate, then
the loop, the revise call billed through `_call_llm_and_update_tokens`), the provenance seal +
verify of both drafts, the persistence stamp, the assurance mechanism at GROUNDED, the contract
(signed posture, flag + floor, absent hashes as the off block), the genie lane's reviser, and
the frontend contract. Off ⇒ the gate's result verbatim (the freeze).
"""
import asyncio, json, logging, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ["TDA_OUTCOME_LOOP"] = "1"
from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
APP_CONFIG.OUTCOME_LOOP = True

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:320]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

from trusted_data_agent.agent import outcome_loop as OL

print("[1] the pure core")
check("parse: absent / disabled → off", not OL.parse({}).enabled and not OL.parse({"outcomeConfig": {"enabled": False, "maxRevisions": 2}}).enabled)
c = OL.parse({"outcomeConfig": {"enabled": True, "maxRevisions": 5, "revisableKinds": ["ungrounded_number", "bogus"]}})
check("parse: maxRevisions clamped to 2, unknown kinds dropped", c.enabled and c.max_revisions == 2 and c.kinds == ("ungrounded_number",), c)
check("parse: default 1 revision, every kind; an EXPLICIT empty kinds list = nothing revisable (review: the editor can say none)", OL.parse({"outcomeConfig": {"enabled": True}}).max_revisions == 1 and OL.parse({"outcomeConfig": {"enabled": True}}).kinds == OL.KINDS and OL.parse({"outcomeConfig": {"enabled": True, "revisableKinds": []}}).kinds == ())
check("parse: a junk maxRevisions → default, never a crash", OL.parse({"outcomeConfig": {"enabled": True, "maxRevisions": "x"}}).max_revisions == 1)
EV = {"ran": True, "decision": "annotate", "unsupported_claims": ["the moon is cheese", "cows fly"], "answer_oos_refs": ["finance_ledger"],
      "scope_databases": ["fitness_db"], "ungrounded_numbers": ["4242"], "judged": True, "judge_passed": False}
fs = OL.findings_from_event(EV)
check("findings: the three deterministic kinds, with their spans and a fix hint each", [f.kind for f in fs] == ["unsupported_claim", "out_of_scope_ref", "ungrounded_number"] and fs[0].spans == ("the moon is cheese", "cows fly") and "fitness_db" in fs[1].hint and all(f.hint for f in fs), fs)
check("findings: a judge's ruling alone is NOT revisable (a verdict, not a span)", OL.findings_from_event({"ran": True, "decision": "annotate", "judged": True, "judge_passed": False}) == [])
check("findings: kinds filter honoured; a gate that did not run yields nothing", [f.kind for f in OL.findings_from_event(EV, ("ungrounded_number",))] == ["ungrounded_number"] and OL.findings_from_event({"ran": False, "unsupported_claims": ["x"]}) == [] and OL.findings_from_event(None) == [])
cfg = OL.parse({"outcomeConfig": {"enabled": True, "maxRevisions": 1}})
check("should_revise: yes with findings, the bound unspent, not a block", OL.should_revise(cfg, fs, 0, "annotate"))
check("should_revise: never on a BLOCK (a revision of a withheld answer is a second silent try)", not OL.should_revise(cfg, fs, 0, "block"))
check("should_revise: the bound", not OL.should_revise(cfg, fs, 1, "annotate") and OL.should_revise(OL.parse({"outcomeConfig": {"enabled": True, "maxRevisions": 2}}), fs, 1, "annotate"))
check("should_revise: nothing to fix / config off / maxRevisions 0", not OL.should_revise(cfg, [], 0, "annotate") and not OL.should_revise(OL.parse({}), fs, 0, "annotate") and not OL.should_revise(OL.parse({"outcomeConfig": {"enabled": True, "maxRevisions": 0}}), fs, 0, "annotate"))
APP_CONFIG.OUTCOME_LOOP = False
check("should_revise: the switch", not OL.should_revise(cfg, fs, 0, "annotate"))
APP_CONFIG.OUTCOME_LOOP = True
note = OL.revision_note(fs)
check("the note names each finding, its spans and its fix, and asks for ONLY the revised answer", '"the moon is cheese"' in note and "finance_ledger" in note and "may reference only: fitness_db" in note and "4242" in note and "Return ONLY the revised answer" in note and "rubric" not in note.lower(), note)
big = OL.Finding("unsupported_claim", tuple(f"claim {i}" for i in range(9)))
check("the note bounds the spans it quotes", "(+3 more)" in OL.revision_note([big]))
check("cleared = no revisable finding remains", OL.cleared(fs, []) and not OL.cleared(fs, fs[:1]))
st = OL.state(revisions=1, cleared_flag=True, findings_before=fs, findings_after=[], drafts=[{"which": "first", "text": "t", "findings": [], "_sealed": True}], delivered="revision", cost_usd=0.00123, tokens_in=10, tokens_out=5)
check("state: append-only record, private bookkeeping stripped, cost carried", st["revisions"] == 1 and st["cleared"] and st["drafts"] == [{"which": "first", "text": "t", "findings": []}] and st["cost_usd"] == 0.00123)
check("summary: cleared / not cleared read differently and name the kinds", "removed" in OL.summary(st) and "not cleared" in OL.summary({**st, "cleared": False}) and "unsupported claim" in OL.summary(st))

print("[2] the loop driver: clears → the revision delivered; not → the annotated first draft")
GOOD = {"ran": True, "decision": "pass", "unsupported_claims": [], "answer_oos_refs": [], "ungrounded_numbers": []}
BAD = {"ran": True, "decision": "annotate", "unsupported_claims": ["the moon is cheese"], "answer_oos_refs": [], "ungrounded_numbers": []}
async def _drive(grade_results, revise_texts, profile=None, first_event=BAD, decision_first=None, raw="DRAFT: the moon is cheese"):
    calls = {"grade": [], "revise": []}
    async def grade(text):
        calls["grade"].append(text); r = grade_results.pop(0); return r
    async def revise(prompt):
        calls["revise"].append(prompt); t = revise_texts.pop(0); return t, 100, 20, 0.001
    events = []
    async def on_event(ev): events.append(ev)
    tok = OL.set_turn(None)
    try:
        out = await OL.run_loop(first_text="DRAFT: the moon is cheese\n\n_annotated_", first_event=first_event, raw_first=raw,
                                profile=(profile if profile is not None else {"outcomeConfig": {"enabled": True, "maxRevisions": 1}}), question="q",
                                grade=grade, revise=revise, user_uuid="u", session_id="s", on_event=on_event)
        stamp = OL.stamp_for_turn(); t = OL.current()
    finally:
        OL.reset_turn(tok)
    return out, calls, events, stamp, t
(text, ev), calls, events, stamp, t = run(_drive([("REVISED clean", GOOD)], ["REVISED clean"]))
check("cleared: the revision's gated text + ITS gate event are delivered, the event NAMING the delivered revision", text == "REVISED clean" and ev.get("outcome_delivered") == "revision" and {k: v for k, v in ev.items() if k != "outcome_delivered"} == GOOD, ev)
check("the reviser saw the RAW first draft (not the annotated text) + the note", "DRAFT: the moon is cheese" in calls["revise"][0] and "_annotated_" not in calls["revise"][0] and "the moon is cheese" in calls["revise"][0] and "Revise it" in calls["revise"][0], calls["revise"][0][:200])
check("the gate re-graded the revision exactly once", calls["grade"] == ["REVISED clean"])
check("both drafts recorded, cleared, revision delivered, cost carried", stamp["revisions"] == 1 and stamp["cleared"] and stamp["delivered"] == "revision" and [d["which"] for d in stamp["drafts"]] == ["first", "revision_1"] and stamp["drafts"][0]["text"] == "DRAFT: the moon is cheese" and stamp["drafts"][0]["findings"][0]["spans"] == ["the moon is cheese"] and stamp["tokens_in"] == 100 and abs(stamp["cost_usd"] - 0.001) < 1e-9, stamp)
check("one live event, saying it cleared", len(events) == 1 and events[0]["type"] == "outcome_revision" and events[0]["details"]["cleared"] is True and "cleared" in events[0]["details"]["summary"])
(text, ev), calls, events, stamp, t = run(_drive([("REVISED still bad\n\n_annotated_", BAD)], ["REVISED still bad"]))
check("not cleared: the annotated FIRST draft is delivered — never the failed revision", text == "DRAFT: the moon is cheese\n\n_annotated_" and ev is BAD)
check("…recorded as revised, not cleared, first delivered, with the revision kept", stamp["revisions"] == 1 and not stamp["cleared"] and stamp["delivered"] == "first" and stamp["drafts"][1]["text"] == "REVISED still bad" and stamp["findings_after"][0]["kind"] == "unsupported_claim")
(text, ev), calls, events, stamp, t = run(_drive([("R1\n\n_annotated_", BAD), ("R2", GOOD)], ["R1", "R2"], profile={"outcomeConfig": {"enabled": True, "maxRevisions": 2}}))
check("maxRevisions 2: a second try, the second draft is what the reviser sees, cleared on the second", text == "R2" and stamp["revisions"] == 2 and stamp["cleared"] and "R1" in calls["revise"][1] and [d["which"] for d in stamp["drafts"]] == ["first", "revision_1", "revision_2"])
(text, ev), calls, events, stamp, t = run(_drive([("R1", BAD), ("R2", BAD)], ["R1", "R2"], profile={"outcomeConfig": {"enabled": True, "maxRevisions": 2}}))
check("the bound: exactly 2 revisions, then the first draft", stamp["revisions"] == 2 and len(calls["revise"]) == 2 and text == "DRAFT: the moon is cheese\n\n_annotated_")
(text, ev), calls, events, stamp, t = run(_drive([], [""]))
check("an empty revision: no re-grade, first draft delivered, the empty draft recorded honestly", calls["grade"] == [] and text.startswith("DRAFT") and stamp["drafts"][1].get("empty") is True)
(text, ev), calls, events, stamp, t = run(_drive([], [], first_event={**BAD, "decision": "block"}))
check("a BLOCKED draft is never revised (no LLM call) and no stamp is written", calls["revise"] == [] and stamp is None)
(text, ev), calls, events, stamp, t = run(_drive([], [], first_event=GOOD))
check("a clean draft → no revision, no stamp", calls["revise"] == [] and stamp is None and text.startswith("DRAFT"))
(text, ev), calls, events, stamp, t = run(_drive([], [], profile={}))
check("no outcomeConfig → untouched", calls["revise"] == [] and stamp is None)
(text, ev), calls, events, stamp, t = run(_drive([("REVISED clean", GOOD)], ["Revised answer:\n---\nREVISED clean\n---"]))
check("an echoed framing (label + the prompt's --- delimiters) is unwrapped BEFORE the re-grade and the delivery", calls["grade"] == ["REVISED clean"] and text == "REVISED clean" and stamp["drafts"][1]["text"] == "REVISED clean")
check("unwrap: only whole leading/trailing framing lines go — an inner rule or fence survives", OL.unwrap_revision("A\n\n---\n\nB") == "A\n\n---\n\nB" and OL.unwrap_revision("```\nX\n```") == "X" and OL.unwrap_revision("  ") == "")
check("the note forbids the echo explicitly", "do not repeat the --- delimiters" in OL.revision_note(fs))
async def _boom(prompt): raise RuntimeError("provider down")
async def _drive_err():
    tok = OL.set_turn(None)
    try:
        async def grade(text): return text, GOOD
        return await OL.run_loop(first_text="F", first_event=BAD, raw_first="F", profile={"outcomeConfig": {"enabled": True}}, question="q", grade=grade, revise=_boom)
    finally:
        OL.reset_turn(tok)
text, ev = run(_drive_err())
check("a failing reviser fails OPEN: the first draft exactly as the gate returned it", text == "F" and ev is BAD)

print("[3] the executor's grounding entrypoint: ONE seam, gate then loop, the revise call billed")
from trusted_data_agent.agent.executor import PlanExecutor
from trusted_data_agent.agent.grounding import runtime as GR
ex = PlanExecutor.__new__(PlanExecutor)
ex.kg_enrichment_event = None; ex.user_uuid = "u"; ex.session_id = "s"; ex.current_turn_number = 1
ex.original_user_input = "is the moon cheese?"; ex.event_handler = None; ex._cw_knowledge_chunks = None
ex._resolve_gate_profile = lambda kind: {"tag": "T", "groundingConfig": {"mode": "annotate"}, "outcomeConfig": {"enabled": True, "maxRevisions": 1}}
ex._detect_profile_type = lambda: "tool_enabled"
ex.turn_cost_usd = 0.0
logged = []; ex._log_system_event = lambda ev: logged.append(ev)
_orig = GR.run_grounding
async def _gate(text, **kw):
    if "cheese" in text:
        return text + "\n\n_annotated_", dict(BAD)
    return text, dict(GOOD)
GR.run_grounding = _gate
llm = []
async def _llm(prompt, reason, **kw):
    llm.append((prompt, reason, kw)); ex.turn_cost_usd += 0.002; return "The moon is rock.", 120, 30
ex._call_llm_and_update_tokens = _llm
q = type("Q", (), {"items": [], "put_nowait": lambda self, x: self.items.append(x)})()
APP_STATE.setdefault("notification_queues", {})["u"] = {q}
async def _exec_drive():
    # the contextvar is task-scoped: read the stamp INSIDE the task the seam ran in
    tok = OL.set_turn(None)
    try:
        text, ev = await ex._run_grounding_gate_full("The moon is cheese.")
        return text, ev, OL.stamp_for_turn()
    finally:
        OL.reset_turn(tok)
try:
    text, ev, stamp = run(_exec_drive())
finally:
    GR.run_grounding = _orig; APP_STATE["notification_queues"].pop("u", None)
check("the revision cleared → the revised text and the clean event are what the seam returns", text == "The moon is rock." and ev.get("decision") == "pass", (text, ev))
check("ONE revise call, history disabled, the reason names it, the draft + note in the prompt", len(llm) == 1 and llm[0][1] == "Outcome Revision" and llm[0][2].get("disabled_history") is True and "The moon is cheese." in llm[0][0] and "Revise it" in llm[0][0])
check("the revision's cost is the turn's cost delta (billed through the accumulator)", abs(stamp["cost_usd"] - 0.002) < 1e-9 and stamp["tokens_in"] == 120)
check("the step is recorded in the trace AND pushed live over the notification channel, session-keyed", logged and logged[0]["type"] == "outcome_revision" and q.items and q.items[0]["type"] == "outcome_revision" and q.items[0]["payload"]["session_id"] == "s")
# the RE-GRADE is real: a revision that still says cheese must NOT be delivered
llm.clear(); logged.clear()
async def _llm_bad(prompt, reason, **kw):
    llm.append((prompt, reason, kw)); return "The moon is still cheese.", 50, 10
ex._call_llm_and_update_tokens = _llm_bad
GR.run_grounding = _gate
try:
    text2, ev2, stamp2 = run(_exec_drive())
finally:
    GR.run_grounding = _orig
check("a revision the gate still fails is NOT delivered — the annotated first draft is (the re-grade is the real gate)", text2 == "The moon is cheese.\n\n_annotated_" and ev2.get("decision") == "annotate" and stamp2 and not stamp2["cleared"] and len(llm) == 1, (text2, stamp2))
EX = src("src/trusted_data_agent/agent/executor.py")
check("the seam is the ONE grounding entrypoint every engine calls (gate → run_loop, raw_first = the model's text)", "return await _outcome_loop.run_loop(" in EX and "raw_first=answer_text" in EX and "grade=_grade, revise=self._outcome_revise" in EX)
CE = src("src/trusted_data_agent/agent/engines/coordinate_engine.py"); GC = src("src/trusted_data_agent/agent/genie_coordinator.py")
check("the genie lane runs the same loop over the coordinator's answer with its own priced reviser", "_oloop.run_loop(" in CE and "revise=_genie_revise" in CE and "async def revise_text(self, prompt: str):" in GC and "_pc.priced(" in GC.split("async def revise_text")[1][:2500] and "_tbud.record_tokens(tin + tout)" in GC)

print("[4] the provenance seal + verify: both drafts sealed, re-derivable from the stamp")
from trusted_data_agent.core import provenance as PV
import hashlib
chain = PV.ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="tool_enabled")
if chain is not None:
    t = OL.TurnOutcome(user_uuid="u", session_id="s"); t.revisions = 1; t.cleared = True; t.delivered = "revision"
    t.drafts = [{"which": "first", "text": "cheese", "findings": [{"kind": "unsupported_claim", "spans": ["cheese"]}]}, {"which": "revision_1", "text": "rock", "findings": []}]
    tok = OL.set_turn(t)
    try:
        chain.add_step("user_query", "q", ""); env = chain.finalize()
        turn = {"outcome": OL.stamp_for_turn()}
    finally:
        OL.reset_turn(tok)
    steps = [s for s in env["provenance_chain"] if s.get("step_type") == "outcome_draft"]
    check("two outcome_draft steps sealed (first + revision), summarized", len(steps) == 2 and "first draft" in steps[0]["content_summary"], steps)
    v = PV.verify_content(env, turn)
    check("verify_content re-derives both drafts from the stamp (0 mismatches)", v and not [m for m in (v.get("mismatches") or []) if "outcome_draft" in m] and (v.get("verified", 0) >= 2), v)
    turn2 = json.loads(json.dumps(turn)); turn2["outcome"]["drafts"][0]["text"] = "tampered"
    v2 = PV.verify_content(env, turn2)
    check("a tampered draft is a NAMED mismatch", any("outcome_draft" in m for m in (v2.get("mismatches") or [])), v2)
    check("the sealed marker never reaches the stamp", not any("_sealed" in d for d in turn["outcome"]["drafts"]))
else:
    check("provenance chain class present", False, dir(PV)[:10])

print("[5] the stamp + the assurance mechanism at GROUNDED")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps outcome via setdefault from stamp_for_turn", 'turn_data.setdefault("outcome", _outcome_stamp)' in SM and "_oloop.stamp_for_turn()" in SM)
ES = src("src/trusted_data_agent/agent/execution_service.py")
check("execution_service arms the slot and releases it in finally", "_ol_mod.set_turn(None)" in ES and "_ol_mod.reset_turn(_outcome_token)" in ES)
from trusted_data_agent.agent import assurance_points as AP
pt = next(p for p in AP.REGISTRY if p.key == "outcome_loop")
check("station grounded · alter · control · turn_key outcome · owns nothing · post-door", pt.station == "grounded" and pt.power == "alter" and AP.POINT_CLASS["outcome_loop"] == "control" and pt.turn_key == "outcome" and AP.POINT_DIMENSIONS["outcome_loop"] == () and "outcome_loop" in AP._POST_DOOR_POINTS)
check("partition conflict-free", AP.partition_conflicts() == [])
r = AP._read_outcome_loop({}, "tool_enabled"); check("no stamp → na", r.state == "na")
r = AP._read_outcome_loop({"outcome": {"revisions": 1, "cleared": True, "findings_before": [{"kind": "unsupported_claim"}]}}, "tool_enabled")
check("cleared → pass, acted, names what was removed", r.state == "pass" and r.acted and "unsupported claim removed" in r.detail)
r = AP._read_outcome_loop({"outcome": {"revisions": 1, "cleared": False, "findings_before": [{"kind": "out_of_scope_ref"}], "findings_after": [{"kind": "out_of_scope_ref"}]}}, "tool_enabled")
check("not cleared → warn, acted, 'never a second silent try'", r.state == "warn" and r.acted and "not cleared" in r.detail and "never a second silent try" in r.detail)


print("[5b] review fixes: a judge's pass retires its family; the re-grade is deterministic + unparked; a delivered revision is rendered; the coordinator emits")
J = {**BAD, "decision": "pass", "judge_passed": True, "judge_family": "groundedness", "answer_oos_refs": ["other_db.t"]}
fj = OL.findings_from_event(J)
check("judge PASSED (groundedness) → the unsupported claims it ruled on are NOT revisable; the scope refs still are", [f.kind for f in fj] == ["out_of_scope_ref"], fj)
J2 = {**J, "judge_family": "scope_faithfulness"}
check("judge PASSED (scope) → the scope refs retire, the corpus finding stands", [f.kind for f in OL.findings_from_event(J2)] == ["unsupported_claim"])
J3 = {**GOOD, "judge_passed": True, "judge_family": "scope_faithfulness", "ungrounded_numbers": ["42"]}
check("…a number survives a scope judge's pass only when the gate flagged a numeric concern", OL.findings_from_event(J3) == [] and [f.kind for f in OL.findings_from_event({**J3, "numeric_concern": True})] == ["ungrounded_number"])
check("judge FAILED / abstained → nothing retires", [f.kind for f in OL.findings_from_event({**J, "judge_passed": False})] == ["unsupported_claim", "out_of_scope_ref"] and [f.kind for f in OL.findings_from_event({**J, "judge_passed": None})] == ["unsupported_claim", "out_of_scope_ref"])
# the re-grade tells a regrade-aware gate so: deterministic, never parked
seen = []
async def _drive_regrade():
    tok = OL.set_turn(None)
    try:
        async def grade(text, regrade=False):
            seen.append(regrade); return text, GOOD
        async def revise(p): return "R", 1, 1, 0.0
        return await OL.run_loop(first_text="F_ann", first_event=BAD, raw_first="F", profile={"outcomeConfig": {"enabled": True}}, question="q", grade=grade, revise=revise)
    finally:
        OL.reset_turn(tok)
text, ev = run(_drive_regrade())
check("a regrade-aware gate is called with regrade=True on the revision (a plain gate is called as before — the [2] scenarios)", seen == [True] and text == "R")
GRs = src("src/trusted_data_agent/agent/grounding/runtime.py")
check("run_grounding: skip_judge bypasses the judge escalation; park=False skips the review parking", "if verdict.needs_judge and not skip_judge:" in GRs and 'if verdict.decision.value == "block" and park and (review or _retain_enforced_blocks()):' in GRs and 'event["judge_skipped"] = True' in GRs)
EXs = src("src/trusted_data_agent/agent/executor.py"); COs = src("src/trusted_data_agent/agent/engines/coordinate_engine.py")
check("executor + coordinator grade closures forward regrade → skip_judge / not park", "skip_judge=regrade, park=not regrade" in EXs and "skip_judge=regrade, park=not regrade" in COs)
# (M4) the live event says what will be delivered — a blocked re-grade is not 'cleared'
async def _drive_block():
    tok = OL.set_turn(None); evs = []
    try:
        async def grade(text): return text + "_w", {**GOOD, "decision": "block"}
        async def revise(p): return "R", 1, 1, 0.0
        async def on_event(e): evs.append(e)
        r = await OL.run_loop(first_text="F_ann", first_event=BAD, raw_first="F", profile={"outcomeConfig": {"enabled": True}}, question="q", grade=grade, revise=revise, on_event=on_event)
        return r, evs, OL.stamp_for_turn()
    finally:
        OL.reset_turn(tok)
(text, ev), evs, stamp = run(_drive_block())
check("a revision the gate WITHHELD is not announced as cleared, and the first draft is delivered", text == "F_ann" and evs[0]["details"]["cleared"] is False and "withheld" in evs[0]["details"]["summary"] and stamp["delivered"] == "first")
# (H1) every delivering lane re-renders its HTML from a delivered revision
IDs = src("src/trusted_data_agent/agent/engines/ideate_engine.py"); FOs = src("src/trusted_data_agent/agent/engines/focus_engine.py")
for nm, body in (("optimize", EXs), ("ideate", IDs), ("focus", FOs), ("coordinate", COs)):
    check(f"{nm}: a delivered revision re-renders the answer HTML from the delivered text", 'outcome_delivered") == "revision"' in body)
check("optimize re-renders the REPORT (direct_answer / executive_summary), not a bare paragraph", "final_content.direct_answer = _gated_text" in EXs and "OutputFormatter(**formatter_kwargs).render()" in EXs)
check("the coordinator lane passes on_event (the revision step is emitted on the genie lane too)", "on_event=_genie_outcome_event" in COs and '"outcome_revision")' in COs)
# (L1) the framings a model echoes
check("unwrap: bold / quoted / 'Here is the revised answer' labels are framing too", OL.unwrap_revision("**Revised answer:**\nX") == "X" and OL.unwrap_revision("Here is the revised answer:\n\nX") == "X" and OL.unwrap_revision("Revised draft\nX") == "X" and OL.unwrap_revision("Revised numbers are: 4") == "Revised numbers are: 4")
print("[6] the contract: signed posture, flag + floor, absent == off")
from trusted_data_agent.core import profile_config_contract as PCC
spec = next(s for s in PCC.REGISTRY if s.key == "outcomeConfig")
check("signed posture; enabled a flag; maxRevisions a FLOOR", spec.signed and spec.signed_dimension == "posture" and ("enabled",) in spec.flag_paths and ("maxRevisions",) in spec.floor_paths)
base = {"profile_type": "tool_enabled", "tag": "X"}
check("absent == {enabled:false} (the editor's off save flips no badge); on differs", PCC.signature_for(dict(base)) == PCC.signature_for(dict(base, outcomeConfig={"enabled": False})) and PCC.signature_for(dict(base)) != PCC.signature_for(dict(base, outcomeConfig={"enabled": True, "maxRevisions": 1})))
check("portable", "outcomeConfig" in PCC.portable_keys())

print("[7] the frontend contract")
UI = src("static/js/ui.js"); EH = src("static/js/eventHandlers.js"); NT = src("static/js/notifications.js"); AC = src("static/js/handlers/answerCapsule.js"); IDX = src("templates/index.html"); CH = src("static/js/handlers/configurationHandler.js"); CSS = src("static/css/main.css")
check("ui: outcome_revision card at both live dispatch sites, the reload branch, classified assurance", UI.count("case 'outcome_revision'") == 2 and 'type === "outcome_revision"' in UI and "_renderOutcomeRevisionDetails" in UI and "type === 'outcome_revision'" in [l for l in UI.splitlines() if ") return 'assurance';" in l][0])
check("ui: the card reads pass when cleared, warn when not, names the findings before/after and the cost", "revision ${d.revision || 1} cleared the finding" in UI and "did not clear it" in UI and "Found in the draft" in UI and "After the revision" in UI and "Revision cost" in UI)
check("reload: synthesized from turnData.outcome only when the trace lacks it; titled", "type: 'outcome_revision'" in EH and "s.action.metadata?.type === 'outcome_revision'" in EH and "if (eventType === 'outcome_revision') return 'Outcome Revision';" in EH)
check("live: session-guarded step over the notification channel", "case 'outcome_revision'" in NT and "rp.session_id === state.currentSessionId" in NT and "UI.updateStatusWindow({ step: rp.step || 'Outcome Revision'" in NT)
check("dial: outcome_loop lights GROUNDED", "outcome_loop: 'grounded'" in src("static/js/handlers/assuranceDial.js"))
check("capsule: a Drafts disclosure ONLY when a revision happened, as a tab list with the finding marked", "function draftsHtml(td)" in AC and "if (!o || !Number(o.revisions)" in AC and 'role="tablist"' in AC and 'role="tab"' in AC and 'role="tabpanel"' in AC and "ac-draft-mark" in AC and "ArrowRight" in AC and "wireDrafts(body)" in AC and "${draftsHtml(td)}" in AC)
check("editor: Outcome Loop card under GROUNDED (after Honesty, before GUARDED), load + save (off = {enabled:false} only)", 'id="profile-card-outcome-loop"' in IDX and IDX.index('id="profile-card-outcome-loop"') > IDX.index("Card: Honesty") and IDX.index('id="profile-card-outcome-loop"') < IDX.index("<!-- Station · GUARDED -->") and "profileData.outcomeConfig = oc" in CH and "if (oc.enabled) {" in CH and "o.enabled === true" in CH)
check("drafts CSS on theme tokens", ".ac-draft-mark" in CSS and "var(--trust-warn" in CSS.split(".ac-draft-mark")[1][:160])

print("[8] off ⇒ the gate's result verbatim")
APP_CONFIG.OUTCOME_LOOP = False
async def _off():
    async def grade(t): return t, GOOD
    async def revise(p): raise AssertionError("must not be called")
    return await OL.run_loop(first_text="X_ann", first_event=BAD, raw_first="X", profile={"outcomeConfig": {"enabled": True}}, question="q", grade=grade, revise=revise)
text, ev = run(_off())
check("switch off → no revision, the first draft + its event returned", text == "X_ann" and ev is BAD and OL.stamp_for_turn() is None)

print(f"\n=== S7 outcome loop: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
