# trusted_data_agent/agent/orchestrators.py
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
import re

from trusted_data_agent.mcp_adapter import adapter as mcp_adapter
from trusted_data_agent.llm import handler as llm_handler
from trusted_data_agent.core.config import AppConfig
from trusted_data_agent.core.utils import get_argument_by_canonical_name
from trusted_data_agent.agent.json_utils import robust_json_parse


app_logger = logging.getLogger("quart.app")

# Cap fan-out so a wide range ("last year" = 365 days) can't launch hundreds of simultaneous
# MCP calls (assessment §2 — unbounded gather). Matches the platform's conservative concurrency.
_MAX_ORCHESTRATOR_CONCURRENCY = 16


def _is_date_arg(key: str) -> bool:
    """True if an argument name is a DATE parameter (assessment §2 fix). The old check
    ``'date' not in k.lower()`` stripped any arg whose name merely *contained* "date" as a
    substring — ``update``, ``validate``, ``mandate`` — deleting unrelated arguments. Match the
    whole word ``date`` in the tokenised key instead, plus the canonical range params."""
    kl = (key or "").lower()
    if kl in ("date", "start_date", "end_date", "date_range"):
        return True
    return "date" in re.split(r'[^a-z0-9]+', kl)


def _resolve_temporal_phrase_deterministic(current_date_str: str, date_phrase: str):
    """
    Deterministically resolve a temporal phrase to (start_date, end_date) YYYY-MM-DD strings.
    Returns (start_str, end_str) or None if the phrase can't be resolved deterministically.
    """
    import calendar
    today = datetime.strptime(current_date_str, '%Y-%m-%d').date()
    phrase = date_phrase.lower().strip()

    # "today"
    if phrase == 'today':
        return (current_date_str, current_date_str)

    # "yesterday"
    if phrase == 'yesterday':
        d = today - timedelta(days=1)
        return (d.strftime('%Y-%m-%d'), d.strftime('%Y-%m-%d'))

    # "last/past N days"
    m = re.match(r'(?:last|past)\s+(\d+)\s+days?', phrase)
    if m:
        n = int(m.group(1))
        start = today - timedelta(days=n)
        end = today - timedelta(days=1)
        return (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))

    # "last/past N weeks"
    m = re.match(r'(?:last|past)\s+(\d+)\s+weeks?', phrase)
    if m:
        n = int(m.group(1))
        start = today - timedelta(weeks=n)
        end = today - timedelta(days=1)
        return (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))

    # "last/past N months"
    m = re.match(r'(?:last|past)\s+(\d+)\s+months?', phrase)
    if m:
        n = int(m.group(1))
        month = today.month - n
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        start = datetime(year, month, today.day if today.day <= calendar.monthrange(year, month)[1] else calendar.monthrange(year, month)[1]).date()
        end = today - timedelta(days=1)
        return (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))

    # "this week" (Monday to today)
    if phrase in ('this week', 'current week'):
        start = today - timedelta(days=today.weekday())
        return (start.strftime('%Y-%m-%d'), current_date_str)

    # "last week" (previous Monday to Sunday)
    if phrase == 'last week':
        last_monday = today - timedelta(days=today.weekday() + 7)
        last_sunday = last_monday + timedelta(days=6)
        return (last_monday.strftime('%Y-%m-%d'), last_sunday.strftime('%Y-%m-%d'))

    # "this month" (1st to today)
    if phrase in ('this month', 'current month'):
        start = today.replace(day=1)
        return (start.strftime('%Y-%m-%d'), current_date_str)

    # "last month" (1st to last day of previous month)
    if phrase == 'last month':
        first_of_this = today.replace(day=1)
        last_of_prev = first_of_this - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        return (first_of_prev.strftime('%Y-%m-%d'), last_of_prev.strftime('%Y-%m-%d'))

    # "this year" (Jan 1 to today)
    if phrase in ('this year', 'current year'):
        start = today.replace(month=1, day=1)
        return (start.strftime('%Y-%m-%d'), current_date_str)

    # "last year"
    if phrase == 'last year':
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
        return (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))

    return None  # Unrecognized — fall back to LLM


def _format_sse(data: dict, event: str = None) -> str:
    """Helper to format data for Server-Sent Events."""
    msg = f"data: {json.dumps(data)}\n"
    if event is not None:
        msg += f"event: {event}\n"
    return f"{msg}\n"

# --- MODIFICATION START: Add user_uuid ---
async def execute_date_range_orchestrator(executor, command: dict, date_param_name: str, date_phrase: str, phase: dict, tool_supports_range: bool = False):
# --- MODIFICATION END ---
    """
    Executes a tool over a calculated date range. Supports two modes:

    1. **Single-date tools** (tool_supports_range=False): Iterates the tool once
       per day in the resolved range. Used for tools with a single `date` parameter.

    2. **Range tools** (tool_supports_range=True): Resolves the temporal phrase to
       start_date and end_date, then calls the tool ONCE with both parameters bound.
       Used for tools with `start_date` + `end_date` parameters.

    It is now "plan-aware" and will use pre-calculated date lists from previous
    phases if available, bypassing redundant LLM calls.
    """
    tool_name = command.get("tool_name")
    args = command.get("arguments", {})
    date_list = []
    # --- MODIFICATION START: Get user_uuid from executor ---
    user_uuid = executor.user_uuid
    # --- MODIFICATION END ---

    # --- MODIFICATION START: Plan-Aware and Resilient Date Handling ---
    is_pre_calculated = False
    arg_value = args.get(date_param_name)

    # Scenario 1: The date argument points to a pre-calculated list in the workflow state.
    if isinstance(arg_value, dict) and "source" in arg_value:
        source_key = arg_value.get("source")
        if source_key in executor.workflow_state:
            source_data = executor.workflow_state[source_key]
            if (isinstance(source_data, list) and len(source_data) > 0 and 
                isinstance(source_data[0], dict) and 'results' in source_data[0]):
                
                potential_dates = source_data[0]['results']
                if isinstance(potential_dates, list) and all(isinstance(d, dict) and 'date' in d for d in potential_dates):
                    date_list = potential_dates
                    is_pre_calculated = True

    # Scenario 1.5: Validate date argument format before proceeding
    if not is_pre_calculated:
        # ONLY reject dict placeholders - these are unresolved references from Pass 0
        # Examples: {"source": "date_range", "duration": 2}, {"key": "phase_1_result"}
        # DO NOT reject temporal phrase STRINGS - the orchestrator is DESIGNED to handle them
        if isinstance(arg_value, dict) and ("source" in arg_value or "key" in arg_value):
            yield _format_sse({
                "step": "System Correction", "type": "workaround",
                "details": (
                    f"Date Range Orchestrator received unresolved placeholder: {arg_value}. "
                    f"This indicates fast-path validation failed. Falling back to tactical planning."
                )
            })
            app_logger.error(
                f"ORCHESTRATOR GUARD: Unresolved placeholder detected in date argument: {arg_value}. "
                f"Fast-path validation should have blocked this. Forcing tactical planning."
            )

            # Raise error to trigger phase retry with tactical planning
            raise ValueError(
                f"Date argument contains unresolved placeholder: {arg_value}. "
                f"Cannot proceed with orchestration. Requires tactical planning."
            )

        # Log when temporal phrase strings are received (this is EXPECTED and CORRECT)
        if isinstance(arg_value, str):
            temporal_patterns = [
                r'past\s+\d+\s+(hours?|days?|weeks?|months?)',
                r'last\s+\d+\s+(hours?|days?|weeks?|months?)',
                r'(yesterday|today)'
            ]
            if any(re.search(pattern, arg_value.lower()) for pattern in temporal_patterns):
                app_logger.debug(
                    f"ORCHESTRATOR: Received temporal phrase '{arg_value}'. "
                    f"Will call TDA_CurrentDate to resolve date range."
                )

    # Scenario 2: The date argument is a single, valid date string (prevents recursion).
    if not is_pre_calculated and isinstance(arg_value, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', arg_value):
        yield _format_sse({
            "step": "System Correction", "type": "workaround",
            "details": "Orchestrator called with a single date; executing directly to prevent recursion."
        })
        yield _format_sse({"target": "db", "state": "busy"}, "status_indicator_update")
        yield _format_sse({"step": "Tool Execution Intent", "type": "tool_intent", "details": command})
        # --- MODIFICATION START: Pass user_uuid ---
        single_result, _, _ = await mcp_adapter.invoke_mcp_tool(
            executor.dependencies['STATE'], command, user_uuid=user_uuid, session_id=executor.session_id,
            profile_id=executor.active_profile_id
        )
        # --- MODIFICATION END ---
        _result_type = "tool_error" if (isinstance(single_result, dict) and single_result.get("status") == "error") else "tool_result"
        yield _format_sse({"step": "Tool Execution Result", "type": _result_type, "details": single_result})
        yield _format_sse({"target": "db", "state": "idle"}, "status_indicator_update")

        executor._add_to_structured_data(single_result)
        executor.last_tool_output = single_result
        return # Exit the orchestrator

    if is_pre_calculated:
        yield _format_sse({
            "step": "Plan Optimization", "type": "plan_optimization",
            "details": "Date Range Orchestrator is using pre-calculated date list from a previous phase."
        })
    else:
        # Scenario 3: Fallback to original LLM-based calculation for natural language phrases.

        # Guard: If date_phrase is empty/None, try to extract from phase goal
        if not date_phrase or not date_phrase.strip():
            phase_goal = phase.get('goal', '')
            temporal_match = re.search(
                r'((?:past|last|next)\s+\d+\s+(?:hours?|days?|weeks?|months?|years?)'
                r'|yesterday|today'
                r'|this\s+(?:week|month|year)'
                r'|last\s+(?:week|month|year))',
                phase_goal, re.IGNORECASE
            )
            if temporal_match:
                date_phrase = temporal_match.group(1)
                app_logger.info(
                    f"ORCHESTRATOR: Empty date_phrase recovered from phase goal: "
                    f"'{date_phrase}' (goal: '{phase_goal}')"
                )
            else:
                raise RuntimeError(
                    f"Date Range Orchestrator received empty date phrase and could not "
                    f"extract temporal reference from phase goal: '{phase_goal}'"
                )

        yield _format_sse({
            "step": "System Orchestration", "type": "workaround",
            "details": f"Detected date range query ('{date_phrase}') for single-day tool ('{tool_name}')."
        })

        # Log orchestration event to turn_action_history for RAG processing visibility
        phase_num = phase.get("phase", executor.current_phase_index + 1)
        orchestration_event = {
            "action": {
                "tool_name": "TDA_SystemOrchestration",
                "arguments": {
                    "orchestration_type": "date_range",
                    "target_tool": tool_name,
                    "date_phrase": date_phrase,
                    "date_param_name": date_param_name
                },
                "metadata": {
                    "phase_number": phase_num,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "is_orchestration": True
                }
            },
            "result": {
                "status": "orchestration_started",
                "metadata": {"comment": "Date range orchestration initiated"},
                "results": []
            }
        }
        executor.turn_action_history.append(orchestration_event)

        date_command = {"tool_name": "TDA_CurrentDate"}
        date_result, _, _ = await mcp_adapter.invoke_mcp_tool(
            executor.dependencies['STATE'], date_command, user_uuid=user_uuid, session_id=executor.session_id,
            profile_id=executor.active_profile_id
        )
        if not (date_result and date_result.get("status") == "success" and date_result.get("results")):
            raise RuntimeError("Date Range Orchestrator failed to fetch current date.")
        current_date_str = date_result["results"][0].get("current_date")

        # Try deterministic resolution first (pure datetime arithmetic, no LLM needed)
        resolved = _resolve_temporal_phrase_deterministic(current_date_str, date_phrase)
        if resolved:
            start_str, end_str = resolved
            app_logger.info(
                f"ORCHESTRATOR: Deterministic date resolution — "
                f"'{date_phrase}' → {start_str} to {end_str} (no LLM call)"
            )
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
            current_date_in_loop = start_date
            while current_date_in_loop <= end_date:
                date_list.append({"date": current_date_in_loop.strftime('%Y-%m-%d')})
                current_date_in_loop += timedelta(days=1)
        else:
            # Fallback: LLM-based resolution for unrecognized temporal phrases
            app_logger.warning(
                f"ORCHESTRATOR: Cannot resolve '{date_phrase}' deterministically. "
                f"Falling back to LLM."
            )
            conversion_prompt = (
                f"Given the current date is {current_date_str}, "
                f"what are the start and end dates for '{date_phrase}'? "
                "Respond with ONLY a JSON object with 'start_date' and 'end_date' in YYYY-MM-DD format."
            )
            reason = "Calculating date range."
            yield _format_sse({"step": "Calling LLM", "details": {"summary": reason}})
            yield _format_sse({"target": "llm", "state": "busy"}, "status_indicator_update")
            range_response_str, _, _ = await executor._call_llm_and_update_tokens(
                prompt=conversion_prompt, reason=reason,
                system_prompt_override="You are a JSON-only responding assistant.", raise_on_error=True
            )
            yield _format_sse({"target": "llm", "state": "idle"}, "status_indicator_update")

            try:
                # §5: robust_json_parse (three-tier: direct → json_repair → None) instead of a
                # bare regex+json.loads, per the repo-wide parse-hardening mandate.
                range_data = robust_json_parse(range_response_str)
                if not isinstance(range_data, dict):
                    raise json.JSONDecodeError("No JSON object found in LLM response", range_response_str, 0)
                raw_start = range_data.get('start_date')
                raw_end = range_data.get('end_date')
                if not raw_start or not isinstance(raw_start, str) or not raw_end or not isinstance(raw_end, str):
                    raise ValueError(
                        f"LLM returned invalid dates: start_date={raw_start!r}, end_date={raw_end!r}"
                    )
                start_date = datetime.strptime(raw_start, '%Y-%m-%d').date()
                end_date = datetime.strptime(raw_end, '%Y-%m-%d').date()

                current_date_in_loop = start_date
                while current_date_in_loop <= end_date:
                    date_list.append({"date": current_date_in_loop.strftime('%Y-%m-%d')})
                    current_date_in_loop += timedelta(days=1)

            except (json.JSONDecodeError, KeyError, ValueError, AttributeError) as e:
                raise RuntimeError(f"Date Range Orchestrator failed to parse date range. Error: {e}")
    # --- MODIFICATION END ---

    if not date_list or not all(isinstance(d, dict) and 'date' in d for d in date_list):
         raise RuntimeError(f"Orchestrator failed: Date list is empty or malformed. Content: {date_list}")

    # --- Range Tool Mode: call tool ONCE with start_date + end_date ---
    if tool_supports_range:
        start_date_str = date_list[0]['date']
        end_date_str = date_list[-1]['date']

        # Build arguments: remove date params (whole-word match — not substrings like 'update'),
        # then set start_date + end_date.
        range_command_args = {k: v for k, v in args.items() if not _is_date_arg(k)}
        range_command_args['start_date'] = start_date_str
        range_command_args['end_date'] = end_date_str
        range_command = {**command, 'arguments': range_command_args}

        app_logger.info(
            f"ORCHESTRATOR (Range Mode): Calling '{tool_name}' once with "
            f"start_date={start_date_str}, end_date={end_date_str} (resolved from '{date_phrase}')"
        )
        yield _format_sse({
            "step": "System Orchestration", "type": "workaround",
            "details": f"Range tool '{tool_name}': resolved '{date_phrase}' → {start_date_str} to {end_date_str}"
        })
        yield _format_sse({"target": "db", "state": "busy"}, "status_indicator_update")
        yield _format_sse({"step": "Tool Execution Intent", "type": "tool_intent", "details": range_command})

        range_result, _, _ = await mcp_adapter.invoke_mcp_tool(
            executor.dependencies['STATE'], range_command, user_uuid=user_uuid, session_id=executor.session_id,
            profile_id=executor.active_profile_id
        )

        _result_type = "tool_error" if (isinstance(range_result, dict) and range_result.get("status") == "error") else "tool_result"
        yield _format_sse({"step": "Tool Execution Result", "type": _result_type, "details": range_result})
        yield _format_sse({"target": "db", "state": "idle"}, "status_indicator_update")

        _rc_md = range_command.setdefault("metadata", {})
        _rc_md["timestamp"] = datetime.now(timezone.utc).isoformat()
        _rc_md["phase_number"] = phase.get("phase", executor.current_phase_index + 1)  # §2: post-sort key
        executor.turn_action_history.append({"action": range_command, "result": range_result})

        final_tool_output = {
            "status": "success",
            "metadata": {"tool_name": tool_name, "comment": f"Range query: {start_date_str} to {end_date_str}"},
            "results": range_result.get("results", []) if isinstance(range_result, dict) else []
        }

        phase_num = phase.get("phase", executor.current_phase_index + 1)
        phase_result_key = f"result_of_phase_{phase_num}"
        executor.workflow_state[phase_result_key] = [final_tool_output]
        executor._add_to_structured_data(final_tool_output)
        executor.last_tool_output = final_tool_output

        # Log orchestration completion
        orchestration_complete_event = {
            "action": {
                "tool_name": "TDA_SystemOrchestration",
                "arguments": {
                    "orchestration_type": "date_range_complete",
                    "orchestration_mode": "range_resolution",
                    "target_tool": tool_name,
                    "start_date": start_date_str,
                    "end_date": end_date_str
                },
                "metadata": {
                    "phase_number": phase_num,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "is_orchestration": True
                }
            },
            "result": final_tool_output
        }
        executor.turn_action_history.append(orchestration_complete_event)
        return  # Skip the per-day iteration below
    # --- End Range Tool Mode ---

    # --- Single-Date Iteration Mode: call tool concurrently per day ---
    # Strip date params by whole word (not substrings like 'update'/'validate').
    cleaned_command_args = { k: v for k, v in args.items() if not _is_date_arg(k) }
    cleaned_command = {**command, 'arguments': cleaned_command_args}

    yield _format_sse({"target": "db", "state": "busy"}, "status_indicator_update")
    yield _format_sse({
        "step": f"System Orchestration: date_range parallel",
        "type": "system_message",
        "details": {"summary": f"Executing {len(date_list)} date(s) concurrently for '{date_phrase}'"}
    })

    # §2 fix: bound the fan-out with a semaphore ("last year" would otherwise launch 365
    # simultaneous MCP calls) and never abort the whole range on one bad day.
    _sem = asyncio.Semaphore(_MAX_ORCHESTRATOR_CONCURRENCY)
    # Stamp the phase number on each per-day entry so the parallel-group post-sort in
    # _stream_parallel_phases keys them correctly (§2 fix — they previously had no phase_number
    # and sorted to float('inf'), shuffling historical replay when this phase ran in a group).
    _pnum = phase.get("phase", executor.current_phase_index + 1)

    async def _call_for_date(date_item):
        date_str = date_item['date']
        day_cmd = {**cleaned_command, 'arguments': {**cleaned_command['arguments'], date_param_name: date_str}}
        async with _sem:
            day_result, _, _ = await mcp_adapter.invoke_mcp_tool(
                executor.dependencies['STATE'], day_cmd, user_uuid=user_uuid, session_id=executor.session_id,
                profile_id=executor.active_profile_id
            )
        _md = day_cmd.setdefault("metadata", {})
        _md["timestamp"] = datetime.now(timezone.utc).isoformat()
        _md["phase_number"] = _pnum
        return date_str, day_cmd, day_result

    # All days run concurrently (bounded). return_exceptions=True so one failing day can't
    # abort the whole range — failed days are recorded and reflected in the final status.
    day_results = await asyncio.gather(
        *[_call_for_date(d) for d in date_list], return_exceptions=True)

    all_results = []
    failed_days = 0
    for gathered, date_item in zip(day_results, date_list):
        if isinstance(gathered, BaseException):
            # The MCP call itself raised for this day — record an honest error entry.
            failed_days += 1
            _err_result = {"status": "error", "metadata": {"date": date_item.get("date")},
                           "error_message": f"Date '{date_item.get('date')}' failed: {gathered}"}
            _err_cmd = {**cleaned_command, 'arguments': {**cleaned_command['arguments'], date_param_name: date_item.get("date")}}
            _err_cmd.setdefault("metadata", {})["phase_number"] = _pnum  # §2: post-sort key
            yield _format_sse({"step": "Tool Execution Result", "type": "tool_error", "details": _err_result})
            executor.turn_action_history.append({"action": _err_cmd, "result": _err_result})
            continue
        date_str, day_cmd, day_result = gathered
        yield _format_sse({"step": "Tool Execution Intent", "type": "tool_intent", "details": day_cmd})
        _is_err = isinstance(day_result, dict) and day_result.get("status") == "error"
        yield _format_sse({"step": "Tool Execution Result", "type": ("tool_error" if _is_err else "tool_result"), "details": day_result})
        executor.turn_action_history.append({"action": day_cmd, "result": day_result})
        if _is_err:
            failed_days += 1
        elif isinstance(day_result, dict) and day_result.get("status") == "success" and day_result.get("results"):
            all_results.extend(day_result["results"])

    yield _format_sse({"target": "db", "state": "idle"}, "status_indicator_update")

    # Honest status: don't label a partially-failed range "success" (§2 fix).
    _total_days = len(date_list)
    _status = "success" if failed_days == 0 else ("error" if failed_days == _total_days else "partial")
    final_tool_output = {
        "status": _status,
        "metadata": {"tool_name": tool_name, "comment": f"Consolidated results for {date_phrase}",
                     "days_total": _total_days, "days_failed": failed_days},
        "results": all_results
    }

    phase_num = phase.get("phase", executor.current_phase_index + 1)
    phase_result_key = f"result_of_phase_{phase_num}"
    executor.workflow_state[phase_result_key] = [final_tool_output]
    
    executor._add_to_structured_data(final_tool_output)
    executor.last_tool_output = final_tool_output
    
    # Log orchestration completion to turn_action_history
    orchestration_complete_event = {
        "action": {
            "tool_name": "TDA_SystemOrchestration",
            "arguments": {
                "orchestration_type": "date_range_complete",
                "target_tool": tool_name,
                "num_iterations": len(date_list)
            },
            "metadata": {
                "phase_number": phase_num,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "is_orchestration": True
            }
        },
        "result": final_tool_output
    }
    executor.turn_action_history.append(orchestration_complete_event)

# --- MODIFICATION START: Add user_uuid ---
async def execute_column_iteration(executor, command: dict):
# --- MODIFICATION END ---
    """
    Executes a tool over multiple columns of a table, including checks for
    data type compatibility.
    """
    tool_name = command.get("tool_name")
    base_args = command.get("arguments", {})
    # --- MODIFICATION START: Get user_uuid from executor ---
    user_uuid = executor.user_uuid
    # --- MODIFICATION END ---

    # --- MODIFICATION START: Use synonym-aware helper to get arguments ---
    db_name = get_argument_by_canonical_name(base_args, 'database_name')
    table_name = get_argument_by_canonical_name(base_args, 'object_name')
    # --- MODIFICATION END ---

    cols_command = {"tool_name": "base_columnDescription", "arguments": {"database_name": db_name, "object_name": table_name}}
    yield _format_sse({"target": "db", "state": "busy"}, "status_indicator_update")
    yield _format_sse({"step": "Tool Execution Intent", "type": "tool_intent", "details": cols_command})
    # --- MODIFICATION START: Pass user_uuid ---
    cols_result, _, _ = await mcp_adapter.invoke_mcp_tool(
        executor.dependencies['STATE'], cols_command, user_uuid=user_uuid, session_id=executor.session_id,
        profile_id=executor.active_profile_id
    )
    # --- MODIFICATION END ---
    _result_type = "tool_error" if (isinstance(cols_result, dict) and cols_result.get("status") == "error") else "tool_result"
    yield _format_sse({"step": "Tool Execution Result", "type": _result_type, "details": cols_result})
    yield _format_sse({"target": "db", "state": "idle"}, "status_indicator_update")
    
    if not (cols_result and isinstance(cols_result, dict) and cols_result.get('status') == 'success' and cols_result.get('results')):
        raise ValueError(f"Failed to retrieve column list for iteration. Response: {cols_result}")
    
    all_columns_metadata = cols_result.get('results', [])
    all_column_results = [cols_result]
    
    yield _format_sse({"target": "llm", "state": "busy"}, "status_indicator_update")
    # --- MODIFICATION START: Call the relocated method on the main executor ---
    tool_constraints, constraint_events = await executor._get_tool_constraints(tool_name)
    for event in constraint_events:
        yield event
    # --- MODIFICATION END ---
    yield _format_sse({"target": "llm", "state": "idle"}, "status_indicator_update")
    required_type = tool_constraints.get("dataType") if tool_constraints else None
    
    yield _format_sse({"target": "db", "state": "busy"}, "status_indicator_update")

    # Phase A (sync): classify each column as skipped-incompatible or runnable, preserving order.
    # `slots` holds the final per-column result in column order; runnable ones are filled after
    # the parallel gather below.
    _sem = asyncio.Semaphore(_MAX_ORCHESTRATOR_CONCURRENCY)
    slots = []          # ordered results (skipped filled now, runnable placeholders = None)
    run_specs = []      # (slot_index, column_name, iter_command)
    for column_info in all_columns_metadata:
        column_name = column_info.get("ColumnName")
        # §2 fix: guard a null column-type value — `next(...)` returns the matched value, which
        # can be None; `None.upper()` crashed the whole orchestrator. Coerce to str first.
        _ct = next((v for k, v in column_info.items() if "type" in k.lower()), "")
        col_type = str(_ct or "").upper()

        # An empty/unknown type is treated as UNKNOWN (never assumed incompatible).
        if required_type and col_type not in ("UNKNOWN", ""):
            is_numeric = any(t in col_type for t in ["INT", "NUMERIC", "DECIMAL", "FLOAT"])
            is_char = any(t in col_type for t in ["CHAR", "VARCHAR", "TEXT"])
            if (required_type == "numeric" and not is_numeric) or \
               (required_type == "character" and not is_char):
                skipped_result = {"status": "skipped", "metadata": {"tool_name": tool_name, "column_name": column_name}, "results": [{"reason": f"Tool requires {required_type}, but '{column_name}' is {col_type}."}]}
                slots.append(skipped_result)
                yield _format_sse({"step": "Skipping incompatible column", "details": skipped_result}, "tool_result")
                continue

        iter_args = base_args.copy()
        for synonym in AppConfig.ARGUMENT_SYNONYM_MAP.get('column_name', set()):
            iter_args.pop(synonym, None)
        iter_args['column_name'] = column_name
        iter_command = {"tool_name": tool_name, "arguments": iter_args}
        run_specs.append((len(slots), column_name, iter_command))
        slots.append(None)  # placeholder, filled after the gather

    # Phase B (bounded parallel): run the compatible columns concurrently (§2 fix — was strictly
    # serial). Semaphore-bounded + return_exceptions so one bad column can't abort the rest.
    _col_pnum = getattr(executor, "current_phase_index", 0) + 1  # §2: post-sort key stamp

    async def _call_for_column(iter_command):
        async with _sem:
            col_result, _, _ = await mcp_adapter.invoke_mcp_tool(
                executor.dependencies['STATE'], iter_command, user_uuid=user_uuid, session_id=executor.session_id,
                profile_id=executor.active_profile_id)
        _md = iter_command.setdefault("metadata", {})
        _md["timestamp"] = datetime.now(timezone.utc).isoformat()
        _md["phase_number"] = _col_pnum
        return col_result

    _gathered = await asyncio.gather(
        *[_call_for_column(spec[2]) for spec in run_specs], return_exceptions=True)

    failed_cols = 0
    for (slot_idx, column_name, iter_command), gathered in zip(run_specs, _gathered):
        if isinstance(gathered, BaseException):
            col_result = {"status": "error", "metadata": {"tool_name": tool_name, "column_name": column_name},
                          "error_message": f"Column '{column_name}' failed: {gathered}"}
        else:
            col_result = gathered
        _is_err = isinstance(col_result, dict) and col_result.get("status") == "error"
        if _is_err:
            failed_cols += 1
        yield _format_sse({"step": "Tool Execution Intent", "type": "tool_intent", "details": iter_command})
        yield _format_sse({"step": "Tool Execution Result", "type": ("tool_error" if _is_err else "tool_result"), "details": col_result})
        # §2 fix: record per-column calls in the trace (the documented TDA_SystemOrchestration
        # contract — the column orchestrator previously left NO trace entries).
        executor.turn_action_history.append({"action": iter_command, "result": col_result})
        slots[slot_idx] = col_result

    all_column_results.extend([s for s in slots if s is not None])
    yield _format_sse({"target": "db", "state": "idle"}, "status_indicator_update")

    executor._add_to_structured_data(all_column_results)
    _status = "success"
    if failed_cols > 0:
        _status = "error" if (run_specs and failed_cols >= len(run_specs)) else "partial"
    executor.last_tool_output = {
        "metadata": {"tool_name": tool_name, "columns_total": len(all_columns_metadata), "columns_failed": failed_cols},
        "results": all_column_results, "status": _status}

    # §2 fix: append the TDA_SystemOrchestration wrapper (the documented contract, mirrors the
    # date-range orchestrator) so trace analysis + RAG seeding see the orchestration boundary.
    _phase_num = getattr(executor, "current_phase_index", 0) + 1
    executor.turn_action_history.append({
        "action": {
            "tool_name": "TDA_SystemOrchestration",
            "arguments": {"orchestration_type": "column_iteration_complete", "target_tool": tool_name,
                          "columns_total": len(all_columns_metadata), "columns_failed": failed_cols},
            "metadata": {"phase_number": _phase_num, "timestamp": datetime.now(timezone.utc).isoformat(),
                         "is_orchestration": True},
        },
        "result": executor.last_tool_output,
    })

async def execute_hallucinated_loop(executor, phase: dict):
    """
    Handles cases where the planner hallucinates a loop over a list of natural
    language strings instead of a proper data source. It uses an LLM to
    semantically understand the intent and then executes a deterministic loop.
    """
    tool_name = phase.get("relevant_tools", [None])[0]
    hallucinated_items = phase.get("loop_over", [])
    
    yield _format_sse({
        "step": "System Correction", "type": "workaround",
        "details": f"Planner hallucinated a loop. Correcting with generalized orchestrator for items: {hallucinated_items}"
    })

    if len(hallucinated_items) == 1 and isinstance(hallucinated_items[0], str):
        date_keywords = ["day", "week", "month", "year", "past", "last", "next"]
        if any(keyword in hallucinated_items[0].lower() for keyword in date_keywords):
            command_for_date_orchestrator = {"tool_name": tool_name, "arguments": {}}
            # QW1 (assessment C6b): execute_date_range_orchestrator requires `phase` as its
            # 5th positional arg — omitting it raised TypeError for every temporal item,
            # dead-ending this whole delegation branch. Thread the current phase through.
            async for event in execute_date_range_orchestrator(executor, command_for_date_orchestrator, 'date', hallucinated_items[0], phase):
                yield event
            return

    semantic_prompt = (
        f"Given the tool `{tool_name}` and the list of items `{json.dumps(hallucinated_items)}`, "
        "what single tool argument name do these items represent? "
        "Respond with only a JSON object, like `{{\"argument_name\": \"table_name\"}}`."
    )
    reason = "Semantically analyzing hallucinated loop items."
    yield _format_sse({"step": "Calling LLM", "details": {"summary": reason}})
    yield _format_sse({"target": "llm", "state": "busy"}, "status_indicator_update")
    response_str, _, _ = await executor._call_llm_and_update_tokens(
        prompt=semantic_prompt, reason=reason,
        system_prompt_override="You are a JSON-only responding assistant.", raise_on_error=True
    )
    yield _format_sse({"target": "llm", "state": "idle"}, "status_indicator_update")
    
    try:
        # §5: robust_json_parse per the repo-wide parse-hardening mandate.
        semantic_data = robust_json_parse(response_str)
        if not isinstance(semantic_data, dict):
            raise ValueError("No JSON object found in LLM response.")
        argument_name = semantic_data.get("argument_name")
        if not argument_name:
            raise ValueError("LLM did not provide an 'argument_name'.")
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"Failed to semantically understand hallucinated loop items. Error: {e}")

    all_results = []
    yield _format_sse({"target": "db", "state": "busy"}, "status_indicator_update")
    for item in hallucinated_items:
        yield _format_sse({"step": f"Processing item: {item}"})
        command = {"tool_name": tool_name, "arguments": {argument_name: item}}
        yield _format_sse({"step": "Tool Execution Intent", "type": "tool_intent", "details": command})
        # --- MODIFICATION START: Pass user_uuid ---
        result, _, _ = await mcp_adapter.invoke_mcp_tool(
            executor.dependencies['STATE'], command, user_uuid=executor.user_uuid, session_id=executor.session_id,
            profile_id=executor.active_profile_id
        )
        # --- MODIFICATION END ---
        _result_type = "tool_error" if (isinstance(result, dict) and result.get("status") == "error") else "tool_result"
        yield _format_sse({"step": "Tool Execution Result", "type": _result_type, "details": result})
        all_results.append(result)
    yield _format_sse({"target": "db", "state": "idle"}, "status_indicator_update")

    executor._add_to_structured_data(all_results)
    executor.last_tool_output = {"metadata": {"tool_name": tool_name}, "results": all_results, "status": "success"}

