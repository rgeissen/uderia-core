# 🧬 Retrieval-Augmented Generation (RAG) for Self-Improving AI

Uderia integrates a **Retrieval-Augmented Generation (RAG)** system designed to create a self-improving agent. This closed-loop feedback mechanism allows the agent's Planner to learn from its own past successes, continuously enhancing its decision-making capabilities over time.

The core value of this RAG implementation is its ability to automatically identify and leverage the most efficient strategies for given tasks. It works by:

1.  **Capturing and Archiving:** Every successful agent interaction is captured and stored as a "case study."
2.  **Analyzing Efficiency:** The system analyzes each case based on token cost to determine its efficiency.
3.  **Identifying Champions:** It identifies the single "champion" strategy for any given user query.
4.  **Augmenting Future Prompts:** When a similar query is received in the future, the system retrieves the champion case and injects it into the Planner's prompt as a "few-shot" example.

This process guides the Planner to generate higher-quality, more efficient plans based on proven, successful strategies, reducing token consumption and improving response quality without manual intervention. The entire process runs asynchronously in the background to ensure no impact on user-facing performance.

**Each agent learns on its own.** Every new Optimize agent gets its own planner repository: its lessons go only there, it reads that repository beside the ones it binds, and it never steers another agent. An agent that binds no planner repository and has no learning repository of its own uses no proven examples. Existing agents keep the shared repository until their owner opts in. [Where an agent learns →](../RAG/RAG.md)

## Two-Tier Repository Architecture

The application supports two distinct types of repositories, each serving a different purpose in the AI agent ecosystem:

### Planner Repositories
**Purpose:** Store execution strategies and planning patterns
- Capture successful agent interactions as few-shot learning examples
- Contain SQL query patterns, API workflows, and proven execution traces
- Retrieved by the RAG system to guide future planning decisions
- Built via **Planner Repository Constructors** - modular templates for domain-specific pattern generation
- Automatically populated from agent execution history or manually via REST API
- Enable the agent to learn from past successes and improve over time
- Available in [Intelligence Marketplace](MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) for community sharing

### Knowledge Repositories
**Purpose:** Provide reference documentation and domain knowledge
- Store general documents, technical manuals, and business context
- Support for PDF, TXT, DOCX, MD, and other document formats
- Configurable chunking strategies (fixed-size, paragraph, sentence, semantic — semantic currently chunks by paragraph)
- Integrated with strategic planning: retrieved cases are injected into the planner's context
- Retrieved during planning to inject domain context into strategic decision-making
- Enable the agent to query relevant background information when making decisions
- Available in [Intelligence Marketplace](MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) for community sharing

Both repository types are available in the [Intelligence Marketplace](MARKETPLACE.md#-intelligence-marketplace-collaborative-knowledge-sharing) for community sharing, discovery, and collaboration. The marketplace enables reference-based subscriptions, forking for customization, ratings and reviews, and flexible publishing options. This separation ensures that execution patterns (how to accomplish tasks) remain distinct from domain knowledge (what the agent needs to know), while both can be leveraged through the unified RAG system.

## Planner Repository Constructors: Modular Plugin System

The RAG system now features a **modular template architecture** that enables domain-specific customization and extensibility:

* **Plugin-Based Design**: Templates are self-contained plugins with their own schemas, validation logic, and population strategies
* **Template Types**: Support for SQL query templates, with extensibility for document Q&A, API workflows, and custom domains
* **Manifest System**: Each template declares its capabilities, required fields, and validation rules via a standardized manifest
* **Registered Loading**: Templates listed in `rag_templates/template_registry.json` are loaded at startup and on hot-reload (`POST /v1/rag/templates/reload`)
* **Programmatic & LLM-Assisted Population**: Templates can be populated via REST API with structured examples or through LLM-assisted generation in the UI
* **Auto-Generation**: Built-in LLM workflows to automatically generate domain-specific examples from database schema or documentation

This modular approach allows organizations to extend the RAG system with custom templates tailored to their specific data patterns, query types, and business domains without modifying core agent code. [Template guide →](../RAG_Templates/README.md) · [Planner champion lifecycle →](../RAG/RAG.md)

## Knowledge Retrieval: Grounded Intelligence for the Focus Profile Class

While Planner Repositories power the self-improving Optimizer, **Knowledge Repositories** serve an entirely different purpose: they deliver **grounded, hallucination-free answers** from verified documents. This is the engine behind the **Focus** profile class (🔵 `rag_focused`).

### The Value Proposition

A language model on its own answers from its training data — a black box of uncertain provenance. Knowledge Retrieval inverts this model:

* **Zero Hallucination by Design**: The LLM synthesizes answers **exclusively** from retrieved documents. No general knowledge is injected. If the knowledge base doesn't contain the answer, the system says so transparently rather than fabricating one.
* **Institutional Memory at Scale**: Corporate policies, engineering runbooks, product documentation, compliance frameworks — all searchable via natural language. When experts leave, their knowledge stays.
* **Source Traceability**: Every answer includes citations back to specific documents, chunks, and metadata. Auditors and compliance teams can verify any claim.
* **Screened & Quarantinable Corpus**: every document is screened at ingest (injection patterns, personal-data categories, exfiltration links — observe-only, never blocking an upload); findings power an earned corpus signature and a one-click, reversible document quarantine that removes a poisoned document from retrieval without deleting it.
* **Grounded by a Runtime Gate**: unsupported numbers, quotes, and identifiers are caught before delivery — after a second-chance corpus search — and marked inline in the answer; weak retrieval yields an honest "the knowledge base does not cover this" instead of an invented answer ([Answer gates →](../Architecture/ANSWER_GATES_ARCHITECTURE.md)).
* **Source Provenance on Every Answer**: each source chip shows the corpus's integrity-signature state, CDC freshness ("verified N days ago"), and sensitivity level — with per-agent sensitivity ceilings governing which repositories an agent may read ([Evidence →](../Architecture/EVIDENCE_ARCHITECTURE.md) · [Trust Membrane →](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md)).
* **Freshness-Aware Ranking** *(optional, off by default)*: With a freshness weight above 0, documents are scored on a blend of semantic relevance and temporal freshness, so recent updates rank appropriately against older but relevant content. Per-document deduplication is likewise off until a limit is set.

### How Knowledge Retrieval Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     KNOWLEDGE RETRIEVAL PIPELINE                        │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  User Query                                                              │
│  "What is our data retention policy for EU customers?"                   │
│       │                                                                  │
│       ▼                                                                  │
│  ┌─────────────────────────────┐                                         │
│  │  Configuration Resolution   │ ← Three-tier: Global → Profile → Lock  │
│  │  maxDocs, freshnessWeight,  │                                         │
│  │  minRelevance, maxTokens    │                                         │
│  └──────────────┬──────────────┘                                         │
│                 ▼                                                         │
│  ┌─────────────────────────────┐                                         │
│  │  Semantic Search (vectors)  │ ← Embedding: per collection            │
│  │  Query each knowledge       │                                         │
│  │  collection assigned to     │                                         │
│  │  the profile                │                                         │
│  └──────────────┬──────────────┘                                         │
│                 ▼                                                         │
│  ┌─────────────────────────────┐                                         │
│  │  Hybrid Scoring             │                                         │
│  │  adjusted = (1-fw) × sim   │   fw = freshnessWeight                  │
│  │           + fw × freshness  │   sim = 1 - cosine_distance            │
│  │                             │   freshness = e^(-decay × days_old)    │
│  └──────────────┬──────────────┘                                         │
│                 ▼                                                         │
│  ┌─────────────────────────────┐                                         │
│  │  Per-Document Deduplication │ ← maxChunksPerDocument limit           │
│  │  + Minimum Relevance Filter │ ← minRelevanceScore threshold          │
│  └──────────────┬──────────────┘                                         │
│                 ▼                                                         │
│  ┌─────────────────────────────┐                                         │
│  │  LLM Synthesis              │ ← Custom synthesis prompt override     │
│  │  System prompt + retrieved  │   available per profile                 │
│  │  documents + user query     │                                         │
│  └──────────────┬──────────────┘                                         │
│                 ▼                                                         │
│  Answer with Source Citations                                            │
│  "Per the EU Data Governance Policy (Section 4.2)..."                    │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### Key Differentiators from Planner Repositories

| Aspect | Planner Repositories | Knowledge Repositories |
|--------|---------------------|----------------------|
| **Purpose** | Self-improving execution strategies | Grounded document retrieval |
| **Profile Class** | 🟣 Optimize (tool_enabled) | 🔵 Focus (rag_focused) |
| **Data Source** | Auto-captured execution traces | Uploaded documents (PDF, DOCX, TXT, MD) |
| **Scoring** | Similarity with efficiency penalties | Hybrid similarity + freshness |
| **Tool Execution** | Yes — full MCP tool calling | None — pure retrieval + synthesis |
| **Hallucination Risk** | Mitigated via proven patterns | Reduced by grounding in retrieved documents, checked by the grounding gate |

### Configuration

Knowledge retrieval behavior is controlled through a **three-tier configuration resolution**:

1. **Admin-Locked** (highest priority): Global settings locked by admin override all profile values
2. **Profile Override**: Per-profile knowledge-retrieval settings
3. **Global Default** (lowest priority): Deployment-wide defaults

Tunable parameters include the maximum number of documents returned, the minimum relevance threshold, the token budget for knowledge context, per-document chunk limits, a relevance-versus-freshness blend, and an optional synthesis prompt override. See the [Knowledge Retrieval Architecture](../Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md) for the full parameter reference and defaults.

### Document Ingestion & Chunking

Knowledge Repositories support multiple document formats (PDF, DOCX, TXT, Markdown) with configurable chunking strategies:

* **Paragraph-based**: Respects natural document structure, combines small paragraphs, splits oversized ones
* **Sentence-based**: Fine-grained chunking for dense technical content
* **Fixed-size**: Character-count chunking with configurable overlap
* **Semantic** (the default selection): intended as boundary-aware splitting; semantic boundary detection is not yet implemented, so it currently chunks by paragraph

Each chunk is embedded with the collection's embedding model (default `all-MiniLM-L6-v2`; a Teradata collection embeds server-side) and stored in the configured vector store (ChromaDB by default, or Teradata or Qdrant) with metadata (title, author, creation date, source filename, category, tags) enabling rich filtering and freshness scoring.

For the comprehensive architecture deep-dive including scoring algorithms, execution flow, and advanced features, see:
[**Knowledge Retrieval Architecture (docs/Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md)**](../Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md)

For a comprehensive overview of the RAG architecture, template development, and maintenance utilities, please see the detailed documentation:
[**RAG System Documentation (docs/RAG/RAG.md)**](../RAG/RAG.md)
[**RAG Template Plugin Development (rag_templates/README.md)**](../../rag_templates/README.md)
