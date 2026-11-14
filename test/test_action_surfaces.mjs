// ACT increment B4d-s — the act on the shared trust surfaces, EXECUTED (no server, no browser).
//
// The shipped code runs here, never a copy: the Designer module is imported as shipped; the
// Live Status card, the step-title helper and the Inbox's parked-write row are extracted from
// their files by name (balanced braces) and run with the small helpers they call stubbed.
//
//   §D  the Designer's journey and the ONE act owed per row (Rule 8.5)
//   §L  the Live Status card speaks each outcome in words, escapes what it shows, names a refusal
//   §T  the step title per outcome (live and reload share it)
//   §I  the Inbox row for a parked write: the diff, "Approve — perform it", never a release
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
let pass = 0, fail = 0;
const check = (name, cond, detail) => {
    if (cond) { pass++; console.log('  ok  ' + name); }
    else { fail++; console.log('  FAIL: ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 300) : '')); }
};
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

/** The source of `function <name>(…) { … }` — balanced braces from the opening brace. */
function extract(src, name) {
    const i = src.indexOf(`function ${name}(`);
    if (i < 0) throw new Error(`function ${name} not found`);
    let j = src.indexOf('{', src.indexOf(')', i));
    let depth = 0;
    for (let k = j; k < src.length; k++) {
        const c = src[k];
        if (c === '{') depth++;
        else if (c === '}') { depth--; if (depth === 0) return src.slice(i, k + 1); }
    }
    throw new Error(`unbalanced ${name}`);
}
const escapeHtml = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// ── §D the Designer ──────────────────────────────────────────────────────────
console.log('§D the Designer');
globalThis.localStorage = { getItem: () => 't' };
const D = await import(pathToFileURL(path.join(ROOT, 'static/js/handlers/actionDesigner.js')).href);
const A = (o) => ({ action: 'set_tier', version: 1, validated: false, signable: false, trust: { state: 'unsigned' }, ...o });
let j = D.journey(A({}), [], 0);
check('an unvalidated action owes validation, every later step ahead',
    j.map((s) => s.state).join() === 'owed,ahead,ahead,ahead', j.map((s) => s.state));
check('…and its one act is Validate', D.nextAct(A({}), []).act === 'validate');
const blocked = A({ validated: true, signable: false });
check('a validated action a check blocks offers no Sign (an act that cannot move the state) — it owes Validate again, never a dead end',
    D.nextAct(blocked, []).act === 'validate' && D.nextAct(blocked, []).label === 'Validate again' && D.nextAct(blocked, []).blocked === true);
check('…and its journey says so — the Validated step OWED (orange), never done (green)', D.journey(blocked, [], 0)[0].note === 'a check blocks signing' && D.journey(blocked, [], 0)[0].state === 'owed');
const signable = A({ validated: true, signable: true });
check('a signable one owes Sign', D.nextAct(signable, []).act === 'sign' && D.nextAct(signable, []).label === 'Sign');
const changed = A({ validated: true, signable: true, trust: { state: 'changed' } });
check('a changed one owes Re-sign, and the journey names the change',
    D.nextAct(changed, []).label === 'Re-sign' && D.journey(changed, [], 0)[1].note === 'changed since it was signed');
const moved = A({ validated: true, signable: true, trust: { state: 'changed', drift: ['the allowed values of status changed'] } });
check('a changed one whose MODEL moved owes Validate again, never Re-sign (signing a proof of an earlier model is refused)',
    D.nextAct(moved, []).act === 'validate' && D.nextAct(moved, []).label === 'Validate again' && D.nextAct(moved, []).stale === true);
j = D.journey(moved, [], 0);
check('…its journey says the proof is of an earlier model: Validated OWED, Signed names the model',
    j[0].state === 'owed' && j[0].note === 'against an earlier model' && j[1].note === 'the model under it changed', j);
check('…an UNSIGNED action validated against an earlier model owes Validate again too',
    D.nextAct(A({ validated: true, signable: true, trust: { state: 'unsigned', drift: ['new attribute Email'] } }), []).label === 'Validate again');
j = D.journey(A({ validated: true, signable: true, trust: { state: 'changed', stale: 'unrecorded', drift: ['validated before the model facts it depends on were recorded'] } }), [], 0);
check('…one signed BEFORE the facts were recorded says so — not that the model moved',
    j[0].note === 'before model checks' && j[1].note === 'awaits a fresh validation', j);
check('…and the Next box names what moved', /: `the model under it changed since it was validated — \$\{modelDrift\(a\)\.join\('; '\)\}/.test(read('static/js/handlers/actionDesigner.js')));
const waits = A({ validated: true, signable: true, trust: { state: 'changed', drift: [], stands_on: { state: 'changed', label: 'extension by ann' } } });
check('signing that waits for the model it stands on owes THAT signature (Open Govern), never a Sign the gate refuses',
    D.nextAct(waits, []).act === 'govern' && D.nextAct(waits, []).label === 'Open Govern'
    && D.journey(waits, [], 0)[1].note === 'waits for the extension by ann', D.journey(waits, [], 0)[1]);
check('…a model that is signed and intact asks for nothing more (Re-sign)',
    D.nextAct(A({ validated: true, signable: true, trust: { state: 'changed', stands_on: { state: 'trusted', label: 'ontology' } } }), []).label === 'Re-sign');
const dsrc = read('static/js/handlers/actionDesigner.js');
check('…Open Govern opens the graph\'s Govern dialog (an event the KG panel answers), and the Designer re-reads when the model is signed',
    /act === 'govern'[\s\S]{0,200}uderia:open-govern/.test(dsrc) && /UderiaBus\.onComponent\('knowledge_graph', onModel\)/.test(dsrc)
    && /UderiaBus\.on\('uderia:open-govern'[\s\S]{0,160}openKgGovernanceDialog\(d\.kgId/.test(read('static/js/handlers/knowledgeGraphPanelHandler.js')));
check('…Escape closes the dialog on top (Govern over the Designer), not both', /e\.key === 'Escape' && !document\.querySelector\('\.kg-gov-dialog'\)/.test(dsrc));
check('a refused sign shows the gate\'s words, never its wire code', /\(\(r\.body\.validation \|\| \{\}\)\.note\) \|\| r\.body\.message/.test(dsrc));
const signed = A({ validated: true, signable: true, trust: { state: 'trusted' } });
check('a signed, ungranted one owes a grant', D.nextAct(signed, [{ granted: false }]).act === 'grant');
j = D.journey(signed, [{ granted: true }, { granted: true }], 0);
check('a granted one counts its agents and owes nothing but use',
    j[2].note === '2 agents' && j[2].state === 'done' && j[3].state === 'owed' && D.nextAct(signed, [{ granted: true }]).act === null, j);
j = D.journey(signed, [{ granted: true }], 3);
check('in use reads the performed count', j[3].state === 'done' && j[3].note === 'performed 3× in recent sessions');
check('the Designer never offers Sign before validation',
    D.nextAct(A({ validated: false, signable: true }), []).act === 'validate');

const ds = read('static/js/handlers/actionDesigner.js');
check('the try-it names its connection (never left for the server to guess)',
    /\.ad-try-conn/.test(ds) && /connection_id: cid/.test(ds));

// ── §L the Live Status card ──────────────────────────────────────────────────
console.log('§L the Live Status card');
const ui = read('static/js/ui.js');
const card = new Function('escapeHtml', '_mechLink', '_trustChip', '_trustVal',
    extract(ui, '_renderActionRecordDetails') + '\nreturn _renderActionRecordDetails;')(
    escapeHtml, (k) => `[mech:${k}]`, (lvl, txt) => `<chip ${lvl}>${txt}</chip>`, (lvl) => `data-lvl="${lvl}"`);
let h = card({ action: 'set_tier', version: 1, outcome: 'performed', rows_affected: 1, posture: 'enforce',
    invocation_hash: 'abcdef1234567890', confirmed_by: 'user:u1',
    consequence: { lines: ['CustomerID 7: LoyaltyTier bronze → gold <b>'] } });
check('a performed act reads done · 1 row, pass', /<chip pass>set_tier v1 · done · 1 row<\/chip>/.test(h), h);
check('the change is shown, escaped', h.includes('bronze → gold &lt;b&gt;') && !h.includes('<b>'));
check('the receipt is named and sealed', h.includes('abcdef123456 · sealed'));
check('the confirming person is named', h.includes('Confirmed by') && h.includes('user:u1'));
check('the mechanism links to the Guide entry of the BOUNDED point', h.includes('[mech:actions]'));
h = card({ action: 'close_lead', outcome: 'refused', refusal: 'this agent is not granted close_lead' });
check('a refusal reads refused — nothing written, warn, with Why',
    h.includes('<chip warn>close_lead · refused — nothing written') && h.includes('Why') && h.includes('not granted close_lead'));
h = card({ action: 'set_tier', outcome: 'prepared', invocation_hash: 'x' });
check('a prepared act says nothing is written yet, and is not sealed',
    h.includes('awaiting your confirmation, nothing written yet') && !h.includes('sealed'));
h = card({ action: 'set_tier', outcome: 'parked', invocation_hash: 'x' });
check('a parked act reads warn, parked for the owner', h.includes("<chip warn>set_tier · parked for the owner's review"));

// ── §T the title helper ─────────────────────────────────────────────────────
console.log('§T the step titles');
const eh = read('static/js/eventHandlers.js');
const title = new Function(extract(eh, '_assuranceStepTitle') + '\nreturn _assuranceStepTitle;')();
const want = { prepared: 'Action Prepared', performed: 'Action Performed', parked: 'Action Parked for Review',
    observed: 'Action Observed', refused: 'Action Refused' };
for (const [o, t] of Object.entries(want)) {
    check(`${o} → ${t} (live payload shape)`, title('action_record', { details: { outcome: o } }) === t);
}
check('the reload payload shape (details at the top) is titled the same',
    title('action_record', { outcome: 'performed' }) === 'Action Performed');
check('every other step title is unchanged', title('tool_surface', {}) === 'Tool Surface' && title('nope', {}) === null);
check('the category is assurance (the Trust-only filter isolates it)',
    /type === 'turn_budget' \|\| type === 'action_record'/.test(ui));

// ── §I the Inbox row ─────────────────────────────────────────────────────────
console.log('§I the Inbox row for a parked write');
const aph = read('static/js/handlers/agentPerformanceHandler.js');
const row = new Function('esc', '_sevDot', '_agentTagChip',
    extract(aph, '_actionReviewRow') + '\nreturn _actionReviewRow;')(
    escapeHtml, (sev) => `<dot ${sev}>`, (tag) => (tag ? `@${tag}` : ''));
const payload = { action: 'set_tier', version: 2, target: 'fitness_db.Customers',
    consequence: { lines: ['CustomerID 7: LoyaltyTier bronze → gold', '<script>x</script>'], summary: 'update 1 row(s) · bound 1 · every check holds' } };
h = row({ id: 'rv-1', source: 'action', profile_tag: 'AGT', session_id: 'abcdef123456', turn_id: 3,
    original_text: JSON.stringify(payload) });
check('the title names the write and its target', h.includes('A write awaits your approval — set_tier v2 on fitness_db.Customers'));
check('the identical diff is shown, escaped', h.includes('LoyaltyTier bronze → gold') && h.includes('&lt;script&gt;') && !h.includes('<script>'));
check('the summary and "nothing is written until you approve"', h.includes('every check holds') && h.includes('nothing is written until you approve'));
check('one Approve that performs it, and Reject — never a release',
    h.includes('Approve — perform it') && h.includes('data-agent-action="review-approve" data-review-id="rv-1"')
    && h.includes('>Reject<') && !h.includes('review-release'));
h = row({ id: 'rv-2', source: 'action', original_text: 'not json' });
check('an unreadable payload still renders, saying the change was not recorded',
    h.includes('The change was not recorded with this item'));

// ── §F a blocker is said ONCE ─────────────────────────────────────────────────
console.log('§F a blocker is said once — in the checklist; the notice and Next name the checks');
{
  check('the blocking checks are named by id, each once', D.blockerIds(['A3: the key probe…', 'A5: EXPLAIN…', 'A6: role …', 'A6: also']) === 'A3 · A5 · A6' && D.blockerIds([]) === 'a check');
  const src = read('static/js/handlers/actionDesigner.js');
  check('neither the notice, the Next box nor the checklist footer restates the checks\' details (Rule 3.0)',
    !/blockers\)? \|\| \[\]\)\.join\(' · '\)/.test(src) && !/blockers\.map\(_esc\)\.join/.test(src) && (src.match(/blockerIds\(/g) || []).length >= 4);
}

// ── §E editing a contract ─────────────────────────────────────────────────────
console.log('§E editing a contract (the Designer opens exactly what is signed)');
{
  const desc = { entity: 'core.Lead', deployed: 'X.Lead', key: ['lead_id'], backend: 'teradata', label: 'Lead',
    columns: [{ name: 'lead_id', type: 'string', is_key: true, in_model: true },
              { name: 'status', type: 'string', in_model: true },
              { name: 'summary', type: 'string', in_model: true },
              { name: 'closure_reason', type: 'string', in_model: true },
              { name: 'priority', type: 'integer', in_model: true }] };
  const signed = { action: 'record_lead', version: 3, backend: 'teradata', description: 'Records a lead',
    target: { entity: 'core.Lead', deployed: 'X.Lead', key: ['lead_id'] }, operation: 'insert',
    attributes: [{ name: 'lead_id', type: 'string', required: true },
                 { name: 'status', type: 'string', required: false, value_domain: ['open', 'closed'], default: 'open' },
                 { name: 'summary', type: 'string', required: true },
                 { name: 'priority', type: 'integer', required: false, default: 2 }],
    bound: { max_rows: 1 }, authority: { required_role: 'R_RECORD', posture: 'enforce' }, ledger: { mode: 'platform' } };
  const f = D.formFromManifest(desc, signed);
  const back = D.buildManifest(desc, { ...f, version: 3 });
  check('contract → form → contract is identical (nothing lost, nothing invented)', JSON.stringify(back) === JSON.stringify(signed), { back, signed });
  check('a column the contract does not write starts unticked', f.cols.closure_reason.include === false);
  check('an edit that changes nothing is not saved (it would mint a version and void the signature for nothing)',
    D.defineProblems(desc, f).some((p) => /a change to save/.test(p)));
  // the server stores a contract normalised (sorted keys, explicit empty conditions): an
  // unchanged edit of THAT is still unchanged, and authored conditions survive an edit
  const stored = JSON.parse(D.canonicalContract({ ...signed, conditions: { pre: [{ id: 'still_open', expr: "status = 'open'" }], post: [] } }));
  const fs2 = D.formFromManifest(desc, stored);
  check('an unchanged edit of the STORED contract (sorted keys, its conditions) is recognised as unchanged',
    D.defineProblems(desc, fs2).some((p) => /a change to save/.test(p)), D.defineProblems(desc, fs2));
  const saved2 = D.buildManifest(desc, { ...fs2, maxRows: 2, version: 4 });
  check('…and an edit keeps the parts the form does not author — the conditions are never dropped',
    JSON.stringify(saved2.conditions) === JSON.stringify(stored.conditions) && saved2.bound.max_rows === 2 && saved2.version === 4, saved2);
  const changed = { ...f, cols: { ...f.cols, status: { ...f.cols.status, values: 'open, closed, pending' } } };
  check('…one change and it may be saved', !D.defineProblems(desc, changed).some((p) => /a change to save/.test(p)));
  check('the form says it is an edit of that action, its name and role kept', f.editing === 'record_lead' && f.nameTouched && f.roleTouched && f.role === 'R_RECORD');
  const upd = { ...signed, action: 'close_lead', operation: 'update', attributes: [{ name: 'status', type: 'string', required: true, value_domain: ['open', 'closed'] }],
    authority: { required_role: 'R_CLOSE', posture: 'review' } };
  check('…an update round-trips too (its key selects the row, never written)', JSON.stringify(D.buildManifest(desc, { ...D.formFromManifest(desc, upd), version: 3 })) === JSON.stringify(upd));
  const src = read('static/js/handlers/actionDesigner.js');
  check('the row offers Edit contract, which reads the SIGNED contract and opens the form on it',
    /class="ad-btn ad-edit"/.test(src) && /async function openEdit\(name\)/.test(src) && /prefill: m/.test(src) && /formFromManifest\(d, defining\.prefill\)/.test(src));
  check('…and an edit is saved as the next version, said so (re-validated and re-signed before use)',
    /Save the new version/.test(src) && /Saving makes this v\$\{/.test(src) && /a changed contract is validated and signed again before an agent may perform it/.test(src));
  check('the Designer opens on ONE action when asked (the side panel row), its details open',
    /opts && opts\.focus && actions\.some/.test(src) && /expanded = opts\.focus/.test(src));
  const R = read('components/builtin/action/renderer.js');
  const owed = new Function(extract(R, '_owedWord') + '\nreturn _owedWord;')();
  check("the side panel's row button names the act owed, as the Designer will offer it",
    owed({ validated: false }) === 'Validate' && owed({ validated: true, signable: false, trust: {} }) === 'Validate again'
    && owed({ validated: true, signable: true, trust: { state: 'unsigned' } }) === 'Sign'
    && owed({ validated: true, signable: true, trust: { state: 'changed' } }) === 'Re-sign'
    && owed({ validated: true, signable: true, trust: { state: 'changed', drift: ['x'] } }) === 'Validate again'
    && owed({ validated: true, signable: true, trust: { state: 'changed', stands_on: { state: 'changed', label: 'x' } } }) === 'Sign the model first'
    && owed({ validated: true, signable: true, trust: { state: 'trusted' }, granted: false }) === 'Grant to this agent'
    && owed({ validated: true, signable: true, trust: { state: 'trusted' }, granted: true }) === 'Open');
  check('…and it opens the Designer on that action', /data-ap-open="\$\{_esc\(a\.action\)\}"/.test(R) && /openDesignerForAgent\(_panelProfile, \{ focus: b\.getAttribute\('data-ap-open'\) \}\)/.test(R));
}

// ── §W the action owns its connection ───────────────────────────────────────
console.log('§W the action owns the connection it writes through');
{
  const cfg = read('static/js/handlers/configurationHandler.js');
  check('the profile offers no second place for it (one place: the action)',
    !/comp-writes-select/.test(cfg) && !/Writes through/.test(cfg));
  check('a profile save STARTS from each component\'s stored settings — a key the form does not model is never dropped',
    // Nov 2026: the entry starts as a copy of the stored settings; the switch is then written
    // only where the owner holds it (an Auto row stores nothing of the switch) — the property
    // this check protects, a key the form does not model is never dropped, is unchanged
    /componentConfig\[compId\] = JSON\.parse\(JSON\.stringify\(stored\)\);\s*if \(owned\) \{\s*componentConfig\[compId\]\.enabled = toggle\.checked;/.test(cfg)
    && !/componentConfig\[compId\] = \{\s*enabled: toggle\.checked,/.test(cfg));
  const dz = read('static/js/handlers/actionDesigner.js');
  check('the Designer starts on the connection the action was validated through, and says it on the row',
    /const connFor = \(a\) => a\.validated_with \|\| a\.defined_with/.test(dz) && /writes through <b>\$\{_esc/.test(dz)
    && /validated without a connection — validate through the one it writes through/.test(dz));
  const ap = read('src/trusted_data_agent/core/action_perform.py');
  check('a refusal for want of a connection says how a person gives it one — validate through it',
    /validate it through the connection it "\s*f"writes through/.test(ap) && !/Capabilities → Component Configuration/.test(ap));
}

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
