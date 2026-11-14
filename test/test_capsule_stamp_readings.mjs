// A gate stamp with NO verdict is an ABSENCE, never a conviction — asserted on the two
// surfaces that read the persisted stamps beside the assurance dial: the answer capsule's
// fact rows and the journey strip. Prod session 0c09571e (a @PXPRODO primer turn): the
// grounding gate abstained (`{ran:false, action:"na", reason:"no groundable content"}`),
// the dial read "nothing in this answer to ground", and the capsule rendered
// "withheld — confirmed drift" in red — its chip had two branches and a fall-through.
// Executes the SHIPPED functions; no server, no network.
import './_uderia_bus.mjs';
import fs from 'node:fs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// The exact stamps the prod turn persisted.
const PROD_GROUNDING = { ran: false, acted: false, action: 'na', gate: 'grounding', reason: 'no groundable content in this answer' };
const PROD_GUARD = { direction: 'outbound', ran: true, acted: false, action: 'pass', categories: [], mode: 'enforce' };

// ── (1) the capsule chips, executed ──────────────────────────────────────────────────────
{
  const ac = read('static/js/handlers/answerCapsule.js');
  const gSrc = ac.match(/    function groundingChip\(g\) \{[\s\S]*?\n    \}\n/)[0];
  const uSrc = ac.match(/    function guardChip\(gu\) \{[\s\S]*?\n    \}\n/)[0];
  const { groundingChip, guardChip } = new Function('esc', gSrc + uSrc + '; return { groundingChip, guardChip };')(esc);
  const g = groundingChip(PROD_GROUNDING);
  check('abstained gate (prod stamp) → absence chip, never withheld', /tc-na/.test(g) && /nothing in this answer to ground/.test(g) && !/withheld/.test(g) && !/tc-danger/.test(g), g);
  check('the words match the dial reader', /nothing in this answer to ground/.test(read('src/trusted_data_agent/agent/assurance_points.py')));
  check('gate error → unknown, said as not-a-pass', /tc-unknown/.test(groundingChip({ ran: true, action: 'error' })) && /not a pass/.test(groundingChip({ ran: true, action: 'error' })));
  check('ran=false with a reason → absence naming the reason', /tc-na[^>]*>did not run — not enabled for this agent/.test(groundingChip({ ran: false, action: 'off', reason: 'not enabled for this agent' })));
  check('the three verdicts keep their chips', /tc-pass[^>]*>in scope/.test(groundingChip({ decision: 'pass' })) && /tc-warn[^>]*>annotated/.test(groundingChip({ decision: 'annotate' })) && /tc-danger[^>]*>withheld — confirmed drift/.test(groundingChip({ decision: 'block' })));
  check('an unrecognised verdict is shown as its word, never as drift', /tc-na[^>]*>review/.test(groundingChip({ decision: 'review' })) && !/withheld/.test(groundingChip({ decision: 'review' })));
  check('no stamp → no row (the row is omitted, not invented)', groundingChip(null) === '');
  check('reason text is escaped', /&lt;b&gt;/.test(groundingChip({ ran: false, action: 'off', reason: '<b>' })));
  const u = guardChip(PROD_GUARD);
  check('a guard that RAN clean → screened — clean', /tc-pass[^>]*>screened — clean/.test(u));
  check('a guard with ran=false is an absence, not a vacuous clean (Rule 3.7)', /tc-na/.test(guardChip({ ran: false, action: 'off', reason: 'master switch off' })) && !/screened — clean/.test(guardChip({ ran: false, action: 'off' })));
  check('guard error → unknown', /tc-unknown/.test(guardChip({ ran: true, action: 'error' })));
  check('block → danger · redact → warn with categories · annotate → warn', /tc-danger[^>]*>blocked \(toxicity\)/.test(guardChip({ ran: true, action: 'block', categories: ['toxicity'] })) && /tc-warn[^>]*>personal data masked: email, ssn/.test(guardChip({ ran: true, action: 'redact', categories: ['email', 'ssn'] })) && /tc-warn[^>]*>caught: pii — annotated/.test(guardChip({ ran: true, action: 'annotate', categories: ['pii'] })));
  check('capsuleHtml routes both rows through the chip builders (one implementation)', /const groundingLine = groundingChip\(g\);/.test(ac) && /const guardLine = guardChip\(gu\);/.test(ac) && (ac.replace(/^\s*\/\/.*$/gm, '').match(/withheld — confirmed drift/g) || []).length === 1);
}

// ── (2) the journey strip's reducer, executed ────────────────────────────────────────────
{
  const tj = read('static/js/handlers/trustJourney.js');
  const win = {}; const doc = { addEventListener() {}, querySelector() { return null; }, getElementById() { return null; }, createElement() { return { style: {}, classList: { add() {} }, setAttribute() {} }; } };
  new Function('window', 'document', 'localStorage', tj)(win, doc, { getItem() { return null; }, setItem() {} });
  const stages = win.TrustJourney.deriveStages({ grounding: PROD_GROUNDING, guardrail: PROD_GUARD, guardrail_inbound: { direction: 'inbound', ran: true, action: 'pass', categories: [] } });
  const gr = stages.find((s) => s.key === 'grounding');
  check('journey: the abstained gate reads na (it once fell back to pass)', gr && gr.state === 'na' && /nothing in this answer to ground/.test(gr.tip), JSON.stringify(gr));
  const ob = stages.find((s) => s.key === 'outbound');
  check('journey: a guard that ran clean reads pass', ob && ob.state === 'pass');
  // An OBSERVE-mode catch (trust campaign 4, S3 — C17): found pii, changed nothing. It read
  // pass/"screened — clean" here and on the dial while the capsule already said "caught".
  const obs = win.TrustJourney.deriveStages({ guardrail: { direction: 'outbound', ran: true, acted: true, action: 'pass',
    mode: 'observe', categories: ['pii'], findings: [{ category: 'pii', label: 'email' }] } }).find((s) => s.key === 'outbound');
  check('journey: an observe-mode catch reads warn and names it, never clean',
        obs && obs.state === 'warn' && /caught pii/.test(obs.tip) && !/clean/.test(obs.tip), JSON.stringify(obs));
  const off = win.TrustJourney.deriveStages({ guardrail: { direction: 'outbound', ran: false, action: 'off', reason: 'master switch off' } }).find((s) => s.key === 'outbound');
  check('journey: a guard with ran=false reads na, never pass', off && off.state === 'na' && /did not run/.test(off.tip), JSON.stringify(off));
  const err = win.TrustJourney.deriveStages({ grounding: { ran: true, action: 'error' } }).find((s) => s.key === 'grounding');
  check('journey: a gate error reads unknown', err && err.state === 'unknown');
  const blk = win.TrustJourney.deriveStages({ grounding: { decision: 'block', out_of_scope_dbs: ['finance'] } }).find((s) => s.key === 'grounding');
  check('journey: a block still reads danger with its scope', blk && blk.state === 'danger' && /finance/.test(blk.tip));
}

// ── (3) the absence family has a chip style on the shared sheet ──────────────────────────
{
  const css = read('static/css/trust.css');
  check('tc-na / tc-unknown are dashed (absence), not a verdict colour', /\.trust-chip\.tc-na \{[^}]*border-style: dashed[^}]*--trust-na/.test(css) && /\.trust-chip\.tc-unknown \{[^}]*border-style: dashed/.test(css));
}

// ── (4) the capsule says each fact ONCE: verdicts on the dial, what it was made from below ──
// Where the dial renders, the grounding gate, the guards and the seal are its stations'
// members in the same words — so the capsule carries "Made from" (things to open or verify)
// and never restates a reading. The Sources row groups by REPOSITORY: a chunk's `source` is a
// DOCUMENT label (its title on one chunk, its file name on the next), and grouping by it
// rendered one repository and its documents as peer "sources", each repeating the
// repository's signature. The chunk shape below is a real @UDERIA turn's (local, Oct 2026).
{
  let ac = read('static/js/handlers/answerCapsule.js');
  ac = ac.replace(/    if \(document\.readyState === 'loading'\)[\s\S]*?\}\)\(\);\s*$/, '    window.__ac = { capsuleHtml };\n})();\n');
  const mk = (dial) => {
    const win = { AssuranceDial: dial ? { enabled: () => true } : undefined, EvidenceView: {} };
    const doc = { addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
                  getElementById() { return null; }, readyState: 'complete' };
    new Function('window', 'document', 'localStorage', ac)(win, doc, { getItem() { return null; } });
    return win.__ac.capsuleHtml;
  };
  const chunk = (doc, source, extra = {}) => ({ collection_id: 2, document_id: doc, source, source_trust: 'changed', verified_days_ago: 0, sync_off: false, ...extra });
  const td = {
    assurance: { stations: {} },
    grounding: { decision: 'block' }, guardrail: { direction: 'outbound', ran: true, action: 'pass', categories: [] },
    provenance_meta: { sealed: true, chain_tip_hash: '36219221b226aa' },
    knowledge_retrieval_event: { chunks: [
      chunk('45f0', 'Uderia Platform REST API Documentation', { collection_name: 'Uderia Documentation' }), chunk('45f0', 'restAPI.md'),
      chunk('fedc', 'CONTEXT_WINDOW_ARCHITECTURE.md'), chunk('fedc', 'Context Window Architecture: Comprehensive Analysis'),
      chunk('6314', 'Knowledge Retrieval Architecture', { sync_off: true }) ] },
  };
  const withDial = mk(true)(td, 's', 1);
  const lbl = (h) => [...h.matchAll(/uppercase tracking-wide[^>]*>([^<]+)</g)].map((m) => m[1]);
  check('with the dial: no Grounding / Guardrail / Provenance reading rows (the dial carries them)',
        !['Grounding', 'Guardrail', 'Provenance'].some((l) => lbl(withDial).includes(l)), lbl(withDial).join(','));
  // "Made from" is a FOLD (closed by default, like the sealed chain inside it) whose line
  // names what it holds, STRUCTURED into what the answer drew on · did · what there is to verify.
  check('…and the material sits in a closed "Made from" fold whose line names what it holds',
        /<details class="ac-made"><summary class="ac-made-sum"><span class="ad-key-lead">Made from<\/span><span class="ac-made-n">1 repository · 3 documents · evidence<\/span><\/summary>/.test(withDial)
        && !/<details class="ac-made" open/.test(withDial));
  const at = (t) => withDial.indexOf(t);
  check('…grouped: Drew on holds the sources; To verify holds the evidence, the chain and the export',
        at('>Drew on<') > 0 && at('>Drew on<') < at('Sources') && at('Sources') < at('>To verify<')
        && at('>To verify<') < at('Evidence') && at('Evidence') < at('ac-export'));
  check('…"Did" only when the answer wrote something, and no sentence restating the group labels',
        !/>Did</.test(withDial) && !/ac-made-sub/.test(withDial)
        && />Did</.test(mk(true)({ ...td, action_acts: [{ outcome: 'performed', words: 'Closed lead L3201' }] }, 's', 1)));
  const srcs = withDial.match(/<div class="ac-src">/g) || [];
  check('one repository → ONE source group with ONE signature pill', srcs.length === 1
        && (withDial.match(/trust-chip u-tip tc-warn[^>]*>Uderia Documentation · signature changed/g) || []).length === 1);
  const docs = [...withDial.matchAll(/<span class="ac-doc u-tip"[^>]*>([^<]+)/g)].map((m) => m[1]);
  check(`its documents listed once each, by document id, title preferred (${docs.join(' | ')})`,
        docs.length === 3 && docs.includes('Uderia Platform REST API Documentation')
        && docs.includes('Context Window Architecture: Comprehensive Analysis') && docs.includes('Knowledge Retrieval Architecture'));
  check('…each with its own freshness (sync off named on the document that has it)',
        /Knowledge Retrieval Architecture<span class="ac-doc-fresh"> · sync off/.test(withDial)
        && /Uderia Platform REST API Documentation<span class="ac-doc-fresh"> · verified 0d ago/.test(withDial));
  check('…and the file name survives in the document’s tooltip', /data-tip="Uderia Platform REST API Documentation\nrestAPI\.md/.test(withDial));
  const noName = mk(true)({ ...td, knowledge_retrieval_event: { chunks: [chunk('a', 'x.md')] } }, 's', 1);
  check('a turn recorded before the repository name was stamped says "knowledge repository", never a document label',
        />knowledge repository · signature changed</.test(noName) && !/>x\.md · signature/.test(noName));
  const legacy = mk(false)(td, 's', 1);
  check('without the dial (the legacy strip) the readings stay — nothing else carries them',
        ['Grounding', 'Guardrail', 'Provenance'].every((l) => lbl(legacy).includes(l)) && !/ac-made/.test(legacy));
  check('…in their original order among the rows',
        JSON.stringify(lbl(legacy)) === JSON.stringify(['Grounding', 'Sources', 'Guardrail', 'Provenance', 'Evidence']), lbl(legacy).join(','));
}

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
