# 📖 User Guide

This comprehensive guide covers everything you need to know to use Uderia effectively, from basic operations to advanced features and automation.

---

## Getting Started

### Prerequisites Before First Use

⚠️ **Important:** Before you can start conversations with the agent, you must complete the initial configuration. Every agent needs:

1. **LLM Provider Configuration** - The AI model that powers the agent
2. **Profile Creation** - Makes the LLM (and, for data agents, an MCP server) a usable agent

An **MCP Server Connection** — where your data and tools live — is needed only for Optimize agents and for Ideate agents with tools.

Until a valid default profile exists, the welcome button reads **Configure Application** and opens Anatomy. Once it does, it reads **Start Reasoning**.

### Initial Configuration

Uderia uses a modern, modular configuration system that separates infrastructure (MCP Servers, LLM Providers) from usage patterns (Profiles). This architecture provides maximum flexibility for different use cases.

**Configuration Flow:** MCP Servers (data agents only) → LLM Providers → Profiles → Start Reasoning

#### Step 1: Configure MCP Servers

1. **Login:** Navigate to `http://localhost:5050` and login with your credentials.

2. **Navigate to Anatomy:** Click **Anatomy** in the left sidebar (star icon).

3. **MCP Servers Tab:** Select the "MCP Servers" tab, click **+ Add Server** and configure one or more MCP Server connections:
   - **Transport Type:** SSE or HTTP (a STDIO server can only be added by import, below)
   - **Server Name:** A friendly identifier for this server (e.g., "Production Database", "Dev Environment")
   - **Host:** The hostname or IP address of your MCP Server
   - **Port:** The port number (e.g., 8888)
   - **Path:** The endpoint path (e.g., /mcp)

4. **Save:** Click **Add** to save. You can configure multiple servers for different environments.

#### MCP Server Import Capability

Instead of manually configuring servers, you can import MCP server definitions from standardized configuration files. Uderia supports **two formats** with automatic detection:

**Supported Formats:**

1. **MCP Registry Format** - Official MCP server specification
2. **Claude Desktop Format** - Import servers from your Claude Desktop configuration

**How to Import:**

1. Navigate to **Anatomy → MCP Servers**
2. Click the **Import from server.json** button
3. Paste your server configuration JSON, or upload the `server.json` file
4. Click **Import Server**

The system automatically detects the format and imports all server configurations.

**Format 1: MCP Registry Specification**

The [official MCP registry format](https://github.com/modelcontextprotocol/registry) is accepted with package transports `sse`, `streamable-http` and `stdio` (and `remotes[]` entries of type `sse`):

```json
{
  "name": "io.example/my-server",
  "version": "1.0.0",
  "description": "Example MCP server",
  "packages": [
    {
      "transport": {
        "type": "sse",
        "url": "http://localhost:8000/sse"
      }
    }
  ]
}
```

**stdio Transport Example:** the command is the package `identifier` and its arguments are `args.positional`:

```json
{
  "name": "time-server",
  "version": "1.0.0",
  "packages": [
    {
      "identifier": "uvx",
      "args": { "positional": ["mcp-server-time", "--local-timezone=America/New_York"] },
      "transport": { "type": "stdio" }
    }
  ]
}
```

Import is the only way to add a STDIO server: the **+ Add Server** dialog offers SSE and HTTP only. For STDIO the Claude Desktop format below is usually the simpler choice.

**Format 2: Claude Desktop Configuration**

Import servers directly from your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone=America/New_York"],
      "env": {}
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/username/Documents"],
      "env": {}
    }
  }
}
```

## MCP Transport Types

The MCP client connects to a server in one of two ways:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           MCP TRANSPORT TYPES                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   🟠 STDIO                         🔵 Streamable HTTP                           │
│   ─────────                        ──────────────────                           │
│   Local subprocess                 Remote server by host, port and path         │
│   Development, local tooling       Production, shared and cloud servers         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 🟠 STDIO - Local Command Execution

**Best for: Development, local databases, file systems**

```json
{
  "mcpServers": {
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone=America/New_York"],
      "env": {}
    }
  }
}
```

**Characteristics:**
- Runs as a local subprocess (npx, uvx, python, node)
- Automatic process lifecycle management (spawn/cleanup)
- No network required - communicates via stdin/stdout
- Ideal for development and local tooling

**Common Use Cases:**
- Local file system access (`@modelcontextprotocol/server-filesystem`)
- Development databases
- Local AI model servers (Ollama integrations)
- CLI tool wrappers

---

### 🔵 Streamable HTTP - Remote Servers

**Best for: Cloud services, remote databases, shared production servers**

```json
{
  "name": "io.example/remote-server",
  "version": "1.0.0",
  "packages": [
    {
      "transport": {
        "type": "streamable-http",
        "url": "http://mcp.example.com:8888/mcp"
      }
    }
  ]
}
```

**Characteristics:**
- Servers added as SSE or HTTP, or imported as `sse` or `streamable-http`, are all connected with the MCP streamable-HTTP client at `http://<host>:<port><path>` (registry import does not accept a `"type": "http"` entry)
- Host, port and path are required; for an HTTPS-only endpoint, expose it through a local proxy or port
- Firewall-friendly and suited to servers shared by many users

**Common Use Cases:**
- Cloud-hosted databases (Teradata, Snowflake, BigQuery)
- Enterprise MCP servers behind a reverse proxy
- One server shared by several agents (see [Multi-Source Agents](../Architecture/MULTI_SOURCE_ARCHITECTURE.md))

To add a tool to a server without forking it (custom MCP objects mounted per endpoint), see `deploy/mcp/README.md`.

---

### Transport Selection Guide

| Scenario | Recommended Transport |
|----------|----------------------|
| Local development | 🟠 STDIO |
| Testing MCP servers | 🟠 STDIO |
| Production cloud database | 🔵 Streamable HTTP |
| Enterprise deployment | 🔵 Streamable HTTP |

**Import Benefits:**

- **Bulk import** multiple servers at once (Claude Desktop format)
- **Format auto-detection** - no need to specify which format you're using
- **Duplicate detection** - warns if server already exists
- **Validation** - checks for required fields before import
- **Server-side ID generation** - ensures unique server identifiers

**Tip:** You can find community MCP servers at the [MCP Registry](https://github.com/modelcontextprotocol/servers) and import them directly.

#### Step 2: Configure LLM Providers

1. **LLM Providers Tab:** Configure one or more LLM provider connections:
   - **Name:** A descriptive name (e.g., "Gemini Flash", "Claude Sonnet")
   - **Provider:** Select from Amazon (AWS Bedrock), Anthropic, Azure, Celeris, Friendli.AI, Google, MLX, Ollama, OpenAI, OpenRouter or xAI
   - **Model:** Choose a specific model from the provider
   - **Credentials:** Enter required authentication details:
     - **Cloud providers:** API Key
     - **Friendli.AI:** Personal Access Token, optional dedicated endpoint URL
     - **Celeris:** API Key, optional base URL
     - **Azure:** Endpoint URL, API Key, API Version, Deployment Name
     - **AWS:** Access Key ID, Secret Access Key, Region
     - **Ollama:** Host URL (e.g., `http://localhost:11434`)
     - **MLX:** Host URL (default `http://localhost:8080`)

2. **Fetch Models:** Click the refresh icon to retrieve available models from your provider.

3. **Save:** Click **+ Add Configuration** to open the dialog, and **Add** to save it. You can configure multiple LLM providers to compare performance.

#### Step 3: Create Profiles

Profiles combine an LLM Provider (and, where needed, an MCP Server) into named agents for different use cases.

1. **Agent Profiles Tab:** After configuring at least one LLM Provider (and an MCP Server for data agents), create profiles:
   - **Class:** Ideate (conversation), Focus (knowledge), Optimize (tools/data) or Coordinate (experts) — chosen by tab; only Optimize (and Ideate with tools) needs an MCP server
   - **Profile Name:** Descriptive name (e.g., "Production Analysis", "Cost-Optimized Research")
   - **Tag:** Short identifier for quick selection (e.g., "PROD", "COST") - used for temporary overrides
   - **MCP Server:** Select which MCP Server this profile uses
   - **LLM Provider:** Select which LLM configuration this profile uses
   - **Description:** Optional details about when to use this profile

2. **Active for Consumption:** Once the profile's tests pass, toggle it on to offer it for `@TAG` overrides and REST use (see below).

3. **Save:** Click **+ Add Profile** to open the dialog and **Save Profile** to create it. Make one profile the default for all queries with the star on its card.

#### Step 4: Start Reasoning

1. Navigate back to **Reasoning** using the left sidebar (chat icon).

2. Click the **Start Reasoning** button to activate your default profile.

3. The application will:
   - Validate your MCP Server connection
   - Authenticate with your LLM provider
   - Load all available tools, prompts, and resources from the MCP Server
   - Display them in the **Resource Panel** (the icon rail beside the chat: MCP Tools, MCP Prompts, Knowledge Graphs, Skills, Extensions, Components, Connectors, Context, Resources when the server offers any, Workspace)
   - Enable the chat input for you to start asking questions

**✅ You're Ready!** Once you see the capabilities loaded and the chat input is active, you can start interacting with your data through natural language queries.

**Example First Query:** `"What databases are available?"` or `"Show me all tables in the DEMO_DB database"`

---

## Using the Interface

### Navigation and Panel Structure

The application provides a multi-panel interface accessible through the left sidebar navigation. Click the hamburger menu (☰) in the top-left to expand/collapse the sidebar.

**The sidebar, top to bottom** (it is grouped the way an agent is built — composed, trusted, observed — then the shared faculties, the exchange and the *Platform* group):

1. **Reasoning** — conversations with your agents
2. *The Agent*
   - **Anatomy** — configuration & setup: agent profiles, LLMs, MCP servers, stores, memory, skills, extensions, agent packs
   - **Trust** — the Trust Circle, trust posture and assurance
   - **Awareness** — sessions & performance
   - **Ontology** — the estate-wide semantic map of your agents (enterprise installs only)
3. *Faculties*
   - **Intelligence** — knowledge, planners, graphs & skills
   - **Components** — the agent's instruments (charts, canvas, dashboards, flows, scheduler, …) and connectors
4. *Exchange*
   - **[Marketplace](../capabilities/MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing)** — share and subscribe
5. *Platform*
   - **Administration** — users, governance & operations (admin only)

Which views each user tier sees is set by an administrator in **Administration → Panes**.

### Appearance & Themes

Customize the application's visual appearance to suit your preferences or working environment. Access theme settings from the user dropdown menu in the top-right corner.

**Available Themes:**

* **Legacy Theme:** Classic dark gray color palette with warm tones. Best for users familiar with the original interface.

* **Modern Theme:** Contemporary dark blue/slate design with glass-panel effects and refined depth. The default theme for new installations.

* **Light Theme:** Clean, professional light mode with high contrast for well-lit environments or accessibility preferences.

Your theme is saved in this browser and persists across sessions. Without a choice of your own, the administrator's default theme (or Modern) applies.

### Reasoning

When you select **Reasoning**, the interface is organized into several key areas:

* **Session History (Left):** Lists all your conversation sessions. Click to switch between sessions; start a new conversation with the New-session button in the conversation header.

* **Resource Panel:** an icon rail opening a drawer per resource — MCP Tools (e.g. `base_tableList`), MCP Prompts (pre-defined multi-step workflows such as `qlty_tableQualityReport`), Knowledge Graphs, Skills, Extensions, Components, Connectors, Context, Resources (shown when the MCP server offers resources) and the Workspace. A badge reads *enabled/available*; Skills adds the active count (*active/enabled/available*), and the Workspace reads *items · sections*. Which tools the model actually sees each turn is the agent's *Tools in context* setting ([Tool Surface →](../Architecture/TOOL_SURFACE_ARCHITECTURE.md)).

* **Chat Window (Center):** Where your conversation with the agent appears, showing both user queries and agent responses.

* **Chat Input (Bottom):** Type your questions in natural language here. Supports profile overrides and voice input.

* **Live Status Panel (Right):** The transparency window showing real-time logs of the agent's internal reasoning, tool executions, and raw data responses.

### Trust

**Trust** shows how far each agent can be trusted — its configuration and how it actually behaved:

* **Trust Circle** — every assurance station, read as *configuration · execution*
* **Trust Posture** — the posture each agent runs under
* **Agent Roster** — every agent with its verdict, the act that is owed, and the runs behind it
* **Assurance Inbox** — findings and held answers waiting for a decision
* **Evidence**, **Experiments**, **Judge Calibration** — the evidence per result, A/B experiments, and the calibration of the LLM judges

See the operator guide: [Agent Trust & Assurance](../manuals/AGENT_TRUST_AND_ASSURANCE.md).

### Awareness

**Awareness** shows your agents' session operations (**My Performance**, or **System Performance** for administrators):

* **KPIs:** total sessions, total tokens, success rate, estimated cost, velocity (24h)
* **Model Distribution** and the most token-intensive sessions and questions
* **Session Roster:** every session — from the UI, the REST API or the scheduler — searchable; open one for its turns, tokens, planner examples and timeline, export it as JSON or generate a report

It is especially valuable for monitoring REST API-triggered workloads and debugging complex agent behaviors. [Sessions →](../Architecture/SESSION_ARCHITECTURE.md) · [Agent Ops & Eval →](../Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md)

### Intelligence

**Intelligence** is your control center for the deployment's knowledge — Planner Repositories, Knowledge Repositories and Knowledge Graphs:

* **Planner Repositories:**
  - Execution strategies and planning patterns from successful agent interactions
  - View, create, update, or delete collections
  - Inspect individual execution traces and their embeddings
  - Bulk import/export collection data
  - Automatically populated from agent executions or via Planner Repository Constructors
  - Each new Optimize agent learns into its own planner repository
  
* **Knowledge Repositories:**
  - General documents and reference materials for planning context
  - Upload PDF, TXT, DOCX, MD files with configurable chunking strategies
  - View document metadata, chunk counts, and storage details
  - Delete documents or entire collections
  - Search within Knowledge repositories using semantic similarity
  - **Fully integrated** with planner for domain context retrieval
  
* **Planner Repository Constructors:**
  - Browse installed constructors (templates for building Planner Repositories)
  - Configure constructor parameters and populate collections
  - LLM-assisted auto-generation from database schemas or documentation
  - Reload constructors without a restart and validate a new plugin before installing it (Administration → Templates)

* **Knowledge Graphs:**
  - Build, version, inspect and assign the agent's knowledge graphs (constructors, Graph Inspector)

* **Content Operations:**
  - Generate contextual questions for documents
  - Populate collections with new content via manual or automated workflows
  - Provide feedback on RAG retrieval quality
  - Preview document chunking before committing
  - Clean orphaned or invalid entries

For detailed RAG workflows and maintenance procedures, see the [RAG Maintenance Guide](../../maintenance/RAG_MAINTENANCE_GUIDE.md), the [RAG System guide](../RAG/RAG.md) and the [Planner Repository Constructors guide](../RAG_Templates/README.md). Architecture: [Knowledge Retrieval →](../Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md) · [Knowledge Graph →](../Architecture/KNOWLEDGE_GRAPH_ARCHITECTURE.md)

### Components

**Components** holds the agent's instruments:

* **User Interface:** the component library — all 19 builtin components: Agent Memory, Canvas, Compute with Lineage, Context Window Manager, Dashboard, Data Visualization, Database Navigator, EXPLAIN Plan, Files, Flow Orchestrator, Knowledge Graph, Needs Input, OBDA-ACT, OBDA-MODEL, OBDA-READ, Platform, Skill Library, Task Scheduler and Tool Finder. Enable a component for an agent here (or in the agent's profile under Capabilities); each action component brings its companion skill (Files only when its agent tool is switched on; Skill Library, Tool Finder and Needs Input have none). A component may carry tabs of its own: here **Files** adds a *Files* tab (your files), and its administration tabs — *Stores*, *Keys*, *Policy*, *Capacity* — are under **Administration → Components → Files**.
* **Connectors:** admin-governed capability tools (browser, files, shell, web search, Google, Microsoft 365, Slack) and the shared **Database Connection** used by the canvas, the Database Navigator and PDM deployment.

[Architecture →](../Architecture/COMPONENT_ARCHITECTURE.md) · [Chart](../Architecture/CHART_ARCHITECTURE.md) · [EXPLAIN Plan](../Architecture/EXPLAIN_PLAN_ARCHITECTURE.md) · [Agent Memory](../Architecture/AGENT_MEMORY_ARCHITECTURE.md) · [Files](../Architecture/FILES_ARCHITECTURE.md) · [OBDA-READ](../Architecture/OBDA_ARCHITECTURE.md) · [OBDA-MODEL · OBDA-ACT](../Architecture/ACT_ARCHITECTURE.md) · [Connectors](../Architecture/CONNECTOR_ARCHITECTURE.md)

### Marketplace

The **[Intelligence Marketplace](../capabilities/MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing)** enables discovery and sharing of AI assets (Planner Repositories, Knowledge Repositories, Agent Packs, Skills, Extensions, Knowledge Graphs and Component instances). Browse public collections, subscribe for reference-based access, fork for customization, rate and review, and publish your own expertise. The marketplace transforms isolated knowledge into a collaborative ecosystem that reduces costs and improves quality through community validation.

For detailed marketplace features, product types, and workflows, see the [Intelligence Marketplace](../capabilities/MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) section in Core Concepts, the [Marketplace Architecture](../Architecture/MARKETPLACE_ARCHITECTURE.md) and the [publisher guide](../Marketplace/README.md).

### Anatomy

**Anatomy** is where you configure all external connections and compose your agents. This is typically the first view you'll use when setting up the application:

* **Anatomy Tab:** the live anatomy of the selected agent — what it is composed of and how each part is wired

* **MCP Servers Tab:**
  - Configure Model Context Protocol server connections
  - Test server connectivity and capability discovery
  - Manage server-specific settings and parameters

* **Vector Stores / Graph Stores Tabs:**
  - Configure the vector-store backends (ChromaDB, Teradata, Qdrant) and graph-store backends (SQLite, Neo4j) that repositories and knowledge graphs live on ([Vector Store →](../Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md) · [KG Store →](../Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md))

* **LLMs Tab:**
  - Add connections to Google, Anthropic, OpenAI, Azure, AWS Bedrock, OpenRouter, Friendli.AI, xAI, Celeris, MLX or Ollama
  - Configure API keys, endpoints, and authentication
  - Test model availability and fetch model lists
  - Compare multiple providers side-by-side

* **Memory Tab:**
  - Context window types (Balanced, Knowledge-Heavy, Conversation-First, Token-Efficient), installed context modules and utilization analytics ([Context Window →](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md))
  - Agent Memory ([Agent Memory →](../Architecture/AGENT_MEMORY_ARCHITECTURE.md))

* **Skills / Extensions Tabs:**
  - Manage the skills injected before a query and the extensions that post-process an answer ([Skills →](../Architecture/SKILL_ARCHITECTURE.md) · [Extensions →](../Architecture/EXTENSION_ARCHITECTURE.md))

* **Agent Profiles Tab:**
  - Create named profiles of four classes (Ideate, Focus, Optimize, Coordinate) combining MCP servers with LLM providers
  - Set default profiles and configure profile tags for quick switching
  - Enable/disable profiles for temporary override selection
  - Manage profile-specific settings and descriptions

* **Agent Packs Tab:**
  - Import, export and publish portable bundles of profiles, collections, skills and knowledge graphs; govern their release lifecycle ([Pack Governance →](../Architecture/PACK_GOVERNANCE_ARCHITECTURE.md))

* **Advanced Tab:**
  - **Access Token Management:** Create, view, and revoke long-lived API tokens for REST API automation
  - **Token Security:** Tokens are shown only once at creation and stored as SHA256 hashes
  - **Usage Tracking:** Monitor token usage with last used timestamps, use counts, and IP addresses
  - **Audit Trail:** Revoked tokens remain visible with revocation dates for compliance and forensics

All credential data is encrypted using Fernet encryption and stored securely in the user database.

### Administration

**Administration** provides deployment-level management (visible only to admin users), one tab each:

* **Users** — create, modify and deactivate accounts; assign tiers; reset passwords
* **Panes** — which user tiers see each view in the sidebar
* **Features** — feature access per tier
* **Quotas** — consumption profiles and rate limits
* **Config** — application-wide settings: Features, AI & Knowledge, User Interface, Security, Trust (incl. the OBDA policy), Operations
* **Optimizer** — Fusion Optimizer settings
* **Components** — deployment-wide component governance
* **Cost** — model pricing and cost sync
* **SSO** — OIDC and SAML identity providers
* **Prompts** — the system prompt editor (PE/Enterprise)
* **Templates** — planner repository template plugins ([guide](../RAG_Templates/README.md))

Architecture: [Cost Tracking](../Architecture/COST_TRACKING_ARCHITECTURE.md) (Cost) · [Prompts](../Architecture/PROMPT_ARCHITECTURE.md) (Prompts) · [Security](../Architecture/SECURITY_ARCHITECTURE.md) (Users, Features, SSO)

### Quick Navigation Tips

* **Sidebar Toggle:** Click the hamburger menu (☰) to expand/collapse the navigation sidebar
* **View Switching:** Click any view in the sidebar to switch instantly
* **Multi-View Workflow:** Configure connections in Anatomy, then switch to Reasoning to start chatting
* **Monitoring While Working:** Keep Awareness open in a separate browser tab to monitor long-running tasks

**Example Workflow:**
1. **Anatomy** → Configure LLM providers and MCP servers → Create agent profiles
2. **[Marketplace](../capabilities/MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing)** → Browse and install AI assets for your domain
3. **Intelligence** → Populate knowledge collections with your knowledge base
4. **Reasoning** → Start chatting with the agent using your enriched context
5. **Awareness** → Monitor sessions and review execution logs
6. **Trust** → See how far each agent can be trusted, and act on what it needs

---

### Asking Questions

Simply type your request into the chat input at the bottom of the **Reasoning** view and press Enter.

* **Example:** `"What tables are in the DEMO_DB database?"`

The agent will analyze your request using your **default profile**, display its thought process in the **Live Status** panel, execute the necessary tool (e.g., `base_tableList`), and then present the final answer in the chat window.

### Temporary Profile Override

The profile system allows you to temporarily switch to a different LLM provider for a single query without changing your default configuration. This is powerful for:
- **Testing:** Compare how different LLMs handle the same question
- **Cost Optimization:** Use cheaper models for simple queries, premium models for complex analysis
- **Specialized Tasks:** Route specific query types to models optimized for that domain

**How to Use Profile Override:**

1. **Type `@` in the question box** - A dropdown appears showing all profiles marked as "Active for Consumption" (an agent can be activated once its tests pass)

2. **Select a profile** - The default profile appears first (non-selectable), followed by available alternatives:
   - Use **arrow keys** to navigate
   - Press **Tab** or **Enter** to select
   - Or **click** on a profile

3. **Profile badge appears** - A colored badge shows the active override profile with the provider color

4. **Type your question** - The query will be executed using the selected profile's LLM provider

5. **Remove override** - Click the **×** on the badge or press **Backspace** (when input is empty) to revert to the default profile

**Visual Indicators:**
- **Question Box Badge:** Shows the temporary override profile with provider-specific colors
- **Session Header:** Displays both the default profile (★ icon) and override profile (⚡ icon with subtle animation)
- **Color Coding:** Each LLM provider has a distinct color (Google=blue, Anthropic=purple, OpenAI=green, etc.)

**Example Workflow:**
```
1. Default: a Gemini profile
2. Type: @CLAUDE <Tab>
3. Badge shows: @CLAUDE (purple)
4. Ask: "Analyze the performance metrics"
5. Query uses Claude instead of Gemini
6. Click × to return to default
```

Over the REST API, select the agent with `profile_id` in the request body instead — see [Profile Override](../RestAPI/PROFILE_OVERRIDE_FEATURE.md).

### Using Prompts Manually

You can directly trigger a multi-step workflow without typing a complex request.

1. Open the **Resource Panel** and choose **MCP Prompts**.

2. Browse the categories and find the prompt you want to run (e.g., `base_tableBusinessDesc`).

3. Click on the prompt. A modal will appear asking for the required arguments (e.g., `db_name`, `table_name`).

4. Fill in the arguments and click **"Run Prompt"**.

The agent will execute the entire workflow and present a structured report.

### Customizing the Agent's Behavior

System prompts are edited in **Administration → Prompts** (part of the enterprise package: an Enterprise or Partner licence); per-agent prompt overrides are set on the agent profile. To test the raw intelligence of a model without the agent's tool-using logic, use an Ideate agent with no tools enabled. See [Prompt Architecture](../Architecture/PROMPT_ARCHITECTURE.md).

---

## Advanced Context Management

Uderia provides a sophisticated, budget-aware **Context Window Orchestrator** that automatically manages every token sent to the LLM — plus manual controls for fine-grained user intervention. Together, these features help you optimize costs, maximize accuracy, and maintain full control over agent behavior.

### Context Window Orchestrator

The orchestrator runs a **five-pass assembly pipeline** on every LLM call, ensuring optimal token utilization without manual intervention:

| Pass | Name | What It Does |
|------|------|-------------|
| 1 | **Resolve** | Activates every registered module (13 builtin), using your context window type's settings as overrides and each module's defaults otherwise |
| 2 | **Dynamic Adjustments** | Applies runtime rules — e.g., allocate full budget to tools on the first turn, transfer budget away from documents when none are attached |
| 3 | **Allocate & Assemble** | Distributes token budgets per module (with min/max constraints) and calls each module to contribute content |
| 3b | **Surplus Reallocation** | Redistributes unused budget from low-utilization modules to high-demand ones |
| 4 | **Condense** | If still over budget, compresses lowest-priority modules first (e.g., summarize conversation history, reduce tool definitions to names-only) |

**13 Pluggable Context Modules:**

| Module | Purpose |
|--------|---------|
| System Prompt | LLM behavioral instructions |
| Tool Definitions | MCP tool schemas (auto-condensed after first turn) |
| Conversation History | Budget-aware sliding window of chat history |
| RAG Context | Reports the planner's champion-case retrieval (planner-owned, 0 tokens in the module) |
| Knowledge Graph Context | Budget-managed KG subgraph enrichment |
| Knowledge Context | Knowledge repository documents |
| User Memory | Agent Memory recall about the user |
| Review Release | Originals a reviewer released into the conversation |
| Plan Hydration | Previous turn results injection (skip redundant tool calls) |
| Document Context | User-uploaded document text |
| Component Instructions | Chart/canvas rendering instructions |
| Workflow History | Cross-turn execution traces |
| Findings | The verified results of earlier turns (tool, statement, row count, columns, single-row values), re-read rather than recalled |

**Context Window Types** — Choose a preset or create your own via **Anatomy → Memory**:

| Type | Strategy |
|------|----------|
| **Balanced** (default) | Even distribution across all active modules |
| **Knowledge-Heavy** | Prioritizes knowledge retrieval for document-intensive workflows |
| **Conversation-First** | Maximizes conversation history for multi-turn dialogues |
| **Token-Efficient** | Minimal viable context for cost-sensitive workloads |

The admin UI provides a **live budget visualization bar**, **condensation order editor** (move modules up or down to set compression priority), and a **dynamic adjustment rule builder** for creating custom runtime rules.

**Utilization Analytics** — Navigate to **Anatomy → Memory → Utilization Analytics**, select a session, and see:
- Summary metrics (average/peak utilization, condensation events, turns analyzed)
- Per-turn stacked bar chart showing module-level token consumption
- Module utilization table with condensation frequency
- Dynamic adjustment firing log

Every LLM call emits a **context window snapshot** visible in the Live Status panel as a color-coded stacked bar chart, providing real-time observability into exactly how your token budget is being spent.

For full technical details, see the [**Context Window Architecture documentation**](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md).

### Understanding Context Elements

The agent's "memory" is composed of three synchronized histories managed by the orchestrator ([Session Architecture →](../Architecture/SESSION_ARCHITECTURE.md)):

1.  **LLM Conversation History (`chat_object`):** The raw, turn-by-turn dialogue between you and the agent. The **conversation_history** module manages this within its allocated token budget, applying a sliding window when the conversation exceeds its allocation.

2.  **Chat History (`session_history`):** Used exclusively for rendering the conversation in the user interface. It is **not** sent to the LLM for context.

3.  **Turn Summaries (`workflow_history`):** A structured summary of the agent's actions. For each turn, it includes the plan, tools executed, results, and the context window snapshot. The **workflow_history** module manages this within its budget allocation.

## Context Management Features

You have several ways to control the agent's context directly from the UI:

### Activate/Deactivate Turn Context

You can activate or deactivate the context of any individual turn by clicking on the small numbered badge that appears on the user ("U") and assistant ("A") avatars.

*   **Clicking a badge** will toggle the `isValid` status for that entire turn.
*   **Inactive turns** are visually dimmed and their conversational history is **completely excluded** from the context sent to the LLM. This is a powerful way to surgically remove parts of the conversation that might be confusing the agent.

### Purge Context

You can deactivate all previous turns at once by clicking the **CTX** dot in the status strip (click the status summary in the header to expand the strip; CTX sits beside the MCP and LLM dots).

*   **Clicking the dot** will prompt you for confirmation.
*   **Upon confirmation,** all past turns in the session will be marked as inactive (`isValid = false`), and the indicator will blink white three times. This effectively resets the agent's conversational and planning memory, forcing it to start fresh from your next query.

### Replay Original Query

You can re-execute the original query for any turn by **clicking and holding** the assistant ("A") avatar for that turn.

*   **Press and hold for 1.5 seconds.** A circular animation will appear to indicate the action.
*   Upon completion, the agent will re-run the original user query for that turn, generating a brand new plan. This is useful for retrying a failed turn or exploring an alternative approach.

## Context Modes

The agent provides two primary modes for handling conversational history, allowing you to control the context sent to the LLM for each query. You can see the current mode in the hint text below the chat input bar.

**Important Note:** In both modes, only turns that are currently **active** (`isValid = true`) are included in the context. Deactivated turns are completely ignored by the LLM and the planner.

### Full Session Context Mode (Default)

In this mode, the agent maintains a complete conversational memory. It sends the **LLM Conversation History (`chat_object`)** from all active turns with each new request.

*   **Best for:** Conversational queries, follow-up questions, and tasks that require the agent to remember the back-and-forth of the dialogue.
*   **Impact:** Uses more tokens, as the conversation history from all active turns is included in the context.

### Turn Summaries Mode

When activated, this mode disables the **LLM Conversation History**. The agent becomes conversationally "stateless" but still operates with full knowledge of its past actions from active turns.

*   **What it sends:**
    1.  The Current User Prompt.
    2.  The **Turn Summaries (`workflow_history`)** from all active turns.
    3.  The full System Prompt (including all available tools).
*   **Best for:** "One-shot" commands, saving tokens, or preventing a long, complex conversation from confusing the planner.
*   **How to activate:**
    *   **Hold `Alt`** while sending a message to use it for a single query.
    *   **Press `Shift` + `Alt`** to lock the mode on for subsequent queries.

---

## REST API Integration

The Uderia platform includes a powerful, asynchronous REST API to enable programmatic control, automation, and integration into larger enterprise workflows.

This API exposes the core functionalities of the agent, allowing developers to build custom applications, automate complex analytical tasks, and manage the agent's configuration without using the web interface.

**Authentication:**

The REST API uses **Bearer token authentication** for all protected endpoints. You have two authentication options:

1. **JWT Tokens (Web UI):** 24-hour tokens automatically issued when you log in to the web interface
2. **Access Tokens (REST API):** Long-lived tokens created in **Anatomy → Advanced** for automation

**Creating Access Tokens:**

1. Navigate to **Anatomy** → **Advanced**
2. Click **"Create Token"**
3. Provide a descriptive name (e.g., "Production Server", "CI/CD Pipeline")
4. Set expiration (default: 90 days, or never expires)
5. **Copy the token immediately** - it's shown only once!
6. Store securely (e.g., environment variables, secrets manager)

**Using Access Tokens:**

```bash
# Set your token as an environment variable
export TDA_ACCESS_TOKEN="tda_9DqZMBXh-OK4H4F7iI2t3EcGctldT-iX"

# Use with example scripts
cd docs/RestAPI/scripts && ./rest_run_query.sh "$TDA_ACCESS_TOKEN" "What tables exist?"   # the script calls ./rest_check_status.sh

# Or directly with curl: create a session, submit a query, poll the task
SESSION_ID=$(curl -s -X POST http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $TDA_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{}' | jq -r .session_id)
TASK_ID=$(curl -s -X POST http://localhost:5050/api/v1/sessions/$SESSION_ID/query \
  -H "Authorization: Bearer $TDA_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt": "What tables exist?", "profile_id": "<profile-id>"}' | jq -r .task_id)
curl -s http://localhost:5050/api/v1/tasks/$TASK_ID \
  -H "Authorization: Bearer $TDA_ACCESS_TOKEN"   # poll until status is complete | error | cancelled
```

`profile_id` is optional (the default agent is used without it) and is the only way to select an agent over REST — never put an `@TAG` in the prompt text.

**Token Management:**
- View all tokens and usage statistics in **Advanced Settings**
- Revoke tokens immediately (soft-delete preserves audit trail)
- Monitor token usage: last used timestamp, use count, IP address
- Tokens are SHA256 hashed in database for security

**Important Notes:**
*   Example scripts (`rest_run_query.sh`, `rest_check_status.sh`) now require an access token as the first argument
*   The `rest_run_query.sh` script can optionally accept a `--session-id` to run a query in an existing session
*   Example scripts support a `--verbose` flag - by default they output only JSON to `stdout`

## Key Capabilities

* **Asynchronous Architecture**: The API is built on a robust, task-based pattern. Long-running queries are handled as background jobs, preventing timeouts and allowing clients to poll for status and retrieve results when ready.

* **Programmatic Configuration**: Automate the entire application setup process through endpoints for LLM configurations (`/v1/llm/configurations`), MCP servers (`/v1/mcp/servers`) and agent profiles (`/v1/profiles`), making it ideal for CI/CD pipelines and scripted deployments.

* **Full Agent Functionality**: Create sessions and submit natural language queries or execute pre-defined prompts programmatically, receiving the same rich, structured JSON output as the web UI.

For complete technical details, endpoint definitions, and cURL examples, please see the full documentation:
[**REST API Documentation (docs/RestAPI/restAPI.md)**](../RestAPI/restAPI.md) · [Profile override](../RestAPI/PROFILE_OVERRIDE_FEATURE.md) · [REST task model & sessions](../Architecture/SESSION_ARCHITECTURE.md)

---

## Real-Time Monitoring

The platform's UI serves as a powerful, real-time monitoring tool that provides full visibility into all agent workloads, regardless of whether they are initiated from the user interface or the REST API. This capability is particularly valuable for developers and administrators interacting with the agent programmatically.

When a task is triggered via a REST call, it is not a "black box" operation. Instead, the entire execution workflow is visualized in real-time within the UI's Live Status panel. This provides a granular, step-by-step view of the agent's process, including:

*   **Planner Activity:** See the strategic plan the agent creates to address the request.
*   **Tool Execution:** Watch as the agent executes specific tools and gathers data.
*   **Response Synthesis:** Observe the final phase where the agent synthesizes the gathered information into a coherent answer.

This gives REST API interactions the same transparency as interactive ones, offering a "glass box" view into the agent's operations. The key benefit is that you can trigger a complex workflow through a single API call and then use the UI to visually monitor its progress, understand how it's being executed, and immediately diagnose any issues that may arise. This turns the UI into an essential tool for the development, debugging, and monitoring of any integration with Uderia.

---

## Operationalization

### From Interactive UI to Automated REST API

The interface is one client of a REST-driven engine: the conversation you have in it and the request you send from code run through the same engine, with the same agent. So the move from interactive development to an automated workflow is not a rebuild. You shape the agent and the question in the interface, then run them from code. Two things travel: the **question** (Steps 1–4 below) and the **agent's setup** ([Agent setup as code](#agent-setup-as-code)).

**Step 1: Develop and Refine in the UI**

The primary development environment is the web-based UI. Here, you can:
*   **Prototype Workflows:** Engage in a dialogue with the agent to build out your desired sequence of actions.
*   **Test and Debug:** Interactively test the agent's understanding and execution of your requests. The real-time feedback and detailed status updates in the UI are invaluable for debugging and refining your prompts.
*   **Validate Outcomes:** Ensure the agent produces the correct results and handles edge cases appropriately before moving to automation.

**Step 2: Isolate the Core Workflow Requests**

Once you have a conversation that successfully executes your desired workflow, you can identify the key session turns that drive the process. These are the prompts you will use to build your REST API requests. The UI helps you distill a complex interaction into a series of precise, automatable commands.

**Step 3: Automate via the REST API**

With your workflow defined, you can transition to the REST API for operational use cases. This is done by sending your prompts to the appropriate API endpoint using an access token for authentication.

**Create an Access Token:**
1. Navigate to **Anatomy** → **Advanced** → **Access Token Management**
2. Click **"Create Token"** and provide a name (e.g., "Production Automation")
3. Copy the token immediately (shown only once!)
4. Store securely in your environment or secrets manager

*   **Example `curl` command:**

```bash
# Set your access token
export TDA_TOKEN="tda_9DqZMBXh-OK4H4F7iI2t3EcGctldT-iX"

# Execute query in a session
curl -X POST http://localhost:5050/api/v1/sessions/{session_id}/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TDA_TOKEN" \
  -d '{
    "prompt": "Your refined prompt from the UI",
    "profile_id": "<optional profile id>"
  }'
```

Select a non-default agent with `profile_id` — never put `@TAG` in the prompt on REST (the REST path does not strip it, so it pollutes retrieval and the stored question). The agent must be Active for Consumption (otherwise `403 profile_not_activated`). Poll `GET /api/v1/tasks/<task_id>` until `status` is `complete`, `error` or `cancelled`.

This allows you to integrate Uderia into larger automated systems, CI/CD pipelines, or other applications.

**Production Workflow Automation:**

For more advanced orchestration and scheduling, Uderia integrates with multiple workflow automation platforms:

* **Apache Airflow**: Enterprise batch processing with DAG-based orchestration. Detailed documentation and example DAGs in the [Airflow Integration Guide (docs/Airflow/Airflow.md)](../Airflow/Airflow.md).

* **n8n**: Visual node-based workflow builder for event-driven automation. Three production-ready templates (Simple Query, Scheduled Reports, Slack Integration) with comprehensive deployment guides in the [n8n Integration Guide (docs/n8n/README.md)](../n8n/README.md).

* **Flowise**: Low-code chatbot and workflow development. Pre-built agent flows with visual designer in the [Flowise Integration Guide (docs/Flowise/Flowise.md)](../Flowise/Flowise.md).

**Step 4: Real-Time Monitoring of REST-driven Workflows**

A key feature of Uderia is the ability to monitor REST-initiated tasks in real-time through the UI. When a workflow is triggered via the API, the UI (if viewing the corresponding session) will display:

*   The incoming request, flagged with a "Rest Call" tag.
*   The complete sequence of agent thoughts, plans, and tool executions as they happen.
*   Live status updates, providing the same level of transparency as if you were interacting with the agent directly in the UI.

This hybrid approach gives you the best of both worlds: the automation and scalability of a REST API, combined with the rich, real-time monitoring and debugging capabilities of the interactive UI. It provides crucial visibility into your operationalized data workflows.

### Agent setup as code

Everything you configure in the interface is reachable through the same API, so an agent's setup can be scripted, reviewed and reproduced like any other code:

*   **Create and change it over REST.** Agents (`/api/v1/profiles`), LLM configurations (`/api/v1/llm/configurations`) and MCP servers (`/api/v1/mcp/servers`) are created and updated through the routes the interface itself calls. Every route describes its effect, scope, tier and arguments in the generated [operations catalog](../RestAPI/PLATFORM_OPERATIONS.md). `scripts/platform_expert_agent.py` provisions an agent this way, over REST alone.
*   **Move it as one artefact.** A finished setup exports as an agent pack (`POST /api/v1/agent-packs/export`): its agents, knowledge, graphs, skills and trust posture in one file. Importing the pack (`POST /api/v1/agent-packs/import`) recreates the setup in another deployment; `conflict_strategy=update_in_place` updates an installed pack without breaking its subscribers ([REST reference](../RestAPI/restAPI.md)).
*   **Credentials stay a person's act.** Entering an API key or a database password, activating an agent for REST use and signing it are deliberately not scriptable by an agent; a person does them once, in the interface or with their own token.

### Uderia as an MCP Server

Beyond connecting *to* MCP servers as a client, Uderia can itself be exposed *as* an MCP server — letting any external MCP client (Claude Desktop, Cursor, or another agent) call a governed Uderia profile as a tool. What makes it useful is **trust**: the `ask_profile` tool runs the full execution pipeline, so the answer it returns is **grounding-gated and guardrailed** before it ever leaves the building — a downstream agent gets a checked answer, not raw model output.

```bash
# Launch the MCP server (STDIO is the default transport; --transport http also supported)
export UDERIA_ACCESS_TOKEN="tda_…"        # a Uderia 90-day access token
export UDERIA_BASE_URL="http://localhost:5050"
python -m trusted_data_agent.mcp_server --profile developer
```

**Governance is enforced server-side** by the REST layer — the token's user, profile/component access, consumption limits, and per-profile grounding/guardrail modes all apply; the MCP server itself holds no policy. A blocked turn is surfaced so a caller can distinguish a refused answer from a real one.

**Consumer-tier tool segmentation (hard gate):** `--profile user|developer|admin|all` selects the tool surface, clamped to `min(launch profile, the token's actual tier)` *before any tool is served* and failing safe to `user` (least privilege). A user-tier token can never see developer or admin tools, regardless of launch flag.

| Tier | Tools exposed |
|---|---|
| **user** | `ask_profile`, `list_profiles`, `list_data_products`, `query_data_product` |
| **developer** | + `list_knowledge_graphs`, `list_actions`, `perform_action`, `list_collections`, `list_sessions`, `get_my_usage` |
| **admin** | + `system_usage`, `list_user_usage` |

`--transport http` serves the same catalog on `--host` / `--port` (default `127.0.0.1:5055`).

See [MCP Server Architecture](../Architecture/MCP_SERVER_ARCHITECTURE.md) and the [REST→MCP tier matrix](../RestAPI/MCP_TIER_MATRIX.md).

---

## Troubleshooting

* **`ModuleNotFoundError`:** This error almost always means you are either (1) not in the project's root directory, or (2) you have not run `pip install -e .` successfully in your active virtual environment.

* **Connection Errors:** Double-check all host, port, path, and API key information. Ensure no firewalls are blocking the connection. If you receive an API key error, verify that the key is correct and has permissions for the model you selected.

* **"Failed to fetch models":** This usually indicates an invalid API key, an incorrect Ollama host, or a network issue preventing connection to the provider's API.

* **AWS Bedrock Errors:**

  * Ensure your AWS credentials have the necessary IAM permissions (`bedrock:ListFoundationModels`, `bedrock:ListInferenceProfiles`, `bedrock-runtime:InvokeModel`).

  * Verify that the selected model is enabled for access in the AWS Bedrock console for your specified region.
