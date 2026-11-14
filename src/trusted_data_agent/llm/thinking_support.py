"""Whether a Google model will accept a request that DISABLES thinking.

Disabling thinking (`thinking_budget=0`) is an OPTIMISATION — a cheaper session
name, a user's cost setting — never a correctness requirement.  Some Gemini
models refuse it outright.  Measured against the live API on 2026-09-09:

    model                    budget=0   budget=-1   budget=1024   no budget
    gemini-3.1-flash-lite    200        200         200           200
    gemini-3.5-flash-lite    400 !!     200         200           200
    gemini-3.5-flash         200        200         200           200
    gemini-2.5-flash-lite    200        200         200           200

so `gemini-3.5-flash-lite` answers `400 INVALID_ARGUMENT / "Request contains an
invalid argument."` to a request that turns thinking OFF, while accepting every
other budget.  Thinking is configurable on it; it just cannot be switched off.

**The capability is not discoverable.**  `GET /v1beta/models/<id>` reports
`"thinking": true` for the model that accepts 0 and the model that refuses it
alike, and a version-number rule would be a guess of exactly the kind that
produced the defect (3.1 accepts, 3.5-flash-lite refuses, 3.5-flash accepts).
So the only honest source is the API's own refusal — learned once per process
and consulted by every Google call site, so the doomed request is attempted at
most once per model and never again.

Because disabling thinking is only ever an optimisation, a refusal degrades to
"thinking enabled" — never to a failed turn.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("quart.app")

# Models proven — by their own refusal — unable to have thinking disabled.
# Deliberately NOT seeded with a hardcoded list: a list is an arms race, and a
# model that changes behaviour would leave a stale claim nothing re-checks.
_REFUSES_DISABLED_THINKING: set[str] = set()


def normalize_model(model: str | None) -> str:
    """`models/Gemini-3.5-Flash-Lite` and `gemini-3.5-flash-lite` are one model."""
    name = (model or "").strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name.lower()


def supports_disabled_thinking(model: str | None) -> bool:
    """True unless this model has already refused a disabled thinking budget.

    Optimistic by design: a model we have never met is tried, and the refusal —
    if it comes — is recovered from and remembered.
    """
    return normalize_model(model) not in _REFUSES_DISABLED_THINKING


def record_disable_refusal(model: str | None) -> None:
    """Record that `model` cannot have thinking disabled.

    Call this ONLY once a retry *without* the thinking config has succeeded —
    that is what proves the thinking config was the cause, rather than blaming
    it for an unrelated 400 and permanently losing the optimisation.
    """
    name = normalize_model(model)
    if not name or name in _REFUSES_DISABLED_THINKING:
        return
    _REFUSES_DISABLED_THINKING.add(name)
    logger.warning(
        "[ThinkingBudget] Model '%s' refuses a disabled thinking budget "
        "(400 INVALID_ARGUMENT). Proceeding with thinking ENABLED for it — a "
        "configured thinking budget of 0 is not honoured for this model, so "
        "its calls will spend thinking tokens.", name
    )


def looks_like_thinking_disable_refusal(exc: BaseException) -> bool:
    """Does this exception look like the API rejecting the request arguments?

    Tolerates both shapes this reaches us in: the native SDK's `ClientError`
    (`.code` / `.status`) and the LangChain wrapper's `ChatGoogleGenerativeAIError`,
    which carries the same facts only inside its message text.

    Deliberately broad — any 400 from the model.  The API's message is generic
    ("Request contains an invalid argument") and its status has been seen worded
    both as INVALID_ARGUMENT and as "Bad Request", so a narrower string match
    would be exactly the brittleness this module exists to avoid.  The precision
    lives in the two caller guards instead: this is consulted ONLY for a request
    the caller knows carried a zero budget, and the refusal is recorded ONLY if
    dropping the thinking config actually fixes it.  An unrelated 400 therefore
    costs one extra request and surfaces as itself, claiming nothing.
    """
    if getattr(exc, "code", None) == 400:
        return True
    text = str(exc).upper()
    return "400" in text and ("INVALID_ARGUMENT" in text or "BAD REQUEST" in text)


#: A caller that sized its output budget for "no thinking" needs room once
#: thinking cannot be switched off. 1024 covers the reasoning these models spend
#: on a short task (measured: ~93-94 tokens for a session title) with margin,
#: while still bounding a call the caller deliberately capped.
THINKING_HEADROOM_TOKENS = 1024


def with_thinking_headroom(gen_config_kwargs: dict) -> dict:
    """Raise an output ceiling that was sized on the assumption thinking was off.

    Session-name generation caps output at 100 tokens — ample for a 3-5 word
    title, but not once the model also reasons inside that budget.  Measured on
    2026-09-09: with thinking left on, `gemini-3.6-flash` spent 94 of the 100
    tokens thinking and returned the title `/Draft`, and `gemini-3.1-pro-preview`
    spent 93 and returned a truncated one.  The answer itself stays as short as
    the prompt asks; only the ceiling moves.

    Returns the mapping unchanged (same object) when no ceiling needs lifting.
    """
    cap = gen_config_kwargs.get("max_output_tokens")
    if cap is None or cap >= THINKING_HEADROOM_TOKENS:
        return gen_config_kwargs
    return {**gen_config_kwargs, "max_output_tokens": THINKING_HEADROOM_TOKENS}


def reset() -> None:
    """Forget everything learned. For tests only."""
    _REFUSES_DISABLED_THINKING.clear()
