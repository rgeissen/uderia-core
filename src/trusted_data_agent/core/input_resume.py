"""
Resuming a turn that stopped to ask — ONE rule for every face (PROP-011 §4.6).

The chat (`/ask_stream` with `input_answer`), the REST route (`POST /v1/input-requests/<id>/
answer`, `POST /v1/tasks/<id>/input`) and, later, the Inbox all call `accept()`: it validates
the person's act — the question is theirs and pending, the resume binding is this server's
and bound to them, the answer fits the options or the schema — consumes the binding ONCE,
and returns what the next turn runs with: the structured block the model reads, the line the
person sees as theirs, and the binding the ledger arms so the step that asked receives the
chosen value in code. Nothing here runs the turn.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("quart.app")


def accept(user_uuid: str, request_id: str, *, decision: str, values: Any, resume: Optional[str],
           via: str, db_path: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """`(resumption, None, 200)` or `(None, reason, http_status)`.

    `resumption`: `{"request": record, "user_input": block, "display_message": line,
    "input_answer": {"request", "decision", "values"}, "session_id", "task_id", "origin"}`."""
    from trusted_data_agent.agent import input_requests as ir
    from trusted_data_agent.core import input_request_store as store

    decision = str(decision or "accept").strip().lower()
    if decision not in store.DECISIONS:
        return None, f"decision must be one of {list(store.DECISIONS)}", 400
    rec = store.get(request_id, user_uuid, db_path=db_path)
    if rec is None:
        return None, "no such question of yours", 404
    if rec["status"] != "pending":
        return None, f"this question is already {rec['status']}", 409
    why = store.verify_resume(resume, rec, user_uuid)
    if why:
        return None, why, 403
    norm = None
    if decision == "accept":
        norm, err = ir.validate_answer(rec, values)
        if err:
            return None, err, 400
    consumed, err = store.consume(request_id, user_uuid=user_uuid, decision=decision, answer=norm,
                                  answered_via=via, db_path=db_path)
    if err:
        return None, err, 409
    # a relayed question (phase 3): the person's one answer is the child's answer too — the
    # chain's record says it came through the parent; the child's turn resumes when the parent
    # re-runs its ask (`resume_child`), never on its own
    if consumed.get("parent_id"):
        store.consume(consumed["parent_id"], user_uuid=user_uuid, decision=decision, answer=norm,
                      answered_via="relay", answered_by=f"relay({consumed['id']})", db_path=db_path)
    block, display = ir.reentry_block(consumed, decision, norm, _original_query(consumed))
    try:
        from trusted_data_agent.core import ui_notify  # noqa: F401 — the notice below is the component's change event
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(user_uuid, "input_answered", f"a question was {consumed['status']}",
                                      session_id=consumed.get("session_id"), profile_tag=consumed.get("profile_tag"),
                                      severity="info", notice_type="input_request_settled",
                                      extra={"request_id": consumed["id"], "status": consumed["status"],
                                             "answer": consumed.get("answer")})
    except Exception:  # noqa: BLE001 — a notice never decides the answer
        pass
    return ({"request": consumed, "user_input": block, "display_message": display,
             "input_answer": {"request": consumed, "decision": decision, "values": norm},
             "session_id": consumed.get("session_id"), "task_id": consumed.get("task_id"),
             "origin": consumed.get("origin")}, None, 200)


def _original_query(rec: Dict[str, Any]) -> Optional[str]:
    """The request the question interrupted — the turn's own user_query, read from the session."""
    try:
        import asyncio
        from trusted_data_agent.core import session_manager
        sid, uid, turn = rec.get("session_id"), rec.get("user_uuid"), rec.get("turn")
        if not (sid and uid):
            return None
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            return None   # the async faces pass it themselves through `with_original_query`
        data = asyncio.run(session_manager.get_session(uid, sid))
        return _query_of(data, turn)
    except Exception:  # noqa: BLE001
        return None


def _query_of(session_data: Optional[Dict[str, Any]], turn: Any) -> Optional[str]:
    wf = ((session_data or {}).get("last_turn_data") or {}).get("workflow_history") or []
    for t in wf:
        try:
            if turn is not None and int(t.get("turn")) == int(turn):
                return t.get("user_query")
        except (TypeError, ValueError):
            continue
    return (wf[-1].get("user_query") if wf else None)


async def remember(resumption: Dict[str, Any], scope: Optional[str]) -> Optional[str]:
    """Decision 10 — the person asked that this answer hold: `session` (this conversation,
    anyone) or `agent` (always, the agent's owner: `inputPolicy.defaults`). Only an ACCEPTED
    single-item choose/confirm is remembered; never an approval or an auth. Returns the key
    stored, or None when nothing was remembered. Never raises."""
    from trusted_data_agent.agent import input_requests as ir
    try:
        if scope not in ("session", "agent"):
            return None
        rec = resumption.get("request") or {}
        ia = resumption.get("input_answer") or {}
        if ia.get("decision") != "accept":
            return None
        key = ir.default_key_of(rec)
        if not key:
            return None
        vals = ia.get("values") or {}
        value = next(iter(vals.values()), None)
        if value is None or isinstance(value, (list, dict)):
            return None
        user_uuid, sid = rec.get("user_uuid"), rec.get("session_id")
        if scope == "session":
            if not sid:
                return None
            from trusted_data_agent.core import session_manager
            await session_manager.set_input_default(user_uuid, sid, key, value)
            return key
        pid = rec.get("profile_id")
        if not pid:
            return None
        from trusted_data_agent.core.config_manager import get_config_manager
        cm = get_config_manager()
        prof = cm.get_profile(pid, user_uuid)   # the caller's own agent, or nothing
        if not prof:
            return None
        auth = dict(prof.get("authorityConfig") or prof.get("identityConfig") or {})
        ip = dict(auth.get("inputPolicy") or {})
        defaults = dict(ip.get("defaults") or {})
        defaults[key] = value
        ip["defaults"] = defaults
        auth["inputPolicy"] = ip
        cm.update_profile(pid, {"authorityConfig": auth}, user_uuid)
        return key
    except Exception as e:  # noqa: BLE001
        logger.warning(f"needs-input: remembering the answer failed: {e}")
        return None


async def resume_child(app, user_uuid: str, child_request_id: str, *, decision: str, values: Any,
                       profile_id: Optional[str] = None, db_path: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Phase 3 — the answer travels DOWN: start the child conversation's re-entry turn with the
    person's answer (already consumed onto the child's record by `accept`), in-process, as the
    owner. `(task_id, None)` or `(None, why)`. The child re-enters exactly as a directly
    answered question would: the structured block as its input, the ledger armed with the
    answer, the resolver that asked receiving the chosen value."""
    from trusted_data_agent.agent import input_requests as ir
    from trusted_data_agent.core import input_request_store as store
    rec = store.get(child_request_id, user_uuid, db_path=db_path)
    if rec is None:
        return None, "the child's question is not on record"
    if rec["status"] == "pending":
        # the parent's accept did not reach it (an older record): consume it now, as the relay
        rec, err = store.consume(child_request_id, user_uuid=user_uuid, decision=decision, answer=values,
                                 answered_via="relay", answered_by="relay", db_path=db_path)
        if err:
            return None, err
    if rec["status"] not in ("accepted", "declined", "cancelled"):
        return None, f"the child's question is {rec['status']}"
    ia = {"request": rec, "decision": decision, "values": values if decision == "accept" else None}
    block, display = ir.reentry_block(rec, decision, ia["values"], None)
    res = {"request": rec, "user_input": block, "display_message": display, "input_answer": ia,
           "session_id": rec.get("session_id"), "task_id": rec.get("task_id"), "origin": rec.get("origin")}
    res = await with_original_query(res)
    payload = {"prompt": res["user_input"], "display_message": res["display_message"], "input_answer": res["input_answer"],
               "source": "ask", "_resume_task_id": res.get("task_id")}
    if profile_id or rec.get("profile_id"):
        payload["profile_id"] = profile_id or rec.get("profile_id")
    try:
        from trusted_data_agent.api.rest_routes import _start_query_task
        async with app.test_request_context("/api/v1/sessions/resume", method="POST"):
            started = await _start_query_task(res["session_id"], user_uuid, payload)
        resp, status = started if isinstance(started, tuple) else (started, 200)
        data = await resp.get_json()
        if status != 202 or not (data or {}).get("task_id"):
            return None, f"the child could not be resumed: {(data or {}).get('error') or status}"
        return data["task_id"], None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"needs-input: resuming the child failed: {e}")
        return None, f"the child could not be resumed: {e}"


async def with_original_query(resumption: Dict[str, Any]) -> Dict[str, Any]:
    """An async face completes the block with the interrupted request's own words."""
    from trusted_data_agent.agent import input_requests as ir
    from trusted_data_agent.core import session_manager
    rec = resumption["request"]
    try:
        data = await session_manager.get_session(rec.get("user_uuid"), rec.get("session_id"))
        q = _query_of(data, rec.get("turn"))
    except Exception:  # noqa: BLE001
        q = None
    if q:
        ia = resumption["input_answer"]
        block, display = ir.reentry_block(rec, ia["decision"], ia.get("values"), q)
        resumption["user_input"], resumption["display_message"] = block, display
    return resumption
