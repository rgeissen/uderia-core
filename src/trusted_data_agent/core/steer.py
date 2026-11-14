"""S10 — Steer: talk to the agent while it works (spec:
docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S10).

A steer is a short operator note sent DURING a turn. It is queued per session and delivered at
the agent's next step boundary — the next tool result, on every lane that runs tools — as a
turn-scoped operator note the model reads with that result; a turn with no further boundary
(a single-call answer, or a steer that arrives after the last tool ran) carries it into the
START of the next turn, said so. Every steer is a RECORDED step: it lands in the trace live
(the per-user notification channel), on the persisted turn (`steers`), and the reader sees it
move from "pinned under the running answer" into the trace when it is considered.

Why a note at the tool boundary, on every provider: the Messages API has no mid-conversation
system role — a system prompt is a top-level parameter — so "native" mid-turn steering does
not exist as an API primitive on Anthropic either; a queued note delivered with the next tool
result is the mechanism Claude Code itself uses for mid-turn messages, and it is the same on
every lane here (one mechanism, no per-provider fork). Off (the master switch) ⇒ nothing can
be queued, nothing is appended, nothing is stamped — byte-identical. Never raises into a turn.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}
MAX_PENDING = 5
MAX_TEXT = 600
MAX_SESSIONS = 500
NOTE_LEAD = ("[OPERATOR STEER — the user changed the instructions while you were working. From here on "
             "follow this; where it conflicts with the original request, this wins:]")
NEXT_TURN_LEAD = ("[OPERATOR STEER — sent during your previous answer, after its last step. Apply it to this "
                  "turn; where it conflicts with the request above, this wins:]")


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "STEER_REATTACH", False))
    except Exception:  # noqa: BLE001
        return False


def _reg(session_id: str) -> Dict[str, Any]:
    rec = _SESSIONS.get(session_id)
    if rec is None:
        if len(_SESSIONS) >= MAX_SESSIONS:
            for k in sorted(_SESSIONS, key=lambda k: _SESSIONS[k]["touched"])[:50]:
                _SESSIONS.pop(k, None)
        rec = _SESSIONS[session_id] = {"pending": [], "consumed": [], "touched": time.time()}
    rec["touched"] = time.time()
    return rec


def queue(session_id: str, text: str, user_uuid: Optional[str] = None) -> Dict[str, Any]:
    """Queue a steer for the session's running turn. Raises ValueError on an empty text, a
    text over the bound, or a full queue (the caller answers 400/409). Never when off."""
    if not enabled():
        raise ValueError("steering is off")
    t = " ".join(str(text or "").split())
    if not t:
        raise ValueError("a steer needs some text")
    if len(t) > MAX_TEXT:
        raise ValueError(f"a steer is at most {MAX_TEXT} characters")
    with _LOCK:
        rec = _reg(session_id)
        if len(rec["pending"]) >= MAX_PENDING:
            raise ValueError(f"{MAX_PENDING} steers are already waiting — let the agent reach its next step")
        st = {"id": uuid.uuid4().hex[:12], "text": t, "ts": time.time(), "user_uuid": user_uuid,
              "consumed": False, "at": None, "consumed_ts": None}
        rec["pending"].append(st)
        return dict(st)


def pending(session_id: str) -> List[Dict[str, Any]]:
    with _LOCK:
        rec = _SESSIONS.get(session_id or "")
        return [dict(s) for s in rec["pending"]] if rec else []


def begin_turn(session_id: Optional[str]) -> None:
    """A new turn: the previous turn's consumed steers leave the record it will stamp (pending
    ones stay — they are delivered at the new turn's start)."""
    if not session_id:
        return
    with _LOCK:
        rec = _SESSIONS.get(session_id)
        if rec:
            rec["consumed"] = []


def consume(session_id: Optional[str], at: str) -> List[Dict[str, Any]]:
    """Every pending steer, marked considered `at` (a tool name, or `turn_start`). Empty when
    nothing waits or the switch is off."""
    if not enabled() or not session_id:
        return []
    with _LOCK:
        rec = _SESSIONS.get(session_id)
        if not rec or not rec["pending"]:
            return []
        out = []
        for st in rec["pending"]:
            st["consumed"] = True; st["at"] = at; st["consumed_ts"] = time.time()
            rec["consumed"].append(st)
            out.append(dict(st))
        rec["pending"] = []
        return out


def note(steers: List[Dict[str, Any]], lead: str = NOTE_LEAD) -> str:
    """The text the model reads with the next result: the lead line, one steer per line."""
    lines = [s["text"] for s in (steers or []) if s.get("text")]
    if not lines:
        return ""
    return lead + "\n" + "\n".join(f"- {t}" for t in lines)


def stamp_for_turn(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The persisted record: the steers considered this turn (where), plus the ones still
    waiting at the turn's end (they carry into the next turn). None when nothing happened."""
    if not session_id:
        return None
    with _LOCK:
        rec = _SESSIONS.get(session_id)
        if not rec or (not rec["consumed"] and not rec["pending"]):
            return None
        rows = [{"id": s["id"], "text": s["text"], "ts": s["ts"], "consumed": True, "at": s["at"], "consumed_ts": s["consumed_ts"]}
                for s in rec["consumed"]]
        rows += [{"id": s["id"], "text": s["text"], "ts": s["ts"], "consumed": False, "at": None, "consumed_ts": None}
                 for s in rec["pending"]]
        return {"steers": rows, "considered": len(rec["consumed"]), "carried": len(rec["pending"])}


def announce(user_uuid: Optional[str], session_id: Optional[str], steers: List[Dict[str, Any]], at: str) -> None:
    """The consumption as a live step over the per-user notification channel (the trust-event
    idiom: loop-guarded, put_nowait, never raises)."""
    if not steers or not user_uuid:
        return
    try:
        import asyncio
        from trusted_data_agent.core.config import APP_STATE
        asyncio.get_running_loop()
        payload = {"session_id": session_id, "at": at, "steers": [{"id": s["id"], "text": s["text"]} for s in steers],
                   "step": "Steer Considered", "type": "steer_consumed",
                   "details": {"at": at, "steers": [{"id": s["id"], "text": s["text"]} for s in steers],
                               "summary": (f"{len(steers)} steer{'s' if len(steers) != 1 else ''} considered "
                                           + ("at the start of this turn" if at == "turn_start" else f"with the result of {at}"))}}
        for q in list(APP_STATE.get("notification_queues", {}).get(user_uuid, set())):
            try:
                q.put_nowait({"type": "steer_consumed", "payload": payload})
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def consume_at_boundary(session_id: Optional[str], tool_name: str, user_uuid: Optional[str] = None) -> Optional[str]:
    """The ONE call both tool boundaries make after a result lands: pending steers are
    consumed with this tool's result, announced live, and returned as the note to append.
    None when nothing waits (the hot path)."""
    steers = consume(session_id, tool_name)
    if not steers:
        return None
    announce(user_uuid, session_id, steers, tool_name)
    return note(steers)


def consume_at_turn_start(session_id: Optional[str], user_uuid: Optional[str] = None) -> Optional[str]:
    """Steers left waiting when the previous turn ended (no boundary reached them) are
    delivered at the new turn's start; None when none waited."""
    steers = consume(session_id, "turn_start")
    if not steers:
        return None
    announce(user_uuid, session_id, steers, "turn_start")
    return note(steers, NEXT_TURN_LEAD)


def _reset_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()
