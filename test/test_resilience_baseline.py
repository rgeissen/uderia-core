"""S4 baseline freeze — committed GREEN before the Resilience Envelope landed.

Pins that with the resilience switch OFF (the default) nothing about an LLM call or a turn
changes, EXECUTING the real seams with fakes: the handler's retry loop retries the
overload/rate-limit family `LLM_API_MAX_RETRIES` times with the SAME exponential backoff
and re-raises the last error; an unknown error is terminal on the first attempt under
`raise_on_error`; a truncated answer is returned as-is (no continuation); the answer, tokens
and the served provider/model come back unchanged; a LangChain lane's model is the provider's
own class; the profile-config contract's binding set is what it is today; and the ReAct
lane's error path returns its `success: False` dict. Every assertion here must stay green
through S4 and after it (with the switch off)."""
import asyncio, logging, os, pathlib, re, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.pop("TDA_RESILIENCE_ENVELOPE", None)
from trusted_data_agent.core.config import APP_CONFIG
if hasattr(APP_CONFIG, "RESILIENCE_ENVELOPE"):
    APP_CONFIG.RESILIENCE_ENVELOPE = False

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

print("[1] the switch is absent or off")
check("RESILIENCE_ENVELOPE absent or False by default", not getattr(APP_CONFIG, "RESILIENCE_ENVELOPE", False))
check("the retry knobs keep their defaults", APP_CONFIG.LLM_API_MAX_RETRIES == 5 and APP_CONFIG.LLM_API_BASE_DELAY == 2)

print("[2] the handler's retry loop, executed with the switch off")
from trusted_data_agent.llm import handler as H
from openai import APIError as OpenAI_APIError

class _Usage:
    prompt_tokens = 11; completion_tokens = 7
    def __init__(self): self.prompt_tokens_details = None
class _Msg:
    def __init__(self, content): self.content = content
class _Choice:
    def __init__(self, content, finish="stop"): self.message = _Msg(content); self.finish_reason = finish
class _Resp:
    def __init__(self, content, finish="stop"):
        self.choices = [_Choice(content, finish)]; self.usage = _Usage()
    def dict(self): return {"choices": "…"}

class _FakeOpenAI:
    """A fake `AsyncOpenAI` whose completions fail `fail_first` times with the overload family,
    then answer."""
    def __init__(self, fail_first=0, exc=None, answer="hello", finish="stop"):
        self.calls = 0; self.fail_first = fail_first; self.exc = exc; self.answer = answer; self.finish = finish
        outer = self
        class _Completions:
            async def create(self, **kw):
                outer.calls += 1
                if outer.calls <= outer.fail_first:
                    raise outer.exc
                return _Resp(outer.answer, outer.finish)
        class _Chat:
            completions = _Completions()
        self.chat = _Chat()

def _overload():
    return OpenAI_APIError("overloaded", request=None, body=None)

_sleeps = []
async def _fake_sleep(d): _sleeps.append(d)
_real_sleep = H.asyncio.sleep
_real_random = H.random.uniform
H.asyncio.sleep = _fake_sleep
H.random.uniform = lambda a, b: 0.5

_LOOP = asyncio.new_event_loop()
def run(coro): return _LOOP.run_until_complete(coro)

fake = _FakeOpenAI(fail_first=2, exc=_overload())
res = run(H.call_llm_api(fake, "hi", current_provider="OpenAI", current_model="gpt-4o", raise_on_error=True, disabled_history=True))
check("two overload errors then success: 3 calls, the answer returned", fake.calls == 3 and res[0] == "hello", (fake.calls, res[0]))
check("the 5-tuple: (text, in, out, provider, model)", res[1] == 11 and res[2] == 7 and res[3] == "OpenAI" and res[4] == "gpt-4o", res)
check("the backoff is base·2^attempt + jitter, unchanged", _sleeps == [2 * 1 + 0.5, 2 * 2 + 0.5], _sleeps)

_sleeps.clear()
fake = _FakeOpenAI(fail_first=99, exc=_overload())
raised = None
try:
    run(H.call_llm_api(fake, "hi", current_provider="OpenAI", current_model="gpt-4o", raise_on_error=True, disabled_history=True))
except Exception as e:
    raised = e
check("the overload family is retried LLM_API_MAX_RETRIES times then re-raised", fake.calls == APP_CONFIG.LLM_API_MAX_RETRIES and isinstance(raised, OpenAI_APIError), (fake.calls, type(raised)))
check("max_retries-1 backoffs slept", len(_sleeps) == APP_CONFIG.LLM_API_MAX_RETRIES - 1, _sleeps)

_sleeps.clear()
fake = _FakeOpenAI(fail_first=99, exc=ValueError("bad request"))
raised = None
try:
    run(H.call_llm_api(fake, "hi", current_provider="OpenAI", current_model="gpt-4o", raise_on_error=True, disabled_history=True))
except Exception as e:
    raised = e
check("an unknown error is terminal on the first attempt under raise_on_error", fake.calls == 1 and isinstance(raised, ValueError) and _sleeps == [], (fake.calls, raised, _sleeps))

fake = _FakeOpenAI(fail_first=99, exc=ValueError("bad request"))
res = run(H.call_llm_api(fake, "hi", current_provider="OpenAI", current_model="gpt-4o", raise_on_error=False, disabled_history=True))
check("…and an empty answer when not raising", fake.calls == 1 and res[0] == "", (fake.calls, res))

fake = _FakeOpenAI(answer="the first half of a long", finish="length")
res = run(H.call_llm_api(fake, "hi", current_provider="OpenAI", current_model="gpt-4o", raise_on_error=True, disabled_history=True))
check("a truncated answer (finish=length) is returned as-is — one call, no continuation", fake.calls == 1 and res[0] == "the first half of a long", (fake.calls, res[0]))

H.asyncio.sleep = _real_sleep; H.random.uniform = _real_random

print("[3] a LangChain lane's model is the provider's own class")
from trusted_data_agent.llm import langchain_adapter as LA
class _CM:
    def get_llm_configurations(self, user_uuid): return [{"id": "cfg-1", "provider": "OpenAI", "model": "gpt-4o", "credentials": {"apiKey": "sk-test"}}]
_real_cm = LA.get_config_manager
LA.get_config_manager = lambda: _CM()
try:
    inst = LA.create_langchain_llm("cfg-1", "u")
    # The S4 property this pins: with the switch off there is NO resilience envelope — the model is
    # the provider's own class. The one sanctioned layer is the always-on single-usage subclass
    # (llm/stream_usage.py, Oct 2026: OpenAI-compatible streams repeating cumulative usage were
    # summed by LangChain), which adds nothing but the usage merge — so: its direct parent must be
    # the provider's own ChatOpenAI, and nothing resilience-shaped may be present.
    from trusted_data_agent.llm import resilient_chat as _rc, stream_usage as _su
    _cls = type(inst)
    _own = _cls if not _su.is_applied(inst) else _cls.__mro__[1]
    check("create_langchain_llm returns a ChatOpenAI (no wrapper, switch off)",
          _own.__name__ == "ChatOpenAI" and _own.__module__.startswith("langchain_openai") and not _rc.is_wrapped(inst),
          (_cls.__name__, _own.__name__, _own.__module__))
finally:
    LA.get_config_manager = _real_cm

print("[4] the ReAct error path is the failure dict (success False, the raw error kept under `error`)")
CA = src("src/trusted_data_agent/agent/conversation_agent.py")
# Restated 2026-10-29 (trust campaign C18): the answer is the honest sentence naming the failure
# class (agent/turn_failure.py) — the raw provider error had been delivered as the answer. The
# shape the engine relies on is unchanged: success False, the error under `error`.
check("ConversationAgentExecutor returns success False, the honest sentence as the answer, the raw error under `error`",
      '"response": _tf.answer(_unwrap(e)),' in CA and '"success": False,\n                "error": str(e),' in CA)

print("[5] the contract's binding set today")
from trusted_data_agent.core import profile_config_contract as PCC
check("llmConfigurationId and mcpServerId are instance bindings", {"llmConfigurationId", "mcpServerId"} <= set(PCC.IMPORT_BINDING_KEYS))

print(f"\n=== S4 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
