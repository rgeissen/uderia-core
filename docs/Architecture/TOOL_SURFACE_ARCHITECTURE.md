# Tool Surface Architecture

How the platform decides which of an agent's enabled tools the model is **shown** on a turn:
every enabled tool (`all`), or only the tools that could answer this turn (`governed`, labelled
*Relevant per turn* in the editor) with the rest held behind a search. This is the live
contract for the Governed Tool Surface; the S6 entry in
`docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md` is its history.

**Last updated:** 2026-10-30

## 1. The contract

| # | Rule | Who owns it |
|---|---|---|
| 1 | **Shown, never permitted.** The surface decides what the model sees first. It never widens the profile's enabled set, and it is not the boundary: the enabled tools and Agent Authority are. | `tool_surface.select` · the chokepoints |
| 2 | **Authority first.** A tool Agent Authority denies is never offered and never findable. A denied tool the model asks for (by name or by search) is recorded as a finding. | `tool_surface.denied_names` · `note_denied_call` |
| 3 | **A held tool is never lost.** Everything held can be brought in by `TDA_FindTools`, with the same rankers. An agent on which the finder is not offered holds nothing. | `tool_surface.find` · `has_finder` |
| 4 | **Deterministic, zero tokens.** Selection is a rule over identifier tokens plus one bounded embedding rule. No LLM call decides the surface, and no rule is question-specific. | `tool_surface.score` · `semantic_pick` |
| 5 | **Never breaks a turn.** Every arming, re-selection, overlay and search path is fail-open to "every enabled tool", except the authority read, which fails **closed**. | every `try` in the module and its seams |
| 6 | **Every decision is explained.** Each tool carries the reason it is where it is, and the turn's stamp records what decided (words, meaning, finder). | `Surface.reasons` · `Surface.ranker` |

## 2. The control

The only control is per agent: `toolSurface.mode` on the profile, `all` | `governed`
(`tool_surface.profile_mode`; anything other than `governed` is read as `all`). **There is no
deployment switch** (removed Oct 2026; see the note in `core/config.py`).

- **Profile editor:** *Tools in context* select (`#profile-modal-tool-surface`, inside
  `#profile-field-tool-surface`), shown only when the agent uses MCP tools (`toolSurfaceField`
  in `configurationHandler.js`: `tool_enabled`, or `llm_only` with `useMcpTools`). Options are
  *All enabled tools* (`all`) and *Relevant per turn* (`governed`).
- **Defaults:** a stored agent without the key reads `all` (byte-identical to before). A **new**
  agent created in the editor starts on `governed`. The bootstrap Optimize agents in
  `tda_config.json` (`OPTIM`, `OPGGT`) ship with `toolSurface.mode: governed`.
- **Portable, unsigned:** `ConfigKeySpec(key="toolSurface")` in
  `core/profile_config_contract.py`. A pack carries the author's choice, and toggling it never
  moves the agent's signature, because it changes what is shown, not what is allowed.

Mode `all` arms nothing: `tool_surface.arm` returns `None`, no surface is ambient, no stamp is
written, and every seam below is a no-op.

## 3. The decision (`agent/tool_surface.py`, pure core)

`select(question, catalog, enrichment_names=, scope=, mode=, semantic=)` returns a `Surface`
with `offered`, `held`, `denied`, `widened`, `asked_denied`, `finds`, `reasons` and `ranker`.
The catalog is the agent's **enabled** set as `ToolInfo(name, category, description, scope)`:
`catalog_from_structured` for the Optimize lane (disabled tools excluded) and
`catalog_from_langchain` for the ReAct lane.

For each tool, in order:

| Step | Outcome | Reason on the chip |
|---|---|---|
| Authority denies it | `denied` | the authority's own reason |
| A `TDA_*` tool (framework or component) | `offered` | "framework / component tool — always offered" |
| **Backbone**: base name in `plan_compiler.DISCOVERY_TOOLS`, or a name matching the SQL execution pattern (`readquery`, `read_query`, `runquery`, `executequery`) | `offered` | "backbone — discovery and execution" |
| **Words**: `score(tool, terms) > 0` | `offered` | "addresses the question: …" (the hit tokens) |
| **Named category**: the question names the tool's category | `offered` | "its category (…) is what the question is about" |
| otherwise | `held` | "held — findable with TDA_FindTools" |
| **Meaning**: `semantic_pick` over the held set | moved to `offered` | "near in meaning to the question (0.xx)" |

If the mode is `governed` but `TDA_FindTools` is not in the catalog, nothing is held: every
non-denied tool is offered with "no Tool Finder on this agent — nothing is held".

### 3.1 The words ranker

- **Vocabulary** (`question_terms`): the question's tokens plus the leaf tokens of every entity
  or table the turn's KG enrichment named (`entity_names`, `scope_entities` and the keys of
  `table_columns`, read by `executor._surface_enrichment_names`).
- **Tokens** (`_tokens`): identifiers are split on `_` and camelCase (`dba_tableSpace` gives
  `dba`, `table`, `space`), lower-cased, at least 3 characters, minus a stop list (common verbs,
  quantifiers, superlatives and the generic data nouns `table`, `column`, `database`, `query`
  …); a trailing plural `s` is also folded.
- **Score**: `10 × name hits + 4 × category hits + 1 × description hits`.
- Tools are recognised by **base name** (`core/tool_identity.base_name`), so an additional
  server's `crm__base_readQuery` is backbone exactly like `base_readQuery`
  ([MULTI_SOURCE_ARCHITECTURE.md](MULTI_SOURCE_ARCHITECTURE.md)).

### 3.2 The meaning ranker (`agent/tool_semantics.py`)

Closes the paraphrase gap the words cannot ("how much disk is each table consuming" →
`dba_tableSpace`) without asking the model to search.

- **One rule**, `tool_surface.semantic_pick`: keep candidates whose cosine similarity is at least
  `max(FLOOR, RATIO × best)`, where `best` is the question's best match over the **whole**
  catalog, best first, at most `CAP`. Constants: `FLOOR = 0.18`, `RATIO = 0.80`, `CAP = 5`. The
  rule is relative because no absolute threshold separates hits from noise on the real catalog
  (`test/fixtures/tool_catalog_calibration.json`, 62 tools).
- **Text embedded**: `tool_text(name, category, description)` = the identifier split into words,
  the category, the description. The query is the question plus the enrichment's entity leaves.
- **Model**: the platform's RAG embedder (`APP_CONFIG.RAG_EMBEDDING_MODEL`, default
  `all-MiniLM-L6-v2`) through `SentenceTransformerProvider.get_cached`, shared with the retriever.
- **Never blocks a turn**: `warm()` resolves the model on a daemon thread (called at startup in
  `main.py` after the RAG retriever loads, and by the first governed turn). Until it is ready,
  `similarity` returns `None`, the words decide alone, and the stamp says
  `semantic_state: warming` (or `unavailable`).
- **Caching**: catalog vectors are keyed by a content signature of names and texts (a changed
  description re-embeds), at most 32 catalogs per process. An injectable embedder
  (`set_embedder`) makes the rule testable without a model.

## 4. The Tool Finder (`components/builtin/find_tools/`)

`TDA_FindTools(query)` is the surface's own search, a headless deterministic component
(`FindToolsHandler`, `metadata.suppress_inline`).

- **Offered only where it matters**: `is_profile_relevant` is true only for a governed agent, so
  every other agent never sees it. Like any component it can be disabled per agent, and then a
  governed agent holds nothing (§3).
- **`find`** searches the **held** set only (never the denied set), words first, then meaning
  (`semantic_pick` with the remaining cap), at most `MAX_WIDEN = 8` per call. Found tools move to
  `widened` for the rest of the turn. With nothing found it returns up to five `closest` held
  names so the model can rephrase; `denied_matching` names denied tools the query addresses
  (recorded in `asked_denied`, never returned).
- **Widening reaches the next prompt**: `run_find` calls the turn's `on_widen` hook, which on the
  Optimize lane rebuilds the STATE overlay (§5.1). The ReAct lane re-binds on every model call.
- Correctness lives in `instructions.json` (none / medium / heavy). By recorded design there is no
  companion skill, because the tool description is the whole contract.

## 5. Installing the surface per turn

The turn's surface is a context variable (`tool_surface._TURN`, a `TurnSurface`). The execution
seam resets it at the start of every turn (`execution_service.run_agent_execution`:
`_ts_mod.set_turn(None)`, released in `finally`), and each lane arms it with `arm(...)`. The
authority scope passed in is the ambient principal's (`executor._surface_scope`), so the surface's
denied set is exactly what the chokepoint would deny.

### 5.1 Optimize (`tool_enabled`)

1. **Armed before context assembly** (`executor._arm_tool_surface_optimize`, called from the
   executor before `_run_context_window_assembly`) over `STATE['structured_tools']`.
2. **A per-executor STATE overlay**, never a global write (`_apply_surface_overlay`):
   `ChainMap({structured_tools: offered ∪ widened, tools_context, prompts_context}, base)`, with
   both contexts rebuilt by `rebuild_tools_and_prompts_context` and installed on
   `executor.dependencies['STATE']`. `mcp_tools` is **not** filtered, so a held tool that is
   found, or that a replayed plan names, still executes. The base may already be the `@TAG`
   override's overlay; readers that need the override's slots read every depth
   (`mcp_adapter._overlay_map`).
3. **Re-selected after the KG enrichment** (planner, after `kg_enrichment`):
   `_reselect_tool_surface` re-runs `select` with the enrichment's vocabulary. The offered set can
   only **grow**, and the assembled `tool_definitions` contribution is refreshed
   (`_refresh_assembled_tool_definitions`) so the planner reads the current surface. A lane that
   skipped the pre-assembly seam is armed here instead.
4. **The `tool_definitions` context module** filters through `_apply_surface` and renders **full
   descriptions on every governed turn**. The names-only condensation applies only to the
   whole-catalog case after turn one.
5. **The prompt cache**: under a governed surface the strategic planner's `available_tools` is not
   declared stable, because the offered few differ per turn
   ([PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md) §3.1).

### 5.2 ReAct (`llm_only` with MCP tools)

Armed in `engines/conversation_engine.py` after the enrichment and before the agent binds, over
the LangChain tools (`catalog_from_langchain`, categories from `structured_tools`), then passed
as `tool_surface=` to `ConversationAgentExecutor`:

- the system prompt lists the offered and widened tools **with descriptions**, plus one line
  saying how many are held and that `TDA_FindTools` brings them in;
- the agent is a LangGraph **dynamic model**: on every call it binds offered ∪ widened, while the
  ToolNode holds **every** tool, so a found tool executes through the same guarded coroutine. A
  model that cannot bind the subset falls back to binding everything.

Focus (`rag_focused`) and Coordinate (`genie`) do not arm a surface of their own. A coordinator's
experts are agents, and each runs its own lane and its own surface.

### 5.3 The self-correction catalog follows the surface

`executor._get_correction_catalog` filters `structured_tools` through the same
`tool_definitions._apply_surface` before building the compact catalog
(`build_compact_tools_and_prompts_context`: one line per tool, the description's opening
statement capped at `COMPACT_DESCRIPTION_CHARS = 240`, argument names). The per-turn cache is
keyed by the shown set, so a tool found mid-turn appears in the next correction.

## 6. What is recorded and shown

- **Stamp**: `Surface.state()` (`mode`, the four sets, `asked_denied`, `finds`, `reasons`,
  `ranker` {lexical, semantic, floor, ratio, cap, finder, semantic_added, semantic_model,
  semantic_state}, `counts`) is persisted as `workflow_history[n].tool_surface` by
  `session_manager.update_last_turn_data` (`setdefault`; no surface means no key).
- **Live**: a `tool_surface` notification per change (`_broadcast`) and a collapsed
  `Tool Surface` system step (`executor.surface_event`: "offered N of M tools; …").
- **UI**: the Live Status card `_renderToolSurfaceDetails` (`ui.js`): chips per set with the reason
  as tooltip, a *Ranked by* row that names words, meaning (model and additions, or *warming up*)
  and the finder, and a warn row for a denied tool asked for. The reload path synthesises it from
  the stamp (`eventHandlers.js`). The living answer shows *Found N more tools* and *Asked for a
  tool it may not use* chips.
- **Assurance**: the `tool_surface` point on BOUNDED (`assurance_points._read_tool_surface`):
  `na` without a stamp, `pass` naming the split, `warn` when the model asked for a denied tool.
  See [ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md).

## 7. Files and gates

| File | Role |
|---|---|
| `src/trusted_data_agent/agent/tool_surface.py` | the pure decision, the finder search, the per-turn runtime, stamp and broadcast |
| `src/trusted_data_agent/agent/tool_semantics.py` | the meaning ranker: constants, embedder lifecycle, catalog cache, `similarity` |
| `src/trusted_data_agent/core/tool_identity.py` | `base_name` / `qualify` for multi-server tool names |
| `components/builtin/find_tools/` | `TDA_FindTools`: manifest, instructions, handler |
| `components/builtin/context_window/modules/tool_definitions/handler.py` | `_apply_surface`, full descriptions on governed turns |
| `src/trusted_data_agent/agent/executor.py` | `_arm_tool_surface_optimize`, `_apply_surface_overlay`, `_reselect_tool_surface`, `_get_correction_catalog`, `surface_event` |
| `src/trusted_data_agent/agent/planner.py` | the post-enrichment re-select; `_strategic_cache_prefix` |
| `src/trusted_data_agent/agent/engines/conversation_engine.py` · `agent/conversation_agent.py` | ReAct arming, prompt listing, dynamic model |
| `src/trusted_data_agent/agent/tool_enforcement.py` | the chokepoint's `note_denied_call` |
| `static/js/handlers/configurationHandler.js` · `templates/index.html` | the *Tools in context* control |

Gates (all in `test/trust_battery.sh`):

- `test_tool_surface.py`: the pure core, both rankers with an injected embedder, the finder
  guard, the Optimize overlay, the ReAct dynamic model, the component, the stamp, the assurance
  point and the contract (110 assertions at writing).
- `test_tool_semantics.py`: the calibration against the real 62-tool catalog and the real model
  (25 assertions).
- `test_tool_surface_baseline.py`: the freeze, so mode `all` changes nothing.
- `test_tool_surface_ui.mjs`: the Live Status card, the wiring, the editor control (27 assertions).
- `test_tool_identity.py` (with `tool_identity_gate.py`): every name rule reads the base name.
