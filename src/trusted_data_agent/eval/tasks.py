"""Task Transparency Classification Framework.

Turns a turn's execution trace into discrete, typed **Tasks** — the unit of agent
work that Agent Ops & Evaluation scores to drive and improve agent quality. It is
deterministic, CI-pure (no LLM, 0 tokens), and derived from the **signed Execution
Provenance Chain** (primary) with event/trace fallbacks, so it works the same way
across all five profile classes.

A **Task** = one discrete unit of executed work, with a *class* (what kind of work)
and a *mode* (how it ran). The classes are quality-relevant, not an exhaustive
event taxonomy (the Live Status filter remains the observability lens):

    planning      strategic planning (Optimize) — the WHOLE planning phase = 1 task
    retrieval     a knowledge / RAG search
    tool          an MCP / component / connector invocation ("doing")
    reasoning     an LLM generation or decision ("thinking": synthesis, tactical,
                  the direct answer, coordinator routing/synthesis)
    coordination  consulting a sub-agent / expert (Coordinate)
    correction    re-doing failed work (self-correction / error recovery) — *rework*

Modes:
    sequential    ran on its own
    parallel      provably overlapped a sibling (timestamp overlap) OR was part of a
                  parallelising orchestrator wave (date-range / column iteration)

Why these and not the 7 Live Status categories: CONTEXT and SYSTEM are scaffolding
(context-window assembly, session naming, skills) — overhead, not agent *work* — so
they are excluded from the task count. ``correction`` is the eval-specific addition
the filter does not surface: rework is the single most quality-relevant task class.

Design intent:
  * Quality levers it exposes — work VOLUME (bloat → cost/latency), COMPOSITION
    (thinking vs doing), REWORK (corrections), and PARALLELISM (latency).
  * Honest by construction — ``parallel_efficiency`` only reports a number when
    concurrency is *structurally* observable; it never fabricates a speedup from a
    serialized chain (provenance steps are appended serially, so timestamp overlap
    is real when it appears but usually absent — the classifier abstains, never lies).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── canonical step_type → task class map (the signed provenance vocabulary) ─────
# "Open" steps START a task; "close" steps only supply the END timestamp of the
# task they pair with (they are not tasks themselves).
_OPEN_STEP_CLASS = {
    "strategic_plan": "planning",
    "plan_rewrite": "planning",     # collapses into the single planning task
    "tool_call": "tool",
    "agent_tool_call": "tool",
    "rag_search": "retrieval",
    "rag_synthesis": "reasoning",
    "llm_call": "reasoning",
    "coordinator_synthesis": "reasoning",
    "self_correction": "correction",
}
_CLOSE_STEPS = {"tool_result", "agent_tool_result", "rag_results", "llm_response"}
# query_intake / profile_resolve / turn_complete are overhead — never tasks.
#
# `tactical_decision` is deliberately NOT a task: it is per-phase routing that is
# usually FASTPATH (args pre-known from the strategic plan → NO LLM call), so
# counting it as a reasoning task over-counts the common case. Each phase's real
# work is its tool call; the genuine reasoning is the strategic plan (planning) and
# the final synthesis (TDA_FinalReport, classified as reasoning below).

# Framework tools are not ordinary "doing": TDA_FinalReport IS the answer synthesis
# (an LLM generation → reasoning); the rest are scaffolding (logging / current-date /
# context report / orchestration wrapper) and are not tasks. The orchestrator's
# wrapped calls are added separately as a parallel wave (_append_orchestrator_groups).
_SYNTHESIS_TOOLS = {"TDA_FinalReport"}
_NON_TASK_TOOLS = {"TDA_SystemLog", "TDA_CurrentDate", "TDA_ContextReport",
                   "TDA_SystemOrchestration"}
_TOOL_NAME_RE = re.compile(r"(?:Call|Result):\s*([A-Za-z0-9_]+)")


def _tool_from_summary(summary: str):
    m = _TOOL_NAME_RE.search(summary or "")
    return m.group(1) if m else None

TASK_CLASSES = ("planning", "retrieval", "tool", "reasoning", "coordination", "correction")
# Orchestrators that run their wrapped calls concurrently (asyncio.gather).
_PARALLEL_ORCHESTRATORS = {"date_range", "column_iteration"}

# ── flow node_type → task class (flow-agents) ───────────────────────────────────
# A flow is a DAG; each executed node is a task. start/end are scaffolding.
_FLOW_NODE_CLASS = {
    "task_tool": "tool",
    "task_function": "tool",        # deterministic function = "doing"
    "task_llm": "reasoning",
    "task_prompt": "reasoning",
    "decision": "reasoning",        # an LLM / condition decision
    "task_extension": "reasoning",  # post-processing (often LLM-backed)
    "judge": "reasoning",           # a verdict on a round (an agent or the eval judge panel)
    "artefact": "tool",             # writes the round's code to a canvas = "doing"
    "file_input": "tool",           # reads a kept file (Files) = "doing"
    "file_output": "tool",          # keeps its input as a file (Files) = "doing"
}
# Classes whose execution can genuinely run tasks in parallel (so parallel_efficiency
# is meaningful). The rest are single-path; the metric abstains for them by design.
PARALLEL_CAPABLE_CLASSES = frozenset({"tool_enabled", "genie", "flow"})


@dataclass
class Task:
    """One discrete unit of executed agent work."""
    task_class: str               # one of TASK_CLASSES
    name: str = ""                # tool name / step summary (transparency)
    started_at: Optional[float] = None   # epoch seconds (None if unknown)
    ended_at: Optional[float] = None
    mode: str = "sequential"      # "sequential" | "parallel"
    wave_group: Optional[int] = None     # explicit concurrency group (orchestrators)

    @property
    def duration_s(self) -> float:
        if self.started_at is None or self.ended_at is None:
            return 0.0
        return max(0.0, self.ended_at - self.started_at)


@dataclass
class TaskSet:
    """The classified tasks of a turn + derived quality views."""
    tasks: list = field(default_factory=list)
    waves: int = 0                # number of execution waves (critical-path length)
    max_concurrency: int = 1      # largest wave size (1 = fully sequential)
    source: str = "provenance"    # where the tasks came from (provenance|trace|fallback)

    # — counts —
    @property
    def total(self) -> int:
        return len(self.tasks)

    def by_class(self) -> dict:
        out = {c: 0 for c in TASK_CLASSES}
        for t in self.tasks:
            out[t.task_class] = out.get(t.task_class, 0) + 1
        return out

    def count(self, *classes: str) -> int:
        return sum(1 for t in self.tasks if t.task_class in classes)

    def breakdown(self) -> list:
        """Composition as ordered ``"N <class>"`` parts (non-zero only) — the
        transparency split surfaced on the Quality chip + Agent Performance UX,
        e.g. ``["1 planning", "2 tool", "2 reasoning"]``."""
        bc = self.by_class()
        return [f"{bc[c]} {c}" for c in TASK_CLASSES if bc.get(c)]

    # — parallelism —
    @property
    def parallel_count(self) -> int:
        return sum(1 for t in self.tasks if t.mode == "parallel")

    @property
    def has_parallelism(self) -> bool:
        return self.max_concurrency > 1

    @property
    def parallel_efficiency(self) -> Optional[float]:
        """Speedup = tasks / waves (1.0 = fully sequential, N = N-way parallel).

        Returns None when no concurrency is structurally observable — the honest
        "not measurable", never a fabricated 1.0."""
        if not self.has_parallelism or self.waves <= 0:
            return None
        return round(self.total / self.waves, 4)

    def summary(self) -> str:
        bc = self.by_class()
        parts = [f"{n} {c}" for c, n in bc.items() if n]
        return f"{self.total} task{'' if self.total == 1 else 's'}" + (
            f" ({' · '.join(parts)})" if parts else "")


def _epoch(ts) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return None


def _tasks_from_provenance(run) -> list:
    """Walk the signed chain into Tasks, pairing open/close steps and collapsing
    the strategic-planning steps (plan + every rewrite pass) into ONE planning task."""
    tasks: list = []
    open_task: Optional[Task] = None
    for step in run.provenance_steps:
        if not isinstance(step, dict):
            continue
        st = step.get("step_type") or ""
        ts = _epoch(step.get("timestamp"))
        if st in _CLOSE_STEPS:
            if open_task is not None and open_task.ended_at is None:
                open_task.ended_at = ts
            continue
        if st.startswith("error:"):
            tasks.append(Task("correction", name=st, started_at=ts, ended_at=ts))
            continue
        cls = _OPEN_STEP_CLASS.get(st)
        if cls is None:
            continue  # overhead (query_intake / profile_resolve / tactical_decision / turn_complete)
        name = (step.get("content_summary") or st)[:80]
        # Tool steps: framework scaffolding is not a task; the final-report tool is
        # the answer synthesis (reasoning), not ordinary tool "doing".
        if cls == "tool":
            tool = _tool_from_summary(step.get("content_summary"))
            if tool in _NON_TASK_TOOLS:
                continue
            if tool in _SYNTHESIS_TOOLS:
                cls = "reasoning"
        # Planning collapse: extend the existing planning task instead of adding one.
        if cls == "planning" and open_task is not None and open_task.task_class == "planning":
            open_task.ended_at = ts
            continue
        open_task = Task(cls, name=name, started_at=ts, ended_at=ts)
        tasks.append(open_task)
    return tasks


def _supplement_react_reasoning(run, tasks: list) -> None:
    """ReAct (conversation_with_tools) records tool calls in the chain but its LLM
    reasoning iterations live in conversation_agent_events — count those so the
    'thinking' tasks are not invisible for the ReAct flow."""
    events = run._t.get("conversation_agent_events")
    if not isinstance(events, list):
        return
    for e in events:
        if isinstance(e, dict) and e.get("type") in ("conversation_llm_step", "agent_llm_step"):
            ts = _epoch((e.get("payload") or {}).get("timestamp"))
            tasks.append(Task("reasoning", name="llm step", started_at=ts, ended_at=ts))


def _supplement_coordination(run, tasks: list) -> None:
    """Each consulted expert is one coordination task (Coordinate)."""
    have = any(t.task_class == "coordination" for t in tasks)
    if have:
        return
    for s in run.slaves_invoked():
        nm = (s.get("tag") or s.get("profile_tag") or s.get("tool_name") or "expert") if isinstance(s, dict) else "expert"
        tasks.append(Task("coordination", name=str(nm)))


def _tasks_from_flow_nodes(run) -> list:
    """Flow-agents: each executed node is a task, classed by node_type. Node runs
    carry real ``started_at``/``completed_at`` stamps, so flow parallelism (parallel
    branches) is faithfully captured — flows are the strongest parallel-efficiency case."""
    tasks: list = []
    for n in run.executed_nodes():
        if not isinstance(n, dict):
            continue
        cls = _FLOW_NODE_CLASS.get(n.get("node_type"))
        if cls is None:
            continue  # start / end / unknown scaffolding
        if n.get("status") in ("error", "error_routed"):
            # a failed node is rework-adjacent, but its *kind* of work is its class;
            # keep the class and let flow_nodes_succeeded carry the failure signal.
            pass
        tasks.append(Task(cls,
                          name=str(n.get("label") or n.get("node_id") or n.get("node_type"))[:80],
                          started_at=_epoch(n.get("started_at")),
                          ended_at=_epoch(n.get("completed_at"))))
    return tasks


def _fallback_tasks(run) -> list:
    """No signed chain (legacy turns): synthesise a minimal task list from the trace
    so the volume KPI is still populated."""
    tasks = [Task("tool", name=c.get("tool_name", "")) for c in run.tool_calls()]
    if not tasks:
        # every class produces at least its final answer (one reasoning task)
        if (run.final_answer_text or "").strip():
            tasks.append(Task("reasoning", name="answer"))
    return tasks


def _append_orchestrator_groups(run, tasks: list) -> None:
    """Parallelising orchestrators (date-range / column-iteration) run their wrapped
    calls concurrently (asyncio.gather). Add each wrapped call as a real parallel
    tool task sharing one explicit ``wave_group`` — structural concurrency that needs
    no timestamps."""
    gid = 1
    for entry in run.execution_trace:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action") or {}
        if action.get("tool_name") != "TDA_SystemOrchestration":
            continue
        if action.get("orchestrator_type") not in _PARALLEL_ORCHESTRATORS:
            continue
        wrapped = [w for w in (action.get("wrapped_tool_calls") or []) if isinstance(w, dict)]
        if len(wrapped) <= 1:
            continue
        for w in wrapped:
            tasks.append(Task("tool", name=str(w.get("tool") or w.get("tool_name") or "")[:80],
                              mode="parallel", wave_group=gid))
        gid += 1


def _compute_waves(tasks: list) -> tuple:
    """Group tasks into execution waves and mark the parallel ones. A wave = tasks
    that ran concurrently, established by (1) an explicit ``wave_group`` (orchestrator
    groups), else (2) provable wall-clock overlap (timestamp intervals). Tasks with
    neither each form their own sequential wave. Returns (waves, max_concurrency)."""
    waves = 0
    max_conc = 1
    consumed = set()

    # (1) explicit orchestrator groups
    groups: dict = {}
    for t in tasks:
        if t.wave_group is not None:
            groups.setdefault(t.wave_group, []).append(t)
            consumed.add(id(t))
    for members in groups.values():
        waves += 1
        max_conc = max(max_conc, len(members))
        if len(members) > 1:
            for m in members:
                m.mode = "parallel"

    # (2) timestamp overlap among the rest
    timed = [t for t in tasks if id(t) not in consumed
             and t.started_at is not None and t.ended_at is not None and t.duration_s > 0]
    timed.sort(key=lambda t: t.started_at)
    cur_end = None
    wave_members: list = []
    for t in timed:
        consumed.add(id(t))
        if cur_end is not None and t.started_at < cur_end:   # overlaps current wave
            wave_members.append(t)
            cur_end = max(cur_end, t.ended_at)
        else:
            if len(wave_members) > 1:
                for m in wave_members:
                    m.mode = "parallel"
            max_conc = max(max_conc, len(wave_members))
            waves += 1
            wave_members = [t]
            cur_end = t.ended_at
    if wave_members:
        if len(wave_members) > 1:
            for m in wave_members:
                m.mode = "parallel"
        max_conc = max(max_conc, len(wave_members))

    # (3) everything else is its own sequential wave
    waves += sum(1 for t in tasks if id(t) not in consumed)
    return max(waves, 1), max_conc


def classify_tasks(run) -> TaskSet:
    """Classify an execution into a typed TaskSet.

    Polymorphic over RunRecord (profile-agents) and FlowRunRecord (flow-agents) so
    the ``tasks`` / ``parallel_efficiency`` KPIs read consistently for EVERY agent
    type. Pure + defensive — never raises (an unexpected shape yields an empty
    TaskSet)."""
    try:
        # Flow-agent: classify the DAG's node runs (real per-node timings → genuine
        # parallel detection).
        if getattr(run, "profile_type", None) == "flow":
            tasks = _tasks_from_flow_nodes(run)
            waves, max_conc = _compute_waves(tasks)
            return TaskSet(tasks=tasks, waves=waves, max_concurrency=max_conc,
                           source="flow_nodes")
        source = "provenance"
        tasks = _tasks_from_provenance(run) if run.provenance_steps else []
        if not tasks:
            tasks = _fallback_tasks(run)
            source = "fallback"
        # Class-specific supplements (work the chain under-represents).
        pclass = run.profile_type
        if pclass == "conversation_with_tools":
            _supplement_react_reasoning(run, tasks)
        if pclass == "genie":
            _supplement_coordination(run, tasks)
        _append_orchestrator_groups(run, tasks)
        waves, max_conc = _compute_waves(tasks)
        return TaskSet(tasks=tasks, waves=waves, max_concurrency=max_conc, source=source)
    except Exception:
        return TaskSet(tasks=[], waves=0, max_concurrency=1, source="error")
