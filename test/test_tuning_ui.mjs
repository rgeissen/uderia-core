// ACT C1d — the Workload tuning segment's act model (PROP-008 §4.5): the offered act is the
// act that is OWED. A pending proposal offers Approve and Reject; a rebuild proposal is
// advice (Reject only — the platform never rebuilds a table); an approved one offers Land;
// a landed / rejected / failed / expired one offers nothing. The words say what happened —
// a landing reads its before/after estimate and its approver, never a bare "done".
//
// Run: node test/test_tuning_ui.mjs
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
    if (cond) { pass++; console.log(`  PASS  ${name}`); }
    else { fail++; console.log(`  FAIL  ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail) : ''}`); }
};
installDom();
const M = await import('../static/js/handlers/rag/kgUpdateVersions.js');
const P = (status, extra = {}) => ({ id: 't1', status, purpose: 'join_index', table: 'n.incident', landing: 'direct',
    proposal: { columns: ['ORI', 'INCNUM', 'V1'], evidence: '2 statements scanned all 12,351,550 rows' }, ...extra });

console.log('[§A] the offered act is the owed act');
check('pending → Approve, Reject', JSON.stringify(M.tuningActs(P('pending'))) === '["approve","reject"]');
check('a rebuild proposal → Reject only', JSON.stringify(M.tuningActs(P('pending', { landing: 'rebuild' }))) === '["reject"]');
check('approved → Land, and only Land', JSON.stringify(M.tuningActs(P('approved'))) === '["land"]');
for (const st of ['landed', 'rejected', 'failed', 'expired'])
    check(`${st} → nothing to do`, M.tuningActs(P(st)).length === 0);
check('nothing on a missing proposal', M.tuningActs(null).length === 0);

console.log('[§B] the words');
const w = M.tuningWords(P('pending'));
check('names the purpose, the table and the columns', w.what.includes('Covering join index') && w.what.includes('n.incident') && w.what.includes('INCNUM'));
check('…carries the evidence', w.evidence.includes('12,351,550'));
check('…pending reads "worth a look"', w.state[0] === 'worth a look' && w.state[1] === 'warn');
const l = M.tuningWords(P('landed', { receipt: { measured_before: 12.5, measured_after: 3, measured_statements: 2,
    approved_by: 'admin', attestation: { signed: true } } }));
check('a landing reads its before → after estimate, approver and signature',
    l.tail.includes('12.5s → 3s') && l.tail.includes('2 workload statements') && l.tail.includes('approved by admin') && l.tail.includes('signed receipt'), l.tail);
const nm = M.tuningWords(P('landed', { receipt: { measured_before: null, measured_after: null } }));
check('…an unmeasured landing says so, never a number', nm.tail.startsWith('not measured'), nm.tail);
check('a rebuild proposal says it is advice', M.tuningWords(P('pending', { landing: 'rebuild', purpose: 'partition' })).tail.includes('never rebuilds'));
check('a failure reads the database\'s words', M.tuningWords(P('failed', { note: '[Error 3807] x' })).tail.includes('3807'));
const x = M.tuningWords(P('pending', { table: 'n.<b>x</b>' }));
check('names are escaped', !x.what.includes('<b>'));

const g = M.tuningWords(P('landed', { receipt: { measured_before: 37.52, measured_after: 53.95, measured_statements: 2,
    unconfident_before: 2, unconfident_after: 0, approved_by: 'admin', attestation: { signed: true } } }));
check('a rise after statistics is explained as guesses replaced, never read as a regression',
    g.tail.includes('2 of 2 plans rested on a low- or no-confidence estimate') && g.tail.includes('first well-founded'), g.tail);
check('…and said only when guesses were removed', !l.tail.includes('confidence estimate'));

const nc = M.tuningWords(P('landed', { receipt: { measured_before: 37.52, measured_after: 53.95, measured_statements: 2,
    plans_changed: 0, unconfident_before: 0, unconfident_after: 0, approved_by: 'admin', attestation: { signed: true } } }));
check('a landing that changed no plan says so — a cost rise is never read as a regression or a win',
    nc.tail.includes('no plan changed: the workload runs as before'), nc.tail);
const ch = M.tuningWords(P('landed', { receipt: { measured_before: 0.62, measured_after: 0.26, measured_statements: 2,
    plans_changed: 2, approved_by: 'admin', attestation: { signed: true } } }));
check('…and names how many plans changed when they did', ch.tail.includes('2 of 2 plans changed'), ch.tail);

console.log('[§C] the row renders only the owed acts');
const src = readFileSync('static/js/handlers/rag/kgUpdateVersions.js', 'utf8');
check('the row draws its buttons from tuningActs, never its own status test',
    /const acts = tuningActs\(p\)\.map/.test(src) && !/status === 'pending'[^\n]*kg-uv-tn-act/.test(src));
check('the segment stays hidden unless the list answers (OBDA-MODEL · OBDA-ACT off ⇒ no segment)',
    /if \(!r\.ok\) return;\s*\/\/ OBDA-MODEL · OBDA-ACT off/.test(src) && /box\.style\.display = ''/.test(src));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
