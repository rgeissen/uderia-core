/**
 * A deliberately small DOM for executing shipped plain-script UI modules under Node —
 * enough to build elements from innerHTML, find them by tag/class/id/data-attribute, wire
 * listeners, dispatch events, and track focus. Shared by the JS gates that EXECUTE a module
 * rather than grep it (S8's turn-lineage gate is the first consumer; the dbnav gate keeps its
 * own older copy). Not a browser: no layout, no CSS.
 */
import { ICON as PANEL_ICONS } from '../static/js/handlers/panelIcons.js';

export class El {
    constructor(tag, attrs = {}) {
        this.tagName = String(tag).toUpperCase(); this.attrs = { ...attrs }; this.children = []; this.parentNode = null;
        this.dataset = {}; this.style = { setProperty() {}, removeProperty() {} }; this._html = ''; this._text = ''; this.value = ''; this.disabled = false;
        this._classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean)); this._listeners = {}; this.open = false;
        this.checked = 'checked' in attrs;   // the attribute sets the initial state; `:checked` reads the live property
        this.disabled = 'disabled' in attrs;   // a browser reflects the boolean attribute onto the property
        if (attrs.value !== undefined) this.value = attrs.value;
        this.classList = { add: (...c) => c.forEach(x => this._classes.add(x)), remove: (...c) => c.forEach(x => this._classes.delete(x)),
            contains: c => this._classes.has(c), toggle: (c, on) => { if (on === undefined) { this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c); } else on ? this._classes.add(c) : this._classes.delete(c); } };
        for (const [k, v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    /** Attached to the installed document's body (or a registered root)? — what the browser's
     *  `isConnected` answers; a module that rebuilds a detached bubble reads it. */
    get isConnected() { let e = this; while (e) { if (globalThis.document && (e === globalThis.document.body)) return true; e = e.parentNode; } return false; }
    /** `hidden` reflects to the attribute, as in the browser (a module toggles the property, a selector or a gate reads the attribute). */
    get hidden() { return 'hidden' in this.attrs; }
    set hidden(v) { if (v) this.attrs.hidden = ''; else delete this.attrs.hidden; }
    /** `open` reflects to the attribute too (a <details> a module opens, a selector reads). */
    get open() { return 'open' in this.attrs; }
    set open(v) { if (v) this.attrs.open = ''; else delete this.attrs.open; }
    get parentElement() { return this.parentNode || null; }
    /** `id` reflects to the attribute (a module sets `el.id = …`, getElementById reads the attribute). */
    get id() { return this.attrs.id || ''; }
    set id(v) { this.attrs.id = String(v); }
    get className() { return [...this._classes].join(' '); }
    set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
    get innerHTML() { return this._html; }
    set innerHTML(h) { this._html = String(h); this.children = parseHtml(this._html, this); }
    get innerText() { return this.textContent; }
    get textContent() { return (this._text + this.children.map(c => c.textContent).join(' ')).trim(); }
    set textContent(v) { this._text = String(v); this.children = []; this._html = ''; }
    get lastElementChild() { return this.children[this.children.length - 1] || null; }
    get firstElementChild() { return this.children[0] || null; }
    get nextElementSibling() { const p = this.parentNode; if (!p) return null; const i = p.children.indexOf(this); return p.children[i + 1] || null; }
    appendChild(c) { if (c.parentNode && c.parentNode.children) c.parentNode.children = c.parentNode.children.filter(x => x !== c); c.parentNode = this; this.children.push(c); return c; }
    replaceWith(c) { const p = this.parentNode; if (!p) return; const i = p.children.indexOf(this); c.parentNode = p; p.children.splice(i, 1, c); this.parentNode = null; }
    after(c) { const p = this.parentNode; if (!p) return; const i = p.children.indexOf(this); c.parentNode = p; p.children.splice(i + 1, 0, c); }
    insertBefore(c, ref) { if (c.parentNode && c.parentNode.children) c.parentNode.children = c.parentNode.children.filter(x => x !== c); const i = this.children.indexOf(ref); c.parentNode = this; this.children.splice(i < 0 ? this.children.length : i, 0, c); return c; }
    remove() { const p = this.parentNode; if (p) p.children = p.children.filter(c => c !== this); this.parentNode = null; }   // detached, as in the browser (isConnected reads it)
    insertAdjacentElement(pos, el) {
        if (pos === 'afterend') this.after(el);
        else if (pos === 'beforeend') this.appendChild(el);
        else if (pos === 'beforebegin') { const p = this.parentNode; if (p) p.insertBefore(el, this); }
        else if (pos === 'afterbegin') { el.parentNode = this; this.children.unshift(el); }
        return el;
    }
        insertAdjacentHTML(pos, html) {
        const nodes = parseHtml(String(html), this);
        if (pos === 'beforeend') { nodes.forEach(n => { n.parentNode = this; this.children.push(n); }); }
        else if (pos === 'afterbegin') { nodes.forEach(n => { n.parentNode = this; }); this.children.unshift(...nodes); }
        else { const p = this.parentNode; if (!p) return; const i = p.children.indexOf(this); nodes.forEach(n => { n.parentNode = p; }); p.children.splice(pos === 'afterend' ? i + 1 : i, 0, ...nodes); }
        this._html += String(html);
    }
    get outerHTML() { const a = Object.entries(this.attrs).map(([k, v]) => v === '' ? k : `${k}="${v}"`).join(' '); return `<${this.tagName.toLowerCase()}${a ? ' ' + a : ''}>${this._html}</${this.tagName.toLowerCase()}>`; }
    set outerHTML(html) { const p = this.parentNode; if (!p) return; const nodes = parseHtml(String(html), p); const i = p.children.indexOf(this); nodes.forEach(n => { n.parentNode = p; }); p.children.splice(i, 1, ...nodes); }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    removeAttribute(k) { delete this.attrs[k]; if (k.startsWith('data-')) delete this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())]; }
    hasAttribute(k) { return k in this.attrs; }
    _walk(out = []) { for (const c of this.children) { out.push(c); c._walk(out); } return out; }
    _matches(sel) {
        return String(sel).split(',').map(x => x.trim()).some(s0 => {
            const wantChecked = s0.endsWith(':checked');
            const s = wantChecked ? s0.slice(0, -':checked'.length) : s0;
            if (wantChecked && !this.checked) return false;
            const m = s.match(/^([a-zA-Z][a-zA-Z0-9]*)?((?:\.[\w-]+)*)?(?:\[([\w-]+)(?:="([^"]*)")?\])?(?:#([\w-]+))?$/);
            if (!m) return false;
            const [, tag, cls, ak, av, id] = m;
            if (tag && this.tagName !== tag.toUpperCase()) return false;
            if (cls && !cls.split('.').filter(Boolean).every(c => this._classes.has(c))) return false;
            if (ak && (av === undefined ? !(ak in this.attrs) : this.attrs[ak] !== av)) return false;
            if (id && this.attrs.id !== id) return false;
            return true;
        });
    }
    matches(sel) { return this._matches(sel); }
    closest(sel) { let e = this; while (e) { if (e._matches && e._matches(sel)) return e; e = e.parentNode; } return null; }
    querySelectorAll(sel) { return this._walk().filter(e => e._matches(sel)); }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
    removeEventListener() {}
    dispatchEvent(ev) {
        if (!ev.target) ev.target = this;
        ev.preventDefault = ev.preventDefault || (() => {}); ev.stopPropagation = ev.stopPropagation || (() => { ev._stopped = true; });
        let e = this;
        while (e && !ev._stopped) { (e._listeners[ev.type] || []).forEach(fn => fn.call(e, ev)); if (!ev.bubbles) break; e = e.parentNode; }
        return true;
    }
    click() { this.dispatchEvent({ type: 'click', bubbles: true }); }
    focus() { globalThis.document.activeElement = this; }
    blur() {}
    animate() { return {}; }
    scrollIntoView() {}
    getClientRects() { return [{}]; }
}
// a small HTML parser: elements by tag/class/id/data-* + text between tags
export function parseHtml(html, parent) {
    const out = []; const stack = [parent]; const re = /<\/?([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g; let m;
    const VOID = new Set(['input', 'br', 'hr', 'img', 'path', 'circle', 'line', 'rect', 'polyline', 'polygon', 'ellipse']);
    const starts = new Map();   // element → index where its inner html starts
    while ((m = re.exec(html))) {
        const [full, tag, rawAttrs, selfClose] = m;
        if (full.startsWith('</')) {
            if (stack.length > 1) { const closed = stack.pop(); if (starts.has(closed)) closed._html = html.slice(starts.get(closed), m.index); }
            // text after a closing tag belongs to the element now on top (a sibling text run)
            const cur = stack[stack.length - 1];
            if (cur && cur !== parent) {
                const rest = html.slice(re.lastIndex); const t = rest.slice(0, rest.indexOf('<') < 0 ? rest.length : rest.indexOf('<')).trim();
                if (t) cur._text = (cur._text ? cur._text + ' ' : '') + t.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
            }
            continue;
        }
        // double- AND single-quoted values, as a browser parses them (server-rendered rows use both)
        const attrs = {}; const ar = /([\w-]+)(?:="([^"]*)"|='([^']*)')?/g; let a;
        // a browser decodes character references in attribute values (dataset reads the decoded text)
        const dec = v => v.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#x27;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
        while ((a = ar.exec(rawAttrs))) { const v = a[2] !== undefined ? a[2] : a[3]; attrs[a[1]] = v === undefined ? '' : dec(v); }
        const el = new El(tag, attrs);
        const p = stack[stack.length - 1]; el.parentNode = p; if (p === parent) out.push(el); else p.children.push(el);
        const rest = html.slice(re.lastIndex); const t = rest.slice(0, rest.indexOf('<') < 0 ? rest.length : rest.indexOf('<'));
        el._text = t.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").trim();
        if (!selfClose && !VOID.has(tag)) { stack.push(el); starts.set(el, re.lastIndex); }
    }
    return out;
}
/** Install `document`, `window` and the browser globals a plain script expects. */
export function installDom(extraWindow = {}) {
    const byId = new Map();
    const body = new El('body');
    const doc = {
        body, head: new El('head'), documentElement: new El('html'), activeElement: null, readyState: 'complete',
        getElementById: id => byId.get(id) || body._walk().find(e => e.attrs.id === id) || null,
        createElement: t => new El(t),
        querySelectorAll: sel => body._walk().filter(e => e._matches(sel)),
        querySelector: sel => doc.querySelectorAll(sel)[0] || null,
        _listeners: {},
        addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); },
        removeEventListener(t, fn) { this._listeners[t] = (this._listeners[t] || []).filter(f => f !== fn); },
        dispatchEvent(ev) { ev.preventDefault = ev.preventDefault || (() => {}); ev.stopPropagation = ev.stopPropagation || (() => {}); (this._listeners[ev.type] || []).forEach(fn => fn(ev)); return true; },
        _register: (id, el) => byId.set(id, el),
    };
    doc.documentElement.style = { setProperty() {}, removeProperty() {} };
    globalThis.document = doc;
    // main.js loads the shared panel glyphs before any panel can render; so does the stub
    globalThis.window = Object.assign({ matchMedia: () => ({ matches: false }), PanelIcons: PANEL_ICONS }, extraWindow);
    globalThis.localStorage = { _m: {}, getItem(k) { return this._m[k] ?? null; }, setItem(k, v) { this._m[k] = String(v); }, removeItem(k) { delete this._m[k]; } };
    globalThis.MutationObserver = class { constructor(fn) { this.fn = fn; } observe() {} disconnect() {} };
    globalThis.CustomEvent = class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; this.bubbles = !!init.bubbles; } };
    globalThis.KeyboardEvent = class { constructor(type, init = {}) { Object.assign(this, { type, bubbles: !!init.bubbles, key: init.key, metaKey: !!init.metaKey, ctrlKey: !!init.ctrlKey, altKey: !!init.altKey }); } };
    globalThis.Event = class { constructor(type, init = {}) { this.type = type; this.bubbles = !!init.bubbles; } };
    globalThis.fetch = async () => { throw new Error('no network in CI'); };
    globalThis.setTimeout = (fn) => { return 0; }; globalThis.clearTimeout = () => {};
    return { doc, body, El };
}
