"""``MemoryStore`` — CRUD + dedup-upsert + recall queries + GDPR erase over ``agent_memory``.

Synchronous (brief ORM calls, like ``config_manager``); safe to call from async paths — the
SQLite serialization layer (core/sqlite_util) holds the process lock around the short C-calls.

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.2.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from components.builtin.memory.types import (
    MemoryItem, normalize_kind, normalize_source,
)
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


def _row_to_item(row) -> MemoryItem:
    return MemoryItem(
        id=row.id, content=row.content, kind=row.kind, source=row.source,
        mem_key=row.mem_key, profile_id=row.profile_id, confidence=row.confidence,
        active=bool(row.active), session_id=row.session_id, turn_id=row.turn_id,
        recall_count=int(row.recall_count or 0),
        created_at=_iso(row.created_at), updated_at=_iso(row.updated_at),
        last_recalled_at=_iso(row.last_recalled_at),
    )


def screen_memory_content(content: str, enabled: Optional[bool] = None) -> List[str]:
    """Memory firewall — screen a to-be-stored memory for prompt-injection patterns.

    A memory item carrying an injection ("ignore all previous instructions…") is
    a PERSISTENT compromise vector: it would be recalled into every future turn
    of the agent, surviving across sessions. Returns the matched pattern labels
    (empty = clean / screening off). Deterministic, 0-token — the same
    high-precision detector the inbound guardrail uses.

    ``enabled`` defaults to the guardrails master switch (GUARDRAILS_ENABLED),
    so with the master off the write path is byte-identical to baseline.
    Fails open: any error screens as clean (a firewall bug must never block
    legitimate memory writes).
    """
    try:
        if enabled is None:
            from trusted_data_agent.core.config import APP_CONFIG
            enabled = bool(getattr(APP_CONFIG, "GUARDRAILS_ENABLED", False))
        if not enabled or not content:
            return []
        from trusted_data_agent.agent.guardrails.primitives import detect_prompt_injection
        return sorted({label for label, _ in detect_prompt_injection(content)})
    except Exception:
        return []


def _report_quarantine(user_uuid: str, labels, *, source=None, session_id=None, turn_id=None) -> None:
    """The memory firewall's trust event + live banner — ONE wording for every write path
    (a new memory and an edited one). Best-effort: never fails the write."""
    try:
        from trusted_data_agent.core import trust_events
        summary = ("Memory firewall quarantined a memory write "
                   f"(pattern(s): {', '.join(labels)}) — stored paused, never recalled")
        trust_events.record_event(
            user_uuid, "memory_quarantine", summary,
            severity="warning", session_id=session_id, turn_id=turn_id,
            payload={"labels": list(labels), "source": source})
        # Live banner + in-place inbox refresh (loop-guarded no-op off-loop).
        trust_events.broadcast_notice(user_uuid, "memory_quarantine", summary,
                                      session_id=session_id, severity="warning")
    except Exception:  # noqa: BLE001
        pass


class MemoryStore:
    """Thin ORM facade. One instance is cheap; all methods open their own short session."""

    def _session(self):
        from trusted_data_agent.auth.database import SessionLocal
        return SessionLocal()

    # --- write ----------------------------------------------------------------

    @_signals.announces("memory", "created", user="user_uuid", ids=("id",), profile="item.profile_id")
    def add(self, user_uuid: str, item: MemoryItem) -> MemoryItem:
        """Insert, or update-in-place when ``mem_key`` collides for the same (user, profile).
        Returns the persisted item with id/timestamps populated.

        Memory firewall: when the guardrails master switch is on, content that
        matches a prompt-injection pattern is stored QUARANTINED (active=False) —
        preserved and inspectable in the Memory manager, but never recalled into
        a future turn. Master off → byte-identical baseline write."""
        from trusted_data_agent.auth.models import AgentMemory
        quarantine_labels = screen_memory_content(item.content)
        if quarantine_labels:
            item.active = False
            logger.warning(
                f"Memory firewall: quarantined a memory write for user {user_uuid} "
                f"(prompt-injection pattern(s): {', '.join(quarantine_labels)}) — "
                f"stored paused, never recalled.")
            _report_quarantine(user_uuid, quarantine_labels, source=item.source,
                               session_id=item.session_id, turn_id=item.turn_id)
        s = self._session()
        try:
            row = None
            if item.mem_key:
                row = (s.query(AgentMemory)
                       .filter(AgentMemory.user_id == user_uuid,
                               AgentMemory.profile_id == item.profile_id,
                               AgentMemory.mem_key == item.mem_key)
                       .first())
            now = datetime.now(timezone.utc)
            if row is not None:
                row.content = item.content
                row.kind = normalize_kind(item.kind)
                row.source = normalize_source(item.source)
                row.confidence = item.confidence
                row.active = False if quarantine_labels else True
                row.session_id = item.session_id or row.session_id
                row.turn_id = item.turn_id or row.turn_id
                row.updated_at = now
            else:
                row = AgentMemory(
                    user_id=user_uuid, profile_id=item.profile_id,
                    kind=normalize_kind(item.kind), mem_key=item.mem_key,
                    content=item.content, source=normalize_source(item.source),
                    confidence=item.confidence, active=item.active,
                    session_id=item.session_id, turn_id=item.turn_id,
                )
                s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_item(row)
        finally:
            s.close()

    @_signals.announces("memory", "updated", user="user_uuid", ids=("item_id",))
    def update(self, user_uuid: str, item_id: str, **fields) -> Optional[MemoryItem]:
        from trusted_data_agent.auth.models import AgentMemory
        edit_labels = None
        s = self._session()
        try:
            row = (s.query(AgentMemory)
                   .filter(AgentMemory.id == item_id, AgentMemory.user_id == user_uuid)
                   .first())
            if row is None:
                return None
            if "content" in fields and fields["content"]:
                row.content = str(fields["content"])
                # Memory firewall on edited content too — an edit must not be a
                # side door around the quarantine on add().
                edit_labels = screen_memory_content(row.content)
                if edit_labels:
                    fields = dict(fields)
                    fields["active"] = False
                    logger.warning(
                        f"Memory firewall: quarantined an edited memory {item_id} "
                        f"(prompt-injection pattern) — stored paused.")
            if "kind" in fields:
                row.kind = normalize_kind(fields["kind"])
            if "active" in fields:
                row.active = bool(fields["active"])
            if "confidence" in fields:
                try:
                    row.confidence = max(0.0, min(1.0, float(fields["confidence"])))
                except (TypeError, ValueError):
                    pass
            if "profile_id" in fields:
                # Move / promote: reassign owner (None / "" = promote to global/shared).
                row.profile_id = fields["profile_id"] or None
            if "mem_key" in fields:
                mk = fields["mem_key"]
                row.mem_key = (str(mk).strip().lower()[:120] or None) if mk else None
            row.updated_at = datetime.now(timezone.utc)
            s.commit()
            s.refresh(row)
            if edit_labels:
                # the same trust event + banner add() raises — an edit that the firewall
                # paused was otherwise visible only in the server log
                _report_quarantine(user_uuid, edit_labels, source="edit",
                                   session_id=row.session_id, turn_id=row.turn_id)
            return _row_to_item(row)
        finally:
            s.close()

    @_signals.announces("memory", "deleted", user="user_uuid", ids=("item_id",))
    def delete(self, user_uuid: str, item_id: str) -> bool:
        from trusted_data_agent.auth.models import AgentMemory
        s = self._session()
        try:
            n = (s.query(AgentMemory)
                 .filter(AgentMemory.id == item_id, AgentMemory.user_id == user_uuid)
                 .delete())
            s.commit()
            return n > 0
        finally:
            s.close()

    @_signals.announces("memory", "deleted", user="user_uuid", profile="profile_id")
    def erase_all(self, user_uuid: str, profile_id: Optional[str] = None) -> int:
        """GDPR erase. ``profile_id=None`` ⇒ erase ALL of the user's memory."""
        from trusted_data_agent.auth.models import AgentMemory
        s = self._session()
        try:
            q = s.query(AgentMemory).filter(AgentMemory.user_id == user_uuid)
            if profile_id is not None:
                q = q.filter(AgentMemory.profile_id == profile_id)
            n = q.delete()
            s.commit()
            return n
        finally:
            s.close()

    @_signals.announces("memory", "updated", user="user_uuid", aspect="active")
    def set_active_by_source(self, user_uuid: str, *, session_id: str, turn_id,
                             source: str, active: bool) -> int:
        """Pause or resume the memories a specific turn produced. Returns rows changed.

        The vote signal's hook (docs/roadmap/VOTE_SIGNAL_PLAN.md §10): a fact the
        extractor distilled from a turn the user then REJECTED must stop being recalled.

        Deactivates, never deletes — the row stays visible and re-activatable in the
        Memory manager, so the user can see what their downvote paused and undo it.
        ``source`` is required and always ``learned`` at the call site: a fact the user
        deliberately asked the agent to remember must NOT be killed by a downvote on
        the turn it happened in — only machine-inferred ones.
        """
        if not user_uuid or not session_id or turn_id is None:
            return 0
        from trusted_data_agent.auth.models import AgentMemory
        s = self._session()
        try:
            rows = (s.query(AgentMemory)
                    .filter(AgentMemory.user_id == user_uuid,
                            AgentMemory.session_id == session_id,
                            AgentMemory.turn_id == str(turn_id),
                            AgentMemory.source == source,
                            AgentMemory.active.is_(not active))
                    .all())
            now = datetime.now(timezone.utc)
            for row in rows:
                row.active = active
                row.updated_at = now
            s.commit()
            return len(rows)
        except Exception as e:  # noqa: BLE001 — a vote must never fail on this
            logger.warning(f"memory set_active_by_source failed: {e}")
            return 0
        finally:
            s.close()

    def bump_recall(self, user_uuid: str, item_ids: List[str]) -> None:
        """Best-effort recall feedback; never raises into the caller."""
        if not item_ids:
            return
        from trusted_data_agent.auth.models import AgentMemory
        s = self._session()
        try:
            now = datetime.now(timezone.utc)
            for row in (s.query(AgentMemory)
                        .filter(AgentMemory.user_id == user_uuid,
                                AgentMemory.id.in_(list(item_ids)))):
                row.recall_count = int(row.recall_count or 0) + 1
                row.last_recalled_at = now
            s.commit()
        except Exception as e:
            logger.debug(f"memory bump_recall skipped: {e}")
        finally:
            s.close()

    # --- read ----------------------------------------------------------------

    def list_items(self, user_uuid: str, profile_id: Optional[str] = None,
                   kind: Optional[str] = None,
                   include_inactive: bool = True) -> List[MemoryItem]:
        """Inspection list (Memory panel / REST GET). ``profile_id`` filters exactly."""
        from trusted_data_agent.auth.models import AgentMemory
        s = self._session()
        try:
            q = s.query(AgentMemory).filter(AgentMemory.user_id == user_uuid)
            if profile_id is not None:
                q = q.filter(AgentMemory.profile_id == profile_id)
            if kind:
                q = q.filter(AgentMemory.kind == kind)
            if not include_inactive:
                q = q.filter(AgentMemory.active == True)  # noqa: E712
            rows = q.order_by(AgentMemory.updated_at.desc()).all()
            return [_row_to_item(r) for r in rows]
        finally:
            s.close()

    def list_for_recall(self, user_uuid: str, profile_id: Optional[str],
                        scope: str = "profile") -> List[MemoryItem]:
        """Active candidates for recall. ``profile`` scope = user-global ∪ this profile;
        ``global`` scope = all of the user's active items."""
        from trusted_data_agent.auth.models import AgentMemory
        s = self._session()
        try:
            q = (s.query(AgentMemory)
                 .filter(AgentMemory.user_id == user_uuid,
                         AgentMemory.active == True))  # noqa: E712
            if scope != "global":
                q = q.filter((AgentMemory.profile_id == None) |  # noqa: E711
                             (AgentMemory.profile_id == profile_id))
            rows = q.order_by(AgentMemory.updated_at.desc()).all()
            return [_row_to_item(r) for r in rows]
        finally:
            s.close()
