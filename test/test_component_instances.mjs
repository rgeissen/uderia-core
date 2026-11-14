#!/usr/bin/env node
/**
 * Whose a component's things are — ONE declaration, read by every surface that says it.
 *
 * A component manifest's `instances` ('user' · 'agent' · 'page') states whose the things it
 * lists are. Two surfaces read it: the component card's pill (Components overview and detail)
 * and the Workspace, whose sections list FOR the agent only when the field says 'agent'.
 * Before, the split lived only in each Workspace section's own code (a `profile_id` here, a
 * filter there), so nothing showed it and nothing kept a new section honest.
 *
 * This gate asserts the declaration is complete, the same enum everywhere, and that the
 * Workspace's scoping cannot drift from it. No server, no browser.
 *
 * Run: node test/test_component_instances.mjs
 */
import fs from 'node:fs';
import vm from 'node:vm';

let pass = 0, fail = 0;
const check = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');

const ENUM = ['user', 'agent', 'page', 'none'];
const W = read('static/js/handlers/workspacePanelHandler.js');
const C = read('static/js/handlers/componentHandler.js');
const MODELS = read('src/trusted_data_agent/components/models.py');
const SCHEMA = JSON.parse(read('components/schemas/component_manifest_v1.schema.json'));

// ── the registry, evaluated (its values are functions and literals; nothing runs at definition) ──
const start = W.indexOf('const WORKSPACE_REGISTRY = {');
const end = W.indexOf('\n};\n', start);
check('the Workspace registry is found', start > 0 && end > start);
const REG = vm.runInNewContext('(' + W.slice(start + 'const WORKSPACE_REGISTRY = '.length, end + 2) + ')', {});
const keys = Object.keys(REG);

// ── manifests ──
const manifests = {};
for (const d of fs.readdirSync('components/builtin')) {
    const p = `components/builtin/${d}/manifest.json`;
    if (fs.existsSync(p)) manifests[JSON.parse(read(p)).component_id] = JSON.parse(read(p));
}

// ── one enum, everywhere ──
check('the manifest schema declares `instances` with the enum user · agent · page · none',
    JSON.stringify((SCHEMA.properties.instances || {}).enum) === JSON.stringify(ENUM));
check('…and requires it of every component', (SCHEMA.required || []).includes('instances'));
check('the API keeps only a declared value (models.INSTANCE_SCOPES is the same enum)',
    /INSTANCE_SCOPES = \("user", "agent", "page", "none"\)/.test(MODELS)
    && /"instances": self\.manifest\.get\("instances"\) if self\.manifest\.get\("instances"\) in INSTANCE_SCOPES else None/.test(MODELS));
const pillStart = C.indexOf('const INSTANCE_PILLS = {');
const pills = vm.runInNewContext('(' + C.slice(pillStart + 'const INSTANCE_PILLS = '.length, C.indexOf('\n};\n', pillStart) + 2) + ')', {});
check('the card has a pill for every value and no other', JSON.stringify(Object.keys(pills)) === JSON.stringify(ENUM));
check('every pill says it in words (a label and a sentence)', ENUM.every(k => pills[k].label && pills[k].tip && pills[k].tip.length > 20));

// ── complete: every component declares; what keeps something is exactly what the Workspace lists ──
for (const id of Object.keys(manifests)) {
    check(`"${id}" declares instances`, ENUM.includes(manifests[id].instances), `got ${manifests[id].instances}`);
}
const keeping = Object.keys(manifests).filter(id => manifests[id].instances && manifests[id].instances !== 'none').sort();
check('a component that keeps something has a Workspace section, and a stateless one has none (one set, two readers)',
    JSON.stringify(keeping) === JSON.stringify([...keys].sort()),
    `keeping-but-no-section: ${keeping.filter(id => !keys.includes(id)).join(', ') || '-'} · section-but-none: ${keys.filter(id => !keeping.includes(id)).join(', ') || '-'}`);

// ── the Workspace's scoping follows the field ──
check('the loader derives the list scope from comp.instances, nothing else',
    /const scope = comp\.instances === 'agent' \? profileId : null;/.test(W));
check('…and hands every list hook the scope, never the raw profile id',
    /reg\.list\(scope\)/.test(W) && /reg\.listUrl\(scope\)/.test(W) && /reg\.filterItems\(items, scope\)/.test(W)
    && !/reg\.list\(profileId\)|reg\.listUrl\(profileId\)|reg\.filterItems\(items, profileId\)/.test(W));
for (const k of keys) {
    const r = REG[k], v = (manifests[k] || {}).instances;
    const scoped = typeof r.filterItems === 'function'
        || (typeof r.listUrl === 'function' && r.listUrl('P-1') !== r.listUrl(null))
        || (typeof r.list === 'function' && r.list.length >= 1);
    if (v === 'agent') check(`"${k}" is agent-scoped, so its list can narrow to the agent`, scoped);
    else check(`"${k}" is the person's own, so its list does not narrow to an agent`, !scoped);
}

// ── the pill is shown where the component is described ──
check('the card and the detail header both carry the pill', (C.match(/\$\{_instancePill\(comp\)\}/g) || []).length === 2);
check('the pill is theme-aware (CSS variables, no Tailwind colour class) and its tip is a data-tip',
    /_instancePill[\s\S]{0,700}var\(--text-primary\)[\s\S]{0,200}var\(--border-primary\)/.test(C)
    && /comp-instances-pill u-tip/.test(C) && /data-tip="\$\{p\.tip\}"/.test(C));

console.log(fail ? `\n${pass} passed, ${fail} FAILED` : `ALL PASS — ${pass} assertions (whose a component's things are: one declaration)`);
process.exit(fail ? 1 : 0);
