#!/usr/bin/env node
/**
 * Database Navigator — the renderer, EXECUTED under a DOM stub (no browser, no server).
 *
 * The shipped module is imported as-is (globals stubbed first); every assertion runs a
 * real function on real payload shapes — the ones the handler and the REST routes emit —
 * so a renderer that drops a fact, renders a wire token, or invents a value fails here.
 */
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

// ── DOM stub ──────────────────────────────────────────────────────────────────
class El {
    constructor(tag, attrs = {}) {
        this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.children = []; this.parentNode = null;
        this.dataset = {}; this.style = { setProperty() {}, removeProperty() {} }; this._html = ''; this.textContent = ''; this.value = '';
        this._classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean)); this._listeners = {};
        this.classList = { add: (...c) => c.forEach(x => this._classes.add(x)), remove: (...c) => c.forEach(x => this._classes.delete(x)),
            contains: c => this._classes.has(c), toggle: (c, on) => { if (on === undefined) { this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c); } else on ? this._classes.add(c) : this._classes.delete(c); } };
        for (const [k, v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    get innerHTML() { return this._html; }
    set innerHTML(h) { this._html = String(h); this.children = parseHtml(this._html, this); }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    _walk(out = []) { for (const c of this.children) { out.push(c); c._walk(out); } return out; }
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
    removeEventListener() {}
    dispatchEvent(ev) { (this._listeners[ev.type] || []).forEach(fn => fn(ev)); if (ev.type === 'click' && this.onclick) this.onclick(ev); return true; }
    click() { this.dispatchEvent({ type: 'click', target: this, preventDefault() {}, stopPropagation() {} }); }
    scrollIntoView() {} focus() {}
    get offsetHeight() { return 100; }
}
// a deliberately small HTML parser: enough to find elements by tag/class/id/data-* attributes
function parseHtml(html, parent) {
    const out = []; const stack = [parent]; const re = /<\/?([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g; let m;
    while ((m = re.exec(html))) {
        const [full, tag, rawAttrs, selfClose] = m;
        if (full.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
        const attrs = {}; const ar = /([\w-]+)(?:="([^"]*)")?/g; let a;
        while ((a = ar.exec(rawAttrs))) attrs[a[1]] = a[2] === undefined ? '' : a[2];
        const el = new El(tag, attrs);
        const p = stack[stack.length - 1]; el.parentNode = p; if (p === parent) out.push(el); else p.children.push(el);
        // text content: up to the next tag
        const rest = html.slice(re.lastIndex); const t = rest.slice(0, rest.indexOf('<') < 0 ? rest.length : rest.indexOf('<'));
        el.textContent = t.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').trim();
        if (!selfClose && !['svg', 'path', 'ellipse', 'circle', 'polyline', 'polygon', 'line', 'rect', 'input', 'br', 'kbd'].includes(tag) || tag === 'svg') {
            if (!selfClose && !['input', 'br'].includes(tag)) stack.push(el);
        }
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
    addEventListener() {}, activeElement: null,
};
doc.documentElement.style = { setProperty() {}, removeProperty() {} };
globalThis.document = doc;
globalThis.window = { state: { currentResourcePanelProfileId: 'p1' }, __canvasSplitMode: false, PanelIcons: (await import('../static/js/handlers/panelIcons.js')).ICON };
globalThis.localStorage = { _m: {}, getItem(k) { return this._m[k] ?? null; }, setItem(k, v) { this._m[k] = String(v); } };
// Node ≥ 21 defines `navigator` as a getter-only global — assignment throws; define it.
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true, writable: true });
globalThis.fetch = async () => { throw new Error('no network in CI'); };
globalThis.CSS = { escape: s => s };
const timers = []; globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; }; globalThis.clearTimeout = () => {};

await import(pathToFileURL('static/js/handlers/panelOpenControl.js').href);   // window.PanelOpenControl
const mod = await import(pathToFileURL('components/builtin/database_navigator/renderer.js').href);
const { renderDatabaseNavigator, _internal: X } = mod;
const SRC = readFileSync('components/builtin/database_navigator/renderer.js', 'utf8');

// ── helpers ───────────────────────────────────────────────────────────────────
ok('esc escapes the four characters', X.esc('<a href="x">&</a>') === '&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
ok('hl escapes THEN highlights (no injection through a query)', X.hl('<Sales>', 'sale') === '&lt;<mark>Sale</mark>s&gt;');
ok('hl ignores 1-char terms', X.hl('Sales', 's') === 'Sales');
ok('num formats with separators, blank on null', X.num(12345678) === (12345678).toLocaleString() && X.num(null) === '');
ok('kind icons: table/view/other are distinct monochrome SVGs', X.kindIcon('table') !== X.kindIcon('view') && X.kindIcon('macro') === X.kindIcon('other') && !/#[0-9a-f]{6}/i.test(X.kindIcon('table')));

// ── flag chips ────────────────────────────────────────────────────────────────
const chips = X.flagChips(['measure', 'pii', 'key', 'text_date'], { allowed_values: [{ value: '0', label: 'Female' }, { value: '1', label: 'Male' }], sampled_shape: 'DD-MMM-YYYY' });
ok('flag chips render in the fixed order key → pii → text_date → measure', chips.indexOf('f-key') < chips.indexOf('f-pii') && chips.indexOf('f-pii') < chips.indexOf('f-text_date') && chips.indexOf('f-text_date') < chips.indexOf('f-measure'));
ok('every flag chip carries a plain-language tooltip (u-tip, never a native title)', (chips.match(/data-tip=/g) || []).length >= 6 && !/ title=/.test(chips));
ok('ontology decoded values + sampled shape become chips', chips.includes('2 values') && chips.includes('DD-MMM-YYYY') && chips.includes('0=Female'));
ok('an unknown flag is ignored, not rendered raw', !X.flagChips(['zzz'], null).includes('zzz'));

// ── inline card: describe ─────────────────────────────────────────────────────
function card(spec) { const c = new El('div', { id: 'c1' }); byId.set('c1', c); renderDatabaseNavigator('c1', { spec }); return c; }
const DETAIL = { info: { database: 'fitness_db', name: 'Sales', qualified: 'fitness_db.Sales', kind: 'table', row_count: 12345678, row_count_source: 'statistics' },
    columns: [{ name: 'SaleID', data_type: 'INTEGER', flags: ['key'] }, { name: 'Email', data_type: 'VARCHAR(100)', flags: ['pii', 'text'] }, ...Array.from({ length: 9 }, (_, i) => ({ name: `c${i}`, data_type: 'INTEGER', flags: ['measure'] }))],
    indexes: [], foreign_keys_out: [{ parent_table: 'Customers', child_columns: ['CustomerID'], parent_columns: ['CustomerID'], declared: true }], foreign_keys_in: [],
    intelligence: { role: 'fact', role_label: 'Fact', flag_counts: { key: 1, pii: 1, measure: 9 }, scope: 'out_of_scope' } };
let c = card({ action: 'describe', database: 'fitness_db', object: 'Sales', detail: DETAIL, source: { id: 'conn:x', kind: 'direct', label: 'MyTD' }, message: 'm' });
let h = c.innerHTML;
ok('describe card: qualified name, role chip, row count with its provenance, references', h.includes('fitness_db.Sales') && h.includes('>Fact<') && h.includes('(stats)') && h.includes('references Customers'));
ok('describe card: out-of-scope is SAID', h.includes('outside trusted scope'));
ok('describe card: flag counts as chips (key/pii/measure)', h.includes('1 key') && h.includes('1 pii') && h.includes('9 measure'));
ok('describe card: first 8 columns + an honest "+N more"', h.includes('+3 more columns'));
ok('describe card: source named (connection vs MCP)', h.includes('MyTD · connection'));
ok('describe card: the shared open control reads View in Canvas when the panel is closed', h.includes('sp-open-ctl') && h.includes('data-sp-open="database_navigator"') && h.includes('View in Canvas'));
ok('describe card: no auto-open unless split mode / open action', timers.length === 0);

// ── inline card: search / preview / joins / error / open ─────────────────────
c = card({ action: 'search', query: 'sale', hits: [{ kind: 'object', database: 'fitness_db', object: 'Sales', score: 105 }, { kind: 'column', database: 'fitness_db', object: 'Sales', column: 'SaleDate', score: 40 }] });
h = c.innerHTML;
ok('search card: count + hits with their path', h.includes('2 matches for') && h.includes('SaleDate') && h.includes('fitness_db.Sales'));
ok('search card: a hit routes to the panel at that object', c.querySelectorAll('.dbnav-hit').length === 2 && c.querySelector('.dbnav-hit').dataset.name === 'Sales');
c = card({ action: 'search', query: 'zzz', hits: [] });
ok('search card: honest miss', c.innerHTML.includes('Nothing named like'));
c = card({ action: 'preview', database: 'fitness_db', object: 'Sales', preview: { columns: ['SaleID', 'Email'], rows: [[1, 'j•••'], [2, null]], masked_columns: ['Email'] } });
h = c.innerHTML;
ok('preview card: masked columns named, null rendered as null', h.includes('masked: Email') && h.includes('>null<') && h.includes('j•••'));
c = card({ action: 'suggest_joins', database: 'db', object: 'Sales', declared_out: [{ child_table: 'Sales', child_columns: ['CustomerID'], parent_table: 'Customers', parent_columns: ['CustomerID'] }], declared_in: [], inferred: [{ child_table: 'SaleDetails', child_columns: ['SaleID'], parent_table: 'Sales', parent_columns: ['SaleID'], confidence: 0.8 }] });
h = c.innerHTML;
ok('joins card: declared and inferred are labelled apart, with the confidence', h.includes('>declared<') && h.includes('inferred · 80%'));
c = card({ action: 'describe', error: 'no_source', message: 'no source: add a Database Connection' });
ok('error card: the remedy is shown, no fake detail', c.innerHTML.includes('add a Database Connection') && !c.innerHTML.includes('dbnav-mini'));
timers.length = 0;
c = card({ action: 'open', database: 'fitness_db' });
ok('open action auto-opens the panel', timers.length === 1);
timers.length = 0; window.__canvasSplitMode = true;
c = card({ action: 'describe', database: 'd', object: 'o', detail: DETAIL, source: { id: 's', kind: 'mcp', label: 'M' } });
ok('split mode auto-opens on any successful card', timers.length === 1 && c.innerHTML.includes('M · MCP'));
window.__canvasSplitMode = false; timers.length = 0;
c = card({ action: 'describe', error: 'catalog', message: 'boom' }); window.__canvasSplitMode = true; card({ action: 'describe', error: 'catalog', message: 'boom' }); window.__canvasSplitMode = false;
ok('an error card never auto-opens', timers.length === 0);

// ── panel detail: capability-honest facts ─────────────────────────────────────
const detailHost = new El('div', { id: 'dbnav-detail' }); byId.set('dbnav-detail', detailHost);
X.S.selected = { db: 'fitness_db', name: 'Sales' };
X.S.detail = { ...DETAIL, columns: DETAIL.columns.slice(0, 2), unavailable: ['foreign keys'], view_text: '', ontology: { present: true, entity: { name: 'Sales', description: 'One row per sale', row_count: 100, key_unique: true }, declared_keys: ['SaleID'], related: [] },
    intelligence: { ...DETAIL.intelligence, role_reasons: ['declared foreign keys plus measures'], keys: { declared: [{ columns: ['SaleID'], kind: 'primary_key' }], inferred: [] }, inferred_joins: [], questions: ['How many rows does Sales have?'], sample_sql: 'SELECT TOP 20 "SaleID"\nFROM "fitness_db"."Sales"', size_human: '320.0 KB', stats_age_days: 8, actions: [{ id: 'chart' }],
        source: { id: 'conn:x', kind: 'direct', label: 'MyTD', capabilities: ['preview', 'row_count', 'statistics', 'sizes', 'indexes', 'foreign_keys'] } } };
const hOf = () => detailHost.innerHTML + (doc.getElementById('dbnav-tabbody')?.innerHTML || '');
X.renderDetail();
h = hOf();
ok('detail: role chip carries its reasons as the tooltip', h.includes('data-tip="declared foreign keys plus measures"'));
ok('detail: row count says it is from statistics and how old', h.includes('from statistics, 8d old'));
ok('detail: the ontology description is shown beside the dictionary comment', h.includes('One row per sale'));
ok('detail: "not available from this source" is SAID for a failed enrichment read', h.includes('Not available from this source: foreign keys'));
ok('detail: actions follow capabilities (preview + count) and the ontology (graph) and the chart hint', ['data-act="preview"', 'data-act="count"', 'data-act="graph"', 'data-act="chart"', 'data-act="copy"', 'data-act="ask"'].every(a => h.includes(a)));
ok('detail: out-of-scope chip with its explanation', h.includes('outside trusted scope') && h.includes('grounding gate'));
ok('detail: tabs incl. Ontology when present, Definition absent for a table', h.includes('data-tab="ontology"') && !h.includes('data-tab="definition"'));
ok('detail: columns tab marks the key column and its pii chip', h.includes('f-pii') && h.includes('SaleID'));
X.S.detail.intelligence.source.capabilities = ['columns'];
X.S.detail.unavailable = []; X.S.detail.intelligence.size_human = '';
X.renderDetail(); h = hOf();
ok('detail: an MCP-shaped source says "not available" for keys/references — never a blank or a zero', h.includes('not available') && !h.includes('data-act="preview"') && !h.includes('data-act="count"'));
ok('detail: size/keys read "not available" when the source lacks the capability (never a blank)', (h.match(/not available/g) || []).length >= 2);
X.S.tab = 'keys'; X.renderDetail(); h = hOf();
ok('keys tab: declared relationship rendered as declared, jump link to the parent', h.includes('>declared<') && h.includes('class="dbnav-jump"'));
X.S.tab = 'ask'; X.renderDetail(); h = hOf();
ok('ask tab: suggested question + sample SQL verbatim (escaped)', h.includes('How many rows does Sales have?') && h.includes('&quot;fitness_db&quot;.&quot;Sales&quot;'));
X.S.tab = 'preview'; X.S.preview = { error: 'database error 3807: no such object' }; X.renderDetail(); h = hOf();
ok('preview tab: a failed preview shows the database error, not a blank grid', h.includes('Preview failed') && h.includes('3807'));
X.S.preview = { loading: true }; let threw = false; try { X.renderDetail(); } catch (e) { threw = true; } h = hOf();
ok('preview tab: the in-flight state renders as loading and never throws (the live check caught a .map on undefined)', !threw && h.includes('Sampling rows'));
X.S.preview = { columns: ['a', 'b'], rows: [[1, null]], truncated: true, masked_columns: [], sql: 'SELECT TOP 1 * FROM x', elapsed_ms: 12 }; X.renderDetail(); h = hOf();
ok('preview tab: a result renders the grid, the limited note, timing and the statement', h.includes('>null<') && h.includes('(limited)') && h.includes('12 ms') && h.includes('SELECT TOP 1'));

// ── tree: system databases hidden by default and COUNTED ─────────────────────
const tree = new El('div', { id: 'dbnav-tree' }); byId.set('dbnav-tree', tree);
X.S.source = 'conn:x'; X.S.hits = null; X.S.showSystem = false;
X.S.dbs = [{ name: 'fitness_db', object_count: 5, is_system: false }, { name: 'DBC', is_system: true }, { name: 'TD_SYSFNLIB', is_system: true }];
X.renderTree(); h = tree.innerHTML;
ok('tree: system databases hidden and their number stated', h.includes('fitness_db') && !h.includes('>DBC<') && h.includes('2 system databases hidden'));
X.S.showSystem = true; X.renderTree(); h = tree.innerHTML;
ok('tree: system toggle reveals them, styled as system', h.includes('is-system') && h.includes('TD_SYSFNLIB'));
X.S.open.add('fitness_db'); X.S.objects['fitness_db'] = [{ name: 'Sales', kind: 'table', row_count: 100, row_count_source: 'statistics' }, { name: 'v_top', kind: 'view' }];
X.renderTree(); h = tree.innerHTML;
ok('tree: an expanded database lists objects with a ~ on a statistics row count', h.includes('100~') && h.includes('v_top'));
X.S.hits = [{ kind: 'object', database: 'fitness_db', object: 'Sales' }, { kind: 'column', database: 'fitness_db', object: 'Sales', column: 'SaleDate', data_type: 'TIMESTAMP' }, { kind: 'database', database: 'fitness_db' }];
X.S.query = 'sale'; X.renderTree(); h = tree.innerHTML;
ok('hits: grouped by kind with counts and highlighted matches', h.includes('Databases · 1') && h.includes('Tables · 1') && h.includes('Columns · 1') && h.includes('<mark>Sale</mark>'));
X.S.hits = []; X.renderTree();
ok('hits: an honest empty result explains what search matches', tree.innerHTML.includes('names and comments, not questions'));
X.S.source = null; X.S.hits = null; X.S.reason = 'no source: add a Database Connection'; X.renderTree();
ok('tree: no source → the remedy from the backend, verbatim', tree.innerHTML.includes('add a Database Connection'));

// ── structural rules ──────────────────────────────────────────────────────────
ok('renderer never hardcodes a theme colour outside the accent fallback', !/#(?!84cc16)[0-9a-f]{6}\b/i.test(SRC.replace(/var\(--[^)]*\)/g, '')) );
ok('renderer never uses emoji', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(SRC));
ok('renderer never re-derives a role or a flag (it reads intelligence.*)', !/infer_role|column_flags|is_pii\s*=/.test(SRC) && /intel\.role_label/.test(SRC));
ok('the split panel is exported and the rail panel is NOT (the resource panel reaches the navigator through the Workspace section — test_dbnav_workspace)', typeof mod.renderDatabaseRailPanel === 'undefined' && typeof mod.openDatabaseNavigatorPanel === 'function' && typeof mod.closeDatabaseNavigatorPanel === 'function');
ok('previews are requested masked (unmask is never sent by the panel)', !/unmask:\s*true/.test(SRC));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
