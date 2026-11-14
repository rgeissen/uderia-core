"""
Ideate Engine — llm_only profile execution.

Handles direct LLM conversation without the Planner/Executor pipeline.
Optional knowledge retrieval is supported when knowledgeConfig.enabled=True.

Profile type:  ``llm_only``  (IFOC label: "Ideate", colour: green)

Notes
-----
- Pure llm_only (no useMcpTools, no active component tools) runs through this engine.
- llm_only WITH useMcpTools or component tools routes to _execute_conversation_with_tools()
  inside PlanExecutor; that sub-path is handled separately and not registered here.

This engine is registered with EngineRegistry so it can be resolved via:
    EngineRegistry.resolve(profile)   # returns IdeateEngine class
"""

from __future__ import annotations
from trusted_data_agent.agent import answer_checks as _answer_checks

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncGenerator

from .registry import EngineRegistry
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
class IdeateEngine(ExecutionEngine):
    """Direct-LLM execution engine for ``llm_only`` profiles."""

    profile_type = "llm_only"

    @classmethod
    def applies_to(cls, profile):
        """Only claim pure llm_only profiles (no useMcpTools, no component tools).

        Profiles with useMcpTools=True or active component tools are handled by
        _execute_conversation_with_tools() inside PlanExecutor.
        """
        if profile.get("profile_type") != "llm_only":
            return False
        if profile.get("useMcpTools", False):
            return False
        return True

    async def run(self, executor: "PlanExecutor") -> AsyncGenerator[str, None]:  # type: ignore[override]
        """Execute a direct LLM conversation turn.

        Extracted from PlanExecutor.run() ``if is_llm_only:`` block (lines 2994-3663).
        All state is accessed via the ``executor`` parameter.
        """
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.agent.prompt_loader import get_prompt_loader
        from trusted_data_agent.agent.formatter import OutputFormatter
        from trusted_data_agent.core.config import APP_CONFIG

        app_logger.info("🗨️ IdeateEngine: llm_only profile detected - direct execution mode")

        # --- EPC: llm_only query_intake + profile_resolve ---
        executor.provenance.add_step(
            "query_intake",
            executor.original_user_input,
            f"Query: {executor.original_user_input[:80]}",
        )
        executor.provenance.add_step(
            "profile_resolve",
            f"{executor.active_profile_id}:llm_only:{executor._get_current_profile_tag() or 'CHAT'}",
            f"Profile: @{executor._get_current_profile_tag() or 'CHAT'} (llm_only)",
        )

        # Initialize event collection for plan reload
        llm_execution_events = []

        profile_config = executor._get_profile_config()
        profile_tag = profile_config.get("tag", "CHAT")
        profile_name = profile_config.get("name", "Conversation")

        session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
        history_length = len(session_data.get("session_history", [])) if session_data else 0
        turn_number = executor.current_turn_number

        knowledge_config = profile_config.get("knowledgeConfig", {})
        knowledge_enabled = knowledge_config.get("enabled", False)
        knowledge_collections = knowledge_config.get("collections", [])
        knowledge_collection_names = (
            [c.get("name", "Unknown") for c in knowledge_collections] if knowledge_enabled else []
        )

        # --- Emit execution_start lifecycle event ---
        try:
            start_event = executor._emit_lifecycle_event(
                "execution_start",
                {
                    "profile_type": "llm_only",
                    "profile_tag": profile_tag,
                    "query": executor.original_user_input,
                    "history_length": history_length,
                    "knowledge_enabled": knowledge_enabled,
                    "knowledge_collections": len(knowledge_collections) if knowledge_enabled else 0,
                },
            )
            yield start_event
        except Exception as e:
            app_logger.warning(f"IdeateEngine: Failed to emit execution_start: {e}")

        # --- Knowledge Retrieval (optional) ---
        knowledge_context_str = None
        knowledge_accessed = []

        if knowledge_enabled and executor.rag_retriever:
            app_logger.info("🔍 IdeateEngine: Knowledge retrieval enabled")
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                from trusted_data_agent.agent.rag_access_context import RAGAccessContext

                config_manager = get_config_manager()
                effective_config = config_manager.get_effective_knowledge_config(knowledge_config)
                max_docs = effective_config.get("maxDocs", APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS)
                min_relevance = effective_config.get(
                    "minRelevanceScore", APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE
                )
                max_tokens = effective_config.get("maxTokens", APP_CONFIG.KNOWLEDGE_MAX_TOKENS)
                cw_knowledge_budget = getattr(executor, "_cw_knowledge_max_tokens", None)
                if cw_knowledge_budget:
                    max_tokens = (
                        min(cw_knowledge_budget, max_tokens) if max_tokens else cw_knowledge_budget
                    )

                collection_ids = [c["id"] for c in knowledge_collections] if knowledge_collections else []
                if collection_ids:
                    rag_context = RAGAccessContext(
                        user_id=executor.user_uuid,
                        retriever=executor.rag_retriever,
                    )
                    all_results = await executor.rag_retriever.retrieve_examples(
                        query=executor.original_user_input,
                        k=max_docs * len(knowledge_collections),
                        min_score=min_relevance,
                        allowed_collection_ids=set(collection_ids),
                        rag_context=rag_context,
                        repository_type="knowledge",
                        max_sensitivity=knowledge_config.get("maxSensitivity"),  # Wave 5 B2
                        require_signed_knowledge=bool(knowledge_config.get("requireSignedKnowledge")),  # K5
                    )

                    if all_results:
                        reranked_results = all_results
                        for coll_config in knowledge_collections:
                            if coll_config.get("reranking", False):
                                coll_results = [
                                    r
                                    for r in all_results
                                    if r.get("metadata", {}).get("collection_id") == coll_config["id"]
                                ]
                                if coll_results and executor.llm_handler:
                                    # §4: route the reranking call's cost into llm_execution_events
                                    # (persisted as knowledge_events + summed by _getTurnCost).
                                    reranked = await executor._rerank_knowledge_with_llm(
                                        query=executor.original_user_input,
                                        documents=coll_results,
                                        max_docs=max_docs,
                                        _events=llm_execution_events,
                                    )
                                    reranked_results = [
                                        r
                                        for r in reranked_results
                                        if r.get("metadata", {}).get("collection_id") != coll_config["id"]
                                    ]
                                    reranked_results.extend(reranked)

                        final_results = reranked_results[:max_docs]

                        for doc in final_results:
                            if not doc.get("collection_name"):
                                coll_id = doc.get("collection_id")
                                if coll_id and executor.rag_retriever:
                                    coll_meta = executor.rag_retriever.get_collection_metadata(coll_id)
                                    if coll_meta:
                                        doc["collection_name"] = coll_meta.get("name", "Unknown")

                        knowledge_docs = executor._format_knowledge_for_prompt(final_results, max_tokens)

                        if knowledge_docs.strip():
                            knowledge_context_str = (
                                "\n\n--- KNOWLEDGE CONTEXT ---\n"
                                "The following domain knowledge may be relevant to this conversation:\n\n"
                                f"{knowledge_docs}\n\n(End of Knowledge Context)\n"
                            )

                            knowledge_accessed = []
                            knowledge_chunks = []
                            collection_names = set()

                            for r in final_results:
                                r_metadata = r.get("metadata", {})
                                r_collection_name = r.get("collection_name", "Unknown")
                                collection_names.add(r_collection_name)

                                source_name = r_metadata.get("title") or r_metadata.get("filename")
                                if not source_name:
                                    if "(Imported)" in r_collection_name or r_metadata.get("source") == "import":
                                        source_name = "No Document Source (Imported)"
                                    else:
                                        source_name = "Unknown Source"

                                knowledge_accessed.append(
                                    {
                                        "collection_id": r.get("collection_id"),
                                        "collection_name": r_collection_name,
                                        "source": source_name,
                                    }
                                )
                                knowledge_chunks.append(
                                    {
                                        "source": source_name,
                                        "collection_name": r_collection_name,
                                        "content": r.get("content", ""),
                                        "similarity_score": r.get("similarity_score", 0),
                                        "document_id": r.get("document_id"),
                                        "chunk_index": r.get("chunk_index", 0),
                                    }
                                )

                            event_details = {
                                "summary": (
                                    f"Retrieved {len(final_results)} relevant document(s) "
                                    f"from {len(collection_names)} knowledge collection(s)"
                                ),
                                "collections": list(collection_names),
                                "document_count": len(final_results),
                                "chunks": knowledge_chunks,
                            }
                            event_data = {
                                "step": "Knowledge Retrieved",
                                "type": "knowledge_retrieval",
                                "details": event_details,
                            }
                            executor._log_system_event(event_data)
                            yield executor._format_sse_with_depth(event_data, "knowledge_retrieval")
                            llm_execution_events.append(
                                {"type": "knowledge_retrieval", "payload": event_details}
                            )
            except Exception as e:
                app_logger.error(f"IdeateEngine: Knowledge retrieval error: {e}", exc_info=True)

        # --- System prompt ---
        prompt_loader = get_prompt_loader()
        system_prompt = prompt_loader.get_prompt("CONVERSATION_EXECUTION")
        if not system_prompt:
            system_prompt = "You are a helpful AI assistant. Provide natural, conversational responses."
            app_logger.warning("IdeateEngine: CONVERSATION_EXECUTION prompt not found, using fallback")

        # Inject component instructions
        comp_section = getattr(executor, "_cw_component_instructions", None)
        if comp_section is None:
            from trusted_data_agent.components.manager import get_component_instructions_for_prompt
            comp_section = get_component_instructions_for_prompt(
                executor.active_profile_id, executor.user_uuid, session_data
            )
        system_prompt = system_prompt.replace("{component_instructions_section}", comp_section)
        # PROP-011 — the model's act is taught only while it may be taken (the component in use)
        try:
            from trusted_data_agent.agent import input_requests as _ir_carrier
            system_prompt = system_prompt.replace("{needs_input_carrier}", _ir_carrier.carrier_section("block"))
        except Exception:  # noqa: BLE001
            system_prompt = system_prompt.replace("{needs_input_carrier}", "")

        # Inject skill content
        if executor.skill_result and executor.skill_result.has_content:
            sp_block = executor.skill_result.get_system_prompt_block()
            if sp_block:
                system_prompt = f"{system_prompt}\n\n{sp_block}"

        # KG enrichment
        kg_enrichment_text = await executor._get_kg_enrichment()
        if kg_enrichment_text:
            if knowledge_context_str:
                knowledge_context_str += "\n\n" + kg_enrichment_text
            else:
                knowledge_context_str = kg_enrichment_text
            yield executor._format_sse_with_depth(
                {
                    "step": "Knowledge Graph Enrichment",
                    "type": "kg_enrichment",
                    "details": executor.kg_enrichment_event,
                }
            )
        # Agent Memory + Findings — the user_memory block (who the user is) and the verified
        # results of earlier turns (the `findings` module), re-read not recalled; the findings
        # text is the same the grounding gate takes as vouched evidence.
        _findings_text = executor._session_context_blocks()
        if _findings_text:
            knowledge_context_str = ((knowledge_context_str + "\n\n") if knowledge_context_str else "") + _findings_text

        # Document context from uploaded files
        doc_context = None
        if executor.attachments:
            doc_context, doc_trunc_events = executor.documents_for_turn()
            for evt in doc_trunc_events:
                event_data = {"step": "Context Optimization", "type": "context_optimization", "details": evt}
                executor._log_system_event(event_data)
                yield executor._format_sse_with_depth(event_data)

        # Build user message
        user_message = await executor._build_user_message_for_conversation(
            knowledge_context=knowledge_context_str,
            document_context=doc_context,
        )

        if executor.skill_result and executor.skill_result.has_content:
            uc_block = executor.skill_result.get_user_context_block()
            if uc_block:
                user_message = f"{uc_block}\n\n{user_message}"

        # Emit llm_execution event
        event_data = {
            "step": "Calling LLM for Execution",
            "type": "llm_execution",
            "details": {
                "profile_tag": profile_tag,
                "profile_name": profile_name,
                "turn_number": turn_number,
                "history_length": history_length,
                "knowledge_enabled": knowledge_enabled,
                "knowledge_collections": knowledge_collection_names,
                "model": f"{executor.current_provider}/{executor.current_model}",
                "session_id": executor.session_id,
                "user_message": user_message,
            },
        }
        executor._log_system_event(event_data)
        yield executor._format_sse_with_depth(event_data, "llm_execution")
        llm_execution_events.append(
            {
                "type": "llm_execution",
                "payload": {
                    "profile_tag": profile_tag,
                    "profile_name": profile_name,
                    "turn_number": turn_number,
                    "history_length": history_length,
                    "knowledge_enabled": knowledge_enabled,
                    "knowledge_collections": knowledge_collection_names,
                    "model": f"{executor.current_provider}/{executor.current_model}",
                    "session_id": executor.session_id,
                    "user_message": user_message,
                },
            }
        )

        # Temporarily swap to clean dependencies (prevents LLM from seeing tool defs)
        clean_dependencies = {
            "STATE": {
                "llm": executor.dependencies["STATE"]["llm"],
                "mcp_tools": {},
                "structured_tools": {},
                "structured_prompts": {},
                "prompts_context": "",
            }
        }
        original_dependencies = executor.dependencies
        executor.dependencies = clean_dependencies

        try:
            yield executor._format_sse_with_depth(
                {"target": "llm", "state": "busy"}, "status_indicator_update"
            )

            # EPC: llm_call step
            import hashlib as _hl
            executor.provenance.add_step(
                "llm_call",
                f"{_hl.sha256((system_prompt or '').encode()).hexdigest()[:32]}:{_hl.sha256((user_message or '').encode()).hexdigest()[:32]}",
                f"LLM call: {executor.current_provider}/{executor.current_model}",
            )

            response_text, input_tokens, output_tokens = await executor._call_llm_and_update_tokens(
                prompt=user_message,
                reason="Direct LLM Execution (Conversation Profile)",
                system_prompt_override=system_prompt,
                source=executor.source,
                multimodal_content=executor.multimodal_content,
                on_delta=executor.answer_stream,   # S1: this call IS the answer
            )

            executor.provenance.add_step(
                "llm_response",
                (response_text or "")[:4096],
                f"Response: {len(response_text or '')} chars",
            )
        finally:
            executor.dependencies = original_dependencies

        yield executor._format_sse_with_depth(
            {"target": "llm", "state": "idle"}, "status_indicator_update"
        )

        # LLM execution complete event
        llm_complete_event = {
            "step": "LLM Execution Complete",
            "type": "llm_execution_complete",
            "details": {
                "summary": f"Generated response using {executor.current_provider}/{executor.current_model}",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": f"{executor.current_provider}/{executor.current_model}",
                "provider": executor.current_provider,
                "model_name": executor.current_model,
                "response_length": len(response_text),
                "knowledge_used": len(knowledge_accessed) if knowledge_accessed else 0,
                "session_id": executor.session_id,
                "response_text": response_text,
                "cost_usd": executor._last_call_metadata.get("cost_usd", 0),
                "cached_tokens": executor._last_call_metadata.get("cached_tokens", 0),   # S2
            },
        }
        executor._log_system_event(llm_complete_event)
        yield executor._format_sse_with_depth(llm_complete_event, "llm_execution_complete")
        llm_execution_events.append(
            {
                "type": "llm_execution_complete",
                "payload": {
                    "summary": f"Generated response using {executor.current_provider}/{executor.current_model}",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "model": f"{executor.current_provider}/{executor.current_model}",
                    "provider": executor.current_provider,
                    "model_name": executor.current_model,
                    "response_length": len(response_text),
                    "knowledge_used": len(knowledge_accessed) if knowledge_accessed else 0,
                    "session_id": executor.session_id,
                    "response_text": response_text,
                    "cost_usd": executor._last_call_metadata.get("cost_usd", 0),
                },
            }
        )

        # Token update event
        updated_session = await session_manager.get_session(executor.user_uuid, executor.session_id)
        if updated_session:
            yield executor._format_sse_with_depth(
                {
                    "statement_input": input_tokens,
                    "statement_output": output_tokens,
                    "turn_input": executor.turn_input_tokens,
                    "turn_output": executor.turn_output_tokens,
                    "total_input": updated_session.get("input_tokens", 0),
                    "total_output": updated_session.get("output_tokens", 0),
                    "call_id": str(uuid.uuid4()),
                    "cost_usd": executor._last_call_metadata.get("cost_usd", 0),
                },
                "token_update",
            )

        # PROP-011 (phase 2): the model's act on an engine without a tool loop — a
        # ` ```uderia-ask ``` ` block in the answer, raised into the ledger (the turn then ends
        # `input_required`); the block leaves the text, the prose around it stays.
        try:
            from trusted_data_agent.agent import input_requests as _ir_mod
            response_text, _ask = _ir_mod.extract_ask_block(response_text)
            if _ask is not None and _ir_mod.pending() is None:
                _ir_mod.raise_from_act(_ask, source="answer")
        except Exception as _ae:  # noqa: BLE001
            app_logger.debug(f"needs-input: ask block skipped: {_ae}")

        executor.final_summary_text = response_text

        # Format response via OutputFormatter
        formatter = OutputFormatter(
            llm_response_text=response_text,
            collected_data=executor.structured_collected_data,
            original_user_input=executor.original_user_input,
            active_prompt_name=None,
        )
        final_html, tts_payload = formatter.render()

        # Tier 1.1 grounding gate (charter #14: all engines). A pure Ideate turn abstains
        # cleanly (no scope, no chunks). When the knowledge_context module served chunks
        # this turn, the executor threads them (E2 cache) so the corpus-support family
        # (RAG Trust Hardening Wave 1) checks the answer against the retrieved knowledge.
        response_text, _grounding_event = await executor._run_grounding_gate_full(response_text)
        if (_grounding_event or {}).get("decision"):
            executor.final_summary_text = response_text
            from trusted_data_agent.agent.grounding import adapter as _grounding_adapter
            if _grounding_event.get("outcome_delivered") == "revision":
                # S7 review: a delivered revision is rendered from the delivered text
                final_html, tts_payload = OutputFormatter(
                    llm_response_text=response_text, collected_data=executor.structured_collected_data,
                    original_user_input=executor.original_user_input, active_prompt_name=None).render()
            final_html = _grounding_adapter.apply_to_html(final_html, _grounding_event)
            yield executor._format_sse_with_depth({
                "step": "Grounding Gate", "type": "grounding_gate", "details": _grounding_event})

        # Tier 1.3 outbound guardrail (charter #14: all engines). PII redaction + toxicity
        # block on the answer; default observe → unchanged (#15). Deterministic, 0-token,
        # fails open. Runs after grounding so both verdicts apply to the delivered answer.
        response_text, _guardrail_event = executor._run_guardrail_outbound(response_text)
        if (_guardrail_event or {}).get("acted"):
            executor.final_summary_text = response_text
            from trusted_data_agent.agent.guardrails import adapter as _guardrail_adapter
            final_html = _guardrail_adapter.apply_event_to_html(final_html, response_text, _guardrail_event)
            yield executor._format_sse_with_depth({
                "step": "Guardrail", "type": "guardrail", "details": _guardrail_event})

        # RAG Trust Hardening Wave 1 §1.3 — retrieval-sufficiency honesty (module-fed
        # path): fires only when the knowledge_context module RAN retrieval this turn and
        # found (near-)nothing yet the answer asserts specifics. Chat turns with no
        # bound collections abstain by construction. Default observe → unchanged (#15).
        _honesty_event = None
        try:
            response_text, _honesty_event = executor._run_honesty_gate_rag(response_text)
            if (_honesty_event or {}).get("acted"):
                # The TEXT/HTML change only when the gate acted on the text (an enforce
                # replacement or an appended disclosure) — the Optimize seam's rule. An
                # observe flag is a card, never a banner: the answer stays byte-identical.
                if (_honesty_event.get("answer_contained")
                        or _honesty_event.get("disclosure_appended")):
                    executor.final_summary_text = response_text
                    from trusted_data_agent.agent.honesty import adapter as _honesty_adapter
                    final_html = _honesty_adapter.apply_to_html(
                        final_html, _honesty_event, response_text)
                yield executor._format_sse_with_depth({
                    "step": "Honesty Gate", "type": "honesty", "details": _honesty_event})
        except Exception:
            pass  # honesty must never break a turn

        # Wave 5 (B2): sensitivity annotation for module-fed knowledge (confidential repo).
        try:
            from trusted_data_agent.core.knowledge_provenance import sensitivity_note_html
            _sens_note = sensitivity_note_html(
                getattr(executor, "_cw_knowledge_chunks", None) or [])
            if _sens_note:
                from trusted_data_agent.agent import answer_surface as _ans
                final_html = _ans.place_note(final_html, _sens_note)
        except Exception:
            pass

        # PROP-008 — a question whose subject is not in the model: the platform's note + act
        response_text, final_html = executor._apply_model_gap(response_text, final_html)
        executor.final_summary_text = response_text

        # Emit final_answer
        event_data = {
            "step": "Finished",
            "final_answer": final_html,
            "final_answer_text": response_text,
            "turn_id": executor.current_turn_number,
            "session_id": executor.session_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tts_payload": tts_payload,
            "source": executor.source,
            "is_session_primer": executor.is_session_primer,
            # S1.1 — the turn's duration on the SAME basis the turn persists (one clock, two moments)
            "duration_ms": executor._turn_duration_ms(),
            # S1.1 — the checks that ran, as the reload will read them (one verdict, two moments)
            "checks": _answer_checks.checks_summary(_grounding_event, _guardrail_event, _honesty_event),
        }
        executor._log_system_event(event_data)
        yield executor._format_sse_with_depth(event_data, "final_answer")

        # Persist conversation turn
        await session_manager.add_message_to_histories(
            executor.user_uuid,
            executor.session_id,
            "assistant",
            content=response_text,
            html_content=final_html,
            is_session_primer=executor.is_session_primer,
        )

        # Session name generation (first turn only)
        system_events = []
        if executor.current_turn_number == 1:
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
            if session_data and session_data.get("name") == "New Chat":
                async for result in executor._generate_and_emit_session_name():
                    if isinstance(result, str):
                        yield result
                    else:
                        new_name, name_input_tokens, name_output_tokens, name_events = result
                        system_events.extend(name_events)

                        if name_input_tokens > 0 or name_output_tokens > 0:
                            executor.turn_input_tokens += name_input_tokens
                            executor.turn_output_tokens += name_output_tokens
                            await session_manager.update_token_count(
                                executor.user_uuid,
                                executor.session_id,
                                name_input_tokens,
                                name_output_tokens,
                            )
                            updated_session = await session_manager.get_session(
                                executor.user_uuid, executor.session_id
                            )
                            if updated_session:
                                from trusted_data_agent.core.cost_manager import get_cost_manager as _CM2
                                _name_cost = _CM2().calculate_cost(
                                    provider=executor.current_provider or "Unknown",
                                    model=executor.current_model or "Unknown",
                                    input_tokens=name_input_tokens,
                                    output_tokens=name_output_tokens,
                                )
                                # §3.2-D: Ideate's main synthesis routes through the accumulating
                                # _call_llm_and_update_tokens, so fold the session-name cost in too
                                # (guarded: only when the accumulator is populated — see executor.py).
                                if getattr(executor, "turn_cost_usd", 0.0) > 0:
                                    executor.turn_cost_usd += (_name_cost or 0.0)
                                yield executor._format_sse_with_depth(
                                    {
                                        "statement_input": name_input_tokens,
                                        "statement_output": name_output_tokens,
                                        "turn_input": executor.turn_input_tokens,
                                        "turn_output": executor.turn_output_tokens,
                                        "total_input": updated_session.get("input_tokens", 0),
                                        "total_output": updated_session.get("output_tokens", 0),
                                        "call_id": "session_name_generation",
                                        "cost_usd": _name_cost,
                                    },
                                    "token_update",
                                )
                                await session_manager.update_turn_token_counts(
                                    executor.user_uuid,
                                    executor.session_id,
                                    executor.current_turn_number,
                                    executor.turn_input_tokens,
                                    executor.turn_output_tokens,
                                )

                        if new_name != "New Chat":
                            try:
                                await session_manager.update_session_name(
                                    executor.user_uuid, executor.session_id, new_name
                                )
                            except Exception as e:
                                app_logger.error(f"IdeateEngine: Failed to update session name: {e}")

        # Update session models/providers used
        await session_manager.update_models_used(
            executor.user_uuid,
            executor.session_id,
            executor.current_provider,
            executor.current_model,
            profile_tag,
        )

        # session_model_update SSE for sidebar
        session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
        if session_data:
            dual_model_info = None
            if session_data.get("is_dual_model_active"):
                dual_model_info = {
                    "strategicProvider": session_data.get("strategic_provider"),
                    "strategicModel": session_data.get("strategic_model"),
                    "tacticalProvider": session_data.get("tactical_provider"),
                    "tacticalModel": session_data.get("tactical_model"),
                }
            yield executor._format_sse_with_depth(
                {
                    "type": "session_model_update",
                    "payload": {
                        "session_id": executor.session_id,
                        "models_used": session_data.get("models_used", []),
                        "profile_tags_used": session_data.get("profile_tags_used", []),
                        "last_updated": session_data.get("last_updated"),
                        "provider": executor.current_provider,
                        "model": executor.current_model,
                        "name": session_data.get("name", "Unnamed Session"),
                        "dual_model_info": dual_model_info,
                    },
                },
                event="notification",
            )

        session_input_tokens = session_data.get("input_tokens", 0) if session_data else 0
        session_output_tokens = session_data.get("output_tokens", 0) if session_data else 0

        # §3.3: turn_cost = Σ per-call costs (dual-model aware), fallback re-price if accumulator empty.
        turn_cost = executor._resolve_turn_cost_usd()

        session_cost_usd = 0.0
        try:
            previous_session_cost = executor._calculate_session_cost_at_turn(session_data)
            session_cost_usd = previous_session_cost + turn_cost
        except Exception as e:
            app_logger.warning(f"IdeateEngine: Failed to calculate session cost: {e}")

        # EPC: seal provenance chain
        _provenance_data = None
        try:
            if hasattr(executor, "provenance") and executor.provenance:
                executor.provenance.add_step(
                    "turn_complete",
                    response_text or "",
                    f"llm_only turn {executor.current_turn_number} complete",
                )
                _provenance_data = executor.provenance.finalize()
        except Exception as _epc_err:
            app_logger.debug(f"IdeateEngine EPC finalize: {_epc_err}")

        # Save turn summary to workflow_history
        turn_summary = {
            "turn": executor.current_turn_number,
            "user_query": executor.original_user_input,
            "final_summary_text": response_text,
            "status": "success",
            "is_session_primer": executor.is_session_primer,
            "execution_trace": [],
            "raw_llm_plan": None,
            "original_plan": None,
            "system_events": system_events,
            "knowledge_events": llm_execution_events,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": executor.current_provider,
            "model": executor.current_model,
            "profile_tag": profile_tag,
            "profile_type": "llm_only",
            "duration_ms": executor._turn_duration_ms(),
            "task_id": executor.task_id if hasattr(executor, "task_id") else None,
            "turn_input_tokens": executor.turn_input_tokens,
            "turn_output_tokens": executor.turn_output_tokens,
            "turn_cost": turn_cost,
            "session_cost_usd": session_cost_usd,
            "session_id": executor.session_id,
            "session_input_tokens": session_input_tokens,
            "session_output_tokens": session_output_tokens,
            "rag_source_collection_id": None,
            "case_id": None,
            "knowledge_accessed": knowledge_accessed,
            "knowledge_retrieval_event": (
                {
                    "enabled": knowledge_enabled,
                    "retrieved": len(knowledge_accessed) > 0,
                    "document_count": len(knowledge_accessed),
                }
                if knowledge_enabled
                else None
            ),
            "kg_enrichment_event": executor.kg_enrichment_event,
            "grounding": _grounding_event,  # Tier 1.1 grounding gate verdict (reload/audit, #17)
            "guardrail": _guardrail_event,  # Tier 1.3 outbound guardrail verdict (reload/audit, #17)
            "guardrail_inbound": getattr(executor, "_inbound_guardrail_event", None),  # Tier 1.3 inbound (#17)
            # Gates whose seam has no turn context record into the per-turn tally;
            # unpacked here so each lands on its own turn key (P1d / assurance registry).
            **_assurance_stamps(),
            "honesty": _honesty_event,  # Wave 1 §1.3 retrieval-sufficiency honesty (reload/audit, #17)
            "context_window_snapshot_event": getattr(executor, "context_window_snapshot_event", None),
            "skills_applied": (
                executor.skill_result.to_applied_list()
                if executor.skill_result and executor.skill_result.has_content
                else []
            ),
        }
        if _provenance_data:
            turn_summary.update(_provenance_data)

        await session_manager.update_last_turn_data(
            executor.user_uuid, executor.session_id, turn_summary
        )

        # Emit execution_complete lifecycle event
        try:
            _turn_cost = executor._resolve_turn_cost_usd()  # §3.3: Σ per-call costs (dual-model aware)
            complete_event = executor._emit_lifecycle_event(
                "execution_complete",
                {
                    "profile_type": "llm_only",
                    "profile_tag": profile_tag,
                    "total_input_tokens": executor.turn_input_tokens,
                    "total_output_tokens": executor.turn_output_tokens,
                    "knowledge_accessed": len(knowledge_accessed) > 0,
                    "cost_usd": _turn_cost,
                    "duration_ms": executor._turn_duration_ms(),
                    "success": True,
                },
            )
            yield complete_event
        except Exception as e:
            app_logger.warning(f"IdeateEngine: Failed to emit execution_complete: {e}")

        app_logger.info("✅ IdeateEngine: llm_only execution completed successfully")
