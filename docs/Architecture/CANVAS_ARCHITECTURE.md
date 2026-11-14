# Canvas Component Architecture: Interactive Code & Document Workspace

> Modular capability-plugin workspace providing CodeMirror 6 editing, live preview, version tracking, diff view, inline AI modification, split-screen mode, MCP execution bridge, RAG-aware generation, and template gallery — all rendered from a single `TDA_Canvas` tool call.

## Overview

The Canvas component (`TDA_Canvas`) is a feature-rich interactive workspace that transforms LLM-generated code and documents into an editable, previewable, version-tracked environment. The Uderia Canvas uses a **modular capability plugin architecture** where each feature — editing, preview, diff, execution — is a self-contained plugin that can be added, removed, or replaced independently.

The canvas operates as a deterministic component: the backend handler applies no LLM transformation — it persists the content as a first-class, user-owned, versioned canvas (`core/canvas_db.py`, schema `40_canvases.sql`) addressable by `canvas_id` or title from any session — while the frontend renderer orchestrates 12 registered capabilities and 4 execution connectors to deliver the full interactive experience.

---

## Table of Contents

- [File Structure](#file-structure)
- [System Architecture](#system-architecture)
  - [Data Flow](#data-flow)
  - [Capability Plugin System](#capability-plugin-system)
  - [State Management](#state-management)
- [Backend Architecture](#backend-architecture)
  - [Handler](#handler)
  - [Language Detection](#language-detection)
  - [REST API Endpoints](#rest-api-endpoints)
- [Frontend Architecture](#frontend-architecture)
  - [Renderer Core](#renderer-core)
  - [CodeMirror 6 Integration](#codemirror-6-integration)
  - [Capability Registry](#capability-registry)
- [Capabilities Reference](#capabilities-reference)
  - [execution_bridge — Connector-Aware Execution](#execution_bridge--connector-aware-execution)
  - [Database connections — managed in one place](#database-connections--managed-in-one-place)
  - [navigator_bridge — the SQL canvas consumes the Database Navigator](#navigator_bridge--the-sql-canvas-consumes-the-database-navigator)
  - [explain — the statement's plan beside the canvas](#explain--the-statements-plan-beside-the-canvas)
  - [sources_badge — RAG Source Attribution](#sources_badge--rag-source-attribution)
  - [template_gallery — Starter Templates](#template_gallery--starter-templates)
  - [toolbar — Copy, Download, Expand, Info](#toolbar--copy-download-expand-info)
  - [code_editor — CodeMirror 6 Editor](#code_editor--codemirror-6-editor)
  - [html_preview — HTML Live Preview with Responsive Viewports](#html_preview--html-live-preview-with-responsive-viewports)
  - [markdown_preview — Markdown Rendering](#markdown_preview--markdown-rendering)
  - [svg_preview — SVG Rendering](#svg_preview--svg-rendering)
  - [diff_view — Side-by-Side Change Tracking](#diff_view--side-by-side-change-tracking)
  - [version_history — Turn-Based Version Management](#version_history--turn-based-version-management)
- [Version Tracking System](#version-tracking-system)
  - [Following changes made elsewhere](#following-changes-made-elsewhere)
- [Diff Algorithm](#diff-algorithm)
- [Live Coding Animation](#live-coding-animation)
- [Inline AI Selection](#inline-ai-selection)
- [Canvas Split Mode Toggle](#canvas-split-mode-toggle)
- [Split-Screen Mode](#split-screen-mode)
- [Bidirectional Context](#bidirectional-context)
- [Template System](#template-system)
- [Integration Points](#integration-points)
  - [Profile Integration](#profile-integration)
  - [Fast-Path Execution](#fast-path-execution)
  - [Component Discovery](#component-discovery)
  - [SSE Event Pipeline](#sse-event-pipeline)
- [Security Considerations](#security-considerations)
- [Performance Characteristics](#performance-characteristics)
- [Extensibility](#extensibility)
- [File Reference](#file-reference)

---

## File Structure

```
components/builtin/canvas/
├── manifest.json           # Component metadata, tool definition, render targets
├── handler.py              # CanvasComponentHandler — deterministic; persists via core/canvas_db.py
├── renderer.js             # Capability + connector-based renderer (~5970 lines)
├── instructions.json       # Intensity-keyed LLM guidance (none/medium/heavy)
├── templates.json          # 12 starter templates for template gallery
└── connectors/             # Backend execution connectors (Python)
    ├── __init__.py         # Connector registry — get_connector(), list_connectors()
    ├── base.py             # BaseCanvasConnector ABC, ExecutionResult, ConnectionTestResult
    └── sql.py              # SQLNativeConnector (PostgreSQL, MySQL, SQLite, DuckDB, MongoDB, Teradata, JDBC)

src/trusted_data_agent/core/canvas_db.py   # Canvas persistence: canvases + canvas_versions (self-applying schema)
src/trusted_data_agent/api/canvas_routes.py # /v1/canvases REST (canvas_bp, registered in main.py)
schema/40_canvases.sql                      # DDL: canvases, canvas_versions
```

---

## System Architecture

### Data Flow

```
LLM calls TDA_Canvas(content, language, title, sources?)
         │
         ▼
CanvasComponentHandler.process()
  → Validates/normalizes language
  → Upserts the canvas by title (or updates by canvas_id) → records a version
  → Computes metadata (line_count, previewable)
  → Returns ComponentRenderPayload(spec={canvas_id, versions, ...})
         │
         ▼
Execution path routes payload:
  tool_enabled  → phase_executor fast-path (skip tactical LLM)
  llm_only      → LangChain StructuredTool via agent
  rag_focused   → auto-upgrade to agent after retrieval
  genie         → coordinator tool merge
         │
         ▼
generate_component_html() embeds:
  <div data-component-id="canvas" data-spec='{...}'></div>
         │
         ▼
ui.js scans [data-component-id] → renderComponent('canvas', ...)
         │
         ▼
renderCanvas(containerId, payload):
  1. Inject CSS (once)
  2. Parse spec, record version
  3. Build canvas state object
  4. Build DOM (header, tab bar, toolbar, body)
  5. Filter capabilities for this language
  6. Initialize all active capabilities
  7. Create tab buttons and panels
  8. Render toolbar capabilities
  9. Activate default tab (code_editor)
  10. Return canvasState
```

### Capability Plugin System

Each capability implements a standard contract:

```javascript
{
    id: string,                    // Unique identifier (e.g., 'code_editor')
    label: string,                 // Tab/button text
    type: 'tab' | 'toolbar',      // Where it renders
    languages: string[],           // Which languages activate it ('*' = all)
    init(canvasState),             // Called during canvas creation
    render(panel, content, language, canvasState),  // Render into container
    destroy(),                     // Cleanup
    getState?(canvasState),        // Return current state (optional)
    refresh?(panel, content, language, canvasState),  // Refresh on tab switch (optional)
    shouldCreateTab?(canvasState), // Gate whether tab appears (optional)
}
```

Capabilities are registered via `registerCapability(cap)` and stored in the module-level `_capabilities` array. During `renderCanvas()`, capabilities are filtered by language match and partitioned by type:

- **Tab capabilities**: Create tab buttons in the header tab bar and corresponding panels in the body
- **Toolbar capabilities**: Render buttons/widgets into the header toolbar area

### State Management

The `canvasState` object is the central data contract shared across all capabilities:

```javascript
canvasState = {
    // Content
    content: string,           // Original content from backend
    language: string,          // Normalized language identifier
    title: string,             // Canvas title (default: 'Canvas')
    sources: string | null,    // RAG source documents (comma-separated)

    // Metadata
    previewable: boolean,      // Whether language supports preview (html, svg, markdown)
    lineCount: number,         // Line count
    fileExtension: string,     // Download extension (e.g., '.py')

    // DOM references
    container: HTMLElement,    // Root container
    _tabBar: HTMLElement,      // Tab bar container
    _toolbar: HTMLElement,     // Toolbar container
    _body: HTMLElement,        // Body container
    _panels: Object,          // Map of tabId → panel element

    // Editor
    activeTab: string,         // Currently active tab ID
    _editorView: EditorView,   // CodeMirror 6 instance (set by code_editor)

    // Version state
    versions: Array,           // All versions from _canvasVersions store
    previousContent: string,   // Content of previous version (for diff)
    versionNumber: number,     // Current version number

    // Inline AI
    _inlineAIBtn: HTMLElement,    // Floating "Ask AI" button
    _inlineAIPrompt: HTMLElement, // Prompt input container

    // Execution bridge
    _runBtn: HTMLElement,         // Run button reference
    _activeConnector: Object,     // Resolved connector for current language
    _activeConnectionId: string,  // Selected named connection ID (SQL / MongoDB; null = MCP bridge, SQL only)
    _connections: Array,          // Cached list of saved connections

    // Methods
    getContent(): string,      // Returns live content from CM6 editor
}
```

---

## Backend Architecture

### Handler

**File:** `components/builtin/canvas/handler.py`

The `CanvasComponentHandler` extends `BaseComponentHandler` with these key properties:

| Property | Value | Purpose |
|----------|-------|---------|
| `component_id` | `"canvas"` | Unique component identifier |
| `tool_name` | `"TDA_Canvas"` | LLM tool invocation name |
| `is_deterministic` | `True` | Bypasses tactical LLM in tool_enabled profiles |

`process()` dispatches on the optional `action` argument:

| Action | Args | Effect |
|---|---|---|
| *(omitted)* / `create` | `content` (+ `language`, `title`, `sources`) | `upsert_canvas` by normalized title — reusing a title updates that canvas and records a version |
| `update` | `canvas_id`, `content` | `update_canvas` on that canvas |
| `open` | `canvas_id` or `title` | Opens an existing canvas — from any session |
| `list` | — | The user's canvases |
| `delete` | `canvas_id` | Deletes the canvas and its versions |
| `restore` | `canvas_id`, `version_id` | Restores a stored version |
| `get` | `canvas_id` or `title` | Returns the canvas content (read) |

For content actions the handler normalizes the language (lowercase, strip whitespace; heuristic detection via `_detect_language()` when unknown), computes `previewable`, `line_count` and `file_extension`, and returns a `ComponentRenderPayload` whose spec carries `canvas_id` and the stored versions (`action='open'`, so a freshly written canvas pops out as before). Persistence is the handler's only side effect — all rendering logic lives in the frontend renderer.

Every store write (`create_canvas`, `update_canvas`, `delete_canvas`, `restore_version` in `canvas_db.py`) carries `@_signals.announces("canvas", …)`, raising one `component_changed` notice. The manifest declares `instances: "user"` (a canvas belongs to the user, not to an agent), `signals` (`announces: created · updated · deleted · restored`, `consumes_refs: true`), and `supports_instance_io: true` — `list_instances` / `export_instance` / `import_instance` make a canvas portable (file export/import, marketplace publish/fork/subscribe); an imported canvas starts with its latest content as v1.

### Language Detection

When the LLM provides an unrecognized language value, the handler falls back to heuristic detection:

| Heuristic | Detection |
|-----------|-----------|
| HTML | Starts with `<!DOCTYPE` or `<html`, or contains `<head` in first 200 chars |
| SVG | Starts with `<svg` |
| Mermaid | Starts with `graph ` or `sequenceDiagram` |
| Python | Contains `def ` in first 500 chars or `import ` in first 200 |
| SQL | Contains `SELECT `, `CREATE TABLE`, or `INSERT INTO` in first 300 chars (case-insensitive) |
| JSON | Starts with `{` and ends with `}` |
| Markdown | Starts with `# ` or contains `\n## ` |
| Default | Falls back to `html` if no heuristic matches |

**Supported languages:** html, css, javascript, python, sql, markdown, json, svg, mermaid

### REST API Endpoints

Canvas REST endpoints live in two places. **Persistence** — `api/canvas_routes.py` (`canvas_bp`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/canvases` | List the user's canvases |
| POST | `/v1/canvases` | Create / upsert by title (`mode: "create"` refuses a title in use) |
| GET | `/v1/canvases/<id>` | Get a canvas with its versions |
| PUT | `/v1/canvases/<id>` | Update (content, title, language; `record_version` controls whether a version is recorded) |
| DELETE | `/v1/canvases/<id>` | Delete a canvas and its versions |
| GET | `/v1/canvases/<id>/versions` | Version history |
| POST | `/v1/canvases/<id>/versions/<vid>/restore` | Restore a prior version |

**Workspace services** — `rest_routes.py`: `POST /v1/canvas/inline-ai`, `POST /v1/canvas/explain` (see [explain](#explain--the-statements-plan-beside-the-canvas)), `POST /v1/canvas/execute`, the five `/v1/canvas/connections` routes (see [Database connections](#database-connections--managed-in-one-place)) and `GET /v1/canvas/templates`:

#### POST /v1/canvas/inline-ai

Lightweight LLM call for inline code modification. Does not use the planner/executor — calls `call_llm_api()` directly with `disabled_history=True`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `selected_code` | string | Yes | Code selected by user |
| `instruction` | string | Yes | Modification instruction |
| `full_content` | string | Yes | Full file content for context |
| `language` | string | Yes | Content language |

**Response:** `{ status, modified_code, input_tokens, output_tokens }`

#### POST /v1/canvas/execute

Execute a canvas statement server-side. With `connection_id` (or `connector_id`) it runs on a native connector (`connectors/sql.py`) using the credentials resolved from the one Database Connection store; without either, `language: "sql"` runs through the session's MCP bridge. JavaScript, Python, HTML and CSS execute in the browser and never reach this route.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | Yes | Statement to execute |
| `language` | string | Yes | `"sql"` (MCP bridge or native) or `"mongodb"` (native only) |
| `connection_id` | string | No | Named Database Connection (`dbconn_…`, or a legacy `canvas_conn_…`) |
| `connector_id` | string | No | Native connector id (default `sql_native` when `connection_id` is given) |
| `session_id` | string | No | Session ID for MCP adapter resolution (bridge path) |

**Response:** `{ status, result, row_count, execution_time_ms }` plus, on the native path, `columns / rows / truncated / grid_truncated / masked_columns` for the result grid.

**Error responses:** 400 (unsupported language on the bridge, unknown connector, no credentials, SQL error), 401 (auth required), 503 (MCP not connected)

#### GET /v1/canvas/templates

Serve starter templates from `templates.json`. No authentication required.

**Response:** `{ status, templates: [...] }`

---

## Frontend Architecture

### Renderer Core

**File:** `components/builtin/canvas/renderer.js` (~5970 lines)

The renderer is a single ES module. Its main exports:

| Export | Purpose |
|--------|---------|
| `renderCanvas(containerId, payload)` | Main entry point — routes to split-panel or inline mode based on `window.__canvasSplitMode` |
| `getOpenCanvasState()` | Returns current split-panel canvas state for bidirectional context |
| `closeSplitPanel()` | Close the split-screen panel and clear bidirectional context |
| `insertIntoOpenCanvas(text)` / `highlightInOpenCanvas(names)` | The one seam other panels (Database Navigator, EXPLAIN plan) use to write into / select in the open canvas |
| `isUserEdit(transactions)`, `canExplain`, `explainTarget`, `planTableRanges` | Pure rules exported for the gates |

The module self-contains all CSS (injected once via `injectStyles()`), all 12 capabilities, the 4 connectors, the diff algorithm, live animation engine, inline AI functions, split-screen management, version tracking and autosave, and split-mode routing logic.

### CodeMirror 6 Integration

CodeMirror 6 is loaded lazily via dynamic `import()` from `esm.sh` CDN. We import from **individual `@codemirror/*` sub-packages** (not the `codemirror` meta-package, which esm.sh serves as a UMD bundle that breaks named ESM exports).

```javascript
// Core packages (cached in _cmCache singleton):
@codemirror/view@6        → EditorView, lineNumbers, keymap, drawSelection, etc.
@codemirror/state@6       → EditorState, Compartment
@codemirror/commands@6    → defaultKeymap, history, historyKeymap
@codemirror/language@6    → syntaxHighlighting, bracketMatching, foldGutter, etc.
@codemirror/search@6      → searchKeymap, highlightSelectionMatches
@codemirror/autocomplete@6 → autocompletion, closeBrackets, completionKeymap
@codemirror/lint@6        → lintKeymap

// Language extensions:
@codemirror/lang-html@6   → html()
@codemirror/lang-javascript@6 → javascript()
@codemirror/lang-python@6 → python()
@codemirror/lang-sql@6    → sql()
@codemirror/lang-json@6   → json()
@codemirror/lang-css@6    → css()
@codemirror/lang-markdown@6 → markdown()

// Theme (chrome only — bg, cursor, selection, gutters):
@codemirror/theme-one-dark@6 → oneDarkTheme

// Syntax highlighting (custom "Oceanic" palette):
@lezer/highlight@1           → HighlightStyle, tags
```

All 16 modules are loaded in parallel via `Promise.all()`. The `basicSetup` extension array is **constructed manually** from the imported sub-package pieces (mirroring the official `codemirror/src/codemirror.ts` source) rather than imported from the meta-package. This avoids the UMD re-export issue on esm.sh.

**Syntax Color Palette ("Oceanic"):** We use `oneDarkTheme` for editor chrome (background, cursor, selection, gutters) but replace oneDark's red-heavy syntax highlighting with a custom `HighlightStyle` that uses blues, teals, and greens:

| Token | Color | Name |
|-------|-------|------|
| Keywords | `#c792ea` | Lavender |
| Names/variables | `#d6deeb` | Light slate |
| Properties | `#80cbc4` | Teal |
| Functions | `#82aaff` | Periwinkle |
| Strings | `#c3e88d` | Lime green |
| Numbers/types | `#ffcb6b` | Gold |
| Operators | `#89ddff` | Ice blue |
| Comments | `#637777` | Muted teal (italic) |
| Booleans/atoms | `#f78c6c` | Soft coral |

The Prism.js fallback uses matching token color overrides scoped to `.canvas-fallback-code .token.*` so both renderers look consistent.

The `_cmLoadPromise` singleton prevents duplicate loading attempts. If loading fails (network issue), the renderer falls back to Prism.js highlighting in a read-only `<pre><code>` block.

**Language extension mapping:**

| Language | CodeMirror Extension |
|----------|---------------------|
| html, svg | `langHtml()` |
| css | `langCss()` |
| javascript | `langJs()` |
| python | `langPy()` |
| sql | `langSql()` |
| json | `langJson()` |
| markdown, mermaid | `langMd()` |

### Capability Registry

Capabilities are registered in this exact order (which determines toolbar button order and tab order):

| # | ID | Type | Languages | Description |
|---|-----|------|-----------|-------------|
| 1 | `code_editor` | tab | `*` | CodeMirror 6 editor with live animation |
| 2 | `html_preview` | tab | `html` | Sandboxed iframe with responsive viewports |
| 3 | `markdown_preview` | tab | `markdown` | Lightweight markdown-to-HTML renderer |
| 4 | `mermaid_preview` | tab | `mermaid` | Mermaid diagram rendering |
| 5 | `svg_preview` | tab | `svg` | Sanitized inline SVG rendering |
| 6 | `diff_view` | tab | `*` | LCS-based side-by-side diff (conditional) |
| 7 | `version_history` | toolbar | `*` | Version dropdown with restore (conditional) |
| 8 | `execution_bridge` | toolbar | `*` | Connector-aware "Run" button (+ connection picker, Explain on Teradata) |
| 9 | `navigator_bridge` | toolbar | `sql`, `mongodb` | Database Navigator completion / describe / insert-join |
| 10 | `sources_badge` | toolbar | `*` | RAG knowledge source dropdown |
| 11 | `template_gallery` | toolbar | `*` | Starter template gallery modal |
| 12 | `toolbar` | toolbar | `*` | Copy, Download / Save to Files, Expand, info badge |

The former `credentials` tab capability is gone: connections are managed only in Components → Connectors → Database Connection (see below).

### Connector Registry

Execution connectors handle code execution for specific languages. Registered in this order:

| # | ID | Languages | Backend? | Named Connections | Description |
|---|-----|-----------|----------|-------------------|-------------|
| 1 | `javascript_worker` | `javascript` | No | None | Web Worker sandbox with console capture, 10s timeout |
| 2 | `html_sandbox` | `html`, `css` | No | None | HTML: Preview tab switch. CSS: Iframe with sample elements |
| 3 | `python_pyodide` | `python` | No | None | Pyodide WASM (~10MB lazy-load), persistent session |
| 4 | `sql_native` | `sql`, `mongodb` | Yes | Driver, host, port, database, user, password, SSL, JDBC fields | Native DB drivers (asyncpg/aiomysql/aiosqlite/duckdb/pymongo/teradatasql/JayDeBeApi); the driver list is the one declaration in `core/database_connections.DRIVERS`. Uses named connections selected via toolbar picker. Falls back to MCP bridge when no connection selected — never for a `mongodb` statement (JSON: collection + pipeline, run on a MongoDB connection only) |

---

## Capabilities Reference

### execution_bridge — Connector-Aware Execution

**Type:** toolbar | **Languages:** `*` (all — guarded by connector availability)

Adds a green "▶ Run" button as the first toolbar item for any language that has a registered connector. For SQL canvases, a **connection picker** `<select>` dropdown appears next to the Run button, listing "MCP Bridge (default)" plus all saved named connections. The button's tooltip shows which connector will handle execution. Clicking it:
1. Reads current editor content via `state.getContent()`
2. Calls `executeViaConnector(state)` which dispatches to the active connector
3. The connector returns `{ result, error, stats: { rowCount, timeMs } }`
4. Results displayed in a console panel below the canvas body

**Connector dispatch flow:**
```
Run button click → executeViaConnector(state)
                      ↓
              state._activeConnector || getConnectorForLanguage(language)
                      ↓
              connector.execute(code, null, state)
                      ↓
              if state._activeConnectionId:
                  POST /v1/canvas/execute { connector_id, connection_id }
                  Backend reads encrypted creds for that connection_id
              else:
                  Legacy MCP bridge (executeSqlViaMcp)
                      ↓
              showConsolePanel(state, result/error, ...)
```

**Per-language execution:**
- **JavaScript**: Web Worker (browser sandbox, 10s timeout, console capture)
- **Python**: Pyodide WASM (lazy-loaded, persistent session, stdout/stderr capture)
- **SQL**: Named connection selected → `POST /v1/canvas/execute` with `connector_id` + `connection_id`; otherwise falls back to MCP bridge
- **MongoDB** (`language: 'mongodb'`, JSON-highlighted): the same route on a MongoDB Database Connection — the statement names a collection and an aggregation pipeline (or a find-style filter), parsed by `core/mongo_query.py`; with no connection selected the canvas says to choose one (there is no MCP fallback). The navigator opens a MongoDB statement as `mongodb`
- **HTML**: Switches to Preview tab
- **CSS**: Shows sample elements in iframe with user CSS applied

The console panel (`showConsolePanel()`) shows:
- Header: "Console" label, count badge (rows for SQL, lines for other languages), execution time, close button
- Body: Execution results as preformatted text
- Error state: Red text and "Error" badge

### Database connections — managed in one place

The Canvas has **no connection manager of its own**. SQL canvases run on the user's Database
Connections, picked from the **connection menu** in the canvas toolbar (MCP Bridge by default).
Connections are created, edited, tested and deleted in **Components → Connectors → Database
Connection** — the one manager, shared by the canvas, the Database Navigator and PDM deployment.
The Components → Canvas → **Connections** tab and the picker's **Manage connections…** /
**Add a database connection…** entry both open it (`window.openPlatformConnector('uderia-database')`).
(The Canvas used to carry a second full manager here over the same store; it was removed as a
duplicate. The `/v1/canvas/connections` routes remain for REST callers.)

The toolbar row is the panel's auto-hiding sub-toolbar, which collapses by clipping
(`overflow: hidden`). A menu opened from it (connection picker, History, Sources) extends below
the row, so it used to open invisibly and a tap landed on the editor. `split-panels.css` keeps
the sub-toolbar revealed and unclipped while any `*dropdown--open` menu inside it is open — one
rule for every split panel; a new toolbar menu must use a `…dropdown--open` class to be covered
(gate: `test/test_canvas_connection_pointer.mjs`).

**Storage:** the Canvas reads and writes connections through the **one Database Connection store** (`core/database_connections.py`, the same store the Database Navigator, the Connectors panel and PDM deploy use). The Canvas routes are thin adapters over it; the store's id is the connection's id everywhere (`dbconn_…`). Older connections saved as legacy `canvas_conn_{id}` credentials are listed read-through (by their full `canvas_conn_…` id) and still execute; **editing one promotes it** into the store (the legacy entry is then hidden), deleting one removes it. The form says `user`, the store says `username` — the routes translate. A masked password sent back unchanged by the edit form keeps the stored password (it used to be stored as the mask), and deleting a migrated connection also removes its legacy original (which used to reappear). A caller may open a canvas already bound to a connection by passing `connection_id` in the render spec — how the navigator's *Open in canvas* works. Gate: `test/test_canvas_connection_store.py`.

**REST endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/canvas/connections` | List all saved connections (passwords masked) |
| GET | `/v1/canvas/connections/<connection_id>` | Get single connection (password masked) |
| PUT | `/v1/canvas/connections` | Save connection (create or update, encrypted via Fernet) |
| DELETE | `/v1/canvas/connections/<connection_id>` | Delete connection |
| POST | `/v1/canvas/connections/test` | Test connection (credentials in body, not stored) |

**Connection picker integration:** After save/delete, `_refreshConnectionPicker(state)` updates the toolbar dropdown to reflect the current connection list.

### navigator_bridge — the SQL canvas consumes the Database Navigator

**Type:** toolbar | **Languages:** `sql`, `mongodb` (describe/complete are SQL-only)

The canvas CONSUMES the navigator's catalog; it never grows a schema browser of its own. Every
piece is conditional on two facts and silent otherwise: the navigator component is enabled for
the profile (its REST answers; `403 component_disabled` ⇒ the bridge does nothing) and the
canvas is bound to a **direct** Database Connection (`state._activeConnectionId`; the MCP
bridge has no catalog). `_navSourceFor(state)` resolves the navigator source whose
`connection_id` is the canvas's binding (`GET /v1/database-navigator/sources`, cached 60 s
per profile × connection); every catalog read is cached per object.

| Piece | What it does | Where |
|---|---|---|
| **Schema-aware completion** | Registered as language data on the SQL editor (`langExt[0].language.data.of({ autocomplete })`, picked up by basicSetup's `autocompletion()` beside lang-sql's keywords). After `FROM`/`JOIN`: catalog search hits as `db.table` + databases (system ones hidden). After `alias.` / `db.table.`: the table's columns with type + flag chips (`pii`, `key`, `text_date`…), keys ranked first; after `db.`: its objects. After a joined table or inside `ON`: **JOIN snippets from declared FKs** (inferred joins offered too, labelled with their confidence, ranked below). A bare column completes from every table in the statement, only once a word is typed or on Ctrl-Space. It **suggests, never rewrites** (`validFor` scopes the replacement to the typed word). | `_navComplete`, pure `_sqlAliasMap` / `_completionContext` / `_resolveIdentifier` / `_joinSnippets` |
| **Describe in navigator** | The identifier at the cursor (or the selection), resolved through the statement's aliases (a bare name is searched in the catalog), opens the navigator ON THE SAME SOURCE at that table — and column (`openDatabaseNavigatorPanel({source, database, object, column})`). | `_describeInNavigator` |
| **Navigator → canvas** | Tree objects, search hits and column rows are `draggable` (`text/plain` = the qualified name; CodeMirror's drop cursor takes it). The Keys & joins tab offers **insert join** on every relationship row — only while the canvas is open — writing `JOIN <other> ON a.k = b.k` at the cursor through the canvas's ONE seam, `insertIntoOpenCanvas(text)` (false, never a throw, with no open canvas). | navigator `renderer.js` `_draggable` / `_joinClause`; canvas `insertIntoOpenCanvas` |
| **Result-grid parity** | `/v1/canvas/execute` returns `columns/rows/truncated/grid_truncated/masked_columns`; the console draws them through the **navigator's own grid** (`_internal.resultGrid`), masked by the server's `masked_columns` — the SAME rule as the navigator preview (`dbcatalog.intelligence.masked_columns`; the handler and the endpoint both delegate, gate-asserted) — with a "show values" toggle. Text stays the fallback (an error, no columns, no navigator renderer). | `showConsolePanel(…, structured)` → `_renderConsoleGrid` |
| **Ask the agent** | The statement lands in the composer as a fenced block; the user presses Send — never auto-submitted. | `_handOffToComposer` |

Boundaries, deliberate: the canvas runs what the owner wrote (no preflight gate on this path);
no visual query builder; no second schema browser. Gate: `test/test_canvas_navigator_bridge.mjs`
(the shipped helpers, completion source against a fake navigator REST, the drag/insert-join
surfaces and the console grid EXECUTED under a DOM stub) + `test/test_canvas_navigator_bridge.py`
(one masking rule, one statement reader, the grid payload), both in the battery.

### explain — the statement's plan beside the canvas

On SQL bound to a **Teradata** Database Connection the toolbar carries an **Explain** button (the
plan icon, beside Run). It sends the editor's selection, or the whole statement, to
`POST /v1/canvas/explain`; the backend runs `EXPLAIN` on a read-only connection (the statement is
explained, never executed; anything but one SELECT is refused first) and returns the same spec the
agent's `TDA_ExplainPlan` builds (`plan_parser.build_spec` — one builder, one renderer). The plan
opens in the **EXPLAIN Plan panel** (`#explain-plan-split-panel`), a split panel beside the canvas:

- **compare** — the panel keeps the last plans; a re-explain shows what changed from the previous
  one, matched by finding kind (step numbers move between plans): `no longer: rows redistributed`,
  `new: product join`, step count and estimated time before → after; a change is coloured by how
  much the finding matters, not by its direction;
- **plan → SQL** — selecting a step selects the tables it reads in the editor
  (`highlightInOpenCanvas`); a plan names a table by its alias, so `planTableRanges` resolves the
  alias to the table it stands for first;
- **SQL ← plan** — "Open statement in canvas" from any plan, including one from an answer (whose
  inline row offers "Open in panel").
- **segments** — the plan is split into **Overview** (stats, the comparison, findings folded by
  kind — one line, its explanation once, a chip per step — and *where the time goes*), **Graph**,
  **Steps** (a table of every step, sortable by order / time / rows, filterable to the steps with
  a finding) and **Statement**. A step chip or a table row opens the Graph on that step; a plan
  that just arrived with a comparison opens on the Overview, otherwise the reader's last segment
  is kept;
- **a graph that knows its width** — `layoutPlan(spec, {maxWidth, tables})` lays the plan out for
  the width it is shown in (re-laid out on resize, fullscreen, a dashboard tile): nodes narrow to
  a minimum, then a long row wraps (a parallel group's rows share one labelled frame), spare
  width widens the nodes; when the source tables would not fit one row they are named inside the
  steps that read them (switchable). Zoom re-lays the plan out for `width / zoom`, so zooming out
  shows more of it; *Whole plan* picks the zoom at which it fits (`fitAllZoom`). Selecting a step
  lights its edges and the steps at their other end, and the step's facts dock under the graph
  (beside it when the panel is wide); arrows walk the steps in execution order, Escape clears.
  The key is one click away (shown when there is room for it). A bar in each node is its share of
  the estimated time;
- **chrome** — the panel uses the shared split-panel title bar (instance switcher over the
  panel's own plans, toolbar pin, fullscreen, close) and the shared auto-hiding menu row: source
  badge, Re-explain (canvas plans, which know their connection), Compare, Open in canvas, Copy,
  Download.

The connection picker shows the bound connection's **name** beside the driver icon (it had been
an icon only, the same database glyph as the navigator's "Describe" button). Errors go to the
console as for Run. Not offered for other drivers or the MCP bridge (no plan the panel can read).

### sources_badge — RAG Source Attribution

**Type:** toolbar | **Languages:** `*` (all)

Only renders when `canvasState.sources` is non-null. Shows a purple "📚 Sources" button that toggles a dropdown listing the knowledge documents that informed the canvas content. Sources are passed as a comma-separated string in the `TDA_Canvas` tool call's `sources` argument.

The LLM is instructed (via `instructions.json`) to include sources when generating content based on knowledge repository documents.

### template_gallery — Starter Templates

**Type:** toolbar | **Languages:** `*` (all)

Adds a "Templates" button that opens a full-screen modal gallery:
1. Fetches templates from `GET /v1/canvas/templates` (cached in module-level `_templateCache`)
2. Groups templates by category (HTML, Python, SQL, Markdown, CSS, JavaScript)
3. Renders category tabs and a 2-column card grid
4. Clicking a card calls `applyTemplate()`:
   - Records current content as a version (for undo)
   - Replaces editor content via `restoreVersion()`
   - Closes modal

Modal dismissal: Escape key, close button, or overlay click.

**12 included templates:**

| ID | Category | Description |
|----|----------|-------------|
| html-landing | HTML | Responsive landing page with hero, features, CTA |
| html-dashboard | HTML | Admin dashboard with sidebar, stats cards, data table |
| html-form | HTML | Styled contact form with validation |
| python-cli | Python | argparse CLI with subcommands and logging |
| python-flask | Python | Minimal Flask REST API with CRUD endpoints |
| python-data | Python | Pandas data loading, cleaning, and visualization |
| sql-query | SQL | Common patterns: JOIN, GROUP BY, subqueries, window functions |
| sql-migration | SQL | Migration with CREATE, ALTER, and rollback |
| markdown-report | Markdown | Structured report with sections, tables, conclusions |
| markdown-docs | Markdown | API documentation with endpoints, parameters, examples |
| css-reset | CSS | Modern reset with custom properties and utility classes |
| js-module | JavaScript | ES module with class, async/await, error handling |

### toolbar — Copy, Download, Expand, Info

**Type:** toolbar | **Languages:** `*` (all)

Renders four toolbar elements:
- **Copy**: `navigator.clipboard.writeText()` with "Copied!" flash feedback
- **Download**: the file (language-appropriate extension) goes through the one export exit `window.FilesKeep.deliver(blob, filename, {componentId: 'canvas', instanceId: canvas_id})` — a download under the tier's download policy, or *Save to Files* (PROP-010, [FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md))
- **Expand**: Opens split-screen panel via `popOutCanvas(state)` (visible when `#canvas-split-panel` exists in DOM)
- **Info badge**: `"{lineCount} lines · {language}"` pill

### code_editor — CodeMirror 6 Editor

**Type:** tab | **Languages:** `*` (all)

The primary capability. During `init()`, loads CodeMirror 6 asynchronously. During `render()`:

1. Determines if live coding animation is active (`window.__canvasLiveMode`)
2. Creates `EditorView` with extensions: `...basicSetup` (manually constructed array), theme chrome and syntax colors wrapped in `Compartment`s for live hot-swap (see Theme Awareness), language extension, line wrapping — all filtered via `.filter(Boolean)` for safety
3. In live mode: starts with empty document, read-only compartment, triggers `animateCodeInsertion()`
4. In normal mode: starts with full content, attaches inline AI selection listener
5. Falls back to Prism.js `<pre><code>` if CodeMirror fails to load

**Inline AI integration**: An `EditorView.updateListener` detects non-empty text selections and shows a floating "Ask AI" button. This listener is disabled during live animation (`!isLive` guard).

### html_preview — HTML Live Preview with Responsive Viewports

**Type:** tab | **Languages:** `html`

Renders HTML content in a sandboxed `<iframe srcdoc>` with `sandbox="allow-scripts"`. Includes a responsive viewport toggle bar with three presets:

| Viewport | Width | Height | Scaling |
|----------|-------|--------|---------|
| Desktop | 100% | 500px | None |
| Tablet | 768px | 1024px | `transform: scale()` to fit container |
| Mobile | 375px | 667px | `transform: scale()` to fit container |

The `refresh()` method re-renders the preview with the latest editor content when the user switches back to the Preview tab.

### markdown_preview — Markdown Rendering

**Type:** tab | **Languages:** `markdown`

Uses a built-in lightweight markdown-to-HTML renderer (`renderMarkdownToHtml()`) — no external dependencies. Supports:

- Headers (H1-H6)
- Bold, italic, bold+italic
- Inline code and fenced code blocks
- Links (opens in new tab)
- Blockquotes
- Unordered and ordered lists
- Horizontal rules
- Tables (with header row)
- Paragraphs

### svg_preview — SVG Rendering

**Type:** tab | **Languages:** `svg`

Renders SVG content as inline HTML after sanitization:
- Strips all `<script>` tags
- Removes all `on*` event handler attributes

Displayed in a centered white-background container with automatic scaling.

### diff_view — Side-by-Side Change Tracking

**Type:** tab | **Languages:** `*` (all) | **Conditional**: only appears when `previousContent` exists

Uses the LCS (Longest Common Subsequence) diff algorithm to compute line-by-line differences between the previous and current version. Renders a side-by-side view:

- **Left panel**: Previous version with red highlighting for removed lines
- **Right panel**: Current version with green highlighting for added lines
- **Header**: `+N added / −M removed` summary with version numbers
- **Synchronized scrolling**: Both panels scroll together

### version_history — Turn-Based Version Management

**Type:** toolbar | **Languages:** `*` (all) | **Conditional**: only appears when 2+ versions exist

Renders a "History" button with a version count badge. Clicking opens a dropdown listing all versions from newest to oldest, showing:
- Version number and current marker
- Turn index and timestamp
- "Restore" button for historical versions

Clicking a historical version previews it in the code editor. The "Restore" button replaces the editor content via a CodeMirror 6 transaction.

---

## Version Tracking System

Versions are stored in a module-level `Map` (`_canvasVersions`) keyed by normalized canvas title:

```
_canvasVersions: Map<string, Array<{
    content: string,
    language: string,
    timestamp: number,    // Date.now()
    turnIndex: number,    // _globalTurnCounter (monotonically increasing)
}>>
```

**`recordVersion(title, content, language)`**:
1. Normalizes title to lowercase key
2. Checks if latest version has identical content (deduplication)
3. Increments `_globalTurnCounter`
4. Appends new version entry
5. Returns `{ versions, previousContent, versionNumber }`

**Lifecycle**: The in-memory map is the fallback for a canvas without a `canvas_id` (e.g. one rendered from a message persisted before canvases were stored); its versions are lost on page reload. The global turn counter ensures consistent ordering across multiple canvases.

**Persisted canvases (Option A — the normal case).** Every canvas the handler writes has a `canvas_id` and keeps its versions in the store (`core/canvas_db.py`, `canvas_versions`), so history survives reloads and the canvas can be opened from any session.

**Saving what the user types.** A persisted canvas autosaves the user's edits — typing, deleting, undo/redo, an inline-AI edit (`userEvent: 'input.ai'`), a navigator insert (`'input.paste'`) — 1.5 s after the last one, with `record_version: false`, so a pause is not a version. Leaving the canvas (switching in the panel, *New canvas*, closing the panel, a page hide with `keepalive`) saves with `record_version: true`, which records ONE version (de-duplicated against the latest). Programmatic content — an agent write being animated in, a version restore or preview — carries no user event and is never saved. A re-render of the same canvas drops a pending autosave, so a stale editor can never overwrite a newer agent write. The rule `isUserEdit(transactions)` is exported for the gate.

**New and Rename.** The panel switcher offers *New canvas* (an inline name; same language and database connection as the open canvas; `POST /v1/canvases` with `mode: "create"`, which refuses a title in use instead of overwriting that canvas) and *Rename* of the open canvas (`PUT` `title`; a collision is refused under the field, the connection follows the new title).

### Following changes made elsewhere

The open canvas follows its own store writes through `UderiaBus.onComponent('canvas', …)` (every `component_changed` notice for the canvas component; the bus marks the echo of this tab's own autosave `own` and the canvas ignores it):

- **Deleted anywhere** → the panel closes.
- **Updated / restored elsewhere** (another tab, a REST script, a scheduled run, an agent turn) and **nothing typed is pending** → reloaded in place (`_reloadCanvasFromStore`).
- **Updated elsewhere while an edit is pending** → the pending autosave is **held** (nothing is sent — an autosave that fired here would overwrite the outside write) and the panel shows the shared split-panel notice *Changed elsewhere while you were editing* with **Reload** (take theirs, drop the pending save) and **Keep mine** (send mine now, as a version, over theirs). Leaving a held canvas (switch, close, page hide) keeps mine — typed work is never dropped silently.

The notice is `splitPanelChrome.showSplitPanelNotice` (`.sp-notice`), the same line every split panel uses. Gates: `test/test_component_bus.mjs` §7, `test/test_component_signals.py` §D.

**`restoreVersion(state, content)`**: Replaces CodeMirror editor content via a single transaction (supports Ctrl+Z undo).

**`previewVersion(state, content, versionNum)`**: Same as restore, plus switches to the Code tab.

---

## Diff Algorithm

The `computeLineDiff(oldText, newText)` function implements a line-based LCS (Longest Common Subsequence) algorithm:

1. **Split** both texts into line arrays
2. **Build LCS table**: Dynamic programming matrix (`Uint16Array` for memory efficiency)
3. **Backtrack** to produce diff entries:
   - `equal`: Line exists in both versions (with old and new line numbers)
   - `added`: Line exists only in new version (green)
   - `removed`: Line exists only in old version (red)

**`diffStats(diff)`**: Counts added and removed lines for the summary header.

**Complexity**: O(m × n) time and space where m and n are line counts. The `Uint16Array` optimization limits maximum comparable file length to 65,535 lines.

---

## Live Coding Animation

**Problem**: Canvas content arrives complete in the `final_answer` SSE event, so without help the editor would show nothing until the whole answer is there.

**Solution**: Simulated streaming using CodeMirror 6 transactions to insert content line-by-line after the full content arrives.

**What the animation provides:** syntax highlighting and line numbers while the content appears, a button to skip the animation, a progress indicator, and the preview opening automatically when it completes.

### Animation Flow

```
window.__canvasLiveMode = true  (set by eventHandlers.js)
         │
         ▼
renderCanvas() detects live mode
         │
         ▼
CodeEditor creates empty doc with read-only compartment
         │
         ▼
animateCodeInsertion(view, fullContent, canvasState, compartment, cm)
  │
  ├── Split content into lines
  ├── Calculate adaptive speed:
  │   ├── ≤30 lines: 1 line/80ms (~2.4s total)
  │   ├── 31-100 lines: 1 line/adaptive ms (~3s total)
  │   └── 100+ lines: N lines/batch/20ms (~2s total)
  │
  ├── Show progress bar with Skip button
  │
  ├── For each batch:
  │   ├── CM6 transaction: insert at doc end
  │   ├── Update progress: "42 / 128 lines"
  │   ├── Auto-scroll (unless user has scrolled up)
  │   └── await delay
  │
  └── On complete:
      ├── Remove progress bar
      ├── Reconfigure read-only compartment to writable
      └── If HTML → auto-switch to Preview tab (600ms delay)
```

### Cross-Module Communication

The `window.__canvasLiveMode` global flag solves a module isolation problem:
- `eventHandlers.js` imports from `../components/builtin/canvas/renderer.js` (filesystem path)
- `componentRenderers.js` imports from `/api/v1/components/canvas/renderer` (API-served path)
- The browser treats these as **separate module instances** with independent state
- A module-level flag set by one would be invisible to the other
- `window.__canvasLiveMode` is globally visible to both

The flag is set to `true` before `UI.addMessage()` in `eventHandlers.js` and cleared by `renderCanvas()` after consumption, with a 100ms `setTimeout` fallback for responses without canvas content.

---

## Inline AI Selection

**Purpose**: Select text in the editor → floating "Ask AI" button → type instruction → LLM modifies just that selection → result replaces selection atomically.

### Flow

```
User selects text in CodeMirror 6
         │
         ▼
EditorView.updateListener detects non-empty selection
  → showInlineAIButton(canvasState, view, coords)
  → Floating blue "Ask AI" button near selection end
         │
         ▼
User clicks → showInlineAIPrompt()
  → Input (260px) + "Go" button + cancel (✕)
  → Enter key submits, Escape cancels
         │
         ▼
executeInlineAI(canvasState, view, from, to, selectedText, instruction)
  → Loading state (disabled input, "..." button)
  → POST /v1/canvas/inline-ai
  → Body: { selected_code, instruction, full_content, language }
         │
         ▼
Backend: Direct call_llm_api(disabled_history=True)
  → System prompt: "Replace ONLY selected code, output ONLY replacement"
  → Returns { modified_code, input_tokens, output_tokens }
         │
         ▼
CM6 dispatch({ changes: { from, to, insert: modified_code } })
  → Single transaction = Ctrl+Z undoes atomically
  → showInlineAITokenBadge() (fades after 3s)
```

**Safeguards**:
- Selection listener disabled during live animation (`!isLive` guard)
- `mousedown` on prompt container prevents selection loss via `e.preventDefault()`
- Error handling: shows error in input placeholder, auto-clears after 3s
- Prism.js fallback mode: no inline AI (CM6-only feature)

---

## Canvas Split Mode Toggle

A `</>` toggle button in the conversation header controls how canvases render. Inspired by Claude Artifacts (inline card → side panel), ChatGPT Canvas (persistent side panel), and Gemini Canvas (explicit user toggle).

### Toggle Button

**Location:** `#canvas-mode-toggle` in `templates/index.html`, placed next to `#window-menu-button` in the header right section.

**Styling:** Defined in `static/css/main.css` as `#canvas-mode-toggle` with `[data-theme="light"]` variant. Active state uses the `.canvas-toggle-active` CSS class (toggled by `applyCanvasToggleStyle()`) which provides visible indicators in both dark mode (white glow/ring) and light mode (slate background/ring).

**State:** Persisted in `localStorage('canvasSplitMode')` and exposed via `window.__canvasSplitMode` (set by `setCanvasSplitMode()` in `state.js`).

### Two Rendering Modes

`renderCanvas()` reads `window.__canvasSplitMode` and routes accordingly:

#### Split Mode ON

1. **Inline:** `renderInlineCard()` — compact card with title, language badge, 3-line code preview, "View in Canvas →" link
2. **Split Panel:** `autoPopOutCanvas()` → `renderCanvasFull()` — full canvas with all capabilities (editable CM6, tabs, toolbar, inline AI, console, version history, diff)

If the panel is already open, new canvases **replace** the current content. Old inline card badges update accordingly.

#### Split Mode OFF

`renderInlineCompact()` — limited inline canvas with:
- Read-only CodeMirror (`EditorView.editable.of(false)` + `EditorState.readOnly.of(true)`)
- Copy button only
- 300px max-height with "Show more" expand
- No tabs, toolbar, inline AI, run, sources, version history, or diff

### Feature Comparison

| Feature | Split Panel | Inline Compact |
|---------|-------------|---------------|
| CodeMirror editor | Editable | Read-only |
| Tab bar (Code/Preview/Diff) | Yes | No |
| Copy / Download / Expand | Yes | Copy only |
| Inline AI editing | Yes | No |
| Run (SQL execution) | Yes | No |
| Sources / Version history | Yes | No |
| Bidirectional LLM context | Yes | No |
| Max height | Full panel | ~300px |

### Rendering Architecture

```
renderCanvas(containerId, payload)
    │
    ├── containerId starts with 'canvas-split-render-'?
    │   └── renderCanvasFull()  (internal, full capabilities)
    │
    ├── window.__canvasSplitMode === true?
    │   ├── renderInlineCard()       → compact card in chat
    │   └── autoPopOutCanvas()       → full canvas in split panel
    │
    └── else (split OFF)
        └── renderInlineCompact()    → read-only inline viewer
```

### State Flow

```
Toggle ON  → setCanvasSplitMode(true) → localStorage + window.__canvasSplitMode
Toggle OFF → setCanvasSplitMode(false) → closeSplitPanel() via dynamic import
```

### Files

- `templates/index.html` — Toggle button HTML
- `static/js/domElements.js` — `canvasModeToggle` reference
- `static/js/state.js` — `canvasSplitMode` state + `setCanvasSplitMode()`
- `static/js/eventHandlers.js` — Toggle initialization, `applyCanvasToggleStyle()`
- `components/builtin/canvas/renderer.js` — `renderInlineCard()`, `renderInlineCompact()`, `autoPopOutCanvas()`, `renderCanvasFull()`, routing in `renderCanvas()`

---

## Split-Screen Mode

**Design decision**: Originally planned as floating sub-windows (using `subWindowManager.js`), redesigned per user feedback to a side-by-side split-screen layout matching the Gemini Canvas / Claude Artifacts pattern.

### HTML Structure

```
#main-content
  └── div.flex-1.flex-col.min-h-0
       ├── #chat-canvas-split (flex-row wrapper)
       │    ├── main#chat-container (flex-1, scrollable chat)
       │    └── aside#canvas-split-panel (hidden by default, 50% when open)
       │         ├── .sp-header (shared chrome: accent code-icon + #canvas-switcher-btn switcher + fullscreen + close)
       │         └── #canvas-split-content (render target; in-body .canvas-header = editor controls — tabs + toolbar — as a secondary row)
       └── footer#chat-footer
```

### Behavior

**Opening** — Two entry points:
- `popOutCanvas(state)` — Manual expand from toolbar button (takes canvasState)
- `autoPopOutCanvas(spec)` — Automatic from split mode toggle (takes raw spec, clears old card badges)

Both follow the same flow:
1. Update split panel title
2. Clear previous content
3. Create render target inside `#canvas-split-content`
4. Show panel with CSS transition (`width: 0` → `width: 50%`, `min-width: 320px`)
5. Call `renderCanvasFull()` to get full capability experience in the panel
6. Store reference in `_activeSplitCanvasState` for bidirectional context

**Closing** (`closeSplitPanel()`):
1. Clear `_activeSplitCanvasState` (stops bidirectional context)
2. Remove `canvas-split--open` class (triggers CSS transition)
3. On `transitionend`: hide panel, clear content DOM

**Split panel overrides**:
- No border/radius on `.canvas-container`
- Unlimited `max-height` on `.canvas-body` and `.cm-editor`

---

## Bidirectional Context

When a canvas is open in the split panel, user edits flow back to the LLM on every subsequent query.

### Data Flow

```
User edits code in split-panel CodeMirror
         │
         ▼
getOpenCanvasState()  [renderer.js]
  → Reads _activeSplitCanvasState._splitState.getContent()
  → Compares with originalContent to detect modifications
  → Returns { title, language, content, modified }
         │
         ▼
handleChatSubmit()  [eventHandlers.js]
  → canvas_context = getOpenCanvasState()
  → Included in POST /ask_stream body
         │
         ▼
routes.py / rest_routes.py
  → canvas_context = data.get("canvas_context")
  → Passed to execution_service.run_agent_execution()
         │
         ▼
execution_service.py → PlanExecutor(canvas_context=...)
         │
         ▼
PlanExecutor._format_canvas_context()  [executor.py]
  → Formats as structured text block:
    "# Open Canvas: {title}
     Language: {language} | Status: Modified/Unchanged
     ```{language}
     {content}
     ```"
         │
         ├── llm_only → prepended to user message
         ├── rag_focused → prepended to synthesis context
         ├── tool_enabled → prepended to strategic planning input
         └── conversation_with_tools → prepended to effective query
```

The `modified` flag tells the LLM whether the user has edited the canvas content, helping it decide whether to reference the canvas state.

### Instructions Guidance

The `instructions.json` (medium and heavy intensity) instructs the LLM:
- When an "Open Canvas" section appears in context, use `TDA_Canvas` with the **same title** to update it
- Always provide complete updated content (not just changed parts)
- The user can see what changed via the built-in diff view

---

## Template System

Templates are stored as static JSON in `components/builtin/canvas/templates.json` and served via `GET /v1/canvas/templates`. Each template has:

```json
{
    "id": "html-landing",
    "name": "Landing Page",
    "category": "HTML",
    "language": "html",
    "description": "Responsive landing page with hero, features, and CTA",
    "content": "<!DOCTYPE html>..."
}
```

Templates contain 30-100 lines of well-commented, production-quality starter code. The template gallery modal groups them by category with filterable tabs and a 2-column card grid.

When a template is applied:
1. Current editor content is recorded as a version (preserving undo capability)
2. Editor content is replaced via `restoreVersion()`
3. The `canvasState.language` is updated to match the template's language

---

## Integration Points

### Profile Integration

Canvas is enabled per-profile via the profile's `componentConfig` (stored in `user_preferences`; `tda_config.json` only seeds a new user). An agent with no entry inherits the manifest default — `profile_defaults.enabled_for` lists all four IFOC classes — and then also gets the `canvas-expert` companion skill:

```json
"componentConfig": {
    "canvas": { "enabled": true, "intensity": "medium" }
}
```

**Intensity levels** control LLM instruction aggressiveness:

| Level | Behavior |
|-------|----------|
| `none` | No instructions injected — LLM won't use TDA_Canvas |
| `medium` | Contextual guidance — use canvas for substantial content |
| `heavy` | Aggressive guidance — MUST use canvas for any code/document output |

Profiles without `componentConfig.canvas` get the component at default intensity (`medium`).

### Fast-Path Execution

In `tool_enabled` profiles, the `CanvasComponentHandler.is_deterministic = True` flag triggers the generic deterministic fast-path in `phase_executor.py`:

1. Strategic plan includes `TDA_Canvas` tool call with arguments
2. Phase executor detects deterministic component
3. Constructs `generic_action` from tool name + strategic arguments
4. Emits "Plan Optimization" SSE event
5. Executes via `_execute_action_with_orchestrators()` — bypasses tactical LLM entirely
6. Sets `_component_bypass_handled = True`

This saves one full LLM call (tactical planning) per canvas invocation.

### Component Discovery

The `ComponentManager` (singleton via `get_component_manager()`) auto-discovers the canvas component:

1. Scans `components/builtin/canvas/manifest.json`
2. Loads `CanvasComponentHandler` via `importlib`
3. Creates `StructuredTool` from manifest `tool_definition`
4. Injects intensity-keyed instructions from `instructions.json`
5. Registers renderer endpoint at `/v1/components/canvas/renderer`

No manual registration is needed. Adding the component directory is sufficient.

### SSE Event Pipeline

Canvas content flows through the standard SSE pipeline:
1. Handler returns `ComponentRenderPayload` with `render_target = INLINE`
2. `generate_component_html()` wraps spec in `<div data-component-id="canvas" data-spec='...'>`
3. The div is embedded in the `final_answer` SSE event
4. `ui.js` scans for `[data-component-id]` attributes after rendering
5. `renderComponent('canvas', containerId, spec)` is called from `componentRenderers.js`
6. `renderCanvas()` executes, creating the full interactive workspace

---

## Security Considerations

### HTML Preview Sandboxing

The HTML preview iframe uses `sandbox="allow-scripts"` — scripts can execute within the iframe but cannot:
- Access parent window DOM
- Navigate the parent window
- Open popups
- Submit forms
- Access `localStorage`/cookies of the parent origin

### SVG Sanitization

SVG content is sanitized before inline rendering:
- All `<script>` tags are stripped
- All `on*` event handler attributes are removed

### Inline AI

The inline AI endpoint:
- Requires JWT authentication
- Uses `disabled_history=True` — no conversation history leakage
- Uses `session_id=None` — no session state mutation
- Strips markdown code fences from LLM response

### Server-side Execution

The execute endpoint:
- Requires JWT authentication
- Runs SQL (MCP bridge or a native connection) and MongoDB (native connection only); other languages return 400
- Resolves native credentials from the Database Connection store server-side — the browser never holds a password
- Masks result columns by the same rule as the Database Navigator preview (`masked_columns`)
- Runs what the owner wrote — there is no preflight gate on this path

---

## Performance Characteristics

### Loading

| Operation | Time | Notes |
|-----------|------|-------|
| CSS injection | ~1ms | One-time, cached via `_stylesInjected` flag |
| CodeMirror 6 CDN load | 200-500ms | First load only, cached in `_cmCache` singleton |
| Canvas render | ~5ms | DOM construction + capability initialization |
| Live animation | 2-4s | Adaptive speed based on content length |
| Prism.js fallback | ~2ms | No network dependency |

### Memory

| Component | Footprint | Notes |
|-----------|-----------|-------|
| CodeMirror 6 modules (16 packages) | ~800KB | Loaded once, shared across all canvases |
| Version store | Variable | Proportional to content × versions per canvas |
| LCS diff table | O(m × n) | Created on demand, garbage collected after render |
| Template cache | ~50KB | Cached after first gallery open |

### CDN Dependency

CodeMirror 6 is loaded from `esm.sh` using 16 individual sub-package imports (15 `@codemirror/*` + 1 `@lezer/highlight`). The `codemirror` meta-package is **not used** because esm.sh serves it as a UMD bundle that doesn't expose named ESM exports (`EditorView` and `basicSetup` are both `undefined`). Instead, `basicSetup` is constructed manually from the sub-package exports.

CSP allows `https://esm.sh` in both `script-src` and `connect-src` directives (configured in `main.py`).

If the CDN is unreachable:
- Canvas still renders (Prism.js fallback)
- Editing is disabled (read-only `<pre><code>`)
- Preview, diff, toolbar, templates all work normally
- `_cmLoadPromise` is reset to `null` on failure, allowing retry on next canvas render

### Theme Awareness

The canvas is fully theme-aware across all three themes (legacy, modern, light):

1. **CSS Variables:** All backgrounds, borders, text colors, and hover states use CSS variable wrappers with dark-theme fallbacks — e.g., `var(--bg-secondary, rgba(0,0,0,0.15))`. This ensures the canvas adapts automatically when the theme changes.

2. **Light Theme Overrides:** A `.canvas-light` CSS class (~25 selectors) handles elements that need explicit light-theme treatment — header backgrounds, toolbar borders, template cards, split-panel chrome, etc. A `MutationObserver` on `document.body` watches `data-theme` attribute changes and toggles `.canvas-light` on all canvas elements in real time.

3. **Dual CodeMirror Themes:** Two complete theme sets are loaded:
   - **Dark:** `oneDarkTheme` (editor chrome) + `oceanicStyle` (blue/teal/green syntax palette)
   - **Light:** `oceanicLightTheme` (white bg, slate gutters, blue selection) + `oceanicLightStyle` (rich colors on white: purple keywords, teal properties, blue functions, emerald strings)

4. **Live Theme Hot-Swap:** CodeMirror theme and syntax-highlighting extensions are wrapped in `Compartment`s at creation time. A module-level `_liveEditors` registry tracks all live editor instances. When the theme changes, `reconfigureCmEditors()` iterates the registry and dispatches `compartment.reconfigure()` effects — switching between dark/light themes instantly without recreating editors. Editors are removed from the registry on destroy or when disconnected from the DOM.

---

## Extensibility

### Adding a New Capability

Create a capability object and call `registerCapability()`:

```javascript
registerCapability({
    id: 'my_capability',
    label: 'My Tab',
    type: 'tab',           // or 'toolbar'
    languages: ['python'],  // or ['*'] for all

    init(state) {
        // Called during canvas creation
    },

    render(panel, content, language, state) {
        // Render your UI into the panel
    },

    destroy() {
        // Cleanup
    },
});
```

No changes to CanvasCore, other capabilities, or backend handler are required.

### Adding a New Language

1. Add language to `SUPPORTED_LANGUAGES` set in `handler.py`
2. Add file extension to `EXTENSION_MAP` in `handler.py`
3. Add detection heuristic to `_detect_language()` in `handler.py`
4. Add `@codemirror/lang-*` import to `loadCodeMirror()` `Promise.all()` and add mapping to `getCmLanguageExt()` in `renderer.js`
5. Add preview capability if applicable (e.g., `mermaid_preview`)

### Adding a New Execution Connector

The connector plugin system makes adding new execution languages straightforward:

```javascript
registerConnector({
    id: 'my_connector',
    name: 'My Language',
    languages: ['mylang'],
    requiresBackend: false,    // true = calls /v1/canvas/execute with connector_id
    credentialSchema: null,    // or array of field definitions for Credentials tab

    async execute(code, credentials, state) {
        // Execute code and return normalized result
        return {
            result: 'output text',
            error: null,  // or error message string
            stats: { rowCount: 0, timeMs: 42 },
        };
    },

    // Optional: browser-side connection test
    async testConnection(credentials) {
        return { valid: true, message: 'Connected', server_info: 'v1.0' };
    },
});
```

The `execution_bridge` capability automatically picks up the new connector — no changes to existing code required. If `credentialSchema` is provided, the Credentials tab appears automatically.

For backend connectors, also create a Python connector class extending `BaseCanvasConnector` in `connectors/` and register it in `connectors/__init__.py`.

---

## File Reference

### Canvas Component Files

| File | Lines | Purpose |
|------|-------|---------|
| `components/builtin/canvas/manifest.json` | ~115 | Component metadata, TDA_Canvas tool definition, `instances`, `signals` |
| `components/builtin/canvas/handler.py` | ~460 | Deterministic handler: 7 actions over the canvas store, instance export/import |
| `components/builtin/canvas/renderer.js` | ~5970 | Full frontend: 12 capabilities, 4 connectors, CSS, animation, inline AI, split-screen, autosave, follow-elsewhere |
| `components/builtin/canvas/instructions.json` | ~5 | LLM guidance at none/medium/heavy intensity |
| `components/builtin/canvas/templates.json` | ~100 | 12 starter templates |
| `src/trusted_data_agent/core/canvas_db.py` | ~350 | Canvas store: `canvases`, `canvas_versions`, signal-announced writes |
| `src/trusted_data_agent/api/canvas_routes.py` | ~180 | `/v1/canvases` persistence REST (7 endpoints) |
| `schema/40_canvases.sql` | ~45 | DDL: `canvases`, `canvas_versions` |
| `components/builtin/canvas/connectors/__init__.py` | ~30 | Backend connector registry |
| `components/builtin/canvas/connectors/base.py` | ~50 | BaseCanvasConnector ABC, ExecutionResult, ConnectionTestResult |
| `components/builtin/canvas/connectors/sql.py` | ~530 | SQLNativeConnector (PostgreSQL, MySQL, SQLite, DuckDB, MongoDB, Teradata, JDBC) |
| `schema/20_canvas_connectors.sql` | ~20 | Legacy `canvas_connector_credentials` table (connections now live in `core/database_connections.py`; legacy rows are read through) |

### Modified Integration Files

| File | Change |
|------|--------|
| `components/component_registry.json` | Canvas entry (active, Code category) |
| `static/js/componentRenderers.js` | Import + register `renderCanvas` |
| `tda_config.json` | Canvas in all 4 profile componentConfigs |
| `src/trusted_data_agent/agent/phase_executor.py` | Generic deterministic fast-path |
| `src/trusted_data_agent/api/rest_routes.py` | Connector-aware execute endpoint + credential CRUD + SQL test |
| `src/trusted_data_agent/auth/database.py` | Bootstrap `canvas_connector_credentials` table |
| `src/trusted_data_agent/main.py` | CSP: added `'wasm-unsafe-eval'` for Pyodide WASM |
| `requirements.txt` | Database drivers (asyncpg, aiomysql, aiosqlite, duckdb, pymongo, teradatasql) |
| `static/js/eventHandlers.js` | Live mode flag, canvas context collection |
| `src/trusted_data_agent/api/routes.py` | Accept `canvas_context` parameter |
| `src/trusted_data_agent/api/rest_routes.py` | Accept `canvas_context`; inline-ai / explain / execute / connections / templates endpoints |
| `src/trusted_data_agent/agent/execution_service.py` | Thread `canvas_context` to PlanExecutor |
| `src/trusted_data_agent/agent/executor.py` | Format + inject canvas context for all profiles |
| `src/trusted_data_agent/agent/conversation_agent.py` | Accept canvas context, emit sub_window SSE |
| `src/trusted_data_agent/components/utils.py` | Skip sub_window payloads in HTML generation |
| `templates/index.html` | Split-screen HTML structure |
| `static/js/state.js` | Expose `window.__currentSessionId` |

### Automatic Integration (Zero Changes)

| System | Mechanism |
|--------|-----------|
| ComponentManager discovery | Auto-scans `components/builtin/canvas/manifest.json` |
| Handler loading | `importlib` auto-imports `CanvasComponentHandler` |
| LangChain tool creation | Auto-created `StructuredTool` from manifest args |
| Instruction injection | Auto-injected via `get_instructions_text()` |
| Tool merging (all profiles) | Auto-merged via component tool factories |
| MCP routing | `is_component_tool()` routes to handler |
| DOM rendering | `ui.js` scans `[data-component-id]` → `renderComponent()` |

---

## Competitive Comparison

| Feature | Gemini | Claude | ChatGPT | Uderia Canvas |
|---------|:------:|:------:|:-------:|:------------:|
| Syntax highlighting | Yes | Yes | Yes | **Yes** |
| Live HTML preview | Yes | Yes | Yes | **Yes** |
| Direct code editing | Yes | No | Limited | **Yes** (CM6) |
| Responsive viewport toggle | No | No | No | **Yes** |
| Diff view on LLM updates | No | No | No | **Yes** |
| Version history | No | No | No | **Yes** |
| Inline AI selection | No | No | Limited | **Yes** |
| Live coding animation with highlighting | No | No | No | **Yes** |
| Backend code execution | No | No | No | **Yes** (SQL via MCP or native connections, MongoDB) |
| RAG-aware source attribution | No | No | No | **Yes** |
| Template gallery | No | No | No | **Yes** |
| Modular plugin architecture | No | No | No | **Yes** |
| Profile-based intensity control | No | No | No | **Yes** |
| Bidirectional context (edits → LLM) | No | Partial | No | **Yes** |
| Graceful CM6 → Prism fallback | N/A | N/A | N/A | **Yes** |
| Works across 4 profile types | N/A | N/A | N/A | **Yes** |
