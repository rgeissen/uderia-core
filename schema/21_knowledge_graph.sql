-- ============================================================
-- Schema: Knowledge Graph
-- Version: 2.0 (regenerated 2026-08-24 from the live shape)
-- Description: Entity-relationship graph for context enrichment.
--              Scoped per kg_id (one graph = one kg_id; a profile binds a graph
--              through kg_profile_assignments). UNIQUE(kg_id, name, entity_type).
--              The pre-kg_id shape (UNIQUE(profile_id, user_uuid, ...)) that this
--              file used to declare is migrated in auth/database.py; on a fresh
--              install this file creates the final shape directly.
-- ============================================================

CREATE TABLE IF NOT EXISTS kg_entities (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                kg_id         TEXT NOT NULL DEFAULT '',
                profile_id    TEXT NOT NULL,
                user_uuid     TEXT NOT NULL,
                name          TEXT NOT NULL,
                entity_type   TEXT NOT NULL,
                properties_json TEXT DEFAULT '{}',
                source        TEXT NOT NULL DEFAULT 'manual',
                source_detail TEXT,
                created_at    TEXT,
                updated_at    TEXT,
                UNIQUE(kg_id, name, entity_type)
            );

CREATE TABLE IF NOT EXISTS kg_relationships (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                kg_id                TEXT NOT NULL DEFAULT '',
                profile_id           TEXT NOT NULL,
                user_uuid            TEXT NOT NULL,
                source_entity_id     INTEGER NOT NULL,
                target_entity_id     INTEGER NOT NULL,
                relationship_type    TEXT NOT NULL,
                cardinality          TEXT,
                metadata_json        TEXT DEFAULT '{}',
                source               TEXT NOT NULL DEFAULT 'manual',
                created_at           TEXT,
                FOREIGN KEY (source_entity_id) REFERENCES kg_entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_entity_id) REFERENCES kg_entities(id) ON DELETE CASCADE,
                UNIQUE(kg_id, source_entity_id, target_entity_id, relationship_type)
            );

CREATE INDEX IF NOT EXISTS idx_kg_entities_kg_id ON kg_entities(kg_id);

CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(kg_id, name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_kg_entities_profile_user ON kg_entities(profile_id, user_uuid);

CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(kg_id, entity_type);

CREATE INDEX IF NOT EXISTS idx_kg_relationships_kg_id ON kg_relationships(kg_id);

CREATE INDEX IF NOT EXISTS idx_kg_relationships_profile_user ON kg_relationships(profile_id, user_uuid);

CREATE INDEX IF NOT EXISTS idx_kg_relationships_source ON kg_relationships(source_entity_id);

CREATE INDEX IF NOT EXISTS idx_kg_relationships_target ON kg_relationships(target_entity_id);

CREATE INDEX IF NOT EXISTS idx_kg_relationships_type ON kg_relationships(kg_id, relationship_type);
