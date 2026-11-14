# REST → MCP Tier Matrix

> Maps every Uderia REST endpoint to the **MCP consumer tier** at which it should be exposed as a
> tool by the Tier 2.1 MCP server (`src/trusted_data_agent/mcp_server/`). This is the source of
> truth for the MCP server's tiered tool catalog. Generated from a full codebase enumeration
> (2026-06-28): **552 endpoints across 16 route files**. The tally below is that enumeration's; the
> API has grown since (about 800 route registrations across 24 files in `api/` on 2026-10-30), so
> newer endpoints may be unclassified here. The implemented catalog section is current as of
> 2026-10-30.

## Legend

| Tier | Meaning — which consumer-profile surface exposes the endpoint |
|---|---|
| **all** | Every consumer profile (user · developer · admin). Safe, broadly-useful reads of own/public data **plus the core agent-invocation flow** (create session, submit query, poll task, list profiles/KGs). The base tier. |
| **developer** | `developer` + `admin` profiles. Introspection / diagnostics of the **caller's own** resources (sessions, collections, flows, evals, configs, own usage). |
| **admin** | `admin` profile only. Instance-wide, cross-user, or governance **reads** (system consumption, user lists, app/feature config). |
| **N/A** | Never an MCP tool. Auth/OAuth/SSO/SAML flows, SSE/streaming, file up/download/export blobs, callbacks, and **all state-mutating writes** (the exception is the core ask flow, which is `all`). |

**Hard gate:** the tier is a ceiling, not a grant. The MCP server clamps the exposed surface to
`min(launch --profile, the access token's actual user tier)`, and **every call is still
authorized server-side by the REST layer** — a tool that exceeds the token's rights fails at REST.

**Write policy (v1):** the MCP server exposes **reads + the ask flow**, plus ONE write: performing a
**signed action** (`perform_action`, `developer`; PROP-008 §5.6 — a consumer reads, a builder acts),
which writes only through a signed contract under its posture and always previews first. All other
mutations are `N/A` for v1 even where a tier is conceptually appropriate; a future increment may promote
selected own-resource creates (e.g. create dashboard) to `developer`. The matrix records the
*conceptual* tier so that promotion is a lookup, not a re-analysis.

## Tally

| Tier | Count | Becomes an MCP tool in v1? |
|---|---|---|
| **all** | 36 | ✅ (the 5 core + safe listings) |
| **developer** | 158 | ◑ the read subset (own-resource introspection) |
| **admin** | 51 | ◑ the read subset (governance/instance reads) |
| **N/A** | 307 | ❌ never |
| **Total** | **552** | |

Per source: `rest_routes.py` 271 · `auth/admin/system-prompts/provenance/contact` 150 · feature
routes (agent_pack/flow/dashboard/skills/kg-marketplace/component-instance/canvas/knowledge/connector) 130 · `routes.py` (UI/SSE) 51 (all N/A).

---

## v1 MCP tool catalog (the exposable READ surface)

These are the endpoints the MCP server wraps as tools, grouped by the tier that gates them. The
implemented v1 catalog (`mcp_server/server.py`) is a curated subset of this — the rest are the
sanctioned backlog for future tools.

### Tier `all` — every consumer profile

| MCP tool (suggested) | REST | Notes |
|---|---|---|
| `ask_profile` | POST `/v1/sessions` → POST `/v1/sessions/<id>/query` → GET `/v1/tasks/<id>` | The core flow. Governed: grounding gate + guardrails apply. ✅ implemented |
| `ask_profile` (genie) | POST `/v1/sessions/<id>/genie-query` | Genie coordinator variant of the ask flow |
| `run_prompt` | POST `/v1/prompts/<name>/execute` | Execute a named MCP prompt (agent flow) |
| `list_profiles` | GET `/v1/profiles` | ✅ implemented |
| `get_default_profile` | GET `/v1/profiles/default` | |
| `list_knowledge_graphs` | GET `/v1/knowledge-graph/list` | ✅ implemented — but at `developer` in `TOOL_CATALOG` |
| `list_data_products` | GET `/v1/data-products?kg_id=` | ✅ implemented — the signed OBDA-READ products on one graph |
| `query_data_product` | POST `/v1/data-products/<product>/query` | ✅ implemented — a read despite the POST: compiled, read-only, with a provenance receipt |
| `kg_stats` | GET `/v1/knowledge-graph/stats` | |
| `list_industry_models` | GET `/v1/knowledge-graph/industry-models` | |
| `whoami` | GET `/api/v1/auth/me` (+ `/me/features`, `/me/panes`) | Identity/feature reads (also the hard-gate input) |
| `provenance_public_key` | GET `/api/v1/provenance/public-key` | Verify signed attestations |
| `list_flows` | GET flows list | Read-only flow listing |
| `list_dashboards` | GET dashboards list | |
| `list_skills` / `list_activated_skills` | GET skills list / activated | |
| `search_knowledge` | knowledge repository search | RAG search (read) |
| marketplace browse | GET marketplace collections/extensions/skills/KGs/components | Public catalog reads |

### Tier `developer` — own-resource introspection (read subset)

| Capability | REST |
|---|---|
| `list_knowledge_graphs` ✅ | GET `/v1/knowledge-graph/list` |
| `list_actions` ✅ | GET `/v1/actions` (`?kg_id=` or `?profile_id=`) |
| `perform_action` ✅ (the one write) | POST `/v1/actions/<name>/preview` → POST `/v1/actions/<name>/perform` (two-step, `invocation_hash`) |
| `list_sessions` ✅ | GET `/v1/sessions` |
| `get_session_details` | GET `/v1/sessions/<id>/details` |
| `session_analytics` | GET `/v1/sessions/<id>/context-analytics`, `/v1/sessions/analytics` |
| `list_collections` ✅ | GET `/v1/rag/collections` (+ `/<id>`, `/<id>/rows`) |
| `get_my_usage` ✅ | GET `/v1/consumption/summary` (+ `/turns`, `/history`) |
| `profile_resources` | GET `/v1/profiles/<id>/resources`, `/components`, `/classification` |
| `list_mcp_servers` | GET `/v1/mcp/servers` |
| `list_llm_configs` | GET `/v1/llm/configurations` (+ `/<id>/context-limit`) |
| `list_vectorstore_configs` | GET `/v1/vectorstore/configurations`, `/allowed-backends` |
| `kg_introspection` | GET `/v1/knowledge-graph/{subgraph,schema-overview,node-neighborhood,graph-spec,context,versions,assignments}` |
| `kg_store_configs` | GET `/v1/kg-store/{backends,configurations}` |
| `list_extensions` | GET `/v1/extensions`, `/activated`, `/<name>/source` |
| `list_components` | GET `/v1/components` (+ `/<id>`, `/manifest`) |
| `list_flows_detail` | GET flow def / versions / variables / tools / runs / node-data / run |
| `dashboard_detail` | GET dashboard / tiles |
| `skill_detail` | GET skill content / profile-skills |
| `canvas_detail` | GET canvas / versions; `/v1/canvas/connections` |
| `db_connections` | GET `/v1/database-connections` (+ `/<id>`) |
| `evals_read` | GET `/v1/evals/*` (datasets, runs, scores, online, dashboard, agents/*, diff, annotations) |
| `scheduler_read` | GET `/v1/scheduled-tasks` (+ `/<id>`, `/<id>/runs`), `/v1/scheduler/status` |
| `system_prompts_read` | GET `/api/v1/system-prompts/*` (list, content, params, versions, diff) |
| `provenance_read` | GET `/api/v1/sessions/<id>/provenance` (+ turn, verify) |
| `credentials_meta` | GET `/v1/credentials`, `/v1/config/stored-credentials`, own audit-logs, quota-status |
| `connector_introspection` | GET `/v1/platform-connectors` (+ `/<id>`, `/<id>/tools`), `/v1/connector-registry/{sources,servers}` |

### Tier `admin` — governance / instance-wide reads (read subset)

| Capability | REST |
|---|---|
| `system_usage` ✅ | GET `/v1/consumption/system-summary` |
| `list_user_usage` ✅ | GET `/v1/consumption/users`, `/api/v1/auth/user/consumption-summary` |
| `list_users` | GET `/api/v1/auth/admin/users`, `/v1/admin/users` (+ `/<id>`) |
| `admin_stats` | GET `/v1/admin/stats` |
| `app_config_read` | GET `/v1/admin/app-config` |
| `feature_governance` | GET `/v1/admin/features`, `/v1/admin/panes` |
| `cost_admin` | GET `/v1/costs/models`, `/v1/costs/analytics` |
| `governance_settings_read` | GET `/v1/admin/{genie,knowledge,vectorstore,kg-marketplace,extension,skill,component,tts,window,rag-offload}-settings` |
| `kg_store_settings` | GET `/v1/kg-store/settings` |
| `sso_saml_read` | GET `/api/v1/auth/{sso,saml}/configurations` (+ users, sync-history) |
| `rate_limit_read` | GET `/api/v1/auth/admin/rate-limit-settings`, `/consumption-profiles` |
| `platform_scheduler_read` | GET `/v1/admin/scheduler/platform-jobs` (+ `/<id>/runs`) |

### N/A — never an MCP tool (307)

Grouped by why (full per-endpoint classification in the enumeration source). These are **not**
exposed at any tier:

| Category | Examples | Why |
|---|---|---|
| **State mutations** (the bulk) | POST/PUT/PATCH/DELETE on profiles, collections, dashboards, flows, skills, KGs, configs, connectors, extensions, components, scheduled tasks, eval datasets | An MCP read-surface must not mutate platform state (v1). Authorization aside, side effects are out of scope. |
| **Auth / identity flows** | login, logout, register, password, email-verify, refresh, tokens create/list/delete, OAuth, SSO, SAML (all of `auth_routes.py` bar `/me*`) | Auth is the client's job; the MCP server receives an already-minted token. |
| **SSE / streaming** | `/ask_stream`, `/api/notifications/subscribe`, flow `/stream`, any `…/stream` | Streaming is a UI transport, not a tool result. The ask flow uses task-polling instead. |
| **File / blob I/O** | upload, download, export (collection/KG/extension/component/provenance), import | Binary streams aren't MCP tool payloads. |
| **UI / interactive** (`routes.py`) | login pages, `/simple_chat`, TTS, UI session rename/delete, `/configure`, toggles | The cookie/JWT web-UI layer; duplicated by `/v1` or UI-only. |
| **Callbacks / webhooks** | OAuth/SSO/SAML callbacks, contact form | External-trigger endpoints, not callable tools. |

---

## Audit findings (from the enumeration)

1. **Duplicate route** — FIXED 2026-10-30: `GET /api/v1/system-prompts/list` was registered twice;
   the second, unreachable handler (`list_system_prompts`) was removed — `list_all_prompts` is the
   one that always served.
2. **Inline-admin gates** — a few endpoints enforce admin via an internal `current_user.is_admin`
   check rather than a decorator (`/api/v1/auth/user/consumption-summary`, flow
   `toggle-platform`/`platform-meta`). Classified `admin` regardless.
3. **License-tier (not admin) gates** — system-prompt **mutations** use
   `require_prompt_engineer_or_enterprise` (PE/Enterprise license tier, distinct from the
   user/developer/admin role tier). All are mutations → `N/A` for MCP regardless.
4. **Decorator-less auth** — skills / kg-marketplace / component-instance blueprints resolve the
   user inside the body (`get_current_user_from_request()`) rather than via `@require_auth`. Still
   authenticated; classified normally.

## Source

Full per-endpoint classification was produced by a three-way parallel enumeration of
`src/trusted_data_agent/api/*.py` on 2026-06-28. Re-run that enumeration when endpoints change.
The MCP server's implemented catalog lives in `src/trusted_data_agent/mcp_server/server.py`
(`TOOL_CATALOG`, 12 tools: 4 `user` · 6 `developer` · 2 `admin`) and must stay a subset of the
`all`/`developer`/`admin` rows above (the one sanctioned write being `perform_action`).
