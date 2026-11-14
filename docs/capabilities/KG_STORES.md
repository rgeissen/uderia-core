# 🕸️ KG Store Abstraction Layer

Uderia's Knowledge Graph capabilities are powered by a **pluggable KG store layer** that decouples all graph storage, traversal, and algorithm operations from any specific database. Start with the built-in local store and scale to an enterprise graph database — without changing a single profile, agent pack, or workflow.

**Two production backends, one unified interface:**

| | **SQLite + NetworkX** (Default) | **Neo4j** (Enterprise) |
|---|---|---|
| **Best for** | Local / single-user / getting started | Enterprise graph workloads, large-scale graph analytics |
| **Deployment** | Embedded, zero-config | External Bolt server (self-hosted or AuraDB) |
| **Graph Algorithms** | BFS/DFS subgraph, importance ranking, cycle detection, shortest path (NetworkX) | Full Cypher + APOC, native graph algorithms |
| **Query Language** | Python API | Native Cypher queries |
| **Full-Text Search** | — | Native full-text index |
| **Dependencies** | None (networkx already required) | `pip install neo4j` |

**Why this matters:**

* **No vendor lock-in.** Knowledge Graphs created with the SQLite backend work identically on Neo4j. Switch backends per-profile by selecting a KG configuration — existing entities, relationships, agent packs, and marketplace KGs continue to work unchanged.
* **Capability-based negotiation.** Each backend declares what it supports (`GRAPH_ALGORITHMS`, `NATIVE_GRAPH_QUERIES`, `FULL_TEXT_SEARCH`). Uderia adapts its behaviour automatically.
* **Per-profile backend selection.** Each profile can be pinned to a specific KG backend configuration via the Profile Edit dialog. Unset profiles fall through a resolution chain: profile config → user default config → global SQLite fallback.
* **Admin governance.** Admins control which backends each user tier may use and whether KG is enabled per tier at all — without a server restart.
* **Extensible by design.** Adding a new graph backend takes one new file, a registry call, an import in `kgstore/__init__.py` and an entry in `ALL_KG_BACKENDS` — no routes, no UI code, no handler code needs to change.

For the full architectural deep-dive — including the factory resolution chain, delegate wrapper, capability system, and backend implementation guides — see:
[**KG Store Abstraction Architecture (docs/Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md)**](../Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md)
