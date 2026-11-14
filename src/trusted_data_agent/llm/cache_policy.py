"""The Attested Cache (S2) — pure policy for provider prompt caching.

What is cached is the STABLE PREFIX of a call — the system prompt and the conversation
history that precede the volatile question — and the policy here decides, per provider,
how that prefix is marked so the provider serves it from cache:

  * Anthropic (and Claude on OpenRouter): explicit ``cache_control`` breakpoints — one on the
    system prompt, one on the last history message, so the next call (the next ReAct step,
    the next turn) reads the whole prefix at the cached rate. ``ttl`` is the profile's choice
    (``5m`` default; ``1h`` for a long working session).
  * OpenAI / Azure: automatic prefix caching — order alone decides, nothing to mark.
  * Google (Gemini 2.5+): implicit caching — order alone decides; the usage reading
    (``cached_content_token_count``) already existed and is what the cost rule consumes.
  * Ollama / MLX / Friendli / xAI / Bedrock: no prompt cache — the policy is a no-op and the
    turn's reading says so (``capability: none``), never pretends.

The prefix's identity is ALSO a fact: ``prefix_hash`` is the sha256 of the bytes the
provider was asked to cache. Two consecutive calls with the same hash and the second one
reading nothing from cache (on a provider that has one, above the provider's minimum,
inside the TTL) is surfaced by the assurance reading, never silently paid for. Whether it
is a *finding* depends on the capability: under BREAKPOINTS the platform asked for exactly
those bytes to be cached, so a miss means something outside the hash moved; under IMPLICIT
or AUTOMATIC the provider caches on its own and the platform set no breakpoint, so the same
miss is the provider's choice and the reading says so rather than claiming drift its own
unchanged hash refutes. Everything here is pure and deterministic.

The cache PLAN (docs/Architecture/PROMPT_CACHE_ARCHITECTURE.md) is one rule set for every
provider, so a call site never knows which provider it is talking to:

  1. Stable first, volatile last — the call site declares where its stable prefix ends
     (``stable_split``); nothing the turn changes may sit before that point.
  2. A breakpoint where the provider needs one — under BREAKPOINTS the stable prefix of the
     prompt is its own cached block (``mark_stable_blocks`` / ``bedrock_blocks``), beside
     the system and history breakpoints (three of the four Anthropic allows).
  3. An affinity key where the provider routes by one — under AUTOMATIC the agent's key
     (``affinity_key``) rides the request in the provider's own field (``affinity_fields``),
     so consecutive calls land on the machine that holds the prefix (OpenAI/Azure, xAI; not
     OpenRouter, whose own routing is already cache-aware — see ``_AFFINITY_FIELDS``).
  4. Read what was served — ``read_usage`` on every provider, priced by ``priced_cost``.

A field a provider refuses is learned from its own error and never sent again (the caller
keeps the set); nothing is a hardcoded model list.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

BREAKPOINTS = "breakpoints"   # explicit cache_control (Anthropic-shaped)
AUTOMATIC = "automatic"       # provider caches a repeated prefix on its own (OpenAI-shaped)
IMPLICIT = "implicit"         # provider caches on its own and REPORTS it (Gemini)
NONE = "none"

TTLS = ("5m", "1h")
DEFAULT_TTL = "5m"

# Minimum cacheable prefix per provider family — below it the provider silently caches
# nothing (no error, no write), so a miss there is NOT a finding.
MIN_CACHEABLE_TOKENS = {BREAKPOINTS: 1024, AUTOMATIC: 1024, IMPLICIT: 1024}
# Anthropic's minimum is PER MODEL (platform.claude.com → prompt caching, Sep 2026): a prompt
# under it is never cached however it is marked. Ordered most-specific first; an Anthropic
# model this table does not name takes the LARGEST minimum, so an unknown model can never
# produce a false finding (the conservative direction is "not a finding").
ANTHROPIC_MIN_TOKENS = (
    ("fable-5-1", 512), ("mythos-5-1", 512), ("opus-5", 512), ("fable-5", 512), ("mythos-5", 512),
    ("opus-4-8", 1024), ("sonnet-5", 1024), ("sonnet-4-6", 1024), ("sonnet-4-5", 1024),
    ("opus-4-1", 1024), ("sonnet-4", 1024), ("opus-4", 1024),
    ("mythos-preview", 2048), ("opus-4-7", 2048), ("haiku-3-5", 2048),
    ("opus-4-6", 4096), ("opus-4-5", 4096), ("haiku-4-5", 4096),
)
ANTHROPIC_MIN_UNKNOWN = 4096
TTL_SECONDS = {"5m": 300, "1h": 3600}

# Cached-read price as a fraction of the fresh input price, per provider family; the
# cache WRITE surcharge for explicit breakpoints (Anthropic: 1.25× for 5m, 2× for 1h).
# Anthropic reads are 0.1× — except Fable 5.1 / Mythos 5.1 at 0.025× (per-model exception).
READ_PRICE_FACTOR = {"Anthropic": 0.10, "Google": 0.25, "OpenAI": 0.50, "Azure": 0.50, "OpenRouter": 0.50}
ANTHROPIC_READ_FACTOR_EXCEPTIONS = (("fable-5-1", 0.025), ("mythos-5-1", 0.025))
WRITE_PRICE_FACTOR = {"5m": 1.25, "1h": 2.0}


def _model_key(model: Optional[str]) -> str:
    # `claude-haiku-4-5-20251001`, `anthropic/claude-haiku-4.5`, `eu.anthropic.claude-haiku-4-5-…-v1:0`
    # all normalise to a form the tables can match: lowercase, dots → dashes.
    return (model or "").lower().replace(".", "-")


def min_cacheable_tokens(provider: Optional[str], model: Optional[str] = None) -> int:
    """The smallest prefix the provider will cache for THIS model."""
    cap = capability(provider, model)
    if cap == BREAKPOINTS and (provider or "").strip() == "Amazon" and bedrock_family(model) == "nova":
        return 1024
    if cap == BREAKPOINTS:
        mk = _model_key(model)
        # longest needle first: `opus-4-7` must win over `opus-4`
        for needle, n in sorted(ANTHROPIC_MIN_TOKENS, key=lambda t: -len(t[0])):
            if needle in mk:
                return n
        return ANTHROPIC_MIN_UNKNOWN
    return MIN_CACHEABLE_TOKENS.get(cap, 1024)


def read_price_factor(provider: Optional[str], model: Optional[str] = None, default: float = 0.25) -> float:
    p = (provider or "").strip()
    mk = _model_key(model)
    if p == "Anthropic" or (p == "OpenRouter" and capability(p, model) == BREAKPOINTS):
        for needle, f in ANTHROPIC_READ_FACTOR_EXCEPTIONS:
            if needle in mk:
                return f
        return READ_PRICE_FACTOR["Anthropic"]
    if p == "Amazon":
        fam = bedrock_family(model)
        if fam == "anthropic":
            return READ_PRICE_FACTOR["Anthropic"]
        if fam == "nova":
            return 0.25   # Nova: cached reads at a 75% discount
    return READ_PRICE_FACTOR.get(p, default)


def write_price_factor(provider: Optional[str], model: Optional[str], ttl: str = DEFAULT_TTL) -> float:
    """What a cache WRITE costs relative to fresh input: Claude (native, broker, Bedrock)
    surcharges it by the ttl; Nova writes at the input rate; a provider without breakpoints
    has no write."""
    if capability(provider, model) != BREAKPOINTS:
        return 1.0
    if (provider or "").strip() == "Amazon" and bedrock_family(model) == "nova":
        return 1.0
    return WRITE_PRICE_FACTOR.get(ttl, 1.25)


def capability(provider: Optional[str], model: Optional[str] = None) -> str:
    p = (provider or "").strip()
    m = (model or "").lower()
    if p == "Anthropic":
        return BREAKPOINTS
    if p == "OpenRouter" and ("anthropic/" in m or "claude" in m):
        return BREAKPOINTS
    if p in ("OpenAI", "Azure", "OpenRouter", "XAI"):
        return AUTOMATIC
    if p == "Google":
        return IMPLICIT
    if p == "Amazon" and bedrock_family(model) in ("anthropic", "nova"):
        return BREAKPOINTS
    return NONE


def bedrock_family(model: Optional[str]) -> Optional[str]:
    """The Bedrock model family that decides the cache marking: ``anthropic`` (Claude, the
    Anthropic body with ``cache_control``), ``nova`` (Amazon Nova, ``cachePoint`` blocks), or
    None (Titan, Llama, Mistral… — no prompt cache). Reads the model id and the cross-region
    inference-profile form (``us.anthropic.claude-…``, ``eu.amazon.nova-…``); an ARN names no
    family and reads None, so it is never marked."""
    m = (model or "").lower()
    if not m or m.startswith("arn:"):
        return None
    parts = m.split(".")
    for i, part in enumerate(parts[:2]):
        if part == "anthropic":
            return "anthropic"
        if part == "amazon" and len(parts) > i + 1 and parts[i + 1].startswith("nova"):
            return "nova"
    return None


def resolve(profile: Any, master_enabled: bool) -> Dict[str, Any]:
    """The profile's cache setting under the master switch: ``{"enabled", "ttl"}``.
    Absent block ⇒ enabled when the master is (caching never changes what the agent says,
    only what the prefix costs); an explicit ``enabled: false`` opts the agent out."""
    if not master_enabled:
        return {"enabled": False, "ttl": DEFAULT_TTL}
    block = profile.get("cacheConfig") if isinstance(profile, dict) else None
    block = block if isinstance(block, dict) else {}
    enabled = block.get("enabled", True)
    ttl = str(block.get("ttl") or DEFAULT_TTL)
    if ttl not in TTLS:
        ttl = DEFAULT_TTL
    return {"enabled": bool(enabled), "ttl": ttl}


def cache_control(ttl: str = DEFAULT_TTL) -> Dict[str, str]:
    cc = {"type": "ephemeral"}
    if ttl == "1h":
        cc["ttl"] = "1h"
    return cc


def system_blocks(system_text: Optional[str], ttl: str = DEFAULT_TTL) -> List[Dict[str, Any]]:
    """The system prompt as ONE cached text block (empty text ⇒ no block)."""
    if not system_text:
        return []
    return [{"type": "text", "text": system_text, "cache_control": cache_control(ttl)}]


def mark_last_message(messages: List[Dict[str, Any]], ttl: str = DEFAULT_TTL) -> List[Dict[str, Any]]:
    """Place a breakpoint on the LAST message of ``messages`` (the end of the stable prefix —
    the caller passes the history WITHOUT the volatile question). Never mutates its input;
    a message whose content is already blocks gets the mark on its last text block."""
    if not messages:
        return []
    out = [dict(m) for m in messages]
    last = out[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content, "cache_control": cache_control(ttl)}]
    elif isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        for i in range(len(blocks) - 1, -1, -1):
            if isinstance(blocks[i], dict) and blocks[i].get("type") == "text":
                blocks[i]["cache_control"] = cache_control(ttl)
                break
        last["content"] = blocks
    return out


def prefix_hash(system_text: Optional[str], history: Optional[List[Dict[str, Any]]],
                stable_prefix: Optional[str] = None) -> str:
    """Identity of the bytes the provider was asked to cache: system + history contents, and
    the prompt's declared stable prefix when the call carries one."""
    parts = [system_text or ""]
    for m in history or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            c = "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in c)
        parts.append(f"{(m.get('role') if isinstance(m, dict) else '')}:{c or ''}")
    if stable_prefix:
        parts.append(f"stable:{stable_prefix}")
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def mark_openai_messages(messages: List[Dict[str, Any]], ttl: str = DEFAULT_TTL) -> List[Dict[str, Any]]:
    """OpenAI-shaped message list (system first, question last) with the breakpoints an
    Anthropic model behind a broker (OpenRouter) honours: the system message and the last
    history message become text-part content carrying ``cache_control``. The volatile last
    message is never marked; never mutates its input; an empty history marks the system only."""
    if not messages:
        return []
    out = [dict(m) for m in messages]
    if out[0].get("role") == "system":
        sc = out[0].get("content")
        if isinstance(sc, str) and sc:
            out[0]["content"] = system_blocks(sc, ttl)
        history = out[1:-1]
        if history:
            out[1:-1] = mark_last_message(history, ttl)
    else:
        history = out[:-1]
        if history:
            out[:-1] = mark_last_message(history, ttl)
    return out


# ── the cache plan: stable split · breakpoint · affinity ────────────────────────────────

# A declared stable prefix shorter than this is not worth a block of its own (every
# provider's minimum is ≥ 512 tokens ≈ 2K characters); it is then simply not split.
MIN_STABLE_CHARS = 2000


def stable_split(prompt: Optional[str], stable_prefix: Optional[str]) -> Optional[Tuple[str, str]]:
    """``(stable, volatile)`` when ``stable_prefix`` is a true prefix of ``prompt`` long enough
    to cache; otherwise None — a declaration that does not match the prompt it describes is
    ignored, never trusted (the call is then sent exactly as without one)."""
    if not prompt or not stable_prefix or len(stable_prefix) < MIN_STABLE_CHARS:
        return None
    if len(stable_prefix) >= len(prompt) or not prompt.startswith(stable_prefix):
        return None
    return stable_prefix, prompt[len(stable_prefix):]


_VOLATILE_SENTINEL = "\x00\x01uderia-volatile\x01\x00"


def template_stable_prefix(template: str, values: Dict[str, Any], stable_keys) -> Optional[str]:
    """The part of ``template.format(**values)`` that does not depend on any value outside
    ``stable_keys``: render once with every other value replaced by a sentinel and keep what
    precedes the first sentinel. Template-agnostic (an edited prompt moves the boundary with
    its text) and conservative — a key not declared stable is volatile, so a new template
    variable can never be cached by accident. None when nothing stable leads the template."""
    try:
        probe = template.format(**{k: (v if k in stable_keys else _VOLATILE_SENTINEL) for k, v in values.items()})
    except Exception:  # noqa: BLE001 — a template this cannot render is simply not split
        return None
    i = probe.find(_VOLATILE_SENTINEL)
    if i <= 0:
        return None
    return probe[:i]


def mark_stable_blocks(prompt: str, stable_prefix: Optional[str], ttl: str = DEFAULT_TTL) -> Any:
    """The volatile question as Anthropic-shaped content: the stable prefix as a cached text
    block, the rest as a plain one. Without a usable split the prompt is returned unchanged
    (a plain string), so the request is byte-identical to one without the plan."""
    split = stable_split(prompt, stable_prefix)
    if split is None:
        return prompt
    stable, volatile = split
    return [{"type": "text", "text": stable, "cache_control": cache_control(ttl)},
            {"type": "text", "text": volatile}]


def bedrock_blocks(text: str, stable_prefix: Optional[str] = None, *, whole: bool = False) -> List[Dict[str, Any]]:
    """Nova content blocks with ``cachePoint`` markers: ``whole`` marks the end of the whole
    text (the system prompt); otherwise the stable prefix of the prompt is followed by a
    marker and the volatile rest. No usable split ⇒ the plain single text block."""
    if whole:
        return [{"text": text}, {"cachePoint": {"type": "default"}}] if text else []
    split = stable_split(text, stable_prefix)
    if split is None:
        return [{"text": text}]
    return [{"text": split[0]}, {"cachePoint": {"type": "default"}}, {"text": split[1]}]


def mark_bedrock_body(body: Dict[str, Any], model: Optional[str], system_text: Optional[str],
                      stable_prefix: Optional[str] = None, ttl: str = DEFAULT_TTL) -> Optional[Dict[str, Any]]:
    """The Bedrock request body with the cache plan's markers, or None when the model's
    family takes none (the caller then sends the plain body). Claude: the Anthropic shape —
    a cached system block, the last history message marked, the question's stable prefix its
    own cached block. Nova: ``cachePoint`` after the system prompt and after the stable
    prefix. A multimodal question (content already blocks) keeps its blocks unmarked.
    Never mutates ``body``."""
    fam = bedrock_family(model)
    if fam not in ("anthropic", "nova"):
        return None
    out = json.loads(json.dumps(body))
    msgs = out.get("messages") or []
    if fam == "anthropic":
        if system_text:
            out["system"] = system_blocks(system_text, ttl)
        if msgs:
            history, last = msgs[:-1], dict(msgs[-1])
            if isinstance(last.get("content"), str):
                last["content"] = mark_stable_blocks(last["content"], stable_prefix, ttl)
            out["messages"] = mark_last_message(history, ttl) + [last]
        return out
    if system_text:
        out["system"] = bedrock_blocks(system_text, whole=True)
    if msgs:
        last = dict(msgs[-1]); content = last.get("content")
        if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) and set(content[0]) == {"text"}:
            last["content"] = bedrock_blocks(content[0]["text"], stable_prefix)
        out["messages"] = msgs[:-1] + [last]
    return out


def affinity_key(user_uuid: Optional[str], profile_id: Optional[str]) -> Optional[str]:
    """The agent's cache-affinity key: one per (owner, agent), stable across turns and
    sessions — every turn of an agent shares its system prompt and capability catalog, so
    every turn should land where that prefix is warm. Hashed: no identifier leaves."""
    if not user_uuid or not profile_id:
        return None
    return "uderia-" + hashlib.sha256(f"{user_uuid}|{profile_id}".encode("utf-8")).hexdigest()[:32]


# Where each AUTOMATIC provider takes the affinity key: a top-level request field
# (``kwargs``) or a header. OpenRouter is deliberately ABSENT: its own routing already sticks
# to an upstream once that upstream SERVED from cache, while its ``session_id`` sticks from
# the first request to whichever upstream answered — measured on the Optimize lane's live
# call sequence (MiMo v2.6 Flash, 2026-10-29), the key pinned the agent to an upstream that
# served 27 cached tokens over nine calls; without it the broker's own stickiness reached one
# that served 6.9K. A broker in front of Claude takes breakpoints instead (BREAKPOINTS).
_AFFINITY_FIELDS = {
    "OpenAI": ("kwargs", "prompt_cache_key"),
    "Azure": ("kwargs", "prompt_cache_key"),
    "XAI": ("headers", "x-grok-conv-id"),
}


def affinity_fields(provider: Optional[str], model: Optional[str], key: Optional[str],
                    refused: Optional[set] = None) -> Dict[str, Dict[str, str]]:
    """The request additions that carry ``key`` to ``provider``:
    ``{"kwargs": {...}, "extra_body": {...}, "extra_headers": {...}}`` (empty parts omitted).
    Only under AUTOMATIC (a breakpoint provider caches what it was told to; an implicit one
    takes no key); a (provider, model) in ``refused`` already declined the field and gets
    nothing."""
    p = (provider or "").strip()
    if not key or capability(p, model) != AUTOMATIC or p not in _AFFINITY_FIELDS:
        return {}
    if refused and (p, model) in refused:
        return {}
    where, name = _AFFINITY_FIELDS[p]
    return {{"kwargs": "kwargs", "headers": "extra_headers"}[where]: {name: key}}


def affinity_refused(provider: Optional[str], error: Any) -> bool:
    """True when ``error`` is the provider refusing the affinity field itself (an older API
    version, a gateway that validates strictly) — the one error the caller may retry without
    the field and remember."""
    p = (provider or "").strip()
    if p not in _AFFINITY_FIELDS:
        return False
    text = str(error).lower()
    return _AFFINITY_FIELDS[p][1].lower() in text


def cache_point_refused(error: Any) -> bool:
    """True when a Bedrock model refused the cache markers (a model without prompt caching
    in this region) — retried once without them and remembered."""
    t = str(error).lower()
    return "cachepoint" in t or "cache_control" in t or "prompt caching" in t

def read_usage(provider: str, usage: Any) -> Dict[str, int]:
    """Normalise a provider usage object to ``{input, output, cached, written}`` where
    ``input`` is the TOTAL prompt (fresh + cached + written). Anthropic reports the three
    apart (its ``input_tokens`` EXCLUDES the cached prefix); OpenAI folds the cached count
    into ``prompt_tokens`` and details it; Gemini folds it into ``prompt_token_count``."""
    g = (lambda k: (usage.get(k) if isinstance(usage, dict) else getattr(usage, k, None)))
    if provider in ("Anthropic",):
        fresh = int(g("input_tokens") or 0); out = int(g("output_tokens") or 0)
        cached = int(g("cache_read_input_tokens") or 0); written = int(g("cache_creation_input_tokens") or 0)
        return {"input": fresh + cached + written, "output": out, "cached": cached, "written": written}
    if provider in ("OpenAI", "Azure", "Friendli", "OpenRouter", "XAI", "Celeris", "MLX"):
        total = int(g("prompt_tokens") or 0); out = int(g("completion_tokens") or 0)
        det = g("prompt_tokens_details")
        cached = 0
        written = 0
        if det is not None:
            dg = (lambda k: (det.get(k) if isinstance(det, dict) else getattr(det, k, None)))
            cached = int(dg("cached_tokens") or 0)
            written = int(dg("cache_write_tokens") or 0)   # a broker fronting Anthropic reports the write here
        return {"input": total, "output": out, "cached": cached, "written": written}
    if provider == "Amazon":
        if g("inputTokens") is not None or g("cacheReadInputTokenCount") is not None:   # Nova
            fresh = int(g("inputTokens") or 0); out = int(g("outputTokens") or 0)
            cached = int(g("cacheReadInputTokenCount") or 0); written = int(g("cacheWriteInputTokenCount") or 0)
        else:                                                                             # Claude
            fresh = int(g("input_tokens") or 0); out = int(g("output_tokens") or 0)
            cached = int(g("cache_read_input_tokens") or 0); written = int(g("cache_creation_input_tokens") or 0)
        return {"input": fresh + cached + written, "output": out, "cached": cached, "written": written}
    if provider == "Google":
        total = int(g("prompt_token_count") or 0); out = int(g("candidates_token_count") or 0)
        return {"input": total, "output": out, "cached": int(g("cached_content_token_count") or 0), "written": 0}
    return {"input": int(g("input_tokens") or g("prompt_tokens") or 0), "output": int(g("output_tokens") or g("completion_tokens") or 0), "cached": 0, "written": 0}


def priced_cost(base_cost_fn, provider: str, model: str, input_tokens: int, output_tokens: int,
                cached: int = 0, written: int = 0, ttl: str = DEFAULT_TTL,
                read_factor_default: float = 0.25) -> float:
    """ONE pricing rule for a call with a cache reading. ``base_cost_fn(provider, model, in, out)``
    prices fresh tokens; cached reads are discounted to the provider's read factor and explicit
    cache writes surcharged by the ttl's write factor. Never negative; never raises."""
    try:
        base = float(base_cost_fn(provider, model, input_tokens, output_tokens) or 0.0)
        if not (cached or written) or not base:
            return max(0.0, base)
        unit_in = float(base_cost_fn(provider, model, 1_000_000, 0) or 0.0) / 1_000_000.0
        rf = read_price_factor(provider, model, read_factor_default)
        adjust = -min(cached, input_tokens) * unit_in * (1.0 - rf)
        if written and capability(provider, model) == BREAKPOINTS:
            adjust += min(written, input_tokens) * unit_in * (write_price_factor(provider, model, ttl) - 1.0)
        return max(0.0, base + adjust)
    except Exception:  # noqa: BLE001
        try:
            return max(0.0, float(base_cost_fn(provider, model, input_tokens, output_tokens) or 0.0))
        except Exception:  # noqa: BLE001
            return 0.0


def miss_is_finding(*, cap: str, prev_hash: Optional[str], this_hash: str, cached: int,
                    input_tokens: int, seconds_since_prev: Optional[float], ttl: str = DEFAULT_TTL,
                    min_tokens: Optional[int] = None) -> bool:
    """The attested-prefix rule: the same prefix bytes, inside the TTL, above the provider's
    minimum, on a provider that caches — and nothing came from cache. Every other case is
    not a finding (a first call, a changed prefix, an expired TTL, a short prefix, no cache)."""
    if cap == NONE or not prev_hash or prev_hash != this_hash or cached > 0:
        return False
    if input_tokens < (min_tokens if min_tokens is not None else MIN_CACHEABLE_TOKENS.get(cap, 1024)):
        return False
    if seconds_since_prev is not None and seconds_since_prev > TTL_SECONDS.get(ttl, 300):
        return False
    return True
