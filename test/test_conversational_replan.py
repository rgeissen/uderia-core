"""OC-F13 — a data question answered with the planning prompt's greeting is re-planned.

FOUND LIVE (Optimize campaign, 2026-10-28/29, MiMo v2.6 Flash): the meta-planning prompt
offers `{"plan_type": "conversational", "response": "I'm doing well, thank you for asking!
How can I help you with your {mcp_system_name} today?"}` for greetings. The model copied it
for "How many customers do we have?" (A1) and "What is today's date according to the
database?" (E3) — 3 of 10 targeted runs — and the planner delivered the greeting as the
answer: no plan, no tool, no figure.

The rule (`conversational_guard.goal_is_small_talk`, `Planner._screen_conversational`): the
shortcut is accepted only when the QUESTION is small talk; otherwise the planner re-plans
once, priced like the first call, and the re-plan replaces the first answer if it parses.
CI-pure (a stub executor; no server, no LLM).
"""
import asyncio
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


from trusted_data_agent.agent.conversational_guard import goal_is_small_talk, REPLAN_NOTE  # noqa: E402
from trusted_data_agent.agent import planner as pl  # noqa: E402
from trusted_data_agent.agent import executor as ex  # noqa: E402

print("\n── §1 small talk keeps the shortcut")
for g in ("hello", "Hi!", "Hello, how are you today?", "thanks", "Thank you very much!",
          "good morning", "bye", "Thanks a lot, see you later", "hey there", "you're welcome",
          "Good evening, Uderia", "how's it going?", ""):
    check(f"small talk: {g!r}", goal_is_small_talk(g))

print("\n── §2 anything that asks for something is re-planned")
for g in ("How many customers do we have?",                       # A1
          "What is today's date according to the database?",      # E3
          "hello, how many customers are there?",
          "Thanks, now break it down by region",
          "thanks! and the top 5 products?",
          "yes", "ok", "sure, go ahead",                           # consent after a question
          "hi, list the tables",
          "What can you do?",
          "Hello " + "word " * 15):                                # long → not small talk
    check(f"not small talk: {g[:50]!r}", not goal_is_small_talk(g))

GREETING = '{"plan_type": "conversational", "response": "I\'m doing well, thank you for asking! How can I help you with your Teradata MCP today?"}'
PLAN = '[{"phase": 1, "goal": "Count customers", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}}, {"phase": 2, "goal": "report", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]'


def make(goal, replies):
    exe = ex.PlanExecutor.__new__(ex.PlanExecutor)
    exe.original_user_input = goal
    exe.active_prompt_name = None
    exe.source = "text"
    exe.execution_depth = 0
    exe.user_uuid, exe.session_id = "u", "s"
    exe.turn_input_tokens = exe.turn_output_tokens = 0
    exe._last_call_metadata = {"cost_usd": 0.0001}
    logged, prompts = [], []
    exe._log_system_event = lambda ev: logged.append(ev)
    exe._format_sse_with_depth = lambda ev, *a, **k: ev

    async def _call(prompt, **kw):
        prompts.append((prompt, kw))
        return replies.pop(0), 1000, 50
    exe._call_llm_and_update_tokens = _call
    p = pl.Planner.__new__(pl.Planner)
    p.executor = exe
    return p, logged, prompts


async def _nosession(*a, **k):
    return None
pl.session_manager.get_session = _nosession


def screen(p, first):
    async def go():
        evs = []
        async for e in p._screen_conversational(pl.robust_json_parse(first), first, "PLANNING PROMPT",
                                                "OpenRouter", "xiaomi/mimo"):
            evs.append(e)
        return evs
    return asyncio.run(go())


print("\n── §3 a greeting for a data question is re-planned once (A1, E3)")
p, logged, prompts = make("How many customers do we have?", [PLAN])
evs = screen(p, GREETING)
text, obj = p._screened
check("one more planning call", len(prompts) == 1, len(prompts))
check("…carrying the correction note and the question",
      prompts and prompts[0][0].startswith("PLANNING PROMPT") and "NOT small talk" in prompts[0][0]
      and "How many customers do we have?" in prompts[0][0])
check("…priced like the first: a logged 'Calling LLM for Planning' event with tokens and cost",
      any(e.get("step") == "Calling LLM for Planning" and e["details"].get("input_tokens") == 1000
          and "conversational re-plan" in e["details"].get("summary", "") for e in logged))
check("…the campaign registry's marker is on it",
      any("conversational re-plan" in str(e.get("details")) for e in logged))
check("the re-plan replaces the greeting", isinstance(obj, list) and obj[0]["relevant_tools"] == ["base_readQuery"]
      and text == PLAN)
check("a System Correction is announced", any(isinstance(e, dict) and e.get("step") == "System Correction" for e in evs))

print("\n── §4 genuine small talk keeps the shortcut, at no extra call")
p, logged, prompts = make("Hello, how are you today?", [])
screen(p, GREETING)
check("no re-plan", prompts == [] and logged == [])
check("…the greeting stands", p._screened[1].get("plan_type") == "conversational")

print("\n── §5 a non-conversational plan is untouched")
p, logged, prompts = make("How many customers do we have?", [])
screen(p, PLAN)
check("no call, plan unchanged", prompts == [] and p._screened == (PLAN, pl.robust_json_parse(PLAN)))

print("\n── §6 an unparseable re-plan keeps the first answer (never worse than before)")
p, logged, prompts = make("What is today's date according to the database?", ["not json at all"])
screen(p, GREETING)
check("one call, first answer kept", len(prompts) == 1 and p._screened[0] == GREETING)

print("\n── §7 wiring")
import inspect  # noqa: E402
src = inspect.getsource(pl.Planner._generate_meta_plan)
i_screen, i_conv = src.find("self._screen_conversational("), src.find("self.executor.is_conversational_plan = True")
check("the meta-planner screens the plan before accepting the conversational shortcut", 0 < i_screen < i_conv)
check("the parse-error retry shares the priced retry helper",
      'self._strategic_retry(retry_prompt, "parse-error retry"' in src)
check("the correction note formats with any question text", "{" not in REPLAN_NOTE.format(goal="a {b} c").replace("{b}", ""))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
