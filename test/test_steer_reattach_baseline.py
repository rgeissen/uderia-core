"""S10 baseline freeze — committed GREEN before Steer & Reattach landed.

Pins that with NO steer/reattach (the master switch off) nothing changes: an SSE frame the
executor formats carries exactly its data/event lines (no `id:` cursor — the log stamps NOTHING
off), the stream's opening `task_start` payload is `{task_id}` and nothing else, the ReAct tool
boundary appends nothing to a tool result beyond today's notes, no `steers` stamp lands at the
persistence chokepoint, every steer / active-task / task-stream endpoint is refused off, the
composer is DISABLED while a turn runs (today's contract — S10 keeps it live in steer mode ONLY
when a turn is known to be steerable, which off never is), and the UI stream parser still
accepts a frame with no cursor line. Every assertion here must stay green through S10 and
after it (with the switch off) — it asserts BEHAVIOUR under the switch, never the absence of
code, because an absence pin is true only before the feature lands."""
import asyncio, json, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import logging; logging.disable(logging.CRITICAL)
os.environ.pop("TDA_STEER_REATTACH", None)
from trusted_data_agent.core.config import APP_CONFIG
if hasattr(APP_CONFIG, "STEER_REATTACH"):
    APP_CONFIG.STEER_REATTACH = False

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:300]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

print("[1] the switch is absent or off")
check("STEER_REATTACH absent or False by default", not getattr(APP_CONFIG, "STEER_REATTACH", False))

print("[2] an SSE frame is data + event, nothing else")
from trusted_data_agent.agent.executor import PlanExecutor
frame = PlanExecutor._format_sse({"task_id": "t1"}, "task_start")
check("the frame is exactly data + event lines, no id: cursor", frame == 'data: {"task_id": "t1"}\n\nevent: task_start\n\n'.replace('\n\nevent', '\nevent') and not frame.startswith("id:") and "\nid:" not in frame, repr(frame))
RT = src("src/trusted_data_agent/api/routes.py")
_open = RT[RT.index('_start = {"task_id": task_id}'):RT.index('_start = {"task_id": task_id}') + 400]
check("the stream opens with a task_start carrying ONLY the task id (the reattach flag is added under the switch alone)",
      '_start = {"task_id": task_id}' in _open and 'if _tstream.enabled():' in _open and '_start["reattach"] = True' in _open)
check("the execution is already a task of its own (a closed client never cancels it)", "asyncio.create_task(run_and_signal_completion())" in RT)

print("[3] the ReAct tool boundary appends only today's notes")
from trusted_data_agent.agent import tool_enforcement as TE
async def _orig(**kw): return ("rows: 3", None)
async def _drive():
    g = TE._make_guarded_coroutine("base_readQuery", _orig, "s10-freeze", user_uuid="u")
    return await g(sql="SELECT 1")
out = asyncio.run(_drive())
check("a clean tool result comes back byte-identical (no steer note appended)", out[0] == "rows: 3", out)

print("[4] off: no steer stamp, every endpoint refused, the composer disabled while a turn runs")
from trusted_data_agent.core import steer as ST, turn_stream as TS
check("the steer queue reports itself off", ST.enabled() is False and TS.enabled() is False)
check("no cursor is stamped off (the log appends nothing, stamp() leaves the frame alone)",
      TS.append("t-freeze", "data: {}\nevent: x\n\n") is None and TS.stamp("data: {}\nevent: x\n\n", None) == "data: {}\nevent: x\n\n")
check("no steer stamp off (stamp_for_turn is None → setdefault never runs)", ST.stamp_for_turn("s10-freeze") is None)
SM = src("src/trusted_data_agent/core/session_manager.py")
check("the persistence chokepoint stamps the steers key ONLY when a stamp exists (setdefault under the None guard)",
      'setdefault("outcome"' in SM and "_steer_stamp = _steer.stamp_for_turn(session_id)" in SM and "if _steer_stamp:" in SM)
RR = src("src/trusted_data_agent/api/rest_routes.py")
_gate = RR[RR.index("def _steer_gate"):RR.index("def _steer_gate") + 600]
check("the endpoint gate refuses with 404 steer_reattach_disabled when the switch is off", '"steer_reattach_disabled"' in _gate and "404" in _gate)
for _ep in ("/v1/sessions/<session_id>/steer\"", "/v1/sessions/<session_id>/steers\"", "/v1/tasks/<task_id>/events\"", "/v1/tasks/<task_id>/stream\""):
    _i = RR.index(_ep); _body = RR[_i:_i + 900]
    check(f"endpoint {_ep.rstrip(chr(34))} consults the gate first", "_steer_gate()" in _body, _ep)
_i = RR.index('"/v1/sessions/<session_id>/active-task"'); _body = RR[_i:_i + 1600]
check("active-task (a read every session load makes) answers 'nothing running, switch off' with 200 — never a 404 — and reads nothing else first",
      "if not _ts.enabled():" in _body and '"task_id": None' in _body and '"enabled": False}), 200' in _body
      and _body.index("if not _ts.enabled():") < _body.index("_get_user_uuid_from_request()"))
UI = src("static/js/ui.js")
check("the composer input is disabled while a turn runs unless a steerable turn is known (off ⇒ never)",
      "DOM.userInput.disabled = isActive && !_steerLive;" in UI and "SteerReattach.shouldSteer(state.currentSessionId)" in UI)
EH = src("static/js/eventHandlers.js")
ps = EH[EH.index("async function processStream("):EH.index("async function processStream(") + 8000]
check("the stream parser still reads data:/event: lines (a frame without id: parses as before)", "line.startsWith('data:')" in ps and "line.startsWith('event:')" in ps)
SR = src("static/js/handlers/steerReattach.js")
_ots = SR[SR.index("function onTaskStart("):SR.index("function onTaskStart(") + 400]
check("the client arms steer/reattach only on a task the stream declared reattachable (task_start.reattach — absent off)",
      "!payload.reattach) return;" in _ots)

print(f"\n=== S10 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
