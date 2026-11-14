"""A Focus answer delivered when nothing in the repositories matched still runs the trust tail.

FOUND LIVE (trust campaign, 2026-10-29, Focus · S4): a question the corpus could not answer took
the engine's "No relevant knowledge found" early return. The delivered answer carried no provenance
seal (I7: chain tip ABSENT), no integrity stamp at answer time (I8: ABSENT) and no outbound screen
(GUARDED read `na`) — an answer outside the trust tail, on every Focus question the corpus does not
cover. The early return now runs the same three steps as the normal tail: the outbound guard, the
per-turn assurance stamps (the integrity fact), the sealed chain. CI-pure (source order).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


src = open(os.path.join(HERE, "..", "src", "trusted_data_agent", "agent", "engines", "focus_engine.py")).read()
a = src.find("if not all_results and not answer_from_attachments:")
b = src.find("await session_manager.update_last_turn_data(", a)
block = src[a:b]
check("the no-knowledge early return exists", a > 0 and b > a)
check("it screens the answer with the outbound guard", "executor._run_guardrail_outbound(no_results_text)" in block)
check("it seals the provenance chain", 'executor.provenance.add_step("turn_complete"' in block and "executor.provenance.finalize()" in block)
check("its turn record carries the guard verdicts", '"guardrail": _nr_guardrail_event' in block and '"guardrail_inbound":' in block)
check("…the per-turn assurance stamps (the integrity fact at answer time)", "**_assurance_stamps()," in block)
check("…and the seal", "turn_summary.update(_nr_provenance)" in block)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
