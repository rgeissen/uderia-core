// S6 — the Governed Tool Surface's surfaces, executed / structurally pinned:
//   (1) the Live Status `tool_surface` card: offered · found · held · denied chips, each carrying
//       its reason as the tooltip; a denied tool the model asked for is the named finding (warn);
//   (2) the wiring: both live dispatch sites + the reload branch + the assurance category, the
//       reload synthesis from the stamp (only when the trace lacks it), the shared title, the dial,
//       the session-guarded notification, the editor's Tool Surface select in the surface contract.
import fs from 'node:fs';
const read = (rel) => fs.readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

{
  const ui = read('static/js/ui.js');
  const fn = ui.match(/function _renderToolSurfaceDetails\(details\) \{[\s\S]*?\n\}\n/)[0];
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _mechLink = (k) => `<span data-mech="${k}">${k}</span>`;
  const _trustChip = (lvl, word) => `<span class="trust-chip tc-${lvl}">${word}</span>`;
  const _trustVal = (lvl) => `data-lvl="${lvl}"`;
  const render = new Function('escapeHtml', '_mechLink', '_trustChip', '_trustVal', fn + '; return _renderToolSurfaceDetails;')(escapeHtml, _mechLink, _trustChip, _trustVal);
  const d = { counts: { offered: 3, held: 2, denied: 1, widened: 1, total: 7 }, offered: ['base_readQuery', 'TDA_Charting'], held: ['dba_tableSpace'], denied: ['base_writeQuery'], widened: ['dba_sessionInfo'],
              asked_denied: [], reasons: { base_readQuery: 'backbone — discovery and execution', dba_tableSpace: 'held — findable with TDA_FindTools', base_writeQuery: 'tool is a write action', dba_sessionInfo: 'found by the model: session' },
              summary: 'offered 3 of 7 tools; 1 denied by authority; 1 found by the model' };
  const h = render(d);
  check('a governed turn reads pass + the split', /tc-pass/.test(h) && /offered 3 of 7/.test(h) && /1 denied by authority/.test(h));
  check('one chip per tool in its set, class per set', /ts-chip ts-offered[^>]*>base_readQuery/.test(h) && /ts-chip ts-held[^>]*>dba_tableSpace/.test(h) && /ts-chip ts-denied[^>]*>base_writeQuery/.test(h) && /ts-chip ts-widened[^>]*>dba_sessionInfo/.test(h));
  check('every chip carries its REASON as the tooltip (u-tip, never a native title)', /data-tip="backbone — discovery and execution"/.test(h) && /data-tip="tool is a write action"/.test(h) && !/ title=/.test(h) && /u-tip/.test(h));
  check('no asked-denied → no finding row', !/Asked for, denied/.test(h));
  const h2 = render({ ...d, asked_denied: ['base_writeQuery <x>'] });
  check('a denied tool the model asked for → warn + the named finding, escaped', /tc-warn/.test(h2) && /asked for a denied tool/.test(h2) && /Asked for, denied/.test(h2) && /base_writeQuery &lt;x&gt;/.test(h2) && /never offered, never found/.test(h2));
  check('the card routes to the mechanism (one glossary channel)', /data-mech="tool_surface"/.test(h));
  const h3 = render({ counts: { offered: 2, total: 2 }, offered: ['a', 'b'], summary: 's' });
  check('empty sets render no rows (no "Held:" over nothing)', !/Held</.test(h3) && !/Denied by authority/.test(h3) && /Offered/.test(h3));

  // Oct 2026 — the card says what DECIDED (words · meaning · the finder), never a silent ranker
  const dR = { ...d, mode: 'governed', ranker: { lexical: true, semantic: true, semantic_model: 'all-MiniLM-L6-v2', semantic_state: 'ready', finder: true, semantic_added: ['dba_tableSpace', 'qlty_missingValues'] } };
  const hR = render(dR);
  check('ranked by: words · meaning with the model and the count of additions', /Ranked by/.test(hR) && /words · meaning \(all-MiniLM-L6-v2: 2 added\)/.test(hR), hR);
  const hW = render({ ...dR, ranker: { ...dR.ranker, semantic: false, semantic_state: 'warming', semantic_added: [] } });
  check('ranked by: a warming model is said in words — never a blank', /meaning: warming up — words only this turn/.test(hW), hW);
  const hU = render({ ...dR, ranker: { ...dR.ranker, semantic: false, semantic_state: 'unavailable', semantic_added: [] } });
  check('ranked by: an unavailable model is said', /meaning: unavailable — words only/.test(hU), hU);
  const hF = render({ ...dR, ranker: { ...dR.ranker, finder: false } });
  check('no Tool Finder on the agent → the card says nothing is held, and why', /no Tool Finder on this agent — nothing held/.test(hF), hF);
  check('mode all / no ranker → no Ranked-by row (the freeze)', !/Ranked by/.test(render({ ...d, ranker: dR.ranker, mode: 'all' })) && !/Ranked by/.test(h));
  const hX = render({ ...dR, ranker: { ...dR.ranker, semantic_model: '<m>' } });
  check('the model name is escaped', /&lt;m&gt;/.test(hX) && !/<m>/.test(hX));
}

{
  const ui = read('static/js/ui.js'); const eh = read('static/js/eventHandlers.js'); const nt = read('static/js/notifications.js');
  const _catChain = (ui.match(/[^\n]*\) return 'assurance';/) || [''])[0];
  check('wired at both live dispatch sites, the reload branch, and classified assurance (both chains)', (ui.match(/case 'tool_surface'/g) || []).length === 2 && /type === "tool_surface"/.test(ui) && /type === 'tool_surface'/.test(_catChain) && /type === 'tool_surface' \|\|/.test(ui.split("return 'assurance'")[1] || ''));
  check('the live card also moves the dial (BOUNDED)', /window\.AssuranceDial\.note\(_adSession\(\), 'tool_surface', details\)/.test(ui) && /tool_surface: 'bounded'/.test(read('static/js/handlers/assuranceDial.js')));
  check('reload: synthesized from turnData.tool_surface only when the trace lacks the step', /type: 'tool_surface'/.test(eh) && /s\.action\.metadata\?\.type === 'tool_surface'/.test(eh) && /turnData\.tool_surface\.counts/.test(eh));
  check('the shared assurance title helper covers tool_surface (all four classes)', /if \(eventType === 'tool_surface'\) return 'Tool Surface';/.test(eh));
  check('notification: session-guarded dial update, never a card of its own', /case 'tool_surface'[\s\S]{0,500}p\.session_id === state\.currentSessionId[\s\S]{0,200}AssuranceDial\.note/.test(nt));
  const ch = read('static/js/handlers/configurationHandler.js'); const idx = read('templates/index.html');
  check('editor: the Tool Surface select beside the MCP server, gated by the surface contract on usesMcp', /id="profile-modal-tool-surface"/.test(idx) && /toolSurfaceField:\s*\{ sel: '#profile-field-tool-surface',\s*when: c => c\.usesMcp \}/.test(ch));
  check('editor: load reads the stored mode (absent = all); save writes {mode}', /profile\.toolSurface\.mode === 'governed'\) \? 'governed' : 'all'/.test(ch) && /profileData\.toolSurface = \{ mode: v === 'governed' \? 'governed' : 'all' \}/.test(ch));
  check('editor: the tooltip says it changes what is SHOWN, not what is permitted', /Changes what is shown, never what is permitted/.test(idx));
  check('editor (Oct 2026): "Tools in context" — All enabled tools / Relevant per turn; no deployment switch named', /Tools in context/.test(idx) && /<option value="all">All enabled tools<\/option>/.test(idx) && /<option value="governed">Relevant per turn<\/option>/.test(idx) && !/deployment master switch/.test(idx.split('id="profile-field-tool-surface"')[1].slice(0, 2500)));
  check('editor (Oct 2026): a NEW agent starts on Relevant per turn; a stored agent keeps its own', /const storedMode = profile\s*\n\s*\? \(\(profile\.toolSurface && profile\.toolSurface\.mode === 'governed'\) \? 'governed' : 'all'\)\s*\n\s*: 'governed';/.test(ch));
  const css = read('static/css/main.css');
  check('chip CSS on theme tokens, no motion', /\.ts-chip\.ts-denied \{[^}]*var\(--trust-danger/.test(css) && !/\.ts-chip[^}]*(animation|transition)/.test(css));
}

{
  // review fixes: keyboard-reachable chips; found = offered (solid), dashed belongs to the held set
  const ui2 = read('static/js/ui.js'); const css2 = read('static/css/main.css');
  check('every surface chip is focusable and names its reason to assistive tech (tabindex + aria-label), the reason still on hover', /class="ts-chip \$\{cls\} u-tip" tabindex="0" data-tip=/.test(ui2) && /aria-label="\$\{escapeHtml\(String\(n\)\)\}: \$\{escapeHtml\(String\(reasons\[n\] \|\| ''\)\)\}"/.test(ui2));
  check('a focused chip shows a focus ring on the brand token', /\.ts-chip:focus-visible \{[^}]*var\(--teradata-orange/.test(css2));
  check('a WIDENED chip is solid like an offered one; only the HELD set is dashed', !/\.ts-chip\.ts-widened \{[^}]*dashed/.test(css2) && /\.ts-chip\.ts-held \{[^}]*dashed/.test(css2));
}
if (fail) { console.log(`FAILED — ${fail} of ${pass + fail}`); process.exit(1); }
console.log(`ALL PASS — ${pass} assertions (tool surface UI)`);
