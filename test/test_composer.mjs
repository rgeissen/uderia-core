// The composer (Oct 2026) — the question box before · during · after a turn.
//   (1) composerChrome.js EXECUTED under the DOM stub: the running state (hairline + ring
//       classes, the steering tag, the shortcut line), the receipt row (the answer's own verdict
//       read from its trust chip, the turn's duration, tokens and cost from the one source each,
//       hidden at the next keystroke / a new turn / a switch / dismiss), the trust-record route;
//   (2) updateHintAndIndicatorState EXECUTED: the context mode is a chip (words, mode, tooltip);
//   (3) the wiring: every element id the existing code reads survives the restructure, and each
//       state is fed from the one place it is decided.
import './_uderia_bus.mjs';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// ── (1) the module, executed ─────────────────────────────────────────────────────────
const { doc, body } = installDom();
globalThis.setTimeout = (fn) => { fn(); return 0; };   // the receipt paints after the settle's tick
const host = new El('div');
host.innerHTML = `
<div id="chat-log"><div class="msg"><div class="msg-actions-slot"><button type="button" class="la-trust la-pass"><span class="la-tw">Verified</span></button></div></div></div>
<form id="chat-form" class="composer"><div id="composer-receipt" class="composer-receipt" hidden></div><textarea id="user-input"></textarea></form>
<div id="composer-hint"></div>
<span id="turn-input-tokens">18,200</span><span id="turn-output-tokens">3,200</span>`;
body.appendChild(host);
window.sessionCostAccumulator = { turn: 0.0031 };
await import(pathToFileURL('static/js/handlers/composerChrome.js').href);
const C = window.Composer;
const $ = (id) => doc.getElementById(id);
const form = $('chat-form'), input = $('user-input'), receipt = $('composer-receipt'), hint = $('composer-hint');
const bubble = doc.querySelector('.msg'), chip = doc.querySelector('.la-trust');
let chipClicks = 0; chip.addEventListener('click', () => { chipClicks++; });

check('the module publishes its entry points', C && ['setRunning', 'showReceipt', 'hideReceipt', 'refresh', 'clear'].every(k => typeof C[k] === 'function'));
check('before: the shortcut line names Enter, Shift+Enter and Alt', /send/.test(hint.innerHTML) && /new line/.test(hint.innerHTML) && /Alt/.test(hint.innerHTML));
check('before: an empty box dims Send (is-empty)', form.classList.contains('is-empty'));

C.setRunning(true, true);
check('during: the card runs (hairline + ring are keyed on is-running)', form.classList.contains('is-running'));
check('during, steering: the steering tag is on and the line says what Enter does now', form.classList.contains('is-steering') && /sends a steer/.test(hint.innerHTML));
check('during: the card is aria-busy', form.getAttribute('aria-busy') === 'true');
C.setRunning(true, false);
check('during, not steering: no steering tag, the line says Stop keeps what arrived', !form.classList.contains('is-steering') && /Stop keeps what has arrived/.test(hint.innerHTML) && !/steer/.test(hint.innerHTML));
C.setRunning(false);
input.value = 'typed'; input.dispatchEvent({ type: 'input' }); input.value = ''; C.setRunning(false);
check('a box cleared from code (no input event) reads empty again once the state changes', form.classList.contains('is-empty'));
check('after the turn: running state cleared, the idle line is back', !form.classList.contains('is-running') && !form.classList.contains('is-steering') && form.getAttribute('aria-busy') == null && /new line/.test(hint.innerHTML));

C.showReceipt('s1', { turnId: 3, durationMs: 6240, partial: false, bubble });
check('after: the receipt row shows', !receipt.hidden);
check('the verdict is the answer chip\'s own word and state', /Verified/.test(receipt.innerHTML) && /cr-verdict cr-pass/.test(receipt.innerHTML));
check('the duration is the turn\'s own figure', /answered in <b>6\.2s<\/b>/.test(receipt.innerHTML));
check('the tokens are the Live Status turn counters (18.2k + 3.2k)', /<b>21\.4k<\/b> tokens/.test(receipt.innerHTML));
check('the cost is the live accumulator\'s turn figure', /\$0\.0031/.test(receipt.innerHTML));
check('the row routes to the trust record and can be dismissed', /class="cr-open"/.test(receipt.innerHTML) && /class="cr-dismiss"/.test(receipt.innerHTML) && /aria-label="Dismiss/.test(receipt.innerHTML));

receipt.querySelector('.cr-open').click();
check('"Trust record" opens the answer\'s trust record through its own chip', chipClicks === 1);

chip.className = 'la-trust la-warn'; chip.querySelector('.la-tw').textContent = '1 finding';
doc.dispatchEvent(new CustomEvent('uderia:answer-verdict', { detail: { sid: 's1', turnId: 3 } }));
check('a later verdict (the seal, a score) repaints the row', /1 finding/.test(receipt.innerHTML) && /cr-warn/.test(receipt.innerHTML));
chip.className = 'la-trust la-pass'; chip.querySelector('.la-tw').textContent = 'Verified';

window.sessionCostAccumulator.turn = 0.0052; C.refresh();
check('refresh picks up a late post-turn cost', /\$0\.0052/.test(receipt.innerHTML));

input.value = 'next question'; input.dispatchEvent({ type: 'input' });
check('the next keystroke hides the receipt', receipt.hidden);
check('typing lifts the empty state', !form.classList.contains('is-empty'));
input.value = ''; input.dispatchEvent({ type: 'input' });

C.showReceipt('s1', { durationMs: 4100, partial: true, bubble });
check('a stopped turn reads "stopped after", never "answered in"', /stopped after <b>4\.1s<\/b>/.test(receipt.innerHTML) && !/answered in/.test(receipt.innerHTML));
C.setRunning(true, false);
check('a new turn hides the receipt', receipt.hidden);
C.setRunning(false);

C.showReceipt('s1', { durationMs: 1000, bubble });
C.clear();
check('a session switch clears the receipt', receipt.hidden);

C.showReceipt('s1', { durationMs: 1000, bubble });
receipt.querySelector('.cr-dismiss').click();
check('dismiss hides the row and returns focus to the box', receipt.hidden && doc.activeElement === input);

chip.querySelector('.la-tw').textContent = '<img src=x>';
C.showReceipt('s1', { durationMs: 1000, bubble });
check('the verdict word is escaped', !/<img/.test(receipt.innerHTML) && /&lt;img/.test(receipt.innerHTML));
chip.querySelector('.la-tw').textContent = 'Verified';

// nothing to say → no row (a turn with no verdict chip, no duration, no counters)
const bare = new El('div');
doc.querySelector('.msg-actions-slot').innerHTML = '';   // no Living Answer verdict on this turn
window.sessionCostAccumulator.turn = 0; $('turn-input-tokens').textContent = '0'; $('turn-output-tokens').textContent = '0';
C.showReceipt('s1', { bubble: bare });
check('an empty receipt is never drawn', receipt.hidden);

// ── (2) the context chip, executed ───────────────────────────────────────────────────
const ui = read('static/js/ui.js');
const fnSrc = ui.slice(ui.indexOf('export function updateHintAndIndicatorState() {'), ui.indexOf('export function updateVoiceModeUI()')).replace('export function', 'function');
const ctxChip = new El('button', { id: 'input-hint', class: 'composer-ctx' });
ctxChip.innerHTML = '<svg></svg><span class="composer-ctx-label">x</span>';
const fakeDom = { inputHint: ctxChip, contextStatusDot: new El('span'), sendIcon: new El('svg') };
const run = (state) => new Function('DOM', 'state', fnSrc + '\nupdateHintAndIndicatorState();')(fakeDom, state);
run({ isLastTurnModeLocked: false, isTempLastTurnMode: false });
check('full context: the chip reads "Full context", mode full, not pressed', ctxChip.querySelector('.composer-ctx-label').textContent === 'Full context' && ctxChip.dataset.mode === 'full' && ctxChip.getAttribute('aria-pressed') === 'false');
check('the chip\'s tooltip says how to change it (Alt, Shift + Alt, click)', /Alt/.test(ctxChip.getAttribute('data-tip')) && /click/i.test(ctxChip.getAttribute('data-tip')));
run({ isLastTurnModeLocked: false, isTempLastTurnMode: true });
check('Alt held: "Turn summaries", mode temp', ctxChip.querySelector('.composer-ctx-label').textContent === 'Turn summaries' && ctxChip.dataset.mode === 'temp');
run({ isLastTurnModeLocked: true, isTempLastTurnMode: false });
check('locked: "Turn summaries · locked", mode locked, pressed', /locked/.test(ctxChip.querySelector('.composer-ctx-label').textContent) && ctxChip.dataset.mode === 'locked' && ctxChip.getAttribute('aria-pressed') === 'true');
check('the status dot keeps its class contract', fakeDom.contextStatusDot.className === 'connection-dot context-last-turn-locked');

// ── (3) the wiring ───────────────────────────────────────────────────────────────────
const idx = read('templates/index.html');
const ids = ['chat-form', 'user-input', 'input-area', 'submit-button', 'send-icon', 'stop-icon', 'loading-spinner', 'file-attach-button', 'file-attach-input',
    'attach-spinner', 'attach-icon', 'voice-input-button', 'mic-icon', 'key-observations-toggle-button', 'observations-autoplay-off-icon', 'observations-autoplay-on-icon',
    'observations-off-icon', 'active-profile-tag', 'active-skill-tags', 'active-extension-tags', 'input-hint', 'drop-zone-overlay', 'composer-receipt', 'composer-hint'];
for (const id of ids) check(`id="${id}" exists exactly once`, (idx.match(new RegExp(`id="${id}"`, 'g')) || []).length === 1);
const formBlock = idx.slice(idx.indexOf('<form id="chat-form"'), idx.indexOf('id="composer-hint"'));
check('the composer carries no growing-on-hover buttons, no grey Tailwind fills, no hard-white text', !/hover:scale/.test(formBlock) && !/bg-gray-\d/.test(formBlock) && !/text-white/.test(formBlock));
check('the textarea has a label and is described by the shortcut line', /<label for="user-input"/.test(formBlock) && /aria-describedby="composer-hint"/.test(formBlock));
check('the icon-only buttons are named', ['file-attach-button', 'voice-input-button', 'key-observations-toggle-button', 'submit-button'].every(id => new RegExp(`id="${id}"[^>]*aria-label="[^"]+"`).test(formBlock)));
check('composer.css and composerChrome.js are linked and pinned', /css\/composer\.css'\) \}\}\?v=/.test(idx) && /handlers\/composerChrome\.js'\) \}\}\?v=/.test(idx));

check('setExecutionState drives the composer\'s running state', /window\.Composer\.setRunning\(isActive, _steerLive\)/.test(ui));
check('voice mode wears is-active, not Tailwind fills', /voiceInputButton\.classList\.toggle\('is-active'/.test(ui) && !/voiceInputButton\.classList\.(add|remove)\('bg-/.test(ui));
check('the token display keeps the receipt in step (live only)', /if \(!isHistorical\) \{ try \{ if \(window\.Composer && window\.Composer\.refresh\)/.test(ui));
const ev = read('static/js/eventHandlers.js');
const fa = ev.slice(ev.indexOf("} else if (eventName === 'final_answer')"));
check('the receipt is drawn at final_answer, after the Living Answer settled', fa.indexOf('window.Composer.showReceipt(originSessionId') > fa.indexOf('window.LivingAnswer.settle(originSessionId') && fa.indexOf('window.Composer.showReceipt(originSessionId') > 0);
check('a click on the context chip locks / unlocks turn summaries', /DOM\.inputHint\.addEventListener\('click'[\s\S]{0,200}state\.isLastTurnModeLocked = !state\.isLastTurnModeLocked/.test(ev));
const sm = read('static/js/handlers/sessionManagement.js');
check('a session switch clears the receipt', /if \(state\.currentSessionId !== sessionId\) \{ try \{ if \(window\.Composer\) window\.Composer\.clear\(\)/.test(sm));
const la = read('static/js/handlers/livingAnswer.js');
check('the Living Answer announces a verdict change', /UderiaBus\.emit\('uderia:answer-verdict'/.test(la));

const css = read('static/css/composer.css');
const bareHex = css.replace(/\/\*[\s\S]*?\*\//g, '').replace(/var\([^;]*?\)/g, '').match(/#[0-9a-fA-F]{3,6}\b/g) || [];
check('colours come from theme tokens (only #fff on accent, #000 mix, #dc2626 stop-hover outside a var fallback)', bareHex.every(h => ['#fff', '#000', '#dc2626'].includes(h.toLowerCase())), bareHex.join(','));
check('reduced motion stops the ring and the hairline', /prefers-reduced-motion[\s\S]*composer-ring-arc \{ animation: none/.test(css) && /prefers-reduced-motion[\s\S]*composer-hair i \{ animation: none/.test(css));
check('light theme has its own shadows', /\[data-theme="light"\] #chat-form\.composer/.test(css));
check('the hairline and the ring move only while running', /\.composer-hair i \{[\s\S]*?animation-play-state: paused/.test(css) && /\.is-running \.composer-hair i \{ animation-play-state: running/.test(css) && /\.is-running \.composer-ring-arc \{ animation-play-state: running/.test(css));

console.log(fail ? `FAILED — ${pass + fail} assertions (${pass} passed, ${fail} failed)` : `ALL PASS — ${pass} assertions (${pass} passed, 0 failed)`);
process.exit(fail ? 1 : 0);
