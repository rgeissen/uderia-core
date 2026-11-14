#!/usr/bin/env node
/**
 * Resource-panel badge gate — ONE rule for every tab badge (resourceBadge.js).
 *
 * A badge reads enabled/available — what the agent may use over what its list holds —
 * and active/enabled/available on the one lane where active (every turn, unasked)
 * differs from enabled (on request): Skills. "Auto" is a mark, never a count. Before
 * this gate each tab had its own idea: Components
 * counted everything installed (the figure never moved on a toggle), Skills counted
 * auto-applied only and dropped the count at zero, Knowledge Graphs counted every
 * assigned graph, Resources/Connectors counted the list. The Workspace, not a list of
 * switches, reads items · sections (§5). No server, no DOM.
 */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ''}`); } };
const read = (f) => readFileSync(f, 'utf8');

console.log('[1. the rule itself]');
const { tabLabel, badgeCounts, badgeFigures, fillCategoryTab, workspaceLabel } = await import(pathToFileURL('static/js/handlers/resourceBadge.js').href);
ok('all on → both figures equal', tabLabel('Tools', [{ on: true }, { on: true }], i => i.on) === 'Tools (2/2)');
ok('some off → enabled over available', tabLabel('Tools', [{ on: true }, { on: false }, { on: true }], i => i.on) === 'Tools (2/3)');
ok('all off → (0/N), never a bare name', tabLabel('Skills', [{ on: false }], i => i.on) === 'Skills (0/1)');
ok('empty list → (0/0)', tabLabel('X', [], () => true) === 'X (0/0)');
ok('a non-array counts as empty', tabLabel('X', undefined, () => true) === 'X (0/0)');
ok('badgeCounts exposes active/enabled/available (active null without the predicate)',
   JSON.stringify(badgeCounts([1, 2, 3], n => n > 1)) === '{"active":null,"enabled":2,"available":3}');
ok('never an asterisk — the figures say it', !/\*/.test(tabLabel('Tools', [{ on: false }], i => i.on)));
ok('the label is what the rail parser reads: "(enabled/available)"', /^Tools \(\d+\/\d+\)$/.test(tabLabel('Tools', [{ on: false }], i => i.on)));
// The third figure: active (every turn, unasked) is a subset of enabled; Skills is the lane that has it.
const SK = [{ e: true, a: true }, { e: true, a: true }, { e: true, a: false }, { e: false, a: true }, { e: false, a: false }];
ok('with an active predicate → active/enabled/available', tabLabel('Skills', SK, s => s.e, s => s.a) === 'Skills (2/3/5)');
ok('an active entry that is not enabled is not counted active (active ⊂ enabled)', badgeCounts(SK, s => s.e, s => s.a).active === 2);
ok('badgeFigures is the same text the label carries', badgeFigures(SK, s => s.e, s => s.a) === '2/3/5' && badgeFigures(SK, s => s.e) === '3/5');
// A category tab inside Tools/Prompts carries its own figures in a muted span.
{
    const made = [];
    globalThis.document = { createElement: (t) => { const el = { tag: t, className: '', textContent: '', children: [], appendChild(c) { this.children.push(c); } }; made.push(el); return el; } };
    const tab = { textContent: '', children: [], appendChild(c) { this.children.push(c); } };
    fillCategoryTab(tab, 'base', [{ disabled: false }, { disabled: true }, { disabled: false }]);
    ok('fillCategoryTab: label, then "enabled/available" in .category-tab-fig', tab.textContent === 'base' && tab.children.length === 1 && tab.children[0].className === 'category-tab-fig' && tab.children[0].textContent === '2/3');
    const bare = { textContent: '', children: [], appendChild(c) { this.children.push(c); } };
    fillCategoryTab(bare, 'remote', null);
    ok('…and the label alone for a server that lists nothing yet', bare.textContent === 'remote' && bare.children.length === 0);
    delete globalThis.document;
}

console.log('\n[2. every tab badge goes through the rule — none hand-rolled]');
const SITES = [
    ['static/js/ui.js',                                   /tabButton\.textContent = tabLabel\(name, items, item => !item\.disabled\)/,            'Tools / Prompts / Resources / Connectors (updateResourceTabCounter)'],
    ['static/js/handlers/capabilitiesManagement.js',      /UI\.updateResourceTabCounter\(type, typeCapitalized\)/,                                 'the generic resource-panel branch'],
    ['static/js/eventHandlers.js',                        /UI\.updateResourceTabCounter\(type, typeCapitalized\)/,                                 'the duplicate loader in eventHandlers'],
    ['static/js/handlers/componentsPanelHandler.js',      /tabLabel\('Components', components,\s*c => c\.profile_enabled && !c\.globally_disabled,\s*c => c\.active && !c\.globally_disabled\)/, 'Components: active/enabled/available (active = enabled and acts every turn)'],
    ['static/js/handlers/skillsPanelHandler.js',          /tabLabel\('Skills', skills, s => s\.enabled && s\.available !== false, s => s\.active\)/, 'Skills: active/enabled/available (admin-disabled is not enabled)'],
    ['static/js/handlers/knowledgeGraphPanelHandler.js',  /tabLabel\('Knowledge Graphs', visibleGraphs,\s*kg => _getKgStateForProfile\(kg, activeProfileId\)\.isActive\)/, 'Knowledge Graphs: active for the profile'],
    ['static/js/handlers/extensionsPanelHandler.js',      /tabLabel\('Extensions', extensions, \(\) => true\)/,                                     'Extensions: every listed one is activated'],
];
for (const [file, re, label] of SITES) ok(`${label} uses tabLabel`, re.test(read(file)), file);
ok('capabilitiesManagement uses the shared counter at BOTH its render paths', (read('static/js/handlers/capabilitiesManagement.js').match(/UI\.updateResourceTabCounter\(type, typeCapitalized\)/g) || []).length === 2);

// A hand-rolled "(N)" from a raw list length on a resource tab is the drift this gate ends.
const RESOURCE_TABS = ['Tools', 'Prompts', 'Resources', 'Connectors', 'Components', 'Skills', 'Knowledge Graphs', 'Extensions'];
for (const [file] of SITES) {
    const src = read(file);
    const hand = [...src.matchAll(/textContent = `(Tools|Prompts|Resources|Connectors|Components|Skills|Knowledge Graphs|Extensions) \(\$\{[^}]*\.length\}\)/g)];
    // knowledgeGraphPanelHandler also drives the Intelligence tab (another surface, not the resource panel) — allowed there only.
    const allowed = file.endsWith('knowledgeGraphPanelHandler.js') ? hand.filter(m => /graphs\.length/.test(m[0]) && !/visibleGraphs/.test(m[0])).length : 0;
    ok(`${file}: no hand-rolled "(list.length)" resource-tab label`, hand.length === allowed, hand.map(m => m[0]).join(' | '));
}
// The special-profile Tools labels (knowledge collections / coordinated experts) read the same rule: all listed are in effect.
const cap = read('static/js/handlers/capabilitiesManagement.js');
ok('Focus / Ideate knowledge collections and Coordinate experts label through the rule',
   (cap.match(/UI\.tabLabel\('Tools', (knowledgeCollections|slaveEntries|collections), \(\) => true\)/g) || []).length === 3);
ok('resource tabs list is complete for this gate', RESOURCE_TABS.length === 8);

console.log('\n[3. the rail reads the label]');
const rail = read('static/js/handlers/resourcePanelHandler.js');
ok('the rail badge parses "(e/a)", "(act/e/a)" and the Workspace\'s "(items · sections)" from the tab text',
   /match\(\/\\\(\(\(\?:\\d\+\\\/\)\{1,2\}\\d\+\|\\d\+ · \\d\+\)\\\)\/\)/.test(rail));
ok('…shows the pair as written on the rail and in the panel header', /badge\.textContent = pair/.test(rail) && /panelCount\.textContent = pair/.test(rail));
ok('…and reads no asterisk any more', !/includes\('\*'\)/.test(rail) && !/hasModified/.test(rail));
ok('no dead marker colour in the CSS', !/rail-badge--modified/.test(read('static/css/main.css')));

console.log('\n[4. the three words everywhere: category tabs, the Skills card, the default agent]');
const cap2 = read('static/js/handlers/capabilitiesManagement.js'), evh = read('static/js/eventHandlers.js');
ok('every category tab inside Tools/Prompts carries its figures (both loaders)',
   /UI\.fillCategoryTab\(categoryTab, entry\.label, entry\.unclassified \? null : data\[category\]\)/.test(cap2)
   && /UI\.fillCategoryTab\(categoryTab, UI\.mcpCategoryLabel\(category\), data\[category\]\)/.test(evh)
   && /\.category-tab-fig \{/.test(read('static/css/main.css')));
const sk = read('static/js/handlers/skillsPanelHandler.js');
ok('the Skills card says ACTIVE · ENABLED · OFF — never AUTO as a state', />ACTIVE<\/span>/.test(sk) && />ENABLED<\/span>/.test(sk) && />OFF<\/span>/.test(sk) && !/>AUTO<\/span>/.test(sk));
ok('…and "auto" is a mark beside the state when the platform holds the switch', /if \(skill\.auto\) \{/.test(sk) && /skill-auto-mark[^>]*>auto</.test(sk));
ok('…ACTIVE requires enabled (active ⊂ enabled), and admin-disabled is not enabled', /const isEnabled = !!skill\.enabled && skill\.available !== false;/.test(sk) && /if \(skill\.active && isEnabled\)/.test(sk));
const mainjs = read('static/js/main.js');
ok('the default agent reads the per-agent endpoint — never the global /tools catalog',
   /const fetchId = profileId \|\| window\.configState\?\.defaultProfileId \|\| null;/.test(mainjs)
   && /fetch\(`\/api\/v1\/profiles\/\$\{fetchId\}\/resources`/.test(mainjs)
   && !/fetch\('\/tools'/.test(mainjs) && !/fetch\('\/prompts'/.test(mainjs) && !/fetch\('\/connectors'/.test(mainjs));

console.log('\n[5. the Workspace: the sum of what it holds first, then its sections — a dot, never a fraction]');
ok('workspaceLabel reads "Workspace (items · sections)"', workspaceLabel(23, 9) === 'Workspace (23 · 9)');
ok('…never a slash (a slash pair is always enabled/available)', !/\//.test(workspaceLabel(23, 9)));
const ws = read('static/js/handlers/workspacePanelHandler.js');
ok('the Workspace tab is written through workspaceLabel', /import \{ workspaceLabel \} from '\.\/resourceBadge\.js\?v=/.test(ws) && /tab\.textContent = workspaceLabel\(items, sections\)/.test(ws));
ok('the first figure is the sum of the sections\' OWN totals (Files lists 8 newest but counts all), the second the section count',
   /sections\.reduce\(\(n, s\) => n \+ _sectionTotal\(s\.reg, s\.items\), 0\)/.test(ws)
   && /_setTabState\(true, total, sections\.length\)/.test(ws)
   && (ws.match(/_sectionTotal\(/g) || []).length >= 3);
ok('the section header and the badge share that total rule', /const total = _sectionTotal\(reg, items\);/.test(ws));
ok('the rail badge and panel count show "items · sections" as written; no hand-rolled "(N)"', /const pair = `\$\{items\} · \$\{sections\}`;/.test(ws) && !/`Workspace \(\$\{count\}\)`/.test(ws));
ok('a load in flight keeps the previous figures; no section clears them', /if \(items == null\) \{/.test(ws) && /if \(!visible\) \{/.test(ws));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
