/**
 * Canvases Tab — Canvas Workspace Component Detail
 *
 * Renders a "Canvases" tab inside the Canvas Workspace component detail panel.
 * Lists all of the user's persisted canvases with open, rename, export, and
 * delete — mirroring the Dashboards/Flows instance-management tabs so a canvas
 * is treated like any other stateful component instance.
 *
 * Entry point: window.canvasesHandler.initCanvasesTab(containerId, comp)
 * Called from componentHandler.js after the Canvases tab panel is in the DOM.
 * `comp` is the component object (manifest-driven: supports_instance_io drives
 * the Export/Import affordances, icon_svg/icon_accent feed the shared remap UI).
 */

(function () {
    'use strict';

    // ── Constants ──────────────────────────────────────────────────────────────

    const AUTH = () => localStorage.getItem('tda_auth_token');
    const BASE = '/api/v1';
    const ACCENT = '#06b6d4';   // canvas accent (matches manifest icon_accent)

    // Lazy import of the shared instance export/import UI (this is a classic IIFE
    // script, so use a dynamic import like the renderer import below).
    const _io = () => import('/static/js/handlers/instancePortabilityUI.js?v=1.0');
    const _ioMeta = () => ({
        component_id: 'canvas',
        display_name: _componentMeta.display_name || 'Canvas',
        icon_svg: _componentMeta.icon_svg,
        icon_accent: _componentMeta.icon_accent || ACCENT,
    });

    // ── CSS ────────────────────────────────────────────────────────────────────

    function _injectCSS() {
        if (document.getElementById('canvases-handler-css')) return;
        const s = document.createElement('style');
        s.id = 'canvases-handler-css';
        s.textContent = `
/* Canvases tab shell */
.cnv-split         { display:flex; gap:0; height:540px; overflow:hidden; }
.cnv-list          { flex:0 0 55%; overflow-y:auto; padding-right:12px;
                     border-right:1px solid var(--border-secondary, rgba(255,255,255,0.06)); }
.cnv-detail        { flex:1; overflow-y:auto; padding-left:16px; }
.cnv-detail.cnv-hidden { display:none; }

/* List header */
.cnv-list-hdr      { display:flex; align-items:center; justify-content:space-between;
                     margin-bottom:12px; padding:0 4px; gap:8px; }
.cnv-list-title    { font-size:13px; font-weight:600; color:var(--text-muted);
                     letter-spacing:.05em; text-transform:uppercase; }
.cnv-hdr-btns      { display:flex; gap:6px; flex-shrink:0; }
.cnv-hdr-btn       { display:inline-flex; align-items:center; gap:5px; padding:5px 12px;
                     border-radius:8px; font-size:12px; font-weight:500; cursor:pointer;
                     border:1px solid rgba(6,182,212,.3); background:rgba(6,182,212,.08);
                     color:#22d3ee; transition:background .12s; }
.cnv-hdr-btn:hover { background:rgba(6,182,212,.16); }

/* Canvas rows */
.cnv-row           { display:flex; align-items:center; gap:8px; padding:9px 8px;
                     border-radius:10px; cursor:pointer; transition:background .12s;
                     border:1px solid transparent; }
.cnv-row:hover     { background:var(--hover-bg-strong, rgba(255,255,255,0.05)); }
.cnv-row.active    { background:rgba(6,182,212,.08); border-color:rgba(6,182,212,.22); }
.cnv-row + .cnv-row { margin-top:4px; }

/* Row: icon */
.cnv-row-icon      { width:32px; height:32px; border-radius:8px; flex-shrink:0;
                     display:flex; align-items:center; justify-content:center;
                     background:rgba(6,182,212,.12); color:#22d3ee; }

/* Row: text block */
.cnv-row-body      { flex:1; min-width:0; }
.cnv-row-name      { font-size:13px; font-weight:500; color:var(--text-primary);
                     white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cnv-row-meta      { font-size:11px; color:var(--text-muted); margin-top:1px; }

/* Row: language badge */
.cnv-badge-lang    { display:inline-flex; align-items:center; padding:2px 7px;
                     border-radius:20px; font-size:11px; font-weight:500;
                     border:1px solid var(--border-secondary, rgba(255,255,255,.12));
                     background:var(--bg-overlay, rgba(255,255,255,.05));
                     color:var(--text-muted); white-space:nowrap; flex-shrink:0; }

/* Row: action buttons (reveal on hover) */
.cnv-row-actions   { display:flex; gap:2px; flex-shrink:0; opacity:0; transition:opacity .12s; }
.cnv-row:hover .cnv-row-actions { opacity:1; }
.cnv-act-btn       { display:inline-flex; align-items:center; justify-content:center;
                     width:28px; height:28px; border-radius:6px; border:none;
                     background:transparent; cursor:pointer; color:var(--text-muted);
                     transition:background .12s, color .12s; }
.cnv-act-btn:hover         { background:var(--hover-bg-strong, rgba(255,255,255,.08)); color:var(--text-primary); }
.cnv-act-btn.danger:hover  { background:rgba(239,68,68,.15); color:#ef4444; }
.cnv-act-btn.primary:hover { background:rgba(6,182,212,.15); color:#22d3ee; }

/* Empty state */
.cnv-empty         { text-align:center; padding:48px 16px; color:var(--text-muted);
                     font-size:13px; }
.cnv-empty svg     { margin:0 auto 12px; display:block; opacity:.35; }

/* Detail panel */
.cnv-detail-head   { margin-bottom:16px; }
.cnv-detail-title  { font-size:15px; font-weight:600; color:var(--text-primary); margin-bottom:4px; }
.cnv-detail-sub    { font-size:12px; color:var(--text-muted); }
.cnv-field-label   { font-size:12px; font-weight:500; color:var(--text-muted);
                     margin-bottom:4px; display:block; }

/* Inputs */
.cnv-input         { width:100%; padding:7px 10px; border-radius:8px; font-size:13px;
                     border:1px solid var(--border-secondary, rgba(255,255,255,.12));
                     background:var(--bg-overlay, rgba(255,255,255,.04));
                     color:var(--text-primary); outline:none; transition:border-color .15s;
                     box-sizing:border-box; }
.cnv-input:focus   { border-color:rgba(6,182,212,.4); }

/* Buttons */
.cnv-btn           { display:inline-flex; align-items:center; gap:5px; padding:6px 14px;
                     border-radius:8px; font-size:13px; font-weight:500; cursor:pointer;
                     border:none; transition:background .12s; }
.cnv-btn-canvas    { background:rgba(6,182,212,.1); color:#22d3ee;
                     border:1px solid rgba(6,182,212,.25); }
.cnv-btn-canvas:hover   { background:rgba(6,182,212,.2); }
.cnv-btn-secondary { background:var(--bg-overlay, rgba(255,255,255,.06)); color:var(--text-muted);
                     border:1px solid var(--border-secondary, rgba(255,255,255,.1)); }
.cnv-btn-secondary:hover { background:var(--hover-bg-strong, rgba(255,255,255,.1)); color:var(--text-primary); }
.cnv-btn-danger    { background:rgba(239,68,68,.1); color:#ef4444;
                     border:1px solid rgba(239,68,68,.2); }
.cnv-btn-danger:hover   { background:rgba(239,68,68,.2); }
.cnv-btn:disabled  { opacity:.45; cursor:not-allowed; }

/* Undo chip */
.cnv-undo-chip     { display:inline-flex; align-items:center; gap:8px; padding:5px 12px;
                     border-radius:8px; background:rgba(239,68,68,.1); color:#ef4444;
                     font-size:12px; border:1px solid rgba(239,68,68,.22); }
.cnv-undo-btn      { background:none; border:none; color:#ef4444; cursor:pointer;
                     font-weight:600; padding:0; }

/* Divider */
.cnv-divider       { border:none; border-top:1px solid var(--border-secondary, rgba(255,255,255,.07)); margin:14px 0; }

/* Spinner */
@keyframes cnv-spin { to { transform:rotate(360deg); } }
.cnv-spinner       { display:inline-block; width:14px; height:14px;
                     border:2px solid var(--border-secondary, rgba(255,255,255,.15)); border-top-color:#22d3ee;
                     border-radius:50%; animation:cnv-spin .7s linear infinite; }

/* Scrollbars */
.cnv-list::-webkit-scrollbar,
.cnv-detail::-webkit-scrollbar              { width:4px; }
.cnv-list::-webkit-scrollbar-track,
.cnv-detail::-webkit-scrollbar-track        { background:transparent; }
.cnv-list::-webkit-scrollbar-thumb,
.cnv-detail::-webkit-scrollbar-thumb        { background:var(--border-secondary, rgba(255,255,255,.12)); border-radius:2px; }
`;
        document.head.appendChild(s);
    }

    // ── SVG icons (monochrome, currentColor) ────────────────────────────────────

    const ICONS = {
        canvas:  `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M3 9h18M9 21V9"/></svg>`,
        open:    `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14 5l7 7-7 7M21 12H3"/></svg>`,
        trash:   `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3M3 7h18"/></svg>`,
        edit:    `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 013.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>`,
        check:   `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>`,
        export:  `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 3v13m0-13l-4 4m4-4l4 4"/></svg>`,
        import:  `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 16V3m0 13l-4-4m4 4l4-4"/></svg>`,
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
        console.log(`[Canvases] ${type}: ${msg}`);
    }

    // ── Module state ───────────────────────────────────────────────────────────

    let _root          = null;
    let _componentMeta = {};        // canvas component object (manifest-driven)
    let _canvases      = [];        // cached list from API
    let _selectedId    = null;      // currently selected canvas id

    // ══════════════════════════════════════════════════════════════════════════
    // Entry point
    // ══════════════════════════════════════════════════════════════════════════

    function initCanvasesTab(containerId, comp) {
        _injectCSS();
        _componentMeta = comp || {};
        _root = document.getElementById(containerId);
        if (!_root) return;

        _root.innerHTML = `
            <div class="cnv-split">
                <div class="cnv-list" id="cnv-list-panel"></div>
                <div class="cnv-detail cnv-hidden" id="cnv-detail-panel"></div>
            </div>
        `;

        _loadAndRenderList();
    }

    // ══════════════════════════════════════════════════════════════════════════
    // List panel
    // ══════════════════════════════════════════════════════════════════════════

    async function _loadAndRenderList() {
        const panel = document.getElementById('cnv-list-panel');
        if (!panel) return;

        const importBtn = _componentMeta.supports_instance_io
            ? `<button class="cnv-hdr-btn" id="cnv-import-btn" title="Import a canvas from a file">${ICONS.import} Import</button>`
            : '';

        panel.innerHTML = `
            <div class="cnv-list-hdr">
                <span class="cnv-list-title">My Canvases</span>
                <div class="cnv-hdr-btns">${importBtn}</div>
            </div>
            <div id="cnv-rows-container" style="color:var(--text-muted);font-size:13px">
                <span class="cnv-spinner"></span>
            </div>
        `;

        panel.querySelector('#cnv-import-btn')?.addEventListener('click', async () => {
            const m = await _io();
            m.importInstance(_ioMeta(), { onDone: () => _loadAndRenderList() });
        });

        try {
            const data = await _api('GET', '/canvases');
            _canvases = data.canvases || [];
            _renderRows();
        } catch (err) {
            const c = document.getElementById('cnv-rows-container');
            if (c) c.innerHTML = `<div style="color:#ef4444;font-size:13px;padding:8px">Failed to load: ${_esc(err.message)}</div>`;
        }
    }

    function _renderRows() {
        const container = document.getElementById('cnv-rows-container');
        if (!container) return;

        if (_canvases.length === 0) {
            container.innerHTML = `
                <div class="cnv-empty">
                    ${ICONS.canvas}
                    <div style="font-weight:500;margin-bottom:4px">No canvases yet</div>
                    <div style="font-size:12px">Ask the agent to draft code or a document, or import one.</div>
                </div>`;
            return;
        }

        const exportBtn = (id) => _componentMeta.supports_instance_io
            ? `<button class="cnv-act-btn" data-action="export" title="Export canvas">${ICONS.export}</button>`
            : '';

        container.innerHTML = _canvases.map(c => `
            <div class="cnv-row${c.canvas_id === _selectedId ? ' active' : ''}" data-id="${_esc(c.canvas_id)}">
                <div class="cnv-row-icon">${ICONS.canvas}</div>
                <div class="cnv-row-body">
                    <div class="cnv-row-name">${_esc(c.title || 'Canvas')}</div>
                    <div class="cnv-row-meta">${c.updated_at ? _relTime(c.updated_at) : ''}</div>
                </div>
                <span class="cnv-badge-lang">${_esc(c.language || 'text')}</span>
                <div class="cnv-row-actions">
                    <button class="cnv-act-btn primary" data-action="open" title="Open canvas">${ICONS.open}</button>
                    ${exportBtn(c.canvas_id)}
                    <button class="cnv-act-btn danger" data-action="delete" title="Delete">${ICONS.trash}</button>
                </div>
            </div>
        `).join('');

        container.querySelectorAll('.cnv-row').forEach(row => {
            const id = row.dataset.id;

            row.addEventListener('click', e => {
                if (e.target.closest('.cnv-act-btn')) return;
                _selectCanvas(id);
            });

            row.querySelector('[data-action="open"]').addEventListener('click', e => {
                e.stopPropagation();
                _openCanvas(id);
            });

            row.querySelector('[data-action="export"]')?.addEventListener('click', async e => {
                e.stopPropagation();
                const m = await _io();
                const c = _canvases.find(x => x.canvas_id === id);
                m.exportInstance('canvas', id, (c && c.title) || id);
            });

            row.querySelector('[data-action="delete"]').addEventListener('click', e => {
                e.stopPropagation();
                _handleDelete(id);
            });
        });
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Detail panel
    // ══════════════════════════════════════════════════════════════════════════

    function _selectCanvas(id) {
        _selectedId = id;
        document.querySelectorAll('.cnv-row').forEach(r =>
            r.classList.toggle('active', r.dataset.id === id)
        );
        const cv = _canvases.find(c => c.canvas_id === id);
        if (cv) _renderDetail(cv);
    }

    function _renderDetail(cv) {
        const panel = document.getElementById('cnv-detail-panel');
        if (!panel) return;
        panel.classList.remove('cnv-hidden');

        panel.innerHTML = `
            <div class="cnv-detail-head">
                <div class="cnv-detail-title">${_esc(cv.title || 'Canvas')}</div>
                <div class="cnv-detail-sub">
                    ${_esc(cv.language || 'text')}
                    ${cv.updated_at ? ' · updated ' + _relTime(cv.updated_at) : ''}
                </div>
            </div>

            <button class="cnv-btn cnv-btn-canvas" id="cnv-open-canvas" style="width:100%;justify-content:center;margin-bottom:14px">
                ${ICONS.open} Open Canvas
            </button>

            <hr class="cnv-divider">

            <label class="cnv-field-label" for="cnv-name-input">Title</label>
            <div style="display:flex;gap:8px;margin-bottom:14px">
                <input id="cnv-name-input" class="cnv-input" type="text"
                       value="${_esc(cv.title || '')}" maxlength="120" placeholder="Canvas title">
                <button class="cnv-btn cnv-btn-secondary" id="cnv-rename-btn" style="flex-shrink:0">
                    ${ICONS.edit} Rename
                </button>
            </div>

            ${_componentMeta.supports_instance_io ? `
            <button class="cnv-btn cnv-btn-secondary" id="cnv-export-btn" style="width:100%;justify-content:center;margin-bottom:14px">
                ${ICONS.export} Export
            </button>` : ''}

            <hr class="cnv-divider">

            <button class="cnv-btn cnv-btn-danger" id="cnv-delete-btn" style="width:100%;justify-content:center">
                ${ICONS.trash} Delete Canvas
            </button>
        `;

        panel.querySelector('#cnv-open-canvas').addEventListener('click', () => _openCanvas(cv.canvas_id));

        const nameInput = panel.querySelector('#cnv-name-input');
        const renameBtn = panel.querySelector('#cnv-rename-btn');
        renameBtn.addEventListener('click', () => _handleRename(cv.canvas_id, nameInput));
        nameInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); _handleRename(cv.canvas_id, nameInput); }
        });

        panel.querySelector('#cnv-export-btn')?.addEventListener('click', async () => {
            const m = await _io();
            m.exportInstance('canvas', cv.canvas_id, cv.title || cv.canvas_id);
        });

        panel.querySelector('#cnv-delete-btn').addEventListener('click', () => _handleDelete(cv.canvas_id));
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Actions
    // ══════════════════════════════════════════════════════════════════════════

    async function _handleRename(id, input) {
        const newName = (input.value || '').trim();
        if (!newName) { input.focus(); return; }

        const cv = _canvases.find(c => c.canvas_id === id);
        if (cv && cv.title === newName) return; // no change

        try {
            await _api('PUT', `/canvases/${id}`, { title: newName });
            if (cv) cv.title = newName;
            _renderRows();
            const titleEl = document.querySelector('.cnv-detail-title');
            if (titleEl) titleEl.textContent = newName;
            _toast('Canvas renamed');
        } catch (err) {
            _toast(err.message, 'error');
        }
    }

    function _handleDelete(id) {
        const cv = _canvases.find(c => c.canvas_id === id);
        if (!cv) return;

        const rows = document.getElementById('cnv-rows-container');
        const row  = rows?.querySelector(`.cnv-row[data-id="${id}"]`);
        if (!row) return;

        const chip = document.createElement('div');
        chip.style.cssText = 'padding:4px 0';
        chip.innerHTML = `
            <div class="cnv-undo-chip">
                ${ICONS.trash} Deleting "${_esc(cv.title || 'Canvas')}"…
                <button class="cnv-undo-btn">Undo</button>
            </div>`;
        row.replaceWith(chip);

        let cancelled = false;
        chip.querySelector('.cnv-undo-btn')?.addEventListener('click', () => {
            cancelled = true;
            _loadAndRenderList();
            _toast('Delete cancelled');
        });

        setTimeout(async () => {
            if (cancelled) return;
            try {
                await _api('DELETE', `/canvases/${id}`);
                _canvases = _canvases.filter(c => c.canvas_id !== id);
                chip.remove();
                if (_selectedId === id) {
                    _selectedId = null;
                    document.getElementById('cnv-detail-panel')?.classList.add('cnv-hidden');
                }
                if (_canvases.length === 0) _renderRows();
                _toast(`"${cv.title || 'Canvas'}" deleted`);
            } catch (err) {
                _loadAndRenderList();
                _toast(err.message, 'error');
            }
        }, 5000);
    }

    async function _openCanvas(id) {
        // Navigate to conversation view if needed (the canvas split panel lives there).
        const convView = document.getElementById('conversation-view');
        if (!convView?.classList.contains('active')) {
            document.getElementById('view-switch-conversation')?.click();
        }

        try {
            const r = await fetch(`${BASE}/canvases/${id}`, { headers: { 'Authorization': `Bearer ${AUTH()}` } });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const c = await r.json();
            const lang = (c.language || '').toLowerCase();
            const spec = {
                canvas_id: c.canvas_id,
                title: c.title,
                language: lang,
                content: c.content,
                sources: c.sources || null,
                previewable: ['html', 'svg', 'markdown'].includes(lang),
                line_count: ((c.content || '').match(/\n/g) || []).length + 1,
                versions: c.versions || [],
                action: 'open',
            };

            const { renderCanvas } = await import('/api/v1/components/canvas/renderer');
            const tempId = `canvas-detail-open-${c.canvas_id}`;
            let tempDiv = document.getElementById(tempId);
            if (!tempDiv) {
                tempDiv = document.createElement('div');
                tempDiv.id = tempId;
                tempDiv.style.display = 'none';
                document.body.appendChild(tempDiv);
            }
            Promise.resolve(renderCanvas(tempId, { component: 'canvas', ...spec }))
                .finally(() => tempDiv.remove());
            _toast(`Opened "${c.title || 'Canvas'}"`);
        } catch (err) {
            _toast(err?.message || 'Failed to open canvas', 'error');
        }
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    window.canvasesHandler = { initCanvasesTab };

})();
