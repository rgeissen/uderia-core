"""OC-F15 — a final report with no JSON is repeated once, never routed through an invented correction.

FOUND LIVE (Optimize campaign, 2026-10-29, MiMo v2.6 Flash, A1 "How many customers do we
have?"): twice in 20 turns the report call answered "Could not extract a canonical answer." —
no JSON at all. The adapter returned "Failed to generate valid report JSON"; the generic
correction's "text sanitization" LLM call then proposed `synthesized_answer` = the QUESTION as
the tool's arguments, and the report ran a second time. Two extra LLM calls, two
`TDA_FinalReport` entries in the trace (O1), and a correction that invented its input.

The rule (`adapter.report_needs_format_retry`, `_invoke_final_report_task`): a response with no
JSON object or list is repeated ONCE inside the tool with the format restated; both calls' tokens
are returned as the tool's (one priced event — O6). A response WITH JSON that fails validation is
not this case (the coercion and the fallback extractor own it). CI-pure (a stub LLM handler).
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


from trusted_data_agent.mcp_adapter import adapter as A  # noqa: E402

LIVE = "Could not extract a canonical answer.\n\n_finished within budget — 0 of 12 steps used · ~14k of ~150k tokens used_"
GOOD = '{"direct_answer": "You have 100 customers.", "key_metric": null, "key_observations": []}'

print("\n── §1 which responses a format retry can fix")
check("the live response (prose, no JSON) needs it", A.report_needs_format_retry(LIVE))
check("an empty response needs it", A.report_needs_format_retry("") and A.report_needs_format_retry(None))
check("a JSON object does not", not A.report_needs_format_retry(GOOD))
check("a fenced JSON block does not", not A.report_needs_format_retry("```json\n" + GOOD + "\n```"))
check("JSON with wrong field names does not (the fallback extractor owns it)",
      not A.report_needs_format_retry('{"dirct_anser": "You have 100 customers."}'))


def run(replies):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return replies.pop(0), 600, 50, None, None
    orig = A.llm_handler.call_llm_api
    A.llm_handler.call_llm_api = fake
    try:
        STATE = {"llm": "L", "llm_provider": "OpenRouter", "llm_model": "m"}
        ws = {"original_user_input": "How many customers do we have?",
              "workflow_goal_prompt": "How many customers do we have?",
              "phase_1": [{"status": "success", "results": [{"customer_count": 100}]}]}
        res, tin, tout = asyncio.run(A._invoke_final_report_task(
            STATE, {"tool_name": "TDA_FinalReport", "arguments": {}}, ws, user_uuid=None, session_id=None))
    finally:
        A.llm_handler.call_llm_api = orig
    return res, tin, tout, calls


print("\n── §2 the live shape: repeated once, inside the tool")
res, tin, tout, calls = run([LIVE, GOOD])
check("the report succeeds", res.get("status") == "success", res)
check("…with the answer from the repeated call", res["results"][0]["direct_answer"] == "You have 100 customers.")
check("exactly one repeat", len(calls) == 2, len(calls))
check("…its prompt restates the format", "contained no JSON object" in calls[1]["prompt"]
      and calls[1]["prompt"].startswith(calls[0]["prompt"]))
check("…on the same model and instance as the first call",
      all(calls[1][k] == calls[0][k] for k in ("llm_instance", "current_provider", "current_model", "user_uuid")))
check("both calls' tokens are the tool's (one priced event, O6)", (tin, tout) == (1200, 100), (tin, tout))

print("\n── §3 a good first response is not repeated")
res, tin, tout, calls = run([GOOD])
check("one call, tokens unchanged", len(calls) == 1 and (tin, tout) == (600, 50))

print("\n── §4 a repeat that also fails ends as before (an error, no third call)")
res, tin, tout, calls = run([LIVE, "Still no JSON."])
check("two calls only", len(calls) == 2)
check("…the tool reports the error the correction chain expects", res.get("status") == "error"
      and res.get("error_message") == "Failed to generate valid report JSON.")
check("…and both calls are still priced", (tin, tout) == (1200, 100))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
