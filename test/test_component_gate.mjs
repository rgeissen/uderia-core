/**
 * Executes the SHIPPED static/js/handlers/componentGate.js under a stubbed browser.
 *
 * Why executed and not grepped: the first version of this rule was a substring check
 * ("new Map()" present), and a mutation that made the gate fail OPEN sailed through it
 * because an unrelated line satisfied the same substring. A gate that cannot fail its own
 * rule is not a gate — so this drives the real module and asserts BEHAVIOUR.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

let passed = 0, failed = 0;
const check = (name, cond, detail = '') => {
  if (cond) passed++;
  else { failed++; console.log(`  FAIL: ${name}${detail ? ' — ' + detail : ''}`); }
};

function loadGate(fetchImpl) {
  const win = {};
  const ctx = vm.createContext({
    window: win,
    localStorage: { getItem: () => 'tok' },
    fetch: fetchImpl,
    console: { warn() {}, log() {}, error() {} },
    Promise, Map, Set, encodeURIComponent, Error, JSON,
  });
  vm.runInContext(readFileSync('static/js/handlers/componentGate.js', 'utf8'), ctx);
  return win.ComponentGate;
}

const okResponse = (components) => Promise.resolve({
  ok: true, json: () => Promise.resolve({ components }),
});

// --- resolves the server's answer, per component -----------------------------
{
  const G = loadGate(() => okResponse([
    { component_id: 'dashboard', profile_enabled: false },
    { component_id: 'chart', profile_enabled: true },
  ]));
  check('gate module is exported on window', !!G);
  await G.isEnabled('dashboard', 'p1').then(v =>
    check('disabled component -> false', v === false, String(v)));
  await G.isEnabled('chart', 'p1').then(v =>
    check('enabled component -> true', v === true, String(v)));
  await G.isEnabled('flow', 'p1').then(v =>
    check('component absent from payload -> false', v === false, String(v)));
  check('peek reads the cache synchronously', G.peek('chart', 'p1') === true);
  check('peek on an unknown profile is undefined (not false)',
        G.peek('chart', 'other') === undefined);
}

// --- FAILS CLOSED, and a failure is not cached -------------------------------
{
  let calls = 0;
  const G = loadGate(() => {
    calls++;
    return calls === 1 ? Promise.reject(new Error('network'))
                       : okResponse([{ component_id: 'dashboard', profile_enabled: true }]);
  });
  const first = await G.isEnabled('dashboard', 'p1');
  check('lookup failure -> false (fails CLOSED)', first === false, String(first));
  const second = await G.isEnabled('dashboard', 'p1');
  check('a failed lookup is NOT cached — the retry gets the real answer',
        second === true, String(second));
  check('the retry actually re-fetched', calls === 2, `calls=${calls}`);
}

// --- an HTTP error is a failure, not an empty allow ---------------------------
{
  const G = loadGate(() => Promise.resolve({ ok: false, status: 403 }));
  check('HTTP error -> false', (await G.isEnabled('dashboard', 'p1')) === false);
}

// --- missing inputs never produce an affordance -------------------------------
{
  const G = loadGate(() => okResponse([{ component_id: 'dashboard', profile_enabled: true }]));
  check('no profileId -> false', (await G.isEnabled('dashboard', '')) === false);
  check('no componentId -> false', (await G.isEnabled('', 'p1')) === false);
}

// --- concurrent callers share one in-flight request ---------------------------
{
  let calls = 0;
  const G = loadGate(() => { calls++; return okResponse([{ component_id: 'chart', profile_enabled: true }]); });
  await Promise.all([G.isEnabled('chart', 'p1'), G.isEnabled('chart', 'p1'), G.prime('p1')]);
  check('concurrent lookups coalesce into one fetch', calls === 1, `calls=${calls}`);
}

// --- invalidate forces a re-read (profile save changed componentConfig) -------
{
  let calls = 0;
  const G = loadGate(() => {
    calls++;
    return okResponse([{ component_id: 'dashboard', profile_enabled: calls > 1 }]);
  });
  check('before save: disabled', (await G.isEnabled('dashboard', 'p1')) === false);
  check('cached: no second fetch', (await G.isEnabled('dashboard', 'p1')) === false && calls === 1);
  G.invalidate('p1');
  check('after invalidate: re-read picks up the change',
        (await G.isEnabled('dashboard', 'p1')) === true, `calls=${calls}`);
}

if (failed) {
  console.log(`\nFAILED — ${failed} of ${passed + failed} assertions`);
  process.exit(1);
}
// The battery's canonical JS result line: `ALL PASS — N assertions (…)`. run_js REQUIRES
// this exact shape — a bare "N passed" reads as NO RESULT LINE and the battery goes red
// on coverage-unverifiable, by design.
console.log(`\nALL PASS — ${passed} assertions (the shipped component gate fails closed and honours the server's answer).`);
process.exit(0);
