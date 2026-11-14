"""The Resilience Envelope (S4) — the PURE policy behind every LLM call's recovery.

Spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S4. This module decides, never acts:

  * `classify(exc)`          → what an error IS: retryable (rate limit, overload, server, connection,
                               timeout), terminal (auth, bad request, context too long, not found,
                               anything unknown), or a REFUSAL (the provider declined on content grounds).
  * `backoff(attempt, …)`    → how long to wait before attempt N, honouring a `retry-after` the
                               provider sent (a provider's own number beats the schedule) and a cap.
  * `finish_kind(provider, finish)` → whether a completion STOPPED, was TRUNCATED (`max_tokens`,
                               `length`, `MAX_TOKENS`) or was REFUSED (`refusal`, `content_filter`,
                               `SAFETY`, `RECITATION`, `PROHIBITED_CONTENT`).
  * `continuation(...)`      → the messages that ask for the rest of a truncated answer: the partial as
                               the assistant's own prior turn, then one instruction to continue EXACTLY
                               where it stopped — never a restatement; bounded to MAX_CONTINUATIONS.
  * `refusal_answer(...)`    → the honest, delivered answer for a provider refusal — named category,
                               no canned "technical error".
  * `RecoveryStep`           → the one record shape every act produces: {kind, from, to, reason, …}.

No I/O, no SDK import, no clock: every input is a value. The handler (`llm/handler.py`) and the
LangChain wrapper (`llm/resilient_chat.py`) are the two actors; `core/provider_health.py` is the
breaker; `agent/resilience_envelope.py` accumulates a turn's steps and stamps them.
"""
from __future__ import annotations

import email.utils
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# ── Classification kinds ─────────────────────────────────────────────────────
RETRYABLE = "retryable"
TERMINAL = "terminal"
REFUSAL = "refusal"

# ── Finish kinds ─────────────────────────────────────────────────────────────
STOPPED = "stopped"
TRUNCATED = "truncated"
REFUSED = "refused"

# ── Recovery step kinds ──────────────────────────────────────────────────────
STEP_RETRY = "retry"
STEP_CONTINUATION = "continuation"
STEP_FALLBACK = "fallback"
STEP_REFUSAL = "refusal"
STEP_KINDS = (STEP_RETRY, STEP_CONTINUATION, STEP_FALLBACK, STEP_REFUSAL)

MAX_CONTINUATIONS = 2          # bounded by the spec: a runaway answer is not "resilience"
DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 2.0
MAX_BACKOFF_SECONDS = 30.0     # a provider's retry-after may exceed it; our own schedule never does
MAX_RETRY_AFTER_HONOURED = 60.0  # beyond this a "retry-after" is a rejection, not a wait

_RE_RATE = re.compile(r"\b(rate.?limit|too many requests|quota|429)\b", re.I)
_RE_OVERLOAD = re.compile(r"\b(overloaded|over capacity|capacity|unavailable|503|529|502|504|bad gateway|gateway time.?out)\b", re.I)
_RE_TIMEOUT = re.compile(r"\b(timed? ?out|timeout|deadline exceeded)\b", re.I)
_RE_CONN = re.compile(r"\b(connection (reset|refused|error|aborted)|connect(ion)? error|network|temporarily|EOF)\b", re.I)
_RE_CONTEXT = re.compile(r"(context.?(length|window)|maximum context|too (many tokens|long)|prompt is too long|exceeds the (maximum|model))", re.I)
_RE_AUTH = re.compile(r"\b(invalid (api|x-api)-?key|unauthori[sz]ed|authentication|permission denied|forbidden|401|403)\b", re.I)
_RE_REFUSE = re.compile(r"\b(content.?filter|safety|refus(al|ed)|blocked by|prohibited content|policy violation)\b", re.I)


@dataclass
class Classification:
    kind: str                 # RETRYABLE | TERMINAL | REFUSAL
    reason: str               # a short, stable label: rate_limit · overloaded · server_error · timeout ·
                              # connection · auth · bad_request · context_too_long · not_found · refusal · unknown
    status: Optional[int] = None
    retry_after: Optional[float] = None   # seconds the provider asked us to wait, if it said
    category: Optional[str] = None        # refusal category when kind == REFUSAL

    @property
    def retryable(self) -> bool:
        return self.kind == RETRYABLE


def _status_of(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    if isinstance(v, int) and 100 <= v <= 599:
        return v
    return None


def parse_retry_after(value: Any, now: Optional[float] = None) -> Optional[float]:
    """`Retry-After` as seconds: an integer/float, or an HTTP-date (→ seconds from `now`)."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value >= 0 else None
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    try:
        dt = email.utils.parsedate_to_datetime(s)
        secs = dt.timestamp() - (now if now is not None else time.time())
        return max(0.0, secs)
    except Exception:  # noqa: BLE001
        return None


def _retry_after_of(exc: BaseException) -> Optional[float]:
    v = getattr(exc, "retry_after", None)
    ra = parse_retry_after(v)
    if ra is not None:
        return ra
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            hv = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:  # noqa: BLE001
            hv = None
        ra = parse_retry_after(hv)
        if ra is not None:
            return ra
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        ra = parse_retry_after(err.get("retry_after") if isinstance(err, dict) else None)
        if ra is not None:
            return ra
    return None


_RE_CREDITS = re.compile(r"(requires more credits|insufficient[ _](credits|funds|balance|quota)|"
                         r"exceeded your current quota|credit balance is too low|payment required|"
                         r"billing (hard )?limit|out of credits)", re.I)


def out_of_credits(exc: BaseException) -> bool:
    """The provider account has no budget left (HTTP 402, or a quota/credit message under 429/400).
    Deliberately NOT a class of its own in ``classify``: a quota error stays RETRYABLE there, so a
    signed fallback still takes over when the primary account is empty. This answers only what
    to TELL the user (``agent/turn_failure.py``)."""
    return _status_of(exc) == 402 or bool(_RE_CREDITS.search(str(exc) or ""))


def classify(exc: BaseException) -> Classification:
    """What an error IS. Duck-typed on status codes, class names and the message, so it holds
    across every SDK (Anthropic, OpenAI-shaped, Google, boto, httpx) and for plain exceptions.
    An UNKNOWN error is TERMINAL: retrying a bug is not resilience, it is noise."""
    name = type(exc).__name__
    msg = str(exc) or ""
    status = _status_of(exc)
    ra = _retry_after_of(exc)

    # --- the platform's own silence limit (llm/streaming.py): a typed marker wins ---
    # `stalled` / `no_response` — the provider stopped sending: a retry or the signed fallback
    # may answer. `ceiling` — the answer was STILL being written at the absolute bound: a retry
    # writes the same long answer again, so it is terminal.
    _why = getattr(exc, "reason", None)
    if name == "LLMCallTimeout" and _why in ("stalled", "no_response", "ceiling"):
        if _why == "ceiling":
            return Classification(TERMINAL, "stream_ceiling", status)
        return Classification(RETRYABLE, "timeout", status)

    # --- refusals: the provider declined on content grounds (a typed marker wins) ---
    if isinstance(exc, ProviderRefusal):
        return Classification(REFUSAL, "refusal", status, None, exc.category)
    if name in ("ContentFilterFinishReasonError",) or (status in (400, 422) and _RE_REFUSE.search(msg)):
        return Classification(REFUSAL, "refusal", status, None, "content_filter")

    # --- rate limits + overload: retry, honouring the provider's own wait ---
    if status == 429 or name == "RateLimitError" or _RE_RATE.search(msg):
        return Classification(RETRYABLE, "rate_limit", status or 429, ra)
    if status in (502, 503, 504, 529) or name in ("InternalServerError", "ServerError") or _RE_OVERLOAD.search(msg):
        return Classification(RETRYABLE, "overloaded" if (status in (503, 529) or "overload" in msg.lower()) else "server_error", status, ra)
    if status is not None and 500 <= status <= 599:
        return Classification(RETRYABLE, "server_error", status, ra)
    if name in ("APITimeoutError", "TimeoutException", "ReadTimeout", "ConnectTimeout", "TimeoutError", "asyncio.TimeoutError") or _RE_TIMEOUT.search(msg):
        return Classification(RETRYABLE, "timeout", status, ra)
    if name in ("APIConnectionError", "ConnectError", "RemoteProtocolError", "ConnectionError", "ConnectionResetError", "EndpointConnectionError") or _RE_CONN.search(msg):
        return Classification(RETRYABLE, "connection", status, ra)

    # --- terminal: nothing a retry changes ---
    if _RE_CONTEXT.search(msg):
        return Classification(TERMINAL, "context_too_long", status)
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError") or _RE_AUTH.search(msg):
        return Classification(TERMINAL, "auth", status)
    if status == 404 or name == "NotFoundError":
        return Classification(TERMINAL, "not_found", status)
    if status in (400, 422) or name in ("BadRequestError", "UnprocessableEntityError", "ClientError"):
        return Classification(TERMINAL, "bad_request", status)
    return Classification(TERMINAL, "unknown", status)


def backoff(attempt: int, *, base: float = DEFAULT_BASE_DELAY, retry_after: Optional[float] = None,
            jitter: float = 0.0, cap: float = MAX_BACKOFF_SECONDS) -> float:
    """Seconds to wait BEFORE retry number `attempt` (1 = the first retry). A provider's
    `retry-after` is honoured verbatim up to MAX_RETRY_AFTER_HONOURED (its clock knows better
    than our schedule); otherwise base·2^(attempt-1) + jitter, capped."""
    if retry_after is not None and 0 <= retry_after <= MAX_RETRY_AFTER_HONOURED:
        return float(retry_after)
    n = max(1, int(attempt))
    return min(cap, float(base) * (2 ** (n - 1)) + max(0.0, float(jitter)))


TIMEOUT_MAX_ATTEMPTS = 2   # a hung provider is retried ONCE without a fallback; each attempt
                           # costs the whole attempt timeout, a turn cannot wait for five


def should_retry(cls: Classification, attempt: int, max_retries: int = DEFAULT_MAX_RETRIES) -> bool:
    """`attempt` = how many attempts have been MADE so far (≥1). A `timeout` is capped at
    TIMEOUT_MAX_ATTEMPTS — every retry of a hang costs the full attempt timeout."""
    if not cls.retryable:
        return False
    if cls.retry_after is not None and cls.retry_after > MAX_RETRY_AFTER_HONOURED:
        return False   # the provider asked us to come back later than a turn can wait
    if cls.reason == "timeout":
        max_retries = min(int(max_retries), TIMEOUT_MAX_ATTEMPTS)
    return attempt < max(1, int(max_retries))


def route_to_fallback_now(cls: Classification, has_fallback: bool) -> bool:
    """A TIMEOUT with a signed fallback bound is served by the fallback after the FIRST
    attempt: a second wait on a hung provider costs a whole attempt timeout for a small
    chance, while the fallback answers now. Every other retryable failure keeps its schedule."""
    return bool(has_fallback) and cls.retryable and cls.reason == "timeout"


# ── Finish reasons ───────────────────────────────────────────────────────────
_TRUNCATED = {"max_tokens", "length", "MAX_TOKENS", "FinishReason.MAX_TOKENS", "max_output_tokens", "end_turn_max_tokens"}
_REFUSED = {"refusal", "content_filter", "SAFETY", "FinishReason.SAFETY", "RECITATION", "FinishReason.RECITATION",
            "PROHIBITED_CONTENT", "FinishReason.PROHIBITED_CONTENT", "BLOCKLIST", "FinishReason.BLOCKLIST",
            "SPII", "FinishReason.SPII", "content_filtered", "guardrail_intervened"}


def finish_kind(finish: Any) -> str:
    """STOPPED / TRUNCATED / REFUSED for a provider's finish/stop reason (any SDK's spelling)."""
    if finish is None:
        return STOPPED
    f = getattr(finish, "name", None) or str(finish)
    if f in _TRUNCATED:
        return TRUNCATED
    if f in _REFUSED:
        return REFUSED
    return STOPPED


def refusal_category(finish: Any) -> str:
    f = getattr(finish, "name", None) or str(finish or "")
    f = f.replace("FinishReason.", "")
    return {"SAFETY": "safety", "RECITATION": "recitation", "PROHIBITED_CONTENT": "prohibited_content",
            "BLOCKLIST": "blocklist", "SPII": "sensitive_pii", "content_filter": "content_filter",
            "content_filtered": "content_filter", "refusal": "refusal", "guardrail_intervened": "guardrail"}.get(f, "refusal")


# ── Continuation ─────────────────────────────────────────────────────────────
CONTINUE_INSTRUCTION = ("Your previous message was cut off by the output limit. Continue EXACTLY from where "
                        "it stopped — do not repeat anything already written, do not restart or summarise, "
                        "do not add a preamble. Begin with the next character.")


def should_continue(finish: Any, continuations_done: int, partial_text: str) -> bool:
    """Ask for the rest of a truncated answer — bounded, and only when there IS a partial to
    continue from (an empty truncation is an error, not a continuation)."""
    return finish_kind(finish) == TRUNCATED and continuations_done < MAX_CONTINUATIONS and bool((partial_text or "").strip())


def continuation(history: List[Dict[str, Any]], prompt: str, partial_text: str) -> Tuple[List[Dict[str, Any]], str]:
    """The next call's (history, prompt): the question and the partial answer become the
    conversation so far, the prompt becomes the continue instruction. Provider-neutral —
    every branch of the handler builds its messages from exactly these two."""
    hist = list(history or [])
    hist.append({"role": "user", "content": prompt})
    hist.append({"role": "assistant", "content": partial_text})
    return hist, CONTINUE_INSTRUCTION


def join_continuation(parts: List[str]) -> str:
    """Concatenate the partial(s) and the continuation(s) — no separator: the model was told to
    begin with the next character. A continuation that repeats the partial's tail is trimmed
    (a common model habit) on the longest overlap ≥ 20 chars."""
    out = ""
    for p in parts:
        p = p or ""
        if not out:
            out = p
            continue
        k = min(len(out), len(p), 400)
        overlap = 0
        for n in range(k, 19, -1):
            if out.endswith(p[:n]):
                overlap = n
                break
        out += p[overlap:]
    return out


# ── Refusals ─────────────────────────────────────────────────────────────────
class ProviderRefusal(Exception):
    """The provider declined to answer on content grounds. TERMINAL, never retried, never
    handed to a fallback (a second model would be asked to override a safety decision) —
    the turn delivers an honest answer instead (`refusal_answer`)."""

    def __init__(self, category: str, provider: str, model: str, detail: str = ""):
        self.category = category or "refusal"
        self.provider = provider
        self.model = model
        self.detail = detail or ""
        super().__init__(f"{provider}/{model} refused ({self.category})" + (f": {detail}" if detail else ""))


_CATEGORY_TEXT = {
    "safety": "its safety filter",
    "content_filter": "its content filter",
    "recitation": "its recitation filter (the answer would have reproduced protected text)",
    "prohibited_content": "its prohibited-content policy",
    "blocklist": "a blocklist",
    "sensitive_pii": "its sensitive-personal-data filter",
    "guardrail": "a provider guardrail",
    "refusal": "a content policy",
}


def refusal_answer(category: str, provider: str, model: str) -> str:
    """The delivered, honest answer for a refusal: what happened, by whom, and what the reader
    can do — no canned "technical error", no pretence of an answer."""
    why = _CATEGORY_TEXT.get(category or "refusal", _CATEGORY_TEXT["refusal"])
    return (f"I could not answer this: the model ({provider} / {model}) declined the request under {why}. "
            f"This is the provider's decision, not a failure of the data or the tools. "
            f"Rephrasing the question, removing any sensitive detail it does not need, or asking a narrower "
            f"question usually resolves it.")


# ── The record ───────────────────────────────────────────────────────────────
@dataclass
class RecoveryStep:
    kind: str                       # retry · continuation · fallback · refusal
    reason: str                     # the classification reason / finish reason / breaker state
    from_model: str = ""            # "Provider/model" of the failing call
    to_model: str = ""              # for a fallback: the model that served instead; else the same
    attempt: int = 0                # retries: the attempt number this step precedes
    delay: float = 0.0              # retries: seconds waited
    status: Optional[int] = None
    detail: str = ""
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_model")
        d["to"] = d.pop("to_model")
        d["at"] = round(d["at"], 3)
        return d


def model_label(provider: Optional[str], model: Optional[str]) -> str:
    return f"{provider or '?'}/{model or '?'}"


def sealable(step: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of a step the provenance chain seals — identity + reason, never a timestamp,
    so an identical recovery hashes identically on replay."""
    return {k: step.get(k) for k in ("kind", "reason", "from", "to", "attempt", "status") if step.get(k) not in (None, "", 0) or k in ("kind", "reason", "from", "to")}
