#!/usr/bin/env node
/**
 * Two UI contracts that regressed silently before (Oct 2026):
 *
 *  1. Text never sits on a border. The global card rule painted ANY element whose class
 *     merely contained "card" (labels, headers, every card-btn, a Tailwind `bg-[var(--card-bg)]`)
 *     as a padding-less bordered pill; the answer's own invisible wrapper was repainted by an
 *     !important light-theme rule; ~110 pills used 4–6px side padding.
 *  2. The Workspace follows a component switched on/off (resource panel, profile editor,
 *     component manager) and a turn that may have created an instance — without a reload.
 * No browser, no server: source-level checks of the rules that make both hold.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const read = (p) => readFileSync(join(ROOT, p), 'utf8');
let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

console.log('\n[1. text keeps its distance from a border]');
const themes = read('static/css/themes.css');
ok('the global card rule paints card CONTAINERS only (class `card` or ending in -card), never any class containing "card"',
   /\.card, \[class\$="-card"\], \[class\*="-card "\] \{/.test(themes) && !/^\.card, \[class\*="card"\] \{/m.test(themes)
   && !/\[data-theme="legacy"\] \[class\*="card"\]/.test(themes));
ok('the answer\'s own wrapper (.response-card.summary-card) is invisible in every theme, including the !important light rule',
   /\[data-theme="light"\] \.response-card\.summary-card,[\s\S]{0,200}background: none !important;[\s\S]{0,200}box-shadow: none !important;/.test(themes));
const files = [];
(function walk(d) { for (const f of readdirSync(join(ROOT, d))) { const p = join(d, f); const st = statSync(join(ROOT, p));
  if (st.isDirectory()) { if (!/node_modules|vendor|(^|\/)dist(\/|$)/.test(p)) walk(p); /* dist/: local build output, git-ignored, never served */ } else if (/\.(js|html)$/.test(f)) files.push(p); } })('static/js');
files.push('templates/index.html');
const tight = files.filter(p => /\bpx-(?:1|1\.5) py-(?:0|0\.5) rounded/.test(read(p)));
ok('no pill uses 4–6px side padding (px-1 / px-1.5 with rounded)', tight.length === 0, tight.join(', '));
const flow = read('components/builtin/flow/renderer.js');
ok('flow node agent tags are padded pills, clear of the status tick', (flow.match(/padding: '1px 8px', borderRadius: 999/g) || []).length >= 2 && /marginRight: '16px', flexShrink: 0, fontSize: '0\.6rem'/.test(flow));
ok('topology chips and the keyboard hint have room', /\.ftp-chip \{ font-size: 0\.64rem; padding: 2px 10px;/.test(flow) && /\.ftp-kbd \{[^}]*padding: 1px 7px;/.test(flow));

console.log('\n[2. the Workspace follows changes without a reload]');
const ws = read('static/js/handlers/workspacePanelHandler.js');
// Since Oct 2026 the Workspace has no private listener: the resource panel's ONE subscriber
// (resourceSync.js, wired in main.js) re-runs the per-profile load on `uderia:components-changed`,
// which reloads the Workspace and invalidates + primes the enablement gate (test_resource_panel_live_sync.mjs).
const mainSrc = read('static/js/main.js');
ok('the Workspace reloads on uderia:components-changed through the one subscriber (no private listener)',
   !/addEventListener\('uderia:components-changed'/.test(ws)
   && /wireResourcePanelSync\(/.test(mainSrc) && /loadWorkspacePanel\(\);/.test(mainSrc)
   && /ComponentGate\.invalidate\(profileId\);\s*window\.ComponentGate\.prime\(profileId\);/.test(mainSrc));
ok('…and after every turn (an agent may have created a flow, dashboard, canvas or memory)', /UderiaBus\.on\('uderia:turn-complete', \(\) => _refreshIfShown\(null\)\)/.test(ws));
ok('a reload asked for while one runs is run after it, never dropped',
   /if \(_loading\) \{ _again = true; return; \}/.test(ws) && /if \(_again\) \{ _again = false; loadWorkspacePanel\(\); \}/.test(ws));
ok('a toggle for another agent does not reload this agent\'s Workspace', /if \(profileId && current && profileId !== current\) return;/.test(ws));
for (const [file, label] of [['static/js/handlers/componentsPanelHandler.js', 'the resource panel toggle'],
                             ['static/js/handlers/configurationHandler.js', 'a profile save that carries componentConfig'],
                             ['static/js/handlers/componentHandler.js', 'the component manager\'s per-agent switch']]) {
  ok(`${label} announces uderia:components-changed`, /UderiaBus\.emit\('uderia:components-changed', \{ profileId \}\)/.test(read(file)));
}
ok('the profile-save dispatch fires only when componentConfig is part of the update',
   /hasOwnProperty\.call\(updates, 'componentConfig'\)\) \{\s*UderiaBus\.emit\('uderia:components-changed'/.test(read('static/js/handlers/configurationHandler.js')));

console.log('\n[3. every component with a panel is in the Workspace, or excluded by name]');
const regBlock = ws.slice(ws.indexOf('const WORKSPACE_REGISTRY = {'), ws.indexOf('\n};', ws.indexOf('const WORKSPACE_REGISTRY = {')));
const listed = new Set([...regBlock.matchAll(/^    (\w+): \{/gm)].map(m => m[1]));
// A builtin component is left out of the Workspace only for a stated reason.
const EXCLUDED = {
  chart: 'its output lives in the answer; nothing persistent to open',
  context_window: 'has its own Context panel in the resource rail',
  compute: 'headless: no panel',
  find_tools: 'headless: no panel',
  platform: 'headless: no panel',
  skill_library: 'headless: no panel',
};
const builtin = readdirSync('components/builtin').filter(d => { try { return statSync(join('components/builtin', d, 'manifest.json')).isFile(); } catch (_) { return false; } })
  .map(d => JSON.parse(read(join('components/builtin', d, 'manifest.json'))).component_id);
const missing = builtin.filter(id => !listed.has(id) && !(id in EXCLUDED));
ok('every builtin component has a Workspace section or a named exclusion', missing.length === 0, missing.join(', '));
ok('no exclusion names a component that has a section', Object.keys(EXCLUDED).every(id => !listed.has(id)));
ok('EXPLAIN Plan lists the panel\'s plans from the page (no server endpoint) and opens one',
   /explain_plan: \{[\s\S]*?list: \(\) => import\('\/api\/v1\/components\/explain_plan\/renderer'\)\.then\(m => m\.panelPlans\(\)\)[\s\S]*?m\.showPanelPlan\(it\.i\)/.test(regBlock));
ok('the loader reads a local list instead of fetching when an entry declares one',
   /if \(typeof reg\.list === 'function'\) \{[\s\S]{0,200}items = \(await reg\.list\(scope\)\) \|\| \[\];/.test(ws));
ok('a new plan refreshes the Workspace (the explain_plan component announces on the bus; the Workspace follows every component)', /UderiaBus\.announce\('explain_plan', 'created'\)/.test(read('components/builtin/explain_plan/renderer.js')) && /UderiaBus\.onComponent\('\*'/.test(ws));

console.log('\n[5. evidence-row header: the icons keep their box under a long title]');
const ans = read('static/css/answer.css');
ok('the chevron and the component icon never shrink (flex: none, 14px box) — the scheduler row once squeezed its chevron to 9px',
   /\.ans-ev-s > \.ans-ic, \.ans-src-s > \.ans-ic \{ flex: none; width: 14px; height: 14px; \}/.test(ans));

console.log('\n[6. one fact, one place: a card inside an evidence row never restates the row summary]');
const flowR = read('components/builtin/flow/renderer.js');
ok('the inline flow card syncs the enclosing row summary (name, nodes, edges) whenever its stats refresh',
   /function _syncRowSummary\(wrapper, flow, visibleNodes, edgeCount\)/.test(flowR) && /_flowCardStats\(definition\);\n  _syncRowSummary\(wrapper, flow, visibleNodes, edgeCount\);/.test(flowR));
ok('the row text is rewritten only when it changes (no churn)', /if \(next !== txt\) t\.textContent = next;/.test(flowR));
ok('the row states nodes AND edges, so the card need not', /· \{m\} edge\{'' if m == 1 else 's'\}/.test(read('components/builtin/flow/handler.py')));
ok('ONE hider: answer.css hides [data-ans-restates] inside a row body', /\.ans-ev-b \[data-ans-restates\] \{ display: none !important; \}/.test(read('static/css/answer.css')));
const MARKS = [
    ['flow',               /class="flow-inline-card-header" data-ans-restates/,                       1, 'the card header (icon · name · size)'],
    ['scheduler',          /data-ans-restates/g,                                                      2, 'the toast title and its schedule line'],
    ['canvas',             /data-ans-restates/g,                                                      2, 'the card title and the compact title'],
    ['knowledge_graph',    /data-ans-restates/g,                                                      2, 'the compact title and the nodes · edges badge'],
    ['dashboard',          /data-ans-restates/g,                                                      2, 'the name and the New badge'],
    ['memory',             /<div data-ans-restates style=/,                                           1, 'the Agent Memory header line'],
    ['database_navigator', /class="dbnav-title" data-ans-restates/,                                   1, 'the db.name title'],
    ['explain_plan',       /data-ans-restates/g,                                                      2, 'the who · steps span and the total-time span'],
];
for (const [id, re, n, what] of MARKS) {
    const src = read(`components/builtin/${id}/renderer.js`);
    const hits = (src.match(re) || []).length;
    ok(`${id}: marks ${what}`, hits >= n, `${hits} mark(s)`);
}
ok('chart: the plot drops its own title inside a row (the row states it)', /_host\.closest\('details\.ans-ev'\)[\s\S]{0,80}title: undefined/.test(read('components/builtin/chart/renderer.js')));
ok('no renderer keeps a private hide rule for the same purpose (one mechanism)',
   !/\.ans-ev-b \.flow-inline-card-header \{ display: none/.test(flowR) && !/\.ans-ev-b \.sched-toast-title/.test(read('components/builtin/scheduler/renderer.js')));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
