"""
Conversation Engine — llm_only profiles with MCP tools or active component tools.

Handles the LangChain ReAct agent path: a single-turn tool-calling agent
without the multi-phase Planner/Executor overhead of OptimizeEngine.

Profile type:  ``llm_only_with_tools``  (IFOC label: "Ideate" + tools, colour: green)

Notes
-----
- Pure llm_only (no useMcpTools, no component tools) routes to IdeateEngine.
- llm_only WITH useMcpTools=True is claimed here via applies_to().
- llm_only with active component tools (but no useMcpTools) is detected at
  runtime in executor.run() and also delegates here.

This engine is registered with EngineRegistry so it can be resolved via:
    EngineRegistry.resolve(profile)   # returns ConversationEngine class
"""

from __future__ import annotations
from trusted_data_agent.agent import answer_checks as _answer_checks
from trusted_data_agent.agent import findings as _findings_mod

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncGenerator

from .registry import EngineRegistry
from .base import ExecutionEngine

_TOOL_CALL_START = re.compile(r'\{\s*"tool_name"\s*:')


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



def _apply_gate_verdicts_to_html(final_html, gated_text, grounding_event, guardrail_event,
                                 honesty_event, citation_event):
    """Each gate's verdict applied to the answer HTML by that gate's own adapter, in the order
    the gates ran — grounding (marks + Scope note, or the withheld answer), guardrail (the
    redacted/withheld body, or its note), honesty (the contained answer or its note), the
    citation disclosure. `gated_text` is the text after every gate (what the stored answer
    reads); a redaction or a contained answer is rebuilt from it. Fail-soft per gate: an
    adapter that raises leaves the HTML as the previous gate left it."""
    html = final_html
    try:
        if (grounding_event or {}).get("decision"):
            from trusted_data_agent.agent.grounding import adapter as _g
            html = _g.apply_to_html(html, grounding_event)
    except Exception:  # noqa: BLE001
        pass
    try:
        if (guardrail_event or {}).get("acted"):
            from trusted_data_agent.agent.guardrails import adapter as _gr
            html = _gr.apply_event_to_html(html, gated_text, guardrail_event)
    except Exception:  # noqa: BLE001
        pass
    try:
        from trusted_data_agent.agent.honesty import adapter as _h
        # only when the gate CHANGED the answer (contained it, or appended a disclosure) —
        # an observe-mode verdict records without speaking, exactly as on the Optimize lane
        if (honesty_event or {}).get("acted") and (
                honesty_event.get("answer_contained") or honesty_event.get("disclosure_appended")):
            html = _h.apply_to_html(html, honesty_event, gated_text)
        if (citation_event or {}).get("acted") and citation_event.get("disclosure_appended"):
            html = _h.apply_to_html(html, citation_event, gated_text)
    except Exception:  # noqa: BLE001
        pass
    return html


def _strip_leaked_tool_calls(text: str) -> str:
    """Remove tool-call JSON the model echoed into its final answer.

    Some models, after invoking a component tool (e.g. TDA_Charting), ALSO dump the
    raw call `{"tool_name": "...", "arguments": {...}}` into their text answer. The
    tool already executed — the chart/component renders separately — so the JSON is
    pure noise that the user sees as a wall of text under the chart. Remove each
    balanced-brace block, respecting quoted strings/escapes so nested braces and
    quoted `{` don't break matching, then tidy the resulting blank runs.
    """
    if not text or '"tool_name"' not in text:
        return text
    out, i, n = [], 0, len(text)
    while i < n:
        m = _TOOL_CALL_START.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        depth, j, in_str, esc = 0, m.start(), False, False
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        i = j  # skip the matched (or unterminated) block
    # Drop stray markdown code fences left around the removed block, collapse blank runs.
    cleaned = ''.join(out)
    cleaned = re.sub(r'```(?:json)?\s*```', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned

if TYPE_CHECKING:
    from trusted_data_agent.agent.executor import PlanExecutor

app_logger = logging.getLogger("quart.app")



def turn_cap_message(timeout_s: int, inflight_tool, provider: str) -> str:
    """The honest sentence for a whole-turn cap. Before this the message blamed the
    MODEL ("did not respond") whatever was actually running; on the full NIBRS extract
    it was the WAREHOUSE, on a query the agent could have narrowed. Pure, so a test can
    pin both branches."""
    import time as _t
    if inflight_tool:
        name, started = inflight_tool
        ran = int(max(0, _t.time() - float(started)))
        return (f"The turn exceeded its {timeout_s}s budget while the tool call '{name}' was still "
                f"running ({ran}s and counting). The model had answered; the data query had not. "
                f"Narrow the query (a tighter filter, a join on the full key) and ask again, or ask "
                f"for a smaller slice.")
    return (f"The AI model did not respond within {timeout_s} seconds. This may be a temporary "
            f"issue with the {provider} provider. Please try again or switch to a different model.")

@EngineRegistry.register
class ConversationEngine(ExecutionEngine):
    """LangChain ReAct agent for llm_only profiles with MCP tools or component tools."""

    profile_type = "llm_only_with_tools"

    @classmethod
    def applies_to(cls, profile) -> bool:
        """Claim llm_only profiles that have useMcpTools=True.

        The has_component_tools case requires runtime detection (component manager)
        and is handled separately in executor.run() which also delegates here.
        """
        return (
            profile.get("profile_type") == "llm_only"
            and bool(profile.get("useMcpTools", False))
        )

    async def run(self, executor: "PlanExecutor") -> AsyncGenerator[str, None]:  # type: ignore[override]
        """Execute LangChain ReAct agent turn.

        Extracted from PlanExecutor._execute_conversation_with_tools() (lines 1686–2401).
        All state is accessed via the ``executor`` parameter.
        """
        # --- All imports used by the method body ---
        import os
        import time
        from trusted_data_agent.llm.langchain_adapter import (
            create_langchain_llm,
            load_mcp_tools_for_langchain,
        )
        from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.agent.formatter import OutputFormatter

        config_manager = get_config_manager()

        # Get profile configuration
        profile_config = executor._get_profile_config()
        profile_tag = profile_config.get("tag", "CONV")
        from trusted_data_agent.core.agent_mcp_servers import primary_of as _primary_of
        mcp_server_id = _primary_of(profile_config)
        llm_config_id = profile_config.get("llmConfigurationId")

        # --- EPC: Record query intake and profile for conversation_with_tools ---
        try:
            if hasattr(executor, 'provenance') and executor.provenance:
                executor.provenance.add_step("query_intake", executor.original_user_input, f"Query: {executor.original_user_input[:100]}")
                executor.provenance.add_step("profile_resolve", f"{executor.active_profile_id}:conversation_with_tools:{profile_tag}", f"Profile: @{profile_tag} (conversation_with_tools)")
        except Exception as _epc_err:
            app_logger.debug(f"EPC conversation_with_tools intake: {_epc_err}")

        # MCP server is optional when component tools are available (platform feature).
        # Component tools (TDA_Charting, etc.) don't require an MCP server.
        from trusted_data_agent.components.manager import get_component_langchain_tools
        component_tools = get_component_langchain_tools(
            executor.active_profile_id, executor.user_uuid,
            session_id=executor.session_id,
            current_turn_number=getattr(executor, "current_turn_number", None),
            current_query=getattr(executor, "original_user_input", None),
            llm_instance=(executor.profile_llm_instance
                          or (executor.dependencies.get("STATE") or {}).get("llm")),
        )

        if not mcp_server_id and not component_tools:
            error_msg = "conversation_with_tools profile requires an MCP server configuration or active component tools."
            app_logger.error(error_msg)
            yield executor._format_sse_with_depth({"step": "Error", "error": error_msg}, "error")
            return

        if not llm_config_id:
            error_msg = "conversation_with_tools profile requires an LLM configuration."
            app_logger.error(error_msg)
            yield executor._format_sse_with_depth({"step": "Error", "error": error_msg}, "error")
            return

        try:
            # Create LangChain LLM instance
            app_logger.info(f"Creating LangChain LLM for config {llm_config_id}")
            llm_instance = create_langchain_llm(llm_config_id, executor.user_uuid, thinking_budget=executor.thinking_budget)

            # Load MCP tools filtered by profile (if MCP server configured)
            all_tools = []
            if mcp_server_id:
                app_logger.info(f"Loading MCP tools from server {mcp_server_id}")
                mcp_tools = await load_mcp_tools_for_langchain(
                    mcp_server_id=mcp_server_id,
                    profile_id=executor.active_profile_id,
                    user_uuid=executor.user_uuid
                )
                all_tools.extend(mcp_tools)
                app_logger.info(f"Loaded {len(mcp_tools)} MCP tools for agent")

            # PROP-006 phase 2: the agent's ADDITIONAL MCP servers — each server's selected tools,
            # named `<alias>__<name>` so no two bound tools share a name. One server that cannot be
            # reached never stops the turn: it answers from the reachable sources (B9).
            from trusted_data_agent.core.agent_mcp_servers import additional_of as _additional_of
            from trusted_data_agent.agent import source_routing as _srt
            for _srv in _additional_of(profile_config):
                if _srt.is_unavailable(_srv.server_id):
                    # the turn-start probe already found it down: not loaded (no second timeout)
                    continue
                try:
                    _extra = await load_mcp_tools_for_langchain(
                        mcp_server_id=_srv.server_id, profile_id=executor.active_profile_id,
                        user_uuid=executor.user_uuid, alias=_srv.alias, selection=list(_srv.tools))
                    all_tools.extend(_extra)
                    app_logger.info(f"Loaded {len(_extra)} MCP tools from additional server "
                                    f"{_srv.server_id} ({_srv.alias})")
                except Exception as _xe:  # noqa: BLE001
                    app_logger.warning(f"Additional MCP server {_srv.server_id} ({_srv.alias}) "
                                       f"could not be loaded: {_xe}")
                    _srt.mark_unavailable(_srv.server_id, _xe)
            # B9 — named, never silent: ONE record of the turn's unreachable servers (the
            # turn-start probe and the loading above feed it; the Optimize lane reads the same):
            # a Live Status step (persisted for reload) and a note to the model, so a missing
            # source is never read as missing data.
            try:
                for _uev in _srt.take_unavailable_events():
                    executor._conv_source_events = getattr(executor, "_conv_source_events", []) + [
                        {"type": _uev["type"], "payload": dict(_uev)}]
                    yield executor._format_sse_with_depth(_uev)
            except Exception:  # noqa: BLE001
                pass

            # Merge component tools (TDA_Charting, etc.) — platform feature, per-profile config
            # component_tools already loaded above (before MCP server check)
            if component_tools:
                all_tools.extend(component_tools)
                app_logger.info(f"Added {len(component_tools)} component tool(s): {', '.join(t.name for t in component_tools)}")

            # Merge platform connector tools (Google, browser, files, etc.) — admin-governed
            try:
                connector_lc_tools = await _load_connector_tools_for_langchain(
                    executor.active_profile_id, executor.user_uuid
                )
                if connector_lc_tools:
                    all_tools.extend(connector_lc_tools)
                    app_logger.info(
                        f"Added {len(connector_lc_tools)} platform connector tool(s): "
                        f"{', '.join(t.name for t in connector_lc_tools)}"
                    )
            except Exception as _ce:
                app_logger.warning(f"Could not load platform connector tools: {_ce}")

            # Budget-aware history window from context window module (set in execute())
            cw_history_window = getattr(executor, '_cw_history_window', 10)

            # Get conversation history for context
            # NOTE: The current user query has already been added to chat_object by execution_service.py
            # before this code runs. We need to exclude it to avoid duplication since the agent
            # will add it again as the current query.
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
            # ONE rule for the history every agent receives (agent/agent_history.py): roles
            # normalised (`model` → assistant), only the trailing current question removed, priming
            # and turned-off messages skipped, the budget-aware window.
            from trusted_data_agent.agent import agent_history as _ah
            conversation_history = (_ah.build(session_data.get("chat_object", []), executor.original_user_input, cw_history_window,
                                              disabled=bool(getattr(executor, "disabled_history", False)))
                                    if session_data else [])
            app_logger.info(f"[ConvAgent] Built conversation history with {len(conversation_history)} messages")

            # --- KNOWLEDGE RETRIEVAL FOR CONVERSATION WITH TOOLS ---
            # Check if knowledge collections are enabled for this profile
            knowledge_config = profile_config.get("knowledgeConfig", {})
            knowledge_enabled = knowledge_config.get("enabled", False)
            knowledge_context_str = ""
            knowledge_accessed = []
            knowledge_chunks = []
            knowledge_retrieval_event_data = None

            if knowledge_enabled and executor.rag_retriever:
                app_logger.info("🔍 Knowledge retrieval enabled for conversation_with_tools profile")

                try:
                    retrieval_start_time = time.time()

                    knowledge_collections = knowledge_config.get("collections", [])
                    # Use three-tier configuration (global -> profile -> locks)
                    effective_config = config_manager.get_effective_knowledge_config(knowledge_config)
                    max_docs = effective_config.get("maxDocs", APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS)
                    min_relevance = effective_config.get("minRelevanceScore", APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE)
                    max_tokens = effective_config.get("maxTokens", APP_CONFIG.KNOWLEDGE_MAX_TOKENS)
                    # Budget-aware override from context window module (Phase 5b)
                    cw_knowledge_budget = getattr(executor, '_cw_knowledge_max_tokens', None)
                    if cw_knowledge_budget:
                        max_tokens = min(cw_knowledge_budget, max_tokens) if max_tokens else cw_knowledge_budget
                        app_logger.info(f"[ConvAgent] Knowledge max_tokens from context window budget: {max_tokens:,}")

                    if knowledge_collections:
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
                        _search_modes = {}
                        try:
                            from trusted_data_agent.core.collection_db import get_collection_db
                            _cdb = get_collection_db()
                            for _cc in knowledge_collections:
                                _cid = _cc.get("id")
                                _cn = _cc.get("name", str(_cid))
                                if _cid:
                                    _dbr = _cdb.get_collection_by_id(_cid)
                                    if _dbr:
                                        _search_modes[_cn] = _dbr.get("search_mode", "semantic")
                                        continue
                                _search_modes[_cn] = "semantic"
                        except Exception as _e:
                            app_logger.debug(f"Failed to read search_mode for collections: {_e}")

                        yield executor._format_sse_with_depth({
                            "type": "knowledge_retrieval_start",
                            "payload": {
                                "collections": collection_names_for_start,
                                "max_docs": max_docs,
                                "session_id": executor.session_id,
                                "search_modes": _search_modes,
                            }
                        }, event="notification")

                        from trusted_data_agent.agent.rag_access_context import RAGAccessContext
                        rag_context = RAGAccessContext(user_id=executor.user_uuid, retriever=executor.rag_retriever)

                        all_results = await executor.rag_retriever.retrieve_examples(
                            query=executor.original_user_input,
                            k=max_docs * len(knowledge_collections),
                            min_score=min_relevance,
                            allowed_collection_ids=set([c["id"] for c in knowledge_collections]),
                            rag_context=rag_context,
                            repository_type="knowledge"
                        )

                        if all_results:
                            # Apply reranking if configured
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
                                        yield executor._format_sse_with_depth({
                                            "type": "knowledge_reranking_start",
                                            "payload": {
                                                "collection": coll_name,
                                                "document_count": len(coll_results),
                                                "session_id": executor.session_id
                                            }
                                        }, event="notification")

                                        # §4: collect the reranking call's cost on a turn-scoped
                                        # executor list (the knowledge block runs BEFORE system_events
                                        # is built); merged into system_events at persist time so the
                                        # cost enters the reload event-sum (ReAct is single-model, so
                                        # turn_cost already includes it via the re-price).
                                        if not hasattr(executor, "_conv_rerank_events"):
                                            executor._conv_rerank_events = []
                                        reranked = await executor._rerank_knowledge_with_llm(
                                            query=executor.original_user_input,
                                            documents=coll_results,
                                            max_docs=max_docs,
                                            _events=executor._conv_rerank_events
                                        )

                                        # Emit reranking complete event with token info
                                        # Get session to show updated token counts
                                        updated_session = await session_manager.get_session(executor.user_uuid, executor.session_id)
                                        if updated_session:
                                            yield executor._format_sse_with_depth({
                                                "type": "knowledge_reranking_complete",
                                                "payload": {
                                                    "collection": coll_name,
                                                    "reranked_count": len(reranked),
                                                    "session_id": executor.session_id
                                                }
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
                                    if coll_id:
                                        coll_meta = executor.rag_retriever.get_collection_metadata(coll_id)
                                        if coll_meta:
                                            doc["collection_name"] = coll_meta.get("name", "Unknown")

                            # Format knowledge context for agent
                            knowledge_context_str = executor._format_knowledge_for_prompt(final_results, max_tokens)

                            # Build detailed event data for Live Status panel
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
                                    "content": doc.get("content", ""),
                                    "similarity_score": doc.get("similarity_score", 0),
                                    "document_id": doc.get("document_id"),
                                    "chunk_index": doc.get("chunk_index", 0)
                                })

                            knowledge_accessed = list(collection_names)

                            # Calculate retrieval duration
                            retrieval_duration_ms = int((time.time() - retrieval_start_time) * 1000)

                            # Store event data for SSE emission and turn summary
                            # Include chunks for live status window display
                            knowledge_retrieval_event_data = {
                                "collections": list(collection_names),
                                "document_count": len(final_results),
                                "duration_ms": retrieval_duration_ms,
                                "summary": f"Retrieved {len(final_results)} documents from {len(collection_names)} collection(s)",
                                "chunks": knowledge_chunks,  # Include full chunks for UI display
                                "search_modes": _search_modes,
                            }

                            # Emit completion event for Live Status panel (replaces old single event)
                            yield executor._format_sse_with_depth({
                                "type": "knowledge_retrieval_complete",
                                "payload": knowledge_retrieval_event_data
                            }, event="notification")

                            app_logger.info(f"📚 Retrieved {len(final_results)} knowledge documents from {len(collection_names)} collection(s) in {retrieval_duration_ms}ms")

                except Exception as e:
                    app_logger.error(f"Error during knowledge retrieval for conversation_with_tools: {e}", exc_info=True)
                    # Continue without knowledge (graceful degradation)
            # --- END KNOWLEDGE RETRIEVAL ---

            # --- KG enrichment (all profile types) ---
            kg_enrichment_text = await executor._get_kg_enrichment()
            if kg_enrichment_text:
                if knowledge_context_str:
                    knowledge_context_str += "\n\n" + kg_enrichment_text
                else:
                    knowledge_context_str = kg_enrichment_text
                yield executor._format_sse_with_depth({
                    "step": "Knowledge Graph Enrichment",
                    "type": "kg_enrichment",
                    "details": executor.kg_enrichment_event
                })
            # Agent Memory + Findings — the user_memory block (who the user is) and the verified
            # results of earlier turns (the `findings` module), re-read not recalled; the
            # findings text is the same the grounding gate takes as vouched evidence.
            _findings_text = executor._session_context_blocks()
            if _findings_text:
                knowledge_context_str = ((knowledge_context_str + "\n\n") if knowledge_context_str else "") + _findings_text

            # Inject resolved skill content (e.g. teradata-sql-expert) into the agent's
            # system prompt — mirror IdeateEngine/FocusEngine. Both the system-prompt and
            # user-context skill blocks are combined here so all skill markdown reaches the
            # model (previously this engine only surfaced the skills-applied metadata).
            skill_context = None
            if executor.skill_result and executor.skill_result.has_content:
                _parts = [
                    executor.skill_result.get_system_prompt_block(),
                    executor.skill_result.get_user_context_block(),
                ]
                _parts = [p for p in _parts if p]
                if _parts:
                    skill_context = "\n\n".join(_parts)
                    app_logger.info(
                        f"Conversation agent: injecting {len(skill_context)} chars of skill content "
                        f"({[s['name'] for s in executor.skill_result.to_applied_list()]})"
                    )

            # Create and execute agent with real-time SSE event handler
            # Pass executor.event_handler for immediate SSE streaming during execution
            # Respect capability flag: some LLMs don't support function calling
            effective_tools = [] if getattr(llm_instance, '_no_function_calling', False) else all_tools

            # --- S6 The Governed Tool Surface (ReAct lane): decided AFTER the enrichment named
            # the tables in play and BEFORE the agent binds. The model binds offered ∪ found per
            # call (a dynamic model); every tool stays executable through the guarded coroutine.
            _surface = None
            try:
                from trusted_data_agent.agent import tool_surface as _ts
                if effective_tools and _ts.armed_governed(profile_config):
                    _state = executor.dependencies.get('STATE') or {}
                    _cats = _ts.categories_from_structured(_state.get('structured_tools', {}) or {})
                    _catalog = _ts.catalog_from_langchain(effective_tools, _cats, _state.get('tool_scopes', {}))
                    if _ts.arm(executor.original_user_input or "", _catalog, profile=profile_config,
                               scope=executor._surface_scope(), enrichment_names=executor._surface_enrichment_names(),
                               user_uuid=executor.user_uuid, session_id=executor.session_id) is not None:
                        _surface = _ts.current()
                        _sev = executor.surface_event()
                        if _sev:
                            executor._log_system_event(_sev)
                            yield executor._format_sse_with_depth(_sev)
            except Exception as _surf_err:  # noqa: BLE001 — a surface must never break a turn
                app_logger.debug(f"[surface] react arming skipped: {_surf_err}")

            _source_note = None
            try:
                from trusted_data_agent.agent import source_routing as _srt
                _source_note = _srt.turn_sources_note()
            except Exception:  # noqa: BLE001
                _source_note = None
            agent = ConversationAgentExecutor(
                profile=profile_config,
                user_uuid=executor.user_uuid,
                session_id=executor.session_id,
                llm_instance=llm_instance,
                mcp_tools=effective_tools,
                async_event_handler=executor.event_handler,  # Real-time SSE via asyncio.create_task()
                max_iterations=APP_CONFIG.LANGCHAIN_MAX_ITERATIONS,
                conversation_history=conversation_history,
                knowledge_context=knowledge_context_str or None,  # From knowledge retrieval and/or KG enrichment
                document_context=executor.document_context,
                multimodal_content=executor.multimodal_content,
                turn_number=executor.current_turn_number,
                provider=executor.current_provider,  # NEW: Pass provider for event tracking
                model=executor.current_model,        # NEW: Pass model for event tracking
                canvas_context=executor._format_canvas_context(),  # Canvas bidirectional context
                component_instructions=getattr(executor, '_cw_component_instructions', None),
                provenance=getattr(executor, 'provenance', None),  # EPC: Pass provenance chain
                answer_stream=executor.answer_stream,  # S1: the ReAct answer streams
                cost_sink=executor._accumulate_call_cost,  # S2: each cache-priced call → the turn accumulator
                skill_context=skill_context,  # Resolved skill markdown (teradata-sql-expert, etc.)
                tool_surface=_surface,  # S6: the governed surface (None = bind everything, today's path)
                source_note=_source_note,  # B9: a server that could not be reached, named to the model
            )

            # Execute agent (events are emitted in real-time via async_event_handler)
            # Wrap with timeout to prevent indefinite hangs (e.g. Friendli streaming stalls)
            _conv_timeout = int(os.environ.get('TDA_CONVERSATION_AGENT_TIMEOUT', '300'))
            # Seed the user_input cache so TDA_Dashboard's _handle_visualize
            # can use the original question as source_prompt (not the LLM's
            # 'query' arg, which often contains stale result data).
            try:
                from trusted_data_agent.mcp_adapter.adapter import _user_input_cache as _uic
                _uic[executor.session_id] = executor.original_user_input
            except Exception:
                pass

            try:
                result = await asyncio.wait_for(
                    agent.execute(executor.original_user_input),
                    timeout=_conv_timeout
                )
            except asyncio.TimeoutError:
                msg = turn_cap_message(_conv_timeout, getattr(agent, "inflight_tool", None),
                                       executor.current_provider)
                app_logger.error(f"⏱️ Conversation agent turn cap: {msg} "
                                 f"(Model: {executor.current_model})")
                raise Exception(msg)

            # Note: Events are now emitted in real-time via asyncio.create_task() in the agent
            # The collected_events in result are used for session storage/replay only

            # Extract result data
            response_text = result.get("response", "")
            # Strip any tool-call JSON the model echoed into its answer (the component
            # already rendered separately). Cleans both the displayed text and the
            # stored chat_object (every downstream use reads this same variable).
            response_text = _strip_leaked_tool_calls(response_text)
            # PROP-011 (phase 2): the act carried in the answer itself — a ` ```uderia-ask ``` `
            # block (the carrier for an agent without a tool loop; a ReAct agent has TDA_AskUser
            # but may still write the block). Raised into the ledger, validated by the one
            # validator; the block leaves the text, the prose around it stays.
            try:
                from trusted_data_agent.agent import input_requests as _ir_mod
                response_text, _ask = _ir_mod.extract_ask_block(response_text)
                if _ask is not None and _ir_mod.pending() is None:
                    _ir_mod.raise_from_act(_ask, source="answer")
            except Exception as _ae:  # noqa: BLE001
                app_logger.debug(f"needs-input: ask block skipped: {_ae}")
            # The model's own text, before any gate speaks. The answer body is rendered from
            # THIS; each gate then adds its verdict to the rendered HTML through its own
            # adapter (a platform note, an in-place mark, a withheld/redacted body) — the
            # same pattern as the Focus, Ideate, Optimize and Coordinate lanes. Rendering the
            # gated text instead drew a gate's sentence ("> Some statements…") as if the
            # model had written it, never as a note.
            _model_text = response_text
            # Tier 1.1 grounding gate (charter #14: all engines). Runs here — before the
            # final HTML is built from response_text — so any annotation/withholding flows
            # into both the displayed answer and the stored chat_object. Default observe →
            # response_text unchanged (charter #15). Abstains on non-KG turns.
            response_text, _grounding_event = await executor._run_grounding_gate_full(
                response_text,
                trace_extra={"conversation_agent_events": result.get("collected_events", [])})
            # Tier 1.3 outbound guardrail (charter #14: all engines). Runs here — before the
            # final HTML is rendered from response_text — so a redaction/withhold flows into
            # both the displayed answer and the stored chat_object. Default observe → unchanged.
            response_text, _guardrail_event = executor._run_guardrail_outbound(response_text)
            # OBDA-READ (OBDA_VISION.md §5) — governed-data lane observability (charter #14). Scan the
            # ReAct tool calls into governed (compile_metric) vs free-formed (raw SQL). 0-token;
            # abstains (None) when the turn did no data ops. Emitted live + stamped for reload (#17).
            from trusted_data_agent.agent import obda as _obda_mod
            _kg_calls = _obda_mod.calls_from_conversation_events(result.get("collected_events", []))
            _obda_event = executor._run_obda_scan(_kg_calls)
            executor._record_kg_used(_kg_calls)   # `used` KG telemetry — not gated on OBDA-READ
            # Honesty post-condition (charter #14: all data-running engines). Runs before the
            # final HTML is rendered from response_text, so an enforce rewrite of a confident
            # false negative (asserts non-existence while every raw-SQL data query failed) flows
            # into both the displayed answer and the stored chat_object. Default observe →
            # response_text unchanged (#15). Abstains on legit empty results / no data queries.
            # What the model was GIVEN this turn (the knowledge block + the conversation): the
            # whitelist for the unsourced-record check — a record whose values are in none of it,
            # from a turn that ran no data query, was written by the model. Only for an agent that
            # HAS a data tool and chose not to use it; a pure Ideate turn has nothing to query.
            _support_text = None
            if any(not str(getattr(t, "name", "")).startswith("TDA_") for t in (effective_tools or [])):
                _support_text = "\n".join([knowledge_context_str or "", executor.document_context or ""]
                                          + [str(m.get("content") or "") for m in (conversation_history or [])])
            response_text, _honesty_event = await executor._run_honesty_gate(
                response_text,
                _obda_mod.calls_from_conversation_events(result.get("collected_events", [])),
                support_text=_support_text)
            # F-001 — cited-but-unread. The ReAct family is where a progressive
            # skill actually lives (an llm_only profile with ANY active component tool
            # is routed here, so this seam covers Ideate-with-a-library too). Passes
            # BOTH evidence shapes; abstains byte-identically without a library.
            response_text, _citation_event = executor._run_citation_gate(
                response_text,
                _obda_mod.calls_from_conversation_events(result.get("collected_events", [])),
                result.get("collected_events", []))
            tools_used = result.get("tools_used", [])
            success = result.get("success", False)
            # Whole-turn wall-clock on the canonical baseline (turn_start_time) — the same
            # basis every other class reports; the agent's own internal run is the floor.
            duration_ms = max(int(result.get("duration_ms", 0) or 0), int(executor._turn_duration_ms() or 0))
            input_tokens = result.get("input_tokens", 0)
            output_tokens = result.get("output_tokens", 0)

            # If exactly one data (non-TDA_) tool was used and TDA_Dashboard was also
            # called, stamp mcp_tool_name + mcp_args_json on the tile so future refreshes
            # can replay the tool directly without the LLM.
            _last_data_tool_name = result.get("last_data_tool_name")
            _last_data_tool_args = result.get("last_data_tool_args")
            if _last_data_tool_name and "TDA_Dashboard" in tools_used:
                try:
                    import json as _json
                    from components.builtin.dashboard.handler import _conn as _dash_conn, _now as _dash_now
                    _turn_no = getattr(executor, "current_turn_number", None)
                    # Stamp ONLY the tile(s) created in THIS turn (source_turn_id),
                    # never "the latest tile in the session" — the old session-scoped
                    # lookup cross-contaminated tiles from other turns with this turn's
                    # data tool, producing a misleading/incorrect direct-refresh bolt.
                    if _turn_no is not None:
                        with _dash_conn() as _dc:
                            _rows = _dc.execute(
                                "UPDATE dashboard_tiles SET mcp_tool_name=?, mcp_args_json=?, updated_at=?"
                                " WHERE source_session_id=? AND source_turn_id=? AND user_id=?",
                                (_last_data_tool_name, _json.dumps(_last_data_tool_args or {}),
                                 _dash_now(), executor.session_id, _turn_no, executor.user_uuid),
                            ).rowcount
                            _dc.commit()
                            if _rows:
                                app_logger.info(
                                    f"[ConvEngine] Stamped mcp_tool_name={_last_data_tool_name!r}"
                                    f" on {_rows} tile(s) for turn {_turn_no}"
                                )
                except Exception as _e:
                    app_logger.warning(f"[ConvEngine] Could not stamp mcp_tool_name on tile: {_e}")

            # Include component-internal LLM tokens (e.g., chart mapping resolution).
            # These are NOT captured by LangChain callbacks but were already persisted
            # to session DB by call_llm_api → update_token_count (handler.py:1082).
            comp_llm_in = result.get("component_llm_input_tokens", 0)
            comp_llm_out = result.get("component_llm_output_tokens", 0)
            combined_input = input_tokens + comp_llm_in
            combined_output = output_tokens + comp_llm_out

            # Update turn token counters with combined totals
            executor.turn_input_tokens += combined_input
            executor.turn_output_tokens += combined_output

            # LangChain tokens are now persisted incrementally per LLM step
            # inside conversation_agent.py (via update_token_count per on_chat_model_end).
            # Only persist component LLM tokens here (chart mapping, etc.) since those
            # are NOT captured by LangChain callbacks.
            if comp_llm_in > 0 or comp_llm_out > 0:
                await session_manager.update_token_count(
                    executor.user_uuid,
                    executor.session_id,
                    comp_llm_in,
                    comp_llm_out
                )

            # Emit final token_update event with complete session totals
            updated_session = await session_manager.get_session(executor.user_uuid, executor.session_id)
            if updated_session:
                # Calculate cost using combined tokens (LangChain + component LLM)
                _conv_cost = 0
                try:
                    from trusted_data_agent.core.cost_manager import get_cost_manager
                    _conv_cost = get_cost_manager().calculate_cost(
                        provider=executor.current_provider or "Unknown",
                        model=executor.current_model or "Unknown",
                        input_tokens=combined_input,
                        output_tokens=combined_output
                    )
                except Exception:
                    pass
                yield executor._format_sse_with_depth({
                    "statement_input": combined_input,
                    "statement_output": combined_output,
                    "turn_input": executor.turn_input_tokens,
                    "turn_output": executor.turn_output_tokens,
                    "total_input": updated_session.get("input_tokens", 0),
                    "total_output": updated_session.get("output_tokens", 0),
                    "call_id": str(uuid.uuid4()),
                    "cost_usd": _conv_cost
                }, "token_update")

            # Format the response using OutputFormatter's markdown renderer
            # This ensures consistent formatting with other profile types
            # a delivered S7 revision IS the model's answer now (cleared by the gate) — every
            # other gate outcome is applied to the HTML below, never rendered as prose
            _render_text = (response_text if (_grounding_event or {}).get("outcome_delivered") == "revision"
                            else _model_text)
            formatter_kwargs = {
                "llm_response_text": _render_text,  # Triggers markdown rendering
                # the rows the agent's data tools returned — rendered as `Data` evidence
                # rows by the SAME formatter path the Optimize lane uses, and persisted
                # inside the body so a plan reload shows the identical rows
                "collected_data": list(result.get("data_results") or []),
                "original_user_input": executor.original_user_input,
                "active_prompt_name": None
            }
            formatter = OutputFormatter(**formatter_kwargs)
            final_html, tts_payload = formatter.render()
            final_html = _apply_gate_verdicts_to_html(
                final_html, response_text, _grounding_event, _guardrail_event,
                _honesty_event, _citation_event)

            # S3 — number lineage (the ReAct twin of the Optimize seam): receipts sealed,
            # figures marked in the rendered prose, an unreceipted figure disclosed under an
            # acting honesty mode. Off ⇒ byte-identical. Stamped for reload (#17).
            response_text, final_html, _compute_lineage_event = executor._run_compute_lineage(response_text, final_html)
            # S5 — the budget's finish line (no armed budget ⇒ untouched)
            response_text, final_html = executor._apply_turn_budget_finish(response_text, final_html)
            # PROP-008 — a question whose subject is not in the model: the platform's note + act
            response_text, final_html = executor._apply_model_gap(response_text, final_html)

            # Append component rendering HTML (chart, code, audio, video, etc.)
            from trusted_data_agent.components.utils import generate_component_html, canvas_contents
            from trusted_data_agent.agent.honesty import adapter as _honesty_adapter
            # A contained honesty verdict replaced the prose; the chart/tile built from the
            # same invented figures must not render beneath it (the Optimize path rebuilds
            # its whole body — this seam is the ReAct twin of that).
            _payloads, _dropped = _honesty_adapter.suppress_components(
                result.get("component_payloads", []), _honesty_event)
            if _dropped:
                app_logger.info(f"[honesty] suppressed {len(_dropped)} data visual(s) under a contained verdict: {_dropped}")
            final_html += generate_component_html(_payloads)

            # Add assistant message to conversation history (with HTML for display)
            await session_manager.add_message_to_histories(
                user_uuid=executor.user_uuid,
                session_id=executor.session_id,
                role='assistant',
                content=response_text,  # Clean text for LLM consumption
                html_content=final_html,  # Formatted HTML for UI display
                profile_tag=profile_tag,
                is_session_primer=executor.is_session_primer
            )

            # Update session metadata — the model that SERVED (S4: a fallback names itself on
            # the turn's resilience record; the session's models_used must not claim the
            # configured model served an answer it never produced).
            from trusted_data_agent.agent import resilience_envelope as _renv
            _srv_prov, _srv_mod = _renv.served_model(executor.current_provider, executor.current_model)
            await session_manager.update_models_used(
                executor.user_uuid,
                executor.session_id,
                _srv_prov,
                _srv_mod,
                profile_tag
            )

            # Collect system events for plan reload (like session name generation).
            # §4: seed with any knowledge-reranking cost events collected earlier this turn so their
            # cost enters the persisted event-sum (matches turn_cost, which includes reranking).
            system_events = list(getattr(executor, "_conv_rerank_events", []))
            system_events.extend(getattr(executor, "_conv_source_events", []) or [])

            # Generate session name if first turn (using unified generator)
            if executor.current_turn_number == 1:
                async for name_result in executor._generate_and_emit_session_name():
                    if isinstance(name_result, str):
                        # SSE event - yield to frontend
                        yield name_result
                    else:
                        # Final result tuple: (name, input_tokens, output_tokens, collected_events)
                        new_name, name_input_tokens, name_output_tokens, name_events = name_result
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
                                from trusted_data_agent.core.cost_manager import get_cost_manager as _CM1
                                _name_cost = _CM1().calculate_cost(
                                    provider=executor.current_provider or "Unknown",
                                    model=executor.current_model or "Unknown",
                                    input_tokens=name_input_tokens,
                                    output_tokens=name_output_tokens
                                )
                                # S2: the ReAct steps now accumulate through cost_sink, so the
                                # session-name call folds in too (guarded exactly as Ideate: only
                                # when the accumulator is populated — see executor.py §3.2-D).
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

                        if new_name and new_name != "New Chat":
                            await session_manager.update_session_name(executor.user_uuid, executor.session_id, new_name)
                            yield executor._format_sse_with_depth({
                                "session_id": executor.session_id,
                                "newName": new_name
                            }, "session_name_update")

            # Grounding gate observability event (live) — before the answer so it reads
            # in order in Live Status. Stamped into turn_summary below for reload (#17).
            if (_grounding_event or {}).get("decision"):
                yield executor._format_sse_with_depth({
                    "step": "Grounding Gate",
                    "type": "grounding_gate",
                    "details": _grounding_event
                })
            if (_guardrail_event or {}).get("acted"):
                yield executor._format_sse_with_depth({
                    "step": "Guardrail",
                    "type": "guardrail",
                    "details": _guardrail_event
                })
            if _obda_event:
                yield executor._format_sse_with_depth({
                    "step": "Governed Data Operations",
                    "type": "obda",
                    "details": _obda_event
                })
            if (_honesty_event or {}).get("acted"):
                yield executor._format_sse_with_depth({
                    "step": "Honesty Check",
                    "type": "honesty",
                    "details": _honesty_event
                })
            if _compute_lineage_event:
                from trusted_data_agent.agent.compute_lineage import step_title as _lin_title
                yield executor._format_sse_with_depth({
                    "step": _lin_title(_compute_lineage_event),
                    "type": "compute_lineage",
                    "details": _compute_lineage_event
                })
            if (_citation_event or {}).get("acted"):
                yield executor._format_sse_with_depth({
                    "step": "Citation Check",
                    "type": "honesty",
                    "details": _citation_event
                })

            # Emit final answer with formatted HTML
            yield executor._format_sse_with_depth({
                "step": "Finished",
                "final_answer": final_html,  # Send formatted HTML
                "final_answer_text": response_text,  # Also include clean text
                # Code the turn put into canvases, as data — the clean text no longer holds it
                # (a flow node / REST caller reads this; the chat reads the canvas card).
                "canvas_contents": canvas_contents(_payloads),
                "turn_id": executor.current_turn_number,
                "session_id": executor.session_id,  # Include session_id for filtering when switching sessions
                "tts_payload": tts_payload,
                "source": executor.source,
                "is_session_primer": executor.is_session_primer,
                # S1.1 — the turn's duration on the SAME basis the turn persists (one clock, two moments)
                "duration_ms": duration_ms,
                # S1.1 — the checks that ran, as the reload will read them (one verdict, two moments)
                "checks": _answer_checks.checks_summary(_grounding_event, _guardrail_event, _honesty_event, _compute_lineage_event),
            }, "final_answer")

            # Get session data for session token totals (needed for plan reload display)
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)
            session_input_tokens = session_data.get("input_tokens", 0) if session_data else 0
            session_output_tokens = session_data.get("output_tokens", 0) if session_data else 0

            # §3.3: turn_cost = Σ per-call costs (dual-model aware; ReAct steps accumulate too).
            turn_cost = executor._resolve_turn_cost_usd()  # S2: the ReAct steps accumulate through cost_sink (cache-priced)
            app_logger.debug(f"[conversation_with_tools] Turn {executor.current_turn_number} cost (Σ per-call): ${turn_cost:.6f}")

            # Calculate session cost (cumulative up to and including this turn)
            session_cost_usd = 0.0
            try:
                previous_session_cost = executor._calculate_session_cost_at_turn(session_data)
                session_cost_usd = previous_session_cost + turn_cost  # Add current turn
                app_logger.debug(f"[conversation_with_tools] Session cost at turn {executor.current_turn_number}: ${session_cost_usd:.6f}")
            except Exception as e:
                app_logger.warning(f"Failed to calculate session cost: {e}", exc_info=True)

            # --- EPC: Seal provenance chain for conversation_with_tools ---
            _provenance_data = None
            try:
                if hasattr(executor, 'provenance') and executor.provenance:
                    executor.provenance.add_step("turn_complete", response_text or "", f"conversation_with_tools turn {executor.current_turn_number} complete")
                    _provenance_data = executor.provenance.finalize()
            except Exception as _epc_err:
                app_logger.debug(f"EPC conversation_with_tools finalize: {_epc_err}")

            # Save turn data to workflow_history for session reload
            turn_summary = {
                "turn": executor.current_turn_number,
                "user_query": executor.original_user_input,
                "final_summary_text": response_text,  # Clean text for LLM context
                "final_summary_html": final_html,  # Formatted HTML for session reload
                "status": "success" if success else "failed",
                # C18: a failed ReAct turn names its error on the record (the answer carries only
                # the honest sentence); JUDGED's clean_termination reads status + error_message.
                # Same shape as Optimize's failed turn: the honest sentence as error_message, the
                # raw provider error as error_details (the record keeps it; the answer never shows it).
                **({"error_message": response_text or "The turn failed.",
                    "error_details": str(result.get("error") or "")[:2000]} if not success else {}),
                "is_session_primer": executor.is_session_primer,  # Flag for RAG case filtering
                "execution_trace": [],
                "tools_used": tools_used,
                # Findings: the rows the lane's data tools returned, as verified observations
                # the next turns re-read (agent/findings.py) — stamped here because the ReAct
                # record carries no execution_trace for the persistence seam to extract from.
                "findings": _findings_mod.from_data_results(result.get("data_results") or []),
                "conversation_agent_events": result.get("collected_events", []),
                "system_events": system_events,  # Session name generation and other system operations (UI replay only)
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": executor.current_provider,
                "model": executor.current_model,
                "profile_tag": profile_tag,
                "profile_type": "conversation_with_tools",
                "duration_ms": duration_ms,
                "session_id": executor.session_id,
                "turn_input_tokens": executor.turn_input_tokens,  # Cumulative turn total (includes all LLM calls like reranking)
                "turn_output_tokens": executor.turn_output_tokens,  # Cumulative turn total
                "turn_cost": turn_cost,  # NEW - Cost at time of execution
                "session_cost_usd": session_cost_usd,  # NEW - Cumulative cost snapshot
                # Session totals at the time of this turn (for plan reload)
                "session_input_tokens": session_input_tokens,
                "session_output_tokens": session_output_tokens,
                # Knowledge retrieval tracking for session reload
                "knowledge_accessed": knowledge_accessed,
                "knowledge_retrieval_event": knowledge_retrieval_event_data,
                # UI-only: Full document chunks for plan reload display (not sent to LLM)
                "knowledge_chunks_ui": knowledge_chunks if knowledge_enabled else [],
                # KG enrichment event for session reload
                "kg_enrichment_event": executor.kg_enrichment_event,
                # Tier 1.1 grounding gate verdict for plan reload + audit (charter #17)
                "grounding": _grounding_event,
                # Tier 1.3 outbound guardrail verdict for plan reload + audit (charter #17)
                "guardrail": _guardrail_event,
                "guardrail_inbound": getattr(executor, "_inbound_guardrail_event", None),
                # Gates whose seam has no turn context record into the per-turn tally;
                # unpacked here so each lands on its own turn key (P1d / assurance registry).
                **_assurance_stamps(),
                # OBDA-READ governed-data lane summary for plan reload + audit (charter #17)
                "obda": _obda_event,
                "compute_lineage": _compute_lineage_event,  # S3 number lineage (reload/audit, #17)
                # Honesty post-condition verdict for plan reload + audit (charter #17)
                "honesty": _honesty_event,
                # F-001 cited-but-unread verdict — its OWN key: a turn runs both
                # branches and one dict cannot carry two verdicts (charter #17)
                "honesty_citation": _citation_event,
                "library_use": getattr(executor, "_library_use", None),  # read → ran per library section (§4a)
                "context_window_snapshot_event": getattr(executor, 'context_window_snapshot_event', None),
                # Pre-processing skills applied to this turn
                "skills_applied": executor.skill_result.to_applied_list() if executor.skill_result and executor.skill_result.has_content else []
            }
            if _provenance_data:
                turn_summary.update(_provenance_data)

            await session_manager.update_last_turn_data(executor.user_uuid, executor.session_id, turn_summary)
            app_logger.info(f"✅ conversation_with_tools execution completed: {len(tools_used)} tools used")

        except Exception as e:
            app_logger.error(f"conversation_with_tools execution error: {e}", exc_info=True)
            from trusted_data_agent.agent import turn_failure as _tf   # C18: never the raw error
            from trusted_data_agent.core.utils import unwrap_exception as _unwrap
            error_msg = _tf.answer(_unwrap(e))
            yield executor._format_sse_with_depth({"step": "Error", "error": error_msg}, "error")

            # Get session data for session cost calculation
            session_data = await session_manager.get_session(executor.user_uuid, executor.session_id)

            # §3.3: turn_cost = Σ per-call costs (dual-model aware) for the error case.
            turn_cost = executor._resolve_turn_cost_usd()  # S2: the ReAct steps accumulate through cost_sink (cache-priced)
            app_logger.debug(f"[conversation_with_tools-error] Turn {executor.current_turn_number} cost (Σ per-call): ${turn_cost:.6f}")

            # Calculate session cost (cumulative up to and including this turn)
            session_cost_usd = 0.0
            try:
                previous_session_cost = executor._calculate_session_cost_at_turn(session_data)
                session_cost_usd = previous_session_cost + turn_cost
                app_logger.debug(f"[conversation_with_tools-error] Session cost at turn {executor.current_turn_number}: ${session_cost_usd:.6f}")
            except Exception as e:
                app_logger.warning(f"Failed to calculate session cost for error case: {e}")

            # Save error turn data
            turn_summary = {
                "turn": executor.current_turn_number,
                "user_query": executor.original_user_input,
                "final_summary_text": error_msg,
                "status": "failed",
                "is_session_primer": executor.is_session_primer,  # Flag for RAG case filtering
                "error": str(e),
                # The model that SERVED the failed turn: without it the consumption row was
                # recorded as Unknown/unknown and its real spend was billed to no model (the
                # trust campaign's B4 timeout: $0.14 on "Unknown").
                "provider": executor.served_provider_model()[0],
                "model": executor.served_provider_model()[1],
                "profile_tag": profile_config.get("tag", "CONV"),
                "profile_type": "conversation_with_tools",
                "duration_ms": executor._turn_duration_ms(),
                "turn_input_tokens": executor.turn_input_tokens,  # Accumulated tokens up to failure
                "turn_output_tokens": executor.turn_output_tokens,
                "turn_cost": turn_cost,  # NEW - Cost at time of error
                "session_cost_usd": session_cost_usd,  # NEW - Cumulative cost snapshot
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "skills_applied": executor.skill_result.to_applied_list() if executor.skill_result and executor.skill_result.has_content else []
            }
            await session_manager.update_last_turn_data(executor.user_uuid, executor.session_id, turn_summary)


# ---------------------------------------------------------------------------
# Platform connector tools — LangChain wrapper
# ---------------------------------------------------------------------------

async def _load_connector_tools_for_langchain(profile_id: str, user_uuid: str) -> list:
    """
    Return LangChain StructuredTool objects for all active platform connector
    tools on this profile.  Calls invoke_connector_tool() for actual execution
    so the connector strategy layer handles transport + user token injection.
    """
    try:
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field, create_model
        from trusted_data_agent.core.platform_connector_registry import (
            get_effective_tools, list_installed_servers, invoke_connector_tool
        )
    except ImportError as e:
        app_logger.debug(f"Platform connector LangChain tools unavailable: {e}")
        return []

    tool_schemas = get_effective_tools(profile_id)
    if not tool_schemas:
        return []

    # Build tool_name → server_id lookup from installed servers.
    # manifest_tools holds original names (e.g. read_emails); get_effective_tools()
    # returns namespaced names (e.g. google__read_emails).  Map both forms so the
    # lookup succeeds regardless of which form tool_name uses.
    from trusted_data_agent.core.platform_connector_registry import _namespace_tool
    tool_to_server: dict[str, str] = {}
    for server in list_installed_servers():
        if not server.get("enabled"):
            continue
        sid = server["id"]
        for t in (server.get("manifest_tools") or []):
            tool_to_server[t] = sid
            tool_to_server[_namespace_tool(sid, t)] = sid

    lc_tools = []
    for schema in tool_schemas:
        tool_name = schema["name"]
        server_id = tool_to_server.get(tool_name)
        if not server_id:
            continue

        # Build Pydantic model from argument schema
        field_defs: dict = {}
        for arg in schema.get("arguments", []):
            py_type = {"integer": int, "number": float, "boolean": bool}.get(
                arg.get("type", "string"), str
            )
            is_required = arg.get("required", False)
            field_defs[arg["name"]] = (
                py_type if is_required else (py_type | None),
                Field(default=... if is_required else None, description=arg.get("description", "")),
            )
        args_schema = create_model(f"{tool_name}_schema", **field_defs) if field_defs else None

        # Closure captures server_id, tool_name, user_uuid
        async def _invoke(
            _sid=server_id, _tname=tool_name, _uuid=user_uuid, **kwargs
        ) -> str:
            import json as _json
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            try:
                result = await invoke_connector_tool(_sid, _tname, kwargs, user_uuid=_uuid)
                return _json.dumps(result) if not isinstance(result, str) else result
            except Exception as exc:
                return _json.dumps({"error": str(exc)})

        lc_kwargs = dict(
            coroutine=_invoke,
            name=tool_name,
            description=schema.get("description", tool_name),
        )
        if args_schema:
            lc_kwargs["args_schema"] = args_schema

        lc_tools.append(StructuredTool.from_function(**lc_kwargs))

    return lc_tools
