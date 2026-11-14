"""The Attested Cache (S2) — the per-turn runtime beside the pure policy.

`llm/cache_policy.py` decides HOW a prefix is marked and priced; this module holds what a
TURN accumulates about it and stamps it onto the persisted turn at the one chokepoint every
engine's turn dict lands in (`session_manager.update_last_turn_data`, the agent-authority
precedent): a `TurnCache` in a task-local contextvar, armed by `execution_service` for the
turn and released in its `finally`; every LLM call that carries a cache reading — the
handler's providers AND the LangChain lanes (ReAct, genie) — records into it.

The stamp (`turn["cache"]`) is facts, never a verdict: capability, whether caching was on,
the calls, the total/cached/written tokens, the last prefix hash, and the number of calls
whose miss was a FINDING under `cache_policy.miss_is_finding` — the same prefix bytes, inside
the TTL, above the minimum, on a provider that caches, and nothing read from cache.
"""
from __future__ import annotations

import contextvars
import hashlib, logging, time
from typing import Any, Dict, Optional

from trusted_data_agent.llm import cache_policy as _cp

_TURN: "contextvars.ContextVar[Optional[TurnCache]]" = contextvars.ContextVar("uderia_turn_cache", default=None)


class TurnCache:
    """What one turn learned about its prompt cache. `record()` is called per LLM call."""

    def __init__(self, *, enabled: bool, ttl: str = _cp.DEFAULT_TTL, previous_prefix_hash: Optional[str] = None,
                 previous_at: Optional[float] = None):
        self.enabled = bool(enabled)
        self.ttl = ttl if ttl in _cp.TTLS else _cp.DEFAULT_TTL
        self.calls = 0
        self.input_tokens = 0
        self.cached_tokens = 0
        self.written_tokens = 0
        self.capability = _cp.NONE
        self.prefix_hash: Optional[str] = None
        self.misses_found = 0          # misses that were FINDINGS (the attested-prefix rule)
        self.hits = 0                  # calls that read something from cache
        self._prev_hash = previous_prefix_hash
        self._prev_at = previous_at
        self.started_at = time.time()

    def record(self, *, provider: str, model: str, input_tokens: int, output_tokens: int = 0,
               cached: int = 0, written: int = 0, prefix_hash: Optional[str] = None) -> Dict[str, Any]:
        cap = _cp.capability(provider, model)
        now = time.time()
        finding = False
        if prefix_hash is not None and self.enabled:
            since = (now - self._prev_at) if self._prev_at else None
            finding = _cp.miss_is_finding(cap=cap, prev_hash=self._prev_hash, this_hash=prefix_hash,
                                          cached=int(cached or 0), input_tokens=int(input_tokens or 0),
                                          seconds_since_prev=since, ttl=self.ttl,
                                          min_tokens=_cp.min_cacheable_tokens(provider, model))
            self._prev_hash, self._prev_at = prefix_hash, now
            self.prefix_hash = prefix_hash
        self.calls += 1
        self.capability = cap if self.calls == 1 else (self.capability if self.capability == cap else "mixed")
        self.input_tokens += int(input_tokens or 0)
        self.cached_tokens += int(cached or 0)
        self.written_tokens += int(written or 0)
        if cached:
            self.hits += 1
        if finding:
            self.misses_found += 1
        return {"finding": finding, "capability": cap}

    def summary(self) -> Dict[str, Any]:
        share = (self.cached_tokens / self.input_tokens) if self.input_tokens else 0.0
        return {"enabled": self.enabled, "ttl": self.ttl, "capability": self.capability, "calls": self.calls,
                "input_tokens": self.input_tokens, "cached_tokens": self.cached_tokens,
                "written_tokens": self.written_tokens, "cached_share": round(share, 4),
                "hits": self.hits, "misses_found": self.misses_found, "prefix_hash": self.prefix_hash,
                "at": self.started_at}


_log = logging.getLogger("quart.app")


def set_turn_cache(tc: Optional[TurnCache]):
    return _TURN.set(tc)


def reset_turn_cache(token) -> None:
    try:
        _TURN.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current() -> Optional[TurnCache]:
    return _TURN.get()


def record_call(**kw) -> Optional[Dict[str, Any]]:
    """Record one LLM call's cache reading into the turn's accumulator (no-op outside a turn,
    or when nothing cache-shaped was reported). Never raises."""
    tc = _TURN.get()
    if tc is None:
        return None
    try:
        return tc.record(**kw)
    except Exception:  # noqa: BLE001
        return None


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    """The persisted stamp — only when caching was ARMED for the turn or a provider reported
    a cache reading anyway (Gemini's implicit cache reports with caching off, and that fact
    is worth keeping); otherwise None so an untouched deployment's turns are byte-identical."""
    tc = _TURN.get()
    if tc is None:
        return None
    if not tc.enabled and not tc.cached_tokens:
        return None
    return tc.summary()


# ---------------------------------------------------------------------------
# The LangChain lanes (ReAct agent, genie coordinator) — same policy, their shapes
# ---------------------------------------------------------------------------
def policy_for(profile: Any, provider: str, model: str) -> Dict[str, Any]:
    """The profile's cache setting under the master switch + the provider's capability."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        pol = _cp.resolve(profile or {}, bool(getattr(APP_CONFIG, "PROMPT_CACHING", False)))
    except Exception:  # noqa: BLE001
        pol = {"enabled": False, "ttl": _cp.DEFAULT_TTL}
    pol["capability"] = _cp.capability(provider, model)
    return pol


def langchain_prefix(system_prompt: str, history: list, *, profile: Any, provider: str, model: str):
    """Build the LangChain system message + history with the stable prefix marked for the
    provider (breakpoints only where the provider has them). Returns
    ``(system_message, history, prefix_hash, policy)``; with caching off the messages are
    exactly the plain ones the lanes always built."""
    from langchain_core.messages import SystemMessage
    pol = policy_for(profile, provider, model)
    texts = []
    for m in history:
        c = getattr(m, "content", "")
        if isinstance(c, list):
            c = "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in c)
        texts.append({"role": getattr(m, "type", ""), "content": c})
    ph = _cp.prefix_hash(system_prompt, texts)
    _log.debug("[PromptCache] prefix=%s system=%s (%d chars) history=%d cap=%s enabled=%s",
              ph[:12], hashlib.sha256((system_prompt or "").encode()).hexdigest()[:12], len(system_prompt or ""), len(texts), pol["capability"], pol["enabled"])
    if not (pol["enabled"] and pol["capability"] == _cp.BREAKPOINTS):
        return SystemMessage(content=system_prompt), list(history), ph, pol
    sys_msg = SystemMessage(content=_cp.system_blocks(system_prompt, pol["ttl"]) or system_prompt)
    hist = list(history)
    if hist:
        last = hist[-1]
        c = getattr(last, "content", "")
        if isinstance(c, str) and c:
            hist[-1] = last.__class__(content=[{"type": "text", "text": c, "cache_control": _cp.cache_control(pol["ttl"])}])
    return sys_msg, hist, ph, pol


def langchain_usage(usage: Any, response_metadata: Any = None) -> Dict[str, int]:
    """Normalise a LangChain ``usage_metadata`` (dict or object) to ``{input, output, cached,
    written}``. LangChain's ``input_tokens`` already INCLUDES the cached prefix.

    ``response_metadata`` (the message's raw provider metadata) is the fallback for what
    LangChain's normalisation drops: an OpenAI-shaped broker fronting Anthropic reports the
    cache WRITE as ``token_usage.prompt_tokens_details.cache_write_tokens``, which never
    reaches ``usage_metadata.input_token_details`` — without it a write is priced as fresh."""
    g = (lambda o, k: (o.get(k) if isinstance(o, dict) else getattr(o, k, None)))
    if not usage:
        return {"input": 0, "output": 0, "cached": 0, "written": 0}
    det = g(usage, "input_token_details") or {}
    cached = int(g(det, "cache_read") or 0)
    written = int(g(det, "cache_creation") or 0)
    inp = int(g(usage, "input_tokens") or g(usage, "prompt_tokens") or 0)
    out = int(g(usage, "output_tokens") or g(usage, "completion_tokens") or 0)
    if not written and response_metadata:
        raw = g(response_metadata, "token_usage") or g(response_metadata, "usage") or {}
        rdet = g(raw, "prompt_tokens_details") or {}
        written = int(g(rdet, "cache_write_tokens") or 0)
        if not cached:
            cached = int(g(rdet, "cached_tokens") or 0)
    return {"input": inp, "output": out, "cached": cached, "written": written}


def priced(provider: str, model: str, u: Dict[str, int], ttl: str = _cp.DEFAULT_TTL) -> float:
    """The call's cost under the one cache-aware rule (`calculate_cost_with_cache`)."""
    from trusted_data_agent.core.cost_manager import get_cost_manager
    cm = get_cost_manager()
    if u.get("cached") or u.get("written"):
        return cm.calculate_cost_with_cache(provider, model, u["input"], u["output"],
                                            cached_tokens=u["cached"], cache_write_tokens=u["written"], ttl=ttl)
    return cm.calculate_cost(provider=provider, model=model, input_tokens=u["input"], output_tokens=u["output"])
