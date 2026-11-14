"""A Gemini model that refuses to have thinking disabled must not fail every call.

THE DEFECT (measured against the live API, 2026-09-09): `gemini-3.5-flash-lite`
answers `400 INVALID_ARGUMENT / "Request contains an invalid argument."` to any
request carrying `thinkingBudget: 0`, while accepting -1, 1024 and no budget at
all.  `gemini-3.1-flash-lite`, `gemini-3.5-flash` and `gemini-2.5-flash-lite`
all accept 0.  The platform sends 0 from two places — session-name generation
(always) and a configured `thinking_budget` (which the LLM-config editor stamps
as 0 on every save) — so switching a profile's model to 3.5-flash-lite made
every call fail.

The capability is NOT discoverable (`GET /v1beta/models/<id>` reports
`"thinking": true` for the model that accepts 0 and the one that refuses it
alike), so it is learned from the refusal.  Disabling thinking is only ever an
OPTIMISATION, so a refusal must degrade to "thinking enabled", never to a
failed turn — and a model that accepts 0 must keep getting 0 (no regression).
"""
import asyncio, pathlib, sys, types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from trusted_data_agent.llm import thinking_support as ts

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name} {detail}")

REFUSER = "gemini-3.5-flash-lite"
ACCEPTER = "gemini-3.1-flash-lite"

# The verbatim error text the operator reported (LangChain-wrapped shape).
REPORTED = ("Error calling model 'gemini-3.5-flash-lite' (Bad Request): 400 Bad Request. "
            "{'message': '{\\n \"error\": {\\n \"code\": 400,\\n \"message\": \"Request "
            "contains an invalid argument.\",\\n \"status\": \"INVALID_ARGUMENT\"\\n }\\n}\\n', "
            "'status': 'Bad Request'}")

class NativeClientError(Exception):
    """Shape of google.genai.errors.ClientError."""
    def __init__(self, code=400, status="INVALID_ARGUMENT"):
        super().__init__(f"{code} {status}. {{'error': {{'code': {code}}}}}")
        self.code, self.status = code, status

# ── the registry ────────────────────────────────────────────────────────────
def t_registry():
    ts.reset()
    check("an unmet model is optimistically tried", ts.supports_disabled_thinking(REFUSER))
    ts.record_disable_refusal(REFUSER)
    check("a recorded refusal is remembered", not ts.supports_disabled_thinking(REFUSER))
    check("the refusal does not leak to another model", ts.supports_disabled_thinking(ACCEPTER))
    check("model names are normalised (prefix + case)",
          not ts.supports_disabled_thinking("models/Gemini-3.5-Flash-Lite"))
    ts.reset()
    check("reset forgets", ts.supports_disabled_thinking(REFUSER))
    check("no model is seeded — the list would be an arms race",
          ts.supports_disabled_thinking(REFUSER) and ts.supports_disabled_thinking("anything"))

def t_detector():
    check("the operator's verbatim error is recognised",
          ts.looks_like_thinking_disable_refusal(Exception(REPORTED)))
    check("the native ClientError shape is recognised",
          ts.looks_like_thinking_disable_refusal(NativeClientError()))
    check("a differently-worded 400 still matches (status wording varies)",
          ts.looks_like_thinking_disable_refusal(NativeClientError(status="Bad Request")))
    check("a 429 is not mistaken for it",
          not ts.looks_like_thinking_disable_refusal(Exception("429 RESOURCE_EXHAUSTED. quota")))
    check("a 500 is not mistaken for it",
          not ts.looks_like_thinking_disable_refusal(Exception("500 INTERNAL. oops")))
    check("a 400 carrying no code attribute and no 400-ish text is not matched",
          not ts.looks_like_thinking_disable_refusal(Exception("model overloaded")))
    # The structured branch must stand on its own: a wrapper may carry the code
    # while its message says nothing a string match could find.
    opaque400 = type("W", (Exception,), {})("the request was rejected")
    opaque400.code = 400
    check("a structured 400 with an opaque message is recognised",
          ts.looks_like_thinking_disable_refusal(opaque400))
    opaque500 = type("W", (Exception,), {})("the request was rejected")
    opaque500.code = 500
    check("a structured 500 with the same message is not",
          not ts.looks_like_thinking_disable_refusal(opaque500))

# ── the native handler lane ─────────────────────────────────────────────────
from trusted_data_agent.llm.handler import _google_generate_content
from google.genai import types as genai_types

class FakeClient:
    """Refuses a config carrying thinking_budget==0, like gemini-3.5-flash-lite."""
    def __init__(self, refuses_zero=True, always_fail=False):
        self.refuses_zero, self.always_fail, self.calls = refuses_zero, always_fail, []
        self.aio = types.SimpleNamespace(models=self)
    async def generate_content(self, *, model, contents, config):
        tc = getattr(config, "thinking_config", None)
        budget = getattr(tc, "thinking_budget", None) if tc is not None else None
        self.calls.append(budget)
        if self.always_fail:
            raise NativeClientError()
        if self.refuses_zero and budget == 0:
            raise NativeClientError()
        return f"answer(budget={budget})"

def zero_kwargs():
    return {"max_output_tokens": 100,
            "thinking_config": genai_types.ThinkingConfig(thinking_budget=0)}

def t_native_recovers():
    ts.reset()
    c = FakeClient()
    out = asyncio.run(_google_generate_content(c, REFUSER, "hi", zero_kwargs()))
    check("a refusal is recovered, not surfaced", out == "answer(budget=None)")
    check("exactly one retry, with the thinking config dropped", c.calls == [0, None], c.calls)
    check("the refusal is recorded only after the retry proved the cause",
          not ts.supports_disabled_thinking(REFUSER))

def t_headroom():
    """A 100-token ceiling sized for 'no thinking' truncates the title once the
    model reasons inside it (measured: gemini-3.6-flash returned '/Draft')."""
    small = {"max_output_tokens": 100}
    check("a ceiling too small for thinking is lifted",
          ts.with_thinking_headroom(small)["max_output_tokens"] == ts.THINKING_HEADROOM_TOKENS)
    big = {"max_output_tokens": 8192}
    check("a generous ceiling is left alone", ts.with_thinking_headroom(big) is big)
    check("no ceiling is left alone", ts.with_thinking_headroom({}) == {})
    check("the caller's mapping is never mutated", small["max_output_tokens"] == 100)

def t_native_retry_gets_headroom():
    ts.reset()
    caps = []
    class C(FakeClient):
        async def generate_content(self, *, model, contents, config):
            caps.append(getattr(config, "max_output_tokens", None))
            return await FakeClient.generate_content(self, model=model, contents=contents, config=config)
    c = C()
    kw = {"max_output_tokens": 100,
          "thinking_config": genai_types.ThinkingConfig(thinking_budget=0)}
    asyncio.run(_google_generate_content(c, REFUSER, "hi", kw))
    check("the retry lifts the session-name ceiling so the title survives",
          caps == [100, ts.THINKING_HEADROOM_TOKENS], caps)

def t_native_no_regression():
    ts.reset()
    c = FakeClient(refuses_zero=False)
    out = asyncio.run(_google_generate_content(c, ACCEPTER, "hi", zero_kwargs()))
    check("a model that accepts 0 still gets 0", c.calls == [0], c.calls)
    check("...and is never recorded as refusing", ts.supports_disabled_thinking(ACCEPTER))
    check("...and its answer is returned untouched", out == "answer(budget=0)")

def t_native_unrelated_400():
    """A 400 with another cause must surface as itself and leave no false claim."""
    ts.reset()
    c = FakeClient(always_fail=True)
    try:
        asyncio.run(_google_generate_content(c, REFUSER, "hi", zero_kwargs()))
        check("an unrelated 400 is raised", False)
    except NativeClientError:
        check("an unrelated 400 is raised, not swallowed", True)
    check("...and the model is NOT recorded as refusing",
          ts.supports_disabled_thinking(REFUSER))

def t_native_only_zero_is_recovered():
    ts.reset()
    c = FakeClient(always_fail=True)
    kw = {"max_output_tokens": 100,
          "thinking_config": genai_types.ThinkingConfig(thinking_budget=1024)}
    try:
        asyncio.run(_google_generate_content(c, REFUSER, "hi", kw))
        check("a non-zero budget failure is not retried", False)
    except NativeClientError:
        check("a non-zero budget failure is not retried", c.calls == [1024], c.calls)

def t_native_consult_skips_doomed_call():
    """Once learned, the doomed argument is never sent again."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/trusted_data_agent/llm/handler.py").read_text()
    check("the handler consults the registry before sending a zero budget",
          "supports_disabled_thinking(model_name)" in src)
    check("the handler routes its google call through the recovery helper",
          "await _google_generate_content(" in src)

# ── the langchain lane (the one that produced the reported error) ───────────
from trusted_data_agent.llm.langchain_adapter import _thinking_aware_google_cls

def _patched_parent(monkey_name, impl):
    from langchain_google_genai import ChatGoogleGenerativeAI
    original = getattr(ChatGoogleGenerativeAI, monkey_name)
    setattr(ChatGoogleGenerativeAI, monkey_name, impl)
    return ChatGoogleGenerativeAI, original

def _mk(model, budget=0):
    return _thinking_aware_google_cls()(
        model=model, google_api_key="k", temperature=0.0,
        include_thoughts=True, thinking_budget=budget)

def t_langchain_agenerate_recovers():
    ts.reset()
    seen = []
    async def fake(self, *a, **kw):
        b = kw.get("thinking_budget", self.thinking_budget)
        seen.append(b)
        if b == 0: raise Exception(REPORTED)
        return "result"
    cls, orig = _patched_parent("_agenerate", fake)
    try:
        out = asyncio.run(_mk(REFUSER)._agenerate([]))
        check("ainvoke path recovers from the reported error", out == "result")
        check("exactly one retry with the budget cleared", seen == [0, None], seen)
        check("the model is recorded", not ts.supports_disabled_thinking(REFUSER))
    finally:
        setattr(cls, "_agenerate", orig)

def t_langchain_agenerate_no_regression():
    ts.reset()
    seen = []
    async def fake(self, *a, **kw):
        seen.append(kw.get("thinking_budget", self.thinking_budget)); return "result"
    cls, orig = _patched_parent("_agenerate", fake)
    try:
        asyncio.run(_mk(ACCEPTER)._agenerate([]))
        check("a model that accepts 0 is called once, with 0", seen == [0], seen)
        check("...and is not recorded", ts.supports_disabled_thinking(ACCEPTER))
    finally:
        setattr(cls, "_agenerate", orig)

def t_langchain_agenerate_unrelated_failure():
    ts.reset()
    async def fake(self, *a, **kw): raise Exception(REPORTED)   # fails both times
    cls, orig = _patched_parent("_agenerate", fake)
    try:
        try:
            asyncio.run(_mk(REFUSER)._agenerate([]))
            check("a persistent failure is raised", False)
        except Exception as e:
            check("a persistent failure surfaces the ORIGINAL error", str(e) == REPORTED)
        check("...and records nothing", ts.supports_disabled_thinking(REFUSER))
    finally:
        setattr(cls, "_agenerate", orig)

def t_langchain_astream_recovers():
    ts.reset()
    seen = []
    async def fake(self, *a, **kw):
        b = kw.get("thinking_budget", self.thinking_budget)
        seen.append(b)
        if b == 0: raise Exception(REPORTED)
        for c in ("a", "b"): yield c
    cls, orig = _patched_parent("_astream", fake)
    try:
        async def drain():
            return [c async for c in _mk(REFUSER)._astream([])]
        check("streaming recovers", asyncio.run(drain()) == ["a", "b"])
        check("streaming retried once with the budget cleared", seen == [0, None], seen)
    finally:
        setattr(cls, "_astream", orig)

def t_langchain_astream_midstream_failure_not_retried():
    """Re-running a stream that already yielded would duplicate the caller's chunks."""
    ts.reset()
    calls = []
    async def fake(self, *a, **kw):
        calls.append(1)
        yield "a"
        raise Exception(REPORTED)
    cls, orig = _patched_parent("_astream", fake)
    try:
        async def drain():
            return [c async for c in _mk(REFUSER)._astream([])]
        try:
            asyncio.run(drain()); check("a mid-stream failure is raised", False)
        except Exception:
            check("a mid-stream failure is raised, never re-streamed", len(calls) == 1, calls)
    finally:
        setattr(cls, "_astream", orig)

def t_langchain_consults_after_learning():
    """A long-lived instance must stop paying the doomed round-trip."""
    ts.reset(); ts.record_disable_refusal(REFUSER)
    llm = _mk(REFUSER)
    cfg = llm._build_thinking_config(thinking_budget=0, include_thoughts=True)
    budget = getattr(cfg, "thinking_budget", None) if cfg is not None else None
    check("a learned refusal drops the budget before the request is built", budget is None)
    check("...while include_thoughts (token accounting) survives",
          cfg is not None and getattr(cfg, "include_thoughts", None) is True)
    keep = _mk(ACCEPTER)._build_thinking_config(thinking_budget=0, include_thoughts=True)
    check("an unlearned model keeps its zero budget",
          getattr(keep, "thinking_budget", None) == 0)

def t_adapter_returns_the_aware_class():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/trusted_data_agent/llm/langchain_adapter.py").read_text()
    check("the adapter builds the self-healing class, not the bare one",
          "_thinking_aware_google_cls()(**llm_kwargs)" in src
          and "return ChatGoogleGenerativeAI(**llm_kwargs)" not in src)

for fn in (t_registry, t_detector, t_headroom, t_native_retry_gets_headroom,
           t_native_recovers, t_native_no_regression, t_native_unrelated_400,
           t_native_only_zero_is_recovered, t_native_consult_skips_doomed_call,
           t_langchain_agenerate_recovers, t_langchain_agenerate_no_regression,
           t_langchain_agenerate_unrelated_failure, t_langchain_astream_recovers,
           t_langchain_astream_midstream_failure_not_retried,
           t_langchain_consults_after_learning, t_adapter_returns_the_aware_class):
    try: fn()
    except Exception as e:
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
