# Cost Tracking Architecture

**Version**: 2.1
**Last Updated**: 2026-10-30
**Status**: Production

> **Proof-pack:** [P4 — FinOps / Cost Controls](../proof-packs/P4_FINOPS.md) (per-agent cost-attribution demo with real numbers + `session = Σ turn` invariant + 4 enforced tiers) — the publishable evidence artifact for assessment requirement P4.

---

## Table of Contents

1. [Overview](#overview)
2. [Design Principles](#design-principles)
3. [Architecture Components](#architecture-components)
4. [Data Flow](#data-flow)
5. [Storage Schema](#storage-schema)
6. [API Contracts](#api-contracts)
7. [Cost Management REST API](#cost-management-rest-api)
8. [Frontend Integration](#frontend-integration)
9. [Frontend Cost Management Admin UI](#frontend-cost-management-admin-ui)
10. [Cost Calculation](#cost-calculation)
11. [Consumption Tracking Subsystem](#consumption-tracking-subsystem)
12. [Efficiency Tracker Subsystem](#efficiency-tracker-subsystem)
13. [Session Naming Attribution](#session-naming-attribution)
14. [Legacy Session Support](#legacy-session-support)
15. [Edge Cases](#edge-cases)
16. [Testing & Validation](#testing--validation)
17. [Performance Considerations](#performance-considerations)
18. [Future Enhancements](#future-enhancements)

---

## Overview

The **Cost Tracking Engine** provides accurate, real-time cost tracking for LLM interactions across all profile types, execution modes, and session lifecycles. It ensures consistent cost accounting from backend storage through API delivery to frontend display.

### Key Features

- **Single Source of Truth**: Backend calculates and stores authoritative costs
- **Real-time Tracking**: Live cost accumulation during execution with SSE streaming
- **Historical Accuracy**: Point-in-time cost snapshots for session replay
- **Multi-Engine Support**: Consistent tracking across every engine class (Ideate, Ideate + tools, Focus, Optimize, Coordinate)
- **Session Naming Attribution**: Includes system-level LLM costs in Turn 1
- **Legacy Compatibility**: Transparent fallback for sessions created before the cost engine
- **Dual-Model Support**: Separate tracking for strategic vs tactical models
- **Cost Management Admin UI**: Full admin dashboard for pricing configuration and analytics
- **LiteLLM Auto-Sync**: Automatic pricing updates from LiteLLM with provider availability checking (daily platform job `platform-cost-price-sync` + on-demand)
- **Live Provider Pricing**: Authoritative per-model prices from providers with a public price API (OpenRouter, Friendli)
- **Price Coverage**: Every model in use is priced or named (`ensure_price`, `GET /v1/costs/coverage`); a fallback-priced turn is stamped `cost_basis: estimated`
- **Cache-Aware Pricing**: Cached prompt prefixes priced at the provider's read factor (see [PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md))
- **Consumption Tracking**: Per-user token quotas, rate limiting, and monthly rollover with archiving
- **RAG Efficiency Tracking**: Measures token/cost savings from RAG-guided improvements
- **Model Deprecation Tracking**: Automatic detection of unavailable models via provider API queries
- **Bootstrap Pricing**: Default costs loaded from `tda_config.json` at startup

### Problem Solved

**Before**: Cost tracking was fragile and inconsistent:
- No authoritative cost storage (only token counts)
- Frontend calculated costs from event streams (risk of double-counting)
- Session naming costs not attributed to turns
- Inconsistent between live execution and historical reload
- **Result**: Session costs could be 2x actual costs

**After**: Reliable cost accounting:
- Backend stores `turn_cost` and `session_cost_usd` in workflow_history
- Frontend trusts backend data with verification
- Session naming costs included in Turn 1
- Consistent costs across all layers
- **Result**: Accurate cost tracking within ±$0.000001

---

## Design Principles

### 1. Backend as Single Source of Truth

**Principle**: All cost calculations happen in the backend; frontend displays authoritative data.

**Rationale**:
- Centralized pricing logic (one place to update)
- Prevents double-counting from duplicate events
- Consistent across all clients (UI, API, CLI)
- Historical costs remain accurate even if pricing changes

### 2. Point-in-Time Snapshots

**Principle**: Each turn stores cumulative `session_cost_usd` at the time of completion.

**Rationale**:
- Historical replay shows costs as they were
- Enables cost progression tracking
- Verifiable accounting (session cost = sum of turn costs)
- No need to recalculate history when pricing changes

**Readers serve the session total, not the snapshot (Sep 2026).** The stored per-turn
`session_cost_usd` stays "cost at the time this turn ran", but every surface that *reports* the
session cost — the session GET (`GET /session/<id>`), the turn-details endpoint and the engines'
per-turn stamp (`executor._calculate_session_cost_at_turn`) — reads ONE definition,
`session_manager.session_cost_total(session_data)`: Σ `turn_cost` over EVERY turn in the record
(regenerate siblings and turns outside the model's context included, a legacy turn priced from its
tokens). The session cost therefore no longer moves with which answer of a regenerate is adopted,
mirroring the session token counters. Gate: `test/test_session_cost_total.py`.

### 3. Fail-Safe Fallback

**Principle**: Legacy sessions without stored costs calculate from tokens with warnings.

**Rationale**:
- Backwards compatibility (no migration needed)
- Graceful degradation (approximate costs better than none)
- Clear logging for debugging
- Smooth transition for existing deployments

### 3a. Every model in use has a price — or is named (Oct 2026)

**Principle**: a model is never put into use silently on the fallback rate. The fallback
($0.10/$0.40) is an estimate that reads exactly like a price wherever a cost is shown; for
Google calls it hid a 3.5× under-statement (Optimize campaign OC-F12). One rule,
`CostManager.ensure_price(provider, model, base_model=None)`, resolves a price in this order
and fills the table from the first source that has one:

1. an existing row — exact id, normalised id (ARN / regional prefix stripped), or the vendor's
   **OpenRouter mirror** (`Google/gemini-x` ← OpenRouter `google/gemini-x`, exact id only);
2. the provider's **live price API** (`LIVE_PRICING_PROVIDERS`: OpenRouter, Friendli);
3. **$0** for local inference (MLX, Ollama), stored explicitly with source `local`;
4. **LiteLLM**'s table for the exact id (as given, under the provider's LiteLLM prefix, normalised);
5. an **Azure** deployment's base model — declared (`baseModel`, stored with source
   `azure-base-model`), else inferred from a deployment name that ends with a known model id
   (source `inferred`), each with a note.

Otherwise it returns `unpriced` with the sentence the operator reads. Where it runs:

| Moment | What happens |
|---|---|
| **Selection** — an LLM configuration is created or updated | `ensure_price` runs; the response carries `pricing`; the dialog shows a warning when the model would bill at the fallback, an info line when a price was filled or inferred (`pricingNotice`) |
| **Daily** — platform job `platform-cost-price-sync` (03:15 UTC) | the full cost sync: LiteLLM, the live price APIs, then the **coverage phase** (`fill_coverage`) |
| **On demand** — `GET /v1/costs/coverage` (`?fill=1`), `POST /v1/costs/sync` | coverage of every model in use (each user's configurations — which also supply the judge panel — and the models billed in 30 days) |
| **Billing** — a call with no price | still billed at the fallback, but logged as a WARNING once per model, and the turn is stamped `cost_basis: estimated` (`priced` otherwise) at the persistence chokepoint |

Manual entries are never overwritten. A model no source prices (no public price API, a
retired model, a dedicated endpoint) stays `unpriced` until an operator enters its price.
Gates: `test_cost_vendor_mirror.py`, `test_pricing_notice.mjs`.

**Spend is booked under the model that was called.** Judge calls are not turns: each judge model
of a scored turn is booked as its OWN `consumption_turns` line (`status: judge`, the judge's
provider/model, priced at its own rate — `ConsumptionManager.record_usage_line`), never added into
the turn's row, which billed a Google judge as the turn model's spend. The user's token and cost
aggregates grow exactly once; the 24-hour activity chart counts turns, not judge lines. A failed
Ideate + tools turn records the model that served it (its spend was booked to `Unknown/unknown`).
"Models in use" counts only rows that spent something and never the `flow/flow-run` placeholder of
a flow's display-only summary turn; such a turn carries no `cost_basis`.
Gate: `test_consumption_attribution.py`.

### 4. Immediate Visibility

**Principle**: Costs display immediately when switching sessions (no waiting for API calls).

**Rationale**:
- Better user experience (no flickering $0.000000)
- Reduces perceived latency
- Consistent with auto-load last turn behavior
- Session data already includes cost information

---

## Architecture Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        COST TRACKING ENGINE                     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   BACKEND       │  │   API LAYER      │  │   FRONTEND       │
│   (Python)      │  │   (REST/SSE)     │  │   (JavaScript)   │
└─────────────────┘  └──────────────────┘  └──────────────────┘
        │                     │                      │
        ├─ CostManager        ├─ get_turn_details   ├─ updateTokenDisplay
        ├─ ConsumptionMgr     ├─ SSE events         ├─ handleReloadPlanClick
        ├─ EfficiencyTracker   ├─ Legacy fallback    ├─ handleLoadSession
        ├─ Executor            ├─ Cost Mgmt REST API ├─ sessionCostAccumulator
        ├─ SessionManager      │  (8 endpoints)      └─ CostManagementHandler
        └─ WorkflowHistory     └─ Cost Analytics         (Admin UI)
```

### Component Responsibilities

#### Backend Components

**CostManager** (`src/trusted_data_agent/core/cost_manager.py`) — Singleton
- Calculates cost from (provider, model, input_tokens, output_tokens) via `calculate_cost()`
- Maintains pricing table (`llm_model_costs`) with `get_model_cost()` and `get_fallback_cost()`
- Syncs pricing via `sync_from_litellm()` — four phases: (1) availability check, (2) LiteLLM pricing, (3) live provider pricing APIs, (4) coverage (`fill_coverage`: every model in use priced or named)
- Resolves a price for a model about to be used via `ensure_price()` (see §3a) and labels a turn's basis via `cost_basis()` (`priced` / `estimated`)
- Prices a call with a prompt-cache reading via `calculate_cost_with_cache()` (see *Cache-aware pricing* below)
- Persists authoritative live pricing from providers with a cost API via `upsert_live_costs(provider, source, entries)` (OpenRouter, Friendli). Two refresh paths: **Phase 3 of `sync_from_litellm()`** (reliable — `handler.fetch_live_pricing_sync()` hits each provider's **public** pricing URL with a synchronous httpx client, no credentials/event loop), and **`list_models()`** (opportunistic — reuses the dropdown payload). Never via `_check_model_availability()` — its `loop.run_until_complete()` raises "another loop is running" in the async server.
- Normalizes model names for AWS Bedrock ARNs and regional prefixes via `_normalize_model_name()`
- Loads bootstrap pricing from `tda_config.json` at startup via `_ensure_bootstrap_costs_loaded()`
- Checks model availability across provider APIs via `_check_model_availability()` (Google, Anthropic, OpenAI, Amazon, OpenRouter)
- Manages manual cost entries, fallback pricing, and deprecation tracking

**Pricing source precedence** (highest to lowest, by `llm_model_costs.source`): `manual` > `config_default` > **live provider API** (`openrouter`, `friendli`) > `litellm`. Live-API rows are protected from LiteLLM-sync overwrite via `LIVE_PRICING_PROVIDERS` / `LIVE_API_PRICING_SOURCES` in `cost_manager.py`. **Design intent: LiteLLM only covers providers without a validated live pricing API.** LiteLLM's static DB lags and mis-keys provider catalogs (OpenRouter keys are prefixed `openrouter/`, so a bare slug like `meta-llama/llama-4-scout` would otherwise fall back to default pricing); capturing the provider's own live prices keyed by the exact runtime `(provider, model)` closes that gap.

Validated providers (empirical): **OpenRouter** (`/api/v1/models`, per-token → ×1M) and **Friendli** (`/serverless/v1/models`, already per-1M — no scaling). Assessed and left on LiteLLM: Google (no price fields), OpenAI/Anthropic (no public pricing API), Azure (SKU/region pricing keyed by arbitrary deployment name → unmappable), AWS Bedrock (public but ~15 MB SKU catalog + ARN keys → deferred), xAI (OpenAI-compatible, no price field). MLX/Ollama are local ($0). Each provider's extractor owns its own unit handling. To add a provider's live pricing: register it in `LIVE_PRICING_PROVIDERS` (`cost_manager.py`) + `_PROVIDER_PRICING_SPECS` (`llm/handler.py` — `{url, data_key, extractor}`; the `url` must be a public, credential-free pricing endpoint so Phase 3 can fetch it) and optionally call `_persist_provider_pricing()` from its `list_models()` branch.

**ConsumptionManager** (`src/trusted_data_agent/auth/consumption_manager.py`)
- Real-time per-user consumption tracking with database persistence
- Rate limiting enforcement (hourly/daily request limits) via `check_rate_limits()`
- Monthly token quota enforcement via `check_token_quota()`
- Turn-level audit trail via `record_turn()` (stores to `consumption_turns` table)
- Separate usage lines for models that are not the turn's (judges) via `record_usage_line()` (`status: judge`)
- Monthly rollover with archiving via `rollover_period()`
- Session count reconciliation via `reconcile_session_count()`

**EfficiencyTracker** (`src/trusted_data_agent/core/efficiency_tracker.py`) — Singleton
- Tracks RAG-based token and cost savings across sessions
- Compares sequential turns (Turn N vs Turn N-1) within sessions
- Per-user and per-session efficiency metrics
- Persistent JSON state file at `tda_sessions/efficiency_state.json`

**Executor** (`src/trusted_data_agent/agent/executor.py`)
- Accumulates tokens per turn (`self.turn_input_tokens`, `self.turn_output_tokens`) and the Σ of per-call costs (`self.turn_cost_usd`, fed by `_call_llm_and_update_tokens` and the LangChain lanes' `_accumulate_call_cost`)
- Resolves `turn_cost` via `_resolve_turn_cost_usd()` before saving turn_summary
- Calculates `session_cost_usd` (cumulative) via `_calculate_session_cost_at_turn()`
- Stores both in workflow_history
- Manages dual-model configuration (strategic vs tactical) for `tool_enabled` profile

**SessionManager** (`src/trusted_data_agent/core/session_manager.py`)
- Persists workflow_history through the active session backend (`core/session_backends/`: file default, Postgres optional)
- Defines the session cost once: `session_cost_total()`
- Stamps `cost_basis` (`priced` / `estimated`) on a turn that spent something, at the persistence chokepoint
- Updates turn costs when session naming adds tokens (`update_turn_token_counts()`)
- Recalculates `turn_cost` and `session_cost_usd` when turn tokens change

#### API Layer

**Session Routes** (`src/trusted_data_agent/api/routes.py`)
- Endpoint: `GET /api/session/{session_id}/turn/{turn_id}/details`
- Returns: `turn_cost`, `session_cost_usd` (the session total, `session_cost_total`), tokens, provider, model, `dual_model_info`
- Legacy fallback: Calculates `turn_cost` from tokens if missing

**Cost Management REST API** (`src/trusted_data_agent/api/rest_routes.py`)
- `GET /api/v1/costs/coverage` — Which models in use have a price (`?fill=1` also fills what the sources can)
- `POST /api/v1/costs/sync` — Full cost sync (availability, LiteLLM, live price APIs, coverage)
- `GET /api/v1/costs/models` — Retrieve all model costs (the UI paginates client-side)
- `PUT /api/v1/costs/models/<cost_id>` — Update model pricing (manual override)
- `POST /api/v1/costs/models` — Add manual cost entry
- `DELETE /api/v1/costs/models/<cost_id>` — Remove cost entry
- `PUT /api/v1/costs/fallback` — Update default fallback pricing
- `GET /api/v1/costs/analytics` — Comprehensive cost analytics dashboard
- All endpoints require admin authentication (`@require_admin`)

**SSE Events** (live execution)
- Event: `token_update` with `cost_usd` per LLM call
- Frontend accumulates costs during live execution
- Backend sends authoritative costs in real-time

#### Frontend Components

**updateTokenDisplay()** (`static/js/ui.js`)
- Primary cost display function
- **Historical mode**: Trusts backend `turn_cost` and `session_cost_usd`
- **Live mode**: Accumulates `cost_usd` from SSE events
- Maintains `window.sessionCostAccumulator`
- Terminal event protection (preserves statement tokens when cost-only events arrive)

**handleReloadPlanClick()** (`static/js/eventHandlers.js`)
- Fetches turn details from API
- Extracts backend-provided costs
- Passes to `updateTokenDisplay()` with `isHistorical=true`
- Verification: Compares backend cost vs event sum (_getTurnCost)

**handleLoadSession()** (`static/js/handlers/sessionManagement.js`)
- Initializes costs when switching sessions
- Fetches last turn's cost data from `workflow_history`
- Sets `sessionCostAccumulator` immediately
- Updates cost display before rendering UI

**CostManagementHandler** (`static/js/handlers/costManagement.js`)
- Admin cost management dashboard (analytics KPIs + pricing configuration table)
- LiteLLM sync with availability checking
- Manual cost entry management
- Fallback pricing configuration
- Search filtering, pagination with infinite scroll, auto-refresh (30s)
- Deprecated model visibility toggle with localStorage persistence

---

## Data Flow

### Flow 1: Live Execution (New Turn)

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. USER SUBMITS QUERY                                            │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. BACKEND EXECUTION (executor.py)                               │
│    ├─ LLM Call #1: Strategic planning                           │
│    │  ├─ Extracts tokens from response.usage                    │
│    │  ├─ self.turn_input_tokens += input_tokens                 │
│    │  ├─ self.turn_output_tokens += output_tokens               │
│    │  ├─ CostManager.calculate_cost() → cost_usd                │
│    │  └─ Emit SSE: token_update with cost_usd                   │
│    ├─ LLM Call #2: Tactical execution                           │
│    │  └─ (repeat token/cost tracking)                           │
│    └─ LLM Call #3: Session naming (Turn 1 only)                 │
│       ├─ Adds tokens to self.turn_input_tokens                  │
│       └─ Emit SSE: token_update with cost_usd                   │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. FRONTEND LIVE DISPLAY (ui.js::updateTokenDisplay)            │
│    ├─ Receives SSE token_update events                          │
│    ├─ Accumulates cost_usd into sessionCostAccumulator.turn     │
│    ├─ Accumulates cost_usd into sessionCostAccumulator.session  │
│    └─ Updates header: Turn Cost / Session Cost                  │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. BACKEND STORAGE (executor.py → session_manager.py)           │
│    ├─ Calculate turn_cost = CostManager.calculate_cost(         │
│    │      provider, model,                                      │
│    │      self.turn_input_tokens,                               │
│    │      self.turn_output_tokens                               │
│    │   )                                                         │
│    ├─ Calculate session_cost_usd = previous_turns_sum + turn    │
│    ├─ Create turn_summary with:                                 │
│    │  ├─ turn_input_tokens: self.turn_input_tokens              │
│    │  ├─ turn_output_tokens: self.turn_output_tokens            │
│    │  ├─ turn_cost: calculated above                            │
│    │  └─ session_cost_usd: cumulative total                     │
│    └─ Save to workflow_history in session file                  │
└──────────────────────────────────────────────────────────────────┘
```

### Flow 2: Historical Reload (Click Turn Avatar)

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. USER CLICKS TURN AVATAR                                       │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. FRONTEND REQUEST (eventHandlers.js::handleReloadPlanClick)   │
│    └─ API.fetchTurnDetails(sessionId, turnId)                   │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. BACKEND API (routes.py::get_turn_details)                    │
│    ├─ Load session data                                         │
│    ├─ Extract turn from workflow_history[turn_id - 1]           │
│    ├─ IF turn_cost exists:                                      │
│    │  └─ Use stored value (authoritative)                       │
│    ├─ ELSE (legacy session):                                    │
│    │  └─ Calculate: CostManager.calculate_cost(tokens)          │
│    ├─ session_cost_usd = session_manager.session_cost_total()   │
│    │  (Σ turn_cost over EVERY turn; legacy turns from tokens)   │
│    └─ Return JSON with turn_cost, session_cost_usd              │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. FRONTEND DISPLAY (ui.js::updateTokenDisplay)                 │
│    ├─ IF data.turn_cost !== undefined:                          │
│    │  ├─ Use backend value (preferred)                          │
│    │  └─ Log: "[Reload] Using backend turn_cost: $X.XXXXXX"    │
│    ├─ ELSE:                                                      │
│    │  ├─ Fallback: _getTurnCost(turnData) from events          │
│    │  └─ Warn: "[Reload] Backend turn_cost missing"            │
│    ├─ IF data.session_cost_usd !== undefined:                   │
│    │  ├─ Use backend value (preferred)                          │
│    │  └─ Log: "[Reload] Using backend session_cost_usd: $X.XX" │
│    ├─ ELSE:                                                      │
│    │  └─ Warn: "[Reload] Backend session_cost_usd missing"     │
│    └─ Update header with costs                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Flow 3: Session Switching

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. USER SWITCHES TO SESSION                                      │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. FRONTEND LOAD (sessionManagement.js::handleLoadSession)      │
│    ├─ API.loadSession(sessionId) → returns session data         │
│    ├─ Extract: last_turn_data.workflow_history[]                │
│    ├─ Get last turn: workflow_history[length - 1]               │
│    ├─ Read: lastTurn.turn_cost (last turn's cost)               │
│    ├─ Read: data.session_cost_usd (served session_cost_total;   │
│    │        lastTurn.session_cost_usd only if absent)           │
│    ├─ Initialize sessionCostAccumulator:                         │
│    │  ├─ .turn = lastTurn.turn_cost                             │
│    │  └─ .session = served session total                        │
│    └─ Update cost display IMMEDIATELY                           │
└──────────────────────────────────────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. AUTO-LOAD LAST TURN (async, for Live Status panel)           │
│    ├─ Find last turn avatar in chat                             │
│    ├─ Call: handleReloadPlanClick(lastAvatar)                   │
│    └─ Refreshes cost display with same values                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Storage Schema

### Session File Structure

**Location**: `tda_sessions/{user_uuid}/{session_id}.json` with the default file backend (`TDA_SESSION_BACKEND=file`); the `postgres` backend stores the same JSON body in its `sessions` table. All code reaches it through `session_manager`, never the path.

```json
{
  "id": "3f6dd773-154d-44f5-b5bc-56e401fe3936",
  "name": "System Version Check",
  "provider": "Friendli",
  "model": "MiniMaxAI/MiniMax-M2.1",
  "input_tokens": 29519,        // Session total input
  "output_tokens": 524,          // Session total output
  "last_turn_data": {
    "workflow_history": [
      {
        "turn": 1,
        "user_query": "what is the system version",
        "turn_input_tokens": 13496,      // Turn 1 tokens (includes session naming)
        "turn_output_tokens": 302,
        "turn_cost": 0.004411,            // NEW - Turn 1 cost
        "session_cost_usd": 0.004411,     // NEW - Session cost as of Turn 1
        "provider": "Friendli",
        "model": "MiniMaxAI/MiniMax-M2.1",
        "timestamp": "2026-02-13T10:30:00Z",
        "system_events": [               // Session naming events
          {
            "type": "session_name_generation_complete",
            "payload": {
              "details": {
                "cost_usd": 0.000188     // Included in turn_cost above
              }
            }
          }
        ]
      },
      {
        "turn": 2,
        "user_query": "how many databases are on the system",
        "turn_input_tokens": 16023,
        "turn_output_tokens": 222,
        "turn_cost": 0.005073,            // NEW - Turn 2 cost
        "session_cost_usd": 0.009484,     // NEW - Session cost as of Turn 2 (Turn 1 + Turn 2)
        "provider": "Friendli",
        "model": "MiniMaxAI/MiniMax-M2.1",
        "timestamp": "2026-02-13T10:31:00Z"
      }
    ]
  }
}
```

### Key Fields

| Field | Type | Scope | Description |
|-------|------|-------|-------------|
| `turn_input_tokens` | Integer | Turn | Total input tokens for this turn (includes all LLM calls) |
| `turn_output_tokens` | Integer | Turn | Total output tokens for this turn |
| `turn_cost` | Float | Turn | Cost for this turn only (provider-specific pricing) |
| `session_cost_usd` | Float | Session | **Cumulative** cost from Turn 1 through this turn (point-in-time snapshot) |
| `session_input_tokens` | Integer | Session | Total session input tokens as of this turn |
| `session_output_tokens` | Integer | Session | Total session output tokens as of this turn |
| `cost_basis` | String | Turn | `priced` (the model has a price row) or `estimated` (billed at the fallback rate); absent on a turn that spent nothing or a flow summary turn |
| `cache` | Object | Turn | The prompt-cache totals the pricing used (cached / written tokens) |

### Cache-aware pricing (S2 — the Attested Cache)

Every LLM call is priced through **one rule**, `CostManager.calculate_cost_with_cache(provider, model, input, output, cached_tokens, cache_write_tokens, ttl)`: the fresh tokens at the base price, the cached prefix at the provider's read factor (`cache_policy.read_price_factor` — Anthropic 0.1× with the Fable 5.1 / Mythos 5.1 exception at 0.025×, Claude via OpenRouter and Bedrock at the Anthropic factor, Bedrock Nova 0.25×, Google 0.25×, OpenAI-shaped 0.5×), an explicit write at the ttl's surcharge (`5m` 1.25×, `1h` 2×; Nova writes at the input rate). `input` is always the TOTAL prompt (fresh + cached + written) — `cache_policy.read_usage` normalises Anthropic's three-part usage (its `input_tokens` EXCLUDES the cached prefix) and the OpenAI/Gemini folded shapes to that one convention before pricing.

The three call families reach it the same way: `executor._call_llm_and_update_tokens` (the handler lanes), the ReAct agent and the genie coordinator (`prompt_cache.priced`). Both LangChain agents hand each priced call to `executor._accumulate_call_cost` (the `cost_sink`), so the executor's per-turn accumulator is complete on every lane and `turn_cost = Σ cache-priced calls` — a single-model re-price of the summed tokens would forget what the provider served from cache (on the ReAct lane it over-stated the turn ~4× at an 85% cached share). The turn's `cache` stamp (`workflow_history[n].cache`) records the totals the pricing used; the master switch `PROMPT_CACHING` governs what is SENT (breakpoints), never what is read — a provider that caches on its own (Gemini) is read and priced at its cached rate whether or not the switch is on.

### Calls after the turn (one booking — `core/post_turn_usage`)

Some LLM calls run after the turn's `/ask_stream` has closed: the agent-memory extraction
(backgrounded) and the model-gap proposal suggestion (its own REST call). Each is booked by
`book_post_turn_call(...)`, once, the same way: the session totals, the turn's tokens **and its
own priced increment** (`update_turn_token_counts(additive=True, added_cost_usd=…)` — never a
re-price of the whole turn), the consumption ledger, the step persisted on the turn in the
replay shape (`{type, payload: {type, step, details}}` in `system_events`), and live on the
per-user notification channel: a `token_update` with absolute totals and a per-call `call_id`,
and the step itself, both session-tagged. Calls inside an open stream (session naming, Smart
Next Steps) keep their stream path; the judge has its own, equivalent one.

Every engine's reload replays `turn.system_events`: Optimize (`renderHistoricalTrace` — a
producer's step title is kept unless the title map knows the type), Ideate/Focus and Ideate
with tools (also when the turn renders as a summary card), and Coordinate (after the
coordinator's own `genie_events`). A bare event stored by an older booking is lifted into the
payload shape when the turn is loaded (`_normalizeTurnEventLists`). Gate:
`test/test_post_turn_usage.py`.

### Cost Invariants

**Must always be true**:
1. `turn_cost` ≥ 0 (costs are non-negative)
2. `session_cost_usd` stored on Turn N = sum of `turn_cost` of every turn already in the record when Turn N ran + Turn N's own; the session cost every reader serves = `session_cost_total` (Σ over all turns)
3. `session_cost_usd` is monotonically increasing (never decreases)
4. If `turn_input_tokens` and `turn_output_tokens` exist, `turn_cost` must be calculable
5. `turn_cost` = Σ of the per-call cache-priced costs of the turn (never a re-price of the summed tokens at one model's fresh rate) — on every engine lane
6. A call the **signed fallback** served (S4, the Resilience Envelope) is priced at **the model that served** — never at the configured model that failed. The handler lanes get this by construction (`call_llm_api` returns the provider/model that actually answered, and the fallback is the same function at depth 1); the LangChain lanes read the envelope's `uderia_served_by` stamp on the message (`prompt_cache.priced(_srv_provider, _srv_model, …)`); the step event names the served model and carries `served_by_fallback`. A retry costs nothing extra (no tokens were served); a continuation is a second priced call like any other.
7. **One usage per streamed call.** An OpenAI-compatible server may repeat its *cumulative* usage on several stream chunks (Celeris: 3 of ~4; vLLM `continuous_usage_stats`: every chunk), and LangChain's chunk merge SUMS `usage_metadata` — a 7,629-token prompt was recorded as 22,887 on the ReAct lane (3× input, 2× output, in the trace, `turn_cost`, session totals and the ledger). Every `BaseChatOpenAI` built by `create_langchain_llm` is re-classed by `llm/stream_usage.apply()` (before the resilience envelope, so envelopes and signed fallbacks inherit it): its `_astream`/`_stream` strip usage from every chunk and stamp one **field-wise-max** merge on the final chunk — the identity for the compliant single-usage stream, the true value for a cumulative one. Scope is OpenAI-compatible only: other providers report *incremental* usage across chunks (Anthropic: input on `message_start`, output on `message_delta`), where the sum is correct. The handler lanes never had the defect (`llm/streaming.py` keeps the last usage it sees). Gate: `test/test_stream_usage.py`.
8. **The persisted turn names the model that served it, and the ledger books the accurate cost.** An `@TAG` override is reverted (`restore_override_app_state`) in the turn's `finally`, *before* the turn is persisted; the revert snapshots `executor.turn_served_provider/model` first, and the persistence sites read `executor.served_provider_model()`. The consumption ledger (`session_manager` post-save) books the turn's `turn_cost` (the Σ-per-call accumulator) and re-prices the summed tokens at one model only when no positive `turn_cost` exists. Gate: `test/test_turn_served_model.py`.

---

## API Contracts

### GET /api/session/{session_id}/turn/{turn_id}/details

**Purpose**: Retrieve full turn details including costs for historical display.

#### Request

```http
GET /api/session/3f6dd773-154d-44f5-b5bc-56e401fe3936/turn/2/details
Authorization: Bearer {jwt_token}
```

#### Response (Success)

```json
{
  "turn": 2,
  "user_query": "how many databases are on the system",
  "turn_input_tokens": 16023,
  "turn_output_tokens": 222,
  "turn_cost": 0.005073,              // Authoritative turn cost
  "session_cost_usd": 0.009484,       // Session total (session_cost_total, every turn)
  "cost_basis": "priced",             // or "estimated" (fallback rate)
  "session_input_tokens": 29519,      // Session totals for header
  "session_output_tokens": 524,
  "provider": "Friendli",
  "model": "MiniMaxAI/MiniMax-M2.1",
  "original_plan": [...],
  "execution_trace": [...],
  "conversation_agent_events": [...]
}
```

#### Response (Legacy Session - Calculated Costs)

```json
{
  "turn": 2,
  "turn_input_tokens": 16023,
  "turn_output_tokens": 222,
  "turn_cost": 0.005073,              // Calculated from tokens
  "session_cost_usd": 0.009484,       // session_cost_total: legacy turns priced from tokens
  ...
}
```

**Note**: Legacy sessions (missing `turn_cost` in storage) have costs calculated on-demand using current pricing. Logs show warnings.

#### Backend Implementation

`routes.py` → `get_turn_details()`: a turn without a stored `turn_cost` (legacy) is priced on
demand with `CostManager.calculate_cost()` from its tokens at its own provider/model (falling back
to the session's); `session_cost_usd` is ALWAYS replaced by
`session_manager.session_cost_total(session_data)` — the session's total over every turn, not the
turn's stored snapshot — so the KPI cannot move with which answer of a regenerate is adopted.

---

## Cost Management REST API

**File**: `src/trusted_data_agent/api/rest_routes.py` (section `COST MANAGEMENT API ENDPOINTS`; handlers `get_cost_coverage`, `sync_costs_from_litellm`, `get_all_model_costs`, `update_model_cost`, … `get_cost_analytics`)

All endpoints require admin authentication (`@require_admin` decorator).

### GET /api/v1/costs/coverage

**Purpose**: Report which models in use have a price (lookup only — nothing written or fetched).
`?fill=1` first runs `CostManager.fill_coverage()` (`ensure_price` for every unpriced model, no live
fetch) and adds a `filled` list. "In use" = each user's LLM configurations (which also supply the
judge panel) plus every model billed in the last 30 days with non-zero spend (never the
`flow/flow-run` placeholder).

```json
{
    "status": "success",
    "models": [{"provider": "Google", "model": "gemini-3.1-flash-lite", "used_in": ["billed", "configuration"],
                "priced": true, "input": 0.1, "output": 0.4}],
    "total": 12,
    "unpriced": 1,
    "unpriced_models": ["Friendli/dedicated-endpoint-x"]
}
```

### POST /api/v1/costs/sync

**Purpose**: Run the full cost sync — model availability, LiteLLM pricing, the providers' live price
APIs, and the coverage phase. The same sync runs daily as platform job `platform-cost-price-sync`
(`15 3 * * *` UTC, seeded by `task_scheduler._ensure_cost_sync_job`).

#### Request

```http
POST /api/v1/costs/sync
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
    "check_availability": true    // Optional, default true
}
```

#### Response (Success)

```json
{
    "status": "success",
    "pricing": {
        "synced_count": 150,
        "new_models": ["OpenAI/gpt-4o-mini"],
        "updated_models": ["Anthropic/claude-sonnet-4-5"]
    },
    "availability": {
        "checked": true,
        "deprecated_count": 3,
        "undeprecated_count": 1,
        "skipped_providers": ["Azure"]
    },
    "coverage": { "total": 12, "unpriced": 1, "unpriced_models": ["..."], "filled": [] },
    "warnings": []                    // present only when a phase reported errors
}
```

**Four-Phase Process** (`CostManager.sync_from_litellm`):
1. **Phase 1 — Availability Check** (when `check_availability`): Queries Google, Anthropic, OpenAI, Amazon, OpenRouter APIs for available models. Marks missing models as `is_deprecated=True`. Skips providers without configured credentials.
2. **Phase 2 — LiteLLM Pricing**: Iterates LiteLLM's `model_cost` dictionary. Creates new entries, updates existing `litellm`-sourced entries. Protects `manual`, `config_default` and live-API rows from overwrite.
3. **Phase 3 — Live Provider Pricing**: For each `LIVE_PRICING_PROVIDERS` entry, `handler.fetch_live_pricing_sync(provider)` fetches the public pricing URL (sync httpx, no credentials) and `upsert_live_costs()` persists it. Runs after LiteLLM so it wins.
4. **Phase 4 — Coverage**: `fill_coverage()` prices every model in use that is still unpriced (vendor OpenRouter mirror, LiteLLM exact id, local $0, Azure base model) and reports the rest; unpriced models are logged as a WARNING.

Without the `litellm` package the sync returns at once (`LiteLLM library not available`) and none of the four phases run.

### GET /api/v1/costs/models

**Purpose**: Retrieve all model pricing entries.

```http
GET /api/v1/costs/models?include_fallback=true
Authorization: Bearer {jwt_token}
```

```json
{
    "status": "success",
    "costs": [
        {
            "id": "uuid-string",
            "provider": "Google",
            "model": "gemini-2.5-flash",
            "input_cost_per_million": 0.3,
            "output_cost_per_million": 2.5,
            "is_manual_entry": false,
            "is_fallback": false,
            "is_deprecated": false,
            "source": "litellm",
            "last_updated": "2026-10-28T03:15:00+00:00",
            "notes": null
        }
    ],
    "count": 4102
}
```

### PUT /api/v1/costs/models/{cost_id}

**Purpose**: Update pricing for a specific model (creates manual override).

```json
{
    "input_cost": 0.15,
    "output_cost": 0.60,
    "notes": "Updated via admin interface"
}
```

Sets `is_manual_entry=True` and `source='manual'`, protecting from future LiteLLM sync overwrites.

### POST /api/v1/costs/models

**Purpose**: Add a manual cost entry for a model not in LiteLLM.

```json
{
    "provider": "Friendli",
    "model": "Llama-3.3-70B-Instruct",
    "input_cost": 0.09,
    "output_cost": 0.16,
    "notes": "Friendli serverless endpoint pricing"
}
```

### DELETE /api/v1/costs/models/{cost_id}

**Purpose**: Remove a cost entry. Fallback entries cannot be deleted.

### PUT /api/v1/costs/fallback

**Purpose**: Update the default fallback pricing for unknown models.

```json
{
    "input_cost": 0.10,
    "output_cost": 0.40
}
```

### GET /api/v1/costs/analytics

**Purpose**: Comprehensive cost analytics across all sessions.

Reads every session body through the backend-agnostic `session_manager` API (`get_all_session_bodies`, or the caller's own with `SESSIONS_FILTER_BY_USER`) and calculates real-time analytics.

#### Response

```json
{
    "total_cost": 12.45,
    "cost_by_provider": {
        "Google": 5.20,
        "Anthropic": 4.80,
        "Friendli": 2.45
    },
    "cost_by_model": {
        "Google/gemini-2.5-flash": 5.20,
        "Anthropic/claude-sonnet-4-5": 4.80
    },
    "avg_cost_per_session": 0.0245,
    "avg_cost_per_turn": 0.0082,
    "total_sessions": 508,
    "most_expensive_sessions": [...],
    "most_expensive_queries": [...],
    "cost_trend": [...]
}
```

---

## Frontend Integration

### Cost Display Components

**Location**: Live Status Panel Header

```html
<!-- Turn Cost Card -->
<div id="metric-card-turn-cost" class="metric-card">
  <div class="metric-label">TURN COST</div>
  <div id="turn-cost-value" class="metric-value">$0.000000</div>
</div>

<!-- Session Cost Card -->
<div id="metric-card-session-cost" class="metric-card">
  <div class="metric-label">SESSION COST</div>
  <div id="session-cost-value" class="metric-value">$0.000000</div>
</div>
```

### Cost Accumulator

**Global State**: `window.sessionCostAccumulator`

```javascript
window.sessionCostAccumulator = {
  // Current turn costs (reset on each new turn)
  turn: 0.005073,                  // Last turn cost

  // Session-level cumulative costs
  session: 0.009484,               // Total session cost

  // Dual-model tracking (strategic vs tactical)
  strategic: 0.002500,             // Cumulative strategic cost
  tactical: 0.006984,              // Cumulative tactical cost

  // Per-turn dual-model breakdown
  strategicTurnIn: 5000,
  strategicTurnOut: 100,
  strategicTurnCost: 0.001200,
  tacticalTurnIn: 11023,
  tacticalTurnOut: 122,
  tacticalTurnCost: 0.003873,

  // Session-level dual-model tokens
  strategicSessionIn: 15000,
  strategicSessionOut: 300,
  tacticalSessionIn: 14519,
  tacticalSessionOut: 224,

  // Last statement metadata
  lastStmtPhase: 'tactical'        // 'strategic' | 'tactical' | null
};
```

### updateTokenDisplay() Signature

```javascript
/**
 * Updates token and cost displays in the Live Status panel header.
 *
 * @param {Object} data - Token and cost data
 * @param {number} data.statement_input - Input tokens for this LLM call
 * @param {number} data.statement_output - Output tokens for this LLM call
 * @param {number} data.turn_input - Cumulative turn input tokens
 * @param {number} data.turn_output - Cumulative turn output tokens
 * @param {number} data.total_input - Session total input tokens
 * @param {number} data.total_output - Session total output tokens
 * @param {number} [data.cost_usd] - Cost for this LLM call (live execution)
 * @param {number} [data.turn_cost] - Total turn cost (historical reload)
 * @param {number} [data.session_cost_usd] - Cumulative session cost (historical reload)
 * @param {Object} [data.perModelBreakdown] - Dual-model cost breakdown
 * @param {boolean} isHistorical - True if displaying historical turn (reload)
 */
export function updateTokenDisplay(data, isHistorical = false) {
  // Implementation...
}
```

### Key Behavior Differences

| Mode | Cost Source | Accumulation | Use Case |
|------|-------------|--------------|----------|
| **Live Execution** (`isHistorical=false`) | SSE events `cost_usd` | Incremental accumulation | Real-time cost tracking during query execution |
| **Historical Reload** (`isHistorical=true`) | API response `turn_cost`, `session_cost_usd` | Direct assignment | Display costs when clicking turn avatars |
| **Session Switch** | Session data `workflow_history` | Direct assignment from last turn | Initialize costs when loading session |

---

## Cost Calculation

### CostManager Implementation

**File**: `src/trusted_data_agent/core/cost_manager.py` — Singleton via `get_cost_manager()`

```python
class CostManager:
    def __init__(self):
        # Loads LiteLLM library (optional dependency)
        # Calls _ensure_bootstrap_costs_loaded() from tda_config.json

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """
        Calculate cost for an LLM call.

        Returns:
            Cost in USD (float)
        """
        # Lookup model-specific pricing, with ARN normalization
        costs = self.get_model_cost(provider, model)
        if not costs:
            # Falls back to configurable fallback pricing (NOT $0.00);
            # logged as a WARNING once per model (an estimate that reads like a price)
            costs = self.get_fallback_cost()

        input_cost_per_million, output_cost_per_million = costs

        input_cost = (input_tokens / 1_000_000) * input_cost_per_million
        output_cost = (output_tokens / 1_000_000) * output_cost_per_million

        return input_cost + output_cost

    def get_model_cost(self, provider, model) -> Optional[Tuple[float, float]]:
        """Lookup pricing with ARN normalization fallback."""
        # 1. Try exact match (provider + model)
        # 2. Try normalized model name (strips ARN prefix, regional prefix)
        # Returns (input_cost_per_million, output_cost_per_million) or None

    def get_fallback_cost(self) -> Tuple[float, float]:
        """Get configurable fallback pricing (default: $0.10 input, $0.40 output)."""

    def calculate_cost_with_cache(self, provider, model, input_tokens, output_tokens,
                                  cached_tokens=0, cache_write_tokens=0, ttl="5m") -> float:
        """The one pricing rule for a call with a prompt-cache reading (S2)."""

    def sync_from_litellm(self, check_availability=True, user_uuid=None) -> Dict:
        """Four-phase sync:
        Phase 1: Check model availability across provider APIs (marks deprecated)
        Phase 2: Sync pricing from LiteLLM model_cost dictionary
        Phase 3: Live provider pricing APIs (OpenRouter, Friendli) via upsert_live_costs()
        Phase 4: Coverage — fill_coverage() prices or names every model in use
        Protected entries (manual, config_default, live API) are never overwritten by LiteLLM."""

    def ensure_price(self, provider, model, *, base_model=None, fetch_live=True) -> Dict:
        """Resolve or fill a model's price (see Design Principle 3a).
        Returns {status: priced|filled|unpriced, source, input, output, note}. Never raises."""

    def cost_basis(self, provider, model) -> str:
        """'priced' when the model has a price row, 'estimated' otherwise."""

    def price_coverage(self, days=30) -> Dict / fill_coverage(self, days=30) -> Dict:
        """Coverage of models_in_use(): lookup only / fill then report."""

    def _normalize_model_name(self, model) -> str:
        """Strips ARN prefixes and regional identifiers.
        e.g., 'arn:aws:bedrock:eu-central-1:123:.../eu.amazon.nova-lite-v1:0'
              → 'amazon.nova-lite-v1:0'"""

    def _check_model_availability(self, user_uuid) -> Dict:
        """Queries Google, Anthropic, OpenAI, Amazon, OpenRouter APIs for available models.
        Marks DB entries as deprecated/un-deprecated based on availability."""

    def _ensure_bootstrap_costs_loaded(self):
        """Loads default_model_costs from tda_config.json on first init."""
```

**Key Design Decisions**:
- Fallback pricing ensures costs are never `$0.00` for unknown models (configurable via admin UI)
- Manual entries and config_default entries are protected from LiteLLM sync overwrite
- ARN normalization handles AWS Bedrock inference profile identifiers
- Singleton pattern via `get_cost_manager()` ensures pricing is loaded once

### Pricing Table Schema

**Table**: `llm_model_costs`
**Model**: `LLMModelCost` (`src/trusted_data_agent/auth/models.py`)

```sql
CREATE TABLE llm_model_costs (
    id VARCHAR(36) PRIMARY KEY,                    -- UUID string
    provider VARCHAR(50) NOT NULL,                 -- Google, Anthropic, OpenAI, Amazon, Azure, Friendli, Ollama
    model VARCHAR(100) NOT NULL,                   -- Model identifier
    input_cost_per_million INTEGER NOT NULL,        -- USD per 1M input tokens (declared INTEGER; SQLite stores the float value, e.g. 0.10)
    output_cost_per_million INTEGER NOT NULL,       -- USD per 1M output tokens (same)
    is_manual_entry BOOLEAN NOT NULL DEFAULT FALSE, -- True if manually entered by admin
    is_fallback BOOLEAN NOT NULL DEFAULT FALSE,     -- True for fallback/default pricing
    is_deprecated BOOLEAN NOT NULL DEFAULT FALSE,   -- True if model is deprecated/discontinued
    source VARCHAR(50) NOT NULL,                   -- see Source Priority below
    last_updated TIMESTAMP WITH TIME ZONE NOT NULL,
    notes TEXT,                                     -- Admin notes
    UNIQUE(provider, model)
);
CREATE INDEX idx_llm_cost_provider_model ON llm_model_costs(provider, model);
```

**Source Priority** (determines update behavior during LiteLLM sync):
- `config_default` — Bootstrap pricing from `tda_config.json`, protected from sync overwrite
- `manual` — Admin-created entries, protected from sync overwrite (`is_manual_entry=True`)
- `openrouter`, `friendli` — Live provider price APIs (`LIVE_API_PRICING_SOURCES`), protected from LiteLLM overwrite, refreshed by sync Phase 3
- `litellm` — Auto-synced from LiteLLM library, updated on each sync (also the source `ensure_price` writes for a LiteLLM exact-id fill)
- `local` — $0 rows `ensure_price` writes for MLX / Ollama
- `azure-base-model`, `inferred` — an Azure deployment priced as its declared / name-inferred base model (with a note)
- `system_default` — Hardcoded fallback entries

**Example Data**:

| Provider | Model | Input Cost (per 1M) | Output Cost (per 1M) | Source |
|----------|-------|---------------------|----------------------|--------|
| Friendli | MiniMaxAI/MiniMax-M2.1 | $0.10 | $0.40 | config_default |
| Anthropic | claude-sonnet-4-5 | $3.00 | $15.00 | litellm |
| OpenAI | gpt-4o | $2.50 | $10.00 | litellm |

### Cost Calculation in Executor

**File**: `src/trusted_data_agent/agent/executor.py`

`turn_cost` is NOT a re-price of the turn's summed tokens at one model. Every priced call adds its
own cost (priced at the model that served it, cache-aware) to `self.turn_cost_usd`:
`_call_llm_and_update_tokens` for the handler lanes, `_accumulate_call_cost` (the `cost_sink`) for
the ReAct agent and the genie coordinator, `_merge_sub_totals` for a delegated prompt's
sub-executor. At persistence:

```python
turn_cost = self._resolve_turn_cost_usd()
# = self.turn_cost_usd when > 0 (the Σ of per-call costs — what the Live Status event sum shows);
#   else a single-model re-price of turn_input/output_tokens (a path that bypassed the
#   accumulator — never regress to $0); 0.0 when nothing was spent.

session_cost_usd = self._calculate_session_cost_at_turn(session_data) + turn_cost
# _calculate_session_cost_at_turn delegates to session_manager.session_cost_total()
# (every turn already in the record; the current turn is not yet persisted)

turn_summary = {
    # ... other fields ...
    "turn_input_tokens": self.turn_input_tokens,
    "turn_output_tokens": self.turn_output_tokens,
    "turn_cost": turn_cost,
    "session_cost_usd": session_cost_usd,
}
# session_manager stamps cost_basis ('priced' / 'estimated') when the turn is persisted.
```

Gates: `test/test_turn_cost.py` (Σ per-call, dual-model), `test/test_rerank_cost_crossclass.py`
(reranking cost events), `test/test_session_cost_total.py`.

---

## Frontend Cost Management Admin UI

**File**: `static/js/handlers/costManagement.js`
**Class**: `CostManagementHandler` — initialized on DOMContentLoaded as `window.costManager`

### Features

| Feature | Description |
|---------|-------------|
| **Cost Analytics Dashboard** | KPI cards: total cost, avg cost per session, avg cost per turn, total sessions |
| **Cost by Provider Chart** | Horizontal bar chart showing cost breakdown by provider |
| **Top 5 Expensive Models** | Horizontal bar chart showing the most expensive models |
| **Pricing Configuration Table** | Editable table with inline cost editing (input/output per 1M tokens) |
| **LiteLLM Sync** | Button triggers `POST /api/v1/costs/sync` with availability checking |
| **Manual Entry** | Prompt-based flow to add custom provider/model pricing |
| **Fallback Configuration** | Adjustable default costs for unknown models |
| **Search Filtering** | Filter by provider or model name |
| **Deprecated Model Toggle** | Checkbox to show/hide deprecated models (persisted in localStorage) |
| **Infinite Scroll** | Paginated table with scroll-based loading (20/50/100 items) |
| **Auto-Refresh** | Optional 30-second polling for live data (persisted in localStorage) |

### Table Row Styling

| Entry Type | Visual Indicator |
|------------|-----------------|
| Manual entry | Green left border + green dot + "MANUAL" badge |
| Deprecated model | Yellow left border + warning badge + reduced opacity |
| LiteLLM entry | Standard styling + blue "litellm" source label |
| Config default | Standard styling + gray "config_default" source label |

### Sort Priority

1. `config_default` entries first
2. `manual` entries second
3. every other source (`litellm`, live-API, `local`, `inferred`, …) third
4. Within same priority: alphabetical by provider, then model

---

## Consumption Tracking Subsystem

**File**: `src/trusted_data_agent/auth/consumption_manager.py`
**Class**: `ConsumptionManager`

### Purpose

Provides real-time per-user consumption tracking, rate limiting enforcement, and monthly quota management. Operates as a database-backed tracking layer complementing the session-file-based cost tracking.

### Database Tables

#### `user_consumption` (UserConsumption model)

Tracks real-time consumption metrics per user per monthly period.

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | String (PK) | User identifier |
| `current_period` | String | Monthly period (YYYY-MM) |
| `total_input_tokens` | Integer | Cumulative input tokens this period |
| `total_output_tokens` | Integer | Cumulative output tokens this period |
| `total_tokens` | Integer | Combined total tokens |
| `input_tokens_limit` | Integer (nullable) | Monthly input quota (NULL = unlimited) |
| `output_tokens_limit` | Integer (nullable) | Monthly output quota (NULL = unlimited) |
| `estimated_cost_usd` | Integer | Micro-dollars (USD x 1,000,000) |
| `rag_cost_saved_usd` | Integer | Micro-dollars saved by RAG |
| `successful_turns` | Integer | Quality metric |
| `failed_turns` | Integer | Quality metric |
| `total_turns` | Integer | Total turn count |
| `rag_guided_turns` | Integer | RAG-used turn count |
| `rag_output_tokens_saved` | Integer | Tokens saved by RAG |
| `requests_this_hour` | Integer | Rate limiting counter |
| `requests_today` | Integer | Rate limiting counter |
| `models_used` | JSON | Model usage breakdown |
| `providers_used` | JSON | Provider usage breakdown |
| `total_sessions` | Integer | Session count |
| `active_sessions` | Integer | Currently open sessions |

#### `consumption_turns` (ConsumptionTurn model)

Granular per-turn audit trail.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID (PK) | Turn record ID |
| `user_id` | String | User identifier |
| `session_id` | String | Session identifier |
| `turn_number` | Integer | 1-indexed turn number |
| `user_query` | Text | User's query text |
| `session_name` | String | Session name (updated retroactively) |
| `input_tokens` | Integer | Turn input tokens |
| `output_tokens` | Integer | Turn output tokens |
| `provider` | String | LLM provider |
| `model` | String | Model name |
| `cost_usd_cents` | Integer | Micro-dollars per turn |
| `status` | String | the turn's status (`success`, a failure/partial status), or `judge` for a judge model's own usage line (`record_usage_line`) |
| `rag_used` | Boolean | Whether RAG was used |
| `rag_tokens_saved` | Integer | Tokens saved by RAG |

#### `consumption_periods_archive` (ConsumptionPeriodsArchive model)

Immutable monthly snapshots for historical reporting.

#### `consumption_profiles` (ConsumptionProfile model)

Defines rate limiting and quota tiers assignable to users.

| Field | Type | Description |
|-------|------|-------------|
| `prompts_per_hour` | Integer | Hourly rate limit (default: 100) |
| `prompts_per_day` | Integer | Daily rate limit (default: 1000) |
| `input_tokens_per_month` | Integer (nullable) | Monthly input quota |
| `output_tokens_per_month` | Integer (nullable) | Monthly output quota |

### Cost Units

- **Storage**: Micro-dollars (USD x 1,000,000) for precision without floating-point issues
- **API Response**: Converted to USD for display
- **Example**: 500,000 micro-dollars = $0.50

### Per-Turn Cost Recording Flow

```
1. LLM Call Completes
   ↓
2. Extract: provider, model, input_tokens, output_tokens
   ↓
3. Turn persisted (session_manager post-save) → cost_usd = the turn's turn_cost
   (Σ per-call accumulator); re-priced from summed tokens only when no positive
   turn_cost exists
   ↓
4. Convert to micro-dollars: cost_usd_cents = int(cost_usd * 1_000_000)
   ↓
5. ConsumptionManager.record_turn()
   → Store in consumption_turns table (audit trail)
   → Update user_consumption totals (running counters)
   → Track model/provider usage (JSON fields)
   → Calculate RAG cost savings if applicable
6. Judge models of a scored turn (eval/online.py) → record_usage_line(kind="judge"):
   their OWN row under the judge's provider/model; aggregates grow once, no prompt counted
```

### Time-Window Resets & Monthly Rollover (self-healing, Aug 2026)

The hourly/daily/monthly counters are only meaningful relative to their reset
boundaries. The reset semantics are defined **once** —
`ConsumptionManager.apply_hourly_reset` / `apply_daily_reset` (requests AND
the `*_tokens_today` counters) / `_rollover` — and delivered by three
cooperating paths (see
[SCHEDULER_ARCHITECTURE.md](SCHEDULER_ARCHITECTURE.md) → Platform Maintenance
Jobs):

1. **Lazy healer**: `get_or_create_consumption()` heals overdue windows on
   every consumption access, so quotas enforce against the correct period
   even if no scheduled job ever fires (rate limiting being disabled — the
   default — no longer leaves counters accumulating forever).
2. **Platform maintenance jobs** (in-app APScheduler, seeded by schema 33)
   sweep all users including idle ones, via the same `apply_*` helpers.
3. **Startup catch-up sweep** in `task_scheduler.start_scheduler()` covers
   resets missed while the server was down (APScheduler's in-memory jobstore
   cannot see those).

The rollover itself (`_rollover`, public wrapper `rollover_period()`):
1. Archives the finished period to `consumption_periods_archive`
2. Resets all token counts, turn counts, sessions for the new period
3. Preserves historical data for analytics and reporting

**All-time invariant:** every rollover archives the finished period's totals
*before* resetting them, so `SUM(consumption_periods_archive) + current
period == all-time`, always. The admin "Tokens Consumed" column
(`GET /v1/consumption/users` → `lifetime_*_tokens`) reads exactly this —
deliberately NOT a `SUM(consumption_turns)`, because that ledger is pruned to
90 days by `cleanup_old_turns()` at each rollover. Gate:
`test/test_consumption_healing.py`.

### Maintenance Scripts

| Script | Purpose |
|--------|---------|
| `maintenance/backfill_consumption_costs.py` | Populate historical cost data |
| `maintenance/consumption_periodic_jobs.py` | Recurring maintenance tasks. Run in-process by the platform scheduler (managed session) + the startup catch-up sweep; external cron is optional, standalone CLI use only |
| `maintenance/reconcile_consumption.py` | Fix data inconsistencies |
| `maintenance/check_token_usage.py` | Verify consumption records |
| `maintenance/check_user_profile.py` | Audit user profiles and limits |

---

## Efficiency Tracker Subsystem

**File**: `src/trusted_data_agent/core/efficiency_tracker.py`
**Class**: `EfficiencyTracker` — Singleton via `get_efficiency_tracker()`

### Purpose

Tracks RAG-based token and cost savings by comparing sequential turns within sessions. Quantifies the efficiency gains from RAG-guided responses.

### How It Works

```
Turn N-1: 1000 output tokens (cost: $0.04)
Turn N:    750 output tokens (cost: $0.03), RAG used: True

Tokens Saved: 250
Cost Saved:   $0.01
Attribution:  Only counted because RAG was used AND tokens decreased
```

### Key Rules

1. **RAG Attribution**: Savings only counted if `had_rag=True` AND `tokens_saved > 0`
2. **Sequential Comparison**: Always Turn N vs Turn N-1 within same session
3. **Per-User Tracking**: Separate metrics per `user_uuid`
4. **Per-Session Tracking**: Cumulative improvements within sessions

### State Structure

**File**: `tda_sessions/efficiency_state.json`

```json
{
    "total_output_tokens_saved": 15000,
    "total_rag_improvements": 42,
    "total_sessions_tracked": 18,
    "cumulative_cost_saved": 1.2345,
    "last_updated": "2026-03-01T12:00:00",
    "session_improvements": {
        "session-uuid": [
            {"turn_index": 2, "tokens_saved": 250, "cost_saved": 0.01, "timestamp": "..."}
        ]
    },
    "user_metrics": {
        "user-uuid": {
            "tokens_saved": 5000,
            "improvements": 15,
            "cost_saved": 0.45,
            "sessions": ["session-1", "session-2"]
        }
    }
}
```

### API

```python
# Record an improvement
tracker.record_improvement(
    session_id, turn_index,
    previous_output_tokens, current_output_tokens,
    had_rag=True, cost_per_output_token=0.0004,
    user_uuid="user-123"
)

# Get metrics (global or per-user)
metrics = tracker.get_metrics(user_uuid="user-123")
# Returns: total_output_tokens_saved, total_rag_improvements,
#          cumulative_cost_saved, avg_tokens_saved_per_improvement
```

---

## Session Naming Attribution

### Problem

Session naming is a system-level LLM call that generates a descriptive session name after Turn 1. This costs ~$0.0002 (244 input / 96 output tokens) but was NOT included in Turn 1's cost.

**Impact**: Session cost at Turn 1 was understated by $0.0002.

### Solution

Session naming tokens are added to Turn 1's accumulators BEFORE turn_summary is saved, ensuring they're included in `turn_cost` calculation.

### Implementation

#### Pattern A: Session Naming BEFORE turn_summary Creation

**Profiles**: `conversation_with_tools`

```python
# Session naming happens first
if self.current_turn_number == 1:
    async for name_result in self._generate_and_emit_session_name():
        if isinstance(name_result, tuple):
            new_name, name_input_tokens, name_output_tokens, name_events = name_result

            # Add session name tokens to turn accumulators
            if name_input_tokens > 0 or name_output_tokens > 0:
                self.turn_input_tokens += name_input_tokens
                self.turn_output_tokens += name_output_tokens

                await session_manager.update_token_count(...)

                # The name call's own cost is folded into self.turn_cost_usd
                # (only when the main pipeline already accumulated)

# THEN turn_summary is created (using updated accumulators)
turn_cost = self._resolve_turn_cost_usd()    # Includes session naming

turn_summary = {
    "turn_input_tokens": self.turn_input_tokens,
    "turn_output_tokens": self.turn_output_tokens,
    "turn_cost": turn_cost,                    # Includes session naming cost
    # ...
}
```

#### Pattern B: Session Naming AFTER turn_summary Creation

**Profiles**: `rag_focused`, `llm_only`, `genie`

```python
# turn_summary created first (without session naming)
turn_summary = {
    "turn_input_tokens": self.turn_input_tokens,    # Excludes session naming
    "turn_output_tokens": self.turn_output_tokens,
    "turn_cost": turn_cost,                          # Excludes session naming
    # ...
}

await session_manager.update_last_turn_data(...)

# THEN session naming happens
if self.current_turn_number == 1:
    async for name_result in self._generate_and_emit_session_name():
        if isinstance(name_result, tuple):
            new_name, name_input_tokens, name_output_tokens, name_events = name_result

            # Add to turn accumulators
            self.turn_input_tokens += name_input_tokens
            self.turn_output_tokens += name_output_tokens

            # Update the already-saved turn in workflow_history, ADDING the
            # name call's already-priced cost (never a re-price of the whole turn)
            await session_manager.update_turn_token_counts(
                self.user_uuid, self.session_id, self.current_turn_number,
                self.turn_input_tokens,    # Now includes session naming
                self.turn_output_tokens,
                added_cost_usd=_name_cost
            )
```

#### SessionManager: update_turn_token_counts()

**File**: `src/trusted_data_agent/core/session_manager.py`

```python
async def update_turn_token_counts(user_uuid, session_id, turn_number, input_tokens, output_tokens,
                                   added_cost_usd: float = None, additive: bool = False) -> bool:
```

Runs inside `_session_transaction` (the per-session lock). It sets the turn's token counts (or adds
to them with `additive=True`) and:

- with `added_cost_usd` — ADDS that already-priced increment to the stored `turn_cost` (session
  naming, a judge run, a post-turn call priced at its own model). This is the normal path: it keeps
  `turn_cost` = Σ per-call costs.
- with `added_cost_usd=None` — re-prices the turn from its totals at the session model (legacy
  behaviour, correct only for a same-model addition).

It then refreshes the turn's `session_cost_usd`. Gate: `test/test_post_turn_usage.py`.

### Verification

**Session File**:
```json
{
  "turn": 1,
  "turn_input_tokens": 13487,      // Includes session naming (244 tokens)
  "turn_output_tokens": 262,        // Includes session naming (96 tokens)
  "turn_cost": 0.004360,            // Includes session naming ($0.000188)
  "system_events": [
    {
      "type": "session_name_generation_complete",
      "payload": {
        "details": {
          "cost_usd": 0.000188       // Reflected in turn_cost above
        }
      }
    }
  ]
}
```

---

## Legacy Session Support

### Definition

**Legacy session**: Session created before cost tracking engine deployment (missing `turn_cost` and `session_cost_usd` in workflow_history).

### Detection

```python
if "turn_cost" not in turn_data:
    # This is a legacy session
    app_logger.info(f"Legacy session detected for turn {turn_id}")
```

### Fallback Strategy

#### API Endpoint Fallback

```python
# routes.py::get_turn_details()

if "turn_cost" not in turn_data_copy:
    # Calculate from tokens using current pricing
    cost_manager = CostManager()
    turn_cost = cost_manager.calculate_cost(
        provider=turn_data_copy.get("provider"),
        model=turn_data_copy.get("model"),
        input_tokens=turn_data_copy.get("turn_input_tokens", 0),
        output_tokens=turn_data_copy.get("turn_output_tokens", 0)
    )
    turn_data_copy["turn_cost"] = turn_cost
    app_logger.warning(f"Calculated legacy turn cost: ${turn_cost:.6f}")

if "session_cost_usd" not in turn_data_copy:
    # Sum all turn costs
    session_cost = 0.0
    for i in range(turn_id):
        turn = workflow_history[i]
        if "turn_cost" in turn:
            session_cost += float(turn["turn_cost"])
        else:
            # Recursively calculate from tokens
            ...
    turn_data_copy["session_cost_usd"] = session_cost
```

#### Frontend Fallback

```javascript
// ui.js::updateTokenDisplay()

if (isHistorical) {
    // Historical reload
    if (data.turn_cost !== undefined) {
        turnCost = parseFloat(data.turn_cost);
        console.log(`[Historical] Turn cost from backend: $${turnCost.toFixed(6)}`);
    } else {
        // Legacy fallback: estimate from tokens
        turnCost = _estimateCostFromTokens(data.turn_input || 0, data.turn_output || 0);
        console.warn('[Historical] Backend did not provide turn_cost, estimating from tokens');
    }
}

function _estimateCostFromTokens(inputTokens, outputTokens) {
    const INPUT_COST_PER_1M = 0.10;   // Fallback default
    const OUTPUT_COST_PER_1M = 0.40;

    const inputCost = (inputTokens / 1_000_000) * INPUT_COST_PER_1M;
    const outputCost = (outputTokens / 1_000_000) * OUTPUT_COST_PER_1M;

    return inputCost + outputCost;
}
```

### Limitations of Legacy Fallback

1. **Approximate Costs**: Uses current pricing, not historical pricing at time of execution
2. **Pricing Changes**: If provider changed pricing, legacy costs may be inaccurate
3. **Missing Data**: If token counts are also missing, cost is $0.000000
4. **No Verification**: Cannot verify accuracy against actual costs paid

### Migration Strategy

**No migration needed**. Legacy sessions work transparently:
- API calculates costs on-demand
- Frontend displays calculated costs
- Logs show warnings for debugging
- New turns in legacy sessions store costs correctly

---

## Edge Cases

### 1. Zero-Token Turns

**Scenario**: Turn completes with 0 input and 0 output tokens (e.g., error before LLM call).

**Behavior**:
- `turn_cost` = $0.000000
- `session_cost_usd` = previous session cost (no change)
- Cost display shows $0.000000 for turn

**Implementation**:
```python
if self.turn_input_tokens == 0 and self.turn_output_tokens == 0:
    turn_cost = 0.0  # No LLM calls made
```

### 2. Missing Pricing Data

**Scenario**: Provider/model not in `llm_model_costs` table.

**Behavior**:
- CostManager falls back to configurable fallback pricing via `get_fallback_cost()`
- Default fallback: $0.10 input / $0.40 output per 1M tokens (configurable via admin UI)
- Logs a WARNING once per model: "[Cost] No price for {provider}/{model} — billed at the FALLBACK rate (an estimate)"
- Costs are approximate but non-zero — never `$0.000000` for actual token usage
- The persisted turn is stamped `cost_basis: "estimated"` (`priced` otherwise), so a consumer can tell an estimate from a price
- Frontend displays fallback-based cost

**Mitigation**:
- Admins can adjust fallback pricing via `PUT /api/v1/costs/fallback`
- Admins can add manual entries via `POST /api/v1/costs/models` or admin UI
- `POST /api/v1/costs/sync` (also daily, `platform-cost-price-sync`) pulls pricing from LiteLLM and the live provider APIs automatically
- `ensure_price` runs when an LLM configuration is saved; `GET /api/v1/costs/coverage` names every unpriced model in use
- Bootstrap pricing from `tda_config.json` covers commonly used models

### 3. Mid-Turn Session Naming Failure

**Scenario**: Session naming LLM call fails after Turn 1 execution completes.

**Behavior**:
- Turn 1 costs saved without session naming
- Session naming tokens NOT added to turn
- `turn_cost` reflects only conversation costs
- No `session_name_generation_complete` event in `system_events`

**Impact**: Turn 1 cost understated by ~$0.0002 (acceptable, rare case).

### 4. Concurrent Session Modifications

**Scenario**: Two browser tabs modify same session simultaneously.

**Behavior**:
- Every load-modify-save of a session runs inside `session_manager._session_transaction` — a per-session lock from the lock provider (`core/locks/`: in-process by default, Redis with `TDA_LOCK_BACKEND=redis`), so concurrent writers cannot clobber each other's token/cost updates (`update_turn_token_counts(additive=True)` adds deltas inside the lock)
- A tab's cost accumulator may still be stale until it reloads the session
- Reloading the session resyncs costs (the served session total is `session_cost_total`)

### 5. Floating-Point Precision

**Scenario**: Costs stored/calculated with different precision levels.

**Behavior**:
- Backend stores 6 decimals (microsecond precision)
- JavaScript displays 6 decimals
- Comparisons use ±$0.000001 tolerance

**Example**:
```python
# Backend: 0.004360500000000001 (floating-point artifact)
# Display: $0.004361 (rounds to 6 decimals)
# Validation: abs(0.004361 - 0.004360) < 0.000001 → PASS
```

### 6. Multi-Provider Sessions

**Scenario**: User switches LLM provider mid-session (Turn 1 uses Provider A, Turn 2 uses Provider B).

**Behavior**:
- Each turn stores its own `provider` and `model`
- `turn_cost` calculated with correct provider pricing
- `session_cost_usd` sums costs from all providers

**Example**:
```json
{
  "turn": 1,
  "provider": "OpenAI",
  "model": "gpt-4o",
  "turn_cost": 0.008500
},
{
  "turn": 2,
  "provider": "Anthropic",
  "model": "claude-sonnet-4-5",
  "turn_cost": 0.012000
}
// session_cost_usd at Turn 2 = 0.008500 + 0.012000 = 0.020500
```

### 7. Profile Override Mid-Session

**Scenario**: User uses `@TAG` syntax to override profile for single query.

**Behavior**:
- Turn uses overridden profile's LLM config
- Costs calculated with override provider/model
- Session cost sums all turns regardless of profile

Costs are tied to provider/model, not profile. The one subtlety: the override is reverted in the
turn's `finally` before the turn is persisted, so persistence reads
`executor.served_provider_model()` (snapshotted before the revert) — see Cost Invariant 8.

### 8. Cancelled/Failed Turns

**Scenario**: User cancels query mid-execution or LLM call fails.

**Behavior**:
- Partial token counts accumulated
- `turn_cost` resolved from the partial per-call accumulator (`_resolve_turn_cost_usd`)
- `status` field set to "cancelled" or "error"
- Costs still tracked and included in session total
- A turn the provider failed (quota, auth, rate limit, outage) delivers one honest sentence (`agent/turn_failure.answer`, never the raw error), records the model it was sent to (a failed Ideate + tools turn no longer books to `Unknown/unknown`); an empty account counts against the provider breaker. Gate: `test/test_turn_failure.py`

**Rationale**: Even failed turns consume tokens and incur costs.

---

## Testing & Validation

### Unit Tests (CI gates — no server, no network)

| Gate | What it pins |
|---|---|
| `test/test_turn_cost.py` | `turn_cost` = Σ per-call costs (dual-model), `_resolve_turn_cost_usd` |
| `test/test_session_cost_total.py` | One session-cost definition (`session_cost_total`) read by every surface |
| `test/test_rerank_cost_crossclass.py` | Knowledge reranking emits a cost event on every engine class |
| `test/test_token_accounting_single_count.py` | Each LLM call's tokens persisted to the session exactly once |
| `test/test_post_turn_usage.py` | Calls after the turn booked once (`book_post_turn_call`) |
| `test/test_stream_usage.py` | One usage per streamed call (Cost Invariant 7) |
| `test/test_turn_served_model.py` | Persisted turn names the model that served it (Cost Invariant 8) |
| `test/test_cost_vendor_mirror.py` | `ensure_price` resolution order, vendor OpenRouter mirror, coverage, `cost_basis` |
| `test/test_pricing_notice.mjs` | The LLM-configuration dialog's pricing notice |
| `test/test_consumption_attribution.py` | Judge lines booked under the judge's model, models-in-use filter |
| `test/test_consumption_healing.py` | Time-window resets, rollover archiving, all-time invariant |
| `test/test_consumption_profiles.py` | Consumption profiles, quotas, rate limiter integration |
| `test/test_turn_failure.py` | A provider-failed turn's answer, record and model |
| `test/test_prompt_cache_plan.py` | Cache plan per provider (what is cached, hence what is priced at the read factor) |

### Integration Tests

**Scenario 1: New Session with 2 Turns**
1. Execute Turn 1 → Verify `turn_cost` and `session_cost_usd` stored
2. Execute Turn 2 → Verify `session_cost_usd` = Turn 1 + Turn 2
3. Reload Turn 1 → Verify costs match stored values
4. Reload Turn 2 → Verify costs match stored values

**Scenario 2: Legacy Session**
1. Load legacy session (missing `turn_cost`, `session_cost_usd`)
2. Reload Turn 1 → Verify backend calculates costs from tokens
3. Reload Turn 2 → Verify session cost = sum of calculated turn costs

**Scenario 3: Session Naming**
1. Execute Turn 1 → Verify session naming cost included
2. Check Turn 1 `turn_cost` includes session naming
3. Reload Turn 1 → Verify costs match

### Validation Checklist

- [ ] Turn 1: `session_cost_usd` = `turn_cost`
- [ ] Turn N: `session_cost_usd` = sum of `turn_cost` for Turns 1..N
- [ ] All `turn_cost` values ≥ 0
- [ ] `session_cost_usd` monotonically increasing
- [ ] Session naming cost included in Turn 1
- [ ] Legacy sessions calculate costs without errors
- [ ] Switching sessions displays costs immediately (not $0.000000)
- [ ] Clicking turn avatars updates costs correctly
- [ ] Live execution accumulates costs in real-time
- [ ] Browser console shows "[Reload] Using backend turn_cost" messages

### Test Execution (REST API)

```bash
# Authenticate
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | jq -r '.token')

# Create session
SESSION_ID=$(curl -s -X POST http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{}' | jq -r '.session_id')

# Submit Turn 1
TASK1_ID=$(curl -s -X POST "http://localhost:5050/api/v1/sessions/$SESSION_ID/query" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "test query 1"}' | jq -r '.task_id')

# Wait for completion (poll task status)

# Fetch Turn 1 details
curl -s -X GET "http://localhost:5050/api/session/$SESSION_ID/turn/1/details" \
  -H "Authorization: Bearer $JWT" | jq '{turn_cost, session_cost_usd}'

# Submit Turn 2
TASK2_ID=$(curl -s -X POST "http://localhost:5050/api/v1/sessions/$SESSION_ID/query" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "test query 2"}' | jq -r '.task_id')

# Fetch Turn 2 details
curl -s -X GET "http://localhost:5050/api/session/$SESSION_ID/turn/2/details" \
  -H "Authorization: Bearer $JWT" | jq '{turn_cost, session_cost_usd}'

# Verify: Turn 2 session_cost_usd = Turn 1 turn_cost + Turn 2 turn_cost
```

---

## Performance Considerations

### Computational Cost

**Cost Calculation**: O(1) - simple arithmetic
- Input: (tokens, pricing) → multiply and sum
- No database queries during execution
- Pricing table cached in memory

**Session Cost Calculation**: O(N) where N = number of turns
- Must iterate all previous turns to sum costs
- Mitigated: Only calculated once per turn at save time
- Historical reload: Cost already stored, no calculation

### Storage Impact

**Per Turn**: +16 bytes
- `turn_cost`: 8 bytes (float64)
- `session_cost_usd`: 8 bytes (float64)

**Per Session**: +16N bytes (N = number of turns)

**Example**: 100-turn session = 1.6 KB additional storage (negligible)

### Network Impact

**SSE Events**: +20 bytes per `token_update` event
- Adds `cost_usd` field: 8 bytes float + JSON overhead

**API Response**: +30 bytes per turn details request
- Adds `turn_cost` and `session_cost_usd` fields

**Impact**: Negligible - costs already include token counts (~same size)

### Browser Performance

**Cost Display Updates**: O(1) - simple DOM text update
```javascript
turnCostEl.textContent = `$${cost.toFixed(6)}`;  // ~5μs
```

**Session Switching**: +1 array access
```javascript
const lastTurn = workflow_history[workflow_history.length - 1];  // O(1)
```

### Optimization Opportunities

1. **Cache CostManager pricing**: Load all pricing into memory at startup (already done)
2. **Batch cost calculations**: Calculate all turn costs in single pass (not needed - already fast)
3. **Lazy load workflow_history**: Only load when needed (future enhancement)
4. **Cost deltas**: Store deltas instead of cumulative (not recommended - loses point-in-time semantics)

---

## Future Enhancements

### Implemented (moved from v1.0 future list)

The following features were listed as future enhancements in v1.0 and have since been implemented:

- **Cost Budgets & Rate Limiting** — Implemented via `ConsumptionProfile` and `ConsumptionManager`. Per-user hourly/daily request limits and monthly token quotas with enforcement. See [Consumption Tracking Subsystem](#consumption-tracking-subsystem).
- **Cost Analytics Dashboard** — Implemented via `GET /api/v1/costs/analytics` and `CostManagementHandler` frontend. Includes KPI cards (total cost, avg per session/turn), cost by provider/model charts, and most expensive sessions/queries. See [Frontend Cost Management Admin UI](#frontend-cost-management-admin-ui).

### 1. Cost Optimization Suggestions

**Feature**: AI-powered cost optimization recommendations.

```
"Your session cost: $0.25
Suggestion: Switch to claude-haiku-4-5 for 60% cost savings on simple queries.
Estimated savings: $0.15 per session"
```

### 2. Cost Projections

**Feature**: Estimate costs before execution.

```python
# Before submitting query
estimated_cost = cost_estimator.estimate(
    query=user_input,
    profile=current_profile,
    history_length=len(conversation_history)
)

# Show to user: "Estimated cost: $0.15 - $0.25"
```

### 3. Cost Allocation Tags

**Feature**: Tag sessions with cost centers for enterprise billing.

```json
{
  "session_id": "abc123",
  "cost_allocation": {
    "department": "Engineering",
    "project": "AI Research",
    "cost_center": "CC-1234"
  },
  "session_cost_usd": 5.67
}
```

### 4. Cost Caching for Common Queries

**Feature**: Detect and cache responses for repeated queries to save costs.

```python
# Check cache before LLM call
cached_response = cost_cache.get(query_hash)
if cached_response and cached_response["age_minutes"] < 60:
    return cached_response["answer"]  # $0.00 cost
else:
    response = await call_llm(...)    # $0.05 cost
    cost_cache.set(query_hash, response)
```

### 5. Multi-Currency Support

**Feature**: Display costs in user's preferred currency.

```python
# Configuration
user_preferences = {
    "currency": "EUR",
    "exchange_rate": 0.92  # USD to EUR
}

# Display
session_cost_eur = session_cost_usd * user_preferences["exchange_rate"]
# Show: "€0.0087" instead of "$0.0094"
```

### 6. Cost Export & Reporting

**Feature**: Export cost data for accounting systems.

```python
# Export to CSV
GET /api/v1/costs/export?start_date=2026-02-01&end_date=2026-02-28&format=csv

# Response
session_id,date,turns,input_tokens,output_tokens,cost_usd
abc123,2026-02-01,5,50000,2000,1.25
def456,2026-02-01,3,30000,1500,0.75
```

---

## Appendix A: File Locations

### Backend — Core

| File | Purpose |
|------|---------|
| `src/trusted_data_agent/core/cost_manager.py` | Cost calculation engine, four-phase cost sync, `ensure_price` / coverage, availability checking |
| `src/trusted_data_agent/auth/consumption_manager.py` | Per-user consumption tracking, rate limiting, quotas, judge usage lines |
| `src/trusted_data_agent/auth/consumption_enforcer.py` | Pre-turn enforcement of rate limits and token quotas |
| `src/trusted_data_agent/core/efficiency_tracker.py` | RAG efficiency and cost savings tracking |
| `src/trusted_data_agent/core/post_turn_usage.py` | One booking for LLM calls made after the turn (`book_post_turn_call`) |
| `src/trusted_data_agent/llm/handler.py` | `_PROVIDER_PRICING_SPECS`, `fetch_live_pricing_sync`, `_persist_provider_pricing` (live pricing) |
| `src/trusted_data_agent/llm/cache_policy.py` | Cache read/write price factors, usage normalisation (`read_usage`, `priced_cost`) |
| `src/trusted_data_agent/llm/stream_usage.py` | One usage per streamed call on OpenAI-compatible LangChain models |
| `src/trusted_data_agent/core/task_scheduler.py` | Daily `platform-cost-price-sync` job |
| `src/trusted_data_agent/auth/models.py` | Database models: LLMModelCost, ConsumptionProfile, UserConsumption, ConsumptionTurn, ConsumptionPeriodsArchive |

### Backend — Integration

| File | Purpose |
|------|---------|
| `src/trusted_data_agent/agent/executor.py` | Per-call cost accumulator, `_resolve_turn_cost_usd`, dual-model config, session naming |
| `src/trusted_data_agent/core/session_manager.py` | `session_cost_total`, `update_turn_token_counts`, `cost_basis` stamp, consumption booking |
| `src/trusted_data_agent/api/routes.py` | Turn details + session GET (session total, legacy turn-cost fallback) |
| `src/trusted_data_agent/api/rest_routes.py` | Cost management REST API (8 endpoints), cost analytics, consumption endpoints |

### Frontend

| File | Purpose |
|------|---------|
| `static/js/ui.js` | Cost display (`updateTokenDisplay`), cost accumulator, terminal event protection |
| `static/js/eventHandlers.js` | Historical reload cost handling |
| `static/js/handlers/sessionManagement.js` | Session switching cost initialization |
| `static/js/handlers/costManagement.js` | Admin cost management UI (Administration → Cost tab) |

### Maintenance

| File | Purpose |
|------|---------|
| `maintenance/backfill_consumption_costs.py` | Populate historical cost data |
| `maintenance/consumption_periodic_jobs.py` | Recurring maintenance tasks |
| `maintenance/reconcile_consumption.py` | Fix data inconsistencies |
| `maintenance/check_token_usage.py` | Verify consumption records |
| `maintenance/check_user_profile.py` | Audit user profiles and limits |

### Configuration

| File | Purpose |
|------|---------|
| `tda_config.json` | Bootstrap `default_model_costs` for startup |

### Documentation

| File | Purpose |
|------|---------|
| `docs/Architecture/COST_TRACKING_ARCHITECTURE.md` | This document |
| `docs/Finops/COST_MANAGEMENT_IMPLEMENTATION.md` | Implementation guide |
| `docs/Architecture/PROMPT_CACHE_ARCHITECTURE.md` | The cache plan per provider (what is served from cache, hence priced at the read factor) |
| `docs/Consumption Profiles/CONSUMPTION_PROFILES.md` | Consumption profiles and enforcement |

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **Turn Cost** | Cost for a single turn (one user query → assistant response) |
| **Session Cost** | Cumulative cost for entire session (sum of all turn costs) |
| **Point-in-Time Snapshot** | Session cost stored at each turn reflecting total cost up to that moment |
| **Legacy Session** | Session created before cost tracking engine (missing turn_cost/session_cost_usd) |
| **Session Naming** | System LLM call to generate descriptive session name (occurs after Turn 1) |
| **Cost Accumulator** | Frontend state object tracking costs during live execution |
| **Authoritative Cost** | Cost calculated and stored by backend (single source of truth) |
| **Fallback Cost** | Configurable default pricing for unknown models (default: $0.10/$0.40 per 1M tokens) |
| **Live Execution** | Real-time query execution with SSE streaming |
| **Historical Reload** | Loading past turn data by clicking turn avatar |
| **Dual-Model** | Profile using separate strategic and tactical LLM models |
| **Micro-dollars** | Cost storage unit in consumption tables: USD × 1,000,000 (avoids floating-point) |
| **Bootstrap Costs** | Default model pricing loaded from `tda_config.json` at CostManager startup |
| **Consumption Profile** | Rate limiting and quota tier (prompts/hour, tokens/month) assigned to users |
| **Consumption Turn** | Per-turn audit record in `consumption_turns` table |
| **RAG Efficiency** | Token/cost savings from RAG-guided responses (Turn N < Turn N-1) |
| **Model Deprecation** | Marking models as unavailable after provider API availability check |
| **ARN Normalization** | Stripping AWS Bedrock ARN prefixes and regional identifiers for cost lookup |
| **LiteLLM Sync / Cost Sync** | Four-phase process: availability check, LiteLLM pricing, live provider pricing, coverage |
| **Cost Basis** | `priced` or `estimated` (fallback rate) — stamped on each persisted turn |
| **Price Coverage** | Every model in use priced or named (`price_coverage` / `fill_coverage`) |
| **Manual Override** | Admin-set pricing protected from LiteLLM sync overwrite (`is_manual_entry=True`) |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude Code | Initial architecture document |
| 2.0 | 2026-03-03 | Claude Code | Major update: Added Cost Management REST API section (7 endpoints). Added Consumption Tracking Subsystem section (ConsumptionManager, 4 DB tables, rate limiting, quotas, monthly rollover). Added Efficiency Tracker Subsystem section (RAG savings). Added Frontend Cost Management Admin UI section (CostManagementHandler). Fixed database schema (wrong column names/types, missing columns). Fixed CostManager API (wrong method names, added fallback pricing, ARN normalization, bootstrap costs, availability checking). Updated Architecture Components diagram. Fixed Edge Cases (fallback pricing behavior). Moved implemented features from Future Enhancements. Updated Appendix A file locations. Updated Glossary with 10 new terms. |
| 2.1 | 2026-10-30 | Claude Code | Doc audit: four-phase cost sync + coverage endpoint (8 endpoints), `ensure_price` order corrected (local before LiteLLM; Azure source tags), session cost served as `session_cost_total`, executor `turn_cost` = Σ per-call accumulator (`_resolve_turn_cost_usd`), `update_turn_token_counts(added_cost_usd, additive)`, `cost_basis`/`cache` fields, judge usage lines, session locking, real gate list (replaces nonexistent `test_cost_tracking.py`), `_PROVIDER_PRICING_SPECS` naming, schema `source` values, removed line counts and a dead plan link. |

---

**End of Document**
