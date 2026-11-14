"""
Needs input (PROP-011, phase 1) — the state, the store, the ledger, the guarantees, re-entry.

Pure where it can be (no server, no network, no LLM): the state split, the record store on a
temporary database, the turn-scoped ledger, the validators, the resolvers raising a question,
the resume binding consumed once, the REST outcome rule, the downgrade guard; and source-text
gates on the seams that must hold — the three chokepoints, the three gate abstentions, the plan
loop ending at a question, the readers that no longer hold their own list of terminal strings.

Run: python test/test_input_requests.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "needs-input-test")

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
DB = _tmp.name

from trusted_data_agent.core import turn_states as TS  # noqa: E402
from trusted_data_agent.core import input_request_store as STORE  # noqa: E402
from trusted_data_agent.agent import input_requests as IR  # noqa: E402
from trusted_data_agent.core import input_resume as RESUME  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

STORE._db_path = lambda db_path=None: DB   # every store call in this process hits the temp DB

# ─────────────────────────── [1] the state split ───────────────────────────
print("\n[1] the terminal / interrupted split")
check("input_required is interrupted, settled, not terminal, not a failure",
      TS.is_interrupted("input_required") and TS.is_settled("input_required")
      and not TS.is_terminal("input_required") and not TS.is_failure("input_required"))
check("auth_required is interrupted too", TS.is_interrupted("auth_required"))
check("complete / success are terminal and success", TS.is_terminal("complete") and TS.is_success("complete") and TS.is_success("success"))
check("failed / error are failures; cancelled is terminal but not a failure",
      TS.is_failure("failed") and TS.is_failure("error") and TS.is_terminal("cancelled") and not TS.is_failure("cancelled"))
check("pending / processing are active, not settled", TS.is_active("processing") and not TS.is_settled("pending"))
check("case and whitespace do not matter", TS.is_settled(" Complete ") and TS.is_interrupted("INPUT_REQUIRED"))

# ─────────────────────────── [2] the store ───────────────────────────
print("\n[2] the store: a record, a binding consumed once, expiry, supersession")
items = [{"header": "Graph", "question": "Which knowledge graph do you mean by 'Sales'?",
          "options": [{"value": "kg-1", "label": "Sales"}, {"value": "kg-2", "label": "Sales"}],
          "multi_select": False, "other_allowed": False}]
rec = STORE.create(user_uuid="u-1", kind="choose", items=items, why="two match", session_id="s-1", turn=3,
                   task_id="t-1", origin="interactive", raised_by="code", profile_id="p-1", profile_tag="SALES",
                   source="graphs.get.kg_id", expires_in_hours=24, db_path=DB)
check("a record is created pending with an id, a digest and a resume binding",
      rec and rec["id"].startswith("ir-") and rec["status"] == "pending" and rec["digest"] and rec.get("resume"))
check("the owner's read carries a fresh binding; another user's read is None",
      (STORE.get_for_owner(rec["id"], "u-1", db_path=DB) or {}).get("resume") and STORE.get(rec["id"], "u-2", db_path=DB) is None)
check("the binding verifies for its person and request", STORE.verify_resume(rec["resume"], rec, "u-1") is None)
check("…and is refused for another person", "another person" in (STORE.verify_resume(rec["resume"], rec, "u-2") or ""))
other = STORE.create(user_uuid="u-1", kind="confirm", items=[{"header": "Drop", "question": "Drop the staging table?",
                                                              "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]}],
                     why=None, session_id="s-1", turn=4, task_id=None, origin="rest", raised_by="model", profile_id="p-1",
                     profile_tag="SALES", source="TDA_AskUser", db_path=DB)
check("…and for another request", "another question" in (STORE.verify_resume(other["resume"], rec, "u-1") or ""))
check("…and without one", STORE.verify_resume(None, rec, "u-1") is not None)
check("pending_for_session finds the newest pending question", (STORE.pending_for_session("s-1", "u-1", db_path=DB) or {}).get("id") == other["id"])
check("pending_count counts this person's pending questions", STORE.pending_count("u-1", db_path=DB) == 2)
got, err = STORE.consume(rec["id"], user_uuid="u-1", decision="accept", answer={"Graph": "kg-2"}, answered_via="chat", db_path=DB)
check("consume flips pending → accepted once, recording who, how and what",
      err is None and got["status"] == "accepted" and got["answer"] == {"Graph": "kg-2"} and got["answered_via"] == "chat" and got["answered_by"] == "u-1")
got2, err2 = STORE.consume(rec["id"], user_uuid="u-1", decision="accept", answer={"Graph": "kg-1"}, answered_via="rest", db_path=DB)
check("a second answer is refused — single use", got2 is None and "already accepted" in err2)
got3, err3 = STORE.consume(other["id"], user_uuid="u-2", decision="accept", answer={"Drop": "yes"}, answered_via="rest", db_path=DB)
check("another person cannot answer", got3 is None and "not yours" in err3)
check("a bad decision is refused", STORE.consume(other["id"], user_uuid="u-1", decision="maybe", answer=None, answered_via="rest", db_path=DB)[1])
expired = STORE.create(user_uuid="u-1", kind="provide", items=[{"header": "FY", "question": "When does the fiscal year start?", "schema": {"type": "string"}}],
                       why=None, session_id="s-2", turn=1, task_id=None, origin="scheduler", raised_by="code", profile_id="p-1",
                       profile_tag="SALES", source="x", expires_in_hours=-1, db_path=DB)
check("an expired pending question flips to expired on read", (STORE.get(expired["id"], "u-1", db_path=DB) or {}).get("status") == "expired")
check("…and cannot be answered", "expired" in (STORE.consume(expired["id"], user_uuid="u-1", decision="accept", answer={"FY": "April"}, answered_via="rest", db_path=DB)[1] or "already"))
sup = STORE.supersede_pending_for_session("s-1", "u-1", by="next turn", db_path=DB)
check("a new turn supersedes the session's pending questions, and says by what",
      sup == [other["id"]] and (STORE.get(other["id"], "u-1", db_path=DB) or {}).get("superseded_by") == "next turn")
check("the public view never carries the digest, and the binding only when asked",
      "digest" not in STORE.public_view(rec) and "resume" not in STORE.public_view(rec) and "resume" in STORE.public_view(rec, with_resume=True))
check("the list reads the person's questions newest first, by status",
      [r["id"] for r in STORE.list_for_user("u-1", status="all", db_path=DB)][0] == expired["id"]
      and all(r["status"] == "accepted" for r in STORE.list_for_user("u-1", status="accepted", db_path=DB)))

# ─────────────────────────── [3] the ledger ───────────────────────────
print("\n[3] the ledger: policy, raising, the guarantees")
check("the default policy asks, parks, enforces, one per turn — and says the component is in use", IR.policy_for({}) == {**IR.DEFAULT_POLICY, "enabled": True})
pol = IR.policy_for({"authorityConfig": {"inputPolicy": {"interactive": "never", "raisers": "observe", "maxPerTurn": 3}}})
check("the agent's input policy is read from authorityConfig", pol["interactive"] == "never" and pol["raisers"] == "observe" and pol["maxPerTurn"] == 3)
check("an unknown value falls to the default; maxPerTurn is bounded",
      IR.policy_for({"authorityConfig": {"inputPolicy": {"raisers": "loud", "maxPerTurn": 99}}})["raisers"] == "enforce"
      and IR.policy_for({"authorityConfig": {"inputPolicy": {"maxPerTurn": 99}}})["maxPerTurn"] == 8)
_floor = getattr(APP_CONFIG, "INPUT_RAISERS_FLOOR", "enforce")
APP_CONFIG.INPUT_RAISERS_FLOOR = "observe"
check("the deployment floor holds every agent at observe", IR.policy_for({"authorityConfig": {"inputPolicy": {"raisers": "enforce"}}})["raisers"] == "observe")
APP_CONFIG.INPUT_RAISERS_FLOOR = _floor

check("outside a turn nothing is pending, nothing is refused, the text is unchanged",
      IR.pending() is None and IR.refusal_for("base_readQuery") is None and IR.ensure_question_in("x") == "x" and IR.turn_status() == "success" and IR.turn_stamp() == {})
check("outside a turn an ambiguity raises nothing", IR.ambiguity("repository", "Sales", [{"id": "1", "name": "Sales"}, {"id": "2", "name": "Sales"}]) is None)

tok = IR.begin_turn(user_uuid="u-3", session_id="s-3", origin="interactive", profile={"id": "p-3", "tag": "ENF"}, task_id=None)
IR.set_turn(7)
r = IR.ambiguity("repository", "Sales", [{"id": "c-1", "name": "Sales", "detail": "owned"}, {"id": "c-2", "name": "Sales"}], source="repos.get.collection_id")
check("enforce: an ambiguity raises a choose question, pending in the ledger and persisted",
      r and IR.pending() is r and r["kind"] == "choose" and r["turn"] == 7 and r["raised_by"] == "code"
      and r["source"] == "repos.get.collection_id" and [o["value"] for o in r["items"][0]["options"]] == ["c-1", "c-2"]
      and (STORE.get(r["id"], "u-3", db_path=DB) or {}).get("status") == "pending")
check("the question names the kind in the person's words", "repository" in r["items"][0]["question"] and "repositories" in (r["why"] or ""))
blk = IR.refusal_for("base_readQuery")
check("nothing runs past a question: a recovery-shaped refusal naming the question",
      blk and blk["status"] == "error" and blk["error"] == "input_required" and "Which repository" in blk["error_message"]
      and blk["metadata"]["terminal"] and blk["input_request"] == r["id"])
check("the turn's own reporting tools are exempt", all(IR.refusal_for(t) is None for t in ("TDA_FinalReport", "TDA_ComplexPromptReport", "TDA_ContextReport", "TDA_LLMTask")))
check("a component tool is refused too", IR.refusal_for("TDA_Platform") is not None)
txt = IR.ensure_question_in("I found two.")
check("the question reaches the person verbatim: appended when absent, with its options",
      txt.startswith("I found two.") and "Which repository do you mean by 'Sales'?" in txt and "Options:" in txt)
check("…and not twice", IR.ensure_question_in(txt) == txt)
check("the turn reads input_required and its stamp carries the question (as the faces read it)",
      IR.turn_status() == "input_required" and IR.turn_stamp()["input_request_id"] == r["id"]
      and "digest" not in IR.turn_stamp()["input_request"])
r2, why2 = IR.raise_request("confirm", [{"header": "Drop", "question": "Drop it?", "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]}], None, raised_by="model")
check("a second question in the turn is refused with the pending one named", r2 is None and "already pending" in why2 and "Sales" in why2)
check("a second ambiguity is recorded, never a second question",
      IR.ambiguity("agent", "sales", [{"id": "a", "name": "@SALES"}, {"id": "b", "name": "@SALES"}]) is None and IR.observed()[0]["kind"] == "agent")
payload = {"final_answer": "<p>I found two.</p>", "final_answer_text": "I found two.", "turn_id": 7}
view = IR.finish_payload(payload)
check("finish_payload puts the question (with the binding) on the result and appends it to text and HTML",
      view and view.get("resume") and payload["input_required"] is True and payload["input_request"]["id"] == r["id"]
      and "Which repository" in payload["final_answer_text"] and "input-request-static" in payload["final_answer"])
IR.end_turn(tok)
check("end_turn clears the ledger", IR.pending() is None and not IR.in_turn())

tok = IR.begin_turn(user_uuid="u-4", session_id="s-4", origin="interactive", profile={"id": "p-4", "tag": "OBS", "authorityConfig": {"inputPolicy": {"raisers": "observe"}}})
r = IR.ambiguity("repository", "Sales", [{"id": "c-1", "name": "Sales"}, {"id": "c-2", "name": "Sales"}], source="repos.get.collection_id")
check("observe: the ambiguity is recorded, nothing is pending, the turn completes",
      r is None and IR.pending() is None and IR.observed()[0]["candidates"] == ["c-1", "c-2"] and IR.turn_status() == "success"
      and IR.turn_stamp()["ambiguities_observed"][0]["code"] == "ambiguous_name")
IR.end_turn(tok)

tok = IR.begin_turn(user_uuid="u-5", session_id="s-5", origin="interactive", profile={"id": "p-5", "tag": "NEV", "authorityConfig": {"inputPolicy": {"interactive": "never"}}})
r, why = IR.raise_request("choose", items, "two", raised_by="code", source="x")
closed = STORE.list_for_user("u-5", status="declined", db_path=DB)
check("never: the question is recorded and closed by policy at once; the turn ends complete with an honest sentence",
      r is None and "never to ask" in why and IR.pending() is None and IR.turn_status() == "success"
      and closed and closed[0]["answered_by"] == "policy(never)" and IR.turn_stamp()["input_requests_closed"] == [closed[0]["id"]])
IR.end_turn(tok)

tok = IR.begin_turn(user_uuid="u-6", session_id="s-6", origin="scheduler", profile={"id": "p-6", "tag": "SCH", "authorityConfig": {"inputPolicy": {"interactive": "never"}}})
r, why = IR.raise_request("choose", items, "two", raised_by="code", source="x")
check("an unattended run is not closed by the interactive policy: it parks the question", r is not None and IR.unattended())
IR.end_turn(tok)

# ─────────────────────────── [4] validation ───────────────────────────
print("\n[4] the validators: one act, one shape")
ok, err = IR.validate_ask({"kind": "choose", "question": "Which?", "options": ["a", "b", "c"], "why": "it matters"})
check("a choose with 2–8 unique options normalises", ok and ok["kind"] == "choose" and len(ok["items"][0]["options"]) == 3 and ok["why"] == "it matters")
check("one option is refused", IR.validate_ask({"kind": "choose", "question": "Which?", "options": ["a"]})[1])
check("duplicate option values are refused", IR.validate_ask({"kind": "choose", "question": "Which?", "options": ["a", "a"]})[1])
check("nine options are refused", IR.validate_ask({"kind": "choose", "question": "Which?", "options": list("abcdefghi")})[1])
check("five items are refused", IR.validate_ask({"kind": "confirm", "items": [{"question": "q"}] * 5})[1])
check("a question over 300 characters is refused", IR.validate_ask({"kind": "confirm", "question": "x" * 301})[1])
check("an unknown kind is refused", IR.validate_ask({"kind": "approve", "question": "q"})[1])
check("a confirm gets Yes/No", [o["value"] for o in IR.validate_ask({"kind": "confirm", "question": "Drop?"})[0]["items"][0]["options"]] == ["yes", "no"])
check("a provide with a flat primitive schema passes", IR.validate_ask({"kind": "provide", "question": "FY start?", "schema": {"type": "string", "format": "date"}})[0])
check("a provide may never ask for a credential",
      "credential" in (IR.validate_ask({"kind": "provide", "question": "Key?", "schema": {"type": "object", "properties": {"api_key": {"type": "string"}}}})[1] or "")
      and "credential" in (IR.validate_ask({"kind": "provide", "question": "Pw?", "schema": {"type": "object", "properties": {"password": {"type": "string"}}}})[1] or ""))
check("a nested object is refused", IR.validate_ask({"kind": "provide", "question": "q", "schema": {"type": "object", "properties": {"x": {"type": "object"}}}})[1])
check("the header is bounded to 12 characters", len(IR.validate_ask({"kind": "confirm", "question": "q", "header": "a very long header"})[0]["items"][0]["header"]) == 12)

q = {"kind": "choose", "items": [{"header": "Graph", "question": "Which?", "options": [{"value": "kg-1", "label": "Sales (north)"}, {"value": "kg-2", "label": "Sales (south)"}], "multi_select": False, "other_allowed": False}]}
check("an answer by value binds", IR.validate_answer(q, {"Graph": "kg-2"})[0] == {"Graph": "kg-2"})
check("an answer by label binds to the value", IR.validate_answer(q, "sales (south)")[0] == {"Graph": "kg-2"})
check("something else is refused where Other is not allowed", IR.validate_answer(q, "kg-9")[1])
q_other = {"kind": "choose", "items": [dict(q["items"][0], other_allowed=True)]}
check("…and kept where it is", IR.validate_answer(q_other, "none of these")[0] == {"Graph": "none of these"})
check("two picks on a single-select are refused", IR.validate_answer(q, {"Graph": ["kg-1", "kg-2"]})[1])
q_multi = {"kind": "choose", "items": [dict(q["items"][0], multi_select=True)]}
check("…and kept on a multi-select", IR.validate_answer(q_multi, {"Graph": ["kg-1", "kg-2"]})[0] == {"Graph": ["kg-1", "kg-2"]})
check("an unanswered item is named", "no answer" in (IR.validate_answer(q, {})[1] or ""))

# ─────────────────────────── [5] re-entry ───────────────────────────
print("\n[5] re-entry: the block the model reads, the value code binds")
rec = {"kind": "choose", "items": q["items"], "source": "graphs.get.kg_id", "raised_by": "code"}
block, shown = IR.reentry_block(rec, "accept", {"Graph": "kg-2"}, "show me the Sales graph")
check("the block carries the request, the question and the answer, and forbids re-asking",
      "Original request: show me the Sales graph" in block and "Which?" in block and "kg-2" in block and "Do not ask it again" in block)
check("the person sees the option's label as their line", shown == "Sales (south)")
check("a decline is a block that says not to proceed", "declined" in IR.reentry_block(rec, "decline", None, "q")[0])
tok = IR.begin_turn(user_uuid="u-7", session_id="s-7", origin="interactive", profile={}, answer={"request": rec, "decision": "accept", "values": {"Graph": "kg-2"}})
check("bound_answer by source returns the chosen value", IR.bound_answer("graphs.get.kg_id") == "kg-2")
rec_code = {"kind": "choose", "raised_by": "code", "source": "repos.get.collection_id",
            "items": [{"header": "Repository", "question": "Which repository do you mean by 'Sales'?", "options": [{"value": "c-1", "label": "Sales"}, {"value": "c-2", "label": "Sales"}]}]}
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-7", session_id="s-7", origin="interactive", profile={}, answer={"request": rec_code, "decision": "accept", "values": {"Repository": "c-2"}})
from trusted_data_agent.core import platform_operations as P  # noqa: E402
dup = [{"id": "c-1", "name": "Sales"}, {"id": "c-2", "name": "Sales"}, {"id": "c-3", "name": "Other"}]
check("on the re-entry turn the resolver receives the chosen id in code", P._resolve_owned("repository", dup, "Sales") == ("c-2", None))
check("a declined answer binds nothing", (IR.end_turn(tok), IR.begin_turn(user_uuid="u-7", session_id="s-7", origin="interactive", profile={}, answer={"request": rec_code, "decision": "decline", "values": None}), IR.bound_answer("repos.get.collection_id"))[2] is None)
IR._LEDGER.set(None)   # the inline begin_turn above left a ledger armed: clear it outright

# ─────────────────────────── [6] the resolvers ───────────────────────────
print("\n[6] the resolvers: ambiguity is a question, never absence")
check("a unique name resolves as before", P._resolve_owned("repository", dup, "Other") == ("c-3", None))
check("an id resolves as before", P._resolve_owned("repository", dup, "c-1") == ("c-1", None))
check("an unknown name is still absence", "no repository of yours" in P._resolve_owned("repository", dup, "Nope")[1])
try:
    P._resolve_owned("repository", dup, "sales")
    check("two names raise Ambiguous", False)
except IR.Ambiguous as exc:
    check("two names raise Ambiguous with the candidates", exc.kind == "repository" and [c["id"] for c in exc.candidates] == ["c-1", "c-2"])
from trusted_data_agent.api.operations import Arg  # noqa: E402


class _Op:
    op = "repos.get"
    path = "/api/v1/rag/collections/<collection_id>"
    path_params = {"collection_id": Arg("string", "the repository", required=True)}
    query = {}
    body = {}


_orig = P.RESOLVERS["collection_id"]
P.RESOLVERS["collection_id"] = lambda v, u: P._resolve_owned("repository", dup, v)
try:
    placed, err = P.bind(_Op(), {"collection_id": "Sales"}, "u-8", resolve=True)
    check("outside a turn bind says how many match and names them by id — never 'no such repository'",
          placed is None and "2 of your repositories are named 'Sales'" in err and "c-1" in err and "no repository" not in err)
    tok = IR.begin_turn(user_uuid="u-8", session_id="s-8", origin="interactive", profile={"id": "p-8", "tag": "BIND"})
    placed, err = P.bind(_Op(), {"collection_id": "Sales"}, "u-8", resolve=True)
    check("in an enforcing turn bind returns an InputRequired error carrying the question",
          placed is None and isinstance(err, IR.InputRequired) and err.request["source"] == "repos.get.collection_id"
          and IR.pending() is not None)
    check("…whose text still reads as a sentence for any reader of (value, err)", "named 'Sales'" in str(err))
    IR.end_turn(tok)
    tok = IR.begin_turn(user_uuid="u-8", session_id="s-8", origin="interactive", profile={"id": "p-8", "tag": "OBS", "authorityConfig": {"inputPolicy": {"raisers": "observe"}}})
    placed, err = P.bind(_Op(), {"collection_id": "Sales"}, "u-8", resolve=True)
    check("in an observing turn bind fails honestly and the ambiguity is recorded",
          placed is None and not isinstance(err, IR.InputRequired) and "2 of your repositories" in err and IR.observed())
    IR.end_turn(tok)
    placed, err = P.bind(_Op(), {"collection_id": "Other"}, "u-8", resolve=True)
    check("a unique name binds as before", err is None and placed["path"]["collection_id"] == "c-3")
finally:
    P.RESOLVERS["collection_id"] = _orig
from trusted_data_agent.core import platform_profile_shape as SHAPE  # noqa: E402
try:
    SHAPE._pick("LLM configuration", "Gemini", [{"id": "l-1", "name": "Gemini"}, {"id": "l-2", "name": "gemini"}])
    check("_pick raises on two ingredients with one name", False)
except IR.Ambiguous as exc:
    check("_pick raises on two ingredients with one name (it took the first before)", exc.kind == "LLM configuration")
check("_pick still resolves a unique name", SHAPE._pick("LLM configuration", "Gemini", [{"id": "l-1", "name": "Gemini"}]) == ("l-1", None))
csrc = inspect.getsource(P.call)
check("call turns an InputRequired into the question result the model reads (status input_required, request_id, question, message)",
      "isinstance(err, _ir.InputRequired)" in csrc and '"status": "input_required"' in csrc and '"question": _store.public_view(err.request)' in csrc)

# ─────────────────────────── [7] the REST outcome rule ───────────────────────────
print("\n[7] the REST outcome: interrupted for a client that declared it, complete + flag for the rest")
from trusted_data_agent.api import rest_routes as R  # noqa: E402
pay = {"final_answer_text": "Which?", "input_request": {"id": "ir-x"}, "input_required": True}
check("a declaring client reads input_required", R._task_outcome(pay, [], accepts_input_required=True)[0] == "input_required")
check("every other client reads complete, with the flag beside the question", R._task_outcome(pay, [])[0] == "complete" and R._task_outcome(pay, [])[1]["input_required"] is True)
check("an answer without a question is complete as before", R._task_outcome({"final_answer_text": "42"}, [], accepts_input_required=True)[0] == "complete")
check("the error rule is unchanged", R._task_outcome(None, [{"event_type": "error", "event_data": {"error": "boom"}}])[0] == "error")
rsrc = src("src/trusted_data_agent/api/rest_routes.py")
check("the declaration is the X-Uderia-Accepts header on the query, stored on the task",
      'request.headers.get("X-Uderia-Accepts")' in rsrc and '"accepts_input_required": accepts_input_required' in rsrc)
for op in ("input_requests.list", "input_requests.get", "input_requests.answer", "input_requests.withdraw", "tasks.input"):
    check(f"the route {op} is described", f'@operation("{op}"' in rsrc)
check("answering and withdrawing are a person's act — human_only, never callable by an agent",
      re.search(r'@operation\("input_requests\.answer", effect="human_only"', rsrc)
      and re.search(r'@operation\("input_requests\.withdraw", effect="human_only"', rsrc)
      and re.search(r'@operation\("tasks\.input", effect="human_only"', rsrc))
check("a client body can never carry the re-entry binding", 'data.pop("input_answer", None)' in rsrc)
check("a REST answer continues under the question's own task id", "_resume_task_id" in rsrc and '"resumed_from"' in rsrc)
check("the completion notice carries the question and fires for an interrupted task too",
      '"input_request": final_result_payload.get("input_request")' in rsrc and 'in ("complete", "input_required")' in rsrc)

# ─────────────────────────── [8] resume end to end ───────────────────────────
print("\n[8] input_resume.accept: the person's act, validated and consumed once")
rec = STORE.create(user_uuid="u-9", kind="choose", items=q["items"], why=None, session_id="s-9", turn=2, task_id="t-9",
                   origin="rest", raised_by="code", profile_id="p-9", profile_tag="R", source="graphs.get.kg_id", db_path=DB)
res, why, code = RESUME.accept("u-9", rec["id"], decision="accept", values={"Graph": "kg-2"}, resume="not-a-token", via="rest", db_path=DB)
check("a binding this server did not issue is refused (403), nothing consumed",
      res is None and code == 403 and (STORE.get(rec["id"], "u-9", db_path=DB) or {}).get("status") == "pending")
res, why, code = RESUME.accept("u-9", rec["id"], decision="accept", values={"Graph": "kg-9"}, resume=rec["resume"], via="rest", db_path=DB)
check("an answer outside the options is refused (400), nothing consumed", res is None and code == 400)
res, why, code = RESUME.accept("u-10", rec["id"], decision="accept", values={"Graph": "kg-2"}, resume=rec["resume"], via="rest", db_path=DB)
check("another person is refused (404 — not theirs)", res is None and code == 404)
res, why, code = RESUME.accept("u-9", rec["id"], decision="accept", values={"Graph": "kg-2"}, resume=rec["resume"], via="rest", db_path=DB)
check("the owner's valid answer is accepted: the block, the line, the binding, the task to continue",
      code == 200 and res["request"]["status"] == "accepted" and "The user answered" in res["user_input"]
      and res["display_message"] == "Sales (south)" and res["input_answer"]["values"] == {"Graph": "kg-2"}
      and res["task_id"] == "t-9" and res["session_id"] == "s-9")
res2, why2, code2 = RESUME.accept("u-9", rec["id"], decision="accept", values={"Graph": "kg-2"}, resume=rec["resume"], via="rest", db_path=DB)
check("the same binding a second time is refused (409)", res2 is None and code2 == 409)
rec = STORE.create(user_uuid="u-9", kind="confirm", items=[{"header": "Drop", "question": "Drop?", "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]}],
                   why=None, session_id="s-9", turn=3, task_id=None, origin="interactive", raised_by="model", profile_id="p-9", profile_tag="R", source="TDA_AskUser", db_path=DB)
res, why, code = RESUME.accept("u-9", rec["id"], decision="decline", values=None, resume=rec["resume"], via="chat", db_path=DB)
check("a decline needs no values and says the work does not proceed", code == 200 and res["request"]["status"] == "declined" and "declined" in res["user_input"])

# ─────────────────────────── [9] the downgrade guard ───────────────────────────
print("\n[9] the input policy is authority: unsigned, downgrade-guarded")
from trusted_data_agent.core import profile_config_contract as C  # noqa: E402
d = C._authority_downgrades({"inputPolicy": {"interactive": "ask", "raisers": "enforce"}}, {"inputPolicy": {"interactive": "never", "raisers": "observe"}})
check("ask → never and enforce → observe are each a downgrade", sorted(x.path for x in d) == ["inputPolicy.interactive", "inputPolicy.raisers"])
check("the reverse is not", not C._authority_downgrades({"inputPolicy": {"interactive": "never"}}, {"inputPolicy": {"interactive": "ask"}}))
check("absent on both sides is not", not C._authority_downgrades({}, {}))

# ─────────────────────────── [10] the seams, in the source ───────────────────────────
print("\n[10] the seams hold: chokepoints, abstentions, the plan loop, the readers")
te = src("src/trusted_data_agent/agent/tool_enforcement.py")
check("sql_gate_before refuses past a question, after authority and before any statement is read",
      te.index("_ir_mod.refusal_for(tool_name)") > te.index("def sql_gate_before(") and te.index("_ir_mod.refusal_for(tool_name)") < te.index("gates = [nonstring_sql_block("))
check("the component chokepoint (ReAct) refuses past a question in the tool's (content, artifact) shape",
      "_ir_mod.refusal_for(_tool_name)" in src("src/trusted_data_agent/components/manager.py"))
ad = src("src/trusted_data_agent/mcp_adapter/adapter.py")
check("the adapter (Optimize) refuses every tool past a question, before the SQL gate",
      ad.index("_ir_mod.refusal_for(tool_name)") < ad.index("sql_gate_before(\n") if "sql_gate_before(\n" in ad else ad.index("_ir_mod.refusal_for(tool_name)") < ad.index("from trusted_data_agent.agent.tool_enforcement import LANE_OPTIMIZE"))
ex = src("src/trusted_data_agent/agent/executor.py")
for fn in ("_run_grounding_gate_full", "_run_honesty_gate", "_run_honesty_gate_rag"):
    body = ex[ex.index(f"def {fn}("):]
    body = body[:body.index("\n    def ", 10) if "\n    def " in body[10:] else len(body)]
    check(f"{fn} abstains on a turn that stopped to ask", "_ir_mod.pending() is not None" in body)
check("the plan ends at a question (the loop breaks before the next phase group)",
      '"step": "Plan Ended at a Question"' in ex and ex.index("Plan Ended at a Question") > ex.index("for group in phase_groups:"))
check("the executor stamps the turn onto the ledger", "_ir_mod.set_turn(self.current_turn_number)" in ex)
es = src("src/trusted_data_agent/agent/execution_service.py")
check("execution_service arms the ledger with the agent's policy and the re-entry answer, supersedes on a new turn, releases in the finally",
      "_ir_mod.begin_turn(user_uuid=user_uuid, session_id=session_id, origin=_ir_origin" in es
      and "_ir_store.supersede_pending_for_session(session_id, user_uuid" in es and "_ir_mod.end_turn(_ir_token)" in es)
check("…and finishes the turn in ONE place: the result, the record (input_required), the stream event",
      "_ir_mod.finish_payload(final_result_payload)" in es and 'session_manager.stamp_last_turn(' in es and '"type": "input_required"' in es)
check("the chat route validates and consumes the person's act before the turn runs", "_resume.accept(user_uuid, str(_ia.get(\"input_request_id\"))" in src("src/trusted_data_agent/api/routes.py"))
check("the session load serves the pending question with the owner's binding", '"pending_input_request"' in src("src/trusted_data_agent/api/routes.py"))
LIST = re.compile(r'in \((?:"complete"|"completed"|"failed"|"error"|"cancelled"|"success")(?:, ?"(?:complete|completed|failed|error|cancelled|success)")+\)|'
                  r'frozenset\(\{"complete"|\{"complete", "error", "cancelled"\}|\["complete", "error", "cancelled"\]')
for rel in ("src/trusted_data_agent/mcp_server/client.py", "src/trusted_data_agent/core/platform_asking.py", "src/trusted_data_agent/core/flow_executor.py",
            "src/trusted_data_agent/eval/rest_executor.py", "src/trusted_data_agent/eval/benchmarks/grounding/harness.py",
            "src/trusted_data_agent/eval/scorers/reliability.py", "src/trusted_data_agent/eval/online.py", "src/trusted_data_agent/eval/mining.py",
            "src/trusted_data_agent/agent/assurance_points.py", "src/trusted_data_agent/agent/grounding/coordinator_figures.py",
            "src/trusted_data_agent/core/session_backends/file_backend.py", "src/trusted_data_agent/agent/genie_coordinator.py",
            "src/trusted_data_agent/core/config.py", "src/trusted_data_agent/api/rest_routes.py"):
    t = src(rel)
    m = LIST.search(t)
    check(f"{rel.split('/')[-1]} holds no list of terminal strings of its own (it asks turn_states)", m is None and "turn_states" in t,
          m.group(0) if m else "turn_states not imported")
man = json.loads(src("components/builtin/needs_input/manifest.json"))
check("the component is a system component scoped to the agent, with a renderer (and, since phase 2, the one tool)",
      man["component_type"] == "system" and man["category"] == "System" and man["instances"] == "agent"
      and man["frontend"]["renderer_export"] == "renderNeedsInput" and man.get("tool_definition", {}).get("name") == "TDA_AskUser")

# ─────────────────────────── [11] phase 2 — the model's act on three carriers ───────────────────────────
print("\n[11] the model's act: one object, three carriers, one validator")
txt, ask = IR.extract_ask_block("I found two policies.\n\n```uderia-ask\n{\"kind\": \"choose\", \"question\": \"Which policy?\", \"options\": [\"Travel 2024\", \"Travel 2025\"]}\n```\n\nTell me which.")
check("the ```uderia-ask``` block is lifted out of the answer, the prose stays",
      ask and ask["kind"] == "choose" and txt == "I found two policies.\n\nTell me which." and "uderia-ask" not in txt)
check("no block → the text unchanged, None", IR.extract_ask_block("plain answer") == ("plain answer", None))
check("an unparsable block stays in the text and is None", IR.extract_ask_block("```uderia-ask\nnot json\n```")[1] is None and "uderia-ask" in IR.extract_ask_block("```uderia-ask\nnot json\n```")[0])
check("ASK_USER: with JSON is the act", IR.parse_ask_prefix('ASK_USER: {"kind": "choose", "question": "Which db?", "options": ["Sales_EU", "Sales_US"]}')["options"] == ["Sales_EU", "Sales_US"])
check("ASK_USER: with a bare sentence is a provide", IR.parse_ask_prefix("ASK_USER: Which fiscal year?") == {"kind": "provide", "question": "Which fiscal year?"})
check("no prefix → None", IR.parse_ask_prefix("FINAL_ANSWER: no") is None)
check("outside a turn the act cannot be raised and the model is told to answer with what it has",
      IR.raise_from_act({"kind": "confirm", "question": "Drop?"}, source="x")[0] is None and not IR.model_may_ask())
tok = IR.begin_turn(user_uuid="u-11", session_id="s-11", origin="interactive", profile={"id": "p-11", "tag": "ACT"})
check("in a turn the model may ask", IR.model_may_ask())
rec, note = IR.raise_from_act({"kind": "choose", "question": "Which policy?", "options": ["Travel 2024", "Travel 2025"], "why": "they differ"}, source="answer")
check("a valid act is raised as the model's question, pending, with the sentence the model reads",
      rec and rec["raised_by"] == "model" and rec["source"] == "answer" and IR.pending() is rec and "End your answer by asking it" in note)
rec2, note2 = IR.raise_from_act({"kind": "choose", "question": "Another?", "options": ["a", "b"]}, source="answer")
check("a second act names the pending one", rec2 is None and "already pending" in note2)
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-12", session_id="s-12", origin="interactive", profile={"id": "p-12", "tag": "BAD"})
rec, note = IR.raise_from_act({"kind": "provide", "question": "Your API key?", "schema": {"type": "object", "properties": {"api_key": {"type": "string"}}}}, source="answer")
check("an act asking for a credential is refused by the one validator — nothing pending", rec is None and "credential" in note and IR.pending() is None)
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-13", session_id="s-13", origin="interactive", profile={"id": "p-13", "tag": "NEV", "authorityConfig": {"inputPolicy": {"interactive": "never"}}})
check("under never the model may not ask, and an act is closed by policy with the honest sentence",
      not IR.model_may_ask() and "never to ask" in IR.raise_from_act({"kind": "confirm", "question": "Drop?"}, source="answer")[1])
IR.end_turn(tok)
sys.path.insert(0, str(ROOT / "components" / "builtin" / "needs_input"))
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("needs_input_handler", ROOT / "components" / "builtin" / "needs_input" / "handler.py")
_mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
H = _mod.NeedsInputHandler()
check("the component carries TDA_AskUser and offers it unless the policy is never",
      H.tool_name == "TDA_AskUser" and H.offers_tool({}) and not H.offers_tool({"authorityConfig": {"inputPolicy": {"interactive": "never"}}}))
tok = IR.begin_turn(user_uuid="u-14", session_id="s-14", origin="interactive", profile={"id": "p-14", "tag": "TOOL"})
pay = asyncio.run(H.process({"kind": "confirm", "question": "Drop the staging table?", "why": "it cannot be undone"}, {"user_uuid": "u-14"}))
check("the tool raises the question and answers the model with status input_required and the question",
      pay.spec["status"] == "input_required" and pay.spec["request_id"] == IR.pending()["id"] and pay.metadata["input_required"] is True
      and "End your answer by asking it" in pay.spec["message"] and pay.metadata["suppress_inline"])
pay2 = asyncio.run(H.process({"kind": "confirm", "question": "Again?"}, {"user_uuid": "u-14"}))
check("a second call is refused in the result, not raised", pay2.spec.get("refused") and "already pending" in pay2.spec["message"])
IR.end_turn(tok)
man = json.loads(src("components/builtin/needs_input/manifest.json"))
check("the manifest declares the tool with its required args and the instructions carry the bare API",
      man["tool_definition"]["name"] == "TDA_AskUser" and man["tool_definition"]["args"]["kind"]["required"] and man["tool_definition"]["args"]["question"]["required"]
      and "TDA_AskUser" in json.loads(src("components/builtin/needs_input/instructions.json"))["heavy"])
mg = src("src/trusted_data_agent/components/manager.py")
check("a system component's tool is offered where its manifest declares one (every gating site reads the one tuple)",
      '_TOOL_BEARING_TYPES = ("action", "system")' in mg and mg.count("_TOOL_BEARING_TYPES") >= 6 and 'component_type == "action"' not in mg.replace('component_type == "action" and', ''))
pl = src("src/trusted_data_agent/agent/planner.py")
check("the planner's carrier: plan_type input_required raises the act and ends the plan with no phases",
      'plan_object.get("plan_type") == "input_required"' in pl and '_ir_mod.raise_from_act(_ask, source="planner")' in pl
      and pl.index('plan_type") == "input_required"') > pl.index('plan_type") == "conversational"'))
pe = src("src/trusted_data_agent/agent/phase_executor.py")
check("the self-correction carrier: ASK_USER: raises the act and halts on the FINAL_ANSWER path, before FINAL_ANSWER is read",
      '_ir_mod.parse_ask_prefix(response_str)' in pe and pe.index('"ASK_USER:" in response_str') < pe.index('"FINAL_ANSWER:" in response_str'))
for rel in ("src/trusted_data_agent/agent/engines/conversation_engine.py", "src/trusted_data_agent/agent/engines/ideate_engine.py", "src/trusted_data_agent/agent/engines/focus_engine.py"):
    t = src(rel)
    check(f"{rel.split('/')[-1]} lifts the uderia-ask block before rendering and raises it once",
          "_ir_mod.extract_ask_block(response_text)" in t and 'raise_from_act(_ask, source="answer")' in t and "_ir_mod.pending() is None" in t)

# ─────────────────────────── [12] phase 3 — chains, remembered answers, unattended, auth, elicitation ───────────────────────────
print("\n[12] phase 3: the question travels up, the answer down; remembered; unattended; auth; a data source asks")
child_q = {"id": "ir-child", "kind": "choose", "why": "two regions", "items": [{"header": "Region", "question": "Which region?",
           "options": [{"value": "eu", "label": "EU"}, {"value": "us", "label": "US"}], "multi_select": False, "other_allowed": False}],
           "resume": "child-binding-never-copied"}
tok = IR.begin_turn(user_uuid="u-20", session_id="s-parent", origin="interactive", profile={"id": "p-20", "tag": "SOLVER"})
rec, note = IR.relay_child_question(child_q, agent_tag="@SALES", source="ask:@SALES:s-child")
check("a child's question becomes the parent's question: same items, raised by child, parent_id the child's, the chain on the record",
      rec and rec["raised_by"] == "child" and rec["parent_id"] == "ir-child" and rec["source"] == "ask:@SALES:s-child"
      and rec["items"][0]["options"][1]["value"] == "us" and "resume" not in rec["items"][0] and "@SALES asks" in (rec["why"] or ""))
check("the parent model is told to relay, never to answer", "Relay this question to the user" in note and "do not answer it yourself" in note)
check("the chokepoints now refuse in the parent turn too", IR.refusal_for("base_readQuery") is not None)
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-20", session_id="s-parent", origin="interactive", profile={"id": "p-20", "tag": "SOLVER"},
                    answer={"request": rec, "decision": "accept", "values": {"Region": "us"}})
cont = IR.continuation_for("ask:@SALES:")
check("on the parent's re-entry the ask step finds its continuation: the child's request and session, the person's values",
      cont and cont["child_request_id"] == "ir-child" and cont["child_session_id"] == "s-child" and cont["values"] == {"Region": "us"})
check("another step's prefix finds nothing", IR.continuation_for("coordinate:@OTHER:") is None)
IR.end_turn(tok)
# the answer propagates to the child's record through accept
child = STORE.create(user_uuid="u-21", kind="choose", items=child_q["items"], why=None, session_id="s-child2", turn=1, task_id="t-c",
                     origin="ask", raised_by="code", profile_id="p-c", profile_tag="SALES", source="repos.get.collection_id", db_path=DB)
parent = STORE.create(user_uuid="u-21", kind="choose", items=child_q["items"], why="@SALES asks", session_id="s-par2", turn=3, task_id=None,
                      origin="interactive", raised_by="child", profile_id="p-p", profile_tag="SOLVER", source=f"ask:@SALES:s-child2",
                      parent_id=child["id"], db_path=DB)
res, why, code = RESUME.accept("u-21", parent["id"], decision="accept", values={"Region": "eu"}, resume=parent["resume"], via="chat", db_path=DB)
check("accepting the parent's question answers the child's too, attributed as a relay",
      code == 200 and (STORE.get(child["id"], "u-21", db_path=DB) or {}).get("status") == "accepted"
      and (STORE.get(child["id"], "u-21", db_path=DB) or {}).get("answered_via") == "relay"
      and (STORE.get(child["id"], "u-21", db_path=DB) or {}).get("answered_by") == f"relay({parent['id']})")
# remembered answers (decision 10)
tok = IR.begin_turn(user_uuid="u-22", session_id="s-22", origin="interactive",
                    profile={"id": "p-22", "tag": "REM", "authorityConfig": {"inputPolicy": {"defaults": {IR.default_key_for_ambiguity("repository", "Sales"): "c-2"}}}})
check("a remembered answer on the agent resolves the ambiguity in code, recorded as a default used",
      P._resolve_owned("repository", dup, "Sales") == ("c-2", None) and IR.turn_stamp()["input_defaults_used"][0]["scope"] == "agent")
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-22", session_id="s-22", origin="interactive", profile={"id": "p-22", "tag": "REM"},
                    session_defaults={IR.default_key_for_question("Which policy?"): "Travel 2025"})
rec, note = IR.raise_from_act({"kind": "choose", "question": "Which policy?", "options": ["Travel 2024", "Travel 2025"]}, source="answer")
check("a remembered answer in the session hands the model the person's earlier answer instead of a question",
      rec is None and "Travel 2025" in note and "do not ask it again" in note and IR.pending() is None
      and IR.turn_stamp()["input_defaults_used"][0]["scope"] == "session")
IR.end_turn(tok)
check("only a single choose/confirm is ever remembered",
      IR.default_key_of({"kind": "choose", "items": [{"default_key": "k"}]}) == "k"
      and IR.default_key_of({"kind": "provide", "items": [{"default_key": "k"}]}) is None
      and IR.default_key_of({"kind": "choose", "items": [{"default_key": "k"}, {"default_key": "j"}]}) is None)
# unattended: a scheduled run's question names its run; expiry returns the rows
sched = STORE.create(user_uuid="u-23", kind="provide", items=[{"header": "FY", "question": "When does the fiscal year start?", "schema": {"type": "string"}}],
                     why=None, session_id="s-23", turn=1, task_id="sched:task-1:run-1", origin="scheduler", raised_by="code", profile_id="p",
                     profile_tag="T", source="x", expires_in_hours=-1, db_path=DB)
rows = STORE.expire_due(db_path=DB, return_rows=True)
check("the expiry job gets the expired records back, so an unattended run can be ended and told",
      any(r["id"] == sched["id"] and r["status"] == "expired" and r["task_id"] == "sched:task-1:run-1" for r in rows))
st = STORE.stats_for_user("u-21", db_path=DB)
check("the scoreboard readings: asked, accepted, by raiser, median time to answer",
      st["asked"] == 2 and st["accepted"] == 2 and st["by_raiser"].get("child") == 1 and st["median_answer_seconds"] is not None)
# auth_required
tok = IR.begin_turn(user_uuid="u-24", session_id="s-24", origin="interactive", profile={"id": "p-24", "tag": "MAIL"})
rec, note = IR.raise_auth_request(server_id="uderia-google", platform="google", display_name="Google")
check("an unconnected user connector raises an auth question whose only answer is connecting; the turn reads auth_required",
      rec and rec["kind"] == "auth" and rec["items"][0]["connector"]["platform"] == "google" and IR.turn_status() == "auth_required"
      and "connect their Google account" in note)
pay = {"final_answer_text": "x", "final_answer": "<p>x</p>"}
IR.finish_payload(pay)
check("the result says which interrupted state it is", pay["input_status"] == "auth_required")
check("a declaring REST client reads auth_required", R._task_outcome(pay, [], accepts_input_required=True)[0] == "auth_required")
IR.end_turn(tok)
# a data source elicits
items = IR.elicit_items_from_schema("Which warehouse?", {"type": "object", "properties": {"warehouse": {"type": "string", "enum": ["north", "south"]}, "limit": {"type": "integer", "description": "How many rows?"}}})
check("an MCP form schema becomes items: an enum field as options, a primitive as a typed field, each naming its field",
      len(items) == 2 and items[0]["field"] == "warehouse" and [o["value"] for o in items[0]["options"]] == ["north", "south"]
      and items[1]["schema"]["type"] == "integer" and items[1]["question"] == "How many rows?")
from trusted_data_agent.core import mcp_elicitation as ME  # noqa: E402
check("outside a turn an elicitation is cancelled, never raised",
      asyncio.run(ME.on_elicitation(None, type("P", (), {"message": "m", "requestedSchema": None, "url": None})(), type("C", (), {"server_name": "srv", "tool_name": "t"})())).action == "cancel")
tok = IR.begin_turn(user_uuid="u-25", session_id="s-25", origin="interactive", profile={"id": "p-25", "tag": "DS"},
                    answer={"request": {"raised_by": "data_source", "source": "mcp:srv:t", "items": [{"header": "warehouse"}]}, "decision": "accept", "values": {"warehouse": "north"}})
r = asyncio.run(ME.on_elicitation(None, type("P", (), {"message": "m", "requestedSchema": {"type": "object", "properties": {"warehouse": {"type": "string"}}}, "url": None})(),
                                   type("C", (), {"server_name": "srv", "tool_name": "t"})()))
check("on the re-entry turn the same elicitation is accepted at once with the person's answer (the retry pattern)",
      r.action == "accept" and r.content == {"warehouse": "north"})
IR.end_turn(tok)
for rel, needles in (
    ("src/trusted_data_agent/core/platform_asking.py", ("_ir.continuation_for(f\"ask:{ttag}:\")", "_ir.relay_child_question(child_q", '"source": "ask"')),
    ("src/trusted_data_agent/agent/genie_coordinator.py", ("_ir_mod.continuation_for(f\"coordinate:@{self.profile_tag}:\")", "_ir_mod.relay_child_question(", "_continue_with_answer")),
    ("src/trusted_data_agent/core/task_scheduler.py", ('_record_run_end(run_id, "input_required"', "async def resume_task_run", 'INPUT_EXPIRY_JOB_ID = "platform-input-expiry"', 'task_id=f"sched:{task_id}:{run_id}"', 'subject_suffix=" — needs your input"')),
    ("src/trusted_data_agent/core/flow_executor.py", ("class FlowNeedsInput(Exception)", 'update_run_status(self.run_id, "input_required"', "async def resume_flow_for_input", 'query_body["input_answer"] = _resume.get("input_answer")')),
    ("src/trusted_data_agent/core/platform_connector_registry.py", ("_auth_question_if_not_connected(server_id, user_uuid)", '"error": "auth_required"')),
    ("src/trusted_data_agent/core/configuration_service.py", ("callbacks=_cbs",)),
    ("src/trusted_data_agent/llm/langchain_adapter.py", ("load_mcp_tools(session=None, connection=connection, **_lk)",)),
    ("src/trusted_data_agent/mcp_server/server.py", ("answer_to: str = None", "[Needs input ")),
    ("src/trusted_data_agent/api/rest_routes.py", ('_tid.startswith("sched:")', "_flow_exec.resume_flow_for_input(", "def _request_is_internal", '@operation("input_requests.stats"')),
):
    t = src(rel)
    for n in needles:
        check(f"{rel.split('/')[-1]} carries the seam: {n[:48]}", n in t)

# ─────────────────────────── [13] phase 4 — the registered point and the measure ───────────────────────────
print("\n[13] phase 4: the assurance point, the measure, the mirrors")
from trusted_data_agent.agent import assurance_points as AP  # noqa: E402
pt = next((p for p in AP.points() if p.key == "needs_input"), None)
check("needs_input is registered at REVIEWED with the power to withhold, on every class", pt and pt.station == "reviewed" and pt.power == "withhold" and not pt.applies_to)
check("it owns guessed_past_ambiguity and the partition has no conflict",
      AP.POINT_DIMENSIONS.get("needs_input") == ("guessed_past_ambiguity",) and "guessed_past_ambiguity" in AP.station_dimensions().get("reviewed", ()) and AP.partition_conflicts() == [])
rd = AP.read_turn({"input_request": {"id": "ir-1", "kind": "choose", "raised_by": "code"}, "status": "input_required"}, "llm_only")
r1 = next(r for r in rd if r.key == "needs_input")
check("a turn that stopped to ask reads pass, acted — a reading, never an alarm", r1.state == "pass" and r1.acted and "asked the user" in (r1.word or ""))
rd = AP.read_turn({"ambiguities_observed": [{"code": "ambiguous_name", "kind": "repository", "value": "Sales", "candidates": ["a", "b"]}], "status": "success"}, "tool_enabled")
r2 = next(r for r in rd if r.key == "needs_input")
check("a turn that went past a code-detected ambiguity reads warn", r2.state == "warn" and "went past 1" in (r2.word or ""))
rd = AP.read_turn({"status": "success"}, "rag_focused")
check("a turn with nothing to ask reads na", next(r for r in rd if r.key == "needs_input").state == "na")
from trusted_data_agent.eval.scorers import reliability as _rel  # noqa: E402,F401 — registers the scorers
from trusted_data_agent.eval.scorer import all_scorers  # noqa: E402
from trusted_data_agent.eval.types import RunRecord  # noqa: E402
sc = next(s for s in all_scorers() if s.dimension == "guessed_past_ambiguity")
v_na = sc.score(None, RunRecord({"status": "success"}))
v_fail = sc.score(None, RunRecord({"status": "success", "ambiguities_observed": [{"kind": "repository"}]}))
v_pass = sc.score(None, RunRecord({"status": "input_required", "input_request_id": "ir-1", "ambiguities_observed": [{"kind": "repository"}]}))
check("the scorer abstains with nothing seen, fails a guess, passes a stop",
      v_na.passed is None and v_fail.passed is False and v_pass.passed is True and not sc.gating)
from trusted_data_agent.eval import stages as ST  # noqa: E402
_stages_with = [k for k, dims in ST.STAGE_DIMENSIONS.items() if "guessed_past_ambiguity" in tuple(dims)]
_gaps = ST.partition_gaps() if callable(getattr(ST, "partition_gaps", None)) else {}
check("the dimension is staged exactly once (THE_RUN), the partition has no gap for it",
      len(_stages_with) == 1 and str(_stages_with[0]).lower() == "the_run"
      and "guessed_past_ambiguity" not in str((_gaps or {}).get("undeclared") or "") and "guessed_past_ambiguity" not in str((_gaps or {}).get("duplicated") or ""))
qd = src("static/js/handlers/qualityDimensions.js")
check("the Guide names the dimension (label, description, the long form)", "guessed_past_ambiguity: { label: 'Guessed Past Ambiguity'" in qd and "guessed_past_ambiguity: 'PROP-011" in qd)
check("the ring mirrors the partition (reviewed owns it) and the mechanism map places the point at REVIEWED",
      "reviewed: ['guessed_past_ambiguity']" in src("static/js/handlers/agentPerformanceHandler.js") and "needs_input: 'reviewed'" in src("static/js/ui.js"))
sp = src("test/trust_campaign/suite.py")
check("the campaign targets the point on every engine (N1 tool + Ideate, N2 Focus, N3 Coordinate)",
      sp.count('targets=("needs_input",)') == 3 and '"reviewed": {"guessed_past_ambiguity"}' in sp)

# ─────────────────────────── [14] the fire drill — the test server's lever, off by default ───────────────────────────
print("\n[14] the fire drill: off by default; a marked input raises its question in code; a re-entry raises nothing")
from trusted_data_agent.agent import needs_input_drill as DR  # noqa: E402
os.environ.pop("TDA_NEEDS_INPUT_INJECT", None)
check("off by default: a marked input raises nothing", not DR.enabled() and DR.kind_of("x [needs-input:choose]") is None
      and asyncio.run(DR.fire("x [needs-input:choose]")) is None)
os.environ["TDA_NEEDS_INPUT_INJECT"] = "1"
check("on: every kind is recognised, nothing else", [DR.kind_of(f"[needs-input:{k}]") for k in ("choose", "provide", "confirm", "auth", "elicit")] == ["choose", "provide", "confirm", "auth", "elicit"]
      and DR.kind_of("[needs-input:approve]") is None and DR.kind_of("plain") is None)
check("outside a turn the drill says so and raises nothing", asyncio.run(DR.fire("[needs-input:choose]"))["request_id"] is None)
tok = IR.begin_turn(user_uuid="u-30", session_id="s-30", origin="interactive", profile={"id": "p-30", "tag": "DRILL"})
out = asyncio.run(DR.fire("Summarise the Sales graph [needs-input:choose]"))
check("a marked turn raises the choice in code (raised_by code), the turn reads input_required",
      out["request_id"] and IR.pending()["raised_by"] == "code" and IR.turn_status() == "input_required")
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-30", session_id="s-30", origin="interactive", profile={"id": "p-30", "tag": "DRILL"},
                    session_defaults={IR.default_key_for_ambiguity("knowledge graph", "Sales"): "kg-sales-2"})
out = asyncio.run(DR.fire("[needs-input:choose]"))
check("the drill takes the resolver's step first: a remembered answer means no question", out["request_id"] is None and "remembered" in out["note"] and IR.pending() is None)
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-30", session_id="s-31", origin="interactive", profile={"id": "p-30", "tag": "DRILL"},
                    answer={"request": {"id": "ir-x", "raised_by": "code", "source": "fire_drill"}, "decision": "accept", "values": {"x": "y"}})
check("a re-entry turn carrying the marker raises nothing (the answer is on the ledger)", "re-entry" in asyncio.run(DR.fire("[needs-input:provide]"))["note"] and IR.pending() is None)
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid="u-30", session_id="s-32", origin="interactive", profile={"id": "p-30", "tag": "DRILL"})
check("auth and confirm raise their kinds", asyncio.run(DR.fire("[needs-input:auth]"))["request_id"] and IR.turn_status() == "auth_required")
IR.end_turn(tok)
os.environ.pop("TDA_NEEDS_INPUT_INJECT", None)
check("the drill is wired after the ledger is armed, behind its switch", "_drill.enabled()" in src("src/trusted_data_agent/agent/execution_service.py") and "await _drill.fire(user_input)" in src("src/trusted_data_agent/agent/execution_service.py"))
check("the expiry job closes only a run that is WAITING (a turn still running records its own outcome)",
      "WHERE id = ? AND status = 'input_required'" in src("src/trusted_data_agent/core/task_scheduler.py")
      and '_now_q.get("status") in ("expired", "superseded")' in src("src/trusted_data_agent/core/task_scheduler.py"))
check("the live probe exists and drives every face (A–H)", all(f"[{s}]" in src("test/needs_input_live_probe.py") for s in "ABCDEFGH"))

# ── §15 the carriers are variables: the model's act is taught only while it may be taken ──
# (the component toggle governs the prompt text, the tool, the resolvers' posture — one rule)
check("outside a turn every carrier is nothing", all(IR.carrier_section(c) == "" for c in ("block", "block_focus", "plan", "correction")))
_on = {"id": "p-15a", "tag": "ON", "componentConfig": {"needs_input": {"enabled": True}}}
_off = {"id": "p-15b", "tag": "OFF", "componentConfig": {"needs_input": {"enabled": False}}}
tok = IR.begin_turn(user_uuid=None, session_id="s-15a", origin="interactive", profile=_on)
check("component in use: the policy says so, the model may ask, every carrier carries its act",
      IR.policy_for(_on).get("enabled") is True and IR.model_may_ask()
      and "uderia-ask" in IR.carrier_section("block") and "uderia-ask" in IR.carrier_section("block_focus")
      and '"plan_type": "input_required"' in IR.carrier_section("plan") and "ASK_USER:" in IR.carrier_section("correction"))
check("the carrier texts carry single braces (format kwargs never re-parse)", "{{" not in IR.carrier_section("plan") and "{{" not in IR.carrier_section("correction"))
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid=None, session_id="s-15b", origin="interactive", profile=_off)
_pol = IR.policy_for(_off)
check("component off: interactive never, raisers observe, the model may not ask, every carrier is nothing",
      _pol.get("enabled") is False and _pol["interactive"] == "never" and _pol["raisers"] == "observe" and not IR.model_may_ask()
      and all(IR.carrier_section(c) == "" for c in ("block", "block_focus", "plan", "correction")))
check("component off: an ambiguity is recorded, never raised", IR.ambiguity("repository", "Sales", [{"id": "1", "name": "Sales"}, {"id": "2", "name": "Sales"}]) is None and IR.pending() is None)
IR.end_turn(tok)
tok = IR.begin_turn(user_uuid=None, session_id="s-15c", origin="scheduler", profile=_off)
check("component off: an unattended run does not get the act either", not IR.model_may_ask())
IR.end_turn(tok)
check("a missing component reads off, a failed lookup keeps today's behaviour",
      "if comp is None:\n            return False" in src("src/trusted_data_agent/agent/input_requests.py")
      and "except Exception:  # noqa: BLE001\n        return True" in src("src/trusted_data_agent/agent/input_requests.py"))
check("the engines substitute {needs_input_carrier} from the turn: Ideate, Focus, the planner (both paths), the correction prompt",
      'replace("{needs_input_carrier}", _ir_carrier.carrier_section("block"))' in src("src/trusted_data_agent/agent/engines/ideate_engine.py")
      and "replace('{needs_input_carrier}', _ir_carrier.carrier_section('block_focus'))" in src("src/trusted_data_agent/agent/engines/focus_engine.py")
      and src("src/trusted_data_agent/agent/planner.py").count('"needs_input_carrier": _needs_input_carrier,') == 2
      and '_ir_carrier.carrier_section("plan")' in src("src/trusted_data_agent/agent/planner.py")
      and src("src/trusted_data_agent/agent/phase_executor.py").count("needs_input_carrier=_needs_input_carrier_text()") == 3)

os.unlink(DB)
# ── the plan's carrier reaches the strategic prompt through the ContextBuilder ──────────────
# (2 Nov 2026 added `needs_input_carrier` to the planner's variables; the builder's pass-through
#  list was not extended, and every strategic plan built through it raised KeyError until 11 Nov)
_cb = (ROOT / "components/builtin/context_window/context_builder.py").read_text(encoding="utf-8")
_pl = (ROOT / "src/trusted_data_agent/agent/planner.py").read_text(encoding="utf-8")
_tpl = (ROOT / "schema/default_prompts/WORKFLOW_META_PLANNING_PROMPT.txt").read_text(encoding="utf-8")
check("the strategic prompt carries the plan's carrier", "{needs_input_carrier}" in _tpl)
check("the planner hands the carrier to the ContextBuilder", '"needs_input_carrier": _needs_input_carrier,' in _pl)
_ck = _cb[_cb.index("control_keys = ["):_cb.index("]", _cb.index("control_keys = ["))]
check("the ContextBuilder passes the carrier through to the strategic template (control_keys)", '"needs_input_carrier"' in _ck)
import re as _re
_fields = set(_re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", _tpl))
_supplied = set(_re.findall(r'^\s+"([a-z_]+)",?\s*(?:#.*)?$', _ck, _re.M)) | set(_re.findall(r'tv\["([a-z_]+)"\]', _cb)) | set(_re.findall(r'_tv\["([a-z_]+)"\]', _pl))
check("every field of the strategic template is supplied on the ContextBuilder path", _fields <= _supplied | {"available_tools", "available_prompts", "component_tools", "turn_action_history", "knowledge_context", "rag_few_shot_examples"}, sorted(_fields - _supplied))

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
