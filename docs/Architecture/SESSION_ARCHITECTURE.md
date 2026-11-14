# Session Architecture

How a conversation is stored, serialised, streamed and kept apart: the session record and its
three histories, the storage and lock backends, archiving, the REST task model, the two SSE
channels, and the browser's per-session isolation. This is the live contract; scale history is
in [SCALE_ASSESSMENT.md](SCALE_ASSESSMENT.md) (WS1), and the per-turn assembly that reads the
histories is in [CONTEXT_WINDOW_ARCHITECTURE.md](CONTEXT_WINDOW_ARCHITECTURE.md).

**Last updated:** 2026-10-30

## 1. The contract

| # | Rule | Who owns it |
|---|---|---|
| 1 | **One record per session, one write seam.** All persistence goes through `session_manager.save_session` / `_save_session`, which stamps `last_updated`, writes through the active backend, upserts the summary index and emits `session_model_update`. Those side effects stay in the manager, so every backend behaves the same. | `core/session_manager.py` |
| 2 | **Every load-modify-save is serialised per session.** Writers use `_session_transaction(user_uuid, session_id)`, which holds the session's lock for the whole cycle, so a stale load can never overwrite another writer. | `_session_transaction` · `core/locks/` |
| 3 | **Three histories, three readers.** The LLM's history (`chat_object`), the UI's history (`session_history`) and the planner's turn record (`last_turn_data.workflow_history`) are written separately and never derived from each other. | `add_message_to_histories` · `update_last_turn_data` |
| 4 | **The turn record is the one chokepoint.** Every engine's turn lands in `update_last_turn_data`, which stamps the turn-level facts (authority, actions, bill of materials, cache, tool surface, budget …) once, with `setdefault`, so an engine's own stamp always wins. | `update_last_turn_data` |
| 5 | **Delete means archive.** A user delete or a cascading artifact delete marks the session archived with a reason; the body is kept. | `delete_session` · `archive_sessions_by_*` |
| 6 | **A session belongs to one user.** Storage, listing, task polling and reattach are keyed by the authenticated user's UUID. | the backends · `rest_routes` · `turn_stream` |
| 7 | **The browser renders only the session it is showing.** A stream for another session keeps running, but writes nothing to the page until the user returns. | `processStream` · `sessionUiCache` |

## 2. The session record

`create_session` writes the initial body (session id = `uuid4`, `core/utils.generate_session_id`):

| Field | Meaning |
|---|---|
| `id`, `user_uuid`, `name` (`"New Chat"`), `created_at`, `last_updated` | identity |
| `profile_id`, `profile_tag` | the agent the session was created with (later: the last one used) |
| `profile_tags_used[]`, `models_used[]` (`"provider/model"`) | every agent and model that served a turn, appended by `update_models_used`; `ensure_primary_profile_tag` moves a flow's orchestrating agent to the front |
| `dual_model_usage` | per planning phase (`strategic`, `tactical`, `tactical_fastpath`, `conversation`), the models used |
| `provider`, `model` | the last provider/model (backwards compatibility) |
| `input_tokens`, `output_tokens` | session token totals (`update_token_count`) |
| `chat_object`, `session_history`, `last_turn_data.workflow_history` | the three histories (§3) |
| `is_temporary`, `temporary_purpose` | utility sessions (hidden behind the sidebar's utility filter) |
| `genie_metadata` | `is_genie_slave`, `parent_session_id`, `slave_profile_id`, `slave_sequence_number`; `get_session` enriches a slave with `nesting_level` and `slave_profile_tag` from `genie_session_links` |
| `license_info`, `system_prompt_template`, `charting_intensity`, `full_context_sent` | creation-time settings |
| `archived`, `is_archived`, `archived_at`, `archived_reason` | set by archiving (§6) |

Session cost has one definition, `session_manager.session_cost_total` (the sum of `turn_cost` over
every turn in the record), used by both the per-turn snapshot and the session endpoints.

## 3. The three histories

| History | Written by | Entry shape | Read by |
|---|---|---|---|
| **`chat_object`** | `add_message_to_histories` (plain `content`) | `{role, content, isValid, turn_number}`; assistant entries of an Optimize turn may carry **`tool_context`**; a user entry with files carries `has_attachments` | the LLM: the `conversation_history` context module and the engines' history builder |
| **`session_history`** | `add_message_to_histories` (`html_content`, else `content`) | `{role, content, isValid, turn_number}` plus `source`, `origin`, `profile_tag`, `is_session_primer`, `attachments`, `extension_specs`, `skill_specs` | the UI's chat log |
| **`last_turn_data.workflow_history`** | `update_last_turn_data` (one dict per turn) | the turn: `turn`, `user_query`, `execution_trace`, `original_plan`, `final_summary_text`, tokens and cost, `status`, `isValid`, `profile_tag`/`profile_type`, `provider`/`model`, and every trust stamp | the planner (strategic history, plan hydration), Live Status reloads, the scorers, feedback |

- **`tool_context`** is built by `executor._build_tool_context_summary`: a
  `[Tool Execution Summary]` block listing each data tool call of the turn (name, key argument,
  row count, columns, one sample row; `TDA_*` tools skipped). It lets the next turn's history,
  and its RAG offload, know what data was already fetched.
- **Roles**: an assistant message is stored as role `model` when the session's provider is
  Google; every other provider keeps `assistant`.
- **Turn numbers**: a user message opens the next turn after the highest existing number (a
  regenerated sibling may sit mid-history), and an assistant message joins it. Regenerate and
  edit insert by lineage (`agent/turn_lineage.py`, S8). `get_session` back-fills missing turn
  numbers on old records and re-saves once.
- **Validity**: `isValid` marks what the LLM may still see. `toggle_turn_validity` flips one
  turn; `purge_session_memory` resets `chat_object` (to the Google primer pair for Google) and
  marks every UI message and workflow turn invalid, keeping the UI history and traces.
- **After every turn**, `_ingest_turn_to_session_store` (a background task) indexes the turn into
  the per-session vector store (`components/builtin/context_window/session_vector_store.py`:
  in-memory chromadb by default, an external backend if configured), so RAG offload
  condensation has warm data on the next turn.

## 4. Storage backends (`core/session_backends/`)

`SessionPersistenceBackend` (`base.py`) owns the body (`load`, `save`, `delete_record`,
`exists`) and the summary index (`init_index`, `ensure_index_ready`, `query_summaries`,
`upsert_summary`, `delete_summary`, `rebuild_summaries`, plus the slow-path `scan_*` fallbacks).
`factory.get_session_backend()` builds one singleton from the environment:

| `TDA_SESSION_BACKEND` | Backend | Layout |
|---|---|---|
| `file` (default) | `FileSessionBackend` | **one JSON file per session** at `tda_sessions/<user_uuid>/<session_id>.json` (root overridable with `TDA_SESSIONS_DIR`), written atomically (temp file + `os.replace`); summary index in `tda_sessions/session_index.db` (SQLite, opened through `core/sqlite_util`) |
| `postgres` | `PostgresSessionBackend` | table `uderia_sessions`: the body as JSONB plus typed summary columns, so the table is the index; upsert on `session_id`; DSN `TDA_SESSION_PG_DSN` (or a postgres `TDA_AUTH_DB_URL`); falls back to the file backend if it cannot start |

There are no per-session directories and no separate `conversation.json` / `workflow.json`
files: all three histories live in the one body. Attachments are stored by the file store
(PROP-010, [FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md)), and the LLM call log is the global
`logs/llm_conversation*.log`, not per session.

- **The index is a cache.** `upsert_summary` runs fire-and-forget after each save. At startup
  `ensure_session_index_ready` (called from `main.py`) rebuilds the file index when it is
  missing, empty or lacks the enrichment columns. When the index is unavailable, listing falls
  back to `scan_summaries`. The summary's `status` (`empty` · `success` · `partial` · `failed`)
  comes from the valid workflow turns (`file_backend._compute_session_status`, also used by the
  Postgres projection).
- **Parity**: the same session-access suite runs against both backends.

## 5. Locks (`core/locks/`)

`LockProvider.get_lock(key)` returns an async context manager; `session_manager._get_session_lock`
keys it by `session_id`. Selected once by `TDA_LOCK_BACKEND`:

| Backend | Behaviour |
|---|---|
| `inprocess` (default) | `InProcessLockProvider`: one `asyncio.Lock` per key, created race-free with `setdefault`; an LRU bounded at 4,096 that never evicts a held lock. Single process only. |
| `redis` | `RedisLockProvider` (`TDA_REDIS_URL`, default `redis://localhost:6379/0`): `SET key token NX PX` with a 30 s TTL and 50 ms polling, released by a Lua compare-and-delete; key prefix `tda:lock:`. Falls back to in-process if `redis` is not installed. |

A multi-server deployment needs both the Postgres backend and Redis locks: file storage and
in-process locks coordinate only inside one process.

## 6. Archiving

`delete_session(user_uuid, session_id, archived_reason=)` never removes the body. It sets
`archived`, `is_archived`, `archived_at` and `archived_reason`, saves through the backend,
upserts the index, purges the session's unkept attachments (`filestore.session_files.purge_session`),
destroys the per-session vector store, and cascades to every Genie child registered in
`genie_session_links` (`cleanup_genie_slave_sessions`). It is idempotent.

| Trigger | Function | Reason written |
|---|---|---|
| User deletes a session (`DELETE /api/session/<id>`) | `delete_session` | `User manually deleted session` |
| Profile deleted, or an agent pack uninstalled | `archive_sessions_by_profile` | `Profile '<id>' was deleted` |
| Collection deleted (or pack uninstall) | `archive_sessions_by_collection` | `Collection '<id>' was deleted` |
| MCP server deleted | `archive_sessions_by_mcp_server` | `MCP server '<id>' was deleted` |
| LLM configuration deleted | `archive_sessions_by_llm_config` | `LLM config '<id>' was deleted` |
| REST `PUT /v1/sessions/<id>/archive` | `delete_session` | the caller's `archived_reason` (default `Manually archived`) |

The `archive_sessions_by_*` functions find affected sessions by calling
`RelationshipAnalyzer.analyze_artifact_relationships` **in-process** (the same analysis the
`GET /v1/artifacts/<type>/<id>/relationships` endpoint serves to the UI's deletion warnings), then
archive each active session. A hard delete exists only as a backend primitive
(`delete_record`).

## 7. Per-user isolation

- **Storage**: the file backend writes under the user's directory (ids sanitised to
  `[A-Za-z0-9_-]`); the Postgres backend filters `load` by `user_uuid`.
- **`SESSIONS_FILTER_BY_USER`** (env `TDA_SESSIONS_FILTER_BY_USER`, default `true`) governs reads
  and listings. When it is off, the file backend's `load` searches every user's directory for the
  id, and the Postgres `load` drops the user filter. `GET /sessions?all_users=true` lists every
  user's sessions for a user with the `VIEW_ALL_SESSIONS` feature by passing
  `get_all_sessions(..., filter_by_user=False)` — the scope travels with that call. The global is
  never flipped: it is read by every concurrent request, list and single-session load alike, so
  turning it off around an `await` (as the route once did) unscoped the whole process for the
  call's duration. Gate: `test/test_session_scope_per_call.py`.
- **Requests**: the UI routes (`@require_auth`) and REST routes resolve the user from the JWT or
  access token (`_get_user_uuid_from_request`). A REST task records its owner, and
  `GET /v1/tasks/<id>` answers 403 to anyone else; the reattach endpoints answer 404 to a
  non-owner (`_reattach_owner`).
- **Notifications** are queued per user: `APP_STATE['notification_queues'][user_uuid]`.

## 8. Context modes

| Mode | How it is chosen | Effect |
|---|---|---|
| **Full Context** (default) | nothing held | the conversation history is part of every LLM call |
| **Turn Summaries** | hold `Alt` for one query (`state.isTempLastTurnMode`), `Shift+Alt` or a click on the composer's context chip to lock it (`state.isLastTurnModeLocked`) | the request carries `disabled_history: true` (also forced for a replay); the executor sets `disabled_history`, and the LLM handler and the executor's prompt assembly leave the `chat_object` history out |

A Coordinate turn under Turn Summaries therefore has no conversation history, which is one of the
conditions for the coordinator's single-expert pass-through
([COORDINATOR_ARCHITECTURE.md](COORDINATOR_ARCHITECTURE.md)).

## 9. Turns as tasks

Every turn, interactive or REST, runs as its own `asyncio` task through the same
`execution_service.run_agent_execution` pipeline, with a task id `task-<uuid4>`
(`generate_task_id`).

### 9.1 Interactive (`POST /ask_stream`)

- The route authenticates, checks consumption limits, resolves the session (an SSE `error` frame,
  "Session not found or invalid.", if it is not the user's), and **cancels the previous active
  task for the same user and session**
  (`APP_STATE['active_tasks']['<user>_<session>']`).
- It emits `task_start` (`{task_id}`, plus `reattach: true` and `stream: true` when those
  switches are on), then every event of the turn as an SSE frame, with keepalives.
- `POST /api/session/<id>/cancel_stream` sets the cancellation flag and cancels the task; the
  stream ends with `cancelled` (and a partial answer when one was delivered).
- **Reattach (S10)**: with `STEER_REATTACH` on (env `TDA_STEER_REATTACH`; off in a bare run, on in
  `docker-compose.yml`), `core/turn_stream.py` keeps every frame in a cursor-numbered log
  (`id: <seq>`), at most 200 tasks and 4,000 frames per task, kept 15 minutes after the turn ends.
  A dropped connection resumes from its cursor (`GET /v1/tasks/<id>/stream?since=`, or the
  JSON `GET /v1/tasks/<id>/events`), and a session opened mid-turn finds its running task with
  `GET /v1/sessions/<id>/active-task` and backfills from 0. Frames below the retained floor are
  reported as a gap, and the client reloads the session instead.

### 9.2 REST (`POST /v1/sessions/<id>/query`)

- Profile selection goes through the activation firewall (`_assert_profile_activated`, 403
  `profile_not_activated`; see [the REST API reference](../RestAPI/restAPI.md)). The task is
  registered in `APP_STATE['background_tasks']` with `status: "pending"`, `events[]`, `intermediate_data[]` (successful tool results) and `result`.
- Status moves `pending` → `processing` → **`complete`** | `error` | `cancelled`. The final status
  is decided by `rest_routes._task_outcome`: a turn that produced a payload is `complete` whatever
  it recovered from; no payload plus an `error` event is `error` with the engine's message; an
  exception is `error`; cancellation is `cancelled`.
- Clients poll `GET /v1/tasks/<id>` (the status word is `complete`, not `completed`) and can
  `POST /v1/tasks/<id>/cancel`.
- Every event is also forwarded to the owner's open pages as `rest_task_update`, and the end as
  `rest_task_complete` (with `source`, e.g. `scheduler`), so a REST or scheduled turn renders live
  in the UI.

The task registry is process memory: it is not persisted, and a restart forgets it.

### A turn that stops to ask (PROP-011)

A turn that cannot go on without the person ends `input_required` (or `auth_required`) — an
**interrupted** state, not a terminal one: the record stays open, the session's pending question
is served on load (`pending_input_request`, with the owner's resume binding) beside every
question of the conversation in its live state (`input_requests` by id), and the next turn either
answers it (`input_answer` on `/ask_stream` or `/v1/sessions/<id>/query`) or supersedes it. Every
poller asks `core/turn_states.is_settled` — never its own list of strings. A REST client that
declares `X-Uderia-Accepts: input_required` sees the state; one that does not sees `complete` with
`input_required: true`. Contract: [NEEDS_INPUT_ARCHITECTURE.md](NEEDS_INPUT_ARCHITECTURE.md).

## 10. The two SSE channels

| Channel | Transport | Scope | Session guard |
|---|---|---|---|
| `/ask_stream` | `fetch()` body per request (`eventHandlers.processStream`) | one turn | `processStream` skips every frame while `state.currentSessionId !== originSessionId` |
| `/api/notifications/subscribe` | one `EventSource` per page (`notifications.subscribeToNotifications`) | every session of the user | per event type, below |

The notification route takes its user from a one-minute signed ticket (`POST /api/notifications/ticket`,
issued only with the caller's bearer token, because `EventSource` cannot send one; the ticket is
never accepted as a bearer itself — `auth/security.create_stream_ticket` / `verify_stream_ticket`)
and keeps one queue per open page, with a heartbeat comment every 20 s and the active maintenance banner replayed to a late
subscriber.

**A stream ends when the server stops.** A notification stream never finishes on its own, and
hypercorn's graceful stop waits for open responses — so an open tab used to hold every restart
until it was forced. The server's shutdown trigger (`main._stop_requested`, on SIGINT/SIGTERM)
first puts a close sentinel on every open queue (`main.close_live_streams`), each stream's
generator returns on it, and only then does the graceful stop run (measured: 1.3 s with one open
tab, 1.6 s with three; before, still running after 15 s). The stream re-raises a cancellation
rather than swallowing it. The page reconnects with its usual backoff. Gate: `test_server_stop.py`.

Notification guards (`notifications.js`):

- `status_indicator_update` (the MCP and LLM dots) and `rag_retrieval` (the CCR blink) are
  **always** processed: they are system-wide indicators.
- Genie events, knowledge and KG enrichment events, and the trust notices (`tool_surface`, judge
  steps …) are applied only when `payload.session_id` matches the viewed session; a knowledge
  event without a `session_id` is dropped while more than one stream is active.
- `rest_task_update` events are buffered per session in `state.restEventBuffer` (the session is
  tracked in `state.activeRestSessions`) and replayed into Live Status when the user opens that
  session mid-run; `rest_task_complete` clears the tracking.
- `session_model_update` (emitted by `_save_session`, de-duplicated per session on
  provider/model/profile tags) refreshes the sidebar badges.

**Reconnect.** The browser retries a dropped `EventSource` by itself, but an error response (a
proxy's 502 during a restart) closes it for good. Then `notifications.js` opens a new one with a
backoff of 2 s doubling to 30 s (`SSE_RETRY_MIN_MS`, `SSE_RETRY_MAX_MS`), immediately on the
browser's `online` event, and never when signed out. The SSE dot reads `reconnecting`
meanwhile, and the status strip's verdict (`handlers/statusSummary.js`) reads **Reconnecting**
(not Working). Before the first report the dot is `pending`: **Signed out** without a session,
**Connecting** with one. **Offline** means the stream itself failed.

## 11. Browser session isolation

State (`static/js/state.js`): `activeStreamSessions` (sessions with a running `processStream`),
`sessionUiCache`, `activeRestSessions`, `restEventBuffer`.

- **Switching away from a running session** (`sessionManagement.handleLoadSession`): when the
  session is in `activeStreamSessions` or `activeRestSessions`, the page is snapshotted into
  `sessionUiCache[id]`: `chatHTML`, `statusHTML`, `statusTurn`, `headerState` (title, task id,
  prompt and model, thinking indicator, knowledge banner, token counters), `executionState`
  (`currentStatusId`, the mode flags, provider/model, profile, pending events) and `handlerState`
  (the `genieHandler` and `conversationAgentHandler` module state).
- **Switching back while it still runs**: the snapshot is restored in full and `processStream`
  resumes rendering new frames.
- **A stream that ends while another session is shown**: its `finally` removes the session from
  `activeStreamSessions` and deletes its cache without touching page state (which belongs to the
  viewed session); the next visit loads from the server.
- **Normal load path** (no running stream): the session is fetched from `GET /session/<id>`, the
  chat log rendered, Live Status reset (title, banner, mode flags, `cleanupCoordination`,
  `cleanupExecution`), and then:
  - with a live REST buffer, its events are replayed;
  - otherwise the **last turn auto-loads**: the last `.clickable-avatar[data-turn-id]` is passed to
    `handleReloadPlanClick` (fire-and-forget), which fills Live Status from the turn's stored
    execution data;
  - the canvas, KG, flow, dashboard and Files panels are restored for the session;
  - `reattachRunningTurn` looks for a turn still running in the session (S10) and backfills it
    silently.

## 12. Files and gates

| File | Role |
|---|---|
| `src/trusted_data_agent/core/session_manager.py` | the record, the histories, the turn chokepoint, archiving, Genie links, `session_cost_total` |
| `src/trusted_data_agent/core/session_backends/` | `base.py` (contract), `factory.py`, `file_backend.py`, `postgres_backend.py` |
| `src/trusted_data_agent/core/locks/` | `base.py`, `factory.py`, `inprocess.py`, `redis_backend.py` |
| `src/trusted_data_agent/core/turn_stream.py` | the S10 per-turn frame log |
| `src/trusted_data_agent/core/relationship_analyzer.py` · `artifact_detectors.py` | which sessions an artifact touches |
| `src/trusted_data_agent/api/routes.py` | `/ask_stream`, `/api/notifications/subscribe`, `/sessions`, `/session/<id>`, delete, cancel, purge, turn routes |
| `src/trusted_data_agent/api/rest_routes.py` | `/v1/sessions*`, `/v1/tasks*`, `_task_outcome`, active-task and reattach endpoints |
| `components/builtin/context_window/session_vector_store.py` | the per-session store behind RAG offload |
| `static/js/eventHandlers.js` | `processStream`, the stream lifecycle, reattach, `handleReloadPlanClick` |
| `static/js/notifications.js` | the notification channel, its guards, REST buffering, reconnect |
| `static/js/handlers/sessionManagement.js` | cache capture and restore, the normal load path |
| `static/js/state.js` · `handlers/statusSummary.js` | isolation state; the status strip verdict |

Gates:

- In `test/trust_battery.sh`: `test_rest_task_outcome.py` (the task status rule),
  `test_steer_reattach.py` / `test_steer_reattach_baseline.py` / `test_steer_reattach_ui.mjs`
  (the frame log and reattach), `test_status_bar.mjs` (the strip's verdicts and reconnect),
  `test_answer_stream_dom.mjs` (stream lifecycle incl. the `activeStreamSessions` teardown),
  `test_turn_siblings.py` / `test_turn_lineage_baseline.py` (lineage in the histories).
  (`test_session_idle.py` gates the *login* session's idle expiry, a different "session".)
- Run directly (not in the battery): `test_session_access.py` (the backend-agnostic API, file
  backend), `test_session_backend_postgres.py` (the same suite on a real Postgres via `pgserver`;
  skips without it), `test_locks.py` (the lock providers; Redis only with `TDA_TEST_REDIS_URL`),
  `test_session_cost_total.py`.
