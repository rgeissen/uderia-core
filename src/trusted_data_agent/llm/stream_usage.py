"""One usage per streamed call on the OpenAI-compatible LangChain lanes.

LangChain merges a stream's chunks with ``AIMessageChunk.__add__``, and that merge SUMS
``usage_metadata``. The OpenAI contract sends usage once, on the final chunk, so the sum is
the usage. Several OpenAI-compatible servers do not keep that contract: they repeat the
CUMULATIVE usage on several chunks (Celeris sends the full block on 3 of ~4 chunks; vLLM's
``continuous_usage_stats`` on every chunk). Summed, a 7,629-token prompt was recorded as
22,887 input tokens, and every streamed lane — the ReAct agent, the coordinator, any
``astream`` — billed, metered and quota-charged ~3x input / ~2x output (prod sessions
84399ee0 vs 86ac6b28, Oct 2026). The handler lane was never affected: it keeps the last
usage it sees (``llm/streaming.py``).

The fix is at the model, not at each consumer: an OpenAI-compatible instance is re-classed
(the ``resilient_chat`` mechanism — constructor, fields and ``bind_tools`` untouched) so its
``_astream`` / ``_stream`` strip usage from every chunk and put ONE merged usage on the final
chunk. The merge is field-wise MAX, which is the identity for the compliant single-usage
stream and the true value for a cumulative one (an early chunk may carry a field the last one
lacks — Celeris reports ``cache_read`` only on its last chunk, its first carries none).

Scope is deliberately ``BaseChatOpenAI`` only. Other providers report INCREMENTAL usage
across chunks (langchain_anthropic: input tokens on ``message_start``, output on
``message_delta``), where the sum is correct and a max would be wrong.

The stream is delayed by exactly one chunk (the held chunk is released when the next arrives),
so the final chunk keeps its identity and its ``finish_reason`` — the resilience envelope reads
the LAST chunk's finish reason to decide on a continuation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger("quart.app")
_SUBCLASSES: Dict[type, type] = {}


def merge_usage(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    """Field-wise max of two usage dicts (nested dicts recursively). Pure."""
    if not a:
        return dict(b) if b else (dict(a) if a is not None else None)
    if not b:
        return dict(a)
    out: Dict[str, Any] = dict(a)
    for k, v in b.items():
        cur = out.get(k)
        if isinstance(v, dict) or isinstance(cur, dict):
            out[k] = merge_usage(cur if isinstance(cur, dict) else {}, v if isinstance(v, dict) else {})
        elif isinstance(v, (int, float)) and isinstance(cur, (int, float)):
            out[k] = max(cur, v)
        elif cur is None:
            out[k] = v
    return out


def _usage_of(chunk) -> Optional[dict]:
    m = getattr(chunk, "message", None)
    um = getattr(m, "usage_metadata", None) if m is not None else None
    if not um:
        return None
    return dict(um) if isinstance(um, dict) else None


def _strip(chunk) -> None:
    m = getattr(chunk, "message", None)
    if m is not None and getattr(m, "usage_metadata", None) is not None:
        try:
            m.usage_metadata = None
        except Exception:  # noqa: BLE001 — an immutable message keeps its usage (old behaviour)
            pass


def _stamp(chunk, usage: Optional[dict]) -> None:
    if not usage:
        return
    m = getattr(chunk, "message", None)
    if m is None:
        return
    try:
        # total is recomputed from the merged parts: a max of totals could disagree with them
        if "input_tokens" in usage or "output_tokens" in usage:
            usage = dict(usage)
            usage["total_tokens"] = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        m.usage_metadata = usage
    except Exception:  # noqa: BLE001
        pass


class _Dedup:
    """Holds the last chunk back, merges every chunk's usage into it, releases it at the end."""

    def __init__(self):
        self.held = None
        self.usage: Optional[dict] = None

    def push(self, chunk):
        """Returns the chunk to release now (or None)."""
        u = _usage_of(chunk)
        if u:
            self.usage = merge_usage(self.usage, u)
            _strip(chunk)
        out, self.held = self.held, chunk
        return out

    def finish(self):
        out, self.held = self.held, None
        if out is not None:
            _stamp(out, self.usage)
        return out


def _subclass_for(cls: type) -> type:
    sub = _SUBCLASSES.get(cls)
    if sub is not None:
        return sub

    class _SingleUsage(cls):  # type: ignore[misc,valid-type]
        async def _astream(self, *args, **kwargs):
            d = _Dedup()
            async for chunk in super()._astream(*args, **kwargs):
                out = d.push(chunk)
                if out is not None:
                    yield out
            last = d.finish()
            if last is not None:
                yield last

        def _stream(self, *args, **kwargs):
            d = _Dedup()
            for chunk in super()._stream(*args, **kwargs):
                out = d.push(chunk)
                if out is not None:
                    yield out
            last = d.finish()
            if last is not None:
                yield last

    _SingleUsage.__name__ = f"SingleUsage{cls.__name__}"
    _SingleUsage.__qualname__ = _SingleUsage.__name__
    _SUBCLASSES[cls] = _SingleUsage
    return _SingleUsage


def _is_openai_compatible(inst) -> bool:
    try:
        from langchain_openai.chat_models.base import BaseChatOpenAI
    except Exception:  # noqa: BLE001 — langchain-openai absent: nothing to fix
        return False
    return isinstance(inst, BaseChatOpenAI)


def is_applied(inst) -> bool:
    # isinstance, not type(): the resilience envelope re-classes ON TOP of this subclass
    return any(isinstance(inst, s) for s in _SUBCLASSES.values())


def apply(inst):
    """Re-class an OpenAI-compatible LangChain chat model so a streamed call carries ONE usage.
    Idempotent; a non-OpenAI-compatible model is returned unchanged; never raises."""
    try:
        if inst is None or is_applied(inst) or not _is_openai_compatible(inst):
            return inst
        object.__setattr__(inst, "__class__", _subclass_for(type(inst)))
        return inst
    except Exception as e:  # noqa: BLE001
        _log.warning(f"[stream-usage] model not re-classed (streamed usage may be summed): {e}")
        return inst
