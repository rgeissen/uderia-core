# Proof Pack — P6: Sovereignty / Hybrid Deployment

**What this proves:** every pluggable layer (LLM inference, embedding, vector store, graph store, data source, auth/state) has a **fully-local option**, so a **100% air-gapped** deployment with **zero external network calls** is a *configuration choice*, not a special build. Uderia owns no storage and no model: select the local option at every layer (Ollama/MLX + ChromaDB + SQLite/NetworkX + local MCP + local `tda_*` state), launch `--offline --nogitcall` bound to `127.0.0.1`, and the deployment makes **no outbound network calls** beyond the operator's own infrastructure.
**What you can check:** the deploy-anywhere matrix below and the offline start.
**Status:** ✅ Built · matrix below · Last updated 2026-06-28

---

## 1. The headline deliverable — Deploy-Anywhere Matrix

Every layer has a **fully-local / air-gapped** column. Selecting the left column end-to-end yields a zero-external-dependency install.

| Layer | **Fully-local / air-gapped** | On-prem enterprise | Cloud |
|---|---|---|---|
| **LLM inference** | **Ollama** (`localhost:11434`) · **MLX** (Apple Silicon, `localhost:8080`) | Friendli dedicated endpoint · Bedrock/Azure in customer tenant | Google · Anthropic · OpenAI · OpenRouter · xAI · Friendli serverless |
| **Embedding** | **SentenceTransformer, client-side in-process** (`all-MiniLM-L6-v2`, local HF cache; `--offline`) | Teradata server-side (Bedrock/Azure) | hosted via Teradata cloud |
| **Vector store** | **ChromaDB** (embedded, local file) · Qdrant (self-hosted) | Teradata (on-prem) · Qdrant (self-hosted) | Qdrant Cloud · Teradata VantageCloud |
| **KG store** | **SQLite + NetworkX** (zero-dep, local file) · Neo4j (self-hosted) | Neo4j (self-hosted) | Neo4j Aura |
| **Data source / MCP** | **Local MCP (uvx STDIO)** · local SQLite/Postgres/MySQL; **credential passthrough — no server-side storage** | customer-run Teradata / enterprise DB via MCP | any cloud DB via MCP (customer-owned) |
| **Auth / state** | **`tda_auth.db` (local SQLite)** · `tda_sessions/` files · `tda_keys/` · Fernet-encrypted creds | same, on customer hardware | same (no managed cloud state) |

**Proof of the headline claim:** the entire left column — Ollama/MLX + ChromaDB + SQLite/NetworkX + local MCP/SQLite + local `tda_*` state — makes **zero external network calls** when launched with `--offline --nogitcall` on `127.0.0.1`. There is no mandatory SaaS, no runtime license phone-home (the license is a local file `tda_keys/license.key`), and no Uderia-hosted data plane.

---

## 2. LLM inference — two fully-local options

Single source-of-truth factory `llm/client_factory.py:create_llm_client()` (`:15`), dispatched by provider. The two **local** options need no API key and contact no external host:

- **Ollama** (`:95-98`) — `OllamaClient(host=...)` defaults to `http://localhost:11434` (`handler.py:36-44`); a connection error says "Could not connect to Ollama server" — it talks only to the configured local host. *Local, offline.*
- **MLX** (`:148-166`) — OpenAI-compatible local server, `base_url=f"{mlx_host}/v1"` (default `localhost:8080`), dummy `api_key="mlx"`; validation hits the **local** `/v1/models`. Tool-calling enabled by default.

The eight cloud providers (Google, Anthropic, OpenAI, Azure, Bedrock, Friendli, OpenRouter, xAI) are configured per-user with Fernet-encrypted credentials — they are *options*, not requirements. Friendli additionally supports a **dedicated endpoint URL** (BYO inference endpoint).

---

## 3. Embedding, vector & KG stores — local at every layer

**Embedding (air-gap-critical):** the default `SentenceTransformerProvider` (`vectorstore/embedding_providers.py:57-90`) embeds **client-side, in-process** (`all-MiniLM-L6-v2`), cached per-process from the local HuggingFace cache. `--offline` (`main.py:451-453`) sets `HF_HUB_OFFLINE=1` so even the model load is local-cache-only — no embedding API call ever leaves the process.

**Vector store:**
- **ChromaDB** (default) — `PersistentClient` over a local `persist_directory` (`chromadb_backend.py:54-66`); ships in `requirements.txt`; client-side embedding → fully self-contained.
- Qdrant — `url` is configurable, so it points at a self-hosted instance or Qdrant Cloud (`qdrant_backend.py:74`).
- Teradata — on-prem or cloud; the only **server-side-embedding** backend.

**KG store:**
- **SQLite + NetworkX** (default) — a local file via `sqlite_util.connect()` + pure-Python NetworkX; **zero external dependency** (`sqlite_backend.py:17-66`). The factory resolution chain **always terminates at local SQLite** (`kgstore/factory.py:90,126`) — a KG always has a working local backend with no config.
- Neo4j — self-hostable (Bolt to any host); package-absent path degrades gracefully (`neo4j_backend.py:47-49`).

---

## 4. Data sources & residency — BYO-data, all-local state

- **BYO-data via MCP**: the customer connects Uderia to *their own* MCP servers / databases with runtime capability discovery (`mcp_adapter/adapter.py`). **Credential passthrough — no server-side storage** (CLAUDE.md). A local MCP STDIO server runs in-process via `uvx`, needing no remote endpoint; local DB drivers ship for SQLite/Postgres/MySQL (`aiosqlite`/`asyncpg`/`aiomysql`). There is **no Uderia-hosted data dependency**.
- **All deployment state is local by default:**

| State | Location | Anchor |
|---|---|---|
| Auth / users / config / encrypted creds / profiles / prompts | `tda_auth.db` (local SQLite) | `auth/database.py:25` |
| Session data (conversation, workflow, llm history) | `tda_sessions/` (local files) | `core/session_manager.py:28` |
| Keys (license, public key, JWT secret) | `tda_keys/` (local files) | `auth/database.py:3022`; `Dockerfile:12` |
| Credential encryption | Fernet, per-user PBKDF2-SHA256 (100k iters, user_id salt) | `auth/encryption.py:15-60` |

- **No mandatory phone-home.** The only outbound calls are optional and disable-able: a GitHub star-count fetch (`--nogitcall`, `main.py:441`) and a HuggingFace model fetch (`--offline` → local cache only, `main.py:442,451`). The default bind is `127.0.0.1` (`main.py:432`) — local-only unless explicitly exposed.

**→ Fully air-gapped:** Ollama/MLX + ChromaDB + SQLite/NetworkX + local MCP/SQLite + `tda_*` state + `--offline --nogitcall` = **zero external network calls.**

---

## 5. Deployment topologies

| Topology | Detail | Anchor |
|---|---|---|
| Single-user local | `python -m trusted_data_agent.main`; binds `127.0.0.1:5050` by default | `main.py:428-440` |
| Multi-user Docker | `python:3.12-slim` container + volume mounts; binds `0.0.0.0:5050` | `Dockerfile:1-37`, `docker-compose.yml` |
| Load-balanced multi-container | multiple containers on ports 5050, 5051, 5052… | CLAUDE.md "Deployment Options" |

---

## 6. Reproduce it

```bash
# Launch fully offline, local-only (with Ollama/MLX + ChromaDB + SQLite KG configured)
python -m trusted_data_agent.main --offline --nogitcall --host 127.0.0.1

# Verify no mandatory external state: all platform data is local files
ls tda_auth.db tda_sessions/ tda_keys/
```

A network monitor on this configuration shows traffic only to the operator's own LLM host (`localhost` for Ollama/MLX) and the operator's own MCP/data sources — nothing else.

**Public artifacts:** [`docs/Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md`](../Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md), [`docs/Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md`](../Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md).

---

### Key code anchors
| Concern | File:line |
|---|---|
| LLM factory · Ollama · MLX | `llm/client_factory.py:15,95,148`; `llm/handler.py:36` |
| Client-side embedding | `vectorstore/embedding_providers.py:57-90` |
| ChromaDB local | `vectorstore/chromadb_backend.py:54-66` |
| SQLite KG + always-local fallback | `kgstore/sqlite_backend.py:17-66`; `kgstore/factory.py:90,126` |
| Local state / residency | `auth/database.py:25`; `core/session_manager.py:28`; `auth/encryption.py:15-60` |
| Air-gap flags | `main.py:432` (`--host`), `:441` (`--nogitcall`), `:442,451` (`--offline`) |
