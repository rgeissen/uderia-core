"""
Genie Profile Coordinator
==========================

Provides LangChain-based coordination for Genie profiles.
Genie profiles orchestrate multiple child profiles (sessions) to answer complex queries.

Architecture:
- GenieCoordinator: Main class that builds and executes a LangChain agent
- SlaveSessionTool: LangChain tool wrapper for child session REST calls
- Session context is reused when the same child profile is called multiple times

Usage:
    from trusted_data_agent.agent.genie_coordinator import GenieCoordinator

    coordinator = GenieCoordinator(
        genie_profile=profile,
        slave_profiles=slave_profiles,  # Variable name preserved for API compatibility
        user_uuid=user_uuid,
        parent_session_id=session_id,
        auth_token=token,
        llm_instance=langchain_llm
    )

    result = await coordinator.execute("Your complex question here")
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Any, Optional, Callable

import httpx
from pydantic import Field

from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from trusted_data_agent.agent.profile_prompt_resolver import ProfilePromptResolver

logger = logging.getLogger(__name__)

# Module-level session cache (shared across all SlaveSessionTool instances)
# Using module-level dict avoids Pydantic treating it as a private attribute
# Note: Variable name 'slave_session_cache' preserved for API compatibility
_slave_session_cache: Dict[str, str] = {}

# Module-level event callbacks (keyed by parent_session_id)
_event_callbacks: Dict[str, Callable[[str, Dict], None]] = {}

# Module-level provenance chains (keyed by parent_session_id) — EPC
_provenance_chains: Dict[str, Any] = {}

# Module-level full HTML response cache for pass-through (keyed by parent_session_id:profile_tag)
# Stores the slave's rich final_answer HTML so the coordinator can pass it through intact
# instead of using the plain-text final_answer_text summary.
_slave_html_responses: Dict[str, str] = {}

# Module-level per-slave-session execution locks (keyed by slave session_id)
# Prevents concurrent executions on the same slave session, which causes turn_number
# corruption in chat_object (two tasks read the same workflow_history length, both
# get current_turn_number=N, then their assistant messages collide at turn N+1).
_slave_session_locks: Dict[str, asyncio.Lock] = {}


def _get_slave_session_lock(session_id: str) -> asyncio.Lock:
    """Get or create a per-slave-session lock to serialize concurrent invocations."""
    # setdefault is atomic under the GIL — avoids TOCTOU between check and assignment
    return _slave_session_locks.setdefault(session_id, asyncio.Lock())


def _format_collected_data_as_text(collected_data: dict, max_rows: int = 50) -> str:
    """
    Formats structured data rows from a tool_enabled slave into plain text
    so the coordinator synthesis LLM has access to actual retrieved data
    when full_result_passthrough is enabled on the slave profile.
    """
    if not collected_data or not isinstance(collected_data, dict):
        return ""

    sections = []
    for _key, items in collected_data.items():
        if not isinstance(items, list) or not items:
            continue

        # Flatten: items may be row-dicts directly, or result-dicts with a
        # "results"/"data" array (MCP tool response wrapper).
        rows = []
        for item in items:
            if isinstance(item, dict):
                nested = item.get("results") or item.get("data")
                if isinstance(nested, list):
                    rows.extend(r for r in nested if isinstance(r, dict))
                elif not item.get("status"):  # plain row dict (no status wrapper)
                    rows.append(item)
            elif isinstance(item, list):
                rows.extend(r for r in item if isinstance(r, dict))

        if not rows:
            continue

        total = len(rows)
        sample = rows[:max_rows]
        cols = list(sample[0].keys())
        header = " | ".join(cols)
        row_lines = [" | ".join(str(r.get(c, "")) for c in cols) for r in sample]
        truncation = f" (showing first {max_rows} of {total})" if total > max_rows else f" ({total} rows)"
        block = (
            f"Retrieved data{truncation}:\n"
            + header + "\n"
            + "-" * min(len(header), 120) + "\n"
            + "\n".join(row_lines)
        )
        sections.append(block)

    return "\n\n".join(sections)



class SlaveSessionTool(BaseTool):
    """
    LangChain tool that wraps REST calls to a child session.

    Each child profile becomes a separate tool instance. When invoked,
    it creates or reuses a child session and executes the query through
    the existing Uderia REST API.

    Note: Class name 'SlaveSessionTool' preserved for API compatibility.
    """
    name: str = Field(description="Tool name like invoke_CHAT")
    description: str = Field(description="Description of what this profile does")
    profile_id: str = Field(description="Profile ID for this child profile")
    profile_tag: str = Field(description="Profile tag (e.g., CHAT, RAG)")
    user_uuid: str = Field(description="User UUID who owns the sessions")
    parent_session_id: str = Field(description="Parent Genie session ID")
    base_url: str = Field(default="http://localhost:5050", description="API base URL")
    auth_token: str = Field(description="Authentication token for API calls")
    query_timeout: float = Field(default=300.0, description="Timeout for query execution in seconds")
    current_nesting_level: int = Field(default=0, description="Current nesting depth in Genie hierarchy")
    full_result_passthrough: bool = Field(default=False, description="Append full structured data rows to result for coordinator synthesis LLM")

    class Config:
        arbitrary_types_allowed = True

    def _emit_event(self, event_type: str, payload: dict):
        """Emit event via callback if configured."""
        callback = _event_callbacks.get(self.parent_session_id)
        if callback:
            try:
                # Note: genie_event_handler signature is (event_type, payload) - different from other profiles
                callback(event_type, payload)
            except Exception as e:
                logger.warning(f"SlaveSessionTool event callback error: {e}")

    def _run(self, query: str) -> str:
        """Synchronous run - not implemented, use async."""
        raise NotImplementedError("Use async _arun instead")

    async def _arun(self, query: str) -> str:
        """Execute query via child session with context reuse."""
        start_time = time.time()

        # Check if child is a Genie profile (nested coordination)
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        slave_profile = config_manager.get_profile(self.profile_id, self.user_uuid)
        is_nested_genie = slave_profile and slave_profile.get("profile_type") == "genie"

        if is_nested_genie:
            # Verify we won't exceed depth limit
            global_settings = config_manager.get_genie_global_settings()
            max_depth = int(global_settings.get('maxNestingDepth', {}).get('value', 3))
            next_level = self.current_nesting_level + 1

            if next_level >= max_depth:
                error_msg = f"Cannot invoke nested Genie @{self.profile_tag} - would exceed max depth ({max_depth})"
                logger.warning(error_msg)

                # Emit completion event with error
                self._emit_event("genie_slave_completed", {
                    "profile_tag": self.profile_tag,
                    "success": False,
                    "error": error_msg,
                    "nesting_level": next_level,
                    "max_depth": max_depth,
                    "session_id": self.parent_session_id
                })

                return f"Error: {error_msg}"

            # Log nested Genie invocation
            logger.info(f"🔮 Invoking nested Genie @{self.profile_tag} at level {next_level}")

        # EPC: Record coordinator dispatch
        try:
            prov = _provenance_chains.get(self.parent_session_id)
            if prov:
                prov.add_step("coordinator_dispatch", f"{self.profile_tag}:{query or ''}", f"Dispatching to @{self.profile_tag}")
        except Exception:
            pass

        # Emit child invoked event with full query for UI display
        self._emit_event("genie_slave_invoked", {
            "profile_tag": self.profile_tag,
            "profile_id": self.profile_id,
            "query": query or "",  # Full query for UI display
            "query_preview": query[:100] if query else "",  # Short preview for logs
            "nesting_level": self.current_nesting_level,
            "is_nested_genie": is_nested_genie,
            "session_id": self.parent_session_id
        })

        try:
            session_id = await self._get_or_create_slave_session()

            # Update event with session ID
            self._emit_event("genie_slave_progress", {
                "profile_tag": self.profile_tag,
                "slave_session_id": session_id,
                "status": "executing",
                "message": "Processing query...",
                "session_id": self.parent_session_id
            })

            # Serialize executions on the same slave session to prevent turn_number
            # corruption. LangGraph may dispatch parallel tool calls to the same
            # slave when the coordinator LLM outputs multiple calls in one response.
            # Concurrent writes cause both tasks to read the same workflow_history
            # length, assign identical current_turn_number, and produce orphaned
            # assistant messages with the wrong turn_number.
            slave_lock = _get_slave_session_lock(session_id)
            async with slave_lock:
                # PROP-011 phase 3 — on the coordinator's re-entry turn, the expert whose question
                # was relayed is CONTINUED with the person's answer, never asked afresh
                _cont = None
                try:
                    from trusted_data_agent.agent import input_requests as _ir_mod
                    _cont = _ir_mod.continuation_for(f"coordinate:@{self.profile_tag}:")
                except Exception:  # noqa: BLE001
                    _cont = None
                if _cont is not None and _cont.get("child_session_id") == session_id:
                    result = await self._continue_with_answer(session_id, _cont)
                else:
                    result = await self._execute_and_poll(session_id, query)

            # Emit completion event with full result for UI display
            duration_ms = int((time.time() - start_time) * 1000)

            # EPC: Record child chain reference
            try:
                prov = _provenance_chains.get(self.parent_session_id)
                if prov:
                    # Read child session's provenance tip hash (if available)
                    from trusted_data_agent.core.provenance import get_previous_turn_tip_hash as _get_tip
                    child_tip = await _get_tip(self.user_uuid, session_id)
                    prov.add_step("child_chain_ref", f"{session_id}:{child_tip or 'none'}", f"@{self.profile_tag} completed ({duration_ms}ms)")
            except Exception:
                pass

            self._emit_event("genie_slave_completed", {
                "profile_tag": self.profile_tag,
                "slave_session_id": session_id,
                "result": result or "",  # Full result for UI display
                "result_preview": result[:200] if result else "",  # Short preview for compact view
                "duration_ms": duration_ms,
                "success": True,
                "session_id": self.parent_session_id
            })

            return result
        except Exception as e:
            logger.error(f"SlaveSessionTool error for @{self.profile_tag}: {e}", exc_info=True)

            # Emit error event
            duration_ms = int((time.time() - start_time) * 1000)
            self._emit_event("genie_slave_completed", {
                "profile_tag": self.profile_tag,
                "duration_ms": duration_ms,
                "success": False,
                "error": str(e),
                "session_id": self.parent_session_id
            })

            return f"Error invoking @{self.profile_tag}: {str(e)}"

    async def _get_or_create_slave_session(self) -> str:
        """Reuse existing child session or create new one.

        If the child profile has a session_primer configured, it will be
        automatically executed when the session is first created.
        """
        cache_key = f"{self.parent_session_id}:{self.profile_id}"

        if cache_key in _slave_session_cache:
            logger.debug(f"Reusing existing child session for @{self.profile_tag}")
            return _slave_session_cache[cache_key]

        logger.info(f"Creating new child session for @{self.profile_tag}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/sessions",
                headers={"Authorization": f"Bearer {self.auth_token}"},
                json={
                    "profile_id": self.profile_id,
                    "genie_parent_session_id": self.parent_session_id,
                    "genie_slave_profile_id": self.profile_id
                }
            )

            if response.status_code != 201:
                raise Exception(f"Failed to create child session: {response.status_code} - {response.text}")

            data = response.json()
            session_id = data.get("session_id")
            session_primer = data.get("session_primer")

            if not session_id:
                raise Exception(f"No session_id in response: {data}")

            # Cache the session immediately so primer execution can reuse it
            _slave_session_cache[cache_key] = session_id
            logger.info(f"Created child session {session_id} for @{self.profile_tag}")

            # Execute session primer if configured (leverages existing _execute_and_poll)
            if session_primer:
                # Handle session_primer as string or dict
                primer_text = None

                if isinstance(session_primer, dict):
                    # Check if enabled
                    if not session_primer.get("enabled", False):
                        logger.debug(f"Session primer disabled for @{self.profile_tag}")
                    else:
                        # Extract statements based on mode
                        statements = session_primer.get("statements", [])
                        mode = session_primer.get("mode", "combined")

                        if mode == "combined":
                            # Combine all statements into one query
                            primer_text = "\n\n".join(statements) if statements else None
                        elif mode == "sequential":
                            # Execute each statement in order; store as a list for sequential dispatch
                            primer_text = statements if statements else None
                        else:
                            logger.warning(f"Unknown session_primer mode '{mode}' for @{self.profile_tag}")
                            primer_text = "\n\n".join(statements) if statements else None
                elif isinstance(session_primer, str):
                    # Legacy string format
                    primer_text = session_primer

                if primer_text:
                    # primer_text is either a str (combined/legacy) or list[str] (sequential)
                    primer_statements = primer_text if isinstance(primer_text, list) else [primer_text]
                    logger.info(f"Executing {len(primer_statements)} session primer statement(s) for @{self.profile_tag}")
                    self._emit_event("genie_slave_progress", {
                        "profile_tag": self.profile_tag,
                        "slave_session_id": session_id,
                        "status": "primer_executing",
                        "message": f"Executing session primer ({len(primer_statements)} statement(s))...",
                        "session_id": self.parent_session_id
                    })

                    for i, stmt in enumerate(primer_statements, 1):
                        try:
                            await self._execute_primer(session_id, stmt)
                            logger.info(f"Session primer {i}/{len(primer_statements)} completed for @{self.profile_tag}")
                        except Exception as e:
                            logger.warning(f"Session primer {i}/{len(primer_statements)} failed for @{self.profile_tag}: {e}")
                            # Continue — primer failure shouldn't block the main query

            return session_id

    async def _execute_primer(self, session_id: str, primer: str) -> str:
        """Execute a session primer query with is_session_primer flag."""
        async with httpx.AsyncClient(timeout=self.query_timeout) as client:
            # Submit primer with is_session_primer flag
            response = await client.post(
                f"{self.base_url}/api/v1/sessions/{session_id}/query",
                headers={"Authorization": f"Bearer {self.auth_token}"},
                json={
                    "prompt": primer,
                    "profile_id": self.profile_id,
                    "is_session_primer": True
                }
            )

            if response.status_code != 202:
                raise Exception(f"Failed to submit primer: {response.status_code} - {response.text}")

            data = response.json()
            task_id = data.get("task_id")

            if not task_id:
                raise Exception(f"No task_id in primer response: {data}")

            logger.info(f"Submitted session primer to @{self.profile_tag}, task_id: {task_id}")

            # Poll for completion (same pattern as _execute_and_poll)
            poll_interval = 1.0
            max_polls = int(self.query_timeout / poll_interval)

            for _ in range(max_polls):
                status_response = await client.get(
                    f"{self.base_url}/api/v1/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {self.auth_token}"}
                )

                if status_response.status_code != 200:
                    await asyncio.sleep(poll_interval)
                    continue

                status_data = status_response.json()
                status = status_data.get("status")

                from trusted_data_agent.core import turn_states as _ts
                if _ts.is_success(status) or _ts.is_interrupted(status):
                    return "Primer executed successfully"
                elif _ts.is_terminal(status):
                    error = status_data.get("error", "Unknown error")
                    raise Exception(f"Primer execution failed: {error}")

                await asyncio.sleep(poll_interval)

            raise Exception("Timeout waiting for primer execution")

    async def _continue_with_answer(self, session_id: str, cont: dict) -> str:
        """PROP-011 phase 3 — the answer travels down: the expert's own re-entry turn, started
        in-process with the person's answer, then polled like any expert turn."""
        if cont.get("decision") != "accept":
            return (f"The user {'declined' if cont.get('decision') == 'decline' else 'did not answer'} "
                    f"@{self.profile_tag}'s question — answer from what you have, and say so.")
        from trusted_data_agent.core import input_resume as _resume
        from trusted_data_agent.core.platform_operations import _app
        task_id, why = await _resume.resume_child(_app(), self.user_uuid, cont["child_request_id"],
                                                  decision="accept", values=cont.get("values"), profile_id=self.profile_id)
        if not task_id:
            return f"Error from @{self.profile_tag}: could not continue with the user's answer ({why})"
        logger.info(f"Continued @{self.profile_tag} with the user's answer, task_id: {task_id}")
        async with httpx.AsyncClient(timeout=self.query_timeout) as client:
            return await self._poll_task(client, session_id, task_id)

    async def _execute_and_poll(self, session_id: str, query: str) -> str:
        """Submit query and poll for completion."""
        async with httpx.AsyncClient(timeout=self.query_timeout) as client:
            # Submit query
            response = await client.post(
                f"{self.base_url}/api/v1/sessions/{session_id}/query",
                headers={"Authorization": f"Bearer {self.auth_token}"},
                json={
                    "prompt": query,
                    "profile_id": self.profile_id,
                }
            )

            if response.status_code != 202:
                raise Exception(f"Failed to submit query: {response.status_code} - {response.text}")

            data = response.json()
            task_id = data.get("task_id")

            if not task_id:
                raise Exception(f"No task_id in response: {data}")

            logger.info(f"Submitted query to @{self.profile_tag}, task_id: {task_id}")
            return await self._poll_task(client, session_id, task_id)

    async def _poll_task(self, client, session_id: str, task_id: str) -> str:
        """Poll an expert's task to its settled state and return the text the coordinator reads."""
        if True:
            # Poll for completion - derive max_polls from query_timeout
            poll_interval = 1.0
            max_polls = int(self.query_timeout / poll_interval)

            for _ in range(max_polls):
                status_response = await client.get(
                    f"{self.base_url}/api/v1/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {self.auth_token}"}
                )

                if status_response.status_code != 200:
                    logger.warning(f"Task status check failed: {status_response.status_code}")
                    await asyncio.sleep(poll_interval)
                    continue

                status_data = status_response.json()
                status = status_data.get("status")

                from trusted_data_agent.core import turn_states as _ts
                if _ts.is_success(status) or _ts.is_interrupted(status):
                    result = status_data.get("result", {})

                    # PROP-011 phase 3 — the question travels up: the expert stopped to ask; its
                    # question becomes the coordinator turn's question (the chain on the record)
                    # and the coordinator relays it, never answers it.
                    _child_q = result.get("input_request") if isinstance(result, dict) else None
                    if isinstance(_child_q, dict) and _child_q.get("id"):
                        from trusted_data_agent.agent import input_requests as _ir_mod
                        _rec, _note = _ir_mod.relay_child_question(
                            _child_q, agent_tag=f"@{self.profile_tag}",
                            source=f"coordinate:@{self.profile_tag}:{session_id}")
                        logger.info(f"@{self.profile_tag} stopped to ask the user; relayed as {(_rec or {}).get('id')}")
                        return _note

                    # Forward child CCR events to parent Genie session
                    child_events = status_data.get("events", [])
                    for event in child_events:
                        evt_type = event.get("event_type")
                        if evt_type == "rag_retrieval":
                            self._emit_event("rag_retrieval", event.get("event_data", {}))

                    # Handle case where result might be a string directly (instead of dict)
                    if isinstance(result, str):
                        logger.info(f"@{self.profile_tag} completed successfully (text length: {len(result)})")
                        return result
                    elif result is None:
                        logger.warning(f"@{self.profile_tag} completed but result is None")
                        return "No response received"

                    # Prefer clean text (final_answer_text) for LLM consumption
                    # HTML formatting in final_answer adds noise for coordinator reasoning
                    # Fall back to final_answer/final_response if clean text not available
                    final_response = (
                        result.get("final_answer_text") or  # Clean text - preferred for LLM
                        result.get("final_response") or     # Legacy field
                        result.get("final_answer", "")      # HTML formatted - fallback
                    )
                    # When full_result_passthrough is enabled on this slave profile, append
                    # the structured data rows so the coordinator synthesis LLM has access
                    # to the actual retrieved data, not just TDA_FinalReport's text summary.
                    if self.full_result_passthrough:
                        data_text = _format_collected_data_as_text(result.get("collected_data") or {})
                        if data_text:
                            final_response = final_response + "\n\n" + data_text
                    # Cache the full HTML response for pass-through rendering.
                    # When synthesis is skipped, the coordinator delivers the slave's answer
                    # directly to the user — we want the rich HTML (table, charts, etc.),
                    # not the plain-text summary stored in final_answer_text.
                    html_response = result.get("final_answer") or final_response
                    if html_response and html_response != final_response:
                        _slave_html_responses[f"{self.parent_session_id}:{self.profile_tag}"] = html_response
                    logger.info(f"@{self.profile_tag} completed successfully (text length: {len(final_response)})")
                    return final_response

                elif _ts.is_terminal(status):
                    error = status_data.get("error", "Unknown error")
                    logger.error(f"@{self.profile_tag} task failed: {error}")
                    return f"Error from @{self.profile_tag}: {error}"

                await asyncio.sleep(poll_interval)

            return f"Timeout waiting for @{self.profile_tag} response"


class GenieCoordinator:
    """
    Builds and executes a LangChain agent that coordinates child sessions.

    The coordinator:
    1. Creates tools from child profiles
    2. Loads the coordinator system prompt (with profile-level overrides)
    3. Builds a LangChain agent executor
    4. Executes queries and synthesizes results
    5. Emits real-time events for UI feedback (if event_callback provided)

    Note: Parameter names like 'slave_profiles' preserved for API compatibility.
    """

    def __init__(
        self,
        genie_profile: Dict[str, Any],
        slave_profiles: List[Dict[str, Any]],
        user_uuid: str,
        parent_session_id: str,
        auth_token: str,
        llm_instance: Any,  # LangChain-compatible LLM
        base_url: str = "http://localhost:5050",
        event_callback: Optional[Callable[[str, Dict], None]] = None,
        genie_config: Optional[Dict[str, Any]] = None,
        current_nesting_level: int = 0,
        provenance: Optional[Any] = None,
        skill_result: Optional[Any] = None,
        tools_enabled: bool = True,
        answer_stream: Optional[Any] = None,
    ):
        """
        Initialize the Genie Coordinator.

        Args:
            genie_profile: The Genie profile configuration
            slave_profiles: List of child profile configurations (parameter name preserved for API compatibility)
            user_uuid: User UUID who owns the sessions
            parent_session_id: The parent Genie session ID
            auth_token: Authentication token for REST API calls
            llm_instance: LangChain-compatible LLM instance
            base_url: Base URL for REST API (default localhost:5050)
            event_callback: Optional callback for real-time UI events.
                           Signature: (event_type: str, payload: dict) -> None
            genie_config: Optional configuration dict with parameters:
                         - temperature: LLM temperature (0.0-1.0)
                         - queryTimeout: Query timeout in seconds (60-900)
                         - maxIterations: Max agent iterations (1-25)
            current_nesting_level: Current depth in nested Genie hierarchy (0 = top-level)
            skill_result: Optional resolved skill content to inject into the coordinator system prompt
        """
        self.genie_profile = genie_profile
        self.slave_profiles = slave_profiles
        self.user_uuid = user_uuid
        self.parent_session_id = parent_session_id
        # Running coordinator LLM cost, for the in-flight per-run budget cap (Agent Authority).
        # The coordinator's own routing/synthesis calls escaped the cap entirely; this lets the
        # same predicate stop the next call before it is paid for (autonomous turns only → no
        # ambient principal on an interactive turn → no-op → baseline).
        self._run_cost_usd = 0.0
        self.auth_token = auth_token
        self.llm_instance = llm_instance
        self.base_url = base_url
        self.event_callback = event_callback
        self.current_nesting_level = current_nesting_level
        self.provenance = provenance  # EPC: Provenance chain from execution_service
        self.skill_result = skill_result  # Pre-resolved skill content for coordinator prompt
        # S1 The Governed Stream: the turn's delta sink (None ⇒ off). The coordinator's own
        # synthesis streams; a routing message (tool calls) is a decision and is withdrawn.
        # A pass-through answer (one expert, no synthesis) arrives whole with final_answer.
        self.answer_stream = answer_stream

        # Extract effective config values with defaults
        genie_config = genie_config or {}
        self.query_timeout = float(genie_config.get('queryTimeout', 300))
        self.max_iterations = int(genie_config.get('maxIterations', 10))

        # Extract provider and model info for cost tracking
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        llm_config_id = genie_profile.get("llmConfigurationId")
        if llm_config_id:
            llm_configurations = config_manager.get_llm_configurations(user_uuid)
            llm_config = next((c for c in llm_configurations if c.get("id") == llm_config_id), None)
            self.provider = llm_config.get('provider', 'Unknown') if llm_config else 'Unknown'
            self.model = llm_config.get('model', 'unknown') if llm_config else 'unknown'
        else:
            self.provider = 'Unknown'
            self.model = 'unknown'

        # Log nesting level for debugging
        if current_nesting_level > 0:
            logger.info(f"🔮 GenieCoordinator initialized at nesting level {current_nesting_level}")

        # Collect events during execution for plan reload
        self.collected_events = []

        # KG enrichment event (populated during execute if active KG exists)
        self.kg_enrichment_event = None

        # Track LLM call count for step naming
        self.llm_call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # Track which profiles were actually invoked during execution
        self.invoked_profiles = []

        # Collect component payloads from direct component tool calls (TDA_Charting, etc.)
        self.component_payloads: list = []

        # Register event callback for this parent session
        # Wrap the callback to also collect events from child tools
        # Transient events that are only meaningful during live execution (UI indicator dots).
        # They must NOT be persisted to turn data — they clutter plan reload and have no replay value.
        _TRANSIENT_EVENT_TYPES = frozenset({"status_indicator_update", "token_update"})

        if event_callback:
            def collecting_callback(event_type: str, payload: dict):
                # Collect event for plan reload (skip transient UI-only events)
                if event_type not in _TRANSIENT_EVENT_TYPES:
                    self.collected_events.append({
                        "type": event_type,
                        "payload": dict(payload)
                    })

                # Track profile invocations for synthesis/completion events
                if event_type == "genie_slave_invoked":
                    profile_tag = payload.get("profile_tag")
                    if profile_tag and profile_tag not in self.invoked_profiles:
                        self.invoked_profiles.append(profile_tag)

                # Forward to original callback (genie_event_handler signature is (event_type, payload))
                event_callback(event_type, payload)
            _event_callbacks[parent_session_id] = collecting_callback
        else:
            # Even without an external callback, collect events for plan reload
            def collecting_only_callback(event_type: str, payload: dict):
                # Skip transient UI-only events
                if event_type not in _TRANSIENT_EVENT_TYPES:
                    self.collected_events.append({
                        "type": event_type,
                        "payload": dict(payload)
                    })

                # Track profile invocations for synthesis/completion events
                if event_type == "genie_slave_invoked":
                    profile_tag = payload.get("profile_tag")
                    if profile_tag and profile_tag not in self.invoked_profiles:
                        self.invoked_profiles.append(profile_tag)
            _event_callbacks[parent_session_id] = collecting_only_callback

        # EPC: Register provenance chain for SlaveSessionTool access
        if self.provenance:
            _provenance_chains[parent_session_id] = self.provenance

        # Build tools and agent. A briefing primer turn (agent/primer.py) builds the
        # coordinator with NO tools at all — neither experts nor components — so the
        # briefing is acknowledged rather than dispatched.
        self.tools_enabled = bool(tools_enabled)
        self.tools = self._build_tools() if self.tools_enabled else []

        # Merge component tools (TDA_Charting, etc.) so the coordinator can call them
        from trusted_data_agent.components.manager import get_component_langchain_tools
        component_tools = get_component_langchain_tools(
            self.genie_profile.get("id"), self.user_uuid,
            session_id=parent_session_id,
        ) if self.tools_enabled else []
        if component_tools:
            self.tools.extend(component_tools)
            logger.info(f"GenieCoordinator: added {len(component_tools)} component tool(s)")

        # Track component tool names for event emission (distinguish from SlaveSessionTools)
        self.component_tool_names = {t.name for t in component_tools} if component_tools else set()

        self.agent_executor = self._build_agent()

        logger.info(f"GenieCoordinator initialized with {len(self.tools)} tools ({len(self.tools) - len(component_tools) if component_tools else len(self.tools)} profile + {len(component_tools) if component_tools else 0} component)")

    def _emit_event(self, event_type: str, payload: dict):
        """Emit event via callback if configured. Events are collected via the registered callback."""
        # Events are collected via _event_callbacks[parent_session_id] which wraps
        # the original callback to also collect events for plan reload
        callback = _event_callbacks.get(self.parent_session_id)
        if callback:
            try:
                # Note: genie_event_handler signature is (event_type, payload) - different from other profiles
                callback(event_type, payload)
            except Exception as e:
                logger.warning(f"GenieCoordinator event callback error: {e}")

    def _build_tools(self) -> List[SlaveSessionTool]:
        """Create a LangChain tool for each child profile."""
        tools = []

        for profile in self.slave_profiles:
            profile_id = profile.get("id")
            profile_tag = profile.get("tag", "UNKNOWN")
            profile_name = profile.get("name", profile_tag)
            profile_description = profile.get("description", "")
            profile_type = profile.get("profile_type", "tool_enabled")

            # Build tool description
            description = self._build_tool_description(profile)

            tool = SlaveSessionTool(
                name=f"invoke_{profile_tag}",
                description=description,
                profile_id=profile_id,
                profile_tag=profile_tag,
                user_uuid=self.user_uuid,
                parent_session_id=self.parent_session_id,
                base_url=self.base_url,
                auth_token=self.auth_token,
                query_timeout=self.query_timeout,
                current_nesting_level=self.current_nesting_level,
                full_result_passthrough=profile.get("full_result_passthrough", False),
            )

            tools.append(tool)

            logger.debug(f"Created tool: invoke_{profile_tag}")

        return tools

    def _build_tool_description(self, profile: Dict[str, Any]) -> str:
        """
        Build a descriptive tool description for LLM routing decisions.

        Enhanced format includes profile name alongside tag for better context:
        @TAG (Profile Name): Description. Type: profile_type
        """
        profile_tag = profile.get("tag", "UNKNOWN")
        profile_name = profile.get("name", "")
        profile_type = profile.get("profile_type", "tool_enabled")
        description = profile.get("description", "General purpose profile")

        # Format: @TAG (Name): Description. Type: type
        # Include name only if it's different from tag and not empty
        if profile_name and profile_name.upper() != profile_tag.upper():
            return f"@{profile_tag} ({profile_name}): {description}. Type: {profile_type}"
        else:
            return f"@{profile_tag}: {description}. Type: {profile_type}"

    def _build_agent(self):
        """Build the LangGraph react agent with coordinator prompt."""
        # Load prompt from database (supports profile-level overrides)
        resolver = ProfilePromptResolver(profile_id=self.genie_profile.get("id"))
        system_prompt = resolver.get_genie_coordinator_prompt()

        if not system_prompt:
            # Fallback to hardcoded default
            logger.warning("Could not load GENIE_COORDINATOR_PROMPT from database, using fallback")
            system_prompt = self._get_fallback_prompt()

        # Inject available profiles into the prompt
        tool_descriptions = "\n".join([
            f"- {self._build_tool_description(p)}"
            for p in self.slave_profiles
        ])
        system_prompt = system_prompt.replace("{available_profiles}", tool_descriptions)

        # Inject component instructions
        from trusted_data_agent.components.manager import get_component_instructions_for_prompt
        comp_section = get_component_instructions_for_prompt(
            self.genie_profile.get("id"), self.user_uuid
        )
        system_prompt = system_prompt.replace("{component_instructions_section}", comp_section)

        # Inject skill content into coordinator system prompt
        if self.skill_result and self.skill_result.has_content:
            skill_block = self.skill_result.get_system_prompt_block()
            if skill_block:
                system_prompt += f"\n\n{skill_block}"
                logger.debug(f"Injected skill content into genie coordinator prompt (~{self.skill_result.total_estimated_tokens} tokens)")

        # Store system prompt for use in execute()
        self.system_prompt = system_prompt

        # Create LangGraph react agent (new API in langchain v1 / langgraph)
        # Note: recursion_limit is passed at runtime via astream_events config, not here
        return create_react_agent(
            model=self.llm_instance,
            tools=self.tools
        )

    def _get_fallback_prompt(self) -> str:
        """Get fallback coordinator prompt if database prompt unavailable."""
        return """You are a Genie Coordinator that orchestrates specialized AI profiles to answer complex queries.

Your available profiles are tools that invoke specialized sessions:
{available_profiles}

{component_instructions_section}
---
### MANDATORY DISPATCH RULE

You are a COORDINATOR, not an expert. You do NOT possess knowledge about the user's data environment.

NEVER answer questions about any of the following from your own knowledge — ALWAYS dispatch to a slave profile:
- Database existence, names, or schemas
- Table/view/column existence or structure
- Data content, row counts, or values
- System configuration, metrics, or status
- Any factual claim about the user's environment

If you are unsure whether a question requires tool dispatch, DISPATCH. Only answer directly for:
- Clarifying your own coordination decisions
- Summarizing/synthesizing results already returned by slave profiles
- Genuinely generic knowledge questions unrelated to the user's data

Violation of this rule causes hallucinated answers that erode user trust.

---
### Coordination Rules

1. **ANALYZE** the user's question to determine which profile(s) are best suited to answer it.

2. **ROUTE** the query (or decomposed sub-queries) to appropriate profiles using the invoke_* tools.
   - For database queries or data analysis: Use tool-enabled profiles
   - For conversational explanation: Use llm_only profiles
   - For knowledge lookups: Use rag_focused profiles
   - For complex questions: Decompose and route to multiple profiles

3. **SYNTHESIZE** the results from profile invocations into a coherent final response.

4. **SINGLE PROFILE OPTIMIZATION**: If a single profile can fully answer the question, use only that profile.

---
### Important Behaviors

- Each tool invocation creates or reuses a real session visible in the UI
- Subsequent calls to the same profile reuse that session's context
- Be specific in your sub-queries to get targeted results
- Always briefly explain your routing decision before invoking tools

---
### Response Format

After gathering information from profiles, provide a synthesized answer that:
- Combines insights from all consulted profiles
- Presents a unified, coherent response
- Credits which profiles contributed to the answer (if multiple were used)
"""

    async def revise_text(self, prompt: str):
        """S7 — ONE call on the coordinator's own model over a draft + the outcome note (no
        tools), priced through the SAME cache-aware rule as every coordinator call and counted
        into the turn's totals, so a revision is never a free or a double-billed call. Returns
        `(text, tokens_in, tokens_out, cost_usd)`; `("", 0, 0, 0.0)` on any failure."""
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            msgs = [SystemMessage(content=("You revise your own draft answer to remove or correct exactly what the "
                                           "grounding check named. Keep every supported statement; add no new claims; "
                                           "return only the revised answer.")),
                    HumanMessage(content=prompt)]
            out = await self.llm_instance.ainvoke(msgs)
            from trusted_data_agent.llm.response_text import response_text as _rt
            text = _rt(out)
            usage = getattr(out, "usage_metadata", None) or {}
            tin = int((usage.get("input_tokens") if isinstance(usage, dict) else getattr(usage, "input_tokens", 0)) or 0)
            tout = int((usage.get("output_tokens") if isinstance(usage, dict) else getattr(usage, "output_tokens", 0)) or 0)
            from trusted_data_agent.agent import prompt_cache as _pc
            _cu = _pc.langchain_usage(usage, getattr(out, "response_metadata", None)); _cu["input"], _cu["output"] = tin, tout
            _pol = getattr(self, "_cache_pol", None) or {}
            _served = ((getattr(out, "response_metadata", None) or {}).get("uderia_served_by") or {})
            cost = _pc.priced(_served.get("provider") or self.provider, _served.get("model") or self.model, _cu, _pol.get("ttl", "5m")) or 0.0
            self.total_input_tokens += tin; self.total_output_tokens += tout
            self._run_cost_usd += cost; self.turn_cost_usd = getattr(self, "turn_cost_usd", 0.0) + cost
            from trusted_data_agent.agent import turn_budget as _tbud
            _tbud.record_tokens(tin + tout)
            import uuid as _uuid
            self.llm_call_count += 1
            self._emit_event("genie_llm_step", {"step_number": self.llm_call_count, "step_name": "Outcome Revision",
                                                "input_tokens": tin, "output_tokens": tout, "cached_tokens": _cu.get("cached", 0),
                                                "provider": self.provider, "model": self.model, "served_by_fallback": bool(_served),
                                                "session_id": self.parent_session_id, "call_id": str(_uuid.uuid4()), "cost_usd": cost})
            return text, tin, tout, cost
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[Genie] outcome revision failed: {e}")
            return "", 0, 0, 0.0

    async def execute(self, query: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Execute the coordinator logic for a user query.

        Args:
            query: The user's question
            conversation_history: Optional list of previous messages in format:
                [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]

        Returns:
            Dict with coordinator_response and metadata
        """
        start_time = time.time()
        logger.info(f"GenieCoordinator executing query: {query[:100]}...")

        # --- PHASE 2: Emit execution_start lifecycle event for genie ---
        try:
            from datetime import datetime, timezone
            self._emit_event("execution_start", {
                "profile_type": "genie",
                "session_id": self.parent_session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "turn_id": getattr(self, 'turn_number', 1),
                "profile_tag": self.genie_profile.get("tag", "GENIE"),
                "query": query[:200] if query else "",
                "available_slaves": len(self.slave_profiles)
            })
            logger.info("✅ Emitted execution_start event for genie profile")
        except Exception as e:
            # Silent failure - don't break execution
            logger.warning(f"Failed to emit execution_start event: {e}")
        # --- PHASE 2 END ---

        # Emit coordination start event
        self._emit_event("genie_coordination_start", {
            "genie_session_id": self.parent_session_id,
            "session_id": self.parent_session_id,
            "profile_tag": self.genie_profile.get("tag", "GENIE"),
            "query": query[:200] if query else "",
            "slave_profiles": [
                {
                    "id": p.get("id"),
                    "tag": p.get("tag", "UNKNOWN"),
                    "name": p.get("name", ""),
                    "profile_type": p.get("profile_type", "tool_enabled")
                }
                for p in self.slave_profiles
            ]
        })

        # --- KG enrichment for coordinator context ---
        try:
            # Agent Memory — the user_memory module's recalled block, set by the engine after
            # its context-window assembly (the coordinator is built before that assembly runs)
            _mem = getattr(self, "memory_context", None)
            if _mem and _mem not in self.system_prompt:
                self.system_prompt += f"\n\n{_mem}"
            from trusted_data_agent.components.manager import get_component_context_enrichment
            kg_text, kg_details = await get_component_context_enrichment(
                query=query,
                profile_id=self.genie_profile.get("id"),
                user_uuid=self.user_uuid,
                # the coordinator's OWN session (parent_session_id is the master session
                # its experts are spawned under) — so the coordinator's next question
                # carries the entities its previous routing turn touched
                session_id=getattr(self, "parent_session_id", None),
            )
            if kg_text and kg_details:
                self.system_prompt += f"\n\n{kg_text}"
                self.kg_enrichment_event = {
                    "enrichments": kg_details,
                    "total_entities": sum(d.get("entity_count", 0) for d in kg_details),
                    "total_relationships": sum(d.get("relationship_count", 0) for d in kg_details),
                    "session_id": self.parent_session_id,
                }
                self._emit_event("kg_enrichment", self.kg_enrichment_event)
                logger.info(f"KG enrichment for genie: {self.kg_enrichment_event['total_entities']} entities, "
                            f"{self.kg_enrichment_event['total_relationships']} relationships")
        except Exception as e:
            logger.warning(f"KG enrichment for genie coordinator failed: {e}")

        try:
            # Build input messages with system prompt and conversation history
            _history_msgs = []

            # Add conversation history if provided (for multi-turn context)
            if conversation_history:
                logger.info(f"Including {len(conversation_history)} messages from conversation history")
                from trusted_data_agent.agent import agent_history as _ah
                for msg in conversation_history:
                    role = _ah.role_of(msg)          # the one role rule (agent/agent_history.py)
                    content = _ah.text_of(msg)
                    if role == "user":
                        _history_msgs.append(HumanMessage(content=content))
                    elif role == "assistant":
                        _history_msgs.append(AIMessage(content=content))
            # S2 The Attested Cache: the stable prefix (system + history) is marked for the
            # provider's cache; with caching off these are the plain messages as before.
            from trusted_data_agent.agent import prompt_cache as _pc
            _sys_msg, _history_msgs, self._cache_prefix_hash, self._cache_pol = _pc.langchain_prefix(
                self.system_prompt, _history_msgs, profile=self.genie_profile, provider=self.provider, model=self.model)
            messages = [_sys_msg] + _history_msgs

            # Add the current query, prepending user_context skill block if present
            uc_block = self.skill_result.get_user_context_block() if self.skill_result else ""
            human_content = f"{uc_block}\n\n{query}" if uc_block else query
            messages.append(HumanMessage(content=human_content))

            # Reset tracking for this execution
            self.llm_call_count = 0
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.invoked_profiles = []  # Reset profile invocation tracking

            output = ""
            tools_used = []
            _routing_tool_call_count = 0  # Number of parallel tool calls in routing decision
            _passed_through = False        # True when early break skipped synthesis LLM
            _passed_through_html = None    # Full slave HTML for pass-through display (vs text-only output)

            # Use astream_events to track LLM calls with token usage
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

                # S1: provisional answer text, as the coordinator's model writes it
                if event_kind == "on_chat_model_stream" and self.answer_stream is not None:
                    try:
                        from trusted_data_agent.llm.response_text import text_from_content as _tfc
                        _t = _tfc(getattr(event_data.get("chunk"), "content", None))
                        if _t:
                            self.answer_stream.delta(_t)
                    except Exception as _sd:  # noqa: BLE001
                        logger.debug(f"[Genie] stream delta skipped: {_sd}")

                # --- Status indicator: LLM busy on start ---
                if event_kind in ("on_llm_start", "on_chat_model_start"):
                    # In-flight per-run budget cap — before this coordinator call is paid for.
                    # Autonomous turns only; no-op on an interactive turn (no principal).
                    try:
                        from trusted_data_agent.core import agent_authority as _aa
                        _bok, _bwhy = _aa.check_budget_before_call(self._run_cost_usd)
                        if not _bok:
                            logger.warning(
                                f"[Genie] per-run budget cap halted coordination: {_bwhy}")
                            self._emit_event("agent_budget_exceeded", {
                                "reason": _bwhy, "cost_usd": self._run_cost_usd})
                            break
                    except Exception as _gbe:  # a budget-check bug must never break a turn
                        logger.debug(f"[Genie] budget check skipped: {_gbe}")
                    self._emit_event("status_indicator_update", {
                        "target": "llm", "state": "busy"
                    })

                # Note: on_tool_start/on_tool_end/on_tool_error events here are for
                # SlaveSessionTool invocations (child profile calls) AND component tools
                # (TDA_Charting, etc.). SlaveSessionTools emit their own events internally.
                # Component tools need explicit event emission for Live Status visibility.

                # Emit event when a component tool is invoked (for Live Status display)
                if event_kind == "on_tool_start" and event_name in self.component_tool_names:
                    self._emit_event("genie_component_invoked", {
                        "tool_name": event_name,
                        "session_id": self.parent_session_id
                    })

                # Capture component payloads from direct component tool calls
                # (TDA_Charting, etc. — merged at init time alongside SlaveSessionTools)
                if event_kind == "on_tool_end":
                    _raw_output = event_data.get("output", "")
                    # LangGraph wraps tool returns in a ToolMessage; extract plain string
                    tool_output = (
                        _raw_output.content
                        if hasattr(_raw_output, "content")
                        else str(_raw_output) if _raw_output else ""
                    )
                    from trusted_data_agent.components.utils import extract_component_payload
                    _parsed = extract_component_payload(tool_output)
                    if _parsed:
                        # Surface component-piggybacked Live Status events (e.g. a
                        # progressive-disclosure section load) BEFORE the payload is
                        # stored. The other two engines already do this
                        # (phase_executor / conversation_agent); without it the
                        # coordinator's own library fetches were invisible in the
                        # trace and their cost could not be attributed to the LLM
                        # call that carries them.
                        _cmeta = _parsed.get("metadata") or {}
                        for _evt in (_cmeta.pop("_component_llm_events", None) or []):
                            self._emit_event(_evt.get("type", "component_llm_event"), {
                                **( _evt.get("details") or {} ),
                                "step": _evt.get("step"),
                                "type": _evt.get("type"),
                                "session_id": self.parent_session_id,
                            })
                        self.component_payloads.append(_parsed)
                        logger.info(f"[Genie] Captured component payload: {_parsed['component_id']}")
                        # Emit completion event for Live Status display
                        self._emit_event("genie_component_completed", {
                            "tool_name": event_name,
                            "component_id": _parsed["component_id"],
                            "success": True,
                            "session_id": self.parent_session_id
                        })
                    elif (
                        _routing_tool_call_count == 1           # routing called exactly one expert
                        and self.llm_call_count == 1            # no re-dispatch: still the first LLM iteration
                        and event_name not in self.component_tool_names  # it's a slave tool
                        and not conversation_history             # no cross-turn context to weave
                        and self.invoked_profiles               # expert tag already captured
                    ):
                        # Pass-through: break before synthesis LLM fires.
                        # Safe because _routing_tool_call_count == 1 AND llm_call_count == 1
                        # together guarantee the routing LLM made exactly one decision with
                        # one expert, so this on_tool_end is the last tool event.
                        # llm_call_count == 1 prevents this from firing when the coordinator
                        # re-dispatches to the same expert a second time (llm_call_count > 1).
                        invoked_tag = self.invoked_profiles[0]
                        invoked_profile = next(
                            (p for p in self.slave_profiles if p.get("tag") == invoked_tag),
                            None
                        )
                        invoked_type = invoked_profile.get("profile_type", "") if invoked_profile else ""
                        # Pass-through is safe when tool_enabled experts already run their
                        # own synthesis internally — the coordinator receives final_answer_text,
                        # not raw data. Synthesis only earns its cost when multiple experts
                        # need combining or conversation history is present.
                        #
                        # Exception: if a knowledge-only expert (rag_focused / llm_only) was
                        # called but an executor expert (tool_enabled) also exists in the
                        # profile, do NOT pass through. Mandatory pipelines like
                        # TDDIC → TDEXO require the synthesis LLM to fire so it can decide
                        # whether to invoke the executor as the next step.
                        _has_uncalled_executor = (
                            invoked_type in ("rag_focused", "llm_only")
                            and any(
                                p.get("profile_type") == "tool_enabled"
                                for p in self.slave_profiles
                                if p.get("tag") != invoked_tag
                            )
                        )
                        if _has_uncalled_executor:
                            logger.info(
                                f"[Genie] Pass-through skipped: knowledge expert @{invoked_tag} "
                                f"({invoked_type}) answered but tool_enabled executor exists — "
                                "synthesis LLM will evaluate whether to invoke executor"
                            )
                        elif tool_output:
                            output = tool_output
                            # Retrieve the slave's full HTML response (stored by _execute_and_poll).
                            # Used by the caller to display rich content (tables, charts, etc.)
                            # while keeping `output` (plain text) available for LLM if needed.
                            html_key = f"{self.parent_session_id}:{invoked_tag}"
                            _passed_through_html = _slave_html_responses.pop(html_key, None)
                            _passed_through = True
                            logger.info(
                                f"[Genie] Pass-through: single {invoked_type} expert @{invoked_tag}, "
                                f"no prior context — breaking before synthesis LLM"
                            )
                            break  # Exit astream_events; synthesis LLM never fires

                # Track token usage from LLM calls
                # Some providers emit on_llm_end, others emit on_chat_model_end
                if event_kind in ("on_llm_end", "on_chat_model_end"):
                    llm_output = event_data.get("output", {})

                    # Extract token usage
                    input_tokens = 0
                    output_tokens = 0

                    if hasattr(llm_output, 'usage_metadata') and llm_output.usage_metadata:
                        usage = llm_output.usage_metadata
                        if isinstance(usage, dict):
                            input_tokens = usage.get('input_tokens', 0) or 0
                            output_tokens = usage.get('output_tokens', 0) or 0
                        else:
                            input_tokens = getattr(usage, 'input_tokens', 0) or 0
                            output_tokens = getattr(usage, 'output_tokens', 0) or 0

                    self.total_input_tokens += input_tokens
                    self.total_output_tokens += output_tokens

                    # Emit LLM step event
                    self.llm_call_count += 1

                    # Determine step type based on output
                    has_tool_calls = hasattr(llm_output, 'tool_calls') and llm_output.tool_calls
                    if has_tool_calls and self.answer_stream is not None:
                        try:   # S1: a routing message is a decision, not the answer
                            if self.answer_stream.raw_text:
                                self.answer_stream.reset()
                        except Exception:  # noqa: BLE001
                            pass

                    # Capture how many parallel experts the routing LLM requested.
                    # Used by the on_tool_end pass-through check to distinguish
                    # single-expert routing (count=1) from multi-expert (count>1).
                    if self.llm_call_count == 1 and has_tool_calls:
                        _routing_tool_call_count = len(llm_output.tool_calls)

                    if self.llm_call_count == 1:
                        step_name = "Routing Decision" if has_tool_calls else "Response Generation"
                    else:
                        step_name = "Response Synthesis" if not has_tool_calls else f"Routing Decision #{self.llm_call_count}"

                    # Calculate cost for this Genie coordinator LLM call — the one cache-aware rule (S2)
                    from trusted_data_agent.agent import prompt_cache as _pc
                    _cu = _pc.langchain_usage(getattr(llm_output, "usage_metadata", None), getattr(llm_output, "response_metadata", None))
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
                    self._run_cost_usd += (call_cost or 0.0)   # for the in-flight budget cap
                    from trusted_data_agent.agent import turn_budget as _tbud
                    _tbud.record_tokens(input_tokens + output_tokens)   # S5: the tokens unit
                    self.turn_cost_usd = getattr(self, "turn_cost_usd", 0.0) + (call_cost or 0.0)  # S2: Σ cache-priced calls this turn

                    # Stable per-call identity so the frontend's per-call cost ledger counts
                    # this coordinator call exactly once across SSE channels (the coordinator
                    # has no paired token_update — genie_llm_step is the sole cost carrier).
                    import uuid as _uuid
                    self._emit_event("genie_llm_step", {
                        "step_number": self.llm_call_count,
                        "step_name": step_name,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cached_tokens": cached_tokens,   # S2: prompt tokens served from cache
                        "provider": _srv_provider, "model": _srv_model,   # S4: the model that served
                        "served_by_fallback": bool(_served),
                        "session_id": self.parent_session_id,
                        "call_id": str(_uuid.uuid4()),
                        "cost_usd": call_cost  # NEW: Track cost for Genie coordinator LLM calls
                    })

                    logger.info(f"[Genie] LLM Step {self.llm_call_count} ({step_name}): {input_tokens} in / {output_tokens} out")

                    # --- Status indicator: LLM idle after completion ---
                    self._emit_event("status_indicator_update", {
                        "target": "llm", "state": "idle"
                    })

                # Capture final output from chain end
                elif event_kind == "on_chain_end" and event_name == "LangGraph":
                    chain_output = event_data.get("output", {})
                    if isinstance(chain_output, dict) and "messages" in chain_output:
                        # Extract final AI message for response
                        for msg in reversed(chain_output["messages"]):
                            if hasattr(msg, 'content') and hasattr(msg, 'type'):
                                if msg.type == 'ai' and msg.content:
                                    content = msg.content
                                    if isinstance(content, list):
                                        # Some providers (e.g. Gemma via OpenRouter) return
                                        # content as [{type:text, text:...}] instead of a plain str
                                        content = "".join(
                                            p.get("text", "") if isinstance(p, dict) else str(p)
                                            for p in content
                                        )
                                    output = content
                                    break
                                # Track tool calls
                                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                    for tc in msg.tool_calls:
                                        tool_name = tc.get('name', '') if isinstance(tc, dict) else getattr(tc, 'name', '')
                                        if tool_name and tool_name not in tools_used:
                                            tools_used.append(tool_name)

            # Strip model-specific thinking artifacts from the final answer.
            # Gemma and some other models prefix the response with their internal
            # reasoning step (e.g. "thought\n..."). These must not reach the user.
            if output:
                import re as _re
                # Strip leading "thought\n", "Thought:\n", "<think>...</think>", etc.
                output = _re.sub(r'^(thought|Thought:?)\s*\n+', '', output, flags=_re.IGNORECASE)
                output = _re.sub(r'^<think>.*?</think>\s*', '', output, flags=_re.DOTALL | _re.IGNORECASE)
                output = output.strip()

            # Emit synthesis events only when synthesis actually ran.
            # Pass-through queries break early — no synthesis LLM was called.
            if _passed_through:
                self._emit_event("genie_synthesis_skipped", {
                    "profile_tag": self.invoked_profiles[0] if self.invoked_profiles else None,
                    "reason": "Single knowledge expert, no prior context",
                    "session_id": self.parent_session_id
                })
            else:
                self._emit_event("genie_synthesis_start", {
                    "profiles_consulted": self.invoked_profiles,
                    "session_id": self.parent_session_id
                })

            # Calculate duration
            total_duration_ms = int((time.time() - start_time) * 1000)

            if not _passed_through:
                # Prepare response preview for status window (truncate if too long)
                response_preview = None
                if output:
                    response_preview = output[:500] + "..." if len(output) > 500 else output

                # Emit synthesis complete event with the response (matches other profiles' "LLM Synthesis Results")
                self._emit_event("genie_synthesis_complete", {
                    "profiles_consulted": self.invoked_profiles,
                    "session_id": self.parent_session_id,
                    "synthesized_response": response_preview,
                    "success": True
                })

            # Emit coordination complete event (without response - it's in genie_synthesis_complete)
            # Calculate turn cost for completion card
            from trusted_data_agent.core.cost_manager import get_cost_manager
            _cost_mgr = get_cost_manager()
            _turn_cost = _cost_mgr.calculate_cost(
                provider=self.provider,
                model=self.model,
                input_tokens=self.total_input_tokens,
                output_tokens=self.total_output_tokens
            )
            self._emit_event("genie_coordination_complete", {
                "total_duration_ms": total_duration_ms,
                "profiles_used": self.invoked_profiles,  # Use tracked invoked profiles
                "success": True,
                "session_id": self.parent_session_id,
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "cost_usd": _turn_cost
            })

            # --- PHASE 2: Emit execution_complete lifecycle event for genie ---
            try:
                from datetime import datetime, timezone
                self._emit_event("execution_complete", {
                    "profile_type": "genie",
                    "session_id": self.parent_session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "profile_tag": self.genie_profile.get("tag", "GENIE"),
                    "experts_consulted": len(self.invoked_profiles),
                    "tools_used": tools_used,
                    "total_input_tokens": self.total_input_tokens,
                    "total_output_tokens": self.total_output_tokens,
                    "duration_ms": total_duration_ms,
                    "success": True
                })
                logger.info("✅ Emitted execution_complete event for genie profile")
            except Exception as e:
                # Silent failure - don't break execution
                logger.warning(f"Failed to emit execution_complete event: {e}")
            # --- PHASE 2 END ---

            logger.info(f"GenieCoordinator completed. Tools used: {tools_used}")

            # Filter slave_sessions to only include sessions for tools actually used in this turn
            # This ensures per-turn tracking instead of returning all accumulated sessions
            turn_slave_sessions = {}
            for tool_name in tools_used:
                # Tool names are invoke_{profile_tag}, extract the tag
                profile_tag = tool_name.replace("invoke_", "") if tool_name.startswith("invoke_") else tool_name
                # Find the profile_id for this tag
                for profile in self.slave_profiles:
                    if profile.get("tag") == profile_tag:
                        profile_id = profile.get("id")
                        cache_key = f"{self.parent_session_id}:{profile_id}"
                        if cache_key in _slave_session_cache:
                            # Store with profile_tag as key for frontend display
                            turn_slave_sessions[profile_tag] = _slave_session_cache[cache_key]
                        break

            return {
                "coordinator_response": output,
                "coordinator_html": _passed_through_html,  # Full slave HTML for pass-through display; None when synthesis ran
                "tools_used": tools_used,
                "slave_sessions": turn_slave_sessions,
                "genie_events": self.collected_events,  # For plan reload
                "component_payloads": self.component_payloads,  # Component tool render specs
                "kg_enrichment_event": self.kg_enrichment_event,  # KG enrichment for persistence
                "success": True,
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens
            }

        except Exception as e:
            logger.error(f"GenieCoordinator execution error: {e}", exc_info=True)

            # Calculate duration
            total_duration_ms = int((time.time() - start_time) * 1000)

            # Emit error completion event
            self._emit_event("genie_coordination_complete", {
                "total_duration_ms": total_duration_ms,
                "profiles_used": [],
                "success": False,
                "error": str(e),
                "session_id": self.parent_session_id,
                "cost_usd": 0.0
            })

            # --- PHASE 2: Emit execution_error lifecycle event for genie ---
            try:
                from datetime import datetime, timezone
                # Classify error type
                error_str = str(e).lower()
                if "rate limit" in error_str or "429" in error_str:
                    error_type = "rate_limit"
                elif "quota" in error_str or "insufficient" in error_str:
                    error_type = "quota_exceeded"
                elif "timeout" in error_str:
                    error_type = "timeout"
                else:
                    error_type = "coordination_error"

                self._emit_event("execution_error", {
                    "profile_type": "genie",
                    "session_id": self.parent_session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "profile_tag": self.genie_profile.get("tag", "GENIE"),
                    "error_message": str(e),
                    "error_type": error_type,
                    "experts_consulted": len(self.invoked_profiles),
                    "partial_input_tokens": self.total_input_tokens,
                    "partial_output_tokens": self.total_output_tokens,
                    "duration_ms": total_duration_ms,
                    "success": False
                })
                logger.info(f"✅ Emitted execution_error event for genie profile (error_type: {error_type})")
            except Exception as emit_error:
                # Silent failure - don't break error handling flow
                logger.warning(f"Failed to emit execution_error event: {emit_error}")
            # --- PHASE 2 END ---

            # C18: the honest sentence, never the raw error (it is in `error` and the log)
            from trusted_data_agent.agent import turn_failure as _tf
            from trusted_data_agent.core.utils import unwrap_exception as _unwrap
            return {
                "coordinator_response": _tf.answer(_unwrap(e)),
                "tools_used": [],
                "slave_sessions": {},  # No sessions for failed turn
                "genie_events": self.collected_events,  # For plan reload (includes error event)
                "success": False,
                "error": str(e),
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens
            }
        finally:
            self._cleanup_session_state()

    def get_used_slave_sessions(self) -> Dict[str, str]:
        """Get map of profile_id -> session_id for child sessions of this coordinator.

        Note: Method name preserved for API compatibility.
        """
        prefix = f"{self.parent_session_id}:"
        return {k[len(prefix):]: v for k, v in _slave_session_cache.items() if k.startswith(prefix)}

    def load_existing_slave_sessions(self, existing_sessions: List[Dict[str, Any]]):
        """
        Pre-populate the session cache with existing child sessions from database.

        This preserves conversational context across multiple queries by reusing
        existing child sessions instead of creating new ones.

        Args:
            existing_sessions: List of session records from get_genie_slave_sessions()
                Each record should have 'slave_session_id' and 'slave_profile_id'
                (field names preserved for API compatibility)
        """
        for session_record in existing_sessions:
            slave_session_id = session_record.get('slave_session_id')
            slave_profile_id = session_record.get('slave_profile_id')

            if slave_session_id and slave_profile_id:
                cache_key = f"{self.parent_session_id}:{slave_profile_id}"
                _slave_session_cache[cache_key] = slave_session_id
                logger.info(f"Loaded existing child session {slave_session_id} for profile {slave_profile_id}")

        logger.info(f"Pre-loaded {len(existing_sessions)} existing child sessions into cache")

    def clear_session_cache(self):
        """Clear the session cache entries for this coordinator's parent session (useful for testing)."""
        prefix = f"{self.parent_session_id}:"
        stale_keys = [k for k in list(_slave_session_cache) if k.startswith(prefix)]
        for k in stale_keys:
            session_id = _slave_session_cache.pop(k, None)
            if session_id:
                _slave_session_locks.pop(session_id, None)

    def _cleanup_session_state(self):
        """Remove per-execution state for this parent session from module-level dicts.

        Called in a finally block after execute() completes to prevent unbounded
        memory growth in long-running processes with many genie sessions.
        The slave session cache is intentionally NOT cleared here — it persists
        across turns so child sessions can be reused within the same parent session.
        """
        _event_callbacks.pop(self.parent_session_id, None)
        _provenance_chains.pop(self.parent_session_id, None)
        # Clear any leftover HTML responses for this session (normally consumed by execute())
        stale_html_keys = [k for k in list(_slave_html_responses) if k.startswith(f"{self.parent_session_id}:")]
        for k in stale_html_keys:
            _slave_html_responses.pop(k, None)
