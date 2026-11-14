# KG Store Abstraction Layer — Architecture

> **Proof-pack:** [P6 — Sovereignty / Hybrid Deployment](../proof-packs/P6_SOVEREIGNTY.md) (deploy-anywhere matrix; SQLite + NetworkX is the fully-local / zero-dependency KG backend) — the publishable evidence artifact for assessment requirement P6.

**Last updated:** 2026-10-30

## Overview

The Uderia Platform's **KG Store Abstraction Layer** decouples all knowledge graph storage, traversal, and query operations from any specific graph database or library. It defines a single synchronous interface that all backends implement, enables capability-based negotiation for optional features (graph algorithms, native Cypher queries, full-text search, adaptive subgraph extraction, semantic search), and ships with two backends: **SQLite + NetworkX** (local/embedded, default) and **Neo4j** (enterprise graph database).

The abstraction makes it possible to add new graph backends without touching any agent, route, or UI code — only the backend module and a one-line registry call are required.

---

## Module Layout

```
src/trusted_data_agent/kgstore/
├── __init__.py               # Public API surface — re-exports + auto-registration of backends
├── base.py                   # KGStoreBackend abstract base class
├── capabilities.py           # KGStoreCapability enum + REQUIRED_CAPABILITIES
├── types.py                  # Shared data types (KGEntity, KGRelationship, KGBackendConfig)
├── settings.py               # Governance settings (tier gates + allowed backends)
├── factory.py                # Backend registry + per-profile resolution
├── sqlite_backend.py         # SQLite + NetworkX implementation
├── neo4j_backend.py          # Neo4j Bolt implementation (optional dependency)
│   # — pure / backend-independent helpers that live beside the layer —
├── conformance.py            # check_conformance(): zero-LLM ontology-topology validator (ONTOLOGY_ARCHITECTURE)
├── graph_diff.py             # diff_graphs(): layer-aware diff of two graph states (update lifecycle, version diffs)
├── keys.py                   # key_columns(): THE reading of an entity's declared key columns
├── migrations.py             # entity-type rename + edge-vocabulary data migrations (idempotent)
├── semantic_index.py         # embedding seed index: the second recall channel for KG retrieval
└── usage.py                  # kg_entity_usage telemetry (seeded / rendered / used) — a bounded ranking prior
```

| File | Role |
|---|---|
| `base.py` | Abstract interface all backends implement |
| `types.py` | Normalized data structures |
| `capabilities.py` | Capability flags + required set |
| `settings.py` | Tier-based governance (enabled flags, allowed backend lists) |
| `factory.py` | Registry + resolution chain (profile → user default → global SQLite) |
| `sqlite_backend.py` | SQLite concrete backend with NetworkX in-memory graph |
| `neo4j_backend.py` | Neo4j concrete backend (graceful fallback when package absent) |
| `conformance.py` | Pure validator returning `conformant` / `partial` / `nonconformant` with typed, counted issues; its type sets are pinned equal to `graph_store.ENTITY_TYPES` / `RELATIONSHIP_TYPES` by `test/test_kg_conformance.py` |
| `graph_diff.py` | Pure diff keyed by `(name, entity_type)` / `(source, target, relationship_type)`; shows what a KG update would change before it is confirmed |
| `keys.py` | `KEY_PROPERTIES = ("business_key", "natural_key_column")`; every runtime guard that asks for an entity's key (column-existence preflight, the JOIN check) reads it here, so two checks over one graph cannot disagree |
| `migrations.py` | Data migrations (no schema change): logical `table`/`column` → `entity`/`attribute`, `physical_*` → `table`/`column`, layer-guarded and idempotent; edge-vocabulary normalisation. Gate: `test/test_kg_type_migration.py` |
| `semantic_index.py` | One float32 matrix per graph signature, persisted and swept when idle; search is one matmul. ON by default (`APP_CONFIG.KG_SEMANTIC_SEEDING`); lexical ranking stays authoritative, semantic hits only widen the candidate pool |
| `usage.py` | Usage counters kept in their own `kg_entity_usage` table — never on `kg_entities`, so recording neither invalidates the graph cache nor moves a trust hash; `priors()` is 0.0 without data |

---

## Core Components

### 1. Abstract Base — `KGStoreBackend`

**File:** [kgstore/base.py](../../src/trusted_data_agent/kgstore/base.py)

Every backend extends `KGStoreBackend` and implements the following groups of methods:

#### Lifecycle

| Method | Description |
|---|---|
| `initialize()` | One-time setup (create tables, establish connections, seed counters) |
| `shutdown()` | Graceful teardown (close connections, flush caches) |
| `capabilities() → set[KGStoreCapability]` | Declare supported capability flags |
| `test_connection() → dict` | Health check — returns `{"success": bool, "message": str}` |
| `invalidate_cache(kg_id)` | Evict any in-process graph cache for the given KG |

#### Entity CRUD

| Method | Description |
|---|---|
| `add_entity(kg_id, name, entity_type, properties, source_chunk_id) → int` | Insert entity, return assigned integer ID |
| `get_entity(kg_id, entity_id) → dict \| None` | Fetch single entity by ID |
| `get_entity_by_name(kg_id, name, entity_type) → dict \| None` | Fetch by name (served from the cached graph on SQLite) |
| `search_entities(kg_id, query, entity_type, limit) → list[dict]` | Name/property search |
| `list_entities(kg_id, entity_type, limit) → list[dict]` | List with optional type filter |
| `update_entity(kg_id, entity_id, properties, name, entity_type) → bool` | Patch fields |
| `delete_entity(kg_id, entity_id) → bool` | Hard delete + cascade relationships |

#### Relationship CRUD

| Method | Description |
|---|---|
| `add_relationship(kg_id, from_id, to_id, rel_type, properties) → int` | Insert edge, return ID |
| `get_relationships(kg_id, entity_id, direction, rel_type, limit) → list[dict]` | Fetch edges |
| `list_relationships(kg_id, rel_type, limit) → list[dict]` | List with optional type filter |
| `get_relationship(kg_id, rel_id) → dict \| None` | Fetch single edge |
| `delete_relationship(kg_id, rel_id) → bool` | Remove edge |

#### Bulk Operations

| Method | Description |
|---|---|
| `import_bulk(kg_id, entities, relationships) → dict` | Transactional batch ingest. **Validates** entity/relationship types (rejects unknown), stamps `layer` when absent, and carries `source_detail`. Returns real inserted-vs-updated counts: `{entities_added, entities_updated, entities_rejected, relationships_added, relationships_updated, relationships_rejected, relationships_unresolved}` (not rows-submitted). |
| `clear_graph(kg_id) → dict` | Remove all entities and relationships for a KG; returns the deleted counts |
| `replace_model_layer(kg_id, profile_id, user_uuid, entities, relationships, keep_layers) → dict` | THE resync operation (instead of `clear_graph` + `import_bulk`): replaces the walked model, keeps every node a walk never produces (`types.is_kept_on_replace` — the kept layers, an extension's realisation, stacked contracts), re-links kept edges by name and names any it had to drop in `kept_edges_dropped` |
| `get_stats(kg_id) → dict` | Counts, density, connected components, has_cycles |

#### Optional Graph Algorithms (capability-gated)

| Method | Capability Required | Description |
|---|---|---|
| `extract_subgraph(kg_id, entity_id, depth) → dict` | `GRAPH_ALGORITHMS` | BFS/DFS neighbourhood |
| `get_entity_importance(kg_id, limit) → list` | `GRAPH_ALGORITHMS` | Degree + centrality ranking |
| `detect_cycles(kg_id) → list` | `GRAPH_ALGORITHMS` | Cycle detection |
| `find_shortest_path(kg_id, from_id, to_id) → list` | `GRAPH_ALGORITHMS` | Dijkstra / BFS |
| `native_query(kg_id, query_string) → list[dict]` | `NATIVE_GRAPH_QUERIES` | Raw Cypher / Gremlin |
| `full_text_search(kg_id, query, limit) → list[dict]` | `FULL_TEXT_SEARCH` | Substring (`LIKE`) match over name + serialized properties — not a full-text index |
| `extract_subgraph_adaptive(kg_id, seed_entity_ids, query_entity_ids, max_nodes, query_terms, per_table_cap) → dict` | `ADAPTIVE_SUBGRAPH` | The **primary enrichment path**: relevance/FK-scoped, per-table column-capped extraction over the cached graph (§4.1). Delegates to the shared pure function `_adaptive_subgraph_from_graph(G, …)`. `extract_subgraph` above stays the plain BFS neighbourhood. |

---

### 2. Capability System

**File:** [kgstore/capabilities.py](../../src/trusted_data_agent/kgstore/capabilities.py)

```python
class KGStoreCapability(Enum):                 # plain Enum + auto() — the members
    # Required — all backends must implement these   # are identity, not string values
    ADD_ENTITY = auto()
    GET_ENTITY = auto()
    DELETE_ENTITY = auto()
    LIST_ENTITIES = auto()
    ADD_RELATIONSHIP = auto()
    DELETE_RELATIONSHIP = auto()
    LIST_RELATIONSHIPS = auto()
    IMPORT_BULK = auto()
    CLEAR_GRAPH = auto()
    GET_STATS = auto()

    # Optional — advertised but not universally supported
    GRAPH_ALGORITHMS = auto()
    NATIVE_GRAPH_QUERIES = auto()
    FULL_TEXT_SEARCH = auto()
    SEMANTIC_SEARCH = auto()
    ADAPTIVE_SUBGRAPH = auto()                 # the shared relevance-scoped extractor (§4.1)
```

`REQUIRED_CAPABILITIES` is the set of the first 10 — every backend must declare all of them (`assert_required_capabilities()` runs when the factory builds an instance).

The base class also carries non-abstract read helpers with a default implementation over the required methods, which a backend may override: `get_full_graph`, `get_typed_subgraph`, `get_schema_overview`, `get_connected_entities`, `all_table_columns`, `get_node_neighborhood`.

---

### 3. Factory & Resolution Chain

**File:** [kgstore/factory.py](../../src/trusted_data_agent/kgstore/factory.py)

The factory maintains two module-level dicts:

```python
_REGISTRY: Dict[str, Type[KGStoreBackend]]   # "sqlite" → SQLiteKGBackend, "neo4j" → Neo4jKGBackend
_INSTANCES: Dict[str, KGStoreBackend]         # cache: config fingerprint (md5 of type + config) → initialized instance
```

**Resolution chain for `get_backend_for_profile(profile_id, user_uuid, config_id=None)`** — the selection half is `resolve_backend_choice(...)`, which answers *which* backend without instantiating (connecting to) it, so a UI badge or an offline gate can ask without dialling a remote host:

```
0. Explicit config_id (KG constructor / fork / install request, or the KG's own kg_metadata.kg_store_config_id)
         ↓ (if it names nothing — falls through, never fails)
1. Load profile → read profile.kgStoreConfigId
         ↓ (if set)
2. Load named KG backend configuration from user_preferences
         ↓ (if not set / config not found)
3. Load user's default KG backend configuration (is_default=True)
         ↓ (if none)
4. Return get_default_sqlite_backend()  ← always succeeds
```

**Instance caching:** Once initialized, a backend instance is cached by its config fingerprint, so an edited configuration (new credentials) is a new key and gets a fresh instance. `reset_instance_cache()` shuts every cached instance down (tests, maintenance); a backend's own `invalidate_cache(kg_id)` evicts its in-process graph cache.

**Adding a new backend:**

```python
# In your_backend.py:
from trusted_data_agent.kgstore.base import KGStoreBackend
from trusted_data_agent.kgstore.factory import register_backend

class MyKGBackend(KGStoreBackend):
    def capabilities(self):
        return {KGStoreCapability.ADD_ENTITY, ...}
    # implement all required methods ...

register_backend("mybackend", MyKGBackend)

# In kgstore/__init__.py:
from . import your_backend  # triggers registration
```

That is the only change required — no routes, no UI code, no handler code needs to be modified.

---

### 4. GraphStore Delegate

**File:** [components/builtin/knowledge_graph/graph_store.py](../../components/builtin/knowledge_graph/graph_store.py)

`GraphStore` is the **thin delegate wrapper** that the rest of the platform uses. It maintains the same public interface as before the abstraction was introduced, while internally routing all calls through the factory to the appropriate backend.

**Key design points:**

- All CRUD methods (`add_entity`, `update_entity`, `delete_entity`, `search_entities`, `list_entities`, `add_relationship`, `get_relationships`, `list_relationships`, `delete_relationship`, `get_stats`) are `async def` — bodies are synchronous but making them awaitable is required because all REST handlers `await` them.
- KG metadata methods (`set_kg_metadata`, `get_kg_metadata`, `rename_kg`, `set_active_kg`, `list_all_graphs`) remain synchronous `def` — they operate directly on `tda_auth.db`, not the backend.
- `_get_backend()` is lazy — called on first use, triggering factory resolution.
- Writes are gated by the graph's **write mode** (`_write_mode()`): whole-graph rewrites (`import_bulk`, `clear_graph`, `replace_model_layer`) call `_assert_writable()` and raise `KnowledgeGraphReadOnlyError` on a subscribed graph; node-level writes call `_assert_layer_writable()`, which lets a subscriber edit the graph's **authored** layer while its base (publisher) layer stays read-only.

```python
class GraphStore:
    def __init__(self, profile_id, user_uuid, kg_id=None, kg_store_config_id=None):
        self.profile_id = profile_id
        self.user_uuid = user_uuid
        self.kg_id = kg_id
        self._kg_store_config_id = kg_store_config_id  # explicit override (constructor/import)
        self._backend = None  # lazy

    def _get_backend(self) -> KGStoreBackend:
        if self._backend is None:
            # 1. Explicit config_id (passed at construction) takes priority.
            # 2. Otherwise read kg_store_config_id from kg_metadata for the
            #    active KG — ensures panel/inspector/graph-spec always routes
            #    to the correct backend, not the profile's current default.
            effective_config_id = self._kg_store_config_id
            if not effective_config_id:
                row = conn.execute(
                    "SELECT kg_store_config_id FROM kg_metadata "
                    "WHERE profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                    (self.profile_id, self.user_uuid),
                ).fetchone()
                if row:
                    effective_config_id = row["kg_store_config_id"]
            self._backend = get_backend_for_profile(
                self.profile_id, self.user_uuid, config_id=effective_config_id
            )
        return self._backend

    async def add_entity(self, name, entity_type, ...):
        self._assert_writable()
        return self._get_backend().add_entity(self.kg_id, name, entity_type, ...)
```

---

### 5. Governance — Settings & Tier Gates

**File:** [kgstore/settings.py](../../src/trusted_data_agent/kgstore/settings.py)

Governance is stored in the `kg_backend_settings` table (seeded by `schema/38_kg_backend_settings.sql`):

| Key | Default | Meaning |
|---|---|---|
| `kg_enabled_user` | `"true"` | Whether `user` tier can use KG at all |
| `kg_enabled_developer` | `"true"` | Whether `developer` tier can use KG at all |
| `allowed_backends_user` | `'["sqlite"]'` | JSON list of backend types user tier may configure |
| `allowed_backends_developer` | `'["sqlite", "neo4j"]'` | JSON list for developer tier |

Admin tier always bypasses both gates (can use all backends regardless of settings).

**API:**

```
GET  /api/v1/kg-store/settings        — read current settings (admin only)
PUT  /api/v1/kg-store/settings        — update settings (admin only)
GET  /api/v1/kg-store/backends        — list available backends + allowed_for_tier flag
```

---

### 6. Configuration Management

KG backend configurations are stored as JSON objects in `user_preferences.preferences_json` under the `"kg_backend_configurations"` key — the same pattern used for LLM configurations, profiles, and context window types.

Each configuration object:

```json
{
  "id": "kg-1778536383518-a5590bfd",
  "name": "Production Neo4j",
  "backend_type": "neo4j",
  "is_default": false,
  "config": {
    "uri": "bolt://neo4j-host:7687",
    "username": "neo4j",
    "password": "...",
    "database": "neo4j"
  },
  "created_at": "2026-05-11T21:53:03.518423+00:00"
}
```

The built-in `kg-default-sqlite` configuration is always present and cannot be deleted (enforced by `remove_kg_backend_configuration()`).

**REST API:**

```
GET    /api/v1/kg-store/configurations          — list user's configurations
POST   /api/v1/kg-store/configurations          — create new configuration
GET    /api/v1/kg-store/configurations/<id>     — read single configuration
PUT    /api/v1/kg-store/configurations/<id>     — update configuration
DELETE /api/v1/kg-store/configurations/<id>     — delete (blocks default)
POST   /api/v1/kg-store/configurations/<id>/test — test connection
```

---

### 7. Backend Implementations

#### SQLiteKGBackend

**File:** [kgstore/sqlite_backend.py](../../src/trusted_data_agent/kgstore/sqlite_backend.py)

- **Storage:** SQLite database at `tda_auth.db` — tables `kg_entities` and `kg_relationships`, both scoped by `kg_id`
- **In-memory graph:** NetworkX `DiGraph` cached per `kg_id`, **LRU-bounded** (`_GRAPH_CACHE_MAX = 6`) and evicted by `invalidate_cache(kg_id)`. When the graph is loaded, `get_entity_by_name` / `get_relationships` are served from it (no SQL round-trip)
- **Capabilities:** the 10 required + `FULL_TEXT_SEARCH` (unconditional), plus `GRAPH_ALGORITHMS` + `ADAPTIVE_SUBGRAPH` when NetworkX is present (**13 total**)
- **Dependencies:** None beyond the standard library + `networkx` (already in `requirements.txt`)
- **Graph algorithms:** `extract_subgraph_adaptive` is the relevance/FK-scoped adaptive extractor (§4.1) delegating to the shared pure `_adaptive_subgraph_from_graph`; degree-based importance ranking; cycle detection (`get_stats.has_cycles` via `nx.find_cycle`, `detect_cycles` bounded by `islice` — not full `simple_cycles` enumeration); shortest path via `nx.shortest_path`

#### Neo4jKGBackend

**File:** `kgstore/neo4j_backend.py`

- **Transport:** Bolt driver (`neo4j` Python package)
- **Graceful fallback:** `HAS_NEO4J = False` at module level when package not installed — `test_connection()` returns `{"success": False, "message": "neo4j package not installed"}` and all methods raise `RuntimeError` rather than crashing at import time
- **Integer IDs:** Neo4j nodes use native integer IDs via a `_KGCounter` label (one node per `kg_id`, `MERGE`d); entity IDs are stored as `entity_id` property alongside properties
- **Dict key contract:** `_record_to_entity()` adds `"id"` as an alias for `"entity_id"` so all callers that read `entity["id"]` work regardless of backend
- **Relationship dicts:** `get_relationships()`, `list_relationships()`, and `get_full_graph()` all return `source_name`, `target_name`, `source_type`, `target_type` fields (fetched from the source/target node via Cypher JOIN) to match the SQLite `_row_to_relationship()` contract. `get_full_graph()` relationships use `source_id`/`target_id` keys (not `source_entity_id`) to match the SQLite `_subgraph_to_dict()` contract expected by the graph-spec renderer.
- **Multi-tenancy:** All nodes carry a `kg_id` property — complete isolation between KGs on the same Neo4j instance
- **Adaptive parity (Aug 2026):** `extract_subgraph_adaptive` is no longer a BFS stub — it materializes the KG into a NetworkX `DiGraph` (`_to_networkx`, entity_id-keyed, mirroring the SQLite node attrs) and calls the **same** shared `_adaptive_subgraph_from_graph`, so the extraction algorithm is identical across backends; `search_entities` now matches entity name **and** serialized properties (recall parity with SQLite)
- **Capabilities:** All 10 required + `GRAPH_ALGORITHMS`, `NATIVE_GRAPH_QUERIES`, `FULL_TEXT_SEARCH`, plus `ADAPTIVE_SUBGRAPH` when NetworkX is present (**14 total**)
- **Installation:** `pip install neo4j`

---

## Data Flow: Entity Creation

```
REST handler: POST /api/v1/knowledge-graph/entities
       │
       ▼
_get_graph_store(user_uuid, profile_id)
       │  creates GraphStore(profile_id=..., user_uuid=..., kg_id=...)
       ▼
await store.add_entity(name, entity_type, properties)
       │
       ▼  GraphStore._get_backend()
       │       └─ factory.get_backend_for_profile(profile_id, user_uuid, config_id)
       │               0. the KG's kg_metadata.kg_store_config_id (or an explicit override)
       │               1. profile.kgStoreConfigId → named config
       │               2. user's is_default config
       │               3. global SQLite fallback
       ▼
KGStoreBackend.add_entity(kg_id, name, entity_type, properties)
       │
       ├─ SQLiteKGBackend: INSERT INTO kg_entities ... → int ID
       └─ Neo4jKGBackend:  MERGE counter + CREATE (entity) ... → int ID
```

---

## Admin UI

The **Graph Stores** tab in Anatomy (Anatomy → Graph Stores) provides:

- **Backend list:** Available backends with `allowed_for_tier` badge
- **Configuration cards:** Named backend configurations with Edit / Delete / Test actions
- **Add/Edit modal:** Name + backend type + backend-specific fields (Neo4j: URI, username, password, database)
- **Test Connection:** Calls `/api/v1/kg-store/configurations/<id>/test`, shows success/error inline
- **Governance section** (admin only): Enable/disable KG per tier, set allowed backend lists per tier

The **Profile Edit dialog** includes a **KG Backend** dropdown (populated by `kgStoreHandler.populateProfileSelect()`) that lets each profile be pinned to a specific backend configuration. Leaving it empty uses the resolution chain.

**Live-stats enrichment in `kg_list_all`:** The `/v1/knowledge-graph/list` endpoint always calls `GraphStore.list_all_graphs()`, which counts entities and relationships from the SQLite `kg_entities`/`kg_relationships` tables. For non-SQLite KGs those tables are empty. After enriching each KG entry with its backend config, the endpoint calls `backend.get_stats(kg_id)` for any KG whose `backend_type != "sqlite"` and overwrites `total_entities`, `total_relationships`, `entity_types`, and `relationship_types` with the live backend counts. This ensures KG cards always show accurate counts regardless of where data is stored.

---

## Adding a New Backend — Checklist

1. Create `src/trusted_data_agent/kgstore/<name>_backend.py`
2. Implement `KGStoreBackend` — all 10 required methods + any optional capabilities
3. Call `register_backend("<name>", YourBackend)` at module level
4. Import the module in `kgstore/__init__.py` (triggers auto-registration)
5. Add `"<name>"` to `ALL_KG_BACKENDS` in `kgstore/settings.py`
6. Add the backend type to the `<select>` in the Add/Edit modal in `kgStoreHandler.js` and expose its connection fields via `toggleBackendFields()`

No other files need to change.

---

## Related Documentation

- [Vector Store Abstraction Architecture](VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md) — the analogous layer for RAG/vector search
- [KG Subscription Plan](KG_SUBSCRIPTION_PLAN.md) — marketplace subscribe/sync for Knowledge Graphs
- [Engine Modularization Architecture](ENGINE_MODULARIZATION_ARCHITECTURE.md) — IFOC execution engine dispatch
