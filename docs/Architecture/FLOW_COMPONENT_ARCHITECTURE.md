# Flow Component Architecture

> **Visual DAG orchestration** — lets the LLM author, edit, and execute directed acyclic graphs (and bounded cycles) through natural conversation, with a live React Flow canvas that stays in sync bidirectionally via a patch protocol and per-node SSE animation.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [File Structure](#2-file-structure)
3. [Architecture Diagram](#3-architecture-diagram)
4. [Component Layer (TDA_Flow)](#4-component-layer-tda_flow)
   - [Tool Definition](#tool-definition)
   - [Action Reference](#action-reference)
   - [Handler Routing](#handler-routing)
   - [Payload Types](#payload-types)
5. [Graph Model](#5-graph-model)
   - [Node Types](#node-types)
   - [Edge Types](#edge-types)
   - [Scopes (Bounded Cycles)](#scopes-bounded-cycles)
   - [Parameters & Data Flow (Flowise-style)](#parameters--data-flow-flowise-style)
   - [Topology Templates (PROP-007)](#topology-templates-prop-007)
   - [File Input / File Output Nodes (PROP-010)](#file-input--file-output-nodes-prop-010)
6. [Execution Engine (flow_executor.py)](#6-execution-engine-flow_executorpy)
   - [FlowDefinition & Validation](#flowdefinition--validation)
   - [Execution Order](#execution-order)
   - [Node Executors](#node-executors)
   - [Partial Execution (run-from-node)](#partial-execution-run-from-node)
   - [Scope Execution](#scope-execution)
   - [Session Context Model](#session-context-model)
   - [Token & Cost Accumulation](#token--cost-accumulation)
   - [Cancellation](#cancellation)
7. [Frontend Renderer](#7-frontend-renderer)
   - [React Flow Integration](#react-flow-integration)
   - [Exported Functions](#exported-functions)
   - [Render Views](#render-views)
   - [Live Canvas Animation (SSE)](#live-canvas-animation-sse)
   - [Bidirectional Sync Protocol](#bidirectional-sync-protocol)
   - [Following changes made elsewhere](#following-changes-made-elsewhere)
   - [Node Status Badges](#node-status-badges)
   - [Inline Card](#inline-card)
   - [Layout Engine (Sugiyama-Barycenter)](#layout-engine-sugiyama-barycenter)
   - [Canvas Intelligence Features](#canvas-intelligence-features)
   - [Editing & UX Affordances](#editing--ux-affordances)
8. [Database Schema](#8-database-schema)
9. [REST API Endpoints](#9-rest-api-endpoints)
10. [SSE Stream Endpoint](#10-sse-stream-endpoint)
11. [Version Snapshots](#11-version-snapshots)
12. [Profile Integration](#12-profile-integration)
13. [LLM Instructions](#13-llm-instructions)
14. [Design Decisions](#14-design-decisions)
15. [File Reference](#15-file-reference)
16. [Execution & Profile Hardening (Jun 2026)](#16-execution--profile-hardening-jun-2026)

---

## 1. System Overview

### What the Flow Component Does

The Flow component (`TDA_Flow`) gives the LLM the ability to build and run **visual execution graphs**. Each node in a flow is an independently-executed unit — an LLM prompt, a Python expression, or a conditional branch. The LLM creates and edits flows through structured tool calls; users can also edit the graph directly in the React Flow canvas with all mutations syncing back to the conversation.

Flows execute asynchronously: the execution endpoint returns a `run_id` immediately, and the live canvas subscribes to a per-run SSE stream that animates each node as it transitions through `idle → queued → running → success / error / skipped`.

### Design Principles

> LLM nodes route through the identical `execute_query()` pipeline as user-submitted queries — the same LLM, the same profile, the same cost tracking.

> Canvas edits and conversation edits converge on the same graph definition. Neither source is authoritative; the patch protocol reconciles both.

### Architecture Position

```
                ┌───────────────────┐
                │   User / LLM      │
                └────────┬──────────┘
                         │ TDA_Flow tool call
                         ▼
               ┌──────────────────────┐
               │  FlowComponentHandler│  ← handler.py
               └────────┬─────────────┘
                         │ ComponentRenderPayload (spec)
                         ▼
               ┌──────────────────────┐
               │   generate_html()    │  ← data-component-id div
               │   addMessage()       │  ← ui.js picks up div
               │   renderFlow()       │  ← renderer.js
               └────────┬─────────────┘
                         │ visual card + optional split panel
                         ▼
               ┌──────────────────────┐
               │  React Flow canvas   │  ← @xyflow/react v12 via CDN
               │  (flow-split-panel)  │
               └────────┬─────────────┘
          ┌─────────────┴──────────────┐
          │ canvas edits               │ execute button
          ▼                            ▼
  PATCH /v1/flows/<id>/patch    POST /v1/flows/<id>/execute
  → apply_patch()               → FlowExecutor.run()
  → snapshot_flow()             → per-node REST API calls
  → uderia:flow-patch (page bus) → SSE stream: /runs/<id>/stream
```

---

## 2. File Structure

```
components/builtin/flow/
├── manifest.json          # Component metadata, TDA_Flow tool definition, render targets
├── handler.py             # FlowComponentHandler — routes actions to flow_db / flow_executor
├── renderer.js            # React Flow canvas renderer (~9 300 lines)
└── instructions.json      # Intensity-keyed LLM guidance (medium / heavy) — unchanged by params/UX work

src/trusted_data_agent/core/
├── flow_db.py             # SQLite CRUD: flows, flow_runs, flow_node_runs, flow_variables + patch ops + reconcile_orphaned_runs()
├── flow_layout.py         # Sugiyama-Barycenter hierarchical layout engine
├── flow_executor.py       # FlowDefinition, FlowExecutor, node executors, $-refs, partial + parallel-level execution, token budget
├── flow_refs.py           # Shared reference-rewriting core (rewrite_refs) — used by runtime resolver AND authoring canonicaliser
├── flow_validation.py     # Authoring-time canonicalisation + validation of decision/function references (validate_and_repair)
├── flow_loops.py          # The one rule set for making / editing / removing a loop (scope); LoopError
├── flow_rounds.py         # Pure helpers for scope rounds (exit rule, convergence, round records)
├── flow_topologies/       # PROP-007 topology templates: registry.py (catalog), slots.py, prefill.py, one generator per shape
└── safe_expression.py     # AST-allowlist evaluator (safe_eval) for task_function / decision nodes

src/trusted_data_agent/api/
└── flow_routes.py         # REST endpoints (flows, runs, node-data, flow-variables) + SSE stream

schema/
└── 39_flow_system.sql     # DDL: flows, flow_runs, flow_node_runs tables
                           # (flow_variables created via self-applying migration in flow_db.py)

test/
├── test_flow_parameters.py # 37 assertions: $-refs, named outputs, global vars, partial execution
├── test_safe_expression.py  # 47 assertions: AST allowlist — functional + every sandbox-escape vector
├── test_flow_parallel.py    # 15 assertions: dependency-level layering + real concurrency proof
├── test_flow_budget.py      # 12 assertions: per-run token budget halt / settings fallback
├── test_flow_retry.py       # 14 assertions: task_llm transient-failure retry / backoff / exhaustion
├── test_flow_reconcile.py   # 9 assertions: orphaned-run reconciliation (isolated temp DB)
├── test_flow_instructions_consistency.py # 30 assertions: every advertised action dispatches (anti-phantom)
├── test_flow_session_binding.py # 15 assertions: per-node session resolution matrix + handler wiring
├── test_flow_topologies.py / test_flow_topology_baseline.py / test_flow_topology_picker.mjs # PROP-007 templates
├── test_flow_dialogic.py / test_eval_flow.py # judge + artefact nodes, loops that stop on a verdict
├── test_flow_actions.py / test_flow_actions_ui.mjs # task_action nodes (OBDA-ACT)
└── test_flow_layout.py / test_flow_decision_pruning.py / test_flow_extensions.py / test_flow_rest_contract.py

static/js/handlers/
└── flowsHandler.js        # "Flows" tab inside the Components view (~1 000 lines)

static/js/
└── componentRenderers.js  # Imports renderFlow, registers it under 'flow'
```

---

## 3. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONVERSATION LAYER                                                   │
│                                                                       │
│  User: "Create a two-step flow: fetch data then summarise it"        │
│       ↓                                                               │
│  LLM calls TDA_Flow(action='create', name='Fetch & Summarise',       │
│    definition={nodes:[...], edges:[...]})                             │
│                                                                       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  COMPONENT LAYER  (components/builtin/flow/)                          │
│                                                                       │
│  handler.py                                                           │
│    process(arguments, context)                                        │
│      → create_flow() / apply_patch() / execute_flow() etc.           │
│      → _flow_canvas_payload() → ComponentRenderPayload(INLINE)       │
│      → silent payloads for LLM-internal ops (silent_patch etc.)      │
│                                                                       │
│  renderer.js                                                          │
│    renderFlow(containerId, spec)                                      │
│      → spec.view === 'canvas'       → _renderFlowInlineCard()        │
│      → spec.view === 'silent_patch' → UderiaBus 'uderia:flow-patch'  │
│      → spec.view === 'list'         → _renderFlowList()              │
│      → spec.view === 'run_status'   → _renderRunStatus()             │
│      → spec.view === 'silent_*'     → collapse container (no UI)     │
│                                                                       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  PERSISTENCE LAYER  (flow_db.py + SQLite)                             │
│                                                                       │
│  flows                          flow_runs                             │
│  ├── flow_id (flow-{hex12})     ├── run_id (frun-{hex12})            │
│  ├── user_id                    ├── flow_id → flows                  │
│  ├── name / description         ├── user_id                          │
│  ├── definition (JSON)          ├── status (queued/running/…)        │
│  ├── is_template / is_reusable  ├── input_params (JSON)              │
│  ├── version (int, auto-inc)    ├── output_result (JSON)             │
│  └── created_at / updated_at    ├── execution_trace (JSON)           │
│                                 ├── tokens_input / tokens_output     │
│  flow_node_runs                 └── cost_usd                         │
│  ├── id, run_id, node_id                                             │
│  ├── node_type, scope_id, iteration                                  │
│  ├── status / error_msg                                              │
│  ├── input_state / output_state (JSON)                               │
│  ├── task_id / session_id (for task_llm nodes)                       │
│  └── tokens_input/output / started_at / completed_at                │
│                                                                       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  EXECUTION LAYER  (flow_executor.py)                                  │
│                                                                       │
│  FlowExecutor.run(input_params)                                       │
│    1. _build_execution_order()  ← topological sort (NetworkX)        │
│    2. For each unit:                                                  │
│       ("node", id)   → _run_node(id, scope_id=None, iteration=0)    │
│       ("scope", id)  → _run_scope(id)  ← bounded iteration          │
│    3. _execute_node() dispatches to:                                  │
│       task_llm      → REST API poll (POST /sessions + GET /tasks)   │
│       task_function → restricted Python eval                         │
│       decision      → bool(eval(condition))                          │
│       start / merge → passthrough                                     │
│       end           → passthrough + final=True                       │
│    4. update_run_status() + emit SSE via _broadcast_flow_event()     │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Layer (TDA_Flow)

### Tool Definition

`TDA_Flow` is declared in `manifest.json`. The tool accepts:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✓ | See Action Reference below |
| `flow_id` | string | all but create / list | ID of the target flow |
| `name` | string | create | Human-readable name |
| `description` | string | — | Optional description |
| `global_params` | object | create / update | Flow-level variables |
| `node` | object | add_node / insert_node | Node definition `{id?, type, label, data, position?}` — types `task_llm` · `task_tool` · `task_prompt` · `task_function` · `decision` · `merge` · `start` · `end`, plus `file_input` · `file_output` when Files is available (`[[gate:files]]`) |
| `node_id` / `node_updates` | string / object | remove_node / update_node | Target node and the fields to change |
| `after_node_id` | string | insert_node | Splice the new node into this node's outgoing edge |
| `edge` / `edge_id` | object / string | add_edge / remove_edge | Edge definition (`type`: `normal` · `error` · `conditional` · `cycle`) or its id |
| `scope` / `scope_id` / `scope_updates` | object / string / object | create_scope / update_scope / delete_scope | Loop definition and edits (see *Scopes*) |
| `topology` / `params` / `fills` | string / object / object | create | Start from a topology template (see *Topology Templates*) |
| `input_params` | object | execute | Key-value overrides merged into `global_params` |
| `run_id` | string | get_run / cancel_run | Target run ID |

The session of an `execute` is the conversation it is called from; node session sharing follows the flow's `settings.parallelExecution` (§16.2), not a tool argument.

### Action Reference

| Action | DB Operation | Payload Type | LLM Sees |
|---|---|---|---|
| `create` | INSERT flow (optionally from a `topology`) | `canvas` view | Inline card + optional split panel |
| `open` | SELECT flow | `canvas` view | Inline card; opens the split panel |
| `get` | SELECT flow | `silent_get` | Nothing (silent) |
| `list` | SELECT flows | `list` view | Compact flow list card |
| `update` | UPDATE flow | `canvas` view | Updated inline card |
| `delete` | DELETE flow | silent | Nothing |
| `insert_node` | `apply_patch(add_node)` | `silent_patch` | Nothing; canvas animates |
| `add_node` | `apply_patch(add_node)` | `silent_patch` | Nothing |
| `remove_node` | `apply_patch(remove_node)` | `silent_patch` | Nothing |
| `update_node` | `apply_patch(update_node)` | `silent_patch` | Nothing |
| `add_edge` | `apply_patch(add_edge)` | `silent_patch` | Nothing |
| `remove_edge` | `apply_patch(remove_edge)` | `silent_patch` | Nothing |
| `create_scope` | `apply_patch(create_scope)` | `silent_patch` | Nothing |
| `update_scope` | `apply_patch(update_scope)` | `silent_patch` | Nothing |
| `delete_scope` | `apply_patch(delete_scope)` | `silent_patch` | Nothing |
| `update_global_params` | `apply_patch(update_global_params)` | `silent_patch` | Nothing |
| `execute` | INSERT run + start async | `run_status` view | Run status card |
| `get_run` | SELECT run | `run_status` view | Run status card |
| `cancel_run` | UPDATE status=cancelled | silent | Nothing |

**`insert_node` smart resolution** — accepts human-readable labels instead of UUIDs:
- `"start"` or `"end"` → resolves to the flow's start / end node
- Any node `type` keyword (e.g. `"task_llm"`, `"decision"`) → resolves to the first node of that type
- A full node UUID → used directly

### Handler Routing

`FlowComponentHandler.process()` dispatches to private `_handle_*` methods:

```
process(arguments, context)
  action == "create"              → _handle_create()
  action in patch_actions         → _handle_patch()   → apply_patch()
  action == "insert_node"         → _handle_insert_node() → _handle_patch()
  action == "execute"             → _handle_execute()
  action == "get_run"             → _handle_get_run()
  action == "cancel_run"          → _handle_cancel_run()
  ...
```

After every structural mutation (`apply_patch`), the handler calls `_schedule_snapshot()` — a 2-second debounced function that coalesces burst mutations (e.g. "add three nodes") into a single version snapshot.

### Payload Types

| `spec.view` | `render_target` | Frontend effect |
|---|---|---|
| `"canvas"` | `INLINE` | Renders inline flow card with "Open in canvas" button |
| `"list"` | `INLINE` | Renders compact list of the user's flows |
| `"run_status"` | `INLINE` | Renders run progress card with node status grid |
| `"silent_patch"` | `INLINE` | Emits `uderia:flow-patch` on the page bus; collapses container |
| `"silent_get"` | `INLINE` | Collapses container — no visible output |
| `"silent_list"` | `INLINE` | Collapses container — no visible output |
| `"silent_error"` | `INLINE` | Collapses container — error returned as LLM tool result text |

All flow payloads use `render_target = INLINE`. The inline HTML placeholder (`<div data-component-id="flow" data-spec='{...}'>`) is picked up by `addMessage()` in `ui.js`, which calls `renderComponent('flow', containerId, spec)` → `renderFlow()`.

---

## 5. Graph Model

### Node Types

| Type | Purpose | Executor |
|---|---|---|
| `start` | Entry point (one per flow) | Passthrough — initialises state |
| `end` | Terminal node (one or more) | Passthrough — marks completion |
| `task_llm` | LLM prompt execution | `_execute_llm_node()` — REST API poll |
| `task_tool` | **Deterministic tool call (no LLM)** | `_execute_tool_node()` — `mcp_adapter.invoke_mcp_tool()` |
| `task_action` | **Signed action by an agent (no LLM, OBDA-ACT only)** | `_execute_action_node()` — `action_perform.perform_unattended()` (PROP-008 §6) |
| `task_prompt` | **MCP prompt call (LLM-driven; Optimize profiles only)** | `_execute_llm_node()` — REST `/query` with `prompt_name` |
| `task_function` | Python expression evaluation | `_execute_function_node()` — AST-allowlisted `safe_eval()` |
| `decision` | Conditional routing | `_execute_decision_node()` — AST-allowlisted bool eval; `true`/`false` edges; emits `node_warning` on bad condition |
| `task_extension` | Deterministic post-processing of upstream output | `_execute_extension_node()` — runs a registered extension (boolean-check, classify, extract, json) so a downstream decision can route on structured data |
| `merge` | Joins parallel paths | Passthrough — merges predecessor outputs |
| `judge` | **A verdict on the previous step** (PROP-007) — pass/fail, score, feedback | an agent through the ordinary LLM-node path (reply ends in `VERDICT:` / `SCORE:` lines), or the eval judge panel (`profile_id: "judge_panel"`, consensus via `build_panel`, cost billed to the run). One verdict shape either way; an abstention is `passed=None` and never ends a loop |
| `artefact` | **Writes the previous step's code into ONE canvas per run** (PROP-007) | a new canvas version each round; prose with no code keeps the last real version and the next round is told no code arrived |
| `file_input` | **Reads a file kept in Files (no LLM, PROP-010)** | `_execute_file_input_node()` — see [File Input / File Output Nodes](#file-input--file-output-nodes-prop-010) |
| `file_output` | **Keeps its input as a file in Files (no LLM, PROP-010)** | `_execute_file_output_node()` — passes its input through unchanged |

**Node data fields** (stored in `node.data`):

- `task_llm`: `prompt` (may contain `{variable}` placeholders), `profile_id` (optional per-node override), `session_context` (`"pinned"` \| `"new"`), `session_id`, `retries` (optional int; transient-failure retry count, default `DEFAULT_LLM_NODE_RETRIES`)
- `task_tool`: `tool_name` (exact tool name — MCP data tool, `TDA_` builtin, or component tool), `arguments` (dict; string values support `$name` / `{name}` references resolved from the node's state context — a value that is exactly `$name` keeps the upstream value's type), `profile_id` (optional; selects the MCP server/connection), `output_as` (optional alias)
- `task_action`: `action` (an action type on the agent's active graph), `invocation` (`{key, values}`; `$name`/`{name}` resolved from upstream outputs), `profile_id` (the agent whose grant it runs under; else the flow's `defaultProfileId`), `connection_id` (optional; else the agent's action connection), `output_as`. Runs unattended — the action's **posture** decides (enforce writes with a signed receipt · review parks for the owner · observe records); the output carries `outcome`, `rows_affected`, `invocation_hash`, `review_id`, `signed_receipt`. A refusal is a node **error**. A flow whose action node's agent is not granted the action (or that exists while OBDA-MODEL · OBDA-ACT is off) is refused at save (`create_flow`/`update_flow` → REST `400 action_not_granted`) and again at the run's start. The canvas offers the node only while OBDA-MODEL · OBDA-ACT is on, and its picker lists only granted actions.
- `task_prompt`: `prompt_name` (exact MCP prompt name), `prompt_arguments` (dict; `$name`/`{name}` resolved), `profile_id` (the Optimize profile that runs it), `session_context`, `output_as`. **Differs from `task_tool`: an MCP prompt is LLM-driven** — it runs through the identical REST `/sessions/<id>/query` path as `task_llm` (with `prompt_name`/`prompt_arguments` in the body → `active_prompt_name` on the executor), so it costs tokens. The node is surfaced in the canvas (palette / command palette / edge-insert) **only when the flow's default profile is Optimize (`tool_enabled`)**; a node can still override to another OPTIM profile in its inspector.
- `task_function`: `expression` (Python expression; result stored as `node_id.output`)
- `decision`: `condition` (Python boolean expression; routes to `true`/`false`-labelled edges)
- `judge`: `profile_id` (`"judge_panel"` or an agent profile), `rubric`, optional `instructions`, `output_as`
- `artefact`: `source` (the upstream value holding the code, default `output`), optional `language`
- `file_input`: `file_id` (required), `filename` (display)
- `file_output`: `source` (default `output`), `format` (`auto` · `csv` · `md` · `json` · `txt`), `filename` (template), optional `profile_id`

**Adding nodes on the canvas** — every add surface (the left palette, ⌘K, the edge `+` menu, the number keys 1–7, the help overlay) and the new-node defaults read ONE declaration, `ADDABLE_NODES` in `renderer.js` (filtered by `_addableNodes(isOptim, actOn, filesOn)`); a new node type added there appears everywhere at once. Keys: 1 LLM · 2 Function · 3 Decision · 4 Merge · 5 Tool · 6 Judge · 7 Canvas. Prompt (Optimize default profile only), Action (OBDA-ACT on) and File input / File output (Files available to the user) have no key and appear only under their condition. A new Judge defaults to the eval judge panel; a new Canvas node reads `output`.

### Edge Types

| `edge.type` | Meaning |
|---|---|
| `"default"` (omitted) | Forward edge — always active |
| `"error"` | Activated only when the source node raises an exception; prevents run failure |
| `"cycle"` (referenced by `scope.back_edge_id`) | A loop's back edge, from the step that ends a round to the step that starts one; never activated by the executor (the scope runs its rounds), excluded from topological sort and layout, drawn as a dashed loop-back edge |

**Decision edge routing**: for `decision` nodes, edges carry a `condition` field (`"true"` or `"false"`). Only the matching edge(s) activate after the condition is evaluated.

### Scopes (Bounded Cycles)

A scope defines a bounded iteration block — a set of nodes that execute repeatedly until `max_iterations` is reached. Scopes are the only mechanism that introduces cycles into what is otherwise a DAG.

```json
{
  "id": "scope-abc123",
  "name": "Retry loop",
  "node_ids": ["node-1", "node-2"],
  "back_edge_id": "edge-loop",
  "max_iterations": 5
}
```

The executor builds a sub-graph from `node_ids` (forward edges only, back edge excluded) and runs a topological sort within the scope. Iteration stops when `max_iterations` is reached, the optional `exit_when = {node, condition}` holds over that node's output (PROP-007 — a judge's verdict), a node fails without an error path, or the flow is cancelled. `history_window` (optional) limits how many earlier rounds each member is shown; a node after the loop always reads the whole transcript.

**Making, editing and removing a loop — one rule set: `core/flow_loops.py`.** Every path that creates or changes a scope goes through it: the canvas (*Make loop* on a multi-selection, *Repeat as a loop* on a step, ⌘K; the frame's *Edit* opens the loop editor; a round stepper on the frame), the patch ops below, and the TDA_Flow tool. Rules: a loop's members must be connected and a round must start at exactly one member and end at exactly one member (the back edge, `type: "cycle"`, goes from the end to the start; one member may repeat on its own); Start/End never repeat; a step belongs to at most one loop; `max_iterations` is clamped to 1–20 and `history_window` to 1–20 or absent; a *pass* / *pass with score ≥ t* stop rule must name a Judge member (the server writes the condition), a custom condition may read any member's output and is evaluated against a sample verdict before it is stored; members and the back edge change only by removing and re-making the loop. A refusal is a `LoopError` (code + a sentence naming the fix): the patch route answers **400** `{error, code}`, the TDA_Flow tool returns the sentence to the model.

| Patch op | Payload | Effect |
|---|---|---|
| `create_scope` | `{node_ids, name?, max_rounds?, exit_when?, history_window?}` (canvas) **or** `{scope: {...}}` (tool — completed by `adopt_scope`: missing back edge added, limit clamped) | makes the loop and lays the flow out (the frame gets `position`/`size`); `exit_when` defaults to the last Judge member |
| `update_scope` | `{scope_id, updates: {name?, max_iterations?, history_window?, exit_when?}}` — `exit_when` is `{node, mode: "pass"\|"threshold"\|"expression", threshold?, expression?}`, `{node, condition}`, or `null` (fixed rounds) | edits in place; any other key is refused |
| `delete_scope` | `{scope_id}` | removes the loop and its back edge; the steps stay |

### Parameters & Data Flow (Flowise-style)

A flow exposes values to its nodes through three tiers, plus per-node named outputs. All are referenced inside node prompts/expressions/conditions with `{name}` (str.format) **or** `$name` (Flowise sigil). The sigil is stripped before the sandboxed `eval` (`$name` → bare `name`); in LLM prompts `$name` is value-substituted and `{name}` formatted. Literal `$5` (dollar+digit) is left intact.

| Tier | Storage | Scope | Persisted via | Reference |
|---|---|---|---|---|
| **Global variables** | `flow_variables` table (per-user) | All of a user's flows | `GET/PUT /v1/flow-variables` | `$name` / `{name}` |
| **Flow parameters** | `definition.global_params` | This flow | `update_global_params` patch op | `$name` / `{name}` |
| **Named node outputs** | runtime only (`output_as` on a node) | Downstream nodes | n/a (execution) | `$name` / `{name}` |
| **Default output** | runtime (`output` key) | Immediate successors | n/a | `output` |

**Context precedence (lowest → highest):** global variables → flow parameters → predecessor node outputs. A more-specific value shadows a more-general one on key clash.

**Named outputs (`output_as`):** a node may publish its primary result under a user-chosen key *in addition to* the generic `output`. This fixes fan-in collisions (two predecessors both writing `output`) and lets a downstream node reference a value by a stable name regardless of distance. Implemented by `_apply_output_alias(node, output)` before `record_output`.

**Execution-time overrides:** `input_params` (in the `execute` body) merge into `FlowState.global_params` at run start — this is how a flow becomes a reusable template.

**Global variables are not a secrets store** — they are plaintext constants; route secrets through credential configuration instead.

Reference resolution helpers in `flow_executor.py`: `_normalize_param_refs` (`$name`→`name` for eval), `_substitute_dollar_values` (`$name`→value for prompts), `_load_global_variables` (seeds the lowest context layer, fail-open).

### Referencing a value across nodes — tolerant resolution + authoring canonicalisation

A node referencing a previous node's value is the single most error-prone thing a
model authors, because a SQL/`task_tool` node's `output` is a **list of row dicts**
(`[{"customer_count": "100"}]`), not a scalar, and models invent many ways to reach
into it. Two layers, **one shared core**, make it robust:

**Column shortcuts (resolution).** `FlowState.resolve_for_node` exposes each
predecessor `output`'s first-row columns as top-level, **numeric-coerced** names
(`customer_count` → `100`). So `customer_count <= 100` works directly — no need to
know `output` is a list or hand-write `int(output[0]['customer_count'])` (which still
works). `setdefault` keeps explicit params/outputs authoritative.

**Shared reference core (`core/flow_refs.py`).** One implementation of
`{ref}` / `{Node.col}` / `$name` parsing (`rewrite_refs`, whole-then-last-segment).
Two consumers, no drift:
- **Runtime** (`flow_executor._substitute_brace_refs`) rewrites each reference to its
  **value** before `safe_eval`.
- **Authoring** (`core/flow_validation.py`) rewrites each reference to its canonical
  **name** before persist.

**Authoring canonicalisation + validation (`flow_validation.validate_and_repair`).**
Runs at the persistence boundary (`flow_db.create_flow` / `update_flow`, which
`apply_patch` routes through) so the **stored flow — and the canvas — never shows a
broken reference**. Every form a model invents collapses to the clean canonical name:

| Model writes | Stored |
|---|---|
| `customer_count <= 100` / `$customer_count <= 100` | `customer_count <= 100` |
| `{customer_count} <= 100` / `{Get Customer Count.customer_count} <= 100` | `customer_count <= 100` |
| `node-2d243c2d.customer_count <= 100` / `Get Customer Count.customer_count <= 100` | `customer_count <= 100` |

`_strip_node_qualifiers` removes a real node-id/label prefix from a bare dotted
reference (`<node>.col` → `col`) — those prefixes are never valid identifiers, so the
match is unambiguous. SQL result columns are derived statically from `AS <alias>`.
A reference to a value the upstream step **cannot** produce is **rejected** on the
LLM create path with a precise message (*"references 'total_revenue', which the
previous step does not produce. Available: ['customer_count']…"*) so the model
self-corrects in the same turn instead of shipping a silently-mis-routing decision.
Set/dict literals and unknown names are left intact (conservative).

### Topology Templates (PROP-007)

A flow can start from a **topology template** instead of an empty canvas — the picker in the canvas, `POST /v1/flows` with `topology`, and `TDA_Flow(action='create', topology=…)` all call ONE generator, `core/flow_topologies.generate(key, params, fills, resolver)`. The catalog is data (`flow_topologies/registry.py` → `TEMPLATES`): `pipeline` · `parallel` · `fork-join` · `conditional` · `iterative` (a worker refines until a judge passes) · `debate` (debaters argue in rounds until a moderator calls consensus, then a synthesiser) · `teacher-student` · `pair-programming` · `blank`; `swarm` is listed but is not a flow — its card hands off to creating a Coordinate agent.

- **Parameters** (`params`, e.g. `{stages: 3}`, `{branches: 3}`, `{debaters: 3, max_rounds: 3}`) are clamped to each template's declared range (`clamp_params`).
- **Slots** are the agent positions a template leaves open (by role — `stage`, `worker`, `judge`, `synthesiser` …). `fills` assigns agents by role (a list in slot order or one `@TAG` for every slot of that role) or by node id; an unknown `@TAG` is rejected (`slots.py`), an unfilled slot stays visibly open (`FlowSlotUnfilledError` names it when a run needs it).
- **Pre-fill** (`prefill.py`): opened from a Coordinate agent, the picker fills slots from its experts; recently used agents are offered as suggestion chips, never as fills.
- REST: `GET /v1/flows/templates` (catalog with a laid-out default skeleton per available shape; an unshipped shape is listed greyed with its reason) and `GET /v1/flows/templates/<key>?<param>=…&source_profile_id=…` (live preview; creates nothing).

Gates: `test_flow_topologies.py`, `test_flow_topology_baseline.py`, `test_flow_topology_picker.mjs`. Design: `docs/proposals/PROP-007-flow-topology-templates.md`.

### File Input / File Output Nodes (PROP-010)

Two deterministic nodes connect a flow to the user's **Files** ([FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md)). Both cost 0 tokens; both are offered on the canvas (and to the model, through the `[[gate:files]]` part of the `node` argument) only while Files is available to the user.

| Node | Reads | Emits |
|---|---|---|
| **File input** (`file_input`) | `data.file_id` — a file the user owns, read through the file service (the one read path, from the store it was written to); a deleted file is a node error naming it | CSV / TSV / XLSX → rows (`output` = list of row dicts, the same shape a tool node's query gives, so `$column` shortcuts work; `columns`, `row_count`, `truncated` beyond 10 000 rows) · JSON → its value · text / Markdown / PDF / DOCX → its text (first 200 000 chars). Any other type is refused. The read is recorded as a use of the file in the run's session |
| **File output** (`file_output`) | `data.source` (default `output`) from the node's context | Writes it as `format` (`auto`: rows → CSV, an object → JSON, text → Markdown) under `filename` — a template with `$name` / `{name}` plus `{date}`, `{time}`, `{run}` — via `filestore.service.put_file(source_kind="flow", origin_kind="flow_run", flow_id, flow_run_id, flow_node_id, …)`, placed by the node's agent (else the flow's). Passes its input through unchanged; emits `file_id`, `filename`, `size_bytes`. An empty input is a node error |

On the canvas both render as one `FileNode`; after a run the node links to the file it read or kept ("open it in Files").

---

## 6. Execution Engine (flow_executor.py)

### FlowDefinition & Validation

`FlowDefinition` parses and validates a raw definition dict:

```python
flow_def = FlowDefinition(definition)
errors = flow_def.validate()     # returns list of error strings; empty = valid
G = flow_def.build_graph()       # NetworkX DiGraph of forward edges only
```

Validation checks:
- All edge source/target nodes exist
- All scope `node_ids` exist
- `back_edge_id` (if set) exists in the edge list

**Orphan-edge healing:** before validation, `execute_flow` calls `flow_def.prune_orphan_edges()`, which drops any edge whose source or target node no longer exists (e.g. an edge the LLM wired to a node it never created). ReactFlow already ignores such edges on the canvas; pruning lets the flow execute its valid subgraph instead of failing hard at validation. Removed edge IDs are logged.

If NetworkX is not installed, `build_graph()` returns `None` and execution falls back to definition order.

### Execution Order

`_build_execution_order(flow_def)` returns a list of execution units:

```
[
  ("node", "start-node-id"),
  ("scope", "scope-abc123"),      ← whole scope treated as one unit
  ("node", "decision-node-id"),
  ("node", "end-node-id"),
]
```

Uses `nx.topological_sort()` on the forward-edge graph. Scoped nodes are grouped: the scope unit appears at the position of the first scoped node in topological order.

### Parallel Execution (dependency levels)

`run()` does **not** execute units one at a time. `_build_execution_levels(flow_def)` groups units into **dependency levels** (via `nx.topological_generations` over a unit-collapsed graph — each scope is one super-unit); units within a level have no mutual dependency and run **concurrently** through `asyncio.gather()`, bounded by a `MAX_PARALLEL_NODES` (default 8) semaphore. This mirrors the platform's established parallel-phase pattern (`OptimizeEngine._stream_parallel_phases`).

- **State safety:** concurrent `_run_node` calls only read predecessor outputs (written in earlier levels) and write their own `node_id` key; token/cost `+=` accumulation is safe under cooperative asyncio scheduling.
- **Trace order:** `execution_trace` is folded back in deterministic level → unit order (units sorted within a level), so historical replay stays stable even though live SSE events interleave (each event carries `node_id`; the canvas keys by it).
- **Error semantics:** within a level, independent siblings of a failing node still complete; the run aborts after the level settles (first exception re-raised). A node with an `error` edge routes instead of raising.
- **Partial runs** use `_partial_execution_levels` (node-only, scopes flattened) with the same layering.
- **Fallback:** when NetworkX (or `topological_generations`) is unavailable, levels degrade to one-unit-per-level — i.e. the original fully-sequential behaviour.

### Node Executors

**`_execute_llm_node`** — the core executor for `task_llm` nodes:

```
1. Resolve {variable} (str.format) AND $variable references in prompt from state_ctx
2. Profile cascade: node.profile_id → caller_profile_id → user default
3. Session resolution: compact mode → pin to caller; individual → create new
4. POST /api/v1/sessions/<id>/query  (source='flow')
5. Poll GET /api/v1/tasks/<task_id> at 2s intervals until terminal status
6. Extract final_answer_text + token counts from task events
7. Return {"output": text, "session_id": ..., "tokens_input": ..., "cost_usd": ...}
```

Internal auth uses `create_internal_token(user_id)` — a short-lived JWT with `internal=True` marker, refreshed on every poll cycle.

**Bounded retry:** `_execute_llm_node` is a thin wrapper over `_execute_llm_node_once` that retries on `RuntimeError` (transient provider/network hiccups, task errors, poll timeouts) with exponential backoff. Retry count is per-node (`node.data.retries`, default `DEFAULT_LLM_NODE_RETRIES = 1`); `retries=0` restores fail-fast. Each retry emits a `flow_node_retry` SSE event. This stops a single provider blip from aborting the whole run, mirroring the self-correction resilience of the main pipeline.

**`_execute_tool_node`** — the flow's "do an action" primitive. Calls a named tool **directly, with no LLM round-trip**, through the same dispatcher the agent uses (`mcp_adapter.invoke_mcp_tool(APP_STATE, command, user_uuid, session_id, profile_id)`), which covers MCP data-source tools, `TDA_` builtins (e.g. `TDA_CurrentDate`), and component tools (`TDA_Charting`, …) in one call. Flow:

```
1. tool_name = node.data.tool_name        (raise if missing)
2. arguments = _resolve_arg_value(node.data.arguments, state_ctx)
       # $name / {name} resolved; a whole-value "$name" preserves the upstream type
3. profile_id = _resolve_profile_ref(node.data.profile_id) or caller_profile_id
       # selects the MCP server/connection
4. result, tin, tout = await invoke_mcp_tool({tool_name, arguments}, …)
5. if result.status == "error" → raise RuntimeError  (so error edges / retries apply)
6. output = result["results"] (rows) when present, else the raw result
7. return {output, tool_name, tokens_input: tin, tokens_output: tout, cost_usd: 0.0, _raw}
```

Pure data tools cost **0 tokens** and are fully deterministic — the key advantage over prompting a `task_llm` node to call the tool (which costs tokens and may not call it, or may hallucinate the call). Downstream nodes consume `output` (the row list) or reference it by `output_as`.

**`_execute_function_node`** — evaluates a Python expression through `safe_expression.safe_eval()`:

```python
result = safe_eval(_normalize_param_refs(expression), state_ctx)
```

`safe_eval` is an **AST allowlist**, not `eval()` with a swapped `__builtins__` (which is not a sandbox — any in-scope string can reach `object` via dunder attributes like `"".__class__.__mro__[1].__subclasses__()` and achieve arbitrary code execution). It:

- Parses in `eval` mode and walks every node against a fixed allowlist (literals, operators, comparisons, comprehensions, f-strings, calls).
- **Rejects any identifier or attribute beginning with `_`** — this severs every dunder-traversal escape (`__class__`, `__globals__`, `__subclasses__`, `__import__` …) while still allowing ordinary methods (`.lower()`, `.strip()`, `.split()`).
- Restricts bare-name calls to a vetted `SAFE_FUNCTIONS` table (`len`, `int`, `sorted`, `sum` …).
- Evaluates with `__builtins__` emptied, so no builtin is reachable by name.
- Caps source length and large `**` exponents (cheap CPU-bomb mitigation).

This matters because flow expressions are often LLM-generated and travel between users via Platform Flows / fork / marketplace — one user's expression can execute in another's process. `$name` references are normalized to bare names before evaluation. Coverage: `test/test_safe_expression.py` (47 assertions, including every known escape vector).

**`_execute_decision_node`** — evaluates a boolean condition through the same `safe_eval`. Returns `{"output": True/False, "condition_result": True/False}`. On a rejected/invalid condition it emits a **`node_warning`** SSE event (so the bad condition is visible on the canvas) and defaults to `False` — it no longer silently masquerades a broken condition as a legitimate False route.

### Partial Execution (run-from-node)

`execute_flow(..., start_node_id, run_mode)` supports re-running a slice of the graph without re-running everything — the n8n-style debugging accelerator.

| `run_mode` | Targets |
|---|---|
| `"full"` (default) | Whole flow from the start |
| `"single"` | Just `start_node_id` |
| `"downstream"` | `start_node_id` + everything reachable via forward edges |

Mechanics:
- **State seeding** — `_latest_run_node_outputs(flow_id, user_id)` loads the most recent run's `node_outputs` into `FlowState.node_outputs` so upstream values (and changed parameters) are in scope.
- **Order restriction** — `_compute_partial_targets` builds the node set; `_partial_execution_order` returns the topologically-ordered `("node", id)` units restricted to that set (scopes flattened).
- A new run is recorded, so the per-node Data panel and subsequent seeds reflect the latest state.

### Scope Execution

`_run_scope(scope_id)` iterates up to `scope.max_iterations` (default 10):

```
for iteration in range(max_iterations):
    stop if cancelled / over the token budget
    ctx = round history (round, max_rounds, previous_<alias>, history — windowed by history_window)
    for level in dependency levels inside the scope:   ← concurrent under parallelExecution
        await _run_node(node_id, scope_id, iteration)   ← each participant's session pinned across rounds
    record the round; stop if exit_when holds AND the round produced what it owed (convergence_gap)
emit flow_scope_complete {exit_reason: converged | max_rounds | cancelled | budget | error, iterations_completed}
```

Each round emits `flow_scope_iteration` (and `flow_scope_round_complete`) so the canvas frame shows "round N of M"; `flow_scope_complete` says how it ended. A missing or unreadable verdict never stops a loop (reported, runs to the limit). How each loop ended is kept in the run's `output_result.scopes`, which is what *Earlier runs* reads back. Pure helpers: `core/flow_rounds.py`.

### Session Context Model

Every run is **bound to a chat session** so it is monitorable in the session-scoped UX (sidebar entry + Live Status). Binding is resolved in `execute_flow` *before* the run starts:

| Trigger | Bound session |
|---|---|
| LLM tool (`TDA_Flow(execute)`) | the chat session it was launched from (`context["session_id"]`) — the run contributes to that conversation. |
| REST `POST /execute` **with** `session_id` | that session (reuse). |
| REST `POST /execute` **without** `session_id` (headless) | a **fresh session provisioned per run** — created **under the flow's effective profile** (its `defaultProfileId`, so the session chip matches the run's profile) and named after the flow (`"<flow name>"`). Each run gets its **own** new session; runs are never folded into a prior one. |

In the flow canvas the run target is a **selector + single button**: a two-option toggle — **"In Chat"** (passes the current `session_id` → result inline in the conversation) / **"New Session"** (passes none → a fresh session per click, named after the flow) — next to one **"Run Flow"** button that honours the selected target (so per-node "Run only this node" / "Run from here" use the same target). The separate **Serialize | Parallelize** control (shown only when the flow has parallel LLM fan-out) governs node execution/session-sharing and is independent of the run target.

The bound `session_id` is returned by `execute`, persisted on `flow_runs.session_id`, and — for an auto-provisioned session — broadcast live via **exactly one** `new_session_created` notification so the sidebar updates **without a refresh**. The session is created via `POST /v1/sessions` with the desired `name` in the body; that endpoint applies the name *before* its single broadcast, so `_provision_run_session` neither renames-then-re-emits nor double-broadcasts (an earlier second emit produced a duplicate sidebar card — now fixed). The frontend `new_session_created` handler is also idempotent by session id (replaces an existing card rather than prepending a duplicate). The session name is re-asserted after the run (the platform's LLM-driven auto-naming would otherwise rename it). *(`flows.runs_session_id` and `get/set_flow_runs_session_id()` survive from an earlier persistent-session design and are now vestigial — the per-run model never reads them.)*

**Node session sharing is governed by the flow's execution mode** (`settings.parallelExecution`), **not** by a request parameter — this guarantees the unsafe "shared session + concurrent writers" combination can never be selected:

| `settings.parallelExecution` | `node_session_mode` | Behaviour |
|---|---|---|
| `false` (default, serialize) | `compact` | All LLM nodes share the bound session and run sequentially; their tokens roll into that session's total. |
| `true` (parallelize) | `individual` | Each LLM node runs in its **own** fresh session (its tokens land in that child session, not the bound one); dependency levels run concurrently. |

> The `node_session_mode` field accepted by `POST /execute` is **vestigial** — `run()` derives the mode from `parallelExecution` and overrides any request value (`flow_executor.py`). To get per-node isolation, set the flow's `parallelExecution`, not the request param.

Per-node override via `node.data.session_context`: `"new"` forces a fresh session for that node (**highest priority**); `"pinned"` + `node.data.session_id` forces a specific session. Resolution lives in `_resolve_node_session(node_data, node_session_mode, caller_session_id)` (priority: `new` → compact+caller → individual → pinned → caller → fresh). A `task_llm` node that needs its **own** session creates it inline (the `else` branch of `_execute_llm_node_once`) — a NameError bug in that branch (logging an undefined `profile_id`) historically broke individual/parallel/`new` sessions and is fixed.

> **Cost tradeoff (compact mode).** In compact mode every LLM node inherits the bound session's conversation history, so a long pipeline's later nodes carry (and pay for) the accumulated context of the earlier ones in the same run. Desirable for conversational flows that *should* build on prior steps; for independent data-pipeline flows set the LLM node's `session_context: "new"` (fresh session for that node; data still flows via `output`/`output_as`/params, which are independent of chat history) or use parallelize mode. (Headless runs no longer reuse one persistent session across runs, so cross-run accumulation is gone — each run starts a clean session.)

> **Data flow into LLM-node prompts (gotcha).** A `task_llm` node's `prompt` is resolved with plain `str.format(**state_ctx)`, where `state_ctx` is the merged output of the node's **immediate predecessors only** (plus global params and first-row column shortcuts). It does **not** use the robust `{Node.col}`/`$name` resolver that decision/function nodes use, and it cannot reach a non-adjacent node's output. So a prompt can reference an immediate predecessor's column by bare name (`{total_revenue}`), but a `{Node.col}` form (spaces/dots) throws inside `.format()` and leaves *every* `{…}` literal. To surface a value produced upstream of the immediate predecessor, have the predecessor carry it (e.g. fold it into that node's query/output) — don't reference across a gap.

### The Flow-Run Turn (monitoring anchor)

When a run settles, **headless/REST runs write a cohesive flow-run turn** into the bound session (suppressed for chat-triggered runs via `record_run_turn=False`, which already produce the agent's own turn + per-node turns). The turn is **display-only** — it never re-counts tokens:

- **UI history** gets a rich "Flow Run" card (`_render_flow_run_card_html`): status, aggregate tokens/cost, and a button that opens the flow split panel to inspect run state + per-node output — the same affordance as the regular flow inline card. The card is static HTML in `session_history`, so the button is wired by a delegated `.flow-run-open-btn` handler in `ui.js` (→ `_openFlowCanvasFromTrace` → `openFlowSplitPanel`, which reconciles to the latest run via `/runs?limit=1`). The button label is **open-state aware** (parity with the inline flow card and every other component card): **"View in Canvas →"** when closed, **"Expanded in side panel →"** when this flow's canvas is already open. `openFlowSplitPanel`/`closeFlowSplitPanel` stamp the open flow id on `#flow-split-panel` (`dataset.flowId`) and report it through the shared `PanelOpenControl.shown` (page event `uderia:panel-state`); `ui.js` `_syncFlowRunCardButtons()` reflects it on every flow-run card. **The sync runs from the chat-log MutationObserver and writes `innerHTML`, so it guards re-entrancy with an idempotent state check (`dataset.openState`) — it only writes when the open-state actually flips, so the observer can't re-trigger it into an infinite loop.** Card colour is **derived dynamically** from the target component's manifest accent — the same MutationObserver sets `--sp-accent` on the card from `window.__componentAccents['flow']`; never hardcoded. Extensible via `_CARD_TARGET_COMPONENT` (card class → component id).
- **LLM `chat_object`** gets only a compact pointer — `"[Flow Run] '<name>' <status> — N tokens, $C. Inspect node-level detail via TDA_Flow(action='get_run', run_id=…)."` — so the model is aware of the run and can pull full node-level detail on demand (via `get_run`, which serialises `run` + `node_runs` to the model) without bloating its context. This honours the UI-only-verbose / pull-not-push split using `add_message_to_histories`'s `content` (LLM) vs `html_content` (UI) channels.
- **`workflow_history`** entry carries `profile_type='flow'`, `flow_id`, `flow_run_id`, plus `source='rest'` + `origin='flow'` — the **"Rest Call" + "Flow"** chat badges that distinguish a REST run from a conversation-triggered one — and **`profile_tag`** (the flow's **effective run profile**: the caller's when a session was passed, else the flow's `defaultProfileId`, resolved independently of the session's *stored* profile, which for a headless run would be the user default, not the flow's). This makes the flow-run "You" turn show a profile chip like every other turn; per-node turns still show their *own* (possibly overridden) profiles → run-level vs node-level layering. Accounting is **zeroed** (`turn_cost`/`turn_*_tokens = 0`) so the run never double-counts (the per-node `/query` turns already counted their tokens); the true aggregate rides in the display-only `flow_total_cost`/`flow_tokens_*`, sourced from the run ledger (correct even in `individual` mode, where node cost scatters to child sessions).

On reload, the flow turn renders its node-by-node trace + an Open-in-Canvas button in the **Live Status** panel (`UI.renderFlowRunTrace`, dispatched from the flow branch of `handleReloadPlanClick`).

### Token & Cost Accumulation

Each LLM node derives its own tokens/cost from the `token_update` events its query emitted, then `FlowState` accumulates across nodes:

```python
self.state.tokens_input  += output.get("tokens_input", 0)
self.state.tokens_output += output.get("tokens_output", 0)
self.state.cost_usd      += output.get("cost_usd", 0.0)
```

**Per-node derivation (`_execute_llm_node_once`)** — every `token_update` carries `statement_*` (this call), `turn_*` (cumulative this turn), `total_*` (cumulative session), and `cost_usd` (THIS call only):

- **Tokens** read the LAST `turn_input`/`turn_output` — turn-scoped, so they cover every call in the turn (including any LLM extension) and do **not** bleed in earlier nodes that share a serialized session (which `total_*` would).
- **Cost** is the **SUM** of `cost_usd` across all events. `cost_usd` is per-statement, so keeping only the last (the old behaviour) dropped every call but the final one — and when an LLM extension ran, its `token_update` is last, so the node reported only the extension's cost and silently dropped the main LLM cost.

**LLM-backed extensions are surfaced, not hidden.** Each post-processing extension that consumed tokens is captured into `_extensions` ({name, success, tokens, cost}) and emitted on `flow_node_complete`; the LLM node renders a compact per-extension cost line so an extension's LLM call is a visible step, never an invisible cost folded into the node total. (The flow header's live cost reads `cost_usd` from each node — a prior bug read a non-existent `cost` field and stayed at $0.)

Final totals are written to `flow_runs.tokens_input`, `flow_runs.tokens_output`, `flow_runs.cost_usd` when the run completes. Per-node counts are stored in `flow_node_runs`.

### Per-Run Token Budget

`FlowExecutor` accepts an optional `max_tokens_per_run` (input + output). Resolution: explicit arg (from the `execute` body / `POST /execute` `max_tokens_per_run`) → `definition.settings.max_tokens_per_run` → unbounded.

`_over_budget()` is checked **between dependency levels** and **between scope iterations** — cooperative, not pre-emptive (an in-flight LLM call cannot be cancelled mid-flight). When tokens cross the ceiling, the run stops before launching the next unit, is marked `error` with a `budget_exceeded` entry in `execution_trace`, and emits `flow_run_error` with `budget_exceeded: true`. This stops a runaway scope (`max_iterations × N` LLM nodes) early rather than after it has already burned the tokens — strictly better than a post-run-only check.

### Cancellation

`FlowExecutor.cancel()` sets `self._cancelled = True`. The run loop checks this flag before each execution level and before each scope iteration. Cancellation is cooperative — units already launched in the current level complete before the run halts. Run status is set to `"cancelled"` in both the DB and the SSE stream.

For the flag to reach a live run, the executor must be discoverable by `run_id`. `flow_executor.py` owns a module-level registry `_ACTIVE_EXECUTORS: dict[run_id, FlowExecutor]`: `execute_flow()` inserts the executor **before** launching its task and a `task.add_done_callback` removes it when the run settles. `cancel_run(run_id)` looks the executor up and calls `.cancel()`, returning whether a live instance was found; `flow_routes.cancel_run_endpoint` delegates to it (and still flips the DB status for already-settled runs). **Without this registry the cancel endpoint can only flip the DB status while the background task keeps executing every remaining node** — the bug this design replaced.

**Interrupting an in-flight LLM node (prompt abandonment + spawned-task cancel).** A `task_llm` node is a REST query being polled in `_execute_llm_node_once`; cancelling between *levels* wouldn't stop a node already mid-poll (worst case it would block up to `LLM_NODE_TIMEOUT` = 300 s). So a `cancel_check` callable (`lambda: self._cancelled`) is threaded `_run_node → _execute_node → _execute_llm_node → _execute_llm_node_once`. The poll loop checks it each iteration (≤ one `LLM_NODE_POLL_INTERVAL`, ~2 s) and, on cancel:
1. **Propagates the cancel** to the spawned query — `_cancel_spawned_task()` POSTs `/v1/tasks/<task_id>/cancel`, which calls `task_object.cancel()` so the underlying LLM generation actually aborts mid-flight (stops accruing cost) rather than running to completion. Best-effort: an already-finished task returns "already finished"; a propagation hiccup is logged and ignored.
2. **Raises `FlowCancelled`** — a dedicated exception (NOT a node error). `_run_node` catches it *before* the generic handler, records the node `cancelled` (best-effort — a bookkeeping failure never escalates a cancel into an `error`), emits `flow_node_cancelled` (canvas clears the spinner), and returns without raising. The run loop then settles to `cancelled`. In parallelize mode every concurrently-running node in the level independently notices the flag and cancels its own task.

### Run Failure & Resume

When a node fails with no `error`-typed edge, the run aborts. The run-level handler:
- Persists the **partial state** — `update_run_status("error", output_result=state.snapshot(), tokens_input/output, cost_usd)` — so the successful upstream node outputs (and the tokens already spent) survive on the failed run. This is what lets a later partial re-run seed upstream values via `_latest_run_node_outputs`.
- Stamps the failing node id (`_failed_node_id`, set in `_run_node` just before re-raising) onto the `flow_run_error` event, and surfaces a **cleaned** message (`_clean_error_message` strips internal `[FlowNode] node=… execution error:` prefixes; the raw error stays in the logs).

The canvas turns the failed node red (inspector shows the message) and renders a run-level **error banner** ("Run failed at *<node>*") offering **Retry from failed node** (`run_mode="downstream"` from the failed node — re-runs it plus everything downstream, seeded from the failed run's persisted outputs) and **Run full flow**. "Restart from where it failed" is therefore: fix the node → click *Retry from failed node*; upstream nodes are not re-executed, their outputs are seeded.

### Orphaned Run Reconciliation

In-flight executors live only in process memory (`_active_executors` in `flow_routes.py`). A crash or restart therefore leaves their `flow_runs` rows stuck in `queued`/`running` forever, with a dead SSE stream. `flow_db.reconcile_orphaned_runs()` runs once at startup (`main.py` `@before_serving`) and settles every such run to `cancelled` — the closest status the frontend treats as terminal (renderer.js run-status polling) — recording an `interrupted` reason in `execution_trace`. Idempotent; coverage in `test/test_flow_reconcile.py`.

---

### Pausing at a node that asked (PROP-011)

A `task_llm` node whose turn stops to ask raises `FlowNeedsInput`: the run is recorded
`input_required` with the node that asked, downstream nodes do not run, and the question is
stored against the node run (`flow_db.find_node_run_by_task`). The person's answer
(`POST /v1/input-requests/<id>/answer`) resumes the flow **at that node** through
`resume_flow_for_input`: a new run starts with the answer bound to the node's turn, the earlier
nodes' outputs carried forward, and the paused run linked (`paused_run_id`). An unanswered
question expires like a scheduled run's.

## 7. Frontend Renderer

### React Flow Integration

The canvas uses React 18 + `@xyflow/react` v12, loaded from CDN as UMD bundles. The renderer bootstraps React via `ReactDOM.createRoot()` inside the `#flow-split-content` div of the persistent `#flow-split-panel` aside element.

**CSS injection (critical):** `renderer.js` is served as an ES module from `/api/v1/components/flow/renderer`. All panel layout CSS (`#flow-split-panel { width: 45%; … }`) lives inside `_injectCSS()` — an idempotent function that inserts a `<style id="flow-renderer-css">` tag on first call and no-ops on subsequent calls. `_injectCSS()` **must** be called at the top of `openFlowSplitPanel()` before any panel geometry is needed; without it the container has zero computed width and React Flow renders completely invisibly.

**Viewport initialisation:** The React app is mounted without the `fitView` prop (which caused a layout thrash before node positions were stable). Instead, `onInit` fires two delayed `fitView()` calls — at 80 ms (catches the common fast-path case) and at 400 ms (catches slow CDN hydration) — using `{ duration: 300 }` for a smooth animated settle.

**Welcome-screen guard:** When the user switches away from the Flows view while the canvas split panel is open, `ui.js` would normally call `showWelcomeScreen()`. Both call sites in `handleViewSwitch` are guarded:

```javascript
const _flowPanelOpen = document.getElementById('flow-split-panel')
    ?.classList.contains('flow-split--open');
if (!_flowPanelOpen) {
    if (window.showWelcomeScreen) window.showWelcomeScreen();
}
```

Custom node types registered with React Flow:

| Type key | React component | Visual |
|---|---|---|
| `start` / `end` | `OvalNode` | Rounded pill (green / red) |
| `task_llm` | `LLMNode` | Card with profile tag badge and extension slots |
| `task_prompt` / `task_tool` / `task_action` / `task_function` / `merge` | `StandardNode` | Card tinted by `NODE_COLORS[type]` |
| `decision` | `DecisionNode` | Diamond-shaped card |
| `scope` | `ScopeGroupNode` | Loop frame with round stepper |
| `judge` | `JudgeNode` | Verdict card (pass/fail, score) |
| `artefact` | `ArtefactNode` | Canvas-writer card |
| `file_input` / `file_output` | `FileNode` | File card linking to the file read / kept |

### Exported Functions

| Export | Signature | Purpose |
|---|---|---|
| `renderFlow` | `(containerId, payload)` | Main entry — routes by `spec.view`; called by `addMessage()` |
| `openFlowSplitPanel` | `(spec)` | Opens `#flow-split-panel` and mounts/updates the React canvas |
| `closeFlowSplitPanel` | `()` | Closes the split panel and unmounts React |
| `isFlowPanelActive` | `() → boolean` | Returns true when the panel is visible |
| `getOpenFlowState` | `() → spec \| null` | Returns the current canvas spec for session cache |

**Shared panel chrome:** `#flow-split-panel` uses the common split-panel header (`class="sp-panel"`, `--sp-accent:#f59e0b`) — see COMPONENT_ARCHITECTURE.md → Split-Panel Chrome. The flow **instance switcher** lives in the header (`#flow-switcher-btn`, wired via `mountSplitPanelSwitcher` → re-open via `openFlowSplitPanel`); the old in-canvas React `.flow-selector` is hidden to avoid duplication. The React Flow canvas background is `var(--card-bg)` (matching the other panel bodies), not `--bg-primary`.

### Render Views

**`canvas` view** → `_renderFlowInlineCard(container, spec)`

Renders a compact flow summary card in the chat message with:
- Flow name, node count, edge count badges
- "Open in canvas" button that calls `openFlowSplitPanel(spec)`
- In `__canvasSplitMode`, the panel opens automatically on `create`/`open` actions

**`list` view** → `_renderFlowList(containerId, spec)`

Renders a scrollable list of the user's flows, each with a "Open" button and run count.

**`run_status` view** → `_renderRunStatus(containerId, spec)`

Renders a live run progress card with per-node status badges. The card polls `GET /v1/flows/runs/<run_id>` until the run reaches a terminal status, independent of the canvas SSE stream (so it works even when the canvas is closed).

**Silent views** (`silent_patch`, `silent_get`, `silent_list`, `silent_error`)

The container and its parent are collapsed to `display:none; height:0`. For `silent_patch`, `uderia:flow-patch` is emitted on the page bus before collapsing, so an open canvas can apply the mutation in-place.

### Live Canvas Animation (SSE)

When a run starts and `runState.runId` is set, a React `useEffect` opens an `EventSource`:

```javascript
const es = new EventSource(
  `/api/v1/flows/runs/${runId}/stream?token=${encodeURIComponent(token)}`
);
es.onmessage = (evt) => {
  const { type, payload } = JSON.parse(evt.data);
  _handleRunEvent(type, payload);
};
```

Events handled by `_handleRunEvent`:

| Event type | Canvas effect |
|---|---|
| `flow_run_start` | Resets all node statuses to `idle` |
| `flow_node_start` | Sets node badge to `running` with spinner |
| `flow_node_complete` | Sets node badge to `success` |
| `flow_node_error` | Sets node badge to `error` |
| `flow_node_cancelled` | Node abandoned because the run was cancelled mid-flight — clears the spinner, de-animates incoming edges (not an error) |
| `flow_node_warning` | Surfaces a non-fatal node warning (e.g. a decision condition that could not be evaluated and routed to False) |
| `flow_node_retry` | A `task_llm` node failed transiently and is being retried (carries `attempt` / `max_attempts`) |
| `flow_scope_start` | Highlights scope container |
| `flow_scope_iteration` | Updates iteration counter on scope container |
| `flow_scope_complete` | Marks scope container complete |
| `flow_run_complete` | Sets run summary; closes EventSource |
| `flow_run_error` | Sets global error state + run-level error banner; closes EventSource. Carries `node_id` (the failing node, for *Retry from failed node*), a cleaned `error` string, and `budget_exceeded: true` when the per-run token budget was hit |

The SSE endpoint closes automatically when the run reaches a terminal status (`complete`, `error`, `cancelled`).

### Bidirectional Sync Protocol

**Canvas → Backend** (user edits the graph visually):

The React Flow canvas intercepts node drag, add, and delete events and sends `PATCH /v1/flows/<flow_id>/patch` with an `op` and payload. `apply_patch()` in `flow_db.py` applies the operation and returns the updated definition. The canvas reconciles its local React state against the server response.

**Backend → Canvas** (LLM edits via `TDA_Flow`):

When the handler returns a `silent_patch` spec, `renderFlow()` emits `uderia:flow-patch` on the page bus (`static/js/uderiaBus.js` — the one registry of page events; no raw `CustomEvent` on `document`):

```javascript
UderiaBus.emit('uderia:flow-patch', { flow_id, op, payload, flow });
```

The open canvas listens at:
```javascript
UderiaBus.on('uderia:flow-patch', onPatchEvent);
```

`onPatchEvent` applies the mutation directly to React state via `setNodes` / `setEdges`, with a 300 ms CSS slide transition on existing nodes (`_enableNodeSlide(300)`). For `insert_node`, the full updated definition is included in `payload` and the canvas performs a full reconcile rather than a minimal patch.

### Following changes made elsewhere

Every flow store write is announced once as `component_changed` (manifest `signals.announces`: `created · updated · deleted · snapshot`). The renderer follows it with `UderiaBus.onComponent('flow', …)` for the flow open in `#flow-split-panel`:

- **Deleted anywhere** → the panel closes.
- **Updated elsewhere** (another tab, a REST script — never this page's own writes, whose echo the bus marks `own`, nor a `versions` snapshot record) → the canvas is **not** redrawn under the user's hand: the shared split-panel notice says *Changed elsewhere* and offers **Reload** (`splitPanelChrome.showSplitPanelNotice`).

The agent's own edits during a turn still reach the open canvas live through `uderia:flow-patch`, and its runs through `uderia:flow-run-event` (§16.8). Gates: `test/test_component_bus.mjs` §7, `test/test_component_signals.py` §D.

### Node Status Badges

Each canvas node renders a status badge driven by `nodeRunStatus[nodeId]`:

| Status | Color | Indicator |
|---|---|---|
| `idle` | Gray | — |
| `queued` | Slate | Clock icon |
| `running` | Blue | Spinner |
| `success` | Green | Check icon |
| `error` | Red | X icon with tooltip |
| `skipped` | Amber | Skip icon |
| `cancelled` | Gray | Cancel icon |

### Inline Card

The `canvas` view renders an inline card using theme-aware CSS variables (`var(--card-bg)`, `var(--border-primary)`, `var(--text-primary)` etc.) — never hardcoded Tailwind colour classes. The card includes:

- Flow name (bold) and description (muted)
- Node / edge / scope count badges
- Last-modified timestamp
- "Open in canvas" button

Clicking "Open in canvas" fetches the freshest flow definition from `GET /v1/flows/<flow_id>` before mounting, ensuring the canvas always reflects the current server state.

### Layout Engine (Sugiyama-Barycenter)

`flow_layout.py` implements a standalone Sugiyama-Barycenter hierarchical layout algorithm that positions nodes automatically. It is imported by `flow_db.py` and called at creation time and after every structural patch.

**Algorithm passes:**

1. **Longest-path layering** (`_assign_layers`) — assigns each node to a horizontal layer via reverse BFS from sinks; forward edges always point downward.
2. **Barycenter crossing minimisation** (`_minimize_crossings`) — runs 3 top-down + bottom-up sweeps, computing the barycenter position of each node as the mean of its neighbours' positions in the adjacent layer and reordering accordingly.
3. **Median X-coordinate assignment** (`_assign_x_coordinates`) — aligns each node to the median X of its neighbours across three passes (downward, upward, average), then spaces columns evenly at `X_STEP = 220 px`.

**Layout constants** (defined in `flow_layout.py`; the matching values in `renderer.js` exist only for the `window.__flowDebug` helper — see the single-source-of-truth note below):

| Constant | Value | Purpose |
|---|---|---|
| `X_CENTER` | 0 | Horizontal centre of the canvas (`nodeOrigin=[0.5,0]`) |
| `X_STEP` | 220 | Horizontal spacing between columns |
| `Y_START` | 80 | Y offset of the first (top) layer |
| `Y_STEP` | 160 | Vertical spacing between layers |

**Public API:**

```python
compute_layout(definition) → Dict[str, Dict[str, float]]
# Returns {node_id: {"x": float, "y": float}} without mutating definition

apply_layout(definition) → None
# Mutates definition["nodes"][*]["position"] in place
```

**Auto-layout mode** — controlled by `definition.settings.autoLayout` (bool, **default `True` for new flows**):
- `True` → `apply_layout()` is called automatically after every `add_node`, `remove_node`, and `add_edge` patch
- `False` → nodes keep their positions; `apply_layout()` is only called when the user clicks "Apply Layout" in the toolbar

New flows default to `True`: `create_flow()` does `definition.setdefault("settings", {}).setdefault("autoLayout", True)` and `generate_empty_flow()` seeds `settings.autoLayout = True`, so a freshly built/created flow is arranged without a manual Layout click.

The layout result is included in the `apply_patch()` response as `layout_applied: true` / `false`.

**Toolbar controls:**

| Control | Behaviour |
|---|---|
| **Auto** toggle | Enables auto-layout; immediately re-layouts the current graph (calls the backend `/layout` endpoint) |
| **Off** toggle | Disables auto-layout; nodes become free-draggable |
| **Apply Layout** button | Visible only when auto-layout is OFF; one-shot layout call |

The active mode is persisted in `definition.settings.autoLayout` via `update_settings` patch so it survives session reload.

**Single source of truth — the backend owns layout (critical):**

Node positions are computed **only** by the backend `compute_layout`/`apply_layout` (`flow_layout.py`) — at create time, after each structural patch, and on demand via `POST /v1/flows/<flow_id>/layout` (the **Apply Layout** button and the **Auto** toggle). The stored positions on every node are authoritative.

The frontend **must never re-run a layout on open or resize.** A topological DAG layout is width-independent: node positions never need to change when the canvas grows or shrinks — only the zoom-to-fit (`fitView`) should react to the new space. Accordingly:

- `_realignToViewport()` (fires on the `#flow-split-panel` ResizeObserver, e.g. when the resource panel opens/closes) does **remeasure + `fitView` only** — it does not reposition nodes.
- The lightweight client-side `_autoLayout()` (a naive rank-centering helper) is confined to the `window.__flowDebug` tooling. It is **not** equivalent to the backend engine — it collapses single-node ranks onto a vertical spine and does not branch decision nodes. Calling it in any live path (open, resize, mount) overwrites the correct backend positions with an inferior spine, which historically forced a manual "Apply Layout" click on every open. Do not reintroduce it into a render path.
- On session restore, `_restoreFlowState()` (`sessionManagement.js`) re-fetches the authoritative flow by `flow_id` (`GET /v1/flows/<id>`) before opening, so a stale `localStorage` snapshot's positions are replaced with the stored backend layout rather than rendered as-is.

**Decision-branch normalization:** `_normalize_decision_branches(definition)` (in `flow_db.py`, called from `create_flow` and `update_flow`) assigns the unused `'true'`/`'false'` condition to any condition-less edge leaving a decision node and sets its `type` to `'conditional'`. This guarantees decision branches render their colour-coded True/False labels and are actually followed by the executor (which only activates edges whose `condition` is `'true'`/`'false'`).

### Canvas Button Session Guard

The **Open Canvas** buttons (one in each flow row in the list view, one in the detail panel) are disabled until a conversation session exists in `window.__currentSessionId`. This prevents the canvas from opening into an empty, uninitialised state.

**Implementation** (inside the `flowsHandler.js` IIFE):

```javascript
const _canvasBtns = new Set();   // all wired canvas buttons
let   _sessionPollTimer = null;

function _hasSession() {
    return !!(window.__currentSessionId || localStorage.getItem('currentSessionId'));
}

function _setCanvasBtnEnabled(btn, enabled) { /* applies disabled/opacity/cursor/title */ }

function _registerCanvasBtn(btn) {
    _canvasBtns.add(btn);
    _setCanvasBtnEnabled(btn, _hasSession());   // immediate state
    if (!_hasSession() && !_sessionPollTimer) {
        _sessionPollTimer = setInterval(() => {
            if (_hasSession()) {
                clearInterval(_sessionPollTimer);
                _sessionPollTimer = null;
                _canvasBtns.forEach(b => { if (document.contains(b)) _setCanvasBtnEnabled(b, true); });
                _canvasBtns.clear();
            }
        }, 400);
    }
}
```

Both `_wireProfileFlowRowActions()` (`[data-action="canvas"]`) and `_wireProfileFlowForm()` (`#ff-canvas`) call `_registerCanvasBtn(btn)` when wiring their respective button.

`_openFlowCanvas()` also checks `_hasSession()` at the top as belt-and-suspenders and shows a toast if the session is somehow missing.

**State source:** `window.__currentSessionId` is the global written by `setCurrentSessionId()` in `state.js` — identical to the global used by `renderer.js` for `in_session` execution. `localStorage('currentSessionId')` is checked as a fallback for page-reload scenarios.

---

### Canvas Intelligence Features

Three opt-in / opt-out behaviours are exposed through an **Options menu** (gear icon in the toolbar). Each setting is persisted in `definition.settings` via `update_settings` patch.

#### Smart Delete (Bridge Reconnect)

When a node is deleted, if it has both predecessor and successor nodes, the engine automatically creates bridge edges connecting each predecessor directly to each successor — preserving the flow's logical continuity without manual rewiring.

**Controlled by:** `definition.settings.smartDelete` (bool, default `true` — opt-out model)

**Bridge creation rules (backend — `flow_db.py` `remove_node` op):**

| Predecessor count | Successor count | Result |
|---|---|---|
| 1 | 1 | Bridge pred → succ |
| N > 1 | 1 | Bridge each pred → succ |
| 1 | M > 1 | Bridge pred → each succ |
| N > 1 | M > 1 | **No bridges** (N×M explosion guard) |

Only `"normal"` (non-cycle, non-animated) edges are considered. Bridge edges have type `"normal"` and ID `e-bridge-{uuid8}`.

**Frontend animation:** After the delete patch returns, the renderer compares edge IDs before and after the call. New edges that connect non-deleted nodes are classified as bridge edges and receive a 320 ms CSS `flow-edge--entering` enter animation.

#### Snap-to-Grid

Controls the React Flow `snapToGrid` prop.

**Controlled by:** `definition.settings.snapToGrid` (bool, default `true` — preserves existing behaviour)

When enabled, dragged nodes snap to a 15 × 15 px grid. When disabled, nodes move freely.

#### Path Highlight on Hover

When the pointer enters a node, all nodes and edges that are **not** on the forward or backward path through the hovered node are dimmed to 12–18 % opacity. Moving the pointer away restores full opacity.

**Controlled by:** `definition.settings.pathHighlight` (bool, default `false` — opt-in)

**BFS implementation (frontend):** `onNodeMouseEnter` runs two BFS passes from the hovered node — one following edges forward (successors) and one following edges backward (predecessors) — collecting all reachable node IDs and the edge IDs that connect them. The results are stored in `highlightedNodes` (Set) and `highlightedEdges` (Set) React state. `displayNodes` and `displayEdges` useMemos apply `opacity: 1` to highlighted elements and `opacity: 0.12–0.18` (with a 0.15 s CSS transition) to all others.

**Options menu settings summary:**

| Setting | `definition.settings` key | Default | Description |
|---|---|---|---|
| Smart Delete | `smartDelete` | `true` | Bridge pred→succ on node deletion |
| Snap to Grid | `snapToGrid` | `true` | 15×15 px drag snap |
| Path Highlight | `pathHighlight` | `false` | Dim non-path nodes on hover |

### Editing & UX Affordances

All client-side, contained in `renderer.js`; the LLM tool surface (`instructions.json`) is unchanged by any of these.

**Smart parameter fields** — `SmartTextField` wraps the prompt/expression/condition inputs. Typing `$` opens a grouped, typed autocomplete (Flow parameter → Global variable → Upstream output → `output`); unknown `$refs` are underlined inline and also surfaced as graph-validation warnings in the health bar (Jump-to-node). Scope is computed per node by `_buildScopeItems` (flow params + global vars + reverse-BFS upstream `output_as` names).

**Inspector Data panel** — per `producesOutput` node, shows the latest **Input** and **Output** JSON for that node plus a **Run node** button. Sourced from `GET /v1/flows/<id>/node-data` (latest result per node across recent runs, so single-node runs don't wipe other nodes' data); refreshes on inspect and on run completion.

**Editor mechanics:**

| Feature | Interaction | Notes |
|---|---|---|
| Undo / redo | `⌘Z` / `⇧⌘Z` (+ toolbar buttons) | Client-side stack of definitions captured at the `patchFlow` chokepoint; restore via `PUT /v1/flows/<id>`; capped 50; resets on flow switch |
| Inline rename | double-click a node | Floating editor over the label → `update_node {label}` |
| Decision branch labels | — | True/False edges render color-coded pills (green/red) + matching stroke/arrow |
| Duplicate | `⌘D` / context menu | Data-preserving copy incl. edges internal to the selection |
| Copy / paste | `⌘C` / `⌘V` | In-memory clipboard of nodes + internal edges |
| Multi-select | Shift+drag / Shift+click | Bulk delete (`Del`) and bulk ops via the selection toolbar |
| Edge insert / delete | hover an edge → `+` / `×` | `+` opens a node-type picker (reuses `insert_node`); `×` disconnects |
| Run-from-node | context menu | "Run only this node" / "Run from here →" (partial execution) |
| Execution stats | toolbar toggle | Per-node token/cost overlay; **off by default** and rendered only when on (`showAnalytics`). When off, no overlay markup is emitted — this also prevents the overlay from displacing handles so edges don't visually leak into node bodies |

Editing helpers read `rfNodesRef`/`rfEdgesRef` so they stay correct inside the `[]`-stable keyboard handler.

**Loops** — made from a multi-selection (*Make loop* in the selection toolbar), a single step (*Repeat as a loop* in its context menu) or ⌘K; the frame's *Edit* opens the loop editor (name, rounds, stop rule, history window, remove) and a − / + stepper sets the round limit; both are disabled while the loop runs. Rules and refusals: §5 *Scopes*.

**Runs** — reopening a flow paints its latest finished run; *Earlier runs* in the status bar lists the last 15 finished runs and paints the one picked (statuses, verdicts, canvas + version, how each loop ended). One painter, `_paintRunOverlay`, clears the previous run's fields first; the overlay is built from `GET /v1/flows/runs/<id>` by `_runOutcomeOverlay`, the same fields the live stream sets.

**Agent choice** — every agent field (a node's profile, a slot, the toolbar's default profile) opens the platform's shared agent picker: search plus Ideate / Focus / Optimize / Coordinate class pills. A plain dropdown is only the fallback when that picker has not loaded.

**Discoverability:**
- **Contextual selection toolbar** — floats on multi-select: "N selected · Duplicate · Copy · Make loop · Delete".
- **Help overlay** (`?` key / toolbar button) — grouped keyboard & gesture cheatsheet.
- **Command palette** (`⌘K`) — every action by name with fuzzy filter (`_paletteActions`).
- **Params panel** (toolbar) — two sections (Flow Parameters / Global Variables), body-portal, theme-aware.

All new surfaces use theme CSS variables (`var(--card-bg)`, `var(--text-*)`, `var(--border-*)`, `var(--teradata-orange)`) and monochrome SVG icons — light/dark safe.

### LLM-Node Extension Slots

Each `task_llm` node carries a vertical **extension slot rail** on its right edge. A slot attaches a post-processing extension (e.g. `boolean_check`, `extract`, `condition`) that runs on the node's answer, producing structured data a downstream `decision` node can route on. Clicking **+** opens a picker; configured slots are stored on the node as `data.data.extensions = [{id, param?}, …]`.

**Picker list derivation — manifest is the single source of truth.** The list of selectable extensions is **not** hardcoded in the renderer. `_fetchFlowExtensions()` (`renderer.js`) calls `GET /v1/extensions/activated` (which already applies admin governance + user activation) and keeps only entries whose manifest `flow_node_eligible !== false`, de-duplicated by `extension_id`. Every display fact — `label` (`display_name`), `desc` (`description`), and `hasParam` (`parameters.supported`) — comes from the manifest, so a newly activated extension or a brand-new built-in appears automatically with no renderer edit.

| Concern | Source of truth | Notes |
|---|---|---|
| Which extensions appear | manifest `flow_node_eligible` (default `true`) | Terminal/export extensions (e.g. `pdf`) set `false`. Surfaced via `manager.list_extensions()`. |
| Activation / admin governance | `GET /v1/extensions/activated` | Endpoint already filters disabled + non-activated extensions. |
| Label / description / hasParam | manifest (`display_name` / `description` / `parameters.supported`) | No parallel copy in the frontend. |
| Badge abbreviation + colour | `EXT_VISUALS` map + `_extVisual()` fallback | **Presentation only** — not an eligibility gate. Unknown ids get a derived abbreviation + deterministic colour, so user-imported extensions still render. |

> **The renderer must not gate the picker on a hardcoded extension list.** A prior implementation kept a `BUILTIN_EXTENSIONS` array that independently decided eligibility *and* params, which drifted from the manifests (new built-ins never appeared; `pdf` was excluded only by omission; `summary`'s parameter was wrong). Eligibility now lives in the manifest; the frontend constant is reduced to abbr/colour hints.

**No stale cache.** `_fetchFlowExtensions()` fetches on **every** picker open — there is no module-level activated-extensions cache. The endpoint is light and the picker opens on user action, so an extension activated mid-session shows up on the next open without a page reload. (The earlier never-invalidated cache caused activations to stay invisible until reload — do not reintroduce it.)

The slot rail itself renders synchronously from `_extVisual(id)` (abbr/colour/label), independent of the picker fetch, so configured slots show correctly on first paint. The executor (`_execute_extension_node`) resolves extensions by id with **no activation gate** — activation/eligibility is purely a picker-UX concern.

**Search + category grouping (picker discoverability).** As the eligible list grows (user-imported + marketplace extensions), the picker provides:
- A **search box** (auto-focused on open) matching `id + display_name + description + keywords` — the same semantics as the Extensions config screen (`extensionHandler.js`), mirrored as the self-contained `_extMatchesSearch()` predicate in `renderer.js` (the renderer is an isolated Preact ES module and can't import the `static/js` handler, so semantics are shared, not code). Typing `Enter` on a sole match selects it; `Escape` closes the picker.
- **Category-grouped results** under ordered section headers (Transform → Automation → Analysis → others alphabetical) via `_groupExtsByCategory()`, using the `category` field the endpoint already returns. Grouping replaces a category *filter control* — it organizes without consuming popover width.

Deliberately **not** ported from the config screen's full filter bar: **Status** (the picker only ever shows activated extensions), **Level/tier** (an authoring concern), and **Sort** (replaced by category grouping). This keeps the inline popover proportionate; the full filter bar stays a page-level concern. The popover widens to 260px with a scrollable, height-capped list. Frontend-only — no backend change (`category`/`keywords` were already in `list_extensions()`).

---

## 8. Database Schema

Defined in `schema/39_flow_system.sql`, auto-applied by `flow_db.py` on import (`_ensure_schema()`).

### `flows`

| Column | Type | Notes |
|---|---|---|
| `flow_id` | TEXT PK | `flow-{hex12}` |
| `user_id` | TEXT | Owner |
| `name` | TEXT | Display name |
| `description` | TEXT | — |
| `definition` | TEXT | JSON: `{nodes, edges, scopes, global_params}` |
| `is_template` | INTEGER | 1 = Platform Flow (visible to all users) |
| `is_reusable` | INTEGER | Reserved for library flows |
| `version` | INTEGER | Auto-incremented on every `update_flow()` call |
| `runs_session_id` | TEXT | **Vestigial** — from an earlier persistent-session design (one reused "<name> — runs" session per flow). The current model provisions a fresh session per headless run, so this column + `get/set_flow_runs_session_id()` are no longer read. Kept (self-applying `_ensure_columns()` migration) to avoid a destructive drop. |
| `created_at` | TEXT | ISO-8601 UTC |
| `updated_at` | TEXT | ISO-8601 UTC |

### `flow_runs`

| Column | Type | Notes |
|---|---|---|
| `run_id` | TEXT PK | `frun-{hex12}` |
| `flow_id` | TEXT FK | → flows |
| `user_id` | TEXT | — |
| `status` | TEXT | `queued` / `running` / `complete` / `error` / `cancelled` |
| `session_id` | TEXT | Chat session the run is bound to (the caller's, or the fresh session provisioned for a headless run). Self-applying `_ensure_columns()` migration. |
| `input_params` | TEXT | JSON overrides passed to `execute` |
| `output_result` | TEXT | JSON: final `FlowState.snapshot()` |
| `execution_trace` | TEXT | JSON: list of `{node_id, status, ...}` entries |
| `tokens_input` | INTEGER | Accumulated across all LLM nodes |
| `tokens_output` | INTEGER | — |
| `cost_usd` | REAL | — |
| `started_at` / `completed_at` | TEXT | ISO-8601 UTC |

### `flow_node_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `run_id` | TEXT FK | → flow_runs |
| `node_id` | TEXT | — |
| `node_type` | TEXT | `task_llm` / `task_function` / `decision` etc. |
| `scope_id` | TEXT | Null for non-scoped nodes |
| `iteration` | INTEGER | Scope iteration index (0 for non-scoped) |
| `status` | TEXT | `success` / `error` / `skipped` |
| `input_state` | TEXT | JSON: resolved state context passed to node |
| `output_state` | TEXT | JSON: node output dict |
| `error_msg` | TEXT | Set when status = error |
| `task_id` | TEXT | REST task ID for `task_llm` nodes |
| `session_id` | TEXT | Session used for `task_llm` nodes |
| `tokens_input` / `tokens_output` | INTEGER | Per-node token counts |
| `started_at` / `completed_at` | TEXT | ISO-8601 UTC |

Partial-run seeding reads the latest run's `flow_node_runs` (`input_state`/`output_state`). The inspector Data panel reads the latest result **per node across recent runs** (see `/node-data` in §9), so single-node runs don't erase other nodes' earlier data.

### `flow_variables`

Cross-flow, per-user global variables (Flowise-style `$vars`). Self-applying migration via `_ensure_flow_variables_table()` in `flow_db.py`.

| Column | Type | Notes |
|---|---|---|
| `user_id` | TEXT | Part of composite PK |
| `name` | TEXT | Part of composite PK |
| `value` | TEXT | Plaintext constant (not for secrets) |
| `updated_at` | TEXT | ISO-8601 UTC |

CRUD: `list_flow_variables`, `get_flow_variables_dict` (execution seed), `replace_flow_variables` (full overwrite, blank names dropped).

---

## 9. REST API Endpoints

All endpoints require JWT auth via `@require_auth`. Users can only access their own flows and runs.

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/flows` | List user's flows |
| `POST` | `/v1/flows` | Create a new flow |
| `GET` | `/v1/flows/tools` | **Profile-scoped** tool/prompt catalog for the canvas pickers. `?profile_id=<id>` returns that profile's active tools **and** prompts (from the per-profile classification — the same data the Classification Results dialog shows); without it, falls back to builtins + globally-connected server + components (no prompts). Returns `{tools:[{name,description,category}], prompts:[{name,description,category,arguments}]}` |
| `GET` | `/v1/flows/templates` | Topology template catalog with default skeletons (PROP-007) |
| `GET` | `/v1/flows/templates/<key>` | Live preview of one template for given params / source agent (creates nothing) |
| `GET` | `/v1/flows/<flow_id>` | Get a single flow |
| `PUT` | `/v1/flows/<flow_id>` | Update metadata or full definition |
| `DELETE` | `/v1/flows/<flow_id>` | Delete flow and all runs |
| `GET` | `/v1/flows/platform` | List Platform Flows (is_template=1, all users) |
| `POST` | `/v1/flows/<flow_id>/toggle-platform` | Admin: promote/demote to Platform Flow |
| `PUT` | `/v1/flows/<flow_id>/platform-meta` | Admin: update Platform Flow name/description |
| `POST` | `/v1/flows/<flow_id>/fork` | Fork a flow into the current user's Profile Flows |
| `POST` | `/v1/flows/<flow_id>/patch` | Apply a graph mutation op + auto-snapshot |
| `POST` | `/v1/flows/<flow_id>/layout` | Re-run the server layout on the flow |
| `GET` | `/v1/flows/<flow_id>/versions` | List version snapshots |
| `POST` | `/v1/flows/<flow_id>/versions` | Create a named snapshot |
| `POST` | `/v1/flows/<flow_id>/versions/<vid>/restore` | Restore flow definition from snapshot |
| `POST` | `/v1/flows/<flow_id>/execute` | Start async execution; returns `{run_id, status: "queued", status_url, session_id}` (`status_url` mirrors the query Task contract; `session_id` is the bound session) |
| `GET` | `/v1/flows/<flow_id>/runs` | List runs for a flow |
| `GET` | `/v1/flows/<flow_id>/node-data` | Latest input/output per node (powers the inspector Data panel) |
| `GET` | `/v1/flows/runs/<run_id>` | Get run status + node-level trace |
| `POST` | `/v1/flows/runs/<run_id>/cancel` | Cancel an in-progress run |
| `GET` | `/v1/flows/runs/<run_id>/stream` | SSE stream for live run events (see §10) |
| `GET` | `/v1/flow-variables` | List the user's cross-flow global variables |
| `PUT` | `/v1/flow-variables` | Replace the user's global-variable set (`{variables: {name: value}}`) |

The `execute` endpoint also accepts:
- `input_params` — key-value dict merged into `global_params` at run start
- `session_id` — bind the run to this session (reuse). **Omit it** and a fresh session is provisioned for this run, named after the flow (see §6 *Session Context Model*).
- `node_session_mode` — accepted for compatibility but **not authoritative**; the flow's `settings.parallelExecution` governs node session sharing (`run()` overrides any request value).
- `start_node_id` + `run_mode` — partial execution: `"single"` (one node) or `"downstream"` (node + descendants); omit / `"full"` runs the whole flow. See §6 *Partial Execution*.

> Full REST reference (request/response shapes, Task-Object aliases, end-to-end walkthrough): **[docs/RestAPI/restAPI.md §3.32 — Flow Execution & Monitoring](../RestAPI/restAPI.md)**.

`GET /v1/flows/<id>/node-data` returns `{run_id, status, node_data: {node_id: {status, input_state, output_state, tokens_*, ...}}}`. Each node shows its **most recent execution across recent runs** (scans `list_runs(limit=25)` newest-first, newest run wins per node). This means running a single node — which creates a new run containing only that node — does **not** wipe the earlier results of other nodes; revisiting a previously-run node still shows its data. `run_id`/`status` reflect the newest run.

---

## 10. SSE Stream Endpoint

`GET /v1/flows/runs/<run_id>/stream?token=<jwt>`

EventSource cannot set custom headers in the browser, so the JWT is passed as a query parameter. The endpoint validates the token before streaming.

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

Each message is a JSON object:
```json
{"type": "flow_node_start", "payload": {"run_id": "...", "node_id": "...", "node_type": "task_llm", ...}}
```

The stream closes automatically when the server emits `flow_run_complete` or `flow_run_error`. The active executor is tracked in `_ACTIVE_EXECUTORS: dict[run_id, FlowExecutor]` in `flow_executor.py` (populated by `execute_flow`, cleared on task completion); `flow_routes.cancel_run_endpoint` calls `flow_executor.cancel_run(run_id)`, which calls `executor.cancel()` on the live instance.

---

## 11. Version Snapshots

`snapshot_flow(flow_id, user_id, label=None)` creates a point-in-time copy of the current definition. It is a no-op (returns `None`) if the definition has not changed since the last snapshot (hash comparison).

Snapshots are triggered:
- Automatically after every `PATCH /v1/flows/<flow_id>/patch` (2-second debounce via `_schedule_snapshot()` in the handler)
- Automatically after every `PATCH /v1/flows/<flow_id>/patch` REST call (immediate, via `flow_routes.py`)
- On demand via `POST /v1/flows/<flow_id>/versions`
- Via the "Save Version" button in the canvas UI

`restore_version(version_id, flow_id, user_id)` replaces the current definition with the snapshot and increments `version`.

Auto-snapshots carry a **descriptive label** derived from the patch op via `_label_for_patch(op, payload)` (e.g. "Added LLM node", "Removed node", "Connected nodes"), so the History dropdown shows what each version changed rather than an opaque `vN`.

**Version history vs. undo/redo:** these are distinct. *Version history* (this section) is server-persisted, named restore points shown in the History dropdown. *Undo/redo* (§7 Editing) is an ephemeral, session-local client stack captured at the `patchFlow` chokepoint and restored via `PUT /v1/flows/<id>` — bound to `⌘Z` / `⇧⌘Z`.

---

## 12. Profile Integration

The flow component respects all four profile types:

| Profile Type | TDA_Flow Availability | Notes |
|---|---|---|
| `tool_enabled` (Optimize) | ✓ via FASTPATH | `insert_node` and primitive patch ops use fast-path; no tactical LLM call needed when only TDA_Flow is relevant |
| `llm_only` (Ideate) | ✓ via LangChain agent | Full tool availability via conversation agent |
| `rag_focused` (Focus) | ✓ | Available but flows are rare in knowledge-retrieval contexts |
| `genie` (Coordinate) | ✓ | Coordinator can invoke TDA_Flow; specialist sub-profiles also have access |

**Component instructions** (`instructions.json`) are injected into the system prompt at the intensity set in the agent's `componentConfig.flow.intensity` (`none` · `medium` · `heavy`; `medium` by default). Taste — node composition, naming, when to use a loop — lives in the companion skill `skills/builtin/flow-designer/`, which auto-assigns while the component is enabled. Key rules enforced by the instructions:

- One `TDA_Flow` call per user intent (no split operations)
- Prefer `insert_node` over primitive `add_node` + `add_edge` sequences for simple node additions
- Never fabricate flow or node UUIDs — always fetch with `get` or `list` first
- Use `TDA_Scheduler` (not `TDA_Flow`) to schedule recurring flow executions
- No emoji in text responses; status labels are plain text

---

## 13. LLM Instructions

`instructions.json` defines three intensity levels (`none` is empty — the tool is not described):

**`medium`** (default) — compact rules injected on every turn:
- Prefer `insert_node` for single-node additions
- Fetch before modify; never guess IDs
- One call per intent

**`heavy`** — full reference:
- Complete action and parameter reference
- Node type descriptions with data field requirements
- Patch op syntax examples
- Scope creation patterns
- Execution mode guidance (`node_session_mode`)

---

## 14. Design Decisions

### Why `render_target = INLINE` for all flow payloads

The chart component uses `sub_window`; the flow component uses `INLINE` for everything. This is intentional: the `#flow-split-panel` is a persistent aside element in `index.html` (not a dynamic sub-window). `openFlowSplitPanel()` mounts/updates React into this fixed container. The inline `[data-component-id]` card in the chat message acts as a persistent "handle" the user can click to reopen the canvas at any time, even after the session has been reloaded.

### Why `silent_*` views instead of no payload

Returning a `ComponentRenderPayload` with `view: "silent_patch"` allows the patch CustomEvent to fire against the live canvas without any visible chat card. Returning `None` or an error string would prevent the canvas from updating in-place during LLM-driven edits.

### Why LLM nodes call the REST API, not `execute_query()` directly

Routing through `POST /api/v1/sessions/<id>/query` ensures:
1. Cost and token tracking apply through the standard `rest_task_complete` pipeline
2. The session appears in the sidebar and its chat log is accessible to the user
3. The turn is tagged in the chat log. The node posts `source: "flow"`, which the `/query` endpoint splits into the transport `source='rest'` (Rest Call tag) **plus** the orchestration `origin='flow'` (Flow tag) — so a flow turn shows **Rest Call + Flow**. (`origin` is a server-stamped, persisted turn field, independent of `source`; see Coordinator Architecture → Turn Provenance Tags for the parallel genie case.)

### Why NetworkX is used for topological sort

`nx.topological_sort()` handles complex multi-root graphs and detects cycles in forward edges gracefully. The `back_edge_id` mechanism isolates scope back-edges from the sort so scopes appear as single units in the execution plan. If NetworkX is absent, execution falls back to definition order (which is correct for simple linear flows).

### Why layout lives in a separate `flow_layout.py` module

The layout algorithm has no dependency on the database or session state — it operates purely on a definition dict. Keeping it separate allows `flow_db.py` to import it cleanly, and makes the algorithm independently testable. `flow_db.py` calls `apply_layout(definition)` (which mutates in place) rather than `compute_layout()` (which returns a position map) so that a single call both computes and applies positions.

### Why Smart Delete defaults to ON

The most common case when a node is removed is that the user wants the flow to remain connected. Requiring the user to manually re-wire predecessor → successor edges after every deletion is friction-heavy and error-prone. The opt-out model (ON by default, toggle in Options) covers 90 % of cases automatically while still allowing the user to turn it off for scenarios where the disconnected result is intentional (e.g. splitting a flow into two branches). The N×M guard prevents an exponential edge explosion when both sides have multiple nodes — in that case the user must decide how to route manually.

### Why `_injectCSS()` is called in `openFlowSplitPanel`, not in `renderFlow`

`renderFlow()` is the entry point for **inline card** rendering (chat-message placeholders). It does not open or size the split panel. `openFlowSplitPanel()` is the first function that makes `#flow-split-panel` visible and therefore is the first moment when the browser needs to resolve `width: 45%` for the panel. Calling `_injectCSS()` any earlier (e.g. at module load time) would be premature and could conflict with the CSS cascade if the panel element hasn't been stamped yet. Calling it in `renderFlow()` would be too late — the panel would be visible for one render frame with zero width before the style tag appeared.

### Why the session poll uses `setInterval` rather than a custom event

The auto-init (`conversationInitializer.js`) runs as an ES module import at page load and sets `window.__currentSessionId` asynchronously, but it does not dispatch a browser event when done. Modifying `conversationInitializer.js` or `state.js` to emit an event would create a coupling between the session layer and the flow component. The 400 ms poll is a self-contained, low-overhead alternative: it starts only when at least one canvas button is in a disabled state, clears itself the instant a session appears, and adds zero overhead when the session is already present at wire time (the `!_hasSession()` guard prevents the interval from starting).

### Platform Flows vs Profile Flows

| | Profile Flows | Platform Flows |
|---|---|---|
| `is_template` | 0 | 1 |
| Owner | Per-user | Admin-promoted |
| Visible to | Owner only | All users |
| Can fork? | No | Yes (via `fork_flow()`) |
| Tab in UI | "Profile Flows" | "Platform Flows" |

Platform Flows allow admins to publish reusable flow blueprints. Any user can fork a Platform Flow to their own Profile Flows and modify it independently.

---

## 15. File Reference

| File | Purpose |
|---|---|
| `components/builtin/flow/manifest.json` | Tool definition (`TDA_Flow`), 19 actions, render targets, profile defaults, `instances: "user"`, `signals`, `answer_surface.pin` |
| `components/builtin/flow/handler.py` | `FlowComponentHandler` — routes actions, builds payloads, debounced snapshot |
| `components/builtin/flow/renderer.js` | React Flow canvas: `renderFlow`, `openFlowSplitPanel` (calls `_injectCSS()` first), `closeFlowSplitPanel`, `isFlowPanelActive`, `getOpenFlowState`. `onInit` fires two delayed `fitView()` calls at 80 ms + 400 ms. LLM-node extension slots: `_fetchFlowExtensions()` (manifest-driven, no cache), `EXT_VISUALS` + `_extVisual()` (presentation-only badge hints), `_extMatchesSearch()` + `_groupExtsByCategory()` (picker search + category grouping). |
| `src/trusted_data_agent/extensions/manager.py` | `list_extensions()` projection surfaces `flow_node_eligible` (default `true`) — the single source of truth consumed by the flow extension picker. |
| `components/builtin/flow/instructions.json` | LLM guidance: `medium` + `heavy` intensity levels |
| `src/trusted_data_agent/core/flow_layout.py` | Standalone Sugiyama-Barycenter layout engine: `compute_layout(definition)` → position map, `apply_layout(definition)` → mutates in place. Contains `_assign_layers`, `_minimize_crossings`, `_assign_x_coordinates`, `_median_x`, `_barycenter` helpers. |
| `src/trusted_data_agent/core/flow_db.py` | SQLite CRUD: `create_flow`, `get_flow`, `list_flows`, `update_flow`, `delete_flow`, `apply_patch`, `_should_auto_layout`, `list_platform_flows`, `set_flow_template_flag`, `fork_flow`, `update_flow_metadata`, `snapshot_flow`, `list_versions`, `restore_version`, `create_run`, `get_run`, `list_runs`, `update_run_status`, `append_node_run`, `get_node_runs`, `reconcile_orphaned_runs`. Imports `apply_layout` from `flow_layout.py`. |
| `src/trusted_data_agent/core/flow_executor.py` | `FlowState`, `FlowDefinition` (incl. `prune_orphan_edges`), `FlowExecutor` (parallel-level `run()`, `_over_budget`), `_execute_llm_node` (retry wrapper) + `_execute_llm_node_once`, `_resolve_node_session`, `_execute_function_node`, `_execute_decision_node`, `_execute_extension_node`, `_build_execution_levels`, `_partial_execution_levels`, `execute_flow`, `generate_empty_flow` |
| `src/trusted_data_agent/core/flow_topologies/` | PROP-007 topology templates: `generate`, `catalog`, `preview`, slots, pre-fill |
| `src/trusted_data_agent/core/flow_loops.py` / `flow_rounds.py` | Loop rules (`LoopError`) and round helpers |
| `src/trusted_data_agent/core/safe_expression.py` | `safe_eval` — AST-allowlist evaluator for `task_function`/`decision` nodes; `SAFE_FUNCTIONS`, `UnsafeExpressionError` |
| `src/trusted_data_agent/api/flow_routes.py` | `flow_bp` blueprint: 20+ REST endpoints + `GET /v1/flows/runs/<run_id>/stream` SSE |
| `schema/39_flow_system.sql` | DDL: `flows`, `flow_runs`, `flow_node_runs` |
| `components/component_registry.json` | Explicit registration (display_order=5, category=Automation) |
| `static/js/handlers/flowsHandler.js` | "Flows" tab UI: Profile Flows + Platform Flows sub-tabs, CRUD, run history. Includes `_hasSession()`, `_registerCanvasBtn()`, `_setCanvasBtnEnabled()`, `_canvasBtns` Set, `_sessionPollTimer` for canvas button session guard. |
| `static/js/componentRenderers.js` | Imports `renderFlow`; registers under `'flow'` key |
| `static/js/handlers/componentHandler.js` | Adds "Flows" tab for `flow` component_id |
| `static/js/handlers/splitPanelResizer.js` | Resize handle for `#flow-split-panel` |
| `static/js/handlers/sessionManagement.js` | Saves/restores flow split panel state on session switch |
| `templates/index.html` | `<aside id="flow-split-panel">` persistent container |
| `static/js/ui.js` | Welcome-screen guard: both `showWelcomeScreen()` call sites in `handleViewSwitch` check `#flow-split-panel.flow-split--open` and skip if canvas is active. |
| `static/css/main.css` | `.flow-task-tag` badge style for `origin='flow'` turns |

---

## 16. Execution & Profile Hardening (Jun 2026)

A batch of correctness and UX fixes to decision routing, the session/concurrency model, profile assignment, and layout. All are test-covered (`test/test_flow_decision_pruning.py`, `test/test_flow_parallel.py`, `test/test_flow_layout.py`).

### 16.1 Decision branch pruning (correctness)

Decision nodes previously ran **both** branches: `_active_successors()` existed but was **dead code** — `run()` executed every topological node unconditionally. It is now wired into the level loop: an active-node set is seeded with entry nodes; a unit runs only if it has an active incoming edge; after each level the nodes that ran propagate activation, and a **decision activates only the edge matching its `condition_result`**. The untaken branch is recorded `skipped` (emits `flow_node_skipped`). Files: `flow_executor.py` (`run()`, `_active_successors`). Test: `test_flow_decision_pruning.py`.

### 16.2 Serialize ⟷ Parallelize (session model, supersedes the compact/individual pill)

A single linear conversation cannot hold concurrent turns, so concurrent `task_llm` nodes writing to one shared session corrupted turn numbers. `settings.parallelExecution` (bool, default **false = serialize**) is now **authoritative over session sharing** — the corrupting "shared + concurrent" combination is unselectable:

- **Serialize:** units run **sequentially** in **one shared session** (`node_session_mode` forced to `compact`) — coherent conversation, correct turn numbers, no spawning.
- **Parallelize:** levels run **concurrently** (`asyncio.gather`), each node forced to its **own session** (`individual`). Context flows between nodes via flow parameters (`output`/`output_as`), never shared conversation.

A header **Serialize | Parallelize** toggle appears **only when the flow has a parallel fan-out of ≥2 `task_llm` nodes** (`_flowHasParallelLLMFanout` in `renderer.js` — decision branches are mutually exclusive, so they don't count; merge-aware so a single post-merge LLM doesn't trigger it). The old `compact/individual` pills were removed (toolbar + inline card). Files: `flow_executor.py` (`run()`), `renderer.js`.

### 16.3 Profile resolution — tags not IDs, fabricated-ID guard

The LLM authors profiles as friendly **tags** (`OPTIM`) and sometimes **hallucinates** a raw `profile-…` ID (a near-copy of a real one, one digit off). `_resolve_profile_ref(ref, user_id)` (`flow_executor.py`) now validates against the user's real profiles: a known tag → its real ID; an already-valid ID → unchanged; **anything else (fabricated ID / unknown tag) → `None`** so the node falls back to the flow default instead of carrying a broken reference. Applied at **runtime** (`_execute_llm_node`) and **authoring** (`handler.py` `_canonicalize_node_profile`, called from `create` / `add_node` / `update_node` / `insert_node`). The inspector dropdown bug (every profile showed "(undefined)") was fixed — it read `p.type`; the field is `p.profile_type`.

### 16.4 Flow-level default profile

`settings.defaultProfileId` lets the author set one execution profile for the whole flow. Cascade: **node `profile_id` → flow default → session caller → user default** (`run()` resolves the flow default and uses it as the caller fallback). On `create`, it is **initialised to the profile that built the flow** (`context.profile_id`). Changeable via a **Default profile** dropdown in the canvas header (`renderer.js`). Note: a flow built under an `llm_only` authoring profile (e.g. FlowDesigner) defaults nodes to it; DB nodes need a `tool_enabled` profile (e.g. `@OPTIM`) — set the default accordingly.

### 16.5 Per-node profile tag badge

Each `task_llm` node shows its **effective** profile as a tag pill (e.g. `@OPTIM`), coloured by the profile's IFOC type (purple = Optimize, green = Ideate, blue = Focus, orange = Coordinate) — colour conveys the type, so no type label. The pill is **dimmed** when the profile is **inherited** from the flow default. Stamped in the `displayNodes` useMemo (falls back to the default when a node's own `profile_id` doesn't resolve), so it updates live when the profile changes in the inspector or the header default changes. The pill is offset left of the top-right status-icon corner so it never overlaps the ✓/spinner.

### 16.6 Layout — decision branches align to their handles

A decision's **True** source handle is on the **right**, **False** on the **bottom**. `compute_layout` (`flow_layout.py`) now runs a deterministic post-pass: when a decision's true/false targets share a rank and True is left of False, it **exchanges their x** so the True node sits right and False left — eliminating crossed branch edges. Swapping two existing positions is safe; a merge below stays centred (median unchanged). Test: `test_flow_layout.py` ("TRUE branch is right of FALSE branch"). The decision node's status icon was also moved out of the rotated diamond into the unrotated wrapper and centred near the top vertex (it was being skewed 45°).

### 16.7 Edges missing on cold load — handle-bounds re-registration (`HandleBoundsFixer`)

**Symptom:** on platform start / hard refresh, entering a session with a flow showed the nodes correctly positioned but **no edges**; switching session away and back made them appear. Every prior *timing* fix failed because the defect is not a measurement delay.

**Root cause (confirmed via store-vs-DOM probe):** a single clean mount, edges present in the store, all nodes measured — but `node.internals.handleBounds` was `null` for every node (`0/N`). ReactFlow computes handle bounds during the node-measurement pass; on a **cold** mount (the `@xyflow/react` CDN has just finished loading) a custom node's `<Handle>` children mount **one frame after** the node is first measured, so the bounds pass misses them and stores nothing. Node *dimensions* are still measured (so nodes stay centred via `nodeOrigin:[0.5,0]`), but every edge loses its endpoints and renders **zero** paths. Nothing re-measures handles afterward — the node's `ResizeObserver` only fires on a size *change*, which doesn't happen when the handle children appear — so edges stay invisible until a full remount (warm path: handles are present in the first measurement pass, so it works). The old `inst.updateNodeInternals` retries (`_remeasureUntilReady`) all fired in the first ~100 ms, before the handles painted, and never again.

**Fix:** `HandleBoundsFixer` — a tiny component rendered as a **child of `<ReactFlow>`** (so it lives inside the store context, like `Controls`/`MiniMap`) that waits for `useNodesInitialized()` to flip true, then calls `useUpdateNodeInternals()` for every node id to re-register handle bounds, with retries at 60 ms and 260 ms to guarantee it lands after the handles paint. Gated on `_hasRFHooks` (the UMD build exposing the v12 store hooks). Independent of cold/warm timing.

A complementary, defensive `_mountSeq` token coalesces duplicate/concurrent `openFlowSplitPanel()` calls (auto-open + `_restoreFlowState`) into a single mount — hardening, not the edges fix. Files: `renderer.js` (`HandleBoundsFixer`, `_hasRFHooks`, `_mountSeq`).

### 16.8 Conversation-triggered runs animate the open canvas — single event bus (root cause)

**Symptom:** executing a flow from chat ("execute the Customer Revenue Analysis Flow") with the flow canvas open did **not** animate the canvas, even though a run started and completed. A query that uses a knowledge graph, by contrast, animates the open KG panel from any initiator.

**Root cause:** flow run events were emitted to **one of two divergent buses chosen by the initiator**, and the canvas was hard-wired to one of them plus to runs it started itself:

| Initiator | Emitter the caller passed | Landed on | Canvas saw it? |
|---|---|---|---|
| Canvas Run button → `POST /execute` | `flow_event_emitter` → `_broadcast_flow_event(run_id,…)` | per-run `/stream` (`_run_subscribers[run_id]`) | ✅ |
| Chat `TDA_Flow(execute)` | `flow_emitter` → `event_handler(…, "notification")` | per-user notification channel | ❌ |

The per-run `/stream` the canvas subscribes to was **never fed** for a chat-initiated run (its events went to the notification channel instead), so a run_id hand-off alone would have connected the canvas to a *dead stream*. KG works because it animates directly off the **always-on per-user notification channel** (guarded by `session_id`) — a broadcast bus, not a point-to-point stream keyed on a run_id you only get if you started the run.

**Fix — one canonical emitter, callers no longer choose a bus.** `execute_flow` now builds a single canonical emitter (`flow_executor.py` `_canonical_emitter`) that fans **every** event the same way for **every** initiator:
1. **per-run SSE stream** (`_broadcast_flow_event`) — the blow-by-blow animation, with the existing replay buffer for late subscribers;
2. **per-user notification channel** (`APP_STATE["notification_queues"]`) — **run-lifecycle events only** (`flow_run_start` / `flow_run_complete` / `flow_run_error`), keeping the shared channel light while giving the open canvas an always-on **discovery** signal for runs it didn't start.

Every payload is stamped with `flow_id` + `run_id` so consumers can route/guard. The REST `/execute` and chat handler callers stopped passing bus-specific emitters (`event_emitter` survives only as an optional additive observer for tests/future hooks). The per-run pub/sub registries (`_run_subscribers`, `_run_event_buffer`, `_broadcast_flow_event`) **moved from `flow_routes.py` into `flow_executor.py`** so they live with the run lifecycle (alongside `_ACTIVE_EXECUTORS`) and the canonical emitter can feed them directly — `flow_routes.stream_run_endpoint` imports them by reference.

**Frontend:** [notifications.js](../../static/js/notifications.js) re-emits `flow_run_*` notifications on the page bus as `uderia:flow-run-event` (formerly the DOM CustomEvent `flow:run-event`) (no session guard — flow runs aren't bound to the current session). The canvas (`renderer.js`) listens for it, guards by `flow_id` against the open flow, and on `flow_run_start` for a run it didn't start sets `runState.runId` — which triggers the existing per-run `EventSource` → `_handleRunEvent` animation pipeline (the replay buffer back-fills the events that fired before the subscription). This complements the pre-existing `_onFlowRunChange` adopter (which only covered the inline-card *button* path). Net effect: chat-, REST-, scheduler-, and other-session-initiated runs all animate the open canvas through one bus and one guard pattern, matching KG. Files: `flow_executor.py`, `flow_routes.py`, `components/builtin/flow/handler.py`, `notifications.js`, `renderer.js`.

**Snapshot on connect (reconcile-on-mount) — the other half of "snapshot + stream".** The `flow_run_start` broadcast is a *one-shot delta*; a canvas that mounts **after** a run started (e.g. you execute a flow in session A, then switch to session B showing the same flow mid-run) misses it. So on mount — the canvas remounts on every open/flow-switch — it also **reconciles against current state**: `GET /v1/flows/<flow_id>/runs?limit=1` (newest-first by `created_at`), and if the latest run is `queued`/`running` (and not its own, via `ownRunIdRef`), it adopts the run_id, handing off to the same `EventSource` + replay path. This mirrors the per-run stream's own replay buffer and the inline card's fetch-before-mount: a live view must reconcile current state on connect, then apply deltas. With both halves, the canvas is self-healing for all three timings — mount-before-run (live broadcast), mount-during-run (reconcile-on-mount), mount-after-run (reconcile finds the terminal run; final state shows via replay/`get_run` + the inspector Data panel). Renderer-only, reuses the existing `/runs` endpoint.
