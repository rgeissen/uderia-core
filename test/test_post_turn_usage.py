"""
An LLM call made AFTER its turn is booked once, the same way, and shown on every engine.

Found Oct 2026 on production: the model-gap proposal suggestion (a paid call on the agent's
model, made after the answer) showed in the Live Status as a bare yellow card titled with its
raw type, its tokens counted nowhere on reload, nothing live. Mapping every path showed the
same class elsewhere: the agent-memory extraction re-priced the WHOLE turn at the session model
and sent its live step into a stream that had already closed; a Coordinate turn never replayed
the steps appended to it (next steps, memory, the judge); Optimize replaced a producer's step
title with the raw type, and a bare stored event aborted its whole replay; older Ideate/Focus
turns rendered as a summary card dropped them.

  §A  core/post_turn_usage.book_post_turn_call (EXECUTED, stubbed store): session totals, the
      turn's PRICED increment (never a re-price), consumption, the step persisted in the shape
      every replay reads, and the live token_update + step on the notification channel —
      session-tagged; nothing written for a call with no session
  §B  the producers route through it: the proposal suggestion and the memory extraction
  §C  every engine's replay reads the appended steps: Coordinate, Ideate/Focus (detailed and
      summary-card turns), Ideate with tools, Optimize — with the stored title kept and a bare
      legacy event lifted into the payload shape (EXECUTED)
  §D  live: the notification channel renders both step types; the three step renderers and the
      four title maps know them; the last-statement reader counts them

Run: python test/test_post_turn_usage.py
"""
import asyncio
import json
import os
import subprocess
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:400] if detail is not None else ""))


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


print("[§A] one booking for a post-turn call (executed)")
from trusted_data_agent.core import post_turn_usage as PTU  # noqa: E402
from trusted_data_agent.core import session_manager as SM  # noqa: E402
from trusted_data_agent.core.config import APP_STATE  # noqa: E402

calls = []
session = {"input_tokens": 1000, "output_tokens": 100,
           "last_turn_data": {"workflow_history": [{"turn": 3, "turn_input_tokens": 500, "turn_output_tokens": 50}]}}


async def _utc(u, s, i, o):
    calls.append(("session", i, o)); session["input_tokens"] += i; session["output_tokens"] += o


async def _uttc(u, s, t, i, o, added_cost_usd=None, additive=False):
    calls.append(("turn", t, i, o, added_cost_usd, additive))
    tr = session["last_turn_data"]["workflow_history"][0]
    tr["turn_input_tokens"] += i; tr["turn_output_tokens"] += o


async def _gs(u, s):
    return session


async def _ase(u, s, t, evts):
    calls.append(("events", t, evts))


saved = (SM.update_token_count, SM.update_turn_token_counts, SM.get_session, SM.append_system_events_to_turn)
SM.update_token_count, SM.update_turn_token_counts, SM.get_session, SM.append_system_events_to_turn = _utc, _uttc, _gs, _ase


class Q:
    def __init__(self): self.items = []
    def put_nowait(self, m): self.items.append(m)


q = Q()
APP_STATE.setdefault("notification_queues", {})["u1"] = {q}
try:
    out = asyncio.run(PTU.book_post_turn_call(
        "u1", "s1", 3, event_type="proposal_suggestion_complete", step="Proposal Suggested",
        call_id="proposal_suggestion:abc", input_tokens=200, output_tokens=20, cost_usd=0.0012,
        provider="Google", model="gemini-x", details={"summary": "Suggested what the new concept records"}))
finally:
    SM.update_token_count, SM.update_turn_token_counts, SM.get_session, SM.append_system_events_to_turn = saved
check("the session's totals take the call", ("session", 200, 20) in calls, calls)
check("the turn takes its tokens AND its own priced cost, additively — never a re-price of the whole turn",
      ("turn", 3, 200, 20, 0.0012, True) in calls, calls)
ev = next((c[2] for c in calls if c[0] == "events"), [None])[0] or {}
check("the step is persisted in the shape every replay reads: {type, payload: {type, step, details}}",
      ev.get("type") == "proposal_suggestion_complete" and ev.get("payload", {}).get("step") == "Proposal Suggested"
      and ev["payload"].get("type") == "proposal_suggestion_complete"
      and ev["payload"]["details"].get("input_tokens") == 200 and ev["payload"]["details"].get("cost_usd") == 0.0012
      and ev["payload"]["details"].get("model") == "gemini-x", ev)
tu = next((m["payload"] for m in q.items if m["type"] == "token_update"), {})
check("live: a token_update with ABSOLUTE totals and the call's own id, session-tagged",
      tu.get("total_input") == 1200 and tu.get("turn_input") == 700 and tu.get("statement_input") == 200
      and tu.get("call_id") == "proposal_suggestion:abc" and tu.get("session_id") == "s1" and tu.get("cost_usd") == 0.0012, tu)
st = next((m for m in q.items if m["type"] == "proposal_suggestion_complete"), {})
check("live: the step itself on the notification channel, carrying its session",
      st.get("payload", {}).get("step") == "Proposal Suggested" and st["payload"]["details"].get("session_id") == "s1", st)
check("the caller gets the step back", out.get("step") == "Proposal Suggested")
calls.clear(); q.items.clear()
SM.update_token_count, SM.update_turn_token_counts, SM.get_session, SM.append_system_events_to_turn = _utc, _uttc, _gs, _ase
try:
    asyncio.run(PTU.book_post_turn_call("u1", None, None, event_type="memory_write", step="Memory Updated",
                                        call_id="m", input_tokens=5, output_tokens=1, cost_usd=0.0))
finally:
    SM.update_token_count, SM.update_turn_token_counts, SM.get_session, SM.append_system_events_to_turn = saved
check("a call with no session books nothing and notifies nothing", not calls and not q.items, (calls, q.items))
APP_STATE["notification_queues"].pop("u1", None)

print("[§B] the post-turn producers book through it")
ps = src("src/trusted_data_agent/agent/proposal_suggest.py")
check("the proposal suggestion — with a call id of its own (the live ledger is keyed by it)",
      "book_post_turn_call(" in ps and 'call_id=f"proposal_suggestion:{uuid.uuid4().hex[:12]}"' in ps
      and "append_system_events_to_turn" not in ps)
mr = src("components/builtin/memory/runtime.py")
check("the memory extraction — its hand-rolled booking (which re-priced the turn) is gone",
      "book_post_turn_call(" in mr and "_record_extraction_usage" not in mr
      and "update_turn_token_counts" not in mr and 'await event_handler(evt, "memory_write")' not in mr)

print("[§C] every engine's replay reads the appended steps")
eh = src("static/js/eventHandlers.js")
ui = src("static/js/ui.js")
check("Coordinate: the turn's appended steps replay after the coordinator's own (a type it recorded is not repeated)",
      "const appendedSteps = (turnData.system_events || []).filter(" in eh and "!genieTypes.has(e.type)" in eh
      and "appendedSteps.forEach((event, index) => {" in eh)
check("Ideate / Focus / Ideate-with-tools turns shown as a summary card replay them too (3 fallbacks)",
      eh.count("_replayAppendedSteps(turnData);") == 3)
check("Optimize keeps a producer's step title — the title map's raw-type fallback never replaces it",
      "if (harmonizedTitle && (harmonizedTitle !== eventData.type || !eventData.step)) {" in ui)
check("Optimize reads a bare stored event as its own payload (it used to abort the replay)",
      "const eventData = event.payload || { type: event.type, step: event.step, details: event.details || {} };" in ui)
check("a bare stored event is lifted into the payload shape once, where the replay receives the turn",
      "_normalizeTurnEventLists(turnData);" in eh)
start = eh.index("function _normalizeTurnEventLists(")
end = eh.index("\n}\n", start) + 3
probe = eh[start:end] + """
const t = { system_events: [
  { type: 'proposal_suggestion', step: 'Proposal suggested', details: { input_tokens: 9 } },
  { type: 'next_step_generation_complete', payload: { type: 'next_step_generation_complete', step: 'S', details: {} } } ],
  genie_events: [ { type: 'genie_llm_step', payload: { input_tokens: 3 } } ] };
_normalizeTurnEventLists(t);
console.log(JSON.stringify(t));
"""
r = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
try:
    t = json.loads(r.stdout)
except Exception:  # noqa: BLE001
    t = {}
check("…executed: the bare event gains its payload; a stored event and a genie details-payload are untouched",
      (t.get("system_events") or [{}])[0].get("payload", {}).get("details", {}).get("input_tokens") == 9
      and t["system_events"][1]["payload"]["step"] == "S"
      and t["genie_events"][0] == {"type": "genie_llm_step", "payload": {"input_tokens": 3}}, (r.stdout, r.stderr))

print("[§D] live, titles, details, the last statement")
nt = src("static/js/notifications.js")
check("the notification channel renders both post-turn steps, session-guarded",
      "case 'proposal_suggestion_complete':\n            case 'memory_write': {" in nt
      and "if (psid && psid === state.currentSessionId) {" in nt)
check("the three step renderers (standard, conversation/knowledge, coordinator) show what it did, model, tokens, cost",
      ui.count("detailsEl.innerHTML = _renderPostTurnLlmCallDetails(details);") == 2
      and "} else if (POST_TURN_LLM_TYPES.includes(type)) {" in ui and "function _renderPostTurnLlmCallDetails(" in ui)
check("the four title maps name them (a legacy event has no usable step)",
      eh.count("        case 'proposal_suggestion_complete':\n        case 'proposal_suggestion':\n            return 'Proposal Suggested';") == 4
      and eh.count("        case 'memory_write':\n            return 'Memory Updated';") == 4)
check("the reload's last-statement reader counts them as post-turn calls",
      "'proposal_suggestion_complete', 'proposal_suggestion', 'memory_write']);" in eh)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
