# Multi-source agents — architecture

**Status:** phase 1 (the combine step, one Data sources section) and phase 2 (several MCP servers
per agent, incl. routing each call to the server that holds its data — §9.6) built, September 2026.
**Why:** PROP-006 (`docs/proposals/PROP-006-multi-source-agents.md`). **Build rules and decisions:**
`MULTI_SOURCE_AGENTS_BUILD_BRIEF.md`.
**Regression assessment:** `MULTI_SOURCE_REGRESSION_ASSESSMENT_2026-09.md`.

An agent may read several data sources — its MCP server and the Database Navigator's database
connections — and, when an answer needs rows from two of them together, **combine** their results
in one read-only SELECT that code runs. The model decides *what* to combine; code decides whether
it may, runs it, and says what happened.

---

## 1. Two identifiers for a source

| | Form | Scope | Used by |
|---|---|---|---|
| **Source id** | `mcp:<server id>` · `conn:<connection id>` · `combine` | one user's configuration | every recorded statement, an agent's own rules, the editor |
| **Endpoint** | `mcp:<host>:<port><path>` · `mcp:stdio:<command> <args>` · `db:<driver>://<host>:<port>/<database>` · `db:<driver>:file:<path>` | the system itself | deployment rules (hold for every user who reaches that system) |

Endpoints are derived by code from the configuration (`core/source_endpoints.py`) and never contain a
credential. Connections are always read through the one store
(`core/database_connections.DatabaseConnectionStore.list`), so legacy canvas connections count too.
Endpoint resolution is lazy — only when a deployment rule has to be checked — and cached 60 s.

## 2. Every result names its source

The per-turn result-set registry (`agent/result_sets.py`) records, for every statement a data tool
returns, at ONE seam (`tool_enforcement.sql_gate_after → record_catalog → result_sets.record`):

| Field | Set when | Meaning |
|---|---|---|
| `ts` | always | when the rows were read (UTC ISO) |
| `source_id` | the calling lane knows it | `mcp:<id>` from the MCP adapter / the ReAct wrapper (tool metadata `uderia_mcp_server_id`), `conn:<id>` from the Navigator, `combine` for a combine |
| `phase` | inside an Optimize plan phase | the phase that produced it (a contextvar set by `PhaseExecutor.execute_phase`) |
| `inputs`, `origin_sources`, `columns`, `truncated` | a combine result | its input statements and the ORIGINAL sources behind them (a combine of a combine keeps the first sources) |

The persisted compute stamp (`stamp_for_turn`) carries `source_id`, `origin_sources`, `inputs`, `ts`
and `phase` only when present, so a single-source turn persists exactly as before.

## 3. The combine step

`TDA_Compute` `op: "sql"` (`components/builtin/compute/handler.py:_combine`,
`agent/compute_sql.py`). One DuckDB SELECT over the turn's results, exposed as tables `s1 … sN`
(statement numbers) — or `p<N>` in a plan, where statement numbers are not known yet
(`resolve_phase_tables`; parallel phases finish in any order).

Order of checks, all code:

1. **Phase names** resolved to statements (`no_such_phase` otherwise).
2. **Only the turn's tables**, one SELECT, no file/URL/extension functions
   (`referenced_statements`: `single_select_guard` + an allowlist of `s<N>`/CTEs + denied functions
   matched by rendered name).
3. **Isolation** (`isolation_refusal`): the administrator's rules (by endpoint), the agent's own
   rules (by source id — they only add), and the ontology's cross-domain rule (the same
   `preflight.joins.cross_domain_violations` the SQL checks use). A chained combine is judged on its
   `origin_sources`.
4. **Completeness** (`completeness`): an input cut by the registry, or a `TOP/LIMIT n` its rows
   exactly fill, is refused by default (`COMPUTE_SQL_INCOMPLETE=refuse`) or used and disclosed
   (`disclose`).
5. **The one SQL checkpoint** — `tool_enforcement.guarded_sql_call` under the logical name
   `compute_sql`, dialect `duckdb`: authority, loop guard, turn budget, preflight; the result is not
   re-scanned by the firewall (its rows came from results already scanned).
6. **Run** in a sandboxed in-process DuckDB: external access, autoload and autoinstall off,
   configuration locked, 256 MB memory, 20 s timeout (interrupted), 200 000 input rows, 5 000 output
   rows. Inputs are loaded with their source types (`_duck_type`). One exception, named in the result: a column whose every value is a plain number written as text (some MCP servers serialise every value as JSON text) loads as a number; anything else, a code like `"007"` included, stays text (`_numeric_text_type`, decision 12).
7. **Join keys** (`plan_keys`): overlap measured on the actual rows under a fixed ladder
   (exact · trimmed · case-folded · numeric · a consistent prefix/suffix stripped); a clear winner is
   applied to the ON clause and named; otherwise the overlap is handed back.
8. **Findings**: an empty join, rows multiplied by the join, a truncated result.

The result is registered as a new statement; a receipt (`compute.SQL_RECEIPT_FIELDS`: op, sql,
statements, input_times, normalizations, row_count, result_rows, columns, truncated,
result_statement) is sealed into the provenance chain like every compute receipt. Personal-data
columns are masked on the card (the existing classifier).

**Classification.** A combine is not a data retrieval: `obda.is_data_retrieval` /
`is_derived_call` (shared by the OBDA-READ lane and the honesty gate) exclude it, and Agent Authority's
exclusive-data-surface denial (E1) exempts it (`_is_derived_sql`) — it reads nothing the agent had
not already been allowed to read. Everything else (write denial, denied tools) applies.

## 4. Isolation rules — two levels

| Level | Stored | Written against | Edited in |
|---|---|---|---|
| Deployment | `APP_CONFIG.COMBINE_ISOLATION` (persisted in `system_settings`, restored at startup) | endpoints | Administration → Trust → Enforcement → **Combining rules** |
| Agent | profile key `combineIsolation` | source ids | profile editor → **Data sources** → Combining results |

An agent's rules only add restrictions; the administrator's always apply. `combineIsolation` is
validated at the profile door (`source_endpoints.clean_agent_rules`, `400 invalid_combine_isolation`),
is instance-local (never portable, never written by a pack import), and enters the agent's
**deployment** trust half only when non-empty (`trust/deployment.py`, drift names added/removed rules).

## 5. Transparency — the same as single-source, plus which source

A surface names sources only on a turn that read more than one source or combined
(`source_endpoints.multi_source` — the ONE condition). A single-source turn reads, and costs,
exactly as before.

| Surface | What it shows | Where |
|---|---|---|
| Number Lineage step (Live Status, live and reload) | Source rows (name, statements, rows) and Combined results rows (each input, its source, when it was read); title *Number Lineage — Combined Results / Several Sources* | `executor._run_compute_lineage` attaches `sources` (`turn_sources`); `compute_lineage.step_title` (one rule, mirrored by `eventHandlers._assuranceStepTitle`); `ui.js:_computeSourcesRows` |
| Lineage dialog | a combine receipt as COMBINE with rows read/produced, normalizations and the SQL; a combined figure's second hop — every input with its source name and read time | `figureLineage.js` |
| Turn details (reload) | source names resolved at read time (a rename reads correctly on an old turn) | `routes.get_turn_details → enrich_for_reader` |
| Resource panel (phase 2) | every MCP server's tools and prompts — before, only the first server's were listed — the category tabs grouped by server, each group led once by the server's chip (monochrome, its alias or the distinctive words of its name, the full name, role and classification state in the platform tooltip); a server not classified on this instance is a dashed-chip group with a note, never omitted | `adapter.resource_panel_servers` (the runtime's own `_merge_server_snapshot`, so the panel and the turn cannot disagree) → `GET /v1/profiles/<id>/resources` `servers` / `category_servers`; `ui.js:mcpServerChip` / `mcpCategoryServer` / `mcpGroupCategories` (one chip builder, also the Live Status Server row) |
| Tool step (Live Status, live and reload; phase 2) | a **Server** row on every MCP tool step of an agent with more than one MCP server: the server's name and why it went there (*the model called this server's tool* for a `<alias>__` name, *the first server* for a plain one, *routed here by Uderia — this server can read X; the model called Y, which cannot* for a call §9.6 re-routed). Stamped on the persisted call, never re-derived: the Optimize lane's result `mcp_source`, the ReAct lane's event `source`. Named only for MCP tools — never a connector, component or `TDA_` tool; a single-server agent's steps carry no field | `agent_mcp_servers.call_source` (the ONE rule; the turn's server names armed at the execution seam beside the alias map); `adapter.invoke_mcp_tool` → `_stamp_mcp_source` (the route `_invoke_mcp_tool_impl` actually took); `ConversationAgentExecutor._with_source` (each tool's own server metadata); `ui.js:mcpSourceKv` / `appendMcpSource` |

## 6. Configuration UX

**Data sources** section in the profile editor (`static/js/handlers/dataSources.js`), between AI
Stack and Capabilities — every source in one place:

- a summary in words (*Reads 2 sources · may combine results · 1 rule limits combining*);
- **Tools** — the MCP server select (the same element, moved; storage `mcpServerId`), its endpoint
  and classification state; a class that calls no MCP tools says so;
- **Databases** — the default catalog source (`componentConfig.database_navigator.sourceId`), the
  agent's own queries (`…query = {enabled, sources}`), one row per database with Make default /
  Test / remove, an Add-a-database picker (a connection already added or not queryable is shown
  with why);
- **Combining results** — only with two or more sources: the deployment state in words, the
  administrator's rules locked and mapped onto the agent's sources, the agent's own rules;
- a re-attest note on a signed agent.

**Several MCP servers (phase 2).** Under Tools, an agent that calls MCP tools can add further
servers — the control appears only when the user has another server, so a single-server agent sees
nothing new and pays no fetch. Each additional server is a row with its **short name** (the alias
its tools read as `<alias>__<name>`): suggested from the words of the server's name that the first
server's name does not share (generic words such as "MCP" dropped — `Teradata MCP - dbc` beside
`Teradata MCP - sales` suggests `dbc`), validated live against the alias rule (lower-case letters
and digits, single underscores, a letter first, at most 12 characters, unique), removable, with a
*Pick tools* link. **Resources** gains one group per additional server whose tools and prompts are
picked by their own names (All / None; all ticked is stored as `["*"]`); the first server's lists
are unchanged and labelled only when a second server exists. Selecting one of the additional
servers as the first server removes it from the list with a note. The save sends
`additionalMcpServers` only when it is set now or was before (`[]` removes it, as does an agent that
stops calling MCP tools); a short-name problem stops the save and opens the section.

State starts equal to the stored profile, so a save at any moment carries every value forward; the
state of another profile's editor is never read (`stateFor`). Data comes from `GET /v1/data-sources`
(`sources_view`). The administrator's editor is `static/js/handlers/combineRulesAdmin.js`, saved
with the Trust panel's one Save.

Theme: variables only, monochrome SVG, no class containing `card` (the global card rule would
restyle it); labels scoped by id so the editor's hard-coded dark label colour cannot win.

## 7. Switches

| Switch | Default | Effect |
|---|---|---|
| `TDA_COMPUTE_SQL` (`APP_CONFIG.COMPUTE_SQL`) | off | the combine op; effective only under `TDA_COMPUTE_LINEAGE`. Its tool text, the `sql` argument and the guidance sit behind `[[gate:compute_sql]]`, so off is byte-identical |
| `COMPUTE_SQL_INCOMPLETE` | `refuse` | `disclose` uses an incomplete input and discloses it |
| `COMBINE_ISOLATION` | `[]` | deployment rules (admin UI) |
| `TDA_MCP_SOURCE_ROUTING` (`APP_CONFIG.MCP_SOURCE_ROUTING`) | on | a multi-server agent's call goes to the server that holds its data (§9.6); only agents with several MCP servers are affected |

## 8. The single-source rule and its named exceptions

A single-source agent gets no added tokens, calls, latency, stored state, signatures, UI or
configuration; every enhancement works for both through one code path. Deliberate exceptions:

- `ts` on every recorded statement and `source_id` where the lane knows it (read by nothing unless
  a turn is multi-source); `phase` only inside Optimize plans.
- The profile editor's Data sources section exists for every agent (one source shows one row and
  no Combining card).
- The `compute-expert` skill is **not** edited: skill content is hashed raw, so an edit would move
  the signature of every agent carrying it, single-source ones included. The combine's guidance
  (filter and aggregate at each source, apply a named fix once) is in the gated tool text, and the
  code's refusals name the fix.

## 9. Phase 2 — several MCP servers

An agent may read several MCP servers — two Teradata systems, a warehouse and a CRM. Built on the
same rule as phase 1: **a single-server agent is byte-identical.** `test_multi_mcp_baseline` (114)
was committed green against the unchanged code and holds a single-server agent's tokens, calls,
snapshot, signature and routing to a golden file, with two named exceptions (M9: the delete guard
also covers Ideate+tools agents; M10: gaining a server flags reclassification).

### 9.1 One name per tool (decision 2)

Two servers often expose a tool of the same name (`base_readQuery`). Every store keyed by tool name
— the catalog, the profile's selection, Agent Authority's denied tools, the loop guard, the governed
tool surface, classification categories, a plan, a champion case — would keep one and silently lose
the other. So `core/tool_identity.py` gives every tool and prompt a name unique across the agent:

| Server | Name the agent sees | Name the server is called with |
|---|---|---|
| primary (`mcpServerId`) | `base_readQuery` (unchanged) | `base_readQuery` |
| additional, alias `crm` | `crm__base_readQuery` | `base_readQuery` |

`qualify` builds the name (an invalid alias, or a name no provider accepts, raises — never
truncated); `parse` / `base_name` read it back, and only a KNOWN alias of the running turn makes a
prefix a qualifier (a primary tool may itself contain `__`). Every rule that recognises a tool by
name ("is this the SQL tool", discovery tools, correction strategies, preflight, the governed
surface backbone) reads the base name; `test/tool_identity_gate.py` fails the build on a literal
tool-name comparison that bypasses `base_name` (18 routed rules).

### 9.2 Storage and the one resolver (decision 10)

`additionalMcpServers: [{id, alias, tools, prompts}]` on the profile. The server ids are this
instance's; everything else is portable (stage 2 of the implementation plan): an agent pack carries
every server by NAME in the manifest entry's `mcpServers` list, the importer resolves each name —
or an explicit binding `{"primary": id, "<alias>": id}` — to its own servers
(`agent_mcp_servers.resolve_manifest_servers`), and names any server it cannot resolve rather than
guessing. The alias and each server's tool/prompt selection are part of the agent's signed
**capability** (`profile_config_contract`, `travels_as_binding`); the ids are not, so a publisher's
signature reinstates on a copy bound to the consumer's servers. The plain tool names of the first
server are a compatibility rule (decision 2.0), not a hierarchy. Validated at the door — `400
invalid_additional_mcp_servers` names the problem (a bad alias, a duplicate, the primary listed
again, an unknown server); `[]` removes the key. ONE resolver, `core/agent_mcp_servers.servers_of`,
answers "which servers does this agent read, under which alias" for every consumer. The delete guard
and the relationship detector see additional servers (M9); a changed (server, alias) set flags the
agent for reclassification (M10).

### 9.3 Classification, catalog, routing

- **Per server.** Each additional server is classified through the SAME path as the primary
  (`adapter._list_server_capabilities` → `_classify_capabilities` → `_structure_capabilities`), its
  snapshot stored in the deployment-local `additionalMcpClassification` and reused while its mode
  matches. Categories read `<alias>: <category>`; each server's own selection applies.
- **One catalog.** The turn's catalog is the primary's plus each additional server's qualified tools;
  the ReAct lane loads LangChain tools per server (`load_mcp_tools_for_langchain(alias, selection)`),
  each carrying its server id in its metadata. Clients are pooled per server
  (`configuration_service.pooled_mcp_client`).
- **Routing.** The turn's alias → server map is armed once at the execution seam
  (`tool_identity.set_turn_servers`, a contextvar — per turn, never process-global).
  `adapter.resolve_mcp_target` is the ONE resolver name → (client, server id, server-side name),
  used by `invoke_mcp_tool` and all four MCP prompt fetches (planner, REST execute / execute-raw, UI
  preview). Outside a turn a REST caller passes the profile's own `server_map_for`.
- **The turn's primary server is pinned too (Oct 2026, OC-F1).** Every REST query runs
  `switch_profile_context`, which rewrites the PER-USER MCP server id — so two concurrent turns of
  one user on different MCP entries raced on it, and a call went to the other turn's server. The
  execution seam now arms the turn's PRIMARY server in a context variable
  (`tool_identity.set_turn_primary_server`, released in its `finally`; inherited by every task the
  turn spawns — a delegated prompt, an orchestrator), every `@TAG` overlay names its server, and
  `mcp_adapter.turn_server_id` is the ONE resolution: overlay slot (read through every overlay
  depth, `_overlay_map`) → the armed primary → the per-user value only outside a turn. Single-server
  agents use the same rule. Gate: `test_turn_server_pin.py`.

### 9.4 Facts, planners, trust, authority

| Concern | Rule | Where |
|---|---|---|
| Preflight facts (O5/O6) | A call to an ADDITIONAL server keeps its facts under `mcp:<server>` even on Teradata — one warehouse's database list never answers for another's. The primary and every single-server agent share the session's facts as before. Both lanes set the scope (`additional_mcp_scope`). | `preflight/dialect.ns` — [PREFLIGHT_SQL_ARCHITECTURE.md](PREFLIGHT_SQL_ARCHITECTURE.md) |
| Planners (M6, O8) | A planner repository keyed to one of the turn's additional servers loads for the agent in its turn (its learned plans name that server's tools). A plan naming a server the agent does not have in this turn (`crm__base_readQuery` learned with another server set) is re-keyed by the plan validator to the ONE catalog tool of that base name — the first server's plain name first — and named as a *Plan Optimization* step; ambiguous or absent, the name stands. Routing (§9.6) then sends the call to the server that holds its data. | `rag_retriever._planner_keyed_to` — [FUSION_OPTIMIZER_ARCHITECTURE.md](FUSION_OPTIMIZER_ARCHITECTURE.md) |
| Trust (M5) | The deployment half gains `additional_mcp` — one entry per server `{server_id, alias, catalog}`, the catalog in the server's own names, UNRESOLVED if never classified here; ABSENT for a single-server agent (no attestation moves). Attesting over an unclassified server is refused naming its alias; drift names the server (added / removed / renamed / tools appeared or removed). | `trust/deployment.py` — [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md) |
| Authority | `denied_tools` matches the name the agent sees: `base_readQuery` denies the primary's tool, `crm__base_readQuery` the additional server's. `deny_writes` classifies the SQL statement, whichever server it goes to. | `agent_authority.is_tool_allowed` — [AGENT_AUTHORITY_ARCHITECTURE.md](AGENT_AUTHORITY_ARCHITECTURE.md) |

### 9.5 Transparency and configuration

Every MCP tool step of a multi-server agent names its server — live and after a reload (Optimize:
the result's `mcp_source`; ReAct: the event's `source`; one rule `agent_mcp_servers.call_source`).
The resource panel lists every server's tools and prompts grouped under ONE server chip
(`ui.js` → `mcpServerChip`), an unclassified server shown as such. Servers are added in Data sources →
Tools and each server's tools and prompts picked in Resources (§5, §6).

### 9.6 Routing each call to the server that holds its data (B10)

Two servers exposing `base_readQuery` with the same description look interchangeable to the model.
In the first live run (fitness server + NIBRS server) it called `nibrs__base_readQuery` for a
`fitness_db.Customers` count; the NIBRS login was refused (Error 3523) and the model answered that
it lacked permission instead of trying the other server. Code now decides where it safely can
(`agent/source_routing.py`, both lanes):

- **What a server holds** is what its own login can read. Measured on both servers: each login's
  database-list tool returns only the databases it may read (the fitness login lists `fitness_db`,
  the NIBRS login lists the five NIBRS databases, neither lists the other's). The list tool is found
  by role (`dbcatalog.mcp_provider.resolve_roles_by_shape`), never by a hard-coded name, probed once
  per (user, server) — concurrently, bounded to 8 s, cached 30 minutes, a failure re-probed after 2
  minutes — and refined by what each call proved (a success holds, a 3523/3802 does not — never a 3807, which names a missing table in a database the login does read) and
  by the turn's own database-list results. Zero LLM tokens.
- **Before the call:** a statement (or database argument) naming a database the called server does
  not hold goes to the ONE other server of the agent that holds it. Several holders → the call is
  refused before anything runs, naming them; nobody known → the call stands.
- **After a failed call:** an error that says "this server cannot read that" (Teradata
  3523/3524/3802, PostgreSQL permission-denied / schema-does-not-exist; never 3807) on a READ is sent once to the
  server that holds the data — or, when nothing is known, to the only other server offering the
  same tool. If that fails too, the first error stands.
- **Never:** a write (a write on another warehouse is a different act), a server known not to hold
  the data, a tool outside that server's selection, a name Agent Authority denies (checked on the
  name the agent would call, fail-closed), more than one re-route per call, a `TDA_` or connector
  tool, a single-server agent (nothing is armed).
- **Order:** the route is decided BEFORE the SQL gate, so preflight, the loop guard and authority
  judge the call as the tool that will actually run, in that server's scope
  (`preflight.dialect.server_scope` clears an outer additional-server scope for the primary).
- **Transparency:** the Server row reads *routed here by Uderia — this server can read fitness_db;
  the model called nibrs__base_readQuery (server …), which cannot* (Optimize: the result's
  `mcp_source.reason = routed` with `called`, `from_server`, `databases`; ReAct: the tool-end
  event's `source`, from the turn's re-route ledger). The agent reads a `[Routing]` note first in
  the result, naming the tool to call next time.

- **The model is told which server holds what** (`servers_note`): which tools reach which server
  (the unprefixed ones the first, `<alias>__…` the others) and the databases each login reads,
  system catalogs left out, capped at 12 per server. The lists are warmed once at the execution seam
  (only for a routing turn); the Optimize lane reads the note at the top of its tool definitions
  (`tool_definitions` module, full and names-only), the ReAct lane on the user side of the turn.
  Before, "which databases can you read?" was answered from the first server alone; now both
  agents list both servers. An entry the list marks as a USER (a login's own space, e.g.
  `nibrs_source_agent`) is left out of the note — the model once queried
  `nibrs_source_agent.victim` for data — unless it is all the login reads; routing still counts it.
  Teradata's own utility databases (`sqlj`, `sysjdbc`, `td_sysxml`, …) are left out of the note too;
  they crowded the real databases past the cap.
- **One statement never spans two servers.** A SELECT joining databases held by different servers
  runs nowhere, so it is refused before anything runs (`split`), naming where each database lives
  and telling the model to query each server with its own tool and combine the results. The
  planner's SQL-consolidation pass asks `spans_servers` before its LLM call and keeps such phases
  separate (observed live: a fitness count and a NIBRS count merged into one SELECT that failed on
  both); statements on one server are still consolidated.
- **An override agent gets its additional servers.** An agent driven by `@TAG` or REST `profile_id`
  runs from a per-executor catalog overlay built from its FIRST server; the Optimize override path
  now merges its additional servers into that catalog (`optimize_engine.merge_override_additional_servers`,
  the activation path's own per-server load) before the overlay is installed. Before, such an
  agent planned with one server's tools. Live: an override on a two-server Optimize agent merged
  the NIBRS server's 48 tools and answered a NIBRS count on that server.

- **One decision, both lanes; one per-lane policy** (implementation plan stage 1). *Where* a call
  runs is decided once: `source_routing.plan_before` (before the call) and `plan_after` (after a
  failed call) return a `CallDecision` (stand / re-route / refuse, the refusal text, and the ONE
  re-route record `reroute_info`: what was called, the server it would have reached, where it went,
  the note, and the plan phase it ran in). The Optimize lane (`adapter._invoke_routed`) and the
  ReAct lane (`tool_enforcement._react_route_before/_after`) only run the call and record the
  outcome; neither keeps a private copy of the decision (asserted on the syntax tree). The routed
  source stamp is built once too (`routed_source`). What legitimately differs between the lanes is
  declared in `SourceLane` (the `SqlLane` precedent): `plan_bound` — the Optimize lane honours the
  server its plan chose for a call (`planned_server`, unless that server is known not to hold the
  data); the ReAct lane has no plan. Scheduling (parallel phases, per-server concurrency, merging
  within one server) is never decided here — it belongs to the Optimize planner and phase executor.
  A refusal AFTER a failed call (several servers could hold the data) now names those servers in
  both lanes (before, only the Optimize lane did). Gate: `test_multi_source_lanes` (the same call
  over the same fake servers, both lanes, the same decision).
- **The first server is read through one accessor too** (`agent_mcp_servers.primary_of`), beside
  `servers_of` / `additional_of`; a new direct read of the stored key on a multi-server path is
  refused by `test_primary_server_reads` (a shrink-only ratchet over the syntax tree).

Live-verified on both lanes (a forced wrong call: `nibrs__base_readQuery` for `fitness_db` ran only
on the fitness server and answered 100, the step named the route). Switch:
`APP_CONFIG.MCP_SOURCE_ROUTING` (env `TDA_MCP_SOURCE_ROUTING`, default on; it only affects agents
with several MCP servers). Gate: `test_mcp_source_routing`.

### 9.6a A dashboard tile's MCP replay (M11)

A tile's *refresh-mcp* replays the stamped tool outside any turn. It used to run through the
process-global state — on whichever server the user activated last — and a qualified name
(`nibrs__base_readQuery`) could not be routed at all. `source_routing.profile_call_scope` arms the
tile's own profile for the call: its first server as the call's server (a per-call STATE overlay,
the @TAG overlay's pattern), its additional servers' alias map, names and routing; outside a turn
no catalog is loaded, so candidates are judged by the profile's own server selection. A tile
stamped from a call Uderia re-routed is re-routed again on refresh. Live: a tile stamped
`nibrs__base_readQuery` over `fitness_db` refreshed on the fitness server (100 rows).

### 9.6b One of the agent's servers is down (B9)

A server that cannot be reached never stops the turn — the agent answers from the others — and it
is **named, never silent**, so a missing source is never read as missing data:

- **Activation** reports each additional server (`test_results.additional_mcp_servers`): reached
  (its tool count) or not (a warning naming it). Before, activation said "all tests passed" with a
  server down, because it only checked that the server was configured.
- **One record per turn, both lanes, at turn start** (stage 1; closes R3). The turn keeps ONE
  record of the agent's servers that could not be reached (`source_routing.mark_unavailable` /
  `unavailable_servers`), fed by the turn-start probe (a probe that raised or timed out — never a
  server that merely has no database-list tool), by the ReAct lane's tool loading, and by a
  mid-call transport failure. Both lanes announce it the same way, once per turn
  (`take_unavailable_events`): a Live Status step *MCP Server Unavailable* — the Optimize lane
  before planning (persisted as a system log step, titled on reload), the ReAct lane before the
  agent runs (persisted with the turn's system events). The ReAct lane does not try to load a
  server the probe already found down (no second connection timeout).
- **One note, every prompt path.** `turn_sources_note()` is everything the model is told about the
  sources this turn: which tools reach which reachable server and what each holds, and which could
  not be reached — *"…could not be reached in this turn, so its data cannot be read. If the
  question needs that data, say that this source is currently unavailable — never answer as if the
  data did not exist."* The same text reaches the ReAct agent (user side of the turn), the
  Optimize planner on both of its prompt paths (the `tool_definitions` context-window module and
  the fallback path — before stage 1 the fallback got no note at all), and the plan gate. With one
  server down the reachable one is still listed (before, the list was dropped when only one server
  remained, and the planner, told nothing, invented a database — found live).
- **Both lanes, mid-turn:** a call whose server did not answer (a transport failure —
  `transient_transport_error`, after its one resend) reads *"Server 'X' could not be reached — its
  data cannot be read in this turn. Do not retry it; answer from the agent's other servers and say
  that this source was unavailable."*; a READ goes once to a server KNOWN to hold the data (never
  guessed — the down server never said what it holds). The failure is recorded in the turn's one
  record and the server's cached database list is dropped (`note_unreachable`), so the NEXT turn
  names the server at its start — a list cached before the server went away used to hide it for up
  to 30 minutes (found live).
- **A refused connection is a transport failure** (fixed in stage 1). httpx raises its
  `ConnectError` FROM httpcore's, and the classifier's leaf walk follows the cause — so the usual
  "server is down" ended on `httpcore.ConnectError`, which was not in its list: for every HTTP MCP
  server the mid-turn handling above, the re-route after an unreachable call and the one resend
  never applied. httpcore's network and connect errors now count; read/write/pool timeouts still
  do not. Gate: `test_mcp_transport_retry` (the real chained shape).

Live-verified (a fixture server stopped after its list was cached): both lanes name it at turn
start; the ReAct agent answers the reachable half and says the other source is unavailable.

Not built (B9's remainder): a per-server circuit breaker (R1) and a deterministic
`source_unavailable` disclosure from the honesty gate (R2) — until then, whether the answer says a
source was unavailable is the model's (the Optimize lane's answers did not always say it).

### 9.6c A statement over two servers is split by code (implementation plan stages 3a–3b)

The bench's cross-server questions (a customer table on one server, its sales on another) failed
on the Optimize lane in one shape: the planner named the right tables on the right servers and put
them in ONE statement; routing refused it (no server holds both); self-correction — one action —
swapped in a query over one server; the phase counted as done; the report stated a confident wrong
number for the whole. Two things, both code:

* **The planner is told the tables, not only the databases** (3a). `source_routing.ensure_catalogs`
  reads each server's tables (and columns, when a server has ≤ 16 tables) with its database list,
  through the server's own list-tables / describe tools resolved by role — cached with the list
  (30 min, expiring with it; a failure backs off 2 min and leaves the list standing; bounded: ≤ 6
  user databases and ≤ 40 tables per server, else the database list alone). The one *Data sources*
  note lists each table under its server's line with its columns and states the rule: *one
  statement reads the tables of ONE server*. The columns seed the session's column store, so the
  3.0 describe pass and the correction strategies know these tables. `holdings_for_turn()` is the
  structured form. Measured: Optimize cost per question 3.5× → 1.0× the baseline, LLM calls
  7.0 → 3.3, controls kept.
* **A spanning statement is split, never substituted** (3b, `agent/cross_source.py`). One fetch per
  server — the columns the statement uses of that server's table, its own WHERE conjuncts pushed
  down when every join is INNER, that server's own tool — and the ORIGINAL statement as the combine
  step (`TDA_Compute` op sql) over `p<N>`, so its join, aggregation and ordering run unchanged over
  the fetched rows with every combine protection (isolation, completeness, the receipt). A
  statement that reads an earlier phase's rows as a table (`… IN (SELECT CustomerID FROM s1)` —
  the combine written as SQL, which the note draws out of a planner) is the same case: the real
  table is fetched, the phase result read as `p<N>`. Two seams, one splitter: the planner pass
  `cross_source_shape` (after `compute_insertion`, before validation; references follow the split
  through `renumber_plan_refs`; the fetches run in one parallel group) and the phase executor at a
  refusal that carries `code: spans_servers` + `split` (database → server): fetch per server,
  combine by `s<N>`. What the splitter declines — an unplaceable table, two tables of one server,
  a nested SELECT other than `col IN (SELECT one column FROM one table …)`, a CTE, a UNION,
  `SELECT *`, an unattributable column, a forward phase reference — is withdrawn to the tactical
  step with the split named (planner) or ends the phase with the refusal (executor): **a one-server
  substitute is never the phase's answer**, and neither path spends an LLM round. No-op for a
  single-server agent and with `COMPUTE_SQL` off. Gate `test/test_cross_source_plan.py`.
  Measured (the two-server fixture, gemini-3.1-flash-lite, 13 questions × 3 runs): Optimize
  cross-server 5/33 → 26–29/33, controls 6/6, $0.0015 per question, LLM calls 2.2 (baseline 3.6);
  Ideate+tools 39/39 at 2.4× lower cost. The remaining Optimize misses are the model's column
  judgement on a graph-less agent, each answered honestly.

Not built (plan 3c–3k): learned cross-source cases keyed by their server set, the cap-bites
recovery hint, choosing the server at plan time beyond the `_server` stamp, a per-server
concurrency cap, plan-level handling of a down server, merging within one server. The splitter
takes one table per server per statement; a same-server join beside a cross-server one is declined.

### 9.7 Open

B4 (authority allow-list per source), B8 (MCP tool annotations), B11–B12 (the knowledge graph across sources, the compose act), a source chip on the lineage
surfaces, and the DataAgentBench two-server run. See the build brief and the assessment §3.
Hand-over requirements for each open item: `MULTI_SOURCE_PHASE2_OPEN_REQUIREMENTS.md`.

## 10. Gates

`test_compute_sql_baseline.py` (off ⇒ byte-identical), `test_compute_sql.py` (the combine, isolation,
attribution, persistence, transparency, configuration), `test_combine_ux.mjs` (dialog, card, title,
theme), `test_data_sources_ui.mjs` (the Data sources section and the administrator's editor over the
real template markup), `test_mcp_source_attribution.py` + `test_mcp_source_ui.mjs` (the Server row on tool steps, the server chip), `test_resource_panel_servers.py` (every server in the resource panel), `test_mcp_source_routing.py` (routing to the holding server, both lanes), `test_multi_source_lanes.py` (one decision and one record of the turn's sources for both lanes; the plan-bound policy; the phase on each re-route record; the unreachable-server record), `test_primary_server_reads.py` (the first server read through one accessor, a shrink-only ratchet), `test_turn_server_pin.py` (a turn's calls go to its own primary server under concurrency), `test_mcp_transport_retry.py` (a refused HTTP connection is a transport failure), plus the restated `test_dbnav_query_ui.mjs` §5 and `test_duckdb_driver.py` §2.
All in `test/trust_battery.sh`.
