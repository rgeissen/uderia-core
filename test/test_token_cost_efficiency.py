"""
§4 Token / cost efficiency — regression gate for the six items of
docs/assessments/FUSION_OPTIMIZER_DEEP_ASSESSMENT_2026-07.md (plan: TOKEN_COST_EFFICIENCY_PLAN.md):

  1. compact correction catalog          (build_compact_tools_and_prompts_context)
  2. parse-retry emits a cost event       (planner strategic retry)
  3. global tool-constraints cache        (_get_tool_constraints L2)
  4. lazy legacy-history summary           (planner _generate_meta_plan)
  5. drop deepcopy before distillation     (_distill_data_for_llm_context is non-mutating)
  6. plan-gate outcome telemetry           (_record_plan_gate_outcome)

CI-pure: no server, no DB, no network. Functional for the pure helpers (1/3/5/6); scoped
comment-stripped source assertions for 2/4 (they live inside large I/O-bound async generators).

Run:  PYTHONPATH=src python3 test/test_token_cost_efficiency.py
"""
import asyncio
import copy
import sys
import types
from pathlib import Path

import trusted_data_agent.core.config as _cfg
from trusted_data_agent.agent import executor as ex
from trusted_data_agent.agent.executor import (
    PlanExecutor,
    build_compact_tools_and_prompts_context,
    rebuild_tools_and_prompts_context,
)
from trusted_data_agent.agent.engines import optimize_engine as oe

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


_SRC = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent"


def _code_lines(rel):
    text = (_SRC / rel).read_text()
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


class _FakeTool:
    def __init__(self, name, description, args):
        self.name = name
        self.description = description
        self.args = args  # {arg: {type, required, description}}


# Shared fake catalog
_TOOLS_META = {
    "Data": [{"name": "base_readQuery", "disabled": False}, {"name": "base_tableList", "disabled": False}],
    "Skip": [{"name": "disabled_tool", "disabled": True}],
}
_MCP = {
    "base_readQuery": _FakeTool("base_readQuery", "Run a read-only SQL query.",
                                {"sql": {"type": "string", "required": True, "description": "the SQL text to run — can be very long"}}),
    "base_tableList": _FakeTool("base_tableList", "List tables in a database.",
                                {"database_name": {"type": "string", "required": True, "description": "the database"},
                                 "scope": {"type": "string", "required": False, "description": "all or user"}}),
    "disabled_tool": _FakeTool("disabled_tool", "should not appear", {}),
}
_PROMPTS = {
    "Reports": [{"name": "qlty_databaseQuality", "description": "Assess DB quality.",
                 "arguments": [{"name": "database_name", "type": "string", "required": True, "description": "the db"}],
                 "disabled": False}],
}


# ============================================================ 1 — compact correction catalog
print("\n[1] compact correction catalog: names + description + arg-names, no per-arg schema, smaller")
ctools, cprompts = build_compact_tools_and_prompts_context(_TOOLS_META, _MCP, _PROMPTS)
check("every enabled tool name present", "base_readQuery" in ctools and "base_tableList" in ctools)
check("disabled tool excluded", "disabled_tool" not in ctools)
check("descriptions preserved (switch-discoverability)", "Run a read-only SQL query." in ctools)
check("arg NAMES present with * for required", "[args: sql*]" in ctools and "database_name*, scope" in ctools)
check("per-arg type/description lines dropped (the size win)",
      "(string, required):" not in ctools and "the SQL text to run" not in ctools)
check("prompt name + arg-names present", "qlty_databaseQuality" in cprompts and "[args: database_name*]" in cprompts)
# materially smaller than the full catalog for the same data (via APP_STATE)
_cfg.APP_STATE["structured_tools"] = _TOOLS_META
_cfg.APP_STATE["mcp_tools"] = _MCP
_cfg.APP_STATE["structured_prompts"] = _PROMPTS
ftools, fprompts = rebuild_tools_and_prompts_context()
check("compact tools catalog is smaller than the full one", len(ctools) < len(ftools))
check("full catalog DOES carry the verbose per-arg lines (proves the win is real)",
      "(string, required):" in ftools)


# ============================================================ 3 — global tool-constraints cache
print("\n[3] tool-constraints classification cached process-globally (not re-paid per turn/sub-executor)")
ex._TOOL_CONSTRAINTS_CACHE.clear()


def _fake_exec(state_tools):
    async def _no_llm(**kw):
        raise AssertionError("LLM called (cache miss re-paid the classification)")
    return types.SimpleNamespace(
        tool_constraints_cache={},
        dependencies={"STATE": {"mcp_tools": state_tools}},
        _call_llm_and_update_tokens=_no_llm,
        _format_sse_with_depth=lambda d: d,  # passthrough so a MISS reaches the LLM stub
        source="text",
    )

_tool = _FakeTool("univariateStatistics", "Quantitative stats over a numeric column.", {})
# Pre-seed the global cache as if a prior turn already classified it.
ex._TOOL_CONSTRAINTS_CACHE[("univariateStatistics", _tool.description)] = {"dataType": "numeric"}

e1 = _fake_exec({"univariateStatistics": _tool})
c1, ev1 = run(PlanExecutor._get_tool_constraints(e1, "univariateStatistics"))
check("L2 hit returns the cached classification (no LLM call)", c1 == {"dataType": "numeric"})
check("L2 hit emits no cost events", ev1 == [])
check("L1 populated from L2 for the rest of the turn", e1.tool_constraints_cache["univariateStatistics"] == {"dataType": "numeric"})

# a SECOND, independent executor (fresh per-turn L1) still hits the process-global L2 → no re-pay
e2 = _fake_exec({"univariateStatistics": _tool})
c2, ev2 = run(PlanExecutor._get_tool_constraints(e2, "univariateStatistics"))
check("a different executor (next turn / sub-executor) also hits L2 — no re-pay", c2 == {"dataType": "numeric"})

# a tool with the SAME name but a DIFFERENT description is a distinct entry (key captures inputs)
_tool_v2 = _FakeTool("univariateStatistics", "A totally different description.", {})
e3 = _fake_exec({"univariateStatistics": _tool_v2})
raised = False
try:
    run(PlanExecutor._get_tool_constraints(e3, "univariateStatistics"))
except AssertionError:
    raised = True  # correctly a MISS → would call the (stub) LLM
check("distinct (name, description) is a cache MISS (not a stale hit)", raised)


# ============================================================ 5 — distiller non-mutating (deepcopy dropped)
print("\n[5] _distill_data_for_llm_context is non-mutating → the per-phase deepcopy was pure waste")
_de = types.SimpleNamespace(_context_distiller=None)
_de._distill_data_for_llm_context = types.MethodType(PlanExecutor._distill_data_for_llm_context, _de)
big = {"phase_1": {"results": [{"a": i, "b": i * 2} for i in range(5000)], "metadata": {"src": "x"}},
       "note": "keep me", "nested": {"small": [1, 2, 3]}}
before = copy.deepcopy(big)
out = _de._distill_data_for_llm_context(big)
check("input workflow_state is byte-identical after distillation (no mutation)", big == before)
check("output distilled the large result set (summary, not 5000 rows)",
      isinstance(out["phase_1"], dict) and "comment" in out["phase_1"] and out["phase_1"]["metadata"]["row_count"] == 5000)
check("output preserves small/scalar branches", out["note"] == "keep me" and out["nested"]["small"] == [1, 2, 3])
check("output is a NEW object (not the same dict)", out is not big)


# ============================================================ 6 — plan-gate telemetry
print("\n[6] plan-gate outcome telemetry counters")
for k in oe._PLAN_GATE_STATS:
    oe._PLAN_GATE_STATS[k] = 0
oe._record_plan_gate_outcome("simple")
oe._record_plan_gate_outcome("simple")
oe._record_plan_gate_outcome("complex")
snap = oe._record_plan_gate_outcome("escalated_tool")
check("simple counter increments", oe._PLAN_GATE_STATS["simple"] == 2)
check("complex + escalated_tool counters increment", oe._PLAN_GATE_STATS["complex"] == 1 and oe._PLAN_GATE_STATS["escalated_tool"] == 1)
check("returns a snapshot dict", isinstance(snap, dict) and snap["simple"] == 2)
_before_unknown = dict(oe._PLAN_GATE_STATS)
oe._record_plan_gate_outcome("bogus_outcome")  # backstop: unknown key must not crash or add
check("unknown outcome is a safe no-op (backstop)", oe._PLAN_GATE_STATS == _before_unknown)


# ============================================================ 2 & 4 — scoped source
print("\n[2] parse-retry emits a cost event (system_event + token_update) so it enters the event-sum")
_pl = _code_lines("agent/planner.py")
check("retry captures its tokens (no longer discarded as _, _)",
      "response_text, _retry_in, _retry_out = await self.executor._call_llm_and_update_tokens(" in _pl)
check("retry logs a 'Calling LLM for Planning' system_event with cost (reload event-sum)",
      '_retry_event = {"step": "Calling LLM for Planning"' in _pl and "self.executor._log_system_event(_retry_event)" in _pl)
check("retry uses a FRESH call_id (no idempotency collision with the primary)",
      "_retry_call_id = str(uuid.uuid4())" in _pl)
check("retry also emits a token_update for live counters", '"call_id": _retry_call_id, "cost_usd": _retry_cost' in _pl)

print("\n[4] legacy history summary computed lazily (skipped on the ContextBuilder path)")
check("no unconditional _create_summary_from_history call before the ContextBuilder branch",
      "previous_turn_summary_str = self._create_summary_from_history" not in _pl)
check("lazy accessor exists", "_get_previous_turn_summary" in _pl and "_previous_turn_summary_cache" in _pl)
check("legacy _tv uses the lazy accessor",
      '"turn_action_history": _get_previous_turn_summary()' in _pl)

# item 5 source: the deepcopy is gone at the distillation call
_pe = _code_lines("agent/phase_executor.py")
check("[5] deepcopy dropped at the distillation call site",
      "self.executor._distill_data_for_llm_context(self.executor.workflow_state" in _pe
      and "copy.deepcopy(self.executor.workflow_state)" not in _pe)
# item 1 source: the three correction sites use the compact catalog
check("[1] all 3 correction sites use _get_correction_catalog()", _pe.count("self.executor._get_correction_catalog()") == 3)


# FOUND (2026-10-29, the prompt-cache measurement): a column-error correction on a governed agent
# sent a 19.5K-token prompt — the "compact" catalog listed all 54 tools (the planner was offered
# 17) with their full descriptions, one of them 6.5K tokens of rules and parameter manuals.
print("\n[1b] the correction catalog: the turn's surface, each tool's opening statement")
_long = _FakeTool("base_manual", "Run a pipeline over the cluster.\n\nTECHNICAL DETAILS:\n" + "rule. " * 2000, {"q": {"required": True}})
lt, _ = build_compact_tools_and_prompts_context({"All Tools": [{"name": "base_manual"}]}, {"base_manual": _long}, {})
check("[1b] a multi-page description is cut to its opening statement", "Run a pipeline over the cluster." in lt and "TECHNICAL DETAILS" not in lt and len(lt) < 400)
_wide = _FakeTool("base_wide", "x " * 400, {})
wt, _ = build_compact_tools_and_prompts_context({"All Tools": [{"name": "base_wide"}]}, {"base_wide": _wide}, {})
check("[1b] …and capped (with an ellipsis) when the first paragraph itself is long", wt.rstrip().endswith("…") and len(wt) < ex.COMPACT_DESCRIPTION_CHARS + 80)
from trusted_data_agent.agent import tool_surface as _TS
_cx = types.SimpleNamespace(dependencies={"STATE": {"structured_tools": _TOOLS_META, "mcp_tools": _MCP, "structured_prompts": _PROMPTS}})
_cx._get_correction_catalog = types.MethodType(ex.PlanExecutor._get_correction_catalog, _cx)
_tok = _TS.set_turn(_TS.TurnSurface(_TS.Surface(mode="governed", offered=["base_readQuery"], held=["base_tableList"]), []))
try:
    gt, _ = _cx._get_correction_catalog()
    check("[1b] a governed turn's correction lists only the offered tools", "base_readQuery" in gt and "base_tableList" not in gt)
    _TS.current().surface.widened.append("base_tableList")
    gt2, _ = _cx._get_correction_catalog()
    check("[1b] a tool found mid-turn appears in the next correction (the cache follows the surface)", "base_tableList" in gt2)
finally:
    _TS.reset_turn(_tok)
_cx._correction_catalog = None
at, _ = _cx._get_correction_catalog()
check("[1b] without a governed surface every enabled tool is listed, as before", "base_readQuery" in at and "base_tableList" in at)


print(f"\n{'='*60}\n§4 token/cost efficiency: {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
