"""The Resilience Envelope (S4) — the per-turn runtime beside the pure policy.

`llm/resilience.py` decides what an error is and what to do; this module holds what a TURN
accumulated about it and stamps it onto the persisted turn at the one chokepoint every
engine's turn dict lands in (`session_manager.update_last_turn_data` — the prompt-cache /
agent-authority precedent): a `TurnResilience` in a task-local contextvar, armed by
`execution_service` for the turn and released in its `finally`; every recovery act — a
retry, a continuation, a fallback, a refusal — is RECORDED here by the handler and the
LangChain wrapper, broadcast LIVE as an `llm_recovery` Live Status step over the per-user
notification channel (the judge-step precedent: one delivery covers every engine, no
double render), and sealed by the executor into the provenance chain as `llm_recovery`.

The stamp (`turn["resilience"]`) is facts, never a verdict: the steps, the model that
SERVED the answer (`served_by`) when it was not the configured one, and a refusal if the
turn ended in one. Absent when nothing was recovered — an untouched turn is byte-identical.
"""
from __future__ import annotations

import contextvars
import logging
import time
from typing import Any, Dict, List, Optional

from trusted_data_agent.llm import resilience as _res

_log = logging.getLogger("quart.app")
_TURN: "contextvars.ContextVar[Optional[TurnResilience]]" = contextvars.ContextVar("uderia_turn_resilience", default=None)


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "RESILIENCE_ENVELOPE", False))
    except Exception:  # noqa: BLE001
        return False


class TurnResilience:
    def __init__(self, *, user_uuid: Optional[str] = None, session_id: Optional[str] = None,
                 primary_provider: Optional[str] = None, primary_model: Optional[str] = None,
                 fallback: Optional[Dict[str, Any]] = None):
        self.user_uuid = user_uuid
        self.session_id = session_id
        self.primary = {"provider": primary_provider, "model": primary_model}
        self.fallback = fallback            # the SIGNED fallback config {id, provider, model, credentials} or None
        self.steps: List[Dict[str, Any]] = []
        self.served_by: Optional[Dict[str, str]] = None
        self.refusal: Optional[Dict[str, Any]] = None
        self.started_at = time.time()

    def record(self, step: _res.RecoveryStep) -> Dict[str, Any]:
        d = step.to_dict()
        d["n"] = len(self.steps) + 1
        self.steps.append(d)
        if step.kind == _res.STEP_FALLBACK and step.to_model:
            prov, _, mod = step.to_model.partition("/")
            self.served_by = {"provider": prov, "model": mod}
        if step.kind == _res.STEP_REFUSAL:
            self.refusal = {"category": step.reason, "model": step.from_model, "detail": step.detail}
        return d

    def summary(self) -> Optional[Dict[str, Any]]:
        if not self.steps:
            return None
        kinds = {}
        for s in self.steps:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        return {"steps": [{k: v for k, v in st.items() if not k.startswith("_")} for st in self.steps], "counts": kinds, "primary": dict(self.primary),
                "served_by": dict(self.served_by) if self.served_by else None,
                "fallback": bool(self.served_by), "refusal": self.refusal, "at": self.started_at,
                # The fallback this turn was ALLOWED — the one the signed deployment names —
                # so a reader can tell "served by the signed fallback" (inside the signature)
                # from a model that is not it. Identity only, never credentials.
                "fallback_declared": ({"provider": self.fallback.get("provider"),
                                       "model": self.fallback.get("model")}
                                      if self.fallback else None)}


# ── arming ───────────────────────────────────────────────────────────────────
def set_turn(tr: Optional[TurnResilience]):
    return _TURN.set(tr)


def reset_turn(token) -> None:
    try:
        _TURN.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current() -> Optional[TurnResilience]:
    return _TURN.get()


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    tr = _TURN.get()
    return tr.summary() if tr is not None else None


# ── the signed fallback ─────────────────────────────────────────────────────
def resolve_fallback(profile: Optional[Dict[str, Any]], user_uuid: Optional[str]) -> Optional[Dict[str, Any]]:
    """The profile's fallback LLM — ONLY the configuration `llmFallbackConfigurationId` names
    and ONLY if the user owns it (it is part of the signed deployment config, so "which
    fallback" is itself attested). Anything else ⇒ None: there is no fallback, and the
    handler must refuse to improvise one."""
    try:
        fid = (profile or {}).get("llmFallbackConfigurationId")
        if not fid or not user_uuid:
            return None
        if fid == (profile or {}).get("llmConfigurationId"):
            return None   # a fallback that IS the primary is no fallback
        from trusted_data_agent.core.config_manager import get_config_manager
        cfgs = get_config_manager().get_llm_configurations(user_uuid) or []
        cfg = next((c for c in cfgs if c.get("id") == fid), None)
        if not cfg or not cfg.get("provider") or not cfg.get("model"):
            return None
        return {"id": fid, "provider": cfg["provider"], "model": cfg["model"], "credentials": cfg.get("credentials") or {}}
    except Exception as e:  # noqa: BLE001
        _log.debug(f"[resilience] fallback not resolved: {e}")
        return None


def served_model(provider: Optional[str], model: Optional[str]):
    """The (provider, model) that SERVED this turn — the signed fallback when one served,
    else the configured pair given. ONE rule for every engine's `models_used` record."""
    tr = _TURN.get()
    if tr is not None and tr.served_by:
        return tr.served_by.get("provider") or provider, tr.served_by.get("model") or model
    return provider, model


def fallback_for_turn() -> Optional[Dict[str, Any]]:
    tr = _TURN.get()
    return tr.fallback if tr is not None else None


# ── the live step ────────────────────────────────────────────────────────────
STEP_TITLE = {_res.STEP_RETRY: "Provider Retry", _res.STEP_CONTINUATION: "Answer Continued",
              _res.STEP_FALLBACK: "Served by Fallback", _res.STEP_REFUSAL: "Provider Refusal"}


def recovery_event(step: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
    """ONE event shape for the live channel AND the reload synthesis (the stamp carries the
    same dicts, so a plan reload renders the identical card)."""
    d = dict(step)
    d["session_id"] = session_id
    d["summary"] = describe(step)
    return {"step": STEP_TITLE.get(step.get("kind"), "LLM Recovery"), "type": "llm_recovery", "details": d}


def describe(step: Dict[str, Any]) -> str:
    k = step.get("kind"); r = step.get("reason") or ""
    frm = step.get("from") or "?"; to = step.get("to") or frm
    if k == _res.STEP_RETRY:
        return f"{frm} failed ({r}); retried after {float(step.get('delay') or 0):.1f}s (attempt {step.get('attempt')})"
    if k == _res.STEP_CONTINUATION:
        return f"{frm} stopped at its output limit ({r}); asked to continue exactly where it stopped (continuation {step.get('attempt')})"
    if k == _res.STEP_FALLBACK:
        return f"{frm} could not serve ({r}); the signed fallback {to} served instead — cost billed at {to}"
    if k == _res.STEP_REFUSAL:
        return f"{frm} refused the request ({r}); an honest answer naming the refusal was delivered"
    return f"{k}: {r}"


def record(step: _res.RecoveryStep) -> Optional[Dict[str, Any]]:
    """Record one recovery act for the turn and broadcast it live. No-op outside an armed turn.
    Never raises."""
    tr = _TURN.get()
    if tr is None:
        return None
    try:
        d = tr.record(step)
    except Exception:  # noqa: BLE001
        return None
    _broadcast(tr, d)
    return d


def _broadcast(tr: TurnResilience, step: Dict[str, Any]) -> None:
    try:
        import asyncio
        from trusted_data_agent.core.config import APP_STATE
        asyncio.get_running_loop()
        evt = recovery_event(step, tr.session_id)
        for q in list(APP_STATE.get("notification_queues", {}).get(tr.user_uuid, set())):
            try:
                q.put_nowait({"type": "llm_recovery", "payload": evt})
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass


def broadcast_provider_health(user_uuid: Optional[str], snap: Dict[str, Any]) -> None:
    """A breaker transition, live to ONE user's header strip (the turn's owner — a shared
    process serves many users, but only the one whose call tripped it is told; the strip
    re-reads `/v1/llm/provider-health` on open, so nobody is left with a stale reading)."""
    try:
        import asyncio
        from trusted_data_agent.core.config import APP_STATE
        asyncio.get_running_loop()
        for q in list(APP_STATE.get("notification_queues", {}).get(user_uuid, set())):
            try:
                q.put_nowait({"type": "provider_health", "payload": snap})
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass


# ── the breaker's transitions reach the turn's owner live ───────────────────────────────────
def _on_breaker_transition(snap: Dict[str, Any]) -> None:
    tr = _TURN.get()
    if tr is not None and tr.user_uuid:
        broadcast_provider_health(tr.user_uuid, snap)


try:
    from trusted_data_agent.core import provider_health as _ph
    _ph.add_listener(_on_breaker_transition)
except Exception:  # noqa: BLE001
    pass
