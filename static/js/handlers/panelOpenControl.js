/**
 * panelOpenControl.js — the ONE control that opens a component's side panel from its
 * card in the conversation.
 *
 * Every component card (canvas, flow, flow run, dashboard, knowledge graph, scheduler,
 * memory, database navigator, EXPLAIN plan) renders the same button through html(), so
 * all of them look the same, say the same words and behave the same:
 *
 *   - Rule: the button reads "Expanded in side panel →" exactly when the panel is open
 *     on THIS card's item. Clicking it then closes the panel; clicking any other card's
 *     button opens (or switches) the panel to that card's item.
 *   - State: each panel reports what it shows through shown(component, id) when it
 *     opens, switches item and closes (id null). Every button of that component is
 *     re-derived from that one record — the switcher, auto-open and restore paths
 *     included, so two cards can never both read "Expanded".
 *   - Single-panel components (scheduler, memory, navigator) use the id '*': any open
 *     panel is "their" panel.
 *   - Clicks are delegated from document, so a card rendered as static HTML (the
 *     flow-run card, a reloaded answer) works without per-element wiring. A component
 *     registers its open/close handlers when its renderer module loads; a click on a
 *     card whose renderer is not loaded yet imports it first. Renderers register through
 *     window.__panelOpenQueue (see drain), so the load order never matters.
 *
 * Exposed on window.PanelOpenControl so component renderer modules (served from
 * /api/v1/components/<id>/renderer) can use it without a module import. Styling lives
 * in static/css/split-panels.css (.sp-open-row / .sp-open-ctl).
 */

const ANY = '*';
const LABEL = 'View in Canvas';
const EXPANDED_LABEL = 'Expanded in side panel';

const _shown = Object.create(null);     // component -> shown id (string) | null
const _handlers = Object.create(null);  // component -> { open(id, btn), close(id, btn) }

function _esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _key(id) { return id == null || id === '' ? ANY : String(id); }

/** True when the panel of `component` is open on the item `id` ('*' = any item). */
function isExpanded(component, id) {
    const s = _shown[component];
    if (s == null) return false;
    const k = _key(id);
    return k === ANY || s === ANY || s === k;
}

/** The id the panel of `component` currently shows, or null when it is closed. */
function shownId(component) {
    return _shown[component] == null ? null : _shown[component];
}

const _PANEL_ICON = '<svg class="sp-open-ctl-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/></svg>';

/**
 * The button markup. `accent` is the component's manifest icon_accent (the panel's own
 * colour); `panelId` the id of the <aside> it opens (aria-controls); `name` names the
 * item for screen readers ("Open Sales report in the side panel").
 */
function html({ component, id = ANY, accent = '', panelId = '', name = '', row = true } = {}) {
    const k = _key(id);
    const on = isExpanded(component, k);
    // The component's live manifest accent wins (window.__componentAccents, the same map
    // that colours its panel); `accent` is the renderer's fallback.
    let live = '';
    try { live = ((typeof window !== 'undefined' && window.__componentAccents) || {})[component] || ''; } catch (_) {}
    const acc = live || accent;
    const style = acc ? ` style="--sp-accent:${_esc(acc)}"` : '';
    const ctl = `<button type="button" class="sp-open-ctl${on ? ' is-expanded' : ''}"`
        + ` data-sp-open="${_esc(component)}" data-sp-open-id="${_esc(k)}"`
        + (panelId ? ` aria-controls="${_esc(panelId)}"` : '')
        + (name ? ` data-sp-open-name="${_esc(name)}"` : '')
        + ` aria-expanded="${on}" aria-label="${_esc(_aria(on, name))}"${style}>`
        + `${_PANEL_ICON}<span class="sp-open-ctl-label">${on ? EXPANDED_LABEL : LABEL}</span>`
        + `<span class="sp-open-ctl-arrow" aria-hidden="true">&rarr;</span></button>`;
    return row ? `<div class="sp-open-row">${ctl}</div>` : ctl;
}

function _aria(on, name) {
    const what = name ? `${name} ` : '';
    return on ? `Close ${what}side panel` : `Open ${what}in the side panel`;
}

/** Re-derive one button from the shared record. */
function apply(btn) {
    if (!btn || !btn.dataset) return;
    const on = isExpanded(btn.dataset.spOpen, btn.dataset.spOpenId);
    btn.classList.toggle('is-expanded', on);
    btn.setAttribute('aria-expanded', String(on));
    btn.setAttribute('aria-label', _aria(on, btn.dataset.spOpenName || ''));
    const label = btn.querySelector('.sp-open-ctl-label');
    if (label) label.textContent = on ? EXPANDED_LABEL : LABEL;
}

/** A panel reports what it now shows (null = closed). Every button follows. */
function shown(component, id) {
    _shown[component] = id == null ? null : _key(id);
    if (typeof document !== 'undefined') {
        document.querySelectorAll(`.sp-open-ctl[data-sp-open="${component}"]`).forEach(apply);
        try {
            UderiaBus.emit('uderia:panel-state', { component, id: _shown[component] });
        } catch (_) { /* no CustomEvent (test stub) */ }
    }
}

/** A component's renderer registers how to open its panel on an item and close it. */
function register(component, handlers) {
    _handlers[component] = handlers;
}

/** Renderers may load BEFORE this module (the component renderers are imported early),
 *  so they never call register() directly: they push [component, handlers] onto
 *  window.__panelOpenQueue and this module drains it — at load and before every click. */
function drain() {
    if (typeof window === 'undefined') return;
    const q = window.__panelOpenQueue;
    while (q && q.length) { const [c, h] = q.shift(); if (c && h) _handlers[c] = h; }
}

async function _handlerFor(component) {
    drain();
    if (_handlers[component]) return _handlers[component];
    try { await import(`/api/v1/components/${component}/renderer`); } catch (e) {
        console.warn(`[PanelOpenControl] could not load the ${component} renderer`, e);
    }
    drain();
    return _handlers[component] || null;
}

async function _onClick(btn) {
    const component = btn.dataset.spOpen;
    const id = btn.dataset.spOpenId;
    const h = await _handlerFor(component);
    if (!h) return;
    if (isExpanded(component, id)) await h.close(id, btn);
    else await h.open(id, btn);
}

const _PIN_ICON = '<svg class="sp-open-ctl-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17z"/></svg>';

/**
 * A row's acts sit together. An answer row whose card has this control (plan, flow,
 * knowledge graph …) keeps its other acts — today "Pin to dashboard", drawn hidden in the
 * row footer — on the card's button row, to the left of the control, as the secondary
 * variant of the same pill (`.sp-row-act`): same height, radius, type and focus ring,
 * neutral until hovered. The emptied footer is removed, so the row adds no line of its
 * own. A row with no control (a chart, a table) keeps its footer and its text-link acts.
 * Idempotent; runs when a control is inserted and when the row's acts are wired.
 */
function adoptRowActs(scope) {
    if (!scope || !scope.querySelectorAll) return;
    const rows = [];
    if (scope.matches && scope.matches('.ans-ev')) rows.push(scope);
    else if (scope.closest && scope.closest('.ans-ev')) rows.push(scope.closest('.ans-ev'));
    scope.querySelectorAll('.ans-ev').forEach(r => rows.push(r));
    for (const row of rows) {
        const ctl = row.querySelector('.sp-open-ctl');
        if (!ctl || !ctl.parentElement) continue;
        row.querySelectorAll('.ans-pin').forEach((act) => {
            const footer = act.closest('.ans-tf');
            if (!footer) return;   // already on the button row
            act.classList.add('sp-row-act');
            act.classList.remove('ans-act');   // the footer's text-link style; the row uses the pill
            if (!act.querySelector('.sp-open-ctl-icon')) act.insertAdjacentHTML('afterbegin', _PIN_ICON);
            ctl.parentElement.insertBefore(act, ctl);
            if (!footer.querySelector('button') && !footer.querySelector('details')) footer.remove();
        });
    }
}

if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('.sp-open-ctl') : null;
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        _onClick(btn);
    });
    // A card inserted as static HTML (flow run, reloaded answer) takes the live state.
    if (typeof MutationObserver !== 'undefined' && document.body) {
        new MutationObserver((muts) => {
            for (const m of muts) {
                for (const n of m.addedNodes) {
                    if (n.nodeType !== 1) continue;
                    if (n.matches && n.matches('.sp-open-ctl')) { apply(n); adoptRowActs(n); }
                    else if (n.querySelectorAll) {
                        const ctls = n.querySelectorAll('.sp-open-ctl');
                        ctls.forEach(apply);
                        if (ctls.length) adoptRowActs(n);
                    }
                }
            }
        }).observe(document.body, { childList: true, subtree: true });
    }
}

const PanelOpenControl = { ANY, LABEL, EXPANDED_LABEL, html, apply, shown, shownId, isExpanded, register, drain, adoptRowActs, _onClick };
if (typeof window !== 'undefined') { window.PanelOpenControl = PanelOpenControl; drain(); }
export default PanelOpenControl;
