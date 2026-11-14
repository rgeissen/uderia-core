# 🧠 Memory: Context Window Management

Every token sent to an LLM costs money and competes for limited space. Uderia's **Memory** subsystem — the budget-aware Context Window Orchestrator — treats the model's context window as a managed resource rather than an append-only buffer, ensuring that every LLM call receives the most relevant, highest-value information within its token budget.

## Value Proposition

| Without Memory management | With Uderia Memory |
|---|---|
| History grows unbounded → costs spiral | Token budget enforced per call → predictable cost |
| Irrelevant past turns crowd out current context | Priority-based module allocation → right data at the right time |
| Long sessions degrade in quality | RAG-offload condensation retrieves semantically relevant history | 
| No visibility into what the LLM sees | Per-call `context_window_snapshot` events surfaced in Live Status |

## How It Works

On every LLM call, the orchestrator runs a **five-pass assembly pipeline**:

1. **Resolve** — activate every registered module (13 builtin), using the context window type's settings as overrides and each module's manifest defaults otherwise
2. **Dynamic Adjustments** — apply runtime condition-based rules (e.g. first turn, no uploaded documents)
3. **Allocate & Assemble** — distribute the token budget across active modules and invoke each module's context-contribution step
4. **Reallocate Surplus** — redistribute unused budget from low-utilisation modules to those that need more space
5. **Condense** — if the assembled context still exceeds the budget, condense lowest-priority modules first

## The 13 Builtin Modules

| Module | Purpose |
|---|---|
| `system_prompt` | LLM system prompt |
| `tool_definitions` | MCP tool schemas (full descriptions on turn 1 and on every turn of a governed tool surface; names-only after turn 1 otherwise) |
| `conversation_history` | Budget-aware chat history with sliding-window + RAG-offload condensation |
| `rag_context` | Reports champion-case retrieval (0 tokens — the planner retrieves the cases after assembly and injects them itself) |
| `knowledge_graph_context` | Budget-managed knowledge-graph subgraph enrichment |
| `knowledge_context` | Knowledge repository documents (screened by the retrieval firewall) |
| `user_memory` | Agent Memory recall — durable facts about the user |
| `plan_hydration` | Previous turn results injected to skip redundant tool calls |
| `document_context` | Uploaded document text |
| `component_instructions` | Component tool instructions (chart, canvas, scheduler, …) |
| `workflow_history` | Cross-turn execution traces for the planner |
| `review_release` | Owner-released withheld originals (HITL release-to-conversation, forward-only + labelled) |
| `findings` | The verified results of earlier turns (tool, statement, row count, columns, single-row values), re-read rather than recalled; the newest turn is kept under condensation |

## RAG-Offload Condensation

When conversation history grows too large, Uderia does not simply truncate it. Instead it stores earlier turns in a **per-session vector store** (ChromaDB) and retrieves only the chunks that are semantically relevant to the current query — preserving long-horizon context without ballooning token counts. An amber **RAG** badge in the Live Status panel shows exactly how many chunks were retrieved when this fires.

## Configurable Memory Types

Four predefined **Context Window Types** (plus custom types) let teams tune the token allocation strategy per profile:

- **Balanced** — even distribution across all modules (default)
- **Knowledge-Heavy** — prioritises knowledge retrieval
- **Conversation-First** — maximises conversation history
- **Token-Efficient** — minimal viable context for cost-sensitive workloads

Profiles bind to a Memory type, which administrators manage in **Anatomy → Memory**.

## Observability

Every LLM call emits a `context_window_snapshot` SSE event rendered as a colour-coded stacked bar chart in the **Live Status** panel, showing per-module allocation, utilisation percentage, dynamic adjustments that fired, and any condensation events. Teams can see what the model actually received on each call, token by token.

For full details on the five-pass pipeline, per-module budget rules, dynamic adjustment DSL, surplus reallocation, condensation strategies, and the context bridge for Optimize profiles, see:
[**Context Window Architecture (docs/Architecture/CONTEXT_WINDOW_ARCHITECTURE.md)**](../Architecture/CONTEXT_WINDOW_ARCHITECTURE.md)

Context window management is the short-term half of memory. The long-term half — durable facts about the user, recalled across sessions — is [Agent Memory →](../Architecture/AGENT_MEMORY_ARCHITECTURE.md).
