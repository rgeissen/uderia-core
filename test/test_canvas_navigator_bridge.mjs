#!/usr/bin/env node
/**
 * The SQL canvas ↔ Database Navigator bridge — EXECUTED under a DOM stub (no browser, no network).
 *
 * The canvas CONSUMES the navigator's catalog; it never reimplements a schema browser. Four
 * increments, each driven with the shipped code:
 *   1. schema-aware completion — the pure helpers (aliases, context, identifier, join snippets)
 *      and the completion source against a fake navigator REST; it suggests, never rewrites;
 *      silent when the canvas is on the MCP bridge or the component is disabled (403);
 *   2. the bidirectional link — a navigator row drags as its qualified name, the Keys tab offers
 *      "insert join" ONLY while the canvas is open, the clause is the declared key, and the canvas
 *      exposes insertIntoOpenCanvas (false with no open canvas — never a throw);
 *   3. result-grid parity — ONE grid (the navigator's) draws the canvas console, masked by the
 *      server's masked_columns, with an explicit "show values" toggle;
 *   4. the hand-off — the statement lands in the composer as a fenced block, never submitted.
 */
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

const { doc, body, El } = installDom({ state: { currentResourcePanelProfileId: 'p1' }, __canvasSplitMode: false });
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true, writable: true });
globalThis.CSS = { escape: s => s };
globalThis.setTimeout = (fn) => 0;

const cv = await import(pathToFileURL('components/builtin/canvas/renderer.js').href);
const nav = await import(pathToFileURL('components/builtin/database_navigator/renderer.js').href);
const C = cv._internal, N = nav._internal;

// ── [1] completion: the pure helpers
const sql = 'SELECT c.CustomerID, s.Total FROM fitness_db.Customers AS c JOIN fitness_db.Sales s ON s.CustomerID = c.CustomerID WHERE c.';
const am = C.sqlAliasMap(sql);
ok('[1] an AS alias resolves to its table', am.get('c') && am.get('c').db === 'fitness_db' && am.get('c').table === 'Customers');
ok('[1] a bare alias resolves too', am.get('s') && am.get('s').table === 'Sales');
ok('[1] the table name itself is a key (an unaliased reference resolves)', am.get('customers') && am.get('customers').db === 'fitness_db');
ok('[1] a keyword after the table is never read as its alias', !am.has('on') && !am.has('where'));
ok('[1] `FROM t WHERE` yields no alias named where', !C.sqlAliasMap('SELECT * FROM t WHERE x = 1').has('where'));

let cc = C.completionContext('SELECT * FROM fitness_db.Customers c WHERE c.');
ok('[1] after `alias.` the context is a member of that alias', cc.kind === 'member' && cc.qualifier === 'c' && cc.word === '');
cc = C.completionContext('SELECT fitness_db.Customers.Cust');
ok('[1] `db.table.` qualifies by the pair and keeps the typed word', cc.kind === 'member' && cc.qualifier === 'fitness_db.Customers' && cc.word === 'Cust');
cc = C.completionContext('SELECT * FROM fit');
ok('[1] after FROM the context is a table', cc.kind === 'table' && cc.word === 'fit');
ok('[1] after JOIN too', C.completionContext('SELECT * FROM a JOIN ').kind === 'table');
ok('[1] inside ON the context is a join key', C.completionContext('SELECT * FROM a JOIN b ON ').kind === 'on');
ok('[1] right after a joined table the JOIN-tail snippets apply', C.completionContext('SELECT * FROM a JOIN fitness_db.Sales s ').kind === 'join_tail');
ok('[1] elsewhere it is a column', C.completionContext('SELECT Cust').kind === 'column');

ok('[1] the identifier at the cursor is the dotted name around it', C.identifierAt('SELECT c.CustomerID FROM x', 12) === 'c.CustomerID');
ok('[1] …a bare word', C.identifierAt('FROM Customers', 8) === 'Customers');
ok('[1] …empty on whitespace', C.identifierAt('a  b', 1) === '' || C.identifierAt('a  b', 2) === '');

let r = C.resolveIdentifier('c.CustomerID', am);
ok('[1] alias.col resolves through the statement', r.database === 'fitness_db' && r.object === 'Customers' && r.column === 'CustomerID');
r = C.resolveIdentifier('fitness_db.Sales', am);
ok('[1] db.table resolves as the pair', r.database === 'fitness_db' && r.object === 'Sales' && r.column === '');
r = C.resolveIdentifier('Sales', am);
ok('[1] a bare table in the statement resolves', r.database === 'fitness_db' && r.object === 'Sales');
r = C.resolveIdentifier('Products', am);
ok('[1] a bare name outside the statement keeps an empty database (the catalog is asked next)', r.database === '' && r.object === 'Products');

const detail = { info: { database: 'fitness_db', name: 'Sales', kind: 'table' },
    columns: [{ name: 'SaleID', data_type: 'INTEGER', is_primary: true, flags: ['key'] }, { name: 'CustomerID', data_type: 'INTEGER', flags: ['key'] }, { name: 'Email', data_type: 'VARCHAR(100)', flags: ['pii'] }],
    foreign_keys_out: [{ child_database: 'fitness_db', child_table: 'Sales', child_columns: ['CustomerID'], parent_database: 'fitness_db', parent_table: 'Customers', parent_columns: ['CustomerID'], declared: true }],
    foreign_keys_in: [{ child_database: 'fitness_db', child_table: 'SaleDetails', child_columns: ['SaleID'], parent_database: 'fitness_db', parent_table: 'Sales', parent_columns: ['SaleID'], declared: true }],
    intelligence: { inferred_joins: [{ child_database: 'fitness_db', child_table: 'Sales', child_columns: ['StoreID'], parent_database: 'fitness_db', parent_table: 'Stores', parent_columns: ['StoreID'], confidence: 0.7, reason: 'name' }] } };
let js = C.joinSnippets(detail, C.sqlAliasMap('SELECT * FROM fitness_db.Sales s'));
ok('[1] a declared FK yields a JOIN snippet using the statement\'s alias', js.some(x => x.apply.startsWith('JOIN fitness_db.Customers') && /ON \w+\.CustomerID = s\.CustomerID/.test(x.apply) && x.detail === 'declared FK'));
ok('[1] the referencing side yields one too', js.some(x => x.apply.startsWith('JOIN fitness_db.SaleDetails') && / = s\.SaleID/.test(x.apply)));
ok('[1] an inferred join is offered AND labelled inferred with its confidence', js.some(x => x.apply.startsWith('JOIN fitness_db.Stores') && x.detail === 'inferred join · 70%'));
ok('[1] a declared key ranks above an inferred one', js.find(x => x.detail === 'declared FK').boost > js.find(x => x.detail.startsWith('inferred')).boost);
js = C.joinSnippets(detail, C.sqlAliasMap('SELECT * FROM fitness_db.Sales s JOIN fitness_db.Customers c ON c.CustomerID = s.CustomerID'));
ok('[1] a partner already in the statement is not offered again', !js.some(x => x.apply.includes('Customers')));
ok('[1] no detail → no snippets, never a throw', C.joinSnippets(null, new Map()).length === 0);

// ── [1b] the completion source against a fake navigator REST
const calls = [];
let sourcesStatus = 200;
globalThis.fetch = async (url) => {
    calls.push(url);
    const json = (status, obj) => ({ ok: status < 400, status, json: async () => obj });
    if (url.includes('/sources')) return json(sourcesStatus, sourcesStatus === 200 ? { sources: [{ id: 'conn:dbconn_1', kind: 'direct', connection_id: 'dbconn_1', driver: 'teradata' }, { id: 'mcp:x', kind: 'mcp' }] } : { status: 'error', code: 'component_disabled', message: 'disabled' });
    if (url.includes('/object?')) return json(200, detail);
    if (url.includes('/objects?')) return json(200, { objects: [{ name: 'Sales', kind: 'table', row_count: 1200 }, { name: 'v_Sales', kind: 'view' }] });
    if (url.includes('/databases')) return json(200, { databases: [{ name: 'fitness_db' }, { name: 'DBC', is_system: true }] });
    if (url.includes('/search')) return json(200, { hits: [{ kind: 'object', database: 'fitness_db', object: 'Sales' }, { kind: 'column', database: 'fitness_db', object: 'Sales', column: 'SaleID' }] });
    return json(404, {});
};
const ctxFor = (text, explicit = true) => ({ pos: text.length, explicit, state: { doc: { toString: () => text } } });
const st = (cid, language = 'sql') => ({ _activeConnectionId: cid, language, getContent: () => '' });

ok('[1b] on the MCP bridge the completion is silent (no catalog, no request)', (await C.navComplete(ctxFor('SELECT * FROM fit'), st(null))) === null && calls.length === 0);
let res = await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s WHERE s.'), st('dbconn_1'));
ok('[1b] `alias.` offers the table\'s columns from the catalog', res && res.options.map(o => o.label).join(',') === 'SaleID,CustomerID,Email');
ok('[1b] a column carries its type and flag chips', res && res.options[2].detail === 'VARCHAR(100) · pii' && res.options[0].detail === 'INTEGER · key');
ok('[1b] a key column ranks first', res && res.options[0].boost > res.options[2].boost);
ok('[1b] completion is scoped to the typed word (validFor) — it suggests, it never rewrites', res && res.from === 'SELECT * FROM fitness_db.Sales s WHERE s.'.length && res.validFor instanceof RegExp);
res = await C.navComplete(ctxFor('SELECT * FROM fitness_db.'), st('dbconn_1'));
ok('[1b] `db.` offers the database\'s objects with their kind', res && res.options.some(o => o.label === 'Sales' && o.detail.startsWith('table')) && res.options.some(o => o.label === 'v_Sales' && o.type === 'interface'));
res = await C.navComplete(ctxFor('SELECT * FROM Sal'), st('dbconn_1'));
ok('[1b] after FROM the catalog search offers qualified tables + databases (system ones hidden)', res && res.options.some(o => o.label === 'fitness_db.Sales') && res.options.some(o => o.label === 'fitness_db') && !res.options.some(o => o.label === 'DBC'));
res = await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s '), st('dbconn_1'));
ok('[1b] after a joined table the declared-FK JOIN snippets are offered', res && res.options.some(o => o.apply && o.apply.startsWith('JOIN fitness_db.Customers')));
res = await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s WHERE ', false), st('dbconn_1'));
ok('[1b] a bare column context with nothing typed is silent unless invoked explicitly (a table IS in scope — the guard is load-bearing)', res === null);
res = await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s WHERE ', true), st('dbconn_1'));
ok('[1b] …and invoked explicitly (Ctrl-Space) it offers the statement\'s columns', res && res.options.some(o => o.label === 'Email'));
res = await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s WHERE Cust'), st('dbconn_1'));
ok('[1b] a bare column completes from every table in the statement', res && res.options.some(o => o.label === 'CustomerID'));
const before = calls.filter(u => u.includes('/object?')).length;
await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s WHERE s.'), st('dbconn_1'));
ok('[1b] the catalog is fetched once per object (cached)', calls.filter(u => u.includes('/object?')).length === before);
C.navCache.sources.clear(); sourcesStatus = 403;
ok('[1b] the component disabled for the profile (403) → the bridge is silent, never an error', (await C.navComplete(ctxFor('SELECT * FROM fitness_db.Sales s WHERE s.'), st('dbconn_2'))) === null);
sourcesStatus = 200; C.navCache.sources.clear();
ok('[1b] a connection the navigator does not list → silent', (await C.navComplete(ctxFor('SELECT * FROM x a WHERE a.'), st('dbconn_unknown'))) === null);

// the completion source is REGISTERED as language data on the SQL editor only
const src = readFileSync('components/builtin/canvas/renderer.js', 'utf8');
ok('[1c] the SQL editor registers the navigator completion as language data (basicSetup\'s autocompletion picks it up)',
   /language === 'sql' && langExt\[0\][\s\S]{0,200}language\.data\.of\(\{ autocomplete: \(ctx\) => _navComplete\(ctx, state\) \}\)/.test(src));

// ── [2] the bidirectional link
const grid = N.resultGrid;
ok('[2] the navigator exports ONE result grid + the join clause + its css for the canvas', typeof grid === 'function' && typeof N.joinClause === 'function' && typeof N.css === 'function');
const fk = detail.foreign_keys_out[0];
let jc = N.joinClause(fk, 'out');
ok('[2] the join clause (out) joins the parent on the declared key', jc.clause === 'JOIN fitness_db.Customers ON fitness_db.Sales.CustomerID = fitness_db.Customers.CustomerID' && jc.other === 'fitness_db.Customers');
jc = N.joinClause(detail.foreign_keys_in[0], 'in');
ok('[2] the join clause (in) joins the referencing table', jc.clause.startsWith('JOIN fitness_db.SaleDetails ON fitness_db.SaleDetails.SaleID = fitness_db.Sales.SaleID'));
const multi = { child_database: 'd', child_table: 'a', child_columns: ['k1', 'k2'], parent_database: 'd', parent_table: 'b', parent_columns: ['p1', 'p2'] };
ok('[2] a composite key joins on every column', N.joinClause(multi, 'out').clause === 'JOIN d.b ON d.a.k1 = d.b.p1 AND d.a.k2 = d.b.p2');

// the tree rows and search hits drag as qualified names
const tree = new El('div', { id: 'dbnav-tree' }); body.appendChild(tree); doc._register('dbnav-tree', tree);
N.S.sources = [{ id: 'conn:dbconn_1', kind: 'direct', connection_id: 'dbconn_1' }]; N.S.source = 'conn:dbconn_1';
N.S.dbs = [{ name: 'fitness_db', object_count: 2 }]; N.S.open = new Set(['fitness_db']);
N.S.objects = { fitness_db: [{ name: 'Sales', kind: 'table', qualified: 'fitness_db.Sales' }, { name: 'Customers', kind: 'table', qualified: 'fitness_db.Customers' }] };
N.S.hits = null;
N.renderTree();
const objRow = tree.querySelectorAll('.dbnav-obj')[0];
ok('[2] a tree object row is draggable', objRow && objRow.getAttribute('draggable') === 'true');
let dragged = null;
objRow._listeners.dragstart[0]({ dataTransfer: { setData: (t, v) => { dragged = [t, v]; } } });
ok('[2] …and drags its qualified name as text/plain (the editor\'s drop cursor takes it)', dragged && dragged[0] === 'text/plain' && dragged[1] === 'fitness_db.Sales');
N.S.hits = [{ kind: 'column', database: 'fitness_db', object: 'Sales', column: 'SaleID', data_type: 'INTEGER' }, { kind: 'object', database: 'fitness_db', object: 'Sales' }]; N.S.query = 'sal';
N.renderHits(tree);
const hitRows = tree.querySelectorAll('.dbnav-hit');
dragged = null; hitRows.find(h => h.dataset.kind === 'column')._listeners.dragstart[0]({ dataTransfer: { setData: (t, v) => { dragged = [t, v]; } } });
ok('[2] a column hit drags as db.table.column', dragged && dragged[1] === 'fitness_db.Sales.SaleID');
N.S.hits = null;

// "insert join" appears in the Keys tab ONLY while the canvas is open
const panel = new El('aside', { id: 'canvas-split-panel' }); body.appendChild(panel); doc._register('canvas-split-panel', panel);
const det = new El('div', { id: 'dbnav-detail' }); body.appendChild(det); doc._register('dbnav-detail', det);
N.S.selected = { db: 'fitness_db', name: 'Sales' }; N.S.detail = { ...detail, indexes: [], unavailable: [], view_text: '' }; N.S.tab = 'keys';
N.renderDetail();
let tabbody = doc.getElementById('dbnav-tabbody');
ok('[2] with the canvas closed the Keys tab offers no insert', tabbody && !tabbody.innerHTML.includes('dbnav-insert-join') && tabbody.innerHTML.includes('declared'));
panel.classList.add('canvas-split--open');
N.renderDetail();
tabbody = doc.getElementById('dbnav-tabbody');
const insBtns = tabbody.querySelectorAll('.dbnav-insert-join');
ok('[2] with the canvas open every relationship row offers "insert join"', insBtns.length === 3, String(insBtns.length));
ok('[2] the row carries the clause spec the button writes', tabbody.querySelectorAll('tr[data-join]').length === 3);
panel.classList.remove('canvas-split--open');
ok('[2] canvas → navigator: the canvas exports insertIntoOpenCanvas', typeof cv.insertIntoOpenCanvas === 'function');
ok('[2] …which is false (never a throw) when no canvas is open', cv.insertIntoOpenCanvas('x') === false);
const navSrc = readFileSync('components/builtin/database_navigator/renderer.js', 'utf8');
ok('[2] the navigator inserts through the canvas\'s ONE seam, not its own editor access', /cv\.insertIntoOpenCanvas && cv\.insertIntoOpenCanvas\(text\)/.test(navSrc) && !/_editorView/.test(navSrc));
ok('[2] "Describe in navigator" opens the navigator on the SAME source at the resolved object + column',
   /openDatabaseNavigatorPanel\(\{ source: source\.id, profile_id: _navProfileId\(\), database: r && r\.database \|\| null, object: r && r\.object \|\| null, column: r && r\.column \|\| null \}\)/.test(src)
   && navSrc.includes("_select(want.db, want.name, (spec && spec.column) || null, true)"));

// ── [3] result-grid parity
const structured = { columns: ['CustomerID', 'Email', 'Total'], rows: [[1, 'maria@x.io', 12.5], [2, null, 3]], masked_columns: ['Email'], row_count: 2, truncated: false, grid_truncated: false };
let html = grid(structured, {});
ok('[3] the grid masks the server-declared personal-data column', html.includes('m•••') && !html.includes('maria@x.io'));
ok('[3] …and marks its header', /<th class="is-masked">Email<\/th>/.test(html));
ok('[3] null stays null, a number is numeric', html.includes('>null<') && html.includes('class="num'));
html = grid(structured, { unmask: true });
ok('[3] the explicit unmask shows the value', html.includes('maria@x.io'));
html = grid({ columns: Array.from({ length: 14 }, (_, i) => 'c' + i), rows: [Array.from({ length: 14 }, (_, i) => i)] }, { maxCols: 10 });
ok('[3] the card form caps columns and says how many more', html.includes('+4'));
ok('[3] the query card draws through the same grid', navSrc.includes("const grid = _resultGrid(s, { maxCols: 10, maxRows: 20 });"));

// the canvas console draws that grid for a structured result, text otherwise
C.setNavigatorModule(nav);
const wrapper = new El('div', { class: 'canvas-wrapper' }); const host = new El('div'); host.appendChild(wrapper);
const container = new El('div'); container.appendChild(host);
const state = { container, language: 'sql', getContent: () => 'SELECT 1' };
// the stub's querySelector walks descendants: wrapper's parentElement is `host`
C.showConsolePanel(state, 'CustomerID\tEmail\n1\tmaria@x.io', 2, 12, false, structured);
await new Promise(r => setImmediate(r));
let consoleBody = host.querySelectorAll('.canvas-console-body')[0];
ok('[3] the console renders the navigator grid for a structured result', consoleBody && consoleBody.classList.contains('canvas-console-body--grid') && consoleBody.innerHTML.includes('dbnav-t'));
ok('[3] …masked, with the toggle and the mask note', consoleBody && consoleBody.innerHTML.includes('m•••') && consoleBody.innerHTML.includes('Personal data masked: Email') && !!consoleBody.querySelectorAll('.canvas-console-unmask')[0]);
consoleBody.querySelectorAll('.canvas-console-unmask')[0]._listeners.click[0]({});
ok('[3] the toggle shows the values and says so', consoleBody.innerHTML.includes('maria@x.io') && consoleBody.innerHTML.includes('Personal data shown'));
C.showConsolePanel(state, 'plain text', 1, 3, false, null);
await new Promise(r => setImmediate(r));
consoleBody = host.querySelectorAll('.canvas-console-body').pop();
ok('[3] without a structured result the console stays the text it always was', consoleBody && consoleBody.textContent === 'plain text' && !consoleBody.classList.contains('canvas-console-body--grid'));
C.showConsolePanel(state, 'ERR', 0, 3, true, structured);
await new Promise(r => setImmediate(r));
consoleBody = host.querySelectorAll('.canvas-console-body').pop();
ok('[3] an error never draws a grid', consoleBody && consoleBody.textContent === 'ERR');
ok('[3] the connector hands the grid through with the SERVER\'s masked_columns (never a client copy of the rule)',
   src.includes("masked_columns: data.masked_columns || []") && !/_pii|piiReason|isPii/.test(src));

// ── [4] the hand-off
const form = new El('form', { id: 'chat-form' }); body.appendChild(form); doc._register('chat-form', form);
const input = new El('textarea', { id: 'user-input' }); form.appendChild(input); doc._register('user-input', input);
input.form = form;
let submitted = false; form.addEventListener('submit', () => { submitted = true; }); input.addEventListener('submit', () => { submitted = true; });
input.requestSubmit = () => { submitted = true; }; form.requestSubmit = () => { submitted = true; }; form.submit = () => { submitted = true; };
let inputEvents = 0; input.addEventListener('input', () => inputEvents++);
ok('[4] the statement lands in the composer as a fenced sql block', C.handOffToComposer({ language: 'sql', getContent: () => 'SELECT 1' }) === true && input.value.includes('```sql\nSELECT 1\n```'));
ok('[4] …firing input (the composer\'s own listeners resize/enable Send) and never submitting', inputEvents === 1 && submitted === false);
ok('[4] an empty canvas hands nothing off', C.handOffToComposer({ language: 'sql', getContent: () => '  ' }) === false);
ok('[4] a MongoDB statement is fenced as json', C.handOffToComposer({ language: 'mongodb', getContent: () => '{"a":1}' }) && input.value.includes('```json'));
ok('[4] the toolbar carries the two acts as icon buttons in the platform\'s button class',
   src.includes("id: 'navigator_bridge'") && src.includes("canvas-toolbar-btn canvas-toolbar-btn--icon canvas-nav-ask") && src.includes("canvas-toolbar-btn canvas-toolbar-btn--icon canvas-nav-describe"));
ok('[4] no emoji on the new controls', !/canvas-nav-(ask|describe)[\s\S]{0,300}[\u{1F300}-\u{1FAFF}]/u.test(src));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
