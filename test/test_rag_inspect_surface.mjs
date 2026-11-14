#!/usr/bin/env node
/**
 * Planner-repository inspect surface — the case must show the query it was seeded from.
 *
 * Two defects this pins:
 *  1. "Selected Case Details" showed case id, task, session, provider, tokens — every
 *     opaque identifier and not the one human-readable fact that says WHICH case this
 *     is: the user query it was seeded from.
 *  2. The User Query column carried a NATIVE `title`. The platform has a theme tooltip
 *     (`u-tip` + uTip.js: viewport-clamped, wraps, themed); a native title renders one
 *     unwrapped OS-styled line, which is unreadable for a query of any length.
 *
 * NB the 200-character cut visible in these queries is NOT a rendering limit: the
 * policing pack's seeds carry 200-char text because the product's own
 * `Query_Cookbook.use_case` column truncated it before extraction. The UI shows
 * everything it is given — asserted below by there being no length cap in the path.
 *
 * Source-level assertions (the renderer is embedded in a large DOM-bound function);
 * each is mutation-tested.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', 'static', 'js', 'ui.js'), 'utf8');

let pass = 0; const failures = [];
const check = (name, cond, detail = '') => {
    if (cond) pass++; else failures.push(name + (detail ? ` — ${detail}` : ''));
};

// ── [1] the User Query column uses the platform tooltip, never a native title ──
const cellRe = /<td[^>]*>\$\{escapeHtml\(r\.user_query \|\| ''\)\}<\/td>/g;
const cells = src.match(/<td[^>]*user_query[^>]*>\$\{escapeHtml\(r\.user_query \|\| ''\)\}<\/td>/g) || [];
check('[1] both row builders render the query cell', cells.length === 2, cells.length);
check('[1] every query cell uses the theme tooltip',
    cells.every(c => c.includes('u-tip') && c.includes('data-tip=')), cells);
check('[1] no query cell falls back to a native title',
    cells.every(c => !/\btitle=/.test(c)), cells);

// ── [2] the details panel leads with the query the case was seeded from ───────
check('[2] the panel resolves the seeded query', src.includes('const seededQuery ='));
check('[2] it prefers the case document, falling back to the row',
    /const seededQuery = \(caseData\.intent && caseData\.intent\.user_query\)\s*\|\|\s*rowRecord\.user_query \|\| ''/.test(src));
check('[2] absence is stated, never blank',
    src.includes('not recorded on this case'));
check('[2] the query wraps rather than being clipped to one line',
    /seededQuery[\s\S]{0,400}whitespace-pre-wrap/.test(src));
check('[2] it is escaped', /\$\{escapeHtml\(seededQuery\)\}/.test(src));

// Scoped to the panel's OWN template: `queryHtml` and the "User Query:" label each
// occur elsewhere in ui.js, so a bare source search is satisfied by code this gate
// did not put there — it passed a mutation that deleted the block outright.
// `queryHtml` and the label each occur elsewhere in ui.js, and there are several
// `ragSelectedCaseMetadata.innerHTML` sites (one only writes "Loading…"), so the
// region is anchored on the one unique marker this feature introduced. A bare
// source search passed a mutation that deleted the block outright.
const anchorAt = src.indexOf('const seededQuery =');
const region = anchorAt < 0 ? '' : src.slice(anchorAt, anchorAt + 2000);
check('[2] the region resolves', anchorAt > -1);
check('[2] it labels the block User Query', region.includes('User Query:</span>'));
check('[2] the block is written INTO the details panel',
    /DOM\.ragSelectedCaseMetadata\.innerHTML = `\s*\n\s*\$\{queryHtml\}/.test(region));

// ── [3] nothing in the display path caps the query length ────────────────────
// The UI must never be the reason a query looks truncated (the stored 200-char
// text comes from the source product, and a display cap would hide that).
const around = src.slice(Math.max(0, src.indexOf('const seededQuery =') - 500),
                         src.indexOf('const seededQuery =') + 900);
check('[3] no substring/slice cap around the query rendering',
    !/seededQuery[^\n]{0,80}\.(slice|substring|substr)\(/.test(around), 'a display cap would misattribute source truncation');

if (failures.length) {
    console.error(`\n${failures.length} FAILED:`);
    for (const f of failures) console.error('  FAIL: ' + f);
    console.error(`\n${pass} passed, ${failures.length} FAILED`);
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions (planner inspect surface).`);
