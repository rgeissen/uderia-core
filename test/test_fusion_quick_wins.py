"""
Fusion Optimizer quick-win hardening — regression gate for the 10 §6 quick wins of
docs/assessments/FUSION_OPTIMIZER_DEEP_ASSESSMENT_2026-07.md.

Each block force-feeds the exact failure the fix addresses (data-less errors, a temporal
hallucinated-loop item, meta_plan=None, a withheld-answer turn, an incidental-substring
error code, a fail-open profile-resolution error) and asserts the corrected behaviour.
Where a fix lives in a nested closure or an in-place structural change (QW4/QW5/QW6), the
check is a scoped source/order assertion (documented inline as such).

CI-pure: no server, no DB, no network. Functional where reachable, structural otherwise.

Run:  PYTHONPATH=src python3 test/test_fusion_quick_wins.py
"""
import asyncio
import inspect
import re
import sys
import types
from pathlib import Path

from trusted_data_agent.agent import orchestrators
from trusted_data_agent.agent import phase_executor as pe
from trusted_data_agent.agent import rag_retriever
from trusted_data_agent.agent import executor as ex_mod
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


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _drain(agen):
    out = []
    async for x in agen:
        out.append(x)
    return out


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent" / "agent"


# ============================================================ QW1 (C6b)
print("\n[QW1/C6b] hallucinated-loop → date-range delegation passes the required `phase` arg")
_recorded = {}


async def _fake_date_orch(*args, **kwargs):
    _recorded["args"] = args
    _recorded["kwargs"] = kwargs
    if False:  # pragma: no cover — make this an async generator
        yield None
    return


_orig_date_orch = orchestrators.execute_date_range_orchestrator
orchestrators.execute_date_range_orchestrator = _fake_date_orch
try:
    fake_exec = types.SimpleNamespace()
    phase = {"relevant_tools": ["dba_resusageSummary"], "loop_over": ["past 5 days"]}
    # Before the fix this raised TypeError (missing `phase`); the call must complete.
    run(_drain(orchestrators.execute_hallucinated_loop(fake_exec, phase)))
    check("date branch reached the (patched) orchestrator", "args" in _recorded)
    check("orchestrator received 5 positional args (incl. phase)", len(_recorded.get("args", ())) == 5)
    check("the 5th positional arg IS the phase dict", _recorded.get("args", (None,) * 5)[4] is phase)
    check("date_param_name arg is 'date'", _recorded.get("args", (None,) * 5)[2] == "date")
finally:
    orchestrators.execute_date_range_orchestrator = _orig_date_orch

# Signature-level guard: the orchestrator genuinely requires `phase` (no default).
_sig = inspect.signature(_orig_date_orch)
check("execute_date_range_orchestrator declares a required `phase` param",
      "phase" in _sig.parameters and _sig.parameters["phase"].default is inspect.Parameter.empty)


# ============================================================ QW2 (C7)
print("\n[QW2/C7] FASTPATH column-orchestrator accumulator name is defined (no undefined `all_loop_item_results`)")
_pe_src = (_SRC_ROOT / "phase_executor.py").read_text()
# The undefined standalone name must be gone. `\b` after `results` excludes the legitimate
# `all_loop_item_results_aggregate` (next char is `_`, a word char → no boundary).
check("no bare `all_loop_item_results` read remains",
      re.search(r"\ball_loop_item_results\b", _pe_src) is None)
check("the correct accumulator `all_loop_results` is present",
      "all_loop_results" in _pe_src)
check("the unrelated `all_loop_item_results_aggregate` (slow path) is untouched",
      "all_loop_item_results_aggregate" in _pe_src)


# ============================================================ QW3 (C8)
print("\n[QW3/C8] Table/Column correction strategies: search both fields + guard .group(1)")


class _FakeStratExec:
    """Minimal executor for the Table/Column correction strategies. prompt_resolver
    returns None so generate_correction short-circuits after the regex+group(1) —
    exercising the (previously crashing) match path without an LLM call."""
    def __init__(self):
        self.original_user_input = "list rows"
        self.dependencies = {"STATE": {"tools_context": "", "prompts_context": ""}}
        self.prompt_resolver = types.SimpleNamespace(
            get_tactical_self_correction_table_error_prompt=lambda: None,
            get_tactical_self_correction_column_error_prompt=lambda: None,
        )


def _err(data="", msg=""):
    return {"status": "error", "data": data, "error_message": msg}


_failed = {"tool_name": "base_readQuery", "arguments": {"database_name": "db"}}

# (a) pattern ONLY in error_message (data empty) — previously raised AttributeError on .group(1)
try:
    corrected, events = run(
        pe.TableNotFoundStrategy(_FakeStratExec()).generate_correction(
            _failed, _err(data="", msg="Object 'db.ghost' does not exist")))
    check("Table: error-message-only match does not raise; returns (None, []) via None prompt",
          corrected is None and events == [])
except Exception as e:
    check(f"Table: error-message-only match does not raise (got {type(e).__name__})", False)

try:
    corrected, events = run(
        pe.ColumnNotFoundStrategy(_FakeStratExec()).generate_correction(
            _failed, _err(data="", msg="Column 'ghost' does not exist")))
    check("Column: error-message-only match does not raise; returns (None, [])",
          corrected is None and events == [])
except Exception as e:
    check(f"Column: error-message-only match does not raise (got {type(e).__name__})", False)

# (b) pattern in NEITHER field — must delegate to GenericCorrectionStrategy (guarded, not crash)
_sentinel = ({"tool_name": "SENTINEL_GENERIC"}, [("delegated", None)])
_orig_generic = pe.GenericCorrectionStrategy.generate_correction


async def _fake_generic(self, fa, er):
    return _sentinel[0], _sentinel[1]


pe.GenericCorrectionStrategy.generate_correction = _fake_generic
try:
    corrected, _ = run(
        pe.TableNotFoundStrategy(_FakeStratExec()).generate_correction(
            _failed, _err(data="totally unrelated failure", msg="")))
    check("Table: no-match delegates to GenericCorrectionStrategy (no AttributeError)",
          corrected is _sentinel[0])
    corrected, _ = run(
        pe.ColumnNotFoundStrategy(_FakeStratExec()).generate_correction(
            _failed, _err(data="totally unrelated failure", msg="")))
    check("Column: no-match delegates to GenericCorrectionStrategy",
          corrected is _sentinel[0])
finally:
    pe.GenericCorrectionStrategy.generate_correction = _orig_generic


# ============================================================ QW4 (C5)
print("\n[QW4/C5] planner _pass(): None-guard + fault-isolation (scoped source assertion — nested closure)")
_planner_src = (_SRC_ROOT / "planner.py").read_text()
_m = re.search(r"async def _pass\(name, gen\):.*?\n        async for event in self\._generate_meta_plan\(",
               _planner_src, re.DOTALL)
check("_pass block located in generate_and_refine_plan", _m is not None)
_pass_block = _m.group(0) if _m else ""
check("_pass guards meta_plan length with isinstance(..., list) (no len(None) crash)",
      "isinstance(plan, list)" in _pass_block)
check("_pass wraps the generator drive in try/except (fault isolation)",
      "except Exception as _pass_err:" in _pass_block)
check("_pass no longer calls bare len(self.executor.meta_plan) unguarded",
      "n_before = len(self.executor.meta_plan)" not in _pass_block)


# ============================================================ QW5 (C12)
print("\n[QW5/C12] SQL consolidation LLM call: raise_on_error=False + wrapped in try (non-fatal)")
_consol_src = inspect.getsource(  # real method — import already loaded via engines chain
    __import__("trusted_data_agent.agent.planner", fromlist=["Planner"]).Planner
    ._rewrite_plan_for_sql_consolidation)
check("consolidation call uses raise_on_error=False", "raise_on_error=False" in _consol_src)
# Ignore comment lines (the rationale comment mentions the old value in prose).
_consol_code = "\n".join(l for l in _consol_src.splitlines() if not l.lstrip().startswith("#"))
check("no live call uses raise_on_error=True", "raise_on_error=True" not in _consol_code)
check("consolidation LLM call is wrapped (except Exception as _consol_err)",
      "except Exception as _consol_err:" in _consol_src)
check("a falsy response skips consolidation (keeps the original plan)",
      "if not response_text:" in _consol_src)


# ============================================================ QW6
print("\n[QW6] EPC provenance chain is initialised AFTER current_turn_number is computed")
_run_src = inspect.getsource(ex_mod.PlanExecutor.run)
_i_turn = _run_src.find("self.current_turn_number = len(session_data")
_i_prov = _run_src.find("self.provenance = ProvenanceChain(")
check("turn-number recompute is present in run()", _i_turn != -1)
check("EPC init is present in run()", _i_prov != -1)
check("EPC init comes AFTER the turn-number recompute (turn_number no longer sealed as 0)",
      _i_turn != -1 and _i_prov != -1 and _i_prov > _i_turn)


# ============================================================ QW7
print("\n[QW7] trust-gate profile resolution fails OPEN but is logged + counted (not silent)")
import trusted_data_agent.core.config_manager as _cfgmod

_fake_self = types.SimpleNamespace(active_profile_id="p1", user_uuid="u1")
_orig_gcm = _cfgmod.get_config_manager

# (a) error path → returns None AND increments the audit counter
_before = ex_mod._TRUST_GATE_FAILOPEN["count"]


def _raising_gcm():
    raise RuntimeError("transient DB error")


_cfgmod.get_config_manager = _raising_gcm
try:
    _res = ex_mod.PlanExecutor._resolve_gate_profile(_fake_self, "grounding")
    check("fail-open returns None (gate degrades to observe/off)", _res is None)
    check("audit counter incremented on fail-open",
          ex_mod._TRUST_GATE_FAILOPEN["count"] == _before + 1)
finally:
    _cfgmod.get_config_manager = _orig_gcm

# (b) success path → returns the profile AND does not touch the counter
_sentinel_profile = {"id": "p1", "groundingConfig": {"mode": "enforce"}}
_after_fail = ex_mod._TRUST_GATE_FAILOPEN["count"]
_cfgmod.get_config_manager = lambda: types.SimpleNamespace(
    get_profile=lambda pid, uid: _sentinel_profile)
try:
    _res = ex_mod.PlanExecutor._resolve_gate_profile(_fake_self, "grounding")
    check("success path returns the resolved profile", _res is _sentinel_profile)
    check("counter unchanged on success",
          ex_mod._TRUST_GATE_FAILOPEN["count"] == _after_fail)
finally:
    _cfgmod.get_config_manager = _orig_gcm


# ============================================================ QW8
print("\n[QW8] DEFINITIVE_TOOL_ERRORS patterns are anchored (no incidental-substring aborts)")
_pats = list(pe.DEFINITIVE_TOOL_ERRORS.keys())


def _matches_any(text):
    return any(re.search(p, text, re.IGNORECASE) for p in _pats)


check("real permission error '[Error 3523]' still matches (definitive)",
      _matches_any("[Teradata Database] [Error 3523] The user does not have ..."))
check("longer code '35234' does NOT match 3523", not _matches_any("[Error 35234] some other failure"))
check("a data value containing '3523' embedded ('x3523y') does NOT match",
      not _matches_any("row: value=x3523y"))
check("real 'Invalid query' still matches", _matches_any("The generated Invalid query text"))
check("'Invalid queryset' does NOT false-match 'Invalid query'",
      not _matches_any("Invalid queryset returned rows"))
check("old bare literal keys are gone (keys are anchored regexes)",
      "3523" not in _pats and "Invalid query" not in _pats)


# ============================================================ QW9 (C13)
print("\n[QW9/C13] answer_withheld_by_trust_gate: blocks poison the RAG champion seed")
w = oe.answer_withheld_by_trust_gate
check("grounding decision=block → withheld", w({"grounding": {"decision": "block"}}) is True)
check("honesty decision=block → withheld", w({"honesty": {"decision": "block"}}) is True)
check("guardrail action=block → withheld", w({"guardrail": {"action": "block"}}) is True)
check("grounding annotate → NOT withheld", w({"grounding": {"decision": "annotate"}}) is False)
check("guardrail redact → NOT withheld (answer still valid)", w({"guardrail": {"action": "redact"}}) is False)
check("clean turn (no verdicts) → NOT withheld", w({"status": "success"}) is False)
check("None/absent verdicts tolerated → NOT withheld", w({"grounding": None, "honesty": None}) is False)
check("non-dict verdict tolerated → NOT withheld", w({"grounding": "block"}) is False)
check("non-dict turn_summary tolerated → NOT withheld", w(None) is False)
# And the enqueue condition actually consumes it:
_oe_src = inspect.getsource(oe)
check("the RAG enqueue condition includes `not _trust_blocked`",
      "not _trust_blocked" in _oe_src and "_trust_blocked = not turn_seedable(turn_summary)" in _oe_src)
# A FLAGGED answer is not a template either (2026-09-15): the honesty gate found the
# answer over-claiming; under observe it only flags, but the plan is the same plan.
ts = oe.turn_seedable
check("clean turn (honesty action=pass, no decision) → seedable", ts({"honesty": {"ran": True, "action": "pass"}}) is True)
check("honesty flag (observe) → NOT seedable", ts({"honesty": {"decision": "flag", "kind": "empty_relation"}}) is False)
check("honesty disclosed (annotate) → NOT seedable", ts({"honesty": {"decision": "disclosed"}}) is False)
check("honesty block → NOT seedable", ts({"honesty": {"decision": "block"}}) is False)
check("grounding block → NOT seedable (withheld family)", ts({"grounding": {"decision": "block"}}) is False)
check("guardrail redact → seedable (answer still valid)", ts({"guardrail": {"action": "redact"}}) is True)
check("non-dict turn → NOT seedable", ts(None) is False)


# ============================================================ QW10
print("\n[QW10] RAG champion over-fetch multiplier restored to the documented 3×")
check("RAG_OVERFETCH_MULTIPLIER == 3", rag_retriever.RAG_OVERFETCH_MULTIPLIER == 3)
_rag_src = (_SRC_ROOT / "rag_retriever.py").read_text()
check("no residual `k * 10` over-fetch remains", "k * 10" not in _rag_src)
check("both query sites use the named constant",
      _rag_src.count("k * RAG_OVERFETCH_MULTIPLIER") >= 2)


print(f"\n=== Fusion quick-wins results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
