# Knowledge Graph Component Architecture: Context-Enriching Entity-Relationship System

> Pluggable graph store abstraction (SQLite + NetworkX default, Neo4j enterprise option), D3.js force-directed visualization, automatic context injection (guardrail) on every engine, LLM-driven entity management, and multi-profile management UI — every graph identified by its `kg_id`, owned by a `(profile_id, user_uuid)` pair for multi-user isolation.

*Last updated: 2026-10-30.*

**Related:** the canonical topology and closed vocabularies are defined in [ONTOLOGY_ARCHITECTURE.md](ONTOLOGY_ARCHITECTURE.md) (this doc conforms to it); storage backends in [KG_STORE_ABSTRACTION_ARCHITECTURE.md](KG_STORE_ABSTRACTION_ARCHITECTURE.md); the inspector (lenses, theatre, open-on-concept, drafts view, live redraw) in [KG_INSPECTOR_ARCHITECTURE.md](KG_INSPECTOR_ARCHITECTURE.md); the authored layer and proposals (OBDA-MODEL) in [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md); signed data products (OBDA-READ) in [OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md); the SQL preflight guard the enrichment feeds in [PREFLIGHT_SQL_ARCHITECTURE.md](PREFLIGHT_SQL_ARCHITECTURE.md); the grounding gate that consumes the trusted scope in [ANSWER_GATES_ARCHITECTURE.md](ANSWER_GATES_ARCHITECTURE.md); RDF/OWL interchange in [OPEN_STANDARDS_ARCHITECTURE.md](OPEN_STANDARDS_ARCHITECTURE.md); marketplace subscribe in [KG_SUBSCRIPTION_PLAN.md](KG_SUBSCRIPTION_PLAN.md).

## Overview

The Knowledge Graph component (`TDA_KnowledgeGraph`) captures database topology, business concepts, metrics, and domain taxonomies as a typed entity-relationship graph. Unlike static schema documentation, the knowledge graph is a **living, queryable model** that grows as users interact with the platform — through LLM inference, manual enrichment, bulk import, or one of the KG constructors (agent discovery against a live database, industry models, declared-model registries, ANDP products).

The component's primary value is **context injection**: before the planning/answering LLM call of every engine, the system extracts a relevant subgraph based on the user's query and injects it into the prompt. This guides tool selection, SQL construction, and argument generation — functioning as a semantic guardrail that reduces hallucination and improves first-attempt success rates.

Each graph has its own `kg_id` and is owned by a `(profile_id, user_uuid)` pair. A profile may own several graphs (one active at a time), and a graph can be assigned to further profiles of the same user (`kg_profile_assignments`). This aligns with the platform's multi-profile architecture where different profiles connect to different MCP servers and data sources.

---

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [File Structure](#file-structure)
- [System Architecture](#system-architecture)
  - [Data Flow](#data-flow)
  - [Dual-Layer Architecture](#dual-layer-architecture)
- [Data Model](#data-model)
  - [Entity Schema](#entity-schema)
  - [Relationship Schema](#relationship-schema)
  - [Type Enumerations](#type-enumerations)
- [Backend Architecture](#backend-architecture)
  - [GraphStore Class](#graphstore-class)
  - [Entity CRUD](#entity-crud)
  - [Relationship CRUD](#relationship-crud)
  - [Bulk Operations](#bulk-operations)
  - [NetworkX Graph Algorithms](#networkx-graph-algorithms)
  - [Cross-Profile Enumeration](#cross-profile-enumeration)
- [Component Handler](#component-handler)
  - [Tool Actions](#tool-actions)
  - [Context Enrichment (Guardrail)](#context-enrichment-guardrail)
  - [Query Search Algorithm](#query-search-algorithm)
  - [Model Gap — "Not in this model"](#model-gap--not-in-this-model)
  - [Semantic Seeding — the Second Recall Channel](#semantic-seeding--the-second-recall-channel)
  - [Conversational Carry-Over — the Third Recall Channel](#conversational-carry-over--the-third-recall-channel)
  - [Released-only grounding — Model B](#released-only-grounding--model-b-tier-43-1a)
  - [Prompt Injection Format](#prompt-injection-format)
- [Frontend Architecture](#frontend-architecture)
  - [D3 Visualization — Three Display Modes](#d3-visualization--three-display-modes)
  - [Interactive Features](#interactive-features)
  - [Theme Compliance](#theme-compliance)
  - [Management UI](#management-ui)
- [REST API](#rest-api)
  - [Entity Endpoints](#entity-endpoints)
  - [Relationship Endpoints](#relationship-endpoints)
  - [Graph Operations](#graph-operations)
  - [Management Endpoints](#management-endpoints)
  - [Further Endpoints](#further-endpoints)
- [Integration Points](#integration-points)
  - [Planner Integration](#planner-integration)
  - [Component System Integration](#component-system-integration) (incl. [Component Signals](#component-signals))
  - [Profile Configuration](#profile-configuration)
  - [Session UI Integration](#session-ui-integration)
- [MCP Schema Discovery (never built)](#mcp-schema-discovery-never-built)
- [Security & Isolation](#security--isolation)
- [Performance Characteristics](#performance-characteristics)
- [Roadmap](#roadmap)
- [File Reference](#file-reference)

---

## Design Philosophy

### 1. Pluggable Backend Storage

The `kgstore/` abstraction layer decouples all graph operations from any specific database. The **SQLite + NetworkX** backend (default) provides durable CRUD with multi-user isolation and graph algorithms (BFS, shortest path, centrality, cycle detection) — NetworkX is lazy-loaded and cached, invalidated on any write. The **Neo4j** backend (enterprise) routes the same interface to a Bolt-connected AuraDB or self-hosted instance. Adding a new backend requires one file and one registry call — no handler, route, or UI code changes.

See [KG Store Abstraction Architecture](KG_STORE_ABSTRACTION_ARCHITECTURE.md) for the full design.

### 2. Guardrail, Not Controller

The knowledge graph enriches the LLM's context but does not constrain it — the retained autonomy to deviate when the graph is incomplete is by design. `componentConfig.knowledge_graph.intensity` sizes how much of a discovered table's columns the enrichment carries per turn — the **per-table column cap** (`handler._INTENSITY_COLUMN_CAP`): `low` 6 · `medium` 12 (default) · `high` 24 · `full` = legacy (all columns of every discovered table). Any other value (including the instruction levels `none`/`heavy`) resolves to the `medium` cap. Seed columns (the ones the question named) are never capped; the withheld remainder is reported as "+N more columns" so the agent can ask to expand. (Aug 2026: before this, `intensity` was read nowhere.)

### 3. Profile-Scoped Isolation

Each graph (`kg_id`) is owned by one `(profile_id, user_uuid)` pair; only the owner's active graph (or a graph explicitly assigned to the profile) is read for that profile. A profile connected to a sales database builds a different knowledge graph than one connected to an HR system. Cross-profile enumeration is read-only for management UI purposes.

### 4. Progressive Enrichment

Graphs start empty and grow through four channels:
- **LLM-inferred**: The LLM calls `add_entity`/`add_relationship` during conversation (or, under OBDA-MODEL, `propose_entity`/`propose_attribute` onto the authored layer)
- **Manual**: Users import JSON / RDF files or use the REST API
- **Constructors**: `POST /v1/knowledge-graph/generate` runs a registered constructor strategy (`agent/kg_constructors/registry.py`): agent discovery against a live database (`discovery_mode` `tool` / `prompt`), `industry_model`, `declared_model`, and `andp_semantic` (the ANDP walker)
- **Marketplace / agent packs**: install, fork, subscribe and pack import write through the same `import_bulk` / `replace_model_layer` chokepoints

### 5. Deterministic Fast-Path

The component handler declares `is_deterministic = True`. When the LLM calls `TDA_KnowledgeGraph`, the response is computed directly from the graph store without an additional LLM call — saving tokens and latency.

---

## File Structure

```
components/builtin/knowledge_graph/
├── manifest.json           # Component metadata, tool definition, signals, instances, render targets
├── handler.py              # KnowledgeGraphHandler — process() + context enrichment (~6,400 lines)
├── graph_store.py          # GraphStore — thin delegate over kgstore/ abstraction
├── resolver.py             # Deterministic logical → deployed-physical resolution (resolve_capabilities)
├── metric_api.py           # Governed metric semantic core (compile_metric)
├── concepts.py             # declare_concept — deterministic business-concept declaration
├── bridge_probe.py         # probe_bridge — turn a declared product_bridge into evidence
├── renderer.js             # D3.js v7 force-directed visualization + inspector (~4,400 lines)
├── instructions.json       # Intensity-keyed LLM guidance (none/medium/heavy)
└── __init__.py

src/trusted_data_agent/kgstore/
├── __init__.py             # Public API surface + backend auto-registration
├── base.py                 # KGStoreBackend ABC
├── capabilities.py         # KGStoreCapability enum
├── types.py                # THE vocabularies (ENTITY_TYPES, RELATIONSHIP_TYPES, layers, keep rules)
├── settings.py             # Tier-based governance (allowed backends)
├── factory.py              # Backend registry + per-profile resolution chain
├── sqlite_backend.py       # SQLite + NetworkX implementation (+ the shared adaptive extractor)
├── neo4j_backend.py        # Neo4j Bolt implementation (optional dependency)
├── conformance.py          # Topology conformance check (publish gate)
├── graph_diff.py           # Diff between two graph versions (check-update)
├── migrations.py           # migrate_kg_payload_types — old-model payload normalisation
├── keys.py                 # key_columns — THE reading of an entity's declared key
├── semantic_index.py       # Semantic seeding index (per-KG float32 matrix)
└── usage.py                # kg_entity_usage telemetry (seeded/rendered/used/missed)

src/trusted_data_agent/agent/kg_constructors/   # Constructor strategies (registry, walkers,
                                                # source_validation, authored_deploy, …)
src/trusted_data_agent/core/
├── kg_release.py           # Released-only grounding (Model B)
├── kg_version_db.py        # Version snapshots / restore
├── kg_subscription.py      # Marketplace subscribe sync
└── kg_stage.py, kg_derived_state.py

components/builtin/context_window/modules/knowledge_graph/  # knowledge_graph_context CW module

schema/
├── 21_knowledge_graph.sql          # kg_entities + kg_relationships tables
├── 22_kg_profile_assignments.sql   # KG → profile assignments
├── 37_kg_subscriptions.sql         # Marketplace subscriptions
├── 38_kg_backend_settings.sql      # kg_backend_settings governance table
└── 41_kg_versions.sql              # Version history

static/js/
├── api.js                          # Frontend API functions
├── kgVocabulary.js                 # The counting vocabulary (nodes vs entities)
└── handlers/
    ├── knowledgeGraphPanelHandler.js  # Management UI for Resource Panel + Intelligence page
    ├── kgTheatre.js                   # Execution theatre (see KG_INSPECTOR_ARCHITECTURE.md)
    ├── rag/kgConstructorModal.js      # Constructor dialogs
    └── kgStoreHandler.js              # Graph Stores admin tab (add/edit/delete/test backends)

skills/builtin/knowledge-graph-expert/  # Companion skill (component_id: knowledge_graph)

templates/
└── index.html              # Split panel DOM (#kg-split-panel), Intelligence tab
```

---

## System Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          KNOWLEDGE GRAPH DATA FLOW                          │
│                                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │  User Query  │    │  REST API    │    │  JSON Import │                   │
│  │  (session)   │    │  (manual)    │    │  (file)      │                   │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘                   │
│         │                   │                   │                           │
│         ▼                   ▼                   ▼                           │
│  ┌─────────────────────────────────────────────────────┐                   │
│  │        GraphStore(profile_id, user_uuid, kg_id)      │                   │
│  │  ┌───────────────────┐  ┌─────────────────────────┐ │                   │
│  │  │   SQLite Layer    │  │   NetworkX DiGraph       │ │                   │
│  │  │   ─────────────   │  │   ──────────────────     │ │                   │
│  │  │   kg_entities     │──▶  Lazy-loaded on first    │ │                   │
│  │  │   kg_relationships│  │  graph algorithm call    │ │                   │
│  │  │                   │  │                          │ │                   │
│  │  │   CRUD, search,   │  │  BFS, shortest path,    │ │                   │
│  │  │   persistence     │  │  centrality, cycles      │ │                   │
│  │  └───────────────────┘  └─────────────────────────┘ │                   │
│  └──────────────────────────┬──────────────────────────┘                   │
│                              │                                              │
│              ┌───────────────┼───────────────┐                              │
│              │               │               │                              │
│              ▼               ▼               ▼                              │
│  ┌───────────────┐ ┌──────────────┐ ┌───────────────┐                      │
│  │  Context      │ │  Tool Call   │ │  Visualization│                      │
│  │  Enrichment   │ │  Response    │ │  Spec         │                      │
│  │  (guardrail)  │ │  (inline)    │ │  (D3 graph)   │                      │
│  └───────┬───────┘ └──────┬───────┘ └───────┬───────┘                      │
│          │                │                  │                              │
│          ▼                ▼                  ▼                              │
│  ┌───────────────┐ ┌──────────────┐ ┌───────────────┐                      │
│  │  Planner      │ │  Chat        │ │  Split Panel  │                      │
│  │  Prompt       │ │  Message     │ │  D3 Force     │                      │
│  │  Injection    │ │  (HTML)      │ │  Graph        │                      │
│  └───────────────┘ └──────────────┘ └───────────────┘                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Dual-Layer Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │            GraphStore Instance                │
                    │    (one kg_id; owner profile_id, user_uuid)  │
                    │                                              │
                    │  ┌────────────────┐  ┌────────────────────┐ │
                    │  │  SQLite Layer  │  │  NetworkX Layer    │ │
                    │  │                │  │                    │ │
                    │  │  add_entity()  │  │  extract_subgraph_ │ │
                    │  │  get_entity()  │  │    adaptive()      │ │
                    │  │  search()      │  │  shortest_path()   │ │
                    │  │  list()        │  │  centrality()      │ │
                    │  │  update()      │  │  detect_cycles()   │ │
                    │  │  delete()      │  │  connected()       │ │
                    │  │  import_bulk() │  │  get_stats()       │ │
                    │  │  clear_graph() │  │                    │ │
                    │  │                │  │  Lazy-loaded,      │ │
                    │  │  ACID durable  │  │  cached,           │ │
                    │  │  Multi-user    │  │  invalidated on    │ │
                    │  │                │  │  any write          │ │
                    │  └────────────────┘  └────────────────────┘ │
                    └──────────────────────────────────────────────┘
                                          │
                    Write ops ────────────┘
                    (add/update/delete)
                    trigger invalidate_cache(kg_id)
                    → the kg_id's DiGraph leaves
                      the backend's LRU cache
                    → Next algorithm call
                      re-loads from SQLite
```

---

## Data Model

### Entity Schema

**Table:** `kg_entities` (SQLite, `tda_auth.db`)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-incrementing entity ID |
| `kg_id` | TEXT NOT NULL | The graph this node belongs to (the scope key) |
| `profile_id` | TEXT NOT NULL | Owning profile |
| `user_uuid` | TEXT NOT NULL | Owning user |
| `name` | TEXT NOT NULL | Entity display name (qualified, e.g. `schema.table.column`) |
| `entity_type` | TEXT NOT NULL | One of the 20 enumerated types (`ENTITY_TYPES`) |
| `properties_json` | TEXT | JSON blob: description, data_type, business_meaning, `layer` (`logical` · `physical` · `authored` · `observed`), etc. |
| `source` | TEXT NOT NULL | Origin: `manual`, `mcp_discovery`, `llm_inferred` |
| `source_detail` | TEXT | Optional metadata (MCP server ID, tool name) |
| `created_at` | TIMESTAMP | UTC creation time |
| `updated_at` | TIMESTAMP | UTC last modification |

**Unique constraint:** `(kg_id, name, entity_type)`

**Indexes:**
- `idx_kg_entities_kg_id` — Per-graph scans
- `idx_kg_entities_profile_user` — Fast profile/user filtering
- `idx_kg_entities_type` — `(kg_id, entity_type)` type-filtered queries
- `idx_kg_entities_name` — `(kg_id, name COLLATE NOCASE)` case-insensitive name lookup

### Relationship Schema

**Table:** `kg_relationships` (SQLite, `tda_auth.db`)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-incrementing relationship ID |
| `kg_id` | TEXT NOT NULL | The graph this edge belongs to |
| `profile_id` | TEXT NOT NULL | Owning profile |
| `user_uuid` | TEXT NOT NULL | Owning user |
| `source_entity_id` | INTEGER FK | Source node (CASCADE delete) |
| `target_entity_id` | INTEGER FK | Target node (CASCADE delete) |
| `relationship_type` | TEXT NOT NULL | One of the 11 enumerated types (`RELATIONSHIP_TYPES`) |
| `cardinality` | TEXT | Optional: `1:1`, `1:N`, `N:M` |
| `metadata_json` | TEXT | JSON blob: description, confidence, evidence |
| `source` | TEXT NOT NULL | Origin tracking |
| `created_at` | TIMESTAMP | UTC creation time |

**Unique constraint:** `(kg_id, source_entity_id, target_entity_id, relationship_type)`

**Indexes:**
- `idx_kg_relationships_kg_id` — Per-graph scans
- `idx_kg_relationships_profile_user` — Fast profile/user filtering
- `idx_kg_relationships_source` — Outgoing edge queries
- `idx_kg_relationships_target` — Incoming edge queries
- `idx_kg_relationships_type` — `(kg_id, relationship_type)` type-filtered queries

Graph-level metadata (name, description, `is_active`, `kg_store_config_id`, source descriptor, source validation, lifecycle state, subscription stamps) lives in `kg_metadata` (created/migrated by `GraphStore._ensure_kg_metadata_table`); profile assignments in `kg_profile_assignments`; per-entity usage telemetry in the separate `kg_entity_usage` table (`kgstore/usage.py`).

### Type Enumerations

**Entity Types (20)** — the closed enum `kgstore.types.ENTITY_TYPES` (= `LOGICAL_TYPES | PHYSICAL_TYPES`, re-exported by `graph_store.py`); the normative definitions are [ONTOLOGY_ARCHITECTURE.md §2.3](ONTOLOGY_ARCHITECTURE.md). The logical relation/field are `entity`/`attribute`; `table`/`column` are their **physical** twins, linked by `implements` (an old-model payload using `table`/`column` for the logical layer is normalised by `kgstore.migrations.migrate_kg_payload_types` at every write chokepoint).

| Group | Types | Example |
|-------|-------|---------|
| Logical structure | `database`, `domain`, `entity`, `attribute`, `foreign_key` | `SalesDB`, `Finance`, `Order`, `Order.order_date` |
| Semantics | `business_concept`, `taxonomy`, `metric` | `Customer Lifetime Value`, `Region`, `Revenue` |
| Product description | `query_recipe`, `design_decision`, `quality_metric`, `naming_standard`, `product_bridge` | a proven query, a declared cross-product link |
| Stacked contracts (signed on their own) | `data_product` (OBDA-READ), `action_type` (OBDA-ACT) | a consumption contract, a write contract |
| Physical | `catalog`, `schema`, `table`, `column`, `ddl_artifact` | `airlines_core.ffp_member`, a deployed view |

> **Glossary concepts and retrieval quality (Sep 2026).** A handful of declared concepts help; a full business glossary does not. The industry-model constructor's `include_glossary` option adds one `business_concept` per distinct glossary term (5,051 on the airlines model), and those nodes compete with tables and columns for the enrichment's seed slots and node budget — the recorded-conversation gate `test_kg_conversation_bench` dropped from 49/0 to 5 failures with them present. Build agent-facing industry-model graphs without it (it is off by default), and remove it from an existing graph through check-update / apply-update with `options_override: {"include_glossary": false}` (`docs/Architecture/INDUSTRY_MODEL_KG_CONSTRUCTOR_PLAN.md` D4).

**Relationship Types (11)** — `kgstore.types.RELATIONSHIP_TYPES`:

| Type | Semantic | Example |
|------|----------|---------|
| `contains` | Parent → child containment | logical `database → entity → attribute`; physical `schema → table → column` |
| `foreign_key` | FK reference | `orders.customer_id → customers.id` |
| `is_a` | Inheritance/taxonomy | `Premium Customer is_a Customer` |
| `has_property` | Concept attachment (entity → business_concept) | `Order has_property "Fulfilment SLA"` (legacy `entity→attribute` `has_property` is normalised to `contains` at import + startup — ONTOLOGY §2.4) |
| `measures` | Metric measurement | `Revenue measures Sales Performance` |
| `derives_from` | Calculation derivation | `Profit derives_from Revenue - Costs` |
| `depends_on` | Dependency | `Report depends_on Daily ETL` |
| `relates_to` | Generic association | `Marketing relates_to Sales` |
| `implements` | Physical node → the logical node it realises | `airlines_core.ffp_member implements loyalty.ffp_member` |
| `member` | Data-product membership (data_product → entity) — a governance claim, not containment | `<data product> member <entity>` |
| `acts_on` | Action type → the one entity a signed write contract may touch | `record_lead acts_on Lead` |

**Source Origins** (`source` is a provenance tag; the common values — constructors also stamp their own, e.g. `constructor_agent`, `constructor_cross_layer`, `kg_constructor_*`):

| Source | Meaning | Set By |
|--------|---------|--------|
| `manual` | User-created via REST API or import | REST endpoints, import |
| `mcp_discovery` | Reserved for MCP schema auto-discovery | Discovery module (V2 stub — unused) |
| `llm_inferred` | Created by LLM during conversation | `TDA_KnowledgeGraph` tool |
| `constructor_structural` | Inferred during KG generation (Phase 3 gap-fill) | `kg_generate()` pipeline |
| `constructor_fk_inferred` | FK relationships inferred from naming conventions, shared columns, or column properties (Phase 3.5) | `kg_generate()` pipeline |

---

## Backend Architecture

### GraphStore Class

**Location:** `components/builtin/knowledge_graph/graph_store.py`

The `GraphStore` class is a **thin delegate wrapper** — the single access point for all graph operations. It is instantiated per request for one graph; when `kg_id` is omitted the owner profile's **active** graph is resolved lazily (`_resolve_active_kg_id`, `kg_metadata.is_active`), with `profile_id` as the legacy fallback id:

```python
store = GraphStore(profile_id="profile-123", user_uuid="user-456",
                   kg_id=None, db_path=None, kg_store_config_id=None,
                   allow_subscribed_write=False)
```

**Write guards.** `_write_mode()` decides what a caller may write: `all` for an owned graph (or the subscription sync path, which passes `allow_subscribed_write=True`); `authored_only` for a SUBSCRIBED graph with OBDA-MODEL available — its base layer is read-only and only the subscriber's own authored extension may be edited, node by node (`_assert_layer_writable`); with OBDA-MODEL off a subscribed graph refuses every write. Whole-graph writes (`import_bulk`, `clear_graph`, `replace_model_layer`) run `_assert_writable()` and raise `KnowledgeGraphReadOnlyError` unless the mode is `all`. The authored layer and its proposals are described in [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md).

**Change announcements.** Every write seam is decorated with `@_signals.announces("knowledge_graph", <action>, ids=(kg_id,), aspect=…)` (`add_entity`, `update_entity`, `delete_entity`, `add_relationship`, `delete_relationship`, `import_bulk` → `imported`, `clear_graph` → `cleared`, `set_kg_metadata` → aspect `metadata`, `set_lifecycle_state` → aspect `lifecycle`; versions and releases announce from `core/kg_version_db.py` / `core/kg_release.py`). The page receives ONE `uderia:component-changed` event per write — see [Component Signals](#component-signals).

`_get_backend()` is lazy-evaluated on first use. It resolves the correct backend via the `kgstore/factory` resolution chain:
1. Reads `kg_store_config_id` from `kg_metadata` for the active KG (one SQLite query, cached)
2. Passes that `config_id` to `get_backend_for_profile()` — which looks up the named config, falls back to user default, then SQLite global fallback

This means graph operations always route to the same backend that the KG was originally constructed on, regardless of what the profile's current default is.

### Entity CRUD

| Method | Signature | Behavior |
|--------|-----------|----------|
| `add_entity()` | `(name, entity_type, properties?, source?, source_detail?) → int` | Upsert via `INSERT ON CONFLICT UPDATE`. Returns entity ID. |
| `get_entity()` | `(entity_id) → dict?` | Lookup by ID within profile scope. |
| `get_entity_by_name()` | `(name, entity_type?) → dict?` | Case-insensitive name lookup. Optional type filter. |
| `search_entities()` | `(query_text, limit?, entity_type?) → list[dict]` | Substring match on `name` and `properties_json`. |
| `list_entities()` | `(entity_type?, limit?) → list[dict]` | List all entities, optionally filtered by type. Default limit: 200. |
| `update_entity()` | `(entity_id, properties) → bool` | Merge new properties into existing `properties_json`. |
| `delete_entity()` | `(entity_id) → bool` | Delete entity and all relationships (CASCADE). |

### Relationship CRUD

| Method | Signature | Behavior |
|--------|-----------|----------|
| `add_relationship()` | `(source_entity_id, target_entity_id, relationship_type, cardinality?, metadata?, source?) → int` | Upsert via `INSERT ON CONFLICT UPDATE`. Returns relationship ID. |
| `get_relationships()` | `(entity_id, direction?) → list[dict]` | Get edges from/to entity. Direction: `outgoing`, `incoming`, or `both`. Deduplicates for self-referential edges. |
| `list_relationships()` | `(entity_id?, relationship_type?, limit?) → list[dict]` | List with optional filters. Default limit: 200. |
| `delete_relationship()` | `(relationship_id) → bool` | Delete single edge. |

### Bulk Operations

| Method | Signature | Behavior |
|--------|-----------|----------|
| `import_bulk()` | `(entities, relationships) → {entities_added, entities_updated, entities_rejected, relationships_added, relationships_updated, relationships_rejected, relationships_unresolved, …}` | THE write chokepoint. Single-transaction batch import; entities resolved by name for relationship linking. Aug 2026: **validates types** (unknown entity/relationship types are rejected and counted, not written), **stamps `layer`** when absent (physical types→physical, else logical), **carries `source_detail`** (was hard-coded NULL), normalises the legacy `has_property` entity→attribute edge to `contains`, and reports rows actually **inserted vs updated** (they used to report rows submitted, so an idempotent re-import "added" everything). |
| `set_kg_metadata()` | `(name, database_name?, description?, is_active=True) → kg_id` | Register or update KG metadata. When `is_active=True` (default) deactivates all other KGs for the profile. When `is_active=False` inserts as inactive — used on import when the profile already has an active KG. |
| `get_kg_metadata()` | `() → {kg_id, name, database_name, is_active, created_at, updated_at}?` | Fetch metadata for the current KG instance. |
| `list_all_graphs()` | `(user_uuid, db_path?) → [{kg_id, profile_id, kg_name, total_entities, is_active, kg_store_config_id, ...}]` | Static — returns all KGs for a user across all profiles (one row per `kg_id`). Always includes `kg_store_config_id` so the REST endpoint can route live-stat fetches to the correct backend. |
| `replace_model_layer()` | `(entities, relationships, keep_layers?) → {…, kept_edges_dropped, extensions_replaced, realisation_marks_carried, dangling_stamped, realisations_orphaned}` | **THE resync operation** — apply-update, subscription sync (`core/kg_subscription.py`), agent-pack `update_in_place` refresh (`agent_pack_manager`, gate `test_agent_pack_kg_refresh.py`), declarative redeploy and version restore all use it instead of `clear_graph()` + `import_bulk()`. Replaces only what a walk produces and keeps what `kgstore.types.is_kept_on_replace` says to keep: the `authored` and `observed` layers, an extension's realisation, and the stacked contracts (`data_product`, `action_type`). A payload that carries an extension replaces that extension only. Kept edges whose other end disappeared are reported and stamped (`dangling_edges`), never silently lost. Warms the semantic index. |
| `merge_authored_layer()` | `(entities, relationships) → dict` | Merge an authored-layer payload without touching the walked model. |
| `clear_graph()` | `() → {entities_deleted, relationships_deleted}` | Delete all data of this graph (uninstall, owner-initiated clear) — a delete, never a resync. |

**Import format:**
```json
{
  "entities": [
    {"name": "orders", "entity_type": "table", "properties": {"description": "Customer orders"}}
  ],
  "relationships": [
    {"source_name": "orders", "source_type": "table",
     "target_name": "customers", "target_type": "table",
     "relationship_type": "foreign_key", "cardinality": "N:1"}
  ]
}
```

### NetworkX Graph Algorithms

All graph algorithm methods lazy-load the full graph from SQLite into a `networkx.DiGraph` on first call. The SQLite backend caches one DiGraph per `kg_id` in a process-wide LRU (`SQLiteKGBackend._graph_cache`, with a name index beside it); any write to that graph drops its entry (`invalidate_cache(kg_id)`).

| Method | Signature | Algorithm | Use Case |
|--------|-----------|-----------|----------|
| `extract_subgraph_adaptive()` | `(seed_entity_ids, query_entity_ids?, max_nodes?) → {entities, relationships}` | Three-phase entity-type-aware extraction (see below) | Context enrichment, visualization |
| `extract_subgraph()` | `(entity_ids, depth?, max_nodes?) → {entities, relationships}` | Legacy BFS traversal (both directions) from seed entities | Deprecated — use `extract_subgraph_adaptive()` |
| `get_full_graph()` | `(max_nodes?) → {entities, relationships}` | Full graph export (capped) | Full visualization |
| `find_shortest_path()` | `(source_id, target_id) → list[int]?` | Undirected shortest path | "How are these tables related?" |
| `get_connected_entities()` | `(entity_id) → {descendants, ancestors}` | Directed reachability | Impact analysis |
| `get_entity_importance()` | `() → {entity_id: float}` | Degree centrality | Node sizing in visualization |
| `detect_cycles()` | `() → list[list[int]]` | Simple cycle detection (capped at 20) | Taxonomy validation |
| `get_stats()` | `() → dict` | Entity/relationship type breakdowns, connected components, density, cycle detection | Dashboard, card metadata |

#### Adaptive Subgraph Extraction Algorithm

The `extract_subgraph_adaptive()` method replaces the fixed-depth BFS with a scalable, entity-type-aware algorithm designed for schemas with 1000s of entities. In the current vocabulary "table" and "column" in the walkthrough below mean the **logical** relation and field (`entity` / `attribute`; the walked set is `kgstore.types.EXPANDABLE_STRUCTURAL_TYPES = {entity, foreign_key}`) — a physical `table`/`column` seed reaches them through its `implements` twin (see *Layered Graphs* below). It uses three phases:

```
Phase 1: Structural Discovery (unbounded)
├── Phase 1a: FK-Chain Traversal
│   BFS from seeds through table/foreign_key nodes ONLY.
│   NO depth limit → handles 3, 4, 5, N-way JOIN chains.
│   Skips database hub (would explode), columns (detail), semantic nodes.
│
├── Phase 1b: Joinable Table Discovery
│   For each discovered table → collect its column names.
│   Find OTHER tables sharing those column names → join candidates.
│   Iterate up to 3 rounds for transitive joins:
│     Round 1: orders(customer_id) → discovers customers(customer_id)
│     Round 2: customers(region_id) → discovers regions(region_id)
│     Round 3: regions(country_id) → discovers countries(country_id)
│
└── Phase 1c: Database Context
    For each discovered table → add its parent database entity.
    Context only — don't expand from database nodes.

Phase 2: Column Expansion (budget-aware)
    column_budget = max_nodes - structural_count
    Sort tables: query-matched first, then by distance from seeds.
    Add columns per-table until budget exhausted.

Phase 3: Semantic Enrichment (capped at 50)
    Add business_concept, metric, taxonomy, domain nodes
    connected to discovered structural entities.
```

**Key design choices:**
- **Entity-type awareness**: Only `entity` and `foreign_key` nodes are expanded during structural discovery. Database hub nodes (which connect to all tables) are explicitly excluded to prevent explosion.
- **No fixed depth limit**: FK chains are traversed without bound, ensuring 3, 4, 5, or N-way JOINs are always discovered.
- **Iterative joinable-table discovery**: Even without explicit FK edges, tables are connected through shared column names (e.g., `customer_id` in both `orders` and `customers`). Up to 3 iterative rounds discover transitive join paths.
- **Budget-aware column expansion**: Columns are added only after structural discovery, prioritizing query-matched tables and limiting to the remaining node budget.
- **Default `max_nodes=500`** (up from 50): Supports schemas with hundreds of tables while keeping output manageable.

**Scalability:**
| Schema size | Typical result |
|-------------|---------------|
| 10 tables | All tables + all columns (~100 nodes) |
| 100 tables | ~30-50 relevant tables + columns for top priority (~300 nodes) |
| 1000 tables | ~30-100 relevant tables + columns for top ~50 (~500 nodes) |

All phases are O(V + E) combined.

**Aug 2026 — one shared pure extractor + relevance-scoped mode + backend parity.** The
algorithm is now a single **module-level pure function** `_adaptive_subgraph_from_graph(G, seeds, query_ids, max_nodes, query_terms, per_table_cap)` over a NetworkX `DiGraph`; `SQLiteKGBackend.extract_subgraph_adaptive` calls it directly (proven byte-identical to the prior inline algorithm), and `Neo4jKGBackend` materializes its KG into a `DiGraph` (`_to_networkx`) and calls the **same** function — so extraction is identical across backends (no untested dialect Cypher), advertised as the `ADAPTIVE_SUBGRAPH` capability. When `query_terms` / `per_table_cap` are supplied (the enrichment path always does), Phase 2 becomes **relevance-scoped**: seed columns first, then query-matching columns, then join keys, capped per table (the cap is the `intensity` per-table column cap above), with the withheld remainder counted in `omitted_columns` (→ the "+N more columns" hint). Scoped mode also **skips the greedy Phase 1b joinable round** — with a real query it does not need to speculatively pull in every name-coincident table, which is what let a large estate blow the budget on irrelevant tables before this change.

### Cross-Profile Enumeration

**Static method** (no instance needed):

```python
GraphStore.list_all_graphs(user_uuid) → list[dict]
```

Returns one entry per graph (`kg_id`) registered in `kg_metadata` for the user, with summary statistics (counts, type breakdowns, timestamps, `is_active`, `kg_store_config_id`). Used by the management UI to show all knowledge graphs across profiles; `GET /v1/knowledge-graph/list` enriches each row with backend, assignment, subscription and source-validation data.

---

## Component Handler

**Location:** `components/builtin/knowledge_graph/handler.py`

The `KnowledgeGraphHandler` serves dual purposes:
1. **Tool handler**: Processes `TDA_KnowledgeGraph` tool calls from the LLM
2. **Context enrichment**: Provides `get_context_enrichment()` for planner guardrail injection

### Tool Actions

The `TDA_KnowledgeGraph` tool accepts an `action` parameter; `validate_arguments` accepts **16** actions (the manifest advertises the 12 core ones; `query_product` / `list_products` are the OBDA-READ aliases and `propose_entity` / `propose_attribute` the OBDA-MODEL ones — unadvertised here, offered through their own components):

| Action | Arguments | Returns | Purpose |
|--------|-----------|---------|---------|
| `query` | `query_text?`, `entity_type?` | HTML results or stats | Look up entities by NAME (substring over name + properties). The phrase is tried first; when it matches nothing the lookup falls back to its meaningful terms (each with its singular), ranked by how many terms a node satisfies — so a whole question still surfaces the nodes it is about. A true miss is recovery-shaped (graph size, types, sample names, what a lookup matches, the next step) and carries `suppress_inline` so no dead card reaches the answer. No `query_text` → graph statistics. Gate: `test/test_kg_query_lookup.py`. |
| `visualize` | `entity_name?`, `depth?`, `title?` | D3 force-graph spec | Interactive subgraph preview (inline) |
| `open` | `entity_name?` | Panel spec | Open the full graph panel, focused on the named concept (see [KG_INSPECTOR_ARCHITECTURE.md](KG_INSPECTOR_ARCHITECTURE.md)) |
| `add_entity` | `entity_name`, `entity_type`, `properties?` | Confirmation HTML | Add entity (source: `llm_inferred`) |
| `add_relationship` | `entity_name`, `target_entity`, `relationship_type`, `entity_type?`, `target_entity_type?` | Confirmation HTML | Create edge between entities |
| `get_context` | `query_text?` | Pre-formatted enrichment text | Debug/test context enrichment output |
| `snapshot` / `versions` / `restore` | `label?` / — / `version_id` | Version card | Version history (`core/kg_version_db.py`) |
| `compile_metric` | `metric_name`, `dimensions?`, `measures?` | Compiled SQL | Governed, ratio-safe metric SQL (`metric_api.py`) |
| `declare_concept` | `entity`, `concept_name`, `definition` | Confirmation | Declare a business concept once (`concepts.py`) |
| `probe_bridge` | `bridge_name` | Evidence card | Test a declared `product_bridge` (`bridge_probe.py`) |
| `query_product` / `list_products` | product args | Product result | Unadvertised aliases of OBDA-READ (`TDA_DataProduct`) |
| `propose_entity` / `propose_attribute` | entity/attribute args | Advisor verdict | OBDA-MODEL proposals onto the authored layer (gated on the OBDA-MODEL plane + the agent's `modelAuthoring` grant) |

**Write refusals.** `_KG_WRITE_ACTIONS` (`add_entity`, `add_relationship`, `restore`, `declare_concept`, `propose_*`) are refused for an agent grounded on a release (Model B, below). Under an `enforce` OBDA model policy the **direct edits** (`_KG_DIRECT_EDITS`: `add_entity`, `add_relationship`, `declare_concept`, `restore`) are refused for an agent — the graph then changes only through a proposal a person lands and publishes.

All actions return `ComponentRenderPayload` with either `html` (inline display) or `spec` (D3 visualization data).

### Context Enrichment (Guardrail)

The core intelligence feature. Called automatically before the planning/answering call of every engine (see [Planner Integration](#planner-integration)):

```python
async def get_context_enrichment(query, profile_id, user_uuid, max_nodes=500,
                                 column_cap=None, record_usage=True,
                                 session_id=None, workflow_history=None) -> tuple  # (text, metadata)
```

It returns the prompt text and a metadata dict (the `kg_enrichment` event payload: entity/relationship counts, `scope_databases` / `scope_entities` for the grounding gate, `join_keys`, `fanout_keys`, `text_date_columns`, `value_domains`, `table_columns_full`, `semantic_seeding`, `carry_over`, `model_gap`, `data_products`, `kg_id` / `kg_name`, …). The companion `get_trusted_scope()` resolves the bound graph's whole-KG deployed scope independently of the query, cached per KG on a content signature.

**Pipeline:**

```
User Query: "Show me orders with high revenue customers"
                    │
                    ▼
    ┌───────────────────────────────┐
    │  1. Tokenize query            │
    │     → ["orders", "high",      │
    │        "revenue", "customers"]│
    └───────────────┬───────────────┘
                    │
                    ▼
    ┌───────────────────────────────┐
    │  2. Search entities           │
    │     Full query + individual   │
    │     keywords (skip stop words)│
    │     → match: orders (table),  │
    │       revenue (metric),       │
    │       customers (table)       │
    └───────────────┬───────────────┘
                    │
                    ▼
    ┌───────────────────────────────┐
    │  3. Adaptive extraction       │
    │     max_nodes=500             │
    │     → FK chains (unbounded),  │
    │       joinable tables,        │
    │       budget-aware columns    │
    └───────────────┬───────────────┘
                    │
                    ▼
    ┌───────────────────────────────┐
    │  4. Format for prompt         │
    │     Structured text with      │
    │     entity types, properties, │
    │     and relationships         │
    └───────────────┬───────────────┘
                    │
                    ▼
    Injected into planner's
    knowledge_context_str
```

**Aug 2026 — declared-FK rendering, usage telemetry, semantic seeding.** Enrichment
refinements, all self-gating so a bare / un-instrumented graph renders identically:

- **Declared foreign keys are preferred over name coincidence.** When `foreign_key`
  edges exist the prompt renders a `DECLARED FOREIGN KEYS (verified …)` block *before*
  the name-based `JOINABLE COLUMNS` block; a joinable that a real FK backs is
  cross-tagged `[declared FK]`, and JOINABLE is explicitly labelled a hint ("NOT a
  verified key"). No DECLARED block renders on an FK-less graph (regression-safe).
- **Usage telemetry as a ranking prior.** A **separate** `kg_entity_usage` table
  (`kgstore/usage.py` — never `kg_entities`, so recording never invalidates the graph
  cache or churns trust hashes) records `seeded` / `rendered` / `used` per entity,
  fire-and-forget off the hot path. `priors()` feeds a bounded, log-saturated prior
  into seed ranking that is `0.0` on an un-instrumented graph (byte-identical ranking)
  and can only break near-ties — it can never displace a real lexical match
  (`_USAGE_PRIOR_MAX` sits below a leading-prefix match).
- **Semantic seeding** (`kgstore/semantic_index.py`, `APP_CONFIG.KG_SEMANTIC_SEEDING` —
  shipped opt-in in Aug 2026, **ON by default** since; see [Semantic Seeding](#semantic-seeding--the-second-recall-channel)). When on, the query's nearest entities *by
  meaning* (cosine over an injectable embedder, per-KG index cached by content
  signature, bounded, never raising into retrieval) are unioned into the lexical seed
  pool, so a synonym the lexical seeder cannot match ("customers" → an entity named
  `Party`) still surfaces. A perfect semantic-only match still ranks below an exact
  lexical identifier match — semantic widens recall, it does not override precision.
- **Structured facts for the preflight guard — one computation, two consumers.** Beside the
  rendered text the enrichment metadata carries machine-readable lists that the SQL
  preflight guard (`agent/preflight/`) records per session at the trusted-scope seam and
  checks every raw-SQL call against before the database sees it: `join_keys` (from
  `_declared_join_pairs`, the same function that renders the DECLARED FOREIGN KEYS block)
  and `text_date_columns` (from `_text_date_columns`, keyed by the DEPLOYED table when a
  capability resolved one). The latter is built on ONE predicate, `_is_text_date_attr` —
  a DATE deployed as characters wide enough to hold one (`_MIN_DATE_WIDTH` = 8; a
  one-character flag whose description merely mentions a date is not a date column) —
  which is also the predicate behind the enrichment's "a DATE stored as CHAR(11) text"
  warning line, so what the agent is told and what the guard refuses cannot diverge.
  What the ontology cannot DECLARE — the SHAPE the text actually holds (`INCDATE` declares
  MM/DD/YYYY and holds `31-OCT-2023`) — a constructor can SAMPLE: with `validate_with_source`
  every constructor with a live source (`agent/kg_constructors/source_validation.py`, one pure
  core; the runner is the ANDP cursor, the industry-model deploy backend after its load, or the
  discovery constructor's resolved `run_sql` tool role) stamps `sampled_shape`/`sample_value`/
  `date_range` on the attribute and `row_count`/`key_unique` on the entity. The enrichment then
  states the exact `CAST(col AS DATE FORMAT '…')` and the data span, the entity line states the
  row count and whether the declared key really identifies a row, and `text_date_columns` carries
  the shape so the guard is seeded without a probe. Absent a sampled shape, the guard still learns
  it from result rows at the chokepoints (`components/dateforms.py:column_shape`), process-wide —
  and a learned shape wins over the ontology's, logged, because the data is fresher.
  **What the validation reads and how (measured on the 58-million-row NIBRS product):** ONE
  bounded `TOP 200000` sample per table yields two lexical witnesses per text-date column (the
  shape), then ONE exact `COUNT(*)` per table, then ONE sampled read per table chunk carrying the
  blank counts, the date ranges and the share of non-date values, then one exact `GROUP BY` per
  declared key — 283 s in all, apply 0 s (the checked candidate is reused). The probes read a
  text date through the regex-guarded form `dateforms.guarded_cast` — `CAST(CASE WHEN
  REGEXP_SIMILAR(TRIM(col), '<the shape's pattern>', 'i') = 1 THEN TRIM(col) END AS DATE FORMAT
  '…')` — because Teradata's `TRYCAST` accepts no FORMAT phrase (Error 3706) and a plain CAST
  fails on a value outside the shape (Error 2666); that form is also the DEFINITION of the
  unparseable share. **It is not what the agent is told to write.** Measured on the 12.35M-row
  Incident table (2026-09-05): a text slice 15 s · a plain CAST 18 s · a `CHAR_LENGTH` guard 20 s ·
  a rebuilt-ISO `TRYCAST` 52 s · the regex guard past the endpoint's 110 s ceiling — so
  `dateforms.date_read_form`, ONE chooser for the enrichment, the preflight refusal and the
  rewrite, recommends the cheapest safe read: a measured share of non-date values → the regex-free
  `safe_cast` (the ISO text rebuilt from the shape's fields and `TRYCAST`, NULL for anything that
  is not a date); a SAMPLED zero share → `length_guarded_cast` (skips blanks and short junk — a
  sampled zero is not a proof, and this costs nothing); an exact zero → the plain CAST. And the
  cheap forms come FIRST: a period bucket or a year/month filter is a fixed slice of the text
  (`text_period_forms` — `SUBSTR(col, 8, 4)` is the year of a DD-MMM-YYYY, `SUBSTR(col, 4, 3)` the
  month, a `POSITION` expression its number), which converts no row; the guard passes a slice
  aligned to the shape's fields (`slice_aligned`) and refuses only one that cuts across them.
  **The stamp travels with a pack:** an agent-pack export writes each graph's stamp into its graph
  file, and every import path (fresh install, in-place refresh, split create) applies it after the
  import — the publisher's stamp verbatim with its `validated_at`, else derived from the stamped
  nodes, else cleared — so a consumer's badge says when the PUBLISHER validated, never when the
  copy was made, and a refresh from an unvalidated source cannot leave a stale badge.
  A DATE produced by such a cast carries its FORMAT, and a bare string literal beside it is
  converted with that FORMAT (`'2023-01-01'` beside a DD-MMM-YYYY cast: Error 2665, measured) —
  the guard's `date_expr_literal` finding, resolved through a select alias too, rewrites the
  literal to a typed `DATE '…'`. **The cards say when:** `set_source_validation` stamps
  `{validated_at, tables, keys_checked, keys_not_unique, shapes, date_ranges, blank_ratios,
  probe_errors, seconds}` on the graph's METADATA (never on a node, which would move the signed
  content hash of an unchanged graph), derived from the graph's content by `has_source_validation`
  and cleared by an unvalidated resync; both card renderers show *Source-validated · date* beside
  the backend badge. The option lives in all three constructor dialogs and in the Update dialog,
  preset to what the graph was built with (`validate_with_source` in the source descriptor;
  `entry_options` reads a resync override on an ANDP graph's FLAT descriptor). A validated
  update changes the graph's content, so its attestation reads *changed* until re-signed.
  Beside them, `value_domains` (from `_value_domains`): per deployed table, every attribute's declared value set — a coded column's codes and, for the decoded-label half of a two-column decode, the domain's labels (the `declared_model` walker stamps `decoded_from` / `decoded_labels` on the label and `decoded_as` on the code, linking the pair by the one fact both share, `source_column`; the enrichment renders the label column's values from it). The preflight VALUE guard records them per session: a literal a CLOSED declared domain does not contain is provable from the ontology alone (a range entry or a missing-codes-only set is open and proves nothing).

### Query Search Algorithm

The search combines two strategies for maximum recall:

1. **Direct substring search**: The full query string is matched against entity names and `properties_json`
2. **Tokenized keyword search**: Individual words (3+ chars, stop words excluded) are each searched independently

Results are deduplicated by entity ID and capped at 15 matches.

**Stop words excluded:** the ONE declared set `handler._STOP_WORDS` (`the`, `show`, `get`, `list`, `find`, … and the function words `per`, `via`, `versus`, `also`, `just`, `very`, `its`, `our`, `your`, `their`, `these`, `those`, `there`, `then`). A 3-letter function word is a substring of decoded values and descriptions — `per` in "Personal", "person", "perpetrator" — and on *"robberies per month"* it was a matched term of 9 of 14 seeds (prod session ce559107, turn 2).

**What may become a seed** (`_rank_seeds`, deterministic, 0-token):
- **Only a row the question reached.** A row with no evidence (score 0) never fills a free slot — it came from the full-question substring search on a word the scorer does not count. On *"robberies per month"* a correspondence entered the cut that way and pulled the whole evidence subject area into a crime-count turn. When NOTHING scores, the pool is kept (a question the scorer cannot read still gets its candidates).
- **A passing mention yields to stronger evidence** (`_drop_weak_mentions`). A row whose only evidence is a description mention (5 points) of a word some other row answers more strongly is dropped — the `crime` domain is an exact match, so every `ori` column whose description mentions "the agency reporting the crime" no longer takes a slot. A mention that IS the best evidence for its word stays: *"month"* reaches `incident_date_text` only through its description, and that is the column the question turns on. A row with any stronger signal is never touched.

Measured on the prod question against the local Policing Core Model: 15 seeds → 4 (the crime domain, the two robbery codes, the month column).

### Model Gap — "Not in this model"

When the question's SUBJECT matches nothing in the whole graph, presenting another entity in its place is the failure (evidence types read back as "the open leads"). `_question_subject` takes the head noun after a question opener ("which …", "how many …", "list …", "show me …"), skipping determiners, state adjectives and qualifiers (`_SUBJECT_SKIP` — "show me the ACTUAL email" / "show me ONE email" once read back as *Not in the model: actual*), and `_model_gap` claims a gap only when neither that word nor its singular was reached by name, label, description, declared value or meaning (any semantic bridge withdraws the claim). The enrichment then LEADS with a `NOT IN THIS MODEL:` line (`_render_gap_lead`) and carries `metadata["model_gap"] = {term, singular, may_author, entities}`; the offer to extend the model is rendered by the platform under the answer (`agent/model_gap.py`), and the model is asked only to `propose_entity` when the user asks. Armed only while the OBDA-MODEL plane is enabled for the agent, so the baseline is byte-identical. Gate: `test/test_model_gap.py`.

### Semantic Seeding — the Second Recall Channel

Lexical search matches the **words** a question used. It is precise and it is literal:
a question about "customers" finds nothing against an entity called `Party`, and
ranking can only order what the pool already contains. Semantic seeding is a second
channel that matches what the question **meant**.

**How it works** (`src/trusted_data_agent/kgstore/semantic_index.py`):

1. Each entity's `name-leaf + label + description + business_meaning` is embedded once
   (the platform's configured `RAG_EMBEDDING_MODEL` — the same model the knowledge
   repositories use), L2-normalised, and stored as ONE float32 matrix per graph.
2. At query time the question is embedded and cosine similarity is a single
   `matrix @ query`; the top-k comes from `argpartition` (linear), filtered at
   `min_score` (0.35 default, `top_k` 10).
3. Hits are **unioned into the lexical candidate pool** and passed to `_rank_seeds` as
   `semantic_scores`, contributing a bounded bonus (`_SEMANTIC_SCORE_MAX = 45`).

**Lexical ranking stays authoritative.** The bonus is calibrated deliberately between a
leading-prefix lexical match (+40) and an exact identifier match (+100): a strong
synonym *competes* with a lexical hit, and can never displace an exact match. Semantic
seeding widens recall; it does not override precision.

**ON by default** (`KG_SEMANTIC_SEEDING`, env `TDA_KG_SEMANTIC_SEEDING`, admin toggle at
Administration → App Config → AI & Knowledge → *Knowledge Graph Retrieval*). Disabling
it — or running on a machine without numpy — leaves retrieval exactly lexical.

### Conversational Carry-Over — the Third Recall Channel

Lexical and semantic seeding are both functions of the **question**. A follow-up
names nothing a graph holds: *"Give me the names of those customers"* seeds every
`*_name` column in the model (`accounts_receivable.customer_name`,
`service_bulletin.oem_name`) and not `ffp_member`, the table the whole previous turn
was about. Observed on production (session `0aa3a650`, 2026-09-19): the agent then
guessed a non-existent column, joined a finance table for the names, and took seven
LLM rounds and four SQL statements to answer a question the conversation already knew
the answer to.

**How it works** (`handler.previous_turn_carry_over`, pure; seeded in
`get_context_enrichment`):

1. Every enrichment caller passes the `session_id` (the executor's legacy path, the
   `knowledge_graph_context` context-window module, the genie coordinator); the
   handler reads the session's `workflow_history` through the ONE session reader.
2. Walking the previous turns newest-first (bounded lookback of 3), the first turn whose
   enrichment showed the model entities contributes the ones its data tool calls
   actually **referenced** — `kgstore.usage.touched_entities`, the same definition of
   "used" the `kg_seed_utilization` scorer and the usage telemetry share — ordered
   **tables → descriptive columns → key columns**, deduped, capped at 8. A turn that
   showed entities but read nothing (a refusal, an answer from memory) falls back to
   what it was itself carrying (continuity is transitive) or its top seeds; a session
   primer is never a topic.
3. The resolved entities are inserted into the seed list **behind the seeded data
   products and ahead of the lexical ranking**, and they count as query seeds — so
   their columns are tier 0 (never capped) and their tables get the column budget
   first.

The event carries `carry_over` (`from_turn`, `source`, `entities`); the Live Status
card shows a *Carried over* row and the collapsed title reads `, N carried from the
previous turn`; the KG theatre names the channel. A session with no usable previous
turn seeds exactly as before.

### Layered Graphs — One Candidate per Fact, and the Physical Twin Expands

On a graph with a logical and a physical layer the same fact is two nodes with the
same text — `loyalty.mileage_accrual.base_miles` (attribute) and
`airlines_core.mileage_accrual.base_miles` (column, which `implements` it) — and
substring search returns both. Two rules keep that from costing recall:

* **`_collapse_layer_twins`** (the seed pool, and the `query` lookup): a physical twin
  collapses onto its logical node before ranking, keeping arrival order and moving any
  semantic score it earned onto the survivor. Fifteen seed slots are fifteen concepts
  again (on the prod turn, nine of the first twelve seeds were physical copies).
* **Twin following** (`_adaptive_subgraph_from_graph`, Phase 1a): a physical seed also
  stands for its logical twin one `implements` hop away, because only the logical
  layer is structurally expandable — a physical column seed used to be a dead end whose
  table never entered `TABLE SCHEMAS` at all.

A physical-only or logical-only graph is unchanged by both.

### The Join Backbone and Declared Join Keys from Edges

The relevance-scoped column cap (intensity low 6 · medium 12 · high 24) fills with the
columns that mention the question's words. On *"top customers by miles flown"* it filled
with the twelve `*miles*` columns of `mileage_accrual`, and
`mileage_accrual.ffp_member_id` — the one declared key to `ffp_member`, a table sitting
in the same subgraph — was budgeted out: the FK edge never entered the rendered
subgraph, the DECLARED FOREIGN KEYS block was empty, the preflight guard had nothing to
enforce, and the model joined on `booking_segment_id = ffp_member_id`.

* **Join backbone** (`_join_backbone_columns`): a column that joins two tables **both in
  the subgraph** (either endpoint of a `foreign_key` edge whose owning tables are both
  discovered) or is a table's primary key is kept over the cap (bounded per table,
  `_BACKBONE_CAP`). A join column between two tables in play is not one more fact
  competing on relevance — it is what makes the two facts combinable. A key to a table
  NOT in the subgraph stays an ordinary column.
* **Endpoint types travel on edges**: `_subgraph_to_dict` emits `source_type` /
  `target_type`, without which `_declared_join_pairs` could not recognise a column-level
  FK and produced **no pairs on any graph whose keys are declared as column edges**
  (industry models, agent discovery). The derivation also recovers the types from the
  subgraph's own entities for an older edge shape.
* **The key from the edge**: where the two tables declare no `business_key` (the ANDP
  shape, which still wins where present), a pair with exactly ONE FK edge naming the
  SAME column on both sides gets that column as its checkable key — `ffp_member ⇄
  mileage_accrual — join on (ffp_member_id)` — and flows into `join_keys` for the
  preflight guard. Two FKs between one pair (a sender and a receiver) or differing
  column names stay un-keyed by construction (the renderer names the alternatives:
  *join on ONE of …*); a guard that enforced either would refuse legitimate joins.
* **The guard checks each side**: `preflight.joins.sql_joins` records the ON columns
  per table, and a same-name key is only joined on when both sides name it — the prod
  turn-1 join named `ffp_member_id` on the wrong side and used to pass.

* **Identity columns have their own tier**: `_column_tier` ranks a name-like leaf
  (`*name`, `first_name`, `last_name`, `title`, `label`) after the question-matching
  columns and before the key-ish ones (0 seed · 1 term · 2 identity · 3 key · 4 rest).
  A "who / which" question needs the column a row is called by whatever words it used;
  before, `ffp_member` rendered `address_line_1` and `postal_code` and budgeted out
  `member_first_name`, and the model went to a finance table for a name.
  **Place columns are the same class** (Sep 2026): `city`, `state`/`state_abbr`,
  `country`, `region`, `county`, `district` carry the proper nouns a question names
  ("in Chicago"). On the policing core model the medium cap cut `v_Agency.city` while
  showing `core_city_code` (a Yes/No flag), and "how many robberies in Chicago?" answered
  every core city — 3 of 3 runs, WITH a primer sentence saying "a city is `Agency.city`":
  prose the model ignored for the data it was shown. A `*_code` leaf never lifts.
  **Recorded follow-on (not built):** both identity lists are keyed on column NAMES —
  a heuristic that grows one entry per case (`brand`, `department`, `sku` would be next).
  The principled form derives the class from what a column HOLDS: a text column that is
  not a code and whose source-validated `sample_value`s are proper-noun-shaped is an
  identity column. That retires both name lists on validated graphs (the name tier stays
  as the fallback for unvalidated ones).
* **A question spanning both areas of a declared correspondence LEADS with it**
  (`_spanning_correspondences` + `_render_correspondence_lead`, Sep 2026): when the seeds
  the question matched fall in BOTH subject areas of a `type_correspondence` bridge, the
  block opens with the correspondence — the areas, "no per-record key, a join is refused",
  the basis, what it covers on each side as data, and the rule (state it; each area from
  its own tables; never one figure spanning both; an unlisted kind is not bound). The
  bridge entity line still renders below. Measured on the Ideate lane (9 runs): with the
  bridge only as an entity line near the bottom, a weak model wrote the cross-area JOIN 3
  of 9 times and, refused by the preflight guard, gave up twice — and the pack skill that
  had prevented it was restating the graph. The class is any integrated model with a
  domain partition and no per-record key across it; a graph declaring no subject areas
  yields nothing (byte-identical). Event: `spanning_bridges`. Gate:
  `test/test_kg_correspondence_lead.py` on a synthetic retail model — the mechanism is
  proven generic, the policing pack is one instance.
* **A shared foreign key is a fan-out, not a hint**: a column that is a FOREIGN key in
  every table sharing it points at a third table, and joining two of those tables on
  it is fact-to-fact through a dimension key (five customers with the identical total,
  prod 0aa3a650 turn 1). It is rendered under *NOT JOIN KEYS (fan-out)* — the rule
  once, one line per column — instead of JOINABLE COLUMNS; a column one table holds as
  its pk stays a legitimate hint.
* **…and the DECLARED form of the same trap is a rule the guard enforces** (prod
  ecfb8c41, 2026-09-19): a key TWO tables both hold as a foreign key to a third — the
  declared-FK hint `ffp_member_id: ffp_member, mileage_redemption, member_benefit
  [declared FK]` invited `mileage_redemption ⋈ member_benefit ON ffp_member_id`, every
  redemption row of a member against every benefit row of the same member, and member 2
  read 2,638,492 miles redeemed where the true figure is 17,708 (149×). The pk excuse
  above excused exactly that: the OWNER holds the key as its pk, so the two HOLDERS were
  never listed. ONE pure computation, `_fanout_keys(subgraph)` (from column-level FK
  edges: the holders per column leaf + the owner), feeds BOTH the render — the owner
  named on the fan-out line, *join each listed table to the OWNER, or aggregate each
  separately per key and join the aggregates* — AND `metadata["fanout_keys"]`, which the
  preflight join check reads beside `join_keys` (`preflight/joins.py:record_fanout` →
  `fanout_violations`): a JOIN equating the column on two of its holders, with no
  DECLARED pair between them, is refused under `PREFLIGHT_JOIN_MODE=enforce`
  (`fanout_block_result`, recovery-shaped — the owner and the aggregate-then-join
  shape named, nothing run). A join to the owner, a derived-table aggregate join, the
  same holders on another column, or a pair the graph declares is never a verdict.
  Gates: `test_preflight_joins.py` (the prod statement, the ladder, the declaration
  override), the continuity gate (the computation on a synthetic layered graph), and
  the conversation bench's `fanout_pairs` expectation on the ecfb8c41 fixture.
* **…and the refusal repairs itself — the refused fan-out is REWRITTEN to
  aggregate-then-join at 0 LLM** (prod 940f02fa, 2026-09-19): the refusal was
  working, and each turn still paid for it — the model's OWN first correction
  (`(SELECT k, SUM … GROUP BY k) R JOIN member_benefit B`) passed the guard (correct
  arithmetic) but returned member 2 FIVE times under `TOP 5` (28 distinct tiers over
  its 149 benefit rows), so it spent one to two further rounds reducing the other side
  (a 46K-token probe on turn 2). The shape it wanted is mechanical, and the doctrine
  the text-date and reserved-alias rewrites already follow applies: *a structured
  refusal that names its own remedy never costs an LLM round*. ONE pure rewrite,
  `preflight/joins.py:rewrite_fanout_sql`, consumed by BOTH chokepoints
  (`FanoutJoinStrategy` in the Optimize correction chain; `fanout_rewrite` in the ReAct
  tool wrapper, on the same seam as the text-date twin): each holder is aggregated at
  its OWN grain in a derived table (its key, its grouped columns, its aggregates, its
  own WHERE conjuncts), the two aggregates are joined on the key, and a `TOP n … ORDER
  BY` whose every term belongs to ONE side is pushed INTO that side so the TOP ranks
  that side's rows rather than the join's repeats — with the join's membership carried
  in (`k IN (SELECT k FROM other [WHERE other's conjuncts])` for an INNER join, since
  the original ranked only keys present on both sides; never into the optional side of
  a LEFT join; a HAVING on the ranked side moves inside with the TOP, one on the other
  side keeps the TOP outside). The model's GROUP BY grain is preserved exactly — a
  column the other side contributes ungrouped still repeats per key, and the result
  NAMES that grain and the way to one row per key (aggregate those columns per key, or
  take the attribute from the owner) rather than guessing a MAX. Everything that needs
  judgement DECLINES to today's refusal: an expression mixing both tables, a bare
  `COUNT(*)` (it counted the multiplied rows), a third table, a subquery/CTE/UNION/
  window, `SELECT *`, an ON beyond the single key equality, `TOP … WITH TIES/PERCENT`,
  a column the parse cannot attribute. The rewrite re-runs every join check on its own
  output, and the ReAct twin re-preflights the rewritten statement in FULL before
  accepting it (a rewrite any check would refuse hands the ORIGINAL refusal back).
  Proven on live Teradata (derived-table `TOP … ORDER BY` is the form Teradata accepts;
  `ORDER BY` alone in a subquery is Error 3706): the rewritten prod statements return
  the top-5 set with exact totals — 17,708 for member 2, where the fan-out read
  531,240 — every row accounted for (107 = Σ distinct tiers incl. a NULL group). Two
  precision rules landed with it: a holder whose OWN single-column primary key IS the
  fan-out column is not a holder (a 1:1 extension table cannot multiply — `_pk_is_column`;
  no such table exists in any local graph today, 82,222 FK edges scanned), and
  `sql_joins` resolves ON qualifiers against the join's OWN scope, so a derived table
  aliased with a holder's name (`… ) AS mileage_redemption JOIN ( … ) AS member_benefit`)
  is a join between aggregates, never the fan-out it replaced (it was: the guard would
  have refused the very shape it asked for). Gates: `test_preflight_fanout_rewrite.py`
  (73 at landing, 86 today — the prod statements, the push truth table, the decline set, both twins through
  the real guarded coroutine, an unrelated schema so the rule is the CLASS and not the
  conversation), `test_preflight_joins.py` 82 → 86, the conversation bench 41 → 49
  (every recorded fan-out statement rewrites from the enrichment's own `fanout_keys`);
  eight mutations fail by name.
* **…and on the ReAct lane a rewrite is a STEP of the turn, not a footnote** (same day):
  every mechanical rewrite the tool wrapper performs — TOP placement, a reserved alias, a
  text-stored date, the fan-out — used to reach the model as a note appended to the tool
  content and NOTHING else: the persisted trace kept the model's ORIGINAL statement plus a
  5,000-char preview, so on a long result the historical Live Status card and the KG
  theatre narrated a statement that never ran (the Optimize lane emits a `System
  Self-Correction` event for the same rewrites). Now ONE seam in `tool_enforcement`
  records each rewrite into a per-turn ledger (`begin_rewrite_capture` /
  `record_rewrite` / `drain_rewrites` — a contextvar holding a MUTABLE list the agent
  arms in the parent task, since a child task's `.set()` is invisible to it), and
  `ConversationAgentExecutor` drains it after every tool call into a persisted
  `conversation_tool_rewrite` event (kind, label, original + executed SQL, changes,
  grain) sealed into the provenance chain as `agent_tool_rewrite`. Rendered on the live
  stream, the notification channel and the plan reload as a System Self-Correction card
  (executed SQL open, the original kept behind a closed block, "0 LLM rounds", filed
  under TOOL EXECUTION by type authority so live and reload agree); the KG theatre
  swaps the statement under test for the executed one (the original kept on the record,
  an `applied: rewrite` consequence, the strip labelled REWRITTEN). Lane, not class:
  Conversation, Focus-with-tools and every Coordinate expert of those classes ride it.
  Gates: `test_react_rewrite_event.mjs` (24 at landing, 26 today — the titles, the category on both sources,
  the card, the reducer executed), the fan-out suite's §8 (the ledger across a task
  boundary, all four kinds through the real guarded coroutine, the agent's emit + seal);
  proven in headless Chrome on a reloaded turn.

Under a node budget the seeder's ORDER now decides which table gets its columns first
(a table's rank = the best rank of the seed that brought it in); before, a set-ordered
tie handed the budget to whichever table had the lower node id.

Gate: `test/test_kg_conversation_continuity.py` (in the battery).

### The Conversation Bench — real sessions as the regression corpus

Every defect above was visible in data the platform already persisted; nobody was
reading it against the outcome. `test/performance/kg_conversation_bench.py` closes
that loop: it replays a RECORDED conversation turn by turn against the local graph
(the mirror of a prod industry graph is byte-identical), running the real
`get_context_enrichment` with a `workflow_history` built from the previous turns —
each carrying the enrichment the bench produced for it plus the agent's recorded tool
calls — and measures every turn against what a correct answer needs (`expect`: tables,
declared pairs keyed, identity columns, named columns, carry-over) and against what the
agent DID (`recall_missed`: the tables the recorded SQL read that the enrichment had not
shown). Fixtures live in `test/fixtures/kg_conversations/` (built from `tda_sessions`
files — prod's need `sudo`); `--compare before after` diffs two runs;
`test/test_kg_conversation_bench.py` gates the follow-up turns' floors deterministically
(semantic seeding off) and is in the battery. Add a fixture for every conversation that
surfaces a defect — a fix that does not move the bench did not fix the conversation.

### Enrichment Recall and the `missed` prior

`kg_seed_utilization` asks *of what we showed, how much was used*. The inverse is the
number that flags a retrieval defect on prod without anyone reading a screenshot:
**`enrichment_recall`** — of the tables the turn's data calls READ, the share the
enrichment had SHOWN (ONE definition, `kgstore.usage.unshown_tables`, leaf-matched
because the graph names `loyalty.ffp_member` and the SQL `airlines_core.ffp_member`;
`tables_read` collects FROM/JOIN tables and catalog-tool objects, never CTE names).
Informational at GROUNDED (owned by `trusted_scope`, staged at RETRIEVE), abstains
without an enrichment or without a table read. Every miss is also RECORDED — the
`missed` kind in `kg_entity_usage`, bumped on the graph's own table entity at the
executor's telemetry seam (`_record_kg_missed`, beside `used`) — and weighs most in the
ranking prior: a table the agent had to find alone is the one the graph most needs to
offer next time. The carry-over carries the previous turn's missed tables too, ahead of
what was shown, and carried entities CONSUME seed slots (`_SEED_LIMIT` 15, floor 7)
rather than extend the cut (measured: turn 2 of the airlines conversation 289 → 143
entities, 46K → 24K chars, every expectation kept).

### Preflight — the column-existence check

The graph holds every column of every table; the render is capped by relevance. A
weak model that reads a capped schema invents the column it needs
(`boarding_event.miles_flown`) and pays a database error, an LLM round and a `SELECT *`
to learn what the graph knew. `agent/preflight/columns.py` checks every raw-SQL call's
column references (qualified through aliases; unqualified only on a single-table
statement; SELECT aliases and CTE names are never columns) against the session's
COMPLETE lists: the enrichment emits `table_columns_full` for the tables in play (small,
on the event for reload) and records the WHOLE graph's lists once per session into the
guard's store (`GraphStore.all_table_columns`, one pass over the cached graph — never on
the event). Armed only for a **column-complete** graph, read from the graph's CONTENT
so a subscribed copy qualifies: an industry model's root carries `industry` + `scope`,
a declared model `standard: iso_11179`, a discovery graph `source: agent_discovery`; an
ANDP graph (`column_metadata` covers a fraction) and an unrecognised graph never arm.
`PREFLIGHT_COLUMN_MODE` (env `TDA_PREFLIGHT_COLUMN_MODE`, default `observe`): under
`enforce` the refusal is recovery-shaped — the table's real columns and the closest
matches, tables in play first — and carries the 3810 phrase so the Optimize
column-not-found strategy engages as on a real error.

#### The performance contract

It shipped opt-in and off because the search was a per-entity Python cosine loop. That
is what changed; the numbers are why (`test/performance/kg_semantic_bench.py`):

| entities | search (loop) | resident (loop) | search (now) | resident (now) |
|---|---|---|---|---|
| 1,000 | 15.1 ms | 12.3 MB | 0.062 ms | 1.5 MB |
| 4,000 | 59.9 ms | 49.4 MB | 0.086 ms | 6.1 MB |
| 20,000 | 275.4 ms | 246.9 MB | 0.426 ms | 30.7 MB |

A Python list of floats costs ~24 bytes per component; one float32 matrix costs 4.

**When the encoding happens.** The index is a derived artefact of the graph, so it is
built where the graph *changes*: `GraphStore.import_bulk` — the single write chokepoint
every path reaches (constructors, marketplace install/fork/sync, agent-pack import, PDM
deploy, KG rebuild) — warms it after a successful write. A graph written from now on is
therefore searchable by meaning from its **first** question.

Single-entity CRUD (`add_entity`, `update_entity`, …) deliberately does **not** warm: an
agent adding entities one at a time would re-embed the whole graph per call. Those writes
move the signature, and the lazy path below rebuilds once, on the next query.

**Existing graphs are not migrated by that hook** — a graph nobody rewrites never passes
through `import_bulk`. They self-heal on the lazy path instead: the first question finds
no index, is answered lexically exactly as before, and builds in the background. The gap
is one query per graph, once, ever. `maintenance/warm_kg_semantic_indexes.py` pre-builds
them for an operator who would rather not spend even that (e.g. right after a deploy);
measured on 8 real graphs, 30k entities: 58 s, 45 MB on disk.

Four properties make it safe to leave on:

- **A cold graph never blocks a turn.** Embedding 3,748 entities measures ~3.6 s (plus
  ~10 s if the process has not loaded the model). A graph with no index returns **no
  hits** — lexical seeding stands, unchanged — and builds on a background thread.
  Concurrent turns join the one build in flight rather than starting their own.
- **A restart re-loads, it does not re-embed.** The matrix is persisted per
  `(kg_id, signature)` under `KG_SEMANTIC_INDEX_DIR` (default `.cache/kg_semantic`) and
  read back in ~20 ms. The signature is the graph's `(row count, max updated_at)`, so a
  rebuild or import supersedes the index rather than needing invalidation, and stale
  versions are pruned.
- **A graph nobody queries costs no memory.** Resident memory is a statement about
  which graphs are actually being *asked* something. A build triggered by a graph
  WRITE has nobody waiting on it, so it is persisted and then **released**: a
  deployment that imports ten agent packs at startup carries none of their matrices,
  and the first real question reads one back from disk in ~20 ms. (The one exception:
  if the matrix could not be persisted — no signature, or an unwritable index dir —
  it is kept, because releasing it would throw the embedding work away and re-pay it
  on the next query.) Measured across every route: write 0 MB · backfill 0 MB · five
  graphs written at startup 0 MB · channel off 0 MB · queried 0.77 MB / 500 entities.
- **Memory scales with graphs QUERIED, not with users.** The cache is keyed by
  `(graph, version)` and is process-global, so a hundred people asking one agent
  questions share **one** matrix (measured: 50 users on one graph → 3.07 MB, one
  matrix). A per-user key would multiply a 30 MB graph by the size of the tenant.
- **Memory is bounded in bytes**, not graph count, with LRU eviction (measured: 12
  distinct graphs against a 10 MB ceiling → 9.22 MB resident, 3 graphs, most-recent
  kept) — so even the queried set has a ceiling.
- **Idle graphs are given back.** The byte ceiling only reclaims under *pressure* — it
  runs when a new index is admitted — so a working set that goes quiet would otherwise
  hold its memory indefinitely. `_sweep_idle()` releases any matrix nothing has queried
  for `_IDLE_EVICT_SECONDS` (30 min), opportunistically on access rather than from a
  timer thread. This is nearly free to undo *because* the matrix is persisted: the next
  question re-loads it in ~10 ms instead of re-embedding (measured). A graph still
  being asked things is never taken away. Honest limit: a process that goes
  **completely** idle keeps what it last held until something touches the index again.
- **It never raises into retrieval.** Any failure — no embedder, a changed embedding
  model against a built index, a store error — yields no hits and leaves lexical
  seeding intact.

The recurring per-query floor is the **query encode** (~5 ms warm), not the search.

#### What the Live Status event reports

The `kg_enrichment` event carries `semantic_seeding` per graph, and the step title and
its card report it. The reading distinguishes the three ways the channel contributes
nothing, because as a bare absence they are indistinguishable and only one is a finding
about the question:

| state | reading |
|---|---|
| `off` | "Lexical only — semantic seeding is off" |
| `cold` / `building` | "Lexical only — the semantic index is still building for this graph" |
| `ready`, no hits | "nothing cleared the similarity threshold" |
| `ready`, hits, none new | "semantics agreed, adding nothing the words missed" |
| `ready`, bridges | "N entities reached only by meaning", each named with its similarity |

Only an entity that was **both** reached by meaning **and** still present in the
rendered subgraph counts as a contribution (`_semantic_contribution`); a hit that
ranking or the node budget dropped is counted separately as `bridged_dropped` rather
than credited or hidden.

#### Identity, utilisation and the `used` signal (Sep 2026)

The enrichment event now says **which graph** enriched the turn and **what it rendered**:
`enrichments[].kg_id`, `kg_name` (the Live Status card's *Graph* row — a consumer's pack copy
sits beside the publisher's and the two were indistinguishable by owner profile alone),
`entity_names` (the rendered entities by qualified name, bounded by the node budget) and
`grounding` (see Model B below). An older event carries none of these and the card invents
nothing.

**`kg_seed_utilization`** — the semantic channel's number. Of the entities the question
reached ONLY by meaning (`semantic_seeding.bridged`), the fraction the turn's data tool
calls then referenced. Informational (non-gating, no admin threshold), owned by
`trusted_scope` (GROUNDED), staged at RETRIEVE; it abstains — never passes — when the
channel was off / warming / errored, when nothing was bridged, or when the turn ran no
data calls. It exists because the channel was dead on production for 18 days and nothing
measured it (the offline bench disables it for determinism).

The "did the answer TOUCH this entity" test is **one rule** — `kgstore.usage.touched_entities`:
the entity's leaf name (`Victim` from `NIBRS_Source.Victim`, `V40191` from
`…Incident.V40191`) appears as a whole identifier, case-insensitively, in the ARGUMENTS of a
tool call (SQL, `table_name`, a column list) — never in a result (a row that happens to
contain the word is not the agent using the entity); generic leaves (`id`, `count`, `date`…)
are excluded rather than counted everywhere. The same rule feeds the **`used`** telemetry
(`kg_entity_usage.used_count`, the strongest signal the ranking prior weights) — declared
since Phase E and never recorded by anyone until Sep 2026; `executor._record_kg_used`
records it beside the OBDA-READ scan at both data-running engines, fire-and-forget, not gated on
OBDA-READ. Honest limit: a metric or business concept whose name never appears in SQL cannot
read as used — the number under-counts rather than invents.

KG seam failures (bridge-table discovery, semantic no-embedder, semantic hits,
trusted-scope resolution) log **WARNING once per (key, process)** via `core/log_once.warn_once`
and DEBUG thereafter; the index persist/load failures stay DEBUG (a cache that cannot be
written still works).

### Released-only grounding — Model B (Tier 4.3 §1a)

By default an agent grounds on the **live draft** of its bound ontology (Model A): every
edit, resync and re-validation applies at once. Tier 4.3's lifecycle *publish* is a
release act that pins a conformance-stamped snapshot — and, since Sep 2026, an agent can
**opt in** to grounding on that release only:

```json
"componentConfig": { "knowledge_graph": { "enabled": true, "groundOn": "released" } }
```

**Mechanism** (`core/kg_release.py`, one decision stated once — `resolve_grounding`):
the published version is **materialised** into a shadow graph `<kg_id>::released` in the
same `kg_entities` / `kg_relationships` tables, so the whole retrieval path — search,
adaptive subgraph, capabilities, the semantic index, usage telemetry, the grounding gate's
scope — runs unchanged over a different `kg_id`. The shadow's `kg_metadata` row carries
`released_of` + `released_version_id`, is never active, and is hidden from every enumerator
(graph lists, pack export, the published catalog). It is rebuilt only when
`published_version_id` moves — eagerly at publish, lazily on first read.

**Rules:**

| Situation | Behaviour |
|---|---|
| `groundOn` absent or any value but `released` | Model A, byte-identical |
| `released`, ontology has a release | every KG read (enrichment, trusted scope, `TDA_KnowledgeGraph` reads) sees the released view; the event carries `grounding: {mode, kg_id, version_id, label, lifecycle}` and the Live Status card reads *Grounded on: released 1.2.0* |
| `released`, no release yet (or it could not be materialised) | the **live draft**, and the event says so (`fallback: "live"`; the card warns) — an agent never silently reads nothing |
| `released`, the release is deprecated | still the release, flagged as deprecated |
| a KG **write** action through a released-mode agent | refused, recovery-shaped (*the release is read-only; report the change to the owner, or set Ground on to the live draft*) — an agent that edited a draft it cannot see would never observe its own edit |
| trust | the agent's ontology ingredient hashes the **released** content: editing the draft does not flip a released-mode agent's badge (its behaviour did not change); a new publish does. `groundOn == released` is itself a signed capability token |
| KG deletion / pack uninstall | the released view dies with the graph |

The three read seams (`get_context_enrichment`, `get_trusted_scope`, `_get_store`) route
through ONE resolver, `KnowledgeGraphHandler._resolve_grounding_store`, so they cannot
disagree about which graph an agent reads. Profile editor: the knowledge-graph component
row's **Ground on** selector; the save carries a stored value forward when the select is
not rendered. Gate: `test/test_kg_model_b.py` (real SQLite, real store, real handler).

### Prompt Injection Format

The enrichment text follows a structured format (rendered by the handler; blocks appear only when they have content — a bare graph renders the short form):

```
--- KNOWLEDGE GRAPH CONTEXT ---
The following known entities and relationships may inform your planning:
[NOT IN THIS MODEL: …]                     (model gap — leads when it fires)
[a declared correspondence lead]           (a question spanning both areas of a bridge)
[consumption contracts of signed data products]

TABLE SCHEMAS (use these to validate SQL column references):
  sales.orders  [key: order_id]: order_id(INTEGER), customer_id(INTEGER), order_date(DATE), amount(DECIMAL)  (+N more columns not shown — ask to expand)

REPEATING GROUPS (…)
DECLARED FOREIGN KEYS (declared join columns — prefer these over name matches, …):
  customer_id: customers, orders
JOINABLE COLUMNS (shared column NAMES — a hint, NOT a verified key; …)
NOT JOIN KEYS (fan-out) — …

METRIC ENTITIES (authoritative definitions — …):
  - Revenue (Total order amounts; compilable; measures: 'Total Revenue'; dimensions: 'Order Date')
PUBLISHED QUERIES (the product's OWN recipes for business questions — …)
DOMAIN ENTITIES / BUSINESS_CONCEPT ENTITIES / … (one block per remaining type; attributes
  already shown under TABLE SCHEMAS and data products shown as contracts are skipped)

KNOWN RELATIONSHIPS:
  - Revenue --[measures]--> orders.amount
--- END KNOWLEDGE GRAPH CONTEXT ---
```

Under an exclusive data surface the intro line and the SQL-helping blocks are withheld (see *Signed data products* under [Planner Integration](#planner-integration)).

**A compilable metric's line names its declared measures and dimensions.** `compile_metric`
selects by those NAMES, and a metric's measure names are display-style (`Total Fi Product
Revenue`), not the column they aggregate. Rendering formula/deployed/base table without the
names left the model nothing to copy: in prod (session `d9a60903`) it asked for the COLUMN
`fi_product_revenue`, the name filter emptied, and the refusal said only "a metric query
requires at least one measure" — two wasted tool rounds before it fell back to raw SQL. The
names are resolved once by `resolver.resolve_capabilities` (`metric_measures` /
`metric_dimensions`, declared order, de-duplicated) and rendered bounded at
`_METRIC_NAMES_SHOWN`, with the remainder **counted** (`+N more`) rather than dropped
silently. A non-compilable metric renders no names line — it would be bait for a tool that
cannot serve it.

---

## Frontend Architecture

### D3 Visualization — Three Display Modes

**Renderer:** `components/builtin/knowledge_graph/renderer.js` (D3 v7 via CDN)

The visualization follows the Canvas component's progressive display pattern:

| Mode | Entry Point | Size | Purpose |
|------|-------------|------|---------|
| **A. Inline Compact** | `renderKGInlineCompact()` | 200px height in chat | Mini preview with simplified force layout |
| **B. Split Panel** | `openKGSplitPanel()` | 50% viewport, side-by-side with chat | Full interactive graph with toolbar |
| **C. Fullscreen** | `toggleKGFullscreen()` | 100% viewport | Maximum graph exploration space |

**Transition flow:**
```
Chat message → [Inline Compact]
                    │
                    ├── "Open in Graph →" button
                    ▼
              [Split Panel]
                    │
                    ├── Fullscreen button (⊞)
                    ▼
              [Fullscreen]
                    │
                    ├── Exit fullscreen
                    ▼
              [Split Panel]
                    │
                    ├── Close button (×)
                    ▼
              [Panel hidden]
```

**Coexisting panels:** the KG panel is one of the shared split panels (`.sp-panel`); it no longer closes the Canvas panel (the old `kg-requesting-split` / `canvas-requesting-split` mutual-exclusion events are gone). A session that had the panel open restores it on reload (`sessionManagement.js` → `_restoreKGState` → `openKGSplitPanel`).

**Lenses, theatre, focus and drafts** — the detail view, the overview lens, the execution animation and theatre, opening on a concept (`open` with `entity_name` → `focus_names` → `_focusOpenedNames` selects and zooms to it) and the author's drafts view (`graph-spec?include_drafts=1`, drafts drawn dashed, still visible only to their author) are documented in [KG_INSPECTOR_ARCHITECTURE.md](KG_INSPECTOR_ARCHITECTURE.md).

**Live redraw:** the renderer follows `UderiaBus.onComponent('knowledge_graph', …)` — any write announced for the open graph's `kg_id` (an answer's add/propose, a Govern-dialog landing or publish, a Workspace edit, another tab) redraws the panel in place, keeping the view (`preserveKGView`) and whether it shows drafts. The management panel follows the same channel.

**Counting vocabulary:** every count shown to a person goes through `static/js/kgVocabulary.js` — a **node** is any element of a graph, an **entity** is a business concept only (type `entity`); store names such as `total_entities` count nodes and are never printed as "entities" (gate `test/test_kg_vocabulary.mjs`).

### Interactive Features

| Feature | Description | Implementation |
|---------|-------------|----------------|
| **Force-directed layout** | Spring-charge physics for organic node positioning | `d3.forceSimulation` with link, charge, center, collision forces |
| **Zoom & pan** | Mouse wheel zoom (0.1x–4x), drag to pan | `d3.zoom()` on SVG container |
| **Node drag** | Reposition individual nodes | `d3.drag()` with simulation restart |
| **Hover glow** | Entity-type-colored glow filter on hover | SVG `feGaussianBlur` + `feFlood` filter per type |
| **Click focus** | Click node to highlight connected subgraph, dim unrelated | Connected-set computation, opacity animation |
| **Edge gradients** | Edges colored as gradient from source to target type | SVG `linearGradient` per link |
| **Directional arrows** | Arrowhead markers on edges | SVG `marker` with `refX` offset |
| **Search** | Toolbar input filters nodes by name (live) | Opacity toggle on input event |
| **Entity type filters** | Colored pills toggle entity type visibility | `display:none` on filtered nodes + connected edges |
| **Zoom to fit** | Button resets viewport | `d3.zoom.transform` transition |
| **Export PNG** | Button exports 2x resolution PNG | SVG → Image → Canvas → Blob download |
| **Tooltips** | Hover shows entity properties, type, business meaning | HTML overlay positioned at cursor |
| **Legend** | Color-coded entity type legend overlay | Bottom-right glass panel |
| **Animated entry** | Nodes fade in sequentially, edges draw progressively | CSS transitions + `stroke-dashoffset` animation |
| **Center entity pulse** | Pulsing ring animation on the queried entity | CSS `@keyframes kg-pulse` |
| **Node importance sizing** | Higher-centrality nodes rendered larger | Degree centrality → circle radius |

### Theme Compliance

The renderer uses CSS custom properties for all chrome colors, ensuring compatibility across themes:

```css
/* SVG elements use CSS fill/stroke to evaluate variables live */
.kg-node-name  { fill: var(--text-primary, #e5e7eb); }
.kg-edge-label { fill: var(--text-muted, #6b7280); opacity: 0.5; }
.kg-mini-label { fill: var(--text-muted, #9ca3af); }
.kg-mini-link  { stroke: var(--border-primary, #4b5563); }

/* Light theme adjustments */
[data-theme="light"] .kg-edge-label { opacity: 0.7; }
[data-theme="light"] .kg-mini-link  { stroke-opacity: 0.6; }
```

Entity type colors are provided by the backend handler (`handler.ENTITY_TYPE_COLORS`, one entry per type) and passed through the D3 spec. They are constant across themes and grouped by layer:

| Group | Colors |
|-------|--------|
| Logical | `database` blue `#3b82f6` · `entity` green `#22c55e` · `attribute` lime `#a3e635` · `foreign_key` amber `#f59e0b` · `business_concept` violet `#8b5cf6` · `taxonomy` pink `#ec4899` · `metric` cyan `#06b6d4` · `domain` orange `#f97316` |
| Product description | `query_recipe` rose `#e11d48` · `design_decision` fuchsia `#c026d3` · `quality_metric` teal-cyan `#14b8a6` · `naming_standard` purple `#a855f7` · `product_bridge` rose-red `#f43f5e` |
| Stacked contracts | `data_product` gold `#eab308` · `action_type` red `#dc2626` |
| Physical (slate/teal family) | `catalog` `#64748b` · `schema` `#94a3b8` · `table` `#0d9488` · `column` `#5eead4` · `ddl_artifact` `#78716c` |

### Management UI

Knowledge graphs are managed in two UI locations:

**1. Resource Panel (Sidebar)**

Location: `knowledgeGraphPanelHandler.js` → `loadKnowledgeGraphsPanel()`

- Compact cards using CSS variables (`--card-bg`, `--border-primary`)
- **Active-only filter toggle** — shows all KGs or only the active one per profile
- Active indicator badge per card
- Export menu (`openKgExportMenu`) — v2.0 `.json` (including `kg_id`, `kg_name`, metadata) or an RDF serialisation (`?format=` `rdf` · `skos` · `owl` · `all`, see [OPEN_STANDARDS_ARCHITECTURE.md](OPEN_STANDARDS_ARCHITECTURE.md))
- Delete button (styled confirmation modal)
- IFOC badge showing profile type with color coding

**2. Intelligence Panel (Third Tab; formerly "Intelligence Performance")**

Location: `knowledgeGraphPanelHandler.js` → `loadKnowledgeGraphsIntelligenceTab()`

- Full `glass-panel` cards with `card-btn` action system
- **Backend badge** — coloured pill showing the storage backend: **Neo4j** (blue `#3b82f6`) or **SQLite** (green `#4ade80`). Sourced from `kg.backend_type` returned by the list endpoint.
- Entity/relationship count display — for non-SQLite KGs the counts come from a live `backend.get_stats()` call at list time (not from SQLite row counts, which would be zero)
- Entity type pills showing type breakdown
- **Import dialog (two-mode)**:
  - File picker triggers dialog with **Ownership Profile** selector and import mode:
    - **Create new Knowledge Graph** (default) — always mints a fresh KG, activates it only if the profile has no active KG yet
    - **Merge into existing** — appears when the selected ownership profile already has KGs; dropdown shows KG names (from `kg_name` field)
  - "Target Profile" label renamed to **"Ownership Profile"** (reflects that the profile owns the KG)
- Export and Delete buttons (both scoped to `kg_id`)
- Activate button to switch the active KG per profile, and profile assignments (`/v1/knowledge-graph/assignments`)
- Tab counter showing number of knowledge graphs
- iPadOS file picker workaround (removes `accept` attribute)

**Cross-panel refresh:** both panels follow `UderiaBus.onComponent('knowledge_graph', …)` (debounced 250 ms), so any announced graph write — from either panel, an answer, a REST call or another tab — refreshes both.

---

## REST API

All endpoints are JWT-authenticated and scoped to the current user. Most take `profile_id` (query or body) and, where a specific graph is meant, `kg_id` (preferred — robust when a profile owns several graphs). `rest_routes.py` registers 53 `/v1/knowledge-graph/*` routes; `kg_marketplace_routes.py` adds publish and subscriptions. The tables below cover the core surface; the rest are listed under [Further Endpoints](#further-endpoints).

### Entity Endpoints

| Endpoint | Method | Parameters | Response |
|----------|--------|------------|----------|
| `/v1/knowledge-graph/entities` | `POST` | Body: `{profile_id, name, entity_type, properties?, source?}` | `{status, entity: {id, name, ...}}` |
| `/v1/knowledge-graph/entities` | `GET` | Query: `profile_id`, `entity_type?`, `search?` | `{status, entities: [...]}` |
| `/v1/knowledge-graph/entities/<id>` | `PUT` | Body: `{profile_id, properties}` | `{status, updated: bool}` |
| `/v1/knowledge-graph/entities/<id>` | `DELETE` | Query: `profile_id` | `{status, deleted: bool}` |

### Relationship Endpoints

| Endpoint | Method | Parameters | Response |
|----------|--------|------------|----------|
| `/v1/knowledge-graph/relationships` | `POST` | Body: `{profile_id, source_entity_id, target_entity_id, relationship_type, cardinality?, metadata?}` | `{status, relationship: {id, ...}}` |
| `/v1/knowledge-graph/relationships` | `GET` | Query: `profile_id`, `entity_id?`, `relationship_type?` | `{status, relationships: [...]}` |
| `/v1/knowledge-graph/relationships/<id>` | `DELETE` | Query: `profile_id` | `{status, deleted: bool}` |

### Graph Operations

| Endpoint | Method | Parameters | Response |
|----------|--------|------------|----------|
| `/v1/knowledge-graph/subgraph` | `GET` | Query: `profile_id`, `entity_name`, `max_nodes?` | `{status, nodes, edges, node_count}` |
| `/v1/knowledge-graph/stats` | `GET` | Query: `profile_id` | `{status, stats: {total_entities, total_relationships, entity_types, ...}}` |
| `/v1/knowledge-graph/import` | `POST` | Body: `{profile_id, entities: [...], relationships: [...], kg_name?, kg_description?, kg_database_name?, target_kg_id?}` | `{status, entities_added, relationships_added}` |
| `/v1/knowledge-graph/clear` | `DELETE` | Query: `kg_id` (preferred) or `profile_id` (legacy) | Deletes the graph's data AND its `kg_metadata` / `kg_profile_assignments` rows — this is the KG delete |
| `/v1/knowledge-graph/context` | `GET` | Query: `profile_id`, `query` | `{status, query, enrichment, has_context}` (`enrichment` = the `(text, metadata)` pair) |
| `/v1/knowledge-graph/generate` | `POST` | Body: `{profile_id, database_name, include_semantic?, graph_name?, description?, kg_store_config_id?, discovery_mode? ("tool" · "prompt" · "industry_model" · "declared_model" · "andp_semantic"), mcp_context_prompt?, target_database?, discovery_tools?, validate_with_source?}` | Constructor result (counts per phase) |
| `/v1/knowledge-graph/graph-spec` | `GET` | Query: `profile_id` or `kg_id`, `max_nodes?`, `types?`, `focus?`, `focus_names?`, `project?`, `divisions?`, `domains?`, `include_drafts?` | D3 spec for the inspector; unpublished authored drafts are cut unless `include_drafts=1` |
| `/v1/knowledge-graph/schema-overview` | `GET` | Query: `profile_id`/`kg_id` | Type-level meta-graph (overview lens) |
| `/v1/knowledge-graph/node-neighborhood` | `GET` | Query: `entity_id`, `profile_id`/`kg_id` | One node's neighbourhood |
| `/v1/knowledge-graph/discover` | `POST` | — | `501 {error: "not_implemented"}` — MCP schema discovery was never built; use the KG constructors |

**Import modes (two-mode behaviour):**

When `target_kg_id` is provided, the import **merges** into the specified existing KG using `import_bulk()` upsert semantics (no new KG created). When omitted the import **creates a new KG** under a fresh `kg_id`:
- Calls `set_kg_metadata(name, database_name, description, is_active=not has_active)` first — so the imported KG becomes the active KG only when the target profile has none yet.
- Preserves any existing active KG on the profile (no accidental displacement).

### Management Endpoints

| Endpoint | Method | Parameters | Response |
|----------|--------|------------|----------|
| `/v1/knowledge-graph/list` | `GET` | None (user-scoped) | `{status, knowledge_graphs: [{kg_id, profile_id, kg_name, total_entities, entity_types, is_active, kg_store_config_id, backend_type, backend_name, ...}]}` — for non-SQLite KGs `total_entities`/`total_relationships`/`entity_types` are overwritten with live counts from `backend.get_stats()` |
| `/v1/knowledge-graph/export` | `GET` | Query: `profile_id`, `kg_id?`, `format?` (`json` default · `rdf` · `skos` · `owl` · `all`) | File download (Content-Disposition: attachment) |
| `/v1/knowledge-graph/import-rdf` | `POST` | Query: `profile_id`, `serialization`, `target_kg_id?`; body: RDF text | Import an RDF graph |
| `/v1/knowledge-graph/rename` | `PATCH` | Body: `{kg_id, name, description?}` | `{status}` |
| `/v1/knowledge-graph/assignments` | `GET` / `POST` / `DELETE` | `kg_owner_profile_id`, `kg_id`, `assigned_profile_ids` / `assigned_profile_id` | Read / set / remove the profiles a graph is assigned to |
| `/v1/knowledge-graph/assignments/activate` | `PATCH` | Body: `{kg_owner_profile_id, kg_id, assigned_profile_id}` | Make the graph the active one for a profile (replaces the removed `POST /activate`) |

**Export file format (v2.0):**
```json
{
  "export_version": "2.0",
  "kg_id": "550e8400-e29b-41d4-a716-446655440000",
  "kg_name": "CRM Schema",
  "kg_description": "Customer relationship management tables",
  "kg_database_name": "crm_prod",
  "profile_id": "profile-123",
  "profile_tag": "@OPTIM",
  "exported_at": "2026-04-16T10:30:00+00:00",
  "stats": {
    "total_entities": 42,
    "total_relationships": 38
  },
  "entities": [...],
  "relationships": [...]
}
```

> **v1.0 exports** (pre-April 2026) remain import-compatible — they create a new unnamed KG and activate it if the profile has none.

### Further Endpoints

| Area | Endpoints |
|------|-----------|
| Versions | `GET/POST /v1/knowledge-graph/versions`, `POST /versions/<version_id>/restore`, `DELETE /versions/<version_id>` |
| Lifecycle & conformance | `GET /<kg_id>/lifecycle`, `POST /<kg_id>/lifecycle/<action>` (`propose` · `approve` · `reject` · `publish` · `deprecate`; publish is conformance-gated), `GET /<kg_id>/conformance` |
| Update (resync) | `POST /check-update`, `POST /apply-update` (body `{kg_id, options_override?}`; apply runs `replace_model_layer`) |
| Physical deploy | `POST /redeploy-physical`, `POST /redeploy-metric-views`, `POST /reload-demo-data` |
| Authored layer (OBDA-MODEL) | `POST /propose`, `POST /propose/suggest`, `GET /authored`, `POST /authored/deploy`, `POST /authored/publish`, `DELETE /authored/<extension_id>`, `POST /declare-concept` |
| Workload tuning | `POST /tuning/review`, `GET /tuning/proposals`, `POST /tuning/proposals/<rid>/<action>`, `DELETE /tuning/proposals/<rid>` |
| Metrics | `GET /metrics/<metric_name>/export` |
| Constructor catalogs | `GET /declared-registries`, `GET /andp-products`, `GET /industry-models`, `GET /industry-models/<industry>/structure` |
| Marketplace (`kg_marketplace_routes.py`) | `POST /v1/knowledge-graph/<profile_id>/publish`, `GET /v1/knowledge-graph/subscriptions` |

All paths above are under `/v1/knowledge-graph`. The authored-layer, tuning and deploy routes are documented in [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md); the full REST reference is [restAPI.md](../RestAPI/restAPI.md).

---

## Integration Points

### Planner Integration

The knowledge graph reaches every engine through ONE entry, `components.manager.get_component_context_enrichment()` → `ComponentManager.get_context_enrichment()` (iterates components whose manifest `backend.supports_context_enrichment` is true) → `KnowledgeGraphHandler.get_context_enrichment()`. Two seams call it:

```
Context Window assembly (pass 3)
    knowledge_graph_context module            components/builtin/context_window/modules/knowledge_graph/
    │  budget → node cap (30 tokens/node, 20..500), column_cap, session_id
    │  caches text on the executor (_cw_kg_text) + stamps kg_enrichment_event
    ▼
executor._get_kg_enrichment()                 agent/executor.py
    │  trusts the CW cache when the module ran (_cw_kg_assembled);
    │  else falls back to a direct get_component_context_enrichment() call;
    │  always applies the whole-KG trusted scope (_apply_trusted_scope)
    ▼
consumers:
    Optimize   planner (strategic planning)  → knowledge_context_str += text, plus the
               "Schema-Aware SQL Construction" directive; emits the kg_enrichment event
    Ideate / Ideate+tools / Focus engines     → engines/{ideate,conversation,focus}_engine.py
    Coordinate (genie_coordinator.py)          → calls get_component_context_enrichment directly
               (the CW module excludes genie; experts get their own enrichment)
```

For Optimize, the enrichment text is appended to the planner's `knowledge_context_str`, which already includes knowledge repository retrieval results. This means the LLM receives both RAG case examples and knowledge graph topology in a single context section. The enrichment event's `scope_databases` also scope the recovery of a "database not found" tool error (`phase_executor.DatabaseNotFoundStrategy._kg_scope_databases`) so recovery and the grounding gate cannot diverge. Context-window budgeting is described in [CONTEXT_WINDOW_ARCHITECTURE.md](CONTEXT_WINDOW_ARCHITECTURE.md).

**Signed data products in the enrichment (Aug 2026, hardened by a live walkthrough):**

- Products are **ALWAYS seeded** into the enrichment subgraph, regardless of the question's
  wording (`_search_entities_for_query` fetches them by type, bounded at 10, deduped,
  fail-soft). A product is named for its *subject area* while a question names *entities*,
  so the lexical match loses essentially every time — and an absent product is a missing
  OPTION, not a missing fact: the planner cannot choose a governed lane it was never shown.
- The consumption contract **names its admissible members** — measures, plus dimensions in
  the QUALIFIED `Entity.Attribute` form (the form a request must use; the manifest stores
  them as LISTS of objects, and an earlier `isinstance(dict)` check silently rendered no
  vocabulary at all, making the model guess a column name on every turn).
- A rendered product appears **exactly once** — never again as a bare entity (the poorer
  second entry is what once made the planner treat a product as an ordinary table).
- Under an **EXCLUSIVE data surface** (`authorityConfig.dataSurface`, read from the SAME
  ambient-principal contextvar the tool chokepoints enforce with): only on-surface products
  render as contracts; off-surface products are NAMED on one refusal line (never silently
  dropped); and the rest of the enrichment — TABLE SCHEMAS, joinable columns, entity
  listings, the `compile_metric` preamble — is **withheld with an honest one-line reason**,
  because it exists solely to help an agent write SQL it cannot execute (bait, not
  transparency). Advisory/non-exclusive surfaces keep the full enrichment. The grounding
  gate's trusted scope is computed from the FULL subgraph and is untouched by the text cut.
- The enrichment EVENT carries `data_products` (the contracts actually shown — ONE shared
  rule `_rendered_products` with the prompt renderer, so event and prompt cannot disagree),
  `data_products_off_surface`, and `surface_only`; the Live-Status title (one builder,
  `ui.js kgEnrichmentTitle`, live + reload) distinguishes ANALYSED from INJECTED.

### Component System Integration

The knowledge graph plugs into the standard component lifecycle:

| Integration Point | Mechanism | File |
|-------------------|-----------|------|
| **Tool definition** | `manifest.json` → `tool_definition` | Injected into LLM context via `get_tool_definitions()`; on a governed tool surface it is offered per turn like any tool (see [TOOL_SURFACE_ARCHITECTURE.md](TOOL_SURFACE_ARCHITECTURE.md)) |
| **Tool offer** | No `offers_tool` / `is_profile_relevant` override — the tool is offered wherever the component is enabled | `components/base.py` defaults |
| **Instructions** | `instructions.json` → intensity-keyed text | Injected via `get_instructions_text()` |
| **Companion skill** | `knowledge-graph-expert` (`skill_registry.json`, `component_id: knowledge_graph`) — auto-assigned while the component is enabled | `skills/builtin/knowledge-graph-expert/` |
| **Fast-path execution** | `is_deterministic = True`, manifest `backend.fast_path: true` | Bypasses tactical LLM in `phase_executor.py` |
| **LangChain tool** | Auto-generated `StructuredTool` | For `llm_only`, `rag_focused`, `genie` profiles |
| **Context enrichment** | manifest `backend.supports_context_enrichment: true` | See [Planner Integration](#planner-integration) |
| **Renderer discovery** | `frontend.renderer_file` + CDN deps (D3 v7) | D3 loaded on first use |
| **Instances** | `instances: "agent"` | The Workspace scopes the KG section to the agent shown |
| **Answer surface** | `answer_surface: {open: false, pin: true}` | The KG output in an answer is one evidence row (`agent/answer_surface.component_row`), collapsed by default (an action payload may override, e.g. a graph the user asked to see opens) and pinnable to a dashboard |

#### Component Signals

The manifest's `signals` block declares the component's place on the page bus ([COMPONENT_ARCHITECTURE.md § Component Signals](COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other)):

| Field | Value |
|-------|-------|
| `emits` | `uderia:kg-node-select`, `uderia:kg-node-deselect`, `uderia:kg-node-dblclick`, `uderia:kg-rendered` |
| `listens` | `uderia:component-changed`, `uderia:effective-profile` (the panel follows the agent the page shows — `followEffectiveProfile`) |
| `announces` | `created`, `updated`, `imported`, `cleared`, `renamed`, `snapshot`, `restored`, `published`, `withdrawn`, `deleted` |
| `consumes_refs` | `false` |

Server-side, the announcements come from the `@_signals.announces(...)` decorators on the `GraphStore` write seams (and `core/kg_version_db.py`, `core/kg_release.py`, plus `announces_route` on routes without a store seam); client-side, a Govern-dialog act announces via `UderiaBus.announce('knowledge_graph', 'updated', [kgId], {aspect: 'authored'})`. The renderer and both management panels follow `UderiaBus.onComponent('knowledge_graph')`.

### Profile Configuration

The knowledge graph is enabled per-profile via `componentConfig`:

```json
{
  "componentConfig": {
    "knowledge_graph": {
      "enabled": true,
      "intensity": "medium",
      "groundOn": "live"
    }
  }
}
```

`groundOn: "released"` opts into Model B (above); the graph's storage backend is chosen by the profile's `kgStoreConfigId` (see [KG_STORE_ABSTRACTION_ARCHITECTURE.md](KG_STORE_ABSTRACTION_ARCHITECTURE.md)).

**Instruction levels** (`instructions.json`; the column cap above reads the same `intensity` key with its own `low`/`medium`/`high`/`full` values):

| Level | LLM Behavior |
|-------|-------------|
| `none` | KG disabled — no tool, no instructions, no enrichment |
| `medium` | Use graph context as guidance when available. Add entities/relationships when user explicitly requests. |
| `heavy` | Strictly validate against graph. Proactively suggest adding discovered entities. Always offer to visualize relationships. |

**Default:** Enabled for all profile types (`tool_enabled`, `llm_only`, `rag_focused`, `genie`) at `medium` intensity (`profile_defaults` in the manifest). A profile without `componentConfig` inherits this default — and with it the companion skill (see CLAUDE.md, *Component + Skill Separation*).

### Session UI Integration

**Split panel DOM** is defined in `templates/index.html`:

```html
<aside id="kg-split-panel" class="sp-panel" style="display:none;">
    <div class="sp-header">
        <div class="sp-header-left">
            <span class="sp-accent-icon">…</span>
            <button id="kg-switcher-btn" class="sp-switcher" title="Switch knowledge graph">
                <span id="kg-split-title" class="sp-switcher-name">Knowledge Graph</span> …
            </button>
        </div>
        <div class="sp-header-actions">
            <button id="kg-split-fullscreen" class="sp-action-btn">…</button>
            <button id="kg-split-close" class="sp-action-btn">…</button>
        </div>
    </div>
    <div id="kg-split-content" class="kg-split-body"></div>
</aside>
```

The panel uses the shared split-panel chrome (`sp-*`); the header's switcher (`#kg-switcher-btn`) changes which graph is shown. The split panel persists across messages within a session — when a new KG visualization is rendered inline, clicking "Open in Graph" updates the existing panel rather than creating a new one. The panel state (open/closed, current spec) is stored in module-level variables in `renderer.js` and restored with the session (`_restoreKGState`).

---

## MCP Schema Discovery (never built)

The V2 stub that was meant to populate a graph from MCP tool schemas was removed (2026-10-30). `POST /v1/knowledge-graph/discover` remains registered and answers `501 {error: "not_implemented"}` — it had answered `success` with zero counts, which read as "discovered nothing". Automatic population is done by the KG constructors (`POST /v1/knowledge-graph/generate` — agent discovery walks a live database through the MCP tools; the declarative walkers read industry models, declared registries and ANDP products), see `agent/kg_constructors/` and `docs/Architecture/INDUSTRY_MODEL_KG_CONSTRUCTOR_PLAN.md`.

---

## Security & Isolation

| Concern | Mechanism |
|---------|-----------|
| **Multi-user isolation** | Every graph is a `kg_id` owned by a `(profile_id, user_uuid)` pair; routes resolve a `kg_id` only within the caller's `user_uuid` |
| **Cross-user visibility** | Impossible — no endpoint accepts `user_uuid` as input; derived from JWT |
| **Cross-profile reads** | `list_all_graphs()` only returns metadata (counts), not entity data |
| **SQL injection** | Parameterized queries throughout `GraphStore` |
| **Bulk import validation** | Single-entity writes raise `ValueError` on a type outside `ENTITY_TYPES` / `RELATIONSHIP_TYPES`; `import_bulk` rejects and counts invalid rows (`entities_rejected`, `relationships_rejected`) instead of writing them |
| **Subscribed graphs** | Read-only base layer (`KnowledgeGraphReadOnlyError`); only the subscription sync path may rewrite it |
| **Agent writes** | Refused for a released-mode agent and, under an `enforce` OBDA model policy, for direct edits (proposals only) |
| **JWT authentication** | All REST endpoints decorated with `@require_auth` |

---

## Performance Characteristics

| Operation | Complexity | Notes |
|-----------|------------|-------|
| Entity CRUD | O(1) per operation | SQLite indexed by profile + user |
| Substring search | O(n) | Scans entities within the graph; semantic seeding runs beside it (see [the performance contract](#the-performance-contract)) |
| NetworkX load | O(E + V) | Full graph loaded from SQLite on first algorithm call |
| Adaptive subgraph extraction | O(V + E) | Three-phase: structural (unbounded FK chains), column expansion (budget-aware), semantic enrichment (capped) |
| Legacy subgraph BFS | O(V + E) | Bounded by `depth` and `max_nodes` parameters (deprecated) |
| Centrality | O(V + E) | Computed over full graph |
| Cycle detection | O(V + E) | Capped at 20 cycles |
| Context enrichment | ~5-20ms | Search + adaptive extraction + format for typical graphs (<500 entities) |
| FK inference (ingestion) | O(T² × C) | T=tables, C=avg columns per table; runs once during KG generation |
| D3 rendering | Browser-side | Force simulation self-terminates; mini graph stops after 3s |

**NetworkX cache invalidation:** Any write operation (add, update, delete, import, clear, replace) invalidates the backend's cached graph for that `kg_id`. The next graph algorithm call re-loads the full graph from SQLite. For write-heavy workloads, consider batching mutations before querying algorithms.

---

## Roadmap

| Feature | Status | Description |
|---------|--------|-------------|
| Entity/Relationship CRUD | **Shipped** | Full CRUD via REST API and LLM tool |
| D3 force-directed visualization | **Shipped** | 3-mode progressive display with interactive features |
| Planner context enrichment | **Shipped** | Automatic subgraph injection into strategic planning |
| Resource Panel management | **Shipped** | Browse, export, delete across profiles |
| Intelligence panel tab | **Shipped** | Import, export, delete with IFOC badges |
| Bulk import/export | **Shipped** | JSON file format with profile metadata |
| Adaptive subgraph extraction | **Shipped** | Entity-type-aware, unbounded FK chains, budget-aware columns (replaces fixed-depth BFS) |
| FK edge inference (Phase 3.5) | **Shipped** | Deterministic FK detection via column properties, naming conventions, and shared column names |
| Multi-KG per profile + assignments | **Shipped** | `kg_id`-scoped graphs, one active per profile, assignable to further profiles (`kg_profile_assignments`) |
| KG constructors | **Shipped** | Agent discovery, industry model, declared model, ANDP; source validation; check-update / apply-update |
| Versions, lifecycle, released grounding | **Shipped** | Snapshots/restore, propose→publish lifecycle with conformance gate, Model B (`groundOn: released`) |
| Authored layer (OBDA-MODEL) | **Shipped** | Proposals, drafts, landing and publishing — [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md) |
| RDF / OWL / SKOS interchange | **Shipped** | `export?format=`, `import-rdf` |
| Promote (cross-profile copy) | **Planned** | Copy KG from one profile to another (assignment covers sharing within a user) |
| MCP schema auto-discovery | **V2 Stub** | Superseded in practice by the agent-discovery constructor |
| Embedding-based semantic search | **Shipped** | Semantic seeding (`kgstore/semantic_index.py`), beside lexical search — on by default |
| Auto-enrichment during execution | **Planned** | Automatically capture discovered schema from tool results |
| Marketplace/AgentPack integration | **Shipped** | KGs exported in Agent Pack manifest v1.4 (`knowledge_graphs` array + per-profile `kg_refs`); import restores KGs after profiles are created; `update_in_place` refreshes pack-owned graphs in place via `replace_model_layer` (`kgs_refreshed`); marketplace publish / fork / subscribe |
| KG Store Abstraction Layer | **Shipped** | `kgstore/` ABC + factory + SQLite + Neo4j backends; `GraphStore` is now a thin delegate; `kg_store_config_id` persisted on `kg_metadata` for per-KG backend routing |
| Graph merge/conflict resolution | **Planned** | Merge two profile graphs with deduplication |

---

## File Reference

### Backend

| File | Purpose | Key Classes/Functions |
|------|---------|----------------------|
| `components/builtin/knowledge_graph/graph_store.py` | Thin delegate wrapper over kgstore/ | `GraphStore` (`import_bulk`, `replace_model_layer`, `list_all_graphs`), `KnowledgeGraphReadOnlyError`; re-exports `ENTITY_TYPES`, `RELATIONSHIP_TYPES` |
| `components/builtin/knowledge_graph/handler.py` | Component handler + context enrichment | `KnowledgeGraphHandler` (`process`, `get_context_enrichment`, `get_trusted_scope`, `_resolve_grounding_store`), `ENTITY_TYPE_COLORS`, `_rank_seeds`, `_model_gap` |
| `components/builtin/knowledge_graph/resolver.py` / `metric_api.py` / `concepts.py` / `bridge_probe.py` | Deployed resolution, metric compiler, concept declaration, bridge probe | `resolve_capabilities`, … |
| `components/builtin/knowledge_graph/manifest.json` | Component metadata + tool definition | `TDA_KnowledgeGraph` tool spec |
| `components/builtin/knowledge_graph/instructions.json` | Intensity-keyed LLM instructions | `none`, `medium`, `heavy` |
| `src/trusted_data_agent/kgstore/types.py` | THE vocabularies + keep rules | `ENTITY_TYPES`, `RELATIONSHIP_TYPES`, `LAYERS`, `is_kept_on_replace` |
| `src/trusted_data_agent/kgstore/semantic_index.py` | Semantic seeding index | `is_enabled`, `warm`, `graph_signature` |
| `src/trusted_data_agent/kgstore/usage.py` | Usage telemetry (`kg_entity_usage`) | `record`, `touched_entities`, `unshown_tables`, `priors` |
| `src/trusted_data_agent/kgstore/keys.py` | Declared-key reader | `key_columns`, `with_declared_keys` |
| `src/trusted_data_agent/kgstore/base.py` | Backend ABC | `KGStoreBackend` |
| `src/trusted_data_agent/kgstore/factory.py` | Backend registry + resolution chain | `get_backend_for_profile()`, `register_backend()` |
| `src/trusted_data_agent/kgstore/sqlite_backend.py` | SQLite + NetworkX backend | `SQLiteKGBackend`, `_adaptive_subgraph_from_graph` |
| `src/trusted_data_agent/kgstore/neo4j_backend.py` | Neo4j Bolt backend | `Neo4jKGBackend` |
| `src/trusted_data_agent/kgstore/settings.py` | Tier-based governance | `is_backend_allowed()`, `ALL_KG_BACKENDS` |
| `schema/21_knowledge_graph.sql` | SQLite entity/relationship tables | `kg_entities`, `kg_relationships` |
| `schema/22_kg_profile_assignments.sql`, `37_kg_subscriptions.sql`, `41_kg_versions.sql` | Assignments, subscriptions, versions | |
| `schema/38_kg_backend_settings.sql` | Governance settings table | `kg_backend_settings` |
| `src/trusted_data_agent/core/kg_release.py` | Released-only grounding (Model B) | `resolve_grounding` |
| `src/trusted_data_agent/agent/kg_constructors/` | Constructor strategies | `registry.register`, `source_validation`, walkers |
| `src/trusted_data_agent/api/rest_routes.py` | REST API endpoints (53 `/v1/knowledge-graph/*` routes) | `kg_add_entity`, `kg_list_all`, `kg_export`, `kg_graph_spec`, `kg_generate`, etc. |
| `src/trusted_data_agent/api/kg_marketplace_routes.py` | Marketplace publish / subscriptions | |
| `src/trusted_data_agent/components/manager.py` | Component manager orchestration | `get_component_context_enrichment()` |
| `components/builtin/context_window/modules/knowledge_graph/handler.py` | Budget-managed CW module | `KnowledgeGraphContextModule` (`knowledge_graph_context`) |
| `src/trusted_data_agent/components/base.py` | Base handler class | `BaseComponentHandler.get_context_enrichment()` |

### Frontend

| File | Purpose | Key Functions |
|------|---------|---------------|
| `components/builtin/knowledge_graph/renderer.js` | D3 visualization (3 modes) + inspector | `renderKnowledgeGraph()`, `openKGSplitPanel()`, `closeKGSplitPanel()`, `followEffectiveProfile()`, `focusKGNodes()` |
| `static/js/kgVocabulary.js` | Counting vocabulary | `nodesLabel()`, `entitiesLabel()`, `relationshipsLabel()` |
| `static/js/handlers/kgTheatre.js` | Execution theatre | see [KG_INSPECTOR_ARCHITECTURE.md](KG_INSPECTOR_ARCHITECTURE.md) |
| `static/js/handlers/rag/kgConstructorModal.js` | Constructor dialogs | |
| `static/js/handlers/knowledgeGraphPanelHandler.js` | Management UI (2 locations) + backend badge | `loadKnowledgeGraphsPanel()`, `loadKnowledgeGraphsIntelligenceTab()`, `initializeImportHandler()` |
| `static/js/handlers/kgStoreHandler.js` | Graph Stores admin tab | `window.kgStoreHandler` (`load`, `showAddModal`, `showEditModal`, `testConfig`, …) |
| `static/js/api.js` | API client functions | `loadKnowledgeGraphList()`, `exportKnowledgeGraph()`, `deleteKnowledgeGraph()`, `importKnowledgeGraph()`, `fetchKnowledgeGraphSpec()`, `generateKnowledgeGraph()`, `activateKgForProfile()` |
| `templates/index.html` | Split panel DOM + Intelligence tab | `#kg-split-panel`, `#knowledge-graphs-tab-content` |

### Integration

| File | Purpose | Symbol |
|------|---------|--------|
| `src/trusted_data_agent/agent/executor.py` | Enrichment fetch (CW cache or direct) + trusted scope | `_get_kg_enrichment()`, `_apply_trusted_scope()`, `_record_kg_used()`, `_record_kg_missed()` |
| `src/trusted_data_agent/agent/planner.py` | Strategic-planner context injection | the `COMPONENT CONTEXT ENRICHMENT` block in strategic planning (`knowledge_context_str`, `kg_enrichment` event) |
| `src/trusted_data_agent/agent/engines/{ideate,conversation,focus}_engine.py` | Direct engines' enrichment | `executor._get_kg_enrichment()` |
| `src/trusted_data_agent/agent/genie_coordinator.py` | Coordinator enrichment | `get_component_context_enrichment()` |
| `src/trusted_data_agent/agent/phase_executor.py` | KG scope for DB-not-found recovery | `DatabaseNotFoundStrategy._kg_scope_databases()` |
| `static/js/handlers/ragCollectionManagement.js` | Intelligence tab wiring | `initializeKnowledgeGraphIntelligenceHandlers()` |
