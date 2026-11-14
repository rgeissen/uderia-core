/**
 * KG split panel follows the composer's effective profile — gate.
 *
 * Executes the SHIPPED `followEffectiveProfile` / `_renderNoGraphState` out of
 * components/builtin/knowledge_graph/renderer.js under a DOM stub, and pins the
 * seams around it: main.js emits ONE `uderia:effective-profile` signal from the
 * resource-panel seam, the renderer subscribes to it, the switcher pins, close
 * unpins, and /graph-spec carries the graph's identity. No server, no network.
 *
 *   node test/test_kg_follow_profile.mjs
 */
import './_uderia_bus.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = fs.readFileSync(path.join(ROOT, 'components/builtin/knowledge_graph/renderer.js'), 'utf8');
const MAIN = fs.readFileSync(path.join(ROOT, 'static/js/main.js'), 'utf8');
const REST = fs.readFileSync(path.join(ROOT, 'src/trusted_data_agent/api/rest_routes.py'), 'utf8');

let passed = 0, failed = 0;
function check(label, cond, detail) {
  if (cond) { passed++; console.log(`  ✓ ${label}`); }
  else { failed++; console.log(`  ✗ ${label}${detail !== undefined ? '  ' + String(detail).slice(0, 200) : ''}`); }
}

// ── extract the shipped code verbatim ─────────────────────────────────────────
const start = SRC.indexOf('let _userPinnedKgId = null;');
const end = SRC.indexOf('if (typeof window !== \'undefined\') {\n    UderiaBus.on(\'uderia:effective-profile\'');
check('the follow rule exists in the shipped module', start > 0 && end > start);
const slice = SRC.slice(start, end).replace(/^export\s+/mg, '');

function harness({ panelOpen = true, active = null, theatreOpen = false, specs = {}, profileTags = {} } = {}) {
  const els = {
    'kg-split-panel': { style: { display: panelOpen ? 'flex' : 'none' } },
    'kg-split-content': { innerHTML: '' },
    'kg-split-title': { textContent: 'old title' },
  };
  const calls = { open: [], cleared: 0, persisted: [] };
  const document = { getElementById: (id) => els[id] || null };
  const localStorage = { getItem: () => 'tok' };
  const fetchSpec = async (pid) => {
    const s = specs[pid];
    if (s === 'fail') throw new Error('boom');
    if (s === undefined) return null;
    return { spec: s, profile_tag: profileTags[pid] || '' };
  };
  const fn = new Function(
    'document', 'localStorage', '_activeSpec', '_theatreTurnOpen', 'openKGSplitPanel',
    '_clearAllAnimations', '_graphState', '_persistKGOpenState', 'DEFAULT_INSPECT_TYPES', 'fetch', '__calls',
    '_theatre', '_unmountTheatre', '_reportKGShown',
    `${slice}
     return {
       follow: (pid, o) => followEffectiveProfile(pid, Object.assign({ fetchSpec: ${fetchSpec.toString()} }, o || {})),
       get active() { return _activeSpec; },
       set active(v) { _activeSpec = v; },
       pin(v) { _userPinnedKgId = v; },
       get pinned() { return _userPinnedKgId; },
     };`);
  const api = fn(document, localStorage, active, theatreOpen,
    (spec) => { calls.open.push(spec); },
    () => { calls.cleared++; }, null,
    (spec) => { calls.persisted.push(spec); }, 'database,domain,entity,metric',
    () => { throw new Error('real fetch must not be reached'); }, calls,
    null, () => { calls.unmounted = (calls.unmounted || 0) + 1; },
    (id) => { calls.shown = id; });
  // the fetchSpec closure above was stringified; give it its data through globals
  globalThis.__specs = specs; globalThis.__tags = profileTags;
  return { api, calls, els };
}
// The stringified fetchSpec cannot close over `specs`; rebuild it to read globals.
function harness2(opts) {
  const h = harness(opts);
  const specs = opts.specs || {}, tags = opts.profileTags || {};
  h.follow = (pid, o) => h.api.follow(pid, Object.assign({
    fetchSpec: async (p) => {
      const s = specs[p];
      if (s === 'fail') throw new Error('boom');
      if (s === undefined) return null;
      return { spec: s, profile_tag: tags[p] || '' };
    },
  }, o || {}));
  return h;
}

const GRAPH_A = { kg_id: 'kg-A', kg_name: 'banking_core', nodes: [{ id: 0 }], links: [] };
const GRAPH_B = { kg_id: 'kg-B', kg_name: 'retail_core', nodes: [{ id: 0 }, { id: 1 }], links: [] };
const EMPTY = { kg_id: null, nodes: [], links: [] };

console.log('\n[the follow rule]');
(async () => {
  // switch: a different graph is rendered through the canonical entry point
  let h = harness2({ active: { kg_id: 'kg-A', profile_id: 'p-bank' }, specs: { 'p-retail': GRAPH_B } });
  let r = await h.follow('p-retail');
  check('a different graph → switched', r === 'switched', r);
  check('rendered through openKGSplitPanel with the canonical spec', h.calls.open.length === 1 && h.calls.open[0]._canonical === true);
  check('the new spec carries the profile it was resolved for + its kg identity',
        h.calls.open[0].profile_id === 'p-retail' && h.calls.open[0].kg_id === 'kg-B');
  check('the title is the graph name', h.calls.open[0].title === 'retail_core');

  // same graph → no repaint
  h = harness2({ active: { kg_id: 'kg-A', profile_id: 'p-bank' }, specs: { 'p-bank2': GRAPH_A } });
  r = await h.follow('p-bank2');
  check('the same graph under another profile → same (no re-render)', r === 'same' && h.calls.open.length === 0, r);

  // no graph → honest empty state naming the agent; the old graph does NOT stay on screen
  h = harness2({ active: { kg_id: 'kg-A', profile_id: 'p-bank' }, specs: { 'p-ideat': EMPTY }, profileTags: { 'p-ideat': 'IDEAT' } });
  r = await h.follow('p-ideat');
  check('no graph bound → no-graph', r === 'no-graph', r);
  check('the empty state names the agent', h.els['kg-split-content'].innerHTML.includes('@IDEAT') && h.els['kg-split-content'].innerHTML.includes('No knowledge graph'));
  check('the agent is named by TAG, never by profile id', !h.els['kg-split-content'].innerHTML.includes('p-ideat'));
  // no tag anywhere (an older backend + no profile list) → "this agent", still never the id
  h = harness2({ active: { kg_id: 'kg-A' }, specs: { 'p-x': EMPTY } });
  await h.follow('p-x');
  check('with no tag available it says "this agent", not the id',
        h.els['kg-split-content'].innerHTML.includes('this agent') && !h.els['kg-split-content'].innerHTML.includes('p-x'));
  check('animations cleared, active spec marked empty, open-state persisted for the session',
        h.calls.cleared === 1 && h.api.active && h.api.active._empty === true && h.calls.persisted.length === 1);
  check('the empty state never opens a graph', h.calls.open.length === 0);
  // …and switching back to a profile WITH the graph re-renders (the empty spec is not "the same graph")
  r = await h.follow('p-bank', { fetchSpec: async () => ({ spec: GRAPH_A, profile_tag: 'IDBANK' }) });
  check('from the empty state, the graph comes back', r === 'switched' && h.calls.open.length === 1, r);

  // closed panel → nothing, no fetch
  h = harness2({ panelOpen: false, specs: { 'p-retail': 'fail' } });
  r = await h.follow('p-retail');
  check('closed panel → nothing (no fetch)', r === 'closed');

  // pinned by the switcher → nothing
  h = harness2({ active: { kg_id: 'kg-A' }, specs: { 'p-retail': GRAPH_B } });
  h.api.pin('kg-A');
  r = await h.follow('p-retail');
  check('a graph the user pinned in the switcher is not swapped', r === 'pinned' && h.calls.open.length === 0);

  // streaming → nothing (either signal)
  h = harness2({ active: { kg_id: 'kg-A' }, specs: { 'p-retail': GRAPH_B } });
  r = await h.follow('p-retail', { streaming: true });
  check('a streaming turn keeps its graph (composer signal)', r === 'streaming' && h.calls.open.length === 0);
  h = harness2({ active: { kg_id: 'kg-A' }, theatreOpen: true, specs: { 'p-retail': GRAPH_B } });
  r = await h.follow('p-retail');
  check('a streaming turn keeps its graph (theatre signal)', r === 'streaming');

  // failed fetch → leave what is there
  h = harness2({ active: { kg_id: 'kg-A' }, specs: { 'p-retail': 'fail' } });
  r = await h.follow('p-retail');
  check('a failed fetch leaves the panel as it is', r === 'unavailable' && h.calls.open.length === 0 && h.els['kg-split-content'].innerHTML === '');

  // no profile → nothing
  h = harness2({ active: { kg_id: 'kg-A' } });
  r = await h.follow(null);
  check('no profile → nothing', r === 'no-profile');

  // stale response: the LAST change wins whatever order the fetches land in
  h = harness2({ active: { kg_id: 'kg-A' } });
  let releaseFirst;
  const slow = new Promise(res => { releaseFirst = res; });
  const p1 = h.api.follow('p-slow', { fetchSpec: async () => { await slow; return { spec: GRAPH_B }; } });
  const p2 = h.api.follow('p-fast', { fetchSpec: async () => ({ spec: { ...GRAPH_A, kg_id: 'kg-C', kg_name: 'c' } }) });
  const r2 = await p2; releaseFirst(); const r1 = await p1;
  check('an earlier change whose fetch lands later is dropped', r1 === 'stale' && r2 === 'switched'
        && h.calls.open.length === 1 && h.calls.open[0].kg_id === 'kg-C', `${r1}/${r2}`);

  console.log('\n[the seams]');
  // main.js emits the ONE signal from the resource-panel seam (every override/default change funnels there)
  const seam = MAIN.slice(MAIN.indexOf('async function _updateResourcePanelForProfile'), MAIN.indexOf('async function _updateResourcePanelForProfile') + 3000);
  check('main.js dispatches uderia:effective-profile from _updateResourcePanelForProfile',
        /UderiaBus\.emit\('uderia:effective-profile'/.test(seam));
  check('the signal carries the profile and the streaming flag', /profileId: profileId \|\| null/.test(seam) && /streaming:/.test(seam));
  // every composer path reaches that seam (badge show, badge hide, picker select) — by call, not by a second list
  const showBadge = MAIN.slice(MAIN.indexOf('function showActiveTagBadge'), MAIN.indexOf('function hideActiveTagBadge'));
  const hideBadge = MAIN.slice(MAIN.indexOf('function hideActiveTagBadge'), MAIN.indexOf('// In-flight memo'));
  const select = MAIN.slice(MAIN.indexOf('function selectProfile'), MAIN.indexOf('function selectProfile') + 4000);
  check('override shown → seam', /updateResourcePanelForProfile\(profile\.id\)/.test(showBadge));
  check('override removed → seam (default restored)', /updateResourcePanelForProfile\(defaultProfileId\)/.test(hideBadge));
  check('override picked from the @ selector → seam', /updateResourcePanelForProfile\(profile\.id\)/.test(select));
  // the renderer subscribes to the signal (no composer site knows the panel exists)
  check('renderer subscribes to uderia:effective-profile', SRC.includes("UderiaBus.on('uderia:effective-profile'"));
  check('the listener routes streaming into the guard', /followEffectiveProfile\(d\.profileId, \{ streaming: !!d\.streaming \}\)/.test(SRC));
  // the switcher pins; close unpins
  check('switcher selection pins the graph', /onSelect: \(id, item\) => \{ _userPinnedKgId = \(item && item\.kg_id\) \|\| null; _switchKGInPanel\(item\); \}/.test(SRC));
  const closeFn = SRC.slice(SRC.indexOf('export function closeKGSplitPanel()'), SRC.indexOf('export function closeKGSplitPanel()') + 800);
  check('closing the panel forgets the pin', /_userPinnedKgId = null;/.test(closeFn));
  // /graph-spec carries the graph's identity (both branches), so "same graph" is decidable
  // the WHOLE function (to the next route) — a fixed-size window broke when the function grew
  const _gsAt = REST.indexOf('async def kg_graph_spec');
  const gs = REST.slice(_gsAt, REST.indexOf('@rest_api_bp.route', _gsAt) > 0 ? REST.indexOf('@rest_api_bp.route', _gsAt) : _gsAt + 20000);
  check('graph-spec success payload carries kg_id + kg_name', /"kg_id": getattr\(store, "kg_id", None\),\s*\n\s*"kg_name": _kg_name,/.test(gs));
  check('graph-spec empty payload carries kg_id too', (gs.match(/"kg_id": getattr\(store, "kg_id", None\)/g) || []).length >= 2);
  check('graph-spec empty payload names the agent by tag (resolved before the empty return)',
        gs.indexOf('profile_tag = profile.get("tag", "")') < gs.indexOf('if not subgraph["entities"]:')
        && /"profile_tag": profile_tag,\s*\n\s*\}\)\s*\n\s*importance = /.test(gs));
  // the rail panel keys on the same profile the seam stores (it already followed the override)
  const RAIL = fs.readFileSync(path.join(ROOT, 'static/js/handlers/knowledgeGraphPanelHandler.js'), 'utf8');
  check('the resource-rail KG panel keys on state.currentResourcePanelProfileId (set at the seam)',
        RAIL.includes('const activeProfileId = state.currentResourcePanelProfileId;'));

  // ── the theatre's idle frame is scoped to THIS session and THIS graph ──────────
  // (a new session used to open "The last turn of this session is ready to replay" —
  //  the previous session's turn — and a followed graph offered a story told over another graph)
  console.log('\n[idle note + armed-story scope]');
  const THEATRE = fs.readFileSync(path.join(ROOT, 'static/js/handlers/kgTheatre.js'), 'utf8');
  const aStart = SRC.indexOf('let _theatreArmedFor = null;');
  const aEnd = SRC.indexOf('const _THEATRE_START');
  check('the armed-story scope + idle-note rule exist in the shipped module', aStart > 0 && aEnd > aStart);
  const ascope = SRC.slice(aStart, aEnd).replace(/^export\s+/mg, '');
  function noteHarness({ sessionId = 's1', profileId = 'p-over', sessionProfileId = 'p-own', hasTurns = false, tags = { 'p-over': 'OPTIM', 'p-own': 'IDGGT' }, activeKg = 'kg-A', turn = [], last = null } = {}) {
    const window = { __currentSessionId: sessionId, __currentProfileId: sessionProfileId, configState: { profiles: Object.entries(tags).map(([id, tag]) => ({ id, tag })) } };
    const document = { querySelector: () => (hasTurns ? {} : null) };
    const fn = new Function('window', 'document', '_activeSpec', '_theatreTurn', '_theatreLastTurnData', '_theatre',
      `${ascope}
       return { note: (c) => theatreIdleNote(c), current: (sid, kg) => armedStoryIsCurrent(sid, kg), stamp: (kg) => _stampArmedStory(kg),
                drop: () => _dropStaleArmedStory(), get turn() { return _theatreTurn; }, get last() { return _theatreLastTurnData; }, get armed() { return _theatreArmedFor; } };`);
    return fn(window, document, { profile_id: profileId, kg_id: activeKg }, turn, last, null);
  }
  // the four readings, from the facts the platform decides them from
  let n = noteHarness({ hasTurns: false, profileId: 'p-over' }).note();
  check('new session + an override active → names the override, says its graph is on stage', /new session/.test(n) && /@OPTIM/.test(n) && /on stage/.test(n));
  n = noteHarness({ hasTurns: false, profileId: 'p-own' }).note();
  check('new session under its own profile → "nothing to replay yet", names the agent', /new session/.test(n) && /nothing to replay yet/i.test(n) && /@IDGGT/.test(n));
  n = noteHarness({ hasTurns: true, profileId: 'p-over' }).note();
  check('existing session + override → the override is named, no "new session"', !/new session/.test(n) && /@OPTIM/.test(n) && /active for the next question/.test(n));
  n = noteHarness({ hasTurns: true, profileId: 'p-own' }).note();
  check('existing session, own profile, no story → "nothing to replay for this graph yet"', /Nothing to replay for this graph yet/.test(n) && /@IDGGT/.test(n));
  n = noteHarness({ hasTurns: false, profileId: 'p-unknown', tags: {} }).note();
  check('an agent with no known tag is "the agent" — never a profile id', /the agent/.test(n) && !/p-unknown/.test(n));
  check('the note never carries the replay sentence', !/ready to replay/.test(noteHarness().note()));
  // armed-story scope: session first, then graph; a story with no graph identity trusts the session
  let hs = noteHarness({ sessionId: 's1', activeKg: 'kg-A', turn: [['x', {}]] });
  hs.stamp('kg-A');
  check('a story armed in this session on this graph is current', hs.current() === true);
  check('…and reads as stale from another session', hs.current('s2') === false);
  check('…and reads as stale on another graph', hs.current('s1', 'kg-B') === false);
  hs = noteHarness({ sessionId: 's1', activeKg: null, turn: [['x', {}]] }); hs.stamp(null);
  check('a story with no graph identity is trusted on the session alone', hs.current('s1', 'kg-B') === true && hs.current('s2') === false);
  hs = noteHarness({ sessionId: 's1', activeKg: 'kg-A', turn: [['x', {}]], last: { a: 1 } }); hs.stamp('kg-A');
  check('dropping a CURRENT story is a no-op', hs.drop() === false && hs.turn.length === 1 && hs.last !== null);
  check('a story from another session is dropped: turn + last turn + owner cleared', (() => {
    const w = { __currentSessionId: 's1', __currentProfileId: 'p', configState: { profiles: [] } };
    const f = new Function('window', 'document', '_activeSpec', '_theatreTurn', '_theatreLastTurnData', '_theatre',
      `${ascope} _stampArmedStory('kg-A'); window.__currentSessionId = 's2'; const dropped = _dropStaleArmedStory(); return { dropped, t: _theatreTurn.length, l: _theatreLastTurnData, a: _theatreArmedFor };`);
    const r = f(w, { querySelector: () => null }, { kg_id: 'kg-A' }, [['x', {}]], { a: 1 }, null);
    return r.dropped === true && r.t === 0 && r.l === null && r.a === null;
  })());
  // the seams: mount drops + passes the note, open unmounts BEFORE the wipe, toggle-on never strands the stage, the live turn stamps its owner
  const mountFn = SRC.slice(SRC.indexOf('async function _mountTheatre()'), SRC.indexOf('async function _mountTheatre()') + 900);
  check('mount drops a stale story before the stage is built', mountFn.indexOf('_dropStaleArmedStory();') > 0 && mountFn.indexOf('_dropStaleArmedStory();') < mountFn.indexOf('mod.mountTheatre('));
  check('mount hands the theatre its idle note', /idleNote: theatreIdleNote\(\),/.test(mountFn));
  const openFn = SRC.slice(SRC.indexOf('export function openKGSplitPanel(spec)'), SRC.indexOf('export function openKGSplitPanel(spec)') + 2500);
  check('openKGSplitPanel unmounts a mounted theatre BEFORE wiping the content', openFn.indexOf('if (_theatre) _unmountTheatre(false);') > 0 && openFn.indexOf('if (_theatre) _unmountTheatre(false);') < openFn.indexOf("contentArea.innerHTML = ''"));
  check('a live turn start stamps the story owner', /_theatreTurn = \[\]; _stampArmedStory\(\);/.test(SRC));
  const replayFn = SRC.slice(SRC.indexOf('export function replayKGTheatre(turnData)'), SRC.indexOf('export function replayKGTheatre(turnData)') + 1400);
  check('a reloaded turn stamps its owner from the event\'s graph identity BEFORE any early return',
        replayFn.indexOf('_stampArmedStory((ev0.metadata && ev0.metadata.kg_id) || ev0.kg_id || null);') > 0
        && replayFn.indexOf('_stampArmedStory(') < replayFn.indexOf('if (!isKGTheatreEnabled() || !turnData) return false;'));
  check('opening the panel arms a remembered last turn only when it is current', /_mountTheatre\(\)\.then\(t => \{ if \(t && !_theatreTurn\.length && _theatreLastTurnData && armedStoryIsCurrent\(\)\) replayKGTheatre\(_theatreLastTurnData\); \}\);/.test(SRC));
  check('toggle-on drops a stale story and always leaves a stage (a primer last turn no longer strands it)', /_dropStaleArmedStory\(\);\s*\n\s*if \(_theatreTurn\.length\)[^\n]*\n\s*else if \(!\(_theatreLastTurnData && replayKGTheatre\(_theatreLastTurnData\)\)\) _mountTheatre\(\);/.test(SRC));
  check('the follow listener refreshes the note even when the graph stayed', /followEffectiveProfile\(d\.profileId, \{ streaming: !!d\.streaming \}\)\.then\(\(\) => _refreshTheatreIdleNote\(\)/.test(SRC));
  check('the no-graph state stands on no stage', /if \(_theatre\) _unmountTheatre\(false\);\s*\/\/ no graph, no stage/.test(SRC));
  // the theatre side: the note shows ONLY while idle with nothing armed; the replay sentence keeps precedence
  check('theatre: idle sub-line = replay sentence if armed, else the host note, never both', /!idle \? '' : \(_replaySource && _replaySource\.length\) \? 'The last turn of this session is ready to replay — use the control below\.' : \(opts\.idleNote \|\| ''\)/.test(THEATRE));
  check('theatre exposes setIdleNote', /setIdleNote\(text\) \{ opts\.idleNote = text \|\| ''; refreshChrome\(\); \}/.test(THEATRE));
  // The property, not the literal (a hardcoded `?v=1.22` broke the moment the module moved
  // again while the rule held): the renderer pins the theatre module, and whenever the
  // module's content differs from HEAD its pin differs from HEAD's pin too.
  {
    const pinOf = (src) => (src.match(/kgTheatre\.js\?v=(\d+\.\d+)/) || [])[1] || null;
    const pin = pinOf(SRC);
    let headPin = null, headTheatre = null;
    try {
      const { execSync } = await import('node:child_process');
      headPin = pinOf(execSync('git show HEAD:components/builtin/knowledge_graph/renderer.js', { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
      headTheatre = execSync('git show HEAD:static/js/handlers/kgTheatre.js', { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    } catch (_) { /* no git: consistency only */ }
    const contentMoved = headTheatre !== null && headTheatre !== THEATRE;
    check('the theatre module is pinned by the renderer, and its pin moves with its content', !!pin && (!contentMoved || pin !== headPin), `pin=${pin} head=${headPin} moved=${contentMoved}`);
  }

  console.log(`\nKG follow-profile: ${passed} passed, ${failed} failed`);
  console.log(failed ? `FAILED — ${failed} of ${passed + failed}` : `ALL PASS — ${passed} assertions`);
  process.exit(failed ? 1 : 0);
})();
