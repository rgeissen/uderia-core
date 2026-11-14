// S7 — the Outcome Loop's surfaces, executed / structurally pinned:
//   (1) the capsule's Drafts disclosure: rendered ONLY when a revision happened, a real tab list
//       (tablist / tab / tabpanel, roving tabindex, arrow keys), the first draft with each finding
//       span MARKED, a clean revision labelled clean, an empty revision said honestly;
//   (2) the Live Status `outcome_revision` card: pass when cleared, warn when not, both finding
//       sets and the cost; (3) the wiring: dispatch sites, reload synthesis, title, dial, the
//       session-guarded live step, the editor card's load/save contract.
import fs from 'node:fs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// ── (1) the drafts, executed ──────────────────────────────────────────────────────────────
{
  const ac = read('static/js/handlers/answerCapsule.js');
  const fnSrc = ac.match(/    function draftsHtml\(td\) \{[\s\S]*?\n    \}\n/)[0];
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const draftsHtml = new Function('esc', fnSrc + '; return draftsHtml;')(esc);
  check('no revision → nothing rendered (mark the exception)', draftsHtml({}) === '' && draftsHtml({ outcome: { revisions: 0, drafts: [{ which: 'first', text: 'x' }] } }) === '');
  const td = { outcome: { revisions: 1, cleared: true, drafts: [
    { which: 'first', text: 'The moon is cheese & rock.', findings: [{ kind: 'unsupported_claim', spans: ['moon is cheese'] }] },
    { which: 'revision_1', text: 'The moon is rock.', findings: [] }] } };
  const h = draftsHtml(td);
  check('a real tab list: tablist + one tab and one panel per draft, roving tabindex, aria wiring', /role="tablist"/.test(h) && (h.match(/role="tab"/g) || []).length === 2 && (h.match(/role="tabpanel"/g) || []).length === 2 && /id="ac-draft-tab-0"[^>]*aria-selected="true"[^>]*aria-controls="ac-draft-panel-0"[^>]*tabindex="0"/.test(h) && /id="ac-draft-tab-1"[^>]*aria-selected="false"[^>]*tabindex="-1"/.test(h) && /aria-labelledby="ac-draft-tab-1"/.test(h));
  check('the first draft carries its finding count; the clean revision says clean', /First draft · 1 finding</.test(h) && /Revision 1 · clean</.test(h));
  check('the finding span is MARKED inside the first draft, the text escaped', /<mark class="ac-draft-mark">moon is cheese<\/mark> &amp; rock/.test(h));
  check('the second panel is hidden until selected', /id="ac-draft-panel-1"[^>]*class="ac-draft-panel hidden"/.test(h));
  check('the verdict chip reads the cleared state', /tc-pass[^>]*>revised — the revision passed the gate/.test(h));
  const h2 = draftsHtml({ outcome: { revisions: 1, cleared: false, drafts: [{ which: 'first', text: 'x', findings: [{ kind: 'k', spans: ['x'] }] }, { which: 'revision_1', text: '', findings: [], empty: true }] } });
  check('not cleared → warn verdict; an empty revision is said honestly', /tc-warn[^>]*>revised — not cleared/.test(h2) && /the revision returned nothing/.test(h2));
  const h3 = draftsHtml({ outcome: { revisions: 1, cleared: false, drafts: [{ which: 'first', text: 'a (b) [c] a.b', findings: [{ kind: 'k', spans: ['(b)', 'a.b'] }] }] } });
  check('regex-special spans are marked literally', /<mark class="ac-draft-mark">\(b\)<\/mark>/.test(h3) && /<mark class="ac-draft-mark">a\.b<\/mark>/.test(h3), h3);
  check('keyboard: arrow keys, Home/End move the selection; wired after the capsule renders', /ArrowRight/.test(ac) && /ArrowLeft/.test(ac) && /'Home'/.test(ac) && /'End'/.test(ac) && /wireDrafts\(body\)/.test(ac) && /\$\{draftsHtml\(td\)\}/.test(ac));
}

// ── (2) the Live Status card, executed ────────────────────────────────────────────────────
{
  const ui = read('static/js/ui.js');
  const fn = ui.match(/function _renderOutcomeRevisionDetails\(details\) \{[\s\S]*?\n\}\n/)[0];
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _mechLink = (k) => `<span data-mech="${k}">${k}</span>`;
  const _trustChip = (lvl, word) => `<span class="trust-chip tc-${lvl}">${word}</span>`;
  const render = new Function('escapeHtml', '_mechLink', '_trustChip', fn + '; return _renderOutcomeRevisionDetails;')(escapeHtml, _mechLink, _trustChip);
  const c = render({ revision: 1, cleared: true, findings_before: [{ kind: 'unsupported_claim', spans: ['moon is cheese'] }], findings_after: [], tokens_in: 120, tokens_out: 30, cost_usd: 0.002, summary: 'revision 1: the finding cleared — the revision is delivered' });
  check('cleared → pass, names the finding before, "no revisable finding remains", the cost', /tc-pass[^>]*>revision 1 cleared the finding/.test(c) && /unsupported claim: moon is cheese/.test(c) && /no revisable finding remains/.test(c) && /120 in · 30 out · \$0\.00200/.test(c));
  const n = render({ revision: 1, cleared: false, findings_before: [{ kind: 'out_of_scope_ref', spans: ['finance_ledger'] }], findings_after: [{ kind: 'out_of_scope_ref', spans: ['finance_ledger'] }], summary: 's' });
  check('not cleared → warn, the remaining finding shown after', /tc-warn[^>]*>revision 1 did not clear it/.test(n) && (n.match(/out of scope ref: finance_ledger/g) || []).length === 2);
  check('routes to the mechanism', /data-mech="outcome_loop"/.test(c));
  const _catChain = (ui.match(/[^\n]*\) return 'assurance';/) || [''])[0];
  check('wired at both live dispatch sites, the reload branch, classified assurance', (ui.match(/case 'outcome_revision'/g) || []).length === 2 && /type === "outcome_revision"/.test(ui) && /type === 'outcome_revision'/.test(_catChain) && /type === 'outcome_revision' \|\|/.test(ui.split("return 'assurance'")[1] || ''));
  check('the live card moves the dial at GROUNDED', /window\.AssuranceDial\.note\(_adSession\(\), 'outcome_loop', details\)/.test(ui) && /outcome_loop: 'grounded'/.test(read('static/js/handlers/assuranceDial.js')));
}

// ── (3) the wiring ────────────────────────────────────────────────────────────────────────
{
  const eh = read('static/js/eventHandlers.js'); const nt = read('static/js/notifications.js'); const ch = read('static/js/handlers/configurationHandler.js'); const idx = read('templates/index.html');
  check('reload: synthesized from turnData.outcome only when the trace lacks the step; titled', /type: 'outcome_revision'/.test(eh) && /s\.action\.metadata\?\.type === 'outcome_revision'/.test(eh) && /if \(eventType === 'outcome_revision'\) return 'Outcome Revision';/.test(eh));
  check('live: a session-guarded Live Status step over the notification channel', /case 'outcome_revision'[\s\S]{0,600}rp\.session_id === state\.currentSessionId[\s\S]{0,200}UI\.updateStatusWindow/.test(nt));
  check('editor: the card under GROUNDED with enabled / max revisions / kinds', /id="profile-card-outcome-loop"/.test(idx) && /id="profile-outcome-enabled"/.test(idx) && /id="profile-outcome-max-revisions"/.test(idx) && /id="profile-outcome-kind-ungrounded_number"/.test(idx));
  check('editor: OFF saves {enabled:false} only (hashes as the signed default); ON writes the bound + a narrowed kinds list', /const oc = \{ enabled: !!en\.checked \};\s*if \(oc\.enabled\) \{/.test(ch) && /if \(kinds\.length < 3\) oc\.revisableKinds = kinds;/.test(ch));
  check('editor: populate reads enabled === true, maxRevisions 2|1, kinds (absent = all)', /o\.enabled === true/.test(ch) && /Number\(o\.maxRevisions\) === 2\) \? '2' : '1'/.test(ch) && /kinds === null \? true : kinds\.includes\(k\)/.test(ch));
  const css = read('static/css/main.css');
  check('drafts CSS on theme tokens, focus ring on the tab, no motion', /\.ac-draft-tab:focus-visible \{ outline: 2px solid var\(--teradata-orange\)/.test(css) && /\.ac-draft-mark[^}]*var\(--trust-warn/.test(css) && !/\.ac-draft[^}]*(animation|transition)/.test(css));
}

if (fail) { console.log(`FAILED — ${fail} of ${pass + fail}`); process.exit(1); }
console.log(`ALL PASS — ${pass} assertions (outcome loop UI)`);
