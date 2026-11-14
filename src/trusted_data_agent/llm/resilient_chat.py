"""The Resilience Envelope on the LangChain lanes (S4) — `wrap(model)` gives a LangChain chat
model the SAME recovery policy the handler applies (`llm/resilience.py`): retry what a retry can
change (honouring retry-after), fail fast on the terminal, continue a truncated answer (bounded),
serve from the SIGNED fallback when the primary is exhausted, and turn a provider refusal into an
honest answer — every act recorded into the turn (`agent/resilience_envelope.py`) and the
provider's health into the breaker.

Mechanism (the `_ThinkingAwareChatGoogle` precedent, generalised): the instance is re-classed to a
per-provider-class subclass built lazily — `object.__setattr__(inst, "__class__", Sub)` — so the
provider's own constructor, fields and `bind_tools` are untouched; only `_agenerate` / `_astream`
/ `_generate` are enveloped. The tools the agent bound are remembered on `bind_tools`, so a
fallback to ANOTHER provider re-binds them in that provider's own format (a provider-formatted
`tools` kwarg from the primary would be rejected by the fallback).

Two honest limits: a stream that has already yielded is never re-run (the caller holds those
chunks; an error mid-stream propagates), and a fallback call runs through the fallback model's
`_agenerate` / `_astream` directly (no nested LangChain run) so the lane's callbacks see ONE
model call — priced at the model that served, which the message's `response_metadata` names
(`uderia_served_by`).
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from trusted_data_agent.llm import resilience as _res
from trusted_data_agent.llm import streaming as _streaming

_log = logging.getLogger("quart.app")
_SUBCLASSES: Dict[type, type] = {}


def _env():
    from trusted_data_agent.agent import resilience_envelope as _renv
    return _renv


def _health():
    from trusted_data_agent.core import provider_health as _ph
    return _ph


def _knobs():
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return int(getattr(APP_CONFIG, "LLM_API_MAX_RETRIES", _res.DEFAULT_MAX_RETRIES)), float(getattr(APP_CONFIG, "LLM_API_BASE_DELAY", _res.DEFAULT_BASE_DELAY))
    except Exception:  # noqa: BLE001
        return _res.DEFAULT_MAX_RETRIES, _res.DEFAULT_BASE_DELAY


def _limits():
    """(silence, ceiling) for ONE provider call — the same LLM_CALL_TIMEOUT_SECONDS /
    LLM_STREAM_MAX_SECONDS the handler lane applies per attempt (llm/streaming.py), so a provider
    that stops sending is cut as a classifiable `timeout` this envelope retries or serves from
    the signed fallback, on every lane alike."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return (float(getattr(APP_CONFIG, "LLM_CALL_TIMEOUT_SECONDS", 0) or 0),
                float(getattr(APP_CONFIG, "LLM_STREAM_MAX_SECONDS", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


async def _limited(coro):
    silence, ceiling = _limits()
    return await _streaming.await_with_activity_timeout(coro, silence=silence, ceiling=ceiling)


def _limited_stream(agen):
    silence, ceiling = _limits()
    return _streaming.iterate_with_activity_timeout(agen, silence=silence, ceiling=ceiling)


def _breaker_gate(env, ph, renv, max_retries):
    """S4 (review): the LangChain lanes ask the breaker BEFORE the first attempt, exactly as the
    handler lane does. Returns `(max_retries, gate)`: gate == "fallback" ⇒ the breaker is OPEN
    (or a probe is in flight) and a signed fallback is bound — serve it now, the timeout is never
    paid; open without a fallback ⇒ ONE attempt, no backoff; HALF_OPEN ⇒ the probe is ONE call.
    Fails open to the plain schedule."""
    try:
        if not ph.allows(env.provider):
            if env.fallback_model() is not None:
                snap = ph.get(env.provider).snapshot()
                renv.record(_res.RecoveryStep(kind=_res.STEP_FALLBACK, reason="breaker_open", from_model=env.label, to_model=env.fallback_label(),
                                              detail=f"{env.provider} breaker {snap.get('state')} ({snap.get('last_reason') or 'failures'})"))
                return max_retries, "fallback"
            return 1, "once"
        if ph.get(env.provider).snapshot().get("state") == "half_open":
            return 1, "probe"
    except Exception:  # noqa: BLE001
        pass
    return max_retries, "run"


def _finish_of(msg) -> Any:
    md = getattr(msg, "response_metadata", None) or {}
    return md.get("finish_reason") or md.get("stop_reason") or md.get("done_reason")


def _has_tool_calls(msg) -> bool:
    return bool(getattr(msg, "tool_calls", None))


def _honest_result(category: str, provider: str, model: str):
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    text = _res.refusal_answer(category, provider, model)
    msg = AIMessage(content=text, response_metadata={"finish_reason": "stop", "uderia_refusal": category},
                    usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    return ChatResult(generations=[ChatGeneration(message=msg)])


def _stamp_served_by(result, provider: str, model: str):
    try:
        for g in getattr(result, "generations", []) or []:
            m = getattr(g, "message", None)
            if m is not None:
                md = dict(getattr(m, "response_metadata", None) or {})
                md["uderia_served_by"] = {"provider": provider, "model": model}
                m.response_metadata = md
    except Exception:  # noqa: BLE001
        pass
    return result


class _Envelope:
    """Per-instance state, kept in `__dict__` under one key so pydantic never sees it."""

    def __init__(self, provider: str, model: str, fallback_factory: Optional[Callable[[], Any]],
                 fallback_identity: Optional[Dict[str, str]]):
        self.provider = provider
        self.model = model
        self.label = _res.model_label(provider, model)
        self.fallback_factory = fallback_factory
        self.fallback_identity = fallback_identity or {}
        self.raw_tools: Optional[List[Any]] = None
        self.tool_kwargs: Dict[str, Any] = {}
        self._fallback_model = None

    def fallback_model(self):
        if self._fallback_model is None and self.fallback_factory is not None:
            self._fallback_model = self.fallback_factory()
        return self._fallback_model

    def fallback_label(self) -> str:
        return _res.model_label(self.fallback_identity.get("provider"), self.fallback_identity.get("model"))

    def fallback_call_kwargs(self, fb_model, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """The primary's provider-formatted tool kwargs are dropped; the remembered raw tools
        are re-bound in the FALLBACK provider's format."""
        kw = {k: v for k, v in kwargs.items() if k not in ("tools", "tool_choice", "functions", "function_call", "parallel_tool_calls")}
        if self.raw_tools:
            try:
                bound = fb_model.bind_tools(self.raw_tools, **self.tool_kwargs)
                kw.update(getattr(bound, "kwargs", {}) or {})
            except Exception as e:  # noqa: BLE001
                _log.warning(f"[resilience] could not re-bind tools on the fallback: {e}")
        return kw


def envelope_of(inst) -> Optional[_Envelope]:
    return inst.__dict__.get("_uderia_envelope") if hasattr(inst, "__dict__") else None


def _subclass_for(cls: type) -> type:
    sub = _SUBCLASSES.get(cls)
    if sub is not None:
        return sub

    class _Resilient(cls):  # type: ignore[misc,valid-type]
        # ── tools ────────────────────────────────────────────────────────────
        def bind_tools(self, tools, **kwargs):
            env = envelope_of(self)
            if env is not None:
                env.raw_tools = list(tools) if tools is not None else None
                env.tool_kwargs = dict(kwargs)
            return super().bind_tools(tools, **kwargs)

        # ── the non-streamed call ─────────────────────────────────────────────
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            env = envelope_of(self)
            if env is None:
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            parent = super()._agenerate
            renv, ph = _env(), _health()
            max_retries, base = _knobs()
            max_retries, gate = _breaker_gate(env, ph, renv, max_retries)
            if gate == "fallback":
                fb = env.fallback_model()
                result = await _limited(fb._agenerate(messages, stop=stop, run_manager=run_manager, **env.fallback_call_kwargs(fb, kwargs)))
                return _stamp_served_by(result, env.fallback_identity.get("provider", ""), env.fallback_identity.get("model", ""))
            attempt = 0
            last_exc = None
            while attempt < max_retries:
                attempt += 1
                try:
                    result = await _limited(parent(messages, stop=stop, run_manager=run_manager, **kwargs))
                except Exception as exc:
                    cls_ = _res.classify(exc)
                    if cls_.kind == _res.REFUSAL:
                        ph.record_success(env.provider)
                        renv.record(_res.RecoveryStep(kind=_res.STEP_REFUSAL, reason=cls_.category or "refusal", from_model=env.label, to_model=env.label, detail=str(exc)[:200]))
                        return _honest_result(cls_.category or "refusal", env.provider, env.model)
                    if not cls_.retryable:
                        raise
                    last_exc = exc
                    ph.record_failure(env.provider, cls_.reason)
                    if _res.should_retry(cls_, attempt, max_retries):
                        delay = _res.backoff(attempt, base=base, retry_after=cls_.retry_after, jitter=random.uniform(0, 1))
                        renv.record(_res.RecoveryStep(kind=_res.STEP_RETRY, reason=cls_.reason, from_model=env.label, to_model=env.label, attempt=attempt + 1, delay=delay, status=cls_.status, detail=str(exc)[:200]))
                        await asyncio.sleep(delay)
                        continue
                    break
                ph.record_success(env.provider)
                msg = result.generations[0].message if result.generations else None
                fin = _finish_of(msg)
                if msg is not None and _res.finish_kind(fin) == _res.REFUSED:
                    renv.record(_res.RecoveryStep(kind=_res.STEP_REFUSAL, reason=_res.refusal_category(fin), from_model=env.label, to_model=env.label))
                    return _honest_result(_res.refusal_category(fin), env.provider, env.model)
                # a truncated FINAL answer (no tool calls) is continued, bounded
                conts = 0
                parts: List[str] = []
                while msg is not None and not _has_tool_calls(msg) and _res.should_continue(fin, conts, str(getattr(msg, "content", "") or "")):
                    from langchain_core.messages import AIMessage, HumanMessage
                    parts.append(str(msg.content))
                    conts += 1
                    renv.record(_res.RecoveryStep(kind=_res.STEP_CONTINUATION, reason=str(fin), from_model=env.label, to_model=env.label, attempt=conts))
                    more = await _limited(parent(list(messages) + [AIMessage(content=msg.content), HumanMessage(content=_res.CONTINUE_INSTRUCTION)],
                                                 stop=stop, run_manager=run_manager, **kwargs))
                    msg2 = more.generations[0].message if more.generations else None
                    if msg2 is None:
                        break
                    msg, fin, result = msg2, _finish_of(msg2), more
                if parts:
                    msg.content = _res.join_continuation(parts + [str(msg.content)])
                return result
            # exhausted on a retryable error: the signed fallback, else the error
            fb = env.fallback_model()
            if fb is not None:
                cls_ = _res.classify(last_exc) if last_exc else None
                renv.record(_res.RecoveryStep(kind=_res.STEP_FALLBACK, reason=(cls_.reason if cls_ else "exhausted"), from_model=env.label,
                                              to_model=env.fallback_label(), status=(cls_.status if cls_ else None), detail=str(last_exc)[:200]))
                _log.warning(f"[resilience] {env.label} exhausted; serving from the signed fallback {env.fallback_label()}")
                result = await _limited(fb._agenerate(messages, stop=stop, run_manager=run_manager, **env.fallback_call_kwargs(fb, kwargs)))
                return _stamp_served_by(result, env.fallback_identity.get("provider", ""), env.fallback_identity.get("model", ""))
            raise last_exc

        # ── the streamed call ────────────────────────────────────────────────
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            env = envelope_of(self)
            if env is None:
                async for c in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yield c
                return
            parent = super()._astream
            renv, ph = _env(), _health()
            max_retries, base = _knobs()
            max_retries, gate = _breaker_gate(env, ph, renv, max_retries)
            if gate == "fallback":
                async for chunk in self._stream_fallback(env, messages, stop, run_manager, kwargs):
                    yield chunk
                return
            attempt = 0
            last_exc = None
            while attempt < max_retries:
                attempt += 1
                emitted = False
                last_chunk = None
                text_parts: List[str] = []
                saw_tool_calls = False
                try:
                    async for chunk in _limited_stream(parent(messages, stop=stop, run_manager=run_manager, **kwargs)):
                        emitted = True
                        last_chunk = chunk
                        m = getattr(chunk, "message", None)
                        if m is not None:
                            c = getattr(m, "content", "")
                            if isinstance(c, str) and c:
                                text_parts.append(c)
                            if getattr(m, "tool_call_chunks", None) or getattr(m, "tool_calls", None):
                                saw_tool_calls = True
                        yield chunk
                except Exception as exc:
                    cls_ = _res.classify(exc)
                    if emitted:
                        raise   # the caller holds the chunks: never re-run a stream that yielded
                    if cls_.kind == _res.REFUSAL:
                        ph.record_success(env.provider)
                        renv.record(_res.RecoveryStep(kind=_res.STEP_REFUSAL, reason=cls_.category or "refusal", from_model=env.label, to_model=env.label, detail=str(exc)[:200]))
                        from langchain_core.messages import AIMessageChunk
                        from langchain_core.outputs import ChatGenerationChunk
                        yield ChatGenerationChunk(message=AIMessageChunk(content=_res.refusal_answer(cls_.category or "refusal", env.provider, env.model),
                                                                         response_metadata={"finish_reason": "stop", "uderia_refusal": cls_.category}))
                        return
                    if not cls_.retryable:
                        raise
                    last_exc = exc
                    ph.record_failure(env.provider, cls_.reason)
                    if _res.should_retry(cls_, attempt, max_retries):
                        delay = _res.backoff(attempt, base=base, retry_after=cls_.retry_after, jitter=random.uniform(0, 1))
                        renv.record(_res.RecoveryStep(kind=_res.STEP_RETRY, reason=cls_.reason, from_model=env.label, to_model=env.label, attempt=attempt + 1, delay=delay, status=cls_.status, detail=str(exc)[:200]))
                        await asyncio.sleep(delay)
                        continue
                    break
                ph.record_success(env.provider)
                # the stream ended: a truncated FINAL answer is continued, bounded
                fin = _finish_of(getattr(last_chunk, "message", None)) if last_chunk is not None else None
                if fin is not None and _res.finish_kind(fin) == _res.REFUSED and not text_parts:
                    renv.record(_res.RecoveryStep(kind=_res.STEP_REFUSAL, reason=_res.refusal_category(fin), from_model=env.label, to_model=env.label))
                    from langchain_core.messages import AIMessageChunk
                    from langchain_core.outputs import ChatGenerationChunk
                    yield ChatGenerationChunk(message=AIMessageChunk(content=_res.refusal_answer(_res.refusal_category(fin), env.provider, env.model),
                                                                     response_metadata={"finish_reason": "stop", "uderia_refusal": _res.refusal_category(fin)}))
                    return
                conts = 0
                partial = "".join(text_parts)
                while not saw_tool_calls and _res.should_continue(fin, conts, partial):
                    from langchain_core.messages import AIMessage, HumanMessage
                    conts += 1
                    renv.record(_res.RecoveryStep(kind=_res.STEP_CONTINUATION, reason=str(fin), from_model=env.label, to_model=env.label, attempt=conts))
                    fin = None
                    more_parts: List[str] = []
                    async for chunk in _limited_stream(parent(list(messages) + [AIMessage(content=partial), HumanMessage(content=_res.CONTINUE_INSTRUCTION)],
                                                              stop=stop, run_manager=run_manager, **kwargs)):
                        m = getattr(chunk, "message", None)
                        c = getattr(m, "content", "") if m is not None else ""
                        if isinstance(c, str) and c:
                            more_parts.append(c)
                        f2 = _finish_of(m)
                        if f2:
                            fin = f2
                        yield chunk
                    partial = _res.join_continuation([partial, "".join(more_parts)])
                return
            fb = env.fallback_model()
            if fb is not None:
                cls_ = _res.classify(last_exc) if last_exc else None
                renv.record(_res.RecoveryStep(kind=_res.STEP_FALLBACK, reason=(cls_.reason if cls_ else "exhausted"), from_model=env.label,
                                              to_model=env.fallback_label(), status=(cls_.status if cls_ else None), detail=str(last_exc)[:200]))
                _log.warning(f"[resilience] {env.label} exhausted; streaming from the signed fallback {env.fallback_label()}")
                async for chunk in self._stream_fallback(env, messages, stop, run_manager, kwargs):
                    yield chunk
                return
            raise last_exc

        async def _stream_fallback(self, env, messages, stop, run_manager, kwargs):
            """The signed fallback, streamed; the served-by stamp on the FIRST chunk only."""
            fb = env.fallback_model()
            served = {"provider": env.fallback_identity.get("provider", ""), "model": env.fallback_identity.get("model", "")}
            stamped = False
            async for chunk in _limited_stream(fb._astream(messages, stop=stop, run_manager=run_manager, **env.fallback_call_kwargs(fb, kwargs))):
                # ONE stamp, on the first chunk: LangChain merges chunk metadata with
                # `merge_dicts`, which CONCATENATES equal strings — a stamp on every chunk
                # read back as provider 'AnthropicAnthropic…' on the summed message.
                if not stamped:
                    try:
                        m = getattr(chunk, "message", None)
                        if m is not None:
                            md = dict(getattr(m, "response_metadata", None) or {})
                            md["uderia_served_by"] = served
                            m.response_metadata = md
                            stamped = True
                    except Exception:  # noqa: BLE001
                        pass
                yield chunk

        # ── the sync call (not used by the platform's lanes; retry + terminal only) ──
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            env = envelope_of(self)
            if env is None:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            max_retries, base = _knobs()
            attempt = 0
            while True:
                attempt += 1
                try:
                    return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                except Exception as exc:
                    cls_ = _res.classify(exc)
                    if not cls_.retryable or not _res.should_retry(cls_, attempt, max_retries):
                        raise
                    import time as _t
                    _t.sleep(_res.backoff(attempt, base=base, retry_after=cls_.retry_after))

    _Resilient.__name__ = f"Resilient{cls.__name__}"
    _Resilient.__qualname__ = _Resilient.__name__
    _SUBCLASSES[cls] = _Resilient
    return _Resilient


def wrap(inst, *, provider: str, model: str, fallback_factory: Optional[Callable[[], Any]] = None,
         fallback_identity: Optional[Dict[str, str]] = None):
    """Envelope a LangChain chat model in place. Idempotent; never raises (an un-wrappable
    instance is returned as it was — the envelope is a protection, never a precondition)."""
    try:
        if envelope_of(inst) is not None:
            return inst
        cls = type(inst)
        base = cls
        # already a Resilient subclass (a second wrap on a re-classed instance)?
        if cls in _SUBCLASSES.values():
            base = next(k for k, v in _SUBCLASSES.items() if v is cls)
        sub = _subclass_for(base)
        object.__setattr__(inst, "__class__", sub)
        inst.__dict__["_uderia_envelope"] = _Envelope(provider, model, fallback_factory, fallback_identity)
        return inst
    except Exception as e:  # noqa: BLE001
        _log.warning(f"[resilience] LangChain model not enveloped: {e}")
        return inst


def is_wrapped(inst) -> bool:
    return envelope_of(inst) is not None
