# Dashboard Canvas Architecture

> **Persistent live dashboards composed from conversation visuals** — lets users pin any chart, table, or KPI rendered in the conversation pane onto a persistent split-screen canvas. Tiles re-execute their originating query on demand using the same profile and session context that produced them.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [File Structure](#2-file-structure)
3. [Architecture Diagram](#3-architecture-diagram)
4. [Component Layer (TDA_Dashboard)](#4-component-layer-tda_dashboard)
   - [Tool Definition](#tool-definition)
   - [Action Reference](#action-reference)
   - [Handler Routing](#handler-routing)
   - [Payload Views](#payload-views)
5. [Pin-to-Dashboard Flow](#5-pin-to-dashboard-flow)
   - [Dashboard Picker Modal](#dashboard-picker-modal)
   - [Tile Creation Request](#tile-creation-request)
6. [Frontend Renderer](#6-frontend-renderer)
   - [Split Panel Lifecycle](#split-panel-lifecycle)
   - [Exported Functions](#exported-functions)
   - [CSS Grid Layout](#css-grid-layout)
   - [Tile Anatomy](#tile-anatomy)
   - [Edit Mode](#edit-mode)
   - [Dashboard Switcher](#dashboard-switcher)
   - [Inline Chat Cards](#inline-chat-cards)
7. [Tile Refresh Mechanism](#7-tile-refresh-mechanism)
   - [Following changes made elsewhere](#following-changes-made-elsewhere)
   - [Save snapshot to Files](#save-snapshot-to-files)
8. [Database Schema](#8-database-schema)
9. [REST API Endpoints](#9-rest-api-endpoints)
10. [Session & Panel Lifecycle](#10-session--panel-lifecycle)
11. [Theme System](#11-theme-system)
12. [LLM Instructions](#12-llm-instructions)
13. [Design Decisions](#13-design-decisions)
14. [File Reference](#14-file-reference)

---

## 1. System Overview

### What the Dashboard Component Does

The Dashboard component (`TDA_Dashboard`) gives users a persistent visual workspace that lives alongside the conversation. Any chart, table, or KPI that the LLM renders in the chat can be pinned to a dashboard with a single click. Tiles persist across sessions and can be refreshed on demand by re-executing their originating prompt through the standard agent pipeline.

The component follows the same **split-panel canvas** pattern used by Flow, Knowledge Graph, and the Scheduler: a resizable `<aside>` panel opens to the right of the chat and stays open across turns until the user closes it or switches sessions.

### Two Entry Points

**1. LLM tool calls** — The LLM can visualize data inline and create, open, list, and manage dashboards and tiles directly through the `TDA_Dashboard` tool. The handler returns inline chat cards with "Open in Canvas" / "Add to Dashboard" buttons.

**2. Pin acts** — "Pin to dashboard" is the footer act of an answer row: every data table and every component whose manifest declares `answer_surface.pin: true` (chart, knowledge graph, flow, EXPLAIN plan) carries a server-drawn `.ans-pin` button, revealed and handled in `ui.js` (`_wirePinActs` / `_onPinAct`). Users click it to open a dashboard picker modal and add the visual as a tile (see [§5](#5-pin-to-dashboard-flow)).

### Design Principles

> A dashboard tile is a snapshot with a re-execution handle — it stores the rendered spec for instant display and the originating prompt for on-demand refresh.

> The dashboard canvas is not a separate reporting system — it is a pinboard of conversation outputs, powered by the identical execution pipeline that produced them.

> No build step, no React, no CDN component library — pure vanilla JS + CSS Grid, consistent with the Scheduler canvas pattern.

### Architecture Position

```
                ┌─────────────────────────────┐
                │     User / LLM              │
                └────────────┬────────────────┘
                     two paths│
           ┌─────────────────┴───────────────────┐
           │ TDA_Dashboard tool call             │ Pin button (chat chart hover)
           ▼                                     ▼
  ┌─────────────────────┐             ┌─────────────────────────┐
  │ DashboardHandler    │             │ ui.js answer-row pin act │
  │ (handler.py)        │             │ _wirePinActs /           │
  │ CRUD actions        │             │ _onPinAct                │
  └──────────┬──────────┘             └───────────┬─────────────┘
             │ ComponentRenderPayload             │ addTileFromComponent()
             │ (inline chat card)                │ Dashboard Picker Modal
             ▼                                   ▼
  ┌─────────────────────────────────────────────────────────┐
  │                  renderer.js                            │
  │   renderDashboard()  openDashboardPanel()               │
  │   addTileFromComponent()  _showDashboardPicker()        │
  └───────────────────────┬─────────────────────────────────┘
                          │
              ┌───────────┴──────────────┐
              │                          │
              ▼                          ▼
   ┌──────────────────┐       ┌────────────────────┐
   │ #dashboard-      │       │  REST API           │
   │ split-panel      │◄─────►│  dashboard_routes.py│
   │ CSS Grid canvas  │       │  /api/v1/dashboards │
   └──────────────────┘       └────────────────────┘
                                         │
                              ┌──────────┴──────────┐
                              │     SQLite DB        │
                              │  dashboards          │
                              │  dashboard_tiles     │
                              └─────────────────────┘
```

---

## 2. File Structure

```
components/builtin/dashboard/
├── manifest.json          # Component metadata, TDA_Dashboard tool definition, render targets
├── handler.py             # DashboardComponentHandler — routes 8 actions to SQLite
├── renderer.js            # Full split-panel canvas + inline chat cards (~3 980 lines)
└── instructions.json      # Intensity-keyed LLM guidance (none / medium / heavy)

src/trusted_data_agent/api/
├── dashboard_routes.py    # 12 REST endpoints, registered as dashboard_bp
└── files_routes.py        # POST /v1/files/dashboard-snapshot (Save snapshot to Files)

schema/
└── 39_dashboards.sql      # DDL: dashboards, dashboard_tiles tables + indexes

static/js/handlers/
└── dashboardHandler.js    # Session lifecycle only: close/restore the panel on a session switch

static/js/
└── ui.js                  # _wirePinActs / _onPinAct — the answer-row "Pin to dashboard" act

src/trusted_data_agent/auth/
└── database.py            # _create_dashboard_tables() called during DB init

src/trusted_data_agent/
└── main.py                # dashboard_bp registered at url_prefix="/api"

static/js/
└── componentRenderers.js  # _renderers.set('dashboard', renderDashboard)

templates/
└── index.html             # <aside id="dashboard-split-panel">, dashboardHandler.js script tag

static/js/handlers/
└── splitPanelResizer.js   # 'dashboard-split-panel' added to SPLIT_PANEL_IDS
```

---

## 3. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        BROWSER                                          │
│                                                                         │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────┐  │
│  │        CHAT PANE            │  │    DASHBOARD SPLIT PANEL        │  │
│  │  ┌───────────────────────┐  │  │  ┌─────────────────────────┐   │  │
│  │  │  LLM response         │  │  │  │ dash-panel-header       │   │  │
│  │  │  ┌────────────────┐   │  │  │  │  [switcher] [+] [⟳] [✎]│   │  │
│  │  │  │[data-component-│   │  │  │  └─────────────────────────┘   │  │
│  │  │  │ id="chart"]    │   │  │  │  ┌─────────────────────────┐   │  │
│  │  │  │  G2Plot render │   │  │  │  │ dash-grid               │   │  │
│  │  │  │  ┌──────────┐  │   │  │  │  │  12-col CSS Grid        │   │  │
│  │  │  │  │Pin to Db ◄──┼───┼──┼──┼──►   ┌────────┐ ┌──────┐  │   │  │
│  │  │  │  │(hover btn)│  │   │  │  │  │   │ tile   │ │ tile │  │   │  │
│  │  │  │  └──────────┘  │   │  │  │  │   └────────┘ └──────┘  │   │  │
│  │  │  └────────────────┘   │  │  │  │  ┌──────────────────┐   │   │  │
│  │  │                       │  │  │  │  │ tile (full width) │   │   │  │
│  │  │  ┌────────────────┐   │  │  │  │  └──────────────────┘   │   │  │
│  │  │  │ TDA_Dashboard  │   │  │  │  └─────────────────────────┘   │  │
│  │  │  │ inline card    │   │  │  │                                 │  │
│  │  │  │ [Open in Canvas│   │  │  └─────────────────────────────────┘  │
│  │  │  │  ──────────────►───┼──┼──► openDashboardPanel()              │  │
│  │  │  └────────────────┘   │  │                                       │  │
│  │  └───────────────────────┘  │                                       │  │
│  └─────────────────────────────┘                                       │  │
│                                                                         │
│  ui.js                                                                  │
│   .ans-pin click → _onPinAct() → addTileFromComponent()                 │
│   ← _showDashboardPicker() → POST /api/v1/dashboards/<id>/tiles         │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                              │ REST
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        BACKEND                                          │
│                                                                         │
│   dashboard_routes.py (dashboard_bp)                                    │
│     GET  /v1/dashboards                  list_dashboards                │
│     POST /v1/dashboards                  create_dashboard               │
│     GET  /v1/dashboards/<id>             get_dashboard + tiles          │
│     PUT  /v1/dashboards/<id>             update_dashboard               │
│     DEL  /v1/dashboards/<id>             delete_dashboard               │
│     GET  /v1/dashboards/<id>/tiles       list_tiles                     │
│     POST /v1/dashboards/<id>/tiles       add_tile                       │
│     PUT  /v1/dashboards/<id>/tiles/<t>   update_tile                    │
│     DEL  /v1/dashboards/<id>/tiles/<t>   delete_tile                    │
│     POST /v1/dashboards/<id>/tiles/<t>/refresh      refresh_tile        │
│     POST /v1/dashboards/<id>/tiles/<t>/refresh-mcp  refresh_tile_mcp    │
│     PUT  /v1/dashboards/<id>/layout      bulk_update_layout             │
│                                                                         │
│   DashboardComponentHandler (handler.py)                                │
│     actions: visualize / create / open / close / list / add_tile /      │
│              refresh / delete                                           │
│                                                                         │
│   SQLite: tda_auth.db                                                   │
│     dashboards       → one row per user dashboard                       │
│     dashboard_tiles  → one row per tile, FK → dashboards ON DELETE CASCADE│
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Layer (TDA_Dashboard)

### Tool Definition

Defined in `manifest.json`:

```json
{
  "component_id": "dashboard",
  "tool_name": "TDA_Dashboard",
  "render_targets": { "default": "inline" },
  "profile_defaults": { "enabled_for": ["tool_enabled", "llm_only", "rag_focused", "genie"] }
}
```

The tool is available across all four profile types. It renders an inline card in the chat regardless of the action — only `open` additionally auto-opens the split panel.

The manifest also declares `instances: "user"` (a dashboard belongs to the user, not to an agent) and its `signals`: it announces `created · updated · deleted · refreshed`, listens to `uderia:component-changed`, consumes results by reference (`consumes_refs: {implicit: "latest_data"}`) and `conflicts_with: ["chart"]`. `supports_instance_io: true` opts it into the per-instance export/import contract shared with flow (marketplace publish/fork of dashboards).

### Action Reference

| Action | Required Args | Effect |
|--------|---------------|--------|
| `visualize` | `content`, `tile_title` (`chart_type` optional) | **The default for any show/chart/plot request.** Renders an inline auto-viz card (`view: "visualization"`) with "Add to Dashboard" / "Open Dashboard" — nothing is stored until the user pins. A missing `chart_type` is detected from the user's own words (`_chart_type_from_text`) |
| `create` | `name` | Creates a new dashboard; returns inline card with "Open in Canvas" button |
| `open` | `dashboard_id` | Loads dashboard + tiles; returns open panel payload; auto-opens split panel |
| `close` | — | Signals frontend to close the panel |
| `list` | — | Returns list of user dashboards as inline card with clickable rows |
| `add_tile` | `dashboard_id`, `query`, `tile_title` | Adds a tile — `refresh_status='ok'` when `content` was passed, else `'pending'` (the frontend refreshes it on open). Without a `dashboard_id`, or when the content is an HTML table, it falls through to `visualize` so the user picks the dashboard |
| `refresh` | `dashboard_id` | Signals frontend to refresh all tiles in the open panel |
| `delete` | `dashboard_id` | Deletes dashboard; closes panel if it was open |

### Handler Routing

```python
class DashboardComponentHandler(BaseComponentHandler):
    async def process(self, arguments, context) -> ComponentRenderPayload:
        action = arguments.get("action", "")
        if action == "create":      return await self._handle_create(...)
        elif action == "open":      return await self._handle_open(...)
        elif action == "close":     return self._handle_close()
        elif action == "list":      return await self._handle_list(...)
        elif action == "add_tile":  return await self._handle_add_tile(...)
        elif action == "visualize": return self._handle_visualize(...)
        elif action == "refresh":   return await self._handle_refresh(...)
        elif action == "delete":    return await self._handle_delete(...)
        else:                       return self._error_payload(...)
```

Every write the handler makes (`create`, `add_tile`, `delete`) is announced through `_announce()` → `core/ui_notify.announce_component_change`, the same notice the REST routes raise with `@_signals.announces_route` — two writers, one table, one change event.

### Data by reference (the session ledger)

`content` is no longer copied from a private cache. The dashboard's old session-wide `_viz_content_cache` is gone: every data and component result of a session is recorded in the session ledger (`components/signals.py` → `ComponentLedger`) at the two tool chokepoints, and before `process()` runs, `signals.before_process` resolves an explicit `source_ref` argument — or, when none is given, the manifest's declared implicit source `latest_data` — and fills an empty `content` argument from that entry. The model never has to copy rows through its context to visualize them. See [COMPONENT_ARCHITECTURE.md § Component Signals](COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other).

### Payload Views

The `spec` object returned in the `ComponentRenderPayload` always includes a `view` field that drives `renderDashboard()` on the frontend:

| `view` | When | Frontend action |
|--------|------|-----------------|
| `card` / `created` | create action | Render inline card with "Open in Canvas" |
| `open` / `panel_open` | open action | Render card + auto-open split panel |
| `list` | list action | Render clickable list of dashboards |
| `visualization` | visualize action (and `add_tile` fall-through) | Render the inline auto-viz card with "Add to Dashboard" |
| `tile_added` | add_tile action | Render "Pinned" confirmation card |
| `close` | close action | Call `closeDashboardPanel()` |
| `deleted` | delete action | Render deletion confirmation; close panel if affected |
| `refresh` | refresh action | Trigger tile refresh in open panel |
| `error` | any failure | Render error message |

---

## 5. Pin-to-Dashboard Flow

**One act, one place, one rule.** "Pin to dashboard" is the **footer act of an answer row** — the same `.ans-tf` footer and `.ans-act` button on every pinnable row, server-rendered hidden:

| Row | Button | Drawn by |
|-----|--------|----------|
| A data row's table (and a prose table) | `.ans-pin-table[data-pin-table]` beside *Copy table* | `formatter._data_table_html` |
| A pinnable component row — every component whose manifest declares `answer_surface.pin: true` (chart, knowledge graph, flow, EXPLAIN plan) | `.ans-pin[data-pin-component="<id>"]` after the component's own output | `answer_surface.component_row` → `pin_footer` |
| A component row persisted before its manifest declared `pin` | the same button, added on insertion | `ui.js:_wirePinActs` (`_PINNABLE_COMPONENTS` = the manifests, gate-asserted) |

- **One visibility rule** — `ui.js:_wirePinActs` reveals a button only when the Dashboard component is enabled for the agent that answered (`ComponentGate.isEnabled('dashboard', profileId)`, profile from the originating user message), marked synchronously so a re-render never wires it twice. No gate module ⇒ no button.
- **One click** — a delegated document handler (`_onPinAct`) builds the tile: a table → `{component_id:'table', html: table.outerHTML}`; a component → its `[data-component-id]` container's spec + HTML, title from the spec (`options.title.text`, `title`, `flow.name`) else the row's summary. Turn, session and profile are read **at the click** (`_deriveTableOrigin`) — on a live answer the turn id is stamped when the turn finalizes, after the act was drawn — and the spec's own `source_turn_id` wins when the backend embedded one. Then `addTileFromComponent(payload, meta)`.
- **The Next Steps chip does not repeat it** — `recommender.derive_render_flags` sets `has_direct_pin` when the answer carries either button, and Tier A then offers no "Pin to dashboard" chip (asking the agent to pin would be the same act at the cost of a turn). Answers with nothing pinnable keep the chip.
- **A pinned act can be performed again (PROP-008 §6, OBDA-ACT only).** An `action` tile is a RECORD of one invocation; once its row has **settled** (done · parked · observed · refused) the tile offers **Prepare again** — `POST /v1/actions/<name>/preview` with the pinned invocation, as the agent that prepared it (else the tile's agent), `face: "dashboard"` — and shows the server's `row_html` (the same row the answer draws) with its Confirm; nothing is written until that click, the grant is checked server-side, and the receipt's signed occurrence names the dashboard. While a row still awaits its own Confirm the button is hidden (that Confirm is the owed act); an undo row never offers it. Pure helpers `_actAgainSource` / `_actAgainBody` / `_actRowPending`; gate `test_dashboard_act_again.mjs`.
- **The floating hover button is gone.** `dashboardHandler.js` used to inject an absolutely positioned `.dash-pin-btn` over every `[data-component-id]` (hover only, top-right corner). It covered the card's own controls — the open-in-panel control among them — and could not be found without hovering. `dashboardHandler.js` now only closes/restores the panel on a session switch.
- **Foreign tables** (markup outside the Answer Surface grammar) still take the legacy table interception: wrapped in a dashboard viz card with its own Pin control (`ui.js` → `_pinVizToExistingDashboard`).

> **Profile provenance — do NOT infer it document-wide.** Both paths read the tag from the originating user message's `.message-profile-badge`. Earlier, table interception took the *last* `.message-profile-badge, .profile-tag` in the whole document; because `.profile-tag` is also used by the config panel / profile pickers / other sessions, a stray chip (e.g. `@FOCUS`) leaked onto unrelated tiles. The badge is always resolved from the turn's own user message.

### Dashboard Picker Modal

When the pin button is clicked, `window.addTileFromComponent(payload, meta)` is called. This opens a modal (`dash-picker-backdrop` + `dash-picker-modal`) that:

1. `GET /api/v1/dashboards?source_session_id=<sid>&source_prompt=<prompt>` — loads the user's dashboard list with server-side pin state
2. Renders each dashboard as a selectable row: `Dashboard Name ✓ (N tiles)` — the checkmark appears for dashboards that already contain a tile generated from the same session and prompt
3. Offers a subdued "Create new dashboard" row (muted style) at the bottom
4. Confirms with an "Add to Dashboard" primary button

Selected dashboard ID is tracked locally in the modal closure; the Add button is disabled until a selection is made.

**Server-backed pin state:** The `pinned` flag on each dashboard is resolved server-side by querying `dashboard_tiles WHERE user_id=? AND source_session_id=? AND source_prompt=?`. This means checkmarks survive page refresh, server restart, or any other browser/session boundary — the UI always reflects the actual DB state rather than ephemeral JS memory.

After a successful pin, a *Pinned to dashboard* toast is shown and the target dashboard is shown: reloaded in place when it is already the open dashboard, otherwise fetched and opened in the split panel.

### Tile Creation Request

On confirm:

```
POST /api/v1/dashboards/<id>/tiles
Body:
  title        — derived from spec or container text
  component_id — 'chart' | 'table' | 'kpi' | 'narrative' | 'knowledge_graph' | 'flow'
  spec         — parsed G2Plot spec (self-contained, renders without re-query)
  html         — container.innerHTML (pre-rendered fallback for tables/KPIs)
  source_prompt     — originating user message text
  source_session_id — the session that produced the visual
  source_turn_id    — turn that produced the visual (binds MCP-refresh stamping)
  profile_id        — profile that ran the turn (override-aware)
  profile_tag       — its tag, resolved consistently with profile_id (e.g. "IDGGT")
  tile_width   — 12 for tables, 6 for charts, 3-4 for KPIs
  tile_height  — 4 for charts, 2 for KPIs, 6 for tables
  last_refreshed — current ISO timestamp
```

On the server, `add_tile` stamps `mcp_tool_name`/`mcp_args_json` via `_mcp_stamp_for_turn(source_session_id, user_uuid, source_turn_id)` when that turn used exactly one data tool — enabling the Tier-1 bolt refresh.

On success, the dashboard panel opens (or reloads if already open) and a toast confirmation is shown.

---

## 6. Frontend Renderer

### Split Panel Lifecycle

The panel `<aside id="dashboard-split-panel">` follows the same pattern as every other split canvas:

| State | CSS class | `style.display` |
|-------|-----------|-----------------|
| Hidden | — | `none` |
| Opening | `dash-split--open` | `flex` |
| Open | `dash-split--open` | `flex` |
| Closing | class removed | `flex` (briefly) |
| Closed | — | `none` (after 350ms) |

`splitPanelResizer.js` manages the resize handle between chat and the panel. `'dashboard-split-panel'` is registered in `SPLIT_PANEL_IDS` so it participates in the flex-row layout alongside canvas, KG, scheduler, and flow panels.

### Auto-Viz Typing & Ordering (deterministic intelligence)

The inline visualization card types each column of the parsed table before choosing a
chart, and that typing is where a chart goes wrong silently. Rules (all in
`renderer.js`, gate `test/test_dashboard_viz_typing.mjs`):

- **Cells are normalised first** (`_cleanCell`): whitespace collapsed and trimmed (a warehouse
  `CHAR` column arrives space-padded), and every spelling of an absent value (`null`, `None`,
  `NaN`, `N/A`, blank) becomes empty so it never votes as a category.
- **Temporal = every sampled value matches a form in the date catalogue** (`_DATE_FORMS`):
  ISO date/datetime, `YYYY-MM`, `YYYY/MM/DD`, **`DD-MON-YYYY`** (the Teradata/Oracle default —
  `31-OCT-2023`), `MON DD, YYYY`, `MON YYYY`, `MM/YYYY`, slash/dotted `D/M/Y` or `M/D/Y`
  (order decided per column from the evidence), quarters, halves, ISO weeks, bare month
  names, and bare years only inside 1800–2200 so a column of 4-digit counts stays numeric.
  Each form carries a parser, so the same table decides *is this time* and *which point
  comes first*.
- **Numeric = the WHOLE cleaned string is a number**, never `parseFloat` — `parseFloat("31-OCT-2023")`
  is `31`, which once typed a whole date column numeric and plotted the day-of-month digits on a
  0–32 axis (prod session `661402ae`, Sep 2026). Currency, thousands separators, percent,
  accounting negatives and the unicode minus are accepted; a `12K` suffix is display text.
- **A line on a temporal x is sorted chronologically** (`_sortChronological`, stable,
  unparseable values last) whatever order the rows arrived in — a `TOP 30 … ORDER BY date DESC`
  or a text date sorted lexically no longer zigzags the line. A line on a categorical x keeps
  row order; categorical charts still sort by value.
- **Chart-type hints are word-bounded** (`_extractVizHint`): "airline", "timeline" and
  "deadline" are not line-chart requests.

The backend twin lives in `handler.py:_content_to_html` (gate `test/test_dashboard_content.py`):
every content shape — HTML, a markdown table wrapped in prose or a fence, a JSON string, a
tool-result envelope, row dicts (column set = the union), row lists with a text header — becomes
the same clean table, with `None`/`NaN` empty, short rows padded and empty rows dropped. The
chart component orders a line/area axis with the same catalogue
(`src/trusted_data_agent/components/dateforms.py`, gate `test/test_chart_chronological.py`),
so the two visual components never disagree about a temporal axis.

### Exported Functions

| Function | Purpose |
|----------|---------|
| `renderDashboard(containerId, spec)` | ComponentRenderers registry entry point; dispatches on `spec.view` |
| `openDashboardPanel(spec)` | Opens the panel, loads tiles, wires header buttons |
| `closeDashboardPanel(saveForSession?)` | Closes panel, clears module state, cancels timers |
| `isDashboardPanelActive()` | Returns `true` if panel has `dash-split--open` class |
| `getOpenDashboardState()` | The open dashboard's id/name (used by the Workspace and session save) |
| `addTileFromComponent(payload, meta)` | Opens the dashboard picker modal |
| `_actAgainSource` / `_actAgainBody` / `_actRowPending` | Pure helpers for an action tile's *Prepare again* (exported for `test_dashboard_act_again.mjs`) |

`openDashboardPanel`, `closeDashboardPanel`, `addTileFromComponent` and `isDashboardPanelActive` are also exposed as `window.*` globals for the non-module `dashboardHandler.js` IIFE and `ui.js`.

### CSS Grid Layout

Tiles are positioned on a **12-column CSS Grid**:

```css
.dash-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  grid-auto-rows: 80px;
  gap: 10px;
}
```

Each tile's position is expressed as:
```css
grid-column: <pos_col + 1> / span <tile_width>;
grid-row:    <pos_row + 1> / span <tile_height>;
```

**Default tile sizing conventions:**

| Content type | `tile_width` | `tile_height` | Rationale |
|---|---|---|---|
| KPI / single value | 3–4 | 2 | Compact, row of 3–4 across full width |
| Bar / line chart | 6 | 4 | Half-width, gives chart enough vertical room |
| Pie / donut chart | 4–6 | 4 | Relatively square aspect |
| Table | 12 | 6 | Full width, enough rows visible |
| Narrative text | 6–12 | 3 | Flexible |

**Auto-position algorithm** (server-side, `_auto_position()` in `dashboard_routes.py`):

```
1. Start at row 0, col 0
2. For each existing tile (sorted by sort_order):
   - Mark occupied columns in the tile's row range
3. For the new tile width W:
   - Scan rows from top to bottom
   - Find first contiguous run of W free columns in a row
   - Place tile there
4. If no space found in any existing row: append at new bottom row
```

### Tile Anatomy

```
┌─────────────────────────────────────────────────────┐
│  .dash-tile-header                                  │
│   ╔═══╗ Title (contenteditable in edit mode)        │
│   ║ ⣿ ║ [OPTIM badge] [42m ago] [⟳ or ⚡] [⋮]      │
│   ╚═══╝ (drag handle, edit mode only)               │
├─────────────────────────────────────────────────────┤
│  .dash-tile-body                                    │
│   ┌───────────────────────────────────────────────┐ │
│   │ .dash-tile-chart-container                    │ │
│   │   G2Plot instance (autoFit: true)             │ │
│   │   OR .dash-tile-table-container (innerHTML)   │ │
│   │   OR .dash-tile-kpi-container                 │ │
│   │   OR .dash-tile-skeleton (loading)            │ │
│   │   OR .dash-tile-error (error state)           │ │
│   └───────────────────────────────────────────────┘ │
│                                      ┌──────────────┤ (edit mode)
└──────────────────────────────────────┘  resize grip │
```

The header refresh control is **⚡ (bolt)** when the tile has a stamped `mcp_tool_name` (Tier-1 direct MCP replay) and **⟳** otherwise (LLM re-run) — see [§7](#7-tile-refresh-mechanism). The profile badge colour is resolved by tag from the profile's own `color` field so it matches the chat profile chip.

**Tile states:**

| `refresh_status` | Body content |
|---|---|
| `ok` | Rendered spec (G2Plot / HTML) |
| `pending` | Skeleton shimmer |
| `refreshing` | Skeleton shimmer + header spinner |
| `error` | Error message + Retry button |

**Tile context menu** (⋮ button, appears on hover):
- Refresh tile
- Rename
- Edit query (if `source_prompt` present)
- Duplicate
- Remove tile

### Tile Content Rendering (`_renderTileContent`)

The body renderer branches on `tile.component_id`:

| `component_id` | Render path |
|---|---|
| `chart` | G2Plot instance built from the stored spec (`autoFit: true`) |
| `table` / `kpi` / `narrative` | Pre-rendered `tile.html` injected into the matching container |
| `knowledge_graph` | Dynamic `import('/api/v1/components/knowledge_graph/renderer')` → `renderKnowledgeGraph(host, {...spec, action: undefined, _dashboardFill: true})` |
| `flow` | Dynamic `import('/api/v1/components/flow/renderer')` → `renderFlowPreview(host, {...spec, _dashboardFill: true})` |

**Live-component preview pattern (KG + flow).** Rather than freeze the pin-time HTML, these tiles re-render fresh from the live component renderer. Each export honours a `_dashboardFill` flag that stretches the compact preview to fill the tile (instead of the fixed-height block used in chat), reconciles to the component's live state by id (`renderFlowPreview` re-fetches `/api/v1/flows/<id>`, so a stale pinned snapshot — often missing node positions — self-heals), and exposes a working **"Open in Graph →" / "View in Canvas →"** button that opens the component's split panel. The flow preview is a static SVG schematic drawn from `definition.nodes[].position` + edges (no React Flow); its open button reflects live panel state (`Expanded in side panel →`) and reverts when the panel closes (the close handler resets every `.flow-inline-open-btn--active` globally).

**Table tiles** use `align-self: start` to shrink short tables to content, but are capped to their grid-row span via an inline `max-height` (`_spanHeightPx`) so a tall table scrolls internally instead of overflowing its track and overlapping the tile below. A JS-toggled bottom scroll-fade (`dash-has-scroll-fade`) signals more rows below the fold.

**Flow tile refresh is re-render only (no LLM)** — special-cased early in `_refreshTile`. The flow's live state is the source of truth; re-running the chat prompt that created the flow would re-create/modify it. The refresh tooltip reads "Sync with live flow".

### Edit Mode

The panel header (`index.html`, `#dashboard-split-panel`) carries: the switcher, **Add** (chat to add a tile), **Refresh all**, **Save snapshot to Files** (hidden unless Files is available to the user — see [Save snapshot to Files](#save-snapshot-to-files)), **Edit layout**, **Fullscreen** and **Close**.

Edit mode is toggled by the edit button in the panel header. When active:

- `.dash-grid.edit-mode` CSS class activates
- Drag handles appear on tile headers (pointer-based drag via `pointerdown/pointermove/pointerup`)
- A drag ghost element tracks the pointer
- Resize grips appear at the bottom-right of each tile (CSS `::before` L-shaped corner)
- Tile titles become `contenteditable`; `blur` event PATCHes the new title to the server
- An edit toolbar appears below the panel header with usage guidance

Layout changes (resize, drag) are persisted via:
- Per-resize: `PUT /api/v1/dashboards/<id>/tiles/<tile_id>` with `{tile_width, tile_height}`
- Per-drag: same endpoint with `{pos_col, pos_row}` after drop (or bulk `PUT /layout`)

### Dashboard Switcher

The switcher button (`#dashboard-switcher-btn`) in the shared panel header opens a dropdown via the common `mountSplitPanelSwitcher` helper (`static/js/handlers/splitPanelChrome.js`, used by all split panels — see COMPONENT_ARCHITECTURE.md → Split-Panel Chrome):

```
GET /api/v1/dashboards
  → list rows with tile_count
  → render .sp-switcher-item for each (current dashboard highlighted via .is-current)
  → "Create new dashboard" footer (.sp-switcher-new → _promptCreateDashboard)
```

The helper's `onSelect` calls `_loadAndRenderDashboard(id)` which fetches the full dashboard + tiles and re-renders the grid.

### Inline Chat Cards

Every TDA_Dashboard action returns a chat card. Cards share a consistent structure:

```
┌─────────────────────────────────────────┐
│  [grid icon]  Dashboard Name     [badge]│
├─────────────────────────────────────────┤
│  Description or tile count              │
├─────────────────────────────────────────┤
│  [Open in Canvas]                       │
└─────────────────────────────────────────┘
```

The `list` view renders an interactive list where each row opens the corresponding dashboard.

---

## 7. Tile Refresh Mechanism

### Core principle: a tile is a `(data-source, visualization-spec)` pair

A dashboard tile is **not** a slice of a conversation. Refresh re-acquires the **data** and re-applies the **fixed visualization** the user pinned — it never scrapes a fresh, non-deterministic conversational answer. Two invariants flow from this:

- **Non-destructive** — a refresh must NEVER blank a working visualization. If the re-run yields no valid chart, the prior `spec` (with its embedded data) is kept.
- **Component-aware** — extraction reads the tile's actual `component_id`. A chart tile's fresh visual is recovered from the embedded `data-component-id="chart"` spec, not only the dashboard's own `visualize` component.

### Three-tier refresh provenance (best → worst)

The refresh icon in the tile header reflects which tier applies: a **bolt** (⚡, direct MCP data refresh, no LLM) when `mcp_tool_name` is stamped, otherwise a **⟳** (LLM re-run).

| Tier | When | Path | Determinism |
|------|------|------|-------------|
| **1 — MCP replay** | tile has `mcp_tool_name` (single data tool produced the chart) | `POST /v1/dashboards/<id>/tiles/<tile_id>/refresh-mcp` → re-run just that tool → re-apply stored chart spec | Fully deterministic; values update, visual unchanged |
| **2 — Presentation-locked re-run** | chart tile, no MCP stamp | `POST .../refresh` with the source prompt **scoped** to "reproduce ONLY the {type} chart — no narrative" | Answer *is* the chart, so extraction is clean |
| **3 — Embedded-spec extraction** | tables / fallback | `POST .../refresh` re-runs the prompt; extract the embedded `data-component-id` `data-spec` from the answer HTML | Best-effort; guarded by the non-destructive invariant |

```
User clicks tile refresh control
         ↓
_refreshTile(tileId)
  ├── tile.mcp_tool_name → POST .../tiles/<tile_id>/refresh-mcp   (Tier 1, bolt)
  └── else               → POST .../tiles/<tile_id>/refresh       (Tier 2/3, ⟳)
         ↓ (Tier 2/3)
dashboard_routes.refresh_tile()
  ├── guard: reject degenerate source_prompt ('you'/'assistant'/empty) → 422 invalid_source_prompt
  ├── chart tile w/o mcp stamp → presentation-locked effective_prompt
  ├── run_agent_execution() in the tile's source_session_id, profile_override = tile.profile_id
  └── return { task_id }
         ↓
_pollTileRefresh(tileId, taskId)
  ├── prefer embedded data-component-id="chart" spec (carries fresh data + viz)
  ├── else rebuild from embedded dashboard table data-spec + locked chart type
  ├── NON-DESTRUCTIVE GUARD: if no chart extracted but tile had one → keep prior spec
  └── PUT .../tiles/<tile_id> + re-render
```

### Turn-bound MCP stamping (`source_turn_id`)

The bolt/MCP stamp (`mcp_tool_name`, `mcp_args_json`) is written at **three** sites, and **all** bind to the specific turn that produced the tile via `source_turn_id` — never "the latest data tool in the session" (which cross-contaminated tiles across turns):

| Site | Path | Binding |
|------|------|---------|
| `dashboard_routes.add_tile` | hover-pin (REST) | `_mcp_stamp_for_turn(session_id, user_uuid, turn_id)` — scans only the matching turn's trace |
| `optimize_engine.py` | `tool_enabled` `TDA_Dashboard` pin | `UPDATE … WHERE source_session_id=? AND source_turn_id=? AND user_id=?` (current turn) |
| `conversation_engine.py` | `llm_only`+tools `TDA_Dashboard` pin | same turn-bound `UPDATE` |

A turn that used no single data tool gets **no stamp** (LLM re-run). The turn number is threaded `executor.current_turn_number` → `workflow_state["current_turn_number"]` → both component-dispatch contexts (`adapter.py` optimize path; `manager.py get_component_langchain_tools(current_turn_number=…)` conversation path) → `_handle_add_tile` records it. Helpers: `_load_session_workflow_history`, `_single_data_tool_in_trace`, `_mcp_stamp_for_turn`.

**Key invariant:** The tile's `spec` is self-contained G2Plot configuration with data embedded — the tile renders immediately from stored spec without any network call. The refresh path only runs when the user explicitly requests fresh data.

**Pending tiles:** When the LLM calls `add_tile` without content, the tile is persisted with `refresh_status='pending'`. `_loadAndRenderDashboard()` detects pending tiles and auto-triggers `_refreshTile()` for each — so the LLM can add a tile and the data populates automatically when the panel opens.

**Gating:** the capability-creating routes (`POST /v1/dashboards`, `POST …/tiles`) answer `403 component_not_available` when the Dashboard component is disabled for the user (`_assert_component_available` → `is_component_available`, global + per-user override). Reads, updates, refreshes and deletes stay open so a user can always inspect and clean up their own dashboards.

### Following changes made elsewhere

Every dashboard write is announced as `component_changed` (handler `_announce`, routes `@_signals.announces_route` with `aspect` `tiles` / `layout`). The open panel follows its own dashboard through `UderiaBus.onComponent('dashboard', …)` (debounced 300 ms reload of `_loadAndRenderDashboard`) — a tile an agent turn added, a pin from a card, a REST write, another tab. It does **not** reload for:

- `refreshed` — a refresh STARTED; its result lands as the tile's own update;
- a layout save this page made itself (`aspect: "layout"` with `own: true` — the bus marks the echo of the tab's own `X-Uderia-Client` request). A layout saved in **another** tab or by another client **is** applied, so two tabs agree;
- anything while dragging, resizing or in edit mode.

A `deleted` notice for the open dashboard closes the panel. Contract: CLAUDE.md *Component Signals* / [COMPONENT_ARCHITECTURE.md](COMPONENT_ARCHITECTURE.md#component-signals--how-components-talk-to-each-other).

### Save snapshot to Files

*Save snapshot to Files* (PROP-010) writes the dashboard as ONE PDF into the user's Files. The **server** writes it (`POST /v1/files/dashboard-snapshot` → `filestore.service.snapshot_dashboard` → `formats.dashboard_snapshot_pdf`) from the dashboard's own rows — tile order, titles, refresh times, agents, tables and text. Only what the browser draws is sent, as a PNG per tile id: a chart tile is drawn again off-screen in light colours from its stored spec (`_chartPicture`; a PDF is read on paper whatever the viewer's theme), any other drawn tile contributes its largest canvas/SVG (`_tilePicture`); a tile with no picture is written from its data. The snapshot's `source_json` records the dashboard and each tile's refresh time. A dashboard with no tiles is refused (`400 empty`). See [FILES_ARCHITECTURE.md](FILES_ARCHITECTURE.md).

---

## 8. Database Schema

Two tables in `tda_auth.db` (schema `39_dashboards.sql`):

### `dashboards`

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | `dash-{12 hex chars}` |
| `user_id` | TEXT | Foreign key to `users.id` (enforced by query scoping) |
| `name` | TEXT | Display name in switcher |
| `description` | TEXT | Optional; shown in inline card |
| `is_default` | INTEGER | Boolean; 0 or 1. Only one default per user (enforced by UPDATE before INSERT) |
| `auto_refresh_s` | INTEGER | Global auto-refresh interval. 0 = manual only |
| `theme` | TEXT | `'auto'` \| `'dark'` \| `'light'` — reserved for per-dashboard theme override |
| `layout_config` | TEXT | JSON; reserved for future custom grid settings (column count, row height) |
| `created_at`, `updated_at` | TEXT | ISO 8601 UTC |

### `dashboard_tiles`

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | `tile-{12 hex chars}` |
| `dashboard_id` | TEXT | FK → `dashboards(id)` ON DELETE CASCADE |
| `user_id` | TEXT | Redundant denormalization; enables user-scoped tile queries without JOIN |
| `title` | TEXT | Editable display name |
| `component_id` | TEXT | `'chart'` \| `'table'` \| `'kpi'` \| `'narrative'` \| `'knowledge_graph'` \| `'flow'` \| `'explain_plan'` \| `'action'` (the pinned component's id) |
| `spec` | TEXT | JSON string — G2Plot config with data embedded, or KPI/narrative spec |
| `html` | TEXT | Pre-rendered HTML (used for tables and KPIs as render fallback) |
| `source_prompt` | TEXT | User message that generated this visual — enables refresh |
| `source_session_id` | TEXT | Session context for refresh execution |
| `source_turn_id` | INTEGER | Turn that produced this tile. Binds MCP-refresh stamping to the originating turn so it never cross-contaminates tiles across turns. Recorded by both pin paths. *(migration via `auth/database.py` ALTER)* |
| `mcp_tool_name` | TEXT | Single data tool that produced the chart's data, when known — enables direct MCP replay (bolt icon). NULL ⇒ LLM re-run. *(migration via ALTER)* |
| `mcp_args_json` | TEXT | JSON args for `mcp_tool_name`, replayed verbatim on `refresh-mcp`. *(migration via ALTER)* |
| `profile_id` | TEXT | Profile used for original execution (resolved consistently with `profile_tag`) |
| `profile_tag` | TEXT | Human-readable tag (e.g. `OPTIM`) — shown as a colour-coded badge on the tile header. Resolved **by tag first** (override-aware); colour sourced from the profile's own `color` field so the tile badge matches the chat profile chip |
| `pos_col` | INTEGER | 0-indexed column-start in 12-column grid |
| `pos_row` | INTEGER | 0-indexed row-start |
| `tile_width` | INTEGER | Column span (1–12) |
| `tile_height` | INTEGER | Row span |
| `sort_order` | INTEGER | Secondary sort within same (pos_row, pos_col) for stable ordering |
| `last_refreshed` | TEXT | ISO timestamp of last successful refresh; displayed as relative time |
| `refresh_status` | TEXT | `'ok'` \| `'pending'` \| `'refreshing'` \| `'error'` |
| `created_at`, `updated_at` | TEXT | ISO 8601 UTC |

> **Migrations:** `mcp_tool_name`, `mcp_args_json`, and `source_turn_id` are added to pre-existing databases via self-applying `ALTER TABLE` statements in `auth/database.py` (not a new schema file) — zero manual deployment steps.

### Indexes

```sql
CREATE INDEX idx_dashboards_user      ON dashboards(user_id);
CREATE INDEX idx_dash_tiles_dashboard ON dashboard_tiles(dashboard_id);
CREATE INDEX idx_dash_tiles_user      ON dashboard_tiles(user_id);
```

---

## 9. REST API Endpoints

All endpoints require `Authorization: Bearer <jwt>`. Users can only access their own dashboards (enforced via `WHERE user_id = ?` on all queries).

| Method | Path | Handler | Notes |
|--------|------|---------|-------|
| `GET` | `/v1/dashboards` | `list_dashboards` | Returns array with `tile_count` via LEFT JOIN. Optional query params `source_session_id` + `source_prompt`: when both are provided, a second query against `dashboard_tiles` resolves `pinned: true` on matching dashboards |
| `POST` | `/v1/dashboards` | `create_dashboard` | Body: `{name, description?, is_default?}`; returns `{dashboard_id, name, created_at}` |
| `GET` | `/v1/dashboards/<id>` | `get_dashboard` | Returns dashboard + full tiles array; `spec` parsed from JSON string |
| `PUT` | `/v1/dashboards/<id>` | `update_dashboard` | Partial update: name, description, theme, auto_refresh_s, is_default |
| `DELETE` | `/v1/dashboards/<id>` | `delete_dashboard` | Cascades to tiles via FK |
| `GET` | `/v1/dashboards/<id>/tiles` | `list_tiles` | Returns tiles sorted by sort_order, created_at |
| `POST` | `/v1/dashboards/<id>/tiles` | `add_tile` | Auto-positions via `_auto_position()`; returns `{tile_id}` |
| `PUT` | `/v1/dashboards/<id>/tiles/<tile_id>` | `update_tile` | Partial update: title, spec, html, pos_col, pos_row, tile_width, tile_height, refresh_status, last_refreshed, source_prompt |
| `DELETE` | `/v1/dashboards/<id>/tiles/<tile_id>` | `delete_tile` | Scoped to user via dashboard ownership check |
| `POST` | `/v1/dashboards/<id>/tiles/<tile_id>/refresh` | `refresh_tile` | Tier 2/3 (⟳). Re-runs the source prompt in the tile's session with `profile_id` override; chart tiles use a presentation-locked prompt. Guards degenerate prompts (`422 invalid_source_prompt`). Returns `{task_id}` for polling |
| `POST` | `/v1/dashboards/<id>/tiles/<tile_id>/refresh-mcp` | `refresh_tile_mcp` | Tier 1 (⚡). Replays the stamped `mcp_tool_name` directly (no LLM) and re-applies the stored chart spec |
| `PUT` | `/v1/dashboards/<id>/layout` | `bulk_update_layout` | Body: `{tiles: [{id, pos_col, pos_row, tile_width, tile_height}]}`; batch UPDATE |

---

## 10. Session & Panel Lifecycle

### Panel open / close on session switch

`sessionManagement.js` closes the dashboard panel when the user switches sessions:

```javascript
// In the session-switch handler:
const dashPanel = document.getElementById('dashboard-split-panel');
if (dashPanel && dashPanel.classList.contains('dash-split--open')) {
    if (typeof window.closeDashboardSplitPanelForSession === 'function') {
        window.closeDashboardSplitPanelForSession(previousSessionId);
    }
}
```

`closeDashboardSplitPanelForSession(sessionId)` (exported by `dashboardHandler.js`) saves the active dashboard ID to `localStorage` under `dashboard_state_<sessionId>` before closing.

`restoreDashboardStateForSession(sessionId)` reads the saved state and re-opens the panel when switching back.

### Module state

`renderer.js` tracks the following module-level variables:

| Variable | Type | Purpose |
|----------|------|---------|
| `_currentDashboardId` | string \| null | Active dashboard; null when panel is closed |
| `_dashboardData` | object \| null | Cached `{id, name, tiles[]}` from last load |
| `_editMode` | boolean | Whether edit mode is active |
| `_autoRefreshTimer` | timer \| null | For future global auto-refresh support |
| `_tsUpdateTimer` | timer \| null | Interval that updates relative timestamps every 60 s |

All state is reset in `closeDashboardPanel()`.

---

## 11. Theme System

The entire dashboard canvas uses CSS custom properties — no hardcoded colours except the IFOC profile-type constants and the Uderia brand orange:

```css
/* All of these adapt to light/dark theme automatically */
var(--card-bg)           /* tile and panel background */
var(--border-primary)    /* tile borders, header separators */
var(--text-primary)      /* tile titles, body text */
var(--text-muted)        /* timestamps, secondary labels */
var(--bg-overlay)        /* panel header, dropdown backgrounds */
var(--shadow-xl)         /* dropdown and modal shadows */
```

Hardcoded only for semantic identity:

```javascript
const C = {
  accent:  '#F15F22',  // Uderia orange — dashboard accent colour
  purple:  '#9333ea',  // Edit mode highlight
  blue:    '#3b82f6',  // Informational
  green:   '#4ade80',  // Success states
};
```

**IFOC profile badge colours** (canonical, matching `configurationHandler.js`):

| Profile type | Colour |
|---|---|
| `tool_enabled` (Optimize) | `#9333ea` |
| `llm_only` (Ideate) | `#4ade80` |
| `rag_focused` (Focus) | `#3b82f6` |
| `genie` (Coordinate) | `#F15F22` |

All icons are monochrome inline SVGs using `currentColor` — no emoji, no multi-colour glyphs.

---

## 12. LLM Instructions

`instructions.json` provides intensity-keyed guidance to the LLM:

**none** — Component disabled; no guidance emitted.

**medium** — One paragraph: for ANY show/chart/plot/visualize request call `visualize` with `query` (natural language, not SQL), `content` (the current data answer), `tile_title` and `chart_type` when the user names one; never call `add_tile` unless the user explicitly asks to pin to an existing dashboard; do not create a dashboard automatically; other actions `create`, `open`, `list`, `delete`; no emoji.

**heavy** — The same, per action: `visualize` as the default, `add_tile` only on an explicit save/pin with an existing `dashboard_id` (+ optional `tile_width` / `tile_height`), `create` / `open` / `refresh` (optional `tile_id`) / `close` / `list` / `delete`, and the rules: do not call `open` after `create` or `add_tile`, do not pass SQL as `query`, no emoji.

Taste — tile sizing grid, query writing style, visual composition — lives in the companion skill `skills/builtin/dashboard-expert/`, which auto-assigns while the component is enabled.

---

## 13. Design Decisions

### No dedicated dashboard creation wizard

Dashboards are created either through natural language (`TDA_Dashboard action=create`) or inline in the picker modal when pinning. There is no separate "Create dashboard" form — consistent with the platform's conversational-first philosophy.

### Spec self-sufficiency

Every tile stores the complete G2Plot configuration object (including embedded data arrays) in the `spec` column. This means:

- Tiles render instantly on panel open with zero network calls
- The dashboard is readable offline (no re-query needed for display)
- Refresh is opt-in and explicit, not automatic on load

The trade-off is storage size. Large query results with thousands of rows are expected to be distilled before being embedded in a chart spec — the G2Plot renderer works best with aggregated data anyway.

### 12-column grid, not drag-to-position

The grid uses integer column/row positions rather than pixel-based drag positioning. This keeps the layout system simple, composable (rows pack naturally), and consistent with how professional BI tools (Grafana, Retool) represent layout. The `_auto_position()` algorithm implements a simple first-fit shelf packer.

### Server-side pin state, not client memory

The picker modal fetches pinned state from the server (`pinned` flag on `list_dashboards`) rather than maintaining an ephemeral JS `Set`. This is the only correct approach for a web app: the client has no reliable way to know what was pinned in previous page loads. The trade-off is one extra DB query per modal open (scoped to the current user, session, and prompt — fast index hit). The result is a UI that is always accurate regardless of page refresh, server restart, or multi-tab use.

### The pinned dashboard is shown after a pin

Pinning opens (or reloads) the target dashboard in the split panel, so the user sees where the tile landed. (An earlier design kept the panel closed after a pin; the picker's `addTileFromComponent` now opens it.)

### Re-execution uses existing task infrastructure

Tile refresh does not have its own agent pipeline. `refresh_tile` registers a background task in `APP_STATE["background_tasks"]` and calls `execution_service.run_agent_execution()` in the tile's `source_session_id` with the stored `source_prompt` and `profile_id` — the same task shape as `/v1/sessions/<id>/query`, emitting the same SSE notifications — and the renderer polls `/api/v1/tasks/<task_id>` (`_pollTileRefresh`). This means:

- Full LLM reasoning, tool calling, and plan execution runs normally
- Token usage, costs, and execution traces are recorded as usual
- All platform improvements (new prompts, provider changes) apply to scheduled refreshes automatically

### Split panel follows standard pattern

`#dashboard-split-panel` follows the identical `<aside>` + CSS class pattern as `#canvas-split-panel`, `#kg-split-panel`, `#scheduler-split-panel`, and `#flow-split-panel`. The `splitPanelResizer.js` handles width persistence and drag handles uniformly. This keeps the DOM structure, CSS variable inheritance, and resize behaviour identical for all canvases.

### Pin act on the answer row, not a floating overlay (replaces the MutationObserver)

The pin button used to be injected by a `MutationObserver` in `dashboardHandler.js` as an absolutely positioned hover overlay on every `[data-component-id]`. It covered the card's own controls and could not be found without hovering, so it was removed. The pin is now a server-drawn footer act of the answer row, declared per component by its manifest (`answer_surface.pin`), revealed by one visibility rule and handled by one delegated click handler in `ui.js` — new pinnable components opt in through their manifest, not through a list in shared code. See [§5](#5-pin-to-dashboard-flow).

---

## 14. File Reference

| File | Purpose |
|------|---------|
| `components/builtin/dashboard/manifest.json` | Tool definition, render targets, profile defaults |
| `components/builtin/dashboard/handler.py` | `DashboardComponentHandler` — 8 actions, inline payload construction, `_content_to_html` |
| `components/builtin/dashboard/renderer.js` | Full canvas renderer: grid, tiles, switcher, picker modal, inline cards |
| `components/builtin/dashboard/instructions.json` | Intensity-keyed LLM guidance |
| `src/trusted_data_agent/api/dashboard_routes.py` | 12 REST endpoints registered as `dashboard_bp` |
| [src/trusted_data_agent/api/files_routes.py](../../src/trusted_data_agent/api/files_routes.py) | `dashboard_snapshot` — `POST /v1/files/dashboard-snapshot` |
| [src/trusted_data_agent/filestore/service.py](../../src/trusted_data_agent/filestore/service.py) | `snapshot_dashboard()` — the server-written PDF |
| [static/js/ui.js](../../static/js/ui.js) | `_wirePinActs` / `_onPinAct` — the answer-row pin act |
| `schema/39_dashboards.sql` | DDL: `dashboards`, `dashboard_tiles` + indexes |
| `static/js/handlers/dashboardHandler.js` | Session save/restore of the open dashboard (no pin injection any more) |
| Gates | `test_dashboard_viz_typing.mjs`, `test_dashboard_content.py`, `test_dashboard_act_again.mjs`, `test_component_bus.mjs` §7, `test_component_signals.py` |
| [static/js/componentRenderers.js](../../static/js/componentRenderers.js) | `_renderers.set('dashboard', renderDashboard)` |
| [static/js/handlers/splitPanelResizer.js](../../static/js/handlers/splitPanelResizer.js) | `SPLIT_PANEL_IDS` includes `'dashboard-split-panel'` |
| [static/js/handlers/sessionManagement.js](../../static/js/handlers/sessionManagement.js) | Closes panel + saves state on session switch |
| [templates/index.html](../../templates/index.html) | `<aside id="dashboard-split-panel">` HTML structure |
| [src/trusted_data_agent/auth/database.py](../../src/trusted_data_agent/auth/database.py) | `_create_dashboard_tables()` called during DB init |
| [src/trusted_data_agent/main.py](../../src/trusted_data_agent/main.py) | Blueprint registration |
