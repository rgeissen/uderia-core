#!/usr/bin/env node
/**
 * Platform roles, visible — the gate for the one renderer (platformRolesView.js) and the three
 * places it is read: the Platform component's Roles tab and card line (componentHandler.js),
 * the agent editor's role picker (configurationHandler.js), and the playbook skill's card in
 * Components → Skill Library → Libraries ("taught to").
 *
 * A planned role never reads as a grant; a change a person always approves is marked; what a
 * role lists but cannot call says why; every view reads ONE endpoint through ONE module. No
 * server, no DOM.
 */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ''}`); } };
const read = (f) => readFileSync(f, 'utf8');

globalThis.localStorage = { getItem: () => 'tok' };
let fetches = [];
let answer = { ok: true, body: { roles: [] } };
globalThis.fetch = async (url, opts) => { fetches.push({ url, opts }); return { ok: answer.ok, status: answer.ok ? 200 : 500, json: async () => answer.body }; };

const V = await import(pathToFileURL('static/js/handlers/platformRolesView.js').href);

console.log('[1. counts]');
const active = { id: 'agent-builder', title: 'Agent Builder', status: 'active', effects: ['read', 'write'],
                 counts: { operations: 46, callable: 36, writes: 9, playbooks: 3, agents: 1 } };
ok('an active role says what it reaches, may change, teaches and who holds it',
   V.countsLine(active) === 'reaches 36 operations · may submit 9 changes · teaches 3 playbooks · held by 1 agent', V.countsLine(active));
ok('a role that writes nothing does not mention changes',
   V.countsLine({ ...active, counts: { callable: 1, writes: 0, playbooks: 1, agents: 2 } }) === 'reaches 1 operation · teaches 1 playbook · held by 2 agents');
const planned = { id: 'publisher', status: 'planned', needs: ['agent-packs', 'marketplace'], counts: { callable: 9, writes: 2, playbooks: 0 } };
ok('a planned role never reads as a grant — it says it grants nothing and what it waits for',
   V.countsLine(planned) === 'planned: grants nothing yet — waits for agent-packs, marketplace' && !/reaches/.test(V.countsLine(planned)));

console.log('[2. the folds]');
const detail = { ...active, operations: [
    { op: 'profiles.create', effect: 'write', summary: 'Create an agent', callable: true, always_reviewed: 'it changes what an agent may do' },
    { op: 'profiles.rename', effect: 'write', summary: 'Rename', callable: true, always_reviewed: null },
    { op: 'profiles.list', effect: 'read', summary: 'List <agents>', callable: true },
    { op: 'trust.agent_sign_off', effect: 'human_only', summary: 'Sign an agent', callable: false, why_not: "a person's act" },
], playbooks: [{ anchor: 'building/create-agent', title: 'Create an agent', summary: 'Compose one' }], agents: [{ id: 'p', tag: 'BUILDER' }] };
const html = V.reachHTML(detail);
ok('the playbooks it teaches are a fold with their titles', /Playbooks it teaches · 1/.test(html) && /Create an agent/.test(html) && /building\/create-agent/.test(html));
ok('the changes it can submit are a fold; one a person always approves is marked',
   /Changes it can submit · 2/.test(html) && /always approved by you \(it changes what an agent may do\)/.test(html));
ok('…an ordinary change carries no such mark', (html.match(/always approved by you/g) || []).length === 1);
ok('what it reads is a fold', /What it reads · 1/.test(html));
ok('what it lists but cannot call says why', /Listed, not callable · 1/.test(html) && /a person&#39;s act/.test(html));
ok('the agents that hold it are named by tag', /Held by .*@BUILDER/.test(html));
ok('text is escaped', /List &lt;agents&gt;/.test(html) && !/List <agents>/.test(html));
ok('an empty fold is not drawn', !/· 0</.test(V.reachHTML({ operations: [], playbooks: [], agents: [] })));
ok('folds are native <details> — no emoji, theme variables only',
   /<details class="prv-fold"/.test(html) && /var\(--text-primary\)/.test(html) && ![...html].some(ch => ch.codePointAt(0) >= 0x2600 && !'—·…'.includes(ch)));

const withWords = { ...detail,
    examples: [{ ask: 'Add the FAQ to Policies', outcome: 'It submits adding it; you approve.' }],
    not_offered: [{ what: 'Releasing a quarantine', why: 'A human judgement.', ops: ['knowledge.document_release'] },
                  { what: 'Where results are sent', why: 'A way for data to leave.', args: ['scheduled_tasks.create:output_channel'] }] };
const wh = V.reachHTML(withWords);
ok('a role\'s examples are the first fold — the ask quoted, the outcome beneath',
   /Examples · 1/.test(wh) && wh.indexOf('Examples · 1') < wh.indexOf('Playbooks it teaches') && /“Add the FAQ to Policies”/.test(wh));
ok('its boundaries are a fold — what, why, and the operations and arguments each covers',
   /What it does not offer — and why · 2/.test(wh) && /A human judgement\./.test(wh)
   && /knowledge\.document_release/.test(wh) && /scheduled_tasks\.create<\/span> · output_channel/.test(wh));
ok('a role without examples or boundaries draws neither fold', !/Examples ·|does not offer/.test(V.reachHTML(detail)));

console.log('[3. one fetch, cached, failure not cached]');
answer = { ok: true, body: { roles: [active], declaration: { playbooks: 'platform-playbooks' } } };
fetches = [];
await V.fetchRoles(false); await V.fetchRoles(false);
ok('one request per shape per page', fetches.length === 1 && fetches[0].url === '/api/v1/platform/roles');
await V.fetchRoles(true);
ok('the detail shape asks for it', fetches.length === 2 && fetches[1].url === '/api/v1/platform/roles?detail=1');
ok('…with the session token', fetches[0].opts.headers.Authorization === 'Bearer tok');
V.invalidateRoles(); fetches = []; answer = { ok: false, body: {} };
let threw = false; try { await V.fetchRoles(false); } catch { threw = true; }
ok('a failed read throws (the view says it could not read — never an empty list as if there were no roles)', threw);
answer = { ok: true, body: { roles: [] } }; fetches = [];
await V.fetchRoles(false);
ok('…and is not cached: the next read asks again', fetches.length === 1);

console.log('[4. the declaration]');
const line = V.declarationLine({ declaration: { grant: 'componentConfig.platform.roles', default: ['guide'], playbooks: 'platform-playbooks' },
                                 roles: [{ id: 'guide', title: 'Guide' }] });
ok('the declaration names where roles are kept, the default by title, and the playbook skill',
   /componentConfig\.platform\.roles/.test(line) && /holds Guide/.test(line) && /platform-playbooks/.test(line));
ok('…and that roles only narrow, carry no tier, and are signed', /only narrows/.test(line) && /no user tier/.test(line) && /signature/.test(line));

console.log('[5. the Components view]');
const ch = read('static/js/handlers/componentHandler.js');
ok('it imports the one renderer', /from '\.\/platformRolesView\.js\?v=[\d.]+'/.test(ch));
ok('the Roles tab is drawn for a component whose manifest declares roles — not by component id',
   /if \(manifest\.roles \|\| comp\.roles\) \{\s*const rolesIcon/.test(ch) && !/component_id === 'platform'/.test(ch));
ok('the tab reads the detail shape and renders each role with the shared folds',
   /async function _loadPlatformRoles\(\)/.test(ch) && /fetchRoles\(true\)/.test(ch) && /reachHTML\(r\)/.test(ch) && /countsLine\(r\)/.test(ch));
ok('each role shows the digest its agents\' signatures hold', /r\.digest/.test(ch));
ok('the card carries a roles line, filled from the counts', /data-roles-line=/.test(ch) && /_fillRoleLines\(grid\)/.test(ch) && /fetchRoles\(false\)/.test(ch));
ok('the Libraries tab names the roles taught each playbook — only for the skill the roles slice',
   /function _taughtTo\(skillId, roles\)/.test(ch) && /\(roles\.declaration \|\| \{\}\)\.playbooks !== skillId/.test(ch)
   && /taught to no role/.test(ch) && /data-taught-to=/.test(ch));
ok('no raw fetch of the roles endpoint outside the module', !/fetch\(['`]\/api\/v1\/platform\/roles/.test(ch));

console.log('[6. the manifest]');
const man = JSON.parse(read('components/builtin/platform/manifest.json'));
const schema = JSON.parse(read('components/schemas/component_manifest_v1.schema.json'));
ok('the Platform manifest declares its roles', man.roles && man.roles.grant === 'componentConfig.platform.roles'
   && man.roles.definitions === 'roles/' && man.roles.playbooks === 'platform-playbooks' && man.roles.default[0] === 'guide');
ok('the schema describes the roles section and refuses a stray key',
   schema.properties.roles && schema.properties.roles.additionalProperties === false
   && JSON.stringify(schema.properties.roles.required) === JSON.stringify(['grant', 'default', 'definitions', 'playbooks']));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
