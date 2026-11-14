/**
 * Marketplace visibility control — the portal contract and the scope vocabulary.
 *
 * Two defects this gate exists to keep fixed:
 *
 *  1. THE PORTAL. The filter bar is a `.glass-panel`, and `backdrop-filter`
 *     creates a stacking context — so a menu positioned inside it is trapped
 *     there however high its z-index, and the cards painted after the bar cover
 *     it. Every option measured `hittable: false`: the control was unusable, so
 *     a listing shared with you personally could not be reached at all. The menu
 *     is therefore portalled to <body> while open. Layout cannot be measured in
 *     Node, so this gate asserts the PROPERTY that makes the fix work (the menu
 *     leaves the bar, is fixed-positioned, and returns to its exact slot on
 *     close) rather than the pixels; the pixels were verified in a real browser.
 *
 *  2. THE SCOPE. 'all' — the union of public and shared-with-me — is what the
 *     backend supported all along and the UI never offered. It is now the
 *     default, because 'public' alone hides a listing granted to you and gives
 *     no sign that anything is missing.
 *
 * Runs the SHIPPED functions against a small DOM stub. No server, no network.
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (ok) pass++; else { fail++; console.error(`  FAIL ${name}\n    got:  ${JSON.stringify(got)}\n    want: ${JSON.stringify(want)}`); }
};
const ok = (name, cond) => check(name, !!cond, true);
// A gate that dies without naming what broke is half a gate.
const m1 = (name, re, str) => {
    const m = str.match(re);
    if (m) return m[1];
    fail++; console.error(`  FAIL ${name}\n    pattern did not match: ${re}`);
    return '(no match)';
};

// ---------------------------------------------------------------- extraction
const SRC = readFileSync('static/js/handlers/marketplaceHandler.js', 'utf8');
const slice = (from, to) => {
    const a = SRC.indexOf(from);
    if (a < 0) throw new Error(`anchor not found: ${from}`);
    const b = SRC.indexOf(to, a);
    if (b < 0) throw new Error(`end anchor not found: ${to}`);
    return SRC.slice(a, b);
};
const selectSrc = slice('function _tdaMenuOf(', 'function loadMarketplaceContent(');
const visSrc = slice('function _backendVisibility(', '\n\n');
ok('extraction is non-vacuous (select machinery)', selectSrc.length > 800);
ok('extraction is non-vacuous (scope reducer)', visSrc.length > 60);

// ------------------------------------------------------------------ DOM stub
class El {
    constructor(tag, attrs = {}) {
        this.tagName = tag.toUpperCase();
        this.children = []; this.parentNode = null;
        this.attrs = { ...attrs }; this.dataset = {}; this.style = {};
        this.textContent = ''; this._listeners = {};
        this._classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean));
        this.offsetHeight = 120;
        this.classList = {
            add: (...c) => c.forEach(x => this._classes.add(x)),
            remove: (...c) => c.forEach(x => this._classes.delete(x)),
            contains: c => this._classes.has(c),
            toggle: (c, on) => { if (on === undefined) this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c); else on ? this._classes.add(c) : this._classes.delete(c); },
        };
    }
    get className() { return [...this._classes].join(' '); }
    get nextSibling() {
        if (!this.parentNode) return null;
        const i = this.parentNode.children.indexOf(this);
        return this.parentNode.children[i + 1] || null;
    }
    get parentElement() { return this.parentNode; }
    appendChild(c) { c.parentNode?.removeChild(c); c.parentNode = this; this.children.push(c); return c; }
    insertBefore(c, ref) {
        c.parentNode?.removeChild(c); c.parentNode = this;
        const i = ref ? this.children.indexOf(ref) : -1;
        if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
        return c;
    }
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    _walk(out = []) { for (const c of this.children) { out.push(c); c._walk(out); } return out; }
    _matches(sel) {
        const tagAttr = sel.match(/^([a-zA-Z]+)\[(.+)\]$/);   // e.g. input[type="hidden"]
        if (tagAttr) {
            return this.tagName === tagAttr[1].toUpperCase() && this._matches(`[${tagAttr[2]}]`);
        }
        if (sel.startsWith('[') && sel.endsWith(']')) {
            const m = sel.slice(1, -1).split('=');
            const k = m[0];
            if (m.length === 1) return k in this.attrs;
            return this.attrs[k] === m[1].replace(/^["']|["']$/g, '');
        }
        if (sel.startsWith('.')) return this._classes.has(sel.slice(1));
        if (sel.startsWith('#')) return this.attrs.id === sel.slice(1);
        return this.tagName === sel.toUpperCase();
    }
    querySelectorAll(sel) { return this._walk().filter(e => e._matches(sel)); }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    contains(n) { return n === this || this._walk().includes(n); }
    addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
    dispatchEvent(ev) { (this._listeners[ev.type] || []).forEach(fn => fn(ev)); return true; }
    click() { this.dispatchEvent({ type: 'click', target: this, stopPropagation() {} }); }
    getBoundingClientRect() { return { left: 100, top: 200, right: 324, bottom: 236, width: 224, height: 36 }; }
}

function buildDom() {
    const body = new El('body');
    const bar = new El('div', { class: 'glass-panel' });          // the stacking context
    const root = new El('div', { class: 'w-56 relative tda-select', 'data-tda-select': 'visibility' });
    const label = new El('span', { 'data-tda-select-label': '' }); label.textContent = 'All';
    const btn = new El('button', { 'data-tda-select-button': '' });
    btn.appendChild(label);
    const menu = new El('div', { class: 'dropdown-menu', 'data-tda-select-menu': '' });
    const VALUES = [['all', 'All'], ['all_inc_own', 'All + mine'], ['public', 'Public'],
                    ['public_inc_own', 'Public + mine'], ['targeted', 'Shared with me'],
                    ['targeted_inc_own', 'Shared with me + mine']];
    for (const [v, text] of VALUES) {
        const o = new El('button', { 'data-tda-select-option': '' });
        o.dataset.value = v;
        const chk = new El('svg', { class: 'tda-select-check' + (v === 'all' ? '' : ' invisible') });
        if (v !== 'all') chk._classes.add('invisible');
        const sp = new El('span'); sp.textContent = text;
        o.appendChild(chk); o.appendChild(sp);
        menu.appendChild(o);
    }
    const hidden = new El('input', { type: 'hidden', id: 'marketplace-visibility-filter' });
    hidden.value = 'all';
    root.appendChild(btn); root.appendChild(menu); root.appendChild(hidden);
    bar.appendChild(root);
    body.appendChild(bar);
    const cards = new El('div', { class: 'cards' });               // painted after the bar
    body.appendChild(cards);
    return { body, bar, root, btn, menu, hidden, label };
}

function run(dom) {
    const docListeners = {};
    const document = {
        body: dom.body,
        querySelector: s => dom.body.querySelector(s),
        querySelectorAll: s => dom.body.querySelectorAll(s),
        addEventListener: (t, fn) => { (docListeners[t] ||= []).push(fn); },
    };
    const winListeners = [];
    const window = {
        innerWidth: 1600, innerHeight: 1000,
        addEventListener: (t, fn) => winListeners.push([t, fn, 'add']),
        removeEventListener: (t, fn) => winListeners.push([t, fn, 'remove']),
    };
    const factory = new Function('document', 'window', 'Event',
        `${selectSrc}\n${visSrc}\nreturn {_initTdaSelect,_setTdaSelectValue,_tdaMenuOf,_backendVisibility};`);
    const api = factory(document, window,
        class { constructor(t, o) { this.type = t; Object.assign(this, o); } });
    return { api, docListeners, winListeners };
}

// ------------------------------------------------------- A. portal contract
{
    const dom = buildDom();
    const { api, docListeners } = run(dom);
    api._initTdaSelect(dom.root, dom.hidden);

    check('A1 menu starts inside the filter bar', dom.root.contains(dom.menu), true);

    dom.btn.click();   // open
    check('A2 open portals the menu out of the stacking context', dom.root.contains(dom.menu), false);
    check('A3 open appends the menu to <body>', dom.menu.parentNode === dom.body, true);
    check('A4 open positions the menu fixed', dom.menu.style.position, 'fixed');
    ok('A5 open gives the menu a z-index above the page', Number(dom.menu.style.zIndex) > 1000);
    ok('A6 open sets an explicit left/top (never left to the trapped stylesheet)',
       !!dom.menu.style.left && !!dom.menu.style.top);
    check('A7 open marks the menu open', dom.menu.classList.contains('open'), true);
    check('A8 open reports expanded', dom.btn.getAttribute('aria-expanded'), 'true');

    // the value setter must still find its options while the menu is portalled
    api._setTdaSelectValue(dom.root, 'targeted');
    check('A9 value applies while portalled (label)', dom.label.textContent, 'Shared with me');
    check('A10 value applies while portalled (hidden input)', dom.hidden.value, 'targeted');

    // clicking inside the portalled menu must NOT be read as an outside click
    (docListeners.click || []).forEach(fn => fn({ target: dom.menu }));
    check('A11 a click inside the portalled menu does not close it', dom.menu.classList.contains('open'), true);

    // a genuine outside click closes and restores
    (docListeners.click || []).forEach(fn => fn({ target: dom.body }));
    check('A12 outside click closes', dom.menu.classList.contains('open'), false);
    check('A13 close returns the menu to its original parent', dom.root.contains(dom.menu), true);
    check('A14 close restores its original slot (before the hidden input)',
          dom.root.children.map(c => c.tagName), ['BUTTON', 'DIV', 'INPUT']);
    check('A15 close clears the portal positioning',
          [dom.menu.style.position, dom.menu.style.top, dom.menu.style.left, dom.menu.style.zIndex], ['', '', '', '']);
    check('A16 close reports collapsed', dom.btn.getAttribute('aria-expanded'), 'false');
}

// picking an option closes, restores, and applies the value
{
    const dom = buildDom();
    const { api } = run(dom);
    let changes = 0;
    dom.hidden.addEventListener('change', () => changes++);
    api._initTdaSelect(dom.root, dom.hidden);
    dom.btn.click();
    const opt = dom.menu.querySelectorAll('[data-tda-select-option]').find(o => o.dataset.value === 'targeted_inc_own');
    opt.click();
    check('A17 picking an option restores the menu home', dom.root.contains(dom.menu), true);
    check('A18 picking an option applies the label', dom.label.textContent, 'Shared with me + mine');
    check('A19 picking an option applies the value', dom.hidden.value, 'targeted_inc_own');
    check('A20 picking an option fires exactly one change', changes, 1);
    check('A21 picking an option closes the menu', dom.menu.classList.contains('open'), false);
}

// the resolver finds the menu in both states — nothing may query the root directly
{
    const dom = buildDom();
    const { api } = run(dom);
    api._initTdaSelect(dom.root, dom.hidden);
    check('A22 resolver finds the menu while closed', api._tdaMenuOf(dom.root) === dom.menu, true);
    dom.btn.click();
    check('A23 resolver finds the menu while portalled', api._tdaMenuOf(dom.root) === dom.menu, true);
}

// ---------------------------------------------------- B. scope vocabulary
{
    const dom = buildDom();
    const { api } = run(dom);
    const b = api._backendVisibility;
    check('B1 all -> all', b('all'), 'all');
    check('B2 all + mine -> all', b('all_inc_own'), 'all');
    check('B3 public -> public', b('public'), 'public');
    check('B4 public + mine -> public', b('public_inc_own'), 'public');
    check('B5 shared-with-me -> targeted', b('targeted'), 'targeted');
    check('B6 shared-with-me + mine -> targeted', b('targeted_inc_own'), 'targeted');
    // A filter is not a permission boundary — the backend still scopes to what the
    // caller may see — so an unrecognised value must widen, never silently hide.
    check('B7 an unknown value widens rather than hides', b('nonsense'), 'all');
}

// -------------------------------------------- C. defaults + labels (markup)
{
    const html = readFileSync('templates/index.html', 'utf8');
    const i = html.indexOf('data-tda-select="visibility"');
    const j = html.indexOf('<input type="hidden" id="marketplace-visibility-filter"', i);
    ok('C0 the visibility control is present', i > 0 && j > i);
    const seg = html.slice(i, j);

    const blocks = seg.split(/(?=<button type="button" data-tda-select-option)/).filter(b => /data-value="/.test(b));
    const values = blocks.map(b => b.match(/data-value="([a-z_]+)"/)[1]);
    const labels = blocks.map(b => b.match(/<span>([^<]*)<\/span>/)[1]);
    const checked = blocks.filter(b => /tda-select-check w-4 h-4 mr-2 flex-shrink-0"/.test(b))
                          .map(b => b.match(/data-value="([a-z_]+)"/)[1]);

    check('C1 every scope is offered, widest first',
          values, ['all', 'all_inc_own', 'public', 'public_inc_own', 'targeted', 'targeted_inc_own']);
    check('C2 the filter speaks from the reader\'s seat',
          labels, ['All', 'All + mine', 'Public', 'Public + mine', 'Shared with me', 'Shared with me + mine']);
    check('C3 exactly one option is pre-checked, and it is the default', checked, ['all']);
    check('C4 the collapsed control shows the default',
          m1('C4 label regex', /data-tda-select-label[^>]*>([^<]*)</, seg), 'All');
    check('C5 the stored default is the widest scope',
          m1('C5 hidden-input regex', /id="marketplace-visibility-filter" value="([a-z_]+)"/, html), 'all');
    ok('C6 the filter no longer says "Targeted"', !/<span>Targeted( \+ mine)?<\/span>/.test(seg));
    // The longest label is "Shared with me + mine"; without this the collapsed
    // control wrapped onto a second row and the whole filter bar grew.
    ok('C11 the collapsed label can never wrap to a second row',
       /data-tda-select-label[^>]*class="[^"]*whitespace-nowrap/.test(seg));
    ok('C12 the control is wide enough for the longest label',
       /class="w-64 relative tda-select" data-tda-select="visibility"/.test(html));
    // The publisher is choosing an act, so their dialog keeps the publisher's word.
    ok('C7 the publish dialog still says "Targeted"', /Targeted/.test(html.slice(j)));
}

// the JS default must agree with the markup default, or the first fetch lies
{
    ok('C8 the module default scope is the widest',
       /let currentVisibility = 'all';/.test(SRC));
    ok('C9 both new values are accepted from storage',
       /'all', 'all_inc_own', 'public', 'public_inc_own', 'targeted', 'targeted_inc_own'/.test(SRC));
    ok('C10 a stored show-own preference keeps the widest scope',
       /currentVisibility = 'all_inc_own';/.test(SRC));
    // A value stored under the old key predates the new option set, labels and
    // default — reading it would replay the default that hid shared listings.
    ok('C13 the stored preference key is versioned past the old vocabulary',
       /localStorage\.getItem\('marketplace_visibility_v2'\)/.test(SRC));
    ok('C14 the same version is written back',
       /localStorage\.setItem\('marketplace_visibility_v2'/.test(SRC));
    ok('C15 no site still reads the pre-vocabulary key',
       !/localStorage\.(get|set)Item\('marketplace_(visibility|show_own)'/.test(SRC));
}

// ------------------------------------ D. the pack card: seal, graphs, classes
{
    const packSrc = slice('function _packTagsHtml(', '// ── Browse Marketplace card');
    ok('D0 extraction is non-vacuous (pack card builders)', packSrc.length > 600);

    // Execute the shipped builders. renderProfileTag is stubbed to RECORD its call:
    // the colour itself is that function's own contract (and was verified live) —
    // what must be pinned here is that the pack card delegates to it with the class
    // the listing served, instead of hand-rolling a grey lookalike.
    const calls = [];
    const factory = new Function('escapeHtml', 'renderProfileTag',
        `${packSrc}\nreturn {_packTagsHtml,_packStatsHtml};`);
    const api = factory(x => String(x), (tag, type) => { calls.push([tag, type]); return `<span class="profile-tag">@${tag}</span>`; });

    const classes = { NIBRS: 'llm_only', PDOCS: 'rag_focused', NIBRSO: 'tool_enabled', POLICE: 'genie' };
    api._packTagsHtml(['NIBRS', 'PDOCS', 'NIBRSO', 'POLICE'], classes);
    check('D1 every member chip is THE profile tag, carrying its served class',
          calls, [['NIBRS', 'llm_only'], ['PDOCS', 'rag_focused'], ['NIBRSO', 'tool_enabled'], ['POLICE', 'genie']]);

    calls.length = 0;
    api._packTagsHtml(['@NIBRS'], classes);
    check('D2 a leading @ never defeats the class lookup', calls, [['NIBRS', 'llm_only']]);

    calls.length = 0;
    api._packTagsHtml(['MYSTERY'], classes);
    // null, never a default class — an unknown member is shown as unknown.
    check('D3 an unknown member is not given a guessed class', calls, [['MYSTERY', null]]);

    calls.length = 0;
    api._packTagsHtml(['NIBRS'], undefined);
    check('D4 a payload with no class map still renders the chip', calls, [['NIBRS', null]]);

    ok('D5 no hand-rolled lookalike pill survives in the tags builder',
       !/bg-white\/10[^`]*font-mono/.test(packSrc));

    // the ontology count
    const withGraphs = api._packStatsHtml({ profiles: 6, collections: 2, graphs: 3, subscribers: 0, rating: 0, ratingCount: 0, date: null });
    ok('D6 a known ontology count is stated', /3 knowledge graphs/.test(withGraphs));
    const oneGraph = api._packStatsHtml({ profiles: 1, collections: 0, graphs: 1, subscribers: null, rating: 0, ratingCount: 0, date: null });
    ok('D7 one ontology reads singular', /1 knowledge graph(?!s)/.test(oneGraph));
    const zeroGraphs = api._packStatsHtml({ profiles: 1, collections: 0, graphs: 0, subscribers: null, rating: 0, ratingCount: 0, date: null });
    ok('D8 zero is a fact and is stated', /0 knowledge graphs/.test(zeroGraphs));
    const noGraphs = api._packStatsHtml({ profiles: 1, collections: 0, graphs: null, subscribers: null, rating: 0, ratingCount: 0, date: null });
    ok('D9 an older payload that cannot report one says nothing, never a false 0',
       !/knowledge graph/.test(noGraphs));

    // both cards must ask for both facts, and each seal must key on ITS OWN artefact
    const browse = slice('function createAgentPackMarketplaceCard(', 'function createMyAgentPackCard(');
    const mine = slice('function createMyAgentPackCard(', 'function _packSyncButtonHtml(');
    ok('D10 the browse card passes the served class map', /_packTagsHtml\(pack\.profile_tags, pack\.profile_classes\)/.test(browse));
    ok('D11 the browse card states the ontology count', /graphs: pack\.kg_count/.test(browse));
    ok('D12 my-assets passes the served class map', /_packTagsHtml\(tags, pack\.profile_classes\)/.test(mine));
    ok('D13 my-assets states the ontology count', /graphs: pack\.kg_count/.test(mine));
    // The browse card shows the PUBLISHER's artefact; My Assets shows MINE. Keying
    // both on the same field is how the seal silently vanished on one of them.
    ok('D14 the browse seal keys on the publisher installation',
       /data-trust-market-id="\$\{pack\.source_installation_id \|\| ''\}"/.test(browse));
    ok('D15 the my-assets seal keys on my own installation',
       /data-trust-market-id="\$\{pack\.installation_id \|\|/.test(mine));
}

// the browse endpoint must SERVE the seal's key — it used to fetch it, use it, drop it
{
    const py = readFileSync('src/trusted_data_agent/api/agent_pack_routes.py', 'utf8');
    ok('D16 the listing serves the seal key', /pack\["source_installation_id"\] = src_id/.test(py));
    ok('D17 the listing serves the ontology count', /"kg_count": row\["kg_count"\]/.test(py));
    ok('D18 the listing serves each member class', /"profile_classes":/.test(py));
    const mgr = readFileSync('src/trusted_data_agent/core/agent_pack_manager.py', 'utf8');
    ok('D19 the installed list serves the ontology count', /"kg_count": counts\.get\("knowledge_graph", 0\)/.test(mgr));
    ok('D20 the installed list serves the member tags', /"profile_tags": profile_tags/.test(mgr));
    ok('D21 the installed list serves each member class', /"profile_classes":/.test(mgr));
}

// ----------------------------------------------------------------- verdict

// ---------------------------------------------------------------- E: the installed-pack card's info line
// The card showed Experts INSTEAD of Profiles for a coordinator pack, assuming
// every member is one of the coordinator's experts. The policing pack broke that
// on 2026-09-16 (3 experts + 3 standalone analysts + the coordinator = 7), so the
// card read "Experts: 3" and nothing on it said the pack ships 7 agents — while
// the marketplace card, harmonized to member chips, showed all 7. One pack, two
// surfaces, two answers. Executes the shipped builder.
{
    const APH = readFileSync('static/js/handlers/agentPackHandler.js', 'utf8');
    const a = APH.indexOf('function _packInfoItems('), b = APH.indexOf('// ── Render');
    ok('E0 extraction is non-vacuous (installed-pack info line)', a > 0 && b > a);
    const infoSrc = APH.slice(a, b);
    const api = new Function('_esc', `${infoSrc}\nreturn {_packInfoItems};`)(x => String(x));
    const items = (html) => [...html.matchAll(/<span>([A-Za-z ]+): <span class="text-gray-300">([^<]*)<\/span>/g)].map(m => [m[1], m[2]]);

    // the policing pack as both accounts serve it
    const policing = api._packInfoItems({ pack_type: 'genie', coordinator_tag: 'POLICE', experts_count: 3, profiles_count: 7, collections_count: 3, kg_count: 3 }, '15.9.2026');
    check('E1 a coordinator pack with standalone members states BOTH counts, experts first',
          items(policing), [['Coordinator', '@POLICE'], ['Experts', '3'], ['Profiles', '7'], ['Collections', '3'], ['Knowledge graphs', '3'], ['Installed', '15.9.2026']]);

    // a pack where every member IS an expert keeps the pre-existing line
    const vat = api._packInfoItems({ pack_type: 'genie', coordinator_tag: 'VAT', experts_count: 5, profiles_count: 6, collections_count: 4, kg_count: 0 }, '');
    check('E2 experts + coordinator == profiles: Experts only, no redundant Profiles',
          items(vat), [['Coordinator', '@VAT'], ['Experts', '5'], ['Collections', '4'], ['Knowledge graphs', '0']]);

    // a non-coordinator pack never says Experts
    const bundle = api._packInfoItems({ pack_type: 'bundle', experts_count: 0, profiles_count: 2, collections_count: 1, kg_count: null }, '');
    check('E3 a bundle states Profiles and never Experts',
          items(bundle), [['Profiles', '2'], ['Collections', '1']]);

    // an older payload with no profiles_count degrades to the old line, never a false count
    const legacy = api._packInfoItems({ pack_type: 'genie', coordinator_tag: 'X', experts_count: 3, collections_count: 1 }, '');
    check('E4 no profiles_count served: Experts alone, nothing invented',
          items(legacy), [['Coordinator', '@X'], ['Experts', '3'], ['Collections', '1']]);

    // and the render path uses the builder rather than a surviving inline copy
    ok('E5 renderAgentPacks delegates to the one builder', /const infoItems = _packInfoItems\(pack, installed\);/.test(APH));
    ok('E6 no inline Experts line survives outside the builder',
       (APH.match(/Experts: <span/g) || []).length === 1);
}

console.log(fail === 0
    ? `ALL PASS — ${pass} assertions (marketplace visibility: portal + scope).`
    : `FAILED — ${fail} of ${pass + fail} assertions.`);
process.exit(fail === 0 ? 0 : 1);
