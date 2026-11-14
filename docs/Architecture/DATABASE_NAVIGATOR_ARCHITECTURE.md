# Database Navigator Architecture

**Component:** `database_navigator` (`TDA_DatabaseNavigator`) · **Package:** `src/trusted_data_agent/dbcatalog/` · **Since:** Sep 2026 · **Last updated:** 2026-10-30

The Database Navigator lets a person and an agent explore the database objects behind an agent — databases, tables, views, columns, keys, relationships, statistics, samples and what the bound ontology knows about each object — through **either** a direct Database Connection **or** the agent's MCP data-source tools. It is built on one catalog contract and one deterministic intelligence layer, so the two ways of seeing a database produce the same reading and the agent's tool and the user's panel can never disagree.

---

## 1. Why two sources, and why the connection is not a fallback

An MCP connection with catalog-shaped tools cannot be assumed: a profile may bind no server, the server may expose only a read-query tool, or the user may be browsing outside any agent. The traditional connection therefore had to be a first-class source. On Teradata it is also the **richer** one: the data dictionary (`DBC.*`) holds declared referential integrity, primary and unique indexes, collected statistics with their age, sizes, comments and view text — none of which the `base_*` tools return.

| Source | How it reads | What it uniquely supplies |
|---|---|---|
| **direct** (`conn:<id>`) — the user's Database Connection | Teradata: `DBC.DatabasesV/TablesV/ColumnsV/IndicesV/All_RI_ChildrenV/StatsV/TableSizeV`; PostgreSQL/MySQL/DuckDB: `information_schema` (DuckDB keys from `duckdb_constraints()`); SQLite: `sqlite_master` + PRAGMA; MongoDB: `listCollections` + `index_information` + fields **sampled** from the documents (`dbcatalog/mongo_provider.py`) | foreign keys, indexes, statistics + age, sizes, comments, view text, dictionary-wide search in ONE read |
| **mcp** (`mcp:<server>`) — the profile's MCP tools | tool ROLES resolved by name and description (`databaseList` lists databases, `columnDescription` describes columns, `readQuery` runs a SELECT); never a hard-coded tool name | works for any connected data source; previews and counts through the governed tool chokepoint |

Both are listed in the panel's source picker.

**Every driver is declared once.** `core/database_connections.DRIVERS` names each driver with its
label, how its catalog is read (`teradata` · `information_schema` · `sqlite` · `mongodb` · none),
whether the connection is a file, whether its reader returns comments, and the **query language**
its statements are written in (`sql`, or `mongodb` — `DOCUMENT_DRIVERS`, `driver_language()`). The store's validation, the Canvas
SQL connector, `resolution.DIRECT_DRIVERS`, the catalog provider, the connection form and the
profile editor's source pickers all derive from it; the JS mirrors are pinned equal by
`test/test_duckdb_driver.py`. Adding a driver = one declaration line + an executor branch.
`COMMENTS` is advertised only where a reader fetches them (PostgreSQL via `col_description` /
`obj_description`, DuckDB via `duckdb_columns()` / `duckdb_tables()`): the personal-data
classifier reads a column's comment, so a capability that promised comments and fetched none left
a column documented as personal data unmasked.

**DuckDB** (a file database, like SQLite): three behaviours the catalog corrects — its
`information_schema.constraint_column_usage` lists a foreign key's OWN columns, so keys are read
from `duckdb_constraints()` (the PostgreSQL join names the child as its own parent);
`schemata` lists a schema once per attached catalog, so only the file's catalog is listed; and
`duckdb.connect()` creates a missing file, so the executor refuses a path that does not exist
(naming it) instead of reading an empty database.

**MongoDB** — the first connection whose statements are not SQL. A statement is a JSON document
naming a collection and an aggregation pipeline (or a find-style `filter` / `projection` / `sort` /
`limit`, converted to one pipeline), and every surface — the Canvas, the navigator's preview and
count, the agent's `query` — runs it through ONE executor, `core/mongo_client.py`, over ONE
parser and check, `core/mongo_query.py`. A collection reads as a table and a view as a view;
MongoDB declares no schema, so `describe` lists the fields **sampled** from up to 200 documents,
each typed by every BSON type it was seen with (`double|int32|string` when a collection is
inconsistent), nested fields as dot paths, with the share of documents carrying it — and says so
in `unavailable` (*a declared schema — fields sampled from 200 documents of about 12,000*, or
*read from all 10 documents*), never presenting a sample as a schema. `_id` is the primary key,
unique indexes mark their field; counts on the listing are the collection's metadata count
(`statistics`), `count` is exact. A connection that names a database is scoped to it: a statement
naming another database is refused. MongoDB's own `system.*` collections are not listed.

**MongoDB is read-only in two halves.** MongoDB has no read-only mode a client can set on a
connection, so the platform's half is an **allowlist** of pipeline stages (`READ_STAGES`), never a
denylist — a stage MongoDB adds later is refused until someone reviews it — and the operators
that write (`$out`, `$merge`) or run server-side JavaScript (`$function`, `$accumulator`,
`$where`) are refused wherever they appear, inside `$lookup` / `$unionWith` / `$facet`
sub-pipelines included; the result is bounded on the server (`$limit`, `maxTimeMS`). The
database's half is the **login's rights**: `connectionStatus` with `showPrivileges` is read
(cached ten minutes) and classified by `classify_privileges` — no authenticated user means the
server runs without access control and anyone can write; a write action on the connection's
database (or any database) means the login can write. An agent query on a login that can write —
or whose rights could not be read — is refused (`writable_login`) unless the connection's owner
ticked *Allow agent queries on a login that can write* (`allow_writable_login`); the connection
test names the login's rights, amber when it can write. The Canvas runs what its owner wrote, as
it does for SQL.

**MCP roles are resolved from the LIVE schema, never guessed.** Two things the first live run taught: a profile's tool set carries look-alikes (`dba_tableSqlList` lists the SQL that touched a table; `qlty_columnSummary` profiles a column's values), so the scorer disqualifies the analysis / DBA / security / plotting families for catalog roles and leaves a role unresolved when no reader fits — an unresolved role is an honest capability gap, a guessed one is an empty tree with no explanation. And the argument names in `APP_STATE` come from the profile's persisted classification snapshot, which can lag a redeployed server (`obj_name` → `table_name`); since `invoke_mcp_tool` realigns every synonym back to the snapshot's name, the provider reads the live schema through the app's own MCP client (one `list_tools` per server, cached for the process) and reconciles that one tool's entry in the adapter's arg map, logging that re-classifying the profile persists the fix. **The declared names end the probe (Oct 2026).** When a role's tool is called with the argument names its schema DECLARES and the server accepts them, the result — empty included — is the answer (`trust_preferred=True` in `mcp_provider.py`, over `kg_constructors/database_context._probe_two_args` / `_call_first_nonempty`). The argument-name grid is walked only when those names are refused, or for callers whose preferred name is a guess (the KG constructors). An empty object had once walked 55 undeclared combinations, which FastMCP 4 refuses slowly (~50 s per object, trust campaign C12). Gate: `test/test_navigator_probe_bound.py`.

**Several databases, named apart.** An agent over more than one source is told which is which: the navigator enriches every engine with the databases the agent may query (source id, name, dialect, database/file and its tables, bounded and cached); a database name the source lacks is resolved to the one database holding the object, or refused naming the real ones; search over a source without dictionary search lists it (bounded, with the scope said as listed/partial) instead of reporting "0 found" over nothing read. Gate: `test/test_navigator_multi_source.py`. Multi-source agents in general: [MULTI_SOURCE_ARCHITECTURE.md](MULTI_SOURCE_ARCHITECTURE.md).

Resolution order (`dbcatalog/resolution.py`, reported back as `reason`): the source named in the agent's `componentConfig.database_navigator.sourceId` → the user's default Database Connection → the first Database Connection → the MCP tools → an honest empty state naming both remedies.

## 2. The catalog contract

`CatalogProvider` (`dbcatalog/base.py`) answers `list_databases`, `list_objects(db)`, `describe_object(db, name)`, `preview`, `row_count`, `search_objects`, `search_columns`, `existing_functions(names)` (which shipped functions / table operators the source has — `None` when it cannot tell; Teradata direct and Teradata-over-MCP answer from `DBC.TablesV` over the function databases, read by the skill library), and declares what it can supply in `capabilities()` (`CatalogCapability`: `list_databases · list_objects · columns · comments · foreign_keys · indexes · statistics · sizes · view_text · row_count · preview · object_kinds · search`). The renderer shows a fact only where the source declares the capability and writes *not available from this source* otherwise — a blank would read as "there are none".

Normalised types (`dbcatalog/types.py`): `CatalogSource`, `DatabaseInfo`, `ObjectInfo`, `ColumnInfo` (with `flags` and `ontology`), `ForeignKey` (`declared` vs inferred, with `confidence` and `reason`), `IndexInfo`, `ObjectDetail`, `PreviewResult`.

**Enrichment reads degrade, core reads raise.** In the Teradata provider a failed enrichment read (sizes, statistics, indexes, referential integrity, view text) is named in `ObjectDetail.unavailable` and the detail is still returned; a failed core read (databases, objects, columns) raises a recovery-shaped `CatalogError`. Enrichment reads run concurrently (`asyncio.gather`), and the runner keeps one live connection per `(host, user, database)` so a describe costs six dictionary reads on one socket, not six connections.

**Read-only by construction.** Every statement the navigator runs is one it composed itself; `sql_runner.single_select_guard` refuses anything but a single SELECT before a preview or count runs, and identifiers are quoted through a shape check that refuses a name the dictionary cannot hold.

## 3. Deterministic intelligence (zero tokens)

`dbcatalog/intelligence.py` is pure and provider-independent. Every verdict carries its reasons; a verdict the evidence does not settle is `unknown`, never guessed.

| Reading | Rule (summary) |
|---|---|
| **Column flags** | `key` (declared primary/unique) · `identifier` (key-shaped name only) · `pii` (name tokens/phrases or a comment saying so) · `text_date` (a date stored as characters wide enough to hold one — shares `components/dateforms.is_text_date_attr` with the enrichment and the preflight guard) · `date`/`timestamp` · `code` (≤3 chars or a code-shaped name) · `measure` (numeric, not a key) · `text` · `identity` · `unique_by_stats` |
| **Object role** | evidence-ranked among `fact · dimension · bridge · lookup · log · staging` (a view/macro/procedure is itself): keys + measures ⇒ fact; referenced + descriptive text ⇒ dimension; keys and nothing else ⇒ bridge; a few columns around a code ⇒ lookup; name hints weigh in; `unknown` below the evidence floor |
| **Keys** | declared indexes win; else the table's own `<table>_id`/`id`, unique-per-statistics key-shaped columns (plural-tolerant stems) |
| **Joins** | declared foreign keys are facts; name-based candidates (shared key-shaped column, compatible types, direction from whichever side holds it as a primary key) are hypotheses with confidence and reason; generic names (`id`, `name`, `code` …) never join; one verdict per child column |
| **Search** | identifier-token ranking, the KG handler's rules: exact (a plural of the name IS the name) > prefix > word > substring > comment mention; objects outrank columns; system databases excluded unless asked |
| **Scope** | `in_scope` / `out_of_scope` against the agent's trusted scope (from its bound ontology, via `get_component_trusted_scope`), `unscoped` when it has none |
| **Actions & questions** | preview/count follow capabilities; copy name; sample SQL in the source's dialect; chart when measures + dimensions exist; open in graph when the ontology knows the object; role-specific suggested questions |

## 4. The ontology overlay

`dbcatalog/overlay.py` reads the agent's bound knowledge graph exactly as the KG component resolves it (owner through the assignment table, Model B released view honoured): the physical `table` node for `db.name`, its `implements` edge to the logical entity (business description, validated `row_count`, `key_unique`), attribute-level facts (`is_pii`, `classification`, `allowed_values`, `sampled_shape`, `date_range`, `blank_ratio`, `deployed_type`), declared keys and the graph's own foreign-key edges. Read-only and fail-open: any failure returns `{"present": False}` and the navigator shows the dictionary alone. Ontology-declared PII joins the column's flags, so a preview masks it even when the dictionary has no idea.

## 5. One service, two faces

`dbcatalog/service.py:NavigatorService` is THE implementation behind both faces. It owns provider resolution, the per-user snapshot cache (`dbcatalog/cache.py`, TTL 30 min, keyed by user then source — one user's connection never serves another's tree), the intelligence, the overlay, the trusted scope and PII masking (`mask_preview`: keep the first character, mask the rest; unmasking is an explicit request).

- **Agent face** — `components/builtin/database_navigator/handler.py` (`VALID_ACTIONS`): `describe · search · preview · suggest_joins · open`, plus the opt-in `query` (§5a). The words the agent reads are composed deterministically from the same facts (role, keys, columns with flags, references, ontology description, scope note, what is unavailable, the sample query). A missing source is a recovery-shaped payload naming both remedies, never a guess.
- **Panel face** — `api/database_navigator_routes.py` (`/v1/database-navigator/{sources,databases,objects,object,search,preview,count,refresh}`), every route gated on the component being available to the caller (the same admin + per-user governance every component honours, failing closed) and answering only from the caller's own connections or active MCP session.

**The agent's own sources, resolved once.** `dbcatalog/agent_source.py` (`agent_profile` · `agent_mcp` · `agent_provider`) turns a tool call's context into the agent's catalog provider — its MCP tools or a direct Database Connection. The navigator handler and the skill library both call it, so the two never disagree about which database the agent is connected to.

## 5a. The agent's own query (`query`, opt-in)

An agent may run **one read-only SELECT per call** against a Database Connection — the
navigator is the place an agent already reads a database's structure, so it is the place it
queries it. Design record: `DAB_P1_NAVIGATOR_QUERY.md`.

- **Off unless the agent opts in** — `componentConfig.database_navigator.query = {enabled, sources}`
  (profile editor → the navigator row → *Allow the agent to run its own read-only queries*).
  Without it the action, its `sql` argument and its instruction lines are removed by the
  per-agent gate `[[gate:navigator_query]]` on every model-facing surface, and the agent is
  byte-identical (frozen by `test_navigator_query_baseline`).
- **Which databases** — `sources` is an allow-list of `conn:<id>`; empty means the navigator's
  resolved default. A source that is not allowed, not present on this instance, or reached
  through MCP is refused and named (an MCP source keeps that server's own SQL tool and its path's
  protections).
- **The same protections as an MCP SQL tool** — `agent/tool_enforcement.py:guarded_sql_call`
  runs the ReAct wrapper's sequence around the navigator's executor: authority (`deny_writes`,
  denied tools under both names, the exclusive data surface), loop guard, turn budget, preflight
  (Teradata-only rewrites stay on Teradata), timeout, errors as results, the result-set registry,
  the empty-result note, the tool-result firewall, steer. Read-only is decided **before**
  anything runs (`single_select_guard`), and the connection is opened read-only at the database
  (SQLite `mode=ro`, DuckDB `read_only=True`, Postgres read-only transaction, MySQL `START TRANSACTION READ ONLY`); a write
  refused either way reads `not_read_only` — *nothing was executed / changed* — never a failure
  a model might retry. `EXPLAIN <one SELECT>` is admitted on this path only
  (`single_select_guard(allow_explain=True)`: the explained statement must pass the same guard;
  `EXPLAIN ANALYZE` is refused because it executes the statement) so the EXPLAIN Plan component
  can read a plan the agent asked for; previews, counts and dictionary reads stay SELECT-only.
- **MongoDB sources** take `collection` + `pipeline` instead of `sql` (both arguments exist only
  behind the query gate), and `database` when the connection spans several databases — a
  connection that names none and a statement that names none are refused before anything runs,
  listing the databases the connection holds. SQL sent to a MongoDB source, or a pipeline to a SQL source, is refused
  as `wrong_query_language`, naming the right arguments. The stage allowlist runs before
  anything is sent (`not_read_only`); then the login gate (`writable_login`, above). The guarded
  executor receives `{collection, pipeline, query}` — the canonical JSON text is the statement the
  registry, the card and the canvas show — and an agent-written pipeline is a **free-formed data
  operation** exactly like raw SQL: the exclusive data surface denies it, the OBDA-READ lane counts it
  as free-formed and the honesty gate counts it as a data retrieval (`obda._is_document_query`).
- **What comes back** — at most 100 rows by default (`limit`, clamped to 500); the FULL result
  (up to the registry bound) reaches the turn's result-set registry, so Compute with Lineage works
  over it and a figure cites its **statement number**. Columns are typed values (the executor
  returns structured rows, not display text).
- **Personal data** — the model reads the values it needs; the **card** masks personal-data
  columns (`intelligence._pii_reason`, the same rule as preview, with the tables the statement
  reads as context — a `name` in a table of people is personal, a product's `name` is not).
- **Signed** — the portable capability hash gains `database_navigator:query` when the opt-in is
  on; the deployment half records the queryable sources (`{id, driver}`), absent otherwise, and
  names added or removed databases as drift.
- **UX** — the answer's evidence row reads *Query · N rows · source · statement n* (collapsed); the
  card shows the statement, a 20-row grid with personal data masked, how many rows the agent read,
  a rewrite when one ran, and **Open in canvas** — the statement in the SQL canvas bound to the
  same connection (the canvas is the editor). The panel's table detail offers **Query in canvas**
  (the dialect-correct sample query) for a direct source.
- **Grounded in its own dialect and source** (P3) — the preflight checks parse the statement in
  the source's dialect and judge it only with that source's facts (`agent/preflight/dialect.py`;
  a Teradata source shares the ontology's). Where no ontology describes the source, its own
  dictionary does: `describe` records the table's complete column list and every `query` records
  the schema list, so a column or schema the source does not have is refused before it runs,
  naming *the database's own dictionary* and the real columns. A refusal returns the recovery text,
  not only the short error.
- **The companion skill teaches it only where it applies** — `database-navigator-expert` carries a
  gated section (aggregate in SQL, respect the row bound and the source's dialect, one source per
  statement, name the source and statement of a figure). Skill content goes through the same gates
  as component instructions (`skills/manager.py:gate_skill_text`, failing closed).

## 6. The panel

`components/builtin/database_navigator/renderer.js` renders two surfaces: the inline chat card for a tool result, and the split panel (`#dbnav-split-panel`, the shared `.sp-header` chrome; source picker with the resolution reason, search with `/` focus and keyboard navigation, a tree with system databases hidden and counted, per-database filter; a detail with role chip and its reasons on hover, capability-honest facts, actions, and tabs Columns · Keys & joins · Preview · Ask · Definition · Ontology). The last source and selection persist per browser. It never re-derives a role or a flag — it reads `intelligence.*`. Theme-aware (CSS variables, `--sp-accent` from the manifest), monochrome icons, `u-tip` tooltips.

**The resource panel reaches the navigator through the Workspace section, not a rail tab of its own.** The rail was already seven tabs wide, and the Workspace panel's pattern — *a section appears exactly when its component is enabled for the active profile* — is the same gate every other action component uses. The navigator has no instances, so its rows are the agent's catalog **sources** (`GET /v1/database-navigator/sources`, one row per source, the resolved default marked active, each opening the split panel ON that source; never deletable there — sources are configured under Connectors / the profile's MCP server), with the browser's **recently viewed** objects as uncounted sub-rows beneath (read from the panel's own `localStorage` record; each opens the navigator AT that object). One `WORKSPACE_REGISTRY` entry in `static/js/handlers/workspacePanelHandler.js` declares it; the section badge and the Work tab total count sources, never the trailer. Gate: `test/test_dbnav_workspace.mjs` (the shipped handler executed under a DOM stub against a fake backend).

**The focus ring follows the manifest accent.** A split panel you interact with takes an accent ring (`polish.css` §7, `panelFocus.js`), using its own `--sp-accent` — the navigator's lime `#84cc16`, applied to the `<aside>` by `componentHandler.applyComponentPanelAccents` from the manifest's `icon_accent`. Panel identity for the ring AND the resize handles is **structural** (`#chat-canvas-split > .sp-panel`, the selector `polish.css` already frames): neither `panelFocus.js` nor `splitPanelResizer.js` keeps an id list, because the navigator was once added to the resizer's list and not to the focus module's twin and never took its ring. Gate: `test/test_split_panel_structural.mjs` (both IIFEs executed with a panel no list ever named).

**The SQL canvas consumes the navigator (Sep 2026).** Once a canvas is bound to a direct connection the navigator lists, its editor completes tables/columns/declared-FK JOINs from this catalog, "Describe in navigator" opens the panel at the identifier under the cursor and REVEALS it in the tree (`_revealSelected`: leaves search mode, opens its database, shows a system database, drops a filter that hides it, then scrolls the row to the centre once it renders), and the navigator hands names back: every tree object, search hit and column row is **draggable** as its qualified name (`_draggable`), and the Keys & joins tab offers **insert join** per relationship row while the canvas is open (`_joinClause(f, dir)` → `JOIN <other> ON child.k = parent.k`, written through the canvas's `insertIntoOpenCanvas`). The canvas console draws its rows through this panel's ONE result grid (`_internal.resultGrid`, the same function the query card uses), masked by the SAME server rule as the preview — `dbcatalog.intelligence.masked_columns` over `statement_tables` (the handler's `_statement_tables` delegates; the canvas execute endpoint calls the same two functions). The full bridge is documented in [CANVAS_ARCHITECTURE.md → navigator_bridge](CANVAS_ARCHITECTURE.md). Gates: `test/test_canvas_navigator_bridge.{mjs,py}`.

## 7. Governance

Standard component chain: manifest `profile_defaults.enabled_for` (`tool_enabled`, `llm_only` — no longer `genie`, see *Default enablement* below; `on_for_new_agents: false`), `componentConfig.database_navigator = {enabled, intensity, sourceId?}` (the profile editor's **Source** selector; absent = auto, carried forward on save), admin and per-user gates — the same `profile_enabled` the Workspace section gates on. Packs default it OFF like every action component (`agent_packs/common/component_config.py`). The companion skill `database-navigator-expert` auto-assigns when the component is enabled. The manifest declares `instances: agent` (the Workspace scopes its section to the agent) and `signals` (`announces: refreshed`, listens on `uderia:component-changed` — see [COMPONENT_ARCHITECTURE.md → Component Signals](COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other)). Agent Authority still applies to the tool; the direct path runs only navigator-composed single SELECTs. Previews come back masked by default on both faces.

## 8. Gates

`test/test_navigator_query.py` (the query action end to end on real SQLite: opt-in, allow-list, writes, authority, bounds, registry, masking, dialect, skill gating, signatures), `test/test_navigator_query_baseline.py` (an agent without the opt-in is byte-identical), `test/test_guarded_sql_call.py`, `test/test_sql_connector_structured.py`, `test/test_dbnav_query_ui.mjs` (the card, Open in canvas, the editor controls, executed under a DOM stub), `test/test_dbcatalog.py` (contract + guard · flags/keys · roles · joins/search · the Teradata provider over a replayed dictionary · MCP + sqlite providers · cache · resolution order · service · overlay · component face · REST face), `test/test_dbnav_renderer.mjs` (the shipped renderer executed under a DOM stub), `test/test_dbnav_workspace.mjs` (the Workspace section), `test/test_navigator_multi_source.py` (several sources named apart), `test/test_navigator_probe_bound.py` (declared names end the probe), `test/test_duckdb_driver.py` (the driver table's JS mirrors), `test/test_split_panel_structural.mjs` (panel identity for ring + handles), and `test/test_canvas_navigator_bridge.{mjs,py}` (the SQL canvas ↔ navigator bridge: one masking rule, one statement reader, completion, drag/insert-join, the console grid), all in `test/trust_battery.sh`.

## Default enablement (Oct 2026)

The navigator is **not a coordinator's tool**: `profile_defaults.enabled_for` lists
`tool_enabled` and `llm_only` only, so a Coordinate agent that does not name it does not get it — a
coordinator handed the catalogue explored it itself instead of routing to an expert (trust
campaign C14: 229K tokens for one turn, no expert consulted). **New agents start without it**
(`on_for_new_agents: false`, a creation-only flag read by the editor's pre-fill —
`rest_routes._new_agent_default`); existing data agents that rely on the default keep it, because
writing an explicit `enabled` would change the signed capability set of every signed agent. An
agent that wants it — coordinator included — enables it explicitly. Gate: `test_dbcatalog.py`.

