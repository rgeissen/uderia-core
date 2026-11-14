/**
 * Files Tab — Files component detail (Components → Files)
 *
 * Renders a "Files" tab inside the Files component detail panel. Lists the user's files with
 * search and the panel's kind filters, and manages them in place: rename, keep, download,
 * delete and restore, plus "Open in Files panel" for preview, attach and the rest. Mirrors the
 * Canvases / Dashboards / Flows instance-management tabs, so a file is managed like any other
 * component instance.
 *
 * Every action goes through the Files REST face (/v1/files…): downloads leave through the one
 * governed exit (GET /v1/files/<id>/download — tier policy + audit), and the tab follows changes
 * made anywhere else through the component change event (`UderiaBus.onComponent('files')`, page event `uderia:component-changed`).
 *
 * Entry point: window.filesTabHandler.initFilesTab(containerId, comp)
 * Called from componentHandler.js after the Files tab panel is in the DOM.
 */

(function () {
    'use strict';

    const AUTH = () => localStorage.getItem('tda_auth_token');
    const BASE = '/api/v1';

    const KINDS = [
        ['all', 'All'], ['table', 'Tables'], ['document', 'Documents'], ['image', 'Images'],
        ['export', 'Exports'], ['upload', 'Uploads'], ['deleted', 'Recently deleted'],
    ];
    const IFOC = {
        llm_only: { label: 'Ideate', color: '#4ade80' }, rag_focused: { label: 'Focus', color: '#3b82f6' },
        tool_enabled: { label: 'Optimize', color: '#9333ea' }, genie: { label: 'Coordinate', color: '#F15F22' },
    };

    // ── CSS ────────────────────────────────────────────────────────────────────

    function _injectCSS() {
        if (document.getElementById('files-tab-css')) return;
        const s = document.createElement('style');
        s.id = 'files-tab-css';
        s.textContent = `
.ftb-bar          { display:flex; flex-direction:column; gap:8px; margin-bottom:10px; }
.ftb-bar-row      { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.ftb-search       { flex:1 1 220px; display:flex; align-items:center; gap:8px; height:32px; padding:0 10px;
                    border:1px solid var(--border-primary); border-radius:8px; background:var(--input-bg, var(--bg-primary));
                    color:var(--text-muted); }
.ftb-search svg   { width:14px; height:14px; flex:0 0 auto; }
.ftb-search input { flex:1; min-width:0; border:0; background:transparent; color:var(--text-primary); font:inherit; font-size:13px; outline:none; }
.ftb-store        { font-size:12px; color:var(--text-muted); white-space:nowrap; }
.ftb-store.warn   { color:var(--teradata-orange, #F15F22); }
.ftb-pills        { display:flex; gap:4px; flex-wrap:wrap; }
.ftb-pills .ind-tab { padding:.3rem .65rem; font-size:.75rem; }
.ftb-pills .ftb-n { color:var(--text-muted); font-variant-numeric:tabular-nums; font-weight:400; margin-left:4px; }
.ftb-pills .ind-tab.active .ftb-n { color:inherit; opacity:.8; }
.ftb-acts .card-btn svg, .ftb-bar .card-btn svg { width:14px; height:14px; }

.ftb-split        { display:flex; gap:0; height:520px; overflow:hidden; }
.ftb-list         { flex:0 0 55%; overflow-y:auto; padding-right:12px; border-right:1px solid var(--border-secondary, rgba(255,255,255,0.06)); }
.ftb-detail       { flex:1; overflow-y:auto; padding-left:16px; min-width:0; }
.ftb-detail.ftb-hidden { display:none; }
@media (max-width: 760px) {
  .ftb-split { flex-direction:column; height:auto; }
  .ftb-list { flex:none; max-height:360px; padding-right:0; border-right:0; border-bottom:1px solid var(--border-secondary); padding-bottom:8px; }
  .ftb-detail { padding-left:0; padding-top:12px; }
}

.ftb-row          { display:flex; align-items:center; gap:8px; padding:8px; border-radius:10px; cursor:pointer;
                    transition:background .12s; border:1px solid transparent; }
.ftb-row:hover    { background:var(--hover-bg-strong, rgba(255,255,255,0.05)); }
.ftb-row.active   { background:rgba(120,113,108,.14); border-color:rgba(120,113,108,.4); }
.ftb-row + .ftb-row { margin-top:3px; }
.ftb-ic           { width:32px; height:32px; border-radius:8px; flex-shrink:0; display:flex; align-items:center; justify-content:center;
                    background:var(--bg-primary); border:1px solid var(--border-secondary); color:var(--text-secondary); }
.ftb-ic svg       { width:16px; height:16px; }
.ftb-body         { flex:1; min-width:0; }
.ftb-name         { font-size:13px; font-weight:500; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ftb-meta         { font-size:11px; color:var(--text-muted); margin-top:1px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ftb-badge        { flex-shrink:0; font-size:11px; color:var(--text-muted); border:1px solid var(--border-primary); border-radius:999px; padding:0 7px; }
.ftb-row-acts     { display:flex; gap:2px; flex-shrink:0; opacity:0; transition:opacity .12s; }
.ftb-row:hover .ftb-row-acts, .ftb-row:focus-within .ftb-row-acts, .ftb-row.active .ftb-row-acts { opacity:1; }
.ftb-ibtn         { display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px; border-radius:6px;
                    border:none; background:transparent; cursor:pointer; color:var(--text-muted); transition:background .12s, color .12s; }
.ftb-ibtn svg     { width:14px; height:14px; }
.ftb-ibtn:hover   { background:var(--hover-bg-strong, rgba(255,255,255,.08)); color:var(--text-primary); }
.ftb-ibtn.danger:hover { background:rgba(239,68,68,.15); color:#ef4444; }

.ftb-empty        { text-align:center; padding:48px 16px; color:var(--text-muted); font-size:13px; }
.ftb-empty svg    { margin:0 auto 12px; display:block; opacity:.35; width:28px; height:28px; }
.ftb-notice       { display:flex; align-items:center; gap:10px; margin-bottom:8px; padding:7px 10px; border-radius:8px; font-size:12px;
                    background:rgba(120,113,108,.14); color:var(--text-primary); }
.ftb-notice button { border:0; background:transparent; padding:0; font-weight:600; cursor:pointer; color:inherit; text-decoration:underline; }

.ftb-d-title      { font-size:15px; font-weight:600; color:var(--text-primary); margin-bottom:2px; word-break:break-word; }
.ftb-d-sub        { font-size:12px; color:var(--text-muted); margin-bottom:12px; }
.ftb-acts         { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }
.ftb-label        { font-size:12px; font-weight:500; color:var(--text-muted); margin-bottom:4px; display:block; }
.ftb-input        { width:100%; padding:7px 10px; border-radius:8px; font-size:13px; border:1px solid var(--border-secondary, rgba(255,255,255,.12));
                    background:var(--bg-overlay, rgba(255,255,255,.04)); color:var(--text-primary); outline:none; box-sizing:border-box; }
.ftb-input:focus  { border-color:rgba(120,113,108,.6); }
.ftb-facts        { width:100%; font-size:12px; border-collapse:collapse; margin:4px 0 12px; }
.ftb-facts th     { text-align:left; font-weight:500; color:var(--text-muted); padding:5px 10px 5px 0; vertical-align:top; white-space:nowrap; width:1%; }
.ftb-facts td     { color:var(--text-primary); padding:5px 0; word-break:break-word; }
.ftb-uses         { list-style:none; margin:0 0 12px; padding:0; font-size:12px; }
.ftb-uses li      { padding:4px 0; color:var(--text-primary); border-top:1px solid var(--border-secondary, rgba(255,255,255,.06)); }
.ftb-uses li span { color:var(--text-muted); }
.ftb-divider      { border:none; border-top:1px solid var(--border-secondary, rgba(255,255,255,.07)); margin:14px 0; }
@keyframes ftb-spin { to { transform:rotate(360deg); } }
.ftb-spinner      { display:inline-block; width:14px; height:14px; border:2px solid var(--border-secondary, rgba(255,255,255,.15));
                    border-top-color:var(--text-secondary); border-radius:50%; animation:ftb-spin .7s linear infinite; }
`;
        document.head.appendChild(s);
    }

    // ── SVG icons (monochrome, currentColor) ────────────────────────────────────

    const _svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
    const ICONS = {
        file: _svg('M7 3h7l5 5v13H7zM14 3v5h5'),
        table: _svg('M4 5h16v14H4zM4 10h16M4 15h16M10 5v14'),
        doc: _svg('M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6'),
        image: _svg('M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01'),
        code: _svg('M9 8l-4 4 4 4M15 8l4 4-4 4'),
        folder: _svg('M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 12h6M9 15h4'),
        search: _svg('M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14zM20 20l-4-4'),
        open: _svg('M14 5l7 7-7 7M21 12H3'),
        download: _svg('M12 4v11m0 0l-4-4m4 4l4-4M5 19h14'),
        trash: _svg('M19 7l-.9 12.1A2 2 0 0 1 16.1 21H7.9a2 2 0 0 1-2-1.9L5 7m5 4v6m4-6v6M9 7V4h6v3M3 7h18'),
        restore: _svg('M3 12a9 9 0 1 0 3-6.7M3 4v5h5'),
        keep: _svg('M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z'),
        edit: _svg('M15.2 5.2l3.6 3.6M16.7 3.7a2.5 2.5 0 0 1 3.6 3.6L6.5 21H3v-3.6z'),
    };

    // ── helpers ────────────────────────────────────────────────────────────────

    async function _api(path, opts = {}) {
        const headers = { 'Content-Type': 'application/json', ...(AUTH() ? { Authorization: `Bearer ${AUTH()}` } : {}) };
        const r = await fetch(BASE + path, { ...opts, headers });
        let body = {};
        try { body = await r.json(); } catch (_) { body = {}; }
        return { ok: r.ok, status: r.status, body };
    }

    const _esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function _when(iso) {
        if (!iso) return '';
        const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime();
        const d = (Date.now() - t) / 1000;
        if (d < 60) return 'just now';
        if (d < 3600) return `${Math.floor(d / 60)} min ago`;
        if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
        if (d < 172800) return 'yesterday';
        return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function _purgeIn(iso) {
        if (!iso) return 'kept until an admin purges it';
        const t = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
        const days = Math.ceil((t - Date.now()) / 86400000);
        return days <= 0 ? 'removed shortly' : days === 1 ? 'removed tomorrow' : `removed in ${days} days`;
    }

    function _glyph(f) {
        // an uploaded file's category is "upload", so the type is read from the extension too
        if (f.category === 'image' || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(f.ext)) return ICONS.image;
        if (f.category === 'table' || ['csv', 'tsv', 'xlsx'].includes(f.ext)) return ICONS.table;
        if (['md', 'markdown', 'txt', 'pdf', 'docx'].includes(f.ext)) return ICONS.doc;
        if (['sql', 'json', 'py', 'js', 'ttl', 'nt', 'rdf', 'jsonld', 'html', 'xml', 'yaml', 'yml'].includes(f.ext)) return ICONS.code;
        return ICONS.file;
    }

    function _agentHtml(f) {
        const i = IFOC[f.profile_type];
        if (!i && !f.profile_tag) return '<span style="color:var(--text-muted)">No agent</span>';
        const dot = i ? `<span aria-hidden="true" style="width:7px;height:7px;border-radius:999px;background:${i.color};display:inline-block;margin-right:5px"></span>` : '';
        return `<span style="display:inline-flex;align-items:center">${dot}${_esc(i ? i.label : '')}${f.profile_tag ? ` <span style="color:var(--text-muted);margin-left:4px">@${_esc(f.profile_tag)}</span>` : ''}</span>`;
    }

    function _origin(f) {
        if (f.source_kind === 'upload') return 'Uploaded';
        if (f.origin_kind === 'scheduled_run') return f.scheduled_task_name ? `Scheduled task · ${f.scheduled_task_name}` : 'Scheduled task';
        if (f.origin_kind === 'flow_run') return (f.source && f.source.flow_name) ? `Flow · ${f.source.flow_name}` : 'Flow run';
        if (f.source_kind === 'agent') return 'Saved by the agent';
        if (f.component_id) return `Export · ${f.component_id.replace(/_/g, ' ')}`;
        return 'Kept from a conversation';
    }

    function _say(msg, type = 'info') {
        if (typeof window.showAppBanner === 'function') window.showAppBanner(msg, type);
    }

    function _confirm(msg, onYes) {
        if (typeof window.showConfirmBanner === 'function') window.showConfirmBanner(msg, onYes);
        else onYes();
    }

    // ── module state ───────────────────────────────────────────────────────────

    const S = { files: [], facets: {}, storage: null, kind: 'all', q: '', sel: null, detail: {}, notice: null, undoId: null, seq: 0 };
    let _root = null;
    let _qTimer = null;
    let _listening = false;

    function _mounted() { return _root && document.body.contains(_root); }

    // ══════════════════════════════════════════════════════════════════════════
    // Entry point
    // ══════════════════════════════════════════════════════════════════════════

    function initFilesTab(containerId) {
        _injectCSS();
        _root = document.getElementById(containerId);
        if (!_root) return;
        S.sel = null; S.detail = {}; S.notice = null; S.undoId = null;

        _root.innerHTML = `
            <div class="ftb-bar">
                <div class="ftb-bar-row">
                    <label class="ftb-search">${ICONS.search}<input type="search" id="ftb-q" placeholder="Search names, conversations, questions, agents" aria-label="Search files" value="${_esc(S.q)}"></label>
                    <span class="ftb-store" id="ftb-store"></span>
                    <button type="button" class="card-btn card-btn--neutral card-btn--sm" id="ftb-open-panel">${ICONS.folder} Open Files panel</button>
                </div>
                <div class="ftb-pills" id="ftb-kinds" role="group" aria-label="Kind"></div>
            </div>
            <div class="ftb-split">
                <div class="ftb-list" id="ftb-list"><span class="ftb-spinner"></span></div>
                <div class="ftb-detail ftb-hidden" id="ftb-detail"></div>
            </div>`;

        _root.querySelector('#ftb-q').addEventListener('input', (e) => {
            clearTimeout(_qTimer);
            _qTimer = setTimeout(() => { S.q = e.target.value.trim(); _load(); }, 220);
        });
        _root.querySelector('#ftb-open-panel').addEventListener('click', () => _openInPanel(null));

        if (!_listening) {
            _listening = true;
            // live everywhere: a file kept, renamed, deleted or used anywhere else reloads the tab
            UderiaBus.onComponent('files', (e) => {
                if (!_mounted()) return;
                const d = (e && e.detail) || {};
                if (d.action === 'used') {
                    const ids = d.ids || [];
                    ids.forEach(id => { delete S.detail[id]; });
                    if (S.sel && ids.includes(S.sel)) _loadDetail(S.sel);
                    return;
                }
                S.detail = {};
                _load();
            });
        }
        _load();
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Data
    // ══════════════════════════════════════════════════════════════════════════

    async function _load() {
        const seq = ++S.seq;
        const p = new URLSearchParams({ kind: S.kind, sort: 'newest' });
        if (S.q) p.set('q', S.q);
        const r = await _api(`/files?${p.toString()}`);
        if (seq !== S.seq || !_mounted()) return;
        if (!r.ok) {
            const list = _root.querySelector('#ftb-list');
            if (list) list.innerHTML = `<div style="color:#ef4444;font-size:13px;padding:8px">${_esc(r.body.message || 'Files could not be loaded.')}</div>`;
            return;
        }
        S.files = r.body.files || [];
        S.facets = r.body.facets || {};
        S.storage = r.body.storage || null;
        if (S.sel && !S.files.some(f => f.id === S.sel)) S.sel = null;
        _renderBar();
        _renderList();
        if (S.sel) _loadDetail(S.sel); else _root.querySelector('#ftb-detail')?.classList.add('ftb-hidden');
    }

    async function _loadDetail(id) {
        if (!S.detail[id]) {
            const r = await _api(`/files/${encodeURIComponent(id)}`);
            if (!r.ok) { _say(r.body.message || 'The file could not be read.', 'error'); return; }
            S.detail[id] = r.body.file;
        }
        if (S.sel === id && _mounted()) _renderDetail(S.detail[id]);
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Render
    // ══════════════════════════════════════════════════════════════════════════

    function _renderBar() {
        const st = S.storage;
        const store = _root.querySelector('#ftb-store');
        if (store) {
            store.textContent = st ? (st.quota_label ? `${st.used_label} of ${st.quota_label} used` : `${st.used_label} used`) : '';
            store.classList.toggle('warn', !!(st && st.warn));
        }
        const host = _root.querySelector('#ftb-kinds');
        if (!host) return;
        host.innerHTML = KINDS.map(([k, label]) => {
            const n = S.facets[k] != null ? S.facets[k] : '';
            return `<button type="button" class="ind-tab ind-tab--pill${S.kind === k ? ' active' : ''}" data-kind="${k}" aria-pressed="${S.kind === k}">${_esc(label)}<span class="ftb-n">${_esc(n)}</span></button>`;
        }).join('');
        host.querySelectorAll('[data-kind]').forEach(b => b.addEventListener('click', () => {
            S.kind = b.dataset.kind; S.sel = null; _load();
        }));
    }

    function _renderList() {
        const list = _root.querySelector('#ftb-list');
        if (!list) return;
        const deleted = S.kind === 'deleted';
        const notice = S.notice
            ? `<div class="ftb-notice" role="status"><span>${_esc(S.notice)}</span>${S.undoId ? '<button type="button" id="ftb-undo">Undo</button>' : ''}</div>`
            : '';
        if (!S.files.length) {
            list.innerHTML = notice + `<div class="ftb-empty">${ICONS.folder}
                <div style="font-weight:500;margin-bottom:4px">${S.q ? 'No files match this search' : deleted ? 'Nothing was deleted recently' : 'No files yet'}</div>
                <div style="font-size:12px">${S.q ? 'Search reads names, conversation titles, questions and agents.'
                    : deleted ? 'Deleted files stay here for the retention period, then their content and keys are removed.'
                    : 'Keep a table, chart or answer from a conversation, or attach a file to a question.'}</div></div>`;
            _wireNotice(list);
            return;
        }
        list.innerHTML = notice + S.files.map(f => {
            const meta = [f.type_label, f.size_label, deleted ? _purgeIn(f.purge_after) : _when(f.created_at), f.session_title].filter(Boolean).join(' · ');
            const badge = deleted ? '' : `<span class="ftb-badge" title="${f.lifetime === 'kept' ? 'Kept — survives its conversation' : 'Goes with its conversation unless kept'}">${f.lifetime === 'kept' ? 'Kept' : 'Conversation'}</span>`;
            const acts = deleted
                ? `<button type="button" class="ftb-ibtn" data-act="restore" title="Restore" aria-label="Restore ${_esc(f.filename)}">${ICONS.restore}</button>`
                : `<button type="button" class="ftb-ibtn" data-act="open" title="Open in Files panel" aria-label="Open ${_esc(f.filename)} in the Files panel">${ICONS.open}</button>
                   <button type="button" class="ftb-ibtn" data-act="download" title="Download" aria-label="Download ${_esc(f.filename)}">${ICONS.download}</button>
                   <button type="button" class="ftb-ibtn danger" data-act="delete" title="Delete" aria-label="Delete ${_esc(f.filename)}">${ICONS.trash}</button>`;
            return `<div class="ftb-row${f.id === S.sel ? ' active' : ''}" data-id="${_esc(f.id)}" tabindex="0" role="button" aria-label="${_esc(f.filename)}">
                <div class="ftb-ic">${_glyph(f)}</div>
                <div class="ftb-body"><div class="ftb-name">${_esc(f.filename)}</div><div class="ftb-meta">${_esc(meta)}</div></div>
                ${badge}<div class="ftb-row-acts">${acts}</div></div>`;
        }).join('');
        _wireNotice(list);
        list.querySelectorAll('.ftb-row').forEach(row => {
            const id = row.dataset.id;
            row.addEventListener('click', (e) => { if (!e.target.closest('.ftb-ibtn')) _select(id); });
            row.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === row) { e.preventDefault(); _select(id); } });
            row.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', (e) => {
                e.stopPropagation();
                _act(b.dataset.act, S.files.find(f => f.id === id));
            }));
        });
    }

    function _wireNotice(list) {
        list.querySelector('#ftb-undo')?.addEventListener('click', _undo);
    }

    function _select(id) {
        S.sel = id;
        _root.querySelectorAll('.ftb-row').forEach(r => r.classList.toggle('active', r.dataset.id === id));
        _loadDetail(id);
    }

    function _renderDetail(f) {
        const panel = _root.querySelector('#ftb-detail');
        if (!panel || !f) return;
        panel.classList.remove('ftb-hidden');
        const deleted = !!f.deleted_at;
        const prov = f.provenance || {};
        const store = f.store || {};
        const title = prov.session_title || f.session_title;   // its name now, else the name it had
        const from = title
            ? `${_esc(title)}${prov.session_exists === false ? ' <span style="color:var(--text-muted)">(conversation deleted)</span>' : ''}`
            : '<span style="color:var(--text-muted)">—</span>';
        const uses = (prov.uses || []);
        const facts = [
            ['Type', `${_esc(f.type_label)} · ${_esc(f.size_label)}`],
            ['Lifetime', deleted ? `Deleted ${_esc(_when(f.deleted_at))} · ${_esc(_purgeIn(f.purge_after))}`
                : f.lifetime === 'kept' ? `Kept${f.kept_at ? ' ' + _esc(_when(f.kept_at)) : ''}` : 'Goes with its conversation unless kept'],
            ['Origin', _esc(_origin(f))],
            ['Conversation', from],
            ['Question', f.question ? _esc(f.question) : '<span style="color:var(--text-muted)">—</span>'],
            ['Agent', _agentHtml(f)],
            ['Store', `${_esc(store.name || '—')}${f.encrypted ? ' · encrypted' : ''}`],
            ['Created', _esc(_when(f.created_at))],
        ].map(([k, v]) => `<tr><th scope="row">${k}</th><td>${v}</td></tr>`).join('');
        const usesHtml = uses.length
            ? `<ul class="ftb-uses">${uses.map(u => `<li>${_esc(u.session_title || 'A deleted conversation')}${u.turn_id != null ? ` <span>· turn ${_esc(u.turn_id)}</span>` : ''}${u.used_at ? ` <span>· ${_esc(_when(u.used_at))}</span>` : ''}</li>`).join('')}</ul>`
            : '<p style="font-size:12px;color:var(--text-muted);margin:0 0 12px">Not used by any turn yet.</p>';

        const acts = deleted
            ? `<button type="button" class="card-btn card-btn--primary card-btn--sm" data-d="restore">${ICONS.restore} Restore</button>`
            : `<button type="button" class="card-btn card-btn--primary card-btn--sm" data-d="open">${ICONS.open} Open in Files panel</button>
               <button type="button" class="card-btn card-btn--neutral card-btn--sm" data-d="download">${ICONS.download} Download</button>
               ${f.lifetime !== 'kept' ? `<button type="button" class="card-btn card-btn--neutral card-btn--sm" data-d="keep" title="Keep it after its conversation is deleted">${ICONS.keep} Keep</button>` : ''}`;

        panel.innerHTML = `
            <div class="ftb-d-title">${_esc(f.filename)}</div>
            <div class="ftb-d-sub">${_esc(f.type_label)} · ${_esc(f.size_label)}</div>
            <div class="ftb-acts">${acts}</div>
            ${deleted ? '' : `
            <label class="ftb-label" for="ftb-name">Name</label>
            <div style="display:flex;gap:8px;margin-bottom:14px">
                <input id="ftb-name" class="ftb-input" type="text" maxlength="200" value="${_esc(f.filename)}" aria-label="File name">
                <button type="button" class="card-btn card-btn--neutral card-btn--sm" id="ftb-rename" style="flex-shrink:0">${ICONS.edit} Rename</button>
            </div>`}
            <table class="ftb-facts"><tbody>${facts}</tbody></table>
            <span class="ftb-label">Used in</span>
            ${usesHtml}
            ${deleted ? '' : `<hr class="ftb-divider">
            <button type="button" class="card-btn card-btn--danger card-btn--sm" data-d="delete">${ICONS.trash} Delete file</button>`}`;

        panel.querySelectorAll('[data-d]').forEach(b => b.addEventListener('click', () => _act(b.dataset.d, f)));
        const input = panel.querySelector('#ftb-name');
        panel.querySelector('#ftb-rename')?.addEventListener('click', () => _rename(f, input.value));
        input?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); _rename(f, input.value); } });
    }

    // ══════════════════════════════════════════════════════════════════════════
    // Actions
    // ══════════════════════════════════════════════════════════════════════════

    function _act(action, f) {
        if (!f) return;
        if (action === 'open') return _openInPanel(f.id);
        if (action === 'download') return _download(f);
        if (action === 'delete') return _delete(f);
        if (action === 'restore') return _restore(f.id);
        if (action === 'keep') return _keep(f);
    }

    async function _openInPanel(id) {
        const convView = document.getElementById('conversation-view');
        if (!convView?.classList.contains('active')) document.getElementById('view-switch-conversation')?.click();
        try {
            const m = await import('/api/v1/components/files/renderer');
            await m.openFilesPanel(id ? { focus: id } : {});
        } catch (err) {
            console.error('[Files tab] the Files panel could not be opened', err);
            _say('The Files panel could not be opened.', 'error');
        }
    }

    async function _download(f) {
        // the one governed exit: the server applies the tier's download policy and records it
        const r = await fetch(`${BASE}/files/${encodeURIComponent(f.id)}/download`, { headers: AUTH() ? { Authorization: `Bearer ${AUTH()}` } : {} });
        if (!r.ok) {
            let m = 'The download did not start.';
            try { m = (await r.json()).message || m; } catch (_) { /* keep default */ }
            _say(m, 'error');
            return;
        }
        const cd = r.headers.get('Content-Disposition') || '';
        const mt = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(cd);
        const name = mt ? decodeURIComponent(mt[1] || mt[2]) : f.filename;
        const a = document.createElement('a');
        a.href = URL.createObjectURL(await r.blob());
        a.download = name;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 250);
    }

    async function _rename(f, value) {
        const v = (value || '').trim();
        if (!v || v === f.filename) return;
        const r = await _api(`/files/${encodeURIComponent(f.id)}`, { method: 'PATCH', body: JSON.stringify({ filename: v }) });
        if (!r.ok) { _say(r.body.message || 'The file could not be renamed.', 'error'); return; }
        delete S.detail[f.id];
        _load();
    }

    async function _keep(f) {
        const r = await _api(`/files/${encodeURIComponent(f.id)}/keep`, { method: 'POST' });
        if (!r.ok) { _say(r.body.message || 'The file could not be kept.', 'error'); return; }
        delete S.detail[f.id];
        _load();
    }

    function _delete(f) {
        const go = async () => {
            const r = await _api(`/files/${encodeURIComponent(f.id)}`, { method: 'DELETE' });
            if (!r.ok) { _say(r.body.message || 'The file could not be deleted.', 'error'); return; }
            S.notice = r.body.purged ? `${f.filename} was deleted.` : `${f.filename} moved to Recently deleted.`;
            S.undoId = r.body.purged ? null : f.id;
            if (S.sel === f.id) S.sel = null;
            delete S.detail[f.id];
            _load();
        };
        // an attachment of a live conversation is still in use; say so before it goes
        if (f.lifetime !== 'kept' && f.source_kind === 'upload') {
            _confirm(`Delete ${f.filename}? It is attached to "${f.session_title || 'a conversation'}" and will no longer be readable there.`, go);
        } else {
            go();
        }
    }

    async function _restore(id) {
        const r = await _api(`/files/${encodeURIComponent(id)}/restore`, { method: 'POST' });
        if (!r.ok) { _say(r.body.message || 'The file could not be restored.', 'error'); return; }
        delete S.detail[id];
        S.notice = 'Restored.';
        S.undoId = null;
        _load();
    }

    async function _undo() {
        const id = S.undoId;
        if (!id) return;
        S.undoId = null;
        await _restore(id);
    }

    // ── Public API ─────────────────────────────────────────────────────────────

    window.filesTabHandler = { initFilesTab };
})();
