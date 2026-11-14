# Proof Pack — A4: Multi-Agent Orchestration

**What this proves:** the coordinator routes a question across specialist agents, each running its **own full IFOC pipeline**, then synthesizes — with per-expert evaluation, honest cost attribution, and one sealed provenance chain across the whole tree. Each expert is a real session running its own engine (Optimize/Focus/Ideate), is **scored in its own profile class**, bills its **own** cost (no double-count), and a coordinator can itself be an expert of another coordinator (nested, depth-guarded).
**What you can check:** the annotated coordination trace below, and the files it cites.
**Status:** ✅ Built · annotated trace below · Last updated 2026-06-28

---

## 1. What the claim requires, and the architecture that delivers it

Genuine multi-agent orchestration means a coordinator that delegates to specialists, combines their results, and is observable and governable end-to-end. Uderia's Genie coordinator (`agent/genie_coordinator.py`, `agent/engines/coordinate_engine.py`) is a LangGraph ReAct agent whose **tools are other agents**:

- **`SlaveSessionTool`** (`genie_coordinator.py:122-510`) — one LangChain tool per expert profile, named `invoke_{TAG}`. It creates a **real child session** (`POST /api/v1/sessions` with `genie_parent_session_id`), submits the routed sub-query (`POST …/query`), and polls to completion. The expert runs its *own* engine — a `tool_enabled` expert runs the full Planner/Executor; a `rag_focused` expert runs retrieval+synthesis.
- **`GenieCoordinator`** (`genie_coordinator.py:513-1380`) — builds the ReAct agent (`create_react_agent`, `:780`); the LLM routes by calling `invoke_{TAG}` tools, then makes a synthesis call to combine results.
- **The coordinator never executes domain tools itself** — it is purely router + synthesizer (COORDINATOR_ARCHITECTURE.md §1).

**Per-slave-session lock** (`genie_coordinator.py:65-71,234-236`): an `asyncio.Lock` per child session prevents LangGraph's parallel tool dispatch from corrupting the child's `turn_number`.

**Pass-through optimization** (`genie_coordinator.py:1000-1055`, COORDINATOR §14): when a **single expert** answers, on the **first** LLM iteration, with **no prior history** and **no uncalled `tool_enabled` executor**, the coordinator skips the synthesis LLM call entirely (the expert's answer is already complete prose) — halving token cost and latency. Emits `genie_synthesis_skipped`. Rich HTML is preserved via `_slave_html_responses`.

**Nested genie (coordinator-of-coordinators)** (COORDINATOR §5):
- Default `maxNestingDepth=3` (`schema/08_genie_global_settings.sql`).
- **Compile-time**: `config_manager.py` DFS detects circular/self-reference child chains.
- **Runtime**: blocked at both the engine (`coordinate_engine.py:185`) and the invocation (`genie_coordinator.py:170-192`) — a too-deep nesting emits an error verdict rather than recursing.

---

## 2. Per-expert evaluation & honest cost (the trust layer)

The coordinator turn is the evaluable unit, and **each expert is also auto-scored in its own IFOC class** (WS3, Jun 2026):

- **`_summarize_slaves()`** (`coordinate_engine.py:38-110`) reads each expert's **own** persisted child-session turn for its authoritative status/tokens/cost + `started_at`/`completed_at`/`duration_ms` (the waterfall).
- **SELF-cost model — no double-count** (`coordinate_engine.py:657-689`, `eval/types.py:376-387`): every turn (coordinator AND each expert) carries its **own** cost as the scored metric (`turn_cost`). Experts billed their own child sessions; portfolio sums never double-count. `RunRecord.turn_cost_usd` reads `turn_cost` only.
- **`genie_rollup`** (`coordinate_engine.py:666-689`): a **display-only** inclusive annotation (`coordinator + experts`), rendered as "+N expert $Y · turn total $Z" — never summed into portfolio totals.
- **Span tagging** (`eval/online.py:170-221`): coordinator spans → `agent.invocation="direct"` + `eval.experts` + `eval.expert_count`; expert spans → `agent.invocation="expert"` + `agent.parent_session_id`.
- **Coordination scorers** (genie roster, 19): `RoutingCorrectnessScorer` (expected ⊆ invoked), `CoordinationEfficiencyScorer` (no dupes, ≤ max), `ExpertSuccessScorer` (every consulted expert succeeded), `synthesis_quality` judge, plus `answer_groundedness` on experts that ran tools.

**Full-result pass-through** (`slaveProfileSettings.fullResultPassthrough`): for `tool_enabled` experts whose raw rows the coordinator must combine, an opt-in flag appends the expert's full `collected_data` (pipe-delimited, ≤50 rows) to what the synthesis LLM sees (`genie_coordinator.py:489-492,74-118`). Default OFF → synthesis sees only the brief summary. Survives Agent-Pack export via tag-remap.

---

## 3. Live-Status event sequence (17 distinct events)

Emitted in order during a coordination turn (`genie_coordinator.py`):

| # | Event | Payload (key fields) |
|---|---|---|
| 1 | `execution_start` | `profile_type:"genie"`, `query`, `available_slaves`, `turn_id` |
| 2 | `genie_coordination_start` | `slave_profiles[]` (id/tag/name/type each) |
| 3 | `kg_enrichment` *(if KG bound)* | `total_entities`, `total_relationships` |
| 4 | `genie_llm_step` #1 → **"Routing Decision"** | `input_tokens`, `output_tokens`, `cost_usd`, `call_id` |
| 5 | `genie_slave_invoked` (per expert) | `profile_tag`, `query`, `nesting_level`, `session_id` |
| 6 | `genie_slave_progress` | `slave_session_id`, `status:"executing"` |
| 7 | `rag_retrieval` *(forwarded from child)* | child's retrieval event |
| 8 | `genie_slave_completed` (per expert) | `result`, `duration_ms`, `success` |
| 9 | `genie_llm_step` #2 → **"Response Synthesis"** | tokens + `cost_usd` *(skipped on pass-through)* |
| 10 | `genie_synthesis_start` / `genie_synthesis_skipped` | `profiles_consulted[]` |
| 11 | `genie_synthesis_complete` | `synthesized_response` (preview), `success` |
| 12 | `genie_coordination_complete` | `total_duration_ms`, `profiles_used[]`, token totals, `cost_usd` |
| 13 | `execution_complete` | `experts_consulted`, `tools_used[]`, `duration_ms` |

Component-tool branches (`genie_component_invoked`/`_completed`) and error path (`execution_error`, classified `rate_limit`/`quota_exceeded`/`timeout`/`coordination_error`) complete the catalog. Transient events are filtered from the persisted trace (`:617-645`).

---

## 4. Annotated coordination trace (code-faithful reconstruction)

> **Label: code-faithful reconstruction** — built strictly from the event-emission code (§3) and the real persisted turn shape in the test fixture `test/test_eval_genie_ux.py` (whose values are what a turn actually persists). A two-expert turn (synthesis runs) so the full pipeline is exercised.

**Query:** *"What were our top customers last month, and is that trend consistent with the market outlook?"* (spans data + knowledge domains)
**Coordinator:** `@GENIE` · children: `@TDAT` (`tool_enabled`, Teradata analyst), `@MKT` (`rag_focused`, market-knowledge).

```
STEP 1  execution_start            profile_type=genie, available_slaves=2, turn_id=1
        └─ coordinator turn begins; wall-clock timer set (coordinate_engine.py:178)

STEP 2  genie_coordination_start    slave_profiles=[{TDAT,tool_enabled},{MKT,rag_focused}]
        └─ ReAct agent built with tools invoke_TDAT, invoke_MKT

STEP 3  genie_llm_step #1           "Routing Decision"  in=8668 out=120 cost=$0.0023   [COORDINATOR self-cost]
        └─ _routing_tool_call_count=2 → multi-expert → pass-through CANNOT fire (:1001)

STEP 4  genie_slave_invoked         TDAT, q="Show top customers last month", nesting=0
   4b   genie_slave_progress        TDAT child-A status=executing
        └─ POST /sessions (child-A, genie_parent_session_id=GENIE) → POST .../query → poll
           @TDAT runs its OWN Optimize pipeline (Planner/Executor, MCP base_readQuery), bills its OWN turn
   4c   genie_slave_completed       TDAT child-A duration_ms=3400 success=true
        └─ [EXPERT-A self-cost: 10532 in / 628 out / $0.0013 — scored in its OWN tool_enabled class]

STEP 5  genie_slave_invoked         MKT, q="Is the trend consistent with market outlook?"
   5b   genie_slave_progress / rag_retrieval (forwarded from child-B)
   5c   genie_slave_completed       MKT child-B duration_ms=2100 success=true
        └─ [EXPERT-B self-cost: 6200 in / 240 out / $0.0009 — scored in its OWN rag_focused class]

STEP 6  genie_llm_step #2           "Response Synthesis"  in=4900 out=310 cost=$0.0017   [COORDINATOR self-cost]
        └─ 2 experts → multi-source combining required (§14) → synthesis runs

STEP 7  genie_synthesis_start       profiles_consulted=[TDAT, MKT]
STEP 8  genie_synthesis_complete    "Top customers were A,B,C… consistent with the bullish outlook because…"

STEP 9  genie_coordination_complete total_duration_ms=6200, profiles_used=[TDAT,MKT]
                                     in=13568 out=430 cost=$0.0040   ← COORDINATOR-ONLY (routing+synthesis) = turn_cost
STEP 10 execution_complete          experts_consulted=2, tools_used=[invoke_TDAT,invoke_MKT]
```

**Persisted turn** (`coordinate_engine.py:750-794`):
```jsonc
{ "profile_type":"genie", "status":"success",
  "final_summary_text":"<unified answer>",                    // answer_present reads this
  "turn_cost":0.0040, "turn_input_tokens":13568, "turn_output_tokens":430,  // COORDINATOR SELF-COST (billed)
  "slaves_invoked":[
    {"profile_tag":"TDAT","slave_session_id":"child-A","status":"success",
     "input_tokens":10532,"output_tokens":628,"cost_usd":0.0013,"profile_type":"tool_enabled",
     "started_at":100.0,"completed_at":103.4,"duration_ms":3400},
    {"profile_tag":"MKT","slave_session_id":"child-B","status":"success",
     "input_tokens":6200,"output_tokens":240,"cost_usd":0.0009,"profile_type":"rag_focused",
     "started_at":103.4,"completed_at":105.5,"duration_ms":2100}],
  "genie_rollup":{ "coordinator_cost_usd":0.0040,"expert_cost_usd":0.0022,"expert_count":2,
                   "cost_usd":0.0062 },                       // DISPLAY-ONLY — never summed into portfolio
  "grounding":{…},"guardrail":{…},"guardrail_inbound":{…},     // Tier 1.1/1.3 gates run on the synthesized answer
  "duration_ms":6200, "profile_tag":"GENIE" }
```

**Provenance & eval:**
- One Ed25519 EPC chain seals the whole tree: `query_intake → profile_resolve → coordinator_dispatch (per expert) → child_chain_ref (per expert) → coordinator_synthesis → turn_complete` (`coordinate_engine.py:740-748`).
- Portfolio cost = `$0.0040 (coord) + $0.0013 (TDAT) + $0.0009 (MKT)` — **no double-count**; the `$0.0062` rollup is display only.

**Pass-through variant** (single expert, no history — the real fixture values): routing calls only `invoke_TDAT` → STEP 6 synthesis is **skipped**, `genie_synthesis_skipped` emitted, the expert's rich HTML passed through. Persisted `turn_cost:0.0021`, one slave, `genie_rollup.cost_usd:0.0034`.

---

## 5. Agent Performance UX — coordination is legible

(`static/js/handlers/agentPerformanceHandler.js`)

- **Coordination Trace Tree + timing waterfall** (`:808-850`): master node → expert child nodes, each with its own verdicts/cost/duration. **Honest parallel detection** — "ran in parallel" is shown only when real `started_at`/`completed_at` overlap; otherwise sequential. A synthetic node appears when an expert wasn't scored as its own span ("enable live scoring to capture its quality").
- **Runs Ledger** (`:377,657`): every scored coordinator run (newest first) with per-dimension verdicts, band-strip, expand-to-verdicts, band/invocation filter; coordinator runs carry `experts[]` children + display-only `rollup_cost`/`rollup_tokens`.
- **Compare view** (`:338-346`): select 2+ agents → side-by-side metric bars + quality heatmap.
- **Roster** shows the direct/expert split ("· N as expert") so an agent's expert activity isn't conflated with direct invocations.

---

## 6. CI proof

- **`test/test_eval_genie_ux.py` — 42 assertions** (CI-pure, no LLM/server): RunRecord genie accessors, SELF-cost (`turn_cost_usd`/`total_tokens` — rollup is *not* the metric), genie scorers (`answer_present` via `final_summary_text`, `expert_success`, coordination), `analysis.agent_runs` master/slave tree (expert matched to its span by session id, synthetic fallback, no double-count), `online_quality_by_agent` direct/expert split, `build_quality_span` invocation/parent/experts attrs, REST `evals_agent_runs`.
- Coordination scorers also exercised in `test_eval_deterministic.py` (routing, coordination_efficiency) and `test_eval_reliability.py` (expert_success).

**Public artifact:** [`docs/Architecture/COORDINATOR_ARCHITECTURE.md`](../Architecture/COORDINATOR_ARCHITECTURE.md) (full coordinator architecture; nested coordination §5).

---

### Key code anchors
| Concern | File:line |
|---|---|
| SlaveSessionTool | `agent/genie_coordinator.py:122-510` |
| GenieCoordinator | `agent/genie_coordinator.py:513-1380` |
| Pass-through optimization | `agent/genie_coordinator.py:1000-1055` |
| Nested depth guard | `coordinate_engine.py:185`, `genie_coordinator.py:170-192` |
| SELF-cost + rollup | `coordinate_engine.py:38-110,657-689`; `eval/types.py:376-387` |
| Span invocation tagging | `eval/online.py:170-221` |
| EPC seal across tree | `coordinate_engine.py:740-748` |
| Agent Performance UX | `static/js/handlers/agentPerformanceHandler.js:338-850` |
