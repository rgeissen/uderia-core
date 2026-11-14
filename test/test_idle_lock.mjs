// Idle lock (static/js/idleLock.js) executed under a minimal DOM stub.
// Run: node test/test_idle_lock.mjs
import fs from 'node:fs';
import vm from 'node:vm';

let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log(`FAIL: ${n} ${d}`); } };
const src = fs.readFileSync(new URL('../static/js/idleLock.js', import.meta.url), 'utf8');

function harness(minutes) {
  let now = 1_000_000;
  const store = { tda_auth_token: 'tok' };
  const listeners = {};
  let tick = null, loggedOut = false, href = null, body = [];
  const mkEl = () => {
    const el = { style: {}, attrs: {}, children: [], textContent: '', innerHTML: '',
      setAttribute(k, v) { this.attrs[k] = v; },
      remove() { body = body.filter(x => x !== el); },
      querySelector() { return sub; }, addEventListener(e, f) { this['on' + e] = f; }, focus() {} };
    return el;
  };
  const sub = { textContent: '', addEventListener(e, f) { sub.onclick = f; }, focus() {} };
  const ctx = {
    Date: { now: () => now }, Number, Math, String,
    setInterval: (f) => { tick = f; },
    localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } },
    document: { createElement: () => mkEl(), body: { appendChild: el => body.push(el) } },
    window: {
      UDERIA_SESSION_IDLE_MINUTES: minutes,
      addEventListener: (e, f) => { (listeners[e] ||= []).push(f); },
      authClient: { logout: async () => { loggedOut = true; delete store.tda_auth_token; } },
      location: { set href(v) { href = v; }, get href() { return href; } },
    },
  };
  ctx.window.localStorage = ctx.localStorage;
  vm.runInNewContext(src, ctx);
  return {
    advance(ms) { now += ms; tick && tick(); },
    input(ev = 'keydown') { (listeners[ev] || []).forEach(f => f()); },
    other(tsOffset) { store.uderia_last_activity = String(now + tsOffset); },
    get banner() { return body.length > 0; }, get loggedOut() { return loggedOut; },
    get href() { return href; }, get armed() { return tick !== null; }, sub,
  };
}

const off = harness(0);
check('limit 0: not armed', !off.armed);

const h = harness(30);
check('limit 30: armed', h.armed);
h.advance(28 * 60_000);
check('no warning at 28 min', !h.banner);
h.advance(90_000);
check('warning in the last minute', h.banner);
check('warning counts down', /signed out in \d+ second/.test(h.sub.textContent), h.sub.textContent);
h.input('keydown');
check('input clears the warning', !h.banner);
h.advance(29 * 60_000);
check('input reset the clock', !h.loggedOut);
h.advance(60_000 + 5_000);
await new Promise(r => setImmediate(r));
check('signed out at the limit', h.loggedOut);
check('redirected with reason', h.href === '/login?reason=idle', h.href);

const m = harness(30);
m.advance(25 * 60_000);
m.other(0);              // activity in another tab, now
m.advance(10 * 60_000);  // 35 min after this tab's last input, 10 after the other tab's
await new Promise(r => setImmediate(r));
check('activity in another tab keeps this tab signed in', !m.loggedOut);

console.log(fail ? `\n${pass} passed, ${fail} failed` : `ALL PASS — ${pass} assertions`);
process.exit(fail ? 1 : 0);
