"""
Coordinate Engine — genie profile execution.

Wraps GenieCoordinator to provide multi-expert coordination for genie profiles.
Routes queries to child (slave) profiles and synthesises their responses.

Profile type:  ``genie``  (IFOC label: "Coordinate", colour: purple)

Notes
-----
- This engine is registered with EngineRegistry for profile resolution.
- In Phase 3 the genie dispatch path in execution_service.py still calls
  execute_genie() directly; CoordinateEngine.run() is not yet wired into
  the PlanExecutor dispatch chain.
- Phase 4 will unify dispatch so genie profiles also flow through
  EngineRegistry.resolve() → CoordinateEngine.run().
"""

from __future__ import annotations
from trusted_data_agent.agent import answer_checks as _answer_checks

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional

from .registry import EngineRegistry
from .base import ExecutionEngine

if TYPE_CHECKING:
    from trusted_data_agent.agent.executor import PlanExecutor

app_logger = logging.getLogger("quart.app")


_OK_STATUS = frozenset({"success", "ok", "complete", "completed"})


async def _summarize_slaves(genie_events: list, user_uuid: str, session_manager) -> tuple:
    """Build the per-expert (slave) summary for a coordinator turn.

    This is the rolled-up *outcome + resource* signal the coordinator turn carries
    (see Agent Ops & Evaluation §12a). Each consulted expert is identified from the
    coordinator's own ``genie_*`` events; its authoritative status/tokens/cost are
    read from the expert's persisted child-session turn (the slaves run as normal
    turns and bill their own consumption there — we only *attribute* their cost to
    the coordinator turn for evaluation, never re-charge it).

    Returns ``(slaves, agg_input_tokens, agg_output_tokens, agg_cost_usd)`` where
    ``slaves`` is a list of
    ``{profile_tag, slave_session_id, slave_turn, status, input_tokens, output_tokens, cost_usd}``.
    Pure-ish + defensive: never raises (a lookup miss yields a best-effort entry).
    """
    # Identify each invoked expert + its child session id from the coordinator events.
    by_tag: dict = {}
    order: list = []
    for e in genie_events or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") not in ("genie_slave_invoked", "genie_slave_progress",
                                  "genie_slave_completed"):
            continue
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        tag = p.get("profile_tag")
        if not tag:
            continue
        if tag not in by_tag:
            by_tag[tag] = None
            order.append(tag)
        sid = p.get("slave_session_id")
        if sid and not by_tag.get(tag):
            by_tag[tag] = sid

    slaves: list = []
    agg_in = agg_out = 0
    agg_cost = 0.0
    for tag in order:
        sid = by_tag.get(tag)
        entry = {"profile_tag": tag, "slave_session_id": sid, "status": "success",
                 "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        if sid:
            try:
                sd = await session_manager.get_session(user_uuid, sid)
                wh = (sd or {}).get("last_turn_data", {}).get("workflow_history", [])
                if wh:
                    st = wh[-1]
                    entry["status"] = st.get("status", "success")
                    # WHICH turn of the child session this expert's answer is. The
                    # summary already reads that turn for status/tokens/cost; naming it
                    # is what lets a vote on the coordinator's answer reach the expert
                    # turn that produced it (docs/roadmap/VOTE_SIGNAL_PLAN.md §8).
                    # Absent on coordinator turns persisted before this — those simply
                    # do not propagate, rather than guessing a turn number.
                    _t = st.get("turn", st.get("turn_number"))
                    if _t is not None:
                        entry["slave_turn"] = _t
                    entry["input_tokens"] = int(st.get("turn_input_tokens") or 0)
                    entry["output_tokens"] = int(st.get("turn_output_tokens") or 0)
                    entry["cost_usd"] = float(st.get("turn_cost") or 0.0)
                    entry["profile_type"] = st.get("profile_type")
                    # Timing for the coordination waterfall: the child turn stamps
                    # `duration_ms` and a completion `timestamp` (≈ end). Derive the
                    # start so the UX can position bars and detect real overlap, not
                    # fabricate it. Epoch seconds; absent → 0 (UX falls back to order).
                    entry["duration_ms"] = int(st.get("duration_ms") or 0)
                    try:
                        from datetime import datetime
                        ts = st.get("timestamp")
                        end = datetime.fromisoformat(str(ts)).timestamp() if ts else 0.0
                    except (ValueError, TypeError):
                        end = 0.0
                    entry["completed_at"] = round(end, 3) if end else 0.0
                    entry["started_at"] = round(end - entry["duration_ms"] / 1000.0, 3) if end else 0.0
            except Exception as _e:
                app_logger.debug(f"[Genie] slave summary lookup failed for {tag}/{sid}: {_e}")
        agg_in += entry["input_tokens"]
        agg_out += entry["output_tokens"]
        agg_cost += entry["cost_usd"]
        slaves.append(entry)
    return slaves, agg_in, agg_out, agg_cost


@EngineRegistry.register
class CoordinateEngine(ExecutionEngine):
    """Multi-expert coordination engine for ``genie`` profiles."""

    profile_type = "genie"

    async def run(self, executor: "PlanExecutor") -> AsyncGenerator[str, None]:  # type: ignore[override]
        """Not used in Phase 3 — genie is dispatched via execute_genie().

        Phase 4 will wire genie profiles into the unified EngineRegistry dispatch
        so this method becomes the primary execution path.
        """
        raise NotImplementedError(
            "CoordinateEngine must be invoked via execute_genie() in Phase 3. "
            "Phase 4 will add unified PlanExecutor dispatch."
        )
        yield  # type: ignore[misc]  — marks this as AsyncGenerator; unreachable

    async def execute_genie(
        self,
        user_uuid: str,
        session_id: str,
        user_input: str,
        event_handler,
        genie_profile: dict,
        task_id: str = None,
        disabled_history: bool = False,
        current_nesting_level: int = 0,
        is_session_primer: bool = False,
        attachments: list = None,
        skill_result=None,
        answer_stream=None,
    ) -> Optional[Dict[str, Any]]:
        """Execute a genie coordination turn.

        Extracted from execution_service._run_genie_execution().
        Builds and runs GenieCoordinator, streams events via event_handler,
        persists turn data, and returns the final payload dict.

        Args:
            user_uuid: UUID of the user who owns the sessions.
            session_id: Parent genie session ID.
            user_input: The user's query text.
            event_handler: Async callable (payload, event_type) → None.
            genie_profile: Genie profile configuration dict.
            task_id: Optional task ID for REST API tracking.
            disabled_history: If True, skip loading conversation history.
            current_nesting_level: Current depth in nested genie hierarchy.
            is_session_primer: If True, skip session name generation.
            attachments: Optional list of uploaded document attachments.
            skill_result: Optional pre-resolved skill content.

        Returns:
            Final payload dict (same structure as other profile types), or None on error.
        """
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.llm.langchain_adapter import create_langchain_llm
        from trusted_data_agent.agent.genie_coordinator import GenieCoordinator

        config_manager = get_config_manager()
        profile_id = genie_profile.get("id")
        profile_tag = genie_profile.get("tag", "GENIE")
        # Canonical turn start for wall-clock latency (eval KPI). execute_genie is
        # dispatched directly (not via CoordinateEngine.run(executor)), so there is
        # no `executor` instance in scope — time the turn locally instead.
        _turn_start_time = time.time()

        try:
            # Check depth limit at runtime
            global_settings = config_manager.get_genie_global_settings()
            max_depth = int(global_settings.get('maxNestingDepth', {}).get('value', 3))

            if current_nesting_level >= max_depth:
                await event_handler({
                    "error": f"Maximum Genie nesting depth ({max_depth}) exceeded. Cannot execute nested Genie at level {current_nesting_level}."
                }, "error")
                app_logger.warning(f"Genie execution blocked: nesting level {current_nesting_level} >= max depth {max_depth}")
                return None

            # Note: genie_coordination_start is emitted by GenieCoordinator with full details

            # Validate genie configuration
            genie_config = genie_profile.get("genieConfig", {})
            slave_profile_ids = genie_config.get("slaveProfiles", [])

            if not slave_profile_ids:
                await event_handler({
                    "error": "Genie profile has no child profiles configured."
                }, "error")
                return None

            # Get child profile details
            slave_profiles = []
            for pid in slave_profile_ids:
                slave_profile = config_manager.get_profile(pid, user_uuid)
                if slave_profile:
                    slave_profiles.append(slave_profile)
                else:
                    app_logger.warning(f"Genie profile {profile_id} references missing child profile {pid}")

            if not slave_profiles:
                await event_handler({
                    "error": "No valid child profiles found for this genie."
                }, "error")
                return None

            # Trust-aware routing (opt-in, per coordinator): when
            # genieConfig.requireTrustedExperts is true, the coordinator routes
            # ONLY to experts whose Trust-Membrane badge is 🟢 (signed and
            # unchanged, including every ingredient) — compositional trust
            # enforced at routing time. An unsigned/changed expert is excluded
            # (fail-closed: a resolution error also excludes). If the Trust
            # Membrane package is physically absent (removable Ontology Suite),
            # the flag cannot be honoured and routing proceeds unrestricted with
            # a loud warning — absence-tolerance over a bricked coordinator.
            if genie_config.get("requireTrustedExperts"):
                try:
                    from trusted_data_agent.core.trust import composition as trust_comp
                    vetted, excluded = [], []
                    for slave in slave_profiles:
                        state = None
                        try:
                            at = trust_comp.resolve_profile_trust(
                                slave["id"], user_uuid, profile=slave)
                            state = getattr(getattr(at, "state", None), "value", None) or (
                                at.to_dict().get("state") if hasattr(at, "to_dict") else None)
                        except Exception:
                            state = None
                        if state == "trusted":
                            vetted.append(slave)
                        else:
                            excluded.append(f"@{slave.get('tag') or slave.get('id')} ({state or 'unresolvable'})")
                    if excluded:
                        app_logger.warning(
                            f"Trust-aware routing: coordinator {profile_id} excluded "
                            f"{len(excluded)} unsigned/changed expert(s): {', '.join(excluded)}")
                    slave_profiles = vetted
                    if not slave_profiles:
                        await event_handler({
                            "error": ("Trust-aware routing: this coordinator requires SIGNED (trusted) "
                                      "experts, but none of its child profiles currently hold a green "
                                      f"trust badge — excluded: {', '.join(excluded)}. Sign the experts "
                                      "(and their ingredients) or disable requireTrustedExperts.")
                        }, "error")
                        return None
                except ImportError:
                    app_logger.warning(
                        "Trust-aware routing requested but the Trust Membrane is not installed — "
                        "routing unrestricted (the flag cannot be honoured without the Ontology Suite).")

            # Annotate each slave with per-slave settings from the coordinator profile
            slave_profile_settings = genie_config.get("slaveProfileSettings", {})
            for slave in slave_profiles:
                settings = slave_profile_settings.get(slave["id"], {})
                slave["full_result_passthrough"] = settings.get("fullResultPassthrough", False)

            # Note: genie_coordination_start is emitted by GenieCoordinator with slave_profiles details

            # Get LLM configuration for coordinator
            llm_config_id = genie_profile.get("llmConfigurationId")
            if not llm_config_id:
                await event_handler({
                    "error": "Genie profile requires an LLM configuration."
                }, "error")
                return None

            # Get effective genie config (merges global settings with profile overrides)
            effective_genie_config = config_manager.get_effective_genie_config(genie_config)

            # Create LangChain LLM from Uderia config with temperature from genie config
            llm_instance = create_langchain_llm(
                llm_config_id,
                user_uuid,
                temperature=float(effective_genie_config['temperature'])
            )

            # Get LLM config details for logging
            llm_configurations = config_manager.get_llm_configurations(user_uuid)
            llm_config = next((c for c in llm_configurations if c.get("id") == llm_config_id), None)
            provider = llm_config.get('provider', 'Unknown') if llm_config else 'Unknown'
            model = llm_config.get('model', 'unknown') if llm_config else 'unknown'

            # Determine base URL - use internal localhost for self-calls
            base_url = "http://localhost:5050"

            # Get auth token from APP_STATE or generate internal token
            # For internal execution, we use a special internal auth mechanism
            from trusted_data_agent.auth.security import create_internal_token
            auth_token = create_internal_token(user_uuid)

            # Create event handler that forwards Genie events to SSE
            async def genie_sse_event_handler(event_type: str, payload: dict):
                """Forward genie coordination events to SSE stream."""
                await event_handler(payload, event_type)

            # --- EPC: Create provenance chain for genie ---
            _genie_provenance = None
            try:
                from trusted_data_agent.core.provenance import ProvenanceChain, get_previous_turn_tip_hash
                prev_tip = await get_previous_turn_tip_hash(user_uuid, session_id)
                _genie_provenance = ProvenanceChain(
                    session_id=session_id, turn_number=0,  # Will be updated below
                    user_uuid=user_uuid, profile_type="genie",
                    previous_turn_tip_hash=prev_tip,
                    event_queue=None,  # Genie doesn't use async generator
                )
                _genie_provenance.add_step("query_intake", user_input, f"Query: {user_input[:100]}")
                _genie_provenance.add_step("profile_resolve", f"{profile_id}:genie:{profile_tag}", f"Profile: @{profile_tag} (genie)")
            except Exception as _epc_err:
                app_logger.debug(f"EPC genie init: {_epc_err}")

            # Build and execute coordinator
            from trusted_data_agent.agent.primer import is_briefing_turn as _is_briefing_turn
            coordinator = GenieCoordinator(
                genie_profile=genie_profile,
                slave_profiles=slave_profiles,
                user_uuid=user_uuid,
                parent_session_id=session_id,
                auth_token=auth_token,
                llm_instance=llm_instance,
                base_url=base_url,
                event_callback=lambda t, p: asyncio.create_task(genie_sse_event_handler(t, p)),
                genie_config=effective_genie_config,
                current_nesting_level=current_nesting_level,
                provenance=_genie_provenance,
                skill_result=skill_result,
                # A briefing primer offers the coordinator NO expert tools — otherwise it
                # routes the briefing to an expert, whose OWN primer then fires too.
                tools_enabled=not _is_briefing_turn(genie_profile, is_session_primer),
                answer_stream=answer_stream,  # S1
            )

            # Load existing child sessions to preserve context across multiple queries
            existing_slaves = await session_manager.get_genie_slave_sessions(session_id, user_uuid)
            if existing_slaves:
                coordinator.load_existing_slave_sessions(existing_slaves)
                app_logger.info(f"Loaded {len(existing_slaves)} existing child sessions for Genie session {session_id}")

            # Build conversation history from session's chat_object
            # This provides multi-turn context to the Genie coordinator
            # Respects disabled_history flag (for single-turn context mode) and isValid flag (for purged/toggled turns)
            session_data = await session_manager.get_session(user_uuid, session_id)

            # Compute turn number for lifecycle events (same logic as executor.py)
            workflow_history = session_data.get('last_turn_data', {}).get('workflow_history', []) if session_data else []
            turn_number = len(workflow_history) + 1
            coordinator.turn_number = turn_number
            if answer_stream is not None:
                answer_stream.turn_id = turn_number
            # Update provenance turn number now that we know it
            if _genie_provenance:
                _genie_provenance.turn_number = turn_number

            # --- Tier 1.3 inbound prompt-injection guardrail (genie path; charter #14) ---
            # Genie is dispatched directly (no PlanExecutor), so it scans inbound here — the
            # analogue of the executor's dispatch-entry scan for the other four engines.
            # enforce + injection → self-contained refusal terminal (coordination never runs);
            # observe/annotate → surface live + stamp guardrail_inbound. Deterministic, fails open.
            _genie_inbound_event = None
            try:
                from trusted_data_agent.agent.guardrails import runtime as _gr_rt, adapter as _gr_ad
                _ib_text, _genie_inbound_event = _gr_rt.scan_inbound(
                    user_input, profile=genie_profile, profile_class="genie",
                    user_uuid=user_uuid, session_id=session_id)
                if (_genie_inbound_event or {}).get("acted"):
                    await event_handler(
                        {"step": "Guardrail (input)", "type": "guardrail",
                         "details": _genie_inbound_event, "session_id": session_id}, "guardrail")
                    if _genie_inbound_event.get("action") == "block":
                        _fh, _ge, _fa, _ts = _gr_ad.build_block_turn(
                            user_input=user_input, event=_genie_inbound_event,
                            profile_tag=profile_tag, profile_type="genie",
                            turn_number=turn_number, session_id=session_id,
                            duration_ms=int((time.time() - _turn_start_time) * 1000))
                        try:
                            await session_manager.add_message_to_histories(
                                user_uuid, session_id, "assistant",
                                content=_ts["final_summary"], html_content=_fh,
                                is_session_primer=is_session_primer)
                        except Exception as _e:  # noqa: BLE001
                            app_logger.warning(f"[Genie] inbound block history save failed: {_e}")
                        await event_handler(_fa, "final_answer")
                        try:
                            await session_manager.update_last_turn_data(user_uuid, session_id, _ts)
                        except Exception as _e:  # noqa: BLE001
                            app_logger.warning(f"[Genie] inbound block turn save failed: {_e}")
                        return {"final_response": _ts["final_summary"],
                                "final_summary_text": _ts["final_summary"],
                                "success": True, "guardrail_blocked": True}
            except Exception as _ib_err:  # noqa: BLE001
                app_logger.debug(f"[Genie] inbound guardrail skipped: {_ib_err}")

            # --- CONTEXT WINDOW MANAGER (Feature-Flagged, Observability) ---
            cw_history_window = 10  # Default fallback
            cw_document_max_chars = None  # Default: use APP_CONFIG limit
            genie_cw_snapshot_event = None  # Capture for turn_data persistence
            try:
                from trusted_data_agent.core.config import APP_CONFIG as _CW_APP_CONFIG
                if _CW_APP_CONFIG.USE_CONTEXT_WINDOW_MANAGER:
                    from components.builtin.context_window.handler import ContextWindowHandler
                    from components.builtin.context_window.base import AssemblyContext

                    cwt = config_manager.get_default_context_window_type(user_uuid)
                    if cwt:
                        enriched_session = dict(session_data) if session_data else {}
                        enriched_session['current_query'] = user_input or ''
                        enriched_session['attachments'] = list(attachments or [])   # the "no_documents_attached" rule reads them

                        # Resolve model context limit
                        model_context_limit = 128_000
                        try:
                            import litellm
                            model_key = f"{provider}/{model}"
                            model_info = litellm.model_cost.get(model_key) or litellm.model_cost.get(model or "")
                            if model_info:
                                model_context_limit = model_info.get("max_input_tokens") or model_info.get("max_tokens") or 128_000
                        except Exception:
                            pass

                        # Apply profile-level context limit override
                        context_limit_override = genie_profile.get("contextLimitOverride")
                        if context_limit_override and isinstance(context_limit_override, int):
                            if context_limit_override < model_context_limit:
                                model_context_limit = context_limit_override

                        # Apply session-level context limit override (takes precedence)
                        session_context_limit = enriched_session.get('session_context_limit_override')
                        if session_context_limit and isinstance(session_context_limit, int):
                            if session_context_limit < model_context_limit:
                                model_context_limit = session_context_limit

                        ctx = AssemblyContext(
                            profile_type="genie",
                            profile_id=profile_id or "",
                            session_id=session_id,
                            user_uuid=user_uuid,
                            session_data=enriched_session,
                            turn_number=turn_number,
                            is_first_turn=turn_number == 1,
                            model_context_limit=model_context_limit,
                            dependencies={},
                            profile_config=genie_profile,
                        )

                        handler = ContextWindowHandler()
                        assembled = await handler.assemble(cwt, ctx)

                        if assembled and assembled.snapshot:
                            app_logger.info(
                                f"Context Window Snapshot: "
                                f"{assembled.snapshot.total_used:,}/{assembled.snapshot.available_budget:,} tokens "
                                f"({assembled.snapshot.utilization_pct:.1f}% utilization)"
                            )
                            snapshot_payload = assembled.snapshot.to_sse_event()
                            genie_cw_snapshot_event = snapshot_payload
                            await event_handler({
                                "step": "Context Window Assembly",
                                "type": "context_window_snapshot",
                                "details": assembled.snapshot.to_summary_text(),
                                "payload": snapshot_payload,
                            }, "notification")

                            # Agent Memory: the user_memory module's recalled block reaches the
                            # coordinator's own prompt (its experts recall through their own
                            # engines); it had no reader before Nov 2026.
                            _cw_mem = assembled.contributions.get("user_memory")
                            if _cw_mem is not None and _cw_mem.content:
                                coordinator.memory_context = _cw_mem.content

                            # Extract budget-aware values for downstream use
                            cw_hist = assembled.contributions.get("conversation_history")
                            if cw_hist and cw_hist.metadata.get("turn_count", 0) > 0:
                                cw_history_window = cw_hist.metadata["turn_count"]
                            app_logger.info(
                                f"Context window: conversation_history budget-aware window={cw_history_window} "
                                f"({cw_hist.tokens_used:,} tokens)" if cw_hist else
                                f"Context window: using default history window={cw_history_window}"
                            )
                            for cm in assembled.snapshot.contributions:
                                if cm.module_id == "document_context":
                                    from trusted_data_agent.agent.executor import document_char_limit
                                    cw_document_max_chars = document_char_limit(cm.tokens_allocated)
                                    app_logger.info(
                                        f"Context window: document_context budget={cm.tokens_allocated:,} tokens "
                                        f"(~{cw_document_max_chars:,} chars)"
                                    )
                                    break
            except Exception as cw_err:
                app_logger.debug(f"[Genie] Context window assembly skipped: {cw_err}")
            # --- CONTEXT WINDOW MANAGER END ---

            # ONE rule for the history every agent receives (agent/agent_history.py); `disabled`
            # is the Alt-key single-turn mode — the agent then receives no history.
            from trusted_data_agent.agent import agent_history as _ah
            conversation_history = (_ah.build(session_data.get("chat_object", []), user_input, cw_history_window,
                                              disabled=bool(disabled_history))
                                    if session_data else [])
            app_logger.info(f"[Genie] Built conversation history with {len(conversation_history)} messages"
                            + (" (history disabled for this turn)" if disabled_history else ""))

            # Prepend document context to user_input for genie if attachments present
            genie_user_input = user_input
            doc_context = None
            if attachments:
                from trusted_data_agent.agent.executor import load_document_context
                doc_context, _ = load_document_context(user_uuid, session_id, attachments, max_total_chars=cw_document_max_chars)
                if doc_context:
                    genie_user_input = f"[User has uploaded documents]\n{doc_context}\n\n[User's question]\n{user_input}"
                # S9 — the coordinator's attachments are this turn's sources too (the grounding
                # gate + the persisted stamp read the registry; the document text above already
                # carries the platform's verified reading). No transcription fallback here.
                try:
                    from trusted_data_agent.llm import image_sources as _isrc
                    _isrc.register_from_manifest(user_uuid, session_id, attachments)
                except Exception as _ase:  # noqa: BLE001
                    app_logger.debug(f"[attachment-sources] genie seam skipped: {_ase}")

            # Execute coordination with conversation context
            result = await coordinator.execute(genie_user_input, conversation_history=conversation_history)

            # Extract the final response
            coordinator_response = result.get('coordinator_response', '')
            # In pass-through mode the coordinator preserves the slave's full HTML response
            # (tables, charts, key observations) separately from the plain-text summary.
            # Fall back to coordinator_response when synthesis ran (coordinator_html is None).
            coordinator_html = result.get('coordinator_html') or coordinator_response
            success = result.get('success', False)

            # Generate component HTML from coordinator's direct component tool calls
            # (TDA_Charting, etc. — component-agnostic, works for any future component)
            from trusted_data_agent.components.utils import generate_component_html
            genie_component_html = generate_component_html(result.get("component_payloads", []))

            # Extract token counts from coordination result IMMEDIATELY (before any other processing)
            input_tokens = result.get('input_tokens', 0) or 0
            output_tokens = result.get('output_tokens', 0) or 0

            # Initialize session totals (updated inside the if-block, used in final_payload)
            session_input_tokens = 0
            session_output_tokens = 0

            # Update session token counts and emit token_update event IMMEDIATELY
            # This ensures the status window shows correct tokens during live execution
            if input_tokens > 0 or output_tokens > 0:
                await session_manager.update_token_count(
                    user_uuid,
                    session_id,
                    input_tokens,
                    output_tokens
                )

                # Get updated session totals and turn number for token_update event
                current_session = await session_manager.get_session(user_uuid, session_id)
                if current_session:
                    workflow_history = current_session.get('last_turn_data', {}).get('workflow_history', [])
                    turn_number = len(workflow_history) + 1
                    session_input_tokens = current_session.get("input_tokens", 0)
                    session_output_tokens = current_session.get("output_tokens", 0)

                    # Calculate cost for genie coordinator LLM call
                    _genie_cost = 0
                    try:
                        from trusted_data_agent.core.cost_manager import get_cost_manager
                        _genie_cost = get_cost_manager().calculate_cost(
                            provider=provider or "Unknown",
                            model=model or "Unknown",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens
                        )
                    except Exception:
                        pass

                    # Emit token_update event IMMEDIATELY (before session name generation)
                    # For genie, turn tokens = statement tokens (single coordination per turn)
                    await event_handler({
                        "statement_input": input_tokens,
                        "statement_output": output_tokens,
                        "turn_input": input_tokens,
                        "turn_output": output_tokens,
                        "total_input": session_input_tokens,
                        "total_output": session_output_tokens,
                        "call_id": f"genie_{turn_number}",
                        "cost_usd": _genie_cost
                    }, "token_update")

                    app_logger.info(f"[Genie] Token update emitted: {input_tokens} in / {output_tokens} out "
                                  f"(session total: {session_input_tokens} in / {session_output_tokens} out)")

            # Log the Genie conversation to session files
            try:
                # Add turn data to workflow_history
                current_session = await session_manager.get_session(user_uuid, session_id)
                workflow_history = current_session.get('last_turn_data', {}).get('workflow_history', [])
                turn_number = len(workflow_history) + 1

                # Get session token totals (after updating them above)
                session_input_tokens = current_session.get("input_tokens", 0)
                session_output_tokens = current_session.get("output_tokens", 0)

                # Collect session name generation events (before creating turn_data)
                # Skip if this is a session primer - only generate from real user queries
                session_name_events = []
                if not is_session_primer:  # Skip session primers
                    current_session_check = await session_manager.get_session(user_uuid, session_id)
                    # Generate if name not set (works for turn 1 or turn 2+ after primer)
                    if current_session_check and (not current_session_check.get("name") or current_session_check.get("name") == "New Chat"):
                        try:
                            app_logger.info(f"[Genie] Generating session name from first real user query (collecting events for history)")

                            from trusted_data_agent.agent.session_name_generator import generate_session_name_with_events

                            # Create a separate LangChain instance with thinking disabled for session name generation
                            # This ensures we get clean titles (3-5 tokens) instead of thinking blocks (100+ tokens)
                            session_name_llm = create_langchain_llm(
                                llm_config_id,
                                user_uuid,
                                temperature=0.7,
                                disable_thinking=True  # Disable extended thinking for session names
                            )

                            async for event_dict, event_type, in_tok, out_tok in generate_session_name_with_events(
                                user_query=user_input,
                                session_id=session_id,
                                llm_interface="langchain",
                                llm_instance=session_name_llm,  # Use dedicated instance with thinking disabled
                                emit_events=False  # Don't emit yet, we'll emit after saving to history
                            ):
                                if event_dict is None:
                                    # Final yield: session name
                                    session_name = event_type
                                    session_name_input_tokens = in_tok
                                    session_name_output_tokens = out_tok
                                else:
                                    # Collect event for history (store complete event_data)
                                    session_name_events.append({
                                        "type": event_type,
                                        "payload": event_dict  # Full event data, not just details
                                    })

                        except Exception as name_error:
                            app_logger.warning(f"Failed to generate genie session name: {name_error}")
                            session_name = None
                            session_name_input_tokens = 0
                            session_name_output_tokens = 0
                    else:
                        session_name = None
                        session_name_input_tokens = 0
                        session_name_output_tokens = 0
                else:
                    # Session primer - skip name generation
                    if is_session_primer:
                        app_logger.debug(f"[Genie] ⏭️  Skipping session name generation for session primer: {user_input[:50]}...")
                    session_name = None
                    session_name_input_tokens = 0
                    session_name_output_tokens = 0

                # Combine coordinator events with session name events
                combined_genie_events = result.get('genie_events', []) + session_name_events

                # --- Expert (slave) rollup for evaluation (Agent Ops & Eval §12a) ---
                # The coordinator turn is the evaluable unit. Its *quality verdicts*
                # stay isolated to coordination dimensions, but the experts' OUTCOME
                # (success/failure) and RESOURCE cost roll up onto this turn so the
                # Coordinate scorers can grade routing/expert_success and the cost KPI
                # reflects the true cost of the delegated turn. Consumption is NOT
                # re-charged here — each expert already billed its own child session;
                # this is attribution-only (kept in `genie_rollup`, separate from the
                # coordinator-only `turn_*_tokens`/`turn_cost` that feed consumption).
                # S2: the coordinator's cost is the Σ of its cache-priced calls (recorded per
                # call by the GenieCoordinator); the single-model re-price is the fallback only
                # for a coordinator that recorded nothing (never $0 over spent tokens).
                _coord_cost = float(getattr(coordinator, "turn_cost_usd", 0.0) or 0.0)
                if not _coord_cost:
                    try:
                        from trusted_data_agent.core.cost_manager import get_cost_manager
                        _coord_cost = get_cost_manager().calculate_cost(
                            provider=provider or "Unknown", model=model or "Unknown",
                            input_tokens=input_tokens, output_tokens=output_tokens)
                    except Exception:
                        pass
                _slaves, _s_in, _s_out, _s_cost = await _summarize_slaves(
                    combined_genie_events, user_uuid, session_manager)
                genie_rollup = {
                    "coordinator_input_tokens": input_tokens,
                    "coordinator_output_tokens": output_tokens,
                    "coordinator_cost_usd": round(_coord_cost, 6),
                    "expert_input_tokens": _s_in,
                    "expert_output_tokens": _s_out,
                    "expert_cost_usd": round(_s_cost, 6),
                    "expert_count": len(_slaves),
                    # Inclusive totals (coordinator + all experts) — the eval cost/token
                    # attribution for this turn.
                    "input_tokens": input_tokens + _s_in,
                    "output_tokens": output_tokens + _s_out,
                    "cost_usd": round(_coord_cost + _s_cost, 6),
                }

                # --- Tier 1.1 grounding gate (charter #14: ALL engines incl. Coordinate/genie) ---
                # The same engine-agnostic flow the other four engines use, run on the
                # coordinator's synthesized answer: withhold/annotate per the coordinator
                # profile's groundingConfig, emit live, and stamp the verdict for reload/audit.
                # Abstains cleanly (no event, answer untouched) when the coordinator has no KG
                # scope. Fails open — a gate error never breaks the genie turn.
                _grounding_event = None
                try:
                    from trusted_data_agent.agent.grounding import runtime as _grounding_runtime
                    from trusted_data_agent.agent import outcome_loop as _oloop

                    async def _genie_grade(_text, regrade=False):
                        return await _grounding_runtime.run_grounding(
                            _text,
                            kg_enrichment_event=result.get('kg_enrichment_event'),
                            profile=genie_profile,
                            profile_class="genie",
                            trace_extra={"conversation_agent_events": combined_genie_events},
                            user_uuid=user_uuid, session_id=session_id, turn_id=turn_number,
                            query=user_input, event_handler=event_handler,
                            skip_judge=regrade, park=not regrade,
                            attached_texts=((doc_context,) if doc_context else ()))

                    async def _genie_outcome_event(_ev):
                        # S7 review: the coordinator lane emits the revision STEP too (the other
                        # four engines do), so a genie revision is visible live and on reload
                        try:
                            combined_genie_events.append(dict(_ev))
                        except Exception:  # noqa: BLE001
                            pass
                        await event_handler({**_ev, "session_id": session_id}, "outcome_revision")

                    async def _genie_revise(_prompt):
                        # ONE call on the coordinator's own model; priced through the coordinator's
                        # accounting so the turn total includes it
                        _r = await coordinator.revise_text(_prompt)
                        return _r

                    _gated_text, _grounding_event = await _genie_grade(coordinator_response or "")
                    # S7 — the Outcome Loop on the coordinator's synthesized answer (all engines)
                    _gated_text, _grounding_event = await _oloop.run_loop(
                        first_text=_gated_text, first_event=_grounding_event, profile=genie_profile,
                        raw_first=coordinator_response or "", question=user_input,
                        grade=_genie_grade, revise=_genie_revise, user_uuid=user_uuid, session_id=session_id,
                        on_event=_genie_outcome_event)
                    if (_grounding_event or {}).get("decision"):
                        coordinator_response = _gated_text
                        from trusted_data_agent.agent.grounding import adapter as _grounding_adapter
                        if _grounding_event.get("outcome_delivered") == "revision":
                            coordinator_html = _gated_text   # S7 review: the delivered text IS the answer now
                        coordinator_html = _grounding_adapter.apply_to_html(coordinator_html, _grounding_event)
                        await event_handler(
                            {"step": "Grounding Gate", "type": "grounding_gate",
                             "details": _grounding_event, "session_id": session_id},
                            "grounding_gate")
                except Exception as _g_err:
                    app_logger.debug(f"[Genie] grounding gate skipped: {_g_err}")

                # --- Tier 1.3 outbound guardrail (charter #14: all engines incl. Coordinate/genie) ---
                # PII redaction + toxicity block on the coordinator's synthesized answer, run
                # before EPC sealing so the redacted/withheld text flows into the provenance
                # chain too. Deterministic, 0-token. Default observe → unchanged (#15). Fails open.
                _guardrail_event = None
                try:
                    from trusted_data_agent.agent.guardrails import runtime as _guardrail_runtime
                    _g_text, _guardrail_event = _guardrail_runtime.scan_outbound(
                        coordinator_response or "", profile=genie_profile, profile_class="genie",
                        user_uuid=user_uuid, session_id=session_id,
                        turn_id=len(workflow_history or []) + 1)
                    if (_guardrail_event or {}).get("acted"):
                        coordinator_response = _g_text
                        from trusted_data_agent.agent.guardrails import adapter as _guardrail_adapter
                        coordinator_html = _guardrail_adapter.apply_event_to_html(
                            coordinator_html, coordinator_response, _guardrail_event)
                        await event_handler(
                            {"step": "Guardrail", "type": "guardrail",
                             "details": _guardrail_event, "session_id": session_id},
                            "guardrail")
                except Exception as _g_err:
                    app_logger.debug(f"[Genie] guardrail skipped: {_g_err}")

                # F-001 — cited-but-unread. The coordinator binds component tools
                # directly, so it can carry a progressive skill and cite a section it
                # never opened; its experts are covered by their own engines. This is
                # genie's ONLY honesty branch — the SQL and RAG branches do not apply to
                # a synthesis turn, and inventing a seam for them here would report a
                # coordination turn as though a data gate had cleared it. Abstains
                # byte-identically without a progressive library (#15).
                _citation_event = None
                try:
                    from trusted_data_agent.agent.honesty import runtime as _cit_runtime
                    from components.builtin.skill_library.handler import (
                        known_anchors_for_profile as _known_anchors,
                        closed_vocabulary_for_profile as _known_vocab)
                    _anchors = _known_anchors(genie_profile or {})
                    _vocab, _valid_ids, _vocab_label = _known_vocab(genie_profile or {})
                    if _anchors or _vocab:
                        _c_text, _citation_event = _cit_runtime.run_honesty_citation(
                            coordinator_response or "", [],
                            known_anchors=_anchors,
                            events=list(getattr(coordinator, "collected_events", []) or []),
                            vocabulary=_vocab, valid_identifiers=_valid_ids,
                            vocabulary_label=_vocab_label,
                            profile=genie_profile, profile_class="genie",
                            user_uuid=user_uuid, session_id=session_id,
                            turn_id=len(workflow_history or []) + 1)
                        if (_citation_event or {}).get("disclosure_appended"):
                            coordinator_response = _c_text
                            from trusted_data_agent.agent.honesty import (
                                adapter as _cit_adapter)
                            coordinator_html = _cit_adapter.apply_to_html(
                                coordinator_html, _citation_event, coordinator_response)
                        if (_citation_event or {}).get("acted"):
                            await event_handler(
                                {"step": "Citation Check", "type": "honesty",
                                 "details": _citation_event, "session_id": session_id},
                                "honesty")
                except Exception as _c_err:
                    app_logger.debug(f"[Genie] citation check skipped: {_c_err}")

                # PROP-008 — a question whose subject is not in the coordinator's model: the
                # platform's note + act, from the coordinator's OWN enrichment (the same pure
                # delivery the four other engines reach through executor._apply_model_gap),
                # after the gates like theirs (a grounding revision replaces the html).
                try:
                    from trusted_data_agent.agent import model_gap as _model_gap
                    coordinator_response, coordinator_html, _ = _model_gap.apply(
                        coordinator_response, coordinator_html, result.get('kg_enrichment_event'))
                except Exception as _mg_err:  # noqa: BLE001
                    app_logger.debug(f"[Genie] model-gap note skipped: {_mg_err}")

                # The answer is saved to the session history HERE — after every gate that may
                # change it (grounding withhold/annotate/revision, outbound guardrail redaction,
                # citation disclosure, the model-gap note). Saved before them, a reload showed —
                # and the next turn's context (chat_object) read — the text the gates had
                # withheld or redacted, while only the live stream carried the gated answer.
                # The user message was already saved by the caller; component HTML (chart,
                # code, …) rides with the answer.
                genie_html_content = coordinator_html + (genie_component_html or "")
                if genie_html_content == coordinator_response:
                    genie_html_content = None  # No separate HTML needed when content is identical
                await session_manager.add_message_to_histories(
                    user_uuid=user_uuid,
                    session_id=session_id,
                    role='assistant',
                    content=coordinator_response,
                    html_content=genie_html_content,
                    profile_tag=profile_tag,
                    is_session_primer=is_session_primer
                )

                # Coordinator-own figures — OBSERVE (CONTROL-genie-G4 rung 2). Figures in the
                # DELIVERED answer that nothing the coordinator received supports (the question,
                # the experts' answers, the components' results). Recorded on the turn for
                # audit and the trust campaign; the answer is untouched. The same rule the
                # campaign scorer reads (`coordinator_figures.assess_turn`). Fails open.
                _coord_figures_event = None
                try:
                    from trusted_data_agent.agent.grounding import coordinator_figures as _cfig
                    _coord_figures_event = _cfig.assess_turn({
                        "profile_type": "genie",
                        "final_summary_text": coordinator_response or "",
                        "user_query": user_input,
                        "genie_events": combined_genie_events,
                    })
                    if _coord_figures_event.get("fires"):
                        app_logger.info(
                            f"[Genie] coordinator-own figures (observe): "
                            f"{_coord_figures_event.get('unsourced')} "
                            f"disclosed={_coord_figures_event.get('disclosed')} turn {turn_number}")
                except Exception as _cf_err:  # noqa: BLE001
                    app_logger.debug(f"[Genie] coordinator-figures reading skipped: {_cf_err}")

                # SIGNED's per-turn fact + the bill of materials (PROP-009), resolved ONCE
                # and BEFORE the chain is sealed so the BOM step lands in it. Genie never
                # passes through executor.run() (it is dispatched directly), so this is the
                # coordinator's own seam. Defensive: the trust suite is removable, and an
                # absent stamp reads as an honest "not recorded".
                _integrity_event = None
                try:
                    from trusted_data_agent.core.trust import evidence as _evidence
                    from trusted_data_agent.core.trust.composition import turn_evidence
                    _evidence.set_turn_bom(None)
                    _integrity_event, _genie_bom = turn_evidence(
                        genie_profile.get("id"), user_uuid, profile=genie_profile)
                    if _genie_bom:
                        _evidence.set_turn_bom(_genie_bom)
                except Exception as _ti_err:  # noqa: BLE001
                    app_logger.debug(f"[Genie] integrity stamp skipped: {_ti_err}")

                # --- EPC: Seal provenance chain for genie ---
                _provenance_data = None
                try:
                    if _genie_provenance and not _genie_provenance._sealed:
                        _genie_provenance.add_step("coordinator_synthesis", coordinator_response[:4096] if coordinator_response else "", f"Genie synthesis complete")
                        _genie_provenance.add_step("turn_complete", coordinator_response or "", f"genie turn {turn_number} complete")
                        _provenance_data = _genie_provenance.finalize()
                except Exception as _epc_err:
                    app_logger.debug(f"EPC genie finalize: {_epc_err}")

                turn_data = {
                    'turn': turn_number,
                    'user_query': user_input,
                    'genie_coordination': True,
                    'tools_used': result.get('tools_used', []),
                    'slave_sessions': result.get('slave_sessions', {}),
                    'genie_events': combined_genie_events,  # Include session name events
                    'kg_enrichment_event': result.get('kg_enrichment_event'),  # KG enrichment for persistence
                    'grounding': _grounding_event,  # Tier 1.1 grounding gate verdict (reload/audit, #17)
                    'guardrail': _guardrail_event,  # Tier 1.3 outbound guardrail verdict (reload/audit, #17)
                    'guardrail_inbound': _genie_inbound_event,  # Tier 1.3 inbound (#17)
                    'integrity': _integrity_event,  # SIGNED at answer time — sealed, never re-derived (reload/audit)
                    'honesty_citation': _citation_event,  # F-001 cited-but-unread (reload/audit, #17)
                    'coordinator_figures': _coord_figures_event,  # CONTROL-genie-G4 rung 2 — observe only
                    'context_window_snapshot_event': genie_cw_snapshot_event,  # CW snapshot for reload
                    'skills_applied': skill_result.to_applied_list() if skill_result and skill_result.has_content else [],
                    'success': success,
                    'final_response': coordinator_response[:500] if coordinator_response else '',
                    # Full answer text for eval/answer_present + uniform read across engines
                    # (RunRecord.final_answer_text reads final_summary_text first).
                    'final_summary_text': coordinator_response or '',
                    # Per-expert outcome+resource summary (rolled-up signal, isolated verdicts).
                    'slaves_invoked': _slaves,
                    'genie_rollup': genie_rollup,
                    'status': 'success' if success else 'failed',
                    'provider': provider,
                    'model': model,
                    'profile_tag': profile_tag,
                    'profile_type': 'genie',  # Mark as genie for session gallery status detection
                    'duration_ms': max(0, int((time.time() - _turn_start_time) * 1000)),  # Turn wall-clock latency (eval KPI)
                    # Token tracking (consistent with other execution paths).
                    # turn_* + turn_cost are COORDINATOR-ONLY (feed consumption/billing,
                    # no double-count with the experts' own child-session turns); the
                    # inclusive total lives in genie_rollup for eval attribution.
                    'turn_input_tokens': input_tokens,  # Cumulative turn total
                    'turn_output_tokens': output_tokens,  # Cumulative turn total
                    'turn_cost': round(_coord_cost, 6),
                    'session_input_tokens': session_input_tokens,  # Session totals at time of this turn
                    'session_output_tokens': session_output_tokens  # Session totals at time of this turn
                }
                if _provenance_data:
                    turn_data.update(_provenance_data)

                await session_manager.update_last_turn_data(
                    user_uuid=user_uuid,
                    session_id=session_id,
                    turn_data=turn_data
                )

                # Update models used
                from trusted_data_agent.agent import resilience_envelope as _renv
                _srv_prov, _srv_mod = _renv.served_model(provider, model)   # S4: the model that SERVED
                await session_manager.update_models_used(
                    user_uuid=user_uuid,
                    session_id=session_id,
                    provider=_srv_prov,
                    model=_srv_mod,
                    profile_tag=profile_tag
                )

            except Exception as log_error:
                app_logger.error(f"Failed to log Genie session data: {log_error}")

            # Send final answer event with proper fields for frontend and extensions
            # Include component HTML (chart divs, etc.) in the visual response but keep clean text for extensions
            genie_visual_response = (coordinator_html + genie_component_html) if genie_component_html else coordinator_html
            final_payload = {
                "final_answer": genie_visual_response,  # Required by eventHandlers.js (includes component divs)
                "final_answer_text": coordinator_response,  # Plain text for extensions (ExtensionContext.answer_text)
                "html_response": genie_visual_response,
                "raw_response": coordinator_response,
                "turn_id": turn_number,  # Required for turn numbering in UI
                "session_id": session_id,  # Include session_id for filtering when switching sessions
                "profile_tag": profile_tag,
                "profile_type": "genie",
                "provider": provider,
                "model": model,
                "turn_input_tokens": input_tokens,
                "turn_output_tokens": output_tokens,
                "total_input_tokens": session_input_tokens,
                "total_output_tokens": session_output_tokens,
                "execution_trace": result.get('genie_events', []),
                "tools_used": result.get('tools_used', []),
                "genie_coordination": True,
                "slave_sessions_used": result.get('slave_sessions', {}),
                "genie_events": result.get('genie_events', []),  # Full event list for GenieContext
                "kg_enrichment_event": result.get('kg_enrichment_event'),  # KG enrichment for persistence
                # S1.1 — the turn's duration on the SAME basis the turn persists (one clock, two moments)
                "duration_ms": max(0, int((time.time() - _turn_start_time) * 1000)),
                # S1.1 — the checks that ran, as the reload will read them (one verdict, two moments)
                "checks": _answer_checks.checks_summary(locals().get('_grounding_event'), locals().get('_guardrail_event')),
            }
            if answer_stream is not None:
                # S1: every provisional delta lands BEFORE the authoritative answer
                try:
                    await answer_stream.finish()
                    final_payload["answer_stream"] = answer_stream.summary()
                except Exception:  # noqa: BLE001
                    pass
            await event_handler(final_payload, "final_answer")

            # Emit session name events AFTER final answer (events already collected and saved to history)
            # Note: Don't gate on turn_number==1 — when a session primer occupies turn 1,
            # the first real query arrives on turn 2+ and still needs name emission.
            # The generation guard ensures we only generate when name is unset or "New Chat".
            if session_name_events:
                # Emit the collected session name events via SSE
                for event in session_name_events:
                    await event_handler(event['payload'], event['type'])

                # Persist session name tokens and emit token_update so Last Stmt KPI updates
                if session_name_input_tokens > 0 or session_name_output_tokens > 0:
                    await session_manager.update_token_count(
                        user_uuid, session_id,
                        session_name_input_tokens, session_name_output_tokens
                    )
                    _updated_session = await session_manager.get_session(user_uuid, session_id)
                    if _updated_session:
                        _sess_in = _updated_session.get("input_tokens", 0)
                        _sess_out = _updated_session.get("output_tokens", 0)
                        _name_cost = 0
                        try:
                            from trusted_data_agent.core.cost_manager import get_cost_manager
                            _name_cost = get_cost_manager().calculate_cost(
                                provider=provider or "Unknown",
                                model=model or "Unknown",
                                input_tokens=session_name_input_tokens,
                                output_tokens=session_name_output_tokens
                            )
                        except Exception:
                            pass
                        await event_handler({
                            "statement_input": session_name_input_tokens,
                            "statement_output": session_name_output_tokens,
                            "turn_input": input_tokens + session_name_input_tokens,
                            "turn_output": output_tokens + session_name_output_tokens,
                            "total_input": _sess_in,
                            "total_output": _sess_out,
                            "call_id": "genie_session_name",
                            "cost_usd": _name_cost
                        }, "token_update")

                # Update session name in database
                if session_name and session_name != "New Chat":
                    await session_manager.update_session_name(user_uuid, session_id, session_name)
                    await event_handler({
                        "session_id": session_id,
                        "newName": session_name
                    }, "session_name_update")

                    app_logger.info(f"[Genie] Session name updated to '{session_name}' "
                                  f"({session_name_input_tokens} in / {session_name_output_tokens} out)")

            # Send session_model_update so the master session bubbles to the top of the history panel
            # (all other profile types emit this; Genie was the only one missing it)
            _genie_session_data = await session_manager.get_session(user_uuid, session_id)
            if _genie_session_data:
                await event_handler({
                    "session_id": session_id,
                    "models_used": _genie_session_data.get("models_used", []),
                    "profile_tags_used": _genie_session_data.get("profile_tags_used", []),
                    "last_updated": _genie_session_data.get("last_updated"),
                    "provider": provider,
                    "model": model,
                    "name": _genie_session_data.get("name", "New Chat"),
                }, "session_model_update")

            return final_payload

        except Exception as e:
            app_logger.error(f"Genie coordination error for user {user_uuid}, session {session_id}: {e}", exc_info=True)
            await event_handler({
                "error": f"Genie coordination failed: {str(e)}"
            }, "error")
            return None
