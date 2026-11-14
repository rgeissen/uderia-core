# Uderia Platform - Comprehensive Scale Assessment

**Date:** March 2026 (updated June 2026 — WS1; status markers and file references re-verified against the code 2026-10-30)

> This is a point-in-time assessment: the findings below are kept as the record of what was measured in March 2026. Each finding carries a **Status** line where the code has since changed; the severity matrix's *Status* column is the current state. File references name symbols, not line numbers.
**Current capacity estimate:** ~1,000s concurrent agents (single server, after Phase 1). WS1 (Jun 2026) makes the session-storage tier horizontally scalable (Postgres backend) and removes the file re-serialize bottleneck — see [WS1 — Session Storage & Coordination Hardening](#ws1--session-storage--coordination-hardening-complete--jun-2026).

---

## Executive Summary

The platform has **14 critical bottleneck categories** across 6 architectural layers. The root causes are:

1. **SQLite as primary database** -- single-writer serialization
2. **File-based session storage** -- filesystem I/O saturation
3. **In-process-only state** -- all caches, locks, and queues are per-process Python objects
4. **Single-process event loop** -- no horizontal scaling capability
5. **Unbounded in-memory caches** -- no eviction policies anywhere
6. **No backpressure mechanisms** -- no circuit breakers, no request queuing *(circuit breakers since shipped — see #13)*

---

## Layer 1: Database (CRITICAL)

### 1.1 SQLite with StaticPool -- Single Connection Serialization

| Property | Current | Impact at Scale |
|----------|---------|-----------------|
| **Connection pool** | `StaticPool` (1 connection) | All DB ops serialize through 1 connection |
| **Concurrent writers** | 1 (SQLite limitation) | Write throughput: ~100 ops/sec max |
| **WAL mode** | Not enabled | Readers blocked during writes |
| **Connection per request** | `get_db_session()` creates new session each call | 3-5 DB sessions per agent request |

**File:** `src/trusted_data_agent/auth/database.py` (engine creation from `DATABASE_URL`)

**Status (Oct 2026):** RESOLVED as a pool problem, bounded by design as a write problem. A file SQLite now runs a `QueuePool` (`TDA_AUTH_DB_POOL_SIZE`, default 10; `StaticPool` only for `:memory:`) with `PRAGMA journal_mode=WAL` and `synchronous=NORMAL`. Every SQLite C-call in the process is serialized through ONE re-entrant lock and a write transaction holds it to its commit (`core/sqlite_util.py`, contract in [SQLITE_CONCURRENCY.md](SQLITE_CONCURRENCY.md)) — this removed a libsqlite3 VFS-mutex deadlock that froze the event loop, and makes SQLite's single-writer ceiling explicit rather than lifting it. The lift is `TDA_AUTH_DB_URL` pointing at PostgreSQL (real `QueuePool`, `TDA_AUTH_DB_POOL_SIZE` / `TDA_AUTH_DB_MAX_OVERFLOW` / `TDA_AUTH_DB_POOL_RECYCLE`); the auth-schema migration itself is still out of scope (see *Known follow-ons*).

**At 1K agents:** ~5,000 concurrent DB operations competing for 1 connection -- ~50s queue buildup per burst.
**At 1M agents:** Completely inoperable. ~10,000s (2.7 hours) queue wait.

**Missing optimizations:**
- No `PRAGMA journal_mode=WAL` (would enable concurrent reads during writes)
- No `PRAGMA synchronous=NORMAL`
- No `PRAGMA cache_size` tuning
- No `PRAGMA temp_store=MEMORY`

### 1.2 Collection Database -- No Connection Pooling

**File:** `src/trusted_data_agent/core/collection_db.py` (`CollectionDatabase`)

Every RAG collection operation creates a brand new SQLite connection. At scale, this creates connection storms and file descriptor exhaustion.

**Status (Oct 2026):** connections are opened through `sqlite_util.connect()` (WAL + `busy_timeout`, the process-wide serialization, connection reuse) — the dedicated pool of Phase 2 Item 7 was not built.

### 1.3 Consumption Enforcement -- N+1 Query Pattern

**File:** `src/trusted_data_agent/auth/consumption_enforcer.py` (`ConsumptionEnforcer.can_execute_prompt`)

Each enforcement check runs **5 sequential DB queries**:
1. Load User
2. Load ConsumptionProfile
3. Count hourly prompts (`func.count()` table scan)
4. Count daily prompts
5. Sum monthly token usage

**Status (WS1):** the hourly and daily counts are one query (`SUM(CASE …)`); the Redis-counter fast path (Phase 2 Item 6) was not built.

**At 1K agents:** 5,000 queries/sec just for enforcement.
**At 1M agents:** 5M queries/sec -- impossible on SQLite.

### Recommended Improvements -- Database Layer

| Scale Target | Action | Effort |
|-------------|--------|--------|
| **100s** | Enable WAL mode, tune PRAGMAs, add connection pooling for collection_db | 1-2 days |
| **1,000s** | Migrate to PostgreSQL with pgBouncer connection pooling | 1-2 weeks |
| **10,000s** | Read replicas for query-heavy paths (consumption checks, session listing) | 1 week |
| **100,000s+** | Shard user data by user_uuid, dedicated DB for token_usage analytics | 2-4 weeks |
| **1M+** | Consumption enforcement via Redis counters (INCR + TTL), not DB queries | 1 week |

---

## Layer 2: Session Management (CRITICAL)

### 2.1 File-Based Session Storage

**File:** `src/trusted_data_agent/core/session_backends/file_backend.py` (`FileSessionBackend`; originally inline in `session_manager.py`)

**Status (WS1):** the storage is behind `SessionPersistenceBackend`; `TDA_SESSION_BACKEND=postgres` stores one JSONB row per session. The file backend below remains the zero-config default and keeps this cost.

Sessions stored as `tda_sessions/{user_uuid}/{session_id}.json`. Every mutation requires:
1. Read entire JSON file from disk
2. Parse full JSON into Python dict
3. Modify in-memory
4. Re-serialize entire dict to JSON
5. Write to temp file
6. Atomic rename

**Session sizes:** Typical 500KB-5MB (chat history + workflow traces + execution data). Heavy sessions can reach 10MB+.

**At 1K agents:** 1,000 concurrent file read/write cycles -- filesystem I/O saturation on spinning disk, marginal on SSD.
**At 1M agents:** 1M files in nested directories -- filesystem metadata operations alone become bottleneck (inode lookups, directory scanning).

### 2.2 Session Listing -- O(n) Full Scan

**File:** `src/trusted_data_agent/core/session_manager.py` (`get_all_sessions`)

Listing sessions requires opening and parsing EVERY session file. No index, no metadata cache.

**Status:** RESOLVED — a backend-owned summary index (`<sessions_dir>/session_index.db` for the file backend, the `sessions` table for Postgres), readied at startup by `ensure_session_index_ready()`.

**At 100 sessions/user:** ~100 file opens + JSON parses per listing.
**At 10K sessions/user:** UI becomes unusable (seconds of latency).

### 2.3 In-Process Session Locks -- No Cross-Server Coordination

**File:** `src/trusted_data_agent/core/session_manager.py` (`_get_session_lock`, `_session_transaction`)

**Status (WS1):** the per-session write lock comes from `core/locks/` (`get_lock_provider()`: in-process default, Redis with `TDA_LOCK_BACKEND=redis`). The findings below describe the pre-WS1 state.

- Locks exist only within a single Python process
- Multiple server instances = independent lock dicts = no coordination
- Lock dict grows unbounded (never pruned for completed sessions)
- Lock creation itself is racy (check-then-create without atomicity)

### Recommended Improvements -- Session Layer

| Scale Target | Action | Effort |
|-------------|--------|--------|
| **100s** | Add session metadata index (SQLite table with id, name, created_at, updated_at) to avoid full-file scans | 2-3 days |
| **1,000s** | Move session hot data (active chat, tokens, status) to Redis; keep cold data on disk | 1-2 weeks |
| **10,000s** | Replace file storage entirely with PostgreSQL (JSONB) or dedicated document store | 2-3 weeks |
| **100,000s+** | Implement session sharding by user_uuid hash; add distributed locking (Redis SETNX) | 2 weeks |
| **1M+** | Event-sourced sessions (append-only log, materialized views) -- eliminates full re-serialization | 4-6 weeks |

---

## Layer 3: LLM & Agent Execution (CRITICAL)

### 3.1 No LLM Client Pooling

**File:** `src/trusted_data_agent/llm/client_factory.py` (`create_llm_client`)

Every `create_llm_client()` call creates a **new client instance** with its own httpx connection pool.

**Status:** RESOLVED — `get_or_create_llm_client()` pools clients (max 50, keyed by provider + model + credentials hash); `APP_STATE['llm_instance_pool']` is a `BoundedLRUCache` (`TDA_LLM_POOL_MAX`, 256).

**Memory per client:** ~500KB-1MB (httpx internals + SDK objects).
**At 1K agents:** 1,000 client objects = ~500MB-1GB.
**At 1M agents:** 1M clients = 500GB-1TB -- physically impossible.

### 3.2 Global APP_STATE -- Unbounded, Unprotected

**File:** `src/trusted_data_agent/core/config.py` (`APP_STATE`)

**Status:** lock protection (Phase 1) and bounded LRU caches for the load-growing sub-dicts (WS1). Per-turn `@TAG` overrides no longer mutate `APP_STATE` at all — they read a per-executor `ChainMap` overlay.

**Problems:**
- No locking on dict mutations -- race conditions under concurrent access
- All sub-dicts grow unbounded (no eviction): `llm_instance_pool`, `mcp_client_pool`, `tts_clients_by_user`, `notification_queues`
- Single `rag_processing_queue` for entire platform
- Single `configuration_lock` serializes all config changes globally

### 3.3 Single RAG Processing Worker

**File:** `src/trusted_data_agent/main.py` (`rag_processing_worker`)

**ONE worker** processes ALL RAG submissions sequentially.

**Status:** RESOLVED — N workers started at startup, `TDA_RAG_WORKERS` (default **2**).

**At 100ms/operation:** Max throughput = 10 ops/sec.
**At 1K agents:** If 50% trigger RAG = 500 ops queued = 50s wait.
**At 1M agents:** 500K ops queued = 50,000s (13.9 hours) wait.

### 3.4 No Backpressure or Circuit Breakers

- No circuit breaker for LLM provider failures (retry storm on outage)
- No request queuing with priority (all agents equal)
- No adaptive rate limiting based on provider response times
- Hard-coded event queue: `asyncio.Queue(maxsize=100)` in `executor.py` (`PlanExecutor.run`)

**Status (circuit breakers):** RESOLVED by the S4 Resilience Envelope, not by the Phase 2 plan's `llm/circuit_breaker.py`: `core/provider_health.py` is a process-wide breaker per provider (3 consecutive retryable failures in 120 s open it for 60 s, doubling to 600 s; half-open single probe), `llm/resilience.py` is the pure policy (classify · backoff honouring `retry-after` · truncation continuation · refusal), `llm/handler.py` (`call_llm_api`) and `llm/resilient_chat.py` are the two actors, and an open breaker routes to the profile's signed fallback model. Read-only status: `GET /v1/llm/provider-health`. Detail: [LLM_PROVIDER_ARCHITECTURE.md](LLM_PROVIDER_ARCHITECTURE.md). Priority queuing and adaptive rate limiting remain open.

### 3.5 MCP Connection Management

**File:** `src/trusted_data_agent/mcp_adapter/adapter.py`

- New MCP session per request (no keep-alive/reuse) — *still true: `client.session(server_id)` is opened per call*
- No connection pooling to MCP servers — *partially changed: one `MultiServerMCPClient` per server is kept in `APP_STATE['mcp_client_pool']`*
- At 1M agents: 1M simultaneous MCP connections (most MCP servers would collapse)

### Recommended Improvements -- Execution Layer

| Scale Target | Action | Effort |
|-------------|--------|--------|
| **100s** | Implement LLM client pool (per provider+model key, max 10 clients each) | 2-3 days |
| **1,000s** | Add circuit breaker per LLM provider (exponential backoff, half-open probing) | 1 week |
| **1,000s** | Scale RAG worker to pool of N workers (configurable, default 10) | 2-3 days |
| **10,000s** | Replace APP_STATE dicts with concurrent-safe structures (LRU caches with max-size) | 1-2 weeks |
| **100,000s+** | Distribute execution via task queue (Celery/Redis Queue/NATS) -- decouple API from execution | 3-4 weeks |
| **1M+** | MCP connection multiplexing (shared connections per server, request pipelining) | 2-3 weeks |
| **1M+** | Kubernetes-based autoscaling with pod-per-agent or pod-per-N-agents | 4-6 weeks |

---

## Layer 4: Caching & Memory (HIGH)

### 4.1 Prompt Loader -- Unbounded Triple Cache

**File:** `src/trusted_data_agent/agent/prompt_loader.py` (`PromptLoader` caches)

**Status:** RESOLVED (Phase 1) — `_TTLCache` (5 min TTL, 10K max).

Cache key pattern: `f"{name}:{user_uuid}:{profile_id}"`.
**At 1M users x 100 prompts x 10 profiles = 1B possible keys.** No TTL, no eviction, no max size.

### 4.2 ChromaDB Collection Cache -- Unbounded

**File:** `src/trusted_data_agent/agent/rag_retriever.py` (`RAGRetriever.collections`); `vectorstore/chromadb_backend.py` (`_coll_cache`)

**Status:** the ChromaDB backend's `_coll_cache` is a `BoundedLRUCache` (`TDA_COLL_CACHE_MAX`, 512) (WS1).

Each collection object holds in-memory indices. No eviction policy.

### 4.3 Vector Store Factory -- Unbounded Instance + Lock Caches

**File:** `src/trusted_data_agent/vectorstore/factory.py` (`_INSTANCES`, `_INIT_LOCKS`)

`_INSTANCES` and `_INIT_LOCKS` dicts grow indefinitely with unique config keys. Never pruned. *(Still true — left unbounded by design: config-keyed and unsafe to evict.)*

### 4.4 Embedding Model Cache

Each unique embedding model loads 100-500MB of weights into memory. With N distinct models cached globally, memory grows as `N * model_size`.

### 4.5 Cost Manager -- No Caching

**File:** `src/trusted_data_agent/core/cost_manager.py` (`CostManager.get_model_cost`)

`get_model_cost()` creates a new DB session for EVERY lookup. Called on every token count event. No caching whatsoever.

**Status:** RESOLVED (Phase 1) — `_cost_cache` with a 5-minute TTL, cleared after every cost sync.

### Recommended Improvements -- Caching Layer

| Scale Target | Action | Effort |
|-------------|--------|--------|
| **100s** | Add TTL (5 min) + max-size (10K entries) to PromptLoader caches | 1 day |
| **100s** | Add LRU cache to `cost_manager.get_model_cost()` (costs change rarely) | 2 hours |
| **1,000s** | Implement cache eviction for ChromaDB collections (LRU, max 100 collections) | 1-2 days |
| **10,000s** | Move hot caches to Redis (prompt cache, cost cache, config cache) -- shared across instances | 1-2 weeks |
| **100,000s+** | Implement tiered caching: L1 (in-process LRU) -> L2 (Redis) -> L3 (database) | 2-3 weeks |
| **1M+** | Embedding model serving via dedicated GPU service (not in-process) | 2-4 weeks |

---

## Layer 5: Web Server & API (HIGH)

### 5.1 Single-Process Event Loop

**File:** `src/trusted_data_agent/main.py` (`main` → `hypercorn.asyncio.serve`)

All request handling, SSE streaming, LLM calls, and background tasks run on ONE asyncio event loop. Any CPU-bound operation (JSON parsing, column statistics, token estimation) blocks all other requests.

### 5.2 SSE Connection Scaling

Each active agent holds an SSE connection (`/ask_stream`) for the duration of execution.

**At 1K agents:** 1,000 concurrent SSE connections -- manageable for Hypercorn.
**At 10K agents:** TCP backlog pressure, need to tune `somaxconn`.
**At 1M agents:** Requires dedicated SSE/WebSocket gateway (e.g., Centrifugo, Socket.IO cluster).

### 5.3 JWT Validation -- No Caching

**File:** `src/trusted_data_agent/auth/middleware.py` (`get_current_user`)

Every request: (1) crypto signature verification, (2) database query for user record. No caching of verified tokens.

**Status:** RESOLVED (Phase 1) — in-memory auth cache, 60 s TTL (`_AUTH_CACHE_TTL`), invalidated on revocation. The Redis L2 (Phase 2 Item 5) was not built.

### 5.4 Rate Limiting -- In-Memory Only

**File:** `src/trusted_data_agent/auth/rate_limiter.py` (`_rate_limits`)

Per-process Python dict. Multiple servers = independent rate limit state. No persistence across restarts. *(Still true — Phase 2 Item 4 not built.)*

### Recommended Improvements -- API Layer

| Scale Target | Action | Effort |
|-------------|--------|--------|
| **100s** | Add JWT signature LRU cache (10K entries, 1-hour TTL) | 2-3 hours |
| **1,000s** | Run Hypercorn with multiple workers (`--workers N`) | 1 day |
| **10,000s** | Move rate limiting to Redis (shared across instances) | 2-3 days |
| **100,000s+** | Deploy behind reverse proxy (nginx/envoy) with connection limiting | 1 week |
| **100,000s+** | Separate SSE gateway from API server (dedicated SSE service) | 2-3 weeks |
| **1M+** | Kubernetes Horizontal Pod Autoscaler with readiness probes | 2-3 weeks |
| **1M+** | Replace SSE with WebSocket + pub/sub (Redis Pub/Sub or NATS) | 3-4 weeks |

---

## Layer 6: Infrastructure & Deployment (MEDIUM)

### 6.1 Single-Server Architecture

Current deployment model is one Python process on one machine. All state (files, SQLite, in-memory caches) is local. No mechanism for horizontal scaling, state sharing, automatic failover, or health monitoring.

### 6.2 File System as Database

Three categories of data stored on local filesystem:
1. `tda_auth.db` -- SQLite database
2. `tda_sessions/` -- Session JSON files
3. `chromadb/` -- Vector store data

All are single-machine, single-process resources.

### 6.3 No Observability at Scale

- Logging is per-process (`logging.getLogger`)
- No distributed tracing (no request IDs propagated across LLM calls)
- No metrics collection (no Prometheus/StatsD integration)
- No alerting on resource exhaustion

**Status (Oct 2026):** PARTIAL. Health surfaces exist — `GET /v1/admin/db-health` (+ `POST /v1/admin/db-health/backup`), `GET /v1/llm/provider-health`; an unreadable database is announced on every open stream (`core/db_health.py`) and backed up daily with an integrity check (platform job `platform-db-backup`); a start that changes the schema is backed up first and recorded in the `schema_ledger` table (`core/schema_ledger.py`). Scored turns are exported as OTLP-shaped quality spans to a pluggable sink (`eval/online.py`, default JSONL `tda_eval/online_eval.jsonl`). Still missing: Prometheus-style metrics, distributed tracing across LLM/MCP calls, resource-exhaustion alerting.

### Recommended Improvements -- Infrastructure

| Scale Target | Action | Effort |
|-------------|--------|--------|
| **1,000s** | Add health check endpoints, structured JSON logging, request ID tracing | 1 week |
| **10,000s** | Containerize with Docker Compose: API + PostgreSQL + Redis + ChromaDB | 1-2 weeks |
| **100,000s+** | Kubernetes deployment with StatefulSets for databases, Deployments for API | 3-4 weeks |
| **100,000s+** | Add Prometheus metrics (request latency, queue depth, cache hit rate, DB pool usage) | 1-2 weeks |
| **1M+** | Multi-region deployment with geo-routing, data replication | 2-3 months |

---

## Consolidated Bottleneck Severity Matrix

| # | Bottleneck | Layer | Current Limit | Breaks At | Severity | Status |
|---|-----------|-------|--------------|-----------|----------|--------|
| 1 | SQLite StaticPool (1 connection) | Database | ~100 writes/sec | 100s of agents | **CRITICAL** | **RESOLVED** (Phase 1 WAL; `QueuePool` + process-wide serialization via `sqlite_util`; writes still single-writer — Postgres via `TDA_AUTH_DB_URL`) |
| 2 | File-based sessions (full re-serialize) | Session | ~1K concurrent | 1,000s of agents | **CRITICAL** | **RESOLVED** (WS1 — swappable `SessionPersistenceBackend`; Postgres backend does single-row JSONB upsert, no re-serialize. File remains zero-config default) |
| 3 | Single RAG processing worker | Execution | 10 ops/sec | 100s of agents | **CRITICAL** | **RESOLVED** (Phase 1 — `TDA_RAG_WORKERS`, default 2) |
| 4 | No LLM client pooling (new client per call) | Execution | ~1GB at 1K agents | 1,000s of agents | **CRITICAL** | **RESOLVED** (Phase 1) |
| 5 | Global APP_STATE (unprotected, unbounded) | Execution | Race conditions at any scale | 100s of agents | **CRITICAL** | **RESOLVED** (Phase 1) |
| 6 | In-process-only locks (no distributed coordination) | Session | 1 server instance | Any multi-instance | **CRITICAL** | **RESOLVED** (WS1 — `LockProvider`; per-session write lock routed through it; Redis drop-in via `TDA_LOCK_BACKEND=redis`. Global config locks not yet routed) |
| 7 | Session listing O(n) file scan | Session | ~1K sessions/user | 1,000s of sessions | **HIGH** | **RESOLVED** (Phase 1) |
| 8 | Consumption enforcement 5 queries/check | Database | ~200 checks/sec | 1,000s of agents | **HIGH** | **PARTIAL** (WS1 — merged the hourly+daily counts into one query; deeper Redis-counter work deferred with multi-server) |
| 9 | No JWT validation cache | API | ~10K verifications/sec | 10,000s of agents | **HIGH** | **RESOLVED** (Phase 1) |
| 10 | Unbounded prompt/collection/backend caches | Caching | GB-scale memory | 10,000s of agents | **HIGH** | **RESOLVED** (WS1 — `BoundedLRUCache` on the load-growing caches: `llm_instance_pool`, `tts_clients_by_user`, ChromaDB `_coll_cache`. Vector `_INSTANCES` intentionally left — config-keyed + unsafe to evict, see §4.3) |
| 11 | Single asyncio event loop | API | CPU-bound ops block all | 1,000s of agents | **HIGH** | Open |
| 12 | In-memory rate limiting (per-process) | API | 1 server instance | Any multi-instance | **MEDIUM** | Open (Phase 2) |
| 13 | No circuit breakers for LLM providers | Execution | Retry storms on outage | 100s of agents | **MEDIUM** | **RESOLVED** (S4 Resilience Envelope — `core/provider_health.py` breaker + `llm/resilience.py` + signed fallback) |
| 14 | No observability/metrics | Infra | Blind at any scale | 100s of agents | **MEDIUM** | **PARTIAL** (db/provider health endpoints, daily DB backup, schema ledger, OTLP-shaped quality spans; no metrics/tracing) |

---

## WS1 — Session Storage & Coordination Hardening (COMPLETE — Jun 2026)

**Goal:** make the session-storage tier (the dominant write bottleneck, #2/#6 above) horizontally scalable and bounded under load, without disturbing the zero-config single-user default.

**What shipped (each increment is independently shippable and behavior-preserving):**

| # | Change | Files |
|---|--------|-------|
| R1.0 | Consolidated **15 direct session-file bypasses** behind the `session_manager` public API (the seam the backend swaps behind). | `session_manager.py`, `artifact_detectors.py`, `rest_routes.py`, `routes.py`, `dashboard_routes.py`, `agent_pack_routes.py`, `auth_routes.py`, `executor.py`, `execution_service.py` |
| R1.1 | **`SessionPersistenceBackend` ABC + `FileSessionBackend`** (file I/O + SQLite index moved behind the interface); `session_manager`'s 4 persistence primitives delegate; notifications/enrichment stay manager-side. | `core/session_backends/` (new), `session_manager.py`, `main.py` |
| R1.2 | **`PostgresSessionBackend`** — one row per session, full `body` JSONB + denormalized summary columns (the table *is* the index); env-sized auth pool; lazy `asyncpg` pool closed on shutdown. | `core/session_backends/postgres_backend.py`, `auth/database.py` |
| R1.3 | **`LockProvider`** (in-process default + Redis drop-in); per-session write lock routed through it; consumption hourly+daily counts merged into one query. | `core/locks/` (new), `session_manager.py`, `auth/consumption_enforcer.py` |
| R1.4 | **`BoundedLRUCache`** applied to the caches that grow with load (`llm_instance_pool`, `tts_clients_by_user`, ChromaDB `_coll_cache`). | `core/bounded_cache.py` (new), `config.py`, `tts_service.py`, `vectorstore/chromadb_backend.py` |

**Selection (zero-config defaults; multi-server is opt-in):**
- `TDA_SESSION_BACKEND` = `file` (default) | `postgres` — DSN via `TDA_SESSION_PG_DSN` / postgres `TDA_AUTH_DB_URL`
- `TDA_LOCK_BACKEND` = `inprocess` (default) | `redis` — url via `TDA_REDIS_URL`
- Cache bounds: `TDA_LLM_POOL_MAX` (256), `TDA_TTS_CACHE_MAX` (256), `TDA_COLL_CACHE_MAX` (512)

**Default vs opt-in (no second database by default).** WS1 added the *option* of Postgres session storage — it did **not** introduce a second database for existing installs. With defaults, the platform uses exactly the same stores as before WS1:

| Setup | Auth DB | Session storage | Databases |
|---|---|---|---|
| **Default (single-server, zero-config)** | SQLite (`tda_auth.db`) | files + SQLite index | unchanged from pre-WS1 |
| Opt session storage into Postgres only | SQLite | **Postgres** | 2 (transient — see auth-DB scoping) |
| Multi-server, fully migrated | **Postgres** | **Postgres** | 1 (the goal — needs the separate auth-DB-on-Postgres effort) |

The "2 databases" row is a *transient* state on the way to multi-server; the end-state points both auth and sessions at **one** Postgres. See `docs/Strategy/AUTH_DB_POSTGRES_SCOPING.md`.

### Measured session-write throughput (file vs Postgres)

Harness `test/performance/session_storage_load.py` drives the real `_session_transaction` load-modify-save path (append a turn to a growing `workflow_history` + bump token counters) under concurrency. It isolates the **storage tier** (no HTTP/LLM/MCP), so the numbers reflect exactly what WS1 changed. Reproduce:

```bash
PYTHONPATH=src python test/performance/session_storage_load.py --sessions 25 --turns 15 --rows 20
```

**Result** (25 concurrent sessions × 15 turns = 375 writes, 20 rows/turn, MacBook dev box, Postgres 16 via `pgserver`):

| backend | writes/s | p50 | p95 | p99 | max | wall |
|---|--:|--:|--:|--:|--:|--:|
| **file** (FileSessionBackend) | **763** | 30 ms | 66 ms | 66 ms | 68 ms | 0.49 s |
| **postgres** (PostgresSessionBackend) | **2,812** | 6 ms | 17 ms | 21 ms | 32 ms | 0.13 s |

**Reading:** Postgres delivers **~3.7× the write throughput** and **~5× lower p50 latency** under concurrency. The limiting resource for the **file** backend is two-fold: (1) the whole (growing) session body is re-serialized + atomically rewritten every turn, and (2) the fire-and-forget SQLite summary-index upserts contend on a single-writer DB — both worsen as concurrency and session size rise (a 50×30 run amplifies this sharply). The **postgres** backend writes one row (body + summary in a single upsert) with a real connection pool, so it scales with concurrency instead of degrading.

**Recommendation:** single-server / single-user stays on the file backend (zero deps). For multi-server or high-concurrency single-server, set `TDA_SESSION_BACKEND=postgres` (+ `TDA_LOCK_BACKEND=redis` for cross-process session-write coordination).

### Verification
- Parity: `test/test_session_access.py` runs **39 assertions** against the file backend; `test/test_session_backend_postgres.py` runs the **same 39** against a real Postgres (`pgserver`) — identical behavior across backends, including the concurrent-transaction (no-lost-update) test.
- Locks: `test/test_locks.py` (7) — mutual exclusion, LRU eviction never drops a held lock.
- Caches: `test/test_bounded_cache.py` (25) — incl. a 100k-insert soak that stays flat at the bound.

### Known follow-ons (out of WS1 scope)
- **Auth DB** (users, `genie_session_links`, consumption, the 38 SQLite `schema/*.sql`) is **not** migrated to Postgres — a separate, larger initiative. The session-storage tier (the dominant bottleneck) is.
- Global `configuration_lock`/`_pool_lock` and `genie_coordinator._slave_session_locks` not routed through `LockProvider` (low-frequency; in-process fine for single-server).
- Vector `_INSTANCES` left unbounded by design (config-keyed + Teradata global-context = unsafe to evict).

---

## Phased Improvement Roadmap

### Phase 1: Quick Wins (COMPLETE -- March 2026) -- Scale to 1,000s of agents

**Goal:** Remove the cheapest critical bottlenecks without architectural changes.
**Status:** All 8 items implemented and verified via REST API testing.

1. **SQLite WAL mode + PRAGMAs** -- `database.py` -- WAL, synchronous=NORMAL, cache_size=-64000, temp_store=MEMORY [DONE; since Jun 2026 the pool is a `QueuePool` and every SQLite open goes through `core/sqlite_util.connect()`]
2. **LLM client pool** -- `client_factory.py` -- `get_or_create_llm_client()` with 50-max pool, keyed by provider+model+credentials hash [DONE]
3. **Scale RAG worker** -- `main.py` -- Pool of workers (configurable via `TDA_RAG_WORKERS`, code default 2) [DONE]
4. **JWT validation cache** -- `middleware.py` -- In-memory TTL cache (60s, 10K max) with explicit invalidation on token revocation [DONE]
5. **Cost manager cache** -- `cost_manager.py` -- 5-minute TTL cache for model pricing lookups [DONE]
6. **PromptLoader bounded caches** -- `prompt_loader.py` -- `_TTLCache` class with TTL (5 min) + max-size (10K) eviction [DONE]
7. **Session metadata index** -- `session_manager.py` -- aiosqlite-based index with auto-rebuild on startup [DONE]
8. **APP_STATE lock protection** -- `config.py`, `configuration_service.py` -- asyncio.Lock + dict.setdefault() for atomic operations [DONE]

### Phase 2: Foundation (POSTPONED) -- Scale to 10,000s of agents

> **Status (2026-10-30).** This plan was never executed as written; parts of its intent shipped by other routes. The plan below is kept as the record. None of its three NEW files exist.
>
> | Item | Outcome |
> |---|---|
> | 1 Circuit breakers | **Superseded** by the S4 Resilience Envelope: `core/provider_health.py` (breaker), `llm/resilience.py` (policy), `llm/resilient_chat.py` + `llm/handler.py` (actors), `agent/resilience_envelope.py` (per-turn steps); `GET /v1/llm/provider-health` (read-only — no reset endpoint, no `TDA_CIRCUIT_BREAKER_*` env) |
> | 2 Cache eviction | **Superseded** by WS1 R1.4 `core/bounded_cache.py` (`BoundedLRUCache`); no `cache_janitor.py`. Vector `_INSTANCES` deliberately unbounded |
> | 3 Redis foundation | **Not built.** Redis is used only by the optional lock provider (`core/locks/redis_backend.py`, `TDA_LOCK_BACKEND=redis`, `TDA_REDIS_URL`); no `core/redis_client.py`, no `TDA_REDIS_ENABLED` |
> | 4 Redis rate limiting | **Not built** (`rate_limiter.py` still in-memory) |
> | 5 Redis JWT cache | **Not built** (in-memory 60 s cache only) |
> | 6 Redis consumption counters | **Not built**; WS1 merged hourly+daily counts into one query |
> | 7 Collection DB pooling | **Superseded** by `core/sqlite_util.connect()` |

**Goal:** Add reliability infrastructure (circuit breakers), cache hygiene (evict unbounded caches), and Redis integration (shared state for future multi-process scaling).
**Infrastructure:** Redis instance (existing), Teradata (enterprise DB -- full migration deferred to Phase 3).
**Target:** ~10,000s of concurrent agents with resilience to provider failures.
**Estimated effort:** ~7 days

#### Item 1: LLM Circuit Breakers (~2 days) -- Priority: TOP

Prevents cascading failures when LLM providers go down.

**New file:** `src/trusted_data_agent/llm/circuit_breaker.py`
- `CircuitBreaker` class: CLOSED -> OPEN (after 3 consecutive failures) -> HALF_OPEN (after 60s timeout) -> CLOSED (on success)
- Module-level registry keyed by provider name
- `CircuitBreakerOpen` exception for fast-fail (non-retryable)
- Config: `TDA_CIRCUIT_BREAKER_THRESHOLD` (default 3), `TDA_CIRCUIT_BREAKER_TIMEOUT` (default 60)

**Integration points:**
- `llm/handler.py` -- wrap retry loop: check `allow_request()` before attempts, `record_failure()` in exception handlers, `record_success()` after response
- Expand caught error types: add `GoogleAPIError`, `botocore.ClientError`, `httpx.ConnectError/TimeoutException`, `ConnectionError/TimeoutError`
- `agent/executor.py` -- catch `CircuitBreakerOpen`, yield user-friendly SSE error
- `agent/phase_executor.py` -- catch in tactical calls, treat as non-retryable
- `api/admin_routes.py` -- `GET /api/v1/admin/circuit-breakers` + `POST .../reset`

#### Item 2: Cache Eviction for Unbounded Caches (~1 day) -- Priority: HIGH

**New file:** `src/trusted_data_agent/core/cache_janitor.py`
- Periodic async task (every 5 min): purge completed background_tasks (>1hr), stale mcp_tool_schema_cache (>10min)
- Start via `asyncio.create_task()` in `main.py` startup

**Max-size guards:**
- `configuration_service.py` -- `llm_instance_pool` FIFO eviction at 50
- `vectorstore/factory.py` -- `_INSTANCES` FIFO eviction at 20
- `core/config.py` -- `current_provider_by_user` / `current_model_by_user` FIFO eviction at 500

#### Item 3: Redis Foundation (~0.5 day) -- Priority: MEDIUM

**New file:** `src/trusted_data_agent/core/redis_client.py`
- Async Redis singleton via `redis>=5.0.0` (add to `requirements.txt`)
- Config: `TDA_REDIS_ENABLED` (default false), `TDA_REDIS_URL`, `TDA_REDIS_PREFIX=uderia:`
- `get_redis() -> Optional[Redis]` -- returns None if disabled/unavailable (all consumers must handle None)
- Lifecycle: connect in `@app.before_serving`, close in `@app.after_serving`
- Admin: `GET /api/v1/admin/redis/status`

#### Item 4: Redis-Backed Rate Limiting (~1 day) -- Priority: MEDIUM (depends on Item 3)

- `rate_limiter.py` -- add Redis path (INCR + EXPIRE), fallback to existing in-memory
- `check_rate_limit()` becomes async -- update 4 callers (all in async route handlers)

#### Item 5: Redis-Backed JWT Auth Cache (~0.5 day) -- Priority: LOW (depends on Item 3)

- `middleware.py` -- add Redis as L2 cache: L1 (in-memory) -> L2 (Redis GET/SET with 60s TTL) -> DB
- `get_current_user()` becomes async -- update `require_auth`, `require_admin`, `optional_auth` decorators

#### Item 6: Redis-Backed Consumption Counters (~1 day) -- Priority: LOW (depends on Item 3)

- `consumption_enforcer.py` -- replace 5 sequential DB queries with Redis INCR+EXPIRE counters
- Keep DB writes for audit trail; Redis is fast-path check
- Fallback to existing DB queries if Redis unavailable

#### Item 7: Collection DB Connection Pooling (~0.5 day) -- Priority: LOW

- `collection_db.py` -- replace per-call `sqlite3.connect()` with context-manager pool (3 connections)
- Add WAL mode pragma to pooled connections

#### Implementation Order

```
Day 1-2: Item 1 (Circuit Breakers) -- new file + handler integration + admin endpoints
Day 3:   Item 2 (Cache Eviction) + Item 7 (Collection DB Pooling)
Day 4:   Item 3 (Redis Foundation)
Day 5:   Item 4 (Redis Rate Limiting)
Day 6:   Item 5 (Redis JWT Cache) + Item 6 (Redis Consumption Counters)
Day 7:   Integration testing all items together
```

#### Files Modified (Phase 2)

| File | Items | Changes |
|------|-------|---------|
| **NEW** `llm/circuit_breaker.py` *(never created — see `core/provider_health.py`)* | 1 | CircuitBreaker class + registry |
| **NEW** `core/cache_janitor.py` *(never created — see `core/bounded_cache.py`)* | 2 | Background cleanup task |
| **NEW** `core/redis_client.py` *(never created — see `core/locks/redis_backend.py`)* | 3 | Async Redis singleton |
| `llm/handler.py` | 1 | Circuit breaker wrapping retry loop |
| `agent/executor.py` | 1 | Handle CircuitBreakerOpen |
| `agent/phase_executor.py` | 1 | Handle CircuitBreakerOpen |
| `api/admin_routes.py` | 1, 3 | Circuit breaker + Redis admin endpoints |
| `main.py` | 2, 3 | Cache janitor + Redis lifecycle |
| `core/configuration_service.py` | 2 | LLM pool max-size |
| `vectorstore/factory.py` | 2 | Instance pool max-size |
| `core/config.py` | 2 | Per-user dict max-size |
| `auth/rate_limiter.py` | 4 | Redis rate limiting + async |
| `auth/middleware.py` | 5 | Redis L2 JWT cache + async |
| `auth/consumption_enforcer.py` | 6 | Redis consumption counters |
| `core/collection_db.py` | 7 | Connection pooling |
| `requirements.txt` | 3 | Add redis>=5.0.0 |

### Phase 3: Horizontal Scaling (1-3 months) -- Scale to 100,000s of agents

**Goal:** Stateless API servers with externalized state, horizontal scaling.

1. **Task queue** -- Celery/Redis Queue for agent execution (decouple API from LLM calls)
2. **Dedicated SSE/WebSocket gateway** -- Separate service for real-time streaming
3. **Kubernetes deployment** -- HPA, StatefulSets, service mesh
4. **Observability** -- Prometheus metrics, distributed tracing, structured logging
5. **Embedding model service** -- Dedicated GPU service for vector embeddings
6. **MCP connection multiplexing** -- Shared connection pools per MCP server

### Phase 4: Planet Scale (3-6 months) -- Scale to 1M+ agents

**Goal:** Multi-region, fully distributed, auto-scaling architecture.

1. **Event-sourced sessions** -- Append-only event log, materialized views
2. **Database sharding** -- User-based sharding for PostgreSQL
3. **Multi-region** -- Geo-routing, data replication, eventual consistency
4. **Agent scheduling** -- Priority queues, fair scheduling, resource quotas per tenant
5. **Vector store clustering** -- Qdrant Cloud cluster or Teradata distributed deployment
6. **Pub/Sub for real-time** -- NATS or Redis Streams replacing SSE

---

## Key Files Reference

Concern = the March 2026 finding; *now* = the state verified 2026-10-30.

| Area | File | Primary Concern |
|------|------|-----------------|
| Database engine | `src/trusted_data_agent/auth/database.py` (`DATABASE_URL`, engine) | StaticPool, no WAL — *now QueuePool + WAL; Postgres via `TDA_AUTH_DB_URL`* |
| SQLite access | `src/trusted_data_agent/core/sqlite_util.py` (`connect`) | *now the one serialized way to open SQLite* |
| Session storage | `src/trusted_data_agent/core/session_backends/` (`FileSessionBackend`, `PostgresSessionBackend`), `session_manager.py` (`_session_transaction`, `get_all_sessions`) | File I/O, locks, listing — *now swappable backend + summary index* |
| Session locks | `src/trusted_data_agent/core/locks/` (`get_lock_provider`) | *in-process default, Redis optional* |
| Collection DB | `src/trusted_data_agent/core/collection_db.py` (`CollectionDatabase`) | No connection pooling — *now `sqlite_util.connect`* |
| Global state | `src/trusted_data_agent/core/config.py` (`APP_STATE`) | Unbounded, unprotected — *now locked + bounded LRU sub-dicts* |
| Bounded caches | `src/trusted_data_agent/core/bounded_cache.py` (`BoundedLRUCache`) | *WS1* |
| LLM clients | `src/trusted_data_agent/llm/client_factory.py` (`create_llm_client`, `get_or_create_llm_client`) | No pooling/reuse — *now pooled* |
| LLM handler | `src/trusted_data_agent/llm/handler.py` (`call_llm_api`) | No circuit breaker — *now breaker + signed fallback* |
| Provider breaker | `src/trusted_data_agent/core/provider_health.py`, `llm/resilience.py`, `llm/resilient_chat.py` | *S4 Resilience Envelope* |
| RAG worker | `src/trusted_data_agent/main.py` (`rag_processing_worker`) | Single worker — *now `TDA_RAG_WORKERS`* |
| Agent executor | `src/trusted_data_agent/agent/executor.py` (`PlanExecutor.run`, `asyncio.Queue(maxsize=100)`) | Hard-coded queue size |
| Prompt caching | `src/trusted_data_agent/agent/prompt_loader.py` (`_TTLCache`) | Unbounded caches — *now TTL + max-size* |
| JWT auth | `src/trusted_data_agent/auth/middleware.py` (`get_current_user`) | No validation cache — *now 60 s cache* |
| Rate limiting | `src/trusted_data_agent/auth/rate_limiter.py` (`_rate_limits`) | In-memory only (unchanged) |
| Consumption | `src/trusted_data_agent/auth/consumption_enforcer.py` (`ConsumptionEnforcer.can_execute_prompt`) | N+1 queries — *hourly+daily merged* |
| Cost tracking | `src/trusted_data_agent/core/cost_manager.py` (`CostManager.get_model_cost`) | No caching — *now 5-min TTL cache* |
| Vector factory | `src/trusted_data_agent/vectorstore/factory.py` (`_INSTANCES`, `_INIT_LOCKS`) | Unbounded caches (by design) |
| ChromaDB | `src/trusted_data_agent/vectorstore/chromadb_backend.py` (`_coll_cache`) | Unbounded collection cache — *now bounded LRU* |
| Web server | `src/trusted_data_agent/main.py` (`main` → `hypercorn.asyncio.serve`) | Single event loop (unchanged) |
| MCP adapter | `src/trusted_data_agent/mcp_adapter/adapter.py` | No connection pooling — *client per server pooled, session per call* |
| DB health | `src/trusted_data_agent/core/db_health.py`, `core/schema_ledger.py` | *unreadable-DB announcement, daily backup, upgrade backup* |
| Component mgr | `src/trusted_data_agent/components/manager.py` (`get_component_manager`) | Global singleton |
