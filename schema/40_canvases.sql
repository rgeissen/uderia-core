-- 40_canvases.sql
-- Canvas persistence (Option A): promote the Canvas component from an ephemeral,
-- message-embedded artifact to a first-class, addressable, versioned entity.
--
-- Before this, a canvas's content lived only inside the `data-spec` blob of the
-- assistant message that created it (in one session's conversation.json), and
-- version history was an in-memory, title-keyed JS Map that died on page reload.
--
-- These tables give each canvas a stable canvas_id (user-scoped, so it can be
-- opened from any session) plus durable version history that survives reloads.
--
-- Applied via canvas_db._ensure_schema() (self-applying, mirrors flow_db).

CREATE TABLE IF NOT EXISTS canvases (
    canvas_id    TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    title        TEXT NOT NULL,
    -- normalized title (lower/trim) used as the natural upsert key, mirroring the
    -- LLM's existing "same title to update" behavior. Unique per user.
    title_key    TEXT NOT NULL,
    content      TEXT NOT NULL,
    language     TEXT,
    sources      TEXT,
    -- originating session (provenance only; resolution is by canvas_id/title, not session)
    session_id   TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_canvases_user_id   ON canvases(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_canvases_user_title ON canvases(user_id, title_key);

CREATE TABLE IF NOT EXISTS canvas_versions (
    version_id   TEXT PRIMARY KEY,
    canvas_id    TEXT NOT NULL REFERENCES canvases(canvas_id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    content      TEXT NOT NULL,
    language     TEXT,
    version_num  INTEGER NOT NULL,
    label        TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_canvas_versions_canvas_id ON canvas_versions(canvas_id);
CREATE INDEX IF NOT EXISTS idx_canvas_versions_user_id   ON canvas_versions(user_id);
