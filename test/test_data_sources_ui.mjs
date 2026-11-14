/**
 * PROP-006 — the profile editor's DATA SOURCES section, EXECUTED: the shipped module
 * (static/js/handlers/dataSources.js) driven over the section's REAL markup extracted from
 * templates/index.html, under the shared DOM stub. No server, no network (fetchers injected).
 *
 *   [1] the markup: nav item + section; the MCP select moved in with its id intact; the old
 *       controls are gone from the component row and AI Stack
 *   [2] single source: one row, NO Combining card, the summary says "Reads 1 source"
 *   [3] several sources: add a database, the Combining card appears, deployment state in words
 *   [4] rules: the administrator's rules locked and mapped onto the agent's own sources; the
 *       agent's rules added / removed; a rule naming a source the agent no longer reads kept
 *   [5] collect (the save): same contract as before the move — absent = off, carry-forward,
 *       a stored id this instance lacks stays visible; stale state is never read for another profile
 *   [6] honest states: navigator off, no MCP class, a failed load, a signed agent's note
 *   [7] theme: the module and the section markup use theme variables only
 *   [9] several MCP servers (phase 2): a single-server agent gets nothing added and pays no call;
 *       adding a server, its short name, its tools picked per server under Resources, the save
 *
 * Run: node test/test_data_sources_ui.mjs
 */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + String(detail).slice(0, 300) : ''}`); } };
const flush = () => new Promise(r => setImmediate(r));

const { body } = installDom();
const DS = await import(pathToFileURL('static/js/handlers/dataSources.js').href + '?t=' + Date.now());
const tpl = readFileSync('templates/index.html', 'utf8');
const ch = readFileSync('static/js/handlers/configurationHandler.js', 'utf8');
const modSrc = readFileSync('static/js/handlers/dataSources.js', 'utf8');
const craSrcEarly = readFileSync('static/js/handlers/combineRulesAdmin.js', 'utf8');

// ── the section's real markup ──
const secStart = tpl.indexOf('<div id="profile-section-data-sources"');
const secEnd = tpl.indexOf('<div id="profile-section-capabilities"');
const sectionHtml = tpl.slice(secStart, secEnd);

console.log('[1] markup: one section owns the sources');
ok('[1] a Data sources nav item exists', /data-profile-section="data-sources"/.test(tpl));
ok('[1] …placed after AI Stack and before Capabilities',
   tpl.indexOf('data-profile-section="ai-stack"') < tpl.indexOf('data-profile-section="data-sources"')
   && tpl.indexOf('data-profile-section="data-sources"') < tpl.indexOf('data-profile-section="capabilities"'));
ok('[1] the section exists with its body host', secStart > 0 && sectionHtml.includes('id="profile-data-sources-body"'));
ok('[1] the MCP select moved INTO the section with its id intact (population + save unchanged)',
   sectionHtml.includes('id="profile-modal-mcp-server"') && sectionHtml.includes('id="profile-field-mcp-server"')
   && (tpl.match(/id="profile-modal-mcp-server"/g) || []).length === 1);
const aiStack = tpl.slice(tpl.indexOf('<div id="profile-section-ai-stack"'), secStart);
ok('[1] AI Stack no longer carries the MCP field or claims it', !aiStack.includes('profile-modal-mcp-server') && !/MCP server and/.test(aiStack));
ok('[1] the component row no longer renders the Navigator source select or query row',
   !ch.includes('comp-dbnav-source-select') && !ch.includes('comp-dbnav-query-row'));
ok('[1] …it points to Data sources instead', ch.includes('comp-dbnav-open-sources') && ch.includes('data-profile-section="data-sources"'));
ok('[1] the editor surface contract still addresses the moved MCP field by its id',
   /mcpServerField:\s*\{\s*sel: '#profile-field-mcp-server'/.test(ch));

// ── a modal built from the real section + the controls that live elsewhere ──
const VIEW = {
    mcp_servers: [{ source_id: 'mcp:s1', id: 's1', kind: 'mcp', name: 'Teradata MCP', endpoint: 'mcp:uderia.com:8888/mcp' }],
    connections: [
        { source_id: 'conn:hr', id: 'hr', kind: 'database', name: 'HR warehouse', driver: 'teradata', endpoint: 'db:teradata://hr-host:1025/hr', queryable: true },
        { source_id: 'conn:crm', id: 'crm', kind: 'database', name: 'CRM', driver: 'postgresql', endpoint: 'db:postgresql://crm:5432/crm', queryable: true },
        { source_id: 'conn:files', id: 'files', kind: 'database', name: 'Files', driver: 'jdbc', endpoint: null, queryable: false },
    ],
    admin_rules: [{ endpoints: ['db:teradata://hr-host:1025/hr', 'db:postgresql://crm:5432/crm'], reason: 'HR data never meets CRM data',
                    yours: { 'db:teradata://hr-host:1025/hr': ['conn:hr'], 'db:postgresql://crm:5432/crm': ['conn:crm'] }, applies: true }],
    combine: { available: true, reason: '', incomplete_inputs: 'refuse' },
};
function buildModal({ navOn = true, mcp = 's1', usesMcp = true } = {}) {
    const modal = new El('div', { id: 'profile-modal' });
    modal.innerHTML = sectionHtml + `
        <button class="profile-nav-item" data-profile-section="capabilities"></button>
        <input type="checkbox" class="comp-toggle" data-component-id="database_navigator">`;
    body.appendChild(modal);
    const sel = modal.querySelector('#profile-modal-mcp-server'); sel.value = mcp;
    const nt = modal.querySelector('.comp-toggle'); nt.checked = navOn;
    if (!usesMcp) modal.querySelector('#profile-field-mcp-server').style.display = 'none';
    return modal;
}
async function open(profile, opts = {}, fetchers = {}) {
    const modal = buildModal(opts);
    const st = DS.init({ modal, profile,
        fetchView: fetchers.fetchView || (async () => JSON.parse(JSON.stringify(VIEW))),
        fetchTrust: fetchers.fetchTrust || (async () => ({ state: 'unsigned' })),
        testConnection: fetchers.testConnection || (async (id) => ({ ok: true, text: `Connected to ${id}.` })) });
    await flush(); await flush();
    return { modal, st, host: modal.querySelector('#profile-data-sources-body') };
}
// the DOM stub matches one attribute per selector — `[a="x"][b="y"]` is resolved by filtering
function find(root, sel) {
    const m = sel.match(/^(.*?)\[data-ds-id="([^"]+)"\]$/);
    if (!m) return root.querySelector(sel);
    return root.querySelectorAll(m[1] || '*').find(e => e.dataset && e.dataset.dsId === m[2]) || null;
}
const click = (host, sel) => { const b = find(host, sel); if (!b) throw new Error('no ' + sel); b.dispatchEvent({ type: 'click', bubbles: true, target: b }); };
const change = (host, el) => el.dispatchEvent({ type: 'change', bubbles: true, target: el });

// ── [2] single source ──
console.log('[2] single source: nothing to combine, nothing added');
let P = { id: 'p1', mcpServerId: 's1', componentConfig: { database_navigator: { enabled: true } }, classification_results: { last_classified: '2026-09-01' } };
let { host, st, modal } = await open(P);
ok('[2] the summary says one source', host.querySelector('#ds-summary').innerHTML.includes('Reads 1 source'), host.querySelector('#ds-summary').innerHTML);
ok('[2] the MCP row shows its endpoint and classification', host.querySelector('#ds-mcp-meta').innerHTML.includes('uderia.com:8888/mcp')
   && host.querySelector('#ds-mcp-meta').innerHTML.includes('tools classified'));
ok('[2] NO Combining card for a single source', host.querySelector('#ds-combining-host').innerHTML.trim() === '');
ok('[2] queries off by default, said in words', host.querySelector('#ds-databases').innerHTML.includes('Queries are off'));
ok('[2] no trust note on an unsigned agent', host.querySelector('#ds-trust-note').innerHTML === '');
ok('[2] an untouched save writes no query key and no rules key (baseline)',
   DS.collectQuery(st, undefined) === null && DS.collectRules(st, undefined) === undefined && DS.collectSourceId(st, undefined) === '');

// ── [3] several sources ──
console.log('[3] several sources: add, test, default, combining appears');
const qt = host.querySelector('[data-ds-act="toggle-query"]'); qt.checked = true; change(host, qt);
ok('[3] queries on with none chosen: the default database is shown as a row', host.querySelector('#ds-databases').innerHTML.includes('The default database'));
ok('[3] …it counts as a second source', host.querySelector('#ds-summary').innerHTML.includes('Reads 2 sources'));
click(host, '[data-ds-act="open-picker"]');
const picks = host.querySelectorAll('.ds-pick');
ok('[3] the picker lists every connection', picks.length === 3, picks.length);
const files = picks.find(b => b.dataset.dsId === 'conn:files');
ok('[3] a connection the agent cannot query is shown and says why, not hidden', files && files.hasAttribute('disabled') && files.innerHTML.includes('cannot be queried directly'));
const filt = host.querySelector('[data-ds-act="picker-filter"]'); filt.value = 'crm';
filt.dispatchEvent({ type: 'input', bubbles: true, target: filt });
ok('[3] search narrows the list, the box stays', host.querySelectorAll('.ds-pick').length === 1 && host.querySelector('[data-ds-act="picker-filter"]') === filt);
click(host, '.ds-pick[data-ds-id="conn:crm"]');
click(host, '[data-ds-act="open-picker"]');
click(host, '.ds-pick[data-ds-id="conn:hr"]');
click(host, '[data-ds-act="open-picker"]');
click(host, '.ds-pick[data-ds-id="conn:files"]');
ok('[3] a disabled pick adds nothing', !st.query.sources.includes('conn:files'));
ok('[3] …and leaves the picker open (nothing happened to close it for)', !!host.querySelector('.ds-picker'));
ok('[3] an added connection reads "added" in the picker', find(host, '.ds-pick[data-ds-id="conn:crm"]').innerHTML.includes('added'));
click(host, '[data-ds-act="open-picker"]');
ok('[3] rows for each chosen database with name and endpoint',
   host.querySelector('#ds-databases').innerHTML.includes('CRM') && host.querySelector('#ds-databases').innerHTML.includes('db:postgresql://crm:5432/crm'));
ok('[3] the summary counts three sources and says combining is available',
   /Reads 3 sources · may combine results/.test(host.querySelector('#ds-summary').innerHTML), host.querySelector('#ds-summary').innerHTML);
click(host, '[data-ds-act="make-default"][data-ds-id="conn:hr"]');
ok('[3] "Make default" marks the row and sets the catalog source', st.sourceId === 'conn:hr'
   && host.querySelector('[data-ds-source="conn:hr"]').innerHTML.includes('is-default'));
click(host, '[data-ds-act="test"][data-ds-id="conn:crm"]'); await flush(); await flush();
ok('[3] Test reports in words on the row', host.querySelector('[data-ds-source="conn:crm"]').innerHTML.includes('Connected to conn:crm.'));
const comb = host.querySelector('#ds-combining-host').innerHTML;
ok('[3] the Combining card appears with several sources', comb.includes('Combining results'));
ok('[3] …the deployment state in words, code not the model', comb.includes('run by code, never by the model'));
ok('[3] …and the incomplete-input policy', comb.includes('<b>refused</b> as an input'));

// ── [4] rules ──
console.log('[4] rules: administrator locked, agent adds');
ok('[4] the administrator\'s rule is shown with the agent\'s own source names, locked',
   comb.includes('HR warehouse') && comb.includes('set by your administrator') && comb.includes('HR data never meets CRM data') && comb.includes('is-admin'));
ok('[4] the admin rule cannot be removed here', !/is-admin[\s\S]*?data-ds-act="remove-rule"/.test(comb.split('this agent\'s rule')[0]));
click(host, '[data-ds-act="builder-open"]');
ok('[4] a new rule cannot be added with fewer than two sources picked', host.querySelector('[data-ds-act="builder-save"]').hasAttribute('disabled'));
click(host, '[data-ds-act="builder-toggle"][data-ds-id="mcp:s1"]');
click(host, '[data-ds-act="builder-toggle"][data-ds-id="conn:crm"]');
const reason = host.querySelector('[data-ds-act="builder-reason"]'); reason.value = 'tools never see CRM rows';
reason.dispatchEvent({ type: 'input', bubbles: true, target: reason });
click(host, '[data-ds-act="builder-save"]');
ok('[4] the rule is added, sorted, with its reason', JSON.stringify(st.rules) === JSON.stringify([{ sources: ['conn:crm', 'mcp:s1'], reason: 'tools never see CRM rows' }]), JSON.stringify(st.rules));
click(host, '[data-ds-act="builder-open"]');
click(host, '[data-ds-act="builder-toggle"][data-ds-id="conn:crm"]');
click(host, '[data-ds-act="builder-toggle"][data-ds-id="mcp:s1"]');
click(host, '[data-ds-act="builder-save"]');
ok('[4] the same rule twice is added once', st.rules.length === 1);
ok('[4] the summary counts the rules that limit combining', /2 rules limit combining/.test(host.querySelector('#ds-summary').innerHTML), host.querySelector('#ds-summary').innerHTML);
ok('[4] collectRules sends the rules', JSON.stringify(DS.collectRules(st, undefined)) === JSON.stringify(st.rules));
click(host, '[data-ds-act="remove"][data-ds-id="conn:crm"]');
ok('[4] removing a source keeps a rule that names it, marked (never silently dropped)',
   st.rules.length === 1 && host.querySelector('#ds-combining-host').innerHTML.includes('not among this agent'));
click(host, '[data-ds-act="remove-rule"]');
ok('[4] removing the agent rule clears it; a stored set is cleared with []', DS.collectRules(st, [{ sources: ['a', 'b'] }]).length === 0);

// ── [5] collect contract (the pre-move gate's properties, now on the section) ──
console.log('[5] collect: the same contract as before the move');
({ host, st } = await open({ id: 'p2', mcpServerId: 's1', componentConfig: { database_navigator: { enabled: true, query: { enabled: true, sources: ['conn:crm', 'conn:gone'] } } } }));
ok('[5] a stored ON block is ON, and a save keeps the stored sources',
   JSON.stringify(DS.collectQuery(st, undefined)) === JSON.stringify({ enabled: true, sources: ['conn:crm', 'conn:gone'] }));
ok('[5] a stored id this instance lacks stays visible and says so',
   host.querySelector('[data-ds-source="conn:gone"]') && host.querySelector('[data-ds-source="conn:gone"]').innerHTML.includes('not on this instance'));
const qt2 = host.querySelector('[data-ds-act="toggle-query"]'); qt2.checked = false; change(host, qt2);
ok('[5] switching OFF keeps a chosen list, disabled (nothing signed changes)',
   JSON.stringify(DS.collectQuery(st, undefined)) === JSON.stringify({ enabled: false, sources: ['conn:crm', 'conn:gone'] }));
click(host, '[data-ds-act="toggle-query"]');   // no-op click; toggles are change-driven
st.query.sources = [];
ok('[5] switching OFF with nothing chosen removes the block', DS.collectQuery(st, undefined) === null);
ok('[5] with no state the stored values are carried forward',
   JSON.stringify(DS.collectQuery(null, { enabled: true, sources: ['conn:x'] })) === JSON.stringify({ enabled: true, sources: ['conn:x'] })
   && DS.collectSourceId(null, 'conn:x') === 'conn:x' && DS.collectRules(null, [{ sources: ['a', 'b'] }]) === undefined);
ok('[5] the state of an earlier modal is never read for another profile', DS.stateFor({ id: 'other' }) === null && DS.stateFor({ id: 'p2' }) === st);
// edits before the connections load are kept (the view arrives later)
let release;
({ host, st } = await open({ id: 'p3', mcpServerId: 's1', componentConfig: { database_navigator: { enabled: true } } }, {},
    { fetchView: () => new Promise(r => { release = r; }) }));
const qt3 = host.querySelector('[data-ds-act="toggle-query"]'); qt3.checked = true; change(host, qt3);
ok('[5] an edit before the connections load is saved', JSON.stringify(DS.collectQuery(st, undefined)) === JSON.stringify({ enabled: true, sources: [] }));
release(JSON.parse(JSON.stringify(VIEW))); await flush(); await flush();
ok('[5] …and survives the load', st.query.enabled === true);

// ── [6] honest states ──
console.log('[6] honest states');
({ host, st } = await open({ id: 'p4', mcpServerId: 's1', componentConfig: {} }, { navOn: false }));
ok('[6] navigator off: said, with the way to turn it on', host.querySelector('#ds-databases').innerHTML.includes('Database Navigator is off')
   && host.querySelector('[data-ds-act="open-capabilities"]'));
let navClicked = false; st.modal.querySelector('.profile-nav-item[data-profile-section="capabilities"]').click = () => { navClicked = true; };
click(host, '[data-ds-act="open-capabilities"]');
ok('[6] …the button opens Capabilities', navClicked);
const nt = st.modal.querySelector('.comp-toggle'); nt.checked = true; change(st.modal, nt);
ok('[6] turning the Navigator on elsewhere updates the section', host.querySelector('#ds-databases').innerHTML.includes('Default database'));
({ host, st } = await open({ id: 'p5', componentConfig: {} }, { usesMcp: false }));
ok('[6] a class that calls no MCP tools: the Tools card says so instead of standing empty',
   host.querySelector('#ds-mcp-absent').innerHTML.includes('does not call MCP tools'));
({ host, st } = await open({ id: 'p6', mcpServerId: 's1', componentConfig: { database_navigator: { enabled: true } } }, {},
    { fetchView: async () => { throw new Error('down'); } }));
ok('[6] a failed load is said, and stored choices are kept', host.querySelector('#ds-databases').innerHTML.includes('could not be loaded'));
({ host, st } = await open({ id: 'p7', mcpServerId: 's1', componentConfig: {} }, {}, { fetchTrust: async () => ({ state: 'trusted' }) }));
ok('[6] a signed agent: the re-attest note', host.querySelector('#ds-trust-note').innerHTML.includes('re-attest'));
({ host, st } = await open({ id: 'p8', mcpServerId: 's1', componentConfig: { database_navigator: { enabled: true, query: { enabled: true, sources: ['conn:hr', 'conn:crm'] } } } }, {},
    { fetchView: async () => ({ ...JSON.parse(JSON.stringify(VIEW)), combine: { available: false, reason: 'combining results is off in this deployment (TDA_COMPUTE_SQL)' } }) }));
ok('[6] combining off in the deployment: said, rules still shown', host.querySelector('#ds-combining-host').innerHTML.includes('Off in this deployment')
   && host.querySelector('#ds-combining-host').innerHTML.includes('TDA_COMPUTE_SQL'));
ok('[6] a changed MCP select updates the section', (() => {
    const sel = st.modal.querySelector('#profile-modal-mcp-server'); sel.value = 'zz'; change(st.modal, sel);
    return host.querySelector('#ds-mcp-meta').innerHTML.includes('not on this instance');
})());

// ── [7] theme ──
console.log('[7] theme: variables only');
const TAILWIND_COLOR = /\b(?:text|bg|border|ring|from|to|via)-(?:gray|slate|zinc|neutral|stone|red|rose|amber|yellow|emerald|green|teal|cyan|sky|blue|indigo|violet|purple|pink|orange|lime|fuchsia)-\d{2,3}\b|\[#[0-9a-fA-F]{3,8}\]/;
const bare = (txt) => txt.replace(/var\([^)]*\)/g, '').match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/);
ok('[7] the module: no Tailwind colour class', !TAILWIND_COLOR.test(modSrc), (modSrc.match(TAILWIND_COLOR) || [])[0]);
ok('[7] the module: no bare colour outside a var()', !bare(modSrc), (bare(modSrc) || [])[0]);
ok('[7] the section markup: no Tailwind colour class', !TAILWIND_COLOR.test(sectionHtml), (sectionHtml.match(TAILWIND_COLOR) || [])[0]);
ok('[7] the section markup: no bare colour', !bare(sectionHtml), (bare(sectionHtml) || [])[0]);
const navBtn = tpl.slice(tpl.indexOf('data-profile-section="data-sources"') - 120, tpl.indexOf('<span class="text-sm font-medium">Data sources</span>'));
ok('[7] the nav icon is monochrome (currentColor)', navBtn.includes('stroke="currentColor"') && !bare(navBtn));
const cmpLink = ch.slice(ch.indexOf('comp-dbnav-sources-link') - 20, ch.indexOf('comp-dbnav-sources-link') + 700);
ok('[7] the component row pointer uses theme variables only', !TAILWIND_COLOR.test(cmpLink) && !bare(cmpLink));
// the platform's global `[class*="card"]` rule paints pill backgrounds onto any class
// containing "card" (it once did so to the compute card's title) — the new UI avoids the word
const _cls = (txt) => [...txt.matchAll(/class="([^"]*)"/g)].flatMap(m => m[1].split(/\s+/)).concat([...txt.matchAll(/^\.([\w-]+)/gm)].map(m => m[1]));
ok('[7] no class name contains "card" (the global card rule would restyle it)',
   ![..._cls(modSrc), ..._cls(sectionHtml), ..._cls(craSrcEarly)].some(c => /card/.test(c)),
   [..._cls(modSrc), ..._cls(sectionHtml), ..._cls(craSrcEarly)].filter(c => /card/.test(c)).join(','));
ok('[7] the section\'s labels beat the editor\'s hard-coded label colour (scoped by id, theme variable)',
   /#profile-modal #profile-data-sources-body label \{ color:var\(--text-muted\)/.test(modSrc));
ok('[7] icons are monochrome SVG (currentColor), no emoji', /stroke="currentColor"/.test(modSrc) && !/[\u{1F300}-\u{1FAFF}☀-➿]/u.test(modSrc));

// ── [8] the administrator's combining rules (Administration → Trust → Enforcement) ──
console.log('[8] administrator rules: endpoints, locked meaning, saved through the Trust panel');
const craSrc = readFileSync('static/js/handlers/combineRulesAdmin.js', 'utf8');
new Function(craSrc)();
const CRA = globalThis.window.CombineRulesAdmin;
ok('[8] the editor is published for adminManager', !!(CRA && CRA.mount && CRA.rules && CRA.snapshot));
const ENDPOINTS = [
    { endpoint: 'db:teradata://hr-host:1025/hr', kind: 'database', names: ['HR warehouse', 'hr'], users: 3 },
    { endpoint: 'db:postgresql://crm:5432/crm', kind: 'database', names: ['CRM'], users: 1 },
    { endpoint: 'mcp:uderia.com:8888/mcp', kind: 'mcp', names: ['Teradata MCP'], users: 5 },
];
const aHost = new El('div', { id: 'combine-rules-admin-host' }); body.appendChild(aHost);
CRA.mount(aHost, [{ endpoints: ['db:postgresql://crm:5432/crm', 'db:teradata://hr-host:1025/hr'], reason: 'HR never meets CRM' },
                  { endpoints: ['db:gone://x/y', 'mcp:uderia.com:8888/mcp'], reason: '' }], async () => ENDPOINTS);
await flush(); await flush();
const aHtml = aHost.innerHTML;
ok('[8] a stored rule reads as a sentence with the names users gave the systems', aHtml.includes('Never combine') && aHtml.includes('HR warehouse, hr') && aHtml.includes('CRM'));
ok('[8] …with the endpoint and how many users reach it', aHtml.includes('db:teradata://hr-host:1025/hr · 3 users'));
ok('[8] a rule naming a system no user reaches today is kept and says so', aHtml.includes('no user reaches this system today'));
ok('[8] the section says rules hold for every user and agents cannot loosen them', aHtml.includes('every user who reaches these systems') && aHtml.includes('can never loosen'));
const aClick = (sel) => { const b = find(aHost, sel); if (!b) throw new Error('no ' + sel); b.dispatchEvent({ type: 'click', bubbles: true, target: b }); };
const before = CRA.snapshot();
aClick('[data-cra-act="open"]');
ok('[8] the builder lists every system users reach', aHost.querySelectorAll('.cra-pick').length === 3);
ok('[8] a rule needs two systems', aHost.querySelector('[data-cra-act="save"]').hasAttribute('disabled'));
const pickEp = (ep) => { const b = aHost.querySelectorAll('.cra-pick').find(x => x.dataset.craEp === ep); b.dispatchEvent({ type: 'click', bubbles: true, target: b }); };
pickEp('mcp:uderia.com:8888/mcp'); pickEp('db:postgresql://crm:5432/crm');
const why = aHost.querySelector('[data-cra-act="reason"]'); why.value = 'tools never see CRM';
why.dispatchEvent({ type: 'input', bubbles: true, target: why });
aClick('[data-cra-act="save"]');
ok('[8] the rule is added in the app-config shape', JSON.stringify(CRA.rules()[2]) === JSON.stringify({ endpoints: ['db:postgresql://crm:5432/crm', 'mcp:uderia.com:8888/mcp'], reason: 'tools never see CRM' }), JSON.stringify(CRA.rules()));
ok('[8] the snapshot moves (the panel marks itself unsaved)', CRA.snapshot() !== before);
aClick('[data-cra-act="remove"]');
ok('[8] a rule can be removed', CRA.rules().length === 2);
const fHost = new El('div'); body.appendChild(fHost);
CRA.mount(fHost, [], async () => { throw new Error('down'); }); await flush(); await flush();
find(fHost, '[data-cra-act="open"]').dispatchEvent({ type: 'click', bubbles: true, target: find(fHost, '[data-cra-act="open"]') });
ok('[8] a failed listing is said; nothing is lost', fHost.innerHTML.includes('could not be listed'));
const am = readFileSync('static/js/adminManager.js', 'utf8');
ok('[8] adminManager mounts the rules before the unsaved-changes baseline is taken',
   am.indexOf("CombineRulesAdmin.mount(document.getElementById('combine-rules-admin-host')") > 0
   && am.indexOf("CombineRulesAdmin.mount(") < am.indexOf('this.resetTrustDirty();', am.indexOf("CombineRulesAdmin.mount(")));
ok('[8] …snapshots them by value', /base\.combine_isolation = window\.CombineRulesAdmin/.test(am));
ok('[8] …and saves them with the Trust panel\'s one Save', /config\.combine_isolation = window\.CombineRulesAdmin\.rules\(\)/.test(am));
ok('[8] the host sits in the Enforcement container and the script loads before adminManager',
   tpl.indexOf('id="combine-rules-admin-host"') > tpl.indexOf('<h3 class="text-lg font-bold text-white">Enforcement</h3>')
   && tpl.indexOf('id="combine-rules-admin-host"') < tpl.indexOf('<h3 class="text-lg font-bold text-white">What reaches you</h3>')
   && tpl.indexOf('js/handlers/combineRulesAdmin.js') < tpl.indexOf("js/adminManager.js"));
ok('[8] theme: no Tailwind colour class, no bare colour', !TAILWIND_COLOR.test(craSrc) && !bare(craSrc), (craSrc.match(TAILWIND_COLOR) || bare(craSrc) || [])[0]);

// ── [9] several MCP servers (PROP-006 phase 2) ──
console.log('[9] several MCP servers: nothing added for one, per-server picking for several');
const resStart = tpl.indexOf('<div id="profile-content-mcp-resources"');
const resEnd = tpl.indexOf('<!-- Card: Intelligence Collections -->');
const resHtml = tpl.slice(resStart, resEnd);
ok('[9] the Resources card carries the two phase-2 hosts, around the primary\'s unchanged lists',
   resHtml.indexOf('id="profile-modal-primary-mcp-label"') > 0
   && resHtml.indexOf('id="profile-modal-primary-mcp-label"') < resHtml.indexOf('id="profile-modal-tools"')
   && resHtml.indexOf('id="profile-modal-prompts"') < resHtml.indexOf('id="profile-modal-additional-mcp"'));
ok('[9] the Tools card carries the additional-servers host', sectionHtml.includes('id="ds-mcp-additional"') && sectionHtml.includes('id="ds-tools-sub"'));
const VIEW2 = { ...JSON.parse(JSON.stringify(VIEW)), mcp_servers: [
    ...VIEW.mcp_servers,
    { source_id: 'mcp:s2', id: 's2', kind: 'mcp', name: 'CRM MCP', endpoint: 'mcp:crm-host:9000/mcp' },
    { source_id: 'mcp:s3', id: 's3', kind: 'mcp', name: '9 Lakehouse!', endpoint: 'mcp:lake:9100/mcp' } ] };
const CATALOG = { s2: { tools: ['base_readQuery', 'base_tableList', 'crm_accounts'], prompts: ['base_query'] },
                  s3: { tools: ['base_readQuery'], prompts: [] } };
async function openMulti(profile, { prompts = true, view = VIEW2, fetchResources } = {}) {
    const modal = buildModal();
    const card = new El('div'); card.innerHTML = resHtml; modal.appendChild(card);
    if (!prompts) modal.querySelector('#profile-modal-prompts-section').style.display = 'none';
    const calls = [];
    const st = DS.init({ modal, profile,
        fetchView: async () => JSON.parse(JSON.stringify(view)),
        fetchTrust: async () => ({ state: 'unsigned' }),
        fetchResources: fetchResources || (async (id) => { calls.push(id); return JSON.parse(JSON.stringify(CATALOG[id])); }) });
    await flush(); await flush(); await flush();
    return { modal, st, host: modal.querySelector('#profile-data-sources-body'), calls,
             res: modal.querySelector('#profile-modal-additional-mcp'), label: modal.querySelector('#profile-modal-primary-mcp-label') };
}

// one server, and the user has no other: nothing at all is added
let M = await openMulti({ id: 'm1', mcpServerId: 's1', componentConfig: {} }, { view: VIEW });
ok('[9] one server, no other to add: the additional host is empty', M.host.querySelector('#ds-mcp-additional').innerHTML === '');
ok('[9] …the Resources card gains nothing', M.res.innerHTML === '' && M.label.innerHTML === '');
ok('[9] …no catalogue is fetched', M.calls.length === 0);
ok('[9] …the Tools sub-line is unchanged', M.host.querySelector('#ds-tools-sub').textContent === 'the MCP server the agent calls');
ok('[9] …a save sends no additionalMcpServers key', DS.collectAdditional(M.st, undefined) === undefined);

// one server, another exists: only the add control, still no fetch and no key
M = await openMulti({ id: 'm2', mcpServerId: 's1', componentConfig: {} });
ok('[9] one server with another available: only the add control', /Add another MCP server/.test(M.host.querySelector('#ds-mcp-additional').innerHTML)
   && !M.host.querySelector('#ds-mcp-additional').innerHTML.includes('data-ds-source'));
ok('[9] …still no fetch, no Resources change, no key', M.calls.length === 0 && M.res.innerHTML === '' && DS.collectAdditional(M.st, undefined) === undefined);
ok('[9] …the summary still reads one source', M.host.querySelector('#ds-summary').innerHTML.includes('Reads 1 source'));

click(M.host, '[data-ds-act="open-add-mcp"]');
const mpicks = M.host.querySelectorAll('[data-ds-act="add-mcp"]');
ok('[9] the picker lists every server and says why one cannot be added', mpicks.length === 3
   && find(M.host, '[data-ds-act="add-mcp"][data-ds-id="s1"]').hasAttribute('disabled')
   && find(M.host, '[data-ds-act="add-mcp"][data-ds-id="s1"]').innerHTML.includes('primary'));
click(M.host, '[data-ds-act="add-mcp"][data-ds-id="s2"]');
await flush(); await flush();
ok('[9] adding a server suggests a valid short name from its name', JSON.stringify(M.st.additional) === JSON.stringify([{ id: 's2', alias: 'crm', tools: ['*'], prompts: ['*'] }]), JSON.stringify(M.st.additional));
ok('[9] …its catalogue is fetched once, for that server', JSON.stringify(M.calls) === '["s2"]', M.calls);
const rowHtml = find(M.host, '[data-ds-source="mcp:s2"]') ? find(M.host, '[data-ds-source="mcp:s2"]').innerHTML : M.host.querySelector('#ds-mcp-additional').innerHTML;
ok('[9] the row shows the name, endpoint, short name and the qualified form', rowHtml.includes('CRM MCP') && rowHtml.includes('crm-host:9000')
   && rowHtml.includes('value="crm"') && rowHtml.includes('crm__'));
ok('[9] …and the selection in words', rowHtml.includes('3 of 3 tools') && rowHtml.includes('1 of 1 prompts'), rowHtml);
ok('[9] the summary counts two sources; the Combining card appears', M.host.querySelector('#ds-summary').innerHTML.includes('Reads 2 sources')
   && M.host.querySelector('#ds-combining-host').innerHTML.includes('Combining results'));
ok('[9] the Tools sub-line counts the servers', M.host.querySelector('#ds-tools-sub').textContent.includes('servers the agent calls — 2'));
ok('[9] Resources: one group for the server, its tools listed by their own names', M.res.innerHTML.includes('CRM MCP')
   && M.res.querySelectorAll('input[data-ds-act="res-pick"]').length === 4 && M.res.innerHTML.includes('value="crm_accounts"'));
ok('[9] …and the primary\'s lists are labelled, keeping their names', M.label.innerHTML.includes('Teradata MCP') && M.label.innerHTML.includes('keep their names'));

const pickBox = (kind, name) => M.res.querySelectorAll('input[data-ds-act="res-pick"]').find(e => e.dataset.dsKind === kind && e.value === name);
let box = pickBox('tools', 'crm_accounts'); box.checked = false; change(M.res, box);
ok('[9] unticking a tool keeps a literal list in catalogue order', JSON.stringify(M.st.additional[0].tools) === '["base_readQuery","base_tableList"]', JSON.stringify(M.st.additional[0].tools));
ok('[9] …the count follows', M.res.innerHTML.includes('2 of 3 tools'));
box = pickBox('tools', 'crm_accounts'); box.checked = true; change(M.res, box);
ok('[9] ticking every tool again collapses to all ("*")', JSON.stringify(M.st.additional[0].tools) === '["*"]');
const bulk = (act, kind) => { const b = M.res.querySelectorAll(`[data-ds-act="${act}"]`).find(e => e.dataset.dsKind === kind); b.dispatchEvent({ type: 'click', bubbles: true, target: b }); };
bulk('res-none', 'prompts');
ok('[9] None clears a column', JSON.stringify(M.st.additional[0].prompts) === '[]');
bulk('res-all', 'prompts');
ok('[9] All selects the column', JSON.stringify(M.st.additional[0].prompts) === '["*"]');
ok('[9] the save sends the list', JSON.stringify(DS.collectAdditional(M.st, undefined)) === JSON.stringify([{ id: 's2', alias: 'crm', tools: ['*'], prompts: ['*'] }]));

// the short name
let inp = M.host.querySelectorAll('input[data-ds-act="alias"]')[0];
inp.value = 'Bad Name'; inp.dispatchEvent({ type: 'input', bubbles: true, target: inp });
ok('[9] a bad short name is flagged while typing, without a re-render (the caret stays)',
   M.host.querySelectorAll('input[data-ds-act="alias"]')[0] === inp && inp.getAttribute('aria-invalid') === 'true');
change(M.host, inp);
ok('[9] …and said in words once committed', M.host.querySelector('#ds-mcp-additional').innerHTML.includes('Lower-case letters and digits'));
ok('[9] …the save is refused before it is sent', Object.keys(DS.additionalErrors(M.st)).join() === 's2');
inp = M.host.querySelectorAll('input[data-ds-act="alias"]')[0]; inp.value = 'crm'; change(M.host, inp);
ok('[9] a good short name clears the problem', !Object.keys(DS.additionalErrors(M.st)).length && M.host.querySelector('#ds-mcp-additional').innerHTML.includes('crm__'));
click(M.host, '[data-ds-act="open-add-mcp"]'); click(M.host, '[data-ds-act="add-mcp"][data-ds-id="s3"]'); await flush(); await flush();
ok('[9] a name that starts with a digit still yields a valid short name', M.st.additional[1].alias === 'lakehouse', M.st.additional[1].alias);
ok('[9] a server with no prompts says so', M.res.innerHTML.includes('No prompts on this server'));
inp = M.host.querySelectorAll('input[data-ds-act="alias"]')[1]; inp.value = 'crm'; change(M.host, inp);
ok('[9] the same short name twice is refused on the second', DS.additionalErrors(M.st).s3 === 'Another server already uses this short name.');
inp = M.host.querySelectorAll('input[data-ds-act="alias"]')[1]; inp.value = 'lake'; change(M.host, inp);

// Pick tools → Resources
let resNavClicked = false;
const rnav = new El('button', { class: 'profile-nav-item', 'data-profile-section': 'resources' }); rnav.click = () => { resNavClicked = true; }; M.modal.appendChild(rnav);
click(M.host, '[data-ds-act="pick-resources"][data-ds-id="s3"]');
ok('[9] "Pick tools" opens Resources and unfolds the MCP card', resNavClicked && M.modal.querySelector('#profile-content-mcp-resources').querySelector('details').open === true);

// remove, and the promotion rule
click(M.host, '[data-ds-act="remove-mcp"][data-ds-id="s3"]');
ok('[9] removing a server drops it and its group', M.st.additional.length === 1 && !M.res.innerHTML.includes('9 Lakehouse'));
const msel = M.modal.querySelector('#profile-modal-mcp-server'); msel.value = 's2'; change(M.modal, msel);
ok('[9] making an additional server the first one removes it from the list, and says so',
   M.st.additional.length === 0 && M.host.querySelector('#ds-mcp-additional').innerHTML.includes('removed from the additional servers'));
ok('[9] …a save then removes a stored list with []', JSON.stringify(DS.collectAdditional(M.st, [{ id: 's2', alias: 'crm', tools: ['*'], prompts: ['*'] }])) === '[]');
ok('[9] …and sends nothing when nothing was stored', DS.collectAdditional(M.st, undefined) === undefined);

// stored servers on open; a failed listing; prompts not used by the class
M = await openMulti({ id: 'm3', mcpServerId: 's1', componentConfig: {}, additionalMcpServers: [{ id: 's2', alias: 'crm', tools: ['base_readQuery'], prompts: [] }] },
    { prompts: false, fetchResources: async () => { throw new Error('connection refused'); } });
ok('[9] a stored server opens with its stored selection', JSON.stringify(M.st.additional) === JSON.stringify([{ id: 's2', alias: 'crm', tools: ['base_readQuery'], prompts: [] }]));
ok('[9] a failed listing is said; the stored choice is kept and saved as it was',
   M.res.innerHTML.includes('Could not be listed') && M.res.innerHTML.includes('connection refused')
   && JSON.stringify(DS.collectAdditional(M.st, M.st.profile.additionalMcpServers)) === JSON.stringify([{ id: 's2', alias: 'crm', tools: ['base_readQuery'], prompts: [] }]));
ok('[9] a class that cannot use prompts gets no prompts column or count',
   !M.res.innerHTML.includes('>Prompts') && !M.host.querySelector('#ds-mcp-additional').innerHTML.includes('prompts'));
ok('[9] an agent that no longer calls MCP tools removes a stored list', JSON.stringify(DS.collectAdditional(M.st, M.st.profile.additionalMcpServers, { usesMcp: false })) === '[]');
ok('[9] before the section rendered nothing is sent', DS.collectAdditional(null, [{ id: 's2', alias: 'crm' }]) === undefined);
M = await openMulti({ id: 'm4', mcpServerId: 's1', componentConfig: {}, additionalMcpServers: [{ id: 'gone', alias: 'old', tools: ['*'], prompts: ['*'] }] });
ok('[9] a stored server this instance lacks stays visible and says so', M.host.querySelector('#ds-mcp-additional').innerHTML.includes('not on this instance'));

// pure rules
ok('[9] suggestAlias: the distinctive words, slugged, at most 12, never a taken name',
   DS.suggestAlias('Teradata MCP - dbc', [], 'Teradata MCP - fitness_manager') === 'dbc'
   && DS.suggestAlias('Teradata MCP - nibrs_source_agent', [], 'Teradata MCP') === 'nibrs_source'
   && DS.suggestAlias('Teradata MCP Production', []) === 'teradata_pro'
   && DS.suggestAlias('Teradata MCP', [], 'Teradata MCP') === 'teradata'
   && DS.suggestAlias('CRM', ['crm']) === 'crm2' && DS.suggestAlias('!!!', []) === 'srv',
   [DS.suggestAlias('Teradata MCP - dbc', [], 'Teradata MCP - fitness_manager'), DS.suggestAlias('Teradata MCP - nibrs_source_agent', [], 'Teradata MCP'), DS.suggestAlias('Teradata MCP Production', []), DS.suggestAlias('Teradata MCP', [], 'Teradata MCP')].join(','));
ok('[9] the short-name rule matches the server\'s (tool_identity.ALIAS_RE)',
   DS.validAlias('crm') && DS.validAlias('a_b2') && !DS.validAlias('a__b') && !DS.validAlias('_a') && !DS.validAlias('2a') && !DS.validAlias('abcdefghijklm')
   && readFileSync('src/trusted_data_agent/core/tool_identity.py', 'utf8').includes("^[a-z](?:[a-z0-9]|_(?=[a-z0-9]))*$"));

// the editor's save
ok('[9] the editor passes a per-server catalogue fetcher', /DataSources\.init\(\{ modal, profile,[\s\S]{0,400}fetchResources: async \(serverId\)/.test(ch));
ok('[9] the save refuses a short-name problem before sending, then sends the list',
   /DataSources\.additionalErrors\(dsState\)[\s\S]{0,500}return;[\s\S]{0,300}DataSources\.collectAdditional\(dsState, profile\?\.additionalMcpServers, \{ usesMcp \}\)/.test(ch)
   && ch.includes('profileData.additionalMcpServers = extra'));
ok('[9] the primary\'s own tools and prompts are still read from their own lists only',
   ch.includes("const selectedTools = Array.from(toolsContainer.querySelectorAll('input:checked'))"));
ok('[9] theme: the new Resources hosts are bare (every style comes from the module\'s theme variables)',
   resHtml.includes('<div id="profile-modal-primary-mcp-label"></div>') && resHtml.includes('<div id="profile-modal-additional-mcp"></div>'));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions) — the Data sources section, executed`);
process.exit(fail ? 1 : 0);
