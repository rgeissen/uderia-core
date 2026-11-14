"""Provider health — the process-wide circuit breaker per LLM provider (S4, the Resilience
Envelope; spec docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S4).

One breaker per provider name (`"Anthropic"`, `"Google"`, …), three states:

  closed    — healthy; every failure is counted, every success clears the count
  open      — `FAILURE_THRESHOLD` consecutive RETRYABLE failures inside `FAILURE_WINDOW_SECONDS`
              tripped it; calls for `open_for` seconds route to the fallback IMMEDIATELY instead
              of paying the timeout (when the profile has one — a breaker never refuses service
              for lack of an alternative: without a fallback the call is still attempted, once)
  half_open — the open period elapsed; ONE probe call is allowed through; success closes the
              breaker, failure re-opens it for twice as long (capped)

Only RETRYABLE failures (rate limit, overload, server, connection, timeout) count — a bad
request or a refusal is the call's own fault, not the provider's health. Everything here is
pure state + a clock (`now` injectable for tests); the honest UI reads `snapshot()` and shows
a strip ONLY while a breaker is open or half-open, with the recovery time.

Never raises into a turn. Off by construction when nobody records into it.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

FAILURE_THRESHOLD = 3            # consecutive retryable failures that open the breaker
FAILURE_WINDOW_SECONDS = 120.0   # …inside this window
OPEN_SECONDS = 60.0              # the first open period
OPEN_MAX_SECONDS = 600.0         # doubled per re-trip, capped here
PROBE_LEASE_SECONDS = 150.0      # a half-open probe that never reported back (cancelled from
                                 # outside the attempt loop) releases its lease after this

_LOCK = threading.Lock()
_BREAKERS: Dict[str, "Breaker"] = {}
_LISTENERS: List[Callable[[Dict[str, Any]], None]] = []


class Breaker:
    def __init__(self, provider: str):
        self.provider = provider
        self.state = CLOSED
        self.consecutive_failures = 0
        self.first_failure_at: Optional[float] = None
        self.opened_at: Optional[float] = None
        self.open_for = OPEN_SECONDS
        self.trips = 0
        self.last_reason: str = ""
        self.last_error_at: Optional[float] = None
        self.probe_in_flight = False
        self.probe_started_at: Optional[float] = None

    # ── reads ────────────────────────────────────────────────────────────
    def _refresh(self, now: float) -> None:
        if self.state == OPEN and self.opened_at is not None and now - self.opened_at >= self.open_for:
            self.state = HALF_OPEN
            self.probe_in_flight = False

    def allows(self, now: Optional[float] = None) -> bool:
        """May a call go to this provider right now? closed → yes; half_open → the ONE probe;
        open → no (route to the fallback; without one the caller may still try — see module doc)."""
        now = time.time() if now is None else now
        self._refresh(now)
        if self.state == CLOSED:
            return True
        if self.state == HALF_OPEN:
            if self.probe_in_flight and self.probe_started_at is not None and now - self.probe_started_at < PROBE_LEASE_SECONDS:
                return False
            # no probe, or the previous probe's lease expired without a report (it was
            # cancelled from outside the attempt loop — the executor's call timeout, a stopped
            # turn): this call becomes the probe. A latch here starved the primary forever.
            self.probe_in_flight = True
            self.probe_started_at = now
            return True
        return False

    def recovers_in(self, now: Optional[float] = None) -> Optional[float]:
        now = time.time() if now is None else now
        self._refresh(now)
        if self.state == OPEN and self.opened_at is not None:
            return max(0.0, self.open_for - (now - self.opened_at))
        return 0.0 if self.state == HALF_OPEN else None

    # ── writes ───────────────────────────────────────────────────────────
    def record_success(self, now: Optional[float] = None) -> Optional[str]:
        now = time.time() if now is None else now
        self._refresh(now)
        prev = self.state
        self.consecutive_failures = 0
        self.first_failure_at = None
        self.probe_in_flight = False
        if self.state in (HALF_OPEN, OPEN):
            self.state = CLOSED
            self.opened_at = None
            self.open_for = OPEN_SECONDS
        return f"{prev}->{self.state}" if prev != self.state else None

    def record_probe_answered(self, now: Optional[float] = None) -> Optional[str]:
        """A TERMINAL answer (auth, bad request, context too long…): the provider is
        reachable, so a half-open probe closes the breaker exactly as a success would; a
        closed breaker's failure count is left alone (a terminal error is not an outage)."""
        now = time.time() if now is None else now
        self._refresh(now)
        if self.state in (HALF_OPEN, OPEN):
            return self.record_success(now)
        return None

    def record_failure(self, reason: str, now: Optional[float] = None) -> Optional[str]:
        """A RETRYABLE failure (the caller classifies). Returns a transition label when the
        state changed."""
        now = time.time() if now is None else now
        self._refresh(now)
        prev = self.state
        self.last_reason = reason or ""
        self.last_error_at = now
        if self.state == HALF_OPEN:
            self.probe_in_flight = False
            self.trips += 1
            self.open_for = min(OPEN_MAX_SECONDS, self.open_for * 2)
            self.state = OPEN
            self.opened_at = now
            return f"{prev}->{self.state}"
        if self.state == OPEN:
            return None
        if self.first_failure_at is None or now - self.first_failure_at > FAILURE_WINDOW_SECONDS:
            self.first_failure_at = now
            self.consecutive_failures = 0
        self.consecutive_failures += 1
        if self.consecutive_failures >= FAILURE_THRESHOLD:
            self.state = OPEN
            self.opened_at = now
            self.trips += 1
            return f"{prev}->{self.state}"
        return None

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        self._refresh(now)
        return {"provider": self.provider, "state": self.state, "consecutive_failures": self.consecutive_failures,
                "trips": self.trips, "open_for": self.open_for, "recovers_in": self.recovers_in(now),
                "last_reason": self.last_reason, "last_error_at": self.last_error_at, "degraded": self.state != CLOSED}


# ── the process-wide registry ───────────────────────────────────────────────
def get(provider: str) -> Breaker:
    key = (provider or "?").strip()
    with _LOCK:
        b = _BREAKERS.get(key)
        if b is None:
            b = _BREAKERS[key] = Breaker(key)
        return b


def allows(provider: str, now: Optional[float] = None) -> bool:
    try:
        return get(provider).allows(now)
    except Exception:  # noqa: BLE001
        return True


def record_success(provider: str, now: Optional[float] = None) -> None:
    try:
        b = get(provider)
        t = b.record_success(now)
        if t:
            _notify(b, t)
    except Exception:  # noqa: BLE001
        pass


def record_probe_answered(provider: str, now: Optional[float] = None) -> None:
    try:
        b = get(provider)
        t = b.record_probe_answered(now)
        if t:
            _notify(b, t)
    except Exception:  # noqa: BLE001
        pass


def record_failure(provider: str, reason: str, now: Optional[float] = None) -> None:
    try:
        b = get(provider)
        t = b.record_failure(reason, now)
        if t:
            _notify(b, t)
    except Exception:  # noqa: BLE001
        pass


def snapshot(now: Optional[float] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        bs = list(_BREAKERS.values())
    return [b.snapshot(now) for b in bs]


def degraded(now: Optional[float] = None) -> List[Dict[str, Any]]:
    return [s for s in snapshot(now) if s["degraded"]]


def reset(provider: Optional[str] = None) -> None:
    with _LOCK:
        if provider is None:
            _BREAKERS.clear()
        else:
            _BREAKERS.pop(provider, None)


def add_listener(fn: Callable[[Dict[str, Any]], None]) -> None:
    """A transition listener (the live notification broadcaster). Never raises into a turn."""
    _LISTENERS.append(fn)


def _notify(b: Breaker, transition: str) -> None:
    snap = b.snapshot()
    snap["transition"] = transition
    for fn in list(_LISTENERS):
        try:
            fn(snap)
        except Exception:  # noqa: BLE001
            pass
