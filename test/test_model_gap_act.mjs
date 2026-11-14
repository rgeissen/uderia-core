// The model-gap note's act (PROP-008 §5a — agent/model_gap.py → static/js/ui.js
// _onGapProposeAct / _submitGapProposal): the shipped handlers EXECUTED under the DOM stub, on
// the note HTML the backend really renders.
//
//   §A  one click opens the proposal form IN PLACE — the concept's name, the model's own
//       entities to belong to — and asks for ONE thing: a suggestion of what it records, from
//       the answering agent's model, for the turn the note belongs to
//   §A2 the suggestion fills only what the person has not written, preselects only an untouched
//       'Belongs to', and says whose suggestion it is
//   §B  the act toggles; Cancel closes
//   §C  the form refuses an empty proposal in words, without a request
//   §D  Check the proposal posts it to the model's rules DIRECTLY (no model, no chat
//       turn) and the verdict row — with its Draft it — takes the form's place; the note's act,
//       now done, says so and is not offered again
//   §E  a refusal is shown in the form, which stays for a correction
//
// Run: node test/test_model_gap_act.mjs
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { installDom, parseHtml } from './dom_stub.mjs';

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 300) : ''}`); }
};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

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

const UI = readFileSync('static/js/ui.js', 'utf8');
const noteHtml = execFileSync('python', ['-c', `
import sys; sys.path[:0] = ["src", "."]
from trusted_data_agent.agent import model_gap as MG
ev = {"enrichments": [{"kg_id": "kg-7", "model_gap": {"term": "warrants", "singular": "warrant", "may_author": True,
      "entities": ["Justice.Person", "Justice.Court"]}}]}
print(MG.apply("", "<p>The model does not record warrants.</p>", ev)[1])
`], { encoding: 'utf8' }).trim();

const { doc, body } = installDom({});
let calls = [];
let reply = { status: 200, body: {} };
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
  const r = typeof reply === 'function' ? reply() : reply;
  return { ok: r.status < 400, status: r.status, json: async () => r.body };
};
localStorage.setItem('tda_auth_token', 'tok');
const SRC = ['_gapEl', '_gapField', '_onGapProposeAct', '_submitGapProposal'].map(n => extract(UI, n)).join('\n');
// eslint-disable-next-line no-new-func
let origin = { profileTag: 'WARRANTS_AGENT', profileId: 'p-w', sessionId: 's-9', turnId: '4', sourcePrompt: 'Which warrants are open?' };
const _deriveTableOrigin = () => origin;   // the pin act's own origin reader, stubbed
const { act, submit, suggestions } = new Function('document', 'localStorage', 'console', '_deriveTableOrigin',
  `const _gapSuggestions = new Map();\n${SRC}; return { act: _onGapProposeAct, submit: _submitGapProposal, suggestions: _gapSuggestions };`)(globalThis.document, globalThis.localStorage, console, _deriveTableOrigin);

function setup() {
  body.children = [];
  calls = [];
  suggestions.clear();   // each scenario is a fresh page
  parseHtml(`<div class="message-content">${noteHtml}</div>`, body).forEach(e => body.appendChild(e));
  return { btn: doc.querySelector('.ans-gap-propose'), note: doc.querySelector('.ans-note') };
}
const form = () => doc.querySelector('.ans-gap-form');

console.log('[§A] one click → the form, in place; one request: the suggestion');
{
  const { btn, note } = setup();
  check('the backend note renders the act', !!btn && btn.textContent === 'Propose a Warrant', noteHtml);
  act(btn);
  const f = form();
  check('the form opens directly under its note', !!f && note.nextElementSibling === f);
  check("the name is the concept's, ready to keep", f.querySelector('.ans-gap-name').value === 'Warrant');
  const opts = f.querySelector('.ans-gap-parent').querySelectorAll('option');
  check("'Belongs to' offers the model's own entities (leaf shown, full name sent), and none",
    opts.length === 3 && opts[0].getAttribute('value') === '' && opts[1].textContent === 'Person'
    && opts[1].getAttribute('value') === 'Justice.Person', opts.map(o => [o.textContent, o.getAttribute('value')]));
  check('the cursor waits where the person writes what it records', doc.activeElement === f.querySelector('.ans-gap-attrs'));
  const c = calls[0] || {};
  check('opening asks for ONE thing: the suggestion — never a chat turn',
    calls.length === 1 && c.url === '/api/v1/knowledge-graph/propose/suggest' && c.method === 'POST', calls);
  check("…for the agent that answered, the turn the note belongs to, and the question it answered",
    JSON.stringify(c.body) === JSON.stringify({ kg_id: 'kg-7', name: 'Warrant', question: 'Which warrants are open?',
      profile_id: 'p-w', session_id: 's-9', turn_id: '4' }), c.body);
  check('the person is told the platform adds the identifier and the link',
    f.querySelector('.ans-gap-hint').textContent === 'The platform adds the identifier and the link to what it belongs to.');
}

console.log('[§A2] the suggestion fills only what the person has not written');
{
  reply = { status: 200, body: { status: 'success', records: ['Issued at', 'Court', 'Status'], belongs_to: 'Justice.Person', profile_tag: 'WARRANTS_AGENT' } };
  let { btn } = setup();
  await act(btn);
  let f = form();
  check('an untouched form takes the suggested terms, one per line', f.querySelector('.ans-gap-attrs').value === 'Issued at\nCourt\nStatus');
  check("…and the suggested 'Belongs to'", f.querySelector('.ans-gap-parent').value === 'Justice.Person');
  check("…and says whose suggestion it is", f.querySelector('.ans-gap-hint').textContent.startsWith('Suggested by @WARRANTS_AGENT\u2019s model — edit freely.'));
  ({ btn } = setup());
  const pending = act(btn);
  f = form();
  f.querySelector('.ans-gap-attrs').value = 'what I typed';
  f.querySelector('.ans-gap-parent').value = 'Justice.Court';
  await pending;
  check("what the person already wrote is never overwritten", f.querySelector('.ans-gap-attrs').value === 'what I typed'
    && f.querySelector('.ans-gap-parent').value === 'Justice.Court');
  reply = { status: 200, body: { status: 'success', records: [], belongs_to: null } };
  ({ btn } = setup());
  await act(btn);
  check('no suggestion ⇒ an empty form that still works, and no claim of a suggestion',
    form().querySelector('.ans-gap-attrs').value === '' && !form().querySelector('.ans-gap-hint').textContent.startsWith('Suggested'));
  // a paid call booked to the turn: the same concept on the same turn is asked for ONCE per page
  reply = { status: 200, body: { status: 'success', records: ['Issued at'], belongs_to: null } };
  ({ btn } = setup());
  await act(btn);
  form().querySelector('.ans-gap-cancel').dispatchEvent({ type: 'click', preventDefault: () => {} });
  await act(btn);
  check('a form opened again reuses its suggestion — one call, one booking, not two',
    calls.filter(c => c.url.endsWith('/propose/suggest')).length === 1 && form().querySelector('.ans-gap-attrs').value === 'Issued at',
    calls.map(c => c.url));
  reply = { status: 200, body: { status: 'success', records: [], belongs_to: null } };
  ({ btn } = setup());
  await act(btn);
  form().querySelector('.ans-gap-cancel').dispatchEvent({ type: 'click', preventDefault: () => {} });
  await act(btn);
  check('…but an empty suggestion is not kept (a failed call may be asked again)',
    calls.filter(c => c.url.endsWith('/propose/suggest')).length === 2, calls.map(c => c.url));
  reply = { status: 200, body: {} };
}

console.log('[§B] the act toggles; Cancel closes');
{
  const { btn } = setup();
  await act(btn); act(btn);
  check('a second click closes the form', !form());
  act(btn);
  form().querySelector('.ans-gap-cancel').click();
  check('Cancel closes it and returns the focus to the act', !form() && doc.activeElement === btn);
}

console.log('[§C] an empty proposal is refused in words, without a request');
{
  const { btn } = setup();
  await act(btn);
  calls = [];
  form().querySelector('.ans-gap-submit').click();
  await settle();
  check('no request', calls.length === 0, calls);
  check('the form says what is missing', form().querySelector('.ans-gap-msg').textContent === 'Name at least one thing a Warrant records.');
}

console.log('[§D] Check the proposal → the verdict row takes the form\'s place');
{
  const { btn } = setup();
  await act(btn);
  calls = [];
  const f = form();
  f.querySelector('.ans-gap-attrs').value = 'warrant_id: integer\n  person_id \n\nissued_at\nstatus';
  f.querySelector('.ans-gap-parent').value = 'Justice.Person';
  f.querySelector('.ans-gap-desc').value = 'A court order to arrest a person.';
  reply = { status: 200, body: { status: 'success', html: "<div class='kg-proposal' data-kg-proposal='entity'><div class='kg-proposal-head'>Proposed entity <strong>Warrant</strong></div><div class='kg-proposal-foot'><button type='button' class='ans-act kg-proposal-draft' data-kg-id='kg-7' data-kg-args='{}'>Draft it on the authored layer</button></div></div>" } };
  f.querySelector('.ans-gap-submit').click();
  await settle();
  const c = calls[0] || {};
  check('one request, to the advisor — never a chat turn', calls.length === 1 && c.url === '/api/v1/knowledge-graph/propose' && c.method === 'POST', calls);
  check("the person's proposal, as written: name, one attribute per line, the parent, the definition",
    JSON.stringify(c.body) === JSON.stringify({ kg_id: 'kg-7', action: 'propose_entity', entity_name: 'Warrant',
      attributes: ['warrant_id: integer', 'person_id', 'issued_at', 'status'], relates_to: 'Justice.Person',
      description: 'A court order to arrest a person.' }), c.body);
  const draft = doc.querySelector('.kg-proposal-draft');
  check('the verdict row takes the form\'s place, with its Draft it', !form() && !!draft && !!draft.closest('.kg-proposal'));
  check('the note\'s act is done and not offered again', btn.disabled === true && btn.textContent === 'Proposed below');
}

console.log('[§E] a refusal stays in the form for a correction');
{
  const { btn } = setup();
  await act(btn);
  const f = form();
  f.querySelector('.ans-gap-attrs').value = 'warrant_id';
  f.querySelector('.ans-gap-parent').value = 'Justice.Nowhere';
  reply = { status: 422, body: { status: 'error', message: "no entity named 'Justice.Nowhere' in this graph" } };
  f.querySelector('.ans-gap-submit').click();
  await settle();
  check('the reason is shown in the form', form() && form().querySelector('.ans-gap-msg').textContent === "no entity named 'Justice.Nowhere' in this graph");
  check('the form can be sent again', form().querySelector('.ans-gap-submit').disabled === false
    && form().querySelector('.ans-gap-submit').textContent === 'Check the proposal');
  check('the note\'s act is still offered', btn.disabled === false);
}

console.log('[§F] bound once, by delegation');
check('one delegated click listener for .ans-gap-propose, guarded against double binding',
  /!window\.__gapProposeBound[\s\S]{0,200}closest\('\.ans-gap-propose'\)[\s\S]{0,120}_onGapProposeAct\(btn\)/.test(UI));
check('the form never writes into the composer', !/user-input/.test(extract(UI, '_onGapProposeAct') + extract(UI, '_submitGapProposal')));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
