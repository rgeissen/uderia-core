#!/usr/bin/env node
/**
 * Split-panel identity is STRUCTURAL — one rule, three consumers.
 *
 * polish.css frames and rings every `#chat-canvas-split > .sp-panel`; the resizer
 * builds handles between them; panelFocus hands the accent ring to the one you touch.
 * The resizer and panelFocus each kept a hand-maintained id LIST of the panels — and
 * the Database Navigator was added to one list and not the other, so it resized but
 * never took the focus ring (its manifest accent reached the header icon and nothing
 * else). This gate executes both shipped IIFEs under a DOM stub with an `<aside>` that
 * NO list ever named and asserts it is a panel to both, by structure alone.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

// ── minimal DOM: a split row with a chat container, the known asides, and a NEW one ──
class El {
    constructor(id, cls = '', display = '') {
        this.id = id; this._cls = new Set(cls.split(/\s+/).filter(Boolean)); this.children = []; this.parentNode = null;
        this.style = { display, width: '', flex: '', maxWidth: '', minWidth: '' }; this._listeners = {};
        this.classList = { add: (...c) => c.forEach(x => this._cls.add(x)), remove: (...c) => c.forEach(x => this._cls.delete(x)),
            contains: c => this._cls.has(c), toggle: (c, on) => { on ? this._cls.add(c) : this._cls.delete(c); } };
    }
    get className() { return [...this._cls].join(' '); } set className(v) { this._cls = new Set(v.split(/\s+/).filter(Boolean)); }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    insertBefore(c, ref) { c.parentNode = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? this.children.length : i, 0, c); return c; }
    querySelectorAll(sel) {
        if (sel === ':scope > .sp-panel') return this.children.filter(c => c._cls.has('sp-panel'));
        if (sel.startsWith('.')) { const k = sel.slice(1); const out = []; const walk = n => n.children.forEach(c => { if (c._cls.has(k)) out.push(c); walk(c); }); walk(this); return out; }
        return [];
    }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    closest(sel) {
        // the modules ask two things: 'am I in a resize handle?' and 'which panel am I in?'
        if (sel === '.split-resize-handle') { let n = this; while (n) { if (n._cls.has('split-resize-handle')) return n; n = n.parentNode; } return null; }
        let n = this; while (n) { if (n._cls.has('sp-panel') && n.parentNode && n.parentNode.id === 'chat-canvas-split') return n; n = n.parentNode; } return null;
    }
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); }
    setAttribute() {} getAttribute() { return null; }
    compareDocumentPosition(o) { const all = []; const walk = n => n.children.forEach(c => { all.push(c); walk(c); }); walk(root); return all.indexOf(o) > all.indexOf(this) ? 4 : 2; }
}
const root = new El('root');
const split = root.appendChild(new El('chat-canvas-split'));
const chat = split.appendChild(new El('chat-container'));
const header = root.appendChild(new El('conversation-header'));
const known = ['canvas-split-panel', 'kg-split-panel', 'dashboard-split-panel'].map(id => split.appendChild(new El(id, 'sp-panel', 'none')));
// the panel NO list names — the property under test
const novel = split.appendChild(new El('zzz-future-split-panel', 'sp-panel', 'none'));
const lookup = {}; const reg = n => { lookup[n.id] = n; n.children.forEach(reg); }; reg(root);
const document = {
    readyState: 'complete',
    getElementById: id => lookup[id] || null,
    createElement: () => { const e = new El(''); return e; },
    head: new El('head'), body: new El('body'),
    querySelectorAll: sel => root.querySelectorAll(sel),
    addEventListener() {},
};
const localStorage = { getItem: () => null, setItem() {} };
let observed = [];
class MutationObserver { constructor() {} observe(el) { observed.push(el.id); } }
const ctx = vm.createContext({ document, localStorage, MutationObserver, window: {}, console, Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
    getComputedStyle: () => ({ display: 'flex' }), requestAnimationFrame: fn => fn(), setTimeout: fn => fn() });
vm.runInContext(readFileSync('static/js/handlers/splitPanelResizer.js', 'utf8'), ctx);
vm.runInContext(readFileSync('static/js/handlers/panelFocus.js', 'utf8'), ctx);
reg(root);   // the resizer created #conversation-panel at init

// ── resizer: the novel panel is watched + handled by structure ────────────────
ok('the resizer wraps the chat in a structural #conversation-panel (.sp-panel)', !!lookup['conversation-panel'] && lookup['conversation-panel']._cls.has('sp-panel'));
ok('the resizer observes the NOVEL panel without any list naming it', observed.includes('zzz-future-split-panel'), observed.join(','));
ok('…and still observes the known ones', ['canvas-split-panel', 'kg-split-panel', 'dashboard-split-panel', 'conversation-panel'].every(id => observed.includes(id)));
// open the novel panel → a resize handle must appear between chat and it
novel.style.display = 'flex';
ctx.window.SplitPanelResizer.update();
ok('a handle is built between the chat and the NOVEL panel once it opens', split.querySelectorAll('.split-resize-handle').length === 1);
ok('cc-solo drops when a second panel is visible', !split._cls.has('cc-solo'));

// ── focus: the novel panel takes the ring by structure ────────────────────────
const inner = novel.appendChild(new El('novel-inner'));
ctx.window.PanelFocus.focus('zzz-future-split-panel');
ok('PanelFocus.focus() rings the NOVEL panel', novel._cls.has('sp-focused'));
ctx.window.PanelFocus.clear();
ok('clear() strips the ring from the NOVEL panel (it is in the walk, not only in the focus call)', !novel._cls.has('sp-focused'));
// interaction inside the novel panel → it takes the ring, the chat yields it
ctx.window.PanelFocus.focus('conversation-panel');
const pointerdown = split._listeners['pointerdown'][0];
pointerdown({ target: inner });
ok('a pointerdown INSIDE the novel panel hands it the ring', novel._cls.has('sp-focused') && !lookup['conversation-panel']._cls.has('sp-focused'));
novel.style.display = 'none';
pointerdown({ target: chat });
ok('…and the chat takes it back', lookup['conversation-panel']._cls.has('sp-focused') && !novel._cls.has('sp-focused'));
pointerdown({ target: inner });
ok('a hidden panel never takes the ring (display:none is not a panel you can be in)', !novel._cls.has('sp-focused'));

// ── structural: neither module may grow a list back ──────────────────────────
for (const f of ['static/js/handlers/splitPanelResizer.js', 'static/js/handlers/panelFocus.js']) {
    const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
    ok(`${f.split('/').pop()} names no split-panel id list (identity is :scope > .sp-panel)`,
        !/'(canvas|kg|scheduler|flow|dashboard|memory|dbnav)-split-panel'/.test(src) && /:scope > \.sp-panel/.test(src));
}
ok('polish.css frames the same structure the modules resolve', /#chat-canvas-split > \.sp-panel/.test(readFileSync('static/css/polish.css', 'utf8')));
ok('every split-panel aside in the template carries .sp-panel (so it is a panel to all three)', (() => {
    const idx = readFileSync('templates/index.html', 'utf8');
    const asides = [...idx.matchAll(/<aside id="([a-z-]+-split-panel)"([^>]*)>/g)];
    return asides.length >= 7 && asides.every(m => /class="[^"]*\bsp-panel\b/.test(m[2]));
})());

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
