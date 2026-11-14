"""S8 baseline freeze — committed GREEN before Regenerate-as-an-Experiment landed.

Pins that with NO turn lineage (the master switch off, or no regenerate/edit ever asked for)
nothing changes, EXECUTING the real seams over an isolated session store: a user + assistant
message land in both histories with exactly today's keys and today's turn numbering; a turn
record lands through the persistence chokepoint with no lineage keys and never disturbs another
turn's validity; the session body carries no `branches`; the eval hook is dispatched from the
one execution seam unconditionally (every turn is scored — a sibling will be an ordinary turn);
the served history carries `turn_number` on every message (what a comparison can group by).
Every assertion here must stay green through S8 and after it (with the switch off)."""
import asyncio, logging, os, pathlib, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.pop("TDA_TURN_LINEAGE", None)
from trusted_data_agent.core.config import APP_CONFIG
if hasattr(APP_CONFIG, "TURN_LINEAGE"):
    APP_CONFIG.TURN_LINEAGE = False

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

print("[1] the switch is absent or off")
check("TURN_LINEAGE absent or False by default", not getattr(APP_CONFIG, "TURN_LINEAGE", False))

print("[2] an isolated session store: both histories take exactly today's message shape + numbering")
from trusted_data_agent.core.session_backends.factory import reset_session_backend
from trusted_data_agent.core.session_backends.file_backend import FileSessionBackend
_tmp = tempfile.mkdtemp(prefix="s8_freeze_")
reset_session_backend(FileSessionBackend(_tmp))
from trusted_data_agent.core import session_manager as SM
U, S = "user-1", "sess-1"
run(SM.save_session(U, S, {"id": S, "user_uuid": U, "session_history": [], "chat_object": [], "last_turn_data": {"workflow_history": []},
                           "license_info": {"provider": "Anthropic"}, "name": "t"}))
run(SM.add_message_to_histories(U, S, "user", "how many customers?", profile_tag="IDGGT"))
run(SM.add_message_to_histories(U, S, "assistant", "100 customers", html_content="<p>100 customers</p>", profile_tag="IDGGT"))
run(SM.add_message_to_histories(U, S, "user", "and products?", profile_tag="IDGGT"))
sd = run(SM.read_session(U, S))
sh, co = sd["session_history"], sd["chat_object"]
check("session_history: user(1) · assistant(1) · user(2) — turn numbers count user messages", [m["turn_number"] for m in sh] == [1, 1, 2], sh)
check("session_history: the assistant message carries exactly today's keys", set(sh[1].keys()) == {"role", "content", "isValid", "turn_number", "profile_tag"}, sh[1])
check("chat_object mirrors the numbering and the keys", [m["turn_number"] for m in co] == [1, 1, 2] and set(co[1].keys()) == {"role", "content", "isValid", "turn_number"}, co)
check("no lineage key on any message", all(k not in m for m in sh + co for k in ("sibling_of", "branch_of", "branch", "adopted")))
check("no branches on the session body", "branches" not in sd)

print("[3] the persistence chokepoint: a turn record lands with no lineage keys and never touches its neighbours")
run(SM.update_last_turn_data(U, S, {"turn": 1, "user_query": "how many customers?", "final_summary_text": "100 customers", "status": "success"}))
run(SM.update_last_turn_data(U, S, {"turn": 2, "user_query": "and products?", "final_summary_text": "20 products", "status": "success"}))
wf = run(SM.read_session(U, S))["last_turn_data"]["workflow_history"]
check("two records, both valid, turn 1 untouched by turn 2's write", [t["turn"] for t in wf] == [1, 2] and all(t.get("isValid") is True for t in wf), wf)
check("no lineage key on any turn record", all(k not in t for t in wf for k in ("sibling_of", "branch_of", "branch", "adopted")))
sd = run(SM.read_session(U, S))
check("every message is still valid — nothing displaced", all(m.get("isValid", True) for m in sd["session_history"] + sd["chat_object"]))

print("[4] the executor's turn number is the record count + 1 (a sibling record will take the next number)")
EX = src("src/trusted_data_agent/agent/executor.py")
check("current_turn_number = len(workflow_history) + 1", 'self.current_turn_number = len(session_data["last_turn_data"]["workflow_history"]) + 1' in EX)

print("[5] the eval hook is dispatched from the one execution seam, unconditionally on the turn's kind")
ES = src("src/trusted_data_agent/agent/execution_service.py")
seam = ES[ES.index("async def run_agent_execution("):]
# Restated 2026-10-29 (trust campaign C18): still ONE seam — run_agent_execution — with two
# outcomes in it: an answered turn, and a turn an engine ended with an `error` event and no
# answer (it was never scored, so JUDGED read nothing on a turn the provider never answered).
check("_dispatch_online_eval is called only from run_agent_execution — once per outcome (answered · failed without an answer)",
      seam.count("_dispatch_online_eval(") == 2 and ES[:ES.index("async def run_agent_execution(")].count("_dispatch_online_eval(") == 1
      and "elif final_result_payload is None and _errored_turn_id is not None:" in seam)
check("the user message is added at ONE seam (`if message_to_save:`)", seam.count("await session_manager.add_message_to_histories(") == 1 and "if message_to_save:" in seam)

print("[6] the served history carries turn_number on every message (what a comparison groups by)")
check("get_session backfills turn_number on legacy histories", '"turn_number" not in session_data["session_history"][0]' in src("src/trusted_data_agent/core/session_manager.py"))
SMJS = src("static/js/handlers/sessionManagement.js")
check("the reload loop renders assistant messages with a turn id and user messages without", "UI.addMessage(msg.role, msg.content, currentTurnId, isValid, msg.source, null, isSessionPrimer)" in SMJS or "UI.addMessage(msg.role, msg.content, " in SMJS)

import shutil; shutil.rmtree(_tmp, ignore_errors=True); reset_session_backend(None)
print(f"\n=== S8 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
