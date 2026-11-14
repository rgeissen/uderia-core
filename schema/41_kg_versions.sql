-- 41_kg_versions.sql
-- Knowledge Graph version history — follows the same design pattern as
-- flow_versions (schema 39) and canvas_versions (schema 40): a per-artifact
-- *_versions table in tda_auth.db with version_id / <id> / user_id / payload /
-- version_num / label / created_at, dedup against the latest, and a shared
-- retention cap (core/versioning.py).
--
-- KG-specific resource efficiency: the payload is the FULL graph (entities +
-- relationships) serialized to JSON and gzip+base64 compressed (graphs compress
-- ~10x). Snapshots are taken coarsely (explicit request + before destructive
-- rebuild/clear/restore ops), never per entity add. A content_hash drives dedup
-- without re-reading the heavy blob.
--
-- Applied via kg_version_db._ensure_schema() (self-applying, mirrors flow/canvas).

CREATE TABLE IF NOT EXISTS kg_versions (
    version_id   TEXT PRIMARY KEY,
    kg_id        TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    payload      TEXT NOT NULL,          -- gzip+base64 JSON {entities, relationships}
    compressed   INTEGER DEFAULT 1,
    content_hash TEXT NOT NULL,          -- sha256 of the uncompressed JSON, for dedup
    entity_count INTEGER DEFAULT 0,
    rel_count    INTEGER DEFAULT 0,
    version_num  INTEGER NOT NULL,
    label        TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_kg_versions_kg_id   ON kg_versions(kg_id);
CREATE INDEX IF NOT EXISTS idx_kg_versions_user_id ON kg_versions(user_id);
