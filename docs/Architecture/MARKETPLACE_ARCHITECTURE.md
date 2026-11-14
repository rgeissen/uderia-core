# Intelligence Marketplace — Architecture Document

## Executive Summary

The Intelligence Marketplace is Uderia's collaborative ecosystem for sharing, discovering, and deploying enterprise AI assets. It transforms the platform from a single-user tool into a network where community-validated strategies, domain knowledge, agent configurations, behavioral skills, processing extensions, and entity-relationship models circulate freely — reducing token costs, accelerating onboarding, and enabling continuous improvement through fork-and-improve workflows.

### Product Catalog

The marketplace serves **seven product types** in its UI (one tab each) through a unified infrastructure, plus an eighth that is API-only:

| Product Type | Description | Acquisition Models |
|---|---|---|
| **Planner Repositories** | Proven execution patterns and strategies for autonomous task completion | Subscribe, Fork |
| **Knowledge Repositories** | Reference documents and domain knowledge for planning context | Subscribe, Fork |
| **Agent Packs** | Bundled agent teams (coordinator, experts, knowledge collections) as portable `.agentpack` files | Subscribe, Fork |
| **Skills** | Pre-processing prompt injections that modify LLM behavior (Claude Code compatible `skill.json` + `.md`) | Install |
| **Extensions** | Reusable processing modules with tiered complexity (convention, simple, standard, LLM) | Install |
| **Knowledge Graphs** | Entity-relationship models for database topology, business concepts, and domain ontologies (a signed OBDA-MODEL extension can be its own listing) | Subscribe (snapshot-with-sync), Install, Fork |
| **Component Instances** | Ready-made instances of a component — a flow, a dashboard — never component code | Subscribe (manual sync), Fork |
| **Assurance Modules** *(API only, off by default)* | Third-party trust capability packs (properties, assessors) — see [ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md) | Install (grants availability, never authority) |

### Design Principles

1. **Unified Infrastructure** — Shared tables (`marketplace_sharing_grants`) and consistent API patterns across all product types
2. **Zero-Trust Security** — JWT authentication on every endpoint, ownership validation on every mutation
3. **Ownership Sovereignty** — Publishers retain full control; consumers choose between reference subscriptions (live updates) and independent forks (full autonomy)
4. **Admin Governance** — Skills, extensions, component instances, knowledge graphs and assurance modules each have an administrative enable/disable toggle (collections and agent packs have none — see [Governance & Administration](#governance--administration))
5. **Network Effects** — Ratings, install counts, and subscriber metrics surface the highest-quality assets

---

## System Architecture

### Component Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Frontend (UI Layer)                           │
│  marketplaceHandler.js  │  skillHandler.js  │  agentPackHandler.js  │
│  ─────────────────────────────────────────────────────────────────── │
│  7 Product Type Tabs  │  Browse / My Assets  │  Search, Sort, Rate  │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ REST API (JWT Auth)
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        Backend (API Layer)                           │
│  rest_routes.py          — Collections (Planner + Knowledge),       │
│                            Extensions                               │
│  agent_pack_routes.py    — Agent Packs                              │
│  skills_routes.py        — Skills                                   │
│  kg_marketplace_routes.py — Knowledge Graphs                        │
│  component_instance_marketplace_routes.py — Component Instances     │
│  assurance_module_marketplace_routes.py   — Assurance Modules       │
│  admin_routes.py         — Governance settings                      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────────────┐
│  SQLite Database  │ │   ChromaDB   │ │  File System             │
│  tda_auth.db      │ │ Vector Store │ │  marketplace_data/       │
│  ───────────────  │ │ ──────────── │ │  ──────────────────────  │
│  collections      │ │  Embeddings  │ │  skills/{id}/            │
│  marketplace_*    │ │  per-coll.   │ │  extensions/{id}/        │
│  *_ratings        │ │              │ │  knowledge_graphs/{id}/  │
│  sharing_grants   │ │              │ │  (packs: file_path)      │
└──────────────────┘ └──────────────┘ └──────────────────────────┘
```

### Data Flow Patterns

All product types follow three core lifecycle patterns:

#### Publish Flow

```
Publisher creates asset locally
    ↓
Publisher clicks "Publish"
    ↓
[Backend] JWT authentication + ownership validation
    ↓
[Backend] Validate: asset meets minimum requirements (e.g., ≥1 RAG case)
    ↓
[Database] Insert marketplace record (metadata, visibility, metrics)
    ↓
[Filesystem] Copy asset content to marketplace_data/{type}/{marketplace_id}/
    ↓
Asset appears in marketplace browse (if public) or targeted recipients
```

#### Subscribe / Install Flow

```
Consumer finds asset in marketplace
    ↓
Consumer clicks "Subscribe" / "Install"
    ↓
[Backend] JWT authentication
    ↓
[Backend] Validate: not owner (for subscribe), visibility access check
    ↓
[Database] INSERT subscription/sharing grant  OR  copy asset to user workspace
    ↓
[Database] INCREMENT subscriber_count / install_count
    ↓
Asset available in consumer's workspace
```

#### Fork Flow (Full Copy)

```
Consumer clicks "Fork"
    ↓
[Frontend] Display fork modal (custom name input)
    ↓
[Backend] JWT authentication
    ↓
[Database] Create new record (consumer as owner)
    ↓
[ChromaDB] Copy all embeddings to new collection (Collections only)
    ↓
[Filesystem] Copy all asset files to new directory
    ↓
Consumer owns independent copy — modify freely without affecting original
```

---

## Product Type Architectures

### 1. Collections (Planner & Knowledge Repositories)

Collections are the foundational marketplace product. **Planner Repositories** contain proven execution patterns that guide the autonomous agent, while **Knowledge Repositories** provide reference documents and domain context for planning.

**Database Tables:**

```sql
-- Core collection table (auth/database.py, extended for marketplace)
collections (
    id, name, collection_name, mcp_server_id, description, owner_user_id,
    visibility, is_marketplace_listed, subscriber_count,      -- marketplace fields
    marketplace_category, marketplace_tags, marketplace_long_description,
    repository_type, ...                                      -- + chunking / search columns
)

-- Subscriptions (reference-based, no data duplication)
collection_subscriptions (
    id, user_id, source_collection_id, enabled,
    mcp_server_id,          -- planner keyed to the SUBSCRIBER's server at subscribe time
    subscribed_at, last_synced_at,
    UNIQUE(user_id, source_collection_id)
)

-- Ratings (average computed on read, not stored)
collection_ratings (
    id, collection_id, user_id, rating (1-5), comment, helpful_count,
    created_at, updated_at,
    UNIQUE(collection_id, user_id)
)
```

**Access Control Matrix:**

| Action | Owner | Subscriber | Public User |
|--------|-------|------------|-------------|
| View | Yes | Yes | Yes (if public) |
| Edit | Yes | No | No |
| Delete | Yes | No | No |
| Publish | Yes | No | No |
| Subscribe | No | N/A | Yes |
| Fork | Yes | Yes | Yes (if public) |
| Rate | No | Yes | No (subscribe first) |

**API Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/marketplace/collections` | Browse with search, filters, pagination |
| POST | `/v1/marketplace/collections/:id/subscribe` | Subscribe (reference) |
| DELETE | `/v1/marketplace/subscriptions/:id` | Unsubscribe |
| POST | `/v1/marketplace/collections/:id/fork` | Fork (full copy incl. embeddings) |
| POST | `/v1/rag/collections/:id/publish` | Publish (owner only) |
| POST | `/v1/marketplace/collections/:id/rate` | Rate (1-5 stars + review) |

**Trust consequence of reference vs copy:** a subscriber's repository card shows the publisher's collection id and store — it is a **view**, so its trust state is the publisher's at the same moment. The seal over a planner covers the cases the pack **shipped**, not what the repository learned since (a case seeded by the publisher's own use reaches every subscriber on their next query without moving any seal); a **locked** planner (owner-only `trust_locked`) is sealed over every case and refuses the learning loop. A forked repository hashes on its own. A reference made by an agent-pack subscription follows the pack: the collection unsubscribe refuses it (`409 managed_by_agent_pack`) and the card reads *Removed with the pack*. See [TRUST_MEMBRANE_ARCHITECTURE.md §3a](TRUST_MEMBRANE_ARCHITECTURE.md).

**Key Implementation Detail — Fork:**
Forking a collection is a deep copy operation that clones the database record, copies all ChromaDB vector embeddings to a new collection, and duplicates all RAG case files on disk. The consumer becomes the full owner of the fork.

---

### 2. Agent Packs

Agent Packs bundle complete agent teams — coordinator configurations, expert agents, and their associated knowledge collections — into portable `.agentpack` files for one-click distribution.

**Pack Types:** `genie` (full orchestrator), `bundle` (multi-agent), `single` (individual agent)

**Distribution mode** (`marketplace_agent_packs.distribution_mode`, `schema/36_marketplace_distribution_mode.sql`, set at publish): `both` (default) · `subscribe_only` (the fork route answers `403` and names Subscribe) · `fork_only` (the subscribe route answers `403`). A **deprecated** pack (see [PACK_GOVERNANCE_ARCHITECTURE.md](PACK_GOVERNANCE_ARCHITECTURE.md)) can no longer be forked.

**Profile materialization (publish is metadata-only; subscribe re-exports LIVE profiles):** Publishing an agent pack (`/v1/agent-packs/:id/publish`) writes marketplace metadata only — it points the listing at the publisher's installation and does **not** snapshot the profile definitions. Subscribe (`/v1/marketplace/agent-packs/:id/install`) reads the publisher's **live** profiles at subscribe time (`build_subscribed_profiles` → `get_profile(resource_id, publisher_user_id)` → `_to_manifest_entry` → `_build_profile`), remapping publisher LLM/MCP/collection IDs to the subscriber's. Consequence: a manual change to a published profile (e.g. setting `componentConfig` to memory-only) **propagates to *new* subscribers automatically** (no re-publish needed), and **existing** subscribers take it on their next **Sync** (`POST /v1/marketplace/agent-packs/:id/sync`, subscriber-initiated) or receive it through the publisher's **fan-out** (`fan_out_pack_sync`, scheduled by both in-place update sites — the import route and the fork route — with no subscriber action). A sync re-materializes the copies **in place, ids preserved** (`sync_pack_subscription`): profiles through `_upsert_pack_profile`, the pack's knowledge-graph copies through the KG materializer's update mode (unchanged export hash → kept; changed → `rebuild_copy` under the same local id; a graph the pack no longer ships → deleted), the publisher's trust reinstated. The read side says when a copy is behind: `marketplace_agent_packs.content_updated_at` (moves at publish and on the in-place update only — never on a subscribe or fork) against `agent_pack_installations.synced_at`; browse cards, the installed-pack card **and the subscriber's knowledge-graph cards** all render it. `_to_manifest_entry` carries the full transportable field set including `skillsConfig` and **`componentConfig`** — so component enablement (and thus companion-skill auto-attach) is reproduced faithfully rather than reverting to platform defaults.

**Database Tables:**

```sql
-- Schema: 10_marketplace_agent_packs.sql (+ 36, 44)
marketplace_agent_packs (
    id, name, description, version, author, pack_type,
    publisher_user_id, source_installation_id,
    profile_count, collection_count, coordinator_tag, profile_tags, manifest_summary,
    file_path, file_size_bytes,
    visibility, download_count, install_count,
    published_at, updated_at,
    distribution_mode,      -- 36: both | subscribe_only | fork_only
    content_updated_at      -- 44: moves on publish and in-place update only
)
agent_pack_installations (... synced_at, sync_error)   -- 44: the subscriber's copy

agent_pack_ratings (
    id, pack_id, user_id,
    rating CHECK(1-5), comment,
    created_at, updated_at,
    UNIQUE(pack_id, user_id)
)
```

**API Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/agent-packs/:installation_id/publish` | Publish to marketplace |
| GET | `/v1/marketplace/agent-packs` | Browse published packs |
| GET | `/v1/marketplace/agent-packs/:id` | Detail view |
| GET | `/v1/marketplace/agent-packs/:id/mcp-requirements` | MCP servers the pack needs (for the subscriber's bindings) |
| POST | `/v1/marketplace/agent-packs/:id/install` | Subscribe (sharing grant + materialised copies; optional `mcp_bindings`) |
| POST | `/v1/marketplace/agent-packs/:id/sync` | Subscriber pulls the publisher's current content in place |
| POST | `/v1/agent-packs/:installation_id/sync-subscribers` | Publisher pushes the fan-out now (owner only) |
| DELETE | `/v1/marketplace/agent-packs/:id/subscribe` | Unsubscribe |
| POST | `/v1/marketplace/agent-packs/:id/fork` | Fork (physical copy) |
| GET/PUT | `/v1/marketplace/agent-packs/:id/targeted-users` | Targeted-sharing grantees |
| POST | `/v1/marketplace/agent-packs/:id/rate` | Rate/review |
| DELETE | `/v1/marketplace/agent-packs/:id` | Unpublish |

**Key Implementation Detail — Source Tracking:**
Installed agent packs maintain a `source_installation_id` reference back to the publisher's original, enabling provenance tracking and the update chip described above.

**Republishing without breaking subscribers (`POST /v1/agent-packs/import`, `conflict_strategy`):**

| Strategy | Effect | Subscribers |
|---|---|---|
| `update_in_place` | re-imports on top of the existing pack, preserving the installation id, the marketplace listing id, every per-tag profile id and per-ref collection id (collections paired by identity — repository type + name — never by position); documents diffed by `source_content_hash`; then schedules the fan-out (`schedule_pack_subscriber_sync`). `dry_run=true` returns the plan (`would_preserve`, `would_change`) without committing; a dropped tag is refused (`400 dropped_tags`) unless `force_drop_tags=true`, which removes the profile and archives its sessions | safe — pointers stay valid, content flows through |
| `replace` | deletes the conflicting profiles, the orphaned collections, the old installation row **and its marketplace listing**, then installs fresh ids | **breaks** them |
| `expand` | auto-renames colliding tags and installs alongside | untouched |

**Pack-owned knowledge graphs are refreshed in place** on `update_in_place` (Sep 2026): a consumer's graph copy has no source of its own, so the pack is its source — the graph active for each shipped profile among the pack's `knowledge_graph` resources is snapshotted, cleared and re-imported from the manifest under the same `kg_id`, assignments and attestation identity (response `kgs_refreshed`); a graph the pack does not own is never touched.

**Signature renewal reaches subscribers** (Oct 2026): when a repository's scheduled sync changes content under the owner's standing renewal policy, `core/trust/renewal.renew_after_sync` renews the repository's and its agents' signatures, then renews the seal of every installed pack that ships the repository or a renewed agent and runs that pack's `fan_out_pack_sync`, so each subscriber's copy reinstates the renewed attestations by content. A signature the publisher applies by hand after an import travels the same way through `sync-subscribers`.

---

### 3. Skills

Skills are pre-processing prompt injections that modify LLM behavior before query execution — enforcing SQL best practices, requiring concise responses, enabling chain-of-thought reasoning, etc. Skills use the **Claude Code compatible format** (`skill.json` + `<name>.md`), ensuring portability between Uderia and Claude Code environments.

**Skill Format:**

```
skill-name/
├── skill.json      ← Manifest (name, version, description, author, tags, main_file)
└── skill-name.md   ← Pure markdown content (the actual prompt injection)
```

With optional Uderia enhancement:
```json
{
  "name": "sql-expert",
  "version": "1.0.0",
  "description": "SQL optimization guidance",
  "author": "admin",
  "tags": ["sql", "database"],
  "main_file": "sql-expert.md",
  "uderia": {
    "allowed_params": ["strict", "lenient"],
    "param_descriptions": { "strict": "Enforce strict SQL standards" },
    "injection_target": "system_prompt",
    "icon": "database"
  }
}
```

**Database Tables:**

```sql
-- Schema: 17_marketplace_skills.sql
marketplace_skills (
    id, skill_id, name, description, version, author,
    injection_target, has_params, tags_json,
    publisher_user_id, visibility, manifest_json, content_hash,
    download_count, install_count,
    published_at, updated_at
)

skill_ratings (
    id, skill_marketplace_id, user_id,
    rating CHECK(1-5), comment,
    created_at, updated_at,
    UNIQUE(skill_marketplace_id, user_id)
)
```

**API Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/skills/:skill_id/publish` | Publish user skill |
| GET | `/v1/marketplace/skills` | Browse with search/sort/paginate |
| GET | `/v1/marketplace/skills/:id` | Detail + ratings |
| POST | `/v1/marketplace/skills/:id/install` | Install (writes to the skills state dir `tda_skills/` via `save_skill()`) |
| POST | `/v1/marketplace/skills/:id/rate` | Rate (1-5 + comment) |
| DELETE | `/v1/marketplace/skills/:id` | Unpublish (publisher only) |

**Key Implementation Detail — Hot Reload:**
After installation, the skill manager hot-reloads so the new skill is immediately available for activation via `#skill-name` syntax without restart.

**Export Format:**
Skills export as `.skill` files (ZIP containing `skill.json` + `<name>.md`). Import accepts both `.skill` and `.zip`.

---

### 4. Extensions

Extensions are reusable processing modules that extend agent capabilities. They follow a tiered complexity model that determines their execution environment and resource requirements.

**Extension Tiers:**

| Tier | Description | Execution Model |
|------|-------------|-----------------|
| `convention` | Naming/format conventions | Static rules |
| `simple` | Basic transformations | Lightweight processing |
| `standard` | Full processing pipelines | Standard execution |
| `llm` | LLM-powered extensions | Requires model inference |

**Database Tables:**

```sql
-- Schema: 14_marketplace_extensions.sql
marketplace_extensions (
    id, extension_id, name, description, version, author,
    extension_tier, category, requires_llm,
    publisher_user_id, visibility, manifest_json, source_hash,
    download_count, install_count,
    published_at, updated_at
)

extension_ratings (
    id, extension_marketplace_id, user_id,
    rating CHECK(1-5), comment,
    created_at, updated_at,
    UNIQUE(extension_marketplace_id, user_id)
)
```

**API Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/extensions/:id/publish` | Publish extension |
| GET | `/v1/marketplace/extensions` | Browse with search/sort/paginate |
| GET | `/v1/marketplace/extensions/:id` | Detail + ratings |
| POST | `/v1/marketplace/extensions/:id/install` | Install into system |
| POST | `/v1/marketplace/extensions/:id/rate` | Rate (1-5 + comment) |
| DELETE | `/v1/marketplace/extensions/:id` | Unpublish |

---

### 5. Knowledge Graphs

Knowledge Graphs represent entity-relationship models — database schemas, business concepts, domain ontologies — built through the Intelligence panel. Published KGs can be installed into specific target profiles with merge semantics.

**Database Tables:**

```sql
-- Schema: 23_marketplace_knowledge_graphs.sql (+ 37, runtime ALTERs in auth/database.py)
marketplace_knowledge_graphs (
    id, source_profile_id, name, description, version, author, domain,
    entity_count, relationship_count,
    entity_types_json, relationship_types_json, tags_json,
    publisher_user_id, visibility, manifest_json, content_hash,
    download_count, install_count,
    published_at, updated_at,
    kg_id,                    -- runtime ALTER: original source KG UUID for provenance
    extends_marketplace_id,   -- runtime ALTER (PROP-008 A5): an extension listing names its base
    extension_id,             --   ... and the extension layer it carries; NULL on a base listing
    subscriber_count, export_content_hash   -- 37: Subscribe + auto-sync detection
)

kg_subscriptions (            -- 37: snapshot-with-sync materialisation
    id, user_uuid, source_kg_id, source_marketplace_id, target_profile_id, local_kg_id,
    enabled, subscribed_at, last_synced_at, last_synced_export_hash, last_sync_error,
    source_kind               -- 'agent_pack' when the copy belongs to a pack subscription
)

knowledge_graph_ratings (
    id, kg_marketplace_id, user_id,
    rating CHECK(1-5), comment,
    created_at, updated_at,
    UNIQUE(kg_marketplace_id, user_id)
)

kg_marketplace_settings (     -- key/value: ('kg_marketplace_enabled', 'true')
    id, setting_key, setting_value, updated_at
)
```

**API Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/knowledge-graph/:profile_id/publish` | Publish KG |
| GET | `/v1/marketplace/knowledge-graphs` | Browse with search, sort, domain filter |
| GET | `/v1/marketplace/knowledge-graphs/:id` | Detail + user's rating |
| POST | `/v1/marketplace/knowledge-graphs/:id/install` | Install into target profile |
| POST | `/v1/marketplace/knowledge-graphs/:id/fork` | Fork (alias for install, increments download_count) |
| POST | `/v1/marketplace/knowledge-graphs/:id/subscribe` | Subscribe: a read-only local copy (`kg_metadata.is_subscribed`) kept in sync |
| DELETE | `/v1/marketplace/knowledge-graphs/:id/subscribe` | Unsubscribe |
| POST | `/v1/marketplace/knowledge-graphs/:id/sync` | Re-sync the subscribed copy (refused `409 managed_by_agent_pack` for a pack's copy) |
| GET | `/v1/knowledge-graph/subscriptions` | The caller's KG subscriptions |
| GET/PUT | `/v1/marketplace/knowledge-graphs/:id/targeted-users` | Targeted-sharing grantees |
| POST | `/v1/marketplace/knowledge-graphs/:id/rate` | Rate (1-5 + comment) |
| DELETE | `/v1/marketplace/knowledge-graphs/:id` | Unpublish (publisher only) |
| GET/POST | `/v1/admin/kg-marketplace-settings` | Admin governance |

**Key Implementation Details:**

- **Profile-scoped install:** Unlike Skills (global), KGs install into a specific target profile. Install and fork both create a **new named KG** via `GraphStore(profile_id, user_uuid, kg_id=new_uuid)` + `set_kg_metadata(name, ..., is_active=not has_active)` followed by `import_bulk()`.
- **Non-displacing activation:** The installed KG only becomes the active KG when the target profile has no active KG yet (`is_active=not has_active`). An existing active KG is preserved.
- **Merge semantics:** Entities are matched by `(name, entity_type)` — existing entities are upserted, not replaced.
- **Source provenance:** Installed entities receive `source = 'marketplace'` for tracking.
- **Publish uses `kg_id`:** The publish endpoint accepts `kg_id` in the request body to export a specific named KG rather than the legacy "active KG for profile" lookup.
- **Export format:** Published KGs use v2.0 format (includes `kg_id`, `kg_name`, `kg_description`, `kg_database_name`). v1.0 exports remain import-compatible.
- **`kg_id` column migration:** Added to `marketplace_knowledge_graphs` via runtime `ALTER TABLE` in `database.py` — safe on existing installations.
- **Subscribe = snapshot-with-sync:** a subscribed graph is a read-only local copy rebuilt from the publisher's export when its `export_content_hash` moves; detail in [KG_SUBSCRIPTION_PLAN.md](KG_SUBSCRIPTION_PLAN.md) and [KNOWLEDGE_GRAPH_ARCHITECTURE.md](KNOWLEDGE_GRAPH_ARCHITECTURE.md).
- **Extension listings (PROP-008 A5):** a publish with `extension_id` lists one signed OBDA-MODEL extension of a graph as its own listing (`extends_marketplace_id` names the base); a subscriber may publish its extension of a subscribed graph, never the graph itself (`403`). See [ACT_ARCHITECTURE.md](ACT_ARCHITECTURE.md).

---

### 6. Component Instances

Instances of an instance-capable component (today **flow** and **dashboard**) — data, never code — published from the component and acquired from the **Components** tab. Subscribe materialises a local copy and records a `component_instance_subscriptions` row; **Sync** is manual (never auto-pulled) and re-materialises the copy in place; Fork goes through the shared remap dialog. Publish and fork require the component to be available to the caller and `user_components_marketplace_enabled`.

Tables (`schema/41_marketplace_component_instances.sql`): `marketplace_component_instances` (`component_id`, `instance_id`, `envelope_json`, `content_hash`, counters incl. `subscriber_count`), `component_instance_ratings`, `component_instance_subscriptions` (`local_instance_id`, `synced_content_hash`, `mapping_json`).

Endpoints (`api/component_instance_marketplace_routes.py`): `POST /v1/components/<cid>/instances/<id>/publish`, `GET /v1/marketplace/component-instances` (+ `/mine`, `/<mid>`), `POST …/<mid>/install` (fork), `POST|DELETE …/<mid>/subscribe`, `POST …/<mid>/sync`, `POST …/<mid>/rate`, `DELETE …/<mid>`, `GET/PUT …/<mid>/targeted-users`. Full contract: [COMPONENT_ARCHITECTURE.md § Component-Instance Marketplace](COMPONENT_ARCHITECTURE.md#component-instance-marketplace-publish--fork--subscribesync).

### 7. Assurance Modules (API only)

Third-party trust-capability packs: `POST /v1/assurance-modules/<pack_id>/publish`, `GET /v1/marketplace/assurance-modules`, `POST …/<mid>/install`, `POST …/<mid>/rate`, `DELETE …/<mid>` (`api/assurance_module_marketplace_routes.py`, `schema/43_marketplace_assurance_modules.sql`). Off by default (`assurance_module_marketplace_enabled = false`); no marketplace tab. An install grants availability, never authority. See [ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md).

---

## Shared Infrastructure

### Targeted Sharing (marketplace_sharing_grants)

A unified table enables fine-grained visibility control across all product types:

```sql
-- Schema: 11_marketplace_sharing.sql
marketplace_sharing_grants (
    id,
    resource_type,     -- 'collection', 'agent_pack', 'skill', 'extension', 'knowledge_graph',
                       --   'component_instance', 'assurance_module'
    resource_id,
    grantor_user_id,
    grantee_user_id,
    created_at
)
```

When a publisher sets visibility to **targeted**, they can grant access to specific users. The marketplace browse query joins against this table to filter results per-user.

### Visibility Model

Two things are called "visibility" and they are **not** the same. Conflating them
is what once hid a listing that had been shared correctly the whole time.

**1 — Listing visibility** (what the *publisher* chose, stored on the row). Two
values, unchanged:

| Visibility | Behavior |
|---|---|
| **Public** | Discoverable by all authenticated users in marketplace browse |
| **Targeted** | Visible only to users with an explicit sharing grant |

Private assets (not published) are invisible to the marketplace entirely.

**2 — Browse scope** (what the *viewer* asked to see — the `visibility` query
parameter on every browse endpoint):

| Scope | Returns |
|---|---|
| `public` | Public listings only. **The API default** — the wider blast radius, so it stays the conservative one. |
| `targeted` | Listings granted to the caller, **plus the caller's own targeted listings**. |
| `all` | The **union** of the two. **The UI default.** |

A `targeted` listing is **never** returned under `public`. That is why the UI sends
`all`: browsing the API default, a user would not see a pack shared with them
personally and nothing on screen would say anything was missing.

Three rules this model has to keep, each learned from a defect (Sep 2026):

- **`all` must be a true union.** Four endpoints (agent packs, skills, extensions,
  knowledge graphs) each omitted the `publisher_user_id` branch their own `targeted`
  arm carries, so a publisher's *own* targeted listing vanished under the name "All".
- **The scope must be reachable.** The filter bar is a `.glass-panel`, and
  `backdrop-filter` creates a stacking context — the hand-rolled dropdown's
  `z-index` was trapped inside it and the cards painted over the menu (measured:
  every option unhittable). The menu is portalled to `<body>` while open and
  restored to its exact slot on close — the mechanism `customSelect.js` and
  `splitPanelChrome.js` already use.
- **A changed option set needs a versioned preference key.** The stored UI
  preference is `marketplace_visibility_v2`: the options, labels and default all
  moved, and replaying an old stored value would reinstate the very default that
  hid the grant. One reset; every choice after it respected.

**Vocabulary is per seat, deliberately.** The filter says **"Shared with me"** (the
consumer is asking what they may see); the publish dialog keeps **"Targeted"** (the
publisher is choosing who may see it). Two seats, two correct words for one stored
value.

### Rating Architecture

Each product type has its own rating table following a consistent pattern:

- **1-5 star scale** with optional text comment
- **One rating per user per asset** (UNIQUE constraint, upsert on re-rate)
- **Self-rating prevention** — publishers cannot rate their own assets
- **Acquire before rating** — collections require a subscription; every other type checks the `marketplace_installs` ledger (`schema/35_marketplace_installs.sql`, one row per `artifact_type` · `marketplace_id` · `user_id`, written on install / fork / subscribe)
- **Average rating** computed on read (`AVG(rating)` in the browse/detail queries — collections batch it via `collection_db.get_bulk_collection_ratings`); no average is stored. Detail: [RATING_SYSTEM_IMPLEMENTATION.md](../Marketplace/RATING_SYSTEM_IMPLEMENTATION.md)

### Metrics Tracking

All product types track engagement through consistent counter fields:

| Metric | Incremented On |
|---|---|
| `subscriber_count` | Subscribe action (collections, KGs, component instances) |
| `download_count` | Fork / download action |
| `install_count` | Install action |
| average rating | computed on read from the rating table |

---

### Trust Portability (Trust Membrane, Tier 5.2)

Every product type preserves a signed artefact's **trust** across publish → subscribe/install/
fork. Trust travels with **content, not ids** (signatures are content-bound), via one uniform
contract each surface implements identically:

```
publish     → envelope["trust"] = portability.capture(kind, local_id)   # None if unsigned
materialize → portability.reinstate(kind, new_local_id, envelope.trust) # honesty-gated
```

The **honesty gate** re-establishes a signature only if the materialized copy's content hash
equals what was signed — a re-generated or tampered copy stays ⚪/🟠, never blindly 🟢. The
claim carries the signer's public key, so a cross-instance consumer verifies with the
publisher's key. A read-only "publisher signed" seal is shown on browse cards
(`mountMarketplaceTrust`).

| Product | Portability |
|---|---|
| Knowledge Graphs | copy (new id) → `reinstate("ontology", …)` on install/fork/subscribe/sync |
| Knowledge Graphs **inside an agent pack** (subscribe) | snapshot-with-sync copy keyed to the PACK listing (`kg_subscriptions.source_kind = 'agent_pack'`) → the pack's `member_trust` reinstated per copy on subscribe/sync; the copy follows the pack — its card offers the pack sync and no unsubscribe of its own, and the KG-marketplace sync/unsubscribe refuse it (`409 managed_by_agent_pack`) |
| Agent Packs | pack-level + **per-member** reinstate (content-hash matched) on fork/import |
| Skills / Extensions | preserved id → free within-instance; claim reinstated cross-instance |
| Collections | reference model (`source_collection_id`, same id) → trusted for free within-instance |
| Component instances | flow/dashboard/canvas → `reinstate(component_id, …)` on fork/subscribe |

Full detail: [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md) ·
`TIER_5.2_PORTABILITY.md`.

---

## Security Architecture

### Authentication

All marketplace endpoints require JWT authentication:

```http
Authorization: Bearer <JWT_TOKEN>
```

- Token issued on login, includes `user_id` claim
- Configurable expiration (default: 24 hours)
- Middleware validates and extracts `user_id` before handler execution

### Authorization Rules

| Action | Constraint |
|--------|-----------|
| **Publish** | Must be asset owner |
| **Unpublish** | Must be asset publisher |
| **Subscribe** | Cannot subscribe to own assets |
| **Fork** | Anyone with visibility access |
| **Install** | Anyone with visibility access |
| **Rate** | Cannot rate own assets |
| **Manage sharing grants** | Must be asset publisher |
| **Admin settings** | Must have admin role |

### Input Validation & Threat Mitigation

| Threat | Mitigation |
|--------|------------|
| SQL Injection | Parameterized queries throughout |
| XSS | Output escaping (`escapeHtml()`), Content Security Policy |
| CSRF | JWT in Authorization header (not cookies) |
| Unauthorized Access | JWT validation + ownership checks on every mutation |
| Data Exfiltration | Visibility-based access control per asset |
| Content Integrity | `content_hash` on stored marketplace files |

---

## Frontend Architecture

### UI Structure

The marketplace frontend (sidebar → **Marketplace**) is organized around two main views and seven product type tabs:

**Main Tabs:**
- **Browse Marketplace** — discover and acquire community assets
- **My Assets** — manage your published and installed assets

**Product Type Tabs:**

| Tab | Handler | Icon |
|---|---|---|
| Planner Repositories | `marketplaceHandler.js` | Clipboard |
| Knowledge Repositories | `marketplaceHandler.js` | Chart |
| Agent Packs | `marketplaceHandler.js` | Cube |
| Extensions | `marketplaceHandler.js` | Puzzle |
| Skills | `marketplaceHandler.js` | Wand |
| Knowledge Graphs | `marketplaceHandler.js` | Graph/Link |
| Components | `marketplaceHandler.js` (+ `instancePortabilityUI.js` remap dialog) | Grid |

**Shared UI Components:**
- Search bar with type-ahead
- Sort dropdown (subscribers, downloads, installs, rating, recent)
- Visibility filter — All · Public · Shared with me, each with a "+ mine" variant that adds the caller's own listings (`marketplace_visibility_v2`; reduced to the backend scope by `_backendVisibility`)
- Paginated card grid with glass-morphism design
- Rating modal (interactive 5-star selection + comment)
- Fork modal (custom name input + explainer)
- Install modal (target selector where applicable)
- Publish modal (name, description, version, visibility)

### Card Design

Each product type renders branded cards with consistent structure:
- Asset name and description
- Publisher username
- Type badge with product-specific accent color
- Star rating (visual + numeric)
- Engagement metrics (subscribers, installs, downloads)
- Context-aware action buttons (Subscribe / Fork / Install / Rate / Publish / Unpublish)

---

## Technology Stack

| Layer | Technology |
|---|---|
| Frontend | HTML5, Tailwind CSS, Vanilla JavaScript (ES6 modules) |
| Backend | Python 3.12, Quart (async Flask) |
| Database | SQLite (`tda_auth.db`) |
| Vector Store | ChromaDB (embeddings for Collections) |
| Authentication | JWT (PyJWT) |
| File Storage | Local filesystem (`marketplace_data/`) |

### File Structure

```
uderia/
├── src/trusted_data_agent/
│   ├── api/
│   │   ├── rest_routes.py              # Collection + Extension marketplace endpoints
│   │   ├── agent_pack_routes.py        # Agent Pack marketplace endpoints (+ fan_out_pack_sync)
│   │   ├── skills_routes.py            # Skills marketplace endpoints
│   │   ├── kg_marketplace_routes.py    # Knowledge Graph marketplace endpoints
│   │   ├── component_instance_marketplace_routes.py  # Component-instance marketplace
│   │   ├── assurance_module_marketplace_routes.py    # Assurance-module marketplace
│   │   └── admin_routes.py             # Governance settings
│   ├── core/
│   │   ├── agent_pack_manager.py       # Import strategies, subscribe materialisation, sync
│   │   └── trust/renewal.py            # Signature renewal → pack fan-out
│   ├── agent/
│   │   └── rag_retriever.py            # Collection access control, fork logic
│   ├── skills/settings.py              # Skills marketplace governance
│   ├── extensions/settings.py          # Extensions marketplace governance
│   ├── components/settings.py          # Component-instance marketplace governance
│   ├── kg/settings.py                  # KG marketplace governance
│   ├── assurance_modules/settings.py   # Assurance-module marketplace governance
│   └── auth/
│       ├── database.py                 # Schema bootstrap
│       └── models.py                   # Subscription, Rating models
├── static/js/handlers/
│   ├── marketplaceHandler.js           # Collections, Agent Packs, Extensions, KGs UI
│   └── skillHandler.js                 # Skills marketplace UI
├── schema/
│   ├── 10_marketplace_agent_packs.sql
│   ├── 11_marketplace_sharing.sql
│   ├── 14_marketplace_extensions.sql
│   ├── 16_skill_settings.sql
│   ├── 17_marketplace_skills.sql
│   ├── 23_marketplace_knowledge_graphs.sql
│   ├── 35_marketplace_installs.sql
│   ├── 36_marketplace_distribution_mode.sql
│   ├── 37_kg_subscriptions.sql
│   ├── 41_marketplace_component_instances.sql
│   ├── 43_marketplace_assurance_modules.sql
│   └── 44_pack_subscription_sync.sql
├── marketplace_data/                   # Published asset files (project root)
│   ├── skills/{marketplace_id}/
│   ├── extensions/{marketplace_id}/
│   └── knowledge_graphs/{marketplace_id}/kg_export.json
│   # agent packs: the .agentpack path is stored on the listing (file_path)
└── templates/
    └── index.html                      # Marketplace UI
```

---

## API Reference — Complete Endpoint Catalog

### Collection Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/marketplace/collections` | Yes | Browse collections |
| POST | `/v1/marketplace/collections/:id/subscribe` | Yes | Subscribe |
| DELETE | `/v1/marketplace/subscriptions/:id` | Yes | Unsubscribe |
| POST | `/v1/marketplace/collections/:id/fork` | Yes | Fork (deep copy) |
| POST | `/v1/rag/collections/:id/publish` | Owner | Publish |
| POST | `/v1/marketplace/collections/:id/rate` | Yes | Rate |

### Agent Pack Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/v1/agent-packs/:installation_id/publish` | Owner | Publish |
| GET | `/v1/marketplace/agent-packs` | Yes | Browse |
| GET | `/v1/marketplace/agent-packs/:id` | Yes | Detail |
| GET | `/v1/marketplace/agent-packs/:id/mcp-requirements` | Yes | MCP requirements |
| POST | `/v1/marketplace/agent-packs/:id/install` | Yes | Subscribe |
| POST | `/v1/marketplace/agent-packs/:id/sync` | Subscriber | Sync the copy |
| POST | `/v1/agent-packs/:installation_id/sync-subscribers` | Owner | Fan out to subscribers |
| DELETE | `/v1/marketplace/agent-packs/:id/subscribe` | Yes | Unsubscribe |
| GET/PUT | `/v1/marketplace/agent-packs/:id/targeted-users` | Publisher | Targeted users |
| POST | `/v1/marketplace/agent-packs/:id/fork` | Yes | Fork |
| POST | `/v1/marketplace/agent-packs/:id/rate` | Yes | Rate |
| DELETE | `/v1/marketplace/agent-packs/:id` | Publisher | Unpublish |

### Skill Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/v1/skills/:skill_id/publish` | Owner | Publish |
| GET | `/v1/marketplace/skills` | Yes | Browse |
| GET | `/v1/marketplace/skills/:id` | Yes | Detail |
| POST | `/v1/marketplace/skills/:id/install` | Yes | Install |
| POST | `/v1/marketplace/skills/:id/rate` | Yes | Rate |
| DELETE | `/v1/marketplace/skills/:id` | Publisher | Unpublish |

### Extension Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/v1/extensions/:id/publish` | Owner | Publish |
| GET | `/v1/marketplace/extensions` | Yes | Browse |
| GET | `/v1/marketplace/extensions/:id` | Yes | Detail |
| POST | `/v1/marketplace/extensions/:id/install` | Yes | Install |
| POST | `/v1/marketplace/extensions/:id/rate` | Yes | Rate |
| DELETE | `/v1/marketplace/extensions/:id` | Publisher | Unpublish |

### Knowledge Graph Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/v1/knowledge-graph/:profile_id/publish` | Owner | Publish |
| GET | `/v1/marketplace/knowledge-graphs` | Yes | Browse |
| GET | `/v1/marketplace/knowledge-graphs/:id` | Yes | Detail |
| POST | `/v1/marketplace/knowledge-graphs/:id/install` | Yes | Install into profile |
| POST | `/v1/marketplace/knowledge-graphs/:id/fork` | Yes | Fork |
| POST/DELETE | `/v1/marketplace/knowledge-graphs/:id/subscribe` | Yes | Subscribe / unsubscribe |
| POST | `/v1/marketplace/knowledge-graphs/:id/sync` | Subscriber | Sync the copy |
| POST | `/v1/marketplace/knowledge-graphs/:id/rate` | Yes | Rate |
| DELETE | `/v1/marketplace/knowledge-graphs/:id` | Publisher | Unpublish |

### Component Instance & Assurance Module Endpoints

See [§6](#6-component-instances) and [§7](#7-assurance-modules-api-only).

### Admin Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET/POST | `/v1/admin/kg-marketplace-settings` | Admin | KG marketplace toggle |
| GET/POST | `/v1/admin/skill-settings` | Admin | Skills marketplace toggle (among the skill settings) |
| GET/POST | `/v1/admin/extension-settings` | Admin | Extensions marketplace toggle |
| GET/POST | `/v1/admin/component-settings` | Admin | Component-instance marketplace toggle |
| GET/PUT | `/v1/agent-packs/governance/policy` | Admin | Pack lifecycle policy (approval, trust-gated publish) |

### Common API Patterns

**Pagination** (browse endpoints; collections use `limit`/`offset` instead):
```
?page=1&per_page=10
```

**Search** (all browse endpoints):
```
?search=keyword
```

**Sort** (all browse endpoints):
```
?sort_by=rating|recent|installs|downloads|subscribers
```

**Error Response Format:**
```json
{
  "error": "Human-readable error message",
  "details": "Optional additional context"
}
```

**HTTP Status Codes:**
- `200` — Success
- `400` — Invalid input
- `401` — Missing/invalid JWT
- `403` — Insufficient permissions (or marketplace disabled)
- `404` — Resource not found
- `409` — Conflict (already subscribed, already rated, etc.)
- `500` — Server error

---

## Governance & Administration

### Per-Product-Type Toggles

Administrators control marketplace availability through settings tables:

| Product Type | Setting | Location |
|---|---|---|
| Skills | `user_skills_marketplace_enabled` | `skill_settings` table |
| Extensions | `user_extensions_marketplace_enabled` | `extension_settings` table |
| Component instances | `user_components_marketplace_enabled` | `component_settings` table |
| Knowledge Graphs | `kg_marketplace_enabled` | `kg_marketplace_settings` table |
| Assurance modules | `assurance_module_marketplace_enabled` (default **off**) | assurance-module settings |

Collections and agent packs have no marketplace toggle; agent packs are governed by the pack lifecycle policy instead ([PACK_GOVERNANCE_ARCHITECTURE.md](PACK_GOVERNANCE_ARCHITECTURE.md)).

When disabled:
- Publish returns `403 Forbidden`
- Marketplace browse button hidden in UI
- Install returns `403 Forbidden`
- Existing installations are not affected

Managed via: **Administration → App Config** (the Skill, Extension and Component Management sections carry the skill, extension and component marketplace checkboxes); the KG and assurance-module toggles are set through their admin endpoints.

---

## Performance & Scalability

### Current Performance Characteristics

| Operation | Typical Latency | Notes |
|---|---|---|
| Browse (paginated) | < 200ms | JOIN-optimized queries, 10 items/page |
| Subscribe / Unsubscribe | < 50ms | Simple INSERT/DELETE |
| Rate | < 50ms | Upsert + average recomputation |
| Fork (Collection, 100 embeddings) | 1-2s | ChromaDB copy + file copy |
| Install (Skill) | < 200ms | File copy + hot-reload |
| Install (KG, 500 entities) | 1-3s | Bulk import with merge |

### Database Indexes

```sql
CREATE UNIQUE INDEX idx_subscription_user_collection ON collection_subscriptions(user_id, source_collection_id);
CREATE INDEX idx_subscription_user ON collection_subscriptions(user_id);
CREATE INDEX idx_subscription_collection ON collection_subscriptions(source_collection_id);
CREATE INDEX idx_rating_collection ON collection_ratings(collection_id);
-- Similar indexes exist for all product-type rating and subscription tables
```

### Scalability Considerations

| Component | Current | Production Scale |
|---|---|---|
| Database | SQLite (single-instance) | PostgreSQL for multi-user |
| Vector Store | ChromaDB (local) | Distributed (Milvus, Weaviate) |
| File Storage | Local filesystem | S3 / blob storage |
| Search | SQL LIKE queries | Full-text search / Elasticsearch |
| Caching | None | Redis for browse result caching |

---

## Document History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2024 | Initial guide (Phases 1-4: Collections) |
| 1.1 | Feb 2026 | Added Skills (Phase 5) and Knowledge Graphs (Phase 6) |
| 1.2 | Mar 2026 | Added Extensions and Agent Packs |
| 2.0 | Mar 2026 | Elevated to Architecture Document — unified all 6 product types, removed phase-by-phase build log, restructured around architectural concerns |
| 2.1 | 2026-10-30 | Checked against code: Component Instances and Assurance Modules added; KG Subscribe; agent-pack distribution modes, conflict strategies, in-place KG refresh, sync/fan-out incl. signature renewal; schema blocks, sharing-grant columns, governance toggles, file structure and rating eligibility corrected |
