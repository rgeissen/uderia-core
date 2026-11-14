#!/usr/bin/env node
/**
 * Posture-editor round-trip gate — the F5-class contract, made executable.
 *
 * The profile editor renders the posture ladder as TWO postures with one
 * property each (observe+disclose ⇒ 'annotate', enforce+retain ⇒ 'review').
 * The defect class this pins: a stored mode the populate path does not
 * recognize DISPLAYS as the fallback and the next Save then PERSISTS the
 * downgrade — a protection silently weakened by opening and saving a form.
 * That trap shipped twice before the recognition list was centralized, and a
 * presentation change to the same code is exactly where it would ship a third
 * time.
 *
 * Method (mirrors test_trust_circle_grammar.mjs): extract the REAL helper
 * block verbatim from configurationHandler.js — POSTURE_MODES → composeGateMode
 * — and execute it against a stub modal. Every stored mode, for every gate
 * group, must populate the radios/checkboxes AND compose back to the SAME
 * string. No reimplementation: if the shipped code downgrades, this fails.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', 'static', 'js', 'handlers', 'configurationHandler.js'), 'utf8');

// ── verbatim extraction ──────────────────────────────────────────────────────
const start = src.indexOf('const POSTURE_MODES =');
if (start < 0) { console.error('FAIL: POSTURE_MODES block not found'); process.exit(1); }
const endMarker = 'return el.value;\n    };';
const end = src.indexOf(endMarker, start);
if (end < 0) { console.error('FAIL: composeGateMode end not found'); process.exit(1); }
const block = src.slice(start, end + endMarker.length);
for (const sym of ['GATE_PROPS', 'applyGateMode', 'composeGateMode', 'syncGateProps']) {
    if (!block.includes(sym)) { console.error(`FAIL: extracted block lacks ${sym}`); process.exit(1); }
}

// ── stub modal ───────────────────────────────────────────────────────────────
function makeModal() {
    const radios = {};   // group -> value -> el
    const boxes = {};    // id -> el
    const GROUPS = {
        'agent-posture-mode': ['observe', 'enforce'],
        'grounding-mode': ['', 'observe', 'enforce'],
        'grounding-corpus-mode': ['', 'observe', 'enforce'],
        'honesty-mode': ['', 'off', 'observe', 'enforce'],
        'guardrail-mode': ['', 'observe', 'enforce'],
        'guardrail-inbound-mode': ['', 'observe', 'enforce'],
    };
    const BOX_IDS = [
        'agent-posture-annotate-disclose', 'agent-posture-review-retention',
        'grounding-annotate-disclose', 'grounding-review-retention',
        'grounding-corpus-annotate-disclose', 'grounding-corpus-review-retention',
        'honesty-annotate-disclose', 'honesty-review-retention',
        'guardrail-annotate-disclose', 'guardrail-review-retention',
        'guardrail-inbound-annotate-disclose', 'guardrail-inbound-review-retention',
    ];
    for (const [g, vals] of Object.entries(GROUPS)) {
        radios[g] = {};
        for (const v of vals) {
            // Real radio-group semantics: checking one unchecks its siblings —
            // without this the stub under-tests posture flips (a second
            // applyGateMode left both radios checked).
            const el = { value: v, onchange: null, _checked: false };
            Object.defineProperty(el, 'checked', {
                get() { return this._checked; },
                set(val) {
                    if (val) for (const s of Object.values(radios[g])) s._checked = false;
                    this._checked = !!val;
                },
            });
            radios[g][v] = el;
        }
    }
    for (const id of BOX_IDS) {
        boxes[id] = { checked: false, disabled: false,
                      closest: () => ({ style: {} }) };
    }
    return {
        _radios: radios, _boxes: boxes,
        querySelector(sel) {
            let m = sel.match(/^input\[name="([^"]+)"\]\[value="([^"]*)"\]$/);
            if (m) return (radios[m[1]] || {})[m[2]] || null;
            m = sel.match(/^input\[name="([^"]+)"\]:checked$/);
            if (m) return Object.values(radios[m[1]] || {}).find((r) => r.checked) || null;
            m = sel.match(/^#([\w-]+)$/);
            if (m) return boxes[m[1]] || null;
            return null;
        },
        querySelectorAll(sel) {
            const m = sel.match(/^input\[name="([^"]+)"\]$/);
            return m ? Object.values(radios[m[1]] || {}) : [];
        },
    };
}

let pass = 0, fail = 0;
const check = (label, cond) => {
    if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${label}`); }
};

// Execute the verbatim block with a fresh stub per scenario.
function harness() {
    const modal = makeModal();
    // eslint-disable-next-line no-new-func
    const fn = new Function('modal', `${block}\n return { applyGateMode, composeGateMode, GATE_PROPS };`);
    return { modal, ...fn(modal) };
}

// ── 1 · round-trip: every stored mode survives populate → save unchanged ────
const CASES = [
    // The agent-level default has no Inherit (it IS the root of the chain).
    ['agent-posture-mode', ['observe', 'annotate', 'enforce', 'review'], 'observe', []],
    // Every gate defaults to '' (Inherit — mode key omitted on save = the stored form).
    ['grounding-mode', ['', 'observe', 'annotate', 'enforce', 'review'], '', []],
    ['grounding-corpus-mode', ['', 'observe', 'annotate', 'enforce', 'review'], '', []],
    ['honesty-mode', ['', 'off', 'observe', 'annotate', 'enforce', 'review'], '', ['off']],
    ['guardrail-mode', ['', 'observe', 'annotate', 'enforce', 'review'], '', []],
    ['guardrail-inbound-mode', ['', 'observe', 'annotate', 'enforce', 'review'], '', []],
];
for (const [group, modes, dflt, extra] of CASES) {
    for (const stored of modes) {
        const { applyGateMode, composeGateMode } = harness();
        applyGateMode(group, stored, dflt, extra);
        const out = composeGateMode(group);
        check(`${group}: stored '${stored || '(inherit)'}' round-trips (got '${out}')`,
              out === stored);
    }
}

// ── 2 · the composites render as posture + property, never a phantom rung ───
{
    const { modal, applyGateMode } = harness();
    applyGateMode('grounding-mode', 'annotate');
    check('annotate populates the OBSERVE radio',
          modal._radios['grounding-mode'].observe.checked === true);
    check('annotate checks the disclosure box',
          modal._boxes['grounding-annotate-disclose'].checked === true);
    check('annotate leaves retention unchecked',
          modal._boxes['grounding-review-retention'].checked === false);
}
{
    const { modal, applyGateMode } = harness();
    applyGateMode('grounding-mode', 'review');
    check('review populates the ENFORCE radio',
          modal._radios['grounding-mode'].enforce.checked === true);
    check('review checks the retention box',
          modal._boxes['grounding-review-retention'].checked === true);
    check('review leaves disclosure unchecked',
          modal._boxes['grounding-annotate-disclose'].checked === false);
}

// ── 3 · property boxes are live only under their own posture ────────────────
{
    const { modal, applyGateMode } = harness();
    applyGateMode('guardrail-mode', 'observe');
    check('under Observe: disclosure enabled, retention disabled',
          modal._boxes['guardrail-annotate-disclose'].disabled === false
          && modal._boxes['guardrail-review-retention'].disabled === true);
    applyGateMode('guardrail-mode', 'enforce');
    check('under Enforce: retention enabled, disclosure disabled',
          modal._boxes['guardrail-review-retention'].disabled === false
          && modal._boxes['guardrail-annotate-disclose'].disabled === true);
}

// ── 4 · unknown stored garbage falls to the default, never to a stronger mode
{
    const { applyGateMode, composeGateMode } = harness();
    applyGateMode('grounding-mode', 'bogus-mode');
    check("unknown mode falls back to 'observe' (never escalates)",
          composeGateMode('grounding-mode') === 'observe');
}

// ── 5 · a stale checked property on the OTHER posture never leaks into save ─
{
    const { modal, applyGateMode, composeGateMode } = harness();
    applyGateMode('grounding-mode', 'review');            // enforce + retention
    modal._radios['grounding-mode'].enforce.checked = false;
    modal._radios['grounding-mode'].observe.checked = true; // user flips to Observe
    check('retention checked + Observe selected saves observe, not review',
          composeGateMode('grounding-mode') === 'observe');
}

/* ── grainJudge — the optional grain_faithfulness confirmation ─────────────
   The F5-class contract, applied to a checkbox: populate must read ONLY an
   explicit true; save must write the key ONLY when checked AND carry it in the
   carryForward exclusion set so an UNCHECK genuinely clears a stored true
   (a stale carried-forward true would re-enable a judge the user turned off). */
{
    const src = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
    check('populate reads explicit true only',
          src.includes("gj.checked = h.grainJudge === true"));
    check('save writes grainJudge only when checked',
          /if \(gjEl && gjEl\.checked\) profileData\.honestyConfig\.grainJudge = true;/.test(src));
    const m = src.match(/carryForward\(profileData\.honestyConfig, 'honestyConfig',\s*new Set\(\[([^\]]*)\]\)\)/);
    check("carryForward exclusion set carries 'grainJudge' (uncheck clears)",
          !!m && m[1].includes("'grainJudge'") && m[1].includes("'mode'"),
          m ? m[1] : 'carryForward call not found');
}

/* ── the OBDA-READ tri-state (Option A/B/C — operator model 2026-08-17) ──────────
   Populate reads the STORED shape into one radio; save maps back; A clears a
   stored surface (dataSurface is in the carryForward exclusion set); an empty
   product list saves as A so the editor can never show a mode that does not
   act (the runtime treats an empty list as off). */
{
    const src = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
    check('populate derives the mode from the stored shape (empty list reads A)',
          src.includes("? (ds.exclusive ? 'exclusive' : 'mixed') : 'off'"));
    check('save: A or an empty list stores NO dataSurface',
          src.includes("(surfMode !== 'off' && surfProducts.length)"));
    check('save: B stores exclusive:true, C exclusive:false',
          src.includes("exclusive: surfMode === 'exclusive'"));
    check('advisory rides Option B only',
          src.includes("advisory: surfMode === 'exclusive' ? surfAdvisory : false"));
    check("dataSurface stays in the exclusion set (A genuinely clears)",
          /carryForward\(profileData\.authorityConfig[^)]*'dataSurface'/s.test(src));
    const html = readFileSync('templates/index.html', 'utf8');
    check('the card offers exactly the three options',
          ['off', 'exclusive', 'mixed'].every(v =>
              html.includes(`name="profile-obda-mode" id="profile-obda-mode-${v}" value="${v}"`)));
    check('Option A is the default-checked option',
          /id="profile-obda-mode-off" value="off"[^>]*checked/.test(html));
}

/* ── an empty UI is not an empty choice (the collections wipe) ──────────────
   One save that fired before the async collections render (or after a failed
   one in a stale tab) collected ZERO checkboxes and wrote
   autocompleteCollections: [] — silently disabling the profile's query
   suggestions, beside the dataSurface wipe from the same save. The contract:
   the render stamps completion; a save without the stamp carries the STORED
   selections forward; the stamp resets on every open so a previous open's
   stamp cannot vouch for this one. */
{
    const src = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
    check('the collections render stamps completion (both containers)',
          src.split("dataset.collectionsRendered = '1'").length >= 3);
    check('the stamps reset on every modal open',
          (src.match(/delete (plannerContainer|knowledgeContainer)\.dataset\.collectionsRendered/g) || []).length >= 2,
          'a stale stamp from a previous open would let an early save collect empties again');
    check('autocomplete: an unrendered UI carries the stored value forward',
          /if \(!_collectionsRendered && profile\)/.test(src)
          && src.includes('profile.autocompleteCollections'));
    check('ragCollections gets the same protection',
          src.includes('&& profile && Array.isArray(profile.ragCollections)'));
    check('a rendered-but-unchecked UI still saves [] (that IS a choice)',
          src.includes('=== \'1\'')
          && src.includes("A rendered-but-"),
          'carrying forward unconditionally would make deselection impossible');
}

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `\nALL PASS — ${pass} assertions (posture editor round-trip).`);
process.exit(fail ? 1 : 0);
