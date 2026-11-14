/**
 * customSelect.js
 *
 * Eliminates OS-default <select> dropdown popups across the whole app.
 *
 * A native <select>'s OPEN option list is always painted by the operating
 * system and cannot be themed with CSS. This module progressively enhances
 * every <select> into a theme-compliant custom dropdown:
 *
 *   - The underlying <select> stays in the DOM as the single source of truth
 *     (value, change/input events, form submission, option population). All
 *     existing code that reads `.value` or listens for `change` keeps working.
 *   - A themed trigger button + portal popup list render the visible UI using
 *     the same CSS variables and look as the rest of the application.
 *   - Programmatic `.value` / `.selectedIndex` writes are proxied so the
 *     trigger stays in sync, and a MutationObserver re-enhances selects that
 *     are added or re-populated at runtime (component renderers, modals, etc.).
 *
 * Opt out per element with `data-no-enhance` (on the <select> or any ancestor).
 *
 * A LONG list filters: a select with more than FILTER_MIN options (or `data-filterable`)
 * opens with a filter field — typing narrows the options by their label, the arrows and
 * Enter work on what is left, and a key typed on the closed trigger opens it with that key
 * already in the filter. `data-no-filter` opts out.
 * `multiple` and `size > 1` selects are left native (list boxes, not popups).
 *
 * Theme-aware (CSS variables only) and monochrome icons only — no emoji.
 * Self-initialising on import; no template/server changes required.
 */

const NS = 'cs';
const FILTER_MIN = 10;   // more options than this and the open list carries a filter field
const protoValDesc = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
const protoIdxDesc = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'selectedIndex');

let _cssInjected = false;
let _openController = null; // { close } of the currently open menu

// ── Styling ───────────────────────────────────────────────────────────────
function injectCSS() {
    if (_cssInjected) return;
    _cssInjected = true;
    const style = document.createElement('style');
    style.id = 'cs-custom-select-styles';
    style.textContent = `
    .cs-wrap { position: relative; display: inline-block; vertical-align: middle; box-sizing: border-box; }
    .cs-wrap > select.cs-native { position: absolute !important; width: 1px !important; height: 1px !important;
        padding: 0 !important; margin: -1px !important; border: 0 !important; clip: rect(0 0 0 0);
        clip-path: inset(50%); overflow: hidden !important; opacity: 0 !important; pointer-events: none !important; }

    .cs-trigger {
        display: flex; align-items: center; gap: 8px; width: 100%; box-sizing: border-box;
        padding: 7px 10px; min-height: 34px;
        font-size: 0.85rem; line-height: 1.2; text-align: left;
        color: var(--text-primary, #e2e8f0);
        background: var(--input-bg, var(--card-bg, rgba(15, 23, 42, 0.6)));
        border: 1px solid var(--border-primary, rgba(148, 163, 184, 0.25));
        border-radius: var(--radius-md, 8px);
        cursor: pointer; user-select: none;
        transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
    }
    .cs-trigger:hover { border-color: var(--border-strong, rgba(148, 163, 184, 0.45)); }
    .cs-trigger:focus-visible, .cs-trigger.cs-open {
        outline: none;
        border-color: var(--teradata-orange, #F15F22);
        box-shadow: 0 0 0 2px color-mix(in srgb, var(--teradata-orange, #F15F22) 25%, transparent);
    }
    .cs-trigger.cs-disabled { opacity: 0.55; cursor: not-allowed; }
    .cs-trigger-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .cs-trigger-label.cs-placeholder { color: var(--text-muted, #94a3b8); }
    .cs-trigger-chevron { flex-shrink: 0; color: var(--text-muted, #94a3b8); transition: transform 0.18s; }
    .cs-trigger.cs-open .cs-trigger-chevron { transform: rotate(180deg); }

    .cs-menu {
        position: fixed; z-index: 10050; box-sizing: border-box;
        min-width: 160px; max-height: 320px; overflow: hidden auto;
        padding: 4px;
        background: var(--bg-overlay, var(--card-bg, rgba(15, 23, 42, 0.98)));
        border: 1px solid var(--border-primary, rgba(148, 163, 184, 0.18));
        border-radius: 10px;
        box-shadow: var(--shadow-xl, 0 20px 40px rgba(0, 0, 0, 0.5));
        backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
        animation: cs-menu-in 0.14s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes cs-menu-in { from { opacity: 0; transform: translateY(-6px) scale(0.98); } to { opacity: 1; transform: translateY(0) scale(1); } }
    .cs-group-label {
        padding: 6px 10px 3px; font-size: 0.68rem; font-weight: 700; letter-spacing: 0.04em;
        text-transform: uppercase; color: var(--text-muted, #94a3b8);
    }
    .cs-option {
        display: flex; align-items: center; gap: 8px; padding: 7px 10px;
        font-size: 0.84rem; color: var(--text-primary, #e2e8f0);
        border-radius: 6px; cursor: pointer; white-space: nowrap;
    }
    .cs-option-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .cs-option-check { flex-shrink: 0; width: 14px; height: 14px; opacity: 0; color: var(--teradata-orange, #F15F22); }
    .cs-option.cs-selected .cs-option-check { opacity: 1; }
    .cs-option.cs-selected { color: var(--text-primary, #fff); font-weight: 600; }
    .cs-option:hover:not(.cs-option-disabled),
    .cs-option.cs-active:not(.cs-option-disabled) { background: var(--hover-bg-strong, rgba(148, 163, 184, 0.14)); }
    .cs-option-disabled { opacity: 0.45; cursor: not-allowed; }
    .cs-filter { position: sticky; top: -4px; margin: -4px -4px 4px; padding: 6px;
        background: var(--bg-overlay, var(--card-bg, rgba(15, 23, 42, 0.98)));
        border-bottom: 1px solid var(--border-secondary, rgba(148, 163, 184, 0.14)); z-index: 1; }
    .cs-filter-input { width: 100%; box-sizing: border-box; padding: 6px 9px; font-size: 0.82rem;
        color: var(--text-primary, #e2e8f0); background: var(--input-bg, var(--bg-primary, rgba(15, 23, 42, 0.6)));
        border: 1px solid var(--border-primary, rgba(148, 163, 184, 0.25)); border-radius: 6px; outline: none; }
    .cs-filter-input:focus { border-color: var(--teradata-orange, #F15F22); }
    .cs-option.cs-filtered, .cs-group-label.cs-filtered { display: none; }
    .cs-nomatch { padding: 8px 10px; font-size: 0.8rem; color: var(--text-muted, #94a3b8); }
    `;
    (document.head || document.documentElement).appendChild(style);
}

const CHEVRON = '<svg class="cs-trigger-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
const CHECK = '<svg class="cs-option-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

// ── Per-select enhancement ─────────────────────────────────────────────────
function shouldSkip(sel) {
    if (!sel || sel.tagName !== 'SELECT') return true;
    if (sel.dataset.csEnhanced === '1') return true;
    if (sel.multiple || sel.size > 1) return true;
    if (sel.hasAttribute('data-no-enhance')) return true;
    if (sel.closest('[data-no-enhance]')) return true;
    return false;
}

function enhanceSelect(sel) {
    if (shouldSkip(sel)) return;
    injectCSS();
    sel.dataset.csEnhanced = '1';

    // Wrap the native select; copy layout-affecting attributes so position/width are preserved.
    const wrap = document.createElement('div');
    wrap.className = 'cs-wrap';
    const cs = getComputedStyle(sel);
    const fullWidth = sel.classList.contains('w-full') || cs.width === '100%' ||
        cs.display === 'block' || (sel.style && sel.style.width === '100%');
    if (fullWidth) {
        wrap.style.display = 'block';
        wrap.style.width = '100%';
    } else {
        const w = parseFloat(cs.width);
        if (w > 0) wrap.style.width = cs.width;
    }
    wrap.style.margin = cs.margin;

    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.classList.add('cs-native');
    sel.setAttribute('tabindex', '-1');
    sel.setAttribute('aria-hidden', 'true');

    // Trigger button.
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'cs-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML = `<span class="cs-trigger-label"></span>${CHEVRON}`;
    wrap.appendChild(trigger);
    const labelEl = trigger.querySelector('.cs-trigger-label');

    let menu = null;
    let filterInput = null;
    let activeIndex = -1;
    const filterable = () => !sel.hasAttribute('data-no-filter') &&
        (sel.hasAttribute('data-filterable') || sel.options.length > FILTER_MIN);
    let typeahead = '';
    let typeaheadTimer = null;

    function optionList() {
        return Array.from(sel.options);
    }

    function syncTrigger() {
        const opt = sel.options[sel.selectedIndex];
        const txt = opt ? (opt.textContent || '').trim() : '';
        const isPlaceholder = !opt || opt.value === '' || (txt === '' && sel.selectedIndex <= 0);
        labelEl.textContent = txt || (opt ? '' : 'Select…');
        labelEl.classList.toggle('cs-placeholder', isPlaceholder && (!txt || txt === ''));
        const disabled = sel.disabled;
        trigger.classList.toggle('cs-disabled', disabled);
        trigger.disabled = disabled;
        if (menu) renderMenuSelection();
    }

    function renderMenuSelection() {
        if (!menu) return;
        menu.querySelectorAll('.cs-option').forEach(el => {
            const i = Number(el.dataset.index);
            el.classList.toggle('cs-selected', i === sel.selectedIndex);
        });
    }

    function buildMenu() {
        menu = document.createElement('div');
        menu.className = 'cs-menu';
        menu.setAttribute('role', 'listbox');

        let html = filterable()
            ? `<div class="cs-filter"><input type="text" class="cs-filter-input" placeholder="Filter ${sel.options.length} options…"
                 aria-label="Filter options" autocomplete="off" spellcheck="false"></div>` : '';
        let group = 0;
        const renderOpt = (opt) => {
            const i = opt.index;
            const disabled = opt.disabled;
            const selected = i === sel.selectedIndex;
            if (opt.hidden) return;
            html += `<div class="cs-option${selected ? ' cs-selected' : ''}${disabled ? ' cs-option-disabled' : ''}"
                role="option" aria-selected="${selected}" data-index="${i}" data-group="${group}">
                ${CHECK}<span class="cs-option-label" title="${escapeHtml(opt.textContent.trim() || opt.value)}">${escapeHtml(opt.textContent.trim() || opt.value)}</span></div>`;
        };

        // Preserve <optgroup> structure.
        const groups = sel.querySelectorAll('optgroup');
        if (groups.length) {
            Array.from(sel.children).forEach(child => {
                if (child.tagName === 'OPTGROUP') {
                    group += 1;
                    html += `<div class="cs-group-label" data-group="${group}">${escapeHtml(child.label)}</div>`;
                    Array.from(child.children).forEach(renderOpt);
                } else if (child.tagName === 'OPTION') {
                    renderOpt(child);
                }
            });
        } else {
            optionList().forEach(renderOpt);
        }
        menu.innerHTML = html + '<div class="cs-nomatch" hidden>No match</div>';

        // keep focus on the trigger — except in the filter field, which takes the typing
        menu.addEventListener('mousedown', e => { if (!e.target.closest('.cs-filter')) e.preventDefault(); });
        filterInput = menu.querySelector('.cs-filter-input');
        if (filterInput) {
            filterInput.addEventListener('input', applyFilter);
            filterInput.addEventListener('keydown', e => {
                switch (e.key) {
                    case 'ArrowDown': e.preventDefault(); moveActive(1); break;
                    case 'ArrowUp': e.preventDefault(); moveActive(-1); break;
                    case 'Enter': e.preventDefault(); if (activeIndex >= 0 && isShown(activeIndex)) pick(activeIndex); break;
                    case 'Escape': e.preventDefault(); close(); trigger.focus(); break;
                    case 'Tab': close(); break;
                    default: break;
                }
            });
        }
        menu.addEventListener('click', e => {
            const optEl = e.target.closest('.cs-option');
            if (!optEl || optEl.classList.contains('cs-option-disabled')) return;
            pick(Number(optEl.dataset.index));
        });
        document.body.appendChild(menu);
    }

    // an option is shown unless the filter hides it
    function isShown(i) {
        if (!menu) return true;
        const el = menu.querySelector(`.cs-option[data-index="${i}"]`);
        return !!el && !el.classList.contains('cs-filtered');
    }

    function applyFilter() {
        if (!menu || !filterInput) return;
        const q = filterInput.value.trim().toLowerCase();
        let shown = 0;
        const groupsShown = new Set();
        menu.querySelectorAll('.cs-option').forEach(el => {
            const opt = sel.options[Number(el.dataset.index)];
            const hay = `${(opt && opt.textContent) || ''} ${(opt && opt.value) || ''}`.toLowerCase();
            const hit = !q || hay.includes(q);
            el.classList.toggle('cs-filtered', !hit);
            if (hit) { shown += 1; groupsShown.add(el.dataset.group); }
        });
        menu.querySelectorAll('.cs-group-label').forEach(g => g.classList.toggle('cs-filtered', !groupsShown.has(g.dataset.group)));
        const none = menu.querySelector('.cs-nomatch');
        if (none) none.hidden = shown > 0;
        if (!isShown(activeIndex)) {
            const first = optionList().find(o => !o.disabled && !o.hidden && isShown(o.index));
            setActive(first ? first.index : -1);
        }
    }

    function positionMenu() {
        if (!menu) return;
        const r = trigger.getBoundingClientRect();
        // As wide as its longest option (never narrower than the trigger), within the viewport:
        // an option cut to "Writes through: Polici…" cannot be told from its neighbours
        menu.style.width = 'max-content';
        // the LAYOUT width (offsetWidth — the opening animation scales the box, so a rect
        // measures it short) + the scrollbar a long list adds (it takes its width from the options)
        const natural = menu.offsetWidth + (menu.offsetWidth - menu.clientWidth) + 2;
        const width = Math.min(Math.max(r.width, 160, natural), window.innerWidth - 16);
        menu.style.width = width + 'px';
        // starts under the trigger; a menu wider than the room to its right aligns to its right edge
        let left = r.left;
        if (left + width > window.innerWidth - 8) left = Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8));
        menu.style.left = left + 'px';
        // Measure then decide up/down.
        menu.style.top = '-9999px';
        const mh = menu.offsetHeight;
        const below = window.innerHeight - r.bottom;
        if (below < mh + 8 && r.top > below) {
            menu.style.top = Math.max(8, r.top - mh - 4) + 'px';
        } else {
            menu.style.top = (r.bottom + 4) + 'px';
        }
    }

    function setActive(i) {
        activeIndex = i;
        if (!menu) return;
        menu.querySelectorAll('.cs-option').forEach(el => {
            const active = Number(el.dataset.index) === i;
            el.classList.toggle('cs-active', active);
            if (active) el.scrollIntoView({ block: 'nearest' });
        });
    }

    function open(initialFilter) {
        if (menu || sel.disabled) return;
        if (_openController) _openController.close();
        buildMenu();
        positionMenu();
        trigger.classList.add('cs-open');
        trigger.setAttribute('aria-expanded', 'true');
        setActive(sel.selectedIndex >= 0 ? sel.selectedIndex : firstEnabledIndex());
        if (filterInput) { filterInput.value = initialFilter || ''; applyFilter(); filterInput.focus(); }
        _openController = { close };
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', close);
        document.addEventListener('mousedown', onDocDown, true);
    }

    function close() {
        if (!menu) return;
        menu.remove();
        menu = null;
        filterInput = null;
        trigger.classList.remove('cs-open');
        trigger.setAttribute('aria-expanded', 'false');
        if (_openController && _openController.close === close) _openController = null;
        window.removeEventListener('scroll', onScroll, true);
        window.removeEventListener('resize', close);
        document.removeEventListener('mousedown', onDocDown, true);
    }

    function onScroll(e) {
        if (menu && e.target instanceof Node && menu.contains(e.target)) return;
        positionMenu();
    }
    function onDocDown(e) {
        if (menu && (menu.contains(e.target) || trigger.contains(e.target))) return;
        close();
    }

    function firstEnabledIndex() {
        const o = optionList().find(x => !x.disabled && !x.hidden);
        return o ? o.index : -1;
    }

    function pick(index) {
        const opt = sel.options[index];
        if (!opt || opt.disabled) return;
        const prev = sel.value;
        protoIdxDesc.set.call(sel, index);
        syncTrigger();
        if (sel.value !== prev) {
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
        close();
        trigger.focus();
    }

    function moveActive(dir) {
        const opts = optionList();
        if (!opts.length) return;
        let i = activeIndex;
        for (let n = 0; n < opts.length; n++) {
            i = (i + dir + opts.length) % opts.length;
            if (!opts[i].disabled && !opts[i].hidden && isShown(i)) { setActive(i); break; }
        }
    }

    function doTypeahead(ch) {
        clearTimeout(typeaheadTimer);
        typeahead += ch.toLowerCase();
        typeaheadTimer = setTimeout(() => { typeahead = ''; }, 600);
        const opts = optionList();
        const match = opts.find(o => !o.disabled && !o.hidden &&
            (o.textContent || '').trim().toLowerCase().startsWith(typeahead));
        if (match) { if (menu) setActive(match.index); else pick(match.index); }
    }

    // Trigger interactions.
    trigger.addEventListener('click', () => { menu ? close() : open(); });
    // a filterable list: a key typed on the closed trigger opens it, filtering by that key
    trigger.addEventListener('keydown', e => {
        if (sel.disabled) return;
        switch (e.key) {
            case 'ArrowDown': e.preventDefault(); menu ? moveActive(1) : open(); break;
            case 'ArrowUp': e.preventDefault(); menu ? moveActive(-1) : open(); break;
            case 'Enter':
            case ' ':
                e.preventDefault();
                if (menu) { if (activeIndex >= 0) pick(activeIndex); }
                else open();
                break;
            case 'Escape': if (menu) { e.preventDefault(); close(); } break;
            case 'Tab': if (menu) close(); break;
            case 'Home': if (menu) { e.preventDefault(); setActive(firstEnabledIndex()); } break;
            default:
                if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
                    if (!menu && filterable()) { e.preventDefault(); open(e.key); }
                    else doTypeahead(e.key);
                }
        }
    });

    // Keep the trigger in sync with native changes (dispatched events).
    sel.addEventListener('change', syncTrigger);

    // Proxy programmatic value / selectedIndex writes so the trigger updates.
    try {
        Object.defineProperty(sel, 'value', {
            configurable: true,
            get() { return protoValDesc.get.call(this); },
            set(v) { protoValDesc.set.call(this, v); syncTrigger(); }
        });
        Object.defineProperty(sel, 'selectedIndex', {
            configurable: true,
            get() { return protoIdxDesc.get.call(this); },
            set(v) { protoIdxDesc.set.call(this, v); syncTrigger(); }
        });
    } catch (_) { /* non-configurable in some engines — change listener still covers most cases */ }

    // Re-render when options or disabled state change at runtime.
    const mo = new MutationObserver(() => { if (menu) { close(); } syncTrigger(); });
    mo.observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected', 'value'] });

    syncTrigger();
}

// ── Bulk + observer ─────────────────────────────────────────────────────────
function enhanceAll(root = document) {
    try {
        (root.querySelectorAll ? root.querySelectorAll('select') : []).forEach(enhanceSelect);
    } catch (_) { /* ignore */ }
}

let _debounce = null;
function startObserver() {
    const obs = new MutationObserver(muts => {
        let found = false;
        for (const m of muts) {
            for (const node of m.addedNodes) {
                if (node.nodeType !== 1) continue;
                if (node.tagName === 'SELECT' || (node.querySelector && node.querySelector('select'))) { found = true; break; }
            }
            if (found) break;
        }
        if (!found) return;
        clearTimeout(_debounce);
        _debounce = setTimeout(() => enhanceAll(document), 30);
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
}

function init() {
    injectCSS();
    enhanceAll(document);
    startObserver();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// Public API for explicit/manual use.
window.enhanceSelects = enhanceAll;
window.enhanceSelect = enhanceSelect;

export { enhanceAll as enhanceSelects, enhanceSelect };
