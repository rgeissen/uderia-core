#!/usr/bin/env node
/**
 * The Database Navigator `query` action's UX — EXECUTED under a DOM stub (no browser).
 *
 * Three surfaces, each driven with the real shipped code:
 *   1. the inline query card (renderer.js): statement, result grid, personal data masked
 *      in the view only, "Open in canvas" bound to the same connection;
 *   2. the canvas accepting that binding (renderer.js of the canvas component);
 *   3. the profile-editor controls (configurationHandler.js): "Allow queries" + the
 *      databases it may query — and a save that never DROPS the stored block (the
 *      editor rebuilds componentConfig from the fields it models; a key it did not model
 *      used to vanish on every save).
 */
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

const { doc, body, El } = installDom({ state: { currentResourcePanelProfileId: 'p1' }, __canvasSplitMode: false });
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true, writable: true });
globalThis.CSS = { escape: s => s };
const timers = []; globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };

const mod = await import(pathToFileURL('components/builtin/database_navigator/renderer.js').href);
const X = mod._internal;

// ── [1] the card-only mask mirrors the server's mask_value
const svc = readFileSync('src/trusted_data_agent/dbcatalog/service.py', 'utf8');
const serverMask = (svc.match(/^MASK = "([^"]+)"/m) || [])[1];
ok('[1] the card masks with the server\'s mask string', serverMask && X.maskCell('Maria') === 'M' + serverMask, `${serverMask} ${X.maskCell('Maria')}`);
ok('[1] a one-character value is fully masked', X.maskCell('M') === serverMask);
ok('[1] null stays null (shown as null, never masked into text)', X.maskCell(null) === null);

// ── [2] the inline query card
function card(spec) {
    const host = new El('div', { id: 'c1' }); body.appendChild(host); doc._register('c1', host);
    mod.renderDatabaseNavigator('c1', { spec });
    const html = host.innerHTML; host.parentNode.children.splice(host.parentNode.children.indexOf(host), 1);
    return { host, html };
}
const Q = {
    action: 'query', sql: 'SELECT name, stars FROM review', row_count: 150, shown: 100, truncated: true,
    statement: 2, columns: ['name', 'stars'], rows: Array.from({ length: 100 }, (_, i) => [`Reviewer ${i}`, 4]),
    masked_columns: ['name'],
    source: { id: 'conn:dbconn_1', kind: 'direct', label: 'shop', dialect: 'sqlite', connection_id: 'dbconn_1' },
};
let { host, html } = card(Q);
ok('[2] the card is titled Query', html.includes('dbnav-card-t">Query<'), html.slice(0, 200));
ok('[2] the statement that ran is shown', html.includes('SELECT name, stars FROM review'));
ok('[2] the statement number is shown', html.includes('statement 2'));
ok('[2] the row total is stated', html.includes('150'));
ok('[2] the masked column is masked in the view', html.includes('R' + serverMask) && !html.includes('Reviewer 0<'), html.match(/<td[^>]*>[^<]*<\/td>/)?.[0]);
ok('[2] …and the card says so', /Personal data masked in this view: name/.test(html));
ok('[2] the grid shows at most 20 rows', (html.match(/<tr>/g) || []).length <= 21, String((html.match(/<tr>/g) || []).length));
ok('[2] it says the rest exist and how many the agent read', /Showing 20 of 150 rows/.test(html) && /the agent read 100/.test(html));
ok('[2] "Open in canvas" is offered for a direct connection', html.includes('Open in canvas'));
ok('[2] the answer is not interrupted: a query result never pops the navigator panel',
   (() => { const before = timers.length; window.__canvasSplitMode = true; card(Q); window.__canvasSplitMode = false; return timers.length === before; })());

({ html } = card({ ...Q, source: { ...Q.source, connection_id: '' } }));
ok('[2] no "Open in canvas" without a connection to bind to', !html.includes('Open in canvas'));
({ html } = card({ ...Q, masked_columns: [] }));
ok('[2] without masking the values are shown', html.includes('Reviewer 0') && !html.includes('Personal data masked'));
({ html } = card({ action: 'query', error: 'not_read_only', message: 'refusing to run a statement that writes — nothing was executed',
                   sql: 'DELETE FROM review', source: Q.source }));
ok('[2] a refused query shows why and the statement', html.includes('nothing was executed') && html.includes('DELETE FROM review'));
ok('[2] …and can still be opened in the canvas to fix it', html.includes('Open in canvas'));
({ html } = card({ ...Q, original_sql: 'SELECT name, stars FROM review LIMIT 5', rewrites: [{ executed_sql: 'x' }] }));
ok('[2] a rewrite names what the agent wrote', html.includes('Rewritten before it ran') && html.includes('LIMIT 5'));

// clicking "Open in canvas" hands the canvas the statement AND the connection
({ host } = card(Q));
const cvBtn = host.querySelectorAll('button').find(b => (b._html || '').includes('Open in canvas') || (b.innerHTML || '').includes('Open in canvas'));
ok('[2] the button exists as an element', !!cvBtn);
const canvasSrc = readFileSync('components/builtin/database_navigator/renderer.js', 'utf8');
// the language follows the source: 'mongodb' for a MongoDB statement (JSON), 'sql' otherwise
ok('[2] the canvas is opened with the statement, the source\'s language and the connection id',
   /renderCanvas\(tmp\.id, \{ component: 'canvas', action: 'open', view: 'canvas',\s*title, content: sql, language: language === 'mongodb' \? 'mongodb' : 'sql',\s*connection_id: connectionId/.test(canvasSrc));
ok('[2] a query card hands the canvas its own language',
   canvasSrc.includes("language: s.language || s.source.language || 'sql'"));

// ── [3] the navigator panel's "Query in canvas"
X.S.sources = [{ id: 'conn:dbconn_1', kind: 'direct', connection_id: 'dbconn_1' }, { id: 'mcp:x', kind: 'mcp' }];
X.S.source = 'conn:dbconn_1';
ok('[3] a direct source yields its canvas connection id', X.srcConnectionId() === 'dbconn_1');
ok('[3] a SQL source opens SQL in the canvas', X.srcLanguage() === 'sql');
X.S.sources = [{ id: 'conn:m', kind: 'direct', driver: 'mongodb', connection_id: 'm' }];
X.S.source = 'conn:m';
ok('[3] a MongoDB source opens its statement as mongodb', X.srcLanguage() === 'mongodb');
X.S.sources = [{ id: 'conn:dbconn_1', kind: 'direct', connection_id: 'dbconn_1' }, { id: 'mcp:x', kind: 'mcp' }];
X.S.source = 'conn:dbconn_1';
X.S.source = 'mcp:x';
ok('[3] an MCP source yields none (no Query in canvas there)', X.srcConnectionId() === '');

// ── [4] the canvas honours the binding BEFORE its state is built
const cv = readFileSync('components/builtin/canvas/renderer.js', 'utf8');
const seed = cv.indexOf('if (spec && spec.connection_id)');
const silent = cv.indexOf('const isSilent = ');
const stateInit = cv.indexOf("_activeConnectionId: _canvasConnections.get(canvasTitle.toLowerCase().trim())");
ok('[4] renderCanvas seeds the connection from spec.connection_id', seed > 0 && seed < silent, `${seed} ${silent}`);
ok('[4] …keyed exactly as the state reads it (title, lower-cased, trimmed)',
   cv.includes("_canvasConnections.set(String(spec.title || 'Canvas').toLowerCase().trim(), spec.connection_id)") && stateInit > 0);

// ── [5] the profile-editor controls — moved to the Data sources section (PROP-006). The
// contract they kept (absent = off, carry-forward before render, a stored id this instance
// lacks stays visible, a non-direct driver is not offered, switching OFF keeps a chosen list)
// is EXECUTED in test/test_data_sources_ui.mjs against the shipped module; here the save path
// is pinned to it, so the component row can never quietly grow a second copy.
const ch = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
const save = ch.slice(ch.indexOf('// Collect componentConfig from component toggles'), ch.indexOf('// Collect recommenderConfig'));
ok('[5] the save writes the query block through the Data sources state, with the stored block',
   /DataSources\.collectQuery\(dsState, stored\.query\)/.test(save) && /componentConfig\[compId\]\.query = query/.test(save));
ok('[5] …and the pinned catalog source the same way', /DataSources\.collectSourceId\(dsState, stored\.sourceId\)/.test(save));
ok('[5] the state is read only for THIS profile (never an earlier modal\'s)', /DataSources\.stateFor\(profile\)/.test(save));
ok('[5] no second copy of the query controls remains in the profile editor',
   !ch.includes('comp-dbnav-query-row') && !ch.includes('collectDbnavQuery') && !ch.includes('comp-dbnav-source-select'));
const ds = readFileSync('static/js/handlers/dataSources.js', 'utf8');
ok('[5] the Data sources module owns the controls', /data-ds-act="toggle-query"/.test(ds) && /export function collectQuery/.test(ds));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
