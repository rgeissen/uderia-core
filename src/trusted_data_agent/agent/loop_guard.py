"""
The in-flight loop guard — stop a repeat BEFORE it runs, not after it is graded.

Today a tool loop is graded post-hoc: `no_tool_loops` reports it once the tokens are
spent and the user has waited. This is the same property (`tool_repetition`) consulted at
the tool chokepoint, so the guard and the Scorecard can never disagree about what a repeat
is — `assessors/tool_repetition.signature()` is the one definition.

FOUR PROPERTIES, EACH DELIBERATE:

  * **Off unless configured.** No `max_tool_repeats` ceiling ships, because how many
    repeats are legitimate depends on the agent's work — an orchestrator iterating dates
    calls one tool many times with DIFFERENT arguments (not a repeat), and a shipped
    default would convict patterns nobody has seen.
  * **It blocks the (N+1)th, never the retry.** A self-correction retry carries the same
    signature, so a guard tripping on the second call would break the platform's own
    recovery path.
  * **The block is RECOVERY-SHAPED**, not a silent failure — the same pattern the preflight
    SQL guard uses at this chokepoint. The agent is told it is repeating, so
    self-correction can do something else; a silent block would look like a broken tool.
  * **`TDA_` framework tools are exempt**, the same exemption Agent Authority makes here:
    they are the agent's own reporting and orchestration, not work being repeated.

Turn-scoped via `contextvars`, so concurrent sessions never share a ledger — the same
mechanism (and the same reason) as the agent principal.
"""

from __future__ import annotations

import contextvars
import logging
from collections import Counter
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")

_CALLS: contextvars.ContextVar[Optional[Counter]] = contextvars.ContextVar(
    "uderia_loop_guard_calls", default=None)


def begin_turn() -> Any:
    """Start a fresh ledger for this turn. Returns the token to reset with."""
    return _CALLS.set(Counter())


def end_turn(token: Any) -> None:
    try:
        _CALLS.reset(token)
    except (ValueError, LookupError):
        pass


def _ceiling() -> Optional[int]:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        v = (getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {}) or {}).get(
            "max_tool_repeats")
        if isinstance(v, dict):          # the 3-band shape {healthy, danger}
            v = v.get("danger")
        return int(v) if v is not None else None
    except (TypeError, ValueError, Exception):  # noqa: BLE001
        return None


def check_and_record(tool_name: str, arguments: Any) -> Optional[Dict[str, Any]]:
    """Record this call; return a recovery-shaped block if it exceeds the ceiling.

    Fails QUIET: no ledger (outside a turn), no ceiling, or any error → `None`, which is
    today's behaviour. A guard that cannot resolve its own configuration must not invent
    an enforcement — the opposite failure direction from a gate, and the right one here,
    because this one can stop work the user asked for.
    """
    ledger = _CALLS.get()
    if ledger is None or not tool_name or str(tool_name).startswith("TDA_"):
        return None
    try:
        from trusted_data_agent.agent.assurance.assessors.tool_repetition import signature
        sig = signature(tool_name, arguments)
        ledger[sig] += 1
        seen = ledger[sig]
        ceiling = _ceiling()
        if ceiling is None or seen <= ceiling:
            return None
        logger.warning(
            f"loop-guard: '{tool_name}' called {seen}× with identical arguments "
            f"(ceiling {ceiling}) — blocking the repeat")
        return {
            "status": "error",
            "error": "repeated_identical_call",
            # Recovery-shaped: the agent is TOLD it is repeating, so self-correction can
            # change course. A bare failure would read as a broken tool and invite a retry
            # of the very call being stopped.
            "error_message": (
                f"This exact call to '{tool_name}' has already been made {ceiling}× this "
                f"turn with identical arguments and returned the same result. Repeating "
                f"it will not produce new information — use what you already have, or "
                f"change the arguments."),
            "loop_guard": {"tool_name": tool_name, "repeats": seen, "ceiling": ceiling},
        }
    except Exception as exc:  # noqa: BLE001 — a guard must never break a turn
        logger.debug(f"loop-guard: skipped ({exc})")
        return None
