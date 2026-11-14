-- ============================================================================
-- 41_marketplace_component_instances.sql
-- ----------------------------------------------------------------------------
-- Marketplace for COMPONENT INSTANCES (a specific flow, a specific dashboard —
-- anything from a component that declares supports_instance_io). This is the
-- marketplace layer on top of the instance-portability contract:
--   publish  = export_instance()  -> a listing (envelope_json + content_hash)
--   fork     = import_instance(mode="create", mapping=...)
--   (Phase B) subscribe + sync = import_instance(mode="update", target_id=...)
--
-- Instances are DATA (flow definition / dashboard spec), never code — so unlike
-- the extension/component-definition marketplace there is no code-execution trust
-- concern. Cross-references (profiles) travel as tags and remap on fork via the
-- shared remap dialog. Generic over component_id so flow + dashboard (+ future
-- canvas) share one path. Mirrors marketplace_extensions / marketplace_knowledge_graphs.
-- ============================================================================

CREATE TABLE IF NOT EXISTS marketplace_component_instances (
    id                 VARCHAR(36) PRIMARY KEY,          -- marketplace listing UUID
    component_id       VARCHAR(100) NOT NULL,            -- 'flow' | 'dashboard' | ...
    instance_id        VARCHAR(100) NOT NULL,            -- source instance id at publish (republish lookup)
    name               VARCHAR(255) NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    author             VARCHAR(255) NOT NULL DEFAULT '',
    publisher_user_id  VARCHAR(36) NOT NULL,
    visibility         VARCHAR(20) NOT NULL DEFAULT 'public',  -- 'public' | 'targeted'
    envelope_json      TEXT NOT NULL,                    -- export_instance() output (portable data)
    content_hash       VARCHAR(64) NOT NULL DEFAULT '',  -- sha256 of portable content (Phase-B sync change-detection)
    download_count     INTEGER NOT NULL DEFAULT 0,
    install_count      INTEGER NOT NULL DEFAULT 0,
    subscriber_count   INTEGER NOT NULL DEFAULT 0,       -- Phase B
    published_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mci_publisher    ON marketplace_component_instances(publisher_user_id);
CREATE INDEX IF NOT EXISTS idx_mci_component     ON marketplace_component_instances(component_id);
CREATE INDEX IF NOT EXISTS idx_mci_visibility    ON marketplace_component_instances(visibility);
CREATE INDEX IF NOT EXISTS idx_mci_published_at  ON marketplace_component_instances(published_at);
-- One listing per (publisher, source instance) so republish updates in place.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mci_publisher_instance
    ON marketplace_component_instances(publisher_user_id, component_id, instance_id);


CREATE TABLE IF NOT EXISTS component_instance_ratings (
    id                       VARCHAR(36) PRIMARY KEY,
    instance_marketplace_id  VARCHAR(36) NOT NULL,
    user_id                  VARCHAR(36) NOT NULL,
    rating                   INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment                  TEXT,
    created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at               DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cir_unique
    ON component_instance_ratings(instance_marketplace_id, user_id);


-- Phase B: live-tracking subscriptions. A subscribe materializes a local copy
-- (import_instance mode=create + remap) and links it here; manual Sync
-- re-materializes IN PLACE (import_instance mode=update, target_id=local_instance_id)
-- using the mapping captured at subscribe time, when the publisher's content_hash
-- moves past synced_content_hash. Unsubscribe drops the link (keeps the local copy).
CREATE TABLE IF NOT EXISTS component_instance_subscriptions (
    id                   VARCHAR(36) PRIMARY KEY,
    marketplace_id       VARCHAR(36) NOT NULL,          -- -> marketplace_component_instances.id
    subscriber_user_id   VARCHAR(36) NOT NULL,
    component_id         VARCHAR(100) NOT NULL,
    local_instance_id    VARCHAR(100) NOT NULL,         -- the materialized flow_id / dashboard id
    synced_content_hash  VARCHAR(64) NOT NULL DEFAULT '',
    mapping_json         TEXT NOT NULL DEFAULT '{}',    -- profile remap captured at subscribe; reused on sync
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cis_unique
    ON component_instance_subscriptions(marketplace_id, subscriber_user_id);
CREATE INDEX IF NOT EXISTS idx_cis_subscriber
    ON component_instance_subscriptions(subscriber_user_id);
