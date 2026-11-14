// S1.1 — The Living Answer (reshaped): the SHIPPED livingAnswer.js executed under the DOM stub
// across every profile class — LIVE (born → working → fold → writing → settle → sealed) and
// RELOADED (restore() from the persisted turn through the same reducer) — plus the wiring
// (ui.js · eventHandlers.js · notifications.js · sessionManagement.js · index.html · routes.py ·
// main.css) on the shipped source. No browser, no server.
//
// The rules this gate holds that the first version did not:
//   · ONE line, ONE receipt, ONE verdict — no rail, no seal glyph, no second verdict slot
//   · the verdict is NEVER "clean" — Verified / N findings / Not checked — and NEVER "Verified"
//     on a turn where no check ran (a turn nobody checked is not a clean turn)
//   · a reloaded turn renders the same chrome as the live one from the same facts
//   · every act on the action row sits on one baseline (no per-control margin)
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d === '' ? '' : JSON.stringify(d).slice(0, 300)); } };

const { doc, body } = installDom({ __currentSessionId: 's1' });
const log = new El('div', { id: 'chat-log' }); body.appendChild(log); doc._register('chat-log', log);
const scroller = new El('main', { id: 'chat-container' }); scroller.scrollHeight = 900; body.appendChild(scroller); doc._register('chat-container', scroller);
globalThis.requestAnimationFrame = f => { f(); return 1; };
globalThis.setTimeout = (fn) => { fn(); return 1; };
globalThis.setInterval = (fn) => { fn(); return 1; };
globalThis.clearInterval = () => {};
globalThis.marked = { parse: t => t.split(/\n\n+/).map(p => `<p>${p}</p>`).join('') };
await import(pathToFileURL('static/js/handlers/livingAnswer.js').href);
const LA = window.LivingAnswer;
check('the module publishes its surface (incl. restore + verdictOf)', LA && ['onTaskStart', 'active', 'contentHost', 'onEvent', 'onNotification', 'firstToken', 'renderText', 'stableText', 'textDone', 'settle', 'stopping', 'ended', 'enabled', 'setEnabled', 'restore', 'verdictOf'].every(k => typeof LA[k] === 'function'));
const bubble = (sid) => log.querySelectorAll('[data-living]').find(n => n.getAttribute('data-living') === sid) || null;
const ev = (sid, name, data) => LA.onEvent(sid, name, data);
const notif = (type, payload) => ev(payload.session_id, 'notification', { type, payload });
const st = (sid) => LA._live.get(sid);
const texts = (root) => root.querySelectorAll('.la-chip').map(c => c.querySelector('.la-t').textContent + ' · ' + c.querySelector('.la-f').textContent);
/** what addMessage appends: an assistant message with its action row (the slot the verdict lands in) */
/** the user's bubble of a turn: as the app builds it, THIS avatar carries data-turn-id and reloads the turn's trace */
function userBubble(turn) {
  const b = new El('div', { class: 'message-bubble group flex items-start gap-4 justify-end' });
  b.innerHTML = `<div class="p-4"><p>You</p></div><div class="w-10 user-avatar clickable-avatar" data-turn-id="${turn}">U</div>`;
  return b;
}
function finalBubble(html) {
  const b = new El('div', { class: 'message-bubble group flex items-start gap-4' });
  b.innerHTML = `<div class="w-10 assistant-avatar-icon clickable-avatar long-press-replay">A</div><div class="p-4 rounded-xl glass-panel relative flex-1 min-w-0"><p class="font-bold">Assistant</p><div class="message-content">${html}</div><div class="msg-actions-row mt-2 flex items-center justify-between gap-3"><div class="msg-actions-slot flex items-center min-w-0"><button type="button" class="ac-toggle">Why trust this answer?</button></div><div class="pill"></div></div></div>`;
  return b;
}
const trust = (b) => b.querySelector('.la-trust');
const noClean = (b) => !/\bclean\b/i.test(b.querySelector('.la-line').textContent + ' ' + (trust(b) ? trust(b).textContent : '') + ' ' + (b.querySelector('.la-steps') ? b.querySelector('.la-steps').textContent : ''));

// ── off: a task_start without `stream` arms nothing ─────────────────────────────────────────
LA.onTaskStart('s1', { task_id: 't0' });
check('a task_start WITHOUT stream arms nothing (the switch is off there)', !bubble('s1') && LA.contentHost('s1') === null && LA.active('s1') === false);

// ── Optimize, LIVE: born → working → fold → writing → settle → sealed ───────────────────────
LA.onTaskStart('s1', { task_id: 't1', stream: true });
const b1 = bubble('s1');
check('BORN at task_start: one bubble, busy, ONE work line (dot · words · clock · hairline), the receipt open, NO rail, NO seal, NO text yet',
  !!b1 && b1.getAttribute('aria-busy') === 'true' && b1.querySelectorAll('.la-line').length === 1 && !!b1.querySelector('.la-dot') && !!b1.querySelector('.la-clock') && !!b1.querySelector('.la-track')
  && !b1.querySelector('.la-rail') && !b1.querySelector('.la-seal') && !b1.querySelector('.la-seg') && !b1.querySelector('.la-status') && !b1.querySelector('.la-sum')
  && /Assembling the context/.test(b1.querySelector('.la-lead').textContent) && (b1.querySelector('.la-content').textContent || '') === '' && log.children.length === 1);
check('the bubble is NOT an assistant message (no turn id, no badge) — the capsule/pin/feedback observers skip it', !b1.querySelector('[data-turn-id]') && !b1.querySelector('.assistant-badge'));
const h1 = LA.contentHost('s1'), h2 = LA.contentHost('s1');
check('contentHost hands ui.js the SAME bubble twice — one bubble, never two', h1 && h2 && h1.wrapper === b1 && h1.content === h2.content && h1.content.classList.contains('message-content') && log.children.length === 1);
notif('execution_start', { session_id: 's1', profile_type: 'tool_enabled', profile_tag: 'OPTIM' });
check('execution_start names the agent on the work line', /@OPTIM/.test(b1.querySelector('.la-lead').textContent) && st('s1').cls === 'tool_enabled');
ev('s1', 'message', { type: 'context_window_snapshot', details: 'Context: 5.0K/923K (0.5%) | Syst:1.1K', payload: { contributions: [{}, {}, {}] } });   // the executor's real shape: prose in details, data in payload
let chips = b1.querySelectorAll('.la-chip');
check('context assembled → a receipt row in consumer words with its fact and a timeline mark; NO phase key column', chips.length === 1 && /Assembled the context/.test(chips[0].textContent) && /3 modules/.test(chips[0].textContent) && !!chips[0].querySelector('.la-mark') && !chips[0].querySelector('.la-k') && chips[0].children[0].classList.contains('la-mark'));
check('the receipt is OPEN while working (depth 2 live) and the line is not yet a control', !b1.querySelector('.la-proc').hidden && b1.querySelector('.la-proc').classList.contains('la-open') && b1.querySelector('.la-line').getAttribute('role') === 'status');
ev('s1', 'message', { type: 'plan_generated', details: { phase_count: 2 } });
check('planning: the hairline moves to Reason and the plan row carries its fact', st('s1').phase === 'reason' && /Planned the work/.test(b1.querySelector('.la-steps').textContent) && /2 phases/.test(b1.querySelector('.la-steps').textContent));
ev('s1', 'tool_intent', { details: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT * FROM fitness_db.Sales' } } });
let tool = b1.querySelectorAll('.la-chip').find(c => /Queried/.test(c.textContent));
check('a tool call lands as a RUNNING row in consumer words (the table it queries, never the tool id)', !!tool && tool.classList.contains('la-run') && /Queried fitness_db\.Sales/.test(tool.textContent) && !/base_readQuery/.test(tool.textContent) && /Queried/.test(b1.querySelector('.la-lead').textContent));
ev('s1', 'tool_result', { type: 'tool_result', details: { tool_name: 'base_readQuery', row_count: 32 } });
check('its result SNAPS the row to its fact (rows); under the stub the measured duration is 0 ms, which the rule renders as nothing', !tool.classList.contains('la-run') && tool.classList.contains('la-snap') && /32 rows/.test(tool.textContent) && st('s1').chips.find(c => /Queried/.test(c.text)).dur != null && tool.querySelector('.la-dur').textContent.trim() === '');
ev('s1', 'token_update', { turn_input: 100, turn_output: 20 });
check('the clock reads the turn tokens, on the line, once', /120 tokens/.test(b1.querySelector('.la-clock').textContent) && b1.querySelectorAll('.la-clock').length === 1);
check('nothing folded yet: the line still reads the status words', st('s1').folded === false && !/Writing|Done/.test(b1.querySelector('.la-line').textContent));
LA.firstToken('s1');
const line1 = b1.querySelector('.la-line');
check('FOLD at the first token: the receipt folds, the SAME line now reads "Writing · 1 query" (the answer is still being written — never "Done" here) with a chevron, and becomes a control', st('s1').folded && b1.querySelector('.la-proc').hidden === true && /Writing/.test(line1.textContent) && !/Done|Worked/.test(line1.textContent) && /1 query/.test(line1.textContent) && line1.getAttribute('role') === 'button' && line1.getAttribute('tabindex') === '0' && line1.getAttribute('aria-expanded') === 'false' && st('s1').phase === 'draft');
check('the folded line carries NO verdict word (the verdict has its own slot)', !/clean|Verified|finding/i.test(line1.textContent));
line1.click();
check('click → the receipt reopens in place (depth 2 on demand)', b1.querySelector('.la-proc').hidden === false && line1.getAttribute('aria-expanded') === 'true');
line1.click();
check('click again → folded', b1.querySelector('.la-proc').hidden === true);
// stable block model
check('stableText holds back an unclosed code fence', LA.stableText('Here:\n```sql\nSELECT 1') === 'Here:\n' && LA.stableText('Here:\n```sql\nSELECT 1\n```\ndone') === 'Here:\n```sql\nSELECT 1\n```\ndone');
check('stableText holds back a table until its header row is closed (the separator row)', LA.stableText('Top:\n| a | b |\n| 1 |') === 'Top:' && LA.stableText('Top:\n| a | b |\n|---|---|\n| 1 | 2 |') === 'Top:\n| a | b |\n|---|---|\n| 1 | 2 |');
check('plain text passes through untouched', LA.stableText('The five members are:') === 'The five members are:');
const content = h1.content;
check('renderText renders the (stable) markdown into the host', LA.renderText(content, 'Hello **world**\n\n| a |\n| x |') === true && /<p>Hello/.test(content.innerHTML) && !/\| a \|/.test(content.innerHTML));
// the checks report BEFORE the final answer: they land as the receipt's rows, the findings count
ev('s1', 'message', { type: 'grounding_gate', details: { decision: 'annotate', reason: 'a claim the sources do not support' } });
ev('s1', 'message', { type: 'guardrail', details: { acted: true, action: 'redact', categories: ['pii'] } });
ev('s1', 'message', { type: 'honesty', details: { acted: false } });
ev('s1', 'message', { type: 'compute_lineage', details: { receipted: 2, unreceipted: 0 } });
ev('s1', 'llm_execution_complete', { details: {} });
const gates1 = b1.querySelectorAll('.la-chip.la-gate');
check('each check is a receipt row in consumer words with the state as its mark: sources warn · personal data danger · claims pass · figures pass; findings = 2',
  gates1.length === 4 && /Checked against its sources/.test(gates1[0].textContent) && gates1[0].classList.contains('la-warn') && /Screened for personal data/.test(gates1[1].textContent) && gates1[1].classList.contains('la-warn')
  && /Checked the claims/.test(gates1[2].textContent) && gates1[2].classList.contains('la-pass') && /Traced the figures/.test(gates1[3].textContent) && gates1[3].classList.contains('la-pass') && st('s1').findings === 2 && st('s1').phase === 'screen', gates1.map(g => g.className + ' ' + g.textContent));
check('a check row never leaks a wire token', !/annotate|redact|decision|action/.test(b1.querySelector('.la-steps').textContent));
check('textDone with an outcome KEEPS the bubble for the settle', LA.textDone('s1', 'complete') === true && !!bubble('s1'));
// the authoritative message lands (what addMessage appends), then the settle
const fin = finalBubble('<p>Juan leads with <mark class="figure-receipted">17,708</mark> miles. <mark class="claim-unsupported">All five are Platinum.</mark></p>');
log.appendChild(fin);
check('settle() moves the line and the receipt ONTO the authoritative message, before its text, and removes the living bubble',
  LA.settle('s1', fin, { partial: false, turnId: 7 }) === true && !bubble('s1') && log.children.length === 1
  && (() => { const box = fin.querySelector('.message-content').parentNode; const kids = box.children.map(c => c.className.split(' ')[0]); const iC = kids.indexOf('message-content'); const at = k => kids.indexOf(k); return ['la-line', 'la-proc'].every(k => at(k) >= 0 && at(k) < iC) && box.classList.contains('la-settled') && !box.querySelector('.la-rail') && !box.querySelector('.la-status') && !box.querySelector('.la-sum'); })());
const t1 = trust(fin);
check('the ONE verdict lands on the action row, FIRST in the slot: "2 findings", worth-a-look, a real button', !!t1 && /2 findings/.test(t1.textContent) && t1.classList.contains('la-warn') && t1.tagName === 'BUTTON' && fin.querySelector('.msg-actions-slot').children[0] === t1, t1 && (t1.className + ' | ' + t1.textContent + ' | ' + fin.querySelector('.msg-actions-slot').children.map(c => c.className)));
const acT = fin.querySelector('.ac-toggle');
check('ONE trust control: the capsule\'s "Why trust this answer?" toggle is folded BEHIND the chip (kept for its listener + state, out of the tab order, hidden from readers) — never a second button', acT.classList.contains('la-folded-toggle') && acT.getAttribute('tabindex') === '-1' && acT.getAttribute('aria-hidden') === 'true' && !!t1.querySelector('.la-tchev') && t1.getAttribute('aria-expanded') === 'false');
{ const _ol = fin.querySelector('.la-steps'); const _keys = _ol.querySelectorAll('.la-key'); const _first = _ol.querySelector('.la-chip.la-gate');
  check('THE KEY: exactly one labelled hairline "Checks on the draft" sits immediately before the first check row — green (trust) is never an unexplained colour change', _keys.length === 1 && _keys[0].nextElementSibling === _first && /Checks on the draft/.test(_keys[0].textContent) && _keys[0].getAttribute('role') === 'presentation');
  const _css2 = fs.readFileSync(new URL('../static/css/main.css', import.meta.url), 'utf8').replace(/\n/g, ' ');
  check('Done is carried by SHAPE: the settled work-line dot is a monochrome check mark (a mask), never a third colour', /\.la-line\.la-settled \.la-dot[^}]*mask: url\("data:image\/svg\+xml/.test(_css2) && /\.la-line\.la-settled \.la-dot[^}]*background: var\(--text-primary/.test(_css2)); }
check('the verdict is stated ONCE: not on the line, not in the receipt (Rule 3.0)', fin.querySelectorAll('.la-trust').length === 1 && !/finding/.test(fin.querySelector('.la-line').textContent) && !/2 findings/.test(fin.querySelector('.la-steps').textContent), [fin.querySelectorAll('.la-trust').length, fin.querySelector('.la-line').textContent]);
check('the word "clean" is not rendered anywhere on the answer', noClean(fin));
check('the settled line reads Done · 1 query with the frozen clock, and the hairline is done (100%)', /Done/.test(fin.querySelector('.la-line').textContent) && /1 query/.test(fin.querySelector('.la-line').textContent) && fin.querySelector('.la-fill').style.width === '100%' && fin.querySelector('.la-line').classList.contains('la-settled'));
let toggled = 0; fin.querySelector('.ac-toggle').addEventListener('click', () => { toggled += 1; acT.setAttribute('aria-expanded', acT.getAttribute('aria-expanded') === 'true' ? 'false' : 'true'); });
t1.click();
check('clicking the chip opens the trust record (the folded toggle\'s own click) and the chip MIRRORS the open state (chevron turns, aria-expanded)', toggled === 1 && t1.classList.contains('la-open') && t1.getAttribute('aria-expanded') === 'true');
t1.click();
check('…and closes it again', toggled === 2 && !t1.classList.contains('la-open') && t1.getAttribute('aria-expanded') === 'false');
check('the verdict says what it means in words on hover (a themed tooltip, never a native title) and says it opens the record', t1.classList.contains('u-tip') && /found something/.test(t1.getAttribute('data-tip')) && /Opens the trust record/.test(t1.getAttribute('data-tip')) && !t1.getAttribute('title') && /Opens why you can trust/.test(t1.getAttribute('aria-label')));
check('the marks on the final text are armed to LAND (claim draws, receipt underlines)', fin.querySelector('mark.claim-unsupported').classList.contains('la-draw') && fin.querySelector('mark.figure-receipted').classList.contains('la-rcpt'));
LA.onNotification('turn_score_complete', { session_id: 'other', turn_id: 7 });
check('another session\'s score never seals this turn', !st('s1').sealed && !/Sealed/.test(t1.getAttribute('data-tip')));
LA.onNotification('turn_score_complete', { session_id: 's1', turn_id: 7 });
check('SEALED on the score: the verdict\'s tooltip says so, verification is announced once — no seal glyph, no second slot', st('s1').sealed && /Sealed in the provenance chain/.test(t1.getAttribute('data-tip')) && /Verification complete: 2 findings/.test(doc.getElementById('answer-stream-status').textContent) && !fin.querySelector('.la-seal') && fin.querySelectorAll('.la-trust').length === 1);
LA.ended('s1');
check('the stream\'s end releases the turn', LA.active('s1') === false && !LA._live.get('s1'));

// ── the HONEST verdict: no check ran → "Not checked", never Verified, never clean ────────────
window.__currentSessionId = 'v1';
LA.onTaskStart('v1', { task_id: 'tv', stream: true });
ev('v1', 'tool_intent', { details: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT count(*) FROM x.customers' } } });
ev('v1', 'tool_result', { details: { row_count: 1 } });
ev('v1', 'llm_execution_complete', { details: {} });
LA.firstToken('v1'); LA.textDone('v1', 'complete');
const finV = finalBubble('<p>747 customers.</p>'); log.appendChild(finV); LA.settle('v1', finV, { partial: false, turnId: 8 });
check('NO gate ran → the verdict reads "Not checked" (absence family: dashed), never "Verified", never "clean"', !!trust(finV) && /Not checked/.test(trust(finV).textContent) && trust(finV).classList.contains('la-na') && !/Verified|clean/i.test(trust(finV).textContent) && /No check ran/.test(trust(finV).getAttribute('data-tip')) && noClean(finV));
LA.onNotification('turn_score_complete', { session_id: 'v1', turn_id: 8 });
check('a sealed-but-unchecked turn stays "Not checked" — sealing is not a check', /Not checked/.test(trust(finV).textContent) && /no check ran/i.test(doc.getElementById('answer-stream-status').textContent));
LA.ended('v1');
window.__currentSessionId = 'v2';
LA.onTaskStart('v2', { task_id: 'tv2', stream: true });
ev('v2', 'message', { type: 'grounding_gate', details: { decision: '', reason: 'no groundable content in this answer' } });
ev('v2', 'message', { type: 'guardrail', details: { acted: false, action: 'pass', categories: [] } });
LA.firstToken('v2'); LA.textDone('v2', 'complete');
const finV2 = finalBubble('<p>ok</p>'); log.appendChild(finV2); LA.settle('v2', finV2, { partial: false, turnId: 9 });
check('a gate with nothing to check is not a check that ran; one gate that DID run clean → "Verified" naming what it checked', /Verified/.test(trust(finV2).textContent) && trust(finV2).classList.contains('la-pass') && /personal data and harm/.test(trust(finV2).getAttribute('data-tip')) && !/sources/.test(trust(finV2).getAttribute('data-tip')) && Object.keys(st('v2').gates).length === 1 && noClean(finV2));
LA.ended('v2');
check('verdictOf is the one rule: findings win, then ran-clean, then not-checked; a block reads danger', (() => { const f = LA._fresh('x'); const a = LA.verdictOf(f); f.gates.guardrail = 'pass'; const b = LA.verdictOf(f); f.gates.grounding = 'danger'; f.findings = 1; const c = LA.verdictOf(f); return a.word === 'Not checked' && a.state === 'na' && b.word === 'Verified' && b.state === 'pass' && c.word === '1 finding' && c.state === 'danger'; })());

// ── the frame's `checks`: a clean check is quiet on the live stream by design, so the settle reads
//    the stamps the final_answer carries — the SAME reducer the reload uses; never twice ────────────
window.__currentSessionId = 'k1';
LA.onTaskStart('k1', { task_id: 'tk', stream: true });
ev('k1', 'message', { type: 'guardrail', details: { acted: true, action: 'redact', categories: ['pii'] } });   // the live stream announced this one (it acted)
LA.firstToken('k1'); LA.textDone('k1', 'complete');
const finK = finalBubble('<p>x</p>'); log.appendChild(finK);
LA.settle('k1', finK, { partial: false, turnId: 5, checks: { grounding: { decision: 'pass' }, guardrail: { ran: true, acted: true, action: 'redact' }, honesty: { ran: true, acted: false, action: 'pass' } } });
const kRows = texts(finK);
check('settle(checks): the quiet clean checks land as rows; the check the stream already reported is NOT counted twice (one row, one finding)', kRows.length === 3 && kRows.filter(r => /Screened for personal data/.test(r)).length === 1 && /Checked against its sources · in scope/.test(kRows.join('|')) && /Checked the claims/.test(kRows.join('|')) && st('k1').findings === 1 && /1 finding/.test(trust(finK).textContent));
check('…and the tooltip names every check the verdict rests on', /found something/.test(trust(finK).getAttribute('data-tip')));
LA.ended('k1');
window.__currentSessionId = 'k2';
LA.onTaskStart('k2', { task_id: 'tk2', stream: true });
ev('k2', 'tool_intent', { details: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 1 FROM t.x' } } }); ev('k2', 'tool_result', { details: { row_count: 1 } });
LA.firstToken('k2'); LA.textDone('k2', 'complete');
const finK2 = finalBubble('<p>x</p>'); log.appendChild(finK2);
LA.settle('k2', finK2, { partial: false, turnId: 6, checks: { guardrail: { ran: true, acted: false, action: 'pass' }, honesty: { ran: true, acted: false, action: 'pass' } } });
check('the check rows land in ONE order whatever order they arrived in (a live lineage event before the frame\'s sources check)', (() => { const ids = st('k2') ? [] : []; return true; })() && (() => { window.__currentSessionId = 'k4'; LA.onTaskStart('k4', { task_id: 'tk4', stream: true }); ev('k4', 'message', { type: 'compute_lineage', details: { receipted: 1, unreceipted: 0 } }); LA.firstToken('k4'); LA.textDone('k4', 'complete'); const f = finalBubble('<p>x</p>'); log.appendChild(f); LA.settle('k4', f, { checks: { grounding: { decision: 'pass' }, guardrail: { ran: true, acted: false, action: 'pass' } } }); const t = texts(f).map(x => x.split(' · ')[0]); LA.ended('k4'); return JSON.stringify(t) === JSON.stringify(['Checked against its sources', 'Screened for personal data and harm', 'Traced the figures']); })());
check('a turn whose only checks were quiet reads Verified — from the frame, not from a guess', /Verified/.test(trust(finK2).textContent) && /personal data and harm · its claims/.test(trust(finK2).getAttribute('data-tip')) && texts(finK2).length === 3);
check('an empty checks record leaves the verdict honest: Not checked', (() => { window.__currentSessionId = 'k3'; LA.onTaskStart('k3', { task_id: 'tk3', stream: true }); LA.firstToken('k3'); LA.textDone('k3', 'complete'); const f = finalBubble('<p>x</p>'); log.appendChild(f); LA.settle('k3', f, { checks: {} }); const ok = /Not checked/.test(trust(f).textContent); LA.ended('k3'); return ok; })());
LA.ended('k2');

// ── depth 3: a row opens ITS Live Status card on both paths — by IDENTITY (the key the receipt's reducer
// wrote onto the event, worn by the card as data-la), never by the words a card happens to contain ──
globalThis.setTimeout = (fn, ms) => { if (ms >= 1000) return 0; fn(); return 1; };   // the highlight LASTS; polls run at once
const statusHost = new El('div', { id: 'status-window-content' }); body.appendChild(statusHost); doc._register('status-window-content', statusHost);
const cb = new El('input', { id: 'toggle-status-checkbox', type: 'checkbox' }); cb.checked = false; body.appendChild(cb); doc._register('toggle-status-checkbox', cb);
let cbChanged = 0; cb.addEventListener('change', () => { cbChanged += 1; });
const hot = id => { const e = doc.getElementById(id); return !!(e && e.classList.contains('la-card-hot')); };
const cool = () => statusHost.querySelectorAll('.status-step').forEach(c => c.classList.remove('la-card-hot'));
// the panel as the app really renders it: the engine's start card lists EVERY available tool, the
// context card names the Knowledge Graph module, a tool call is an Executing card AND a Completed card
const PANEL = '<div class="status-step" id="s-ctx" data-la="ctx"><h4>Context Window Assembly</h4><div>Knowledge Graph 1.2k · Tool Definitions 3.4k</div></div>'
  + '<div class="status-step" id="s-kg" data-la="kg"><h4>Knowledge Graph Enrichment (46 nodes)</h4></div>'
  + '<div class="status-step" id="s-start"><h4>Ideate Engine Started (3 tools available)</h4><div><span>base_readQuery</span><span>TDA_KnowledgeGraph</span></div></div>'
  + '<div class="status-step" id="s-t1i" data-la="tool1"><h4>Executing Tool: base_readQuery</h4></div><div class="status-step" id="s-t1c" data-la="tool1"><h4>Tool Completed: base_readQuery (0.1s)</h4></div>'
  + '<div class="status-step" id="s-t2i" data-la="tool2"><h4>Executing Tool: base_readQuery</h4></div><div class="status-step" id="s-t2c" data-la="tool2"><h4>Tool Completed: base_readQuery</h4></div>'
  + '<div class="status-step" id="s-lin" data-la="gate:lineage"><h4>Number Lineage</h4></div>';
statusHost.innerHTML = PANEL; LA.panelTurn(LA.turnStamp('rl', 3));
const userD = userBubble(3); log.appendChild(userD);
const finD = finalBubble('<p>x</p>'); log.appendChild(finD);
const turnD = { turn: 3, duration_ms: 1000, context_window_snapshot_event: { modules: { a: 1, b: 1 } }, kg_enrichment_event: { total_entities: 46, entity_count: 46 },
  conversation_agent_events: [
  { type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT COUNT(*) FROM a' } } }, { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', result_rows: 1, duration_ms: 120 } },
  { type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT * FROM x' } } }, { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', result_rows: 9, duration_ms: 12 } } ],
  grounding: { decision: 'pass', ran: true }, guardrail: { ran: true, acted: false, action: 'pass' }, compute_lineage: { ran: true, figures: 2, receipts: 2, unreceipted: [] } };
LA.restore(finD, turnD, { sessionId: 'rl' });
const dRows = [...finD.querySelectorAll('.la-chip')]; const rowOf = id => dRows.find(li => li.getAttribute('data-chip') === id);
let avClicks = 0, ownAvClicks = 0; userD.querySelector('.clickable-avatar').addEventListener('click', () => { avClicks += 1; }); finD.querySelector('.clickable-avatar').addEventListener('click', () => { ownAvClicks += 1; });
rowOf('kg').click();
check('the KG row lands on the KG card — not the context card, whose body names the Knowledge Graph module (where the words used to land it)', hot('s-kg') && !hot('s-ctx') && cb.checked === true && cbChanged === 1); cool();
rowOf('tool1').click();
check('the FIRST query row lands on its OWN result card — not the engine\'s start card, whose body lists every available tool', hot('s-t1c') && !hot('s-t1i') && !hot('s-start') && !hot('s-t2c')); cool();
rowOf('tool2').click();
check('the SECOND query row lands on the SECOND call\'s result card (ordinal by identity, not by which cards mention the tool)', hot('s-t2c') && !hot('s-t1c') && !hot('s-t2i')); cool();
rowOf('gate:lineage').click();
check('a check with a card lands on it', hot('s-lin')); cool();
// an Optimize turn keeps its cards INSIDE collapsed phase containers (nested for a sub-phase), and a
// filter pill may have hidden the category — the link opens every ancestor and re-enables the category
// THROUGH ITS PILL, then lands (operator finding: the click "didn't work" on a card inside a phase)
{ const outer = new El('details', { class: 'status-phase-container', 'data-filter-category': 'planning' }); outer._classes.add('status-phase-container');
  const inner = new El('details', { class: 'status-phase-container' }); inner._classes.add('status-phase-container');
  const cardP = new El('div', { class: 'status-step', id: 's-p2', 'data-la': 'tool2', 'data-filter-category': 'tools' }); cardP._classes.add('status-step'); cardP._classes.add('event-filtered-hidden');
  statusHost.querySelector('#s-t2c').remove(); doc._register('s-p2', cardP);
  inner.appendChild(cardP); outer.appendChild(inner); statusHost.appendChild(outer);
  const pill = new El('button', { class: 'event-filter-chip', 'data-filter': 'tools' }); pill._classes.add('event-filter-chip'); body.appendChild(pill);
  let pillClicks = 0; pill.addEventListener('click', () => { pillClicks += 1; cardP.classList.remove('event-filtered-hidden'); });
  rowOf('tool2').click();
  check('a card inside NESTED collapsed phase containers: every ancestor container is opened, then the card is landed on', outer.open && inner.open && hot('s-p2'));
  check('a card whose category a pill filtered out: the pill is clicked (the chip stays in step), never a silent class removal', pillClicks === 1 && !cardP.classList.contains('event-filtered-hidden'));
  cool(); pill.remove(); }
const capToggle = finD.querySelector('.ac-toggle'); capToggle.setAttribute('aria-expanded', 'false'); capToggle.addEventListener('click', () => capToggle.setAttribute('aria-expanded', capToggle.getAttribute('aria-expanded') === 'true' ? 'false' : 'true'));
rowOf('gate:grounding').click();
check('a check that found nothing has no card by design: its row opens the trust record — never a reload that lands nowhere', capToggle.getAttribute('aria-expanded') === 'true' && avClicks === 0 && ![...statusHost.querySelectorAll('.status-step')].some(c => c.classList.contains('la-card-hot')));
check('…and says so', /trust record/.test(rowOf('gate:grounding').getAttribute('aria-label')) && /Live Status card\.$/.test(rowOf('tool1').getAttribute('aria-label')));
check('a duration under 50 ms shows no number (a fact worth a number, or nothing)', rowOf('tool1').querySelector('.la-dur').textContent === '0.1s' && rowOf('tool2').querySelector('.la-dur').textContent === '');
// the words fallback — a card rendered without a key — matches the card's TITLE only, in order
statusHost.innerHTML = '<div class="status-step" id="w-start"><h4>Ideate Engine Started</h4><div>base_readQuery</div></div><div class="status-step" id="w-1"><h4>Tool Completed: base_readQuery</h4></div><div class="status-step" id="w-2"><h4>Tool Completed: base_readQuery</h4></div>';
rowOf('tool2').click();
check('a card without a key is matched by its title, never its body: the second query row lands on the second titled card, not the start card', hot('w-2') && !hot('w-1') && !hot('w-start')); cool();
// the key is an ordinal WITHIN a turn: the panel showing ANOTHER turn's tool1 must never be taken for this one
statusHost.innerHTML = '<div class="status-step" id="o-1" data-la="tool1"><h4>Tool Completed: base_readQuery</h4></div>'; LA.panelTurn(LA.turnStamp('rl', 2));
userD.querySelector('.clickable-avatar').addEventListener('click', () => { statusHost.innerHTML = PANEL; LA.panelTurn(LA.turnStamp('rl', 3)); });
rowOf('tool1').click();
check('when the panel shows another turn, the same-ordinal card there is NOT taken: the row loads THIS turn\'s trace through the TURN\'s avatar (the user avatar that carries its number — the assistant\'s own is a replay, never a reload) and then lands on its card', avClicks === 1 && ownAvClicks === 0 && hot('s-t1c') && !doc.getElementById('o-1'));
cool();
statusHost.innerHTML = '<div class="status-step" id="x-1" data-la="tool1"><h4>Tool Completed: base_readQuery</h4></div>'; LA.panelTurn(LA.turnStamp('OTHER-SESSION', 3));
rowOf('tool1').click();
check('…and a stamp left by another session with the same turn number never matches either', avClicks === 2 && !hot('x-1'));
// stampTurn: the receipt writes its keys onto the persisted records the reload renders its cards from
{
  const t = JSON.parse(JSON.stringify(turnD)); t.execution_trace = [
    { action: { tool_name: 'TDA_SystemLog', arguments: { message: 'Strategic Meta-Plan Generated', details: [{ phase: 1 }, { phase: 2 }] } } },
    { action: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 1 FROM d.t' } }, result: { status: 'success', metadata: { row_count: 1 } } } ];
  check('stampTurn writes a key onto every record a card is rendered from', LA.stampTurn(t) === true
    && t.context_window_snapshot_event.__la === 'ctx' && t.kg_enrichment_event.__la === 'kg'
    && t.execution_trace[0].action.arguments.details.__la === 'plan' && t.execution_trace[1].action.__la === 'tool1' && t.execution_trace[1].result.__la === 'tool1'   // the trace is replayed first
    && t.conversation_agent_events[0].payload.__la === 'tool2' && t.conversation_agent_events[1].payload.__la === 'tool2' && t.conversation_agent_events[3].payload.__la === 'tool3'
    && t.grounding.__la === 'gate:grounding' && t.guardrail.__la === 'gate:guardrail' && t.compute_lineage.__la === 'gate:lineage',
    { ctx: t.context_window_snapshot_event.__la, a0: t.conversation_agent_events[0].payload.__la, g: t.grounding.__la, tr: t.execution_trace[1].action.__la });
  check('the key is a link, not a fact of the turn: it never enters the record\'s JSON', !/__la/.test(JSON.stringify(t)));
  check('stampTurn renders nothing and registers no receipt', !LA._live.get('__stamp') && LA.stampTurn(null) === false);
}
// the live path: the reducer marks the frame's payload — the very object the dispatch hands the card renderer
{
  window.__currentSessionId = 'lv'; LA.onTaskStart('lv', { task_id: 'tlv', stream: true });
  const pay = { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 1 FROM q.r' } };
  ev('lv', 'message', { type: 'conversation_tool_invoked', payload: pay });
  check('a live frame\'s payload carries the row\'s key the instant the row lands (before the dispatch renders the card)', pay.__la === 'tool1');
  check('a live turn declares itself as what the panel shows', LA.panelTurn() === 'live:lv');
  // a model fires several calls in one round: each completion closes the OLDEST open call of that tool
  const pay2 = { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 2 FROM q.s' } };
  ev('lv', 'message', { type: 'conversation_tool_invoked', payload: pay2 });
  ev('lv', 'message', { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', result_rows: 5 } });
  const lvRows = () => [...LA._live.get('lv').chips];
  check('two open calls, one completion: the FIRST closes, the second still runs (never the last one, as a single slot did)', lvRows()[0].fact === '5 rows' && !lvRows()[0].running && lvRows()[1].running === true);
  ev('lv', 'message', { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', result_rows: 7 } });
  check('…and the next completion closes the second', lvRows()[1].fact === '7 rows' && !lvRows()[1].running);
  ev('lv', 'message', { type: 'conversation_tool_invoked', payload: { tool_name: 'TDA_KnowledgeGraph', arguments: {} } });
  LA.firstToken('lv'); LA.textDone('lv', 'complete');
  const fLv = finalBubble('<p>x</p>'); log.appendChild(fLv);
  LA.settle('lv', fLv, { partial: false, turnId: 9, checks: {} });
  check('a settled receipt never reads "running": a call whose completion never arrived is closed as it stands', lvRows().every(c => !c.running) && lvRows()[2].fact === 'done');
  check('at settle the live panel takes the turn\'s number', LA.panelTurn() === LA.turnStamp('lv', 9));
  LA.ended('lv');
}
// the words: every entity a statement reads, not the first FROM
check('a join names every table it reads, once each, in order', LA._toolWords('base_readQuery', { sql: 'SELECT s.id FROM fitness_db.Sales s JOIN fitness_db.Customers c ON s.c=c.c LEFT JOIN fitness_db.SaleDetails d ON d.s=s.id JOIN fitness_db.Sales s2 ON 1=1' }) === 'Queried fitness_db.Sales, fitness_db.Customers, fitness_db.SaleDetails');
check('a CTE\'s name, a derived table and a function\'s FROM are not tables', JSON.stringify(LA._sqlTables('WITH t AS (SELECT * FROM a.x) SELECT EXTRACT(YEAR FROM d) FROM t JOIN (SELECT 1 FROM a.y) z ON 1=1')) === JSON.stringify(['a.x', 'a.y']));
check('more than three tables are counted, not listed', LA._toolWords('base_readQuery', { sql: 'SELECT 1 FROM a.b JOIN a.c ON 1=1 JOIN a.d ON 1=1 JOIN a.e ON 1=1' }) === 'Queried a.b, a.c, a.d +1 more');
// the card side (ui.js / eventHandlers.js / sessionManagement.js), on their source: the key is read at the card's creation and the reload stamps its turn first
{
  const fs = await import('fs');
  const ui = fs.readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');
  const eh = fs.readFileSync(new URL('../static/js/eventHandlers.js', import.meta.url), 'utf8');
  const sm = fs.readFileSync(new URL('../static/js/handlers/sessionManagement.js', import.meta.url), 'utf8');
  check('every card renderer stamps the key at the card\'s creation (standard, genie, conversation)', (ui.match(/_stampLa\(stepEl, eventData\)/g) || []).length >= 3 && /function _laKeyOf\(eventData\)/.test(ui) && /eventData\.details\.__la/.test(ui) && /stepEl\.dataset\.la = k/.test(ui));
  check('a reload stamps the turn\'s records BEFORE rendering a card, and declares the turn the panel shows', /stampTurn\(turnData\); window\.LivingAnswer\.panelTurn\(window\.LivingAnswer\.turnStamp\(sessionId, turnId\)\)/.test(eh) && eh.indexOf('LivingAnswer.stampTurn(turnData)') < eh.indexOf("payload: { type: 'grounding_gate', step: 'Grounding Gate', details: turnData.grounding }"));
  check('a card built from HTML (the context snapshot) forwards the payload\'s key', (eh.match(/_la: snapshot(Payload|Event)\.__la/g) || []).length === 3);
  check('the session UI cache carries which turn the cached panel shows', /statusTurn: DOM\.statusWindowContent\.dataset\.laTurn/.test(sm) && /dataset\.laTurn = cached\.statusTurn/.test(sm));
}
statusHost.innerHTML = ''; LA.panelTurn('');   // the later sections start from an empty panel

// ── the capsule mounts its toggle AFTER the settle (an observer): the fold still happens ─────────
{
  const _MO = globalThis.MutationObserver; const observers = [];
  globalThis.MutationObserver = class { constructor(fn) { this.fn = fn; observers.push(this); } observe() { this.on = true; } disconnect() { this.on = false; } };
  window.__currentSessionId = 'm1';
  LA.onTaskStart('m1', { task_id: 'tm', stream: true }); LA.firstToken('m1'); LA.textDone('m1', 'complete');
  const finM = new El('div', { class: 'message-bubble group flex items-start gap-4' });
  finM.innerHTML = '<div class="w-10 assistant-avatar-icon" data-turn-id="9">A</div><div class="p-4 glass-panel"><p>Assistant</p><div class="message-content"><p>x</p></div><div class="msg-actions-row"><div class="msg-actions-slot flex"></div><div class="pill"></div></div></div>';
  log.appendChild(finM);
  LA.settle('m1', finM, { partial: false, turnId: 9, checks: { guardrail: { ran: true, acted: false, action: 'pass' } } });
  const slotM = finM.querySelector('.msg-actions-slot');
  check('with no toggle yet, the chip lands alone and an observer is armed on the slot', !!slotM.querySelector('.la-trust') && observers.length === 1 && observers[0].on === true);
  const late = new El('button', { class: 'ac-toggle', 'aria-expanded': 'false' }); late.textContent = 'Why trust this answer?'; slotM.appendChild(late);
  observers[0].fn([]);
  check('the toggle that arrives later is folded the moment it lands, and the observer lets go', late.classList.contains('la-folded-toggle') && late.getAttribute('tabindex') === '-1' && late.getAttribute('aria-hidden') === 'true' && observers[0].on === false);
  let lateClicks = 0; late.addEventListener('click', () => { lateClicks += 1; late.setAttribute('aria-expanded', 'true'); });
  slotM.querySelector('.la-trust').click();
  check('…and the chip drives it (one control) and mirrors its open state', lateClicks === 1 && slotM.querySelector('.la-trust').classList.contains('la-open'));
  LA.ended('m1');
  globalThis.MutationObserver = _MO;
}

// ── Stop: the partial is kept, the cut is marked ─────────────────────────────────────────────
window.__currentSessionId = 's2';
LA.onTaskStart('s2', { task_id: 't2', stream: true });
ev('s2', 'tool_intent', { details: { tool_name: 'base_tableList', arguments: { database_name: 'fitness_db' } } });
LA.stopping('s2');
check('Stop pressed: the hairline stalls and the line says so', bubble('s2').querySelector('.la-fill').classList.contains('la-stalled') && /Stopping/.test(bubble('s2').querySelector('.la-lead').textContent));
LA.firstToken('s2'); LA.textDone('s2', 'partial');
const fin2 = finalBubble('<p>The tables are</p>'); log.appendChild(fin2);
LA.settle('s2', fin2, { partial: true });
check('a partial settle draws the "stopped here" hairline at the cut, the line reads Stopped, the answer is kept', !!fin2.querySelector('.la-cut') && fin2.querySelector('.la-cut').getAttribute('role') === 'note' && /Stopped/.test(fin2.querySelector('.la-lead').textContent) && /1 tool call/.test(fin2.querySelector('.la-line').textContent));
LA.ended('s2');
check('a stopped, unchecked, persisted partial reads "Not checked" — never a green "clean"', /Not checked/.test(trust(fin2).textContent) && /Stopped before/.test(trust(fin2).getAttribute('data-tip')) && noClean(fin2));

// ── error / cancel without a partial: nothing to settle into → the bubble is dropped ────────
window.__currentSessionId = 's3';
LA.onTaskStart('s3', { task_id: 't3', stream: true });
ev('s3', 'message', { type: 'context_window_snapshot', payload: {} });
check('textDone with NO outcome drops the bubble (an error leaves no ghost)', LA.textDone('s3') === false && !bubble('s3') && LA.active('s3') === false);

// ── session guard: a turn running in a session that is not on screen never paints here ───────
window.__currentSessionId = 's3';
LA.onTaskStart('s9', { task_id: 't9', stream: true });
ev('s9', 'message', { type: 'context_window_snapshot', payload: {} });
check('another session\'s turn arms its data but paints nothing on this screen', LA.active('s9') && !bubble('s9'));
window.__currentSessionId = 's9';
const h9 = LA.contentHost('s9');
check('switching to it mounts the bubble from its data (the row it already holds is rebuilt)', !!h9 && !!bubble('s9') && bubble('s9').querySelectorAll('.la-chip').length === 1);
LA.ended('s9');

// ── Focus ────────────────────────────────────────────────────────────────────────────────────
window.__currentSessionId = 'f1';
LA.onTaskStart('f1', { task_id: 'tf', stream: true });
notif('knowledge_retrieval_start', { session_id: 'f1' });
let kc = bubble('f1').querySelectorAll('.la-chip')[0];
check('Focus: retrieval starts as a running row, the hairline at Retrieve', kc && kc.classList.contains('la-run') && /Searched the knowledge repositories/.test(kc.textContent) && st('f1').phase === 'retrieve');
notif('knowledge_retrieval_complete', { session_id: 'f1', document_count: 6 });
check('…and snaps to its chunk count; the fold will say "6 documents"', /6 documents/.test(kc.textContent) && st('f1').counts.chunks === 6);
LA.firstToken('f1');
check('the Focus line counts documents', /6 documents/.test(bubble('f1').querySelector('.la-line').textContent));
LA.textDone('f1');
window.__currentSessionId = 'f2';
LA.onTaskStart('f2', { task_id: 'tf2', stream: true });
notif('knowledge_retrieval_complete', { session_id: 'f2', document_count: 0 });
check('nothing relevant found reads as worth a look (half mark), in words', bubble('f2').querySelector('.la-chip').classList.contains('la-warn') && /nothing relevant/.test(bubble('f2').querySelector('.la-chip').textContent));
LA.textDone('f2');

// ── Coordinate ───────────────────────────────────────────────────────────────────────────────
window.__currentSessionId = 'g1';
LA.onTaskStart('g1', { task_id: 'tg', stream: true });
notif('genie_slave_invoked', { session_id: 'g1', profile_tag: 'NIBRS' });
notif('genie_slave_invoked', { session_id: 'g1', profile_tag: 'CASEDF' });
notif('genie_slave_completed', { session_id: 'g1', profile_tag: 'NIBRS', success: true, duration_ms: 3200 });
notif('genie_slave_completed', { session_id: 'g1', profile_tag: 'CASEDF', success: false, error: 'x' });
const gch = bubble('g1').querySelectorAll('.la-chip');
check('Coordinate: one row per expert, each with its own outcome (answered · 3.2s / failed)', gch.length === 2 && /Asked @NIBRS/.test(gch[0].textContent) && /answered/.test(gch[0].textContent) && /3\.2s/.test(gch[0].querySelector('.la-dur').textContent) && /Asked @CASEDF/.test(gch[1].textContent) && /failed/.test(gch[1].textContent) && gch[1].classList.contains('la-err') && st('g1').counts.experts === 2);
notif('genie_synthesis_skipped', { session_id: 'g1' });
check('a pass-through says so instead of pretending a synthesis happened', /Passed the expert/.test(bubble('g1').querySelector('.la-steps').textContent));
LA.firstToken('g1');
check('the Coordinate line counts experts', /2 experts/.test(bubble('g1').querySelector('.la-line').textContent));
LA.textDone('g1');

// ── Conversation (ReAct) ─────────────────────────────────────────────────────────────────────
window.__currentSessionId = 'c1';
LA.onTaskStart('c1', { task_id: 'tc', stream: true });
notif('conversation_tool_invoked', { session_id: 'c1', tool_name: 'base_tableList', arguments: { database_name: 'fitness_db' } });
notif('conversation_tool_completed', { session_id: 'c1', tool_name: 'base_tableList', success: false, result_preview: '[Error 3802]' });
const cc = bubble('c1').querySelectorAll('.la-chip');
check('ReAct: a tool call in consumer words; a failed call snaps to "failed" with an outlined mark', /Listed the tables in fitness_db/.test(cc[0].textContent) && /failed/.test(cc[0].textContent) && cc[0].classList.contains('la-err'));
notif('conversation_tool_rewrite', { session_id: 'c1', kind: 'fanout' });
LA.onNotification('steer_consumed', { session_id: 'c1', details: { at: 'base_readQuery', steers: [{ id: 'x' }] } });
LA.onNotification('llm_recovery', { session_id: 'c1', details: { kind: 'fallback', to: 'Anthropic/claude-haiku', session_id: 'c1' } });
const all = bubble('c1').querySelectorAll('.la-chip');
check('a deterministic rewrite, a considered steer and a fallback each land as their own row', all.length === 4 && /Corrected the statement/.test(all[1].textContent) && /no LLM round spent/.test(all[1].textContent) && /Considered your steer/.test(all[2].textContent) && /at base_readQuery/.test(all[2].textContent) && /Served by the fallback/.test(all[3].textContent));
// THE MARK VOCABULARY: an act (a correction, a fallback) never wears a verdict colour — only a CHECK
// (la-gate) does; the stylesheet has no pass/warn/danger rule for la-fix, and a fix chip is not a gate
{ const _css = fs.readFileSync(new URL('../static/css/main.css', import.meta.url), 'utf8');
  const _fixRules = _css.split('\n').filter(l => /\.la-chip\.la-fix/.test(l));
  check('an act (correction / fallback) carries no verdict colour: no la-fix rule paints pass/warn/danger; the chip is la-fix, never la-gate',
        _fixRules.every(l => !/--trust-(pass|warn|danger)/.test(l)) && all[1].classList.contains('la-fix') && !all[1].classList.contains('la-gate') && all[3].classList.contains('la-fix') && !all[3].classList.contains('la-gate'), _fixRules);
  check('a done act is a SOLID dot at full text strength — never the muted grey of the absence family', /\.la-chip \.la-mark \{[^}]*background: var\(--text-primary/.test(_css.replace(/\n/g, ' ')) && !/\.la-chip \.la-mark \{[^}]*background: var\(--text-muted/.test(_css.replace(/\n/g, ' ')));
  check('a CHECK wears the trust family (the pass rule is la-gate.la-pass alone)', /\.la-chip\.la-gate\.la-pass \.la-mark \{ background: var\(--trust-pass/.test(_css) && !/\.la-chip\.la-fix \.la-mark,/.test(_css)); }
LA.firstToken('c1');
const cs = bubble('c1').querySelector('.la-line').textContent;
check('the ReAct line counts them apart: 1 tool call · 1 correction · 1 steer · 1 recovery', /1 tool call/.test(cs) && /1 correction/.test(cs) && /1 steer/.test(cs) && /1 recovery/.test(cs));
ev('c1', 'message', { type: 'compute_lineage', details: { receipted: 1, unreceipted: 2 } });
ev('c1', 'message', { type: 'honesty', details: { acted: false } });
check('two unreceipted figures read worth a look on their own row; the clean claims check does not hide it', bubble('c1').querySelector('.la-chip[data-chip="gate:lineage"]').classList.contains('la-warn') && /2 figures without a receipt/.test(bubble('c1').querySelector('.la-chip[data-chip="gate:lineage"]').textContent) && st('c1').findings === 1);
LA.textDone('c1');

// ── S6/S7 (review): a revision and the tool-surface exceptions ──────────────────────────────
window.__currentSessionId = 'r1';
LA.onTaskStart('r1', { task_id: 'tr', stream: true });
ev('r1', 'message', { type: 'grounding_gate', details: { decision: 'annotate', reason: 'x' } });
check('a grounding annotation counts one finding', st('r1').findings === 1);
LA.onNotification('outcome_revision', { session_id: 'r1', step: 'Outcome Revision', details: { revision: 1, cleared: true, session_id: 'r1' } });
const rch = bubble('r1').querySelectorAll('.la-chip');
check('a cleared revision lands as its own row, retires the finding it cleared', rch.length === 2 && /Revised the draft/.test(rch[1].textContent) && /the finding cleared/.test(rch[1].textContent) && st('r1').findings === 0 && st('r1').counts.revisions === 1 && rch[1].classList.contains('la-pass'));
LA.onNotification('tool_surface', { session_id: 'r1', counts: { offered: 5, total: 12, widened: 0 }, asked_denied: [] });
check('an ordinary surface (offered N of M, nothing found, nothing denied-asked) is NOT a row — Live Status detail, not the consumer surface', bubble('r1').querySelectorAll('.la-chip').length === 2);
LA.onNotification('tool_surface', { session_id: 'r1', counts: { offered: 5, total: 12, widened: 2 }, asked_denied: ['base_writeQuery'] });
const rch2 = bubble('r1').querySelectorAll('.la-chip');
check('the exceptions ARE rows: a widened surface (found 2 more tools) and a denied tool asked for (worth a look)', rch2.length === 4 && /Found 2 more tools/.test(rch2[2].textContent) && /Asked for a tool it may not use/.test(rch2[3].textContent) && /base_writeQuery/.test(rch2[3].textContent) && rch2[3].classList.contains('la-warn'));
LA.onNotification('tool_surface', { session_id: 'r1', counts: { offered: 5, total: 12, widened: 2 }, asked_denied: ['base_writeQuery'] });
check('a repeated surface notice with nothing new adds no row', bubble('r1').querySelectorAll('.la-chip').length === 4);
LA.firstToken('r1'); LA.textDone('r1', 'complete');
const finR = finalBubble('<p>Revised answer.</p>'); log.appendChild(finR);
LA.settle('r1', finR, { partial: false, turnId: 3 });
check('a cleared revision delivers a VERIFIED answer whose tooltip says it was revised (not "clean", not "1 finding")', /Verified/.test(trust(finR).textContent) && /revised once/.test(trust(finR).getAttribute('data-tip')) && /1 revision/.test(finR.querySelector('.la-line').textContent) && noClean(finR));
LA.ended('r1');
check('a non-cleared revision keeps the finding and reads it as worth a look', (() => { window.__currentSessionId = 'r2'; LA.onTaskStart('r2', { task_id: 'tr2', stream: true }); ev('r2', 'message', { type: 'grounding_gate', details: { decision: 'annotate', reason: 'x' } }); LA.onNotification('outcome_revision', { session_id: 'r2', details: { revision: 1, cleared: false } }); const rows = bubble('r2').querySelectorAll('.la-chip'); const ok = st('r2').findings === 1 && rows[1].classList.contains('la-warn') && /Tried a revision/.test(rows[1].textContent); LA.textDone('r2'); return ok; })());

// ── the vocabulary: known tools never leak their ids ─────────────────────────────────────────
const tw = LA._toolWords;
check('tool ids become consumer words', tw('base_readQuery', { sql: 'select 1' }) === 'Ran a query' && tw('base_databaseList') === 'Listed the databases' && tw('TDA_KnowledgeGraph') === 'Consulted the knowledge graph' && tw('TDA_Compute') === 'Computed the figures' && tw('dba_tableSpace') === 'Table space' && tw('TDA_SystemOrchestration') === 'Ran the query across the range');
check('the final report and the framework scaffolding are NOT steps (they are the drafting / the bookkeeping)', tw('TDA_FinalReport') === null && tw('TDA_SystemLog') === null && tw('TDA_CurrentDate') === null);

// ── the per-viewer opt-out ───────────────────────────────────────────────────────────────────
localStorage.setItem('tda_living_answer', '0');
window.__currentSessionId = 'o1';
LA.onTaskStart('o1', { task_id: 'to', stream: true });
check('opted out: a streaming turn arms nothing — the plain S1 bubble takes over', !bubble('o1') && LA.contentHost('o1') === null && LA.enabled() === false);
const finO = finalBubble('<p>x</p>');
check('opted out: a reloaded turn is left alone too (one preference, both paths)', LA.restore(finO, { turn: 1, duration_ms: 100 }) === false && !finO.querySelector('.la-line'));
localStorage.removeItem('tda_living_answer');

// ── RELOAD PARITY: restore() rebuilds the same chrome from the persisted turn ────────────────
// a ReAct turn as the platform persists it (conversation_agent_events + the gate stamps)
const reactTurn = {
  turn: 4, profile_type: 'conversation_with_tools', profile_tag: 'IDAIRL', duration_ms: 4180, turn_input_tokens: 37900, turn_output_tokens: 197,
  context_window_snapshot_event: { type: 'context_window_snapshot', contributions: [{}, {}, {}, {}, {}, {}] },
  kg_enrichment_event: { total_entities: 121, enrichments: [{ entity_count: '12' }] },
  conversation_agent_events: [
    { type: 'conversation_agent_start', payload: { profile_tag: 'IDAIRL' } },
    { type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT COUNT(DISTINCT customer_id) FROM airlines_core.accounts_receivable' } } },
    { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', success: true, result_rows: 1, duration_ms: 230 } },
    { type: 'conversation_llm_complete', payload: {} },
  ],
  grounding: { decision: 'pass', reason: 'observe', scope_databases: ['airlines_core'] },
  guardrail: { direction: 'outbound', ran: true, acted: false, action: 'pass', categories: [], mode: 'observe' },
  honesty: { gate: 'honesty', branch: 'sql', ran: true, acted: false, action: 'pass', mode: 'observe' },
  integrity: { ran: true, state: 'unsigned' }, provenance_chain: [{}, {}, {}],
};
window.__currentSessionId = 'rl';
const userRL = userBubble(4); log.appendChild(userRL);
const finRL = finalBubble('<p>We have 747 customers.</p>'); log.appendChild(finRL);
check('restore() on a reloaded ReAct turn builds the settled chrome: the line before the text, the receipt folded, the verdict in the slot', LA.restore(finRL, reactTurn, { sessionId: 'rl' }) === true && (() => { const box = finRL.querySelector('.message-content').parentNode; const kids = box.children.map(c => c.className.split(' ')[0]); return kids.indexOf('la-line') < kids.indexOf('message-content') && kids.indexOf('la-proc') < kids.indexOf('message-content') && box.classList.contains('la-settled') && finRL.querySelector('.la-proc').hidden === true; })() && !!trust(finRL));
check('…the line reads the persisted facts: Done · 1 query · 12 nodes, the clock 4.2s · 38.1k tokens (duration_ms + tokens from the turn, no timer)', /Done/.test(finRL.querySelector('.la-line').textContent) && /1 query/.test(finRL.querySelector('.la-line').textContent) && /12 nodes/.test(finRL.querySelector('.la-line').textContent) && /4\.2s/.test(finRL.querySelector('.la-clock').textContent) && /38\.1k tokens/.test(finRL.querySelector('.la-clock').textContent));
const rlRows = texts(finRL);
check('…the receipt holds the same rows a live turn would: context · knowledge graph · the query with its rows and duration · the three checks', rlRows.length === 6 && /Assembled the context · 6 modules/.test(rlRows[0]) && /Read the knowledge graph · 12 nodes/.test(rlRows[1]) && /Queried airlines_core\.accounts_receivable · 1 row/.test(rlRows[2]) && /0\.2s/.test(finRL.querySelectorAll('.la-chip')[2].querySelector('.la-dur').textContent) && /Checked against its sources · in scope/.test(rlRows[3]) && /Screened for personal data and harm · nothing found/.test(rlRows[4]) && /Checked the claims · claims only what it can show/.test(rlRows[5]));
check('…the verdict is derived by the SAME rule: three checks ran clean → Verified, tooltip names them, sealed', /Verified/.test(trust(finRL).textContent) && trust(finRL).classList.contains('la-pass') && /its sources · personal data and harm · its claims/.test(trust(finRL).getAttribute('data-tip')) && /Sealed/.test(trust(finRL).getAttribute('data-tip')) && noClean(finRL));
check('…no row is left "running" on a reloaded turn', finRL.querySelectorAll('.la-chip.la-run').length === 0);
check('restore() is idempotent (a second call adds nothing)', LA.restore(finRL, reactTurn, { sessionId: 'rl' }) === true && finRL.querySelectorAll('.la-line').length === 1 && finRL.querySelectorAll('.la-trust').length === 1);
let avatarClicks = 0; userRL.querySelector('.clickable-avatar').addEventListener('click', () => { avatarClicks += 1; });
finRL.querySelectorAll('.la-chip')[2].click();
check('depth 3 on a reloaded turn: a row opens the turn\'s trace (the avatar\'s own reload), since no live card exists', avatarClicks === 1);
const lineRL = finRL.querySelector('.la-line'); lineRL.click();
check('the restored line is the same control: click opens the receipt', finRL.querySelector('.la-proc').hidden === false && lineRL.getAttribute('aria-expanded') === 'true');
// parity: the SAME facts driven live produce the SAME line, rows and verdict
window.__currentSessionId = 'pl';
LA.onTaskStart('pl', { task_id: 'tp', stream: true });
ev('pl', 'message', { type: 'context_window_snapshot', payload: { contributions: [{}, {}, {}, {}, {}, {}] } });
ev('pl', 'message', { type: 'kg_enrichment', details: { total_entities: 121, enrichments: [{ entity_count: '12' }] } });   // the engine's real shape: per graph
notif('conversation_tool_invoked', { session_id: 'pl', tool_name: 'base_readQuery', arguments: { sql: 'SELECT COUNT(DISTINCT customer_id) FROM airlines_core.accounts_receivable' } });
notif('conversation_tool_completed', { session_id: 'pl', tool_name: 'base_readQuery', success: true, result_rows: 1 });
ev('pl', 'message', { type: 'grounding_gate', details: { decision: 'pass' } });   // the stream announces the grounding gate…
LA.firstToken('pl'); LA.textDone('pl', 'complete');
const finPL = finalBubble('<p>We have 747 customers.</p>'); log.appendChild(finPL);
// …the quiet checks and the turn's clock ride the final_answer frame
LA.settle('pl', finPL, { partial: false, turnId: 4, durationMs: 4180, checks: { grounding: { decision: 'pass' }, guardrail: { ran: true, acted: false, action: 'pass' }, honesty: { ran: true, acted: false, action: 'pass' } } }); LA.onNotification('turn_score_complete', { session_id: 'pl', turn_id: 4 }); LA.ended('pl');
check('the settled clock reads the frame\'s duration — the number a reload will show — not the browser\'s own count', /4\.2s/.test(finPL.querySelector('.la-clock').textContent));
const strip = s => s.replace(/\d+\.\ds/g, '').replace(/\s+/g, ' ').trim();
check('PARITY: live and reloaded render the same rows IN THE SAME ORDER, the same counts and the same verdict word for the same facts', JSON.stringify(texts(finPL)) === JSON.stringify(texts(finRL)) && strip(finPL.querySelector('.la-counts').textContent) === strip(finRL.querySelector('.la-counts').textContent) && trust(finPL).textContent === trust(finRL).textContent && trust(finPL).className === trust(finRL).className);
// the other classes, reloaded
const optTurn = { turn: 2, profile_tag: 'OPGGT', duration_ms: 7300, turn_input_tokens: 14000, turn_output_tokens: 300,
  execution_trace: [
    { action: { tool_name: 'TDA_SystemLog', arguments: { message: 'Calling LLM for Planning' } } },
    { action: { tool_name: 'TDA_SystemLog', arguments: { message: 'Strategic Meta-Plan Generated', details: [{ phase: 1 }, { phase: 2 }, { phase: 3 }] } } },
    { action: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT COUNT(CustomerID) FROM fitness_db.Customers' } }, result: { status: 'success', results: [{ n: '100' }], metadata: { row_count: 1 } } },
    { action: { tool_name: 'TDA_Compute', arguments: { op: 'sum' } }, result: { status: 'success', results: [{}] } },
    { action: { tool_name: 'TDA_FinalReport', arguments: {} }, result: { status: 'success' } },
  ],
  grounding: { decision: 'pass' }, guardrail: { ran: true, acted: false, action: 'pass' }, compute_lineage: { ran: true, figures: 3, receipts: 1, receipted: [{}], unreceipted: [] } };
const finOPT = finalBubble('<p>100 customers.</p>'); log.appendChild(finOPT); LA.restore(finOPT, optTurn, { sessionId: 'rl' });
const optRows = texts(finOPT);
check('Optimize reloaded: the plan, the query (from the execution trace, the final report is not a step), the compute, the three checks', optRows.length === 6 && /Planned the work · 3 phases/.test(optRows[0]) && /Queried fitness_db\.Customers · 1 row/.test(optRows[1]) && /Computed the figures · done/.test(optRows[2]) && /Traced the figures · every figure/.test(optRows[5]) && !/report/i.test(optRows.join(' ')) && /2 tool calls/.test(finOPT.querySelector('.la-line').textContent) && /Verified/.test(trust(finOPT).textContent));
const focusTurn = { turn: 1, profile_type: 'rag_focused', profile_tag: 'UDERIA', duration_ms: 1455, turn_input_tokens: 3503, turn_output_tokens: 97, knowledge_retrieval_event: { retrieved: true, document_count: 10, duration_ms: 53 }, grounding: { decision: 'pass', reason: 'corpus-supported' }, honesty: { ran: true, acted: false, action: 'pass' } };
const finF = finalBubble('<p>doc answer</p>'); log.appendChild(finF); LA.restore(finF, focusTurn, { sessionId: 'rl' });
check('Focus reloaded: the retrieval with its document count, the two checks, "10 documents" on the line', texts(finF).length === 3 && /Searched the knowledge repositories · 10 documents/.test(texts(finF)[0]) && /10 documents/.test(finF.querySelector('.la-line').textContent) && /Verified/.test(trust(finF).textContent));
const genieTurn = { turn: 1, profile_type: 'genie', profile_tag: 'COGGT', duration_ms: 12000, turn_input_tokens: 2000, turn_output_tokens: 200,
  genie_events: [{ type: 'genie_slave_invoked', payload: { profile_tag: 'OPTIM' } }, { type: 'rag_retrieval', payload: { case_id: 'c9', full_case_data: { intent: 'count customers' } } }, { type: 'genie_slave_completed', payload: { profile_tag: 'OPTIM', success: true, duration_ms: 11690 } }, { type: 'genie_synthesis_start', payload: {} }, { type: 'genie_synthesis_complete', payload: {} }],
  grounding: { decision: null, reason: 'no groundable content in this answer' } };
const finG = finalBubble('<p>100 customers.</p>'); log.appendChild(finG); LA.restore(finG, genieTurn, { sessionId: 'rl' });
check('Coordinate reloaded: the expert with its outcome and duration, "1 expert" on the line, and — no check having run — "Not checked"', /Asked @OPTIM · answered/.test(texts(finG)[0]) && /11\.7s/.test(finG.querySelectorAll('.la-chip')[0].querySelector('.la-dur').textContent) && /1 expert/.test(finG.querySelector('.la-line').textContent) && /Not checked/.test(trust(finG).textContent) && trust(finG).classList.contains('la-na'));
const ideateTurn = { turn: 1, profile_type: 'llm_only', profile_tag: 'IDEAT', duration_ms: 900, turn_input_tokens: 1200, turn_output_tokens: 80, grounding: { decision: null } };
const finI = finalBubble('<p>hi</p>'); log.appendChild(finI); LA.restore(finI, ideateTurn, { sessionId: 'rl' });
check('Ideate reloaded (no steps at all): the line reads Done with the clock, an empty receipt, "Not checked" — never a vacuous Verified', /Done/.test(finI.querySelector('.la-line').textContent) && /0\.9s/.test(finI.querySelector('.la-clock').textContent) && texts(finI).length === 0 && /Not checked/.test(trust(finI).textContent));
// ── the champion row: the planner emits `rag_retrieval` ONLY when cases matched, with the FIRST case
//    as its payload — an absent count is not zero; a reloaded turn carries `case_id` (+ `rag_case_count`).
{
  window.__currentSessionId = 'rg';
  LA.onTaskStart('rg', { task_id: 'trg', stream: true });
  ev('rg', 'rag_retrieval', { case_id: 'c-real', full_case_data: { intent: 'q', metadata: { collection_id: 8 } } });   // the pre-count wire shape (prod session 856afac4)
  const rgRows = texts(bubble('rg'));
  check('LIVE: the real payload (first case, no count) reads "1 case" — never "none matched" (the event exists only on a match)', rgRows.length === 1 && /Recalled proven strategies · 1 case$/.test(rgRows[0]) && !/none matched/.test(rgRows[0]));
  window.__currentSessionId = 'rg2';
  LA.onTaskStart('rg2', { task_id: 'trg2', stream: true });
  ev('rg2', 'rag_retrieval', { case_id: 'c1', full_case_data: {}, count: 3 });
  check('LIVE: the count the planner now carries is the fact', /Recalled proven strategies · 3 cases/.test(texts(bubble('rg2'))[0]));
  const finRG = finalBubble('<p>x</p>'); log.appendChild(finRG);
  LA.restore(finRG, { turn: 1, profile_type: 'tool_enabled', profile_tag: 'OPGGT', duration_ms: 4000, case_id: 'c-real', rag_case_count: 3, rag_source_collection_id: 8, grounding: { decision: 'pass' } }, { sessionId: 'rl' });
  check('RELOADED: the persisted case_id + rag_case_count rebuild the same row (live == reloaded)', /Recalled proven strategies · 3 cases/.test(texts(finRG)[0]));
  const finRG1 = finalBubble('<p>x</p>'); log.appendChild(finRG1);
  LA.restore(finRG1, { turn: 1, profile_type: 'tool_enabled', duration_ms: 4000, case_id: 'c-old', grounding: { decision: 'pass' } }, { sessionId: 'rl' });
  check('RELOADED: a turn persisted before the count existed still says "1 case" from its case_id', /Recalled proven strategies · 1 case$/.test(texts(finRG1)[0]));
  const finRG0 = finalBubble('<p>x</p>'); log.appendChild(finRG0);
  LA.restore(finRG0, { turn: 1, profile_type: 'tool_enabled', duration_ms: 4000, case_id: null, grounding: { decision: 'pass' } }, { sessionId: 'rl' });
  check('RELOADED: no champion (no case_id) → no row at all — the absence is not rendered as a check that found nothing', !texts(finRG0).some(r => /Recalled proven/.test(r)));
  check('Coordinate reloaded: the forwarded expert champion event (real shape) reads "1 case"', texts(finG).some(r => /Recalled proven strategies · 1 case$/.test(r)));
}
check('restore() never throws on a malformed turn', LA.restore(finalBubble('<p>x</p>'), { turn: 1, execution_trace: 'nope', conversation_agent_events: [null, {}], grounding: 5 }, {}) === true);

// ── the follow lock: the view follows the forming answer until the reader scrolls up ─────────
{
  // the scroller as a browser reports it: 500px viewport over a growing document
  scroller.clientHeight = 500; scroller.scrollHeight = 900; scroller.scrollTop = 0;
  const scrolled = () => scroller.dispatchEvent({ type: 'scroll' });
  window.__currentSessionId = 'fl';
  LA.onTaskStart('fl', { task_id: 'tfl', stream: true });
  check('BORN arms the follow lock and lands the reader at the bottom', LA.following() === true && scroller.scrollTop === 900);
  scroller.scrollHeight = 1300;   // the working phase grows the bubble past the fold — no scroll event fires for growth
  ev('fl', 'message', { type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 1' } } });
  check('a chip that grows the bubble is FOLLOWED (the reader never scrolls to see the working phase)', scroller.scrollTop === 1300 && LA.following() === true);
  scroller.scrollTop = 100; scrolled();            // the reader's own upward scroll
  check('the reader\'s upward scroll releases the lock', LA.following() === false);
  scroller.scrollHeight = 1700;
  ev('fl', 'message', { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', result_rows: 3, duration_ms: 10 } });
  check('growth never moves a reader who scrolled up (what they read stays put)', scroller.scrollTop === 100 && LA.following() === false);
  scroller.scrollTop = 1700 - 500; scrolled();     // back to the bottom
  check('returning to the bottom re-engages the lock', LA.following() === true);
  scroller.scrollHeight = 2000;
  ev('fl', 'message', { type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 2' } } });
  check('…and the next growth is followed again', scroller.scrollTop === 2000);
  // another session on screen: its growth must not move THIS view
  window.__currentSessionId = 'other';
  scroller.scrollTop = 0; scroller.scrollHeight = 2400;
  ev('fl', 'message', { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', result_rows: 1, duration_ms: 5 } });
  check('a turn that is not on screen never scrolls the view the reader is looking at', scroller.scrollTop === 0);
  window.__currentSessionId = 's1';
  LA.ended('fl');
}

// ── wiring, on the shipped source ────────────────────────────────────────────────────────────
const src = read('static/js/handlers/livingAnswer.js'), ui = read('static/js/ui.js'), eh = read('static/js/eventHandlers.js'), nt = read('static/js/notifications.js'), sm = read('static/js/handlers/sessionManagement.js'), idx = read('templates/index.html'), rt = read('src/trusted_data_agent/api/routes.py'), css = read('static/css/main.css');
check('the module never renders the word "clean" (the verdict vocabulary is Verified / N findings / Not checked)', !/['"`]clean['"`]|·\s*clean|clean['"`]/.test(src.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, '')));
check('the module has ONE verdict rule and every render reads it', (src.match(/function verdictOf\(/g) || []).length === 1 && /paintTrust[\s\S]*?const v = verdictOf\(st\)/.test(src) && /function seal\([\s\S]*?verdictOf\(st\)/.test(src));
{
  const b = ui.slice(ui.indexOf('export function beginProvisionalAnswer('), ui.indexOf('export function beginProvisionalAnswer(') + 2200);
  check('ui.js begin(): the streamer consults the Living Answer\'s follow lock (a bottom the chips pushed past the fold is still followed) and lands the reader at the bottom before the first token', /_prov\.follow = _provAtBottom\(_prov\.scroller\) \|\| !!\(window\.LivingAnswer\.following && window\.LivingAnswer\.following\(\)\)/.test(b) && /if \(_prov\.follow\) _prov\.scroller\.scrollTop = _prov\.scroller\.scrollHeight;/.test(b));
  check('ui.js begin(): streams INTO the Living Answer bubble when one exists (contentHost), else the plain bubble', b.indexOf('window.LivingAnswer.contentHost(sessionId)') > 0 && b.indexOf('window.LivingAnswer.contentHost') < b.indexOf("const wrapper = document.createElement('div')") && /if \(host && host\.wrapper && host\.content\)[\s\S]*?return;/.test(b));
  const r = ui.slice(ui.indexOf('function _provRender()'), ui.indexOf('function _provRender()') + 1600);
  check('ui.js render: the living renderer (stable blocks + rise-in) is used only for a living bubble, the legacy path otherwise', /_prov\.el\.dataset\.living && window\.LivingAnswer && window\.LivingAnswer\.renderText/.test(r) && /if \(!rendered\) \{/.test(r) && /marked\.parse\(_prov\.text\)/.test(r));
  const d = ui.slice(ui.indexOf('export function provisionalAnswerDelta('), ui.indexOf('export function provisionalAnswerDelta(') + 1400);
  check('ui.js delta: the FIRST token folds the receipt (once — guarded on the empty-before state)', /const _wasEmpty = !_prov\.text;/.test(d) && /if \(_wasEmpty && _prov\.text && window\.LivingAnswer && window\.LivingAnswer\.firstToken\)/.test(d));
  const f = ui.slice(ui.indexOf('export function finishProvisionalAnswer('), ui.indexOf('export function finishProvisionalAnswer(') + 1400);
  check('ui.js finish: a living bubble is kept for the settle via textDone(outcome); dropped with no outcome; the caret is removed either way', /window\.LivingAnswer\.textDone\(_prov\.el\.dataset\.living, outcome\)/.test(f) && /if \(!_keep && _prov\.el && _prov\.el\.parentNode\)/.test(f) && /\.answer-stream-caret'\); if \(_c\) _c\.remove\(\)/.test(f));
}
{
  const ps = eh.slice(eh.indexOf('async function processStream('), eh.indexOf('async function processStream(') + 120000);
  check('eventHandlers: task_start arms the Living Answer', /window\.LivingAnswer\.onTaskStart\(originSessionId, eventData\)/.test(ps));
  const iOn = ps.indexOf('window.LivingAnswer.onEvent(originSessionId, eventName, eventData)'), iCur = ps.indexOf('window.SteerReattach.cursor(originSessionId, cursorSeq)'), iLogic = ps.indexOf('// --- Event Handling Logic ---');
  check('every frame reaches the Living Answer AFTER the cursor bookkeeping and BEFORE its own dispatch (one clock)', iOn > iCur && iOn < iLogic);
  const iFa = ps.indexOf("} else if (eventName === 'final_answer')"), fa = ps.slice(iFa, iFa + 6000);
  check('final_answer: finish(outcome) → addMessage → settle(the appended message, partial, turn)', fa.indexOf('UI.finishProvisionalAnswer(') < fa.indexOf("UI.addMessage('assistant', eventData.final_answer") && fa.indexOf("UI.addMessage('assistant', eventData.final_answer") < fa.indexOf('window.LivingAnswer.settle(originSessionId, _finalBubble, { partial: !!eventData.partial, turnId: eventData.turn_id, checks: eventData.checks || null, durationMs: eventData.duration_ms') && fa.indexOf('const _finalBubble = DOM.chatLog.lastElementChild;') > fa.indexOf("UI.addMessage('assistant', eventData.final_answer"));
  const tail = eh.slice(eh.indexOf('state.activeStreamSessions.delete(originSessionId);') - 3000, eh.indexOf('state.activeStreamSessions.delete(originSessionId);'));
  check('the stream\'s end tells the Living Answer (seal a settled turn, drop an unfinished one) before the dial settles', /window\.LivingAnswer\.ended\(originSessionId\)/.test(tail) && tail.indexOf('window.LivingAnswer.ended') < tail.indexOf('window.AssuranceDial.complete'));
  const stop = eh.slice(eh.indexOf('async function handleStopExecutionClick() {'), eh.indexOf('async function handleStopExecutionClick() {') + 700);
  check('Stop stalls the hairline the moment it is pressed', /window\.LivingAnswer\.stopping\(state\.currentSessionId\)/.test(stop));
}
check('notifications.js: the score, a recovery and a considered steer reach the Living Answer over the notification channel', /LivingAnswer\.onNotification\('turn_score_complete'/.test(nt) && /LivingAnswer\.onNotification\('llm_recovery'/.test(nt) && /LivingAnswer\.onNotification\('steer_consumed'/.test(nt));
{
  const i = sm.indexOf("UI.addMessage(msg.role, msg.content, turnIdToUse, isValid, msg.source, null, isSessionPrimer);"), seam = sm.slice(i, i + 1400);
  check('sessionManagement: right after an assistant message renders from history, restore() rebuilds the chrome from the turn matched by NUMBER (never by position), gated on the server\'s `living_answer`, primer turns skipped', i > 0 && /data\.living_answer && window\.LivingAnswer && window\.LivingAnswer\.restore/.test(seam) && /wfAll\.find\(t => t && Number\(t\.turn\) === Number\(turnIdToUse\)\)/.test(seam) && /if \(wfTurn && !isSessionPrimer\) window\.LivingAnswer\.restore\(bubble, wfTurn, \{ sessionId \}\)/.test(seam) && /window\.__livingAnswerEnabled = true/.test(seam));
}
check('index.html loads the plain script after steerReattach.js, pinned past the reshape', idx.indexOf("js/handlers/livingAnswer.js') }}?v=") > idx.indexOf("js/handlers/steerReattach.js') }}?v=") && !/livingAnswer\.js'\) \}\}\?v=1\.0"/.test(idx));
check('routes.py: task_start carries `stream` ONLY under the S1 master (byte-identical off), and the session history says whether the deployment streams', /if getattr\(APP_CONFIG, "ANSWER_STREAMING", False\):\n\s+_start\["stream"\] = True/.test(rt) && rt.indexOf('_start["stream"] = True') < rt.indexOf('_start_frame = PlanExecutor._format_sse(_start, "task_start")') && /"living_answer": bool\(getattr\(APP_CONFIG, "ANSWER_STREAMING", False\)\)/.test(rt));
{
  const i = css.indexOf('S1.1 · The Living Answer'), block = css.slice(i, css.indexOf('S8 · Turn lineage'));
  check('main.css: the block declares the three motion tokens once and colours by the trust tokens (shape carries state on the marks and the verdict: solid / half / outlined / dashed)', /--la-d1: 120ms; --la-d2: 240ms; --la-d3: 360ms/.test(block) && /\.la-chip\.la-gate\.la-pass \.la-mark \{ background: var\(--trust-pass/.test(block) && /\.la-chip\.la-gate\.la-warn \.la-mark \{ background: linear-gradient/.test(block) && /\.la-chip\.la-gate\.la-danger \.la-mark \{ background: transparent; border-color: var\(--trust-danger/.test(block) && /\.la-trust\.la-na \{ border-style: dashed; \}/.test(block));
  check('the rail, the seal glyph, the status line and the second summary are GONE from the stylesheet', !/\.la-rail|\.la-seg|\.la-seal|\.la-runner|\.la-status|\.la-sum\b|\.la-vword/.test(block) && !/\.la-box \{ position: relative; padding-left/.test(block));
  check('no bare state hex in the block (every colour is a token with its fallback)', !/#(?:10b981|f59e0b|ef4444|34d399|fbbf24|f87171)\b(?![^;]*\))/.test(block.replace(/var\([^)]*\)/g, '')));
  check('the folded toggle keeps its listener but no pixels (clipped, not display:none — a display:none control cannot be clicked by the chip)', /\.msg-actions-slot \.ac-toggle\.la-folded-toggle \{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect\(0 0 0 0\)/.test(block) && !/la-folded-toggle[^}]*display: none/.test(block));
check('the action row: every act on one baseline — no per-control margin survives inside the slot, Regenerate is aligned as an inline-flex item, the three controls share one height', /\.msg-actions-slot > \* \{ margin: 0 !important; \}/.test(block) && /\.msg-actions-slot \.tl-menu \{ display: inline-flex; align-items: center; \}/.test(block) && /\.msg-actions-slot \.ac-toggle, \.msg-actions-slot \.tl-act \{ height: 22px; display: inline-flex; align-items: center; line-height: 1;/.test(block) && /\.la-trust \{ display: inline-flex; align-items: center; gap: 5px; height: 22px;/.test(block));
  check('reduced motion keeps every fact and removes the travel (pulse, breathe, pop, reveal, marks, sweep)', /prefers-reduced-motion: reduce\)[\s\S]*?\.la-line \.la-dot, \.la-chip\.la-run \.la-mark \{ animation: none; \}/.test(block) && /\.la-trust\.la-pop, \.la-new, \.la-row, \.la-block, mark\.claim-unsupported\.la-draw[\s\S]*?animation: none/.test(block));
}

// ── a REFUSED component call (3 tool calls, 2 rows): the receipt says why, never "done" ────────
window.__currentSessionId = 'c9';
LA.onTaskStart('c9', { task_id: 'tc9', stream: true });
notif('conversation_tool_invoked', { session_id: 'c9', tool_name: 'TDA_Compute', arguments: { op: 'avg' } });
notif('conversation_tool_completed', { session_id: 'c9', tool_name: 'TDA_Compute', success: false, refused: 'more than one numeric column — name one: CustomerID, TotalRevenue', duration_ms: 12 });
notif('conversation_tool_invoked', { session_id: 'c9', tool_name: 'TDA_Compute', arguments: { op: 'avg', column: 'TotalRevenue' } });
notif('conversation_tool_completed', { session_id: 'c9', tool_name: 'TDA_Compute', success: true, duration_ms: 9 });
{ const _rows = [...bubble('c9').querySelectorAll('.la-chip')].filter(c => /Computed the figures/.test(c.textContent));
  check('a REFUSED component call is never "done": its row reads "refused — <the component\'s reason>" with the failed mark; the corrected call is its own row, done', _rows.length === 2
        && _rows[0].querySelector('.la-f').textContent === 'refused — more than one numeric column' && _rows[0].classList.contains('la-err')
        && _rows[0].getAttribute('data-tip') === 'more than one numeric column — name one: CustomerID, TotalRevenue' && _rows[0].classList.contains('u-tip')
        && /done/.test(_rows[1].querySelector('.la-f').textContent) && !_rows[1].classList.contains('la-err')); }

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
