// PROP-009 increment 3 — the answer-level Evidence view, EXECUTED under the shared DOM stub.
//   (1) the vocabulary: state words, never wire tokens; a missing side reads "—";
//   (2) the Evidence row: the summary words, the parts named on the row, attention as a token
//       chip; a turn before evidence recording says so; a deleted session says so;
//   (3) the tree: fixed-order pair "when answered · now", the status word, detail one click
//       down (signer, key, hashes), the owed act only where a part is changed or unsigned;
//   (4) the wiring: the capsule renders the host ONLY when EvidenceView is present, mounts it
//       after load and fresh on re-open, the receipt carries the evidence, the script tag sits
//       inside the Ontology-Suite guard, pins bumped; no native title, no emoji.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

const { doc, body } = installDom({});
let served = null;
const fetched = [];
globalThis.fetch = async (url) => { fetched.push(url); return { ok: !!served, json: async () => served }; };
await import(pathToFileURL('static/js/handlers/evidenceView.js').href);
const EV = window.EvidenceView;
check('the module publishes its surface', EV && ['mount', 'fetchEvidence', 'rowHtml', 'itemHtml', 'stateWord'].every(k => typeof EV[k] === 'function'));

// ── (1) vocabulary ──────────────────────────────────────────────────────────────────────────
check('state words: trusted reads in words', EV.stateWord('trusted') === 'signed and intact');
check('state words: changed reads in words', EV.stateWord('changed') === 'changed after signing');
check('state words: deployment_unattested is never a wire token', EV.stateWord('deployment_unattested') === 'not attested here');
check('state words: a missing side reads —', EV.stateWord(null) === '—' && EV.stateWord('') === '—');
check('state words: an unknown token is never shown with underscores', !/_/.test(EV.stateWord('some_new_state')));

const signer = { signer_uuid: 'u-1', signer_name: 'admin', signed_at: 1760000000, key_fingerprint: 'abcdef0123456789abcdef', key_status: 'retired', history_id: 7 };
const view = {
  recorded: true, session: { available: true }, resolved: 'turn_start',
  chain: { valid: true, verified_steps: 5, key_fingerprint: 'abcdef0123456789', key_status: 'active' },
  summary: { parts: 4, attention: 'warn', words: '4 parts · 1 not signed-and-intact when answered · 1 changed since',
             changed_since: ['sql'], not_found_now: [], not_signed_when_answered: ['notes'] },
  groups: [
    { key: 'agent', label: 'Agent', items: [{ role: 'agent', kind: 'agent', kind_label: 'agent', artifact_id: 'p1', label: 'AG',
        then: { state: 'trusted', live_hash: 'ha1', signer }, now: { state: 'trusted', live_hash: 'ha1' }, status: 'unchanged' }] },
    { key: 'deployment', label: 'Deployment', items: [{ role: 'deployment', kind: 'agent_deployment', kind_label: 'deployment', artifact_id: 'p1', label: 'deployment',
        then: { state: 'deployment_unattested' }, now: { state: null }, status: 'unknown', summary: { provider: 'Google', model: 'gem', tools: 31 } }] },
    { key: 'ingredients', label: 'What the agent is made of', items: [
        { role: 'ingredient', kind: 'skill', kind_label: 'skill', artifact_id: 'sk1', label: 'sql',
          then: { state: 'trusted', live_hash: 'hs1', signer }, now: { state: 'trusted', live_hash: 'hs9' }, status: 'changed_since' },
        { role: 'ingredient', kind: 'skill', kind_label: 'skill', artifact_id: 'sk2', label: 'notes',
          then: { state: 'unsigned', live_hash: 'hn1' }, now: { state: 'unsigned', live_hash: 'hn1' }, status: 'unchanged' },
        { role: 'ingredient', kind: 'ontology', kind_label: 'ontology', artifact_id: 'kg1', label: 'Fitness',
          then: { state: 'trusted', live_hash: 'ho1', signer }, now: { state: 'trusted', live_hash: 'ho1' }, status: 'unchanged' } ] },
  ],
};

// ── (2) the Evidence row ───────────────────────────────────────────────────────────────────
{
  const h = EV.rowHtml(view);
  check('row: the summary words lead', h.includes('4 parts · 1 not signed-and-intact when answered · 1 changed since'), h.slice(0, 300));
  check('row: attention is a token chip (warn)', /trust-chip tc-warn/.test(h));
  check('row: the changed part is named on the row', /changed since: sql/.test(h));
  check('row: the unsigned part is named on the row', /not signed when answered: notes/.test(h));
  check('row: the chain verification is stated', /record verified — 5 step\(s\) re-derived/.test(h));
  check('row: a native details/summary (keyboard reachable)', /<details class="ev-root">/.test(h) && /<summary>/.test(h));
  check('row: no wire token reaches the reader', !/deployment_unattested|changed_since|not_found\b/.test(h.replace(/data-[a-z-]+="[^"]*"/g, '').replace(/class="[^"]*"/g, '')), h);
  const nr = EV.rowHtml({ recorded: false, summary: { words: 'parts not recorded — this answer predates evidence recording', attention: 'na' } });
  check('row: a turn before evidence recording says so, never an empty tree', /predates evidence recording/.test(nr) && !/ev-group/.test(nr) && /tc-na/.test(nr));
  const gone = EV.rowHtml({ ...view, session: { available: false, note: 'session deleted — content not retained' }, chain: { valid: null, note: 'session deleted — content not retained' } });
  check('row: a deleted session says so', /session deleted — content not retained/.test(gone));
  const bad = EV.rowHtml({ ...view, chain: { valid: false, errors: ['Step 3 (trust.bom): the turn\'s bill of materials does not match what was sealed'] } });
  check('row: a record that does not verify says so, in danger', /tc-danger/.test(bad) && /does NOT verify/.test(bad) && /trust\.bom/.test(bad));
  check('row: unavailable evidence is said', /Evidence unavailable/.test(EV.rowHtml(null)));
  const allGood = EV.rowHtml({ ...view, summary: { parts: 3, attention: 'pass', words: '3 parts · all signed when answered', changed_since: [], not_found_now: [], not_signed_when_answered: [] } });
  check('row: all signed reads pass and names nothing', /tc-pass/.test(allGood) && !/ev-named/.test(allGood));
}

// ── (3) the tree ───────────────────────────────────────────────────────────────────────────
{
  const h = EV.rowHtml(view);
  const order = ['Agent', 'Deployment', 'What the agent is made of'].map(l => h.indexOf(`>${l}<`));
  check('tree: groups in order', order.every(i => i > 0) && order[0] < order[1] && order[1] < order[2], order);
  const changed = EV.itemHtml(view.groups[2].items[0]);
  const ia = changed.indexOf('When the answer was given'), ib = changed.indexOf('data-tip="Now"');
  check('tree: the pair is fixed-order — when answered, then now', ia > 0 && ib > ia, changed);
  check('tree: a changed part says "changed since", in warn', /tc-warn">changed since/.test(changed));
  check('tree: detail names the signer and time one click down', /<details class="ev-item">/.test(changed) && /Signed by/.test(changed) && /admin/.test(changed));
  check('tree: detail names a retired signing key', /retired key/.test(changed));
  check('tree: detail shows both content hashes', /when answered <code>hs1/.test(changed) && /now <code>hs9/.test(changed));
  const dep = EV.itemHtml(view.groups[1].items[0]);
  check('tree: a missing "now" side reads —', />—</.test(dep), dep);
  check('tree: the deployment reads as model, tools and connectors, with its surface', /Model, tools and connectors/.test(dep) && /Google · gem · 31 tools/.test(dep));
  const uns = EV.itemHtml(view.groups[2].items[1]);
  check('tree: an unsigned part says nobody signed it', /nobody — this part was not signed/.test(uns));
  const ok = EV.itemHtml(view.groups[2].items[2]);
  check('tree: the norm (unchanged) is not marked — only the exception is', !/>unchanged</.test(ok), ok);
  check('tree: an unchanged signed part offers no act', !/ev-open/.test(ok));
  // the owed act appears only with the trust surface present
  window.TrustBadge = { openArtifactTrust: () => {} };
  check('tree: a changed part offers its trust record', /ev-open/.test(EV.itemHtml(view.groups[2].items[0])));
  check('tree: an unsigned part offers its trust record', /ev-open/.test(EV.itemHtml(view.groups[2].items[1])));
  check('tree: a signed, unchanged part offers nothing', !/ev-open/.test(EV.itemHtml(view.groups[2].items[2])));
  check('tree: the agent row never offers the artefact act', !/ev-open/.test(EV.itemHtml(view.groups[0].items[0])));
  delete window.TrustBadge;
}

// ── mount (executed) ───────────────────────────────────────────────────────────────────────
{
  served = view;
  const host = new El('div'); body.appendChild(host);
  await EV.mount(host, 'sess-1', 3);
  check('mount: reads the turn endpoint', fetched.some(u => u === '/api/v1/evidence/turn/sess-1/3'), fetched);
  check('mount: renders the row into the host', /4 parts/.test(host.innerHTML));
  const n = fetched.length;
  await EV.mount(host, 'sess-1', 3);
  check('mount: a second open uses the cache', fetched.length === n);
  await EV.mount(host, 'sess-1', 3, { fresh: true });
  check('mount: fresh re-reads ("now" moves)', fetched.length === n + 1);
}

// ── (4) wiring ─────────────────────────────────────────────────────────────────────────────
{
  const cap = read('static/js/handlers/answerCapsule.js');
  check('capsule: the host renders only when EvidenceView is present', /window\.EvidenceView \? factRow\('Evidence', '<div data-ac-evidence><\/div>'\) : ''/.test(cap));
  check('capsule: mounted after load', /evHost && window\.EvidenceView\) window\.EvidenceView\.mount\(evHost, ids\.sid, ids\.turn\);/.test(cap));
  check('capsule: re-read fresh on re-open', /window\.EvidenceView\.mount\(evHost, ids\.sid, ids\.turn, \{ fresh: true \}\)/.test(cap));
  // PROP-009 increment 5: with the suite present the answer's export IS the evidence bundle.
  check('capsule: the export is the evidence bundle when the suite is present',
    /window\.EvidenceView && window\.EvidenceView\.exportBundleText/.test(cap) && /include_receipt: true/.test(cap));
  const html = read('templates/index.html');
  check('template: the script is inside the Ontology-Suite guard', /\{% if trust_membrane_available %\}<script src="\{\{ url_for\('static', filename='js\/handlers\/evidenceView\.js'\) \}\}\?v=[\d.]+"><\/script>\{% endif %\}/.test(html));
  { const m = /answerCapsule\.js'\) \}\}\?v=(\d+)\.(\d+)/.exec(html);
    check('template: the capsule pin was bumped (past the pre-evidence 2.10)', !!m && (+m[1] > 2 || +m[2] > 10)); }
  const src = read('static/js/handlers/evidenceView.js');
  check('no native title tooltips', !/\stitle=/.test(src));
  check('no emoji', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(src));
  check('theme tokens only (no hex colours)', !/#[0-9a-fA-F]{3,6}\b/.test(src.replace(/&#\d+;/g, '')));
}

// ── (5) the estate — every signature (increment 4) ─────────────────────────────────────────
{
  const rows = [
    { kind: 'ontology', kind_label: 'ontology', artifact_id: 'kg1', label: 'Fitness', state_now: 'changed', signed: true, signed_at: 1760000000, signer_name: 'admin', key_status: 'active', answers: 5, since_current: 1, flags: ['changed'], worst: 'changed' },
    { kind: 'skill', kind_label: 'skill', artifact_id: 'sk1', label: 'sql', state_now: 'trusted', signed: true, signed_at: 1760000000, signer_name: 'admin', key_status: 'retired', answers: 3, since_current: 3, flags: ['retired_key'], worst: 'retired_key' },
    { kind: 'flow', kind_label: 'flow', artifact_id: 'f1', label: 'nightly', state_now: 'unsigned', signed: false, answers: 0, since_current: null, flags: ['not_signed'], worst: 'unsigned' },
    { kind: 'skill', kind_label: 'skill', artifact_id: 'sk2', label: 'notes', state_now: 'trusted', signed: true, signed_at: 1760000000, signer_name: 'admin', key_status: 'active', answers: 2, since_current: 2, flags: [], worst: 'trusted' },
  ];
  check('estate: filter all keeps every row', EV.estateFilter(rows, 'all', '', '').length === 4);
  check('estate: filter changed keeps only changed', EV.estateFilter(rows, 'changed', '', '').map(r => r.artifact_id).join() === 'kg1');
  check('estate: filter retired key', EV.estateFilter(rows, 'retired_key', '', '').map(r => r.artifact_id).join() === 'sk1');
  check('estate: filter recorded-not-signed', EV.estateFilter(rows, 'not_signed', '', '').map(r => r.artifact_id).join() === 'f1');
  check('estate: kind + search compose', EV.estateFilter(rows, 'all', 'skill', 'NOT').map(r => r.artifact_id).join() === 'sk2');
  check('estate: usage words say both counts', EV.usageWords(rows[0]) === 'used by 5 answers · 1 with this version');
  check('estate: usage words when every answer used this version', EV.usageWords(rows[1]) === 'used by 3 answers · all with this version');
  check('estate: never used says so', EV.usageWords(rows[2]) === 'not used by a recorded answer yet');
  const ch = EV.estateRowHtml(rows[0]);
  check('estate: a changed row says "changed since signed"', /changed since signed/.test(ch) && /tc-warn/.test(ch));
  check('estate: a changed row offers its trust record', /ev-open/.test(ch));
  const rk = EV.estateRowHtml(rows[1]);
  check('estate: a retired key is named on the row', /retired key/.test(rk));
  check('estate: an intact row (even with a retired key) offers no act', !/ev-open/.test(rk));
  const ns = EV.estateRowHtml(rows[2]);
  check('estate: a row without a signature says so and offers its record', /recorded without a signature/.test(ns) && /ev-open/.test(ns));
  check('estate: rows are keyboard-reachable disclosures', /<details class="ev-erow"/.test(ch) && /<summary>/.test(ch));
  // a summary is one toggle — an act nested in it is mis-announced / unreachable (Chrome:
  // "interactive element inside of a <summary> element"); the act is the details' sibling
  const actInSummary = (h) => [...h.matchAll(/<summary>([\s\S]*?)<\/summary>/g)].some(m => /<button/.test(m[1]));
  window.TrustBadge = { openArtifactTrust: () => {} };   // the tree offers its act only with the trust surface
  const treeActs = [0, 1].map(i => EV.itemHtml(view.groups[2].items[i]));
  delete window.TrustBadge;
  check('no act sits inside a <summary> (estate rows and tree parts)',
    ![ch, ns, ...treeActs].some(actInSummary) && [ch, ns, ...treeActs].every(h => /ev-open/.test(h)));
  check('…the act follows its own <details>, in the row wrapper',
    [ch, ns, ...treeActs].every(h => /<div class="ev-disc">[\s\S]*<\/details><button type="button" class="ev-open"/.test(h)));
  // mount, pre-filtered (the SIGNED station routes here with filter=changed)
  served = { summary: { words: '4 signatures · 1 changed since signed · 1 made with a retired key', attention: 'warn' }, rows };
  EV._estate.data = null;
  const root = new El('div'); body.appendChild(root);
  await EV.mountEstate(root, { filter: 'changed' });
  check('estate: mount reads /api/v1/evidence', fetched.includes('/api/v1/evidence'));
  check('estate: the summary describes the whole scope', /4 signatures · 1 changed since signed · 1 made with a retired key/.test(root.innerHTML));
  check('estate: a station route arrives pre-filtered', /ev-pill ev-pill-on" data-filter="changed"/.test(root.innerHTML) && /1 of 4 shown/.test(root.innerHTML), root.innerHTML.slice(0, 200));
  check('estate: the filtered list shows only the changed row', /Fitness/.test(root.innerHTML) && !/>notes</.test(root.innerHTML));
  const estateReads = () => fetched.filter(u => u === '/api/v1/evidence').length;
  const n = estateReads();
  await EV.mountEstate(root, {});
  check('estate: a quick re-show uses the loaded list', estateReads() === n);
  EV._estate.at = Date.now() - 120000;
  await EV.mountEstate(root, {});
  check('estate: an older list is re-read ("now" moves)', estateReads() === n + 1);
  const empty = EV.estateFilter(rows, 'changed', 'flow', '');
  check('estate: an empty filter result is said', empty.length === 0);
}

// ── (6) the trust popover's usage line ──────────────────────────────────────────────────────
{
  const tb = read('static/js/handlers/trustBadge.js');
  const fnSrc = tb.slice(tb.indexOf('function _usageWord'), tb.indexOf('async function _fillUsage'));
  const _esc = (x) => String(x == null ? '' : x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const usageHtml = new Function('_esc', fnSrc + '; return _usageHtml;')(_esc);
  check('popover: never used is said', /Not used by any recorded answer yet/.test(usageHtml({ answers: 0, turns: [] })));
  check('popover: both counts in words', /Used by 214 answers · 31 answers with this version, 183 with an earlier one/.test(usageHtml({ answers: 214, since_current: 31, turns: [] })));
  check('popover: every answer with this version', /Used by 3 answers · every one with this version/.test(usageHtml({ answers: 3, since_current: 3, turns: [] })));
  check('popover: an unreadable count shows nothing (no guessed number)', usageHtml(null) === '' && usageHtml({}) === '');
  const list = usageHtml({ answers: 1, since_current: 0, turns: [{ profile_tag: 'AG', turn: 2, answered_at: '2026-10-08T10:00:00Z', same_as_current: false }] });
  check('popover: the answers are listed one click down', /<details/.test(list) && /@AG/.test(list) && /an earlier version/.test(list));
  check('popover: the dialog fills the line', /_fillUsage\(overlay\.querySelector\('\.tb-usage'\), kind, id\);/.test(tb));
}

// ── (7) the ledger chip's limit follows custody and anchoring (increment 7) ──────────────────
{
  const onHost = EV.ledgerTip({ custody: { key_on_host: true }, anchoring: { enabled: false } });
  check('ledger tip: a host-held key says its administrator could rebuild the chain',
    /held on this host/.test(onHost) && /could rebuild the whole chain/.test(onHost) && /not published outside this host/.test(onHost));
  const offHost = EV.ledgerTip({ custody: { key_on_host: false }, anchoring: { enabled: true, sink: 'rfc3161' } });
  check('ledger tip: an external key never claims the host could re-sign',
    /held outside this host/.test(offHost) && !/could rebuild/.test(offHost));
  check('ledger tip: published checkpoints name their sink', /published outside this host \(rfc3161\)/.test(offHost));
  check('ledger tip: an older server (no custody field) reads as host-held, never stronger',
    /held on this host/.test(EV.ledgerTip({})));
  const warn = EV.ledgerChip({ words: { words: 'ledger intact · 3 entries · the last anchor failed: HTTP 503', attention: 'warn' } });
  check('ledger chip: a failed anchor renders as warn', /tc-warn/.test(warn) && /the last anchor failed/.test(warn));
}

console.log(fail ? `\nFAILED — ${fail} of ${pass + fail} assertions` : `\nALL PASS — ${pass} assertions`);
process.exit(fail ? 1 : 0);
