#!/usr/bin/env node
/**
 * The header status strip is one quiet strip that leads with a verdict — the gate.
 *
 * The six connection LEDs (MCP · LLM · CTX · KNW · CCR · SSE) were a glass pill: gradient,
 * blur, glow, halo ring, text shadow, labels that spread on hover — restyled in THREE files
 * (main.css, themes.css with eleven !important rules, polish.css). It is now ONE block in
 * main.css: a hairline capsule, 8px solid dots, 11px labels, colour in the dot only, and the
 * summary leads with a verdict word (Ready · Working · Reconnecting · Offline) that
 * statusSummary.js writes from the class it already picks. Activity ripples; it never blinks.
 *
 * Before the stream reports, the page says what is true instead of "Offline": the SSE and
 * summary dots start `pending`; no session reads Signed out (hollow, muted, nothing to open),
 * a session whose stream has not opened reads Connecting. Offline = the stream failed.
 *
 * A stream finding its way back reads Reconnecting, never Working, and it does find its way
 * back: an error RESPONSE (a proxy's 502 during a restart) closes an EventSource for good, so
 * notifications.js reopens it with a 2s → 30s backoff — the strip once stayed amber until reload.
 *
 * Nothing the drivers rely on moved: every id, every class name they write, the SSE-mirror
 * rule, the MutationObserver, Enter/Space, aria-expanded, the .is-open reveal.
 *
 * Run: node test/test_status_bar.mjs
 */
import fs from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };
const read = (p) => fs.readFileSync(p, 'utf8');
const block = (css, sel) => { const i = css.indexOf(sel); if (i < 0) return ''; return css.slice(i, css.indexOf('}', i) + 1); };

const T = read('templates/index.html');
const CSS = read('static/css/main.css');
const TH = read('static/css/themes.css');
const PO = read('static/css/polish.css');
const JS = read('static/js/handlers/statusSummary.js');

const secStart = T.indexOf('<div class="status-bar-section">');
const secEnd = T.indexOf('<!-- User Menu (Authentication) -->', secStart);
const sec = secStart > 0 ? T.slice(secStart, secEnd) : '';

// ---- 1. the markup: ids kept, Tailwind carries no colour or size ----
check('the strip exists', !!sec);
for (const id of ['status-summary', 'status-summary-dot', 'status-summary-word', 'status-strip-detail', 'mcp-status-dot', 'llm-status-dot', 'context-status-dot', 'knowledge-status-dot', 'ccr-status-dot', 'sse-status-dot'])
    check(`…#${id} is present (the drivers address it)`, sec.includes(`id="${id}"`));
check('…six LEDs, each a .connection-dot', (sec.match(/class="connection-dot/g) || []).length === 7);
check('…KNW starts hollow (knowledge-idle); the summary and SSE start pending; the rest disconnected', /id="knowledge-status-dot" class="connection-dot knowledge-idle"/.test(sec) && /id="status-summary-dot" class="connection-dot pending"/.test(sec) && /id="sse-status-dot" class="connection-dot pending"/.test(sec) && (sec.match(/class="connection-dot disconnected"/g) || []).length === 4);
check('…no Tailwind colour / shadow / blur classes on the strip', !/\b(bg-white\/|text-gray-|via-white|shadow-lg|backdrop-blur|rounded-lg|from-transparent)/.test(sec));
check('…no Tailwind padding/gap on the section (spacing is the token block\'s)', !/status-bar-section[^"]*"[^>]*class="[^"]*\b(px-|py-|gap-x-)/.test(sec) && !/<div class="status-bar-section [^"]*(px-5|gap-x-5)/.test(sec));
check('…dividers are plain .status-divider / .status-divider-strong', (sec.match(/class="status-divider"/g) || []).length === 2 && /class="status-divider-strong"/.test(sec));
check('…LEDs grouped in .status-group', (sec.match(/class="status-group"/g) || []).length === 3);
check('…the summary is a button-like control: role, tabindex, aria-expanded, aria-controls', /role="button"/.test(sec) && /tabindex="0"/.test(sec) && /aria-expanded="false"/.test(sec) && /aria-controls="status-strip-detail"/.test(sec));
check('…the summary starts Signed out, never Offline (data-verdict + the word)', /data-verdict="signed-out"/.test(sec) && /<span id="status-summary-word" class="status-label">Signed out<\/span>/.test(sec) && !/data-verdict="offline"/.test(sec) && !/>Offline</.test(sec));
check('…no disclosure caret: the summary is the dot and its word', !/status-caret/.test(sec) && !/<svg/.test(T.slice(T.indexOf('id="status-summary"'), T.indexOf('</div>', T.indexOf('id="status-summary-word"')))));
check('…CTX names its click (the only clickable LED)', /title="Context window — click to purge the conversation context"/.test(sec) && /cursor-pointer group" title="Context window/.test(sec));
check('…every item has a title in words (no acronym-only tooltips)', !/title="MCP Server Status"|title="LLM Provider Status"|title="Champion Case Retrieval Status"/.test(sec));
check('…no emoji', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(sec));

// ---- 2. one CSS block, flat ----
const sect = block(CSS, '.status-bar-section {');
check('.status-bar-section: capsule, token fill, hairline border', /border-radius:\s*9999px/.test(sect) && /background:\s*var\(--status-pill-bg/.test(sect) && /border:\s*1px solid var\(--border-primary/.test(sect));
check('…no gradient, no blur, no glow', !/gradient/.test(sect) && /backdrop-filter:\s*none/.test(sect) && /box-shadow:\s*none/.test(sect));
check('…32px tall', /height:\s*32px/.test(sect));
const lbl = block(CSS, '\n.status-label {');   // anchored: '.status-item:hover .status-label {' comes first
check('.status-label: 11px, weight 500, muted token, no text shadow', /font-size:\s*11px/.test(lbl) && /font-weight:\s*500/.test(lbl) && /color:\s*var\(--text-muted/.test(lbl) && /text-shadow:\s*none/.test(lbl));
const hov = block(CSS, '.status-item:hover .status-label {');
check('hover lifts the label to primary text and does NOT spread the letters', /color:\s*var\(--text-primary/.test(hov) && !/letter-spacing/.test(hov) && !/text-shadow:\s*0/.test(hov));
check('the hover pill is the shared hover token', /\.status-item:hover \{ background: var\(--hover-bg/.test(CSS));
const dot = block(CSS, '.connection-dot {');
check('.connection-dot: 8px, solid fill from --dot, no shadow', /width:\s*8px/.test(dot) && /background:\s*var\(--dot\)/.test(dot) && /box-shadow:\s*none/.test(dot) && !/gradient/.test(dot));
check('…the halo ring is gone', /\.connection-dot::before \{ content: none; \}/.test(CSS));
check('…hover does not scale or glow', /\.connection-dot:hover \{ transform: none; box-shadow: none; \}/.test(CSS));
check('no rule repaints a dot on hover (the legacy white !important hover on CTX is gone)', !/#context-status-dot:hover/.test(CSS) && !/connection-dot[^{]*:hover \{[^}]*background/.test(CSS));
check('no radial-gradient remains on any connection-dot state', !/\.connection-dot\.[a-z-]+ \{[^}]*radial-gradient/.test(CSS));

// ---- 3. the dot grammar: every class the drivers write ----
const states = ['pending', 'disconnected', 'connected', 'idle', 'busy', 'reconnecting', 'context-last-turn-temp', 'context-last-turn-locked', 'pulsing', 'knowledge-idle', 'knowledge-configured', 'knowledge-active', 'history-disabled-preview'];
for (const st of states) check(`…\.connection-dot.${st} is styled`, new RegExp(`\\.connection-dot\\.${st}\\b`).test(CSS));
check('disconnected = error token', /\.connection-dot\.disconnected \{ --dot: var\(--status-error/.test(CSS));
check('connected · idle · knowledge-configured = success token', /\.connection-dot\.connected,\s*\.connection-dot\.idle,\s*\.connection-dot\.knowledge-configured \{ --dot: var\(--status-success/.test(CSS));
check('busy · reconnecting · last-turn-temp = warning token', /\.connection-dot\.busy,\s*\.connection-dot\.reconnecting,\s*\.connection-dot\.context-last-turn-temp \{ --dot: var\(--status-warning/.test(CSS));
check('last turn held = dashed, hollow', /\.connection-dot\.context-last-turn-temp \{[^}]*border: 1\.5px dashed var\(--dot\)/.test(CSS) && /\.connection-dot\.context-last-turn-temp \{[^}]*background: transparent/.test(CSS));
check('last turn locked = brand orange (a chosen mode, never an alarm)', /\.connection-dot\.context-last-turn-locked \{ --dot: var\(--teradata-orange/.test(CSS));
check('pending = hollow grey, the same reading as knowledge idle (nothing to report yet)', /\.connection-dot\.knowledge-idle,\s*\.connection-dot\.pending \{\s*--dot: var\(--text-muted/.test(CSS));
check('knowledge idle = hollow grey, full opacity', /\.connection-dot\.knowledge-idle,\s*\.connection-dot\.pending \{[^}]*background: transparent;[^}]*box-shadow: inset 0 0 0 1\.5px var\(--dot\)/.test(CSS) && !/\.connection-dot\.(knowledge-idle|pending)[^{]*\{[^}]*opacity: 0\./.test(CSS));
check('knowledge active = purple', /\.connection-dot\.knowledge-active \{ --dot: #a855f7; \}/.test(CSS));
check('pulsing = a ripple ring, not a colour blink', /\.connection-dot\.pulsing \{ animation: none; \}/.test(CSS) && /\.connection-dot\.pulsing::after \{[^}]*animation: status-ripple/.test(CSS) && !/@keyframes blink \{/.test(CSS));
check('reduced motion stops the ripple', /prefers-reduced-motion: reduce\) \{\s*\.connection-dot\.pulsing::after \{ animation: none/.test(CSS));
check('the purge flash keeps no glow', /@keyframes blink-white \{\s*50% \{\s*background-color: var\(--text-primary, white\);\s*\}\s*\}/.test(CSS));

// ---- 4. the reveal, and the verdict's chrome ----
check('the reveal lives in the same block (#status-strip-detail collapsed → .is-open)', /#status-strip-detail \{[^}]*max-width: 0/.test(CSS) && /#status-strip-detail\.is-open \{[^}]*max-width: 720px/.test(CSS));
check('the strip opens to the LEFT, so the summary never moves: it sits before the summary, its strong divider last, margin on its right, sliding in from the right', sec.indexOf('id="status-strip-detail"') < sec.indexOf('id="status-summary"') && sec.indexOf('class="status-divider-strong"') > sec.indexOf('id="sse-status-dot"') && /#status-strip-detail \{[^}]*margin-right: 0;[^}]*transform: translateX\(6px\)/.test(CSS) && /#status-strip-detail\.is-open \{[^}]*margin-right: 8px/.test(CSS) && !/#status-strip-detail[^{]*\{[^}]*margin-left/.test(CSS));
check('…and no caret styling is left behind', !/\.status-caret/.test(CSS));
check('the verdict word is sentence case, primary text', /#status-summary \.status-label \{[^}]*text-transform: none;[^}]*color: var\(--text-primary/.test(CSS));
check('signed out is untinted, muted, not a pointer; connecting is untinted', /#status-summary\[data-verdict="signed-out"\] \{ cursor: default; \}/.test(CSS) && /#status-summary\[data-verdict="signed-out"\] \.status-label \{ color: var\(--text-muted/.test(CSS) &&!/data-verdict="(signed-out|connecting)"\][^{]*\{[^}]*(--status-error|--status-warning)/.test(CSS));
check('working/reconnecting tint the summary amber, offline red — via tokens', /#status-summary\[data-verdict="working"\],\s*#status-summary\[data-verdict="reconnecting"\] \{[^}]*--status-warning/.test(CSS) && /#status-summary\[data-verdict="offline"\] \{[^}]*--status-error/.test(CSS));

// ---- 5. ONE place: themes.css and polish.css no longer restyle the strip ----
check('themes.css keeps one light token for the strip and nothing else', (TH.match(/\.status-bar-section/g) || []).length === 1 && /\[data-theme="light"\] \.status-bar-section \{ --status-pill-bg:/.test(TH));
check('…no light-theme !important on labels, dots or dividers', !/\[data-theme="light"\] \.(status-label|connection-dot|status-divider|status-item)/.test(TH));
check('polish.css no longer styles the strip (section 6 points to main.css)', !/\.status-bar-section \{/.test(PO) && !/\.connection-dot/.test(PO) && !/#status-strip-detail \{/.test(PO) && /styled in ONE place: main\.css/.test(PO));
check('…polish.css keeps the shared focus ring and tabular figures for the labels', /\.status-item:focus-visible \{/.test(PO) && /\.status-label,\s*time \{/.test(PO));

// ---- 6. the verdict word comes from the class the handler already picks ----
check('statusSummary.js names the six verdicts', /'signed-out':\s*'Signed out'/.test(JS) && /connecting:\s*'Connecting'/.test(JS) && /working:\s*'Working'/.test(JS) && /reconnecting:\s*'Reconnecting'/.test(JS) && /offline:\s*'Offline'/.test(JS) && /ready:\s*'Ready'/.test(JS));
check('…busy beats reconnecting beats disconnected beats connected; a stream that has not reported is connecting', /if \(states\.includes\('busy'\)\)\s*return 'working';\s*if \(states\.includes\('reconnecting'\)\)\s*return 'reconnecting';\s*if \(states\.includes\('disconnected'\)\)\s*return 'offline';\s*if \(states\.includes\('connected'\)\)\s*return 'ready';\s*return 'connecting';/.test(JS));
check('…no session reads Signed out before anything else, with a pending dot', /if \(!_signedIn\(\)\) \{\s*\/\/[^\n]*\n\s*summary\.className = 'connection-dot pending';\s*_writeVerdict\('signed-out'\);\s*return;/.test(JS) && JS.indexOf('_signedIn()) {') < JS.indexOf('_dots().some(_isWorking)) {'));
check('…a reconnecting LED reads Reconnecting (amber ripple), checked after busy, never Working', /if \(_dots\(\)\.some\(_isReconnecting\)\) \{\s*summary\.className = 'connection-dot reconnecting pulsing';\s*_writeVerdict\('reconnecting'\);/.test(JS) && JS.indexOf('_dots().some(_isWorking)') < JS.indexOf('_dots().some(_isReconnecting)') && !/YELLOW_STATES/.test(JS) && /function _isWorking\(dot\) \{\s*return dot\.classList\.contains\('pulsing'\) \|\| dot\.classList\.contains\('busy'\);/.test(JS));
const NT = read('static/js/notifications.js');
check('notifications.js: a stream the browser CLOSED is reopened (it never retries an error response)', /eventSource\.readyState === EventSource\.CLOSED && _sse === eventSource/.test(NT) && /_scheduleResubscribe\(_sseRetryDelay\)/.test(NT));
check('…backoff 2s doubling to a 30s cap, reset when the stream opens', /SSE_RETRY_MIN_MS = 2000/.test(NT) && /SSE_RETRY_MAX_MS = 30000/.test(NT) && /Math\.min\(_sseRetryDelay \? _sseRetryDelay \* 2 : SSE_RETRY_MIN_MS, SSE_RETRY_MAX_MS\)/.test(NT) && /onopen = async \(\) => \{[^}]*_sseRetryDelay = 0;/.test(NT));
check('…one stream per page (the old one closed), no reopen once signed out, sooner when back online', /if \(_sse\) _sse\.close\(\);/.test(NT) && /if \(!localStorage\.getItem\('tda_auth_token'\)\) return;/.test(NT) && /addEventListener\('online'/.test(NT));
check('…the stale comment promising the browser reconnects by itself is gone', !/the browser will attempt to reconnect automatically/.test(NT));
check('…main.js imports the new notifications.js', parseFloat((read('static/js/main.js').match(/notifications\.js\?v=([\d.]+)/) || [, '0'])[1]) >= 5.108);
check('…the session is read from the one token key, expiry honoured, read-only, never throws', /localStorage\.getItem\('tda_auth_token'\)/.test(JS) && /tda_token_expiry/.test(JS) && !/localStorage\.removeItem/.test(JS) && /catch \(_\) \{ return false; \}/.test(JS));
check('…signed out opens nothing: the toggle refuses, aria-disabled set, an open strip closes', /if \(summary\.getAttribute\('data-verdict'\) === 'signed-out'\) return;/.test(JS) && /setAttribute\('aria-disabled', 'true'\)/.test(JS) && /detail\.classList\.remove\('is-open'\)/.test(JS));
check('…a sign-in/out in another tab re-reads the verdict', /addEventListener\('storage'/.test(JS));
check('…the SSE writer clears pending when the stream reports', /sseStatusDot\.classList\.remove\('pending', 'connected', 'reconnecting', 'disconnected'\)/.test(read('static/js/ui.js')));
check('…any busy LED → the summary is busy pulsing AND reads Working', /summary\.className = 'connection-dot busy pulsing';\s*_writeVerdict\('working'\);/.test(JS));
check('…otherwise the SSE classes are inherited verbatim and the verdict follows them (no class = pending, never disconnected)', /summary\.className = 'connection-dot ' \+ \(states\.length \? states\.join\(' '\) : 'pending'\);\s*_writeVerdict\(_verdictFor\(states\)\);/.test(JS));
check('…the word is written to #status-summary-word and data-verdict to #status-summary', /host\.setAttribute\('data-verdict', key\)/.test(JS) && /getElementById\('status-summary-word'\)/.test(JS));
check('…the mirror still observes every LED\'s class', /new MutationObserver\(_update\)/.test(JS) && /attributeFilter: \['class'\]/.test(JS));
check('…Enter/Space still toggle, aria-expanded still written', /e\.key === 'Enter' \|\| e\.key === ' '/.test(JS) && /setAttribute\('aria-expanded', shown \? 'true' : 'false'\)/.test(JS));

// ---- 7. pins ----
const pin = (re) => parseFloat((T.match(re) || [, '0'])[1]);
check('main.css pin ≥ 5.15', pin(/css\/main\.css'\) \}\}\?v=([\d.]+)"/) >= 5.15);
check('themes.css pin ≥ 1.9', pin(/css\/themes\.css'\) \}\}\?v=([\d.]+)"/) >= 1.9);
check('polish.css pin ≥ 2.5', pin(/css\/polish\.css'\) \}\}\?v=([\d.]+)"/) >= 2.5);
check('statusSummary.js pin ≥ 1.4', pin(/statusSummary\.js'\) \}\}\?v=([\d.]+)"/) >= 1.4);

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
