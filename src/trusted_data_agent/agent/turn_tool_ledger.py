"""
The per-turn tool ledger — the ReAct lane's "no repeated work" and "circuit breaker per tool".

Two rules, both turn-scoped (``contextvars``, like the loop guard and the agent principal, so
concurrent sessions never share a ledger), both applied at the ONE chokepoint every ReAct tool
call passes (``tool_enforcement._guarded``), both exempting ``TDA_*`` framework tools:

  * **Reuse.** When the model asks for a call whose (tool, arguments) signature already RAN
    AND SUCCEEDED earlier in this turn, the first result is handed back — zero tool time, zero
    model-facing error. The signature is ``assessors/tool_repetition.signature`` (the one
    definition of "the same call", shared with the loop guard and the Scorecard). A WRITE
    invalidates every memoised read before it runs: "count, insert, count again" must see the
    insert. Reads are memoised only; a failed call is never reused (the model is expected to
    correct it). Off with ``TDA_TOOL_RESULT_REUSE=0``.

  * **Breaker.** A tool that fails ``N`` times IN A ROW in one turn (the EXECUTION failing: it
    raised, timed out, or the server returned an error — never a platform refusal such as an
    authority denial or a preflight block, which are deterministic and already recovery-shaped)
    is disabled for the rest of the turn. The block is recovery-shaped: the model is told the
    tool is off and to answer with what it has. A success resets the count. ``N`` is
    ``APP_CONFIG.TOOL_BREAKER_FAILURES`` (env ``TDA_TOOL_BREAKER_FAILURES``, default 4; ``0``
    = off). Four, not three: the deterministic dialect retries absorb the common one-shot SQL
    refusals, so three consecutive failures of a data tool are already the model guessing.

Every entry point fails QUIET (no ledger outside a turn, any error → today's behaviour): a
ledger that cannot resolve itself must not stop work the user asked for.
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")

_LEDGER: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "uderia_turn_tool_ledger", default=None)

#: the shipped default: consecutive EXECUTION failures of one tool before it is disabled
DEFAULT_BREAKER_FAILURES = 4


def begin_turn() -> Any:
    """Start a fresh ledger for this turn. Returns the token to reset with."""
    return _LEDGER.set({"memo": {}, "failures": {}, "tripped": set(), "reused": 0})


def end_turn(token: Any) -> None:
    try:
        _LEDGER.reset(token)
    except (ValueError, LookupError):
        pass


def _ledger() -> Optional[Dict[str, Any]]:
    return _LEDGER.get()


def _framework(tool_name: Optional[str]) -> bool:
    return not tool_name or str(tool_name).startswith("TDA_")


def reuse_enabled() -> bool:
    v = os.environ.get("TDA_TOOL_RESULT_REUSE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def breaker_failures() -> int:
    """The breaker's ceiling: config, then env, then the default. ``0`` disables it."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        v = getattr(APP_CONFIG, "TOOL_BREAKER_FAILURES", None)
        if v is not None:
            return max(0, int(v))
    except Exception:  # noqa: BLE001
        pass
    try:
        return max(0, int(os.environ.get("TDA_TOOL_BREAKER_FAILURES", DEFAULT_BREAKER_FAILURES)))
    except (TypeError, ValueError):
        return DEFAULT_BREAKER_FAILURES


def _signature(tool_name: str, arguments: Any) -> str:
    from trusted_data_agent.agent.assurance.assessors.tool_repetition import signature
    return signature(tool_name, arguments or {})


# ── reuse ────────────────────────────────────────────────────────────────────────

def reuse(tool_name: str, arguments: Any) -> Optional[Any]:
    """The result of an identical call that SUCCEEDED earlier this turn, or ``None``."""
    led = _ledger()
    if led is None or _framework(tool_name) or not reuse_enabled():
        return None
    try:
        hit = led["memo"].get(_signature(tool_name, arguments))
        if hit is None:
            return None
        led["reused"] = int(led.get("reused", 0)) + 1
        logger.info(f"tool-ledger: '{tool_name}' — identical call earlier this turn, result reused")
        return hit
    except Exception as exc:  # noqa: BLE001 — a ledger must never break a turn
        logger.debug(f"tool-ledger: reuse skipped ({exc})")
        return None


def is_write(tool_name: str, arguments: Any) -> bool:
    """Whether the call mutates: by its SQL statement first, else by the tool's name."""
    try:
        from trusted_data_agent.agent.tool_enforcement import classify_sql_write
        if classify_sql_write(tool_name, arguments if isinstance(arguments, dict) else None):
            return True
        from trusted_data_agent.core.agent_authority import _looks_like_write
        return bool(_looks_like_write(tool_name))
    except Exception:  # noqa: BLE001 — unknown ⇒ treat as a write (never reuse across it)
        return True


def record_success(tool_name: str, arguments: Any, result: Any) -> None:
    """A call ran and succeeded: memoise a read, invalidate every memoised read on a write,
    and reset the tool's failure streak."""
    led = _ledger()
    if led is None or _framework(tool_name):
        return
    try:
        led["failures"][tool_name] = 0
        if is_write(tool_name, arguments):
            led["memo"].clear()           # the data may have changed: no read is current
            return
        led["memo"][_signature(tool_name, arguments)] = result
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tool-ledger: record skipped ({exc})")


# ── breaker ──────────────────────────────────────────────────────────────────────

def record_failure(tool_name: str) -> None:
    """An EXECUTION failed (raised, timed out, server error). Trips the breaker at the ceiling."""
    led = _ledger()
    if led is None or _framework(tool_name):
        return
    try:
        n = int(led["failures"].get(tool_name, 0)) + 1
        led["failures"][tool_name] = n
        ceiling = breaker_failures()
        if ceiling and n >= ceiling and tool_name not in led["tripped"]:
            led["tripped"].add(tool_name)
            logger.warning(f"tool-ledger: '{tool_name}' failed {n}× in a row this turn — "
                           f"disabled for the rest of the turn")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tool-ledger: failure record skipped ({exc})")


def breaker_block(tool_name: str) -> Optional[Dict[str, Any]]:
    """A recovery-shaped block when the tool's breaker has tripped this turn, else ``None``."""
    led = _ledger()
    if led is None or _framework(tool_name) or tool_name not in led.get("tripped", ()):
        return None
    n = int(led["failures"].get(tool_name, 0))
    return {
        "status": "error",
        "error": "tool_disabled_for_turn",
        "error_message": (
            f"'{tool_name}' failed {n} times in a row this turn and is disabled for the rest "
            f"of it. Do not call it again. Answer with what you already have, state plainly "
            f"what could not be computed, or use a different tool."),
        "tool_breaker": {"tool_name": tool_name, "failures": n, "ceiling": breaker_failures()},
    }


def stats() -> Dict[str, Any]:
    """What this turn's ledger did — for a log line or a test; empty outside a turn."""
    led = _ledger()
    if led is None:
        return {}
    return {"reused": int(led.get("reused", 0)),
            "tripped": sorted(led.get("tripped", ())),
            "memo_size": len(led.get("memo", {}))}
