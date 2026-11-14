"""S8 — Regenerate as an Experiment: sibling turns + edit branches (CI-pure).

Drives the REAL session_manager seams over an isolated store with the relation armed exactly
as the execution seam arms it, and the pure core directly: parent pointers, adoption on
arrival, Adopt switching, a branch displacing and restoring, the validity rule (ONE mechanism —
isValid — for "not in the model's context"), the reader's payload, and the switch-off byte
identity. Every act is append-only: the record never loses a turn."""
import asyncio, ast, logging, os, pathlib, sys, tempfile, copy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ["TDA_TURN_LINEAGE"] = "1"
from trusted_data_agent.core.config import APP_CONFIG
APP_CONFIG.TURN_LINEAGE = True

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:400]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

from trusted_data_agent.core.session_backends.factory import reset_session_backend
from trusted_data_agent.core.session_backends.file_backend import FileSessionBackend
_tmp = tempfile.mkdtemp(prefix="s8_"); reset_session_backend(FileSessionBackend(_tmp))
from trusted_data_agent.core import session_manager as SM
from trusted_data_agent.agent import turn_lineage as TL
U = "user-1"

def fresh(sid):
    run(SM.save_session(U, sid, {"id": sid, "user_uuid": U, "session_history": [], "chat_object": [], "last_turn_data": {"workflow_history": []}, "license_info": {"provider": "Anthropic"}, "name": "t"}))
def body(sid): return run(SM.read_session(U, sid))
def turn(sid, n, q, a, tag="IDGGT", rel=None):
    """One turn as the execution seam runs it: user message (unless a regenerate), the answer, the record."""
    tok = TL.set_turn(rel)
    try:
        if rel is None or rel.kind != "regenerate":
            run(SM.add_message_to_histories(U, sid, "user", q, profile_tag=tag))
        run(SM.add_message_to_histories(U, sid, "assistant", a, html_content=f"<p>{a}</p>", profile_tag=tag))
        run(SM.update_last_turn_data(U, sid, {"turn": n, "user_query": q, "final_summary_text": a, "status": "success", "profile_tag": tag, "model": "m"}))
    finally:
        TL.reset_turn(tok)
def valid_ctx(sd):
    """What the model's readers see: the valid chat_object roles+turns, in order."""
    return [(m["role"], m["turn_number"]) for m in sd["chat_object"] if m.get("isValid", True) is not False]

print("[1] a regenerate is a sibling: no second user message, its own turn number, adopted on arrival")
S = "s-sib"; fresh(S)
turn(S, 1, "how many customers?", "100 customers")
turn(S, 2, "and products?", "20 products")
rel = TL.build_relation(body(S), {"kind": "regenerate", "of": 1})
check("build_relation resolves the root's query, the next turn number, the root branch", rel.kind == "regenerate" and rel.of == 1 and rel.root == 1 and rel.new_turn == 3 and rel.query == "how many customers?" and rel.branch == 0, rel)
turn(S, 3, "how many customers?", "one hundred customers", rel=rel)
sd = body(S); wf = sd["last_turn_data"]["workflow_history"]
check("three records, append-only; the sibling names its root", [t["turn"] for t in wf] == [1, 2, 3] and wf[2].get("sibling_of") == 1, wf)
check("the sibling is adopted on arrival, the root is not", wf[2].get("adopted") is True and wf[0].get("adopted") is False)
check("the root record is out of context, the sibling in, turn 2 untouched", wf[0]["isValid"] is False and wf[2]["isValid"] is True and wf[1]["isValid"] is True)
sh = sd["session_history"]
check("session_history: exactly ONE user message for turn 1; the sibling answer sits beside it, numbered 3", [ (m["role"], m["turn_number"]) for m in sh] == [("user", 1), ("assistant", 1), ("assistant", 3), ("user", 2), ("assistant", 2)], sh)
check("the sibling message carries sibling_of; the root's user message stays valid; the root's answer is invalid", sh[2].get("sibling_of") == 1 and sh[0]["isValid"] is True and sh[1]["isValid"] is False and sh[2]["isValid"] is True)
check("the model's context: u1 · a3 (the adopted sibling, in place) · u2 · a2 — alternating, no duplicate", valid_ctx(sd) == [("user", 1), ("assistant", 3), ("user", 2), ("assistant", 2)], valid_ctx(sd))
turn(S, 4, "totals?", "sum 120")
check("the next ordinary turn takes the NEXT number after the highest (4), not a collision", body(S)["session_history"][-1]["turn_number"] == 4 and body(S)["last_turn_data"]["workflow_history"][-1]["turn"] == 4)

print("[2] Adopt switches — append-only, only validity moves")
sd = body(S)
moved = TL.apply_adoption(sd, 1); run(SM.save_session(U, S, sd)); sd = body(S); wf = sd["last_turn_data"]["workflow_history"]
check("adopting the root: root adopted + valid, the sibling kept but invalid", moved["adopted"] == 1 and wf[0]["adopted"] is True and wf[0]["isValid"] is True and wf[2]["adopted"] is False and wf[2]["isValid"] is False)
check("context follows: u1 · a1 · u2 · a2 · u4 · a4", valid_ctx(sd) == [("user", 1), ("assistant", 1), ("user", 2), ("assistant", 2), ("user", 4), ("assistant", 4)], valid_ctx(sd))
check("the record still holds every sibling", [t["turn"] for t in wf] == [1, 2, 3, 4])
try:
    TL.apply_adoption(sd, 2); check("adopting a turn with no siblings is refused", False)
except ValueError: check("adopting a turn with no siblings is refused", True)

print("[3] a regenerate of a sibling names the ROOT; a third sibling; the set in record order")
rel2 = TL.build_relation(body(S), {"kind": "regenerate", "of": 3})
check("regenerating the sibling resolves to the root", rel2.root == 1 and rel2.new_turn == 5 and rel2.query == "how many customers?")
turn(S, 5, "how many customers?", "a hundred", rel=rel2)
sd = body(S)
check("sibling set = [1, 3, 5], the newest adopted", TL.sibling_set(sd, 1) == [1, 3, 5] and TL.effective_adopted(sd, 1) == 5)
check("context: u1 · a5 · u2 · a2 · u4 · a4", valid_ctx(sd) == [("user", 1), ("assistant", 5), ("user", 2), ("assistant", 2), ("user", 4), ("assistant", 4)], valid_ctx(sd))
pl = TL.lineage_payload(sd)
check("the reader's payload names the set, the adopted one, each sibling's agent", pl["siblings"]["1"]["adopted"] == 5 and [t["turn"] for t in pl["siblings"]["1"]["turns"]] == [1, 3, 5] and pl["siblings"]["1"]["turns"][2]["profile_tag"] == "IDGGT" and pl["branches"]["active"] == 0, pl)

print("[4] an edit is a branch: the head displaces the turns from the edited one on, the record keeps them")
B = "s-br"; fresh(B)
turn(B, 1, "q1", "a1"); turn(B, 2, "q2", "a2"); turn(B, 3, "q3", "a3")
relE = TL.build_relation(body(B), {"kind": "edit", "of": 2, "prompt": "q2 edited"})
check("build_relation for an edit: head = its own turn, the new text", relE.kind == "edit" and relE.of == 2 and relE.new_turn == 4 and relE.branch == 4 and relE.query == "q2 edited")
turn(B, 4, "q2 edited", "a4", rel=relE)
sd = body(B); wf = sd["last_turn_data"]["workflow_history"]
check("branches: head 4 of 2 on the root, active", (sd.get("branches") or {}).get("active") == 4 and (sd.get("branches") or {}).get("heads") == [{"turn": 4, "of": 2, "parent": 0}], sd.get("branches"))
check("turns 2 and 3 displaced (kept, invalid); 1 and 4 valid; 4 records", [t["isValid"] for t in wf] == [True, False, False, True] and len(wf) == 4)
check("the head record carries branch_of + branch; an ordinary turn on the branch is stamped with it", wf[3].get("branch_of") == 2 and wf[3].get("branch") == 4)
check("the edited user message + its answer are out of the view; the new question in", [m.get("isValid") for m in sd["session_history"]] == [True, True, False, False, False, False, True, True])
check("context: u1 · a1 · u4 · a4", valid_ctx(sd) == [("user", 1), ("assistant", 1), ("user", 4), ("assistant", 4)], valid_ctx(sd))
turn(B, 5, "q5", "a5"); sd = body(B)
check("a following ordinary turn belongs to the active branch", sd["last_turn_data"]["workflow_history"][4].get("branch") == 4 and TL.current_view(sd) == {1, 4, 5})

print("[4b] a regenerate of a question BEFORE the branch point stays visible on the branch (answers travel with their question)")
relS = TL.build_relation(body(B), {"kind": "regenerate", "of": 1})
turn(B, 6, "q1", "a1 again", rel=relS); sd = body(B)
check("sibling 6 of 1 is on the root branch (its question's) and in branch 4's view", sd["last_turn_data"]["workflow_history"][5].get("branch") == 0 and TL.current_view(sd) == {1, 4, 5, 6} and TL.effective_adopted(sd, 1) == 6)
check("context on the branch: u1 · a6 · u4 · a4 · u5 · a5", valid_ctx(sd) == [("user", 1), ("assistant", 6), ("user", 4), ("assistant", 4), ("user", 5), ("assistant", 5)], valid_ctx(sd))
TL.apply_adoption(sd, 1); run(SM.save_session(U, B, sd))

print("[5] switching branches restores exactly what it displaces")
sd = body(B); moved = TL.switch_branch(sd, 0); run(SM.save_session(U, B, sd)); sd = body(B)
check("back to the root: 2, 3 restored; 4, 5 displaced; the sibling of 1 stays (its question is on both)", moved["restored"] == [2, 3] and moved["displaced"] == [4, 5] and TL.current_view(sd) == {1, 2, 3, 6})
check("context is the original conversation again", valid_ctx(sd) == [("user", 1), ("assistant", 1), ("user", 2), ("assistant", 2), ("user", 3), ("assistant", 3)], valid_ctx(sd))
moved = TL.switch_branch(sd, 4); run(SM.save_session(U, B, sd)); sd = body(B)
check("and forward again", moved["restored"] == [4, 5] and TL.current_view(sd) == {1, 4, 5, 6})
try:
    TL.switch_branch(sd, 9); check("an unknown branch is refused", False)
except ValueError: check("an unknown branch is refused", True)
check("the payload carries the heads + the view", TL.lineage_payload(sd)["branches"] == {"active": 4, "heads": [{"turn": 4, "of": 2, "parent": 0}], "view": [1, 4, 5, 6]})

print("[6] a purge on an untouched turn survives a switch (only membership changes are touched)")
sd = body(B); run(SM.toggle_turn_validity(U, B, 1)); sd = body(B)
check("turn 1 purged by the user", sd["last_turn_data"]["workflow_history"][0]["isValid"] is False and sd["session_history"][0]["isValid"] is False)
TL.switch_branch(sd, 0); run(SM.save_session(U, B, sd)); sd = body(B)
check("after the switch turn 1 is still purged (it never changed membership)", sd["last_turn_data"]["workflow_history"][0]["isValid"] is False)
run(SM.toggle_turn_validity(U, B, 1))

print("[7] nested: an edit on a branch, and a regenerate inside a branch that a switch takes out of view")
sd = body(B); TL.switch_branch(sd, 4); run(SM.save_session(U, B, sd))
relN = TL.build_relation(body(B), {"kind": "edit", "of": 5, "prompt": "q5 edited"})
turn(B, 7, "q5 edited", "a7", rel=relN); sd = body(B)
check("nested head 7 of 5 with parent branch 4; view = 1, 4, 6, 7", sd["branches"]["heads"][-1] == {"turn": 7, "of": 5, "parent": 4} and TL.current_view(sd) == {1, 4, 6, 7})
relR = TL.build_relation(sd, {"kind": "regenerate", "of": 4})
turn(B, 8, "q2 edited", "a8", rel=relR); sd = body(B)
check("a regenerate inside the branch: sibling 8 of 4, adopted, on branch 7's view — stamped with its ROOT's branch (4), not the active head", sd["last_turn_data"]["workflow_history"][7].get("sibling_of") == 4 and sd["last_turn_data"]["workflow_history"][7].get("branch") == 4 and TL.effective_adopted(sd, 4) == 8 and 8 in TL.current_view(sd))
TL.switch_branch(sd, 4); run(SM.save_session(U, B, sd)); sd = body(B)
check("answers travel with their question: on branch 4 the adopted sibling 8 still stands for turn 4", TL.effective_adopted(sd, 4) == 8 and sd["last_turn_data"]["workflow_history"][7]["isValid"] is True and sd["last_turn_data"]["workflow_history"][3]["isValid"] is False and 8 in TL.current_view(sd))
# the fallback for a record that predates the rule (a sibling stamped with a branch its root is not on)
sd_leg = copy.deepcopy(sd); sd_leg["last_turn_data"]["workflow_history"][7]["branch"] = 7
check("legacy: a sibling stamped off its root's branch is out of view there and the root stands in — never a question with no answer", TL.effective_adopted(sd_leg, 4) == 4 and 8 not in TL.current_view(sd_leg))
# …and a SWITCH on such a record re-evaluates the root even though only the sibling changed
# membership (a question must never be left with no answer standing for it)
sd_leg2 = copy.deepcopy(sd); sd_leg2["last_turn_data"]["workflow_history"][7]["branch"] = 7
TL.switch_branch(sd_leg2, 7); TL.apply_adoption(sd_leg2, 8)     # on branch 7 the sibling stands, the root is out
r4 = sd_leg2["last_turn_data"]["workflow_history"][3]
check("legacy switch: the sibling adopted while it is in view, the root record invalid", r4["isValid"] is False and TL.effective_adopted(sd_leg2, 4) == 8)
TL.switch_branch(sd_leg2, 4)
check("legacy switch back: the root record is re-validated although its own membership never changed", r4["isValid"] is True and TL.effective_adopted(sd_leg2, 4) == 4)
try:
    TL.build_relation(sd, {"kind": "edit", "of": 7, "prompt": "x"}); check("editing a turn off the active branch is refused", False)
except ValueError as e: check("editing a turn off the active branch is refused", "not on the active branch" in str(e))
try:
    TL.build_relation(sd, {"kind": "edit", "of": 8, "prompt": "x"}); check("editing a regenerate is refused (edit the question's turn)", False)
except ValueError as e: check("editing a regenerate is refused (edit the question's turn)", "regenerate" in str(e))

print("[8] build_relation refuses what cannot be regenerated")
sd = body(S)
for req, why in (({"kind": "bogus", "of": 1}, "kind"), ({"kind": "regenerate", "of": "1"}, "turn number"), ({"kind": "regenerate", "of": 99}, "does not exist"), ({"kind": "edit", "of": 1, "prompt": " "}, "prompt")):
    try:
        TL.build_relation(sd, req); check(f"refused: {why}", False)
    except ValueError as e: check(f"refused: {why}", why in str(e), str(e))
sd2 = copy.deepcopy(sd); sd2["last_turn_data"]["workflow_history"][1]["is_session_primer"] = True
try:
    TL.build_relation(sd2, {"kind": "regenerate", "of": 2}); check("a session primer is never regenerated", False)
except ValueError as e: check("a session primer is never regenerated", "primer" in str(e))
sd3 = copy.deepcopy(sd); sd3["last_turn_data"]["workflow_history"][0]["user_query"] = ""
try:
    TL.build_relation(sd3, {"kind": "regenerate", "of": 1}); check("a turn with no question cannot be regenerated", False)
except ValueError as e: check("a turn with no question cannot be regenerated", "no question" in str(e))

print("[9] a regenerate carries the root's attachments / extensions / skills")
C = "s-carry"; fresh(C)
tok = TL.set_turn(None)
run(SM.add_message_to_histories(U, C, "user", "q", profile_tag="IDGGT", attachments=[{"file_id": "f1", "filename": "a.pdf", "extracted_text": "secret"}], extension_specs=[{"name": "json", "param": None}], skill_specs=[{"name": "sql", "param": None}]))
run(SM.add_message_to_histories(U, C, "assistant", "a")); run(SM.update_last_turn_data(U, C, {"turn": 1, "user_query": "q", "final_summary_text": "a", "status": "success"}))
TL.reset_turn(tok)
relC = TL.build_relation(body(C), {"kind": "regenerate", "of": 1})
check("carried = the root user message's attachments (without extracted text), extensions, skills", relC.carried.get("attachments") == [{"file_id": "f1", "filename": "a.pdf"}] and relC.carried.get("extension_specs") == [{"name": "json", "param": None}] and relC.carried.get("skill_specs") == [{"name": "sql", "param": None}], relC.carried)

print("[10] the seams: execution_service arms + releases the relation; the persistence chokepoint stamps; the stream + REST carry it")
ES = src("src/trusted_data_agent/agent/execution_service.py")
seam = ES[ES.index("async def run_agent_execution("):]
tree = ast.parse(ES)
fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_execution")
params = [a.arg for a in fn.args.args]
check("run_agent_execution takes turn_relation", "turn_relation" in params)
check("the relation is built from the session at the seam and armed; a regenerate saves NO user message and runs the root's query", "_tl_mod.build_relation(session_data, turn_relation)" in seam and "message_to_save = None" in seam and "user_input = _rel.query" in seam and "_lineage_token = _tl_mod.set_turn(_rel)" in seam)
fin = seam[seam.rindex("    finally:"):]
check("released in the seam's finally", "_tl_mod.reset_turn(_lineage_token)" in fin)
check("the payload carries the relation for the caller", 'final_result_payload["lineage"] = _rel_now.to_dict()' in seam)
# scoring both: the eval hook is dispatched for a sibling exactly as for any turn — no `if` on
# the way to the dispatch mentions the relation (a sibling that is not scored cannot be compared)
_gated = False
for node in ast.walk(fn):
    if isinstance(node, ast.If) and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_dispatch_online_eval" for c in ast.walk(node)):
        if any(tok in ast.unparse(node.test) for tok in ("lineage", "turn_relation", "_rel")):
            _gated = True
check("the eval dispatch is never gated on the turn's relation — a sibling is scored like any turn", not _gated)
SMS = src("src/trusted_data_agent/core/session_manager.py")
upd = SMS[SMS.index("async def update_last_turn_data("):]
check("the persistence chokepoint stamps BEFORE the append and adopts AFTER it", upd.index("_tl.stamp_record(session_data, _lineage_rel, turn_data)") < upd.index('["workflow_history"].append(turn_data)') < upd.index("_tl.after_record(session_data, _lineage_rel, turn_data)"))
addm = SMS[SMS.index("async def add_message_to_histories("):SMS.index("async def update_session_name(")]
check("the message seam registers an edit's head BEFORE the message lands, and inserts a sibling beside its set in BOTH histories", "_tl.register_head(session_data, _rel.new_turn, _rel.of)" in addm and addm.count("_tl.insert_index(") == 2 and addm.count("_tl.stamp_message(") == 2)
RT = src("src/trusted_data_agent/api/routes.py"); RR = src("src/trusted_data_agent/api/rest_routes.py")
check("/ask_stream maps regenerate_of / edit_of to the relation and passes it", '"regenerate_of"' in RT and '"edit_of"' in RT and "turn_relation=turn_relation" in RT)
for ep in ('/turns/<int:turn>/regenerate"', '/turns/<int:turn>/edit"', '/turns/<int:turn>/adopt"', '/branches/<int:head>/activate"', '/lineage"'):
    check(f"REST endpoint {ep} exists behind the gate", ep in RR)
check("the public /query never reads a client-supplied relation", 'data.pop("turn_relation", None)' in RR)
check("the REST completion notice carries the relation", '"lineage": final_result_payload.get("lineage")' in RR)
check("the session GET serves the lineage only when something regenerated or branched", "_tl.lineage_payload(session_data)" in RT and 'response_data["lineage"] = _lin' in RT)


print("[10b] review fix: the UI's 'Same agent' regenerate runs under the ROOT turn's agent, as the REST path does")
_ask = RT[RT.index("async def ask_stream("):]
_reg = _ask[_ask.index("session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)"):]
check("/ask_stream resolves a regenerate's profile from the root turn's tag when the client sent none, BEFORE the override is applied",
      'turn_relation.get("kind") == "regenerate" and not profile_override_id' in _reg and '_tl.find_turn(session_data, turn_relation["of"])' in _reg and 'profile_override_id = _rp.get("id")' in _reg
      and _reg.index('profile_override_id = _rp.get("id")') < _reg.index("if profile_override_id:"))
check("…the same rule the REST regenerate applies (one resolution, two doors)", 'root_rec.get("profile_tag")' in RR and 'if not pid and kind == "regenerate":' in RR)
print("[11] off ⇒ byte-identical: no stamps, no payload, a stamp call is a no-op")
APP_CONFIG.TURN_LINEAGE = False
O = "s-off"; fresh(O)
turn(O, 1, "q", "a"); turn(O, 2, "q2", "a2")
sd = body(O)
check("no lineage keys anywhere, no branches, payload None", all(k not in t for t in sd["last_turn_data"]["workflow_history"] for k in ("sibling_of", "branch", "adopted", "branch_of")) and "branches" not in sd and TL.lineage_payload(sd) is None)
rec = {"turn": 3}; TL.stamp_record(sd, TL.TurnRelation("regenerate", 1, 1, 3, "q"), rec)
check("stamp_record is a no-op with the switch off", rec == {"turn": 3})
APP_CONFIG.TURN_LINEAGE = True

import shutil; shutil.rmtree(_tmp, ignore_errors=True); reset_session_backend(None)
print(f"\n=== S8 turn siblings: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
