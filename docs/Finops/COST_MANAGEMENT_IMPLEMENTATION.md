# Cost Management System Implementation

## Overview
A comprehensive cost tracking and management system for LLM token consumption has been successfully implemented.

**Last verified against the code:** 2026-10-30. This is the implementation guide; the live contract
(turn/session cost, cache-aware pricing, invariants, consumption ledger) is
[COST_TRACKING_ARCHITECTURE.md](../Architecture/COST_TRACKING_ARCHITECTURE.md).

## Components Implemented

### 1. Database Layer
- **New Table**: `llm_model_costs`
  - Stores pricing for each provider/model combination
  - Tracks input/output cost per 1M tokens
  - Supports manual entries, LiteLLM syncing and live provider price APIs
  - Includes fallback pricing for unknown models and an `is_deprecated` flag from the availability check
- **Creation**: created at startup from the ORM model (the one-time `maintenance/migrate_llm_model_costs.py` was removed); bootstrap prices from `tda_config.json` `default_model_costs`
- **SQLAlchemy Model**: `LLMModelCost` in `auth/models.py`

### 2. Backend Services
- **Cost Manager**: `core/cost_manager.py`
  - Singleton service for all cost operations
  - Four-phase cost sync (`sync_from_litellm`): provider availability check, LiteLLM pricing, live provider price APIs (OpenRouter, Friendli — `LIVE_PRICING_PROVIDERS`), coverage
  - `ensure_price()`: every model put into use gets a price or is named (run on LLM-configuration save, daily, on demand)
  - Cost calculation based on actual model pricing, cache-aware (`calculate_cost_with_cache`)
  - Fallback mechanism for unknown models (logged once per model; the turn is stamped `cost_basis: estimated`)
  - 5-minute in-memory price cache, cleared after every sync
  - CRUD operations for cost entries
- **Daily refresh**: platform job `platform-cost-price-sync` (03:15 UTC, `core/task_scheduler.py`) runs the full sync

### 3. REST API Endpoints
All endpoints require admin authentication:

- `GET /api/v1/costs/coverage` - Which models in use have a price (`?fill=1` fills what it can)
- `POST /api/v1/costs/sync` - Full cost sync (availability, LiteLLM, live price APIs, coverage)
- `GET /api/v1/costs/models` - Get all model costs
- `PUT /api/v1/costs/models/<id>` - Update a model cost
- `POST /api/v1/costs/models` - Add manual cost entry
- `DELETE /api/v1/costs/models/<id>` - Delete cost entry
- `PUT /api/v1/costs/fallback` - Update fallback cost
- `GET /api/v1/costs/analytics` - Get comprehensive cost analytics

### 4. Frontend UI

#### New Administration Tab: "Cost"
Located in the Administration pane with two main sections:

**A. Cost Analytics Dashboard (Top Section)**
- **KPI Cards**:
  - Total Cost (across all sessions)
  - Average Cost per Session
  - Average Cost per Turn
  - Total Sessions Analyzed
  
- **Visualization Charts**:
  - Cost by Provider (bar chart)
  - Top 5 Expensive Models (bar chart)

**B. Cost Configuration Table (Bottom Section)**
- **Fallback Cost Configuration**:
  - Adjustable default costs for unknown models
  - Input/Output cost per 1M tokens
  
- **Model Cost Table**:
  - Displays all provider/model pricing
  - Inline editing of costs
  - Source tracking (`config_default`, `manual`, `litellm`, `openrouter`, `friendli`, `local`, `inferred`, `azure-base-model`, `system_default`)
  - Search, deprecated-model toggle, infinite scroll, optional 30 s auto-refresh
  - Last updated timestamps
  - Delete functionality for manual entries
  
- **Actions**:
  - "Sync from LiteLLM" button - Runs the full cost sync (incl. live provider prices and coverage)
  - "Add Manual Entry" button - Add custom model costs

### 5. JavaScript Handler
- **File**: `static/js/handlers/costManagement.js`
- **Features**:
  - Real-time cost data loading
  - Interactive table with inline editing
  - Chart rendering for analytics
  - Toast notifications for user feedback
  - Automatic data refresh after updates

### 6. Updated Dependencies
- Added `litellm>=1.0.0` to `requirements.txt`

### 7. Updated Analytics
- **Execution Dashboard** (`/api/v1/sessions/analytics`):
  - Now uses actual model-specific costs instead of $0.01/1K average
  - Iterates through all sessions and calculates real costs
  - More accurate cost estimates

## Key Features

### Hybrid Approach
- **Precedence**: `manual` > `config_default` > live provider API (`openrouter`, `friendli`) > `litellm` — a LiteLLM sync never overwrites the higher sources
- **LiteLLM**: covers providers without a validated live price API
- **Vendor mirror**: a direct Google/Anthropic/OpenAI model with no row of its own is priced from OpenRouter's row for the same id
- **Manual Override**: Admins can add/edit any model
- **Fallback Mechanism**: Configurable default for unknown models
- **Full Autonomy**: All data stored locally in SQLite

### Cost Calculation
```python
cost = (input_tokens / 1_000_000) * input_cost_per_million + 
       (output_tokens / 1_000_000) * output_cost_per_million
```

A call with a prompt-cache reading prices the cached prefix at the provider's read factor
(`calculate_cost_with_cache`); a turn's cost is the sum of its per-call costs, each at the model
that served it.

### Provider Inference
When syncing from LiteLLM, the system automatically infers providers from model names:
- `gpt-*` → OpenAI
- `claude-*` → Anthropic
- `gemini-*` → Google
- `titan-*`, `nova-*` → Amazon
- And more... (`CostManager._infer_provider_from_model`)

## Usage Instructions

### For Administrators

1. **Access Cost Tab**:
   - Navigate to Administration pane
   - Click the "Cost" tab

2. **Initial Setup**:
   - Click "Sync from LiteLLM" to populate pricing data
   - Review and adjust fallback costs if needed

3. **Add Custom Model**:
   - Click "Add Manual Entry"
   - Enter provider, model, input/output costs
   - Saves with "manual" source tag

4. **Edit Existing Costs**:
   - Modify values directly in the table
   - Click "Save" button for that row
   - Changes marked as manual overrides

5. **Monitor Costs**:
   - View real-time analytics in dashboard
   - Track spending by provider/model
   - Identify expensive queries

### For Developers

**Using Cost Manager**:
```python
from trusted_data_agent.core.cost_manager import get_cost_manager

cost_manager = get_cost_manager()

# Calculate cost for a turn
cost = cost_manager.calculate_cost(
    provider="Google",
    model="gemini-2.5-flash",
    input_tokens=1000,
    output_tokens=500
)

# Get pricing for a model
costs = cost_manager.get_model_cost("Google", "gemini-2.5-flash")
# Returns: (input_cost_per_million, output_cost_per_million)

# Make sure a model about to be used has a price (or learn that it has none)
r = cost_manager.ensure_price("Google", "gemini-3.1-flash-lite")
# r = {"status": "priced" | "filled" | "unpriced", "source": ..., "input": ..., "output": ...}

# Full sync (availability check needs an admin user's credentials)
results = cost_manager.sync_from_litellm(check_availability=True, user_uuid=admin_uuid)
```

## Database Schema

```sql
CREATE TABLE llm_model_costs (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_cost_per_million REAL NOT NULL,
    output_cost_per_million REAL NOT NULL,
    is_manual_entry BOOLEAN NOT NULL DEFAULT 0,
    is_fallback BOOLEAN NOT NULL DEFAULT 0,
    is_deprecated BOOLEAN NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    last_updated DATETIME NOT NULL,
    notes TEXT,
    UNIQUE(provider, model)
);
```

## Future Enhancements

1. **Cost Budgets & Alerts**:
   - Set spending limits per user/session *(token quotas and prompt rate limits exist via consumption profiles — see `docs/Consumption Profiles/`; USD budgets and email alerts do not)*
   - Email alerts when thresholds exceeded

2. **Cost Trends**:
   - Historical cost tracking over time *(partly: the analytics endpoint returns `cost_trend`; monthly periods are archived in `consumption_periods_archive`)*
   - Monthly/weekly spending reports
   - Cost forecasting

3. **RAG Cost Attribution**:
   - Update RAG savings to use actual model costs *(the efficiency tracker records cost saved per RAG-guided turn — `core/efficiency_tracker.py`)*
   - Track cost impact of champion cases

4. **Export Capabilities**:
   - CSV export of cost data
   - Detailed cost reports

5. **Advanced Analytics**:
   - Cost efficiency metrics
   - Provider comparison
   - Model ROI analysis

## Testing Checklist

- [x] Table created at startup
- [x] Cost Manager syncs from LiteLLM
- [x] REST API endpoints respond correctly
- [x] Admin UI displays Cost tab
- [x] Cost analytics dashboard loads
- [x] Cost table displays and updates
- [x] Fallback cost can be modified
- [x] Manual entries can be added/deleted
- [x] Analytics endpoint uses actual costs

## Setup

No migration step is needed: the table is created at startup and seeded from
`tda_config.json`. `litellm` is in `requirements.txt` (`pip install -r requirements.txt`); without
it the sync stops at once with `LiteLLM library not available` (no live-price or coverage phase either).

Gates: `test/test_cost_vendor_mirror.py`, `test/test_turn_cost.py`,
`test/test_consumption_attribution.py` (full list in COST_TRACKING_ARCHITECTURE.md → Testing).
