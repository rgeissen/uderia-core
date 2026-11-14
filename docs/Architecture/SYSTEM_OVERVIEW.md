# 🏗️ System Architecture & Deployment

## System Overview

The Uderia Platform is built on a modern, asynchronous client-server architecture with four primary layers:

```
┌──────────┐      ┌─────────────┐      ┌──────────┐      ┌─────┐      ┌─────────┐
│  Browser │ SSE  │   Backend   │ HTTP │   LLM    │ HTTP │ MCP │ SQL  │ Data    │
│   (UI)   │◄────►│   (Quart)   │─────►│ Provider │      │ Svr │─────►│ Source  │
└──────────┘      └─────────────┘      └──────────┘      └─────┘      └─────────┘
```

**Communication Flow:**
1. User sends query via browser → Backend receives via REST/SSE
2. Backend orchestrates → LLM generates plan
3. LLM requests tools → MCP Server executes against data source
4. Results flow back → Backend formats → Browser renders in real-time

## Component Layers

### Frontend Layer
- **Technology:** Single-page app (Vanilla JS, Tailwind CSS, HTML)
- **Communication:** REST API for requests, Server-Sent Events (SSE) for real-time updates
- **Key Features:** Live status monitoring, session management, context controls
- **State Management:** Browser localStorage (respects server persistence settings)
- **User-facing messages:** one of three banner systems (app, conversation, maintenance) plus confirm/prompt dialogs and panel notices — see [Banner Messaging](BANNER_MESSAGING_SYSTEM.md)

### Backend Layer
- **Technology:** Quart (async Python web framework)
- **Responsibilities:** 
  - Session management and user isolation
  - LLM orchestration with Fusion Optimizer engine
  - Configuration management and credential handling
  - RAG system integration
- **Key Modules:**
  - `api/` - REST endpoints and SSE handlers
  - `agent/` - Executor, Formatter, and planning logic
  - `llm/` - Multi-provider LLM connectors
  - `mcp_adapter/` - MCP protocol client
  - `core/` - Configuration, sessions, utilities
  - `connectors/` - Platform connector OAuth modules (Google, Microsoft 365, Slack)
  - `vectorstore/` - Pluggable vector store backends (ChromaDB, Teradata EVS, Qdrant)

### LLM Integration Layer
- **Supported Providers:** Google (Gemini), Anthropic (Claude), OpenAI, Azure OpenAI, AWS Bedrock, OpenRouter (100+ models), Friendli.AI, xAI (Grok), Celeris, MLX (Apple Silicon — [setup](../MLX/README.md)), Ollama
- **Authentication:** Dynamic credential handling per session
- **Protocol:** REST API calls with structured prompts (system + user + tools)
- **Details:** [LLM Provider Architecture](LLM_PROVIDER_ARCHITECTURE.md) · [Prompt Cache](PROMPT_CACHE_ARCHITECTURE.md)

### MCP Integration Layer
- **Protocol:** Model Context Protocol - standardized tool/prompt/resource exposure
- **Transport Types:**
  - 🟠 **STDIO** — local servers via subprocess (npx, uvx, python)
  - 🔵 **Streamable HTTP** — remote servers by host, port and path (entries saved as `sse` or `http` connect through the same streamable-HTTP client). HTTPS endpoints are reached through a reverse proxy / local port (the client builds `http://host:port/path`)
- **Import Formats:** MCP Registry specification and Claude Desktop configuration
- **Security:** Credential passthrough, no credential storage in agent
- **Lifecycle Management:** Automatic process spawning and cleanup for STDIO servers
- **Several servers per agent:** see [Multi-Source Agents](MULTI_SOURCE_ARCHITECTURE.md); extending a server without forking it: `deploy/mcp/README.md`

## Data Flow & Session Management

**Authentication Flow:**
1. User logs in → Backend validates credentials
2. JWT token issued (24-hour expiry) → Stored in browser localStorage as `tda_auth_token`
3. All API requests include `Authorization: Bearer <token>` header
4. Token refreshed automatically or user re-authenticates

**Configuration Flow:**
1. User enters credentials (LLM + MCP) → Validated by backend
2. Credentials encrypted using Fernet → Stored per-user in `tda_auth.db`
3. MCP/LLM profiles created → Associated with user account
4. Configuration persists across sessions (user-specific)

**Query Execution Flow:**
1. User query → Backend authenticates JWT → Creates/loads session
2. Backend invokes Fusion Optimizer with context (conversation history + workflow summaries)
3. Optimizer generates strategic plan → Executes via LLM + MCP tools
4. Results streamed via SSE → UI updates in real-time
5. Session persisted with turn history and summaries

**Session Isolation:**
- Each user identified by database user ID (from JWT token)
- Sessions stored per user — `tda_sessions/<user_uuid>/<session_id>.json` with the file backend, or in PostgreSQL with the Postgres backend (see [Session Architecture](SESSION_ARCHITECTURE.md))
- User credentials isolated in encrypted database storage
- Multi-user support: Multiple users can access simultaneously with separate sessions

**Deployment Architectures:**

**Single-User (Development):**
```
Local Machine → Python Process → localhost:5050
```

**Multi-User (Production):**
```
Option 1: Load Balancer → Multiple Container Instances (port 5050, 5051, 5052...)

```

Several instances need the PostgreSQL session backend and Redis locks ([Sessions](SESSION_ARCHITECTURE.md)); the known bottlenecks and their status are in the [Scale Assessment](SCALE_ASSESSMENT.md).

**Security Considerations:**

- **Credentials:** LLM/MCP credentials never logged or exposed in UI
- **Isolation:** Session data segregated by user UUID
- **Transport:** HTTPS recommended for production (configure via reverse proxy)
