// S10 — Steer & Reattach: the client, EXECUTED under the shared DOM stub + structurally pinned.
//   (1) the module: task_start arms steer mode (the composer live, its placeholder saying what a
//       sent message does), cursors move forward only, a sent steer is POSTed + pinned + announced,
//       a consumed steer flips its pin and announces; (2) reattach: the tail is fetched from the
//       cursor, `delivered` ends it, 404 = gone, a gap is reported, retries are bounded;
//   (3) the wiring: the parser reads `id:`, task_start arms, final_answer marks delivered, a broken
//       stream reattaches before it reports an error, the stream's end disarms; Enter-with-text
//       steers while the Stop button still stops; the fresh-load path reattaches a running turn
//       (never the cache-restore path); the step renders on all three paths + the notification.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

const { doc, body } = installDom({ __currentSessionId: 'sess-1' });
const log = new El('div', { id: 'chat-log' }); body.appendChild(log); doc._register('chat-log', log);
const input = new El('textarea', { id: 'user-input' }); input.placeholder = 'Ask anything'; body.appendChild(input); doc._register('user-input', input);
const calls = [];
let streamBodies = [];
globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body });
    if (/\/steer$/.test(url)) return { ok: true, status: 202, json: async () => ({ status: 'queued', steer: { id: 'st1', text: JSON.parse(opts.body).text } }) };
    if (/active-task$/.test(url)) return { ok: true, json: async () => ({ task_id: 'task-9', last_seq: 7 }) };
    if (/\/tasks\/gone\//.test(url)) return { ok: false, status: 404 };
    if (/\/tasks\/flaky\//.test(url)) { const b = streamBodies.shift(); return b === undefined ? { ok: false, status: 502 } : { ok: true, status: 200, body: b }; }
    if (/\/tasks\/task-9\/stream\?since=(\d+)/.test(url)) return { ok: true, status: 200, body: { since: url.match(/since=(\d+)/)[1] } };
    return { ok: false, status: 404, json: async () => ({}) };
};
globalThis.setTimeout = (fn) => { fn(); return 1; };   // every timer fires at once: pins settle, retries never wait
await import(pathToFileURL('static/js/handlers/steerReattach.js').href);
const SR = window.SteerReattach;
check('the module publishes its surface', SR && ['onTaskStart', 'cursor', 'delivered', 'ended', 'liveTask', 'shouldSteer', 'sendSteer', 'consumed', 'reattach', 'activeTask'].every(k => typeof SR[k] === 'function'));

// ── (1) steer mode ────────────────────────────────────────────────────────────────────────
{
  check('before any turn, nothing steers', SR.shouldSteer('sess-1') === false && SR.liveTask('sess-1') === null);
  SR.onTaskStart('sess-1', { task_id: 'task-1' });
  check('a task_start WITHOUT reattach arms nothing (the switch is off there)', SR.shouldSteer('sess-1') === false);
  SR.onTaskStart('sess-1', { task_id: 'task-1', reattach: true });
  check('task_start with reattach arms steer mode: the composer is live with the steer placeholder + an aria-description', SR.shouldSteer('sess-1') && input.disabled === false && input.placeholder === SR.PLACEHOLDER_STEER && /considered at its next step/.test(input.getAttribute('aria-description') || ''), input.placeholder);
  SR.cursor('sess-1', 3); const afterBack = SR.cursor('sess-1', 2) || SR.liveTask('sess-1').seq; SR.cursor('sess-1', 5);
  check('the cursor only moves forward (a lower cursor never rewinds it)', afterBack === 3 && SR.liveTask('sess-1').seq === 5, afterBack);
  const st = await SR.sendSteer('sess-1', '  only EMEA please ');
  const post = calls.find(c => /\/sessions\/sess-1\/steer$/.test(c.url) && c.method === 'POST');
  const pin = log.querySelector('.steer-pin');
  check('sendSteer POSTs the trimmed text, pins it under the running answer, names its state', post && JSON.parse(post.body).text === 'only EMEA please' && st.id === 'st1' && pin && pin.dataset.steerId === 'st1' && /only EMEA please/.test(pin.textContent) && /considered at the next step/.test(pin.textContent), pin && pin.textContent);
  check('the pin is a note, the steer text is emphasised (italic), the live region announced', pin.getAttribute('role') === 'note' && pin.querySelector('em.steer-pin-text') && /Steer sent/.test(doc.getElementById('steer-live').textContent));
  check('an empty steer is never sent', (await SR.sendSteer('sess-1', '   ')) === null && calls.filter(c => c.method === 'POST').length === 1);
  // the guard must be load-bearing on a still-pinned steer — after consumption the pin is gone
  // and a missing guard would return 0 for the wrong reason
  const guarded = SR.consumed({ session_id: 'other', at: 'x', steers: [{ id: 'st1' }] });
  check('consumed is session-guarded (another session\'s step never flips this pin)', guarded === 0 && !pin.classList.contains('is-considered') && /considered at the next step/.test(pin.textContent));
  const n = SR.consumed({ session_id: 'sess-1', at: 'base_readQuery', steers: [{ id: 'st1', text: 'only EMEA please' }] });
  check('consumed flips the pin (considered · where) and announces it', n === 1 && pin.classList.contains('is-considered') && /considered with the result of base_readQuery/.test(pin.textContent) && /Steer considered/.test(doc.getElementById('steer-live').textContent), pin.textContent);
  check('consumed is idempotent', SR.consumed({ session_id: 'sess-1', steers: [{ id: 'st1' }] }) === 0);
  SR.ended('sess-1');
  check('the stream\'s end disarms: no steer mode, the original placeholder back', SR.shouldSteer('sess-1') === false && input.placeholder === 'Ask anything' && input.getAttribute('aria-description') === null);
}

// ── (2) reattach ──────────────────────────────────────────────────────────────────────────
{
  const seen = [];
  const process = async (bodyObj, sid) => { seen.push(bodyObj.since); SR.cursor(sid, Number(bodyObj.since) + 3); SR.delivered(sid); return 7; };
  const res = await SR.reattach('sess-1', 'task-9', 5, process, { silent: true });
  check('reattach fetches the tail from the cursor, hands the body to the app\'s reader, ends on delivered with the turn', res.status === 'delivered' && res.turn === 7 && seen[0] === '5' && calls.some(c => /\/tasks\/task-9\/stream\?since=5$/.test(c.url)), res);
  SR.ended('sess-1');
  const gone = await SR.reattach('sess-1', 'gone', 0, process, { silent: true });
  check('a 404 is gone (finished long ago, or never streamed) — no retry storm', gone.status === 'gone' && calls.filter(c => /\/tasks\/gone\//.test(c.url)).length === 1);
  SR.ended('sess-1');
  streamBodies = [undefined, { since: '0' }];   // first attempt 502, second serves
  const seen2 = [];
  const process2 = async (b, sid) => { seen2.push(b.since); SR.cursor(sid, 4); SR.delivered(sid); return 3; };
  const flaky = await SR.reattach('sess-1', 'flaky', 0, process2, { silent: true });
  check('a failed attempt is retried (bounded backoff) and the second serves', flaky.status === 'delivered' && seen2.length === 1 && calls.filter(c => /\/tasks\/flaky\//.test(c.url)).length === 2, flaky);
  SR.ended('sess-1');
  const never = await SR.reattach('sess-1', 'flaky', 0, async () => null, { silent: true });
  check('retries are bounded: a tail that never delivers ends as failed after MAX_RETRIES', never.status === 'failed' && calls.filter(c => /\/tasks\/flaky\//.test(c.url)).length >= 6, never);
  SR.ended('sess-1');
  const act = await SR.activeTask('sess-1');
  check('activeTask reads the running task + cursor', act && act.taskId === 'task-9' && act.lastSeq === 7, act);
  const noteEl = SR.note('reconnected — caught up', true);
  check('the reconnect note is a status line under the answer', noteEl && noteEl.getAttribute('role') === 'status' && /caught up/.test(noteEl.textContent));
}

// ── (2b) review fixes: a dead turn is not retried; the note is said once; a restored pin is found ──
{
  streamBodies = [{ since: '0' }];
  const flakyCalls = () => calls.filter(c => /\/tasks\/flaky\//.test(c.url)).length;
  const before = flakyCalls();
  const endedProcess = async (b, sid) => { SR.cursor(sid, 2); SR.markEnded(sid); return null; };   // the tail's end said done:true
  const dead = await SR.reattach('sess-1', 'flaky', 0, endedProcess, { silent: true });
  check('a tail whose end says the turn is OVER (no final answer) ends the reattach at once — status ended, ONE fetch, no retry storm', dead.status === 'ended' && flakyCalls() - before === 1, dead);
  SR.ended('sess-1');
  streamBodies = [undefined, { since: '0' }, { since: '3' }];
  let n = 0; const twice = async (b, sid) => { n += 1; SR.cursor(sid, 3); if (n === 2) SR.delivered(sid); return 9; };
  log.querySelectorAll('.reattach-note').forEach(e => e.remove());
  const _to = globalThis.setTimeout; globalThis.setTimeout = (fn, ms) => (ms >= 2000 ? 0 : _to(fn, 0));   // the stub's timers fire at once — hold only the note's 2.5 s fade so it can be counted; the backoff still resolves
  const res2 = await SR.reattach('sess-1', 'flaky', 0, twice);
  globalThis.setTimeout = _to;
  check('the reconnect note is said ONCE across a multi-attempt reattach (it used to repeat per attempt)', res2.status === 'delivered' && log.querySelectorAll('.reattach-note').length === 1, log.querySelectorAll('.reattach-note').length);
  SR.ended('sess-1');
  // a pin the session UI cache restored from innerHTML: the module's map lost it, the DOM still has it
  SR.onTaskStart('sess-1', { task_id: 'task-p', reattach: true });
  const st2 = await SR.sendSteer('sess-1', 'restored pin');
  SR._internals._pins.clear();   // what a cache restore does to the map
  const pin2 = [...log.querySelectorAll('.steer-pin')].find(p => p.dataset && p.dataset.steerId === st2.id);   // held BEFORE consumed: the stub's timers fire at once and the flipped pin fades out of the log
  const c2 = SR.consumed({ session_id: 'sess-1', at: 'base_readQuery', steers: [{ id: st2.id }] });
  check('a pin the map lost is found by its steer id in the live log and still flips', c2 === 1 && pin2 && pin2.classList.contains('is-considered'), c2);
  SR.ended('sess-1');
  const EH2 = read('static/js/eventHandlers.js');
  check('processStream marks the session ended when reattach_end carries done:true', /eventName === 'reattach_end'\) \{ if \(eventData && eventData\.done\) window\.SteerReattach\.markEnded\(originSessionId\); continue; \}/.test(EH2));
  check('reattachRunningTurn releases the Living Answer with the turn', /SR\.ended\(sid\);[\s\S]{0,120}window\.LivingAnswer\.ended\(sid\)/.test(EH2));
  const TSpy = read('src/trusted_data_agent/core/turn_stream.py');
  check("the server's reattach_end carries done", /'done': ended/.test(TSpy) && /ended = bool\(rec\["done"\]\) if rec is not None else True/.test(TSpy));
}

// ── (3) the wiring ────────────────────────────────────────────────────────────────────────
{
  const EH = read('static/js/eventHandlers.js');
  const ps = EH.slice(EH.indexOf('async function processStream('), EH.indexOf('async function _maybeReattach('));   // the whole function, never a distance bound
  check('processStream reads the id: cursor and forwards it; the reattach control frames never render', /line\.startsWith\('id:'\)/.test(ps) && /SteerReattach\.cursor\(originSessionId, cursorSeq\)/.test(ps) && /eventName === 'reattach_gap'[^\n]*markGap[^\n]*continue/.test(ps) && /eventName === 'reattach_end'[^\n]*continue/.test(ps));
  check('task_start arms; final_answer marks delivered', /SteerReattach\.onTaskStart\(originSessionId, eventData\)/.test(ps) && /SteerReattach\.delivered\(originSessionId\)/.test(ps));
  const hs = EH.slice(EH.indexOf('export async function handleStreamRequest('), EH.indexOf('export async function handleChatSubmit('));
  check('a broken stream reattaches BEFORE any error is shown, and the stream\'s end releases steer mode', /recovered = await _maybeReattach\(originSessionId, null\)/.test(hs) && hs.indexOf('_maybeReattach(originSessionId, null)') < hs.indexOf('Sorry, a stream processing error occurred') && /SteerReattach\.ended\(originSessionId\)/.test(hs));
  check('_maybeReattach resumes from the live cursor and falls back to a session re-read on a gap', /SR\.reattach\(sid, live\.taskId, live\.seq, processStream\)/.test(EH) && /res\.status === 'gap'[\s\S]{0,200}__reloadSession\(sid\)/.test(EH));
  check('reattachRunningTurn: finds the task, enters execution state, backfills from 0 silently, settles the dial', /export async function reattachRunningTurn/.test(EH) && /SR\.reattach\(sid, active\.taskId, 0, processStream, \{ silent: true \}\)/.test(EH) && /window\.__reattachRunningTurn = reattachRunningTurn/.test(EH));
  const cs = EH.slice(EH.indexOf('export async function handleChatSubmit('), EH.indexOf('export async function handleChatSubmit(') + 3000);
  check('Enter with text while a turn runs is a steer (focus kept); the Stop button — e.submitter — still stops', /viaButton = !!\(e && e\.submitter\)/.test(cs) && /!viaButton && steerText && window\.SteerReattach/.test(cs) && /sendSteer\(sid, steerText\)/.test(cs) && /DOM\.userInput\.focus\(\)/.test(cs) && cs.indexOf('sendSteer') < cs.indexOf('handleStopExecutionClick()'));
  check('reload-harden synthesizes the step from the persisted stamp, considered and carried apart', /turnData\.steers && Array\.isArray\(turnData\.steers\.steers\)/.test(EH) && /Steer Carried to the Next Turn/.test(EH) && /Steer Considered/.test(EH));
  const SM = read('static/js/handlers/sessionManagement.js');
  const fresh = SM.slice(SM.indexOf('export async function handleLoadSession('));
  const cacheRestore = SM.slice(0, SM.indexOf('export async function handleLoadSession('));
  check('the fresh-load path reattaches a running turn; the cache-restore path (this tab still reads it) never does', /__reattachRunningTurn\(sessionId\)/.test(fresh) && !/__reattachRunningTurn/.test(cacheRestore) && /!state\.activeStreamSessions\.has\(sessionId\)/.test(fresh));
  const UI = read('static/js/ui.js');
  check('the composer stays live in steer mode and is disarmed when the turn ends', /DOM\.userInput\.disabled = isActive && !_steerLive;/.test(UI) && /SteerReattach\.shouldSteer\(state\.currentSessionId\)/.test(UI) && /SteerReattach\.disarmComposer\(\)/.test(UI));
  check('the step renders on all three paths and flips the pin', (UI.match(/case 'steer_consumed': \{/g) || []).length === 2 && /type === "steer_consumed"/.test(UI) && (UI.match(/SteerReattach\.consumed\(\{ \.\.\.details, session_id: _adSession\(\) \}\)/g) || []).length === 3 && /function _renderSteerConsumedDetails/.test(UI));
  const rend = new Function('escapeHtml', UI.slice(UI.indexOf('function _renderSteerConsumedDetails'), UI.indexOf('function _renderAttachmentSourcesDetails')) + '\nreturn _renderSteerConsumedDetails;')((s) => String(s));
  check('the card says where it was considered and quotes the steer', /considered with the result of base_readQuery/.test(rend({ at: 'base_readQuery', steers: [{ text: 'be terser' }] })) && /<em>be terser<\/em>/.test(rend({ at: 'x', steers: [{ text: 'be terser' }] })) && /start of the next turn/.test(rend({ carried: true, steers: [] })));
  const NT = read('static/js/notifications.js');
  check('the live step rides the notification channel, session-guarded', /case 'steer_consumed': \{[\s\S]{0,600}sp\.session_id === state\.currentSessionId[\s\S]{0,300}type: 'steer_consumed'/.test(NT));
  check('the reload title names the step', /eventType === 'steer_consumed'/.test(EH) && /'Steer Considered'/.test(EH));
  const IDX = read('templates/index.html');
  check('index.html: the script is pinned after attachmentSources', /attachmentSources\.js'\) \}\}\?v=[\d.]+"><\/script>\n\s*<script src="\{\{ url_for\('static', filename='js\/handlers\/steerReattach\.js'\) \}\}\?v=[\d.]+"><\/script>/.test(IDX));
  const CSSs = read('static/css/main.css');
  const block = CSSs.slice(CSSs.indexOf('/* S10 — steer & reattach'), CSSs.indexOf('/* S9 — attachment sources'));
  const hexes = block.replace(/var\([^)]*\)/g, '').match(/#[0-9a-fA-F]{3,6}\b/g) || [];
  check('CSS: theme tokens only, reduced motion honoured', hexes.length === 0 && /prefers-reduced-motion/.test(block));
}

console.log(`${fail ? 'FAILURES' : 'ALL PASS'} — ${pass} assertions (${fail} failed)`);
process.exit(fail ? 1 : 0);
