"""A delegated prompt's usage is MERGED into the turn — a sibling phase's concurrent call is never lost.

FOUND LIVE (Optimize campaign run 4, 2026-10-27, invariant O6 price honesty): on two turns the
persisted `turn_cost` / `turn_input_tokens` were lower than the sum of the priced LLM calls the
trace itself recorded — by exactly ONE call each (a tactical call of 8,211 tokens; a
self-correction of 19,387). Both turns ran parallel phases, and one phase's self-correction
switched to a prompt: `_run_sub_prompt` copied the parent's running totals INTO the
sub-executor and wrote the sub-executor's totals BACK over the parent's when it finished. The
sibling phase's call, accumulated on the parent while the sub-executor ran, was overwritten — a
lost update, so the turn's recorded price under-stated what the providers billed.

The rule: the sub-executor's totals are merged as a DELTA over the values it started from
(`PlanExecutor._merge_sub_totals`), never assigned. CI-pure.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


from trusted_data_agent.agent import executor as ex  # noqa: E402


def mk(i=0, o=0, c=0.0):
    e = ex.PlanExecutor.__new__(ex.PlanExecutor)
    e.turn_input_tokens, e.turn_output_tokens, e.turn_cost_usd = i, o, c
    return e


print("\n── §1 the merge is a delta over the sub-executor's starting point")
parent = mk(1000, 100, 0.10)
sub = mk(1000, 100, 0.10)                      # inherited at spawn
base = ex.PlanExecutor._sub_totals_base(sub)
sub.turn_input_tokens += 500; sub.turn_output_tokens += 50; sub.turn_cost_usd += 0.05   # the sub's own calls
parent.turn_input_tokens += 8211; parent.turn_output_tokens += 30; parent.turn_cost_usd += 0.0008  # a SIBLING phase, concurrently
parent._merge_sub_totals(sub, base)
check("the sibling's concurrent call survives the merge (input)", parent.turn_input_tokens == 1000 + 8211 + 500, parent.turn_input_tokens)
check("…output", parent.turn_output_tokens == 100 + 30 + 50, parent.turn_output_tokens)
check("…cost", abs(parent.turn_cost_usd - (0.10 + 0.0008 + 0.05)) < 1e-12, parent.turn_cost_usd)

print("\n── §2 the live shape: two concurrent tasks, one of them running a sub-executor")


async def scenario():
    p = mk(0, 0, 0.0)

    async def phase_with_sub_prompt():
        s = mk(p.turn_input_tokens, p.turn_output_tokens, p.turn_cost_usd)
        b = ex.PlanExecutor._sub_totals_base(s)
        await asyncio.sleep(0.01)
        s.turn_input_tokens += 18751; s.turn_cost_usd += 0.0019     # the sub's work
        await asyncio.sleep(0.02)
        p._merge_sub_totals(s, b)

    async def sibling_phase():
        await asyncio.sleep(0.015)
        p.turn_input_tokens += 8211; p.turn_cost_usd += 0.00084       # lands while the sub runs

    await asyncio.gather(phase_with_sub_prompt(), sibling_phase())
    return p

p = asyncio.run(scenario())
check("both calls are in the turn's tokens", p.turn_input_tokens == 18751 + 8211, p.turn_input_tokens)
check("both calls are in the turn's cost", abs(p.turn_cost_usd - (0.0019 + 0.00084)) < 1e-12, p.turn_cost_usd)

print("\n── §3 _run_sub_prompt merges, never assigns")
src = inspect.getsource(ex.PlanExecutor._run_sub_prompt)
check("no copy-back assignment of the sub's totals",
      "self.turn_input_tokens = sub_executor.turn_input_tokens" not in src
      and "self.turn_cost_usd = sub_executor.turn_cost_usd" not in src)
check("the merge helper is used", "_merge_sub_totals(sub_executor" in src and "_sub_totals_base(sub_executor)" in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
