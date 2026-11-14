// Needs input (PROP-011) — the question card and its wiring.
//   (1) the renderer EXECUTED under the DOM stub: a pending card under a bubble (items, options,
//       Send disabled until answered, who asks and on what grounds), the composer rule
//       (bindComposerText), supersession, the bus events the manifest declares, a closed card;
//   (2) the wiring, in the source: the final answer attaches the card (stream and REST), the
//       reload rebuilds it from the turn record + the session's pending question, the composer
//       posts the binding, the status strip reads Waiting for you as a quiet reading, the
//       Workspace section, the split panel markup, the Inbox's questions.
// Run: node test/test_needs_input_ui.mjs
import './_uderia_bus.mjs';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { installDom, El } from './dom_stub.mjs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// ── (1) the renderer, executed ──────────────────────────────────────────────────────
const { doc, body } = installDom();
globalThis.CSS = globalThis.CSS || { escape: (s) => String(s) };
const host = new El('div');
host.innerHTML = `<div id="chat-log"><div class="message-wrap"><div class="message-bubble" id="b1"></div></div></div>
<form id="chat-form"><textarea id="user-input"></textarea></form>`;
body.appendChild(host);
const emitted = [];
UderiaBus.on('uderia:input-required', (ev) => emitted.push(['required', ev.detail]));
UderiaBus.on('uderia:input-answered', (ev) => emitted.push(['answered', ev.detail]));
const M = await import(pathToFileURL('components/builtin/needs_input/renderer.js').href);
const bubble = doc.getElementById('b1');
const req = {
  id: 'ir-1', session_id: 's-1', status: 'pending', kind: 'choose', raised_by: 'code', profile_tag: 'SALES',
  source: 'graphs.get.kg_id', why: "'Sales' names 2 of your knowledge graphs", resume: 'tok',
  items: [{ header: 'Graph', question: "Which knowledge graph do you mean by 'Sales'?",
            options: [{ value: 'kg-1', label: 'Sales', detail: 'north' }, { value: 'kg-2', label: 'Sales (south)' }],
            multi_select: false, other_allowed: false }],
};
const card = M.attach(bubble, req);
check('a pending card is attached under the bubble, pending', card && card.dataset.state === 'pending' && card.dataset.niId === 'ir-1');
check('the card names who asks and on what grounds (the agent, the platform mark, the source)',
  /@SALES/.test(card.innerHTML) && /platform/.test(card.innerHTML) && /graphs\.get\.kg_id/.test(card.innerHTML));
check('every option is a button carrying its value; the why is muted text',
  card.querySelectorAll('.ni-opt').length === 2 && card.querySelector('.ni-opt[data-value="kg-2"]') && /ni-why/.test(card.innerHTML));
check('Send is disabled until an answer is chosen; Decline and Not now are offered',
  card.querySelector('[data-ni-act="accept"]').disabled === true && card.querySelector('[data-ni-act="decline"]') && card.querySelector('[data-ni-act="cancel"]'));
check('the attach emits uderia:input-required for the session', emitted.some(([k, d]) => k === 'required' && d.session_id === 's-1' && d.request_id === 'ir-1'));
check('the same request attached twice is one card', M.attach(bubble, req) === card);
check('pendingFor reads the pending question of the session', M.pendingFor('s-1') && M.pendingFor('s-1').id === 'ir-1' && M.pendingFor('s-2') === null);
// the composer rule
let b = M.bindComposerText('s-1', 'sales (south)');
check('a typed option label IS the answer (bound by value, with the binding)', b && b.decision === 'accept' && b.values.Graph === 'kg-2' && b.resume === 'tok' && b.input_request_id === 'ir-1');
check('a typed option value is too', (M.bindComposerText('s-1', 'kg-1') || {}).values?.Graph === 'kg-1');
check('anything else is a new prompt (null) where Other is not allowed', M.bindComposerText('s-1', 'show me revenue by month') === null);
check('no pending question → null', M.bindComposerText('s-9', 'kg-1') === null);
M.supersede('s-1');
check('moving on supersedes the card: closed, says so, emits input-answered', card.dataset.state === 'closed' && /moved on/.test(card.innerHTML)
  && emitted.some(([k, d]) => k === 'answered' && d.decision === 'superseded') && M.pendingFor('s-1') === null);
// other allowed + provide
const req2 = { id: 'ir-2', session_id: 's-2', status: 'pending', kind: 'provide', raised_by: 'model', profile_tag: 'FIN', resume: 't2',
  items: [{ header: 'FY', question: 'When does the fiscal year start?', schema: { type: 'string', format: 'date' } }] };
const b2host = new El('div'); body.appendChild(b2host);
const card2 = M.attach(b2host, req2, { host: b2host });
check('a provide question renders a typed field of the schema\'s type and the agent mark', card2.querySelector('input[data-ni-field="value"]') && /agent/.test(card2.innerHTML));
check('typed text answers a provide question through the composer', (M.bindComposerText('s-2', '2026-04-01') || {}).values?.FY === '2026-04-01');
// a closed card on reload
const done = Object.assign({}, req, { id: 'ir-3', session_id: 's-3', status: 'accepted', answer: { Graph: 'kg-2' } });
const c3host = new El('div'); body.appendChild(c3host);
const card3 = M.attach(c3host, done, { host: c3host });
check('a reloaded answered card shows the answer by its label and offers no buttons',
  card3.dataset.state === 'answered' && /Sales \(south\)/.test(card3.innerHTML) && !card3.querySelector('[data-ni-act]'));
const exp = Object.assign({}, req, { id: 'ir-4', session_id: 's-4', status: 'expired' });
const c4host = new El('div'); body.appendChild(c4host);
check('an expired card says so', /expired unanswered/.test(M.attach(c4host, exp, { host: c4host }).innerHTML));
// a panel/Inbox card never captures the composer
const r5 = Object.assign({}, req, { id: 'ir-5', session_id: 's-5' });
const c5host = new El('div'); body.appendChild(c5host);
M.attach(c5host, r5, { host: c5host, via: 'rest' });
check('a card answered over REST (panel, Inbox) is keyed by the request, not the session', M.pendingFor('s-5') === null);
// phase 3: an auth question, remembered answers
const r6 = { id: 'ir-6', session_id: 's-6', status: 'pending', kind: 'auth', raised_by: 'code', profile_tag: 'MAIL', resume: 't6',
  items: [{ header: 'Connect', question: 'Connect your Google account so the agent can continue.', options: [{ value: 'connected', label: 'Connect Google' }], connector: { platform: 'google', server_id: 'uderia-google', display_name: 'Google' } }] };
const c6host = new El('div'); body.appendChild(c6host);
const card6 = M.attach(c6host, r6, { host: c6host });
check('an auth question offers Connect (the OAuth popup is the answer; nothing is typed) and no remember choice',
  card6.querySelector('.ni-connect[data-platform="google"]') && !card6.querySelector('input[type="radio"]'));
const r7 = Object.assign({}, req, { id: 'ir-7', session_id: 's-7', status: 'pending' });
const c7host = new El('div'); body.appendChild(c7host);
const card7 = M.attach(c7host, r7, { host: c7host });
check('a single choose offers to remember the answer: once · this session · always for this agent (never for a provide)',
  card7.querySelectorAll('input[name="ni-rem-ir-7"]').length === 3 && !card2.querySelector('input[type="radio"]'));
check('the binding carries the remember choice to the composer and to REST',
  /remember: decision === 'accept' \? \(remember \|\| null\) : null/.test(read('components/builtin/needs_input/renderer.js'))
  && /remember: remember \|\| undefined/.test(read('components/builtin/needs_input/renderer.js')));
check('the panel reads the scoreboard (asked · answered · declined · expired · median)', /\/api\/v1\/input-requests\/stats/.test(read('components/builtin/needs_input/renderer.js')));
check('the module exposes the panel and the composer hooks on window.NeedsInput',
  typeof window.NeedsInput.openNeedsInputPanel === 'function' && typeof window.NeedsInput.bindComposerText === 'function' && 'pendingAnswer' in window.NeedsInput);

// ── (2) the wiring ──────────────────────────────────────────────────────────────────
const R = read('components/builtin/needs_input/renderer.js');
const man = JSON.parse(read('components/builtin/needs_input/manifest.json'));
check('the renderer emits exactly what the manifest declares, and listens as it declares',
  JSON.stringify(man.signals.emits.slice().sort()) === JSON.stringify(['uderia:input-answered', 'uderia:input-required'])
  && man.signals.listens.includes('uderia:component-changed') && /UderiaBus\.onComponent\('needs_input'/.test(R));
check('theme variables only, monochrome glyphs, one delegated listener',
  !/text-purple|bg-gray-|text-gray-/.test(R) && /var\(--card-bg\)/.test(R) && /var\(--text-muted\)/.test(R) && (R.match(/document\.addEventListener\('click'/g) || []).length === 1);
const EH = read('static/js/eventHandlers.js');
check('the stream\'s final answer attaches the card', /eventData\.input_request && window\.NeedsInput\) window\.NeedsInput\.attach\(_finalBubble, eventData\.input_request\)/.test(EH));
check('the composer posts the binding from the card or the composer rule, and supersedes otherwise',
  /window\.NeedsInput\.pendingAnswer \|\| window\.NeedsInput\.bindComposerText\(state\.currentSessionId, cleanedMessage\)/.test(EH)
  && /window\.NeedsInput\.supersede\(state\.currentSessionId\)/.test(EH) && /input_answer: inputAnswer \|\| undefined/.test(EH));
const N = read('static/js/notifications.js');
check('a REST turn\'s completion attaches the card too', /input_request \} = data\.payload/.test(N) && /window\.NeedsInput\.attach\(DOM\.chatLog\.lastElementChild, input_request\)/.test(N));
const SM = read('static/js/handlers/sessionManagement.js');
check('a reload rebuilds the card from the turn record, with the session\'s pending binding',
  /wfTurn\.input_request/.test(SM) && /data\.pending_input_request/.test(SM));
const SS = read('static/js/handlers/statusSummary.js');
check('the status strip gains Waiting for you — a quiet reading, shown while connected and nothing works',
  /waiting:\s+'Waiting for you'/.test(SS) && /states\.includes\('connected'\) && _isWaiting\(\)/.test(SS)
  && /UderiaBus\.on\('uderia:input-required'/.test(SS) && /UderiaBus\.on\('uderia:input-answered'/.test(SS));
check('…and the check sits before the inherited SSE state, keeping the strip\'s own gate intact',
  SS.indexOf("_isWaiting()") < SS.indexOf("summary.className = 'connection-dot ' + (states.length"));
const CSS_ = read('static/css/main.css');
check('waiting is tinted with the posture pass token, never amber or red', /#status-summary\[data-verdict="waiting"\] \{[^}]*--trust-pass/.test(CSS_)
  && !/#status-summary\[data-verdict="waiting"\] \{[^}]*(--status-warning|--status-error)/.test(CSS_));
const T = read('templates/index.html');
check('the split panel markup exists as a structural .sp-panel with a close control',
  /<aside id="needs-input-split-panel" class="sp-panel"/.test(T) && /id="needs-input-split-close"/.test(T) && /id="needs-input-split-content"/.test(T));
check('the status strip script is re-versioned', /statusSummary\.js'\) \}\}\?v=1\.5/.test(T));
const W = read('static/js/handlers/workspacePanelHandler.js');
check('the Workspace lists this agent\'s questions (scoped by profile_id), never deletes or creates one, opens the panel',
  /needs_input: \{/.test(W) && /profile_id=\$\{encodeURIComponent\(profileId\)\}/.test(W.slice(W.indexOf('needs_input: {')))
  && /noDelete: true/.test(W.slice(W.indexOf('needs_input: {'), W.indexOf('database_navigator: {')))
  && /openNeedsInputPanel/.test(W));
const AP = read('static/js/handlers/agentPerformanceHandler.js');
check('the Inbox lists the pending questions, counts them in the badge, and attaches the component\'s cards over REST',
  /api\('\/input-requests\?status=pending'\)/.test(AP) && /\(\(questions && questions\.pending\) \|\| 0\)/.test(AP)
  && /m\.attach\(h, JSON\.parse\(h\.dataset\.question\), \{ host: h, via: 'rest' \}\)/.test(AP) && /tc-pass">\$\{questions\.pending\} waiting for you/.test(AP));
const CR = read('static/js/componentRenderers.js');
check('the renderer is registered (the builtin table — loaded dynamically, only where installed)', /needs_input: 'renderNeedsInput'/.test(CR));
const BUS = read('static/js/uderiaBus.js');
check('both events are registered on the bus with their fields', /'uderia:input-required':\s+\{ detail: \['session_id', 'request_id'\]/.test(BUS) && /'uderia:input-answered':\s+\{ detail: \['session_id', 'request_id', 'decision'\]/.test(BUS));

// ── (2b) a reloaded card reads its outcome from the store, never from the turn's snapshot ──
// (the browser pass found three settled questions rendered pending on reload, the composer
// bound to one, and the next message refused as "already accepted")
const SM2 = read('static/js/handlers/sessionManagement.js');
check('the reload merges the live record by id and closes a question the store no longer knows',
  /\(\(data\.input_requests && data\.input_requests\[ir\.id\]\) \|\| \{ status: 'superseded' \}\)/.test(SM2)
  && /window\.NeedsInput\.attach\(bubble, Object\.assign\(\{\}, ir, live\)\)/.test(SM2));
const RT = read('src/trusted_data_agent/api/routes.py');
check('the session load serves every question of the conversation in its live state, by id',
  /_ir_store\.list_for_user\(user_uuid, status="all", session_id=session_id, limit=200\)/.test(RT)
  && /response_data\["input_requests"\] = \{r\["id"\]: _ir_store\.public_view\(r\) for r in _all\}/.test(RT));

// ── (2c) on the stream the card rides the `input_required` notice ─────────────────────
// (the finish seam raises the question AFTER the final answer was forwarded, so the stream's
// final_answer event has no `input_request`; the browser pass saw the answer and no card)
const EH2 = read('static/js/eventHandlers.js');
check('the stream attaches the card from the input_required notice, session-guarded, under the last bubble',
  /eventData\.type === 'input_required'\) \{[\s\S]{0,900}q\.session_id === state\.currentSessionId\) && window\.NeedsInput && DOM\.chatLog\.lastElementChild\)[\s\S]{0,120}window\.NeedsInput\.attach\(DOM\.chatLog\.lastElementChild, q\)/.test(EH2));
const ES = read('src/trusted_data_agent/agent/execution_service.py');
check('…and the seam emits that notice on the stream with the session id',
  /await event_handler\(\{"type": "input_required",\s+"payload": \{\*\*_ir_view, "session_id": session_id\}\}, "notification"\)/.test(ES));

// ── (3) the agent editor owns `authorityConfig.inputPolicy` ─────────────────────────
// Every value the runtime accepts is offered (a stored mode the editor did not list would be
// displayed as the fallback and then persisted as it — the posture-ladder defect).
const PY = read('src/trusted_data_agent/agent/input_requests.py');
const pyVals = (key, re) => [...PY.matchAll(re)].map((m) => m[1]);
const CH = read('static/js/handlers/configurationHandler.js');
const editorSel = (id) => { const m = T.match(new RegExp(`<select id="${id}"[\\s\\S]*?</select>`)); return m ? [...m[0].matchAll(/<option value="([a-z]+)"/g)].map((x) => x[1]).sort() : null; };
check('the editor lists every interactive mode the runtime accepts', JSON.stringify(editorSel('profile-input-interactive')) === JSON.stringify(['ask', 'never'])
  && /in \("ask", "never"\)/.test(PY));
check('…every unattended mode', JSON.stringify(editorSel('profile-input-unattended')) === JSON.stringify(['fail', 'park']) && /in \("park", "fail"\)/.test(PY));
check('…every raiser posture', JSON.stringify(editorSel('profile-input-raisers')) === JSON.stringify(['enforce', 'observe']) && /RAISER_POSTURES = \("observe", "enforce"\)/.test(PY));
check('questions per turn is bounded as the runtime bounds it (1–8)', /id="profile-input-max" min="1" max="8"/.test(T) && /max\(1, min\(n, 8\)\)/.test(PY)
  && /mx >= 1 && mx <= 8/.test(CH));
check('the editor reads the stored policy with the full allowed sets, never a shorter ladder',
  /setSel\('#profile-input-interactive', ip\.interactive, \['ask', 'never'\], 'ask'\)/.test(CH)
  && /setSel\('#profile-input-unattended', ip\.unattended, \['park', 'fail'\], 'park'\)/.test(CH)
  && /setSel\('#profile-input-raisers', ip\.raisers, \['enforce', 'observe'\], 'enforce'\)/.test(CH));
check('the editor owns inputPolicy only when its controls were rendered, and writes only what differs from the defaults',
  /const ownsInput = !!modal\.querySelector\('#profile-input-interactive'\)/.test(CH)
  && /\.\.\.\(ownsInput \? \['inputPolicy'\] : \[\]\)/.test(CH)
  && /=== 'never'\) inputPolicy\.interactive = 'never'/.test(CH) && /=== 'observe'\) inputPolicy\.raisers = 'observe'/.test(CH));
check('standing answers round-trip as key = value lines', /\$\{k\} = \$\{typeof v === 'string' \? v : JSON\.stringify\(v\)\}/.test(CH)
  && /line\.match\(\/\^\\s\*\(\[\^=\]\+\?\)\\s\*=\\s\*\(\.\+\?\)\\s\*\$\/\)/.test(CH));
check('the section says what it is, and that weakening it is a downgrade', /id="profile-authority-input-policy"/.test(T)
  && /weakening it is a recorded downgrade/.test(T));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
