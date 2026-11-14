"""plan_compiler.py — Plan transformation pipeline and canonical tool-set constants.

This module is the single source of truth for:
  - Tool-set classifications used across the planner, executor, and phase_executor
  - The PlanPass protocol (pure plan_in → plan_out + SSE events)
  - The PlanContext data bag passed to every pass
  - The PlanCompiler pipeline runner

Design principle: rewrite passes are deterministic functions on the plan list.
They must not access executor or planner state directly; they receive everything
they need via PlanContext.  This makes passes unit-testable with plain dicts — no
executor instance required.

Usage::

    from trusted_data_agent.agent.plan_compiler import (
        UTILITY_TOOLS, TERMINAL_TOOLS, VISUALIZATION_TOOLS, SYSTEM_TOOLS,
        PlanContext, PlanCompiler,
    )

Adding a new tool:
    1. Decide which set(s) it belongs to (see docstrings below).
    2. Add it here only — never add local copies in planner.py or executor.py.
    3. Update the docstring of the affected set.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import AsyncGenerator, Protocol, runtime_checkable

app_logger = logging.getLogger("quart.app")


# ---------------------------------------------------------------------------
# Canonical tool-set classifications — single source of truth.
# ---------------------------------------------------------------------------

# Tools that do NOT gather external data (no DB / API / file-system calls).
# A phase whose relevant_tools are ALL in UTILITY_TOOLS is not a "data-gathering
# phase" for purposes of:
#   - chart/dashboard data-refetch collapse (_rewrite_plan_collapse_chart_data_refetch)
#   - charting cleanup  (_rewrite_plan_for_charting_phases)
#   - temporal data-flow wiring (_rewrite_plan_for_temporal_data_flow)
#   - implicit dependency detection (_phase_implicit_deps in executor.py)
UTILITY_TOOLS: frozenset[str] = frozenset({
    "TDA_Charting",
    "TDA_Dashboard",
    "TDA_FinalReport",
    "TDA_ComplexPromptReport",
    "TDA_LLMTask",
    "TDA_ContextReport",
    "TDA_CurrentDate",
    "TDA_DateRange",
    "TDA_LLMFilter",
    "TDA_SystemLog",
    "TDA_Scheduler",
})

# Tools that synthesise or report on ALL prior results.
# Terminal phases run alone — never in a parallel group — even without explicit
# result_of_phase_N references in the plan.
# Used by: executor._is_terminal_phase, executor._build_phase_groups.
TERMINAL_TOOLS: frozenset[str] = frozenset({
    "TDA_FinalReport",           # final natural-language answer synthesis
    "TDA_ComplexPromptReport",   # rich-format final synthesis
    "TDA_ContextReport",         # context-based answer (uses all collected context)
    "TDA_LLMTask",               # LLM reasoning over all collected data
})

# Tools that consume structured data produced by preceding data-gathering phases.
# A phase that uses a VISUALIZATION_TOOL implicitly depends on all preceding
# data-gathering phases — even if the plan carries no result_of_phase_N string
# (e.g. because a rewrite pass stripped the argument that carried it).
#
# This is a belt-and-suspenders complement to the string-scanning dependency
# detector in executor._phase_referenced_deps.  Both are combined in
# _build_phase_groups so the parallel scheduler is never misled by absent strings.
#
# NOTE: For TDA_Dashboard, only action=visualize consumes phase data.
#       Other actions (create, add_tile, refresh) are orchestration calls.
#       This is guarded in executor._phase_implicit_deps.
VISUALIZATION_TOOLS: frozenset[str] = frozenset({
    "TDA_Charting",
    "TDA_Dashboard",
})

# Schema-DISCOVERY tools — their purpose is to identify database / table / column names
# that a later data-EXECUTION phase (raw SQL) consumes. A weak planner routinely writes the
# query with a hardcoded (often hallucinated) name and NO result_of_phase_N reference, so the
# string-scanning dependency detector misses the dependency and the discovery + query race in
# parallel (the turn-6 "give me all airport stations" failure: a KG-query phase and a
# `SELECT * FROM airport.station` phase were dispatched concurrently). Used by
# executor._phase_discovery_deps to force discovery-before-execution ordering.
#
# Over-detection is safe by design (it only forces sequential execution); a missed dependency
# would let a query race ahead of the discovery meant to inform it.
DISCOVERY_TOOLS: frozenset[str] = frozenset({
    "TDA_KnowledgeGraph",     # 'query' identifies the table/entity a later SQL phase targets
    "base_databaseList",
    "base_tableList",
    "base_columnDescription",
    "base_tableDDL",
    "base_tablePreview",
    "base_tableAffinity",
})

# Tools excluded from temporal-context injection (TDA_CurrentDate argument wiring).
# A phase whose first relevant_tool is in SYSTEM_TOOLS will not receive an
# injected date argument by _rewrite_plan_for_temporal_data_flow.
SYSTEM_TOOLS: frozenset[str] = frozenset({
    "TDA_CurrentDate",
    "TDA_DateRange",
    "TDA_FinalReport",
    "TDA_LLMTask",
    "TDA_ContextReport",
    "TDA_ComplexPromptReport",
    "TDA_SystemLog",
    "TDA_Charting",
    "TDA_Dashboard",
})


# ---------------------------------------------------------------------------
# PlanContext — immutable snapshot of everything a pass might need.
# ---------------------------------------------------------------------------

@dataclass
class PlanContext:
    """Read-only context bag passed to every PlanPass.

    Passes MUST NOT mutate any field here.  The compiler threads the plan list
    through the pass chain; PlanContext provides the external state each pass
    needs to make decisions.
    """

    query: str = ""
    """The user query that triggered plan generation."""

    previous_turn_data: dict = field(default_factory=dict)
    """Session data from the previous conversation turn (workflow_history, etc.)."""

    available_tool_names: list[str] = field(default_factory=list)
    """MCP tool names available in the current session."""

    knowledge_context: str = ""
    """Retrieved knowledge-repository content for this query."""

    enable_sql_consolidation: bool = True
    """Whether the SQL consolidation rewrite pass is enabled (from APP_CONFIG)."""

    extra: dict = field(default_factory=dict)
    """Escape hatch for pass-specific data.  Prefer explicit fields over extra."""


# ---------------------------------------------------------------------------
# PlanPass protocol — pure plan → plan transform.
# ---------------------------------------------------------------------------

@runtime_checkable
class PlanPass(Protocol):
    """A single deterministic plan rewrite pass.

    CONTRACT:
      - Side-effect-free on the *plan* argument — do not mutate; return a new list.
      - Return a 2-tuple: (new_plan, sse_events).
          new_plan   : the transformed plan (may be the same list if unchanged).
          sse_events : zero or more SSE event dicts to forward to the UI before
                       the diff event is emitted.
      - May be async; PlanCompiler handles both sync and async transparently.

    The Protocol is @runtime_checkable so passes can be validated cheaply with
    isinstance() in tests.
    """

    name: str
    """Short identifier shown in plan_rewrite_diff SSE events."""

    async def transform(
        self, plan: list[dict], ctx: PlanContext
    ) -> tuple[list[dict], list[dict]]:
        """Transform *plan* and return (new_plan, sse_events)."""
        ...


# ---------------------------------------------------------------------------
# PlanCompiler — pipeline runner.
# ---------------------------------------------------------------------------

class PlanCompiler:
    """Chains PlanPass instances; emits plan_rewrite_diff SSE events for mutating passes.

    Usage::

        compiler = PlanCompiler(passes=[PassA(), PassB(), PassC()])
        async for sse_event in compiler.compile(raw_plan, ctx):
            yield sse_event
        final_plan = compiler.last_plan

    The compiler is intentionally decoupled from PlanExecutor / Planner so that
    passes can be unit-tested with plain phase dicts — no executor state required.

    Thread safety: not thread-safe; create one instance per planning call.
    """

    def __init__(self, passes: list) -> None:
        self._passes = passes
        self.last_plan: list[dict] = []

    async def compile(
        self,
        initial_plan: list[dict],
        ctx: PlanContext,
    ) -> AsyncGenerator[dict, None]:
        """Run the pass pipeline; yield SSE event dicts (pass events + diff events).

        Pass events are yielded first (in order), then the diff event (if the plan
        changed) — matching the behaviour of the original _pass() helper in
        generate_and_refine_plan.
        """
        plan = [dict(p) for p in initial_plan]  # shallow-copy; passes own mutations

        for pass_ in self._passes:
            before_json = json.dumps(plan, sort_keys=True, default=str)
            n_before = len(plan)

            # Accept both sync and async transform methods transparently.
            result = pass_.transform(plan, ctx)
            if hasattr(result, "__await__"):
                new_plan, events = await result
            else:
                # Synchronous transform — call directly.
                new_plan, events = result

            for ev in events:
                yield ev

            after_json = json.dumps(new_plan, sort_keys=True, default=str)
            if after_json != before_json:
                n_after = len(new_plan)
                yield {
                    "step": "Plan Rewrite",
                    "type": "plan_rewrite_diff",
                    "details": {
                        "pass": pass_.name,
                        "phases_before": n_before,
                        "phases_after": n_after,
                        "delta": n_after - n_before,
                    },
                }

            plan = new_plan

        self.last_plan = plan
