/**
 * Gate for static/js/handlers/requestCoalescer.js.
 *
 * WHY: the login path fires the same URL 2-5x within milliseconds from independent
 * panels (measured on production: /skills 5x, /components 4x, /knowledge-graph/list 4x),
 * each a full round trip on a ~1.3s-latency link.
 *
 * The two properties that make broad application safe, and that a naive version gets
 * wrong, are asserted here: it must NOT retain anything after a request settles (so it
 * can never serve stale data), and every caller must get its OWN response clone (a
 * Response body reads once — sharing one silently hands the second caller a consumed
 * stream).
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (n, c, d = '') => { c ? pass++ : (fail++, console.error(`  FAIL: ${n}${d ? ' — ' + d : ''}`)); };

// ── a Response stub with real read-once semantics ────────────────────────────
class FakeResponse {
  constructor(body, id) { this._body = body; this._used = false; this.id = id; this.ok = true; this.status = 200; }
  clone() { const c = new FakeResponse(this._body, this.id); c._cloneOf = this; return c; }
  async json() {
    if (this._used) throw new TypeError('body stream already read');
    this._used = true;
    return this._body;
  }
}

let calls, resolvers;
function installFetch() {
  calls = []; resolvers = [];
  globalThis.fetch = (url, init) => {
    calls.push({ url, method: (init?.method || 'GET').toUpperCase() });
    const id = calls.length;
    return new Promise((res) => resolvers.push(() => res(new FakeResponse({ n: id }, id))));
  };
}
const flush = () => { const r = resolvers; resolvers = []; r.forEach((f) => f()); };

globalThis.window = globalThis;
installFetch();
const SRC = 'static/js/handlers/requestCoalescer.js';
const mod = await import(`../${SRC}`);
const { coalescedFetch, inflightCount } = mod;

// ── §1 the win: concurrent identical GETs become ONE request ────────────────
{
  installFetch();
  const ps = [coalescedFetch('/api/x'), coalescedFetch('/api/x'), coalescedFetch('/api/x')];
  check('three concurrent identical GETs issue ONE request', calls.length === 1, `issued ${calls.length}`);
  flush();
  const rs = await Promise.all(ps);
  check('all three callers receive a response', rs.every(Boolean));
  check('each caller gets its OWN object, not the shared one', new Set(rs).size === 3);
  // THE detail: every caller must be able to read the body
  const bodies = await Promise.all(rs.map(r => r.json().catch(e => ({ err: String(e) }))));
  check('every caller can read the body independently',
        bodies.every(b => b && b.n === 1), JSON.stringify(bodies));
}

// ── §2 NOT a cache: nothing survives settling ───────────────────────────────
{
  installFetch();
  const p1 = coalescedFetch('/api/y'); flush(); await p1;
  check('the memo is released once the request settles', inflightCount() === 0);
  const p2 = coalescedFetch('/api/y');
  check('a later identical GET hits the network again (never stale)', calls.length === 2, `issued ${calls.length}`);
  flush(); await p2;
}

// ── §3 different URLs and different users never merge ───────────────────────
{
  installFetch();
  const ps = [coalescedFetch('/api/a'), coalescedFetch('/api/b')];
  check('different URLs are not merged', calls.length === 2);
  flush(); await Promise.all(ps);

  installFetch();
  const q = [coalescedFetch('/api/a', { headers: { Authorization: 'Bearer A' } }),
             coalescedFetch('/api/a', { headers: { Authorization: 'Bearer B' } })];
  check('two different users never share one in-flight request', calls.length === 2, `issued ${calls.length}`);
  flush(); await Promise.all(q);
}

// ── §4 POST is not merged unless it explicitly opts in ──────────────────────
{
  installFetch();
  const ps = [coalescedFetch('/api/p', { method: 'POST' }), coalescedFetch('/api/p', { method: 'POST' })];
  check('POST is NOT coalesced by default', calls.length === 2, `issued ${calls.length}`);
  flush(); await Promise.all(ps);

  installFetch();
  const qs = [coalescedFetch('/api/p', { method: 'POST' }, { coalescePost: true }),
              coalescedFetch('/api/p', { method: 'POST' }, { coalescePost: true })];
  check('POST IS coalesced when the call site opts in', calls.length === 1, `issued ${calls.length}`);
  flush();
  const rs = await Promise.all(qs);
  // Read defensively: a mutation that hands out the SHARED response makes the second
  // .json() throw "body stream already read". A gate that dies instead of NAMING that
  // is half a gate.
  const bs = await Promise.all(rs.map(r => r.json().catch(e => ({ err: String(e) }))));
  check('opted-in POST callers each get a readable body',
        bs.every(b => b && b.n === 1), JSON.stringify(bs));
}

// ── §5 a failure must not be memoised ───────────────────────────────────────
{
  calls = [];
  globalThis.fetch = () => { calls.push(1); return Promise.reject(new Error('down')); };
  await coalescedFetch('/api/z').catch(() => {});
  check('the memo is released after a failure', inflightCount() === 0);
  await coalescedFetch('/api/z').catch(() => {});
  check('a failure is not memoised — the next call retries', calls.length === 2, `issued ${calls.length}`);
}

// ── §6 structural: the properties that keep this safe ───────────────────────
const src = readFileSync(SRC, 'utf8');
check('callers receive a clone, never the shared response', /shared\.then\(\(r\) => r\.clone\(\)\)/.test(src));
check('the memo is released only by its owner',
      /if \(_inflight\.get\(key\) === shared\) _inflight\.delete\(key\)/.test(src));
check('the auth token is part of the key', /Authorization/.test(src));
check('no result cache is retained (the map holds promises only)',
      !/_cache\s*=|localStorage/.test(src));

if (fail) { console.log(`\n${fail} FAILED — ${pass} passed (request coalescer).`); process.exit(1); }
console.log(`\nALL PASS — ${pass} assertions (overlapping requests merge; nothing is cached).`);
