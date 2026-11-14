# Uderia as an MCP Server (Tier 2.1)

**Target:** A3 (agent interoperability / MCP) — the "wide open" agentic field (0 of 6 vendors GREEN).
**Status:** IMPLEMENTED & LIVE-VERIFIED — a 12-tool, tier-segmented catalog (ask, signed data products, signed actions, introspection, usage) over STDIO + streamable-HTTP. A real `fastmcp.Client` round-trip against a live instance returned a profile's answer (2026-06-28). Doc checked against code 2026-10-30.

> Uderia has always been an MCP **consumer** (it connects to MCP data-source servers and platform
> connectors). Tier 2.1 makes it an MCP **provider**: any external MCP client — Claude Desktop,
> Cursor, another agent — can call a governed Uderia profile *as a tool*. What makes this useful is
> trust: the answer passes through the profile's full pipeline, **including the Tier 1.1 grounding
> gate and the Tier 1.3 P2 guardrails**, before it leaves the building. *A trusted agent you can
> call as a tool.*

---

## 1. Architecture — a thin facade over the REST API

The MCP server adds **no new execution path**. Each MCP tool call becomes a sequence of
authenticated REST calls against Uderia's own API, so every bit of session, profile-override,
governance, grounding-gate, and guardrail logic is reused unchanged.

```
External MCP client (Claude Desktop / Cursor / agent)
        │  MCP wire (STDIO or streamable-HTTP)
        ▼
Uderia MCP server  (src/trusted_data_agent/mcp_server/)
  server.py  — FastMCP app + @tool functions
  client.py  — UderiaRestClient (httpx facade)
        │  REST  (Bearer = Uderia access token)
        ▼
Uderia REST API  →  run_agent_execution  →  grounding gate + guardrails
```

| File | Role |
|---|---|
| `mcp_server/client.py` | `UderiaRestClient` — pure httpx facade: `create_session` → `submit_query` → `poll_task`, plus `list_profiles` / `resolve_profile_id` / `list_knowledge_graphs`, the data-product wrappers (`list_data_products`, `query_data_product`) and the action wrappers (`list_actions`, `preview_action`, `perform_action` via `_json_or_refusal`, which returns a 4xx JSON refusal instead of raising). No fastmcp imports → unit-testable with an injected `httpx.MockTransport`. |
| `mcp_server/server.py` | The `FastMCP` app and the `@mcp.tool()` functions. Each builds a per-call client and delegates. |
| `mcp_server/__main__.py` | CLI: `python -m trusted_data_agent.mcp_server [--transport stdio\|http] [--host] [--port]`. |

## 2. Tools — tier-segmented catalog

Tools are segmented by **consumer tier** (see §3a). Each declares the lowest tier that may see it.

| Tool | Tier | Signature → wraps |
|---|---|---|
| `ask_profile` | all | `(prompt, profile?, answer_to?, answer?) → str` — ask a governed profile (grounding-gated + guardrailed). Resolves tag→id, creates a session, submits with the override, polls to terminal status (`complete`; `failed`/`error` raise). `result.guardrail_blocked` is surfaced. |
| `list_profiles` | all | `() → [{tag,id,type,description}]` |
| `list_data_products` | all | `(kg_id) → [...]` — the signed data products (OBDA-READ) on one graph, each with version, backend, validation state and trust state (`GET /v1/data-products?kg_id=`) |
| `query_data_product` | all | `(kg_id, product, measures?, dimensions?, filters?, time_grain?, order_by?, limit?, entity?, attributes?) → dict` — rows plus the governed `provenance` receipt; Uderia compiles fan-out-safe SQL and executes it read-only; a rejection names the closest declared members (`POST /v1/data-products/<product>/query`). See [OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md) |
| `list_knowledge_graphs` | developer | `() → [{name,id,entities,description}]` (`/v1/knowledge-graph/list`) |
| `list_collections` | developer | `() → [{id,name,type,documents}]` (`/v1/rag/collections`) |
| `list_sessions` | developer | `(limit=20) → [{id,name,updated}]` (`/v1/sessions`) |
| `get_my_usage` | developer | `() → dict` own consumption (`/v1/consumption/summary`) |
| `list_actions` | developer | `(kg_id?, profile?) → {actions:[…]}` — the signed write contracts on a graph, each with its trust state; with `profile` each says whether that agent is `granted` it (`/v1/actions`) |
| `perform_action` | developer | `(action, key, values?, invocation_hash?, kg_id?, profile?, connection_id?) → dict` — **two-step**, the prepare-then-confirm every face uses: without `invocation_hash` it previews (the write runs in a transaction that is always rolled back; nothing is written) and returns the hash; with it, it performs only that invocation. The contract's posture holds (review parks, observe records); refusals return structured (`/v1/actions/<name>/preview` · `/perform`). Never at user tier: a consumer reads, a builder acts (PROP-008 §5.6) |
| `system_usage` | admin | `() → dict` instance-wide consumption |
| `list_user_usage` | admin | `() → list` per-user consumption |

The catalog is a curated subset of the read endpoints in
[../RestAPI/MCP_TIER_MATRIX.md](../RestAPI/MCP_TIER_MATRIX.md); add a tool by appending
`(fn, min_tier)` to `TOOL_CATALOG` in `server.py`.

**A turn that stops to ask (PROP-011):** `ask_profile` returns the question when the agent's turn
ends `input_required` (the record id, the items, the resume binding); the caller answers by calling
`ask_profile` again with `answer_to=<request id>` and `answer=<values>`, which re-enters the same
session bound to the question. The other direction — a data source that elicits from the agent
during a turn (MCP elicitation) — is held for a bounded time (`INPUT_ELICIT_HOLD_SECONDS`) and,
unanswered, becomes a `data_source` question for the person (`core/mcp_elicitation.py`).


## 3. Auth & governance

- **Token:** a Uderia **90-day access token** (Anatomy → Advanced → Access Tokens), supplied via the
  `UDERIA_ACCESS_TOKEN` env var. This is the natural fit for the STDIO subprocess model — an MCP
  client spawns the server with the token in its env. The same token is the REST `Bearer`.
- **Target instance:** `UDERIA_BASE_URL` (default `http://localhost:5050`).
- **Governance:** enforced **server-side by the REST layer** — the token's user, their profile and
  component access, consumption limits, and the per-profile grounding/guardrail modes all apply
  exactly as they do for any REST or UI call. The MCP server holds no policy of its own.

## 3a. Consumer-tier segmentation (hard gate)

`--profile user|developer|admin|all` (or `UDERIA_MCP_PROFILE`) selects the tool surface *per
consumer perspective*. It is a **hard security gate**, not just a UX filter:

```
effective surface = tools where min_tier ≤ min(launch profile rank, token's actual tier rank)
```

At startup the CLI resolves the access token's real user tier from `GET /api/v1/auth/me`
(`resolve_token_tier_rank`) and clamps the requested profile to it **before any tool is served**.
So a `user`-tier token launched with `--profile admin` exposes only the `user` tools — it can never
even *see* developer/admin tools. Resolution **fails safe to `user`** (least privilege) on any
error (missing token, unreachable instance, unexpected tier). `all` resolves to `admin` (full
catalog, still clamped to the token). An unknown `--profile` value degrades to `user`.

Run one server entry per consumer (e.g. a `uderia-user` and a `uderia-admin` Claude Desktop
server, each with its own token) to give each consumer a surface scoped to their perspective.

`register_tools(profile, max_rank)` performs the (idempotent) clamped registration; the REST layer
remains the ultimate authorization boundary regardless of which tools are visible.

## 4. Running it

```bash
# STDIO — an MCP client spawns this as a subprocess:
UDERIA_ACCESS_TOKEN=tda_… python -m trusted_data_agent.mcp_server

# Streamable-HTTP — serve at http://127.0.0.1:5055/mcp:
UDERIA_ACCESS_TOKEN=tda_… python -m trusted_data_agent.mcp_server --transport http --port 5055
```

Claude Desktop config (STDIO):
```json
{
  "mcpServers": {
    "uderia": {
      "command": "python",
      "args": ["-m", "trusted_data_agent.mcp_server"],
      "env": { "UDERIA_ACCESS_TOKEN": "tda_…", "UDERIA_BASE_URL": "http://localhost:5050" }
    }
  }
}
```

## 5. Tests & verification

- **CI (`test/test_mcp_server.py`, 52 checks):** the full ask flow, profile-tag resolution,
  poll timeout / failure-status, 401 handling, the `guardrail_blocked` pass-through, tool
  registration, **tier resolution from `/auth/me`, the tier-segmented catalog, the hard-gate clamp
  (`min(profile, token tier)`), and the fail-safe-to-`user`** — all against an `httpx.MockTransport`
  (no live server, deterministic).
- **Live (2026-06-28):** an actual `fastmcp.Client` over streamable-HTTP returned 26 real profiles
  and `ask_profile(profile="IDEAT", …)` returned the agent's answer; tier segmentation showed
  `--profile user/developer/admin` → 2/6/8 tools with an admin token (the catalog of that date; today 4/10/12). The **down-clamp** is
  **live-verified**: a real **developer**-tier access token launched with `--profile admin` was
  clamped to exactly the **6 developer tools of that date (admin tools absent)** — startup logged
  `clamped to token tier 'developer'`. An unauthenticatable token also correctly **fails safe to
  the user tools** (least privilege). (`t_hard_gate_clamp` covers the same clamp deterministically
  in CI.) *(The live developer-tier token was minted via the admin "create as pre-verified" option,
  which skips the email-verification step that otherwise blocks a freshly-created user from logging
  in.)*

## 6. Boundaries / follow-ups (honest limits)

- **Auth is single-token-per-process** (env var) — perfect for the STDIO-per-user model, and the
  basis of the hard tier gate (the process serves one consumer's clamped surface). A multi-user
  HTTP deployment that authenticates **per request** (Bearer header → user → per-request clamp) is
  a follow-up; the REST facade already supports it.
- **Reads, the ask flow and signed actions only.** The catalog is 12 tools (§2); the one write is
  `perform_action`, through a signed contract. Promoting other own-resource writes (e.g. create
  dashboard at `developer`) or exposing component tools (`TDA_*`) and `compile_metric` is a
  natural next increment — the tier for each is already
  recorded in [MCP_TIER_MATRIX.md](../RestAPI/MCP_TIER_MATRIX.md), so promotion is a lookup.
- **Not mounted in-process yet** — the server runs standalone (like `mcp_servers/google_search.py`).
  Mounting the HTTP transport into the running Quart app (so `/mcp` is served by Uderia itself) is
  an option once per-request auth lands.

Files: `src/trusted_data_agent/mcp_server/{__init__,__main__,server,client}.py`, `test/test_mcp_server.py`.
