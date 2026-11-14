"""OC-F16 — an empty or truncated strategic plan is retried, not fatal.

FOUND LIVE (Optimize campaign, 2026-10-29, MiMo v2.6 Flash, E3 "What is today's date according
to the database?"): the planning call billed 51 output tokens and delivered two characters,
`[{`. A guard that predates the parse-error retry raised "LLM returned an empty or invalid
response" for any response under three characters, so the turn ended as an unrecoverable error —
no answer — while the retry written for exactly this case sat on the next lines.

The rule (`planner.parse_plan_response`): an empty or truncated response is "no plan", like any
unparseable text — it goes to the one parse-error retry; only a retry that also yields no plan
ends the turn. CI-pure.
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


from trusted_data_agent.agent import planner as pl  # noqa: E402

print("\n── §1 what counts as no plan")
for t in ("[{", "", "  ", None, "{}x"[:2]):
    check(f"no plan: {t!r}", pl.parse_plan_response(t) is None)
check("a real plan parses", isinstance(pl.parse_plan_response('[{"phase": 1, "goal": "g", "relevant_tools": ["x"]}]'), list))
check("a conversational object parses", pl.parse_plan_response('{"plan_type": "conversational", "response": "hi"}') == {"plan_type": "conversational", "response": "hi"})

print("\n── §2 the meta-planner retries instead of failing")
src = inspect.getsource(pl.Planner._generate_meta_plan)
check("the early fatal raise for a short response is gone", "LLM returned an empty or invalid response" not in src)
i_first = src.find("plan_object = parse_plan_response(response_text)")
i_retry = src.find('self._strategic_retry(retry_prompt, "parse-error retry"')
i_fatal = src.find("Failed to generate a valid meta-plan after 2 attempts")
check("first parse → parse-error retry → fatal only after the retry", 0 < i_first < i_retry < i_fatal, (i_first, i_retry, i_fatal))
check("the retry's answer is read through the same rule", src.count("parse_plan_response(response_text)") == 2)

# FOUND (2026-10-29, the prompt-cache measurement): both retries (parse-error, OC-F13 re-plan)
# sent the FULL master system prompt with its own capability catalog — 24.6K tokens where the
# first attempt sends 2.3K — because only the first attempt passed strip_capabilities.
_retry_src = inspect.getsource(pl.Planner._strategic_retry)
check("a retry strips the duplicate catalog like the first attempt (24.6K → 2.3K system prompt)",
      "strip_capabilities=True" in _retry_src)
check("a retry shares the first attempt's stable prefix (the cache plan)",
      'cache_prefix=getattr(self, "_planning_cache_prefix", None)' in _retry_src and "self._planning_cache_prefix = planning_cache_prefix" in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
