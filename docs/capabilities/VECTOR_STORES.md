# 🗄️ Vector Store Abstraction Layer

Uderia's RAG and Knowledge capabilities are powered by a **pluggable vector store layer** that decouples all embedding, storage, and retrieval operations from any single database vendor. This means you can start with the built-in local store and scale to enterprise infrastructure — without changing a single profile, collection, or workflow.

**Three production backends, one unified interface:**

| | **ChromaDB** (Default) | **Qdrant Cloud** | **Teradata Enterprise Vector Store** |
|---|---|---|---|
| **Best for** | Local / single-user / getting started | Cloud-native / managed vector search | Enterprise / shared infrastructure / governed data |
| **Deployment** | Embedded, zero-config | Managed cloud (Qdrant Cloud) | Server-side, connects to existing Teradata environment |
| **Embedding** | Client-side (SentenceTransformer) | Client-side (SentenceTransformer) | Server-side (Amazon Bedrock or Azure AI) — no local GPU needed |
| **Chunking** | Client-side (Uderia-managed) | Client-side (Uderia-managed) | Client-side *or* server-side — pass raw files and let the database handle it |
| **Search Modes** | Semantic | Semantic, Hybrid (semantic + BM25 keyword) | Semantic, Hybrid (native server-side BM25 on server-side chunked collections; runtime-side RRF otherwise) |
| **Scaling** | Single-node | Cloud-managed, horizontal scaling | Massively parallel, leverages Teradata's query engine |

**Why this matters:**

* **No vendor lock-in.** Collections created on ChromaDB work identically on Qdrant or Teradata. Switch backends by changing a configuration — existing profiles, RAG templates, and marketplace packs continue to work unchanged.
* **From laptop to cloud to enterprise.** Start local with ChromaDB, move to Qdrant Cloud for managed scalability, or deploy on Teradata for governed enterprise infrastructure — all without touching your workflows. The Teradata backend adds server-side embedding (no local GPU costs), server-side chunking (upload raw PDFs and let the database handle splitting), and connection resilience with automatic stale-connection detection and serialized reconnect to survive transient network issues.
* **Hybrid Search.** Multiple backends support hybrid search combining dense vector similarity with BM25 keyword matching. Qdrant Cloud builds the BM25 sparse vector in the runtime and fuses the dense and sparse legs on the Qdrant server with its Reciprocal Rank Fusion (RRF), unweighted. Teradata EVS uses **native server-side BM25** — the lake server performs RRF fusion internally, so no embedding or scoring happens on the runtime side. Native server-side BM25 needs a server-side chunked collection (FILE_CONTENT_BASED); client-side chunked collections get hybrid search through runtime-side RRF over a lexical search of their staging table.
* **Teradata server-side chunking control.** When using the Teradata EVS backend, you can choose between *Optimized* (structure-aware dynamic chunking that follows document layout) and *Fixed Size* (character-based splitting with configurable chunk size). Header and footer trimming is available for PDF documents to exclude page headers/footers before chunking. All parameters are persisted at the collection level and editable per-upload.
* **Asynchronous counters.** For Teradata server-side ingestion, document and chunk counts update asynchronously. After a document upload completes, the runtime polls the EVS backend at 30s, 60s, and 90s intervals to retrieve the final chunk count. During this window, the Knowledge Repository card may temporarily show stale counts — a notification is emitted once the actual count is available.
* **Capability-based negotiation.** Each backend declares what it supports (e.g., `SERVER_SIDE_CHUNKING`, `GET_ALL`, `GET_BY_METADATA_FILTER`). Uderia adapts its behavior automatically — no feature flags or conditional code in your workflows.
* **Safe concurrent access.** The factory uses config-fingerprinted singleton caching with per-key async locks, ensuring multiple sessions never race to initialize the same backend.

For the full architectural deep-dive — including ingestion paths, connection resilience patterns, and the EVS object ownership model — see:
[**Vector Store Abstraction Architecture (docs/Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md)**](../Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md)
