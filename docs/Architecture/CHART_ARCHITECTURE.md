# Chart Architecture

**Component:** `chart` (`TDA_Charting`, display name *Data Visualization*) · **Package:** `components/builtin/chart/` · **Renderer:** G2Plot 2.4.31 · **Companion skill:** `chart-expert` · **Last updated:** 2026-10-30

The chart component turns a data result into an interactive G2Plot chart inside the answer. It is the component library's reference implementation of the intelligence-layering rule ([COMPONENT_ARCHITECTURE.md §0](COMPONENT_ARCHITECTURE.md)): the model chooses *that* a result deserves a chart, *which* chart type fits the question, and *which* columns fill the roles; the component repairs everything else — hallucinated data shapes, wrong or missing column names, swapped axes, wide tables, top-N cuts, text dates on a time axis — so that the renderer never receives a mapping it cannot draw.

COMPONENT_ARCHITECTURE.md keeps the long-form history of the chart handler (heatmap and treemap design notes); this document is the component's own contract as the code stands.

---

## 1. Files

| File | Role |
|---|---|
| `manifest.json` | tool definition, the **chart type registry** (`chart_types`), the tactical prompt rule, `answer_surface`, `signals`, `instances`, render targets, CDN dependency, profile defaults |
| `handler.py` | `ChartComponentHandler` — data normalisation, filters, the five-stage mapping pipeline, the G2Plot spec builder |
| `instructions.json` | intensity-keyed (`none · medium · heavy`) instructions; `{G2PLOT_GUIDELINES}` is substituted from `guidelines.txt` |
| `guidelines.txt` | the charting guidelines the model reads: the mapping roles, worked examples, the chart types grouped by purpose |
| `renderer.js` | `renderChart(containerId, payload)` — instantiates `G2Plot[spec.type]` |
| `skills/builtin/chart-expert/chart-expert.md` | the companion skill (judgement only) |

## 2. Tool contract (`TDA_Charting`)

| Arg | Required | Meaning |
|---|---|---|
| `chart_type` | yes | one of the registered types (§4) |
| `data` | yes | the data rows — "the EXACT `results` array" of the preceding data tool |
| `title` | yes | what the chart shows |
| `mapping` | yes | role → column name (`x_axis`, `y_axis`, `color`, `angle`, `size`, `value`) |
| `sort_by` | no | column to sort by before charting |
| `sort_direction` | no | `asc` or `desc` (default `desc`) |
| `row_limit` | no | rows kept after sorting ("top N") |
| `source_ref` | no | added automatically (§6): the `ref` of an earlier result in the conversation — the component reads its rows itself |

`data` and `mapping` are dynamic-key object arguments, so the LangChain lanes advertise them as JSON-encoded strings and decode them before the handler runs (`components/manager.py:_OPEN_OBJECT_ARG_TYPES`) — valid for every provider's tool-schema validator. `validate_arguments` requires a non-empty list of dicts **after** normalisation; an empty result returns `spec.error` ("…empty list. Cannot generate chart without data.") rather than a chart.

**Instructions.** `medium` and `heavy` share one text (pass the exact `results` array; pick a type from the guidelines; use `sort_by`/`sort_direction`/`row_limit` for subsets; say in the answer what the chart represents) and differ in the trigger — *when you have data suitable* vs *when a data result is suitable, render it*. Intensity comes from `componentConfig.chart.intensity` (manifest default `medium`).

**Tactical rule.** The manifest's `tactical_prompt_rule` (pie ⇒ `angle` + `color`; bar/column/line/area/scatter ⇒ `x_axis` + `y_axis`; never invent keys) is injected into the Optimize lane's tactical prompt at `{chart_mapping_rule_section}` by `ComponentManager.get_tactical_prompt_rules` — only while the component is enabled for the profile.

## 3. The handler pipeline

`ChartComponentHandler.process` (`is_deterministic = True`):

```
arguments.data
  → _transform_chart_data        normalise hallucinated shapes
  → _apply_data_filters          sort_by / sort_direction / row_limit
  → validate_arguments           non-empty list of dicts, else spec.error
  → _resolve_and_validate_mapping   the five stages (below)
  → [_melt_wide_to_long]         when the mapping asks for a melt (heatmap)
  → _build_g2plot_spec           roles → G2Plot fields, coercion, ordering, special types
  → ComponentRenderPayload       spec + title "<title> · <type> · N points" + metadata
```

**Normalisation** (`_transform_chart_data`): a list of `{results: [...]}` wrappers is flattened; `{labels, values}` becomes `[{label, value}]`; `{columns, rows}` becomes its `rows`; the `qlty_distinctCategories` shape has `ColumnName` renamed to `SourceColumnName`.

**Filters** (`_apply_data_filters`): `sort_by` (numeric where the value parses, `None` last) then `row_limit`, applied before the mapping is resolved, so the model never pre-trims rows.

### The five-stage mapping pipeline

`_resolve_and_validate_mapping(chart_type, data, raw_mapping, context)` always returns a mapping the spec builder can consume. Stages 1–2 always run; 3–5 run only when stage 2 finds a problem. The stage that settled it is recorded as `metadata.mapping_resolution.resolved_by`.

| Stage | Function | What it does | `resolved_by` |
|---|---|---|---|
| 1 Sanitize | `_sanitize_mapping` | drops `_`-prefixed keys, keys that are not a role of this type (the universal roles `x_axis · y_axis · color · angle · size · value` are always accepted), and `None` / empty / non-string values | — |
| 2 Validate | `_validate_mapping` | diagnostic only: required roles missing, columns not in the data (case-insensitive), axes swapped (x all-numeric while y is not, over 5 rows); duplicates are reported but do not invalidate | `sanitized_passthrough` when valid |
| 3 Deterministic repair | `_repair_mapping_deterministic` | (a) fuzzy-match a bad column (`_fuzzy_match_column`: exact case-insensitive → separator-stripped → token Jaccard > 0.5 with a unique best → a unique prefix/suffix), rejecting a match to a **constant-value** column for `x_axis`/`color` (`_has_multiple_values`, 50-row sample); (b) swap the axes; (c) fill missing required roles from `_classify_columns` (name patterns: temporal · metric · dimension · ambiguous) via `_assign_roles` | `deterministic_repair` |
| 4 LLM repair | `_repair_mapping_via_llm_wrapper` → `_resolve_mapping_via_llm` | only when the context carries an `llm_callable` **and** some column is `ambiguous` (numeric with no recognisable name): one small call (`max_tokens=200`) that sees each column's first value and its auto-detected class; the answer is sanitised and merged under the existing roles | `llm_assisted` |
| 5 Positional fallback | `_positional_fallback` | the required roles take the columns in order; a heatmap with more than three columns melts | `positional_fallback` |

`_assign_roles` is type-aware: **heatmap** prefers two categorical/temporal axes with the first metric as colour, else melts (one axis + ≥2 metrics, or ≥3 numerics); **pie** takes the first numeric as `angle` and the first varied category as `color`; **gauge** takes one `value`; **scatter** prefers two numerics; every other type takes the first varied dimension as `x_axis`, the first unused numeric as `y_axis`, and a remaining category as `color` when the type allows it.

A stage-4 call returns its tokens in `metadata.llm_input_tokens` / `llm_output_tokens` and a piggybacked Live Status event in `metadata._component_llm_events` (*Chart Mapping Resolution (LLM)*, `correction_type: component_llm_mapping`), which the executing lane emits and accounts for ([COMPONENT_ARCHITECTURE.md → Component LLM Event Piggybacking](COMPONENT_ARCHITECTURE.md#component-llm-event-piggybacking)). The callable is wired at both tool chokepoints: `mcp_adapter/adapter.py` (Optimize, the turn's `STATE['llm']`) and `components/manager.get_component_langchain_tools` (the LangChain lanes: the turn's client handed in as `llm_instance` by the Ideate-with-tools and Focus engines, else the per-user registry through `mcp_adapter.turn_llm` — never the process-global `APP_STATE['llm']`).

### The G2Plot spec

`_build_g2plot_spec(args, data)` maps roles to fields — `x_axis`/`category` → `xField`, `y_axis`/`value` → `yField`, `color` → `seriesField`, `angle` → `angleField`, `size` → `sizeField` (scatter bubbles) — and returns `{type, options}`:

- **colorField** — pie, heatmap and treemap take `colorField` instead of `seriesField`.
- **Coercion** — the value columns are converted to floats (thousands separators removed); a heatmap's `yField` stays a string so G2Plot draws discrete cells, and a `meta` block makes both heatmap axes categorical with numerically sorted values where they parse.
- **Padding** — string cells are stripped (a warehouse `CHAR` column arrives space-padded).
- **Chronological axis** — for the types in `_CHRONOLOGICAL_CHARTS` (`line`, `area`, `dualaxes` — keys are the manifest's chart types) with no explicit `sort_by`, rows are ordered by the x column's date text through `components/dateforms.sort_chronological`, the same date catalogue the dashboard renderer uses; a non-date x is never reordered.
- **Treemap** — flat rows become `{name: "root", children}` (`_build_treemap_hierarchy`: `xField` at level 1, `yField` at level 2, row count as the leaf value) and `xField`/`yField` are removed.
- **Type** — the G2Plot class comes from the registry's `g2plot_type`; an unregistered type falls back to `chart_type.capitalize()`.
- **Guard** — a mapping value that is not a non-empty string is skipped with a warning; a column not in the data raises (caught: the payload carries `spec.error`).

`process` stamps the originating turn as `spec.source_turn_id` so a dashboard pin binds its refresh provenance to that turn (§7).

## 4. Chart types

The registry is `manifest.json → chart_types` (loaded at import — adding a type is a manifest entry and a restart). **17 types**:

| Group (as `guidelines.txt` presents them) | Types → G2Plot class |
|---|---|
| Comparison | `bar` → Column · `column` → Column · `waterfall` → Waterfall · `funnel` → Funnel · `radar` → Radar |
| Trend | `line` → Line · `area` → Area · `dualaxes` → DualAxes |
| Proportion | `pie` → Pie · `rose` → Rose · `treemap` → Treemap |
| Distribution | `scatter` → Scatter · `histogram` → Histogram · `boxplot` → Box · `heatmap` → Heatmap |
| Specialised | `gauge` → Gauge · `wordcloud` → WordCloud |

Required roles per type (`mapping_roles`): `x_axis + y_axis` for most; `angle + color` for pie; `x_axis` for histogram; `x_axis + y_axis + color` for heatmap; `value` for gauge. Optional `color` on bar, column, line, area, histogram, boxplot, radar, rose, treemap; `color` and `size` on scatter.

## 5. Deterministic layer vs skill layer

| Layer | Carries |
|---|---|
| Component (`handler.py`) | every repair above, sorting/limiting, melting, coercion, chronological order, the G2Plot mapping, turn stamping |
| Instructions + guidelines | the correctness baseline: which args, which roles per type, pass the exact rows |
| Skill (`chart-expert`, linked by `component_id: "chart"` in `skills/skill_registry.json`, `injection_target: user_context`) | when to chart vs leave a table, the chart type by question shape (comparison, trend, share, correlation, distribution, top-N, two scales, multi-dimension score), meaningful mapping, anti-patterns (pies beyond ~6 slices, one-value charts, too many series, re-fetching data, vague titles) |

The skill auto-assigns whenever the component is enabled (`skills/manager.py:resolve_auto_skill_specs`).

## 6. Data flow by lane

- **Optimize** (`tool_enabled`). A `TDA_Charting` phase takes the **component fast path** in `phase_executor.py`: `_resolve_charting_data` resolves the rows from the workflow state (including a literal placeholder such as `result_of_phase_2` a weak model writes instead of the data), the mapping is passed **empty** so the handler's pipeline decides it, the title is the phase goal, and the tactical LLM is skipped (a *Component Fast-Path* plan-optimisation event is emitted). If the rows cannot be resolved it falls through to the tactical LLM. For a follow-up that only asks to chart the previous answer, `planner._is_chart_only_query` prepends a *chart continuation* block to the strategic prompt so the planner reuses the previous turn's data; a question that names new content is never classified chart-only.
- **Ideate + tools, Focus, Coordinate** (LangChain lanes). The model calls the bound tool; `ComponentManager` runs the handler. A model that writes the call as text JSON instead of invoking it is recovered by `ConversationAgentExecutor._recover_leaked_component_calls` (the call is executed, the JSON stripped).
- **By reference.** The manifest declares `signals.consumes_refs: true`, so the tool gains a `source_ref` argument (`signals.with_source_ref_arg`). At either chokepoint `signals.before_process` resolves the ref against the session's result ledger and fills an empty `data` from the entry's rows — the model names an earlier result instead of copying its rows through its context. Every chart result is itself recorded (`signals.after_process`) under `component:chart:<container id>`, stamped on `metadata.ref`.

## 7. Where a chart goes after the answer

- **In the answer** — the chart is an evidence row of kind `chart` (`answer_surface`: `kind: chart`, `word: Chart`, `open: true` — the one kind that opens by default). `renderChart` gives the container a definite height (`options.height`, default 400 px) so G2Plot's `autoFit` stays stable, forces `autoFit` on, and drops the plot's own title inside an evidence row (the row summary already states it; a dashboard tile or canvas keeps it). A spec G2Plot cannot draw renders an error line in place.
- **Pin to dashboard** — `answer_surface.pin: true` gives the row the footer act *Pin to dashboard* (`answer_surface.component_row` → `pin_footer`; revealed by `ui.js:_wirePinActs` only when the Dashboard component is enabled for the agent that answered). The tile is created from the spec (`POST /v1/dashboards/<id>/tiles`, `component_id: chart`, default 6 × 4), and the spec's `source_turn_id` lets the server stamp the turn's single data tool for a no-LLM refresh. Detail: [DASHBOARD_ARCHITECTURE.md §5, §7](DASHBOARD_ARCHITECTURE.md).
- **Keep to Files** — *Keep* on a chart row calls `POST /v1/files/keep` with `kind: chart`; `filestore/service.keep_from_turn` stores the browser's PNG **and** a CSV of the chart's own data read from the persisted answer's `data-spec` (`formats.charts_in_html` / `chart_rows`) as one pair; identical content again answers `409 duplicate`. Detail: [FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md).
- **Sub-window** — the manifest also supports a `sub_window` target (`{chart_type} Chart: {title}`, 600 × 400, resizable).

## 8. Governance and declarations

- **Enablement** — the standard component chain (`components.enablement`): admin and per-user gates → `componentConfig.chart.enabled` → manifest `enabled_for` (`llm_only`, `rag_focused`, `tool_enabled`, `genie` — on by default for every class). Agent packs switch it off unless they opt in (`agent_packs/common/component_config.ACTION_COMPONENTS`). `offers_tool` is not overridden.
- **`instances: "none"`** — a chart keeps nothing of its own; it has no Workspace section and no store.
- **`signals`** — `emits` / `listens` / `announces` empty; `consumes_refs: true`; `conflicts_with: ["dashboard"]`. The conflict is enforced only by the profile editor (enabling one unchecks the other); the runtime does not enforce it, because both are default-on for every class (see the note in `ComponentManager.get_active_components`).
- **No component-specific REST endpoints** — the renderer is served by the generic `/v1/components/chart/renderer`; persistence goes through the dashboard and Files endpoints above.

## 9. Gates

| Gate | What it pins | In the battery |
|---|---|---|
| `test/test_chart_chronological.py` | the shared date catalogue; a line orders its x chronologically, a column keeps the caller's order, an explicit `sort_by` wins, a non-date x is untouched | yes |
| `test/test_chart_only_query.py` | `Planner._is_chart_only_query`: chart follow-ups stay chart-only, a "show …" question with new content does not, the new rule only narrows the original | yes |
| `test/test_component_signals.py` | chart and dashboard declare each other in `conflicts_with`; a chart payload is recorded in the ledger under `component:chart:<id>` | yes |
| `test/test_answer_surface.py` | a chart row opens by default; `answer_surface` kind/word/open are honoured; component rows carry `data-spec` | yes |
| `test/test_file_store.py` (§F) | keeping a chart stores the PNG and the CSV of its own data as one pair | yes |
| `test/test_p2_components.py` | the `TDA_Charting` contract (required/all args) and argument validation | no |

No gate exercises the five-stage mapping pipeline stage by stage (`_sanitize_mapping` … `_positional_fallback`); it is covered only through end-to-end calls.
