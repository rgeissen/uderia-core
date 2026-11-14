"""S2 baseline freeze — committed GREEN before the Attested Cache landed.

Pins that with prompt caching OFF (the default) nothing about a call changes: the Anthropic
`system` argument is a plain string and no `cache_control` reaches any provider; the
ReAct/genie system message is a plain string; `calculate_cost` prices input + output at the
listed rates with no cache term; the executor's per-call metadata carries `cached_tokens`
(the Gemini implicit-cache reading that already exists) and nothing else cache-shaped.
Every assertion here must stay green through S2 and after it."""
import asyncio, logging, os, pathlib, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.llm import handler as H
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]

print("[1] the switch is absent or off")
check("PROMPT_CACHING absent or False by default", not getattr(APP_CONFIG, "PROMPT_CACHING", False))

print("[2] Anthropic: a plain-string system, no cache_control, usage read as today")
class _AUsage: input_tokens = 11; output_tokens = 7
class _AMsg:
    def __init__(self): self.content = [types.SimpleNamespace(text="hello")]; self.usage = _AUsage()
    def dict(self): return {}
class FakeAnthropic:
    def __init__(self): self.calls = []; self.messages = types.SimpleNamespace(create=self._c, stream=None)
    async def _c(self, **kw): self.calls.append(kw); return _AMsg()
async def _drive():
    fa = FakeAnthropic()
    out = await H.call_llm_api(fa, "hi", user_uuid="u", session_id="s", current_provider="Anthropic", current_model="claude-x", dependencies={"STATE": {}}, disabled_history=True)
    kw = fa.calls[0]
    check("system is a plain string", isinstance(kw["system"], str), type(kw["system"]))
    check("no cache_control anywhere in the request", "cache_control" not in repr(kw))
    check("the user message is a plain string", isinstance(kw["messages"][-1]["content"], str))
    check("tokens read from usage.input_tokens/output_tokens", out[1:3] == (11, 7))
    check("LAST_CALL_USAGE carries no cache reading", not (H.LAST_CALL_USAGE.get({}) or {}).get("cached_tokens"))
asyncio.run(_drive())

print("[3] cost: input + output at the listed rates, nothing else")
from trusted_data_agent.core.cost_manager import CostManager
cm = CostManager.__new__(CostManager)
cm.get_model_cost = lambda p, m: (2.0, 10.0)
check("calculate_cost(1M in, 1M out) == 12.0", abs(cm.calculate_cost("P", "m", 1_000_000, 1_000_000) - 12.0) < 1e-9)

print("[4] structural: the ReAct/genie system message is a plain string; the executor discount is Gemini-only today")
ca = (ROOT / "src/trusted_data_agent/agent/conversation_agent.py").read_text(); gc = (ROOT / "src/trusted_data_agent/agent/genie_coordinator.py").read_text()
def _plain_or_degrading(src):
    if "SystemMessage(content=self.system_prompt)" in src:
        return True
    if "langchain_prefix(" not in src:
        return False
    # the cache-aware builder must DEGRADE to the plain message with caching off
    try:
        from trusted_data_agent.agent import prompt_cache as PC
        from langchain_core.messages import HumanMessage
        prev = getattr(APP_CONFIG, "PROMPT_CACHING", False); APP_CONFIG.PROMPT_CACHING = False
        try:
            sm, hist, _h, pol = PC.langchain_prefix("SYS", [HumanMessage(content="hi")], profile={}, provider="Anthropic", model="claude-x")
        finally:
            APP_CONFIG.PROMPT_CACHING = prev
        return sm.content == "SYS" and hist[0].content == "hi" and pol["enabled"] is False
    except Exception as e:
        print("   degrade probe failed:", e); return False
check("conversation_agent builds SystemMessage(content=self.system_prompt) (or a cache-aware equivalent that degrades to it with caching off)", _plain_or_degrading(ca))
check("genie builds SystemMessage(content=self.system_prompt) (or a cache-aware equivalent that degrades to it with caching off)", _plain_or_degrading(gc))
ex = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
check("_last_call_metadata carries cached_tokens", '"cached_tokens": _cached_tokens' in ex or '"cached_tokens":' in ex)

print(f"\n=== S2 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
