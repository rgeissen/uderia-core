#!/usr/bin/env node
/**
 * Database Navigator in the Workspace section — the REAL workspacePanelHandler.js
 * EXECUTED under a DOM stub against a fake backend (no browser, no server).
 *
 * The rule under test: the navigator has NO rail tab of its own. When the component
 * is enabled for the active profile it appears as a Workspace section whose rows are
 * the agent's catalog SOURCES (one per source, the resolved default marked active,
 * each opening the split panel on that source) with the recently-viewed objects as
 * uncounted sub-rows beneath. When the component is disabled, no section renders.
 *
 * Every assertion drives `loadWorkspacePanel()` end to end — a registry entry that
 * lists the wrong endpoint, counts the trailer, or opens without the source id fails
 * here, and so does a resurrected rail tab.
 */
import './_uderia_bus.mjs';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

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
    click() { this.dispatchEvent({ type: 'click', target: this, preventDefault() {}, stopPropagation() {} }); }
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
// the handler reads the active profile from the imported state module, falling back to
// window.currentResourcePanelProfileId — the fallback is the seam a stub can set
globalThis.window = { currentResourcePanelProfileId: 'p1' };
globalThis.localStorage = { _m: {}, getItem(k) { return this._m[k] ?? null; }, setItem(k, v) { this._m[k] = String(v); } };

// ── fake backend ──────────────────────────────────────────────────────────────
const NAV_COMP = { component_id: 'database_navigator', display_name: 'Database Navigator', icon_accent: '#84cc16', icon_svg: '<svg></svg>', profile_enabled: true,
    instances: JSON.parse(readFileSync('components/builtin/database_navigator/manifest.json', 'utf8')).instances };   // as the components API serves it
const SOURCES = { status: 'success', default: 'conn:7', reason: 'your default database connection', sources: [
    { id: 'conn:7', kind: 'direct', label: 'Teradata local', driver: 'teradata', detail: 'teradata · td.example.com', is_default: true, reason: 'your default database connection', capabilities: [] },
    { id: 'mcp:s1', kind: 'mcp', label: 'Teradata MCP', driver: 'mcp', detail: 'via MCP tools: base_tableList', reason: '', capabilities: [] },
] };
const calls = [];
let components = [NAV_COMP];
let sources = SOURCES;
// coalescedFetch hands every caller `response.clone()` — a fake without `clone` makes the
// enablement read throw and the section silently vanish, so the fake is faithful there.
const resp = (ok, body) => { const r = { ok, json: async () => body }; r.clone = () => resp(ok, body); return r; };
globalThis.fetch = async (url) => {
    calls.push(String(url));
    if (/\/components$/.test(url)) return resp(true, { components });
    if (/\/database-navigator\/sources/.test(url)) return resp(true, sources);
    return resp(false, {});
};

const { loadWorkspacePanel } = await import(pathToFileURL('static/js/handlers/workspacePanelHandler.js').href);
const SRC = readFileSync('static/js/handlers/workspacePanelHandler.js', 'utf8');

async function render() {
    byId.clear(); calls.length = 0;
    const c = new El('div', { id: 'workspace-panel-content' }); byId.set('workspace-panel-content', c);
    const tab = new El('button', { class: 'resource-tab', 'data-type': 'workspace' }); tab.textContent = 'Workspace'; byId.set('__tab', tab);
    await loadWorkspacePanel();
    c.tab = tab;
    return c;
}

// ── 1. enabled → a section whose rows are the SOURCES ─────────────────────────
let root = await render();
let sec = root.children.find(e => e._matches('.ws-section'));
ok('an enabled navigator renders ONE Workspace section', !!sec && root.children.filter(e => e._matches('.ws-section')).length === 1);
ok('the section lists sources through the navigator\'s own sources endpoint, scoped to the active profile',
    calls.some(u => /\/api\/v1\/database-navigator\/sources\?profile_id=p1$/.test(u)));
const head = sec.querySelector('.ws-sec-head');
ok('section identity comes from the manifest (name + accent)', head.innerHTML.includes('Database Navigator') && head.innerHTML.includes('#84cc16'));
const rows = sec.querySelectorAll('.ws-row').filter(r => !r._matches('.ws-row--sub'));
ok('one row per source', rows.length === 2, `${rows.length}`);
ok('rows are labelled by source label; meta says what kind of source it is',
    rows[0].innerHTML.includes('Teradata local') && rows[0].innerHTML.includes('teradata · td.example.com') && rows[1].innerHTML.includes('MCP tools'));
ok('the resolved default carries the active dot and says so; the other does not',
    rows[0].querySelector('.ws-row-dot')._matches('.is-active') && rows[0].innerHTML.includes('· default') && !rows[1].querySelector('.ws-row-dot')._matches('.is-active') && !rows[1].innerHTML.includes('· default'));
ok('sources are never deletable from here (they are configured elsewhere)', sec.querySelectorAll('.ws-del').length === 0);
ok('the section badge counts SOURCES', head.querySelector('.ws-sec-count').textContent === '2');
ok('no recently-viewed trailer when the browser holds none', !sec.querySelector('.ws-trailer'));

// ── 2. recently viewed → uncounted sub-rows that open AT the object ───────────
localStorage.setItem('dbnav_last_v1', JSON.stringify({ recent: [{ db: 'fitness_db', name: 'Sales' }, { db: 'NIBRS_Source_Domain', name: 'Incident' }, { db: '', name: 'junk' }] }));
root = await render(); sec = root.querySelector('.ws-section');
const trailer = sec.querySelector('.ws-trailer');
ok('recently-viewed objects render as a trailer under the sources', !!trailer && trailer.innerHTML.includes('Recently viewed'));
const subs = sec.querySelectorAll('.ws-row--sub');
ok('one sub-row per valid recent object (a malformed record is skipped)', subs.length === 2 && subs[0].innerHTML.includes('fitness_db.Sales') && subs[1].innerHTML.includes('NIBRS_Source_Domain.Incident'));
ok('the badge still counts sources, never the trailer', sec.querySelector('.ws-sec-count').textContent === '2');
ok('the Workspace TAB reads items · sections — sources counted, never the trailer', root.tab.textContent === 'Workspace (2 · 1)', root.tab.textContent);
ok('sub-rows are open-only', subs.every(r => r.querySelectorAll('.ws-del').length === 0 && r.querySelectorAll('.ws-open').length === 1));

// ── 3. the open contract (source id + profile; recent → database + object) ────
// The registry's `open` dynamic-imports the shipped renderer by its SERVED URL, which
// Node cannot resolve; the contract is asserted on the source, where the arguments are.
const navEntry = SRC.slice(SRC.indexOf('database_navigator: {'), SRC.indexOf('// ── Database Navigator: recently viewed'));
ok('a source row opens the navigator ON that source, for the active profile',
    /openDatabaseNavigatorPanel\(\{\s*source:\s*it\.id,\s*profile_id:\s*profileId\s*\}\)/.test(navEntry));
ok('a recent row opens the navigator AT the object on the resolved default source',
    /openDatabaseNavigatorPanel\(\{\s*source:\s*dflt\.id,\s*database:\s*r\.db,\s*object:\s*r\.name,\s*profile_id:\s*profileId\s*\}\)/.test(SRC));
ok('open() receives the profile id from the load path (not re-derived from a global)', /reg\.open\(it,\s*profileId\)/.test(SRC));

// ── 4. empty sources → the honest remedy, no rows ─────────────────────────────
sources = { status: 'success', default: null, reason: 'no source: add a Database Connection …', sources: [] };
root = await render(); sec = root.querySelector('.ws-section');
ok('no sources → the section names BOTH remedies instead of an empty list',
    !!sec && sec.querySelectorAll('.ws-row').length === 0 && /Database Connection/.test(sec.deepHTML) && /MCP server/.test(sec.deepHTML));
ok('no sources → no trailer either (nothing to open a recent object on)', !sec.querySelector('.ws-trailer'));
sources = SOURCES;

// ── 5. gating: disabled for the profile → no section, no fetch ────────────────
components = [{ ...NAV_COMP, profile_enabled: false }];
root = await render();
ok('a navigator disabled for the profile renders no section', root.querySelectorAll('.ws-section').length === 0);
ok('…and never asks the navigator backend for its sources', !calls.some(u => /database-navigator/.test(u)));
components = [NAV_COMP];

// ── 6. the rail tab is GONE (structural) ──────────────────────────────────────
const IDX = readFileSync('templates/index.html', 'utf8');
const EH = readFileSync('static/js/eventHandlers.js', 'utf8');
const RENDERER = readFileSync('components/builtin/database_navigator/renderer.js', 'utf8');
ok('index.html has no Database rail item / tab / panel', !/data-type="database"/.test(IDX) && !/id="database-panel/.test(IDX) && !/rail-badge-database/.test(IDX));
ok('eventHandlers no longer lazy-loads a database rail panel', !/renderDatabaseRailPanel|type === 'database'/.test(EH));
ok('the renderer exports no rail panel (two surfaces: card + split panel)', !/export\s+(async\s+)?function\s+renderDatabaseRailPanel/.test(RENDERER) && /export\s+async\s+function\s+openDatabaseNavigatorPanel/.test(RENDERER));
ok('the Workspace section is the ONLY resource-panel route to the navigator', (SRC.match(/openDatabaseNavigatorPanel/g) || []).length >= 2 && !/renderDatabaseRailPanel/.test(SRC));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
