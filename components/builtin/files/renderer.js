/**
 * Files — the split panel (PROP-010 §4.7).
 *
 * The process it serves: an answer produces something worth keeping → keep it in one click →
 * find it again from any conversation → see where it came from → reuse it in a turn, or take
 * it out by download. The Workspace section is the launcher; this panel is the browser.
 *
 *   find      one search box (names · conversations · questions · agents), kind chips with
 *             live counts, agent filter, sort, group by date / conversation / agent, list or grid
 *   preview   served by GET /v1/files/<id>/preview — a bounded, safe representation, never the
 *             raw file inline (Markdown is rendered with its raw HTML escaped; HTML is source)
 *   trace     the provenance card (conversation → turn → agent → model → evidence → store),
 *             readable after the conversation is deleted; "Used in" lists the turns that read it
 *   act       Download (THE governed exit, with its formats and "with evidence"), Ask about this
 *             file, Open in Canvas, Keep (an attachment), Rename, Delete → Recently deleted (Undo)
 *   many      checkbox / Shift-click / keyboard selection → zip · attach · delete
 *
 * Conventions: the shared .sp-* chrome (header lives in templates/index.html), --sp-accent from
 * the manifest, theme variables only, monochrome SVG icons, the platform tooltip (data-tip),
 * window.showConfirmBanner / showAppBanner (never a native dialog), live through
 * `UderiaBus.onComponent('files')` — the `component_changed` notice the server announces at the file service's one write path.
 */

const IFOC = {
    tool_enabled: { label: 'Optimize', color: '#9333ea' },
    llm_only: { label: 'Ideate', color: '#4ade80' },
    rag_focused: { label: 'Focus', color: '#3b82f6' },
    genie: { label: 'Coordinate', color: '#F15F22' },
};

const GLYPH = {
    table: 'M4 5h16v14H4zM4 10h16M4 15h16M10 5v14',
    doc: 'M7 3h7l4 4v14H7zM14 3v4h4M9.5 12h6M9.5 15.5h6',
    image: 'M4 5h16v14H4zM4 16l5-5 4 4 2-2 5 5M15.5 9h.01',
    code: 'M9 8l-4 4 4 4M15 8l4 4-4 4',
    file: 'M7 3h7l4 4v14H7zM14 3v4h4',
};

const ICON = {
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14zM20 20l-4-4"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11m0 0l-4-4m4 4l4-4M5 19h14"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
    grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/></svg>',
    lock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3',
    data: 'M12 4c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
    pin: 'M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z',
    folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 12h6M9 15h4',
};

const KINDS = [
    ['all', 'All'], ['table', 'Tables'], ['document', 'Documents'], ['image', 'Images'],
    ['export', 'Exports'], ['upload', 'Uploads'], ['deleted', 'Recently deleted'],
];
const GROUPS = [['date', 'Date'], ['conversation', 'Conversation'], ['agent', 'Agent']];
const UI_KEY = 'tda_files_ui';

// ── state ────────────────────────────────────────────────────────────────────
const S = {
    files: [], facets: {}, storage: null, total: 0, fileCount: 0, available: true,
    q: '', kind: 'all', group: 'date', view: 'list', sort: 'newest', agent: '',
    sel: null, checked: new Set(), anchor: null,
    detail: {}, preview: {}, menu: false, renaming: false,
    notice: null, undoId: null, loading: false, loaded: false, error: null,
    kgPick: null, kgRepos: null,
    fullscreen: false, focusAfterLoad: null, narrow: false, detailOpen: false,
};
try {
    const saved = JSON.parse(localStorage.getItem(UI_KEY) || '{}');
    ['kind', 'group', 'view', 'sort'].forEach(k => { if (saved[k]) S[k] = saved[k]; });
} catch (_) { /* per-viewer convenience only */ }

function _saveUi() {
    try { localStorage.setItem(UI_KEY, JSON.stringify({ kind: S.kind, group: S.group, view: S.view, sort: S.sort })); } catch (_) { /* ignore */ }
}

// ── helpers ──────────────────────────────────────────────────────────────────
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const _tok = () => localStorage.getItem('tda_auth_token');
const _hdrs = (json = true) => ({ ...(json ? { 'Content-Type': 'application/json' } : {}), ...(_tok() ? { Authorization: `Bearer ${_tok()}` } : {}) });

async function _api(path, opts = {}) {
    const r = await fetch(`/api/v1${path}`, { ...opts, headers: { ..._hdrs(!(opts.body instanceof FormData)), ...(opts.headers || {}) } });
    let body = null;
    try { body = await r.json(); } catch (_) { body = null; }
    return { ok: r.ok, status: r.status, body: body || {} };
}

function _svg(path, size = 16, sw = 1.8) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
}

function _glyphOf(f) {
    if (f.category === 'image') return GLYPH.image;
    if (f.category === 'table' || ['csv', 'tsv', 'xlsx'].includes(f.ext)) return GLYPH.table;
    if (['md', 'markdown', 'txt', 'pdf', 'docx'].includes(f.ext)) return GLYPH.doc;
    if (['sql', 'json', 'py', 'js', 'ttl', 'nt', 'rdf', 'jsonld', 'html', 'xml', 'yaml', 'yml'].includes(f.ext)) return GLYPH.code;
    return GLYPH.file;
}

function _agentOf(f) {
    const i = IFOC[f.profile_type] || null;
    return { label: i ? i.label : (f.profile_tag ? `@${f.profile_tag}` : 'No agent'), color: i ? i.color : 'var(--text-muted)', tag: f.profile_tag };
}

// The agent's IFOC word in the text colour, its IFOC colour as a dot — the word stays readable in
// both themes (a green "Ideate" on white is not), the colour still says which class answered.
function _agentHtml(a, strong) {
    return `<span style="display:inline-flex;align-items:center;gap:4px;color:var(--text-secondary)${strong ? ';font-weight:500;color:var(--text-primary)' : ''}"><span aria-hidden="true" style="width:7px;height:7px;border-radius:999px;background:${a.color};display:inline-block"></span>${esc(a.label)}</span>`;
}

// The agent itself: the platform's agent pill (window.renderProfileTag — the same element as the
// conversation header's @TAG, coloured by its IFOC class) with the class word beside it, so the
// class is said in words too. No tag (an agent never named, or none): the dot and the word.
function _agentPill(a, f, strong) {
    if (!a.tag || typeof window.renderProfileTag !== 'function') return _agentHtml(a, strong);
    const word = IFOC[f.profile_type] ? ` <span style="color:var(--text-muted)">${esc(IFOC[f.profile_type].label)}</span>` : '';
    return `<span style="display:inline-flex;align-items:center;gap:4px">${window.renderProfileTag(esc(a.tag), f.profile_type || undefined, 'fl-agent-pill')}${word}</span>`;
}

function _when(iso) {
    if (!iso) return '';
    const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime();
    const d = (Date.now() - t) / 1000;
    if (d < 60) return 'just now';
    if (d < 3600) return `${Math.floor(d / 60)} min ago`;
    if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
    if (d < 172800) return 'yesterday';
    return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function _purgeIn(iso) {
    if (!iso) return 'kept until an admin purges it';
    const t = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
    const days = Math.ceil((t - Date.now()) / 86400000);
    return days <= 0 ? 'removed shortly' : days === 1 ? 'removed tomorrow' : `removed in ${days} days`;
}

function _bucket(iso) {
    const t = new Date(iso && (iso.endsWith('Z') || iso.includes('+')) ? iso : (iso || '') + 'Z');
    const now = new Date();
    const day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (t.getTime() >= day0) return 'Today';
    if (t.getTime() >= day0 - 86400000) return 'Yesterday';
    if (t.getTime() >= day0 - 6 * 86400000) return 'This week';
    return 'Earlier';
}

function _notify(msg, type = 'info') {
    if (typeof window.showAppBanner === 'function') window.showAppBanner(msg, type);
}

function _confirm(msg, onYes) {
    if (typeof window.showConfirmBanner === 'function') window.showConfirmBanner(msg, onYes);
    else onYes();   // never a native dialog; the banner system is always present in the app
}

function _visible() {
    return S.files;
}

function _selected() {
    return S.files.find(f => f.id === S.sel) || null;
}

// ── CSS ──────────────────────────────────────────────────────────────────────
function _css() {
    if (document.getElementById('files-css-v1')) return;
    const s = document.createElement('style');
    s.id = 'files-css-v1';
    s.textContent = `
#files-split-panel { width:0; min-width:0; max-width:980px; flex-shrink:0; display:flex; flex-direction:column; height:100%;
  overflow:hidden; background:var(--card-bg,rgba(20,22,25,0.6)); border-left:1px solid var(--border-primary,rgba(255,255,255,0.1));
  transition:width .3s ease, min-width .3s ease; }
#files-split-panel.files-split--open { width:56%; min-width:420px; }
.files-fullscreen #files-split-panel.files-split--open { position:fixed !important; top:var(--files-fullscreen-top,0px); left:0 !important;
  right:0 !important; bottom:0 !important; width:auto !important; max-width:none; z-index:50; }
@media (prefers-reduced-motion: reduce) { #files-split-panel { transition:none; } .fl-row, .fl-tile { transition:none !important; } }
.files-split-body { flex:1; display:flex; flex-direction:column; min-height:0; overflow:hidden; background:var(--bg-secondary,var(--bg-tertiary)); }
.files-split-body.fl-drop { box-shadow:inset 0 0 0 2px var(--sp-accent); }
.fl-bar { display:flex; flex-direction:column; gap:8px; padding:10px 12px; border-bottom:1px solid var(--border-primary); }
.fl-bar-row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.fl-search { flex:1 1 220px; display:flex; align-items:center; gap:8px; height:32px; padding:0 10px; border:1px solid var(--border-primary);
  border-radius:8px; background:var(--input-bg, var(--bg-primary)); color:var(--text-muted); }
.fl-search svg { width:14px; height:14px; flex:0 0 auto; }
.fl-search input { flex:1; min-width:0; border:0; background:transparent; color:var(--text-primary); font:inherit; font-size:13px; outline:none; }
.fl-kbd { font-size:11px; color:var(--text-muted); border:1px solid var(--border-primary); border-radius:4px; padding:0 5px; }
.fl-lbl { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--text-muted); }
.fl-lbl select { height:32px; border:1px solid var(--border-primary); border-radius:8px; background:var(--input-bg, var(--bg-primary)); color:var(--text-primary); padding:0 8px; font:inherit; font-size:12px; }
/* Industrial controls (main.css): filters and switches are .ind-tab--pill, buttons are .card-btn.
   These rules only size them for the panel and join the split Download button. */
.fl-pills { display:flex; gap:4px; flex-wrap:wrap; align-items:center; }
.fl-pills .ind-tab { padding:.3rem .65rem; font-size:.75rem; gap:.35rem; }
.fl-pills .ind-tab svg { width:14px; height:14px; }
.fl-pills .ind-tab .fl-n { color:var(--text-muted); font-variant-numeric:tabular-nums; font-weight:400; }
.fl-pills .ind-tab.active .fl-n { color:inherit; opacity:.8; }
.fl-grp-lbl { font-size:12px; color:var(--text-muted); margin-left:6px; }
.fl-spacer { flex:1 1 auto; }
.fl-main { flex:1; display:flex; min-height:0; }
.fl-list { flex:1 1 52%; min-width:0; overflow:auto; padding:4px 6px 12px; border-right:1px solid var(--border-primary); }
.fl-detail { flex:1 1 48%; min-width:0; overflow:auto; }
.fl-grp { display:flex; align-items:baseline; gap:8px; padding:14px 8px 6px; }
.fl-grp-t { font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--text-muted); }
.fl-grp-t > span { text-transform:none; letter-spacing:normal; font-weight:500; }   /* an agent pill heads its group as itself */
.fl-grp-n, .fl-grp-s { font-size:11px; color:var(--text-muted); }
.fl-grp a { margin-left:auto; font-size:12px; color:var(--text-secondary); text-decoration:underline; text-underline-offset:2px; text-decoration-color:var(--border-primary); cursor:pointer; }
.fl-row { display:flex; align-items:center; gap:4px; border-radius:8px; margin:1px 0; transition:background .12s ease; }
.fl-row:hover { background:var(--hover-bg); }
.fl-row.is-sel { background:color-mix(in srgb, var(--sp-accent) 14%, transparent); box-shadow:inset 0 0 0 1px var(--sp-accent); }
.fl-row input[type=checkbox] { width:15px; height:15px; margin:0 2px 0 6px; accent-color:var(--sp-accent); flex:0 0 auto; }
.fl-row-main { flex:1; min-width:0; display:flex; align-items:center; gap:10px; border:0; background:transparent; padding:7px 6px; text-align:left; cursor:pointer; color:inherit; font:inherit; }
.fl-ic { flex:0 0 auto; width:32px; height:32px; border-radius:8px; background:var(--bg-primary); border:1px solid var(--border-secondary);
  display:inline-flex; align-items:center; justify-content:center; color:var(--text-secondary); }
.fl-tx { flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
.fl-nm-row { display:flex; align-items:center; gap:6px; min-width:0; }
.fl-nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500; color:var(--text-primary); font-size:13px; }
.fl-meta { font-size:12px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fl-new { flex:0 0 auto; font-size:10px; font-weight:600; padding:1px 6px; border-radius:999px; background:var(--teradata-orange,#F15F22); color:#fff; }
.fl-badge { flex:0 0 auto; font-size:11px; color:var(--text-muted); border:1px solid var(--border-primary); border-radius:999px; padding:0 6px; }
.fl-note { flex:0 0 auto; font-size:11px; color:var(--text-muted); }
.fl-ibtn { flex:0 0 auto; width:28px; height:28px; margin-right:4px; border:0; border-radius:6px; background:transparent; color:var(--text-muted);
  display:inline-flex; align-items:center; justify-content:center; cursor:pointer; }
.fl-ibtn:hover { background:var(--hover-bg-strong); color:var(--text-primary); }
.fl-ibtn svg { width:15px; height:15px; }
.fl-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); gap:8px; padding:0 6px; }
.fl-tile { display:flex; flex-direction:column; gap:6px; padding:8px; border-radius:8px; border:1px solid var(--border-secondary); background:transparent;
  cursor:pointer; text-align:left; color:inherit; font:inherit; min-width:0; transition:background .12s ease; }
.fl-tile:hover { background:var(--hover-bg); }
.fl-tile.is-sel { border-color:var(--sp-accent); background:color-mix(in srgb, var(--sp-accent) 12%, transparent); }
.fl-thumb { height:88px; border-radius:6px; background:var(--bg-primary); border:1px solid var(--border-secondary); display:flex; align-items:center;
  justify-content:center; overflow:hidden; color:var(--text-secondary); }
.fl-thumb img { max-width:100%; max-height:100%; object-fit:contain; }
.fl-notice { display:flex; align-items:center; gap:10px; margin:10px 8px 0; padding:8px 10px; border-radius:8px;
  background:color-mix(in srgb, var(--sp-accent) 14%, transparent); font-size:12px; color:var(--text-primary); }
.fl-notice button { border:0; background:transparent; padding:0; font-weight:600; cursor:pointer; color:inherit; text-decoration:underline; }
.fl-bulk { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:10px 8px 0; padding:6px 8px; border:1px solid var(--border-primary);
  border-radius:8px; background:var(--bg-primary); font-size:12px; }
.fl-acts .card-btn svg, .fl-bulk .card-btn svg, .fl-empty .card-btn svg { width:14px; height:14px; }
.fl-split .card-btn:first-child { border-top-right-radius:0; border-bottom-right-radius:0; }
.fl-split .card-btn + .card-btn { border-top-left-radius:0; border-bottom-left-radius:0; border-left-width:0; padding-left:.45rem; padding-right:.45rem; }
.fl-split .card-btn .fl-fmt { font-weight:400; opacity:.85; }
.fl-menu { position:absolute; top:36px; left:0; z-index:6; min-width:250px; background:var(--bg-overlay,var(--card-bg)); border:1px solid var(--border-primary);
  border-radius:8px; padding:4px; box-shadow:0 10px 30px rgba(0,0,0,.25); }
.fl-menu button { width:100%; display:flex; flex-direction:column; gap:1px; text-align:left; border:0; background:transparent; border-radius:6px; padding:7px 10px;
  cursor:pointer; color:var(--text-primary); font:inherit; font-size:13px; }
.fl-menu button:hover, .fl-menu button:focus-visible { background:var(--hover-bg); }
.fl-menu small { font-size:11px; color:var(--text-muted); }
.fl-d-head { padding:14px 16px 10px; display:flex; flex-direction:column; gap:4px; border-bottom:1px solid var(--border-secondary); }
.fl-d-title { display:flex; align-items:center; gap:10px; min-width:0; }
.fl-d-title h3 { margin:0; font-family:var(--font-display,'Space Grotesk',sans-serif); font-size:15px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-primary); }
.fl-d-title input { flex:1; min-width:0; height:30px; border:1px solid var(--sp-accent); border-radius:6px; background:var(--input-bg,var(--bg-primary)); color:var(--text-primary); padding:0 8px; font:inherit; }
.fl-d-meta { font-size:12px; color:var(--text-muted); }
.fl-pv { margin:12px 16px; border:1px solid var(--border-secondary); border-radius:8px; background:var(--bg-primary); overflow:hidden; }
.fl-pv-scroll { max-height:300px; overflow:auto; }
.fl-pv table { width:100%; border-collapse:collapse; font-size:12px; font-variant-numeric:tabular-nums; }
.fl-pv th { position:sticky; top:0; background:var(--bg-primary); text-align:left; padding:7px 10px; color:var(--text-muted); font-weight:500; border-bottom:1px solid var(--border-secondary); white-space:nowrap; }
.fl-pv td { padding:6px 10px; border-bottom:1px solid var(--border-secondary); white-space:nowrap; color:var(--text-primary); }
.fl-pv-note { padding:6px 10px; font-size:11px; color:var(--text-muted); }
.fl-pv-md { padding:12px 16px; line-height:1.55; font-size:13px; color:var(--text-primary); }
.fl-pv-md h1, .fl-pv-md h2, .fl-pv-md h3 { font-size:14px; margin:.6em 0 .3em; }
.fl-pv-code { margin:0; padding:10px 0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; line-height:1.6; color:var(--text-primary); }
.fl-pv-code div { display:flex; gap:12px; padding:0 12px; white-space:pre; }
.fl-pv-code span:first-child { color:var(--text-muted); min-width:22px; text-align:right; user-select:none; }
.fl-pv-img { display:flex; justify-content:center; padding:12px; }
.fl-pv-img img { max-width:100%; max-height:280px; object-fit:contain; }
.fl-pv-empty { padding:22px 16px; text-align:center; color:var(--text-muted); font-size:12px; }
.fl-acts { display:flex; flex-wrap:wrap; gap:8px; align-items:center; padding:0 16px 12px; }
.fl-split { position:relative; display:inline-flex; }
.fl-msg { margin:0 16px 12px; padding:9px 12px; border-radius:8px; background:var(--hover-bg); color:var(--text-secondary); font-size:12px; line-height:1.5; }
.fl-msg.warn { background:color-mix(in srgb, var(--status-warning,#d97706) 12%, transparent); color:var(--text-primary); }
.fl-card { margin:0 16px 12px; padding:12px 14px; border:1px solid var(--border-secondary); border-radius:8px; display:flex; flex-direction:column; gap:10px; }
.fl-card-t { font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--text-muted); }
.fl-steps { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; }
.fl-steps li { display:grid; grid-template-columns:14px 96px minmax(0,1fr); column-gap:10px; align-items:start; padding:4px 0; font-size:12.5px; }
.fl-steps .dot { width:7px; height:7px; margin-top:5px; border-radius:999px; border:1.5px solid var(--sp-accent); justify-self:center; }
.fl-steps .k { color:var(--text-muted); font-size:12px; }
.fl-steps .v { color:var(--text-primary); min-width:0; overflow-wrap:anywhere; }
.fl-steps .v.muted { color:var(--text-muted); font-style:italic; }
.fl-steps a, .fl-uses a { color:var(--text-primary); text-decoration:underline; text-underline-offset:2px; text-decoration-color:var(--border-primary); cursor:pointer; }
.fl-tags { display:flex; flex-wrap:wrap; gap:6px; }
.fl-tag { display:inline-flex; align-items:center; gap:5px; font-size:11px; padding:2px 8px; border:1px solid var(--border-primary); border-radius:999px; color:var(--text-secondary); }
.fl-tag svg { width:11px; height:11px; }
.fl-uses { display:flex; flex-direction:column; gap:5px; font-size:12.5px; }
.fl-ev { margin-top:2px; }
.fl-foot { display:flex; align-items:center; gap:12px; padding:7px 12px; border-top:1px solid var(--border-primary); color:var(--text-muted); font-size:12px; flex-wrap:wrap; }
.fl-meter { flex:0 1 160px; height:4px; border-radius:999px; background:var(--hover-bg-strong); overflow:hidden; }
.fl-meter span { display:block; height:100%; background:var(--sp-accent); }
.fl-meter.warn span { background:var(--status-warning,#d97706); }
.fl-empty { padding:40px 22px; display:flex; flex-direction:column; align-items:center; text-align:center; gap:10px; color:var(--text-secondary); }
.fl-empty .hero { width:56px; height:56px; border-radius:14px; background:color-mix(in srgb, var(--sp-accent) 16%, transparent); display:flex; align-items:center; justify-content:center; color:var(--sp-accent); }
.fl-empty h3 { margin:0; font-family:var(--font-display,'Space Grotesk',sans-serif); font-size:17px; color:var(--text-primary); }
.fl-empty ol { margin:6px 0 0; padding:0; list-style:none; text-align:left; display:flex; flex-direction:column; gap:7px; font-size:13px; }
.fl-empty ol span { font-family:var(--font-display,'Space Grotesk',sans-serif); color:var(--sp-accent); font-weight:600; margin-right:8px; }
.fl-sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
.fl-narrow .fl-list { flex:1 1 100%; border-right:0; }
.fl-narrow .fl-detail { display:none; flex:1 1 100%; }
.fl-narrow.fl-detail-open .fl-list { display:none; }
.fl-narrow.fl-detail-open .fl-detail { display:block; }
.fl-back { display:none; margin:10px 16px 0; padding:0; border:0; background:none; color:var(--text-secondary); font-size:12px; cursor:pointer;
  text-decoration:underline; text-underline-offset:2px; text-decoration-color:var(--border-primary); }
.fl-narrow .fl-back { display:inline-block; }
`;
    document.head.appendChild(s);
}

// ── open / close ─────────────────────────────────────────────────────────────
let _changeOff = null;

export async function openFilesPanel(opts = {}) {
    _css();
    const panel = document.getElementById('files-split-panel');
    if (!panel) return;
    if (opts.kind) S.kind = opts.kind;
    if (opts.q != null) S.q = opts.q;
    if (opts.focus) { S.sel = opts.focus; S.focusAfterLoad = opts.focus; S.focusOpened = true; if (opts.kind == null && S.kind !== 'all') S.kind = 'all'; }
    // the agent filter: a file opened by id is shown whatever agent produced it; otherwise the list
    // starts on the agent the next turn runs under, unless the user picked a filter this opening
    if (opts.focus) S.agent = '';
    else if (!_agentPinned) S.agent = _effectiveProfileId() || '';
    panel.style.display = 'flex';
    panel.offsetHeight;   // reflow so the width transition runs
    panel.classList.add('files-split--open');
    _persistOpenState();
    document.getElementById('files-split-close').onclick = _manualClose;
    const fs = document.getElementById('files-split-fullscreen');
    if (fs) fs.onclick = _toggleFullscreen;
    const add = document.getElementById('files-split-add');
    if (add) add.onclick = _pickLocalFiles;
    _shell();
    if (!_changeHandler) {
        const onChange = (e) => {
            const p = document.getElementById('files-split-panel');
            if (!p || !p.classList.contains('files-split--open')) return;
            const d = (e && e.detail) || {};
            if (d.action === 'used') {
                // a turn read these files: only their provenance changed ("Used in") — the list,
                // the previews and the content stay; refetch the detail of the open one
                (d.ids || []).forEach(id => { delete S.detail[id]; });
                if (S.sel && (d.ids || []).includes(S.sel)) _loadDetail(S.sel);
                return;
            }
            S.detail = {}; S.preview = {};
            _load();
        };
        _changeOff = UderiaBus.onComponent('files', onChange);
    }
    _watchWidth(panel);
    await _load();
}

// Below 760px the list fills the panel and a file opens in its place (a back link returns);
// wider, list and detail sit side by side.
let _ro = null;
function _watchWidth(panel) {
    if (_ro || typeof ResizeObserver === 'undefined') return;
    _ro = new ResizeObserver(() => {
        const narrow = panel.getBoundingClientRect().width < 760;
        if (narrow !== S.narrow) { S.narrow = narrow; panel.classList.toggle('fl-narrow', narrow); _renderDetail(); }
    });
    _ro.observe(panel);
}

// ── this session remembers the panel (the KG / canvas / dashboard pattern) ────
// A `files_state_<sessionId>` marker means the panel was OPEN in that session, with the file
// that was selected: written on open and on every selection, removed only by the user's own
// close. A session switch closes the panel WITHOUT removing it, so returning to the session —
// or reloading the page — opens Files again on the same file (sessionManagement._restoreFilesState).
function _persistOpenState() {
    const sid = window.__currentSessionId;
    if (!sid) return;
    try { localStorage.setItem(`files_state_${sid}`, JSON.stringify({ open: true, focus: S.sel || null })); } catch (_) { /* quota */ }
}

function _manualClose() {
    const sid = window.__currentSessionId;
    if (sid) { try { localStorage.removeItem(`files_state_${sid}`); } catch (_) { /* no-op */ } }
    closeFilesPanel();
}

export function closeFilesPanel() {
    const panel = document.getElementById('files-split-panel');
    if (!panel) return;
    if (S.fullscreen) _toggleFullscreen();
    if (_changeOff) { _changeOff(); _changeOff = null; }
    _agentPinned = false;   // a closed panel forgets the user's pick; reopening follows the agent again
    panel.classList.remove('files-split--open');
    panel.style.width = ''; panel.style.minWidth = ''; panel.style.maxWidth = ''; panel.style.flex = '';
    let hidden = false;
    const finish = () => {
        if (hidden) return;
        hidden = true;
        panel.removeEventListener('transitionend', onEnd);
        if (!panel.classList.contains('files-split--open')) {
            panel.style.display = 'none';
            const b = document.getElementById('files-split-content');
            if (b) b.innerHTML = '';
        }
        try { window.SplitPanelResizer?.update(); } catch (_) { /* optional */ }
    };
    const onEnd = () => finish();
    panel.addEventListener('transitionend', onEnd);
    setTimeout(finish, 400);   // transitionend never fires without a transition (reduced motion, fresh session)
}

export function isFilesPanelOpen() {
    return !!document.getElementById('files-split-panel')?.classList.contains('files-split--open');
}

function _toggleFullscreen() {
    const main = document.getElementById('main-content-area');
    if (!main) return;
    S.fullscreen = !S.fullscreen;
    if (S.fullscreen) {
        const nav = document.querySelector('body > nav');
        document.documentElement.style.setProperty('--files-fullscreen-top', (nav ? nav.offsetHeight : 0) + 'px');
        main.classList.add('files-fullscreen');
    } else {
        main.classList.remove('files-fullscreen');
        document.documentElement.style.removeProperty('--files-fullscreen-top');
    }
    const b = document.getElementById('files-split-fullscreen');
    if (b) { b.title = S.fullscreen ? 'Exit fullscreen' : 'Fullscreen'; b.classList.toggle('is-active', S.fullscreen); }
}

// ── data ─────────────────────────────────────────────────────────────────────
let _loadSeq = 0;

async function _load() {
    const seq = ++_loadSeq;
    S.loading = true;
    const p = new URLSearchParams({ kind: S.kind, sort: S.sort });
    if (S.q) p.set('q', S.q);
    if (S.agent) p.set('profile_id', S.agent);
    const r = await _api(`/files?${p.toString()}`);
    if (seq !== _loadSeq) return;     // a newer load superseded this one
    S.loading = false;
    S.loaded = true;
    if (!r.ok) { S.error = r.body.message || 'Files could not be loaded.'; _renderAll(); return; }
    S.error = null;
    S.files = r.body.files || [];
    S.facets = r.body.facets || {};
    S.storage = r.body.storage || null;
    S.total = r.body.total || 0;
    S.fileCount = r.body.file_count || 0;
    S.available = r.body.available !== false;
    S.checked = new Set([...S.checked].filter(id => S.files.some(f => f.id === id)));
    if (S.focusAfterLoad && S.files.some(f => f.id === S.focusAfterLoad)) S.sel = S.focusAfterLoad;
    S.focusAfterLoad = null;
    if (!S.sel || !S.files.some(f => f.id === S.sel)) S.sel = S.files[0]?.id || null;
    if (S.narrow && S.focusOpened) { S.detailOpen = true; document.getElementById('files-split-panel')?.classList.add('fl-detail-open'); }
    S.focusOpened = false;
    _renderAll();
    if (S.sel) _loadDetail(S.sel);
}

async function _loadDetail(id) {
    if (!S.detail[id]) {
        const r = await _api(`/files/${encodeURIComponent(id)}`);
        if (r.ok) S.detail[id] = r.body.file;
    }
    if (!S.preview[id]) {
        const r = await _api(`/files/${encodeURIComponent(id)}/preview`);
        S.preview[id] = r.ok ? r.body.preview : { kind: 'unavailable', message: r.body.message || 'Preview unavailable' };
    }
    if (S.sel === id) _renderDetail();
    if (S.sel === id && isFilesPanelOpen()) _persistOpenState();
}

// ── rendering ────────────────────────────────────────────────────────────────
function _body() { return document.getElementById('files-split-content'); }

function _shell() {
    const b = _body();
    if (!b || b.querySelector('.fl-bar')) return;
    b.innerHTML = `
      <div class="fl-bar">
        <div class="fl-bar-row">
          <label class="fl-search">${ICON.search}<span class="fl-sr">Search files</span>
            <input type="search" id="fl-q" placeholder="Search names, conversations, questions, agents" autocomplete="off">
            <span class="fl-kbd" aria-hidden="true">/</span></label>
          <label class="fl-lbl">Agent <select id="fl-agent"><option value="">All agents</option></select></label>
          <label class="fl-lbl">Sort <select id="fl-sort"><option value="newest">Newest</option><option value="name">Name</option><option value="size">Size</option></select></label>
        </div>
        <div class="fl-bar-row" id="fl-filters"></div>
      </div>
      <div class="fl-main">
        <div class="fl-list" id="fl-list" tabindex="0" role="listbox" aria-label="Files" aria-multiselectable="true"></div>
        <div class="fl-detail" id="fl-detail" aria-label="Selected file"></div>
      </div>
      <div class="fl-foot" id="fl-foot"></div>
      <p class="fl-sr" id="fl-live" aria-live="polite"></p>
      <input type="file" id="fl-add-input" multiple style="display:none">`;
    const q = b.querySelector('#fl-q');
    q.value = S.q;
    let t = null;
    q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { S.q = q.value.trim(); _load(); }, 160); });
    b.querySelector('#fl-sort').value = S.sort;
    b.querySelector('#fl-sort').addEventListener('change', (e) => { S.sort = e.target.value; _saveUi(); _load(); });
    b.querySelector('#fl-agent').addEventListener('change', (e) => {
        if (e.target.value === S.agent) return;          // the panel's own sync, not a pick
        S.agent = e.target.value; _agentPinned = true; _load();
    });
    b.querySelector('#fl-add-input').addEventListener('change', (e) => { _addLocal(e.target.files); e.target.value = ''; });
    b.addEventListener('keydown', _onKey);
    ['dragenter', 'dragover'].forEach(ev => b.addEventListener(ev, (e) => {
        if (e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files')) { e.preventDefault(); b.classList.add('fl-drop'); }
    }));
    ['dragleave', 'drop'].forEach(ev => b.addEventListener(ev, (e) => {
        if (ev === 'dragleave' && b.contains(e.relatedTarget)) return;
        b.classList.remove('fl-drop');
        if (ev === 'drop' && e.dataTransfer?.files?.length) { e.preventDefault(); _addLocal(e.dataTransfer.files); }
    }));
    _fillAgents();
    _syncAgentSelect();
    if (window.enhanceSelects) window.enhanceSelects(b);
}

// ── the agent in view ────────────────────────────────────────────────────────
// The list follows the agent the next turn runs under — the one `uderia:effective-profile`
// signal main.js emits from the resource-panel seam (every default change and @TAG override
// funnels there; the knowledge-graph panel follows the same signal). Rules:
//   closed panel        → nothing (a closed panel costs nothing; opening reads the agent then)
//   user picked a filter → nothing until the panel closes (the select is an explicit act)
//   same agent          → nothing (no reload)
//   otherwise           → the filter becomes that agent and the list reloads
let _agentPinned = false;

function _effectiveProfileId() {
    return (window.state && window.state.currentResourcePanelProfileId) || window.currentResourcePanelProfileId || null;
}

function _agentTag(id) {
    const p = ((window.configState && window.configState.profiles) || []).find(x => (x.id || x.profile_id) === id);
    return p ? (p.tag || p.name || id) : null;
}

function _syncAgentSelect() {
    const sel = _body()?.querySelector('#fl-agent');
    if (!sel) return;
    if (S.agent && ![...sel.options].some(o => o.value === S.agent)) {
        const o = document.createElement('option');   // an agent the list did not carry yet
        o.value = S.agent;
        o.textContent = `@${_agentTag(S.agent) || S.agent}`;
        sel.appendChild(o);
    }
    if (sel.value !== S.agent) sel.value = S.agent;
}

export function followEffectiveProfile(profileId) {
    const p = document.getElementById('files-split-panel');
    if (!p || !p.classList.contains('files-split--open')) return 'closed';
    if (_agentPinned) return 'pinned';
    const next = profileId || '';
    if (next === S.agent) return 'same';
    S.agent = next;
    _syncAgentSelect();
    _load();
    return 'followed';
}

if (typeof window !== 'undefined' && window.UderiaBus) {
    UderiaBus.on('uderia:effective-profile', (ev) => followEffectiveProfile(((ev && ev.detail) || {}).profileId));
}

function _showAllAgents() {
    S.agent = ''; _agentPinned = true;
    _syncAgentSelect();
    _load();
}

function _fillAgents() {
    const sel = _body()?.querySelector('#fl-agent');
    const profiles = (window.configState && window.configState.profiles) || [];
    if (!sel || sel.options.length > 1) return;
    profiles.forEach(p => {
        const o = document.createElement('option');
        o.value = p.id || p.profile_id;
        o.textContent = `@${p.tag || p.name || o.value}`;
        sel.appendChild(o);
    });
}

function _renderAll() {
    _renderChips();
    _renderList();
    _renderDetail();
    _renderFoot();
}

function _renderChips() {
    const host = _body()?.querySelector('#fl-filters');
    if (!host) return;
    const chips = KINDS.map(([k, label]) => {
        const n = S.facets[k] != null ? S.facets[k] : '';
        return `<button type="button" class="ind-tab ind-tab--pill${S.kind === k ? ' active' : ''}" data-kind="${k}" aria-pressed="${S.kind === k}">${esc(label)} <span class="fl-n">${esc(n)}</span></button>`;
    }).join('');
    const groups = GROUPS.map(([g, label]) => `<button type="button" class="ind-tab ind-tab--pill${S.group === g ? ' active' : ''}" data-group="${g}" aria-pressed="${S.group === g}">${label}</button>`).join('');
    host.innerHTML = `<div class="fl-pills" role="group" aria-label="Kind">${chips}</div><span class="fl-spacer"></span>
      <div class="fl-pills" role="group" aria-label="Group files by"><span class="fl-grp-lbl">Group</span>${groups}</div>
      <div class="fl-pills" role="group" aria-label="View">
        <button type="button" data-view="list" aria-pressed="${S.view === 'list'}" aria-label="List view" class="ind-tab ind-tab--pill u-tip${S.view === 'list' ? ' active' : ''}" data-tip="List">${ICON.list}</button>
        <button type="button" data-view="grid" aria-pressed="${S.view === 'grid'}" aria-label="Grid view" class="ind-tab ind-tab--pill u-tip${S.view === 'grid' ? ' active' : ''}" data-tip="Grid">${ICON.grid}</button>
      </div>`;
    host.querySelectorAll('[data-kind]').forEach(b => b.addEventListener('click', () => { S.kind = b.dataset.kind; _saveUi(); _load(); }));
    host.querySelectorAll('[data-group]').forEach(b => b.addEventListener('click', () => { S.group = b.dataset.group; _saveUi(); _renderChips(); _renderList(); }));
    host.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => { S.view = b.dataset.view; _saveUi(); _renderChips(); _renderList(); }));
}

function _groups(files) {
    const order = { date: ['Today', 'Yesterday', 'This week', 'Earlier'] }[S.group];
    const map = new Map();
    for (const f of files) {
        let key, sub = '', link = null, agent = null;
        if (S.group === 'date') key = _bucket(f.created_at);
        // By AGENT — the agent that placed the file (its pill heads the group), as the Agent filter
        // beside it lists agents; not by IFOC class, which once grouped @UDERIA under "Focus".
        else if (S.group === 'agent') { const a = _agentOf(f); key = a.tag ? '@' + a.tag : a.label; agent = { a, f }; }
        else if (f.scheduled_task_id) { key = f.scheduled_task_name || 'Scheduled task'; sub = 'scheduled task'; link = { kind: 'task', id: f.scheduled_task_id }; }
        else if (f.session_id) { key = f.session_title || 'Untitled conversation'; link = { kind: 'session', id: f.session_id }; }
        else key = 'Added by you';
        if (!map.has(key)) map.set(key, { title: key, sub, link, agent, items: [] });
        map.get(key).items.push(f);
    }
    if (S.group === 'agent') {
        // the IFOC order (Optimize · Coordinate · Focus · Ideate), then the tag; no agent last
        const rank = (g) => { const t = g.agent && g.agent.f.profile_type; const i = ['tool_enabled', 'genie', 'rag_focused', 'llm_only'].indexOf(t);
            return (g.agent && g.agent.a.tag ? 0 : 10) + (i < 0 ? 4 : i); };
        return [...map.values()].sort((x, y) => rank(x) - rank(y) || x.title.localeCompare(y.title));
    }
    const keys = order ? order.filter(k => map.has(k)).concat([...map.keys()].filter(k => !order.includes(k))) : [...map.keys()];
    return keys.map(k => map.get(k));
}

function _rowMeta(f) {
    const a = _agentOf(f);
    return `${esc(f.type_label)} · ${esc(f.size_label)} · ${_agentPill(a, f)} · ${esc(f.deleted_at ? 'deleted ' + _when(f.deleted_at) : _when(f.created_at))}`;
}

function _badges(f) {
    let h = '';
    if (f.is_new) h += '<span class="fl-new">New</span>';
    if (f.category === 'upload' && f.lifetime === 'kept') h += '<span class="fl-badge u-tip" data-tip="Kept — it stays when its conversation is deleted">Kept</span>';
    if (f.category === 'upload' && f.lifetime === 'conversation' && !f.deleted_at) h += '<span class="fl-badge u-tip" data-tip="Deleted with its conversation unless you keep it">Conversation</span>';
    return h;
}

function _renderList() {
    const host = _body()?.querySelector('#fl-list');
    if (!host) return;
    const files = _visible();
    let html = '';
    if (S.notice) {
        html += `<div class="fl-notice" role="status"><span style="flex:1">${esc(S.notice)}</span>${S.undoId ? '<button type="button" data-undo>Undo</button>' : ''}</div>`;
    }
    if (S.checked.size) {
        html += `<div class="fl-bulk"><strong>${S.checked.size} selected</strong><span class="fl-spacer"></span>
          <button type="button" class="card-btn card-btn--primary card-btn--sm" data-bulk="zip">${ICON.download}Download (zip)</button>
          <button type="button" class="card-btn card-btn--neutral card-btn--sm" data-bulk="attach">Attach</button>
          <button type="button" class="card-btn card-btn--danger card-btn--sm" data-bulk="delete">Delete</button>
          <button type="button" class="card-btn card-btn--neutral card-btn--sm" data-bulk="clear">Clear</button></div>`;
    }
    if (S.error) {
        html += `<div class="fl-empty"><p>${esc(S.error)}</p><button type="button" class="card-btn card-btn--neutral" data-retry>Try again</button></div>`;
    } else if (!S.loaded) {
        html += `<div class="fl-empty"><p>Loading files…</p></div>`;
    } else if (!files.length && !S.q && S.kind === 'all' && !S.fileCount) {
        html += `<div class="fl-empty"><div class="hero">${_svg(ICON.folder, 28, 1.6)}</div>
          <h3>Keep what your conversations produce</h3>
          <p style="margin:0;max-width:360px;line-height:1.5">Tables, charts, answers and the files you attach, in one place — with where each one came from.</p>
          <ol><li><span>1</span>Press <strong>Keep</strong> under a table, a chart or an answer</li>
              <li><span>2</span>Attach a file in a conversation — it appears here</li>
              <li><span>3</span>Or add files directly</li></ol>
          <button type="button" class="card-btn card-btn--primary card-btn--lg" data-add>Add files</button>
          ${S.available ? '' : '<p style="margin:0;font-size:12px">Keeping files is turned off for you. An admin can turn it on in Administration → Components → Files.</p>'}</div>`;
    } else if (!files.length && S.agent && !S.q && S.kind === 'all') {
        const who = _agentTag(S.agent);
        html += `<div class="fl-empty"><p style="margin:0;color:var(--text-primary);font-weight:600">No files from ${who ? `@${esc(who)}` : 'this agent'} yet</p>
          <p style="margin:0;font-size:12px">The list follows the agent your next question goes to. You have ${S.fileCount} file${S.fileCount === 1 ? '' : 's'} from other agents.</p>
          <button type="button" class="card-btn card-btn--neutral" data-all-agents>Show all agents</button></div>`;
    } else if (!files.length) {
        html += `<div class="fl-empty"><p style="margin:0;color:var(--text-primary);font-weight:600">${S.q ? `No files match “${esc(S.q)}”` : 'Nothing here'}</p>
          <p style="margin:0;font-size:12px">${S.kind === 'deleted' ? `Deleted files stay here for the retention period, then their content and keys are removed.` : 'Search reads names, conversation titles, questions and agents.'}</p></div>`;
    } else {
        for (const g of _groups(files)) {
            const link = g.link ? `<a data-glink="${g.link.kind}" data-gid="${esc(g.link.id)}">${g.link.kind === 'task' ? 'Open task' : 'Open conversation'}</a>` : '';
            html += `<div class="fl-grp"><span class="fl-grp-t">${g.agent && g.agent.a.tag ? _agentPill(g.agent.a, g.agent.f) : esc(g.title)}</span><span class="fl-grp-n">${g.items.length}</span>${g.sub ? `<span class="fl-grp-s">· ${esc(g.sub)}</span>` : ''}${link}</div>`;
            if (S.view === 'grid') {
                html += `<div class="fl-grid">${g.items.map(f => `
                  <button type="button" class="fl-tile${f.id === S.sel ? ' is-sel' : ''}" data-id="${esc(f.id)}" role="option" aria-selected="${f.id === S.sel}">
                    <span class="fl-thumb" data-thumb="${f.category === 'image' ? esc(f.id) : ''}">${_svg(_glyphOf(f), 30, 1.4)}</span>
                    <span class="fl-nm u-tip" data-tip="${esc(f.filename)}">${esc(f.filename)}</span>
                    <span class="fl-meta">${esc(f.type_label)} · ${_agentPill(_agentOf(f), f)}</span>
                  </button>`).join('')}</div>`;
            } else {
                html += g.items.map(f => `
                  <div class="fl-row${f.id === S.sel ? ' is-sel' : ''}" data-id="${esc(f.id)}">
                    <input type="checkbox" data-check="${esc(f.id)}" ${S.checked.has(f.id) ? 'checked' : ''} aria-label="Select ${esc(f.filename)}">
                    <button type="button" class="fl-row-main" role="option" aria-selected="${f.id === S.sel}">
                      <span class="fl-ic">${_svg(_glyphOf(f))}</span>
                      <span class="fl-tx"><span class="fl-nm-row"><span class="fl-nm u-tip" data-tip="${esc(f.filename)}">${esc(f.filename)}</span>${_badges(f)}</span>
                        <span class="fl-meta">${_rowMeta(f)}</span></span>
                    </button>
                    ${f.deleted_at ? '' : `<button type="button" class="fl-ibtn u-tip" data-dl="${esc(f.id)}" data-tip="Download" aria-label="Download ${esc(f.filename)}">${ICON.download}</button>`}
                  </div>`).join('');
            }
        }
    }
    host.innerHTML = html;
    host.querySelector('[data-undo]')?.addEventListener('click', _undo);
    host.querySelector('[data-retry]')?.addEventListener('click', _load);
    host.querySelector('[data-all-agents]')?.addEventListener('click', _showAllAgents);
    host.querySelector('[data-add]')?.addEventListener('click', _pickLocalFiles);
    host.querySelectorAll('[data-bulk]').forEach(b => b.addEventListener('click', () => _bulk(b.dataset.bulk)));
    host.querySelectorAll('[data-glink]').forEach(a => a.addEventListener('click', () => {
        if (a.dataset.glink === 'session') _openSession(a.dataset.gid);
        else import('/api/v1/components/scheduler/renderer').then(m => m.openSchedulerSplitPanel({ action: 'list', tasks: [] })).catch(() => {});
    }));
    host.querySelectorAll('.fl-row').forEach(row => {
        row.querySelector('.fl-row-main').addEventListener('click', (e) => _clickRow(row.dataset.id, e));
        row.querySelector('[data-check]').addEventListener('click', (e) => { e.stopPropagation(); _toggleCheck(row.dataset.id, e.shiftKey); });
        row.querySelector('[data-dl]')?.addEventListener('click', (e) => { e.stopPropagation(); _download(row.dataset.id); });
    });
    host.querySelectorAll('.fl-tile').forEach(t => t.addEventListener('click', (e) => _clickRow(t.dataset.id, e)));
    host.querySelectorAll('[data-thumb]').forEach(el => { if (el.dataset.thumb) _thumb(el, el.dataset.thumb); });
}

async function _thumb(el, id) {
    if (!S.preview[id]) {
        const r = await _api(`/files/${encodeURIComponent(id)}/preview`);
        S.preview[id] = r.ok ? r.body.preview : { kind: 'none' };
    }
    const pv = S.preview[id];
    if (pv && pv.kind === 'image' && pv.data_url) el.innerHTML = `<img alt="" src="${esc(pv.data_url)}">`;
}

function _clickRow(id, e) {
    if (e && e.shiftKey) { _toggleCheck(id, true); return; }
    if (e && (e.metaKey || e.ctrlKey)) { _toggleCheck(id, false); return; }
    _select(id);
}

function _select(id) {
    S.sel = id; S.menu = false; S.renaming = false; S.anchor = id;
    if (S.narrow) { S.detailOpen = true; document.getElementById('files-split-panel')?.classList.add('fl-detail-open'); }
    _renderList();
    _renderDetail();
    _loadDetail(id);
}

function _toggleCheck(id, range) {
    if (range && S.anchor && S.anchor !== id) {
        const ids = S.files.map(f => f.id);
        const a = ids.indexOf(S.anchor), b = ids.indexOf(id);
        if (a >= 0 && b >= 0) ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => S.checked.add(x));
    } else if (S.checked.has(id)) S.checked.delete(id);
    else S.checked.add(id);
    S.anchor = id;
    _renderList();
}

function _say(msg) {
    const live = _body()?.querySelector('#fl-live');
    if (live) live.textContent = msg;
}

function _renderFoot() {
    const host = _body()?.querySelector('#fl-foot');
    if (!host) return;
    const st = S.storage || {};
    const frac = st.fraction != null ? Math.min(1, st.fraction) : null;
    host.innerHTML = `<span>${S.fileCount} file${S.fileCount === 1 ? '' : 's'}</span>
      ${frac != null ? `<span class="fl-meter${st.warn ? ' warn' : ''}" role="img" aria-label="${esc(st.used_label)} of ${esc(st.quota_label)} used"><span style="width:${(frac * 100).toFixed(1)}%"></span></span>
        <span>${esc(st.used_label)} of ${esc(st.quota_label)}</span>` : `<span>${esc(st.used_label || '')} used</span>`}
      ${st.warn ? '<span style="color:var(--text-primary)">Files is nearly full — delete files or ask an admin for more space.</span>' : ''}
      <span class="fl-spacer"></span>
      <span>/ search · ↑↓ move · Enter preview · Space select · D download · F2 rename</span>`;
}

// ── detail ───────────────────────────────────────────────────────────────────
// the component an export was saved from, in words
const COMPONENT_WORDS = { knowledge_graph: 'A knowledge graph', canvas: 'A canvas', dashboard: 'A dashboard', flow: 'A flow',
                          explain_plan: 'An explain plan', action: 'An action receipt', chart: 'A chart' };

function _steps(f, d) {
    const prov = (d && d.provenance) || {};
    const a = _agentOf(f);
    const agentV = _agentPill(a, f, true);
    const conv = f.session_id
        ? (prov.session_exists === false ? '<span class="v muted">Conversation deleted</span>'
            : `<a data-open-session="${esc(f.session_id)}" data-turn="${esc(f.turn_id ?? '')}">${esc(prov.session_title || f.session_title || 'Open the conversation')}</a>`)
        : '—';
    const rows = [];
    if (f.origin_kind === 'upload') {
        rows.push(['Uploaded', `by you, ${esc(_when(f.created_at))}`]);
        rows.push(['Conversation', f.session_id ? conv : 'added directly to Files']);
        rows.push(['Agent', f.profile_type || f.profile_tag ? `${agentV} <span style="color:var(--text-muted)">(selected when uploaded)</span>` : '—']);
    } else if (f.origin_kind === 'flow_run') {
        const src = f.source || {};
        rows.push(['Flow', esc(src.flow_name || 'A flow')]);
        rows.push(['Run', `${esc(_when(f.created_at))}${f.flow_run_id ? ` <span style="color:var(--text-muted)">· run ${esc(String(f.flow_run_id).slice(0, 8))}</span>` : ''}`]);
        rows.push(['Node', esc(src.node_label || 'File output')]);
        rows.push(['Agent', f.profile_type || f.profile_tag ? agentV : '<span style="color:var(--text-muted)">the flow\u2019s default</span>']);
    } else if (f.origin_kind === 'export') {
        const src = f.source || {};
        if (f.component_id === 'dashboard' && Array.isArray(src.tiles)) {
            const times = src.tiles.map(t => t.last_refreshed).filter(Boolean).sort();
            rows.push(['Snapshot of', `the dashboard “${esc(src.dashboard_name || f.session_title || 'Dashboard')}”`]);
            rows.push(['Taken', esc(_when(src.taken_at || f.created_at))]);
            rows.push(['Tiles', `${src.tiles.length} tile${src.tiles.length === 1 ? '' : 's'}`
                + (times.length ? ` · refreshed ${esc(_when(times[0]))}${times.length > 1 && times[times.length - 1] !== times[0] ? ` to ${esc(_when(times[times.length - 1]))}` : ''}` : ' · none refreshed yet')]);
        } else {
            rows.push(['Saved from', esc(COMPONENT_WORDS[f.component_id] || (f.session_id ? 'A conversation' : 'An export'))]);
            if (f.session_id) rows.push(['Conversation', conv]);
        }
        rows.push(['Agent', f.profile_type || f.profile_tag ? agentV : '—']);
    } else if (f.origin_kind === 'scheduled_run') {
        rows.push(['Scheduled task', esc(f.scheduled_task_name || 'Scheduled task')]);
        rows.push(['Run', esc(new Date((f.created_at || '') + (f.created_at && !f.created_at.endsWith('Z') ? 'Z' : '')).toLocaleString())]);
        rows.push(['Agent', agentV]);
        rows.push(['Model', esc(f.model || '—')]);
        rows.push(['Evidence', esc(f.evidence || '—')]);
    } else {
        rows.push(['Conversation', conv]);
        rows.push([f.turn_id != null ? `Turn ${esc(f.turn_id)}` : 'Turn', f.question ? `“${esc(f.question)}”` : '—']);
        rows.push(['Agent', f.profile_type || f.profile_tag ? agentV : '—']);
        rows.push(['Model', esc(f.model || '—')]);
        // live: the evidence component itself is the value (one slot, said once); else the
        // summary from the stored bill of materials, labelled as such
        rows.push(['Evidence', prov.session_exists && f.turn_id != null && window.EvidenceView
            ? '<span data-ev-host></span>'
            : esc(f.evidence || '—') + (prov.session_exists === false && f.has_bom ? ' <span style="color:var(--text-muted)">(from the stored record)</span>' : '')]);
    }
    rows.push(['Stored in', esc(f.store?.name || '—')]);
    return rows.map(([k, v]) => `<li><span class="dot" aria-hidden="true"></span><span class="k">${k}</span><span class="v">${v}</span></li>`).join('');
}

function _previewHtml(f, pv) {
    if (!pv) return '<div class="fl-pv-empty">Loading preview…</div>';
    if (pv.kind === 'table') {
        const cols = pv.columns || [];
        const rows = pv.rows || [];
        return `<div class="fl-pv-scroll"><table><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
          <tbody>${rows.map(r => `<tr>${cols.map((_, i) => `<td>${esc(r[i] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
          <div class="fl-pv-note">Showing ${rows.length.toLocaleString()} of ${(pv.total_rows || rows.length).toLocaleString()} row${(pv.total_rows || rows.length) === 1 ? '' : 's'}</div>`;
    }
    if (pv.kind === 'markdown') {
        // raw HTML in the file is escaped first: Markdown formatting renders, embedded HTML cannot run
        const safe = String(pv.text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const html = (typeof marked !== 'undefined') ? marked.parse(safe) : `<pre>${safe}</pre>`;
        return `<div class="fl-pv-scroll"><div class="fl-pv-md">${html}</div></div>${pv.truncated ? '<div class="fl-pv-note">Preview shows the beginning of the file</div>' : ''}`;
    }
    if (pv.kind === 'code' || pv.kind === 'document') {
        const lines = String(pv.text || '').split('\n').slice(0, 400);
        return `<div class="fl-pv-scroll"><div class="fl-pv-code">${lines.map((l, i) => `<div><span>${i + 1}</span><span>${esc(l)}</span></div>`).join('')}</div></div>
          ${pv.pages ? `<div class="fl-pv-note">${pv.pages} page${pv.pages === 1 ? '' : 's'} — text of the first pages</div>` : (pv.truncated ? '<div class="fl-pv-note">Preview shows the beginning of the file</div>' : '')}`;
    }
    if (pv.kind === 'image') {
        return `<div class="fl-pv-img"><img alt="${esc(f.filename)}" src="${esc(pv.data_url)}"></div>${f.pair_id && f.ext === 'png' ? '<div class="fl-pv-note">Kept with its data as a CSV — the file to trust for the figures</div>' : ''}`;
    }
    if (pv.kind === 'unavailable') return `<div class="fl-pv-empty">${esc(pv.message || 'This file cannot be read right now.')}</div>`;
    return `<div class="fl-pv-empty">No preview for ${esc(f.type_label)} files. Download it to open it.</div>`;
}

function _renderDetail() {
    const host = _body()?.querySelector('#fl-detail');
    if (!host) return;
    const f = _selected();
    if (!f) { host.innerHTML = S.files.length ? '' : ''; return; }
    const d = S.detail[f.id];
    const pv = S.preview[f.id];
    const fmts = (f.formats || []);
    const evidence = f.has_bom || (f.session_id && f.turn_id != null && f.origin_kind !== 'upload');
    const title = S.renaming
        ? `<input id="fl-rename" value="${esc(f.filename)}" aria-label="New name">`
        : `<h3 class="u-tip" data-tip="${esc(f.filename)}">${esc(f.filename)}</h3>`;
    const tags = [];
    if (f.encrypted) tags.push(`<span class="fl-tag">${_svg(ICON.lock, 11, 2)}Encrypted</span>`);
    if (f.data_derived) tags.push(`<span class="fl-tag">${_svg(ICON.data, 11, 2)}From a data source</span>`);
    if (f.category === 'upload') tags.push(`<span class="fl-tag">${_svg(ICON.pin, 11, 2)}${f.lifetime === 'kept' ? 'Kept — stays when the conversation is deleted' : 'Deleted with the conversation'}</span>`);
    const uses = (d && d.provenance && d.provenance.uses) || [];
    host.innerHTML = `
      <button type="button" class="fl-back" data-back>← All files</button>
      <div class="fl-d-head"><div class="fl-d-title">${_svg(_glyphOf(f), 18)}${title}</div>
        <span class="fl-d-meta">${esc(f.type_label)} · ${esc(f.size_label)} · ${esc(f.origin_kind === 'upload' ? 'uploaded' : 'kept')} ${esc(_when(f.created_at))}</span></div>
      <div class="fl-pv">${_previewHtml(f, pv)}</div>
      <div class="fl-acts">
        ${f.deleted_at ? `<button type="button" class="card-btn card-btn--primary" data-act="restore">Restore</button>
            <span style="font-size:12px;color:var(--text-muted)">In Recently deleted · ${esc(_purgeIn(f.purge_after))}, with its key</span>`
        : `<div class="fl-split">
             <button type="button" class="card-btn card-btn--primary" data-act="download" aria-label="Download as ${esc(fmts[0]?.label || f.type_label)}">${ICON.download}Download <span class="fl-fmt">${esc(fmts[0]?.label || f.type_label)}</span></button>
             <button type="button" class="card-btn card-btn--primary" data-act="menu" aria-haspopup="menu" aria-expanded="${S.menu}" aria-label="More download formats">${ICON.chevron}</button>
             ${S.menu ? `<div class="fl-menu" role="menu">${fmts.map(m => `<button type="button" role="menuitem" data-fmt="${esc(m.id)}"><span>${esc(m.label)}</span><small>${esc(m.hint)}</small></button>`).join('')}
               ${evidence ? `<button type="button" role="menuitem" data-fmt="${esc(fmts[0]?.id || 'original')}" data-evidence="1"><span>With evidence (zip)</span><small>The file and the evidence of its answer, verifiable offline</small></button>` : ''}</div>` : ''}
           </div>
           ${f.attachable ? '<button type="button" class="card-btn card-btn--neutral" data-act="ask">Ask about this file</button>' : ''}
           ${f.can_canvas ? '<button type="button" class="card-btn card-btn--neutral" data-act="canvas">Open in Canvas</button>' : ''}
           ${f.can_knowledge ? '<button type="button" class="card-btn card-btn--neutral" data-act="knowledge" aria-expanded="' + (S.kgPick === f.id) + '">Add to knowledge</button>' : ''}
           ${f.category === 'upload' && f.lifetime === 'conversation' ? '<button type="button" class="card-btn card-btn--success" data-act="keep">Keep</button>' : ''}
           <span class="fl-spacer"></span>
           <button type="button" class="card-btn card-btn--neutral" data-act="rename" aria-label="Rename ${esc(f.filename)}">Rename</button>
           <button type="button" class="card-btn card-btn--danger" data-act="delete" aria-label="Delete ${esc(f.filename)}">Delete</button>`}
      </div>
      ${S.kgPick === f.id && !f.deleted_at ? _knowledgeChooser(f) : ''}
      <div id="fl-act-msg"></div>
      ${f.attach_note && !f.deleted_at ? `<p class="fl-msg">${esc(f.attach_note)}</p>` : ''}
      <div class="fl-card"><span class="fl-card-t">Where this came from</span><ol class="fl-steps">${_steps(f, d)}</ol>
        ${tags.length ? `<div class="fl-tags">${tags.join('')}</div>` : ''}</div>
      <div class="fl-card"><span class="fl-card-t">Used in</span>
        <div class="fl-uses">${uses.length ? uses.map(u => u.exists
            ? `<a data-open-session="${esc(u.session_id)}" data-turn="${esc(u.turn_id ?? '')}">Turn ${esc(u.turn_id ?? '?')} of “${esc(u.session_title || 'a conversation')}”</a>`
            : `<span style="color:var(--text-muted)">Turn ${esc(u.turn_id ?? '?')} of a deleted conversation</span>`).join('')
          : '<span style="color:var(--text-muted)">Not read by any turn yet.</span>'}</div></div>`;
    host.querySelector('[data-back]')?.addEventListener('click', () => {
        S.detailOpen = false;
        document.getElementById('files-split-panel')?.classList.remove('fl-detail-open');
        _body()?.querySelector(`.fl-row[data-id="${CSS.escape(f.id)}"] .fl-row-main`)?.focus();
    });
    host.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => _act(b.dataset.act, f)));
    host.querySelectorAll('[data-fmt]').forEach(b => b.addEventListener('click', () => { S.menu = false; _download(f.id, b.dataset.fmt, !!b.dataset.evidence); _renderDetail(); }));
    host.querySelectorAll('[data-open-session]').forEach(a => a.addEventListener('click', () => _openSession(a.dataset.openSession, a.dataset.turn)));
    _wireKnowledgeChooser(host, f);
    const evHost = host.querySelector('[data-ev-host]');
    if (evHost && window.EvidenceView && window.EvidenceView.mount) {
        try { window.EvidenceView.mount(evHost, f.session_id, f.turn_id); } catch (_) { /* evidence is optional */ }
    }
    const ri = host.querySelector('#fl-rename');
    if (ri) {
        ri.focus(); ri.select();
        ri.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); _rename(f, ri.value); }
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); S.renaming = false; _renderDetail(); }
        });
        ri.addEventListener('blur', () => { if (S.renaming) _rename(f, ri.value); });
    }
}

function _msg(text, warn = false) {
    const el = _body()?.querySelector('#fl-act-msg');
    if (el) el.innerHTML = text ? `<p class="fl-msg${warn ? ' warn' : ''}" role="status">${esc(text)}</p>` : '';
    _say(text || '');
}

// ── acts ─────────────────────────────────────────────────────────────────────
async function _act(act, f) {
    if (act === 'menu') { S.menu = !S.menu; _renderDetail(); return; }
    if (act === 'download') return _download(f.id, (f.formats || [])[0]?.id || 'original');
    if (act === 'rename') { S.renaming = true; _renderDetail(); return; }
    if (act === 'delete') return _delete([f.id]);
    if (act === 'restore') {
        const r = await _api(`/files/${encodeURIComponent(f.id)}/restore`, { method: 'POST' });
        if (r.ok) { S.notice = `${f.filename} restored.`; S.undoId = null; S.kind = 'all'; _saveUi(); _load(); } else _msg(r.body.message, true);
        return;
    }
    if (act === 'keep') {
        const r = await _api(`/files/${encodeURIComponent(f.id)}/keep`, { method: 'POST' });
        if (r.ok) { S.notice = `${f.filename} is kept. It stays when the conversation is deleted.`; S.undoId = null; _load(); } else _msg(r.body.message, true);
        return;
    }
    if (act === 'ask') return _attach([f.id], true);
    if (act === 'canvas') return _toCanvas(f);
    if (act === 'knowledge') {
        S.kgPick = S.kgPick === f.id ? null : f.id;
        if (S.kgPick && S.kgRepos == null) {
            _renderDetail();
            const r = await _api('/files/knowledge-targets');
            S.kgRepos = r.ok ? (r.body.repositories || []) : [];
        }
        _renderDetail();
    }
}

// ── Add to knowledge repository (PROP-010 §4.10) ─────────────────────────────
// The document goes through the repository's OWN upload flow (its firewall, chunking,
// embedding and change tracking); Files only chooses the repository.
function _knowledgeChooser(f) {
    const repos = S.kgRepos;
    if (repos == null) return '<div class="fl-card"><span class="fl-card-t">Add to a knowledge repository</span><p class="fl-msg">Loading your repositories…</p></div>';
    if (!repos.length) {
        return `<div class="fl-card"><span class="fl-card-t">Add to a knowledge repository</span>
          <p class="fl-msg">You own no knowledge repository yet. Create one in Intelligence → Knowledge, then add ${esc(f.filename)} to it.</p></div>`;
    }
    return `<div class="fl-card" data-kg-chooser><span class="fl-card-t">Add to a knowledge repository</span>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 0 2px">
        <label class="fl-sr" for="fl-kg-repo">Repository</label>
        <select id="fl-kg-repo" style="flex:1;min-width:180px">${repos.map(r => `<option value="${esc(r.id)}">${esc(r.name)}${r.document_count != null ? ` · ${esc(r.document_count)} document${r.document_count === 1 ? '' : 's'}` : ''}</option>`).join('')}</select>
        <button type="button" class="card-btn card-btn--primary" data-kg-add>Add</button>
        <button type="button" class="card-btn card-btn--neutral" data-kg-cancel>Cancel</button>
      </div>
      <p class="fl-msg" style="margin:6px 0 0">The repository reads it the way it reads any upload: screened, split into chunks and embedded. A document already there with the same name is refreshed, not duplicated.</p></div>`;
}

function _wireKnowledgeChooser(host, f) {
    const box = host.querySelector('[data-kg-chooser]');
    if (!box) return;
    box.querySelector('[data-kg-cancel]')?.addEventListener('click', () => { S.kgPick = null; _renderDetail(); });
    box.querySelector('[data-kg-add]')?.addEventListener('click', async (e) => {
        const sel = box.querySelector('#fl-kg-repo');
        const repo = (S.kgRepos || []).find(r => String(r.id) === String(sel && sel.value));
        if (!repo) return;
        e.currentTarget.disabled = true;
        e.currentTarget.textContent = 'Adding…';
        const r = await _api(`/files/${encodeURIComponent(f.id)}/to-knowledge`, { method: 'POST', body: JSON.stringify({ collection_id: repo.id }) });
        S.kgPick = null;
        _renderDetail();
        if (!r.ok) { _msg(r.body.message || 'The repository did not take the document', true); return; }
        _msg(r.body.status === 'unchanged'
            ? `${repo.name} already holds this version of ${f.filename} — nothing to add.`
            : `${f.filename} was added to ${repo.name}${r.body.chunks_stored ? ` as ${r.body.chunks_stored} chunk${r.body.chunks_stored === 1 ? '' : 's'}` : ''}. Focus agents that use ${repo.name} can now retrieve it.`);
    });
}

function _filenameFrom(resp, fallback) {
    const cd = resp.headers.get('Content-Disposition') || '';
    const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) { try { return decodeURIComponent(star[1]); } catch (_) { /* fall through */ } }
    const plain = cd.match(/filename="([^"]+)"/i);
    return plain ? plain[1] : fallback;
}

function _save(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 250);
}

async function _download(id, fmt, withEvidence = false) {
    const f = S.files.find(x => x.id === id) || S.detail[id];
    const p = new URLSearchParams();
    if (fmt) p.set('format', fmt);
    if (withEvidence) p.set('with_evidence', '1');
    const r = await fetch(`/api/v1/files/${encodeURIComponent(id)}/download?${p.toString()}`, { headers: _hdrs(false) });
    if (!r.ok) {
        let m = 'The download did not start.';
        try { m = (await r.json()).message || m; } catch (_) { /* keep default */ }
        if (S.sel !== id) _select(id);
        setTimeout(() => _msg(m, true), 50);
        return;
    }
    _save(await r.blob(), _filenameFrom(r, f?.filename || 'file'));
    _say(`Downloaded ${f?.filename || 'the file'}`);
    if (f && f.is_new) setTimeout(_load, 300);
}

async function _rename(f, value) {
    S.renaming = false;
    const v = (value || '').trim();
    if (!v || v === f.filename) { _renderDetail(); return; }
    const r = await _api(`/files/${encodeURIComponent(f.id)}`, { method: 'PATCH', body: JSON.stringify({ filename: v }) });
    if (!r.ok) { _renderDetail(); _msg(r.body.message, true); return; }
    S.detail = {}; _load();
}

async function _delete(ids) {
    const go = async () => {
        let done = 0;
        let last = null;
        for (const id of ids) {
            const r = await _api(`/files/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (r.ok) { done += 1; last = id; }
        }
        const name = ids.length === 1 ? (S.files.find(f => f.id === ids[0])?.filename || 'The file') : `${done} files`;
        S.notice = done ? `${name} moved to Recently deleted.` : 'Nothing was deleted.';
        S.undoId = done === 1 && ids.length === 1 ? last : null;
        S.checked.clear();
        _say(S.notice);
        _load();
    };
    if (ids.length > 1) _confirm(`Move ${ids.length} files to Recently deleted?`, go);
    else go();
}

async function _undo() {
    if (!S.undoId) return;
    const id = S.undoId;
    S.undoId = null;
    const r = await _api(`/files/${encodeURIComponent(id)}/restore`, { method: 'POST' });
    S.notice = r.ok ? 'Restored.' : (r.body.message || 'Could not restore.');
    _load();
}

async function _attach(ids, focusComposer) {
    const sid = window.__currentSessionId;
    if (!sid) { _msg('Open a conversation first, then attach the file to it.', true); return; }
    let ok = 0;
    const refused = [];
    for (const id of ids) {
        const r = await _api(`/files/${encodeURIComponent(id)}/attach`, { method: 'POST', body: JSON.stringify({ session_id: sid }) });
        if (r.ok && r.body.file) {
            ok += 1;
            if (typeof window.__addPendingAttachment === 'function') window.__addPendingAttachment(r.body.file);
        } else refused.push(r.body.message || 'not attachable');
    }
    if (ok && focusComposer) document.getElementById('user-input')?.focus();
    S.notice = ok ? `${ok === 1 ? 'Attached to the conversation' : `${ok} files attached to the conversation`} — ask your question.` : (refused[0] || 'Nothing was attached.');
    S.undoId = null;
    if (refused.length && ok) S.notice += ` ${refused.length} could not be attached.`;
    _renderList();
}

async function _toCanvas(f) {
    const r = await _api(`/files/${encodeURIComponent(f.id)}/to-canvas`, { method: 'POST' });
    if (!r.ok) { _msg(r.body.message, true); return; }
    const make = async (title) => fetch('/api/v1/canvases', {
        method: 'POST', headers: _hdrs(), body: JSON.stringify({ mode: 'create', title, content: r.body.content, language: r.body.language, session_id: window.__currentSessionId || null }),
    });
    let c = await make(r.body.title);
    if (c.status === 409) c = await make(`${r.body.title} (from Files)`);
    if (!c.ok) { _msg('The canvas could not be created.', true); return; }
    const canvas = await c.json();
    if (typeof window.__openCanvasById === 'function') await window.__openCanvasById(canvas.canvas_id);
    _say(`Opened ${f.filename} in Canvas`);
}

async function _bulk(kind) {
    const ids = [...S.checked];
    if (!ids.length) return;
    if (kind === 'clear') { S.checked.clear(); _renderList(); return; }
    if (kind === 'delete') return _delete(ids);
    if (kind === 'attach') return _attach(ids, true);
    if (kind === 'zip') {
        const r = await fetch('/api/v1/files/download', { method: 'POST', headers: _hdrs(), body: JSON.stringify({ ids }) });
        if (!r.ok) { let m = 'The download did not start.'; try { m = (await r.json()).message || m; } catch (_) { /* default */ } S.notice = m; _renderList(); return; }
        const skipped = parseInt(r.headers.get('X-Files-Skipped') || '0', 10);
        _save(await r.blob(), 'files.zip');
        S.notice = skipped ? `Downloaded. ${skipped} file${skipped === 1 ? ' was' : 's were'} not included — the zip names why.` : `Downloaded ${ids.length} files.`;
        S.undoId = null;
        _renderList();
    }
}

function _openSession(sid, turn) {
    if (!sid) return;
    const go = () => {
        if (!turn) return;
        setTimeout(() => {
            const el = document.querySelector(`#chat-log [data-turn-id="${CSS.escape(String(turn))}"]`);
            el?.closest('.message-bubble, .flex')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 600);
    };
    if (window.__currentSessionId === sid) { go(); return; }
    if (typeof window.__reloadSession === 'function') Promise.resolve(window.__reloadSession(sid)).then(go).catch(() => {});
}

function _pickLocalFiles() {
    _body()?.querySelector('#fl-add-input')?.click();
}

async function _addLocal(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;
    const fd = new FormData();
    files.forEach(f => fd.append('files', f));
    if (window.__currentProfileId) fd.append('profile_id', window.__currentProfileId);
    const r = await _api('/files', { method: 'POST', body: fd });
    const added = (r.body.files || []).length;
    const refused = r.body.refused || [];
    S.notice = added ? `Added ${added} file${added === 1 ? '' : 's'}.` + (refused.length ? ` ${refused.length} not added: ${refused[0].reason}.` : '')
        : (r.body.message || 'Nothing was added.');
    S.undoId = null;
    if (added) { S.focusAfterLoad = r.body.files[0].id; S.kind = 'all'; }
    _load();
}

// ── keyboard (scoped to the panel) ───────────────────────────────────────────
function _onKey(e) {
    const tag = (e.target && e.target.tagName) || '';
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && e.target.type !== 'checkbox';
    if (typing) {
        if (e.key === 'Escape' && e.target.id === 'fl-q' && e.target.value) { e.target.value = ''; S.q = ''; _load(); e.preventDefault(); }
        if (e.key === 'ArrowDown' && e.target.id === 'fl-q') { e.preventDefault(); _body()?.querySelector('#fl-list')?.focus(); _move(0); }
        return;
    }
    const ids = S.files.map(f => f.id);
    if (e.key === '/') { e.preventDefault(); _body()?.querySelector('#fl-q')?.focus(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const i = Math.max(0, ids.indexOf(S.sel));
        const j = Math.min(ids.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1)));
        if (e.shiftKey) { S.checked.add(ids[i]); S.checked.add(ids[j]); }
        _select(ids[j]);
        _body()?.querySelector(`.fl-row[data-id="${CSS.escape(ids[j])}"], .fl-tile[data-id="${CSS.escape(ids[j])}"]`)?.scrollIntoView({ block: 'nearest' });
        return;
    }
    if (e.key === ' ' && S.sel) { e.preventDefault(); _toggleCheck(S.sel, false); return; }
    if (e.key === 'Enter' && S.sel) { e.preventDefault(); _loadDetail(S.sel); return; }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); ids.forEach(id => S.checked.add(id)); _renderList(); return; }
    if (e.key.toLowerCase() === 'd' && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        if (S.checked.size) _bulk('zip'); else if (S.sel) _download(S.sel, (_selected()?.formats || [])[0]?.id);
        return;
    }
    if (e.key === 'F2' && S.sel) { e.preventDefault(); S.renaming = true; _renderDetail(); return; }
    if ((e.key === 'Delete' || e.key === 'Backspace') && (S.checked.size || S.sel)) {
        e.preventDefault();
        _confirm(S.checked.size > 1 ? `Move ${S.checked.size} files to Recently deleted?` : 'Move this file to Recently deleted?',
            () => _delete(S.checked.size ? [...S.checked] : [S.sel]));
        return;
    }
    if (e.key === 'Escape') {
        if (S.menu) { S.menu = false; _renderDetail(); return; }
        if (S.narrow && S.detailOpen) { _body()?.querySelector('[data-back]')?.click(); return; }
        if (S.checked.size) { S.checked.clear(); _renderList(); return; }
    }
}

function _move(i) {
    if (S.files[i]) _select(S.files[i].id);
}

// ── the component entry (a conversation card that opens the panel) ───────────
// The inline body of a TDA_Files save (the agent kept a file this turn): what was kept, where,
// and the two acts — open it in Files, download it through the governed exit. list and read
// are headless (the model answers in prose), so this is the only inline body.
export function renderFiles(containerId, payload) {
    _css();
    const host = document.getElementById(containerId);
    if (!host) return;
    let spec = payload;
    try { if (typeof payload === 'string') spec = JSON.parse(payload); } catch (_) { spec = {}; }
    const f = (spec && spec.file) || null;
    if (!f || !f.id) {
        host.innerHTML = `<button type="button" class="card-btn card-btn--neutral">${_svg(ICON.folder, 14)}Open Files</button>`;
        host.querySelector('button').addEventListener('click', () => openFilesPanel({ focus: spec && spec.file_id }));
        return;
    }
    const meta = [f.type_label, f.size_label, f.store && f.store.name ? `in ${f.store.name}` : null, f.encrypted ? 'encrypted' : null]
        .filter(Boolean).map(esc).join(' · ');
    host.innerHTML = `<div class="fl-inline" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:2px 0">
        <span style="color:var(--text-muted);display:flex">${_svg(ICON.folder, 16)}</span>
        <span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:2px">
          <span style="font-weight:500;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${spec.already ? 'Already in Files' : 'Kept in Files'} · ${esc(f.filename)}</span>
          <span style="font-size:12px;color:var(--text-muted)">${meta}</span>
        </span>
        <button type="button" class="card-btn card-btn--neutral card-btn--sm" data-open>Open</button>
        <button type="button" class="card-btn card-btn--neutral card-btn--sm" data-dl aria-label="Download ${esc(f.filename)}">${ICON.download}Download</button>
        <span data-msg role="status" style="flex-basis:100%;font-size:12px;color:var(--text-muted)"></span>
      </div>`;
    host.querySelector('[data-open]').addEventListener('click', () => openFilesPanel({ focus: f.id }));
    host.querySelector('[data-dl]').addEventListener('click', async () => {
        const fmt = (f.formats || [])[0]?.id || 'original';
        const r = await fetch(`/api/v1/files/${encodeURIComponent(f.id)}/download?format=${encodeURIComponent(fmt)}`, { headers: _hdrs(false) });
        if (!r.ok) {
            let m = 'The download did not start.';
            try { m = (await r.json()).message || m; } catch (_) { /* keep default */ }
            host.querySelector('[data-msg]').textContent = m;
            return;
        }
        _save(await r.blob(), _filenameFrom(r, f.filename));
    });
}

export default renderFiles;

if (typeof window !== 'undefined') {
    window.FilesPanel = { open: openFilesPanel, close: closeFilesPanel, isOpen: isFilesPanelOpen };
}
