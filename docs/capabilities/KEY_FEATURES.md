# 🌟 Key Features

Uderia's features are organized around the seven principles stated in the [value proposition](../value/VALUE_PROPOSITION.md). Each section below lists the capabilities that realize one principle.

---

## 🚀 Actionable: From Discovery to Production in Seconds

The interface is one client of a REST-driven engine. A query explored in conversation runs unchanged as an API call: the same session, the same agent, the same engine. This is the second half of *from ideation to operationalization*: IFOC is how you work; this is how the work leaves the interface.

* **Agent setup as code**: everything configured in the interface is reachable through the API:
  - Agents, LLM configurations and MCP servers are created and changed through the same `/api/v1` routes the interface calls
  - Every route describes itself (`@operation`: effect, scope, tier, arguments) in the generated [operations catalog](../RestAPI/PLATFORM_OPERATIONS.md)
  - A finished setup exports as an agent pack (`POST /api/v1/agent-packs/export`) and imports into another deployment (`POST /api/v1/agent-packs/import`) with its knowledge, graphs, skills and trust posture
  - `scripts/platform_expert_agent.py` is a worked example: it provisions an agent over REST alone

* **Comprehensive REST API**: Full programmatic control with asynchronous task-based architecture for reliable, scalable automation:
  - Session management (create, delete, list with conversation history)
  - Query execution with async submit + poll pattern
  - Task management (status polling, cancellation, result retrieval)
  - Configuration management (profiles, LLM providers, MCP servers)
  - RAG collection CRUD operations
  - Analytics endpoints (session costs, token usage, efficiency metrics)
  - Select a non-default agent per query with `profile_id` in the request body ([Profile override →](../RestAPI/PROFILE_OVERRIDE_FEATURE.md) · [REST reference →](../RestAPI/restAPI.md))
  - An agent is callable over REST only once activated — every one of its tests passed; an unactivated agent is refused with `403 profile_not_activated`, fail-closed ([Authorization & access control →](SECURITY_AND_TRUST.md#-authorization--access-control))

* **Uderia as an MCP Server**: Use any governed Uderia agent as a tool from Claude Desktop, Cursor or another agent:
  - A built-in MCP server publishes a 12-tool catalog clamped to the caller's tier — ask an agent (`ask_profile`), list and query signed data products, list and perform signed actions, and read usage
  - Every call runs through the same authority, answer gates and audit as a turn in the UI — an external client gets no shortcut around them
  - STDIO by default; `--transport http` serves it over the network
  - [Architecture →](../Architecture/MCP_SERVER_ARCHITECTURE.md) · [Setup →](../guides/USER_GUIDE.md#uderia-as-an-mcp-server) · [Tier matrix →](../RestAPI/MCP_TIER_MATRIX.md)

* **Long-Lived Access Tokens**: Secure automation without session management:
  - Configurable expiration (90 days default, or never)
  - SHA256 hashed storage with audit trail
  - Usage tracking (last used timestamp, use count, IP address)
  - Soft-delete preservation for compliance
  - One-time display at creation for enhanced security

* **Apache Airflow Integration**: Production-ready DAG examples for batch query automation:
  - Session reuse via `tda_session_id` variable
  - Profile override via `tda_profile_id` for specialized workloads
  - Bearer token authentication for secure API access
  - Async polling pattern for reliable long-running executions
  - Complete example DAG included

* **n8n Workflow Automation**: Visual node-based workflow builder for enterprise automation:
  - Three documented reference workflows (Simple Query, Scheduled Report, Slack Integration), one of them included as importable JSON (Scheduled Report)
  - Profile override support via REST API `profile_id` parameter
  - Event-driven triggers (webhooks, cron schedules, manual execution)
  - Linear ultra-clean workflow pattern for reliability
  - Business process routing (email, Slack, CRM, databases)
  - Docker deployment with reverse proxy support
  - Comprehensive documentation with troubleshooting guides ([see docs/n8n](../n8n/README.md))

* **Flowise Integration**: Low-code workflow automation and chatbot development:
  - Pre-built agent flow for TDA Conversation handling
  - Asynchronous submit & poll pattern implementation
  - Session management with multi-turn conversation support
  - Bearer token authentication for secure API access
  - Profile override capability for specialized workflows
  - TTS payload extraction for voice-enabled chatbots
  - Visual workflow designer for complex orchestration
  - Import-ready JSON template included ([see docs/Flowise](../Flowise/Flowise.md))

* **IFOC Workflow - From Ideation to Operationalization**: Four execution modes in one conversation, switched per question with a tag:
  - **🟢 IDEATE (Conversation)**: Brainstorm, explore, and draft solutions without touching live systems—creative ideation without constraints
  - **🔵 FOCUS (Knowledge)**: Verified intelligence with zero-hallucination guarantee—every answer grounded in your documents for document-verified answers
  - **🟣 OPTIMIZE (Efficiency)**: The powerhouse—Fusion Optimizer with full MCP Tools + Prompts support, strategic planning, and self-correction for sovereign efficiency
  - **🟠 COORDINATE (Multi-Profile)**: Multi-level autonomous orchestration where coordinators manage specialist teams for cross-team orchestration
  - Switch between modes instantly with a simple `@TAG` (e.g. the bootstrap agents `@IDEAT`, `@FOCUS`, `@OPTIM`, `@COORD`)
  - Temporary overrides via `@TAG` syntax for single queries without changing defaults
  - Nested coordination support: Build 3-level AI hierarchies (Master → Coordinators → Specialists)
  - Complete safeguards: Circular dependency detection, depth limits, cost visibility at every level
  - Stop force-fitting every problem into one AI—match your intent to the right intelligence phase

* **Session Primer & Automatic Context**: Transform generic LLMs into pre-educated specialists from the first message:
  - Auto-initialize new sessions with domain-specific knowledge
  - Inject business context, schemas, and common patterns at session creation
  - Profiles can define default primer content for consistent onboarding
  - Eliminates repetitive context-setting for every new conversation
  - Specialists understand your environment without manual training

* **Autonomous AI Organization (Genie Mode)**: From intent to autonomy—your AI organization that senses, reasons, and delivers:
  - Multi-profile coordination where specialized agents work as a unified team
  - Master coordinator intelligently routes queries to domain experts
  - Automatic discovery and orchestration of specialist capabilities
  - Cross-domain synthesis: agents gather data independently, then coordinate findings
  - Real-time topology visualization showing agent activation and collaboration
  - Stop orchestrating manually—start delegating to an AI organization that never sleeps
  - Executive-level queries like "Improve Product Margin for Q4" automatically cascade to CFO, CMO, and Legal specialists

* **Intelligent MCP Server Import**: Import community MCP servers in either of two formats:
  - Import from official MCP Registry format (io.example/server-name specifications)
  - Import from Claude Desktop configuration files (direct migration)
  - Automatic format detection with validation
  - Bulk import multiple servers at once
  - Two client transports: 🟠 STDIO (local subprocess) and 🔵 Streamable HTTP (remote; entries saved as `sse` or `http` connect through the same client)
  - STDIO servers: automatic subprocess lifecycle management (npx, uvx, python)
  - Server-side ID generation ensures uniqueness
  - Duplicate detection prevents configuration conflicts
  - One-click access to [MCP community servers](https://github.com/modelcontextprotocol/servers)

* **Docker Deployment Support**: Production-ready containerization:
  - Multi-user support in single shared container
  - Environment variable overrides
  - Volume mounts for the databases, sessions, keys, files and vector caches
  - Load balancer ready for horizontal scaling
  - Upgrades back up the database before changing its layout, and refuse to start if they cannot

---

## 🔍 Transparent and Trusted: Eliminate the AI Black Box

Build trust through complete visibility into every decision, action, and data point the agent processes — and through trust that is checked, recorded and signed rather than asserted.

### Trust that is earned, shown and carried

An agent's trust is not a claim made about it: it is checked on every answer, recorded, signed, and visible to the people who rely on the agent.

* **Answer gates**: before an answer reaches the user it passes the grounding gate (does it stay within the data and knowledge the turn actually had?), the honesty gate (is what it claims about its own evidence true?) and the guardrails (personal data, toxicity, prompt injection, in and out). Per agent, each gate observes, annotates, or enforces — optionally keeping the withheld original for review. [Answer gates →](SECURITY_AND_TRUST.md#-answer-gates--between-the-draft-and-the-user)
* **Scored after every turn**: a profile-class-aware scorer suite grades each turn, most of it deterministic and at no token cost; the verdicts are signed and bound to the turn they grade. [Evaluation & attestation →](SECURITY_AND_TRUST.md#-agent-evaluation--attestation) · [Proof pack A6 →](../proof-packs/A6_EVAL_OBSERVABILITY.md)
* **Signed parts, one trust state**: an agent's configuration, knowledge, graphs and skills are each signed; the agent is trusted only when every part it uses is, and a changed part is named. [Trust Membrane →](TRUST_MEMBRANE.md)
* **Evidence per answer**: each answer carries the inventory of what produced it, with each part's signature state at that moment, exportable and verifiable without the deployment. [Evidence →](EVIDENCE.md)
* **Authority that fails closed**: an agent acts only within the tools, data and writes it was granted; anything else is refused at the chokepoint, not left to the model. [Authorization & access control →](SECURITY_AND_TRUST.md#-authorization--access-control)
* **The Trust Circle**: every agent shows where it stands on each station of its trust model and which act is owed next, in one grammar across every surface. [The Living Trust Circle →](SECURITY_AND_TRUST.md#-the-living-trust-circle)
* **Trust travels**: an agent exported as a pack carries its trust posture; the receiving deployment checks it again before it is trusted there. [Marketplace →](MARKETPLACE.md)

### Visibility into every step


* **Live Status Panel**: Real-time window into the agent's reasoning process:
  - Strategic plan visualization with phase-by-phase breakdown
  - Tactical decision display showing tool selection rationale
  - Raw data inspection for every tool response
  - Self-correction events with recovery strategy visibility
  - Streaming updates via Server-Sent Events (SSE)
  - Dual-model cost breakdown for Fusion Optimizer showing strategic vs tactical costs with color-coded visualization
  - [Architecture →](../Architecture/SESSION_ARCHITECTURE.md)

* **Sessions & Live Streaming**: Every conversation is one record holding three histories — what the LLM sees, what the UI renders, and the planner's turn trace — written through one serialised seam over file or PostgreSQL storage, with in-process or Redis locks.
  - Every turn, interactive or REST, runs as its own task; a dropped connection can reattach to a turn that is still running
  - The browser renders only the session on screen, and restores a running session exactly as it was left when you switch back
  - Sessions are kept per user (`tda_sessions/<user_uuid>/<session_id>.json` with the file backend), and archived — not deleted — when an artefact they used is removed
  - [Architecture →](../Architecture/SESSION_ARCHITECTURE.md)

* **Asks Instead of Guessing (Needs Input)**: A turn that cannot go on without a person stops and asks — never guesses, never gives up silently
  - Uderia finds the ambiguity in code (two of your things with one name, a value nobody recorded, a step that cannot be undone) and raises the question itself; the model may ask too, through one tool or a marked block
  - No tool runs past a pending question, the question reaches you verbatim, the answer gates abstain, and the turn reads `input_required` — interrupted, not failed
  - Your answer is a single-use signed binding; the next turn continues with the chosen value bound in code, remembered for the session or the agent if you say so
  - Holds everywhere a turn runs: a scheduled run waits and tells its channel, a flow pauses at the node that asked, a coordinator relays an expert's question up and your answer down, an unconnected connector asks you to connect
  - [Architecture →](../Architecture/NEEDS_INPUT_ARCHITECTURE.md)

* **Smart Next Steps**: After each answer, a few one-tap suggestions sit in a dock above the composer — actions such as *Chart this* or *Pin to dashboard*, and follow-up questions drawn from your knowledge.
  - The two default tiers (action chips and retrieved follow-ups) spend **zero extra LLM tokens**; an LLM-enriched tier is strictly opt-in
  - Suggestions are ephemeral: never stored in the chat, gone on the next question
  - Optional at three levels — admin gate, per agent, per user mute — and a recommender error can never break a turn
  - [Architecture →](../Architecture/NEXT_STEP_RECOMMENDER_ARCHITECTURE.md)

* **The Ontology View**: The sidebar's *Ontology* is a live, agent-centric map of your whole estate — every agent, what grounds it, what it remembers, and which models, connectors and stores it depends on.
  - Built on read from live sources, so it is always in sync and has nothing to migrate
  - Answers what no single config screen can: which agents are grounded, which model is a single point of failure, what is configured but unused
  - Never re-draws configuration — every node links out to the screen that manages it; admins can view across users
  - [Architecture →](../Architecture/PLATFORM_ONTOLOGY_ARCHITECTURE.md)

* **A Vote That Teaches**: A thumbs up or down goes through one dispatcher to the part of the agent that produced the answer — the champion plan it reused, the documents it relied on, the expert it routed to, the memory it recalled.
  - Hovering a vote tells you what it will do before you cast it
  - Votes sit beside the quality scores as a separate preference axis, so disagreement between people and scores stays visible
  - [Architecture →](../Architecture/FEEDBACK_SIGNAL_ARCHITECTURE.md)

* **Dynamic Capability Discovery**: Instant overview of agent potential:
  - Automatic loading of all MCP Tools from connected servers
  - Prompt library display with categorization
  - Resource enumeration for data source visibility
  - Real-time capability updates on configuration changes
  - Visual organization in the Resource Panel (an icon rail beside the chat; each badge reads enabled/available)
  - [Architecture →](../Architecture/TOOL_SURFACE_ARCHITECTURE.md)

* **Every Number Has a Receipt** *(deployment switch `TDA_COMPUTE_LINEAGE`, on in the Docker deployment)*: Figures in an answer are computed by code, not by the model's own arithmetic. Click any marked number to see the exact rows it came from; a near miss is named rather than passed over. [Details →](../Architecture/COMPONENT_ARCHITECTURE.md#compute-component-the-receipted-figure-reference-s3-sep-2026)

* **Rich Data Rendering**: Intelligently formats and displays various data types:
  - Query results in interactive tables with sorting/filtering
  - SQL DDL in syntax-highlighted code blocks
  - Key metrics in summary cards
  - Integrated charting engine for data visualization ([Architecture →](../Architecture/CHART_ARCHITECTURE.md))
  - Real-time rendering as data streams in

* **Comprehensive Token Tracking**: Per-turn visibility into LLM consumption:
  - Input token counts for every request
  - Output token counts for every response
  - Token-to-cost mapping with provider-specific pricing
  - Historical token trends across sessions
  - Optimization insights for cost-conscious users
  - Theme-aware KPI displays follow the dark and light themes
  - [Architecture →](../Architecture/COST_TRACKING_ARCHITECTURE.md)

* **Anti-Hallucination by Architecture**: Ground every answer in verified sources—zero fabrication:
  - Strict retrieval-then-synthesize pattern where the LLM answers only from retrieved documents
  - Knowledge Graph maps your databases (tables, relationships, business concepts) before query generation
  - RAG system retrieves and scores documents from knowledge bases with full citations
  - Source traceability with citations back to specific document chunks
  - Transparent failure when no relevant sources exist (no guessing)
  - Dual knowledge layers work in unison for comprehensive grounding
  - Deterministic answer gates (grounding, honesty, guardrails) check every draft before it reaches you — [Architecture →](../Architecture/ANSWER_GATES_ARCHITECTURE.md)

* **Execution Monitoring (Awareness)**: Cross-source workload tracking:
  - Session roster with status filters and analytics
  - Per-session execution details: reasoning steps, tool calls with arguments and results, errors
  - A running turn can be cancelled (REST `POST /v1/tasks/<id>/cancel`)

* **Enterprise Audit Logging - From Uncertainty to Accountability**: Every action recorded, every decision traceable:
  - Complete forensic trail with user, IP, timestamp, and outcome for every interaction
  - User authentication and authorization events (login attempts, OAuth flows, token generation)
  - Configuration changes with before/after snapshots (LLM provider switches, profile updates)
  - Prompt executions with full turn-level attribution and cost tracking
  - API usage patterns and access history for security monitoring
  - Admin actions on user accounts and system settings
  - Progressive security lockouts and suspicious activity detection
  - 20+ specialized logging functions for comprehensive coverage
  - Configurable retention policies for GDPR and data sovereignty compliance
  - REST API access for integration with compliance tools (SOC2, audit reports)
  - From audit trail to compliance report in one click
  - [Architecture →](../Architecture/SECURITY_ARCHITECTURE.md)

* **Intelligent Context Window Management**: Budget-aware orchestration of every token sent to the LLM:
  - Modular architecture with 13 pluggable context modules (system prompt, tools, history, champion-case reporting, knowledge, knowledge graph, documents, agent memory, review release, findings of earlier turns and more)
  - Five-pass assembly pipeline: resolve → dynamic adjustments → allocate & assemble → surplus reallocation → condense
  - Per-module budget allocation with min/max constraints and priority-based condensation
  - Dynamic adjustment rules that adapt context composition at runtime (first turn, long conversations, high-confidence RAG)
  - Real-time observability via context window snapshot events with per-module utilization metrics
  - 4 predefined context window types (Balanced, Knowledge-Heavy, Conversation-First, Token-Efficient) plus custom types
  - Admin UI with live budget visualization, condensation order editor, and dynamic rule builder
  - Per-session utilization analytics dashboard with trend charts and module breakdown
  - tiktoken-based BPE token estimation for accurate budget planning
  - [Full architecture documentation →](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md)

* **Interactive Visual Components**: Modular, plugin-based UI component library:
  - **Canvas Component:** A code and SQL editor in the conversation. It runs queries against your databases, completes table and column names from the Database Navigator, and hands a statement to the agent in one click.
  - **Chart Component:** 17 chart types, with the right chart and columns picked from your data automatically.
  - **Dashboard Canvas:** Pin any chart, table or result from the conversation to a persistent dashboard. Arrange and resize tiles, and refresh them on demand without asking again.
  - **Knowledge Graph:** Entity-relationship visualization for context enrichment and document structure exploration
  - **Task Scheduler:** Ask for a recurring task in plain words. It runs exactly like a normal question and delivers results by email, webhook or to Files.
  - **Flow Orchestrator:** Describe a multi-step workflow and the agent builds, edits and runs it on a live canvas, with branches, parallel paths and loops. Start from a ready-made shape such as a pipeline, a debate or pair programming, filled with a team's agents.
  - **EXPLAIN Plan:** See why a Teradata query is slow: its plan drawn as a graph of steps, costly patterns flagged first. Explain again after a change to see what improved.
  - **Database Navigator:** Browse any connected database as a living catalog, with what each table is for, its keys, likely joins and masked previews. It serves you as a panel and the agent as a tool.
  - **Files:** Keep any table, chart, answer or export from a conversation in an encrypted store the admin chooses (a local folder, a share, S3, Azure Blob or Google Cloud Storage), find it again, see where it came from, and attach it to a new question.
  - **Agent Memory:** Durable facts and preferences about you, recalled into every future conversation, each one inspectable and erasable.
  - **Compute with Lineage:** Sums, rankings, pivots and differences computed by code over the turn's results, with a receipt behind every figure.
  - **Skill Library:** A skill that is a whole library: the agent opens only the sections a task needs, within the sections you grant it.
  - **OBDA-READ · OBDA-MODEL · OBDA-ACT:** Read through signed data products, propose model extensions a person publishes, and perform signed write contracts a person confirms.
  - **Tool Finder:** On a governed tool surface the agent sees only the tools that could answer, and finds any other tool by describing what it needs ([Architecture →](../Architecture/TOOL_SURFACE_ARCHITECTURE.md)).
  - **Context Window Manager:** The budget-aware assembly of every prompt (see above).
  - Self-contained component architecture with manifest-driven discovery and hot-reload
  - Profile-level intensity control and admin governance
  - 3 render targets: inline (chat bubble), sub_window (persistent canvas panel), status_panel (Live Status area)
  - Third-party extensibility: add custom components without modifying core files

* **System Customization**: Take control of agent behavior:
  - System prompt editing in **Administration → Prompts** (Prompt Engineer and Enterprise licences), with per-agent prompt overrides on the agent profile
  - Save and reset capabilities for experimentation
  - An Ideate agent without tools for baseline testing against the bare model
  - Dynamic Capability Management (enable/disable tools/prompts)
  - Phased rollouts without server restart

---

## 💬 The Conversation Agent: Streaming, Steerable, Accountable

The answer arrives as it is written and stays accountable. It carries its checks, its recoveries and its alternatives in the turn's signed record. Each capability works across the profile classes; its detail section records the lanes it covers and its honest limits.

* **The Living Answer** *(deployment switch `TDA_ANSWER_STREAMING`, on in the Docker deployment)*: The answer streams in place and never jumps, and notes, evidence and actions sit beneath it in one consistent layout. When it is done, a short receipt shows its steps and one verdict: *Verified*, *N findings* or *Not checked*. Stop it at any time and keep what arrived. [Architecture →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-governed-stream-s1--one-delta-sink-every-engine) · [Reading it →](../manuals/AGENT_TRUST_AND_ASSURANCE.md) · Answer layout → (`docs/roadmap/ANSWER_SURFACE_GRAMMAR.md`)
* **Steer & Reattach** *(deployment switch `TDA_STEER_REATTACH`, on in the Docker deployment)*: Send a note mid-answer and the agent uses it at its next step. Close the tab and come back: the answer is still running. [Architecture →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#steer--reattach-s10--talk-to-the-agent-while-it-works-never-lose-a-running-turn)
* **Regenerate as an Experiment** *(deployment switch `TDA_TURN_LINEAGE`, on in the Docker deployment)*: A second answer is scored beside the first, so you choose with a reason. Editing a question starts a branch, and nothing is overwritten. [Architecture →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#regenerate-as-an-experiment-s8--a-second-answer-and-the-reason-to-prefer-it)
* **The Resilience Envelope** *(deployment switch `TDA_RESILIENCE_ENVELOPE`, on in the Docker deployment)*: When a model provider fails, a fallback model you approved takes over and the answer says so. Every recovery is recorded. [Architecture →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-resilience-envelope-s4--one-policy-every-lane) · [LLM providers →](../Architecture/LLM_PROVIDER_ARCHITECTURE.md)
* **The Outcome Loop** *(per agent, opt-in)*: A draft with a fixable issue gets one chance to fix it, and is delivered only if the fix passes the same check. [Architecture →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-outcome-loop-s7--the-gates-finding-goes-back-to-the-model-once-on-every-lane) · [Answer gates →](../Architecture/ANSWER_GATES_ARCHITECTURE.md)
* **Grounded Multimodal** *(deployment opt-in, needs the OCR extras)*: Uderia reads attachments (images, PDFs, spreadsheets) itself, so their tables can be cited as sources. [Architecture →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#grounded-multimodal-s9--an-attachment-is-a-source-not-a-prompt)

The design specification behind these increments, including what was deliberately not built, is `docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md`.

---

## ⚡ Efficient: Intelligent Optimization Engine

The Fusion Optimizer plans before it acts, runs known steps without a model call, and recovers from failed steps.

**Where the savings come from:** the plan is written once instead of the model re-reading its whole context at every step; steps whose arguments are already known run without a model call (FASTPATH); a question the agent has answered before replays a proven plan; and the most frequent calls can go to a cheaper model. Real per-agent cost figures, read from a live instance, are in the [P4 FinOps proof pack](../proof-packs/P4_FINOPS.md).

See the dedicated section below (**[The Fusion Optimizer](FUSION_OPTIMIZER.md)**) for comprehensive architectural details on:

* Multi-layered strategic and tactical planning
* Proactive optimization (Plan Hydration, Tactical Fast Path, Specialized Orchestrators)
* Self-correction of failed steps, with honest failure when a step cannot be fixed
* Context-aware learning from execution history
* Deterministic plan validation and hallucination prevention

**Key efficiency highlights:**

* **The Attested Cache** *(deployment switch `TDA_PROMPT_CACHING`, on in the Docker deployment)*: The stable part of every prompt is cached and paid for once, priced correctly per provider. An unexpected cache miss is reported instead of silently billed. One cache plan serves every provider: the strategic planner marks the stable start of its prompt; Claude (direct, through OpenRouter or on Bedrock) and Amazon Nova get a cache breakpoint there, while OpenAI, Azure and xAI receive each agent's routing key so repeated turns land on a warm cache. An option a provider refuses is learned, never hardcoded. [Details →](../Architecture/COST_TRACKING_ARCHITECTURE.md#cache-aware-pricing-s2--the-attested-cache) · [Cache plan →](../Architecture/PROMPT_CACHE_ARCHITECTURE.md)

* **The Governed Tool Surface** *(per agent)*: An agent no longer has to read its whole tool catalog every turn. On *Relevant per turn* it sees the discovery and execution backbone, the framework tools, and the tools the question addresses by its words or comes close to in meaning — with their full descriptions — while the rest wait one `TDA_FindTools` search away. A tool the agent's authority denies is never offered and never found, and every placement records its reason in the turn record and on the Live Status card. Set it per agent under **Data sources → Tools → Tools in context** (*All enabled tools* · *Relevant per turn*); new Optimize agents start on *Relevant per turn*. [Architecture →](../Architecture/TOOL_SURFACE_ARCHITECTURE.md) · [Every lane →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-governed-tool-surface-s6--only-the-tools-that-could-answer-on-every-lane)

* **Multi-Source Agents**: One agent can read several sources — more than one MCP server and the Database Navigator's database connections. Each call is routed to the server that holds its data, and every result names the source it came from. When an answer needs rows from two sources together, the model decides *what* to combine and code decides whether it may: the combine runs as one read-only SELECT over the turn's own results (`TDA_Compute` op `sql`), with isolation rules a deployment can tighten per endpoint. Sources are managed in the agent's **Data sources** section. [Architecture →](../Architecture/MULTI_SOURCE_ARCHITECTURE.md)

* **The Visible Budget** *(per agent)*: Give a turn a step, token or time budget the agent can see. It paces itself, finishes gracefully, and *Continue* picks up the rest. [Details →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#the-visible-budget-s5--one-budget-every-data-running-lane)

* **Proven Teradata SQL Patterns**: A library of Teradata SQL patterns, every statement run on a real Teradata before it ships. Agents open only the sections their own database can run. [Details →](../Architecture/SKILL_ARCHITECTURE.md)

* **Self-Improving Learning System**: Closed-loop learning from past successes:
  - Automatic capture and archiving of all successful interactions
  - Token-based efficiency analysis to identify "champion" strategies
  - Few-shot learning through injection of champion examples
  - Asynchronous processing to eliminate user-facing latency
  - Per-user cost savings attribution and tracking

* **Planner Repository Constructors**: Modular plugin system for domain-specific optimization:
  - Self-contained templates with validation schemas
  - SQL query templates with extensibility for document Q&A, API workflows
  - LLM-assisted auto-generation from database schemas
  - Registered in `rag_templates/template_registry.json`, hot-reloadable without a restart
  - Programmatic population via REST API for CI/CD integration

* **Knowledge Repositories**: Domain context injection for better planning:
  - PDF, TXT, DOCX, MD document support
  - Configurable chunking strategies (fixed-size, paragraph, sentence, semantic — semantic currently chunks by paragraph)
  - Automatic retrieval during strategic planning for context-aware decisions
  - Semantic search for relevant background information
  - [Intelligence Marketplace](MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) integration for community knowledge sharing

---

## 🛡️ Sovereignty: Your Data, Your Rules, Your Environment

Maintain complete control over your data exposure strategy with flexible deployment and provider options.

* **Multi-Provider LLM Support**: Freedom to choose your AI infrastructure:
  - **Cloud Hyperscalers**: Google (Gemini), Anthropic (Claude), OpenAI (GPT), Azure OpenAI
  - **AWS Bedrock**: Foundation models and inference profiles for custom/provisioned models
  - **OpenRouter**: Unified gateway to 100+ open and proprietary models via a single API key
  - **Friendli.AI**: High-performance serverless and dedicated endpoint support
  - **xAI**: Grok models via xAI API
  - **Celeris**: Celeris-1 diffusion LLM via its OpenAI-compatible API (`celeris_api_key`, optional base-URL override)
  - **MLX**: Apple Silicon local inference on your own infrastructure ([setup guide](../MLX/README.md))
  - **Ollama**: Fully local, offline LLM execution on your own infrastructure
  - Dynamic provider switching without configuration restart
  - Live model refresh to fetch latest available models
  - **One layer, no provider branches:** no call site needs to know which provider it is talking to. When a provider fails, the call is retried, continued, or served by the agent's own *signed* fallback model; every recovery is recorded and priced at the model that actually answered. When nothing can answer, the user gets one honest sentence naming the cause instead of a raw error, and provider quirks (such as models that refuse to disable thinking) are learned from the provider's own refusal, never from hard-coded model lists.
  - [Architecture →](../Architecture/LLM_PROVIDER_ARCHITECTURE.md)

* **Comparative LLM Testing**: Validate model behavior across providers:
  - Identical MCP tools and prompts across different LLMs
  - Side-by-side performance comparison
  - Model capability robustness validation
  - An Ideate agent without tools for baseline reasoning assessment
  - Profile-based A/B testing with `@TAG` overrides
  - [Architecture →](../Architecture/LLM_PROVIDER_ARCHITECTURE.md) · Profile comparison skill → (`.claude/skills/profile-perf/SKILL.md`)

* **Encrypted Credential Storage**:
  - Fernet symmetric encryption for all API keys
  - Per-user credential isolation in SQLite database
  - Credentials never logged or exposed in UI/API responses
  - Secure passthrough to LLM/MCP providers
  - Admin oversight without credential access
  - [Architecture →](../Architecture/SECURITY_ARCHITECTURE.md)

* **System Prompts, Versioned**: the fifteen system prompts are plain-text files shipped with the code; every stored change is versioned with who and why; a release updates a prompt nobody edited and never one a person did; per-user and per-profile overrides are a separate layer; the editor is part of the enterprise package
  - [Architecture →](../Architecture/PROMPT_ARCHITECTURE.md)

* **Multi-User Isolation**: Complete session and data segregation:
  - JWT-based authentication with 24-hour expiry (configurable, `TDA_JWT_EXPIRY_HOURS`)
  - User-specific sessions (`tda_sessions/<user_uuid>/`, or PostgreSQL with the Postgres backend)
  - Database-level user UUID isolation
  - Role-based access control (User, Developer, Admin)
  - Simultaneous multi-user support with no cross-contamination
  - [Sessions →](../Architecture/SESSION_ARCHITECTURE.md) · [Security →](../Architecture/SECURITY_ARCHITECTURE.md)

* **Flexible Deployment Options**: Adapt to your infrastructure:
  - Single-user development (local Python process)
  - Multi-user production (load-balanced containers or shared instance)
  - HTTPS support via reverse proxy configuration
  - Docker volume mounts for persistent data

* **White-Label Branding**: One deployment can present itself as a purpose-built product per DNS name, with no fork:
  - A brand is a descriptor plus its assets (`static/branding/<key>/brand.json`) — names and copy, logos, favicon, accent colours, email sender — and adding one touches no Python and no existing template
  - Chosen per request by the `Host` header, or pinned for a dedicated instance with `UDERIA_BRAND`
  - An unbranded deployment renders byte-identically to the default Uderia; a malformed descriptor is skipped, so branding can never break a request
  - Branding is not tenancy: for real data isolation give a customer its own instance (the env pin rides along)
  - [Architecture →](../Architecture/BRANDING_ARCHITECTURE.md)

* **Voice Conversation Privacy**: Optional Google Cloud TTS with user-provided credentials:
  - User-controlled API key management
  - No server-side credential storage for voice features
  - Browser-based Speech Recognition (local processing)
  - Hands-free operation with configurable voice modes
  - Key observations handling (autoplay-off, autoplay-on, off)

* **Platform Connectors**: Give the agent autonomous action plus enterprise collaboration access:
  - **Web** — Web search, fetch, and content extraction (admin-configured search API)
  - **Files** — Read/write filesystem within an admin-configured path allowlist (a host filesystem tool — not the [Files component](COMPONENTS.md#files--keep-what-your-conversations-produce))
  - **Browser** — Playwright-based browser automation, screenshots, and form fill
  - **Shell** — Command execution inside Docker-isolated throwaway containers (registry-installed connector; its audit is kept by the connector)
  - **Outlook** — Read emails, send messages, manage calendar events via Microsoft Graph API
  - **Teams** — Read channel history, post messages, schedule meetings via Graph API
  - **SharePoint** — Browse sites, read and write files via Graph API
  - **Slack** — Read channel history, post messages via Slack API
  - **Google Workspace** — Gmail read/send and Calendar management (existing connector)
  - **Database Connection** — each user's own database connections (Teradata, PostgreSQL, MySQL, SQLite, DuckDB, MongoDB, JDBC), shared by the Canvas, the Database Navigator and PDM deployment
  - **Remote MCP Server** — connect any HTTP/SSE MCP endpoint by URL (optional bearer/header auth); its tools are discovered at install
  - SaaS connectors require a per-user OAuth consent flow; admin configures app credentials once via **Administration → Components → Connectors**
  - Tokens stored encrypted (`messaging_identities` table, Fernet-encrypted) with automatic refresh
  - [Architecture →](../Architecture/CONNECTOR_ARCHITECTURE.md)

* **Document Upload & Multimodal Analysis**: Attach documents and images directly in chat conversations:
  - Native multimodal delivery for capable providers (Google Gemini, Anthropic Claude, OpenAI GPT, Azure, AWS Bedrock Claude)
  - Automatic text extraction fallback for all other providers (OpenRouter, Friendli, Celeris, xAI, Ollama, MLX, Bedrock non-Claude models)
  - Supports PDF, DOCX, TXT, MD, CSV, TSV, XLSX and image formats (JPG, PNG, GIF, WebP); every attachment is kept encrypted in the Files store
  - Drag-and-drop or click-to-attach with image thumbnail previews and Visual/Text processing badges
  - Provider-aware routing: images sent natively to vision models, documents via base64 or text extraction as appropriate
  - Up to 5 files per message, 50 MB per file
  - Full REST API support for programmatic upload workflows
  - [Files →](../Architecture/FILES_ARCHITECTURE.md) · [Grounded multimodal →](../Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md#grounded-multimodal-s9--an-attachment-is-a-source-not-a-prompt) · [Provider capabilities →](../Architecture/LLM_PROVIDER_ARCHITECTURE.md)

* **Decoupled Planning with Champion Cases**: An Optimize agent can split its model calls: a cloud model for strategic planning and a local model for tactical steps (the profile's `dualModelConfig`).

  **How It Works:**
  1. **Strategic planning** on the strategic model, guided by proven plans from the agent's planner repositories (3 by default)
  2. **Tactical steps** — choosing each tool and its arguments — on the tactical model
  3. **Every other call** (the final report, self-correction) on the agent's base model

  **What the strategic model sees:** the question, the tool and schema context, the champion plans, and summaries of earlier turns, which can include their results. Choose the split with that in mind.

  **Full sovereignty** comes from running every layer locally — inference, embeddings, vector and graph stores, data source and state — which is a configuration, not a build ([P6 proof pack](../proof-packs/P6_SOVEREIGNTY.md)).

* **Enterprise OAuth Authentication**: Federated identity with five providers:
  - **Supported Providers:** Google (OIDC), GitHub (OAuth2), Microsoft/Azure AD (OIDC), Discord (OAuth2), Okta (OIDC)
  - CSRF protection via cryptographic state parameter validation
  - Email verification with configurable enforcement
  - Account merging and deduplication (link multiple providers to one account)
  - Rate limiting with abuse detection and progressive lockout
  - Throwaway email blocking for registration integrity
  - Brute force detection on login attempts
  - Comprehensive audit logging for all authentication events
  - Provider popularity analytics and usage tracking
  - Account linking/unlinking for existing users
  - Full REST API: initiate, callback, link, disconnect, verification endpoints
  - **Enterprise SSO:** any OIDC IdP and SAML 2.0 (ADFS, Okta, Azure AD, OneLogin, PingIdentity) with group-to-tier mapping and JIT provisioning — Administration → SSO
  - [OAuth setup →](../OAuth/README.md) · [Security →](../Architecture/SECURITY_ARCHITECTURE.md)

* **Three-Tier Role-Based Access Control**: Hierarchical permission system with granular feature governance:
  - **User Tier** (19 features): Execute prompts, use MCP tools, manage own sessions and credentials, basic configuration
  - **Developer Tier** (+25 features, 44 total): RAG collection management, template creation/testing, MCP diagnostics, import/export, advanced configuration
  - **Admin Tier** (+22 features, 66 total): User management, credential oversight, system configuration, security settings, database administration, compliance reporting
  - Feature tags mapped to a minimum required tier via a central `FEATURE_TIER_MAP`, enforced by tier-based access checks (`@require_auth` / `@require_admin`)
  - Hierarchical permission inheritance (Admin inherits all Developer features, Developer inherits all User features)
  - 5 predefined feature groups for bulk permission checks (session_management, rag_management, template_management, user_management, system_admin)
  - Tier-based UI adaptation: features appear/disappear based on user's tier
  - REST API endpoint: `GET /api/v1/auth/me/features` returns user's available features
  - Admin self-protection: administrators cannot modify their own tier
  - Backward compatible with legacy `is_admin` field
  - [Architecture →](../Architecture/SECURITY_ARCHITECTURE.md)

* **A Database That Says When It Is Damaged**: The runtime's database is copied and verified every night (02:30 UTC, the last seven kept in `backups/db/`), and an unreadable database raises an alert banner on every open page instead of failing quietly. Off-host backups read the verified copies, never the live file. [Architecture →](../Architecture/SQLITE_CONCURRENCY.md)

---

## 💰 Financial Governance: Track Every Penny, Control Every Cost

Transparent, real-time cost tracking with fine-grained control over spending at every level of abstraction.

* **Real-Time Cost Tracking**: Per-interaction visibility:
  - Automatic cost calculation using up-to-date provider pricing
  - Per-turn breakdown (input tokens, output tokens, total cost)
  - Session-level cumulative cost tracking
  - User-level cost aggregation across all sessions
  - Historical cost trends and analytics

* **Provider-Specific Pricing Models**: Accurate cost attribution:
  - One current input and output price per model (per 1M tokens), with cached input priced by the provider's read factor; no context-tier or batch pricing
  - Google Gemini (current 3.x family)
  - Anthropic Claude (Opus, Sonnet, Haiku)
  - OpenAI GPT models
  - Azure OpenAI (priced from the deployment's base model)
  - AWS Bedrock (foundation models, inference profiles)
  - OpenRouter (100+ models with live pricing via model catalog)
  - Friendli.AI serverless and dedicated endpoints (live pricing from its model catalog)
  - xAI (Grok) · Celeris
  - Ollama and MLX (local models, zero external cost)

* **Database-Backed Cost Persistence**: Complete financial audit trail:
  - `llm_model_costs` table: one price row per provider and model, with its source (manual, configured, live provider API or LiteLLM)
  - `user_consumption` and `consumption_turns` tables tracking per-turn token usage and cost
  - `consumption_periods_archive` for immutable monthly cost rollover; per-session cost summaries persist in session files (`tda_sessions/`), and RAG learning savings in the efficiency tracker (`efficiency_state.json`)
  - `access_tokens` with usage tracking
  - Exportable cost reports for budgeting and forecasting

* **Profile-Based Spending Controls**: Optimize costs by workload:
  - Tag profiles by cost characteristics (e.g., "COST" for Gemini Flash)
  - Quick switching between expensive (Claude Opus) and economical (Gemini Flash) models
  - Profile override via `@TAG` syntax for cost-conscious queries
  - REST API profile selection for automated cost optimization

* **Efficiency Attribution**: Quantify learning system savings:
  - Before/after token comparison for champion case-guided planning
  - Estimated cost savings from few-shot learning
  - Per-user attribution of efficiency gains
  - Continuous improvement ROI visibility

* **Cost Management Dashboard & Pricing Sync**: Admin financial control center:
  - Analytics dashboard with total spend, average cost per session and per turn, cost-by-provider, and most-expensive-model charts (`GET /api/v1/costs/analytics`)
  - Automatic model pricing sync from the LiteLLM library with live provider availability probes; manual price overrides and fallback pricing are protected from overwrite
  - Model deprecation tracking flags models that are no longer available
  - Monthly consumption rollover archives each period to an immutable record, alongside a per-turn consumption audit trail

* **Every Model Priced, Every Cost Where It Was Spent**: No silent zero on the bill:
  - Every model in use is priced when it is selected, daily and on demand — from a live provider price list, the vendor's OpenRouter listing or LiteLLM — or named as unpriced (`GET /api/v1/costs/coverage`); a turn priced from a fallback reads *estimated*
  - Spend is booked under the model that was actually called; each judge model is its own consumption line

* **Cost Optimization Recommendations** *(planned)*: Actionable insights:
  - Model selection guidance based on task complexity
  - Context pruning opportunities for token reduction
  - Champion case population priorities for maximum savings
  - Profile configuration suggestions for workload patterns

* **Consumption Profile Enforcement**: Granular usage controls and quotas:
  - Four predefined tiers: Free, Pro, Enterprise, Unlimited
  - Per-user prompt rate limits (hourly and daily)
  - Monthly token quotas (input and output tokens separately)
  - Configuration change rate limits per hour
  - Profile activation/deactivation for testing
  - Global override mode for emergency rate limiting
  - Admin bypass for unrestricted system access
  - Real-time enforcement with clear error messages
  - Database-backed consumption tracking and audit trail

[Cost Tracking Architecture →](../Architecture/COST_TRACKING_ARCHITECTURE.md)

---

## 🤝 Collaborative: Intelligence Marketplace

The [Intelligence Marketplace](MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) is where execution patterns, domain knowledge, agent teams, skills, extensions and knowledge graphs are shared and discovered. Community-validated assets lower token cost and shorten onboarding, because a strategy that worked elsewhere is the starting point here.

**See the full [Intelligence Marketplace](MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) section in Core Concepts for:**
- Product catalog (7 asset types)
- Smart discovery and search
- Reference-based subscriptions and forking
- Community ratings and quality assurance
- Publishing workflows and API integration
- Agent Packs for portable AI teams

---

### Bundled MCP Server: Google Search (Gemini Grounded Search)

Uderia ships with a ready-to-use MCP server for public internet search, located at `mcp_servers/google_search.py`. It uses Google's Gemini Grounded Search API to find current public information and return factual summaries with source citations.

**How to activate:**

1. **Import MCP Server** — Navigate to Anatomy → MCP Servers → Import and paste the following Claude Desktop configuration:
   ```json
   {
     "mcpServers": {
       "Google Search": {
         "command": "python",
         "args": ["/app/mcp_servers/google_search.py"],
         "env": {"GEMINI_API_KEY": "your-gemini-api-key"}
       }
     }
   }
   ```
   Replace `/app/mcp_servers/...` with the actual path if running outside Docker.

2. **Link to a Profile** — Create or edit a profile (e.g., `tool_enabled` type) and select "Google Search" as its MCP Server.

3. **Use** — The `external_search` tool is now available. Queries routed to this profile will search the public internet via Gemini and return results with citations.

Each user provides their own Gemini API key through the `env` field, enabling per-user authentication without shared credentials.

---

## 🧩 Open and Extensible: Built to Be Built On

Open at every boundary, extensible without forking. What an organisation already runs stays in place; Uderia connects to it through open standards and grows through documented contracts.

* **Source-available code and open standards**:
  - The public core is licensed under the Functional Source License (FSL-1.1-ALv2): use, change and redistribute it for anything but a competing commercial product or service, and each release becomes Apache 2.0 two years after it ships; the code of the framework, its runtime and its trust model is in it, with the default prompts in plain text (the split with the enterprise package, see below)
  - Model Context Protocol in both directions: agents reach data through MCP servers, and every agent is callable as an MCP tool ([Uderia as an MCP server →](../Architecture/MCP_SERVER_ARCHITECTURE.md))
  - OAuth, OIDC and SAML for identity; Ed25519 signatures anyone can verify offline; OpenTelemetry-shaped quality spans through a pluggable exporter (the span shape, not yet the OTLP wire protocol)
  - Knowledge graphs exported in W3C formats (RDF, OWL, SKOS, PROV-O, DCAT, Data Cube), consumed by third-party tools and re-imported without loss ([proof pack T4 →](../proof-packs/T4_OPEN_STANDARDS.md))
  - [Open standards architecture →](../Architecture/OPEN_STANDARDS_ARCHITECTURE.md)

* **Sixteen extension points, each with a documented contract**: components (including your own), skills, extensions, repository templates, knowledge sources, knowledge-graph constructors, context-window modules, execution engines, model providers, vector and graph stores, data sources through MCP with custom objects, connectors, assurance modules, agent packs, branding, and integration over REST and MCP. What each adds and where its contract lives: [Value Proposition: what the framework is →](../value/VALUE_PROPOSITION.md#what-the-framework-is)

* **The split, stated plainly**: the public core ([uderia-core](https://github.com/rgeissen/uderia-core), FSL-1.1-ALv2) carries the framework, its runtime, its trust model, sixteen of the nineteen components, every storage, vector and graph backend, the Teradata depth and the account connectors; the enterprise package ([uderia-enterprise](https://github.com/rgeissen/uderia-enterprise), under the Uderia Enterprise Licence) installs beside it through the plug-in seam and adds the three OBDA planes, identity federation (OIDC, SAML 2.0), external key managers, white-label branding, the system-prompt editor and the acts of signing. A licence key may name the parts it entitles.

