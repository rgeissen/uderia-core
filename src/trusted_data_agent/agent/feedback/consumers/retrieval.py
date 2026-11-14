"""Vote consumer — per-document retrieval feedback (the Focus lane's learning gap).

Applies to any turn that RETRIEVED — keyed on the turn carrying a knowledge
retrieval event, not on profile type, because the signal is about the DOCUMENTS
rather than the engine that read them (a Focus turn, an Optimize turn that consulted
a knowledge repository, and a genie expert's turn all say the same thing about a
source).

**Records only.** No reranking, no scoring, no quarantine — the rows surface in
Corpus Health for a human to act on. A statistical signal never acts alone.
"""
from __future__ import annotations

import asyncio
import logging

from trusted_data_agent.agent.feedback.dispatcher import register_consumer, signal_enabled
from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")


def document_ids(docs) -> list:
    """The document ids of the chunks that served an answer. Pure.

    Chunk shapes differ across the retrieval paths, so the id is looked for in the
    three places it actually appears, in order. A chunk carrying none is SKIPPED —
    an unidentifiable source cannot be rated, and inventing a key would attach a
    user's judgement to the wrong document.
    """
    out: list = []
    seen: set = set()
    for d in docs or []:
        if not isinstance(d, dict):
            continue
        meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
        doc_id = d.get("document_id") or d.get("id") or meta.get("document_id")
        if not doc_id:
            continue
        doc_id = str(doc_id)
        # One vote per document per turn, however many of its chunks were served —
        # otherwise a document that happened to contribute five chunks would outvote
        # one that contributed a single decisive chunk.
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out


@register_consumer("retrieval")
async def retrieval(event: VoteEvent) -> None:
    if not signal_enabled("retrieval_feedback"):
        return

    from trusted_data_agent.eval.types import RunRecord

    run = RunRecord(event.turn)
    docs = document_ids(run.retrieved_documents())
    if not docs:
        return  # nothing was retrieved (or nothing identifiable) — no signal

    from trusted_data_agent.core import retrieval_feedback

    await asyncio.to_thread(
        retrieval_feedback.record,
        event.user_uuid,
        document_ids=docs,
        vote=event.score,
        collection_id=run.rag_collection_id,
        query_text=event.turn.get("user_query") or "",
        session_id=event.session_id,
        turn=event.turn_id,
    )
    app_logger.debug(
        f"[feedback] retrieval feedback {event.score:+d} recorded for "
        f"{len(docs)} document(s) on turn {event.turn_id}")
