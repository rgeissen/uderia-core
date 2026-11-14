-- Schema 38: KG Backend Settings
-- Mirrors 24_vectorstore_settings.sql
-- Provides tier-level governance for the KG backend abstraction layer.
--
-- Two-level governance:
--   1. Feature gate  — is the KG feature enabled for this tier?
--   2. Backend allowlist — which backend types can this tier use?
--
-- Admin tier always bypasses both gates (enforced in settings.py, not here).

CREATE TABLE IF NOT EXISTS kg_backend_settings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key   TEXT    NOT NULL UNIQUE,
    setting_value TEXT    NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by    TEXT
);

-- Seed defaults (all tiers enabled, SQLite available to all, Neo4j to developer+)
INSERT OR IGNORE INTO kg_backend_settings (setting_key, setting_value) VALUES
    ('kg_enabled_user',           'true'),
    ('kg_enabled_developer',      'true'),
    ('allowed_backends_user',      '["sqlite"]'),
    ('allowed_backends_developer', '["sqlite", "neo4j"]');
