"""The persisted turn names — and the ledger prices — the model that SERVED the turn.

The defect (prod session 84399ee0, Oct 2026): an @TAG override ran the Optimize turn on
Google/gemini-3.1-flash-lite, but `restore_override_app_state` reset executor.current_* to the
default (Celeris) in the `finally` BEFORE the turn summary was built, so the turn recorded
Celeris and the consumption ledger re-priced the whole turn at Celeris rates ($0.003854) while the
accurate per-call sum (turn_cost) was $0.00497.

Also pins the ReAct tool listing: names only, because each listed tool is bound with its full
schema (descriptions were sent twice per ReAct step).

CI-pure. Run: python test/test_turn_served_model.py
"""
import os
import re
import sys
import types

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def src(rel):
    return open(os.path.join(ROOT, rel)).read()


from trusted_data_agent.agent.engines.optimize_engine import restore_override_app_state  # noqa: E402
from trusted_data_agent.agent.executor import PlanExecutor  # noqa: E402


def fake_executor(applied=True):
    ex = types.SimpleNamespace(
        profile_override_applied=applied,
        current_provider="Google", current_model="gemini-3.1-flash-lite",   # the override's model
        original_provider=None, original_model=None, original_provider_details={},
        user_uuid="u-test", turn_served_provider=None, turn_served_model=None,
    )
    ex.served_provider_model = types.MethodType(PlanExecutor.served_provider_model, ex)
    return ex


print("\n[1] the revert snapshots the serving model before restoring the default")
ex = fake_executor()
# original_* left None so no user-keyed setters run (pure); emulate the reset they precede:
restore_override_app_state(ex)
ex.current_provider, ex.current_model = "Celeris", "celeris-1"   # what the revert restores
check("snapshot holds the override's provider", ex.turn_served_provider == "Google", ex.turn_served_provider)
check("snapshot holds the override's model", ex.turn_served_model == "gemini-3.1-flash-lite", ex.turn_served_model)
check("served_provider_model() returns the serving model, not the restored default",
      ex.served_provider_model() == ("Google", "gemini-3.1-flash-lite"), ex.served_provider_model())

print("\n[2] a second (finally safety-net) call does not overwrite the snapshot")
ex.profile_override_applied = True     # even if re-armed, the first snapshot wins
restore_override_app_state(ex)
check("snapshot unchanged", ex.served_provider_model() == ("Google", "gemini-3.1-flash-lite"))

print("\n[3] no override → no snapshot → current_* is the truth (baseline unchanged)")
ex2 = fake_executor(applied=False)
restore_override_app_state(ex2)
check("no snapshot taken", ex2.turn_served_provider is None)
check("served = current", ex2.served_provider_model() == ("Google", "gemini-3.1-flash-lite"))

print("\n[4] persistence sites read the serving model")
opt = src("src/trusted_data_agent/agent/engines/optimize_engine.py")
m = re.search(r"turn_summary = \{.*?\"turn_input_tokens\"", opt, re.S)
check("Optimize turn summary uses served_provider_model()",
      m is not None and "served_provider_model()" in m.group(0) and '"provider": executor.current_provider' not in m.group(0))
exe = src("src/trusted_data_agent/agent/executor.py")
m2 = re.search(r"\"final_summary\": None,  # No final summary for failed turns.*?\"profile_tag\"", exe, re.S)
check("error/partial turn save uses served_provider_model()", m2 is not None and "served_provider_model()" in m2.group(0))

print("\n[5] the consumption ledger uses the accurate turn cost")
sm = src("src/trusted_data_agent/core/session_manager.py")
check("ledger captures turn_cost", '"turn_cost": turn_data.get(\'turn_cost\')' in sm)
blk = sm[sm.find('_tc = _consumption_data.get("turn_cost")'):]
blk = blk[:blk.find("manager.record_turn(")]
check("ledger prefers turn_cost when it is a positive number", "_tc > 0" in blk and "cost_usd, _cost_src = float(_tc)" in blk, blk[:300])
check("ledger falls back to re-pricing when turn_cost is absent/zero", "calculate_cost(" in blk)
check("bool is never read as a cost", "not isinstance(_tc, bool)" in blk)

print("\n[6] ReAct tool listing: name AND description (a names-only listing cut Celeris to 0/9 tool calls)")
ca = src("src/trusted_data_agent/agent/conversation_agent.py")
lsp = ca[ca.find("def _load_system_prompt"):ca.find("def _get_default_prompt")]
check("tool descriptions are in the system-prompt listing", 'f"- {tool.name}: {tool.description}"' in lsp)
check("no names-only listing survives", 'f"- {tool.name}"\n' not in lsp)
check("held-back note survives (tool surface)", "more tools are held back for this turn" in lsp)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
