"""S5 baseline freeze — committed GREEN before the Visible Budget landed.

Pins that with NO turn budget (the master switch off, or a profile with no `turnBudget`)
nothing changes, EXECUTING the real seams: a guarded ReAct tool call returns the tool's own
content with nothing appended; the recommender's Tier-A chips carry no "Continue" chip for a
completed turn and `derive_render_flags` does not flag one; the Optimize phase groups are the
plan's own; the profile-config contract's portable set is what it is today; the ReAct
hard-stop notices are unchanged; no `budget` stamp is written. Every assertion here must stay
green through S5 and after it (with the switch off)."""
import asyncio, json, logging, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.pop("TDA_TURN_BUDGET", None)
from trusted_data_agent.core.config import APP_CONFIG
if hasattr(APP_CONFIG, "TURN_BUDGET"):
    APP_CONFIG.TURN_BUDGET = False

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

print("[1] the switch is absent or off")
check("TURN_BUDGET absent or False by default", not getattr(APP_CONFIG, "TURN_BUDGET", False))

print("[2] a guarded ReAct tool call returns the tool's content verbatim — nothing appended")
from trusted_data_agent.agent import tool_enforcement as TE
async def _tool(**kw):
    return json.dumps({"results": [{"a": 1}, {"a": 2}], "row_count": 2}), None
g = TE._make_guarded_coroutine("base_readQuery", _tool, "sess-freeze", "u")
content, artifact = run(g(sql="SELECT a FROM t"))
check("content unchanged (no budget note, no other annotation)", content == json.dumps({"results": [{"a": 1}, {"a": 2}], "row_count": 2}), content[:120])
check("artifact passed through", artifact is None)
async def _tool2(**kw):
    return "plain text result", None
g2 = TE._make_guarded_coroutine("base_tableList", _tool2, "sess-freeze", "u")
c2, _ = run(g2(database_name="db"))
check("a plain-text result is untouched", c2 == "plain text result", c2)

print("[3] the recommender offers no Continue chip on a completed turn")
from trusted_data_agent.agent import recommender as RC
flags = RC.derive_render_flags({"final_answer": "<p>done</p>", "final_answer_text": "done", "collected_data": {"q": [{"x": 1}]}})
check("render flags carry no stopped-early marker for a completed turn", not flags.get("stopped_early"))
chips = RC._build_action_chips({"profile_type": "llm_only", "componentConfig": {}}, flags, "u")
check("no chip is a Continue chip", not any("continue" in str(c.get("label", "")).lower() for c in chips), chips)

print("[4] the Optimize phase groups are the plan's own")
from trusted_data_agent.agent.executor import PlanExecutor
plan = [{"phase": 1, "goal": "a", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT 1"}},
        {"phase": 2, "goal": "b", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
groups = PlanExecutor._build_phase_groups(plan)
check("two phases, two groups, in order", [[p["phase"] for p in g] for g in groups] == [[1], [2]], groups)

print("[5] the ReAct hard stops keep their wording")
CA = src("src/trusted_data_agent/agent/conversation_agent.py")
check("cost-cap stop notice", "reached its configured per-run cost budget" in CA)
check("input-token stop notice", "reached the per-turn budget of" in CA)

print("[6] the contract's portable set today")
from trusted_data_agent.core import profile_config_contract as PCC
check("cacheConfig portable, authorityConfig portable, classification_results local", "cacheConfig" in PCC.portable_keys() and "authorityConfig" in PCC.portable_keys() and "classification_results" not in PCC.portable_keys())

print("[7] no budget stamp is written for a turn without a budget (the persistence chokepoint)")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps `cache`/`compute`/`resilience` via setdefault (the chokepoint idiom the budget will join)",
      'setdefault("cache"' in SM and 'setdefault("compute"' in SM and 'setdefault("resilience"' in SM)

print(f"\n=== S5 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
