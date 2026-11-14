-- Schema 45: Files and file stores (PROP-010)
--
-- Where the files a session produced — kept answers, tables, charts, exports, and every
-- chat attachment — are stored, and the governance over them. The BYTES never live here:
-- each file is an opaque object in an admin-configured store (platform local by default),
-- encrypted with its own data key. This database holds the metadata, the wrapped key, the
-- keyed content fingerprint and the provenance.
--
-- Ownership, placement and provenance are three bindings (PROP-010 §4.1):
--   owned by the user (user_uuid) · placed by the agent of the turn (store_config_id,
--   resolved from the agent's fileStoreConfigId) · traced to the turn (session_id, turn_id,
--   bom_json copied at save time).

-- Admin-configured stores. The built-in "platform-local" store is not a row: it always
-- exists (the resolution chain's last step) and cannot be deleted.
CREATE TABLE IF NOT EXISTS file_store_configs (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    backend_type      TEXT NOT NULL,              -- local · s3 (later: azure_blob · gcs)
    config_encrypted  TEXT,                       -- backend settings incl. credentials, Fernet
    encrypt           INTEGER NOT NULL DEFAULT 1, -- envelope encryption of every object
    is_default        INTEGER NOT NULL DEFAULT 0,
    capacity_mb       INTEGER,                    -- what the admin provisions for Files here; NULL = no limit
    last_test_ok      INTEGER,
    last_test_at      TIMESTAMP,
    last_test_message TEXT,
    created_by        TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Governance, key/value like kg_backend_settings. Defaults live in filestore/settings.py;
-- a missing row reads as its default.
CREATE TABLE IF NOT EXISTS file_store_settings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key   TEXT    NOT NULL UNIQUE,
    setting_value TEXT    NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by    TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id               TEXT PRIMARY KEY,
    user_uuid        TEXT NOT NULL,
    store_config_id  TEXT NOT NULL,            -- the store it was WRITTEN to (never re-resolved)
    object_key       TEXT NOT NULL,            -- opaque: <user>/<uuid>; no filename reaches a store
    filename         TEXT NOT NULL,
    media_type       TEXT,
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    content_hmac     TEXT,                     -- keyed fingerprint of the plaintext
    wrapped_key      TEXT,                     -- the data key, wrapped; NULL when the store does not encrypt
    key_version      INTEGER,
    source_kind      TEXT NOT NULL,            -- upload · answer · table · chart · component · export · snapshot · flow · agent
    source_anchor    TEXT,                     -- the answer row it was kept from (drives "Kept · Open")
    pair_id          TEXT,                     -- a chart's PNG and its CSV share one
    lifetime         TEXT NOT NULL DEFAULT 'kept',   -- conversation · kept
    kept_at          TIMESTAMP,
    origin_kind      TEXT NOT NULL DEFAULT 'turn',   -- turn · scheduled_run · flow_run · upload · export
    scheduled_task_id TEXT,
    scheduled_run_id  TEXT,
    flow_id          TEXT,
    flow_run_id      TEXT,
    flow_node_id     TEXT,
    component_id     TEXT,                     -- the component a file was saved from (dashboard, knowledge_graph, …)
    instance_id      TEXT,                     -- that component's instance (a dashboard id, a KG id, a canvas id)
    source_json      TEXT,                     -- what the source recorded about itself (a snapshot's tiles and their refresh times)
    profile_id       TEXT,
    profile_tag      TEXT,
    profile_type     TEXT,
    session_id       TEXT,
    session_title    TEXT,
    turn_id          INTEGER,
    question         TEXT,
    bom_hash         TEXT,
    bom_json         TEXT,
    data_derived     INTEGER NOT NULL DEFAULT 0,
    seen_at          TIMESTAMP,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at       TIMESTAMP,
    purge_after      TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_files_user_created ON files (user_uuid, created_at);
CREATE INDEX IF NOT EXISTS idx_files_user_session ON files (user_uuid, session_id);
CREATE INDEX IF NOT EXISTS idx_files_store ON files (store_config_id);
CREATE INDEX IF NOT EXISTS idx_files_purge ON files (purge_after);

-- Every turn that read a file: the provenance card's "Used in", and the lineage from an
-- answer back to a file.
CREATE TABLE IF NOT EXISTS file_uses (
    file_id    TEXT NOT NULL,
    user_uuid  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id    INTEGER,
    used_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (file_id, session_id, turn_id)
);
