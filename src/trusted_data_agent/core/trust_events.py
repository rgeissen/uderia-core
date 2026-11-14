"""Trust events — the Assurance Inbox substrate.

Trust incidents were previously dashboard archaeology (a weekly look at the
roster) or, worse, log lines: the circuit breaker, the memory firewall, and the
retrieval firewall acted without leaving a *worked item*. This module turns
trust events into a triageable queue:

- **Stored events** — out-of-band incidents with no persisted turn stamp
  (breaker trips, memory quarantines, retrieval defuses). Small self-applying
  ``trust_events`` table (sqlite_util pattern), recorded fail-open by the emit
  sites — a store failure must never affect the action itself.
- **Derived events** — incidents already persisted ON the turn (grounding
  blocks, guardrail redact/block, honesty rewrites, inbound refusals) are
  derived at READ time from the turn stamps via the pure
  :func:`derive_turn_events`, so the executor/engines need no new emit seams
  and the inbox can never disagree with the trace.
- **Session sentinel** — the multi-turn attack detector: per-turn guards see
  one request at a time, so a "crescendo" of probing attempts across a session
  is invisible to them. :func:`derive_session_sentinel` (pure) flags a session
  whose inbound-guardrail findings cross a threshold.

NOT part of the removable Ontology Suite (`core/trust/`) — inbox items cover
guardrails/breaker events that exist without the trust membrane.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

# One statement per entry — sqlite3's execute() runs a single statement.
_DDL_STATEMENTS = (
    """
CREATE TABLE IF NOT EXISTS trust_events (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'warning',
    profile_tag  TEXT,
    session_id   TEXT,
    turn_id      TEXT,
    summary      TEXT,
    payload_json TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    acked_at     TEXT
)
""",
    # PROP-002 — acknowledgments of DERIVED observations (the sealed trace is
    # never rewritten; the ack is a stored disposition ABOUT it). turn_id '' =
    # session-level (the sentinel).
    """
CREATE TABLE IF NOT EXISTS derived_acks (
    user_id      TEXT NOT NULL,
    session_id   TEXT NOT NULL DEFAULT '',
    turn_id      TEXT NOT NULL DEFAULT '',
    event_type   TEXT NOT NULL,
    acked_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, session_id, turn_id, event_type)
)
""",
)

SEVERITIES = ("info", "warning", "danger")

# ── PROP-002: ONE severity source ─────────────────────────────────────────────
# The severity of a derived event and the state the assurance-points registry
# reads for the SAME turn stamp must agree (an honesty block once read `warning`
# here and `danger` on the dial). The canonical map lives BESIDE the registry
# (`agent.assurance_points.DERIVED_EVENT_SEVERITY`); this module imports it
# lazily and falls back to an equal literal so the derivation stays importable
# in contexts where the agent package is absent. test_trust_events asserts the
# two are identical.
_FALLBACK_SEVERITY = {
    "grounding_block": "danger",
    "grounding_annotate": "warning",
    "guardrail_outbound_block": "danger",
    "guardrail_outbound_redact": "warning",
    "guardrail_outbound_annotate": "warning",
    "guardrail_inbound_block": "danger",
    "guardrail_inbound_redact": "warning",
    "guardrail_inbound_annotate": "warning",
    "honesty_rewrite": "danger",
    "honesty_flag": "warning",
    "session_sentinel": "danger",
    "judge_condemnation": "danger",
    "assurance_binding_changed": "warning",
    "assurance_module_drift": "danger",
    "surface_breach": "danger",
    # the OBDA policy under OBSERVE saw a call enforce would refuse — worth a look, never danger
    "policy_observed": "warning",
}


def derived_severity(event_type: str) -> str:
    """The severity for a derived event type — from the ONE map."""
    try:
        from trusted_data_agent.agent.assurance_points import DERIVED_EVENT_SEVERITY
        return DERIVED_EVENT_SEVERITY.get(event_type) or _FALLBACK_SEVERITY.get(event_type, "warning")
    except Exception:
        return _FALLBACK_SEVERITY.get(event_type, "warning")


# Which review-queue SOURCE a derived event type belongs to — the disposition
# join must not bleed across gates on one turn (a decided grounding item once
# stamped the guardrail observation on the same turn as resolved).
_EVENT_SOURCES = {
    "grounding_block": ("grounding",),
    "grounding_annotate": ("grounding",),
    "guardrail_outbound_block": ("guardrail_outbound",),
    "guardrail_outbound_redact": ("guardrail_outbound",),
    "guardrail_outbound_annotate": ("guardrail_outbound",),
    "guardrail_inbound_block": ("guardrail_inbound",),
    "guardrail_inbound_redact": ("guardrail_inbound",),
    "guardrail_inbound_annotate": ("guardrail_inbound",),
    "honesty_rewrite": ("honesty", "honesty_rag"),
    "honesty_flag": ("honesty", "honesty_rag"),
}

# Sentinel: this many inbound injection-flagged requests in ONE session reads as
# a probing campaign, not an accident.
SENTINEL_MIN_FINDINGS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None):
    conn = sqlite_util.connect(_db_path(db_path))
    for stmt in _DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
    return conn


def broadcast_notice(user_uuid: str, event_type: str, summary: str,
                     session_id: Optional[str] = None,
                     profile_tag: Optional[str] = None,
                     severity: Optional[str] = None,
                     notice_type: str = "trust_event_notice",
                     extra: Optional[dict] = None) -> None:
    """Push a live notice over the per-user notification channel so a trust
    action is SEEN the moment it happens (banner + in-place Assurance Inbox
    refresh) — the same live-emission root-cause fix the HITL review flow got.

    ``severity`` (PROP-002) rides the payload so the banner TONE can honour it —
    without it every live trust banner was hardcoded amber, so an `info` receipt
    and a `danger` red-team failure raised the same alarm. ``notice_type`` +
    ``extra`` let event families with their own client rendering (the trust
    breaker) travel this ONE emission path instead of hand-rolling a second
    queue push.

    Thread-aware: emit sites may run in sync code off the event loop (a route's
    ``asyncio.to_thread`` — the file service's rename/delete/restore/keep, MemoryStore). The
    queues belong to the serving loop and an ``asyncio.Queue`` is not thread-safe, so off the
    loop the put is handed to it with ``call_soon_threadsafe`` (``APP_STATE["serving_loop"]``,
    recorded at startup). Before this, a notice raised in a worker thread was skipped silently
    and a REST rename never reached an open page. No serving loop (a script, a test) ⇒ skip
    (the stored inbox row remains the durable record). Never raises."""
    try:
        import asyncio
        from trusted_data_agent.core.config import APP_STATE
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        serving = None
        if not on_loop:
            serving = APP_STATE.get("serving_loop")
            if serving is None or serving.is_closed():
                return
        payload = {"event_type": event_type, "summary": summary,
                   "session_id": session_id, "profile_tag": profile_tag,
                   "severity": severity if severity in SEVERITIES else None}
        if extra:
            payload.update(extra)
        message = {"type": notice_type, "payload": payload}
        for q in list(APP_STATE.get("notification_queues", {}).get(user_uuid, set())):
            # TC-70: `put_nowait`, not `loop.create_task(q.put(...))` — the created task was
            # held by NOTHING, so CPython could garbage-collect it before it ran (the
            # documented asyncio caveat this codebase guards against elsewhere with
            # strong-ref sets), silently dropping the live notice. The queues are
            # unbounded, so the synchronous put never blocks and needs no task at all.
            try:
                if on_loop:
                    q.put_nowait(message)
                else:
                    serving.call_soon_threadsafe(q.put_nowait, message)
            except Exception:
                continue
    except Exception:
        pass  # notification is best-effort; the inbox row is the record


# ── stored events ─────────────────────────────────────────────────────────────
def record_event(user_uuid: str, event_type: str, summary: str, *,
                 severity: str = "warning", profile_tag: Optional[str] = None,
                 session_id: Optional[str] = None, turn_id=None,
                 payload: Optional[dict] = None,
                 db_path: Optional[str] = None) -> Optional[str]:
    """Record one trust event. Fail-open: returns the event id, or None on any
    failure (the triggering action must never depend on the inbox)."""
    try:
        eid = str(uuid.uuid4())
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO trust_events (id, created_at, user_id, event_type, severity,"
                " profile_tag, session_id, turn_id, summary, payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (eid, _now(), user_uuid, event_type,
                 severity if severity in SEVERITIES else "warning",
                 profile_tag, session_id,
                 str(turn_id) if turn_id is not None else None,
                 summary, json.dumps(payload or {})))
            conn.commit()
        finally:
            conn.close()
        return eid
    except Exception as e:
        logger.warning(f"trust-events: record failed (action unaffected): {e}")
        return None


def list_events(user_uuid: str, *, include_acked: bool = False, limit: int = 100,
                db_path: Optional[str] = None) -> list:
    try:
        conn = _connect(db_path)
        try:
            q = ("SELECT id, created_at, event_type, severity, profile_tag, session_id,"
                 " turn_id, summary, payload_json, acknowledged, acked_at "
                 "FROM trust_events WHERE user_id = ?")
            args = [user_uuid]
            if not include_acked:
                q += " AND acknowledged = 0"
            q += " ORDER BY created_at DESC LIMIT ?"
            args.append(int(limit))
            rows = conn.execute(q, args).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            try:
                payload = json.loads(r[8] or "{}")
            except Exception:
                payload = {}
            out.append({"id": r[0], "created_at": r[1], "event_type": r[2],
                        "severity": r[3], "profile_tag": r[4], "session_id": r[5],
                        "turn_id": r[6], "summary": r[7], "payload": payload,
                        "acknowledged": bool(r[9]), "acked_at": r[10],
                        "source": "stored"})
        return out
    except Exception as e:
        logger.warning(f"trust-events: list failed: {e}")
        return []


def ack_event(user_uuid: str, event_id: str, db_path: Optional[str] = None) -> bool:
    try:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "UPDATE trust_events SET acknowledged = 1, acked_at = ? "
                "WHERE id = ? AND user_id = ?", (_now(), event_id, user_uuid))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"trust-events: ack failed: {e}")
        return False


# I2/#57 — a RECEIPT is not open work.
#
# Some events record an act the OWNER just performed (releasing a withheld answer,
# acknowledging a corpus finding). They arrived unacknowledged like any other event,
# so acting on the inbox INCREASED its badge: the owner then had to acknowledge the
# records of their own actions, and the count could never reach zero by working it.
# A badge that cannot reach zero is a badge operators learn to ignore — the exact
# failure the trust grammar avoids elsewhere.
#
# These stay VISIBLE in the inbox as history (Rule 3.1 — nothing is hidden); they are
# simply not counted as work awaiting a decision.
RECEIPT_EVENT_TYPES = frozenset({
    "review_release",        # the owner released a withheld original
    "corpus_finding_ack",    # the owner acknowledged a corpus finding
    # PROP-002 (the receipt invariant: acting on the inbox can only LOWER its
    # count): quarantining/releasing a document is an owner ACT, yet it arrived
    # unacked, grew an Ack button, and RAISED the badge — the exact failure the
    # receipt concept exists to prevent.
    "corpus_quarantine",     # the owner quarantined / released a document
    # PROP-003: an admin rebound / unbound an assessment module. It records a decision
    # already taken, so it is history, not work — counting it would make the binding
    # studio raise the very badge it is used from.
    "assurance_binding_changed",
})


def is_receipt(event_type: Optional[str]) -> bool:
    """Does this event RECORD an act the owner performed, rather than ask for one?"""
    return (event_type or "") in RECEIPT_EVENT_TYPES


def unacked_count(user_uuid: str, db_path: Optional[str] = None,
                  reporting: Optional[dict] = None) -> int:
    """Events genuinely AWAITING the owner — receipts of their own acts excluded.

    ``reporting`` (PROP-002b) applies the per-station reporting levels: an item
    below its station's assigned level does not COUNT (presentation, never
    creation — it stays recorded and listable). Danger always counts (the
    construct's floor). Absent/unresolvable ⇒ unfiltered, today's behaviour.
    """
    try:
        conn = _connect(db_path)
        try:
            placeholders = ",".join("?" * len(RECEIPT_EVENT_TYPES))
            if not reporting:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trust_events WHERE user_id = ? AND acknowledged = 0"
                    f" AND event_type NOT IN ({placeholders})",
                    (user_uuid, *sorted(RECEIPT_EVENT_TYPES))).fetchone()
                return int(row[0]) if row else 0
            rows = conn.execute(
                "SELECT event_type, severity FROM trust_events WHERE user_id = ?"
                f" AND acknowledged = 0 AND event_type NOT IN ({placeholders})",
                (user_uuid, *sorted(RECEIPT_EVENT_TYPES))).fetchall()
        finally:
            conn.close()
        try:
            from trusted_data_agent.agent.assurance_points import (
                severity_surfaces, station_of_event)
            return sum(1 for et, sev in rows
                       if severity_surfaces(sev, station_of_event(et), reporting))
        except Exception:
            return len(rows)   # construct unavailable → unfiltered, never zero
    except Exception:
        return 0


# ── derived events (pure — read from persisted turn stamps) ───────────────────
def _turn_ref(turn: dict, session_id: Optional[str]) -> dict:
    return {"session_id": turn.get("session_id") or session_id,
            "turn_id": turn.get("turn"),
            "profile_tag": (turn.get("profile_tag") or "").lstrip("@") or None}


# I2 — a disposition must be visible wherever its incident is.
#
# Derived observations are recomputed from the sealed trace, which records what
# HAPPENED and is immutable — so an incident the owner has already decided kept
# reading "unresolved" forever, and REVIEWED's headline said the trace "carries an
# unresolved observation" minutes after the owner upheld it (#48).
#
# The disposition lives in `review_items`, keyed by the same (session, turn). This
# index is the join. It is passed IN rather than queried here so the derivation stays
# pure and CI-testable; the endpoint supplies it from the review queue.
def disposition_index(review_items: list) -> dict:
    """`{(session_id, str(turn_id), source): {"status", "released", "decided_at"}}`
    for every DECIDED review item. Pending items are excluded: they are still open
    work, and the incident that produced them is correctly still unresolved.

    Keyed by SOURCE too (PROP-002): a `(session, turn)` key let one gate's decided
    item stamp EVERY gate's derived observation on that turn as resolved — a turn
    with a grounding block and a guardrail redact where only the grounding item was
    decided showed both as ruled on."""
    idx: dict = {}
    for it in review_items or []:
        if not isinstance(it, dict):
            continue
        status = (it.get("status") or "").strip().lower()
        if status not in ("approved", "rejected"):
            continue
        sid, tid = it.get("session_id"), it.get("turn_id")
        if sid is None or tid is None:
            continue
        idx[(sid, str(tid), (it.get("source") or "").strip().lower())] = {
            "status": status,
            "released": bool(it.get("released_to_context")),
            "decided_at": it.get("decided_at"),
        }
    return idx


# What a decided disposition MEANS, in the owner's words. `rejected` is the queue's
# term for "the withholding stands" — never shown raw (Rule 3.0 bans wire strings).
_DISPOSITION_WORD = {
    "approved": "released by the owner",
    "rejected": "withholding upheld",
}


def _apply_disposition(item: dict, idx: dict) -> dict:
    """Stamp a derived observation with its recorded disposition, if one exists.

    A decided incident is no longer a call to attention: it becomes a record. So the
    severity drops to `info` — an item the owner has already ruled on must not keep
    demanding the attention it was given, or the inbox can never reach zero and the
    operator learns to ignore it.

    The lookup is per-SOURCE (see :func:`disposition_index`): only a decision on
    the SAME gate's parked item resolves this observation.
    """
    sid, tid = item.get("session_id"), str(item.get("turn_id"))
    d = None
    for src in _EVENT_SOURCES.get(item.get("event_type") or "", ()):
        d = idx.get((sid, tid, src))
        if d:
            break
    if not d:
        return item
    word = _DISPOSITION_WORD.get(d["status"], d["status"])
    if d["status"] == "approved" and d["released"]:
        word = "released into the conversation by the owner"
    item = dict(item)
    item["disposition"] = d["status"]
    item["disposition_label"] = word
    item["decided_at"] = d.get("decided_at")
    item["severity"] = "info"          # decided ⇒ a record, not a call to attention
    item["summary"] = f"{item.get('summary', '')} — {word}"
    return item


def derive_turn_events(turns: list, session_id: Optional[str] = None,
                       dispositions: Optional[dict] = None) -> list:
    """Derive inbox items from stamps ALREADY persisted on turns — grounding
    blocks/annotations, guardrail redact/block (both directions), honesty
    rewrites. Pure + CI-testable; the inbox can never disagree with the trace
    because these ARE the trace.

    ``dispositions`` (from :func:`disposition_index`) joins each observation to its
    recorded human decision, so a decided incident reads as resolved rather than
    accusing the owner of inaction they already took (#48).
    """
    idx = dispositions or {}
    out = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        ref = _turn_ref(turn, session_id)

        g = turn.get("grounding")
        if isinstance(g, dict) and g.get("decision") in ("block", "annotate"):
            et = "grounding_" + g["decision"]
            act = "withheld (confirmed drift)" if g["decision"] == "block" else "annotated (out-of-scope references)"
            out.append(_apply_disposition({**ref, "event_type": et,
                        "severity": derived_severity(et),
                        "summary": f"Grounding gate {act}",
                        "payload": {"out_of_scope_dbs": (g.get("out_of_scope_dbs") or [])[:5]},
                        "source": "derived"}, idx))

        # PROP-002: every ACTING outcome derives — `annotate` changed the answer's
        # presentation just as `redact` did; the dial marked it acted while the
        # inbox had never heard of it.
        for key, direction in (("guardrail", "outbound"), ("guardrail_inbound", "inbound")):
            gu = turn.get(key)
            if isinstance(gu, dict) and gu.get("action") in ("annotate", "redact", "block"):
                et = f"guardrail_{direction}_{gu['action']}"
                what = {"annotate": "findings annotated onto the answer",
                        "redact": "personal data masked",
                        "block": ("request refused (injection)" if direction == "inbound"
                                  else "answer withheld")}[gu["action"]]
                out.append(_apply_disposition({**ref, "event_type": et,
                            "severity": derived_severity(et),
                            "summary": f"{direction.capitalize()} guardrail: {what}",
                            "payload": {"categories": gu.get("categories") or []},
                            "source": "derived"}, idx))

        h = turn.get("honesty")
        if isinstance(h, dict) and h.get("decision") == "block":
            out.append(_apply_disposition({**ref, "event_type": "honesty_rewrite",
                        "severity": derived_severity("honesty_rewrite"),
                        "summary": "Honesty check rewrote a confident non-existence claim "
                                   "built on failed queries",
                        "payload": {"failed_query_count": h.get("failed_query_count")},
                        "source": "derived"}, idx))
        elif isinstance(h, dict) and h.get("decision"):
            # A flagged-but-not-rewritten honesty finding acted on the dial; it
            # derives too (PROP-002 — acted reaches the inbox).
            out.append(_apply_disposition({**ref, "event_type": "honesty_flag",
                        "severity": derived_severity("honesty_flag"),
                        "summary": "Honesty check flagged an assertion not supported "
                                   "by the turn's results",
                        "payload": {"reason": h.get("reason")},
                        "source": "derived"}, idx))
    return out


def derive_session_sentinel(turns: list, session_id: Optional[str] = None, *,
                            min_findings: int = SENTINEL_MIN_FINDINGS) -> Optional[dict]:
    """Multi-turn attack sentinel — pure. Per-turn guards judge one request at a
    time; a session accumulating ``min_findings``+ injection-flagged inbound
    requests (any mode — observe findings count too) reads as a probing
    campaign and surfaces ONE session-level danger item."""
    flagged = []
    tag = None
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        gi = turn.get("guardrail_inbound")
        if isinstance(gi, dict) and (gi.get("findings") or gi.get("action") in ("annotate", "block")):
            flagged.append(turn.get("turn"))
            tag = tag or (turn.get("profile_tag") or "").lstrip("@") or None
    if len(flagged) < min_findings:
        return None
    return {"session_id": session_id, "turn_id": None, "profile_tag": tag,
            "event_type": "session_sentinel", "severity": derived_severity("session_sentinel"),
            "summary": (f"Possible multi-turn attack: {len(flagged)} injection-flagged "
                        f"request(s) in one session (turns {', '.join(str(t) for t in flagged[:6])}"
                        f"{'…' if len(flagged) > 6 else ''})"),
            "payload": {"flagged_turns": flagged[:20], "count": len(flagged)},
            "source": "derived"}


# ── derived-observation acknowledgments (PROP-002, the completeness invariant) ─
# A derived observation IS the sealed trace and is never rewritten — but a
# danger item nobody can ever disposition is permanently unresolvable residue,
# and a red reading with no route to resolution is the failure the completeness
# invariant forbids ("anything that renders `needs you now` resolves to exactly
# one open item or displays its recorded disposition"). The ack is therefore a
# SEPARATE stored disposition ABOUT the trace, keyed (session, turn, event_type)
# — the same append-only pattern review decisions already use. Fail-open like
# every write in this module.
def ack_derived(user_uuid: str, session_id: Optional[str], turn_id,
                event_type: str, db_path: Optional[str] = None) -> bool:
    if not event_type:
        return False
    try:
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO derived_acks (user_id, session_id, turn_id,"
                " event_type, acked_at) VALUES (?,?,?,?,?)",
                (user_uuid, session_id or "",
                 "" if turn_id is None else str(turn_id), event_type, _now()))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"trust-events: derived ack failed: {e}")
        return False


def derived_ack_index(user_uuid: str, db_path: Optional[str] = None) -> set:
    """``{(session_id, turn_id_str, event_type)}`` of the caller's acknowledged
    derived observations (turn_id '' = session-level, e.g. the sentinel)."""
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT session_id, turn_id, event_type FROM derived_acks "
                "WHERE user_id = ?", (user_uuid,)).fetchall()
            return {(r[0], r[1], r[2]) for r in rows}
        finally:
            conn.close()
    except Exception:
        return set()


def apply_derived_acks(items: list, acked: set) -> list:
    """Stamp ``acknowledged`` onto derived items the owner has marked seen.
    Pure — the endpoint supplies the index so this stays CI-testable."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        key = (it.get("session_id") or "",
               "" if it.get("turn_id") is None else str(it.get("turn_id")),
               it.get("event_type") or "")
        out.append({**it, "acknowledged": True} if key in acked else it)
    return out
