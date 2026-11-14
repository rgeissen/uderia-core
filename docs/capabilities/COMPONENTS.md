# 🎨 Components

Uderia renders LLM output through a **plugin-based component library** (19 builtin components — sixteen in the public core, three in the enterprise package: the OBDA planes OBDA-READ, OBDA-MODEL and OBDA-ACT; see [the split](KEY_FEATURES.md#-open-and-extensible-built-to-be-built-on)) where each component is a self-contained module with its own prompt instructions, backend handler, and frontend renderer. Components are toggled per-profile, injected into LLM prompts at runtime, and rendered via tool calls or data-driven detection.

**Shared component characteristics:**
- **Self-contained modules** — each component is a self-contained directory bundling its manifest, LLM tool contract, backend handler, and frontend renderer
- **Three component types** — Action (the LLM calls a `TDA_*` tool), Structural (rendered from data type) and System (runtime infrastructure, e.g. the Context Window Manager)
- **Three render targets** — `inline` (chat bubble), `sub_window` (persistent canvas panel), `status_panel` (Live Status area)
- **Deterministic fast-path** — components with predictable output bypass the tactical LLM entirely, saving ~3,000 tokens per invocation
- **Profile-level intensity control** — each profile toggles components and sets instruction intensity (`none`/`medium`/`heavy`)
- **One vocabulary** — *available* (the admin's), *enabled* (the agent's), *active* (in effect every turn without being called — Context Window, Memory, Needs Input declare it); an agent that stores no switch follows the component's class default and reads **Auto**, the same mark as a companion skill following its component; a toggle takes ownership, *Reset to default* hands it back
- **Admin governance** — deployment-wide component control (all/selective mode, user imports, marketplace access), and per-user grant/block overrides
- **One way to talk to each other** — a page bus, one change event per component announced at its store, and a result ledger by reference; open editors follow writes made in another tab without overwriting yours ([Component Signals →](../Architecture/COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other))
- **Intelligent components, companion skills** — a component absorbs everything code can decide; each action component ships a companion skill for the quality layer, auto-assigned when the component is enabled ([Skills →](../Architecture/SKILL_ARCHITECTURE.md))
- **Third-party extensibility** — add custom components without modifying core files; manifest-driven discovery with hot-reload

[Architecture →](../Architecture/COMPONENT_ARCHITECTURE.md)

---

## Canvas — Interactive Code & Document Workspace

The Canvas component (`TDA_Canvas`) transforms LLM-generated code and documents into an editable, executable workspace. Built on CodeMirror 6 with a modular capability-plugin architecture (12 capabilities, 4 execution connectors):

- **Editing:** Syntax-highlighted code editor with SQL, Python, JavaScript, HTML, and Markdown support
- **Live Preview:** Responsive HTML preview with viewport simulation, Markdown, Mermaid and SVG rendering
- **Execution:** run SQL (and MongoDB statements) on your Database Connections — Teradata, PostgreSQL, MySQL, SQLite, DuckDB, MongoDB, JDBC — plus Python (Pyodide), JavaScript (Web Worker) and HTML/CSS (sandbox); results render in place
- **Version Tracking:** Turn-based version history with side-by-side diff view across conversation turns
- **Inline AI:** Select code and request targeted modifications without regenerating the entire canvas
- **Split-Screen:** Toggle between editor-only, preview-only, and split-panel modes; fullscreen expansion
- **Template Gallery:** 12 starter templates for common tasks (SQL queries, HTML pages, data analysis)
- **RAG-Aware:** Source attribution badges when canvas content is generated from knowledge base retrieval

[Architecture →](../Architecture/CANVAS_ARCHITECTURE.md)

---

## Charts — Automated Data Visualization

The Chart component (`TDA_Charting`) turns a data result into an interactive G2Plot chart: the model picks the chart type and the columns; the component repairs the rest (wrong column names, swapped axes, wide tables, top-N cuts, text dates on a time axis) so the renderer never receives a mapping it cannot draw. Supports 17 chart types (bar, column, line, area, pie, scatter, histogram, heatmap, box plot, word cloud, waterfall, radar, rose, funnel, gauge, dual-axis, treemap). Key to reliability is the **5-stage mapping validation pipeline** — a deterministic repair chain that guarantees valid chart specs even when the LLM hallucinates column names or swaps axes:

1. **Sanitize** — normalize LLM field names to canonical schema
2. **Validate** — check column existence against actual query results
3. **Deterministic Repair** — cardinality-aware column reassignment using data shape heuristics
4. **LLM Repair** — (if needed) targeted LLM call to resolve ambiguous mappings
5. **Positional Fallback** — last-resort mapping by column position

Charts render inline in chat bubbles by default, with optional sub-window mode for interactive exploration. Pin a chart to a dashboard, or keep it to Files as a PNG plus a CSV of its own data.

[Architecture →](../Architecture/CHART_ARCHITECTURE.md)

---

## Knowledge Graph — Context-Enriching Entity-Relationship System

The Knowledge Graph component (`TDA_KnowledgeGraph`) maintains a living, queryable model of database topology, business concepts, metrics, and domain taxonomies as a typed entity-relationship graph. Owned per user and assigned to one or more agents (one active graph per agent); each graph remembers the graph-store backend it lives on.

- **Planner Context Injection:** Before every strategic planning call, the system extracts a relevant subgraph based on the user's query and injects it as context — guiding tool selection, SQL construction, and argument generation. Functions as a semantic guardrail that reduces hallucination
- **Grounding source of truth:** The KG's declared *deployed scope* is exactly what the runtime [Grounding Gate](SECURITY_AND_TRUST.md#-grounding-gate-runtime-anti-hallucination) enforces against — making the graph not just advisory context but the boundary every answer is checked against
- **Ontology — two-layer modeling:** Each KG carries a **logical** model (`entity`/`attribute`) and its **physical** realization (`table`/`column`), linked by `implements`.md` · `docs/Architecture/INDUSTRY_MODEL_KG_TESTPLAN.md` · `docs/Architecture/INDUSTRY_MODEL_PDM_DEPLOYMENT_PLAN.md`)
- **Governed metric semantic API — define once, consume everywhere:** A metric is defined once in the ontology (dimensions/measures/source) and consumed by **both the agent and BI tools** through one pure semantic core, so their SQL is identical by construction. A governed REST surface (`/v1/profiles/<id>/metrics…`) returns a metric's definition, compiled SQL, deployed-view pointer, and access scope — or executes it to rows — while surfacing the source ontology's **lifecycle + trust badge** and cross-linking its **W3C RDF Data Cube** form. Reachable in-app from any metric node (right-click → *Semantic API for BI*)
- **Visualization:** A reduce-then-render inspector (D3 force layout) that samples large graphs into a legible, connected subgraph with type/division/domain facets, schema overview and focus lenses, and live execution-trace animation
- **Progressive Enrichment:** Graphs grow through LLM-inferred entities during conversation, deterministic agent-discovery against a live database + MCP, manual JSON import, or industry-model walkers — with an update/version lifecycle that re-syncs from the source and diffs the change
- **Intensity Control:** At `medium` intensity, graph context is advisory; at `heavy` intensity, the LLM strictly validates against known relationships

[Knowledge Graph →](../Architecture/KNOWLEDGE_GRAPH_ARCHITECTURE.md) · [Ontology →](../Architecture/ONTOLOGY_ARCHITECTURE.md) · [KG Inspector →](../Architecture/KG_INSPECTOR_ARCHITECTURE.md) · [KG Store Abstraction →](../Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md) · [Open Standards (SKOS/OWL/RDF Data Cube interchange) →](../Architecture/OPEN_STANDARDS_ARCHITECTURE.md) · [KG marketplace subscription →](../Architecture/KG_SUBSCRIPTION_PLAN.md)

---

## Task Scheduler — Autonomous Agent Scheduling

The Task Scheduler component (`TDA_Scheduler`) transforms Uderia from a reactive assistant into an always-on autonomous agent. Users create, manage, and monitor scheduled tasks through natural conversation; results render as an interactive scheduling canvas in the chat.

- **Same Pipeline, No Special Runner:** Scheduled tasks execute through the identical execution path as interactive queries — same LLM, same profile, same MCP tools. Every improvement to Uderia automatically benefits scheduled tasks
- **Two Schedule Formats:** Standard cron expressions (`0 9 * * 1-5` = weekdays at 9 AM) and interval shorthand in seconds, minutes, hours or days (`interval:300s`, `interval:30m`, `interval:24h`, `interval:1d`) backed by APScheduler
- **Session Context:** `session_context='new'` creates a fresh ephemeral session per run (appears in sidebar); `session_context='current'` pins the task to the user's active session, inheriting full conversation history and profile context
- **Concurrency Governance:** Three overlap policies — `skip` (default, prevents burst catch-up), `queue` (waits up to 300s for prior run), `allow` (concurrent execution)
- **Token Budget:** Optional `max_tokens_per_run` limit; enforced post-run — runs that exceed the budget are marked as errors and skip result delivery
- **Result Delivery:** Results route to `email` (via the deployment's existing SMTP/SendGrid/SES), `webhook` (POST with optional bearer token) or `files` (each run's answer and tables kept in Files) in addition to the live notification feed
- **Scheduled signed actions:** a task can run an OBDA-ACT action instead of a prompt — no LLM, the action's posture decides
- **Interactive Canvas:** Split-panel scheduling canvas with task cards showing live status, upcoming fire times, prompt preview with expand/collapse, and in-panel enable/disable/delete/history actions
- **8 Conversational Actions:** `create` · `list` · `update` · `delete` · `enable` · `disable` · `run_now` · `history`

[Architecture →](../Architecture/SCHEDULER_ARCHITECTURE.md)

---

## Flow Orchestrator — Visual Workflow Builder

The Flow Orchestrator component (`TDA_Flow`) turns any multi-step process into a visual execution graph through natural conversation — no scripting, no YAML. The agent builds, edits, and runs directed acyclic graphs node-by-node in a live React Flow canvas with per-node status animation and bidirectional sync: edit the graph manually at any step and changes propagate back to the conversation, or let the LLM modify the graph in place while you watch. Every LLM node runs through the full Fusion Optimizer pipeline — same profile, same cost tracking, same self-correction as an interactive query. Supports decision branches, parallel paths, scoped iteration loops, and version snapshots.

A Flowise-style **parameter system** makes flows reusable: cross-flow global variables, per-flow parameters, and per-node named outputs (`output_as`), all referenced inside prompts and expressions as `{name}` or `$name`. The canvas editor offers `$`-autocomplete with inline validation, undo/redo, inline rename, duplicate/copy-paste, marquee multi-select, hover-edge insert/disconnect, a `⌘K` command palette, and a `?` shortcut overlay. **Run-from-node** re-executes a single node (or a node and everything downstream) seeded from the latest run, and an inspector **Data panel** shows each node's real input/output — the n8n-style debugging loop. See [Flow Component Architecture →](../Architecture/FLOW_COMPONENT_ARCHITECTURE.md).

[Architecture →](../Architecture/FLOW_COMPONENT_ARCHITECTURE.md)

---

## Dashboard — Persistent Visualization Canvas

The Dashboard component (`TDA_Dashboard`) provides a persistent, multi-tile canvas for organizing and refreshing visualizations from any session. Pin charts, knowledge graphs, or other component outputs from your conversations to a named dashboard and keep them current with one-click refresh — a deterministic direct-MCP data replay when a tile came from a single data tool, otherwise a re-run of the original query through the Fusion Optimizer pipeline.

- **12-Column CSS Grid:** Flexible tile layout with configurable tile dimensions
- **Pin from Chat:** Any chart or knowledge graph in your conversation can be pinned to the active dashboard with a single click
- **Server-Backed Pin State:** The picker modal resolves which dashboards already contain a tile from the current chart by querying the backend — checkmarks persist across page refresh and server restart
- **Auto-Refresh:** Per-dashboard refresh interval keeps all tiles current automatically
- **Tile-Level Refresh:** Refresh individual tiles on demand; each refresh creates a REST task and streams status in real time
- **Deterministic Bolt Refresh:** When a tile's chart came from a single data tool, refresh replays that tool directly (no LLM, shown with a bolt icon) and re-applies the locked visualization — values update while the chart stays identical. A non-destructive guard ensures a failed refresh never blanks a working tile
- **Multi-Dashboard Support:** Create and switch between named dashboards; set a default for quick access
- **Split-Panel Canvas:** Opens as a persistent side panel alongside your conversation

[Architecture →](../Architecture/DASHBOARD_ARCHITECTURE.md)

---

## Agent Memory — Durable Cross-Session Memory

The Agent Memory component (`TDA_Memory`) gives the agent a managed, governed memory of durable facts and preferences about you — recalled automatically into every future session. It is the **long-term** half of memory (the Context Window handles the working/conversation half).

- **Per-agent by default, shared on request:** facts an agent learns stay with that agent; an explicit *global* scope shares them across all your agents. Each agent recalls its own memory ∪ the global pool
- **All-engine, deterministic recall:** a context module ranks and injects the most relevant memories every turn (scope + relevance + recency + recall history — zero LLM); abstains cleanly when the component is off
- **Three write paths:** the agent remembers on request (`remember`/`forget`/`list`), an opt-in background extractor learns durable preferences, and manual add/edit — with deterministic global-scope detection so "remember globally" never silently goes local
- **Read/write canvas:** a split-panel manager to browse any agent's or the global memory, edit/pause/move items, see provenance (used N× / last recalled), and preview exactly what an agent would recall for a query
- **Ranked by code, never the LLM:** recall reaches every engine through one context-window module; a memory carrying an injected instruction is stored quarantined and never recalled (the memory firewall)
- **GDPR by design:** every item is a row you can see, pause, move or erase; governed by the standard component chain — an agent left on Auto (created through the API or a pack) has it in every class, while a new agent made in the editor starts without it; switch it per agent in Capabilities or globally in Administration → Components

[Architecture →](../Architecture/AGENT_MEMORY_ARCHITECTURE.md) · design record → (`docs/roadmap/TIER_2.2_AGENT_MEMORY.md`)

---

## Skill Library — Progressive Disclosure Skills

The Skill Library component (`TDA_SkillLibrary`) lets a skill be a **library** rather than a wall of text. A skill that carries a whole body of knowledge — a methodology, a standards corpus, a product manual — ships as a lean **root** that is always injected and carries a map of what exists, plus a library of **sections** the agent opens by name, mid-turn, only when the task calls for it. An entire corpus can stand behind an agent without being paid for on every turn.

- **The agent chooses; you set the boundary:** the model decides which section to open from the map in its root (there is no similarity scoring and no pre-fetch — this is a reference work, not retrieval). What it may open *at all* is your decision
- **Library access, enforced:** grant named sections or whole branches per agent, from the profile editor or the component's Libraries tab. Anything outside the grant is **refused at the tool layer**, so one corpus can serve a designer and a builder while the designer never sees the implementation half — and the refusal names what the agent *may* read instead, so it corrects itself in the same turn
- **Grants that age well:** granting "all" stores no list (so sections added by a later update are included, not silently excluded), and branch patterns are stored as written rather than expanded
- **Costs what it opens, and says so:** a fetch is a tool call, so it adds a round-trip and the cost lands on the *next* model call — the one whose prompt now carries the section. Each step in the trace names the sections that just entered it, so a jump in tokens explains itself. Cheaper than injection for a large corpus (measured 4× on a real turn) and *not* worth it for a small skill, where injecting is both cheaper and faster
- **Inside the signature:** every library section is bound into the skill's content hash, so editing one shows up on a signed agent's trust badge exactly as editing its body would; the marketplace verifies each section's checksum *before* installing
- **Present only where it applies:** the tool is offered solely to agents that actually have a progressive disclosure skill active — for every other agent Uderia is byte-identical to before

[Architecture →](../Architecture/SKILL_ARCHITECTURE.md#4a-progressive-disclosure-skills--root--on-demand-library) · [Authoring a progressive disclosure skill →](../Architecture/SKILL_ARCHITECTURE.md#4b-authoring-a-progressive-disclosure-skill)

---

## Database Navigator — The Database as a Living Catalog

The Database Navigator component (`TDA_DatabaseNavigator`) lets you and the agent browse any connected database through one catalog contract. It works over a direct database connection (on Teradata this reads the dictionary directly: declared keys, indexes, statistics, sizes) or over the MCP tools.

- **Deterministic intelligence, zero tokens:** tables are ranked by role (fact, dimension, bridge, lookup…); keys and joins are declared or inferred with their confidence; columns are flagged (key, PII, text-stored date, measure…)
- **The ontology overlay:** the bound knowledge graph's descriptions, validated row counts, decoded values and PII classification appear on each object
- **Safe by construction:** read-only; previews are masked by default
- **One service, two faces:** the same facts serve the agent as a tool and you as a split panel, a Workspace section, and inline cards
- **Where it is offered:** Optimize and Ideate agents; new agents start without it (switch it on in Capabilities), and Coordinators never get it by default — a coordinator should route, not explore the catalogue

[Architecture →](../Architecture/DATABASE_NAVIGATOR_ARCHITECTURE.md)

---

## Compute with Lineage — Numbers You Can Trace

The Compute component (`TDA_Compute`) performs arithmetic over a turn's result sets in code, using a closed set of operations (sum, avg, min, max, count, rank, pivot, diff, join, pct_change, filter — and, for agents with several sources, `sql` to combine their results ([Multi-source →](../Architecture/MULTI_SOURCE_ARCHITECTURE.md))). The model decides *what* to compute, never *how*.

- **A receipt for every figure:** each result carries a hash, is sealed into the turn's signed provenance chain, and is persisted with the rows it was computed over
- **Every figure classified:** each number in the delivered answer is receipted, quoted from a row, taken from a query filter, a declared code, or a column total, or *unreceipted*. A near miss names the value the rows actually give
- **Clickable:** a marked figure opens its rows; the GROUNDED station and the numeric-fidelity score read the same classification

[Architecture →](../Architecture/COMPONENT_ARCHITECTURE.md#compute-component-the-receipted-figure-reference-s3-sep-2026)

---

## Files — Keep What Your Conversations Produce

The Files component (`TDA_Files`) keeps everything a session produces that is a file — a kept table, chart or answer, any export, a dashboard snapshot, a flow's output, and every chat attachment — once, encrypted, in an admin-configured store, and lists it in one panel.

- **Owned by you, placed by the agent, traced to the turn:** a file belongs to the user; its bytes live in the store of the agent that produced it; it remembers the conversation, the turn and what that turn was made from — even after the conversation is deleted
- **Stores the admin chooses:** a local folder or network share, S3-compatible, Azure Blob Storage or Google Cloud Storage, each tested before it can be saved, with capacity, quotas per tier and a default
- **Encrypted by default:** every file has its own AES-256-GCM key; the keys are wrapped by the deployment's own key or by an external key manager (AWS KMS, Azure Key Vault, Google Cloud KMS, HashiCorp Vault Transit), and existing files can be re-wrapped under a new key without rewriting them
- **One way out:** downloads pass one route that applies the tier's download policy and records every download; any browser export can be saved to Files in one click
- **Reuse in a turn:** attach a kept file to a new question — documents, images, and now CSV, TSV and Excel tables, which the model reads as a bounded table. The panel shows each file's *Used in* list as the turn starts and reopens on the same file when you return to the conversation
- **The agent's own file acts (opt-in per agent):** list, read (a table becomes rows `TDA_Compute` can combine) and save — a query result is saved from the turn's own rows, never retyped
- **Managed like every component's instances:** Components → Files has a *Files* tab beside Capabilities and Profiles — search, filter, rename, keep, download, delete and restore without opening a conversation
- **Everywhere files appear:** a dashboard becomes a PDF snapshot, a flow reads and writes files through its own nodes, a kept document can be added to a knowledge repository, and a scheduled task can keep each run's answer

[Architecture →](../Architecture/FILES_ARCHITECTURE.md)

---

## EXPLAIN Plan — Why a Query Is Slow

The EXPLAIN Plan component (`TDA_ExplainPlan`) reads a Teradata EXPLAIN the agent ran in the turn and draws it as a graph of steps: operation, inputs, join method, data movement, row estimates and their confidence.

- **Findings first:** product joins, estimates made without statistics, redistribution and duplication, and full scans are flagged before the steps, ranked worst first
- **Never runs SQL itself:** it reads a plan the turn already produced; the SQL canvas's *Explain* button draws through the same parser and spec
- **Compare after a change:** explain again and the panel keeps a history of plans, so what improved is visible
- **One builder, one renderer:** the same plan graph serves the agent's tool, the split panel and the Canvas's own EXPLAIN

[Architecture →](../Architecture/EXPLAIN_PLAN_ARCHITECTURE.md) · [Canvas →](../Architecture/CANVAS_ARCHITECTURE.md)

---

## OBDA-READ · OBDA-MODEL · OBDA-ACT — The Ontology Planes

Three components govern how an agent works with the ontology. Each has its own split panel, Workspace section and companion skill, and is available only when the admin, the agent's Capabilities and a bound graph all agree.

- **OBDA-READ** (`TDA_DataProduct`) — reads through signed data products: producer-declared slices of the ontology, proven safe by a machine gate before they can be signed. Uderia compiles a fan-out-safe statement; the agent runs it through its own data tool, so every read keeps the SQL guards, and each answer carries a sealed receipt
- **OBDA-MODEL** (`TDA_ModelProposal`) — a concept the model lacks is proposed in business words; the normalisation advisor decides entity, attribute or value domain from the data; the draft lands on the author's extension layer and a person publishes it
- **OBDA-ACT** (`TDA_Action`) — performs a signed write contract on the ontology; the consequence is computed by code and shown before anything changes, a person confirms it in the answer, and the change runs in one transaction with a receipt
- **Contracts only, when you want it:** a per-plane policy (`off · observe · enforce`) makes the signed contracts the only way a plane reads, models or writes

[OBDA-READ →](../Architecture/OBDA_ARCHITECTURE.md) · [OBDA-MODEL · OBDA-ACT →](../Architecture/ACT_ARCHITECTURE.md)

---

## Tool Finder — Only the Tools That Could Answer

The Tool Finder component (`TDA_FindTools`) is the search of the Governed Tool Surface. An agent whose tool surface is governed is offered only the tools that could answer the turn, which keeps prompts small; when it needs another, it describes what it needs and the finder brings it in. It never finds a tool Agent Authority denies, and for an agent without a governed surface it is not present at all.

Set it per agent under **Data sources → Tools → Tools in context** (*All enabled tools* · *Relevant per turn*); new Optimize agents start on *Relevant per turn*. Relevance is chosen by the question's words and its meaning, and the tools shown keep their full descriptions; every placement records its reason in the turn record and on the Live Status card.

[Architecture →](../Architecture/TOOL_SURFACE_ARCHITECTURE.md) · [Every lane →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-governed-tool-surface-s6--only-the-tools-that-could-answer-on-every-lane)

---

## Context Window Manager — The Budget Behind Every Prompt

The Context Window Manager is the system component that assembles every LLM call: thirteen modules (system prompt, tools, history, knowledge, graph, memory, documents, findings of earlier turns and more) share a token budget through a five-pass assembly, with condensation and a live snapshot of what went in. It has no tool; context window types and their modules are configured under Anatomy → Memory → Context Window.

[Architecture →](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md)

---

## Platform Connectors

Platform Connectors extend the agent’s autonomy in two ways: **autonomous capability tools** (web, files, browser, shell) that the admin configures and governs, and **enterprise collaboration accounts** (email, calendar, files, messaging) that each user authenticates via OAuth — no shared user credentials.

- **Web** — Web search, fetch, and content extraction
- **Files** — Read/write filesystem within an admin-configured path allowlist (a host filesystem tool — not the [Files component](#files--keep-what-your-conversations-produce))
- **Browser** — Playwright-based browser automation, screenshots, and form fill
- **Shell** — Command execution inside Docker-isolated throwaway containers (registry-installed connector; see Security Architecture)
- **Outlook** — Read emails, send messages, and manage calendar events via Microsoft Graph API
- **Teams** — Read channel history, post messages, and schedule meetings via Graph API
- **SharePoint** — Browse sites, read and write files via Graph API
- **Slack** — Read channel history and post messages via Slack API
- **Google Workspace** — Gmail read/send and Calendar management
- **Database Connection** — each user's own database connections (Teradata, PostgreSQL, MySQL, SQLite, DuckDB, MongoDB, JDBC), shared by the Canvas, the Database Navigator and PDM deployment
- **Remote MCP Server** — connect any HTTP/SSE MCP endpoint by URL (optional bearer/header auth); its tools are discovered at install
- **Admin governance:** Admin configures OAuth app credentials once (a single Azure AD app for all three Microsoft connectors, a separate Slack app); each user authenticates their own account via a guided OAuth popup — no shared credentials
- **Encrypted token storage:** Per-user tokens stored encrypted in `messaging_identities` (Fernet-encrypted) with automatic refresh
- **Registry-installable:** Admins browse and install connectors from the Uderia built-in catalog, the official MCP Registry, or a private enterprise registry
- **Extensible:** Add new connectors by implementing a single module and registering it — no core files need to change

[Architecture →](../Architecture/CONNECTOR_ARCHITECTURE.md)

---

## Needs Input — A Turn That Stops to Ask

The Needs Input system component is the surface of PROP-011: when a turn cannot go on without something only a person can give, it ends `input_required` with a question instead of a guess, and the person's answer continues it.

- **Uderia asks first, the model may ask too:** the resolvers raise a `choose` where two of the user's things share a name (it used to be reported as absence); the model's own act comes through `TDA_AskUser`, the planner's `plan_type: input_required`, the self-correction `ASK_USER:` or a fenced block — one validator, one ledger, and prompt text that exists only while the component is in use for the agent
- **Guarantees held in code:** a raised question refuses every further tool at the chokepoints, the answer gates abstain, the record reads `input_required` (interrupted, never terminal — every poller asks `turn_states.is_settled`)
- **One record, one binding:** the question is a row with a single-use signed resume binding; the answer re-enters the same session with the chosen value bound in code; *remember for the session* or *for this agent* turns it into a standing answer, and every use of one is recorded on the turn
- **Every face:** the card under the answer (the composer answers it too), a split panel of what is waiting and what was asked, the Assurance Inbox across agents, the status strip's *Waiting for you*; REST (`/v1/input-requests/*`, `X-Uderia-Accepts: input_required`), MCP (`ask_profile(answer_to=…)`, elicitation both ways), scheduled runs that wait and resume, flows that pause at the node, coordinators that relay
- **Governed per agent:** `authorityConfig.inputPolicy` in the Authority card — in a conversation (ask · never), unattended (park · fail), the platform's own questions (enforce · observe), questions per turn, standing answers; weakening it is a recorded downgrade; a deployment floor can hold every agent at *observe*
- **Measured:** the `needs_input` assurance point at REVIEWED owns `guessed_past_ambiguity` (the lone unscored guess); every face driven live with a fire drill (31/31) and in a real browser (17/17)

[Architecture →](../Architecture/NEEDS_INPUT_ARCHITECTURE.md)

---

## Platform — Uderia as a Tool

The Platform component (`TDA_Platform`) lets an agent use Uderia itself: every REST route describes itself (`@operation` — effect, scope, tier, what it governs), and the agent finds the operations that could answer a question and calls the ones it may, as the user, through the same routes and checks as any client.

- **Roles say what an agent is for:** Guide, Agent Builder, Knowledge Curator, Automation Builder, Quality Analyst, Insight Builder, Publisher, Admin Monitor, Problem Solver — declared by the component, signed by what they grant, chosen in the agent editor; a role carries no user tier (the tier gates each operation)
- **Writes under review:** an agent writes only where a role allows it and its write mode permits (off · review · allow); `review` parks the exact request in the Assurance Inbox, a `governs` write or any delete always waits for a person, approval runs it as the owner after a re-check; credentials are withheld from every result
- **A person's acts are never callable:** signing, activation, attestation, credentials, the default agent — listed, never callable by an agent
- **Agents asking agents:** where an administrator allows it, an agent asks the agents its owner lists, each answering in a conversation of its own that records who asked
- **Use cases as a progressive skill:** `platform-playbooks` holds the playbooks per role; `find` delivers the one that fits

[Architecture →](../Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md) · [the catalog →](../RestAPI/PLATFORM_OPERATIONS.md) · [the roles, for the people who give them →](../manuals/PLATFORM_ROLES.md)
