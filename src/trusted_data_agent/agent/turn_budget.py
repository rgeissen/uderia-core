"""The Visible Budget (S5) — a turn that paces itself, and hands back what is left.

Spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S5. ONE budget per profile in THREE
units — `steps` (tool calls the agent may make), `tokens` (input + output the turn may spend),
`seconds` (wall clock since the turn began) — declared as `profile.turnBudget` (portable,
SIGNED: how much an agent may spend is posture), armed per turn under the deployment switch
`APP_CONFIG.TURN_BUDGET`. Absent ⇒ nothing here ever runs (byte-identical baseline).

The model SEES its budget: at every tool boundary (the two chokepoints every data-running
engine passes through) the tool result carries a one-line `[Budget]` note with what is left;
when a unit is exhausted the note becomes the instruction to finish with what it has and to
say what is left — and a further tool call is REFUSED, recovery-shaped (the model answers
instead of being killed). Exhaustion is therefore a recorded GRACEFUL finish; the platform's
hard stops (the per-run cost cap, the ReAct input-token cap, the iteration cap, the call
timeout) remain the last line, one unit above.

Pure core (no I/O, no clock inside — `now` is injected) + a per-turn contextvar runtime.
"""
from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("quart.app")
UNITS = ("steps", "tokens", "seconds")
NEAR_SHARE = 0.75          # from this share on, the note names the unit as nearly exhausted
NOTE_TAG = "[Budget]"
EXHAUSTED_TAG = "[Budget exhausted"


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "TURN_BUDGET", False))
    except Exception:  # noqa: BLE001
        return False


# ── the pure core ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Budget:
    steps: Optional[int] = None
    tokens: Optional[int] = None
    seconds: Optional[float] = None

    def units(self) -> List[str]:
        return [u for u in UNITS if getattr(self, u) is not None]

    def any(self) -> bool:
        return bool(self.units())


def parse(profile: Optional[Dict[str, Any]]) -> Optional[Budget]:
    """The profile's `turnBudget` → a Budget, or None when no unit is set (a zero or a
    negative is not a budget; a non-number is ignored, never a crash)."""
    tb = (profile or {}).get("turnBudget")
    if not isinstance(tb, dict):
        return None
    def _num(v, as_int=True):
        try:
            if v in (None, "", False):
                return None
            f = float(v)
            if f <= 0:
                return None
            return int(f) if as_int else f
        except (TypeError, ValueError):
            return None
    b = Budget(steps=_num(tb.get("steps")), tokens=_num(tb.get("tokens")), seconds=_num(tb.get("seconds"), as_int=False))
    return b if b.any() else None


def remaining(budget: Budget, used: Dict[str, float]) -> Dict[str, Dict[str, float]]:
    """Per configured unit: {used, total, left, share}."""
    out: Dict[str, Dict[str, float]] = {}
    for u in budget.units():
        total = float(getattr(budget, u))
        got = float(used.get(u) or 0)
        out[u] = {"used": got, "total": total, "left": max(0.0, total - got), "share": min(1.0, got / total) if total else 1.0}
    return out


def exhausted_unit(budget: Budget, used: Dict[str, float]) -> Optional[str]:
    """The FIRST exhausted unit (in steps · tokens · seconds order), or None."""
    for u, r in remaining(budget, used).items():
        if r["used"] >= r["total"]:
            return u
    return None


def _fmt_tokens(n: float) -> str:
    n = int(n)
    return f"~{n/1000:.0f}k" if n >= 1000 else str(n)


def _fmt_unit(u: str, r: Dict[str, float]) -> str:
    if u == "steps":
        return f"{int(r['used'])} of {int(r['total'])} steps used"
    if u == "tokens":
        return f"{_fmt_tokens(r['used'])} of {_fmt_tokens(r['total'])} tokens used"
    return f"{int(r['used'])} of {int(r['total'])} s used"


def note(budget: Budget, used: Dict[str, float]) -> str:
    """The one-line note the model reads at the tool boundary. Always short; names a unit
    that is nearly exhausted; when a unit IS exhausted it is the finish instruction."""
    rem = remaining(budget, used)
    ex = exhausted_unit(budget, used)
    parts = [_fmt_unit(u, r) for u, r in rem.items()]
    if ex is not None:
        return (f"{EXHAUSTED_TAG} — {ex}] {' · '.join(parts)}. Do not call any more tools. "
                f"Answer NOW with what you have, and state plainly what is left undone.")
    near = [u for u, r in rem.items() if r["share"] >= NEAR_SHARE]
    if near:
        return (f"{NOTE_TAG} {' · '.join(parts)} — the {near[0]} budget is nearly used: finish with what you "
                f"have; if something must remain undone, say what.")
    return f"{NOTE_TAG} {' · '.join(parts)}."


def refusal(budget: Budget, used: Dict[str, float], tool_name: str) -> Dict[str, Any]:
    """The recovery-shaped refusal of a tool call AFTER exhaustion — the model answers with
    what it has instead of being killed mid-turn."""
    ex = exhausted_unit(budget, used) or "budget"
    rem = remaining(budget, used)
    return {"error": (f"{EXHAUSTED_TAG} — {ex}] `{tool_name}` was not run: the turn's {ex} budget is used up "
                      f"({' · '.join(_fmt_unit(u, r) for u, r in rem.items())}). Do not call more tools. "
                      f"Answer now with what you already have, and state plainly what is left undone."),
            "budget_exhausted": ex, "tool_name": tool_name}


def finish_line(state: Dict[str, Any]) -> str:
    """The quiet line under the answer: within budget, or exhausted and finished gracefully."""
    units = state.get("units") or []
    parts = [_fmt_unit(u["unit"], u) for u in units]
    if state.get("exhausted"):
        return f"budget exhausted ({state['exhausted']}) — stopped gracefully; {' · '.join(parts)}"
    return f"finished within budget — {' · '.join(parts)}"


# ── the per-turn runtime ────────────────────────────────────────────────────────────────────
class TurnBudget:
    def __init__(self, budget: Budget, *, user_uuid: Optional[str] = None, session_id: Optional[str] = None,
                 now: Optional[float] = None):
        self.budget = budget
        self.user_uuid = user_uuid
        self.session_id = session_id
        self.started_at = time.time() if now is None else now
        self.steps = 0
        self.tokens = 0
        self.refusals = 0
        self.notes = 0
        self.killed: Optional[str] = None      # a HARD stop fired (recorded by the engine)
        self._claims = 0                       # steps claimed at the gate, not yet paired with after_tool

    def used(self, now: Optional[float] = None) -> Dict[str, float]:
        now = time.time() if now is None else now
        return {"steps": self.steps, "tokens": self.tokens, "seconds": max(0.0, now - self.started_at)}

    def record_step(self, tool_name: Optional[str] = None) -> int:
        if tool_name and str(tool_name).startswith("TDA_"):
            return self.steps           # framework tools never count as a step
        self.steps += 1
        return self.steps

    def claim(self, tool_name: Optional[str] = None) -> int:
        """The gate admits a data tool: the step is counted NOW and remembered as a claim so
        the matching after_tool does not count it twice."""
        if tool_name and str(tool_name).startswith("TDA_"):
            return self.steps
        self._claims += 1
        return self.record_step(tool_name)

    def claim_seen(self, tool_name: Optional[str] = None) -> bool:
        """after_tool consumes one outstanding claim (True) — or finds none (False: an
        ungated caller, which then counts the step itself)."""
        if tool_name and str(tool_name).startswith("TDA_"):
            return True
        if self._claims > 0:
            self._claims -= 1
            return True
        return False

    def record_tokens(self, n: int) -> int:
        self.tokens += max(0, int(n or 0))
        return self.tokens

    def exhausted(self, now: Optional[float] = None) -> Optional[str]:
        return exhausted_unit(self.budget, self.used(now))

    def note(self, now: Optional[float] = None) -> str:
        self.notes += 1
        return note(self.budget, self.used(now))

    def refuse(self, tool_name: str, now: Optional[float] = None) -> Dict[str, Any]:
        self.refusals += 1
        return refusal(self.budget, self.used(now), tool_name)

    def state(self, now: Optional[float] = None) -> Dict[str, Any]:
        rem = remaining(self.budget, self.used(now))
        ex = exhausted_unit(self.budget, self.used(now))
        return {"units": [{"unit": u, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}} for u, r in rem.items()],
                "exhausted": ex, "graceful": bool(ex) and not self.killed, "killed": self.killed,
                "refusals": self.refusals, "started_at": self.started_at}


_TURN: "contextvars.ContextVar[Optional[TurnBudget]]" = contextvars.ContextVar("uderia_turn_budget", default=None)


def set_turn(tb: Optional[TurnBudget]):
    return _TURN.set(tb)


def reset_turn(token) -> None:
    try:
        _TURN.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current() -> Optional[TurnBudget]:
    return _TURN.get()


def arm(profile: Optional[Dict[str, Any]], *, user_uuid: Optional[str] = None, session_id: Optional[str] = None):
    """Arm the turn (the execution seam): the switch on AND the profile declares a budget."""
    if not enabled():
        return None
    b = parse(profile)
    if b is None:
        return None
    return set_turn(TurnBudget(b, user_uuid=user_uuid, session_id=session_id))


# ── the boundary seams (both chokepoints call these; each is a no-op with no armed turn) ────
def tool_gate(tool_name: str) -> Optional[Dict[str, Any]]:
    """BEFORE a tool runs: a refusal dict once a unit is exhausted (framework `TDA_*` tools are
    never refused — the report must still be written), else None — and an ADMITTED data tool
    CLAIMS its step here, at the boundary. Counting after the run would let a model that fires
    N tool calls in one round (parallel tool calls, run under `asyncio.gather`) pass every one
    of them through a gate that still read "0 used": the budget must be RESERVED before the
    work, so the (budget+1)th call in a batch is the one refused."""
    tb = _TURN.get()
    if tb is None or not tool_name or str(tool_name).startswith("TDA_"):
        return None
    try:
        if tb.exhausted() is not None:
            r = tb.refuse(tool_name)
            _broadcast(tb)
            return r
        tb.claim(tool_name)
    except Exception:  # noqa: BLE001
        return None
    return None


def after_tool(tool_name: str) -> Optional[str]:
    """AFTER a tool ran: the note the result should carry (the step was claimed at the gate;
    a caller that did not gate — the Optimize adapter seam, where the plan is fixed and no
    refusal is needed — claims it here)."""
    tb = _TURN.get()
    if tb is None or not tool_name:
        return None
    try:
        if not tb.claim_seen(tool_name):
            tb.record_step(tool_name)
        n = tb.note()
        _broadcast(tb)
        return n
    except Exception:  # noqa: BLE001
        return None


def record_tokens(n: int) -> None:
    tb = _TURN.get()
    if tb is None:
        return
    try:
        tb.record_tokens(n)
    except Exception:  # noqa: BLE001
        pass


def report_note() -> Optional[str]:
    """For the Optimize final report: the exhausted-instruction when a unit is exhausted (the
    report is written from what was collected; the model must say what is left)."""
    tb = _TURN.get()
    if tb is None:
        return None
    try:
        if tb.exhausted() is not None:
            return note(tb.budget, tb.used())
    except Exception:  # noqa: BLE001
        pass
    return None


def mark_killed(reason: str) -> None:
    tb = _TURN.get()
    if tb is not None:
        tb.killed = reason


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    tb = _TURN.get()
    if tb is None:
        return None
    try:
        st = tb.state()
        st["finish"] = finish_line(st)
        return st
    except Exception:  # noqa: BLE001
        return None


def _broadcast(tb: TurnBudget) -> None:
    """The live bar: the turn's budget state over the per-user notification channel (the
    judge-step precedent — the chokepoints have no request stream). Never raises."""
    try:
        import asyncio
        from trusted_data_agent.core.config import APP_STATE
        asyncio.get_running_loop()
        payload = tb.state()
        payload["session_id"] = tb.session_id
        for q in list(APP_STATE.get("notification_queues", {}).get(tb.user_uuid, set())):
            try:
                q.put_nowait({"type": "turn_budget", "payload": payload})
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
