"""
Conversation Agent Executor
===========================

Provides a LangChain-based agent for conversational tool use with visual cards.

This module implements a simpler, more conversational approach to MCP tool usage
compared to the multi-phase planner/executor architecture. It uses LangChain's
native tool-calling capabilities for efficient, single-loop agent execution.

Architecture:
- ConversationAgentExecutor: Main class that builds and executes a LangChain agent
- Emits SSE events for real-time UI updates (visual tool cards)
- Supports streaming execution with intermediate step tracking

Usage:
    from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor

    executor = ConversationAgentExecutor(
        profile=profile_config,
        user_uuid=user_uuid,
        session_id=session_id,
        llm_instance=langchain_llm,
        mcp_tools=tools,
        event_callback=emit_sse_event
    )

    result = await executor.execute("Show me all products with low inventory")
"""

import asyncio
import json
import logging
import re
import os
import time
from typing import Dict, List, Any, Optional, Callable

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent

from trusted_data_agent.agent.profile_prompt_resolver import ProfilePromptResolver

from trusted_data_agent.llm.response_text import text_from_content

logger = logging.getLogger(__name__)

# LLM conversation loggers for debugging/auditing
llm_logger = logging.getLogger("llm_conversation")
llm_history_logger = logging.getLogger("llm_conversation_history")


def turn_input_token_budget() -> int:
    """The per-turn input-token budget of the ReAct loop (0 = unbounded). Env
    ``TDA_REACT_TURN_INPUT_TOKEN_BUDGET``; the default is generous for a legitimate
    multi-step turn and far below the runaway it exists to stop."""
    try:
        return max(0, int(os.environ.get("TDA_REACT_TURN_INPUT_TOKEN_BUDGET", "600000")))
    except ValueError:
        return 600000


class ConversationAgentExecutor:
    """
    LangChain-based agent for conversational tool use with visual cards.

    This executor provides a simpler alternative to the planner/executor architecture,
    using LangChain's native tool-calling for a more natural conversational flow.

    Features:
    - Single-loop ReAct agent (vs multi-phase planning)
    - Real-time SSE events for visual tool cards
    - Native function calling (OpenAI, Anthropic, Google)
    - Profile-aware tool filtering
    - Streaming execution support
    """

    def __init__(
        self,
        profile: Dict[str, Any],
        user_uuid: str,
        session_id: str,
        llm_instance: Any,
        mcp_tools: List[Any],
        event_callback: Optional[Callable[[str, Dict], None]] = None,
        async_event_handler: Optional[Callable] = None,
        max_iterations: int = 5,
        conversation_history: Optional[List[Dict]] = None,
        knowledge_context: Optional[str] = None,
        document_context: Optional[str] = None,
        multimodal_content: Optional[List[Dict]] = None,
        turn_number: Optional[int] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        canvas_context: Optional[str] = None,
        component_instructions: Optional[str] = None,
        profile_type: Optional[str] = None,
        provenance: Optional[Any] = None,
        skill_context: Optional[str] = None,
        source_note: Optional[str] = None,
        answer_stream: Optional[Any] = None,
        tool_surface: Optional[Any] = None,   # S6: the ambient TurnSurface (the model binds offered ∪ widened)
        cost_sink: Optional[Callable[[float], None]] = None,
    ):
        """
        Initialize the Conversation Agent Executor.

        Args:
            profile: The profile configuration dictionary
            user_uuid: User UUID who owns the session
            session_id: The current session ID
            llm_instance: LangChain-compatible LLM instance
            mcp_tools: List of LangChain tool objects
            event_callback: Optional callback for real-time UI events (sync).
                           Signature: (event_type: str, payload: dict) -> None
            async_event_handler: Optional async callback for real-time SSE events.
                           Signature: async (payload: dict, event_type: str) -> None
                           When provided, events are emitted in real-time via asyncio.create_task()
            max_iterations: Maximum agent iterations (default: 5)
            conversation_history: Optional list of previous messages for context
            knowledge_context: Optional pre-retrieved knowledge context to inject
            document_context: Optional extracted text from uploaded documents
            multimodal_content: Optional list of native multimodal blocks for providers that support it
            turn_number: Optional turn number for status title tracking in the UI
            provider: Optional provider name for event tracking (dual-model feature)
            model: Optional model name for event tracking (dual-model feature)
            canvas_context: Optional formatted canvas context string for bidirectional context
            component_instructions: Optional pre-computed component instructions from CW module
        """
        self.profile = profile
        self.profile_id = profile.get("id")
        self.profile_tag = profile.get("tag", "CONV")
        # S1 The Governed Stream: the turn's delta sink (None ⇒ streaming off). Model text
        # streams as provisional answer; a model message that ends in tool calls is a
        # decision, not the answer, so its text is withdrawn.
        self.answer_stream = answer_stream
        # S2: every priced LLM call is handed to the turn's cost accumulator (the executor's
        # `turn_cost_usd`), so the persisted turn_cost is the Σ of cache-priced calls — never a
        # single-model re-price that forgets what the provider served from cache.
        self.cost_sink = cost_sink
        self.user_uuid = user_uuid
        self.session_id = session_id
        self.inflight_tool = None  # (tool_name, started_at) while a tool call runs
        self.llm_instance = llm_instance
        # Agent-authority + preflight enforcement (Tier 5 Step 4 / P3): the ReAct engine's MCP
        # tools execute through the LangChain session and never reach invoke_mcp_tool, so wrap
        # each tool's coroutine here — the one seam where both the tools and session_id are
        # known — to reach the same fail-closed chokepoint as the Optimize/genie path.
        try:
            from trusted_data_agent.agent import tool_enforcement as _tool_enforcement
            self.mcp_tools = _tool_enforcement.wrap_langchain_mcp_tools(
                mcp_tools, session_id, user_uuid=user_uuid)
        except Exception as _wrap_err:  # never let wrapping break agent construction
            logger.warning(f"[ConvAgent] tool enforcement wrapping skipped: {_wrap_err}")
            self.mcp_tools = mcp_tools
        # PROP-006 phase 2 — which MCP server each tool belongs to (its own metadata, set when the
        # server's tools were loaded), so a multi-server agent's tool steps name the server they
        # went to. Connector and component tools carry no server and are never named.
        self._tool_server = {}
        for _t in (self.mcp_tools or []):
            try:
                _srv = (getattr(_t, "metadata", None) or {}).get("uderia_mcp_server_id")
                if _srv and getattr(_t, "name", None):
                    self._tool_server[_t.name] = str(_srv)
            except Exception:  # noqa: BLE001
                pass
        self.event_callback = event_callback
        self.async_event_handler = async_event_handler
        self.max_iterations = max_iterations
        self.conversation_history = conversation_history or []
        self.knowledge_context = knowledge_context
        self.skill_context = skill_context
        self.source_note = source_note
        self.tool_surface = tool_surface
        self.document_context = document_context
        self.multimodal_content = multimodal_content
        self.canvas_context = canvas_context
        self.component_instructions = component_instructions
        self.turn_number = turn_number
        self.provider = provider or "Unknown"
        self.model = model or "Unknown"
        self.profile_type = profile_type or "conversation_with_tools"
        self.provenance = provenance  # EPC: Provenance chain from executor

        # Collect events during execution for session storage/replay
        self.collected_events = []

        # Collect component render payloads (chart specs, code blocks, etc.)
        # Extracted from tool results so the executor can generate rendering HTML.
        self.component_payloads: list = []

        # The DATA a tool returned this turn — the structured rows, captured ONCE at the
        # tool-end seam (never from the 5,000-char preview the event carries), in the
        # formatter's `collected_data` shape so the engine renders them as the SAME
        # `Data` evidence row the Optimize lane renders (docs/roadmap/ANSWER_SURFACE_GRAMMAR.md).
        # Bounded: DATA_RESULT_MAX_ROWS rows per statement, DATA_RESULT_MAX_STATEMENTS statements.
        self.data_results: list = []
        self._open_data_args: dict = {}   # tool name → FIFO of the arguments of its open calls

        # Track tool execution state
        self.tools_invoked = []
        self.tool_start_times = {}

        # Track component-internal LLM token usage (not captured by LangChain callbacks)
        self._component_llm_input_tokens = 0
        self._component_llm_output_tokens = 0

        # Running cost of THIS run, for the in-flight per-run budget cap (Agent Authority).
        # The cap was enforced only on the PlanExecutor path; the ReAct engine (this class)
        # escaped it — the overrun was flagged post-hoc once the money was spent. This lets
        # the same predicate stop the next model call before it is paid for. No ambient
        # principal (interactive turn) → the check is a no-op → baseline behaviour.
        self._run_cost_usd = 0.0

        # Track LLM call count for step naming
        self.llm_call_count = 0

        # Build the agent
        self.system_prompt = self._load_system_prompt()
        self.agent_executor = self._build_agent()

        logger.info(
            f"ConversationAgentExecutor initialized: "
            f"profile={self.profile_tag}, tools={len(self.mcp_tools)}, "
            f"max_iterations={self.max_iterations}"
        )

    def _with_source(self, payload: dict, tool_name, completed: bool = False) -> dict:
        """``payload`` plus the server the tool call went to (``source``) — a multi-server
        agent's MCP tool only; every other event is returned unchanged. On a completed call,
        a call Uderia re-routed (B10) names the server that really answered."""
        src = self._call_source(tool_name)
        if completed and src:
            try:
                from trusted_data_agent.agent import source_routing as _sr
                info = _sr.take_reroute(tool_name)
                if info:
                    src = _sr.routed_source(info, fallback=src) or src
            except Exception:  # noqa: BLE001
                pass
        if src:
            payload["source"] = src
        return payload

    def _call_source(self, tool_name):
        """The server a multi-server agent's MCP tool call goes to (None otherwise)."""
        sid = (getattr(self, "_tool_server", None) or {}).get(tool_name)
        if not sid:
            return None
        try:
            from trusted_data_agent.core.agent_mcp_servers import call_source as _cs
            return _cs(tool_name, sid)
        except Exception:  # noqa: BLE001
            return None

    async def _emit_event(self, event_type: str, payload: dict):
        """Emit event via callback and collect for replay.

        If async_event_handler is provided, events are awaited directly
        for immediate SSE streaming.
        """
        # Add to collected events for session storage/replay
        self.collected_events.append({
            "type": event_type,
            "payload": dict(payload),
            "timestamp": time.time()
        })

        # Real-time SSE emission via async handler (preferred)
        if self.async_event_handler:
            try:
                # Format as notification event for SSE
                event_data = {
                    "type": event_type,
                    "payload": payload
                }
                logger.info(f"[ConvAgent] Sending {event_type} event via async_event_handler to SSE queue")
                # CRITICAL: Await to ensure event gets into SSE queue
                # The asyncio.sleep(0) calls after _emit_event in the caller allow SSE consumer to process
                await self.async_event_handler(event_data, "notification")
                logger.info(f"[ConvAgent] ✓ Event {event_type} successfully sent to SSE queue")
            except Exception as e:
                logger.warning(f"ConversationAgentExecutor async event handler error: {e}")
        # Fallback to sync callback if no async handler
        elif self.event_callback:
            try:
                self.event_callback(event_type, payload)
            except Exception as e:
                logger.warning(f"ConversationAgentExecutor event callback error: {e}")
        else:
            logger.warning(f"[ConvAgent] No event handler available - event {event_type} will NOT be sent to SSE!")

    async def _emit_status_indicator(self, target: str, status_state: str):
        """Emit a status_indicator_update event directly (not wrapped as notification).

        Status indicator events use a dedicated SSE event type so the frontend
        can update indicator dots (LLM busy/idle, MCP busy/idle) in real-time.
        """
        if self.async_event_handler:
            try:
                await self.async_event_handler(
                    {"target": target, "state": status_state},
                    "status_indicator_update"
                )
            except Exception as e:
                logger.warning(f"ConversationAgentExecutor status indicator error: {e}")

    def _knowledge_section(self) -> str:
        """The retrieved-knowledge block for THIS question (repository chunks and/or the KG
        enrichment), worded exactly as it was when it lived in the system prompt."""
        if not self.knowledge_context:
            return ""
        return (
            "KNOWLEDGE CONTEXT:\n"
            "The following relevant information has been retrieved from knowledge repositories:\n\n"
            f"{self.knowledge_context}\n\n"
            "Use this knowledge to inform your responses. Reference the source documents when appropriate."
        )

    def _load_system_prompt(self) -> str:
        """Load the system prompt for conversation with tools."""
        # Try to load from prompt system
        resolver = ProfilePromptResolver(profile_id=self.profile_id)

        # Try profile-specific prompt first
        prompt = resolver.get_prompt("CONVERSATION_WITH_TOOLS_EXECUTION")

        if not prompt:
            # Fall back to default prompt
            logger.warning("CONVERSATION_WITH_TOOLS_EXECUTION prompt not found, using default")
            prompt = self._get_default_prompt()

        # Inject available tools into prompt (omit section entirely when no tools available)
        _shown = None
        if self.tool_surface is not None:
            _shown = set(self.tool_surface.surface.offered) | set(self.tool_surface.surface.widened)
        # Name AND description, although every listed tool is also bound with its schema: the
        # listing was cut to names once (Oct 2026, ~800 tokens/step) and a weak tool-calling
        # model (Celeris) dropped from 3/9 to 0/9 tool calls on the same prompt — the prose
        # description in the system prompt is what such a model reads; the schema is not enough.
        tool_descriptions = "\n".join([
            f"- {tool.name}: {tool.description}"
            for tool in self.mcp_tools if _shown is None or tool.name in _shown
        ])
        if _shown is not None and self.tool_surface.surface.held:
            tool_descriptions += (f"\n- ({len(self.tool_surface.surface.held)} more tools are held back for this turn — "
                                  f"call TDA_FindTools with what you need to bring the matching ones in)")
        if tool_descriptions:
            prompt = prompt.replace("{tools_context}", f"AVAILABLE TOOLS:\n{tool_descriptions}")
        else:
            prompt = prompt.replace("{tools_context}", "")

        # Inject component instructions (use pre-computed from CW module if available)
        comp_section = self.component_instructions
        if comp_section is None:
            from trusted_data_agent.components.manager import get_component_instructions_for_prompt
            comp_section = get_component_instructions_for_prompt(self.profile_id, self.user_uuid)
        prompt = prompt.replace("{component_instructions_section}", comp_section)

        # Retrieved knowledge (repository chunks + the KG enrichment) is PER-QUESTION and rides
        # the volatile side of the turn — see `_knowledge_section()` / `execute()` — exactly as
        # the Ideate and Focus lanes place it. It used to be appended here, which made the
        # system prompt differ on every turn and defeated the provider's prefix cache (S2).

        # Inject skill content (e.g. teradata-sql-expert). Skills are resolved per-turn
        # by execution_service and combined into skill_context by ConversationEngine.
        # Without this, conversation_with_tools profiles surfaced the "Skills applied"
        # badge but the skill markdown never reached the model.
        if self.skill_context:
            prompt = prompt + "\n\n" + self.skill_context
            logger.info(f"Injected skill content into system prompt ({len(self.skill_context)} chars)")

        return prompt

    def _get_default_prompt(self) -> str:
        """Get default system prompt for conversation with tools."""
        return """You are a helpful AI assistant with access to tools for retrieving data and performing operations.

YOUR CAPABILITIES:
- You have access to tools for database queries, API calls, and other operations
- When a user request requires external data, use the appropriate tool
- After tool execution, interpret and present results conversationally
- You can chain multiple tool calls if needed

{tools_context}

TOOL USAGE GUIDELINES:
- Only use tools when the user's request requires external data
- Present tool results in a natural, conversational way
- If a tool fails, explain the issue and suggest alternatives
- For multi-step queries, execute tools in sequence and synthesize results

CONVERSATION CONTEXT:
- You have access to the complete conversation history
- Reference previous results when relevant
- Be aware of profile context switches

RESPONSE FORMAT:
- Use markdown for formatting (rendered in UI)
- Present data clearly with tables when appropriate
- Cite tool results naturally in your response
"""

    def _build_agent(self):
        """Build the LangGraph react agent.

        S6 — with a governed tool surface armed, the model is a DYNAMIC model (LangGraph's
        `(state, runtime) -> Runnable` form): on every call it binds only the tools the surface
        OFFERS plus those the model has FOUND so far, while the ToolNode holds every tool — so a
        held tool's schema never enters the prefix until it is found, and once found it executes
        through the same guarded coroutine as any other. `TDA_FindTools` rides in the bound set.
        Without a surface the static form (today's) is used unchanged."""
        if self.tool_surface is None:
            return create_react_agent(
                model=self.llm_instance,
                tools=self.mcp_tools
            )
        surface = self.tool_surface.surface
        all_tools = list(self.mcp_tools)

        def _bound_model(state, runtime):
            shown = set(surface.offered) | set(surface.widened)
            bound = [t for t in all_tools if getattr(t, "name", "") in shown]
            try:
                return self.llm_instance.bind_tools(bound)
            except Exception:  # noqa: BLE001 — a model that cannot bind falls back to everything
                return self.llm_instance.bind_tools(all_tools)

        return create_react_agent(model=_bound_model, tools=all_tools)

    async def execute(self, query: str) -> Dict[str, Any]:
        """
        Execute the agent for a user query.

        Args:
            query: The user's question

        Returns:
            Dict with:
                - response: The final response text
                - tools_used: List of tools that were called
                - collected_events: Events for session storage
                - success: Boolean indicating success
        """
        start_time = time.time()
        logger.info(f"ConversationAgentExecutor executing query: {query[:100]}...")
        # Arm the per-turn rewrite ledger the MCP tool wrapper writes into (a deterministic
        # rewrite — TOP placement, reserved alias, text-stored date, fan-out join — is a
        # STEP of this turn; it is drained after each tool call into an event that persists).
        try:
            from trusted_data_agent.agent import tool_enforcement as _tool_enforcement
            _tool_enforcement.begin_rewrite_capture()
        except Exception:
            pass

        # Emit agent start event (carries combined data for single Live Status card)
        await self._emit_event("conversation_agent_start", {
            "session_id": self.session_id,
            "profile_tag": self.profile_tag,
            "profile_type": self.profile_type,
            "available_tools": [tool.name for tool in self.mcp_tools],
            "query": query[:200] if query else "",
            "turn_id": self.turn_number,
        })

        try:
            # Build input messages
            # Add conversation history if available
            logger.info(f"[ConvAgent] Adding {len(self.conversation_history)} history messages")
            _history_msgs = []
            from trusted_data_agent.agent import agent_history as _ah
            for i, msg in enumerate(self.conversation_history):
                # The one role rule (agent_history.role_of): a stored answer under Gemini's `model`
                # role is an AI message — it used to be dropped silently.
                role = _ah.role_of(msg)
                content = _ah.text_of(msg)
                logger.debug(f"[ConvAgent] History[{i}] role={role}, content (first 50 chars): {content[:50] if content else 'EMPTY'}")
                if role == "user":
                    _history_msgs.append(HumanMessage(content=content))
                elif role == "assistant":
                    _history_msgs.append(AIMessage(content=content))
            # S2 The Attested Cache: the stable prefix (system + history) is marked for the
            # provider's cache; with caching off these are the plain messages as before.
            from trusted_data_agent.agent import prompt_cache as _pc
            _sys_msg, _history_msgs, self._cache_prefix_hash, self._cache_pol = _pc.langchain_prefix(
                self.system_prompt, _history_msgs, profile=self.profile, provider=self.provider, model=self.model)
            messages = [_sys_msg] + _history_msgs

            # Add current query (with knowledge, canvas context, document context, and/or
            # multimodal content if present). Retrieved knowledge leads the volatile message
            # so the system block stays a stable, cacheable prefix.
            effective_query = query
            if getattr(self, "_act_correction", None):
                # the recovery run (below): the platform's correction on the user side of the
                # turn — the cached system prompt is untouched
                effective_query = f"{effective_query}\n\n[Platform note] {self._act_correction}"
            if getattr(self, "source_note", None):
                # B9: one of the agent's servers could not be reached — the user side of the turn,
                # so the cached system prompt is untouched (only on a turn where a server failed)
                effective_query = f"{self.source_note}\n\n{effective_query}"
            if self.canvas_context:
                effective_query = f"{self.canvas_context}\n\n{effective_query}"
                logger.info(f"Prepended canvas context ({len(self.canvas_context):,} chars) to query for conversation agent")
            _labelled = False
            if self.document_context:
                effective_query = f"[User has uploaded documents]\n{self.document_context}\n\n[User's question]\n{effective_query}"
                _labelled = True
                logger.info(f"Prepended document context ({len(self.document_context):,} chars) to query for conversation agent")
            _knowledge = self._knowledge_section()
            if _knowledge:
                effective_query = f"{_knowledge}\n\n{effective_query}" if _labelled else f"{_knowledge}\n\n[User's question]\n{effective_query}"
                logger.info(f"Prepended knowledge context ({len(self.knowledge_context):,} chars) to query for conversation agent")

            # Build multimodal HumanMessage if native content blocks are available
            if self.multimodal_content:
                import base64
                content_blocks = [{"type": "text", "text": effective_query}]
                for block in self.multimodal_content:
                    if block["type"] == "image":
                        try:
                            from trusted_data_agent.filestore.session_files import block_bytes as _block_bytes
                            b64 = base64.b64encode(_block_bytes(block)).decode("utf-8")
                            content_blocks.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:{block['mime_type']};base64,{b64}"}
                            })
                            logger.info(f"[ConvAgent/Multimodal] Added native image: {block['filename']}")
                        except Exception as img_err:
                            logger.warning(f"[ConvAgent/Multimodal] Failed to load image {block['filename']}: {img_err}")
                    # PDFs/documents: LangChain doesn't support universal document blocks
                    # These are handled by text extraction fallback in document_context
                logger.info(f"[ConvAgent] Adding multimodal query with {len(content_blocks)} block(s)")
                messages.append(HumanMessage(content=content_blocks))
            else:
                logger.info(f"[ConvAgent] Adding current query: {effective_query[:100] if effective_query else 'EMPTY'}...")
                messages.append(HumanMessage(content=effective_query))

            # Log all HumanMessage contents to identify duplicates
            human_messages = [m.content[:100] for m in messages if isinstance(m, HumanMessage)]
            logger.info(f"[ConvAgent] Total HumanMessages being sent: {len(human_messages)}")
            for i, hm in enumerate(human_messages):
                logger.info(f"[ConvAgent] HumanMessage[{i}]: {hm}...")

            # Log the conversation for debugging/auditing
            history_str = "\n".join([
                f"[{msg.type.upper()}]: {msg.content[:200]}..." if len(msg.content) > 200 else f"[{msg.type.upper()}]: {msg.content}"
                for msg in messages[1:]  # Skip system prompt in summary
            ])
            llm_history_logger.info(
                f"--- CONVERSATION AGENT EXECUTION ---\n"
                f"Profile: @{self.profile_tag}\n"
                f"Tools Available: {len(self.mcp_tools)}\n"
                f"History Messages: {len(self.conversation_history)}\n\n"
                f"--- SYSTEM PROMPT ---\n{self.system_prompt[:500]}...\n\n"
                f"--- CONVERSATION ---\n{history_str}\n"
                + "-" * 50
            )

            # Execute agent with streaming for tool call tracking
            tools_used = []
            final_response = ""
            last_ai_finish = ""
            total_input_tokens = 0
            total_output_tokens = 0

            # Track the last non-TDA_ data tool call for MCP-only tile replay.
            # If exactly one data tool is used in the turn, tiles can be refreshed
            # directly without the LLM.  Reset to None if > 1 data tool is seen.
            _last_data_tool_name = None
            _last_data_tool_args = None
            _data_tool_count = 0

            # Track LLM call timing
            llm_start_time = None
            budget_stopped = False   # set if the per-run budget cap halts the run in flight
            token_budget_stopped = False  # set if the per-turn input-token budget halts the run

            # Use astream_events to track tool calls in real-time
            # Pass recursion_limit in config to control max agent iterations
            # A value of N allows roughly N/2 tool invocations since each iteration is tool_call -> tool_result
            async for event in self.agent_executor.astream_events(
                {"messages": messages},
                version="v2",
                config={"recursion_limit": self.max_iterations * 2}
            ):
                event_kind = event.get("event", "")
                event_name = event.get("name", "")
                event_data = event.get("data", {})

                # Track LLM start time and signal busy indicator
                if event_kind in ("on_llm_start", "on_chat_model_start"):
                    # In-flight per-run budget cap — asked BEFORE this model call is paid for,
                    # using the same predicate as the post-hoc record. Autonomous turns only
                    # (no ambient principal on an interactive turn → no-op → baseline). Halts
                    # the run with a named, legible stop rather than a silent one.
                    try:
                        from trusted_data_agent.core import agent_authority as _aa
                        _ok, _why = _aa.check_budget_before_call(self._run_cost_usd)
                        if not _ok:
                            budget_stopped = True
                            logger.warning(f"[ConvAgent] per-run budget cap halted the run: {_why}")
                            await self._emit_event("agent_budget_exceeded", {
                                "reason": _why, "cost_usd": self._run_cost_usd,
                                "session_id": self.session_id, "profile_tag": self.profile_tag,
                            })
                            break
                    except Exception as _bud_err:  # a budget-check bug must never break a turn
                        logger.debug(f"[ConvAgent] budget check skipped: {_bud_err}")
                    # Per-TURN input-token budget — every turn, every principal. The loop
                    # re-sends its whole scratchpad on each step, so a turn's cost grows with
                    # the square of its steps; the whole-turn TIME cap does not bound it
                    # (measured: 2.88M input tokens in 253 s). Asked before the call is paid
                    # for; ends the run with a named, legible stop, keeping what was produced.
                    _turn_budget = turn_input_token_budget()
                    if _turn_budget and total_input_tokens >= _turn_budget:
                        token_budget_stopped = True
                        logger.warning(f"[ConvAgent] per-turn input-token budget halted the run: "
                                       f"{total_input_tokens:,} >= {_turn_budget:,}")
                        await self._emit_event("agent_budget_exceeded", {
                            "reason": f"turn input tokens {total_input_tokens:,} reached the budget of {_turn_budget:,}",
                            "input_tokens": total_input_tokens, "budget": _turn_budget,
                            "session_id": self.session_id, "profile_tag": self.profile_tag,
                        })
                        break
                    llm_start_time = time.time()
                    await self._emit_status_indicator("llm", "busy")
                    logger.debug(f"[ConvAgent] LLM call started at {llm_start_time}")

                # S1: provisional answer text, as the model writes it
                if event_kind == "on_chat_model_stream" and self.answer_stream is not None:
                    try:
                        _chunk = event_data.get("chunk")
                        _t = text_from_content(getattr(_chunk, "content", None))
                        if _t:
                            self.answer_stream.delta(_t)
                    except Exception as _sd:  # noqa: BLE001 — a delta must never break a turn
                        logger.debug(f"[ConvAgent] stream delta skipped: {_sd}")

                # Track LLM events - try multiple event types
                # Some providers emit on_llm_end, others emit on_chat_model_end
                if event_kind in ("on_llm_end", "on_chat_model_end"):
                    await self._emit_status_indicator("llm", "idle")
                    output = event_data.get("output", {})

                    # Try multiple ways to get token usage from LangChain
                    tokens_found = False
                    input_tokens = 0
                    output_tokens = 0

                    # Method 1: usage_metadata attribute (newer LangChain format)
                    if hasattr(output, 'usage_metadata'):
                        usage = output.usage_metadata
                        logger.info(f"[ConvAgent] Found usage_metadata (raw): {usage}, type: {type(usage)}")
                        if not usage:
                            logger.warning(f"[ConvAgent] usage_metadata exists but is empty/None for FriendliAI")
                        if usage:
                            # Handle both dict and object formats
                            if isinstance(usage, dict):
                                # Try LangChain standard field names first
                                input_tokens = usage.get('input_tokens', 0) or 0
                                output_tokens = usage.get('output_tokens', 0) or 0
                                # Fallback to OpenAI field names (for FriendliAI and other OpenAI-compatible APIs)
                                if not input_tokens and not output_tokens:
                                    input_tokens = usage.get('prompt_tokens', 0) or 0
                                    output_tokens = usage.get('completion_tokens', 0) or 0
                            else:
                                # Try LangChain standard attribute names first
                                input_tokens = getattr(usage, 'input_tokens', 0) or 0
                                output_tokens = getattr(usage, 'output_tokens', 0) or 0
                                # Fallback to OpenAI attribute names (for FriendliAI and other OpenAI-compatible APIs)
                                if not input_tokens and not output_tokens:
                                    input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
                                    output_tokens = getattr(usage, 'completion_tokens', 0) or 0
                            if input_tokens or output_tokens:
                                total_input_tokens += input_tokens
                                total_output_tokens += output_tokens
                                tokens_found = True
                                logger.info(f"[ConvAgent] Tokens from usage_metadata: input={input_tokens}, output={output_tokens}")

                    # Method 2: response_metadata attribute
                    if not tokens_found and hasattr(output, 'response_metadata') and output.response_metadata:
                        meta = output.response_metadata
                        logger.info(f"[ConvAgent] Found response_metadata keys: {meta.keys() if isinstance(meta, dict) else 'not a dict'}")

                        # Try different key names used by various providers
                        for usage_key in ['token_usage', 'usage', 'usage_metadata']:
                            if usage_key in meta:
                                usage = meta[usage_key]
                                logger.info(f"[ConvAgent] Found usage in response_metadata[{usage_key}]: {usage}")
                                input_tokens = usage.get('prompt_tokens', 0) or usage.get('input_tokens', 0) or 0
                                output_tokens = usage.get('completion_tokens', 0) or usage.get('output_tokens', 0) or 0
                                if input_tokens or output_tokens:
                                    total_input_tokens += input_tokens
                                    total_output_tokens += output_tokens
                                    tokens_found = True
                                    logger.info(f"[ConvAgent] Tokens from response_metadata: input={input_tokens}, output={output_tokens}")
                                    break

                    # Method 3: Check if output is a list of messages (LangChain AIMessage with usage)
                    if not tokens_found and hasattr(output, 'generations'):
                        for gen in output.generations:
                            if hasattr(gen, 'message') and hasattr(gen.message, 'usage_metadata'):
                                usage = gen.message.usage_metadata
                                if usage:
                                    # Handle both dict and object formats
                                    if isinstance(usage, dict):
                                        # Try LangChain standard field names first
                                        input_tokens = usage.get('input_tokens', 0) or 0
                                        output_tokens = usage.get('output_tokens', 0) or 0
                                        # Fallback to OpenAI field names (for FriendliAI and other OpenAI-compatible APIs)
                                        if not input_tokens and not output_tokens:
                                            input_tokens = usage.get('prompt_tokens', 0) or 0
                                            output_tokens = usage.get('completion_tokens', 0) or 0
                                    else:
                                        # Try LangChain standard attribute names first
                                        input_tokens = getattr(usage, 'input_tokens', 0) or 0
                                        output_tokens = getattr(usage, 'output_tokens', 0) or 0
                                        # Fallback to OpenAI attribute names (for FriendliAI and other OpenAI-compatible APIs)
                                        if not input_tokens and not output_tokens:
                                            input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
                                            output_tokens = getattr(usage, 'completion_tokens', 0) or 0
                                    if input_tokens or output_tokens:
                                        total_input_tokens += input_tokens
                                        total_output_tokens += output_tokens
                                        tokens_found = True
                                        logger.info(f"[ConvAgent] Tokens from generations: input={input_tokens}, output={output_tokens}")
                                        break

                    if not tokens_found:
                        # Log full output structure for debugging
                        logger.warning(f"[ConvAgent] Could not find token usage. Output attrs: {dir(output) if hasattr(output, '__dict__') else 'N/A'}")

                    # Emit LLM step event for Live Status display
                    self.llm_call_count += 1

                    # Calculate LLM call duration
                    duration_ms = 0
                    if llm_start_time is not None:
                        duration_ms = int((time.time() - llm_start_time) * 1000)
                        logger.debug(f"[ConvAgent] LLM call duration: {duration_ms}ms")

                    # Determine step type based on output content
                    # If output has tool_calls, it's Tool Selection; otherwise it's Response Generation
                    has_tool_calls = hasattr(output, 'tool_calls') and output.tool_calls
                    if has_tool_calls and self.answer_stream is not None:
                        # S1: a message that ends in tool calls is a decision, not the answer
                        try:
                            if self.answer_stream.raw_text:
                                self.answer_stream.reset()
                        except Exception:  # noqa: BLE001
                            pass
                    if self.llm_call_count == 1:
                        step_name = "Tool Selection" if has_tool_calls else "Response Generation"
                    else:
                        step_name = "Response Generation" if not has_tool_calls else f"Tool Selection #{self.llm_call_count}"

                    # Calculate cost for this LLM call — under the one cache-aware rule (S2):
                    # the cached prefix at the provider's cached rate, a write at its surcharge.
                    from trusted_data_agent.agent import prompt_cache as _pc
                    _cu = _pc.langchain_usage(getattr(output, "usage_metadata", None), getattr(output, "response_metadata", None))
                    _cu["input"], _cu["output"] = input_tokens, output_tokens
                    _pol = getattr(self, "_cache_pol", None) or {}
                    # S4: a call the SIGNED fallback served is priced at the model that served —
                    # the envelope names it on the message (`uderia_served_by`); else the lane's own.
                    _served = ((getattr(output, "response_metadata", None) or {}).get("uderia_served_by") or {})
                    _srv_provider = _served.get("provider") or self.provider
                    _srv_model = _served.get("model") or self.model
                    call_cost = _pc.priced(_srv_provider, _srv_model, _cu, _pol.get("ttl", "5m"))
                    cached_tokens = _cu["cached"]
                    self.total_cached_tokens = getattr(self, "total_cached_tokens", 0) + cached_tokens
                    _pc.record_call(provider=self.provider, model=self.model, input_tokens=input_tokens,
                                    output_tokens=output_tokens, cached=cached_tokens, written=_cu["written"],
                                    prefix_hash=getattr(self, "_cache_prefix_hash", None))
                    from trusted_data_agent.agent import turn_budget as _tbud
                    _tbud.record_tokens(input_tokens + output_tokens)   # S5: the tokens unit
                    # Accumulate the run cost so the next model-start can enforce the cap.
                    self._run_cost_usd += (call_cost or 0.0)
                    if self.cost_sink is not None:
                        try:
                            self.cost_sink(call_cost or 0.0)
                        except Exception:  # noqa: BLE001 — accounting must never break a turn
                            pass

                    # One identity for this LLM call, shared by the step event and the
                    # token_update below, so the frontend's per-call cost ledger counts it
                    # exactly once (not step + token_update, and not once per SSE channel).
                    import uuid as _uuid
                    call_id = str(_uuid.uuid4())

                    await self._emit_event("conversation_llm_step", {
                        "step_number": self.llm_call_count,
                        "step_name": step_name,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cached_tokens": cached_tokens,   # S2: prompt tokens served from cache
                        "duration_ms": duration_ms,
                        "provider": _srv_provider,  # the provider that SERVED (S4: a fallback names itself)
                        "model": _srv_model,        # the model that served
                        "served_by_fallback": bool(_served),
                        "session_id": self.session_id,
                        "call_id": call_id,
                        "cost_usd": call_cost  # NEW: Track cost for all profile types
                    })

                    logger.info(f"[ConvAgent] ✓ Emitted conversation_llm_step event #{self.llm_call_count} ({step_name})")

                    # ROOT accounting rule: persist this LangChain step's tokens to the
                    # session counter EXACTLY ONCE, UNCONDITIONALLY — independent of whether a
                    # live event handler is attached. The agent is the single source of truth
                    # for its own LangChain-call session accounting in BOTH live and headless
                    # modes, so wrapping engines (Focus/Conversation) must NOT re-persist these
                    # tokens (doing so double-counted the session + quota totals). The
                    # token_update *event* below is a UI concern and stays gated on a live handler.
                    if self.session_id and (input_tokens > 0 or output_tokens > 0):
                        try:
                            from trusted_data_agent.core import session_manager
                            # Persist this step's tokens to session DB (single source of truth).
                            await session_manager.update_token_count(
                                self.user_uuid, self.session_id, input_tokens, output_tokens
                            )
                            # Emit incremental token_update so header KPI counters update in
                            # real-time — only when a live SSE handler is attached.
                            if self.async_event_handler:
                                session_data = await session_manager.get_session(self.user_uuid, self.session_id)
                                await self.async_event_handler({
                                    "statement_input": input_tokens,
                                    "statement_output": output_tokens,
                                    "turn_input": total_input_tokens,
                                    "turn_output": total_output_tokens,
                                    "total_input": session_data.get("input_tokens", 0) if session_data else 0,
                                    "total_output": session_data.get("output_tokens", 0) if session_data else 0,
                                    # Same call_id as the conversation_llm_step above (per-call identity).
                                    "call_id": call_id,
                                    "cost_usd": call_cost
                                }, "token_update")
                                logger.info(f"[ConvAgent] ✓ Emitted incremental token_update: stmt={input_tokens}/{output_tokens}, turn={total_input_tokens}/{total_output_tokens}")
                        except Exception as e:
                            logger.warning(f"[ConvAgent] Failed to persist/emit incremental token_update: {e}")

                    # CRITICAL: Yield control to event loop to allow SSE consumer to process queue
                    # Use small non-zero delay to ensure SSE actually gets scheduled
                    await asyncio.sleep(0.001)

                    # Emit LLM synthesis results event when it's a Response Generation (not tool selection)
                    if not has_tool_calls:
                        # Extract response content from the output
                        response_content = ""
                        if hasattr(output, 'content'):
                            # `.content` is a LIST of parts when the model reasoned;
                            # slicing/len() on that reports parts, not characters.
                            response_content = text_from_content(output.content)
                        elif hasattr(output, 'generations') and output.generations:
                            # For ChatResult objects
                            response_content = output.generations[0].text if hasattr(output.generations[0], 'text') else ""

                        # Calculate cost for this LLM completion
                        from trusted_data_agent.core.cost_manager import get_cost_manager
                        cost_manager = get_cost_manager()
                        completion_cost = cost_manager.calculate_cost(
                            provider=self.provider,
                            model=self.model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens
                        )

                        await self._emit_event("conversation_llm_complete", {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "response_preview": response_content[:500] + "..." if len(response_content) > 500 else response_content,
                            "response_length": len(response_content),
                            "session_id": self.session_id,
                            "cost_usd": completion_cost
                        })
                        logger.info(f"[ConvAgent] ✓ Emitted conversation_llm_complete event (response length: {len(response_content)})")
                        await asyncio.sleep(0.001)

                    logger.info(f"[ConvAgent] LLM Step {self.llm_call_count} ({step_name}): {input_tokens} in / {output_tokens} out")

                # Track tool invocations
                if event_kind == "on_tool_start":
                    tool_name = event_name
                    # What is running RIGHT NOW — read by the engine when its whole-turn
                    # cap fires, so the error names a slow tool call instead of blaming
                    # the model for the wait.
                    self.inflight_tool = (tool_name, time.time())
                    # Strip LangChain-injected plumbing (ToolRuntime, callbacks) so the
                    # recorded arguments are only what the model actually chose.
                    tool_input = self._clean_tool_args(event_data.get("input", {}))

                    # Record data tool provenance for MCP-replayable tile refresh.
                    # Only non-TDA_ tools produce data; component tools are excluded.
                    if not tool_name.startswith("TDA_"):
                        self._open_data_args.setdefault(tool_name, []).append(tool_input)
                        _data_tool_count += 1
                        if _data_tool_count == 1:
                            _last_data_tool_name = tool_name
                            _last_data_tool_args = tool_input
                        else:
                            # Multiple data tools → cannot replay as a single call
                            _last_data_tool_name = None
                            _last_data_tool_args = None

                    # Signal MCP busy for status indicator dots (frontend uses "db" target for MCP dot)
                    await self._emit_status_indicator("db", "busy")

                    # Record start time
                    self.tool_start_times[tool_name] = time.time()

                    # EPC: Record agent tool call
                    try:
                        if self.provenance:
                            self.provenance.add_step("agent_tool_call", f"{tool_name}:{json.dumps(tool_input, sort_keys=True, default=str)[:4096]}", f"Calling {tool_name}")
                    except Exception:
                        pass

                    # Emit tool invoked event
                    _invoked = {
                        "tool_name": tool_name,
                        "arguments": self._safe_serialize(tool_input),
                        "session_id": self.session_id
                    }
                    await self._emit_event("conversation_tool_invoked", self._with_source(_invoked, tool_name))

                    # CRITICAL: Yield control to event loop to allow SSE tasks to run
                    await asyncio.sleep(0)

                    logger.info(f"Tool invoked: {tool_name}")

                # Track tool completions
                elif event_kind == "on_tool_end":
                    self.inflight_tool = None
                    # Signal MCP idle for status indicator dots (frontend uses "db" target for MCP dot)
                    await self._emit_status_indicator("db", "idle")
                    tool_name = event_name
                    tool_output = event_data.get("output", "")

                    # Calculate duration
                    start = self.tool_start_times.get(tool_name, time.time())
                    duration_ms = int((time.time() - start) * 1000)

                    # Record tool usage
                    if tool_name not in tools_used:
                        tools_used.append(tool_name)

                    # Emit tool completed event
                    # the tool's TEXT, never the message object's repr: a component tool's
                    # ToolMessage carries its rendered card as an artifact, and str() put that
                    # HTML into Live Status — where it rendered a live Confirm (Act II, prod)
                    output_preview = self._tool_output_text(tool_output)[:5000] if tool_output else ""
                    logger.info(f"[ConvAgent] Tool output preview length: {len(output_preview)} chars")
                    logger.debug(f"[ConvAgent] Tool output preview content: {output_preview[:100]}...")

                    # Derive real success from the output. MCP tools RETURN error payloads
                    # (status:"error" / "[Error NNNN]") rather than raising, so a hardcoded
                    # success:True made the honesty gate (and OBDA-READ) count a failed data query as
                    # succeeded — masking the confident-false-negative case the gate exists for.
                    tool_refusal = self._tool_output_refusal(tool_output)
                    tool_success = not self._tool_output_is_error(tool_output) and tool_refusal is None

                    # EPC: Record agent tool result
                    try:
                        if self.provenance:
                            self.provenance.add_step("agent_tool_result", f"{tool_name}:{str(tool_output)[:4096]}", f"{tool_name} completed ({duration_ms}ms)")
                    except Exception:
                        pass

                    # The ROW COUNT, read once from the full output where it is still
                    # structured: the event otherwise carries only a stringified preview, and
                    # a count taken from a preview is a lower bound (a truncated 30-row
                    # sample would read as fewer rows and be excused as complete). None when
                    # the shape is not readable — never a guess.
                    result_rows = self._tool_output_row_count(tool_output)
                    _completed = {
                        "tool_name": tool_name,
                        "result_preview": output_preview,
                        "duration_ms": duration_ms,
                        "success": tool_success,
                        "session_id": self.session_id
                    }
                    if result_rows is not None:
                        _completed["result_rows"] = result_rows
                    # The STRUCTURED half a component result carries (bounded): the OBDA-READ
                    # scanner pairs a compiled statement with the data call that executed it by
                    # this SQL and seals the consumption receipt from this product identity. A
                    # stringified preview carries none of it, so the conversation lane counted
                    # every governed execution as free-formed and raised a false SURFACE BREACH.
                    _md = self._tool_output_metadata(tool_output)
                    if _md:
                        _completed["metadata"] = _md
                    if tool_refusal:
                        # a refused component call is not "done": the receipt says WHY it was
                        # refused (the model then corrects itself — that next call is the one
                        # that produces evidence)
                        _completed["refused"] = tool_refusal
                    await self._emit_event("conversation_tool_completed", self._with_source(_completed, tool_name, completed=True))
                    logger.info(f"[ConvAgent] Tool completed: {tool_name} ({duration_ms}ms, success={tool_success})")
                    _rewrites = self._rewrite_events_for(tool_name)
                    for _rw in _rewrites:
                        await self._emit_event("conversation_tool_rewrite", _rw)
                    self._capture_data_result(tool_name, tool_output, _rewrites)

                    # Update viz content cache so TDA_Dashboard can auto-populate
                    # 'content' even when called from ConversationAgent (which bypasses
                    # invoke_mcp_tool and therefore the cache update in adapter.py).
                    if tool_name not in ("TDA_Dashboard",) and self.session_id:
                        try:
                            from trusted_data_agent.mcp_adapter.adapter import _viz_content_cache, _extract_first_html
                            import re as _re, json as _json
                            # Normalise tool_output to a plain string.
                            # LangChain ToolMessage.content can be:
                            #   str  — raw JSON string from MCP server
                            #   list — [{"type": "text", "text": "<json>"}]
                            raw_out = ""
                            if hasattr(tool_output, 'content'):
                                _tc = tool_output.content
                                if isinstance(_tc, str):
                                    raw_out = _tc
                                elif isinstance(_tc, list):
                                    # LangChain content blocks: plain dicts, plain strings,
                                    # or typed objects (TextContent) with a .text attribute
                                    for _block in _tc:
                                        if isinstance(_block, dict) and _block.get("type") == "text":
                                            raw_out = _block.get("text", "")
                                            break
                                        elif isinstance(_block, str):
                                            raw_out = _block
                                            break
                                        elif hasattr(_block, 'text'):
                                            raw_out = str(_block.text)
                                            break
                                        elif hasattr(_block, 'content'):
                                            raw_out = str(_block.content)
                                            break
                                    if not raw_out:
                                        raw_out = str(_tc)
                                else:
                                    raw_out = str(_tc)
                            else:
                                raw_out = str(tool_output) if tool_output else ""
                            logger.info(f"[ConvAgent] tool_output type={type(tool_output).__name__} raw_len={len(raw_out)} for {tool_name}")
                            if raw_out:
                                _parsed = None
                                # Match both JSON objects {...} and bare arrays [...]
                                _jmatch = _re.search(r'[\[{].*[\]}]', raw_out, _re.DOTALL)
                                if _jmatch:
                                    try:
                                        _parsed = _json.loads(_jmatch.group(0))
                                    except Exception:
                                        pass
                                if _parsed is not None:
                                    _html = _extract_first_html(_parsed)
                                    if _html:
                                        _viz_content_cache[self.session_id] = _html
                                        logger.info(f"[ConvAgent] Cached HTML from {tool_name} (via JSON, {len(_html)} chars)")
                                elif "<table" in raw_out or "<tr" in raw_out:
                                    _viz_content_cache[self.session_id] = raw_out
                                    logger.info(f"[ConvAgent] Cached HTML from {tool_name} (raw, {len(raw_out)} chars)")
                        except Exception as _cache_err:
                            logger.warning(f"[ConvAgent] Cache update failed for {tool_name}: {_cache_err}")

                    # Detect component render payloads (chart specs, code blocks, etc.)
                    # so the executor can generate data-component-id divs for the frontend.
                    from trusted_data_agent.components.utils import extract_component_payload
                    _payload = extract_component_payload(tool_output)
                    if _payload:
                        # Check if this payload targets a sub_window — emit SSE immediately
                        _render_target = _payload.get("render_target", "inline")
                        if _render_target == "sub_window":
                            _cr_payload = {
                                "component_id": _payload.get("component_id"),
                                "render_target": "sub_window",
                                "spec": _payload.get("spec", {}),
                                "title": _payload.get("title", _payload.get("component_id")),
                                "window_id": _payload.get("window_id", ""),
                                "action": _payload.get("window_action", "create"),
                                "interactive": _payload.get("interactive", False),
                                "session_id": self.session_id,
                            }
                            # Collect for session replay
                            self.collected_events.append({
                                "type": "component_render",
                                "payload": dict(_cr_payload),
                                "timestamp": time.time()
                            })
                            # Emit as first-class SSE event (NOT wrapped in notification)
                            # so the frontend handler at eventHandlers.js:1287 receives
                            # eventName === 'component_render' directly
                            if self.async_event_handler:
                                await self.async_event_handler(_cr_payload, "component_render")
                            logger.info(f"[ConvAgent] Emitted sub_window component_render SSE for {_payload['component_id']}")
                        # Always collect for inline rendering (sub_window payloads filtered out in generate_component_html)
                        # — unless the component opts out (e.g. TDA_Memory: result is conveyed by the
                        # agent's prose + the Agent Memory canvas, so an inline card is redundant).
                        if (_payload.get("metadata") or {}).get("suppress_inline"):
                            logger.info(f"[ConvAgent] Inline render suppressed for {_payload['component_id']}")
                        else:
                            self.component_payloads.append(_payload)
                            logger.info(f"[ConvAgent] Captured component payload: {_payload['component_id']}")

                        # --- Component LLM event & token extraction (mirrors phase_executor:2076) ---
                        _comp_meta = _payload.get("metadata") or {}

                        # Emit piggybacked Live Status events (e.g., chart mapping LLM resolution)
                        _comp_events = _comp_meta.pop("_component_llm_events", None)
                        if _comp_events:
                            for evt in _comp_events:
                                await self._emit_event("component_llm_resolution", evt)
                            logger.info(
                                f"[ConvAgent] Emitted {len(_comp_events)} component LLM event(s) "
                                f"for {_payload['component_id']}"
                            )

                        # Accumulate component LLM tokens (not captured by LangChain callbacks)
                        _comp_in = _comp_meta.get("llm_input_tokens", 0)
                        _comp_out = _comp_meta.get("llm_output_tokens", 0)
                        if _comp_in or _comp_out:
                            self._component_llm_input_tokens += _comp_in
                            self._component_llm_output_tokens += _comp_out
                            logger.info(
                                f"[ConvAgent] Component LLM tokens: {_comp_in} in / {_comp_out} out"
                            )

                    # CRITICAL: Yield control to event loop to allow SSE tasks to run
                    await asyncio.sleep(0)

                    logger.info(f"Tool completed: {tool_name} ({duration_ms}ms)")

                # Track tool errors
                elif event_kind == "on_tool_error":
                    # Signal MCP idle on error too (frontend uses "db" target for MCP dot)
                    await self._emit_status_indicator("db", "idle")
                    tool_name = event_name
                    error = str(event_data.get("error", "Unknown error"))

                    # Calculate duration
                    start = self.tool_start_times.get(tool_name, time.time())
                    duration_ms = int((time.time() - start) * 1000)

                    # Record tool usage (even if failed)
                    if tool_name not in tools_used:
                        tools_used.append(tool_name)

                    # Emit tool error event
                    _failed = {
                        "tool_name": tool_name,
                        "result_preview": f"Error: {error}",
                        "duration_ms": duration_ms,
                        "success": False,
                        "error": error,
                        "session_id": self.session_id
                    }
                    await self._emit_event("conversation_tool_completed", self._with_source(_failed, tool_name))

                    # CRITICAL: Yield control to event loop to allow SSE tasks to run
                    await asyncio.sleep(0)

                    logger.warning(f"Tool error: {tool_name} - {error}")

                # Capture final output and token usage from chain end
                elif event_kind == "on_chain_end" and event_name == "LangGraph":
                    output = event_data.get("output", {})
                    if isinstance(output, dict) and "messages" in output:
                        # Why the LAST model message ended — read below when it carried no text.
                        for msg in reversed(output["messages"]):
                            if getattr(msg, "type", None) == "ai":
                                _meta = getattr(msg, "response_metadata", None) or {}
                                last_ai_finish = str(_meta.get("finish_reason") or "")
                                break
                        # Extract final AI message for response
                        for msg in reversed(output["messages"]):
                            if hasattr(msg, 'content') and hasattr(msg, 'type'):
                                if msg.type == 'ai' and msg.content:
                                    # Extended-thinking content is a list of blocks
                                    # ([{"type":"thinking",...},{"type":"text",...}]);
                                    # `text_from_content` keeps only the answer.
                                    final_response = text_from_content(msg.content)
                                    break

                        # Sum up token usage from ALL AI messages (each LLM call has its own usage)
                        # Only do this if we haven't captured tokens from on_llm_end events
                        if total_input_tokens == 0 and total_output_tokens == 0:
                            chain_input_tokens = 0
                            chain_output_tokens = 0
                            for msg in output["messages"]:
                                if hasattr(msg, 'type') and msg.type == 'ai':
                                    if hasattr(msg, 'usage_metadata'):
                                        usage = msg.usage_metadata
                                        if usage:
                                            # Handle both dict and object formats
                                            if isinstance(usage, dict):
                                                # Try LangChain standard field names first
                                                input_t = usage.get('input_tokens', 0) or 0
                                                output_t = usage.get('output_tokens', 0) or 0
                                                # Fallback to OpenAI field names (for FriendliAI and other OpenAI-compatible APIs)
                                                if not input_t and not output_t:
                                                    input_t = usage.get('prompt_tokens', 0) or 0
                                                    output_t = usage.get('completion_tokens', 0) or 0
                                            else:
                                                # Try LangChain standard attribute names first
                                                input_t = getattr(usage, 'input_tokens', 0) or 0
                                                output_t = getattr(usage, 'output_tokens', 0) or 0
                                                # Fallback to OpenAI attribute names (for FriendliAI and other OpenAI-compatible APIs)
                                                if not input_t and not output_t:
                                                    input_t = getattr(usage, 'prompt_tokens', 0) or 0
                                                    output_t = getattr(usage, 'completion_tokens', 0) or 0
                                            chain_input_tokens += input_t
                                            chain_output_tokens += output_t

                            if chain_input_tokens > 0 or chain_output_tokens > 0:
                                total_input_tokens = chain_input_tokens
                                total_output_tokens = chain_output_tokens
                                logger.debug(f"[ConvAgent] Total tokens from chain messages: input={total_input_tokens}, output={total_output_tokens}")

            # Calculate total duration
            total_duration_ms = int((time.time() - start_time) * 1000)

            # Calculate total cost for this conversation agent execution
            from trusted_data_agent.core.cost_manager import get_cost_manager
            cost_manager = get_cost_manager()
            total_cost = cost_manager.calculate_cost(
                provider=self.provider,
                model=self.model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens
            )

            # Emit agent complete event (carries combined data for single Live Status card)
            await self._emit_event("conversation_agent_complete", {
                "total_duration_ms": total_duration_ms,
                "tools_used": tools_used,
                "success": True,
                "session_id": self.session_id,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "profile_tag": self.profile_tag,
                "profile_type": self.profile_type,
                "cost_usd": total_cost
            })

            logger.info(
                f"ConversationAgentExecutor completed. "
                f"Tools used: {tools_used}, Duration: {total_duration_ms}ms, "
                f"Tokens: {total_input_tokens} in / {total_output_tokens} out"
            )

            # Log the response for debugging/auditing
            llm_logger.info(
                f"--- REASON FOR CALL ---\n"
                f"Conversation Agent Execution (@{self.profile_tag})\n"
                f"--- TOOLS USED ---\n{', '.join(tools_used) if tools_used else 'None'}\n"
                f"--- TOKENS ---\nInput: {total_input_tokens}, Output: {total_output_tokens}\n"
                f"--- RESPONSE ---\n{final_response}\n"
                + "-" * 50
            )

            # Recover leaked component tool calls: weaker models sometimes emit a
            # component call (e.g. TDA_Charting) as plain-text JSON in their answer
            # INSTEAD of invoking it. Execute it for real so the component renders,
            # then strip the JSON. Mirrors _auto_canvas_code_blocks recovery.
            final_response = await self._recover_leaked_component_calls(final_response, tools_used)

            # A leaked SELECT: the answer IS one read statement and no data tool ran — the
            # model wrote the query it meant to run. Run it through the bound read tool (the
            # same guarded coroutine a model call reaches) and answer with the rows. Before
            # auto-canvas, which would otherwise file the statement away as a code canvas.
            final_response = await self._recover_leaked_sql(final_response, tools_used)

            # Auto-canvas: convert markdown code blocks to Canvas component payloads
            # when the LLM failed to use TDA_Canvas directly.
            final_response = self._auto_canvas_code_blocks(final_response)

            # If the per-run budget cap halted the run, say so plainly and keep whatever the
            # agent produced before the ceiling was hit (a partial answer beats a silent stop).
            # S5 — a HARD stop is recorded as a kill on the turn budget (never a graceful finish)
            if budget_stopped or token_budget_stopped:
                try:
                    from trusted_data_agent.agent import turn_budget as _tbud
                    _tbud.mark_killed("cost_cap" if budget_stopped else "input_token_cap")
                except Exception:  # noqa: BLE001
                    pass
            if budget_stopped:
                notice = ("\n\n_This autonomous run reached its configured per-run cost budget "
                          "and was stopped before completing._")
                final_response = (final_response or "").rstrip() + notice
            if token_budget_stopped:
                notice = (f"\n\n_This turn read {total_input_tokens:,} input tokens across its steps and "
                          f"reached the per-turn budget of {turn_input_token_budget():,}; it was stopped "
                          "before completing. Narrow the question or the data it reads (fewer columns, "
                          "a row limit, an aggregate) and ask again._")
                final_response = (final_response or "").rstrip() + notice

            # A turn that ended with NO answer text says why, instead of rendering the
            # formatter's neutral placeholder as if the work were done. The common cause with a
            # reasoning model on a local server: its output ceiling was spent thinking.
            # A component-only turn (a chart, a navigator card) legitimately has no text — but a
            # model CUT OFF at its output limit never wrote the answer it was working towards,
            # whatever cards the earlier steps produced, and the reader must be told.
            if not (final_response or "").strip() and (
                    not self.component_payloads or answer_was_cut_off(last_ai_finish)):
                final_response = empty_answer_notice(last_ai_finish, total_output_tokens)

            # An act claimed but never taken: the model answered "prepared … awaiting your
            # confirmation" without calling TDA_Action (Act II: it copied an earlier turn's
            # answer). Whether this turn holds an action record is a fact the platform has —
            # so the agent runs ONCE more, told plainly that nothing was prepared, and the act
            # is prepared for real (its Confirm appears). The honesty gate is the backstop.
            if (not getattr(self, "_act_retry_done", False) and not (budget_stopped or token_budget_stopped)
                    and any(getattr(t, "name", "") == "TDA_Action" for t in self.mcp_tools)):
                try:
                    from trusted_data_agent.agent.honesty.primitives import claims_act as _claims_act
                    from trusted_data_agent.core import action_perform as _AP
                    if _claims_act(final_response) and _AP.turn_active() and not _AP.turn_actions():
                        self._act_retry_done = True
                        self._act_correction = (
                            "Your previous draft said a write was prepared or is awaiting confirmation, "
                            "but you did NOT call TDA_Action in this turn — nothing was prepared or written. "
                            "Earlier turns' acts are not this turn's. If the user asked for a write, call "
                            "TDA_Action now (perform the matching action); otherwise answer without claiming any act.")
                        logger.warning("[ConvAgent] act claimed without TDA_Action — running once more with the correction")
                        retry = await self.execute(query)
                        retry["input_tokens"] = (retry.get("input_tokens") or 0) + total_input_tokens
                        retry["output_tokens"] = (retry.get("output_tokens") or 0) + total_output_tokens
                        retry["act_recovered"] = True
                        return retry
                except Exception as _ae:  # noqa: BLE001 — the gate still discloses it
                    logger.info(f"[ConvAgent] act-claim recovery skipped: {_ae}")

            return {
                "response": final_response,
                "tools_used": tools_used,
                "collected_events": self.collected_events,
                "component_payloads": self.component_payloads,
                "data_results": self.data_results,
                "success": True,
                "budget_stopped": budget_stopped,
                "token_budget_stopped": token_budget_stopped,
                "duration_ms": total_duration_ms,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "component_llm_input_tokens": self._component_llm_input_tokens,
                "component_llm_output_tokens": self._component_llm_output_tokens,
                # MCP-replayable tile support: populated when exactly one data tool was used
                "last_data_tool_name": _last_data_tool_name,
                "last_data_tool_args": _last_data_tool_args,
            }

        except Exception as e:
            logger.error(f"ConversationAgentExecutor error: {e}", exc_info=True)

            # Calculate duration
            total_duration_ms = int((time.time() - start_time) * 1000)

            # Emit error completion event (carries combined data for single Live Status card)
            await self._emit_event("conversation_agent_complete", {
                "total_duration_ms": total_duration_ms,
                "tools_used": self.tools_invoked,
                "success": False,
                "error": str(e),
                "session_id": self.session_id,
                "profile_tag": self.profile_tag,
                "profile_type": self.profile_type,
            })

            # C18: an honest sentence naming the failure class — never the provider's raw error
            # (it stays in the log above and in `error` below).
            from trusted_data_agent.agent import turn_failure as _tf
            from trusted_data_agent.core.utils import unwrap_exception as _unwrap
            return {
                "response": _tf.answer(_unwrap(e)),
                "tools_used": self.tools_invoked,
                "collected_events": self.collected_events,
                "success": False,
                "error": str(e),
                "duration_ms": total_duration_ms
            }

    # LangChain injects framework plumbing (ToolRuntime, callback/run managers,
    # RunnableConfig) into a tool's input dict via InjectedToolArg. These are NOT
    # arguments the model chose — and `_safe_serialize` stringifies them into giant
    # "ToolRuntime(state=…)" blobs that pollute the persisted trace and mislead the
    # eval judges (tool_arg_optimality flagged them as bad arguments). Drop them so
    # only the model-chosen arguments are recorded.
    _INJECTED_ARG_KEYS = frozenset({"runtime", "run_manager", "callbacks", "callback_manager"})
    _INJECTED_TYPE_HINTS = ("Runtime", "RunnableConfig", "CallbackManager", "Injected")

    DATA_RESULT_MAX_ROWS = 200
    DATA_RESULT_MAX_STATEMENTS = 20

    @classmethod
    def _tool_output_structured(cls, tool_output: Any) -> Optional[dict]:
        """The structured payload of a tool's output (dict, JSON string, content_and_artifact
        tuple, MCP content blocks, or a ToolMessage-like object) — ONE coercion for the row
        count and the row capture, so the two can never disagree about what a tool returned."""
        try:
            from trusted_data_agent.agent.tool_enforcement import _coerce_to_dict
            payload = tool_output
            if not isinstance(payload, (dict, str, list, tuple)):
                art = getattr(payload, "artifact", None)
                structured = getattr(art, "structured_content", None) if art is not None else None
                if isinstance(structured, dict):
                    payload = structured
                elif getattr(payload, "content", None) is not None:
                    payload = getattr(payload, "content")
            d = _coerce_to_dict(payload)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    def _capture_data_result(self, tool_name: str, tool_output: Any, rewrites: Optional[list] = None) -> None:
        """Keep the rows a DATA tool returned, in the formatter's `collected_data` shape
        (`{"results": rows, "metadata": {...}}`), so the answer body carries them as a
        `Data` evidence row — the evidence the answer rests on, marked as tool output, on
        the ReAct lane exactly as on the Optimize lane. Persisted INSIDE the rendered body,
        so a plan reload shows the identical row. A framework/component tool (`TDA_*`), an
        error, or a result with no row list captures nothing. Never raises."""
        try:
            if not tool_name or tool_name.startswith("TDA_"):
                return
            args = None
            q = self._open_data_args.get(tool_name)
            if q:
                args = q.pop(0)
            if len(self.data_results) >= self.DATA_RESULT_MAX_STATEMENTS:
                return
            d = self._tool_output_structured(tool_output)
            if not d or self._tool_output_is_error(tool_output):
                return
            rows = None
            for key in ("results", "rows", "data"):
                v = d.get(key)
                if isinstance(v, list):
                    rows = [r for r in v if isinstance(r, dict)]
                    break
            if not rows:
                return
            meta = {"tool_name": tool_name, "row_count": len(rows),
                    "truncated": len(rows) > self.DATA_RESULT_MAX_ROWS}
            if isinstance(args, dict):
                meta["arguments"] = self._safe_serialize(args)
            for rw in (rewrites or []):
                if isinstance(rw, dict) and rw.get("executed_sql"):
                    meta["executed_sql"] = str(rw["executed_sql"])
                    meta["rewrite_kind"] = rw.get("kind")
            self.data_results.append({"results": rows[: self.DATA_RESULT_MAX_ROWS], "metadata": meta})
        except Exception:
            return

    #: the receipt-relevant keys of a component's structured metadata — a closed set, so
    #: the persisted event stays small and a presentation field can never ride it
    _METADATA_KEYS = ("tool_name", "action", "product", "version", "request_hash", "trust_state",
                      "trust", "sql", "sql_hash", "row_count", "error", "error_code", "refused", "plane",
                      # an authority DENIAL: the scanner counts it as denied, never free-formed
                      "agent_denied", "policy_reason", "terminal")

    @classmethod
    def _tool_output_metadata(cls, tool_output: Any) -> Optional[dict]:
        """The bounded ``metadata`` of a structured tool result (a component's
        ``{type, spec, metadata}`` JSON, a ToolMessage artifact, or a dict), or None."""
        try:
            from trusted_data_agent.agent.tool_enforcement import _coerce_to_dict
            payload = tool_output
            if not isinstance(payload, (dict, str, list, tuple)):
                art = getattr(payload, "artifact", None)
                structured = getattr(art, "structured_content", None) if art is not None else None
                payload = structured if isinstance(structured, dict) else getattr(payload, "content", payload)
            d = _coerce_to_dict(payload)
            md = d.get("metadata") if isinstance(d, dict) else None
            if not isinstance(md, dict):
                return None
            out = {k: md[k] for k in cls._METADATA_KEYS if k in md and md[k] is not None}
            if isinstance(out.get("sql"), str) and len(out["sql"]) > 20000:
                out["sql"] = out["sql"][:20000]
            return out or None
        except Exception:  # noqa: BLE001 — a receipt field is a courtesy, never a failure
            return None

    @staticmethod
    def _tool_output_row_count(tool_output: Any) -> Optional[int]:
        """How many rows a raw-SQL tool RETURNED, from the structured output (dict, JSON
        string, ``(content, artifact)`` tuple or MCP content blocks — the same coercion the
        tool wrapper uses). None when the shape carries no row list."""
        try:
            from trusted_data_agent.agent.tool_enforcement import _coerce_to_dict
            payload = tool_output
            # A LangChain ToolMessage-like object: the structured payload rides its
            # `artifact` (structured_content) and the MCP blocks its `content`; str()
            # of it is `content=[{'type': 'text', …}]`, which nothing can count.
            if not isinstance(payload, (dict, str, list, tuple)):
                art = getattr(payload, "artifact", None)
                structured = getattr(art, "structured_content", None) if art is not None else None
                if isinstance(structured, dict):
                    payload = structured
                elif getattr(payload, "content", None) is not None:
                    payload = getattr(payload, "content")
            d = _coerce_to_dict(payload)
            if not isinstance(d, dict):
                return None
            for key in ("results", "rows", "data"):
                v = d.get(key)
                if isinstance(v, list):
                    return len(v)
        except Exception:
            return None
        return None

    @staticmethod
    def _tool_output_text(tool_output) -> str:
        """A tool result as text: a message's ``content`` (a string, or its text blocks), never
        ``str(message)`` — whose repr carries the artifact (a component's rendered HTML)."""
        content = getattr(tool_output, "content", tool_output)
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict):
                    parts.append(str(b.get("text", "")))
                elif isinstance(b, str):
                    parts.append(b)
                else:
                    parts.append(str(getattr(b, "text", "") or ""))
            return "".join(parts)
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _tool_output_refusal(tool_output: Any) -> Optional[str]:
        """A COMPONENT that REFUSED (recovery-shaped: `status:"success"` on the envelope so the
        model reads the reason and self-corrects, `metadata.error_code` + `spec.error` inside)
        — e.g. TDA_Compute: "more than one numeric column — name one". Returns the reason, or
        None. Read from the structured payload first (a ToolMessage's content / an MCP block),
        then from the preview text; conservative — unknown ⇒ None."""
        try:
            payload = tool_output
            if isinstance(payload, tuple) and payload:
                payload = payload[0]
            content = getattr(payload, "content", payload)
            from trusted_data_agent.agent.tool_enforcement import _coerce_to_dict
            d = _coerce_to_dict(content) if not isinstance(content, dict) else content
            if isinstance(d, dict) and d.get("component_id"):
                meta = d.get("metadata") or {}
                spec = d.get("spec") or {}
                if meta.get("error_code") or (isinstance(spec, dict) and spec.get("error")):
                    return str(meta.get("error") or (spec.get("error") if isinstance(spec, dict) else "") or meta.get("error_code") or "refused")[:300]
                return None
            s = str(content) if content is not None else ""
            m = re.search(r'"error_code":\s*"([a-z_]+)"', s)
            if m and '"component_id"' in s:
                m2 = re.search(r'"error":\s*"((?:[^"\\]|\\.)*)"', s)
                return (m2.group(1).encode().decode("unicode_escape") if m2 else m.group(1))[:300]
            return None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _tool_output_is_error(tool_output: Any) -> bool:
        """Heuristically detect a tool that RETURNED (not raised) an error. MCP tools serialize an
        error payload as their output — a dict/JSON with ``status:"error"`` / ``success:false``, or
        a raw string carrying a ``[Error NNNN]`` code / preflight/authority denial. Used to set the
        real ``success`` flag so the honesty gate and OBDA-READ classify a failed data query correctly.
        Conservative: only a clear error signal returns True (unknown → not an error)."""
        try:
            payload = tool_output
            # content_and_artifact tuples: inspect the content element
            if isinstance(payload, tuple) and payload:
                payload = payload[0]
            if isinstance(payload, dict):
                if payload.get("status") == "error" or payload.get("success") is False:
                    return True
                if payload.get("preflight") or payload.get("metadata", {}).get("agent_denied"):
                    return True
                return False
            s = str(payload) if payload is not None else ""
            if not s:
                return False
            import re as _re
            if _re.search(r"\[Error \d+\]", s):
                return True
            low = s.lstrip()[:400].lower()
            if ('"status": "error"' in low or '"status":"error"' in low
                    or '"success": false' in low or '"success":false' in low
                    or '"preflight": true' in low or '"agent_denied": true' in low
                    or low.startswith("error:") or "agent_policy_denied" in low):
                return True
            return False
        except Exception:
            return False

    def _rewrite_events_for(self, tool_name: str) -> list:
        """The `conversation_tool_rewrite` payloads for the rewrites the wrapper recorded
        while THIS tool call ran — drained (so each is emitted once), stamped with the
        session, and sealed into the provenance chain as `agent_tool_rewrite` so the
        executed statement is in the signed record, not only in a preview. Never raises."""
        out = []
        try:
            from trusted_data_agent.agent import tool_enforcement as _tool_enforcement
            for rec in _tool_enforcement.drain_rewrites(tool_name):
                payload = dict(rec)
                payload["session_id"] = self.session_id
                payload["label"] = _tool_enforcement.REWRITE_KIND_LABEL.get(rec.get("kind"), rec.get("kind"))
                out.append(payload)
                try:
                    if self.provenance:
                        self.provenance.add_step(
                            "agent_tool_rewrite",
                            f"{tool_name}:{rec.get('kind')}:{str(rec.get('executed_sql') or '')[:4096]}",
                            f"{tool_name} statement rewritten deterministically ({rec.get('kind')})")
                except Exception:
                    pass
        except Exception:
            return out
        return out

    def _clean_tool_args(self, tool_input: Any) -> Any:
        """Strip LangChain-injected plumbing keys from a tool's input dict, leaving
        only the model-chosen arguments. Pass-through for non-dict inputs."""
        if not isinstance(tool_input, dict):
            return tool_input
        cleaned = {}
        for k, v in tool_input.items():
            if k in self._INJECTED_ARG_KEYS:
                continue
            if any(hint in type(v).__name__ for hint in self._INJECTED_TYPE_HINTS):
                continue
            cleaned[k] = v
        return cleaned

    def _safe_serialize(self, obj: Any) -> Any:
        """Safely serialize object for event payload."""
        try:
            if isinstance(obj, dict):
                return {k: self._safe_serialize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [self._safe_serialize(item) for item in obj]
            elif isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            else:
                return str(obj)
        except Exception:
            return str(obj)

    # ------------------------------------------------------------------
    # Recover leaked component tool calls emitted as text
    # ------------------------------------------------------------------

    @staticmethod
    def _find_tool_call_blocks(text: str) -> list:
        """Find balanced ``{...}`` blocks that contain a ``"tool_name"`` key.

        Returns a list of ``(start, end, parsed_obj)`` tuples (parsed_obj is None
        if the block isn't valid JSON). Quote/escape aware so nested or quoted
        braces don't break matching. Mirrors the scanner in
        ``conversation_engine._strip_leaked_tool_calls``.
        """
        import json as _json
        blocks = []
        n = len(text)
        i = 0
        while i < n:
            m = text.find('"tool_name"', i)
            if m == -1:
                break
            # Walk backwards to the opening brace of the object containing this key.
            start = text.rfind('{', 0, m)
            if start == -1:
                i = m + 1
                continue
            depth, j, in_str, esc = 0, start, False, False
            end = -1
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
                        end = j + 1
                        break
                j += 1
            if end == -1:
                break
            obj = None
            try:
                obj = _json.loads(text[start:end])
            except (ValueError, TypeError):
                obj = None
            blocks.append((start, end, obj))
            i = end
        return blocks

    async def _recover_leaked_component_calls(self, response: str, tools_used: list) -> str:
        """Execute component tool calls the model leaked into its text answer.

        Some models emit ``{"tool_name": "TDA_Charting", "arguments": {...}}`` as
        plain text instead of invoking the tool. When the named tool is a bound
        component tool that did NOT actually run this turn, invoke it for real so
        the component renders, then strip the JSON from the answer. If the tool
        *did* run (it's in ``tools_used``), the text is pure noise — strip only.
        """
        if not response or '"tool_name"' not in response:
            return response

        tool_by_name = {t.name: t for t in self.mcp_tools}
        blocks = self._find_tool_call_blocks(response)
        if not blocks:
            return response

        recovered = 0
        # Process in reverse so string offsets stay valid as we excise blocks.
        for (start, end, obj) in reversed(blocks):
            name = obj.get("tool_name") if isinstance(obj, dict) else None
            args = obj.get("arguments") if isinstance(obj, dict) else None

            # Recover only renderable component tools (TDA_*) that are actually
            # bound and did not already execute this turn.
            if (
                name
                and isinstance(args, dict)
                and name.startswith("TDA_")
                and name in tool_by_name
                and name not in tools_used
            ):
                try:
                    # Invoke as a TOOL CALL (with an id), not with bare args: a
                    # content_and_artifact tool returns only its content when no
                    # tool_call_id is present, and the reader's HTML rides the artifact.
                    import uuid as _uuid
                    result = await tool_by_name[name].ainvoke(
                        {"name": name, "args": args, "id": f"recovered-{_uuid.uuid4().hex[:12]}",
                         "type": "tool_call"})
                    from trusted_data_agent.components.utils import extract_component_payload
                    payload = extract_component_payload(result)
                    if payload:
                        self.component_payloads.append(payload)
                        tools_used.append(name)
                        recovered += 1
                        logger.info(
                            f"[ConvAgent] Recovered leaked component call: {name} "
                            f"(model emitted it as text instead of invoking it)"
                        )
                except Exception as rec_err:
                    logger.warning(
                        f"[ConvAgent] Failed to recover leaked component call {name}: {rec_err}"
                    )

            # Strip the JSON block whether recovered or noise.
            response = response[:start] + response[end:]

        # Tidy residue: empty arrays/objects left behind, code fences, blank runs.
        response = re.sub(r'\[\s*,?\s*\]', '', response)
        response = re.sub(r'```(?:json)?\s*```', '', response)
        response = re.sub(r'\n{3,}', '\n\n', response).strip()

        if recovered:
            logger.info(f"[ConvAgent] Leaked-call recovery: executed {recovered} component tool(s) from text")

        return response

    async def _recover_leaked_sql(self, response: str, tools_used: list) -> str:
        """Run the SELECT a model answered WITH instead of calling (``agent/leaked_sql.py``).

        Fires only when the whole answer is one read-only statement, a bound tool takes
        ``sql``, and no data tool ran this turn. The statement goes through the bound tool
        object — the same guarded coroutine a model-initiated call reaches (Agent Authority,
        preflight, rewrites) — and the SAME tool events the loop emits, so Live Status, the
        honesty gate, OBDA-READ and the eval scorers see a real data call. Zero LLM tokens;
        the rows are rendered deterministically. Fails open: any error leaves the answer as
        it was."""
        try:
            from trusted_data_agent.agent import leaked_sql as _ls
            if getattr(self, "_sql_recovery_done", False) or _ls.ran_data_tool(tools_used):
                return response
            sql = _ls.sole_sql_block(response)
            if not sql:
                return response
            tool = _ls.find_sql_tool(self.mcp_tools)
            if tool is None:
                return response
            self._sql_recovery_done = True
            tool_name = getattr(tool, "name", "base_readQuery")
            logger.warning(f"[ConvAgent] answer was a bare SELECT with no tool call — running it via {tool_name}")
            import uuid as _uuid
            args = {"sql": sql}
            self.tool_start_times[tool_name] = time.time()
            await self._emit_status_indicator("db", "busy")
            try:
                if self.provenance:
                    self.provenance.add_step("agent_tool_call", f"{tool_name}:{json.dumps(args, sort_keys=True)[:4096]}",
                                             f"Calling {tool_name} (recovered from a leaked SQL answer)")
            except Exception:  # noqa: BLE001
                pass
            _invoked = {"tool_name": tool_name, "arguments": args, "session_id": self.session_id,
                        "recovered": "leaked_sql"}
            await self._emit_event("conversation_tool_invoked", self._with_source(_invoked, tool_name))
            try:
                tool_output = await tool.ainvoke({"name": tool_name, "args": args,
                                                  "id": f"recovered-{_uuid.uuid4().hex[:12]}", "type": "tool_call"})
            except Exception as run_err:  # the tool raised: a failed call, reported as one
                tool_output = f'{{"status": "error", "error": {json.dumps(str(run_err)[:400])}}}'
            await self._emit_status_indicator("db", "idle")
            duration_ms = int((time.time() - self.tool_start_times.get(tool_name, time.time())) * 1000)
            text = self._tool_output_text(tool_output) if tool_output else ""
            refusal = self._tool_output_refusal(tool_output)
            success = not self._tool_output_is_error(tool_output) and refusal is None
            result_rows = self._tool_output_row_count(tool_output)
            _completed = {"tool_name": tool_name, "result_preview": text[:5000], "duration_ms": duration_ms,
                          "success": success, "session_id": self.session_id, "recovered": "leaked_sql"}
            if result_rows is not None:
                _completed["result_rows"] = result_rows
            _md = self._tool_output_metadata(tool_output)
            if _md:
                _completed["metadata"] = _md
            if refusal:
                _completed["refused"] = refusal
            await self._emit_event("conversation_tool_completed", self._with_source(_completed, tool_name, completed=True))
            try:
                if self.provenance:
                    self.provenance.add_step("agent_tool_result", f"{tool_name}:{str(tool_output)[:4096]}",
                                             f"{tool_name} completed ({duration_ms}ms)")
            except Exception:  # noqa: BLE001
                pass
            if tool_name not in tools_used:
                tools_used.append(tool_name)
            try:
                self._capture_data_result(tool_name, tool_output, [])
            except Exception:  # noqa: BLE001
                pass
            if not success:
                err = refusal or self._short_tool_error(text)
                return _ls.recovered_answer(sql, None, None, err or "the data tool reported an error")
            rows, declared = _ls.rows_from_output_text(text)
            return _ls.recovered_answer(sql, rows, declared if declared is not None else result_rows, None)
        except Exception as rec_err:  # noqa: BLE001 — a recovery must never break the turn
            logger.warning(f"[ConvAgent] leaked-SQL recovery skipped: {rec_err}")
            return response

    @staticmethod
    def _short_tool_error(text: str) -> str:
        m = re.search(r"\[Error \d+\][^\n]*", text or "")
        if m:
            return m.group(0)[:300]
        try:
            obj = json.loads((text or "")[(text or "").find("{"):])
            if isinstance(obj, dict):
                return str(obj.get("error") or obj.get("error_message") or obj.get("data") or "")[:300]
        except Exception:  # noqa: BLE001
            pass
        return (text or "")[:300]

    # ------------------------------------------------------------------
    # Auto-Canvas: post-process markdown code blocks into Canvas renders
    # ------------------------------------------------------------------

    _CODE_BLOCK_RE = re.compile(r'```(\w+)?\s*\n(.*?)```', re.DOTALL)

    _CANVAS_LANGUAGES = {
        "html", "css", "javascript", "python", "sql",
        "markdown", "json", "svg", "mermaid",
    }

    _EXTENSION_MAP = {
        "html": ".html", "css": ".css", "javascript": ".js",
        "python": ".py", "sql": ".sql", "markdown": ".md",
        "json": ".json", "svg": ".svg", "mermaid": ".mmd",
    }

    def _auto_canvas_code_blocks(self, response: str) -> str:
        """Post-process LLM response: convert markdown code blocks to Canvas payloads.

        When the LLM generates a fenced code block instead of calling TDA_Canvas,
        this method detects the block, creates a Canvas component payload, and
        strips the block from the response text so the executor renders it via
        the component system instead.

        Returns:
            The response text with code blocks removed (Canvas payloads appended
            to ``self.component_payloads``).
        """
        # Skip if TDA_Canvas is not among the bound tools
        if not any(t.name == "TDA_Canvas" for t in self.mcp_tools):
            return response

        # Skip if the LLM already used TDA_Canvas properly
        if any(cp.get("component_id") == "canvas" for cp in self.component_payloads):
            return response

        matches = list(self._CODE_BLOCK_RE.finditer(response))
        if not matches:
            return response

        # Process in reverse so string indices stay valid
        converted = 0
        for match in reversed(matches):
            lang_hint = (match.group(1) or "").lower().strip()
            code = match.group(2).strip()

            if not code:
                continue

            # Resolve language
            language = lang_hint if lang_hint in self._CANVAS_LANGUAGES else None
            if not language:
                language = self._detect_language_hint(code)
            if not language:
                continue  # Unrecognised language — leave as markdown

            # Build a canvas component payload (mirrors CanvasComponentHandler.process)
            line_count = code.count("\n") + 1
            title = "SQL Query" if language == "sql" else f"{language.title()} Code"

            payload = {
                "status": "success",
                "component_id": "canvas",
                "render_target": "inline",
                "spec": {
                    "content": code,
                    "language": language,
                    "title": title,
                    "previewable": language in {"html", "svg", "markdown"},
                    "line_count": line_count,
                    "file_extension": self._EXTENSION_MAP.get(language, ".txt"),
                    "sources": None,
                },
                "title": title,
                "metadata": {
                    "tool_name": "TDA_Canvas",
                    "language": language,
                    "content_length": len(code),
                    "line_count": line_count,
                    "auto_canvas": True,
                },
            }

            # Persist so the auto-canvas becomes a first-class, addressable instance,
            # exactly like a real TDA_Canvas call. Without this the canvas renders but
            # has no canvas_id and no DB row, so it never appears in the canvas
            # switcher dropdown, the Workspace panel, or the instance list. Upsert by
            # title matches CanvasComponentHandler's identity model. Fail-open: if the
            # write fails, still render the (unpersisted) canvas rather than dropping it.
            try:
                from trusted_data_agent.core.canvas_db import (
                    list_versions, upsert_canvas,
                )
                _result = upsert_canvas(
                    self.user_uuid, title, code, language,
                    sources=None, session_id=self.session_id,
                )
                _saved = _result.get("canvas") if isinstance(_result, dict) else None
                _cid = _saved.get("canvas_id") if _saved else None
                if _cid:
                    payload["spec"]["canvas_id"] = _cid
                    payload["spec"]["versions"] = list_versions(_cid, self.user_uuid)
                    payload["metadata"]["canvas_id"] = _cid
            except Exception as exc:
                logger.warning(
                    f"[ConvAgent] Auto-canvas persistence failed "
                    f"(rendering unpersisted): {exc}"
                )

            self.component_payloads.append(payload)

            # Strip the code block from the response text
            response = response[:match.start()] + response[match.end():]
            converted += 1
            logger.info(
                f"[ConvAgent] Auto-canvas: converted {language} code block "
                f"({line_count} lines) to canvas payload"
            )

        if converted:
            logger.info(f"[ConvAgent] Auto-canvas: {converted} code block(s) converted")

        return response.strip()

    @staticmethod
    def _detect_language_hint(content: str) -> Optional[str]:
        """Heuristic language detection for code blocks without a language tag."""
        c = content.strip()
        if not c:
            return None
        if c.startswith("<!DOCTYPE") or c.startswith("<html") or "<head" in c[:200]:
            return "html"
        if c.startswith("<svg"):
            return "svg"
        if c.startswith("graph ") or c.startswith("sequenceDiagram"):
            return "mermaid"
        if "def " in c[:500] or "import " in c[:200]:
            return "python"
        if any(kw in c.upper()[:300] for kw in ["SELECT ", "CREATE TABLE", "INSERT INTO"]):
            return "sql"
        if c.startswith("{") and c.endswith("}"):
            return "json"
        if c.startswith("# ") or "\n## " in c:
            return "markdown"
        return None


def answer_was_cut_off(finish_reason: str) -> bool:
    """The model stopped because it reached its output ceiling, not because it was done."""
    return (finish_reason or "").lower() in ("length", "max_tokens")


def empty_answer_notice(finish_reason: str, output_tokens: int) -> str:
    """What a turn that produced no answer text tells the reader, from why the model stopped."""
    if answer_was_cut_off(finish_reason):
        return (f"No answer was produced: the model reached its output limit ({output_tokens:,} output "
                "tokens this turn) before it wrote one — a reasoning model can spend the whole limit "
                "thinking. Raise the model's output ceiling, or ask a narrower question.")
    return ("No answer was produced: the model ended its turn without writing one"
            + (f" (it stopped with '{finish_reason}')" if finish_reason else "") + ".")
