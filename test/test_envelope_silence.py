#!/usr/bin/env python3
"""A provider that stops sending is cut INSIDE the resilience envelope — every lane.

The LLM stream timeout (`llm/streaming.py`, adopted from Loom) used to wrap the whole
`call_llm_api` from the executor: a stalled stream was cut outside the envelope, so it was never
retried, never served by the signed fallback, never recorded as a recovery step, and the
LangChain lanes (Ideate+tools, Focus, Coordinate) had no limit at all. This asserts:

  [1] one limit implementation (a context manager; the awaitable and the iterator forms on it);
  [2] the classifier: stalled / no_response → a retryable `timeout`, ceiling → terminal;
  [3] the handler lane: a silent attempt is cut, retried, recorded; with a signed fallback it is
      served by the fallback; with the envelope OFF nothing changes;
  [4] the LangChain lane: the same, for `_agenerate` and `_astream`; a stream that already
      yielded is never re-run;
  [5] the executor's outer limit is a backstop wide enough never to race the envelope.

CI-pure: no server, no network, no LLM. Real (short) waits — 0.2 s limits.
"""
import ast
import asyncio
import pathlib
import sys
import time
import types

sys.path.insert(0, "src")
sys.path.insert(0, ".")
ROOT = pathlib.Path(".")

passed = 0
failures = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)


def run(c):
    return _LOOP.run_until_complete(c)


from trusted_data_agent.llm import streaming as S  # noqa: E402
from trusted_data_agent.llm import resilience as R  # noqa: E402

print("\n[1] one limit implementation")


async def _silent(t):
    await asyncio.sleep(t)
    return "late"


async def _ticking(n, every):
    for _ in range(n):
        await asyncio.sleep(every)
        S.note_activity()
    return "done"


def _raises(coro):
    try:
        run(coro)
        return None
    except Exception as e:  # noqa: BLE001
        return e


# Bounds are wide on purpose (Oct 2026): a 0.15 s silence against 0.05 s ticks tolerated only
# ~0.1 s of scheduling delay and failed under machine load. Ticks stay 10-25x inside the 0.5 s
# bound; every silence lasts a full second past it — the same cases, a margin no hiccup reaches.
async def _cm_body(coro, silence, ceiling=0.0):
    async with S.activity_limit(silence, ceiling):
        return await coro

e = _raises(_cm_body(_silent(1.5), 0.5))
check("a silent body is cut: no_response", isinstance(e, S.LLMCallTimeout) and e.reason == "no_response", e)
check("…and it is an asyncio.TimeoutError (every existing handler still catches it)", isinstance(e, asyncio.TimeoutError))
check("a body that keeps sending is not cut at the silence bound",
      run(_cm_body(_ticking(6, 0.05), 0.5)) == "done")
e = _raises(_cm_body(_ticking(40, 0.05), 0.5, ceiling=1.0))
check("a body that never stops meets the ceiling", isinstance(e, S.LLMCallTimeout) and e.reason == "ceiling", e)


async def _tick_then_silent():
    S.note_activity()
    await asyncio.sleep(1.5)

e = _raises(_cm_body(_tick_then_silent(), 0.5))
check("a stream that falls silent after a chunk: stalled", isinstance(e, S.LLMCallTimeout) and e.reason == "stalled", e)
check("silence 0 disables it", run(_cm_body(_silent(0.05), 0)) == "late")


async def _ctx_after():
    async with S.activity_limit(0.5):
        inside = S._ACTIVITY.get() is not None
    return inside, S._ACTIVITY.get()

_in, _after = run(_ctx_after())
check("the activity context variable is set inside the body and reset after it (same task)",
      _in is True and _after is None, (_in, _after))
check("the awaitable form is built on it", run(S.await_with_activity_timeout(_ticking(4, 0.05), silence=0.5)) == "done"
      and isinstance(_raises(S.await_with_activity_timeout(_silent(1.5), silence=0.5)), S.LLMCallTimeout))


async def _gen(items, gap, first=0.0, closed=None):
    try:
        if first:
            await asyncio.sleep(first)
        for i, it in enumerate(items):
            if i:
                await asyncio.sleep(gap)
            yield it
    finally:
        if closed is not None:
            closed.append(True)


async def _collect(agen):
    out = []
    async for x in agen:
        out.append(x)
    return out

check("the iterator form passes items through",
      run(_collect(S.iterate_with_activity_timeout(_gen([1, 2, 3], 0.02), silence=0.5))) == [1, 2, 3])
e = _raises(_collect(S.iterate_with_activity_timeout(_gen([1], 0, first=1.5), silence=0.5)))
check("…no first item: no_response", isinstance(e, S.LLMCallTimeout) and e.reason == "no_response", e)
closed = []
e = _raises(_collect(S.iterate_with_activity_timeout(_gen([1, 2], 1.5, closed=closed), silence=0.5)))
check("…a gap after an item: stalled", isinstance(e, S.LLMCallTimeout) and e.reason == "stalled", e)
check("…and the source generator is closed", closed == [True])


async def _first_only(closed):
    it = S.iterate_with_activity_timeout(_gen([1, 2, 3], 0.02, closed=closed), silence=0.5)
    async for x in it:
        break
    await it.aclose()
    return list(closed)

check("…a consumer that stops early closes the source at once", run(_first_only([])) == [True])
e = _raises(_collect(S.iterate_with_activity_timeout(_gen(list(range(50)), 0.05), silence=0.5, ceiling=1.0)))
check("…a stream that never stops meets the ceiling", isinstance(e, S.LLMCallTimeout) and e.reason == "ceiling", e)

print("\n[2] the classifier")
for why, kind, reason in (("stalled", R.RETRYABLE, "timeout"), ("no_response", R.RETRYABLE, "timeout"),
                          ("ceiling", R.TERMINAL, "stream_ceiling")):
    c = R.classify(S.LLMCallTimeout(why, 120, 121))
    check(f"{why} → {kind} {reason}", c.kind == kind and c.reason == reason, (c.kind, c.reason))
check("a stalled cut with a signed fallback goes to the fallback now",
      R.route_to_fallback_now(R.classify(S.LLMCallTimeout("stalled", 1, 1)), True))
check("a ceiling cut is never retried", not R.should_retry(R.classify(S.LLMCallTimeout("ceiling", 1, 1)), 1, 5))

print("\n[3] the handler lane")
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.llm import handler as H  # noqa: E402
from trusted_data_agent.agent import resilience_envelope as RE  # noqa: E402
from trusted_data_agent.core import provider_health as PH  # noqa: E402

_saved_cfg = {k: getattr(APP_CONFIG, k, None) for k in ("RESILIENCE_ENVELOPE", "LLM_CALL_TIMEOUT_SECONDS",
                                                         "LLM_STREAM_MAX_SECONDS", "LLM_API_MAX_RETRIES",
                                                         "LLM_API_BASE_DELAY")}
APP_CONFIG.RESILIENCE_ENVELOPE = True
APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS = 0.2
APP_CONFIG.LLM_STREAM_MAX_SECONDS = 0
APP_CONFIG.LLM_API_MAX_RETRIES = 3
APP_CONFIG.LLM_API_BASE_DELAY = 0
_orig_uniform = H.random.uniform
H.random.uniform = lambda a, b: 0.0


class _U:
    prompt_tokens = 11
    completion_tokens = 7
    prompt_tokens_details = None


class _Resp:
    def __init__(s, c):
        s.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=c), finish_reason="stop")]
        s.usage = _U()

    def dict(s):
        return {}


class Fake:
    """An OpenAI-shaped client: each script item is ("hang", seconds) or ("ok", text)."""

    def __init__(s, script):
        s.script = list(script)
        s.calls = 0
        o = s

        class C:
            async def create(self_, **kw):
                o.calls += 1
                item = o.script.pop(0)
                if item[0] == "hang":
                    await asyncio.sleep(item[1])
                    return _Resp("too late")
                return _Resp(item[1])

        class Ch:
            completions = C()
        s.chat = Ch()


def arm(fb=None):
    PH.reset()
    return RE.set_turn(RE.TurnResilience(user_uuid="u", session_id="s", primary_provider="OpenAI",
                                         primary_model="m", fallback=fb))


def _safe_call(f):
    """A call whose failure is reported BY NAME in the check that reads it, never a crash."""
    try:
        return call(f)
    except Exception as e:  # noqa: BLE001
        return (f"raised {type(e).__name__}: {e}", 0, 0, "", "")


def call(f, raise_on_error=True):
    return run(H.call_llm_api(f, "hi", current_provider="OpenAI", current_model="m",
                              raise_on_error=raise_on_error, disabled_history=True))

try:
    tok = arm()
    f = Fake([("hang", 2.0), ("ok", "hello")])
    t0 = time.monotonic()
    r = _safe_call(f)
    st = RE.stamp_for_turn() or {}
    check("a silent attempt is cut inside the envelope and retried — the answer arrives",
          r[0] == "hello" and f.calls == 2, (r[0], f.calls))
    check("…in about the silence bound, not the provider's hang", time.monotonic() - t0 < 1.5)
    check("…recorded as a retry step classified `timeout`",
          (st.get("steps") or [{}])[0].get("kind") == "retry" and st["steps"][0].get("reason") == "timeout", st)
    RE.reset_turn(tok)

    tok = arm()
    f = Fake([("hang", 2.0), ("hang", 2.0), ("ok", "never")])
    e = _raises(H.call_llm_api(f, "hi", current_provider="OpenAI", current_model="m",
                               raise_on_error=True, disabled_history=True))
    check("a hang is retried at most TIMEOUT_MAX_ATTEMPTS times, then the cut is raised",
          isinstance(e, S.LLMCallTimeout) and f.calls == R.TIMEOUT_MAX_ATTEMPTS, (e, f.calls))
    RE.reset_turn(tok)

    fb = {"id": "fb", "provider": "Anthropic", "model": "fb-model", "credentials": {"apiKey": "k"}}

    class AFake:
        def __init__(s):
            s.calls = 0
            s.messages = types.SimpleNamespace(create=s._create)

        async def _create(s, **kw):
            s.calls += 1
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text="from the fallback")], stop_reason="end_turn",
                usage=types.SimpleNamespace(input_tokens=5, output_tokens=3, cache_read_input_tokens=0,
                                            cache_creation_input_tokens=0), dict=lambda: {})
    afake = AFake()

    async def _fbc(fb_, uu):
        return afake
    _orig_fbc = H._fallback_client
    H._fallback_client = _fbc
    try:
        tok = arm(fb)
        f = Fake([("hang", 2.0), ("ok", "primary")])
        r = _safe_call(f)
        st = RE.stamp_for_turn() or {}
        check("with a signed fallback bound, a silent primary is served by the fallback after ONE cut",
              r[0] == "from the fallback" and f.calls == 1 and afake.calls == 1, (r[0], f.calls, afake.calls))
        check("…recorded as a fallback step classified `timeout`",
              any(s.get("kind") == "fallback" and s.get("reason") == "timeout" for s in st.get("steps") or []), st)
        RE.reset_turn(tok)
    finally:
        H._fallback_client = _orig_fbc

    APP_CONFIG.RESILIENCE_ENVELOPE = False
    f = Fake([("hang", 0.5)])
    r = _safe_call(f)
    check("envelope OFF: no per-attempt limit (the executor's whole-call limit stays the only one)",
          r[0] == "too late" and f.calls == 1, (r[0], f.calls))
    APP_CONFIG.RESILIENCE_ENVELOPE = True

    print("\n[4] the LangChain lane")
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from trusted_data_agent.llm import resilient_chat as RC

    class FakeChat(BaseChatModel):
        script: list = []
        calls: int = 0

        @property
        def _llm_type(self):
            return "fake"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise NotImplementedError

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            item = self.script.pop(0)
            if item[0] == "hang":
                await asyncio.sleep(item[1])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content=item[1] if item[0] == "ok" else "late", response_metadata={"finish_reason": "stop"}))])

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            item = self.script.pop(0)
            if item[0] == "hang":
                await asyncio.sleep(item[1])
            if item[0] == "stallmid":
                yield ChatGenerationChunk(message=AIMessageChunk(content="partial "))
                await asyncio.sleep(item[1])
            for w in (item[1].split(" ") if item[0] == "ok" else ["late"]):
                yield ChatGenerationChunk(message=AIMessageChunk(content=w, response_metadata={"finish_reason": "stop"}))

    def _safe_gen(m):
        try:
            return run(m._agenerate([HumanMessage(content="q")]))
        except Exception as e:  # noqa: BLE001
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"raised {type(e).__name__}"))])

    def mk(script):
        m = FakeChat()
        m.script = list(script)
        m.calls = 0
        return m

    tok = arm()
    m = mk([("hang", 2.0), ("ok", "hello")])
    RC.wrap(m, provider="OpenAI", model="m")
    res = _safe_gen(m)
    check("_agenerate: a silent attempt is cut and retried",
          res.generations[0].message.content == "hello" and m.calls == 2, (res.generations[0].message.content, m.calls))
    check("…recorded `timeout`", ((RE.stamp_for_turn() or {}).get("steps") or [{}])[0].get("reason") == "timeout")
    RE.reset_turn(tok)

    tok = arm()
    m = mk([("hang", 2.0), ("ok", "a b")])
    RC.wrap(m, provider="OpenAI", model="m")
    try:
        chunks = run(_collect(m._astream([HumanMessage(content="q")])))
    except Exception:  # noqa: BLE001
        chunks = []
    check("_astream: no first chunk → cut and retried",
          "".join(c.message.content for c in chunks) == "ab" and m.calls == 2, m.calls)
    RE.reset_turn(tok)

    tok = arm()
    m = mk([("stallmid", 2.0), ("ok", "never")])
    RC.wrap(m, provider="OpenAI", model="m")
    e = _raises(_collect(m._astream([HumanMessage(content="q")])))
    check("_astream: a stream that YIELDED and then stalled is cut, never re-run",
          isinstance(e, S.LLMCallTimeout) and e.reason == "stalled" and m.calls == 1, (e, m.calls))
    RE.reset_turn(tok)

    fbm = mk([("ok", "from fb")])
    tok = arm()
    m = mk([("hang", 2.0), ("hang", 2.0)])
    RC.wrap(m, provider="OpenAI", model="m", fallback_factory=lambda: fbm,
            fallback_identity={"provider": "Anthropic", "model": "fb"})
    res = _safe_gen(m)
    check("_agenerate: silent attempts exhausted → the signed fallback serves",
          res.generations[0].message.content == "from fb" and m.calls == R.TIMEOUT_MAX_ATTEMPTS, m.calls)
    RE.reset_turn(tok)

    src = (ROOT / "src/trusted_data_agent/llm/resilient_chat.py").read_text()
    check("every provider call in the wrapper runs under the limit (attempt, continuation, fallback)",
          src.count("_limited(") >= 4 and src.count("_limited_stream(") >= 3, (src.count("_limited("), src.count("_limited_stream(")))
finally:
    H.random.uniform = _orig_uniform
    for k, v in _saved_cfg.items():
        setattr(APP_CONFIG, k, v)

print("\n[5] the executor's outer limit is a backstop")
ex = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
fn = next(n for n in ast.walk(ast.parse(ex)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_call_llm_and_update_tokens")
body = ast.get_source_segment(ex, fn)
check("with the envelope on, the outer limit is widened past the envelope's worst case",
      "llm_handler._resilience_enabled()" in body and "TIMEOUT_MAX_ATTEMPTS + 1" in body and "MAX_BACKOFF_SECONDS" in body)
check("…and the outer call uses the widened figures", "silence=_outer, ceiling=_outer_ceiling" in body)
hs = (ROOT / "src/trusted_data_agent/llm/handler.py").read_text()
check("the handler's attempt body runs inside the per-attempt limit", "async with _attempt_limit():" in hs)
check("…which is a no-op with the envelope off", "if not _res_on:\n            return contextlib.nullcontext()" in hs)

print("\n" + "=" * 68)
print(f"RESULT: {'FAILURES' if failures else 'ALL PASS'}  ({passed} passed, {len(failures)} failed)")
print("=" * 68)
sys.exit(1 if failures else 0)
