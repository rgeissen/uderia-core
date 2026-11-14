"""S10 — Reattach: the per-turn EVENT LOG behind the UI stream (spec:
docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S10).

The turn already executes as a task of its own — a closed tab never cancels it — but its
events went into a queue only the original response read, so a lost connection lost the
VIEW of the turn. This module keeps every SSE frame a task emits in a bounded, cursor-numbered
log: the original stream stamps each frame with its cursor (`id: <seq>`, the SSE standard's own
resume field), a reconnecting client replays from the cursor it last saw and tails the log
until the turn is done, and a session that was closed mid-turn can find its running task and
backfill from 0. ONE registry, process-global, bounded in tasks, events per task and age.

Off (the master switch) ⇒ nothing is logged, no cursor is stamped, the endpoints refuse —
byte-identical. Never raises into a turn: every entry point is fail-open.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}
MAX_TASKS = 200
MAX_EVENTS = 4000          # frames kept per task; older ones drop (a replay from below the floor is a GAP, said so)
DONE_TTL_SECONDS = 900     # a finished log is kept 15 minutes for a late reattach
TAIL_POLL_SECONDS = 0.25


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "STEER_REATTACH", False))
    except Exception:  # noqa: BLE001
        return False


def _prune_locked(now: float) -> None:
    dead = [t for t, rec in _TASKS.items() if rec["done"] and now - rec["done_at"] > DONE_TTL_SECONDS]
    for t in dead:
        _TASKS.pop(t, None)
    if len(_TASKS) > MAX_TASKS:
        # finished logs first (oldest first), running ones only when nothing finished is left
        for t in sorted(_TASKS, key=lambda k: (not _TASKS[k]["done"], _TASKS[k]["started"]))[: len(_TASKS) - MAX_TASKS]:
            _TASKS.pop(t, None)


def open(task_id: str, session_id: Optional[str], user_uuid: Optional[str]) -> None:  # noqa: A001
    """Start a task's log (the stream's first act). No-op when off."""
    if not enabled() or not task_id:
        return
    now = time.time()
    with _LOCK:
        _prune_locked(now)
        _TASKS[task_id] = {"task_id": task_id, "session_id": session_id, "user_uuid": user_uuid, "started": now,
                           "seq": 0, "floor": 1, "events": [], "done": False, "done_at": None, "wake": None}


def append(task_id: str, frame: str) -> Optional[int]:
    """Record one SSE frame; returns its cursor (1-based) or None when not logged."""
    if not enabled() or not task_id:
        return None
    with _LOCK:
        rec = _TASKS.get(task_id)
        if rec is None or rec["done"]:
            return None
        rec["seq"] += 1
        rec["events"].append((rec["seq"], frame))
        if len(rec["events"]) > MAX_EVENTS:
            drop = len(rec["events"]) - MAX_EVENTS
            del rec["events"][:drop]
            rec["floor"] = rec["events"][0][0]
        wake = rec["wake"]
    _wake(wake)
    return rec["seq"]


def mark_done(task_id: str) -> None:
    if not task_id:
        return
    with _LOCK:
        rec = _TASKS.get(task_id)
        if rec is None:
            return
        rec["done"] = True
        rec["done_at"] = time.time()
        wake = rec["wake"]
    _wake(wake)


def _wake(ev) -> None:
    try:
        if ev is not None:
            ev.set()
    except Exception:  # noqa: BLE001
        pass


def stamp(frame: str, seq: Optional[int]) -> str:
    """The frame with its cursor as the SSE `id:` field (the standard's own resume key)."""
    if seq is None:
        return frame
    return f"id: {seq}\n{frame}"


def get(task_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        rec = _TASKS.get(task_id or "")
        if rec is None:
            return None
        return {"task_id": rec["task_id"], "session_id": rec["session_id"], "user_uuid": rec["user_uuid"],
                "started": rec["started"], "last_seq": rec["seq"], "floor": rec["floor"], "done": rec["done"]}


def events_since(task_id: str, since: int) -> Tuple[List[Tuple[int, str]], bool, Optional[int]]:
    """``(events after `since`, done, gap)`` — `gap` is the first retained cursor when the
    request asked for frames that have already dropped (the caller says so, never invents)."""
    with _LOCK:
        rec = _TASKS.get(task_id or "")
        if rec is None:
            return [], True, None
        gap = rec["floor"] if (since + 1 < rec["floor"] and rec["seq"] >= rec["floor"]) else None
        return [(s, f) for s, f in rec["events"] if s > since], rec["done"], gap


def active_for_session(session_id: str, user_uuid: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The running task of a session (the newest not-done log), or None."""
    with _LOCK:
        cands = [r for r in _TASKS.values() if r["session_id"] == session_id and not r["done"]
                 and (user_uuid is None or r["user_uuid"] == user_uuid)]
    if not cands:
        return None
    rec = max(cands, key=lambda r: r["started"])
    return {"task_id": rec["task_id"], "last_seq": rec["seq"], "started": rec["started"], "floor": rec["floor"]}


async def tail(task_id: str, since: int, keepalive: float = 15.0):
    """Async generator: every retained frame after `since` (cursor-stamped), then new frames as
    they land, a `: ping` comment on silence, until the task is done. A gap below the retained
    floor yields ONE `reattach_gap` event first (the client re-reads the session instead)."""
    import json
    with _LOCK:
        rec = _TASKS.get(task_id or "")
        if rec is not None and rec["wake"] is None:
            rec["wake"] = asyncio.Event()
    evs, done, gap = events_since(task_id, since)
    if gap is not None:
        yield stamp(f"data: {json.dumps({'reattach_gap': True, 'floor': gap, 'since': since})}\nevent: reattach_gap\n\n", None)
    cursor = since
    for s, f in evs:
        yield stamp(f, s)
        cursor = s
    while not done:
        with _LOCK:
            rec = _TASKS.get(task_id or "")
            wake = rec["wake"] if rec else None
        if wake is None:
            break
        try:
            await asyncio.wait_for(wake.wait(), timeout=keepalive)
            wake.clear()
        except asyncio.TimeoutError:
            yield ": ping\n\n"
        evs, done, _ = events_since(task_id, cursor)
        for s, f in evs:
            yield stamp(f, s)
            cursor = s
    with _LOCK:
        rec = _TASKS.get(task_id or "")
        ended = bool(rec["done"]) if rec is not None else True
    yield stamp(f"data: {json.dumps({'reattached': True, 'last_seq': cursor, 'done': ended})}\nevent: reattach_end\n\n", None)


def _reset_for_tests() -> None:
    with _LOCK:
        _TASKS.clear()
