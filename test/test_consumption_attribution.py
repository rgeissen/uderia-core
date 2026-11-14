"""Spend is booked under the model that was called — judges as their own lines, failed turns
with their model, placeholders never counted as models in use.

FOUND (trust + Optimize campaigns, 2026-10-28):
  * Judge calls were added INTO the turn's consumption row (`update_turn_tokens`), so a Google
    judge's spend read as the turn model's (Nova) — per-provider spend was wrong, and the judge
    models never appeared as models in use.
  * The Ideate + tools error path saved its turn without `provider`/`model`, so a failed turn's
    real spend ($0.14 on the B4 timeout) was booked to "Unknown/unknown".
  * A flow's display-only summary turn (`flow/flow-run`, 0 tokens — its cost lives in the node
    turns) and turns refused before any LLM call were counted as "models in use".
CI-pure: a throwaway SQLite with the two consumption tables.
"""
import inspect
import os
import sys
import uuid
from contextlib import contextmanager

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


from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from trusted_data_agent.auth import models as M  # noqa: E402
from trusted_data_agent.auth.models import ConsumptionTurn, User  # noqa: E402
from trusted_data_agent.auth.consumption_manager import ConsumptionManager  # noqa: E402

engine = create_engine("sqlite://")
# Only the tables the consumption path touches (the full metadata carries a duplicate index
# name that SQLite refuses — unrelated to this gate). A table is created before its indexes,
# so a refused index leaves the table usable.
for _name in ("users", "consumption_profiles", "user_consumption", "consumption_turns", "llm_model_costs"):
    _t = M.Base.metadata.tables.get(_name)
    if _t is None:
        continue
    try:
        _t.create(engine, checkfirst=True)
    except OperationalError:
        pass
Session = sessionmaker(bind=engine)
U, S = "u-attr", "s-attr"
with Session() as _db:
    _db.add(User(id=U, username="attr-user", email="attr@example.invalid", password_hash="x"))
    _db.commit()

print("\n── §1 a judge line is its own row, under its own model, counted once")
with Session() as db:
    cm = ConsumptionManager(db)
    db.add(ConsumptionTurn(id=str(uuid.uuid4()), user_id=U, session_id=S, turn_number=1, input_tokens=1000,
                           output_tokens=100, total_tokens=1100, provider="OpenRouter",
                           model="amazon/nova-2-lite-v1", cost_usd_cents=550, status="success"))
    db.commit()
    before = cm.get_or_create_consumption(U).estimated_cost_usd or 0
    cm.record_usage_line(U, S, 1, "Google", "gemini-3.1-flash-lite", 2000, 50, 575, kind="judge")
    rows = db.query(ConsumptionTurn).filter_by(user_id=U, session_id=S, turn_number=1).all()
    turn = next(r for r in rows if r.status == "success")
    judge = next((r for r in rows if r.status == "judge"), None)
    check("the judge's spend is a separate row under the judge's model",
          judge is not None and (judge.provider, judge.model) == ("Google", "gemini-3.1-flash-lite"))
    check("…the turn's own row is untouched (still the turn model's tokens and cost)",
          (turn.input_tokens, turn.cost_usd_cents) == (1000, 550))
    check("…and the user's cost aggregate grew by the judge's cost exactly once",
          (cm.get_or_create_consumption(U).estimated_cost_usd or 0) - before == 575)
    cm.record_usage_line(U, S, 1, "XAI", "grok-x", 0, 0, 0)
    check("an empty line is not booked", db.query(ConsumptionTurn).filter_by(provider="XAI").count() == 0)

print("\n── §2 the judge sink books per model when it knows the breakdown")
from trusted_data_agent.eval import online as ON  # noqa: E402
src = inspect.getsource(ON._record_score_usage)
check("the sink books each judge model as its own line", "cm_.record_usage_line(" in src and 'kind="judge"' in src)
check("…from the lines the caller holds, in either shape", "_lines = judge_usage_lines(judge_usage)" in src)
# The REAL shape the online hook passes (found live: 132 judge runs, 0 judge lines — the rows were
# broken down a second time and every dict entry was dropped).
_rows = [{"provider": "Google", "model": "gemini-3.1-flash-lite", "input_tokens": 2000, "output_tokens": 50, "calls": 3, "cost_usd": 0.000575},
         {"provider": "XAI", "model": "grok-4-1-fast-non-reasoning", "input_tokens": 1500, "output_tokens": 40, "calls": 3, "cost_usd": 0.00032}]
_l = ON.judge_usage_lines(_rows)
check("already-broken-down rows (what the online hook passes) are booked as they are",
      [(x["provider"], x["model"], x["cost_usd"]) for x in _l] == [("Google", "gemini-3.1-flash-lite", 0.000575), ("XAI", "grok-4-1-fast-non-reasoning", 0.00032)])
_t = ON.judge_usage_lines([("Google", "gemini-3.1-flash-lite", 100, 10), ("Google", "gemini-3.1-flash-lite", 50, 5)])
check("a raw panel meter is aggregated per model", len(_t) == 1 and _t[0]["input_tokens"] == 150)
check("nothing → no lines", ON.judge_usage_lines(None) == [] and ON.judge_usage_lines([]) == [])
check("…and keeps the aggregate booking only when no breakdown exists", "cm_.update_turn_tokens(" in src)

print("\n── §3 the activity chart counts turns, not judge lines")
from trusted_data_agent.api import rest_routes as RR  # noqa: E402
check("velocity excludes judge rows", "ConsumptionTurn.status != 'judge'" in inspect.getsource(RR))

print("\n── §4 a failed Ideate + tools turn carries the model that served it")
from trusted_data_agent.agent.engines import conversation_engine as CE  # noqa: E402
ce = inspect.getsource(CE)
i = ce.find('"status": "failed",')
check("the error turn summary names provider and model",
      i > 0 and '"provider": executor.served_provider_model()[0]' in ce[i:i + 900]
      and '"model": executor.served_provider_model()[1]' in ce[i:i + 900])

print("\n── §5 placeholders and no-spend rows are not models in use")
from trusted_data_agent.core import cost_manager as CMOD  # noqa: E402


@contextmanager
def _session():
    s = Session()
    try:
        yield s
        s.commit()
    finally:
        s.close()


with Session() as db:
    for prov, model, tok, cost in (("flow", "flow-run", 0, 0), ("Unknown", "unknown", 0, 0),
                                   ("Anthropic", "claude-x", 500, 10)):
        db.add(ConsumptionTurn(id=str(uuid.uuid4()), user_id=U, session_id="s2", turn_number=1,
                               input_tokens=tok, output_tokens=0, total_tokens=tok, provider=prov,
                               model=model, cost_usd_cents=cost, status="success"))
    db.commit()
_orig = CMOD.get_db_session
CMOD.get_db_session = _session
try:
    cm2 = CMOD.CostManager.__new__(CMOD.CostManager)
    cm2._cost_cache = {}
    cm2._COST_CACHE_TTL = 300
    used = cm2.models_in_use()
    check("a flow's display-only turn is not a model in use", ("flow", "flow-run") not in used)
    check("a turn that spent nothing is not a model in use", ("Unknown", "unknown") not in used)
    check("a turn that spent something is", ("Anthropic", "claude-x") in used)
    check("a judge model is a model in use (its own line)", ("Google", "gemini-3.1-flash-lite") in used)
finally:
    CMOD.get_db_session = _orig

from trusted_data_agent.core import session_manager as SM  # noqa: E402
sm = inspect.getsource(SM.update_last_turn_data)
check("cost_basis is not stamped on a turn that spent nothing, nor on a flow summary",
      "_spent" in sm and 'turn_data.get("provider") != "flow"' in sm)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
