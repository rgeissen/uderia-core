-- Dashboard Canvas — persistent live dashboards composed from conversation visuals.
-- Schema v1.0 — 2026-05-17
--
-- Two tables:
--   dashboards      — one row per dashboard (owned by a user)
--   dashboard_tiles — one row per tile inside a dashboard

-- ── Dashboards ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dashboards (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT 'My Dashboard',
    description     TEXT NOT NULL DEFAULT '',
    is_default      INTEGER NOT NULL DEFAULT 0,     -- 1 = opened automatically on login
    auto_refresh_s  INTEGER NOT NULL DEFAULT 0,     -- 0 = manual; >0 = global interval in seconds
    theme           TEXT NOT NULL DEFAULT 'auto',   -- 'auto' | 'dark' | 'light'
    layout_config   TEXT NOT NULL DEFAULT '{}',     -- JSON: reserved for future custom grid settings
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── Dashboard Tiles ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dashboard_tiles (
    id                TEXT PRIMARY KEY,
    dashboard_id      TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL,

    -- Presentation
    title             TEXT NOT NULL DEFAULT 'Untitled',
    component_id      TEXT NOT NULL DEFAULT 'chart',   -- 'chart' | 'table' | 'kpi' | 'narrative' | 'knowledge_graph' | 'flow'

    -- Render data (from ComponentRenderPayload)
    spec              TEXT NOT NULL DEFAULT '{}',      -- JSON: G2Plot spec or KPI/narrative spec
    html              TEXT NOT NULL DEFAULT '',        -- Pre-rendered HTML (tables, KPIs)

    -- Re-execution context (enables refresh)
    source_prompt     TEXT NOT NULL DEFAULT '',        -- Original query prompt
    source_session_id TEXT NOT NULL DEFAULT '',        -- Session used to generate this tile
    profile_id        TEXT NOT NULL DEFAULT '',        -- Profile used for execution
    profile_tag       TEXT NOT NULL DEFAULT '',        -- Human-readable profile tag (e.g. OPTIM)

    -- Grid layout (12-column CSS Grid)
    pos_col           INTEGER NOT NULL DEFAULT 0,      -- Grid column-start (0-indexed)
    pos_row           INTEGER NOT NULL DEFAULT 0,      -- Grid row-start (0-indexed)
    tile_width        INTEGER NOT NULL DEFAULT 6,      -- Column span (1–12)
    tile_height       INTEGER NOT NULL DEFAULT 4,      -- Row span (min 1)

    -- MCP-replayable refresh (set when a single data tool produced this tile)
    mcp_tool_name     TEXT DEFAULT NULL,               -- e.g. 'base_readQuery'
    mcp_args_json     TEXT DEFAULT NULL,               -- JSON-encoded tool arguments

    -- Ordering and refresh state
    sort_order        INTEGER NOT NULL DEFAULT 0,
    last_refreshed    TEXT DEFAULT NULL,               -- ISO timestamp of last successful refresh
    refresh_status    TEXT NOT NULL DEFAULT 'ok',      -- 'ok' | 'pending' | 'refreshing' | 'error'

    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_dashboards_user       ON dashboards(user_id);
CREATE INDEX IF NOT EXISTS idx_dashboards_default    ON dashboards(user_id, is_default);
CREATE INDEX IF NOT EXISTS idx_dash_tiles_dashboard  ON dashboard_tiles(dashboard_id);
CREATE INDEX IF NOT EXISTS idx_dash_tiles_user       ON dashboard_tiles(user_id);
CREATE INDEX IF NOT EXISTS idx_dash_tiles_order      ON dashboard_tiles(dashboard_id, sort_order);
