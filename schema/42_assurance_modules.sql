-- ═══════════════════════════════════════════════════════════════════════════
-- PROP-003 · The assurance-module plane
--
-- Three tables, each self-applying at first use by the module that owns it
-- (the house pattern — see core/trust/store.py and
-- api/component_instance_marketplace_routes.py:_ensure_schema). This file is
-- the readable record; no deployment step depends on it.
--
--   assurance_bindings         which module assesses which property, in which
--                              lane. Owned by agent/assurance/store.py.
--   assurance_module_settings  the plane's governance switches. Owned by
--                              assurance_modules/settings.py.
--   installed_assurance_modules  what is on disk, mirrored for the admin UI.
-- ═══════════════════════════════════════════════════════════════════════════

-- The DEPLOYMENT'S EDITS to the shipped binding table.
--
-- Three states, and collapsing any two loses a real distinction:
--   no row                  → the shipped default applies
--   row, assessor_key SET   → the operator rebound this lane
--   row, assessor_key NULL  → the operator chose NOT ASSESSED (an audited act)
--
-- `profile_id` is nullable and v1 always writes NULL (deployment-wide). The
-- column exists from day one so per-profile overlays stay an additive change
-- rather than a migration of live trust configuration.
CREATE TABLE IF NOT EXISTS assurance_bindings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property     TEXT NOT NULL,
    lane         TEXT NOT NULL,   -- IN_FLIGHT | IN_FLIGHT_TRIGGERED | POST_HOC
    assessor_key TEXT,            -- NULL = explicit unbind, distinct from "no row"
    profile_id   TEXT,            -- NULL = deployment-wide
    bound_by     TEXT,            -- user uuid
    bound_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    note         TEXT,
    UNIQUE(property, lane, profile_id)
);

-- Governance. `third_party_assurance_enabled` ships FALSE: a deployment that
-- does nothing runs exactly what it ran before.
--
-- The disable list is authoritative REGARDLESS of any mode — deliberately the
-- component-plane semantics, not the extension plane's, which short-circuits
-- before consulting its list.
CREATE TABLE IF NOT EXISTS assurance_module_settings (
    id            INTEGER PRIMARY KEY,
    setting_key   TEXT NOT NULL UNIQUE,
    setting_value TEXT NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by    TEXT
);

INSERT OR IGNORE INTO assurance_module_settings (setting_key, setting_value) VALUES
    ('third_party_assurance_enabled', 'false'),
    ('assurance_module_marketplace_enabled', 'false'),
    ('disabled_assurance_modules', '[]');

-- What is installed, mirrored from disk for the admin surface.
CREATE TABLE IF NOT EXISTS installed_assurance_modules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id       TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    version       TEXT,
    author        TEXT,
    source        TEXT NOT NULL DEFAULT 'user',  -- builtin | user | marketplace
    is_active     INTEGER NOT NULL DEFAULT 1,
    manifest_json TEXT,
    content_hash  TEXT,          -- what the trust attestation is bound to
    installed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_assurance_bindings_prop
    ON assurance_bindings(property, lane);
