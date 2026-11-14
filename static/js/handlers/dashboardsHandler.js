/**
 * Dashboards Tab — Dashboard Canvas Component Detail
 *
 * Renders a "Dashboards" tab inside the Dashboard Canvas component detail panel.
 * Shows all user dashboards with rename, set-default, open-canvas, and delete.
 *
 * Entry point: window.dashboardsHandler.initDashboardsTab(containerId)
 * Called from componentHandler.js after the Dashboards tab panel is inserted into the DOM.
 */

(function () {
    'use strict';

    // ── Constants ──────────────────────────────────────────────────────────────

    const AUTH = () => localStorage.getItem('tda_auth_token');
    const BASE = '/api/v1';

    // ── CSS ────────────────────────────────────────────────────────────────────

    function _injectCSS() {
        if (document.getElementById('dashboards-handler-css')) return;
        const s = document.createElement('style');
        s.id = 'dashboards-handler-css';
        s.textContent = `
/* Dashboards tab shell */
.dsh-split         { display:flex; gap:0; height:540px; overflow:hidden; }
.dsh-list          { flex:0 0 55%; overflow-y:auto; padding-right:12px;
                     border-right:1px solid rgba(255,255,255,0.06); }
.dsh-detail        { flex:1; overflow-y:auto; padding-left:16px; }
.dsh-detail.dsh-hidden { display:none; }

/* List header */
.dsh-list-hdr      { display:flex; align-items:center; justify-content:space-between;
                     margin-bottom:12px; padding:0 4px; }
.dsh-list-title    { font-size:13px; font-weight:600; color:var(--text-muted);
                     letter-spacing:.05em; text-transform:uppercase; }
.dsh-new-btn       { display:inline-flex; align-items:center; gap:5px; padding:5px 12px;
                     border-radius:8px; font-size:12px; font-weight:500; cursor:pointer;
                     border:1px solid rgba(241,95,34,.3); background:rgba(241,95,34,.08);
                     color:var(--teradata-orange, #F15F22); transition:background .12s; }
.dsh-new-btn:hover { background:rgba(241,95,34,.16); }

/* Dashboard rows */
.dsh-row           { display:flex; align-items:center; gap:8px; padding:9px 8px;
                     border-radius:10px; cursor:pointer; transition:background .12s;
                     border:1px solid transparent; }
.dsh-row:hover     { background:var(--hover-bg-strong, rgba(255,255,255,0.05)); }
.dsh-row.active    { background:rgba(241,95,34,.08); border-color:rgba(241,95,34,.22); }
.dsh-row + .dsh-row { margin-top:4px; }

/* Row: icon */
.dsh-row-icon      { width:32px; height:32px; border-radius:8px; flex-shrink:0;
                     display:flex; align-items:center; justify-content:center;
                     background:rgba(241,95,34,.12); color:var(--teradata-orange, #F15F22); }

/* Row: text block */
.dsh-row-body      { flex:1; min-width:0; }
.dsh-row-name      { font-size:13px; font-weight:500; color:var(--text-primary);
                     white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.dsh-row-meta      { font-size:11px; color:var(--text-muted); margin-top:1px; }

/* Row: badges */
.dsh-badge         { display:inline-flex; align-items:center; padding:2px 7px;
                     border-radius:20px; font-size:11px; font-weight:500;
                     border:1px solid; white-space:nowrap; flex-shrink:0; }
.dsh-badge-default { border-color:rgba(241,95,34,.35); background:rgba(241,95,34,.1);
                     color:var(--teradata-orange, #F15F22); }
.dsh-badge-tiles   { border-color:rgba(255,255,255,.12); background:rgba(255,255,255,.05);
                     color:var(--text-muted); }

/* Row: action buttons (reveal on hover) */
.dsh-row-actions   { display:flex; gap:2px; flex-shrink:0; opacity:0; transition:opacity .12s; }
.dsh-row:hover .dsh-row-actions { opacity:1; }
.dsh-act-btn       { display:inline-flex; align-items:center; justify-content:center;
                     width:28px; height:28px; border-radius:6px; border:none;
                     background:transparent; cursor:pointer; color:var(--text-muted);
                     transition:background .12s, color .12s; }
.dsh-act-btn:hover         { background:rgba(255,255,255,.08); color:var(--text-primary); }
.dsh-act-btn.danger:hover  { background:rgba(239,68,68,.15); color:#ef4444; }
.dsh-act-btn.primary:hover { background:rgba(241,95,34,.15); color:var(--teradata-orange, #F15F22); }

/* Empty state */
.dsh-empty         { text-align:center; padding:48px 16px; color:var(--text-muted);
                     font-size:13px; }
.dsh-empty svg     { margin:0 auto 12px; display:block; opacity:.35; }

/* Detail panel */
.dsh-detail-head   { margin-bottom:16px; }
.dsh-detail-title  { font-size:15px; font-weight:600; color:var(--text-primary); margin-bottom:4px; }
.dsh-detail-sub    { font-size:12px; color:var(--text-muted); }
.dsh-field-label   { font-size:12px; font-weight:500; color:var(--text-muted);
                     margin-bottom:4px; display:block; }

/* Inputs */
.dsh-input         { width:100%; padding:7px 10px; border-radius:8px; font-size:13px;
                     border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.04);
                     color:var(--text-primary); outline:none; transition:border-color .15s;
                     box-sizing:border-box; }
.dsh-input:focus   { border-color:rgba(241,95,34,.4); }

/* Buttons */
.dsh-btn           { display:inline-flex; align-items:center; gap:5px; padding:6px 14px;
                     border-radius:8px; font-size:13px; font-weight:500; cursor:pointer;
                     border:none; transition:background .12s; }
.dsh-btn-primary   { background:rgba(241,95,34,.12); color:var(--teradata-orange, #F15F22);
                     border:1px solid rgba(241,95,34,.28); }
.dsh-btn-primary:hover  { background:rgba(241,95,34,.22); }
.dsh-btn-secondary { background:rgba(255,255,255,.06); color:var(--text-muted);
                     border:1px solid rgba(255,255,255,.1); }
.dsh-btn-secondary:hover { background:rgba(255,255,255,.1); color:var(--text-primary); }
.dsh-btn-canvas    { background:rgba(6,182,212,.1); color:#22d3ee;
                     border:1px solid rgba(6,182,212,.25); }
.dsh-btn-canvas:hover   { background:rgba(6,182,212,.2); }
.dsh-btn-danger    { background:rgba(239,68,68,.1); color:#ef4444;
                     border:1px solid rgba(239,68,68,.2); }
.dsh-btn-danger:hover   { background:rgba(239,68,68,.2); }
.dsh-btn:disabled  { opacity:.45; cursor:not-allowed; }

/* Tile list */
.dsh-tile-row      { display:flex; align-items:center; gap:8px; padding:7px 0;
                     border-bottom:1px solid rgba(255,255,255,.05); font-size:12px; }
.dsh-tile-row:last-child { border-bottom:none; }
.dsh-tile-icon     { width:20px; height:20px; border-radius:4px; flex-shrink:0;
                     display:flex; align-items:center; justify-content:center;
                     background:rgba(255,255,255,.06); color:var(--text-muted); }
.dsh-tile-name     { flex:1; color:var(--text-primary); white-space:nowrap;
                     overflow:hidden; text-overflow:ellipsis; }
.dsh-tile-type     { font-size:10px; color:var(--text-muted); flex-shrink:0;
                     padding:1px 6px; border-radius:10px;
                     border:1px solid rgba(255,255,255,.1); }

/* Undo chip */
.dsh-undo-chip     { display:inline-flex; align-items:center; gap:8px; padding:5px 12px;
                     border-radius:8px; background:rgba(239,68,68,.1); color:#ef4444;
                     font-size:12px; border:1px solid rgba(239,68,68,.22); }
.dsh-undo-btn      { background:none; border:none; color:#ef4444; cursor:pointer;
                     font-weight:600; padding:0; }

/* Divider */
.dsh-divider       { border:none; border-top:1px solid rgba(255,255,255,.07); margin:14px 0; }

/* Section label */
.dsh-section-label { font-size:11px; font-weight:600; letter-spacing:.06em;
                     text-transform:uppercase; color:var(--text-muted); margin-bottom:8px; }

/* Spinner */
@keyframes dsh-spin { to { transform:rotate(360deg); } }
.dsh-spinner       { display:inline-block; width:14px; height:14px;
                     border:2px solid var(--border-secondary, rgba(255,255,255,.15)); border-top-color:var(--teradata-orange, #F15F22);
                     border-radius:50%; animation:dsh-spin .7s linear infinite; }

/* Scrollbars */
.dsh-list::-webkit-scrollbar,
.dsh-detail::-webkit-scrollbar              { width:4px; }
.dsh-list::-webkit-scrollbar-track,
.dsh-detail::-webkit-scrollbar-track        { background:transparent; }
.dsh-list::-webkit-scrollbar-thumb,
.dsh-detail::-webkit-scrollbar-thumb        { background:rgba(255,255,255,.12); border-radius:2px; }
`;
        document.head.appendChild(s);
    }

    // ── SVG icons ──────────────────────────────────────────────────────────────

    const ICONS = {
        dashboard: `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                      <rect x="3" y="3" width="7" height="7" rx="1"/>
                      <rect x="14" y="3" width="7" height="7" rx="1"/>
                      <rect x="3" y="14" width="7" height="7" rx="1"/>
                      <rect x="14" y="14" width="7" height="7" rx="1"/>
                    </svg>`,
        plus:     `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg>`,
        canvas:   `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M3 9h18M9 21V9"/></svg>`,
        trash:    `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3M3 7h18"/></svg>`,
        star:     `<svg width="13" height="13" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>`,
        starOff:  `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>`,
        edit:     `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 013.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>`,
        check:    `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>`,
        tile:     `<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/><rect x="3" y="13" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/></svg>`,
        refresh:  `<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>`,
    };

    // ── API helpers ────────────────────────────────────────────────────────────

    async function _api(method, path, body) {
        const opts = {
            method,
            headers: { 'Authorization': `Bearer ${AUTH()}`, 'Content-Type': 'application/json' },
        };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const r = await fetch(BASE + path, opts);
        if (!r.ok) {
            let msg = `HTTP ${r.status}`;
            try { msg = (await r.json()).error || msg; } catch {}
            throw new Error(msg);
        }
        return r.json();
    }

    function _esc(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _relTime(iso) {
        if (!iso) return null;
        const diffMs = Date.now() - new Date(iso).getTime();
        const diffM  = Math.floor(diffMs / 60000);
        if (diffM < 1)  return 'just now';
        if (diffM < 60) return `${diffM}m ago`;
        const diffH = Math.floor(diffM / 60);
        if (diffH < 24) return `${diffH}h ago`;
        return `${Math.floor(diffH / 24)}d ago`;
    }

    function _toast(msg, type = 'success') {
        if (typeof showToast === 'function')        { showToast(msg, type); return; }
        if (typeof window.showToast === 'function') { window.showToast(msg, type); return; }
        console.log(`[Dashboards] ${type}: ${msg}`);
    }

    // ── Module state ───────────────────────────────────────────────────────────

    let _root          = null;
    let _dashboards    = [];        // cached list from API
    let _selectedId    = null;      // currently selected dashboard id

    // ══════════════════════════════════════════════════════════════════════════
    // Entry point
    // ══════════════════════════════════════════════════════════════════════════

    function initDashboardsTab(containerId) {
        _injectCSS();
        _root = document.getElementById(containerId);
        if (!_root) return;

        _root.innerHTML = `
            <div class="dsh-split">
                <div class="dsh-list" id="dsh-list-panel"></div>
                <div class="dsh-detail dsh-hidden" id="dsh-detail-panel"></div>
            </div>
        `;

        _loadAndRenderList();
    }

    // ══════════════════════════════════════════════════════════════════════════
    // List panel
    // ══════════════════════════════════════════════════════════════════════════

    async function _loadAndRenderList() {
        const panel = document.getElementById('dsh-list-panel');
        if (!panel) return;

        panel.innerHTML = `
            <div class="dsh-list-hdr">
                <span class="dsh-list-title">My Dashboards</span>
                <button class="dsh-new-btn" id="dsh-create-btn">
                    ${ICONS.plus} New Dashboard
                </button>
            </div>
            <div id="dsh-rows-container" style="color:var(--text-muted);font-size:13px">
                <span class="dsh-spinner"></span>
            </div>
        `;

        panel.querySelector('#dsh-create-btn').addEventListener('click', _handleCreate);

        try {
            const data = await _api('GET', '/dashboards');
            _dashboards = data.dashboards || [];
            _renderRows();
        } catch (err) {
            document.getElementById('dsh-rows-container').innerHTML =
                `<div style="color:#ef4444;font-size:13px;padding:8px">Failed to load: ${_esc(err.message)}</div>`;
        }
    }

    function _renderRows() {
        const container = document.getElementById('dsh-rows-container');
        if (!container) return;

        if (_dashboards.length === 0) {
            container.innerHTML = `
                <div class="dsh-empty">
                    ${ICONS.dashboard}
                    <div style="font-weight:500;margin-bottom:4px">No dashboards yet</div>
                    <div style="font-size:12px">Click "New Dashboard" to create your first one.</div>
                </div>`;
            return;
        }

        container.innerHTML = _dashboards.map(d => `
            <div class="dsh-row${d.id === _selectedId ? ' active' : ''}" data-id="${_esc(d.id)}">
                <div class="dsh-row-icon">${ICONS.dashboard}</div>
                <div class="dsh-row-body">
                    <div class="dsh-row-name">${_esc(d.name)}</div>
                    <div class="dsh-row-meta">
                        ${d.tile_count || 0} tile${d.tile_count !== 1 ? 's' : ''}
                        ${d.updated_at ? ' · ' + _relTime(d.updated_at) : ''}
                    </div>
                </div>
                ${d.is_default ? `<span class="dsh-badge dsh-badge-default">${ICONS.star} Default</span>` : ''}
                <div class="dsh-row-actions">
                    <button class="dsh-act-btn primary" data-action="canvas"
                            title="Open Canvas">${ICONS.canvas}</button>
                    <button class="dsh-act-btn danger" data-action="delete"
                            title="Delete">${ICONS.trash}</button>
                </div>
            </div>
        `).join('');

        container.querySelectorAll('.dsh-row').forEach(row => {
            const id = row.dataset.id;

            // Row click → select + load detail
            row.addEventListener('click', e => {
                if (e.target.closest('.dsh-act-btn')) return;
                _selectDashboard(id);
            });

            // Canvas button
            row.querySelector('[data-action="canvas"]').addEventListener('click', e => {
                e.stopPropagation();
                _openCanvas(id);
            });

            // Delete button
            row.querySelector('[data-action="delete"]').addEventListener('click', e => {
                e.stopPropagation();
                _handleDelete(id);
            });
        });
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Detail panel
    // ══════════════════════════════════════════════════════════════════════════

    function _selectDashboard(id) {
        _selectedId = id;

        // Update active row highlight
        document.querySelectorAll('.dsh-row').forEach(r =>
            r.classList.toggle('active', r.dataset.id === id)
        );

        const dash = _dashboards.find(d => d.id === id);
        if (!dash) return;

        _renderDetail(dash);
    }

    async function _renderDetail(dash) {
        const panel = document.getElementById('dsh-detail-panel');
        if (!panel) return;
        panel.classList.remove('dsh-hidden');

        // Fetch full dashboard (includes tiles)
        let fullDash = dash;
        let tiles = [];
        try {
            const data = await _api('GET', `/dashboards/${dash.id}`);
            fullDash = data.dashboard || dash;
            tiles    = data.tiles    || [];
        } catch { /* use cached summary */ }

        const tilesHTML = tiles.length
            ? tiles.map(t => {
                const spec = t.spec || {};
                const chartType = spec.chart_type || spec.tile_type || 'chart';
                return `
                <div class="dsh-tile-row">
                    <div class="dsh-tile-icon">${ICONS.tile}</div>
                    <span class="dsh-tile-name">${_esc(t.title || 'Untitled tile')}</span>
                    <span class="dsh-tile-type">${_esc(chartType)}</span>
                </div>`;
            }).join('')
            : `<div style="color:var(--text-muted);font-size:12px;padding:8px 0">No tiles yet.</div>`;

        panel.innerHTML = `
            <div class="dsh-detail-head">
                <div class="dsh-detail-title">${_esc(fullDash.name)}</div>
                <div class="dsh-detail-sub">
                    ${tiles.length} tile${tiles.length !== 1 ? 's' : ''}
                    ${fullDash.updated_at ? ' · updated ' + _relTime(fullDash.updated_at) : ''}
                    ${fullDash.is_default ? ' · <span style="color:var(--teradata-orange, #F15F22)">Default</span>' : ''}
                </div>
            </div>

            <button class="dsh-btn dsh-btn-canvas" id="dsh-open-canvas" style="width:100%;justify-content:center;margin-bottom:14px">
                ${ICONS.canvas} Open Canvas
            </button>

            <hr class="dsh-divider">

            <label class="dsh-field-label" for="dsh-name-input">Name</label>
            <div style="display:flex;gap:8px;margin-bottom:14px">
                <input id="dsh-name-input" class="dsh-input" type="text"
                       value="${_esc(fullDash.name)}" maxlength="80" placeholder="Dashboard name">
                <button class="dsh-btn dsh-btn-secondary" id="dsh-rename-btn" style="flex-shrink:0">
                    ${ICONS.edit} Rename
                </button>
            </div>

            ${!fullDash.is_default ? `
            <div style="margin-bottom:14px">
                <button class="dsh-btn dsh-btn-secondary" id="dsh-default-btn" style="width:100%;justify-content:center">
                    ${ICONS.star} Set as Default
                </button>
            </div>` : ''}

            <hr class="dsh-divider">

            <div class="dsh-section-label">Tiles (${tiles.length})</div>
            <div id="dsh-tiles-list">${tilesHTML}</div>

            <hr class="dsh-divider">

            <div id="dsh-delete-area">
                <button class="dsh-btn dsh-btn-danger" id="dsh-delete-btn" style="width:100%;justify-content:center">
                    ${ICONS.trash} Delete Dashboard
                </button>
            </div>
        `;

        // Wire open canvas
        panel.querySelector('#dsh-open-canvas').addEventListener('click', () => _openCanvas(dash.id));

        // Wire rename
        const nameInput  = panel.querySelector('#dsh-name-input');
        const renameBtn  = panel.querySelector('#dsh-rename-btn');
        renameBtn.addEventListener('click', () => _handleRename(dash.id, nameInput));
        nameInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); _handleRename(dash.id, nameInput); }
        });

        // Wire set-default
        panel.querySelector('#dsh-default-btn')?.addEventListener('click', () => _handleSetDefault(dash.id));

        // Wire delete
        panel.querySelector('#dsh-delete-btn').addEventListener('click', () => _handleDelete(dash.id));
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Actions
    // ══════════════════════════════════════════════════════════════════════════

    async function _handleCreate() {
        const btn = document.getElementById('dsh-create-btn');
        if (!btn) return;

        // Inline name input swap
        const hdr = btn.closest('.dsh-list-hdr');
        if (!hdr) return;

        hdr.innerHTML = `
            <input id="dsh-new-name-input" class="dsh-input" type="text"
                   placeholder="Dashboard name…" maxlength="80" style="flex:1;margin-right:8px">
            <button class="dsh-btn dsh-btn-primary" id="dsh-new-confirm" style="flex-shrink:0">
                ${ICONS.check} Create
            </button>
        `;
        const inp = hdr.querySelector('#dsh-new-name-input');
        inp.focus();

        const doCreate = async () => {
            const name = inp.value.trim();
            if (!name) { inp.focus(); return; }
            const confirmBtn = hdr.querySelector('#dsh-new-confirm');
            if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.innerHTML = `<span class="dsh-spinner"></span>`; }

            try {
                await _api('POST', '/dashboards', { name });
                _toast(`Dashboard "${name}" created`);
                await _loadAndRenderList();
            } catch (err) {
                _toast(err.message, 'error');
                _loadAndRenderList();
            }
        };

        hdr.querySelector('#dsh-new-confirm').addEventListener('click', doCreate);
        inp.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); doCreate(); }
            if (e.key === 'Escape') _loadAndRenderList();
        });
    }

    async function _handleRename(id, input) {
        const newName = (input.value || '').trim();
        if (!newName) { input.focus(); return; }

        const dash = _dashboards.find(d => d.id === id);
        if (dash && dash.name === newName) return; // no change

        try {
            await _api('PUT', `/dashboards/${id}`, { name: newName });
            // Update local cache
            const d = _dashboards.find(d => d.id === id);
            if (d) d.name = newName;
            _renderRows();
            // Refresh detail title
            const titleEl = document.querySelector('.dsh-detail-title');
            if (titleEl) titleEl.textContent = newName;
            _toast('Dashboard renamed');
        } catch (err) {
            _toast(err.message, 'error');
        }
    }

    async function _handleSetDefault(id) {
        try {
            await _api('PUT', `/dashboards/${id}`, { is_default: true });
            // Update local cache
            _dashboards.forEach(d => { d.is_default = d.id === id ? 1 : 0; });
            _renderRows();
            // Re-render detail with updated default state
            const dash = _dashboards.find(d => d.id === id);
            if (dash) _renderDetail(dash);
            _toast('Default dashboard updated');
        } catch (err) {
            _toast(err.message, 'error');
        }
    }

    function _handleDelete(id) {
        const dash = _dashboards.find(d => d.id === id);
        if (!dash) return;

        // Find the list row and replace it with an undo chip (same pattern as flows handler)
        const rows = document.getElementById('dsh-rows-container');
        const row  = rows?.querySelector(`.dsh-row[data-id="${id}"]`);
        if (!row) return;

        const chip = document.createElement('div');
        chip.style.cssText = 'padding:4px 0';
        chip.innerHTML = `
            <div class="dsh-undo-chip">
                ${ICONS.trash} Deleting "${_esc(dash.name)}"…
                <button class="dsh-undo-btn">Undo</button>
            </div>`;
        row.replaceWith(chip);  // row is detached; chip is now in the DOM

        let cancelled = false;
        chip.querySelector('.dsh-undo-btn')?.addEventListener('click', () => {
            cancelled = true;
            _loadAndRenderList();   // full refresh restores the row
            _toast('Delete cancelled');
        });

        setTimeout(async () => {
            if (cancelled) return;
            try {
                await _api('DELETE', `/dashboards/${id}`);
                _dashboards = _dashboards.filter(d => d.id !== id);
                chip.remove();  // directly remove the chip — no list rebuild needed
                // If this was the selected dashboard, hide the detail panel
                if (_selectedId === id) {
                    _selectedId = null;
                    const detail = document.getElementById('dsh-detail-panel');
                    if (detail) detail.classList.add('dsh-hidden');
                }
                // Show empty state if no dashboards remain
                if (_dashboards.length === 0) _renderRows();
                _toast(`"${dash.name}" deleted`);
            } catch (err) {
                _loadAndRenderList();  // restore on failure
                _toast(err.message, 'error');
            }
        }, 5000);
    }

    async function _openCanvas(id) {
        // Navigate to conversation view if needed
        const convView = document.getElementById('conversation-view');
        if (!convView?.classList.contains('active')) {
            document.getElementById('view-switch-conversation')?.click();
        }

        try {
            const [data, mod] = await Promise.all([
                _api('GET', `/dashboards/${id}`),
                import('/api/v1/components/dashboard/renderer'),
            ]);

            const openDashboardPanel = mod.openDashboardPanel;
            if (typeof openDashboardPanel !== 'function') {
                _toast('Dashboard canvas renderer not available', 'error');
                return;
            }

            openDashboardPanel({ dashboard: data.dashboard, tiles: data.tiles || [] });
            _toast(`Opened "${data.dashboard?.name || 'Dashboard'}"`, 'success');
        } catch (err) {
            _toast(err?.message || 'Failed to open canvas', 'error');
        }
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    window.dashboardsHandler = { initDashboardsTab };

})();
