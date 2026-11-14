# TDA RAG System: Closed-Loop Improvement & Maintenance

## 1. Introduction: The Self-Improving Agent

The application's Retrieval-Augmented Generation (RAG) system is a closed-loop feedback mechanism. Its primary goal is to improve the Planner's [cite: src/trusted_data_agent/agent/planner.py] decision-making over time by allowing it to learn from its own past successes.

The system is designed to automatically:

1.  **Capture** every successful agent turn.
2.  **Analyze** its efficiency (based on token cost).
3.  **Identify** the single "best-in-class" strategy for any given user query.
4.  **Feed** this "best-in-class" example back to the Planner on future, similar queries.

This document details the complete data lifecycle, from real-time processing to batch maintenance.

## 2. Key Components & Data Flow

The RAG system relies on three main storage locations:

1.  **tda_sessions/** (The Raw Log):
    *   This is the "black box recorder" of the application.
    *   It contains the raw JSON logs for every user session, storing the complete workflow_history for every turn, including failures, errors, and conversational chats.
    *   It is the **source material** for the RAG miner.

2.  **rag/tda_rag_cases/** (The Case Study Archive):
    *   This directory is defined by RAG_CASES_DIR in config.py [cite: src/trusted_data_agent/core/config.py].
    *   It is the "filing cabinet" of processed case studies.
    *   When a turn is processed by the RAG system, it is extracted, cleaned, and saved here as a single case_[uuid].json file under the collection's folder (`collection_<id>/case_<uuid>.json`). This archive contains all processed successful turns, not just the most efficient ones.

3.  **.chromadb_rag_cache/** (The Search Index):
    *   This is the persistent vector database (ChromaDB), defined by RAG_PERSIST_DIR in config.py [cite: src/trusted_data_agent/core/config.py].
    *   It does **not** store the full JSON. It stores a vector embedding of the user's query and a flat metadata object.
    *   Crucially, this metadata includes the `case_id` (which links back to the file in RAG_CASES_DIR) and the `is_most_efficient` flag, which is the key to the entire system.

## 3. The RAG Approach: Real-Time Closed-Loop

The primary RAG pipeline is a real-time, asynchronous "Producer-Consumer" system. This ensures that agent improvements are captured immediately without impacting user-facing performance.

### Part 1: The "Producer" (in engines/optimize_engine.py)

1.  A user's query is successfully completed by the Optimize engine (`OptimizeEngine`, driving the PlanExecutor).
2.  At the end of the turn the engine finalizes the `turn_summary` object, which contains the query, the plan, all execution steps, and the final token counts [cite: src/trusted_data_agent/agent/engines/optimize_engine.py].
3.  The engine adds the `user_uuid` to this `turn_summary` and places it into the global `APP_STATE['rag_processing_queue']` [cite: src/trusted_data_agent/agent/engines/optimize_engine.py, src/trusted_data_agent/core/config.py].
4.  This action is instantaneous. The user's `final_answer` has already been sent, so they experience no delay. This entire step is gated by the `RAG_ENABLED` flag, skipped for temporary sessions, and skipped when `turn_seedable(turn_summary)` is false — a trust gate WITHHELD the answer, or the honesty gate flagged it in any mode (the plan that produced an unsupported claim must not be taught).
5.  **Champion replay outcome**: when the turn was guided by a champion (`rag_source_case_id`), `record_champion_replay_outcome` resets that champion's failure counter on success, or counts a failure for a trust-blocked turn; after `RAG_REPLAY_FAILURE_DEMOTE_THRESHOLD` (2, `rag_retriever.py`) consecutive real failures the champion is demoted (`user_feedback_score = -1`) and no longer served.

### Part 2: The "Consumer" (in main.py)

1.  When the application starts, it launches a single, persistent background task: `rag_processing_worker()` [cite: src/trusted_data_agent/main.py].
2.  This worker is the **only** consumer of the `rag_processing_queue`. It runs in an infinite loop, pulling one `turn_summary` at a time.
3.  This singleton worker design **guarantees atomicity** and prevents the database race conditions we previously discussed.
4.  The worker calls the RAGRetriever's central processing method.

### Part 3: The "Processor" (in rag_retriever.py)

This is the core of the RAG logic, performed by the RAGRetriever instance [cite: src/trusted_data_agent/agent/rag_retriever.py].

1.  **Extract & Filter**: The worker calls `await self.retriever.process_turn_for_rag(turn_summary)`. This method first uses `_extract_case_from_turn_summary` to parse the turn. If the turn was not a successful, tool-using plan (e.g., it was a failure or a TDA_ContextReport), the process stops, and the turn is ignored.
    *   **Plan-based seeding gate**: Even a *successful* turn is ignored unless its plan contains at least one **data-acquisition phase**. Presentation/capability-only follow-ups — "show me a bar chart", "show me the knowledge graph", "put that on a dashboard" — acquire no new data; their value is content-independent and their arguments depend on prior-turn context, so they would pollute the repository and surface as misleading champion cases. A phase tool counts as *non-strategy* if it is owned by **any** installed component (builtin, user, or marketplace/agent-pack — resolved dynamically via the component manager, so future components are covered automatically) or is one of a small fixed set of framework-internal control tools (`TDA_FinalReport`, `TDA_SystemLog`, `TDA_ContextReport`, `TDA_CurrentDate`). `TDA_SystemOrchestration` counts as data (it wraps real MCP calls); MCP data-source tools and connectors count as data. A future data-acquiring component may opt back in by declaring `"acquires_data": true` in its manifest. See `_tool_is_data_acquisition` / `_plan_has_data_acquisition` in `rag_retriever.py`. To remove faulty seeds created before this gate existed, run `maintenance/purge_presentation_seeds.py` (dry-run by default; `--apply` to delete; it reuses the identical classifier).
    *   **Denied-turn seeding gate (Aug 2026)**: a turn whose OBDA-READ lane is `confined` — every data attempt DENIED by the agent's data surface, nothing executed — never seeds. Such a turn still ends `status=success` (an honest refusal is a successful turn), so without this gate the generic success criteria could store its plan as a proven strategy. Found in a live walkthrough: pre-governance turns had seeded raw-SQL champions that kept steering a surface-only agent into plans the runtime denies with certainty, and the store had to be reset by hand. Defence-in-depth beside the success criteria (which today also reject confined turns), pinned against criteria drift.
    *   **Authority-aware retrieval (Aug 2026)**: a champion whose plan the *ambient agent principal* would deny is **not offered** (`champion_blocked_by_surface` in `rag_retriever.py`) — deterministic, 0-token: a surface-exclusive principal plus any data-tool phase carrying literal SQL text. Not demoted, not deleted: unconfine the agent and the champion is valid guidance again. A runtime reference (`{"source": "result_of_phase_N", "key": "sql"}`) is the governed seam and never counts as literal SQL; ADVISORY surfaces block nothing; the filter **fails open** (an unreadable authority layer must never empty the champion lane); every skip is logged. This closes the loop the denied-turn gate opens: without it, a champion seeded before confinement could never self-demote — a confined turn ends `status=success`, so the replay-failure counter never moves.
2.  **Archive Case File**: The valid "case study" JSON is saved to the `rag/tda_rag_cases/` directory.
3.  **Query ChromaDB**: It queries the vector database to find the *current* champion for this exact user query (i.e., where `is_most_efficient: True`).
4.  **Compare**: User feedback decides first — a downvoted new case never wins, a downvoted champion always loses, and a higher `user_feedback_score` wins. Only at equal feedback does efficiency decide: the `output_tokens` of the new case against the current champion's (a 0/missing count is a metering failure and treated as unknown, never as "free").
5.  **Perform Atomic Transaction**:
    *   **Case A (New case wins)**: The new case wins the comparison. It is upsert-ed to ChromaDB with `is_most_efficient: True`. The retriever then issues an update command to **demote** the old champion, setting its `is_most_efficient` flag to `False`.
    *   **Case B (Old case wins)**: The new case loses the comparison. It is upsert-ed to ChromaDB with `is_most_efficient: False`. The old champion remains the winner.

## Part 4: How the Agent Uses the Data

1.  **Retrieval**: When a new query comes in, the Planner calls `self.rag_retriever.retrieve_examples()` [cite: src/trusted_data_agent/agent/planner.py].
2.  **Filtering**: This `retrieve_examples` method only searches ChromaDB for cases matching the query where `is_most_efficient: True` [cite: src/trusted_data_agent/agent/rag_retriever.py]. It is always given a `RAGAccessContext` for the requesting user (a `None` context means *every* loaded collection is scanned — never acceptable outside a deliberately global maintenance path), embeds the query once per embedding model, and runs every ChromaDB call off the event loop via `asyncio.to_thread` (see [KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md → Retrieval Pipeline](../Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md)).
3.  **Augmentation**: The "few-shot examples" from these champion cases are formatted and injected directly into the Planner's prompt, guiding it to generate a high-quality, efficient plan based on proven strategies.

### Which planner repositories a turn loads

A planner repository is keyed to the MCP server whose tools its champion cases name; a turn loads
the repositories keyed to its session's server (`rag_retriever._planner_keyed_to`), plus a
subscription keyed to the subscriber's own server. An agent that reads ADDITIONAL MCP servers
(PROP-006 phase 2) also loads the repositories keyed to any of the turn's additional servers — their
cases name that server's tools, which the agent sees as `<alias>__<name>`. For a single-server
agent nothing changes. See [MULTI_SOURCE_ARCHITECTURE.md §9](../Architecture/MULTI_SOURCE_ARCHITECTURE.md).

### Where an agent learns, and what it reads (Oct 2026)

The one rule lives in `core/agent_learning.py`; the decisions behind it are OC-F3, OC-F4 and
OC-F5 in `test/optimize_campaign/FINDINGS.md`.

| Agent | Learns into | Reads |
|---|---|---|
| **Has its own learning repository** (every Optimize agent created since Oct 2026; an existing one after `POST /v1/profiles/<id>/learning-repository`) | only its own repository for the turn's MCP server (created on its first lesson on a server it has not learned on); a LOCKED own repository is frozen: nothing is stored | its own repository + exactly what it binds — never the shared default |
| **No own repository** (agents created before Oct 2026, a new account's bootstrap agents) | the repository whose champion guided the turn if writable, else the user's shared default for the server (unchanged) | exactly what it binds (+ the shared default read back when a bound repository is unwritable) |

* **Nothing bound means nothing read** (`planner.planner_binding_scope`): an agent with no
  `ragCollections` retrieves no champions; only an explicit `"*"` reads every accessible repository.
  Before Oct 2026 an empty binding read EVERY planner repository of the owner.
* **The own repository is a planner repository like any other**: listed under Planner
  Repositories, named `@TAG — learned`, owned by the user, private. It is linked to its agent by
  `collections.learning_profile_id`; that row is the switch (no profile field), so creating it
  moves no signature. Sharing is explicit: bind it to another agent (read only — lessons are
  always written into the learning agent's own repository), or publish it on the marketplace; a
  fork of it is an ordinary repository.
* **Not a signed ingredient**: it is not in `ragCollections`, so its contents are not hashed into
  the agent's signature — learning never unsigns an agent (the same treatment as Agent Memory).
  Its cases are learned, never `template_generated`, so they are hints, never replayed verbatim.
* **Never a default**: both default lookups skip it, so deleting your default can never make one
  agent's repository everyone's notebook.
* **Lifecycle**: removing the agent removes its repositories (row, vectors, case files) — through
  `remove_profile`, and through the two bulk paths that bypass it (unsubscribe, unpublish). A pack
  never ships it; a subscriber or forker gets an agent with its own, empty repository; a
  subscription sync keeps the agent's id and therefore its repository.
* **Verbatim replay** of an exact signed seed (`planner.exact_signed_champion`, 0 tokens) takes a
  repository that is LOCKED, or SIGNED with its signature still covering its content
  (`planner.planner_replayable`); a case learned after signing ends it until it is signed again.

Gates: `test_agent_learning.py`, `test_agent_learning_editor.mjs`, `test_planner_binding_scope.py`,
`test_signed_seed_replay.py`; live: `test/optimize_campaign/marketplace_learning_probe.py`.

### When a case blocks signing: Planner Health (Nov 2026)

A planner repository cannot be signed while a champion case carries unsafe SQL (destructive,
mutating or stacked statements) or prompt injection, because a signed case can be replayed
verbatim. The refusal names each flagged case by its question, and the repository's inspect
view (Planner Health) offers the two acts that clear it: **Remove** the case, or **Acknowledge**
the finding with a reason. An acknowledgement is stored on the case and becomes part of the
signature; a new finding on the same case reopens it. Detail:
[TRUST_MEMBRANE_ARCHITECTURE.md §3a.2](../Architecture/TRUST_MEMBRANE_ARCHITECTURE.md#3a2-planner-health--the-gate-names-the-case-it-refused-nov-2026).

## 4. Maintenance Script: rag_miner.py

This script is a command-line "catch-up" utility to process historical data from `tda_sessions` that the real-time worker may have missed (e.g., turns from before the RAG system was active).

### Purpose

The `rag_miner.py` script [cite: maintenance/rag_miner.py] scans all session files in the `tda_sessions` directory. For every turn it finds, it feeds it into the **exact same** `RAGRetriever.process_turn_for_rag` method used by the real-time worker.

This guarantees that all historical data is processed using the **identical** filtering, efficiency comparison, and atomic update logic as the real-time loop.

### How to Use rag_miner.py

**Critical Warning: Concurrency Error**

You **must stop the main web server** (`python -m trusted_data_agent.main`) before running the `rag_miner.py` script.

Both processes connect to the same `.chromadb_rag_cache/` database. If both are running, the server will hold a lock on the database file, and the miner will fail with a `sqlite3.OperationalError: attempt to write a readonly database error`.

### Workflow:

1.  Ctrl+C to stop the `main.py` server.
2.  Run the `rag_miner.py` script (see commands below).
3.  Restart the `main.py` server.

### Basic Command

From the `uderia` root directory:

```bash
# Ensure your virtual environment is active
# (e.g., source .venv/bin/activate)

# Run the miner
python maintenance/rag_miner.py
```

### Command-Line Arguments

*   `--force`:
    *   This is the "fresh start" or "rebuild" flag.
    *   It will **DELETE all case files** from `rag/tda_rag_cases/`.
    *   It will **DELETE the entire .chromadb_rag_cache/ directory**, wiping the vector database.
    *   Use this if you suspect the RAG store is corrupted or you want to rebuild it from scratch using only the data in `tda_sessions`.
*   `--rebuild`:
    *   Rebuilds ChromaDB from existing JSON case files without processing sessions.
    *   Scans `rag/tda_rag_cases/collection_*/case_*.json` files and loads them into ChromaDB.
    *   Useful after running `maintenance/reset_chromadb.py` or when ChromaDB is corrupted but JSON files are intact.
    *   Does not touch session data - only rebuilds the vector index from your existing cases.
    *   Example: `python maintenance/rag_miner.py --rebuild`
*   `--sessions_dir <path>`:
    *   Tells the miner to look in a different directory for session logs.
    *   Default: `tda_sessions/` [cite: maintenance/rag_miner.py]
*   `--output_dir <path>`:
    *   Tells the miner to save the `case_*.json` files to a different directory.
    *   Default: `rag/tda_rag_cases/` [cite: maintenance/rag_miner.py]

## 5. ChromaDB Recovery: reset_chromadb.py

### When to Use

If you encounter ChromaDB errors like `KeyError: '_type'` or other database corruption issues, you can safely reset the vector database without losing your RAG data.

### How It Works

The `reset_chromadb.py` script [cite: maintenance/reset_chromadb.py] deletes the `.chromadb_rag_cache/` directory. This is safe because:

*   **Your actual RAG data is stored in JSON files** in `rag/tda_rag_cases/collection_*/`
*   ChromaDB is just a **vector index cache** for fast similarity searches
*   The index can be rebuilt from the JSON files

### Usage

```bash
# From the project root
python maintenance/reset_chromadb.py

# Follow the prompts - type 'yes' to confirm deletion
```

### Recovery Steps

After resetting ChromaDB, you have two options to rebuild the vector index:

**Option 1: Using rag_miner.py (Recommended)**
```bash
# Rebuild from existing case files
python maintenance/rag_miner.py --rebuild
```

**Option 2: Using the API**
```bash
# Start the application
python -m trusted_data_agent.main

# In another terminal, refresh each collection (JWT from /api/v1/auth/login)
curl -X POST -H "Authorization: Bearer $JWT" http://localhost:5050/api/v1/rag/collections/0/refresh
curl -X POST -H "Authorization: Bearer $JWT" http://localhost:5050/api/v1/rag/collections/1/refresh
```

### What Gets Preserved

✅ **Preserved** (stored in JSON files):
- All case studies and their metadata
- User queries and SQL statements
- Execution plans and strategies
- User feedback scores
- Efficiency metrics (token counts)
- Collection assignments

❌ **Lost** (will be regenerated):
- Vector embeddings
- ChromaDB indexes
- Similarity search optimizations
