/**
 * Gate for the in-flight promise memo in static/js/handlers/trustBadge.js.
 *
 * WHY THIS EXISTS: renderProfiles() paints four IFOC containers back-to-back and each
 * calls mountAgentBadges() -> fetchAll(). All four land before the first response, so a
 * RESULT-only cache misses every time and fires four identical /v1/trust/agents
 * requests. That endpoint resolves every profile synchronously, so the duplicates
 * serialise on the server event loop and cost ~4x one call on the LOGIN CRITICAL PATH
 * (measured: 1 call 0.5s, 4 concurrent 2.27s).
 *
 * The gate executes the SHIPPED module under a DOM/fetch stub — it does not re-implement
 * the logic. No server, no network.
 */
import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
  if (cond) { pass++; }
  else { fail++; console.error(`  FAIL: ${name}${detail ? ' — ' + detail : ''}`); }
};

// ── minimal environment the module needs at import time ──────────────────────
function makeEnv({ token = 'tokA' } = {}) {
  const calls = [];
  let resolvers = [];
  const store = { tda_auth_token: token };
  const g = globalThis;
  g.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; },
    removeItem: (k) => { delete store[k]; },
  };
  const el = () => ({
    style: {}, classList: { add(){}, remove(){}, contains(){ return false; } },
    setAttribute(){}, getAttribute(){ return null; }, appendChild(){}, remove(){},
    addEventListener(){}, querySelector(){ return null; }, querySelectorAll(){ return []; },
    innerHTML: '', textContent: '', dataset: {}, isConnected: true,
  });
  g.document = {
    getElementById: () => null,
    createElement: () => el(),
    querySelector: () => null,
    querySelectorAll: () => [],
    head: el(), body: el(),
    addEventListener(){},
  };
  g.window = g;
  g.fetch = (url) => {
    calls.push(String(url));
    return new Promise((res) => {
      resolvers.push(() => res({
        ok: true,
        json: async () => ({ agents: [{ profile_id: 'p1', state: 'trusted', composition_state: 'trusted', ingredients: [], blocking: [] }] }),
      }));
    });
  };
  return {
    calls,
    flush: () => { const r = resolvers; resolvers = []; r.forEach((f) => f()); },
    setToken: (t) => { store.tda_auth_token = t; },
  };
}

const SRC = 'static/js/handlers/trustBadge.js';
const src = readFileSync(SRC, 'utf8');
check('module source is present', src.length > 1000);

// The module is an IIFE publishing window.TrustBadge; import it fresh per scenario.
async function loadModule(env) {
  const mod = `data:text/javascript;base64,${Buffer.from(src).toString('base64')}`;
  await import(mod + `#${Math.random()}`);
  return globalThis.window.TrustBadge;
}

// ── §1 the defect: concurrent callers must collapse to ONE request ───────────
{
  const env = makeEnv();
  const TB = await loadModule(env);
  check('TrustBadge is published on window', !!TB && typeof TB.fetchAll === 'function');

  const before = env.calls.length;
  const ps = [TB.fetchAll(), TB.fetchAll(), TB.fetchAll(), TB.fetchAll()];
  const issued = env.calls.length - before;
  check('four concurrent fetchAll() issue exactly ONE request', issued === 1, `issued ${issued}`);

  env.flush();
  const results = await Promise.all(ps);
  check('all four callers receive the payload', results.every(r => r && r.agents && r.agents.length === 1));
  check('all four receive the SAME resolved value', results.every(r => r === results[0]));

  // after settle, the result cache serves without any new request
  const afterSettle = env.calls.length;
  const again = await TB.fetchAll();
  check('a later call is served from the result cache (no new request)',
        env.calls.length === afterSettle, `issued ${env.calls.length - afterSettle}`);
  check('cached value equals the fetched value', again === results[0]);
}

// ── §2 no regression: force:true must still bypass BOTH caches ───────────────
{
  const env = makeEnv();
  const TB = await loadModule(env);
  const p1 = TB.fetchAll(); env.flush(); await p1;
  const n = env.calls.length;
  const p2 = TB.fetchAll({ force: true });
  check('force:true bypasses the result cache', env.calls.length === n + 1);
  env.flush(); await p2;
}

// ── §3 no regression: invalidate() drops the memo, next call refetches ───────
{
  const env = makeEnv();
  const TB = await loadModule(env);
  const p1 = TB.fetchAll(); env.flush(); await p1;
  const n = env.calls.length;
  TB.invalidate();
  const p2 = TB.fetchAll();
  check('invalidate() forces a fresh request', env.calls.length === n + 1);
  env.flush(); await p2;
}

// ── §4 TC-87: a user switch must not serve the previous user's data ──────────
{
  const env = makeEnv({ token: 'tokA' });
  const TB = await loadModule(env);
  const p1 = TB.fetchAll(); env.flush(); await p1;
  const n = env.calls.length;
  env.setToken('tokB');
  const p2 = TB.fetchAll();
  check('a changed auth token forces a fresh request', env.calls.length === n + 1);
  env.flush(); await p2;
}

// ── §5 the memo must not survive its own failure (no poisoned cache) ─────────
{
  const env = makeEnv();
  const TB = await loadModule(env);
  globalThis.fetch = () => Promise.reject(new Error('network down'));
  const r = await TB.fetchAll();
  check('a failed fetch degrades to an empty agent list', r && Array.isArray(r.agents) && r.agents.length === 0);
  // restore a working fetch: the failure must NOT have been memoised
  let issued = 0;
  globalThis.fetch = () => { issued++; return Promise.resolve({ ok: true, json: async () => ({ agents: [{ profile_id: 'p9', state: 'unsigned' }] }) }); };
  const r2 = await TB.fetchAll();
  check('a failure is not memoised — the next call retries', issued === 1, `issued ${issued}`);
  // Read defensively: a mutation that memoises the failure returns the empty list here.
  // A gate that dies on a mutation instead of NAMING it is half a gate.
  check('the retry returns real data', r2?.agents?.[0]?.profile_id === 'p9',
        `got ${JSON.stringify(r2)}`);
}

// ── §6 fetchAgent gets the same treatment, per profile id ───────────────────
{
  const env = makeEnv();
  const TB = await loadModule(env);
  globalThis.fetch = (url) => { env.calls.push(String(url));
    return Promise.resolve({ ok: true, json: async () => ({ trust: { state: 'trusted' } }) }); };
  const before = env.calls.length;
  const ps = [TB.fetchAgent('pX'), TB.fetchAgent('pX'), TB.fetchAgent('pY')];
  await Promise.all(ps);
  const issued = env.calls.length - before;
  check('concurrent fetchAgent dedupes per id (2 ids -> 2 requests)', issued === 2, `issued ${issued}`);
}

// ── §7 structural: the memo is declared and cleared where it must be ─────────
check('an in-flight memo exists for fetchAll', /_allInflight/.test(src));
check('the memo is cleared on a user change',
      /_cacheToken = t;[\s\S]{0,200}_allInflight = null/.test(src));
check('the memo is cleared by invalidate()',
      /function invalidate[\s\S]{0,300}_allInflight = null/.test(src));
check('the memo is only released by its owner (no blind null)',
      /if \(_allInflight === p\) _allInflight = null/.test(src));

if (fail) {
  console.log(`\n${fail} FAILED — ${pass} passed (trustBadge in-flight dedupe).`);
  process.exit(1);
}
console.log(`\nALL PASS — ${pass} assertions (one in-flight request, not four).`);
