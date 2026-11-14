"""Provider streaming helpers for `call_llm_api` — the Governed Stream (S1), provider half.

Every helper streams the provider's answer, hands each text delta to a *sink*, and
returns an object of EXACTLY the shape the non-streaming branch of `call_llm_api`
already reads (Anthropic `Message`, an OpenAI-style completion, a Gemini
`GenerateContentResponse`, the Ollama `/api/chat` dict, a Bedrock `invoke_model`
response). The parsing code in the handler therefore stays single-source: one
branch per provider reads one shape, streamed or not.

The sink contract (`DeltaSink`) is deliberately tiny and synchronous: `delta(text)`
is called on the event-loop thread for every text chunk; `reset()` is called when
text already delivered must be withdrawn (a retry after an overload error, a model
message that turns out to be a tool call). A bare callable is accepted as a
delta-only sink. Nothing here awaits the sink — the sink coalesces and emits on its
own schedule, so a slow consumer can never slow the provider read.

Honest degradation: a provider (or a Bedrock model family) with no streaming API
takes its non-streaming call and the handler hands the sink the whole text ONCE at
the end (`streamed=False` on the sink's `finish` reading). The consumer sees the
same contract — every byte of the answer passes through the sink in order — and
the assurance surface can say "this provider does not stream" rather than pretend.
"""
from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import time
import types
from typing import Any, Callable, Optional, Protocol, runtime_checkable

app_logger = logging.getLogger("quart.app")


# ---------------------------------------------------------------------------
# Stream activity — the limit on an LLM call is on SILENCE, not on its length
# ---------------------------------------------------------------------------
# A whole-call timeout cannot tell a provider that hangs from one that is still writing:
# a streamed answer longer than the limit was cut while its tokens were arriving. Every
# helper below marks each chunk the provider sends (`note_activity`) — any chunk, not only
# text: a reasoning or tool-input delta is the provider working, and a sink that shows one
# JSON field (`answer_stream.JsonFieldSink`) is silent for the rest of the document. The
# caller's timeout (`await_with_activity_timeout`) counts from the last mark.
class StreamActivity:
    """When the provider last sent something on the streamed call in flight."""
    __slots__ = ("last", "count", "parent")

    def __init__(self, parent: Optional["StreamActivity"] = None):
        self.last: Optional[float] = None
        self.count = 0
        self.parent = parent   # a nested limit must not starve the one outside it

    def tick(self) -> None:
        self.last = time.monotonic()
        self.count += 1
        if self.parent is not None:
            self.parent.tick()


_ACTIVITY: contextvars.ContextVar = contextvars.ContextVar("llm_stream_activity", default=None)


def note_activity(activity: Optional[StreamActivity] = None) -> None:
    """Mark that the provider sent a chunk. A no-op when no limit is armed. `activity` is
    passed explicitly from a worker thread, where the context variable is not visible."""
    a = activity if activity is not None else _ACTIVITY.get()
    if a is not None:
        a.tick()


class LLMCallTimeout(asyncio.TimeoutError):
    """Why an LLM call was stopped: `no_response` (nothing arrived at all), `stalled` (a
    stream went silent), or `ceiling` (still streaming at the absolute bound)."""

    def __init__(self, reason: str, seconds: float, elapsed: float):
        super().__init__(f"LLM call stopped ({reason}) after {elapsed:.1f}s")
        self.reason = reason
        self.seconds = seconds
        self.elapsed = elapsed


class activity_limit:
    """``async with activity_limit(silence, ceiling):`` — the body is stopped when the provider has
    been silent for ``silence`` seconds (counted from the body's start until the first chunk a
    stream helper marks, then from the last mark); ``ceiling`` (0 = none) bounds a body that never
    falls silent. ``silence <= 0`` disables it. On a stop, ``LLMCallTimeout`` is raised from the
    ``async with`` — so the CALLER's own ``except`` (the resilience envelope's attempt loop)
    classifies it like any provider error. Runs in the caller's task; never cancels anything
    outside the body. The one implementation: ``await_with_activity_timeout`` is built on it."""

    __slots__ = ("silence", "ceiling", "_activity", "_token", "_start", "_fired", "_handle", "_scope", "_cm")

    def __init__(self, silence: float, ceiling: float = 0.0):
        self.silence = float(silence or 0)
        self.ceiling = float(ceiling or 0)

    async def __aenter__(self):
        if self.silence <= 0:
            return self
        loop = asyncio.get_running_loop()
        self._activity = StreamActivity(parent=_ACTIVITY.get())
        self._token = _ACTIVITY.set(self._activity)
        self._start = time.monotonic()
        self._fired = {}
        self._cm = asyncio.timeout(None)
        self._scope = await self._cm.__aenter__()
        silence, ceiling, activity, start, fired = self.silence, self.ceiling, self._activity, self._start, self._fired

        def _check() -> None:
            now = time.monotonic()
            if activity.last is None:
                deadline, reason, bound = start + silence, "no_response", silence
            else:
                deadline, reason, bound = activity.last + silence, "stalled", silence
                if ceiling > 0 and start + ceiling <= deadline:
                    deadline, reason, bound = start + ceiling, "ceiling", ceiling
            if now < deadline:
                self._handle = loop.call_later(deadline - now, _check)
                return
            fired.update(reason=reason, seconds=bound, elapsed=now - start)
            try:
                self._scope.reschedule(loop.time())
            except RuntimeError:   # the body finished in this same loop step
                fired.clear()

        self._handle = loop.call_later(silence, _check)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.silence <= 0:
            return False
        try:
            try:
                await self._cm.__aexit__(exc_type, exc, tb)
            except TimeoutError:
                if self._fired:
                    raise LLMCallTimeout(self._fired["reason"], self._fired["seconds"],
                                         self._fired["elapsed"]) from None
                raise
        finally:
            if self._handle is not None:
                self._handle.cancel()
            _ACTIVITY.reset(self._token)
        return False


async def await_with_activity_timeout(awaitable, *, silence: float, ceiling: float = 0.0):
    """Await `awaitable`, stopping it when the provider has been silent for `silence` seconds.

    With no stream activity this is `asyncio.wait_for(awaitable, silence)`: the call is cut
    `silence` seconds after it started. Each chunk a stream helper marks moves the limit to
    `silence` seconds after that chunk, so a call that is still being answered is not cut;
    `ceiling` (0 = none) bounds a stream that never stops. `silence <= 0` disables the limit.

    The awaitable runs in the CALLER's task (as `wait_for` does on Python 3.12): context
    variables it sets — `handler.LAST_CALL_USAGE` — are read by the caller afterwards.
    Raises `LLMCallTimeout` (an `asyncio.TimeoutError`)."""
    if not silence or silence <= 0:
        return await awaitable
    async with activity_limit(silence, ceiling):
        return await awaitable


async def iterate_with_activity_timeout(agen, *, silence: float, ceiling: float = 0.0):
    """Yield from the async iterator `agen`, stopping it when the provider has been silent for
    `silence` seconds between two items (the first item included); `ceiling` (0 = none) bounds
    the whole iteration. Each item counts as activity — a LangChain chunk is the provider working.
    Raises `LLMCallTimeout` and closes `agen`. `silence <= 0` passes `agen` through untouched."""
    if not silence or silence <= 0:
        async for item in agen:
            yield item
        return
    start = time.monotonic()
    got_any = False
    it = agen.__aiter__()
    try:
        while True:
            wait = silence
            if ceiling and ceiling > 0:
                wait = min(wait, max(0.0, start + ceiling - time.monotonic()))
            try:
                item = await asyncio.wait_for(it.__anext__(), timeout=wait)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start
                if ceiling and ceiling > 0 and elapsed >= ceiling - 1e-3 and got_any:
                    raise LLMCallTimeout("ceiling", ceiling, elapsed) from None
                raise LLMCallTimeout("stalled" if got_any else "no_response", silence, elapsed) from None
            got_any = True
            note_activity()
            yield item
    finally:
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass


@runtime_checkable
class DeltaSink(Protocol):
    def delta(self, text: str) -> None: ...
    def reset(self) -> None: ...


class _CallableSink:
    """Adapter: a bare callable is a delta-only sink (reset is a no-op)."""
    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[str], None]):
        self._fn = fn

    def delta(self, text: str) -> None:
        self._fn(text)

    def reset(self) -> None:
        pass


def as_sink(on_delta: Any) -> Optional[DeltaSink]:
    """Normalise `on_delta` (None | callable | DeltaSink) to a DeltaSink or None."""
    if on_delta is None:
        return None
    if hasattr(on_delta, "delta") and hasattr(on_delta, "reset"):
        return on_delta
    if callable(on_delta):
        return _CallableSink(on_delta)
    raise TypeError("on_delta must be None, a callable, or an object with delta()/reset()")


def _safe_delta(sink: DeltaSink, text: str) -> None:
    """A sink failure must never break the provider read (the answer still completes)."""
    if not text:
        return
    try:
        sink.delta(text)
    except Exception as exc:  # noqa: BLE001
        app_logger.debug(f"[stream] sink.delta failed (ignored): {exc}")


# ---------------------------------------------------------------------------
# Anthropic — messages.stream → get_final_message() (same Message shape)
# ---------------------------------------------------------------------------
async def anthropic_stream(llm_instance, sink: DeltaSink, **kw):
    """`messages.stream(**kw)`; deltas from `text_stream`; returns the final `Message`."""
    async with llm_instance.messages.stream(**kw) as stream:
        async for text in stream.text_stream:
            note_activity()
            _safe_delta(sink, text)
        return await stream.get_final_message()


# ---------------------------------------------------------------------------
# OpenAI-compatible — stream=True (+ usage in the final chunk where supported)
# ---------------------------------------------------------------------------
# (provider, model) pairs that REFUSED `stream_options`: learned from the provider's
# own 4xx, never a hardcoded list (the thinking_support precedent) — the next call
# skips the doomed argument and simply reports no usage.
_NO_STREAM_OPTIONS: set = set()


def _looks_like_stream_options_refusal(exc: Exception) -> bool:
    s = str(exc).lower()
    return "stream_options" in s or "stream options" in s


async def openai_compatible_stream(llm_instance, sink: DeltaSink, *, provider: str = "", **kw):
    """`chat.completions.create(stream=True)`; returns a synthesized completion object with
    `choices[0].message.content` + `usage.prompt_tokens/completion_tokens` (None when the
    provider reports no usage — the handler already guards a None usage)."""
    key = (provider, kw.get("model"))
    want_usage = key not in _NO_STREAM_OPTIONS
    try:
        if want_usage:
            stream = await llm_instance.chat.completions.create(
                stream=True, stream_options={"include_usage": True}, **kw)
        else:
            stream = await llm_instance.chat.completions.create(stream=True, **kw)
    except Exception as exc:
        if not (want_usage and _looks_like_stream_options_refusal(exc)):
            raise
        _NO_STREAM_OPTIONS.add(key)
        app_logger.info(f"[stream] {provider}/{kw.get('model')} refuses stream_options — "
                        f"streaming without usage from now on")
        stream = await llm_instance.chat.completions.create(stream=True, **kw)

    parts: list = []
    usage = None
    finish_reason = None
    async for chunk in stream:
        note_activity()
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta is not None else None
            if text:
                parts.append(text)
                _safe_delta(sink, text)
            fr = getattr(choices[0], "finish_reason", None)
            if fr:
                finish_reason = fr   # the last chunk's — `length` / `content_filter` / `stop` (S4 reads it)
        u = getattr(chunk, "usage", None)
        if u is not None:
            usage = u
    text_all = "".join(parts)
    resp = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text_all), finish_reason=finish_reason)],
        usage=(types.SimpleNamespace(prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                                     completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                                     prompt_tokens_details=getattr(usage, "prompt_tokens_details", None))
               if usage is not None else None),
        streamed=True,
    )
    resp.dict = lambda: {"streamed": True, "chars": len(text_all),
                         "usage": None if usage is None else {"prompt_tokens": resp.usage.prompt_tokens,
                                                              "completion_tokens": resp.usage.completion_tokens}}
    return resp


# ---------------------------------------------------------------------------
# Google — aio.models.generate_content_stream (synthesized GenerateContentResponse)
# ---------------------------------------------------------------------------
def _chunk_text(chunk) -> str:
    try:
        t = chunk.text
        if t:
            return t
    except (ValueError, AttributeError):
        pass
    try:
        cands = getattr(chunk, "candidates", None) or []
        parts = getattr(getattr(cands[0], "content", None), "parts", None) if cands else None
        if parts:
            return "".join(getattr(p, "text", "") or "" for p in parts
                           if not getattr(p, "thought", False))
    except Exception:  # noqa: BLE001
        pass
    return ""


class _GoogleStreamedResponse:
    """The parts of a `GenerateContentResponse` the handler reads, assembled from the
    stream: `text` (joined deltas), `candidates` (the LAST chunk's — finish_reason and
    safety ratings live there), `usage_metadata` (the last chunk that carried one)."""

    def __init__(self, text: str, candidates, usage_metadata):
        self.text = text
        self.candidates = candidates
        self.usage_metadata = usage_metadata
        self.streamed = True

    def __repr__(self):
        return f"<GoogleStreamedResponse chars={len(self.text)}>"


async def google_stream(llm_instance, sink: DeltaSink, *, model: str, contents, config):
    it = await llm_instance.aio.models.generate_content_stream(
        model=model, contents=contents, config=config)
    parts: list = []
    candidates = None
    usage = None
    async for chunk in it:
        note_activity()
        t = _chunk_text(chunk)
        if t:
            parts.append(t)
            _safe_delta(sink, t)
        if getattr(chunk, "candidates", None):
            candidates = chunk.candidates
        um = getattr(chunk, "usage_metadata", None)
        if um is not None and (getattr(um, "candidates_token_count", None) or getattr(um, "prompt_token_count", None)):
            usage = um
    return _GoogleStreamedResponse("".join(parts), candidates, usage)


# ---------------------------------------------------------------------------
# Ollama — NDJSON stream over /api/chat (same dict shape as the non-streaming reply)
# ---------------------------------------------------------------------------
async def ollama_stream(http_client, sink: DeltaSink, payload: dict) -> dict:
    payload = dict(payload, stream=True)
    parts: list = []
    final: dict = {}
    async with http_client.stream("POST", "/api/chat", json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            note_activity()
            if not line or not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:  # noqa: BLE001 — a malformed keep-alive line is not an answer
                continue
            t = ((obj.get("message") or {}).get("content")) or ""
            if t:
                parts.append(t)
                _safe_delta(sink, t)
            if obj.get("done"):
                final = obj
    out = dict(final)
    out["message"] = {"role": "assistant", "content": "".join(parts)}
    out.setdefault("prompt_eval_count", 0)
    out.setdefault("eval_count", 0)
    out["streamed"] = True
    return out


# ---------------------------------------------------------------------------
# Bedrock (anthropic family) — invoke_model_with_response_stream, in a thread
# ---------------------------------------------------------------------------
class _BedrockStreamedResponse(dict):
    """`response.get('body').read()` → the JSON the non-streaming branch parses."""

    def __init__(self, body: dict):
        super().__init__(body=io.BytesIO(json.dumps(body).encode("utf-8")), streamed=True)


def bedrock_anthropic_stream_sync(llm_instance, sink: DeltaSink, loop, *, body: str, modelId: str,
                                 activity: Optional[StreamActivity] = None):
    """Runs on a worker thread (boto3 is synchronous). Deltas are marshalled to the loop
    thread with `call_soon_threadsafe` so the sink is only ever touched there. `activity` is
    the caller's stream-activity mark: `run_in_executor` does not carry context variables."""
    response = llm_instance.invoke_model_with_response_stream(body=body, modelId=modelId)
    parts: list = []
    in_tok = out_tok = 0
    cache_read = cache_write = 0   # the prompt-cache reading Claude reports at message start
    for event in response.get("body") or []:
        note_activity(activity)
        chunk = (event or {}).get("chunk") or {}
        raw = chunk.get("bytes")
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        kind = obj.get("type")
        if kind == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                parts.append(d["text"])
                loop.call_soon_threadsafe(_safe_delta, sink, d["text"])
        elif kind == "message_start":
            _u = (obj.get("message") or {}).get("usage") or {}
            in_tok = _u.get("input_tokens", in_tok) or in_tok
            cache_read = _u.get("cache_read_input_tokens", cache_read) or cache_read
            cache_write = _u.get("cache_creation_input_tokens", cache_write) or cache_write
        elif kind == "message_delta":
            out_tok = (obj.get("usage") or {}).get("output_tokens", out_tok) or out_tok
        metrics = obj.get("amazon-bedrock-invocationMetrics") or {}
        if metrics:
            in_tok = metrics.get("inputTokenCount", in_tok) or in_tok
            out_tok = metrics.get("outputTokenCount", out_tok) or out_tok
    return _BedrockStreamedResponse({
        "content": [{"type": "text", "text": "".join(parts)}],
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok,
                  "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write},
    })


async def bedrock_anthropic_stream(llm_instance, sink: DeltaSink, *, body: str, modelId: str):
    loop = asyncio.get_running_loop()
    activity = _ACTIVITY.get()
    return await loop.run_in_executor(
        None, lambda: bedrock_anthropic_stream_sync(llm_instance, sink, loop, body=body, modelId=modelId,
                                                    activity=activity))


def was_streamed(response) -> bool:
    """True when `response` came from one of the helpers above (so the handler knows the
    sink already saw the text and must NOT hand it the whole answer again)."""
    if isinstance(response, dict):
        return bool(response.get("streamed"))
    return bool(getattr(response, "streamed", False))
