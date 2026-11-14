"""
The answer is SAVED after every step that may change it — in every engine.

The session history is what a reload renders (``html_content``) and what the next turn's
context reads (``chat_object`` ``content``). A save that runs BEFORE a gate keeps the text
the gate changed: a grounding withhold reappears on reload, a guardrail-redacted value stays
in the next turn's context, a disclosure or note shows live and vanishes on reload. The live
stream carries the gated answer either way, which is why the defect is invisible while
watching a turn.

Found Oct 2026 in the Coordinate (genie) engine — it saved the coordinator's answer right
after synthesis, ahead of its grounding, guardrail and citation gates; the four other
engines already saved after theirs. This gate states the rule for all five, so a sixth
engine or a reordered one fails by name.

Rule, per engine: between the point the answer is produced and the last step that may
change it, nothing is saved; and the answer is saved after that last step.

Run: python test/test_answer_saved_after_gates.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PASS = FAIL = 0
SAVE = "add_message_to_histories("


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail is not None else ""))


# engine → (file, the answer is produced, [the steps that may change it, in order])
ENGINES = {
    "Optimize (tool_enabled)": ("src/trusted_data_agent/agent/executor.py",
        "self.final_summary_text = clean_summary_for_llm",
        ["self._run_grounding_gate_full(", "self._run_guardrail_outbound(self.final_summary_text)",
         "self._run_honesty_gate(", "self._run_citation_gate(", "self._run_compute_lineage(",
         "self._apply_turn_budget_finish(self.final_summary_text", "self._apply_model_gap(self.final_summary_text"]),
    "Ideate (direct)": ("src/trusted_data_agent/agent/engines/ideate_engine.py",
        "response_text, input_tokens, output_tokens = await executor._call_llm_and_update_tokens(",
        ["executor._run_grounding_gate_full(", "executor._run_guardrail_outbound(response_text)",
         "executor._run_honesty_gate_rag(response_text)", "executor._apply_model_gap(response_text, final_html)"]),
    "Ideate + tools (conversation)": ("src/trusted_data_agent/agent/engines/conversation_engine.py",
        'response_text = result.get("response", "")',
        ["executor._run_grounding_gate_full(", "executor._run_guardrail_outbound(response_text)",
         "executor._run_honesty_gate(", "executor._run_citation_gate(", "executor._run_compute_lineage(response_text",
         "executor._apply_turn_budget_finish(response_text", "executor._apply_model_gap(response_text, final_html)"]),
    "Focus (rag_focused)": ("src/trusted_data_agent/agent/engines/focus_engine.py",
        'response_text = agent_result.get("response", "")',
        ["executor._run_grounding_gate_full(", "executor._run_guardrail_outbound(response_text)",
         "executor._run_honesty_gate_rag(", "executor._run_citation_gate(", "executor._apply_model_gap(response_text, final_html)"]),
    "Coordinate (genie)": ("src/trusted_data_agent/agent/engines/coordinate_engine.py",
        "result = await coordinator.execute(",
        ["_grounding_runtime.run_grounding(", "_oloop.run_loop(", "_guardrail_runtime.scan_outbound(",
         "_cit_runtime.run_honesty_citation(", "_model_gap.apply("]),
}

for engine, (path, produced, steps) in ENGINES.items():
    print(f"[{engine}]")
    src = open(os.path.join(ROOT, path)).read()
    p = src.find(produced)
    check("the answer's production point is found (the gate reads the code it claims to)", p >= 0, produced)
    if p < 0:
        continue
    pos = {}
    for s in steps:
        i = src.find(s, p)
        pos[s] = i
        check(f"step present after production: {s[:60]}", i > p, i)
    found = [i for i in pos.values() if i > p]
    if not found:
        continue
    last = max(found)
    first = min(found)
    early = src.find(SAVE, p)
    check("nothing is saved between producing the answer and its last changing step",
          early < 0 or early > last, {"save_at": early, "last_step_at": last, "first_step_at": first})
    after = src.find(SAVE, last)
    check("the answer is saved after its last changing step", after > last, after)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
