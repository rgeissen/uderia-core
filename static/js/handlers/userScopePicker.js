/**
 * User Scope Picker — shared searchable user popover for admin all-users views
 * (Agent Performance roster + Session Performance cards). Plain script, no
 * module system: exposes window.UserScopePicker.
 *
 * Contract: open({ anchor, users, current, onSelect, allLabel }) where
 *   users    = { uuid: username } (built by the lens's own data response —
 *              only users that actually have data appear, deliberately)
 *   current  = the focused uuid or '' / null for "all users"
 *   onSelect = (uuid|null, label) callback; null = all users
 *
 * UX: matches the platform's popover idiom (extension picker / sp-switcher
 * menu): auto-focused search input filtering as you type (name or uuid),
 * Enter selects the first visible entry, Esc / outside click dismisses.
 * Theme-aware (CSS variables), monochrome SVG icons only.
 */
(function () {
    'use strict';

    let _pop = null;

    function close() {
        if (!_pop) return;
        _pop.remove();
        _pop = null;
        document.removeEventListener('mousedown', _onDocDown, true);
        document.removeEventListener('keydown', _onKeyDown, true);
    }

    function _onDocDown(ev) { if (_pop && !_pop.contains(ev.target)) close(); }
    function _onKeyDown(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } }

    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    const _CHECK = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;flex-shrink:0;color:var(--teradata-orange);"><polyline points="20 6 9 17 4 12"/></svg>';
    const _USER = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:.7;"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
    const _USERS = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:.7;"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';

    function open(opts) {
        close();
        const anchor = opts.anchor;
        if (!anchor) return;
        const entries = Object.entries(opts.users || {})
            .map(([uuid, name]) => ({ uuid, name: name || uuid.slice(0, 8) + '…' }))
            .sort((a, b) => a.name.localeCompare(b.name));
        const current = opts.current || '';
        const rect = anchor.getBoundingClientRect();

        _pop = document.createElement('div');
        _pop.setAttribute('role', 'listbox');
        _pop.style.cssText =
            'position:fixed;z-index:1000;' +
            `top:${Math.round(rect.bottom + 6)}px;left:${Math.max(8, Math.round(rect.right - 250))}px;` +
            'width:250px;background:var(--card-bg);border:1px solid var(--border-primary);' +
            'border-radius:10px;box-shadow:0 10px 34px rgba(0,0,0,.28);padding:6px;';
        _pop.innerHTML =
            '<style>.usp-row:hover{background:var(--hover-bg-strong, rgba(127,127,127,.10));}</style>' +
            '<input type="text" placeholder="Search users…" aria-label="Search users" style="width:100%;box-sizing:border-box;padding:6px 8px;margin-bottom:6px;font-size:12px;border-radius:7px;border:1px solid var(--border-secondary);background:var(--input-bg, transparent);color:var(--text-primary);outline:none;">' +
            '<div data-usp-list style="max-height:250px;overflow-y:auto;"></div>';
        const input = _pop.querySelector('input');
        const list = _pop.querySelector('[data-usp-list]');

        const render = (q) => {
            const ql = (q || '').trim().toLowerCase();
            const rows = [
                { uuid: '', name: opts.allLabel || 'All users', icon: _USERS },
                ...entries
                    .filter((e) => !ql || e.name.toLowerCase().includes(ql) || e.uuid.toLowerCase().includes(ql))
                    .map((e) => ({ ...e, icon: _USER })),
            ];
            list.innerHTML = rows.map((e) => {
                const active = current === e.uuid;
                return `<button type="button" class="usp-row" data-usp-uuid="${_esc(e.uuid)}" data-usp-name="${_esc(e.name)}" role="option" aria-selected="${active}"` +
                    ` style="display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:6px 8px;border-radius:7px;border:none;cursor:pointer;font-size:12px;color:var(--text-primary);background:${active ? 'var(--hover-bg-strong, rgba(127,127,127,.12))' : 'transparent'};${active ? 'font-weight:600;' : ''}">` +
                    `${e.icon}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(e.name)}</span>${active ? _CHECK : ''}</button>`;
            }).join('') ||
                '<div style="padding:8px;font-size:11px;color:var(--text-muted);">No matching user.</div>';
        };
        render('');

        input.addEventListener('input', () => render(input.value));
        input.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') {
                const first = list.querySelector('.usp-row');
                if (first) first.click();
            }
        });
        list.addEventListener('click', (ev) => {
            const row = ev.target.closest('.usp-row');
            if (!row) return;
            const uuid = row.dataset.uspUuid || null;
            const name = row.dataset.uspName;
            close();
            if (typeof opts.onSelect === 'function') opts.onSelect(uuid, name);
        });

        document.body.appendChild(_pop);
        document.addEventListener('mousedown', _onDocDown, true);
        document.addEventListener('keydown', _onKeyDown, true);
        input.focus();
    }

    window.UserScopePicker = { open, close };
})();
