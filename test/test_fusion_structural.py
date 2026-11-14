"""
Fusion Optimizer structural hardening — regression gate for the structural (non-quick-win)
findings of docs/assessments/FUSION_OPTIMIZER_DEEP_ASSESSMENT_2026-07.md.

Each block is added as its fix lands (C11, C6a, C10, C12-residual, QW8-residual, C4, C1, C9).
CI-pure: no server, no DB, no network. Functional where reachable; scoped source/structural
assertions where the fix lives in a hot path that can't be cheaply instantiated.

Run:  PYTHONPATH=src python3 test/test_fusion_structural.py
"""
import asyncio
import re
import sys
import types
from pathlib import Path

from trusted_data_agent.agent import executor as ex_mod
from trusted_data_agent.agent import phase_executor as pe
from trusted_data_agent.agent import orchestrators

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


async def _drain(agen):
    out = []
    async for x in agen:
        out.append(x)
    return out


async def _drain_into(agen, out):
    # Collect into a caller-owned list so events survive if the generator raises at the end.
    async for x in agen:
        out.append(x)


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent" / "agent"


# ============================================================ C11 — knowledge reranking
print("\n[C11] knowledge reranking: self.llm_handler assigned; impl runs instead of AttributeError")

# Structural: __init__ now assigns the module handler (fixes impl + all 3 engine-guard callers).
_ex_src = (_SRC_ROOT / "executor.py").read_text()
check("PlanExecutor.__init__ assigns self.llm_handler", "self.llm_handler = llm_handler" in _ex_src)


class _RerankSelf:
    """Only what _rerank_knowledge_with_llm reads: llm_handler + _call_llm_and_update_tokens."""
    def __init__(self, handler, response):
        self.llm_handler = handler
        self._response = response
        self.calls = 0

    async def _call_llm_and_update_tokens(self, prompt, reason, source, **kw):
        self.calls += 1
        return self._response, 0, 0


_docs = [{"content": "alpha"}, {"content": "beta"}, {"content": "gamma"}]

# (a) handler present → guard passes, ranking applied (no AttributeError — the pre-fix crash)
_self = _RerankSelf(handler=object(), response="[2, 1]")
_out = run(ex_mod.PlanExecutor._rerank_knowledge_with_llm(_self, "q", _docs, max_docs=2))
check("reranking runs when llm_handler is truthy (calls LLM)", _self.calls == 1)
check("documents reordered per LLM ranking [2,1] → [beta, alpha]",
      [d["content"] for d in _out] == ["beta", "alpha"])

# (b) handler falsy → skips reranking gracefully (no LLM call, returns docs)
_self2 = _RerankSelf(handler=None, response="[1]")
_out2 = run(ex_mod.PlanExecutor._rerank_knowledge_with_llm(_self2, "q", _docs, max_docs=2))
check("no reranking when llm_handler falsy (no LLM call)", _self2.calls == 0)
check("falsy handler returns documents unchanged", _out2 == _docs)

# (c) malformed LLM ranking → falls back to original order (never raises)
_self3 = _RerankSelf(handler=object(), response="not-json")
_out3 = run(ex_mod.PlanExecutor._rerank_knowledge_with_llm(_self3, "q", _docs, max_docs=2))
check("malformed ranking falls back to original order (capped)", _out3 == _docs[:2])


# ============================================================ C6a — hallucinated loop reachable
print("\n[C6a] loop-typed phase with a list loop_over: no unhashable-TypeError; orchestrator reachable")


class _FakeExec:
    """Minimal PlanExecutor stand-in for the loop-phase methods under test."""
    def __init__(self, workflow_state=None):
        self.workflow_state = workflow_state if workflow_state is not None else {}
        self.meta_plan = [{"phase": 1}]
        self.current_phase_index = 0
        self.execution_depth = 0
        self.turn_action_history = []
        self.last_tool_output = {"status": "success", "results": [{"x": 1}]}
        self.current_loop_items = "UNSET"
        self.structured = []

    def _log_system_event(self, e):
        pass

    def _format_sse_with_depth(self, *a, **k):
        return {}

    def _add_to_structured_data(self, x):
        self.structured.append(x)


class _FakePhaseExec:
    # Bind the real methods under test so intra-method calls (e.g. the empty-loop
    # fall-through to _extract_loop_items) exercise the actual implementation.
    _extract_loop_items = pe.PhaseExecutor._extract_loop_items
    _execute_looping_phase = pe.PhaseExecutor._execute_looping_phase

    def __init__(self, executor):
        self.executor = executor


# --- _extract_loop_items: non-string keys must NOT raise (the exact TypeError root cause) ---
_ex = _FakeExec()
_fp = _FakePhaseExec(_ex)
try:
    _r = pe.PhaseExecutor._extract_loop_items(_fp, ["past 5 days"])
    check("list-of-strings key → [] (no unhashable TypeError)", _r == [])
except TypeError as e:
    check(f"list-of-strings key → [] (no unhashable TypeError) [raised {e}]", False)
try:
    _r = pe.PhaseExecutor._extract_loop_items(_fp, [{"a": 1}])
    check("list-of-dicts key → [] (no unhashable TypeError)", _r == [])
except TypeError as e:
    check(f"list-of-dicts key → [] (no unhashable TypeError) [raised {e}]", False)

# --- _extract_loop_items: valid string reference still works (no regression) ---
_ex2 = _FakeExec(workflow_state={"result_of_phase_1": [{"results": [{"a": 1}, {"a": 2}]}]})
_r2 = pe.PhaseExecutor._extract_loop_items(_FakePhaseExec(_ex2), "result_of_phase_1")
check("valid string reference flattens to the results list (no regression)",
      _r2 == [{"a": 1}, {"a": 2}])
_r3 = pe.PhaseExecutor._extract_loop_items(_FakePhaseExec(_FakeExec()), "result_of_phase_9")
check("absent string reference → [] (existing behavior)", _r3 == [])

# --- _execute_looping_phase: hallucinated string-list loop_over dispatches to the orchestrator ---
_hallu_called = {}


async def _fake_hallu(executor, phase):
    _hallu_called["args"] = (executor, phase)
    if False:  # pragma: no cover — async generator
        yield None
    return


_orig_hallu = orchestrators.execute_hallucinated_loop
pe.orchestrators.execute_hallucinated_loop = _fake_hallu
try:
    _ex3 = _FakeExec()
    _phase = {"phase": 1, "type": "loop", "goal": "loop it",
              "relevant_tools": ["dba_x"], "loop_over": ["tableA", "tableB"]}
    run(_drain(pe.PhaseExecutor._execute_looping_phase(_FakePhaseExec(_ex3), _phase)))
    check("string-list loop_over dispatched to hallucinated-loop orchestrator", "args" in _hallu_called)
    check("orchestrator received (executor, phase)", _hallu_called.get("args", (None, None))[1] is _phase)
    check("result stored under result_of_phase_1 (downstream can reference)",
          _ex3.workflow_state.get("result_of_phase_1") == [_ex3.last_tool_output])
    check("history recorded the hallucinated-loop action", len(_ex3.turn_action_history) == 1)
    check("did NOT fall through to _extract_loop_items (current_loop_items untouched)",
          _ex3.current_loop_items == "UNSET")

    # --- empty list loop_over: must NOT dispatch; clean empty-loop skip instead ---
    _hallu_called.clear()
    _ex4 = _FakeExec()
    _phase_empty = {"phase": 1, "type": "loop", "goal": "empty",
                    "relevant_tools": ["dba_x"], "loop_over": []}
    run(_drain(pe.PhaseExecutor._execute_looping_phase(_FakePhaseExec(_ex4), _phase_empty)))
    check("empty list loop_over does NOT dispatch to the orchestrator", "args" not in _hallu_called)
    check("empty list falls through to the empty-loop skip (current_loop_items == [])",
          _ex4.current_loop_items == [])
    check("empty list stored no result_of_phase key", "result_of_phase_1" not in _ex4.workflow_state)
finally:
    pe.orchestrators.execute_hallucinated_loop = _orig_hallu


# ============================================================ 1.2 — @TAG profile-override state ISOLATION
print("\n[1.2] @TAG override: per-executor STATE overlay (no global bleed) + provider-only revert")
import copy as _copy
from trusted_data_agent.agent.engines.optimize_engine import (
    restore_override_app_state, apply_override_state_overlay,
)


class _OvExec:
    """Fake executor exposing exactly the fields the override isolation reads."""
    def __init__(self, applied=True, override_id="prof-override", deps=None):
        self.profile_override_applied = applied
        self.profile_override_id = override_id
        self.user_uuid = "u1"
        self.original_provider = "Google"
        self.original_model = "gemini"
        self.original_provider_details = {}
        self.profile_llm_instance = None
        self.dependencies = deps if deps is not None else {}
        # Values as mutated by the override (must be reverted):
        self.current_provider = "OverrideProvider"
        self.current_model = "override-model"


def _fake_rebuild(tool_to_exclude=None, state=None):
    return ("TC:" + ",".join(sorted(state.get("mcp_tools", {}).keys())), "PC")


# ---- apply_override_state_overlay: isolates the 8 slots WITHOUT mutating the global ----
_GLOBAL = {
    "llm": "DEFAULT_LLM", "mcp_tools": {"default": "t"}, "mcp_prompts": {"d": "p"},
    "structured_tools": {}, "structured_prompts": {}, "mcp_client": "DEFAULT_CLIENT",
    "tool_scopes": {"x": "column"},  # a NON-overridden slot
}
_global_before = _copy.deepcopy(_GLOBAL)
_ex_ov = _OvExec(deps={"STATE": _GLOBAL, "other": 1})
_overlay = apply_override_state_overlay(
    _ex_ov, _GLOBAL,
    {"mcp_client": "OVR_CLIENT", "mcp_tools": {"ovr": "t"}, "mcp_prompts": {},
     "structured_tools": {}, "structured_prompts": {}},
    llm="OVR_LLM", rebuild_context=_fake_rebuild)
check("executor.dependencies['STATE'] is now the overlay", _ex_ov.dependencies["STATE"] is _overlay)
check("other dependencies keys preserved", _ex_ov.dependencies.get("other") == 1)
check("overlay reads overridden mcp_tools", _overlay["mcp_tools"] == {"ovr": "t"})
check("overlay reads overridden mcp_client", _overlay["mcp_client"] == "OVR_CLIENT")
check("overlay carries the override LLM", _overlay["llm"] == "OVR_LLM")
check("overlay derives tools_context from the OVERRIDE catalog", _overlay["tools_context"] == "TC:ovr")
check("NON-overridden slot falls through to the global", _overlay["tool_scopes"] == {"x": "column"})
check("profile_llm_instance set to the override LLM", _ex_ov.profile_llm_instance == "OVR_LLM")
# THE regression this fixes: the global APP_STATE is UNTOUCHED (no cross-user bleed)
check("global APP_STATE not mutated by the override (the cross-user-bleed fix)", _GLOBAL == _global_before)

# ---- LLM-only overlay (no tool slots): tool reads fall through, llm isolated ----
_G2 = {"mcp_tools": {"d": "t"}, "structured_tools": {}, "structured_prompts": {}, "llm": "DEF"}
_G2_before = _copy.deepcopy(_G2)
_ex_llm = _OvExec(deps={"STATE": _G2})
_ov2 = apply_override_state_overlay(_ex_llm, _G2, {}, llm="OVR2", rebuild_context=_fake_rebuild)
check("llm-only overlay isolates the LLM", _ov2["llm"] == "OVR2" and _ex_llm.profile_llm_instance == "OVR2")
check("llm-only overlay: tool reads fall through to the global default", _ov2["mcp_tools"] == {"d": "t"})
check("llm-only overlay leaves the global untouched", _G2 == _G2_before)

# ---- restore_override_app_state is now PROVIDER-ONLY (no 8-slot restore) ----
import trusted_data_agent.core.config as _cfg
_setter_calls = []
_orig_setters = {}
for _name in ("set_user_provider", "set_user_model", "set_user_friendli_details",
              "set_user_azure_deployment_details", "set_user_aws_region",
              "set_user_model_provider_in_profile"):
    _orig_setters[_name] = getattr(_cfg, _name)
    setattr(_cfg, _name, (lambda n: (lambda *a, **k: _setter_calls.append(n)))(_name))
try:
    # Regression retained: fires even when profile_override_id was CLEARED (failure path), keyed on flag.
    _ex = _OvExec(applied=True, override_id=None)
    restore_override_app_state(_ex)  # provider-only; app_state/rebuild no longer needed
    check("restore fires with profile_override_id=None (keyed on the mutation flag)",
          _ex.current_provider == "Google")
    check("executor.current_model reverted", _ex.current_model == "gemini")
    check("set_user_provider called (per-user revert)", "set_user_provider" in _setter_calls)
    check("set_user_model called (per-user revert)", "set_user_model" in _setter_calls)
    check("mutation flag flipped to False after restore", _ex.profile_override_applied is False)
    # idempotency: a second (finally safety-net) call is a no-op (flag already False)
    _setter_calls.clear(); _ex.current_provider = "sentinel"
    restore_override_app_state(_ex)
    check("second call is a no-op (idempotent, flag-gated)",
          _ex.current_provider == "sentinel" and not _setter_calls)
    # flag never set -> no-op (nothing was mutated)
    _setter_calls.clear()
    _ex2 = _OvExec(applied=False)
    restore_override_app_state(_ex2)
    check("no-op when profile_override_applied is False",
          not _setter_calls and _ex2.current_provider == "OverrideProvider")
finally:
    for _name, _fn in _orig_setters.items():
        setattr(_cfg, _name, _fn)

# --- structural: overlay + revert wired into the override block, except, and finally ---
_oe_src = (_SRC_ROOT / "engines" / "optimize_engine.py").read_text()
check("__init__ initialises profile_override_applied", "self.profile_override_applied = False" in _ex_src)
check("flag set True after capturing originals", "executor.profile_override_applied = True" in _oe_src)
check("finally guards the provider revert on the mutation flag",
      "if executor.profile_override_applied:" in _oe_src)
check("except + finally both revert via the shared helper",
      _oe_src.count("_restore_override_state()") >= 2)
check("override installs a per-executor overlay (apply_override_state_overlay)",
      "apply_override_state_overlay(" in _oe_src)
check("no residual global APP_STATE 8-slot write remains in the override block",
      ("APP_STATE['mcp_tools'] =" not in _oe_src) and ("APP_STATE['llm'] = temp_llm_instance" not in _oe_src))
check("temp_mcp_client is really closed (leak fix), not a bare pass",
      ("aclose()" in _oe_src) and ("temp_mcp_client is not None" in _oe_src))


# ============================================================ C12-residual — consolidation strips loop wiring
print("\n[C12-residual] SQL consolidation: the single consolidated query is never a loop phase")
from trusted_data_agent.agent import planner as planner_mod


class _ConsolExec:
    def __init__(self, meta_plan):
        self.meta_plan = meta_plan
        self.original_user_input = "get a and b"
        self.source = "text"
        self.user_uuid = "u"
        self.session_id = "s"
        self.turn_input_tokens = 0
        self.turn_output_tokens = 0
        self._last_call_metadata = {"cost_usd": 0}
        self.prompt_resolver = types.SimpleNamespace(
            get_sql_consolidation_prompt=lambda: "Goal: {user_goal}\nQueries:\n{inefficient_queries}")

    async def _call_llm_and_update_tokens(self, prompt, reason, system_prompt_override, raise_on_error, source):
        return '{"consolidated_query": "SELECT a, b FROM t1 JOIN t2"}', 10, 5

    def _log_system_event(self, e):
        pass

    def _format_sse_with_depth(self, *a, **k):
        return {}


# Last of the two consecutive SQL phases is a LOOP — its wiring must NOT survive consolidation.
_meta = [
    {"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT a FROM t1"}},
    {"phase": 2, "relevant_tools": ["base_readQuery"], "type": "loop",
     "loop_over": "result_of_phase_1", "arguments": {"sql": "SELECT b FROM t2"}},
]
_cex = _ConsolExec(_meta)
_planner = planner_mod.Planner(_cex)

_orig_get_session = planner_mod.session_manager.get_session


async def _fake_get_session(*a, **k):
    return {"input_tokens": 0, "output_tokens": 0}


planner_mod.session_manager.get_session = _fake_get_session
try:
    run(_drain(_planner._rewrite_plan_for_sql_consolidation()))
    check("two SQL phases consolidated into one", len(_cex.meta_plan) == 1)
    _cp = _cex.meta_plan[0]
    check("consolidated phase carries the merged query", _cp["arguments"]["sql"] == "SELECT a, b FROM t1 JOIN t2")
    check("consolidated phase keeps the first phase number", _cp["phase"] == 1)
    check("loop_over stripped from the consolidated phase", "loop_over" not in _cp)
    check("type: loop stripped from the consolidated phase", _cp.get("type") != "loop")
finally:
    planner_mod.session_manager.get_session = _orig_get_session


# ============================================================ QW8-residual — definitive error skips one loop item
print("\n[QW8-residual] fast-path loop: a definitive error on ONE item skips it, not the whole loop")


class _LoopExec:
    def __init__(self):
        self.workflow_state = {"result_of_phase_0": [{"results": [
            {"object_name": "t1"}, {"object_name": "t2"}, {"object_name": "t3"}]}]}
        self.meta_plan = [{"phase": 1}]
        self.current_phase_index = 0
        self.execution_depth = 0
        self.dependencies = {"STATE": {"mcp_tools": {}, "tool_scopes": {}}}
        self.current_loop_items = []
        self.is_in_loop = False
        self.processed_loop_items = []
        self.turn_action_history = []
        self.last_tool_output = None
        self.structured = []

    def _resolve_arguments(self, args, loop_item=None):
        return dict(loop_item or {})

    def _add_to_structured_data(self, x):
        self.structured.append(x)

    def _log_system_event(self, e, *a):
        pass

    def _format_sse_with_depth(self, *a, **k):
        return {}


class _LoopPhaseExec:
    _extract_loop_items = pe.PhaseExecutor._extract_loop_items
    _execute_looping_phase = pe.PhaseExecutor._execute_looping_phase

    def __init__(self, executor):
        self.executor = executor

    async def _execute_tool(self, command, phase, is_fast_path=False):
        from trusted_data_agent.agent.executor import DefinitiveToolError
        obj = command.get("arguments", {}).get("object_name")
        if obj == "t2":
            # Definitive (non-retryable) failure on the middle item.
            raise DefinitiveToolError("[Error 3523] permission denied on t2", "no access to t2")
        self.executor.last_tool_output = {"status": "success", "results": [{"object_name": obj, "ok": True}]}
        return
        yield  # pragma: no cover — makes this an async generator


_lex = _LoopExec()
_loop_phase = {"phase": 1, "type": "loop", "loop_over": "result_of_phase_0",
               "relevant_tools": ["some_tool"], "arguments": {}}
_raised = None
try:
    run(_drain(_LoopPhaseExec(_lex)._execute_looping_phase(_loop_phase)))
except Exception as e:  # noqa: BLE001
    _raised = e
check("loop did NOT abort on the definitive error (no exception propagated)", _raised is None)
_results = _lex.workflow_state.get("result_of_phase_1")
check("all 3 items are represented despite item 2's definitive error",
      isinstance(_results, list) and len(_results) == 3)
if isinstance(_results, list) and len(_results) == 3:
    check("item 1 succeeded", _results[0].get("status") != "error")
    check("item 2 recorded as an error (skipped, not aborted)", _results[1].get("status") == "error")
    check("item 2 error carries the definitive detail", "3523" in str(_results[1].get("error_message", "")))
    check("item 3 succeeded — loop CONTINUED past the definitive error", _results[2].get("status") != "error")


# ============================================================ C4 — renumber rewrites references
print("\n[C4] renumber_plan_refs: phase field AND every result_of_phase_N reference move together")
from trusted_data_agent.agent.planner import renumber_plan_refs

# --- pure helper: every reference format ---
_p = {"phase": 1, "loop_over": "result_of_phase_1",
      "goal": "read result_of_phase_1 and phase_1",
      "arguments": {"sql": "x", "src": {"source": "result_of_phase_1", "key": "n"},
                    "source_data": ["result_of_phase_1", "result_of_phase_10"]},
      "conditions": "result_of_phase_1 > 0"}
renumber_plan_refs([_p], {1: 2, 10: 11})
check("phase field remapped 1→2", _p["phase"] == 2)
check("loop_over string ref remapped", _p["loop_over"] == "result_of_phase_2")
check("canonical {'source': …} dict ref remapped", _p["arguments"]["src"]["source"] == "result_of_phase_2")
check("source_data list ref remapped", _p["arguments"]["source_data"][0] == "result_of_phase_2")
check("result_of_phase_10 remapped independently (greedy digits, not confused with _1)",
      _p["arguments"]["source_data"][1] == "result_of_phase_11")
check("goal text 'result_of_phase_1' remapped", "result_of_phase_2" in _p["goal"])
check("goal text abbreviated 'phase_1' remapped", "phase_2" in _p["goal"])
check("conditions field ref remapped", _p["conditions"] == "result_of_phase_2 > 0")

# --- numbers ABSENT from the mapping are left unchanged (partial mapping = the key property) ---
_q = {"phase": 5, "arguments": {"a": {"source": "result_of_phase_1"}, "b": {"source": "result_of_phase_5"}}}
renumber_plan_refs([_q], {5: 3})  # only 5 is remapped
check("unmapped ref result_of_phase_1 left unchanged", _q["arguments"]["a"]["source"] == "result_of_phase_1")
check("mapped ref result_of_phase_5 → result_of_phase_3", _q["arguments"]["b"]["source"] == "result_of_phase_3")
check("mapped phase field 5 → 3", _q["phase"] == 3)
# non-dict phase entries are skipped without error
check("non-dict plan entries skipped safely", renumber_plan_refs(["junk", 3, None], {1: 2}) == ["junk", 3, None])

# --- temporal injection end-to-end: prepend TDA_CurrentDate, refs shift +1 ---
_texec = types.SimpleNamespace(original_user_input="show system usage for the past 5 days")
_tplanner = planner_mod.Planner(_texec)
_plan = [
    {"phase": 1, "goal": "list tables", "relevant_tools": ["base_tableList"], "arguments": {}},
    {"phase": 2, "goal": "read from result_of_phase_1", "relevant_tools": ["base_readQuery"],
     "arguments": {"src": {"source": "result_of_phase_1", "key": "name"}},
     "loop_over": "result_of_phase_1"},
]
_new, _injected = _tplanner._inject_temporal_context(_plan)
check("temporal phase injected", _injected is True and _new[0]["relevant_tools"] == ["TDA_CurrentDate"])
check("original phase 1 shifted to 2", _new[1]["phase"] == 2)
check("original phase 2 shifted to 3", _new[2]["phase"] == 3)
check("phase-2's loop_over ref shifted 1→2 (no longer resolves to the date phase)",
      _new[2]["loop_over"] == "result_of_phase_2")
check("phase-2's canonical source ref shifted 1→2", _new[2]["arguments"]["src"]["source"] == "result_of_phase_2")
check("phase-2's goal ref shifted 1→2", "result_of_phase_2" in _new[2]["goal"])

# --- chart-collapse mapping: survivor refs rewrite, injected-data (removed-phase) refs preserved ---
# data phases #1,#2 removed (injected as result_of_phase_1/2); survivors #3 (viz), #4 (report).
_survivors = [
    {"phase": 3, "goal": "chart it", "arguments": {"data": {"source": "result_of_phase_1"}}},
    {"phase": 4, "goal": "report", "arguments": {"note": {"source": "result_of_phase_2"}}},
]
_surv_map = {p["phase"]: i + 1 for i, p in enumerate(_survivors)}  # {3:1, 4:2} — survivors only
renumber_plan_refs(_survivors, _surv_map)
check("survivor viz renumbered 3→1", _survivors[0]["phase"] == 1)
check("survivor report renumbered 4→2", _survivors[1]["phase"] == 2)
check("viz ref to REMOVED/injected phase #1 preserved (resolves from injected key)",
      _survivors[0]["arguments"]["data"]["source"] == "result_of_phase_1")
check("report ref to REMOVED/injected phase #2 preserved",
      _survivors[1]["arguments"]["note"]["source"] == "result_of_phase_2")

# --- SQL consolidation end-to-end (reuses the C12 harness): downstream ref shifts down ---
_meta_c4 = [
    {"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT a FROM t1"}},
    {"phase": 2, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT b FROM t2"}},
    {"phase": 3, "relevant_tools": ["TDA_FinalReport"], "goal": "report using result_of_phase_3",
     "arguments": {"src": {"source": "result_of_phase_3"}}},
]
_cex2 = _ConsolExec(_meta_c4)
_planner2 = planner_mod.Planner(_cex2)
planner_mod.session_manager.get_session = _fake_get_session
try:
    run(_drain(_planner2._rewrite_plan_for_sql_consolidation()))
    check("consolidation merged 2 SQL phases, report remains → 2 phases", len(_cex2.meta_plan) == 2)
    _report = _cex2.meta_plan[1]
    check("downstream report renumbered 3→2", _report["phase"] == 2)
    check("downstream report ref shifted result_of_phase_3 → result_of_phase_2 (goal)",
          "result_of_phase_2" in _report["goal"] and "result_of_phase_3" not in _report["goal"])
    check("downstream report canonical ref shifted 3→2",
          _report["arguments"]["src"]["source"] == "result_of_phase_2")
finally:
    planner_mod.session_manager.get_session = _orig_get_session

# --- structural: all four passes route through the helper ---
_pl_src = (_SRC_ROOT / "planner.py").read_text()
check("temporal injection calls renumber_plan_refs",
      _pl_src.count("renumber_plan_refs(") >= 4)
check("multi-loop synthesis builds a >= synthesis_phase_num shift mapping",
      "p[\"phase\"] >= synthesis_phase_num" in _pl_src)
check("chart-collapse builds a survivor-only mapping", "survivor_mapping" in _pl_src)


# ============================================================ C9 — parallel failure semantics
print("\n[C9] parallel-group failure halts the turn (no more 'success' turns from a blocked phase)")
from trusted_data_agent.agent.executor import DefinitiveToolError


class _ParExec:
    MAX_EXECUTION_DEPTH = 5
    _stream_parallel_phases = ex_mod.PlanExecutor._stream_parallel_phases

    def __init__(self):
        self.execution_depth = 0
        self.turn_action_history = []
        self._phase_parallel_state = {}

    def _log_system_event(self, e, *a):
        pass

    def _format_sse_with_depth(self, data, event=None):
        return {"data": data, "event": event}


class _FakePhaseExecutor:
    def __init__(self, fail_phase=None, exc=None):
        self.fail_phase = fail_phase
        self.exc = exc

    async def execute_phase(self, phase):
        pnum = phase.get("phase")
        if pnum == self.fail_phase:
            raise self.exc
        yield {"phase": pnum, "ok": True}


def _run_group(phase_executor, phases):
    """Drive the REAL _stream_parallel_phases on a fresh fake executor; return
    (collected_events, raised_exc_or_None)."""
    collected = []
    raised = None
    gen = ex_mod.PlanExecutor._stream_parallel_phases(_ParExec(), phase_executor, phases)
    try:
        run(_drain_into(gen, collected))
    except BaseException as e:  # noqa: BLE001 — we assert on the type below
        raised = e
    return collected, raised


_phases = [{"phase": 1}, {"phase": 2}, {"phase": 3}]

# (a) a DefinitiveToolError in one parallel phase HALTS the turn (re-raised) after surfacing events
_evts, _exc = _run_group(_FakePhaseExecutor(fail_phase=2, exc=DefinitiveToolError("boom", "no access")), _phases)
check("terminal DefinitiveToolError is re-raised (turn fails, not 'success')",
      isinstance(_exc, DefinitiveToolError))
check("successful sibling phase 1's event still surfaced",
      any(isinstance(e, dict) and e.get("phase") == 1 and e.get("ok") for e in _evts))
check("successful sibling phase 3's event still surfaced",
      any(isinstance(e, dict) and e.get("phase") == 3 and e.get("ok") for e in _evts))
check("the failing phase 2 emitted a visible error event",
      any(isinstance(e, dict) and isinstance(e.get("data"), dict)
          and e["data"].get("details", {}).get("phase_num") == 2 for e in _evts))

# (b) a generic Exception in one phase ALSO halts the turn (sequential parity)
_evts2, _exc2 = _run_group(_FakePhaseExecutor(fail_phase=1, exc=ValueError("kaboom")), _phases)
check("a generic phase exception also halts the turn (matches sequential)",
      isinstance(_exc2, ValueError))

# (c) all phases clean → no raise, all events surfaced
_evts3, _exc3 = _run_group(_FakePhaseExecutor(fail_phase=None), _phases)
check("clean parallel group does not raise", _exc3 is None)
check("all 3 phases' events surfaced on the clean path",
      sum(1 for e in _evts3 if isinstance(e, dict) and e.get("ok")) == 3)


# ============================================================ C3 — plan hydration restored
print("\n[C3] plan hydration reads the REAL entry['result'] and actually fires")


class _HydrateExec:
    def __init__(self, meta_plan, previous_turn_data):
        self.meta_plan = meta_plan
        self.previous_turn_data = previous_turn_data
        self.workflow_state = {}
        self.original_user_input = "continue with the tables from last turn"

    def _log_system_event(self, e, *a):
        pass

    def _format_sse_with_depth(self, *a, **k):
        return {}


def _prev_turn(result_dict):
    return {"workflow_history": [
        {"execution_trace": [
            {"action": {"tool_name": "base_tableList", "arguments": {}}, "result": result_dict},
        ]},
    ]}


# candidate plan: first phase is a loop with a forward ref (source #2 >= looping #1)
def _candidate_plan():
    return [{"phase": 1, "type": "loop", "loop_over": "result_of_phase_2",
             "relevant_tools": ["base_columnDescription"], "arguments": {}}]


# (a) real result shape → hydration fires
_hx = _HydrateExec(_candidate_plan(),
                   _prev_turn({"status": "success", "metadata": {"row_count": 3},
                               "results": [{"t": "a"}, {"t": "b"}, {"t": "c"}]}))
_hplanner = planner_mod.Planner(_hx)
list(_hplanner._hydrate_plan_from_previous_turn())
check("hydration injected previous-turn data into workflow_state",
      "injected_previous_turn_data" in _hx.workflow_state)
_inj = _hx.workflow_state.get("injected_previous_turn_data", [{}])[0]
check("injected data carries the previous results", _inj.get("results") == [{"t": "a"}, {"t": "b"}, {"t": "c"}])
check("injected data carries the metadata", _inj.get("metadata") == {"row_count": 3})
check("loop_over rewritten to the injected key (skips the re-fetch)",
      _hx.meta_plan[0]["loop_over"] == "injected_previous_turn_data")

# (b) the OLD phantom shape (only tool_output_summary-less, no result) → still fires via result
#     but a legacy nested tool_output_summary is also honoured:
_hx2 = _HydrateExec(_candidate_plan(),
                    _prev_turn({"tool_output_summary": {"status": "success",
                                                        "results": [{"t": "z"}], "metadata": {}}}))
list(planner_mod.Planner(_hx2)._hydrate_plan_from_previous_turn())
check("legacy nested tool_output_summary shape still hydrates (defensive fallback)",
      _hx2.workflow_state.get("injected_previous_turn_data", [{}])[0].get("results") == [{"t": "z"}])

# (c) no success entry → does NOT fire
_hx3 = _HydrateExec(_candidate_plan(), _prev_turn({"status": "error", "error_message": "boom"}))
list(planner_mod.Planner(_hx3)._hydrate_plan_from_previous_turn())
check("no successful previous result → hydration does not fire",
      "injected_previous_turn_data" not in _hx3.workflow_state)
check("non-firing hydration leaves loop_over unchanged",
      _hx3.meta_plan[0]["loop_over"] == "result_of_phase_2")

# (d) non-candidate plan (not a forward-ref loop) → does NOT fire
_hx4 = _HydrateExec([{"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {}}],
                    _prev_turn({"status": "success", "results": [{"t": "a"}]}))
list(planner_mod.Planner(_hx4)._hydrate_plan_from_previous_turn())
check("non-loop first phase → hydration does not fire", "injected_previous_turn_data" not in _hx4.workflow_state)


# ============================================================ C2 — dead recovery replan removed
print("\n[C2] dead autonomous recovery replan removed; phase-exhaustion handled honestly")
_pe_src2 = (_SRC_ROOT / "phase_executor.py").read_text()
check("_recover_from_phase_failure method deleted",
      not hasattr(pe.PhaseExecutor, "_recover_from_phase_failure"))
check("no 'async def _recover_from_phase_failure' in source",
      "async def _recover_from_phase_failure" not in _pe_src2)
# Misleading events/markers gone from executable code (comments explaining the removal are fine).
_pe_code2 = "\n".join(l for l in _pe_src2.splitlines() if not l.lstrip().startswith("#"))
check("no 'Recovery Plan Generated' event emitted in code",
      "Recovery Plan Generated" not in _pe_code2)
check("no 'RECOVERY_REPLAN' history marker in code", "RECOVERY_REPLAN" not in _pe_code2)
check("no 'Attempting LLM-based Recovery' event in code", "Attempting LLM-based Recovery" not in _pe_code2)
# Honest exhaustion handling present: records an error result + surfaces a failure event.
check("phase-exhaustion now records an honest failure event",
      "Phase Failed After" in _pe_code2)
check("phase-exhaustion stores an error result for downstream/report",
      '"status": "error"' in _pe_code2 and "_phase_result_key" in _pe_code2)
# The now-orphaned globally_skipped_tools state was removed.
check("orphaned globally_skipped_tools initializer removed",
      "globally_skipped_tools" not in (_SRC_ROOT / "executor.py").read_text())


# ============================================================ C14 — champion self-demotion (negative-learning substitute)
print("\n[C14] champion self-demotion on replay failure (counter-based, transient-tolerant)")
import json as _json
import tempfile
from trusted_data_agent.agent.rag_retriever import RAGRetriever, RAG_REPLAY_FAILURE_DEMOTE_THRESHOLD


class _RagSelf:
    def __init__(self, case_dir):
        self.collections = {0: object()}
        self._case_dir = Path(case_dir)
        self.demote_calls = []

    def _get_collection_dir(self, cid):
        return self._case_dir

    async def update_case_feedback(self, case_id, score):
        self.demote_calls.append((case_id, score))
        return True


def _fresh_case(dirpath, case_id="abc123", failcount=0):
    f = Path(dirpath) / f"case_{case_id}.json"
    f.write_text(_json.dumps({"metadata": {"user_query": "q", "replay_failure_count": failcount}}))
    return f


def _failcount(dirpath, case_id="abc123"):
    return _json.loads((Path(dirpath) / f"case_{case_id}.json").read_text())["metadata"].get("replay_failure_count", 0)


check("threshold is the counter-based value (2)", RAG_REPLAY_FAILURE_DEMOTE_THRESHOLD == 2)

# (a) first real failure → count=1, NOT yet demoted
_d = tempfile.mkdtemp()
_fresh_case(_d)
_rs = _RagSelf(_d)
run(RAGRetriever.record_champion_replay_outcome(_rs, "abc123", succeeded=False, is_transient=False))
check("first real failure increments counter to 1", _failcount(_d) == 1)
check("first real failure does NOT demote yet", _rs.demote_calls == [])

# (b) second consecutive real failure → demote via update_case_feedback(-1)
run(RAGRetriever.record_champion_replay_outcome(_rs, "abc123", succeeded=False, is_transient=False))
check("second consecutive real failure reaches threshold", _failcount(_d) == 2)
check("champion auto-demoted at threshold (update_case_feedback(-1))",
      _rs.demote_calls == [("abc123", -1)])

# (c) a guided SUCCESS resets the counter
_d2 = tempfile.mkdtemp()
_fresh_case(_d2, failcount=1)
_rs2 = _RagSelf(_d2)
run(RAGRetriever.record_champion_replay_outcome(_rs2, "abc123", succeeded=True))
check("guided success resets the failure counter to 0", _failcount(_d2) == 0)
check("guided success never demotes", _rs2.demote_calls == [])

# (d) a TRANSIENT failure is ignored (no counter bump, no demote) — protects a good champion
_d3 = tempfile.mkdtemp()
_fresh_case(_d3, failcount=1)
_rs3 = _RagSelf(_d3)
run(RAGRetriever.record_champion_replay_outcome(_rs3, "abc123", succeeded=False, is_transient=True))
check("transient failure does NOT bump the counter", _failcount(_d3) == 1)
check("transient failure does NOT demote", _rs3.demote_calls == [])

# (e) no case_id → no-op (turn wasn't champion-guided)
_rs4 = _RagSelf(tempfile.mkdtemp())
run(RAGRetriever.record_champion_replay_outcome(_rs4, "", succeeded=False))
check("no case_id → no-op (no demote)", _rs4.demote_calls == [])

# --- removal of the dead failed-strategy machinery ---
_rag_src = (_SRC_ROOT / "rag_retriever.py").read_text()
_rag_code = "\n".join(l for l in _rag_src.splitlines() if not l.lstrip().startswith("#"))
check("no failed-strategy stored in extraction (case_study['failed_strategy'] = ... gone)",
      'case_study["failed_strategy"] =' not in _rag_code)
check("no strategy_type=='failed' retrieval/format branch",
      'strategy_type == "failed"' not in _rag_code)
check("no failed_strategy read in _prepare_chroma_metadata / summary",
      '"failed_strategy" in case_study' not in _rag_code and '"failed_strategy" in case_data' not in _rag_code)
# --- the wiring: both success and failure hooks call the outcome recorder ---
check("optimize_engine success path records the guided outcome",
      "record_champion_replay_outcome" in (_SRC_ROOT / "engines" / "optimize_engine.py").read_text())
check("executor failure path records the guided outcome (with transient detection)",
      "record_champion_replay_outcome" in (_SRC_ROOT / "executor.py").read_text())


print(f"\n=== Fusion structural results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
