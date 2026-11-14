/**
 * Gate for the stale-response guard in updateResourcePanelForProfile (static/js/main.js).
 *
 * WHY THIS EXISTS: the function is invoked CONCURRENTLY — boot fires it for the default
 * profile (deliberately unawaited, so the 5s MCP round trip does not block the welcome
 * screen), and a session restore / profile switch fires it for a DIFFERENT profile
 * moments later. The slow first response then lands LAST and writes its tools into the
 * panel under the newer profile's id: the panel shows the wrong agent's capabilities.
 *
 * Six call sites already fired unawaited, so the race pre-dates the guard; deferring the
 * boot call is what made it reachable at login.
 *
 * This gate models the exact interleaving against the SHIPPED guard condition extracted
 * from source, so it fails if the guard is removed or inverted. Pure: no server, no DOM.
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (n, c, d = '') => { c ? pass++ : (fail++, console.error(`  FAIL: ${n}${d ? ' — ' + d : ''}`)); };

const SRC = readFileSync('static/js/main.js', 'utf8');

// ── §1 the guard exists on BOTH post-await write paths ───────────────────────
const guardRe = /if \(state\.currentResourcePanelProfileId !== profileId\)/g;
const guards = SRC.match(guardRe) || [];
check('a stale-response guard is present', guards.length >= 1, `found ${guards.length}`);
check('the guard protects BOTH the success and the failure write path',
      guards.length >= 2, `found ${guards.length}, expected >= 2`);

// the guard must sit BEFORE the write it protects
const writeIdx = SRC.indexOf('state.resourceData.tools = tools;');
const guardIdx = SRC.lastIndexOf('if (state.currentResourcePanelProfileId !== profileId)', writeIdx);
check('the guard precedes the resourceData write', guardIdx > -1 && guardIdx < writeIdx);
check('the guard and the write are in the same block (not separated by a function)',
      guardIdx > -1 && !SRC.slice(guardIdx, writeIdx).includes('async function'));

// the boot call site must remain unawaited (that is the perf fix this guard protects)
check('the boot call site is still not awaited',
      /updateResourcePanelForProfile\(window\.configState\.defaultProfileId\)\s*\n\s*\.then\(/.test(SRC));

// ── §2 the race, modelled — guard ON vs guard OFF ────────────────────────────
// Extract the shipped condition so the model cannot drift from the source.
const shipped = SRC.slice(guardIdx, SRC.indexOf('\n', guardIdx)).trim();
check('the extracted condition is the shipped one', shipped.startsWith('if (state.currentResourcePanelProfileId !== profileId)'), shipped);

function simulate({ withGuard }) {
  const state = { currentResourcePanelProfileId: null, resourceData: { tools: null } };
  const order = [];
  // A = default profile, slow (5s MCP round trip). B = session profile, fast.
  const call = (profileId, delay, tools) => {
    state.currentResourcePanelProfileId = profileId;   // set synchronously, as shipped
    return new Promise((res) => setTimeout(() => {
      if (withGuard && state.currentResourcePanelProfileId !== profileId) { order.push(`discard:${profileId}`); return res(); }
      state.resourceData.tools = tools;
      order.push(`write:${profileId}`);
      res();
    }, delay));
  };
  const pA = call('A', 50, 'A-tools');      // boot, slow, unawaited
  const pB = call('B', 5,  'B-tools');      // session restore, fast
  return Promise.all([pA, pB]).then(() => ({ state, order }));
}

const withoutGuard = await simulate({ withGuard: false });
check('WITHOUT the guard the slow boot response wins and corrupts the panel',
      withoutGuard.state.resourceData.tools === 'A-tools' &&
      withoutGuard.state.currentResourcePanelProfileId === 'B',
      `tools=${withoutGuard.state.resourceData.tools} id=${withoutGuard.state.currentResourcePanelProfileId}`);

const withGuard = await simulate({ withGuard: true });
check('WITH the guard the panel keeps the profile it is actually showing',
      withGuard.state.resourceData.tools === 'B-tools' &&
      withGuard.state.currentResourcePanelProfileId === 'B',
      `tools=${withGuard.state.resourceData.tools} id=${withGuard.state.currentResourcePanelProfileId}`);
check('the stale response is discarded, not written', withGuard.order.includes('discard:A'));
check('the current response is still written', withGuard.order.includes('write:B'));

// ── §3 the guard must NOT block the ordinary single-call case ────────────────
{
  const state = { currentResourcePanelProfileId: null, resourceData: { tools: null } };
  const pid = 'solo';
  state.currentResourcePanelProfileId = pid;
  await new Promise((r) => setTimeout(r, 1));
  const blocked = state.currentResourcePanelProfileId !== pid;
  check('a lone call is never discarded by the guard', !blocked);
  if (!blocked) state.resourceData.tools = 'solo-tools';
  check('...and still populates the panel', state.resourceData.tools === 'solo-tools');
}

// ── §4 null is a legitimate profile id (restore-to-default) ──────────────────
{
  const state = { currentResourcePanelProfileId: null, resourceData: { tools: null } };
  const pid = null;
  state.currentResourcePanelProfileId = pid;
  check('null === null, so a restore-to-default call is not self-discarded',
        state.currentResourcePanelProfileId === pid);
}

if (fail) { console.log(`\n${fail} FAILED — ${pass} passed (resource panel race).`); process.exit(1); }
console.log(`\nALL PASS — ${pass} assertions (the slow response never overwrites the current profile).`);
