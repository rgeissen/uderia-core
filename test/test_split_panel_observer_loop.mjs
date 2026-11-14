#!/usr/bin/env node
/**
 * Split-panel observers reach quiescence — the composer-lag gate.
 *
 * Two shipped IIFEs watch the split row: splitPanelResizer.js observes every panel's
 * style/class attributes and rebuilt its drag handles on EVERY record (remove + insert
 * under #chat-canvas-split); subHeaderAutohide.js observes the row's childList and
 * re-wires every panel on every change, ADDING a class the panel already wore — which
 * still re-sets the attribute, which the resizer saw… The two fed each other as fast
 * as the main thread allowed for as long as a panel with a sub-header was open, and
 * every turn restyled the row: with a 500-node graph (~5,000 SVG elements) the
 * conversation input waited ~110 ms per keystroke (measured; 22 ms without).
 *
 * This gate executes BOTH shipped scripts under a DOM stub whose MutationObserver
 * really delivers records (attribute writes + childList changes, flushed like a
 * microtask) and asserts, by counting, that opening a sub-header panel settles in a
 * bounded number of observer turns, that an attribute write which leaves the panel
 * sequence unchanged rebuilds nothing, and that re-wiring an already-wired panel
 * performs no class write. No server, no browser.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

// ── a DOM stub that NOTIFIES: attribute + childList records, delivered on flush ──
const observers = [];           // { el, opts, fn }
let pending = [];               // queued records
let flushing = false;
function notify(target, type, extra = {}) {
    for (const o of observers) {
        const match = (type === 'attributes' && o.opts.attributes && o.el === target)
                   || (type === 'childList' && o.opts.childList && (o.el === target || (o.opts.subtree && isInside(target, o.el))));
        if (match) pending.push({ o, rec: { type, target, ...extra } });
    }
}
function isInside(n, anc) { let p = n.parentNode; while (p) { if (p === anc) return true; p = p.parentNode; } return false; }
let turns = 0;
function flush(limit = 200) {
    if (flushing) return; flushing = true;
    while (pending.length && turns < limit) {
        const batch = pending; pending = [];
        const byObs = new Map();
        for (const { o, rec } of batch) { if (!byObs.has(o)) byObs.set(o, []); byObs.get(o).push(rec); }
        for (const [o, recs] of byObs) { turns++; o.fn(recs, o); }
        // rAF callbacks queued by the scanner run before the next observer turn
        const r = rafQueue; rafQueue = []; r.forEach(f => f());
    }
    flushing = false;
}
let rafQueue = [];

class ClassList {
    constructor(el, cls) { this.el = el; this.set = new Set(cls.split(/\s+/).filter(Boolean)); }
    add(...c) { c.forEach(x => this.set.add(x)); this.el._classWrites++; notify(this.el, 'attributes', { attributeName: 'class' }); }
    remove(...c) { c.forEach(x => this.set.delete(x)); this.el._classWrites++; notify(this.el, 'attributes', { attributeName: 'class' }); }
    contains(c) { return this.set.has(c); }
    toggle(c, on) { const want = on === undefined ? !this.set.has(c) : !!on; want ? this.set.add(c) : this.set.delete(c); this.el._classWrites++; notify(this.el, 'attributes', { attributeName: 'class' }); return want; }
}
class Style {
    constructor(el, display) { this.el = el; this._v = { display, width: '', flex: '', maxWidth: '', minWidth: '', order: '', transition: '' }; }
    setProperty(k, v) { this._v[k] = v; notify(this.el, 'attributes', { attributeName: 'style' }); }
}
for (const k of ['display', 'width', 'flex', 'maxWidth', 'minWidth', 'order', 'transition']) {
    Object.defineProperty(Style.prototype, k, { get() { return this._v[k]; }, set(v) { this._v[k] = v; notify(this.el, 'attributes', { attributeName: 'style' }); } });
}
class El {
    constructor(id, cls = '', display = '', attrs = {}) {
        this.id = id; this.children = []; this.parentNode = null; this._attrs = attrs; this._classWrites = 0; this._listeners = {};
        this.classList = new ClassList(this, cls); this.style = new Style(this, display);
    }
    get className() { return [...this.classList.set].join(' '); }
    set className(v) { this.classList.set = new Set(v.split(/\s+/).filter(Boolean)); this._classWrites++; notify(this, 'attributes', { attributeName: 'class' }); }
    has(c) { return this.classList.set.has(c); }
    appendChild(c) { c.parentNode = this; this.children.push(c); notify(this, 'childList', { addedNodes: [c], removedNodes: [] }); return c; }
    insertBefore(c, ref) { c.parentNode = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? this.children.length : i, 0, c); notify(this, 'childList', { addedNodes: [c], removedNodes: [] }); return c; }
    remove() { const p = this.parentNode; if (!p) return; p.children = p.children.filter(c => c !== this); this.parentNode = null; notify(p, 'childList', { addedNodes: [], removedNodes: [this] }); }
    _all() { const out = []; const walk = n => n.children.forEach(c => { out.push(c); walk(c); }); walk(this); return out; }
    querySelectorAll(sel) {
        if (sel === ':scope > .sp-panel') return this.children.filter(c => c.has('sp-panel'));
        if (sel === '[data-sp-subheader]') return this._all().filter(c => 'spSubheader' in c._attrs);
        if (sel === '.sp-header .sp-header-actions') return this._all().filter(c => c.has('sp-header-actions') && c.parentNode && c.parentNode.has('sp-header'));
        if (sel.startsWith('.') && !sel.includes(' ')) { const k = sel.slice(1); return this._all().filter(c => c.has(k)); }
        return [];
    }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    closest(sel) {
        if (sel === '.split-resize-handle') { let n = this; while (n) { if (n.has('split-resize-handle')) return n; n = n.parentNode; } return null; }
        let n = this; while (n) { if (n.has('sp-panel') && n.parentNode && n.parentNode.id === 'chat-canvas-split') return n; n = n.parentNode; } return null;
    }
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
    setAttribute(k, v) { this._attrs[k] = v; } getAttribute(k) { return this._attrs[k] ?? null; }
    compareDocumentPosition(o) { const all = root._all(); return all.indexOf(o) > all.indexOf(this) ? 4 : 2; }
    get scrollHeight() { return 40; }
    get offsetWidth() { return 400; }
}

const root = new El('root');
const split = root.appendChild(new El('chat-canvas-split'));
const chat = split.appendChild(new El('chat-container'));
root.appendChild(new El('conversation-header'));
// the KG panel: a static header with its actions slot, and a body that will hold a sub-toolbar
const kg = split.appendChild(new El('kg-split-panel', 'sp-panel', 'none'));
const kgHeader = kg.appendChild(new El('kg-h', 'sp-header'));
kgHeader.appendChild(new El('kg-ha', 'sp-header-actions'));
const kgBody = kg.appendChild(new El('kg-split-content', 'kg-split-body'));
// a second sub-header panel that stays closed (the probe saw it re-set too)
const ep = split.appendChild(new El('explain-plan-split-panel', 'sp-panel', 'none'));
ep.appendChild(new El('ep-h', 'sp-header')).appendChild(new El('ep-ha', 'sp-header-actions'));
ep.appendChild(new El('ep-tb', 'kg-toolbar', '', { spSubheader: '' }));

const lookup = {}; const reg = () => { lookup.root = root; root._all().forEach(n => { if (n.id) lookup[n.id] = n; }); }; reg();
const document = {
    readyState: 'complete',
    getElementById: id => { reg(); return lookup[id] || null; },
    createElement: () => new El(''),
    head: new El('head'), body: new El('body'),
    querySelectorAll: sel => root.querySelectorAll(sel),
    addEventListener() {},
};
const localStorage = { getItem: () => null, setItem() {} };
class MutationObserver { constructor(fn) { this.fn = fn; } observe(el, opts) { observers.push({ el, opts, fn: this.fn }); } disconnect() {} }
const ctx = vm.createContext({
    document, localStorage, MutationObserver, window: {}, console, Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
    getComputedStyle: () => ({ display: 'flex' }), requestAnimationFrame: fn => { rafQueue.push(fn); }, setTimeout: fn => fn(), clearTimeout() {},
    WeakMap, JSON, parseInt, parseFloat, Math, Set, Array, Object,
});
vm.runInContext(readFileSync('static/js/handlers/splitPanelResizer.js', 'utf8'), ctx);
vm.runInContext(readFileSync('static/js/handlers/subHeaderAutohide.js', 'utf8'), ctx);
flush();

console.log('[1. opening a sub-header panel settles]');
turns = 0; pending = [];
kg.style.display = 'flex';                                  // the renderer opens the panel
kg.classList.add('kg-split--open');
kgBody.appendChild(new El('kg-tb', 'kg-toolbar', '', { spSubheader: '' }));   // …and builds its toolbar
flush(200);
ok('the observers reach quiescence (no records left to deliver)', pending.length === 0, `${pending.length} pending`);
ok('…within a handful of observer turns, not the 200 cap', turns < 20, `${turns} turns`);
ok('the KG panel is wired as a sub-header panel', kg.has('sp-has-subheader'));
ok('one handle stands between the conversation and the KG panel', split.querySelectorAll('.split-resize-handle').length === 1, String(split.querySelectorAll('.split-resize-handle').length));
const handle0 = split.querySelector('.split-resize-handle');

console.log('\n[2. a write that leaves the sequence unchanged rebuilds nothing]');
const writesBefore = kg._classWrites, epWritesBefore = ep._classWrites;
turns = 0;
kg.classList.add('sp-subheader-open');                     // the hover reveal (subHeaderAutohide._open)
kg.style.setProperty('--sp-subheader-h', '40px');            // …and its measured height
flush(200);
ok('the handle is the SAME element (not removed and re-inserted)', split.querySelector('.split-resize-handle') === handle0);
ok('still exactly one handle', split.querySelectorAll('.split-resize-handle').length === 1);
ok('the scanner wrote no class on the already-wired KG panel', kg._classWrites === writesBefore + 1, `${kg._classWrites - writesBefore} writes (1 = our own hover add)`);
ok('…nor on the closed explain-plan panel', ep._classWrites === epWritesBefore, `${ep._classWrites - epWritesBefore} writes`);
ok('and it settled at once', turns <= 3, `${turns} turns`);

console.log('\n[3. a change the handles DO depend on still rebuilds them]');
ep.style.display = 'flex';                                  // a second panel opens
flush(200);
ok('two handles once a third panel is visible', split.querySelectorAll('.split-resize-handle').length === 2, String(split.querySelectorAll('.split-resize-handle').length));
ok('the explain-plan panel got an explicit width (newly-opened rule kept)', /px$/.test(ep.style.width), ep.style.width);
ok('settled', pending.length === 0);
kg.style.order = '5';                                       // a reorder moves the KG panel to the right
flush(200);
const handles = split.querySelectorAll('.split-resize-handle');
ok('a reorder (same set, new order) rebuilds: the handle before the KG panel carries its order', handles.some(h => h.style.order === '5'), handles.map(h => h.style.order).join(','));
ep.style.display = 'none';
flush(200);
ok('closing a panel drops its handle', split.querySelectorAll('.split-resize-handle').length === 1);

console.log('\n[4. the source carries the rule, not only this stub]');
const resizer = readFileSync('static/js/handlers/splitPanelResizer.js', 'utf8');
const autohide = readFileSync('static/js/handlers/subHeaderAutohide.js', 'utf8');
ok('resizer: the attribute observer compares the panel SEQUENCE before touching the handles', /if \(key === _prevSequenceKey\) return;/.test(resizer));
ok('resizer: the sequence key carries each panel\'s flex order', /_order\(p\)/.test(resizer.slice(resizer.indexOf('_sequenceKey'))));
ok('autohide: wiring adds the class only when it is missing', /if \(!panel\.classList\.contains\('sp-has-subheader'\)\) panel\.classList\.add\('sp-has-subheader'\)/.test(autohide));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail === 0 ? 0 : 1);
