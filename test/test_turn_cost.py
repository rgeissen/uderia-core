"""
turn_cost dual-model re-pricing — regression gate for docs/assessments/TURN_COST_DUAL_MODEL_PLAN.md.

Proves the persisted per-turn ``turn_cost`` equals the SUM of the accurate per-call costs (each
priced at its OWN model) — the exact quantity the frontend ``_getTurnCost`` reconciles against —
instead of re-pricing all turn tokens at a single model (which mis-priced dual-model turns).

CI-pure: no server, no DB, no network. Functional for the resolver math (real PlanExecutor method
bound to a fake self + a fake CostManager); scoped comment-stripped source assertions for the wiring
that lives in hot paths not cheaply instantiable.

Run:  PYTHONPATH=src python3 test/test_turn_cost.py
"""
import sys
import types
from pathlib import Path

import trusted_data_agent.core.cost_manager as cost_manager_mod
from trusted_data_agent.agent.executor import PlanExecutor

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


_SRC = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent"


def _src(rel):
    return (_SRC / rel).read_text()


def _code_lines(text):
    """Source with comment-only lines dropped, so assertions can't be tripped by prose that
    quotes the OLD (removed) pattern in an explanatory comment."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# --- A deterministic per-model priced CostManager (no DB). Rates are USD per 1M tokens. ---
_RATES = {
    "modelA": (10.0, 30.0),   # expensive strategic model
    "modelB": (1.0, 2.0),     # cheap tactical model
}


class _FakeCM:
    def calculate_cost(self, provider, model, in_tok, out_tok):
        r_in, r_out = _RATES.get(model, (0.0, 0.0))
        return (in_tok / 1e6) * r_in + (out_tok / 1e6) * r_out


_ORIG_GET_CM = cost_manager_mod.get_cost_manager
cost_manager_mod.get_cost_manager = lambda: _FakeCM()  # patched for the whole module run


def _cost(model, i, o):
    return _FakeCM().calculate_cost("p", model, i, o)


def _fake_self(turn_cost_usd=0.0, ti=0, to=0, provider="p", model="modelA"):
    return types.SimpleNamespace(
        turn_cost_usd=turn_cost_usd,
        turn_input_tokens=ti,
        turn_output_tokens=to,
        current_provider=provider,
        current_model=model,
    )


_resolve = PlanExecutor._resolve_turn_cost_usd  # unbound; call with a fake self


# =============================================================== 1 — dual-model correctness
print("\n[1] dual-model: turn_cost = Σ per-call costs, NOT a single-model re-price")
# strategic call on modelA, tactical call on modelB
c_strategic = _cost("modelA", 8000, 500)
c_tactical = _cost("modelB", 5000, 300)
accumulated = c_strategic + c_tactical
sm = _fake_self(turn_cost_usd=accumulated, ti=13000, to=800)
resolved = _resolve(sm)
check("resolver returns the accumulated Σ per-call cost", abs(resolved - accumulated) < 1e-12)
# the whole point: this differs from re-pricing the 13000/800 total at EITHER single model
reprice_A = _cost("modelA", 13000, 800)
reprice_B = _cost("modelB", 13000, 800)
check("Σ per-call != re-price at strategic model A", abs(accumulated - reprice_A) > 1e-6)
check("Σ per-call != re-price at tactical model B", abs(accumulated - reprice_B) > 1e-6)
check("Σ per-call lies between the two single-model re-prices",
      reprice_B < accumulated < reprice_A)


# =============================================================== 2 — single-model no-regression
print("\n[2] single-model: accumulator == token-total re-priced at that one model (no regression)")
# two calls, both on modelA → linear pricing means Σ per-call == re-price of the summed tokens
a1 = _cost("modelA", 8000, 500)
a2 = _cost("modelA", 5000, 300)
acc_single = a1 + a2
reprice_single = _cost("modelA", 13000, 800)
check("Σ per-call (same model) == single re-price within 1e-6",
      abs(acc_single - reprice_single) < 1e-6)
sm2 = _fake_self(turn_cost_usd=acc_single, ti=13000, to=800, model="modelA")
check("resolver returns that value for a single-model turn",
      abs(_resolve(sm2) - reprice_single) < 1e-6)


# =============================================================== 3 — fallback (never $0)
print("\n[3] fallback: empty accumulator + tokens spent → single-model re-price (never $0)")
fb = _fake_self(turn_cost_usd=0.0, ti=13000, to=800, model="modelA")
fb_resolved = _resolve(fb)
check("fallback re-prices the summed tokens at current_model", abs(fb_resolved - reprice_single) < 1e-6)
check("fallback is > 0 (no regression to $0)", fb_resolved > 0)
# zero tokens AND empty accumulator → honestly 0.0
zero = _fake_self(turn_cost_usd=0.0, ti=0, to=0)
check("no spend → 0.0", _resolve(zero) == 0.0)
# accumulator wins even when tokens are also present (accurate path preferred over fallback)
both = _fake_self(turn_cost_usd=accumulated, ti=13000, to=800)
check("accumulator (>0) takes precedence over the fallback re-price",
      abs(_resolve(both) - accumulated) < 1e-12)


# =============================================================== 4 — accumulator init + reset wiring
print("\n[4] accumulator: per-turn field initialised (executor is per-turn) + accumulated at source")
_ex = _code_lines(_src("agent/executor.py"))
check("turn_cost_usd initialised alongside the token accumulators",
      "self.turn_cost_usd = 0.0" in _ex and "self.turn_input_tokens = 0" in _ex)
check("§3.2-A: per-call cost accumulated in _call_llm_and_update_tokens",
      "self.turn_cost_usd += (call_cost_usd" in _ex)
check("§3.1: sub-executor SEEDS the accumulator before run",
      "sub_executor.turn_cost_usd = self.turn_cost_usd" in _ex)
check("§3.1: parent RECLAIMS the sub-executor accumulator after run — as a DELTA merge, so a "
      "sibling phase's concurrent call is not overwritten (test_sub_prompt_totals.py)",
      "self._merge_sub_totals(sub_executor, _sub_base)" in _ex
      and "self.turn_cost_usd = sub_executor.turn_cost_usd" not in _ex)
check("§3.2-D: session-name folds _name_cost into the accumulator (guarded by acc>0)",
      "self.turn_cost_usd += (_name_cost or 0.0)" in _ex
      and 'if getattr(self, "turn_cost_usd", 0.0) > 0:' in _ex)
# ideate + focus route the main synthesis through the accumulator, so they MUST fold session-name
# too (guarded). Since S2 the ReAct lane accumulates as well — each cache-priced LangChain call
# lands in the executor accumulator through `cost_sink` — so conversation folds too.
for rel, label, should_fold in [
    ("agent/engines/ideate_engine.py", "ideate", True),
    ("agent/engines/focus_engine.py", "focus", True),
    ("agent/engines/conversation_engine.py", "conversation (ReAct)", True),
]:
    body = _code_lines(_src(rel))
    folds = 'executor.turn_cost_usd += (_name_cost or 0.0)' in body
    guarded = 'if getattr(executor, "turn_cost_usd", 0.0) > 0:' in body
    if should_fold:
        check(f"{label}: guarded session-name fold present (main cost is accumulated)", folds and guarded)
    else:
        check(f"{label}: NO session-name fold (direct call → fallback re-prices turn_input incl name)",
              not folds)


# =============================================================== 5 — engine persistence switched
print("\n[5] every in-turn engine persistence site resolves via the accumulator (not a re-price)")
for rel, label in [
    ("agent/engines/optimize_engine.py", "optimize (dual-model target)"),
    ("agent/engines/ideate_engine.py", "ideate"),
    ("agent/engines/focus_engine.py", "focus"),
    ("agent/engines/conversation_engine.py", "conversation (ReAct)"),
]:
    body = _code_lines(_src(rel))
    # Conversation (ReAct) resolves via the same helper but forces prefer_accumulator=False (its
    # main call never accumulates, so a partial accumulator from reranking must be ignored).
    check(f"{label}: uses executor._resolve_turn_cost_usd()",
          "executor._resolve_turn_cost_usd()" in body
          or "executor._resolve_turn_cost_usd(prefer_accumulator=False)" in body)
    # the old per-turn re-price pattern (turn_cost assigned straight from calculate_cost) must be
    # gone; the remaining calculate_cost calls are per-call _name_cost / call_cost for event display.
    reprice_lines = [l for l in body.splitlines() if "turn_cost" in l and "calculate_cost" in l]
    check(f"{label}: no `turn_cost = ...calculate_cost(...)` whole-turn re-price remains",
          not reprice_lines)

# S2: the LangChain lanes hand every priced call to the ONE accumulator (`cost_sink` →
# `_accumulate_call_cost`), so Conversation no longer forces the single-model re-price — a
# re-price would forget what the provider served from cache.
_ca = _src("agent/conversation_agent.py"); _con = _code_lines(_src("agent/engines/conversation_engine.py")); _fo = _code_lines(_src("agent/engines/focus_engine.py"))
check("ReAct agent hands each priced call to cost_sink", "self.cost_sink(call_cost or 0.0)" in _ca and "cost_sink: Optional[Callable[[float], None]] = None" in _ca)
check("conversation + focus(ReAct) pass executor._accumulate_call_cost as the sink", "cost_sink=executor._accumulate_call_cost" in _con and "cost_sink=executor._accumulate_call_cost" in _fo)
check("conversation no longer forces the single-model re-price", "prefer_accumulator=False" not in _con)
check("_accumulate_call_cost adds to the same accumulator _resolve_turn_cost_usd reads", "def _accumulate_call_cost(self, cost_usd: float)" in _ex and 'self.turn_cost_usd = (getattr(self, "turn_cost_usd", 0.0) or 0.0) + float(cost_usd or 0.0)' in _ex)
_gc = _src("agent/genie_coordinator.py"); _ce = _code_lines(_src("agent/engines/coordinate_engine.py"))
check("genie: coordinator Σ cache-priced calls is the turn cost, re-price only as the never-$0 fallback", 'self.turn_cost_usd = getattr(self, "turn_cost_usd", 0.0) + (call_cost or 0.0)' in _gc and '_coord_cost = float(getattr(coordinator, "turn_cost_usd", 0.0) or 0.0)' in _ce and "if not _coord_cost:" in _ce)

# executor error/partial-save + completion-card sites resolve via the helper too
check("executor error/partial-save + completion cards use _resolve_turn_cost_usd()",
      _ex.count("self._resolve_turn_cost_usd()") >= 3)


# =============================================================== 6 — post-hoc contributors increment
print("\n[6] post-hoc contributors ADD a priced increment (never re-price the whole turn)")
_rec = _code_lines(_src("agent/recommender.py"))
check("recommender: update_turn_token_counts uses added_cost_usd + additive",
      "added_cost_usd=cost_usd, additive=True" in _rec)
check("recommender: no longer re-prices via new_turn_in/new_turn_out into update_turn_token_counts",
      "turn_id, new_turn_in, new_turn_out" not in _rec)

_sm = _code_lines(_src("core/session_manager.py"))
check("extensions: turn_cost = prior + _ext_cost (priced increment)",
      'last_turn["turn_cost"] = float(last_turn.get("turn_cost", 0.0) or 0.0) + (_ext_cost' in _sm)
check("update_turn_token_counts still supports the incremental added_cost_usd path",
      "added_cost_usd is not None" in _sm)

# grounding judge routes through defer_judge_usage → _record_score_usage (incremental) — unchanged
_gr = _code_lines(_src("agent/grounding/runtime.py"))
check("grounding judge cost is deferred (incremental billing, not a whole-turn re-price)",
      "defer_judge_usage" in _gr)


# =============================================================== 7 — invariant shape
print("\n[7] invariant shape: persisted turn_summary.turn_cost == the resolved Σ per-call value")
_opt = _code_lines(_src("agent/engines/optimize_engine.py"))
check("optimize: turn_cost assigned from _resolve_turn_cost_usd()",
      "turn_cost = executor._resolve_turn_cost_usd()" in _opt)
check("optimize: that turn_cost is what enters the persisted turn_summary",
      '"turn_cost": turn_cost,' in _opt)
check("optimize: session_cost_usd = previous + turn_cost (Σ turn_cost stays consistent)",
      "previous_session_cost + turn_cost" in _opt)


# --- restore the real cost manager getter (hygiene) ---
cost_manager_mod.get_cost_manager = _ORIG_GET_CM


# ==========================================================================
# [F] The session-name fold cannot NameError, and hands its cost to the
#     turn-count update as an increment; the tool seam feeds the accumulator.
#
# Found live (operator screenshots, session c264646b): the name block used
# `get_cost_manager` WITHOUT the local import every other site carries —
# NameError on EVERY Optimize name generation since the fold landed, swallowed
# by the outer [SessionName] catch: no cost fold, no name token_update, no
# turn-count update, silently. Separately the tool-invocation seam accumulated
# TOKENS but never fed the COST accumulator, so the TDA_FinalReport synthesis
# was missing from the persisted turn_cost on every Optimize turn — the live
# event-sum and the reload disagreed ($0.001489 vs $0.001309).
#
# NOTE: this suite is INLINE-executed (no runner); a first version of this
# section appended `def t_...` functions that were never called and the suite
# stayed green with the mutation applied — the exact half-a-gate trap already
# recorded twice this codebase-wide. Assertions run at import, like the rest.
# ==========================================================================
_exec_src = (Path(__file__).parent / ".." / "src" / "trusted_data_agent"
             / "agent" / "executor.py").read_text()
_i = _exec_src.find("GENERATING name for session")
_name_block = _exec_src[_i:_i + 6000]
check("name block imports get_cost_manager locally (the NameError fix)",
      "from trusted_data_agent.core.cost_manager import get_cost_manager" in _name_block)
check("the import precedes the fold call",
      _name_block.find("import get_cost_manager")
      < _name_block.find("_name_cost = get_cost_manager"))
check("the turn-count update passes the name cost as an INCREMENT (no re-price)",
      "added_cost_usd=(_name_cost or 0.0)" in _name_block)

_pe_src = (Path(__file__).parent / ".." / "src" / "trusted_data_agent"
           / "agent" / "phase_executor.py").read_text()
_k = _pe_src.find("self.executor.turn_input_tokens += input_tokens")
check("the tool seam feeds the COST accumulator beside the token accumulators",
      "self.executor.turn_cost_usd += (_tool_llm_cost or 0.0)" in _pe_src[_k:_k + 900])

print(f"\n{'='*60}\nturn_cost dual-model: {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
