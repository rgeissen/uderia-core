#!/usr/bin/env node
/**
 * KG execution theatre — the executed-DOM gate.
 *
 * The pure gate (test_kg_theatre.mjs) proves the reducer and the label-collision resolver.
 * This one proves what a viewer SEES: the shipped module is mounted in a real Chrome over
 * the demo turn (test/kg_theatre_harness) and, at the receipt, the prevented beat and the
 * Inspect depth, every rendered text is measured — no two may overlap ("the explanation
 * visual does not have overlapping text segments" is the operator's requirement), the
 * receipt card / barrier pill / trail are present, and the console is clean.
 *
 * Needs Google Chrome (or chromium on PATH) and network for the d3 CDN the app itself uses;
 * it runs on wall-clock time (~40 s), so it is a MANUAL gate — not in trust_battery.sh.
 * Fit is mandatory, so every scenario also asserts the camera scale stays within [0.2, 1.2], the
 * question and statement strip lie on the stage, and (where declared) the subject areas stack in a
 * narrow panel; scenarios drive resizes (narrow, portrait, short, very wide) and multi-statement turns.
 * KGT_ONLY=<regex> runs a subset; KGT_MODULE=<path> serves a mutated copy (mutation-testing the gate).
 * Skips honestly (exit 0, "SKIPPED") when no Chrome is found.
 */
import { spawn, execSync } from 'node:child_process';
import fs from 'node:fs'; import http from 'node:http'; import path from 'node:path'; import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const HARNESS = path.join(HERE, 'kg_theatre_harness');
const MODULE = process.env.KGT_MODULE || path.join(ROOT, 'static/js/handlers/kgTheatre.js');   // KGT_MODULE: a mutated copy, for mutation-testing the gate itself
const ONLY = process.env.KGT_ONLY ? new RegExp(process.env.KGT_ONLY) : null;                     // KGT_ONLY: run the scenarios whose name matches
const PROBE = fs.readFileSync(path.join(HARNESS, 'overlap_probe.js'), 'utf8');

function findChrome() {
  const cands = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', process.env.CHROME_BIN].filter(Boolean);
  for (const c of cands) if (fs.existsSync(c)) return c;
  for (const n of ['google-chrome', 'chromium', 'chromium-browser']) { try { return execSync(`which ${n}`).toString().trim(); } catch (_) {} }
  return null;
}
const chromeBin = findChrome();
if (!chromeBin) { console.log('SKIPPED — no Chrome found (set CHROME_BIN to run the executed-DOM gate)'); process.exit(0); }

let passed = 0, failed = 0; const fails = [];
const check = (name, ok, detail = '') => { if (ok) passed++; else { failed++; fails.push(`${name}${detail ? ' — ' + detail : ''}`); } };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// serve the harness, mapping ./kgTheatre.js to the SHIPPED module (no copy that can drift)
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x'); let f = u.pathname === '/kgTheatre.js' ? MODULE : path.join(HARNESS, u.pathname === '/' ? 'index.html' : u.pathname);
  if (!fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': f.endsWith('.js') ? 'text/javascript' : 'text/html', 'cache-control': 'no-store' }); fs.createReadStream(f).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r)); const port = server.address().port;

const SNAP_FRAME = `(() => JSON.stringify({ entOps: Object.fromEntries([...document.querySelectorAll('.kgt-stage svg g.kgt-ent')].map(g => [g.__data__?.name?.split('.').pop(), +(g.getAttribute('opacity') ?? 1)])), rels: [...document.querySelectorAll('.kgt-stage svg g.kgt-rel')].map(g => +(g.getAttribute('opacity') ?? 1)), threads: document.querySelectorAll('.kgt-stage svg g.kgt-threads path').length, qFocus: !!document.querySelector('.kgt-question.kgt-q-focus') }))()`;
async function frame(query, waitMs, evalExpr, resizeTo, clickSel, clickWait, hoverSel, zoomFirst, hoverOut, hoverBeforeClick, snapAfterClick) {
  const dbg = 9400 + Math.floor(Math.random() * 400);
  const chrome = spawn(chromeBin, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--window-size=1180,720', `--remote-debugging-port=${dbg}`, `--user-data-dir=${fs.mkdtempSync('/tmp/kgt-')}`, 'about:blank'], { stdio: 'ignore' });
  let ws; const logs = [];
  try {
    let tgt; for (let i = 0; i < 60 && !tgt; i++) { await sleep(200); try { tgt = (await (await fetch(`http://127.0.0.1:${dbg}/json`)).json()).find(t => t.type === 'page'); } catch (_) {} }
    if (!tgt) throw new Error('chrome did not expose a page target');
    ws = new WebSocket(tgt.webSocketDebuggerUrl); await new Promise(r => ws.onopen = r);
    let id = 0; const pending = {};
    ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending[m.id]) { pending[m.id](m); delete pending[m.id]; } if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') logs.push(m.params.args.map(a => a.value || a.description).join(' ')); if (m.method === 'Runtime.exceptionThrown') logs.push('EXC ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text)); };
    const send = (method, params = {}) => new Promise(r => { const i = ++id; pending[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
    await send('Runtime.enable'); await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: 1180, height: 720, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: `http://127.0.0.1:${port}/index.html?${query}` }); await sleep(waitMs);
    if (resizeTo) { await send('Emulation.setDeviceMetricsOverride', { width: resizeTo[0], height: resizeTo[1], deviceScaleFactor: 1, mobile: false }); await sleep(2000); }   // a second panel opened beside it
    if (zoomFirst) { await send('Runtime.evaluate', { expression: `(() => { const svg = document.querySelector('.kgt-stage svg'); const r = svg.getBoundingClientRect(); for (let i = 0; i < 6; i++) svg.dispatchEvent(new WheelEvent('wheel', { deltaY: -120, clientX: r.left + 200, clientY: r.top + 300, bubbles: true, cancelable: true })); return 'zoomed'; })()`, returnByValue: true }); await sleep(600); }
  if (hoverBeforeClick) { await send('Runtime.evaluate', { expression: `(() => { const el = document.querySelector(${JSON.stringify(hoverBeforeClick)}); if (!el) return 'missing'; el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); return 'hovered'; })()`, returnByValue: true }); await sleep(400); }   // the cursor is ON the row it is about to click
  for (const [i, sel] of (Array.isArray(clickSel) ? clickSel : clickSel ? [clickSel] : []).entries()) { await send('Runtime.evaluate', { expression: `(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return 'missing'; el.click(); return 'clicked'; })()`, returnByValue: true }); const last = i === (Array.isArray(clickSel) ? clickSel.length : 1) - 1; await sleep(last ? (clickWait || 1200) : 700); }
  let afterClick = null;
  if (hoverSel) { await send('Runtime.evaluate', { expression: `(() => { const el = document.querySelector(${JSON.stringify(hoverSel)}); if (!el) return 'missing'; el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); return 'hovered'; })()`, returnByValue: true }); await sleep(600); }
  if (snapAfterClick) afterClick = JSON.parse((await send('Runtime.evaluate', { expression: SNAP_FRAME, returnByValue: true })).result?.result?.value || 'null');   // what the viewer saw with the cursor still on the (re-rendered) row
  if (hoverOut) { await send('Runtime.evaluate', { expression: `(() => { const el = document.querySelector(${JSON.stringify(hoverOut)}); if (!el) return 'missing'; el.dispatchEvent(new MouseEvent('mouseout', { bubbles: true, relatedTarget: document.body })); return 'left'; })()`, returnByValue: true }); await sleep(800); }
    const r = await send('Runtime.evaluate', { expression: evalExpr, returnByValue: true });
    return { value: r.result?.result?.value, logs, afterClick };
  } finally { try { ws && ws.close(); } catch (_) {} chrome.kill(); }
}
const PROBE_ALL = `(() => { const o = ${PROBE};
  const t = [...document.querySelectorAll('.kgt-stage svg text, .kgt-trail .kgt-tr, .kgt-cap-main')].map(e => e.textContent.trim());
  const st = document.querySelector('.kgt-stage').getBoundingClientRect();
  const within = (r) => r.width > 0 && r.left >= st.left - 2 && r.right <= st.right + 2 && r.top >= st.top - 2 && r.bottom <= st.bottom + 2;
  const tf = (document.querySelector('.kgt-stage svg g.kgt-main') || {}).getAttribute?.('transform') || '';
  const k = +(tf.match(/scale\\(([^)]+)\\)/) || [0, 1])[1];
  const areas = [...document.querySelectorAll('.kgt-stage svg g.kgt-area')].filter(g => +(g.getAttribute('opacity') ?? 1) > 0.3).map(g => g.querySelector('rect').getBoundingClientRect());
  let sideBySide = areas.length >= 2; for (let i = 0; i < areas.length && sideBySide; i++) for (let j = i + 1; j < areas.length; j++) { const a = areas[i], b = areas[j]; if (a.left < b.right - 1 && a.right - 1 > b.left) sideBySide = false; }
  const stacked = areas.length >= 2 && !sideBySide;
  const qEl = document.querySelector('.kgt-stage svg g.kgt-question'); const qIn = qEl ? within(qEl.getBoundingClientRect()) : null;
  const sEl = document.querySelector('.kgt-strip'); const sOn = !!(sEl && sEl.classList.contains('on')); const sIn = sOn ? within(sEl.getBoundingClientRect()) : null;
  return JSON.stringify({ probe: JSON.parse(o), texts: t, err: document.getElementById('err').textContent, caption: document.querySelector('.kgt-cap-main').textContent,
    chips: document.querySelectorAll('.kgt-strip .st').length, stRows: document.querySelectorAll('.kgt-tr-st').length, stAhead: document.querySelectorAll('.kgt-tr-st.ahead').length,
    strip: sEl?.querySelector('.sql')?.textContent || '', k, areas: areas.length, stacked, qIn, sOn, sIn, stage: [Math.round(st.width), Math.round(st.height)],
    activeBeats: [...document.querySelectorAll('.kgt-trail .kgt-tr.active')].map(e => e.dataset.beat),
    idle: getComputedStyle(document.querySelector('.kgt-empty')).display !== 'none', idleSub: document.querySelector('.kgt-empty-sub')?.textContent || '',
    idleCard: (() => { const c = document.querySelector('.kgt-empty-card'), m = document.querySelector('.kgt-empty-main'); if (!c || !m) return null; const cs = getComputedStyle(c), ms = getComputedStyle(m); const r = c.getBoundingClientRect(), rail = document.querySelector('.kgt-rail'), rr = rail && getComputedStyle(rail).display !== 'none' ? rail.getBoundingClientRect() : null; return { bg: cs.backgroundColor, border: cs.borderTopWidth, size: parseFloat(ms.fontSize), color: ms.color, right: r.right, left: r.left, railLeft: rr ? rr.left : null }; })(),
    replayShown: getComputedStyle(document.querySelector('.kgt-replay')).display !== 'none', replayLabel: document.querySelector('.kgt-replay-l')?.textContent || '',
    ribbonActive: [...document.querySelectorAll('.kgt-ribbon .kgt-beat.active')].map(e => e.textContent.trim().toLowerCase()),
    ribbonDone: [...document.querySelectorAll('.kgt-ribbon .kgt-beat.done')].map(e => e.textContent.trim().toLowerCase()),
    ribbonAhead: [...document.querySelectorAll('.kgt-ribbon .kgt-beat.ahead')].map(e => e.textContent.trim().toLowerCase()),
    heldTs: document.querySelector('.kgt-trail .kgt-tr.held .ts')?.textContent || '',
    trail: [...document.querySelectorAll('.kgt-trail .kgt-tr')].map(e => ({ beat: e.dataset.beat, state: ['done', 'queued', 'absent', 'pending'].find(c => e.classList.contains(c)) || '?', sub: e.querySelector('.s')?.textContent || '', ts: e.querySelector('.ts')?.textContent || '' })),
    stWords: [...document.querySelectorAll('.kgt-tr-st .w')].map(e => e.textContent),
    legendOn: getComputedStyle(document.querySelector('.kgt-legend')).display !== 'none', legendItems: document.querySelectorAll('.kgt-legend .it').length, legendGroups: document.querySelectorAll('.kgt-legend .gl').length,
    legendSwatches: document.querySelectorAll('.kgt-legend .it svg, .kgt-legend .it .sv').length, keyPressed: document.querySelector('.kgt-key')?.getAttribute('aria-pressed'),
    explainOn: getComputedStyle(document.querySelector('.kgt-explain')).display !== 'none', explain: document.querySelector('.kgt-explain')?.textContent || '', explainPressed: document.querySelector('.kgt-explain-btn')?.getAttribute('aria-pressed'),
    prefs: window.__t ? window.__t.prefs : null,
    heldRow: document.querySelector('.kgt-trail .kgt-tr.held')?.dataset.beat || null, previewRow: document.querySelector('.kgt-trail .kgt-tr.preview')?.dataset.beat || null,
    entOps: Object.fromEntries([...document.querySelectorAll('.kgt-stage svg g.kgt-ent')].map(g => [g.__data__?.name?.split('.').pop(), +(g.getAttribute('opacity') ?? 1)])),
    rels: [...document.querySelectorAll('.kgt-stage svg g.kgt-rel')].map(g => +(g.getAttribute('opacity') ?? 1)),
    capSub: document.querySelector('.kgt-cap-sub')?.textContent || '',
    stageBeat: window.__t ? window.__t.stageBeat : null, pending: window.__t ? window.__t.pending : null,
    ents: document.querySelectorAll('.kgt-stage svg g.kgt-ent').length,
    litEnts: [...document.querySelectorAll('.kgt-stage svg g.kgt-ent')].filter(g => +(g.getAttribute('opacity') ?? 1) >= 0.99).length,
    dimEnts: [...document.querySelectorAll('.kgt-stage svg g.kgt-ent')].filter(g => +(g.getAttribute('opacity') ?? 1) < 0.3).length,
    threads: document.querySelectorAll('.kgt-stage svg g.kgt-threads path').length,
    qFocus: !!document.querySelector('.kgt-question.kgt-q-focus'), vFocus: !!document.querySelector('svg.kgt-v-focus'),
    termOp: [...document.querySelectorAll('.kgt-question text[data-term="1"]')].map(e => +getComputedStyle(e).opacity),
    otherOp: [...document.querySelectorAll('.kgt-question text[data-term="0"]')].map(e => +getComputedStyle(e).opacity),
    perimOp: +(getComputedStyle(document.querySelector('.kgt-perimeter') || document.body).opacity),
    entOp: [...document.querySelectorAll('.kgt-stage svg g.kgt-ent')].slice(0, 3).map(e => +getComputedStyle(e).opacity) }); })()`;

const scenarios = [
  { name: 'watch receipt', q: 'upto=9&gap=600&end=1', wait: 12000, expect: ['facts applied', 'Trusted scope'], caption: /grain of Offence/ },
  { name: 'inspect receipt', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, expect: ['facts applied', 'IMPLEMENTS', 'Receipt'], caption: /grain of Offence/ },
  { name: 'prevented', q: 'upto=8&gap=600&mode=cross', wait: 11000, expect: ['stopped before the database'], caption: /Stopped a join across subject areas/ },
  { name: 'after reset (the app path)', q: 'upto=9&gap=600&end=1&reset=1', wait: 12000, expect: ['facts applied', 'Trusted scope', 'robberies'], caption: /grain of Offence/ },
  { name: 'wide row (stacks and wraps to fit)', q: 'upto=9&gap=600&end=1&wide=1', wait: 12000, expect: ['Weapon', 'Trusted scope'], caption: /grain of Offence/ },
  { name: 'resized to a narrow panel (re-laid out, re-fitted)', q: 'upto=9&gap=600&end=1&wide=1', wait: 12000, resizeTo: [620, 760], expect: ['Weapon', 'Trusted scope', '2023?', 'facts applied'], caption: /grain of Offence/, stacked: true },
  { name: 'rotated to portrait (question wraps, marks follow)', q: 'upto=9&gap=600&end=1&wide=1', wait: 12000, resizeTo: [760, 1100], expect: ['Weapon', 'Trusted scope', '2023?', 'facts applied'], caption: /grain of Offence/ },
  { name: 'short stage (fits by zooming out, never cut)', q: 'upto=9&gap=600&end=1&wide=1', wait: 12000, resizeTo: [1180, 430], expect: ['Weapon', 'Trusted scope', 'facts applied'], caption: /grain of Offence/ },
  { name: 'very large stage (never magnified past 1.2x native)', q: 'upto=9&gap=600&end=1', wait: 12000, resizeTo: [1900, 1100], expect: ['facts applied', 'Trusted scope'], caption: /grain of Offence/, kMax: 1.2 },
  { name: 'two statements: chips + rail rows', q: 'upto=11&gap=600&end=1&multi=1&depth=inspect', wait: 13000, expect: ['facts applied'], caption: /grain of/, chips: 2, stRows: 2, stAhead: 0 },
  { name: 'two statements: hold on the first', q: 'upto=11&gap=600&end=1&multi=1&depth=inspect', wait: 13000, click: '.kgt-strip .st[data-st="1"]', expect: ['Offence', 'Incident'], caption: /^Statement 1 of 2/, chips: 2, stRows: 2, stAhead: 1, strip: /v_Offence/, activeBeats: ['execute'] },
  { name: 'two statements: hold on the first, narrow panel', q: 'upto=11&gap=600&end=1&multi=1&depth=inspect', wait: 13000, resizeTo: [620, 760], click: '.kgt-strip .st[data-st="1"]', expect: ['Offence'], caption: /^Statement 1 of 2/, chips: 2, strip: /v_Offence/ },
  // The reasoning trail: during a scrub the reducer consumes the whole turn while the stage
  // holds at the clicked beat — "current" read from the reducer left the LAST beat's dot
  // orange beside the held one ("sometimes the dots stay orange", 2026-09-17).
  { name: 'hold on Inject via the trail', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="inject"]', expect: ['Inject'], caption: /./, activeBeats: ['inject'], ribbonActive: ['inject'],
    // a hold never turns a finished beat back into a pending one: the beats after it stay DONE, dimmed as ahead; and 'now' is a live word
    ribbonDone: ['understand', 'seed', 'expand', 'inject', 'execute', 'verify', 'receipt'], ribbonAhead: ['execute', 'verify', 'receipt'], heldTsNotNow: true },
  // The idle frame is the default; a reloaded turn is ARMED behind Replay, never auto-played
  // ("why isn't 'Waiting for the question' showing up anymore?", 2026-09-17).
  { name: 'armed reload: idle frame + Replay last turn offered', q: 'upto=9&arm=1', wait: 3000, caption: /^Waiting for the question/, idle: true, idleSub: /ready to replay/, replayShown: true, replayLabel: 'Replay last turn', activeBeats: [], noText: true },
  { name: 'armed reload: Replay plays the story', q: 'upto=9&arm=1', wait: 3000, click: '.kgt-replay', clickWait: 6000, expect: ['robberies'], caption: /./, notCaption: /^Waiting/, idle: false, replayLabel: 'Replay' },
  // Inspect: the idle card must not slide under the trail rail (it did — centred on the full width)
  { name: 'armed reload, Inspect: the idle card clears the trail rail', q: 'upto=9&arm=1&depth=inspect', wait: 3000, caption: /^Waiting for the question/, idle: true, replayShown: true, activeBeats: [], noText: true, idleClearsRail: true },
  { name: 'nothing to replay: no Replay control', q: 'upto=0', wait: 2500, caption: /^Waiting for the question/, idle: true, idleSub: /^$/, replayShown: false, activeBeats: [], noText: true },
  // Hover = spotlight what the beat touched. Understand touched the QUESTION (its terms come
  // forward, the stage recedes); Verify touched the SCOPE (the perimeter comes forward). Both
  // were inert on hover ("if I hover over understand nothing happens", 2026-09-17).
  // Hover = PREVIEW the frame a click would hold (one mechanism for both gestures). Understand's
  // frame carries its terms forward, Verify's the perimeter — no longer a hover-only spotlight.
  { name: 'hover Understand: previews its frame (terms forward, all dim, no threads)', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, hover: '.kgt-trail .kgt-tr[data-beat="understand"]', caption: /^Understanding/, qFocus: true, termsLit: true, allDim: true, noThreads: true, sparse: true, previewRow: 'understand', heldRow: null, capSub: /preview/ },
  { name: 'hover Verify: previews its frame (the trusted scope forward)', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, hover: '.kgt-trail .kgt-tr[data-beat="verify"]', expect: ['Trusted scope'], caption: /Stayed inside the trusted scope/, vFocus: true, perimLit: true, previewRow: 'verify', heldRow: null },
  { name: 'hover, then leave: the finished story is back, nothing held', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, hover: '.kgt-trail .kgt-tr[data-beat="seed"]', hoverOut: '.kgt-trail .kgt-tr[data-beat="seed"]', expect: ['facts applied', 'Trusted scope'], caption: /grain of Offence/, previewRow: null, heldRow: null, activeBeats: [] },
  // THE claim (2026-09-17): "if I select a topic and no longer hover it, the state is changing".
  // The cursor is on the row it clicks; leaving the row must change NOTHING on the stage.
  { name: 'click Seed with the cursor on it, then leave: the frame does not change', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, hoverBeforeClick: '.kgt-trail .kgt-tr[data-beat="seed"]', click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1200, hover: '.kgt-trail .kgt-tr[data-beat="seed"]', hoverOut: '.kgt-trail .kgt-tr[data-beat="seed"]', caption: /^Seeded/, activeBeats: ['seed'], heldRow: 'seed', previewRow: null, hasThreads: true, sparse: true, seedFrame: true, capSub: /held/, frameUnchangedAfterLeave: true },
  { name: 'click Seed, hover it again: no difference', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1200, hover: '.kgt-trail .kgt-tr[data-beat="seed"]', caption: /^Seeded/, activeBeats: ['seed'], heldRow: 'seed', previewRow: null, hasThreads: true, sparse: true, seedFrame: true, capSub: /held/ },
  { name: 'hold Seed, hover Execute: Execute previews, Seed stays held', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1200, hover: '.kgt-trail .kgt-tr[data-beat="execute"]', caption: /./, activeBeats: ['execute'], heldRow: 'seed', previewRow: 'execute', noneDim: true, capSub: /preview/ },
  // From Execute on, the frame's subject is the statement: the tables it reads stay forward,
  // the injected context it does not read recedes ("shouldn't we highlight only the entity /
  // relationships used for the query if I select execute?", 2026-09-17). Inject keeps everything solid.
  { name: 'hold at Execute: only the tables the statement reads stand forward', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="execute"]', clickWait: 1500, caption: /./, activeBeats: ['execute'], usedFrame: true },
  { name: 'hold at Inject: the whole context is solid (nothing has been used yet)', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="inject"]', clickWait: 1500, caption: /./, activeBeats: ['inject'], allSolid: true },
  { name: 'the receipt keeps the used tables forward', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, caption: /grain of Offence/, usedFrame: true },
  { name: 'hold Seed, hover Execute, leave: Seed is back', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1200, hover: '.kgt-trail .kgt-tr[data-beat="execute"]', hoverOut: '.kgt-trail .kgt-tr[data-beat="execute"]', caption: /^Seeded/, activeBeats: ['seed'], heldRow: 'seed', previewRow: null, hasThreads: true, sparse: true, seedFrame: true },
  // Seed's frame lights the ENTITY an attribute seed belongs to (the thread lands on that box);
  // it lit only the attribute's name before, so Offence stayed dim under a thread pointing at it.
  { name: 'hold at Seed: an attribute seed lights its entity', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1200, caption: /^Seeded/, sparse: true, seedFrame: true },
  // Hold Understand, release it: the stage plays the rest from there. While it catches up the
  // reducer already sits at the receipt — "current" must follow the STAGE, never the reducer
  // ("the receipt dot is flashing", 2026-09-17). Probed mid-catch-up.
  { name: 'release after a hold: current follows the stage during the catch-up', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: ['.kgt-trail .kgt-tr[data-beat="understand"]', '.kgt-trail .kgt-tr[data-beat="understand"]'], clickWait: 250, caption: /./, midCatchUp: true },
  // The viewer zooms in, then clicks a beat: the click is THEIR act, so the camera re-fits
  // (the 5-second "viewer's hand wins" guard is for the story, not for their own click).
  // Without this the new arrangement was drawn under the old zoom — "totally misplacing".
  { name: 'zoomed in, then a beat click: the camera re-fits', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, zoomFirst: true, click: '.kgt-trail .kgt-tr[data-beat="inject"]', clickWait: 1500, expect: ['Inject'], caption: /./, activeBeats: ['inject'] },
  { name: 'zoomed in, then Replay: the camera re-fits', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, zoomFirst: true, click: '.kgt-replay', clickWait: 9000, expect: ['facts applied'], caption: /grain of Offence/ },
  // A BURST (replay, reload, live catch-up) performs the inject op after the model exists, a
  // gapped live turn before it: the story must read the same either way — the folded line
  // shows in both, and it never sits on the IMPLEMENTS label (it did, only on the burst).
  { name: 'replay (a burst) settles to the same story as the gapped play', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-replay', clickWait: 9000, expect: ['facts applied', 'attributes folded'], caption: /grain of Offence/ },
  { name: 'gapped live play shows the folded counts too', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, expect: ['facts applied', 'attributes folded'], caption: /grain of Offence/ },
  // A HELD frame is the story so far ON THE MAP. Holding at Understand/Seed/Expand used to
  // show the raw whole-graph field (dots, no boxes, no subject areas) beside Inject's boxes,
  // because a scrub performed each op as its event arrived — before the model existed — so
  // Seed/Expand took their model-less branches. The turn is now reduced first, then the ops
  // perform up to the hold, with the model known ("still regression", 2026-09-17).
  { name: 'hold at Understand: the map, everything dim, no threads yet, the terms forward', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="understand"]', clickWait: 1500, caption: /^Understanding/, activeBeats: ['understand'], map: true, allDim: true, noThreads: true, sparse: true, qFocus: true, termsLit: true },
  // The RELOAD path (no live stage notices; nostage=1): the model op used to carry no beat and ran
  // under a hold at Understand, lighting the seeds — a held Understand read as Seed (2026-09-17).
  { name: 'reloaded turn, hold at Understand: nothing lit, no threads', q: 'upto=99&gap=300&end=1&depth=inspect&nostage=1', wait: 9000, click: '.kgt-trail .kgt-tr[data-beat="understand"]', clickWait: 1500, caption: /^Understanding/, activeBeats: ['understand'], map: true, allDim: true, noThreads: true, sparse: true, qFocus: true, termsLit: true },
  { name: 'reloaded turn, hold at Seed: the seeds lit, threads drawn', q: 'upto=99&gap=300&end=1&depth=inspect&nostage=1', wait: 9000, click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1500, caption: /^Seeded/, activeBeats: ['seed'], map: true, someLit: true, hasThreads: true, sparse: true, seedFrame: true },
  { name: 'reloaded turn plays to the same receipt', q: 'upto=99&gap=300&end=1&depth=inspect&nostage=1', wait: 9000, expect: ['facts applied', 'Trusted scope'], caption: /grain of Offence/, usedFrame: true },
  { name: 'hold at Seed: the map, the seeds lit, threads drawn', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="seed"]', clickWait: 1500, caption: /^Seeded/, activeBeats: ['seed'], map: true, someLit: true, hasThreads: true, sparse: true },
  { name: 'hold at Expand: the map, the chain known', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-trail .kgt-tr[data-beat="expand"]', clickWait: 1500, caption: /^Expanded/, activeBeats: ['expand'], map: true, noneDim: true },
  { name: 'two statements: portrait, Inspect', q: 'upto=11&gap=600&end=1&multi=1&depth=inspect', wait: 13000, resizeTo: [760, 1100], expect: ['facts applied'], caption: /grain of/, chips: 2, stRows: 2 },
  // The reasoning trail reads from the STAGE, not the reducer. A replay reduces the whole turn
  // at once, so the old trail flipped every row to DONE — with its final facts — the moment
  // Replay was clicked, while the stage was still performing Seed ("I am totally confused
  // about what is displayed", 2026-09-17). Probed mid-replay: the rows the stage has not
  // performed read "up next", carry no facts and no seconds; the ribbon agrees.
  { name: 'mid-replay: the trail reads only what the stage has performed', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-replay', clickWait: 250, caption: /./, midCatchUp: true, sparse: true, trailFollowsStage: true, qFocus: true },   // probed at Understand, whose frame carries its terms forward
  // After the receipt a beat that never happened DID NOT RUN — never "waiting" (the prevented
  // turn has no grounding gate, so Verify reads "did not run this turn").
  { name: 'after the receipt: a beat that never ran says so', q: 'upto=8&gap=600&end=1&mode=cross&depth=inspect', wait: 12000, caption: /Stopped a join across subject areas/, trailAbsent: ['verify'], trailDone: ['understand', 'seed', 'expand', 'inject', 'execute', 'receipt'] },
  // A gapped live turn keeps its seconds; a replay of the same turn KEEPS the live clock (it
  // used to re-stamp every beat at the burst: "0.00 s" on every row); a reload has no clock.
  { name: 'live seconds survive a replay', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-replay', clickWait: 9000, caption: /grain of Offence/, secondsKept: true },
  { name: 'a reloaded turn shows no seconds (it has no clock)', q: 'upto=9&arm=1&depth=inspect', wait: 3000, click: '.kgt-replay', clickWait: 7000, caption: /./, notCaption: /^Waiting/, noSeconds: true },
  // The optional key and the optional explanation line: off by default, one click each,
  // remembered per browser; the explanation follows the beat on stage, then the one clicked.
  { name: 'key and explanation are off by default', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, caption: /grain of Offence/, legendOn: false, explainOn: false },
  { name: 'Key: a swatch per mark, grouped', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-key', clickWait: 600, caption: /grain of Offence/, legendOn: true, legendMin: 28, legendGroups: 5 },
  // show → hide → show: the third click must show the key again (it came back empty, 2026-09-17)
  { name: 'Key: show, hide, show again', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: ['.kgt-key', '.kgt-key', '.kgt-key'], clickWait: 600, caption: /grain of Offence/, legendOn: true, legendMin: 28, legendGroups: 5 },
  { name: 'Key: show, hide', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: ['.kgt-key', '.kgt-key'], clickWait: 600, caption: /grain of Offence/, legendOn: false },
  { name: 'Explain: the beat on stage, then the beat clicked', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: ['.kgt-explain-btn', '.kgt-trail .kgt-tr[data-beat="seed"]'], clickWait: 1500, caption: /^Seeded/, explainOn: true, explainRe: /^SEED.*Finds the entities.*3 seeds reached.*offence_code.*Held here/s, activeBeats: ['seed'], sparse: true },
  { name: 'Explain on the receipt: the verdict', q: 'upto=9&gap=600&end=1&depth=inspect', wait: 12000, click: '.kgt-explain-btn', clickWait: 600, caption: /grain of Offence/, explainOn: true, explainRe: /^RECEIPT.*verdict.*grain of Offence.*Performed\./s },
];
for (const sc of scenarios.filter(s => !ONLY || ONLY.test(s.name))) {
  let out; try { out = await frame(sc.q, sc.wait, PROBE_ALL, sc.resizeTo, sc.click, sc.clickWait, sc.hover, sc.zoomFirst, sc.hoverOut, sc.hoverBeforeClick, sc.frameUnchangedAfterLeave); } catch (e) { check(`${sc.name}: frame captured`, false, String(e)); continue; }
  const v = JSON.parse(out.value || '{}'); const joined = (v.texts || []).join('\n');
  check(`${sc.name}: no page errors`, !v.err && !out.logs.length, (v.err || '') + out.logs.join(' | '));
  if (!sc.noText && !sc.sparse) check(`${sc.name}: text rendered`, (v.probe?.texts || 0) > 20, `texts=${v.probe?.texts}`);   // an early held frame is deliberately sparse (dim entities are below the probe's opacity floor)
  if (!sc.midCatchUp) check(`${sc.name}: no two rendered texts overlap`, (v.probe?.overlaps || []).length === 0, (v.probe?.overlaps || []).join('; '));   // a mid-animation frame is not a settled layout
  if (!sc.noText) check(`${sc.name}: everything shown is on screen`, (sc.midCatchUp || sc.sparse || (v.probe?.shown || 0) > 3) && v.probe?.outside === 0, `shown=${v.probe?.shown} outside=${v.probe?.outside}`);   // mid-animation, the floor on 'shown' does not apply; 'outside' still does
  // fit is mandatory: the camera stays within its declared bounds, the question and the strip are on the stage
  if (sc.kMax) check(`${sc.name}: the camera reached the cap (k=${v.k})`, v.k >= sc.kMax - 0.02, `k=${v.k} stage=${v.stage}`);   // the cap must be what decides here, or the bound is untested
  check(`${sc.name}: camera within [0.2, 1.2] (k=${v.k})`, v.k >= 0.2 - 1e-6 && v.k <= (sc.kMax || 1.2) + 1e-3, `k=${v.k} stage=${v.stage}`);
  if (!sc.noText) check(`${sc.name}: the question is on the stage`, v.qIn === true, `qIn=${v.qIn}`);
  if (v.sOn) check(`${sc.name}: the statement strip is on the stage`, v.sIn === true, `sIn=${v.sIn}`);
  if (sc.stacked != null) check(`${sc.name}: subject areas ${sc.stacked ? 'stacked' : 'side by side'}`, v.stacked === sc.stacked, `areas=${v.areas} stacked=${v.stacked} stage=${v.stage}`);
  for (const e of (sc.expect || [])) check(`${sc.name}: shows "${e}"`, joined.includes(e));
  check(`${sc.name}: headline`, sc.caption.test(v.caption || ''), v.caption);
  if (sc.chips != null) check(`${sc.name}: statement chips`, v.chips === sc.chips, `chips=${v.chips}`);
  if (sc.stRows != null) check(`${sc.name}: rail statement rows`, v.stRows === sc.stRows, `rows=${v.stRows}`);
  if (sc.stAhead != null) check(`${sc.name}: later statements dimmed`, v.stAhead === sc.stAhead, `ahead=${v.stAhead}`);
  if (sc.strip) check(`${sc.name}: strip shows the held statement`, sc.strip.test(v.strip || ''), (v.strip || '').slice(0, 80));
  // ONE current beat, ever: the held one while held, else the reducer's — never both
  check(`${sc.name}: at most one beat reads current in the trail`, (v.activeBeats || []).length <= 1, JSON.stringify(v.activeBeats));
  check(`${sc.name}: at most one beat reads current in the ribbon`, (v.ribbonActive || []).length <= 1, JSON.stringify(v.ribbonActive));
  if (sc.activeBeats) check(`${sc.name}: the held beat is the current one`, JSON.stringify(v.activeBeats) === JSON.stringify(sc.activeBeats), JSON.stringify(v.activeBeats));
  if (sc.ribbonActive) check(`${sc.name}: the ribbon agrees`, JSON.stringify(v.ribbonActive) === JSON.stringify(sc.ribbonActive), JSON.stringify(v.ribbonActive));
  if (sc.ribbonDone) check(`${sc.name}: finished beats stay done while held`, JSON.stringify(v.ribbonDone) === JSON.stringify(sc.ribbonDone), JSON.stringify(v.ribbonDone));
  if (sc.ribbonAhead) check(`${sc.name}: beats after the hold read ahead`, JSON.stringify(v.ribbonAhead) === JSON.stringify(sc.ribbonAhead), JSON.stringify(v.ribbonAhead));
  if (sc.heldTsNotNow) check(`${sc.name}: a held beat is never 'now'`, v.heldTs !== 'now', JSON.stringify(v.heldTs));
  if (sc.map) check(`${sc.name}: the arrangement is on stage (subject areas + entity boxes)`, (v.areas || 0) >= 2 && (v.ents || 0) >= 4, `areas=${v.areas} ents=${v.ents}`);
  if (sc.allDim) check(`${sc.name}: every entity dim`, v.ents > 0 && v.dimEnts === v.ents, `dim=${v.dimEnts}/${v.ents}`);
  if (sc.someLit) check(`${sc.name}: a seed is lit, the rest dim`, v.litEnts >= 1 && v.dimEnts >= 1, `lit=${v.litEnts} dim=${v.dimEnts}`);
  if (sc.noneDim) check(`${sc.name}: no entity left dim once the chain is known`, v.ents > 0 && v.dimEnts === 0, `dim=${v.dimEnts}/${v.ents}`);
  if (sc.noThreads) check(`${sc.name}: no threads`, v.threads === 0, `threads=${v.threads}`);
  if (sc.hasThreads) check(`${sc.name}: threads drawn`, v.threads >= 1, `threads=${v.threads}`);
  if (sc.midCatchUp) {
    check(`${sc.name}: released (no row held)`, !(v.texts || []).some(t => /held —/.test(t)), JSON.stringify(v.texts));
    // the probe must land MID catch-up, or the rule is untested
    check(`${sc.name}: probed while ops were still queued (stage at ${v.stageBeat})`, (v.pending || 0) > 0 && v.stageBeat && v.stageBeat !== 'receipt', `pending=${v.pending} stage=${v.stageBeat}`);
    check(`${sc.name}: current IS the stage's beat, never the reducer's`, JSON.stringify(v.activeBeats) === JSON.stringify([v.stageBeat]), `active=${JSON.stringify(v.activeBeats)} stage=${v.stageBeat}`);
    check(`${sc.name}: trail and ribbon agree`, JSON.stringify(v.activeBeats) === JSON.stringify(v.ribbonActive), `${JSON.stringify(v.activeBeats)} vs ${JSON.stringify(v.ribbonActive)}`);
    check(`${sc.name}: the caption narrates the stage, not the receipt`, !/grain of Offence/.test(v.caption || ''), v.caption);
  }
  if (sc.qFocus != null) check(`${sc.name}: question focus ${sc.qFocus ? 'on' : 'off'}`, v.qFocus === sc.qFocus, `qFocus=${v.qFocus}`);
  if (sc.termsLit) check(`${sc.name}: every term word at full opacity, every other word receded`, v.termOp.length > 0 && v.termOp.every(o => o >= 0.99) && v.otherOp.length > 0 && v.otherOp.every(o => o <= 0.2), JSON.stringify({ t: v.termOp, o: v.otherOp }));
  if (sc.termsLit) check(`${sc.name}: the stage recedes behind the terms`, v.entOp.length > 0 && v.entOp.every(o => o <= 0.35), JSON.stringify(v.entOp));   // Understand's frame: nothing reached yet
  if (sc.vFocus != null) check(`${sc.name}: scope focus ${sc.vFocus ? 'on' : 'off'}`, v.vFocus === sc.vFocus, `vFocus=${v.vFocus}`);
  if (sc.perimLit) check(`${sc.name}: the perimeter is fully visible`, v.perimOp >= 0.99, `perim=${v.perimOp}`);
  // every other scenario: no spotlight class survives without a hover
  if (sc.qFocus == null && sc.vFocus == null) check(`${sc.name}: no stray spotlight`, !v.qFocus && !v.vFocus, `q=${v.qFocus} v=${v.vFocus}`);
  if (sc.idle != null) check(`${sc.name}: idle frame ${sc.idle ? 'shown' : 'hidden'}`, v.idle === sc.idle, `idle=${v.idle}`);
  if (sc.idleClearsRail) check(`${sc.name}: the idle card lies left of the rail`, !!v.idleCard && v.idleCard.railLeft != null && v.idleCard.right <= v.idleCard.railLeft - 4 && v.idleCard.left >= 0, JSON.stringify(v.idleCard));
  if (sc.idle === true) check(`${sc.name}: the idle line is a legible card, not muted text over the field`, !!v.idleCard && v.idleCard.size >= 14 && v.idleCard.border !== '0px' && !/rgba\(0, 0, 0, 0\)|transparent/.test(v.idleCard.bg) && v.idleCard.color === 'rgb(248, 250, 252)', JSON.stringify(v.idleCard));
  if (sc.idleSub) check(`${sc.name}: idle sub-line`, sc.idleSub.test(v.idleSub || ''), JSON.stringify(v.idleSub));
  if (sc.replayShown != null) check(`${sc.name}: Replay control ${sc.replayShown ? 'offered' : 'withheld'}`, v.replayShown === sc.replayShown, `replayShown=${v.replayShown}`);
  if (sc.replayLabel) check(`${sc.name}: Replay label`, v.replayLabel === sc.replayLabel, JSON.stringify(v.replayLabel));
  if (sc.notCaption) check(`${sc.name}: caption moved on`, !sc.notCaption.test(v.caption || ''), v.caption);
  const tr = v.trail || [];
  if (sc.trailFollowsStage) {
    const stageIdx = ['understand', 'seed', 'expand', 'inject', 'execute', 'verify', 'receipt'].indexOf(v.stageBeat);
    check(`${sc.name}: probed while ops were still queued (stage at ${v.stageBeat})`, (v.pending || 0) > 0 && stageIdx >= 0 && stageIdx < 6, `pending=${v.pending} stage=${v.stageBeat}`);
    const done = tr.filter(r => r.state === 'done').map(r => r.beat), queued = tr.filter(r => r.state === 'queued');
    check(`${sc.name}: DONE rows are exactly the beats the stage has performed`, done.length > 0 && done.every(b => ['understand', 'seed', 'expand', 'inject', 'execute', 'verify', 'receipt'].indexOf(b) <= stageIdx), JSON.stringify({ done, stage: v.stageBeat }));
    check(`${sc.name}: the rest read up next, with no facts and no seconds`, queued.length > 0 && queued.every(r => r.sub === 'up next' && r.ts === ''), JSON.stringify(queued));
    check(`${sc.name}: the ribbon marks the same beats done`, JSON.stringify(v.ribbonDone) === JSON.stringify(done), `${JSON.stringify(v.ribbonDone)} vs ${JSON.stringify(done)}`);
  }
  if (sc.trailAbsent) check(`${sc.name}: beats that never ran read "did not run this turn"`, sc.trailAbsent.every(b => tr.find(r => r.beat === b)?.state === 'absent' && /did not run/.test(tr.find(r => r.beat === b)?.sub || '')), JSON.stringify(tr));
  if (sc.trailDone) check(`${sc.name}: performed beats read done, with their facts`, JSON.stringify(tr.filter(r => r.state === 'done').map(r => r.beat)) === JSON.stringify(sc.trailDone) && tr.filter(r => r.state === 'done').every(r => r.sub && !/waiting|up next/.test(r.sub)), JSON.stringify(tr));
  if (sc.secondsKept) { const secs = tr.filter(r => /\d s$/.test(r.ts)); check(`${sc.name}: the live seconds are kept, distinct, in order`, secs.length >= 5 && secs.some(r => parseFloat(r.ts) > 1) && secs.every((r, i, a) => i === 0 || parseFloat(r.ts) >= parseFloat(a[i - 1].ts)), JSON.stringify(tr.map(r => r.ts))); }
  if (sc.noSeconds) check(`${sc.name}: no row wears a clock it never had`, tr.every(r => !/\d s$/.test(r.ts)), JSON.stringify(tr.map(r => r.ts)));
  if (sc.legendOn != null) check(`${sc.name}: key ${sc.legendOn ? 'shown' : 'hidden'}`, v.legendOn === sc.legendOn && v.keyPressed === String(sc.legendOn) && v.prefs?.legend === sc.legendOn, `on=${v.legendOn} pressed=${v.keyPressed} prefs=${JSON.stringify(v.prefs)}`);
  if (sc.legendMin) check(`${sc.name}: every item has a swatch and a label`, v.legendItems >= sc.legendMin && v.legendSwatches >= v.legendItems && v.legendGroups === sc.legendGroups, `items=${v.legendItems} swatches=${v.legendSwatches} groups=${v.legendGroups}`);
  if (sc.explainOn != null) check(`${sc.name}: explanation ${sc.explainOn ? 'shown' : 'hidden'}`, v.explainOn === sc.explainOn && v.explainPressed === String(sc.explainOn), `on=${v.explainOn} pressed=${v.explainPressed}`);
  if (sc.explainRe) check(`${sc.name}: the line explains the beat`, sc.explainRe.test(v.explain || ''), v.explain);
  if ('heldRow' in sc) check(`${sc.name}: held row is ${sc.heldRow}`, v.heldRow === sc.heldRow, `held=${v.heldRow}`);
  if ('previewRow' in sc) check(`${sc.name}: preview row is ${sc.previewRow}`, v.previewRow === sc.previewRow, `preview=${v.previewRow}`);
  if (sc.capSub) check(`${sc.name}: the caption says ${sc.capSub}`, sc.capSub.test(v.capSub || ''), v.capSub);
  // the Seed frame, exactly: both seed entities lit (Incident, and Offence through offence_code), the other four dim
  if (sc.frameUnchangedAfterLeave) { const a = out.afterClick || {}; const b = { entOps: v.entOps, rels: v.rels, threads: v.threads, qFocus: v.qFocus }; check(`${sc.name}: what the cursor showed after the click IS what remains after leaving`, !!out.afterClick && JSON.stringify(a.entOps) === JSON.stringify(b.entOps) && JSON.stringify(a.rels) === JSON.stringify(b.rels) && a.threads === b.threads && a.qFocus === b.qFocus, `after click ${JSON.stringify(a)} vs after leave ${JSON.stringify(b)}`); }
  if (sc.usedFrame) check(`${sc.name}: used tables at full strength, the rest of the context receded`, v.entOps?.Incident === 1 && v.entOps?.Offence === 1 && ['Victim', 'Agency', 'CaseObject', 'ChainOfCustody'].every(n => v.entOps?.[n] > 0.3 && v.entOps?.[n] < 0.6), JSON.stringify(v.entOps));
  if (sc.allSolid) check(`${sc.name}: every injected entity solid`, Object.values(v.entOps || {}).length >= 6 && Object.values(v.entOps).every(o => o === 1), JSON.stringify(v.entOps));
  if (sc.seedFrame) check(`${sc.name}: the Seed frame (Incident + Offence lit, the rest dim)`, v.entOps?.Incident === 1 && v.entOps?.Offence === 1 && ['Victim', 'Agency', 'CaseObject', 'ChainOfCustody'].every(n => v.entOps?.[n] < 0.3), JSON.stringify(v.entOps));
}
server.close();
if (failed) { console.log(fails.map(f => 'FAIL ' + f).join('\n')); console.log(`FAILED — ${passed} passed, ${failed} failed`); process.exit(1); }
console.log(`ALL PASS — ${passed} assertions, 0 failures`);
