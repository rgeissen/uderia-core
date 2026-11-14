#!/usr/bin/env node
/**
 * Component accent consistency — ONE identity colour per component, read from one place.
 *
 * The manifest's `icon_accent` is the component's colour. The evidence-row frame in the chat,
 * the Workspace section, the split panel and the open control all read it (window.__componentAccents
 * / --sp-accent / --ans-accent). The defect (Oct 2026): the card BODY painted its own colour —
 * the flow card purple inside an amber frame, the dashboard in brand orange, action/OBDA/memory
 * from hardcoded constants — so a card wore two colours. Now the row sets --sp-accent to its own
 * accent and every renderer's identity colour reads --sp-accent with the manifest colour as the
 * fallback. No server, no DOM.
 */
import { readFileSync, readdirSync } from 'node:fs';
let pass = 0, fail = 0;
const ok = (l, c, d = '') => { if (c) pass++; else { fail++; console.log(`  FAIL: ${l}${d ? ` — ${d}` : ''}`); } };
const read = (f) => readFileSync(f, 'utf8');
const manifest = (id) => JSON.parse(read(`components/builtin/${id}/manifest.json`));

console.log('[1. the seam: inside an evidence row the renderer accent IS the row accent]');
const ans = read('static/css/answer.css');
ok('.ans-ev sets --sp-accent from --ans-accent', /\.ans-ev \{[\s\S]{0,700}--sp-accent: var\(--ans-accent\);/.test(ans));

console.log('\n[2. renderers with an accent constant read the live accent, manifest colour as fallback]');
for (const id of ['action', 'obda_read', 'obda_model', 'memory']) {
    const src = read(`components/builtin/${id}/renderer.js`);
    const acc = manifest(id).icon_accent.toLowerCase();
    const m = src.match(/const ACCENT = 'var\(--sp-accent, (#[0-9a-fA-F]{6})\)'/);
    ok(`${id}: ACCENT = var(--sp-accent, <manifest>)`, !!m && m[1].toLowerCase() === acc, m ? `fallback ${m[1]} vs manifest ${acc}` : 'no such constant');
}
ok('memory hands the open control a plain colour (a var would self-reference)', /accent: ACCENT_HEX,/.test(read('components/builtin/memory/renderer.js')));

console.log('\n[3. the flow inline card wears the flow accent, not a private purple]');
const flow = read('components/builtin/flow/renderer.js');
const inlineBlock = flow.slice(flow.indexOf('.flow-inline-card {'), flow.indexOf('.flow-inline-card-footer {'));
ok('no hardcoded purple in the inline-card rules', !/#a855f7|147,\s*51,\s*234/.test(inlineBlock), 'purple remains');
ok('icon, badge and type chips read --sp-accent', (inlineBlock.match(/var\(--sp-accent, #f59e0b\)/g) || []).length >= 4);
ok('the flow fallback is the manifest colour', manifest('flow').icon_accent.toLowerCase() === '#f59e0b');

console.log('\n[4. the dashboard identity comes from the accent, not the brand]');
const dash = read('components/builtin/dashboard/renderer.js');
ok('C.accent reads --sp-accent first', /accent:\s*'var\(--sp-accent, var\(--teradata-orange, #F15F22\)\)'/.test(dash));
ok('the KPI value and the primary button read the accent', /\.dash-kpi-value \{[\s\S]{0,120}color: var\(--sp-accent, #F15F22\)/.test(dash) && /background: var\(--sp-accent, #f15f22\)/.test(dash));

console.log('\n[5. the declared palette (manifests) — what the frames and panels show]');
const ids = readdirSync('components/builtin').filter(d => { try { manifest(d); return true; } catch { return false; } });
const byAccent = {};
for (const id of ids) { const a = (manifest(id).icon_accent || '').toLowerCase(); if (a) (byAccent[a] ||= []).push(id); }
const collisions = Object.entries(byAccent).filter(([, v]) => v.length > 1);
// recorded, not yet enforced: three teal, two indigo — the palette re-issue is a separate step
console.log(`  (info) accents shared by several components: ${collisions.map(([a, v]) => `${a} ← ${v.join(', ')}`).join(' · ') || 'none'}`);
ok('every builtin component declares an icon_accent', ids.every(id => /^#[0-9a-fA-F]{6}$/.test(manifest(id).icon_accent || '')));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
