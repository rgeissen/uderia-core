/**
 * Flows Tab — Flow Orchestrator Component Detail
 *
 * Renders a "Flows" tab inside the Flow Orchestrator component detail panel
 * with two sub-tabs:
 *   1. Profile Flows  — user's personal flows (is_template=0); full CRUD + run history
 *   2. Platform Flows — shared blueprint templates (is_template=1); admin-manage, all-fork
 *
 * Entry point: window.flowsHandler.initFlowsTab(containerId)
 * Called from componentHandler.js after the Flows tab panel is inserted into the DOM.
 */

(function () {
    'use strict';

    // ── Constants ──────────────────────────────────────────────────────────────

    const AUTH = () => localStorage.getItem('tda_auth_token');
    const BASE = '/api/v1';

    // ── Canvas button session guard ────────────────────────────────────────────
    // Canvas buttons are disabled until a conversation session is active.
    // A polling watcher enables all registered buttons as soon as a session appears.

    const _canvasBtns = new Set(); // all wired canvas buttons
    let   _sessionPollTimer = null;

    function _hasSession() {
        return !!(window.__currentSessionId || localStorage.getItem('currentSessionId'));
    }

    function _setCanvasBtnEnabled(btn, enabled) {
        if (enabled) {
            btn.disabled = false;
            btn.style.opacity = '';
            btn.style.cursor  = '';
            const orig = btn.dataset.origTitle;
            if (orig) btn.title = orig;
        } else {
            btn.dataset.origTitle = btn.title || '';
            btn.disabled = true;
            btn.style.opacity = '0.38';
            btn.style.cursor  = 'not-allowed';
            btn.title = 'Start a conversation first to enable the canvas';
        }
    }

    function _registerCanvasBtn(btn) {
        _canvasBtns.add(btn);
        _setCanvasBtnEnabled(btn, _hasSession());
        if (!_hasSession() && !_sessionPollTimer) {
            _sessionPollTimer = setInterval(() => {
                if (_hasSession()) {
                    clearInterval(_sessionPollTimer);
                    _sessionPollTimer = null;
                    _canvasBtns.forEach(b => {
                        if (document.contains(b)) _setCanvasBtnEnabled(b, true);
                    });
                    _canvasBtns.clear();
                }
            }, 400);
        }
    }

    // Node type → short label
    const NODE_LABELS = {
        task_llm:      'LLM',
        task_function: 'Fn',
        decision:      'Decision',
        merge:         'Merge',
        start:         'Start',
        end:           'End',
    };

    // Status → dot colour class
    const STATUS_DOT = {
        complete:  'green',
        running:   'purple',
        error:     'red',
        queued:    'amber',
        cancelled: 'gray',
    };

    // ── CSS ────────────────────────────────────────────────────────────────────

    function _injectCSS() {
        if (document.getElementById('flows-handler-css')) return;
        const s = document.createElement('style');
        s.id = 'flows-handler-css';
        s.textContent = `
/* Flows tab shell */
.flows-sub-tabs { display:flex; gap:4px; padding:0 0 12px 0; border-bottom:1px solid rgba(255,255,255,0.08); margin-bottom:16px; }
.flows-sub-tab  { display:inline-flex; align-items:center; gap:6px; padding:6px 14px; border-radius:8px;
                  font-size:13px; font-weight:500; cursor:pointer; border:none; background:transparent;
                  color:var(--text-muted); transition:background .15s, color .15s; }
.flows-sub-tab:hover  { background:var(--hover-bg-strong, rgba(255,255,255,0.06)); color:var(--text-primary); }
.flows-sub-tab.active { background:rgba(147,51,234,0.12); color:#a855f7; }

/* Split layout — mirrors jobs */
.flows-split  { display:flex; gap:0; height:520px; overflow:hidden; }
.flows-list   { flex:0 0 55%; overflow-y:auto; padding-right:12px; border-right:1px solid rgba(255,255,255,0.06); }
.flows-detail { flex:1; overflow-y:auto; padding-left:16px; }
.flows-detail.hidden-panel { display:none; }

/* Flow rows */
.flow-row { display:flex; align-items:center; gap:10px; padding:10px 8px; border-radius:10px;
            cursor:pointer; transition:background .12s; border:1px solid transparent; }
.flow-row:hover  { background:var(--hover-bg-strong, rgba(255,255,255,0.05)); }
.flow-row.active { background:rgba(147,51,234,0.08); border-color:rgba(147,51,234,0.2); }
.flow-row + .flow-row { margin-top:4px; }

/* Status dot */
.flow-dot        { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.flow-dot.green  { background:#22c55e; box-shadow:0 0 4px rgba(34,197,94,.5); }
.flow-dot.purple { background:#a855f7; box-shadow:0 0 4px rgba(168,85,247,.5); }
.flow-dot.amber  { background:#f59e0b; box-shadow:0 0 4px rgba(245,158,11,.5); }
.flow-dot.red    { background:#ef4444; box-shadow:0 0 4px rgba(239,68,68,.5); }
.flow-dot.gray   { background:#6b7280; }

/* Badges */
.flow-badge { display:inline-flex; align-items:center; padding:2px 8px; border-radius:20px;
              font-size:11px; font-weight:500; border:1px solid; white-space:nowrap; flex-shrink:0; }
.flow-meta  { font-size:12px; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

/* Row actions (show on hover) */
.flow-row-actions { display:flex; gap:2px; flex-shrink:0; opacity:0; transition:opacity .12s; }
.flow-row:hover .flow-row-actions { opacity:1; }
.flow-act-btn { display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px;
                border-radius:6px; border:none; background:transparent; cursor:pointer;
                color:var(--text-muted); transition:background .12s, color .12s; }
.flow-act-btn:hover         { background:rgba(255,255,255,0.08); color:var(--text-primary); }
.flow-act-btn.danger:hover  { background:rgba(239,68,68,.15); color:#ef4444; }
.flow-act-btn.promote:hover { background:rgba(147,51,234,.15); color:#a855f7; }

/* Section header */
.flows-section-hdr   { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; padding:0 8px; }
.flows-section-title { font-size:13px; font-weight:600; color:var(--text-muted); letter-spacing:.05em; text-transform:uppercase; }
.flows-new-btn { display:inline-flex; align-items:center; gap:5px; padding:5px 12px; border-radius:8px;
                 font-size:12px; font-weight:500; cursor:pointer;
                 border:1px solid rgba(147,51,234,.3); background:rgba(147,51,234,.08); color:#a855f7;
                 transition:background .12s; }
.flows-new-btn:hover { background:rgba(147,51,234,.16); }
.flows-import-btn { display:inline-flex; align-items:center; gap:5px; padding:5px 12px; border-radius:8px;
                    font-size:12px; font-weight:500; cursor:pointer;
                    border:1px solid var(--border-primary, rgba(148,163,184,.3)); background:transparent;
                    color:var(--text-muted); transition:background .12s, color .12s; }
.flows-import-btn:hover { background:var(--hover-bg, rgba(148,163,184,.1)); color:var(--text-primary); }

/* Detail panel typography */
.flows-detail-title { font-size:15px; font-weight:600; color:var(--text-primary); margin-bottom:2px; }
.flows-detail-sub   { font-size:12px; color:var(--text-muted); margin-bottom:16px; }
.flows-field-label  { font-size:12px; font-weight:500; color:var(--text-muted); margin-bottom:4px; display:block; }

/* Inputs */
.flows-input { width:100%; padding:7px 10px; border-radius:8px; font-size:13px;
               border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.04);
               color:var(--text-primary); outline:none; transition:border-color .15s; box-sizing:border-box; }
.flows-input:focus { border-color:rgba(147,51,234,.4); }
.flows-textarea { resize:vertical; min-height:72px; font-family:inherit; line-height:1.5; }

/* Buttons */
.flows-btn { display:inline-flex; align-items:center; gap:5px; padding:6px 14px; border-radius:8px;
             font-size:13px; font-weight:500; cursor:pointer; border:none; transition:background .12s; }
.flows-btn-primary  { background:rgba(147,51,234,.15); color:#a855f7;  border:1px solid rgba(147,51,234,.3); }
.flows-btn-primary:hover  { background:rgba(147,51,234,.25); }
.flows-btn-canvas   { background:rgba(6,182,212,.1);   color:#22d3ee;  border:1px solid rgba(6,182,212,.25); }
.flows-btn-canvas:hover   { background:rgba(6,182,212,.2); }
.flows-btn-run      { background:rgba(34,197,94,.1);   color:#22c55e;  border:1px solid rgba(34,197,94,.25); }
.flows-btn-run:hover      { background:rgba(34,197,94,.2); }
.flows-btn-fork     { background:rgba(245,158,11,.1);  color:#f59e0b;  border:1px solid rgba(245,158,11,.2); }
.flows-btn-fork:hover     { background:rgba(245,158,11,.2); }
.flows-btn-promote  { background:rgba(147,51,234,.1);  color:#a855f7;  border:1px solid rgba(147,51,234,.2); }
.flows-btn-promote:hover  { background:rgba(147,51,234,.2); }
.flows-btn-secondary { background:rgba(255,255,255,.06); color:var(--text-muted); border:1px solid rgba(255,255,255,.1); }
.flows-btn-secondary:hover { background:rgba(255,255,255,.1); color:var(--text-primary); }
.flows-btn-danger   { background:rgba(239,68,68,.1);   color:#ef4444;  border:1px solid rgba(239,68,68,.2); }
.flows-btn-danger:hover   { background:rgba(239,68,68,.2); }
.flows-btn:disabled { opacity:.45; cursor:not-allowed; }

/* Undo chip */
.flows-undo-chip { display:inline-flex; align-items:center; gap:8px; padding:5px 12px; border-radius:8px;
                   background:rgba(239,68,68,.12); color:#ef4444; font-size:12px; border:1px solid rgba(239,68,68,.25); }
.flows-undo-btn  { background:none; border:none; color:#ef4444; cursor:pointer; font-weight:600; padding:0; }

/* Run history */
.flows-history-item { display:flex; align-items:flex-start; gap:8px; padding:8px 0;
                      border-bottom:1px solid rgba(255,255,255,.05); }
.flows-history-item:last-child { border-bottom:none; }

/* Node type chips */
.flows-node-chips { display:flex; flex-wrap:wrap; gap:4px; margin-top:4px; }
.flow-slot-chip { font-size:10px; padding:1px 7px; border-radius:10px; font-weight:600;
                  color:#f59e0b; border:1px dashed rgba(245,158,11,0.6); background:rgba(245,158,11,0.1); }
.flows-node-chip  { font-size:10px; padding:1px 7px; border-radius:10px;
                    border:1px solid rgba(255,255,255,.15); color:var(--text-muted); white-space:nowrap; }

/* Platform template badge */
.flows-template-badge { display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:20px;
                        font-size:11px; background:rgba(147,51,234,.12); color:#a855f7;
                        border:1px solid rgba(147,51,234,.25); }

/* Divider */
.flows-divider { border:none; border-top:1px solid rgba(255,255,255,.07); margin:14px 0; }

/* Spinner */
@keyframes flows-spin { to { transform:rotate(360deg); } }
.flows-spinner { display:inline-block; width:14px; height:14px; border:2px solid rgba(255,255,255,.15);
                 border-top-color:#a855f7; border-radius:50%; animation:flows-spin .7s linear infinite; }

/* Scrollbars */
.flows-list::-webkit-scrollbar, .flows-detail::-webkit-scrollbar { width:4px; }
.flows-list::-webkit-scrollbar-track, .flows-detail::-webkit-scrollbar-track { background:transparent; }
.flows-list::-webkit-scrollbar-thumb, .flows-detail::-webkit-scrollbar-thumb { background:rgba(255,255,255,.12); border-radius:2px; }
`;
        document.head.appendChild(s);
    }

    // ── SVG icons ──────────────────────────────────────────────────────────────

    const ICONS = {
        flow:    `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M7 5h10M12 7v10"/></svg>`,
        platform:`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M8 21h8M12 17v4"/></svg>`,
        plus:    `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg>`,
        play:    `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 3l14 9-14 9V3z"/></svg>`,
        canvas:  `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M3 9h18M9 21V9"/></svg>`,
        trash:   `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3M3 7h18"/></svg>`,
        fork:    `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 3v12m0 0a3 3 0 106 0m-6 0a3 3 0 100 6 3 3 0 000-6zm6-12a3 3 0 100-6 3 3 0 000 6z"/></svg>`,
        promote: `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 10l7-7m0 0l7 7m-7-7v18"/></svg>`,
        demote:  `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 14l-7 7m0 0l-7-7m7 7V3"/></svg>`,
        export:  `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2"/></svg>`,
        import:  `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 15V3m0 0L8 7m4-4l4 4M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2"/></svg>`,
        check:   `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>`,
        x:       `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>`,
        history: `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
        clock:   `<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path stroke-linecap="round" d="M12 6v6l4 2"/></svg>`,
        edit:    `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 013.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>`,
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
        console.log(`[Flows] ${type}: ${msg}`);
    }

    /** Count non-structural nodes and group by type. */
    function _nodeTypeSummary(definition) {
        const nodes = (definition?.nodes || []).filter(n => n.type !== 'start' && n.type !== 'end');
        const types = {};
        nodes.forEach(n => { const t = n.type || 'unknown'; types[t] = (types[t] || 0) + 1; });
        return { total: nodes.length, types };
    }

    function _nodeChipsHTML(types) {
        return Object.entries(types)
            .map(([t, n]) => `<span class="flows-node-chip">${_esc(NODE_LABELS[t] || t)} ×${n}</span>`)
            .join('');
    }

    /** Minimal valid flow definition for newly created flows. */
    function _emptyFlowDef() {
        return {
            nodes: [
                { id: 'start', type: 'start', label: 'Start', position: { x: 250, y: 50 } },
                { id: 'end',   type: 'end',   label: 'End',   position: { x: 250, y: 400 } },
            ],
            edges: [], scopes: [], global_params: {},
        };
    }

    // ── Module state ───────────────────────────────────────────────────────────

    let _root          = null;
    let _profileFlows  = [];
    let _platformFlows = [];
    let _activeFlowId  = null;   // selected in Profile Flows list
    let _activePlatId  = null;   // selected in Platform Flows list
    let _activeSubTab  = 'profile';
    let _isAdmin       = false;
    let _componentMeta = {};     // flow component object (manifest-driven: supports_instance_io, icon, accent)

    // Lazy import of the shared instance export/import UI (flowsHandler is a classic
    // IIFE script, so use a dynamic import like the renderer import below).
    const _io = () => import('/static/js/handlers/instancePortabilityUI.js?v=1.0');
    const _ioMeta = () => ({
        component_id: 'flow',
        display_name: _componentMeta.display_name || 'Flow',
        icon_svg: _componentMeta.icon_svg,
        icon_accent: _componentMeta.icon_accent,
    });

    // ══════════════════════════════════════════════════════════════════════════
    // Entry point
    // ══════════════════════════════════════════════════════════════════════════

    function initFlowsTab(containerId, comp) {
        _injectCSS();
        _componentMeta = comp || {};
        _root = document.getElementById(containerId);
        if (!_root) return;

        const u = window.authClient?.getUser();
        _isAdmin = u?.profile_tier === 'admin' || !!u?.is_admin;

        _root.innerHTML = `
            <div class="flows-sub-tabs" id="flows-sub-tabs">
                <button class="flows-sub-tab active" data-subtab="profile">
                    ${ICONS.flow} Profile Flows
                </button>
                <button class="flows-sub-tab" data-subtab="platform">
                    ${ICONS.platform} Platform Flows
                </button>
            </div>
            <div id="flows-panel-profile"></div>
            <div id="flows-panel-platform" class="hidden"></div>
        `;

        _root.querySelectorAll('.flows-sub-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                _root.querySelectorAll('.flows-sub-tab').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                _activeSubTab = btn.dataset.subtab;
                _root.querySelectorAll('[id^="flows-panel-"]').forEach(p => p.classList.add('hidden'));
                document.getElementById(`flows-panel-${_activeSubTab}`)?.classList.remove('hidden');
            });
        });

        _renderProfileTab();
        _renderPlatformTab();
    }

    // ══════════════════════════════════════════════════════════════════════════
    // PROFILE FLOWS  (is_template=0 — user's personal flows)
    // ══════════════════════════════════════════════════════════════════════════

    async function _renderProfileTab() {
        const panel = document.getElementById('flows-panel-profile');
        if (!panel) return;
        panel.innerHTML = `
            <div class="flows-split">
                <div class="flows-list" id="prof-flows-list"></div>
                <div class="flows-detail hidden-panel" id="prof-flows-detail"></div>
            </div>`;
        await _loadAndRenderProfileFlows();
    }

    async function _loadAndRenderProfileFlows() {
        const list = document.getElementById('prof-flows-list');
        if (!list) return;
        try {
            const data = await _api('GET', '/flows?include_templates=false');
            _profileFlows = data.flows || [];
            _renderProfileFlowList(_profileFlows, list);
            if (_profileFlows.length) {
                const toSelect = _activeFlowId
                    ? (_profileFlows.find(f => f.flow_id === _activeFlowId) || _profileFlows[0])
                    : _profileFlows[0];
                _openProfileFlowPanel(toSelect);
            }
        } catch (e) {
            list.innerHTML = `<div style="color:var(--text-muted);padding:8px;font-size:13px">Failed to load flows: ${_esc(e.message)}</div>`;
        }
    }

    function _renderProfileFlowList(flows, container) {
        const hdr = `
            <div class="flows-section-hdr">
                <span class="flows-section-title">${flows.length} Flow${flows.length !== 1 ? 's' : ''}</span>
                <div style="display:flex;align-items:center;gap:6px">
                    ${_componentMeta.supports_instance_io ? `<button class="flows-import-btn" id="prof-flow-import-btn" title="Import a flow from a file">${ICONS.import} Import</button>` : ''}
                    <button class="flows-new-btn" id="prof-flow-new-btn">${ICONS.plus} New Flow</button>
                </div>
            </div>`;

        container.innerHTML = flows.length
            ? hdr + flows.map(_profileFlowRowHTML).join('')
            : hdr + `<div style="padding:24px 8px;text-align:center;color:var(--text-muted);font-size:13px;">
                No flows yet.<br>
                <span style="font-size:12px;opacity:.7">Create one here or say "create a flow" in chat.</span>
              </div>`;

        container.querySelector('#prof-flow-new-btn')?.addEventListener('click', async e => {
            e.stopPropagation();
            // PROP-007: "New Flow" opens the topology picker (shape first, then the agents
            // that fill its slots), pre-filled from the active profile when it is a
            // coordinator. The blank editor stays the fallback if the picker cannot load.
            const sourceProfileId = window.state?.currentResourcePanelProfileId
                || window.currentResourcePanelProfileId || null;
            try {
                const m = await import('/api/v1/components/flow/renderer');
                if (typeof m.openTopologyPicker !== 'function') throw new Error('picker unavailable');
                await m.openTopologyPicker({
                    sourceProfileId,
                    onCreated: async (flow) => {
                        if (flow && flow.flow_id) _activeFlowId = flow.flow_id;
                        await _loadAndRenderProfileFlows();
                        if (flow && flow.flow_id) _openFlowCanvas(flow.flow_id);
                    },
                });
            } catch (_) {
                _openProfileFlowPanel(null);
            }
        });
        container.querySelector('#prof-flow-import-btn')?.addEventListener('click', async e => {
            e.stopPropagation();
            const m = await _io();
            m.importInstance(_ioMeta(), { onDone: () => _loadAndRenderProfileFlows() });
        });
        container.querySelectorAll('.flow-row[data-flow-id]').forEach(row => {
            row.addEventListener('click', e => {
                if (e.target.closest('.flow-act-btn')) return;
                const flow = _profileFlows.find(f => f.flow_id === row.dataset.flowId);
                if (flow) _openProfileFlowPanel(flow);
            });
            _wireProfileFlowRowActions(row);
        });
    }

    function _profileFlowRowHTML(flow) {
        const { total, types } = _nodeTypeSummary(flow.definition);
        const lastRan   = flow.last_run_at ? _relTime(flow.last_run_at) : null;
        const dotClass  = STATUS_DOT[flow.last_run_status] || 'gray';
        const isActive  = flow.flow_id === _activeFlowId;
        const chips     = _nodeChipsHTML(types)
            // PROP-007: a flow whose template slots are still open says so on its row.
            + (flow.open_slot_count > 0
                ? `<span class="flow-slot-chip" title="Open template slots — roles that still need an agent">${flow.open_slot_count} slot${flow.open_slot_count === 1 ? '' : 's'} to fill</span>`
                : '');

        return `
        <div class="flow-row${isActive ? ' active' : ''}" data-flow-id="${_esc(flow.flow_id)}" data-flow-name="${_esc(flow.name)}">
            <span class="flow-dot ${dotClass}"></span>
            <div style="flex:1;min-width:0">
                <div style="font-size:13px;font-weight:500;color:var(--text-primary);
                            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px">
                    ${_esc(flow.name)}
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                    <span class="flow-meta">${total} node${total !== 1 ? 's' : ''}</span>
                    ${lastRan ? `<span class="flow-meta">${ICONS.clock} ${lastRan}</span>` : ''}
                    ${chips ? `<div class="flows-node-chips">${chips}</div>` : ''}
                </div>
            </div>
            <div class="flow-row-actions">
                <button class="flow-act-btn" data-action="canvas" title="Open Canvas">${ICONS.canvas}</button>
                <button class="flow-act-btn" data-action="run"    title="Run Now">${ICONS.play}</button>
                ${_componentMeta.supports_instance_io ? `<button class="flow-act-btn" data-action="export" title="Export Flow">${ICONS.export}</button>` : ''}
                ${_isAdmin ? `<button class="flow-act-btn promote" data-action="promote" title="Promote to Platform Flows">${ICONS.promote}</button>` : ''}
                <button class="flow-act-btn danger" data-action="delete" title="Delete">${ICONS.trash}</button>
            </div>
        </div>`;
    }

    function _wireProfileFlowRowActions(row) {
        const flowId = row.dataset.flowId;

        const canvasBtn = row.querySelector('[data-action="canvas"]');
        console.log('[FlowCanvas] Wiring row canvas btn for flowId:', flowId, '| btn found:', !!canvasBtn);
        if (canvasBtn) {
            _registerCanvasBtn(canvasBtn);
            canvasBtn.addEventListener('click', e => {
                console.log('[FlowCanvas] Row canvas btn clicked, flowId:', flowId);
                e.stopPropagation();
                _openFlowCanvas(flowId);
            });
        }

        row.querySelector('[data-action="run"]')?.addEventListener('click', async e => {
            e.stopPropagation();
            const btn = e.currentTarget;
            btn.innerHTML = `<span class="flows-spinner"></span>`;
            btn.disabled = true;
            try {
                await _api('POST', `/flows/${flowId}/execute`);
                btn.innerHTML = ICONS.check;
                btn.style.color = '#22c55e';
                _toast('Flow started');
                setTimeout(() => { btn.innerHTML = ICONS.play; btn.style.color = ''; btn.disabled = false; }, 2000);
            } catch (err) {
                btn.innerHTML = ICONS.play;
                btn.disabled = false;
                _toast(err.message, 'error');
            }
        });

        if (_isAdmin) {
            row.querySelector('[data-action="promote"]')?.addEventListener('click', async e => {
                e.stopPropagation();
                const btn = e.currentTarget;
                btn.innerHTML = `<span class="flows-spinner"></span>`;
                btn.disabled = true;
                try {
                    await _api('POST', `/flows/${flowId}/toggle-platform`, { is_template: true });
                    _toast('Flow promoted to Platform Flows');
                    await Promise.all([_loadAndRenderProfileFlows(), _loadAndRenderPlatformFlows()]);
                } catch (err) {
                    btn.innerHTML = ICONS.promote;
                    btn.disabled = false;
                    _toast(err.message, 'error');
                }
            });
        }

        row.querySelector('[data-action="export"]')?.addEventListener('click', async e => {
            e.stopPropagation();
            const m = await _io();
            m.exportInstance('flow', flowId, row.dataset.flowName || flowId);
        });

        row.querySelector('[data-action="delete"]')?.addEventListener('click', e => {
            e.stopPropagation();
            _confirmDeleteProfileFlow(flowId, row);
        });
    }

    function _confirmDeleteProfileFlow(flowId, row) {
        const flow = _profileFlows.find(f => f.flow_id === flowId);
        if (!flow) return;

        const chip = document.createElement('div');
        chip.style.cssText = 'padding:8px';
        chip.innerHTML = `<div class="flows-undo-chip">
            Deleting "${_esc(flow.name)}" &nbsp;
            <button class="flows-undo-btn" id="undo-del-${_esc(flowId)}">Undo</button>
        </div>`;
        row.replaceWith(chip);

        let cancelled = false;
        chip.querySelector(`#undo-del-${flowId}`)?.addEventListener('click', () => {
            cancelled = true;
            _loadAndRenderProfileFlows();
        });

        setTimeout(async () => {
            if (cancelled) return;
            try {
                await _api('DELETE', `/flows/${flowId}`);
                _profileFlows = _profileFlows.filter(f => f.flow_id !== flowId);
                if (_activeFlowId === flowId) {
                    _activeFlowId = null;
                    const d = document.getElementById('prof-flows-detail');
                    if (d) { d.innerHTML = ''; d.classList.add('hidden-panel'); }
                }
                chip.remove();
                const title = document.querySelector('#prof-flows-list .flows-section-title');
                if (title) title.textContent = `${_profileFlows.length} Flow${_profileFlows.length !== 1 ? 's' : ''}`;
                _toast('Flow deleted');
            } catch (err) {
                _loadAndRenderProfileFlows();
                _toast(err.message, 'error');
            }
        }, 4000);
    }

    // ── Profile Flow detail / edit panel ──────────────────────────────────────

    function _openProfileFlowPanel(flow) {
        const detail = document.getElementById('prof-flows-detail');
        if (!detail) return;
        _activeFlowId = flow?.flow_id || null;
        document.querySelectorAll('.flow-row[data-flow-id]').forEach(r =>
            r.classList.toggle('active', r.dataset.flowId === _activeFlowId)
        );
        detail.classList.remove('hidden-panel');
        detail.innerHTML = _profileFlowFormHTML(flow);
        _wireProfileFlowForm(flow, detail);
    }

    function _profileFlowFormHTML(flow) {
        const isNew = !flow;
        const { total, types } = flow ? _nodeTypeSummary(flow.definition) : { total: 0, types: {} };
        const chips = _nodeChipsHTML(types);
        const summary = total
            ? `${total} node${total !== 1 ? 's' : ''}${chips ? ' · ' + Object.entries(types).map(([t,n]) => `${NODE_LABELS[t]||t}×${n}`).join(' ') : ''}`
            : 'Empty flow';

        return `
        <div>
            <div class="flows-detail-title">${isNew ? 'New Flow' : _esc(flow.name)}</div>
            <div class="flows-detail-sub">${isNew ? 'Create a personal flow' : _esc(summary)}</div>

            <div style="display:flex;flex-direction:column;gap:12px">
                <div>
                    <label class="flows-field-label">Name</label>
                    <input class="flows-input" id="ff-name" placeholder="My workflow" value="${_esc(flow?.name || '')}">
                </div>
                <div>
                    <label class="flows-field-label">Description <span style="opacity:.5">(optional)</span></label>
                    <textarea class="flows-input flows-textarea" id="ff-desc" placeholder="What this flow does...">${_esc(flow?.description || '')}</textarea>
                </div>
            </div>

            <hr class="flows-divider">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                ${!isNew ? `<button class="flows-btn flows-btn-canvas" id="ff-canvas">${ICONS.canvas} Open Canvas</button>` : ''}
                <button class="flows-btn flows-btn-primary" id="ff-save">${isNew ? ICONS.plus + ' Create' : ICONS.check + ' Save'}</button>
                <button class="flows-btn flows-btn-secondary" id="ff-cancel">${ICONS.x} Cancel</button>
                ${!isNew ? `<button class="flows-btn flows-btn-run" id="ff-run" style="margin-left:auto">${ICONS.play} Run Now</button>` : ''}
            </div>

            ${!isNew ? `
            <hr class="flows-divider">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                ${_isAdmin ? `<button class="flows-btn flows-btn-promote" id="ff-promote" style="padding:4px 10px;font-size:12px">${ICONS.promote} Promote to Platform</button>` : ''}
                <button class="flows-btn flows-btn-danger" id="ff-delete" style="padding:4px 10px;font-size:12px">${ICONS.trash} Delete</button>
            </div>

            <hr class="flows-divider">
            <div>
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                    <span style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">Recent Runs</span>
                    <button class="flows-btn flows-btn-secondary" id="ff-history" style="padding:3px 9px;font-size:11px">${ICONS.history} Load history</button>
                </div>
                <div id="ff-history-list"><span style="font-size:12px;color:var(--text-muted)">Click "Load history" to view recent runs.</span></div>
            </div>` : ''}
        </div>`;
    }

    function _wireProfileFlowForm(flow, container) {
        const isNew  = !flow;
        const flowId = flow?.flow_id;

        const ffCanvas = container.querySelector('#ff-canvas');
        console.log('[FlowCanvas] Wiring detail-panel #ff-canvas for flowId:', flowId, '| btn found:', !!ffCanvas);
        if (ffCanvas) {
            _registerCanvasBtn(ffCanvas);
            ffCanvas.addEventListener('click', () => {
                console.log('[FlowCanvas] Detail-panel Open Canvas clicked, flowId:', flowId);
                _openFlowCanvas(flowId);
            });
        }

        container.querySelector('#ff-save')?.addEventListener('click', async () => {
            const btn  = container.querySelector('#ff-save');
            const name = container.querySelector('#ff-name')?.value.trim();
            const desc = container.querySelector('#ff-desc')?.value.trim();
            if (!name) { _toast('Name is required', 'error'); return; }
            btn.disabled = true;
            btn.innerHTML = `<span class="flows-spinner"></span> Saving…`;
            try {
                let saved;
                if (isNew) {
                    const res = await _api('POST', '/flows', { name, description: desc, definition: _emptyFlowDef() });
                    saved = res.flow;
                    _profileFlows.unshift(saved);
                    _activeFlowId = saved.flow_id;
                    _toast('Flow created');
                } else {
                    const res = await _api('PUT', `/flows/${flowId}`, { name, description: desc });
                    saved = res.flow;
                    const idx = _profileFlows.findIndex(f => f.flow_id === flowId);
                    if (idx >= 0) _profileFlows[idx] = saved;
                    _toast('Flow saved');
                }
                await _loadAndRenderProfileFlows();
                const refreshed = _profileFlows.find(f => f.flow_id === _activeFlowId);
                if (refreshed) _openProfileFlowPanel(refreshed);
            } catch (err) {
                btn.disabled = false;
                btn.innerHTML = isNew ? ICONS.plus + ' Create' : ICONS.check + ' Save';
                _toast(err.message, 'error');
            }
        });

        container.querySelector('#ff-cancel')?.addEventListener('click', () => {
            const d = document.getElementById('prof-flows-detail');
            if (d) { d.innerHTML = ''; d.classList.add('hidden-panel'); }
            _activeFlowId = null;
            document.querySelectorAll('.flow-row[data-flow-id]').forEach(r => r.classList.remove('active'));
        });

        container.querySelector('#ff-run')?.addEventListener('click', async () => {
            const btn = container.querySelector('#ff-run');
            btn.disabled = true;
            btn.innerHTML = `<span class="flows-spinner"></span> Running…`;
            try {
                await _api('POST', `/flows/${flowId}/execute`);
                btn.innerHTML = ICONS.play + ' Run Now';
                btn.disabled = false;
                _toast('Flow started');
            } catch (err) {
                btn.innerHTML = ICONS.play + ' Run Now';
                btn.disabled = false;
                _toast(err.message, 'error');
            }
        });

        if (_isAdmin) {
            container.querySelector('#ff-promote')?.addEventListener('click', async () => {
                const btn = container.querySelector('#ff-promote');
                btn.disabled = true;
                btn.innerHTML = `<span class="flows-spinner"></span>`;
                try {
                    await _api('POST', `/flows/${flowId}/toggle-platform`, { is_template: true });
                    _toast('Flow promoted to Platform Flows');
                    await Promise.all([_loadAndRenderProfileFlows(), _loadAndRenderPlatformFlows()]);
                } catch (err) {
                    btn.innerHTML = ICONS.promote + ' Promote to Platform';
                    btn.disabled = false;
                    _toast(err.message, 'error');
                }
            });
        }

        container.querySelector('#ff-delete')?.addEventListener('click', () => {
            if (!flow) return;
            const row = document.querySelector(`.flow-row[data-flow-id="${flowId}"]`);
            if (row) _confirmDeleteProfileFlow(flowId, row);
            const d = document.getElementById('prof-flows-detail');
            if (d) { d.innerHTML = ''; d.classList.add('hidden-panel'); }
            _activeFlowId = null;
        });

        container.querySelector('#ff-history')?.addEventListener('click', async () => {
            if (!flow) return;
            const btn  = container.querySelector('#ff-history');
            const list = container.querySelector('#ff-history-list');
            btn.disabled = true;
            btn.innerHTML = `<span class="flows-spinner"></span>`;
            try {
                const data = await _api('GET', `/flows/${flowId}/runs?limit=10`);
                const runs = data.runs || [];
                list.innerHTML = runs.length
                    ? runs.map(_runItemHTML).join('')
                    : `<span style="font-size:12px;color:var(--text-muted)">No runs yet.</span>`;
                btn.style.display = 'none';
            } catch (err) {
                list.innerHTML = `<span style="font-size:12px;color:#ef4444">${_esc(err.message)}</span>`;
                btn.disabled = false;
                btn.innerHTML = ICONS.history + ' Load history';
            }
        });
    }

    function _runItemHTML(run) {
        const dotColor = { complete: '#22c55e', error: '#ef4444', running: '#a855f7', cancelled: '#6b7280' }[run.status] || '#f59e0b';
        const started  = run.started_at   ? _relTime(run.started_at) : '';
        const dur      = (run.started_at && run.completed_at)
            ? `${Math.round((new Date(run.completed_at) - new Date(run.started_at)) / 1000)}s`
            : run.status === 'running' ? 'running…' : '';
        const toks     = (run.tokens_input || run.tokens_output)
            ? `${((run.tokens_input || 0) + (run.tokens_output || 0)).toLocaleString()} tokens` : '';
        return `
        <div class="flows-history-item">
            <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};flex-shrink:0;margin-top:3px"></span>
            <div>
                <div style="font-size:12px;color:var(--text-primary);font-weight:500">
                    ${_esc(run.status)}${started ? ' · ' + started : ''}${dur ? ' · ' + dur : ''}
                </div>
                ${toks ? `<div style="font-size:11px;color:var(--text-muted)">${toks}</div>` : ''}
            </div>
        </div>`;
    }

    // ══════════════════════════════════════════════════════════════════════════
    // PLATFORM FLOWS  (is_template=1 — shared blueprints)
    // ══════════════════════════════════════════════════════════════════════════

    async function _renderPlatformTab() {
        const panel = document.getElementById('flows-panel-platform');
        if (!panel) return;
        panel.innerHTML = `
            <div class="flows-split">
                <div class="flows-list" id="plat-flows-list"></div>
                <div class="flows-detail hidden-panel" id="plat-flows-detail"></div>
            </div>`;
        await _loadAndRenderPlatformFlows();
    }

    async function _loadAndRenderPlatformFlows() {
        const list = document.getElementById('plat-flows-list');
        if (!list) return;
        try {
            const data = await _api('GET', '/flows/platform');
            _platformFlows = data.flows || [];
            _renderPlatformFlowList(_platformFlows, list);
            if (_platformFlows.length) {
                const toSelect = _activePlatId
                    ? (_platformFlows.find(f => f.flow_id === _activePlatId) || _platformFlows[0])
                    : _platformFlows[0];
                _openPlatformFlowPanel(toSelect);
            }
        } catch (e) {
            list.innerHTML = `<div style="color:var(--text-muted);padding:8px;font-size:13px">Failed to load platform flows: ${_esc(e.message)}</div>`;
        }
    }

    function _renderPlatformFlowList(flows, container) {
        const hdr = `
            <div class="flows-section-hdr">
                <span class="flows-section-title">${flows.length} Blueprint${flows.length !== 1 ? 's' : ''}</span>
            </div>`;

        const emptyMsg = _isAdmin
            ? 'No platform flows yet.<br><span style="font-size:12px;opacity:.7">Promote a profile flow to make it a reusable blueprint.</span>'
            : 'No platform flows yet.<br><span style="font-size:12px;opacity:.7">An admin can promote flows as shared blueprints.</span>';

        container.innerHTML = flows.length
            ? hdr + flows.map(_platformFlowRowHTML).join('')
            : hdr + `<div style="padding:24px 8px;text-align:center;color:var(--text-muted);font-size:13px;">${emptyMsg}</div>`;

        container.querySelectorAll('.flow-row[data-plat-flow-id]').forEach(row => {
            row.addEventListener('click', e => {
                if (e.target.closest('.flow-act-btn')) return;
                const flow = _platformFlows.find(f => f.flow_id === row.dataset.platFlowId);
                if (flow) _openPlatformFlowPanel(flow);
            });
            _wirePlatformFlowRowActions(row);
        });
    }

    function _platformFlowRowHTML(flow) {
        const { total, types } = _nodeTypeSummary(flow.definition);
        const chips    = _nodeChipsHTML(types);
        const isActive = flow.flow_id === _activePlatId;

        return `
        <div class="flow-row${isActive ? ' active' : ''}" data-plat-flow-id="${_esc(flow.flow_id)}">
            <span class="flow-dot purple"></span>
            <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">
                    <span style="font-size:13px;font-weight:500;color:var(--text-primary);
                                 white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px">
                        ${_esc(flow.name)}
                    </span>
                    <span class="flows-template-badge">Blueprint</span>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                    <span class="flow-meta">${total} node${total !== 1 ? 's' : ''}</span>
                    ${chips ? `<div class="flows-node-chips">${chips}</div>` : ''}
                </div>
            </div>
            <div class="flow-row-actions">
                <button class="flow-act-btn" data-action="fork"   title="Fork to Profile Flows">${ICONS.fork}</button>
                ${_isAdmin ? `<button class="flow-act-btn danger" data-action="demote" title="Remove from Platform">${ICONS.demote}</button>` : ''}
            </div>
        </div>`;
    }

    function _wirePlatformFlowRowActions(row) {
        const flowId = row.dataset.platFlowId;

        row.querySelector('[data-action="fork"]')?.addEventListener('click', async e => {
            e.stopPropagation();
            const btn = e.currentTarget;
            btn.innerHTML = `<span class="flows-spinner"></span>`;
            btn.disabled = true;
            try {
                const flow = _platformFlows.find(f => f.flow_id === flowId);
                await _api('POST', `/flows/${flowId}/fork`);
                btn.innerHTML = ICONS.check;
                btn.style.color = '#22c55e';
                _toast(`"${flow?.name || 'Flow'}" forked to Profile Flows`);
                setTimeout(() => { btn.innerHTML = ICONS.fork; btn.style.color = ''; btn.disabled = false; }, 2000);
                await _loadAndRenderProfileFlows();
            } catch (err) {
                btn.innerHTML = ICONS.fork;
                btn.disabled = false;
                _toast(err.message, 'error');
            }
        });

        if (_isAdmin) {
            row.querySelector('[data-action="demote"]')?.addEventListener('click', async e => {
                e.stopPropagation();
                const btn = e.currentTarget;
                btn.innerHTML = `<span class="flows-spinner"></span>`;
                btn.disabled = true;
                try {
                    await _api('POST', `/flows/${flowId}/toggle-platform`, { is_template: false });
                    _toast('Blueprint removed from Platform Flows');
                    _activePlatId = null;
                    await Promise.all([_loadAndRenderPlatformFlows(), _loadAndRenderProfileFlows()]);
                } catch (err) {
                    btn.innerHTML = ICONS.demote;
                    btn.disabled = false;
                    _toast(err.message, 'error');
                }
            });
        }
    }

    // ── Platform Flow detail panel ─────────────────────────────────────────────

    function _openPlatformFlowPanel(flow) {
        const detail = document.getElementById('plat-flows-detail');
        if (!detail) return;
        _activePlatId = flow.flow_id;
        document.querySelectorAll('.flow-row[data-plat-flow-id]').forEach(r =>
            r.classList.toggle('active', r.dataset.platFlowId === _activePlatId)
        );
        detail.classList.remove('hidden-panel');
        detail.innerHTML = _platformFlowDetailHTML(flow);
        _wirePlatformFlowDetail(flow, detail);
    }

    function _platformFlowDetailHTML(flow) {
        const { total, types } = _nodeTypeSummary(flow.definition);
        const chips   = _nodeChipsHTML(types);
        const updated = flow.updated_at ? _relTime(flow.updated_at) : null;

        return `
        <div>
            <div class="flows-detail-title">${_esc(flow.name)}</div>
            <div class="flows-detail-sub">${_esc(flow.description || 'No description')}</div>

            ${chips ? `<div class="flows-node-chips" style="margin-bottom:12px">${chips}</div>` : ''}

            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;align-items:center">
                <span class="flows-template-badge">Blueprint</span>
                <span style="font-size:11px;color:var(--text-muted)">v${_esc(flow.version || '1.0.0')}</span>
                ${updated ? `<span style="font-size:11px;color:var(--text-muted)">Updated ${updated}</span>` : ''}
                <span style="font-size:11px;color:var(--text-muted)">${total} node${total !== 1 ? 's' : ''}</span>
            </div>

            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <button class="flows-btn flows-btn-fork"   id="pf-fork"  >${ICONS.fork}   Fork to Profile Flows</button>
                <button class="flows-btn flows-btn-canvas" id="pf-canvas">${ICONS.canvas} Preview Canvas</button>
            </div>

            ${_isAdmin ? `
            <hr class="flows-divider">
            <div style="font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px">Admin</div>
            <div style="display:flex;flex-direction:column;gap:10px">
                <div>
                    <label class="flows-field-label">Name</label>
                    <input class="flows-input" id="pf-name" value="${_esc(flow.name)}">
                </div>
                <div>
                    <label class="flows-field-label">Description</label>
                    <textarea class="flows-input flows-textarea" id="pf-desc">${_esc(flow.description || '')}</textarea>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:10px;flex-wrap:wrap">
                <button class="flows-btn flows-btn-primary" id="pf-save">${ICONS.check} Save</button>
                <button class="flows-btn flows-btn-danger"  id="pf-demote" style="margin-left:auto">${ICONS.demote} Remove from Platform</button>
            </div>` : ''}
        </div>`;
    }

    function _wirePlatformFlowDetail(flow, container) {
        container.querySelector('#pf-fork')?.addEventListener('click', async () => {
            const btn = container.querySelector('#pf-fork');
            btn.disabled = true;
            btn.innerHTML = `<span class="flows-spinner"></span> Forking…`;
            try {
                await _api('POST', `/flows/${flow.flow_id}/fork`);
                btn.innerHTML = ICONS.check + ' Forked!';
                btn.style.background = 'rgba(34,197,94,.15)';
                btn.style.borderColor = 'rgba(34,197,94,.3)';
                btn.style.color = '#22c55e';
                _toast(`"${flow.name}" forked to Profile Flows`);
                setTimeout(() => {
                    btn.innerHTML = ICONS.fork + ' Fork to Profile Flows';
                    btn.style.background = btn.style.borderColor = btn.style.color = '';
                    btn.disabled = false;
                }, 3000);
                await _loadAndRenderProfileFlows();
            } catch (err) {
                btn.innerHTML = ICONS.fork + ' Fork to Profile Flows';
                btn.disabled = false;
                _toast(err.message, 'error');
            }
        });

        container.querySelector('#pf-canvas')?.addEventListener('click', () => _openFlowCanvas(flow.flow_id));

        if (_isAdmin) {
            container.querySelector('#pf-save')?.addEventListener('click', async () => {
                const btn  = container.querySelector('#pf-save');
                const name = container.querySelector('#pf-name')?.value.trim();
                const desc = container.querySelector('#pf-desc')?.value.trim();
                if (!name) { _toast('Name is required', 'error'); return; }
                btn.disabled = true;
                btn.innerHTML = `<span class="flows-spinner"></span>`;
                try {
                    const res = await _api('PUT', `/flows/${flow.flow_id}/platform-meta`, { name, description: desc });
                    const idx = _platformFlows.findIndex(f => f.flow_id === flow.flow_id);
                    if (idx >= 0) _platformFlows[idx] = res.flow;
                    btn.innerHTML = ICONS.check + ' Save';
                    btn.disabled = false;
                    _toast('Blueprint updated');
                    await _loadAndRenderPlatformFlows();
                } catch (err) {
                    btn.innerHTML = ICONS.check + ' Save';
                    btn.disabled = false;
                    _toast(err.message, 'error');
                }
            });

            container.querySelector('#pf-demote')?.addEventListener('click', async () => {
                const btn = container.querySelector('#pf-demote');
                btn.disabled = true;
                btn.innerHTML = `<span class="flows-spinner"></span>`;
                try {
                    await _api('POST', `/flows/${flow.flow_id}/toggle-platform`, { is_template: false });
                    _toast('Blueprint removed from Platform Flows');
                    _activePlatId = null;
                    await Promise.all([_loadAndRenderPlatformFlows(), _loadAndRenderProfileFlows()]);
                } catch (err) {
                    btn.innerHTML = ICONS.demote + ' Remove from Platform';
                    btn.disabled = false;
                    _toast(err.message, 'error');
                }
            });
        }
    }

    // ── Canvas integration ─────────────────────────────────────────────────────

    function _openFlowCanvas(flowId) {
        if (!_hasSession()) {
            _toast('Start a conversation first to open the flow canvas', 'error');
            return;
        }
        console.log('[FlowCanvas] _openFlowCanvas called, flowId:', flowId);

        // Navigate to conversation view first if we're currently in another view.
        const conversationView = document.getElementById('conversation-view');
        const isActive = conversationView?.classList.contains('active');
        console.log('[FlowCanvas] conversation-view active:', isActive);
        if (!isActive) {
            const navBtn = document.getElementById('view-switch-conversation');
            console.log('[FlowCanvas] nav button found:', !!navBtn);
            navBtn?.click();
        }

        // Load the flow spec and renderer in parallel; ES module import is cached
        // after the first load so repeated calls are instant.
        console.log('[FlowCanvas] Starting Promise.all: api + import');
        Promise.all([
            _api('GET', `/flows/${flowId}`),
            import('/api/v1/components/flow/renderer'),
        ])
            .then(([data, mod]) => {
                console.log('[FlowCanvas] Promise.all resolved. flow_id:', data?.flow_id, '| openFlowSplitPanel type:', typeof mod?.openFlowSplitPanel);
                console.log('[FlowCanvas] definition nodes:', data?.definition?.nodes?.length ?? 'undefined', '| edges:', data?.definition?.edges?.length ?? 'undefined');
                if (!data || !data.flow_id) { _toast('Flow not found', 'error'); return; }
                const openFlowSplitPanel = mod.openFlowSplitPanel;
                if (typeof openFlowSplitPanel !== 'function') {
                    console.error('[FlowCanvas] openFlowSplitPanel is not a function:', mod);
                    _toast('Canvas renderer not available', 'error');
                    return;
                }
                openFlowSplitPanel({
                    view: 'canvas',
                    flow: data,
                    definition: data.definition || { nodes: [], edges: [], scopes: [] },
                    action: 'open',
                });
            })
            .catch(err => {
                console.error('[FlowCanvas] Error:', err);
                _toast(err?.message || 'Failed to open canvas', 'error');
            });
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    window.flowsHandler = { initFlowsTab };

})();
