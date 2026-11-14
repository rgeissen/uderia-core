/**
 * PROP-008 increment B4c — the act in the answer: the `action` renderer EXECUTED under the DOM
 * stub, against a scripted fetch. The row is the REAL handler's html (`_prepared`, generated
 * by a Python subprocess), so the client is tested against what the server emits.
 *
 *   §C  Confirm posts exactly the row's pending block once; the row flips in place to how the
 *       act ended — Done · N rows · receipt, with Undo + Export receipt; a second click while
 *       busy sends nothing; the act is announced (uderia:action-performed)
 *   §O  every ending in words: parked (+ the Inbox route), observed, refused (why, nothing
 *       written), already confirmed (shows the recorded ending), unreachable (Confirm back)
 *   §R  a reloaded row reconciles from the confirmations read — shows how it ended, never its
 *       Confirm; an act a later undo reversed reads Undone
 *   §U  Undo prepares the reversal as a row of its own (Confirm undo, linked by `undoes`);
 *       confirming it marks the original Undone
 *   §E  the exported receipt carries the signed envelope
 *   §P  the Actions panel: action types with trust and grant, pending acts, confirmed acts —
 *       its words come from the same outcomeWords as the flip
 *   §W  wiring: the registry imports the renderer, the aside + accent + session close exist,
 *       the Workspace entry, the Living Answer and composer words
 *
 * Run: node test/test_action_renderer.mjs
 */
import './_uderia_bus.mjs';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { installDom, parseHtml } from './dom_stub.mjs';

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 300) : ''}`); }
}

// the REAL row html, from the handler
const PY = `
import json, sys
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from components.builtin.action.handler import ActionComponentHandler
prep = {"ready": True, "action": "set_customer_tier", "version": 1, "kg_id": "kg1", "operation": "update",
        "invocation": {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}},
        "invocation_hash": "a" * 64, "statement_hash": "b" * 64, "connection_id": "dbconn_t",
        "profile_id": "profile-agent", "session_id": "s1", "turn_id": 3, "undo": False,
        "consequence": {"lines": ["CustomerID 7: LoyaltyTier bronze → gold"],
                        "summary": "update 1 row(s) of fitness_db.Customers · bound 1 · every check holds",
                        "checks": [{"id": "row_exists", "holds": True}]}}
print(ActionComponentHandler()._prepared(prep).html)
`;
const ROW_HTML = execFileSync('python3', ['-c', PY], { encoding: 'utf8' }).trim();
check('(setup) the handler emits a pending row with a Confirm', /data-act-state='pending'/.test(ROW_HTML) && /act-confirm/.test(ROW_HTML));

const { doc, body } = installDom({ __currentSessionId: 's1' });
const calls = []; let next = [];
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
  const r = next.length ? next.shift() : { status: 200, body: {} };
  if (r.throws) throw new Error('offline');
  return { status: r.status, json: async () => r.body };
};
const flush = () => new Promise(r => setImmediate(r));
const M = await import('../components/builtin/action/renderer.js');

// an answer's card lives in the chat log — the only place an action card acts
function mount(html = ROW_HTML, where = 'chat-log') {
  body.children = []; body._html = '';
  const wrap = where === 'dashboard' ? 'class="dash-tile-content-container"' : `id="${where}"`;
  const host = parseHtml(`<div ${wrap}><div class="ans-ev">${html}</div></div>`, body)[0];
  host.parentNode = body; body.children.push(host);
  return body.querySelector('.act-row');
}
function click(sel, root) {
  const el = (root || body).querySelector(sel);
  doc.dispatchEvent({ type: 'click', target: el });
  return el;
}

// ── §X a copy outside the answer never acts ──
console.log('[§X] a card outside the answer (a Live Status copy) never writes');
{
  const before = calls.length;
  const copy = mount(ROW_HTML, 'status-window');
  click('.act-confirm', copy);
  await flush(); await flush();
  check('Confirm on a copy outside the chat log posts nothing and does not flip it',
    calls.length === before && copy.getAttribute('data-act-state') === 'pending' && copy.querySelector('.act-confirm').disabled !== true);
  const src = readFileSync(new URL('../components/builtin/action/renderer.js', import.meta.url), 'utf8');
  check('…and the side panel counts only the answer\'s cards as awaiting', /function _pendingRows\(\) \{\s*return _answerRows\(\)/.test(src));
  const ui = readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');
  check('Live Status shows a tool result as TEXT (escaped), never as markup',
    !/whitespace-pre-wrap">\$\{details\.result_preview\}/.test(ui) && (ui.match(/\$\{escapeHtml\(details\.result_preview\)\}/g) || []).length === 2
    && !/text-rose-300">\$\{details\.error\}/.test(ui));
}

// ── §C ──
console.log('[§C] confirm and the in-place flip');
let row = mount();
const announced = [];
doc.addEventListener('uderia:action-performed', ev => announced.push(ev.detail));
next = [{ status: 200, body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1,
  invocation_hash: 'a'.repeat(64), signed_receipt: { signed: true, signature: 'sig', payload: '{}' } } } }];
click('.act-confirm');
check('a click disables Confirm at once (a second click sends nothing)', row.querySelector('.act-confirm').disabled === true);
click('.act-confirm');
await flush(); await flush();
const perf = calls.filter(c => /\/perform$/.test(c.url));
check('Confirm posts once', perf.length === 1, calls.map(c => c.url));
const b = perf[0] && perf[0].body || {};
check('…exactly the row’s pending block (graph, agent, connection, invocation, its hash, the turn)',
  b.kg_id === 'kg1' && b.profile_id === 'profile-agent' && b.connection_id === 'dbconn_t'
  && b.invocation && b.invocation.values.LoyaltyTier === 'gold' && b.invocation_hash === 'a'.repeat(64)
  && b.session_id === 's1' && b.turn_id === 3, b);
check('…to the action’s perform endpoint', perf[0] && perf[0].url === '/api/v1/actions/set_customer_tier/perform' && perf[0].method === 'POST');
check('a conversation row posts no surface label (the server reads it as the conversation)', !('face' in b) || b.face === undefined);

check('the row flips in place to Done · 1 row · receipt', row.getAttribute('data-act-state') === 'performed'
  && /^Done · 1 row · receipt aaaaaaaaaaaa$/.test(row.querySelector('.act-meta').textContent), row.querySelector('.act-meta').textContent);
check('…keeps the consequence (the record of what changed)', /bronze → gold/.test(row.querySelector('.act-consequence').textContent));
check('…Confirm is gone; Undo and Export receipt are offered with accessible names',
  !row.querySelector('.act-confirm') && /Undo set_customer_tier/.test(row.querySelector('.act-undo').getAttribute('aria-label'))
  && /signed receipt/.test(row.querySelector('.act-export').getAttribute('aria-label')));
check('the act is announced for the receipt surfaces', announced.length === 1 && announced[0].record.outcome === 'performed'
  && announced[0].session_id === 's1');

// ── §O ──
console.log('[§O] every ending in words');
const endings = [
  [{ status: 202, body: { status: 'parked', record: { outcome: 'parked', review_id: 'r1' } } }, 'parked', /^Sent to the owner · parked for review/, '.act-inbox'],
  [{ status: 200, body: { status: 'observed', record: { outcome: 'observed' } } }, 'observed', /^Nothing changed · the action observes only/, null],
  [{ status: 422, body: { status: 'refused', record: { outcome: 'refused', refusal: 'LoyaltyTier is outside its domain' } } }, 'refused', /^Refused — LoyaltyTier is outside its domain\. Nothing was written\.$/, null],
  [{ status: 409, body: { status: 'already_confirmed', record: { outcome: 'performed', rows_affected: 2, invocation_hash: 'c'.repeat(64) } } }, 'performed', /^Done · 2 rows · receipt cccccccccccc$/, '.act-undo'],
  [{ status: 409, body: { error: 'not_trusted', message: "set_customer_tier reads changed — validate and sign it first" } }, 'refused', /^Refused — set_customer_tier reads changed/, null],
];
for (const [resp, state, words, act] of endings) {
  row = mount(); next = [resp];
  click('.act-confirm'); await flush(); await flush();
  check(`${state}${resp.status === 409 ? ' (' + (resp.body.status || resp.body.error) + ')' : ''}: "${(row.querySelector('.act-meta').textContent || '').slice(0, 48)}…"`,
    row.getAttribute('data-act-state') === state && words.test(row.querySelector('.act-meta').textContent)
    && !row.querySelector('.act-confirm') && (act ? !!row.querySelector(act) : true), row.querySelector('.act-meta').textContent);
}
row = mount(); next = [{ throws: true }];
click('.act-confirm'); await flush(); await flush();
check('unreachable: nothing written, Confirm comes back with a note', row.getAttribute('data-act-state') === 'pending'
  && row.querySelector('.act-confirm').disabled === false && /could not be reached — nothing was written/.test(row.querySelector('.act-note').textContent));
check('a parked act is announced too (the composer counts it)', announced.some(d => d.record.outcome === 'parked'));

// ── §R ──
console.log('[§K] a parked row, approved elsewhere, reads Done — header and body alike');
{
  const PK = `
import sys
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from components.builtin.action.handler import ActionComponentHandler
from trusted_data_agent.agent.answer_surface import component_row
rec = {"outcome": "parked", "action": "close_lead", "invocation_hash": "c" * 64, "posture": "review",
       "kg_id": "kg1", "session_id": "s1", "turn_id": 3, "principal": "agent:profile-agent",
       "receipt": {"key": {"lead_id": "L-1"}, "values": {"status": "closed"}}}
pl = ActionComponentHandler()._performed(rec, connection_id="dbconn_t")
print(component_row("action", pl.title, pl.html))
`;
  const WRAPPED = execFileSync('python3', ['-c', PK], { encoding: 'utf8' }).trim();
  body.children = []; body._html = '';
  const host = parseHtml(`<div id="chat-log">${WRAPPED}</div>`, body)[0];
  host.parentNode = body; body.children.push(host);
  const prow = body.querySelector('.act-row');
  const head = body.querySelector('.ans-ev-t');
  check('(setup) the real parked row, in the real answer frame, heads "parked for review"',
    prow && prow.getAttribute('data-act-state') === 'parked' && head && /parked for review/.test(head.textContent), head && head.textContent);
  M.flipRow(prow, { outcome: 'performed', rows_affected: 1, invocation_hash: 'c'.repeat(64) });
  const txt = prow.textContent;
  check('the header says how the act stands NOW (Done), never the stale "parked — nothing written"',
    /^close_lead · Done · 1 row · receipt c{12}$/.test(head.textContent) && !/parked/.test(head.textContent), head.textContent);
  check('the receipt is named ONCE (the Done line), the parked receipt line is gone',
    (txt.match(/c{12}/g) || []).length === 1 && !prow.querySelector('.act-receipt'), txt);
}

console.log('[§R] a reloaded row shows how it ended');
row = mount(); calls.length = 0;
next = [{ status: 200, body: { confirmations: { '3': { ['a'.repeat(64)]: { outcome: 'performed', rows_affected: 1, invocation_hash: 'a'.repeat(64) },
  ['d'.repeat(64)]: { outcome: 'performed', rows_affected: 1, invocation_hash: 'd'.repeat(64), undoes: 'a'.repeat(64) } } } } }];
await M.reconcile([row]); await flush();
check('the confirmations read is asked for the row’s session', calls[0] && calls[0].url === '/api/v1/actions/confirmations?session_id=s1');
check('the reloaded row shows Done — never its Confirm again', row.getAttribute('data-act-state') === 'performed' && !row.querySelector('.act-confirm'));
check('…and reads Undone, since a later undo reversed it', /^Undone · receipt dddddddddddd$/.test((row.querySelector('.act-undone') || {}).textContent || '')
  && !row.querySelector('.act-undo'));
const fresh = mount(ROW_HTML.replace(/a{64}/g, 'e'.repeat(64)));
await M.reconcile([fresh]); await flush();
check('a row with no recorded ending keeps its Confirm', fresh.getAttribute('data-act-state') === 'pending' && !!fresh.querySelector('.act-confirm'));

// ── §U ──
console.log('[§U] undo');
row = mount(); next = [{ status: 200, body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1, invocation_hash: 'a'.repeat(64), signed_receipt: { signed: true } } } }];
click('.act-confirm'); await flush(); await flush();
calls.length = 0;
next = [{ status: 200, body: { status: 'prepared', version: 1, operation: 'update', invocation: { key: { CustomerID: 7 }, values: { LoyaltyTier: 'bronze' } },
  invocation_hash: 'f'.repeat(64), statement_hash: 'b'.repeat(64),
  consequence: { lines: ['CustomerID 7: LoyaltyTier gold → bronze'], summary: 'update 1 row(s) · bound 1' } } }];
click('.act-undo'); await flush(); await flush();
check('Undo asks for the reversal of THIS act', calls[0] && /\/undo$/.test(calls[0].url) && calls[0].body.invocation_hash === 'a'.repeat(64));
const undoRow = body.querySelectorAll('.act-row')[1];
const up = undoRow ? JSON.parse(undoRow.getAttribute('data-act-pending')) : {};
check('…and shows it as a row of its own, awaiting confirmation, linked to the act it undoes',
  undoRow && undoRow.getAttribute('data-act-state') === 'pending' && up.undoes === 'a'.repeat(64) && up.undo === true
  && up.session_id === 's1' && /gold → bronze/.test(undoRow.textContent) && /Confirm undo/.test(undoRow.querySelector('.act-confirm').textContent));
next = [{ status: 200, body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1, invocation_hash: 'f'.repeat(64) } } }];
calls.length = 0;
doc.dispatchEvent({ type: 'click', target: undoRow.querySelector('.act-confirm') }); await flush(); await flush();
check('confirming the undo carries `undoes`', calls[0] && calls[0].body.undoes === 'a'.repeat(64) && calls[0].body.invocation_hash === 'f'.repeat(64));
check('…and marks the original row Undone', /^Undone/.test((body.querySelector('.act-undone') || {}).textContent || ''));
check('an undo row offers no undo of its own', !undoRow.querySelector('.act-undo'));

// ── §E ──
console.log('[§E] the exported receipt');
row = mount(); next = [{ status: 200, body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1, invocation_hash: 'a'.repeat(64),
  confirmed_by: 'user:u1', principal: 'agent:profile-agent', signed_receipt: { signed: true, signature: 'sig', payload: 'p', key_fingerprint: 'k' } } } }];
click('.act-confirm'); await flush(); await flush();
const rd = M.receiptDocument(row);
check('the receipt carries the signed envelope, the act and who confirmed it',
  rd.kind === 'uderia.action.receipt' && rd.signed_receipt.signature === 'sig' && rd.invocation_hash === 'a'.repeat(64)
  && rd.confirmed_by === 'user:u1' && rd.principal === 'agent:profile-agent' && /Ed25519/.test(rd.verify));

// ── §P ──
console.log('[§P] the Actions panel');
const html = M.panelHtml({
  actions: [{ action: 'set_customer_tier', operation: 'update', target: 'fitness_db.Customers', writable: ['LoyaltyTier'], posture: 'enforce', max_rows: 1, trust: { state: 'trusted' }, granted: true },
            { action: 'close_ticket', operation: 'update', target: 'fitness_db.Tickets', writable: ['Status'], posture: 'review', trust: { state: 'changed' }, granted: false }],
  pending: [{ action: 'set_customer_tier', summary: 'update 1 row(s)' }],
  performed: [{ action: 'set_customer_tier', outcome: 'performed', rows_affected: 1, invocation_hash: 'a'.repeat(64), confirmed_by: 'user:u1' }],
  focus: 'close_ticket' });
check('three sections in order', html.indexOf('Action types') < html.indexOf('Awaiting your confirmation') && html.indexOf('Awaiting your confirmation') < html.indexOf('Confirmed in this conversation'));
check('an action type reads its trust and grant in words', /update fitness_db\.Customers · writes LoyaltyTier · enforce · bound 1 · signed · granted/.test(html)
  && /changed since signed · not granted to this agent/.test(html));
check('the focused action is marked', /ap-item is-focus" data-ap-action="close_ticket"/.test(html));
check('a pending act routes to its row in the answer', /data-ap-pending="0"[^>]*aria-label="Show set_customer_tier in the answer"/.test(html));
check('a confirmed act uses the flip’s own words', html.includes(M.outcomeWords({ outcome: 'performed', rows_affected: 1, invocation_hash: 'a'.repeat(64) }).words));
const empty = M.panelHtml({});
check('empty sections say so in words', /No action types/.test(empty) && /Nothing awaits your confirmation/.test(empty) && /No act has been confirmed/.test(empty));
check('defining an action is offered where the types are listed — the owed act (accented) when there is none',
  /class="ap-define is-owed">Define an action</.test(empty) && /class="ap-define">Define an action</.test(html));

// ── §W ──
console.log('[§W] wiring');
const reg = readFileSync('static/js/componentRenderers.js', 'utf8');
check('the registry loads the renderer where the component is installed (which installs the listener at startup)', /action: 'renderAction'/.test(reg) && /export const renderersReady = Promise\.allSettled/.test(reg));
const idx = readFileSync('templates/index.html', 'utf8');
check('the OBDA-ACT panel aside exists, carries the component name and a named close', /<aside id="action-split-panel" class="sp-panel"/.test(idx)
  && /id="action-split-title" class="sp-title">OBDA-ACT</.test(idx)
  && /id="action-split-close"[^>]*aria-label="Close the OBDA-ACT panel"/.test(idx));
// the component is named OBDA-ACT wherever it names itself — the admin list reads the manifest,
// the Workspace row opens "the OBDA-ACT panel", the Designer heads its catalogue with the plane
check('the component is named OBDA-ACT (manifest, Workspace, Designer)',
  JSON.parse(readFileSync('components/builtin/action/manifest.json', 'utf8')).display_name === 'OBDA-ACT'
  && /Open the OBDA-ACT panel on this action/.test(readFileSync('static/js/handlers/workspacePanelHandler.js', 'utf8'))
  && /<h2>OBDA-ACT on \$\{_esc\(kgName\)\}<\/h2>/.test(readFileSync('static/js/handlers/actionDesigner.js', 'utf8')));
check('the panel takes the manifest accent', /action:\s+'action-split-panel'/.test(readFileSync('static/js/handlers/componentHandler.js', 'utf8')));
check('a session switch closes it', /closeActionsPanel\(\)/.test(readFileSync('static/js/handlers/sessionManagement.js', 'utf8')));
const ws = readFileSync('static/js/handlers/workspacePanelHandler.js', 'utf8');
const wsAt = ws.indexOf('\n    action: {');
const wsEntry = wsAt < 0 ? '' : ws.slice(wsAt, ws.indexOf('\n    },\n', wsAt));
check('the Workspace lists the agent’s action types and opens the panel on one',
  /listUrl: \(profileId\) => `\/api\/v1\/actions\?profile_id=/.test(wsEntry) && /itemsKey: 'actions'/.test(wsEntry)
  && /openActionsPanel\(\{ profile_id: profileId, focus: it\.action \}\)/.test(wsEntry) && /noDelete: true/.test(wsEntry), wsEntry.slice(0, 80));
const la = readFileSync('static/js/handlers/livingAnswer.js', 'utf8');
check('the Living Answer says a prepared act awaits confirmation', /Prepared an action · awaiting your confirmation/.test(la) && /Prepared an undo · awaiting your confirmation/.test(la));
const cc = readFileSync('static/js/handlers/composerChrome.js', 'utf8');
check('the composer receipt counts performed acts', /uderia:action-performed/.test(cc) && /actions'\} performed/.test(cc));
check('no emoji in the renderer', !/[\u{1F300}-\u{1FAFF}✅❌⚠]/u.test(readFileSync('components/builtin/action/renderer.js', 'utf8')));

console.log('[§DF] the dashboard surface');
{
  // PROP-008 §6 — a row prepared on a dashboard tile carries face: dashboard; Confirm forwards it
  const dashRow = mount(ROW_HTML.replace(/a{64}/g, '9'.repeat(64)), 'dashboard');
  const pd = JSON.parse(dashRow.getAttribute('data-act-pending')); pd.face = 'dashboard';
  dashRow.setAttribute('data-act-pending', JSON.stringify(pd));
  calls.length = 0;
  next = [{ status: 200, body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1, invocation_hash: '9'.repeat(64) } } }];
  await M.confirmRow(dashRow); await flush();
  const pb = (calls.find(c => /\/perform$/.test(c.url)) || {}).body || {};
  check('Confirm forwards the surface the row was prepared on (face: dashboard)', pb.face === 'dashboard', pb);
  // a pinned act on a DASHBOARD TILE is an acting surface: its Confirm click is honoured
  calls.length = 0;
  const tileRow = mount(ROW_HTML.replace(/a{64}/g, '8'.repeat(64)), 'dashboard');
  next = [{ status: 200, body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1, invocation_hash: '8'.repeat(64) } } }];
  click('.act-confirm', tileRow); await flush(); await flush();
  check('a click on Confirm in a dashboard tile performs (a tile is a place an act may be confirmed)',
    calls.some(c => /\/perform$/.test(c.url)), calls.map(c => c.url));
}

console.log(`\n${fail ? 'FAIL' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
