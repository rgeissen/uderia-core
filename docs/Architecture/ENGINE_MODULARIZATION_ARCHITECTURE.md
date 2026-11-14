# Engine Modularization Architecture

## Overview

Prior to this refactor, all four IFOC profile type execution paths lived inside a single `PlanExecutor.run()` method in `executor.py` (~6,800 lines). The engine modularization extracts each path into a dedicated, independently maintainable **engine class** wired through a lightweight **EngineRegistry**, following the [Method Object](https://refactoring.guru/replace-method-with-method-object) pattern.

**Key invariant:** `executor.py` remains the single shared infrastructure hub (session management, LLM handler, MCP adapter, context window). Engines receive the initialized `PlanExecutor` instance as their execution context — they do not duplicate any infrastructure.

---

## Package Structure

```
src/trusted_data_agent/agent/engines/
├── __init__.py           # Import-order-controlled registration; exports all engine classes
├── base.py               # ExecutionEngine ABC
├── registry.py           # EngineRegistry (register decorator + resolve)
├── ideate_engine.py      # IdeateEngine     — llm_only
├── focus_engine.py       # FocusEngine      — rag_focused
├── coordinate_engine.py  # CoordinateEngine — genie
├── optimize_engine.py    # OptimizeEngine   — tool_enabled
└── conversation_engine.py# ConversationEngine — llm_only + useMcpTools / component tools
```

---

## Core Abstractions

### `ExecutionEngine` (base.py)

Abstract base class. Every engine declares a `profile_type` class variable and implements `run()`.

```python
class ExecutionEngine(ABC):
    profile_type: ClassVar[str] = ""

    @classmethod
    def applies_to(cls, profile: Dict[str, Any]) -> bool:
        """Override for complex eligibility (e.g. llm_only + useMcpTools routing)."""
        return bool(cls.profile_type) and profile.get("profile_type") == cls.profile_type

    @abstractmethod
    async def run(self, executor: "PlanExecutor") -> AsyncGenerator[str, None]:
        """Yield SSE-formatted event strings. Must end with a final_answer event."""
```

### `EngineRegistry` (registry.py)

Class-level list of all registered engine classes. Last-registered wins (specialisations override base engines).

```python
@EngineRegistry.register          # decorator fires at import time
class MyEngine(ExecutionEngine):
    profile_type = "my_new_type"
    ...
```

Registration happens automatically when `engines/__init__.py` imports each engine module. No manual registration step.

---

## Registered Engines

| Engine | Profile Type | IFOC Label | Colour |
|--------|-------------|-----------|--------|
| `IdeateEngine` | `llm_only` | Ideate | Green |
| `ConversationEngine` | `llm_only_with_tools`¹ | Ideate + tools | Green |
| `FocusEngine` | `rag_focused` | Focus | Blue |
| `OptimizeEngine` | `tool_enabled` | Optimize | Purple |
| `CoordinateEngine` | `genie` | Coordinate | Orange |

¹ `ConversationEngine.applies_to()` is overridden: it claims `llm_only` profiles where `useMcpTools=True`. The `has_component_tools` case requires a runtime check (component manager) and is detected in `executor.run()` before the registry is consulted.

---

## Dispatch Flow

`executor.run()` dispatches to engines **directly** (not via `EngineRegistry.resolve()`). The runtime conditions that determine which engine runs were already computed earlier in `run()`, so the dispatch is a series of `if/elif` guards that delegate to the appropriate engine:

```python
# executor.py — simplified dispatch sequence
if is_llm_only:
    async for event in IdeateEngine().run(self): yield event; return

if is_conversation_with_tools:
    async for event in ConversationEngine().run(self): yield event; return

if is_rag_focused:
    async for event in FocusEngine().run(self): yield event; return

# Default: tool_enabled → OptimizeEngine
async for event in OptimizeEngine().run(self): yield event
```

Every engine stream is wrapped in `executor._with_progress(...)`. A session-primer *briefing* turn (`primer.is_briefing_turn`) on an `llm_only` or `tool_enabled` profile is routed to `IdeateEngine`. `ConversationEngine` is also reached through the legacy `PlanExecutor._execute_conversation_with_tools()` shim, which delegates to it.

Genie profiles are dispatched differently: `execution_service.py` calls `CoordinateEngine().execute_genie()` directly (not via `run()`). A future unification phase will route genie through `EngineRegistry.resolve()` → `CoordinateEngine.run()` so it benefits from the same PlanExecutor safeguards as other profiles.

---

## What Each Engine Does

### IdeateEngine (llm_only)
Direct LLM conversation. Optional knowledge retrieval when `knowledgeConfig.enabled=True`. No MCP tools, no strategic planning. Single LLM call per turn. Extracted from `PlanExecutor.run()` (formerly ~670 lines).

### ConversationEngine (llm_only + tools)
LangChain ReAct agent path. Wraps `_execute_conversation_with_tools()`. Used when `useMcpTools=True` or active component tools are present. Enables tool calling without the multi-phase planning overhead of OptimizeEngine. Extracted as Phase 5 (~715 lines).

**Two deterministic recoveries sit after the ReAct loop (Oct 2026, prod session 86ac6b28 — a weak tool-calling model on a data question).** *(1) A leaked SELECT becomes a query:* when the whole answer is one read-only statement and no data tool ran, `_recover_leaked_sql` (`agent/leaked_sql.py`) runs it through the bound read tool — the same guarded tool object a model call reaches, so Agent Authority, preflight and the rewrites apply — emits the loop's own tool events (Live Status, the honesty gate, OBDA-READ and the eval scorers see a real data call) and answers with the rows; zero LLM tokens. A write, a second statement, a block inside a longer explanation, or a turn that already ran a data tool is left alone. *(2) A record with no query behind it is named:* the honesty gate's `unsourced_record` kind (`agent/honesty/gate.py:assess_unsourced_record`) fires when a tool-enabled turn ran no data query and the answer presents a record (table rows, `**Field:** value`) whose values appear in nothing the model was given — the knowledge block, the conversation, any tool result; the engine passes that context as `support_text` only when a data tool is bound. Observe records it, annotate appends the disclosure naming the values, enforce replaces the record (and parks it under `review`). The grounding gate abstains on such a turn (nothing to ground against), which is why this reading exists. Gates: `test/test_leaked_sql_recovery.py`, `test/test_honesty_gate.py` §unsourced.

### FocusEngine (rag_focused)
Mandatory knowledge retrieval from configured collections followed by LLM synthesis. No MCP tools, no Planner/Executor pipeline. When component tools are active for the profile, the synthesis is agent-based: a `ConversationAgentExecutor` over the component tools (e.g. charting the retrieved knowledge), its history built by `agent_history.build`. When the repositories hold nothing for the question but the turn carries an attachment, Focus answers from the attachment (`PlanExecutor.attached_material`). Extracted from `PlanExecutor.run()` (formerly ~1,073 lines).

### CoordinateEngine (genie)
Multi-expert coordinator. Wraps `GenieCoordinator` to route queries to child (slave) profiles and synthesise their responses. Extracted from `execution_service.py` (formerly inline `execute_genie()` function). Also exposes `execute_genie()` as a public method for the current dispatch path in `execution_service.py`.

### OptimizeEngine (tool_enabled)
Full Planner/Executor pipeline: strategic planning → the 19-pass refinement pipeline → phase-by-phase tactical execution (FASTPATH where every argument is known) → self-correction → an honest phase failure when a phase exhausts its attempts (the former LLM recovery replan was removed — see [FUSION_OPTIMIZER_ARCHITECTURE.md §7](FUSION_OPTIMIZER_ARCHITECTURE.md)). Also hosts the efficiency re-plan loop and the opt-in plan gate. The most complex engine. Extracted from `PlanExecutor.run()` (formerly ~1,085 lines). **executor.py was reduced from ~6,800 → ~3,160 lines** by the modularization; shared seams added since (governed stream, resilience, tool surface, outcome loop, …) have grown it back to ~5,600 lines, and the engines package to ~6,300.

---

## Shared turn rules (Oct 2026) — one history, one failure sentence, every engine

- **One conversation history.** `agent/agent_history.build` is the ONLY builder of the history a
  LangChain agent receives — the ReAct lane (`conversation_engine` / `conversation_agent`),
  Focus's tool-assisted synthesis (`focus_engine`) and the coordinator (`coordinate_engine`,
  `genie_coordinator` via `agent_history.role_of`). One role rule (`model` and `ai` are the
  assistant — Focus had dropped every stored answer under Gemini's `model` role), the priming pair
  and `isValid: false` turns are dropped, only the current question is removed, and Turn Summaries
  mode (`disabled`) is honoured on all three lanes. Gate: `test/test_agent_history.py`.
- **One failure sentence.** A turn the provider failed delivers `agent/turn_failure.answer(exc)` —
  the failure class read through the resilience envelope (`credits`, `auth`, `rate_limit`,
  `timeout`, …) and what the user can do, never `str(exc)` — on the Conversation, Focus, Optimize
  and Coordinate failure paths (`genie_coordinator`, `conversation_agent` too). The turn is recorded
  as failed and JUDGED's `clean_termination` grades it. Gate: `test/test_turn_failure.py`; detail
  in [ANSWER_GATES_ARCHITECTURE.md](ANSWER_GATES_ARCHITECTURE.md).
- **A fire drill.** With `TDA_FAULT_INJECT=1` (test server only) a model call marked
  `[fault:402|401|429|503|timeout]` raises the real-shaped provider error inside the handler's
  attempt loop and every LangChain model's generation calls (`llm/fault_injection.py`), so the
  retries, breaker, fallback and the sentence above are exercised on every engine. Gate:
  `test/test_fault_injection.py`.
- **Focus's no-knowledge answer runs the trust tail** (outbound guard, assurance stamps, provenance
  seal) like any answer. Gate: `test/test_focus_no_results_trust_tail.py`.

## The Governed Stream (S1) — one delta sink, every engine

The answer's text reaches the UI **as the model writes it** through ONE object per turn, `agent/answer_stream.py:AnswerStream`, created at the execution seam (`execution_service.run_agent_execution`, armed only when the caller is a live UI stream AND `APP_CONFIG.ANSWER_STREAMING` is on — off by default ⇒ every provider takes its non-streaming API, byte-identical) and handed down: the four `PlanExecutor` engines read it as `executor.answer_stream`, the genie coordinator as its own argument, the Optimize final report through the ambient `answer_stream.current()` contextvar (the adapter is too deep to be handed the object).

| Engine | What streams | The seam |
|---|---|---|
| Ideate | the answer call | `ideate_engine.py` → `_call_llm_and_update_tokens(on_delta=executor.answer_stream)` |
| Conversation (ReAct) | `on_chat_model_stream` chunks; a message ending in tool calls is a DECISION, not the answer → `reset()` | `conversation_agent.py` |
| Focus | the synthesis call; the tools path through its ReAct agent | `focus_engine.py` |
| Optimize | the final report's `direct_answer` field, extracted from the JSON as it is written (`JsonFieldSink`) | `mcp_adapter/adapter.py:_invoke_final_report_task` |
| Coordinate | the coordinator's synthesis; a routing message → `reset()`; a pass-through expert answer arrives whole with `final_answer` | `genie_coordinator.py` |

`llm/handler.py:call_llm_api(on_delta=…)` streams per provider (`llm/streaming.py`: Anthropic `messages.stream`, OpenAI-compatible `stream=True` with usage learned-on-refusal, Google `generate_content_stream`, Ollama NDJSON, Bedrock-Anthropic `invoke_model_with_response_stream`; every helper returns the SAME response shape the non-streaming branch reads, so the parsing stays single-source); a provider with no stream hands the sink the whole text once and says so (`provider_streamed: false`). A retry after an overload error `reset()`s the sink first.

**The guard is the outbound guardrail's own resolution** (`guardrails/runtime.stream_policy`): only an acting mode (enforce / review) alters the stream — PII redacted on a sliding window with a release lag (`StreamGuard`, agreement with `redact_pii` gate-asserted), toxicity withdraws the provisional message. Observe/annotate leave the stream untouched, exactly as they leave the final answer.

**A stop keeps what arrived:** `execution_service` catches `CancelledError`/`Exception` after the seam, persists the partial turn (the executor's own `_save_partial_turn_data`, now idempotent and carrying the partial text; `persist_partial_turn` for genie) and emits `final_answer{partial:true}` before re-raising. Frontend: `ui.js` provisional bubble (batched markdown, follow lock, "new text" pill, once-only announcements, Stop where Send was); `eventHandlers.js` routes `answer_delta` first in the chain. **S1.1 the Living Answer** (`static/js/handlers/livingAnswer.js`, a plain script armed by `task_start.stream`): the same bubble with a lifecycle — born at task_start with ONE work line + a phase hairline, the curated steps as rows of a timeline receipt that fold into that line at the first token, a stable block model for the reveal, the checks as the receipt's last rows, the chrome transplanted onto the authoritative message at settle with the marks landing in place and the ONE verdict chip on the action row (Verified · N findings · Not checked, derived once by `verdictOf`) — every motion keyed off event arrival; ui.js's provisional bubble streams INTO it (`contentHost` / `renderText` / `textDone` seams) and is byte-identical without it. **One reducer, two moments:** every engine's `final_answer` frame carries `checks` + `duration_ms` (`agent/answer_checks.py`) so the settle reads the same facts a reload reads from the persisted stamps, and `restore(bubble, turn)` (called from `sessionManagement` under the server's `living_answer` flag) replays the turn's recorded events through the same reducer — a reloaded answer is byte-equal to the live one. Gates: `test_answer_stream_baseline.py` (the freeze), `test_answer_stream.py`, `test_answer_stream_dom.mjs`, `test_living_answer_dom.mjs`; bench: `test/performance/conversation_lane_bench.py`. Design: `docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S1`.

## The Resilience Envelope (S4) — one policy, every lane

What happens when the model cannot answer as configured is decided by ONE pure policy, `llm/resilience.py`, and performed by the two actors every LLM call passes through: `llm/handler.py:call_llm_api` (the handler lanes — Ideate, Focus's synthesis, Optimize's planner/tactical/report/utility calls, session naming, judges) and `llm/resilient_chat.py` (the LangChain lanes — the ReAct agent, Focus's tools path, the genie coordinator — enveloped in place at construction by `create_langchain_llm`). Under the master `APP_CONFIG.RESILIENCE_ENVELOPE` (env `TDA_RESILIENCE_ENVELOPE`, default off ⇒ the pre-S4 retry loop and the plain provider classes, byte-identical — `test_resilience_baseline` executes that loop and pins it):

| Act | The rule (`llm/resilience.py`) | Handler lanes | LangChain lanes |
|---|---|---|---|
| **retry** | `classify(exc)` → retryable (rate limit · overloaded · server · timeout · connection) vs terminal (auth · bad request · context too long · not found · UNKNOWN — retrying a bug is noise); `backoff` honours the provider's `retry-after` up to 60 s, else base·2^(n−1) capped at 30 s | the attempt loop | `_agenerate` / `_astream` (an opening error only — a stream that yielded is never re-run) |
| **continuation** | `finish_kind` = TRUNCATED (`max_tokens` · `length` · `MAX_TOKENS`) and a non-empty partial ⇒ the partial rides as the assistant's prior turn + `CONTINUE_INSTRUCTION` (continue exactly, never restate); bounded to `MAX_CONTINUATIONS` = 2; `join_continuation` trims a restated tail | every provider branch builds its messages from `history_source` + `prompt`, so ONE substitution serves them all | a FINAL answer only (a message ending in tool calls is a decision) |
| **fallback** | the primary exhausted (retryable, out of attempts) or its breaker OPEN ⇒ the profile's **signed** fallback — `resolve_fallback` accepts ONLY `llmFallbackConfigurationId` naming a configuration the user owns, never the primary, never anything improvised; a fallback never falls back (depth guard, both sites) | `_call_fallback` = the same `call_llm_api` at depth 1, so the 5-tuple names the model that SERVED and the price follows | the envelope re-binds the REMEMBERED raw tools in the fallback provider's format and calls its `_agenerate`/`_astream` directly (no nested LangChain run ⇒ the lane's callbacks see ONE call); `uderia_served_by` on the message drives the price. With the breaker open at construction the lane is BUILT on the fallback |
| **refusal** | `finish_kind` = REFUSED (`refusal` · `content_filter` · `SAFETY` · `RECITATION` · …) ⇒ `ProviderRefusal`, terminal, never retried, never handed to a fallback (a second model must not override a safety decision) | `executor._call_llm_and_update_tokens` delivers `refusal_answer()` — an honest answer naming the category, gated and sealed like any answer, 0 tokens | an honest `AIMessage` / chunk |

**The breaker** (`core/provider_health.py`, process-wide, one per provider): 3 consecutive RETRYABLE failures inside 120 s open it for 60 s (doubling per re-trip, capped at 600 s); half-open admits ONE probe; a success closes it. Only retryable failures count — a bad request or a refusal is the call's fault, not the provider's health. Open + a fallback ⇒ the fallback serves IMMEDIATELY (no timeout paid); open + no fallback ⇒ the call is still attempted, once, no backoff — a breaker never refuses service for lack of an alternative. `GET /v1/llm/provider-health` serves the snapshots; a transition is broadcast (`provider_health`) to the turn's owner and the header strip renders ONLY while a provider is degraded.

**Every act is a record.** `agent/resilience_envelope.py:TurnResilience` (a contextvar armed at the execution seam with the fallback resolved ONCE) records each `RecoveryStep`, broadcasts it live as an `llm_recovery` Live Status step over the notification channel (the judge-step precedent — the handler has no request stream), and `ProvenanceChain.finalize()` seals the steps as `llm_recovery` (timestamp-free content, `verify_content` re-derives them from the persisted `resilience` stamp); `session_manager.update_last_turn_data` stamps `turn["resilience"]` (steps · counts · primary · `served_by` · refusal) — absent when nothing was recovered. `served_model()` is the one rule every engine's `models_used` record reads. The assurance mechanism `llm_recovery` reads at **SIGNED** (a fallback is DECLARED deployment drift for the turn — the fallback identity is itself in the deployment half of the trust envelope); the answer wears a `served by <model>` chip when a fallback served.

**Honest limits.** A fallback call inherits the primary instance's streaming mode (an MLX primary is built `streaming=False`, so its fallback answers arrive whole); Anthropic's native server-side refusal fallbacks are not used (Uderia's refusal handling is uniform across providers); the Optimize engine pre-records the configured model in `models_used` before its first call (the pre-existing header contract), so a session a fallback served lists both. Gates: `test_resilience_baseline.py` (the freeze), `test_llm_resilience.py`, `test_resilience_ui.mjs`; bench: `conversation_lane_bench.py` (`answered` · `recovery_steps` · `retries` · `served_by_fallback`). Design: `docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S4`.

## The Visible Budget (S5) — one budget, every data-running lane

How much ONE turn may spend is declared on the agent (`profile.turnBudget` — `steps` · `tokens` · `seconds`, each optional; signed as posture, every unit a ceiling under the downgrade guard) and armed per turn at the execution seam (`execution_service.run_agent_execution` → `turn_budget.arm`, a contextvar released in `finally`) under the deployment switch `APP_CONFIG.TURN_BUDGET` (default off ⇒ byte-identical: no note, no refusal, no stamp). The pure core is `agent/turn_budget.py` (parse · remaining · note · refusal · finish line — no I/O, the clock injected); the runtime is the per-turn `TurnBudget` the seams below record into.

| Seam | Lane | What it does |
|---|---|---|
| `tool_enforcement._make_guarded_coroutine` | ReAct (Ideate+tools · Focus tools · genie experts) | BEFORE the tool: `tool_gate` — a refusal dict once a unit is exhausted (recovery-shaped: the tool, the unit, *answer now with what you have*), else the step is **claimed** (reserved at the gate, so N parallel tool calls under `asyncio.gather` cannot overrun it — the (budget+1)th in the batch is refused). AFTER: `after_tool` appends the `[Budget]` note to the result (plain · nearly used at 75% · the finish instruction once exhausted). `TDA_*` tools are never counted and never refused — the report must still be written. |
| `mcp_adapter.invoke_mcp_tool` (success seam) | Optimize | `after_tool` counts the step (the plan is fixed — no refusal is needed here). |
| `PlanExecutor` phase loop → `_budget_skip(group)` | Optimize | With a unit exhausted every DATA phase of the group is dropped; with N steps left only the first N data phases of a parallel group run. A synthesis phase (`TDA_FinalReport` · `TDA_ContextReport` · `TDA_LLMTask`) is always kept. The skip is recorded (the trace) AND yielded live as a `turn_budget` step — never silent. |
| `adapter._invoke_final_report_task` | Optimize | `report_note()` — the finish instruction appended to the report prompt once exhausted, so the report says what is left. |
| `executor._call_llm_and_update_tokens` · ReAct + genie accounting | all | `record_tokens(in + out)` — the tokens unit. |
| ReAct hard stops (cost cap · input-token cap) | ReAct | `mark_killed(reason)` — a kill is recorded as a kill, never as graceful. |
| `executor._apply_turn_budget_finish` | Optimize · Conversation (beside `_run_compute_lineage`) | the quiet finish line under the answer, text + html (`role="note"`). |
| `session_manager.update_last_turn_data` | all | `turn_data.setdefault("budget", stamp)` — the reload/audit record. |
| `execution_service` → recommender | all | the stamp rides the final payload → `derive_render_flags.stopped_early` → the **Continue where it stopped** chip, before the component filter. |

**Every state change broadcasts** a `turn_budget` notice over the per-user notification channel (the judge-step precedent — the chokepoints have no request stream); the header bar (`budgetBar.js`) renders it session-guarded, one real `progressbar` per unit with `aria-valuetext` in words, colour = attention (quiet · nearly used · exhausted), the ending in words. On reload the bar is restored from `turnData.budget` and, where the lane logged no skip step, the `turn_budget` step is synthesized. The mechanism `turn_budget` at BOUNDED owns `budget_adherence` (1.0 / 0.5 / 0.0 / abstain).

**Honest limits.** A budget bounds what the platform can see: a single tool call's own duration is not interruptible (the seconds unit is read at the next boundary); the hard stops (cost cap, input-token cap, iteration cap, call timeout) remain the last line above the budget and are reported as a kill when they fire first.

## The Governed Tool Surface (S6) — only the tools that could answer, on every lane

What the model is SHOWN first is decided by ONE pure selector, `agent/tool_surface.py:select` — the discovery/execution backbone (`plan_compiler.DISCOVERY_TOOLS` + the SQL execution tool) and every `TDA_*` tool always offered; a tool whose identifier tokens (name · category · description) overlap the question's vocabulary ∪ the KG enrichment's entity/table leaves offered; a category the question names lifted whole; the rest **held**; Agent Authority's denials applied first and never offered nor findable. Per turn under `APP_CONFIG.TOOL_SURFACE` and `profile.toolSurface.mode == "governed"` (both required; off ⇒ byte-identical, frozen by `test_tool_surface_baseline`). The ambient `TurnSurface` contextvar is armed by each lane and released by `execution_service` in `finally`.

| Seam | Lane | What it does |
|---|---|---|
| `executor._arm_tool_surface_optimize` (BEFORE `_run_context_window_assembly`) | Optimize | decides the surface over `STATE['structured_tools']` and installs a per-executor STATE overlay (the @TAG-override ChainMap precedent): `structured_tools` filtered to offered, `tools_context` rebuilt from it; `mcp_tools` is NOT filtered, so a found or plan-named held tool still executes. |
| `tool_definitions` context module → `_apply_surface` | Optimize | renders only offered ∪ found (the module runs at priority 85, before the KG module at 72 — so the pre-assembly surface is question-only). |
| `planner` after the KG enrichment → `executor._reselect_tool_surface` | Optimize | re-runs the selection with the enrichment's vocabulary — offered can only GROW — rebuilds the overlay and re-renders the cached `tool_definitions` contribution so the ContextBuilder hands the planner the current surface. |
| `conversation_engine` after the KG enrichment → `ConversationAgentExecutor(tool_surface=)` | ReAct (Ideate+tools · Focus tools · genie experts) | the agent is built on a LangGraph **dynamic model** `(state, runtime) → llm.bind_tools(offered ∪ found)` while the ToolNode holds every tool — a held tool's schema never enters the prefix until it is found, and once found it executes through the same guarded coroutine. The prompt's AVAILABLE TOOLS mirrors the surface and names the held count. |
| `TDA_FindTools` (`components/builtin/find_tools`, headless, `is_profile_relevant` = the agent's `toolSurface.mode` is `governed` — no deployment switch) | both | `tool_surface.run_find` — searches the held set with the same scorer, widens the surface (recorded), reports a denied match as a named finding, returns the closest held names when nothing matches. |
| `session_manager.update_last_turn_data` | all | `turn_data.setdefault("tool_surface", stamp)`. |
| `executor.surface_event` | both | the *Tool Surface* Live Status step (recorded in the trace + yielded live). |

**The measurement that moved** (`conversation_lane_bench.py`, Optimize on a 38-tool catalog, mode all → governed): the strategic-planning call's input tokens **15.5k → 13.8k (−12%)**, the turn's input tokens 17.5k → 15.8k (−9%), `answered` and `numeric_fidelity` flat; a space question surfaced `dba_tableSpace` by identifier overlap and the planner chose it first. On a profile with one enabled MCP tool the surface is the whole catalog (honest: there is nothing to hold).

**Honest limits.** The pre-assembly selection is question-only (the KG module renders after `tool_definitions`); the enrichment-driven widening lands at the planner, so the `tool_definitions` contribution is re-rendered rather than re-budgeted. Two rankers decide (Oct 2026): the identifier-token scorer is authoritative and explainable word by word; a SECOND ranker, `agent/tool_semantics.py`, embeds the question (plus the entities the ontology named) against each tool's identity text with the platform's own RAG embedder and widens the offered set under ONE rule, `tool_surface.semantic_pick` — a floor (0.18), a share of the question's own best match (80%), a cap (5) — calibrated on the real 62-tool catalog (`test/fixtures/tool_catalog_calibration.json`, gate `test_tool_semantics.py`): an absolute cosine threshold does not separate (the right tool reads 0.20 on one paraphrase, noise 0.31 on another). A semantic addition carries its score as its reason on the chip; the stamp's `ranker` names the model and its state. A cold model never waits on a turn: the embedder is resolved on a background thread (`warm()` at startup) and until it is ready the words stand alone, stamped `semantic_state: warming`. Under a governed surface the `tool_definitions` module renders FULL descriptions on every turn (a names-only list is what broke a weak tool-calling model); names-only condensation remains the whole-catalog case after turn one. `TDA_FindTools` searches the held set by both rankers. **The finder is the surface's own search, not a separate capability:** a governed agent on which it is not offered holds NOTHING (every tool offered, the reason on each chip) — a held tool with no way back would be a lost tool — and the pack helper never defaults it off. There is no deployment switch: `toolSurface.mode` (editor: *Tools in context* — All enabled tools / Relevant per turn; a NEW agent starts on Relevant per turn, a stored agent keeps its own) is the only control, and `all` is byte-identical to a surface-less turn.

## The Outcome Loop (S7) — the gate's finding goes back to the model once, on every lane

A draft the grounding gate flags with a **fixable deterministic finding** is revised ONCE before delivery, and the same gate grades the revision. ONE pure module, `agent/outcome_loop.py`: `findings_from_event` reads the gate's own event (`unsupported_claims` · `answer_oos_refs` + `scope_databases` · `ungrounded_numbers`; a judge's ruling is a verdict, not a span, and is never revisable); `should_revise` (never on a `block`; the switch; the bound); `revision_prompt` (the raw draft + a note naming each finding, its spans and its fix — never a rubric); `unwrap_revision` (the echoed framing — the prompt's own `---` delimiters, a fence, a label — stripped before the re-grade, inner rules kept); `run_loop(first_text, first_event, profile, question, grade, revise, …)` — the driver every lane calls, returning the delivered `(text, event)`: the revision's gated text + ITS event when it cleared, else the annotated first draft. Per turn under `APP_CONFIG.OUTCOME_LOOP` and `profile.outcomeConfig.enabled` (both required; off ⇒ byte-identical, frozen by `test_outcome_loop_baseline`). The `TurnOutcome` contextvar (drafts · findings · the revision's own cost) is set by `execution_service` and released in `finally`.

| Seam | Lane | What it does |
|---|---|---|
| `executor._run_grounding_gate_full` | Ideate+tools · Focus · Conversation · Optimize | the ONE grounding entrypoint: the gate becomes a `_grade` closure, `run_loop` runs it over the draft and — when a revisable finding stands — over the revision; `_outcome_revise` is ONE `_call_llm_and_update_tokens(reason="Outcome Revision", disabled_history=True)` billed at the turn's accounting seam; `_outcome_event` records the step in the trace and pushes it over the notification channel. |
| `coordinate_engine` → `_genie_grade` / `_genie_revise` → `GenieCoordinator.revise_text` | Coordinate | the same loop over the coordinator's synthesized answer; the reviser priced through the coordinator's own `_pc.priced` seam, tokens recorded against the turn budget, a `genie_llm_step` "Outcome Revision" emitted. |
| `session_manager.update_last_turn_data` | all | `turn_data.setdefault("outcome", stamp)` — only when a revision ran. |
| `provenance.finalize` / `verify_content` | all | every draft sealed as an `outcome_draft` step (`draft_content` is the ONE content function — `outcome_draft|which|kind:spans|text`), re-derivable from the stamp. |
| `assurance_points._read_outcome_loop` | all | the `outcome_loop` mechanism at GROUNDED (class control, power alter, owns nothing): cleared → pass · acted; not cleared → warn · acted, *"never a second silent try"*. |

**The measurement that moved** (`conversation_lane_bench.py`, react + optimize with the gate armed (`minNumericFidelity: 0.9`) and a question inviting a figure no result holds, loop off → on): the **delivered-flagged rate** — the share of delivered answers the gate annotated — Optimize **0.33 → 0** (every revision cleared), react 0.2 → 0.2 (one cleared, one honestly not: a computed total the results do not hold stays annotated, not hidden); revision cost ≈ $0.001 per revised turn; `answered` and `numeric_fidelity` flat.

**Honest limits.** The loop is only as good as the gate it borrows: a revision clears when the gate finds nothing revisable, so a figure the model picks from the results counts as grounded whatever it means (the gate's definition, not the loop's). The revision sees the draft and the note — not the tool results again — so it can remove or qualify, never re-fetch; a finding that needs new data is not revisable (the honest annotated draft is delivered). On the Coordinate lane the gate rarely has groundable content (the experts ran the tools), so the loop is usually inert there by the gate's own abstention.

## Regenerate as an Experiment (S8) — a second answer, and the reason to prefer it

A regenerate is a **scored sibling**: a new turn under the SAME user message (no second user message), run by any engine through the ordinary execution path, scored by the evaluation layer like any turn, compared by its verdicts; exactly one sibling of a set is **adopted** — the one the conversation continues from. An edit-and-resend is a **branch**: the edited question becomes a branch head, the turns it displaces stay in the record, the view follows the active branch. ONE pure module, `agent/turn_lineage.py`: `build_relation` (validate a request against the session; resolve the root's query, the number the new record takes, the branch it belongs to), `TurnRelation` in a task-local contextvar armed at the execution seam, the readers (`sibling_root` · `sibling_set` · `branch_view` · `effective_adopted`), the acts (`apply_adoption` · `register_head` · `switch_branch`) and ONE validity rule (`sync_validity`). Under `APP_CONFIG.TURN_LINEAGE` (off ⇒ byte-identical, frozen by `test_turn_lineage_baseline`).

**The data — all additive, every reader tolerant of its absence:** turn record `sibling_of` · `adopted` · `branch_of` · `branch`; message `sibling_of` / `branch_of`; session `branches {active, heads:[{turn, of, parent}]}`.

| Seam | Lane | What it does |
|---|---|---|
| `execution_service.run_agent_execution(turn_relation=)` | all five | builds + arms the relation from the session (a regenerate runs the ROOT's query with NO user message, the root's attachments/extensions/skills carried; an edit runs the new text as a branch head); released in `finally`; the payload carries `lineage`. |
| `session_manager.add_message_to_histories` | all | an edit's head is REGISTERED before its message lands (the displaced turns are already out of the view it joins); a sibling's assistant message takes the sibling's OWN turn number and is INSERTED beside its question in BOTH histories (the model's context stays alternating); a user message opens the turn after the HIGHEST number, not the last message's. |
| `session_manager.update_last_turn_data` | all | stamps the record's lineage keys BEFORE the append; a regenerate is adopted on arrival AFTER it (only validity moves — every sibling stays). |
| `session_manager.toggle_turn_validity` | all | matches messages by their turn number (a sibling can sit mid-history) — the assistant-count walk is only the fallback for a history written before turn numbers. |
| the eval hook | all | dispatched unconditionally — a sibling is scored exactly like any turn (gate-asserted on the AST). |
| REST `_start_query_task` | — | the ONE task starter behind `/query` and the lineage endpoints; the public `/query` never reads a client relation. |

**ONE mechanism for "not in the model's context"** — the existing `isValid` flag every history reader already honours (the purge toggle's flag): a non-adopted sibling and a displaced branch turn are invalid for exactly the reason a purged turn is. `sync_validity` recomputes only the turns it is given (a purge on an untouched turn survives a switch) and a turn's sibling set with it: a root's USER message follows the view alone (the question is shared by every sibling); assistant messages and the record follow the adopted sibling. **A sibling travels with its question** — it inherits the root's branch and the branch cut-off is judged on the root's number — so a regenerate made after a branch point still shows wherever its question does; a record that predates that rule falls back to the root standing in (never a question with no answer).

**The view.** `branch_view(head)` = the head's own turns, then its parent branch cut at the edited turn, recursively to the root — deterministic from the heads alone. The frontend renders only the view (`sessionManagement.js` skips displaced turns), only the ADOPTED sibling as a bubble, and mounts the experiment card under it (`turnLineage.js`, a plain script; the verdict strips are drawn by the Agents lens's ONE mark builder, published as `window.agentPerformance.mark`). Every act (adopt · switch · a finished regenerate or edit on the UI stream) **re-reads the session** — the server is the one source of lineage; nothing patches bubbles by hand.

**The measurement** (`conversation_lane_bench.py --regenerate`, react): every question regenerated once → `siblings` 2, `siblings_scored` 1.0 (both carry verdict strips), `context_one_answer` 1.0 (the model's context holds exactly one answer per question), `adopted_is_newest` 1.0, the regenerate's own cost ≈ $0.002 (a full turn); the original turn's tokens, `answered` and cost flat.

**Deviations, each deliberate.** The newest regenerate is adopted on arrival (the spec left adoption explicit; the reason to regenerate is to try again — Adopt lets the reader go back, and the record keeps both). "Another model" is another activated PROFILE (the platform's unit of model choice). The UI re-reads the session after a lineage turn rather than splicing the answer live (one renderer — the reload path — for siblings and branches; the streamed answer still renders live while the run is on). **Honest limits.** A purge on a turn a branch switch RESTORES is lost (the switch re-validates it); the comparison card shows the Scorecard's bands and never ranks the siblings — adoption is the reader's click.

## Grounded Multimodal (S9) — an attachment is a source, not a prompt

The parity move — the model sees an image natively — already lived in `llm/document_upload.py`. S9 is the beyond: **the platform reads the attachment itself and vouches for what it read.** ONE pure module, `llm/image_sources.py`: `extract(file_id, filename, path, content_type) → Source` (image → OCR boxes → `table_from_boxes`, a pure row/column clustering — rows by vertical centre at 0.6× the median box height, columns by nearest anchor of the widest row, a caption row dropped, the header detected as a non-numeric first row over numeric rows; PDF → pdfplumber tables + text, a text-less PDF rendered and OCR'd; CSV/TSV → rows; text/docx → text), `source_text` (what a VERIFIED source contributes to the evidence — an unverified one contributes nothing), `document_text_for` (what the MODEL sees, with its provenance line), `to_record`/`from_record` (the manifest round-trip: extraction happens ONCE, at upload), and a per-turn contextvar registry (`begin_turn` · `add` · `register_tables` · `stamp_for_turn`). Under `APP_CONFIG.ATTACHMENT_SOURCES` (off ⇒ byte-identical, frozen by `test_attachment_sources_baseline`). Engines are optional extras — absent ⇒ `method: none` with the reason named, never an error.

**Three states, one rule.** *verified* = read by the platform deterministically; *unverified* = the model's own transcription (the opt-in vision fallback, `attachmentConfig.transcribeImages`), recorded with its own minimal system prompt so it never comes back as a tool-call JSON, marked and **never evidence**; *unread* = nothing could read it, the model saw it natively. The rule the whole increment rests on: **a transcription the model wrote is not evidence for the model's own answer** — proven live: on a text-less bar chart the model transcribed `1 | 75 · 2 | 45 · 3 | 25` (invented) and the answer stated them as facts; the source is stamped unverified, the grounding text excludes it, the GROUNDED reading warns.

| Seam | Lane | What it does |
|---|---|---|
| `api/routes.py chat_upload_file` | — | reads the upload once (`extract`), stores the full record on the manifest, and — for a verified read — replaces the image placeholder / plain text with `document_text_for(src)`, so a text-only provider sees the rows. `.csv`/`.tsv` accepted with the switch on. |
| `executor._load_attachment_sources` (after the document loaders in `run()`) | Ideate · Focus · Optimize · Conversation | rebuilds the sources from the manifest, runs the transcription fallback, registers them for the turn, registers every verified table as an S3 statement (`result_sets.register` — a non-tool registration carrying `source`), appends an image's rows to the document context ONCE (a native-image provider never saw them), yields the **Attachments Read as Sources** step. |
| `coordinate_engine` | genie | `image_sources.register_from_manifest` beside its `load_document_context` — the same registry, no transcription. |
| `grounding/runtime.run_grounding` | all five | merges the live stamp into the turn before `build_inputs`; `RunRecord.grounding_text` + `attachment_chunks` (via `adapter._chunk_texts`) carry the VERIFIED text, so the numeric-fidelity and corpus-support families hold the answer to it. |
| `session_manager.update_last_turn_data` | all | `turn_data.setdefault("attachment_sources", stamp)` — the stamp carries the evidence text in full (one text, two moments: the live gate and the post-hoc scorer). |
| `result_sets.stamp_for_turn` | all | persists an attachment statement (with `source` + sample rows) even when no receipt exists — a quoted figure traces to the image on reload. |
| `assurance_points._read_attachment_sources` | GROUNDED | a measure (`attest`): na · pass · warn · na — the five readings. |

**What the reader sees** (`attachmentSources.js`, a plain script): the badge in words on every chip (`read · OCR · 1 table · 3 rows` → `used as source · …` after the turn; `transcribed · unverified`; `not readable`), the image as its own thumbnail with alt text FROM the extraction, the capsule's **Attachments** row with a crop of the cited region (drawn from the session-scoped content endpoint as a `data:` URL — the page's CSP allows `data:`, deliberately not `blob:`), and the lineage dialog opening an attachment statement on the file, the region and the row. The Live Status step precedes engine dispatch, so it reaches the standard renderer on every lane — that branch moves the dial and marks the bubble too.

**The measurement** (`conversation_lane_bench.py --attachments`): one question per class over an uploaded table image → `attachments_sourced` / `attachments_verified` / `figures_from_attachment` (a quoted figure whose statement is an attachment).

**Deviations, each deliberate.** Extraction at UPLOAD, not per turn (once, cached on the manifest; the composer says "read" before the question is asked). OCR is rapidocr-first (ONNX, no system binary) with pytesseract second; the spec's "where a provider returns structured content" is the same idea one layer earlier — the platform is the provider. **Honest limits.** OCR reads what is printed: a chart with no numbers yields no table (the transcription fallback is the only path, and it is unverified by construction); a PDF table extractor finds ruled tables, not every layout; a very wide OCR table can mis-cluster columns (the clustering is tolerant, not perfect — the sample rows on the stamp let a reader check).

## Steer & Reattach (S10) — talk to the agent while it works; never lose a running turn

Two properties a running turn lacked, one substrate. **Reattach:** a turn's SSE frames were produced once and written to one connection — close the laptop, reload the tab, let a proxy time out, and the turn kept running on the server while its output was lost to the reader. **Steer:** the only way to change what a running agent was doing was Stop — throwing away every step already paid for — and ask again. Under `APP_CONFIG.STEER_REATTACH` (off ⇒ byte-identical, frozen by `test_steer_reattach_baseline`).

**The event log** (`core/turn_stream.py`, process-global, pure): `open(task_id, session_id, user_uuid)` at `task_start`; `append(task_id, frame) → seq` for every frame `/ask_stream` produces, `stamp(frame, seq)` prefixing the SSE-standard `id: <seq>` resume field (a monotonic cursor the browser's own `EventSource` grammar already knows); `mark_done` in the task's `finally`; `events_since(task_id, since) → (events, done, gap)` with a retained floor (`MAX_EVENTS` 4000 — a replay from below it is a **declared gap**, never a silent skip); `active_for_session` (what a fresh tab asks first); async `tail(task_id, since)` waking on an `asyncio.Event`, with `: ping` keepalives, `reattach_gap` first when below the floor and `reattach_end` once the turn ended. A finished log lives `DONE_TTL_SECONDS` (15 min) for a late reattach; `MAX_TASKS` 200 bounds memory. **The turn never knows it was reattached** — the log is written at the one place every UI frame passes (`routes.py /ask_stream`'s queue), so every lane inherits it.

**The steer queue** (`core/steer.py`, per session): `queue` (bounded `MAX_PENDING` 5 · `MAX_TEXT` 600), `begin_turn`, `consume(session_id, at)` (marks each pending steer consumed at the named step), `note(steers, lead)`, `stamp_for_turn → {steers:[{id,text,ts,consumed,at,consumed_ts}], considered, carried}`, `announce` (a `steer_consumed` notification over the per-user channel — `put_nowait`, loop-guarded), and the two delivery seams `consume_at_boundary(session_id, tool_name)` / `consume_at_turn_start(session_id)`.

**The deviation, recorded.** The spec's Claude-native form is a mid-conversation `system` message. The Messages API has no such role — nor does any provider the platform speaks — so the ONE mechanism on every provider is **a note at the agent's next step**: the tool result is the one place a running conversation accepts new content everywhere. Two things the live runs taught, both about the model and not the seam: a note APPENDED after a long JSON tool result was ignored by a small model, so it is **prepended** (`tool_enforcement._lead`, which keeps the content_and_artifact tuple and the MCP content-block list shapes); a carried steer placed BEFORE the next question was ignored, so it is placed **after** it. The lead names the authority explicitly (*"the user changed the instructions while you were working … where it conflicts with the original request, this wins"*).

| Seam | Lane | What it does |
|---|---|---|
| `routes.py /ask_stream` | every UI turn | opens the log at `task_start` (payload gains `"reattach": true`), stamps every frame with its cursor, marks done in the task's `finally`. |
| `tool_enforcement._make_guarded_coroutine` | Ideate+tools · Focus · Conversation · genie experts | after the S6/S7 notes: `consume_at_boundary(session_id, tool_name)` → the note PREPENDED to the tool result (`_lead`). |
| `phase_executor._get_next_tactical_action` | Optimize | the note appended to the tactical prompt (`at = "phase N"`). |
| `mcp_adapter._invoke_final_report_task` | Optimize | the note appended to the final-report prompt (`at = "TDA_FinalReport"`) — the last step that can still change the answer. |
| `execution_service.run_agent_execution` | all five | `begin_turn(session_id)`; a steer left over from the previous turn is delivered AFTER this turn's question (`consume_at_turn_start`, `NEXT_TURN_LEAD`). |
| `session_manager.update_last_turn_data` | all | `turn_data.setdefault("steers", stamp)` — considered-where / carried, on the record. |
| `rest_routes.py` | — | `POST …/steer` (202 · 409 `no_running_turn` · 400 `invalid_steer`), `GET …/steers`, `GET …/active-task`, `GET /tasks/<id>/events?since=`, `GET /tasks/<id>/stream?since=` — owner-scoped, `404 steer_reattach_disabled` off. |

**What the reader sees** (`steerReattach.js`, a plain script): while an answer streams the composer stays LIVE — placeholder *Steer the agent — considered at its next step*, an `aria-description` saying the same; Enter sends a steer (`shouldSteer`), the button still stops (`viaButton = !!e.submitter`); a steer becomes a **pin** under the question (`role=note`: *sent · considered at the next step* → *considered with the result of base_readQuery*, announced in a polite live region, removed after a few seconds); the Live Status trace shows **Steer Considered** / **Steer Carried to the Next Turn** live (the notification channel) and on reload (synthesized from the stamp). `processStream` reads `id:` into the cursor; when the connection drops after `task_start` and before `final_answer`, `_maybeReattach` tails `/tasks/<id>/stream?since=<cursor>` through the SAME `processStream` (bounded retries with backoff, a `404` = the turn is gone) and shows *reconnected — caught up*; a fresh tab or a session switch onto a running session (`reattachRunningTurn`: `active-task` → execution state + status window reset + a silent tail from cursor 0) shows the turn still running with its trace backfilled.

**The measurement** (`conversation_lane_bench.py --drop --steer "…"`): the first question per class drops its stream after six frames, steers over REST, reattaches from the cursor → `dropped` / `resumed` / `frames_after_drop` / `steer_sent` / `steer_considered`. Off: the dropped turn is lost to the client (`resumed` 0, `answered` 0.5/0.8, the steer refused). On: `resumed` 1, `answered` 1/1, react `steer_considered` 1 at `base_readQuery` with the answer in German; the pure-LLM ideate turn has no boundary, so its steer is honestly `carried`. Cost and latency flat.

**Honest limits.** Compliance is the model's — the record says WHERE the steer was considered; the answer shows WHETHER it was followed. A pure LLM turn (no tool, no report step) cannot be steered mid-turn on any provider without a system role; it is carried. A carried steer rides the next turn's stored `user_query` (that IS what the model was asked). A reattach from below the retained floor re-reads the session instead of replaying (the gap is declared). The log is per process — a multi-worker deployment needs it in a shared store (the interface is four functions).

## Several MCP servers per agent (PROP-006 phase 2) — armed once, read by both tool lanes

An agent may read additional MCP servers. The engines are not told: the execution seam
(`execution_service.run_agent_execution`) arms the turn's alias → server map
(`tool_identity.set_turn_servers`) and the server names (`agent_mcp_servers.set_turn_source_labels`)
once, and releases both in its `finally`. The Optimize lane (planner, tactical step, FASTPATH,
genie slaves) reaches every server through `invoke_mcp_tool` → `resolve_mcp_target`; the ReAct lane
(Conversation, Focus with tools, Coordinate experts of those classes) loads LangChain tools per
server (`load_mcp_tools_for_langchain(alias, selection)`), each carrying its server id, and emits
`source` on its tool events. Ideate and Focus without tools read no MCP server. A single-server
agent arms nothing — its turns are byte-identical (`test_multi_mcp_baseline`). Architecture:
[MULTI_SOURCE_ARCHITECTURE.md §9](MULTI_SOURCE_ARCHITECTURE.md).

## Adding a New Engine

1. **Create the file:** `src/trusted_data_agent/agent/engines/my_engine.py`
2. **Subclass and register:**
   ```python
   from .registry import EngineRegistry
   from .base import ExecutionEngine

   @EngineRegistry.register
   class MyEngine(ExecutionEngine):
       profile_type = "my_new_type"  # must match profile.profile_type in DB

       async def run(self, executor):
           # access all shared state via executor.*
           yield executor._format_sse_with_depth({"step": "...", "type": "..."})
   ```
3. **Register on import:** Add `from .my_engine import MyEngine  # noqa: F401` to `engines/__init__.py`
4. **Wire dispatch:** Add the eligibility check in `executor.run()` and delegate.

No other file changes required.

---

## Extraction Phases

The extraction was performed in 5 sequential phases, each independently committed and testable:

| Phase | Engine | Lines Extracted | executor.py After |
|-------|--------|----------------|-------------------|
| 0 | Base infrastructure (ABC + registry) | — | ~6,800 |
| 1 | IdeateEngine | ~670 | ~6,130 |
| 2 | FocusEngine | ~1,073 | ~5,060 |
| 3 | CoordinateEngine | ~360 | ~4,700 |
| 4 | OptimizeEngine | ~1,085 | ~3,875 |
| 5 | ConversationEngine | ~715 | ~3,160 |

---

## Design Rationale

### Why Method Object (not dependency injection)?

All shared infrastructure (LLM client, MCP adapter, session manager, context window) already lives on `PlanExecutor`. The shortest path to modularization is to pass `self` (the executor) as the engine's context, giving engines full access without duplication or re-wiring.

### Why direct import dispatch instead of EngineRegistry.resolve()?

The conditions for routing (is_llm_only, is_rag_focused, etc.) are computed several hundred lines before dispatch and depend on decrypted profile data, runtime feature flags, and component state — not just `profile.profile_type`. Encoding these conditions in `applies_to()` would require engines to import and re-evaluate the same config, coupling them to things they don't otherwise need. The registry is still useful for programmatic introspection and as the mechanism for a future unified genie dispatch.

### Why no EngineContext dataclass yet?

Introducing a dataclass would require identifying every `executor.*` attribute accessed by every engine — a broad surface area that risks introducing bugs. The Method Object pattern is the minimum-change extraction; a lean `EngineContext` is a follow-up refactor once the extraction is stable.

---

## File Reference

| File | Purpose |
|------|---------|
| `src/trusted_data_agent/agent/engines/__init__.py` | Import-order-controlled registration; package public API |
| `src/trusted_data_agent/agent/engines/base.py` | `ExecutionEngine` ABC with `applies_to()` and `run()` |
| `src/trusted_data_agent/agent/engines/registry.py` | `EngineRegistry` — register decorator, resolve, introspection |
| `src/trusted_data_agent/agent/engines/ideate_engine.py` | `IdeateEngine` (llm_only) |
| `src/trusted_data_agent/agent/engines/focus_engine.py` | `FocusEngine` (rag_focused) |
| `src/trusted_data_agent/agent/engines/coordinate_engine.py` | `CoordinateEngine` (genie) |
| `src/trusted_data_agent/agent/engines/optimize_engine.py` | `OptimizeEngine` (tool_enabled) |
| `src/trusted_data_agent/agent/engines/conversation_engine.py` | `ConversationEngine` (llm_only + tools) |
| `src/trusted_data_agent/agent/executor.py` | `PlanExecutor` — shared infrastructure + dispatch routing |
| `src/trusted_data_agent/agent/execution_service.py` | Genie dispatch via `CoordinateEngine().execute_genie()` |
| `src/trusted_data_agent/agent/agent_history.py` | The one conversation-history builder for the LangChain lanes |
| `src/trusted_data_agent/agent/turn_failure.py` | The honest answer for a failed turn, shared by every engine |
| `src/trusted_data_agent/agent/leaked_sql.py` | ReAct lane: a leaked read-only SELECT run through the bound read tool |

---

## Related Documents

- [Fusion Optimizer Architecture](FUSION_OPTIMIZER_ARCHITECTURE.md) — End-to-end execution pipeline for OptimizeEngine
- [Coordinator Architecture](COORDINATOR_ARCHITECTURE.md) — GenieCoordinator internals wrapped by CoordinateEngine
- [Context Window Architecture](CONTEXT_WINDOW_ARCHITECTURE.md) — Shared context management used by all engines
