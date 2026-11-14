"""S10 — Steer & Reattach: the gate (spec DoD).

Executes the REAL modules with the master switch ON: the cursor-numbered event log
(`core/turn_stream.py`: open/append/stamp, events after a cursor, the retained floor and the
declared GAP, done + TTL pruning, the running task of a session, the async tail with its
keepalive and its `reattach_end`), the steer queue (`core/steer.py`: bounds, consume at a
boundary / at turn start, the note, the stamp, the announce over the notification channel),
the ReAct tool boundary through the real guarded coroutine (a queued steer rides the NEXT
result, once), the other seams structurally (tactical decision · final report · turn start ·
the persisted stamp · the five endpoints and their gate), and the switch-off contract (every
seam inert, the frame unchanged). Mutation-tested (see scratch mut_s10.py)."""
import asyncio, json, os, pathlib, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import logging; logging.disable(logging.CRITICAL)
os.environ["TDA_STEER_REATTACH"] = "1"
from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
APP_CONFIG.STEER_REATTACH = True
from trusted_data_agent.core import turn_stream as TS, steer as ST

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:300]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

print("[1] the event log — cursors, replay, the floor, done")
TS._reset_for_tests()
TS.open("t1", "sess-a", "u1")
f1 = 'data: {"task_id": "t1"}\nevent: task_start\n\n'
s1 = TS.append("t1", f1); s2 = TS.append("t1", 'data: {"step": "a"}\n\n'); s3 = TS.append("t1", 'data: {"step": "b"}\n\n')
check("append numbers frames 1, 2, 3", (s1, s2, s3) == (1, 2, 3))
check("stamp puts the cursor as the SSE id: field, first", TS.stamp(f1, 1) == "id: 1\n" + f1 and TS.stamp(f1, None) == f1)
evs, done, gap = TS.events_since("t1", 1)
check("events after cursor 1 are 2 and 3, not done, no gap", [s for s, _ in evs] == [2, 3] and done is False and gap is None, (evs, done, gap))
check("get() reports the session, owner, last cursor", TS.get("t1")["session_id"] == "sess-a" and TS.get("t1")["user_uuid"] == "u1" and TS.get("t1")["last_seq"] == 3 and TS.get("t1")["done"] is False)
check("active_for_session finds the running task (and honours the owner)", TS.active_for_session("sess-a")["task_id"] == "t1" and TS.active_for_session("sess-a", "u2") is None and TS.active_for_session("nope") is None)
TS.mark_done("t1")
check("done: appends are refused, the log stays readable, no longer active", TS.append("t1", "x") is None and TS.events_since("t1", 0)[1] is True and TS.active_for_session("sess-a") is None)
check("an unknown task reads as done + empty (never a crash)", TS.events_since("ghost", 0) == ([], True, None) and TS.get("ghost") is None)
# the floor + the gap
TS.open("t2", "sess-b", "u1")
old_max = TS.MAX_EVENTS; TS.MAX_EVENTS = 5
for i in range(8): TS.append("t2", f"data: {i}\n\n")
TS.MAX_EVENTS = old_max
evs, _, gap = TS.events_since("t2", 0)
check("a full log drops the oldest frames and DECLARES the gap (the first retained cursor)", [s for s, _ in evs] == [4, 5, 6, 7, 8] and gap == 4, (evs, gap))
check("a cursor at or above the floor sees no gap", TS.events_since("t2", 3)[2] is None and TS.events_since("t2", 5)[2] is None)
# prune by TTL + count
TS.mark_done("t2"); TS._TASKS["t2"]["done_at"] = time.time() - TS.DONE_TTL_SECONDS - 1
TS.open("t3", "sess-c", "u1")
check("a finished log past its TTL is pruned on the next open", TS.get("t2") is None and TS.get("t3") is not None)

print("[2] the tail — replay then live, keepalive, reattach_end")
async def tail_scenario():
    TS.open("t4", "sess-d", "u1")
    TS.append("t4", 'data: {"n": 1}\n\n'); TS.append("t4", 'data: {"n": 2}\n\n')
    out = []
    async def reader():
        async for item in TS.tail("t4", 1, keepalive=0.05):
            out.append(item)
    r = asyncio.create_task(reader())
    await asyncio.sleep(0.12)                 # a keepalive ping fires while nothing lands
    TS.append("t4", 'data: {"n": 3}\n\n')
    await asyncio.sleep(0.02)
    TS.mark_done("t4")
    await asyncio.wait_for(r, timeout=2)
    return out
out = asyncio.run(tail_scenario())
check("tail: replays frame 2 (cursor-stamped), pings on silence, delivers 3 live, ends with reattach_end",
      out[0].startswith("id: 2\n") and any(x == ": ping\n\n" for x in out) and any(x.startswith("id: 3\n") for x in out) and "reattach_end" in out[-1] and '"last_seq": 3' in out[-1], out)
check("tail from a cursor beyond the log replays nothing before the live part", not any(x.startswith("id: 1\n") for x in out))
async def gap_scenario():
    TS.open("t5", "sess-e", "u1")
    TS.MAX_EVENTS = 3
    for i in range(6): TS.append("t5", f"data: {i}\n\n")
    TS.MAX_EVENTS = old_max
    TS.mark_done("t5")
    return [x async for x in TS.tail("t5", 0)]
g = asyncio.run(gap_scenario())
check("tail below the floor says GAP first (the client re-reads the session), then what is retained", "reattach_gap" in g[0] and '"floor": 4' in g[0] and g[1].startswith("id: 4\n"), g[:2])


print("[2b] review fixes: the tail's end says whether the turn is over; the cap evicts finished logs first; the firewall scans before the steer note")
async def ended_tail():
    TS.open("t6", "sess-f", "u1"); TS.append("t6", 'data: {"n": 1}\n\n'); TS.mark_done("t6")
    return [x async for x in TS.tail("t6", 0)]
e6 = asyncio.run(ended_tail())
check("a tail on a FINISHED turn ends with reattach_end carrying done:true (the client never retries a dead turn)", '"done": true' in e6[-1], e6[-1])
# the eviction rule
TS._reset_for_tests()
old_cap = TS.MAX_TASKS; TS.MAX_TASKS = 3
TS.open("run-old", "s1", "u1"); TS._TASKS["run-old"]["started"] -= 100
TS.open("done-1", "s2", "u1"); TS.mark_done("done-1"); TS._TASKS["done-1"]["started"] -= 50
TS.open("run-new", "s3", "u1")
TS.open("run-newer", "s4", "u1")
TS.open("trigger", "s5", "u1")          # the prune runs at the next open, before the new log lands
check("over the cap, a FINISHED log is evicted before any running one — the oldest running log survives", TS.get("done-1") is None and TS.get("run-old") is not None and TS.get("run-new") is not None and TS.get("run-newer") is not None and TS.get("trigger") is not None, sorted(TS._TASKS))
TS.MAX_TASKS = old_cap; TS._reset_for_tests()
TEs = src("src/trusted_data_agent/agent/tool_enforcement.py")
_g = TEs[TEs.index("def _make_guarded_coroutine("):]
# The firewall runs in the gate's after-steps (sql_gate_after, first line); the steer is read after.
_after = TEs.split("def sql_gate_after")[1].split("\ndef ")[0]
_i_scan, _i_steer = (_g.find("sql_gate_after(") if "scan_result(tool_name, result" in _after else -1), _g.find("steer_note(session_id, tool_name, user_uuid)")
check("the tool-result firewall scans the TOOL's output (present) before the steer note is prepended", _i_scan >= 0 and _i_steer >= 0 and _i_scan < _i_steer, (_i_scan, _i_steer))
print("[3] the steer queue")
ST._reset_for_tests()
st = ST.queue("sess-a", "  be terser  ", "u1")
check("queue trims + records; pending lists it", st["text"] == "be terser" and not st["consumed"] and ST.pending("sess-a")[0]["id"] == st["id"])
for bad in ("", "   ", "x" * (ST.MAX_TEXT + 1)):
    try: ST.queue("sess-a", bad, "u1"); check(f"queue refuses {bad[:3]!r}…", False)
    except ValueError: check(f"queue refuses an invalid steer ({len(bad)} chars)", True)
for i in range(ST.MAX_PENDING - 1): ST.queue("sess-a", f"s{i}", "u1")
try: ST.queue("sess-a", "one too many", "u1"); check("the queue is bounded", False)
except ValueError as e: check("the queue is bounded and names the bound", str(ST.MAX_PENDING) in str(e))
got = ST.consume("sess-a", "base_readQuery")
check("consume takes every pending steer, stamped with where", len(got) == ST.MAX_PENDING and all(g_["consumed"] and g_["at"] == "base_readQuery" for g_ in got) and ST.pending("sess-a") == [])
n = ST.note(got)
check("the note leads with the operator line and lists each steer", n.startswith(ST.NOTE_LEAD) and "- be terser" in n and n.count("\n- ") == ST.MAX_PENDING)
check("consume with nothing pending is empty; the note of nothing is empty", ST.consume("sess-a", "x") == [] and ST.note([]) == "")
stamp = ST.stamp_for_turn("sess-a")
check("the stamp records the considered steers + where; none carried", stamp["considered"] == ST.MAX_PENDING and stamp["carried"] == 0 and stamp["steers"][0]["at"] == "base_readQuery")
ST.queue("sess-a", "late one", "u1")
stamp2 = ST.stamp_for_turn("sess-a")
check("a steer left pending at the turn's end is stamped as carried", stamp2["carried"] == 1 and any(not r["consumed"] for r in stamp2["steers"]))
ST.begin_turn("sess-a")
check("a new turn clears the considered record but keeps the pending steer", ST.stamp_for_turn("sess-a")["considered"] == 0 and len(ST.pending("sess-a")) == 1)
async def start_scenario():
    return ST.consume_at_turn_start("sess-a", "u1")
note0 = asyncio.run(start_scenario())
check("consume_at_turn_start delivers the carried steer with the next-turn lead", note0 and note0.startswith(ST.NEXT_TURN_LEAD) and "late one" in note0 and ST.pending("sess-a") == [])
check("stamp None for a session that never steered", ST.stamp_for_turn("never") is None and ST.consume_at_boundary("never", "t") is None)
# announce over the notification channel
class _Q:
    def __init__(self): self.items = []
    def put_nowait(self, x): self.items.append(x)
q = _Q(); APP_STATE.setdefault("notification_queues", {})["u9"] = {q}
async def ann_scenario():
    ST.queue("sess-z", "cite every figure", "u9")
    return ST.consume_at_boundary("sess-z", "base_readQuery", "u9")
nz = asyncio.run(ann_scenario())
check("consume_at_boundary announces ONE steer_consumed step over the user's channel, session-tagged, and returns the note",
      nz and len(q.items) == 1 and q.items[0]["type"] == "steer_consumed" and q.items[0]["payload"]["session_id"] == "sess-z" and q.items[0]["payload"]["details"]["at"] == "base_readQuery" and "cite every figure" in q.items[0]["payload"]["details"]["steers"][0]["text"], q.items)
check("announce without a loop never raises (loop-guarded)", ST.announce("u9", "sess-z", [{"id": "a", "text": "t"}], "x") is None)
APP_STATE["notification_queues"].pop("u9", None)

print("[4] the ReAct tool boundary — the steer rides the NEXT result, once")
from trusted_data_agent.agent import tool_enforcement as TE
async def boundary_scenario():
    ST._reset_for_tests()
    calls = []
    async def orig(**kw): calls.append(kw); return ("rows: 3", None)
    g = TE._make_guarded_coroutine("base_readQuery", orig, "sess-r", user_uuid="u1")
    r0 = await g(sql="SELECT 1")
    ST.queue("sess-r", "only EMEA please", "u1")
    r1 = await g(sql="SELECT 2")
    r2 = await g(sql="SELECT 3")
    return r0, r1, r2
r0, r1, r2 = asyncio.run(boundary_scenario())
check("no steer ⇒ the result is untouched", r0[0] == "rows: 3")
check("a queued steer LEADS the next tool result (the first thing the model reads), with the operator lead", r1[0].startswith(ST.NOTE_LEAD) and "only EMEA please" in r1[0] and r1[0].rstrip().endswith("rows: 3"), r1)
check("…and only once — the result after it is clean again", r2[0] == "rows: 3", r2)

print("[5] the other seams + the endpoints (structural)")
PE = src("src/trusted_data_agent/agent/phase_executor.py")
check("the tactical decision reads a steer before its call (Optimize's step boundary)", "_steer.consume_at_boundary(self.executor.session_id" in PE and "prompt=_tactical_prompt" in PE)
AD = src("src/trusted_data_agent/mcp_adapter/adapter.py")
check("the final report reads a steer before its call (the last Optimize boundary)", '_steer.consume_at_boundary(session_id, "TDA_FinalReport", user_uuid)' in AD and AD.index('"TDA_FinalReport", user_uuid)') < AD.index("prompt=final_summary_prompt_text"))
ES = src("src/trusted_data_agent/agent/execution_service.py")
check("the turn start delivers a carried steer with the question, after begin_turn", "_steer_mod.begin_turn(session_id)" in ES and "_steer_mod.consume_at_turn_start(session_id, user_uuid)" in ES and 'user_input = f"{user_input}\\n\\n{_carried}"' in ES)
SM = src("src/trusted_data_agent/core/session_manager.py")
check("the persistence chokepoint stamps steers with setdefault", 'turn_data.setdefault("steers", _steer_stamp)' in SM)
RT = src("src/trusted_data_agent/api/routes.py")
check("the UI stream opens a log, stamps every frame with its cursor, marks done in finally", "_tstream.open(task_id, session_id, user_uuid)" in RT and "await queue.put(_tstream.stamp(sse_event, _tstream.append(task_id, sse_event)))" in RT and "_tstream.mark_done(task_id)" in RT and RT.index("_tstream.mark_done(task_id)") < RT.index("await queue.put(None)\n\n        asyncio.create_task(run_and_signal_completion())"))
check("task_start says reattachable only when the switch is on", '_start["reattach"] = True' in RT and "if _tstream.enabled():" in RT)
RR = src("src/trusted_data_agent/api/rest_routes.py")
for ep in ("/v1/sessions/<session_id>/steer", "/v1/sessions/<session_id>/steers", "/v1/tasks/<task_id>/events", "/v1/tasks/<task_id>/stream"):
    i = RR.index(f'"{ep}"')
    body = RR[i:i + 1400]
    check(f"{ep} is switch-gated before any read", "_steer_gate()" in body and body.index("_steer_gate()") < body.index("_get_user_uuid_from_request()" if "_get_user_uuid_from_request()" in body else "_reattach_owner"))
check("a steer needs a RUNNING turn (409) and an owner-scoped session", '"code": "no_running_turn"}), 409' in RR and "_ts.active_for_session(session_id, user_uuid)" in RR)
check("a task's log is served to its owner only (404 for anyone else)", 'rec.get("user_uuid") != _get_user_uuid_from_request()' in RR)

print("[6] the switch off — every seam inert")
APP_CONFIG.STEER_REATTACH = False
TS._reset_for_tests(); ST._reset_for_tests()
TS.open("off1", "sess-off", "u1")
check("off: nothing is logged, the frame is served unchanged, no task is active", TS.append("off1", "data: x\n\n") is None and TS.stamp("data: x\n\n", TS.append("off1", "data: x\n\n")) == "data: x\n\n" and TS.get("off1") is None and TS.active_for_session("sess-off") is None)
try: ST.queue("sess-off", "hello", "u1"); check("off: queue refuses", False)
except ValueError: check("off: queue refuses", True)
check("off: consume yields nothing, stamp None", ST.consume("sess-off", "x") == [] and ST.stamp_for_turn("sess-off") is None)
async def off_boundary():
    async def orig(**kw): return ("rows: 3", None)
    g = TE._make_guarded_coroutine("base_readQuery", orig, "sess-off", user_uuid="u1")
    return await g(sql="SELECT 1")
check("off: the tool boundary appends nothing", asyncio.run(off_boundary())[0] == "rows: 3")
APP_CONFIG.STEER_REATTACH = True

print(f"\n=== S10 steer & reattach: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
