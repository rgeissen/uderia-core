-- ═══════════════════════════════════════════════════════════════════════════
-- PROP-003 · Phase 3 — the assurance-module marketplace
--
-- Mirrors marketplace_extensions (schema/14) column for column, because the
-- publish/install contract is the same one: only user-created packs publish,
-- the blob's hash is recorded and re-checked at install, visibility is public
-- or targeted (through the shared marketplace_sharing_grants), and rating is
-- gated behind a real install (shared marketplace_installs).
--
-- What is NOT shared with that plane is the execution model: an installed pack
-- still cannot be BOUND until it is signed here, so a marketplace install
-- grants availability, never authority.
--
-- Self-applying at first use by api/assurance_module_marketplace_routes.py.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS marketplace_assurance_modules (
    id                VARCHAR(36) PRIMARY KEY,
    pack_id           TEXT NOT NULL,
    name              TEXT NOT NULL,
    description       TEXT,
    version           TEXT,
    author            TEXT,
    tiers             TEXT,          -- JSON array: which tiers the pack's assessors use
    property_count    INTEGER DEFAULT 0,
    assessor_count    INTEGER DEFAULT 0,
    publisher_user_id TEXT,
    visibility        TEXT NOT NULL DEFAULT 'public',   -- 'public' | 'targeted'
    manifest_json     TEXT,          -- full manifest for preview (+ the "trust" envelope)
    source_hash       TEXT,          -- sha256 of the pack payload; re-checked on install
    download_count    INTEGER DEFAULT 0,
    install_count     INTEGER DEFAULT 0,
    published_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assurance_module_ratings (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    module_marketplace_id    VARCHAR(36) NOT NULL
        REFERENCES marketplace_assurance_modules(id) ON DELETE CASCADE,
    user_id                  TEXT NOT NULL,
    rating                   INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment                  TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(module_marketplace_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_mkt_assurance_publisher
    ON marketplace_assurance_modules(publisher_user_id);
CREATE INDEX IF NOT EXISTS idx_mkt_assurance_visibility
    ON marketplace_assurance_modules(visibility);
