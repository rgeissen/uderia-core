-- Flow System (Phase 1)
-- Nested, composable workflow engine with bidirectional conversation+canvas creation.
-- Flows are directed graphs that MAY contain cycles (back-edges).
-- Cycle scopes define bounded iteration segments at any graph level.
-- Execution runs through the identical execute_query() pipeline as user queries.

CREATE TABLE IF NOT EXISTS flows (
    flow_id      TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    definition   TEXT NOT NULL,          -- JSON: {nodes[], edges[], scopes[], global_params{}}
    is_template  INTEGER DEFAULT 0,      -- 1 = reusable template
    is_reusable  INTEGER DEFAULT 0,      -- 1 = published to template library
    version      TEXT DEFAULT '1.0.0',
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flow_runs (
    run_id          TEXT PRIMARY KEY,
    flow_id         TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL,
    status          TEXT DEFAULT 'queued',  -- queued | running | complete | error | cancelled
    input_params    TEXT,                   -- JSON: runtime parameter overrides
    output_result   TEXT,                   -- JSON: final output state
    execution_trace TEXT,                   -- JSON: ordered list of node execution events
    tokens_input    INTEGER DEFAULT 0,
    tokens_output   INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flow_node_runs (
    id           INTEGER PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES flow_runs(run_id) ON DELETE CASCADE,
    node_id      TEXT NOT NULL,
    node_type    TEXT,                    -- task_llm | task_function | decision | merge | start | end
    scope_id     TEXT,                   -- cycle scope this node belongs to (NULL = top-level)
    iteration    INTEGER DEFAULT 0,      -- cycle iteration index (0 for non-cyclic nodes)
    status       TEXT DEFAULT 'pending', -- pending | running | success | error | skipped
    input_state  TEXT,                   -- JSON: state snapshot entering this node
    output_state TEXT,                   -- JSON: state produced by this node
    error_msg    TEXT,
    task_id      TEXT,                   -- links to REST background task (LLM nodes)
    session_id   TEXT,                   -- session used for LLM call
    tokens_input  INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    started_at   TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_flows_user_id           ON flows(user_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_flow_id       ON flow_runs(flow_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_user_id       ON flow_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_flow_runs_status        ON flow_runs(status);
CREATE INDEX IF NOT EXISTS idx_flow_node_runs_run_id   ON flow_node_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_flow_node_runs_node_id  ON flow_node_runs(node_id);
