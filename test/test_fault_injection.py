"""The provider fault switch — a fire drill for the TEST server, never production. CI-pure.

Why: a provider failure cannot be provoked by a prompt, and a campaign agent wired to an
always-failing endpoint cannot be activated (activation needs its tests to pass). With
TDA_FAULT_INJECT=1 a call carrying `[fault:<kind>]` raises the error that provider failure
raises, SDK-shaped, at the two seams every model call passes (the handler's attempt loop, every
LangChain model) — so the real handling (classify, the honest sentence, the breaker, the
envelope, JUDGED) is exercised. Also pinned here: the Resilience Envelope's reading on a turn no
model answered was `pass` — "the configured model served" — and now reads `na`.
"""
import asyncio, os, pathlib, sys, types, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)
os.environ.pop("TDA_FAULT_INJECT", None)
from trusted_data_agent.llm import fault_injection as FI
from trusted_data_agent.llm import resilience as R
from trusted_data_agent.agent import turn_failure as TF

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
ROOT = pathlib.Path(__file__).resolve().parents[1]

print("\n── §1 off unless the test server turns it on")
try:
    FI.check("how many customers [fault:402]"); off = True
except Exception:
    off = False
check("switch off: a marker does nothing", off and not FI.enabled())
sentinel = object()
check("switch off: attach returns the model untouched", FI.attach(sentinel) is sentinel)
compose = (ROOT / "docker-compose.yml").read_text()
check("production's compose file never sets it", "TDA_FAULT_INJECT" not in compose)

print("\n── §2 each fault is shaped like the real one, and read like it")
os.environ["TDA_FAULT_INJECT"] = "1"
def raised(kind):
    try:
        FI.check(f"count the customers [fault:{kind}]")
    except BaseException as e:
        return e
    return None
e402, e401, e429, e503, eto = (raised(k) for k in ("402", "401", "429", "503", "timeout"))
check("402 → out of credits, the honest sentence names it", R.out_of_credits(e402) and "no credits left" in TF.answer(e402) and e402.status_code == 402)
check("401 → rejected credentials (terminal)", R.classify(e401).reason == "auth" and not R.classify(e401).retryable)
check("429 → rate limit, 503 → overloaded, timeout → timeout — all retryable",
      R.classify(e429).reason == "rate_limit" and R.classify(e503).reason == "overloaded" and R.classify(eto).reason == "timeout"
      and all(R.classify(x).retryable for x in (e429, e503, eto)))
check("an ordinary question is untouched; the marker must be exact", raised("") is None and FI.marker("fault: 402 or [fault:999]") is None)
check("a marker anywhere in a message list (dicts, LangChain messages) is found",
      FI.marker([{"role": "user", "content": "x [fault:503]"}]) == "503"
      and FI.marker([[types.SimpleNamespace(content="y [fault:timeout]")]]) == "timeout")

print("\n── §3 the handler seam: inside the attempt loop, before any provider call")
from trusted_data_agent.llm import handler as H
from trusted_data_agent.core.config import APP_CONFIG
class FakeOpenAI:
    def __init__(self): self.calls = 0; self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._c))
    async def _c(self, **kw):
        self.calls += 1
        r = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"), finish_reason="stop")],
                                  usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=1, prompt_tokens_details=None))
        r.dict = lambda: {}; return r
async def _no_session(u, s): return None
H.get_session = _no_session
prev = getattr(APP_CONFIG, "RESILIENCE_ENVELOPE", False)
APP_CONFIG.RESILIENCE_ENVELOPE = False
async def call(cli, prompt):
    return await H.call_llm_api(cli, prompt, user_uuid="u", current_provider="OpenAI", current_model="gpt-x",
                                dependencies={"STATE": {}}, disabled_history=True, raise_on_error=True)
fo = FakeOpenAI()
try:
    asyncio.run(call(fo, "count the customers [fault:402]")); hit = None
except Exception as e:
    hit = e
check("a marked call raises the injected fault and never reaches the provider", isinstance(hit, FI.InjectedProviderError) and fo.calls == 0, repr(hit))
fo2 = FakeOpenAI()
out = asyncio.run(call(fo2, "count the customers"))
check("an unmarked call reaches the provider as usual", fo2.calls == 1 and out[0] == "ok")
APP_CONFIG.RESILIENCE_ENVELOPE = prev
hd = (ROOT / "src/trusted_data_agent/llm/handler.py").read_text()
check("the check sits inside the attempt's try (retried, breaker-counted like a real failure)",
      hd.index("_fault_injection.check(prompt, system_prompt, chat_history)") > hd.index("async with _attempt_limit():"))

print("\n── §4 the LangChain seam: every model the factory builds")
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
m = FI.attach(FakeListChatModel(responses=["ok"] * 4))
async def lc():
    plain = (await m.ainvoke([HumanMessage("hello")])).content
    errs = []
    for how, kind in (("invoke", "402"), ("stream", "503")):
        try:
            if how == "invoke":
                await m.ainvoke([HumanMessage(f"q [fault:{kind}]")])
            else:
                async for _ in m.astream([HumanMessage(f"q [fault:{kind}]")]):
                    pass
            errs.append(None)
        except Exception as e:
            errs.append(e)
    return plain, errs
plain, errs = asyncio.run(lc())
check("an unmarked LangChain call answers", plain == "ok")
check("a marked ainvoke and a marked astream both raise the SDK-shaped fault",
      [getattr(x, "status_code", None) for x in errs] == [402, 503])
# FOUND while running the F drills live: the first version raised from a LangChain CALLBACK
# (`on_chat_model_start`), which runs BEFORE the resilience wrapper's retry loop — a 503 was never
# retried nor handed to the fallback. The check now sits inside the generation calls, beneath it.
from trusted_data_agent.llm import resilient_chat as RC
class _Counting(FakeListChatModel):
    calls: int = 0
    async def _agenerate(self, messages, *a, **k):
        object.__setattr__(self, "calls", self.calls + 1)
        return await super()._agenerate(messages, *a, **k)
cm = FI.attach(_Counting(responses=["ok"] * 10))
cm = RC.wrap(cm, provider="OpenRouter", model="x")
_orig_backoff = RC._res.backoff
RC._res.backoff = lambda *a, **k: 0.0
attempts = []
_orig_check = FI.check
def _counted(*t):
    attempts.append(1); return _orig_check(*t)
FI.check = _counted
try:
    try:
        asyncio.run(cm.ainvoke([HumanMessage("q [fault:503]")])); wrapped_raised = False
    except Exception as e:
        wrapped_raised = getattr(e, "status_code", None) == 503
finally:
    FI.check = _orig_check; RC._res.backoff = _orig_backoff
check("inside the resilience wrapper an injected 503 is RETRIED like a real one, then raised",
      wrapped_raised and len(attempts) > 1, f"attempts={len(attempts)}")
la = (ROOT / "src/trusted_data_agent/llm/langchain_adapter.py").read_text()
check("the factory attaches it before the envelope wraps the model", la.index("inst = _fi.attach(inst)") < la.index("inst = _rc.wrap(inst"))
os.environ.pop("TDA_FAULT_INJECT", None)

print("\n── §5 the envelope's reading on a turn no model answered")
from trusted_data_agent.agent import assurance_points as AP
def rec(turn):
    return AP._read_llm_recovery(turn, "conversation_with_tools")
failed_retried = {"status": "failed", "resilience": {"steps": [{"kind": "retry"}, {"kind": "retry"}], "counts": {"retry": 2}, "served_by": None}}
r = rec(failed_retried)
check("retries, then nothing served: na, 'no model answered' (it used to read pass — 'the configured model served')",
      r.state == "na" and "no model answered" in r.detail and "2 retries" in r.detail, r)
r0 = rec({"status": "error"})
check("a failed turn with no steps: na, not 'served on the first attempt'", r0.state == "na" and "no model answered" in r0.detail, r0)
ok = rec({"status": "success", "resilience": {"steps": [{"kind": "retry"}], "counts": {"retry": 1}, "served_by": None}})
check("a successful turn after a retry still reads pass", ok.state == "pass")
fb = rec({"status": "success", "resilience": {"steps": [{"kind": "fallback"}], "counts": {"fallback": 1},
          "served_by": {"provider": "OpenAI", "model": "b"}, "fallback_declared": {"provider": "OpenAI", "model": "b"},
          "primary": {"provider": "OpenRouter", "model": "a"}}})
check("…and the signed fallback serving still reads pass", fb.state == "pass" and "signed fallback" in fb.detail)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
