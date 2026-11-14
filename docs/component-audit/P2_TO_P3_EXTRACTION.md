# P2 → P3 Extraction Log

This document captures every **quality/usage heuristic removed from a component's
`instructions.json` / `manifest.json` during the P2 "minimal exposure" refactor**,
verbatim, so the P3 phase can place it into the companion `*-expert` skill with
**zero knowledge loss**. Nothing here was deleted — it was *relocated* out of the
correctness baseline and parked for P3.

Branch: `refactor/p2-component-minimal-exposure`.

**Status: P2 done · P3 done.** Every parked item below has been placed in its companion
`*-expert` skill (`skills/builtin/`), and the four previously-missing skills
(`chart-expert`, `canvas-expert`, `knowledge-graph-expert`, `scheduler-expert`) now exist
and auto-assign with their component. This log is retained as the audit trail of what moved
where. See [COMPONENT_ARCHITECTURE.md §0](../Architecture/COMPONENT_ARCHITECTURE.md) and
[SKILL_ARCHITECTURE.md §5a](../Architecture/SKILL_ARCHITECTURE.md).

---

## Phase 0.3 — Injection-path findings (decisive constraints)

Verified against `src/trusted_data_agent/components/manager.py` and `models.py`:

1. **`get_instructions_text()`** assembles each active component's `instructions.json`
   (intensity-resolved, with `placeholder_substitutions` applied) into the **system
   prompt** via `{component_instructions_section}`. This is the conversation /
   strategic path.
2. **`get_tactical_prompt_rules()`** injects each component's manifest
   **`tactical_prompt_rule`** into `WORKFLOW_TACTICAL_PROMPT` via
   `{chart_mapping_rule_section}`. The docstring is explicit:
   *"The tactical call does NOT receive component instructions."*

   → **Consequence:** chart's `manifest.tactical_prompt_rule` (the per-chart-type
   mapping-key catalog) is the **only** mapping guidance on the Optimize tactical
   path. It is **NOT** redundant with `{G2PLOT_GUIDELINES}` (instructions, strategic
   path) — the two serve different code paths. **`tactical_prompt_rule` is left
   UNCHANGED by P2.** The chart "triplication" concern is a non-issue: `guidelines.txt`
   is the single source for the strategic path (injected via `{G2PLOT_GUIDELINES}`),
   `tactical_prompt_rule` is the single source for the tactical path.
3. The flow **manifest `node` arg description** already carries a condensed node-type
   data-field catalogue and is always present in the tool schema (both paths), so the
   verbose catalogue in flow `instructions.json` `heavy` can be trimmed against it.
4. `instructions.json` / `manifest.json` are loaded by `ComponentManager` at startup →
   **edits require a server restart** before the live smoke test (Phase 2.2).

---

## chart → `chart-expert`

_Removed from `instructions.json` (medium + heavy); place in skill:_

- "you **MUST IMMEDIATELY** use the `TDA_Charting` tool as your **NEXT AND ONLY**
  action. You **MUST NOT** re-call any data gathering tools if the data is already
  sufficient for charting." (medium)
- "You should actively look for opportunities to visualize data… After nearly every
  successful data-gathering operation that yields chartable data, your next step
  **MUST IMMEDIATELY** be to call `TDA_Charting`." (heavy)
- "Do not generate charts for simple data retrievals that are easily readable in a
  table." (medium)
- "Prefer visual answers over text-based tables whenever possible." (heavy)

_(KEPT in instructions: exact-`results`-array data contract; choose `chart_type` +
`mapping`; `sort_by`/`sort_direction`/`row_limit` for top-N; `{G2PLOT_GUIDELINES}`.)_
_(UNCHANGED: `manifest.tactical_prompt_rule` — see Phase 0.3 #2.)_

---

## flow → `flow-designer`

_Removed from `instructions.json` `heavy`; place in skill (flow-designer already
covers most — confirm coverage during P3):_

- The full **"Referencing a previous node's value in a condition/expression"**
  paragraph (column-in-scope-by-name, `output` list, `output_as`, "do NOT reference a
  node by its label"). Rationale: `flow_validation.py` canonicalises every reference
  form deterministically at the persistence boundary; this is a tutorial, not a
  correctness requirement.
- The **decision-branch how-to + rationale** ("Build them with add_edge: edge={…
  condition:\"true\"} …", "Without condition:\"true\"/\"false\" … the True/False branch
  labels will not appear"). Rationale: `_normalize_decision_branches` fills missing
  conditions deterministically.
- Two of the three **edge-integrity restatements** (the verbose CRITICAL paragraph) —
  backend rejects dangling edges with an actionable error.
- The **profile_id hallucination reasoning** ("a profile TAG like 'OPTIM' — NEVER a
  raw profile-… ID, which you will hallucinate") — keep the bare rule, move the
  reasoning.
- "Proactively suggest flows for multi-step or recurring tasks." (heavy)
- The elaborated **"Scheduling a flow"** walkthrough (keep one thin pointer line).
- The verbose per-node-type **data-field catalogue** (manifest `node` arg covers it).

_(KEPT: action+required-arg list; `insert_node` shorthand; task_tool/task_llm/
task_prompt choice; profile_id-is-a-TAG bare rule; open≠get; create≠edit; no-emoji.)_

---

## canvas → `canvas-expert`

_Removed from `instructions.json`; place in skill:_

- The **"Use TDA_Canvas when: … Do NOT use TDA_Canvas for: …"** decision lists (medium).
- "You MUST actively look for opportunities… Users strongly prefer rich interactive
  output over plain text." (heavy)
- The **sources format** style guidance: `"Document Title (collection_name), Another
  Doc (collection_name)"`.

_(KEPT: action enum + per-action args; "always provide COMPLETE content"; update-by-
same-title; open-from-any-session + thin "if title unknown, list first / do NOT
regenerate". ADDED: `mermaid` to languages line; `version_id` to args list.)_

---

## dashboard → `dashboard-expert`

_Removed from `manifest.json` arg descriptions; place in skill (dashboard-expert
already has the grid):_

- `tile_width`: "Use 12 for tables, 6 for charts, 3–4 for KPIs." → reduced to
  "Tile width in grid columns (1–12). Default 6."
- `tile_height`: "Use 2 for KPIs, 4 for charts, 6 for tables." → reduced to
  "Tile height in grid rows (each row ≈ 80px). Default 4."

_(Trimmed in instructions: chart_type per-phrase enumeration → one sentence.
ADDED: `refresh` to heavy action list.)_

---

## knowledge_graph → `knowledge-graph-expert`

_Removed from `instructions.json` `heavy`; place in skill:_

- "Proactively offer to enrich the graph when a query reveals relationships not yet in
  it (action 'add_entity'/'add_relationship')." + the elaborated snapshot-before-
  destructive-edit coaching (keep the bare `snapshot/versions/restore` action mention).

---

## memory → `memory-expert`

_Removed from `instructions.json` `heavy`; place in skill (memory-expert already has it):_

- The confirmation phrasing script: `If a user states a clear lasting preference,
  briefly confirm: "Got it — I'll remember that."`

_(KEPT: the proactive-capture cue — legitimate heavy-intensity behavior.)_

---

## scheduler → `scheduler-expert`

_Removed from `instructions.json`; place in skill:_

- "session_context (… — always ask the user which they prefer)" / "always clarify this
  with the user" — move the *always-ask* coaching; keep the bare current-vs-new
  definition.
- "Also proactively suggest scheduling when a user runs a report or query they might
  want automated." + the verbatim suggestion script ("After completing a data query
  the user runs repeatedly, say: …").
- The elaborated flow-scheduling walkthrough (keep the thin correct-usage line
  `prompt = "Execute flow <flow_id>"`).

_(KEPT: action+required-arg list; cron/interval examples. ADDED: "All schedules are
interpreted in UTC.")_

> Out of P2 scope (Tier-3 code fixes, not exposure): scheduler DOW/`weekday()`
> preview mismatch; memory `forget` substring bluntness; memory `mem_key` tool/skill
> mismatch.
