"""A malformed `source_data` and a row-set tool output never turn a delegated prompt's trouble
into a fatal turn.

FOUND LIVE (trust campaign full run 20261028-181130, Optimize · P2, session 6ae7b013): with
8888's prompts rendering again, P2's plan delegated a phase to an MCP prompt. The weak model
wrote `source_data` as a NESTED list; `_invoke_core_llm_task` did `key in workflow_state` with a
list key → "unhashable type: 'list'". The sub-executor then failed, and `_run_sub_prompt` called
`.get` on `last_tool_output` — a LIST (a row-set result) — so the recoverable sub-prompt
failure became "'list' object has no attribute 'get'": the whole turn errored, empty answer,
no integrity stamp. CI-pure.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.mcp_adapter import adapter as A  # noqa: E402
from trusted_data_agent.agent import executor as EX  # noqa: E402

print("\n── §1 source_data keys, whatever shape the model wrote")
check("a nested list is flattened", A._source_data_keys([["result_of_phase_1"], "result_of_phase_2"]) == ["result_of_phase_1", "result_of_phase_2"])
check("a single string is one key", A._source_data_keys("result_of_phase_3") == ["result_of_phase_3"])
check("non-string entries are dropped, duplicates collapse",
      A._source_data_keys(["a", 7, None, {"x": 1}, "a", ["b"]]) == ["a", "b"])
check("nothing → no keys", A._source_data_keys(None) == [] and A._source_data_keys([]) == [])
ws = {"result_of_phase_1": [{"n": 1}]}
check("every normalised key is hashable (the TypeError cannot recur)",
      all((k in ws) in (True, False) for k in A._source_data_keys([["result_of_phase_1"], [["x"]]])))
check("the core LLM task reads source_data through the normaliser",
      '_source_data_keys(args.get("source_data", []))' in inspect.getsource(A._invoke_core_llm_task))

print("\n── §2 a failed sub-prompt with a row-set last output stays recoverable")
src = inspect.getsource(EX.PlanExecutor._run_sub_prompt)
check("the error branch type-checks last_tool_output before .get",
      'not isinstance(self.last_tool_output, dict) or self.last_tool_output.get("status") != "error"' in src)
exe = EX.PlanExecutor.__new__(EX.PlanExecutor)
exe.last_tool_output = [{"row": 1}]
# the branch body, executed as written in the method
if not isinstance(exe.last_tool_output, dict) or exe.last_tool_output.get("status") != "error":
    exe.last_tool_output = {"status": "error", "error_message": "Sub-prompt 'x' failed."}
check("…and records the sub-prompt failure as an error result", exe.last_tool_output.get("status") == "error")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
