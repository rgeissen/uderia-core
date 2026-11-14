# EXPLAIN Plan Architecture

**Component:** `explain_plan` (`TDA_ExplainPlan`) · **Package:** `components/builtin/explain_plan/` · **Companion skill:** `explain-plan-expert` · **Since:** Oct 2026 · **Last updated:** 2026-10-30

The EXPLAIN Plan component reads a **Teradata** EXPLAIN result and draws it as a graph of steps — operation, AMP scope, inputs, access path, join method, output spool and how it is distributed, the row estimate and its confidence, the estimated time — with the findings a plan reader looks for first: product joins, estimates made without statistics, data duplicated or redistributed between AMPs, full scans, and the step that carries most of the time.

It **never runs SQL**. The agent runs `EXPLAIN <the SELECT>` with its own SQL tool — through the one SQL gate, on every lane — and the component reads that result from the turn's result-set registry by statement number, exactly as Compute with Lineage does. The same parser and the same spec also serve the SQL canvas's *Explain* button (no agent involved), the workload-tuning evidence and the action gate's plan reading.

---

## 1. Files

| File | Role |
|---|---|
| `manifest.json` | tool definition, `answer_surface`, `signals`, `instances`, profile defaults |
| `handler.py` | `ExplainPlanHandler` — finds the EXPLAIN statement, parses it, builds the spec; every refusal recovery-shaped |
| `plan_parser.py` | pure, deterministic: Teradata plan prose → `{steps, edges, sources, result, result_spool, result_steps, total_seconds, findings}`; `summary`, `build_spec` |
| `instructions.json` | intensity-keyed `none · medium · heavy` |
| `renderer.js` | the answer card (a preview), the side panel (segments, compare, re-explain), the layout engine, dashboard-tile rendering |
| `skills/builtin/explain-plan-expert/explain-plan-expert.md` | the companion skill |

## 2. Tool contract (`TDA_ExplainPlan`)

One optional argument:

| Arg | Required | Meaning |
|---|---|---|
| `statement` | no | the statement number of an EXPLAIN that ran **this turn**; omitted = the latest EXPLAIN |

The tool description and the instructions carry the whole correctness baseline: run `EXPLAIN <the SELECT>` with your SQL tool first, this tool does not run SQL, Teradata plans only. `heavy` adds: explain from the findings and the step summary, worst finding first, naming the step; to compare two versions, EXPLAIN both and call the tool once per statement number. (The manifest declares no `intensity_levels`; the instructions file is keyed `none · medium · heavy` and read at `componentConfig.explain_plan.intensity`, default `medium`.)

**Refusals** (each a payload with `metadata.error_code`, the message in `spec.error`, `suppress_inline: true`, never an exception):

| `error_code` | When | The message tells the agent |
|---|---|---|
| `bad_arguments` | `statement` is not an integer | to pass a statement number or omit it |
| `no_explain` | no EXPLAIN has run this turn | to run the EXPLAIN **itself** with its SQL tool, then call again — and *not* to ask the user |
| `no_such_statement` | the number names nothing this turn | that statement numbers start again at 1 every turn, and lists the EXPLAIN statements that did run (or the run-it-yourself instruction) |
| `not_an_explain` | the numbered statement is not an EXPLAIN | to EXPLAIN the SELECT and pass that number |
| `unparsed` | no numbered work steps | that it reads Teradata EXPLAIN output only; describe other plans in words |
| `explain_plan_error` | anything else | the error |

The run-it-yourself wording (`_RUN_IT_YOURSELF`) is one constant: a bare "run EXPLAIN first" had been read by a model as an instruction for the user (prod, Oct 2026).

**Success payload.** `spec` from `plan_parser.build_spec` (`statement · origin · sql` (≤4,000 chars, the keyword removed) `· steps` (≤120, each text ≤700 chars) `· edges · sources · result_steps · result · total_seconds · findings · summary · truncated`), a title such as *Plan of statement 2 · 4 steps · total est. 0.03 s · product join* (the worst non-info finding named), and `metadata` carrying `statement`, the compact `summary` the model reads instead of the raw plan, and the findings' `severity · kind · steps`. No HTML: the renderer draws it in the chat, on reload and in a dashboard tile alike. A plan cut by the registry's row bound says so ("The plan text was cut after N lines; later steps are missing.").

## 3. The parser (deterministic, zero tokens)

`plan_parser.py`, wording observed on Teradata 20.00.24.60:

- `explain_text(rows)` — one line per result row, from the `Explanation` column or any single column; `is_explain(statement)` — the statement starts with `EXPLAIN`.
- `_blocks` splits the prose into numbered blocks by indentation (a nested block is a step of the enclosing *parallel* group) plus the final `->` result line.
- `_step` reads one step: operation and AMP scope (`all-AMPs RETRIEVE`, `JOIN`, `SUM`, …), inputs (the `from` table or spool and the `joined to` side), access path (`by way of …`), join method, output spool, distribution (`duplicated` · `redistributed` · `local`), sort, row estimate and confidence, estimated seconds, whether an index or RowHash was used; `LOCK` steps record table and mode; `END TRANSACTION` and the parallel header are kept as structural steps. A step the patterns do not recognise is kept with its text as `other`, never dropped.
- `parse` builds edges from each spool's producers to its consumers (several producers per spool for UNION branches; a spool number reused after its last use starts a new producer list) and from each base table to the steps that read it; the result spool's producers are `result_steps`.
- `findings(plan)`, worst first (`danger` → `warn` → `info`):

| Kind | Severity | Fires on |
|---|---|---|
| `product_join` | danger | a step joined with a product join |
| `no_confidence` | warn | any row estimate made with *no confidence* (one finding listing the steps) |
| `duplicated` | info | a spool duplicated on all AMPs (with its estimated rows) |
| `redistributed` | info | a spool redistributed by hash |
| `full_scan` | info | a base table read by an all-rows scan with no index |
| `hot_step` | info | one step carries ≥ 50 % of the estimated time (and there is more than one timed step) |

- `summary(plan)` — the compact text the model reads: the work-step count and total estimate, up to 12 step lines, and the findings (or "none of the checked patterns").

## 4. Deterministic layer vs skill layer

| Layer | Carries |
|---|---|
| Component | finding the right statement, parsing, the findings and their order, the summary, the title, the spec, every refusal and its recovery |
| Instructions | the act order (EXPLAIN with your SQL tool → call the tool), Teradata only, never runs SQL |
| Skill (`explain-plan-expert`, `component_id: "explain_plan"` in `skills/skill_registry.json`) | which finding to lead with (product join → large duplicated/redistributed spool → all-rows scan of a large table → no-confidence estimates), what each means and what changes it (an equality join condition; `COLLECT STATISTICS` on filtered/joined/grouped columns; primary-index alignment), that estimates are estimates, compare by EXPLAINing both versions, keep it to two or three findings; points to the `teradata-patterns` library's `performance` sections |

The skill auto-assigns whenever the component is enabled.

## 5. Data flow

```
agent: EXPLAIN SELECT …   (its SQL tool)
   ├─ MCP SQL tool (e.g. base_readQuery)        ┐  post-success seams record the rows in the
   └─ Database Navigator `query` (opt-in)        ┘  turn's result-set registry, numbered per turn
agent: TDA_ExplainPlan(statement?)
   → result_sets.statements(session) / result_sets.get(session, n)
   → plan_parser.explain_text → parse → build_spec
   → ComponentRenderPayload (spec + summary)  → the model reads the summary; the user sees the graph
```

- **The registry is the switch.** `ExplainPlanHandler.is_profile_relevant` returns `agent.result_sets.enabled()`, i.e. `APP_CONFIG.COMPUTE_LINEAGE` (env `TDA_COMPUTE_LINEAGE`; off in code, on by default in `docker-compose.yml`). With it off the component is not relevant, so its tool and instructions are absent — the same switch as `TDA_Compute`. The registry is reset at every turn start (`result_sets.begin_turn`), which is why statement numbers do not carry over.
- **Lineage.** A plan is prose, not data: `agent/compute_lineage.py` treats an EXPLAIN statement specially — its row estimates can trace a figure in the answer, its step numbers and its line count never do.

### Relation to the Database Navigator and `dbcatalog`

The component shares the navigator's read-only rule rather than having its own:

- **`dbcatalog/sql_runner.single_select_guard(sql, allow_explain=True)`** admits `EXPLAIN <one SELECT>` — the explained statement must pass the same guard; `EXPLAIN ANALYZE` is refused because it executes the statement; EXPLAIN of a write or of two statements is refused. Only the navigator's agent `query` path passes `allow_explain=True` (in the Teradata and generic providers' `run_select`); previews, counts and dictionary reads stay SELECT-only. See [DATABASE_NAVIGATOR_ARCHITECTURE.md §5a](DATABASE_NAVIGATOR_ARCHITECTURE.md).
- So an agent with the navigator's opt-in `query` on a Teradata Database Connection can EXPLAIN without an MCP server; an agent on an MCP source EXPLAINs with that server's SQL tool. Either way the result lands in the same registry.
- **The SQL canvas's Explain** (`POST /v1/canvas/explain`, `api/rest_routes.canvas_explain`) explains the canvas's statement on its bound Database Connection without the agent: Teradata connections only (`not_teradata` otherwise), one SELECT (`single_select_guard` on the statement with any leading EXPLAIN removed, `not_read_only` otherwise), run as `EXPLAIN <body>` through the canvas's `sql_native` connector **read-only**, the database's message returned without the driver's stack. It builds the **same spec** with `origin: "canvas"` and `statement: null` (title *Plan of the canvas statement · …*), so one renderer draws both.
- **Other readers of the parser.** `core/tuning_evidence.explain_workload` (OBDA-MODEL workload tuning) EXPLAINs distinct workload statements through `dbcatalog.sql_runner.run_rows` and keeps each plan's findings, estimated seconds, whether it rests on a guess (a `no`/`low` confidence step) and its shape hash. `dbdeploy/action_validation.write_plan_problems` (the OBDA-ACT gate) reads a WRITE plan from its own step text, because the parser models retrieve and join steps and reads a write step as `other`.

## 6. UI

- **In the answer** — a preview card: the graph laid out for the answer's width (≈780 px, wrapping long parallel groups, folding many source tables into the steps that read them) and the shared open-in-panel control; clicking the preview is the same gesture. The row is an evidence row with the word *Plan* and opens by default (`answer_surface: {open: true, word: "Plan", pin: true}`).
- **The side panel** (`#explain-plan-split-panel`, `openExplainPlanPanel`) — segments **Overview** (the findings grouped, where the time goes, the comparison with the previous plan), **Graph** (source tables on top, steps in execution order, a parallel group framed side by side, an edge per table or spool read; a step a finding names carries the finding's colour; zoom 40 %–125 % changes how much fits), **Steps** (a sortable table) and **Statement**. It keeps the last 6 plans of the page (a switcher); a new plan is compared with the previous one **by finding kind** (`comparePlans` — step numbers move between plans), with a Compare toggle. A canvas plan offers **re-explain** (`/v1/canvas/explain` again, "the plan is unchanged / changed"). Selecting a step highlights the tables it reads in the open SQL canvas (`highlightInOpenCanvas`); the statement can be opened in a canvas. Download goes through the one export exit `FilesKeep.deliver` (`explain-plan-<stamp>.txt`, with *Save to Files*).
- **Workspace** — the `explain_plan` entry in `workspacePanelHandler.js` lists the panel's plans (`panelPlans`, newest first, with step and finding counts and origin) and opens one (`showPanelPlan`); there is nothing to create.
- **Dashboard** — *Pin to dashboard* is the row's footer act; a pinned plan re-renders through `renderExplainPlan` in the tile (its refresh button reads *Redraw the pinned plan*).

Theme variables only; monochrome icons; orange accent `#f97316`.

## 7. Governance and declarations

- **Enablement** — the standard chain; manifest `enabled_for: ["tool_enabled", "llm_only"]` (the classes that run SQL); plus the relevance gate above. Agent packs keep it off unless they opt in (`agent_packs/common/component_config.ACTION_COMPONENTS`). `offers_tool` is not overridden.
- **`instances: "page"`** — its plans live in the page only (no server copy); the Workspace section is therefore not scoped to an agent.
- **`signals`** — `emits: ["uderia:component-changed"]` (the panel announces `UderiaBus.announce('explain_plan', 'created')` when a plan is added, so the Workspace refreshes); `listens` / `announces` empty; `consumes_refs: false`.
- **Read-only by construction** — the component reads the registry; the canvas endpoint runs only an EXPLAIN of a guarded SELECT on a read-only connection.

## 8. REST

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/canvas/explain` | `{connection_id, code}` → `{status, spec, title, summary}` for the canvas's statement; errors `no_connection` · `empty` · `not_teradata` · `not_read_only` · `explain_failed` · `unparsed` (422) · `explain_error` |

The agent's tool has no endpoint of its own; the renderer is served by `/v1/components/explain_plan/renderer`.

## 9. Gates

| Gate | What it pins | In the battery |
|---|---|---|
| `test/test_explain_plan.py` | the parser over the real Teradata 20.00 plans in `test/fixtures/teradata_explain_plans.json` (locks, parallel groups, UNION spools, findings); the summary; the handler through the real result-set registry (latest EXPLAIN, every refusal and its wording, cut plans, relevance on/off); the guard admits EXPLAIN only where asked; lineage reads estimates, not step numbers; the canvas endpoint builds the same spec read-only for Teradata only; manifest/skill/renderer/dashboard wiring; packs keep it off | yes |
| `test/test_explain_plan_renderer.mjs` | the shipped renderer under a DOM stub over specs the real parser produced: layout, severity, the card, the side panel beside the SQL canvas, compare, the canvas side, a large plan in the width it is given, theme | yes |
