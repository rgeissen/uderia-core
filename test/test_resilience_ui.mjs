// S4 — the Resilience Envelope's three surfaces, executed / structurally pinned:
//   (1) the provider-health strip: renders ONLY while a breaker is degraded, names the state and
//       the recovery time, announces a change once (aria-live), disappears on recovery;
//   (2) the Live Status `llm_recovery` card: one reading per kind, the served model named for a
//       fallback, never a verdict;
//   (3) the answer's `served by` chip reads the turn-keyed map the session load / the live
//       fallback notice fill; the live + reload wiring is ONE event shape.
import fs from 'node:fs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// ── (1) the strip, executed ───────────────────────────────────────────────────────────────
{
  const src = read('static/js/handlers/providerHealthStrip.js');
  const host = { id: 'provider-health-strip', hidden: true, innerHTML: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const document = { readyState: 'complete', getElementById: (id) => (id === 'provider-health-strip' ? host : null), addEventListener() {} };
  const window = {};
  const localStorage = { getItem: () => null };
  let fetched = 0;
  const fetch = async () => { fetched++; return { ok: true, json: async () => ({ providers: [] }) }; };
  const timers = []; const setInterval = (f, ms) => { timers.push(f); return timers.length; }; const clearInterval = () => {};
  new Function('document', 'window', 'localStorage', 'fetch', 'setInterval', 'clearInterval', src)(document, window, localStorage, fetch, setInterval, clearInterval);
  const S = window.ProviderHealthStrip;
  check('the strip exposes update/refresh/render', S && typeof S.update === 'function' && typeof S.refresh === 'function');
  check('on load it re-reads the health endpoint (never a stale strip after a reload)', fetched === 1);
  S.update({ provider: 'Anthropic', state: 'open', degraded: true, recovers_in: 42, last_reason: 'overloaded' });
  check('a degraded provider shows the strip', host.hidden === false);
  check('…naming the provider, the reason, the fallback routing and the recovery time', /Anthropic/.test(host.innerHTML) && /overloaded/.test(host.innerHTML) && /fallback/.test(host.innerHTML) && /42s/.test(host.innerHTML));
  check('…announced once (aria-live polite on the change)', host.attrs['aria-live'] === 'polite');
  S.render();
  check('a re-render of the SAME state is not re-announced (aria-live off)', host.attrs['aria-live'] === 'off');
  S.update({ provider: 'Anthropic', state: 'half_open', degraded: true, recovers_in: 0 });
  check('half-open reads as a probe pending', /probe/.test(host.innerHTML) && host.attrs['aria-live'] === 'polite');
  S.update({ provider: 'Anthropic', state: 'closed', degraded: false });
  check('recovery hides the strip and empties it', host.hidden === true && host.innerHTML === '');
  S.update({ provider: 'Google', state: 'open', degraded: true, recovers_in: 130 });
  check('a long wait reads in minutes', /3 min/.test(host.innerHTML));
  check('no motion: no animation/transition in the strip CSS', !/animation|transition/.test(read('static/css/main.css').split('.provider-health-strip')[1].split('/* Genie')[0]));
  check('role=status host in the header, hidden by default', /id="provider-health-strip"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden/.test(read('templates/index.html')));
}

// ── (2) the Live Status card, executed ────────────────────────────────────────────────────
{
  const ui = read('static/js/ui.js');
  const fn = ui.match(/function _renderLlmRecoveryDetails\(details\) \{[\s\S]*?\n\}\n/)[0];
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _mechLink = (k) => `<span data-mech="${k}">${k}</span>`;
  const _trustChip = (lvl, word) => `<span class="trust-chip tc-${lvl}">${word}</span>`;
  const _trustVal = (lvl) => `data-lvl="${lvl}"`;
  const render = new Function('escapeHtml', '_mechLink', '_trustChip', '_trustVal', fn + '; return _renderLlmRecoveryDetails;')(escapeHtml, _mechLink, _trustChip, _trustVal);
  const retry = render({ kind: 'retry', reason: 'rate_limit', delay: 3, attempt: 2, status: 429, summary: 'OpenAI/m failed (rate_limit); retried after 3.0s (attempt 2)' });
  check('a retry reads pass + the wait + the attempt', /tc-pass/.test(retry) && /retried \(rate_limit\)/.test(retry) && /3\.0s before attempt 2/.test(retry) && /HTTP 429/.test(retry));
  const fb = render({ kind: 'fallback', reason: 'overloaded', from: 'OpenAI/m', to: 'Anthropic/fb', summary: 's' });
  check('a fallback reads warn + names who served instead of whom', /tc-warn/.test(fb) && /Served by/.test(fb) && /Anthropic\/fb/.test(fb) && /instead of OpenAI\/m/.test(fb) && /deployment drift/.test(fb));
  const ref = render({ kind: 'refusal', reason: 'safety', summary: 's', detail: '<script>' });
  check('a refusal reads warn, names the category, escapes the detail', /provider refused \(safety\)/.test(ref) && /&lt;script&gt;/.test(ref));
  const cont = render({ kind: 'continuation', reason: 'length', summary: 's' });
  check('a continuation reads pass', /tc-pass/.test(cont) && /continued \(length\)/.test(cont));
  check('the card routes to the mechanism (one glossary channel)', /data-mech="llm_recovery"/.test(fb));
  check('the card is dispatched on both live paths and the reload branch', (ui.match(/case 'llm_recovery': \{/g) || []).length === 2 && /type === "llm_recovery"\) \{\s*customRenderedHtml = _renderLlmRecoveryDetails/.test(ui));
  // the PROPERTY (llm_recovery is classified assurance + is a live-status-relevant type), not the
  // exact chain literal — a later step type joining the same chain must not break this gate.
  const _catChain = (ui.match(/[^\n]*\) return 'assurance';/) || [''])[0];
  check('the card is an assurance category + a live-status-relevant type', /type === 'llm_recovery'/.test(_catChain) && /type === 'llm_recovery' \|\|/.test(ui.split("return 'assurance'")[1] || ''));
  check('the dial is told (LIVE_STATION → SIGNED)', /AssuranceDial\.note\(_adSession\(\), 'llm_recovery', details\)/.test(ui) && /llm_recovery: 'signed'/.test(read('static/js/handlers/assuranceDial.js')));
}

// ── (3) the chip + the wiring ─────────────────────────────────────────────────────────────
{
  const ui = read('static/js/ui.js');
  const chip = ui.match(/\/\/ S4 — an answer the SIGNED fallback served[\s\S]*?author\.appendChild\(tag\);\n    \}/);
  check('the served-by chip renders on the ASSISTANT message from the turn-keyed map', chip && /role === 'assistant' && turnId != null && state\.servedByTurn && state\.servedByTurn\[turnId\]/.test(chip[0]));
  check('…as a u-tip with the fallback named and the billing stated', chip && /served-by-tag u-tip/.test(chip[0]) && /billed at the model that served/.test(chip[0]));
  const st = read('static/js/state.js');
  check('state carries servedByTurn + pendingServedBy', /servedByTurn: \{\}/.test(st) && /pendingServedBy: \{\}/.test(st));
  const sm = read('static/js/handlers/sessionManagement.js');
  check('a session load fills servedByTurn from the persisted resilience stamp BEFORE the messages render',
    /state\.servedByTurn = \{\};[\s\S]*?t\.resilience && t\.resilience\.served_by[\s\S]*?state\.feedbackByTurn = \{ \.\.\.data\.feedback_by_turn \};/.test(sm) && sm.indexOf('state.servedByTurn = {}') < sm.indexOf("UI.addMessage(msg.role, msg.content, "));
  const nj = read('static/js/notifications.js');
  const rc = nj.match(/case 'llm_recovery': \{[\s\S]*?break;\n\s*\}/);
  check('the live llm_recovery notice renders the SAME card, session-guarded', rc && /rd\.session_id === state\.currentSessionId/.test(rc[0]) && /type: 'llm_recovery'/.test(rc[0]));
  check('…and a fallback notice stashes served_by per session until final_answer names the turn', rc && /pendingServedBy\[rd\.session_id\]/.test(rc[0]));
  check('the provider_health notice feeds the strip', /case 'provider_health'[\s\S]*?ProviderHealthStrip\.update/.test(nj));
  const eh = read('static/js/eventHandlers.js');
  check('final_answer moves the pending served_by onto the delivered turn (live chip)', /state\.servedByTurn\[eventData\.turn_id\] = _sb/.test(eh));
  check('the REST completion path does the same', /state\.servedByTurn\[turn_id\] = _sb/.test(nj));
  const synth = eh.match(/Reload-harden the S4 recovery steps[\s\S]*?\n        \}\n/);
  check('a reload synthesizes ONE llm_recovery event per persisted step with the live titles', synth && /turnData\.resilience\.steps\.forEach/.test(synth[0]) && /retry: 'Provider Retry', continuation: 'Answer Continued', fallback: 'Served by Fallback', refusal: 'Provider Refusal'/.test(synth[0]));
  check('…routed to genie_events for a coordinator turn', synth && /isGenieTurn \? 'genie_events' : 'system_events'/.test(synth[0]));
  // the reload TITLE (found in the browser: every card read `llm_recovery`) — one helper, all four class title functions
  const helper = eh.match(/function _assuranceStepTitle\(eventType, payload\) \{[\s\S]*?\n\}/);
  check('a reload titles the step per kind through ONE helper', helper && /retry: 'Provider Retry'/.test(helper[0]) && /compute_lineage/.test(helper[0]));
  const consulted = ['getToolEnabledTitle', 'getLlmOnlyTitle', 'getRagFocusedTitle', 'getGenieTitle'].filter((f) => new RegExp(`function ${f}\\([\\s\\S]*?return _assuranceStepTitle\\(eventType, payload\\) \\|\\| eventType;`).test(eh));
  check('…consulted by all four per-class title functions', consulted.length === 4, consulted);
  const cfg = read('static/js/handlers/configurationHandler.js');
  check('the editor offers the fallback (None first, the primary excluded) and saves null when cleared', /None — no fallback/.test(cfg) && /filter\(c => c\.id !== llmSelect\.value\)/.test(cfg) && /llmFallbackConfigurationId: \(\(\) => \{/.test(cfg) && /return \(v && v !== llmSelect\.value\) \? v : null/.test(cfg));
  check('the editor markup carries the select + the explanation', /id="profile-modal-llm-fallback"/.test(read('templates/index.html')) && /Part of the signed deployment/.test(read('templates/index.html')));
}
{
  // review fixes
  const eh2 = read('static/js/eventHandlers.js');
  const sub = eh2.slice(eh2.indexOf('window.AssuranceDial.begin(originSessionId, \'live\')'), eh2.indexOf('window.AssuranceDial.begin(originSessionId, \'live\')') + 600);
  check('a new turn clears a served-by chip pending from an abandoned turn (beside the budget bar reset)', /BudgetBar\.reset\(\)/.test(sub) && /delete state\.pendingServedBy\[originSessionId\]/.test(sub));
  const phs = read('static/js/handlers/providerHealthStrip.js');
  const r = phs.slice(phs.indexOf('const told = degraded.map'), phs.indexOf('h.hidden = false'));
  check("the strip's live region sets its politeness BEFORE the mutation it announces", r.indexOf("h.setAttribute('aria-live'") < r.indexOf('h.innerHTML ='));
  const bb = read('static/js/handlers/budgetBar.js');
  check('the budget bar says "nearly used" at the same share the model is told (0.75)', /const NEAR = 0\.75;/.test(bb));
}
console.log(fail ? `FAILED — ${fail} of ${pass + fail}` : `ALL PASS — ${pass} assertions (resilience UI)`);
process.exit(fail ? 1 : 0);
