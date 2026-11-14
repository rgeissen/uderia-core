/**
 * workspacePanelHandler.js
 *
 * "Workspace" resource panel — surfaces the persistent INSTANCES the user has
 * created with action components (dashboards, flows, canvases, scheduled tasks),
 * grouped into collapsible sections, one per ENABLED component for the active
 * profile. It mirrors the Knowledge-Graphs panel pattern but generalised so a new
 * instance-bearing component plugs in with a single registry entry.
 *
 * Design contract (phase 1):
 *  - A section appears only if its component is ENABLED for the active profile.
 *    Enablement is the full governance merge (global disabled → per-user override →
 *    per-profile componentConfig) computed server-side and returned as
 *    `profile_enabled` by GET /v1/profiles/<id>/components.
 *  - What a section lists follows the component manifest's `instances` (the field the
 *    component card's pill reads): 'user' — ALL the person's own ("profile-gate, show
 *    all"); 'agent' — the active agent's own (its list gets the profile id); 'page' —
 *    held in this page only. A section's list never receives the agent unless 'agent'.
 *  - The panel is a LAUNCHER, not an editor: each row exposes Open (delegates to the
 *    component's own split-panel) and Delete (inline two-step confirm). Full
 *    management lives in the component's own surface.
 *  - Section IDENTITY (icon, name, accent) comes from the component manifest via the
 *    components endpoint, so new components stay visually consistent for free.
 *  - Section BEHAVIOUR (list endpoint, fields, open/delete) comes from
 *    WORKSPACE_REGISTRY below. Adding a new instance-bearing component = add one entry.
 *  - A component with no instances of its own still gets a section when what it opens
 *    ON is a list (the Database Navigator lists its catalog SOURCES); an optional
 *    `trailer` hook may append uncounted sub-rows (its recently-viewed objects).
 *  - Theme-aware throughout: every colour is a CSS variable; icons are monochrome.
 */

import { nodesLabel, entitiesLabel, relationshipsLabel, sizeLabel, addedLabel, entityCountOf, draftBreakdown } from '../kgVocabulary.js?v=1.0';
import { state } from '../state.js';
import { exportInstance, importInstance } from './instancePortabilityUI.js?v=1.0';

import { coalescedFetch } from './requestCoalescer.js';
import { workspaceLabel } from './resourceBadge.js?v=1.3';
// ── Behaviour registry: one entry per instance-bearing component ───────────────
// `open(item)` and `del`/`deleteUrl` reuse the component's existing, proven openers
// (the same code paths sessionManagement.js uses to restore split panels).
const WORKSPACE_REGISTRY = {
    dashboard: {
        noun: 'dashboard',
        trustKind: 'dashboard',
        listUrl: '/api/v1/dashboards',
        itemsKey: 'dashboards',
        idField: 'id',
        labelField: 'name',
        emptyHint: 'Pin a chart or table from a conversation to build one.',
        meta: (it) => {
            const n = Number(it.tile_count || 0);
            return `${n} ${n === 1 ? 'tile' : 'tiles'}`;
        },
        // "+" asks for a name inline (the panel switcher's own New does the same).
        createPrompt: 'Dashboard name',
        create: async (profileId, name) => {
            const r = await fetch('/api/v1/dashboards', {
                method: 'POST', headers: { 'Content-Type': 'application/json', ..._authHeaders() }, body: JSON.stringify({ name }),
            });
            if (!r.ok) return 'Could not create the dashboard';
            const d = await r.json();
            const m = await import('/api/v1/components/dashboard/renderer');
            await m.openDashboardPanel({ dashboard_id: d.dashboard_id, name: d.name || name });
            return true;
        },
        deleteUrl: (id) => `/api/v1/dashboards/${id}`,
        open: (it) => import('/api/v1/components/dashboard/renderer')
            .then(m => m.openDashboardPanel({ dashboard_id: it.id, name: it.name })),
    },
    flow: {
        noun: 'flow',
        trustKind: 'flow',
        listUrl: '/api/v1/flows',
        itemsKey: 'flows',
        idField: 'flow_id',
        labelField: 'name',
        emptyHint: 'Build an automated multi-step workflow.',
        meta: (it) => {
            const nodes = (it.definition && Array.isArray(it.definition.nodes)) ? it.definition.nodes.length : 0;
            // PROP-007: open template slots are named on the row (served as open_slot_count).
            const open = Number(it.open_slot_count) || 0;
            return `${nodes} ${nodes === 1 ? 'node' : 'nodes'}` + (open ? ` · ${open} slot${open === 1 ? '' : 's'} to fill` : '');
        },
        // PROP-007: the section's "New" action opens the topology picker (shape first).
        create: (profileId) => import('/api/v1/components/flow/renderer')
            .then(m => m.openTopologyPicker({ sourceProfileId: profileId || null, onCreated: loadWorkspacePanel })),
        deleteUrl: (id) => `/api/v1/flows/${id}`,
        open: (it) => import('/api/v1/components/flow/renderer')
            .then(m => m.openFlowSplitPanel({
                view: 'canvas',
                flow: it,
                definition: it.definition || { nodes: [], edges: [], scopes: [] },
                action: 'open',
            })),
    },
    canvas: {
        noun: 'canvas',
        trustKind: 'canvas',
        listUrl: '/api/v1/canvases',
        itemsKey: 'canvases',
        idField: 'canvas_id',
        labelField: 'title',
        emptyHint: 'Ask the agent to draft code or a document.',
        meta: (it) => (it.language || 'text'),
        // "+" asks for a title inline; a new canvas starts as SQL, as the panel's own New does.
        createPrompt: 'Canvas title',
        create: async (profileId, name) => {
            const r = await fetch('/api/v1/canvases', {
                method: 'POST', headers: { 'Content-Type': 'application/json', ..._authHeaders() },
                body: JSON.stringify({ mode: 'create', title: name, content: '', language: 'sql', session_id: window.__currentSessionId || null }),
            });
            if (r.status === 409) return `A canvas named “${name}” already exists`;
            if (!r.ok) return 'Could not create the canvas';
            const c = await r.json();
            await _openCanvas(c.canvas_id);
            return true;
        },
        deleteUrl: (id) => `/api/v1/canvases/${id}`,
        open: (it) => _openCanvas(it.canvas_id),
    },
    scheduler: {
        noun: 'scheduled task',
        listUrl: '/api/v1/scheduled-tasks',
        itemsKey: 'tasks',
        idField: 'id',
        labelField: 'name',
        emptyHint: 'Schedule a recurring query for the agent to run.',
        isActive: (it) => !!it.enabled,
        meta: (it) => {
            const sched = it._human_schedule || it.schedule || '';
            const st = it.enabled ? 'active' : 'paused';
            return sched ? `${sched} · ${st}` : st;
        },
        // A task is created by the agent (TDA_Scheduler) from a request in words — the
        // "+" starts that request in the composer rather than offering a second form.
        createTitle: 'New scheduled task — ask the agent',
        create: () => _startInComposer('Schedule a task that '),
        deleteUrl: (id) => `/api/v1/scheduled-tasks/${id}`,
        // The scheduler split panel is a list view (no per-task open); it self-fetches.
        openTitle: 'Open the scheduled tasks',
        open: () => import('/api/v1/components/scheduler/renderer')
            .then(m => m.openSchedulerSplitPanel({ action: 'list' })),
    },
    knowledge_graph: {
        noun: 'knowledge graph',
        trustKind: 'ontology',
        listUrl: '/api/v1/knowledge-graph/list',
        itemsKey: 'knowledge_graphs',
        idField: 'kg_id',
        labelField: 'kg_name',
        emptyHint: 'No knowledge graph for this profile yet.',
        // Export / Import are the knowledge graph's OWN flows (JSON or an RDF serialization —
        // the same menu and dialog as the Knowledge Graphs tab), not the instance envelope.
        exportItem: (it, btn) => _kgModule().then(m => m.openKgExportMenu(btn, it.profile_id, it.kg_id)),
        importNew: (btn, onDone) => _kgModule().then(m => m.startKgImport(btn, onDone)),
        // Delete only what this agent OWNS outright. A graph merely assigned to it belongs
        // to another agent, and a subscribed copy is removed by unsubscribing (the
        // Knowledge Graphs tab offers that act) — neither is a delete from here.
        canDelete: (it, profileId) => !!profileId && it.profile_id === profileId && !it.is_subscribed,
        deleteUrl: (id) => `/api/v1/knowledge-graph/clear?kg_id=${encodeURIComponent(id)}`,
        meta: (it) => {
            return sizeLabel(it);   // "275 nodes · 15 entities" (kgVocabulary)
        },
        // Active-profile-relevant: KGs owned by OR assigned to the active profile
        // (mirrors knowledgeGraphPanelHandler._getKgStateForProfile.isAvailable).
        filterItems: (items, profileId) => (profileId ? items.filter(kg =>
            kg.profile_id === profileId ||
            (Array.isArray(kg.assigned_profiles) && kg.assigned_profiles.some(a => a && a.id === profileId))
        ) : []),
        // A graph is built from a source, not named into existence: "+" opens the graph
        // builders (Intelligence → Knowledge Graphs → New), the panel switcher's own route.
        createTitle: 'New knowledge graph…',
        create: () => import('/api/v1/components/knowledge_graph/renderer').then(m => m.openKgConstructors()),
        open: (it) => _openKnowledgeGraph(it),
        // PROP-008: the drafts on each graph's authored layer, listed under the graph
        // (uncounted — a draft is not a graph). Empty when OBDA-MODEL · OBDA-ACT is off or nothing is drafted.
        trailer: (items, profileId) => _kgDraftRows(items, profileId),
    },
    memory: {
        noun: 'memory',
        // Always exactly two buckets: Local (this agent's own) + Global (shared).
        listUrl: (profileId) => `/api/v1/memory/buckets?profile_id=${encodeURIComponent(profileId || '')}`,
        itemsKey: 'buckets',
        idField: 'key',
        labelField: 'label',
        emptyHint: 'Agent memory.',
        noDelete: true,  // a bucket is not deleted; items are, inside the split canvas
        meta: (it) => `${it.count} ${it.count === 1 ? 'item' : 'items'}`,
        openTitle: 'Open this memory',
        // "+" opens this agent's memory with the add field focused.
        createTitle: 'Add a memory',
        create: (profileId) => import('/api/v1/components/memory/renderer')
            .then(m => m.openMemorySplitPanel(profileId ? { profile_id: profileId } : { key: 'global' }))
            .then(() => { const f = document.getElementById('mem-add-input'); if (f) f.focus(); }),
        open: (it) => import('/api/v1/components/memory/renderer')
            .then(m => m.openMemorySplitPanel(it)),
    },
    needs_input: {
        noun: 'question',
        // PROP-011: the questions this agent put to the person — pending first, then the history
        // of what was asked and answered. The list narrows to the agent; the panel's header
        // carries the count across every agent so no open question hides.
        listUrl: (profileId) => `/api/v1/input-requests?status=all&limit=50${profileId ? `&profile_id=${encodeURIComponent(profileId)}` : ''}`,
        itemsKey: 'items',
        idField: 'id',
        labelField: 'kind',
        emptyHint: 'Nothing asked yet — a turn that cannot go on without you stops here.',
        noDelete: true,  // a question is a record of what was asked; it is answered, never deleted
        noCreate: true,  // only an agent raises one
        label: (it) => ((it.items && it.items[0] && it.items[0].question) || it.kind || 'question'),
        meta: (it) => (it.status === 'pending' ? 'waiting for you' : it.status),
        openTitle: 'Open the questions',
        open: (it) => import('/api/v1/components/needs_input/renderer')
            .then(m => m.openNeedsInputPanel({ profile_id: it.profile_id, focus: it.id })),
    },
    database_navigator: {
        noun: 'source',
        // The navigator has no instances; its rows are the catalog SOURCES this agent
        // can read (the same resolution the tool and the split panel use — one row per
        // source, the resolved default carries the active dot). Opening a row opens
        // the navigator on that source. Sources are configured elsewhere (Database
        // Connections / the profile's MCP server), so there is nothing to delete here.
        listUrl: (profileId) => `/api/v1/database-navigator/sources?profile_id=${encodeURIComponent(profileId || '')}`,
        itemsKey: 'sources',
        idField: 'id',
        labelField: 'label',
        emptyHint: 'No catalog source: add a Database Connection (Components → Connectors) or bind the agent to an MCP server that lists tables.',
        noDelete: true,
        openTitle: 'Open the navigator on this source',
        // Sources are Database Connections, managed in one place — "+" goes there.
        createTitle: 'Add a database connection',
        create: () => { if (typeof window.openPlatformConnector === 'function') window.openPlatformConnector('uderia-database'); },
        // `reason` is set only on the resolved default (resolution.py) — that is the source
        // the agent's tool will read unless told otherwise.
        isActive: (it) => !!it.reason,
        meta: (it) => {
            const what = it.kind === 'mcp' ? 'MCP tools' : (it.detail || it.driver || 'connection');
            return it.reason ? `${what} · default` : what;
        },
        open: (it, profileId) => import('/api/v1/components/database_navigator/renderer')
            .then(m => m.openDatabaseNavigatorPanel({ source: it.id, profile_id: profileId })),
        // Recently viewed objects (the navigator's own per-browser memory) — quiet
        // sub-rows under the sources; each opens the navigator AT that object.
        trailer: (items, profileId) => _dbnavRecentRows(items, profileId),
    },
    explain_plan: {
        noun: 'plan',
        // Plans are not stored on the server: the panel keeps the last few explained in
        // this page (the SQL canvas's Explain, or an agent's TDA_ExplainPlan). `list`
        // reads them locally instead of fetching; a reload starts the list empty.
        list: () => import('/api/v1/components/explain_plan/renderer').then(m => m.panelPlans()),
        idField: 'i',
        labelField: 'label',
        emptyHint: 'Plans explained in this page appear here — from Explain in the SQL canvas, or when the agent explains a query. They are not kept after a reload.',
        noDelete: true,
        // No "+": a plan is derived from a statement (Explain in the SQL canvas, or the
        // agent's TDA_ExplainPlan) — there is nothing to create from an empty name.
        meta: (it) => {
            const s = `${it.steps} ${it.steps === 1 ? 'step' : 'steps'}`;
            return it.findings ? `${s} · ${it.findings} ${it.findings === 1 ? 'finding' : 'findings'}` : s;
        },
        open: (it) => import('/api/v1/components/explain_plan/renderer').then(m => m.showPanelPlan(it.i)),
    },
    action: {
        noun: 'action',
        // PROP-008 — the action component has no instances: its rows are the signed action
        // types on the agent's graph (the catalog the Actions panel and the agent's own
        // list_actions read). "+" DEFINES one: the Actions Designer on the agent's own graph
        // with the form prepared (concept, operation, types, role). Nothing is deleted here;
        // a row opens the panel on that action.
        listUrl: (profileId) => `/api/v1/actions?profile_id=${encodeURIComponent(profileId || '')}`,
        itemsKey: 'actions',
        idField: 'action',
        labelField: 'action',
        // the contract's target, in brackets: an action writes ONE entity of the ontology,
        // and the row says which — `close_lead (Policing Core.Lead)`
        label: (it) => it.entity ? `${it.action} (${it.entity})` : it.action,
        emptyHint: 'No action on this agent’s knowledge graph yet.',
        // the empty section offers the act it is missing, where the eye already is
        emptyAct: 'Define an action',
        createTitle: 'Define an action',
        create: (profileId) => import('./actionDesigner.js?v=1.16')
            .then(m => m.openDesignerForAgent(profileId, { define: true }))
            .then((why) => { if (why && typeof window.showAppBanner === 'function') window.showAppBanner(why, 'warning'); }),
        noDelete: true,
        openTitle: 'Open the OBDA-ACT panel on this action',
        // the active dot: this agent is granted it and its signature is intact
        isActive: (it) => !!it.granted && ((it.trust || {}).state === 'trusted'),
        meta: (it) => {
            const st = (it.trust || {}).state;
            const trust = st === 'trusted' ? 'signed' : st === 'changed' ? 'changed since signed' : 'not signed';
            return [it.operation, it.posture, trust, it.granted ? 'granted' : 'not granted'].filter(Boolean).join(' · ');
        },
        open: (it, profileId) => import('/api/v1/components/action/renderer')
            .then(m => m.openActionsPanel({ profile_id: profileId, focus: it.action })),
    },
    // PROP-010 Files — the launcher: the 8 newest files, then "All files (N) →" opens the browser.
    files: {
        noun: 'file',
        idField: 'id',
        labelField: 'filename',
        emptyHint: 'Press Keep under a table, a chart or an answer — or attach a file to a conversation.',
        list: async () => {
            const r = await coalescedFetch('/api/v1/files?limit=8', { headers: _authHeaders() });
            if (!r.ok) return [];
            const d = await r.json();
            const items = Array.isArray(d.files) ? d.files.slice() : [];
            items._total = d.file_count != null ? d.file_count : items.length;
            return items;
        },
        total: (items) => (items && items._total != null ? items._total : (items || []).length),
        isActive: (it) => !!it.is_new,
        meta: (it) => {
            const ifoc = { tool_enabled: 'Optimize', llm_only: 'Ideate', rag_focused: 'Focus', genie: 'Coordinate' }[it.profile_type];
            return [it.type_label, it.size_label, ifoc || (it.profile_tag ? `@${it.profile_tag}` : null)].filter(Boolean).join(' · ');
        },
        createTitle: 'Add files',
        create: () => _filesPanel({ add: true }),
        openTitle: 'Open in Files',
        open: (it) => _filesPanel({ focus: it.id }),
        exportItem: (it) => _downloadFile(it),
        deleteUrl: (id) => `/api/v1/files/${encodeURIComponent(id)}`,
        trailer: (items) => {
            if (!items || !items.length) return null;
            const a = document.createElement('button');
            a.type = 'button';
            a.className = 'ws-files-all';
            a.textContent = `All files (${items && items._total != null ? items._total : (items || []).length}) →`;
            a.addEventListener('click', () => _filesPanel({}));
            return a;
        },
    },
    obda_read: {
        noun: 'data product',
        // OBDA-READ has no instances: its rows are the signed data products on the agent's
        // graph (the catalog the OBDA-READ panel and the agent's own list_products read).
        // "+" DEFINES one: the Products Designer on the agent's graph. Nothing is deleted
        // here; a row opens the panel on that product.
        listUrl: (profileId) => `/api/v1/data-products?profile_id=${encodeURIComponent(profileId || '')}`,
        itemsKey: 'products',
        idField: 'product',
        labelField: 'product',
        emptyHint: 'No data product on this agent’s knowledge graph yet.',
        emptyAct: 'Define a product',
        createTitle: 'Define a product',
        create: (profileId) => _openProductsDesigner(profileId),
        noDelete: true,
        openTitle: 'Open the OBDA-READ panel on this product',
        // the active dot: its signature is intact and the agent may ask it (on its surface)
        isActive: (it) => ((it.trust || {}).state === 'trusted') && it.on_surface !== false,
        meta: (it) => {
            const st = (it.trust || {}).state;
            const trust = st === 'trusted' ? 'signed' : st === 'changed' ? 'changed since signed' : 'not signed';
            const where = it.on_surface === true ? 'on this agent’s surface' : it.on_surface === false ? 'not on this agent’s surface' : '';
            return [it.version ? `v${it.version}` : '', it.validated ? 'validated' : 'not validated', trust, where].filter(Boolean).join(' · ');
        },
        open: (it, profileId) => import('/api/v1/components/obda_read/renderer')
            .then(m => m.openObdaReadPanel({ profile_id: profileId, focus: it.product })),
    },
    obda_model: {
        noun: 'concept',
        // OBDA-MODEL has no instances: its rows are the concepts the person added to the
        // agent's graph (their extension), each with its stage on the road. "+" opens
        // Govern on the agent's graph — where a draft is landed and published. Nothing is
        // deleted here; a row opens the panel on that concept.
        list: (profileId) => import('/api/v1/components/obda_model/renderer').then(m => m.workspaceConcepts(profileId)),
        idField: 'name',
        labelField: 'name',
        emptyHint: 'Nothing added to this agent’s model yet. A proposal in the conversation, or Propose a concept under an answer, starts one.',
        emptyAct: 'Open Govern',
        createTitle: 'Open Govern on this agent’s graph',
        create: (profileId) => _openGovernForAgent(profileId),
        noDelete: true,
        openTitle: 'Open the OBDA-MODEL panel on this concept',
        // the active dot: published and signed
        isActive: (it) => (it.status || 'draft') === 'published' && (!it.trust || it.trust === 'trusted'),
        meta: (it) => {
            const stage = it.status || 'draft';
            const owed = stage === 'published' && it.trust && it.trust !== 'trusted' ? String(it.trust).replace(/_/g, ' ') : '';
            return [it.entity_type || '', stage, owed].filter(Boolean).join(' · ');
        },
        open: (it, profileId) => import('/api/v1/components/obda_model/renderer')
            .then(m => m.openObdaModelPanel({ profile_id: profileId, focus: it.name })),
    },
};

// The agent's graph, for the two OBDA planes' "+" acts (the product and the extension live
// on it): the catalog endpoints resolve it per agent the way the agent's own tool does.
async function _agentGraph(profileId) {
    try {
        const r = await fetch(`/api/v1/data-products?profile_id=${encodeURIComponent(profileId || '')}`, { headers: _authHeaders() });
        const d = r.ok ? await r.json() : {};
        return d.kg_id ? { kg_id: d.kg_id, kg_name: d.kg_name || d.kg_id } : null;
    } catch (_) { return null; }
}
async function _openProductsDesigner(profileId) {
    const g = await _agentGraph(profileId);
    if (!g) { if (typeof window.showAppBanner === 'function') window.showAppBanner('No knowledge graph is bound to this agent — the Products Designer opens on a graph.', 'warning'); return; }
    if (typeof window.openKgProductsDialog === 'function') return window.openKgProductsDialog(g.kg_id, g.kg_name);
}
async function _openGovernForAgent(profileId) {
    const g = await _agentGraph(profileId);
    if (!g) { if (typeof window.showAppBanner === 'function') window.showAppBanner('No knowledge graph is bound to this agent — Govern opens on a graph.', 'warning'); return; }
    UderiaBus.emit('uderia:open-govern', { kgId: g.kg_id, kgName: g.kg_name });
}

// ── Database Navigator: recently viewed sub-rows ───────────────────────────────
// Reads the navigator's own localStorage record (`dbnav_last_v1`, written by the
// split panel on every selection); never a network call. Rendered as a muted list
// under the source rows and NOT counted in the section badge — the badge counts
// sources, the things you can open a navigator on.
const _DBNAV_LS_KEY = 'dbnav_last_v1';
function _dbnavRecentRows(sources, profileId) {
    let recent = [];
    try { recent = (JSON.parse(localStorage.getItem(_DBNAV_LS_KEY) || 'null') || {}).recent || []; } catch (_) { recent = []; }
    recent = recent.filter(r => r && r.db && r.name).slice(0, 5);
    if (!recent.length || !sources.length) return null;
    const dflt = sources.find(s => !!s.reason) || sources[0];
    const wrap = document.createElement('div');
    wrap.className = 'ws-trailer';
    wrap.innerHTML = `<div class="ws-trailer-label">Recently viewed</div>`;
    for (const r of recent) {
        const row = document.createElement('div');
        row.className = 'ws-row ws-row--sub';
        row.innerHTML =
            `<span class="ws-row-dot"></span>` +
            `<div class="ws-row-main"><span class="ws-row-name" title="${_esc(r.db)}.${_esc(r.name)}">${_esc(r.db)}.${_esc(r.name)}</span></div>` +
            `<div class="ws-row-actions"><button class="ws-btn ws-open" title="Open" aria-label="Open">${_ICON_OPEN}</button></div>`;
        const openFn = () => import('/api/v1/components/database_navigator/renderer')
            .then(m => m.openDatabaseNavigatorPanel({ source: dflt.id, database: r.db, object: r.name, profile_id: profileId }))
            .catch(err => console.error('[Workspace] open failed:', err));
        row.querySelector('.ws-row-main').addEventListener('click', openFn);
        row.querySelector('.ws-open').addEventListener('click', (e) => { e.stopPropagation(); openFn(); });
        wrap.appendChild(row);
    }
    return wrap;
}

// PROP-008 OBDA-MODEL — the person's OWN extension of each listed graph: every concept they
// added, with its stage on the road (draft → validated → deployed → published) and, once
// published, the trust act still owed (unsigned · changed). Rendered asynchronously into the
// trailer the Workspace appends; one GET per graph (bounded). A row opens its graph — a
// concept not yet published WITH the author's drafts (drawn dashed), a published one in the
// model's own view — centred on it. Silent when OBDA-MODEL · OBDA-ACT is off or the person extended nothing.
function _kgDraftRows(graphs, profileId) {
    // listed ONCE: where the OBDA-MODEL component is enabled for this agent, its own section
    // carries the person's concepts, and the graph's trailer steps aside
    if (typeof _enabledWorkspaceIds !== 'undefined' && _enabledWorkspaceIds.has('obda_model')) return null;
    const list = (graphs || []).filter(g => g && g.kg_id).slice(0, 4);
    if (!list.length) return null;
    const wrap = document.createElement('div');
    wrap.className = 'ws-trailer ws-trailer--drafts';
    wrap.style.display = 'none';
    const token = localStorage.getItem('tda_auth_token');
    const fill = async () => {
        const rows = [];
        for (const g of list) {
            try {
                const r = await fetch(`/api/v1/knowledge-graph/authored?kg_id=${encodeURIComponent(g.kg_id)}`,
                                      { headers: { 'Authorization': `Bearer ${token}` } });
                if (!r.ok) continue;
                const d = await r.json();
                // the person's own extension only (an extension is one extender's layer)
                const exts = Array.isArray(d.extensions) ? d.extensions : [];
                const mine = exts.filter(x => x && x.mine).map(x => x.extension_id);
                const trustOf = (id) => ((exts.find(x => x && x.extension_id === id) || {}).trust || {}).state || '';
                for (const n of (d.nodes || [])) {
                    if (exts.length && n.extension_id && !mine.includes(n.extension_id)) continue;
                    rows.push({ g, n, trust: trustOf(n.extension_id || mine[0]) });
                }
            } catch (_) { /* a listing that fails lists nothing */ }
        }
        if (!rows.length) { wrap.remove(); return; }
        const graphsShown = new Set(rows.map(x => x.g.kg_id));
        const only = graphsShown.size === 1 ? rows[0].g : null;
        wrap.innerHTML = `<div class="ws-trailer-label">Your extension${only && only.kg_name ? ` · ${_esc(only.kg_name)}` : ''}</div>`;
        for (const { g, n, trust } of rows) {
            const row = document.createElement('div');
            const published = (n.status || 'draft') === 'published';
            row.className = `ws-row ws-row--sub ws-row--draft${published ? ' ws-row--published' : ''}`;
            const leaf = String(n.name || '').split('.').pop();
            // the concept in words (entity · 9 attributes), its stage, and — published — the act owed
            const what = draftBreakdown([n]) || n.entity_type || '';
            const owed = published && trust && trust !== 'trusted' ? ` · ${trust.replace(/_/g, ' ')}` : '';
            const rule = !published && n.decision && n.decision.rule ? ` · ${n.decision.rule.replace(/_/g, ' ')}` : '';
            const where = only ? '' : ` · ${g.kg_name || ''}`;
            row.innerHTML =
                `<span class="ws-row-dot ws-row-dot--draft"></span>` +
                `<div class="ws-row-main"><span class="ws-row-name" title="${_esc(n.name)}">${_esc(leaf)}</span>` +
                `<span class="ws-row-meta">${_esc(what)} · ${_esc(n.status || 'draft')}${_esc(owed)}${_esc(rule)}${_esc(where)}</span></div>` +
                `<div class="ws-row-actions"><button class="ws-btn ws-open" title="Open ${_esc(leaf)} in its graph" aria-label="Open ${_esc(leaf)}">${_ICON_OPEN}</button></div>`;
            // not yet published: the author's drafts view; published: the model's own view
            const openFn = () => _openKnowledgeGraph(g, { includeDrafts: !published, focusNames: [n.name] });
            row.querySelector('.ws-row-main').addEventListener('click', openFn);
            row.querySelector('.ws-open').addEventListener('click', (e) => { e.stopPropagation(); openFn(); });
            wrap.appendChild(row);
        }
        wrap.style.display = '';
    };
    fill();
    return wrap;
}

// ── Monochrome icons (single-colour, currentColor) ─────────────────────────────
const _ICON_OPEN = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M19 13v6a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1h6"/></svg>`;
const _ICON_TRASH = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M5 7l1 13a1 1 0 001 1h10a1 1 0 001-1l1-13M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg>`;
const _FALLBACK_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;
const _ICON_EXPORT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2"/></svg>`;
const _ICON_NEW = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>`;
const _ICON_IMPORT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V3m0 0L8 7m4-4l4 4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2"/></svg>`;

const PANEL_CONTENT_ID = 'workspace-panel-content';
const TAB_SELECTOR = '.resource-tab[data-type="workspace"]';

let _loading = false;
let _again = false;   // a reload asked for while one runs is run after it, never dropped

// ── Public entry point (called on profile change + on tab activation) ──────────
let _enabledWorkspaceIds = new Set();   // the components enabled for the active agent (this load)

export async function loadWorkspacePanel() {
    const container = document.getElementById(PANEL_CONTENT_ID);
    if (!container) return;
    if (_loading) { _again = true; return; }
    _loading = true;
    _injectWorkspaceCSS();

    const profileId = state.currentResourcePanelProfileId || window.currentResourcePanelProfileId || null;

    try {
        const enabled = await _fetchEnabledWorkspaceComponents(profileId);
        _enabledWorkspaceIds = new Set(enabled.map(c => c.component_id));

        if (enabled.length === 0) {
            _setTabState(false);
            container.innerHTML = '';
            return;
        }

        _setTabState(true, null);   // figures follow once the sections are listed
        container.innerHTML = enabled.map(() => `<div class="ws-skel-row"></div>`).join('');

        const sections = await Promise.all(enabled.map(async (comp) => {
            const reg = WORKSPACE_REGISTRY[comp.component_id];
            // Whose rows these are is the manifest's `instances`, the same field the
            // component card's pill reads: only an 'agent' section is listed FOR the agent;
            // a 'user' or 'page' section lists the person's own whichever agent is shown.
            const scope = comp.instances === 'agent' ? profileId : null;
            let items = [];
            try {
                if (typeof reg.list === 'function') {   // a component whose rows live in the page, not on the server
                    items = (await reg.list(scope)) || [];
                    return { comp, reg, items, profileId };
                }
                const url = (typeof reg.listUrl === 'function') ? reg.listUrl(scope) : reg.listUrl;
                const r = await coalescedFetch(url, { headers: _authHeaders() });
                if (r.ok) {
                    const data = await r.json();
                    items = Array.isArray(data[reg.itemsKey]) ? data[reg.itemsKey] : [];
                    if (typeof reg.filterItems === 'function') items = reg.filterItems(items, scope);
                }
            } catch (_) { /* component endpoint unavailable — empty section */ }
            return { comp, reg, items, profileId };
        }));

        _renderSections(container, sections);
        // The sum of the sections' own totals — the figure each section header shows.
        const total = sections.reduce((n, s) => n + _sectionTotal(s.reg, s.items), 0);
        _setTabState(true, total, sections.length);
    } catch (e) {
        console.error('[Workspace] load failed:', e);
        container.innerHTML = `<div class="ws-empty">Could not load workspace items.</div>`;
    } finally {
        _loading = false;
        if (_again) { _again = false; loadWorkspacePanel(); }
    }
}

// The Workspace follows changes made elsewhere without a page reload:
//   · a component switched on/off for an agent (resource panel, profile editor);
//   · a turn that may have created an instance (a flow, dashboard, canvas, memory).
// Only the active agent's toggle matters; a turn refresh is skipped while the panel is
// not in the page (nothing to update).
function _refreshIfShown(profileId) {
    const current = state.currentResourcePanelProfileId || window.currentResourcePanelProfileId || null;
    if (profileId && current && profileId !== current) return;
    if (!document.getElementById(PANEL_CONTENT_ID)) return;
    loadWorkspacePanel();
}
if (typeof document !== 'undefined' && !window.__workspaceRefreshWired) {
    window.__workspaceRefreshWired = true;
    // `uderia:components-changed` is read by the resource panel's ONE subscriber (resourceSync.js,
    // wired in main.js): it re-runs the per-profile load, which reloads this panel and primes the gate.
    UderiaBus.on('uderia:turn-complete', () => _refreshIfShown(null));
    // Every component's changes — a file kept, a graph drafted, an action authored, a plan explained,
    // a dashboard or flow created — from anywhere (this page, another tab, a REST call, an agent turn):
    // ONE subscriber, coalesced, reloads the sections (components/signals.py).
    let _wsChangeTimer = null;
    UderiaBus.onComponent('*', () => {
        clearTimeout(_wsChangeTimer);
        _wsChangeTimer = setTimeout(() => _refreshIfShown(null), 300);
    });
}

// ── Enablement gate ────────────────────────────────────────────────────────────
async function _fetchEnabledWorkspaceComponents(profileId) {
    if (!profileId) return [];  // resource panel always has an active profile; be safe
    let comps = [];
    try {
        const r = await coalescedFetch(`/api/v1/profiles/${profileId}/components`, { headers: _authHeaders() });
        if (!r.ok) return [];
        const data = await r.json();
        comps = data.components || [];
    } catch (_) {
        return [];
    }
    return comps
        .filter(c => WORKSPACE_REGISTRY[c.component_id] && c.profile_enabled === true)
        // Alphabetical by the name the section shows — the rule the Components list uses.
        .sort((a, b) => String(a.display_name || a.component_id || '').localeCompare(String(b.display_name || b.component_id || '')));
}

// ── Rendering ──────────────────────────────────────────────────────────────────
function _renderSections(container, sections) {
    container.innerHTML = '';
    for (const s of sections) container.appendChild(_buildSection(s));
    if (window.TrustBadge) window.TrustBadge.mountArtifactBadges(container);
}

function _buildSection({ comp, reg, items, profileId }) {
    const accent = comp.icon_accent || 'var(--teradata-orange)';

    // The section is a wrapper around a <details> and its header actions. The "New" /
    // "Import" buttons must NOT live inside the <summary>: a summary is one toggle, and a
    // control nested in it is unreachable or mis-announced for keyboard and screen-reader
    // users (Chrome flags it as an error-level issue). So the summary carries only the
    // section's identity, count and chevron, with fixed slots where the buttons go, and
    // the buttons sit in a sibling layer laid exactly over the header row — the same
    // layout, so the slots and the buttons line up whatever the count's width.
    const section = document.createElement('div');
    section.className = 'ws-section';
    const details = document.createElement('details');
    details.className = 'ws-sec-details';
    // Collapsed by default — the user expands the component groups they want.

    const titles = _titles(comp, reg);
    const total = _sectionTotal(reg, items);
    const count = `<span class="ws-sec-count">${total}</span>`;
    const chevron = `<svg class="ws-sec-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>`;
    const spacer = `<span class="ws-sec-import-spacer" aria-hidden="true"></span>`;
    const summary = document.createElement('summary');
    summary.className = 'ws-sec-head';
    // Import slot BEFORE the count so the count always sits immediately left of the
    // chevron and lines up vertically down the list; every section reserves both slots
    // (a generic "New" for a component that declares `create` — flows → the topology
    // picker — and "Import"), so the counts align whether or not it offers them.
    summary.innerHTML =
        `<span class="ws-sec-icon" style="color:${_esc(accent)}">${comp.icon_svg || _FALLBACK_ICON}</span>` +
        `<span class="ws-sec-name">${_esc(comp.display_name || comp.component_id)}</span>` +
        spacer + spacer + count + chevron;
    details.appendChild(summary);

    const actions = document.createElement('div');
    actions.className = 'ws-sec-actions';
    actions.innerHTML =
        `<span class="ws-sec-actions-fill"></span>` +
        (typeof reg.create === 'function' ? `<button type="button" class="ws-sec-import ws-sec-new" title="${_esc(titles.create)}" aria-label="${_esc(titles.create)}">${_ICON_NEW}</button>` : spacer) +
        (_canImport(comp, reg) ? `<button type="button" class="ws-sec-import ws-sec-imp" data-icon-only="1" title="${_esc(titles.import)}" aria-label="${_esc(titles.import)}">${_ICON_IMPORT}</button>` : spacer) +
        // invisible twins of the count and chevron: they give this row the summary's widths
        `<span class="ws-sec-count ws-sec-ghost" aria-hidden="true">${total}</span>` +
        `<span class="ws-sec-chevron ws-sec-ghost" aria-hidden="true"></span>`;
    actions.querySelector('.ws-sec-new')?.addEventListener('click', (e) => {
        e.preventDefault(); e.stopPropagation();
        if (reg.createPrompt) { _promptNew(details, body, reg, profileId); return; }
        try { Promise.resolve(reg.create(profileId)).catch(err => console.error('[Workspace] create failed:', err)); }
        catch (err) { console.error('[Workspace] create failed:', err); }
    });
    actions.querySelector('.ws-sec-imp')?.addEventListener('click', (e) => {
        e.preventDefault(); e.stopPropagation();
        if (typeof reg.importNew === 'function') {
            try { Promise.resolve(reg.importNew(e.currentTarget, loadWorkspacePanel)).catch(err => console.error('[Workspace] import failed:', err)); }
            catch (err) { console.error('[Workspace] import failed:', err); }
            return;
        }
        importInstance(
            { component_id: comp.component_id, display_name: comp.display_name, icon_svg: comp.icon_svg, icon_accent: comp.icon_accent },
            { onDone: loadWorkspacePanel },
        );
    });

    const body = document.createElement('div');
    body.className = 'ws-sec-body';
    if (items.length === 0) {
        body.innerHTML = `<div class="ws-sec-empty">${_esc(reg.emptyHint || 'Nothing here yet.')}` +
            (reg.emptyAct && typeof reg.create === 'function'
                ? ` <button type="button" class="ws-empty-act">${_esc(reg.emptyAct)}</button>` : '') + `</div>`;
        body.querySelector('.ws-empty-act')?.addEventListener('click', () => {
            try { Promise.resolve(reg.create(profileId)).catch(err => console.error('[Workspace] create failed:', err)); }
            catch (err) { console.error('[Workspace] create failed:', err); }
        });
    } else {
        items.forEach(it => body.appendChild(_buildRow(comp, reg, it, profileId)));
    }
    // Optional per-component trailer (e.g. the navigator's recently-viewed objects):
    // appended after the rows, never counted in the badge.
    if (typeof reg.trailer === 'function') {
        try { const t = reg.trailer(items, profileId); if (t) body.appendChild(t); } catch (_) { /* a trailer is a bonus */ }
    }
    details.appendChild(body);
    section.appendChild(details);
    section.appendChild(actions);
    return section;
}

function _buildRow(comp, reg, it, profileId) {
    const id = it[reg.idField];
    // A component may say its row's name in full (OBDA-ACT: the action AND the entity it
    // writes); otherwise the row is named by its label field.
    const label = (typeof reg.label === 'function' ? reg.label(it) : null) || it[reg.labelField] || '(untitled)';
    const meta = reg.meta ? reg.meta(it) : '';
    const ts = it.updated_at || it.created_at || it.last_run_at || null;
    const active = reg.isActive ? reg.isActive(it) : false;

    const t = _titles(comp, reg);
    const canDelete = _canDelete(reg, it, profileId);
    const row = document.createElement('div');
    row.className = 'ws-row';
    row.innerHTML =
        `<span class="ws-row-dot${active ? ' is-active' : ''}"></span>` +
        `<div class="ws-row-main">` +
            `<span class="ws-row-name" title="${_esc(label)}">${_esc(label)}</span>` +
            (reg.trustKind ? `<span class="inline-flex items-center ml-1" data-trust-artifact-kind="${reg.trustKind}" data-trust-artifact-id="${_esc(String(id))}"></span>` : '') +
            `<span class="ws-row-meta">${_esc(meta)}${ts ? ` · ${_esc(_timeAgo(ts))}` : ''}</span>` +
        `</div>` +
        `<div class="ws-row-actions">` +
            (_canExport(comp, reg) ? `<button class="ws-btn ws-export" title="${_esc(t.export)}" aria-label="${_esc(t.export + ' ' + label)}">${_ICON_EXPORT}</button>` : '') +
            `<button class="ws-btn ws-open" title="${_esc(t.open)}" aria-label="${_esc(t.open + ' ' + label)}">${_ICON_OPEN}</button>` +
            (canDelete ? `<button class="ws-btn ws-del" title="${_esc(t.del)}" aria-label="${_esc(t.del + ' ' + label)}">${_ICON_TRASH}</button>` : '') +
        `</div>`;

    const openFn = () => {
        try { Promise.resolve(reg.open(it, profileId)).catch(err => console.error('[Workspace] open failed:', err)); }
        catch (err) { console.error('[Workspace] open failed:', err); }
    };
    row.querySelector('.ws-row-main').addEventListener('click', openFn);
    row.querySelector('.ws-open').addEventListener('click', (e) => { e.stopPropagation(); openFn(); });
    row.querySelector('.ws-export')?.addEventListener('click', (e) => {
        e.stopPropagation();
        if (typeof reg.exportItem === 'function') {
            try { Promise.resolve(reg.exportItem(it, e.currentTarget)).catch(err => console.error('[Workspace] export failed:', err)); }
            catch (err) { console.error('[Workspace] export failed:', err); }
            return;
        }
        exportInstance(comp.component_id, id, label);
    });
    if (canDelete) _wireDelete(row, reg, id, label);
    return row;
}

// ── One rule for which icons a section shows, and what they are called ────────
// The same act wears the same icon and the same words in every section; an icon appears
// only where the component can actually perform the act:
//   +       reg.create                       (header)
//   Import  supports_instance_io | importNew (header)
//   Export  supports_instance_io | exportItem (row)
//   Open    always                           (row)
//   Delete  !noDelete, and canDelete(item)   (row)
// A component with its OWN import/export (the knowledge graph's JSON/RDF) supplies
// importNew/exportItem and they replace the instance-envelope flow.
function _canImport(comp, reg) { return !!comp.supports_instance_io || typeof reg.importNew === 'function'; }
function _canExport(comp, reg) { return !!comp.supports_instance_io || typeof reg.exportItem === 'function'; }
function _canDelete(reg, it, profileId) {
    if (reg.noDelete) return false;
    if (typeof reg.canDelete !== 'function') return true;
    try { return !!reg.canDelete(it, profileId); } catch (_) { return false; }
}
function _titles(comp, reg) {
    const noun = reg.noun || comp.display_name || comp.component_id;
    const a = /^[aeiou]/i.test(noun) ? 'an' : 'a';
    return {
        create: reg.createTitle || `New ${noun}`,
        import: `Import ${a} ${noun}`,
        export: `Export this ${noun}`,
        open:   reg.openTitle || `Open this ${noun}`,
        del:    `Delete this ${noun}`,
    };
}
function _kgModule() { return import('./knowledgeGraphPanelHandler.js?v=1.189'); }

// PROP-010 Files: the panel opens through the component's own renderer (the one URL every
// surface uses, so there is one panel instance); the row's export icon is a download through
// the governed route.
function _filesPanel(opts) {
    return import('/api/v1/components/files/renderer').then(m => m.openFilesPanel({ focus: opts.focus }).then(() => {
        if (opts.add) document.getElementById('files-split-add')?.click();
    })).catch(err => console.error('[Workspace] Files could not be opened:', err));
}

async function _downloadFile(it) {
    const r = await fetch(`/api/v1/files/${encodeURIComponent(it.id)}/download`, { headers: _authHeaders() });
    if (!r.ok) {
        let m = 'The download did not start.';
        try { m = (await r.json()).message || m; } catch (_) { /* default */ }
        if (typeof window.showAppBanner === 'function') window.showAppBanner(m, 'warning');
        return;
    }
    const cd = r.headers.get('Content-Disposition') || '';
    const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
    const name = star ? decodeURIComponent(star[1]) : it.filename;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 250);
}

// ── "+" with a name: an inline field at the top of the section ────────────────
// Enter creates (reg.create returns true, or a sentence saying why not, shown under the
// field); Escape — or leaving the field empty — cancels. The section opens so the field
// is visible; a second "+" focuses the field already there.
function _promptNew(details, body, reg, profileId) {
    details.open = true;
    let row = body.querySelector('.ws-new-row');
    if (!row) {
        row = document.createElement('div');
        row.className = 'ws-new-row';
        row.innerHTML =
            `<input class="ws-new-input" type="text" maxlength="120" placeholder="${_esc(reg.createPrompt)}" aria-label="${_esc(reg.createPrompt)}">` +
            `<div class="ws-new-err" role="alert"></div>`;
        body.insertBefore(row, body.firstChild);
        const input = row.querySelector('.ws-new-input');
        const err = row.querySelector('.ws-new-err');
        const cancel = () => { if (row.parentNode) row.parentNode.removeChild(row); };
        let busy = false;
        input.addEventListener('keydown', async (e) => {
            if (e.key === 'Escape') { e.preventDefault(); cancel(); return; }
            if (e.key !== 'Enter' || busy) return;
            e.preventDefault();
            const name = (input.value || '').trim();
            if (!name) { cancel(); return; }
            busy = true; input.disabled = true; err.textContent = '';
            let res;
            try { res = await reg.create(profileId, name); }
            catch (ex) { console.error('[Workspace] create failed:', ex); res = 'Could not create'; }
            if (res === true) { cancel(); loadWorkspacePanel(); return; }
            busy = false; input.disabled = false;
            err.textContent = typeof res === 'string' ? res : 'Could not create';
            input.focus(); input.select();
        });
        input.addEventListener('blur', () => { if (!busy && !(input.value || '').trim()) cancel(); });
    }
    const f = row.querySelector('.ws-new-input');
    if (f) f.focus();
}

// Start a request in the chat composer (the agent creates the instance), cursor at the end.
function _startInComposer(text) {
    const box = document.getElementById('user-input');
    if (!box) return;
    if (!(box.value || '').trim()) box.value = text;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    box.focus();
    try { const n = box.value.length; box.setSelectionRange(n, n); } catch (_) { /* not a text field */ }
}

// Single-click delete → platform-standard confirmation banner (no self-invented
// two-step arm/confirm, which read as "nothing happened" on a single click and
// violated the no-native-dialogs convention).
function _wireDelete(row, reg, id, label) {
    const btn = row.querySelector('.ws-del');

    const doDelete = async () => {
        btn.disabled = true;
        try {
            const r = await fetch(reg.deleteUrl(id), { method: 'DELETE', headers: _authHeaders() });
            if (r.ok) {
                row.classList.add('ws-row--removing');
                setTimeout(() => loadWorkspacePanel(), 220);
            } else {
                btn.disabled = false;
                if (typeof window.showAppBanner === 'function') {
                    window.showAppBanner(`Could not delete "${label}" — please try again.`, 'error');
                }
            }
        } catch (_) {
            btn.disabled = false;
            if (typeof window.showAppBanner === 'function') {
                window.showAppBanner(`Could not delete "${label}" — please try again.`, 'error');
            }
        }
    };

    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const msg = `Delete "${label}"? This cannot be undone.`;
        if (typeof window.showConfirmBanner === 'function') {
            window.showConfirmBanner(msg, doDelete);
        } else if (typeof window.showConfirmation === 'function') {
            window.showConfirmation('Delete', `<p>${_esc(msg)}</p>`, doDelete);
        } else {
            doDelete();   // last-resort fallback if no banner system is present
        }
    });
}

// ── Canvas opener (mirrors sessionManagement._restoreCanvasState) ──────────────
if (typeof window !== 'undefined') window.__openCanvasById = (id) => _openCanvas(id);   // Files → "Open in Canvas"
async function _openCanvas(canvasId) {
    const token = localStorage.getItem('tda_auth_token');
    let spec = { canvas_id: canvasId, action: 'open' };
    try {
        const r = await fetch(`/api/v1/canvases/${canvasId}`, { headers: { 'Authorization': `Bearer ${token}` } });
        if (r.ok) {
            const c = await r.json();
            const lang = (c.language || '').toLowerCase();
            spec = {
                canvas_id: c.canvas_id,
                title: c.title,
                language: lang,
                content: c.content,
                sources: c.sources || null,
                previewable: ['html', 'svg', 'markdown'].includes(lang),
                line_count: ((c.content || '').match(/\n/g) || []).length + 1,
                versions: c.versions || [],
                action: 'open',
            };
        }
    } catch (_) { /* fall back to id-only spec */ }

    const { renderCanvas } = await import('/api/v1/components/canvas/renderer');
    const tempId = `canvas-ws-open-${state.currentResourcePanelProfileId || 'x'}-${(spec.canvas_id || 'c')}`;
    let tempDiv = document.getElementById(tempId);
    if (!tempDiv) {
        tempDiv = document.createElement('div');
        tempDiv.id = tempId;
        tempDiv.style.display = 'none';
        document.body.appendChild(tempDiv);
    }
    Promise.resolve(renderCanvas(tempId, { component: 'canvas', ...spec })).finally(() => tempDiv.remove());
}

// A flow's canvas artefact node (pair programming) asks for its run's canvas — ONE
// opener for every caller, so a flow never re-implements how a canvas opens.
if (typeof document !== 'undefined') {
    UderiaBus.on('uderia:flow-open-canvas', (e) => {
        const id = e && e.detail && e.detail.canvas_id;
        if (id) _openCanvas(id);
    });
}

// ── Knowledge-graph opener (mirrors _openCanvas + the conversation KG card) ────
// Pure frontend launch — no LLM round-trip / no TDA_KnowledgeGraph(action="open").
// Reuses the SAME path the conversation KG card uses to open its split view: the
// renderKnowledgeGraph dispatcher with action:'open' (rendered into a hidden temp
// div, exactly like _openCanvas above). Going through the dispatcher renders the
// inline preview first — which flushes layout before the split panel measures its
// dimensions — avoiding the mis-sized graph that calling openKGSplitPanel directly
// produced when launched from an instant click.
if (typeof window !== 'undefined') window.openKnowledgeGraphFromWorkspace = (kg, opts) => _openKnowledgeGraph(kg, opts);
async function _openKnowledgeGraph(kg, opts) {
    const profileId = kg.profile_id;
    if (!profileId) return;
    const token = localStorage.getItem('tda_auth_token');
    const drafts = !!(opts && opts.includeDrafts);
    const focus = (opts && Array.isArray(opts.focusNames)) ? opts.focusNames : [];
    const extra = (kg.kg_id ? `&kg_id=${encodeURIComponent(kg.kg_id)}` : '')
        + (drafts ? '&include_drafts=1' : '')
        + (focus.length ? `&focus_names=${encodeURIComponent(focus.join(','))}` : '');

    // The conversation card receives its spec from the backend tool result; from the
    // Work panel we fetch the equivalent spec (graph-spec uses the same transform as
    // the component's visualize action, so the shape is identical).
    let spec = null;
    try {
        const r = await fetch(
            `/api/v1/knowledge-graph/graph-spec?profile_id=${encodeURIComponent(profileId)}&max_nodes=500${extra}`,
            { headers: { 'Authorization': `Bearer ${token}` } });
        if (r.ok) {
            const data = await r.json();
            spec = data.spec || null;
        }
    } catch (_) { /* spec stays null — handled below */ }

    if (!spec || !Array.isArray(spec.nodes) || spec.nodes.length === 0) {
        if (typeof window.showAppBanner === 'function') {
            window.showAppBanner(`"${kg.kg_name || 'Knowledge graph'}" is empty — nothing to open.`, 'info');
        }
        return;
    }

    const payload = {
        ...spec,
        component: 'knowledge_graph',
        kg_id: kg.kg_id,
        profile_id: profileId,
        title: spec.title || kg.kg_name || kg.profile_name || 'Knowledge Graph',
        action: 'open',
        include_drafts: drafts,       // the panel's own canonical fetch keeps the drafts …
        focus_names: focus,           // … seeds the sample with the clicked concept, and the
                                      // renderer selects + zooms to it once drawn (renderKGFull)
    };

    const { renderKnowledgeGraph } = await import('/api/v1/components/knowledge_graph/renderer');
    const tempId = `kg-ws-open-${profileId}-${kg.kg_id || 'k'}`;
    let tempDiv = document.getElementById(tempId);
    if (!tempDiv) {
        tempDiv = document.createElement('div');
        tempDiv.id = tempId;
        tempDiv.style.display = 'none';
        document.body.appendChild(tempDiv);
    }
    try { renderKnowledgeGraph(tempId, payload); }
    finally { setTimeout(() => tempDiv.remove(), 0); }
}

// A section that lists only its newest rows (Files) says its real total, not the page's size —
// the figure in its header AND its share of the Workspace badge.
function _sectionTotal(reg, items) {
    return typeof reg.total === 'function' ? reg.total(items) : (items || []).length;
}

// ── Tab + rail-badge state ─────────────────────────────────────────────────────
// The badge reads "items · sections" (resourceBadge.workspaceLabel): the sum of what the
// Workspace lists, then the sections listing it. Written on the tab text; the rail badge and
// the panel-header count mirror it (resourcePanelHandler._syncRailBadgesFromTabs reads the
// same form). `items` null = a load in flight: the previous figures stay until the new ones.
function _setTabState(visible, items, sections) {
    const tab = document.querySelector(TAB_SELECTOR);
    const rail = document.querySelector('.rail-item[data-type="workspace"]');
    const panelCount = document.getElementById('workspace-panel-count');
    const badge = document.getElementById('rail-badge-workspace');
    if (rail) rail.classList.toggle('rail-item--hidden', !visible);
    if (tab) tab.style.display = visible ? 'inline-block' : 'none';
    if (!visible) {   // no section for this agent: nothing to count
        if (tab) { tab.textContent = 'Workspace'; tab.title = ''; }
        if (rail) rail.title = 'Workspace';
        if (panelCount) panelCount.textContent = '';
        if (badge) badge.textContent = '';
        return;
    }
    if (items == null) {   // a load in flight: keep the previous figures
        if (tab && !/\(\d+ · \d+\)/.test(tab.textContent)) tab.textContent = 'Workspace';
        return;
    }
    const pair = `${items} · ${sections}`;
    const hover = `${items} item${items === 1 ? '' : 's'} in ${sections} section${sections === 1 ? '' : 's'}`;
    if (tab) { tab.textContent = workspaceLabel(items, sections); tab.title = hover; }
    if (rail) rail.title = `Workspace — ${hover}`;
    if (panelCount && panelCount.textContent !== pair) panelCount.textContent = pair;
    if (badge && badge.textContent !== pair) badge.textContent = pair;
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function _authHeaders() {
    const t = localStorage.getItem('tda_auth_token');
    return t ? { 'Authorization': `Bearer ${t}` } : {};
}

function _esc(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _timeAgo(iso) {
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return '';
    const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (s < 60) return 'just now';
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 30) return `${d}d ago`;
    const mo = Math.floor(d / 30);
    if (mo < 12) return `${mo}mo ago`;
    return `${Math.floor(mo / 12)}y ago`;
}

// ── Theme-aware styles (injected once; all colours via CSS variables) ──────────
function _injectWorkspaceCSS() {
    if (document.getElementById('workspace-panel-styles')) return;
    const css = `
#workspace-panel-content { padding: 4px 4px 10px; }

.ws-section { position: relative; border-bottom: 1px solid var(--border-secondary, rgba(148,163,184,0.10)); }
.ws-section:last-child { border-bottom: none; }
.ws-sec-head {
    display: flex; align-items: center; gap: 8px; padding: 8px 8px;
    cursor: pointer; list-style: none; user-select: none; border-radius: 6px;
}
.ws-sec-head::-webkit-details-marker { display: none; }
.ws-sec-head:hover { background: var(--hover-bg, rgba(148,163,184,0.08)); }
.ws-sec-icon { display: inline-flex; width: 16px; height: 16px; flex-shrink: 0; }
.ws-sec-icon svg { width: 16px; height: 16px; }
.ws-sec-name {
    flex: 1; min-width: 0; font-size: 11px; font-weight: 700; letter-spacing: 0.04em;
    text-transform: uppercase; color: var(--text-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ws-sec-count {
    font-size: 10px; font-weight: 600; color: var(--text-muted);
    background: var(--hover-bg, rgba(148,163,184,0.10));
    border-radius: 9px; padding: 1px 7px; flex-shrink: 0;
}
.ws-sec-chevron { width: 14px; height: 14px; color: var(--text-muted); flex-shrink: 0; transition: transform 0.18s ease; }
.ws-sec-details[open] .ws-sec-chevron { transform: rotate(90deg); }
/* The header actions: a sibling of the <details>, laid over its summary with the
   summary's own padding, gap and slot widths, so each button covers its reserved slot.
   Only the buttons take the pointer; the rest of the row still reaches the summary. */
.ws-sec-actions {
    position: absolute; top: 0; left: 0; right: 0; display: flex; align-items: center; gap: 8px;
    padding: 8px 8px; box-sizing: border-box; pointer-events: none;
}
.ws-sec-actions-fill { flex: 1; }
.ws-sec-actions .ws-sec-import { pointer-events: auto; }
.ws-sec-ghost { visibility: hidden; }
span.ws-sec-chevron.ws-sec-ghost { display: inline-block; }
.ws-sec-body { padding: 1px 2px 8px; display: flex; flex-direction: column; gap: 1px; }
.ws-sec-empty { font-size: 11px; color: var(--text-muted); line-height: 1.4; padding: 4px 10px 8px 26px; }
.ws-files-all { display: block; margin: 4px 10px 8px 26px; padding: 0; border: 0; background: none; cursor: pointer; font-size: 11px;
  color: var(--text-secondary); text-decoration: underline; text-underline-offset: 2px; text-decoration-color: var(--border-primary); }
.ws-files-all:hover, .ws-files-all:focus-visible { color: var(--text-primary); text-decoration-color: currentColor; }
.ws-empty-act { font-size: 11px; padding: 2px 10px; margin-left: 6px; border-radius: 6px; border: 1px solid var(--teradata-orange, #F15F22); color: var(--teradata-orange, #F15F22); background: transparent; cursor: pointer; font-weight: 600; }
.ws-empty-act:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: 2px; }

.ws-row {
    display: flex; align-items: center; gap: 8px; padding: 6px 8px;
    border-radius: 6px; border: 1px solid transparent;
    transition: background 0.1s ease, border-color 0.12s ease, opacity 0.2s ease, transform 0.2s ease;
}
.ws-row:hover { background: var(--hover-bg, rgba(148,163,184,0.06)); }
.ws-row--removing { opacity: 0; transform: translateX(8px); }
.ws-row-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; background: var(--border-primary, rgba(148,163,184,0.4)); }
.ws-row-dot.is-active { background: var(--teradata-orange); box-shadow: 0 0 0 3px rgba(241,95,34,0.15); }
.ws-row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; cursor: pointer; }
.ws-row-name {
    font-size: 12px; font-weight: 500; color: var(--text-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ws-row-meta {
    font-size: 10px; color: var(--text-muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ws-trailer { margin-top: 4px; padding-top: 4px; border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.12)); }
.ws-row-dot--draft { border: 1.5px dashed var(--text-muted, #94a3b8); background: transparent !important; }
.ws-row--draft .ws-row-meta { font-size: 10px; color: var(--text-muted, #94a3b8); margin-left: 6px; }
.ws-trailer-label { font-size: 10px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--text-muted); padding: 2px 10px 2px 26px; }
.ws-row--sub { padding-top: 4px; padding-bottom: 4px; }
.ws-row--sub .ws-row-name { font-size: 11px; font-weight: 400; color: var(--text-muted); }
.ws-row--sub .ws-row-dot { width: 4px; height: 4px; opacity: 0.6; }
.ws-row-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; opacity: 0; transition: opacity 0.12s ease; }
.ws-row:hover .ws-row-actions, .ws-row:focus-within .ws-row-actions { opacity: 1; }
.ws-btn {
    width: 24px; height: 24px; display: flex; align-items: center; justify-content: center;
    border-radius: 5px; background: transparent; border: none; color: var(--text-muted);
    cursor: pointer; transition: color 0.1s ease, background 0.1s ease;
}
.ws-btn svg { width: 14px; height: 14px; }
.ws-btn:hover { color: var(--text-primary); background: var(--hover-bg-strong, rgba(148,163,184,0.12)); }
.ws-btn.ws-del:hover { color: #f87171; background: rgba(248,113,113,0.12); }

.ws-empty { padding: 1.5rem 1rem; text-align: center; color: var(--text-muted); font-size: 12px; }
.ws-skel-row {
    height: 30px; border-radius: 6px; margin: 5px 6px;
    background: var(--hover-bg, rgba(148,163,184,0.08));
    animation: ws-pulse 1.2s ease-in-out infinite;
}
@keyframes ws-pulse { 0%, 100% { opacity: 0.45; } 50% { opacity: 0.9; } }

/* Section-header Import button */
.ws-sec-import {
    width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center;
    border-radius: 5px; background: transparent; border: none; color: var(--text-muted);
    cursor: pointer; flex-shrink: 0; opacity: 0; transition: opacity 0.12s ease, color 0.1s ease, background 0.1s ease;
}
.ws-sec-import svg { width: 14px; height: 14px; }
/* Equal-width placeholder for sections with no import action, so the count
   column aligns vertically across every row (import-capable or not). */
.ws-sec-import-spacer { width: 22px; height: 22px; flex-shrink: 0; }
.ws-section:has(> .ws-sec-details > .ws-sec-head:hover) .ws-sec-import,
.ws-sec-actions:hover .ws-sec-import,
.ws-sec-import:focus-visible { opacity: 1; }
.ws-section:has(> .ws-sec-actions .ws-sec-import:hover) .ws-sec-head { background: var(--hover-bg, rgba(148,163,184,0.08)); }
.ws-sec-import:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: 1px; }
.ws-sec-import:hover { color: var(--teradata-orange); background: var(--hover-bg-strong, rgba(148,163,184,0.12)); }
.ws-new-row { padding: 2px 6px 4px 22px; }
.ws-new-input {
    width: 100%; box-sizing: border-box; font-size: 12px; padding: 5px 8px; border-radius: 6px;
    background: var(--bg-primary, transparent); color: var(--text-primary);
    border: 1px solid var(--border-primary, rgba(148,163,184,0.35)); outline: none;
}
.ws-new-input:focus { border-color: var(--teradata-orange); }
.ws-new-err { font-size: 10px; color: var(--trust-danger, #f87171); padding: 2px 2px 0; }
.ws-new-err:empty { display: none; }
.ws-btn.ws-export:hover { color: var(--teradata-orange); background: var(--hover-bg-strong, rgba(148,163,184,0.12)); }

`;
    const style = document.createElement('style');
    style.id = 'workspace-panel-styles';
    style.textContent = css;
    document.head.appendChild(style);
}
