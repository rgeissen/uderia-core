# Proof Pack — P4: FinOps / Cost Controls for AI

**What this proves:** actual-token metering across the model providers, precedence-ordered live pricing, an idempotent per-call ledger, per-agent cost attribution (incl. coordinator self-cost), and four enforceable consumption tiers. Usage is read from each provider's **own** usage object, priced against a **precedence-ordered** source chain (manual > config > live-API > LiteLLM), folded once via an idempotent `call_id` ledger and attributed to the specific agent; end to end, the invariant `session_cost = Σ turn_cost` holds exactly.
**What you can check:** the real numbers below, read from this instance, and the files they come from.
**Status:** ✅ Built · real numbers below · Last updated 2026-06-28

---

## 1. Token metering — actual provider tokens, never estimates

Tokens come from each provider's **own** response usage object (`llm/handler.py`):

| Provider | Usage field | handler.py |
|---|---|---|
| Google (Gemini) | `usage_metadata.prompt_token_count` | 740-742, 845-847 |
| Anthropic | `usage.input_tokens` / `output_tokens` | 875, 914 |
| OpenAI / Azure / Friendli / OpenRouter / xAI / MLX | `usage.prompt_tokens` / `completion_tokens` | 916, 959 |
| Ollama | `prompt_eval_count` / `eval_count` | 971 |
| AWS Bedrock | `usage.input_tokens` / `inputTokens` / `prompt_token_count` | 1124-1141 |

The extracted counts feed `update_token_count(...)` (`handler.py:1184`) and accumulate per-turn (`executor.py:879-880`) and per-session (persisted session file). Each LLM call emits a `token_update` SSE event (`executor.py:1229-1238`) carrying `{statement_input, statement_output, turn_input, turn_output, total_input, total_output, call_id, cost_usd}` → the frontend TURN COST / SESSION COST cards.

---

## 2. Cost attribution — precedence-ordered pricing, idempotent ledger

**`CostManager.calculate_cost`** (`core/cost_manager.py:442-467`) is the single calculation site:
```python
costs = self.get_model_cost(provider, model) or self.get_fallback_cost()  # never $0.00
return (in_tok/1e6)*in_per_1M + (out_tok/1e6)*out_per_1M
```

**Source precedence (highest→lowest): `manual` > `config_default` > live provider API > `litellm`** — enforced, not just documented:
- `LIVE_PRICING_PROVIDERS = {'OpenRouter','Friendli'}` (`cost_manager.py:34-37`).
- A LiteLLM sync overwrites a row only `if not existing.is_manual_entry and existing.source not in _LITELLM_PROTECTED_SOURCES` (`cost_manager.py:162`) — a stale LiteLLM key can never downgrade an authoritative price.
- **Live pricing APIs** fetched credential-free, synchronously, in Phase 3 of the sync (`cost_manager.py:199-223`, `handler.py:1281-1363`): OpenRouter (`/api/v1/models`, per-token ×1M) and Friendli (`/serverless/v1/models`, per-1M). Google/OpenAI/Anthropic/Azure/Bedrock/xAI stay on LiteLLM (no usable public per-token API); MLX/Ollama are local ($0).

**Idempotent per-call ledger** — every LLM call mints `call_id = uuid4()` (`executor.py:1203`) carried on the `token_update`; the frontend keys cost accumulation by `call_id`, so a re-delivered SSE event folds **once** (the documented fix for "session cost could be 2× actual").

**Real pricing rows** (`tda_auth.db` `llm_model_costs`, 24 priced models):

| provider | model | in/1M | out/1M | source |
|---|---|---|---|---|
| Friendli | MiniMaxAI/MiniMax-M2.1 | $0.30 | $1.20 | config_default |
| Friendli | meta-llama-3.3-70b-instruct | $0.60 | $0.60 | config_default |
| Friendli | Qwen/Qwen3-235B-A22B-Instruct-2507 | $0.20 | $0.80 | config_default |
| **Fallback** | Unknown Models | $0.10 | $0.40 | config_default (`is_fallback=1`) |

The fallback (`get_fallback_cost`, `cost_manager.py:425-440`) guarantees real token usage is **never** priced at $0.00.

---

## 3. Per-agent cost attribution

- **Each turn carries its own `turn_cost`** in `workflow_history`. `RunRecord.turn_cost_usd` (`eval/types.py:378-383`) reads it as the scored metric — **SELF-cost**, so portfolio sums never double-count.
- **`CostCeilingScorer`** (`eval/scorers/deterministic.py:101-122`, dimension `cost`) bands it into `eval.dim.cost.score` / `eval.cost_usd` on the eval span (stamped with `agent.profile_tag` / `agent.invocation`).
- **Genie SELF-cost** (`coordinate_engine.py:38-110,666-689`): each expert's `turn_cost` is read from its own child turn (attribution-only — already billed); the coordinator's inclusive figure is a **display-only `genie_rollup`**, never summed into portfolio.
- **Per-agent roll-up** (`eval/analysis.py:88-257`): `online_quality_by_agent()` → per-tag `avg_cost` with `direct_turns`/`expert_turns` split; `agent_kpi_dashboard()` → portfolio + per-agent `avg_cost`.

**Real per-agent roll-up** (all 514 spans in `tda_eval/online_eval.jsonl`, `eval.dim.cost.score`):

| Agent (profile tag) | turns scored | Σ cost | avg/turn |
|---|---|---|---|
| **IDEAT** (Ideate) | 40 | $0.4931 | $0.012328 |
| **OPTIM** (Optimize) | 474 | $0.7551 | $0.001593 |
| **Portfolio** | 514 | **$1.2482** | $0.002433 (median $0.0012; min $0.0008, max $0.1134) |

**513 of 514** spans carry a non-zero metered cost — essentially every executed turn is cost-attributed to a specific agent.

---

## 4. Budgets & consumption enforcement — fail-closed, audited

**Four consumption tiers** (`tda_auth.db` `consumption_profiles`, real rows):

| Profile | prompts/hr | prompts/day | config-changes/hr | input tok/mo | output tok/mo |
|---|---|---|---|---|---|
| Free | 50 | 500 | 5 | 100,000 | 50,000 |
| Pro | 200 | 2,000 | 20 | 500,000 | 250,000 |
| Enterprise | 500 | 5,000 | 50 | 2,000,000 | 1,000,000 |
| **Unlimited** (default) | 1,000 | 10,000 | 100 | ∞ | ∞ |

**Enforcement** (gated before any LLM call):
- `ConsumptionEnforcer.can_execute_prompt()` (`auth/consumption_enforcer.py:157-218`) — hourly+daily prompt limits + monthly token quotas.
- `ConsumptionManager.check_rate_limits()` / `check_token_quota()` (`consumption_manager.py:183-230`) — token-bucket counters whose windows **self-heal on every access** (`heal_time_windows`, `consumption_manager.py:90` — overdue hourly/daily resets + monthly quota rollover applied lazily, so quotas enforce against the correct period even if no maintenance job ever fires; Aug 2026).
- Executor blocks fail-closed (`executor.py ~2180-2231`): a **real** limit violation raises `ValueError` and is **re-raised** → execution blocked; REST returns 429 / SSE `error`.
- **Apr-2026 hardening**: the only fail-open path (an *infrastructure* failure of the check itself) now increments a process audit counter `APP_STATE["consumption_enforcement_bypasses"]` and emits a visible `consumption_warning`-class SSE `Security Warning`. The move from *silent* fail-open to *audited* enforcement.

**Per-turn audit write** (`ConsumptionManager.record_turn`, `consumption_manager.py:258-365`): a `ConsumptionTurn` row with `cost_usd_cents` stored as **micro-dollars** (USD × 1,000,000 — avoids float drift) plus tokens/provider/model/status, and increments monthly+daily aggregates. Post-processing/extension costs folded via `update_turn_tokens()`.

---

## 5. Admin / governance surface (charter #13, #18 — deployment control is admin-only)

- **Cost API** (`api/rest_routes.py`, `@require_admin`): `POST /v1/costs/sync`, `GET/POST/PUT/DELETE /v1/costs/models[/<id>]`, `PUT /v1/costs/fallback`, `GET /v1/costs/analytics` (total cost, by-provider, by-model, avg/session, avg/turn, most-expensive sessions).
- **Consumption governance** (`api/auth_routes.py`): `GET/POST/PUT /admin/consumption-profiles[/<id>]`, `PUT /admin/users/<id>/consumption-profile`; user/admin summaries via `/v1/consumption/*`.
- **Admin UI**: `static/js/handlers/costManagement.js` — analytics KPI cards, cost-by-provider chart, top-expensive models, inline-editable pricing, LiteLLM sync button.

---

## 6. End-to-end cost-attribution demo (real session)

Real session, **Google / gemini-3.1-flash-lite**:

| Turn | input tok | output tok | `turn_cost` | `session_cost_usd` |
|---|---|---|---|---|
| 1 | 14,607 | 487 | $0.001656 | $0.001656 |
| 2 | 26,559 | 1,060 | $0.003080 | **$0.004735** |

**Verifiable chain:**
1. **Meter** — Gemini `usage_metadata.prompt_token_count` → 26,559 in / 1,060 out (`handler.py:740`).
2. **Attribute** — `CostManager.calculate_cost("Google","gemini-3.1-flash-lite",…)` → `turn_cost=$0.003080`.
3. **Invariant** — `$0.004735 = $0.001656 + $0.003080` ⇒ `session_cost_usd[N] = Σ turn_cost[1..N]` holds **exactly**.
4. **Live KPI** — `token_update` SSE (`{…, call_id:<uuid>, cost_usd}`) → TURN/SESSION COST cards.
5. **Consumption** — `record_turn(…, cost_usd_cents=3080)` writes the audit row + adds 26,559/1,060 to the user's monthly `UserConsumption` → counted against (e.g.) the Pro quota.
6. **Per-agent** — eval span stamps `agent.profile_tag` + `eval.dim.cost.score=$0.003080` → that agent's `avg_cost`.

(Multi-provider confirmation: a second real session priced **OpenRouter / deepseek-v4-flash** at $0.001146/turn via the live-pricing path.)

---

## 7. Reproduce it

```bash
# Real pricing rows
sqlite3 tda_auth.db "SELECT provider,model,input_cost_per_million,output_cost_per_million,source FROM llm_model_costs LIMIT 10;"
# Real consumption tiers
sqlite3 tda_auth.db "SELECT profile_name,max_prompts_per_hour,max_input_tokens_per_month FROM consumption_profiles;"
# Per-agent cost roll-up from the eval sink
python3 -c "import json,collections;a=collections.Counter();c=collections.Counter()
[ (a.__setitem__(d['attributes'].get('agent.profile_tag'),a[d['attributes'].get('agent.profile_tag')]+1),
   c.__setitem__(d['attributes'].get('agent.profile_tag'),c[d['attributes'].get('agent.profile_tag')]+(d['attributes'].get('eval.dim.cost.score') or 0)))
  for d in map(json.loads,open('tda_eval/online_eval.jsonl'))]
print({k:(a[k],round(c[k],4)) for k in a})"
```

**Public artifact:** [`docs/Architecture/COST_TRACKING_ARCHITECTURE.md`](../Architecture/COST_TRACKING_ARCHITECTURE.md).

---

### Key code anchors
| Concern | File:line |
|---|---|
| Token extraction (10 providers) | `llm/handler.py:740-1141,1184,1211` |
| Cost calc | `core/cost_manager.py:442-467`; per-call `executor.py:1046-1060` |
| Pricing precedence / protection | `cost_manager.py:34-42,162`; live pricing `cost_manager.py:199-223`, `handler.py:1281-1363` |
| `token_update` / `call_id` ledger | `executor.py:1203,1229-1238` |
| Genie SELF-cost | `coordinate_engine.py:38-110,666-689`; `eval/types.py:378-383` |
| Per-agent roll-up | `eval/online.py:187-212`; `eval/analysis.py:88-257` |
| Fail-closed enforcement + bypass audit | `executor.py:~2180-2231`; `auth/consumption_enforcer.py:157-218`; `consumption_manager.py:89-304` |
| Admin endpoints | `api/rest_routes.py` (`/v1/costs/*`), `api/auth_routes.py` (`/admin/consumption-profiles`) |
