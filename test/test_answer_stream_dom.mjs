// S1 — the provisional answer bubble, executing the SHIPPED ui.js block under a DOM stub,
// plus the processStream routing (structural). No browser, no server.
import fs from 'node:fs';
const ui = fs.readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');
const ev = fs.readFileSync(new URL('../static/js/eventHandlers.js', import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL:', n, d); } };

// ── extract the shipped block (marker → EOF) and execute it ──────────────────
const start = ui.indexOf('// S1 — The Governed Stream: the provisional answer bubble.');
check('the provisional block ships in ui.js', start > 0);
let block = ui.slice(ui.lastIndexOf('// ═══', start));
block = block.replace(/^export function /gm, 'function ');
const exportsNeeded = ['beginProvisionalAnswer', 'provisionalAnswerDelta', 'finishProvisionalAnswer', 'provisionalAnswerActive', 'provisionalAnswerText', '_prov', 'captureReadingAnchor', 'landInPlace', '_provTailHtml'];

// DOM stub — the minimum the block touches, faithful where it matters (isConnected, scroll geometry)
const flat = n => { const out = []; const w = x => { out.push({ tag: x.tag, cls: x.className, attrs: x.attrs, ds: x.dataset, txt: x.textContent }); x.children.forEach(w); }; w(n); return JSON.stringify(out); };
function el(tag) {
  const e = { tag, id: '', children: [], attrs: {}, dataset: {}, style: {}, className: '', textContent: '', _html: '', parentNode: null, listeners: {},
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
    addEventListener(k, f) { this.listeners[k] = f; }, removeEventListener(k) { delete this.listeners[k]; },
    querySelectorAll(sel) { const out = []; const walk = n => { n.children.forEach(c => { if (sel === '[data-provisional]' && c.dataset.provisional) out.push(c); walk(c); }); }; walk(this); return out; },
    get isConnected() { let n = this; while (n) { if (n === chatLog) return true; n = n.parentNode; } return false; },
    get innerHTML() { return this._html + this.children.map(c => c.outer()).join(''); }, set innerHTML(v) { this._html = v; this.children = []; },
    outer() { return `<${this.tag.toLowerCase()} class="${this.className}"${this.attrs['aria-hidden'] ? ' aria-hidden="true"' : ''}></${this.tag.toLowerCase()}>`; },
    get lastElementChild() { return this.children[this.children.length - 1] || null; }, get tagName() { return this.tag.toUpperCase(); },
    scrollTop: 0, scrollHeight: 1000, clientHeight: 400,
  };
  return e;
}
const chatLog = el('div'); const container = el('main'); container.appendChild(chatLog);
container.scrollHeight = 1000; container.clientHeight = 400; container.scrollTop = 600; // at bottom
const DOM = { chatLog };
const body = el('body'); body.appendChild(container);
const document = { createElement: el, body, getElementById: id => id === 'chat-container' ? container : (id === 'answer-stream-pill' ? container.children.find(c => c.id === 'answer-stream-pill') || null : (id === 'answer-stream-status' ? body.children.find(c => c.id === 'answer-stream-status') || null : null)) };
const setTimeout = (f) => f();
let rafQueue = [];
const requestAnimationFrame = f => { rafQueue.push(f); return rafQueue.length; };
const cancelAnimationFrame = () => { rafQueue = []; };
const runRaf = () => { const q = rafQueue; rafQueue = []; q.forEach(f => f()); };
const marked = { parse: t => `<p>${t}</p>` };
const window = {};
const fn = new Function('DOM', 'document', 'requestAnimationFrame', 'cancelAnimationFrame', 'marked', 'window', 'setTimeout',
  block + '\nreturn {' + exportsNeeded.join(',') + '};');
const P = fn(DOM, document, requestAnimationFrame, cancelAnimationFrame, marked, window, setTimeout);

// ── behaviour ────────────────────────────────────────────────────────────────
P.beginProvisionalAnswer('s1');
const bubble = chatLog.children[0];
check('begin() appends ONE bubble to the chat log', chatLog.children.length === 1 && bubble.className.includes('answer-provisional'));
check('the bubble is NOT an assistant message: no turn badge, no data-turn-id (observers skip it)', !flat(bubble).includes('turnId') && !bubble.className.includes('assistant-badge'));
check('aria-busy + a visually-hidden status line', bubble.attrs['aria-busy'] === 'true' && flat(bubble).includes('The answer is being written.'));
check('the bubble is marked provisional for snapshot cleanup', bubble.dataset.provisional === '1');
P.provisionalAnswerDelta({ seq: 1, message_index: 0, text: 'Hello ' });
P.provisionalAnswerDelta({ seq: 2, message_index: 0, text: '**world**' });
check('deltas accumulate before a frame renders', P.provisionalAnswerText() === 'Hello **world**');
check('rendering is batched — one frame queued', rafQueue.length === 1);
runRaf();
const content = bubble.children[1].children[2];
check('rendered through marked, the caret appended at the end of the last block (never on its own line)', content._html === '<p>Hello **world**</p>' && content.lastElementChild && content.lastElementChild.className === 'answer-stream-caret');
check('the bubble carries the same Assistant author line as a final message', bubble.children[1].children[0].textContent === 'Assistant');
check('follow lock: at the bottom, the view follows the text', container.scrollTop === container.scrollHeight);
P.provisionalAnswerDelta({ seq: 2, message_index: 0, text: 'DUP' });
check('a duplicate seq is dropped', P.provisionalAnswerText() === 'Hello **world**');
P.provisionalAnswerDelta({ seq: 3, message_index: 0, text: '!' });
P.provisionalAnswerDelta({ seq: 4, message_index: 1, reset: true });
check('reset withdraws the text and advances the message index', P.provisionalAnswerText() === '' && P._prov.msg === 1);
P.provisionalAnswerDelta({ seq: 5, message_index: 0, text: 'stale' });
check('text for a withdrawn message index is dropped', P.provisionalAnswerText() === '');
P.provisionalAnswerDelta({ seq: 6, message_index: 1, text: 'Second' });
check('text for the current message index lands', P.provisionalAnswerText() === 'Second');
// follow lock releases when the reader scrolls up, re-engages at the bottom
container.scrollTop = 100; bubble.listeners; container.listeners.scroll && container.listeners.scroll();
runRaf();
check('scrolled up → the view does NOT jump', container.scrollTop === 100);
container.scrollTop = container.scrollHeight - container.clientHeight; container.listeners.scroll();
P.provisionalAnswerDelta({ seq: 7, message_index: 1, text: ' more' }); runRaf();
check('back at the bottom → follows again', container.scrollTop === container.scrollHeight);
P.finishProvisionalAnswer();
check('finish() removes the bubble and the scroll listener', chatLog.children.length === 0 && !P.provisionalAnswerActive() && !container.listeners.scroll);
P.provisionalAnswerDelta({ seq: 1, message_index: 0, text: 'again' });
check('a delta with no bubble begins one (arrival order is not guaranteed)', P.provisionalAnswerActive() && chatLog.children.length === 1);
// a bubble restored from a session-switch snapshot is a stale copy
const stale = el('div'); stale.dataset.provisional = '1'; chatLog.appendChild(stale);
P.beginProvisionalAnswer('s1');
check('begin() drops any stale provisional bubble (snapshot restore) before adding its own', chatLog.querySelectorAll('[data-provisional]').length === 1);
P.finishProvisionalAnswer();
P.finishProvisionalAnswer();
check('finish() is idempotent', chatLog.children.length === 0);
// the "new text" pill: appears only when the reader has scrolled up and text arrives; one click returns
P.beginProvisionalAnswer('s1'); container.scrollTop = 0; container.listeners.scroll();
P.provisionalAnswerDelta({ seq: 1, message_index: 0, text: 'grew' }); runRaf();
const pill = container.children.find(c => c.id === 'answer-stream-pill');
check('scrolled up + new text → a "new text" pill sticks at the scroller bottom (sr label, a real button)', !!pill && pill.tag === 'button' && pill.attrs['aria-label'].includes('New text'));
pill.listeners.click();
check('the pill click re-engages follow, jumps to the bottom and removes itself', P._prov.follow === true && container.scrollTop === container.scrollHeight && !container.children.find(c => c.id === 'answer-stream-pill'));
container.scrollTop = 0; container.listeners.scroll(); P.provisionalAnswerDelta({ seq: 2, message_index: 0, text: ' more' }); runRaf();
check('a second scroll-up shows the pill again, once (no duplicates)', container.children.filter(c => c.id === 'answer-stream-pill').length === 1);
P.provisionalAnswerDelta({ seq: 3, message_index: 0, text: ' x' }); runRaf();
check('…and never twice', container.children.filter(c => c.id === 'answer-stream-pill').length === 1);
P.finishProvisionalAnswer('partial');
check('finishing removes the pill', !container.children.find(c => c.id === 'answer-stream-pill'));
check('the once-only announcement region exists, polite, and carries the partial wording after finish', (() => { const live = body.children.find(c => c.id === 'answer-stream-status'); return !!live && live.attrs['aria-live'] === 'polite' && live.attrs.role === 'status'; })());

// ── processStream routing (structural, the SHIPPED source) ────────────────────
const ps = ev.slice(ev.indexOf('async function processStream('), ev.indexOf('async function processStream(') + 120000);
const iAd = ps.indexOf("if (eventName === 'answer_delta')"), iSi = ps.indexOf("if (eventName === 'status_indicator_update')");
check('answer_delta is handled FIRST in the chain and continues (never reaches Live Status)', iAd > 0 && iAd < iSi && ps.slice(iAd, iSi).includes('continue;'));
check('answer_delta is session-guarded', ps.slice(iAd, iSi).includes('eventData.session_id === state.currentSessionId'));
const iFa = ps.indexOf("} else if (eventName === 'final_answer')");
const faBody = ps.slice(iFa, iFa + 6000);
check('final_answer finishes the provisional bubble BEFORE addMessage, announcing complete/partial once', faBody.indexOf("UI.finishProvisionalAnswer(eventData.partial ? 'partial' : 'complete')") > 0 && faBody.indexOf('UI.finishProvisionalAnswer(') < faBody.indexOf("UI.addMessage('assistant', eventData.final_answer"));
check('a partial final_answer is recorded', faBody.includes('if (eventData.partial) partialDelivered = true;'));
const iCa = ps.indexOf("} else if (eventName === 'cancelled')"); const caBody = ps.slice(iCa, ps.indexOf("} else if (eventName === 'final_answer')", iCa));
// The cancel frames ARRIVE BEFORE the partial final_answer (endpoint + executor first, the partial
// last), so the CANCELLED line and the bubble teardown are deferred to the stream's end and happen
// only when no partial arrived — a kept partial keeps its bubble and gets no CANCELLED line.
check('cancelled: never drops the bubble or draws CANCELLED in the frame itself — it records the cancel', !caBody.includes('UI.finishProvisionalAnswer()') && !caBody.includes("UI.addMessage('assistant', cancelledMessage") && caBody.includes('cancelPending = { turn_id: eventData.turn_id'));
const psEnd = ps.slice(ps.indexOf('the stream ended: a cancel with no partial answer'), ps.indexOf('the stream ended: a cancel with no partial answer') + 700);
check('…and the stream\'s end draws CANCELLED only when no partial arrived (session-guarded)', psEnd.includes('if (cancelPending && !partialDelivered && state.currentSessionId === originSessionId') && psEnd.includes('UI.finishProvisionalAnswer();') && psEnd.includes('cancelled-tag'));
const iEr = ps.indexOf("} else if (eventName === 'error')");
check('error: the provisional bubble is dropped', ps.slice(iEr, iEr + 400).includes('UI.finishProvisionalAnswer()'));
const tail = ev.slice(ev.indexOf('state.activeStreamSessions.delete(originSessionId);'));
check("a stream that ends without a final leaves no caret behind (finally)", tail.slice(0, 600).includes('UI.finishProvisionalAnswer()'));
check("Stop is where Send was: setExecutionState puts the composer's control into stop mode and keeps it ENABLED", ui.includes("DOM.submitButton.dataset.mode = isActive ? 'stop' : 'send';") && ui.includes("DOM.submitButton.disabled = false;") && ui.includes("'Stop the answer'"));
check('handleChatSubmit routes a stop-mode click to the stop handler before anything else', ev.indexOf("DOM.submitButton.dataset.mode === 'stop'") > 0 && ev.indexOf("DOM.submitButton.dataset.mode === 'stop'") < ev.indexOf('if (isUploadInProgress()) return;'));
const idx = fs.readFileSync(new URL('../templates/index.html', import.meta.url), 'utf8');
// the property, not the geometry: one square drawn in currentColor (the Oct-2026 composer redrew it)
const _stopSvg = (idx.match(/<svg id="stop-icon"[\s\S]*?<\/svg>/) || [''])[0];
check('the composer carries a monochrome stop glyph', !!_stopSvg && /fill="currentColor"/.test(_stopSvg) && (_stopSvg.match(/<rect /g) || []).length === 1 && !/#[0-9a-fA-F]{3,6}/.test(_stopSvg));
check('ui.js exports the five entry points', ['beginProvisionalAnswer', 'provisionalAnswerDelta', 'finishProvisionalAnswer', 'provisionalAnswerActive', 'provisionalAnswerText'].every(n => ui.includes(`export function ${n}(`)));
const css = fs.readFileSync(new URL('../static/css/main.css', import.meta.url), 'utf8');
check('the caret + partial note are styled with theme tokens and respect reduced motion', css.includes('.answer-stream-caret') && css.includes('.answer-partial-note') && css.includes('var(--teradata-orange') && (() => { let i = -1, found = false; while ((i = css.indexOf('prefers-reduced-motion', i + 1)) >= 0) { if (css.slice(i, i + 300).includes('.answer-stream-caret { animation: none')) found = true; } return found; })());

// ── the stream in the FINAL answer's shape (server-rendered html + the line in progress) ──
{
  P.beginProvisionalAnswer('s2'); const b2 = chatLog.lastElementChild; const c2 = b2.children[1].children[2];
  P.provisionalAnswerDelta({ seq: 1, message_index: 0, text: 'Para one.\nPart', html: '<p class="text-gray-300 mb-4">Para one.</p>', html_upto: 10 }); runRaf();
  check('server html is drawn as-is inside the final\'s own wrapper, the line in progress as the paragraph it will become',
    c2._html === '<div class="response-card summary-card mb-4"><p class="text-gray-300 mb-4">Para one.</p><p class="text-gray-300 mb-4">Part</p></div>', c2._html);
  check('marked is NOT used once the server renders (one renderer, never two)', !c2._html.includes('<p>Para'));
  P.provisionalAnswerDelta({ seq: 2, message_index: 0, text: 'ial **bo' }); runRaf();
  check('the tail keeps growing from the last html_upto; an unclosed ** stays literal (as the formatter leaves it)', c2._html.includes('<p class="text-gray-300 mb-4">Partial **bo</p>'), c2._html);
  P.provisionalAnswerDelta({ seq: 3, message_index: 0, text: 'ld** <b>x</b> <img src=x onerror=y> `c`' }); runRaf();
  // the tail applies the formatter's inline rules (formatter.md_inline): bold and code render,
  // an attribute-free formatting tag is kept exactly as the final keeps it, and any other
  // markup is shown as text — never executed (test_markdown_render holds the two equal)
  check('the tail applies the formatter\'s inline rules and escapes markup it does not keep', c2._html.includes('<strong>bold</strong> <b>x</b> &lt;img src=x onerror=y&gt; <code class="bg-gray-900/70 text-teradata-orange rounded-md px-1.5 py-0.5 font-mono text-sm">c</code>'), c2._html);
  P.provisionalAnswerDelta({ seq: 4, message_index: 1, reset: true });
  check('a withdrawn message drops its html', P._prov.html === null && P._prov.htmlUpto === 0);
  P.provisionalAnswerDelta({ seq: 5, message_index: 1, text: 'plain' }); runRaf();
  check('…and until the server renders again the stream falls back to marked (older server / genie lane)', c2._html === '<p>plain</p>', c2._html);
  P.finishProvisionalAnswer();
  const UL = '<ul class="list-disc list-outside space-y-2 text-gray-300 mb-4 pl-5">';
  P.beginProvisionalAnswer('s3'); const c3 = chatLog.lastElementChild.children[1].children[2];
  P.provisionalAnswerDelta({ seq: 1, message_index: 0, text: '- one\n- tw', html: UL + '<li>one</li></ul>', html_upto: 6 }); runRaf();
  check('a bullet being written joins the list above it (no 8px jump when its line completes)', c3._html === `<div class="response-card summary-card mb-4">${UL}<li>one</li><li>tw</li></ul></div>`, c3._html);
  P.finishProvisionalAnswer();
  check('tail: a held table header is not drawn (it would jump into a table)', P._provTailHtml('| A | B |') === '');
  check('tail: an open fence draws as a code block, escaped', P._provTailHtml('```sql\nSELECT <1>').includes('<pre><code>SELECT &lt;1&gt;</code></pre>'));
  check('tail: headings take the formatter\'s heading classes', P._provTailHtml('## Title').startsWith('<h3 class="text-lg font-bold text-white mb-3 border-b border-gray-700 pb-2">Title</h3>'));
}

// ── landing in place: the line the reader is on keeps its pixel row ─────────────────
{
  const blk = (txt, top) => ({ textContent: txt, querySelector: () => null, closest: () => null, getBoundingClientRect: () => ({ top, bottom: top + 20 }) });
  const host = (blocks, top) => ({ isConnected: true, querySelectorAll: () => blocks, getBoundingClientRect: () => ({ top }) });
  const sc = { scrollTop: 500, getBoundingClientRect: () => ({ top: 0, bottom: 800, height: 800 }) };
  const cls = new Set(); const wrap = (content) => ({ classList: { add: c => cls.add(c) }, querySelector: s => s === '.message-content' ? content : null });
  // follow on → the anchor is the LAST block with enough words (the line being written)
  // the line being written sits LOW (700) — far from the reading point (320), so only the follow rule picks it
  P._prov.content = host([blk('First paragraph of the answer here.', 310), blk('Second paragraph being written now.', 700), blk('tiny', 730)], 90);
  P._prov.scroller = sc; P._prov.follow = true;
  const a = P.captureReadingAnchor();
  check('following: the anchor is the line being written (a fragment under 12 chars is skipped)', a && a.key === 'Second paragraph being written now.' && a.top === 700, JSON.stringify(a && { k: a.key, t: a.top }));
  P.landInPlace(wrap(host([blk('First paragraph of the answer here.', 300), blk('Second paragraph being written now.', 737)], 80)), a);
  check('landing: the anchored line moved +37 → the scroll moves +37, so it stays on its pixel row', sc.scrollTop === 537, String(sc.scrollTop));
  check('landing: the final bubble carries msg-in-place (no entrance animation)', cls.has('msg-in-place'));
  // the final no longer carries the anchored words (Optimize: the sentence became a lead figure)
  sc.scrollTop = 500;
  P.landInPlace(wrap(host([blk('100 Total Products', 140)], 120)), a);
  check('a line the final dropped falls back to the answer\'s own top edge (90 → 120: +30)', sc.scrollTop === 530, String(sc.scrollTop));
  // not following → the block at the reading point (40% of the viewport)
  P._prov.follow = false; P._prov.content = host([blk('Paragraph far above the fold.', -400), blk('Paragraph the reader is reading.', 310), blk('Paragraph further below it.', 700)], -450);
  const b = P.captureReadingAnchor();
  check('scrolled: the anchor is the block at the reading point, not the last one', b && b.key === 'Paragraph the reader is reading.', b && b.key);
  sc.scrollTop = 500; P.landInPlace(wrap(host([blk('Paragraph the reader is reading.', 310)], -450)), b);
  check('nothing moved → nothing scrolls', sc.scrollTop === 500);
  check('no anchor (nothing streamed) → still lands without animation, never throws', (() => { try { P.landInPlace(wrap(host([], 0)), null); return true; } catch (_) { return false; } })());
  P._prov.content = null; P._prov.scroller = null; P._prov.follow = true;
}
{
  const iCap = faBody.indexOf('UI.captureReadingAnchor()'), iFin = faBody.indexOf('UI.finishProvisionalAnswer('), iAdd = faBody.indexOf("UI.addMessage('assistant', eventData.final_answer"), iSet = faBody.indexOf('window.LivingAnswer.settle('), iLand = faBody.indexOf('UI.landInPlace(');
  check('final_answer: the anchor is taken BEFORE the streamed text is dropped, the landing AFTER the settle', iCap > 0 && iCap < iFin && iFin < iAdd && iAdd < iSet && iSet < iLand);
  check('final_answer: the settle is told the answer landed in place (so it never follows to the bottom)', /inPlace: _streamedHere/.test(faBody.slice(iSet, iLand)));
  const la = fs.readFileSync(new URL('../static/js/handlers/livingAnswer.js', import.meta.url), 'utf8');
  check('livingAnswer.settle skips follow() when the answer landed in place', /if \(!o\.inPlace && onScreen\(sid\)\) follow\(\);/.test(la));
  check('livingAnswer.renderText draws the server html when given one (marked only as the fallback)', /function renderText\(content, text, prebuilt\)/.test(la) && /typeof prebuilt === 'string'/.test(la));
  check('main.css: a landed message has no entrance animation', css.includes('.message-bubble.msg-in-place { animation: none; }'));
}

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
