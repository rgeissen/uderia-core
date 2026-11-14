/**
 * PROP-003 · Phase 1 — the binding studio's rendering contract.
 *
 * EXECUTED, never grepped. The studio's whole job is to render states that are easy to
 * render wrongly — an unbound lane, a lane that does not exist, a binding nothing
 * consults — and a grep for a class name passes while any of them is broken. So this gate
 * loads the shipped module under a minimal DOM, feeds it real payload shapes, and reads
 * the HTML it actually produces.
 *
 * No server, no network, no browser.
 */
import './_uderia_bus.mjs';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
let PASS = 0, FAIL = 0;

function check(name, cond, detail = '') {
    if (cond) { PASS++; console.log(`  PASS  ${name}`); }
    else { FAIL++; console.log(`  FAIL  ${name}${detail ? `\n        ${detail}` : ''}`); }
}
function section(t) { console.log(`\n──── ${t} ${'─'.repeat(Math.max(0, 60 - t.length))}`); }

// ── a minimal DOM, faithful where it matters ─────────────────────────────────
// Faithful stub rule: this must not silently accept what a browser would reject. The
// host records what is written to it; everything else is inert but present.
const _host = { id: 'assurance-bindings-host', innerHTML: '',
                // A real host has both. The stub had only querySelectorAll, so the first
                // code path to use querySelector died in the harness rather than failing
                // an assertion — an incomplete stub, not a defect in the module.
                querySelector: () => null,
                querySelectorAll: () => [] };
const _els = { 'assurance-bindings-host': _host, 'ab-picker': null, 'ab-css': {} };
globalThis.document = {
    getElementById: (id) => _els[id] ?? null,
    createElement: () => ({ style: {}, setAttribute() {}, appendChild() {},
                            addEventListener() {}, querySelector: () => null,
                            querySelectorAll: () => [], remove() {}, dataset: {} }),
    head: { appendChild() {} },
    body: { appendChild() {} },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
};
globalThis.window = {};
globalThis.localStorage = { getItem: () => 'test-token', setItem() {} };
globalThis.CSS = { escape: (s) => s };
globalThis.setTimeout = (fn) => fn && 0;
globalThis.clearTimeout = () => {};

// The served payload the studio consumes, in the shape `/v1/assurance/bindings` returns.
let _served = null;
globalThis.fetch = async () => ({ ok: true, json: async () => _served });

const SRC = readFileSync(join(ROOT, 'static/js/handlers/assuranceBindings.js'), 'utf8');
eval(SRC);
const AB = globalThis.window.AssuranceBindings;
check('the studio module loads and exports its API',
      !!AB && typeof AB.mount === 'function' && typeof AB.refresh === 'function');

// ── the payload ──────────────────────────────────────────────────────────────
function row(property, label, lane, opts = {}) {
    return { property, label, lane, meaningful: opts.meaningful !== false,
             assessor: opts.assessor ?? null, bound: !!opts.assessor,
             note: opts.note ?? null };
}

function payload(over = {}) {
    return Object.assign({
        rows: [
            // bound in both lanes, and CONSULTED in flight
            row('context_use', 'Context use', 'IN_FLIGHT_TRIGGERED',
                { assessor: 'content_word_overlap' }),
            row('context_use', 'Context use', 'IN_FLIGHT', {}),
            row('context_use', 'Context use', 'POST_HOC',
                { assessor: 'content_word_overlap' }),
            // bound in flight, but NOTHING consults it — the honest reading
            row('answer_support', 'Answer support', 'IN_FLIGHT',
                { assessor: 'corpus_support' }),
            row('answer_support', 'Answer support', 'IN_FLIGHT_TRIGGERED', {}),
            row('answer_support', 'Answer support', 'POST_HOC',
                { assessor: 'corpus_support' }),
            // a lane the property does not HAVE, with its own reason
            row('tool_authority', 'Tool authority', 'IN_FLIGHT',
                { meaningful: false,
                  note: 'the boundary needs enforcement in flight, not a measurement' }),
            row('tool_authority', 'Tool authority', 'IN_FLIGHT_TRIGGERED',
                { meaningful: false, note: 'same reason' }),
            row('tool_authority', 'Tool authority', 'POST_HOC',
                { assessor: 'authority_scope' }),
            // meaningful, but NOTHING bound
            row('request_integrity', 'Request integrity', 'IN_FLIGHT', {}),
            row('request_integrity', 'Request integrity', 'IN_FLIGHT_TRIGGERED', {}),
            row('request_integrity', 'Request integrity', 'POST_HOC',
                { meaningful: false, note: 'there is no ground truth to grade a refusal against' }),
            // a third-party property, bound to an UNSIGNED module
            row('acme.tone', 'Tone', 'POST_HOC', { assessor: 'acme.tone_rules' }),
            row('acme.tone', 'Tone', 'IN_FLIGHT', { meaningful: false, note: 'score only' }),
            row('acme.tone', 'Tone', 'IN_FLIGHT_TRIGGERED', { meaningful: false, note: 'score only' }),
        ],
        symmetry: {
            context_use: { state: 'shared' },
            answer_support: { state: 'shared' },
            tool_authority: { state: 'by_design_score_only' },
            request_integrity: { state: 'unbound' },
            'acme.tone': { state: 'by_design_score_only' },
        },
        inflight_consumers: { context_use: 'grounding_gate' },
        overrides: [],
        modules: [
            { key: 'content_word_overlap', label: 'Content-word overlap', builtin: true,
              trust: { state: 'builtin' }, capabilities: ['PURE', 'REPLAYABLE'] },
            { key: 'corpus_support', label: 'Corpus support', builtin: true,
              trust: { state: 'builtin' }, capabilities: ['PURE'] },
            { key: 'authority_scope', label: 'Authority scope', builtin: true,
              trust: { state: 'builtin' }, capabilities: ['REPLAYABLE'] },
            { key: 'acme.tone_rules', label: 'Tone rules', builtin: false,
              trust: { state: 'unsigned', pack_id: 'acme' }, capabilities: ['PURE'] },
        ],
        drift: [],
    }, over);
}

/** Render, in a named VIEW. The studio has two axes — a guarantee ring and the full
 *  capability table — and BOTH must work: neither is a subset of the other, and every
 *  action lives on a row that both reach. A harness that only ever drove the default
 *  view would let the other rot silently, which is the same class of gap as a renderer
 *  tested outside its own scope. */
async function render(p, view) {
    _served = p;
    _host.innerHTML = '';
    await AB.refresh();
    if (view) { AB._setViewForTest(view); }
    return _host.innerHTML;
}

/** The capability table, whichever view is the shipped default. */
async function renderTable(p) { return render(p, 'table'); }

// ── 1 · every cell state renders its OWN family ──────────────────────────────
section('1 · cell states — absence is rendered, never hidden');

let html = await renderTable(payload());
check('the matrix renders', html.includes('ab-matrix') && html.includes('<tbody>'));

const rowOf = (prop) => {
    const i = html.indexOf(`data-prop="${prop}"`);
    return i < 0 ? '' : html.slice(i, html.indexOf('</tr>', i));
};

check('a bound lane names its module',
      rowOf('context_use').includes('Content-word overlap'));
check('…marked as a module with the M badge',
      rowOf('context_use').includes('ab-mark') &&
      rowOf('context_use').includes('>M<'));
check('an UNBOUND but meaningful lane says so — never a blank cell',
      rowOf('request_integrity').includes('nothing bound'));
check('…and states that this is not a pass',
      rowOf('request_integrity').includes('never assessed-clean'));
check('a lane the property does not HAVE renders its own reason',
      rowOf('tool_authority').includes('needs enforcement in flight'));
check('…in the by-design family, not the unbound one',
      rowOf('tool_authority').includes('ab-cell--design') &&
      !rowOf('tool_authority').includes('nothing bound'));
// Isolate the cell rather than scanning a window of characters after it: a distance-
// bounded regex reaches into the NEXT cell of the same row, so it reported a bound
// post-hoc cell's button as the design cell's. The property held; the measurement did not.
const designCell = (rowHtml) => {
    const i = rowHtml.indexOf('ab-cell--design');
    if (i < 0) return '';
    return rowHtml.slice(i, rowHtml.indexOf('</div>', i));
};
check('a by-design absence never offers a picker button',
      !designCell(rowOf('tool_authority')).includes('ab-pick'),
      designCell(rowOf('tool_authority')));

section('2 · the two in-flight lanes are ONE column');

check('the header renders exactly two lane columns',
      (html.match(/<th class="u-tip"/g) || []).length === 3,  // 2 lanes + symmetry
      html.slice(html.indexOf('<thead'), html.indexOf('</thead>')));
check('a TRIGGERED binding fills the in-flight column',
      rowOf('context_use').indexOf('Content-word overlap') <
      rowOf('context_use').lastIndexOf('Content-word overlap'));
check('…and is named as escalating, not deciding',
      rowOf('context_use').includes('can only escalate to a judge'));
check('an UNCONDITIONAL binding is named as able to act',
      rowOf('answer_support').includes('can act on the answer'));
check('a post-hoc binding reports only',
      rowOf('tool_authority').includes('reports only'));

section('3 · the three in-flight readings, kept distinct');

// PROP-003's in-flight seam made a third reading real, and collapsing any two of them
// loses a fact: a gate may RESOLVE a property itself, may consult it through the seam
// (its own assessment plus this one), or may not consult it at all.
html = await renderTable(payload({
    inflight_consumers: { context_use: 'grounding_gate',
                          answer_support: 'grounding_gate' },
    seam_consulted: ['answer_support'],
    routed: {},
}));
check('a property no gate consults in flight still says so',
      rowOf('acme.tone').includes('nothing bound')
      || !rowOf('acme.tone').includes('acts on this'));
check('a property the gate RESOLVES itself carries neither note',
      !rowOf('context_use').includes('no gate consults this in flight yet')
      && !rowOf('context_use').includes('acts on this'));
check('a property consulted through the SEAM says which gate acts on it',
      rowOf('answer_support').includes('grounding gate acts on this'),
      rowOf('answer_support'));
check('…and states the union rule, so nobody reads it as a replacement',
      /data-tip="[^"]*never make it pass where it would have acted/.test(
          rowOf('answer_support')));

// A pack property routed by its manifest is consulted for the same reason, and must read
// the same way — this is the whole in-flight half of the plane.
html = await renderTable(payload({
    inflight_consumers: { context_use: 'grounding_gate' },
    seam_consulted: [],
    routed: { 'acme.tone': 'outbound_guard' },
    rows: payload().rows.map((r) => (
        r.property === 'acme.tone' && r.lane === 'IN_FLIGHT'
            ? { ...r, meaningful: true, note: null,
                assessor: 'acme.tone_rules', bound: true }
            : r)),
}));
check('a ROUTED pack property reads as consulted by the gate it named',
      rowOf('acme.tone').includes('outbound guard acts on this'),
      rowOf('acme.tone'));

html = await renderTable(payload());
check('a bound property nothing consults still says so',
      rowOf('answer_support').includes('no gate consults this in flight yet'));

section('4 · third-party modules carry their trust state');

check('an unsigned third-party module shows its seal',
      rowOf('acme.tone').includes('ab-seal') &&
      rowOf('acme.tone').includes('unsigned'));
check('…and a builtin does NOT (trusted by shipping is not an actionable state)',
      !rowOf('context_use').includes('ab-seal'));
check('a pack property is attributed to its pack',
      rowOf('acme.tone').includes('ab-tag--pack') && rowOf('acme.tone').includes('acme'));

section('5 · overrides and drift are visible');

html = await renderTable(payload({
    overrides: [{ property: 'context_use', lane: 'POST_HOC', assessor_key: 'other',
                  bound_at: '2026-08-06T10:00', note: 'tuned' }],
}));
check('a cell changed from the shipped default is tagged',
      rowOf('context_use').includes('>override<'));
check('…explaining when and why, in a tooltip',
      /data-tip="[^"]*Changed from the shipped default[^"]*tuned/.test(rowOf('context_use')));

html = await renderTable(payload({
    drift: [{ property: 'acme.tone', lane: 'POST_HOC' }],
}));
check('a drifting module is marked on its cell',
      rowOf('acme.tone').includes('ab-cell--drift'));
check('…and names the act that is owed (Rule 8.5)',
      rowOf('acme.tone').includes('re-sign it or unbind'));

section('6 · symmetry is served, not recomputed');

html = await renderTable(payload());
check('each property shows its symmetry state',
      rowOf('context_use').includes('ab-sym--shared'));
check('…in words, on hover — never a bare wire token',
      /data-tip="[^"]*never disagree/.test(rowOf('context_use')));
check('an unbound property reads as such',
      rowOf('request_integrity').includes('ab-sym--unbound'));

section('7 · the house UI rules');

check('no native title attribute (tooltips are u-tip)', !/\stitle="/.test(html));
check('every tooltip host carries the u-tip class',
      (html.match(/data-tip="/g) || []).length ===
      (html.match(/u-tip[^"]*"[^>]*data-tip="|data-tip="[^"]*"[^>]*u-tip/g) || []).length ||
      !html.includes('data-tip'),
      'a data-tip without u-tip renders nothing');
check('no emoji on the surface',
      !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(html));
const CSS_SRC = SRC.slice(SRC.indexOf('function _injectCss'));
check('colours come from CSS variables, never hardcoded Tailwind classes',
      !/class="[^"]*\b(?:text|bg|border)-(?:red|green|amber|purple|blue|gray)-\d{2,3}/.test(SRC));
check('…and every state colour reads a trust token',
      CSS_SRC.includes('var(--trust-pass') && CSS_SRC.includes('var(--trust-warn') &&
      CSS_SRC.includes('var(--trust-danger'));

section('8 · nothing is applied without an explicit confirmation');

check('selecting a module opens a confirm step, it does not POST',
      /_confirm\(prop, lane, b\.dataset\.key, 'bind'\)/.test(SRC));
check('…and only the confirm step calls _apply',
      (SRC.match(/_apply\(/g) || []).length === 2,   // the definition + the one call
      'exactly one call site, from the confirmation');
check('the unbind act states the consequence in words',
      SRC.includes('never assessed-clean') &&
      /ab-warn[\s\S]{0,200}NOT ASSESSED/.test(SRC));
// The refusal path MOVED with staging, and did not weaken. Eligibility is still decided
// at PICK time (`conformance.candidates()` returns blocked-with-reasons before anything is
// written, and the picker never offers a blocked module), so nothing an operator can stage
// is a refusal waiting to happen. What survives to apply-time is a server-side re-check —
// fail-closed, unchanged — and its reason is reported against the row that caused it.
check('a refusal at apply time carries the server\'s own reasons',
      /res\.body && res\.body\.reasons/.test(SRC) && /failed\.push\(/.test(SRC));
// Live-found in the browser: the currently-bound module was offered as a change, and the
// confirm step read "Corpus support → Corpus support". An act that cannot move the state
// reads as the fix (Rule 8.5), and it would have written a no-op to the audit trail.
check('the module already bound here is NOT offered as a change',
      /const eligible = [^;]*!isCurrent/.test(SRC));
check('…and says why it is not selectable, rather than being hidden',
      SRC.includes('this is what already assesses it here'));
check('…and the picker no longer needs to survive a refusal, because it cannot cause one',
      !/_closePicker\(\);\s*await refresh\(\)/.test(SRC),
      'confirm records a decision; it does not write, so there is nothing to refuse there');


section('controls — the machinery that ACTS');

// The half that was missing entirely: the studio showed what a guarantee SEES and said
// nothing about whether anything acts on it, so a dormant third-party control and a
// working one looked identical (they were both simply absent).
check('the studio renders a controls table', SRC.includes('_controlsHtml'));
// Anchored in the FUNCTION, not by distance: the first version bounded the gap at 900
// chars and broke the moment a comment grew between them, while the property held. That
// trap has now cost this repo four fixes; a bound is not an assertion.
const _SH = SRC.slice(SRC.indexOf('function _stationHtml'), SRC.indexOf('let _tab ='));
// Reached from BOTH views, by different paths: the capability view appends the whole
// controls table, the guarantee view renders the station's own subset. Asserting the
// shared row renderer covers both without pinning either path's shape.
check('…and it is reachable from the render, not defined and orphaned',
      SRC.includes('_controlsHtml()')
      && SRC.slice(SRC.indexOf('function _stationHtml'),
                   SRC.indexOf('let _tab =')).includes('_controlRowsHtml'));
check('…and BOTH views reach it — the ring by station, the table whole',
      /_view === 'ring'[\s\S]{0,600}_stationHtml\(\)/.test(SRC)
      && /_matrixHtml\(\)[\s\S]{0,600}_controlsHtml\(\)/.test(SRC));
check('…with an honest reading when nothing acts',
      /No control is registered[\s\S]{0,120}upheld by what is recorded/.test(SRC));

// DORMANT is the state an operator will misread: installed, signed, visible, doing
// nothing. It has to be marked, and the mark has to say what it means.
check('a dormant control is MARKED as such', SRC.includes('dormant &mdash; changes nothing'));
check('…derived from what it actually does now, not from its posture string',
      /const dormant = !c\.acts_now/.test(SRC));
check('"nobody decided" is kept apart from "decided the default"',
      SRC.includes('no decision recorded') && SRC.includes('ab-restore-ctl'));

// Raising a control starts altering live answers. It must never happen on the select's
// own event — a stray scroll over a dropdown is not a decision.
check('a posture change is ARMED, never applied by the select itself',
      /addEventListener\('change', \(\) => _armPosture/.test(SRC)
      && !/addEventListener\('change', \(\) => _applyPosture/.test(SRC));
check('…and the staged value is shown in the select, so the row cannot contradict the confirm',
      /_pending\.key === c\.key[\s\S]{0,60}_pending\.posture/.test(SRC));
check('…the confirm names the consequence in the operator\'s words',
      SRC.includes('will start acting on live') && SRC.includes('act_words'));
check('…including that it moves the deployment attestation',
      /deployment attestation[\s\S]{0,80}changed/.test(SRC));
check('…while standing a control DOWN applies straight away (no ceremony to click through)',
      /if \(!willAct\) return _applyPosture/.test(SRC));

check('the ladder is READ, never re-derived with a second opinion',
      SRC.includes('never re-derived here'));
check('a refused posture is reported against its own control',
      /_stagedPost\.delete\(key\)/.test(SRC) && /label: key/.test(SRC));
// A refused change must not cost the operator the rest of their work. The delete is on
// the SUCCESS branch only, so a refusal leaves that one staged with its reason while the
// others apply — asserted in the staging section against applyStaged directly.


// Found in the browser: GUARDED read "nothing acts here" while two gates uphold it. The
// list was the third-party registry alone, so with no pack installed EVERY station was
// empty — one concept living in two registries, and only one reaching the surface. Same
// dogfooding gap the modules had.
const _CA = SRC.slice(SRC.indexOf('function _ctrlsAt'), SRC.indexOf('function _ringHtml'));
check('what-acts-on-it reads the PLATFORM registry, not only third-party',
      _CA.includes('_model.mechanisms') && _CA.includes('_model.controls'),
      'a station upheld by a native gate must never read "nothing acts here"');
check('…platform first — it is what is there before anyone installs anything',
      _CA.indexOf('mechanisms') < _CA.indexOf('.controls'));
check('…and a platform mechanism offers no deployment-wide posture switch',
      /if \(!c\.third_party\)[\s\S]{0,600}posture_note/.test(SRC)
      && !/if \(!c\.third_party\)[\s\S]{0,600}ab-posture/.test(SRC),
      'a gate mode is per-agent; a switch here would silently mean something else');


// The matrix is a REMAPPING surface, and remapping needs alternatives. With no pack
// installed each property has one shipped module, the bound one is correctly not offered
// as a change, and only unbind (a weakening) and restore remain — so the table looked
// configurable and offered nothing. Every other absence here is rendered; this one was not.
{
    // The default fixture carries a third-party module, so this must override it — the
    // first version of this assertion assumed otherwise and failed against correct code.
    const noPacks = await renderTable(payload({
        modules: [{ key: 'corpus_support', label: 'Corpus support', builtin: true,
                    trust: { state: 'builtin' }, capabilities: ['PURE'] }],
    }));
    check('with no alternatives installed, the table SAYS there is nothing to remap',
          /nothing to\s+remap yet/.test(noPacks),
          'a control surface with an empty control space must say so, not look broken');
    check('…and names what would add the choices',
          /third-party pack adds the choices/.test(noPacks));
    check('…and is honest that it is currently a reading, not a control',
          /is a\s+reading, not a control/.test(noPacks));

    const withPack = await renderTable(payload({
        modules: [{ key: 'corpus_support', label: 'Corpus support', builtin: true,
                    trust: { state: 'builtin' }, capabilities: ['PURE'] },
                  { key: 'acme.alt', label: 'Alt', builtin: false,
                    trust: { state: 'trusted', pack_id: 'acme' }, capabilities: ['PURE'] }],
    }));
    check('…and the note is GONE once a pack could supply one',
          !/nothing to\s+remap yet/.test(withPack),
          'the note must not survive into a deployment where remapping is real');
}


section('staging — one save model in one column');

// The card applied on confirm while it sat below the panel's Save row. Once it moved INTO
// the stack, two save models shared one column. Changes are now chosen here and applied by
// the panel's Save — and the refusal path deliberately did NOT move with them: eligibility
// is decided at PICK time by `conformance.candidates()`, so staging cannot defer a refusal.
check('a binding confirm stages rather than writes',
      /function _apply\(prop, lane, key, act\) \{\s*_stage\(/.test(SRC),
      'applying on confirm is the behaviour that made the card a second save model');
check('…and a posture change stages too', /_stagedPost\.set\(key, posture\)/.test(SRC));
check('…restore stages as null, kept apart from staging observe',
      /_stagedPost\.set\(key, null\)/.test(SRC),
      'one forgets the decision, the other records it — the store keeps them apart');

check('a staged cell shows what it WILL be, marked as not in force',
      SRC.includes('ab-cell--staged') && SRC.includes('not applied yet'),
      'showing the old value hides the decision; showing the new one unmarked claims it is live');
check('…and every staged change can be undone individually',
      SRC.includes('data-undo=') && SRC.includes('data-undo-post='));
check('…with a running count and a discard for all of them',
      SRC.includes('ab-dirty') && SRC.includes('data-ab-discard'));

// `/beforeunload/` alone passed a mutation that deleted the ADD and left the REMOVE —
// the string survived while the guard did not. Assert each direction by itself.
check('leaving with unapplied work warns',
      /addEventListener\('beforeunload', _onBeforeUnload\)/.test(SRC),
      'an accordion hides the row, so a staged change would be invisible AND lost');
check('…and the warning is REMOVED once nothing is staged',
      /removeEventListener\('beforeunload'/.test(SRC),
      'a page that always warns is one people learn to click through');

// Applying: sequential, one act each, and a refusal must not cost the operator the rest.
const _AS = SRC.slice(SRC.indexOf('async function applyStaged'), SRC.indexOf('// ── the ring'));
check('staged changes apply as their OWN requests, never a batch',
      !/batch/i.test(_AS) && /for \(const \[k, c\] of \[\.\.\._staged\]\)/.test(_AS),
      'one act, one audit event, and a refusal attributable to its row');
// Ordering was the first version of this check, and a mutation that moved the delete
// OUT of the success branch still satisfied it — the delete simply stayed earlier in the
// file. The property is that the delete is INSIDE the ok branch, so assert that shape.
check('…a refused change stays staged, with its reason',
      /if \(res\.ok\) \{ _staged\.delete\(k\); applied \+= 1; \}/.test(_AS)
      && /if \(res\.ok\) \{ _stagedPost\.delete\(key\); applied \+= 1; \}/.test(_AS)
      && /failed\.push\(/.test(_AS),
      'the delete must be on the SUCCESS branch only, or a refusal destroys the choice');
check('…and the count reported is what actually applied',
      /applied \+= 1/.test(_AS) && /return \{ applied, failed \}/.test(_AS));
check('…and a refusal lands on the ROW that caused it, not only in the Save status',
      /_failed\.set\(k, reason\)/.test(SRC) && /_failed\.set\(key, reason\)/.test(SRC)
      && /ab-err--row/.test(SRC),
      'the status line carries one reason; "1 refused" without saying which is a dead end');
check('…and choosing again clears the old refusal',
      /_failed\.delete\(_stageKey\(prop, lane\)\)/.test(SRC)
      && /function _unstage[\s\S]{0,120}_failed\.delete\(k\)/.test(SRC),
      'a stale refusal beside a new choice describes a decision nobody made');
check('…the table is re-read from the server afterwards',
      /await refresh\(\)/.test(_AS),
      'the applied state must come from the server, never from the staged copy');

console.log(`\n${'='.repeat(52)}\nRESULT: ${PASS} passed, ${FAIL} failed`);
process.exit(FAIL ? 1 : 0);
