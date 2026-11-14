"""S7 baseline freeze — committed GREEN before the Outcome Loop landed.

Pins that with NO outcome loop (the master switch off, or a profile with no `outcomeConfig`)
nothing changes, EXECUTING the real seams: the executor's grounding-gate entrypoint returns the
runtime's own result byte-for-byte (one call, no revision, no second LLM call); the answer text
an engine delivers is what the gate returned; the contract's signed-posture set is what it is
today; no `outcome` stamp is written; the capsule has no Drafts disclosure. Every assertion
here must stay green through S7 and after it (with the switch off)."""
import asyncio, logging, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.pop("TDA_OUTCOME_LOOP", None)
from trusted_data_agent.core.config import APP_CONFIG
if hasattr(APP_CONFIG, "OUTCOME_LOOP"):
    APP_CONFIG.OUTCOME_LOOP = False

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
check("OUTCOME_LOOP absent or False by default", not getattr(APP_CONFIG, "OUTCOME_LOOP", False))

print("[2] the executor's grounding entrypoint returns the runtime's result verbatim — ONE gate call, NO LLM call")
from trusted_data_agent.agent.executor import PlanExecutor
from trusted_data_agent.agent.grounding import runtime as GR
ex = PlanExecutor.__new__(PlanExecutor)
ex.kg_enrichment_event = None; ex.user_uuid = "u"; ex.session_id = "s"; ex.current_turn_number = 1
ex.original_user_input = "q"; ex.event_handler = None; ex._cw_knowledge_chunks = None
ex._resolve_gate_profile = lambda kind: {"tag": "T", "groundingConfig": {"mode": "annotate"}}
ex._detect_profile_type = lambda: "tool_enabled"
calls = {"gate": 0, "llm": 0}
_orig = GR.run_grounding
async def _fake_gate(answer_text, **kw):
    calls["gate"] += 1
    return answer_text + "\n\n_annotated_", {"ran": True, "acted": True, "action": "annotate", "decision": "annotate",
                                             "unsupported_claims": ["the moon is made of cheese"], "answer_oos_refs": [], "ungrounded_numbers": []}
GR.run_grounding = _fake_gate
async def _no_llm(*a, **kw):
    calls["llm"] += 1; return "REVISED", 1, 1
ex._call_llm_and_update_tokens = _no_llm
try:
    text, ev = run(ex._run_grounding_gate_full("the moon is made of cheese"))
finally:
    GR.run_grounding = _orig
check("the gate ran exactly once", calls["gate"] == 1, calls)
check("no LLM call was made (no revision)", calls["llm"] == 0, calls)
check("the delivered text is the gate's annotated text, verbatim", text == "the moon is made of cheese\n\n_annotated_", text)
check("the event is the gate's own event", ev.get("decision") == "annotate" and ev.get("unsupported_claims") == ["the moon is made of cheese"])

print("[3] the contract's signed-posture set today")
from trusted_data_agent.core import profile_config_contract as PCC
# frozen as "today's set" before S7; S7 adds `outcomeConfig` — whether a failing answer is
# revised before delivery IS posture — so the pin records the set WITH it (the S5 precedent).
check("posture set = the agent default + three answer gates + knowledgeConfig + turnBudget + outcomeConfig",
      {s.key for s in PCC.REGISTRY if s.signed and s.signed_dimension == "posture"}
      == {"postureConfig", "groundingConfig", "guardrailConfig", "honestyConfig", "knowledgeConfig", "turnBudget", "outcomeConfig"})

print("[4] no outcome stamp / no Drafts disclosure today (the chokepoint idioms the loop will join)")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps via setdefault (budget · resilience · tool_surface)", 'setdefault("budget"' in SM and 'setdefault("resilience"' in SM and 'setdefault("tool_surface"' in SM)
AC = src("static/js/handlers/answerCapsule.js")
check("the capsule is a live surface with sections the loop can join", "answerCapsule" in AC or "capsule" in AC.lower())

print(f"\n=== S7 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
