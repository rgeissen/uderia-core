/**
 * Gate — a config panel must never present an UNSAVED value as if it were saved.
 *
 * The defect this pins (found by an operator, Aug 2026): `switchConfigSubtab` only
 * toggles `hidden` and no panel re-reads the server, so within a visit to App Config an
 * edited-but-unsaved control keeps showing its new value across sub-tab switches — on
 * the Trust tab that means it can display "Guardrails on" while the platform holds off.
 * Trust was additionally the ONLY config panel with no dirty tracking at all, so its
 * Save button gave no signal either.
 *
 * Scope, measured rather than assumed: re-entering App Config DOES re-run
 * `loadAppConfig()` and reset every panel to server state. An earlier draft of this gate
 * claimed the stale value survived a full view change too — that was an artifact of a
 * test that clicked a text-matched element instead of the real `[data-tab]` tab, so the
 * loader never fired. The real, and sufficient, defect is within-visit.
 *
 * Source-level gate (no browser, no server): asserts the mechanism exists and is wired.
 * The behavioural proof is the live browser run recorded in the fix commit.
 *
 * Run:  node test/test_config_unsaved_marker.mjs
 */
import fs from 'fs';

let PASS = 0, FAIL = 0;
const fails = [];
const check = (name, cond, detail = '') => {
    if (cond) PASS++; else { FAIL++; fails.push(`${name}${detail ? '  — ' + detail : ''}`); }
};

const js = fs.readFileSync('static/js/adminManager.js', 'utf8');
const css = fs.readFileSync('static/css/main.css', 'utf8');
const html = fs.readFileSync('templates/index.html', 'utf8');

// ── 1. Trust has the dirty tracking every other panel has (the parity gap) ──
check('Trust has a snapshot', /getTrustSnapshot\s*\(\)\s*\{/.test(js));
check('Trust has a dirty check', /checkTrustDirty\s*\(\)\s*\{/.test(js));
check('Trust re-baselines after save', /resetTrustDirty\s*\(\)\s*\{/.test(js));
// Scope this to the checkTrustDirty BODY: `resetTrustDirty` calls the same helper, so a
// whole-file search passes even with the call deleted from the dirty check — leaving a
// Save button that never enables on a change (this mutation escaped the first gate).
const trustBody0 = js.slice(js.indexOf('    checkTrustDirty() {'),
                            js.indexOf('    resetTrustDirty() {'));
check('the Trust Save button is driven by the DIRTY CHECK itself',
    /_updateSaveButton\('save-agent-eval-btn',\s*this\.trustDirty\)/.test(trustBody0));
check('the unsaved marker is driven by the same check',
    /_setUnsavedMarker\('trust',\s*this\.trustDirty\)/.test(trustBody0));

// every control the Trust save WRITES must be in the snapshot it READS, or a change
// to it would leave the panel looking clean while differing from the server
for (const id of ['agent-eval-online-enabled', 'agent-eval-include-judges',
                  'agent-eval-grounding-enabled', 'agent-eval-guardrails-enabled',
                  'agent-eval-judge-rate', 'obda-policy-read', 'obda-policy-model', 'obda-policy-act',
                  'trust-behavior-enabled', 'trust-behavior-breaker-mode',
                  'quality-band-healthy', 'quality-band-warn']) {
    const snap = js.slice(js.indexOf('getTrustSnapshot'), js.indexOf('checkTrustDirty'));
    check(`snapshot covers ${id}`, snap.includes(id));
}
const snap = js.slice(js.indexOf('getTrustSnapshot'), js.indexOf('checkTrustDirty'));
check('snapshot covers the threshold sliders (div handles — no change event)',
    /thresholdSliders\.getThresholds\(\)/.test(snap));
check('snapshot covers per-station reporting', /agent-eval-report-/.test(snap));
// NB locate the BODY, not the first mention: `resetTrustDirty` is *called* in the save
// handler far earlier in the file, so slicing between first-mentions runs backwards and
// yields an empty string — an assertion that then passes or fails for the wrong reason.
const trustBody = js.slice(js.indexOf('    checkTrustDirty() {'),
                           js.indexOf('    resetTrustDirty() {'));
check('the checkTrustDirty body was located', trustBody.length > 50 && trustBody.length < 2000,
    `len=${trustBody.length}`);
check('staged module bindings count as dirty (the same Save applies them)',
    /AssuranceBindings[\s\S]{0,200}isDirty/.test(trustBody));

// ── 2. the watcher cannot miss a control ──
const watch = js.slice(js.indexOf('wireTrustDirtyWatch'), js.indexOf('wireTrustDirtyWatch') + 900);
check('the watcher is delegated on the panel (covers dynamic controls)',
    /config-panel-trust/.test(watch));
check('the watcher includes pointerup (the sliders are drag handles, not inputs)',
    /pointerup/.test(watch));
check('the watcher is idempotent (no listener pile-up on reload)',
    /dirtyWatch/.test(watch));

// ── 3. the systemic half — every panel marks itself, not just Trust ──
const marked = [...js.matchAll(/_setUnsavedMarker\('([a-z-]+)'/g)].map(m => m[1]);
for (const tab of ['trust', 'features', 'security', 'ai-knowledge', 'user-interface', 'operations']) {
    check(`${tab} marks unsaved state`, marked.includes(tab));
    check(`${tab} is a real sub-tab`, html.includes(`data-config-tab="${tab}"`));
}
// a marker driven by an undefined flag is a marker that never fires — the exact
// bug a mechanical camelCase rename introduced here once (aIKnowledgeDirty)
check('no marker is driven by a non-existent dirty flag',
    !/_setUnsavedMarker\([^)]*this\.(aI|uI|sYstem)[A-Za-z]*Dirty/.test(js));

// ── 4. the marker is legible without colour, and says what it means ──
check('the tab marker carries the WORD "unsaved" (never colour alone)',
    /\.config-subtab--unsaved::after[\s\S]{0,200}content:\s*"unsaved"/.test(css));
check('the panel banner states the consequence, not just the state',
    /\.config-panel--unsaved::before[\s\S]{0,300}until you save/.test(css));

for (const f of fails) console.log('  FAIL:', f);
console.log(FAIL
    ? `\nFAIL — ${FAIL} of ${PASS + FAIL} assertions failed.`
    : `\nALL PASS — ${PASS} assertions (a config tab admits it is unsaved).`);
process.exit(FAIL ? 1 : 0);
