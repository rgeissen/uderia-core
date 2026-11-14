#!/usr/bin/env node
/**
 * The sequence of the workspace row is the user's — drag a panel header to change it.
 *
 * Executes the shipped splitPanelResizer.js + splitPanelReorder.js under a small DOM
 * with a simulated flex row (visual position = `order`, then DOM position — what a
 * browser does) and asserts:
 *   - the default sequence paints NOTHING (no `order`, nothing stored);
 *   - a header drag / Alt+Shift+Arrow moves the panel, by CSS `order` only — the DOM
 *     is never re-ordered (a re-inserted panel loses scroll position and iframes);
 *   - resize handles follow the VISUAL neighbours in any sequence;
 *   - the conversation stays the row's flex filler on either side of a handle;
 *   - the whole header is the handle (a control that is pressed and dragged moves the
 *     panel; pressed and released it is still a click), a text field excepted;
 *   - a press without travel, Escape, a drop on the panel's own slot, a lone panel and
 *     a fullscreen panel all move nothing;
 *   - the sequence persists, and is reconciled with the panels that exist.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ── a small DOM with a simulated flex row ────────────────────────────────────
function makeWorld({ saved = null, extraPanel = null } = {}) {
    const world = {};
    const matchSimple = (el, s) => {
        const m = s.match(/^([a-zA-Z]+)?((?:\.[\w-]+)*)(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
        if (!m) return false;
        const [, tag, cls, ak, av] = m;
        if (tag && el.tag !== tag.toLowerCase()) return false;
        if (cls && !cls.split('.').filter(Boolean).every(c => el._cls.has(c))) return false;
        if (ak && (av === undefined ? !(ak in el.attrs) : el.attrs[ak] !== av)) return false;
        return true;
    };
    const matches = (el, sel) => sel.split(',').map(s => s.trim()).some(one => {
        const parts = one.split(/\s+/);
        if (!matchSimple(el, parts[parts.length - 1])) return false;
        let anc = el.parentNode;
        for (let i = parts.length - 2; i >= 0; i--) {
            while (anc && !matchSimple(anc, parts[i])) anc = anc.parentNode;
            if (!anc) return false;
            anc = anc.parentNode;
        }
        return true;
    });
    class El {
        constructor(tag, id = '', cls = '', display = '') {
            this.tag = tag; this.id = id; this.attrs = {}; this._cls = new Set(cls.split(/\s+/).filter(Boolean));
            this.children = []; this.parentNode = null; this.textContent = ''; this._w = 300; this._pos = 'static';
            const props = {};
            this.style = { display, width: '', flex: '', maxWidth: '', minWidth: '', order: '', transition: '', left: '', top: '', height: '',
                setProperty: (k, v) => { props[k] = v; }, getPropertyValue: k => props[k] || '' };
            this._listeners = {};
            this.classList = { add: (...c) => c.forEach(x => this._cls.add(x)), remove: (...c) => c.forEach(x => this._cls.delete(x)),
                contains: c => this._cls.has(c), toggle: (c, on) => { on ? this._cls.add(c) : this._cls.delete(c); } };
        }
        get className() { return [...this._cls].join(' '); } set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
        appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); return c; }
        insertBefore(c, ref) { if (c.parentNode) c.remove(); c.parentNode = this; const i = this.children.indexOf(ref); this.children.splice(i < 0 ? this.children.length : i, 0, c); return c; }
        remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; }
        _walk(out = []) { this.children.forEach(c => { out.push(c); c._walk(out); }); return out; }
        querySelectorAll(sel) {
            if (sel === ':scope > .sp-panel') return this.children.filter(c => c._cls.has('sp-panel'));
            return this._walk().filter(e => matches(e, sel));
        }
        querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
        closest(sel) { let n = this; while (n) { if (matches(n, sel)) return n; n = n.parentNode; } return null; }
        addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
        setAttribute(k, v) { this.attrs[k] = String(v); } getAttribute(k) { return this.attrs[k] ?? null; }
        get offsetWidth() { return this._w; }
        compareDocumentPosition(o) { const all = root._walk(); return all.indexOf(o) > all.indexOf(this) ? 4 : 2; }
        // The flex row: shown children left to right by (order, DOM position).
        getBoundingClientRect() {
            if (this === split) return { left: 0, right: 2000, top: 50, height: 600 };
            const shown = split.children
                .filter(c => c._cls.has('split-resize-handle') || c.id === 'conversation-panel' || (c.style.display !== '' && c.style.display !== 'none'))
                .map((c, i) => ({ c, i, o: parseInt(c.style.order, 10) || 0 }))
                .sort((a, b) => (a.o - b.o) || (a.i - b.i));
            let x = 0;
            for (const { c } of shown) {
                const w = c._cls.has('split-resize-handle') ? 10 : (parseFloat(c.style.width) || c._w);
                if (c === this) return { left: x, right: x + w, top: 50, height: 600 };
                x += w;
            }
            return { left: 0, right: 0, top: 0, height: 0 };
        }
    }
    const root = new El('div', 'root');
    const body = root.appendChild(new El('body', 'body'));
    const head = root.appendChild(new El('head', 'head'));
    const split = body.appendChild(new El('div', 'chat-canvas-split'));
    const chat = split.appendChild(new El('main', 'chat-container'));
    // the conversation header lives outside the row until the resizer relocates it
    const convHeader = body.appendChild(new El('header', 'conversation-header', 'sp-header'));
    const convTitle = convHeader.appendChild(new El('span', 'active-session-title', 'sp-title')); convTitle.textContent = 'My session';
    const panel = (id, name) => {
        const p = split.appendChild(new El('aside', id, 'sp-panel', 'none'));
        const h = p.appendChild(new El('div', '', 'sp-header'));
        const left = h.appendChild(new El('div', '', 'sp-header-left'));
        left.appendChild(new El('span', '', 'sp-accent-icon'));
        const sw = left.appendChild(new El('button', id + '-switcher', 'sp-switcher'));
        const nm = sw.appendChild(new El('span', '', 'sp-switcher-name')); nm.textContent = name;
        const acts = h.appendChild(new El('div', '', 'sp-header-actions'));
        acts.appendChild(new El('button', id + '-close', 'sp-action-btn'));
        p.appendChild(new El('div', id + '-body', 'sp-body'));
        p.header = h; p.left = left; p.switcher = sw;
        return p;
    };
    const canvas = panel('canvas-split-panel', 'Canvas');
    const kg = panel('kg-split-panel', 'Knowledge Graph');
    const dash = panel('dashboard-split-panel', 'Dashboard');
    const flow = panel('flow-split-panel', 'Flow');
    const extra = extraPanel ? panel(extraPanel, 'Future') : null;

    const docListeners = {};
    const document = {
        readyState: 'complete', body, head,
        getElementById: id => root._walk().find(e => e.id === id) || null,
        createElement: tag => new El(tag),
        querySelectorAll: sel => root.querySelectorAll(sel),
        addEventListener: (t, fn) => { (docListeners[t] ||= new Set()).add(fn); },
        removeEventListener: (t, fn) => { docListeners[t] && docListeners[t].delete(fn); },
    };
    const store = saved ? { uderia_split_order_v1: JSON.stringify(saved) } : {};
    const localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
    const timers = [];
    class MutationObserver { observe() {} }
    const window = { getSelection: () => ({ removeAllRanges() {} }) };
    const ctx = vm.createContext({ document, localStorage, MutationObserver, window, console, Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
        getComputedStyle: el => ({ display: 'flex', position: el._pos, getPropertyValue: () => ' #3b82f6 ' }),
        setTimeout: fn => { timers.push(fn); return timers.length; } });
    vm.runInContext(readFileSync('static/js/handlers/splitPanelResizer.js', 'utf8'), ctx);
    vm.runInContext(readFileSync('static/js/handlers/splitPanelReorder.js', 'utf8'), ctx);

    const fire = (t, ev) => [...(docListeners[t] || [])].forEach(fn => fn(ev));
    const splitFire = (t, ev) => (split._listeners[t] || []).forEach(fn => fn(ev));
    Object.assign(world, {
        root, body, split, chat, canvas, kg, dash, flow, extra, store, timers, docListeners, convTitle,
        conv: () => document.getElementById('conversation-panel'),
        R: window.SplitPanelResizer, O: window.SplitPanelOrder,
        visible: () => window.SplitPanelResizer.visiblePanels().map(p => p.id),
        orders: () => split.querySelectorAll(':scope > .sp-panel').map(p => p.style.order),
        domIds: () => split.children.filter(c => c._cls.has('sp-panel')).map(c => c.id),
        handles: () => split.children.filter(c => c._cls.has('split-resize-handle')),
        ghost: () => body.querySelector('.sp-reorder-ghost'), bar: () => body.querySelector('.sp-reorder-indicator'),
        open: (...ps) => { ps.forEach(p => { p.style.display = 'flex'; }); window.SplitPanelResizer.update(); },
        down: (target, x = 0, y = 70, extra = {}) => splitFire('pointerdown', { target, clientX: x, clientY: y, button: 0, ...extra }),
        move: (x, y = 70) => fire('pointermove', { clientX: x, clientY: y }),
        up: () => fire('pointerup', {}),
        key: (t, ev) => fire(t, { preventDefault() { ev._pd = true; }, stopPropagation() {}, ...ev }),
        splitKey: (target, key, mods = { altKey: true, shiftKey: true }) => { const ev = { target, key, ...mods, preventDefault() { ev.prevented = true; } }; splitFire('keydown', ev); return ev; },
        drag: (target, fromX, toX) => { world.down(target, fromX); world.move(toX); world.up(); },
        mid: p => { const r = p.getBoundingClientRect(); return (r.left + r.right) / 2; },
    });
    return world;
}

// ── 1. the default sequence paints nothing ───────────────────────────────────
let w = makeWorld();
const DEFAULT = ['conversation-panel', 'canvas-split-panel', 'kg-split-panel', 'dashboard-split-panel', 'flow-split-panel'];
ok('the default sequence is the DOM order', eq(w.O.get(), DEFAULT), JSON.stringify(w.O.get()));
ok('the default sequence sets NO `order` on any panel (byte-identical row)', w.orders().every(o => o === ''), JSON.stringify(w.orders()));
ok('…and stores nothing', !('uderia_split_order_v1' in w.store));
w.open(w.canvas, w.kg);
ok('two panels open → conversation · canvas · knowledge graph, left to right', eq(w.visible(), ['conversation-panel', 'canvas-split-panel', 'kg-split-panel']));
ok('…with a handle between each pair, none carrying an `order`', w.handles().length === 2 && w.handles().every(h => h.style.order === ''));

// ── 2. drag the knowledge graph in front of the conversation ─────────────────
const kgTitle = w.kg.querySelector('.sp-switcher-name').parentNode.parentNode;   // .sp-header-left — not a control
w.down(kgTitle, 700);
ok('a press alone shows nothing (it may still be a click)', !w.ghost() && !w.bar() && !w.body._cls.has('sp-reordering'));
w.move(703);
ok('travel under the threshold is still not a drag', !w.ghost());
w.move(640);
ok('past the threshold the drag begins: a chip with the panel NAME follows the pointer', !!w.ghost() && w.ghost().textContent === 'Knowledge Graph');
ok('…the source panel is marked and the page is in reorder mode', w.kg._cls.has('sp-reorder-source') && w.body._cls.has('sp-reordering'));
ok('…the chip wears the panel\'s own accent', w.ghost().style.getPropertyValue('--sp-accent') === '#3b82f6');
ok('over its own slot there is no landing bar (nothing would move)', w.bar().style.display === 'none');
w.move(20);
ok('left of the conversation\'s midpoint the landing bar shows at the row\'s left edge', w.bar().style.display === '' && w.bar().style.left === '0px' && w.bar().style.height === '600px', JSON.stringify(w.bar().style.left));
const domBefore = w.domIds();
w.up();
ok('release moves the panel: knowledge graph · conversation · canvas', eq(w.visible(), ['kg-split-panel', 'conversation-panel', 'canvas-split-panel']), JSON.stringify(w.visible()));
ok('the sequence is carried by `order` — the DOM order of the panels is UNCHANGED', eq(w.domIds(), domBefore) && eq(w.domIds(), DEFAULT));
ok('every panel gets its rank, hidden ones included (they keep their slot)', eq(w.orders(), ['2', '3', '1', '4', '5']), JSON.stringify(w.orders()));
ok('the chip, the bar and the reorder mode are gone', !w.ghost() && !w.bar() && !w.body._cls.has('sp-reordering') && !w.kg._cls.has('sp-reorder-source'));
ok('the sequence is stored', eq(JSON.parse(w.store.uderia_split_order_v1), ['kg-split-panel', 'conversation-panel', 'canvas-split-panel', 'dashboard-split-panel', 'flow-split-panel']));
ok('the release of a drag swallows exactly the click that follows it', (w.docListeners.click || new Set()).size === 1);
w.timers.splice(0).forEach(fn => fn());
ok('…and only that one', (w.docListeners.click || new Set()).size === 0);
ok('no drag listener outlives the drag', ['pointermove', 'pointerup', 'pointercancel', 'keydown'].every(t => (w.docListeners[t] || new Set()).size === 0));
ok('the move is announced for screen readers', /Knowledge Graph moved to position 1 of 3/.test((w.body.querySelector('.sr-only') || {}).textContent || ''));

// ── 3. handles follow the VISUAL neighbours ──────────────────────────────────
const hs = w.handles();
ok('still one handle per adjacent pair', hs.length === 2);
ok('each handle carries the `order` of the panel to its right and precedes it in the DOM', hs.every(h => {
    const i = w.split.children.indexOf(h); const right = w.split.children[i + 1];
    return right && right._cls.has('sp-panel') && h.style.order === right.style.order;
}));
ok('…so the row reads panel · handle · panel · handle · panel', (() => {
    const xs = [w.kg, hs.find(h => h.style.order === '2'), w.conv(), hs.find(h => h.style.order === '3'), w.canvas].map(e => e.getBoundingClientRect().left);
    return xs.every((x, i) => i === 0 || x > xs[i - 1]);
})());

// ── 4. a hidden panel appears at the slot it kept ────────────────────────────
w.open(w.flow);
ok('opening flow puts it where the sequence says — after canvas', eq(w.visible(), ['kg-split-panel', 'conversation-panel', 'canvas-split-panel', 'flow-split-panel']));
w.drag(w.flow.left, w.mid(w.flow), w.mid(w.conv()) - 5);
ok('dropping flow left of the conversation\'s midpoint lands it between kg and the conversation', eq(w.visible(), ['kg-split-panel', 'flow-split-panel', 'conversation-panel', 'canvas-split-panel']), JSON.stringify(w.visible()));
ok('the hidden dashboard kept its place behind canvas', eq(w.O.get(), ['kg-split-panel', 'flow-split-panel', 'conversation-panel', 'canvas-split-panel', 'dashboard-split-panel']), JSON.stringify(w.O.get()));
w.timers.splice(0).forEach(fn => fn());

// ── 5. what must NOT start or finish a move ──────────────────────────────────
let before = w.O.get();
w.down(w.canvas.switcher, w.mid(w.canvas)); w.up();
ok('a press on a header control that does not travel stays that control\'s click — nothing moves, nothing swallowed', eq(w.O.get(), before) && (w.docListeners.click || new Set()).size === 0);
w.down(w.canvas.querySelector('.sp-switcher-name'), w.mid(w.canvas)); w.move(w.mid(w.canvas) - 30);
ok('…but the title IS a handle: a press on it that travels becomes a drag (a tab is clickable and draggable)', !!w.ghost() && w.ghost().textContent === 'Canvas');
w.key('keydown', { key: 'Escape' }); w.up();
ok('…and a drag that began on a control swallows the click it would otherwise fire', (w.docListeners.click || new Set()).size === 1 && eq(w.O.get(), before));
w.timers.splice(0).forEach(fn => fn());
const field = w.canvas.header.appendChild(new (w.kg.constructor)('input'));
w.down(field, w.mid(w.canvas)); w.move(10); w.up();
ok('a press in a text-entry field never drags (there a press-and-move selects text)', eq(w.O.get(), before) && !w.ghost() && (w.docListeners.pointermove || new Set()).size === 0);
field.remove();
w.down(w.canvas.left, w.mid(w.canvas)); w.up();
ok('a press with no travel is a plain click: nothing moves and no click is swallowed', eq(w.O.get(), before) && (w.docListeners.click || new Set()).size === 0);
w.down(w.canvas.left, w.mid(w.canvas)); w.move(10);
const esc = { key: 'Escape' }; w.key('keydown', esc);
ok('Escape cancels a drag in flight — nothing moves, the chip is gone', eq(w.O.get(), before) && !w.ghost() && !w.body._cls.has('sp-reordering') && esc._pd === true);
w.up(); w.timers.splice(0).forEach(fn => fn());
w.drag(w.canvas.left, w.mid(w.canvas), w.mid(w.canvas) + 40);
ok('a drop on the panel\'s own slot moves nothing', eq(w.O.get(), before));
w.timers.splice(0).forEach(fn => fn());
w.down(w.canvas.left, w.mid(w.canvas), 70, { button: 2 }); w.move(10); w.up();
ok('a secondary-button press never drags', eq(w.O.get(), before) && !w.ghost());
w.down(w.canvas.querySelector('.sp-body'), w.mid(w.canvas)); w.move(10); w.up();
ok('a press in the panel BODY never drags — only the header is the handle', eq(w.O.get(), before));
w.canvas._pos = 'fixed';
w.down(w.kg.left, w.mid(w.kg)); w.move(1500); w.up();
ok('while a panel is fullscreen (position:fixed) the row is not reorderable', eq(w.O.get(), before));
w.canvas._pos = 'static';

// ── 6. keyboard ──────────────────────────────────────────────────────────────
let ev = w.splitKey(w.kg.switcher, 'ArrowRight');
ok('Alt+Shift+Right on a header control moves that panel one slot right', eq(w.visible(), ['flow-split-panel', 'kg-split-panel', 'conversation-panel', 'canvas-split-panel']) && ev.prevented === true, JSON.stringify(w.visible()));
ev = w.splitKey(w.canvas.switcher, 'ArrowRight');
ok('at the end of the row it is a no-op', eq(w.visible(), ['flow-split-panel', 'kg-split-panel', 'conversation-panel', 'canvas-split-panel']));
w.splitKey(w.kg.switcher, 'ArrowLeft');
ok('Alt+Shift+Left moves it back', eq(w.visible(), ['kg-split-panel', 'flow-split-panel', 'conversation-panel', 'canvas-split-panel']));
before = w.O.get();
w.splitKey(w.kg.switcher, 'ArrowRight', { altKey: true, shiftKey: false });
ok('without both modifiers the key is not ours', eq(w.O.get(), before));
const input = w.kg.header.appendChild(new (w.kg.constructor)('input'));
ev = w.splitKey(input, 'ArrowRight');
ok('in a text field Alt+Shift+Arrow stays the field\'s (select by word)', eq(w.O.get(), before) && !ev.prevented);
ev = w.splitKey(w.kg.querySelector('.sp-body'), 'ArrowRight');
ok('outside a header the key is not ours either', eq(w.O.get(), before) && !ev.prevented);

// ── 7. the conversation stays the flex filler on EITHER side of a handle ─────
// visual row now: kg · flow · conversation · canvas
const convEl = w.conv(); convEl._w = 700;
const hLeftOfConv = w.handles().find(h => h.style.order === convEl.style.order);   // flow | conversation
const lh = hLeftOfConv._listeners.pointerdown[0];
lh({ preventDefault() {}, clientX: 600 });
w.move(700);   // +100 → flow grows, conversation yields
ok('conversation RIGHT of the handle: the left panel takes the width…', w.flow.style.width === '400px', w.flow.style.width);
ok('…and the conversation is never given a width or frozen out of flex', convEl.style.width === '' && convEl.style.flex === '' && convEl.style.minWidth === '');
w.move(2000);
ok('…clamped so the conversation keeps its 300px floor', w.flow.style.width === '700px', w.flow.style.width);
w.up();
ok('only the component panel\'s width is stored — never the conversation\'s', (() => { const s = JSON.parse(w.store.uderia_split_widths_v3 || '{}'); return s['flow-split-panel'] === 700 && !('conversation-panel' in s); })());
const hRightOfConv = w.handles().find(h => h.style.order === w.canvas.style.order);  // conversation | canvas
hRightOfConv._listeners.pointerdown[0]({ preventDefault() {}, clientX: 1000 });
w.move(900);
ok('conversation LEFT of the handle (the original case) still works', w.canvas.style.width === '400px' && convEl.style.width === '', w.canvas.style.width);
w.up();

// ── 8. back to the default → nothing painted, nothing stored ─────────────────
w.O.reset();
ok('reset() restores the default sequence', eq(w.visible(), ['conversation-panel', 'canvas-split-panel', 'kg-split-panel', 'flow-split-panel']));
ok('…clears every `order`, on panels and handles alike', w.orders().every(o => o === '') && w.handles().every(h => h.style.order === ''));
ok('…and forgets the stored sequence', !('uderia_split_order_v1' in w.store));
ok('move(id, dir) is the same act as the keyboard', w.O.move('kg-split-panel', -1) === true && eq(w.visible(), ['conversation-panel', 'kg-split-panel', 'canvas-split-panel', 'flow-split-panel']));
ok('…and refuses an unknown panel or a move off the row', w.O.move('nope', 1) === false && w.O.move('conversation-panel', -1) === false);
w.O.set(['canvas-split-panel', 'bogus', 'canvas-split-panel']);
ok('set() drops unknown and repeated ids and appends the rest in DOM order', eq(w.O.get(), ['canvas-split-panel', 'conversation-panel', 'kg-split-panel', 'dashboard-split-panel', 'flow-split-panel']), JSON.stringify(w.O.get()));
w.O.reset();

// ── 9. a lone panel has nothing to trade places with ─────────────────────────
w = makeWorld();
w.down(w.convTitle, 100); w.move(900);
ok('with only the conversation visible a header press never drags', !w.ghost() && !w.body._cls.has('sp-reordering') && (w.docListeners.pointermove || new Set()).size === 0);
w.up();
ok('…and nothing moves', eq(w.O.get(), DEFAULT));

// ── 10. the stored sequence is restored and reconciled at load ───────────────
w = makeWorld({ saved: ['flow-split-panel', 'gone-split-panel', 'conversation-panel', 'flow-split-panel'], extraPanel: 'zzz-future-split-panel' });
ok('a stored sequence is applied at load; a vanished id is dropped, a repeat ignored', eq(w.O.get().slice(0, 2), ['flow-split-panel', 'conversation-panel']), JSON.stringify(w.O.get()));
ok('a panel the stored sequence never knew is appended in DOM order', eq(w.O.get(), ['flow-split-panel', 'conversation-panel', 'canvas-split-panel', 'kg-split-panel', 'dashboard-split-panel', 'zzz-future-split-panel']));
w.open(w.flow, w.extra);
ok('…and the row is laid out by it', eq(w.visible(), ['flow-split-panel', 'conversation-panel', 'zzz-future-split-panel']), JSON.stringify(w.visible()));
// the ends of the row are the ends of the WHOLE sequence
w = makeWorld();
w.open(w.canvas, w.kg);                                   // dashboard + flow stay hidden
w.drag(w.canvas.left, w.mid(w.canvas), 5000);
ok('a panel dragged to the far right goes to the END of the sequence, past the hidden panels', eq(w.O.get(), ['conversation-panel', 'kg-split-panel', 'dashboard-split-panel', 'flow-split-panel', 'canvas-split-panel']), JSON.stringify(w.O.get()));
w.open(w.flow);
ok('…so a panel opened later appears to its LEFT — rightmost stays rightmost', eq(w.visible(), ['conversation-panel', 'kg-split-panel', 'flow-split-panel', 'canvas-split-panel']));
w.O.set(['dashboard-split-panel']);                        // a hidden panel leads the sequence
w.drag(w.canvas.left, w.mid(w.canvas), -50);
ok('…and the far left is the START of the sequence, ahead of a hidden leader', w.O.get()[0] === 'canvas-split-panel', JSON.stringify(w.O.get()));
w = makeWorld({ saved: 'not-a-list' });
ok('a corrupt stored value reads as the default sequence', eq(w.O.get(), DEFAULT) && w.orders().every(o => o === ''));

// ── 11. structure ────────────────────────────────────────────────────────────
const strip = f => readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
const reorder = strip('static/js/handlers/splitPanelReorder.js');
ok('the reorder module names no split-panel id (a panel is :scope > .sp-panel)', !/'[a-z]+-split-panel'/.test(reorder) && /:scope > \.sp-panel/.test(reorder));
ok('…asks the resizer which panels are visible instead of restating the rule', /visiblePanels\(\)/.test(reorder) && !/style\.display\s*[!=]==/.test(reorder.replace(/d\.bar\.style\.display[^;]*;/g, '')));
ok('…and never re-inserts a panel into the DOM', !/insertBefore|moveBefore/.test(reorder) && !/split\(\)\.appendChild|_split\(\)\.append/.test(reorder));
const idx = readFileSync('templates/index.html', 'utf8');
const iRes = idx.indexOf('js/handlers/splitPanelResizer.js'), iReo = idx.indexOf('js/handlers/splitPanelReorder.js');
ok('index.html loads the reorder module AFTER the resizer, both pinned', iRes > 0 && iReo > iRes && /splitPanelReorder\.js'\) \}\}\?v=\d/.test(idx));

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
