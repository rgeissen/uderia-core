"""Agents asking other agents — TDA_Platform's ``ask`` action.

An agent may put a question to another of its user's agents and use the answer. Four gates,
all of which must open:

  1. **The deployment** — an administrator turns asking on (Administration → Components →
     Platform → Agent asking) and sets its limits: how deep a chain of asks may go, how many
     asks one turn may make, how long one ask may take. Off by default.
  2. **The asking agent's role** — it holds a role that asks (the Problem Solver,
     ``asks: true`` in the role file); asking is where that role's judgement is taught.
  3. **The asking agent's list** — its owner lists the agents it may ask
     (``authorityConfig.platformSurface.ask``: profile ids). Authority, like the write mode:
     portable, unsigned, downgrade-guarded — adding an agent to the list is a widening.
  4. **The asked agent** — it must be activated for REST use (a person's act, all its tests
     passing): the question goes through the same session and query routes as any REST
     client, so the activation firewall, consumption limits and the asked agent's own
     authority, posture and gates all apply. Nothing about the asked agent is borrowed.

The asked agent answers in a conversation of its own, visible to the user, that records who
asked (``platform_asked_by``: the asking conversation, agent and depth). The depth limit is
read from that record, so an asked agent cannot start a chain longer than the deployment allows.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("quart.app")

# component_settings keys → (default, minimum, maximum)
_KEYS = {
    "enabled": ("platform_ask_enabled", False, None, None),
    "max_depth": ("platform_ask_max_depth", 1, 1, 3),
    "max_per_turn": ("platform_ask_max_per_turn", 3, 1, 10),
    "timeout_s": ("platform_ask_timeout_s", 180, 30, 900),
}
ANSWER_CHARS = 6000
# (asking session, turn) → asks made — bounded; a turn never outlives a few hundred entries
_ASKS: Dict[Tuple[str, Any], int] = {}
_ASKS_MAX = 2000


def _clamp(name: str, value: Any) -> Any:
    _key, default, lo, hi = _KEYS[name]
    if name == "enabled":
        return value is True or str(value).lower() in ("true", "1")
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def settings() -> Dict[str, Any]:
    """The deployment's asking settings — off, with the safe limits, when never set."""
    try:
        from trusted_data_agent.components.settings import get_component_settings
        stored = get_component_settings() or {}
    except Exception:  # noqa: BLE001 — unreadable settings read as off
        stored = {}
    out = {}
    for name, (key, default, _lo, _hi) in _KEYS.items():
        out[name] = _clamp(name, stored[key]) if key in stored else default
    return out


def save_settings(values: Dict[str, Any], admin_uuid: str) -> Dict[str, Any]:
    """Store the named settings (clamped to their limits) and return them all."""
    from trusted_data_agent.components.settings import save_component_settings
    upd = {_KEYS[n][0]: _clamp(n, v) for n, v in (values or {}).items() if n in _KEYS}
    if upd:
        save_component_settings(upd, admin_uuid)
    return settings()


def askable(profile: Optional[Dict[str, Any]]) -> list:
    """The agents ``profile`` may ask — ``authorityConfig.platformSurface.ask`` (profile ids)."""
    ps = (((profile or {}).get("authorityConfig") or {}).get("platformSurface") or {})
    v = ps.get("ask") if isinstance(ps, dict) else None
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


async def _depth(user_uuid: str, session_id: Optional[str]) -> int:
    """How deep in a chain of asks the asking conversation is (0: a person started it)."""
    if not session_id:
        return 0
    try:
        from trusted_data_agent.core import session_manager
        s = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id) or {}
        return int((s.get("platform_asked_by") or {}).get("depth") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _count(session_id: Optional[str], turn_id: Any) -> int:
    return _ASKS.get((str(session_id), turn_id), 0)


def _bump(session_id: Optional[str], turn_id: Any) -> None:
    if len(_ASKS) >= _ASKS_MAX:
        _ASKS.clear()
    k = (str(session_id), turn_id)
    _ASKS[k] = _ASKS.get(k, 0) + 1


async def _route(app, method: str, path: str, user_uuid: str, body: Optional[dict] = None):
    """Run one REST route in-process as the user, with a token good for this request only."""
    from trusted_data_agent.auth.security import create_operation_token
    token = create_operation_token(user_uuid, method=method, path=path, op="platform.ask")
    headers = {"Authorization": f"Bearer {token}", "X-Uderia-Operation": "platform.ask"}
    kwargs: Dict[str, Any] = {"method": method, "headers": headers}
    if body is not None:
        kwargs["json"] = body
    resp = await app.test_client().open(path, **kwargs)
    try:
        data = await resp.get_json(force=True)
    except Exception:  # noqa: BLE001
        data = None
    return resp.status_code, (data if isinstance(data, dict) else {})


def _refusal(data: Dict[str, Any], status: int) -> str:
    msg = data.get("message") or data.get("error") or f"HTTP {status}"
    if data.get("code") == "profile_not_activated" or "not activated" in str(msg).lower():
        return ("it is not activated for REST use — its owner activates it (all its tests passing) "
                "before other agents can ask it")
    return str(msg)


async def ask(asker: Dict[str, Any], target: Any, question: str, user_uuid: str, *,
              session_id: Optional[str] = None, turn_id: Any = None, app=None,
              route=None, poll_s: float = 1.0) -> Dict[str, Any]:
    """Put ``question`` to the agent ``target`` (@TAG or id) on behalf of ``asker`` and wait
    for its answer. Every refusal is a sentence the asking agent can pass on."""
    route = route or _route
    cfg = settings()
    if not cfg["enabled"]:
        return {"ok": False, "error": ("Asking other agents is turned off on this deployment — an administrator "
                                       "turns it on (Administration → Components → Platform → Agent asking). "
                                       "Do not retry; tell the user.")}
    from trusted_data_agent.core import platform_roles
    if not platform_roles.may_ask(platform_roles.roles_of(asker)):
        return {"ok": False, "error": ("asking other agents is the Problem Solver role's — this agent does not hold it. "
                                       "Its owner gives it that role (Agent profile → Capabilities → Platform). "
                                       "Do not retry; tell the user.")}
    q = str(question or "").strip()
    if not q:
        return {"ok": False, "error": "ask needs 'question': what to ask the other agent, in full sentences"}
    from trusted_data_agent.core.platform_operations import resolve_agent
    try:
        tid, err = resolve_agent(target, user_uuid)
    except Exception as exc:  # noqa: BLE001 — PROP-011: two agents wear the tag (relay is phase 3)
        from trusted_data_agent.agent import input_requests as _ir
        if not isinstance(exc, _ir.Ambiguous):
            raise
        tid, err = None, (f"{len(exc.candidates)} of your agents are tagged '{exc.value}' — name it by id: "
                          + ", ".join(str(c.get("id")) for c in exc.candidates[:5]))
    if err:
        return {"ok": False, "error": err}
    if tid == asker.get("id"):
        return {"ok": False, "error": "an agent does not ask itself — answer the question yourself"}
    from trusted_data_agent.core.config_manager import get_config_manager
    tprof = get_config_manager().get_profile(tid, user_uuid) or {}
    ttag = "@" + str(tprof.get("tag") or tid)
    allowed = askable(asker)
    if tid not in allowed:
        names = []
        for pid in allowed:
            p = get_config_manager().get_profile(pid, user_uuid) or {}
            if p.get("tag"):
                names.append("@" + p["tag"])
        return {"ok": False, "agent": ttag,
                "error": (f"this agent may not ask {ttag} — its owner lists the agents it may ask "
                          f"(Agent profile → Capabilities → Platform)"
                          + (f"; it may ask: {', '.join(names)}" if names else "; it may ask none") + ".")}
    depth = await _depth(user_uuid, session_id)
    if depth >= cfg["max_depth"]:
        return {"ok": False, "agent": ttag,
                "error": (f"this conversation was itself asked by another agent, and this deployment allows "
                          f"chains of {cfg['max_depth']} ask(s) — answer from what you have.")}
    if _count(session_id, turn_id) >= cfg["max_per_turn"]:
        return {"ok": False, "agent": ttag,
                "error": f"this turn has asked {cfg['max_per_turn']} time(s), the deployment's limit — answer from what you have."}
    if app is None:
        from trusted_data_agent.core.platform_operations import _app
        app = _app()
    if app is None and route is _route:
        return {"ok": False, "error": "the platform is not available in this process — nothing was asked"}

    _bump(session_id, turn_id)
    from trusted_data_agent.agent import input_requests as _ir
    # PROP-011 phase 3 — the answer travels down: on the parent's re-entry turn, the ask that
    # relayed a question is RE-RUN as the child's continuation (the person's answer, consumed
    # onto the child's record by the accept, starts the child's own re-entry turn), never as a
    # fresh question to the child.
    cont = _ir.continuation_for(f"ask:{ttag}:")
    if cont is not None and cont.get("child_session_id"):
        child = cont["child_session_id"]
        if cont.get("decision") != "accept":
            return {"ok": False, "agent": ttag, "session_id": child,
                    "error": (f"the user {'declined' if cont.get('decision') == 'decline' else 'did not answer'} "
                              f"{ttag}'s question — answer from what you have, and say so.")}
        from trusted_data_agent.core import input_resume as _resume
        task, why = await _resume.resume_child(app, user_uuid, cont["child_request_id"], decision="accept",
                                               values=cont.get("values"), profile_id=tid)
        if not task:
            return {"ok": False, "agent": ttag, "session_id": child, "error": f"{ttag} could not continue: {why}."}
        logger.info(f"[platform-ask] @{asker.get('tag')} continued {ttag} with the user's answer (task {task}, session {child})")
    else:
        status, data = await route(app, "POST", "/api/v1/sessions", user_uuid, {"profile_id": tid})
        child = data.get("session_id")
        if status >= 300 or not child:
            return {"ok": False, "agent": ttag, "error": f"{ttag} cannot be asked: {_refusal(data, status)}."}
        try:
            from trusted_data_agent.core import session_manager
            await session_manager.set_platform_asked_by(user_uuid, child, {
                "session_id": session_id, "profile_id": asker.get("id"), "tag": asker.get("tag"),
                "turn": turn_id, "depth": depth + 1})
        except Exception as e:  # noqa: BLE001 — the record is lineage; a failure must not leave depth unbounded
            logger.warning(f"[platform-ask] could not record who asked in {child}: {e}")
            return {"ok": False, "agent": ttag, "error": "the asked conversation could not record who asked — nothing was asked"}
        # `source: ask` is the child's ORIGIN (unattended: its `never` policy does not close a
        # question; it parks and expires on the unattended clock)
        status, data = await route(app, "POST", f"/api/v1/sessions/{child}/query", user_uuid,
                                   {"prompt": q, "profile_id": tid, "source": "ask"})
        task = data.get("task_id")
        if status >= 300 or not task:
            return {"ok": False, "agent": ttag, "session_id": child,
                    "error": f"{ttag} cannot be asked: {_refusal(data, status)}."}
        logger.info(f"[platform-ask] @{asker.get('tag')} asked {ttag} (task {task}, session {child}, depth {depth + 1})")
    deadline = time.monotonic() + cfg["timeout_s"]
    while True:
        status, data = await route(app, "GET", f"/api/v1/tasks/{task}", user_uuid, None)
        st = str(data.get("status") or "")
        from trusted_data_agent.core import turn_states as _ts
        if _ts.is_success(st) or _ts.is_interrupted(st):
            res = data.get("result") or {}
            # PROP-011 phase 3 — the question travels up: the child stopped to ask (its result
            # carries the question whatever status this poller reads); it becomes THIS turn's
            # question, the chain on the record. The asker relays; it never answers for the user.
            child_q = res.get("input_request") if isinstance(res, dict) else None
            if isinstance(child_q, dict) and child_q.get("id"):
                rec, note = _ir.relay_child_question(child_q, agent_tag=ttag, source=f"ask:{ttag}:{child}")
                out = {"ok": False, "agent": ttag, "session_id": child, "status": "input_required", "message": note}
                if rec is not None:
                    from trusted_data_agent.core import input_request_store as _store
                    out["request_id"] = rec["id"]
                    out["question"] = _store.public_view(rec)
                return out
            text = res if isinstance(res, str) else (res.get("final_answer_text") or res.get("final_response")
                                                     or res.get("final_answer") or "")
            text = str(text or "").strip()
            out = {"ok": True, "agent": ttag, "session_id": child,
                   "answer": text[:ANSWER_CHARS] or f"{ttag} finished without an answer."}
            if len(text) > ANSWER_CHARS:
                out["note"] = f"shortened from {len(text)} characters — the full answer is in {ttag}'s conversation"
            out["instruction"] = (f"This is {ttag}'s answer, from its own conversation. Use it, and say that {ttag} "
                                  f"answered; do not present it as your own reading.")
            return out
        if _ts.is_terminal(st):
            return {"ok": False, "agent": ttag, "session_id": child,
                    "error": f"{ttag} could not answer: {data.get('error') or st}."}
        if time.monotonic() >= deadline:
            return {"ok": False, "agent": ttag, "session_id": child, "status": "running",
                    "error": (f"{ttag} has not answered within {cfg['timeout_s']} s; it keeps working and its answer "
                              f"will be in its own conversation. Tell the user; do not ask again.")}
        await asyncio.sleep(poll_s)


def find_hint(asker: Optional[Dict[str, Any]], roles, user_uuid: str) -> Optional[Dict[str, Any]]:
    """For a ``find`` by an agent that may ask: the agents it may ask, each with what it is for —
    so the model meets them at the moment it looks for how to answer, not only in its
    instructions (live: a Problem Solver asked about a concept answered it from memory, wrongly,
    without asking the documentation agent on its list). None when it may not ask."""
    from trusted_data_agent.core import platform_roles
    if not asker or not platform_roles.may_ask(roles) or not settings()["enabled"]:
        return None
    ids = askable(asker)
    if not ids:
        return None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        cm = get_config_manager()
        agents = []
        for pid in ids:
            p = cm.get_profile(pid, user_uuid) or {}
            if p.get("tag") and pid != asker.get("id"):
                agents.append({"agent": "@" + p["tag"], "name": p.get("name") or "",
                               "for": str(p.get("description") or "")[:200]})
    except Exception:  # noqa: BLE001 — a hint is a courtesy
        return None
    if not agents:
        return None
    return {"agents": agents,
            "instruction": ("For any part of the question that is not about the platform itself — a concept, a document, "
                            "data — ask the agent above whose field it is (action 'ask', agent, question) instead of "
                            "answering from memory, and say which agent answered.")}
