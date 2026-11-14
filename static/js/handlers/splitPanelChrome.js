/**
 * splitPanelChrome.js — shared instance-selector dropdown for component split panels.
 *
 * Used by the canvas, knowledge-graph, and dashboard switcher buttons (`.sp-switcher`).
 * Renders a position:fixed portal menu under the button, lists instances from a REST
 * endpoint, highlights the current one, dismisses on outside-click/Esc/scroll, and
 * calls onSelect(id, item). The only component-specific piece is `onSelect`.
 *
 * Exposed on `window.mountSplitPanelSwitcher` so component renderer modules (served
 * from /api/v1/components/<id>/renderer) can call it without a module import.
 * Styling is entirely in static/css/split-panels.css (.sp-switcher-menu / -item).
 */

function _authHeaders() {
    const t = localStorage.getItem('tda_auth_token');
    return t ? { 'Authorization': `Bearer ${t}` } : {};
}

function _esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const _PENCIL_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>`;

/** Turn a menu line into an inline name field (the shared way to name a new instance or
 *  rename one). Enter submits a non-empty name; Escape gives the line back. onSubmit may
 *  return a string — a refusal (e.g. "name in use") shown under the field, which stays
 *  open so the name can be corrected. */
function _inlineName(line, { placeholder, value, submitLabel, onSubmit, onCancel }) {
    const label = line.innerHTML;
    line.classList.add('sp-switcher-naming');
    line.innerHTML = `<input class="sp-switcher-input" type="text" maxlength="120" aria-label="${_esc(placeholder)}" placeholder="${_esc(placeholder)}">`
        + `<button type="button" class="sp-switcher-ok">${_esc(submitLabel)}</button>`;
    const input = line.querySelector('input');
    const ok = line.querySelector('.sp-switcher-ok');
    input.value = value || '';
    let busy = false;
    const submit = async () => {
        const name = input.value.trim();
        if (!name) { input.focus(); input.classList.add('is-invalid'); return; }
        if (busy) return;
        busy = true; ok.disabled = true;
        let refusal = null;
        try { refusal = await onSubmit(name); } finally { busy = false; ok.disabled = false; }
        if (typeof refusal === 'string' && refusal && line.isConnected) {
            input.classList.add('is-invalid');
            let err = line.querySelector('.sp-switcher-err');
            if (!err) {
                err = document.createElement('div');
                err.className = 'sp-switcher-err';
                err.setAttribute('role', 'alert');
                line.appendChild(err);
            }
            err.textContent = refusal;
            input.focus();
        }
    };
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); submit(); }
        else if (e.key === 'Escape') {
            e.preventDefault(); e.stopPropagation();
            line.classList.remove('sp-switcher-naming'); line.innerHTML = label; if (onCancel) onCancel();
        }
    });
    input.addEventListener('input', () => {
        input.classList.remove('is-invalid');
        const err = line.querySelector('.sp-switcher-err'); if (err) err.remove();
    });
    ok.addEventListener('click', (e) => { e.stopPropagation(); submit(); });
    input.addEventListener('click', (e) => e.stopPropagation());
    setTimeout(() => { input.focus(); input.select(); }, 0);
}

const _PLUS_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;

/**
 * @param {object} opts
 *   btnEl        HTMLElement   the `.sp-switcher` button (required)
 *   listUrl      string        REST endpoint returning the instance list (required unless `items`)
 *   items        ()=>array?    the instance list held in the browser (no fetch) — e.g. a panel's history
 *   itemsKey     string?       JSON key holding the array (omit if the body IS the array)
 *   idField      string        item field for the id (required)
 *   labelField   string        item field for the display name (required)
 *   metaField    string?       item field for small right-aligned meta text
 *   metaSuffix   string?       suffix appended to metaField value (e.g. ' tiles')
 *   getCurrentId ()=>id        returns the currently-open instance id (for highlight + no-op reselect)
 *   onSelect     async(id,item) performs the actual switch (required)
 *   newLabel     string?       footer "create new" label
 *   onNew        (name?)=>void  footer handler. With `newPrompt` the footer line becomes an
 *                               inline name field and onNew receives the trimmed name;
 *                               without it, onNew() is called at once (e.g. to route elsewhere).
 *                               Returning a string refuses the name (shown under the field).
 *   newPrompt    string?       placeholder of the inline name field for a new instance
 *   onRename     async(id,name,item)=>bool?  offers "Rename" for the OPEN instance (the one
 *                               getCurrentId names); the field is prefilled with its name;
 *                               a true result updates the title in place; a string refuses
 *                               the name (shown under the field, which stays open)
 *   emptyLabel   string?       text shown when the list is empty
 * @returns {()=>void} unmount
 */
export function mountSplitPanelSwitcher(opts) {
    const btnEl = opts && opts.btnEl;
    if (!btnEl) return () => {};
    // Idempotent: re-mounting the same button is a no-op (keeps the first wiring).
    if (btnEl._spSwitcherMounted) return btnEl._spSwitcherUnmount || (() => {});
    btnEl._spSwitcherMounted = true;

    let menu = null;

    function close() {
        if (menu) { menu.remove(); menu = null; }
        document.removeEventListener('click', onDocClick, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('resize', close);
        window.removeEventListener('scroll', close, true);
    }
    function onDocClick(e) {
        if (menu && !menu.contains(e.target) && !btnEl.contains(e.target)) close();
    }
    function onKey(e) { if (e.key === 'Escape') close(); }

    async function open() {
        let items = [];
        if (typeof opts.items === 'function') {
            try { items = opts.items() || []; } catch (_) { items = []; }
        } else try {
            // cache:'no-store' — the dropdown must reflect instances created since
            // the panel opened (a freshly created canvas/dashboard/KG). Without it,
            // Chrome's in-memory cache can replay an earlier identical GET and the
            // new instance never appears until the cache is evicted (e.g. a session
            // switch), which is why the list looked stale.
            const r = await fetch(opts.listUrl, { headers: _authHeaders(), cache: 'no-store' });
            if (r.ok) {
                const data = await r.json();
                items = opts.itemsKey
                    ? (Array.isArray(data[opts.itemsKey]) ? data[opts.itemsKey] : [])
                    : (Array.isArray(data) ? data : []);
            }
        } catch (_) { /* show empty */ }

        const curId = opts.getCurrentId ? opts.getCurrentId() : null;
        menu = document.createElement('div');
        menu.className = 'sp-switcher-menu';
        // The menu is portalled to <body>, so the panel's --sp-accent doesn't cascade.
        // Copy it from the button so is-current/new tints keep the panel's identity hue.
        const accent = getComputedStyle(btnEl).getPropertyValue('--sp-accent').trim();
        if (accent) menu.style.setProperty('--sp-accent', accent);

        if (items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'sp-switcher-empty';
            empty.textContent = opts.emptyLabel || 'Nothing here yet';
            menu.appendChild(empty);
        } else {
            for (const item of items) {
                const id = opts.idFn ? opts.idFn(item) : item[opts.idField];
                const name = (opts.labelFn ? opts.labelFn(item) : item[opts.labelField]) || 'Untitled';
                const row = document.createElement('div');
                row.className = 'sp-switcher-item' + (id === curId ? ' is-current' : '');
                let metaHtml = '';
                if (opts.metaField != null && item[opts.metaField] != null) {
                    metaHtml = `<span class="sp-switcher-item-meta">${_esc(item[opts.metaField])}${_esc(opts.metaSuffix || '')}</span>`;
                }
                row.innerHTML =
                    `<span class="sp-switcher-item-dot"></span>` +
                    `<span class="sp-switcher-item-name" title="${_esc(name)}">${_esc(name)}</span>` +
                    metaHtml;
                row.addEventListener('click', async () => {
                    close();
                    if (id === curId) return;  // no-op reselect
                    const nameEl = btnEl.querySelector('.sp-switcher-name');
                    if (nameEl) nameEl.textContent = name;  // optimistic label update
                    try { await opts.onSelect(id, item); }
                    catch (e) { console.error('[SplitPanelSwitcher] onSelect failed:', e); }
                });
                menu.appendChild(row);
            }
        }

        const setTitle = (name) => { const el = btnEl.querySelector('.sp-switcher-name'); if (el) el.textContent = name; };
        const cur = items.find(it => (opts.idFn ? opts.idFn(it) : it[opts.idField]) === curId) || null;
        if (opts.onRename && cur) {
            const curName = (opts.labelFn ? opts.labelFn(cur) : cur[opts.labelField]) || '';
            const rn = document.createElement('div');
            rn.className = 'sp-switcher-new sp-switcher-rename';
            rn.setAttribute('role', 'button'); rn.tabIndex = 0;
            rn.innerHTML = _PENCIL_ICON + `<span>Rename “${_esc(curName)}”</span>`;
            const startRename = () => {
                if (rn.classList.contains('sp-switcher-naming')) return;
                _inlineName(rn, { placeholder: 'New name', value: curName, submitLabel: 'Rename',
                    onSubmit: async (name) => {
                        if (name === curName) { close(); return; }
                        let r;
                        try { r = await opts.onRename(curId, name, cur); }
                        catch (e) { console.error('[SplitPanelSwitcher] onRename failed:', e); return 'Could not rename'; }
                        if (typeof r === 'string') return r;   // refusal: keep the field open
                        close();
                        if (r) setTitle(name);
                    } });
            };
            rn.addEventListener('click', startRename);
            rn.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === rn) { e.preventDefault(); startRename(); } });
            menu.appendChild(rn);
        }
        if (opts.newLabel && opts.onNew) {
            const nw = document.createElement('div');
            nw.className = 'sp-switcher-new';
            nw.setAttribute('role', 'button'); nw.tabIndex = 0;
            nw.innerHTML = _PLUS_ICON + `<span>${_esc(opts.newLabel)}</span>`;
            const startNew = () => {
                if (!opts.newPrompt) { close(); opts.onNew(); return; }
                if (nw.classList.contains('sp-switcher-naming')) return;
                _inlineName(nw, { placeholder: opts.newPrompt, value: '', submitLabel: 'Create',
                    onSubmit: async (name) => {
                        let r;
                        try { r = await opts.onNew(name); }
                        catch (e) { console.error('[SplitPanelSwitcher] onNew failed:', e); return 'Could not create'; }
                        if (typeof r === 'string') return r;   // refusal: keep the field open
                        close();
                    } });
            };
            nw.addEventListener('click', startNew);
            nw.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === nw) { e.preventDefault(); startNew(); } });
            menu.appendChild(nw);
        }

        document.body.appendChild(menu);

        // Position fixed under the button, clamped to the viewport.
        const r = btnEl.getBoundingClientRect();
        const menuW = Math.min(360, Math.max(240, menu.offsetWidth));
        let left = r.left;
        if (left + menuW > window.innerWidth - 8) left = Math.max(8, window.innerWidth - menuW - 8);
        menu.style.top = `${Math.round(r.bottom + 4)}px`;
        menu.style.left = `${Math.round(left)}px`;

        setTimeout(() => {
            document.addEventListener('click', onDocClick, true);
            document.addEventListener('keydown', onKey, true);
            window.addEventListener('resize', close);
            window.addEventListener('scroll', close, true);
        }, 0);
    }

    function toggle(e) {
        e.preventDefault();
        e.stopPropagation();
        if (menu) close(); else open();
    }

    btnEl.addEventListener('click', toggle);
    const unmount = function () {
        btnEl.removeEventListener('click', toggle);
        close();
        btnEl._spSwitcherMounted = false;
    };
    btnEl._spSwitcherUnmount = unmount;
    return unmount;
}

// ── Panel notice: a fact about what the panel shows, and the act that follows ──────────
// "Changed elsewhere · Reload" for an editor whose item was written outside it (another tab, a
// REST script, a scheduled run — see uderiaBus.js `own`). ONE line per panel, under its
// .sp-header; a new call replaces it; the act or the dismiss removes it. Shared by every split
// panel so the wording and the chrome stay one (static/css/split-panels.css .sp-notice).

const _X_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>`;

function _panelEl(panel) {
    return typeof panel === 'string' ? document.getElementById(panel) : panel;
}

export function hideSplitPanelNotice(panel) {
    const el = _panelEl(panel);
    if (!el) return;
    el.querySelectorAll(':scope > .sp-notice').forEach(n => n.remove());
}

/** `actions`: [{label, onAction}] in reading order — the first is the act most owed (a reload);
 *  `actionLabel` + `onAction` is the one-act shorthand. Any act removes the line, then runs. */
export function showSplitPanelNotice(panel, text, { actionLabel, onAction, actions } = {}) {
    const el = _panelEl(panel);
    if (!el) return null;
    hideSplitPanelNotice(el);
    const line = document.createElement('div');
    line.className = 'sp-notice';
    line.setAttribute('role', 'status');
    const span = document.createElement('span');
    span.className = 'sp-notice-text';
    span.textContent = text;
    line.appendChild(span);
    const acts = Array.isArray(actions) ? actions : (actionLabel && typeof onAction === 'function' ? [{ label: actionLabel, onAction }] : []);
    for (const a of acts) {
        if (!a || !a.label || typeof a.onAction !== 'function') continue;
        const act = document.createElement('button');
        act.type = 'button';
        act.className = 'sp-notice-action';
        act.textContent = a.label;
        act.addEventListener('click', () => { hideSplitPanelNotice(el); try { a.onAction(); } catch (e) { console.warn('[sp-notice] action failed:', e); } });
        line.appendChild(act);
    }
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'sp-notice-dismiss';
    dismiss.title = 'Dismiss';
    dismiss.setAttribute('aria-label', 'Dismiss');
    dismiss.innerHTML = _X_ICON;
    dismiss.addEventListener('click', () => hideSplitPanelNotice(el));
    line.appendChild(dismiss);
    const header = el.querySelector(':scope > .sp-header');
    if (header) header.insertAdjacentElement('afterend', line); else el.prepend(line);
    return line;
}

// Expose for component renderer modules (which can't easily import static handlers).
window.mountSplitPanelSwitcher = mountSplitPanelSwitcher;
window.showSplitPanelNotice = showSplitPanelNotice;
window.hideSplitPanelNotice = hideSplitPanelNotice;
