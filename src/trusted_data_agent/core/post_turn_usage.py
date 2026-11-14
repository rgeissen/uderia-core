"""One booking for an LLM call made AFTER its turn finished (Oct 2026).

Some calls run once the turn's ``/ask_stream`` has already closed — the agent-memory
extraction (backgrounded), the model-gap proposal suggestion (its own REST call). Each
used to book itself by hand, and each got a different subset right: one re-priced the
whole turn at the session model, one persisted its step in a shape no replay could read,
neither reached the page live. A call like this is booked HERE, once:

  1. the session's token totals                       (``update_token_count``)
  2. the turn's tokens AND its priced cost increment   (``update_turn_token_counts``,
     additive, ``added_cost_usd`` — never a re-price of the whole turn)
  3. the consumption ledger                            (``ConsumptionManager``)
  4. the step, persisted on the turn in the shape every replay reads
     (``{type, payload: {type, step, details}}`` in ``system_events``)
  5. live, on the per-user notification channel (the one channel open after a turn):
     a ``token_update`` with ABSOLUTE totals (idempotent) and the step itself, both
     session-tagged so the page applies them only to the session on screen.

The calls that run INSIDE an open stream (session naming, Smart Next Steps) keep their
stream path; the judge keeps its own (it predates this and already does all five).
Never raises — booking must never fail the call it books.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")


def step_event(event_type: str, step: str, details: Dict[str, Any]) -> Dict[str, Any]:
    """The persisted shape: ``{type, payload: {type, step, details}}``."""
    return {"type": event_type, "payload": {"type": event_type, "step": step, "details": dict(details or {})}}


def _notify(user_uuid: str, message: Dict[str, Any]) -> None:
    try:
        from trusted_data_agent.core.config import APP_STATE
        for q in list(APP_STATE.get("notification_queues", {}).get(user_uuid, set())):
            q.put_nowait(message)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[post-turn] notify skipped: {e}")


async def book_post_turn_call(user_uuid: str, session_id: Optional[str], turn_id: Optional[int], *,
                              event_type: str, step: str, call_id: str,
                              input_tokens: int, output_tokens: int, cost_usd: float,
                              provider: str = "", model: str = "",
                              details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Book one post-turn LLM call (see module doc). Returns the step's payload."""
    in_tok, out_tok, cost = int(input_tokens or 0), int(output_tokens or 0), float(cost_usd or 0.0)
    det = {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost,
           "provider": provider, "model": model, "session_id": session_id, "turn_id": turn_id,
           **(details or {})}
    evt = step_event(event_type, step, det)
    if not session_id:
        return evt["payload"]
    from trusted_data_agent.core import session_manager
    total_in = total_out = 0
    turn_in, turn_out = in_tok, out_tok
    try:
        if in_tok or out_tok:
            await session_manager.update_token_count(user_uuid, session_id, in_tok, out_tok)
        if turn_id is not None and (in_tok or out_tok or cost):
            await session_manager.update_turn_token_counts(user_uuid, session_id, turn_id, in_tok, out_tok,
                                                           added_cost_usd=cost, additive=True)
        refreshed = await session_manager.get_session(user_uuid, session_id) or {}
        total_in, total_out = refreshed.get("input_tokens", 0) or 0, refreshed.get("output_tokens", 0) or 0
        turn = next((t for t in (refreshed.get("last_turn_data", {}) or {}).get("workflow_history", []) or []
                     if t.get("turn") == turn_id), None)
        if turn:
            turn_in, turn_out = turn.get("turn_input_tokens", 0) or 0, turn.get("turn_output_tokens", 0) or 0
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[post-turn] session booking skipped ({event_type}): {e}")
    try:
        if in_tok or out_tok or cost:
            from trusted_data_agent.auth.database import get_db_session
            from trusted_data_agent.auth.consumption_manager import ConsumptionManager
            with get_db_session() as db:
                ConsumptionManager(db).update_turn_tokens(
                    user_id=user_uuid, session_id=session_id, turn_number=turn_id or 0,
                    additional_input_tokens=in_tok, additional_output_tokens=out_tok,
                    additional_cost_usd_cents=int(cost * 1000000))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[post-turn] consumption booking skipped ({event_type}): {e}")
    try:
        if turn_id is not None:
            await session_manager.append_system_events_to_turn(user_uuid, session_id, turn_id, [evt])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[post-turn] step persistence skipped ({event_type}): {e}")
    if in_tok or out_tok or cost:
        _notify(user_uuid, {"type": "token_update", "payload": {
            "statement_input": in_tok, "statement_output": out_tok,
            "turn_input": turn_in, "turn_output": turn_out,
            "total_input": total_in, "total_output": total_out,
            "call_id": call_id, "cost_usd": cost, "turn_id": turn_id, "session_id": session_id}})
    _notify(user_uuid, {"type": event_type, "payload": evt["payload"]})
    return evt["payload"]
