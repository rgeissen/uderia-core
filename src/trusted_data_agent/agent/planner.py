# trusted_data_agent/agent/planner.py
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import re
import json
import logging
import copy
import uuid
from typing import TYPE_CHECKING, Optional
from pathlib import Path

from langchain_mcp_adapters.prompts import load_mcp_prompt

from trusted_data_agent.core import session_manager
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.agent.prompts import (
    WORKFLOW_META_PLANNING_PROMPT,
)
from trusted_data_agent.agent.rag_access_context import RAGAccessContext  # --- MODIFICATION: Import RAGAccessContext ---
from trusted_data_agent.agent.json_utils import robust_json_parse
from trusted_data_agent.agent.conversational_guard import goal_is_small_talk, REPLAN_NOTE
from trusted_data_agent.agent.plan_compiler import UTILITY_TOOLS as _UTILITY_TOOLS, SYSTEM_TOOLS as _SYSTEM_TOOLS_CONST
from trusted_data_agent.core.tool_identity import base_name

if TYPE_CHECKING:
    from trusted_data_agent.agent.executor import PlanExecutor
    from trusted_data_agent.agent.rag_retriever import RAGRetriever  # Type hint only - lazy loaded at runtime


app_logger = logging.getLogger("quart.app")


def get_prompt_text_content(prompt_obj):
    """
    Extracts the text content from a loaded prompt object, handling different
    potential formats returned by the MCP adapter.
    """
    if isinstance(prompt_obj, str):
        return prompt_obj
    if (isinstance(prompt_obj, list) and
        len(prompt_obj) > 0 and
        hasattr(prompt_obj[0], 'content') and
        isinstance(prompt_obj[0].content, str)):
        return prompt_obj[0].content
    elif (isinstance(prompt_obj, dict) and
        'messages' in prompt_obj and
        isinstance(prompt_obj['messages'], list) and
        len(prompt_obj['messages']) > 0 and
        'content' in prompt_obj['messages'][0] and
        isinstance(prompt_obj['messages'][0]['content'], dict) and
        'text' in prompt_obj['messages'][0]['content']):
        return prompt_obj['messages'][0]['content']['text']

    return ""


# C4: canonical inter-phase reference pattern. Mirrors executor._phase_referenced_deps so the
# renumber helper rewrites EXACTLY the references the scheduler/resolver recognise.
_PHASE_REF_RE = re.compile(r'(result_of_phase_|phase_)(\d+)')

# §2 fix — SINGLE SOURCE of the temporal phrase patterns, consumed by BOTH _inject_temporal_context
# (detection) and _extract_temporal_phrase (extraction). Previously the two lists had drifted:
# injection matched `recent|latest` + number-less "in the past", extraction didn't — so those
# queries got a TDA_CurrentDate phase injected but no date wired (a dead phase). Sharing one list
# guarantees detection ⇔ extraction: temporal context is only injected when a resolvable phrase
# exists. Each pattern captures the phrase in group(1).
_TEMPORAL_PATTERNS = [
    r'(past\s+\d+\s+(?:hours?|days?|weeks?|months?))',
    r'(last\s+\d+\s+(?:hours?|days?|weeks?|months?))',
    r'(yesterday|today)',
    r'(in\s+the\s+(?:last|past)\s+\d+\s+(?:hours?|days?|weeks?|months?))',
    r'(for\s+the\s+(?:past|last)\s+\d+\s+(?:hours?|days?|weeks?|months?))',
    r'(\d+\s+(?:hours?|days?|weeks?|months?)\s+ago)',
    r'(this\s+(?:week|month|year))',
    r'(current\s+(?:week|month|year))',
]


def renumber_plan_refs(plan: list, mapping: dict) -> list:
    """C4: renumber phases AND every inter-phase reference in ONE operation.

    The four passes that renumber phases (temporal injection, multi-loop insert, SQL
    consolidation, chart-collapse) previously rewrote only the ``phase`` field, leaving
    ``loop_over`` / ``result_of_phase_N`` string refs / canonical ``{"source": …}`` dicts /
    ``source_data`` pointing at the OLD numbers — so e.g. after temporal injection prepends
    TDA_CurrentDate as phase 1, ``result_of_phase_1`` silently resolved to the date instead of
    the (now phase-2) table list. This rewrites the phase number and all references to it
    together.

    ``mapping`` is ``old_phase_number -> new_phase_number``. A number ABSENT from the mapping is
    left unchanged wherever it appears — this is what lets a caller renumber only the phases
    that moved while references to removed/injected phases keep their old keys (e.g. the
    chart-collapse survivors that reference prior-turn data injected under ``result_of_phase_<old>``).

    Per phase it rewrites: the ``phase`` field (via the mapping) and ``result_of_phase_<n>`` /
    ``phase_<n>`` inside ``loop_over``, ``arguments`` (recursively — covers canonical source
    dicts, ``source_data`` lists, ``data``, ``data_to_filter``, …), ``goal``, ``conditions``,
    ``filter``, and a top-level ``source_data``. Mutates ``plan`` in place and returns it. Pure.
    """
    def _rewrite_str(s: str) -> str:
        return _PHASE_REF_RE.sub(
            lambda m: f"{m.group(1)}{mapping.get(int(m.group(2)), int(m.group(2)))}", s)

    def _rewrite(obj):
        if isinstance(obj, str):
            return _rewrite_str(obj)
        if isinstance(obj, dict):
            return {k: _rewrite(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_rewrite(x) for x in obj]
        return obj

    for phase in plan:
        if not isinstance(phase, dict):
            continue
        if isinstance(phase.get("phase"), int):
            phase["phase"] = mapping.get(phase["phase"], phase["phase"])
        for field in ("loop_over", "goal", "conditions", "filter", "source_data"):
            if field in phase:
                phase[field] = _rewrite(phase[field])
        if isinstance(phase.get("arguments"), (dict, list)):
            phase["arguments"] = _rewrite(phase["arguments"])
    return plan


def _norm_question(q) -> str:
    """The wording of a question as a champion key: case, whitespace and a trailing
    full stop / question mark do not make a different question."""
    return re.sub(r"\s+", " ", str(q or "").strip().lower()).rstrip(" .?!")


def exact_signed_champion(query: str, cases, is_locked) -> Optional[dict]:
    """A signed seed asked WORD FOR WORD is replayed, not re-planned.

    Reasoning belongs only where code cannot decide. When the question matches a champion's
    own question exactly, and that champion is a SEEDED template case (`template_generated`)
    held by a trust-LOCKED planner, or a SIGNED one whose signature still covers its content
    (`planner_replayable` — every case in it was put there on purpose and signed), there is
    nothing left for a
    planner to decide: the proven strategy IS the plan. Re-planning it through a model only
    adds variance — on production the cross-over seed of the policing demo was adopted one
    time in three, the other two answering a different question. A learned champion, a near
    match, or a seed in an unlocked planner still guides the model as a few-shot example.

    ``is_locked(collection_id) -> bool`` is injected (`planner_replayable`), so the rule is
    a pure function. Returns ``{case_id, collection_id, strategy}`` (a deep copy) or None.
    """
    key = _norm_question(query)
    if not key:
        return None
    for c in cases or []:
        fc = (c or {}).get("full_case_data") or {}
        md = fc.get("metadata") or {}
        if not md.get("template_generated"):
            continue
        if _norm_question((fc.get("intent") or {}).get("user_query")) != key:
            continue
        strat = fc.get("successful_strategy")
        phases = strat.get("phases") if isinstance(strat, dict) else None
        if not phases:
            continue
        cid = md.get("collection_id")
        try:
            if cid is None or not is_locked(int(cid)):
                continue
        except Exception:  # noqa: BLE001 — unknown lock state ⇒ not a signed seed
            continue
        return {"case_id": c.get("case_id") or fc.get("case_id"), "collection_id": int(cid),
                "strategy": copy.deepcopy(strat)}
    return None


def _sf_sql_tables(sql):
    from trusted_data_agent.agent import schema_facts as _sf
    return _sf.sql_tables(sql)



def parse_plan_response(response_text) -> object:
    """The strategic plan in a planner response, or None when there is nothing to plan from.

    An empty or truncated response ("", "[{") is None, like any text with no plan in it. None sends
    the turn to the parse-error retry; a retry that is also None ends it (OC-F16: such a response
    used to end the turn as fatal before the retry ran)."""
    text = response_text or ""
    if len(text.strip()) < 3:
        return None
    return robust_json_parse(text)


def planner_binding_scope(profile):
    """Which planner repositories an agent retrieves champions from: "all", "none", or the set.

    OC-F4 (Optimize campaign, 2026-10-29): an agent with NO planner repository bound
    (`ragCollections` missing or empty) retrieved from EVERY planner repository its owner could
    read — the operator's own, other servers', the campaign's — so its answers rested on
    repositories its trust composition does not list (`trust/composition.py` reads the stored
    binding). Now an agent retrieves exactly what it binds: nothing bound ⇒ none. Only the
    explicit wildcard `"*"` still means every accessible repository (a stated choice)."""
    rc = (profile or {}).get("ragCollections")
    if isinstance(rc, list) and "*" in rc:
        return "all"
    if not rc:
        return "none"
    return set(rc)


def planner_replayable(collection_id, row=None, *, attested=None, live=None) -> bool:
    """May a seed of this planner repository be replayed verbatim? LOCKED, or SIGNED AND INTACT.

    OC-F3 (Optimize campaign, 2026-10-29): replay required `trust_locked`, and signing a
    planner does not lock it — so on every operator repository (none is locked) a signed seed
    asked word for word was only a few-shot example and was re-planned through a model. A
    signed repository qualifies when its signature still matches its CONTENT: the attested
    hash equals the live hash (`attestation.resolve_state` — the one trust-state definition).
    An unlocked repository keeps learning, so a case learned after signing changes the hash and
    the repository stops qualifying until it is signed again — a learned case is never replayed
    under a signature that does not cover it. ``attested``/``live`` are injectable for tests;
    any lookup failure reads "not replayable" (re-plan as before)."""
    try:
        if row is not None and row.get("trust_locked"):
            return True
        if attested is None or live is None:
            from trusted_data_agent.core.trust import store as _ts, content as _tc
            attested = _ts.attested_hash("planner", str(collection_id)) if attested is None else attested
            live = _tc.planner_cases(str(collection_id))[1] if live is None else live
        if not attested or not live:
            return False
        from trusted_data_agent.core.trust.attestation import resolve_state, TrustState
        return resolve_state(attested, live) == TrustState.TRUSTED
    except Exception:  # noqa: BLE001 — unknown ⇒ not replayable
        return False

# The strategic template's values that are the same on every turn of an agent. Anything not
# listed is volatile (the question, history, champions, knowledge, constraints, replans…), so
# a value added to the template later is never cached by accident. The tool catalog is stable
# only when every enabled tool is shown: a governed surface offers a different few per turn.
_STRATEGIC_STABLE_KEYS = frozenset({"mcp_system_name", "reporting_tool_name", "sql_consolidation_rule",
                                    "available_prompts", "component_tools",
                                    "needs_input_carrier"})   # the agent's component + policy, not the turn


def _strategic_cache_prefix(planning_prompt: str, rendered: str, values: dict):
    """The strategic call's stable prefix: what precedes the rendered template in the final
    prompt (skills, chart note) plus the template's own stable lead. None if not derivable —
    the handler verifies it against the prompt anyway."""
    try:
        from trusted_data_agent.llm import cache_policy as _cp
        from trusted_data_agent.agent import tool_surface as _ts
        stable = set(_STRATEGIC_STABLE_KEYS)
        if _ts.current() is None:
            stable.add("available_tools")
        lead = _cp.template_stable_prefix(WORKFLOW_META_PLANNING_PROMPT, values, stable)
        off = planning_prompt.find(rendered)
        if lead is None or off < 0:
            return None
        return planning_prompt[:off] + lead
    except Exception:  # noqa: BLE001 — a cache hint never costs the plan
        return None


class Planner:
    """
    Encapsulates all logic related to plan generation, validation, and refinement.
    It is instantiated by the PlanExecutor and maintains a reference to it for state
    and helper method access.
    """
    # --- MODIFICATION START: Accept RAGRetriever instance from executor ---
    def __init__(self, executor: 'PlanExecutor', rag_retriever_instance: Optional['RAGRetriever'] = None, event_handler=None):
        self.executor = executor
        self.rag_retriever = rag_retriever_instance
        self.event_handler = event_handler
        if APP_CONFIG.RAG_ENABLED and not self.rag_retriever:
            app_logger.warning("Planner initialized without a RAGRetriever instance, though RAG is enabled.")
        elif APP_CONFIG.RAG_ENABLED and self.rag_retriever:
            app_logger.info("Planner initialized with global RAGRetriever instance.")
    # --- MODIFICATION END ---

    # --- MODIFICATION START: Update history creation to filter by context validity ---
    def _create_summary_from_history(self, history: dict) -> str:
        """
        Creates an enhanced summary of previous workflow history for planning context.

        Includes explicit turn metadata to help the planner:
        - Identify the most recent turn
        - Understand profile types used
        - Extract SQL queries from conversation context

        This function filters out any turns marked with 'isValid': false.
        """
        if not history or not isinstance(history, dict) or "workflow_history" not in history:
            return json.dumps({"workflow_history": []}, indent=2)

        # 1. Get the full workflow history
        full_workflow_history = history.get("workflow_history", [])
        if not isinstance(full_workflow_history, list):
             return json.dumps({"workflow_history": []}, indent=2)

        # 2. Filter for valid turns only
        # A turn is valid if 'isValid' is missing (defaults to true) or is explicitly true.
        valid_workflow_history = [
            turn for turn in full_workflow_history
            if isinstance(turn, dict) and turn.get("isValid", True) is not False
        ]

        # --- MODIFICATION START: Scrub TDA_SystemLog messages and add turn metadata ---
        scrubbed_workflow_history = []
        for idx, turn in enumerate(valid_workflow_history):
            new_turn = copy.deepcopy(turn)

            # Remove UI-only fields from context to keep it lean
            # These are stored for plan reload UI but not needed for LLM planning
            ui_only_fields = [
                "genie_events",
                "slave_sessions",
                "provider",
                "model",
                "status",
                "conversation_agent_events",
                "knowledge_events",  # Event stream for RAG/knowledge profiles (UI replay only)
                "system_events",  # System operations like session name generation (UI replay only)
                "knowledge_chunks_ui",  # Document chunks for UI display (extracted from knowledge_retrieval_event)
                "session_input_tokens",  # Session totals for UI display only
                "session_output_tokens",  # Session totals for UI display only
                "final_summary_html",  # HTML formatted output (UI only, LLM uses final_summary_text)
                "tts_payload",  # Text-to-speech data (UI only)
                "raw_llm_plan"  # Pre-rewrite LLM plan (session analysis only, not needed for planning)
            ]
            for field in ui_only_fields:
                if field in new_turn:
                    del new_turn[field]

            # Remove heavy chunks array from knowledge_retrieval_event (already in knowledge_chunks_ui)
            if "knowledge_retrieval_event" in new_turn and isinstance(new_turn["knowledge_retrieval_event"], dict):
                if "chunks" in new_turn["knowledge_retrieval_event"]:
                    del new_turn["knowledge_retrieval_event"]["chunks"]

            if "execution_trace" in new_turn and isinstance(new_turn["execution_trace"], list):
                scrubbed_trace = []
                for entry in new_turn["execution_trace"]:
                    if isinstance(entry, dict):
                        action_data = entry.get("action", {})
                        if isinstance(action_data, dict) and action_data.get("tool_name") != "TDA_SystemLog":
                            scrubbed_trace.append(entry)
                new_turn["execution_trace"] = scrubbed_trace

            # Add explicit metadata for planning context
            new_turn["turn_metadata"] = {
                "turn_number": new_turn.get("turn", idx + 1),
                "profile_tag": new_turn.get("profile_tag", "unknown"),
                "profile_type": new_turn.get("profile_type", "unknown"),
                "is_most_recent": (idx == len(valid_workflow_history) - 1)
            }

            # Extract SQL queries from final_summary_text (for llm_only turns)
            # This helps the planner find SQL queries that weren't executed
            if "final_summary_text" in new_turn:
                # Try multiple SQL extraction patterns
                sql_pattern_with_backticks = r"```sql\n(.*?)\n```"
                sql_pattern_generic = r"```\n(SELECT.*?)\n```"
                sql_pattern_plain = r"(SELECT\s+.+?FROM\s+.+?(?:WHERE\s+.+?)?(?:GROUP\s+BY\s+.+?)?(?:ORDER\s+BY\s+.+?)?;)"

                sql_matches = []
                # Try markdown SQL block first
                sql_matches.extend(re.findall(sql_pattern_with_backticks, new_turn["final_summary_text"], re.DOTALL | re.IGNORECASE))
                # Try generic code block
                if not sql_matches:
                    sql_matches.extend(re.findall(sql_pattern_generic, new_turn["final_summary_text"], re.DOTALL | re.IGNORECASE))
                # Try plain SELECT statement
                if not sql_matches:
                    sql_matches.extend(re.findall(sql_pattern_plain, new_turn["final_summary_text"], re.DOTALL | re.IGNORECASE))

                if sql_matches:
                    new_turn["turn_metadata"]["sql_mentioned_in_conversation"] = sql_matches
                    app_logger.debug(f"Extracted {len(sql_matches)} SQL queries from turn {new_turn.get('turn', idx + 1)} (profile: {new_turn.get('profile_tag')})")

            scrubbed_workflow_history.append(new_turn)
        # --- MODIFICATION END ---

        # 3. Create the final history object with only valid turns
        # Add summary header with context
        valid_history_summary = {
            "total_turns": len(scrubbed_workflow_history),
            "most_recent_turn_number": scrubbed_workflow_history[-1].get("turn", len(scrubbed_workflow_history)) if scrubbed_workflow_history else 0,
            "workflow_history": scrubbed_workflow_history
        }

        app_logger.debug(f"Planner context created. Original turns: {len(full_workflow_history)}, Valid (active) turns: {len(valid_workflow_history)}")

        return json.dumps(valid_history_summary, indent=2)
    # --- MODIFICATION END ---

    def _hydrate_plan_from_previous_turn(self):
        """
        Detects if a plan starts with a loop that depends on data from the
        previous turn, and if so, injects that data into the current state.
        This is the "plan injection" feature.
        """
        if not self.executor.meta_plan or not self.executor.previous_turn_data:
            return

        first_phase = self.executor.meta_plan[0]
        is_candidate = (
            first_phase.get("type") == "loop" and
            isinstance(first_phase.get("loop_over"), str) and
            first_phase.get("loop_over").startswith("result_of_phase_")
        )

        if not is_candidate:
            return

        looping_phase_num = first_phase.get("phase")
        source_phase_key = first_phase.get("loop_over")
        source_phase_num_match = re.search(r'\d+', source_phase_key)
        if not source_phase_num_match:
            return
        source_phase_num = int(source_phase_num_match.group())

        if source_phase_num >= looping_phase_num:
            data_to_inject = None

            workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
            if not isinstance(workflow_history, list):
                 return

            for turn in reversed(workflow_history):
                if not isinstance(turn, dict): continue
                execution_trace = turn.get("execution_trace", [])
                for entry in reversed(execution_trace):
                    # C3 fix (restore): execution_trace entries are {"action", "result"} (see
                    # phase_executor.turn_action_history.append + eval/types.RunRecord). The pass
                    # previously read a phantom "tool_output_summary" key that NO producer ever
                    # writes, so it never found a success entry and hydration silently never fired
                    # (the documented 30-50% multi-turn token saving never happened). Read the real
                    # result, with a defensive fallback to a nested tool_output_summary for any
                    # legacy entry that used that shape.
                    from trusted_data_agent.core.trace_shape import phase_output
                    result_summary = phase_output(entry)     # ONE reader for every consumer
                    if not result_summary:
                        continue
                    if result_summary.get("status") == "success":
                        data_to_inject = {
                            "status": "success",
                            "metadata": result_summary.get("metadata", {}),
                            "comment": "Data injected from previous turn's result."
                        }
                        if "results" in result_summary:
                            data_to_inject["results"] = result_summary["results"]
                        break
                if data_to_inject:
                    break

            if data_to_inject:
                injection_key = "injected_previous_turn_data"
                self.executor.workflow_state[injection_key] = [data_to_inject]

                original_loop_source = self.executor.meta_plan[0]['loop_over']
                self.executor.meta_plan[0]['loop_over'] = injection_key

                app_logger.info(f"PLAN INJECTION: Hydrated plan with data from previous turn. Loop source changed from '{original_loop_source}' to '{injection_key}'.")

                event_data = {
                    "step": "Plan Optimization",
                    "type": "plan_optimization",
                    "details": f"PLAN HYDRATION: Injected data from the previous turn to fulfill the request: '{self.executor.original_user_input}'."
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

    @staticmethod
    def _rekey_unknown_server_tool(name, all_tools):
        """``<alias>__<name>`` for an alias NOT armed in this turn → the one catalog tool with the
        same base name (a plain first-server name preferred), else None. A name the catalog has,
        or one that is not alias-shaped, is never touched."""
        try:
            from trusted_data_agent.core.tool_identity import SEP, valid_alias, base_name, turn_aliases
            if not isinstance(name, str) or name in (all_tools or {}) or SEP not in name:
                return None
            prefix, rest = name.split(SEP, 1)
            if not valid_alias(prefix) or prefix in turn_aliases() or not rest:
                return None
            if rest in (all_tools or {}):
                return rest
            hits = [t for t in (all_tools or {}) if base_name(t) == rest]
            return hits[0] if len(hits) == 1 else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _rename_unknown_tool(name, args, all_tools):
        """A planned tool the catalog does not have → the catalog tool it meant, or None.
        Three deterministic readings, in order: a component name with an action suffix
        (`TDA_DatabaseNavigator_search`) → the component; a phase carrying a SELECT under an
        invented SQL runner (`TDA_ExecuteSQL`, `base_query`) → the read tool; else the one
        catalog name within difflib 0.8 (`base_tableDescriptionn`). Anything else stands —
        the call fails honestly and the correction chain runs (Optimize campaign: 12 of 188
        plans named a tool the catalog lacked and paid 16 self-corrections; 5 of 9 distinct
        shapes are repaired here at 0 tokens)."""
        try:
            if not isinstance(name, str) or not name or name in (all_tools or {}):
                return None
            from trusted_data_agent.core.tool_identity import SEP
            if SEP in name:
                return None     # an alias shape belongs to 3b
            if name.startswith("TDA_") and "_" in name[4:]:
                base = name.rsplit("_", 1)[0]
                if base in (all_tools or {}) or base in ("TDA_DatabaseNavigator", "TDA_Charting", "TDA_Compute"):
                    return base
            a = args if isinstance(args, dict) else {}
            sql = a.get("sql") or a.get("query") or a.get("statement")
            if isinstance(sql, str) and re.match(r"\s*(select|with)\b", sql, re.I):
                from trusted_data_agent.agent import schema_facts as _sf
                rt = _sf.tool_for("base_readQuery", list(all_tools or {}))
                if rt:
                    return rt
            import difflib
            close = difflib.get_close_matches(name, list(all_tools or {}), n=1, cutoff=0.8)
            return close[0] if close else None
        except Exception:  # noqa: BLE001
            return None

    def _validate_and_correct_plan(self):
        """
        Deterministically validates the generated meta-plan for common LLM errors,
        such as misclassifying prompts as tools, and corrects them in place.
        """
        if not self.executor.meta_plan:
            return

        all_prompts = self.executor.dependencies['STATE'].get('mcp_prompts', {})
        all_tools = self.executor.dependencies['STATE'].get('mcp_tools', {})

        for phase in self.executor.meta_plan:
            original_phase = copy.deepcopy(phase)
            correction_made = False
            correction_type = None
            
            # Correction 1: Clean up null/None/empty executable_prompt values
            if 'executable_prompt' in phase and phase['executable_prompt'] in [None, 'None', 'null', '', 'undefined']:
                invalid_value = phase['executable_prompt']
                app_logger.warning(f"PLAN CORRECTION: Removing invalid executable_prompt value: '{invalid_value}'")
                del phase['executable_prompt']
                correction_made = True
                correction_type = "invalid_prompt"

            # Correction 2: Prompt misclassified as tool
            if 'relevant_tools' in phase and isinstance(phase['relevant_tools'], list) and phase['relevant_tools']:
                capability_name = phase['relevant_tools'][0]
                if capability_name in all_prompts:
                    app_logger.warning(f"PLAN CORRECTION: Planner wrongly classified prompt '{capability_name}' as a tool. Correcting.")
                    phase['executable_prompt'] = capability_name
                    del phase['relevant_tools']
                    correction_made = True
                    correction_type = "prompt_as_tool"

            # --- MODIFICATION START: Change elif to if for independent correction ---
            # Correction 3: Tool misclassified as prompt
            # Changed from 'elif' to 'if' to ensure this correction runs independently.
            # This handles cases where LLM generates both fields (e.g., empty relevant_tools
            # AND executable_prompt with a tool like TDA_ContextReport). Corrections 2 and 3
            # are mutually exclusive in practice (one deletes what the other creates), so
            # both won't trigger on the same phase in a single pass.
            if 'executable_prompt' in phase and isinstance(phase['executable_prompt'], str):
                capability_name = phase['executable_prompt']
                if capability_name in all_tools:
                    app_logger.warning(f"PLAN CORRECTION: Planner wrongly classified tool '{capability_name}' as a prompt. Correcting.")
                    phase['relevant_tools'] = [capability_name]
                    del phase['executable_prompt']
                    correction_made = True
                    correction_type = "tool_as_prompt"
            # --- MODIFICATION END ---
            
            # Correction 3b (PROP-006 O8): a tool named for a server the agent does not have in
            # this turn — `crm__base_readQuery` from a champion case learned when the agent had a
            # `crm` server, or from another agent's planner — is re-keyed to the ONE tool of this
            # catalog with the same base name (the first server's plain name wins). The routing
            # layer then sends the call to the server that holds its data. Ambiguous or absent:
            # the name stands (the call fails honestly and the correction chain runs).
            if 'relevant_tools' in phase and isinstance(phase['relevant_tools'], list) and phase['relevant_tools']:
                _rk = self._rekey_unknown_server_tool(phase['relevant_tools'][0], all_tools)
                if _rk:
                    app_logger.warning(f"PLAN CORRECTION: tool '{phase['relevant_tools'][0]}' names a server "
                                       f"this agent does not have — re-keyed to '{_rk}'.")
                    phase['relevant_tools'][0] = _rk
                    correction_made = True
                    correction_type = "unknown_server_tool"

            # Correction 3c: a tool the catalog does not have at all — an invented SQL runner
            # carrying a SELECT, a component named with its action as a suffix, a near-miss of a
            # real name — is renamed to the tool it meant (0 tokens); anything else stands.
            if 'relevant_tools' in phase and isinstance(phase['relevant_tools'], list) and phase['relevant_tools']:
                _nm = phase['relevant_tools'][0]
                _known_components = {"TDA_FinalReport", "TDA_LLMTask", "TDA_LLMFilter", "TDA_CurrentDate", "TDA_Charting",
                                     "TDA_DateRange", "TDA_DatabaseNavigator", "TDA_Compute", "TDA_FindTools",
                                     "TDA_ContextReport", "TDA_ComplexPromptReport", "TDA_SystemOrchestration"}
                if isinstance(_nm, str) and _nm not in all_tools and _nm not in _known_components:
                    _rn = self._rename_unknown_tool(_nm, phase.get('arguments'), all_tools)
                    if _rn:
                        app_logger.warning(f"PLAN CORRECTION: tool '{_nm}' is not in the catalog — renamed to '{_rn}'.")
                        phase['relevant_tools'][0] = _rn
                        if _rn.endswith("readQuery") and isinstance(phase.get('arguments'), dict) and 'sql' not in phase['arguments']:
                            _q = phase['arguments'].pop('query', None) or phase['arguments'].pop('statement', None)
                            if isinstance(_q, str):
                                phase['arguments']['sql'] = _q
                        correction_made = True
                        correction_type = "unknown_tool"

            # Correction 4: Remove hallucinated/extraneous arguments that don't exist in tool schema
            # Also mark phases that need argument refinement at execution time
            if 'relevant_tools' in phase and isinstance(phase['relevant_tools'], list) and phase['relevant_tools']:
                tool_name = phase['relevant_tools'][0]
                tool_def = all_tools.get(tool_name)
                
                if tool_def and hasattr(tool_def, 'args') and isinstance(tool_def.args, dict):
                    # Get all valid argument names from tool schema (including synonyms)
                    from trusted_data_agent.core.config import AppConfig
                    valid_args = set()
                    required_args = set()
                    
                    for schema_arg_name, arg_details in tool_def.args.items():
                        valid_args.add(schema_arg_name)
                        
                        # Find canonical name by checking synonym map
                        canonical_name = schema_arg_name
                        for canonical, synonyms in AppConfig.ARGUMENT_SYNONYM_MAP.items():
                            if schema_arg_name in synonyms:
                                canonical_name = canonical
                                valid_args.update(synonyms)
                                break
                        
                        # Track required arguments (using canonical name)
                        if isinstance(arg_details, dict) and arg_details.get('required', False):
                            required_args.add(canonical_name)
                    
                    # Ensure arguments dict exists
                    if 'arguments' not in phase:
                        phase['arguments'] = {}
                    
                    # §2 fix — reorder Correction 5 (fuzzy param rename) BEFORE Correction 4
                    # (delete extraneous). Previously Correction 4 deleted a misspelled arg as
                    # "extraneous" before Correction 5 could recompute it, so a misspelling
                    # (e.g. `databse_name`) cost a paid refinement instead of a FREE rename.
                    provided_args = set(phase['arguments'].keys())
                    extraneous_args = provided_args - valid_args
                    schema_params = set(tool_def.args.keys())
                    missing_schema_params = schema_params - provided_args

                    # Correction 5 FIRST: fuzzy-rename the not-yet-valid args to the closest
                    # missing schema param (exact synonym → 1.0, else difflib ≥ 0.7).
                    if extraneous_args and missing_schema_params:
                        param_corrections = {}
                        for unmatched in list(extraneous_args):
                            best_match = None
                            best_similarity = 0
                            for schema_param in missing_schema_params:
                                if self._is_param_synonym(unmatched, schema_param):
                                    best_match = schema_param
                                    best_similarity = 1.0
                                    break
                                import difflib
                                similarity = difflib.SequenceMatcher(None, unmatched.lower(), schema_param.lower()).ratio()
                                if similarity > best_similarity and similarity > 0.7:  # 70% similar threshold
                                    best_match = schema_param
                                    best_similarity = similarity
                            if best_match:
                                param_corrections[unmatched] = best_match
                        if param_corrections:
                            for wrong_name, correct_name in param_corrections.items():
                                phase['arguments'][correct_name] = phase['arguments'].pop(wrong_name)
                                app_logger.info(
                                    f"PASS 5: Corrected parameter name: '{wrong_name}' → '{correct_name}' "
                                    f"in {tool_name} (phase {phase.get('phase', '?')})"
                                )
                                correction_made = True
                                correction_type = "parameter_name_mismatch"
                            # Recompute extraneous AFTER renaming — the renamed ones are now valid.
                            provided_args = set(phase['arguments'].keys())
                            extraneous_args = provided_args - valid_args

                    # Correction 4 SECOND: remove the args that are genuinely unknown to the tool
                    # (couldn't be fuzzy-matched to a schema param).
                    if extraneous_args:
                        app_logger.warning(f"PLAN CORRECTION: Tool '{tool_name}' received hallucinated arguments: {extraneous_args}")
                        for arg_name in extraneous_args:
                            del phase['arguments'][arg_name]
                        correction_made = True
                        if correction_type != "parameter_name_mismatch":
                            correction_type = "extraneous_args"

                    # Check for missing REQUIRED parameters after all corrections
                    final_provided_canonical = set()
                    for arg in phase['arguments'].keys():
                        canonical_arg = arg
                        for canonical, synonyms in AppConfig.ARGUMENT_SYNONYM_MAP.items():
                            if arg in synonyms:
                                canonical_arg = canonical
                                break
                        final_provided_canonical.add(canonical_arg)

                    final_missing_required = required_args - final_provided_canonical
                    if final_missing_required:
                        app_logger.warning(
                            f"PASS 5: Tool {tool_name} missing required parameters: {final_missing_required}. "
                            f"This may cause execution error. Marking for refinement."
                        )
                        phase['_needs_refinement'] = True

            if correction_made:
                # Determine the appropriate message based on correction type
                if correction_type == "invalid_prompt":
                    summary = "Plan contained invalid prompt reference. The system has removed it to prevent execution errors."
                elif correction_type == "prompt_as_tool":
                    summary = "Planner misclassified a prompt as a tool. The system has corrected the plan to ensure proper execution."
                elif correction_type == "tool_as_prompt":
                    summary = "Planner misclassified a tool as a prompt. The system has corrected the plan to ensure proper execution."
                elif correction_type == "extraneous_args":
                    summary = "Plan contained hallucinated arguments not accepted by the tool. The system has removed them to prevent validation errors."
                elif correction_type == "parameter_name_mismatch":
                    summary = "Plan contained parameter names that don't match the tool schema. The system has corrected them to prevent execution errors."
                elif correction_type == "unknown_server_tool":
                    summary = ("Plan named a tool of an MCP server this agent does not have in this turn (a plan learned "
                               "with another server set). The system re-keyed it to the agent's tool of the same name.")
                else:
                    summary = "Plan has been corrected by the system."
                
                event_data = {
                    "step": "Plan Optimization",
                    "type": "workaround",
                    "details": summary,
                    "correction": {
                        "from": original_phase,
                        "to": phase
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

    def _rewrite_plan_for_charting_phases(self):
        """
        Deterministic rewrite: strips unreliable arguments from TDA_Charting phases.

        The strategic planner cannot know actual column names at planning time
        (data-gathering phases haven't executed yet), so its `mapping` and `data`
        arguments are inherently hallucinated. Stripping them ensures the tactical
        planner — which has access to actual data via workflow state — generates
        correct mapping on the first attempt, avoiding an expensive refinement cycle.

        Design principle: This is a deterministic fix (detectable pattern in code)
        rather than a prompt engineering change, avoiding system prompt convolution.
        """
        if not self.executor.meta_plan:
            return

        # Utility tools — from plan_compiler (single source of truth).
        UTILITY_TOOLS = _UTILITY_TOOLS

        # ── TDA_Charting: strip hallucinated mapping/data args ────────────────────
        for phase in self.executor.meta_plan:
            tools = phase.get("relevant_tools", [])
            if not tools or "TDA_Charting" not in tools:
                continue

            # Determine if there are data-gathering phases before this TDA_Charting phase.
            # If yes (same-turn): strip both data and mapping (data doesn't exist yet).
            # If no (cross-turn chart-only): preserve data reference, strip only mapping.
            charting_phase_num = phase.get("phase", 0)
            has_preceding_data_phase = False
            for other_phase in self.executor.meta_plan:
                other_tools = other_phase.get("relevant_tools", [])
                if other_phase.get("phase", 0) < charting_phase_num and other_tools:
                    if not all(t in UTILITY_TOOLS for t in other_tools):
                        has_preceding_data_phase = True
                        break

            if has_preceding_data_phase:
                # Same-turn: strip both data and mapping (data doesn't exist yet)
                UNRELIABLE_ARGS = {"mapping", "data"}
            else:
                # Cross-turn: preserve data reference, strip only mapping (keys unreliable)
                UNRELIABLE_ARGS = {"mapping"}

            args = phase.get("arguments", {})
            stripped = []
            for key in list(args.keys()):
                if key in UNRELIABLE_ARGS:
                    del args[key]
                    stripped.append(key)

            # Clear stale _needs_refinement flag. It was set by _validate_and_correct_plan()
            # based on the strategic plan's (now-stripped) arguments. The tactical planner
            # will determine correct arguments from actual data — forcing refinement would
            # waste ~14,000 tokens re-generating the data array a second time.
            if '_needs_refinement' in phase:
                app_logger.info(
                    f"PLAN REWRITE (Charting Cleanup): Cleared stale _needs_refinement flag "
                    f"from TDA_Charting phase {phase.get('phase')}."
                )
                del phase['_needs_refinement']

            if stripped:
                app_logger.info(
                    f"PLAN REWRITE (Charting Cleanup): Stripped arguments "
                    f"{stripped} from TDA_Charting phase {phase.get('phase')}. "
                    f"Execution engine will determine correct values from actual data."
                )
                event_data = {
                    "step": "Plan Optimization",
                    "type": "plan_optimization",
                    "details": {
                        "summary": (
                            f"Charting Argument Cleanup: Stripped pre-filled arguments "
                            f"({', '.join(stripped)}) from charting phase. The execution engine "
                            f"will determine correct mapping from actual data columns."
                        ),
                        "correction_type": "charting_argument_cleanup",
                        "stripped_arguments": stripped,
                        "phase_number": phase.get("phase")
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

        # ── TDA_Dashboard: clear stale _needs_refinement flag only ──
        # Unlike TDA_Charting, the `content` argument on a TDA_Dashboard visualize phase
        # is set by the LLM to a placeholder string like "result_of_phase_1", NOT to
        # hallucinated inline data rows.  This placeholder serves a dual purpose:
        #   1. Resolved at execution time by _resolve_dashboard_content() from workflow_state.
        #   2. Dependency signal for the parallel phase scheduler — it tells the scheduler
        #      that Phase N (dashboard) depends on Phase M (data query), keeping them sequential.
        # Stripping it would break (2): the parallel executor would see no dependency and
        # run both phases concurrently, causing _resolve_dashboard_content to find an empty
        # workflow_state because the data phase hasn't finished yet.
        # Therefore: only clear _needs_refinement (stale validation flag); never strip content.
        for phase in self.executor.meta_plan:
            tools = phase.get("relevant_tools", [])
            if not tools or "TDA_Dashboard" not in tools:
                continue

            # Clear _needs_refinement unconditionally — content resolution is deterministic
            # and the tactical LLM doesn't need to touch it.
            if '_needs_refinement' in phase:
                app_logger.info(
                    f"PLAN REWRITE (Dashboard Cleanup): Cleared stale _needs_refinement flag "
                    f"from TDA_Dashboard phase {phase.get('phase')}."
                )
                del phase['_needs_refinement']

    # Words a pure "chart the last result" follow-up is made of: the visual vocabulary,
    # back-references to the previous result, and connective filler. Anything else names
    # content (an entity, a period, a grouping, a question) and makes it a new request.
    _CHART_FOLLOWUP_WORDS = frozenset("""
        chart charts graph graphs plot plots diagram diagrams visual visuals visualization
        visualisation visualize visualise visualizing visualising show display draw render make create generate give build put turn convert
        bar bars pie donut doughnut line lines column area scatter histogram heatmap stacked
        horizontal vertical table view version format style type kind
        it that this these those them data result results above previous same one
        a an the as into in of to for with and or me us please can could would you i
        instead now also again just
    """.split())

    @staticmethod
    def _chart_query_words(text: str) -> set:
        """Lower-cased words with a light singular fold (robberies->robbery, sales->sale)."""
        out = set()
        for w in re.findall(r"[a-z]+", (text or "").lower()):
            if len(w) > 4 and w.endswith("ies"):
                w = w[:-3] + "y"
            elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
            out.add(w)
        return out

    def _is_chart_only_query(self, query: str, previous_query: str = None) -> bool:
        """
        Detect if query is purely a charting request without new data requirements.

        Used to identify continuation queries that should reuse previous turn's data.

        Examples of chart-only queries:
        - "show me a bar chart"
        - "make a pie chart"
        - "visualize it"
        - "chart that data"
        - "plot the revenue" (after a revenue question — the word refers back)

        NOT chart-only (a new request that happens to say "show"):
        - "show me robberies per month" after "how many robberies are there?"
        - "show me tickets per month" after "which state has the most tickets?"

        Two conditions, both required. The original keyword rule is kept unchanged, and
        every content word must be chart vocabulary, a back-reference, or a word the
        previous question already used. The second condition only ever NARROWS the first:
        a query can lose the chart-only label, never gain it.

        Returns True if query requests visualization without specifying new data to fetch.
        """
        if not query:
            return False

        query_lower = query.lower()

        # Chart/visualization keywords
        chart_keywords = r'\b(chart|graph|plot|visualiz\w+|show|display)\b'

        # Data/entity keywords that indicate a NEW data request
        data_keywords = r'\b(customers?|products?|orders?|sales?|items?|users?|employees?|transactions?|invoices?|payments?|get|find|list|top|all|where|from|select)\b'

        has_chart = re.search(chart_keywords, query_lower)
        has_data_request = re.search(data_keywords, query_lower)

        # Chart keyword present but no new data request = chart-only query
        is_chart_only = bool(has_chart and not has_data_request)

        if is_chart_only:
            # A domain-agnostic check for new content: the keyword list above only knows a
            # retail vocabulary, so "show me robberies per month" used to pass it.
            new_words = (self._chart_query_words(query)
                         - self._chart_query_words(" ".join(self._CHART_FOLLOWUP_WORDS))
                         - self._chart_query_words(previous_query))
            if new_words:
                app_logger.debug(f"Not chart-only (new content {sorted(new_words)}): '{query}'")
                is_chart_only = False

        if is_chart_only:
            app_logger.debug(f"Detected chart-only query: '{query}'")

        return is_chart_only

    def _queries_are_semantically_similar(self, current_query: str, previous_query: str) -> bool:
        """
        Check if two user queries are semantically similar using lightweight heuristics.

        This is used to prevent inappropriate data reuse when query context changes
        (e.g., products query vs customers query).

        Returns True if queries appear to be about the same topic/entities.
        """
        if not current_query or not previous_query:
            return False

        # Normalize queries for comparison
        current_lower = current_query.lower()
        previous_lower = previous_query.lower()

        # Extract key entities (table names, domain terms)
        # Common patterns: "show me X", "get X", "list X", "chart X", etc.
        entity_patterns = [
            r'\b(products?|customers?|orders?|sales?|items?|users?|employees?|transactions?|invoices?|payments?)\b',
            r'\b(inventory|stock|warehouse|database|table|system)\b',
            r'\bfrom\s+(\w+)\b',  # SQL table references
        ]

        import re
        current_entities = set()
        previous_entities = set()

        for pattern in entity_patterns:
            current_entities.update(re.findall(pattern, current_lower))
            previous_entities.update(re.findall(pattern, previous_lower))

        # Special case: Detect chart-only continuation queries
        # These queries explicitly request visualization of previous data
        continuation_patterns = [
            r'\b(chart|graph|plot|visualiz\w+)\b.*\b(that|it|this|those|the data)\b',
            r'\b(show|display|create|generate|make)\b.*\b(chart|graph|plot|visualization)\b',
            r'\bchart\b(?!.*\b(for|of|about|showing)\b)',  # "chart" without a subject
        ]

        is_continuation = any(re.search(p, current_lower) for p in continuation_patterns)

        if is_continuation and previous_entities:
            # Current query is a chart-only request that refers to previous data implicitly
            app_logger.info(
                f"Query comparison: '{current_query}' detected as continuation query "
                f"for previous context with entities {previous_entities}"
            )
            return True

        # If no entities found, check for high word overlap as fallback
        if not current_entities and not previous_entities:
            current_words = set(current_lower.split())
            previous_words = set(previous_lower.split())
            # Remove common stop words
            stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                         'of', 'with', 'by', 'from', 'show', 'me', 'get', 'list', 'chart', 'display'}
            current_words -= stop_words
            previous_words -= stop_words

            if current_words and previous_words:
                overlap = len(current_words & previous_words) / max(len(current_words), len(previous_words))
                return overlap > 0.5  # 50% word overlap threshold
            return False

        # Check entity overlap
        if current_entities and previous_entities:
            # Both queries have entities - check for overlap
            overlap = len(current_entities & previous_entities)
            app_logger.debug(f"Query entity comparison: current={current_entities}, previous={previous_entities}, overlap={overlap}")
            return overlap > 0  # Any shared entity indicates similar context
        elif not current_entities and previous_entities:
            # Current has no entities but it's not a continuation query (already checked above)
            app_logger.debug(f"Query comparison: current query has no entities, not detected as continuation")
            return False
        elif current_entities and not previous_entities:
            # Previous has no entities - can't determine similarity
            app_logger.debug(f"Query comparison: previous query has no entities")
            return False

        # Both queries have no entities - word overlap fallback already handled above
        return False

    def _extract_tool_arguments(self) -> dict:
        """
        Extract tool arguments from previous turn's execution trace.

        Returns dict: {tool_name: arguments}
        """
        tool_args = {}

        workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
        if not workflow_history or not isinstance(workflow_history, list):
            return tool_args

        last_turn = workflow_history[-1]
        if not isinstance(last_turn, dict):
            return tool_args

        execution_trace = last_turn.get("execution_trace", [])
        for entry in execution_trace:
            if not isinstance(entry, dict):
                continue

            action = entry.get("action", {})
            if not isinstance(action, dict):
                continue

            tool_name = action.get("tool_name", "")
            arguments = action.get("arguments", {})

            # Handle orchestrated tools (TDA_SystemOrchestration)
            if tool_name == "TDA_SystemOrchestration":
                wrapped_calls = action.get("wrapped_tool_calls", [])
                for wrapped_call in wrapped_calls:
                    if isinstance(wrapped_call, dict):
                        wrapped_tool = wrapped_call.get("tool", "")
                        wrapped_args = wrapped_call.get("arguments", {})
                        if wrapped_tool and wrapped_args:
                            tool_args[wrapped_tool] = wrapped_args
            elif tool_name and arguments:
                tool_args[tool_name] = arguments

        return tool_args

    def _sql_queries_are_similar(self, current_sql: str, previous_sql: str) -> bool:
        """
        Compare two SQL queries to check if they access similar data.

        Returns True if queries access the same primary table(s).
        """
        if not current_sql or not previous_sql:
            return False

        import re

        # Extract table names from FROM clauses
        from_pattern = r'FROM\s+(\w+)'
        current_tables = set(re.findall(from_pattern, current_sql, re.IGNORECASE))
        previous_tables = set(re.findall(from_pattern, previous_sql, re.IGNORECASE))

        # Also check JOIN clauses
        join_pattern = r'JOIN\s+(\w+)'
        current_tables.update(re.findall(join_pattern, current_sql, re.IGNORECASE))
        previous_tables.update(re.findall(join_pattern, previous_sql, re.IGNORECASE))

        if not current_tables or not previous_tables:
            # If can't extract tables, be conservative
            app_logger.debug("Could not extract tables from SQL queries for comparison")
            return False

        # Normalize table names (lowercase for comparison)
        current_tables = {t.lower() for t in current_tables}
        previous_tables = {t.lower() for t in previous_tables}

        # Check for overlap
        overlap = current_tables & previous_tables

        app_logger.debug(
            f"SQL table comparison: current={current_tables}, previous={previous_tables}, overlap={overlap}"
        )

        # Return True if primary tables overlap
        return len(overlap) > 0

    def _validate_query_context_match(
        self,
        data_phases: list,
        plan_data_tools: set,
        skip_sql_check: bool = False
    ) -> bool:
        """
        Validate that the current query context matches the previous turn's context.

        This prevents inappropriate data reuse when the query topic changes
        (e.g., products → customers).

        Args:
            data_phases: The data-gathering phases from the current plan.
            plan_data_tools: Set of tool names used in those phases.
            skip_sql_check: When True, skip the SQL table comparison and rely only on
                user-query semantic similarity. Set this for TDA_Dashboard visualize
                follow-ups where the LLM often hallucinates table names it doesn't know.

        Returns True only if:
        1. User queries are semantically similar
        2. For SQL tools (unless skip_sql_check=True), the SQL queries access similar tables
        """
        # Extract user queries
        current_query = self.executor.original_user_input

        workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
        if not workflow_history or not isinstance(workflow_history, list):
            app_logger.info("Chart data reuse skipped: No previous workflow history available")
            return False

        last_turn = workflow_history[-1]
        if not isinstance(last_turn, dict):
            return False

        previous_query = last_turn.get("user_query", "")

        # Check 1: Query similarity
        if not self._queries_are_semantically_similar(current_query, previous_query):
            app_logger.info(
                f"Chart data reuse skipped: User queries are semantically different. "
                f"Current: '{current_query}', Previous: '{previous_query}'"
            )
            return False

        # Check 2: SQL argument similarity (for SQL-based tools)
        SQL_TOOLS = {"base_readQuery", "base_readQueries", "base_writeQuery"}
        sql_tools_in_plan = {base_name(t) for t in plan_data_tools} & SQL_TOOLS

        if sql_tools_in_plan and not skip_sql_check:
            # Extract previous tool arguments
            prev_tool_args = self._extract_tool_arguments()

            # Get current plan's SQL from data phases
            for phase in data_phases:
                tools = phase.get("relevant_tools", [])
                if not tools:
                    continue

                tool_name = tools[0]
                if base_name(tool_name) not in SQL_TOOLS:
                    continue

                # Get current SQL from phase arguments
                current_args = phase.get("arguments", {})
                current_sql = current_args.get("sql", "")

                # Get previous SQL
                prev_args = prev_tool_args.get(tool_name, {})
                previous_sql = prev_args.get("sql", "")

                # Compare SQL queries
                if current_sql and previous_sql:
                    if not self._sql_queries_are_similar(current_sql, previous_sql):
                        # Extract table names for logging
                        import re
                        current_tables = re.findall(r'FROM\s+(\w+)', current_sql, re.IGNORECASE)
                        previous_tables = re.findall(r'FROM\s+(\w+)', previous_sql, re.IGNORECASE)

                        app_logger.info(
                            f"Chart data reuse skipped: SQL queries access different tables. "
                            f"Current: {current_tables}, Previous: {previous_tables}"
                        )
                        return False
                elif not prev_args:
                    # Previous turn didn't use this tool (edge case)
                    app_logger.info(
                        f"Chart data reuse skipped: Tool {tool_name} not found in previous turn execution"
                    )
                    return False

        # All checks passed
        app_logger.info(
            f"Chart data reuse validation passed: Query context is similar. "
            f"Queries semantically similar, SQL tables match."
        )
        return True

    def _rewrite_plan_collapse_chart_data_refetch(self):
        """
        Collapse redundant data-gathering phases in chart-only follow-up plans.

        When Turn 2 is a chart request and Turn 1 already gathered the data,
        the strategic LLM often re-fetches redundantly (without date filters).
        This pass removes those phases, letting the charting bypass use Turn 1 data.

        Supports both TDA_Charting and TDA_Dashboard (action=visualize) as the
        visualization phase. For TDA_Dashboard, the previous turn's actual result
        rows are injected into workflow_state so the content placeholder resolves
        correctly without a data re-fetch.
        """
        if not self.executor.meta_plan or not self.executor.previous_turn_data:
            return

        # Utility tools — from plan_compiler (single source of truth).
        UTILITY_TOOLS = _UTILITY_TOOLS
        SKIP_TOOLS = {
            "TDA_SystemLog", "TDA_FinalReport", "TDA_ComplexPromptReport",
            "TDA_CurrentDate", "TDA_DateRange"
        }

        # 1. Find the visualization phase — TDA_Charting or TDA_Dashboard (action=visualize)
        charting_phase = None
        viz_tool = None
        for phase in self.executor.meta_plan:
            tools = phase.get("relevant_tools", [])
            if not tools:
                continue
            if "TDA_Charting" in tools:
                charting_phase = phase
                viz_tool = "TDA_Charting"
                break
            if "TDA_Dashboard" in tools:
                # Only treat as a viz phase when the action is 'visualize'.
                # Other actions (create, add_tile, refresh) are orchestration calls, not viz.
                if phase.get("arguments", {}).get("action") == "visualize":
                    charting_phase = phase
                    viz_tool = "TDA_Dashboard"
                    break
        if not charting_phase:
            return

        charting_phase_num = charting_phase.get("phase", 0)

        # 2. Find preceding data-gathering phases (non-utility tools)
        data_phases = []
        plan_data_tools = set()
        for phase in self.executor.meta_plan:
            if phase.get("phase", 0) >= charting_phase_num:
                continue
            tools = phase.get("relevant_tools", [])
            if tools and not all(t in UTILITY_TOOLS for t in tools):
                data_phases.append(phase)
                plan_data_tools.add(tools[0])

        if not data_phases:
            return  # Already a chart-only plan

        # 3. Check if the most recent previous turn has successful results from the SAME tools.
        #    Also capture the actual result data for workflow_state injection (TDA_Dashboard path).
        prev_data_tools = set()
        prev_results_by_tool: dict = {}  # tool_name -> full result dict
        workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
        if workflow_history and isinstance(workflow_history, list):
            last_turn = workflow_history[-1]
            if isinstance(last_turn, dict):
                for entry in last_turn.get("execution_trace", []):
                    if not isinstance(entry, dict):
                        continue
                    action = entry.get("action", {})
                    if not isinstance(action, dict):
                        continue
                    tool = action.get("tool_name", "")
                    if tool in SKIP_TOOLS:
                        continue
                    result = entry.get("result", {})
                    if (isinstance(result, dict) and
                        result.get("status") == "success" and
                        isinstance(result.get("results"), list) and
                        result["results"]):
                        prev_data_tools.add(tool)
                        prev_results_by_tool[tool] = result

        # 4. Only collapse if ALL plan data tools were already used in previous turn
        if not plan_data_tools.issubset(prev_data_tools):
            return

        # 4b. Validate query context similarity before collapsing.
        # For TDA_Dashboard visualize follow-ups the SQL table comparison is skipped:
        # the LLM routinely hallucinates wrong table names when reformatting an existing
        # visualisation (it only sees the user's chart-format request, not the schema).
        # The user-query semantic similarity check is sufficient for this case.
        skip_sql = (viz_tool == "TDA_Dashboard")
        if not self._validate_query_context_match(data_phases, plan_data_tools, skip_sql_check=skip_sql):
            return

        # 5. For TDA_Dashboard: seed workflow_state with the previous turn's actual result data
        # so that content placeholders (e.g. "result_of_phase_1") resolve without re-fetching.
        # TDA_Charting has its own cross-turn data lookup; TDA_Dashboard reads from workflow_state.
        if viz_tool == "TDA_Dashboard":
            for dp in data_phases:
                phase_num = dp.get("phase")
                tool_name = (dp.get("relevant_tools") or [None])[0]
                if tool_name and tool_name in prev_results_by_tool:
                    injection_key = f"result_of_phase_{phase_num}"
                    self.executor.workflow_state[injection_key] = [prev_results_by_tool[tool_name]]
                    app_logger.info(
                        f"PLAN REWRITE (Dashboard Data Reuse): Injected previous turn's "
                        f"'{tool_name}' result into workflow_state['{injection_key}'] for "
                        f"TDA_Dashboard content resolution."
                    )

        # 6. Collapse: remove data-gathering phases from the plan
        data_phase_nums = {p.get("phase") for p in data_phases}
        new_plan = [p for p in self.executor.meta_plan if p.get("phase") not in data_phase_nums]

        # 7. Renumber surviving phases sequentially AND rewrite cross-survivor references (C4).
        # The mapping contains ONLY survivors, so references to the removed data phases (whose
        # prior-turn results were injected above under `result_of_phase_<old>`) are deliberately
        # left unchanged — the viz/report phase still resolves them from the injected keys.
        survivor_mapping = {
            phase["phase"]: idx + 1
            for idx, phase in enumerate(new_plan)
            if isinstance(phase.get("phase"), int)
        }
        renumber_plan_refs(new_plan, survivor_mapping)

        self.executor.meta_plan = new_plan

        app_logger.info(
            f"PLAN REWRITE (Chart/Dashboard Data Reuse): Collapsed {len(data_phases)} redundant "
            f"data-gathering phase(s) for viz tool '{viz_tool}'. Tools {plan_data_tools} already "
            f"executed in previous turn with similar query context."
        )

        event_data = {
            "step": "Plan Optimization",
            "type": "plan_optimization",
            "details": {
                "summary": (
                    f"Visualization Data Reuse: Collapsed {len(data_phases)} redundant data-gathering "
                    f"phase(s) for {viz_tool}. Previous turn already gathered data via "
                    f"{', '.join(plan_data_tools)}. Visualization will use existing data."
                ),
                "correction_type": "chart_data_reuse",
                "viz_tool": viz_tool,
                "collapsed_phases": list(data_phase_nums)
            }
        }
        self.executor._log_system_event(event_data)
        yield self.executor._format_sse_with_depth(event_data)

    def _is_param_synonym(self, provided: str, expected: str) -> bool:
        """Check if provided parameter is likely a synonym of expected parameter."""
        # Exact substring match
        if provided in expected or expected in provided:
            return True

        # Common parameter name synonyms
        synonyms = {
            'date_string': 'start_date',
            'time_phrase': 'date_phrase',
            'query': 'sql',
            'text': 'content',
            'columns': 'dimensions',
            'rows': 'data',
            # Add more as discovered
        }

        # Check direct synonym mapping
        if synonyms.get(provided.lower()) == expected.lower():
            return True

        # Check reverse mapping
        if synonyms.get(expected.lower()) == provided.lower():
            return True

        return False

    def _ensure_final_report_phase(self):
        """
        Deterministically checks and adds a final reporting phase. It is context-aware
        and will not add a report phase to sub-processes where it is not required,
        preventing redundant plan modifications.
        """
        if not self.executor.meta_plan or self.executor.is_conversational_plan:
            return

        is_sub_process_without_summary = (
            self.executor.execution_depth > 0 and
            not self.executor.force_final_summary and
            not APP_CONFIG.SUB_PROMPT_FORCE_SUMMARY
        )
        if is_sub_process_without_summary:
            app_logger.info("Skipping final report check for non-summarizing sub-process.")
            return

        last_phase = self.executor.meta_plan[-1]

        # --- MODIFICATION START: Make final report check robust to LLM key variations ---
        # Check for the key 'relevant_tools' (list)
        last_phase_tools_list = last_phase.get("relevant_tools", [])
        # Also check for the key 'tool' (string), which the LLM used in the log
        last_phase_tool_str = last_phase.get("tool")

        is_already_finalized = (
            any(tool in ["TDA_FinalReport", "TDA_ComplexPromptReport"] for tool in last_phase_tools_list) or
            last_phase_tool_str in ["TDA_FinalReport", "TDA_ComplexPromptReport"]
        )
        # --- MODIFICATION END ---

        # --- MODIFICATION START: Check both relevant_tools AND executable_prompt for TDA_ContextReport ---
        # Knowledge repository queries use TDA_ContextReport for single-phase bypass.
        # The LLM sometimes misclassifies it as executable_prompt instead of relevant_tools.
        # We need to check both locations to properly detect synthesis plans.
        is_synthesis_plan = any(
            "TDA_ContextReport" in p.get("relevant_tools", []) or
            p.get("executable_prompt") == "TDA_ContextReport"
            for p in self.executor.meta_plan
        )
        # --- MODIFICATION END ---

        app_logger.debug(f"DEBUG: _ensure_final_report_phase - Current meta_plan: {self.executor.meta_plan}")
        app_logger.debug(f"DEBUG: _ensure_final_report_phase - Last phase: {last_phase}")
        app_logger.debug(f"DEBUG: _ensure_final_report_phase - Last phase tools list: {last_phase_tools_list}")
        app_logger.debug(f"DEBUG: _ensure_final_report_phase - Last phase tool string: {last_phase_tool_str}")
        app_logger.debug(f"DEBUG: _ensure_final_report_phase - is_already_finalized: {is_already_finalized}")
        app_logger.debug(f"DEBUG: _ensure_final_report_phase - is_synthesis_plan: {is_synthesis_plan}")

        if is_already_finalized or is_synthesis_plan:
            return

        app_logger.warning("PLAN CORRECTION: The generated plan is missing a final reporting step. System is adding it now.")

        reporting_tool_name = "TDA_ComplexPromptReport" if self.executor.source == 'prompt_library' else "TDA_FinalReport"

        new_phase_number = len(self.executor.meta_plan) + 1
        final_phase = {
            "phase": new_phase_number,
            "goal": "Generate the final report based on the data gathered.",
            "relevant_tools": [reporting_tool_name],
            "arguments": {}
        }
        self.executor.meta_plan.append(final_phase)

        event_data = {
            "step": "System Correction",
            "type": "workaround",
            "details": {
                "summary": "The agent's plan was missing a final reporting step. The system has automatically added it to ensure a complete response.",
                "correction": { "added_phase": final_phase }
            }
        }
        self.executor._log_system_event(event_data)
        yield self.executor._format_sse_with_depth(event_data)

    # ===========================================================================
    # PLAN NORMALIZATION METHODS - Template Syntax Canonicalization
    # ===========================================================================

    def _convert_to_canonical(self, value):
        """
        Converts a single argument value to canonical template format.

        Handles all LLM-generated template variations:
        - {{loop_item.key}} → {"source": "loop_item", "key": "key"} (as dict if standalone)
        - {{loop_item['key']}} → {"source": "loop_item", "key": "key"}
        - {loop_item[key]} → {"source": "loop_item", "key": "key"}
        - {loop_item.key} → {"source": "loop_item", "key": "key"}
        - {KeyName} → {"source": "loop_item", "key": "KeyName"}

        Args:
            value: Argument value (string, dict, or primitive)

        Returns:
            Normalized value with canonical template format (dict if pure template, string if embedded)
        """
        # Skip if not string or dict
        if not isinstance(value, (str, dict)):
            return value

        # Handle dict format (may already be canonical or legacy)
        if isinstance(value, dict):
            if value.get("source") == "loop_item" and "key" in value:
                return value  # Already canonical
            # Handle legacy {"result_of_phase_1": "key"} format
            for k, v in value.items():
                if re.match(r"result_of_phase_\d+|phase_\d+", k):
                    return {"source": k, "key": v}
            return value  # Not a template

        # Handle string format
        original_value = value

        # Check if this is a PURE template (entire value is just a template)
        # Pattern: exactly "{KeyName}" with nothing before or after
        pure_template_match = re.fullmatch(r'\{([A-Za-z][A-Za-z0-9_]*)\}', value)
        if pure_template_match:
            key = pure_template_match.group(1)
            # Only convert if it looks like a template variable
            if key[0].isupper() or key in ['TableName', 'ColumnName', 'DatabaseName', 'SchemaName']:
                app_logger.debug(f"Template normalized (pure): '{original_value}' → dict object")
                return {"source": "loop_item", "key": key}

        # Check for other pure template patterns
        pure_patterns = [
            (r'\{\{loop_item\.([A-Za-z0-9_]+)\}\}', 'loop_item'),
            (r'\{\{loop_item\[[\'\"]([A-Za-z0-9_]+)[\'\"]\]\}\}', 'loop_item'),
            (r'\{loop_item\[[\'\"]?([A-Za-z0-9_]+)[\'\"]?\]\}', 'loop_item'),
            (r'\{loop_item\.([A-Za-z0-9_]+)\}', 'loop_item'),
        ]

        for pattern, source in pure_patterns:
            match = re.fullmatch(pattern, value)
            if match:
                key = match.group(1)
                app_logger.debug(f"Template normalized (pure): '{original_value}' → dict object")
                return {"source": source, "key": key}

        # If we get here, it's an EMBEDDED template (template within other text)
        # For embedded templates, we keep them as strings but don't convert to JSON
        # The executor's embedded placeholder handler will resolve them
        # So we actually DON'T need to do anything for embedded templates in the new approach

        # Return unchanged - embedded templates will be left as-is for executor to handle
        return value

    def _normalize_arguments(self, args):
        """
        Recursively normalizes all arguments in a dict to canonical template format.

        Args:
            args: Dictionary of tool/prompt arguments

        Returns:
            Dictionary with normalized template references
        """
        if not isinstance(args, dict):
            return args

        normalized = {}

        for key, value in args.items():
            if isinstance(value, dict):
                # Recursively normalize nested dicts
                normalized[key] = self._normalize_arguments(value)
            elif isinstance(value, list):
                # Normalize each item in list
                normalized[key] = [
                    self._normalize_arguments(item) if isinstance(item, dict)
                    else self._convert_to_canonical(item)
                    for item in value
                ]
            else:
                # Convert individual values
                normalized[key] = self._convert_to_canonical(value)

        return normalized

    def _normalize_plan_syntax(self, plan):
        """
        Normalizes all template syntax in a plan to canonical format.

        This runs ONCE after plan generation, before validation and execution.
        Converts all LLM-generated template variations to a single canonical
        format, simplifying downstream execution logic.

        Args:
            plan: List of phase dictionaries from LLM

        Returns:
            Plan with all templates normalized to canonical format
        """
        if not isinstance(plan, list):
            return plan

        total_changes = 0

        for phase in plan:
            if not isinstance(phase, dict):
                continue

            # Normalize arguments if present
            if "arguments" in phase and isinstance(phase["arguments"], dict):
                original_args = copy.deepcopy(phase["arguments"])
                phase["arguments"] = self._normalize_arguments(phase["arguments"])

                if phase["arguments"] != original_args:
                    total_changes += 1
                    app_logger.debug(
                        f"Phase {phase.get('phase')} arguments normalized:\n"
                        f"  Before: {original_args}\n"
                        f"  After:  {phase['arguments']}"
                    )

        if total_changes > 0:
            app_logger.info(f"Plan normalization complete: {total_changes} phases updated")

        return plan

    def _inject_temporal_context(self, plan):
        """
        Injects TDA_CurrentDate phase at the beginning of plans for temporal queries.

        Detects temporal patterns in the original user input and ensures proper
        temporal context is established before data gathering.

        Args:
            plan: List of phase dictionaries from LLM

        Returns:
            Tuple of (plan, was_injected) where was_injected is True if temporal phase was added
        """
        if not isinstance(plan, list) or not plan:
            return plan, False

        # Detect temporal patterns in original user input. Uses the SAME single-sourced list as
        # _extract_temporal_phrase (§2 fix) — detection ⇔ extraction, so we never inject a
        # TDA_CurrentDate phase whose phrase can't later be extracted and wired.
        query_lower = self.executor.original_user_input.lower()
        is_temporal = any(re.search(pattern, query_lower) for pattern in _TEMPORAL_PATTERNS)

        if not is_temporal:
            return plan, False

        # Check if TDA_CurrentDate is already in the plan
        has_current_date = any(
            phase.get("relevant_tools") == ["TDA_CurrentDate"]
            for phase in plan if isinstance(phase, dict)
        )

        if has_current_date:
            return plan, False  # Already has temporal context

        # Inject TDA_CurrentDate as Phase 1
        app_logger.info(f"TEMPORAL PREPROCESSING: Injecting TDA_CurrentDate phase for temporal query")

        temporal_phase = {
            "phase": 1,
            "goal": "Establish current date as temporal context",
            "relevant_tools": ["TDA_CurrentDate"],
            "arguments": {}
        }

        # Re-number all existing phases AND their inter-phase references +1 (C4). Prepending
        # TDA_CurrentDate as phase 1 shifts every existing phase up by one; without rewriting
        # references, `result_of_phase_1` would resolve to the new date phase instead of the
        # (now phase-2) original first phase.
        shift_mapping = {
            p["phase"]: p["phase"] + 1
            for p in plan if isinstance(p, dict) and isinstance(p.get("phase"), int)
        }
        renumber_plan_refs(plan, shift_mapping)

        # Insert temporal phase at the beginning
        return [temporal_phase] + plan, True

    def _extract_temporal_phrase(self, query_lower: str):
        """
        Extracts the matched temporal phrase from the user query.
        Uses the SAME single-sourced `_TEMPORAL_PATTERNS` as _inject_temporal_context (§2 fix —
        no more drift between detection and extraction). Returns the phrase string or None.
        """
        for pattern in _TEMPORAL_PATTERNS:
            match = re.search(pattern, query_lower)
            if match:
                return match.group(1)
        return None

    # Tools that are NOT data-gathering — sourced from plan_compiler (single source of truth).
    _SYSTEM_TOOLS = _SYSTEM_TOOLS_CONST

    def _rewrite_plan_for_temporal_data_flow(self):
        """
        Deterministic rewrite: when TDA_CurrentDate is in the plan but its result
        is not wired to subsequent data-gathering phases, injects the temporal phrase
        as a date argument. This creates the trigger for the Date Range Orchestrator
        at execution time.

        Design principle: This is a deterministic fix (detectable pattern in code)
        rather than a prompt engineering change, avoiding system prompt convolution.
        """
        if not self.executor.meta_plan or len(self.executor.meta_plan) < 2:
            return

        # Find TDA_CurrentDate phase
        current_date_phase = None
        for phase in self.executor.meta_plan:
            if "TDA_CurrentDate" in phase.get("relevant_tools", []):
                current_date_phase = phase
                break

        if not current_date_phase:
            return

        # Extract temporal phrase from query
        query_lower = self.executor.original_user_input.lower()
        temporal_phrase = self._extract_temporal_phrase(query_lower)
        if not temporal_phrase:
            return

        made_change = False
        mcp_tools = self.executor.dependencies['STATE'].get('mcp_tools', {})

        for phase in self.executor.meta_plan:
            if phase is current_date_phase:
                continue

            tools = phase.get("relevant_tools", [])
            if not tools or tools[0] in self._SYSTEM_TOOLS:
                continue

            tool_name = tools[0]
            args = phase.get("arguments", {})

            # Skip if phase already has a date-related argument
            has_date_arg = any('date' in k.lower() for k in args.keys())
            if has_date_arg:
                continue

            # Check tool schema for date-related parameters
            tool_spec = mcp_tools.get(tool_name)
            if not tool_spec or not hasattr(tool_spec, 'args') or not isinstance(tool_spec.args, dict):
                continue

            date_param = None
            for param_name in tool_spec.args.keys():
                if 'date' in param_name.lower():
                    date_param = param_name
                    break

            if not date_param:
                continue  # Tool doesn't accept date parameter

            # Wire the temporal phrase into the data tool's arguments
            if "arguments" not in phase:
                phase["arguments"] = {}
            phase["arguments"][date_param] = temporal_phrase

            app_logger.info(
                f"PLAN REWRITE (Temporal Data Flow): Wired '{temporal_phrase}' "
                f"into phase {phase.get('phase')} argument '{date_param}' "
                f"for tool '{tool_name}'."
            )

            event_data = {
                "step": "System Correction",
                "type": "workaround",
                "details": {
                    "summary": (
                        f"The agent's plan established temporal context (TDA_CurrentDate) "
                        f"but did not wire the date into the data tool '{tool_name}'. "
                        f"The system has injected '{temporal_phrase}' as the '{date_param}' "
                        f"argument to enable automatic date range handling."
                    ),
                    "correction_type": "temporal_data_flow_wiring",
                    "injected_argument": date_param,
                    "injected_value": temporal_phrase,
                    "target_tool": tool_name,
                    "phase_number": phase.get("phase")
                }
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            made_change = True

        if made_change:
            app_logger.info(
                f"PLAN REWRITE (Temporal Data Flow): Final rewritten plan: "
                f"{self.executor.meta_plan}"
            )

    def _rewrite_llm_filter_deloop(self):
        """Convert any TDA_LLMFilter loop phase to a standalone filter phase.

        TDA_LLMFilter receives a full dataset and filters it with LLM reasoning.
        Placing it inside a loop causes redundant_argument_pruning to strip
        data_to_filter (its value matches the loop_over_key), so every iteration
        fails. The correct form is a single non-loop call with data_to_filter
        pointing to the upstream result key.
        """
        for phase in self.executor.meta_plan:
            if phase.get("type") != "loop":
                continue
            if "TDA_LLMFilter" not in phase.get("relevant_tools", []):
                continue

            loop_over = phase.pop("loop_over", None)
            phase.pop("type", None)

            args = phase.setdefault("arguments", {})
            if "data_to_filter" not in args and loop_over:
                args["data_to_filter"] = loop_over

            app_logger.info(
                "[llm_filter_deloop] Converted TDA_LLMFilter loop phase "
                "(loop_over=%r) to standalone filter phase.", loop_over
            )
        return
        yield  # makes this a sync generator so _pass() can iterate it

    def _rewrite_plan_for_text_dates(self):
        """Apply the preflight text-date rewrite to the plan's literal SQL before it runs.

        The chokepoint guard refuses a DATE deployed as characters that a statement
        compares as a string, orders bare or MIN/MAX's, and the correction chain then
        fixes it at 0 LLM — but only after the refusal has cost a phase attempt. Once the
        column's stored shape is known in this process, the fix is mechanical at plan
        time too, so it is applied here and the refusal never happens. Byte-identical
        when nothing is recorded for the session or no shape has been sampled; a phase
        whose SQL is a runtime reference (`result_of_phase_N`) or a SUBSTR bucket (the
        model's period to choose) is left alone. ONE rewrite serves all three seams
        (`preflight.textdates.rewrite_sql`).
        """
        from trusted_data_agent.agent.preflight import textdates as _td
        from trusted_data_agent.agent.preflight import guard as _guard
        # A rewrite is an ACT on the plan's SQL, so it follows the guard's posture:
        # only `enforce` rewrites; `observe` logs what it would have rewritten and
        # leaves the plan byte-identical; `off` does nothing.
        mode = _guard.resolve_text_date_mode()
        if mode == "off":
            return
        session_id = getattr(self.executor, "session_id", None)
        for phase in self.executor.meta_plan or []:
            if not isinstance(phase, dict):
                continue
            args = phase.get("arguments")
            sql = args.get("sql") if isinstance(args, dict) else None
            if not isinstance(sql, str) or not sql.strip() or "result_of_phase" in sql:
                continue
            fix = _td.rewrite_sql(session_id, sql)
            if not fix:
                continue
            if mode != "enforce":
                app_logger.info("[text_date_cast] OBSERVE: phase %s would rewrite %d text-date use(s) on %s: %s",
                                phase.get("phase"), len(fix.get("changes") or []), ", ".join(fix.get("columns") or []),
                                "; ".join(fix.get("changes") or []))
                continue
            args["sql"] = fix["sql"]
            app_logger.info("[text_date_cast] Phase %s: %s rewrote %d text-date use(s) to CAST … FORMAT before execution: %s",
                            phase.get("phase"), ", ".join(fix.get("columns") or []), len(fix.get("changes") or []),
                            "; ".join(fix.get("changes") or []))
        return
        yield  # makes this a sync generator so _pass() can iterate it

    # S3 — the arithmetic intents a synthesis phase's goal can carry, and the closed-set op
    # each maps to. The SQL aggregate that would already have produced it is named so a data
    # phase that aggregates on the database side is left alone (compute over rows only for
    # what the query did not produce).
    _COMPUTE_INTENTS = (
        (re.compile(r"\b(total|sum(?:\s+of)?|grand\s+total|add(?:\s+up|ing\s+up)?)\b", re.I), "sum", ("sum(",)),
        (re.compile(r"\b(average|avg|mean)\b", re.I), "avg", ("avg(", "average(")),
        (re.compile(r"\b(top|highest|largest|biggest)\s+(\d+|\w+)\b|\brank(?:ing|ed)?\b", re.I), "rank", ("order by", "top ", "limit ")),
        (re.compile(r"\b(percent(?:age)?\s+change|pct\s+change|change\s+(?:since|from|over)|growth\s+rate|trend)\b", re.I), "pct_change", ()),
        (re.compile(r"\b(minimum|lowest|smallest)\b", re.I), "min", ("min(",)),
        (re.compile(r"\b(maximum)\b", re.I), "max", ("max(",)),
    )
    _SYNTHESIS_TOOLS = ("TDA_FinalReport", "TDA_LLMTask", "TDA_ContextReport")

    # Placeholders the strategic planner writes into a SQL phase for a name it does not have yet.
    # Bracketed / angled / braced tokens and a bare `None` never resolve; a `result_of_phase_N`
    # resolves ONLY when phase N yields one scalar name (executor._resolve_arguments) — a search
    # or list tool yields a row set, and the statement then reads `FROM result_of_phase_1` and the
    # database refuses it. Both shapes cost a paid self-correction on the Optimize campaign
    # (16 % of saved plans, 2026-10-27, PASS-1).
    _PLACEHOLDER_RE = re.compile(r"\[[A-Za-z_][\w ]*\]|<[A-Za-z_][\w ]*>|\{[A-Za-z_]\w*\}|(?<![\w.])None(?![\w.])")
    _ROWSET_TOOLS = frozenset({"TDA_DatabaseNavigator", "base_tableList", "base_databaseList",
                               "base_columnDescription", "base_tablePreview", "base_tableDDL"})

    def _rewrite_plan_for_placeholder_sql(self):
        """PASS-1 — a SQL phase written against a placeholder is deferred, never run.

        The statement is handed to the tactical step exactly as `schema_before_sql` does (the
        draft kept in the goal for its intent), after an inventory phase when the plan reads
        none: the tactical step then writes it with the real database and table names in
        view — one small call instead of a syntax error and a paid self-correction. Pure on
        the plan; byte-identical when no phase carries a placeholder."""
        from trusted_data_agent.agent import schema_facts as _sf
        plan = self.executor.meta_plan
        if not isinstance(plan, list) or not plan:
            return
        by_num = {p.get("phase"): p for p in plan if isinstance(p, dict) and isinstance(p.get("phase"), int)}
        _state = (getattr(self.executor, "dependencies", None) or {}).get("STATE") or {}
        inventory_tool = _sf.tool_for("base_databaseList", list(_state.get("mcp_tools") or {})) \
            or _sf.tool_for("base_tableList", list(_state.get("mcp_tools") or {}))

        def _rowset_ref(sql):
            for m in re.finditer(r"result_of_phase_(\d+)", sql):
                src = by_num.get(int(m.group(1)))
                if not src or any(base_name(t) in self._ROWSET_TOOLS for t in ((src or {}).get("relevant_tools") or [])):
                    return m.group(0)
            return None

        converted, inserted = [], 0
        reads_inventory = any(base_name(t) in self._ROWSET_TOOLS
                              for p in plan if isinstance(p, dict) for t in (p.get("relevant_tools") or []))
        i = 0
        while i < len(plan):
            phase = plan[i]
            args = phase.get("arguments") if isinstance(phase, dict) else None
            sql = args.get("sql") if isinstance(args, dict) else None
            if not isinstance(sql, str) or not sql.strip() or phase.get("_inserted_by"):
                i += 1
                continue
            hit = (self._PLACEHOLDER_RE.search(sql) or _rowset_ref(sql))
            token = hit.group(0) if hasattr(hit, "group") else hit
            if not token:
                i += 1
                continue
            if not reads_inventory and inventory_tool and not inserted:
                first_num = phase.get("phase") if isinstance(phase.get("phase"), int) else i + 1
                shift = {p["phase"]: p["phase"] + 1 for p in plan
                         if isinstance(p, dict) and isinstance(p.get("phase"), int) and p["phase"] >= first_num}
                renumber_plan_refs(plan, shift)
                plan.insert(i, {"phase": first_num, "goal": "List the databases and tables available, so the "
                                                            "query that follows names real ones.",
                                "relevant_tools": [inventory_tool], "arguments": {},
                                "_inserted_by": "placeholder_sql"})
                inserted += 1
                i += 1
                reads_inventory = True
            self._defer_sql_phase(phase, args, sql, f"the draft named a placeholder ({token}) that no database holds")
            converted.append(phase.get("phase"))
            i += 1
        if not converted:
            return
        app_logger.info("[placeholder_sql] SQL of phase(s) %s named a placeholder; left to the tactical step (%d inventory phase inserted)",
                        converted, inserted)
        ev = {"step": "System Correction", "type": "workaround",
              "details": {"pass": "placeholder_sql", "summary":
                          (f"The plan's SQL named a placeholder for a database or table it did not have yet. "
                           f"Phase(s) {', '.join(str(c) for c in converted)} will be written after the real names "
                           f"are read{' (an inventory phase inserted)' if inserted else ''} (0 LLM for this rewrite).")}}
        self.executor._log_system_event(ev)
        yield self.executor._format_sse_with_depth(ev)
        return
        yield  # a sync generator for _pass()

    def _defer_sql_phase(self, phase, args, sql, why):
        """Hand a SQL phase to the tactical step: the statement leaves the arguments, the draft
        stays on the phase and in the goal for its intent. Shared by the placeholder and the
        database passes (and the same shape `schema_before_sql` produces)."""
        phase["arguments"] = {k: v for k, v in args.items() if k != "sql"}
        phase["_sql_draft"] = sql
        phase["goal"] = (f"{phase.get('goal') or 'Run the query.'} Write the SQL using ONLY the real database "
                         f"and table names read by the phase(s) before this one — {why}; keep the draft "
                         f"for its intent only: {sql}")

    def _rewrite_plan_for_database_before_sql(self):
        """PASS-3 — a SQL phase naming a database the turn has no reason to believe in is deferred.

        Two readings, both deterministic. With a session CATALOG (the Database Navigator or a
        source dictionary listed every database), a statement naming a database that is not in
        it is wrong before it runs (`preflight.catalog.nonexistent_db_refs`). Without one — the
        cold session — the plan's own shape decides: a plan that SEARCHES an inventory (the
        navigator, databaseList, tableList) and then writes SQL against a database name it
        invented ("DEMO_Customer360_db", "sales_db" — Optimize campaign, 2026-10-27) is deferred
        so the tactical step writes it with the search's result in view; a direct statement
        against a name with no search before it (E1: `fitness_db.Customers`) is left alone, as is
        any plan a champion guided. 0 LLM for the rewrite."""
        plan = self.executor.meta_plan
        if not isinstance(plan, list) or not plan:
            return
        guided = getattr(self, "_champion_guided", False) or getattr(self.executor, "rag_case_count", None)
        # A champion-GUIDED plan has a third reading: the champions' own databases are the ones
        # this turn has a reason to believe in. A guided plan whose SQL names a database NO
        # retrieved champion names (and no catalog lists) is deferred — the model's prior
        # surfacing past a proven example ("DEMO_Customer360_db" against three champions that
        # all read fitness_db: Optimize campaign run 4). Unknown champion databases → stand down.
        champion_dbs = getattr(self, "_champion_databases", None) if guided else None
        if guided and not champion_dbs:
            return
        session_id = getattr(self.executor, "session_id", None)
        try:
            from trusted_data_agent.agent.preflight import catalog as _cat
            has_catalog = bool(session_id) and _cat.has_catalog(session_id)
        except Exception:  # noqa: BLE001
            _cat, has_catalog = None, False
        converted = []
        searched = False
        for phase in plan:
            if not isinstance(phase, dict):
                continue
            args = phase.get("arguments") if isinstance(phase.get("arguments"), dict) else {}
            sql = args.get("sql")
            if not (isinstance(sql, str) and sql.strip()) or phase.get("_inserted_by") or "_sql_draft" in phase:
                searched = searched or any(base_name(t) in self._ROWSET_TOOLS for t in (phase.get("relevant_tools") or []))
                continue
            if has_catalog and _cat is not None:
                bad = _cat.nonexistent_db_refs(session_id, sql)
                if bad:
                    self._defer_sql_phase(phase, args, sql, f"the draft named a database this source does not have ({', '.join(bad)})")
                    converted.append(phase.get("phase"))
                    continue
            elif champion_dbs:
                foreign = sorted({db for db, _t in _sf_sql_tables(sql) if db and db.lower() not in champion_dbs
                                  and not db.lower().startswith("result_of_phase")})
                if foreign:
                    self._defer_sql_phase(phase, args, sql,
                                          f"the draft named a database ({', '.join(foreign)}) that none of the "
                                          f"retrieved proven examples uses ({', '.join(sorted(champion_dbs))})")
                    converted.append(phase.get("phase"))
                    continue
            elif searched:
                dbs = [db for db, _t in _sf_sql_tables(sql) if db]
                if dbs:
                    self._defer_sql_phase(phase, args, sql,
                                          f"the draft named a database ({', '.join(sorted(set(dbs)))}) before the search before it had answered")
                    converted.append(phase.get("phase"))
                    continue
        if not converted:
            return
        app_logger.info("[database_before_sql] SQL of phase(s) %s named an unknown database; left to the tactical step", converted)
        ev = {"step": "System Correction", "type": "workaround",
              "details": {"pass": "database_before_sql", "summary":
                          (f"The plan's SQL named a database the turn has no reason to believe in. Phase(s) "
                           f"{', '.join(str(c) for c in converted)} will be written after the real names are read "
                           f"(0 LLM for this rewrite).")}}
        self.executor._log_system_event(ev)
        yield self.executor._format_sse_with_depth(ev)
        return
        yield  # a sync generator for _pass()

    def _rewrite_plan_for_schema_before_sql(self):
        """Stage 3.0 of the multi-source plan — schema before SQL. The strategic plan writes a
        data phase's SQL before anything has run; with no knowledge graph and no learned plan
        its table and column names are guesses, and the database refuses them (the stage-0
        bench: Optimize answered 0 of 39). For each raw-SQL phase that reads a table the turn has
        no column list for, a column-description phase is inserted before it and the statement
        is handed to the tactical step, which writes it with the real columns in view (the draft
        is kept in the goal as the intent). Deterministic; never when the plan was guided by a
        champion case, the agent has no describe tool, or every table's columns are already known
        (knowledge graph or an earlier describe in the session) — so an agent with a knowledge
        graph plans exactly as before. At most ``schema_facts.MAX_TABLES`` describes per plan."""
        from trusted_data_agent.agent import schema_facts as _sf
        plan = self.executor.meta_plan
        if not isinstance(plan, list) or not plan:
            return
        # A champion case vouches for the tables ITS SQL reads — those are never described again.
        # A plan guided by champions whose tables are not known stands down entirely.
        champion_tables = getattr(self, "_champion_tables", None)
        guided = getattr(self, "_champion_guided", False) or getattr(self.executor, "rag_case_count", None)
        if guided and champion_tables is None:
            return
        champion_tables = champion_tables or set()
        _state = (getattr(self.executor, "dependencies", None) or {}).get("STATE") or {}
        describe = _sf.tool_for(_sf.COLUMN_TOOL, list(_state.get("mcp_tools") or {}))
        if not describe:
            return
        session_id = getattr(self.executor, "session_id", None)
        graph_cols = _sf.kg_table_columns(self.executor)
        graph_leaves = {k.rpartition(".")[2].lower() for k in graph_cols}
        described = set()      # (db, leaf) a phase of the plan already describes
        for p in plan:
            if isinstance(p, dict) and any(base_name(t) == _sf.COLUMN_TOOL for t in (p.get("relevant_tools") or [])):
                a = p.get("arguments") or {}
                _t = a.get("table_name") or a.get("object_name") or a.get("obj_name")
                if isinstance(_t, str) and isinstance(a.get("database_name"), str):
                    described.add((a["database_name"].lower(), _t.lower()))
        inserted = 0
        converted = []
        i = 0
        while i < len(plan):
            phase = plan[i]
            args = phase.get("arguments") if isinstance(phase, dict) else None
            sql = args.get("sql") if isinstance(args, dict) else None
            if (not isinstance(sql, str) or not sql.strip() or "result_of_phase" in sql
                    or phase.get("_inserted_by") == "schema_before_sql"):
                i += 1
                continue
            default_db = args.get("database_name") if isinstance(args.get("database_name"), str) else None
            unknown = []
            for db, table in _sf.sql_tables(sql):
                db = db or default_db
                leaf = table.lower()
                if (not db or leaf in graph_leaves or leaf in champion_tables
                        or _sf.known_columns(session_id, table)):
                    continue
                if (db, table) not in unknown:
                    unknown.append((db, table))
            if not unknown:
                i += 1
                continue
            new = [(db, t) for db, t in unknown if (db.lower(), t.lower()) not in described]
            new = new[:max(0, _sf.MAX_TABLES - inserted)]
            if new:
                first_num = phase.get("phase") if isinstance(phase.get("phase"), int) else i + 1
                shift = {p["phase"]: p["phase"] + len(new) for p in plan
                         if isinstance(p, dict) and isinstance(p.get("phase"), int) and p["phase"] >= first_num}
                renumber_plan_refs(plan, shift)
                for k, (db, t) in enumerate(new):
                    plan.insert(i + k, {
                        "phase": first_num + k,
                        "goal": f"Read the real columns of {db}.{t} before querying it.",
                        "relevant_tools": [describe],
                        "arguments": {"database_name": db, "table_name": t},
                        "_inserted_by": "schema_before_sql",
                    })
                    described.add((db.lower(), t.lower()))
                inserted += len(new)
                i += len(new)
            # the statement is written by the tactical step, after the columns are read
            phase["arguments"] = {k: v for k, v in args.items() if k != "sql"}
            phase["_sql_draft"] = sql
            phase["goal"] = (f"{phase.get('goal') or 'Run the query.'} Write the SQL using ONLY the real "
                             f"tables and columns read by the phase(s) before this one; a draft written "
                             f"before they were known, for its intent only: {sql}")
            converted.append(phase.get("phase"))
            i += 1
        if not converted:
            return
        app_logger.info("[schema_before_sql] %d describe phase(s) inserted; SQL of phase(s) %s left to the tactical step",
                        inserted, converted)
        ev = {"step": "System Correction", "type": "workaround",
              "details": {"pass": "schema_before_sql", "summary":
                          (f"The plan's SQL named tables whose columns were not known. Inserted {inserted} "
                           f"column-description phase(s); phase(s) {', '.join(str(c) for c in converted)} will be "
                           f"written after the real columns are read (0 LLM for this rewrite).")}}
        self.executor._log_system_event(ev)
        yield self.executor._format_sse_with_depth(ev)
        return
        yield  # a sync generator for _pass()

    def _rewrite_plan_for_compute_insertion(self):
        """S3 — Compute with Lineage, the planner wiring. A synthesis phase whose goal is
        arithmetic over the rows of the data phase before it ("the total…", "the average…",
        "top 5 by…", "the change since…") gets a `TDA_Compute` phase inserted deterministically
        in front of it, so the report phase receives a computed, receipted figure rather than
        raw rows it would sum itself. Never when: the switch is off or the component is not
        enabled for the profile; a phase already computes; the data phase's own SQL already
        produces the aggregate (the database did the arithmetic); no data phase precedes the
        synthesis. Inserts at most ONE phase per plan and is idempotent on replan. The
        `column` is left to the component (one numeric column resolves itself; several are
        named back to the agent — the planner never guesses between two measures)."""
        from trusted_data_agent.agent import result_sets as _rs
        if not _rs.enabled():
            return
        plan = self.executor.meta_plan
        if not isinstance(plan, list) or len(plan) < 2:
            return
        try:
            from trusted_data_agent.components.manager import get_component_manager
            _pcfg = self.executor._get_profile_config() if hasattr(self.executor, "_get_profile_config") else {}
            _active = get_component_manager().get_active_components(_pcfg or {}, getattr(self.executor, "user_uuid", None))
            if "compute" not in {getattr(c, "component_id", c) for c in _active}:
                return
        except Exception:  # noqa: BLE001 — the component's own gate decides; the pass stays silent
            return
        def _tools(p):
            t = p.get("relevant_tools") if isinstance(p, dict) else None
            return [x for x in (t or []) if isinstance(x, str)]
        if any("TDA_Compute" in _tools(p) for p in plan):
            return
        for i, phase in enumerate(plan):
            if not isinstance(phase, dict) or i == 0:
                continue
            tools = _tools(phase)
            if not tools or not any(t in self._SYNTHESIS_TOOLS for t in tools):
                continue
            goal = str(phase.get("goal") or "")
            op = None; sql_hints = ()
            for rx, cand, hints in self._COMPUTE_INTENTS:
                if rx.search(goal):
                    op, sql_hints = cand, hints
                    break
            if not op:
                continue
            prev = plan[i - 1]
            if not isinstance(prev, dict):
                continue
            ptools = _tools(prev)
            if not ptools or any(t.startswith("TDA_") for t in ptools):
                continue   # only a DATA phase's rows are worth computing over
            pargs = prev.get("arguments") if isinstance(prev.get("arguments"), dict) else {}
            psql = str(pargs.get("sql") or "").lower()
            if psql and any(h in psql for h in sql_hints):
                continue   # the database already produced this aggregate
            new_num = phase.get("phase") if isinstance(phase.get("phase"), int) else i + 1
            shift = {p["phase"]: p["phase"] + 1 for p in plan
                     if isinstance(p, dict) and isinstance(p.get("phase"), int) and p["phase"] >= new_num}
            renumber_plan_refs(plan, shift)
            compute_phase = {
                "phase": new_num,
                "goal": f"Compute the {op} the report needs over the rows of phase {new_num - 1} (exact, with a receipt).",
                "relevant_tools": ["TDA_Compute"],
                "arguments": {"op": op},
                "_inserted_by": "compute_insertion",
            }
            plan.insert(i, compute_phase)
            app_logger.info("[compute_insertion] inserted TDA_Compute(op=%s) as phase %s before the synthesis phase %s",
                            op, new_num, new_num + 1)
            ev = {"step": "System Correction", "type": "workaround",
                  "details": {"pass": "compute_insertion", "summary":
                              f"Inserted a deterministic compute phase ({op}) before phase {new_num + 1} so the "
                              f"report receives a receipted figure instead of doing the arithmetic itself."}}
            self.executor._log_system_event(ev)
            yield self.executor._format_sse_with_depth(ev)
            return
        return
        yield  # a sync generator for _pass()

    def _rewrite_plan_for_cross_source_shape(self):
        """Stage 3b of the multi-source plan — the cross-source plan shape, decided by code.
        A raw-SQL phase whose statement reads databases held by DIFFERENT servers of this agent
        runs nowhere (routing refuses it); an LLM self-correction can return one action, so it
        swapped in a one-server query and the report stated a confident wrong number for the
        whole (the 3a bench). Now the statement is split deterministically (`cross_source`): one
        fetch phase per server (its columns, its own filters, its own tool) and the ORIGINAL
        statement run over their rows by the combine step (`TDA_Compute` op sql, `p<N>` per
        fetch) — isolation, completeness and the receipt as for any combine; 0 LLM rounds. A
        shape the splitter declines is withdrawn to the tactical step with the split named (the
        3.0 mechanism) rather than left to be refused at run time. Never for a single-server
        agent, a phase whose SQL is a runtime reference, or with the combine step off."""
        from trusted_data_agent.agent import source_routing as _srt
        from trusted_data_agent.agent import cross_source as _cs
        from trusted_data_agent.core.tool_identity import parse as _parse_tool
        plan = self.executor.meta_plan
        if not isinstance(plan, list) or not plan or not _srt.active():
            return
        try:
            from trusted_data_agent.components.manager import ComponentManager, get_component_manager
            if not ComponentManager._instruction_gate_open("compute_sql"):
                return
            _pcfg = self.executor._get_profile_config() if hasattr(self.executor, "_get_profile_config") else {}
            _active = get_component_manager().get_active_components(_pcfg or {}, getattr(self.executor, "user_uuid", None))
            if "compute" not in {getattr(c, "component_id", c) for c in _active}:
                return
        except Exception:  # noqa: BLE001 — the component's own gate decides; the pass stays silent
            return
        user = _srt.turn_user()
        servers = {str(s.server_id): s for s in _srt.turn_servers()}

        def holder(db):
            return _srt.holder_of(user, db, list(servers))

        columns = _srt.turn_catalog_columns()
        i = 0
        while i < len(plan):
            phase = plan[i]
            args = phase.get("arguments") if isinstance(phase, dict) else None
            sql = args.get("sql") if isinstance(args, dict) else None
            tools = [t for t in (phase.get("relevant_tools") or []) if isinstance(t, str)] if isinstance(phase, dict) else []
            if (not isinstance(sql, str) or not sql.strip() or not tools or tools[0].startswith("TDA_")
                    or not self._sql_arg_is_literal(phase) or phase.get("_inserted_by") == "cross_source_shape"):
                i += 1
                continue
            dbs = _srt.referenced_databases(args)
            held = {holder(d) for d in dbs}
            num = phase.get("phase") if isinstance(phase.get("phase"), int) else i + 1
            reads_phase = _cs.references_phase_result(sql)
            if None in held or (not reads_phase and (len(dbs) < 2 or len(held) < 2)):
                i += 1
                continue
            where = "; ".join(f"{d} is on server '{_srt.label_of(holder(d))}'" for d in dbs)
            if reads_phase:
                where = (where + "; " if where else "") + "the statement reads an earlier phase's rows as a table"
            split = _cs.split_statement(sql, holder, columns)
            if split is not None and any(n >= num for n in split.phase_refs):
                split = None                          # a forward reference: not a result yet
            if split is None:
                reason = _cs.why_declined(sql, holder, columns) or "unsupported shape"
                phase["arguments"] = {k: v for k, v in args.items() if k != "sql"}
                phase["_sql_draft"] = sql
                phase["goal"] = (f"{phase.get('goal') or 'Run the query.'} This reads databases held by different "
                                 f"MCP servers ({where}) — no single statement can run it. Fetch each server's rows "
                                 f"with that server's own tool in separate phases, then combine them with TDA_Compute "
                                 f"(op sql, one table p<N> per fetch phase). A draft written before this was known, "
                                 f"for its intent only: {sql}")
                app_logger.info("[cross_source_shape] phase %s spans servers; not split (%s) — handed to the tactical step", num, reason)
                ev = {"step": "System Correction", "type": "workaround",
                      "details": {"pass": "cross_source_shape", "summary":
                                  (f"Phase {num} reads databases on different MCP servers ({where}); the statement "
                                   f"could not be split by code ({reason}) — handed to the tactical step with the "
                                   f"split named.")}}
                self.executor._log_system_event(ev)
                yield self.executor._format_sse_with_depth(ev)
                i += 1
                continue
            k = len(split.fetches)
            base = _parse_tool(tools[0]).name
            shift = {p["phase"]: p["phase"] + k for p in plan
                     if isinstance(p, dict) and isinstance(p.get("phase"), int) and p["phase"] >= num}
            renumber_plan_refs(plan, shift)          # this phase becomes num+k, and references follow it
            for j, f in enumerate(split.fetches):
                srv = servers.get(f.server_id)
                plan.insert(i + j, {
                    "phase": num + j,
                    "goal": (f"Fetch from server '{_srt.label_of(f.server_id)}' the columns of {f.database}.{f.table} "
                             f"that the statement of phase {num + k} uses (its own filters applied) — combined there."),
                    "relevant_tools": [srv.name_for(base) if srv else tools[0]],
                    "arguments": {"sql": f.sql},
                    "_inserted_by": "cross_source_shape",
                    "_server": f.server_id,
                })
            phase["relevant_tools"] = ["TDA_Compute"]
            phase["arguments"] = {"op": "sql", "sql": split.combine_sql([f"p{num + j}" for j in range(k)])}
            phase.pop("_sql_draft", None)
            _over = (f"phases {num}-{num + k - 1} (different MCP servers)" if k > 1 else
                     f"phase {num}" if k == 1 else "earlier phases")
            _over += (" and the rows of phase(s) " + ", ".join(str(n) for n in split.phase_refs)) if split.phase_refs else ""
            phase["goal"] = (f"Combine the rows fetched by {_over} with the statement's own logic "
                             f"(join, aggregation, ordering) — {phase.get('goal') or ''}").rstrip(" —")
            phase["_inserted_by"] = "cross_source_shape"
            app_logger.info("[cross_source_shape] phase %s split into %d fetch phase(s) + a combine (phase %s)", num, k, num + k)
            ev = {"step": "System Correction", "type": "workaround",
                  "details": {"pass": "cross_source_shape", "summary":
                              (f"Phase {num} {'read databases on different MCP servers' if k > 1 else 'read an earlier phase result as a table'}"
                               f" ({_cs.describe(split, _srt.label_of) or where}); "
                               f"split into {k} single-server fetch phase(s)"
                               + (f" ({num}-{num + k - 1})" if k else "")
                               + f" and a combine phase ({num + k}) running the original statement over their rows (0 LLM)."),
                              "fetches": [f.sql for f in split.fetches], "combine": phase["arguments"]["sql"]}}
            self.executor._log_system_event(ev)
            yield self.executor._format_sse_with_depth(ev)
            i += k + 1
        return
        yield  # a sync generator for _pass()

    async def _rewrite_plan_for_multi_loop_synthesis(self):
        """
        Surgically corrects plans where multiple, parallel loops feed into a
        final summary. It inserts a new, intermediate distillation phase to
        transform the raw data into high-level insights before the final
        summary is generated.
        """
        if not self.executor.meta_plan or len(self.executor.meta_plan) < 3:
            return

        made_change = False
        i = 0
        while i < len(self.executor.meta_plan) - 1:
            if (self.executor.meta_plan[i].get("type") == "loop" and
                self.executor.meta_plan[i+1].get("type") == "loop"):

                loop_block_start_index = i
                loop_block = [self.executor.meta_plan[i]]
                base_loop_source = self.executor.meta_plan[i].get("loop_over")

                if not base_loop_source:
                    i += 1
                    continue

                j = i + 1
                while j < len(self.executor.meta_plan) and self.executor.meta_plan[j].get("type") == "loop" and self.executor.meta_plan[j].get("loop_over") == base_loop_source:
                    loop_block.append(self.executor.meta_plan[j])
                    j += 1

                if len(loop_block) >= 2 and j < len(self.executor.meta_plan):
                    final_phase = self.executor.meta_plan[j]
                    is_final_summary = final_phase.get("relevant_tools") == ["TDA_LLMTask"]

                    if is_final_summary:
                        app_logger.warning(
                            "PLAN REWRITE: Detected inefficient multi-loop plan. "
                            "Injecting an intermediate distillation phase."
                        )
                        original_plan_snippet = copy.deepcopy(self.executor.meta_plan[loop_block_start_index : j+1])

                        synthesis_phase_num = j + 1
                        source_data_keys = [f"result_of_phase_{p['phase']}" for p in loop_block]

                        synthesis_task = {
                            "phase": synthesis_phase_num,
                            "goal": f"Distill the raw data from phases {loop_block[0]['phase']}-{loop_block[-1]['phase']} into a concise, per-item summary.",
                            "relevant_tools": ["TDA_LLMTask"],
                            "arguments": {
                                "task_description": (
                                    "Analyze the voluminous raw data from the previous loops. Your task is to distill this information. "
                                    "For each item (e.g., table) processed, produce a concise, one-paragraph summary of the most critical findings. "
                                    "Your output MUST be a clean list of these summary objects, each containing the item's name and the summary text."
                                ),
                                "source_data": source_data_keys
                            }
                        }

                        # C4: shift every phase whose number is >= synthesis_phase_num up by 1
                        # AND rewrite references to them, BEFORE inserting the distillation phase
                        # into the freed slot. (The old code only bumped the `phase` field.)
                        shift_mapping = {
                            p["phase"]: p["phase"] + 1
                            for p in self.executor.meta_plan
                            if isinstance(p, dict) and isinstance(p.get("phase"), int)
                            and p["phase"] >= synthesis_phase_num
                        }
                        renumber_plan_refs(self.executor.meta_plan, shift_mapping)

                        self.executor.meta_plan.insert(j, synthesis_task)

                        final_summary_phase = self.executor.meta_plan[j+1]
                        new_source_key = f"result_of_phase_{synthesis_phase_num}"
                        final_summary_phase["arguments"]["source_data"] = [new_source_key]

                        made_change = True

                        event_data = {
                            "step": "System Correction",
                            "type": "workaround",
                            "details": {
                                "summary": "The agent's plan was inefficient. The system has automatically rewritten it to include a data distillation step, improving the quality and reliability of the final report.",
                                "correction": {
                                    "from": original_plan_snippet,
                                    "to": copy.deepcopy(self.executor.meta_plan[loop_block_start_index : j+2])
                                }
                            }
                        }
                        self.executor._log_system_event(event_data)
                        yield self.executor._format_sse_with_depth(event_data)

                        i = j + 1
                        continue
            i += 1

        if made_change:
            app_logger.info(f"PLAN REWRITE (Multi-Loop): Final rewritten plan: {self.executor.meta_plan}")

    async def _rewrite_plan_for_corellmtask_loops(self):
        """
        Surgically corrects plans where the LLM has incorrectly placed a
        `TDA_LLMTask` inside a loop for an aggregation-style task. It transforms
        the loop into a standard, single-execution phase. This now uses a
        classifier LLM call to determine if the task is appropriate for this optimization.
        """
        if not self.executor.meta_plan:
            return

        made_change = False
        for phase in self.executor.meta_plan:
            is_inefficient_loop = (
                phase.get("type") == "loop" and
                phase.get("relevant_tools") == ["TDA_LLMTask"]
            )

            if is_inefficient_loop:
                task_description = phase.get("arguments", {}).get("task_description", "")

                if not task_description:
                    task_type = "aggregation"
                    app_logger.warning("TDA_LLMTask loop has no task_description. Defaulting to 'aggregation' for rewrite.")
                else:
                    # Use profile-aware prompt resolution
                    classification_prompt_content = self.executor.prompt_resolver.get_task_classification_prompt()
                    if not classification_prompt_content:
                        app_logger.error("Failed to resolve TASK_CLASSIFICATION_PROMPT from profile mapping")
                        task_type = "synthesis"
                    else:
                        classification_prompt = classification_prompt_content.format(task_description=task_description)
                        reason = "Classifying TDA_LLMTask loop intent for optimization."

                        call_id = str(uuid.uuid4())
                        event_data = {"step": "Analyzing Plan Efficiency", "type": "plan_optimization", "details": {"summary": "Checking if an iterative task can be optimized into a single batch operation.", "call_id": call_id}}
                        self.executor._log_system_event(event_data)
                        yield self.executor._format_sse_with_depth(event_data)
                        yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

                        response_text, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
                            prompt=classification_prompt,
                            reason=reason,
                            system_prompt_override="You are a JSON-only responding assistant.",
                            raise_on_error=False,
                            disabled_history=True,
                            source=self.executor.source
                        )

                        # Log plan optimization LLM call with tokens + cost for history
                        opt_log_event = {
                            "step": "Analyzing Plan Efficiency",
                            "type": "system_message",
                            "details": {
                                "summary": "Checking if an iterative task can be optimized into a single batch operation.",
                                "call_id": call_id,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)
                            }
                        }
                        self.executor._log_system_event(opt_log_event)

                        # --- MODIFICATION START: Pass user_uuid to get_session ---
                        updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
                        # --- MODIFICATION END ---
                        if updated_session:
                            yield self.executor._format_sse_with_depth({ "statement_input": input_tokens, "statement_output": output_tokens, "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens, "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0), "call_id": call_id, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0) }, "token_update")

                        yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

                        classification_data = robust_json_parse(response_text)
                        if isinstance(classification_data, dict):
                            task_type = classification_data.get("classification", "synthesis")
                        else:
                            app_logger.warning(f"Failed to parse task classification. Defaulting to 'synthesis'. Response: {response_text}")
                            task_type = "synthesis"

                if task_type == "aggregation":
                    app_logger.warning(
                        f"PLAN REWRITE: Detected inefficient AGGREGATION TDA_LLMTask loop in phase {phase.get('phase')}. "
                        "Transforming to a standard phase."
                    )

                    original_phase = copy.deepcopy(phase)

                    phase.pop("type", None)
                    loop_source = phase.pop("loop_over", None)

                    if "arguments" not in phase: phase["arguments"] = {}
                    if "source_data" not in phase["arguments"] and loop_source:
                        phase["arguments"]["source_data"] = [loop_source]
                    if "task_description" not in phase["arguments"]:
                         phase["arguments"]["task_description"] = phase.get("goal", "Perform the required task.")

                    event_data = {
                        "step": "System Correction",
                        "type": "workaround",
                        "details": {
                            "summary": "The agent's plan was inefficient. The system has automatically rewritten it to perform the analysis in a single, efficient step.",
                            "correction": {"from": original_phase, "to": phase}
                        }
                    }
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)
                    made_change = True
                else:
                    app_logger.info(f"PLAN REWRITE SKIPPED: Task classified as 'synthesis'. Preserving loop for phase {phase.get('phase')}.")

        if made_change:
            app_logger.info(f"PLAN REWRITE (TDA_LLMTask): Final rewritten plan: {self.executor.meta_plan}")


    def _rewrite_plan_for_date_range_loops(self):
        """
        Deterministically rewrites a plan where a `TDA_DateRange` tool
        is not followed by a necessary loop, correcting a common planning flaw.
        This runs after the main plan generation and before execution.
        """
        if not self.executor.meta_plan or len(self.executor.meta_plan) < 2:
            return

        i = 0
        made_change = False
        while i < len(self.executor.meta_plan) - 1:
            current_phase = self.executor.meta_plan[i]
            next_phase = self.executor.meta_plan[i+1]

            is_date_range_phase = (
                "TDA_DateRange" in current_phase.get("relevant_tools", [])
            )

            if not is_date_range_phase:
                i += 1
                continue

            dr_phase_ref = f"result_of_phase_{current_phase['phase']}"

            # Check if next phase references TDA_DateRange via string ref OR loop_over
            uses_string_ref = False
            if isinstance(next_phase.get("arguments"), dict):
                for arg_value in next_phase["arguments"].values():
                    if isinstance(arg_value, str) and arg_value == dr_phase_ref:
                        uses_string_ref = True
                        break

            is_loop_over_dr = (next_phase.get("loop_over") == dr_phase_ref)
            references_date_range = uses_string_ref or is_loop_over_dr

            if not references_date_range:
                i += 1
                continue

            # Check MCP tool schema (source of truth) for range pair
            tool_name = next_phase.get("relevant_tools", [""])[0]
            mcp_tools = self.executor.dependencies['STATE'].get('mcp_tools', {})
            tool_spec = mcp_tools.get(tool_name)
            schema_param_names = set()
            if tool_spec and hasattr(tool_spec, 'args') and isinstance(tool_spec.args, dict):
                schema_param_names = set(tool_spec.args.keys())

            range_pairs = [("start_date", "end_date"), ("from_date", "to_date"),
                           ("begin_date", "end_date"), ("start", "end")]
            is_range_tool = any(s in schema_param_names and e in schema_param_names
                                for s, e in range_pairs)

            if is_range_tool:
                # --- RANGE TOOL: Wire both args from TDA_DateRange, unwrap loop if needed ---
                for start_name, end_name in range_pairs:
                    if start_name in schema_param_names and end_name in schema_param_names:
                        next_phase["arguments"][start_name] = {
                            "source": dr_phase_ref, "key": "start_date"
                        }
                        next_phase["arguments"][end_name] = {
                            "source": dr_phase_ref, "key": "end_date"
                        }
                        break

                # If LLM created a loop, unwrap it — range tools don't iterate per day
                if is_loop_over_dr:
                    next_phase.pop("type", None)
                    next_phase.pop("loop_over", None)
                    app_logger.info(
                        f"PLAN REWRITE: Phase {next_phase['phase']} — unwrapped LLM-generated "
                        f"loop for range tool '{tool_name}'. Wired start_date + end_date "
                        f"from TDA_DateRange (phase {current_phase['phase']})."
                    )
                else:
                    app_logger.info(
                        f"PLAN REWRITE: Phase {next_phase['phase']} — wired start_date + "
                        f"end_date from TDA_DateRange for range tool '{tool_name}'."
                    )

                event_data = {
                    "step": "System Correction",
                    "type": "optimization",
                    "details": {
                        "summary": "Tool accepts a date range natively. "
                                   "Wired start_date and end_date from TDA_DateRange output.",
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
                made_change = True
                i += 1
                continue

            # --- SINGLE-DATE TOOL: Convert to loop if not already one ---
            is_missing_loop = (next_phase.get("type") != "loop")

            if is_missing_loop and uses_string_ref:
                app_logger.warning(
                    f"PLAN REWRITE: Detected TDA_DateRange at phase {current_phase['phase']} "
                    f"not followed by a loop. Rewriting phase {next_phase['phase']}."
                )

                original_next_phase = copy.deepcopy(next_phase)

                next_phase["type"] = "loop"
                next_phase["loop_over"] = dr_phase_ref

                for arg_name, arg_value in next_phase["arguments"].items():
                    if isinstance(arg_value, str) and arg_value == dr_phase_ref:
                        next_phase["arguments"][arg_name] = {
                            "source": "loop_item",
                            "key": "date"
                        }
                        break

                event_data = {
                    "step": "System Correction",
                    "type": "workaround",
                    "details": {
                        "summary": "The agent's plan was inefficiently structured. The system has automatically rewritten it to correctly process each item in the date range.",
                        "correction": {
                            "from": original_next_phase,
                            "to": next_phase
                        }
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
                made_change = True

            # else: single-date tool already a loop — leave unchanged

            i += 1

        if made_change:
            app_logger.info(f"PLAN REWRITE (Date-Range): Final rewritten plan: {self.executor.meta_plan}")

    async def _rewrite_plan_for_empty_context_report(self, knowledge_context_used: str = ""):
        """
        Detects when TDA_ContextReport is used with empty arguments (missing answer_from_context).
        If knowledge context was retrieved, synthesizes an answer from that context and injects
        it into the plan to prevent execution errors.
        
        Args:
            knowledge_context_used: The knowledge context string that was retrieved during planning
        """
        if not self.executor.meta_plan:
            return
        
        made_change = False
        
        for phase in self.executor.meta_plan:
            # Check if this phase uses TDA_ContextReport
            relevant_tools = phase.get("relevant_tools", [])
            if "TDA_ContextReport" not in relevant_tools:
                continue
            
            # Check if arguments are missing or answer_from_context is empty
            arguments = phase.get("arguments", {})
            answer_from_context = arguments.get("answer_from_context")
            
            if answer_from_context:
                # Answer already provided, no need to rewrite
                continue
            
            app_logger.warning(
                f"PLAN REWRITE: Phase {phase.get('phase')} uses TDA_ContextReport but has empty arguments. "
                "Attempting to synthesize answer from knowledge context."
            )
            
            # If we have knowledge context, use it to synthesize an answer
            if knowledge_context_used:
                synthesis_prompt = f"""You are helping to answer a user's question using relevant knowledge from a documentation repository.

User's Question: {self.executor.original_user_input}

Retrieved Knowledge Context:
{knowledge_context_used}

Your task: Provide a clear, comprehensive answer to the user's question based ONLY on the knowledge context above. 
- Directly address what the user is asking
- Use specific examples from the context when helpful
- If the context doesn't fully answer the question, explain what information is available
- Keep the tone helpful and professional

Respond with ONLY the answer text, no preamble or meta-commentary."""

                reason = "Synthesizing answer from knowledge context for TDA_ContextReport"
                call_id = str(uuid.uuid4())
                
                event_data = {
                    "step": "Synthesizing Knowledge Answer",
                    "type": "plan_optimization",
                    "details": {
                        "summary": "TDA_ContextReport was called without an answer. Synthesizing response from retrieved knowledge.",
                        "call_id": call_id
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
                yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")
                
                try:
                    response_text, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
                        prompt=synthesis_prompt,
                        reason=reason,
                        system_prompt_override="You are a helpful assistant that answers questions based on provided documentation.",
                        raise_on_error=False,
                        disabled_history=True,
                        source=self.executor.source
                    )
                    
                    yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

                    # Log knowledge synthesis LLM call with tokens + cost for history
                    synthesis_log_event = {
                        "step": "Synthesizing Knowledge Answer",
                        "type": "system_message",
                        "details": {
                            "summary": "Synthesizing response from retrieved knowledge context.",
                            "call_id": call_id,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)
                        }
                    }
                    self.executor._log_system_event(synthesis_log_event)
                    yield self.executor._format_sse_with_depth(synthesis_log_event)

                    # Update session with token usage
                    updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
                    if updated_session:
                        yield self.executor._format_sse_with_depth({
                            "statement_input": input_tokens,
                            "statement_output": output_tokens,
                            "turn_input": self.executor.turn_input_tokens,
                            "turn_output": self.executor.turn_output_tokens,
                            "total_input": updated_session.get("input_tokens", 0),
                            "total_output": updated_session.get("output_tokens", 0),
                            "call_id": call_id,
                            "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)
                        }, "token_update")

                    # Inject the synthesized answer into the phase arguments
                    original_phase = copy.deepcopy(phase)
                    phase["arguments"]["answer_from_context"] = response_text.strip()
                    
                    event_data = {
                        "step": "System Correction",
                        "type": "workaround",
                        "details": {
                            "summary": "TDA_ContextReport was missing an answer. System synthesized one from knowledge context.",
                            "correction": {
                                "from": original_phase,
                                "to": phase
                            }
                        }
                    }
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)
                    
                    made_change = True
                    app_logger.info(f"Successfully synthesized answer for TDA_ContextReport in phase {phase.get('phase')}")
                    
                except Exception as e:
                    app_logger.error(f"Failed to synthesize answer from knowledge context: {e}. Using fallback message.")
                    # Inject a fallback message
                    phase["arguments"]["answer_from_context"] = (
                        "I found relevant information in the knowledge base. "
                        "Please review the retrieved documents for details about your question."
                    )
                    made_change = True
            else:
                # No knowledge context available, inject a generic fallback
                app_logger.warning(f"No knowledge context available for TDA_ContextReport in phase {phase.get('phase')}. Using generic fallback.")
                phase["arguments"]["answer_from_context"] = (
                    "I don't have enough information to answer this question. "
                    "Please try rephrasing or providing more context."
                )
                made_change = True
        
        if made_change:
            app_logger.info(f"PLAN REWRITE (Empty Context Report): Final rewritten plan: {self.executor.meta_plan}")

    # The KG component actions that COMPILE governed SQL and deliberately never run it.
    # (Executing from inside a component would bypass the Agent-Authority tool
    # chokepoint — the probe_bridge precedent. So the plan must carry the execution.)
    # query_product ONLY — a compiled METRIC is not a governed execution
    # (operator correction, session dd40d071): compile_metric's SQL is in no
    # governed registry and carries no signature, and its pre-OBDA-READ behaviour
    # never had (or needed) an inserted executor phase. Including it made this
    # pass the AUTHOR of a dangling reference: compile_metric('Revenue') failed
    # ("no metric named 'Revenue'"), and the pass-inserted phase — whose goal
    # text called the output "the signed, deterministic contract" — executed a
    # reference to nothing.
    _COMPILE_ONLY_ACTIONS = ("query_product",)

    @staticmethod
    def _is_compile_only_phase(phase: dict) -> bool:
        """True iff this phase compiles governed SQL rather than producing data."""
        if not isinstance(phase, dict):
            return False
        # the OBDA-READ component's own tool, or the knowledge-graph tool's alias of the action
        if not ({"TDA_DataProduct", "TDA_KnowledgeGraph"} & set(phase.get("relevant_tools") or [])):
            return False
        action = (phase.get("arguments") or {}).get("action")
        return action in Planner._COMPILE_ONLY_ACTIONS

    @staticmethod
    def _phase_result_is_consumed(plan: list, phase_num: int, skip_index: int = -1) -> bool:
        """Does any OTHER phase already reference ``result_of_phase_<phase_num>``?

        Both reference forms count — the canonical ``{"source": …}`` dict and the legacy
        ``result_of_phase_N`` / ``phase_N`` string — anywhere in a phase's arguments,
        ``loop_over`` or goal. Word-bounded so phase 1 is never matched by phase 10, and
        so ``phase_1`` does not match inside ``result_of_phase_1``.
        """
        needles = [rf"\bresult_of_phase_{phase_num}\b", rf"\bphase_{phase_num}\b"]

        def walk(value) -> bool:
            if isinstance(value, str):
                return any(re.search(n, value) for n in needles)
            if isinstance(value, dict):
                return any(walk(v) for v in value.values())
            if isinstance(value, list):
                return any(walk(v) for v in value)
            return False

        for idx, other in enumerate(plan):
            if idx == skip_index or not isinstance(other, dict):
                continue
            if walk(other.get("arguments")) or walk(other.get("loop_over")) or walk(other.get("goal")):
                return True
        return False

    def _rewrite_plan_for_governed_execution(self):
        """Guarantee that compiled governed SQL is actually EXECUTED.

        ``query_product`` / ``compile_metric`` COMPILE a statement and hand it back; the
        agent runs it through its own data tool, so the plan needs a following phase
        wired to the canonical reference ``{"source": "result_of_phase_N", "key": "sql"}``.

        Observed live, twice, on the SAME question: one turn planned that seam exactly
        right, the next planned no execute phase at all — three governed compiles, zero
        rows, and an answer of pure prose with no figures. WHICH product and WHICH slice
        is genuine judgement and stays with the model; *"compiled SQL must then be
        executed"* is mechanical, so it is guaranteed here rather than hoped for
        (Component → LLM priority 1).

        SURGICAL BY CONSTRUCTION: a compile phase whose result is already consumed by
        some other phase is left completely alone, so every plan that works today —
        including the model's own correct two-step plans — is byte-identical. The pass
        only ever fires on the shape that is provably broken: a compiled statement
        nothing executes.
        """
        # OBDA-READ IS ADDITIVE (charter #15): this pass exists FOR the governed
        # lane, so with the master off it does not exist — the pre-OBDA-READ planner
        # had no such pass, and one firing anyway is how a plan grew a phase
        # narrating a "governed statement" on a deployment that disabled OBDA-READ.
        try:
            # per AGENT: the OBDA-READ component off for this agent, or Option A (no products
            # configured — the default), gets NO OBDA-READ planner behaviour (operator model).
            from trusted_data_agent.agent.obda import profile_obda as _pobda
            _mode, _ = _pobda(self.executor._resolve_gate_profile("obda"))
            if _mode == "off":
                return
        except Exception:  # noqa: BLE001
            return
        plan = self.executor.meta_plan
        if not plan or not isinstance(plan, list):
            return

        # Never invent a tool this profile does not have. No data tool ⇒ nothing to add;
        # the compile phase still returns its SQL for the agent to report.
        available = self.executor.dependencies['STATE'].get('mcp_tools', {}) or {}
        # the catalog's name for it — on the primary server its real name; the first server that
        # offers it otherwise (PROP-006: a tool is recognised by its base name)
        exec_tool = next((n for t in APP_CONFIG.SQL_OPTIMIZATION_TOOLS for n in available
                          if base_name(n) == t), None)
        if not exec_tool:
            return

        # DEAD-PHASE REWIRING (surface-only turns). Observed live: the model
        # planned `query_product` correctly AND a hand-written
        # `SELECT SUM(customer_count) FROM support_load` — SQL against the
        # product's NAME as if it were a table. Under an exclusive surface a
        # literal-SQL phase is denied by construction (plan-time text can never
        # be the runtime-registered compiled statement), so at runtime it burns
        # a denial and a recovery round doing nothing.
        #
        # The phase is REWRITTEN IN PLACE into the reference-executor — same
        # position, same number — rather than removed: a first version removed
        # and renumbered, and the gate caught the report's reference to the
        # removed phase being renumbered into pointing at ITSELF. In-place
        # rewiring keeps every downstream reference valid and pointing at the
        # phase that yields the rows, which is what the model meant it to do.
        #
        # CONSERVATIVE GUARDS: only with a PRECEDING compile phase to wire to —
        # a literal-SQL-only plan keeps its phase, because the runtime denial
        # there is recovery-shaped ("use query_product…") and is the mechanism
        # that teaches the pivot; and only the FIRST literal follower per
        # compile is rewired — a second would duplicate the execution.
        sql_tools = set(APP_CONFIG.SQL_OPTIMIZATION_TOOLS)
        if self._surface_exclusive_now():
            last_compile_num = None
            wired_compiles = set()
            for p in plan:
                if not isinstance(p, dict):
                    continue
                if self._is_compile_only_phase(p) and isinstance(p.get("phase"), int):
                    last_compile_num = p["phase"]
                    continue
                if (base_name((p.get("relevant_tools") or [None])[0]) in sql_tools
                        and self._phase_has_literal_sql(p)
                        and last_compile_num is not None
                        and last_compile_num not in wired_compiles):
                    dropped_sql = str((p.get("arguments") or {}).get("sql"))[:200]
                    p["relevant_tools"] = [exec_tool]
                    p["arguments"] = {"sql": {"source": f"result_of_phase_{last_compile_num}",
                                              "key": "sql"}}
                    p["goal"] = (f"Execute the governed statement compiled by phase "
                                 f"{last_compile_num}, exactly as returned — it is the "
                                 f"signed, deterministic contract and must not be modified.")
                    wired_compiles.add(last_compile_num)
                    app_logger.warning(
                        f"PLAN REWRITE: phase {p.get('phase')} carried hand-written SQL — "
                        "this agent is surface-only, so it is denied by construction; "
                        "rewired in place to execute the compiled product statement.")
                    yield self.executor._format_sse_with_depth({
                        "step": "System Correction",
                        "type": "workaround",
                        "details": {
                            "summary": ("This agent is surface-only: a hand-written SQL "
                                        "phase was rewired to execute the compiled product "
                                        "statement instead — the data surface would deny "
                                        "the original with certainty."),
                            "correction_type": "surface_dead_sql_rewired",
                            "phase": p.get("phase"),
                            "replaced_sql": dropped_sql,
                        },
                    })
        i = 0
        while i < len(plan):
            phase = plan[i]
            num = phase.get("phase") if isinstance(phase, dict) else None
            if (not self._is_compile_only_phase(phase) or not isinstance(num, int)
                    or self._phase_result_is_consumed(plan, num, skip_index=i)):
                i += 1
                continue

            new_num = num + 1
            # Shift every phase at or after the new slot AND rewrite references to them
            # BEFORE inserting — bumping only the `phase` field would leave every later
            # reference pointing at the wrong phase (the C4 lesson).
            shift = {p["phase"]: p["phase"] + 1 for p in plan
                     if isinstance(p, dict) and isinstance(p.get("phase"), int)
                     and p["phase"] >= new_num}
            renumber_plan_refs(plan, shift)

            exec_phase = {
                "phase": new_num,
                "goal": (f"Execute the governed statement compiled by phase {num}, exactly "
                         f"as returned — it is the signed, deterministic contract and must "
                         f"not be modified."),
                "relevant_tools": [exec_tool],
                "arguments": {"sql": {"source": f"result_of_phase_{num}", "key": "sql"}},
            }
            plan.insert(i + 1, exec_phase)

            app_logger.warning(
                f"PLAN REWRITE: phase {num} compiles governed SQL that no phase executed — "
                f"inserted phase {new_num} ({exec_tool}) to run it.")
            yield self.executor._format_sse_with_depth({
                "step": "System Correction",
                "type": "workaround",
                "details": {
                    "summary": (f"Phase {num} compiles a governed, signed statement but the "
                                f"plan never executed it. The system inserted an execution "
                                f"phase wired to its result, so the answer comes from the "
                                f"data product rather than from prose."),
                    "correction_type": "governed_execution_guarantee",
                    "compiled_by_phase": num,
                    "inserted_phase": new_num,
                    "tool": exec_tool,
                },
            })
            i += 2          # skip past the phase we just inserted

    @staticmethod
    def _phase_has_literal_sql(phase: dict) -> bool:
        """True iff this phase carries literal SQL TEXT (not a runtime reference).

        Distinct from ``_sql_arg_is_literal``, which also returns True for a phase
        with NO sql argument — here the question is "did the model hand-write a
        statement?", and only an actual string with no phase reference counts.
        """
        args = (phase or {}).get("arguments") or {}
        for key in ("sql", "query", "query_request"):
            if key in args:
                value = args[key]
                return isinstance(value, str) and not _PHASE_REF_RE.search(value)
        return False

    @staticmethod
    def _surface_exclusive_now() -> bool:
        """Is the ambient agent principal surface-only (exclusive, not advisory)?

        Read from the SAME contextvar the tool chokepoints enforce with, so the
        planner's view of "raw SQL will be denied" cannot drift from the runtime's.
        Fails soft to False — an unreadable authority layer must never change a plan.
        """
        try:
            from trusted_data_agent.core import agent_authority as _aa
            pr = _aa.get_current_principal()
            sc = getattr(pr, "scope", None)
            return bool(sc is not None
                        and getattr(sc, "data_surface_products", None) is not None
                        and getattr(sc, "data_surface_exclusive", False)
                        and not getattr(sc, "data_surface_advisory", False))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _sql_arg_is_literal(phase: dict) -> bool:
        """True iff this phase's SQL argument is literal text a consolidator could merge.

        A phase can carry SQL that does not EXIST yet at plan time — it is produced by an
        earlier phase at runtime and referenced, either as the canonical
        ``{"source": "result_of_phase_N", "key": "sql"}`` dict or the legacy
        ``"result_of_phase_N"`` string. Neither is SQL.

        This is exactly the OBDA-READ seam: ``query_product`` COMPILES governed, signed,
        deterministic SQL and ``base_readQuery`` executes precisely what it returned.
        """
        args = phase.get("arguments") or {}
        for key in ("sql", "query", "query_request"):
            if key in args:
                value = args[key]
                if not isinstance(value, str):
                    return False                      # a {"source": …} reference dict
                return not _PHASE_REF_RE.search(value)
        return True         # no SQL argument at all — nothing here for this rule to protect

    async def _rewrite_plan_for_sql_consolidation(self):
        """
        Detects and consolidates sequential, inefficient SQL query phases into a
        single, optimized query using a specialized LLM call.
        """
        if not self.executor.meta_plan or len(self.executor.meta_plan) < 2:
            return

        sql_tools = set(APP_CONFIG.SQL_OPTIMIZATION_TOOLS)

        i = 0
        while i < len(self.executor.meta_plan) - 1:
            current_phase = self.executor.meta_plan[i]

            current_tool = (current_phase.get("relevant_tools") or [None])[0]
            if base_name(current_tool) not in sql_tools:
                i += 1
                continue

            j = i + 1
            while j < len(self.executor.meta_plan):
                next_tool = (self.executor.meta_plan[j].get("relevant_tools") or [None])[0]
                if base_name(next_tool) not in sql_tools:
                    break
                j += 1

            sql_sequence = self.executor.meta_plan[i:j]

            if len(sql_sequence) > 1:
                # A phase whose SQL is a RUNTIME REFERENCE holds no SQL at plan time.
                # Handing that placeholder to the consolidation LLM gives it nothing to
                # merge, so it invents SQL from the goal instead — silently replacing a
                # governed, signed, deterministic statement with a hallucinated one
                # against tables it has never seen. Observed live: the model correctly
                # planned `base_readQuery(sql={source: result_of_phase_2, key: sql})`
                # twice and this pass collapsed both into
                # `FROM states LEFT JOIN customers …` — none of which exist.
                #
                # Consolidation is an OPTIMISATION: a skipped merge costs one round
                # trip, a destroyed reference costs correctness. Literal SQL in every
                # phase of the run, or no merge. (Conservative on purpose — a sequence
                # is merged as a unit, so one reference makes the whole run unmergeable;
                # a missed optimisation is not a defect, a fabricated query is.)
                if any(not self._sql_arg_is_literal(p) for p in sql_sequence):
                    app_logger.info(
                        f"PLAN REWRITE: skipping SQL consolidation for phases {i+1}-{j} — "
                        "at least one carries a runtime reference, so its SQL does not "
                        "exist yet at plan time (governed lane preserved).")
                    i = j
                    continue
                # PROP-006 (B10): statements for different MCP servers are never merged — one
                # SELECT joining two warehouses runs nowhere. Only an agent with several servers
                # can span; a single-server agent is never stopped here.
                try:
                    from trusted_data_agent.agent import source_routing as _srt
                    _spans = _srt.spans_servers(
                        [(p.get("relevant_tools") or [None])[0] for p in sql_sequence],
                        [p.get("arguments") or {} for p in sql_sequence])
                except Exception:  # noqa: BLE001
                    _spans = False
                if _spans:
                    app_logger.info(
                        f"PLAN REWRITE: skipping SQL consolidation for phases {i+1}-{j} — the "
                        "statements belong to different MCP servers of this agent.")
                    i = j
                    continue
                app_logger.warning(f"PLAN REWRITE: Detected inefficient sequential SQL plan from phase {i+1} to {j}. Consolidating...")

                inefficient_queries = []
                sql_arg_synonyms = ["sql", "query", "query_request"]
                for phase in sql_sequence:
                    args = phase.get("arguments", {})
                    query = next((args[key] for key in sql_arg_synonyms if key in args), None)
                    if query:
                        inefficient_queries.append(f"-- Query from Phase {phase['phase']}:\n{query}")

                if not inefficient_queries:
                    i = j
                    continue

                # Use profile-aware prompt resolution
                consolidation_prompt_content = self.executor.prompt_resolver.get_sql_consolidation_prompt()
                if not consolidation_prompt_content:
                    app_logger.error("Failed to resolve SQL_CONSOLIDATION_PROMPT from profile mapping, skipping consolidation")
                    i = j
                    continue
                
                consolidation_prompt = consolidation_prompt_content.format(
                    user_goal=self.executor.original_user_input,
                    inefficient_queries="\n\n".join(inefficient_queries)
                )

                reason = "Consolidating inefficient SQL plan."
                call_id = str(uuid.uuid4())
                event_data = {
                    "step": "Optimizing SQL Plan", "type": "plan_optimization",
                    "details": {"summary": "Detected an inefficient multi-step SQL plan. The agent is consolidating it into a single, optimized query.", "call_id": call_id}
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
                yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

                # QW5 (C12): this is an OPTIMIZATION pass — a valid (merely unconsolidated) plan
                # already exists. The call previously ran raise_on_error=True *outside* the parse
                # try, so a transient provider error failed the whole turn; and even with
                # raise_on_error=False the wrapper still raises RuntimeError on timeout. Wrap the
                # call AND drop raise_on_error so any failure downgrades to "skip consolidation,
                # keep the original plan" instead of killing the turn.
                response_text, input_tokens, output_tokens = None, 0, 0
                try:
                    response_text, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
                        prompt=consolidation_prompt, reason=reason,
                        system_prompt_override="You are a JSON-only responding SQL expert.",
                        raise_on_error=False, source=self.executor.source
                    )
                except Exception as _consol_err:
                    app_logger.error(
                        f"SQL consolidation LLM call failed: {_consol_err}. "
                        f"Proceeding with the original (unconsolidated) plan."
                    )

                yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

                if not response_text:
                    # No usable response — keep the valid unconsolidated plan and advance past
                    # this SQL sequence (same skip semantics as the "no inefficient queries" path).
                    i = j
                    continue

                # --- MODIFICATION START: Pass user_uuid to get_session ---
                updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
                # --- MODIFICATION END ---
                if updated_session:
                    yield self.executor._format_sse_with_depth({ "statement_input": input_tokens, "statement_output": output_tokens, "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens, "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0), "call_id": call_id, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0) }, "token_update")

                try:
                    data = robust_json_parse(response_text)
                    if not isinstance(data, dict):
                        raise ValueError("No JSON object found in consolidation response.")
                    consolidated_query = data.get("consolidated_query")
                    if not consolidated_query: raise ValueError("'consolidated_query' key not found.")

                    original_phases = copy.deepcopy(sql_sequence)

                    consolidated_phase = sql_sequence[-1]
                    # C12-residual: the consolidated phase reuses the LAST merged phase's dict —
                    # if that phase carried loop wiring (`type: "loop"` / `loop_over`), the single
                    # consolidated SQL query would become a (possibly self-referential) loop phase.
                    # A consolidated single query never loops; strip the loop markers.
                    consolidated_phase.pop("loop_over", None)
                    if consolidated_phase.get("type") == "loop":
                        consolidated_phase.pop("type", None)
                    consolidated_phase['phase'] = sql_sequence[0]['phase']
                    consolidated_phase['goal'] = f"Execute consolidated SQL query to achieve the goal: '{self.executor.original_user_input}'"

                    args = consolidated_phase.get("arguments", {})
                    found_key = next((key for key in sql_arg_synonyms if key in args), "sql")
                    args[found_key] = consolidated_query
                    consolidated_phase['arguments'] = args

                    num_phases_to_remove = len(sql_sequence)
                    self.executor.meta_plan[i] = consolidated_phase
                    for _ in range(num_phases_to_remove - 1):
                        del self.executor.meta_plan[i+1]

                    # C4: shift every phase AFTER the merged block down by (k-1) AND rewrite
                    # references to them. The consolidated phase keeps sql_sequence[0]'s number,
                    # so only phases numbered above the merged range move. (References to the
                    # removed MIDDLE phases are inherently dangling after a merge — out of scope.)
                    _shift = num_phases_to_remove - 1
                    if _shift > 0:
                        _consolidated_num = consolidated_phase['phase']
                        shift_mapping = {
                            p['phase']: p['phase'] - _shift
                            for p in self.executor.meta_plan
                            if isinstance(p, dict) and isinstance(p.get('phase'), int)
                            and p['phase'] > _consolidated_num + _shift
                        }
                        renumber_plan_refs(self.executor.meta_plan, shift_mapping)

                    event_data = {
                        "step": "System Correction", "type": "workaround",
                        "details": {
                            "summary": "The agent's SQL plan was inefficient. The system has automatically consolidated it into a single query.",
                            "correction": {"from": original_phases, "to": consolidated_phase}
                        }
                    }
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)
                    app_logger.info(f"PLAN REWRITE (SQL Consolidation): Final rewritten plan: {self.executor.meta_plan}")
                    i = 0
                    continue

                except (json.JSONDecodeError, ValueError, AttributeError) as e:
                    app_logger.error(f"Failed to consolidate SQL plan: {e}. Proceeding with original inefficient plan. Response: {response_text}")

            i += 1

    # ===========================================================================
    # KNOWLEDGE REPOSITORY RETRIEVAL METHODS
    # ===========================================================================
    
    def _is_knowledge_enabled(self, profile_config: Optional[dict] = None) -> bool:
        """
        Check if knowledge retrieval is enabled globally and EXPLICITLY in the profile.

        Knowledge retrieval now requires explicit opt-in via profile.knowledgeConfig.enabled = true.
        This separates MCP tool-focused profiles (no knowledge) from RAG-focused profiles (with knowledge).

        Args:
            profile_config: Optional profile configuration dictionary

        Returns:
            True if knowledge retrieval should be performed
        """
        # Global kill switch
        if not APP_CONFIG.KNOWLEDGE_RAG_ENABLED:
            return False

        # CHANGED: Require explicit opt-in from profile
        if not profile_config:
            return False  # No profile = no knowledge retrieval

        knowledge_config = profile_config.get("knowledgeConfig", {})
        # CHANGED: Default to False (require explicit enablement)
        if not knowledge_config.get("enabled", False):
            return False

        return True
    
    def _get_knowledge_collections(self, profile_config: Optional[dict] = None) -> list:
        """
        Get the list of knowledge collections to query based on profile configuration.
        
        Args:
            profile_config: Optional profile configuration dictionary
            
        Returns:
            List of collection metadata dicts with reranking settings
        """
        if not profile_config:
            return []
        
        knowledge_config = profile_config.get("knowledgeConfig", {})
        configured_collections = knowledge_config.get("collections", [])

        if not configured_collections:
            return []

        result = []
        for coll_config in configured_collections:
            # Support both 'id' and 'collectionId' for backwards compatibility
            coll_id = coll_config.get("id") or coll_config.get("collectionId")
            if coll_id is None:
                continue

            # Find collection metadata using retriever's centralized method
            # This checks both APP_STATE (planner collections) and database (knowledge collections)
            coll_meta = self.rag_retriever.get_collection_metadata(coll_id)
            if not coll_meta:
                app_logger.warning(f"Collection {coll_id} configured in profile but not found in APP_STATE or database")
                continue

            # Only include knowledge repositories
            if coll_meta.get("repository_type") != "knowledge":
                app_logger.debug(f"Skipping collection {coll_id} - not a knowledge repository")
                continue

            # Check if collection is enabled
            if not coll_meta.get("enabled", False):
                app_logger.debug(f"Skipping collection {coll_id} - not enabled")
                continue

            result.append({
                "id": coll_id,
                "name": coll_meta.get("name"),
                "reranking_enabled": coll_config.get("reranking", False),
                "metadata": coll_meta
            })

        return result
    
    def _balance_collection_diversity(self, results: list, max_docs: int) -> list:
        """
        Balance document selection to ensure diversity across multiple collections.
        Uses round-robin selection to avoid over-representing any single collection.
        
        Args:
            results: List of retrieved documents with collection_id
            max_docs: Maximum number of documents to return
            
        Returns:
            Balanced list of documents with diverse collection representation
        """
        if not results or max_docs <= 0:
            return []
        
        if len(results) <= max_docs:
            return results
        
        # Group by collection
        by_collection = {}
        for doc in results:
            coll_id = doc.get("collection_id")
            if coll_id not in by_collection:
                by_collection[coll_id] = []
            by_collection[coll_id].append(doc)
        
        # Round-robin selection
        selected = []
        collection_ids = list(by_collection.keys())
        idx = 0
        
        while len(selected) < max_docs:
            # Get next collection in round-robin
            coll_id = collection_ids[idx % len(collection_ids)]
            
            # Take one document from this collection if available
            if by_collection[coll_id]:
                selected.append(by_collection[coll_id].pop(0))
            
            idx += 1
            
            # Break if all collections are exhausted
            if all(len(docs) == 0 for docs in by_collection.values()):
                break
        
        return selected
    
    def _format_with_token_limit(self, documents: list, max_tokens: int) -> str:
        """
        Format knowledge documents into a string while respecting token limits.
        Uses approximate token counting (4 chars ≈ 1 token).
        
        Args:
            documents: List of document dictionaries
            max_tokens: Maximum tokens for all documents combined
            
        Returns:
            Formatted knowledge context string
        """
        if not documents:
            return ""
        
        max_chars = max_tokens * 4  # Rough approximation: 4 chars per token
        
        formatted_sections = []
        current_chars = 0
        
        for i, doc in enumerate(documents, 1):
            # Format document header
            collection_name = doc.get("collection_name", "Unknown")
            similarity = doc.get("similarity_score", 0)
            
            header = f"### Knowledge Document {i} (from '{collection_name}', relevance: {similarity:.2f})\n"
            
            # Get document content - different structure for knowledge vs planner documents
            content = doc.get("content", "")
            
            # If no direct content field, try to get from full_case_data (planner documents)
            if not content:
                full_case = doc.get("full_case_data", {})
                if isinstance(full_case, dict):
                    # Try knowledge structure first
                    content = full_case.get("content", "")
                    # Fall back to planner structure
                    if not content:
                        content = full_case.get("intent", {}).get("user_query", "")
                    
                    # Get strategy/answer if available (planner documents)
                    if "successful_strategy" in full_case:
                        strategy = full_case.get("successful_strategy", {})
                        phases = strategy.get("phases", [])
                        if phases:
                            content += "\n\n**Strategy:**\n"
                            for phase in phases:
                                goal = phase.get("goal", "")
                                tools = phase.get("relevant_tools", [])
                                content += f"- {goal} (using: {', '.join(tools)})\n"
            
            section = header + content + "\n\n"
            section_len = len(section)
            
            # Check if adding this document would exceed limit
            if current_chars + section_len > max_chars:
                # Try to add truncated version
                remaining = max_chars - current_chars
                if remaining > len(header) + 100:  # Only add if we can include meaningful content
                    truncated = header + content[:remaining - len(header) - 20] + "...\n\n"
                    formatted_sections.append(truncated)
                    current_chars += len(truncated)
                break
            
            formatted_sections.append(section)
            current_chars += section_len
        
        if not formatted_sections:
            return ""
        
        return "".join(formatted_sections)
    
    async def _rerank_knowledge_with_llm(self, query: str, documents: list, max_docs: int) -> list:
        """
        Rerank knowledge documents using LLM to assess relevance to the planning query.
        
        Args:
            query: The user's query/goal
            documents: List of candidate documents
            max_docs: Number of top documents to return after reranking
            
        Returns:
            Reranked and filtered list of documents
        """
        if not documents or max_docs <= 0:
            return []
        
        if len(documents) <= max_docs:
            return documents
        
        try:
            # Prepare reranking prompt
            docs_for_ranking = []
            for i, doc in enumerate(documents):
                full_case = doc.get("full_case_data", {})
                content = full_case.get("intent", {}).get("user_query", "")[:500]  # Limit content length
                docs_for_ranking.append(f"Document {i+1}: {content}")
            
            reranking_prompt = f"""You are helping to select the most relevant knowledge documents for a planning task.

User's Goal: {query}

Available Documents:
{chr(10).join(docs_for_ranking)}

Task: Rank these documents by relevance to the user's goal. Return ONLY a JSON array of document numbers in order of relevance (most relevant first).
Example: [3, 1, 5, 2, 4]

Ranking:"""

            # Call LLM for reranking
            response_text, _rr_in, _rr_out = await self.executor._call_llm_and_update_tokens(
                prompt=reranking_prompt,
                reason="Reranking knowledge documents for relevance"
            )
            # §4 cost-transparency: this billed call was invisible to the event-sum. Optimize persists
            # execution_trace, so a _log_system_event reaches the reload event-sum (matches turn_cost).
            self.executor._log_system_event({
                "step": "Calling LLM for Knowledge Reranking",
                "type": "system_message",
                "details": {
                    "summary": "Reranking knowledge documents for relevance",
                    "input_tokens": _rr_in, "output_tokens": _rr_out,
                    "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0),
                },
            })

            # Parse ranking
            ranking = robust_json_parse(response_text)
            if not isinstance(ranking, list):
                app_logger.warning("Failed to parse LLM reranking response, using original order")
                return documents[:max_docs]
            
            # Reorder documents based on ranking
            reranked = []
            for rank in ranking[:max_docs]:
                if 1 <= rank <= len(documents):
                    reranked.append(documents[rank - 1])
            
            app_logger.info(f"LLM reranking selected top {len(reranked)} documents from {len(documents)} candidates")
            return reranked
            
        except Exception as e:
            app_logger.error(f"Error during LLM reranking: {e}. Falling back to similarity order.")
            return documents[:max_docs]
    
    async def _retrieve_knowledge_for_planning(self, query: str, profile_config: Optional[dict] = None) -> str:
        """
        Main knowledge retrieval pipeline for strategic planning.
        
        This method:
        1. Checks if knowledge retrieval is enabled
        2. Gets configured knowledge collections from profile
        3. Retrieves relevant documents from those collections
        4. Applies optional LLM reranking per collection configuration
        5. Balances diversity across collections
        6. Formats results with token limits
        
        Args:
            query: The user's query/goal for planning
            profile_config: Optional profile configuration dictionary
            
        Returns:
            Formatted knowledge context string for inclusion in planning prompt
        """
        # 1. Check if enabled
        if not self._is_knowledge_enabled(profile_config):
            return ""

        # 2. Get configured collections
        knowledge_collections = self._get_knowledge_collections(profile_config)
        if not knowledge_collections:
            return ""
        
        app_logger.info(f"Retrieving knowledge from {len(knowledge_collections)} configured collections")
        
        # 3. Retrieve documents from all collections
        if not self.rag_retriever:
            app_logger.warning("RAG retriever not available for knowledge retrieval")
            return ""
        
        # Get retrieval parameters using three-tier configuration (global -> profile -> locks)
        knowledge_config = profile_config.get("knowledgeConfig", {}) if profile_config else {}
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        effective_config = config_manager.get_effective_knowledge_config(knowledge_config)
        max_docs = effective_config.get("maxDocs", APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS)
        min_relevance = effective_config.get("minRelevanceScore", APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE)
        max_tokens = effective_config.get("maxTokens", APP_CONFIG.KNOWLEDGE_MAX_TOKENS)
        
        # Create RAG context for user-aware retrieval
        rag_context = RAGAccessContext(
            user_id=self.executor.user_uuid,
            retriever=self.rag_retriever
        )
        
        # Query with repository_type filter
        collection_ids = {coll["id"] for coll in knowledge_collections}
        all_results = await self.rag_retriever.retrieve_examples(
            query=query,
            k=max_docs * len(knowledge_collections),  # Retrieve more candidates for diversity balancing
            min_score=min_relevance,
            allowed_collection_ids=collection_ids,
            rag_context=rag_context,
            repository_type="knowledge"  # Filter for knowledge repositories only
        )
        
        if not all_results:
            app_logger.info("No relevant knowledge documents found")
            return ""
        
        app_logger.info(f"Retrieved {len(all_results)} candidate knowledge documents")
        
        # 4. Apply per-collection reranking if configured
        collections_with_reranking = [c for c in knowledge_collections if c.get("reranking_enabled")]
        
        if collections_with_reranking:
            # Group results by collection for per-collection reranking
            by_collection = {}
            for doc in all_results:
                coll_id = doc.get("collection_id")
                if coll_id not in by_collection:
                    by_collection[coll_id] = []
                by_collection[coll_id].append(doc)
            
            # Rerank collections that have reranking enabled
            reranked_results = []
            for coll in knowledge_collections:
                coll_id = coll["id"]
                coll_docs = by_collection.get(coll_id, [])
                
                if not coll_docs:
                    continue
                
                if coll.get("reranking_enabled"):
                    app_logger.info(f"Applying LLM reranking to {len(coll_docs)} documents from collection '{coll['name']}'")
                    coll_docs = await self._rerank_knowledge_with_llm(query, coll_docs, max_docs)
                
                reranked_results.extend(coll_docs)
            
            all_results = reranked_results
        
        # 5. Balance diversity across collections
        balanced_results = self._balance_collection_diversity(all_results, max_docs)
        
        app_logger.info(f"Selected {len(balanced_results)} knowledge documents for planning (balanced across collections)")

        # 6. Format with token limits
        formatted_knowledge = self._format_with_token_limit(balanced_results, max_tokens)
        
        # Track which collections were accessed for event logging
        accessed_collections = list(set(doc.get("collection_name") for doc in balanced_results if doc.get("collection_name")))
        if accessed_collections:
            self.tracked_knowledge_collections = accessed_collections
            self.tracked_knowledge_doc_count = len(balanced_results)
            self.tracked_knowledge_results = balanced_results  # Store full results for event details
            app_logger.info(f"Tracked {len(accessed_collections)} knowledge collections: {accessed_collections}")
            
            if self.event_handler:
                await self.event_handler({
                    "collections": accessed_collections,
                    "document_count": len(balanced_results)
                }, "knowledge_retrieval")
        
        # --- STORE KNOWLEDGE CONTEXT FOR PLAN OPTIMIZATION ---
        # Save the formatted knowledge for potential use in plan rewriting
        self._last_knowledge_context = formatted_knowledge
        # --- END STORAGE ---
        
        return formatted_knowledge

    async def generate_and_refine_plan(self, force_disable_history: bool = False, replan_context: str = None):
        """
        The main public method to generate a plan and then run all validation and
        refinement steps.
        """
        # Store knowledge context before calling _generate_meta_plan
        knowledge_context_for_optimization = ""

        # Helper: run a pass generator and emit a rewrite_diff event if the plan changed.
        # Handles both sync (for…yield) and async (async for…yield) generators transparently.
        async def _pass(name, gen):
            # QW4 (C5): None/conversational guard + fault isolation.
            # (a) A conversational turn leaves meta_plan = None → `len(None)` raised TypeError
            #     here even though every individual pass self-guards None. Guard the length reads.
            # (b) Make _pass the per-pass try/except boundary: a malformed-but-parseable phase
            #     (missing `phase`/`arguments` key → KeyError inside a pass) previously killed the
            #     whole turn. Now the failing pass is logged and skipped; the plan proceeds in its
            #     current (pre/partial-pass) state — a runnable plan beats a turn-fatal crash, and
            #     any residual issue is caught downstream by validation/self-correction.
            plan = self.executor.meta_plan
            before = json.dumps(plan, sort_keys=True, default=str)
            n_before = len(plan) if isinstance(plan, list) else 0
            try:
                if hasattr(gen, '__anext__'):
                    async for ev in gen:
                        yield ev
                else:
                    for ev in gen:
                        yield ev
            except Exception as _pass_err:
                app_logger.error(
                    f"Plan rewrite pass '{name}' failed and was skipped (turn continues): {_pass_err}",
                    exc_info=True,
                )
                return
            after = json.dumps(self.executor.meta_plan, sort_keys=True, default=str)
            if after != before:
                n_after = len(self.executor.meta_plan) if isinstance(self.executor.meta_plan, list) else 0
                diff_event = {
                    "step": "Plan Rewrite",
                    "type": "plan_rewrite_diff",
                    "details": {
                        "pass": name,
                        "phases_before": n_before,
                        "phases_after": n_after,
                        "delta": n_after - n_before,
                    },
                }
                self.executor._log_system_event(diff_event)
                yield self.executor._format_sse_with_depth(diff_event)

        async for event in self._generate_meta_plan(
            force_disable_history=force_disable_history,
            replan_context=replan_context
        ):
            if hasattr(self, '_last_knowledge_context'):
                knowledge_context_for_optimization = self._last_knowledge_context
            yield event

        async for event in _pass("temporal_data_flow", self._rewrite_plan_for_temporal_data_flow()):
            yield event

        # Before consolidation ON PURPOSE: complete the plan, then optimise it. The
        # phases this inserts carry runtime references, which consolidation correctly
        # refuses to merge (it cannot consolidate SQL that does not exist yet).
        async for event in _pass("governed_execution", self._rewrite_plan_for_governed_execution()):
            yield event

        if APP_CONFIG.ENABLE_SQL_CONSOLIDATION_REWRITE:
            async for event in _pass("sql_consolidation", self._rewrite_plan_for_sql_consolidation()):
                yield event

        async for event in _pass("llm_filter_deloop", self._rewrite_llm_filter_deloop()):
            yield event
        async for event in _pass("multi_loop_synthesis", self._rewrite_plan_for_multi_loop_synthesis()):
            yield event
        async for event in _pass("llm_task_optimization", self._rewrite_plan_for_corellmtask_loops()):
            yield event
        async for event in _pass("date_range_wiring", self._rewrite_plan_for_date_range_loops()):
            yield event
        # After every pass that produces or binds the SQL that will actually run
        # (consolidation, date-range binds); before validation, so what is validated is
        # what executes.
        # Before text_date_cast: a statement this pass hands to the tactical step is written
        # after the columns are read, and passes the preflight guard at execution instead.
        async for event in _pass("placeholder_sql", self._rewrite_plan_for_placeholder_sql()):
            yield event
        async for event in _pass("database_before_sql", self._rewrite_plan_for_database_before_sql()):
            yield event
        async for event in _pass("schema_before_sql", self._rewrite_plan_for_schema_before_sql()):
            yield event
        async for event in _pass("text_date_cast", self._rewrite_plan_for_text_dates()):
            yield event
        async for event in _pass("compute_insertion", self._rewrite_plan_for_compute_insertion()):
            yield event
        # Stage 3b: after compute_insertion (a compute this pass inserts is its own), before
        # validation, so what is validated is the split that executes.
        async for event in _pass("cross_source_shape", self._rewrite_plan_for_cross_source_shape()):
            yield event
        async for event in _pass("plan_validation", self._validate_and_correct_plan()):
            yield event
        async for event in _pass("chart_data_reuse", self._rewrite_plan_collapse_chart_data_refetch()):
            yield event
        async for event in _pass("charting_cleanup", self._rewrite_plan_for_charting_phases()):
            yield event
        async for event in _pass("plan_hydration", self._hydrate_plan_from_previous_turn()):
            yield event
        async for event in _pass("context_report", self._rewrite_plan_for_empty_context_report(knowledge_context_for_optimization)):
            yield event
        async for event in _pass("final_report_guarantee", self._ensure_final_report_phase()):
            yield event

        # --- EPC: Record plan after all rewrite passes ---
        try:
            if hasattr(self.executor, 'provenance') and self.executor.provenance:
                import json as _json
                self.executor.provenance.add_step(
                    "plan_rewrite",
                    _json.dumps(self.executor.meta_plan, sort_keys=True, default=str),
                    f"Plan after rewrites: {len(self.executor.meta_plan)} phase(s)")
        except Exception:
            pass
        # --- EPC END ---

        event_data = {
            "step": "Strategic Meta-Plan Generated",
            "type": "plan_generated",
            "details": self.executor.meta_plan,
            "metadata": {
                "execution_depth": self.executor.execution_depth
            }
        }
        self.executor._log_system_event(event_data)
        yield self.executor._format_sse_with_depth(event_data)

    async def _screen_conversational(self, plan_object, response_text, planning_prompt,
                                     strategic_provider, strategic_model):
        """OC-F13: the conversational shortcut is allowed only for small talk.

        A weak planner copies the meta-planning prompt's conversational example for a DATA
        question, and the turn would deliver a canned greeting. When the plan is
        `plan_type: conversational` and the QUESTION is not small talk
        (`conversational_guard.goal_is_small_talk`), re-plan once with a correction note.
        The re-plan's answer replaces the first only if it parses. Yields SSE; the result
        lands on `self._screened` as (response_text, plan_object)."""
        self._screened = (response_text, plan_object)
        if not (isinstance(plan_object, dict) and plan_object.get("plan_type") == "conversational"):
            return
        if goal_is_small_talk(self.executor.original_user_input):
            return
        app_logger.warning("Strategic planner answered a non-small-talk request conversationally. Re-planning once.")
        yield self.executor._format_sse_with_depth({
            "step": "System Correction", "type": "workaround",
            "details": "The planner answered a request with a greeting and no plan. Re-planning."
        })
        retry_prompt = planning_prompt + REPLAN_NOTE.format(goal=(self.executor.original_user_input or "")[:400])
        async for _ev in self._strategic_retry(retry_prompt, "conversational re-plan", strategic_provider, strategic_model):
            yield _ev
        _replanned = robust_json_parse(self._strategic_retry_text)
        if _replanned is not None:
            self._screened = (self._strategic_retry_text, _replanned)

    async def _strategic_retry(self, retry_prompt: str, label: str, strategic_provider, strategic_model):
        """One more strategic planning call, priced and logged like the first (a fresh-call_id
        system_event + a token_update). Yields SSE; the response lands on
        `self._strategic_retry_text`. Shared by the parse-error retry and the OC-F13 re-plan."""
        yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")
        response_text, _retry_in, _retry_out = await self.executor._call_llm_and_update_tokens(
            prompt=retry_prompt,
            reason=f"Generating strategic meta-plan ({label})",
            disabled_history=True,
            active_prompt_name_for_filter=self.executor.active_prompt_name,
            source=self.executor.source,
            current_provider=strategic_provider,
            current_model=strategic_model,
            planning_phase="strategic",
            # The retry prompt IS the planning prompt plus a note: it carries its own capability
            # catalog, so the master system prompt's copy is the same pure duplicate the first
            # attempt strips (it sent 24.6K tokens where the first sent 2.3K), and it shares the
            # first attempt's stable prefix.
            strip_capabilities=True,
            cache_prefix=getattr(self, "_planning_cache_prefix", None),
        )
        yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")
        # §4 cost-transparency fix: this retry's tokens ARE billed (accumulated into the turn) but
        # previously emitted NO cost event, so its cost was invisible to _getTurnCost's event-sum
        # (which sums the persisted "Calling LLM for Planning" system_events, NOT token_updates)
        # → a [Cost Verification] drift. Mirror the primary strategic call: a fresh-call_id
        # system_event (reload event-sum) + a token_update (live counters).
        _retry_call_id = str(uuid.uuid4())
        _retry_cost = self.executor._last_call_metadata.get("cost_usd", 0)
        _retry_event = {"step": "Calling LLM for Planning", "type": "system_message", "details": {
            "summary": f"Strategic meta-plan ({label})",
            "call_id": _retry_call_id,
            "execution_depth": self.executor.execution_depth,
            "input_tokens": _retry_in, "output_tokens": _retry_out,
            "cost_usd": _retry_cost, "planning_phase": "strategic",
        }}
        self.executor._log_system_event(_retry_event)
        yield self.executor._format_sse_with_depth(_retry_event)
        _retry_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
        if _retry_session:
            yield self.executor._format_sse_with_depth({
                "statement_input": _retry_in, "statement_output": _retry_out,
                "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens,
                "total_input": _retry_session.get("input_tokens", 0), "total_output": _retry_session.get("output_tokens", 0),
                "call_id": _retry_call_id, "cost_usd": _retry_cost,
                "planning_phase": "strategic", "provider": strategic_provider, "model": strategic_model
            }, "token_update")
        self._strategic_retry_text = response_text

    async def _generate_meta_plan(self, force_disable_history: bool = False, replan_context: str = None):
        """The universal planner. It generates a meta-plan for ANY request."""
        prompt_obj = None
        explicit_parameters_section = ""

        if self.executor.active_prompt_name:
            event_data = {"step": "Loading Workflow Prompt", "type": "system_message", "details": f"Loading '{self.executor.active_prompt_name}'"}
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            # A prompt the agent has switched off is never run — the ONE rule the profile editor,
            # the resource panel and the runtime share (core/profile_selection.py).
            from trusted_data_agent.core.profile_selection import disabled_prompt_refusal
            _refusal = disabled_prompt_refusal(
                getattr(self.executor, "active_profile_id", None), self.executor.user_uuid,
                self.executor.active_prompt_name)
            if _refusal:
                raise ValueError(_refusal)
            mcp_client = self.executor.dependencies['STATE'].get('mcp_client')
            if not mcp_client: raise RuntimeError("MCP client is not connected.")

            prompt_def = self.executor._get_prompt_info(self.executor.active_prompt_name)

            if not prompt_def:
                raise ValueError(f"Could not find a definition for prompt '{self.executor.active_prompt_name}' in the local cache.")

            required_args = {arg['name'] for arg in prompt_def.get('arguments', []) if arg.get('required')}

            enriched_args = self.executor.prompt_arguments.copy()

            missing_args = {arg for arg in required_args if arg not in enriched_args or enriched_args.get(arg) is None}
            if missing_args:
                raise ValueError(
                    f"Cannot execute prompt '{self.executor.active_prompt_name}' because the following required arguments "
                    f"are missing: {missing_args}"
                )

            self.executor.prompt_arguments = enriched_args

            try:
                # Use server ID instead of name for session management
                server_id = APP_CONFIG.CURRENT_MCP_SERVER_ID
                # PROP-006 phase 2 (O3): a prompt of an ADDITIONAL server is fetched from that server,
                # under its own name
                from trusted_data_agent.mcp_adapter.adapter import resolve_mcp_target
                _p_client, server_id, _p_name = resolve_mcp_target(
                    self.executor.active_prompt_name, mcp_client, server_id, self.executor.user_uuid)
                if not server_id:
                    raise RuntimeError("MCP server ID is not configured.")
                async with _p_client.session(server_id) as temp_session:
                    prompt_obj = await load_mcp_prompt(
                        temp_session, name=_p_name, arguments=self.executor.prompt_arguments
                    )
            except Exception as e:
                app_logger.error(f"Failed to load MCP prompt '{self.executor.active_prompt_name}': {e}", exc_info=True)
                raise ValueError(f"Prompt '{self.executor.active_prompt_name}' could not be loaded from the MCP server.") from e

            if not prompt_obj: raise ValueError(f"Prompt '{self.executor.active_prompt_name}' could not be loaded.")

            self.executor.workflow_goal_prompt = get_prompt_text_content(prompt_obj)
            if not self.executor.workflow_goal_prompt:
                raise ValueError(f"Could not extract text content from rendered prompt '{self.executor.active_prompt_name}'.")

            param_items = [f"- {key}: {json.dumps(value)}" for key, value in self.executor.prompt_arguments.items()]
            explicit_parameters_section = (
                "\n--- EXPLICIT PARAMETERS ---\n"
                "The following parameters were explicitly provided for this prompt execution:\n"
                + "\n".join(param_items) + "\n"
            )
        else:
            self.executor.workflow_goal_prompt = self.executor.original_user_input

        call_id = str(uuid.uuid4())
        summary = f"Generating a strategic meta-plan for the goal"
        
        # --- MODIFICATION START: Defer payload creation until after LLM call ---
        # details_payload = {
        #     "summary": summary,
        #     "full_text": self.executor.workflow_goal_prompt,
        #     "call_id": call_id,
        #     "execution_depth": self.executor.execution_depth
        # }
        # event_data = {"step": "Calling LLM for Planning", "type": "system_message", "details": details_payload}
        # self.executor._log_system_event(event_data)
        # yield self.executor._format_sse_with_depth(event_data)
        # --- MODIFICATION END ---


        # §4 perf: the legacy history summary (deepcopy every turn + json.dumps(indent=2) of the whole
        # trace) is used ONLY by the legacy `_tv` fallback below — the default ContextBuilder path
        # builds its own turn_action_history. Compute it LAZILY, only when the fallback is taken.
        _previous_turn_summary_cache = {"str": None}
        def _get_previous_turn_summary():
            if _previous_turn_summary_cache["str"] is None:
                _previous_turn_summary_cache["str"] = self._create_summary_from_history(self.executor.previous_turn_data)
            return _previous_turn_summary_cache["str"]

        active_prompt_context_section = ""
        # PROP-011 — Directive 2a (plan_type: input_required) exists only while the model may ask
        try:
            from trusted_data_agent.agent import input_requests as _ir_carrier
            _needs_input_carrier = _ir_carrier.carrier_section("plan")
        except Exception:  # noqa: BLE001
            _needs_input_carrier = ""
        if self.executor.active_prompt_name:
            active_prompt_context_section = f"\n- Active Prompt: You are currently executing the '{self.executor.active_prompt_name}' prompt. Do not call it again."

        constraints_section = self.executor.dependencies['STATE'].get("constraints_context", "")

        sql_consolidation_rule_str = ""
        opt_prompts = APP_CONFIG.SQL_OPTIMIZATION_PROMPTS
        opt_tools = APP_CONFIG.SQL_OPTIMIZATION_TOOLS

        if opt_prompts or opt_tools:
            favored_capabilities = []
            if opt_prompts:
                favored_capabilities.extend([f"`{p}` (prompt)" for p in opt_prompts])
            if opt_tools:
                favored_capabilities.extend([f"`{t}` (tool)" for t in opt_tools])

            sql_consolidation_rule_str = (
                "**CRITICAL STRATEGY (SQL Consolidation):** Before creating a multi-step plan, first consider if the user's entire request can be fulfilled with a single, consolidated SQL query. "
                "If the goal involves a sequence of filtering, joining, or looking up data (e.g., \"find all tables in a database that contains X\"), you **MUST** favor using one of the following capabilities "
                f"to write a single statement that performs the entire operation: {', '.join(favored_capabilities)}. "
                "Avoid creating multiple `base_...List` steps if a single query would be more efficient."
            )

        reporting_tool_name_injection = ""
        if self.executor.source == 'prompt_library':
            reporting_tool_name_injection = "TDA_ComplexPromptReport"
        else:
            reporting_tool_name_injection = "TDA_FinalReport"

        # --- KNOWLEDGE REPOSITORIES: Domain knowledge context (retrieved FIRST to inform RAG decision) ---
        knowledge_context_str = ""
        if self.rag_retriever and APP_CONFIG.KNOWLEDGE_RAG_ENABLED:
            # Get profile configuration for knowledge settings
            profile_config = None
            # Use executor's active_profile_id which is already correctly set to either
            # the override profile or the default profile at initialization (executor.py line 103)
            if self.executor.active_profile_id and self.executor.active_profile_id != "__system_default__":
                try:
                    from trusted_data_agent.core.config_manager import get_config_manager
                    config_manager = get_config_manager()
                    profiles = config_manager.get_profiles(self.executor.user_uuid)

                    # Use the active profile directly - it's already the correct profile
                    profile_id = self.executor.active_profile_id

                    if profile_id:
                        profile_config = next((p for p in profiles if p.get("id") == profile_id), None)
                except Exception as e:
                    app_logger.warning(f"Failed to get profile for knowledge retrieval: {e}")
            
            # Retrieve knowledge documents
            knowledge_docs = await self._retrieve_knowledge_for_planning(
                query=self.executor.original_user_input,
                profile_config=profile_config
            )
            
            if knowledge_docs:
                knowledge_context_str = f"\n\n--- KNOWLEDGE CONTEXT ---\nThe following domain knowledge may be relevant to your planning:\n\n{knowledge_docs}\n"
                app_logger.info("Retrieved knowledge context for planning")
                
                # Yield status event for knowledge retrieval (visible in Live Status Window)
                if hasattr(self, 'tracked_knowledge_collections') and self.tracked_knowledge_collections:
                    # Get the actual document details for the event
                    knowledge_chunks = []
                    if hasattr(self, 'tracked_knowledge_results'):
                        for doc in self.tracked_knowledge_results:
                            knowledge_chunks.append({
                                "collection_name": doc.get("collection_name"),
                                "content": doc.get("content", ""),
                                "similarity_score": doc.get("similarity_score", 0),
                                "document_id": doc.get("document_id"),
                                "chunk_index": doc.get("chunk_index", 0)
                            })
                    
                    event_details = {
                        "summary": f"Retrieved {self.tracked_knowledge_doc_count} relevant documents from {len(self.tracked_knowledge_collections)} knowledge collection(s)",
                        "collections": self.tracked_knowledge_collections,
                        "document_count": self.tracked_knowledge_doc_count,
                        "chunks": knowledge_chunks
                    }
                    
                    # Yield SSE event for live UI display
                    yield self.executor._format_sse_with_depth({
                        "step": "Knowledge Retrieved",
                        "type": "knowledge_retrieval",
                        "details": event_details
                    })
                    
                    # Call event handler to capture event for turn summary storage
                    if self.event_handler:
                        await self.event_handler(event_details, "knowledge_retrieval")
            else:
                app_logger.debug("No knowledge context retrieved for planning")

        # --- COMPONENT CONTEXT ENRICHMENT (knowledge graph guardrail) ---
        kg_enrichment_text = await self.executor._get_kg_enrichment()
        kg_schema_directive_str = ""  # Empty by default — no tokens wasted when KG absent
        # Findings — the verified results of earlier turns (the `findings` module), re-read
        # not recalled: a count the last turn returned need not be queried again, and a
        # figure the planner repeats from it is vouched for at the grounding gate.
        _findings_text = self.executor._session_context_blocks()   # memory, then findings
        if _findings_text:
            knowledge_context_str += "\n\n" + _findings_text
        if kg_enrichment_text:
            knowledge_context_str += "\n\n" + kg_enrichment_text
            kg_schema_directive_str = (
                "CRITICAL RULE (Schema-Aware SQL Construction): A KNOWLEDGE GRAPH CONTEXT section is present below. "
                "You MUST validate ALL SQL queries in your plan against it:\n"
                "- Only reference column names that the TABLE SCHEMAS section shows belong to the target table. "
                "A column listed under one table does NOT exist in another table unless explicitly listed there.\n"
                "- If a column appears in multiple tables (see JOINABLE COLUMNS), construct JOINs through the correct "
                "intermediate table — do NOT assume two tables can be directly joined unless they both share a column name.\n"
                "- NEVER assume a column exists in a table unless the TABLE SCHEMAS section explicitly lists it under that table."
            )
            app_logger.info("Component context enrichment injected into planner knowledge context")

            yield self.executor._format_sse_with_depth({
                "step": "Knowledge Graph Enrichment",
                "type": "kg_enrichment",
                "details": self.executor.kg_enrichment_event
            })

            if self.event_handler:
                await self.event_handler(self.executor.kg_enrichment_event, "kg_enrichment")
        else:
            app_logger.debug("KG enrichment returned empty — no schema context injected into planner")

        # --- S6 The Governed Tool Surface: armed before assembly (the executor); HERE, after
        # the enrichment named the tables in play, the selection is RE-RUN with that vocabulary
        # (offered can only grow) and the overlay + the assembled catalog are refreshed before
        # the catalog is read below. Nothing armed (switch off / mode all) ⇒ untouched.
        try:
            from trusted_data_agent.agent import tool_surface as _ts_mod
            if _ts_mod.current() is None:
                self.executor._arm_tool_surface_optimize()   # a lane that skipped the pre-assembly seam
                _grew = _ts_mod.current() is not None
            else:
                _grew = self.executor._reselect_tool_surface()
            if _grew:
                _sev = self.executor.surface_event()
                if _sev:
                    self.executor._log_system_event(_sev)
                    yield self.executor._format_sse_with_depth(_sev)
        except Exception as _surf_err:  # noqa: BLE001
            app_logger.debug(f"[surface] planner re-select skipped: {_surf_err}")

        # --- PLANNER REPOSITORIES: Few-shot examples for execution patterns ---
        # Include RAG examples alongside knowledge context - Directive 3 will decide the best approach
        rag_few_shot_examples_str = ""
        retrieved_cases = None  # Initialize before conditional block

        _no_planner_bound = False
        if self.rag_retriever:
            # Determine which collections to query based on profile
            allowed_collection_ids = None
            # Use executor's active_profile_id which is already correctly set to either
            # the override profile or the default profile at initialization (executor.py line 103)
            if self.executor.active_profile_id and self.executor.active_profile_id != "__system_default__":
                try:
                    from trusted_data_agent.core.config_manager import get_config_manager
                    config_manager = get_config_manager()
                    profiles = config_manager.get_profiles(self.executor.user_uuid)

                    # Use the active profile directly - it's already the correct profile
                    profile_id = self.executor.active_profile_id

                    if profile_id:
                        profile = next((p for p in profiles if p.get("id") == profile_id), None)
                        if profile:
                            # Use ragCollections instead of autocompleteCollections for RAG retrieval filtering
                            _scope = planner_binding_scope(profile)
                            if _scope == "none":
                                _no_planner_bound = True
                                app_logger.info(f"RAG retrieval: no planner repository bound (profile: {profile.get('name')})")
                            elif _scope != "all":
                                allowed_collection_ids = _scope
                                app_logger.info(f"RAG retrieval filtered to collections: {allowed_collection_ids} (profile: {profile.get('name')})")
                except Exception as e:
                    app_logger.warning(f"Failed to get profile collections for RAG filtering: {e}")
            
            # --- MODIFICATION START: Create RAGAccessContext for user-aware retrieval ---
            rag_context = RAGAccessContext(
                user_id=self.executor.user_uuid,
                retriever=self.rag_retriever
            )
            # What this agent LEARNS is read back: a profile bound to a planner the user
            # cannot write (a subscribed view, a locked repository) seeds into the user's
            # own default planner for this server — consult it too (one rule, the mirror
            # of the seeding fallback in rag_retriever).
            # An agent with its OWN learning repository reads it beside what it binds — and
            # never the shared notebook (OC-F5, core/agent_learning). Not bound, so not hashed
            # into its signature; every champion still names its repository on the turn.
            _own_repo = None
            try:
                from trusted_data_agent.core import agent_learning as _al
                if _al.learns_own(self.executor.active_profile_id, self.executor.user_uuid):
                    _own_repo = _al.read_repository(
                        self.executor.active_profile_id,
                        self.executor.effective_mcp_server_id or APP_CONFIG.CURRENT_MCP_SERVER_ID,
                        self.executor.user_uuid)
                    _own_repo = _own_repo if _own_repo is not None else False   # own mode, none here yet
            except Exception as _al_err:  # noqa: BLE001
                app_logger.warning(f"agent learning repository lookup skipped: {_al_err}")
            if _own_repo:
                try:
                    self.rag_retriever.ensure_loaded(int(_own_repo))
                except Exception:  # noqa: BLE001
                    pass
                if _no_planner_bound:
                    _no_planner_bound, allowed_collection_ids = False, {_own_repo}
                elif allowed_collection_ids is not None:
                    allowed_collection_ids = set(allowed_collection_ids) | {_own_repo}
                app_logger.info(f"RAG retrieval reads the agent's own learning repository {_own_repo} "
                                f"(collections: {allowed_collection_ids if allowed_collection_ids is not None else 'all bound by wildcard'})")
            if _own_repo is None:   # own mode: the shared read-back does not apply
                try:
                    from trusted_data_agent.agent.rag_retriever import read_back_own_learning
                    from trusted_data_agent.core.config_manager import get_config_manager as _gcm
                    _widened = read_back_own_learning(
                        allowed_collection_ids, rag_context,
                        self.executor.effective_mcp_server_id or APP_CONFIG.CURRENT_MCP_SERVER_ID, _gcm())
                    if _widened is not allowed_collection_ids:
                        for _cid in set(_widened) - set(allowed_collection_ids or ()):
                            try:
                                self.rag_retriever.ensure_loaded(int(_cid))
                            except Exception:  # noqa: BLE001
                                pass
                        allowed_collection_ids = _widened
                except Exception as _rb_err:  # noqa: BLE001
                    app_logger.warning(f"read-back of own learning skipped: {_rb_err}")

            try:
                _obda_prof = self.executor._resolve_gate_profile("obda")
            except Exception:  # noqa: BLE001
                _obda_prof = None
            retrieved_cases = None if _no_planner_bound else await self.rag_retriever.retrieve_examples(
                query=self.executor.original_user_input,
                k=APP_CONFIG.RAG_NUM_EXAMPLES,
                allowed_collection_ids=allowed_collection_ids,
                rag_context=rag_context,  # --- MODIFICATION: Pass context ---
                obda_profile=_obda_prof,   # per-agent Option A filters OBDA-READ champions
                # Ontology metrics in play this turn: a champion whose SQL contradicts
                # a metric the question names is NOT offered (0-token filter).
                kg_metrics=[m for enr in ((self.executor.kg_enrichment_event or {}).get("enrichments") or [])
                            for m in ((enr or {}).get("metrics") or [])],
                repository_type="planner"  # Explicitly retrieve from planner repositories
            )
            # --- MODIFICATION END ---
            if retrieved_cases:
                if self.event_handler:
                    # Send the full case data of the first (most relevant) case
                    await self.event_handler({
                        "case_id": retrieved_cases[0]['case_id'],
                        "full_case_data": retrieved_cases[0]['full_case_data'],
                        # how many champions matched — the event is emitted ONLY on a match,
                        # so a reader must never take an absent count for zero
                        "count": len(retrieved_cases),
                    }, "rag_retrieval")
                # Add adaptive guidance header when RAG cases exist
                adaptive_header = """### Retrieved RAG Examples (Adapt to Current Request)

**CRITICAL**: These are proven patterns to inform your strategy, NOT templates to copy exactly.
- **Priority**: User's explicit requirements (charts, exports, formats) > RAG pattern
- **If user adds new steps**: You MUST include them even if RAG case doesn't have them
- **Example**: RAG shows "query→report" but user asks "query→chart→report" → chart phase is MANDATORY

"""
                formatted_examples = [self.rag_retriever._format_few_shot_example(case) for case in retrieved_cases]
                rag_few_shot_examples_str = "\n\n" + adaptive_header + "\n".join(formatted_examples) + "\n\n"
                app_logger.debug(f"Retrieved RAG cases for few-shot examples: {[case['case_id'] for case in retrieved_cases]}")

        # A signed seed asked word for word is replayed verbatim — no planning call (see
        # `exact_signed_champion`). Decided here, applied where the model would be called.
        _verbatim = None
        if retrieved_cases:
            try:
                from trusted_data_agent.core.collection_db import get_collection_db as _gcd_v
                _verbatim = exact_signed_champion(
                    self.executor.original_user_input, retrieved_cases,
                    lambda _cid: planner_replayable(_cid, _gcd_v().get_collection_by_id(_cid)))
            except Exception as _vx:  # noqa: BLE001 — a lookup failure means re-plan as before
                app_logger.warning(f"signed-seed replay check skipped: {_vx}")
                _verbatim = None

        # MCP system name: cached read (was a synchronous sqlite open+query on the
        # planning hot path, under the process-wide SQLite lock, every turn).
        from trusted_data_agent.llm.handler import get_mcp_system_name
        mcp_system_name = get_mcp_system_name()

        # Get tools and prompts context from the executor's STATE. For an @TAG override this is a
        # per-executor overlay carrying the override's catalog; for a normal turn it is the global
        # APP_STATE. Reading it here (not the global directly) is what keeps a concurrent turn from
        # seeing another turn's overridden tools_context/prompts_context.
        from trusted_data_agent.core.config import APP_STATE
        _plan_state = self.executor.dependencies.get('STATE', APP_STATE) if isinstance(getattr(self.executor, 'dependencies', None), dict) else APP_STATE
        tools_context = _plan_state.get('tools_context', '--- No Tools Available ---')
        prompts_context = _plan_state.get('prompts_context', '--- No Prompts Available ---')

        # Build component_tools context for strategic planner.
        # Component tools (TDA_Charting, etc.) are managed by ComponentManager
        # and need a dedicated section since they're not in mcp_tools/tools_context.
        # This is component-agnostic — automatically discovers all active components.
        component_tools_str = ""
        try:
            from trusted_data_agent.components.manager import get_component_manager
            comp_manager = get_component_manager()
            if comp_manager:
                profile_config = self.executor._get_profile_config()
                component_tool_defs = comp_manager.get_tool_definitions(
                    profile_config, getattr(self.executor, "user_uuid", None)
                )
                if component_tool_defs:
                    parts = ["--- Available Component Tools ---"]
                    for tool_def in component_tool_defs:
                        tool_str = f"- `{tool_def['name']}` (tool): {tool_def.get('description', 'No description.')}"
                        args = tool_def.get("args", {})
                        if args:
                            tool_str += "\n  - Arguments:"
                            for arg_name, arg_details in args.items():
                                if isinstance(arg_details, dict):
                                    arg_type = arg_details.get('type', 'any')
                                    is_required = arg_details.get('required', False)
                                    req_str = "required" if is_required else "optional"
                                    arg_desc = arg_details.get('description', 'No description.')
                                    tool_str += f"\n    - `{arg_name}` ({arg_type}, {req_str}): {arg_desc}"
                        parts.append(tool_str)
                    component_tools_str = "\n".join(parts)
                    app_logger.info(f"Built component_tools context with {len(component_tool_defs)} component tool(s)")
        except Exception as e:
            app_logger.warning(f"Failed to build component_tools context: {e}")

        app_logger.debug(f"tools_context length: {len(tools_context)} characters; "
                         f"TDA_CurrentDate present: {'TDA_CurrentDate' in tools_context}")

        # CRITICAL FIX: Inject previous query context for chart-only continuation queries
        # This ensures the strategic planner preserves constraints (LIMIT, WHERE, ORDER BY)
        # when re-fetching data for visualization requests
        chart_context_injection = ""
        _wf_prev = (self.executor.previous_turn_data or {}).get("workflow_history", []) if self.executor.previous_turn_data else []
        _prev_query = (_wf_prev[-1].get("user_query", "") if _wf_prev and isinstance(_wf_prev[-1], dict) else "")
        if self.executor.previous_turn_data and self._is_chart_only_query(self.executor.original_user_input, _prev_query):
            workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
            if workflow_history:
                last_turn = workflow_history[-1]
                previous_query = last_turn.get("user_query", "")

                if previous_query:
                    chart_context_injection = f"""
**IMPORTANT CONTEXT - CHART CONTINUATION REQUEST:**
The user's previous query was: "{previous_query}"
The current request is to VISUALIZE that data.

CRITICAL REQUIREMENTS:
1. If you need to re-fetch data, preserve ALL constraints from the previous query:
   - LIMIT clauses (e.g., top 5, top 10)
   - WHERE filters
   - ORDER BY clauses
   - Any grouping or aggregations
2. The chart should show the SAME data set that was returned in the previous turn.
3. Do NOT generate a broader query that returns more rows than the original.

"""
                    app_logger.info(
                        f"Chart-only query detected: Injecting previous context to preserve constraints. "
                        f"Previous query: '{previous_query[:100]}...'"
                    )

        # === ContextBuilder integration (Phase 3a) ===
        # Use the builder for budget-aware module content (tools, history,
        # components) where available.  Knowledge/RAG stay manually gathered
        # because their retrieval emits SSE events the builder can't replicate.
        _builder = getattr(self.executor, 'context_builder', None)
        if _builder and _builder.has_assembled_context:
            try:
                prompt_ctx = await _builder.build("strategic", {
                    "workflow_goal": self.executor.workflow_goal_prompt,
                    "explicit_parameters_section": explicit_parameters_section,
                    "original_user_input": self.executor.original_user_input,
                    "execution_depth": self.executor.execution_depth,
                    "active_prompt_context_section": active_prompt_context_section,
                    "needs_input_carrier": _needs_input_carrier,
                    "replan_instructions": replan_context or "",
                    "constraints_section": constraints_section,
                    "source": self.executor.source,
                })
                _tv = prompt_ctx.template_vars
                # Override SSE-event-dependent vars with manually gathered values
                _tv["knowledge_context"] = knowledge_context_str
                _tv["rag_few_shot_examples"] = rag_few_shot_examples_str
                _tv["kg_schema_directive"] = kg_schema_directive_str
                app_logger.info(
                    f"Strategic planning using ContextBuilder "
                    f"(tokens: {prompt_ctx.tokens_used:,}, source: {prompt_ctx.source})"
                )
                # Emit per-call snapshot for observability + persist for reload
                if prompt_ctx.snapshot:
                    # scope marks a per-call subset (not the turn's full assembly),
                    # so the resource-panel badge never reads it as the turn's utilization.
                    _strat_snap = {**prompt_ctx.snapshot.to_sse_event(), "scope": "strategic"}
                    self.executor.strategic_context_snapshot_event = _strat_snap
                    yield self.executor._format_sse_with_depth({
                        "step": "Context Assembly (Strategic)",
                        "type": "context_window_snapshot",
                        "payload": _strat_snap,
                    })
            except Exception as _cb_err:
                app_logger.warning(f"ContextBuilder strategic build failed, using legacy: {_cb_err}")
                _tv = None
        else:
            _tv = None

        if _tv is None:
            # Legacy fallback: manual gathering (pre-ContextBuilder path). A multi-server agent's
            # sources note reaches the planner on THIS path too (the context-window module adds
            # it on the other) — the one text source_routing.turn_sources_note builds.
            try:
                from trusted_data_agent.agent import source_routing as _srt_note
                _sources_note = _srt_note.turn_sources_note()
            except Exception:  # noqa: BLE001
                _sources_note = None
            if _sources_note and tools_context:
                tools_context = f"{_sources_note}\n\n{tools_context}"
            _tv = {
                "workflow_goal": self.executor.workflow_goal_prompt,
                "explicit_parameters_section": explicit_parameters_section,
                "original_user_input": self.executor.original_user_input,
                "turn_action_history": _get_previous_turn_summary(),  # §4: lazy — legacy path only
                "execution_depth": self.executor.execution_depth,
                "active_prompt_context_section": active_prompt_context_section,
                "needs_input_carrier": _needs_input_carrier,
                "mcp_system_name": mcp_system_name,
                "replan_instructions": replan_context or "",
                "constraints_section": constraints_section,
                "sql_consolidation_rule": sql_consolidation_rule_str,
                "reporting_tool_name": reporting_tool_name_injection,
                "rag_few_shot_examples": rag_few_shot_examples_str,
                "knowledge_context": knowledge_context_str,
                "kg_schema_directive": kg_schema_directive_str,
                "available_tools": tools_context,
                "available_prompts": prompts_context,
                "component_tools": component_tools_str,
            }

        _rendered_plan_prompt = WORKFLOW_META_PLANNING_PROMPT.format(**_tv)
        planning_prompt = chart_context_injection + _rendered_plan_prompt

        # --- Inject skill content into planning prompt (pre-processing) ---
        if self.executor.skill_result and self.executor.skill_result.has_content:
            sp_block = self.executor.skill_result.get_system_prompt_block()
            if sp_block:
                planning_prompt = f"{planning_prompt}\n\n{sp_block}"
            uc_block = self.executor.skill_result.get_user_context_block()
            if uc_block:
                planning_prompt = f"{uc_block}\n\n{planning_prompt}"

        # The cache plan (PROMPT_CACHE_ARCHITECTURE.md): everything before the first value
        # that changes with the turn is this agent's stable prefix — the skills, the chart
        # note, the template's directives and the catalogs that do not depend on the question.
        planning_cache_prefix = _strategic_cache_prefix(planning_prompt, _rendered_plan_prompt, _tv)
        self._planning_cache_prefix = planning_cache_prefix   # the retries share it

        yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

        # Use strategic model for meta-planning (dual-model feature)
        strategic_provider = self.executor.strategic_provider
        strategic_model = self.executor.strategic_model

        if _verbatim is not None:
            # The signed seed IS the plan: 0 tokens, no model, the trace says so.
            response_text, input_tokens, output_tokens = json.dumps(_verbatim["strategy"]), 0, 0
            summary = ("Signed seed replayed verbatim — the question matches a champion of a locked "
                       "planner word for word; no planning call was made")
            self.executor._last_call_metadata = {"cached_tokens": 0, "cost_usd": 0}
            _rv_event = {"step": "Signed seed replayed verbatim", "type": "system_message", "details": {
                "summary": summary, "case_id": _verbatim["case_id"], "collection_id": _verbatim["collection_id"],
                "phase_count": len(_verbatim["strategy"].get("phases") or []),
                "execution_depth": self.executor.execution_depth}}
            self.executor._log_system_event(_rv_event)
            yield self.executor._format_sse_with_depth(_rv_event)
            app_logger.info(f"[Strategic Planning] signed seed {_verbatim['case_id']} (planner {_verbatim['collection_id']}) "
                            f"replayed verbatim for an exact question — no LLM call")
        else:
            response_text, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
                prompt=planning_prompt,
                reason=f"Generating a strategic meta-plan for the goal: '{self.executor.workflow_goal_prompt[:100]}'",
                disabled_history=True,
                active_prompt_name_for_filter=self.executor.active_prompt_name,
                source=self.executor.source,
                current_provider=strategic_provider,  # NEW: Strategic model override
                current_model=strategic_model,        # NEW: Strategic model override
                planning_phase="strategic",           # NEW: Identify as strategic planning call
                # The planning prompt carries its own budget-managed capability catalog;
                # the master system prompt's copy is a pure duplicate on this call.
                strip_capabilities=True,
                cache_prefix=planning_cache_prefix,
                # A provider that fails must SURFACE, not return "": the empty answer read as a
                # truncated plan, the parse retry paid a second call on a dead provider, and the
                # turn reported "internal error (RuntimeError)" instead of the provider's failure
                # (trust campaign F1/F2 on Optimize). A real (truncated) answer still parse-retries.
                raise_on_error=True,
                # No user_uuid/session_id needed here directly as _call_llm takes from self.executor
            )
        yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

        # Log dual-model usage
        if self.executor.is_dual_model_active:
            app_logger.info(f"[Strategic Planning] Used {strategic_provider}/{strategic_model}")

        # --- MODIFICATION START: Build payload *after* LLM call to include tokens ---
        details_payload = {
            "summary": summary,
            "full_text": self.executor.workflow_goal_prompt,
            "call_id": call_id,
            "execution_depth": self.executor.execution_depth,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": self.executor._last_call_metadata.get("cached_tokens", 0),
            "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0),
            "planning_phase": "strategic"
        }
        event_data = {"step": "Calling LLM for Planning", "type": "system_message", "details": details_payload}
        self.executor._log_system_event(event_data)
        yield self.executor._format_sse_with_depth(event_data)
        # --- MODIFICATION END ---

        # Log summary of planning (verbose details logged at debug level)
        rag_case_count = len(retrieved_cases) if retrieved_cases else 0
        # A plan guided by a proven champion carries SQL that already worked: the
        # schema_before_sql pass leaves it alone.
        self._champion_guided = bool(rag_case_count)
        try:
            from trusted_data_agent.agent import schema_facts as _sf_c
            self._champion_tables = set().union(
                *[_sf_c.tables_in_case(c) for c in (retrieved_cases or [])]) if retrieved_cases else set()
        except Exception:  # noqa: BLE001 — unknown ⇒ the pass stands down for the whole plan
            self._champion_tables = None
        try:
            self._champion_databases = set().union(
                *[_sf_c.databases_in_case(c) for c in (retrieved_cases or [])]) if retrieved_cases else set()
        except Exception:  # noqa: BLE001 — unknown ⇒ the guided reading stands down
            self._champion_databases = None
        app_logger.info(f"Meta-planner generated plan (RAG cases: {rag_case_count}, depth: {self.executor.execution_depth})")
        app_logger.debug(f"Generated plan: {response_text[:500]}..." if len(response_text) > 500 else f"Generated plan: {response_text}")

        # --- MODIFICATION START: Pass user_uuid to get_session ---
        # Get user_uuid and session_id from the executor instance
        user_uuid = self.executor.user_uuid
        session_id = self.executor.session_id
        updated_session = await session_manager.get_session(user_uuid, session_id)
        # --- MODIFICATION END ---
        if updated_session:
            yield self.executor._format_sse_with_depth({ "statement_input": input_tokens, "statement_output": output_tokens, "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens, "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0), "call_id": call_id, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0), "planning_phase": "strategic", "provider": strategic_provider, "model": strategic_model }, "token_update")

        # OC-F16 (Optimize campaign, 2026-10-29): an empty or truncated response ("[{" — 51 output
        # tokens billed, two characters delivered) ended the turn as fatal here, before the
        # parse-error retry below ever ran. It IS that case: nothing parses, so it is retried
        # once; only a retry that also fails ends the turn (the `plan_object is None` raise).
        response_text = response_text or ""
        if len(response_text.strip()) < 3:
            app_logger.warning(f"Strategic planner returned an empty or truncated response ({response_text!r}); retrying once.")
        plan_object = parse_plan_response(response_text)

        if plan_object is None:
            # Inject the parse failure back to the LLM and retry once.
            app_logger.warning("Strategic plan JSON parse failed on first attempt. Retrying with error context.")
            yield self.executor._format_sse_with_depth({
                "step": "System Correction", "type": "workaround",
                "details": "Strategic plan response was not valid JSON. Retrying with error context."
            })
            retry_prompt = planning_prompt + (
                "\n\nIMPORTANT: Your previous response could not be parsed as valid JSON.\n"
                f"Partial response received:\n---\n{response_text[:400]}\n---\n"
                "You MUST respond with ONLY a valid JSON array of phase objects. "
                "No explanatory text, no markdown fences, no trailing commas."
            )
            async for _ev in self._strategic_retry(retry_prompt, "parse-error retry", strategic_provider, strategic_model):
                yield _ev
            response_text = self._strategic_retry_text or ""
            plan_object = parse_plan_response(response_text)

        if plan_object is None:
            raise RuntimeError(
                f"Failed to generate a valid meta-plan after 2 attempts. "
                f"Last response: {response_text[:200]}"
            )

        async for _ev in self._screen_conversational(plan_object, response_text, planning_prompt,
                                                     strategic_provider, strategic_model):
            yield _ev
        response_text, plan_object = self._screened

        if isinstance(plan_object, dict) and plan_object.get("plan_type") == "conversational":
            self.executor.is_conversational_plan = True
            self.executor.temp_data_holder = plan_object.get("response", "I'm sorry, I don't have a response for that.")
            event_data = {"step": "Conversational Response Identified", "type": "system_message", "details": self.executor.temp_data_holder}
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            return

        # PROP-011 (phase 2): the planner's carrier of the model's act — `plan_type:
        # input_required` with the question. NO phases exist, so nothing has to be refused: the
        # turn goes to summarization with the question as its answer (the conversational path),
        # the ledger holds the question, the turn ends `input_required`. A question the one
        # validator refuses falls back to an honest conversational answer naming the gap.
        if isinstance(plan_object, dict) and plan_object.get("plan_type") == "input_required":
            from trusted_data_agent.agent import input_requests as _ir_mod
            _ask = plan_object.get("ask") or plan_object.get("question") or {}
            if isinstance(_ask, str):
                _ask = {"kind": "provide", "question": _ask}
            _rec, _note = _ir_mod.raise_from_act(_ask, source="planner")
            self.executor.is_conversational_plan = True
            if _rec is not None:
                self.executor.temp_data_holder = _ir_mod.question_text(_rec)
                event_data = {"step": "Plan Ended at a Question", "type": "system_message",
                              "details": f"The planner asked the user instead of planning around a missing input: {self.executor.temp_data_holder}"}
            else:
                self.executor.temp_data_holder = (f"I need something only you can tell me before I can plan this: "
                                                  f"{str(_ask.get('question') or '').strip() or 'a detail the request leaves open'}.")
                event_data = {"step": "Plan Ended at a Question", "type": "workaround",
                              "details": f"The planner's question could not be raised ({_note}); answering honestly instead."}
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            return

        plan_object_is_dict = isinstance(plan_object, dict)
        is_direct_tool = plan_object_is_dict and "tool_name" in plan_object
        is_direct_prompt = plan_object_is_dict and ("prompt_name" in plan_object or "executable_prompt" in plan_object)

        if is_direct_tool or is_direct_prompt:
            event_data = {
                "step": "System Correction",
                "type": "workaround",
                "details": "Planner returned a direct action instead of a plan. System is correcting the format."
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)

            phase = {
                "phase": 1,
                "goal": f"Execute the action for the user's request: '{self.executor.original_user_input}'",
                "arguments": plan_object.get("arguments", {})
            }
            if is_direct_tool:
                phase["relevant_tools"] = [plan_object["tool_name"]]
            elif is_direct_prompt:
                phase["executable_prompt"] = plan_object.get("prompt_name") or plan_object.get("executable_prompt")
            self.executor.meta_plan = [phase]
        elif plan_object_is_dict and any(
                isinstance(plan_object.get(k), list) and plan_object.get(k)
                for k in ("plan", "phases", "steps")):
            # §2 fix: weak models routinely wrap the plan list in a dict — {"plan": [...]},
            # {"phases": [...]}, {"steps": [...]}. Unwrap it instead of hard-failing with a
            # RuntimeError over a shape that's trivially recoverable.
            _wrapped = next(plan_object[k] for k in ("plan", "phases", "steps")
                            if isinstance(plan_object.get(k), list) and plan_object.get(k))
            app_logger.info(f"Unwrapped a dict-wrapped plan (key present among plan/phases/steps).")
            self.executor.meta_plan = _wrapped
        elif not isinstance(plan_object, list) or not plan_object:
            raise RuntimeError(
                f"LLM response for meta-plan was not a non-empty list. "
                f"Got type: {type(plan_object).__name__}. Response: {response_text[:200]}"
            )
        else:
            self.executor.meta_plan = plan_object

        # --- NORMALIZATION: Convert all template syntaxes to canonical format ---
        if self.executor.meta_plan:
            self.executor.meta_plan = self._normalize_plan_syntax(self.executor.meta_plan)
        # --- END NORMALIZATION ---

        # --- CAPTURE RAW LLM PLAN: Before any preprocessing or rewrite passes ---
        self.executor.raw_llm_plan = copy.deepcopy(self.executor.meta_plan)
        # --- END CAPTURE ---

        # --- TEMPORAL QUERY PREPROCESSING: Inject TDA_CurrentDate for temporal queries ---
        # Log preprocessing gate evaluation for debugging
        app_logger.debug(
            f"TEMPORAL PREPROCESSING GATE: "
            f"meta_plan exists={bool(self.executor.meta_plan)}, "
            f"execution_depth={self.executor.execution_depth}, "
            f"will_run={bool(self.executor.meta_plan and self.executor.execution_depth == 0)}"
        )

        if self.executor.meta_plan and self.executor.execution_depth == 0:
            original_plan, was_injected = self._inject_temporal_context(self.executor.meta_plan)
            self.executor.meta_plan = original_plan
            if was_injected:
                event_data = {
                    "step": "System Correction",
                    "type": "system_correction",
                    "details": "Temporal preprocessing injected TDA_CurrentDate phase to establish date context for temporal query."
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
            else:
                # Log when preprocessing runs but doesn't inject
                app_logger.debug(
                    f"TEMPORAL PREPROCESSING: Evaluated but did not inject. "
                    f"Reasons: query not temporal, or TDA_CurrentDate already present."
                )
        else:
            # Log when preprocessing is skipped
            if self.executor.meta_plan:
                app_logger.info(
                    f"TEMPORAL PREPROCESSING SKIPPED: execution_depth={self.executor.execution_depth} (expected 0). "
                    f"This may cause temporal queries to fail if fast-path executes with placeholders."
                )
            else:
                app_logger.debug(
                    f"TEMPORAL PREPROCESSING SKIPPED: No meta_plan available (depth={self.executor.execution_depth})."
                )
        # --- END TEMPORAL PREPROCESSING ---

        if self.executor.active_prompt_name and self.executor.meta_plan:
            if len(self.executor.meta_plan) > 1 or any(phase.get("type") == "loop" for phase in self.executor.meta_plan):
                self.executor.is_complex_prompt_workflow = True
                app_logger.debug(f"'{self.executor.active_prompt_name}' qualified as complex prompt workflow")