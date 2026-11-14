"""A REST task whose turn errored and answered nothing is reported as `error`, never `complete`.

FOUND LIVE (trust campaign 3, 2026-10-28, Optimize · P2): the engine's unrecoverable-error path
emits an `error` event and returns no payload (no `final_answer`), and the REST background task
marked it `complete` with an empty result — a polling consumer (n8n, Flowise, the campaign, a
coordinator polling its expert) read a finished turn with nothing in it. The rule
(`rest_routes._task_outcome`, pure): a payload ⇒ `complete` whatever was recovered on the way;
no payload + an `error` event ⇒ `error` with the engine's own message and details, in the same
`result` shape the exception path writes. CI-pure.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.api import rest_routes as R  # noqa: E402

ERR = {"event_type": "error", "event_data": {"error": "Execution stopped due to an unrecoverable error.",
                                             "details": "'Client' object has no attribute 'chat'", "type": "error"}}
NOTE = {"event_type": "notification", "event_data": {"type": "conversation_llm_step"}}

st, res = R._task_outcome(None, [NOTE, ERR])
check("no payload + an error event → error", st == "error", st)
check("…with the engine's own message and details",
      res.get("error", "").startswith("Execution stopped") and "no attribute 'chat'" in (res.get("details") or ""))
check("…in the shape the exception path writes (result.error)", res.get("status") == "error")

st, res = R._task_outcome({"final_answer": "42"}, [ERR, NOTE])
check("an answer is complete, whatever error it recovered from (a delegated prompt that failed)",
      st == "complete" and res == {"final_answer": "42"})

st, res = R._task_outcome(None, [NOTE])
check("no payload and no error event → complete, as before (no invented failure)", st == "complete" and res is None)
check("no events at all → complete, as before", R._task_outcome(None, None) == ("complete", None))
st, _ = R._task_outcome(None, [{"event_type": "error", "event_data": "a bare string"}])
check("an error event without a readable body does not crash and does not invent a message", st == "complete")
st, res = R._task_outcome(None, [{"event_type": "error", "event_data": {"type": "error"}}])
check("an error event with no message still reads error, with a plain sentence",
      st == "error" and res["error"] == "The turn stopped before producing an answer.")

src = inspect.getsource(R)
check("the background task decides its status through the one rule",
      "_status, _result = _task_outcome(final_result_payload, task_status_dict.get(\"events\"),\n                                                 accepts_input_required=bool(task_status_dict.get(\"accepts_input_required\")))" in src
      and 'task_status_dict["status"] = _status' in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
