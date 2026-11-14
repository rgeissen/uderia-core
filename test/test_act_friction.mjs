// ACT increment B4d — the FRICTION BUDGET as a gate (PROP-008 §5b): the shipped click handlers
// EXECUTED under the DOM stub, counting the person's clicks until each act's request is sent
// and its row reads how it ended.
//
//   §A  from a question to a performed act:            ONE click   (the action row's Confirm)
//   §D  from "not in the model" to a draft entity:      ONE click   (the proposal's Draft it)
//   §G  from draft to published, in one dialog:         THREE clicks (Preview · Land · Publish)
//
// A budget is an UPPER bound and a LOWER one: an act that lands on fewer clicks than its
// budget skipped the step where the person reads the consequence (a write never lands
// unseen — Land is enabled only by a preview of the target selected), and one that needs
// more has grown friction. Both fail by name.
//
// Run: node test/test_act_friction.mjs
import './_uderia_bus.mjs';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { installDom, parseHtml, El } from './dom_stub.mjs';

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 300) : ''}`); }
};
const flush = () => new Promise((r) => setImmediate(r));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };

/** `function <name>(…) { … }` from a source file — balanced braces. */
function extract(src, name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`function ${name} not found`);
  const j = src.indexOf('{', src.indexOf(')', i));
  let depth = 0;
  for (let k = j; k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}' && --depth === 0) return src.slice(i, k + 1);
  }
  throw new Error(`unbalanced ${name}`);
}

const { doc, body } = installDom({ __currentSessionId: 's1' });
const realSetTimeout = setImmediate;
let calls = [];
let routes = [];
globalThis.fetch = async (url, init = {}) => {
  const c = { url: String(url), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null };
  calls.push(c);
  const r = routes.find((x) => x.match(c)) || { status: 200, body: {} };
  const b = typeof r.body === 'function' ? r.body(c) : r.body;
  return { ok: r.status < 400, status: r.status, json: async () => b };
};

// ── §A the performed act ─────────────────────────────────────────────────────
console.log('[§A] question → performed act');
{
  const PY = `
import sys
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from components.builtin.action.handler import ActionComponentHandler
prep = {"ready": True, "action": "set_customer_tier", "version": 1, "kg_id": "kg1", "operation": "update",
        "invocation": {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}},
        "invocation_hash": "a" * 64, "statement_hash": "b" * 64, "connection_id": "dbconn_t",
        "profile_id": "profile-agent", "session_id": "s1", "turn_id": 3, "undo": False,
        "consequence": {"lines": ["CustomerID 7: LoyaltyTier bronze → gold"],
                        "summary": "update 1 row(s) · bound 1 · every check holds", "checks": []}}
print(ActionComponentHandler()._prepared(prep).html)
`;
  const html = execFileSync('python3', ['-c', PY], { encoding: 'utf8' }).trim();
  await import('../components/builtin/action/renderer.js');
  body.children = [];
  const host = parseHtml(`<div id="chat-log"><div class="ans-ev">${html}</div></div>`, body)[0];   // the answer
  host.parentNode = body; body.children.push(host);
  const row = body.querySelector('.act-row');
  check('(setup) the prepared row offers its Confirm and has written nothing',
    row && row.getAttribute('data-act-state') === 'pending' && !!row.querySelector('.act-confirm') && calls.length === 0);
  routes = [{ match: (c) => /\/perform$/.test(c.url), status: 200,
    body: { status: 'performed', record: { outcome: 'performed', rows_affected: 1, invocation_hash: 'a'.repeat(64) } } }];
  let clicks = 0;
  while (row.getAttribute('data-act-state') === 'pending' && clicks < 5) {
    const btn = row.querySelector('.act-confirm');
    if (!btn || btn.disabled) break;
    clicks++; doc.dispatchEvent({ type: 'click', target: btn }); await settle();
  }
  check('performed in exactly ONE click', clicks === 1 && row.getAttribute('data-act-state') === 'performed',
    { clicks, state: row.getAttribute('data-act-state') });
  check('…one request, to perform, carrying the hash the person saw',
    calls.length === 1 && /\/perform$/.test(calls[0].url) && calls[0].body.invocation_hash === 'a'.repeat(64), calls.map((c) => c.url));
}

// ── §D the draft entity ──────────────────────────────────────────────────────
console.log('[§D] "not in the model" → draft entity');
{
  const ui = readFileSync('static/js/ui.js', 'utf8');
  const start = ui.indexOf('function _wireProposalActs(');
  const end = ui.indexOf("if (typeof document !== 'undefined' && !window.__pinActBound) {");
  const block = ui.slice(start, end);
  calls = [];
  routes = [{ match: (c) => /knowledge-graph\/propose$/.test(c.url), status: 200,
    body: { drafted: true, note: 'Drafted.', html: `<div class="kg-proposal" data-kg-proposal="entity"><div class="kg-proposal-state">Drafted 'Lead' on the authored layer.</div></div>` } }];
  const listeners = {};
  const document = { createElement: (t) => new El(t), addEventListener: (k, f) => { listeners[k] = f; }, dispatchEvent: () => true };
  const window = { showAppBanner: () => {} };
  const P = new Function('document', 'window', 'fetch', 'localStorage', 'CustomEvent', 'console',
    block + '\nreturn { _wireProposalActs, _onProposalAct };')(
    document, window, globalThis.fetch, { getItem: () => 'tok' }, function (t) { this.type = t; }, { error() {} });
  const row = new El('div'); row.className = 'kg-proposal';
  const btn = new El('button', { 'data-kg-id': 'kg1', 'data-kg-args': JSON.stringify({ action: 'propose_entity', entity_name: 'Lead', relates_to: 'fitness_db.Customers', attributes: ['lead_id'], key: ['lead_id'] }) });
  btn.className = 'ans-act kg-proposal-draft'; btn.textContent = 'Draft it on the authored layer';
  row.appendChild(btn);
  const hostD = new El('div'); hostD.appendChild(row);
  P._wireProposalActs(hostD);
  let clicks = 0;
  while (hostD.querySelector('.kg-proposal-draft') && clicks < 5) {
    const b = hostD.querySelector('.kg-proposal-draft');
    if (b.disabled) break;
    clicks++; await P._onProposalAct(b); await settle();
  }
  check('drafted in exactly ONE click (the row is replaced by its drafted state)',
    clicks === 1 && !hostD.querySelector('.kg-proposal-draft') && /Drafted/.test(hostD.children[0].querySelector('.kg-proposal-state').textContent),
    { clicks });
  // a row reads its state once when it is drawn (one authored read per graph — the row must
  // never offer a draft that exists); the ACT is one request
  const acts = calls.filter((c) => /knowledge-graph\/propose$/.test(c.url));
  const reads = calls.filter((c) => /knowledge-graph\/authored\?kg_id=/.test(c.url));
  check('…one request for the act, the proposal itself with draft=true', acts.length === 1 && acts[0].body.draft === true, calls.map((c) => c.url));
  check('…and at most one read of the authored layer when the row is drawn (its state now)', reads.length <= 1 && calls.length === acts.length + reads.length, calls.map((c) => c.url));
}

// ── §G draft → published ─────────────────────────────────────────────────────
console.log('[§G] draft → published, in the Govern dialog');
const kg = readFileSync('static/js/handlers/knowledgeGraphPanelHandler.js', 'utf8');
const wireDeploy = extract(kg, '_wireGovDeploy');
const wirePublish = extract(kg, '_wireGovPublish');
const W = new Function('document', 'fetch', 'localStorage', 'CustomEvent', 'showAppBanner',
  `${kg.match(/const DEPLOY_DRIVERS[^\n]*\nconst DEPLOY_DRIVER_LABEL[^\n]*/)[0]}\n${wireDeploy}\n${wirePublish}\nreturn { _wireGovDeploy, _wireGovPublish };`);

function dialog(connections) {
  calls = [];
  const published = { done: false };
  routes = [
    { match: (c) => /database-connections$/.test(c.url), status: 200, body: { connections } },
    { match: (c) => /authored\/deploy$/.test(c.url) && c.body.dry_run === true, status: 200,
      body: { deploy: { summary: 'one table in UDERIA_EXT_fitness_db, two columns, key lead_id', plan: { pending: [{ kind: 'create_table', identifier: 'UDERIA_EXT_fitness_db.Lead' }] } } } },
    { match: (c) => /authored\/deploy$/.test(c.url) && c.body.dry_run === false, status: 200,
      body: { deploy: { result: { landed: ['x'], errors: [] }, landing: { database: 'UDERIA_EXT_fitness_db' }, receipt: {}, readback: { status: 'conformant', summary: 'conformant' } } } },
    { match: (c) => /authored\/publish$/.test(c.url), status: 200,
      body: () => { published.done = true; return { allowed: true, published: ['Lead'] }; } },
  ];
  const card = parseHtml(`<div>
    <div id="kg-gov-deploy" class="kg-gov-deploy"><div class="kg-gov-deploy-row">
      <select id="kg-gov-deploy-target"><option value="">Choose a database connection…</option></select>
      <button type="button" class="kg-gov-deploy-preview" disabled>Preview</button>
      <button type="button" class="kg-gov-deploy-land" disabled>Land it</button></div>
      <div id="kg-gov-deploy-words"></div></div>
    <div id="kg-gov-exts"><button type="button" class="kg-gov-ext-publish" data-extension-id="ext-u1">Publish 1 draft</button>
      <div id="kg-gov-ext-words"></div></div></div>`, new El('div'))[0];
  // a <select> reflects the chosen option's value; the stub keeps `value` as a plain field
  const sel = card.querySelector('#kg-gov-deploy-target');
  sel.value = '';
  const origAppend = sel.appendChild.bind(sel);
  sel.appendChild = (o) => { o.value = o.value || ''; return origAppend(o); };
  const F = W(doc, globalThis.fetch, { getItem: () => 'tok' }, globalThis.CustomEvent, () => {});
  F._wireGovDeploy(card, 'kg1', async () => {});
  F._wireGovPublish(card, 'kg1', async () => {});
  return { card, sel, published,
    preview: card.querySelector('.kg-gov-deploy-preview'),
    land: card.querySelector('.kg-gov-deploy-land'),
    publish: card.querySelector('.kg-gov-ext-publish'),
    words: card.querySelector('#kg-gov-deploy-words') };
}
const fire = async (el) => { for (const fn of (el._listeners && el._listeners.click) || []) await fn({ type: 'click', target: el }); await settle(); };

{
  const D = dialog([{ id: 'dbconn_1', name: 'Warehouse', driver: 'teradata', username: 'u', host: 'h' }]);
  await settle();
  check('a lone Teradata connection is pre-selected (no choice owed)', D.sel.value === 'dbconn_1', D.sel.value);
  check('Land starts disabled — nothing lands before a preview', D.land.disabled === true);
  let clicks = 0, previewWords = '';
  for (const el of [D.preview, D.land, D.publish]) {
    if (el.disabled) break;
    clicks++; await fire(el);
    if (el === D.preview) previewWords = D.words.innerHTML || '';
  }
  const deploys = calls.filter((c) => /authored\/deploy$/.test(c.url));
  check('draft → published in exactly THREE clicks: Preview · Land · Publish',
    clicks === 3 && D.published.done && calls.some((c) => c.body && c.body.dry_run === false), { clicks, urls: calls.map((c) => `${c.method} ${c.url}`) });
  check('…the preview ran first (a dry run, words on screen), then the landing, then the publish',
    deploys.length === 2 && deploys[0].body.dry_run === true && deploys[1].body.dry_run === false
    && /one table in UDERIA_EXT_fitness_db/.test(previewWords), deploys.map((c) => c.body.dry_run));
}
{
  const D = dialog([{ id: 'dbconn_1', name: 'A', driver: 'teradata' }, { id: 'dbconn_2', name: 'B', driver: 'teradata' }]);
  await settle();
  check('with several connections none is guessed — the person chooses', D.sel.value === '');
  D.sel.value = 'dbconn_1'; await fire(D.preview);
  check('a preview enables Land for THAT target', D.land.disabled === false);
  D.sel.value = 'dbconn_2';
  for (const fn of (D.sel._listeners && D.sel._listeners.change) || []) fn({ type: 'change', target: D.sel });
  check('changing the target disables Land again — nothing lands that was not previewed', D.land.disabled === true);
  // each guard on its own: the click-time check holds even if the button were left enabled
  D.land.disabled = false;
  const n = calls.length; await fire(D.land);
  check('…and a forced click on a target nobody previewed lands nothing (the click-time guard)',
    calls.filter((c) => c.body && c.body.dry_run === false).length === 0 && calls.length === n);
}
{
  const D = dialog([{ id: 'dbconn_1', name: 'A', driver: 'teradata' }]);
  await settle();
  D.land.disabled = false;   // a button somehow enabled before any preview
  const n = calls.length; await fire(D.land); await fire(D.land);
  check('Land before any preview lands nothing, however often it is clicked',
    calls.filter((c) => c.body && c.body.dry_run === false).length === 0 && calls.length === n);
}

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
