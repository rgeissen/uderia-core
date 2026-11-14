#!/usr/bin/env node
/**
 * PROP-007 increment 2 — the topology picker, EXECUTED under the shared DOM stub against a
 * fake backend whose preview endpoint runs the REAL `flow_topologies.preview()` (python3), so
 * the one pre-fill the server owns is the one the picker shows. No browser, no server.
 *
 * What a wrong picker would get wrong, and this gate measures:
 *   · a glyph for every registry key (a shape with no picture renders as a blank card)
 *   · one card per catalog row; an unshipped shape is GREYED with its reason, never hidden
 *   · the served fills SEED the checklist, so what the consumer sees is what Create sends
 *   · symmetric slots fill from a coordinator's experts in order; an ASYMMETRIC slot is never
 *     filled by order — it offers chips and waits for the consumer
 *   · a parameter the consumer touched is sent as `touched` (the expert count may not override it)
 *   · Create sends fills keyed by ROLE as ordered lists (node ids are minted per generation)
 *   · the canvas never offers Run while a required slot is open (structural + executed rule)
 */
import './_uderia_bus.mjs';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { installDom } from './dom_stub.mjs';

const ROOT = new URL('..', import.meta.url).pathname;
let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };

// ── the real registry + the real preview, through python ────────────────────────
function py(code, input) {
    const r = spawnSync('python3', ['-c', code], { cwd: ROOT, env: { ...process.env, PYTHONPATH: `${ROOT}src` }, input: input ? JSON.stringify(input) : undefined });
    if (r.status !== 0) throw new Error(String(r.stderr).slice(-600));
    return JSON.parse(String(r.stdout));
}
const CATALOG = py('import json; from trusted_data_agent.core.flow_topologies import catalog; print(json.dumps(catalog()))');
const PREVIEW_CODE = `import json,sys
from trusted_data_agent.core.flow_topologies import preview
a=json.loads(sys.stdin.read()); print(json.dumps(preview(a['key'], a.get('params') or {}, a.get('source'), a.get('profiles') or [], a.get('touched') or [])))`;

const PROFILES = [
    { id: 'profile-a', tag: 'A', name: 'A', profile_type: 'llm_only' },
    { id: 'profile-b', tag: 'B', name: 'B', profile_type: 'rag_focused' },
    { id: 'profile-c', tag: 'C', name: 'C', profile_type: 'tool_enabled' },
    { id: 'profile-coord', tag: 'COORD', name: 'COORD', profile_type: 'genie', genieConfig: { slaveProfiles: ['profile-a', 'profile-b', 'profile-c'] } },
    { id: 'profile-coord-empty', tag: 'EMPTY', name: 'EMPTY', profile_type: 'genie', genieConfig: { slaveProfiles: [] } },
];

// ── DOM + a fake backend that records every request ───────────────────────────
const { body } = installDom();
const calls = [];
globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url, 'http://x');
    const rec = { url: u.pathname, q: Object.fromEntries(u.searchParams.entries()), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null };
    calls.push(rec);
    const json = (o, status = 200) => ({ ok: status < 400, status, json: async () => o });
    if (rec.url === '/api/v1/flows/templates') return json({ templates: CATALOG, count: CATALOG.length, version: 1 });
    if (rec.url === '/api/v1/profiles') return json({ profiles: PROFILES });
    const m = rec.url.match(/^\/api\/v1\/flows\/templates\/([^/]+)$/);
    if (m) {
        const { source_profile_id, touched, ...params } = rec.q;
        const source = source_profile_id ? PROFILES.find(p => p.id === source_profile_id) : null;
        try {
            const out = py(PREVIEW_CODE, { key: decodeURIComponent(m[1]), params, source, profiles: PROFILES, touched: touched ? touched.split(',') : [] });
            out.source_tag = source ? source.tag : null;
            return json(out);
        } catch (e) { return json({ error: 'flow_template_unavailable', message: String(e) }, 400); }
    }
    if (rec.url === '/api/v1/flows' && rec.method === 'POST') return json({ flow_id: 'flow-new', name: 'created', definition: { nodes: [], edges: [] } }, 201);
    return json({ error: 'not found' }, 404);
};

const RENDERER_PATH = `${ROOT}components/builtin/flow/renderer.js`;
const SRC = readFileSync(RENDERER_PATH, 'utf8');
const R = await import(pathToFileURL(RENDERER_PATH));

// ═══ 1. the glyph map is the registry ══════════════════════════════════════════
const regKeys = CATALOG.map(t => t.key).sort().join(',');
ok('one glyph per registry key, exactly (a shape with no picture is a blank card)',
   Object.keys(R.TOPOLOGY_GLYPHS).sort().join(',') === regKeys, `${Object.keys(R.TOPOLOGY_GLYPHS).sort().join(',')} vs ${regKeys}`);
{ const g = Object.values(R.TOPOLOGY_GLYPHS); ok('no two topologies share a glyph (distinct shapes, not just distinct keys)', new Set(g).size === g.length); }
{ const norm = s => s.replace(/[\d.\s]+/g, '#');
  ok('Parallel and Fork-join glyphs differ in STRUCTURE, not only in numbers', norm(R.TOPOLOGY_GLYPHS.parallel) !== norm(R.TOPOLOGY_GLYPHS['fork-join'])); }
{ // Parallel = independent lanes; Fork-join = lanes that JOIN. A Parallel glyph with a node on its
  // right edge (cx > 16) draws a join and reads as Fork-join at card size.
  const endNodes = g => [...g.matchAll(/<(?:circle|rect)[^>]*?(?:cx|x)="([\d.]+)"/g)].map(m => Number(m[1])).filter(x => x > 16).length;
  ok('Parallel draws independent lanes — no shared end node on its right edge', endNodes(R.TOPOLOGY_GLYPHS.parallel) === 0, String(endNodes(R.TOPOLOGY_GLYPHS.parallel)));
  ok('Fork-join draws its join on the right edge', endNodes(R.TOPOLOGY_GLYPHS['fork-join']) >= 1); }
ok('the picker header glyph is not any topology\'s glyph', !!R.PICKER_HEAD_GLYPH && !Object.values(R.TOPOLOGY_GLYPHS).includes(R.PICKER_HEAD_GLYPH)
   && !/_glyphSvg\('fork-join', 20\)/.test(SRC));
ok('every glyph is monochrome (currentColor, no fill colours)', Object.values(R.TOPOLOGY_GLYPHS).every(g => !/#[0-9a-f]{3,6}|rgb\(/i.test(JSON.stringify(g))));

// ═══ 2. the gallery: one card per row, unavailable greyed with its reason, never hidden ═══
const created = [];
let overlay = await R.openTopologyPicker({ onCreated: f => created.push(f) });
await tick();
ok('the picker is a dialog attached to the body', overlay && overlay.isConnected && overlay.getAttribute('role') === 'dialog');
const cards = overlay.querySelectorAll('.ftp-card');
ok('one card per catalog row', cards.length === CATALOG.length, `${cards.length} vs ${CATALOG.length}`);
const unav = CATALOG.filter(t => !t.available);
// Increment 3 shipped the dialogic shapes — the swarm hand-off is the one greyed card left.
ok('every unavailable shape (the swarm hand-off) is a GREYED card carrying its reason', unav.length >= 1 && unav.every(t => {
    const c = overlay.querySelector(`.ftp-card[data-key="${t.key}"]`);
    const reason = c && c.querySelector('.ftp-card-reason');
    return c && c.classList.contains('is-unavailable') && reason && reason.textContent === t.unavailable_reason;
}));
ok('an available shape is not greyed', CATALOG.filter(t => t.available).every(t => !overlay.querySelector(`.ftp-card[data-key="${t.key}"]`).classList.contains('is-unavailable')));
const firstAvail = CATALOG.find(t => t.available);
ok('the first available shape is auto-selected', overlay.querySelector('.ftp-card.is-selected')?.dataset.key === firstAvail.key);
const previewCalls = () => calls.filter(c => /\/api\/v1\/flows\/templates\/./.test(c.url));
ok('a preview was requested for it from the ONE server-side generator', previewCalls().length === 1 && previewCalls()[0].url.endsWith('/' + firstAvail.key));
// clicking a greyed card explains, never selects
const debate = overlay.querySelector('.ftp-card[data-key="debate"]');
const swarm = overlay.querySelector('.ftp-card[data-key="swarm"]');
ok('the dialogic shapes are available now (debate is a live card, not greyed)', !!debate && !debate.classList.contains('is-unavailable'));
ok('the swarm hand-off card is RENDERED (greyed, never hidden)', !!swarm && swarm.classList.contains('is-unavailable'));
if (swarm) {
    const before = previewCalls().length;
    swarm.click(); await tick();
    ok('the swarm card hands off to the coordinator editor in words', /coordinator/i.test(overlay.querySelector('[data-ftp-status]').textContent));
    ok('…and selects nothing (no preview requested, selection unchanged)', previewCalls().length === before && overlay.querySelector('.ftp-card.is-selected')?.dataset.key === firstAvail.key);
}

// ═══ 3. no source: every slot open, the Create label counts them, a pill re-requests with touched ═══
ok('pipeline (no source) shows its 3 slots, all open', overlay.querySelectorAll('.ftp-slot').length === 3 && overlay.querySelectorAll('.ftp-slot.is-open').length === 3);
const createBtn = overlay.querySelector('[data-ftp-create]');
ok('Create is enabled with open slots (the flow is created now; it cannot run until filled)', !createBtn.disabled);
ok('…and its label counts them', createBtn.querySelector('[data-ftp-create-label]').textContent === 'Create with 3 open slots', createBtn.querySelector('[data-ftp-create-label]')?.textContent);
// the stub has no descendant combinator: scope by hand
const within = (root, outer, inner) => root.querySelector(outer)?.querySelector(inner) || null;
const plus = within(overlay, '.ftp-param[data-param="stages"]', 'button[data-step="1"]');
plus.click(); await tick();
const last = previewCalls().at(-1);
ok('a parameter pill re-requests the preview with the new value AND marks it touched', last.q.stages === '4' && last.q.touched === 'stages');
ok('four slot rows now', overlay.querySelectorAll('.ftp-slot').length === 4);
ok('the status line says the consequence of open slots', /4 slots still to fill — .*cannot run until/.test(overlay.querySelector('[data-ftp-status]').textContent), overlay.querySelector('[data-ftp-status]').textContent);
// fill ONE slot by hand → Create sends a role list with holes (null = left open)
const sel = overlay.querySelectorAll('select[data-slot]')[1];
sel.value = 'profile-b'; sel.dispatchEvent({ type: 'change' }); await tick();
ok('a hand-filled slot reads filled, the others stay open', overlay.querySelectorAll('.ftp-slot.is-filled').length === 1 && overlay.querySelectorAll('.ftp-slot.is-open').length === 3);
createBtn.click(); await tick();
const post = calls.find(c => c.method === 'POST');
ok('Create POSTs the shape + params + fills keyed by ROLE as an ordered list with holes',
   post && post.body.template === 'pipeline' && post.body.params.stages === 4
   && JSON.stringify(post.body.fills) === JSON.stringify({ stage: [null, 'profile-b', null, null] }), JSON.stringify(post && post.body));
ok('no source_tag travels when there was no source', post && !('source_tag' in post.body));
ok('the picker closes and hands the new flow to onCreated', !overlay.isConnected && created.length === 1 && created[0].flow_id === 'flow-new');

// ═══ 4. a coordinator source: symmetric filled in order, asymmetric offers chips ═══
calls.length = 0;
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-coord', onCreated: f => created.push(f) });
await tick();
ok('pipeline pre-fills its 3 stages from the coordinator (stages adapted to 3)',
   overlay.querySelectorAll('.ftp-slot.is-filled').length === 3 && overlay.querySelectorAll('.ftp-slot.is-open').length === 0
   && overlay.querySelector('.ftp-param[data-param="stages"]').classList.contains('is-adapted'));
ok('the fills are the experts IN DECLARED ORDER (A, B, C)', [...overlay.querySelectorAll('select[data-slot]')].map(s => s.querySelector('option[selected]')?.getAttribute('value')).join(',') === 'profile-a,profile-b,profile-c');
ok('the team selector shows the coordinator as the chosen team, listed as a roster',
   overlay.querySelector('[data-ftp-team]')?.querySelector('option[selected]')?.getAttribute('value') === 'profile-coord'
   && /@COORD — @A, @B, @C/.test(overlay.querySelector('[data-ftp-team]')?.textContent || ''));
ok('the note says the flow runs the roster in its own order and does NOT use the coordinator\'s routing',
   /@A, @B, @C/.test(overlay.querySelector('.ftp-prefill-note')?.textContent || '')
   && /@COORD’s own routing is not used/.test(overlay.querySelector('.ftp-prefill-note')?.textContent || ''));
ok('there is ONE way to drop the pre-fill — the team selector (no separate clear button)', !overlay.querySelector('[data-ftp-clear]'));
ok('slot titles are the node labels, capitalised, the role not repeated ("Stage 1", never "Stage 1 · stage")',
   [...overlay.querySelectorAll('.ftp-slot-role')].map(e => e.textContent).join('|') === 'Stage 1|Stage 2|Stage 3');
ok('Create reads plain "Create" — nothing left to fill', overlay.querySelector('[data-ftp-create-label]').textContent === 'Create');
overlay.querySelector('.ftp-card[data-key="fork-join"]').click(); await tick();
const synRow = [...overlay.querySelectorAll('.ftp-slot')].find(r => /Synthesiser/.test(r.querySelector('.ftp-slot-role')?.textContent || ''));
ok('the synthesiser row is titled "Synthesiser" (capitalised like "Worker 1")', synRow && synRow.querySelector('.ftp-slot-role').textContent === 'Synthesiser');
ok('fork-join: the 3 workers are filled, the synthesiser (asymmetric) is OPEN with chips A, B, C — never filled by order',
   overlay.querySelectorAll('.ftp-slot.is-filled').length === 3 && synRow && synRow.classList.contains('is-open')
   && [...synRow.querySelectorAll('.ftp-chip')].map(c => c.textContent).join(',') === '@A,@B,@C');
ok('Create counts the one open slot', overlay.querySelector('[data-ftp-create-label]').textContent === 'Create with 1 open slot');
synRow.querySelectorAll('.ftp-chip')[1].click(); await tick();
ok('a chip fills the slot', overlay.querySelectorAll('.ftp-slot.is-open').length === 0);
overlay.querySelector('[data-ftp-create]').click(); await tick();
const post2 = calls.find(c => c.method === 'POST');
ok('Create sends exactly the shown fills by role, in slot order, plus the source tag (a fill, not a link)',
   post2 && JSON.stringify(post2.body.fills) === JSON.stringify({ worker: ['profile-a', 'profile-b', 'profile-c'], synthesiser: ['profile-b'] })
   && post2.body.params.branches === 3 && post2.body.source_tag === 'COORD', JSON.stringify(post2 && post2.body));

// conditional puts the ASYMMETRIC router FIRST in node order — the case where "fill by order"
// would wrongly hand the router the first expert; the branches (symmetric) take A, B, C.
calls.length = 0;
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-coord', onCreated: () => {} });
await tick();
overlay.querySelector('.ftp-card[data-key="conditional"]').click(); await tick();
const roleOf = r => r.querySelector('.ftp-slot-role')?.textContent || '';
const routerRow = [...overlay.querySelectorAll('.ftp-slot')].find(r => /^Router$/.test(roleOf(r)));
const branchRows = [...overlay.querySelectorAll('.ftp-slot')].filter(r => /^Branch \d+$/.test(roleOf(r)));
ok('conditional: the router (asymmetric, first in order) is OPEN with chips A, B, C — the expert count went to the branches',
   routerRow && routerRow.classList.contains('is-open') && [...routerRow.querySelectorAll('.ftp-chip')].map(c => c.textContent).join(',') === '@A,@B,@C'
   && branchRows.length === 3 && branchRows.every(r => r.classList.contains('is-filled'))
   && branchRows.map(r => r.querySelector('option[selected]')?.getAttribute('value')).join(',') === 'profile-a,profile-b,profile-c',
   `router=${routerRow && routerRow.className} branches=${branchRows.map(r => r.className).join('|')}`);
overlay.querySelector('[data-ftp-close]').click();

// ═══ 4b. a dialogic shape: debate from a team, drawn as a loop ═══
calls.length = 0;
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-coord', onCreated: f => created.push(f) });
await tick();
overlay.querySelector('.ftp-card[data-key="debate"]').click(); await tick();
const titlesD = [...overlay.querySelectorAll('.ftp-slot-role')].map(e => e.textContent);
ok('debate from a 3-expert team: three debaters + the moderator + the synthesiser',
   titlesD.join('|') === 'Debater 1|Debater 2|Debater 3|Moderator|Synthesiser', titlesD.join('|'));
const rowD = (t) => [...overlay.querySelectorAll('.ftp-slot')].find(r => r.querySelector('.ftp-slot-role')?.textContent === t);
ok('the debaters are filled in declared order, the moderator is the eval judge panel',
   ['Debater 1', 'Debater 2', 'Debater 3', 'Moderator'].every(t => rowD(t)?.classList.contains('is-filled'))
   && /judge panel/.test(rowD('Moderator').textContent));
ok('the synthesiser (asymmetric) waits with the team as chips — never filled by order',
   rowD('Synthesiser').classList.contains('is-open') && [...rowD('Synthesiser').querySelectorAll('.ftp-chip')].map(c => c.textContent).join(',') === '@A,@B,@C');
ok('the preview draws the LOOP: a dashed frame around the debate and a loop-back curve',
   !!overlay.querySelector('.ftp-loop-frame') && !!overlay.querySelector('.ftp-loop-edge'));
overlay.querySelector('.ftp-card[data-key="pair-programming"]').click(); await tick();
const titlesP = [...overlay.querySelectorAll('.ftp-slot-role')].map(e => e.textContent);
ok('pair programming asks for exactly two agents — driver and navigator (the canvas is not a slot)',
   titlesP.join('|') === 'Driver|Navigator', titlesP.join('|'));
overlay.querySelector('[data-ftp-close]').click();

// ═══ 5. touched beats the expert count; clear pre-fill drops the source ═══
calls.length = 0;
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-coord', onCreated: () => {} });
await tick();
within(overlay, '.ftp-param[data-param="stages"]', 'button[data-step="1"]').click(); await tick();
ok('a touched parameter is NOT overridden by the expert count (stages 4, three filled, one open)',
   previewCalls().at(-1).q.touched === 'stages' && overlay.querySelectorAll('.ftp-slot').length === 4
   && overlay.querySelectorAll('.ftp-slot.is-filled').length === 3 && overlay.querySelectorAll('.ftp-slot.is-open').length === 1);
{ const sel = overlay.querySelector('[data-ftp-team]'); sel.value = ''; sel.dispatchEvent({ type: 'change', target: sel }); }
await tick();
ok('"Choose agents individually" re-requests WITHOUT the source and opens every slot',
   !('source_profile_id' in previewCalls().at(-1).q) && overlay.querySelectorAll('.ftp-slot.is-open').length === overlay.querySelectorAll('.ftp-slot').length
   && !overlay.querySelector('.ftp-prefill-note'));
{ const sel = overlay.querySelector('[data-ftp-team]'); sel.value = 'profile-coord'; sel.dispatchEvent({ type: 'change', target: sel }); }
await tick();
ok('choosing a team again re-requests WITH that coordinator and pre-fills its experts',
   previewCalls().at(-1).q.source_profile_id === 'profile-coord' && overlay.querySelectorAll('.ftp-slot.is-filled').length === 3);
overlay.querySelector('[data-ftp-close]').click();
ok('the close control removes the dialog', !overlay.isConnected);

// ═══ 6. a single (non-genie) agent is a SUGGESTION on every slot, never a fill ═══
calls.length = 0;
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-a', onCreated: () => {} });
await tick();
ok('a single agent fills nothing by order…', overlay.querySelectorAll('.ftp-slot.is-filled').length === 0);
ok('…and is offered as a chip on every slot that accepts its class', [...overlay.querySelectorAll('.ftp-slot')].every(r => [...r.querySelectorAll('.ftp-chip')].map(c => c.textContent).join() === '@A'));
// Esc closes through the document listener, and the listener is released
document.dispatchEvent({ type: 'keydown', key: 'Escape', target: body });
ok('Escape closes the picker', !overlay.isConnected);
ok('the keydown listener is released on close (no ghost handlers)', (document._listeners.keydown || []).length === 0);
// a picker removed from OUTSIDE (not through close) must not leave a ghost listener either
overlay = await R.openTopologyPicker({ onCreated: () => {} }); await tick();
overlay.remove();
document.dispatchEvent({ type: 'keydown', key: 'ArrowRight', target: body });
ok('an externally removed picker releases its listener on the next key', (document._listeners.keydown || []).length === 0);
overlay = await R.openTopologyPicker({ onCreated: () => {} }); await tick();
const second = await R.openTopologyPicker({ onCreated: () => {} }); await tick();
ok('opening a second picker closes the first through its own close (one dialog, one listener)',
   !overlay.isConnected && second.isConnected && (document._listeners.keydown || []).length === 1);
second.querySelector('[data-ftp-close]').click();
// a genie with no experts suggests nothing
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-coord-empty', onCreated: () => {} });
await tick();
ok('a coordinator with no experts pre-fills nothing and suggests nothing', overlay.querySelectorAll('.ftp-slot.is-filled').length === 0 && overlay.querySelectorAll('.ftp-chip').length === 0 && !overlay.querySelector('[data-ftp-clear]'));
overlay.querySelector('[data-ftp-close]').click();

// ═══ 7. the canvas: a slot is open by ONE rule, and Run is never offered while one is ═══
const slotNode = (fill, accepts = ['llm_only'], required = true) => ({ slot: { role: 'r', required, accepts, hint: '' }, ...(fill === undefined ? {} : { profile_id: fill }) });
ok('_slotIsOpen: no fill → open', R._slotIsOpen(slotNode(undefined), PROFILES) === true);
ok('_slotIsOpen: a fill naming a known profile → not open', R._slotIsOpen(slotNode('profile-a'), PROFILES) === false);
ok('_slotIsOpen: a fill naming NO known profile (deleted agent) → open, not a default', R._slotIsOpen(slotNode('profile-gone'), PROFILES) === true);
ok('_slotIsOpen: profiles not loaded yet → not a verdict', R._slotIsOpen(slotNode('profile-gone'), []) === false);
ok('_slotIsOpen: judge_panel on a slot that accepts it → not open', R._slotIsOpen(slotNode('judge_panel', ['judge_panel']), PROFILES) === false);
ok('_slotIsOpen: judge_panel on a slot that does not → open', R._slotIsOpen(slotNode('judge_panel', ['llm_only']), PROFILES) === true);
ok('_slotIsOpen: an optional slot is never open', R._slotIsOpen(slotNode(undefined, ['llm_only'], false), PROFILES) === false);
ok('_slotIsOpen: a node without a slot is never open', R._slotIsOpen({ profile_id: '' }, PROFILES) === false);

ok('Run Flow is withheld while a slot is open (disabled on openSlotCount)', /disabled:\s*!hasNodes\s*\|\|\s*openSlotCount\s*>\s*0/.test(SRC));
ok('openSlotCount is derived from graphIssues (one derivation for pill, gate and health panel)', /const openSlotCount = graphIssues\.filter\(i => i\.slot\)\.length/.test(SRC));
ok('graphIssues raises an ERROR per open slot through the shared rule', /level: 'error', slot: true/.test(SRC) && /_slotIsOpen\(n\.data\?\.data \|\| n\.data \|\| \{\}, profiles\)/.test(SRC));
ok('a slot never inherits the flow default on the canvas', /ownP \|\| \(!slot && defaultProfileId \? byId\[defaultProfileId\] : null\)/.test(SRC));
ok('the node wears the open-slot class + badge', /flow-node--slot-open/.test(SRC) && /className: 'flow-node-slot-badge'/.test(SRC) && /\.flow-node\.flow-node--slot-open\s*\{[^}]*border-style: dashed/.test(SRC));
ok('the Inspector offers no "inherit" option on a slot and filters to what it accepts',
   /_slotRow \? '— choose an agent for this slot —' : _inheritLabel/.test(SRC) && /\(_slotRow\.accepts \|\| \[\]\)\.includes\(p\.profile_type\)/.test(SRC));
ok('a toolbar pill names the open-slot count', /flow-slot-pill/.test(SRC) && /slot\$\{openSlotCount === 1 \? '' : 's'\} to fill/.test(SRC));

// the shared picker honours a caller's empty-row icon (Anatomy/Trust Circle keep the star)
const AA = readFileSync(`${ROOT}static/js/handlers/agentAnatomy.js`, 'utf8');
ok('agentAnatomy.openAgentPicker renders specialRow.icon when given, the star otherwise', /icon: specialRow\.icon \|\| null/.test(AA) && /\$\{a\.icon \|\| starIco\(null\)\}/.test(AA));

// ═══ 8. the two entry points open the picker ═══
const FH = readFileSync(`${ROOT}static/js/handlers/flowsHandler.js`, 'utf8');
const WS = readFileSync(`${ROOT}static/js/handlers/workspacePanelHandler.js`, 'utf8');
ok('the profile Flows tab "New Flow" opens the picker (blank editor only as the fallback)', /prof-flow-new-btn[\s\S]{0,900}openTopologyPicker\(/.test(FH) && /catch \(_\) \{\s*_openProfileFlowPanel\(null\);/.test(FH));
ok('the Workspace flow section declares a create hook that opens the picker', /create: \(profileId\) => import\('\/api\/v1\/components\/flow\/renderer'\)[\s\S]{0,120}openTopologyPicker\(/.test(WS) && /ws-sec-new/.test(WS));
ok('both rows name open slots in words', /slot\$\{open === 1 \? '' : 's'\} to fill/.test(WS) && /flow-slot-chip/.test(FH));
ok('the split-panel switcher offers "New flow…" through the picker', /newLabel: 'New flow…'/.test(SRC) && /onNew: \(\) => openTopologyPicker\(/.test(SRC));

// ═══ 9. agent choice goes through the platform's ONE filterable picker ═══
// window.AgentAnatomy.openAgentPicker is the shared picker (search + IFOC class pills) behind the
// Anatomy star and the Trust Circle. Present → every agent choice opens it; absent → the native
// select stays (sections 3–6 above ran that fallback path).
const pickCalls = [];
window.AgentAnatomy = { openAgentPicker: (o) => { pickCalls.push(o); } };
calls.length = 0;
overlay = await R.openTopologyPicker({ sourceProfileId: 'profile-coord', onCreated: () => {} });
await tick();
overlay.querySelector('.ftp-card[data-key="fork-join"]').click(); await tick();
ok('with the shared picker present, slots render a trigger, not a native select',
   overlay.querySelectorAll('[data-slot-pick]').length === 4 && overlay.querySelectorAll('select[data-slot]').length === 0);
ok('the team selector is a trigger into the shared picker too', !!overlay.querySelector('[data-ftp-team-pick]') && !overlay.querySelector('select[data-ftp-team]'));
const synTrig = [...overlay.querySelectorAll('[data-slot-pick]')].find(b => /Synthesiser/.test(b.getAttribute('aria-label') || ''));
synTrig.click(); await tick();
const pc1 = pickCalls.at(-1);
ok('a slot opens the shared picker anchored on the dialog', pc1 && pc1.hostEl === overlay.querySelector('.ftp-modal'));
ok('…with items carrying their IFOC class (what the class pills filter on), restricted to what the slot accepts',
   pc1 && pc1.items.length > 0 && pc1.items.every(i => i.id && i.tag && i.type) && pc1.items.every(i => ['llm_only','rag_focused','tool_enabled','genie'].includes(i.type)));
ok('…and a "Leave this slot open" row (the empty choice, never an inherit)', pc1 && pc1.specialRow && pc1.specialRow.name === 'Leave this slot open');
ok('…whose icon is the open-slot mark (a dashed circle), not the Anatomy atlas star', /stroke-dasharray/.test(pc1?.specialRow?.icon || '') && /<circle/.test(pc1?.specialRow?.icon || ''));
pc1.onPick('profile-b'); await tick();
ok('picking an agent fills the slot', overlay.querySelectorAll('.ftp-slot.is-open').length === 0
   && /@B/.test([...overlay.querySelectorAll('[data-slot-pick]')].find(b => /Synthesiser/.test(b.getAttribute('aria-label') || '')).textContent));
[...overlay.querySelectorAll('[data-slot-pick]')].find(b => /Synthesiser/.test(b.getAttribute('aria-label') || '')).click(); await tick();
pickCalls.at(-1).onPick(null); await tick();
ok('the empty row re-opens the slot', overlay.querySelectorAll('.ftp-slot.is-open').length === 1);
overlay.querySelector('[data-ftp-team-pick]').click(); await tick();
const pc2 = pickCalls.at(-1);
ok('the team picker offers coordinators only, each named by its roster',
   pc2 && pc2.items.length === 2 && pc2.items.every(i => i.type === 'genie') && pc2.items.find(i => i.id === 'profile-coord')?.name === '@A, @B, @C'
   && pc2.specialRow?.name === 'Choose agents individually');
pc2.onPick(''); await tick();
ok('picking "individually" in the shared picker drops the team', !('source_profile_id' in previewCalls().at(-1).q) && overlay.querySelectorAll('.ftp-slot.is-filled').length === 0);
overlay.querySelector('[data-ftp-close]').click();
ok('_agentPickerItems restricts to the classes a slot accepts (a slot accepting only Ideate offers no Focus agent)',
   R._agentPickerItems(PROFILES, ['llm_only']).length > 0 && R._agentPickerItems(PROFILES, ['llm_only']).every(i => i.type === 'llm_only')
   && R._agentPickerItems(PROFILES, ['genie']).every(i => i.type === 'genie'));
ok('_agentPickerItems keeps the judge panel only where a slot accepts it',
   R._agentPickerItems(PROFILES, ['judge_panel', 'llm_only'])[0].id === 'judge_panel' && !R._agentPickerItems(PROFILES, ['llm_only']).some(i => i.id === 'judge_panel'));
ok('the canvas Inspector\'s agent field opens the same shared picker (FlowSelect only as the fallback)',
   /profiles\.length > 0 && _sharedAgentPicker\(\)/.test(SRC) && /className: 'flow-agent-trigger'/.test(SRC)
   && /_openSharedAgentPicker\(host, \{\s*items: _agentPickerItems\(profiles, _slotRow \? _slotRow\.accepts : null\)/.test(SRC));
delete window.AgentAnatomy;


// ── increment 4 follow-up: every addable node type, the round stepper, a finished run's outcome ──
console.log('\n[palette — every node type a user can add, from ONE declaration]');
const _staticKeys = [...(SRC.match(/const _staticNodeTypes = \{([\s\S]*?)\};/) || [, ''])[1].matchAll(/^\s*(\w+):/gm)].map(m => m[1]);
const _structural = new Set(['start', 'end', 'scope']);
const _addTypes = R.ADDABLE_NODES.map(n => n.type);
ok('every node type the canvas renders (bar start/end/frame) is addable — judge and canvas included',
   _staticKeys.length > 6 && _staticKeys.filter(k => !_structural.has(k)).every(k => _addTypes.includes(k)),
   `missing: ${_staticKeys.filter(k => !_structural.has(k) && !_addTypes.includes(k)).join(',')}`);
ok('judge and canvas are in the palette list (outside an Optimize-only gate)',
   R._addableNodes(false).some(n => n.type === 'judge') && R._addableNodes(false).some(n => n.type === 'artefact'));
ok('the Prompt node stays Optimize-only', !R._addableNodes(false).some(n => n.type === 'task_prompt') && R._addableNodes(true).some(n => n.type === 'task_prompt'));
const _keys = R.ADDABLE_NODES.map(n => n.key).filter(Boolean);
ok('number-key shortcuts are unique', new Set(_keys).size === _keys.length);
ok('every addable type has a label, short label, title and an icon', R.ADDABLE_NODES.every(n => n.label && n.short && n.title && Array.isArray(n.icon) && n.icon.length));
ok('a new judge defaults to the eval judge panel (runs without an agent choice)', R._defaultNodeData('judge').profile_id === 'judge_panel');
ok('a new canvas node reads the previous step\'s output', R._defaultNodeData('artefact').source === 'output');
ok('a dropped Tool node starts with the same defaults as a clicked one (one defaults function)',
   (SRC.match(/data: _defaultNodeData\(type\)/g) || []).length >= 3 && !/\{ id, type, label, position(: midPos)?, data: \{\} \}/.test(SRC));
ok('the left palette, ⌘K, the edge menu and the help overlay all read the one list (no hand-kept copy)',
   /\.\.\._addableNodes\(_defaultIsOptim(?:, actOn(?:, filesOn)?)?\)\.map\(n => \{/.test(SRC) && /_addableNodes\(_defaultIsOptim(?:, actOn(?:, filesOn)?)?\)\.map\(n => \(\{ label: `Add \$\{n\.label\} node`/.test(SRC)
   && /_addableNodes\(_defaultIsOptim(?:, actOn(?:, filesOn)?)?\)\.map\(\(\{ type: t, label: lbl \}\)/.test(SRC) && /ADDABLE_NODES\.filter\(n => n\.key\)\.map/.test(SRC)
   && !/addNode\('task_llm'\)/.test(SRC) && (SRC.match(/className: 'flow-palette-btn'/g) || []).length === 1);

console.log('\n[toolbar default profile — the shared picker, filterable by engine class]');
ok('the toolbar default-profile control opens the shared agent picker over ALL profiles (FlowSelect only as the fallback)',
   /className: 'flow-default-agent-btn'/.test(SRC) && /items: _agentPickerItems\(profiles, null\),\s*currentId: defaultProfileId \|\| null,/.test(SRC)
   && /onPick: \(id\) => handleSetDefaultProfile\(id \|\| ''\)/.test(SRC));
ok('every item handed to it carries its IFOC class (what the class pills filter on)',
   R._agentPickerItems(PROFILES, null).length === PROFILES.length && R._agentPickerItems(PROFILES, null).every(i => ['llm_only','rag_focused','tool_enabled','genie'].includes(i.type)));

console.log('\n[loop frame — the round limit is editable in place]');
const _pyRange = py('import json; from trusted_data_agent.core import flow_db as d; print(json.dumps([d.SCOPE_MIN_ROUNDS, d.SCOPE_MAX_ROUNDS]))');
ok('the canvas stepper range mirrors the server clamp', R.SCOPE_MIN_ROUNDS === _pyRange[0] && R.SCOPE_MAX_ROUNDS === _pyRange[1]);
ok('the stepper is disabled while the loop is running, and sends the update through the patch op',
   /disabled: liveNow \|\| max <= SCOPE_MIN_ROUNDS/.test(SRC) && /disabled: liveNow \|\| max >= SCOPE_MAX_ROUNDS/.test(SRC)
   && /op: 'update_scope', payload: \{ scope_id, updates: \{ max_iterations \} \}/.test(SRC));
ok('the patch ref is declared before it is assigned (no temporal dead zone at render)',
   SRC.indexOf('const patchFlowRef = useRef(null);') > 0 && SRC.indexOf('const patchFlowRef = useRef(null);') < SRC.indexOf('patchFlowRef.current = patchFlow;'));

console.log('\n[a finished run — reopening the flow shows how it ended]');
const _run = { status: 'complete', output_result: { scopes: { 'scope-1': { exit_reason: 'converged', rounds_completed: 2, converged: true } } } };
const _nrs = [
  { node_id: 'w', node_type: 'task_llm', status: 'success', output_state: { output: 'draft 1' } },
  { node_id: 'j', node_type: 'judge', status: 'success', output_state: { output: 'no', passed: false, score: 0.4, verdict: 'FAIL', judge: 'panel' } },
  { node_id: 'w', node_type: 'task_llm', status: 'success', output_state: { output: 'draft 2' } },
  { node_id: 'j', node_type: 'judge', status: 'success', output_state: { output: 'ok', passed: true, score: 0.9, verdict: 'PASS', judge: 'panel' } },
  { node_id: 'c', node_type: 'artefact', status: 'success', output_state: { output: 'code', canvas_id: 'cv-1', version: 2 } },
  { node_id: 'e', node_type: 'task_function', status: 'error', output_state: {}, error_msg: 'boom' },
  { node_id: 'p', node_type: 'task_llm', status: 'running', output_state: {} },
];
const _ov = R._runOutcomeOverlay(_run, _nrs);
ok('the last round wins for each node', _ov.nodes.w._outputPreview === 'draft 2' && _ov.nodes.j._verdict.passed === true && _ov.nodes.j._verdict.score === 0.9);
ok('a canvas node gets its canvas and version back', _ov.nodes.c._canvasId === 'cv-1' && _ov.nodes.c._canvasVersion === 2);
ok('an error keeps its message; a non-terminal record paints nothing', _ov.nodes.e._execStatus === 'error' && _ov.nodes.e._errorMsg === 'boom' && !('p' in _ov.nodes));
ok('the loop frame says how it ended (converged in round 2), not that it is running', _ov.scopes['scope-1']._exitReason === 'converged' && _ov.scopes['scope-1']._roundsDone === 2 && _ov.scopes['scope-1']._round === null);
ok('an empty or malformed run paints nothing and never throws', JSON.stringify(R._runOutcomeOverlay(null, null).nodes) === '{}' && JSON.stringify(R._runOutcomeOverlay({}, [null, {}]).nodes) === '{}');
ok('the mount reconcile paints a FINISHED latest run through the overlay (a running one is still adopted live)',
   /const ov = _runOutcomeOverlay\(run, run\.node_runs\);/.test(SRC) && /if \(latest\.status !== 'running' && latest\.status !== 'queued'\) \{/.test(SRC));


console.log('\n[loops on the canvas — make from a selection, edit, remove]');
const _mem = [{ id: 'w', label: 'Writer', type: 'task_llm' }, { id: 'j', label: 'Reviewer', type: 'judge' }];
const _f0 = R._loopFormFromScope({ name: 'L', max_iterations: 4 }, _mem);
ok('a loop with no stop rule opens on "after the last round", the judge preselected for when one is chosen',
   _f0.stop.kind === 'none' && _f0.stop.node === 'j' && _f0.rounds === 4 && _f0.history === 'all');
ok('the editor reads a pass rule, a pass-with-score rule and a custom condition back',
   R._loopFormFromScope({ exit_when: { node: 'j', condition: 'passed == True' } }, _mem).stop.kind === 'pass'
   && R._loopFormFromScope({ exit_when: { node: 'j', condition: 'passed == True and (score is None or score >= 0.75)' } }, _mem).stop.threshold === '0.75'
   && R._loopFormFromScope({ exit_when: { node: 'j', condition: 'score >= 0.5' } }, _mem).stop.expression === 'score >= 0.5');
const _u = R._loopUpdatesFromForm({ name: '  ', rounds: '99', history: '2', stop: { kind: 'threshold', node: 'j', threshold: '0.7' } });
ok('the update it sends: name defaulted, rounds clamped, history as a number, a threshold rule by mode (the server builds the condition)',
   _u.name === 'Loop' && _u.max_iterations === R.SCOPE_MAX_ROUNDS && _u.history_window === 2
   && _u.exit_when.mode === 'threshold' && _u.exit_when.threshold === 0.7 && !('condition' in _u.exit_when));
ok('"every earlier round" and "no early stop" send null', (() => { const x = R._loopUpdatesFromForm({ rounds: 3, history: 'all', stop: { kind: 'none' } }); return x.history_window === null && x.exit_when === null; })());
ok('only real steps can become a loop (never Start, End or the frame)',
   JSON.stringify(R._loopableIds([{ id: 's', type: 'start', selected: true }, { id: 'a', type: 'task_llm', selected: true },
     { id: 'x', type: 'scope', selected: true }, { id: 'b', type: 'judge', selected: false }])) === '["a"]');
ok('"Make loop" is offered on a multi-selection, on a step\'s context menu and in ⌘K, all through one _makeLoop',
   /'Make loop'\)/.test(SRC) && /Repeat as a loop/.test(SRC) && /label: 'Make a loop from the selection', run: \(\) => _makeLoop/.test(SRC)
   && /op: 'create_scope', payload: \{ node_ids: ids \}/.test(SRC));
ok('the frame\'s Edit opens the loop editor (disabled while the loop runs); the editor is rendered',
   /UderiaBus\.emit\('uderia:flow-scope-edit'/.test(SRC) && /className: 'flow-scope-edit nodrag nopan', disabled: liveNow/.test(SRC) && /buildLoopEditor\(\),/.test(SRC)
   && /op: 'delete_scope', payload: \{ scope_id: scopeId \}/.test(SRC));
ok('a refused patch says why (the server\'s words, as a warning banner and in the editor)',
   /lastPatchErrorRef\.current = msg;/.test(SRC) && /window\.showAppBanner\(msg, 'warning'\)/.test(SRC) && /error: lastPatchErrorRef\.current \|\|/.test(SRC));

console.log('\n[earlier runs — pick one and see how it ended]');
const _nodes0 = [{ id: 'a', type: 'task_llm', data: { label: 'A', _execStatus: 'error', _errorMsg: 'old', _verdict: { passed: false } } },
                 { id: 'scope-s', type: 'scope', data: { _exitReason: 'max_rounds' } }];
const _p = R._paintRunOverlay(_nodes0, { nodes: { a: { _execStatus: 'success', _outputPreview: 'new' } }, scopes: { s: { _exitReason: 'converged', _roundsDone: 1, _round: null } } });
ok('showing a run clears the previous run\'s fields first (no stale error or verdict survives)',
   _p[0].data._execStatus === 'success' && _p[0].data._errorMsg === null && _p[0].data._verdict === null && _p[0].data.label === 'A');
ok('…and paints the loop frame\'s ending', _p[1].data._exitReason === 'converged' && _p[1].data._roundsDone === 1);
ok('a node the run never reached shows no status', R._paintRunOverlay(_nodes0, { nodes: {}, scopes: {} })[0].data._execStatus === null);
ok('the status bar offers earlier runs (finished only) and the reopen path uses the same painter',
   /buildRunsControl\(\),/.test(SRC) && /filter\(x => x\.status !== 'running' && x\.status !== 'queued'\)/.test(SRC)
   && (SRC.match(/_paintRunOverlay\(nds, /g) || []).length === 2);

console.log('\n[recently used agents in the picker]');
ok('a recent suggestion chip is marked as recent (a word and a dashed outline, not colour alone)',
   /class="ftp-chip\$\{c\.recent \? ' is-recent' : ''\}"/.test(SRC) && /<span class="ftp-chip-recent">recent<\/span>/.test(SRC) && /\.ftp-chip\.is-recent \{ border-style: dashed; \}/.test(SRC));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
