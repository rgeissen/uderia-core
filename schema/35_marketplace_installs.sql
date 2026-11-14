-- schema/35_marketplace_installs.sql
-- Tracks marketplace installs per user to enforce "must install before rating".
--
-- Covered artifact types:
--   agent_pack  → marketplace_agent_packs.id
--   skill       → marketplace_skills.id
--   extension   → marketplace_extensions.id
--   knowledge_graph → marketplace_knowledge_graphs.id
--
-- Unique constraint prevents double-tracking; ON CONFLICT IGNORE makes
-- repeated installs idempotent.

CREATE TABLE IF NOT EXISTS marketplace_installs (
    id              TEXT PRIMARY KEY,
    artifact_type   TEXT NOT NULL,          -- 'agent_pack' | 'skill' | 'extension' | 'knowledge_graph'
    marketplace_id  TEXT NOT NULL,          -- FK to the relevant marketplace_* table
    user_id         TEXT NOT NULL,          -- installer's user UUID
    installed_at    TEXT NOT NULL           -- ISO-8601 UTC timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_installs_unique
    ON marketplace_installs (artifact_type, marketplace_id, user_id);
