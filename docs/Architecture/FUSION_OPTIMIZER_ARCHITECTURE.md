# Fusion Optimizer Architecture

## Overview

The Trusted Data Agent operates on a two-tier execution model: a **Strategic Planner** generates multi-phase plans, and a **Tactical Executor** carries them out phase-by-phase. Between these two layers — and woven throughout them — sits the **Fusion Optimizer**: six interconnected subsystems that collectively transform a raw LLM-generated plan into a production-grade execution pipeline.

These subsystems are not isolated features. They form a **connected optimization mesh** where each subsystem's outputs feed, guard, or trigger others. Plan Validation prevents hallucinations that would otherwise require Error Correction. Context Distillation serves every LLM call made by Error Correction and Re-planning. Re-planning consumes error patterns surfaced by Error Correction. Together, they enforce a design contract:

> **Every plan is validated before execution, every execution is monitored for correction, every correction preserves context budget, and every failure is reported honestly rather than papered over.**

```
                        FUSION OPTIMIZER ARCHITECTURE
                        =============================

    USER QUERY
        |
        v
  +-----------------------+
  |   STRATEGIC PLANNER   |--- generates raw meta_plan (LLM output)
  |   planner.py          |
  +-----------------------+
        |
        | raw plan
        v
  +=======================================================================+
  |                   FUSION OPTIMIZER — Pre-Execution                    |
  |                                                                       |
  |   +---------------------+     +---------------------+                 |
  |   | [S1] DETERMINISTIC  |     | [S3] HALLUCINATION  |                 |
  |   | PLAN VALIDATION     |---->| PREVENTION          |                 |
  |   |                     |     |                     |                 |
  |   | 19-pass refinement  |     | Orchestrator guards |                 |
  |   | (7 structural fixes)|     | Plan hydration      |                 |
  |   +---------------------+     | Turn validity       |                 |
  |           |                   +---------------------+                 |
  |           v                           |                               |
  |   +---------------------+            |                                |
  |   | [S2] PROACTIVE      |<-----------+                                |
  |   | RE-PLANNING         |                                             |
  |   |                     |     +---------------------+                 |
  |   | Prompt->Tool rewrite|     | [S6] CONTEXT        |                 |
  |   | max 1 replan        |     | DISTILLATION        |                 |
  |   +---------------------+     |                     |                 |
  |                               | Serves ALL LLM calls|                 |
  |                               | 5-pass budget       |                 |
  |                               | 2-level analytical  |                 |
  |                               +---------------------+                 |
  +=======================================================================+
        |
        | validated + optimized plan
        v
  +-----------------------+
  |   TACTICAL EXECUTOR   |--- executes phase-by-phase
  |   phase_executor.py   |
  +-----------------------+
        |
        | per-tool execution
        v
  +=======================================================================+
  |                     FUSION OPTIMIZER — Runtime                        |
  |                                                                       |
  |   +---------------------+     +---------------------+                 |
  |   | [S4] INTELLIGENT    |     | [S5] PHASE          |                 |
  |   | ERROR CORRECTION    |---->| EXHAUSTION          |                 |
  |   |                     |     |                     |                 |
  |   | Strategy Pattern    |     | After 5 attempts:   |                 |
  |   | 3 retries per tool  |     | honest error result |                 |
  |   | Profile-aware       |     | turn continues      |                 |
  |   +---------------------+     | (LLM replan removed)|                 |
  |           ^                   +---------------------+                 |
  |           |                           |                               |
  |           +--- [S6] Context Distillation feeds correction prompts     |
  |           +--- [S3] Orchestrator guards prevent hallucinated loops    |
  +=======================================================================+
        |
        v
    FINAL RESULT
```

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [The Optimization Pipeline](#2-the-optimization-pipeline)
3. [Subsystem 1: Deterministic Plan Validation](#3-subsystem-1-deterministic-plan-validation)
4. [Subsystem 2: Proactive Re-planning](#4-subsystem-2-proactive-re-planning)
5. [Subsystem 3: Hallucination Prevention](#5-subsystem-3-hallucination-prevention)
6. [Subsystem 4: Intelligent Error Correction](#6-subsystem-4-intelligent-error-correction)
7. [Subsystem 5: Phase Exhaustion](#7-subsystem-5-phase-exhaustion-autonomous-recovery--removed)
8. [Subsystem 6: Context Distillation](#8-subsystem-6-context-distillation)
9. [Cross-Cutting Concerns](#9-cross-cutting-concerns)
10. [Observability & Event System](#10-observability--event-system)
11. [System Prompt Reference](#11-system-prompt-reference)
12. [Configuration Reference](#12-configuration-reference)
13. [File Reference](#13-file-reference)
14. [Recent Improvements](#14-recent-improvements-engine-modularization-branch)

---

## 1. Design Philosophy

Four governing principles emerge from the architecture and are enforced across all six subsystems:

### 1.1 Deterministic Before LLM

Every correction that can be made with code — regex matching, schema validation, fuzzy string matching, datetime arithmetic — is made before invoking the LLM. This saves tokens, reduces latency, and produces predictable, reproducible results.

**Examples:**
- Plan Validation's structural corrections are entirely deterministic (`planner.py` → `_validate_and_correct_plan()`), as are most of the 19 refinement-pipeline passes (§3.2)
- The Date Range Orchestrator uses `_resolve_temporal_phrase_deterministic()` for 12 common temporal patterns before falling back to LLM (`orchestrators.py` → `_resolve_temporal_phrase_deterministic`)
- Argument synonym mapping uses `AppConfig.ARGUMENT_SYNONYM_MAP` for automatic renaming (`config.py`)

### 1.2 Fail-Fast for Definitive Errors

Errors classified as definitive — permission denied, fundamentally invalid query syntax — are not retried. The `DEFINITIVE_TOOL_ERRORS` dictionary in `phase_executor.py` halts execution immediately with a clear, user-facing message. Only errors in `RECOVERABLE_TOOL_ERRORS` (table not found, column not found) enter the correction pipeline.

### 1.3 Progressive Escalation

The system escalates through increasingly expensive interventions, exhausting cheaper options first:

```
Deterministic Fix (free)
    → Tactical Retry (1 LLM call)
        → Self-Correction Strategy (1 LLM call)
            → Phase Retry (N LLM calls, up to MAX_PHASE_RETRY_ATTEMPTS)
                → Honest phase failure (0 LLM — the turn synthesises from what was gathered)
```

Re-planning (S2) is not on this chain: it runs once, before execution, on plan shape. The former
"Autonomous Recovery" rung (an LLM replan after phase exhaustion) was removed — see §7.

### 1.4 Budget-Aware Everything

Every subsystem that touches the LLM context window is aware of token budgets. The Context Window Handler's 5-pass algorithm, the ExecutionContextDistiller's configurable thresholds, and the 2-level analytical distillation in the adapter all enforce strict budget compliance.

---

## 2. The Optimization Pipeline

### 2.1 Execution Lifecycle with Optimization Checkpoints

```
  Phase 0: PLAN GENERATION
  ┌──────────────────────────────────────────────────────────────────┐
  │ Planner._generate_meta_plan()                                   │
  │   [signed seed asked word for word → replayed, 0 LLM (§2.4)]    │
  │   [LLM generates raw plan from WORKFLOW_META_PLANNING_PROMPT]   │
  │   [no/truncated plan → one parse-error retry (§2.5)]            │
  │   [conversational answer to a non-small-talk turn → one         │
  │    re-plan with a correction note (§2.5)]                       │
  └──────────────┬───────────────────────────────────────────────────┘
                 │
  Phase 1: PLAN REFINEMENT PIPELINE (generate_and_refine_plan — 19 passes)
  ┌──────────────┴───────────────────────────────────────────────────┐
  │ Pass 1:  temporal_data_flow                          [S1/S3]   │
  │ Pass 2:  governed_execution                          [S1]      │  Aug 2026 — OBDA-READ
  │ Pass 3:  sql_consolidation (ENABLE_SQL_CONSOLIDATION_REWRITE)  │
  │ Pass 4:  llm_filter_deloop                           [S1]      │
  │ Pass 5:  multi_loop_synthesis                        [S1/S3]   │
  │ Pass 6:  llm_task_optimization                       [S3]      │
  │ Pass 7:  date_range_wiring                           [S1/S3]   │
  │ Pass 8:  placeholder_sql                             [S1/S3]   │  Oct 2026
  │ Pass 9:  database_before_sql                         [S1/S3]   │  Oct 2026
  │ Pass 10: schema_before_sql                           [S1/S3]   │  Sep 2026
  │ Pass 11: text_date_cast                              [S1]      │  Sep 2026
  │ Pass 12: compute_insertion                           [S1/S3]   │  Sep 2026 — Compute with Lineage
  │ Pass 13: cross_source_shape                          [S1/S3]   │  Sep 2026 — cross-source plan shape
  │ Pass 14: plan_validation                             [S1]      │
  │ Pass 15: chart_data_reuse                            [S1]      │
  │ Pass 16: charting_cleanup                            [S1/S3]   │
  │ Pass 17: plan_hydration                              [S3]      │
  │ Pass 18: context_report                              [S1]      │
  │ Pass 19: final_report_guarantee                      [S1]      │
  └──────────────┬───────────────────────────────────────────────────┘
                 │
  Phase 2: EFFICIENCY RE-PLANNING CHECK
  ┌──────────────┴───────────────────────────────────────────────────┐
  │ Does plan mix prompts + tools?                        [S2]      │
  │   YES + non-granted prompts + multi-phase + depth 0             │
  │     → Loop back to Phase 0 with replan_context                  │
  │     → max_replans = 1                                           │
  │   NO → proceed to execution                                    │
  └──────────────┬───────────────────────────────────────────────────┘
                 │
  Phase 3: EXECUTION WITH RUNTIME GUARDS
  ┌──────────────┴───────────────────────────────────────────────────┐
  │ For each phase in meta_plan:                                    │
  │                                                                 │
  │   [S6] Context distillation on workflow_state                   │
  │        before any tactical LLM call                             │
  │                                                                 │
  │   [S3] Orchestrator guards:                                     │
  │        - Date Range: validates temporal phrases                  │
  │        - Column Iteration: validates type compatibility          │
  │        - Hallucinated Loop: detects string literal lists         │
  │                                                                 │
  │   [S4] On tool error:                                           │
  │        - DEFINITIVE errors → halt immediately                   │
  │        - RECOVERABLE errors → CorrectionHandler                 │
  │          → 3 retries with corrected arguments                   │
  │                                                                 │
  │   [S5] After MAX_PHASE_RETRY_ATTEMPTS (5) exhausted:            │
  │        → error result recorded as result_of_phase_N             │
  │        → "Phase Failed After N Attempts" event                  │
  │        → the turn continues; the report says what is missing   │
  └─────────────────────────────────────────────────────────────────┘
```

### 2.2 Walkthrough Scenario

**User asks:** "Show me customer orders for the past 7 days as a pie chart"

1. **Strategic Planner** generates a 3-phase plan: Phase 1 = `TDA_CurrentDate`, Phase 2 = `base_customerOrders` (no date arg), Phase 3 = `TDA_Charting` (with hallucinated `mapping` args)
2. **[S1] Temporal Data Flow Rewrite** detects `TDA_CurrentDate` is not wired to Phase 2, injects `"past 7 days"` as the date argument
3. **[S1/S3] Chart Argument Cleanup** strips the hallucinated `mapping` and `data` from Phase 3
4. **[S1] Plan Validation** checks Phase 2's tool schema — no extraneous args remain
5. **[S2]** Plan passes the Re-planning check (no executable prompts in plan)
6. **Execution begins**: Phase 2's temporal date arg triggers the **[S3] Date Range Orchestrator**, which deterministically resolves "past 7 days" to 7 concrete dates without an LLM call
7. Phase 2 returns 500 rows — **[S6] Context Distillation** replaces raw data with metadata summary for Phase 3's tactical LLM prompt
8. If Phase 2 had failed with "Table 'CustomerOrders' does not exist", **[S4] TableNotFoundStrategy** would fire, asking the LLM for the correct table name, then retry with corrected args
9. If 5 phase attempts failed, **[S5]** would record the phase as failed (an error result under `result_of_phase_2`) and the turn would continue, so the report states the missing data instead of inventing it

---

### 2.3 Optimize Fast-Lanes (opt-in, default off)

Two **independent, per-profile** toggles let `tool_enabled` profiles behave more like Ideate+tools on *simple* requests while keeping the full Planner/Executor for complex ones. Both are **off by default** and strictly additive — when off, the pipeline above is byte-for-byte unchanged. Configured in the Profile editor → *Advanced Configuration*.

#### Report fast-track — `fastTrackSimpleReports` (+ `fastTrackMaxRows`)

Skips the verbose final-report synthesis when the answer *is* the data.

- **Trigger** (decided in `phase_executor.py`, the single source of truth — it owns the SSE events): the profile opted in **and** the collected result is provably small — total result rows ≤ `fastTrackMaxRows` **and** the distilled data < 6 KB (belt-and-suspenders against a mis-count). Fail-safe: any uncertainty (unknown result shape, no session/profile) ⇒ no fast-track.
- **Effect**: the executor stamps `_fast_track_report` onto the `TDA_FinalReport` action. `adapter.py:_invoke_final_report_task` **honours the stamp** (re-checking only the size guard) and swaps the heavy `CanonicalResponse` report prompt for a one-/two-sentence synthesis prompt. **The LLM call, response parse, and result schema are identical** — only the prompt text differs, so everything downstream (events, storage, RAG seeding) is unchanged.
- **Live Status**: renders a distinct **"Fast-Track Final Report"** step (*"Fast-Track synthesis — simple result, skipping the full report"*) instead of the standard "Synthesizing final user-facing report".
- Helpers: `adapter.py:_resolve_fasttrack_report_cfg()` (reads the session's active profile flags), `_count_report_result_rows()` (conservative row count, returns `None` on uncertainty).

#### Plan gate — `planningGate`

Evaluates **planning necessity** before strategic planning and, when unnecessary, skips the heavy planner.

- **Hook**: top of `OptimizeEngine.execute`, before `planner.generate_and_refine_plan` (guarded entirely by the flag — absent on existing profiles ⇒ never runs). Skipped for replay and delegated turns.
- **Gate call**: a tiny prompt (tools context + the request) asking for `COMPLEX` *or* a single-phase plan JSON.
  - **COMPLEX / unparseable plan / ANY error ⇒ escalation**: leaves `executor.state` untouched so the full strategic planner runs exactly as before.
  - **SIMPLE ⇒ fast lane**: sets `executor.meta_plan` to the gate's minimal plan, ensures the report phase via `Planner._ensure_final_report_phase()`, and hands off to execution by **mirroring the proven replay path** (`meta_plan` + `state = EXECUTING`). The 19-pass refinement pipeline is not run on this lane and there is **no new execution code**.
- **Live Status**: **"Plan Gate: Evaluating Planning Necessity"** (before the gate call) → **"Fast Lane — Strategic Planning Skipped"** or **"Full Planning Required"** (verdict).
- **Trade-off (by design, opt-in)**: a SIMPLE plan **bypasses RAG champion cases and the rewrite passes** (S1–S6 above), so it trusts the model more for trivial lookups. Tool errors still flow through normal self-correction (S4); a misclassified-as-simple multi-step request may under-serve, which is why it is off by default. Pairs naturally with the report fast-track (a SIMPLE gate plan whose single phase yields a small result fast-tracks its report too).

`planningGate` requires a **server restart** to take effect (Python). Both fast-lanes compose: with both on, a trivial request runs `gate (1 light call) → single phase → fast-track synthesis (1 light call)`, approaching Ideate+tools' call count without leaving OptimizeEngine.

---

### 2.4 Signed Seed Replay — an exact question is not re-planned (Oct 2026)

**Rule (`planner.exact_signed_champion`, applied where the strategic model would be called):**
when the question matches a retrieved champion's own question **word for word** (case,
whitespace and a trailing `.`/`?` ignored), and that champion is a **seeded template case**
(`metadata.template_generated`) held by a planner repository that `planner_replayable`
accepts — **trust-locked** (a locked planner never learns, so every case in it was put there on
purpose), or **signed and intact** (its attested hash still equals its live hash; an unlocked
repository that learns a case after signing stops qualifying until it is signed again), the
champion's `successful_strategy` becomes the strategic plan **verbatim**: no planning call,
0 tokens, and the trace carries a `Signed seed replayed verbatim` system message naming the
case and the planner. Everything downstream — the rewrite passes, FASTPATH, the preflight
guard, synthesis — runs exactly as it would on a model-written plan.

Why: reasoning belongs only where code cannot decide. A learned champion, a near match or a
seed in an unlocked planner still guide the model as few-shot examples ("proven patterns,
NOT templates to copy exactly"), because there the model may legitimately adapt. But a signed
seed asked verbatim leaves nothing to adapt — and re-planning it only added variance: on
production (14 Oct 2026) the policing demo's cross-over seed (read the declared
`Offence ↔ CaseObject` correspondence from the registry) was adopted one time in three; the
other two turns re-planned it into a per-record join the preflight guard then refused, or
into a count of the case's objects. The lock state is read through the collection store at
the one decision point and injected into the pure rule, so the rule is unit-tested without a
server. Gate: `test/test_signed_seed_replay.py`.

**Which repositories are read (Oct 2026, `planner.planner_binding_scope`).** Champion retrieval
reads only the planner repositories the agent binds (`ragCollections`): nothing bound means no
champions (it had read every repository the owner could see, beyond the signed composition);
only the explicit wildcard `"*"` still means every accessible repository. Each new Optimize agent
also learns into its own repository (`core/agent_learning.py`); see
[docs/RAG/RAG.md](../RAG/RAG.md) → "Where an agent learns". Gate: `test/test_planner_binding_scope.py`.

---

### 2.5 Strategic call hardening (Oct 2026)

Three rules on the strategic call itself, all found by the Optimize campaign on weak planners:

- **Parse-error retry.** `planner.parse_plan_response` reads an empty or truncated response
  (`""`, `"[{"`) as *no plan*; no plan sends the turn to ONE retry (`_strategic_retry`, priced and
  logged like the first call). A retry that is also no plan ends the turn. Gate:
  `test/test_strategic_plan_retry.py`.
- **Conversational guard.** The meta-planning prompt lets the model answer small talk with
  `{"plan_type": "conversational"}`. `_screen_conversational` accepts that only when the QUESTION is
  small talk (`agent/conversational_guard.goal_is_small_talk`, deterministic — greetings, thanks,
  farewells and filler only, ≤ 12 words); otherwise it re-plans once with `REPLAN_NOTE`. Gate:
  `test/test_conversational_replan.py`.
- **One prompt, one prefix.** A retry strips the master system prompt's duplicate capability
  catalog (`strip_capabilities=True`; 24.6K → 2.3K system tokens) and shares the first attempt's
  declared stable prefix (`_strategic_cache_prefix` → `cache_prefix`); see
  [PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md).

**The planner sees earlier turns.** `ContextBuilder._format_strategic_history` reads the session's
turns at `last_turn_data.workflow_history` (it had read a top-level `workflow_history` the session
never has, so every follow-up was planned blind). Gate: `test/test_strategic_history_source.py`.

## 3. Subsystem 1: Deterministic Plan Validation

**What:** A multi-pass pipeline of deterministic corrections applied to the raw LLM-generated plan before any execution begins.

**Why:** LLMs generate plans with predictable error patterns — misclassifying prompts as tools, hallucinating argument names, forgetting to wire data flow between phases. These errors are detectable by code and correctable without LLM cost.

### 3.1 The Structural Corrections (pass 14, `plan_validation`)

**File:** `src/trusted_data_agent/agent/planner.py` — `_validate_and_correct_plan()`

Seven corrections, applied per phase. Correction 5 runs BEFORE Correction 4, so a misspelled
argument is renamed for free instead of being deleted as extraneous and paying a refinement.

| Pass | Error Pattern | Detection | Correction |
|------|--------------|-----------|------------|
| 1 | Invalid `executable_prompt` values | Value in `[None, 'None', 'null', '', 'undefined']` | Delete the key |
| 2 | Prompt misclassified as tool | `relevant_tools[0]` found in `mcp_prompts` dict | Move to `executable_prompt`, delete `relevant_tools` |
| 3 | Tool misclassified as prompt | `executable_prompt` value found in `mcp_tools` dict | Move to `relevant_tools`, delete `executable_prompt` |
| 3b | Tool named for a server the agent does not have this turn (PROP-006 O8) | `<alias>__name` with an unknown alias | Re-key to the ONE catalog tool with that base name (`_rekey_unknown_server_tool`); ambiguous/absent ⇒ unchanged |
| 3c | Tool the catalog does not have at all | Invented SQL runner carrying a SELECT, component named with its action as suffix, near-miss name | Rename to the tool it meant (0 tokens); anything else stands |
| 5 | Parameter name mismatches (runs first) | Exact synonym, else `difflib.SequenceMatcher` ratio > 0.7 against missing schema params | Rename arg to matching schema parameter |
| 4 | Hallucinated/extraneous arguments (runs second) | Arg names not in tool schema (incl. `ARGUMENT_SYNONYM_MAP`) after renaming | Remove extraneous; flag `_needs_refinement` if required args now missing |

### 3.2 The Refinement Pipeline — 19 passes

**File:** `src/trusted_data_agent/agent/planner.py` — `generate_and_refine_plan()`

Every pass runs through ONE harness, `_pass(name, gen)`: a pass that raises is logged and skipped
(the turn continues on the pre-pass plan), and a pass that changed the plan emits a
`plan_rewrite_diff` event (§14.8). The names below are the `pass` names in those events.

| # | Pass name | Method | Purpose |
|---|-----------|--------|---------|
| 1 | `temporal_data_flow` | `_rewrite_plan_for_temporal_data_flow()` | Wire `TDA_CurrentDate` results to downstream tool date arguments |
| 2 | `governed_execution` (Aug 2026) | `_rewrite_plan_for_governed_execution()` | **The OBDA-READ seam guarantee.** A compile phase (`query_product`/`compile_metric`) whose result NOTHING consumes gains a following executor phase wired to `{"source": "result_of_phase_N", "key": "sql"}`. Under an EXCLUSIVE data surface it also rewrites in place any literal-SQL phase following a compile. Fires only on provably broken shapes; a correct plan is byte-identical. Runs before consolidation on purpose. |
| 3 | `sql_consolidation` | `_rewrite_plan_for_sql_consolidation()` | Only when `ENABLE_SQL_CONSOLIDATION_REWRITE`. Consolidate redundant sequential SQL queries (via `SQL_CONSOLIDATION_PROMPT`). **Never merges a phase whose SQL is a runtime reference** (`_sql_arg_is_literal`) — handing a placeholder to the consolidation LLM made it invent a query. |
| 4 | `llm_filter_deloop` | `_rewrite_llm_filter_deloop()` | A `TDA_LLMFilter` loop phase becomes one standalone filter call over the upstream key (inside a loop, argument pruning stripped `data_to_filter`) |
| 5 | `multi_loop_synthesis` | `_rewrite_plan_for_multi_loop_synthesis()` | Insert intermediate distillation phase between parallel loops and final summary |
| 6 | `llm_task_optimization` | `_rewrite_plan_for_corellmtask_loops()` | Correct plans where `TDA_LLMTask` is unnecessarily placed inside loops (aggregation tasks; a `synthesis` task keeps its loop) |
| 7 | `date_range_wiring` | `_rewrite_plan_for_date_range_loops()` | Convert `TDA_DateRange` + flat reference into proper loop; or wire `start_date`/`end_date` for range tools |
| 8 | `placeholder_sql` (Oct 2026) | `_rewrite_plan_for_placeholder_sql()` | A SQL phase written against a placeholder (`[FOUND_DATABASE].Customers`, `<database_name>`, `None`, a row-set phase's `result_of_phase_N`) is deferred to the tactical step — never run — with an inventory phase inserted when the plan reads none. Gate: `test_placeholder_sql_pass.py` |
| 9 | `database_before_sql` (Oct 2026) | `_rewrite_plan_for_database_before_sql()` | With a session catalog: a statement naming a database the source does not have is deferred. Without one: a plan that searches an inventory and then writes SQL against an invented database name is deferred. Champion-guided plans naming a database no champion uses are deferred too. Gate: `test_database_before_sql_pass.py` |
| 10 | `schema_before_sql` (Sep 2026) | `_rewrite_plan_for_schema_before_sql()` | Insert a column-description phase before raw SQL on a table the turn has no column list for, and withdraw the statement to the tactical step (§6.3, *Schema before SQL*) |
| 11 | `text_date_cast` (Sep 2026) | `_rewrite_plan_for_text_dates()` | Apply the preflight text-date rewrite to the plan's literal SQL once the column's stored shape is known |
| 12 | `compute_insertion` (Sep 2026) | `_rewrite_plan_for_compute_insertion()` | Insert a deterministic `TDA_Compute` phase before a synthesis phase whose goal is arithmetic (§6.3, *Compute with Lineage*); no-op unless `COMPUTE_LINEAGE` |
| 13 | `cross_source_shape` (Sep 2026) | `_rewrite_plan_for_cross_source_shape()` | Split a statement spanning several MCP servers into per-server fetches + a `TDA_Compute` op-sql combine (§6.3) |
| 14 | `plan_validation` | `_validate_and_correct_plan()` | The structural corrections of §3.1 |
| 15 | `chart_data_reuse` | `_rewrite_plan_collapse_chart_data_refetch()` | Eliminate redundant data re-fetch phases before charting / dashboard visualisation |
| 16 | `charting_cleanup` | `_rewrite_plan_for_charting_phases()` | Strip hallucinated `mapping` and `data` args from `TDA_Charting` phases; clear stale `_needs_refinement` |
| 17 | `plan_hydration` | `_hydrate_plan_from_previous_turn()` | Cross-turn data injection with source validation (§5.4) |
| 18 | `context_report` | `_rewrite_plan_for_empty_context_report()` | Synthesize answer from knowledge context when no tools needed |
| 19 | `final_report_guarantee` | `_ensure_final_report_phase()` | Guarantee terminal report phase exists |

Passes 8 and 9 share ONE deferral helper (`_defer_sql_phase`: the statement leaves the
arguments, the draft stays on the phase as `_sql_draft` and in the goal for its intent — the same
shape pass 10 produces), so a deferred statement never takes FASTPATH (§6.7). Both were
calibrated offline over 122 saved plans (`test/optimize_campaign/replay.py`) before a live turn.

### 3.3 Interconnections

- **Feeds S2**: A validated plan is checked for efficiency by Proactive Re-planning
- **Prevents work for S4**: Eliminating hallucinated args and misclassified tools means fewer runtime errors
- **Coordinates with S3**: Several rewrite passes (temporal flow, date range loops, charting cleanup) explicitly prevent hallucination patterns

---

## 4. Subsystem 2: Proactive Re-planning

**What:** After plan validation, the system evaluates whether the plan is structurally inefficient — specifically, whether it mixes high-level executable prompts (which spawn nested `PlanExecutor` sub-plans) with direct tool calls. If so, it forces a replan using only tools.

**Why:** Executable prompts create nested execution sub-plans (recursive `PlanExecutor` instantiation via `execution_depth`). When a plan already contains direct tool calls alongside prompts, the prompt phases represent unnecessary LLM overhead — the same goal can typically be achieved with direct tool calls at lower cost and latency.

**File:** `src/trusted_data_agent/agent/engines/optimize_engine.py` — the replan loop around `planner.generate_and_refine_plan()` (`max_replans = 1`)

### 4.1 Trigger Conditions

All must be true:

1. **`plan_has_prompt`**: At least one phase has `executable_prompt`
2. **`has_other_significant_tool`**: At least one phase is a non-prompt, non-`TDA_LLMTask` tool call
3. **`not is_single_phase_prompt`**: Plan has more than one phase
4. **`non_granted_prompts` exist**: The prompt is not in `GRANTED_PROMPTS_FOR_EFFICIENCY_REPLANNING`
5. **`execution_depth == 0`**: Only at the top level (prevents recursive replan loops)
6. **`replan_attempt < max_replans`**: At most one replan attempt (max_replans = 1)

### 4.2 Replan Context Injection

For each non-granted prompt, the system:
- Extracts the prompt's `description` via `_get_prompt_info()`
- Injects a `CRITICAL REPLANNING DIRECTIVE` telling the planner to replicate the goal using only basic tools
- Granted prompts receive a `CRITICAL PRESERVATION RULE` requiring they remain as plan phases

### 4.3 Configuration

| Key | Default | Purpose |
|-----|---------|---------|
| `GRANTED_PROMPTS_FOR_EFFICIENCY_REPLANNING` | `["base_teradataQuery"]` | Prompts exempt from efficiency re-planning (too complex to decompose into basic tools) |

---

## 5. Subsystem 3: Hallucination Prevention

**What:** A collection of guards distributed across planning and execution that detect and neutralize LLM hallucinations — not just in text output, but in plan structure, data references, and loop constructs.

**Why:** LLMs hallucinate at the structural level: inventing table columns that do not exist, creating loops over string literals instead of data references, injecting unresolved placeholder objects as arguments, and referencing plan phases that have not succeeded.

### 5.1 Date Range Orchestrator

**File:** `src/trusted_data_agent/agent/orchestrators.py` — `execute_date_range_orchestrator()`

Executes a tool over a calculated date range. Supports two modes:
- **Single-date tools** (`tool_supports_range=False`): Iterates the tool once per day
- **Range tools** (`tool_supports_range=True`): Calls tool once with `start_date`/`end_date`

**Guards:**

| Guard | Detection | Action |
|-------|-----------|--------|
| Unresolved placeholder | Date arg is `dict` with `source`/`key` keys not resolvable from `workflow_state` | Raises `ValueError` → forces tactical replanning |
| Single-date recursion | Date arg matches `YYYY-MM-DD` pattern | Executes directly, returns (prevents infinite recursion) |
| Empty date phrase | `date_phrase` is empty/None | Attempts regex extraction from `phase.goal`; raises `RuntimeError` if unrecoverable |

**Deterministic Temporal Resolution** (`_resolve_temporal_phrase_deterministic`):

Handles 12 temporal patterns without any LLM call:

| Pattern | Example |
|---------|---------|
| `today` | Current date |
| `yesterday` | Current date - 1 |
| `last/past N days` | N days back to yesterday |
| `last/past N weeks` | N weeks back to yesterday |
| `last/past N months` | N months back to yesterday |
| `this week` / `current week` | Monday to today |
| `last week` | Previous Monday to Sunday |
| `this month` / `current month` | 1st to today |
| `last month` | 1st to last day of previous month |
| `this year` / `current year` | Jan 1 to today |
| `last year` | Jan 1 to Dec 31 of previous year |

Falls back to LLM only for unrecognized temporal phrases.

**Plan-aware optimization:** When a previous phase has already calculated a date list and stored it in `workflow_state`, the orchestrator reuses it — bypassing both deterministic resolution and LLM calls entirely.

### 5.2 Column Iteration Orchestrator

**File:** `src/trusted_data_agent/agent/orchestrators.py` — `execute_column_iteration()`

| Step | Action |
|------|--------|
| 1 | Fetches actual column metadata via `base_columnDescription` tool |
| 2 | Queries tool constraints for required data type (numeric/character) |
| 3 | For each column: checks type compatibility, skips incompatible with explicit `"skipped"` result |
| 4 | Replaces placeholder `column_name` synonyms with actual column name per iteration |
| 5 | Consolidates all results |

### 5.3 Hallucinated Loop Orchestrator

**File:** `src/trusted_data_agent/agent/orchestrators.py` — `execute_hallucinated_loop()`

**Problem:** Planner generates `loop_over: ["revenue", "expenses", "profit"]` — string literals instead of a data source reference.

**Detection & correction:**

```
loop_over contains string items (not "result_of_phase_N")?
    |
    v
Single item with date keyword? ("day", "week", "month", etc.)
    |
  YES → Route to Date Range Orchestrator
    |
   NO → LLM semantic analysis:
        "What single tool argument do these items represent?"
        → Returns e.g. {"argument_name": "table_name"}
        → Execute tool once per item with correct arg name
```

### 5.4 Plan Hydration Validation

**File:** `src/trusted_data_agent/agent/planner.py` — `_hydrate_plan_from_previous_turn()`

When a plan references data from a previous turn:
1. Validates source phase exists in previous turn's execution trace
2. Checks `result_summary.get("status") == "success"`
3. Only injects data if validation passes
4. Modifies `loop_over` to reference `"injected_previous_turn_data"` key

### 5.5 Turn Validity Filtering

**File:** `src/trusted_data_agent/core/session_manager.py`

- Users can mark turns as invalid via UI (`isValid: false`)
- Invalid turns are filtered from planning context: `turn.get("isValid", True) is not False`
- Prevents the LLM from learning from its own failed attempts
- Invalid turns remain in audit trail but do not influence future execution

---

## 6. Subsystem 4: Intelligent Error Correction

**What:** A Strategy Pattern-based error correction system that intercepts tool execution failures, classifies them, and either generates corrected arguments via LLM or halts execution for definitive errors.

**Why:** Tool calls can fail for many reasons, and the optimal recovery strategy differs per error type. A table-not-found error requires different handling than a column-not-found error or a JSON parsing error.

**File:** `src/trusted_data_agent/agent/phase_executor.py` — `DEFINITIVE_TOOL_ERRORS`, `RECOVERABLE_TOOL_ERRORS`, `CorrectionStrategy` subclasses, `CorrectionHandler`

### 6.1 Error Taxonomy

**Definitive errors** — halt immediately, never retried:

```python
DEFINITIVE_TOOL_ERRORS = {
    # keys are re.search patterns, word-boundary anchored (a row value or a longer code
    # merely CONTAINING "3523" no longer aborts a recoverable error)
    r"\bInvalid query\b": "The generated query was invalid and could not be run against the database.",
    r"\b3523\b": "The user does not have the necessary permissions for the requested object.",
    # + the adapter's non-string-SQL fail-fast marker (see "Complementary deterministic rules")
}
```

**Recoverable errors** — matched by regex, trigger correction strategies:

```python
RECOVERABLE_TOOL_ERRORS = {
    "table_not_found": r"Object '([\w\.]+)' does not exist",
    # Aug 2026: three shapes, ONE pattern — generic + Teradata 3810
    # ("Column/Parameter 'db.S.Price' does not exist") + 5628
    # ("Column product_name not found in DB.tbl"). Before this, every Teradata
    # column error missed the regex and paid a Generic LLM round.
    "column_not_found": r"Column(?:/Parameter)?\s+'?([\w\.]+)'?\s+(?:does not exist|not found)",
    "database_not_found": r"Database '([^']+)' does not exist",
}
```

### 6.2 The Strategy Pattern

```
Tool Error Detected
    |
    v
Is it DEFINITIVE? (permission, invalid query)
    |
  YES → Raise DefinitiveToolError → Halt & Summarize
    |
   NO → CorrectionHandler.attempt_correction()
        |
        v
    Strategy chain (in order; a DECLINE — (None, []) with no events — continues to
    the next strategy; only a real result, or an LLM strategy that tried and failed,
    ends the chain — Aug 2026):
    ┌─────────────────────────────────┐
    │ 0. SurfaceRefusalStrategy       │  "is not on this agent's data surface — its surface is: X"
    │       DETERMINISTIC — 0 LLM     │  → swap product_name to the ONE surface product
    ├─────────────────────────────────┤
    │ 0b. TeradataSyntaxStrategy      │  3706/3707 on a form other dialects accept: `!=` → `<>`, `SELECT *, x` →
    │       DETERMINISTIC — 0 LLM     │  `SELECT <each source>.*, x`, `DATE_TRUNC` (day..year) → `TRUNC`, plus a
    │                                 │  trailing LIMIT in the same pass (sql_dialect.teradata_syntax_rewrite,
    │                                 │  shared with the ReAct/Navigator retry); hour/minute truncation declined;
    │                                 │  `COUNT(DISTINCT *)` over one plain source → COUNT(*) over SELECT DISTINCT *
    ├─────────────────────────────────┤
    │ 1. TeradataLimitStrategy        │  3706/syntax + trailing LIMIT n or trailing TOP n → SELECT TOP n
    │       DETERMINISTIC — 0 LLM     │  ONE rewrite (sql_dialect.teradata_top_rewrite) shared with the ReAct
    │                                 │  tool wrapper; declines (→ chain continues) on any other shape
    ├─────────────────────────────────┤
    │ 1b. TeradataReservedAliasStrategy│ 3706/3707 "… and the 'Month' keyword" (an alias, a table alias, a column) → the word the
    │       DETERMINISTIC — 0 LLM     │  error names is quoted wherever the statement uses it as an identifier
    │                                 │  (sql_dialect.teradata_reserved_alias_rewrite, shared with the ReAct wrapper)
    ├─────────────────────────────────┤
    │ 1c. TeradataDateLiteralStrategy │  6760: a bare ISO date string compared with a TIMESTAMP → DATE '…'
    │       DETERMINISTIC — 0 LLM     │  (sql_dialect.teradata_iso_date_literal_rewrite, shared with ReAct/Navigator)
    ├─────────────────────────────────┤
    │ 1d. TextDateStrategy            │  preflight refused a text-stored date used as a string → CAST(col AS
    │       DETERMINISTIC — 0 LLM     │  DATE FORMAT '<sampled shape>') on every mechanical use; declines when
    │                                 │  the shape is unsampled or a SUBSTR bucket is involved (judgement)
    ├─────────────────────────────────┤
    │ 1e. FanoutJoinStrategy          │  preflight refused a fan-out JOIN (two FK holders of one owner's key) →
    │       DETERMINISTIC — 0 LLM     │  each holder aggregated at its own grain, the aggregates joined, TOP…ORDER BY
    │                                 │  pushed into the ranked side (preflight.joins.rewrite_fanout_sql, shared with
    │                                 │  the ReAct twin); declines on COUNT(*), a mixed expression, a third table
    ├─────────────────────────────────┤
    │ 2. TableNotFoundStrategy        │  regex: "Object '...' does not exist"
    ├─────────────────────────────────┤
    │ 3. ColumnNotFoundStrategy       │  Teradata 3810/5628 + generic; KG-backed fix first (0 LLM)
    ├─────────────────────────────────┤
    │ 4. DatabaseNotFoundStrategy     │  regex: "Database '...' does not exist" (3802)
    │       DETERMINISTIC — 0 LLM     │  → base_tableList(<KG scope>) / base_databaseList
    ├─────────────────────────────────┤
    │ 5. GenericCorrectionStrategy    │  always matches (fallback)
    └─────────────────────────────────┘
        |
        v
    Corrected action (LLM only where deterministic strategies decline)

The DECLINE CONTRACT (Aug 2026) is load-bearing: the deterministic strategies
(SurfaceRefusal on a multi-product surface, TeradataLimit on a non-LIMIT syntax error)
return `(None, [])` to mean "not handled" — the handler CONTINUES the chain, so the
Generic LLM fallback stays reachable. Before this the first `can_handle()` match
returned unconditionally, and TeradataLimit (which claims any "Syntax error") aborted
ALL correction on a non-LIMIT syntax error ("Unable to find a correction").
        |
        v
    Retry with corrected arguments (up to 3 times)
```

### 6.3 Strategy Details

| Strategy | Trigger | Recovery Approach | Prompt |
|----------|---------|-------------------|--------|
| `TableNotFoundStrategy` (Sep 2026: facts-first) | Regex match on table name | **Step 1 (0 LLM):** an UNQUALIFIED table this turn's own table list places in exactly one database is qualified (`database.Table`). **Step 2:** with no knowledge graph, the prompt gets SCHEMA FACTS read from the database — the named database's table list, else the tables this turn listed, else the tables of the few user databases this turn listed — and the directive to rewrite the SAME statement. The prompt never carries a placeholder a model could copy as a database name. | `TACTICAL_SELF_CORRECTION_PROMPT_TABLE_ERROR` |
| `TeradataSyntaxStrategy` (Oct 2026) | 3706 / 3707 / syntax error text | **Deterministic, 0 LLM.** Delegates to ONE rewrite, `agent/sql_dialect.teradata_syntax_rewrite`, also used by the ReAct / Navigator retry (`tool_enforcement._syntax_retry_core`, first in `dialect_retries`): `!=` → `<>`; `SELECT *, expr` → `SELECT <each FROM source>.*, expr` in FROM order, by alias or by the name as written (a bare `orders.*` for `FROM db.orders` is ambiguous, Error 3806); `DATE_TRUNC('day'|'week'|'month'|'quarter'|'year', x)` → `TRUNC(x, 'DD'|'IW'|'MM'|'Q'|'YYYY')`; a trailing LIMIT in the same statement is moved to TOP in the same pass. Declines hour/minute truncation (`TRUNC(ts,'HH')` returns the DATE), an unaliased derived table, multi-statement requests; text in quotes/comments is never read. Runs only after Teradata refused the statement, so a backend that accepts the original never sees it. Found by executing Loom's Teradata patterns; each rule verified on 20.00.24.60. |
| `TeradataLimitStrategy` (Aug 2026; Sep 2026 shared) | 3706 / syntax error text | **Deterministic, 0 LLM.** Delegates to ONE rewrite, `agent/sql_dialect.teradata_top_rewrite`: a trailing `LIMIT n` **or a misplaced trailing `TOP n`** (`… ORDER BY x DESC TOP 30`, the production ReAct shape) → `SELECT TOP n`; declines DISTINCT (Teradata refuses TOP+DISTINCT), a QUALIFY in the outer query block (TOP + QUALIFY in one block is Error 6916; a QUALIFY inside a derived table is another block and is still rewritten), an existing TOP, OFFSET, multi-statement and non-SELECT. Declines `(None, [])` on any other shape, so a syntax error it can't mechanically fix continues to the LLM. The same function serves the ReAct tool wrapper's one-shot retry (`tool_enforcement.retry_teradata_top`), so both engines correct the dialect identically. | *(none — deterministic)* |
| `TeradataReservedAliasStrategy` (Sep 2026; Oct 2026 every name position) | Error 3706/3707 naming a reserved word as "the '<word>' keyword" — an alias (`AS Month`), a table alias (`… input` → "between the 'SELECT' keyword and the 'input' keyword"), a qualified column (`t.Count` → "between '.' and the 'Count' keyword") or a bare column; a misplaced real keyword is declined because it is not an identifier in the statement | **Deterministic, 0 LLM.** Delegates to ONE rewrite, `agent/sql_dialect.teradata_reserved_alias_rewrite`: the word the ERROR names (no reserved-word list is kept) is quoted as a delimited identifier wherever the statement uses it as an identifier — the alias and every reference to it (`AS "Month"`, `ORDER BY "Month"`); a keyword use of the same word (`EXTRACT(MONTH FROM …)`) is never an identifier and stays untouched; SUBSTR/CHAR_LENGTH are rendered back verbatim; declines on multi-statement, an already-quoted or absent identifier, or a result that does not re-parse. Measured: the production statement failed unquoted and returned the twelve months quoted. The same function serves the ReAct wrapper's one-shot retry (`tool_enforcement.retry_teradata_reserved_alias`, sharing ONE retry body with the TOP retry). | *(none — deterministic)* |
| `TeradataDateLiteralStrategy` (Sep 2026) | Error 6760 (invalid timestamp) on a bare ISO date string compared with a TIMESTAMP (`SaleDate >= '2026-08-25'`) | **Deterministic, 0 LLM.** ONE rewrite, `agent/sql_dialect.teradata_iso_date_literal_rewrite` (also behind the ReAct/Navigator retry): the string becomes `DATE '2026-08-25'`. Declines `(None, [])` when the statement has no bare ISO date string. Emits a `System Self-Correction` workaround (`correction_type: teradata_date_literal`). | *(none — deterministic)* |
| `TextDateStrategy` (Sep 2026) | `[Preflight] … DATE stored as` (the preflight text-date refusal) | **Deterministic, 0 LLM.** Delegates to ONE rewrite, `preflight.textdates.rewrite_sql` (also the ReAct wrapper's twin and the planner's `text_date_cast` pass): every proven finding whose fix needs no judgement — a range/equality comparison, `ORDER BY`, `MIN/MAX`, a DATE-expression comparison, `EXTRACT`, a mismatched `FORMAT` — is rewritten to the measured cheapest safe read of the column (`dateforms.date_read_form`: a plain CAST on an exact clean share, a `CHAR_LENGTH` guard on a sampled one, a regex-free rebuilt-ISO `TRYCAST` where non-date values were measured — never the regex guard, which ran past the 110 s ceiling on a 12.35M-row table) with the literal beside it turned into a DATE literal. Declines `(None, [])` when the shape has not been sampled yet, when a `SUBSTR`/`LEFT` slice cuts ACROSS the shape's fields (which period the model meant is its call; a slice aligned to the fields — the month of a DD-MMM-YYYY — is correct and never a finding), on an ambiguous slash literal, or when the rewrite does not re-parse clean — so the recovery-shaped refusal reaches the LLM exactly as before. Emits one `System Self-Correction` workaround event (`correction_type: text_date_cast`) naming every change. | *(none — deterministic)* |
| `FanoutJoinStrategy` (Sep 2026) | `[Preflight] … multiplies rows` (the preflight fan-out refusal) | **Deterministic, 0 LLM.** Delegates to ONE rewrite, `preflight.joins.rewrite_fanout_sql` (also the ReAct wrapper's `fanout_rewrite` twin): each FK holder aggregated at its OWN grain in a derived table (key + its grouped columns + its aggregates + its own WHERE conjuncts), the aggregates joined on the key, a `TOP n … ORDER BY` whose terms all belong to one side pushed INTO that side with the INNER join's membership carried in (`k IN (SELECT k FROM other …)`) and a same-side HAVING moved inside with it; the model's GROUP BY grain preserved exactly, the result naming the grain and the way to one row per key. Declines (→ the recovery-shaped refusal reaches the LLM as before): `COUNT(*)`, an expression/predicate mixing both tables, a third table, a subquery/CTE/UNION/window, `SELECT *`, an ON beyond the key equality, `WITH TIES`/`PERCENT`, an unattributable column. Re-runs every join check on its output. Prod 940f02fa: the model's own correction cost 1–2 further rounds (a 46K-token probe) that the rewrite removes | — |
| `ColumnNotFoundStrategy` (Aug 2026: KG-backed) | Teradata 3810/5628 + generic `Column '…' does not exist` | **Step 1 (0 LLM):** resolve the reference through the SQL's FROM/JOIN aliases and look the table up in the turn's KG schema (`kg_enrichment_event.enrichments[].table_columns`); a unique case/underscore/fuzzy match in the SAME table rewrites the SQL mechanically. **Step 1b (Sep 2026, 0 LLM):** with no knowledge graph, the statement's tables are described through the agent's own column-description tool (`agent/schema_facts.py`; the session store first, every read recorded for the session) and resolved the same way. **Step 2 (LLM):** otherwise the prompt gets an appended SCHEMA FACTS block (the table's real columns + where the column actually lives + JOINABLE keys) — facts instead of "describe the table". Nothing read ⇒ nothing added. | `TACTICAL_SELF_CORRECTION_PROMPT_COLUMN_ERROR` |
| `DatabaseNotFoundStrategy` (Jul 2026) | Regex `Database '([^']+)' does not exist` (Error 3802) | **Deterministic, 0 LLM.** Pivots to schema discovery in the KG-declared deployed scope: `base_tableList(<scope_db>)` → else `base_databaseList(scope=all)`; delegates to Generic only when no discovery tool is available. Reads `executor.kg_enrichment_event` (same scope extraction as the grounding gate). Closes the hole where a hallucinated logical name (`SELECT * FROM airport.station`) hit the blind Generic retry. | *(none — deterministic)* |
| `SurfaceRefusalStrategy` (Aug 2026) | "…is not on this agent's data surface — its surface is: X" | **Deterministic, 0 LLM.** The refusal names its own remedy: when it names exactly ONE surface product, swap `product_name` and retry (everything else preserved). A MULTI-product surface falls through to the LLM — which product answers the question is judgement; the swap is only deterministic with one legal door. Already-at-the-door falls through rather than looping. The doctrine: *a structured refusal that names its own remedy never costs an LLM round.* | *(none — deterministic)* |
| `GenericCorrectionStrategy` | Always (fallback) | Sub-handler for JSON parsing errors in report tools → routes to `TDA_LLMTask` for text sanitization. Otherwise, generic correction with full tool definition | `TACTICAL_SELF_CORRECTION_PROMPT` |

**Complementary deterministic rules (Aug 2026):** a **non-string SQL argument fails fast + definitively** at BOTH chokepoints (`tool_enforcement.nonstring_sql_block`, marker in `DEFINITIVE_TOOL_ERRORS` — no correction rounds; retrying cannot change what the upstream produced); the product compiler **coerces the common filter shapes** onto `{attr, op, value}` (`normalize_filters` — accepted SYNTAX widens, accepted MEANING never); and a **dependency-failure skip** (`_failed_upstream_refs`, checked BEFORE argument resolution) skips a phase whose referenced upstream FAILED — no tool call, no correction rounds, its own honest error result recorded so later references cascade. A MISSING upstream is NOT a failure (parallel groups resolve later).

**ReAct-lane parity (Sep 2026):** every deterministic rewrite the ReAct tool wrapper performs (the `retry_teradata_top` / `retry_teradata_reserved_alias` retries, the `text_date_rewrite` and `fanout_rewrite` twins) is recorded at ONE seam (`tool_enforcement.record_rewrite`, a per-turn ledger the agent arms) and emitted by `ConversationAgentExecutor` as a persisted `conversation_tool_rewrite` event — the lane's equivalent of this table's `System Self-Correction` step, carrying the executed statement, sealed as `agent_tool_rewrite` in the provenance chain, rendered on live/notification/reload and narrated by the KG theatre.

**Schema before SQL (Sep 2026, multi-source plan stage 3.0 — `agent/schema_facts.py`).** Found by the multi-source bench: on an agent with no knowledge graph the planner wrote every data phase's SQL with guessed names, and the Optimize lane answered 0 of 39. Four deterministic rules, one fact source: **(1)** the `schema_before_sql` plan pass (after `date_range_wiring`, before `text_date_cast`) inserts a column-description phase before a raw-SQL phase that reads a table the turn has no column list for, and leaves that statement to the tactical step (the draft kept in the goal as intent, `_sql_draft` on the phase) — never on a champion-guided plan, never without a describe tool, never for a table the knowledge graph or the session already knows, at most four describes per plan; the inserted describe waits for earlier discovery (`_phase_discovery_deps`) and settles its table on this turn's own listing when the plan's guess is plainly one listed table (`settle_describe_args`); a withdrawn statement never takes FASTPATH. **(2)** the column/table strategies read facts from the database when no graph has them (above). **(3)** in the retry loop, a correction that stands a discovery call in for a failed SQL call gets its successful result treated as FACTS — the original statement is corrected again with them and retried, the stand-in's attempt granted back once — instead of that describe ending the phase as its answer. **(4)** in the tactical loop of a withdrawn-statement phase, a successful discovery action is recorded as facts in the phase's correction history and the loop continues; only the query completes the phase. Also: a reference dotted to a reference (`result_of_phase_2.result_of_phase_4`) resolves to `database.table` when both sides are scalars. Gate: `test/test_schema_facts.py`.

**Complementary preflight (prevention, all engines):** `agent/preflight/` runs FIVE checks on every raw-SQL call at both chokepoints (`invoke_mcp_tool` and the ReAct tool wrapper), each with its own `off | observe | enforce` mode, each fail-open by construction. **(1) Schema grounding** (`PREFLIGHT_SQL_MODE`, default observe): FROM/JOIN databases validated against the session's authoritative `base_databaseList(scope=all)` catalog; `enforce` short-circuits with a 3802-shaped error so `DatabaseNotFoundStrategy` engages; no catalog → no-op. **(2) Declared join key** (`PREFLIGHT_JOIN_MODE`, env `TDA_PREFLIGHT_JOIN_MODE`, default observe, production enforce): a JOIN between two tables whose key the bound ontology declares (`join_keys` enrichment metadata, recorded per session at the trusted-scope seam), on columns that are not that key, is refused with the rewrite spelled out — the runaway-join class. The same check refuses a **fan-out join** (Sep 2026): two tables joined to EACH OTHER on a column both hold as a FOREIGN key to a third table (`fanout_keys` enrichment metadata, computed from the graph's column-level FK edges by the same function that renders the *NOT JOIN KEYS (fan-out)* block) — fact-to-fact through a dimension key, a per-key total many times too large (prod: 149×); the refusal names the owner table and the aggregate-then-join shape, and a DECLARED pair between the two always wins. **(3) Text-stored date** (`PREFLIGHT_TEXT_DATE_MODE`, env `TDA_PREFLIGHT_TEXT_DATE_MODE`, default observe, production enforce): a DATE the ontology knows is deployed as characters (`text_date_columns` metadata), compared as a string, ordered bare, MIN/MAX'd or bucketed with SUBSTR, is refused; the stored SHAPE is learned from result rows at the same chokepoints, so the refusal names the exact `CAST(col AS DATE FORMAT '…')` once a sample has passed, and the one probe that settles it before that. A correct CAST passes; a FORMAT-bearing DATE compared against a bare string literal (converted with that FORMAT — Error 2665, measured) is a proven finding rewritten to a typed `DATE '…'`, resolved through a select alias too. A graph validated against its source (`validate_with_source`) seeds the guard with the sampled shape, so the probe round never happens. **The refusal is the last resort, not the first:** once the shape is known in the process, the planner's `text_date_cast` pass (14th, after `date_range_wiring`, before `plan_validation`) rewrites the plan's literal SQL before it runs, and a refusal that still happens is corrected at 0 LLM by `TextDateStrategy` (Optimize) or the wrapper twin (ReAct) — three seams, ONE `textdates.rewrite_sql`. **(4) Value existence** (`PREFLIGHT_VALUE_MODE`, env `TDA_PREFLIGHT_VALUE_MODE`, default observe, production enforce; `agent/preflight/values.py`) — the hallucinated-LITERAL class (prod 2026-09-16: `BH020 = '031'`, Cook County's FIPS code on the UCR county-code column, a value in zero rows → 0 → "there were none"). Two verdicts from two evidence sources: **PROVEN** — the ontology declares the column's value domain (`value_domains` enrichment metadata: a coded column's codes, or the LABELS of the code it decodes — the walker links the pair by `source_column`) and the literal is in neither; refused naming the domain and, when the literal is a label on the code column (or a code on the label column), the exact fix. Only a CLOSED enumeration is proof: a domain with a range entry (`'91 to 818'`) or nothing but missing-value codes (`'-6'`) is open and never proves absence (both were live false positives on the first run). **UNVERIFIED** — no domain and nothing this session has shown the literal occurs in the column: never refused pre-execution (a city name is a legitimate first filter); instead an EMPTY result on it is stamped after execution (`preflight_note` / `preflight_unverified` on the Optimize lane, an appended `[Preflight:value]` note on ReAct) — a zero on an unverified value is a claim about the FILTER, and the note names the distinct-values probe. Every value a result row carries is an observed value for its column (the data vouching for itself), so a value the data showed is never questioned. The honesty gate's `unverified_literal` kind reads that stamp at answer time: a non-existence claim resting on it is disclosed/rewritten. **(5) Column existence** (`PREFLIGHT_COLUMN_MODE`, env `TDA_PREFLIGHT_COLUMN_MODE`, default observe; `agent/preflight/columns.py`) — the hallucinated-COLUMN class (prod 2026-09-19: `boarding_event.miles_flown`, a database error + an LLM round + a `SELECT *` to learn a schema the graph held). A column the statement attributes to a table whose COMPLETE column list the session knows — the enrichment emits `table_columns_full` for the tables in play and records the whole graph's lists once per session (`GraphStore.all_table_columns`) — and which the table does not have, is refused naming the real columns and the closest matches (tables in play first); the error carries the 3810 phrase so the column-not-found strategy engages. Armed only for a column-complete graph read from the graph's content (industry model root `industry`+`scope`, `standard: iso_11179`, `source: agent_discovery` — never ANDP, never an unrecognised graph); anything the parse cannot attribute (an unqualified column across several tables, a SELECT alias, a CTE) is no verdict. Design: [KNOWLEDGE_GRAPH_ARCHITECTURE.md](KNOWLEDGE_GRAPH_ARCHITECTURE.md) (Context Enrichment → structured facts for the preflight guard).

**Compute with Lineage — the `compute_insertion` pass + the deterministic compute phase (Sep 2026, S3 of the conversation-agent roadmap).** Under the deployment switch `COMPUTE_LINEAGE` (default off ⇒ this pass is a no-op and `TDA_Compute` is absent from the catalog), a synthesis phase (`TDA_FinalReport`, `TDA_LLMTask`, `TDA_ContextReport`) whose goal is arithmetic over the data phase before it — the intent vocabulary `_COMPUTE_INTENTS` maps `total / sum / add up → sum`, `average / mean → avg`, `top N / highest / largest / rank → rank`, `percent change / change since / growth rate / trend → pct_change`, `minimum / lowest / smallest → min`, `maximum → max` — gets ONE `TDA_Compute` phase inserted in front of it (`{"op": …}`, the statement defaulting to the latest), with every later `result_of_phase_N` reference renumbered through the same `renumber_plan_refs` the other insertion passes use. It **declines** when the data phase's SQL already carries the aggregate for that intent (`SUM(` for a total, `ORDER BY`/`TOP n`/`LIMIT` for a rank — the database's figure is exact already and a second computation would restate it), when a phase already computes, when no data phase precedes the synthesis, when the component is disabled for the profile (`componentConfig.compute.enabled: false` — the same enablement rule as every other component), and it is idempotent on a replan. The executor's phase scheduler gives the inserted phase a dependency on **every earlier data phase** (`_phase_compute_deps`, the third complement beside the referenced and discovery deps) — found live: without it the compute phase ran in the same parallel group as the query it computes over and computed over nothing. At execution the component reads the rows from the turn's result-set registry (fed at the adapter's post-success seam), computes in-process from a closed op set, and records a receipt; the end-of-turn seam seals each receipt into the provenance chain (`compute_step`), traces every figure in the delivered answer to a receipt or a result row, marks the prose, and reports at GROUNDED (`compute_lineage`). Full contract: [restAPI.md §3.13.7](../RestAPI/restAPI.md); design: `BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3`.

**The cross-source plan shape — the `cross_source_shape` pass (Sep 2026, multi-source plan stages 3a–3b — `agent/cross_source.py`).** A multi-server agent's planner is told, in the one *Data sources* note both lanes read, which server holds which databases **and their tables and columns** (stage 3a: each server's catalog is read with its database list through the server's own list-tables / describe tools resolved by role — `source_routing.ensure_catalogs` — cached with the list, bounded to 6 user databases / 40 tables per server with columns only when a server has ≤ 16 tables, and seeded into the session's column store so the `schema_before_sql` pass and the correction strategies know those tables). Traced on the bench: with the databases alone the planner invented tables (`ms_fixture_crm.purchases`) or put real ones in the wrong database; with the tables it names the right ones — and then puts them in ONE statement, which the routing layer refuses (no server holds both), self-correction swaps in a one-server query, and the report states a confident wrong number for the whole. So a raw-SQL phase whose statement reads tables held by different servers — or reads an earlier phase's rows as a table (`… IN (SELECT CustomerID FROM s1)`, the shape the note draws out of a planner told to query each server separately and combine) — is **split by code, 0 LLM rounds**: one fetch phase per server (the columns the statement uses of that server's table, that table's own WHERE conjuncts pushed down only when every join is INNER, run through that server's own tool, stamped `_server`) and the **original statement** as a `TDA_Compute` op-sql combine phase over `p<N>` (join, aggregates, GROUP BY, ORDER BY, `TOP` → `LIMIT` unchanged — isolation rules, completeness and the sealed receipt apply as for any combine); every reference to the original phase follows it through `renumber_plan_refs`; the scheduler runs the fetches in one parallel group and the combine after them (`_phase_compute_deps`). **Declined, on purpose** — an unplaceable or unqualified table, two tables of one server in one statement, a nested SELECT other than `col IN (SELECT one column FROM one table …)`, a CTE, a UNION, `SELECT *`, a column it cannot attribute to one table, a forward phase reference — the statement is withdrawn to the tactical step with the split named (the `_sql_draft` mechanism), never rewritten by the pass into a one-server query. The same splitter runs in the phase executor when a statement the planner did not see (a champion's, a tactical rewrite's) is refused as spanning (the refusal carries `code: spans_servers` + `split`: database → server): fetch per server, combine by `s<N>`, the phase answered by the combine (`cross_source_split`); declined there, the phase **ends with the refusal** — a one-server substitute proposed by self-correction is never the phase's answer, and no LLM round is spent (`cross_source_unsplit`). Runs after `compute_insertion`, before validation; a no-op for a single-server agent and with the combine step (`COMPUTE_SQL`) off. Gate: `test/test_cross_source_plan.py`.


### 6.4 Ground Truth Enforcement

The table and column error prompts are specifically designed to prevent the LLM from guessing:

- **Table error:** *"Your primary rule is: DO NOT GUESS ANOTHER TABLE NAME. You must work only with the facts."*
- **Column error:** *"Substituting a semantically similar column (e.g., using an 'address' column for a 'zip_code' request) is a forbidden action."*

This forces the LLM to use discovery tools (`listTables`, `describeColumns`) rather than inventing new names — and where a KG is bound, `ColumnNotFoundStrategy` resolves the real column deterministically or hands the LLM the actual schema facts (Aug 2026, §6.3) instead of asking for a tool the profile may not have.

**Honesty post-condition (Aug 2026).** The honesty gate (`agent/honesty/`) adds a `fabricated_after_failure` kind: a turn whose data queries ALL failed and whose answer nonetheless states a quantitative result ("There were 30 days of sales activity recorded") — without disclosing the failure — is flagged/rewritten. Ground truth is enforced at the answer, not only at the tool call. **Oct 2026:** the `unsourced_record` kind — a record (table row / Field: value) presented by a tool-enabled turn that ran NO data query, with values in nothing the model was given — is flagged/disclosed/replaced the same way (see [ENGINE_MODULARIZATION_ARCHITECTURE.md](ENGINE_MODULARIZATION_ARCHITECTURE.md) → ConversationEngine).

### 6.5 LLM Correction Output Parsing

The `CorrectionStrategy._call_correction_llm()` method parses LLM responses into four possible outcomes:

| Response Format | Action |
|----------------|--------|
| `FINAL_ANSWER:` prefix | Task complete — halt retries, return answer |
| JSON with `prompt_name` + `arguments` | Switch to different prompt (escalation) |
| JSON with `tool_name` + `arguments` | Retry with different tool or corrected args |
| JSON with just `arguments` | Retry same tool with corrected args |

Special handling: When the LLM proposes `TDA_LLMTask`, the system auto-injects required context args (`extra_args_for_llm_task`). **Catalog validation (Aug 2026):** a proposal naming a `prompt_name` that is actually a TOOL (or `TDA_*`) is re-keyed to `tool_name`; a `prompt_name` in no catalog is rejected rather than executed as a non-existent sub-prompt (which used to fail on the sub-prompt path).

**Identical call refused (Oct 2026, OC-F14).** A correction that proposes the very call that
just failed — same tool, same arguments (`_same_call_args`) — is refused deterministically: no
resend, no further round, a `correction_output_validation` error event. A correction that swaps
a database tool for one reading only phase results is run as the combine step
(`TDA_Compute` op sql, 0 LLM).

### 6.6 Profile-Aware Prompt Resolution

All correction strategies use `executor.prompt_resolver` (a `ProfilePromptResolver` instance) to load correction prompts. This allows different profiles to customize recovery behavior — for example, a profile for Teradata databases could use different correction prompts than one for PostgreSQL.

### 6.7 FASTPATH — when the tactical LLM is skipped

**File:** `src/trusted_data_agent/agent/phase_executor.py` — `_execute_standard_phase()` (and the
loop variant in the tool-loop path)

A single-tool phase runs without a tactical LLM call when every REQUIRED argument of the tool
(canonicalised through `ARGUMENT_SYNONYM_MAP`) is present in the strategic plan and valid. A
candidate is rejected (falls to the tactical path) when a required value is:

- `None` or `""`;
- a dict placeholder (`{"source": …}` / `{"key": …}`) — except a resolvable phase reference
  (`_is_resolvable_phase_ref`), which the executor resolves deterministically;
- a list of plain strings (hallucinated loop items);
- a temporal phrase ("past 5 days", "yesterday", …) that needs date resolution;
- text still holding an unresolved `result_of_phase_N` or `injected_previous_turn_data`.

Two statement rules close the "No SQL provided" success (FastMCP 4 declares `sql` optional, so
the required-arguments check passed on `{}` — OC-F17): a phase whose statement a plan pass
withdrew (`_sql_draft`) is never fast-pathed without SQL, and **any** phase on a SQL runner
(`_takes_sql`) that carries no statement is rejected — in the single-tool path and the loop path.
FASTPATH `plan_optimization` events carry `phase_num` (§14.11), and the configured tactical model
is still tracked (`planning_phase="tactical_fastpath"`). Gate: `test/test_fastpath_sql_required.py`.

### 6.8 What a correction sees (Oct 2026)

- **The database's message.** A FastMCP 4 server raises a failed tool call as `McpError` inside
  TaskGroup exception groups; `tool_enforcement.tool_error_text` reads the server's own text out of
  the leaves (dropping the `Internal error: ` envelope) on BOTH lanes — the Optimize adapter's error
  path and the ReAct wrapper. Without it every SQL correction ran blind. Gate:
  `test/test_mcp_raised_tool_error.py`.
- **A compact, surface-governed catalog.** Correction prompts receive
  `executor._get_correction_catalog()` — names, the opening statement of each description and
  argument names — cached per turn and filtered by the governed tool surface (the same rule the
  planner's catalog reads), keyed by the shown set so a tool found mid-turn appears in the next
  correction (19.4K → 3.4K tokens). Gate: `test/test_token_cost_efficiency.py`.
- **Delegated prompts are billed once.** A sub-executor's own usage is merged as a delta
  (`executor._merge_sub_totals`), so a parallel sibling's priced call made while it ran is kept.
  Gate: `test/test_sub_prompt_totals.py`.
- **A failed turn tells the truth.** A provider failure ends the turn with one honest sentence
  (`agent/turn_failure.answer`, never the raw error), a failed record and a red JUDGED — see
  [ANSWER_GATES_ARCHITECTURE.md](ANSWER_GATES_ARCHITECTURE.md).

---

## 7. Subsystem 5: Phase Exhaustion (Autonomous Recovery — removed)

**Status:** the LLM recovery replan (`_recover_from_phase_failure`, `ERROR_RECOVERY_PROMPT`,
`globally_skipped_tools`) was **removed**. It had been dead code since the Apr-2026 parallel
phase-groups refactor: it generated a new plan and reset `meta_plan` / `current_phase_index`, but
`_run_plan` iterates a `phase_groups` snapshot built once from the original plan — so the paid
recovery plan never executed while the original plan's remaining phases ran.

**File:** `src/trusted_data_agent/agent/phase_executor.py` — `_execute_standard_phase()` (the
tactical slow-path loop)

### 7.1 Trigger

```python
phase_attempts > APP_CONFIG.MAX_PHASE_RETRY_ATTEMPTS  # default: 5
```

### 7.2 Behaviour

```
Phase fails after MAX_PHASE_RETRY_ATTEMPTS
    |
    v
1. Record an error result as last_tool_output AND workflow_state["result_of_phase_N"]
   ("Phase failed after N attempts." + the phase goal)
    |
    v
2. Emit a "Phase Failed After N Attempts" error event (logged to the trace)
    |
    v
3. End the phase — the turn continues; dependent phases are skipped by the
   dependency-failure rule (§6.3) and the final report states what is missing
```

0 LLM calls. The honesty gate's `fabricated_after_failure` kind (§6.4) covers an answer that
nevertheless states a result. `ERROR_RECOVERY_PROMPT` and
`ProfilePromptResolver.get_error_recovery_base_prompt()` still exist but no execution path calls
them.

---

## 8. Subsystem 6: Context Distillation

**What:** A multi-layered data reduction system that protects the LLM context window from overflow while preserving maximum analytical fidelity.

**Why:** Tool results frequently return hundreds or thousands of rows. Injecting raw data into LLM prompts would exceed context limits and degrade response quality. The distillation system ensures every LLM call operates within its token budget.

### 8.1 Architecture

```
  CONTEXT DISTILLATION — THREE LAYERS
  =====================================

  Layer 1: Execution Context Distillation (intra-turn)
  ┌────────────────────────────────────────────────────────────┐
  │  ExecutionContextDistiller (distiller.py)                  │
  │                                                            │
  │  Trigger: results[] > max_rows (500) OR > max_chars (10K) │
  │  Action:  Recursive traversal of nested dicts/lists        │
  │           Replace large results with metadata summary      │
  │  Used by: Tactical planning, TDA_LLMTask, error recovery  │
  └────────────────────────────────────────────────────────────┘

  Layer 2: Analytical Distillation (report generation)
  ┌────────────────────────────────────────────────────────────┐
  │  _distill_workflow_for_report() (adapter.py)              │
  │                                                            │
  │  L1 Trigger: >100 rows OR >50K chars per result set       │
  │  L1 Action:  Column statistics on FULL dataset             │
  │              + Stratified sample (boundary + spaced)       │
  │                                                            │
  │  L2 Trigger: Total distilled size > 200K chars             │
  │  L2 Action:  Aggressive reduction to 25 rows               │
  │              Column statistics PRESERVED (computed on full) │
  └────────────────────────────────────────────────────────────┘

  Layer 3: Context Window Budget Assembly (per-LLM-call)
  ┌────────────────────────────────────────────────────────────┐
  │  ContextWindowHandler (handler.py)                        │
  │                                                            │
  │  Pass 1: Resolve active modules (skip deactivated)        │
  │  Pass 2: Apply dynamic adjustments (condition-based)      │
  │  Pass 3: Allocate per-module budgets + assemble           │
  │  Pass 3b: Reallocate surplus (under→over utilization)     │
  │  Pass 4: Condense if over budget (lowest priority first)  │
  └────────────────────────────────────────────────────────────┘
```

### 8.2 Layer 1: Execution Context Distiller

**File:** `components/builtin/context_window/distiller.py`

```python
class ExecutionContextDistiller:
    def __init__(self, max_rows=500, max_chars=10_000):
        # Configurable thresholds from context window type

    def distill(self, data, events=None):
        # Recursive traversal of nested dicts/lists
        # results[] > max_rows OR > max_chars
        # → Replace with {status, metadata{row_count, columns}, comment}
```

**Used at three points:**
1. Tactical planning — before the tactical LLM call
2. `TDA_LLMTask` focused payloads — custom data preparation
3. Multi-loop error recovery — simplify context for self-correction prompts

### 8.3 Layer 2: Analytical Distillation

**File:** `src/trusted_data_agent/mcp_adapter/adapter.py` — `_distill_workflow_for_report()` and helpers

**Two-level system:**

| Level | Trigger | Action | Preserves |
|-------|---------|--------|-----------|
| L1 | >100 rows OR >50K chars per result set | Compute per-column statistics on FULL dataset + stratified sample | Full statistical profile |
| L2 | Total distilled size >200K chars | Aggressive reduction to 25 rows per result set | Column statistics (computed on full data during L1) |

**Stratified sampling algorithm:**
```
Take boundary rows (first 5, last 5)
Fill interior with evenly-spaced samples
→ Ensures head/tail/middle coverage instead of naive first-N truncation
```

**Column statistics by type:**

| Type | Detection | Statistics Computed |
|------|-----------|-------------------|
| Numeric | >80% successful `float()` conversion | min, max, mean, sum, median, p25, p75, stddev |
| Temporal | Column name heuristics ("date", "time", "timestamp") | range, distinct count, top-10 values with frequency |
| Categorical | Fallback (everything else) | distinct count, top-10 distribution with percentages |

### 8.4 Layer 3: Context Window Budget Assembly

**File:** `components/builtin/context_window/handler.py`

The ContextWindowHandler implements a 5-pass budget assembly algorithm:

| Pass | Purpose |
|------|---------|
| Pass 1 | Resolve active modules — skip deactivated/inapplicable, redistribute budget |
| Pass 2 | Apply dynamic adjustments — condition-based reallocation (first_turn, no_documents, long_conversation) |
| Pass 3 | Allocate and assemble — per-module budget = available × target_pct / 100, clamped to [min_pct, max_pct] |
| Pass 3b | Reallocate surplus — donors (utilization < 30%) → recipients (utilization > 80%) |
| Pass 4 | Condense if over budget — process modules in `condensation_order` (lowest priority first) |

See [Context Window Architecture](CONTEXT_WINDOW_ARCHITECTURE.md) for detailed analysis.

---

## 9. Cross-Cutting Concerns

### 9.1 The Escalation Chain

```
Tool fails
    |
    v
[S4] Is error definitive? ── YES ──> HALT (DefinitiveToolError)
    |
   NO
    |
    v
[S4] CorrectionHandler.attempt_correction()
    |
    v
Correction succeeded? ── YES ──> Retry tool (up to 3x)
    |
   NO (or 3 retries exhausted)
    |
    v
[Tactical Executor] Phase retry (up to MAX_PHASE_RETRY_ATTEMPTS = 5)
    |
    v
Phase retry exhausted?
    |
   YES
    |
    v
[S5] Error result recorded for the phase (0 LLM)
    |
    v
Dependent phases skipped; the turn continues to the final report,
which states what could not be gathered
```

### 9.2 Context Distillation as Universal Service

Every subsystem that touches the LLM depends on S6:

| Subsystem | How It Uses Context Distillation |
|-----------|--------------------------------|
| S1 Plan Validation | Rewrite passes may trigger LLM calls routed through context-managed prompts |
| S2 Re-planning | Replan LLM call via `_call_llm_and_update_tokens` respects ContextWindowHandler budgets |
| S3 Hallucination Prevention | Orchestrator LLM fallbacks (date resolution, semantic classification) use managed context |
| S4 Error Correction | `_call_correction_llm` routes through `_call_llm_and_update_tokens` |

### 9.3 Plan Validation Prevents Downstream Errors

S1 eliminates entire classes of errors that would otherwise trigger S4:

| Validation Pass | Error Class Prevented |
|----------------|----------------------|
| Correction 2 (prompt as tool) | Tool execution would fail with "tool not found" |
| Correction 3 (tool as prompt) | Prompt execution would fail with "prompt not found" |
| Corrections 3b/3c (unknown server / unknown tool) | Tool execution would fail with "tool not found" |
| Correction 4 (extraneous args) | Tool would reject unrecognized arguments |
| Correction 5 (param name fix) | Tool would receive wrong parameter names |
| `placeholder_sql` / `database_before_sql` / `schema_before_sql` | SQL against a placeholder or a guessed name would fail and pay a self-correction |
| Charting cleanup | Tactical planner would waste a refinement cycle hallucinating column mappings |
| Temporal data flow | Date Range Orchestrator would receive unresolved placeholder |

### 9.4 Profile-Awareness Thread

The `ProfilePromptResolver` (from `profile_prompt_resolver.py`) threads through multiple subsystems, allowing different profiles to customize behavior:

| Subsystem | Prompt Method |
|-----------|--------------|
| S4 | `get_tactical_self_correction_prompt()` |
| S4 | `get_tactical_self_correction_table_error_prompt()` |
| S4 | `get_tactical_self_correction_column_error_prompt()` |
| S6 | Context window type determines distillation thresholds |

---

## 10. Observability & Event System

All subsystems emit SSE events via `executor._format_sse_with_depth()` and persist to `turn_action_history` via `executor._log_system_event()`.

### 10.1 SSE Event Types by Subsystem

| Subsystem | Event Type | Step Text | When |
|-----------|-----------|-----------|------|
| S1 | `workaround` | "Plan Optimization" | Structural correction applied |
| S1 | `plan_optimization` | "Plan Optimization" | Semantic rewrite pass applied; FASTPATH initiated |
| S1 | `plan_rewrite_diff` | "Plan Rewrite" | A refinement pass changed the plan (§14.8) |
| S1 | `workaround` | "System Correction" | A deferral pass (`placeholder_sql`, `database_before_sql`) or the conversational re-plan fired |
| S2 | `plan_optimization` | "Re-planning for Efficiency" | Efficiency replan triggered |
| S3 | `workaround` | "System Orchestration" / "System Correction" | Orchestrator guard activated |
| S4 | `system_message` | "Calling LLM for Self-Correction" | Correction LLM call |
| S4 | `workaround` | "System Self-Correction" | Corrected action applied |
| S5 | `error` | "Phase Failed After N Attempts" | Phase exhausted its attempts (the recovery events are gone with §7's replan) |
| S6 | `context_optimization` | "Context Optimization" | Data distilled |
| All | `token_update` | — | Every LLM call (tokens + cost) |

### 10.2 System Event Log Format

```python
{
    "action": {
        "tool_name": "TDA_SystemLog",
        "arguments": {"message": "<step>", "details": "<details>"},
        "metadata": {
            "execution_depth": N,
            "type": "<event_type>",
            "timestamp": "<ISO 8601>"
        }
    },
    "result": {"status": "info" | "error" | "cancelled"}
}
```

### 10.3 Token Tracking

Every LLM call emits a `token_update` SSE event:

| Field | Scope |
|-------|-------|
| `statement_input` / `statement_output` | This LLM call |
| `turn_input` / `turn_output` | Accumulated for this turn |
| `total_input` / `total_output` | Session cumulative |
| `call_id` | UUID for correlation |
| `cost_usd` | Computed cost for this call |

---

## 11. System Prompt Reference

| Subsystem | Prompt File | Purpose |
|-----------|-------------|---------|
| S1, S2 | `WORKFLOW_META_PLANNING_PROMPT.txt` | Strategic plan generation with all critical directives |
| Tactical | `WORKFLOW_TACTICAL_PROMPT.txt` | Single-phase execution with argument prioritization |
| S1 | `SQL_CONSOLIDATION_PROMPT.txt` | Consolidate inefficient sequential SQL queries |
| S4 | `TACTICAL_SELF_CORRECTION_PROMPT.txt` | Generic error correction with tool/prompt switching |
| S4 | `TACTICAL_SELF_CORRECTION_PROMPT_TABLE_ERROR.txt` | Table-not-found recovery with ground truth enforcement |
| S4 | `TACTICAL_SELF_CORRECTION_PROMPT_COLUMN_ERROR.txt` | Column-not-found recovery with ground truth enforcement |
| — | `ERROR_RECOVERY_PROMPT.txt` | Former autonomous replan (no longer called — §7) |

**Prompt location:** `default_prompts/` directory (configurable per-profile via prompt mapping)

---

## 12. Configuration Reference

| Config Key | Default | Subsystem | Description |
|-----------|---------|-----------|-------------|
| `MAX_PHASE_RETRY_ATTEMPTS` | 5 | S4, S5 | Phase attempts before the phase is recorded as failed (admin-settable) |
| `MAX_EXECUTION_DEPTH` | 5 | S2 | Maximum nested PlanExecutor depth |
| `GRANTED_PROMPTS_FOR_EFFICIENCY_REPLANNING` | `["base_teradataQuery"]` | S2 | Prompts exempt from efficiency re-planning |
| `ENABLE_SQL_CONSOLIDATION_REWRITE` | `True` | S1 | Enable SQL consolidation rewrite pass (enabled by default) |
| `LLM_CALL_TIMEOUT_SECONDS` | 120 | All | Longest provider silence tolerated on an LLM call (0 = disabled) |
| `LLM_STREAM_MAX_SECONDS` | 600 | All | Absolute bound on one streamed LLM call that never falls silent (0 = none) |
| `CONTEXT_DISTILLATION_MAX_ROWS` | 500 | S6 | Execution context distiller row limit |
| `CONTEXT_DISTILLATION_MAX_CHARS` | 10,000 | S6 | Execution context distiller char limit |
| `REPORT_DISTILLATION_MAX_ROWS` | 100 | S6 | L1 per-result-set row limit |
| `REPORT_DISTILLATION_MAX_CHARS` | 50,000 | S6 | L1 per-result-set char limit |
| `REPORT_DISTILLATION_TOTAL_BUDGET` | 200,000 | S6 | L2 trigger: total char budget |
| `REPORT_DISTILLATION_AGGRESSIVE_ROWS` | 25 | S6 | L2 aggressive row limit per result set |
| `DOCUMENT_CONTEXT_MAX_CHARS` | 50,000 | S6 | Total char limit for uploaded documents |
| `DOCUMENT_PER_FILE_MAX_CHARS` | 20,000 | S6 | Per-document char truncation limit |
| `CONDENSE_SYSTEMPROMPT_HISTORY` | `True` | S6 | Names-only tool context after first turn |
| `USE_CONTEXT_WINDOW_MANAGER` | `True` | S6 | Use budget-aware ContextWindowHandler |

Context Window Type overrides (in `tda_config.json`):
- `distillation` block — per-CWT overrides for distiller thresholds
- `modules` — module activation, priority, `target_pct`, `min/max`
- `dynamic_adjustments` — condition-based budget reallocation rules
- `condensation_order` — priority order for over-budget condensation

---

## 13. File Reference

| File | Subsystems | Key Classes / Functions |
|------|-----------|------------------------|
| `src/trusted_data_agent/agent/engines/optimize_engine.py` | All | `OptimizeEngine` — full Planner/Executor pipeline (extracted from executor.py) |
| `src/trusted_data_agent/agent/engines/` | — | Engine package: `IdeateEngine`, `FocusEngine`, `CoordinateEngine`, `ConversationEngine`, `EngineRegistry` |
| `src/trusted_data_agent/agent/planner.py` | S1, S3 | `Planner`, `generate_and_refine_plan()` (19 passes), `_validate_and_correct_plan()`, 16 `_rewrite_*` methods, `_hydrate_plan_from_previous_turn()`, `exact_signed_champion`, `planner_binding_scope`, `planner_replayable`, `parse_plan_response` |
| `src/trusted_data_agent/agent/executor.py` | S4, S6 | `PlanExecutor`, `_build_phase_groups()`, `_stream_parallel_phases()`, `_get_correction_catalog()`, `_merge_sub_totals()`, `_distill_data_for_llm_context()`, engine dispatch |
| `src/trusted_data_agent/agent/conversational_guard.py` | S1 | `goal_is_small_talk()`, `REPLAN_NOTE` |
| `src/trusted_data_agent/agent/schema_facts.py` | S1, S4 | Schema facts for `schema_before_sql`, the deferral passes and the correction strategies |
| `src/trusted_data_agent/agent/phase_executor.py` | S4, S5 | `PhaseExecutor`, `CorrectionHandler`, 11 `CorrectionStrategy` subclasses, FASTPATH, phase exhaustion |
| `src/trusted_data_agent/agent/tool_enforcement.py` | S4 | `tool_error_text()`, the ReAct wrapper's dialect retries |
| `src/trusted_data_agent/agent/orchestrators.py` | S3 | `execute_date_range_orchestrator()` (parallelised), `execute_column_iteration()`, `execute_hallucinated_loop()`, `_resolve_temporal_phrase_deterministic()` |
| `components/builtin/context_window/distiller.py` | S6 | `ExecutionContextDistiller` |
| `components/builtin/context_window/handler.py` | S6 | `ContextWindowHandler`, 5-pass assembly algorithm |
| `components/builtin/context_window/context_builder.py` | S6 | `ContextBuilder` — format bridge between orchestrator and LLM call sites |
| `src/trusted_data_agent/mcp_adapter/adapter.py` | S6 | `_distill_workflow_for_report()`, `_compute_column_statistics()`, `_select_representative_sample()` |
| `src/trusted_data_agent/agent/profile_prompt_resolver.py` | S4 | `ProfilePromptResolver` |
| `src/trusted_data_agent/core/config.py` | All | `AppConfig` with all threshold constants |
| `src/trusted_data_agent/core/session_manager.py` | S3 | Turn validity filtering (`isValid` flag) |
| `src/trusted_data_agent/auth/consumption_enforcer.py` | Security | Consumption enforcement (fail-closed with user-visible warning event) |
| `rag_templates/schemas/planner-schema.json` | S1 | JSON schema defining plan structure |

---

## 14. Recent Improvements (engine-modularization branch)

### 14.1 Engine Modularization

`PlanExecutor.run()` was a 6,800-line monolith containing all four IFOC profile execution paths inline. Each path has been extracted into a dedicated engine class in `src/trusted_data_agent/agent/engines/`. The executor is a shared infrastructure hub that dispatches to engines (~3,160 lines right after the extraction; ~5,600 lines today as shared seams were added). See [Engine Modularization Architecture](ENGINE_MODULARIZATION_ARCHITECTURE.md) for the full design.

### 14.2 Independent Phase Parallelization

`PlanExecutor._stream_parallel_phases()` (in `executor.py`, driven by the phase groups `_build_phase_groups()` computes) uses `asyncio.gather()` to execute independent plan phases concurrently. Phases are eligible for parallelization when the planner marks them without data dependencies on each other. For plans with N parallel data-gathering phases, wall-clock time is reduced from `N × phase_latency` to `1 × phase_latency`.

Events are collected per phase (not interleaved) using a collect-then-yield pattern, so the Live Status panel receives clean per-phase event sequences that render correctly with `renderHistoricalTrace()`. A stable post-sort on `turn_action_history` by `phase_num` ensures historical replay is deterministic even when asyncio scheduling interleaves `_log_system_event()` calls.

**Dependency detection** for grouping phases into parallel waves (`_build_phase_groups`) is the union of three signals — over-detecting a dependency is safe (it only forces serial execution); a missed dependency races a phase ahead of its data:

| Signal | Method | Catches |
|--------|--------|---------|
| Explicit reference | `_phase_referenced_deps` | `result_of_phase_N` strings in args/goal/loop_over |
| Visualization → data | `_phase_implicit_deps` | `TDA_Charting`/`TDA_Dashboard(visualize)` consuming prior phase data even when the placeholder was stripped |
| **Discovery → execution** | `_phase_discovery_deps` (Jul 2026) | a raw-SQL phase (`base_readQuery` / `sql` arg) that follows a schema-**discovery** phase (`DISCOVERY_TOOLS` — `TDA_KnowledgeGraph query`, `base_tableList`, `base_databaseList`, `base_columnDescription`, `base_tableDDL`, `base_tablePreview`, `base_tableAffinity`) |

The discovery→execution signal closes a real defect (session `5fd9be3d` turn 6): the planner emitted phase 1 = "query the KG to identify the table" and phase 2 = `SELECT * FROM airport.station` with a hardcoded name and no `result_of_phase_N` reference, so the first two signals saw no dependency and the scheduler dispatched them concurrently — the query could never consume the discovery. `DISCOVERY_TOOLS` is defined in `plan_compiler.py` (single source of truth alongside `TERMINAL_TOOLS`/`UTILITY_TOOLS`/`VISUALIZATION_TOOLS`).

### 14.3 Date Range Orchestrator Parallelization

`execute_date_range_orchestrator()` in `orchestrators.py` now executes all per-day tool calls concurrently via `asyncio.gather()`. For a 30-day range, latency drops from `30 × tool_latency` to `1 × tool_latency` (plus result consolidation). Results are ordered by date after gathering.

### 14.4 SQL Consolidation Enabled by Default

`ENABLE_SQL_CONSOLIDATION_REWRITE` changed from `False` to `True`. The SQL consolidation rewrite pass (pass 3, `sql_consolidation`, in the refinement pipeline) now fires automatically when the plan contains two or more consecutive phases using SQL tools, merging them into a single optimised query (CTE / JOIN). Individual profiles can opt out via their configuration.

### 14.5 LLM Call Timeout

Every LLM call made through `executor._call_llm_and_update_tokens` is bounded by `LLM_CALL_TIMEOUT_SECONDS` (default 120; 0 disables). The limit is on the provider's **silence**, not on the length of the call (`llm/streaming.py:await_with_activity_timeout`):

- A call that streams nothing is stopped 120 seconds after it started — a hung provider raises instead of blocking.
- A streamed answer is stopped 120 seconds after the provider's **last chunk**. An answer that is still arriving is never cut for being long. Every provider stream helper marks each chunk it receives (`note_activity`), including chunks that carry no visible text (reasoning, tool input, usage).
- A stream that never falls silent is stopped at `LLM_STREAM_MAX_SECONDS` (default 600; 0 = no bound).

The three cases produce three different messages (no response · stopped sending · still writing at the bound). Before Oct 2026 the limit was a whole-call `asyncio.wait_for`, which cut a streamed answer at 120 seconds while its tokens were arriving. Gate: `test/test_stream_activity_timeout.py`.

**Inside the resilience envelope (S4), per attempt, every lane.** While `RESILIENCE_ENVELOPE` is on, the same limit applies to each ATTEMPT inside the envelope's own loop — the handler's attempt body (`llm/handler.py` → `_attempt_limit`, so every direct `call_llm_api` caller too: report synthesis, session naming, judges, the recommender, components) and every provider call of the LangChain wrapper (`llm/resilient_chat.py`: attempts, continuations and the signed fallback; streams through `iterate_with_activity_timeout`). A cut is a classified error (`resilience.classify`): `stalled` / `no_response` → a retryable `timeout` the envelope retries (at most `TIMEOUT_MAX_ATTEMPTS`) or hands to the signed fallback at once, recorded as an `llm_recovery` step; `ceiling` → terminal (`stream_ceiling` — a retry would write the same long answer); a LangChain stream that already yielded is never re-run. The executor's whole-call limit then becomes a backstop sized past the envelope's worst case (`silence × (TIMEOUT_MAX_ATTEMPTS + 1) + MAX_BACKOFF_SECONDS`), so it never cuts a call the envelope is still handling. Envelope off ⇒ unchanged: the executor's limit is the only one, and the LangChain lanes have none. One implementation: `streaming.activity_limit` (a context manager); the awaitable and iterator forms are built on it. Gate: `test/test_envelope_silence.py`.

### 14.6 Cycle Detection for Recursive Prompt Chains

Sub-executors are spawned up to `MAX_EXECUTION_DEPTH` levels deep. A `visited_prompts` set is now threaded through the execution tree: if prompt A calls prompt B which calls prompt A, the cycle is detected at the second invocation and the prompt is skipped with a warning rather than recursing until depth limit. This eliminates runaway token consumption from circular prompt graphs.

### 14.7 RAG Over-Retrieval Fix

`rag_retriever.py` previously fetched `k × 10` candidates from ChromaDB and discarded 90% after client-side filtering. The multiplier is now `k × 3`, reducing ChromaDB query + deserialization cost by ~67% while still providing sufficient candidates for filtering. No change to final result quality.

### 14.8 Plan Rewrite Diff Events

After each of the 19 refinement passes that CHANGED the plan, the `_pass` harness in `planner.py` emits a `plan_rewrite_diff` event (logged to `turn_action_history` and streamed) whose `details` carry:
- `pass` — the pass name (§3.2)
- `phases_before` / `phases_after` — the phase counts around the pass
- `delta` — the change in phase count

A pass that leaves the plan byte-identical emits nothing; a pass that raises is logged and skipped. After all passes the provenance chain records the final plan (`plan_rewrite` step).

This makes the historical trace fully auditable: the Live Status panel can show exactly which pass changed what, rather than only the final plan vs. the raw LLM output.

### 14.9 Consumption Enforcement Hardening

The consumption quota check in `executor.py` was previously fail-open: any exception silently bypassed all rate limits. The check is now fail-closed — enforcement failures surface a visible `consumption_warning` SSE event to the user and increment a bypass counter in the audit log. Genuine quota violations still block execution; the audit trail records all bypass incidents.

### 14.10 Genie Coordinator State Fixes

Five targeted fixes to `genie_coordinator.py` address module-level shared state issues that could cause session context leakage under concurrent parent sessions:

1. **Lock creation atomicity:** `dict.setdefault()` replaces TOCTOU check-then-set in `_get_slave_session_lock()`, preventing duplicate lock objects under concurrent dispatch.
2. **Memory leak:** `_cleanup_session_state()` is called in a `finally` block after each coordinator turn, removing `_event_callbacks` and `_provenance_chains` entries that previously grew indefinitely.
3. **`clear_session_cache()` scope:** Was modifying local variables instead of the module-level dicts. Now correctly removes only entries for the current parent session.
4. **`get_used_slave_sessions()` scope:** Previously returned the entire global cache (all coordinators, all users). Now filters to the current parent session only.
5. **Sequential primer mode:** Was silently executing only the first statement in the `statements` list. Now iterates all statements in order, with per-statement error handling.

### 14.11 Live Status Event Sequencing (Parallel Phases)

Three fixes ensure the Live Status panel renders parallel phase executions correctly in both live and historical (plan reload) modes:

1. **FASTPATH events positioned in phase** (`phase_executor.py`): FASTPATH `plan_optimization` events now include `phase_num` in their `details` dict, enabling the post-sort in `_stream_parallel_phases` to place them inside their correct phase container rather than after the last sequential phase.

2. **Phase Completed footer in historical replay** (`phase_executor.py`): The tactical LLM slow path's `phase_end` emission was the only one of 6 sites that called `_format_sse_with_depth()` without also calling `_log_system_event()`. The missing history write caused `renderHistoricalTrace()` to never see the `phase_end` event and therefore never render the "Phase N/M Completed" footer. Both calls are now made consistently.

3. **Defensive footer in `renderHistoricalTrace()`** (`ui.js`): Phase containers store `phase_num`, `total_phases`, and `depth` as `dataset` attributes at creation time. The two cleanup `while` loops (triggered when a same-depth phase starts, and at end-of-trace) now call `_addDefensivePhaseFooter()` to render a Completed footer if no explicit `phase_end` event was processed — ensuring correct display even if backend tracing regresses.

---

### 14.12 Several MCP servers per agent (PROP-006 phase 2, Sep 2026)

An Optimize agent may read additional MCP servers. What the pipeline sees:

- **Tool names.** The primary server's tools keep their names; an additional server's are
  `<alias>__<name>`. The planner, the tactical step, FASTPATH and the correction strategies see the
  qualified name; every rule that recognises a tool by name reads `tool_identity.base_name`
  (gate-enforced), so the discovery/execution ordering, the SQL checks and the correction chain
  treat `crm__base_readQuery` as the SQL tool.
- **Routing.** `adapter.resolve_mcp_target` maps a name to (client, server, server-side name) for
  every tool call and for all four MCP prompt fetches (O3); the turn's alias → server map is armed
  once at the execution seam.
- **Planners (M6).** A planner repository keyed to one of the turn's additional servers loads for
  the agent (`rag_retriever._planner_keyed_to`); its champion cases name that server's tools.
  A case that names an alias the agent no longer has (O8) is re-keyed by the plan validator
  (Correction 3b, `_rekey_unknown_server_tool`) to the one catalog tool of that base name, and
  routing (B10) then sends the call to the server that holds its data.
- **Transparency.** Each tool result carries `mcp_source` (the server the call actually went to);
  Live Status shows a Server row on every tool step, live and on reload.

A single-server agent is byte-identical (`test_multi_mcp_baseline`). Architecture:
[MULTI_SOURCE_ARCHITECTURE.md §9](MULTI_SOURCE_ARCHITECTURE.md).

## Related Documents

- [Context Window Architecture](CONTEXT_WINDOW_ARCHITECTURE.md) — Detailed analysis of context generation, contribution, and optimization
- [Knowledge Retrieval Architecture](KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md) — RAG pipeline, scoring, and retrieval
- [Engine Modularization Architecture](ENGINE_MODULARIZATION_ARCHITECTURE.md) — How execution paths are modularised into engine classes
- [Component Architecture](COMPONENT_ARCHITECTURE.md) — Extension system and component lifecycle
