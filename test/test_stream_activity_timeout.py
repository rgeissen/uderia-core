"""The limit on an LLM call is on the provider's SILENCE, not on the length of the call.

`executor._call_llm_and_update_tokens` wrapped every call in `asyncio.wait_for(120 s)`. That
bound is right for a call that hangs and wrong for a streamed answer: an answer still being
written at 120 s was cut although its tokens were arriving (a long report, a slow local model).
The limit now counts from the last chunk the provider sent.

Drives the real timeout helper, the real provider stream helpers over fake wires, and the real
executor method. No server, no network, no API spend. Times are scaled down (0.15 s for 120 s).
"""
import ast
import asyncio
import contextvars
import json
import logging
import os
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)

from trusted_data_agent.llm import streaming as S   # noqa: E402

PASS = FAIL = 0
ROOT = os.path.join(os.path.dirname(__file__), "..")


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f": {detail}" if detail is not None else ""))


def run(coro):
    return asyncio.run(coro)


SILENCE = 0.15


async def streaming_call(total, every=0.03, then_silent=0.0, result="done"):
    """A call whose provider sends a chunk every `every` s for `total` s, then goes silent."""
    end = time.monotonic() + total
    while time.monotonic() < end:
        S.note_activity()
        await asyncio.sleep(every)
    if then_silent:
        await asyncio.sleep(then_silent)
    return result


async def outcome(coro_fn, **kw):
    """(value | exception, elapsed) of one guarded call."""
    t0 = time.monotonic()
    try:
        v = await S.await_with_activity_timeout(coro_fn(), **kw)
    except BaseException as exc:  # noqa: BLE001
        return exc, time.monotonic() - t0
    return v, time.monotonic() - t0


print("[1] a call that streams nothing is cut where wait_for cut it")
out, dt = run(outcome(lambda: asyncio.sleep(1.0, "late"), silence=SILENCE))
check("no chunk at all → stopped as `no_response`", isinstance(out, S.LLMCallTimeout) and out.reason == "no_response", out)
check("…`silence` seconds after the call started", SILENCE - 0.02 <= dt <= SILENCE + 0.15, dt)
check("…and it is an asyncio.TimeoutError (the executor's existing handler catches it)", isinstance(out, asyncio.TimeoutError))
out, dt = run(outcome(lambda: asyncio.sleep(0.02, "in time"), silence=SILENCE))
check("a call that answers in time returns its value", out == "in time", out)


async def _raises():
    raise ValueError("provider said no")
out, _ = run(outcome(_raises, silence=SILENCE))
check("the call's own exception passes through unchanged", isinstance(out, ValueError) and str(out) == "provider said no", out)


async def _inner_timeout():
    raise TimeoutError("read timed out inside the SDK")
out, _ = run(outcome(_inner_timeout, silence=SILENCE))
check("a TimeoutError raised INSIDE the call is not relabelled as ours", type(out) is TimeoutError, type(out))
out, dt = run(outcome(lambda: asyncio.sleep(0.3, "unbounded"), silence=0))
check("silence <= 0 disables the limit", out == "unbounded" and dt >= 0.29, (out, dt))

print("[2] a streamed answer still arriving is not cut")
LONG = SILENCE * 4


async def _before():
    try:
        return await asyncio.wait_for(streaming_call(LONG), timeout=SILENCE)
    except asyncio.TimeoutError:
        return "cut"
check("the old whole-call limit cut this answer mid-stream (the defect)", run(_before()) == "cut")
out, dt = run(outcome(lambda: streaming_call(LONG), silence=SILENCE))
check("the same answer, four limits long, now completes", out == "done" and dt >= LONG - 0.02, (out, dt))

print("[3] a stream that falls silent is still stopped")
out, dt = run(outcome(lambda: streaming_call(0.2, then_silent=1.0), silence=SILENCE))
check("stopped as `stalled`", isinstance(out, S.LLMCallTimeout) and out.reason == "stalled", out)
check("…`silence` seconds after the LAST chunk, not after the start", 0.2 + SILENCE - 0.05 <= dt <= 0.2 + SILENCE + 0.2, dt)
check("the record carries the silence allowed and the time the call ran", isinstance(out, S.LLMCallTimeout) and out.seconds == SILENCE and out.elapsed >= 0.2 + SILENCE - 0.05, getattr(out, "elapsed", None))

print("[4] a stream that never stops meets the ceiling")
out, dt = run(outcome(lambda: streaming_call(5.0), silence=SILENCE, ceiling=0.4))
check("stopped as `ceiling`", isinstance(out, S.LLMCallTimeout) and out.reason == "ceiling" and out.seconds == 0.4, out)
check("…at the ceiling", 0.38 <= dt <= 0.6, dt)
out, dt = run(outcome(lambda: asyncio.sleep(1.0), silence=SILENCE, ceiling=0.05))
check("the ceiling never shortens a call that has streamed nothing", isinstance(out, S.LLMCallTimeout) and out.reason == "no_response" and dt >= SILENCE - 0.02, (out, dt))
out, _ = run(outcome(lambda: streaming_call(0.5), silence=SILENCE, ceiling=0))
check("ceiling 0 = no ceiling", out == "done", out)

print("[5] the call runs in the caller's task; the mark is scoped to the call")
SIDE = contextvars.ContextVar("side_channel", default=None)


async def _same_task():
    async def call():
        SIDE.set({"cached_tokens": 7})   # what handler.LAST_CALL_USAGE does
        return "ok"
    await S.await_with_activity_timeout(call(), silence=SILENCE)
    return SIDE.get()
check("a context variable the call sets is read by the caller afterwards (no task boundary)", run(_same_task()) == {"cached_tokens": 7})


async def _scoped():
    seen = []

    async def call():
        seen.append(S._ACTIVITY.get())
        return 1
    before = S._ACTIVITY.get()
    await S.await_with_activity_timeout(call(), silence=SILENCE)
    try:
        await S.await_with_activity_timeout(asyncio.sleep(1.0), silence=0.05)
    except asyncio.TimeoutError:
        pass
    return before, seen[0], S._ACTIVITY.get()
b, during, after = run(_scoped())
check("armed during the call, cleared after it — also after a timeout", b is None and isinstance(during, S.StreamActivity) and after is None, (b, during, after))
S.note_activity()
check("a mark with no limit armed is a no-op", True)


async def _outer_cancel():
    state = {}

    async def call():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            state["inner_cancelled"] = True
            raise
    task = asyncio.ensure_future(S.await_with_activity_timeout(call(), silence=5))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        state["outer"] = "cancelled"
    except asyncio.TimeoutError:
        state["outer"] = "timeout"
    return state
st = run(_outer_cancel())
check("a Stop from outside is a cancellation, never a timeout — and reaches the call", st == {"inner_cancelled": True, "outer": "cancelled"}, st)


async def _nested():
    async def inner_call():
        return await streaming_call(SILENCE * 3)
    async def outer_call():
        return await S.await_with_activity_timeout(inner_call(), silence=SILENCE)
    try:
        return await S.await_with_activity_timeout(outer_call(), silence=SILENCE)
    except asyncio.TimeoutError as exc:
        return exc
_n = run(_nested())
check("a nested limit does not starve the one outside it", _n == "done", _n)

print("[6] every provider stream helper marks each chunk the provider sends")


class Rec:
    def __init__(self): self.d = []
    def delta(self, t): self.d.append(t)
    def reset(self): self.d.clear()


async def marks(fn):
    act = S.StreamActivity()
    tok = S._ACTIVITY.set(act)
    try:
        rec = Rec()
        out = await fn(rec)
    finally:
        S._ACTIVITY.reset(tok)
    return act.count, "".join(rec.d), out


def _oa_chunk(text=None, usage=None, finish=None, extra=False):
    delta = types.SimpleNamespace(content=text)
    if extra:
        delta.reasoning = "thinking…"
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage)


class _OAStream:
    def __init__(self, chunks): self._c = chunks
    def __aiter__(self):
        async def gen():
            for c in self._c:
                yield c
        return gen()


class FakeOpenAI:
    def __init__(self, chunks):
        async def create(**kw): return _OAStream(chunks)
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))


oa = [_oa_chunk(extra=True), _oa_chunk(extra=True), _oa_chunk("Hel"), _oa_chunk("lo", finish="stop"),
      types.SimpleNamespace(choices=[], usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2))]
n, text, _ = run(marks(lambda rec: S.openai_compatible_stream(FakeOpenAI(oa), rec, provider="X", model="m", messages=[])))
check("OpenAI-compatible: 5 chunks → 5 marks, though only 2 carried text (reasoning and usage chunks count)", n == 5 and text == "Hello", (n, text))


class FakeGoogle:
    def __init__(self, chunks):
        async def gcs(model, contents, config):
            async def gen():
                for c in chunks:
                    yield c
            return gen()
        self.aio = types.SimpleNamespace(models=types.SimpleNamespace(generate_content_stream=gcs))


def _g(text, thought=False):
    part = types.SimpleNamespace(text=text, thought=thought)
    return types.SimpleNamespace(text=None if thought else text, usage_metadata=None,
                                 candidates=[types.SimpleNamespace(content=types.SimpleNamespace(parts=[part]))])


n, text, _ = run(marks(lambda rec: S.google_stream(FakeGoogle([_g("pondering", True), _g("pondering", True), _g("42")]), rec, model="m", contents=[], config=None)))
check("Google: thought-only chunks are marks too", n == 3 and text == "42", (n, text))


class _AStream:
    def __init__(self, texts): self._t = texts
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    @property
    def text_stream(self):
        async def gen():
            for t in self._t:
                yield t
        return gen()
    async def get_final_message(self): return "final"


class FakeAnthropic:
    def __init__(self, texts): self.messages = types.SimpleNamespace(stream=lambda **kw: _AStream(texts))


n, text, out = run(marks(lambda rec: S.anthropic_stream(FakeAnthropic(["a", "b", "c"]), rec, model="m")))
check("Anthropic: one mark per text delta", n == 3 and text == "abc" and out == "final", (n, text, out))


class FakeOllamaHTTP:
    def __init__(self, lines): self._l = lines
    def stream(self, method, path, json=None):
        lines = self._l

        class R:
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
            def raise_for_status(s): pass
            async def aiter_lines(s):
                for ln in lines:
                    yield ln
        return R()


ol = [json.dumps({"message": {"content": "lo"}}), "", json.dumps({"message": {"content": "cal"}}), json.dumps({"done": True})]
n, text, _ = run(marks(lambda rec: S.ollama_stream(FakeOllamaHTTP(ol), rec, {"model": "m"})))
check("Ollama: every line the server sends is a mark", n == 4 and text == "local", (n, text))


class FakeBedrock:
    def __init__(self): self.thread = None
    def invoke_model_with_response_stream(self, body, modelId):
        import threading
        self.thread = threading.current_thread()
        ev = lambda o: {"chunk": {"bytes": json.dumps(o).encode()}}   # noqa: E731
        return {"body": [ev({"type": "message_start", "message": {"usage": {"input_tokens": 3}}}),
                         ev({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "bed"}}),
                         ev({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "rock"}}),
                         ev({"type": "message_delta", "usage": {"output_tokens": 2}})]}


async def _bedrock(rec):
    import threading
    fb = FakeBedrock()
    out = await S.bedrock_anthropic_stream(fb, rec, body="{}", modelId="anthropic.x")
    await asyncio.sleep(0)   # let the marshalled deltas land
    return fb.thread is not threading.current_thread(), out
n, text, (off_thread, _) = run(marks(_bedrock))
check("Bedrock: marks made on the worker thread reach the caller's limit", n == 4 and text == "bedrock" and off_thread, (n, text, off_thread))

print("[7] the executor seam: the real _call_llm_and_update_tokens")
from trusted_data_agent.agent import executor as EX   # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG   # noqa: E402


class FakeSelf:
    """Only what the method reads before and after the provider call."""
    def __init__(self):
        self.turn_cost_usd = 0.0
        self.disabled_history = False
        self.current_provider, self.current_model = "Fake", "fake-1"
        self.is_dual_model_active = False
        self.strategic_llm_instance = self.tactical_llm_instance = None
        self.profile_llm_instance = object()
        self.dependencies = {"STATE": {"llm": object()}}
        self.user_uuid, self.session_id, self.active_profile_id = "u", "s", "p"
        self.thinking_budget = None
        self.llm_debug_history = []
        self.turn_input_tokens = self.turn_output_tokens = 0
        self.turn_cached_tokens = self.turn_cache_write_tokens = 0

    def _get_current_profile_tag(self):
        return None


def executor_call(provider_call, *, timeout, ceiling=0):
    saved = (EX.llm_handler.call_llm_api, APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS, getattr(APP_CONFIG, "LLM_STREAM_MAX_SECONDS", 0),
             EX.session_manager.update_models_used)

    async def fake_call_llm_api(llm_instance, prompt, **kw):
        return await provider_call()

    async def no_session_write(**kw):   # the suite never touches a real session
        return None
    EX.llm_handler.call_llm_api = fake_call_llm_api
    EX.session_manager.update_models_used = no_session_write
    APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS, APP_CONFIG.LLM_STREAM_MAX_SECONDS = timeout, ceiling
    fx = FakeSelf()
    try:
        async def go():
            return await EX.PlanExecutor._call_llm_and_update_tokens(fx, "q", "test")
        try:
            return run(go()), fx
        except BaseException as exc:  # noqa: BLE001
            return exc, fx
    finally:
        (EX.llm_handler.call_llm_api, APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS, APP_CONFIG.LLM_STREAM_MAX_SECONDS,
         EX.session_manager.update_models_used) = saved


FIVE = ("the answer", 11, 4, "Fake", "fake-1")


async def _long_stream():
    await streaming_call(SILENCE * 4)
    return FIVE
out, fx = executor_call(_long_stream, timeout=SILENCE)
check("a streamed answer four limits long is delivered, and its tokens are counted", isinstance(out, tuple) and out[0] == "the answer" and fx.turn_input_tokens == 11 and fx.turn_output_tokens == 4, out)


async def _hang():
    await asyncio.sleep(2)
    return FIVE
out, _ = executor_call(_hang, timeout=SILENCE)
check("a call that never answers: the message it always had", isinstance(out, RuntimeError) and f"did not respond within {SILENCE} seconds" in str(out), out)


async def _stall():
    await streaming_call(0.1, then_silent=2)
    return FIVE
out, _ = executor_call(_stall, timeout=SILENCE)
check("a stream that falls silent says so, not 'did not respond'", isinstance(out, RuntimeError) and "stopped sending its answer" in str(out) and "did not respond" not in str(out), out)


async def _endless():
    await streaming_call(5)
    return FIVE
out, _ = executor_call(_endless, timeout=SILENCE, ceiling=0.4)
check("an endless stream is stopped at the ceiling and says so", isinstance(out, RuntimeError) and "still being written after 0.4 seconds" in str(out), out)

src = open(os.path.join(ROOT, "src/trusted_data_agent/agent/executor.py")).read()
fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_call_llm_and_update_tokens")
calls = [ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)]
check("no whole-call wait_for remains on the provider call", "asyncio.wait_for" not in calls and "_streaming.await_with_activity_timeout" in calls, [c for c in calls if "wait" in c])
check("APP_CONFIG carries the ceiling, above the silence limit", APP_CONFIG.LLM_STREAM_MAX_SECONDS == 0 or APP_CONFIG.LLM_STREAM_MAX_SECONDS > APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS,
      (APP_CONFIG.LLM_STREAM_MAX_SECONDS, APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
