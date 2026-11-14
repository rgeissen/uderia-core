"""Provider fault injection — a fire-drill switch for the TEST server only.

Why: a provider failure cannot be provoked by a prompt, so the platform's handling of one (the
honest sentence, the failed record, JUDGED's clean_termination, the breaker, the signed
fallback, the Resilience Envelope's reading) was untestable live until a real outage happened
(trust campaign C18). A campaign agent wired to an always-failing endpoint cannot be activated
either: activation requires its profile tests to pass.

What: with ``TDA_FAULT_INJECT=1`` (OFF by default — production never sets it), a model call whose
messages contain a marker ``[fault:<kind>]`` raises the error that provider failure would raise
— shaped like the SDKs' own (a ``status_code`` and the message), so ``resilience.classify``,
``out_of_credits`` and ``turn_failure.answer`` read it exactly as they read a real one. Every
other call is untouched, so the agents' activation tests still pass. One rule, two seams: the
handler's attempt loop (``check``, inside its ``try`` so retries/breaker/fallback treat it like
a real failure) and every LangChain model (``attach``: the check inside its generation calls,
beneath the resilience wrapper's retry loop).

Kinds: ``402`` (no credits) · ``401`` (rejected credentials) · ``429`` (rate limit, retryable) ·
``503`` (overloaded, retryable) · ``timeout`` (no response, retryable).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("quart.app")

MARKER_RE = re.compile(r"\[fault:(402|401|429|503|timeout)\]", re.I)


class InjectedProviderError(Exception):
    """An SDK-shaped provider error (``status_code`` + the provider's message)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Error code: {status} - {{'error': {{'message': '{message} (injected fault)', 'code': {status}}}}}")
        self.status_code = status


class InjectedTimeout(TimeoutError):
    """The provider did not respond (an injected fault)."""


def enabled() -> bool:
    return (os.environ.get("TDA_FAULT_INJECT") or "").strip().lower() in ("1", "true", "yes", "on")


def _text_of(obj: Any) -> str:
    """Every text a call carries: a string, a message list (dicts, LangChain messages, nested)."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_text_of(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return " ".join(_text_of(x) for x in obj)
    content = getattr(obj, "content", None)
    return _text_of(content) if content is not None else ""


def marker(*texts: Any) -> Optional[str]:
    m = MARKER_RE.search(" ".join(_text_of(t) for t in texts))
    return m.group(1).lower() if m else None


def error_for(kind: str) -> BaseException:
    if kind == "402":
        return InjectedProviderError(402, "This request requires more credits")
    if kind == "401":
        return InjectedProviderError(401, "Invalid API key")
    if kind == "429":
        return InjectedProviderError(429, "Rate limit exceeded, too many requests")
    if kind == "503":
        return InjectedProviderError(503, "Service unavailable: the model is overloaded")
    return InjectedTimeout("Request timed out (injected fault)")


def check(*texts: Any) -> None:
    """Raise the injected fault when the switch is on and the call carries a marker."""
    if not enabled():
        return
    kind = marker(*texts)
    if kind:
        logger.warning(f"[fault-inject] raising an injected provider fault ({kind}) for this call")
        raise error_for(kind)


_SUBCLASSES: dict = {}


def _subclass_for(cls: type) -> type:
    """The model's class with the check INSIDE its generation calls (``_agenerate`` · ``_astream``
    · ``_generate`` · ``_stream``) — the calls the resilience wrapper (``llm/resilient_chat.py``)
    retries around. A LangChain callback fired at ``on_chat_model_start``, which runs BEFORE that
    loop: the fault was never retried nor handed to the fallback."""
    sub = _SUBCLASSES.get(cls)
    if sub is not None:
        return sub

    class _FaultInjected(cls):  # type: ignore[misc,valid-type]
        async def _agenerate(self, messages, *args, **kwargs):
            check(messages)
            return await super()._agenerate(messages, *args, **kwargs)

        async def _astream(self, messages, *args, **kwargs):
            check(messages)
            async for chunk in super()._astream(messages, *args, **kwargs):
                yield chunk

        def _generate(self, messages, *args, **kwargs):
            check(messages)
            return super()._generate(messages, *args, **kwargs)

        def _stream(self, messages, *args, **kwargs):
            check(messages)
            yield from super()._stream(messages, *args, **kwargs)

    _FaultInjected.__name__ = f"FaultInjected{cls.__name__}"
    _FaultInjected.__qualname__ = _FaultInjected.__name__
    _SUBCLASSES[cls] = _FaultInjected
    return _FaultInjected


def attach(llm: Any) -> Any:
    """Re-class a LangChain chat model so the injected fault is raised inside its generation calls
    (beneath the resilience wrapper, which therefore retries it like a real failure). A no-op when
    the switch is off (the model is returned untouched); never raises."""
    if not enabled() or llm is None or type(llm).__name__.startswith("FaultInjected"):
        return llm
    try:
        object.__setattr__(llm, "__class__", _subclass_for(type(llm)))
    except Exception as e:  # noqa: BLE001 — a test switch must never break model creation
        logger.warning(f"[fault-inject] could not attach to the LangChain model: {e}")
    return llm
