# Knowledge Retrieval Architecture

## Overview

The Knowledge Retrieval system powers the **Focus** profile class (`rag_focused`) and is also available as an optional capability for **Ideate** profiles (`llm_only`). It delivers grounded, hallucination-free answers from verified documents. Unlike Planner Repositories that self-improve execution strategies, Knowledge Repositories serve as the organization's searchable institutional memory — policies, documentation, domain expertise, and reference materials accessible through natural language.

This document provides a comprehensive architectural deep-dive into the retrieval pipeline, scoring algorithms, configuration system, vector store abstraction, and integration points.

> **Related Documentation:** [Vector Store Abstraction Architecture](VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md) — backend interface, factory pattern, ChromaDB, Teradata and Qdrant implementations. [Context Window Architecture](CONTEXT_WINDOW_ARCHITECTURE.md) — the `knowledge_context` and `document_context` modules. [Files Architecture](FILES_ARCHITECTURE.md) — attachments the Focus engine answers from.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Architecture Overview](#architecture-overview)
3. [Execution Flow](#execution-flow)
4. [Knowledge Retrieval for Ideate Profiles](#knowledge-retrieval-for-ideate-profiles)
5. [Vector Store Abstraction](#vector-store-abstraction)
6. [Document Ingestion Pipeline](#document-ingestion-pipeline)
7. [CDC Sync Infrastructure](#cdc-sync-infrastructure)
8. [Retrieval Pipeline](#retrieval-pipeline)
9. [Scoring Algorithms](#scoring-algorithms)
10. [Configuration System](#configuration-system)
11. [LLM Synthesis](#llm-synthesis)
12. [Multi-User Access Control](#multi-user-access-control)
13. [Event System](#event-system)
14. [Comparison: Knowledge vs Planner Repositories](#comparison-knowledge-vs-planner-repositories)
15. [Performance Characteristics](#performance-characteristics)
16. [Advanced Features](#advanced-features)
17. [Troubleshooting](#troubleshooting)
18. [File Reference](#file-reference)

---

## Design Philosophy

### Anti-Hallucination by Architecture

The Focus profile class enforces a strict retrieval-then-synthesize pattern:

1. **No general knowledge**: The LLM receives a system prompt instructing it to answer **only** from retrieved documents
2. **No MCP tool calling**: The execution path bypasses the Planner/Executor entirely — no MCP tools, no strategic planning. *Exception*: When component tools (TDA_Charting, TDA_Canvas) are enabled on the profile, synthesis routes through `ConversationAgentExecutor`, allowing the LLM to visualize retrieved data. See [Agent-Based Synthesis](#agent-based-synthesis-with-component-tools).
3. **Transparent failure**: If no relevant documents are found, the system reports this honestly rather than fabricating an answer
4. **Source traceability**: Every synthesized answer carries citations back to specific document chunks and metadata

### Separation from Planner Repositories

The system maintains a clear architectural boundary between two repository types:

| Concern | Planner Repositories | Knowledge Repositories |
|---------|---------------------|----------------------|
| **Goal** | Improve agent execution over time | Ground answers in verified sources |
| **Profile Class** | Optimize (`tool_enabled`) | Focus (`rag_focused`) |
| **Data lifecycle** | Auto-captured from successful executions — into the agent's own learning repository when it has one (`core/agent_learning.py`, see [RAG.md → Where an agent learns](../RAG/RAG.md)) | Uploaded documents, optionally kept in sync with their source (CDC) |
| **Consumer** | Strategic planner (few-shot examples) | LLM synthesis (direct context) |
| **Mutation** | System writes, user rates | User uploads, system indexes |
| **Metadata filters** | `strategy_type`, `is_most_efficient`, `user_feedback_score` | None (all chunks searchable) |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        KNOWLEDGE RETRIEVAL SYSTEM                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌───────────┐    ┌──────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │  Document  │    │  Repository  │    │ Vector Store │    │  Embedding  │  │
│  │  Upload    │───►│  Constructor │───►│   Backend    │◄───│  Provider   │  │
│  │  (API)     │    │  (Chunking)  │    │ (ChromaDB /  │    │ (Client or  │  │
│  └───────────┘    └──────────────┘    │  Teradata)   │    │  Server)    │  │
│                                        └──────┬───────┘    └─────────────┘  │
│                                               │                             │
│                                               │ query()                     │
│                                               ▼                             │
│  ┌───────────┐    ┌──────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │  Profile   │───►│  Config      │───►│  RAG         │───►│  LLM        │  │
│  │  Config    │    │  Resolution  │    │  Retriever   │    │  Synthesis   │  │
│  │            │    │  (3-Tier)    │    │  (Scoring)   │    │  (Answer)    │  │
│  └───────────┘    └──────────────┘    └──────────────┘    └─────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Engines (FocusEngine · IdeateEngine/ConversationEngine)             │  │
│  │  - No planning, no MCP tools (component tools optional)              │  │
│  │  - Mandatory retrieval → LLM synthesis → answer with citations       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **Focus Engine** | `agent/engines/focus_engine.py` (`FocusEngine`) | The `rag_focused` path: retrieval, reranking, synthesis, persistence |
| **Ideate Engines** | `agent/engines/ideate_engine.py` · `conversation_engine.py` | The `llm_only` knowledge path (direct, or ReAct with MCP/component tools) |
| **Executor** | `agent/executor.py` (`PlanExecutor`) | Shared services the engines call: `documents_for_turn()`, `attached_material()`, `_rerank_knowledge_with_llm()`, `_call_llm_and_update_tokens()` |
| **Agent History** | `agent/agent_history.py` | `build()` — the one conversation-history rule for Focus synthesis, ReAct and Coordinate |
| **RAG Retriever** | `agent/rag_retriever.py` | Semantic search, scoring, deduplication |
| **Repository Constructor** | `agent/repository_constructor.py` | Document chunking and vector store population |
| **Config Manager** | `core/config_manager.py` | Three-tier configuration resolution |
| **Knowledge Routes** | `api/knowledge_routes.py` | Document upload and collection management API |
| **Collection Utils** | `core/collection_utils.py` | Collection utility functions |
| **Vector Store Backend** | `vectorstore/base.py` | Abstract interface for vector store operations |
| **Vector Store Factory** | `vectorstore/factory.py` | Backend factory with singleton caching |
| **Conversation Agent** | `agent/conversation_agent.py` | Agent-based synthesis when component tools enabled |

---

## Execution Flow

When a query arrives at a `rag_focused` profile, the executor follows a dedicated path that bypasses the Planner/Executor architecture entirely.

### Step-by-Step Flow

```
1. ENGINE DISPATCH
   │  engines/registry.py resolves profile_type == "rag_focused"
   │  → engines/focus_engine.py: FocusEngine.run(executor)
   │
   ▼
2. CONFIGURATION RESOLUTION
   │  config_manager.py: get_effective_knowledge_config()
   │  Three-tier: Admin Lock → Profile Override → Global Default
   │  → maxDocs, minRelevanceScore, maxTokens, freshnessWeight, etc.
   │
   ▼
3. COLLECTION DISCOVERY
   │  focus_engine.py: Extract knowledge collections from profile config
   │  Each collection has: collectionId, optional reranking flag
   │
   ▼
4. RETRIEVAL
   │  rag_retriever.py: retrieve_examples()
   │  For each accessible collection:
   │    → ChromaDB.query() with cosine similarity
   │    → Calculate similarity_score = 1 - distance
   │    → Calculate freshness_score = exp(-decay × days_old)
   │    → Calculate adjusted_score = blend(similarity, freshness)
   │    → Filter by minRelevanceScore
   │    → Apply maxChunksPerDocument deduplication
   │  Sort all results by adjusted_score descending
   │  Return top maxDocs results
   │
   ▼
5. OPTIONAL RERANKING
   │  executor._rerank_knowledge_with_llm(): per-collection LLM reranking if enabled
   │  Sends top candidates to LLM for relevance re-ordering
   │
   ▼
6. CONTEXT FORMATTING
   │  focus_engine.py: Format documents into knowledge context string
   │  Respect maxTokens budget (capped by the CW knowledge_context allocation)
   │  No documents but a readable attachment → answer from the attachment
   │  (executor.attached_material(); see No-Results Handling)
   │  Include metadata (title, filename, source)
   │
   ▼
7. LLM SYNTHESIS
   │  llm/handler.py: call_llm_api() (raise_on_error=True — a provider
   │  failure becomes the honest sentence from agent/turn_failure.py and a
   │  FAILED turn, never an empty "successful" answer)
   │  Input: system_prompt + knowledge_context + history + user_query
   │  (history from agent_history.build(); none in Turn Summaries mode)
   │  System prompt: RAG_FOCUSED_EXECUTION or synthesisPromptOverride
   │  Output: Synthesized answer grounded in retrieved documents
   │
   ▼
8. RESPONSE FORMATTING
   │  Format HTML answer with source citations
   │  Include TTS payload
   │  Include knowledge source metadata
   │
   ▼
9. PERSISTENCE
   │  session_manager.py: Save to conversation history
   │  Update token counts (input + output)
   │  Emit workflow_history entry for turn replay
```

### What the Focus Profile Does NOT Do

Unlike `tool_enabled` profiles, the `rag_focused` path:
- Does **not** invoke the strategic planner
- Does **not** call MCP tools (component tools like TDA_Charting are the exception — see [Agent-Based Synthesis](#agent-based-synthesis-with-component-tools))
- Does **not** execute tactical planning phases
- Does **not** save results as champion cases for future RAG
- Does **not** perform self-correction loops

This intentional simplicity ensures predictable, fast, grounded responses.

---

## Knowledge Retrieval for Ideate Profiles

Knowledge retrieval is not exclusive to `rag_focused` profiles. **Ideate** (`llm_only`) profiles can also enable knowledge retrieval via `knowledgeConfig.enabled = true`. This allows conversational profiles to ground their answers in verified documents while retaining their direct-conversation execution model.

> **Profile scope clarification**: Knowledge retrieval is **mandatory** for Focus (`rag_focused`) — the entire execution path is built around it. For Ideate (`llm_only`), it is **optional** and must be explicitly enabled via `knowledgeConfig.enabled = true`. Optimize (`tool_enabled`) profiles can also use the `knowledge_context` context window module when collections are configured, but retrieval there feeds the planner prompt rather than driving the synthesis step directly.

### How It Works

When an `llm_only` profile has `knowledgeConfig.enabled = true`, the executor:

1. Detects knowledge is enabled from the profile configuration
2. Runs the **identical retrieval pipeline** as `rag_focused` (3-tier config, scoring, deduplication)
3. Formats retrieved documents into a knowledge context string
4. Injects the context into the LLM prompt alongside the conversation history
5. The LLM synthesizes an answer that blends knowledge documents with conversational context

```python
# engines/ideate_engine.py (and conversation_engine.py) — llm_only knowledge detection
knowledge_config = profile_config.get("knowledgeConfig", {})
knowledge_enabled = knowledge_config.get("enabled", False)

if knowledge_enabled and executor.rag_retriever:
    # Uses same retrieve_examples() pipeline as rag_focused
    all_results = await executor.rag_retriever.retrieve_examples(
        query=executor.original_user_input,
        repository_type="knowledge",
        ...
    )
```

### Key Differences from Focus Profiles

| Aspect | Focus (`rag_focused`) | Ideate with Knowledge (`llm_only`) |
|--------|----------------------|------------------------------------|
| **Primary purpose** | Document-grounded answers only | Conversational with optional grounding |
| **System prompt** | RAG_FOCUSED_EXECUTION (strict) | Standard conversation prompt + knowledge |
| **Component tools** | Optional via ConversationAgentExecutor | Optional via ConversationAgentExecutor |
| **MCP tools** | Never | Available if `useMcpTools = true` |
| **When no results found** | Reports "no relevant knowledge" | Falls back to general conversation |

### Configuration

The same `knowledgeConfig` structure applies to both profile types:

```json
{
  "profile_type": "llm_only",
  "knowledgeConfig": {
    "enabled": true,
    "collections": [
      { "collectionId": 37, "reranking": false }
    ],
    "maxDocs": 5,
    "minRelevanceScore": 0.30
  }
}
```

All configuration parameters, three-tier resolution, and admin locks work identically.

### Events

The `llm_only` knowledge path emits:
- `execution_start` with `knowledge_enabled: true` and `knowledge_collections` count
- `knowledge_retrieval` notification event (retrieval progress)
- Standard `conversation_llm_step` events (synthesis tokens)

---

## Vector Store Abstraction

Knowledge repositories are backed by a **pluggable vector store abstraction layer** (`src/trusted_data_agent/vectorstore/`). This allows collections to use different storage backends while presenting a unified async interface to the retrieval pipeline.

### Backends

| Backend | Type | Embedding | Capabilities |
|---------|------|-----------|-------------|
| **ChromaDB** | Local embedded | Client-side (SentenceTransformers) | 12 capabilities (default) |
| **Teradata** | Enterprise server | Server-side (Bedrock / Azure OpenAI, or the lake's own IAM role) | 13 capabilities (incl. server-side chunking, hybrid search) |
| **Qdrant Cloud** | Managed cloud | Client-side (SentenceTransformers) | 13 capabilities (incl. hybrid dense + sparse search) |

### Architecture

```
RAG Retriever
     │
     ▼
VectorStoreFactory.get_backend(backend_type, config)
     │
     ├── ChromaDB  → PersistentClient, client-side embedding
     ├── Teradata  → EVS SDK/REST + SQL, server-side embedding
     └── Qdrant    → AsyncQdrantClient, client-side embedding
```

- **Factory pattern**: `vectorstore/factory.py` maintains singleton instances keyed by `(backend_type, config)` fingerprint (MD5 of sorted JSON)
- **Async-first**: All interface methods are `async`; ChromaDB wraps sync calls via `asyncio.to_thread()`
- **Capability negotiation**: Backends declare supported operations via `VectorStoreCapability` enum. Callers check `has_capability()` before attempting optional operations.
- **Per-collection backend**: Each collection stores its `backend_type` and `backend_config` in the `collections` table, allowing mixed backends within a single deployment

### Per-Collection Backend Resolution

```python
# factory.py — resolve backend from collection metadata
async def get_backend_for_collection(coll_meta) -> VectorStoreBackend:
    backend_type = coll_meta.get("backend_type", "chromadb")
    backend_config = json.loads(coll_meta.get("backend_config", "{}"))
    return await get_backend(backend_type, backend_config)
```

For a comprehensive deep-dive into the abstraction layer, see [Vector Store Abstraction Architecture](VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md).

---

## Document Ingestion Pipeline

### Supported Document Formats

| Format | Handler | Notes |
|--------|---------|-------|
| PDF | PyPDF2 / pdfplumber | Text extraction with page-aware chunking |
| DOCX | python-docx | Preserves paragraph structure |
| TXT | Direct read | Plain text processing |
| Markdown | Direct read | Preserves heading structure |

### Upload Flow

```
1. Client uploads file via POST /api/v1/knowledge/collections/{id}/documents
   │
   ▼
2. DocumentUploadHandler.prepare_document_for_llm()
   │  - Extract text content from file format
   │  - Calculate SHA-256 content hash
   │  - Extract metadata (title, author if available)
   │
   ▼
3. RepositoryConstructor.construct()
   │  - Apply chunking strategy
   │  - Generate embeddings for each chunk (with ingest_epoch)
   │  - Store chunks + embeddings in ChromaDB
   │  - Save original document to disk
   │
   ▼
4. Register in knowledge_documents table
   │  - document_id, filename, title, author
   │  - category, tags, content_hash, ingest_epoch
   │  - source_uri (if provided — enables CDC sync)
   │  - sync_enabled = 1 when source_uri is set
   │  - Link to collection_id
   │
   ▼
5. SSE progress events emitted to client
   │  - Chunk count, embedding progress
   │  - Final completion status
```

Documents uploaded without a `source_uri` are static — they participate in manual re-upload only. Documents with a `source_uri` are registered for CDC sync and will be automatically checked on each sync interval.

### Chunking Strategies

The `RepositoryConstructor` (`agent/repository_constructor.py`) supports four chunking strategies:

#### 1. Paragraph-Based (Default)

The recommended strategy for most documents. Respects natural paragraph boundaries while maintaining consistent chunk sizes.

**Algorithm:**
1. Split content on double newlines (`\n\n`)
2. Combine small paragraphs until reaching `chunk_size` (default: 1000 chars)
3. If a paragraph exceeds `chunk_size × 1.5`, split by sentence boundaries
4. If fewer than 3 paragraphs exist in a very large document, fall back to fixed-size

**Properties:**
- Preserves semantic coherence within chunks
- Adapts to document structure
- Handles edge cases (single massive paragraph, many tiny paragraphs)

#### 2. Sentence-Based

Splits content at sentence boundaries (regex: `(?<=[.!?])\s+`). Best for dense technical content where precision matters more than narrative flow.

#### 3. Fixed-Size

Character-count chunking with configurable overlap. The overlap ensures that information spanning a chunk boundary is captured in at least one chunk.

**Parameters:**
- `chunk_size`: Characters per chunk (default: 1000)
- `chunk_overlap`: Overlap between consecutive chunks (default: 200)

#### 4. Semantic

Boundary-aware splitting that attempts to detect topic shifts. Uses structural cues (headings, section breaks) to find natural split points.

### Chunk Metadata

Each chunk stored in ChromaDB carries metadata for filtering and scoring:

```json
{
  "collection_id": 37,
  "document_id": "doc_abc123",
  "chunk_index": 3,
  "chunk_method": "paragraph",
  "chunk_size": 847,
  "title": "Product Roadmap 2026",
  "author": "Engineering Team",
  "filename": "roadmap_2026.pdf",
  "category": "product",
  "tags": "roadmap,2026,features",
  "created_at": "2026-01-15T10:30:00+00:00",
  "source": "upload",
  "strategy_type": "knowledge",
  "ingest_epoch": 1746220800
}
```

The `ingest_epoch` field is the Unix timestamp of the ingestion pass that created this chunk. CDC sync uses it to identify and delete stale chunks from previous versions of the same document (see [Ingest Epoch — Stale Chunk Cleanup](#ingest-epoch--stale-chunk-cleanup)).

### Embedding Model

Documents are embedded using **`all-MiniLM-L6-v2`** (384-dimensional vectors) via the `sentence-transformers` library. This model provides a strong balance of speed and quality for semantic similarity tasks.

ChromaDB stores both the embedding vectors and the original chunk text, enabling retrieval by semantic similarity with full content access.

A model already in the local HuggingFace cache loads without contacting the hub; an uncached model downloads on first use. The rule and its call sites are in [Vector Store Abstraction → SentenceTransformerProvider](VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md#sentencetransformerprovider).

---

## CDC Sync Infrastructure

Knowledge repositories support **Change Data Management (CDC) sync** — automatic detection of source document changes and selective re-embedding without manual re-upload. This turns static repositories into living knowledge bases that stay current as upstream documents evolve.

> **Scope**: CDC sync only detects changes to already-registered documents. It does **not** discover new files added to a directory. New file ingestion is handled by the agent pack build pipeline (see [Agent Pack Build Pipeline](#agent-pack-build-pipeline)).

### Overview

```
Source Document (file / HTTP / Google Drive)
          │
          ▼
  sync_knowledge_collection()   ← scheduled or manual trigger
          │
          ├─ Fetch content from source_uri
          ├─ SHA-256 hash comparison
          │
          ├── unchanged → mark last_checked_at, continue
          │
          └── changed → re-ingest via same pipeline as manual upload
                          │
                          ├─ client-side chunking: ingest_epoch cleanup removes stale chunks
                          └─ server-side chunking: delete + re-submit to backend
```

### Document-Level CDC Fields

Every knowledge document in the `knowledge_documents` table carries these CDC fields:

| Field | Type | Description |
|-------|------|-------------|
| `source_uri` | TEXT NULL | URI where the document can be fetched. `NULL` = upload-only (no sync) |
| `sync_enabled` | INTEGER (0/1) | Whether this document participates in CDC sync (default: 1 when `source_uri` is set) |
| `content_hash` | TEXT NULL | SHA-256 hex digest of the last ingested content bytes |
| `last_checked_at` | TEXT NULL | ISO 8601 timestamp of the last check (successful or not) |
| `ingest_epoch` | INTEGER NULL | Unix timestamp of the most recent ingest pass; used for stale chunk cleanup |

### Collection-Level CDC Fields

The `collections` table carries five CDC-related columns:

| Field | Type | Description |
|-------|------|-------------|
| `sync_interval` | TEXT | How often the scheduler fires (e.g. `daily`, `hourly`, `weekly`). Configurable in Platform Jobs UI. |
| `source_root` | TEXT NULL | Base directory for resolving relative `file://` URIs. `NULL` = use env var or auto-detect. |
| `embedding_model_locked` | INTEGER (0/1) | Set to `1` after first successful re-index or build import. Prevents the model-mismatch warning banner. |
| `sync_auto_ack_findings` | INTEGER (0/1) | The owner's standing policy: after a sync that changed content, acknowledge new injection findings on the changed documents (see *Signature renewal after a sync* below). Default `0`. |
| `sync_auto_resign` | INTEGER (0/1) | The owner's standing policy: after a sync that changed content, renew the repository's signature, the signatures of the agents that bind it and the seal of each pack that ships them, then carry the renewal to the packs' subscribers — a renewal only. Default `0`. |

### Signature renewal after a sync (Oct 2026)

A synced repository changes under its signature: every changed document is re-scanned by
the ingest firewall (a finding the owner had acknowledged on the old version comes back
open on the new one), the content hash moves, and so does the hash of every agent that
binds the repository. Three human acts were owed after every scheduled sync — and a
documentation corpus that legitimately stores injection examples owed them daily.

`core/trust/renewal.py` is the owner's standing policy for that, per repository, set from
the Task Scheduler's Platform Jobs tab (*After a sync that changes content*) and applied
by `sync_knowledge_collection` after the document loop (the result carries it as
`renewal`; the scheduler log line prints it; Sync Now renders it):

| Step | Policy | What happens |
|---|---|---|
| Carry-forward | *always* | On a re-ingest, a prior acknowledgement is carried onto the new scan record when the finding labels did **not** widen (`carry_forward_acknowledgement`, pure). The record says it was carried (`acknowledged_carried`), so the scan manifest — and the signature it feeds — tells a carried acknowledgement from a fresh judgement. A widened label set is a new question. Event `corpus_finding_ack_carried`. |
| Acknowledge | `sync_auto_ack_findings` | Open findings on the documents **this run changed** (never an unchanged or quarantined document) are acknowledged with a reason naming the run, the policy and the labels. One inbox event per finding (`corpus_finding_auto_ack`) — deliberately **not** a receipt: an automatic act counts as work to notice. |
| Renew | `sync_auto_resign` | The repository is re-validated and re-signed through the same act the sign-off route uses (`sign_knowledge_repository`), then every agent of the owner that consumes it (by the badge's own ingredient rule). **Renewal only**: an artefact never signed stays unsigned. Authority is the owner's as the route checks it (approver tier; refused under separation of duties). The validator's refusal and the bottom-up gate's refusal stand. Recorded as `event = "renew"` with `verdict.renewal` (run, repository, files, policy) — history reads *renewed by the scheduled sync*, never *signed by the owner*. Events `signature_renewed` / `signature_renewal_refused` (the refusal names the reason, so the act owed is named). |
| Carry | `sync_auto_resign` | **The renewal reaches the copies** (Oct 2026). Every installed pack of the owner that ships the repository or a renewed agent (`packs_carrying`, from `agent_pack_resources`) has its seal renewed over its members' new hashes through the sign-off route's aggregate gate (`renew_pack_seal`: every member 🟢, else refused with the blockers named; a pack never sealed is not sealed), and the pack's marketplace listing is fanned out to its subscribers through the publisher's OWN fan-out (`fan_out_subscribers` → `api.agent_pack_routes.fan_out_pack_sync`, the same act as `POST /v1/agent-packs/<id>/sync-subscribers`), which re-materializes each copy and reinstates the renewed attestations onto it by content hash. **Why**: a subscriber's copy binds the publisher's collection by reference, so its ingredient hash moves with every content-changing sync, and its attestation is its own (agent kinds are owner-scoped) — renewing the owner's agents alone left every subscriber's copy reading *changed* after every daily sync (the uderia_expert consumers re-signed @UDERIA by hand). Events `signature_renewed` (pack seal), `signature_renewal_refused` (unsigned members · a failed fan-out, naming *Sync subscribers* as the act owed), `signature_renewal_carried` (how many subscribers). A pack never published is not fanned out. |

Gate: `test/test_signature_renewal.py`.

### Supported Source URI Schemes

| Scheme | Example | Notes |
|--------|---------|-------|
| `file://` | `file://docs/Architecture/foo.md` | Local filesystem. Relative path (no leading `/`) resolved against `source_root`. |
| `https://` | `https://example.com/spec.pdf` | HTTP GET via `aiohttp` (fallback: `urllib`). |
| `http://` | `http://intranet/guide.md` | Same as `https://`. |
| `gdrive://` | `gdrive://<file_id>` | Google Drive via the platform connector. Requires user's Google account connected in Components → Connectors. |

New schemes are added by registering a coroutine in the `SOURCE_RESOLVERS` dict in `knowledge_sync.py` — no other files need changing.

### Relative `file://` URIs and Source Root

The agent pack build pipeline stores **root-relative paths** rather than absolute filesystem paths:

```
Before (absolute, breaks on any other machine):
  file:///Users/rainer/my_private_code/uderia/docs/Architecture/foo.md

After (root-relative, portable):
  file://docs/Architecture/foo.md
```

At sync time, `_fetch_local_file()` resolves relative paths (no leading `/`) against the **Source Root**, using a three-tier priority chain:

```
1. Collection source_root field   ← set via Platform Jobs UI (preferred)
2. UDERIA_DOCS_ROOT env var        ← server-level override
3. Auto-detect: Path(__file__).resolve().parents[3]   ← zero-config fallback
                                     (4 levels above knowledge_sync.py = repo root)
```

Absolute paths (leading `/` on Unix, `C:\` on Windows) bypass resolution and are used as-is.

### Sync Engine

**File**: `src/trusted_data_agent/core/knowledge_sync.py`

The sync engine exposes two public coroutines:

#### `sync_knowledge_collection(collection_id, user_uuid, older_than_seconds=3600, advance_schedule=True, trigger=None)`

Hash-check CDC sync. Only processes documents whose `last_checked_at` is older than `older_than_seconds`.

| Trigger | `older_than_seconds` | Effect |
|---------|----------------------|--------|
| Platform Jobs UI "Sync Now" button | `0` | Force-checks all documents regardless of last check time |
| APScheduler (`sync_interval`) | `3600` (default) | Throttles to max once per hour per document |

**Algorithm per document:**

```
1. Fetch content bytes from source_uri (via scheme resolver)
2. If fetch fails → log warning, increment errors, continue
3. SHA-256(content_bytes) == content_hash?
   → Yes: mark last_checked_at, increment unchanged, continue
   → No:  call _sync_upsert_document() to re-ingest
4. _sync_upsert_document():
   a. Write content to a temp file
   b. Client-side path: extract text → chunk → embed → upsert (new ingest_epoch)
      Then query for chunks with document_id == old AND ingest_epoch < new_epoch → delete
   c. Server-side path (Teradata EVS): delete existing doc → re-submit to add_document_files()
   d. Update knowledge_documents row with new content_hash, ingest_epoch, last_checked_at
```

**Return value:**
```json
{
  "checked": 85,
  "updated": 3,
  "unchanged": 81,
  "errors": 1,
  "duration_seconds": 4.2
}
```

#### `reindex_knowledge_collection(collection_id, user_uuid, strategy="in_place")`

Force re-embeds **every** document in the collection regardless of content hash. Used when changing the embedding model.

- Iterates all documents (not just sync-enabled ones)
- Skips documents with no `source_uri` (no source to re-fetch from)
- After completion: sets `embedding_model_locked = 1` on the collection, suppressing the model-mismatch warning banner in the UI

```json
{
  "strategy": "in_place",
  "reindexed": 82,
  "skipped": 3,
  "errors": 0,
  "duration_seconds": 47.8
}
```

### Ingest Epoch — Stale Chunk Cleanup

Each re-ingest pass generates a new `ingest_epoch = int(time.time())`. New chunks are stored with this epoch value in their metadata. After the upsert, the engine queries the backend for chunks belonging to the same `document_id` with an `ingest_epoch` **older than** the current epoch — those are stale chunks from a previous version of the document — and deletes them.

```python
stale_filter = AndFilter([
    FieldFilter("document_id", FilterOp.EQ, doc_id),
    FieldFilter("ingest_epoch", FilterOp.LT, ingest_epoch),
])
stale_ids = await backend.get(collection_name, where=stale_filter, ...).ids
if stale_ids:
    await backend.delete(collection_name, ids=stale_ids)
```

This ensures the vector store never accumulates duplicate embeddings of stale document versions.

### Agent Pack Build Pipeline

The Uderia Expert agent pack (`agent_packs/uderia_expert/build.py`) provides the primary ingestion path for file-based knowledge repositories. It performs a **full filesystem scan** on each run, discovering and registering all documents under the configured directory tree.

**Key behaviors:**

- **Full scan on every run**: The pipeline walks the entire directory tree. It registers new files, updates changed files, and removes deleted files. CDC sync does not discover new files — that's exclusively the pipeline's job.
- **Root-relative URIs**: The build pipeline stores `file://docs/Architecture/foo.md` (relative to the repo root) rather than absolute paths, making the knowledge base portable across machines and deployment environments.
- **Two invocation modes**:
  - `--import`: Full scan + register + embed all documents
  - `--update`: Re-scan and update changed files only

**PATCH loop in `do_import()`:**

After initial import, the build pipeline PATCHes each document's `source_uri` via the API:

```python
# build.py — do_import() CDC PATCH
rel_path = doc_info["rel_path"]   # e.g. "docs/Architecture/foo.md"
source_uri = f"file://{rel_path}" # e.g. "file://docs/Architecture/foo.md"
# PATCH /api/v1/knowledge/documents/{id}  body: {source_uri: source_uri, sync_enabled: true}
```

This wires every ingested document into the CDC sync system automatically.

### Platform Jobs UI — Source Root Configuration

The Platform Jobs panel (accessible via Components → Scheduler → Jobs) provides collection-level CDC controls under the **Platform Jobs** sub-tab.

For each knowledge collection, the detail panel shows:

```
Sync Interval       [  Daily      ▼ ]
Source Root
┌─────────────────────────────────────────────────────┐
│ /opt/uderia/                                  [Set] │
└─────────────────────────────────────────────────────┘
ℹ Relative file:// paths are resolved against this directory.
  Leave blank to auto-detect from the installation root.

[ Sync Now ]   [ Re-index ]
```

- **Source Root field**: Pre-populated from `collection.source_root`; placeholder shows `effective_source_root` (the resolved path the server will actually use given the three-tier chain)
- **Set button**: PATCHes `source_root` to the entered value (or `null` if cleared)
- **Sync Now**: Calls `POST /v1/knowledge/repositories/{id}/sync` with `older_than_seconds=0` — always force-checks all documents
- **Re-index**: Calls `POST /v1/knowledge/repositories/{id}/reindex` — re-embeds all documents from source

**`effective_source_root` in the API response:**

`GET /api/v1/rag/collections` returns an `effective_source_root` field for knowledge collections. This is the path the server will actually resolve relative `file://` URIs against, computed from the same three-tier chain. The UI uses this as the placeholder text so operators can verify what path is in effect before setting an explicit override.

### REST API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/knowledge/repositories/{id}/sync` | Trigger CDC sync. Optional `?older_than_seconds=N` (default 3600; 0 = force-check all) |
| `POST` | `/v1/knowledge/repositories/{id}/reindex` | Force re-embed all documents and lock embedding model |
| `PATCH` | `/v1/knowledge/repositories/{id}` | Update collection fields including `source_root`, `sync_interval`, `embedding_model_locked` |
| `GET` | `/v1/rag/collections` | List collections; knowledge collections include `source_root` and `effective_source_root` |

### Scheduler Integration

The Task Scheduler (APScheduler) fires `sync_knowledge_collection()` automatically based on each collection's `sync_interval`. The scheduler job is registered when the collection is created or when `sync_interval` is updated. Fired runs use the default `older_than_seconds=3600` throttle to avoid hammering sources on every tick.

---

## Retrieval Pipeline

### Vector Store Query

The retriever (`rag_retriever.py`) queries each knowledge collection independently. For ChromaDB collections, the query goes directly via the ChromaDB client. For non-ChromaDB collections (e.g., Teradata), the query routes through the **Vector Store Abstraction Layer** (`vectorstore/factory.py`):

```python
# ChromaDB collections (default path) — rag_retriever.py:retrieve_examples
# The query is embedded ONCE per embedding model (off the event loop) and the
# vector is handed to every collection — `query_texts=` would re-embed the
# same text inside each call (~0.1 s CPU each). Both the encode and each
# HNSW query go through asyncio.to_thread: ChromaDB is synchronous, and a
# blocked loop stalls every other session on the server (Aug 2026, prod:
# an unscoped scan over 283 collections froze the server ~27 s per turn).
_qvec = await _query_vector_for(embedding_model)          # cached per model
query_results = await asyncio.to_thread(
    collection.query,
    query_embeddings=[_qvec],   # falls back to query_texts=[query] only if the encoder fails
    n_results=k * RAG_OVERFETCH_MULTIPLIER,   # Over-fetch for post-filtering (3x)
    where=None,                 # Knowledge repos: no metadata filter
    include=["metadatas", "distances", "documents"],
)

# Non-ChromaDB collections — rag_retriever.py:retrieve_examples (vector-store path)
query_result = await backend.query(
    coll_name, query_text=query,
    n_results=k * RAG_OVERFETCH_MULTIPLIER,      # same 3x over-fetch
    embedding_provider=emb_provider,             # client-side embedding (Qdrant/Teradata)
    search_mode=SearchMode(coll.search_mode),    # semantic | hybrid (BM25)
    keyword_weight=coll.hybrid_keyword_weight,
)
```

**Key design choice**: Knowledge repositories use `where=None` (no metadata filter), unlike planner repositories which filter on `strategy_type`, `is_most_efficient`, and `user_feedback_score`. This ensures all uploaded knowledge is searchable.

### Candidate Processing

For each result returned by ChromaDB:

```python
distance = query_results["distances"][0][i]
similarity_score = 1 - distance          # Convert cosine distance to similarity

if similarity_score < min_score:
    continue                              # Below relevance threshold

candidate = {
    "case_id": chunk_id,
    "collection_id": collection_id,
    "content": chunk_text,
    "similarity_score": similarity_score,
    "metadata": chunk_metadata,
    "document_id": metadata.get("document_id"),
    "strategy_type": "knowledge"
}
```

### Multi-Collection Retrieval

When a profile is configured with multiple knowledge collections, the retriever:

1. Queries each collection independently
2. Collects all candidates into a single list
3. Applies scoring, deduplication, and sorting across all collections
4. Returns the top `maxDocs` results regardless of which collection they came from

This means collections compete on merit — the most relevant chunks surface regardless of their source collection.

---

## Scoring Algorithms

### Hybrid Scoring: Relevance + Freshness

Knowledge documents use a blended scoring model that combines semantic relevance with temporal freshness:

```
adjusted_score = (1 - freshnessWeight) × similarity_score + freshnessWeight × freshness_score
```

Where:
- `similarity_score` = `1 - cosine_distance` (range: 0.0 to 1.0)
- `freshness_score` = `exp(-freshnessDecayRate × days_old)` (range: 0.0 to 1.0)
- `freshnessWeight` = configurable blend ratio (default: 0.0 = pure relevance)

### Freshness Decay Model

The freshness score uses exponential decay, providing a natural time-based penalty:

```
freshness_score = e^(-decay_rate × days_old)
```

**Decay curve with default rate (0.005):**

| Document Age | Freshness Score | Impact |
|-------------|----------------|--------|
| Today | 1.000 (100%) | Maximum freshness |
| 1 week | 0.966 (97%) | Negligible penalty |
| 1 month | 0.861 (86%) | Mild penalty |
| 3 months | 0.638 (64%) | Moderate penalty |
| 6 months | 0.407 (41%) | Significant penalty |
| 1 year | 0.161 (16%) | Heavy penalty |
| 2 years | 0.026 (3%) | Near-zero freshness |

**When no date is available**: The system assigns a default freshness score of `0.5` (neutral), ensuring undated documents are neither advantaged nor severely penalized.

### Scoring Examples

**Example 1: Pure relevance (freshnessWeight = 0.0)**
```
adjusted_score = (1.0 × 0.82) + (0.0 × 0.95) = 0.82
→ Only semantic similarity matters
```

**Example 2: Balanced (freshnessWeight = 0.3)**
```
Doc A: similarity=0.85, 10 days old → freshness=0.951
  adjusted = (0.7 × 0.85) + (0.3 × 0.951) = 0.595 + 0.285 = 0.880

Doc B: similarity=0.90, 200 days old → freshness=0.368
  adjusted = (0.7 × 0.90) + (0.3 × 0.368) = 0.630 + 0.110 = 0.740

→ Doc A wins despite lower raw similarity (it's much newer)
```

**Example 3: Heavy freshness bias (freshnessWeight = 0.7)**
```
Doc A: similarity=0.60, 2 days old → freshness=0.990
  adjusted = (0.3 × 0.60) + (0.7 × 0.990) = 0.180 + 0.693 = 0.873

Doc B: similarity=0.95, 365 days old → freshness=0.161
  adjusted = (0.3 × 0.95) + (0.7 × 0.161) = 0.285 + 0.113 = 0.398

→ Very recent doc dominates even with low relevance
```

### Contrast: Planner Repository Scoring

Planner repositories use a different scoring model based on execution efficiency:

```
adjusted_score = similarity_score - penalties

Where penalties:
  - had_tactical_improvements: -0.05 (5%)
  - had_plan_improvements: -0.05 (5%)
```

This penalizes cases that required self-correction, preferring strategies that executed cleanly on the first attempt.

### Per-Document Deduplication

After scoring, the system applies per-document chunk limits to prevent a single document from dominating results:

```python
if max_chunks_per_doc > 0:
    doc_chunk_count = {}
    for case in sorted_candidates:
        doc_id = case.get("document_id", case["case_id"])
        count = doc_chunk_count.get(doc_id, 0)
        if count < max_chunks_per_doc:
            kept.append(case)
            doc_chunk_count[doc_id] = count + 1
        # else: skip — this document already contributed enough chunks
```

**Use case**: When a 50-page document has 5 chunks matching the query, `maxChunksPerDocument=2` ensures other documents also get representation in the final results.

---

## Configuration System

### Three-Tier Resolution

Knowledge retrieval configuration follows a strict precedence hierarchy:

```
┌─────────────────────────────────────────────┐
│  1. ADMIN LOCK (highest priority)           │
│     Global setting with lock = true         │
│     → Always uses global value              │
│     → Profile overrides ignored             │
├─────────────────────────────────────────────┤
│  2. PROFILE OVERRIDE                        │
│     Profile's knowledgeConfig has value     │
│     → Uses profile-specific value           │
├─────────────────────────────────────────────┤
│  3. GLOBAL DEFAULT (lowest priority)        │
│     Global setting value                    │
│     → Used when no profile override exists  │
└─────────────────────────────────────────────┘
```

**Implementation** (`config_manager.py`):

```python
def get_effective_knowledge_config(self, profile_knowledge_config=None):
    global_settings = self.get_knowledge_global_settings()
    effective = {}

    for key, global_config in global_settings.items():
        global_value = global_config['value']
        is_locked = global_config['is_locked']

        if is_locked:
            effective[key] = global_value              # Admin enforced
        else:
            profile_value = profile_knowledge_config.get(key)
            if profile_value is not None:
                effective[key] = profile_value          # Profile override
            else:
                effective[key] = global_value           # Global default

    return effective
```

### Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `maxDocs` | integer | 3 | Maximum documents returned after scoring and deduplication |
| `minRelevanceScore` | float | 0.30 | Minimum cosine similarity threshold (0.0–1.0). Chunks below this are discarded. |
| `maxTokens` | integer | 2,000 | Token budget for the formatted knowledge context sent to the LLM |
| `maxChunksPerDocument` | integer | 0 | Maximum chunks from any single source document. 0 = unlimited. |
| `freshnessWeight` | float | 0.0 | Blend ratio between relevance and freshness. 0.0 = pure relevance, 1.0 = pure freshness. |
| `freshnessDecayRate` | float | 0.005 | Exponential decay constant. Higher values penalize older documents more aggressively. |
| `synthesisPromptOverride` | string | "" | Custom system prompt for LLM synthesis. Replaces the default RAG_FOCUSED_EXECUTION prompt. |

### Profile Configuration Example

```json
{
  "profile_type": "rag_focused",
  "knowledgeConfig": {
    "enabled": true,
    "collections": [
      { "collectionId": 37, "reranking": false },
      { "collectionId": 38, "reranking": false }
    ],
    "maxDocs": 10,
    "minRelevanceScore": 0.25,
    "maxTokens": 8000,
    "maxChunksPerDocument": 2,
    "freshnessWeight": 0.3,
    "freshnessDecayRate": 0.005,
    "synthesisPromptOverride": "You are a domain expert. Answer using ONLY the provided documents..."
  }
}
```

### Context Window Manager Budget Override

When the Context Window Manager is enabled (`APP_CONFIG.USE_CONTEXT_WINDOW_MANAGER`), the `maxTokens` parameter is subject to an additional budget constraint. The context window orchestrator allocates a token budget to the `knowledge_context` module during its five-pass assembly. This budget caps the effective `maxTokens`:

```python
# engines/focus_engine.py (same in ideate_engine.py / conversation_engine.py) — CW budget override
cw_knowledge_budget = getattr(executor, '_cw_knowledge_max_tokens', None)
if cw_knowledge_budget:
    max_tokens = min(cw_knowledge_budget, max_tokens)
```

**Priority**: The lower of `maxTokens` (from configuration) and the CW module budget wins. This ensures knowledge context respects the overall context window allocation, preventing knowledge documents from crowding out conversation history or other modules.

### Tuning Guidelines

**For high-precision use cases** (compliance, legal, policy):
```
minRelevanceScore: 0.50    # Higher threshold, only very relevant docs
maxDocs: 5                  # Focused result set
freshnessWeight: 0.0        # Policy doesn't expire — pure relevance
maxChunksPerDocument: 1     # Diverse sources
```

**For broad knowledge bases** (product documentation, FAQ):
```
minRelevanceScore: 0.25    # Lower threshold to catch related content
maxDocs: 10                 # More documents for comprehensive answers
freshnessWeight: 0.3        # Prefer recent docs (product updates)
maxChunksPerDocument: 3     # Allow detailed coverage from key docs
```

**For time-sensitive content** (news, market intelligence, release notes):
```
minRelevanceScore: 0.30
maxDocs: 8
freshnessWeight: 0.5        # Strong freshness bias
freshnessDecayRate: 0.01    # Aggressive decay (6 months = 5% freshness)
maxChunksPerDocument: 2
```

---

## LLM Synthesis

### System Prompt

The Focus profile uses the `RAG_FOCUSED_EXECUTION` system prompt (stored encrypted in the database). This prompt instructs the LLM to:

1. Answer **exclusively** from the provided knowledge context
2. Cite sources with document titles and metadata
3. Acknowledge when the knowledge base doesn't contain sufficient information
4. Maintain conversational context from prior turns in the session

### Synthesis Prompt Override

Profiles can specify a `synthesisPromptOverride` to customize the LLM's behavior for domain-specific needs. This completely replaces the default system prompt.

**Use cases:**
- Enforce specific citation formats
- Require structured output (tables, bullet points)
- Add domain-specific instructions ("Always mention regulatory requirements")
- Control tone and formality

### Context Assembly

The LLM receives a message structured as:

```
[System Prompt]
  RAG_FOCUSED_EXECUTION (or synthesisPromptOverride)

[Knowledge Context]
  === Document 1 ===
  Title: Product Roadmap 2026
  Source: roadmap_2026.pdf
  ---
  [chunk content]

  === Document 2 ===
  Title: Technical Architecture Guide
  Source: architecture.md
  ---
  [chunk content]

  ... up to maxDocs documents, within maxTokens budget

[Conversation History]
  Previous turns in the session (if any)

[User Query]
  "What new features are planned for Q2?"
```

### Agent-Based Synthesis with Component Tools

When a `rag_focused` (or `llm_only` with knowledge) profile has **component tools** enabled (e.g., TDA_Charting, TDA_Canvas), the synthesis step routes through `ConversationAgentExecutor` instead of a direct LLM call. This allows the LLM to invoke component tools during synthesis — for example, visualizing retrieved data as a chart.

```
Standard synthesis:     Knowledge Context → LLM → Answer
Agent-based synthesis:  Knowledge Context → LLM Agent → [TDA_Charting] → Answer + Chart
```

**Implementation** (`engines/focus_engine.py`, `FocusEngine.run`):

```python
rag_component_tools = get_component_langchain_tools(profile_id, user_uuid)

if rag_component_tools:
    # Agent-based synthesis — LLM can call component tools
    rag_agent = ConversationAgentExecutor(
        mcp_tools=rag_component_tools,
        knowledge_context=knowledge_context,
        profile_type="rag_focused",
        ...
    )
    agent_result = await rag_agent.execute(user_query)
    # success False -> the agent already answered with the honest sentence; the turn is failed
else:
    # Standard direct LLM synthesis (no tools)
    response_text, *_ = await executor._call_llm_and_update_tokens(
        prompt=user_message, raise_on_error=True, ...)
```

**Events emitted** by agent-based synthesis:
- `conversation_agent_start` with `profile_type: "rag_focused"` (branded as "Focus Agent" in UI)
- `conversation_llm_step` (per LLM call within the agent loop)
- `conversation_tool_invoked` / `conversation_tool_completed` (if component tools are called)
- `conversation_agent_complete` (final summary)

When the agent path is active, the executor skips emitting its own `rag_llm_step` and `tool_result` events to avoid duplication.

### Token Tracking

Token counts are extracted from the actual LLM response (not pre-estimated):

- **Input tokens**: System prompt + knowledge context + conversation history + user query
- **Output tokens**: LLM's synthesized answer

These counts are accumulated per-session and emitted as `token_update` events for the UI's real-time token counters.

---

## Multi-User Access Control

### Collection Visibility

Knowledge collections follow a multi-tenant access model:

| Visibility | Who Can Access |
|------------|---------------|
| **Private** | Owner only |
| **Unlisted** | Anyone with the collection ID |
| **Public** | All authenticated users |
| **Admin-owned** (owner_user_id = NULL) | All authenticated users |

### Access Resolution at Query Time

```python
def _get_user_accessible_collections(self, user_id):
    accessible = []
    for collection in all_collections:
        if collection.owner_user_id is None:          # Admin-owned
            accessible.append(collection.id)
        elif collection.owner_user_id == user_id:     # User owns it
            accessible.append(collection.id)
        elif collection.visibility in ["public", "unlisted"]:
            accessible.append(collection.id)
        elif has_active_subscription(user_id, collection.id):
            accessible.append(collection.id)          # Marketplace subscription
    return accessible
```

At query time, the retriever intersects the user's accessible collections with the profile's configured collection IDs, ensuring no unauthorized data leaks into results.

### RAGAccessContext

Access control is encapsulated in the `RAGAccessContext` class (`agent/rag_access_context.py`), which is passed to `retrieve_examples()` at query time:

```python
# engines/focus_engine.py — RAGAccessContext initialization
from trusted_data_agent.agent.rag_access_context import RAGAccessContext
rag_context = RAGAccessContext(user_id=executor.user_uuid, retriever=executor.rag_retriever)

all_results = await executor.rag_retriever.retrieve_examples(
    query=executor.original_user_input,
    rag_context=rag_context,
    repository_type="knowledge",
    ...
)
```

The `RAGAccessContext` provides:
- **`accessible_collections`** (cached property): Set of collection IDs the user can read (owned + subscribed + public)
- **`validate_collection_access(collection_id, write=False)`**: Validates read (or, with `write=True`, write) access before operations
- **Caching**: Accessible collections are computed once per context and cached for the request lifecycle

**`rag_context=None` is NOT "no filter" — it is "no scope".** Without a context, `retrieve_examples()` iterates every loaded collection of every user, so a caller that cannot build a context must return nothing rather than call the retriever (the recommender's Tier B does exactly that since Aug 2026 — its context import once pointed at `agent.rag_retriever`, where the class does not exist, the `ImportError` was swallowed, and every turn ran unscoped: other users' private champion cases became candidates, and the scan cost ~27 s per turn). Always import the class from `agent/rag_access_context.py`. Gates: `test/test_rag_retrieve_embed_once.py` (scoped-only querying) and `test_recommender.py::test_tier_b_access_scope` (context always passed; fail closed).

### Marketplace Integration

Collections published to the Intelligence Marketplace can be subscribed to by other users. Subscribed collections appear in the user's accessible set and can be assigned to their profiles.

---

## Event System

Knowledge retrieval emits events for real-time UI updates. The event flow varies depending on the execution path.

### Standard Event Flow (Direct LLM Synthesis)

```
execution_start
  │  → { profile_type: "rag_focused", knowledge_enabled: true }
  ▼
knowledge_retrieval_start
  │  → UI shows "Retrieving knowledge..."
  ▼
knowledge_retrieval_complete
  │  → UI shows retrieved document titles
  │  → UI updates knowledge banner
  ▼
rag_llm_step (synthesis)
  │  → UI shows "Synthesizing answer..."
  │  → Token counts emitted
  ▼
token_update
  │  → UI updates token counters
  ▼
final_answer
  │  → UI renders answer with citations
  ▼
execution_complete
  │  → UI clears execution indicators
```

### Agent-Based Event Flow (Component Tools Active)

When component tools are enabled, the synthesis step emits agent events instead:

```
execution_start
  │
  ▼
knowledge_retrieval_start → knowledge_retrieval_complete
  │
  ▼
conversation_agent_start          ← "Focus Agent Started"
  │
  ├── conversation_llm_step       ← LLM call (may invoke tools)
  ├── conversation_tool_invoked   ← e.g., TDA_Charting called
  ├── conversation_tool_completed ← Tool result
  ├── conversation_llm_step       ← Follow-up LLM call
  │
  ▼
conversation_agent_complete       ← "Focus Agent Complete"
  │
  ▼
execution_complete
```

Note: When the agent path is active, `rag_llm_step` and `tool_result` events are **not** emitted by the executor (the agent's events replace them).

### Ideate Profile Event Flow (`llm_only` with Knowledge)

```
execution_start
  │  → { profile_type: "llm_only", knowledge_enabled: true }
  ▼
knowledge_retrieval (notification)
  │  → Retrieval progress
  ▼
conversation_llm_step (synthesis)
  │  → Token counts
  ▼
execution_complete
```

### Key Events

| Event | Payload | Purpose |
|-------|---------|---------|
| `execution_start` | `{ profile_type, profile_tag, knowledge_enabled, knowledge_collections }` | Initialize UI execution state |
| `knowledge_retrieval_start` | `{ collection_count }` | Show retrieval indicator |
| `knowledge_retrieval_complete` | `{ results_count, documents[], sources[] }` | Populate knowledge panel |
| `rag_llm_step` | `{ input_tokens, output_tokens, model, cost_usd }` | Token tracking (direct path only) |
| `conversation_agent_start` | `{ profile_type, available_tools[] }` | Agent start (component tools path) |
| `conversation_agent_complete` | `{ tools_used[], input_tokens, output_tokens, cost_usd }` | Agent complete (component tools path) |
| `token_update` | `{ total_input, total_output }` | Cumulative token counters |
| `final_answer` | `{ html, sources[], tts_payload }` | Render answer |
| `execution_complete` | `{ duration_ms, tokens }` | Cleanup UI state |

### Session UI Cache Integration

When a user switches away from a session with active knowledge retrieval, the full execution state (including knowledge banner, retrieved documents, token counters) is cached in `state.sessionUiCache`. Switching back restores the complete UI state without re-fetching from the server.

---

## Comparison: Knowledge vs Planner Repositories

| Aspect | Planner Repositories | Knowledge Repositories |
|--------|---------------------|----------------------|
| **Profile class** | Optimize (`tool_enabled`) | Focus (`rag_focused`) + Ideate (`llm_only` opt-in) |
| **Purpose** | Self-improving execution strategies | Grounded document retrieval |
| **Data source** | Auto-captured execution traces | Uploaded documents (PDF, DOCX, TXT, MD) + CDC-synced from file:// / https:// / gdrive:// |
| **Storage format** | JSON case files on disk + ChromaDB | Chunks in ChromaDB only |
| **MCP server required** | Yes (tied to specific MCP server) | No |
| **Chunking** | None (full case as single document) | Paragraph / sentence / fixed-size / semantic |
| **Metadata filters** | `strategy_type=successful`, `is_most_efficient`, `user_feedback_score` | None (all chunks searchable) |
| **Scoring model** | Similarity - efficiency penalties | Hybrid similarity + freshness |
| **Tool execution** | Yes (full MCP tool calling) | None |
| **Self-correction** | Yes (retry loops with LLM guidance) | No |
| **RAG mining** | Yes (successful *data-acquisition* turns saved as cases; presentation-only turns excluded — see RAG.md "Plan-based seeding gate") | No |
| **Reranking** | No | Optional LLM-based reranking per collection |
| **Typical token cost** | 8,000–15,000 input per turn | 3,000–5,000 input per turn |

---

## Performance Characteristics

### Typical Query Breakdown

| Operation | Input Tokens | Output Tokens | Latency |
|-----------|-------------|---------------|---------|
| Configuration resolution | — | — | <1ms |
| ChromaDB semantic search | — | — | 50–200ms |
| Scoring & deduplication | — | — | <10ms |
| LLM synthesis | 3,000–5,000 | 200–800 | 1–3s |
| **Total** | **3,000–5,000** | **200–800** | **1–4s** |

### Scaling Factors

- **Collection size**: ChromaDB handles millions of chunks with sub-second query times
- **Number of collections**: Each collection is queried independently; more collections = linear increase in query time
- **maxDocs**: Higher values increase LLM synthesis input tokens and cost
- **maxTokens**: Caps the context sent to LLM regardless of maxDocs
- **Document count**: ChromaDB's HNSW index maintains fast approximate nearest-neighbor search at scale

### Token Efficiency vs Other Profile Types

| Profile Type | Typical Input Tokens | Typical Output Tokens |
|-------------|---------------------|----------------------|
| Conversation (`llm_only`) | ~2,000 | 200–500 |
| **Knowledge (`rag_focused`)** | **~3,000–5,000** | **200–800** |
| Conversation with tools (`llm_only` + MCP) | ~3,000–4,000 | 200–500 |
| Optimizer (`tool_enabled`) | ~8,000–15,000 | ~1,000 |

---

## Advanced Features

### LLM-Based Reranking

When enabled per collection, the system performs a second-pass reranking using the LLM:

1. ChromaDB returns top candidates (quantity: `maxDocs × 3`)
2. Candidates are sent to the LLM with the original query
3. LLM scores each candidate for relevance
4. Results reordered by LLM relevance score
5. Top `maxDocs` returned

**Trade-off**: Improved relevance at the cost of an additional LLM call (typically 1,000–2,000 extra input tokens).

### Synthesis Prompt Override

Profiles can inject domain-specific synthesis instructions. This is particularly valuable for:

- **Multi-expert systems** (e.g., Virtual Account Team): Each expert profile gets a specialized prompt that shapes answer style, depth, and focus area
- **Compliance profiles**: Enforce specific citation and disclaimer formats
- **Multilingual use cases**: Instruct the LLM to respond in a specific language

### Session Context Continuity

The Focus profile maintains full session context across turns:

- **Turn 1**: "What products support AI workloads?" → Retrieves and synthesizes
- **Turn 2**: "Tell me more about the first one" → Uses conversation history to resolve "the first one", retrieves relevant chunks

The conversation history is included in the LLM synthesis call, enabling natural multi-turn interactions without losing context. It is built by `agent/agent_history.build()` — the one rule shared with the ReAct lane and the coordinator (Oct 2026): assistant turns stored under Gemini's `model` role count as answers (Focus had dropped them, so a follow-up re-answered turn 1), only the trailing current question is removed, the priming pair and turned-off (`isValid: false`) messages are skipped, and **Turn Summaries mode** (Alt key, `executor.disabled_history`) sends no history at all. Gate: `test/test_agent_history.py`.

### No-Results Handling

When retrieval returns zero results above the minimum relevance threshold:

1. The system emits a `knowledge_retrieval_complete` event with empty results
2. A user-friendly message is generated (not an error)
3. The turn is still saved to conversation history
4. Token counts reflect only the system prompt + empty context overhead

This transparent failure mode prevents hallucination — the system never fabricates answers when it lacks supporting evidence.

**The no-results answer runs the trust tail (Oct 2026).** The early return applies the same outbound guard, assurance stamps (integrity) and provenance seal as a normal answer, and the model-gap note (`executor._apply_model_gap`) when the subject is missing from the ontology as well. Before this a question the corpus could not answer was delivered unsealed and unscreened (trust campaign C19). Gate: `test/test_focus_no_results_trust_tail.py`.

**A provider failure is a failed turn.** The direct synthesis call uses `raise_on_error=True`; a failure is answered with the honest sentence from `agent/turn_failure.answer()` (never the raw error) and the turn is recorded as failed — Focus had recorded an empty answer as a success (trust campaign F1/F2). See [LLM_PROVIDER_ARCHITECTURE.md](LLM_PROVIDER_ARCHITECTURE.md). Gates: `test/test_turn_failure.py`, `test/test_fault_injection.py`.

**An attached document is evidence too (Oct 2026).** The no-results answer is given only when the turn
carries nothing usable of its own. When the user attached a document (or an image the model sees), a
question about it is answered from it: `PlanExecutor.attached_material()` says what the attachments give
the model — the document text the turn sends, S9's readings, an image seen natively — and Focus goes on to
synthesis with a knowledge block stating that no repository held anything on the question (and not to
present the attachment as the repositories). The retrieval step reads *No relevant documents in N knowledge
repositories — answering from the attached document(s)* (`answered_from_attachments` on the event and the
persisted turn; Live Status adds an *Answered from* row). The same material is evidence for the gates on
EVERY engine: the grounding gate counts the attached text as support on a turn it grades anyway
(`run_grounding(attached_texts=…)`; attachments alone never make a turn groundable), and the
knowledge-sufficiency honesty check counts only the specifics the attachment does not carry (an image no
check can read ⇒ no finding). Before this, a correct answer quoted from the attached contract was withheld
as "unsupported by the knowledge base" whenever the repositories also returned something. The
after-the-turn scores (Agent Performance) read the same evidence: the turn records which attachments it
read and the scorers read their text through the guarded manifest (FILES_ARCHITECTURE §3, invariant 6). Gate:
`test/test_focus_attachments.py`.

---

## Troubleshooting

### No Results Returned

**Symptom**: Query returns "no relevant knowledge found" despite documents being uploaded.

**Checks**:
1. **Collection IDs**: Verify the profile's `knowledgeConfig.collections` references correct IDs. After cleanup/reimport, IDs change.
2. **Server restart**: The RAGRetriever loads collections into memory at startup. After creating new collections, the server must be restarted.
3. **minRelevanceScore**: A threshold of 0.50+ may be too restrictive. Try lowering to 0.25.
4. **Embedding mismatch**: Ensure documents were embedded with the same model used for queries (`all-MiniLM-L6-v2`).

### Wrong Documents Returned

**Symptom**: Retrieved documents seem irrelevant to the query.

**Checks**:
1. **Chunking strategy**: Very large chunks dilute semantic signal. Try smaller `chunk_size` or sentence-based chunking.
2. **freshnessWeight**: If set too high, old but relevant documents are penalized. Check if the returned docs are simply newer.
3. **maxChunksPerDocument**: If set to 0 (unlimited), one document with many chunks can dominate results.

### Freshness Scoring Not Working

**Symptom**: All documents have the same adjusted score regardless of age.

**Checks**:
1. **freshnessWeight = 0.0**: The default disables freshness scoring entirely. Set to 0.1+ to activate.
2. **Missing dates**: If `created_at` metadata is missing or malformed, the system assigns a neutral freshness score of 0.5.
3. **Date format**: Dates must be ISO 8601 format (e.g., `2026-01-15T10:30:00+00:00`). Truncated dates cause parsing failures.

### Configuration Not Taking Effect

**Symptom**: Profile overrides appear to be ignored.

**Checks**:
1. **Admin lock**: Check the global Knowledge Repository Configuration panel. If the lock checkbox is enabled for a parameter, the global value overrides all profile settings.
2. **Server restart**: Configuration changes to profiles require a server restart to take effect in the executor.
3. **Diagnostic logging**: Check server logs for `[Executor] Effective knowledge config:` to see what values are actually being used.

### High Token Cost

**Symptom**: Focus profile queries consume more tokens than expected.

**Optimization**:
1. Lower `maxDocs` (fewer documents = less context)
2. Lower `maxTokens` (caps context regardless of doc count)
3. Use `maxChunksPerDocument` to limit repetitive content from single sources
4. Increase `minRelevanceScore` to filter out marginally relevant chunks

### Sync Now Returns 0 Checked

**Symptom**: Clicking "Sync Now" in Platform Jobs returns `checked=0`.

**Checks**:
1. **No sync-enabled documents**: Documents only appear as sync candidates if they have a `source_uri` and `sync_enabled = 1`. Documents uploaded without a source_uri are skipped.
2. **Throttle not bypassed**: Only the "Sync Now" UI button passes `older_than_seconds=0`. Verify the button is calling `POST /v1/knowledge/repositories/{id}/sync` (the trigger endpoint), not the scheduled path.

### Sync Now Returns Stale Count (0 Updated Despite File Changes)

**Symptom**: `checked=85 unchanged=85 updated=0` even after editing source files.

**Checks**:
1. **Wrong source_root**: The sync engine may be reading from the wrong directory and finding the original files. Check `effective_source_root` in the collection API response.
2. **Absolute URI on a different path**: If `source_uri` contains an absolute path (`file:///old/path/...`) that still exists and contains old content, the hash matches the stored hash. Re-run the agent pack build pipeline (`--import`) to update all URIs to root-relative form, then set `source_root` in Platform Jobs.

### CDC Sync Fetch Errors (`file not found`)

**Symptom**: Sync log shows `Fetch failed for 'filename' (doc=...): [Errno 2] No such file or directory`.

**Resolution**:
1. **Set Source Root explicitly**: Go to Platform Jobs → collection detail → Source Root field. Enter the absolute path to the root directory of your Uderia installation (e.g. `/opt/uderia`). Click Set.
2. **Verify the resolved path**: `effective_source_root` in the API response shows what the server will resolve against. The resolved path should be `{effective_source_root}/docs/Architecture/foo.md`.
3. **Set environment variable**: Alternatively, set `UDERIA_DOCS_ROOT=/opt/uderia` in the server's environment. This applies to all collections without a per-collection override.

### CDC Sync Fetch Errors (`Google account not connected`)

**Symptom**: `gdrive://` sync returns `Google account not connected`.

**Resolution**: Visit Components → Connectors → Google and connect your Google account via the OAuth flow. The connected account must have read access to the Drive files referenced by the `gdrive://{file_id}` URIs.

---

## File Reference

### Core Retrieval

| File | Key Symbols | Description |
|------|-------------|-------------|
| `src/trusted_data_agent/agent/engines/focus_engine.py` | `FocusEngine.run()` | RAG-focused execution path (retrieval, rerank, attachments, synthesis, no-results tail) |
| `src/trusted_data_agent/agent/engines/ideate_engine.py` | `IdeateEngine.run()` | llm_only knowledge retrieval path (direct) |
| `src/trusted_data_agent/agent/engines/conversation_engine.py` | `ConversationEngine` | llm_only knowledge path with MCP/component tools (ReAct) |
| `src/trusted_data_agent/agent/executor.py` | `documents_for_turn()`, `attached_material()`, `_rerank_knowledge_with_llm()` | Shared executor services used by the engines |
| `src/trusted_data_agent/agent/agent_history.py` | `build()`, `role_of()` | Conversation history handed to Focus synthesis / ReAct / Coordinate |
| `src/trusted_data_agent/agent/rag_retriever.py` | `RAGRetriever.retrieve_examples()`, `RAG_OVERFETCH_MULTIPLIER` | Full retrieval with scoring and filtering (ChromaDB and vector-store paths) |
| `src/trusted_data_agent/agent/rag_access_context.py` | `RAGAccessContext` | Access control encapsulation |
| `src/trusted_data_agent/agent/conversation_agent.py` | `ConversationAgentExecutor` | Agent-based synthesis |
| `src/trusted_data_agent/agent/repository_constructor.py` | `DocumentProcessor`, `KnowledgeRepositoryConstructor` | Chunking strategies (ingest_epoch stamped on each chunk) |
| `src/trusted_data_agent/core/config_manager.py` | `get_effective_knowledge_config()` | Three-tier config resolution |
| `src/trusted_data_agent/core/collection_utils.py` | — | Collection utility functions |
| `src/trusted_data_agent/api/knowledge_routes.py` | `upload_knowledge_document()`, `upload_knowledge_document_stream()`, `trigger_knowledge_sync()` | Document upload API; manual sync endpoint |
| `src/trusted_data_agent/vectorstore/base.py` | `VectorStoreBackend` | Abstract interface |
| `src/trusted_data_agent/vectorstore/factory.py` | `get_backend()`, `get_backend_for_collection()` | Backend factory with singleton caching |
| `src/trusted_data_agent/vectorstore/capabilities.py` | `VectorStoreCapability` | Capability enum |
| `components/builtin/context_window/modules/knowledge_context/handler.py` | — | CW knowledge module (budget allocation, retrieval firewall, circuit breaker) |

### CDC Sync Infrastructure

| File | Key Symbols | Description |
|------|-------------|-------------|
| `src/trusted_data_agent/core/knowledge_sync.py` | `_fetch_local_file()` | file:// resolver with source_root chain |
| `src/trusted_data_agent/core/knowledge_sync.py` | `_fetch_http()` | http/https resolver (aiohttp + urllib fallback) |
| `src/trusted_data_agent/core/knowledge_sync.py` | `_fetch_google_drive()` | gdrive:// resolver via platform connector |
| `src/trusted_data_agent/core/knowledge_sync.py` | `SOURCE_RESOLVERS`, `fetch_source()` | Scheme → resolver registry and dispatch |
| `src/trusted_data_agent/core/knowledge_sync.py` | `sync_knowledge_collection()` | Hash-check CDC sync |
| `src/trusted_data_agent/core/knowledge_sync.py` | `_sync_upsert_document()`, `_carry_acknowledgement()` | Re-ingest a changed document (client + server-side paths); carry label acknowledgements forward |
| `src/trusted_data_agent/core/knowledge_sync.py` | `superseded_chunk_ids()`, `_remove_superseded_chunks()`, `_sweep_backend()` | Ingest-epoch stale chunk cleanup |
| `src/trusted_data_agent/core/knowledge_sync.py` | `reindex_knowledge_collection()` | Force re-embed + lock embedding model |
| `src/trusted_data_agent/core/trust/renewal.py` | — | Signature renewal after a content-changing sync (see "Signature renewal after a sync") |
| `src/trusted_data_agent/core/collection_db.py` | `get_sync_candidates()`, `mark_document_checked()`, `upsert_document_metadata()`, `upsert_document_metadata_preserving_cdc()` | CDC metadata |
| `src/trusted_data_agent/auth/database.py` | — | Auto-migration: adds `source_root TEXT DEFAULT NULL` to collections table on startup |
| `src/trusted_data_agent/api/rest_routes.py` | `get_rag_collections()` | Adds `source_root` + `effective_source_root` to knowledge collections |
| `agent_packs/uderia_expert/build.py` | `process_docs()`, `do_import()` | Stores root-relative `file://` URIs; CDC PATCH loop |
| `static/js/handlers/jobsHandler.js` | — | Platform Jobs UI: Source Root field, Sync Now, Re-index buttons |

---

*Last updated: 2026-10-30*
