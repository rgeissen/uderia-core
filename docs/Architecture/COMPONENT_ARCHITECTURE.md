# Component Architecture: Generative UI Component Library

> Modular, profile-aware component system that transforms LLM tool calls into rich UI elements — charts, sub-windows, media players, and more — with per-profile toggling, admin governance, and third-party extensibility.

## Overview

The Uderia platform renders LLM output through a **component library** where each content type (chart, table, audio, code editor) is a self-contained module with its own prompt instructions, backend handler, and frontend renderer. Components are toggled per-profile, dynamically injected into LLM prompts at runtime, and rendered on the conversation canvas via tool calls or data-driven detection.

This architecture replaces the monolithic approach where charting, table rendering, and other content types were hardcoded throughout the codebase. The chart component serves as the reference implementation — migrated from the legacy charting system into the first fully self-contained component.

---

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [System Architecture](#system-architecture)
  - [High-Level Data Flow](#high-level-data-flow)
  - [Component Categories](#component-categories)
  - [Builtin Component Catalog](#builtin-component-catalog)
  - [Render Targets](#render-targets)
- [Component Anatomy](#component-anatomy)
  - [Directory Structure](#directory-structure)
  - [Manifest Schema](#manifest-schema)
  - [Instructions System](#instructions-system)
- [Backend Architecture](#backend-architecture)
  - [Base Classes](#base-classes)
  - [Component Definition](#component-definition)
  - [Component Manager](#component-manager)
  - [Admin Governance Settings](#admin-governance-settings)
- [Chart Component: Reference Implementation](#chart-component-reference-implementation)
  - [5-Stage Mapping Validation & Repair Pipeline](#5-stage-mapping-validation--repair-pipeline)
- [Frontend Architecture](#frontend-architecture)
  - [Component Renderer Registry](#component-renderer-registry)
  - [Sub-Window Manager](#sub-window-manager)
  - [Split-Panel Chrome (shared)](#split-panel-chrome-shared)
  - [Open-in-Panel Control (shared)](#open-in-panel-control-shared)
  - [Workspace Panel](#workspace-panel)
  - [The Components View](#the-components-view)
- [Integration Points](#integration-points)
  - [Prompt Injection Pipeline](#prompt-injection-pipeline)
  - [Tool Call Routing](#tool-call-routing)
  - [Component Fast-Path](#component-fast-path)
  - [DOM Rendering](#dom-rendering)
  - [SSE Event Handling](#sse-event-handling)
    - [Component LLM Event Piggybacking](#component-llm-event-piggybacking)
- [REST API](#rest-api)
  - [User Endpoints](#user-endpoints)
  - [Admin Endpoints](#admin-endpoints)
- [Database Schema](#database-schema)
  - [The one enablement rule](#the-one-enablement-rule)
- [Admin Governance](#admin-governance)
- [Compute Component: the receipted-figure reference](#compute-component-the-receipted-figure-reference-s3-sep-2026)
- [Component Signals — how components talk to each other](#component-signals--how-components-talk-to-each-other)
- [Third-Party Components](#third-party-components)
- [Component-Instance Portability (export / import)](#component-instance-portability-export--import)
- [File Reference](#file-reference)

---

## Design Philosophy

### 0. Intelligence Layering: Component → LLM → Skill (the core principle)

**Uderia's central differentiator is that components are *intelligent*.** Every behaviour
that can be determined without judgement is absorbed by deterministic code, so the LLM is
left with only the decisions that genuinely need in-context reasoning, and a companion
skill supplies the taste to make those decisions excellent. Complexity is distributed
across three layers with strict separation of concerns:

| Layer | Home | Owns | Cost |
|---|---|---|---|
| **1 — Intrinsic intelligence (Component)** | `handler.py` / `renderer.js` | Everything derivable from data, context, or keyword patterns: shape repair, fuzzy matching, auto-open/auto-position/default sizing, layout, reference canonicalisation, resolution, ranking, validation-and-repair, error recovery. | 0 tokens |
| **2 — LLM (judgement only)** | `instructions.json` / manifest tool args | The decisions the LLM *must* make: which action, the required args, what the user's intent means in an ambiguous case. **Thin but complete** — the correctness baseline. | in-context tokens |
| **3 — Skill (quality uplift)** | `skills/builtin/<name>-expert/` | "How to think": visual-design patterns, composition strategy, query/prose style, naming conventions, anti-patterns. | context tokens when active |

**Escalation rule — always solve at the lowest layer that can do the job.** Before writing
a line of prompt or skill text, ask: *can the code decide this deterministically?* If yes,
it belongs in layer 1 and must NOT also appear in layers 2 or 3. Only escalate to a skill
(layer 3) for quality knowledge that needs domain taste but not real-time data; only
escalate to the LLM (layer 2) for judgement the code genuinely cannot make.

**Two operating modes, both must work:**

| Mode | Source | Guarantee |
|---|---|---|
| **Without the skill** | `instructions.json` + manifest alone | *Correctness baseline* — the LLM calls the right action with the right args, no errors, no missing required fields. |
| **With the skill** | instructions + companion skill | *Power-user ceiling* — excellent visual/compositional/stylistic decisions, anti-patterns avoided. |

**The design test (apply before shipping any component change):**
> *If the companion skill file were deleted, could the LLM still call the component
> without errors and without missing required fields?*
> — **Yes** → the split is correct. **No** → something correctness-critical leaked into the
> skill and must move back into `instructions.json`.
>
> And the inverse: *is any usage heuristic, layout opinion, or workflow sequence sitting in
> `instructions.json`?* If so it belongs in the skill — the instructions are the contract,
> not the coaching.

This layering is enforced platform-wide: the **P2 "minimal exposure"** pass trimmed every
component's `instructions.json` to the correctness baseline (parking the removed heuristics
in `docs/component-audit/P2_TO_P3_EXTRACTION.md`), and **P3** gave every action component a
companion `*-expert` skill to hold the quality layer. The skill auto-attaches to a profile
whenever its component is enabled — see *Companion Skills & Auto-Assign* in
[SKILL_ARCHITECTURE.md](SKILL_ARCHITECTURE.md).

> **Recorded exception — `skill_library` (PROP-005).** `TDA_SkillLibrary` (the progressive-disclosure
> reader for skills) deliberately has **no companion skill**: its quality layer is inherently
> per-library, and lives in each progressive skill's own always-resident ROOT (the pack
> author's prescriptive procedure — "read these anchors, in this order, before answering").
> A generic companion would be noise the design test explicitly bans. It is also the
> platform's first **headless** action component (no renderer; `metadata.suppress_inline`
> honored centrally in `components/utils.py:generate_component_html`) and the reference
> implementation of the **relevance gate** (`BaseComponentHandler.is_profile_relevant` —
> its tool is offered ONLY to profiles resolving an active progressive skill, closing the
> then-current `get_active_components` absent-entry-defaults-ENABLED asymmetry structurally; that
> asymmetry itself was retired in Aug 2026 by *The one enablement rule*).

> **Recorded exception — `find_tools`.** `TDA_FindTools` (the Governed Tool Surface's search) has no
> companion either: it is offered only on a governed surface (*Relevant per turn*, its
> `is_profile_relevant` reads `tool_surface.armed_governed`) and its tool description is the whole
> contract. See [TOOL_SURFACE_ARCHITECTURE.md](TOOL_SURFACE_ARCHITECTURE.md).

> **A companion follows the tool it teaches.** Where a component's LLM tool is narrower than its
> enablement, the handler declares `offers_tool(profile)` (see *The one enablement rule*). `files`
> is the reference: the component is enabled for every agent (panel, Keep, attachments), but
> `TDA_Files`, its instructions and `files-expert` reach the model only where
> `componentConfig.files.agentTool` is on — so the default-on component costs no tokens and moves
> no signature.

### 1. Self-Contained Components

Each component is a single directory containing everything needed to function — manifest, prompt instructions, backend handler, and frontend renderer. No changes to core files are required to add a new component.

### 2. Two Handler Tiers

| Tier | Base Class | LLM Involvement | Example |
|------|-----------|-----------------|---------|
| **Action** | `BaseComponentHandler` | LLM calls `TDA_*` tool | Chart, Canvas, Knowledge Graph |
| **Structural** | `StructuralHandler` | Automatic from data type | Table, Code Block, Key Metric |

Action components receive prompt instructions and tool definitions injected into the LLM context. Structural components render automatically based on `collected_data` item types — no tool call required.

### 3. Profile-Aware

Components are toggled per-profile via the `componentConfig` key on the profile JSON dict (same pattern as `knowledgeConfig`, `dualModelConfig`). A data analyst profile might enable charts and tables; a chat-only profile might disable everything except markdown. A profile without a `componentConfig` entry for a component falls through to that component's manifest default for its profile type (`profile_defaults.enabled_for`) — **not** to "enabled". See *The one enablement rule* below.

### 4. Deterministic Fast-Path

Components that produce deterministic output (e.g., chart rendering from structured data) declare `is_deterministic = True` and bypass the tactical LLM entirely during phase execution — saving tokens and latency.

### 5. Admin Governance

Administrators control component availability at the platform level (all/selective mode, user imports, marketplace access), while profile owners control per-profile enablement. This mirrors the Extension system governance pattern.

---

## System Architecture

### Three Tool Classes

The platform separates tools into three distinct classes with different lifecycles and availability:

| Class | Examples | Lifecycle | Availability |
|-------|----------|-----------|--------------|
| **System** | TDA_FinalReport, TDA_CurrentDate, TDA_DateRange, TDA_LLMFilter, TDA_LLMTask, TDA_ContextReport, TDA_ComplexPromptReport | Static, always on | `tool_enabled` only (planner infrastructure) |
| **Component** | TDA_Charting, TDA_Canvas, TDA_KnowledgeGraph, … (the 16 tools in the catalog below) | Dynamic, per-profile `componentConfig` | **ALL profile types** (platform feature; each manifest's `enabled_for` lists where it is eligible) |
| **MCP** | base_readQuery, dba_resusageSummary, etc. | Dynamic, per MCP server | `tool_enabled` + `llm_only` with tools |

**Key principle:** Component tools are a **platform feature** — they must be available (selectable by the LLM) for all profile types, not just `tool_enabled`. The `ComponentManager` is the single module all profile classes call into; no component logic lives in profile-specific code.

### High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              RUNTIME FLOW                                    │
│                                                                              │
│  Profile Config                                                              │
│       │                                                                      │
│       ├──────────────────────────────────────────────────────────────┐       │
│       │                                                              │       │
│       ▼                                                              ▼       │
│  get_instructions_text()                              get_langchain_tools()  │
│       │                                                    │                 │
│       ▼                                                    ▼                 │
│  {component_instructions_section}                    LangChain tools         │
│  → injected into MASTER_SYSTEM_PROMPT                (StructuredTool)        │
│                                                            │                 │
│  get_tool_definitions()                                    │                 │
│       │                                                    │                 │
│       ▼                                                    │                 │
│  "Component Tools" category                                │                 │
│  → injected into {tools_context}                           │                 │
│       │                                                    │                 │
│       ├─── tool_enabled: planner sees category ────────────│                 │
│       │                                                    │                 │
│       │    llm_only + tools: merged with MCP tools ◄───────┤                 │
│       │    llm_only (direct): auto-upgrade to agent ◄──────┤                 │
│       │    rag_focused: auto-upgrade after retrieval ◄─────┤                 │
│       │    genie: merged with invoke_* profile tools ◄─────┘                 │
│       │                                                                      │
│       ▼                                                                      │
│  LLM plans/executes → calls TDA_Charting, TDA_Canvas, TDA_Dashboard, etc.   │
│       │                                                                      │
│       ├── Fast-Path (deterministic) → ComponentManager.get_handler()         │
│       │       │                        → handler.process() directly          │
│       │       ▼                                                              │
│       │   ComponentRenderPayload                                             │
│       │                                                                      │
│       ├── Agent Path → LangChain StructuredTool.func() → handler.process()   │
│       │       │         (llm_only, rag_focused, genie)                       │
│       │       ▼                                                              │
│       │   ComponentRenderPayload (JSON)                                      │
│       │                                                                      │
│       └── Standard Path → MCP Adapter routes to handler                      │
│               │             (tool_enabled tactical)                           │
│               ▼                                                              │
│           ComponentRenderPayload                                             │
│               │                                                              │
│               ▼                                                              │
│  Formatter embeds: <div data-component-id="chart" data-spec='...'>           │
│         or                                                                   │
│  SSE event: component_render → sub-window creation                           │
│               │                                                              │
│               ▼                                                              │
│  Frontend: ComponentRendererRegistry.renderComponent() → DOM                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Categories

| Category | LLM Involvement | Prompt Injection | Tool Definition | Examples |
|----------|-----------------|------------------|-----------------|----------|
| **Action** | LLM explicitly calls `TDA_*` tool | Instructions injected | Tool definition injected | Chart, Canvas, Dashboard, Knowledge Graph, … (16 builtin) |
| **Structural** | Automatic from data type | None | None | (contract only — no builtin structural component ships today) |
| **System** | None — platform infrastructure (`SystemHandler`) | None | None | Context Window Manager |

### Builtin Component Catalog

The 19 builtin components (`components/builtin/<id>/`), as Administration → Components lists them
(ten ship in the public core; Scheduler, Flow, Dashboard, Canvas, Platform, Explain Plan, OBDA-READ, OBDA-MODEL and OBDA-ACT ship in the enterprise package and are read from its asset root — `ComponentManager._builtin_dirs`, `core/overlay.py`; `oss/public_core.json` decides):
17 action components (each with one `TDA_*` tool) and two system components (Needs Input is the one
system component that carries a tool, `TDA_AskUser` — a system component may where its manifest declares one). Each action component's
usage knowledge lives in its companion skill (`skills/skill_registry.json`, linked by `component_id`);
the three recorded exceptions (`skill_library`, `find_tools`, `needs_input`) have none. **Keeps** is the manifest's
`instances` declaration (whose the things it keeps are — see *Workspace Panel*); **Default** is
`profile_defaults.enabled_for` (where an agent with no explicit `componentConfig` entry gets it) plus
the relevance gate, if any (`is_profile_relevant` — eligible but offered only when it holds).

| Component | id | Tool | Companion skill | Keeps | Default · relevance gate | Architecture |
|---|---|---|---|---|---|---|
| Agent Memory | `memory` | `TDA_Memory` | `memory-expert` | agent | all four classes; **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [AGENT_MEMORY_ARCHITECTURE.md](AGENT_MEMORY_ARCHITECTURE.md) |
| Canvas Workspace | `canvas` | `TDA_Canvas` | `canvas-expert` | user | all four classes; **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [CANVAS_ARCHITECTURE.md](CANVAS_ARCHITECTURE.md) |
| Compute with Lineage | `compute` | `TDA_Compute` | `compute-expert` | none | all four · only while `COMPUTE_LINEAGE` is on | [§ Compute Component](#compute-component-the-receipted-figure-reference-s3-sep-2026) |
| Context Window Manager | `context_window` | — (system) | — | none | all four (infrastructure) | [CONTEXT_WINDOW_ARCHITECTURE.md](CONTEXT_WINDOW_ARCHITECTURE.md) |
| Dashboard | `dashboard` | `TDA_Dashboard` | `dashboard-expert` | user | all four classes; **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [DASHBOARD_ARCHITECTURE.md](DASHBOARD_ARCHITECTURE.md) |
| Data Visualization | `chart` | `TDA_Charting` | `chart-expert` | none | all four classes | [CHART_ARCHITECTURE.md](CHART_ARCHITECTURE.md) · [§ Chart Component](#chart-component-reference-implementation) |
| Database Navigator | `database_navigator` | `TDA_DatabaseNavigator` | `database-navigator-expert` | agent | Optimize + Ideate; **new agents start without it** (`on_for_new_agents: false`) | [DATABASE_NAVIGATOR_ARCHITECTURE.md](DATABASE_NAVIGATOR_ARCHITECTURE.md) |
| EXPLAIN Plan | `explain_plan` | `TDA_ExplainPlan` | `explain-plan-expert` | page | Optimize + Ideate · only while result sets are on (`COMPUTE_LINEAGE`); **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [EXPLAIN_PLAN_ARCHITECTURE.md](EXPLAIN_PLAN_ARCHITECTURE.md) |
| Files | `files` | `TDA_Files` (opt-in per agent: `offers_tool`) | `files-expert` (follows the tool) | user | all four classes (panel, Keep, attachments) | [FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md) |
| Flow Orchestrator | `flow` | `TDA_Flow` | `flow-designer` | user | all four classes; **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [FLOW_COMPONENT_ARCHITECTURE.md](FLOW_COMPONENT_ARCHITECTURE.md) |
| Knowledge Graph | `knowledge_graph` | `TDA_KnowledgeGraph` | `knowledge-graph-expert` | agent | all four classes; **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [KNOWLEDGE_GRAPH_ARCHITECTURE.md](KNOWLEDGE_GRAPH_ARCHITECTURE.md) |
| Needs Input | `needs_input` | `TDA_AskUser` (system) | — (the card and the composer rule are the contract) | agent | all four classes | [NEEDS_INPUT_ARCHITECTURE.md](NEEDS_INPUT_ARCHITECTURE.md) |
| OBDA-ACT | `action` | `TDA_Action` | `obda-act-expert` | agent | none (opt-in) · only with a bound graph | [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md) |
| OBDA-MODEL | `obda_model` | `TDA_ModelProposal` | `obda-model-expert` | agent | none (opt-in) · only with a bound graph | [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md) |
| OBDA-READ | `obda_read` | `TDA_DataProduct` | `obda-read-expert` | agent | none (opt-in) · only with a bound graph | [OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md) |
| Platform | `platform` | `TDA_Platform` | `platform-expert` | none | off for every agent until its owner enables it; roles in `componentConfig.platform.roles` | [PLATFORM_OPERATIONS_ARCHITECTURE.md](PLATFORM_OPERATIONS_ARCHITECTURE.md) |
| Skill Library | `skill_library` | `TDA_SkillLibrary` | — (per-library roots) | none | all four · only with an active progressive skill | [SKILL_ARCHITECTURE.md §4a](SKILL_ARCHITECTURE.md#4a-progressive-disclosure-skills--root--on-demand-library) |
| Task Scheduler | `scheduler` | `TDA_Scheduler` | `scheduler-expert` | user | all four classes; **new agents start without it** (`on_for_new_agents: false`, Nov 2026) | [SCHEDULER_ARCHITECTURE.md](SCHEDULER_ARCHITECTURE.md) |
| Tool Finder | `find_tools` | `TDA_FindTools` | — (its description is the whole contract) | none | all four · only on a governed tool surface (*Relevant per turn*) | [TOOL_SURFACE_ARCHITECTURE.md](TOOL_SURFACE_ARCHITECTURE.md) |

### Render Targets

Components declare which render targets they support via the manifest:

| Target | Location | Behavior | Example |
|--------|----------|----------|---------|
| `inline` | Inside chat message bubble | Renders as part of the response HTML | Table, chart, key metric |
| `sub_window` | Persistent panel on canvas | Stays open until user closes; survives new messages | Code editor, HTML preview |
| `status_panel` | Live Status panel | Renders in the status visualization area | Progress indicators |

A single component can support multiple targets. The chart component renders `inline` by default but supports `sub_window` for interactive exploration.

---

## Component Anatomy

### Directory Structure

Each component is a self-contained directory:

```
components/builtin/chart/
├── manifest.json         # Metadata, tool definition, dependencies, render targets
├── instructions.json     # LLM prompt text (intensity-keyed for chart)
├── guidelines.txt        # Supplementary prompt text (G2Plot guidelines)
├── handler.py            # Python: process tool args → ComponentRenderPayload
└── renderer.js           # JS: render payload into DOM element
```

**File:** `components/builtin/chart/manifest.json`

### Manifest Schema

**File:** `components/schemas/component_manifest_v1.schema.json`

Every component must include a `manifest.json` validated against the JSON Schema (`ComponentManager._validate_manifest`, a cached Draft 2020-12 validator). Validation is **warn-level and non-fatal**: each violation is logged with its path and the component still loads, so one bad manifest cannot take down startup. Required keys: `component_id`, `display_name`, `version`, `component_type`, `instances`, `signals`. Key sections:

```json
{
  "component_id": "chart",                    // Required: ^[a-z][a-z0-9_]*$
  "display_name": "Data Visualization",       // Required: human-readable name
  "version": "1.0.0",                         // Required: semver ^\d+\.\d+\.\d+$
  "component_type": "action",                 // Required: "action" | "structural" | "system"
  "instances": "none",                        // Required: "user" | "agent" | "page" | "none" (whose the things it keeps are)
  "signals": { "emits": [], "listens": [], "announces": [], "consumes_refs": true,
               "conflicts_with": ["dashboard"] },  // Required: see Component Signals
  "description": "...",                        // Optional
  "category": "Visualization",                // Optional: Automation|Code|General|Governance|Knowledge|System|Visualization
  "icon_svg": "<svg …>", "icon_accent": "#…", // Optional: card / panel identity (monochrome SVG)
  "answer_surface": { "open": true, "pin": true }, // Optional: row footer acts (Open, Pin to dashboard)
  "supports_instance_io": false,              // Optional: instance export/import + marketplace

  "tool_definition": {                        // Action components only
    "name": "TDA_Charting",                   // Must start with TDA_
    "description": "...",
    "args": { ... }                           // Parameter schema
  },

  "instructions": {                           // Prompt injection config
    "file": "instructions.json",              // Relative to component directory
    "format": "intensity_keyed",              // "plain" | "intensity_keyed"
    "placeholder_substitutions": {            // Optional: inject supplementary files
      "{G2PLOT_GUIDELINES}": "guidelines.txt"
    }
  },

  "backend": {
    "handler_file": "handler.py",             // Required
    "handler_class": "ChartComponentHandler", // Optional: auto-detected if omitted
    "fast_path": true                         // Hint for executor optimization
  },

  "frontend": {
    "renderer_file": "renderer.js",           // Required
    "renderer_export": "renderChart",         // Required: exported function name
    "cdn_dependencies": [{                    // Optional: external libraries
      "name": "G2Plot",
      "url": "https://unpkg.com/@antv/g2plot@2.4.31/dist/g2plot.min.js",
      "global": "G2Plot"
    }]
  },

  "render_targets": {
    "default": "inline",                      // Default render target
    "supports": ["inline", "sub_window"],     // All supported targets
    "sub_window": {                           // Sub-window configuration
      "title_template": "{chart_type} Chart: {title}",
      "default_size": { "width": 600, "height": 400 },
      "resizable": true,
      "interactive": false
    }
  },

  "profile_defaults": {
    "enabled_for": ["tool_enabled", "llm_only", "rag_focused", "genie"],  // The class default: an agent with no stored switch (Auto) follows it
    "default_intensity": "medium",                  // Instruction intensity
    "on_for_new_agents": true                       // The ONE recorded creation exception (absent = the class default); only the Database Navigator says false
  },
  "acts_every_turn": false                          // Optional: in effect on every turn without being called — the vocabulary's "active" (Context Window, Memory, Needs Input say true)
}
```

**The vocabulary on a component (Nov 2026):** *available* is the admin's (Administration →
Components, per-user overrides) plus the class list; *enabled* is the agent's (Capabilities —
a stored `componentConfig[id].enabled` is the owner's, an absent one follows `enabled_for` for
the class and reads **Auto**, the same mark as a companion skill following its component; a hand on
the switch owns it, *Reset to default* hands it back; a save never strips a stored switch);
*active* is the manifest's `acts_every_turn` — a declaration, never a switch, always a subset of
enabled. The per-profile components view (`GET /v1/profiles/<id>/components`) serves `auto`,
`acts_every_turn` and `active`; the Components lane reads `active/enabled/available`. Readers:
`enablement.is_auto`, `enablement.acts_every_turn`. Gate: `test/test_component_auto_mark.mjs`.

### Instructions System

Components can provide LLM instructions in two formats:

**Plain text** (`format: "plain"`): Single string injected as-is.

**Intensity-keyed** (`format: "intensity_keyed"`): JSON object mapping intensity levels to instruction variants:

```json
{
  "none": "",
  "medium": "Standard instructions: the actions and their required args...",
  "heavy": "The same contract with every arg documented..."
}
```

Every builtin component ships the three levels `none` · `medium` · `heavy`. The profile's `componentConfig[component_id].intensity` setting selects which variant to inject; an unknown level falls back to `medium` (`ComponentDefinition.get_instructions`). Default when the profile sets none: the session's `charting_intensity`, else `"medium"` (`ComponentManager.get_instructions_text`).

**Placeholder substitution**: The manifest's `placeholder_substitutions` field maps placeholders to supplementary files. For example, `{G2PLOT_GUIDELINES}` is replaced with the contents of `guidelines.txt` at instruction load time.

**`instructions.json` is layer 2 — the correctness baseline, nothing more** (see *Design
Philosophy §0*). It documents the actions, the required args, and any hard "don't do X"
constraint that prevents breakage — and deliberately omits usage heuristics, layout
opinions, and workflow sequences, which live in the companion skill (layer 3). Two
manifest-level facts reach the LLM on different paths and must each stay thin but complete:
`instructions.json` (via `{component_instructions_section}` in the system prompt) and the
manifest `tactical_prompt_rule` (via `{chart_mapping_rule_section}` in the tactical-planner
prompt) — *the tactical call does not receive `instructions.json`*, so a component that must
steer the per-phase tactical LLM (e.g. chart's mapping-key catalog) keeps that guidance in
`tactical_prompt_rule`, not only in the instructions.

---

## Backend Architecture

### Base Classes

**File:** `src/trusted_data_agent/components/base.py`

#### RenderTarget Enum

```python
class RenderTarget(Enum):
    INLINE = "inline"
    SUB_WINDOW = "sub_window"
    STATUS_PANEL = "status_panel"
```

#### ComponentRenderPayload

Dataclass that represents the output of a component handler — the bridge between backend processing and frontend rendering:

| Field | Type | Purpose |
|-------|------|---------|
| `component_id` | `str` | Component identifier (e.g., `"chart"`) |
| `render_target` | `RenderTarget` | Where to render (default `INLINE`) |
| `spec` | `dict` | JSON spec for frontend renderer |
| `html` | `str` | Pre-rendered HTML (inserted directly when non-empty, e.g. the compute receipt card) |
| `container_id` | `str` | Target DOM container ID (auto-generated `component-<hex>` if empty) |
| `title` | `str` | Display title |
| `metadata` | `dict` | Component-specific metadata (`tool_name`, `ref`, `suppress_inline`, `answer_surface`, …) |
| `tts_text` | `str` | Text-to-speech content |
| `window_id` | `str` | Sub-window identifier (auto-generated `sw-<hex>` for a `SUB_WINDOW` target) |
| `window_action` | `str` | Sub-window action: create/update/close (default `create`) |
| `interactive` | `bool` | Whether sub-window accepts user edits |

Key methods:
- `to_collected_data()` — Format for formatter integration into `collected_data`
- `to_sse_event()` — Format for SSE `component_render` event streaming

#### BaseComponentHandler

Abstract base class for action components (LLM-invoked via tool call):

```python
class BaseComponentHandler(ABC):
    @property
    @abstractmethod
    def component_id(self) -> str: ...        # Unique identifier

    @property
    @abstractmethod
    def tool_name(self) -> str: ...           # TDA_* tool name

    @property
    def is_deterministic(self) -> bool:       # Default True (fast-path)
        return True

    @abstractmethod
    async def process(self, arguments: dict, context: Optional[dict] = None
                      ) -> ComponentRenderPayload: ...          # Core processing

    def validate_arguments(self, arguments: dict) -> tuple[bool, str]: ...  # Optional validation

    # Optional hooks — every default leaves existing components untouched
    def is_profile_relevant(self, profile_config: dict) -> bool: ...   # relevance gate (default True)
    def offers_tool(self, profile_config: dict) -> bool: ...           # tool gate (default True)
    async def get_context_enrichment(self, query, profile_id, user_uuid) -> str: ...  # planner context
    supports_instance_io: bool = False                                 # instance portability
    async def export_instance(...) / import_instance(...) / list_instances(...)
```

The two gates are deterministic, cheap and never raise (a raising hook reads `True`); they are
read through `components/enablement.py`, never directly (see *The one enablement rule*).

#### StructuralHandler

Base class for non-tool-driven components (table, code block, key metric). Overrides `tool_name` to return `""` and `is_deterministic` to return `True`. No builtin structural component ships today; the class is the contract for one.

#### SystemHandler

Base class for **system** components — platform infrastructure with no tool, no LLM instructions and
a `status_panel`-only render target. Subclasses implement `component_id` and `get_metrics()` (for
observability). The Context Window Manager (`components/builtin/context_window/`) is the reference
implementation; `ComponentManager.get_system_components()` / `get_system_component(id)` list them.

### Component Definition

**File:** `src/trusted_data_agent/components/models.py`

The `ComponentDefinition` dataclass is the runtime representation of a loaded component:

**Identity fields:** `component_id`, `display_name`, `description`, `version`, `component_type`, `category`, `source` (builtin|agent_pack|user), `agent_pack_id`

**File path fields:** `directory`, `manifest_path`, `handler_path`, `renderer_path`

**Manifest data fields:** `manifest` (full JSON), `tool_definition`, `instructions_config`, `render_targets`, `frontend_config`, `profile_defaults`

**Runtime fields:** `handler` (instantiated handler instance), `_instructions_cache`

Key methods:

| Method | Purpose |
|--------|---------|
| `get_instructions(intensity)` | Load and cache instructions with intensity resolution and placeholder substitution |
| `get_tool_name()` | Extract tool name from manifest |
| `get_cdn_dependencies()` | Extract CDN dependency list |
| `get_default_render_target()` | Default render target from manifest |
| `supports_render_target(target)` | Check if target is supported |
| `to_api_dict()` | Serialize for REST API responses (adds `tool_name`, `signals`, `instances`, `supports_instance_io`, `icon_svg`/`icon_accent`, `has_handler`) |
| `to_frontend_manifest()` | Minimal manifest for frontend registry (incl. `signals`) |

`models.INSTANCE_SCOPES` is the one list of valid `instances` values (`user` · `agent` · `page` · `none`).

### Component Manager

**File:** `src/trusted_data_agent/components/manager.py`

Singleton that discovers, loads, and manages all components. Mirrors the `ExtensionManager` pattern from `src/trusted_data_agent/extensions/manager.py`. **All profile classes call into ComponentManager** — no component logic lives in profile-specific code.

#### Singleton Access

```python
get_component_manager(components_dir=None, user_dir=None)
reset_component_manager()
```

#### Discovery and Loading

The constructor resolves directory paths, builds the manifest validator, and triggers discovery:

```
components_dir  → {project_root}/components/
builtin_dir     → {project_root}/components/builtin/
registry_file   → {project_root}/components/component_registry.json
schemas_dir     → {project_root}/components/schemas/
user_dir        → ~/.tda/components/
```

Three-phase discovery (`_discover_and_load()`):

```
Phase 1: Built-in registry → component_registry.json
         Load components listed explicitly in the registry file
         Source: "builtin"

Phase 2: Built-in auto-discover → components/builtin/*/manifest.json
         Discover components not in registry (new additions)
         Source: "builtin"

Phase 3: User auto-discover → ~/.tda/components/*/manifest.json
         Third-party and user-installed components
         Source: "user"
```

Later phases override earlier ones — a user component with the same `component_id` replaces the built-in version. There is no agent-pack discovery phase: the `agent_pack` source value and `agent_pack_id` column are reserved for pack-bundled components (see *Agent Pack Integration*, not implemented). After discovery `_sync_to_database()` mirrors the set into `installed_components`.

Each component is loaded via `_load_component()`:
1. Parse `manifest.json` and validate it against the schema (warn-level, non-fatal)
2. Dynamically import handler via `importlib.util` (`_load_handler()`)
3. If `handler_class` is omitted, auto-detect first `BaseComponentHandler` subclass
4. Register in `_components` dict and `_tool_to_component` lookup

#### Public API

**Access methods:**

| Method | Returns | Purpose |
|--------|---------|---------|
| `get_component(id)` | `ComponentDefinition` \| `None` | Get single component by ID |
| `get_handler(tool_name)` | `BaseComponentHandler` \| `None` | Get handler by tool name |
| `is_component_tool(tool_name)` | `bool` | Check if tool belongs to a component |
| `get_component_by_tool(tool_name)` | `ComponentDefinition` \| `None` | Component that owns a tool |
| `get_all_components()` | `List[ComponentDefinition]` | All loaded components |
| `get_builtin_components()` | `List[ComponentDefinition]` | Built-in only |
| `get_system_components()` / `get_system_component(id)` | `List` / `ComponentDefinition` | System components (context window, needs input) |

**Profile-aware methods:**

| Method | Purpose |
|--------|---------|
| `get_active_components(profile_config, user_uuid=None)` | Delegates to `components/enablement.py` — the one rule (global gate → explicit `componentConfig` → manifest `enabled_for`), then the relevance hook |
| `get_tool_definitions(profile_config)` | Tool definitions for active action components whose tool is offered (`offers_tool`) |
| `tool_definition_for(comp, profile_config)` | THE tool definition a model sees: `[[gate:…]]` blocks resolved in the description and every arg description, `source_ref` added for a `consumes_refs` component |
| `get_action_tool_status(profile_config)` | Every action tool paired with its per-profile `disabled` flag (the *Component Tools* category `configuration_service` publishes into the tool catalog) |
| `get_instructions_text(profile_config, session_data)` | Assemble all active component instructions with intensity resolution |
| `get_tactical_prompt_rules(profile_config)` | Collect manifest `tactical_prompt_rule`s of active components (tactical planner) |
| `get_context_enrichment(...)` / `get_trusted_scope(...)` | Planner context from components that override `get_context_enrichment` (the KG); the KG's deployed trusted scope |
| `has_active_tool_components(profile_config)` | Quick boolean: does this profile have active action components? |
| `get_langchain_tools(profile_config)` | Create LangChain `StructuredTool` objects from active action components |

**Module-level convenience functions:**

| Function | Purpose |
|----------|---------|
| `get_component_instructions_for_prompt(profile_id, user_uuid, session_data)` | Resolve profile → assemble instruction text |
| `get_component_langchain_tools(profile_id, user_uuid, session_id, …)` | Resolve profile → create LangChain tools |
| `get_component_context_enrichment(...)` / `get_component_trusted_scope(profile_id, user_uuid)` | Resolve profile → component planner context / trusted scope |

These convenience functions handle profile resolution internally so profile classes need only one call.

**Frontend methods:**

| Method | Returns |
|--------|---------|
| `get_frontend_manifest()` | `List[Dict]` — minimal manifests for `ComponentRendererRegistry` |

**Hot-reload:**

`reload()` clears all registries and re-runs discovery. Returns new component count.

#### LangChain Tool Factory

`get_langchain_tools()` wraps each active action component's handler as a LangChain `StructuredTool`:

```python
def get_langchain_tools(self, profile_config: Dict, context: Dict) -> List[StructuredTool]:
    # 1. Filter active action components with handlers
    # 2. Build Pydantic args_schema from manifest (required vs optional fields)
    # 3. For each: create async _run_component() wrapper around handler.process()
    # 4. Return list of StructuredTool objects
```

The async wrapper (`_run_component()`) serializes the `ComponentRenderPayload` to JSON so the LLM agent receives structured output. This enables component tools to work with `ConversationAgentExecutor` (LangGraph-based) across all profile types.

**Pydantic None-value stripping:** The `_build_args_schema()` function creates Pydantic models from the manifest's `args` dict. Optional fields (e.g., `sort_direction`, `sort_by`, `row_limit`) get `default=None`. When the LLM omits an optional argument, Pydantic fills in `None` — but handlers expect absent keys (matching the adapter path where optional args are simply omitted). The `_run_component()` wrapper strips all `None` values from kwargs before calling `handler.process()`:

```python
async def _run_component(_handler=handler, **kwargs) -> str:
    kwargs = {k: v for k, v in kwargs.items() if v is not None}  # Align with adapter path
    payload = await _handler.process(kwargs, _ctx)
    return json.dumps({...})
```

**Component context (`comp_context`):** The convenience function `get_component_langchain_tools(profile_id, user_uuid, session_id)` builds the context dict passed to all component handlers. It includes:

| Key | Source | Purpose |
|-----|--------|---------|
| `session_id` | Parameter | Session isolation |
| `user_uuid` | Parameter | User isolation |
| `llm_callable` | Closure over `call_llm_api` | Enables components to make LLM calls (e.g., chart mapping Stage 4). Mirrors the MCP adapter's own `llm_callable`. Tokens automatically tracked via `call_llm_api` → `update_token_count()`. |

The `llm_callable` closure is only created when both `user_uuid` and `session_id` are provided. Components that don't need LLM assistance (most of them) simply ignore it.

#### Auto-Upgrade Pattern

When a non-tool profile (llm_only direct, rag_focused) has active component tools, the executor automatically routes through `ConversationAgentExecutor` instead of a direct LLM call. The check:

```python
manager = get_component_manager()
if manager.has_active_tool_components(profile_config):
    # Auto-upgrade: use ConversationAgentExecutor with component tools only
```

This is transparent to the user — the profile type doesn't change, but the execution path gains tool-calling capability.

### Admin Governance Settings

**File:** `src/trusted_data_agent/components/settings.py`

Mirrors `src/trusted_data_agent/extensions/settings.py`, plus a per-user override layer (`user_component_settings`).

**Settings stored in `component_settings` table:**

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `components_mode` | `"all"` \| `"selective"` | `"all"` | Whether all components available or admin selects |
| `disabled_components` | JSON array | `[]` | Component IDs disabled in selective mode |
| `user_components_enabled` | boolean | `true` | Allow importing custom components |
| `user_components_marketplace_enabled` | boolean | `true` | Enable marketplace browsing |

**Predicate functions:**

| Function | Purpose |
|----------|---------|
| `is_component_available(component_id, user_uuid=None)` | `False` if the id is in `disabled_components` (authoritative whatever the mode); else the per-user admin override for `user_uuid` when one exists; else `True` |
| `are_user_components_enabled()` | Whether custom import is allowed |
| `is_component_marketplace_enabled()` | Whether marketplace is accessible |
| `is_profile_scheduler_enabled()` / `is_platform_scheduler_enabled()` | The scheduler's two independent job gates (`profile_scheduler_enabled`, `platform_scheduler_enabled`) |

**Per-user overrides** (`schema/34_user_component_settings.sql`, table `user_component_settings`):
an admin can grant or block one component for one user independent of the global setting —
`get_user_component_override` / `set_user_component_override` / `delete_user_component_override`,
listed per component by `get_component_user_overrides` and `get_all_users_with_component_access`
(Administration → Components → the component's **Users** tab).

---

## Chart Component: Reference Implementation

**Directory:** `components/builtin/chart/` · full detail: [CHART_ARCHITECTURE.md](CHART_ARCHITECTURE.md)

The chart component is the first fully migrated component and serves as the reference implementation for the component library. It was extracted from three locations in the legacy codebase:

| Legacy Location | Extracted To |
|-----------------|-------------|
| `src/trusted_data_agent/mcp_adapter/adapter.py` (`_build_g2plot_spec()`) | `components/builtin/chart/handler.py` |
| `static/js/utils.js` (`renderChart()`) | `components/builtin/chart/renderer.js` |
| `trusted-data-agent-license/default_prompts/CHARTING_INSTRUCTIONS.json` | `components/builtin/chart/instructions.json` |
| `trusted-data-agent-license/default_prompts/G2PLOT_GUIDELINES.txt` | `components/builtin/chart/guidelines.txt` |

### ChartComponentHandler

**File:** `components/builtin/chart/handler.py`

```python
class ChartComponentHandler(BaseComponentHandler):
    component_id = "chart"
    tool_name = "TDA_Charting"
    is_deterministic = True

    async def process(self, arguments, context):
        # 1. Transform/normalize data (handle LLM hallucinations)
        # 2. Sort and limit data (_apply_data_filters)
        # 3. Validate arguments
        # 4. Run 5-stage mapping validation & repair pipeline
        # 5. Build G2Plot spec from validated mapping
        # 6. Propagate component LLM events in metadata
        # 7. Return ComponentRenderPayload with spec
```

**Data normalization** (`_transform_chart_data()`): Handles five LLM hallucination patterns:

| Pattern | Correction |
|---------|------------|
| Nested `{results: [...]}` wrapper | Flatten to top-level array |
| `{labels: [...], values: [...]}` | Convert to array of dicts |
| `{columns: [...], rows: [...]}` | Convert to array of dicts |
| `ColumnName` key (qlty_distinctCategories) | Rename to `SourceColumnName` |
| String numeric values | Coerce to float |

**Data filtering** (`_apply_data_filters()`): Handles `sort_by`, `sort_direction`, and `row_limit` arguments for "top N" / "bottom N" queries. Applied after data normalization but before mapping resolution.

#### 5-Stage Mapping Validation & Repair Pipeline

**Function:** `_resolve_and_validate_mapping()`

Every chart request passes through a 5-stage mapping pipeline that guarantees `_build_g2plot_spec()` always receives a valid mapping — eliminating LLM hallucination crashes (null values, wrong column names, swapped axes).

```
LLM Mapping → Stage 1 → Stage 2 → [Stage 3 → Stage 4 → Stage 5] → Valid Mapping
               Sanitize  Validate   Deterministic  LLM Repair  Positional
                                    Repair                     Fallback
```

| Stage | Function | When | What |
|-------|----------|------|------|
| **1. Sanitize** | `_sanitize_mapping()` | Always | Strip `None` values, empty strings, non-string values, unknown role keys, internal `_`-prefixed keys |
| **2. Validate** | `_validate_mapping()` | Always | Diagnose structural issues — missing required roles, columns not in data, numeric/categorical swaps |
| **3. Deterministic Repair** | `_repair_mapping_deterministic()` | If Stage 2 finds problems | Fuzzy-match column names (`_fuzzy_match_column`: exact → normalized → token-overlap Jaccard > 0.5 → unique substring) with **cardinality guard** — rejects constant-value columns for `x_axis`/`color` roles; swap axes when types mismatch; fill missing roles from column classification with cardinality-aware filtering |
| **4. LLM Repair** | `_repair_mapping_via_llm_wrapper()` | If Stage 3 insufficient + `llm_callable` available | Surgical LLM call for genuinely ambiguous columns; tokens tracked and piggybacked as Live Status events |
| **5. Positional Fallback** | `_positional_fallback()` | If all else fails | Guaranteed last resort — assign columns by position and type classification |

**Key design principle:** Stages 1-2 always run. Stages 3-5 only run if Stage 2 finds problems. The pipeline always returns a valid mapping — `_build_g2plot_spec()` never receives garbage input.

**Further detail lives in [CHART_ARCHITECTURE.md](CHART_ARCHITECTURE.md)** (§3 the handler pipeline,
§4 chart types): column classification (`_classify_columns`), the cardinality guard
(`_has_multiple_values`, used by Stage 3 fuzzy repair and by `_assign_roles`), heatmap role priority
(dual-axis before melt) and its categorical `meta`, the treemap flat-to-hierarchy transform
(`_build_treemap_hierarchy`), and the G2Plot spec builder (`_build_g2plot_spec`, whose role map is
`x_axis`/`category` → `xField`, `y_axis`/`value` → `yField`, `color` → `seriesField` (→ `colorField`
for pie, heatmap, treemap), `angle` → `angleField`). This section keeps only what makes chart the
reference implementation of the component contract.

**LLM event piggybacking (Stage 4):** When the LLM repair stage fires, it embeds a `_component_llm_events` list in the mapping metadata. This is propagated through `ComponentRenderPayload.metadata` and extracted by the consuming execution path (phase_executor for `tool_enabled`, conversation_agent for `conversation_with_tools`) to emit Live Status events and track tokens. See [Component LLM Event Piggybacking](#component-llm-event-piggybacking) below.

**Defense-in-depth guard:** `_build_g2plot_spec()` skips any mapping entry where the column name is `None` or non-string, logging a warning. This should never trigger (the pipeline guarantees clean input) but prevents cascading crashes if future code paths bypass the pipeline.

### Chart Renderer

**File:** `components/builtin/chart/renderer.js`

```javascript
export function renderChart(containerId, payload) {
    // 1. Parse spec from payload (JSON string or object)
    // 2. Validate spec.type, spec.options, G2Plot availability
    // 3. Instantiate G2Plot[spec.type](container, spec.options)
    // 4. Call plot.render()
}
```

**CDN dependency:** G2Plot loaded from `https://unpkg.com/@antv/g2plot@2.4.31/dist/g2plot.min.js` (declared in manifest, loaded by `ComponentRendererRegistry`).

---

## Frontend Architecture

### Component Renderer Registry

**File:** `static/js/componentRenderers.js`

Central registry mapping `component_id` → renderer function. Handles CDN dependency loading, renderer registration, and render dispatch.

**Internal state:**

| Variable | Type | Purpose |
|----------|------|---------|
| `_renderers` | `Map<string, Function>` | component_id → renderer function |
| `_loadedDeps` | `Set<string>` | Loaded CDN dependency URLs |
| `_loadingDeps` | `Map<string, Promise>` | In-flight CDN loads (dedup) |

**Pre-registered:** thirteen renderers are imported statically and registered at module load —
`chart`, `canvas`, `knowledge_graph`, `scheduler`, `flow`, `dashboard`, `memory`,
`database_navigator`, `action`, `obda_read`, `obda_model`, `explain_plan`, `files`:
```javascript
_renderers.set('chart', renderChart);   // … one line per pre-registered component
```
Headless components (`compute` — pre-rendered receipt HTML, `skill_library`, `find_tools`) have no
renderer; the system component's `renderContextWindow` is loaded by its own panel, not pre-registered.

**Exported functions:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `renderComponent` | `(componentId, containerId, payload)` | Parse payload → call renderer |
| `registerComponent` | `(componentId, renderer)` | Add renderer to registry |
| `hasRenderer` | `(componentId) → bool` | Check if renderer exists |
| `loadCDNDependency` | `(url, globalName) → Promise` | Load external JS with dedup |
| `registerFromManifest` | `(manifest) → Promise` | Load deps → dynamically import renderer → register |

**`renderComponent()` flow:**
1. Look up renderer in `_renderers` Map
2. If not found, display warning in container
3. Parse `payload` (JSON string → object if needed)
4. Call `renderer(containerId, payload)`
5. Catch errors and display in container with `text-red-400` styling

**`registerFromManifest()` flow** (for dynamic third-party components):
1. Load all CDN dependencies via `loadCDNDependency()`
2. Dynamically import renderer JS module
3. Extract renderer export by name
4. Register in `_renderers` Map

### Sub-Window Manager

**File:** `static/js/subWindowManager.js`

Manages persistent, resizable panels on the conversation canvas. Sub-windows stay open until explicitly closed by the user, even as new chat messages flow in.

**Internal state:**

`_openWindows`: `Map<string, {componentId, title, state, interactive, element}>`

**DOM structure:**

```
<div id="sub-window-container">               ← Container in index.html
  <div class="sub-window glass-panel" id="{windowId}">
    <div class="sub-window-header">
      <span class="sub-window-title">{title}</span>
      <div class="sub-window-controls">
        <button class="sw-minimize">─</button>
        <button class="sw-close">✕</button>
      </div>
    </div>
    <div class="sub-window-body" id="{windowId}-body">
      <!-- Component renderer fills this -->
    </div>
  </div>
</div>
```

**Exported functions:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `createSubWindow` | `(windowId, componentId, options) → HTMLElement` | Create and mount sub-window |
| `updateSubWindow` | `(windowId, payload)` | Update existing sub-window content |
| `closeSubWindow` | `(windowId)` | Remove sub-window from DOM and registry |
| `minimizeSubWindow` | `(windowId)` | Collapse to title bar only |
| `restoreSubWindow` | `(windowId)` | Restore from minimized state |
| `getSubWindowState` | `(windowId) → object` | Read current state (for context injection) |
| `getAllOpenWindows` | `() → Map` | All open windows (for bidirectional context) |
| `closeAllSubWindows` | `()` | Close all windows (session cleanup) |
| `getOpenWindowCount` | `() → number` | Count of open windows |

**Bidirectional context flow:**

When the user sends a new message, the query submission handler calls `getAllOpenWindows()` to collect the current state of all open sub-windows. This state is injected into the query payload as `sub_window_context`, allowing the LLM to reference and update existing sub-windows:

```
LLM → component tool call → sub-window created (illustrative: no builtin opens a sub-window today;
      the split panels above are the live persistent surface)
User edits code in sub-window
User sends next message → sub-window state injected into LLM context
LLM references/updates the sub-window by window_id
```

### Split-Panel Chrome (shared)

**Files:** `static/css/split-panels.css`, `static/js/handlers/splitPanelChrome.js`

The twelve right-side component **split panels** — canvas (`#canvas-split-panel`), knowledge graph, scheduler, dashboard, flow, agent memory, files, Database Navigator (`#dbnav-split-panel`), OBDA-ACT, OBDA-READ, OBDA-MODEL, EXPLAIN plan (`#explain-plan-split-panel`) — all share ONE header system. Each `<aside>` is statically defined in `templates/index.html`, carries `class="sp-panel"`, and gets its `--sp-accent` set from its component manifest `icon_accent` by `componentHandler.applyComponentPanelAccents()` at startup (the `COMPONENT_PANEL_ASIDE` map, one entry per panel — e.g. canvas `#06b6d4`, KG `#6366f1`, scheduler `#d946ef`, dashboard `#ec4899`, flow `#f59e0b`, memory `#14b8a6`). A single CSS variable drives the identity icon, switcher hover, and active states per panel. Activation/deactivation is uniform: open adds the panel's `--open` class (the only width driver — base `width:0`) + flips inline cards to "Expanded in side panel →" + sets header action buttons `.is-active`; close clears the resizer's inline width, animates the collapse, then `display:none` after `transitionend` (400 ms fallback) and resets the cards; every panel is registered in `splitPanelResizer.js` and closed on session switch. Renderers must NOT hardcode `--sp-accent` (manifest-driven) and must `JSON.parse` the string `data-spec` passed by `ui.js`.

**Canonical classes** (`split-panels.css`, linked after `main.css`):

| Class | Purpose |
|-------|---------|
| `.sp-panel` | Panel root; defaults `--sp-accent` |
| `.sp-header` | 44px header bar — `var(--bg-overlay)` bg, `var(--border-primary)` bottom border |
| `.sp-accent-icon` | Identity icon, coloured by `--sp-accent` |
| `.sp-title` | Static title (panels without a switcher, e.g. scheduler) |
| `.sp-switcher` + `.sp-switcher-name` | Instance-selector button (canvas/KG/dashboard/flow) |
| `.sp-action-btn` (28px) | Header action buttons; `.is-active` (accent), `.edit-active` (purple), `.active` (green) |
| `.sp-divider` | Vertical separator |
| `.sp-subtoolbar` | Optional secondary row (canvas editor controls) |
| `.sp-switcher-menu` / `.sp-switcher-item` (`.is-current`/`-dot`/`-name`/`-meta`) / `.sp-switcher-new` | Portal dropdown |

Accent tints use `color-mix(in srgb, var(--sp-accent) N%, transparent)`. All other colours are theme variables (dark + light); icons are monochrome SVG.

**Bar icons — one language** (`static/js/handlers/panelIcons.js`, gate `test/test_panel_icons.mjs`). Every icon in a panel's header and menu bar (`[data-sp-subheader]`) follows four rules:

- **One function, one glyph.** Copy, Download, Close, the database connection and the Database Navigator are drawn from ONE shared set (`window.PanelIcons`, loaded by main.js before any panel can render; renderers read it through `_picon(k)` because a static `/static` import would break the Node DOM-stub tests, which install the same module). A renderer keeps no private copy of a shared glyph.
- **One glyph, one function.** Two different acts never share a drawing: a database **connection** is the plain cylinder; the **Database Navigator** is the catalog with a lens (its manifest icon and panel header, and "Describe in navigator" in the SQL canvas).
- **Industrial style.** Monochrome line drawings, `stroke="currentColor"`, no fill colour and no hex colour in any panel renderer's icons — the connection picker's per-driver colours were removed (the item names its driver in words).
- **One size, one stroke, one shape.** `split-panels.css` sizes every bar icon (`--sp-bar-icon: 16px`, stroke 2; the switcher caret and dropdown check marks excepted). Menu-bar controls share the PCS metrics (`--ctl-height` 28px, `--ctl-radius`); badges are `.pc-badge` (one pill shape); a fullscreen button is named "Fullscreen". The global `button[aria-label]` rule in `themes.css` is written as `button:where(...)` (element specificity), because at (0,1,1) it outranked every component class and squared pills and buttons that carried an aria-label.

**Panel sequence (drag to reorder)** — `static/js/handlers/splitPanelReorder.js` (loaded after `splitPanelResizer.js`). Every panel of the row — the conversation column included — can be moved: press anywhere on its `.sp-header`, drag, release on the accent bar that marks the landing slot (Escape cancels); or press `Alt+Shift+Left/Right` while focus is on a control in the header. Rules:

- **The sequence is CSS `order`, never a DOM move.** Re-inserting a panel resets the chat's scroll position, reloads a canvas preview iframe and re-mounts live canvases; with `order` the DOM is untouched. The **default sequence sets no `order` and stores nothing**, so the row is byte-identical to one without the module.
- **The whole header is the handle** — title and buttons included, as a browser tab is both clickable and draggable: a press that does not travel (6 px) stays that control's click; one that travels is a drag and its click is swallowed. Text-entry fields are exempt.
- **One visibility rule.** The module asks `SplitPanelResizer.visiblePanels()` (visible panels in visual order: `order`, then DOM position); the resizer places each resize handle before its right-hand panel with that panel's `order`, and treats the conversation as the flex filler on **either** side of a handle (it is never given a width).
- **Hidden panels keep their slot** and appear there when opened. The two ends of the row are the ends of the whole sequence (a panel dragged to the far right stays rightmost when another panel opens).
- Per-viewer, in `localStorage` (`uderia_split_order_v1`), reconciled at load (a vanished id is dropped, a new panel appended). No drag while a panel is fullscreen or when only one panel is visible. API: `window.SplitPanelOrder.{get,set,move,reset}`.
- A panel is structural (`#chat-canvas-split > .sp-panel`) — a new component panel is reorderable with no registration. Known limit: keyboard tab order follows the DOM, not the visual sequence.

Gate: `node test/test_split_panel_reorder.mjs` (in the battery).

**Instance switcher helper** — `mountSplitPanelSwitcher(opts)` (exposed on `window` for renderer modules, which can't easily import static handlers):

```
mountSplitPanelSwitcher({
  btnEl,            // the .sp-switcher button
  listUrl, itemsKey,// REST list endpoint + JSON array key
  idField|idFn, labelField|labelFn,
  metaField, metaSuffix,
  getCurrentId,     // () => current id (highlight + no-op reselect)
  onSelect,         // async (id, item) => switch the open panel
  items,            // optional () => array held in the browser (no fetch)
  newLabel, onNew,  // optional "create new" footer
  newPrompt,        // with it the footer becomes an inline name field; onNew(name)
  onRename,         // async (id, name, item) — "Rename" for the OPEN instance
}) => unmount()
```

`onNew` / `onRename` may return a **string** — a refusal (a name in use, a failed save) shown under the field, which stays open so the name can be corrected (`role="alert"`). Which panels offer what: **canvas** New (inline name) + Rename; **flow** New (`POST /v1/flows {name}`) + Rename; **dashboard** New + Rename (the old modal prompt is gone); **knowledge graph** Rename (graphs with a `kg_id`) + *New knowledge graph…*, which routes to Intelligence → Knowledge Graphs and opens its constructor menu (a graph is built by a constructor, never named into being empty). No switcher, so no New/Rename: scheduler (lists all tasks), memory, Database Navigator (its rows are sources), the EXPLAIN plan panel (a history of plans). Gate: `test/test_panel_new_rename.{py,mjs}`.

It fetches the list, renders a `position:fixed` portal menu under the button (copies `--sp-accent` onto the menu since it lives on `<body>`), highlights the current item, dismisses on outside-click/Esc/scroll, and calls `onSelect`. Per panel: canvas (`/v1/canvases` → re-render via `renderCanvas`), KG (`/v1/knowledge-graph/list` → graph-spec by `profile_id` → `openKGSplitPanel`), dashboard (`/v1/dashboards` → `_loadAndRenderDashboard`), flow (`/v1/flows` → re-open via `openFlowSplitPanel`; the in-canvas React `.flow-selector` is hidden). Scheduler has no switcher (its body lists all tasks).

**Canvas exception:** canvas historically suppressed the standard header and rendered an editor-style `.canvas-header` inside the body. It now shows the standard `.sp-header` (accent code-icon + `#canvas-switcher-btn` + fullscreen + close); `renderCanvasFull` skips the in-body icon/title and the toolbar fullscreen/close injection when `containerId` starts with `canvas-split-`, leaving the editor controls (tabs + Code/Changes/run/History/copy/download) as the secondary row. `updateFullscreenButtonIcon` targets `#canvas-split-fullscreen` and toggles `.is-active`.

> **Note:** `templates/index.html` header edits require a server restart (Jinja caches the template at startup). `renderer.js` (served no-cache) and `static/css|js` changes are picked up on browser reload.

### "Pin to dashboard" on a component row

A component whose manifest declares `"answer_surface": {"pin": true}` (chart, knowledge graph, flow, EXPLAIN plan) gets the row footer act **Pin to dashboard** from `answer_surface.component_row` (`pin_footer`) — the same footer and button as a data row's table, revealed by the client only when the Dashboard component is enabled for the agent, handled by one delegated click (`ui.js:_onPinAct`). A payload may withhold it for one action with `metadata.answer_surface.pin: false`. There is no floating hover pin over cards any more. Detail: [DASHBOARD_ARCHITECTURE.md §5](DASHBOARD_ARCHITECTURE.md).

### Open-in-Panel Control (shared)

Every component card in the conversation opens its side panel through ONE control, `static/js/handlers/panelOpenControl.js` (`window.PanelOpenControl`, loaded by `main.js`, styles `.sp-open-row` / `.sp-open-ctl` in `split-panels.css`). The card renders the markup, the panel reports what it shows, and every button follows that one record:

```javascript
PanelOpenControl.html({ component, id, accent, panelId, name, row }) // the button (or a footer row with it)
PanelOpenControl.shown(component, id)    // the panel: open / switched to `id`; null = closed
// a renderer registers its acts through the queue (it may load before this module):
(window.__panelOpenQueue ||= []).push([component, { open(id, btn), close(id, btn) }]);
window.PanelOpenControl && window.PanelOpenControl.drain();
```

- **The rule.** A button reads *Expanded in side panel →* exactly when the panel shows THIS card's item; clicking it then closes the panel. Any other card's button opens or switches the panel to its own item — it never closes another card's item. Single-panel components (scheduler, memory, Database Navigator) use the id `'*'`: any open panel is theirs.
- **One record.** Each panel calls `shown()` where it opens, switches item and closes (`autoPopOutCanvas` / `closeSplitPanel`, `openFlowSplitPanel` / `closeFlowSplitPanel`, `openDashboardPanel` / `closeDashboardPanel`, `openKGSplitPanel` / `closeKGSplitPanel`, …), so the switcher, auto-open and session restore keep every card right, and two cards can never both read *Expanded*. It also dispatches `uderia:panel-state` (`{component, id}`).
- **One click path.** Clicks are delegated from `document`, so a card rendered as static HTML works without wiring — the server-rendered flow-run card (`flow_executor._render_flow_run_card_html`) emits the markup directly; a flow-run card persisted before the control existed (`.flow-run-open-btn`) is replaced on insertion (`ui.js:_upgradeLegacyOpenButtons`). A click on a card whose renderer is not loaded yet imports it first.
- **One look, accessible.** A real `<button>` in the component's own accent (the live manifest accent, `window.__componentAccents`, else the renderer's fallback), `aria-expanded`, `aria-controls` = the panel, an `aria-label` naming the item, a focus ring, reduced-motion respected; in light theme the label text is the accent darkened. A card's preview (the plan graph, the flow diagram on a dashboard tile) may forward its click to the control — the same gesture, never a second one.
- **Item identity.** canvas `canvas_id` (else `title:<title>`), flow `flow_id`, dashboard `id` (card and every row of the dashboard list), knowledge graph `kg_id` (else `title:<title>`), EXPLAIN plan a hash of the plan.
- **Auto-open never fires during a session reload** (`window.__suppressComponentReplayOpen`) — canvas, knowledge graph, dashboard, scheduler, memory, Database Navigator.

Not the open control, and left as they are: the Database Navigator card's *Open in canvas →* (opens the SQL canvas — a different act, its own `.dbnav-canvas-btn`), the canvas compact viewer's icon button (split mode off), and a dashboard visualization card's header icon. Gate: `test/test_panel_open_control.mjs` (rule, click, accessibility, every renderer on the control and reporting open/close, the flow-run card).

### Workspace Panel

**File:** `static/js/handlers/workspacePanelHandler.js`

A resource-rail panel ("Work", briefcase icon, `#workspace-panel`) that lists the persistent **instances** a user created with action components, grouped into collapsible `<details>` sections (collapsed by default), one per component **enabled for the active profile**. Twelve sections (`WORKSPACE_REGISTRY`): dashboard, flow, canvas, scheduler, knowledge graph, memory, Database Navigator, EXPLAIN plan, OBDA-ACT, files, OBDA-READ, OBDA-MODEL. The rail badge reads `items · sections` (the sum of the sections' own totals, then the section count — `resourceBadge.workspaceLabel`).

- **Gating:** a section appears only if its component's `profile_enabled` is true (`GET /v1/profiles/<id>/components`, the full governance merge). Within a section, the rows are scoped by the component's `instances` declaration (below): a `user` component lists all the user's instances whichever agent is shown ("profile-gate, show all"); an `agent` component lists only the active agent's. The rail item hides when no instance-bearing component is enabled.
- **Registry:** `WORKSPACE_REGISTRY` keyed by `component_id` holds `{listUrl, itemsKey, idField, labelField, meta(), deleteUrl(), open(item, profileId)}` — adding a new instance-bearing component is one entry. Chart is excluded (ephemeral); Context Window is the existing Context panel.
- **A component with no instances still belongs here when what it opens ON is a list.** The Database Navigator's rows are its catalog *sources* (one per source, the resolved default marked active, open-only), and an optional `trailer(items, profileId)` hook appends uncounted sub-rows (its recently-viewed objects). This is how the navigator reaches the resource panel — it has no rail tab of its own (the rail is deliberately not widened per component).
- **Launcher, not editor:** each row's **Open** delegates to the component's own split panel via `import('/api/v1/components/<id>/renderer')` (`openDashboardPanel`/`openFlowSplitPanel`/`renderCanvas`/`openSchedulerSplitPanel`); **Delete** is an inline two-step confirm.
- **"+" (add new) on every section that can create something** — declared per registry entry as `create(profileId, name?)` (+ optional `createTitle`, the button's tooltip and label), and always routed through the component's OWN create path, never a second one:

  | Section | "+" does |
  |---|---|
  | Dashboard | inline name field (`createPrompt`) → `POST /v1/dashboards` → opens it |
  | Flow | the topology picker (`openTopologyPicker`) |
  | Canvas | inline title field → `POST /v1/canvases` (`mode:create`, SQL) → opens it; a taken title is said under the field |
  | Task Scheduler | starts *"Schedule a task that …"* in the composer (tasks are created by the agent via `TDA_Scheduler`; a draft already there is never overwritten) |
  | Knowledge Graph | the graph builders (`openKgConstructors`, the panel switcher's own route) |
  | Agent Memory | this agent's memory panel, add field focused |
  | Database Navigator | the Database Connection connector (`openPlatformConnector('uderia-database')`) |
  | Files | the Files panel, add opened |
  | OBDA-ACT | the action designer (`actionDesigner.js`) with the form prepared |
  | OBDA-READ | the Products Designer on the agent's graph |
  | OBDA-MODEL | Govern on the agent's graph |
  | EXPLAIN Plan | **no "+"** — a plan is derived from a statement; the slot is reserved so the counts stay aligned |

  An entry with `createPrompt` gets an inline field at the top of its section (opened if collapsed): Enter creates (`create` returns `true`, or a sentence shown under the field), Escape or an empty name cancels. Gate: `test/test_workspace_new_action.mjs`.
- **One icon vocabulary across every section.** The same act wears the same icon and the same words everywhere, and appears only where the component can perform it (one rule, `_canImport` / `_canExport` / `_canDelete` / `_titles`):

  | Section | + | Import | Export | Open | Delete |
  |---|---|---|---|---|---|
  | Dashboard · Flow · Canvas | ✓ | instance envelope | instance envelope | ✓ | ✓ |
  | Task Scheduler | ✓ (agent) | — | — | ✓ | ✓ |
  | Knowledge Graph | ✓ (builders) | the graph's own import (JSON / RDF) | the graph's own export menu | ✓ | only a graph this agent **owns** (not one assigned to it, not a subscribed copy — that is unsubscribed in the Knowledge Graphs tab) |
  | Agent Memory | ✓ (add) | — | — | ✓ | — (items are deleted inside the panel) |
  | Database Navigator | ✓ (connection) | — | — | ✓ | — (sources are configured elsewhere) |
  | Files | ✓ (add) | — | download (`exportItem`) | ✓ | ✓ |
  | OBDA-ACT · OBDA-READ · OBDA-MODEL | ✓ (designer / Govern) | — | — | ✓ | — (`noDelete`) |
  | EXPLAIN Plan | — | — | — | ✓ | — |

  Words: `New <thing>` (or the entry's `createTitle`) · `Import a/an <thing>` · `Export this <thing>` · `Open this <thing>` (or `openTitle`) · `Delete this <thing>`, with `<thing>` the entry's `noun`; a row button's accessible name adds the item's label. A component with its OWN import/export supplies `importNew(btn, onDone)` / `exportItem(item, btn)` instead of the instance envelope — the knowledge graph's are `startKgImport` / `openKgExportMenu` from `knowledgeGraphPanelHandler.js`, the same flow as the Knowledge Graphs tab's Import button. Not offered, by design: import/export for scheduled tasks and memory (neither has a portable format today).
- **Identity:** section icon/accent come from each component's manifest `icon_accent` (same colours as the split-panel `--sp-accent`).
- **Whose rows (`instances`):** EVERY component manifest declares whose the things it keeps are (the schema requires it) — `user` (the user's own: dashboard, flow, canvas, scheduler, files — listed whichever agent is shown), `agent` (the active agent's own: knowledge graph, memory, database navigator, action, OBDA-READ, OBDA-MODEL), `page` (held in the open page, not kept: EXPLAIN Plan), `none` (keeps nothing: chart, compute, context window, find tools, skill library). The components that keep something are exactly the Workspace's sections. The loader hands a section's `list` / `listUrl` / `filterItems` the profile id ONLY for `agent` (`const scope = comp.instances === 'agent' ? profileId : null`); the component card and its detail header show the same field as a pill (*Per User* · *Per Agent* · *Per Page* · *Stateless*, the sentence on hover). ONE declaration (schema enum = `models.INSTANCE_SCOPES` = the pills), served by `to_api_dict()`. Gate: `test/test_component_instances.mjs` — every component declares, the keeping set equals the Workspace's sections, and each section's list narrows to an agent exactly when it says `agent`.
- **Lifecycle:** loads on tab activation (`eventHandlers.js` `handleResourceTabClick`) and on profile change (`main.js` `updateResourcePanelForProfile`), mirroring the KG/Context panels.

### The Components View

**File:** `static/js/handlers/componentHandler.js`

The **Components** view (sidebar → *Faculties* → **Components**) renders the component library — a card grid with filters for browsing, enabling, and managing components — on its **User Interface** sub-tab; the **Connectors** sub-tab holds the platform connectors (`platformConnectorHandler.js`). Platform-wide governance lives separately in **Administration → Components**.

**Sub-tabs of the Components view:**
```
User Interface | Connectors
^^^^^^^^^^^^^^
```

**Exported functions:**

| Function | Purpose |
|----------|---------|
| `loadComponentsView()` / `loadComponents()` | Fetch `GET /v1/components`, render card grid, apply governance |
| `openComponentDetail(componentId, {activateTabId})` | Open one component's detail view (its tabs, e.g. Flows) |
| `applyComponentPanelAccents()` | Set every split panel's `--sp-accent` from its manifest `icon_accent` |

**Card rendering** (`_renderComponentCard()`):

Each component renders as a glass-panel card with:
- Component name and version
- Source badge (built-in → violet, agent_pack → cyan, user → amber)
- Type badge (action / system)
- The keeps pill from `instances` (*Per User* · *Per Agent* · *Per Page* · *Stateless*)
- Description text
- Enable/disable toggle
- Intensity selector (if applicable)

The detail view's capabilities block (`_renderCapabilities()`) adds render targets, `enabled_for` and the signals (*Emits · Follows · Announces · Reads by reference*).

**Data attributes on cards:** `data-component-type`, `data-component-source`, `data-component-id`

**Filter system** (`_setupFilterPills()`): Category and status filter pills with `.filter-pill` / `.filter-pill--active` CSS classes.

**Governance enforcement:** When `_componentSettings.user_components_enabled === false`, the Import button is hidden.

---

## Integration Points

### Prompt Injection Pipeline

**Files:** `src/trusted_data_agent/llm/handler.py` → `_get_full_system_prompt()`; `src/trusted_data_agent/components/manager.py` → `get_component_instructions_for_prompt()`

When building the system prompt, the LLM handler calls the module-level
`get_component_instructions_for_prompt(active_profile_id, user_uuid, session_data)`, which resolves the
profile dict (`config_manager.get_profile`), calls `ComponentManager.get_instructions_text()`, wraps the
result under `- **Component Guidelines:**`, and the handler substitutes it for
`{component_instructions_section}`. The direct engines (Ideate, Focus) use the same function, or the
text pre-computed by the context-window `component_instructions` module (`executor._cw_component_instructions`).

**Profile config resolution**: the profile dict comes from `config_manager.get_profile(active_profile_id, user_uuid)`, not from `session_data` (which never carries `profile_config`), so per-profile component filtering works correctly.

`ComponentManager.get_instructions_text()`:

1. Filters components by profile config (`get_active_components()` — the one enablement rule)
2. Skips an action component whose tool is not offered (`offers_tool` — e.g. `files` without `agentTool`)
3. Loads each component's instructions at the profile's intensity level
4. Resolves `[[gate:<key>]]` blocks (`apply_instruction_gates`) and joins the blocks

The *tool catalog* (not the component instructions) is what condenses to names-only after the first
turn (`full_context_sent` + `APP_CONFIG.CONDENSE_SYSTEMPROMPT_HISTORY` in `_get_full_system_prompt`).

### Tool Call Routing

Component tools are routed differently depending on the profile type:

#### tool_enabled Path (Planner/Executor)

**File:** `src/trusted_data_agent/mcp_adapter/adapter.py` → `_invoke_mcp_tool_impl()`

When the MCP adapter receives a tool call, it checks if the tool belongs to a component, runs the effect-site gate (`enablement.enforce_component_call` — a disabled component's call is refused with a recovery sentence), and dispatches:

```python
if comp_manager.is_component_tool(tool_name):
    handler = comp_manager.get_handler(tool_name)
    payload = await handler.process(arguments)
    return component_result
```

This intercepts component tool calls before they reach MCP server dispatch. The handler returns a `ComponentRenderPayload` which is formatted into the tool result.

#### llm_only + tools Path (Conversation Agent)

**File:** `src/trusted_data_agent/agent/engines/conversation_engine.py` → `run()`

Component LangChain tools are merged with MCP tools:

```python
component_tools = get_component_langchain_tools(executor.active_profile_id, executor.user_uuid, session_id=executor.session_id, ...)
all_tools = mcp_tools + component_tools  # MCP tools optional when components present
agent = ConversationAgentExecutor(..., mcp_tools=all_tools, ...)
```

MCP server is no longer required when component tools are present — a profile with only component tools can still use the conversation agent path.

**Component LLM event & token extraction** (`conversation_agent.py:on_tool_end`): When a component tool completes, the conversation agent extracts piggybacked events and tokens from the payload metadata — mirroring the extraction that `PhaseExecutor._execute_tool` performs for the `tool_enabled` path:

```python
_payload = extract_component_payload(tool_output)
if _payload:
    _comp_meta = _payload.get("metadata") or {}
    # 1. Emit piggybacked Live Status events
    _comp_events = _comp_meta.pop("_component_llm_events", None)
    if _comp_events:
        for evt in _comp_events:
            await self._emit_event("component_llm_resolution", evt)
    # 2. Accumulate component LLM tokens
    self._component_llm_input_tokens += _comp_meta.get("llm_input_tokens", 0)
    self._component_llm_output_tokens += _comp_meta.get("llm_output_tokens", 0)
```

The accumulated component tokens are returned alongside LangChain tokens in the agent result dict (`component_llm_input_tokens`, `component_llm_output_tokens`). The executor combines them for turn counters, cost calculation, and the `token_update` SSE — but does NOT double-count in the session DB (component tokens are already persisted by `call_llm_api` inside the handler). See [Component LLM Event Piggybacking](#component-llm-event-piggybacking) below.

#### Provider-Agnostic Tool Schemas (dynamic-key object args)

**File:** `src/trusted_data_agent/components/manager.py` — `_build_args_schema()`, `_decode_json_string_args()`

Some component tools take arguments whose keys are **data-dependent** and therefore cannot be enumerated in a closed JSON Schema — e.g. `TDA_Charting`'s `mapping` (`{"x_axis": ..., "y_axis": ...}`, roles vary by chart type) and `data` (rows with arbitrary column names). Manifests declare these as `dict` / `list[dict]`.

The natural JSON Schema for these is an open object (`type: object` + `additionalProperties: true`). That is spec-compliant and accepted by OpenAI, Anthropic, Google, and most OpenRouter backends — but **strict provider validators (Groq, Cerebras, OpenAI strict mode) ignore `additionalProperties` and reject every key the model produces**, since none are enumerated under `properties`. With OpenRouter (a broker that forwards open-weights models like Llama/Qwen to whichever upstream host is cheapest/fastest), a request can silently land on Groq and fail with `additionalProperties '...' not allowed` even though the user only selected "OpenRouter".

**Root-cause fix:** dynamic-key object args (`_OPEN_OBJECT_ARG_TYPES = {"dict", "list[dict]", "object"}`) are advertised in the tool schema as **JSON-encoded strings** — a `type: string` node every validator accepts:

- `_build_args_schema()` types these fields as `Annotated[str, BeforeValidator(_coerce_open_object_to_json_str)]`. The emitted schema is a plain `type: string` (no open object for strict validators to reject), and the before-validator coerces a native `dict`/`list` to its JSON string form so a lenient provider that ignores the schema never trips Pydantic validation. The field names are recorded on the model as `__json_string_fields__`. (Scalar aliases `integer`/`number`/`boolean` are also mapped so e.g. `row_limit` emits `type: integer` rather than an empty `{}`, which strict validators also dislike.)
- `_decode_json_string_args()` (called in `get_component_langchain_tools._run_component`) decodes those JSON strings back into native `dict`/`list` **before the handler runs** — so handlers are completely unchanged.

This works on **every** provider including Groq *directly* (verified by forcing OpenRouter routing to Groq with `provider: {order:["Groq"], allow_fallbacks:false}` — the previously-failing tool call returns HTTP 200). Connector tools (`conversation_engine._load_connector_tools_for_langchain`) already coerce every non-scalar type to `str`, so they never emit open objects.

**The other half of the vocabulary — typed lists (Gemini's `items` requirement, Aug 2026).** A manifest arg declared as bare `"list"` maps to an **items-less `array`** schema node, which Google's validator rejects outright (`parameters.properties[<arg>].items: missing field` — this killed every Ideate+KG turn on Gemini, session 228cae64). The closed vocabulary is therefore:

| Manifest type | Emitted schema | Why |
|---|---|---|
| `list[str]` | `array` with `items: {type: string}` | typed items — what Gemini requires |
| `list[dict]` / `dict` | `type: string` (JSON-string route above) | no open object for Groq, no items-less array for Gemini |
| bare `list` / `array` | **banned in builtin manifests** | kept only as a legacy mapping for third-party manifests; emits the items-less array some providers refuse |

Gate: `test/test_component_tool_schema.py` (in the trust battery) — bans bare-list args across every builtin manifest, asserts the KG tool's real per-arg schema shapes, drives the actual `convert_to_genai_function_declarations` call that failed live, and round-trips the JSON-string decode.

#### Conditional Instruction Blocks (`[[gate:<key>]]`)

**File:** `src/trusted_data_agent/components/manager.py` — `apply_instruction_gates()`, `_instruction_gate_open()`, `_gated_tool_definition()`

A component's instruction surfaces can carry paragraphs that must only be shown when a platform-level condition holds — e.g. the KG component's OBDA-READ guidance ("prefer `query_product` over hand-written SQL") must vanish entirely when the OBDA-READ plane is not enabled for the agent (the `obda` gate key reads the OBDA-READ component through `core/obda_planes.plane_enabled` — the planes are components since Oct 2026, there is no master switch), or the planner is sold a door the runtime then refuses (found live: a plan whose goals quoted "signed, deterministic contract" while the plane was off). Wrapping such content in `[[gate:obda]] … [[/gate]]` makes the condition declarative:

- Resolved at **three seams** so every LLM-facing surface honours it: `get_instructions_text()` (instructions.json), `get_tactical_prompt_rules()`, and `_gated_tool_definition()` — the last walks the manifest tool **description AND every arg description**, so gated arg docs (e.g. `product_name`) disappear with the feature.
- An **unknown gate key keeps the content and warns** (fail-visible): silently dropping instructions on a typo would be a hidden capability cut.
- Adding a gate key is one entry in the resolver's key→predicate map; the markers never reach a model.

#### Leaked Tool-Call Recovery (weak-model robustness)

**File:** `src/trusted_data_agent/agent/conversation_agent.py` — `_recover_leaked_component_calls()`, `_find_tool_call_blocks()`

Weaker models (e.g. Llama-4-Scout) sometimes, on a follow-up turn, emit a component call as **plain-text JSON in the answer** (`{"tool_name": "TDA_Charting", "arguments": {...}}`) instead of invoking the function-calling mechanism. The existing `_strip_leaked_tool_calls` (in `conversation_engine.py`) assumes the tool already ran and removes the JSON as noise — but when the model *never actually invoked* the tool, that left a blank turn (and an empty `[ ]` shell) with no rendered component.

**Root-cause fix (platform layer):** after the agent loop, `_recover_leaked_component_calls()` scans the answer for balanced `{...}` blocks containing `"tool_name"`. For each block naming a bound component tool (`TDA_*`) that did **not** run this turn (not in `tools_used`), it invokes the tool for real via `tool.ainvoke(args)`, extracts the payload with `extract_component_payload()`, appends it to `component_payloads`, and strips the JSON from the text. Native-object args round-trip cleanly through the JSON-string schema's before-validator/decoder. This mirrors the existing `_auto_canvas_code_blocks` recovery (leaked code block → Canvas render) — the platform never silently drops a renderable component the model asked for.

Verified: a 2-turn flow (bar chart → "show me a pie chart instead") where turn 2 leaks the call as text now recovers it and renders the pie chart (log: `Recovered leaked component call: TDA_Charting`).

#### llm_only Direct Path (Auto-Upgrade)

**File:** `src/trusted_data_agent/agent/executor.py` → `PlanExecutor.run()` (dispatch), `agent/engines/ideate_engine.py` (claims only pure `llm_only`)

Pure conversation profiles (no MCP tools) automatically upgrade to agent execution when component tools are active:

```python
has_component_tools = _comp_mgr.has_active_tool_components(profile_config, self.user_uuid)
is_llm_only = (profile_type == "llm_only" and not use_mcp_tools and not has_component_tools)
is_conversation_with_tools = (profile_type == "llm_only" and (use_mcp_tools or has_component_tools))
```

When `has_component_tools` is True, the profile routes through `ConversationAgentExecutor` with only component tools (no MCP, no system tools).

#### rag_focused Path (Auto-Upgrade)

**File:** `src/trusted_data_agent/agent/engines/focus_engine.py`

After RAG retrieval builds knowledge context, if component tools are active, synthesis uses an agent instead of direct LLM:

```python
rag_component_tools = get_component_langchain_tools(executor.active_profile_id, executor.user_uuid, session_id=executor.session_id)
if rag_component_tools:
    # Agent-based synthesis with component tools + RAG knowledge context
    rag_agent = ConversationAgentExecutor(..., mcp_tools=rag_component_tools, ...)
else:
    # Standard direct synthesis (no tools)
```

#### genie Path (Coordinator)

**File:** `src/trusted_data_agent/agent/genie_coordinator.py` (`GenieCoordinator.__init__`)

Component tools are merged with the coordinator's invoke_* profile tools:

```python
component_tools = get_component_langchain_tools(self.genie_profile.get("id"), self.user_uuid,
                                                session_id=parent_session_id) if self.tools_enabled else []
if component_tools:
    self.tools.extend(component_tools)
    self.component_tool_names = {t.name for t in component_tools}
```

The coordinator can then call component tools (e.g., TDA_Charting) when synthesizing results from sub-profiles. Component tool invocations emit explicit Live Status events:

| Event | When | Payload |
|-------|------|---------|
| `genie_component_invoked` | `on_tool_start` when tool is in `component_tool_names` | `{tool_name, session_id}` |
| `genie_component_completed` | `on_tool_end` when payload extraction succeeds | `{tool_name, component_id, success, session_id}` |

These events are routed through `eventHandlers.js` → `genieHandler.js` and rendered as dedicated cards in the Live Status panel. Payload extraction uses the shared `extract_component_payload()` utility; HTML generation uses `generate_component_html()` — both from `components/utils.py`.

### Component Fast-Path

**File:** `src/trusted_data_agent/agent/phase_executor.py` → `_execute_standard_phase()` (the `COMPONENT FAST-PATH` block)

Deterministic components bypass the tactical LLM entirely during phase execution (`tool_enabled` path only). This is the most significant performance optimization — saving an entire LLM round-trip per component invocation.

```
# --- COMPONENT FAST-PATH: Deterministic component handlers (zero LLM calls) ---
if comp_manager.is_component_tool(tool_name):
    if comp_handler.is_deterministic:
        # Chart-specific: resolve data from collected_data, delegate mapping to the handler
        ...
        # Generic deterministic (canvas, etc.): build an action from the strategic args
        # and execute via the shared path
# --- END COMPONENT FAST-PATH ---
```

The chart-specific fast-path:
1. Resolves data from `collected_data` (previous phase results)
2. Passes `mapping = {}` — delegates mapping entirely to the handler's 5-stage pipeline
3. Executes the handler directly with resolved arguments
4. Skips tactical LLM entirely → saves ~3,000 tokens per chart

> **Design note (Feb 2026):** The fast-path previously called `_generate_charting_mapping()` to pre-generate field mappings based on value-type classification (string/numeric/date). This was removed because the naive value-type-only logic could not distinguish constant-value metadata columns from variable category columns, producing "close but wrong" mappings that poisoned the handler's fuzzy repair stage. The handler's 5-stage pipeline — with cardinality-aware `_assign_roles()` and semantic column name pattern matching — is strictly superior. The deprecated `_generate_charting_mapping()` has since been deleted from `phase_executor.py`.

The `_component_bypass_handled` flag pattern ensures graceful fallback — if the fast-path fails for any reason, execution falls through to the standard tactical LLM path.

### DOM Rendering

**File:** `static/js/ui.js` → `addMessage()`

When a message is added to the chat log, `ui.js` scans for component containers:

```javascript
import { renderComponent, hasRenderer } from './componentRenderers.js';

// Generalized component rendering
const componentContainers = messageContent.querySelectorAll('[data-component-id]');
componentContainers.forEach(container => {
    const componentId = container.dataset.componentId;
    const spec = container.dataset.spec;
    if (componentId && spec && hasRenderer(componentId)) {
        renderComponent(componentId, container.id, spec);
    }
});

// Legacy fallback for .chart-render-target without data-component-id
const chartContainers = messageContent.querySelectorAll(
    '.chart-render-target:not([data-component-id])'
);
chartContainers.forEach(container => {
    if (container.dataset.spec) {
        renderChart(container.id, container.dataset.spec);
    }
});
```

The shared utility `generate_component_html()` (`components/utils.py`) embeds component output as:
```html
<div class="response-card mb-4">
  <div id="component-{uuid12}" data-component-id="chart"
       data-spec='{"type":"Column","options":{...}}'></div>
</div>
```

All profile types (llm_only, rag_focused, genie) call this single function — there is no profile-specific HTML generation logic.

### SSE Event Handling

#### Inline Components

Inline components (`render_target: "inline"`) are embedded in the `final_answer` HTML and rendered during `UI.addMessage()` — no dedicated SSE event required.

#### Sub-Window Components

**File:** `static/js/eventHandlers.js` → `processStream()` (the `component_render` branch)

Sub-window components are delivered via SSE `component_render` events during streaming:

```javascript
// Imports
import { renderComponent, hasRenderer } from './componentRenderers.js';
import { createSubWindow, updateSubWindow, closeSubWindow } from './subWindowManager.js';

// Event handler in processStream()
} else if (eventName === 'component_render') {
    const { component_id, window_id, action, spec, title, render_target } = eventData;

    if (render_target === 'sub_window') {
        if (action === 'close')       → closeSubWindow(window_id)
        else if (action === 'update') → updateSubWindow(window_id, spec)
        else                          → createSubWindow() + renderComponent()
    }

    // Update Live Status panel
    UI.updateStatusWindow({
        step: `Component: ${title || component_id}`,
        details: { component_id, render_target, action },
        type: 'component_render'
    });
}
```

**SSE event payload schema:**
```json
{
  "component_id": "chart",
  "window_id": "sw-abc123",
  "action": "create",
  "spec": { "type": "Column", "options": { ... } },
  "title": "Revenue by Quarter",
  "render_target": "sub_window"
}
```

#### Genie Component Events

**File:** `static/js/eventHandlers.js` (`genie_component_*` routing), `static/js/ui.js` (`_renderGenieStep`)

When the genie coordinator calls component tools directly (not through a child profile), it emits dedicated Live Status events:

| SSE Event | Trigger | Live Status Card |
|-----------|---------|------------------|
| `genie_component_invoked` | `on_tool_start` for component tool | "Component: TDA_Charting" (amber, active) |
| `genie_component_completed` | `on_tool_end` with successful payload extraction | "Component Complete: chart" (green, completed) |

These events are routed through the genie event handler path (`eventHandlers.js` → `genieHandler.js` → `ui.js:_renderGenieStep`) and rendered as cards in the genie coordination trace. They are also persisted in `genie_events` for plan reload.

#### Component LLM Event Piggybacking

When a component handler makes internal LLM calls (e.g., chart mapping Stage 4), the resulting events and token counts must reach the frontend Live Status panel and the token/cost tracking system. Since components run inside tool execution (not as top-level LLM calls), this data is **piggybacked** through the component payload metadata.

**The piggybacking contract:**

```
Component Handler                    Consuming Execution Path
─────────────────                    ──────────────────────────
Stage 4 calls llm_callable     →    call_llm_api updates session DB tokens
Handler embeds in metadata:          Consumer extracts from tool result:
  metadata["_component_llm_events"]    → Emitted as Live Status SSE events
  metadata["llm_input_tokens"]         → Added to turn/statement token counters
  metadata["llm_output_tokens"]        → Added to cost calculation
```

**Two extraction paths** (same contract, different consumers):

| Profile Type | Consumer | File | Extraction Method |
|-------------|----------|-----------|-------------------|
| `tool_enabled` | PhaseExecutor | `phase_executor.py` → `_execute_tool` | `tool_result.get("metadata", {}).pop("_component_llm_events")` → `_log_system_event()` + `_format_sse_with_depth()` |
| `conversation_with_tools` | ConversationAgent | `conversation_agent.py:on_tool_end` | `extract_component_payload()` → `_emit_event("component_llm_resolution", evt)` |

**Token flow (no double-counting):**

```
T1:  Component handler calls llm_callable (Stage 4)
T2:  call_llm_api → update_token_count() → SESSION DB UPDATED (component tokens)
T3:  Handler stores tokens in metadata, embeds _component_llm_events
T4:  Consumer extracts events → Live SSE + stored for plan reload
T5:  Consumer accumulates component tokens in separate counters
T6:  Executor combines LangChain + component tokens for turn counters, cost, SSE
T7:  Executor updates session DB with ONLY LangChain tokens (component already in DB)
```

**Frontend handling:**

The `component_llm_resolution` event type is whitelisted in `processStream()` (`eventHandlers.js`) alongside other conversation agent events. The title routing function `_getConversationAgentStepTitle()` maps it to the event's own `step` field (e.g., "Chart Mapping Resolution (LLM)"). This works for both live streaming and plan event reload (stored in `conversation_agent_events`).

---

## REST API

### User Endpoints

**File:** `src/trusted_data_agent/api/rest_routes.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/v1/components` | List all installed components with status and governance (`?profile_type=` adds the resolved creation default) |
| `POST` | `/v1/components/reload` | Hot-reload components from disk |
| `GET` | `/v1/components/<id>` | Get component details + full manifest |
| `GET` | `/v1/components/<id>/renderer` | Serve renderer JS file for dynamic loading |
| `GET` | `/v1/components/manifest` | Get frontend manifests for all active components |
| `GET` | `/v1/profiles/<profile_id>/components` | Per-agent resolution (`profile_enabled` — the one enablement rule), read by every panel and `componentGate.js` |
| `GET` / `PUT` | `/v1/user/component-profile-defaults` | The per-user creation default (which components start on for a NEW agent) |
| `GET` | `/v1/components/<id>/instances/<instance_id>/export` | Instance export (see *Component-Instance Portability*) |
| `POST` | `/v1/components/<id>/instances/import` | Instance import (preflight or commit) |

**`GET /v1/components` response shape:**
```json
{
  "components": [
    {
      "component_id": "chart",
      "display_name": "Data Visualization",
      "version": "1.0.0",
      "component_type": "action",
      "category": "Visualization",
      "source": "builtin",
      "tool_name": "TDA_Charting",
      "instances": "none",
      "signals": { "emits": [], "listens": [], "announces": [], "consumes_refs": true, "conflicts_with": ["dashboard"] },
      "profile_defaults": { "enabled_for": ["llm_only", "rag_focused", "tool_enabled", "genie"], "default_intensity": "medium" },
      "render_targets": { "default": "inline", "supports": ["inline", "sub_window"] },
      "supports_instance_io": false,
      "globally_disabled": false,
      "user_profile_default_enabled": true
    }
  ],
  "_settings": {
    "user_components_enabled": true,
    "marketplace_enabled": true
  }
}
```

`globally_disabled` is the effective access for the caller (global disabled list + per-user override);
`user_profile_default_enabled` is the creation default for a new agent — the user's own setting, else
`profile_defaults.on_for_new_agents`, else "on when `enabled_for` lists any type" (`_new_agent_default`).
The `_settings` blob enables frontend governance enforcement (hiding Import button when user components disabled).

### Admin Endpoints

**File:** `src/trusted_data_agent/api/admin_routes.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/v1/admin/component-settings` | Get governance settings + built-in component list for checklist |
| `POST` | `/v1/admin/component-settings` | Save governance settings (requires admin role) |
| `GET` | `/v1/admin/component-settings/users/<component_id>` | Per-user overrides for one component |
| `PUT` / `DELETE` | `/v1/admin/component-settings/users/<user_uuid>/<component_id>` | Set / remove one user's override |

---

## Database Schema

### Component Tables

**File:** `schema/18_components.sql`

**`installed_components`**: Registry of all installed components.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER PK | Auto-increment |
| `component_id` | TEXT UNIQUE | Component identifier |
| `display_name` | TEXT | Human-readable name |
| `version` | TEXT | Semver version |
| `source` | TEXT | `builtin` \| `agent_pack` \| `user` |
| `agent_pack_id` | TEXT | FK to agent pack (if source=agent_pack) |
| `is_active` | BOOLEAN | Whether component is active |
| `manifest_json` | TEXT | Full manifest for reference |
| `installed_at` | TIMESTAMP | Installation time |
| `updated_at` | TIMESTAMP | Last update time |


### The one enablement rule

`src/trusted_data_agent/components/enablement.py` is the **single** answer to
*"is component X enabled for profile P"*. Every consumer delegates to it: the runtime
tool surface (`get_active_components` → tool definitions, instructions, context
enrichment), `GET /v1/profiles/<id>/components` (`profile_enabled`, read by every panel
and by the frontend affordance gate), `recommender._component_enabled_for_profile`
(next-step chips + companion-skill auto-assign) and the memory adapter.

The chain — first decisive step wins:

1. **global admin gate + per-user admin override** — `settings.is_component_available`
2. **explicit** `profile.componentConfig[cid].enabled`
3. **manifest default for this profile type** — `profile_defaults.enabled_for`

`enabled_for` is the ONLY manifest default. It means **eligible**, not "always offered":
a handler's `is_profile_relevant` hook may narrow further:

| Component | Relevant only when |
|---|---|
| `skill_library` | the profile resolves an active progressive skill |
| `compute`, `explain_plan` | the deployment's `COMPUTE_LINEAGE` switch is on (`result_sets.enabled()`) |
| `find_tools` | the profile asks for a governed tool surface (`tool_surface.armed_governed`) |
| `action`, `obda_read`, `obda_model` | a knowledge graph is bound to the agent (`obda_planes.profile_has_graph`) |
 **Relevance is folded into the ONE rule** (`enablement.relevant()`, consulted
by `is_enabled`/`filter_enabled`), so an inert component never pulls its companion skill into
the prompt either — found when `compute-expert` auto-injected with the switch off (Sep 2026).

**The tool gate (step 5, action affordances only).** A handler may also declare
`offers_tool(profile)` — whether its LLM tool is offered, a narrower question than enablement.
`files` is the reference: enabled for every agent (its panel, Keep, attachments), its
`TDA_Files` offered only where `componentConfig.files.agentTool` is on. The tool surfaces
(`get_tool_definitions`, `get_action_tool_status`, `get_langchain_tools`, the component
instructions) and every `require_action` resolution (companion skills, chips) read it through
`enablement.offers_tool`; the panel-facing enablement does not. Default `True` — no other
component changes.

**Not in the chain:** `component_profile_defaults` is a per-user **creation** default —
it pre-fills the toggles for a profile that does not exist yet (`GET /v1/components?profile_type=`
returns `profile_enabled_default` for exactly that). It is deliberately not a resolution
step for an existing profile. Its manifest counterpart is `profile_defaults.on_for_new_agents`
(creation-only, absent = on when `enabled_for` lists any type): the Database Navigator says
`false`, so a NEW agent starts without it while every existing agent keeps resolving through
`enabled_for` — and keeps its signature (writing an explicit `enabled` onto signed agents would
have read "changed since signing"). This is why the key stays (Nov 2026 review): the only
signature-preserving way to move a default for new agents alone. The editor writes a new agent's
entry only where its creation default differs from the class default, so the Navigator's `false`
lands as an owned `enabled: false` while every other component starts Auto.

**Three enforcement points**, because advertise-time filtering alone only holds for
well-behaved turns:

| Point | Where | Guards against |
|---|---|---|
| advertise | `get_active_components` | the model being offered a disabled capability |
| **effect** | `enablement.enforce_component_call` at `mcp_adapter.adapter` (Optimize) and `_run_component` (ReAct) | a call arriving unadvertised — replayed champion plan, pack-authored plan, `@TAG` override, leaked-call recovery, hallucination |
| affordance | `static/js/handlers/componentGate.js` | the **UI** offering an action the agent may not perform |

The frontend gate reads the server's resolved `profile_enabled` and never re-derives the
rule; it **fails closed**, because an affordance is a claim that a capability exists.

**REST**: capability-*creating* mutations are gated on `is_component_available`
(dashboard create/add_tile, flow create/execute, canvas create, memory POST, scheduler
create). Reads, updates and deletes of existing instances are deliberately open so a user
can always inspect and clean up their own data — and memory reads/erase are never gated
(data-subject rights outrank a feature flag).

**Per-profile component configuration** is stored on the profile JSON dict as `componentConfig` (not in a separate database table). This follows the same pattern as `knowledgeConfig`, `dualModelConfig`, and `genieConfig`:

```json
{
  "componentConfig": {
    "chart": { "enabled": true, "intensity": "medium" }
  }
}
```

When a `componentConfig` entry is absent, `ComponentManager.get_active_components()` resolves that component from its manifest `enabled_for` for the profile's type. (Until Aug 2026 an absent entry defaulted to *enabled* and the admin gate was not consulted at all — see COMPONENT_ENABLEMENT_ASSESSMENT_2026-08 (`docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md`).)

**`installed_components` is populated automatically** by `ComponentManager._sync_to_database()` at startup and on hot-reload. Components no longer on disk are marked `is_active = 0`.

### Component Settings Table

**File:** `schema/19_component_settings.sql`

**`component_settings`**: Admin governance settings (key-value; the scheduler's `profile_scheduler_enabled` / `platform_scheduler_enabled` gates, seeded by `33_scheduler_enhancements.sql`, live here too).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER PK | Auto-increment |
| `setting_key` | TEXT UNIQUE | Setting identifier |
| `setting_value` | TEXT | Setting value (string, JSON, or boolean) |
| `updated_at` | TIMESTAMP | Last update time |
| `updated_by` | TEXT | Admin who changed the setting |

Default values are inserted by the same file. Per-user overrides live in `user_component_settings` (`schema/34_user_component_settings.sql`: `user_uuid`, `component_id`, `is_enabled` 1 = granted / 0 = blocked, `note`, `updated_by`; UNIQUE per user + component).

### Table Creation in Bootstrap

**File:** `src/trusted_data_agent/auth/database.py`

| Function | Purpose |
|----------|---------|
| `_create_components_tables()` | Load `18_components.sql`, create tables with inline fallback |
| `_create_component_settings_table()` | Load `19_component_settings.sql`, bootstrap from `tda_config.json` |
| `_create_user_component_settings_table()` | Create the per-user override table |

---

## Admin Governance

The admin governance system controls component availability at the platform level. It mirrors the Extension governance pattern exactly.

### Administration UI

Two surfaces:

- **Administration → Components** (`static/js/handlers/platformConnectorAdminHandler.js`,
  `_buildComponentAdminSidebar` / `_loadGenericComponentAdminPanel`): one panel per component —
  an Overview (the global enable switch) and a **Users** tab (`_loadCompUsersTab`) for per-user
  grant/block overrides, plus component-specific tabs (`_extraCompTabs`).
- **Administration → App Config → Features → Component Management** (`static/js/adminManager.js`,
  `loadComponentSettings()` / `saveComponentSettings()`):

```
┌─ Component Management ──────────────────────────────────────────┐
│  Control component availability and custom components           │
│                                                                 │
│  Component Availability                                         │
│  ● All components available (default)                          │
│  ○ Selective — Choose which built-in components are available   │
│    ┌──────────────────────────────────────────┐                │
│    │ [x] Data Visualization (chart)           │                │
│    │ [ ] Task Scheduler (scheduler)           │                │
│    │ ...                                       │                │
│    └──────────────────────────────────────────┘                │
│                                                                 │
│  [on] Allow users to import custom components                  │
│  [on] Enable component marketplace                             │
└─────────────────────────────────────────────────────────────────┘
```

### Enforcement Chain

```
Admin disables "scheduler" (global list) — or blocks it for one user
    ↓
component_settings / user_component_settings updated
    ↓
is_component_available("scheduler", user_uuid) returns False
    ↓
enablement.decide() → False for every agent of that user (step 1 wins)
    ↓
ComponentManager.get_active_components() excludes it
    ↓
No scheduler instructions, no TDA_Scheduler tool, no companion skill
    ↓
A call that arrives anyway (replayed plan, @TAG, leaked call) is refused
by enablement.enforce_component_call at the effect site
```

### Backward Compatibility

Profiles without `componentConfig` get each component's manifest default for their profile type (`profile_defaults.enabled_for`) — on for every class for chart, canvas, dashboard, flow, knowledge graph, scheduler, memory and files (and for the relevance-gated compute, skill library and tool finder); on for Optimize + Ideate for the Database Navigator and EXPLAIN plan; off everywhere for the three OBDA planes (`action`, `obda_read`, `obda_model`), which an agent opts into. The legacy `APP_CONFIG.CHARTING_ENABLED` flag survives only as the new session's `charting_intensity` (`none` when off, else `DEFAULT_CHARTING_INTENSITY`) — the intensity fallback for any component whose profile entry sets none; it does not disable a component.

---

## Compute Component: the receipted-figure reference (S3, Sep 2026)

`components/builtin/compute/` (`TDA_Compute`) is the second headless-by-refusal action component and the
reference for **a component whose output is a fact the trust surfaces verify**. It exists behind ONE
deployment switch (`APP_CONFIG.COMPUTE_LINEAGE`, env `TDA_COMPUTE_LINEAGE`, default off ⇒ absent from
every catalog, byte-identical baseline) and applies the layering rule end to end:

| Layer | Owns |
|---|---|
| **Code** (`agent/compute.py`) | the closed op set `sum · avg · min · max · count · rank · pivot · diff · join · pct_change · filter`, tolerant number parsing (`1,234`, `$5.6`, `12%`), column self-resolution (ONE numeric column resolves itself; several → a refusal naming them), honest empty sets, the receipt (`RECEIPT_FIELDS` → canonical JSON → `receipt_hash`) |
| **Registry** (`agent/result_sets.py`) | the turn's result sets, numbered in execution order, fed at the two post-success seams every data-running engine passes through (the MCP adapter · the ReAct tool wrapper) — the handler **reads, never re-queries**, and the model never pastes rows |
| **Handler** (`handler.py`) | argument validation, recovery-shaped refusals (`spec.error` + `suppress_inline`: a refusal must not read as success on the Optimize path and never renders a card), the receipt card (pre-rendered html — no `renderer.js`) |
| **`instructions.json`** | the bare contract: the ops, the args, "use it instead of doing arithmetic yourself" |
| **Skill** (`compute-expert`, auto-assigned with the component) | the judgement: when the query should aggregate instead, statement addressing, what a receipt means to the reader |

**The trust seam** (`executor._run_compute_lineage`, called by the Optimize and Conversation engines after
the citation gate, before the answer is delivered; Ideate/Focus run no data statements and never stamp;
Coordinate is covered through its experts): every receipt is **sealed** into the turn's Ed25519 provenance
chain as a `compute_step` (verified by `provenance.verify_content` — a receipt that matches no sealed step,
or a sealed step whose receipt is gone, is a NAMED mismatch); every figure in the delivered answer is
**classified once** (`compute_lineage.assess`: receipted · quoted from a row or a statement's row count ·
unreceipted — numbers the question asked, tokens glued to identifiers and sub-three-digit figures are never
convicted); the prose is **marked** (`mark_html` — table cells, `<code>` and `<pre>` are masked with
letter-keyed placeholders so a placeholder digit can never collide with a figure) with
`mark.figure-receipted` / `mark.figure-quoted` (role=button, tabindex, aria-label → the lineage dialog
`static/js/handlers/figureLineage.js`) and `mark.figure-unreceipted` (no claim, no red); the disclosure is
append-only and only under honesty mode `annotate`/`enforce`. The stamp lives on the persisted turn
(`compute` at `update_last_turn_data`, `compute_lineage` beside the other gate stamps), the Live Status
step is `compute_lineage` (*Number Lineage*), the assurance mechanism is `compute_lineage` at GROUNDED, and
`numeric_fidelity` counts a receipted or lineage-quoted figure as grounded.

**Optimize planner wiring:** the `compute_insertion` rewrite pass + the scheduler's `_phase_compute_deps`
— see [FUSION_OPTIMIZER_ARCHITECTURE.md](FUSION_OPTIMIZER_ARCHITECTURE.md). Contract: [restAPI.md
§3.13.7](../RestAPI/restAPI.md). Design: `BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3`.

---

## Component Signals — how components talk to each other

Three channels, one contract (`src/trusted_data_agent/components/signals.py`), declared per
component in its manifest and gated. Before this (Oct 2026) the page had ~40 CustomEvent names in
five styles over two targets with no registry — three events fired into the void and one listener
waited for an event nobody sent — five components each kept a private "…-changed" event raised by
whichever caller remembered, and the only data hand-off between two component calls in a turn was
one special case for one pair hardcoded in shared code (the dashboard's session-wide HTML cache).

### The declaration

```json
"signals": {
  "emits":    ["uderia:kg-node-select", "…"],      // page events the renderer raises
  "listens":  ["uderia:component-changed", "…"],   // page events the renderer / panel follows
  "announces": ["created", "updated", "deleted"],  // the write actions the store announces (empty = keeps nothing)
  "consumes_refs": true | { "implicit": "latest_data" } | false,   // takes `source_ref`
  "conflicts_with": ["chart"]                       // moved here from the manifest root
}
```

Required of every component by the schema. The component card shows it (*Emits · Follows ·
Announces · Reads by reference*); `test_component_bus.mjs` asserts that what a manifest declares is
exactly what its renderer does; `test_component_signals.py` asserts that every action a seam speaks
is declared and that a component that keeps things announces.

### 1. The page bus — `static/js/uderiaBus.js`

ONE registry of names (`UderiaBus.EVENTS`: detail fields + the fact each carries), ONE target
(`document`), ONE prefix (`uderia:`). A classic script loaded in `<head>` before every module and
classic handler, so a handler may wire its listeners at parse time; ES modules use the global.

| Call | Meaning |
|---|---|
| `UderiaBus.emit(name, detail)` | dispatch; an unregistered name warns once and still delivers |
| `UderiaBus.on(name, fn)` / `off(name, fn)` / `once` | DOM semantics (`fn(event)`, payload in `event.detail`); `on` returns the unsubscribe; the handler is registered as-is so `off` removes it |
| `UderiaBus.onComponent(id \| '*', fn)` | one component's changes (or every component's); returns the unsubscribe |
| `UderiaBus.announce(componentId, action, ids, extra)` | a page writer's own announcement of a change it just made (origin `page`) |

Rules, each held by the gate: nothing dispatches a `CustomEvent` on `document`/`window` itself and
nothing listens on them for a bus name; every emitted and listened name is registered; every
registered name has an emitter (page or relayed notice) and, unless marked `public` (a hook for what
follows — `panel-state`, `turn-complete`), a listener. A test imports `test/_uderia_bus.mjs`, which
installs the real bus; the bus resolves `document` per call, so a harness may install its stub before
or after.

### 2. One change event for every component

A write that reaches a component's store from ANYWHERE — this page, another tab, a REST script, a
scheduled run, an agent turn — is announced ONCE, at the store's write seam, as the per-user notice
`component_changed` (`core/ui_notify.announce_component_change(user_uuid, component_id, action, ids,
session_id, profile_id, aspect)`; `announce_files_change` is the files spelling). notifications.js
relays it as `uderia:component-changed` `{component_id, action, ids, aspect, session_id, profile_id,
origin: 'server'}`. There is no per-component change event any more.

The seam declares it, so it holds whoever calls (route, handler, scheduler, pack import):

```python
@_signals.announces("canvas", "created", user="user_id", ids=("canvas_id",))      # a store function
def create_canvas(user_id, title, content, ...): ...                               # ids from args or the result

@_signals.announces("knowledge_graph", "updated", user="self.user_uuid", ids=("self.kg_id",),
                    profile="self.profile_id", aspect="graph")                      # a method, sync or async
async def add_entity(self, ...): ...

@_signals.announces("obda_model", "deployed", user="store.user_uuid", ids=("store.kg_id",),
                    when=lambda r: r.get("status") not in ("noop", "error"))        # gated on the result

@_signals.announces_route("dashboard", "updated", ids=("dashboard_id",), aspect="tiles")  # a route with no shared store
async def add_tile(current_user, dashboard_id): ...                                 # after a 2xx; ids from path/query/body
```

A falsy result (nothing written) announces nothing. Decorated today: `canvas_db`, `flow_db`,
`task_scheduler`, the memory store, `GraphStore` (entities, relationships, bulk import, clear,
metadata, lifecycle), `kg_version_db`, `kg_release`, `authored_deploy`, the catalog service
(Database Navigator), the KG authored/assignment/product routes, the action routes, the dashboard
routes and handler (two writers of one table — both announce). Flow runs keep their own live
events (`uderia:flow-run-event`).

**Own or elsewhere.** A page's own writes come back as notices like everyone else's, and an editor
must tell the two apart: its autosave's echo is nothing new, a write from another tab, a REST
script or a scheduled run is. Every tab has `UderiaBus.CLIENT_ID` (sessionStorage — per tab, for
the tab's life); the ONE fetch wrapper in `auth.js` (the session guard) stamps it on every
same-origin request as `X-Uderia-Client`; `ui_notify.request_client()` reads it inside the request
(`has_request_context`, so a store write made in a worker thread of that request still carries it)
and every `component_changed` notice carries it as `client` — `None` off a request (a scheduled
run, a background task, a REST client that sends none). `notifications.js` hands it to the bus, and
the bus alone marks the event `own`: origin `page`, or `client === CLIENT_ID`. Note the consequence
for an agent turn driven from the UI: the tool runs inside that page's stream request, so what the
agent writes during the turn is that page's own — the renderer shows it from the turn's own events
(a canvas write animated in, a flow patch), not from the notice.

**Followers.** Every renderer that keeps instances subscribes to its own component. The **canvas**:
deleted anywhere → the panel closes; written elsewhere (`updated` · `restored`, not own) → re-read in
place while nothing is being typed; with an edit pending the pending autosave is **held** (nothing is
sent — fired meanwhile it would overwrite the write elsewhere before anyone chose) and the panel
says *Changed elsewhere while you were editing · Reload · Keep mine*: Reload takes theirs and drops
mine, Keep mine releases the hold and sends mine as a version over theirs; typing on a held canvas
stays held; leaving the canvas (switch, close, page hide) keeps mine — typed work is never dropped
silently. The **flow**: deleted → closes; `updated` elsewhere → *Changed elsewhere · Reload*, never a redraw
under the hand (the agent reaches an open canvas through `uderia:flow-patch`). The **dashboard**:
reloads the open dashboard on a change (a pin from a card, an agent's tile, another tab) — never
under a drag, a resize or edit mode; a refresh *start* (`refreshed`) is not a reload (its result
lands as the tile's own update; a reload would re-trigger pending tiles); a layout saved **here** is
not (the user's own hand), a layout saved **elsewhere** is applied, so two tabs agree. The scheduler
re-reads its list, the memory panel follows the store (not the turn: the extraction after a turn
writes the store), the Database Navigator re-reads a refreshed catalog (its own Refresh button sets
a flag so it is not echoed back onto itself), the Knowledge Graph renderer redraws the changed graph
in place, the OBDA panels re-render, the Workspace follows `'*'` through ONE coalesced subscriber.
The notice line is ONE for every split panel — `splitPanelChrome.showSplitPanelNotice(panel, text,
{actions: [{label, onAction}, …]})` (or the one-act shorthand `{actionLabel, onAction}`) /
`hideSplitPanelNotice(panel)` (on `window` for renderers), `.sp-notice` under the `.sp-header`,
themed by variables, dismissable; a panel never builds its own. **Echo.** Besides `own`,
the bus swallows the same fact (component · action · ids) from the other origin within `DEDUPE_MS`
(400 ms) — the page's own announcement followed by the server's echo; two announcements from the
same origin, or id-less ones, are two facts. A page writer that announces after its own fetch
therefore costs one reload, not two.

### 3. The ledger — results by reference

Every component result and every data result of a session is recorded the moment it is produced,
at the two tool chokepoints (`mcp_adapter.adapter` for the Optimize lane, `components.manager` for
the LangChain lanes — `signals.before_process` / `after_process` around the one `handler.process`
call; `record_data_result` where a data tool's rows come back). The reference —
`component:<component_id>:<instance id or container id>` (`dashboard_id`, `flow_id`, `canvas_id`,
`kg_id`, `file_id`, `task_id`, … when the payload carries one) or `data:<tool_name>:<8-hex>` — rides
the result's `metadata.ref` in both lanes, so the model sees the name beside the result it names.

A component that declares `consumes_refs` takes the argument `source_ref` (added to its tool
definition by `tool_definition_for`, so every lane sees it). The chokepoint resolves it, hands the
entry to the handler as `context['source']` and fills an EMPTY `content` (the entry's HTML, else its
rows as a table, else its title) or `data` (its rows) from it — the component reads the earlier
result; the model never copies rows through its context. `{ "implicit": "latest_data" }` reads the
latest data result when neither a ref nor content is given: the dashboard declares it, which is what
its private session-wide `_viz_content_cache` used to be (removed). An unknown ref is reported on the
context (`source_ref_unresolved`) and the call proceeds. Dashboard, canvas, chart and files consume.

In memory per session (bounded to 200 entries, LRU across sessions) with a write-behind sidecar
`tda_sessions/<session_id>/components.json` (the 60 most recent, no entry larger than 64 KB), so a
restart keeps the refs a conversation may still name. `get_ledger().entries(session_id)` lists them.

**Adding a component.** Declare `signals`; emit and listen through the bus (register a new name in
`uderiaBus.js` with its detail fields); decorate the store's write functions with `@_signals.announces`
(or a route with `@_signals.announces_route`); if the renderer keeps a panel, follow
`UderiaBus.onComponent('<id>')`; declare `consumes_refs` if the tool takes content it could read by
reference. The two gates name every omission.

## Third-Party Components

### Component Sources

| Source | Location | Discovery | Status |
|--------|----------|-----------|--------|
| `builtin` | `components/builtin/` | Registry + auto-discover | Live (19 components) |
| `user` | `~/.tda/components/` | Auto-discover at startup / `POST /v1/components/reload` | Live — a component dropped into the directory loads on reload |
| `agent_pack` | Extracted from `.agentpack` files | — | **Not implemented** — the source value and `installed_components.agent_pack_id` are reserved |

### Installation Flow

**Not implemented as an upload.** There is no `POST /v1/components/import` endpoint, and the
Components view's Import button (`#import-component-btn-top`) is shown/hidden by governance
(`user_components_enabled`) but has no handler. Today a third-party component is installed by
copying its directory to `~/.tda/components/<component_id>/` and calling
`POST /v1/components/reload`; the manager validates the manifest (warn-level), loads the handler via
`importlib`, and the component appears in the Components view. The planned flow:

```
User clicks Import → selects component directory/zip
    ↓
POST /v1/components/import            (planned)
    ↓
Validate manifest against JSON Schema
    ↓
Copy to ~/.tda/components/{component_id}/
    ↓
ComponentManager.reload()
    ↓
Handler loaded via importlib, renderer registered
    ↓
Component appears in Components tab
```

### Security Model

| Layer | Mechanism |
|-------|-----------|
| Backend handlers | Same trust model as extensions (`importlib` dynamic loading) |
| Frontend renderers | Served via `/v1/components/<id>/renderer` endpoint |
| CDN dependencies | Declared in manifest, loaded via `<script>` tag |
| CSP headers | Restrict script sources to platform origin + declared CDN deps |

### Agent Pack Integration

**Not implemented.** Agent pack manifests carry no `components` array today, and
`agent_pack_manager.py` neither extracts nor removes component directories. What a pack DOES carry is
each profile's `componentConfig` (which builtin components are enabled for it — see the
`profile_config_contract` registry and `agent_packs/common/component_config.py`). The intended design
— a pack-bundled component extracted to `~/.tda/components/`, linked via
`installed_components.agent_pack_id`, removed on uninstall — remains a plan.

---

## Component-Instance Portability (export / import)

A uniform, file-level export/import contract for the persistent **instances** a
component owns (a flow, a dashboard, a canvas), distinct from installing the
component *definition*. One mechanism for every instance-bearing component — a new
one inherits portability with zero endpoint or UI changes.

**Designed so the marketplace reuses it.** This is the same serialization +
remap core the marketplace layer sits on (publish = `export_instance`, install/fork
= `import_instance(mode="create")`, subscribe = create + link, **republish auto-sync
= `import_instance(mode="update", target_id=…)`** which re-materializes in place,
preserving the subscriber's instance id so their references stay valid — the KG
`_fanout_auto_sync` / agent-pack `update_in_place` pattern). A deterministic
`content_hash` (over the portable content, excluding `exported_at`) lets subscribe
detect that a publisher republished new content. The generic primitives —
profile inventory, preflight report, tag→id remap, content hash, name dedupe —
live once in `src/trusted_data_agent/components/instance_portability.py`; only the
component-specific "where do my profile refs live in the spec" walk stays in each
handler, so there is exactly one remap implementation (no drift between flow,
dashboard, and the future marketplace).

**Contract (`BaseComponentHandler`).** A handler opts in with
`supports_instance_io = True` and implements two methods:

| Method | Purpose |
|---|---|
| `export_instance(instance_id, ctx)` | Return a portable JSON envelope `{component_id, schema_version, name, description, spec, refs, content_hash}`. |
| `import_instance(payload, ctx, *, mode, target_id, mapping, dry_run)` | `dry_run=True` → **preflight report** (no write); `dry_run=False` → commit. `mode="create"` mints a fresh instance; `mode="update"` + `target_id` re-materializes an existing one **in place** (subscribe-sync), preserving its id. |

**Cross-references travel as tags/names, remapped on import.** Instances are not
all self-contained — a flow references per-user **profile IDs** and environment
**tool/prompt names**. Export emits profile references as the friendly **tags** the
LLM authors with (`@OPTIM`), so the file is portable; import remaps tags → the
importing user's real profile IDs. This is the same tag-based portability pattern
agent packs use, now generalised per-instance. Tool/prompt names are not
remappable (you can't substitute `base_readQuery`) — they are surfaced
informationally and the executor degrades gracefully (`_resolve_profile_ref`
fallback + orphan-edge pruning + node warnings).

**Two-phase import = best-in-class remap UX.** The dialog never guesses: it POSTs a
`dry_run` preflight, renders the report (each profile ref auto-matched → pre-selected,
or unmatched → "choose" with a dropdown of the user's profiles, IFOC-coloured), then
commits with the resolved `mapping`. Verified end-to-end (auto-match, unmatched →
user-chosen remap, 400 on a non-matching `component_id`).

**Generic REST endpoints** (dispatch to the handler by `component_id`; 400 if it
doesn't declare `supports_instance_io`):

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/components/<component_id>/instances/<instance_id>/export` | Download the portable JSON envelope |
| `POST` | `/v1/components/<component_id>/instances/import` | `{payload, mode, target_id, mapping, dry_run}` → preflight or commit |

**Surfacing — one shared UI, manifest-driven gating.** The download + (file-pick →
preflight → remap dialog → commit) flow lives once in
`static/js/handlers/instancePortabilityUI.js` (`exportInstance`, `importInstance`,
theme-aware dialog with `ip-` classes). Every surface that lists instances calls it;
none reimplements the dialog. Gating is driven by the manifest flag
`supports_instance_io`, surfaced to the frontend via `ComponentDefinition.to_api_dict()`
(so any UI reads `comp.supports_instance_io` — no hardcoded per-surface list). Live
surfaces:
- **Workspace panel** (`workspacePanelHandler.js`) — Export per instance row, Import per
  section header.
- **Components → Flows tab** (`flowsHandler.js`) — Export per flow row, Import in the list
  header. `componentHandler.js` passes the flow component object into `initFlowsTab` so the
  tab gets `supports_instance_io` + icon/accent from the manifest, not hardcoded.

Enabling export/import for a new component is therefore: implement the two handler
methods + set `supports_instance_io: true` in its manifest. The buttons appear on every
surface automatically — zero UI edits.

### Component-Instance Marketplace (publish + fork + subscribe/sync)

Built directly on the contract above: a **Components** tab in the Intelligence
Marketplace (next to Skills) where users **publish** and **fork** component
*instances* (a flow, a dashboard — not the component code). An instance is DATA, so
there is no code-execution trust concern; it's modelled on the **KG marketplace**.

| Marketplace op | Contract call |
|---|---|
| Publish | `export_instance()` → listing (`envelope_json` + `content_hash`); republish UPDATEs in place (UNIQUE on publisher+component+instance) |
| Fork | `import_instance(mode="create", mapping=…)` via the **shared remap dialog** |
| Subscribe | `import_instance(mode="create", mapping=…)` + a `component_instance_subscriptions` row (captures `local_instance_id` + the mapping) |
| Sync (manual) | `import_instance(mode="update", target_id=local_instance_id, mapping=…)` — re-materializes the local copy **in place**, preserving its id |

**Activation gate (the defining rule):** you can only publish/fork instances of a
component **activated for you** — `is_component_available(component_id, user_uuid)`
+ `is_component_marketplace_enabled()`. Browse marks `component_available` so Fork is
disabled (with a hint) when you don't have the component enabled.

**Generic over `component_id`** — flow, dashboard and canvas share one path.
A new instance component appears automatically once it sets `supports_instance_io` and
implements `list_instances(context)` (the contract method that powers "My Assets").

**Fork reuses the remap dialog** via `openRemapDialog({meta, endpoint, payload?, verb})`
in `instancePortabilityUI.js` — endpoint-agnostic, so file-import (generic import
endpoint, sends payload) and marketplace-fork (install endpoint, server holds the
envelope) drive the *same* two-phase preflight→commit dialog. Cross-user profile
references remap (publisher's tags → forker's profiles).

**Endpoints** (`api/component_instance_marketplace_routes.py`, blueprint self-ensures
`schema/41_marketplace_component_instances.sql`): `POST /v1/components/<cid>/instances/<id>/publish`,
`GET /v1/marketplace/component-instances` (browse), `.../mine` (My Assets, generic via
`list_instances`), `GET .../<mid>` (detail), `POST .../<mid>/install` (fork: dry_run preflight → commit, records install + count),
`POST .../<mid>/rate`, `DELETE .../<mid>` (unpublish, cascades), `GET/PUT .../<mid>/targeted-users`.
Reuses `marketplace_sharing_grants` (`resource_type='component_instance'`) + `marketplace_installs`.

**Subscribe + manual Sync (live-tracking).** Subscribe materializes a local copy and
records a `component_instance_subscriptions` row (`local_instance_id` + the
subscribe-time `mapping_json`). Browse exposes `is_subscribed` / `is_sync_available`
(listing `content_hash` ≠ the row's `synced_content_hash`) / `subscriber_count`, so a
card shows **Subscribe** → **Sync now / Up to date** + **Unsubscribe** (KG pattern).
`/sync` re-runs `import_instance(mode="update", target_id=local_instance_id)` with the
stored mapping — **never silently auto-pulls** (manual, by design). Unsubscribe drops
the link but keeps the local copy (now independent); a sync against a deleted local copy
self-heals by dropping the stale subscription.

Verified end-to-end (both phases): publish → browse → republish-in-place (no dup) → fork
(remap + name override) → install-count → self-rating guard → unpublish cascade; and
subscribe → republish (hash moves → `is_sync_available`) → **Sync re-materializes in
place, same id, 2→3 nodes** → up-to-date → unsubscribe (copy kept).

Files: `schema/41_marketplace_component_instances.sql`, `api/component_instance_marketplace_routes.py`,
`components/base.py` (`list_instances`), `components/builtin/{flow,dashboard,canvas}/handler.py`,
`static/js/handlers/instancePortabilityUI.js` (`openRemapDialog`), `static/js/handlers/marketplaceHandler.js`, `templates/index.html`.

---

**Adopters:** **Flow**, **Dashboard** and **Canvas** are live, all on the shared core
(`create` + `update`). For dashboard, each tile's execution **profile** is the
remappable ref (emitted as a tag); tile **data sources** (`mcp_tool_name`) are
informational refs; per-user **provenance** (`source_session_id` / `source_turn_id`)
is reset on import so refresh re-binds to the importer's mapped profile. Canvas is
self-contained (no remappable refs). Scheduler is
deliberately deferred (session/schedule ownership is semantically fraught).
Verified end-to-end: flow + dashboard export → preflight → create, in-place
`mode=update` (id preserved), unmatched-tag → user-chosen remap, provenance reset,
`content_hash` stable across re-export.

Files: `src/trusted_data_agent/components/base.py` (contract),
`src/trusted_data_agent/components/instance_portability.py` (shared remap/hash core),
`components/builtin/{flow,dashboard,canvas}/handler.py` (adopters),
their `manifest.json` (`supports_instance_io`),
`src/trusted_data_agent/api/rest_routes.py` (generic endpoints),
`static/js/handlers/workspacePanelHandler.js` (Export/Import buttons + remap dialog).

---

## File Reference

### Core Files (Component System)

| File | Purpose |
|------|---------|
| `src/trusted_data_agent/components/__init__.py` | Package init, exports `get_component_manager` |
| `src/trusted_data_agent/components/base.py` | `RenderTarget`, `ComponentRenderPayload`, `BaseComponentHandler` (incl. `is_profile_relevant`, `offers_tool`, instance I/O), `StructuralHandler`, `SystemHandler` |
| `src/trusted_data_agent/components/models.py` | `ComponentDefinition` dataclass, `INSTANCE_SCOPES` |
| `src/trusted_data_agent/components/manager.py` | `ComponentManager` singleton (discovery, manifest validation, registry, hot-reload, LangChain tool factory, Pydantic schema builder, instruction gates, `llm_callable` injection, None-value stripping) |
| `src/trusted_data_agent/components/enablement.py` | THE enablement rule (`decide`, `is_enabled`, `filter_enabled`, `relevant`, `offers_tool`, `enforce_component_call`) |
| `src/trusted_data_agent/components/signals.py` | Component signals: manifest block, `@announces` / `@announces_route`, the ref ledger |
| `src/trusted_data_agent/components/settings.py` | Admin governance predicates + per-user overrides |
| `src/trusted_data_agent/components/instance_portability.py` | Shared remap / content-hash core for instance export/import |
| `src/trusted_data_agent/components/dateforms.py` | Date-text recognition shared by the visual components (mirrors the dashboard renderer's `_DATE_FORMS`) |
| `src/trusted_data_agent/components/utils.py` | Shared utilities: `generate_component_html()`, `extract_component_payload()` |
| `components/builtin/<id>/` | The 19 builtin components (manifest, handler, instructions, renderer) |
| `components/component_registry.json` | Phase-1 builtin registry |
| `components/schemas/component_manifest_v1.schema.json` | JSON Schema for manifest validation |
| `schema/18_components.sql` | `installed_components` table (profile config is on profile JSON dict) |
| `schema/19_component_settings.sql` | `component_settings` table |
| `schema/34_user_component_settings.sql` | `user_component_settings` (per-user overrides) |
| `schema/41_marketplace_component_instances.sql` | Component-instance marketplace tables |
| `static/js/componentRenderers.js` | `ComponentRendererRegistry` (renderer map, CDN loading) |
| `static/js/uderiaBus.js` | The page bus (registry, `CLIENT_ID`, `onComponent`, `announce`) |
| `static/js/subWindowManager.js` | Sub-window lifecycle (create, update, close, state) |
| `static/js/handlers/componentHandler.js` | Components view (cards, detail, panel accents) |
| `static/js/handlers/componentGate.js` | Frontend affordance gate (fails closed) |
| `static/js/handlers/workspacePanelHandler.js` | Workspace panel (`WORKSPACE_REGISTRY`) |
| `static/js/handlers/splitPanelChrome.js` · `panelOpenControl.js` · `instancePortabilityUI.js` | Shared panel chrome / open control / export-import dialog |

**Gates:** `test/test_component_enablement.py`, `test/test_component_signals.py`,
`test/test_component_bus.mjs`, `test/test_component_instances.mjs`,
`test/test_component_tool_schema.py`, `test/test_panel_open_control.mjs`,
`test/test_workspace_new_action.mjs`, `test/test_split_panel_reorder.mjs`.

### Integration Points (by symbol)

| File | Symbol | Role |
|------|--------|------|
| `src/trusted_data_agent/llm/handler.py` | `_get_full_system_prompt` | Component instructions → `{component_instructions_section}` |
| `src/trusted_data_agent/mcp_adapter/adapter.py` | `_invoke_mcp_tool_impl` | Route component tools through `ComponentManager.get_handler()`; effect-site gate; ledger record |
| `src/trusted_data_agent/core/configuration_service.py` | `get_action_tool_status` caller | Inject "Component Tools" category into `structured_tools` for planner visibility |
| `src/trusted_data_agent/agent/executor.py` | `PlanExecutor.run` | Auto-upgrade dispatch (`has_active_tool_components`); component HTML via `generate_component_html()`; combined component + LangChain token accounting |
| `src/trusted_data_agent/agent/engines/conversation_engine.py` · `focus_engine.py` · `ideate_engine.py` | `run` | Merge component tools into the ReAct agent; Focus synthesis auto-upgrade; Ideate claims only pure `llm_only` |
| `src/trusted_data_agent/agent/execution_service.py` | — | Genie component HTML generation; companion-skill auto-assign seam |
| `src/trusted_data_agent/agent/conversation_agent.py` | `on_tool_end`, `_recover_leaked_component_calls` | Payload extraction; `component_llm_resolution` events; component token accumulation; leaked-call recovery |
| `src/trusted_data_agent/agent/genie_coordinator.py` | `GenieCoordinator.__init__` | Merge component tools; `genie_component_invoked` / `completed` events |
| `src/trusted_data_agent/agent/phase_executor.py` | `_execute_standard_phase`, `_execute_tool` | Component fast-path; `_component_llm_events` extraction (`tool_enabled`) |
| `src/trusted_data_agent/api/rest_routes.py` | `list_components`, `get_profile_components`, … | Component REST endpoints |
| `src/trusted_data_agent/api/admin_routes.py` | `/v1/admin/component-settings*` | Admin governance endpoints |
| `src/trusted_data_agent/auth/database.py` | `_create_components_tables`, `_create_component_settings_table`, `_create_user_component_settings_table` | Bootstrap |
| `static/js/ui.js` | `addMessage` | `[data-component-id]` rendering + legacy fallback |
| `static/js/eventHandlers.js` | `processStream` | `component_render` SSE; `component_llm_resolution` whitelist; `genie_component_*` routing |
| `static/js/handlers/configurationHandler.js` | — | Component toggle section in profile modal (populate, restore, collect on save) |
| `static/js/adminManager.js` | `loadComponentSettings`, `saveComponentSettings` | Component Management section in App Config → Features |
| `static/js/handlers/platformConnectorAdminHandler.js` | `_buildComponentAdminSidebar`, `_loadCompUsersTab` | Administration → Components (per component, per user) |
| `templates/index.html` | — | Components view, split panels, sub-window container, `#component-config-section` in profile modal |
| `tda_config.json` | — | `component_settings` block + `componentConfig` on default profiles |
| `MASTER_SYSTEM_PROMPT.txt` | — | `{component_instructions_section}` placeholder |
