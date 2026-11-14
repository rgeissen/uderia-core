"""Review queue — human-in-the-loop disposition for withheld answers.

The enforcement ladder gains a fourth acting state: **review** = enforce **with
retention**. Where `enforce` withholds/redacts and DISCARDS the original,
`review` withholds/redacts identically but parks the ORIGINAL answer here so a
human can disposition it — nothing is lost, nothing is silently delivered.
This is the platform's EU-AI-Act-Article-14-shaped human-oversight primitive
(the compliance mapping can now point at a mechanism, not a promise).

Semantics (v1, honest):
- The queue holds the **owner's own** withheld content — the reviewer is the
  user whose agent produced it (the case that matters most: scheduled /
  autonomous runs nobody watched).
- **Four eyes** (PROP-008 §5.4a, parked ACTION writes): when the agent's
  ``authorityConfig.actions.approvers`` names approvers, the item is parked
  with their ids (``approvers_json``). Then ONLY they decide — never the
  owner — and they see the item in their own queue; the write itself still
  runs as the owner's agent on the owner's connection. ``may_decide`` is the
  one rule every caller asks.
- **Approve** = the owner has seen the original and accepts it; the item is
  marked approved and an audit event is appended to the turn. Retroactively
  rewriting the delivered conversation is deliberately NOT done (history stays
  what it was) — the approved content is available from the queue item.
- **Uphold** (reject) = the withholding stands; recorded with an optional note.

Two parking classes (the retention-default work): ``pending`` = review mode's
work item — a human OWES a disposition, counted by the REVIEWED station and the
inbox badge. ``retained`` = plain enforce's recovery ledger for a FULL
withholding (a grounding/toxicity BLOCK — never a routine PII redaction, whose
parked content would be the raw PII): the original is recoverable and MAY be
dispositioned, but nothing is owed, nothing is counted as open work, and no
oversight is claimed. Collapsing the two would make "we have human oversight"
indistinguishable from "we keep a trash can".

Retention bounds: ``pending`` is NEVER auto-pruned (owed work must not silently
vanish). ``retained`` + settled (approved/rejected) items are pruned after
``RETENTION_DAYS``, and ``retained`` is additionally capped per user at
``RETAINED_CAP`` (oldest first) — the queue no longer grows forever. The audit
event a disposition appended to its turn survives pruning (it lives on the
sealed turn, not here); a pruned released-item simply stops feeding the
released-value allowlist, which is the data-minimization intent.

Storage mirrors core/trust_events.py: a small self-applying table, every write
fail-open — a queue failure must never affect the enforcement action itself.
Unlike trust_events, rows DO carry the raw withheld content (that is the point
of review); access is owner-scoped, widened only to the designated approvers
of an item.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

_DDL = """
CREATE TABLE IF NOT EXISTS review_items (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    source         TEXT NOT NULL,
    session_id     TEXT,
    turn_id        TEXT,
    profile_tag    TEXT,
    reason         TEXT,
    categories_json TEXT,
    original_text  TEXT,
    delivered_text TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    decided_at     TEXT,
    note           TEXT
);
"""

STATUSES = ("pending", "approved", "rejected", "retained")

#: Settled/retained items older than this are pruned; pending is never pruned.
RETENTION_DAYS = 90
#: Per-user ceiling on retained (enforce-parked) originals; oldest pruned first.
RETAINED_CAP = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None):
    conn = sqlite_util.connect(_db_path(db_path))
    conn.execute(_DDL)
    # Additive, self-applying migration: release scope. 0 = audit-only approve
    # (the default — the original stays in the record); 1 = the owner explicitly
    # released the original INTO the conversation (forward-only context).
    try:
        conn.execute("ALTER TABLE review_items ADD COLUMN released_to_context INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass  # column already exists
    # Four eyes (PROP-008 §5.4a): the accounts designated to decide this item, as a JSON
    # list of user ids. NULL/empty = the owner decides (the single-account baseline).
    try:
        conn.execute("ALTER TABLE review_items ADD COLUMN approvers_json TEXT")
    except Exception:
        pass  # column already exists
    # …and who decided it — the owner, or a designated approver (audit surfaces name them)
    try:
        conn.execute("ALTER TABLE review_items ADD COLUMN decided_by TEXT")
    except Exception:
        pass  # column already exists
    conn.commit()
    return conn


# ── which withheld originals may be RELEASED into the conversation ───────────
# Release injects the withheld original into subsequent turns' context (see the
# `review_release` context module). That is coherent only when the original is
# something the AGENT produced and the owner is permitting it to be stated:
#
#   grounding / guardrail_outbound → the original is an ANSWER            ✅ releasable
#   guardrail_inbound              → the original is the USER'S REQUEST   ❌ never
#
# An inbound item is parked precisely BECAUSE its text is hostile (a detected
# prompt injection). Releasing it would inject the attacker's payload into the
# model's context under an "[Owner-released after human review]" header — i.e.
# labelled as owner-trusted — completing the attack the guard just blocked, with
# an endorsement the raw attack never had. Approve/uphold stay available: the
# owner can still record a judgement, they simply cannot arm it.
#
# Allow-list, not a deny-list: a future source is non-releasable until someone
# decides it is, which is the safe default for a channel that writes into context.
RELEASABLE_SOURCES = frozenset({"grounding", "guardrail_outbound"})


def is_releasable_source(source: Optional[str]) -> bool:
    """May an item from this source be released INTO the conversation?"""
    return (source or "") in RELEASABLE_SOURCES


def park(user_uuid: str, *, source: str, original_text: str, delivered_text: str,
         reason: str = "", categories: Optional[list] = None,
         session_id: Optional[str] = None, turn_id=None,
         profile_tag: Optional[str] = None, status: str = "pending",
         approvers: Optional[list] = None,
         db_path: Optional[str] = None) -> Optional[str]:
    """Park a withheld original. ``status='pending'`` (review mode) = a human owes
    a disposition; ``status='retained'`` (plain enforce, full withholdings only) =
    a recovery ledger entry — recoverable, dispositionable, but never owed and
    never counted as open work. Fail-open: returns the item id, or None on any
    failure (the enforcement action already happened and must not depend on the
    queue)."""
    try:
        if status not in ("pending", "retained"):
            # A typo must not silently promote a recovery entry into owed work
            # (or vice versa) — log and take the safe reading: owed.
            logger.warning(f"review-queue: unknown park status {status!r} — using 'pending'")
            status = "pending"
        rid = str(uuid.uuid4())
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO review_items (id, created_at, user_id, source, session_id,"
                " turn_id, profile_tag, reason, categories_json, original_text,"
                " delivered_text, status, approvers_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, _now(), user_uuid, source, session_id,
                 str(turn_id) if turn_id is not None else None,
                 (profile_tag or "").lstrip("@") or None, reason,
                 json.dumps(categories or []), original_text or "", delivered_text or "",
                 status, json.dumps([str(a) for a in approvers if a]) if approvers else None))
            _prune(conn, user_uuid)
            conn.commit()
        finally:
            conn.close()
        logger.info(f"review-queue: parked a withheld answer ({source}) as {rid} [{status}]")
        return rid
    except Exception as e:
        logger.warning(f"review-queue: park failed (enforcement unaffected): {e}")
        return None


def _prune(conn, user_uuid: str) -> None:
    """Retention bounds, applied opportunistically on each park (same connection,
    same transaction — fail-open with it). ``pending`` is untouchable: silently
    deleting OWED work would fake a clean inbox. Settled + retained age out after
    ``RETENTION_DAYS``; ``retained`` additionally respects ``RETAINED_CAP`` per
    user (oldest first) so the recovery ledger cannot grow without bound."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute(
        "DELETE FROM review_items WHERE user_id = ? AND status != 'pending' "
        "AND created_at < ?", (user_uuid, cutoff))
    conn.execute(
        "DELETE FROM review_items WHERE user_id = ? AND status = 'retained' "
        "AND id NOT IN (SELECT id FROM review_items WHERE user_id = ? AND "
        "status = 'retained' ORDER BY created_at DESC LIMIT ?)",
        (user_uuid, user_uuid, RETAINED_CAP))


# TC-72: the columns are NAMED, in one place — `SELECT *` + ordinal indexing meant any
# future column insertion silently mis-mapped fields, including `original_text` (the
# highest-sensitivity field here) and the `released_to_context` flag the outbound
# allowlist depends on. The ALTER-appended column is simply part of the list now.
_ITEM_COLS = ("id, created_at, user_id, source, session_id, turn_id, profile_tag, "
              "reason, categories_json, original_text, delivered_text, status, decided_at, "
              "note, released_to_context, approvers_json, decided_by")


def _approvers(raw) -> list:
    try:
        v = json.loads(raw) if raw else []
    except Exception:
        v = []
    return [str(x) for x in v] if isinstance(v, list) else []


def may_decide(user_uuid: str, item: dict) -> bool:
    """Four eyes (PROP-008 §5.4a), ONE rule: an item with designated approvers is decided by
    one of them — never by its owner; an item without is decided by its owner. Pure."""
    if not item or not user_uuid:
        return False
    approvers = item.get("approvers") or []
    if approvers:
        return user_uuid in approvers
    return user_uuid == item.get("owner_id")


def _row_to_item(r, include_content: bool, caller: Optional[str] = None) -> dict:
    (rid, created_at, owner_id, source, session_id, turn_id, profile_tag,
     reason, categories, original_text, delivered_text, status, decided_at,
     note, released, approvers_raw, decided_by) = r
    try:
        cats = json.loads(categories or "[]")
    except Exception:
        cats = []
    item = {"id": rid, "created_at": created_at, "source": source, "session_id": session_id,
            "turn_id": turn_id, "profile_tag": profile_tag, "reason": reason,
            "categories": cats, "status": status, "decided_at": decided_at, "note": note,
            "released_to_context": bool(released), "owner_id": owner_id,
            "approvers": _approvers(approvers_raw), "decided_by": decided_by}
    if caller is not None:
        item["can_decide"] = may_decide(caller, item)
    if include_content:
        item["original_text"] = original_text
        item["delivered_text"] = delivered_text
    return item


def _visible_to(user_uuid: str):
    """The WHERE fragment for the items a caller may see: their own, and those awaiting
    them as a designated approver (the JSON list holds exact quoted ids)."""
    return "(user_id = ? OR approvers_json LIKE ?)", [user_uuid, f'%"{user_uuid}"%']


def list_items(user_uuid: str, *, status: Optional[str] = "pending", limit: int = 100,
               include_content: bool = False, awaiting_me: bool = False,
               db_path: Optional[str] = None) -> list:
    """The caller's items. ``awaiting_me`` widens the list to items OTHER people's agents
    parked for the caller as designated approver — only the decision surfaces (the inbox
    listing, the approvals verb) ask for it; trust history, the agent story and the audit
    pack read the caller's own agents' items alone, so another person's write can never be
    attributed to the caller's same-tag agent."""
    try:
        conn = _connect(db_path)
        try:
            if awaiting_me:
                frag, args = _visible_to(user_uuid)
            else:
                frag, args = "user_id = ?", [user_uuid]
            q = f"SELECT {_ITEM_COLS} FROM review_items WHERE {frag}"
            if status:
                q += " AND status = ?"
                args.append(status)
            q += " ORDER BY created_at DESC LIMIT ?"
            args.append(int(limit))
            rows = conn.execute(q, args).fetchall()
        finally:
            conn.close()
        return [_row_to_item(r, include_content, caller=user_uuid) for r in rows]
    except Exception as e:
        logger.warning(f"review-queue: list failed: {e}")
        return []


def get_item(user_uuid: str, item_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    try:
        conn = _connect(db_path)
        try:
            frag, args = _visible_to(user_uuid)
            r = conn.execute(f"SELECT {_ITEM_COLS} FROM review_items WHERE id = ? AND {frag}",
                             [item_id, *args]).fetchone()
        finally:
            conn.close()
        return _row_to_item(r, include_content=True, caller=user_uuid) if r else None
    except Exception as e:
        logger.warning(f"review-queue: get failed: {e}")
        return None


def decide(user_uuid: str, item_id: str, decision: str, note: Optional[str] = None,
           release_to_context: bool = False, db_path: Optional[str] = None) -> bool:
    """Disposition an open item: 'approved' (owner accepts the original) or
    'rejected' (the withholding stands). Open = ``pending`` (owed) or
    ``retained`` (not owed, but the owner may still rule on a recovered
    original — recoverability is the point of retaining it).

    ``release_to_context`` (approve only, default False = audit-only): the owner
    EXPLICITLY releases the withheld original into the conversation — it becomes
    available to the agent on SUBSEQUENT turns (forward-only; the sealed turn is
    never rewritten) and its values stop being re-redacted in this session.
    A deliberate, logged act of human oversight — never a silent default.

    Returns a TRI-STATE (TC-73): ``True`` = decided; ``False`` = the item is not
    pending / not the caller's (a settled fact); ``None`` = the STORE failed — the
    caller must report a service error, not tell the owner "the item is not pending"."""
    if decision not in ("approved", "rejected"):
        return False
    try:
        conn = _connect(db_path)
        try:
            frag, args = _visible_to(user_uuid)
            r = conn.execute(f"SELECT {_ITEM_COLS} FROM review_items WHERE id = ? AND {frag}",
                             [item_id, *args]).fetchone()
            if not r or not may_decide(user_uuid, _row_to_item(r, False)):
                return False   # not yours to decide: someone else's, or awaiting a designated approver
            cur = conn.execute(
                "UPDATE review_items SET status = ?, decided_at = ?, note = ?,"
                " released_to_context = ?, decided_by = ? "
                "WHERE id = ? AND status IN ('pending', 'retained')",
                (decision, _now(), note,
                 1 if (release_to_context and decision == "approved") else 0,
                 user_uuid, item_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"review-queue: decide failed: {e}")
        return None


def withdraw(user_uuid: str, item_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """The OWNER withdraws one of their own PENDING items — a parked write that will never be
    decided, a proposal nobody will rule on. The row goes (nothing was ever written for it;
    the act log keeps the park). Returns the item that went, ``None`` when there is no such
    pending item of the caller's; a designated approver cannot withdraw (``{"refused": …}``)."""
    conn = _connect(db_path)
    try:
        r = conn.execute(f"SELECT {_ITEM_COLS} FROM review_items WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return None
        item = _row_to_item(r, False, caller=user_uuid)
        if str(item.get("owner_id") or item.get("user_id") or "") != str(user_uuid):
            return {"refused": "not_owner", "message": "only the item's owner may withdraw it"}
        if item.get("status") != "pending":
            return {"refused": "not_pending", "message": f"this item is {item.get('status')} — only a pending item is withdrawn"}
        conn.execute("DELETE FROM review_items WHERE id = ? AND status = 'pending'", (item_id,))
        conn.commit()
        return item
    finally:
        conn.close()


def released_originals_for_session(user_uuid: str, session_id: str,
                                   db_path: Optional[str] = None) -> list:
    """The originals the owner explicitly released INTO this session's
    conversation: [{turn_id, source, original_text, decided_at}], oldest first.
    Consumed by the released-review context module (forward-only injection) and
    by the outbound guardrail's released-value allowlist. Fail-open → []."""
    if not session_id:
        return []
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT turn_id, source, original_text, decided_at FROM review_items "
                "WHERE user_id = ? AND session_id = ? AND status = 'approved' "
                "AND released_to_context = 1 ORDER BY created_at ASC",
                (user_uuid, session_id)).fetchall()
        finally:
            conn.close()
        # Defence in depth: filter on read as well as on write. A row released
        # before this rule existed (or by any future path that forgets the check)
        # must still never reach the context window — this is the one function
        # that feeds released text to the model.
        return [{"turn_id": r[0], "source": r[1], "original_text": r[2] or "",
                 "decided_at": r[3]} for r in rows if is_releasable_source(r[1])]
    except Exception as e:
        logger.warning(f"review-queue: released-for-session failed: {e}")
        return []


def pending_count(user_uuid: str, db_path: Optional[str] = None) -> int:
    """Open OWED work only — ``retained`` items are deliberately excluded, so the
    REVIEWED station and the inbox badge keep meaning "a human must act", never
    "content is stored"."""
    try:
        conn = _connect(db_path)
        try:
            # owed BY THIS PERSON: their own items nobody else is designated to decide, and
            # the items awaiting them as an approver — never an item awaiting someone else
            row = conn.execute(
                "SELECT COUNT(*) FROM review_items WHERE status = 'pending' AND ("
                "(user_id = ? AND (approvers_json IS NULL OR approvers_json IN ('', '[]')))"
                " OR approvers_json LIKE ?)",
                (user_uuid, f'%"{user_uuid}"%')).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def retained_count(user_uuid: str, db_path: Optional[str] = None) -> int:
    """Recoverable enforce-parked originals (no disposition owed)."""
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM review_items WHERE user_id = ? AND status = 'retained'",
                (user_uuid,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0
