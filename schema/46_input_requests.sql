-- Schema 46: Needs input — a turn that stops to ask (PROP-011)
--
-- One record per question an agent put to a person. A turn that raised one ends
-- `input_required` (interrupted, not terminal — core/turn_states.py); the person's answer
-- starts the next turn of the same session bound to the question (re-entry), and the record
-- then says who answered, when, through which channel. Approvals (parked writes, withheld
-- answers) are NOT migrated here: they stay review_items; the Inbox reads both.
--
-- Applied by core/input_request_store.py on connect (CREATE IF NOT EXISTS), the review-queue
-- idiom — nothing else reads this file.

CREATE TABLE IF NOT EXISTS input_requests (
    id              TEXT PRIMARY KEY,                 -- "ir-…"
    user_uuid       TEXT NOT NULL,                    -- the person the question is for
    session_id      TEXT,
    turn            INTEGER,
    task_id         TEXT,                             -- the REST/background task, when there is one
    origin          TEXT,                             -- interactive · rest · mcp · scheduler · flow · ask · coordinate
    raised_by       TEXT,                             -- code · model · data_source · child
    profile_id      TEXT,
    profile_tag     TEXT,
    source          TEXT,                             -- op id · tool name · playbook anchor · MCP server
    parent_id       TEXT,                             -- the chain (a relayed question)
    kind            TEXT NOT NULL,                    -- choose · provide · confirm · approve · auth
    items_json      TEXT NOT NULL,                    -- 1–4 items: {header, question, options, multi_select, other_allowed, schema}
    why             TEXT,
    default_json    TEXT,                             -- only when the owner's input policy declares one
    digest          TEXT NOT NULL,                    -- sha256 of the canonical question; the resume binding carries it
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending · accepted · declined · cancelled · expired · superseded
    answer_json     TEXT,
    answered_by     TEXT,                             -- the person (never an agent) · policy(never) · relay(quote)
    answered_via    TEXT,                             -- chat · rest · mcp · inbox
    answered_at     TEXT,
    superseded_by   TEXT,                             -- the request or turn that superseded it
    expires_at      TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_input_requests_user_status ON input_requests (user_uuid, status);
CREATE INDEX IF NOT EXISTS idx_input_requests_session ON input_requests (session_id, status);
CREATE INDEX IF NOT EXISTS idx_input_requests_task ON input_requests (task_id);
