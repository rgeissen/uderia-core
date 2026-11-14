// PROP-008 increment C2c — act again from a pinned action tile (§6).
//
// The SHIPPED functions are extracted from the dashboard renderer and executed under the DOM
// stub with a fake fetch:
//   §S  which pinned rows can be prepared again (never an undo, never a row without an action)
//   §B  the preview body: the pinned invocation, as the agent that prepared it (else the
//       tile's agent), labelled as clicked on the dashboard
//   §M  the button: preparing shows the server's row (the same row the answer draws) and
//       writes nothing; a refusal is said in words and keeps the record; nothing is ever
//       posted to /perform by the tile itself
//   §W  wiring: the action tile mounts it; theme tokens only
//
// Run: node test/test_dashboard_act_again.mjs
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

const ROOT = new URL('..', import.meta.url).pathname;
const SRC = readFileSync(`${ROOT}components/builtin/dashboard/renderer.js`, 'utf8');
let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail) : ''}`); }
}
function extract(name) {
  const i = SRC.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  let j = SRC.indexOf('{', i), depth = 0;
  for (; j < SRC.length; j++) {
    if (SRC[j] === '{') depth++;
    else if (SRC[j] === '}' && --depth === 0) break;
  }
  return SRC.slice(i, j + 1);
}

const { body } = installDom();
// the click's work is promise microtasks only (fetch → json); the stub replaces timers
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const calls = [];
let next = null;
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
  const r = next || { status: 404, body: {} };
  return { ok: r.status < 400, status: r.status, json: async () => r.body };
};
const F = new Function([
  'function _authHeaders() { return { Authorization: "Bearer t" }; }',
  extract('_actAgainSource'), extract('_actAgainBody'), extract('_mountActAgain'), extract('_actRowPending'),
  'return { _actAgainSource, _actAgainBody, _mountActAgain, _actRowPending };'].join('\n'))();

console.log('[§S] what can be prepared again');
const PEND = { kg_id: 'kg1', action: 'set_tier', invocation: { key: { CustomerID: 7 }, values: { LoyaltyTier: 'gold' } },
               profile_id: 'profile-agent', connection_id: 'dbconn_x', invocation_hash: 'h' };
check('a performed act with an invocation can', F._actAgainSource(JSON.stringify(PEND))?.action === 'set_tier');
check('an undo cannot (it restores one specific before-image)', F._actAgainSource(JSON.stringify({ ...PEND, undo: true })) === null);
check('…nor the act an undo targets', F._actAgainSource(JSON.stringify({ ...PEND, undoes: 'x' })) === null);
check('a row without an action cannot', F._actAgainSource(JSON.stringify({ ...PEND, action: '' })) === null);
check('a row without an invocation cannot', F._actAgainSource(JSON.stringify({ action: 'a' })) === null);
check('unparseable or absent data cannot', F._actAgainSource('{x') === null && F._actAgainSource(null) === null);

console.log('[§B] the preview body');
const b = F._actAgainBody(PEND, { profile_id: 'profile-tile' });
check('the pinned invocation, unchanged', JSON.stringify(b.invocation) === JSON.stringify(PEND.invocation));
check('as the agent that prepared it', b.profile_id === 'profile-agent');
check('through its connection, on its graph', b.connection_id === 'dbconn_x' && b.kg_id === 'kg1');
check('labelled as clicked on the dashboard', b.face === 'dashboard');
check('the tile\'s agent when the row names none', F._actAgainBody({ ...PEND, profile_id: undefined }, { profile_id: 'profile-tile' }).profile_id === 'profile-tile');
check('never a hash: a fresh preview computes a fresh one', !('invocation_hash' in b));

console.log('[§M] the button');
function mount(pending, state = 'performed') {
  const host = document.createElement('div');
  host.innerHTML = `<div class="act-row" data-act-state="${state}" data-act-pending='${JSON.stringify(pending)}'>done</div>`;
  body.appendChild(host);
  F._mountActAgain(host, { profile_id: 'profile-tile' });
  return host;
}
let host = mount(PEND);
let btn = host.querySelector('.dash-act-again-btn');
check('a settled act row offers Prepare again', !!btn && btn.textContent === 'Prepare again');
check('…with an accessible name saying nothing changes before the confirm', /before you confirm/.test(btn.getAttribute('aria-label')));
const undoHost = mount({ ...PEND, undo: true });
check('an undo row offers nothing', !undoHost.querySelector('.dash-act-again-btn'));
check('a settled act shows the button', host.querySelector('.dash-act-again').hidden === false && host.querySelector('.dash-act-again').style.display === 'flex');
const pendHost = mount(PEND, 'pending');
check('a row still awaiting its Confirm hides it (the owed act is that Confirm)', pendHost.querySelector('.dash-act-again').hidden === true);
check('…by its display, which an inline display:flex would otherwise override (found in the browser)', pendHost.querySelector('.dash-act-again').style.display === 'none');
check('_actRowPending: pending, busy and stateless rows are pending; settled ones are not',
  F._actRowPending({ getAttribute: (a) => a === 'data-act-state' ? 'pending' : null })
  && F._actRowPending({ getAttribute: (a) => a === 'data-act-state' ? 'performed' : (a === 'aria-busy' ? 'true' : null) })
  && F._actRowPending({ getAttribute: () => null })
  && !F._actRowPending({ getAttribute: (a) => a === 'data-act-state' ? 'performed' : null })
  && !F._actRowPending({ getAttribute: (a) => a === 'data-act-state' ? 'refused' : null }));

next = { status: 200, body: { status: 'prepared', row_html: `<div class="act-row" data-act-state="pending" data-act-pending='${JSON.stringify({ ...PEND, face: 'dashboard' })}'>fresh <button class="act-confirm">Confirm</button></div>` } };
btn.click(); await settle();
check('preparing asks the PREVIEW, never perform', calls.length === 1 && /\/api\/v1\/actions\/set_tier\/preview$/.test(calls[0].url) && calls[0].method === 'POST');
check('…with the body the pure builder makes', JSON.stringify(calls[0].body) === JSON.stringify(F._actAgainBody(PEND, { profile_id: 'profile-tile' })));
check('the tile now shows the server\'s row with its Confirm', host.querySelector('.act-confirm') && /fresh/.test(host.innerHTML));
check('…and mounts the button again for THAT act, hidden until it settles',
  host.querySelectorAll('.dash-act-again').length === 1 && host.querySelector('.dash-act-again').hidden === true);

host = mount(PEND); btn = host.querySelector('.dash-act-again-btn');
next = { status: 422, body: { status: 'refused', refusal: 'not granted' } };
btn.click(); await settle();
const note = host.querySelector('.dash-act-again-note');
check('a refusal is said in words and that nothing was written', /Cannot prepare it again — not granted\. Nothing was written\./.test(note.textContent), note.textContent);
check('…the record stays, the button can be tried again', /done/.test(host.innerHTML) && btn.disabled === false);
check('the tile never posts to /perform itself', calls.every(c => !/\/perform$/.test(c.url)));

console.log('[§W] wiring');
check('the action tile mounts it', /_mountActAgain\(actHost, tile\);/.test(SRC));
const fn = extract('_mountActAgain');
check('theme tokens only (no hardcoded text colours)', !/color:\s*#/.test(fn.replace(/var\(--trust-danger, #f87171\)/g, '')));
check('the status line is announced', /setAttribute\('role', 'status'\)/.test(fn));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
