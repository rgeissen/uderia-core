"""
Document Context module.

The turn's attached documents, measured against the budget and delivered as the turn's
document text (the executor and the engines send THIS text — ``PlanExecutor.documents_for_turn``).

- One loader: ``executor.load_document_context`` (the sealed attachment manifest through
  ``session_files.readable_manifest`` — a file deleted from Files is never read).
- One limit: ``executor.document_char_limit`` — the allocation, never less than today's fixed
  limit. Condensing never goes below that floor either: an attachment the user sent is never
  silently cut further than before the budget existed.
- When a document was cut at its excerpt and the session's search index holds it (the CWT
  chose ``rag_offload`` for this module, so the executor indexed the documents), the room left
  under the limit is filled with passages from BEYOND the excerpt, searched for this question —
  instead of the rest simply being lost.
- Search results are only ever this turn's attachments that are still readable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from ..base import AssemblyContext, Contribution, ContextModule
from ..token_estimator import estimate_tokens, tokens_to_chars

if TYPE_CHECKING:
    from trusted_data_agent.vectorstore.types import VectorDocument

logger = logging.getLogger("quart.app")

_DOC_MARK = "--- Document: "
_PASSAGES_HEAD = "--- Relevant passages from beyond the excerpt (searched for this question) ---"


def _floor_chars() -> int:
    from trusted_data_agent.core.config import APP_CONFIG
    return APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS


class DocumentContextModule(ContextModule):
    """
    Contributes the turn's attached documents to the context window.

    Condensation: truncation at a document boundary, never below today's fixed limit.
    RAG condensation: floor% via truncation + passages searched in the session index
    (both clamped to the same floor).
    Purgeable: drops the session's document search index — never the attachments themselves
    (they belong to Files; deleting them is the Files service's job).
    """

    supports_rag_condensation: bool = True
    APPROX_TOKENS_PER_CHUNK: int = 150
    _MIN_PASSAGE_ROOM = 1_000   # chars: below this, searching is not worth a round trip

    @property
    def module_id(self) -> str:
        return "document_context"

    def applies_to(self, profile_type: str) -> bool:
        return profile_type in ("tool_enabled", "llm_only", "rag_focused")

    # ── contribute ────────────────────────────────────────────────────────────

    async def contribute(
        self,
        budget: int,
        ctx: AssemblyContext,
    ) -> Contribution:
        attachments = list(ctx.session_data.get("attachments") or [])
        if not attachments:
            return Contribution(content="", tokens_used=0,
                                metadata={"doc_count": 0, "reason": "no_attachments"}, condensable=False)

        from trusted_data_agent.agent.executor import document_char_limit, load_document_context
        limit = document_char_limit(budget)
        try:
            text, events = load_document_context(ctx.user_uuid, ctx.session_id, attachments, max_total_chars=limit)
        except Exception as e:  # noqa: BLE001
            logger.error(f"DocumentContextModule: load failed: {e}")
            return Contribution(content="", tokens_used=0, metadata={"doc_count": 0, "error": str(e)}, condensable=False)
        if not text:
            return Contribution(content="", tokens_used=0,
                                metadata={"doc_count": 0, "reason": "no_content_extracted"}, condensable=False)

        passages, n_passages = "", 0
        if events:   # something was cut: search the rest instead of losing it
            passages, n_passages = await self._passages_beyond(ctx, attachments, text, limit - len(text))
        content = text + passages
        return Contribution(
            content=content,
            tokens_used=estimate_tokens(content),
            metadata={
                "doc_count": text.count(_DOC_MARK),
                "truncation_events": len(events),
                "events": events,
                "total_chars": len(content),
                "char_limit": limit,
                "passages_retrieved": n_passages,
            },
            condensable=True,
        )

    async def _passages_beyond(self, ctx: AssemblyContext, attachments: list, delivered: str,
                               room: int) -> "tuple[str, int]":
        store = getattr(ctx, "session_store", None)
        if room < self._MIN_PASSAGE_ROOM or store is None or not store.has_data(self.module_id):
            return "", 0
        query = ctx.session_data.get("current_query", "") or ""
        if not query:
            return "", 0
        allowed = self._readable_attachment_ids(ctx, attachments)
        n = max(2, room // 900)
        try:
            results = await store.retrieve(self.module_id, query, n_results=n * 2)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"DocumentContextModule: search skipped: {e}")
            return "", 0
        picked: List["VectorDocument"] = []
        used = len(_PASSAGES_HEAD) + 2
        for doc in results:
            if (doc.metadata or {}).get("file_id") not in allowed:
                continue
            body = (doc.content or "").strip()
            if not body or body[:200] in delivered:   # already inside the excerpt
                continue
            block = len(body) + 80
            if used + block > room:
                break
            picked.append(doc)
            used += block
        if not picked:
            return "", 0
        return "\n\n" + _PASSAGES_HEAD + "\n" + self._format_rag_chunks(picked), len(picked)

    @staticmethod
    def _readable_attachment_ids(ctx: AssemblyContext, attachments: list) -> Set[str]:
        """This turn's attachments whose file is still readable — search results outside it
        (another turn's document, a file deleted from Files) are never used."""
        from trusted_data_agent.filestore import session_files
        try:
            readable = session_files.readable_manifest(ctx.user_uuid, ctx.session_id) or {}
        except Exception:  # noqa: BLE001
            return set()
        ids = {(a or {}).get("file_id") for a in attachments if isinstance(a, dict)}
        return {i for i in ids if i and i in readable}

    # ── condense ──────────────────────────────────────────────────────────────

    async def condense(
        self,
        content: str,
        target_tokens: int,
        ctx: AssemblyContext,
    ) -> Contribution:
        """Truncate at a document boundary — never below today's fixed limit."""
        if not content:
            return Contribution(content="", tokens_used=0, metadata={"condensed": True, "doc_count": 0})

        floor = _floor_chars()
        char_budget = max(tokens_to_chars(target_tokens), floor)
        if len(content) > char_budget:
            truncated = content[:char_budget]
            # end on a whole document when that still keeps the floor; never cut below it
            last_boundary = truncated.rfind("\n--- End of ")
            if last_boundary > 0:
                end = truncated.find("\n", last_boundary + 1)
                if end > 0 and end >= floor:
                    truncated = truncated[:end]
            content = truncated + "\n... (documents truncated)"

        return Contribution(
            content=content,
            tokens_used=estimate_tokens(content),
            metadata={"condensed": True, "strategy": "truncation", "floor_chars": _floor_chars()},
        )

    async def condense_rag(
        self,
        content: str,
        target_tokens: int,
        query: str,
        ctx: AssemblyContext,
        session_store: Any,
        floor_pct: int,
    ) -> Contribution:
        """Keep floor_pct% of the target as the excerpt and fill the rest with passages searched
        for the question — the target never below today's fixed limit, the passages only from
        this turn's readable attachments."""
        target_tokens = max(target_tokens, _floor_chars() // 4)
        if estimate_tokens(content) <= target_tokens:
            return Contribution(content=content, tokens_used=estimate_tokens(content),
                                metadata={"condensed": False, "reason": "within_floor"}, condensable=False)

        floor_tokens = int(target_tokens * floor_pct / 100)
        rag_tokens = target_tokens - floor_tokens

        floor_contribution = None
        if floor_pct > 0 and floor_tokens > 0:
            floor_contribution = await self.condense(content, floor_tokens, ctx)

        allowed = self._readable_attachment_ids(ctx, list(ctx.session_data.get("attachments") or []))
        n_chunks = max(1, rag_tokens // self.APPROX_TOKENS_PER_CHUNK)
        results = [d for d in await session_store.retrieve(self.module_id, query, n_results=n_chunks)
                   if (d.metadata or {}).get("file_id") in allowed]
        rag_content = self._format_rag_chunks(results)

        parts = []
        if floor_contribution and floor_contribution.content:
            parts.append(f"--- Recent Context ---\n{floor_contribution.content}")
        if rag_content:
            parts.append(f"--- Relevant Earlier Context ---\n{rag_content}")

        if not parts:
            return await self.condense(content, target_tokens, ctx)

        combined = "\n\n".join(parts)
        return Contribution(
            content=combined,
            tokens_used=estimate_tokens(combined),
            metadata={
                "strategy": "rag_offload",
                "floor_pct": floor_pct,
                "chunks_retrieved": len(results),
                "condensed": True,
            },
            condensable=True,
        )

    def _format_rag_chunks(self, results: "List[VectorDocument]") -> str:
        """Format retrieved document chunks with source labels."""
        parts = []
        for doc in results:
            filename = (doc.metadata or {}).get("filename", "document")
            chunk_num = (doc.metadata or {}).get("chunk_num", "")
            label = f"=== DOCUMENT: {filename}"
            if chunk_num != "":
                label += f" (passage {chunk_num})"
            label += " ==="
            parts.append(f"{label}\n{doc.content}")
        return "\n\n".join(parts)

    # ── purge ─────────────────────────────────────────────────────────────────

    async def purge(
        self,
        session_id: str,
        user_uuid: str,
    ) -> Dict[str, Any]:
        """Drop the session's document search index. The attachments are untouched: they are
        files (PROP-010), removed only through Files or with their conversation."""
        if not session_id or not user_uuid:
            return {"purged": False, "reason": "session_id and user_uuid required"}
        # absolute, like the executor: under the registry's synthetic package a relative import
        # would load a SECOND session_vector_store with its own (empty) registry
        from components.builtin.context_window.session_vector_store import _SESSION_STORES
        store = _SESSION_STORES.get(session_id)
        if store is None or getattr(store, "_user_uuid", None) != user_uuid:
            return {"purged": False, "reason": "No document index for this session"}
        if await store.forget_module(self.module_id):
            return {"purged": True, "details": f"Dropped the document search index of session {session_id}"}
        return {"purged": False, "reason": "No document index for this session"}
