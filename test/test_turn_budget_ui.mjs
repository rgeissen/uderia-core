// S5 — the Visible Budget's surfaces, executed / structurally pinned:
//   (1) the header budget bar: renders ONLY with an armed budget, one real progressbar per unit
//       with aria-valuetext in WORDS, colour = attention (quiet / nearly used / exhausted),
//       the ending in words (gracefully / hard limit), session-guarded, reset on a new turn;
//   (2) the Live Status `turn_budget` card: graceful vs killed, the skipped phases, the refusals;
//   (3) the reload wiring synthesizes the step + restores the bar from the persisted stamp.
import fs from 'node:fs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// ── (1) the bar, executed ─────────────────────────────────────────────────────────────────
{
  const src = read('static/js/handlers/budgetBar.js');
  const host = { id: 'turn-budget-bar', hidden: true, innerHTML: '' };
  const document = { getElementById: (id) => (id === 'turn-budget-bar' ? host : null) };
  const window = { state: { currentSessionId: 'S1' } };
  let intervals = 0; const setInterval = (f, ms) => { intervals++; return intervals; }; const clearInterval = () => {};
  new Function('document', 'window', 'setInterval', 'clearInterval', src)(document, window, setInterval, clearInterval);
  const B = window.BudgetBar;
  check('exposes update/reset/render', B && typeof B.update === 'function' && typeof B.reset === 'function');
  B.render();
  check('nothing armed → hidden and empty', host.hidden === true && host.innerHTML === '');
  const st = (steps, total, extra = {}) => ({ session_id: 'S1', units: [{ unit: 'steps', used: steps, total, left: total - steps, share: steps / total }, { unit: 'tokens', used: 3000, total: 20000, left: 17000, share: 0.15 }], exhausted: null, graceful: false, killed: null, refusals: 0, started_at: 0, ...extra });
  B.update(st(1, 4));
  check('an armed budget shows the bar', host.hidden === false);
  check('one progressbar per unit', (host.innerHTML.match(/role="progressbar"/g) || []).length === 2);
  check('aria-valuetext in WORDS, never a bare percentage', /aria-valuetext="1 of 4 steps used"/.test(host.innerHTML) && /aria-valuetext="3\.0k of 20k tokens used"/.test(host.innerHTML) && !/aria-valuetext="\d+%"/.test(host.innerHTML));
  check('quiet under the near share', /tbb-ok/.test(host.innerHTML) && !/tbb-near|tbb-exhausted/.test(host.innerHTML));
  check('the fill width is the share', /width:25%/.test(host.innerHTML));
  check('no ending line while within budget', !/tbb-ending/.test(host.innerHTML));
  B.update(st(3.4, 4));
  check('≥80% reads nearly used (amber), and says so in words', /tbb-near/.test(host.innerHTML) && /steps nearly used: 3 of 4 steps/.test(host.innerHTML));
  B.update(st(4, 4, { exhausted: 'steps', graceful: true, refusals: 1 }));
  check('exhausted → red segment + the words "used up"', /tbb-exhausted/.test(host.innerHTML) && /steps budget used up: 4 of 4 steps/.test(host.innerHTML));
  check('the ending says gracefully, as a status', /tbb-ending tbb-graceful/.test(host.innerHTML) && /finished gracefully/.test(host.innerHTML) && /role="status"/.test(host.innerHTML));
  B.update(st(4, 4, { exhausted: 'steps', graceful: false, killed: 'cost_cap' }));
  check('a kill says hard limit, in its own class', /tbb-ending tbb-killed/.test(host.innerHTML) && /stopped by a hard limit/.test(host.innerHTML));
  B.update(st(2, 4, { session_id: 'OTHER' }));
  check('session-guarded: another session\'s notice does not repaint the viewed bar', /tbb-killed/.test(host.innerHTML));
  B.reset();
  check('reset hides the bar', host.hidden === true && host.innerHTML === '');
  B.update({ session_id: 'S1', units: [{ unit: 'seconds', used: 2, total: 30, left: 28, share: 2 / 30 }], exhausted: null, started_at: Date.now() / 1000 - 2 });
  check('a running seconds unit ticks on the viewer clock (an interval is armed)', intervals >= 1 && /aria-label="seconds budget"/.test(host.innerHTML));
  const s5css = read('static/css/main.css').split('/* ── S5 the Visible Budget')[1].split('.provider-health-strip')[0];
  check('bar tokens: theme tokens only, no motion', /var\(--trust-pass/.test(s5css) && /var\(--trust-warn/.test(s5css) && /var\(--trust-danger/.test(s5css) && !/animation|transition/.test(s5css) && !/#[0-9a-f]{6}\s*;/i.test(s5css));
  check('host in the Live Status header, hidden by default', /id="turn-budget-bar"[^>]*hidden/.test(read('templates/index.html')));
  check('every segment is focusable with the words as its tooltip (keyboard reach)', /tabindex="0"/.test(src) && /data-tip="\$\{esc\(words\)\}"/.test(src));
}

// ── (2) the Live Status card, executed ────────────────────────────────────────────────────
{
  const ui = read('static/js/ui.js');
  const fn = ui.match(/function _renderTurnBudgetDetails\(details\) \{[\s\S]*?\n\}\n/)[0];
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _mechLink = (k) => `<span data-mech="${k}">${k}</span>`;
  const _trustChip = (lvl, word) => `<span class="trust-chip tc-${lvl}">${word}</span>`;
  const render = new Function('escapeHtml', '_mechLink', '_trustChip', fn + '; return _renderTurnBudgetDetails;')(escapeHtml, _mechLink, _trustChip);
  const g = render({ exhausted: 'steps', killed: null, refusals: 2, units: [{ unit: 'steps', used: 4, total: 4 }], summary: 'budget exhausted (steps) — stopped gracefully; 4 of 4 steps used' });
  check('graceful reads warn + "finished gracefully" + the refusals', /tc-warn/.test(g) && /steps used up · finished gracefully/.test(g) && /2 further tool calls/.test(g) && /4 of 4 steps/.test(g));
  const k = render({ exhausted: 'tokens', killed: 'cost_cap', units: [] });
  check('a kill reads danger + names the hard limit', /tc-danger/.test(k) && /stopped by a hard limit \(cost_cap\)/.test(k));
  const s = render({ exhausted: 'steps', skipped_phases: [2, 3], goals: ['get rows', 'join'], summary: 'x <b>' });
  check('the Optimize skip names the phases and goals, escapes the summary', /phases 2, 3/.test(s) && /get rows · join/.test(s) && /x &lt;b&gt;/.test(s));
  check('the card routes to the mechanism (one glossary channel)', /data-mech="turn_budget"/.test(g));
  // the PROPERTY (classified assurance + a live-status-relevant type), never the chain literal —
  // a later step type joining the same chain must not break this gate (the S4-gate lesson).
  const _catChain = (ui.match(/[^\n]*\) return 'assurance';/) || [''])[0];
  check('wired at both live dispatch sites, the reload branch, and classified assurance', (ui.match(/case 'turn_budget'/g) || []).length === 2 && /type === "turn_budget"/.test(ui) && /type === 'turn_budget'/.test(_catChain) && /type === 'turn_budget' \|\|/.test(ui.split("return 'assurance'")[1] || ''));
}

// ── (3) the wiring: one shape live + reload ───────────────────────────────────────────────
{
  const eh = read('static/js/eventHandlers.js'); const nt = read('static/js/notifications.js');
  check('live: the turn_budget notice drives the bar', /case 'turn_budget'[\s\S]{0,400}window\.BudgetBar\.update\(data\.payload/.test(nt));
  check('a new turn resets the bar before the first live notice', /dialBegun = true;[\s\S]{0,300}window\.BudgetBar\.reset\(\)/.test(eh));
  check('reload: the bar is restored from turnData.budget, or reset when the turn had none', /window\.BudgetBar\.update\(turnData\.budget/.test(eh) && /else if \(window\.BudgetBar\.reset\)/.test(eh));
  check('reload: an exhausted turn synthesizes the turn_budget step with the finish line', /type: 'turn_budget'/.test(eh) && /summary: b\.finish/.test(eh));
  check('reload: a killed turn is titled as stopped, never gracefully', /b\.killed \? 'Budget Exhausted — Turn Stopped' : 'Budget Exhausted — Finished Gracefully'/.test(eh));
  check('the shared assurance title helper covers turn_budget (all four classes)', /eventType === 'turn_budget'/.test(eh));
  check('the dial lights BOUNDED', /turn_budget: 'bounded'/.test(read('static/js/handlers/assuranceDial.js')));
  const ch = read('static/js/handlers/configurationHandler.js');
  check('editor: blank saves {} and a positive integer per unit', /profileData\.turnBudget = tb/.test(ch) && /Number\.isFinite\(n\) && n > 0\) tb\[unit\] = Math\.floor\(n\)/.test(ch));
  check('editor: populate reads profile.turnBudget per unit', /profile-turn-budget-\$\{unit\}/.test(ch) && /profile\.turnBudget/.test(ch));
  check('the Continue chip has its own monochrome icon', /continue: '<svg/.test(read('static/js/handlers/nextStepHandler.js')));
}

if (fail) { console.log(`FAILED — ${fail} of ${pass + fail}`); process.exit(1); }
console.log(`ALL PASS — ${pass} assertions (turn budget UI)`);
