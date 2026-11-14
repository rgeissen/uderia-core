# src/trusted_data_agent/agent/execution_service.py
import logging
import json
import re
import asyncio
import uuid
from datetime import datetime, timezone

from trusted_data_agent.agent.executor import PlanExecutor
from trusted_data_agent.agent.session_name_generator import generate_session_name_with_events
from trusted_data_agent.core.config import APP_STATE, APP_CONFIG
from trusted_data_agent.core import session_manager
from trusted_data_agent.extensions.runner import ExtensionRunner, serialize_extension_results
from trusted_data_agent.extensions.manager import get_extension_manager
from trusted_data_agent.extensions.models import ExtensionContext
from trusted_data_agent.extensions.db import get_user_activated_extensions

app_logger = logging.getLogger("quart.app")

# Online-eval background tasks. The hook is fire-and-forget so it NEVER blocks turn
# completion (the answer is already delivered; scoring is pure observability). We keep
# a strong reference per task — otherwise the event loop may GC a bare create_task —
# and discard it on completion. Each coroutine bounds itself internally (timeouts), so
# a stalled judge provider can neither hang a turn nor leak a coroutine forever.
_ONLINE_EVAL_TASKS: set = set()


def _dispatch_online_eval(**kwargs) -> None:
    """Fire-and-forget the online-eval hook without blocking the turn. Fully defensive:
    a missing loop or a scheduling error must never affect turn completion."""
    try:
        from trusted_data_agent.eval.online import evaluate_turn_online
        task = asyncio.ensure_future(evaluate_turn_online(**kwargs))
        _ONLINE_EVAL_TASKS.add(task)
        task.add_done_callback(_ONLINE_EVAL_TASKS.discard)
    except Exception as e:  # noqa: BLE001 — observability must never break a turn
        app_logger.debug(f"online-eval dispatch skipped: {e}")


def _dispatch_blocked_turn_span(**kwargs) -> None:
    """Fire-and-forget the GATE-ONLY span for a refused turn (I1). Same contract and
    task-set as `_dispatch_online_eval` — a refused turn is not scored, but the gate's
    outcome must still reach the span population or the guard looks idle (#50)."""
    try:
        from trusted_data_agent.eval.online import record_blocked_turn
        task = asyncio.ensure_future(record_blocked_turn(**kwargs))
        _ONLINE_EVAL_TASKS.add(task)
        task.add_done_callback(_ONLINE_EVAL_TASKS.discard)
    except Exception as e:  # noqa: BLE001 — observability must never break a turn
        app_logger.debug(f"blocked-turn span dispatch skipped: {e}")


def _format_sse(data: dict, event_type: str = None) -> str:
    """Format data as SSE event string."""
    lines = []
    if event_type:
        lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data)}")
    lines.append("")
    return "\n".join(lines)

async def _resolve_turn_cost(user_uuid: str, session_id: str, turn_id) -> float:
    """The authoritative persisted cost of a just-completed turn (for the agent-authority
    per-run budget check). Matches by ``turn`` id, falls back to the latest turn. Returns 0.0
    if it cannot be resolved — never raises."""
    try:
        session = await session_manager.get_session(user_uuid, session_id)
        wf = (session or {}).get("last_turn_data", {}).get("workflow_history", []) \
            if isinstance(session, dict) else []
        if not isinstance(wf, list) or not wf:
            return 0.0
        turn = None
        if turn_id is not None:
            turn = next((t for t in wf if isinstance(t, dict) and t.get("turn") == turn_id), None)
        if turn is None:
            turn = wf[-1] if isinstance(wf[-1], dict) else None
        return float((turn or {}).get("turn_cost", 0.0) or 0.0)
    except Exception:
        return 0.0


def _parse_sse_event(event_str: str) -> tuple[dict, str]:
    """
    Parses a raw SSE event string into its data and event type.
    """
    data = {}
    event_type = None
    for line in event_str.strip().split('\n'):
        if line.startswith('data:'):
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                app_logger.warning(f"Could not decode event JSON: {line}")
                data = {"raw_content": line[5:].strip()}
        elif line.startswith('event:'):
            event_type = line[6:].strip()
    return data, event_type


async def _run_extensions(
    extension_specs: list,
    final_payload: dict,
    user_input: str,
    session_id: str,
    user_uuid: str,
    event_handler,
    llm_config_id: str = None,
) -> tuple:
    """
    Execute post-processing extensions after the LLM answer is complete.

    Builds an ExtensionContext from the final payload and runs extensions
    serially via ExtensionRunner. Only activated extensions are executed;
    activation default_param is used when query doesn't provide one.

    Returns (serialized_results, collected_events) tuple, or (None, []).
    """
    try:
        manager = get_extension_manager()
        runner = ExtensionRunner(manager)

        # Look up user's activated extensions keyed by activation_name
        activated = get_user_activated_extensions(user_uuid)
        activated_lookup = {a["activation_name"]: a for a in activated}

        # Filter to activated-only, resolve extension_id, merge default params
        resolved_specs = []
        for spec in extension_specs:
            name = spec.get("name", "")  # activation_name from frontend
            activation = activated_lookup.get(name)

            if activation is None:
                app_logger.warning(
                    f"Extension '{name}' not activated for user {user_uuid} — skipping"
                )
                continue

            # Query param overrides activation default_param
            param = spec.get("param") or activation.get("default_param")
            resolved_specs.append({
                "name": name,                                # activation_name (for result keys)
                "extension_id": activation["extension_id"],  # actual extension to execute
                "param": param,
            })

        if not resolved_specs:
            app_logger.info("No activated extensions to run after filtering")
            return None, []

        # Build rich context from the final answer payload
        context = ExtensionContext(
            answer_text=final_payload.get("final_answer_text") or final_payload.get("final_summary_text") or "",
            answer_html=final_payload.get("final_answer") or final_payload.get("html_response") or "",
            original_query=user_input,
            clean_query=user_input,  # Already stripped by frontend
            session_id=session_id,
            turn_id=final_payload.get("turn_id", 0),
            task_id=final_payload.get("task_id"),
            profile_tag=final_payload.get("profile_tag"),
            profile_type=final_payload.get("profile_type", "tool_enabled"),
            provider=final_payload.get("provider"),
            model=final_payload.get("model"),
            turn_input_tokens=final_payload.get("turn_input_tokens", 0),
            turn_output_tokens=final_payload.get("turn_output_tokens", 0),
            total_input_tokens=final_payload.get("total_input_tokens", 0),
            total_output_tokens=final_payload.get("total_output_tokens", 0),
            execution_trace=final_payload.get("execution_trace", []),
            tools_used=final_payload.get("tools_used", []),
            collected_data=final_payload.get("collected_data", []),
            # LLM config for LLMExtension support
            user_uuid=user_uuid,
            llm_config_id=llm_config_id,
        )

        # Build GenieContext for genie coordinator profiles
        if final_payload.get("genie_coordination"):
            from trusted_data_agent.extensions.models import GenieContext, GenieChildResult

            genie_events = final_payload.get("genie_events", [])

            # Extract available_profiles from genie_coordination_start event
            available_profiles = []
            for evt in genie_events:
                if evt.get("type") == "genie_coordination_start":
                    available_profiles = evt.get("payload", {}).get("slave_profiles", [])
                    break

            # Extract child queries from genie_slave_invoked and results from genie_slave_completed
            child_queries = {}   # tag → {query, profile_id}
            child_results = {}   # tag → GenieChildResult
            for evt in genie_events:
                payload = evt.get("payload", {})
                evt_type = evt.get("type")

                if evt_type == "genie_slave_invoked":
                    child_queries[payload.get("profile_tag")] = {
                        "query": payload.get("query", ""),
                        "profile_id": payload.get("profile_id", ""),
                    }
                elif evt_type == "genie_slave_completed":
                    tag = payload.get("profile_tag", "")
                    invoked = child_queries.get(tag, {})
                    # Resolve profile_type from available_profiles
                    child_profile_type = "unknown"
                    for ap in available_profiles:
                        if ap.get("tag") == tag:
                            child_profile_type = ap.get("profile_type", "unknown")
                            break
                    child_results[tag] = GenieChildResult(
                        profile_tag=tag,
                        profile_id=invoked.get("profile_id", ""),
                        profile_type=child_profile_type,
                        session_id=payload.get("slave_session_id", ""),
                        query=invoked.get("query", ""),
                        response=payload.get("result", ""),
                        duration_ms=payload.get("duration_ms", 0),
                        success=payload.get("success", False),
                        error=payload.get("error"),
                    )

            # Extract coordination metrics
            coordination_duration_ms = 0
            coordinator_llm_steps = 0
            profiles_invoked = []
            for evt in genie_events:
                payload = evt.get("payload", {})
                if evt.get("type") == "genie_coordination_complete":
                    coordination_duration_ms = payload.get("total_duration_ms", 0)
                    profiles_invoked = payload.get("profiles_used", [])
                elif evt.get("type") == "genie_llm_step":
                    coordinator_llm_steps += 1

            context.genie = GenieContext(
                coordinator_response=final_payload.get("final_answer_text") or "",
                coordinator_profile_tag=final_payload.get("profile_tag", "GENIE"),
                available_profiles=available_profiles,
                profiles_invoked=profiles_invoked,
                child_results=child_results,
                slave_sessions=final_payload.get("slave_sessions_used", {}),
                coordination_events=genie_events,
                coordination_duration_ms=coordination_duration_ms,
                coordinator_llm_steps=coordinator_llm_steps,
            )

            app_logger.info(
                f"Built GenieContext: {len(child_results)} child results, "
                f"{len(profiles_invoked)} profiles invoked, "
                f"{coordinator_llm_steps} LLM steps"
            )

        # Wrap event_handler to collect extension lifecycle events for persistence
        collected_events = []

        async def collecting_event_handler(data, event_type):
            """Forward events AND capture extension lifecycle events."""
            await event_handler(data, event_type)
            # Capture extension-related events for session persistence
            evt_type = data.get("type") if isinstance(data, dict) else None
            if evt_type in ("extension_start", "extension_complete") or event_type == "extension_results":
                collected_events.append({
                    "type": evt_type or event_type,
                    "payload": data.get("payload", data) if isinstance(data, dict) else data,
                })

        app_logger.info(f"Running {len(resolved_specs)} extension(s): {[s.get('name') for s in resolved_specs]}")
        results = await runner.run(resolved_specs, context, collecting_event_handler)
        serialized = serialize_extension_results(results)

        # Emit combined extension_results event for frontend
        await collecting_event_handler(
            {"type": "extension_results", "payload": serialized},
            "extension_results",
        )

        return serialized, collected_events

    except Exception as e:
        app_logger.error(f"Extension execution failed: {e}", exc_info=True)
        return None, []


async def _persist_extension_results(
    ext_results: dict,
    ext_events: list,
    user_uuid: str,
    session_id: str,
    event_handler,
    prior_turn_input: int = 0,
    prior_turn_output: int = 0,
    provider: str = None,
    model: str = None,
):
    """
    Persist extension results to the session file and emit cost events if needed.

    Aggregates extension token/cost usage across all results. If any extension
    consumed tokens (LLM-calling extensions), emits a token_update event
    and updates session totals. Turn tokens include prior execution tokens
    (from the main LLM calls) plus extension tokens for accurate KPI display.
    """
    # Aggregate extension token costs
    total_ext_input = sum(r.get("extension_input_tokens", 0) for r in ext_results.values())
    total_ext_output = sum(r.get("extension_output_tokens", 0) for r in ext_results.values())

    # If extensions consumed tokens, update session totals and emit token_update
    if total_ext_input > 0 or total_ext_output > 0:
        await session_manager.update_token_count(
            user_uuid, session_id, total_ext_input, total_ext_output
        )

        current_session = await session_manager.get_session(user_uuid, session_id)
        if current_session:
            total_ext_cost = sum(r.get("extension_cost_usd", 0) for r in ext_results.values())

            # Fallback: calculate cost if extensions didn't (e.g. CostManager failed in call_llm)
            if total_ext_cost == 0 and provider and model:
                try:
                    from trusted_data_agent.core.cost_manager import get_cost_manager
                    cost_mgr = get_cost_manager()
                    total_ext_cost = cost_mgr.calculate_cost(
                        provider, model, total_ext_input, total_ext_output
                    )
                    # Backfill cost into extension results for session persistence
                    for r in ext_results.values():
                        ext_in = r.get("extension_input_tokens", 0)
                        ext_out = r.get("extension_output_tokens", 0)
                        if ext_in > 0 or ext_out > 0:
                            r["extension_cost_usd"] = cost_mgr.calculate_cost(
                                provider, model, ext_in, ext_out
                            )
                    # Backfill cost into extension_complete events for reload renderer
                    for evt in ext_events:
                        if evt.get("type") == "extension_complete":
                            payload = evt.get("payload", {})
                            ext_name = payload.get("name")
                            if ext_name and ext_name in ext_results:
                                payload["cost_usd"] = ext_results[ext_name].get("extension_cost_usd", 0)
                    app_logger.info(f"Extension cost fallback: ${total_ext_cost:.6f}")
                except Exception as e:
                    app_logger.warning(f"Extension cost fallback failed: {e}")

            await event_handler({
                "statement_input": total_ext_input,
                "statement_output": total_ext_output,
                "turn_input": prior_turn_input + total_ext_input,
                "turn_output": prior_turn_output + total_ext_output,
                "total_input": current_session.get("input_tokens", 0),
                "total_output": current_session.get("output_tokens", 0),
                "call_id": "extensions",
                "cost_usd": total_ext_cost,
            }, "token_update")

            app_logger.info(
                f"Extension token update: {total_ext_input} in / {total_ext_output} out "
                f"(turn cumulative: {prior_turn_input + total_ext_input} in / "
                f"{prior_turn_output + total_ext_output} out, "
                f"cost: ${total_ext_cost:.6f})"
            )

    # Persist to session file (post-append to already-saved turn)
    await session_manager.append_extension_results_to_turn(
        user_uuid=user_uuid,
        session_id=session_id,
        extension_results=ext_results,
        extension_events=ext_events,
        extension_input_tokens=total_ext_input,
        extension_output_tokens=total_ext_output,
    )


# --- MODIFICATION START: Add plan_to_execute and is_replay parameters ---
async def _keep_partial_answer(stream, executor, *, status: str, error_message: str, user_uuid: str,
                               session_id: str, user_input: str, profile, event_handler) -> None:
    """S1: the answer text that ARRIVED before a stop/failure is kept. Flushes the stream,
    persists a partial turn (through the executor's own partial save when there is one —
    idempotent with the engine's — or the stream's helper for the genie path) and emits a
    partial `final_answer` so the UI keeps the bubble instead of replacing it with a bare
    CANCELLED line. Nothing here may raise: a stream is never allowed to break a stop."""
    if stream is None:
        return
    try:
        await stream.finish()
        if not stream.delivered_text:
            return
        from trusted_data_agent.agent import answer_stream as _as
        if executor is not None:
            await executor._save_partial_turn_data(status=status, error_message=error_message,
                                                   error_details=None)
            if stream.turn_id is None:
                stream.turn_id = getattr(executor, "current_turn_number", None)
        else:
            await _as.persist_partial_turn(
                stream, user_uuid=user_uuid, session_id=session_id, user_input=user_input,
                profile_tag=(profile or {}).get("tag"), profile_type=(profile or {}).get("profile_type", "genie"),
                status=status, error_message=error_message)
        await event_handler(_as.partial_final_answer(stream, status=status), "final_answer")
    except Exception as exc:  # noqa: BLE001
        app_logger.warning(f"[answer_stream] partial keep failed: {exc}")


async def run_agent_execution(
    user_uuid: str,
    session_id: str,
    user_input: str,
    event_handler,
    active_prompt_name: str = None,
    prompt_arguments: dict = None,
    disabled_history: bool = False,
    source: str = "text",
    plan_to_execute: list = None, # Added optional plan
    is_replay: bool = False, # Added replay flag
    display_message: str = None, # Added optional display message for replays
    task_id: str = None, # Add task_id parameter here
    profile_override_id: str = None, # Add profile override parameter
    is_session_primer: bool = False, # Session primer flag - marks messages as initialization
    attachments: list = None,  # Document upload attachments [{file_id, filename, ...}]
    extension_specs: list = None,  # Post-processing extensions [{"name": "json", "param": null}]
    skill_specs: list = None,  # Pre-processing skills [{"name": "sql-expert", "param": "strict"}]
    canvas_context: dict = None,  # Canvas bidirectional context {title, language, content, modified}
    force_profile_type: str = None,  # Override profile type detection (e.g. "tool_enabled" for KG constructor)
    origin: str = None,  # Orchestration driver for the turn ('flow' from the flow engine); 'genie' is auto-detected
    stream_answer: bool = False,  # S1: the caller consumes `answer_delta` events live (the UI stream); REST/scheduler/flow leave it off
    turn_relation: dict = None,  # S8: {"kind": "regenerate"|"edit", "of": <turn>, "prompt"?} — a sibling or a branch head; None = an ordinary turn
    input_answer: dict = None,  # PROP-011 re-entry: {"request", "decision", "values"} from input_resume.accept — never from a client body
):
# --- MODIFICATION END ---
    """
    The central, abstracted service for running the PlanExecutor.
    """
    final_result_payload = None
    _errored_turn_id = None   # an engine that ended the turn with an `error` event and no answer
    _ir_token = None          # PROP-011: the needs-input ledger's reset token (released in the finally)
    # Agent Authority: the ambient-principal reset token + principal, initialized before the
    # try so the `finally` can always release the principal even if setup raises (M1).
    _identity_token = None
    _mcp_servers_token = None   # PROP-006 phase 2: the turn's additional MCP servers
    _mcp_labels_token = None    # ...and their display names (Live Status names the server)
    _mcp_routing_token = None   # ...and the routing table (B10)
    _authority_token = None
    _actions_token = None
    _turn_principal = None
    _configured_scope = None   # the agent's limits when they do NOT apply to this turn
    _answer_stream = None   # S1: the per-turn delta sink (None ⇒ streaming off ⇒ baseline)
    _executor = None
    _active_profile_for_stream = None
    _stream_token = None
    _cache_token = None
    _resilience_token = None
    _budget_token = None
    from trusted_data_agent.agent import turn_budget as _tb_mod
    from trusted_data_agent.agent import tool_surface as _ts_mod
    _surface_token = _ts_mod.set_turn(None)   # S6: the lanes arm the surface; released in finally
    from trusted_data_agent.agent import outcome_loop as _ol_mod
    _outcome_token = _ol_mod.set_turn(None)   # S7: the gate seam records into it; released in finally
    from trusted_data_agent.agent import turn_lineage as _tl_mod
    from trusted_data_agent.llm import image_sources as _isrc_mod
    from trusted_data_agent.core import steer as _steer_mod
    _lineage_token = None                     # S8: armed below once the session is known; released in finally
    _attachment_token = None                  # S9: the turn's attachment-source registry; released in finally
    _sf_mod, _evidence_token = None, None     # PROP-010: the attachments the turn read; released in finally
    from trusted_data_agent.agent import answer_stream as _as_mod
    from trusted_data_agent.agent import prompt_cache as _pc_mod
    from trusted_data_agent.agent import resilience_envelope as _renv_mod
    try:
        session_data = await session_manager.get_session(user_uuid, session_id)

        if not session_data:
             app_logger.error(f"Execution service: Session {session_id} not found for user {user_uuid}.")
             await event_handler({"error": f"Session '{session_id}' not found."}, "error")
             return None # Indicate failure

        # Send an event with the latest model usage
        await event_handler({
            "session_id": session_id,
            "models_used": session_data.get("models_used", []),
            "profile_tags_used": session_data.get("profile_tags_used", []),
            "last_updated": session_data.get("last_updated", session_data.get("created_at"))
        }, "session_model_update")

        # --- MODIFICATION START: Save the correct user message to history ---
        # For a replay, we save the "Replaying..." message for UI persistence.
        # For a normal query, we save the actual user input.
        message_to_save = display_message if is_replay and display_message else user_input

        # --- S8 The turn's lineage: a regenerate runs the ROOT turn's question again as a sibling
        # (no second user message; the root's attachments/extensions/skills carried unless the
        # caller supplied its own); an edit runs the new text as a branch head. Resolved and
        # validated against the session HERE — the one seam every caller reaches — so an endpoint
        # can never regenerate a primer, a displaced turn, or a turn that carries no question.
        # Off, or no relation ⇒ every line below is today's.
        if turn_relation and _tl_mod.enabled():
            _rel = _tl_mod.build_relation(session_data, turn_relation)   # ValueError → the caller's 400
            if _rel.kind == "regenerate":
                user_input = _rel.query
                message_to_save = None
                attachments = attachments or _rel.carried.get("attachments")
                extension_specs = extension_specs or _rel.carried.get("extension_specs")
                skill_specs = skill_specs or _rel.carried.get("skill_specs")
            else:
                user_input = _rel.query
                message_to_save = _rel.query
            _lineage_token = _tl_mod.set_turn(_rel)
            app_logger.info(f"turn-lineage: {_rel.kind} of turn {_rel.of} → turn {_rel.new_turn} (session {session_id})")

        # Get profile tag from profile_override_id or current default profile
        # IMPORTANT: Use default profile, not stale session profile_id, to avoid wrong tags after override expires
        profile_tag = None
        _resolved_profile_type = None
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()

            # Priority: override > current default profile (NOT stale session profile_id)
            # This ensures profile tags are always correct, even after override expires
            default_profile_id = config_manager.get_default_profile_id(user_uuid)
            profile_id_to_check = profile_override_id or default_profile_id

            if profile_id_to_check:
                profiles = config_manager.get_profiles(user_uuid)
                profile = next((p for p in profiles if p.get("id") == profile_id_to_check), None)
                if profile:
                    profile_tag = profile.get("tag")
                    _resolved_profile_type = profile.get("profile_type")
                    app_logger.debug(f"Resolved profile_tag for user message: {profile_tag} (override={bool(profile_override_id)})")
                else:
                    app_logger.warning(f"Profile {profile_id_to_check} not found when resolving user message tag")
        except Exception as e:
            app_logger.warning(f"Failed to get profile tag: {e}")

        # Orchestration provenance for the turn (root-level, server-authoritative).
        # Independent of `source` (transport), so a genie turn keeps its Rest Call
        # tag AND gains its genie role tag:
        #   - 'flow'         : passed in by the flow engine (via /query).
        #   - 'genie_parent' : the coordinator turn — runs a genie profile and
        #                      orchestrates child experts (top-level OR nested).
        #   - 'genie_child'  : an expert sub-turn the coordinator runs in a genie
        #                      child session under an ordinary profile.
        # Coordinator check wins over child so a nested coordinator (a slave that
        # is itself a genie profile) reads as a parent of its own children.
        _is_genie_slave = bool(session_data.get("genie_metadata", {}).get("is_genie_slave"))
        _genie_parent_session_id = session_data.get("genie_metadata", {}).get("parent_session_id")
        _profile_type = force_profile_type or _resolved_profile_type
        if origin:
            _effective_origin = origin
        elif _profile_type == "genie":
            _effective_origin = "genie_parent"
        elif _is_genie_slave:
            _effective_origin = "genie_child"
        elif source == "scheduler":
            # A scheduled task is a genuinely autonomous run (no human in the loop) —
            # map its source to the autonomous origin so the agent principal applies.
            _effective_origin = "scheduler"
        else:
            _effective_origin = None

        if message_to_save:
            await session_manager.add_message_to_histories(
                user_uuid,
                session_id,
                'user',
                message_to_save,
                html_content=None, # User input is plain text
                source=source,
                profile_tag=profile_tag,
                is_session_primer=is_session_primer,
                attachments=attachments,
                extension_specs=extension_specs,
                skill_specs=skill_specs,
                origin=_effective_origin
            )
            app_logger.debug(f"Added user message to session_history for {session_id}: '{message_to_save}' with profile_tag: {profile_tag}, is_session_primer: {is_session_primer}")

            # Send SSE event to update frontend with the resolved profile_tag + origin.
            # This stamps the correct profile badge AND the orchestration tag
            # (Flow/Genie) onto the live user bubble — server-authoritative, so the
            # live render matches the persisted turn exactly.
            if profile_tag or _effective_origin:
                app_logger.info(f"Sending user_message_profile_tag SSE event: profile_tag={profile_tag}, origin={_effective_origin}, is_session_primer={is_session_primer}")
                await event_handler({
                    "type": "user_message_profile_tag",
                    "payload": {
                        "profile_tag": profile_tag,
                        "origin": _effective_origin,
                        "is_session_primer": is_session_primer
                    }
                }, "notification")
        # --- MODIFICATION END ---

        previous_turn_data = session_data.get("last_turn_data", {})

        # --- GENIE PROFILE DETECTION: Route to Genie coordinator for genie profiles ---
        # Determine active profile type
        active_profile = None
        active_profile_type = "tool_enabled"  # Default

        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        # Get current default profile
        default_profile_id = config_manager.get_default_profile_id(user_uuid)

        # Profile resolution logic:
        # 1. If profile_override_id is set (explicit @TAG), use it
        # 2. Otherwise, ALWAYS use the current default profile (ignore stale session profile_id)
        profile_id_to_use = profile_override_id or default_profile_id

        if profile_id_to_use:
            profiles = config_manager.get_profiles(user_uuid)
            active_profile = next((p for p in profiles if p.get("id") == profile_id_to_use), None)
            if active_profile:
                active_profile_type = active_profile.get("profile_type", "tool_enabled")
                app_logger.debug(
                    f"Resolved profile: {active_profile.get('tag')} (type: {active_profile_type}, "
                    f"override: {bool(profile_override_id)}, default: {profile_id_to_use == default_profile_id})"
                )

        if not active_profile:
            # Fallback to tool_enabled if no profile found
            active_profile_type = "tool_enabled"
            app_logger.warning(f"No active profile found, defaulting to tool_enabled")

        # PROP-011 — the needs-input ledger for this turn (contextvar-scoped like the loop guard
        # and the principal): the agent's input policy, the origin, and — on a re-entry — the
        # person's answer, so the step that asked receives the chosen value in code. A new turn
        # that does not answer the session's pending question means the person moved on: the
        # question is superseded (the composer rule), and the record says so.
        try:
            from trusted_data_agent.agent import input_requests as _ir_mod
            from trusted_data_agent.core import input_request_store as _ir_store
            _ir_origin = _effective_origin or ("rest" if source == "rest" else "interactive")
            if not (input_answer or {}).get("request"):
                _ir_store.supersede_pending_for_session(session_id, user_uuid, by="next turn")
            _ir_token = _ir_mod.begin_turn(user_uuid=user_uuid, session_id=session_id, origin=_ir_origin,
                                           profile=active_profile, task_id=task_id, answer=input_answer,
                                           session_defaults=(session_data or {}).get("input_defaults"))
            # the fire drill (TEST server only, `TDA_NEEDS_INPUT_INJECT=1`): a marked input raises
            # its question here, in code, so every engine's handling of one can be provoked
            from trusted_data_agent.agent import needs_input_drill as _drill
            if _drill.enabled():
                await _drill.fire(user_input)
        except Exception as _ie:  # noqa: BLE001 — the ledger must never stop a turn
            app_logger.debug(f"needs-input ledger not armed: {_ie}")
            _ir_token = None

        # --- S1 The Governed Stream: ONE sink per turn, created here so every engine shares
        # it (the four executor engines through `executor.answer_stream`, genie through its
        # own argument). Armed only when the caller can consume deltas live (`stream_answer`)
        # AND the master switch is on; otherwise None and every provider takes its
        # non-streaming API — byte-identical baseline. The in-flight guard is resolved from
        # the SAME guardrail configuration the post-hoc scan uses (`stream_policy`).
        _active_profile_for_stream = active_profile
        try:
            if stream_answer and getattr(APP_CONFIG, "ANSWER_STREAMING", False):
                from trusted_data_agent.agent import answer_stream as _as
                from trusted_data_agent.agent.guardrails import runtime as _gr
                _pol = _gr.stream_policy(active_profile, user_uuid=user_uuid, session_id=session_id)
                _answer_stream = _as.AnswerStream(
                    event_handler, session_id,
                    guard=_as.StreamGuard(redact_pii=_pol["redact_pii"], block_toxicity=_pol["block_toxicity"],
                                          allowed_values=_pol["allowed_values"]),
                    flush_ms=getattr(APP_CONFIG, "ANSWER_STREAM_FLUSH_MS", 40),
                    # the coordinator's final is markdown the browser renders (the stream's own
                    # renderer); every other lane's final is the formatter's — stream in its shape
                    render_mode=None if active_profile_type == "genie" else "markdown")
        except Exception as _as_err:  # noqa: BLE001 — a stream must never break a turn
            app_logger.debug(f"[answer_stream] not armed: {_as_err}")
            _answer_stream = None

        # --- S2 The Attested Cache: ONE accumulator per turn (task-local), armed here so
        # every LLM call of every engine records into it and the persistence chokepoint
        # stamps it. The previous turn's prefix hash seeds the attested-prefix rule (a miss
        # on an unchanged prefix, inside the TTL, is a finding). Never breaks a turn.
        try:
            from trusted_data_agent.llm import cache_policy as _cpol
            _pol = _cpol.resolve(active_profile or {}, bool(getattr(APP_CONFIG, "PROMPT_CACHING", False)))
            _wh = (previous_turn_data or {}).get("workflow_history") if isinstance(previous_turn_data, dict) else None
            _prev_turn = _wh[-1] if isinstance(_wh, list) and _wh and isinstance(_wh[-1], dict) else {}
            _prev_cache = _prev_turn.get("cache") if isinstance(_prev_turn.get("cache"), dict) else {}
            _cache_token = _pc_mod.set_turn_cache(_pc_mod.TurnCache(
                enabled=_pol["enabled"], ttl=_pol["ttl"],
                previous_prefix_hash=_prev_cache.get("prefix_hash"), previous_at=_prev_cache.get("at")))
        except Exception as _pc_err:  # noqa: BLE001
            app_logger.debug(f"[cache] turn accumulator not armed: {_pc_err}")

        # --- S4 The Resilience Envelope: ONE accumulator per turn (task-local), armed here with
        # the profile's SIGNED fallback resolved ONCE, so every LLM call of every engine records
        # its recovery acts into it, the chain seals them and the persistence chokepoint stamps
        # them. Off ⇒ not armed ⇒ nothing recorded, nothing stamped. Never breaks a turn.
        try:
            if _renv_mod.enabled():
                _prim_prov, _prim_model = None, None
                try:
                    _llm_cfg_id = (active_profile or {}).get("llmConfigurationId")
                    if _llm_cfg_id:
                        _llm_cfg = next((c for c in (config_manager.get_llm_configurations(user_uuid) or []) if c.get("id") == _llm_cfg_id), None)
                        if _llm_cfg:
                            _prim_prov, _prim_model = _llm_cfg.get("provider"), _llm_cfg.get("model")
                except Exception:  # noqa: BLE001
                    pass
                _resilience_token = _renv_mod.set_turn(_renv_mod.TurnResilience(
                    user_uuid=user_uuid, session_id=session_id, primary_provider=_prim_prov, primary_model=_prim_model,
                    fallback=_renv_mod.resolve_fallback(active_profile, user_uuid)))
        except Exception as _re_err:  # noqa: BLE001
            app_logger.debug(f"[resilience] turn accumulator not armed: {_re_err}")

        # --- S5 The Visible Budget: the profile's turn budget armed for THIS turn (the switch on
        # and a budget declared); every tool boundary and every LLM call records into it, the
        # persistence chokepoint stamps it. Never breaks a turn.
        try:
            _budget_token = _tb_mod.arm(active_profile, user_uuid=user_uuid, session_id=session_id)
        except Exception as _tb_err:  # noqa: BLE001
            app_logger.debug(f"[budget] turn budget not armed: {_tb_err}")

        # --- S3 Compute with Lineage: the turn's result-set registry starts empty here, so
        # a statement number always means "the Nth data query of THIS turn". Never breaks
        # a turn; off ⇒ nothing is ever recorded into it.
        try:
            from trusted_data_agent.agent import result_sets as _result_sets
            _result_sets.begin_turn(session_id)
        except Exception:  # noqa: BLE001
            pass
        # --- S9 Grounded Multimodal: the turn's attachment-source registry (a contextvar —
        # parallel turns never mix). Filled at the executor's document seam; released in finally.
        try:
            _attachment_token = _isrc_mod.begin_turn()
        except Exception:  # noqa: BLE001
            _attachment_token = None
        # --- PROP-010: the attachments this turn read, recorded (ids, never text) so the
        # after-the-turn scores read them as evidence — through the guarded manifest, so a
        # file deleted from Files stops being evidence when it stops being readable.
        try:
            from trusted_data_agent.filestore import session_files as _sf_mod
            _evidence_token = _sf_mod.begin_turn(attachments)
        except Exception:  # noqa: BLE001
            _sf_mod, _evidence_token = None, None
        # --- S10 Steer: a new turn starts its steer record; a steer left waiting when the
        # previous turn ended (no step boundary reached it) is delivered NOW, as an operator
        # note the model reads with the question — the stored message stays the user's own.
        try:
            _steer_mod.begin_turn(session_id)
            _carried = _steer_mod.consume_at_turn_start(session_id, user_uuid)
            if _carried and user_input:
                # AFTER the question (recency): a small model followed a trailing instruction
                # and ignored the same one placed before the question
                user_input = f"{user_input}\n\n{_carried}"
        except Exception as _ste:  # noqa: BLE001
            app_logger.debug(f"[steer] turn-start delivery skipped: {_ste}")

        # Update session to reflect the profile being used (unless it's an override)
        # profile_id tracks the "base" profile, overrides are temporary
        if not profile_override_id and active_profile:
            session_profile_id = session_data.get("profile_id")
            if session_profile_id != active_profile.get("id"):
                app_logger.info(
                    f"Updating session profile from {session_profile_id} to {active_profile.get('id')} "
                    f"(default changed to @{active_profile.get('tag')})"
                )
                # BUG FIX: Reload session to get the user message that was just added
                session_data = await session_manager.get_session(user_uuid, session_id)
                session_data["profile_id"] = active_profile.get("id")
                session_data["profile_tag"] = active_profile.get("tag")
                await session_manager.save_session(user_uuid, session_id, session_data)

        # Track the last executed profile (for prompt invocations from resource panel)
        if active_profile:
            current_last_executed = session_data.get("last_executed_profile_id")
            if current_last_executed != active_profile.get("id"):
                # BUG FIX: Reload session to get the user message that was just added
                session_data = await session_manager.get_session(user_uuid, session_id)
                session_data["last_executed_profile_id"] = active_profile.get("id")
                app_logger.debug(f"Updated last_executed_profile_id to {active_profile.get('id')} (@{active_profile.get('tag')})")
                await session_manager.save_session(user_uuid, session_id, session_data)

        # --- SKILL RESOLUTION: Resolve pre-processing skills before executor/genie ---
        # Two sources: (1) profile auto-assigned skills, (2) manual per-query skills
        # Runs for ALL profile types (tool_enabled, rag_focused, llm_only, genie)
        skill_result = None
        try:
            from trusted_data_agent.skills.manager import get_skill_manager
            from trusted_data_agent.skills.models import SkillSpec, SkillResult
            from trusted_data_agent.skills.settings import is_skill_available
            skill_manager = get_skill_manager()
            resolved_specs = []
            seen_skill_ids = set()

            # Build profile-level skill lookup (enabled + active flags)
            profile_skills = {}
            if active_profile:
                for entry in active_profile.get("skillsConfig", {}).get("skills", []):
                    sid = entry.get("id", "")
                    if sid:
                        profile_skills[sid] = entry

            # --- (1) AUTO-SKILLS: explicit profile actives + component-driven companions ---
            # A builtin component's companion skill (e.g. flow -> flow-designer) auto-applies
            # when that component is enabled for the profile, unless the user owns the skill
            # entry (opt in/out). resolve_auto_skill_specs is the pure, unit-tested authority.
            from trusted_data_agent.skills.manager import resolve_auto_skill_specs
            companion_map = skill_manager.companion_skill_map()
            enabled_component_ids = set()
            if active_profile and companion_map:
                try:
                    from trusted_data_agent.components.manager import get_component_manager
                    from trusted_data_agent.agent.recommender import _component_enabled_for_profile
                    _cm = get_component_manager()
                    for _comp_id in companion_map:
                        if _component_enabled_for_profile(
                            _cm, _comp_id, active_profile, active_profile_type, user_uuid
                        ):
                            enabled_component_ids.add(_comp_id)
                except Exception as e:
                    app_logger.debug(f"[skills] companion auto-assign component check failed: {e}")
            for spec in resolve_auto_skill_specs(
                profile_skills, enabled_component_ids, companion_map, is_skill_available
            ):
                if spec.name not in seen_skill_ids:
                    resolved_specs.append(spec)
                    seen_skill_ids.add(spec.name)
                    app_logger.debug(f"Auto-skill (profile/component): {spec.name}")

            # --- (2) MANUAL SKILLS: #hashtag skills checked against profile enabled list ---
            if skill_specs:
                for spec in skill_specs:
                    skill_id = spec.get("name", "")
                    profile_entry = profile_skills.get(skill_id)

                    if not profile_entry or not profile_entry.get("enabled", False):
                        app_logger.warning(f"Skill '{skill_id}' not enabled in profile — skipping")
                        continue

                    if not is_skill_available(skill_id):
                        app_logger.warning(f"Skill '{skill_id}' is disabled by admin — skipping")
                        continue

                    param = spec.get("param") or profile_entry.get("param")

                    if skill_id in seen_skill_ids:
                        # Manual overrides auto-skill param for same skill_id
                        resolved_specs = [
                            SkillSpec(name=skill_id, param=param) if s.name == skill_id else s
                            for s in resolved_specs
                        ]
                        app_logger.debug(f"Manual skill '{skill_id}' overrides auto-skill param")
                    else:
                        resolved_specs.append(SkillSpec(name=skill_id, param=param))
                        seen_skill_ids.add(skill_id)

            # --- Resolve all skills through SkillManager ---
            if resolved_specs:
                skill_result = skill_manager.resolve_skills(resolved_specs, profile_config=active_profile)
                if skill_result and skill_result.has_content:
                    applied_list = skill_result.to_applied_list()
                    await event_handler({
                        "type": "skills_applied",
                        "payload": {
                            "skills": applied_list,
                            "total_estimated_tokens": skill_result.total_estimated_tokens,
                        }
                    }, "notification")
                    app_logger.info(
                        f"Skills applied: {[s['name'] for s in applied_list]} "
                        f"(~{skill_result.total_estimated_tokens} tokens)"
                    )
        except Exception as e:
            app_logger.error(f"Failed to resolve skills: {e}", exc_info=True)
            skill_result = None

        # Agent Authority (Tier 5 Step 4 / P3): for an AUTONOMOUS turn (genie/flow/scheduler/
        # mcp), act as the agent principal — a scoped subset of the owner — by setting the
        # ambient per-turn security context (async-task-local; read fail-closed at every tool
        # chokepoint). Interactive user turns leave it unset → baseline (no gating).
        # A fresh loop-guard ledger for this turn — contextvar-scoped like the principal
        # below, so concurrent sessions never share one.
        _loop_token = None
        try:
            from trusted_data_agent.agent import loop_guard as _loop_guard
            _loop_token = _loop_guard.begin_turn()
        except Exception:  # noqa: BLE001
            _loop_token = None
        # The per-turn tool ledger (reuse of an identical successful call, the per-tool
        # breaker) — the same scoping, for the same reason.
        _tool_ledger_token = None
        try:
            from trusted_data_agent.agent import turn_tool_ledger as _tool_ledger
            _tool_ledger_token = _tool_ledger.begin_turn()
        except Exception:  # noqa: BLE001
            _tool_ledger_token = None

        # KG theatre: arm the live stage-notice sink for this turn (contextvar, like the
        # principal below) so the enrichment pipeline can report understand/seed/expand/
        # inject the moment each completes. Fail-open: no sink ⇒ the enrichment emits
        # nothing extra and the turn is byte-identical.
        _kg_stage_token = None
        try:
            from trusted_data_agent.core import kg_stage as _kg_stage
            _kg_stage_token = _kg_stage.set_stage_sink(user_uuid, session_id)
        except Exception:  # noqa: BLE001
            _kg_stage_token = None

        # PROP-006 phase 2: arm the agent's ADDITIONAL MCP servers for this turn (alias → server
        # id, a context variable like the principal) — what `base_name` recognises and
        # `invoke_mcp_tool` routes by. A single-server agent arms nothing: every reader then sees
        # the empty default and behaves exactly as before.
        _mcp_servers_token = None
        # ...and the turn's PRIMARY server, so no reader inside this turn (the adapter's fallback,
        # a delegated prompt, an orchestrator) ever takes the PER-USER value another concurrent
        # turn of the same user has just switched (OC-F1, 2026-10-27).
        _primary_server_token = None
        try:
            from trusted_data_agent.core.tool_identity import set_turn_primary_server as _set_primary
            from trusted_data_agent.core.agent_mcp_servers import primary_of as _primary_of
            _primary_server_token = _set_primary(_primary_of(active_profile))
        except Exception:  # noqa: BLE001
            _primary_server_token = None
        try:
            from trusted_data_agent.core.agent_mcp_servers import additional_of as _additional_of
            _extra_servers = _additional_of(active_profile)
            if _extra_servers:
                from trusted_data_agent.core.tool_identity import set_turn_servers as _set_turn_servers
                _mcp_servers_token = _set_turn_servers({s.alias: s.server_id for s in _extra_servers})
        except Exception:  # noqa: BLE001
            _mcp_servers_token = None
        # ...and the servers' display names, so every MCP tool step of the turn can name the
        # server it went to (Live Status, live and on reload). Armed only beside the map above.
        _mcp_labels_token = None
        if _mcp_servers_token is not None:
            try:
                from trusted_data_agent.core.agent_mcp_servers import set_turn_source_labels as _set_labels
                from trusted_data_agent.core.config_manager import get_config_manager as _gcm
                _names = {str(sv.get("id")): str(sv.get("name") or sv.get("id"))
                          for sv in (_gcm().get_mcp_servers(user_uuid) or []) if isinstance(sv, dict) and sv.get("id")}
                from trusted_data_agent.core.agent_mcp_servers import primary_of as _primary_of
                _mcp_labels_token = _set_labels(_primary_of(active_profile), _names)
            except Exception:  # noqa: BLE001
                _mcp_labels_token = None
        # ...and the routing table (B10): every server of the agent with its own tool selection,
        # so a call naming data the called server cannot read goes to the one that can.
        _mcp_routing_token = None
        if _mcp_servers_token is not None:
            try:
                from trusted_data_agent.agent import source_routing as _srt
                from trusted_data_agent.core.agent_mcp_servers import servers_of as _servers_of
                _mcp_routing_token = _srt.set_turn(user_uuid, _servers_of(active_profile))
                if _srt.active():
                    # each server's database list — probed once per user × server (parallel,
                    # bounded, cached 30 min) so both lanes can say which server holds what
                    await _srt.ensure_lists(user_uuid, [s.server_id for s in _srt.turn_servers()])
                    # ...and each server's tables (with columns when few) — stage 3a — so the
                    # planner names real tables on the right server; the columns seed this
                    # session's column store (the describe pass and the corrections read it).
                    await _srt.ensure_catalogs(user_uuid, [s.server_id for s in _srt.turn_servers()
                                                           if not _srt.is_unavailable(s.server_id)])
                    _cat_cols = _srt.turn_catalog_columns()
                    if _cat_cols and session_id:
                        from trusted_data_agent.agent.preflight import columns as _pcols
                        _pcols.record_columns(session_id, _cat_cols, origin="dictionary")
            except Exception:  # noqa: BLE001
                pass

        _turn_actor = None
        try:
            from trusted_data_agent.core import agent_authority as _aid
            # An interactive turn normally derives no principal (the human is the
            # control). An agent may opt out of that carve-out — see
            # agent_authority.enforces_interactive — so a switch labelled "structural
            # confinement" can actually confine the mode the operator is using.
            if active_profile and (_effective_origin in _aid.AUTONOMOUS_ORIGINS
                                   or _aid.enforces_interactive(active_profile)):
                _principal = _aid.derive_principal(active_profile, user_uuid)
                _identity_token = _aid.set_current_principal(_principal)
                _turn_principal = _principal
                _turn_actor = _principal.actor()
                if _principal.scope.is_restricted:
                    app_logger.info(
                        f"agent-identity: acting as {_principal.principal_id} "
                        f"(origin={_effective_origin}, scoped)")
            else:
                _turn_actor = _aid.user_actor(user_uuid)
                # The agent's own limits, NOT in force on this interactive turn: carried on the
                # stamp so BOUNDED can say so, instead of reading as though none were configured
                # (trust campaign B3 — a `deny_writes` agent's chat turn read "acting as the user").
                if active_profile:
                    try:
                        _configured_scope = _aid.derive_principal(active_profile, user_uuid).scope
                    except Exception:  # noqa: BLE001 — a disclosure must never break a turn
                        _configured_scope = None
            # Seal the scope IN FORCE onto the ambient context BEFORE the engine runs, so
            # the single persistence chokepoint stamps it onto the turn for every engine.
            # Nothing in the stamp depends on the outcome, so building it here is safe and
            # is what makes it reach `workflow_history` at all.
            _authority_token = _aid.set_turn_authority(
                _aid.build_authority_stamp(_turn_principal, _effective_origin,
                                          configured=_configured_scope))
            # OBDA-ACT (PROP-008 §5.4): the turn's action record — every invocation of a signed
            # action this turn appends to it; the persistence chokepoint stamps it and the
            # provenance chain seals what was performed. Empty record ⇒ no stamp (baseline).
            from trusted_data_agent.core import action_perform as _act_perf
            _actions_token = _act_perf.begin_turn()
        except Exception as _ie:
            app_logger.debug(f"agent-identity: principal setup skipped: {_ie}")

        # Route Genie profiles to Genie coordinator
        if active_profile_type == "genie" and active_profile:
            app_logger.info(f"🔮 Detected Genie profile '{active_profile.get('tag')}' - routing to Genie coordinator")
            from trusted_data_agent.agent.engines.coordinate_engine import CoordinateEngine
            final_result_payload = await CoordinateEngine().execute_genie(
                user_uuid=user_uuid,
                session_id=session_id,
                user_input=user_input,
                event_handler=event_handler,
                genie_profile=active_profile,
                task_id=task_id,
                disabled_history=disabled_history,  # Pass history context flag
                is_session_primer=is_session_primer,  # Pass session primer flag
                attachments=attachments,  # Pass document upload attachments
                skill_result=skill_result,  # Pass resolved skills to genie coordinator
                answer_stream=_answer_stream,  # S1
            )
            if _answer_stream is not None and isinstance(final_result_payload, dict):
                final_result_payload.setdefault("answer_stream", _answer_stream.summary())
        # --- END GENIE PROFILE DETECTION ---
        else:
            executor = PlanExecutor(
                user_uuid=user_uuid,
                session_id=session_id,
                original_user_input=user_input,
                dependencies={'STATE': APP_STATE},
                active_prompt_name=active_prompt_name,
                prompt_arguments=prompt_arguments,
                disabled_history=disabled_history,
                previous_turn_data=previous_turn_data,
                source=source,
                plan_to_execute=plan_to_execute,
                is_replay=is_replay,
                task_id=task_id,
                profile_override_id=profile_override_id,
                event_handler=event_handler,
                is_session_primer=is_session_primer,
                attachments=attachments,
                skill_result=skill_result,
                canvas_context=canvas_context,
                force_profile_type=force_profile_type,
                answer_stream=_answer_stream,  # S1
            )
            _executor = executor
            _stream_token = _as_mod.set_current(_answer_stream) if _answer_stream is not None else None

            async for event_str in executor.run():
                event_data, event_type = _parse_sse_event(event_str)
                if event_type == "final_answer" and _answer_stream is not None:
                    # Every provisional delta lands BEFORE the authoritative answer, and
                    # the answer carries the stream's facts for the assurance surface.
                    await _answer_stream.finish()
                    if isinstance(event_data, dict):
                        event_data.setdefault("answer_stream", _answer_stream.summary())
                await event_handler(event_data, event_type)
                if event_type == "final_answer":
                    final_result_payload = event_data
                elif event_type == "error" and isinstance(event_data, dict) and event_data.get("turn_id") is not None:
                    _errored_turn_id = event_data.get("turn_id")   # C18: a failed turn is still graded

        # Agent Authority: the ambient principal is released in the `finally` (so an engine
        # exception or user-Stop cancellation can never strand a restricted principal into
        # follow-up work on this task context — M1). Stamp the actor onto the turn payload so
        # audit distinguishes agent- vs user-initiated.
        if _turn_actor and isinstance(final_result_payload, dict):
            final_result_payload.setdefault("actor", _turn_actor)

        # Agent Authority — stamp the scope that was IN FORCE on the turn (symmetry map A17).
        # Sealed at execution time, never resolved later: an agent's authorityConfig can
        # change, and re-resolving it at scoring time would grade a historical turn against
        # today's config — the precedent is the turn-sealed SIGNED stamp, which was
        # retroactively flipping historical turns until it was sealed at answer time.
        #
        # The boundary applies to AUTONOMOUS turns only, so the stamp records which it was.
        # An interactive turn is stamped `applied: False` rather than left bare: a
        # measurement must be able to tell "the boundary held" from "the boundary was never
        # in force", and a missing stamp cannot say which.
        if isinstance(final_result_payload, dict):
            try:
                from trusted_data_agent.core import agent_authority as _aid_stamp
                final_result_payload["authority"] = _aid_stamp.build_authority_stamp(
                    _turn_principal, _effective_origin, configured=_configured_scope)
            except Exception as _ae:  # a stamp must never break a turn
                app_logger.debug(f"agent-authority: scope stamp skipped: {_ae}")

        # Agent Authority — per-run budget cap (P3). Post-hoc check (the cost engine owns the
        # numbers): if this autonomous run exceeded its authorityConfig.max_cost_per_run, record
        # it on the payload + emit an audit event so the caller (scheduler, coordinator) can act
        # and the overrun is visible. No budget set → no-op → baseline.
        if (_turn_principal is not None
                and _turn_principal.scope.max_cost_per_run is not None
                and isinstance(final_result_payload, dict)):
            try:
                from trusted_data_agent.core import agent_authority as _aid
                _run_cost = await _resolve_turn_cost(
                    user_uuid, session_id, final_result_payload.get("turn_id"))
                _ok, _reason = _aid.is_run_within_budget(_turn_principal.scope, _run_cost)
                if not _ok:
                    final_result_payload["authority_budget_exceeded"] = True
                    final_result_payload["authority_budget_reason"] = _reason
                    app_logger.warning(
                        f"agent-authority: {_turn_principal.principal_id} run cost "
                        f"${_run_cost:.4f} exceeded budget — {_reason}")
                    try:
                        await event_handler({
                            "type": "notification",
                            "payload": {"type": "agent_budget_exceeded",
                                        "principal_id": _turn_principal.principal_id,
                                        "run_cost_usd": _run_cost,
                                        "reason": _reason},
                        }, "notification")
                    except Exception:
                        pass
            except Exception as _be:
                app_logger.debug(f"agent-authority budget check skipped: {_be}")

        # --- EXTENSION EXECUTION (all profile types, including genie) ---
        if extension_specs and final_result_payload:
            _prior_turn_input = 0
            _prior_turn_output = 0
            _ext_provider = None
            _ext_model = None
            try:
                _session_data = await session_manager.get_session(user_uuid, session_id)
                if _session_data:
                    _wh = _session_data.get("last_turn_data", {}).get("workflow_history", [])
                    if _wh:
                        _latest = _wh[-1]
                        _prior_turn_input = _latest.get("turn_input_tokens", 0) or 0
                        _prior_turn_output = _latest.get("turn_output_tokens", 0) or 0
                        _ext_provider = _latest.get("provider")
                        _ext_model = _latest.get("model")
            except Exception:
                pass

            ext_results, ext_events = await _run_extensions(
                extension_specs=extension_specs,
                final_payload=final_result_payload,
                user_input=user_input,
                session_id=session_id,
                user_uuid=user_uuid,
                event_handler=event_handler,
                llm_config_id=active_profile.get("llmConfigurationId") if active_profile else None,
            )
            if ext_results:
                final_result_payload["extension_results"] = ext_results
                await _persist_extension_results(
                    ext_results, ext_events, user_uuid, session_id, event_handler,
                    prior_turn_input=_prior_turn_input,
                    prior_turn_output=_prior_turn_output,
                    provider=_ext_provider,
                    model=_ext_model,
                )

        # H5: flush any deferred grounding-gate judge cost now the turn is persisted. The
        # grounding judge runs inline during answer assembly (before the turn row exists), so
        # its accounting is stashed and billed here — turn totals + the persisted judge step now
        # land correctly, keeping session totals == Σ turn totals. Drains unconditionally (a
        # turn with no grounding judge → no-op).
        try:
            from trusted_data_agent.eval.online import flush_deferred_judge_usage
            await flush_deferred_judge_usage(user_uuid, session_id, event_handler)
        except Exception as _fe:
            app_logger.debug(f"deferred judge-usage flush skipped: {_fe}")

        # Surface the orchestration origin on the returned payload so the REST
        # rest_task_complete notification can tag the live turn identically to the
        # persisted one (single server-authoritative fact, every render path reads it).
        if isinstance(final_result_payload, dict) and _effective_origin:
            final_result_payload["origin"] = _effective_origin
        # PROP-011 — a turn that stopped to ask: the question rides on the result (with its
        # resume binding — this is the owner's own turn), the question is appended to the
        # delivered text and HTML, the turn's record reads `input_required` (ONE place, not five
        # engines), and the stream carries the card. A raiser in `observe` leaves its findings
        # on the record too (`ambiguities_observed`) — what the measure reads.
        if _ir_token is not None:
            try:
                from trusted_data_agent.agent import input_requests as _ir_mod
                _ir_view = _ir_mod.finish_payload(final_result_payload)
                _ir_stamp = _ir_mod.turn_stamp()
                if _ir_stamp:
                    _ir_turn = final_result_payload.get("turn_id") if isinstance(final_result_payload, dict) else None
                    await session_manager.stamp_last_turn(
                        user_uuid, session_id, _ir_turn,
                        {**_ir_stamp, **({"status": _ir_mod.turn_status()} if _ir_view else {})},
                        append_text=_ir_mod.question_text(_ir_mod.pending()) if _ir_view else None)
                if _ir_view:
                    await event_handler({"type": "input_required",
                                         "payload": {**_ir_view, "session_id": session_id}}, "notification")
            except Exception as _ie:  # noqa: BLE001
                app_logger.warning(f"needs-input: finishing the turn's record failed: {_ie}")
        # S8 — the relation the turn ran under, so the caller (the UI stream, the REST completion
        # notice) can re-read the session's lineage instead of appending a bubble.
        if isinstance(final_result_payload, dict) and _lineage_token is not None:
            try:
                _rel_now = _tl_mod.current()
                if _rel_now is not None:
                    final_result_payload["lineage"] = _rel_now.to_dict()
            except Exception:  # noqa: BLE001
                pass

        # --- NEXT-STEP RECOMMENDER (ephemeral, token-light) ---
        # Single seam for every profile type (genie coordinator + PlanExecutor paths
        # both reach here). Skipped for nested genie *slave* sub-turns (their session
        # is never the one the user is viewing, so suggestions would be wasted).
        # Fully defensive: build_and_emit_suggestions never raises.
        # Architecture: docs/Architecture/NEXT_STEP_RECOMMENDER_ARCHITECTURE.md
        if (not _is_genie_slave
                and isinstance(final_result_payload, dict)
                and final_result_payload.get("final_answer")
                and not final_result_payload.get("guardrail_blocked")):  # Tier 1.3: no follow-ups / no LLM on a refused turn
            from trusted_data_agent.agent.recommender import build_and_emit_suggestions
            # S5 — the turn's budget state rides the payload so the dock can offer Continue
            try:
                _bstamp = _tb_mod.stamp_for_turn()
                if _bstamp and isinstance(final_result_payload, dict):
                    final_result_payload.setdefault("budget", _bstamp)
            except Exception:  # noqa: BLE001
                pass
            await build_and_emit_suggestions(
                user_uuid=user_uuid,
                session_id=session_id,
                user_input=user_input,
                profile=active_profile,
                final_payload=final_result_payload,
                turn_id=final_result_payload.get("turn_id"),
                event_handler=event_handler,
            )
        # --- NEXT-STEP RECOMMENDER END ---

        # --- ONLINE EVALUATION (WS3 W3.4) ---
        # Tail-sampled, deterministic-only quality scoring of the live turn.
        # Same seam, same defensive contract as the recommender: never blocks or
        # mutates the turn. Off by default (APP_CONFIG.ONLINE_EVAL_ENABLED).
        #
        # Genie EXPERT (slave) sub-turns ARE scored (unlike the recommender, which is
        # skipped above): deterministic scoring is free and the expert's own quality
        # (groundedness, retrieval relevance, …) is signal the coordinator turn cannot
        # capture. The span is tagged `agent.invocation='expert'` so it's distinguished
        # in the roster, and self-cost accounting means no double-count with the
        # coordinator's delegated-cost annotation.
        # Architecture: docs/Strategy/WS3_AGENT_EVAL_OBSERVABILITY_PLAN.md
        if (isinstance(final_result_payload, dict)
                and final_result_payload.get("final_answer")
                and not final_result_payload.get("guardrail_blocked")):  # Tier 1.3: skip scoring a refused turn
            # Backgrounded — NEVER awaited in the turn's critical path. The answer is
            # already delivered; scoring (incl. the token-costing judge panel) runs
            # off-path and self-bounds with timeouts, so a stalled provider can no
            # longer freeze turn completion (the inline await did exactly that).
            _dispatch_online_eval(
                user_uuid=user_uuid,
                session_id=session_id,
                profile=active_profile,
                turn_id=final_result_payload.get("turn_id"),
                event_handler=event_handler,
                invocation=("expert" if _is_genie_slave else "direct"),
                parent_session_id=(_genie_parent_session_id if _is_genie_slave else None),
            )
        elif final_result_payload is None and _errored_turn_id is not None:
            # C18: an engine that FAILED the turn (Optimize's unrecoverable error) delivers no
            # `final_answer`, so the turn was never scored and JUDGED read nothing on a turn whose
            # provider never answered. The saved turn carries `status: error`; the deterministic
            # layer grades it (clean_termination fails) — judges are skipped for a failed turn.
            _dispatch_online_eval(
                user_uuid=user_uuid,
                session_id=session_id,
                profile=active_profile,
                turn_id=_errored_turn_id,
                event_handler=event_handler,
                invocation=("expert" if _is_genie_slave else "direct"),
                parent_session_id=(_genie_parent_session_id if _is_genie_slave else None),
            )
        elif (isinstance(final_result_payload, dict)
                and final_result_payload.get("guardrail_blocked")):
            # I1 — a refused turn is never SCORED (feeding an injected prompt to a judge
            # hands the attack a second door), but "not scored" had become "never
            # happened": the turn was absent from the span population, so GUARDED read
            # "clean" on a day the guard refused an attack (#50). Emit a GATE-ONLY span —
            # the gate's outcome and nothing else, excluded from quality denominators.
            _dispatch_blocked_turn_span(
                user_uuid=user_uuid, session_id=session_id,
                turn_id=final_result_payload.get("turn_id"),
                profile=active_profile,
                invocation=("expert" if _is_genie_slave else "direct"))
        # --- ONLINE EVALUATION END ---

        # --- AGENT MEMORY (Tier 2.2 — A7) background learned-extraction ---
        # Opt-in per profile (componentConfig.memory.learn). Fire-and-forget, mirrors the
        # online-eval contract: never blocks/mutates the turn, fails safe, routes its LLM
        # call through CostManager. Skipped for genie expert sub-turns + refused turns.
        if (isinstance(final_result_payload, dict)
                and final_result_payload.get("final_answer")
                and not final_result_payload.get("guardrail_blocked")
                and not _is_genie_slave):
            try:
                from components.builtin.memory import runtime as _memory_runtime
                _memory_runtime.maybe_dispatch_extraction(
                    user_uuid=user_uuid,
                    session_id=session_id,
                    profile=active_profile,
                    profile_type=active_profile_type,
                    user_input=user_input,
                    answer_text=final_result_payload.get("final_answer_text", ""),
                    turn_id=final_result_payload.get("turn_id"),
                    event_handler=event_handler,
                )
            except Exception as _mem_e:
                app_logger.debug(f"memory extraction dispatch skipped: {_mem_e}")
        # --- AGENT MEMORY END ---

    except asyncio.CancelledError:
        # S1: a Stop mid-answer keeps what arrived — persisted as a partial turn and
        # delivered as a partial `final_answer` — before the cancellation propagates.
        await _keep_partial_answer(_answer_stream, _executor, status="cancelled",
                                   error_message="Execution stopped by user",
                                   user_uuid=user_uuid, session_id=session_id, user_input=user_input,
                                   profile=_active_profile_for_stream, event_handler=event_handler)
        raise
    except Exception as e:
        app_logger.error(f"An unhandled error occurred in the agent execution service for user {user_uuid}, session {session_id}: {e}", exc_info=True)
        await _keep_partial_answer(_answer_stream, _executor, status="error", error_message=str(e),
                                   user_uuid=user_uuid, session_id=session_id, user_input=user_input,
                                   profile=_active_profile_for_stream, event_handler=event_handler)
        await event_handler({"error": str(e)}, "error")
        raise
    finally:
        if _cache_token is not None:
            _pc_mod.reset_turn_cache(_cache_token)
        if _resilience_token is not None:
            _renv_mod.reset_turn(_resilience_token)
        if _budget_token is not None:
            _tb_mod.reset_turn(_budget_token)
        if _surface_token is not None:
            _ts_mod.reset_turn(_surface_token)
        if _outcome_token is not None:
            _ol_mod.reset_turn(_outcome_token)
        if _lineage_token is not None:
            _tl_mod.reset_turn(_lineage_token)
        _isrc_mod.end_turn(_attachment_token)
        if _sf_mod is not None:
            _sf_mod.end_turn(_evidence_token)
        if _stream_token is not None:
            _as_mod.reset_current(_stream_token)
        if _answer_stream is not None:
            try:
                await _answer_stream.close()
            except Exception:  # noqa: BLE001
                pass
        # Agent Authority (M1): always release the ambient principal — including on an engine
        # exception or asyncio.CancelledError (user Stop), which bypass `except Exception`. This
        # guarantees a restricted principal never leaks into work later scheduled on this task's
        # context. reset_current_principal swallows a double-reset, so this is safe.
        if _identity_token is not None:
            try:
                from trusted_data_agent.core import agent_authority as _aid
                _aid.reset_current_principal(_identity_token)
            except Exception:
                pass
        if _mcp_servers_token is not None:
            try:
                from trusted_data_agent.core.tool_identity import reset_turn_servers as _reset_turn_servers
                _reset_turn_servers(_mcp_servers_token)
            except Exception:  # noqa: BLE001
                pass
        if _primary_server_token is not None:
            try:
                from trusted_data_agent.core.tool_identity import reset_turn_primary_server as _reset_primary
                _reset_primary(_primary_server_token)
            except Exception:  # noqa: BLE001
                pass
        if _mcp_labels_token is not None:
            try:
                from trusted_data_agent.core.agent_mcp_servers import reset_turn_source_labels as _reset_labels
                _reset_labels(_mcp_labels_token)
            except Exception:  # noqa: BLE001
                pass
        if _mcp_routing_token is not None:
            try:
                from trusted_data_agent.agent import source_routing as _srt
                _srt.reset_turn(_mcp_routing_token)
            except Exception:  # noqa: BLE001
                pass
        if _actions_token is not None:
            try:
                from trusted_data_agent.core import action_perform as _act_perf
                _act_perf.end_turn(_actions_token)
            except Exception:  # noqa: BLE001
                pass
        # Same reasoning for the turn's authority stamp: it must not leak into work later
        # scheduled on this task's context and be persisted onto an unrelated turn.
        if _authority_token is not None:
            try:
                from trusted_data_agent.core import agent_authority as _aid
                _aid.reset_turn_authority(_authority_token)
            except Exception:
                pass
        # Same reasoning for the KG stage sink: a later turn on this task must not
        # report its stages under this turn's session.
        if _kg_stage_token is not None:
            try:
                from trusted_data_agent.core import kg_stage as _kg_stage
                _kg_stage.reset_stage_sink(_kg_stage_token)
            except Exception:
                pass
        # Same reasoning for the loop-guard ledger: a turn's call history must not leak
        # into work later scheduled on this task's context.
        if _loop_token is not None:
            try:
                from trusted_data_agent.agent import loop_guard as _loop_guard
                _loop_guard.end_turn(_loop_token)
            except Exception:
                pass
        if _tool_ledger_token is not None:
            try:
                from trusted_data_agent.agent import turn_tool_ledger as _tool_ledger
                _tool_ledger.end_turn(_tool_ledger_token)
            except Exception:
                pass
        # PROP-011: the needs-input ledger, for the same reason.
        if _ir_token is not None:
            try:
                from trusted_data_agent.agent import input_requests as _ir_mod
                _ir_mod.end_turn(_ir_token)
            except Exception:
                pass

    return final_result_payload

