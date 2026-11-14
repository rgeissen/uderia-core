#!/usr/bin/env node
/**
 * The Workspace "+" — every section that can create something offers it, the same way.
 *
 * The REAL workspacePanelHandler.js is EXECUTED under a DOM stub against a fake backend,
 * with the component renderers it lazily imports (`/api/v1/components/<id>/renderer`)
 * redirected to fakes by a module-resolve hook, so each "+" is driven to its effect:
 *   dashboard / canvas → an inline name field; Enter creates through the component's own
 *                        REST and opens the new instance; a refusal is shown under the field
 *   flow               → the topology picker
 *   knowledge graph    → the graph builders (the panel switcher's own route)
 *   memory             → the agent's memory panel, add field focused
 *   database navigator → the Database Connection connector
 *   scheduler          → a request started in the composer (the agent creates tasks)
 *   explain plan       → no "+" (a plan is derived from a statement) — the slot is reserved
 */
import './_uderia_bus.mjs';
import { pathToFileURL } from 'node:url';
import { register } from 'node:module';
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

// ── fake component renderers ─────────────────────────────────────────────────
const FAKES = {
    dashboard: `export const openDashboardPanel = (s) => { globalThis.__acts.push(['dashboard.open', s]); };`,
    flow: `export const openTopologyPicker = (o) => { globalThis.__acts.push(['flow.picker', o]); }; export const openFlowSplitPanel = () => {};`,
    canvas: `export const renderCanvas = () => {};`,
    knowledge_graph: `export const openKgConstructors = () => { globalThis.__acts.push(['kg.constructors']); };`,
    memory: `export const openMemorySplitPanel = async (s) => { globalThis.__acts.push(['memory.open', s]); };`,
    database_navigator: `export const openDatabaseNavigatorPanel = () => {};`,
    scheduler: `export const openSchedulerSplitPanel = () => {};`,
    explain_plan: `export const panelPlans = () => (globalThis.__plans || []); export const showPanelPlan = () => {};`,
};
// the Knowledge Graphs tab's own handler (imported relatively by the Workspace)
const KG_PANEL = `export const openKgExportMenu = (btn, pid, kgId) => { globalThis.__acts.push(['kg.export', pid, kgId, btn && btn.className]); };
export const startKgImport = (btn, onDone) => { globalThis.__acts.push(['kg.import', btn && btn.dataset.iconOnly, typeof onDone]); };`;
const hook = `
const FAKES = ${JSON.stringify(FAKES)};
export async function resolve(spec, ctx, next) {
    const m = spec.match(/^\\/api\\/v1\\/components\\/([a-z_]+)\\/renderer$/);
    if (m && FAKES[m[1]]) return { url: 'data:text/javascript,' + encodeURIComponent(FAKES[m[1]]), shortCircuit: true };
    if (/knowledgeGraphPanelHandler\.js/.test(spec)) return { url: 'data:text/javascript,' + encodeURIComponent(${JSON.stringify(KG_PANEL)}), shortCircuit: true };
    return next(spec, ctx);
}`;
register('data:text/javascript,' + encodeURIComponent(hook), pathToFileURL('./'));
globalThis.__acts = [];

// ── DOM stub (the renderer gate's, same shape) ────────────────────────────────
class El {
    constructor(tag, attrs = {}) {
        this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.children = []; this.parentNode = null;
        this.dataset = {}; this.style = { setProperty() {}, removeProperty() {} }; this._html = ''; this.textContent = ''; this.value = '';
        this._classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean)); this._listeners = {};
        this.classList = { add: (...c) => c.forEach(x => this._classes.add(x)), remove: (...c) => c.forEach(x => this._classes.delete(x)),
            contains: c => this._classes.has(c), toggle: (c, on) => { if (on === undefined) { this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c); } else on ? this._classes.add(c) : this._classes.delete(c); } };
        for (const [k, v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    get className() { return [...this._classes].join(' '); }
    set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
    get innerHTML() { return this._html; }
    set innerHTML(h) { this._html = String(h); this.children = parseHtml(this._html, this); }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    insertBefore(c, ref) { c.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; }
    removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; return c; }
    get firstChild() { return this.children[0] || null; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    focus() { globalThis.__focused = this; }
    select() {}
    setSelectionRange() {}
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    _walk(out = []) { for (const c of this.children) { out.push(c); c._walk(out); } return out; }
    // everything rendered beneath this element, whether set through innerHTML or appended
    get deepHTML() { return this._html + this.children.map(c => c.deepHTML).join(''); }
    _matches(sel) {
        return sel.split(',').map(x => x.trim()).some(s => {
            const m = s.match(/^([a-zA-Z]*)?((?:\.[\w-]+)*)?(?:\[([\w-]+)(?:="([^"]*)")?\])?(?:#([\w-]+))?$/);
            if (!m) return false;
            const [, tag, cls, ak, av, id] = m;
            if (tag && this.tagName !== tag.toUpperCase()) return false;
            if (cls && !cls.split('.').filter(Boolean).every(c => this._classes.has(c))) return false;
            if (ak && (av === undefined ? !(ak in this.attrs) : this.attrs[ak] !== av)) return false;
            if (id && this.attrs.id !== id) return false;
            return true;
        });
    }
    querySelectorAll(sel) { return this._walk().filter(e => e._matches(sel)); }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
    dispatchEvent(ev) { (this._listeners[ev.type] || []).forEach(fn => fn(ev)); return true; }
    click() { this.dispatchEvent({ type: 'click', target: this, currentTarget: this, preventDefault() {}, stopPropagation() {} }); }
}
function parseHtml(html, parent) {
    const out = []; const stack = [parent]; const re = /<\/?([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g; let m;
    while ((m = re.exec(html))) {
        const [full, tag, rawAttrs, selfClose] = m;
        if (full.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
        const attrs = {}; const ar = /([\w-]+)(?:="([^"]*)")?/g; let a;
        while ((a = ar.exec(rawAttrs))) attrs[a[1]] = a[2] === undefined ? '' : a[2];
        const el = new El(tag, attrs);
        const p = stack[stack.length - 1]; el.parentNode = p; if (p === parent) out.push(el); else p.children.push(el);
        const rest = html.slice(re.lastIndex); const t = rest.slice(0, rest.indexOf('<') < 0 ? rest.length : rest.indexOf('<'));
        el.textContent = t.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').trim();
        if (!selfClose && !['input', 'br', 'path', 'ellipse', 'circle', 'rect', 'line', 'polyline'].includes(tag)) stack.push(el);
    }
    return out;
}
const byId = new Map();
const doc = {
    getElementById: id => byId.get(id) || [...byId.values()].flatMap(e => e._walk()).find(e => e.attrs.id === id) || null,
    createElement: t => new El(t),
    head: new El('head'), body: new El('body'), documentElement: new El('html'),
    querySelectorAll: sel => [...byId.values()].flatMap(e => [e, ...e._walk()]).filter(e => e._matches(sel)),
    querySelector: sel => doc.querySelectorAll(sel)[0] || null,
    addEventListener() {},
};
globalThis.document = doc;
globalThis.window = { currentResourcePanelProfileId: 'p1', openPlatformConnector: (id) => globalThis.__acts.push(['connector', id]),
    showConfirmBanner: (msg, cb) => { globalThis.__acts.push(['confirm', msg]); cb(); } };
globalThis.localStorage = { _m: {}, getItem(k) { return this._m[k] ?? null; }, setItem(k, v) { this._m[k] = String(v); } };
globalThis.Event = class { constructor(t) { this.type = t; } };

// ── fake backend ──────────────────────────────────────────────────────────────
const comp = (id, name) => ({ component_id: id, display_name: name, icon_accent: '#888', icon_svg: '<svg></svg>', profile_enabled: true,
    supports_instance_io: ['dashboard', 'flow', 'canvas'].includes(id),   // as the manifests declare
    // whose its rows are, read from the manifest exactly as the components API serves it
    instances: JSON.parse(readFileSync(`components/builtin/${id}/manifest.json`, 'utf8')).instances ?? null });
const COMPONENTS = [comp('dashboard', 'Dashboard'), comp('flow', 'Flow'), comp('canvas', 'Canvas'), comp('scheduler', 'Task Scheduler'),
    comp('knowledge_graph', 'Knowledge Graph'), comp('memory', 'Agent Memory'), comp('database_navigator', 'Database Navigator'), comp('explain_plan', 'EXPLAIN Plan')];
const posts = [], deletes = [];
// the fixture must not lie about what each component can do
for (const c of COMPONENTS) {
    const man = JSON.parse(readFileSync(`components/builtin/${c.component_id}/manifest.json`, 'utf8'));
    ok(`fixture: ${c.component_id} supports_instance_io matches its manifest`, !!man.supports_instance_io === c.supports_instance_io);
}
let LISTS = null;   // filled for the icon-matrix section
let canvasStatus = 201;
const resp = (ok, body, status) => { const r = { ok, status: status ?? (ok ? 200 : 500), json: async () => body }; r.clone = () => resp(ok, body, status); return r; };
globalThis.fetch = async (url, opts = {}) => {
    url = String(url);
    if (opts.method === 'POST') {
        posts.push({ url, body: JSON.parse(opts.body || '{}'), headers: opts.headers || {} });
        if (url === '/api/v1/dashboards') return resp(true, { dashboard_id: 'dash-new', name: posts.at(-1).body.name }, 201);
        if (url === '/api/v1/canvases') return canvasStatus === 409 ? resp(false, {}, 409) : resp(true, { canvas_id: 'cv-new' }, 201);
        return resp(false, {});
    }
    if (opts.method === 'DELETE') { deletes.push(url); return resp(true, {}); }
    if (/\/components$/.test(url)) return resp(true, { components: COMPONENTS });
    if (LISTS && url in LISTS) return resp(true, LISTS[url]);
    if (/\/api\/v1\/canvases\/cv-new$/.test(url)) return resp(true, { canvas_id: 'cv-new', title: 'x', content: '', language: 'sql' });
    return resp(true, {});
};

const { loadWorkspacePanel } = await import(pathToFileURL('static/js/handlers/workspacePanelHandler.js').href);

async function render() {
    byId.clear();
    const c = new El('div', { id: 'workspace-panel-content' }); byId.set('workspace-panel-content', c);
    const box = new El('textarea', { id: 'user-input' }); byId.set('user-input', box);
    const memAdd = new El('input', { id: 'mem-add-input' }); byId.set('mem-add-input', memAdd);
    await loadWorkspacePanel();
    return c;
}
const sectionOf = (root, name) => root.children.filter(e => e._matches('.ws-section'))
    .find(s => s.querySelector('.ws-sec-name').textContent === name);
const tick = () => new Promise(r => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
const key = (el, k) => el.dispatchEvent({ type: 'keydown', key: k, preventDefault() {} });
// A missing "+" or field must FAIL BY NAME, never crash the gate: clicks and fields fall back
// to an inert stand-in so the assertions after them still run and report.
const plus = (sec) => { const b = sec?.querySelector('.ws-sec-new'); if (b) b.click(); return !!b; };
const field = (sec) => sec?.querySelector('.ws-new-input') || new El('input');

// ── 1. every section that can create offers "+"; explain plan reserves the slot ──
let root = await render();
const names = COMPONENTS.map(c => c.display_name);
ok('every enabled component renders a section', names.every(n => !!sectionOf(root, n)));
for (const n of names.filter(n => n !== 'EXPLAIN Plan')) {
    const plus = sectionOf(root, n)?.querySelector('.ws-sec-new');
    ok(`"${n}" offers a "+"`, !!plus);
    ok(`"${n}" "+" is named for what it does`, !!plus && !!plus.attrs.title && plus.attrs['aria-label'] !== undefined);
}
const ep = sectionOf(root, 'EXPLAIN Plan');
ok('EXPLAIN Plan offers no "+" (a plan is derived from a statement)', !!ep && !ep.querySelector('.ws-sec-new'));
ok('…and reserves the slot so the counts stay aligned', !!ep && ep.querySelector('.ws-sec-head').querySelectorAll('.ws-sec-import-spacer').length >= 1);
ok('every section header carries the same number of action slots',
    new Set(names.map(n => sectionOf(root, n).querySelector('.ws-sec-head').children.filter(e => e._matches('.ws-sec-import,.ws-sec-import-spacer')).length)).size === 1);
ok('no header action sits inside the <summary> (a summary is one toggle — Chrome: interactive element inside <summary>)',
    names.every(n => { const h = sectionOf(root, n).querySelector('.ws-sec-head');
                       return h && h.querySelectorAll('button,a,input,select,textarea').length === 0; }));
ok('…the actions live in the sibling layer laid over the header',
    names.filter(n => n !== 'EXPLAIN Plan').every(n => !!sectionOf(root, n).querySelector('.ws-sec-actions')?.querySelector('.ws-sec-new')));
ok('…which mirrors the header\'s slots, so each button covers its reserved slot',
    names.every(n => { const sec = sectionOf(root, n);
        const slots = (el) => el.children.filter(e => e._matches('.ws-sec-import,.ws-sec-import-spacer')).length;
        return slots(sec.querySelector('.ws-sec-head')) === slots(sec.querySelector('.ws-sec-actions')); }));

// ── 2. dashboard: inline name → POST → open the new dashboard ─────────────────
let sec = sectionOf(root, 'Dashboard');
plus(sec);
ok('the dashboard "+" opens the section', sec.querySelector('.ws-sec-details')?.open === true);
let input = field(sec);
ok('…and shows a name field at the top of the section', !!sec.querySelector('.ws-new-input') && !!sec.querySelector('.ws-sec-body').children[0]?._matches('.ws-new-row'));
ok('…focused', globalThis.__focused === input);
plus(sec);
ok('a second "+" reuses the field rather than adding another', sec.querySelectorAll('.ws-new-row').length === 1);
input.value = '   '; key(input, 'Enter'); await settle();
ok('an empty name creates nothing and closes the field', posts.length === 0 && !sec.querySelector('.ws-new-row'));
plus(sec); input = field(sec);
key(input, 'Escape');
ok('Escape cancels', !sec.querySelector('.ws-new-row'));
plus(sec); input = field(sec);
input.value = '  Q3 Revenue '; key(input, 'Enter'); await settle();
ok('Enter creates through the dashboards endpoint, name trimmed', posts.length === 1 && posts[0].url === '/api/v1/dashboards' && posts[0].body.name === 'Q3 Revenue');
ok('…as JSON', posts[0].headers['Content-Type'] === 'application/json');
ok('…and opens the new dashboard', globalThis.__acts.some(([a, s]) => a === 'dashboard.open' && s.dashboard_id === 'dash-new' && s.name === 'Q3 Revenue'));

// ── 3. canvas: a refusal is shown under the field, the field stays ────────────
root = await render(); posts.length = 0; canvasStatus = 409;
sec = sectionOf(root, 'Canvas');
plus(sec); input = field(sec);
input.value = 'report.sql'; key(input, 'Enter'); await settle();
ok('the canvas "+" creates through the canvases endpoint', posts.length === 1 && posts[0].url === '/api/v1/canvases' && posts[0].body.mode === 'create' && posts[0].body.title === 'report.sql');
ok('a taken title is said under the field', (sec.querySelector('.ws-new-err')?.textContent || '').includes('already exists'));
ok('…and the field stays, editable', !!sec.querySelector('.ws-new-row') && input.disabled === false);
canvasStatus = 201; input.value = 'report2.sql'; key(input, 'Enter'); await settle();
ok('a second try with a free title creates it and closes the field', posts.length === 2 && !sec.querySelector('.ws-new-row'));

// ── 4. the routes: flow, knowledge graph, memory, navigator, scheduler ────────
root = await render(); globalThis.__acts.length = 0;
plus(sectionOf(root, 'Flow')); await settle();
ok('flow "+" opens the topology picker for the active agent', globalThis.__acts.some(([a, o]) => a === 'flow.picker' && o.sourceProfileId === 'p1'));
plus(sectionOf(root, 'Knowledge Graph')); await settle();
ok('knowledge-graph "+" opens the graph builders', globalThis.__acts.some(([a]) => a === 'kg.constructors'));
globalThis.__focused = null;
plus(sectionOf(root, 'Agent Memory')); await settle();
ok('memory "+" opens this agent\'s memory', globalThis.__acts.some(([a, s]) => a === 'memory.open' && s.profile_id === 'p1'));
ok('…with the add field focused', globalThis.__focused === doc.getElementById('mem-add-input'));
plus(sectionOf(root, 'Database Navigator')); await settle();
ok('navigator "+" opens the Database Connection connector', globalThis.__acts.some(([a, id]) => a === 'connector' && id === 'uderia-database'));
const box = doc.getElementById('user-input');
plus(sectionOf(root, 'Task Scheduler')); await settle();
ok('scheduler "+" starts the request in the composer', box.value === 'Schedule a task that ' && globalThis.__focused === box);
box.value = 'my draft';
plus(sectionOf(root, 'Task Scheduler')); await settle();
ok('…never overwriting a draft already in the composer', box.value === 'my draft');
ok('no "+" toggled its section open except the ones that ask for a name',
    ['Flow', 'Knowledge Graph', 'Agent Memory', 'Database Navigator', 'Task Scheduler'].every(n => !sectionOf(root, n).open));

// ── 5. one icon vocabulary across every section ──────────────────────────────
LISTS = {
    '/api/v1/dashboards': { dashboards: [{ id: 'd1', name: 'Sales', tile_count: 2 }] },
    '/api/v1/flows': { flows: [{ flow_id: 'f1', name: 'Nightly', definition: { nodes: [] } }] },
    '/api/v1/canvases': { canvases: [{ canvas_id: 'c1', title: 'q.sql', language: 'sql' }] },
    '/api/v1/scheduled-tasks': { tasks: [{ id: 't1', name: 'Daily report', enabled: true, schedule: '0 9 * * *' }] },
    '/api/v1/knowledge-graph/list': { knowledge_graphs: [
        { kg_id: 'kOwn', kg_name: 'Own graph', profile_id: 'p1', total_entities: 3 },
        { kg_id: 'kAssigned', kg_name: 'Assigned graph', profile_id: 'p9', assigned_profiles: [{ id: 'p1' }], total_entities: 3 },
        { kg_id: 'kSub', kg_name: 'Subscribed graph', profile_id: 'p1', is_subscribed: true, total_entities: 3 },
        { kg_id: 'kOther', kg_name: 'Unrelated graph', profile_id: 'p9', total_entities: 3 },
    ] },
    '/api/v1/memory/buckets?profile_id=p1': { buckets: [{ key: 'p1', label: 'Local', count: 2 }, { key: 'global', label: 'Global', count: 1 }] },
    '/api/v1/database-navigator/sources?profile_id=p1': { sources: [{ id: 'conn:1', label: 'TD', kind: 'direct', reason: 'default' }] },
};
globalThis.__plans = [{ i: 0, label: 'SELECT 1', steps: 2, findings: 0 }];
root = await render(); globalThis.__acts.length = 0;
const rowsOf = (n) => sectionOf(root, n)?.querySelectorAll('.ws-row') || [];
const has = (n, cls) => rowsOf(n).length > 0 && rowsOf(n).every(r => !!r.querySelector(cls));
const none = (n, cls) => rowsOf(n).every(r => !r.querySelector(cls));
const hasImport = (n) => !!sectionOf(root, n)?.querySelector('.ws-sec-imp');
// the matrix: which act each section can perform
const MATRIX = {
    'Dashboard':          { imp: 1, exp: 1, del: 1 },
    'Flow':               { imp: 1, exp: 1, del: 1 },
    'Canvas':             { imp: 1, exp: 1, del: 1 },
    'Task Scheduler':     { imp: 0, exp: 0, del: 1 },
    'Agent Memory':       { imp: 0, exp: 0, del: 0 },
    'Database Navigator': { imp: 0, exp: 0, del: 0 },
    'EXPLAIN Plan':       { imp: 0, exp: 0, del: 0 },
};
for (const [n, m] of Object.entries(MATRIX)) {
    ok(`"${n}" lists its rows`, rowsOf(n).length > 0, `${rowsOf(n).length}`);
    ok(`"${n}" Import ${m.imp ? 'offered' : 'absent'}`, hasImport(n) === !!m.imp);
    ok(`"${n}" Export ${m.exp ? 'on every row' : 'absent'}`, m.exp ? has(n, '.ws-export') : none(n, '.ws-export'));
    ok(`"${n}" Delete ${m.del ? 'on every row' : 'absent'}`, m.del ? has(n, '.ws-del') : none(n, '.ws-del'));
    ok(`"${n}" Open on every row`, has(n, '.ws-open'));
}
// knowledge graph: its own import/export; delete only what this agent owns outright
const kgRows = rowsOf('Knowledge Graph');
const kgRow = (name) => kgRows.find(r => r.querySelector('.ws-row-name').textContent === name);
ok('knowledge graph lists the owned, assigned and subscribed graphs (not unrelated ones)', kgRows.length === 3 && !kgRow('Unrelated graph'));
ok('knowledge graph offers Import', hasImport('Knowledge Graph'));
ok('knowledge graph offers Export on every row', has('Knowledge Graph', '.ws-export'));
ok('Delete on a graph this agent owns', !!kgRow('Own graph')?.querySelector('.ws-del'));
ok('no Delete on a graph merely assigned to it (another agent owns it)', !kgRow('Assigned graph')?.querySelector('.ws-del'));
ok('no Delete on a subscribed copy (it is removed by unsubscribing)', !kgRow('Subscribed graph')?.querySelector('.ws-del'));
kgRow('Own graph')?.querySelector('.ws-export')?.click(); await settle();
ok('KG Export opens the graph\'s own export menu, for that graph', globalThis.__acts.some(([a, pid, k]) => a === 'kg.export' && pid === 'p1' && k === 'kOwn'));
sectionOf(root, 'Knowledge Graph')?.querySelector('.ws-sec-imp')?.click(); await settle();
ok('KG Import starts the graph\'s own import, on an icon-only button, with a refresh callback',
    globalThis.__acts.some(([a, icon, cb]) => a === 'kg.import' && icon === '1' && cb === 'function'));
kgRow('Own graph')?.querySelector('.ws-del')?.click(); await settle();
ok('KG Delete asks first, then clears exactly that graph', globalThis.__acts.some(([a]) => a === 'confirm') && deletes.includes('/api/v1/knowledge-graph/clear?kg_id=kOwn'));
// one vocabulary for the words
const words = (sel, attr = 'title') => names.flatMap(n => rowsOf(n).map(r => r.querySelector(sel)?.attrs[attr]).filter(Boolean));
ok('every Export says "Export this <thing>"', words('.ws-export').length >= 4 && words('.ws-export').every(t => /^Export this \w/.test(t)));
ok('every Delete says "Delete this <thing>"', words('.ws-del').length >= 5 && words('.ws-del').every(t => /^Delete this \w/.test(t)));
ok('every Open says what it opens', words('.ws-open').length >= 8 && words('.ws-open').every(t => /^Open /.test(t) && t !== 'Open'));
ok('the dashboard row names its thing', rowsOf('Dashboard')[0]?.querySelector('.ws-del')?.attrs.title === 'Delete this dashboard');
ok('a row\'s accessible name carries the item, so two rows never read the same',
    rowsOf('Dashboard')[0]?.querySelector('.ws-open')?.attrs['aria-label'] === 'Open this dashboard Sales');
const imports = names.map(n => sectionOf(root, n)?.querySelector('.ws-sec-imp')?.attrs.title).filter(Boolean);
ok('every Import says "Import a/an <thing>"', imports.length === 4 && imports.every(t => /^Import an? \w/.test(t)), imports.join(' | '));
ok('every "+" is named, and never just "New"', names.filter(n => n !== 'EXPLAIN Plan').every(n => { const t = sectionOf(root, n)?.querySelector('.ws-sec-new')?.attrs.title || ''; return t.length > 3 && t !== 'New'; }));

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `\nALL PASS — ${pass} passed, 0 failed  (${pass} assertions)`);
process.exit(fail ? 1 : 0);
