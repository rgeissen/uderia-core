"""
Focus Engine — rag_focused profile execution.

Handles mandatory knowledge retrieval from configured collections followed by
LLM synthesis.  No MCP tools, no Planner/Executor pipeline.

Profile type:  ``rag_focused``  (IFOC label: "Focus", colour: blue)

This engine is registered with EngineRegistry so it can be resolved via:
    EngineRegistry.resolve(profile)   # returns FocusEngine class
"""

from __future__ import annotations
from trusted_data_agent.agent import answer_checks as _answer_checks

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncGenerator

from .registry import EngineRegistry
from trusted_data_agent.agent import resilience_envelope as _renv
from .base import ExecutionEngine

if TYPE_CHECKING:
    from trusted_data_agent.agent.executor import PlanExecutor

app_logger = logging.getLogger("quart.app")


def _assurance_stamps() -> dict:
    """This turn's stamps from gates that fire outside any engine's reach.

    Returns `{}` when nothing recorded — an absent key reads as `unknown` in the
    registry, deliberately NOT as a pass: a gate that had no opportunity to run is
    not the same as one that ran and found nothing.
    """
    try:
        from trusted_data_agent.agent import assurance_tally
        return assurance_tally.snapshot()
    except Exception:  # noqa: BLE001 — never break turn assembly
        return {}



@EngineRegistry.register
class FocusEngine(ExecutionEngine):
    """Knowledge-retrieval execution engine for ``rag_focused`` profiles."""

    profile_type = "rag_focused"

    async def run(self, executor: "PlanExecutor") -> AsyncGenerator[str, None]:  # type: ignore[override]
        """Execute a RAG-focused knowledge retrieval and synthesis turn.

        Extracted from PlanExecutor.run() ``if is_rag_focused:`` block.
        All state is accessed via the ``executor`` parameter.
        """
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.agent.formatter import OutputFormatter
        from trusted_data_agent.core.config import APP_CONFIG

        app_logger.info("🔍 FocusEngine: rag_focused profile detected - mandatory knowledge retrieval")

        # The session is read ONCE here and re-read only after a write to it (an added message,
        # a token update, the turn record): the engine used to read the file twelve times a turn,
        # four of them with nothing written in between (the history's own read among them).
        session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)

        # --- EPC: Record query intake and profile for rag_focused ---
        try:
            if hasattr(executor, 'provenance') and executor.provenance:
                executor.provenance.add_step("query_intake", executor.original_user_input, f"Query: {executor.original_user_input[:100]}")
                _ptag = executor._get_current_profile_tag()
                executor.provenance.add_step("profile_resolve", f"{executor.active_profile_id}:rag_focused:{_ptag}", f"Profile: @{_ptag} (rag_focused)")
        except Exception as _epc_err:
            app_logger.debug(f"EPC rag_focused intake: {_epc_err}")

        # Collect events for plan reload (similar to genie_events and conversation_agent_events)
        # Initialize BEFORE lifecycle emission so we can store the start event
        knowledge_events = []

        # --- PHASE 2: Emit execution_start lifecycle event for rag_focused ---
        try:
            profile_config = executor._get_profile_config()
            knowledge_config = profile_config.get("knowledgeConfig", {})
            knowledge_collections = knowledge_config.get("collections", [])

            execution_start_payload = {
                "profile_type": "rag_focused",
                "profile_tag": executor._get_current_profile_tag(),
                "query": executor.original_user_input,
                "knowledge_collections": len(knowledge_collections)
            }
            start_event = executor._emit_lifecycle_event("execution_start", execution_start_payload)
            yield start_event

            # Store lifecycle start event for reload
            knowledge_events.append({
                "type": "execution_start",
                "payload": execution_start_payload
            })

            app_logger.info("✅ Emitted execution_start event for rag_focused profile")
        except Exception as e:
            app_logger.warning(f"Failed to emit execution_start event: {e}")
        # --- PHASE 2 END ---

        # NOTE: Context window assembly already ran in execute() general block.
        # Budget-aware conversation history is available via executor._cw_conversation_history.

        # --- MANDATORY Knowledge Retrieval ---
        retrieval_start_time = time.time()

        profile_config = executor._get_profile_config()
        knowledge_config = profile_config.get("knowledgeConfig", {})
        knowledge_collections = knowledge_config.get("collections", [])

        # Stamp the slave session with its profile tag + error message even when
        # we early-return for misconfiguration. Without this the slave shows up in
        # the sidebar as an unbranded "New Chat" with no badge — indistinguishable
        # from a fresh empty session, even though it actually ran (and failed) a turn.
        async def _stamp_error_turn(error_msg: str) -> None:
            profile_tag = executor._get_current_profile_tag()
            try:
                await session_manager.update_models_used(
                    executor.user_uuid,
                    executor.session_id,
                    *_renv.served_model(executor.current_provider, executor.current_model),   # S4: the model that SERVED
                    profile_tag,
                )
                await session_manager.add_message_to_histories(
                    executor.user_uuid, executor.session_id, 'assistant',
                    content=error_msg,
                    html_content=f"<div class='error-message'>{error_msg}</div>",
                    profile_tag=profile_tag,
                    is_session_primer=executor.is_session_primer,
                )
            except Exception as _e:
                app_logger.warning(f"FocusEngine: failed to stamp error-turn metadata: {_e}")

        if not knowledge_collections:
            error_msg = "RAG focused profile has no knowledge collections configured."
            await _stamp_error_turn(error_msg)
            yield executor._format_sse_with_depth({"step": "Finished", "error": error_msg}, "error")
            return

        # Check if RAG retriever is available
        if not executor.rag_retriever:
            error_msg = "Knowledge retrieval is not available. RAG system may not be initialized."
            await _stamp_error_turn(error_msg)
            yield executor._format_sse_with_depth({"step": "Finished", "error": error_msg, "error_type": "rag_not_available"}, "error")
            return

        # Retrieve knowledge (REQUIRED) - using three-tier configuration (global -> profile -> locks)
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        effective_config = config_manager.get_effective_knowledge_config(knowledge_config)
        max_docs = effective_config.get("maxDocs", APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS)
        min_relevance = effective_config.get("minRelevanceScore", APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE)
        max_tokens = effective_config.get("maxTokens", APP_CONFIG.KNOWLEDGE_MAX_TOKENS)
        # Budget-aware override from context window module (Phase 5b)
        cw_knowledge_budget = getattr(executor, '_cw_knowledge_max_tokens', None)
        if cw_knowledge_budget:
            max_tokens = min(cw_knowledge_budget, max_tokens) if max_tokens else cw_knowledge_budget
            app_logger.info(f"[rag_focused] Knowledge max_tokens from context window budget: {max_tokens:,}")
        max_chunks_per_doc = effective_config.get("maxChunksPerDocument", APP_CONFIG.KNOWLEDGE_MAX_CHUNKS_PER_DOC)
        freshness_weight = effective_config.get("freshnessWeight", APP_CONFIG.KNOWLEDGE_FRESHNESS_WEIGHT)
        freshness_decay_rate = effective_config.get("freshnessDecayRate", APP_CONFIG.KNOWLEDGE_FRESHNESS_DECAY_RATE)
        synthesis_prompt_override = effective_config.get("synthesisPromptOverride", "")

        app_logger.info(f"[RAG] Effective config: maxDocs={max_docs}, minRelevance={min_relevance}, "
                       f"maxTokens={max_tokens}, maxChunksPerDoc={max_chunks_per_doc}, "
                       f"freshnessWeight={freshness_weight}, freshnessDecay={freshness_decay_rate}, "
                       f"synthesisPrompt={'yes (' + str(len(synthesis_prompt_override)) + ' chars)' if synthesis_prompt_override else 'no'}")

        # Emit start event (fetch actual collection names from metadata)
        collection_names_for_start = []
        for coll_config in knowledge_collections:
            coll_id = coll_config.get("id")
            if coll_id and executor.rag_retriever:
                coll_meta = executor.rag_retriever.get_collection_metadata(coll_id)
                if coll_meta:
                    collection_names_for_start.append(coll_meta.get("name", coll_id))
                else:
                    # Fallback: try to get name from collection DB table directly
                    try:
                        from trusted_data_agent.core.collection_db import get_collection_db
                        coll_db = get_collection_db()
                        coll_info = coll_db.get_collection_by_id(coll_id)
                        if coll_info and coll_info.get("name"):
                            collection_names_for_start.append(coll_info["name"])
                        else:
                            collection_names_for_start.append(coll_config.get("name", coll_id))
                    except Exception as e:
                        app_logger.warning(f"Failed to fetch collection name for {coll_id}: {e}")
                        collection_names_for_start.append(coll_config.get("name", coll_id))
            else:
                collection_names_for_start.append(coll_config.get("name", coll_id or "Unknown"))

        # Collect per-collection search modes for Live Status display
        search_modes = {}
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            _cdb = get_collection_db()
            for coll in knowledge_collections:
                coll_id = coll.get("id")
                coll_name = coll.get("name", str(coll_id))
                if coll_id:
                    db_row = _cdb.get_collection_by_id(coll_id)
                    if db_row:
                        search_modes[coll_name] = db_row.get("search_mode", "semantic")
                        continue
                search_modes[coll_name] = "semantic"
        except Exception as e:
            app_logger.debug(f"Failed to read search_mode for collections: {e}")

        start_event_payload = {
            "collections": collection_names_for_start,
            "max_docs": max_docs,
            "session_id": executor.session_id,
            "search_modes": search_modes,
        }
        knowledge_events.append({"type": "knowledge_retrieval_start", "payload": start_event_payload})
        yield executor._format_sse_with_depth({
            "type": "knowledge_retrieval_start",
            "payload": start_event_payload
        }, event="notification")

        from trusted_data_agent.agent.rag_access_context import RAGAccessContext
        rag_context = RAGAccessContext(user_id=executor.user_uuid, retriever=executor.rag_retriever)

        all_results = await executor.rag_retriever.retrieve_examples(
            query=executor.original_user_input,
            k=max_docs * len(knowledge_collections),
            min_score=min_relevance,
            allowed_collection_ids=set([c["id"] for c in knowledge_collections]),
            rag_context=rag_context,
            repository_type="knowledge",  # Only knowledge, not planner
            max_chunks_per_doc=max_chunks_per_doc,
            freshness_weight=freshness_weight,
            freshness_decay_rate=freshness_decay_rate,
            max_sensitivity=knowledge_config.get("maxSensitivity"),  # Wave 5 B2 ceiling
            require_signed_knowledge=bool(knowledge_config.get("requireSignedKnowledge")),  # K5
        )

        # --- EPC: Record RAG search and results ---
        try:
            if hasattr(executor, 'provenance') and executor.provenance:
                import json as _json
                _coll_ids = [c.get("id", "") for c in knowledge_collections]
                executor.provenance.add_step("rag_search", _json.dumps(_coll_ids), f"Searching {len(knowledge_collections)} collection(s)")
                _doc_ids = [r.get("document_id", "") for r in all_results[:50]]
                _scores = [round(r.get("similarity_score", 0), 4) for r in all_results[:50]]
                executor.provenance.add_step("rag_results", _json.dumps({"doc_ids": _doc_ids, "scores": _scores}), f"Retrieved {len(all_results)} chunks")
        except Exception as _epc_err:
            app_logger.debug(f"EPC rag_focused search/results: {_epc_err}")

        app_logger.info(f"[RAG] Retrieved {len(all_results)} chunks from {len(knowledge_collections)} collection(s)")
        for idx, r in enumerate(all_results[:5]):
            sim = r.get('similarity_score', 0)
            adj = r.get('adjusted_score', sim)
            fresh = r.get('freshness_score', 'N/A')
            title = r.get('metadata', {}).get('title', 'unknown')[:50]
            doc_id = r.get('document_id', 'N/A')
            fresh_str = f" fresh={fresh:.3f}" if isinstance(fresh, (int, float)) else ""
            app_logger.info(f"[RAG]   #{idx+1}: adj={adj:.4f} sim={sim:.4f}{fresh_str} title={title} doc={doc_id}")

        # The turn's own attachments are material too: with nothing in the repositories, a
        # question about the attached document is still answered — from the document.
        _attached_texts, _attached_unreadable = executor.attached_material()
        answer_from_attachments = not all_results and bool(_attached_texts or _attached_unreadable)

        if not all_results and not answer_from_attachments:
            # NO KNOWLEDGE FOUND - Treat as valid response, not error
            retrieval_duration_ms = int((time.time() - retrieval_start_time) * 1000)
            collection_names = set([c.get("name", "Unknown") for c in knowledge_collections])

            no_results_message = """<div class="no-knowledge-found">
    <p><strong>No relevant knowledge found</strong> for your query.</p>
    <p class="text-gray-400 text-sm mt-2">Try rephrasing your question or check your knowledge repositories.</p>
</div>"""
            no_results_text = "No relevant knowledge found. Try rephrasing or check your knowledge repositories."
            # PROP-008 — the subject may be missing from the MODEL too: the platform's note + act
            no_results_text, no_results_message = executor._apply_model_gap(no_results_text, no_results_message)

            # Emit knowledge retrieval complete event (with 0 documents)
            retrieval_complete_payload = {
                "collection_names": list(collection_names),
                "document_count": 0,
                "duration_ms": retrieval_duration_ms,
                "session_id": executor.session_id,
                "search_modes": search_modes,
            }
            knowledge_events.append({"type": "knowledge_retrieval_complete", "payload": retrieval_complete_payload})
            yield executor._format_sse_with_depth({
                "type": "knowledge_retrieval_complete",
                "payload": retrieval_complete_payload
            }, event="notification")

            # Save to conversation history
            await session_manager.add_message_to_histories(
                executor.user_uuid, executor.session_id, 'assistant',
                content=no_results_text,
                html_content=no_results_message,
                is_session_primer=executor.is_session_primer
            )

            # Update models_used for session tracking
            profile_tag = executor._get_current_profile_tag()
            await session_manager.update_models_used(
                executor.user_uuid,
                executor.session_id,
                *_renv.served_model(executor.current_provider, executor.current_model),   # S4: the model that SERVED
                profile_tag
            )

            # Get session data for token totals
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
            session_input_tokens = session_data.get("input_tokens", 0) if session_data else 0
            session_output_tokens = session_data.get("output_tokens", 0) if session_data else 0

            # System events (for session name generation on first turn)
            system_events = []

            # Generate session name for first turn
            if executor.current_turn_number == 1:
                if session_data and session_data.get("name") == "New Chat":
                    async for result in executor._generate_and_emit_session_name():
                        if isinstance(result, str):
                            yield result
                        else:
                            new_name, name_input_tokens, name_output_tokens, name_events = result
                            system_events.extend(name_events)

                            # Add session name tokens to turn totals and session totals
                            if name_input_tokens > 0 or name_output_tokens > 0:
                                executor.turn_input_tokens += name_input_tokens
                                executor.turn_output_tokens += name_output_tokens
                                await session_manager.update_token_count(
                                    executor.user_uuid, executor.session_id, name_input_tokens, name_output_tokens
                                )
                                # Emit token_update event so UI reflects updated session totals
                                updated_session = await session_manager.get_session(executor.user_uuid, executor.session_id)
                                session_data = updated_session or session_data   # the freshest copy from here on
                                if updated_session:
                                    from trusted_data_agent.core.cost_manager import get_cost_manager as _CM
                                    _name_cost = _CM().calculate_cost(
                                        provider=executor.current_provider or "Unknown",
                                        model=executor.current_model or "Unknown",
                                        input_tokens=name_input_tokens,
                                        output_tokens=name_output_tokens
                                    )
                                    # §3.2-D: fold session-name cost into the accumulator when the
                                    # main synthesis accumulated (guard covers the tools path where
                                    # the accumulator is empty and _resolve falls back — see executor.py).
                                    if getattr(executor, "turn_cost_usd", 0.0) > 0:
                                        executor.turn_cost_usd += (_name_cost or 0.0)
                                    yield executor._format_sse_with_depth({
                                        "statement_input": name_input_tokens,
                                        "statement_output": name_output_tokens,
                                        "turn_input": executor.turn_input_tokens,
                                        "turn_output": executor.turn_output_tokens,
                                        "total_input": updated_session.get("input_tokens", 0),
                                        "total_output": updated_session.get("output_tokens", 0),
                                        "call_id": "session_name_generation",
                                        "cost_usd": _name_cost
                                    }, "token_update")
                                # Update turn token counts in workflow_history for reload
                                await session_manager.update_turn_token_counts(
                                    executor.user_uuid, executor.session_id, executor.current_turn_number,
                                    executor.turn_input_tokens, executor.turn_output_tokens
                                )

                            if new_name != "New Chat":
                                try:
                                    await session_manager.update_session_name(executor.user_uuid, executor.session_id, new_name)
                                    yield executor._format_sse_with_depth({
                                        "session_id": executor.session_id,
                                        "newName": new_name
                                    }, "session_name_update")
                                except Exception as name_e:
                                    app_logger.error(f"Failed to save session name: {name_e}")

            # Emit token_update event after session name generation (if any tokens were consumed)
            # This ensures the UI status window shows updated session totals
            if executor.turn_input_tokens > 0 or executor.turn_output_tokens > 0:
                # `session_data` is the copy read after the naming block's token update (or the
                # one read after the no-results message) — nothing has been written since.
                if session_data:
                    yield executor._format_sse_with_depth({
                        "statement_input": 0,  # No new statement tokens in "no results" path
                        "statement_output": 0,
                        "turn_input": executor.turn_input_tokens,
                        "turn_output": executor.turn_output_tokens,
                        "total_input": session_data.get("input_tokens", 0),
                        "total_output": session_data.get("output_tokens", 0),
                        "call_id": "rag_no_results"
                    }, "token_update")

            # Store execution_complete in knowledge_events for reload (BEFORE turn_summary)
            # No LLM synthesis for no-results case, so synthesis_duration_ms is 0
            # §3.3: turn cost = Σ per-call costs (dual-model aware).
            _turn_cost = executor._resolve_turn_cost_usd()
            execution_complete_payload = {
                "profile_type": "rag_focused",
                "profile_tag": profile_tag,
                "collections_searched": len(collection_names),
                "documents_retrieved": 0,
                "no_knowledge_found": True,
                "total_input_tokens": executor.turn_input_tokens,
                "total_output_tokens": executor.turn_output_tokens,
                "retrieval_duration_ms": retrieval_duration_ms,
                "synthesis_duration_ms": 0,
                "total_duration_ms": retrieval_duration_ms,  # Only retrieval, no synthesis
                "cost_usd": _turn_cost,
                "success": True
            }
            knowledge_events.append({
                "type": "execution_complete",
                "payload": execution_complete_payload
            })

            # Calculate session cost (cumulative up to and including this turn)
            session_cost_usd = 0.0
            try:
                previous_session_cost = executor._calculate_session_cost_at_turn(session_data)
                session_cost_usd = previous_session_cost + _turn_cost
                app_logger.debug(f"[rag_focused-no-results] Session cost at turn {executor.current_turn_number}: ${session_cost_usd:.6f}")
            except Exception as e:
                app_logger.warning(f"Failed to calculate session cost: {e}", exc_info=True)

            # The trust tail runs on this answer too (trust campaign C19, 2026-10-29: Focus · S4 —
            # a question the corpus cannot answer took this early return, and the delivered
            # answer carried no provenance seal, no integrity stamp at answer time and no outbound
            # screen: I7/I8 failed and GUARDED read `na`). Same calls as the normal tail below:
            # the outbound guard (deterministic, 0 tokens), the per-turn assurance stamps (the
            # integrity fact lives there), the sealed chain.
            no_results_text, _nr_guardrail_event = executor._run_guardrail_outbound(no_results_text)
            _nr_provenance = None
            try:
                if hasattr(executor, 'provenance') and executor.provenance:
                    executor.provenance.add_step("turn_complete", no_results_text,
                                                 f"rag_focused turn {executor.current_turn_number} complete (no knowledge found)")
                    _nr_provenance = executor.provenance.finalize()
            except Exception as _epc_err:  # noqa: BLE001
                app_logger.debug(f"EPC rag_focused no-results finalize: {_epc_err}")

            # Build turn summary for workflow_history
            turn_summary = {
                "turn": executor.current_turn_number,
                "user_query": executor.original_user_input,
                "final_summary_text": no_results_text,
                "status": "success",  # NOT "error"
                "is_session_primer": executor.is_session_primer,  # Flag for RAG case filtering
                "no_knowledge_found": True,  # Flag for UI indication
                "execution_trace": [],
                "raw_llm_plan": None,
                "original_plan": None,
                "duration_ms": executor._turn_duration_ms(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": executor.current_provider,
                "model": executor.current_model,
                "profile_tag": profile_tag,
                "profile_type": "rag_focused",
                "task_id": executor.task_id if hasattr(executor, 'task_id') else None,
                "turn_input_tokens": executor.turn_input_tokens,
                "turn_output_tokens": executor.turn_output_tokens,
                "turn_cost": _turn_cost,
                "session_cost_usd": session_cost_usd,
                "session_id": executor.session_id,
                "session_input_tokens": session_input_tokens,
                "session_output_tokens": session_output_tokens,
                "knowledge_retrieval_event": {
                    "enabled": True,
                    "retrieved": False,
                    "document_count": 0,
                    "collections": list(collection_names),
                    "duration_ms": retrieval_duration_ms,
                    "summary": f"Searched {len(collection_names)} collection(s), no relevant documents found"
                },
                "knowledge_events": knowledge_events,
                "system_events": system_events,
                "context_window_snapshot_event": getattr(executor, 'context_window_snapshot_event', None),
                "skills_applied": executor.skill_result.to_applied_list() if executor.skill_result and executor.skill_result.has_content else [],
                "guardrail": _nr_guardrail_event,
                "guardrail_inbound": getattr(executor, "_inbound_guardrail_event", None),
                **_assurance_stamps(),
            }
            if _nr_provenance:
                turn_summary.update(_nr_provenance)

            # Save turn data to workflow_history
            await session_manager.update_last_turn_data(executor.user_uuid, executor.session_id, turn_summary)
            app_logger.debug(f"Saved rag_focused (no results) turn data for turn {executor.current_turn_number}")

            # Send session update notification
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
            if session_data:
                yield executor._format_sse_with_depth({
                    "type": "session_model_update",
                    "payload": {
                        "session_id": executor.session_id,
                        "models_used": session_data.get("models_used", []),
                        "profile_tags_used": session_data.get("profile_tags_used", []),
                        "last_updated": session_data.get("last_updated"),
                        "provider": executor.current_provider,
                        "model": executor.current_model,
                        "name": session_data.get("name", "Unnamed Session"),
                    }
                }, event="notification")

            # Emit final_answer (NOT error) with turn_id for badge rendering
            yield executor._format_sse_with_depth({
                "step": "Finished",
                "final_answer": no_results_message,
                "final_answer_text": no_results_text,
                "turn_id": executor.current_turn_number,
                "session_id": executor.session_id,
                "no_knowledge_found": True,
                "is_session_primer": executor.is_session_primer
            }, "final_answer")

            # Emit lifecycle event (already stored in knowledge_events above for reload)
            try:
                complete_event = executor._emit_lifecycle_event("execution_complete", execution_complete_payload)
                yield complete_event
            except Exception as e:
                app_logger.warning(f"Failed to emit execution_complete event: {e}")

            app_logger.info("✅ RAG-focused execution completed (no knowledge found)")
            return

        # Apply reranking if configured (reuse existing code from llm_only)
        reranked_results = all_results
        for coll_config in knowledge_collections:
            if coll_config.get("reranking", False):
                coll_results = [r for r in all_results
                              if r.get("metadata", {}).get("collection_id") == coll_config["id"]]
                if coll_results and executor.llm_handler:
                    # Get actual collection name from metadata
                    coll_id = coll_config.get("id")
                    coll_name = "Unknown"
                    if coll_id and executor.rag_retriever:
                        coll_meta = executor.rag_retriever.get_collection_metadata(coll_id)
                        if coll_meta:
                            coll_name = coll_meta.get("name", "Unknown")
                        else:
                            coll_name = coll_config.get("name", "Unknown")
                    else:
                        coll_name = coll_config.get("name", "Unknown")

                    # Emit reranking start event
                    rerank_start_payload = {
                        "collection": coll_name,
                        "document_count": len(coll_results),
                        "session_id": executor.session_id
                    }
                    knowledge_events.append({"type": "knowledge_reranking_start", "payload": rerank_start_payload})
                    yield executor._format_sse_with_depth({
                        "type": "knowledge_reranking_start",
                        "payload": rerank_start_payload
                    }, event="notification")

                    # §4: route the reranking call's cost into knowledge_events (persisted + summed).
                    reranked = await executor._rerank_knowledge_with_llm(
                        query=executor.original_user_input,
                        documents=coll_results,
                        max_docs=max_docs,
                        _events=knowledge_events,
                    )

                    # Emit reranking complete event
                    rerank_complete_payload = {
                        "collection": coll_name,
                        "reranked_count": len(reranked),
                        "session_id": executor.session_id
                    }
                    knowledge_events.append({"type": "knowledge_reranking_complete", "payload": rerank_complete_payload})
                    yield executor._format_sse_with_depth({
                        "type": "knowledge_reranking_complete",
                        "payload": rerank_complete_payload
                    }, event="notification")

                    reranked_results = [r for r in reranked_results
                                      if r.get("metadata", {}).get("collection_id") != coll_config["id"]]
                    reranked_results.extend(reranked)

        # Limit total documents
        final_results = reranked_results[:max_docs]

        # Enrich documents with collection_name
        for doc in final_results:
            if not doc.get("collection_name"):
                coll_id = doc.get("collection_id")
                if coll_id and executor.rag_retriever:
                    coll_meta = executor.rag_retriever.get_collection_metadata(coll_id)
                    if coll_meta:
                        doc["collection_name"] = coll_meta.get("name", "Unknown")

        # Format knowledge context for LLM
        knowledge_context = executor._format_knowledge_for_prompt(final_results, max_tokens)
        if answer_from_attachments:
            # said to the model in so many words, so the answer cannot pass the attachment off
            # as the knowledge base (and says the repositories held nothing on it)
            knowledge_context = ("KNOWLEDGE CONTEXT: none of this agent's knowledge repositories held "
                                 "anything on this question. Answer from the uploaded documents, cite them "
                                 "by file name, and do not present their content as coming from the "
                                 "knowledge repositories.")
            app_logger.info("[RAG] No repository match — answering from the turn's attachments")

        # Build detailed event for Live Status panel (matching llm_only format)
        knowledge_chunks = []
        collection_names = set()
        for doc in final_results:
            collection_name = doc.get("collection_name", "Unknown")
            collection_names.add(collection_name)
            doc_metadata = doc.get("metadata", {})

            # Try title first (user-friendly name), then filename
            source_name = doc_metadata.get("title") or doc_metadata.get("filename")

            # If no title or filename, check if this is an imported collection
            if not source_name:
                if "(Imported)" in collection_name or doc_metadata.get("source") == "import":
                    source_name = "No Document Source (Imported)"
                else:
                    source_name = "Unknown Source"

            knowledge_chunks.append({
                "source": source_name,
                # the repository travels WITH the chunk: it is part of the citation line the
                # model is shown, so the grounding gate counts it as support (one definition:
                # eval.types.chunk_support_text)
                "collection_name": collection_name,
                "content": doc.get("content", ""),
                "similarity_score": doc.get("similarity_score", 0),
                "document_id": doc.get("document_id"),
                "collection_id": doc.get("collection_id"),
                "chunk_index": doc.get("chunk_index", 0)
            })

        # RAG Trust Hardening Wave 4 (P1/K3): stamp source provenance — corpus trust
        # state, staleness, sensitivity — onto the chunks (fail-open, read-side only).
        try:
            from trusted_data_agent.core.knowledge_provenance import enrich_chunks
            knowledge_chunks = enrich_chunks(knowledge_chunks)
        except Exception:
            pass

        # Calculate retrieval duration
        retrieval_duration_ms = int((time.time() - retrieval_start_time) * 1000)

        # Emit completion event for Live Status panel (replaces old single event)
        # Include chunks for live status window display
        retrieval_summary = (
            f"No relevant documents in {len(knowledge_collections)} knowledge repositor"
            f"{'y' if len(knowledge_collections) == 1 else 'ies'} — answering from the attached document(s)"
            if answer_from_attachments else
            f"Retrieved {len(final_results)} relevant document(s) from {len(collection_names)} knowledge collection(s)")
        if answer_from_attachments:
            collection_names = set(collection_names_for_start)
        event_details = {
            "summary": retrieval_summary,
            "collections": list(collection_names),
            "document_count": len(final_results),
            "duration_ms": retrieval_duration_ms,
            "chunks": knowledge_chunks,  # Include full chunks for UI display
            "search_modes": search_modes,
            "answered_from_attachments": answer_from_attachments,
        }

        knowledge_events.append({"type": "knowledge_retrieval_complete", "payload": event_details})
        yield executor._format_sse_with_depth({
            "type": "knowledge_retrieval_complete",
            "payload": event_details
        }, event="notification")

        # --- LLM Synthesis ---
        from trusted_data_agent.agent.prompt_loader import get_prompt_loader
        prompt_loader = get_prompt_loader()
        system_prompt = prompt_loader.get_prompt("RAG_FOCUSED_EXECUTION")

        # Use fallback if prompt not found, empty, or is a placeholder (decryption failed)
        if not system_prompt or "[ENCRYPTED CONTENT]" in system_prompt:
            system_prompt = "You are a knowledge base assistant. Answer using only the provided documents."
            app_logger.warning("RAG_FOCUSED_EXECUTION prompt not available (decryption failed or not found), using fallback")

        # Apply synthesis prompt override from profile/global settings (if configured)
        if synthesis_prompt_override and synthesis_prompt_override.strip():
            system_prompt = synthesis_prompt_override.strip()
            app_logger.info(f"[RAG] Using synthesis prompt override ({len(system_prompt)} chars)")

        # --- Inject component instructions (use pre-computed from CW module if available) ---
        comp_section = getattr(executor, '_cw_component_instructions', None)
        if comp_section is None:
            from trusted_data_agent.components.manager import get_component_instructions_for_prompt
            comp_section = get_component_instructions_for_prompt(
                executor.active_profile_id, executor.user_uuid, session_data
            )
        system_prompt = system_prompt.replace('{component_instructions_section}', comp_section)
        # PROP-011 — the model's act is taught only while it may be taken (the component in use)
        try:
            from trusted_data_agent.agent import input_requests as _ir_carrier
            system_prompt = system_prompt.replace('{needs_input_carrier}', _ir_carrier.carrier_section('block_focus'))
        except Exception:  # noqa: BLE001
            system_prompt = system_prompt.replace('{needs_input_carrier}', '')

        # --- Inject skill content (pre-processing) ---
        if executor.skill_result and executor.skill_result.has_content:
            sp_block = executor.skill_result.get_system_prompt_block()
            if sp_block:
                system_prompt = f"{system_prompt}\n\n{sp_block}"

        # --- KG enrichment (all profile types) ---
        kg_enrichment_text = await executor._get_kg_enrichment()
        if kg_enrichment_text:
            knowledge_context = (knowledge_context or "") + "\n\n" + kg_enrichment_text
            yield executor._format_sse_with_depth({
                "step": "Knowledge Graph Enrichment",
                "type": "kg_enrichment",
                "details": executor.kg_enrichment_event
            })
        # Agent Memory + Findings — the user_memory block (who the user is) and the verified
        # results of earlier turns (the `findings` module), re-read not recalled; the findings
        # text is the same the grounding gate takes as vouched evidence.
        _findings_text = executor._session_context_blocks()
        if _findings_text:
            knowledge_context = ((knowledge_context + "\n\n") if knowledge_context else "") + _findings_text

        # Load document context from uploaded files
        rag_doc_context = None
        if executor.attachments:
            rag_doc_context, doc_trunc_events = executor.documents_for_turn()
            for evt in doc_trunc_events:
                event_data = {"step": "Context Optimization", "type": "context_optimization", "details": evt}
                executor._log_system_event(event_data)
                yield executor._format_sse_with_depth(event_data)

        user_message = await executor._build_user_message_for_rag_synthesis(
            knowledge_context=knowledge_context,
            document_context=rag_doc_context
        )

        # Inject user_context skill content into user message
        if executor.skill_result and executor.skill_result.has_content:
            uc_block = executor.skill_result.get_user_context_block()
            if uc_block:
                user_message = f"{uc_block}\n\n{user_message}"

        # --- EPC: Record synthesis step ---
        try:
            if hasattr(executor, 'provenance') and executor.provenance:
                executor.provenance.add_step("rag_synthesis", user_message[:4096], f"Synthesis with {len(final_results)} documents")
        except Exception as _epc_err:
            app_logger.debug(f"EPC rag_synthesis: {_epc_err}")

        # Emit "Calling LLM" event with call_id for token tracking
        call_id = str(uuid.uuid4())
        llm_start_time = time.time()

        yield executor._format_sse_with_depth({
            "step": "Calling LLM for Knowledge Synthesis",
            "type": "system_message",
            "details": {
                "summary": "Synthesizing answer from retrieved knowledge",
                "call_id": call_id,
                "document_count": len(final_results),
                "collections": list(collection_names)
            }
        })

        # Set LLM busy indicator
        yield executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

        # Check for active component tools — auto-upgrade synthesis to agent mode
        from trusted_data_agent.components.manager import get_component_langchain_tools as _get_rag_comp_tools
        rag_component_tools = _get_rag_comp_tools(
            executor.active_profile_id, executor.user_uuid, session_id=executor.session_id,
            llm_instance=(executor.profile_llm_instance
                          or (executor.dependencies.get("STATE") or {}).get("llm")))

        rag_component_payloads = []  # Component payloads extracted from agent result (if any)
        _react_events = []  # ReAct tool events, when component-tool synthesis runs (F-001)
        used_agent_synthesis = False  # Track if ConversationAgentExecutor handled synthesis
        _synthesis_error = None       # a provider failure during synthesis: the turn is failed

        if rag_component_tools:
            # --- RAG + Component Tools: Agent-based synthesis ---
            # Use ConversationAgentExecutor for synthesis with component tools
            # (e.g., LLM can call TDA_Charting to visualize retrieved knowledge)
            app_logger.info(f"[RAG] Component tools active ({len(rag_component_tools)}) — agent-based synthesis")
            from trusted_data_agent.llm.langchain_adapter import create_langchain_llm
            from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor

            llm_config_id = profile_config.get("llmConfigurationId")
            rag_llm_instance = create_langchain_llm(llm_config_id, executor.user_uuid, thinking_budget=executor.thinking_budget)

            # ONE rule for the history every agent receives (agent/agent_history.py). This path had
            # its own copy that kept only `user`/`assistant` roles — every stored answer (`model`)
            # was dropped, so the previous question read as still open and was answered again —
            # and removed every earlier message equal to the current question (prod 36c22ee3).
            # Built from the session read at turn start: nothing has written to it since.
            from trusted_data_agent.agent import agent_history as _ah
            rag_conv_history = (_ah.build(session_data.get("chat_object", []), executor.original_user_input,
                                          getattr(executor, "_cw_history_window", 10),
                                          disabled=bool(getattr(executor, "disabled_history", False)))
                                if session_data else [])

            rag_agent = ConversationAgentExecutor(
                profile=profile_config,
                user_uuid=executor.user_uuid,
                session_id=executor.session_id,
                llm_instance=rag_llm_instance,
                mcp_tools=rag_component_tools,
                async_event_handler=executor.event_handler,
                max_iterations=3,
                conversation_history=rag_conv_history,
                knowledge_context=knowledge_context,
                document_context=rag_doc_context,
                multimodal_content=executor.multimodal_content,
                turn_number=executor.current_turn_number,
                provider=executor.current_provider if hasattr(executor, 'current_provider') else None,
                model=executor.current_model if hasattr(executor, 'current_model') else None,
                component_instructions=getattr(executor, '_cw_component_instructions', None),
                profile_type="rag_focused",
                answer_stream=executor.answer_stream,  # S1: the tools-path synthesis streams too
                cost_sink=executor._accumulate_call_cost,  # S2: cache-priced calls → the turn accumulator
            )

            agent_result = await rag_agent.execute(executor.original_user_input)
            response_text = agent_result.get("response", "")
            if agent_result.get("success") is False:
                # The executor already answered with the honest sentence (turn_failure); the
                # TURN is failed too — Focus recorded it as a success (trust campaign F1/F2).
                _synthesis_error = str(agent_result.get("error") or "")
            input_tokens = agent_result.get("input_tokens", 0)
            output_tokens = agent_result.get("output_tokens", 0)

            # ConversationAgentExecutor uses LangChain directly (not llm_handler), so the
            # engine owns the TURN accumulator for the agent's tokens (the agent object never
            # touches executor.turn_*). SESSION persistence is deliberately NOT done here:
            # the agent already persists each LangChain step's tokens to the session counter
            # exactly once, unconditionally (conversation_agent.py). Re-persisting here
            # double-counted the session + quota totals for rag_focused turns — matches the
            # ConversationEngine pattern, which likewise does not re-persist agent tokens.
            executor.turn_input_tokens += input_tokens
            executor.turn_output_tokens += output_tokens

            # Store agent events for plan reload
            for evt in rag_agent.collected_events:
                knowledge_events.append(evt)
            # F-001: keep the ReAct event list reachable at the honesty seam far below.
            # `rag_agent` is bound only inside this branch, so reading it there would
            # NameError on the no-component-tools path.
            _react_events = list(rag_agent.collected_events)

            # Extract component payloads for HTML generation (chart, code, etc.)
            rag_component_payloads = agent_result.get("component_payloads", [])
            used_agent_synthesis = True
        else:
            # --- Standard RAG: Direct LLM synthesis (no tools) ---
            try:
                response_text, input_tokens, output_tokens = await executor._call_llm_and_update_tokens(
                    prompt=user_message,
                    reason="RAG Focused Synthesis",
                    system_prompt_override=system_prompt,
                    multimodal_content=executor.multimodal_content,
                    on_delta=executor.answer_stream,   # S1: the synthesis IS the answer
                    # The provider's failure must be SEEN: with the default the handler returns an
                    # empty answer, which Focus delivered as a successful turn.
                    raise_on_error=True,
                )
            except Exception as _syn_err:
                from trusted_data_agent.agent import turn_failure as _tf   # C18: never the raw error
                from trusted_data_agent.core.utils import unwrap_exception as _unwrap
                app_logger.error(f"RAG synthesis failed: {_syn_err}", exc_info=True)
                response_text, input_tokens, output_tokens = _tf.answer(_unwrap(_syn_err)), 0, 0
                _synthesis_error = str(_syn_err)

        # --- EPC: Record LLM response ---
        try:
            if hasattr(executor, 'provenance') and executor.provenance:
                executor.provenance.add_step("llm_response", response_text[:4096] if response_text else "", f"Synthesis response ({len(response_text) if response_text else 0} chars)")
        except Exception as _epc_err:
            app_logger.debug(f"EPC rag llm_response: {_epc_err}")

        # Calculate LLM call duration
        llm_duration_ms = int((time.time() - llm_start_time) * 1000)

        # Set LLM idle indicator
        yield executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

        # Explicitly update session token counts and emit token_update event
        # This ensures the status window shows correct session totals during live execution
        # Calculate cost for RAG LLM synthesis call (before token_update so it's included)
        from trusted_data_agent.core.cost_manager import get_cost_manager
        cost_manager = get_cost_manager()
        call_cost = cost_manager.calculate_cost(
            provider=executor.current_provider if hasattr(executor, 'current_provider') else "Unknown",
            model=executor.current_model if hasattr(executor, 'current_model') else "Unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens
        )

        if input_tokens > 0 or output_tokens > 0:
            # Re-fetch session to ensure token counts are current
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)

            if session_data:
                yield executor._format_sse_with_depth({
                    "statement_input": input_tokens,
                    "statement_output": output_tokens,
                    "turn_input": executor.turn_input_tokens,
                    "turn_output": executor.turn_output_tokens,
                    "total_input": session_data.get("input_tokens", 0),
                    "total_output": session_data.get("output_tokens", 0),
                    "call_id": call_id,
                    "cost_usd": call_cost
                }, "token_update")

        # Emit RAG LLM step and synthesis result events only for direct LLM path.
        # When ConversationAgentExecutor handled synthesis, it already emitted
        # conversation_llm_step and conversation_llm_complete events — skip to avoid duplicates.
        if not used_agent_synthesis:
            rag_llm_step_payload = {
                "step_name": "Knowledge Synthesis",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": llm_duration_ms,
                "model": f"{executor.current_provider}/{executor.current_model}" if hasattr(executor, 'current_provider') and hasattr(executor, 'current_model') else "Unknown",
                "session_id": executor.session_id,
                # Same call_id as the paired token_update above so the frontend's per-call
                # cost ledger counts this synthesis call exactly once (not step + token_update).
                "call_id": call_id,
                "cost_usd": call_cost
            }
            knowledge_events.append({"type": "rag_llm_step", "payload": rag_llm_step_payload})
            yield executor._format_sse_with_depth({
                "type": "rag_llm_step",
                "payload": rag_llm_step_payload
            }, event="notification")

            synthesis_result_data = {
                "status": "success",
                "metadata": {
                    "tool_name": "LLM_Synthesis",
                    "call_id": call_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "model": f"{executor.current_provider}/{executor.current_model}" if hasattr(executor, 'current_provider') and hasattr(executor, 'current_model') else "Unknown"
                },
                "results": [{
                    "response": response_text[:500] + "..." if len(response_text) > 500 else response_text,
                    "full_length": len(response_text)
                }]
            }
            yield executor._format_sse_with_depth({
                "step": "LLM Synthesis Results",
                "details": synthesis_result_data,
                "tool_name": "LLM_Synthesis"
            }, "tool_result")

            knowledge_events.append({
                "type": "tool_result",
                "payload": {
                    "step": "LLM Synthesis Results",
                    "details": synthesis_result_data,
                    "tool_name": "LLM_Synthesis"
                }
            })

        # Calculate total knowledge search time (retrieval + synthesis)
        total_knowledge_search_time_ms = int((time.time() - retrieval_start_time) * 1000)

        # Emit Knowledge Search Complete summary event (similar to Tools Complete)
        knowledge_search_complete_payload = {
            "status": "complete",
            "collections_searched": len(collection_names),
            "collection_names": list(collection_names),
            "documents_retrieved": len(final_results),
            "total_time_ms": total_knowledge_search_time_ms,
            "retrieval_time_ms": retrieval_duration_ms,
            "synthesis_time_ms": llm_duration_ms,
            "synthesis_tokens_in": input_tokens,
            "synthesis_tokens_out": output_tokens,
            "session_id": executor.session_id
        }
        # NOTE: Don't store knowledge_search_complete in knowledge_events - it's redundant with execution_complete
        # which already contains the same KPIs. Only emit for live streaming.
        yield executor._format_sse_with_depth({
            "type": "knowledge_search_complete",
            "payload": knowledge_search_complete_payload
        }, event="notification")

        # PROP-011 (phase 2): Focus asks when the question is ambiguous BETWEEN documents — the
        # ` ```uderia-ask ``` ` block in the synthesis, raised into the ledger; the prose stays.
        try:
            from trusted_data_agent.agent import input_requests as _ir_mod
            response_text, _ask = _ir_mod.extract_ask_block(response_text)
            if _ask is not None and _ir_mod.pending() is None:
                _ir_mod.raise_from_act(_ask, source="answer")
        except Exception as _ae:  # noqa: BLE001
            app_logger.debug(f"needs-input: ask block skipped: {_ae}")

        # --- Format Response with Sources ---
        formatter = OutputFormatter(
            llm_response_text=response_text,
            collected_data=executor.structured_collected_data,
            rag_focused_sources=final_results  # Pass sources
        )
        final_html, tts_payload = formatter.render()

        # Append component rendering HTML (chart, code, audio, video, etc.)
        from trusted_data_agent.components.utils import generate_component_html
        final_html += generate_component_html(rag_component_payloads)

        # Tier 1.1 grounding gate (charter #14: all engines). Focus grounds the answer's
        # numbers against the retrieved documents; it acts only when the profile also
        # declares a KG scope, otherwise abstains cleanly. Default observe → unchanged (#15).
        response_text, _grounding_event = await executor._run_grounding_gate_full(
            response_text,
            trace_extra={"knowledge_retrieval_event": {"chunks": knowledge_chunks}})
        if (_grounding_event or {}).get("decision"):
            from trusted_data_agent.agent.grounding import adapter as _grounding_adapter
            if _grounding_event.get("outcome_delivered") == "revision":
                # S7 review: a delivered revision is rendered from the delivered text (+ sources)
                final_html, tts_payload = OutputFormatter(
                    llm_response_text=response_text, collected_data=executor.structured_collected_data,
                    rag_focused_sources=final_results).render()
                final_html += generate_component_html(rag_component_payloads)
            final_html = _grounding_adapter.apply_to_html(final_html, _grounding_event)
            yield executor._format_sse_with_depth({
                "step": "Grounding Gate", "type": "grounding_gate", "details": _grounding_event})

        # Tier 1.3 outbound guardrail (charter #14: all engines). PII redaction + toxicity
        # block; default observe → unchanged (#15). Deterministic, 0-token, fails open.
        response_text, _guardrail_event = executor._run_guardrail_outbound(response_text)
        if (_guardrail_event or {}).get("acted"):
            from trusted_data_agent.agent.guardrails import adapter as _guardrail_adapter
            final_html = _guardrail_adapter.apply_event_to_html(final_html, response_text, _guardrail_event)
            yield executor._format_sse_with_depth({
                "step": "Guardrail", "type": "guardrail", "details": _guardrail_event})

        # RAG Trust Hardening Wave 1 §1.3 — retrieval-sufficiency honesty. Focus's
        # zero-results path already answers honestly upstream, so here only the opt-in
        # sufficiencyThreshold case can fire (results passed the floor but the best is
        # still weak + the answer asserts specifics). Default observe → unchanged (#15).
        _honesty_event = None
        try:
            _top = max((r.get("similarity_score", 0) or 0) for r in final_results) \
                if final_results else 0.0
            response_text, _honesty_event = executor._run_honesty_gate_rag(
                response_text,
                # `scores` carries the RAW similarities so the assurance assessor can
                # report both statistics from one assessment (symmetry map A7): the gate's
                # question is answered by `top`, the measurement's by the mean, and the
                # mean is not recoverable from a precomputed pair. Dropping them here was
                # an information loss in the pipeline, not merely a duplication.
                {"searched": True, "qualifying": len(final_results), "top_score": _top,
                 "scores": [float(r.get("similarity_score", 0) or 0)
                            for r in final_results]})
            if (_honesty_event or {}).get("acted"):
                # HTML changes only for an enforce replacement or an appended disclosure
                # (the Optimize seam's rule); an observe flag is a card, never a banner.
                if (_honesty_event.get("answer_contained")
                        or _honesty_event.get("disclosure_appended")):
                    from trusted_data_agent.agent.honesty import adapter as _honesty_adapter
                    final_html = _honesty_adapter.apply_to_html(
                        final_html, _honesty_event, response_text)
                yield executor._format_sse_with_depth({
                    "step": "Honesty Gate", "type": "honesty", "details": _honesty_event})
        except Exception:
            pass  # honesty must never break a turn

        # F-001 — cited-but-unread. Focus reaches a library through its component-tool
        # synthesis loop, so it needs its own call: the RAG honesty seam above carries no
        # tool calls. Abstains byte-identically without a progressive library (#15).
        _citation_event = None
        try:
            from trusted_data_agent.agent import obda as _obda_cit
            response_text, _citation_event = executor._run_citation_gate(
                response_text,
                _obda_cit.calls_from_conversation_events(_react_events),
                _react_events)
            if (_citation_event or {}).get("acted"):
                if _citation_event.get("disclosure_appended"):
                    from trusted_data_agent.agent.honesty import adapter as _cit_adapter
                    final_html = _cit_adapter.apply_to_html(
                        final_html, _citation_event, response_text)
                yield executor._format_sse_with_depth({
                    "step": "Citation Check", "type": "honesty", "details": _citation_event})
        except Exception:
            pass  # the citation check must never break a turn

        # Wave 5 (B2): sensitivity annotation — the answer drew on a confidential repo.
        try:
            from trusted_data_agent.core.knowledge_provenance import sensitivity_note_html
            _sens_note = sensitivity_note_html(knowledge_chunks)
            if _sens_note:
                from trusted_data_agent.agent import answer_surface as _ans
                final_html = _ans.place_note(final_html, _sens_note)
        except Exception:
            pass

        # PROP-008 — a question whose subject is not in the model: the platform's note + act
        response_text, final_html = executor._apply_model_gap(response_text, final_html)

        # Emit final answer
        yield executor._format_sse_with_depth({
            "step": "Finished",
            "final_answer": final_html,
            "final_answer_text": response_text,  # Clean text for parent genie coordinators
            "turn_id": executor.current_turn_number,  # Include turn_id for frontend badge rendering
            "session_id": executor.session_id,  # Include session_id for filtering when switching sessions
            "tts_payload": tts_payload,
            "source": executor.source,
            "knowledge_sources": [{"collection_id": r.get("collection_id"),
                                   "similarity_score": r.get("similarity_score")}
                                  for r in final_results],
            "is_session_primer": executor.is_session_primer,
            # S1.1 — the turn's duration on the SAME basis the turn persists (one clock, two moments)
            "duration_ms": executor._turn_duration_ms(),
            # S1.1 — the checks that ran, as the reload will read them (one verdict, two moments)
            "checks": _answer_checks.checks_summary(_grounding_event, _guardrail_event, _honesty_event),
        }, "final_answer")

        # Save to session
        await session_manager.add_message_to_histories(
            executor.user_uuid, executor.session_id, 'assistant',
            content=response_text, html_content=final_html,
            is_session_primer=executor.is_session_primer
        )

        # Create workflow_history entry for turn reload consistency
        profile_tag = executor._get_current_profile_tag()

        # Update session-level profile_tags_used array (same as other profile types)
        await session_manager.update_models_used(
            executor.user_uuid,
            executor.session_id,
            *_renv.served_model(executor.current_provider, executor.current_model),   # S4: the model that SERVED
            profile_tag
        )
        app_logger.info(f"✅ Updated session {executor.session_id} with rag_focused profile_tag={profile_tag}")

        # Send SSE notification to update UI sidebar in real-time
        session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
        if session_data:
            # Build dual_model_info from session metadata (for header display)
            dual_model_info = None
            if session_data.get("is_dual_model_active"):
                dual_model_info = {
                    "strategicProvider": session_data.get("strategic_provider"),
                    "strategicModel": session_data.get("strategic_model"),
                    "tacticalProvider": session_data.get("tactical_provider"),
                    "tacticalModel": session_data.get("tactical_model")
                }

            notification_payload = {
                "session_id": executor.session_id,
                "models_used": session_data.get("models_used", []),
                "profile_tags_used": session_data.get("profile_tags_used", []),
                "last_updated": session_data.get("last_updated"),
                "provider": executor.current_provider,
                "model": executor.current_model,
                "name": session_data.get("name", "Unnamed Session"),
                "dual_model_info": dual_model_info
            }
            app_logger.info(f"🔔 [RAG Focused] Sending session_model_update SSE: profile_tags={notification_payload['profile_tags_used']}, dual_model={dual_model_info is not None}")
            yield executor._format_sse_with_depth({
                "type": "session_model_update",
                "payload": notification_payload
            }, event="notification")

        # Track which knowledge collections were accessed
        knowledge_accessed = list(set([r.get("collection_id") for r in final_results if r.get("collection_id")]))

        # Session token totals (for plan reload) — from the copy read after the last write above.
        session_input_tokens = session_data.get("input_tokens", 0) if session_data else 0
        session_output_tokens = session_data.get("output_tokens", 0) if session_data else 0

        # Collect system events for plan reload (like session name generation)
        system_events = []

        # Calculate total duration (retrieval + synthesis)
        total_duration_ms = retrieval_duration_ms + llm_duration_ms

        # Store execution_complete in knowledge_events for reload (BEFORE turn_summary)
        # §3.3: turn cost = Σ per-call costs (dual-model aware).
        _turn_cost = executor._resolve_turn_cost_usd()
        execution_complete_payload = {
            "profile_type": "rag_focused",
            "profile_tag": profile_tag,
            "collections_searched": len(collection_names),
            "documents_retrieved": len(final_results),
            "total_input_tokens": executor.turn_input_tokens,
            "total_output_tokens": executor.turn_output_tokens,
            "retrieval_duration_ms": retrieval_duration_ms,
            "synthesis_duration_ms": llm_duration_ms,
            "total_duration_ms": total_duration_ms,
            "cost_usd": _turn_cost,
            "success": True
        }
        knowledge_events.append({
            "type": "execution_complete",
            "payload": execution_complete_payload
        })

        # Calculate session cost (cumulative up to and including this turn)
        session_cost_usd = 0.0
        try:
            previous_session_cost = executor._calculate_session_cost_at_turn(session_data)
            session_cost_usd = previous_session_cost + _turn_cost
            app_logger.debug(f"[rag_focused] Session cost at turn {executor.current_turn_number}: ${session_cost_usd:.6f}")
        except Exception as e:
            app_logger.warning(f"Failed to calculate session cost: {e}", exc_info=True)

        # --- EPC: Seal provenance chain for rag_focused ---
        _provenance_data = None
        try:
            if hasattr(executor, 'provenance') and executor.provenance:
                executor.provenance.add_step("turn_complete", response_text or "", f"rag_focused turn {executor.current_turn_number} complete")
                _provenance_data = executor.provenance.finalize()
        except Exception as _epc_err:
            app_logger.debug(f"EPC rag_focused finalize: {_epc_err}")

        turn_summary = {
            "turn": executor.current_turn_number,
            "user_query": executor.original_user_input,
            "final_summary_text": response_text,
            "status": "failed" if _synthesis_error is not None else "success",
            # A failed synthesis names it on the record: the honest sentence as error_message,
            # the provider's raw error as error_details (the answer never shows it).
            **({"error_message": response_text, "error_details": _synthesis_error[:2000]}
               if _synthesis_error is not None else {}),
            "is_session_primer": executor.is_session_primer,  # Flag for RAG case filtering
            "execution_trace": [],  # No tool executions for rag_focused
            "raw_llm_plan": None,  # No plan for rag_focused
            "original_plan": None,  # No plan for rag_focused
            "duration_ms": executor._turn_duration_ms(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": executor.current_provider,
            "model": executor.current_model,
            "profile_tag": profile_tag,
            "profile_type": "rag_focused",  # Mark as rag_focused for turn reload
            "task_id": executor.task_id if hasattr(executor, 'task_id') else None,
            "turn_input_tokens": executor.turn_input_tokens,  # Cumulative turn total
            "turn_output_tokens": executor.turn_output_tokens,  # Cumulative turn total
            "turn_cost": _turn_cost,
            "session_cost_usd": session_cost_usd,
            "session_id": executor.session_id,
            # Session totals at the time of this turn (for plan reload)
            "session_input_tokens": session_input_tokens,
            "session_output_tokens": session_output_tokens,
            "rag_source_collection_id": knowledge_accessed[0] if knowledge_accessed else None,
            "case_id": None,
            "knowledge_accessed": knowledge_accessed,  # Track knowledge collections accessed
            "knowledge_events": knowledge_events,  # Store all events for plan reload (like genie_events)
            "knowledge_retrieval_event": {
                "enabled": True,  # Always true for rag_focused
                "retrieved": len(knowledge_accessed) > 0,
                "document_count": len(final_results),
                "collections": list(collection_names),  # Include collection names
                "duration_ms": retrieval_duration_ms,  # Add duration for plan reload
                "summary": retrieval_summary,
                "answered_from_attachments": answer_from_attachments,
                "chunks": knowledge_chunks  # Include full chunks for UI display
            },
            # UI-only: Full document chunks for plan reload display (not sent to LLM)
            "knowledge_chunks_ui": knowledge_chunks,
            "kg_enrichment_event": executor.kg_enrichment_event,
            "grounding": _grounding_event,  # Tier 1.1 grounding gate verdict (reload/audit, #17)
            "guardrail": _guardrail_event,  # Tier 1.3 outbound guardrail verdict (reload/audit, #17)
            "guardrail_inbound": getattr(executor, "_inbound_guardrail_event", None),  # Tier 1.3 inbound (#17)
            # Gates whose seam has no turn context record into the per-turn tally;
            # unpacked here so each lands on its own turn key (P1d / assurance registry).
            **_assurance_stamps(),
            "honesty": _honesty_event,  # Wave 1 §1.3 retrieval-sufficiency honesty (reload/audit, #17)
            "honesty_citation": _citation_event,  # F-001 cited-but-unread (reload/audit, #17)
            "library_use": getattr(executor, "_library_use", None),  # read → ran per library section (§4a)
            "system_events": system_events,
            "context_window_snapshot_event": getattr(executor, 'context_window_snapshot_event', None),
            "skills_applied": executor.skill_result.to_applied_list() if executor.skill_result and executor.skill_result.has_content else []
        }
        if _provenance_data:
            turn_summary.update(_provenance_data)

        await session_manager.update_last_turn_data(executor.user_uuid, executor.session_id, turn_summary)
        app_logger.debug(f"Saved rag_focused turn data to workflow_history for turn {executor.current_turn_number}")

        # --- PHASE 2: Emit execution_complete lifecycle event (already stored in knowledge_events) ---
        try:
            complete_event = executor._emit_lifecycle_event("execution_complete", execution_complete_payload)
            yield complete_event
            app_logger.info("✅ Emitted execution_complete event for rag_focused profile")
        except Exception as e:
            app_logger.warning(f"Failed to emit execution_complete event: {e}")
        # --- PHASE 2 END ---

        # --- Session Name Generation (AFTER execution_complete) ---
        # Generate session name for first turn (using unified generator)
        if executor.current_turn_number == 1:
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
            if session_data and session_data.get("name") == "New Chat":
                app_logger.info(f"First turn detected for session {executor.session_id}. Attempting to generate name.")

                async for result in executor._generate_and_emit_session_name():
                    if isinstance(result, str):
                        # SSE event - yield to frontend
                        yield result
                    else:
                        # Final result tuple: (name, input_tokens, output_tokens, collected_events)
                        new_name, name_input_tokens, name_output_tokens, name_events = result
                        system_events.extend(name_events)

                        # Add session name tokens to turn totals and session totals
                        if name_input_tokens > 0 or name_output_tokens > 0:
                            executor.turn_input_tokens += name_input_tokens
                            executor.turn_output_tokens += name_output_tokens
                            await session_manager.update_token_count(
                                executor.user_uuid, executor.session_id, name_input_tokens, name_output_tokens
                            )
                            # Emit token_update event so UI reflects updated session totals
                            updated_session = await session_manager.get_session(executor.user_uuid, executor.session_id)
                            if updated_session:
                                from trusted_data_agent.core.cost_manager import get_cost_manager
                                _name_cost = get_cost_manager().calculate_cost(
                                    provider=executor.current_provider or "Unknown",
                                    model=executor.current_model or "Unknown",
                                    input_tokens=name_input_tokens,
                                    output_tokens=name_output_tokens
                                )
                                # §3.2-D: fold session-name cost into the accumulator when the main
                                # synthesis accumulated (guarded — see executor.py).
                                if getattr(executor, "turn_cost_usd", 0.0) > 0:
                                    executor.turn_cost_usd += (_name_cost or 0.0)
                                yield executor._format_sse_with_depth({
                                    "statement_input": name_input_tokens,
                                    "statement_output": name_output_tokens,
                                    "turn_input": executor.turn_input_tokens,
                                    "turn_output": executor.turn_output_tokens,
                                    "total_input": updated_session.get("input_tokens", 0),
                                    "total_output": updated_session.get("output_tokens", 0),
                                    "call_id": "session_name_generation",
                                    "cost_usd": _name_cost
                                }, "token_update")
                            # Update turn token counts in workflow_history for reload
                            # (turn_summary was saved before session name generation in rag_focused path)
                            await session_manager.update_turn_token_counts(
                                executor.user_uuid, executor.session_id, executor.current_turn_number,
                                executor.turn_input_tokens, executor.turn_output_tokens
                            )

                        if new_name != "New Chat":
                            try:
                                await session_manager.update_session_name(executor.user_uuid, executor.session_id, new_name)
                                yield executor._format_sse_with_depth({
                                    "session_id": executor.session_id,
                                    "newName": new_name
                                }, "session_name_update")
                                app_logger.info(f"Successfully updated session {executor.session_id} name to '{new_name}'.")
                            except Exception as name_e:
                                app_logger.error(f"Failed to save or emit updated session name '{new_name}': {name_e}", exc_info=True)

                        # Update turn data with session name events for reload
                        # (turn_summary was saved before session name generation)
                        if name_events:
                            try:
                                await session_manager.update_turn_system_events(
                                    executor.user_uuid, executor.session_id, executor.current_turn_number, system_events
                                )
                                app_logger.debug(f"Updated turn {executor.current_turn_number} with session name events for reload")
                            except Exception as e:
                                app_logger.warning(f"Failed to update turn with session name events: {e}")
        # --- Session Name Generation END ---

        app_logger.info("✅ RAG-focused execution completed successfully")
