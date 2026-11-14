"""A direct vendor call is priced at the vendor's list price, not the fallback.

FOUND (Optimize campaign OC-F12, 2026-10-28): `llm_model_costs` held `gemini-3.1-flash-lite`
only under provider OpenRouter (`google/gemini-3.1-flash-lite`, $0.25/$1.50). A call through
the Google provider found no row and was priced by the fallback ($0.10/$0.40): 634 direct
Google turns recorded $1.00 against ≈ $3.47 at list price, so Uderia under-stated what Google
billed. The rule (`cost_manager.openrouter_mirror_id` + `get_model_cost`): a vendor's own
provider with no row of its own takes OpenRouter's row for the SAME id; an own row always
wins; no exact mirror → the fallback as before. CI-pure (a throwaway SQLite price table).
"""
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
from trusted_data_agent.auth.models import LLMModelCost  # noqa: E402
from trusted_data_agent.core import cost_manager as CMOD  # noqa: E402

engine = create_engine("sqlite://")
LLMModelCost.__table__.create(engine)
Session = sessionmaker(bind=engine)


@contextmanager
def _session():
    s = Session()
    try:
        yield s
        s.commit()
    finally:
        s.close()


def row(provider, model, i, o, fallback=False):
    with _session() as s:
        s.add(LLMModelCost(id=str(uuid.uuid4()), provider=provider, model=model,
                           input_cost_per_million=i, output_cost_per_million=o,
                           is_manual_entry=False, is_fallback=fallback, is_deprecated=False,
                           source="test"))


row("OpenRouter", "google/gemini-3.1-flash-lite", 0.25, 1.5)
row("OpenRouter", "anthropic/claude-sonnet-5.5", 3.0, 15.0)
row("Google", "gemini-own-row", 0.5, 2.0)
row("OpenRouter", "google/gemini-own-row", 9.0, 9.0)
row("fallback", "fallback", 0.10, 0.40, fallback=True)

_orig = CMOD.get_db_session
CMOD.get_db_session = _session
try:
    cm = CMOD.CostManager.__new__(CMOD.CostManager)
    cm._cost_cache = {}
    cm._COST_CACHE_TTL = 300

    print("\n── §1 the pure mirror id")
    check("Google → google/<model>", CMOD.openrouter_mirror_id("Google", "gemini-3.1-flash-lite") == "google/gemini-3.1-flash-lite")
    check("a model already carrying a vendor prefix has no mirror", CMOD.openrouter_mirror_id("Google", "google/x") is None)
    check("a provider without a mirror (Azure deployment names, Amazon ARNs) has none",
          CMOD.openrouter_mirror_id("Azure", "my-deployment") is None and CMOD.openrouter_mirror_id("Amazon", "nova") is None)

    print("\n── §2 the lookup (OC-F12)")
    check("a direct Google call is priced at OpenRouter's list price for the same model",
          cm.get_model_cost("Google", "gemini-3.1-flash-lite") == (0.25, 1.5))
    check("…and calculate_cost no longer under-states it",
          abs(cm.calculate_cost("Google", "gemini-3.1-flash-lite", 1_000_000, 1_000_000) - 1.75) < 1e-9,
          cm.calculate_cost("Google", "gemini-3.1-flash-lite", 1_000_000, 1_000_000))
    check("an own row always wins over the mirror", cm.get_model_cost("Google", "gemini-own-row") == (0.5, 2.0))
    check("an id OpenRouter spells differently finds no mirror → None (no other model's price)",
          cm.get_model_cost("Anthropic", "claude-sonnet-5-5") is None)
    check("…so it still falls back exactly as before",
          abs(cm.calculate_cost("Anthropic", "claude-sonnet-5-5", 1_000_000, 0) - 0.10) < 1e-9)
    check("an OpenRouter call reads its own row, unchanged", cm.get_model_cost("OpenRouter", "google/gemini-3.1-flash-lite") == (0.25, 1.5))

    print("\n── §3 ensure_price — the rule at selection time")
    r = cm.ensure_price("Google", "gemini-3.1-flash-lite", fetch_live=False)
    check("an already-resolvable model reads priced", r["status"] == "priced" and r["input"] == 0.25)
    r = cm.ensure_price("MLX", "local-model", fetch_live=False)
    check("local inference is filled at $0, explicitly", r["status"] == "filled" and r["source"] == "local"
          and cm.get_model_cost("MLX", "local-model") == (0.0, 0.0))
    _lp = CMOD.litellm_price
    CMOD.litellm_price = lambda p, m, n=None: (1.0, 5.0, "claude-haiku-4-5-20251001") if m == "claude-haiku-4-5-20251001" else None
    try:
        r = cm.ensure_price("Anthropic", "claude-haiku-4-5-20251001", fetch_live=False)
        check("a model LiteLLM knows under its exact id is filled from LiteLLM",
              r["status"] == "filled" and r["source"] == "litellm" and cm.get_model_cost("Anthropic", "claude-haiku-4-5-20251001") == (1.0, 5.0))
    finally:
        CMOD.litellm_price = _lp
    _ag, _lp = CMOD.azure_base_model_guess, CMOD.litellm_price
    CMOD.azure_base_model_guess = lambda d: "gpt-4o-mini" if d == "tda-gpt-4o-mini" else None
    CMOD.litellm_price = lambda p, m, n=None: (0.15, 0.6, "azure/gpt-4o-mini") if (p, m) == ("Azure", "gpt-4o-mini") else None
    try:
        r = cm.ensure_price("Azure", "tda-gpt-4o-mini", fetch_live=False)
        check("an Azure deployment is priced from the model its name carries — and SAYS it is inferred",
              r["status"] == "filled" and r["source"] == "inferred" and "inferred" in (r.get("note") or ""))
        r2 = cm.ensure_price("Azure", "prod-chat", base_model="gpt-4o-mini", fetch_live=False)
        check("…a declared base model is used and is not called inferred",
              r2["status"] == "filled" and r2["source"] == "azure-base-model")
    finally:
        CMOD.azure_base_model_guess, CMOD.litellm_price = _ag, _lp
    CMOD.litellm_price = lambda *a, **k: None
    try:
        r = cm.ensure_price("XAI", "grok-unknown", fetch_live=False)
        check("a model no source prices reads unpriced, with a sentence that says what that means",
              r["status"] == "unpriced" and "fallback rate" in (r.get("note") or ""))
    finally:
        CMOD.litellm_price = _lp
    check("ensure_price never writes over an existing row",
          cm.ensure_price("Google", "gemini-own-row", fetch_live=False)["input"] == 0.5)

    print("\n── §4 the label a consumer sees, and the warning an operator sees")
    check("a priced model's turn reads priced", cm.cost_basis("Google", "gemini-3.1-flash-lite") == "priced")
    check("an unpriced model's turn reads estimated", cm.cost_basis("XAI", "grok-unknown") == "estimated")
    check("a turn with no known model reads estimated", cm.cost_basis("Unknown", "unknown") == "estimated")
    import logging as _lg
    _hits = []
    class _H(_lg.Handler):
        def emit(self, rec): _hits.append(rec.getMessage())
    _h = _H(); CMOD.logger.addHandler(_h)
    try:
        cm.calculate_cost("XAI", "grok-unknown", 10, 10); cm.calculate_cost("XAI", "grok-unknown", 10, 10)
    finally:
        CMOD.logger.removeHandler(_h)
    check("a fallback price is logged as a WARNING once per model, not silently",
          sum("FALLBACK" in m and "grok-unknown" in m for m in _hits) == 1, _hits)
finally:
    CMOD.get_db_session = _orig

print("\n── §5 wiring: selection, sync, schedule, persistence")
import inspect  # noqa: E402
from trusted_data_agent.api import rest_routes as RR  # noqa: E402
from trusted_data_agent.core import task_scheduler as TS, session_manager as SM  # noqa: E402
rsrc = inspect.getsource(RR)
check("create AND update of an LLM configuration run the pricing check and return it",
      rsrc.count("pricing = await _price_for_configuration(") == 2 and rsrc.count('"pricing": pricing') == 2)
check("the coverage endpoint exists (admin)", "'/v1/costs/coverage'" in rsrc)
check("the cost sync ends with the coverage phase", "results['coverage'] = self.fill_coverage()" in inspect.getsource(CMOD.CostManager.sync_from_litellm))
check("a daily price-sync platform job is seeded and dispatched",
      TS.COST_SYNC_JOB_ID == "platform-cost-price-sync" and "_ensure_cost_sync_job()" in inspect.getsource(TS._load_platform_jobs)
      and "_run_cost_price_sync" in inspect.getsource(TS._execute_platform_job_async))
check("the turn chokepoint stamps cost_basis", 'turn_data["cost_basis"] = _gcm().cost_basis(' in inspect.getsource(SM.update_last_turn_data))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
