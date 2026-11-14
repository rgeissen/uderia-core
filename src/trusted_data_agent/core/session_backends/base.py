"""Abstract session persistence backend — the storage seam for session_manager.

A backend owns three things:
  1. Full session body load / save / delete / exists.
  2. The session-summary "index" used for fast listing, and its lifecycle.
  3. Slow-path scans (only meaningful for the file backend; DB backends inherit
     the no-op defaults because their summary query is always authoritative).

Behaviours that are NOT storage — ``last_updated`` stamping, the
``session_model_update`` notification, genie-metadata enrichment — deliberately
stay in ``session_manager`` so they execute identically for every backend.
"""
from abc import ABC, abstractmethod


class SessionPersistenceBackend(ABC):
    # ── full body ────────────────────────────────────────────────────────────
    @abstractmethod
    async def load(self, user_uuid: str, session_id: str) -> dict | None:
        """Return the full session body, or None if it does not exist."""

    @abstractmethod
    async def save(self, user_uuid: str, session_id: str, data: dict) -> bool:
        """Persist the full session body verbatim. Pure write: no stamping, no
        index update, no notifications (the manager owns those). Returns success."""

    @abstractmethod
    async def delete_record(self, user_uuid: str, session_id: str) -> bool:
        """Hard-delete the stored body. (Archiving is a normal save, not this.)"""

    @abstractmethod
    async def exists(self, user_uuid: str, session_id: str) -> bool:
        """Whether a body is stored for (user, session)."""

    # ── summary index ────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def index_ready(self) -> bool:
        """True once the summary index is usable."""

    @abstractmethod
    async def init_index(self) -> None:
        """Create / open the summary index."""

    @abstractmethod
    async def ensure_index_ready(self) -> None:
        """Init the index and rebuild it if empty/stale (startup hook)."""

    @abstractmethod
    async def query_summaries(self, user_uuid, include_archived: bool = False,
                              filter_by_user: bool = True) -> list | None:
        """Return summary dicts, or None if the index is unavailable (the caller
        should then fall back to ``scan_summaries``)."""

    @abstractmethod
    async def upsert_summary(self, session_id: str, data: dict) -> None:
        """Insert/update one session's summary row from its body."""

    @abstractmethod
    async def delete_summary(self, session_id: str) -> None:
        """Remove one session's summary row."""

    @abstractmethod
    async def rebuild_summaries(self) -> None:
        """Repopulate the whole index from authoritative storage."""

    # ── slow-path scans (file backend only; DB backends keep the defaults) ───
    async def scan_summaries(self, user_uuid, include_archived: bool = False,
                             filter_by_user: bool = True) -> list:
        """Build summaries directly from storage when the index is unavailable."""
        return []

    async def scan_all_bodies(self, include_archived: bool = True) -> list:
        """Load every session body across all users when the index is unavailable."""
        return []
