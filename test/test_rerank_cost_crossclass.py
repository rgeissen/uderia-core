"""
Cross-class cost-transparency: knowledge reranking (a billed LLM call) must emit a cost event so it
enters _getTurnCost's event-sum — the item-2 parse-retry pattern, which recurs in the SHARED
executor._rerank_knowledge_with_llm (Ideate/Focus/Conversation) and planner._rerank (Optimize).

Also covers the partial-accumulator turn_cost fix for the Conversation (ReAct) engine: its main
LangChain call never accumulates, but reranking does, so a partial accumulator must NOT be treated
as authoritative.

CI-pure: no server/DB/network. Functional for the reranking event shape + resolver; scoped source
for the four caller wirings.

Run:  PYTHONPATH=src python3 test/test_rerank_cost_crossclass.py
"""
import asyncio
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


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


_SRC = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent"


def _code_lines(rel):
    text = (_SRC / rel).read_text()
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# the exact extraction the frontend _getTurnCost.extractCost performs
def _extract_cost(event):
    if not event or "payload" not in event:
        return 0.0
    payload = event["payload"]
    details = payload.get("details") if isinstance(payload.get("details"), dict) else payload
    return float(details.get("cost_usd", 0) or 0)


# ============================================================ 1 — reranking emits a summable cost event
print("\n[1] executor._rerank_knowledge_with_llm appends a correctly-shaped cost event to _events")


def _fake_rerank_self(cost):
    async def _call(**kw):
        self_ref._last_call_metadata = {"cost_usd": cost}
        return "[2, 1]", 120, 30  # ranking picks doc2 then doc1
    self_ref = types.SimpleNamespace(
        llm_handler=object(),
        _last_call_metadata={},
        _call_llm_and_update_tokens=_call,
    )
    return self_ref


docs = [{"content": "alpha"}, {"content": "beta"}]
evs = []
fs = _fake_rerank_self(0.00042)
reranked = run(PlanExecutor._rerank_knowledge_with_llm(fs, "q", docs, 5, _events=evs))
check("reranking reordered docs per the ranking ([2,1] → beta, alpha)",
      reranked == [{"content": "beta"}, {"content": "alpha"}])
check("exactly one cost event appended", len(evs) == 1)
check("event carries the reranking call's cost under payload.details.cost_usd",
      _extract_cost(evs[0]) == 0.00042)
check("event type is not an aggregate _getTurnCost would skip",
      evs[0]["type"] == "knowledge_reranking_llm")
check("event carries the token counts", evs[0]["payload"]["details"]["input_tokens"] == 120
      and evs[0]["payload"]["details"]["output_tokens"] == 30)

# _events=None (default) → no crash, no event (back-compat for callers that don't collect)
evs2 = None
r2 = run(PlanExecutor._rerank_knowledge_with_llm(_fake_rerank_self(0.001), "q", docs, 5))
check("default (_events=None) still returns reranked docs (back-compat)", isinstance(r2, list))

# no documents → returns input, no event, no LLM call
none_ev = []
r3 = run(PlanExecutor._rerank_knowledge_with_llm(_fake_rerank_self(0.001), "q", [], 5, _events=none_ev))
check("empty documents → no cost event (reranking didn't run)", none_ev == [])


# ============================================================ 2 — Conversation partial-accumulator fix
print("\n[2] _resolve_turn_cost_usd(prefer_accumulator=False) ignores a PARTIAL accumulator (ReAct)")

_RATES = {"m": (2.0, 4.0)}  # USD per 1M


class _FakeCM:
    def calculate_cost(self, provider, model, i, o):
        r_in, r_out = _RATES.get(model, (0.0, 0.0))
        return (i / 1e6) * r_in + (o / 1e6) * r_out


_orig = cost_manager_mod.get_cost_manager
cost_manager_mod.get_cost_manager = lambda: _FakeCM()

# ReAct turn: accumulator holds ONLY the reranking cost (partial); turn_input holds ReAct + rerank.
partial = types.SimpleNamespace(turn_cost_usd=0.00042, turn_input_tokens=10000, turn_output_tokens=2000,
                                current_provider="p", current_model="m")
reprice = _FakeCM().calculate_cost("p", "m", 10000, 2000)  # = full single-model total
got_false = PlanExecutor._resolve_turn_cost_usd(partial, prefer_accumulator=False)
got_true = PlanExecutor._resolve_turn_cost_usd(partial, prefer_accumulator=True)
check("prefer_accumulator=False re-prices the full turn_input (NOT the partial 0.00042)",
      abs(got_false - reprice) < 1e-12 and got_false > 0.00042)
check("prefer_accumulator=True would (wrongly, for ReAct) return the partial accumulator",
      abs(got_true - 0.00042) < 1e-12)
check("the two differ — proving the partial-accumulator trap is real", abs(got_false - got_true) > 1e-6)
# default arg is prefer_accumulator=True (no behaviour change for the fully-accumulated engines)
full = types.SimpleNamespace(turn_cost_usd=0.05, turn_input_tokens=10000, turn_output_tokens=2000,
                             current_provider="p", current_model="m")
check("default (prefer_accumulator=True) unchanged for fully-accumulated engines",
      abs(PlanExecutor._resolve_turn_cost_usd(full) - 0.05) < 1e-12)

cost_manager_mod.get_cost_manager = _orig


# ============================================================ 3 — caller wiring (scoped source)
print("\n[3] all four rerank callers route the cost into a persisted+summed list")
_ide = _code_lines("agent/engines/ideate_engine.py")
_foc = _code_lines("agent/engines/focus_engine.py")
_con = _code_lines("agent/engines/conversation_engine.py")
_pl = _code_lines("agent/planner.py")
check("Ideate routes cost into llm_execution_events (persisted as knowledge_events)",
      "_events=llm_execution_events" in _ide)
check("Focus routes cost into knowledge_events (persisted + summed)",
      "_events=knowledge_events" in _foc)
check("Conversation collects into a turn-scoped executor list",
      "_events=executor._conv_rerank_events" in _con)
check("Conversation seeds system_events with the collected rerank events at persist",
      'system_events = list(getattr(executor, "_conv_rerank_events", []))' in _con)
# S2 superseded the partial-accumulator rule: the ReAct steps accumulate through cost_sink, so the
# accumulator is complete (reranking + ReAct + session name) and BOTH sites read it.
check("Conversation resolves BOTH turn_cost sites through the (now complete) accumulator",
      _con.count("executor._resolve_turn_cost_usd()") == 2 and "prefer_accumulator=False" not in _con)
check("Optimize (planner) logs a reranking cost system_event (execution_trace → summed)",
      'self.executor._log_system_event({' in _pl and '"step": "Calling LLM for Knowledge Reranking"' in _pl)
check("reranking now uses robust_json_parse (§5 mandate) instead of bare json.loads",
      "ranking = robust_json_parse(response_text)" in _code_lines("agent/executor.py"))


print(f"\n{'='*60}\nrerank cost cross-class: {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
