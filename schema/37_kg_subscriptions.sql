-- ============================================================
-- Schema: Knowledge Graph Subscriptions
-- Version: 1.0
-- Description: Live-tracking subscriptions for marketplace KGs.
--              Subscribers get a deep-copy materialization into
--              their target profile, marked is_subscribed=1
--              (read-only). On publisher republish, the local
--              copy is rebuilt from the new export — preserving
--              activation state but replacing entities and
--              relationships.
--
-- Companion to:
--   - 23_marketplace_knowledge_graphs.sql (publisher listings)
--   - 21_knowledge_graph.sql              (entity/relationship store)
--   - 22_kg_profile_assignments.sql       (per-profile activation)
--
-- Design decisions in docs/Architecture/KG_SUBSCRIPTION_PLAN.md.
-- ============================================================

-- The subscription rows themselves
CREATE TABLE IF NOT EXISTS kg_subscriptions (
    id                       TEXT    PRIMARY KEY,                         -- UUID
    user_uuid                TEXT    NOT NULL,                             -- subscriber
    source_kg_id             TEXT    NOT NULL,                             -- publisher's kg_metadata.kg_id (immutable across republish)
    source_marketplace_id    TEXT    NOT NULL,                             -- marketplace_knowledge_graphs.id
    target_profile_id        TEXT    NOT NULL,                             -- subscriber's profile that hosts the local copy
    local_kg_id              TEXT    NOT NULL,                             -- subscriber's kg_metadata.kg_id (newly minted on subscribe)
    enabled                  INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    subscribed_at            TEXT    NOT NULL,                             -- ISO timestamp
    last_synced_at           TEXT    NOT NULL,                             -- bumped on every (re)sync
    last_synced_export_hash  TEXT,                                         -- sha256 of publisher's kg_export.json at last successful sync
    last_sync_error          TEXT,                                         -- non-NULL when most recent sync failed
    UNIQUE(user_uuid, source_kg_id, target_profile_id)
);

-- Lookup the user's own subscriptions (one-to-many) and during cleanup
CREATE INDEX IF NOT EXISTS idx_kg_subs_user
    ON kg_subscriptions(user_uuid);

-- Lookup all subscribers of a marketplace listing (auto-sync fanout, unpublish cascade)
CREATE INDEX IF NOT EXISTS idx_kg_subs_source_marketplace
    ON kg_subscriptions(source_marketplace_id);

-- Lookup the subscription record for a specific local KG (graph_store read-only gate)
CREATE INDEX IF NOT EXISTS idx_kg_subs_local_kg
    ON kg_subscriptions(local_kg_id);

-- ------------------------------------------------------------
-- Stamps on existing tables
--
-- kg_metadata gains is_subscribed + source_subscription_id so the
-- graph_store can short-circuit writes for read-only subscribed KGs
-- without having to JOIN kg_subscriptions on every operation.
-- ------------------------------------------------------------
-- Note: ALTER ... ADD COLUMN is run defensively (table may not exist
-- on a fresh install if no KGs were ever created).  Both columns are
-- nullable / default 0 so the change is purely additive.
ALTER TABLE kg_metadata ADD COLUMN is_subscribed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE kg_metadata ADD COLUMN source_subscription_id TEXT;

-- ------------------------------------------------------------
-- Marketplace listing extensions
--
-- subscriber_count: cached counter incremented/decremented on
-- subscribe/unsubscribe/cleanup, surfaced as a popularity signal
-- alongside install_count.
--
-- export_content_hash: sha256 of the kg_export.json file written
-- to marketplace_data/<id>/.  Recomputed on every (re)publish.
-- Subscribers compare this to their last_synced_export_hash to
-- decide whether a sync is needed (fast no-op path).
-- ------------------------------------------------------------
ALTER TABLE marketplace_knowledge_graphs ADD COLUMN subscriber_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE marketplace_knowledge_graphs ADD COLUMN export_content_hash TEXT;

-- P0 of docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md: which marketplace produced the copy.
-- kg = a KG listing (marketplace_knowledge_graphs), agent_pack = an agent-pack listing
-- (marketplace_agent_packs). The two fan-outs filter on it and never touch each other's rows.
-- NB no semicolon inside a comment here: the loader splits this file on semicolons.
ALTER TABLE kg_subscriptions ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'kg';
