// S3 — the figure-lineage dialog script, executed under a DOM stub: the click on a
// receipted mark opens a real dialog (role, aria-modal, labelled), reads the turn's compute
// stamp ONCE per (session, turn), renders the receipt + rows, formats numbers exactly as the
// answer does (en-US), traps focus, closes on Escape and returns focus to the figure; a
// keyboard Enter on the focused mark opens it; with no mark on the page nothing runs.
import fs from 'node:fs';
const src = fs.readFileSync(new URL('../static/js/handlers/figureLineage.js', import.meta.url), 'utf8');
let pass = 0, fail = 0;
const check = (n, c, d = '') => { if (c) pass++; else { fail++; console.log('  FAIL', n, d); } };

// --- a tiny DOM stub: enough surface for the script's real code paths ---
const listeners = {};
const els = [];
function mkEl(tag) {
  const el = { tag, children: [], dataset: {}, className: '', attrs: {}, _html: '', parent: null, listeners: {},
    get classList() { const self = this; return { contains: (c) => (self.className || '').split(' ').includes(c) }; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] ?? null; },
    addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.removed = true; },
    focus() { document.activeElement = this; },
    matches(sel) { return sel.split(',').some(s => s.trim().split('.').slice(1).every(cls => (this.className || '').split(' ').includes(cls)) && s.trim().startsWith(this.tag)); },
    closest(sel) { let e = this; while (e) { if (sel.split(',').some(s => e.matches(s.trim()) || (s.trim().startsWith('[') && e.dataset.sessionId))) return e; e = e.parent; } return null; },
    contains(x) { let e = x; while (e) { if (e === this) return true; e = e.parent; } return false; },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      // resolve against the innerHTML we rendered: minimal class/id matching over parsed pseudo-nodes
      const out = []; const want = sel.split(',').map(s => s.trim());
      const walk = (n) => { for (const c of n.children) { if (want.some(w => (w.startsWith('#') && c.attrs.id === w.slice(1)) || (w.startsWith('.') && (c.className || '').split(' ').includes(w.slice(1))) || w === c.tag || (w.includes('[') && w.startsWith(c.tag)))) out.push(c); walk(c); } };
      walk(this); return out;
    },
    get innerHTML() { return this._html; },
    set innerHTML(h) { this._html = h; this.children = parse(h, this); },
    get textContent() { return this._text ?? this._html.replace(/<[^>]+>/g, ''); },
    set textContent(t) { this._text = t; },
  };
  els.push(el); return el;
}
// a deliberately small HTML parser: tags with class/id/role/aria attrs, nested
function parse(html, parent) {
  const kids = []; const stack = [{ children: kids }];
  const re = /<(\/?)([a-z0-9]+)([^>]*)>|([^<]+)/gi; let m;
  while ((m = re.exec(html))) {
    if (m[4] !== undefined) { const top = stack[stack.length - 1]; if (top !== stack[0] && top.tag) top._text = (top._text || '') + m[4]; continue; }
    if (m[1]) { stack.pop(); continue; }
    const el = mkEl(m[2]); const attrs = m[3] || '';
    for (const a of attrs.matchAll(/([a-zA-Z-]+)="([^"]*)"/g)) { el.attrs[a[1]] = a[2]; if (a[1] === 'class') el.className = a[2]; if (a[1].startsWith('data-')) el.dataset[a[1].slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = a[2]; }
    const top = stack[stack.length - 1]; el.parent = top.tag ? top : parent; (top.children || kids).push(el);
    if (!/^(path|input|br)$/.test(m[2]) && !attrs.endsWith("/")) stack.push(el);
  }
  return kids;
}
const body = mkEl('body'); const document = { body, activeElement: null, listeners: {},
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }, removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter(x => x !== f); },
  createElement: mkEl, querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s) };
let fetches = 0;
const window = { __currentSessionId: 'sid-1', state: {} };
const localStorage = { getItem: () => 'tok' };
const STAMP = { compute: { receipts: [{ op: 'sum', column: 'Price', row_count: 100, row_count_used: 100, statements: [1], value: 90512.28, receipt_hash: 'abc', sample_rows: [{ Price: 199, ProductName: 'Bench' }, { Price: 1999.5, ProductName: 'Cycle' }, { Price: 4599, ProductName: 'Treadmill' }] }], statements: [{ n: 1, statement: 'SELECT Price FROM p', row_count: 100 }] } };
const fetch = async (url) => { fetches++; return { ok: true, json: async () => STAMP }; };
const fn = new Function('document', 'window', 'localStorage', 'fetch', src + '\nreturn window;');
fn(document, window, localStorage, fetch);
check('the script registers ONE delegated click + one keydown listener (no per-mark wiring)', (document.listeners.click || []).length === 1 && (document.listeners.keydown || []).length === 1);
check('with no mark on the page nothing runs', fetches === 0 && !body.querySelector('.fl-dialog'));

// a chat bubble with a receipted mark
const log = mkEl('div'); log.attrs.id = 'chat-log'; body.appendChild(log);
const bubble = mkEl('div'); bubble.className = 'message-bubble'; log.appendChild(bubble);
const mark = mkEl('mark'); mark.className = 'figure-receipted u-tip'; mark.dataset = { tip: 'Computed: SUM(Price) · 100 rows · statement 1 — click to see the rows', receipt: '0', turnId: '3' }; mark.textContent = '90,512.28'; bubble.appendChild(mark);
const click = document.listeners.click[0];
await click({ target: mark, preventDefault() {} });
await new Promise(r => setTimeout(r, 10));
const dlg = body.querySelector('.fl-dialog');
check('a real dialog: role=dialog, aria-modal, labelled by its heading', dlg && dlg.attrs.role === 'dialog' && dlg.attrs['aria-modal'] === 'true' && dlg.attrs['aria-labelledby'] === 'fl-title');
check('the compute stamp is read once from the turn-details endpoint', fetches === 1);
const bodyEl = dlg && dlg.querySelector('.fl-body');
check('the receipt is rendered (operation, rows used, statement, value) and the rows follow', bodyEl && /SUM\(Price\)/.test(bodyEl.innerHTML) && /100 of 100/.test(bodyEl.innerHTML) && /<table/.test(bodyEl.innerHTML) && /Bench/.test(bodyEl.innerHTML));
check("numbers are formatted exactly as the answer's figures (en-US), never a locale variant", /90,512\.28/.test(bodyEl.innerHTML) && !/90\.512,28/.test(bodyEl.innerHTML) && /1,999\.5/.test(bodyEl.innerHTML) && /4,599/.test(bodyEl.innerHTML) && !/4\.599/.test(bodyEl.innerHTML));
check('focus moved into the dialog (the close control)', document.activeElement && document.activeElement.className === 'fl-close');
// Escape closes and returns focus to the figure
const keyHandler = (document.listeners.keydown || []).find(f => f.name === 'onKey') || (document.listeners.keydown || [])[1];
keyHandler({ key: 'Escape', preventDefault() {} });
check('Escape closes the dialog and returns focus to the figure', !body.querySelector('.fl-dialog') && document.activeElement === mark);
// a second open reuses the cache
await click({ target: mark, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
check('a second open re-uses the cached stamp (one fetch total)', fetches === 1 && !!body.querySelector('.fl-dialog'));
keyHandler({ key: 'Escape', preventDefault() {} });
// keyboard Enter on the focused mark opens it
const kb = (document.listeners.keydown || [])[0];
await kb({ key: 'Enter', target: mark, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
check('Enter on a focused figure opens the dialog', !!body.querySelector('.fl-dialog'));
keyHandler({ key: 'Escape', preventDefault() {} });
// a quoted figure names row + statement
const q = mkEl('mark'); q.className = 'figure-quoted u-tip'; q.dataset = { tip: 'Quoted from the data: row 2 of statement 1 — click to see the rows', statement: '1', row: '2', turnId: '3' }; q.textContent = '1,999.5'; bubble.appendChild(q);
await click({ target: q, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
const qb = body.querySelector('.fl-dialog').querySelector('.fl-body');
check('a quoted figure: the dialog names row and statement and highlights the row', /row 2 of statement 1/.test(qb.innerHTML) && /fl-hit/.test(qb.innerHTML) && /SELECT Price FROM p/.test(qb.innerHTML));
keyHandler({ key: 'Escape', preventDefault() {} });
// a LITERAL the agent wrote into the statement (Sep 2026: "offence code '120'"): the quoted
// family's mark with data-literal — the dialog leads with the statement it was a filter in
const lit = mkEl('mark'); lit.className = 'figure-quoted u-tip'; lit.dataset = { tip: "From the statement: used as a filter in statement 1 — click to see the statement and its rows", statement: '1', row: '0', literal: 'filter', turnId: '3' }; lit.textContent = '120'; bubble.appendChild(lit);
await click({ target: lit, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
const lb = body.querySelector('.fl-dialog').querySelector('.fl-body');
check('a literal: the dialog says it was a filter in the statement (never "row 0"), shows the statement', /Used as a filter in statement 1/.test(lb.innerHTML) && !/row 0/.test(lb.innerHTML) && /SELECT Price FROM p/.test(lb.innerHTML));
keyHandler({ key: 'Escape', preventDefault() {} });
const bnd = mkEl('mark'); bnd.className = 'figure-quoted u-tip'; bnd.dataset = { tip: 'From the statement: the bound of statement 1', statement: '1', row: '0', literal: 'bound', turnId: '3' }; bnd.textContent = '100'; bubble.appendChild(bnd);
await click({ target: bnd, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
check('a bound says so', /Used as the bound of statement 1/.test(body.querySelector('.fl-dialog').querySelector('.fl-body').innerHTML));
keyHandler({ key: 'Escape', preventDefault() {} });
// a figure DERIVED from a column (Sep 2026: "179,193 robberies" = the sum of twelve monthly
// rows): the quoted family's mark with data-derived — the dialog names the aggregate, the
// column and the statement, never "row 0" and never a highlighted row
const der = mkEl('mark'); der.className = 'figure-quoted u-tip'; der.dataset = { tip: 'Computed from the data: the sum of Price over the 100 rows of statement 1', statement: '1', row: '0', derived: 'sum', column: 'Price', turnId: '3' }; der.textContent = '90,512.28'; bubble.appendChild(der);
await click({ target: der, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
const db = body.querySelector('.fl-dialog').querySelector('.fl-body');
check('a derived figure: the dialog names the aggregate, the column and the statement', /Computed from the data: the sum of Price over 100 rows of statement 1/.test(db.innerHTML) && /SELECT Price FROM p/.test(db.innerHTML) && !/row 0/.test(db.innerHTML) && !/fl-hit/.test(db.innerHTML));
check('a derived figure never reads as a filter', !/Used as a filter/.test(db.innerHTML));
keyHandler({ key: 'Escape', preventDefault() {} });
// a DECLARED code (from the knowledge graph) has no rows to open: a note, not a control
const dec = mkEl('mark'); dec.className = 'figure-declared u-tip'; bubble.appendChild(dec);
await click({ target: dec, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
check('a declared-code mark opens nothing (its lineage is the ontology, named on hover)', !body.querySelector('.fl-dialog'));
check('the declared mark has its own theme-token style in the quoted family', /mark\.figure-declared\s*\{[^}]*var\(--text-muted/.test(fs.readFileSync(new URL('../static/css/trust.css', import.meta.url), 'utf8')));
// an unreceipted mark is not a control
const u = mkEl('mark'); u.className = 'figure-unreceipted u-tip'; bubble.appendChild(u);
await click({ target: u, preventDefault() {} }); await new Promise(r => setTimeout(r, 10));
check('an unreceipted mark opens nothing (it is a note, not a control)', !body.querySelector('.fl-dialog'));
check('the marks CSS is theme-token based and the unreceipted mark is never red', (() => { const css = fs.readFileSync(new URL('../static/css/trust.css', import.meta.url), 'utf8'); const seg = css.slice(css.indexOf('mark.figure-receipted'), css.indexOf('.fl-empty')); return /var\(--trust-pass/.test(seg) && /var\(--text-muted/.test(seg) && !/--trust-danger/.test(seg); })());

// --- the compute card's own action: same dialog, receipt found by hash, turn read from the message ---
check('the card\'s "View the rows" button is dispatched BEFORE the mark selector and by its data-act', /closest\('\.compute-open-btn\[data-act="rows"\]'\)/.test(src) && /closest\('\.ans-ev, \.compute-card'\)/.test(src) && src.indexOf('compute-open-btn[data-act="rows"]') < src.indexOf('e.target.closest(SEL)'));
check('the turn is read from a receipted/quoted mark of the same answer, else the user bubble the answer follows (its avatar carries data-turn-id)', /function turnOfCard\(card\)/.test(src) && /mark\.figure-receipted\[data-turn-id\], mark\.figure-quoted\[data-turn-id\]/.test(src) && /previousElementSibling/.test(src) && /\[data-turn-id\]'\)/.test(src));
// the PROPERTY, not the expression: the card path renders the receipt through the SAME receiptRows
// and the SAME table over rc.sample_rows as the mark path (PROP-006 put a combine's second hop
// between the two; pinning the adjacent call text broke on that while the property held)
{ const _cardBody = (src.match(/body\.innerHTML = rc\s*\n\s*\?\s*`[^\n]*`/) || [''])[0];
  check('the receipt is found on the persisted stamp by its hash and rendered through the SAME receiptRows + table', /r\.receipt_hash === hash/.test(src) && /receiptRows\(rc\)/.test(_cardBody) && /table\(rc\.sample_rows\)/.test(_cardBody)); }
check('an absent record is said in words, never a blank dialog', /No receipt with this hash is on the persisted turn/.test(src) && /compute record is not available/.test(src));

// --- the Live Status card (ui.js) executed, not grepped: the SHIPPED function, stub helpers ---
{
  const { readFileSync } = await import('node:fs');
  const ui = readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');
  const start = ui.indexOf('function _renderComputeLineageDetails(details)');
  let i = ui.indexOf('{', start), depth = 0, end = i;
  for (; end < ui.length; end++) { if (ui[end] === '{') depth++; else if (ui[end] === '}' && --depth === 0) break; }
  // the card calls `_computeSourcesRows` (PROP-006) — extract it too, so the harness runs the
  // shipped pair rather than a stub that would pass whatever the helper does
  const hStart = ui.indexOf('function _computeSourcesRows(src)');
  let hi = ui.indexOf('{', hStart), hd = 0, he = hi;
  for (; he < ui.length; he++) { if (ui[he] === '{') hd++; else if (ui[he] === '}' && --hd === 0) break; }
  const fnSrc = ui.slice(hStart, he + 1) + '\n' + ui.slice(start, end + 1);
  const render = new Function('_mechLink', '_trustChip', '_trustVal', 'escapeHtml', fnSrc + '\nreturn _renderComputeLineageDetails;')(
    () => 'Number lineage', (lvl, w) => `<chip ${lvl}>${w}</chip>`, (lvl) => `data-state="${lvl}"`,
    (x) => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;'));
  const ok = render({ decision: 'pass', figures: 2, receipted: [], quoted: [{ figure: '16,569', lineage: 'row 7 of statement 1' }],
    literals: [], declared: [], derived: [{ figure: '179,193', lineage: 'the sum of robbery_count over the 12 rows of statement 1' }], unreceipted: [], misderived: [] });
  check('card: a derived figure is counted and listed with its lineage', /1 derived from a column/.test(ok) && /Derived<\/div>/.test(ok) && /179,193 <span[^>]*>&larr; the sum of robbery_count over the 12 rows of statement 1/.test(ok) && /every figure traced/.test(ok));
  const bad = render({ decision: 'note', figures: 1, receipted: [], quoted: [], literals: [], declared: [], derived: [], unreceipted: ['180,193'],
    misderived: [{ figure: '180,193', lineage: 'the sum of robbery_count over the 12 rows of statement 1', value: 179193 }], disclosure_appended: true });
  check('card: a near miss names the value the rows give, and stays unreceipted', /Close, not equal/.test(bad) && /is 179,193/.test(bad) && /1 of 1 figure\(s\) unreceipted/.test(bad) && /Unreceipted<\/div>/.test(bad));
  const legacy = render({ decision: 'pass', figures: 1, receipted: [], quoted: [{ figure: '16,569', lineage: 'row 7 of statement 1' }], literals: [], declared: [], unreceipted: [] });
  check('card: an event from before the new classes renders unchanged (no derived line, no crash)', !/derived/i.test(legacy) && !/Close, not equal/.test(legacy) && /1 quoted from the data/.test(legacy));
}

console.log(fail ? `FAILED — ${fail} of ${pass + fail}` : `ALL PASS — ${pass + fail} assertions (figure lineage dialog)`);
process.exit(fail ? 1 : 0);
