/**
 * Database Navigator — renderer.
 *
 * Two surfaces, one state:
 *   renderDatabaseNavigator(containerId, payload)  inline chat card for a tool result
 *   openDatabaseNavigatorPanel(spec)               the split panel (tree · detail · search)
 * The resource panel reaches the navigator through the Workspace section
 * (workspacePanelHandler.js lists this agent's catalog sources + recently viewed
 * objects and calls openDatabaseNavigatorPanel) — there is no rail panel of its own.
 *
 * Every fact rendered here was computed by the backend NavigatorService (the same
 * implementation the agent's tool reads); this module renders, filters and routes —
 * it never re-derives a role, a key or a flag. Theme-aware throughout (CSS variables,
 * --sp-accent from the manifest), monochrome SVG icons, keyboard-navigable.
 */

const ACCENT = 'var(--sp-accent, #84cc16)';
const API = '/api/v1/database-navigator';
const LS_KEY = 'dbnav_last_v1';

// ── helpers ───────────────────────────────────────────────────────────────────
function _esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function _hdr() {
  const t = localStorage.getItem('tda_auth_token');
  return { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) };
}
async function _get(path, params = {}) {
  const qs = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
  const r = await fetch(`${API}${path}${qs ? '?' + qs : ''}`, { headers: _hdr(), cache: 'no-store' });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message || `HTTP ${r.status}`);
  return data;
}
async function _post(path, body) {
  const r = await fetch(`${API}${path}`, { method: 'POST', headers: _hdr(), body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message || `HTTP ${r.status}`);
  return data;
}
function _num(n) { return (n == null || n === '') ? '' : Number(n).toLocaleString(); }
function _profileId() {
  try {
    if (window.activeProfileOverrideId) return window.activeProfileOverrideId;
    const st = window.state || {};
    return st.currentResourcePanelProfileId || null;
  } catch (_) { return null; }
}
function _fillComposer(text) {
  const input = document.getElementById('user-input');
  if (!input) return;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
}
async function _copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { return false; }
}

// ── icons (monochrome, currentColor) ──────────────────────────────────────────
const I = {
  db: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>',
  table: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/></svg>',
  view: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  other: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 12h10M4 17h7"/></svg>',
  col: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M9 4v16M15 4v16"/></svg>',
  chev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M15 8l2 2M18 5l2 2"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V6l8-3z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
  code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  hash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 9h16M4 15h16M10 3L8 21M16 3l-2 18"/></svg>',
  graph: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5M12 12l-5 5M12 12l5 5"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>',
};

// ── CSS (injected once) ───────────────────────────────────────────────────────
function _css() {
  if (document.getElementById('dbnav-css-v1')) return;
  const s = document.createElement('style');
  s.id = 'dbnav-css-v1';
  s.textContent = `
#dbnav-split-panel { width:0; min-width:0; max-width:60%; flex-shrink:0; overflow:hidden; display:flex; flex-direction:column;
  background:var(--card-bg, rgba(20,22,25,0.6)); border-left:1px solid var(--border-primary, rgba(255,255,255,0.1));
  transition:width .3s ease, min-width .3s ease; }
#dbnav-split-panel.dbnav-split--open { width:52%; min-width:min(560px, 60vw); }
.dbnav-fullscreen #dbnav-split-panel.dbnav-split--open { position:fixed !important; top:var(--dbnav-fullscreen-top,0px);
  left:0 !important; right:0 !important; bottom:0 !important; width:auto !important; max-width:none !important; z-index:60; }
.dbnav-body { flex:1; min-height:0; display:flex; flex-direction:column; background:var(--bg-secondary, var(--bg-tertiary,#f8fafc)); }
.dbnav-toolbar { display:flex; align-items:center; gap:8px; padding:8px 10px; border-bottom:1px solid var(--border-secondary, rgba(148,163,184,0.14)); }
.dbnav-search { flex:1; display:flex; align-items:center; gap:6px; padding:0 8px; height:32px; border-radius:8px;
  background:var(--bg-primary, rgba(15,23,42,0.35)); border:1px solid var(--border-secondary, rgba(148,163,184,0.18)); color:var(--text-muted,#94a3b8); }
.dbnav-search:focus-within { border-color:${ACCENT}; box-shadow:0 0 0 2px color-mix(in srgb, ${ACCENT} 22%, transparent); }
.dbnav-search svg { width:14px; height:14px; flex-shrink:0; }
.dbnav-search input { flex:1; background:none; border:none; outline:none; color:var(--text-primary,#e2e8f0); font-size:0.8rem; min-width:0; }
.dbnav-search kbd { font-size:0.62rem; padding:1px 5px; border-radius:4px; border:1px solid var(--border-secondary, rgba(148,163,184,0.25)); color:var(--text-muted,#94a3b8); font-family:inherit; }
.dbnav-toggle { display:inline-flex; align-items:center; gap:5px; font-size:0.7rem; color:var(--text-muted,#94a3b8); cursor:pointer; user-select:none; white-space:nowrap; }
.dbnav-toggle input { accent-color:${ACCENT}; }
.dbnav-body { container-type:inline-size; }
.dbnav-main { flex:1; min-height:0; display:grid; grid-template-columns:minmax(200px, 38%) 1fr; }
@container (max-width: 640px) {
  .dbnav-main { grid-template-columns:1fr; grid-template-rows:minmax(120px, 38%) 1fr; }
  .dbnav-tree { border-right:none; border-bottom:1px solid var(--border-secondary, rgba(148,163,184,0.14)); }
  .dbnav-facts { grid-template-columns:repeat(3, 1fr); }
}
.dbnav-tree { min-height:0; overflow:auto; border-right:1px solid var(--border-secondary, rgba(148,163,184,0.14)); padding:6px 4px; }
.dbnav-detail { min-height:0; overflow:auto; padding:12px 14px; }
.dbnav-row { display:flex; align-items:center; gap:6px; padding:5px 8px; border-radius:7px; cursor:pointer; color:var(--text-primary,#e2e8f0); font-size:0.78rem; min-width:0; }
.dbnav-row:hover, .dbnav-row.is-focus { background:var(--hover-bg, rgba(148,163,184,0.08)); }
.dbnav-row.is-selected { background:color-mix(in srgb, ${ACCENT} 16%, transparent); }
.dbnav-row svg { width:13px; height:13px; flex-shrink:0; color:var(--text-muted,#94a3b8); }
.dbnav-row .chev { transition:transform .15s; }
.dbnav-row.is-open .chev { transform:rotate(90deg); }
.dbnav-row .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; min-width:0; }
.dbnav-row .meta { font-size:0.66rem; color:var(--text-muted,#94a3b8); font-variant-numeric:tabular-nums; white-space:nowrap; }
.dbnav-row.is-system .name { color:var(--text-muted,#94a3b8); font-style:italic; }
.dbnav-children { margin-left:14px; border-left:1px dashed var(--border-secondary, rgba(148,163,184,0.2)); padding-left:2px; }
.dbnav-filter { margin:2px 4px 4px 14px; }
.dbnav-filter input { width:100%; box-sizing:border-box; font-size:0.72rem; padding:3px 8px; border-radius:6px; background:var(--bg-primary, rgba(15,23,42,0.35));
  border:1px solid var(--border-secondary, rgba(148,163,184,0.18)); color:var(--text-primary,#e2e8f0); outline:none; }
.dbnav-empty { padding:20px 12px; color:var(--text-muted,#94a3b8); font-size:0.78rem; line-height:1.5; }
.dbnav-empty strong { color:var(--text-primary,#e2e8f0); }
table.dbnav-t th.is-masked { text-decoration:underline dotted; text-underline-offset:3px; }
[draggable="true"] { cursor:grab; }
button.dbnav-insert-join { margin-left:6px; cursor:pointer; border:1px solid var(--border-primary, rgba(148,163,184,0.3)); background:transparent; color:var(--text-primary); font:inherit; }
button.dbnav-insert-join:hover { background:var(--hover-bg-strong, rgba(148,163,184,0.15)); }
.dbnav-chip { display:inline-flex; align-items:center; gap:4px; font-size:0.64rem; padding:2px 7px; border-radius:999px; white-space:nowrap;
  background:var(--bg-overlay, rgba(15,23,42,0.3)); border:1px solid var(--border-secondary, rgba(148,163,184,0.18)); color:var(--text-muted,#94a3b8); }
.dbnav-chip.role { color:${ACCENT}; border-color:color-mix(in srgb, ${ACCENT} 45%, transparent); background:color-mix(in srgb, ${ACCENT} 10%, transparent); }
.dbnav-chip.f-key, .dbnav-chip.f-identifier { color:var(--status-info,#0284c7); border-color:color-mix(in srgb, var(--status-info,#0284c7) 40%, transparent); }
.dbnav-chip.f-pii { color:var(--status-error,#dc2626); border-color:color-mix(in srgb, var(--status-error,#dc2626) 40%, transparent); }
.dbnav-chip.f-text_date, .dbnav-chip.f-code { color:var(--status-warning,#d97706); border-color:color-mix(in srgb, var(--status-warning,#d97706) 40%, transparent); }
.dbnav-chip.f-measure { color:var(--status-success,#16a34a); border-color:color-mix(in srgb, var(--status-success,#16a34a) 40%, transparent); }
.dbnav-chip.scope-out { color:var(--status-warning,#d97706); border-color:color-mix(in srgb, var(--status-warning,#d97706) 45%, transparent); }
.dbnav-chip.scope-in { color:var(--status-success,#16a34a); border-color:color-mix(in srgb, var(--status-success,#16a34a) 40%, transparent); }
.dbnav-h { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.dbnav-title { font-size:0.95rem; font-weight:600; color:var(--text-primary,#e2e8f0); word-break:break-all; }
.dbnav-sub { font-size:0.72rem; color:var(--text-muted,#94a3b8); margin-top:4px; line-height:1.5; }
.dbnav-why { font-size:0.68rem; color:var(--text-muted,#94a3b8); margin-top:2px; }
.dbnav-facts { display:grid; grid-template-columns:repeat(6, minmax(0, 1fr)); gap:6px; margin:10px 0; }
@container (max-width: 900px) { .dbnav-facts { grid-template-columns:repeat(3, minmax(0, 1fr)); } }
.dbnav-fact { min-width:0; overflow:hidden; }
.dbnav-fact .v { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dbnav-fact { padding:7px 9px; border-radius:8px; background:var(--bg-overlay, rgba(15,23,42,0.25)); border:1px solid var(--border-secondary, rgba(148,163,184,0.14)); }
.dbnav-fact .k { font-size:0.6rem; text-transform:uppercase; letter-spacing:.05em; color:var(--text-muted,#94a3b8); }
.dbnav-fact .v { font-size:0.82rem; color:var(--text-primary,#e2e8f0); font-variant-numeric:tabular-nums; margin-top:1px; }
.dbnav-fact .v small { font-size:0.62rem; color:var(--text-muted,#94a3b8); }
.dbnav-actions { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 12px; }
.dbnav-act { display:inline-flex; align-items:center; gap:5px; font-size:0.72rem; padding:4px 9px; border-radius:7px; cursor:pointer; white-space:nowrap;
  background:none; color:var(--text-primary,#e2e8f0); border:1px solid var(--border-secondary, rgba(148,163,184,0.22)); font:inherit; }
.dbnav-act svg { width:12px; height:12px; color:${ACCENT}; }
.dbnav-act:hover { border-color:${ACCENT}; background:color-mix(in srgb, ${ACCENT} 8%, transparent); }
.dbnav-act:disabled { opacity:.45; cursor:default; }
.dbnav-tabs { display:flex; gap:2px; border-bottom:1px solid var(--border-secondary, rgba(148,163,184,0.14)); margin-bottom:8px; overflow-x:auto; scrollbar-width:none; }
.dbnav-tabs::-webkit-scrollbar { display:none; }
.dbnav-tab { white-space:nowrap; flex-shrink:0; }
.dbnav-tab { font-size:0.72rem; padding:6px 10px; cursor:pointer; color:var(--text-muted,#94a3b8); border-bottom:2px solid transparent; background:none; border-top:none; border-left:none; border-right:none; font:inherit; }
.dbnav-tab.is-active { color:var(--text-primary,#e2e8f0); border-bottom-color:${ACCENT}; }
.dbnav-tab .n { font-size:0.62rem; color:var(--text-muted,#94a3b8); margin-left:4px; }
table.dbnav-t { width:100%; border-collapse:collapse; font-size:0.74rem; }
table.dbnav-t th { text-align:left; font-weight:600; font-size:0.62rem; text-transform:uppercase; letter-spacing:.05em; color:var(--text-muted,#94a3b8);
  padding:4px 6px; border-bottom:1px solid var(--border-secondary, rgba(148,163,184,0.18)); position:sticky; top:0; background:var(--bg-secondary, var(--bg-tertiary,#f8fafc)); }
table.dbnav-t td { padding:4px 6px; border-bottom:1px solid var(--border-secondary, rgba(148,163,184,0.09)); color:var(--text-primary,#e2e8f0); vertical-align:top; }
table.dbnav-t tr:hover td { background:var(--hover-bg, rgba(148,163,184,0.06)); }
table.dbnav-t td.mono, table.dbnav-t td.num { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.7rem; }
table.dbnav-t td.num { text-align:right; font-variant-numeric:tabular-nums; }
table.dbnav-t td.muted { color:var(--text-muted,#94a3b8); }
.dbnav-pre { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.7rem; white-space:pre-wrap; word-break:break-word; padding:8px 10px; border-radius:8px;
  background:var(--bg-primary, rgba(15,23,42,0.35)); border:1px solid var(--border-secondary, rgba(148,163,184,0.16)); color:var(--text-primary,#e2e8f0); max-height:320px; overflow:auto; }
.dbnav-note { display:flex; gap:6px; align-items:flex-start; font-size:0.7rem; color:var(--text-muted,#94a3b8); padding:6px 8px; border-radius:7px;
  background:var(--bg-overlay, rgba(15,23,42,0.2)); border:1px dashed var(--border-secondary, rgba(148,163,184,0.25)); margin:6px 0; }
.dbnav-note svg { width:12px; height:12px; flex-shrink:0; margin-top:1px; }
.dbnav-q { display:block; width:100%; text-align:left; font-size:0.74rem; padding:6px 9px; margin:3px 0; border-radius:7px; cursor:pointer; font:inherit;
  background:none; color:var(--text-primary,#e2e8f0); border:1px solid var(--border-secondary, rgba(148,163,184,0.16)); }
.dbnav-q:hover { border-color:${ACCENT}; }
.dbnav-hit { padding:5px 8px; border-radius:7px; cursor:pointer; font-size:0.76rem; display:flex; gap:6px; align-items:center; color:var(--text-primary,#e2e8f0); }
.dbnav-hit:hover, .dbnav-hit.is-focus { background:var(--hover-bg, rgba(148,163,184,0.08)); }
.dbnav-hit svg { width:12px; height:12px; color:var(--text-muted,#94a3b8); flex-shrink:0; }
.dbnav-hit .path { color:var(--text-muted,#94a3b8); font-size:0.66rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dbnav-hit mark { background:color-mix(in srgb, ${ACCENT} 30%, transparent); color:inherit; border-radius:2px; padding:0 1px; }
.dbnav-group { font-size:0.6rem; text-transform:uppercase; letter-spacing:.06em; color:var(--text-muted,#94a3b8); padding:8px 8px 2px; }
.dbnav-src { display:flex; align-items:center; gap:6px; font-size:0.68rem; color:var(--text-muted,#94a3b8); padding:6px 10px; border-bottom:1px solid var(--border-secondary, rgba(148,163,184,0.12)); }
.dbnav-src select { font:inherit; font-size:0.7rem; background:var(--bg-primary, rgba(15,23,42,0.35)); color:var(--text-primary,#e2e8f0);
  border:1px solid var(--border-secondary, rgba(148,163,184,0.2)); border-radius:6px; padding:2px 6px; max-width:60%; }
.dbnav-loading { padding:12px; font-size:0.74rem; color:var(--text-muted,#94a3b8); }
.dbnav-loading::after { content:'…'; animation:dbnav-dots 1s steps(3,end) infinite; }
@keyframes dbnav-dots { 0%{content:'.'} 33%{content:'..'} 66%{content:'...'} }
@media (prefers-reduced-motion: reduce) { .dbnav-loading::after { animation:none; } }
.dbnav-card { border:1px solid color-mix(in srgb, ${ACCENT} 18%, transparent); border-left:3px solid ${ACCENT}; border-radius:12px; padding:0.7rem 0.85rem;
  background:color-mix(in srgb, ${ACCENT} 4%, transparent); max-width:640px; }
.dbnav-card-h { display:flex; align-items:center; gap:0.5rem; color:${ACCENT}; }
.dbnav-card-h svg { width:14px; height:14px; }
.dbnav-card-t { font-weight:600; font-size:0.8rem; color:var(--text-primary,#e2e8f0); }
.dbnav-card-m { color:var(--text-muted,#94a3b8); font-size:0.8rem; margin-top:0.3rem; line-height:1.45; }
.dbnav-canvas-btn { margin-top:0.55rem; background:none; border:none; color:${ACCENT}; font-family:inherit; font-size:0.78rem; font-weight:600; cursor:pointer; padding:0; }
.dbnav-canvas-btn:focus-visible { outline:2px solid ${ACCENT}; outline-offset:2px; border-radius:3px; }
.dbnav-card-acts { display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:flex-end; margin-top:0.55rem; }
.dbnav-mini { margin-top:0.5rem; overflow:auto; max-height:200px; }
.dbnav-recent-block { margin-top:10px; }
`;
  document.head.appendChild(s);
}

// ── state ─────────────────────────────────────────────────────────────────────
const S = {
  sources: [], source: null, reason: '', dbs: [], objects: {}, open: new Set(), filters: {},
  selected: null, detail: null, tab: 'columns', showSystem: false, query: '', hits: null,
  loading: {}, fullscreen: false, preview: null, recent: [], profileId: null,
};

function _saveLast() {
  try { localStorage.setItem(LS_KEY, JSON.stringify({ source: S.source, selected: S.selected, showSystem: S.showSystem, recent: S.recent.slice(0, 8) })); } catch (_) {}
}
function _loadLast() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || 'null') || {}; } catch (_) { return {}; }
}
function _pushRecent(db, name) {
  S.recent = [{ db, name }, ...S.recent.filter(r => !(r.db === db && r.name === name))].slice(0, 8);
}
function _srcConnectionId() {
  const s = S.sources.find(x => x.id === S.source);
  return (s && s.kind === 'direct' && s.connection_id) || '';
}
/** The query language of the selected source: a MongoDB statement is JSON, never SQL. */
function _srcLanguage() {
  const s = S.sources.find(x => x.id === S.source);
  return (s && String(s.driver || '').toLowerCase() === 'mongodb') ? 'mongodb' : 'sql';
}
function _srcCaps() {
  const s = S.sources.find(x => x.id === S.source);
  return new Set((s && s.capabilities) || []);
}

// ── data loading ──────────────────────────────────────────────────────────────
async function _loadSources() {
  const r = await _get('/sources', { profile_id: S.profileId || _profileId() });
  S.sources = r.sources || []; S.reason = r.reason || '';
  const last = _loadLast();
  const wanted = (S.source && S.sources.some(s => s.id === S.source)) ? S.source
    : (last.source && S.sources.some(s => s.id === last.source)) ? last.source : r.default;
  S.source = wanted || null;
  if (Array.isArray(last.recent)) S.recent = last.recent;
  if (typeof last.showSystem === 'boolean') S.showSystem = last.showSystem;
  return r;
}
async function _loadDbs(refresh = false) {
  if (!S.source) { S.dbs = []; return; }
  const r = await _get('/databases', { source: S.source, profile_id: S.profileId || _profileId(), refresh: refresh ? 1 : '' });
  S.dbs = r.databases || [];
}
async function _loadObjects(db, refresh = false) {
  const r = await _get('/objects', { source: S.source, database: db, profile_id: S.profileId || _profileId(), refresh: refresh ? 1 : '' });
  S.objects[db] = r.objects || [];
  return S.objects[db];
}
async function _loadDetail(db, name, refresh = false) {
  const r = await _get('/object', { source: S.source, database: db, name, profile_id: S.profileId || _profileId(), refresh: refresh ? 1 : '' });
  return r;
}

// ── rendering: panel skeleton ─────────────────────────────────────────────────
function _root() { return document.getElementById('dbnav-split-content'); }

function _renderSkeleton() {
  const root = _root(); if (!root) return;
  const srcOpts = S.sources.map(s => `<option value="${_esc(s.id)}" ${s.id === S.source ? 'selected' : ''}>${_esc(s.label)} · ${_esc(s.kind === 'direct' ? s.driver : 'MCP')}</option>`).join('');
  root.innerHTML = `
    <div class="dbnav-body">
      <div class="dbnav-src">
        <span>Source</span>
        <select id="dbnav-source" ${S.sources.length ? '' : 'disabled'}>${srcOpts || '<option>none</option>'}</select>
        <span id="dbnav-src-reason" class="u-tip" data-tip="${_esc(S.reason)}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">${_esc(S.reason)}</span>
      </div>
      <div class="dbnav-toolbar">
        <label class="dbnav-search"><span>${I.search}</span><input id="dbnav-q" type="text" placeholder="Search databases, tables, columns" autocomplete="off" spellcheck="false" value="${_esc(S.query)}"><kbd>/</kbd></label>
        <label class="dbnav-toggle"><input id="dbnav-sys" type="checkbox" ${S.showSystem ? 'checked' : ''}> system</label>
      </div>
      <div class="dbnav-main">
        <div class="dbnav-tree" id="dbnav-tree" tabindex="0" aria-label="Database objects"></div>
        <div class="dbnav-detail" id="dbnav-detail"></div>
      </div>
    </div>`;
  root.querySelector('#dbnav-source').onchange = async (e) => {
    S.source = e.target.value; S.objects = {}; S.open = new Set(); S.selected = null; S.detail = null; S.hits = null;
    _saveLast(); _renderTree(); _renderDetail();
    await _loadDbs().catch(err => _treeError(err)); _renderTree();
  };
  const q = root.querySelector('#dbnav-q');
  let t = null;
  q.oninput = () => { S.query = q.value; clearTimeout(t); t = setTimeout(_runSearch, 220); };
  q.onkeydown = (e) => {
    if (e.key === 'Escape') { q.value = ''; S.query = ''; S.hits = null; _renderTree(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); _focusNext(1); }
    if (e.key === 'Enter') { const f = document.querySelector('#dbnav-tree .is-focus'); if (f) f.click(); }
  };
  root.querySelector('#dbnav-sys').onchange = (e) => { S.showSystem = e.target.checked; _saveLast(); _renderTree(); };
  const tree = root.querySelector('#dbnav-tree');
  tree.onkeydown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); _focusNext(1); }
    if (e.key === 'ArrowUp') { e.preventDefault(); _focusNext(-1); }
    if (e.key === 'Enter') { const f = tree.querySelector('.is-focus'); if (f) f.click(); }
    if (e.key === 'ArrowRight') { const f = tree.querySelector('.is-focus.dbnav-db:not(.is-open)'); if (f) f.click(); }
    if (e.key === 'ArrowLeft') { const f = tree.querySelector('.is-focus.dbnav-db.is-open'); if (f) f.click(); }
  };
}
function _focusNext(dir) {
  const rows = [...document.querySelectorAll('#dbnav-tree .dbnav-row, #dbnav-tree .dbnav-hit')];
  if (!rows.length) return;
  const i = rows.findIndex(r => r.classList.contains('is-focus'));
  rows.forEach(r => r.classList.remove('is-focus'));
  const n = rows[Math.max(0, Math.min(rows.length - 1, (i < 0 ? (dir > 0 ? 0 : rows.length - 1) : i + dir)))];
  n.classList.add('is-focus'); n.scrollIntoView({ block: 'nearest' });
}
function _treeError(err) {
  const tree = document.getElementById('dbnav-tree');
  if (tree) tree.innerHTML = `<div class="dbnav-empty"><strong>Could not read the catalog.</strong><br>${_esc(err.message || err)}</div>`;
}

// ── rendering: tree ───────────────────────────────────────────────────────────
function _kindIcon(kind) { return kind === 'view' ? I.view : (kind === 'table' ? I.table : I.other); }

function _renderTree() {
  const tree = document.getElementById('dbnav-tree'); if (!tree) return;
  if (S.hits) return _renderHits(tree);
  if (!S.source) {
    tree.innerHTML = `<div class="dbnav-empty"><strong>No catalog source yet.</strong><br>${_esc(S.reason || 'Add a Database Connection or bind an MCP server that lists tables.')}</div>`;
    return;
  }
  if (!S.dbs.length) { tree.innerHTML = `<div class="dbnav-loading">Reading databases</div>`; return; }
  const dbs = S.dbs.filter(d => S.showSystem || !d.is_system);
  const hiddenN = S.dbs.length - dbs.length;
  tree.innerHTML = dbs.map(d => {
    const isOpen = S.open.has(d.name);
    const meta = d.object_count != null ? `${_num(d.object_count)} obj` : '';
    let kids = '';
    if (isOpen) {
      const objs = S.objects[d.name];
      if (!objs) kids = `<div class="dbnav-children"><div class="dbnav-loading">Reading objects</div></div>`;
      else {
        const f = (S.filters[d.name] || '').toLowerCase();
        const shown = objs.filter(o => !f || o.name.toLowerCase().includes(f));
        kids = `<div class="dbnav-children">
          ${objs.length > 12 ? `<div class="dbnav-filter"><input type="text" placeholder="filter ${objs.length} objects" value="${_esc(S.filters[d.name] || '')}" data-db="${_esc(d.name)}"></div>` : ''}
          ${shown.length ? shown.map(o => {
            const sel = S.selected && S.selected.db === d.name && S.selected.name === o.name;
            const rc = o.row_count != null ? `${_num(o.row_count)}${o.row_count_source === 'statistics' ? '~' : ''}` : '';
            return `<div class="dbnav-row dbnav-obj ${sel ? 'is-selected' : ''}" data-db="${_esc(d.name)}" data-name="${_esc(o.name)}" title="${_esc(o.comment || o.qualified)}">${_kindIcon(o.kind)}<span class="name">${_esc(o.name)}</span><span class="meta">${rc}</span></div>`;
          }).join('') : `<div class="dbnav-empty" style="padding:6px 8px;">${objs.length ? 'no object matches the filter' : 'no tables or views'}</div>`}
        </div>`;
      }
    }
    return `<div class="dbnav-row dbnav-db ${isOpen ? 'is-open' : ''} ${d.is_system ? 'is-system' : ''}" data-db="${_esc(d.name)}" title="${_esc(d.comment || d.name)}"><span class="chev">${I.chev}</span>${I.db}<span class="name">${_esc(d.name)}</span><span class="meta">${meta}</span></div>${kids}`;
  }).join('') + (hiddenN ? `<div class="dbnav-empty" style="padding:8px;">${hiddenN} system database${hiddenN === 1 ? '' : 's'} hidden</div>` : '');

  tree.querySelectorAll('.dbnav-db').forEach(el => el.onclick = () => _toggleDb(el.dataset.db));
  tree.querySelectorAll('.dbnav-obj').forEach(el => { el.onclick = () => _select(el.dataset.db, el.dataset.name); _draggable(el, `${el.dataset.db}.${el.dataset.name}`); });
  tree.querySelectorAll('.dbnav-filter input').forEach(inp => {
    inp.oninput = () => { S.filters[inp.dataset.db] = inp.value; const pos = inp.selectionStart; _renderTree(); const again = tree.querySelector(`.dbnav-filter input[data-db="${CSS.escape(inp.dataset.db)}"]`); if (again) { again.focus(); again.setSelectionRange(pos, pos); } };
    inp.onclick = (e) => e.stopPropagation();
  });
  _scrollToSelected();
}
async function _toggleDb(db) {
  if (S.open.has(db)) { S.open.delete(db); _renderTree(); return; }
  S.open.add(db); _renderTree();
  if (!S.objects[db]) {
    try { await _loadObjects(db); } catch (err) { S.objects[db] = []; _treeError(err); return; }
  }
  _renderTree();
}

// ── rendering: search hits ────────────────────────────────────────────────────
function _hl(text, q) {
  const t = _esc(text); const terms = String(q || '').toLowerCase().split(/[^a-z0-9]+/).filter(x => x.length >= 2);
  if (!terms.length) return t;
  const re = new RegExp(`(${terms.map(x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig');
  return t.replace(re, '<mark>$1</mark>');
}
async function _runSearch() {
  const q = (S.query || '').trim();
  if (q.length < 2) { S.hits = null; _renderTree(); return; }
  if (!S.source) return;
  const tree = document.getElementById('dbnav-tree');
  if (tree) tree.innerHTML = `<div class="dbnav-loading">Searching</div>`;
  try {
    const r = await _get('/search', { source: S.source, q, database: S.selected ? S.selected.db : '', profile_id: S.profileId || _profileId() });
    if ((S.query || '').trim() !== q) return;   // a newer query is in flight
    S.hits = r.hits || [];
    _renderTree();
  } catch (err) { _treeError(err); }
}
function _renderHits(tree) {
  const hits = S.hits || [];
  if (!hits.length) { tree.innerHTML = `<div class="dbnav-empty">Nothing named like <strong>${_esc(S.query)}</strong>.<br>Try a shorter fragment; search matches names and comments, not questions.</div>`; return; }
  const groups = [['database', 'Databases'], ['object', 'Tables'], ['view', 'Views'], ['column', 'Columns'], ['other', 'Other']];
  tree.innerHTML = groups.map(([k, label]) => {
    const hs = hits.filter(h => (h.kind === k) || (k === 'other' && !['database', 'object', 'view', 'column'].includes(h.kind)));
    if (!hs.length) return '';
    return `<div class="dbnav-group">${label} · ${hs.length}</div>` + hs.map(h => {
      const icon = h.kind === 'column' ? I.col : h.kind === 'database' ? I.db : _kindIcon(h.kind === 'object' ? 'table' : h.kind);
      const main = h.kind === 'column' ? h.column : (h.object || h.database);
      const path = h.kind === 'column' ? `${h.database}.${h.object} · ${h.data_type || ''}` : (h.kind === 'database' ? '' : h.database);
      return `<div class="dbnav-hit" data-kind="${_esc(h.kind)}" data-db="${_esc(h.database)}" data-name="${_esc(h.object || '')}" data-col="${_esc(h.column || '')}">${icon}<span>${_hl(main, S.query)}</span><span class="path">${_esc(path)}</span></div>`;
    }).join('');
  }).join('');
  tree.querySelectorAll('.dbnav-hit').forEach(el => el.onclick = () => {
    if (el.dataset.kind === 'database') { S.hits = null; S.query = ''; const q = document.getElementById('dbnav-q'); if (q) q.value = ''; S.open.add(el.dataset.db); _renderTree(); _toggleDb(el.dataset.db); _toggleDb(el.dataset.db); return; }
    S.hits = null; S.query = ''; const q = document.getElementById('dbnav-q'); if (q) q.value = '';
    S.open.add(el.dataset.db);
    _select(el.dataset.db, el.dataset.name, el.dataset.col || null);
  });
  // a hit drags as its qualified name (a column as `db.table.column`)
  tree.querySelectorAll('.dbnav-hit').forEach(el => {
    if (el.dataset.kind === 'database') return _draggable(el, el.dataset.db);
    _draggable(el, el.dataset.kind === 'column' ? `${el.dataset.db}.${el.dataset.name}.${el.dataset.col}` : `${el.dataset.db}.${el.dataset.name}`);
  });
}

// ── rendering: detail ─────────────────────────────────────────────────────────
// Bring the selected object into the tree: leave search mode, open its database, show a
// system database it lives in, drop a per-database filter that would hide it, and scroll
// the row into view. Used when something outside the tree selects an object (the
// canvas's "Describe in navigator", a reopened panel) — the tree must show WHERE it is.
let _revealPending = false;
function _revealSelected() {
  if (!S.selected) return;
  const { db, name } = S.selected;
  if (S.hits) {
    S.hits = null; S.query = '';
    const q = document.getElementById('dbnav-q'); if (q) q.value = '';
  }
  S.open.add(db);
  const d = (S.dbs || []).find(x => x.name === db);
  if (d && d.is_system && !S.showSystem) {
    S.showSystem = true; const sys = document.getElementById('dbnav-sys'); if (sys) sys.checked = true;
  }
  const f = (S.filters[db] || '').toLowerCase();
  if (f && !String(name).toLowerCase().includes(f)) delete S.filters[db];
  _revealPending = true;
}
function _scrollToSelected() {
  if (!_revealPending || !S.selected) return;
  const tree = document.getElementById('dbnav-tree'); if (!tree) return;
  const row = tree.querySelector('.dbnav-obj.is-selected');
  if (!row) return;            // objects still loading — the next render scrolls
  _revealPending = false;
  row.scrollIntoView({ block: 'center' });
}

async function _select(db, name, focusColumn = null, reveal = false) {
  S.selected = { db, name }; S.preview = null; S.tab = 'columns'; S.detail = null;
  _pushRecent(db, name); _saveLast();
  if (reveal) _revealSelected();
  if (!S.objects[db]) { S.open.add(db); _loadObjects(db).then(_renderTree).catch(() => {}); }
  _renderTree(); _renderDetail();
  try {
    const d = await _loadDetail(db, name);
    if (!S.selected || S.selected.db !== db || S.selected.name !== name) return;
    S.detail = d; _renderDetail(focusColumn);
  } catch (err) {
    const el = document.getElementById('dbnav-detail');
    if (el) el.innerHTML = `<div class="dbnav-empty"><strong>Could not describe ${_esc(db)}.${_esc(name)}.</strong><br>${_esc(err.message || err)}</div>`;
  }
}

function _flagChips(flags, ont) {
  const order = ['key', 'identifier', 'pii', 'text_date', 'date', 'timestamp', 'code', 'measure', 'text', 'identity', 'unique_by_stats'];
  const tip = { key: 'declared key (primary or unique index)', identifier: 'key-shaped name, not a declared key', pii: 'personal data — masked in previews',
    text_date: 'a date stored as text: string order is not date order', date: 'date', timestamp: 'timestamp', code: 'short coded value — decode before filtering',
    measure: 'numeric and summable', text: 'descriptive text', identity: 'identity column', unique_by_stats: 'unique per collected statistics' };
  const out = order.filter(f => (flags || []).includes(f)).map(f => `<span class="dbnav-chip f-${f} u-tip" data-tip="${_esc(tip[f] || f)}">${_esc(f.replace('_', ' '))}</span>`);
  if (ont && ont.allowed_values && Array.isArray(ont.allowed_values) && ont.allowed_values.length) {
    const vals = ont.allowed_values.slice(0, 8).map(v => typeof v === 'object' ? `${v.value ?? v.code ?? ''}=${v.label ?? ''}` : String(v)).join(', ');
    out.push(`<span class="dbnav-chip u-tip" data-tip="${_esc(vals)}">${ont.allowed_values.length} values</span>`);
  }
  if (ont && ont.sampled_shape) out.push(`<span class="dbnav-chip u-tip" data-tip="sampled date shape (Teradata FORMAT) from source validation">${_esc(ont.sampled_shape)}</span>`);
  return out.join(' ');
}

function _renderDetail(focusColumn = null) {
  const el = document.getElementById('dbnav-detail'); if (!el) return;
  if (!S.selected) {
    const recent = S.recent.length ? `<div class="dbnav-recent-block"><div class="dbnav-group" style="padding-left:0;">Recently viewed</div>${S.recent.map(r => `<button class="dbnav-q" data-db="${_esc(r.db)}" data-name="${_esc(r.name)}">${_esc(r.db)}.${_esc(r.name)}</button>`).join('')}</div>` : '';
    el.innerHTML = `<div class="dbnav-empty"><strong>Pick a table or view</strong> to see its columns, keys, relationships, statistics and a sample of its rows.<br>Press <kbd style="font-size:.65rem;border:1px solid var(--border-secondary);border-radius:4px;padding:0 4px;">/</kbd> to search by name.</div>${recent}`;
    el.querySelectorAll('.dbnav-q').forEach(b => b.onclick = () => { S.open.add(b.dataset.db); _select(b.dataset.db, b.dataset.name); });
    return;
  }
  const d = S.detail;
  if (!d) { el.innerHTML = `<div class="dbnav-h"><span class="dbnav-title">${_esc(S.selected.db)}.${_esc(S.selected.name)}</span></div><div class="dbnav-loading">Reading the dictionary</div>`; return; }
  const info = d.info, intel = d.intelligence || {}, ont = d.ontology || {}, caps = new Set(((intel.source || {}).capabilities) || []);
  const scope = intel.scope === 'out_of_scope' ? `<span class="dbnav-chip scope-out u-tip" data-tip="This database is outside the agent's trusted scope — the grounding gate will flag answers drawn from it">outside trusted scope</span>`
    : intel.scope === 'in_scope' ? `<span class="dbnav-chip scope-in u-tip" data-tip="Inside the agent's trusted scope (from its bound ontology)">in trusted scope</span>` : '';
  const roleTip = (intel.role_reasons || []).join('; ') || 'not enough structural evidence';
  const rc = info.row_count != null ? `${_num(info.row_count)}<small> ${info.row_count_source === 'statistics' ? `from statistics${intel.stats_age_days != null ? ', ' + intel.stats_age_days + 'd old' : ''}` : 'exact'}</small>` : `<small>${caps.has('statistics') ? 'no statistics collected' : 'not available from this source'}</small>`;
  const facts = [
    ['Rows', rc], ['Columns', _num(d.columns.length)],
    ['Size', intel.size_human ? intel.size_human : `<small>${caps.has('sizes') ? '—' : 'not available'}</small>`],
    ['Keys', (intel.keys && intel.keys.declared && intel.keys.declared.length) ? intel.keys.declared.map(k => k.columns.join(', ')).join(' · ') : (intel.keys && intel.keys.inferred && intel.keys.inferred.length ? `${intel.keys.inferred[0].columns.join(', ')}<small> inferred</small>` : `<small>${caps.has('indexes') ? 'none declared' : 'not available'}</small>`)],
    ['References', d.foreign_keys_out.length ? _num(d.foreign_keys_out.length) : `<small>${caps.has('foreign_keys') ? 'none' : 'not available'}</small>`],
    ['Referenced by', d.foreign_keys_in.length ? _num(d.foreign_keys_in.length) : `<small>${caps.has('foreign_keys') ? 'none' : 'not available'}</small>`],
  ];
  const tabs = [['columns', 'Columns', d.columns.length], ['keys', 'Keys & joins', d.indexes.length + d.foreign_keys_out.length + d.foreign_keys_in.length + ((intel.inferred_joins || []).length)],
    ['preview', 'Preview', null], ['ask', 'Ask', (intel.questions || []).length], ...(d.view_text ? [['definition', 'Definition', null]] : []), ...(ont.present ? [['ontology', 'Ontology', null]] : [])];
  el.innerHTML = `
    <div class="dbnav-h">
      ${_kindIcon(info.kind).replace('<svg', `<svg style="width:16px;height:16px;color:${ACCENT}"`)}
      <span class="dbnav-title" data-ans-restates>${_esc(info.database)}.${_esc(info.name)}</span>
      <span class="dbnav-chip role u-tip" data-tip="${_esc(roleTip)}">${_esc(intel.role_label || info.kind)}${intel.role_confidence ? ` · ${Math.round(intel.role_confidence * 100)}%` : ''}</span>
      ${scope}
    </div>
    ${info.comment ? `<div class="dbnav-sub">${_esc(info.comment)}</div>` : ''}
    ${ont.present && ont.entity && ont.entity.description ? `<div class="dbnav-sub">${I.graph.replace('<svg', '<svg style="width:11px;height:11px;vertical-align:-1px;margin-right:4px"')}${_esc(ont.entity.description)}</div>` : ''}
    <div class="dbnav-facts">${facts.map(([k, v]) => `<div class="dbnav-fact"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}</div>
    <div class="dbnav-actions">
      ${caps.has('preview') && ['table', 'view'].includes(info.kind) ? `<button class="dbnav-act" data-act="preview">${I.play}Preview rows</button>` : ''}
      ${caps.has('row_count') && ['table', 'view'].includes(info.kind) ? `<button class="dbnav-act" data-act="count">${I.hash}Count rows</button>` : ''}
      <button class="dbnav-act" data-act="copy">${I.copy}Copy name</button>
      <button class="dbnav-act" data-act="sql">${I.copy}Copy sample SQL</button>
      ${_srcConnectionId() && ['table', 'view'].includes(info.kind) ? `<button class="dbnav-act u-tip" data-act="canvas" data-tip="${_srcLanguage() === 'mongodb' ? 'Open a starter aggregation in the canvas, bound to this MongoDB connection' : 'Open a starter SELECT in the SQL canvas, bound to this connection'}">${I.code}Query in canvas</button>` : ''}
      <button class="dbnav-act" data-act="ask">${I.chat}Ask the agent</button>
      ${(intel.actions || []).some(a => a.id === 'chart') ? `<button class="dbnav-act" data-act="chart">${I.chart}Chart it</button>` : ''}
      ${ont.present ? `<button class="dbnav-act" data-act="graph">${I.graph}Open in graph</button>` : ''}
    </div>
    ${(d.unavailable || []).length ? `<div class="dbnav-note">${I.warn}<span>Not available from this source: ${_esc(d.unavailable.join(', '))}.</span></div>` : ''}
    <div class="dbnav-tabs">${tabs.map(([k, l, n]) => `<button class="dbnav-tab ${S.tab === k ? 'is-active' : ''}" data-tab="${k}">${l}${n != null ? `<span class="n">${n}</span>` : ''}</button>`).join('')}</div>
    <div id="dbnav-tabbody"></div>`;
  el.querySelectorAll('.dbnav-tab').forEach(b => b.onclick = () => { S.tab = b.dataset.tab; _renderDetail(); });
  el.querySelectorAll('.dbnav-act').forEach(b => b.onclick = () => _action(b.dataset.act, b));
  _renderTab(focusColumn);
}

function _renderTab(focusColumn) {
  const body = document.getElementById('dbnav-tabbody'); if (!body || !S.detail) return;
  const d = S.detail, intel = d.intelligence || {};
  if (S.tab === 'columns') {
    body.innerHTML = `<table class="dbnav-t"><thead><tr><th>#</th><th>Column</th><th>Type</th><th>Null</th><th>Flags</th><th>Comment</th></tr></thead><tbody>
      ${d.columns.map(c => `<tr data-col="${_esc(c.name)}" ${focusColumn && c.name.toLowerCase() === String(focusColumn).toLowerCase() ? 'style="background:color-mix(in srgb, ' + ACCENT + ' 14%, transparent)"' : ''}>
        <td class="num muted">${c.position ?? ''}</td>
        <td class="mono" style="white-space:nowrap;">${c.is_primary ? I.key.replace('<svg', '<svg style="width:10px;height:10px;display:inline-block;vertical-align:-1px;margin-right:3px;color:var(--status-info,#0284c7)"') : ''}${_esc(c.name)}</td>
        <td class="mono muted">${_esc(c.data_type || '')}${c.stats && c.stats.unique_values != null ? `<span class="u-tip" data-tip="${_num(c.stats.unique_values)} distinct${c.stats.null_count != null ? ', ' + _num(c.stats.null_count) + ' null' : ''} (statistics)" style="margin-left:4px;color:var(--text-muted)">·${_num(c.stats.unique_values)}</span>` : ''}</td>
        <td class="muted">${c.nullable === false ? 'no' : c.nullable === true ? 'yes' : ''}</td>
        <td>${_flagChips(c.flags, c.ontology)}</td>
        <td class="muted">${_esc(c.comment || (c.ontology && c.ontology.description) || '')}</td></tr>`).join('')}
    </tbody></table>`;
    if (focusColumn) { const r = body.querySelector(`tr[data-col="${CSS.escape(focusColumn)}"]`); if (r) r.scrollIntoView({ block: 'center' }); }
    // a column row drags as its bare name — the canvas's completion qualifies it where an alias is in play
    body.querySelectorAll('tr[data-col]').forEach(tr => _draggable(tr, tr.dataset.col));
  } else if (S.tab === 'keys') {
    const idx = d.indexes.length ? `<table class="dbnav-t"><thead><tr><th>Index</th><th>Kind</th><th>Columns</th><th>Unique</th></tr></thead><tbody>${d.indexes.map(i => `<tr><td class="mono">${_esc(i.name)}</td><td class="muted">${_esc(i.kind.replace('_', ' '))}</td><td class="mono">${_esc(i.columns.join(', '))}</td><td class="muted">${i.unique ? 'yes' : ''}</td></tr>`).join('')}</tbody></table>` : `<div class="dbnav-empty" style="padding:6px 0;">No declared keys or indexes${(d.unavailable || []).includes('indexes') ? ' (not available from this source)' : ''}.</div>`;
    const canvas = _canvasOpen();
    const fkRow = (f, dir) => `<tr data-join="${_esc(JSON.stringify({ f, dir }))}"><td class="mono">${dir === 'out' ? `${_esc(f.child_columns.join(', '))}` : `${_esc(f.child_database)}.${_esc(f.child_table)}.${_esc(f.child_columns.join(', '))}`}</td><td class="muted">→</td><td class="mono"><a href="#" data-db="${_esc(dir === 'out' ? f.parent_database : f.child_database)}" data-name="${_esc(dir === 'out' ? f.parent_table : f.child_table)}" class="dbnav-jump">${dir === 'out' ? `${_esc(f.parent_database)}.${_esc(f.parent_table)}.${_esc(f.parent_columns.join(', '))}` : `${_esc(f.parent_columns.join(', '))}`}</a></td><td>${f.declared ? `<span class="dbnav-chip scope-in">declared</span>` : `<span class="dbnav-chip u-tip" data-tip="${_esc(f.reason)}">inferred · ${Math.round(f.confidence * 100)}%</span>`}${canvas ? `<button class="dbnav-chip dbnav-insert-join u-tip" data-tip="Write this JOIN into the open SQL canvas at the cursor">insert join</button>` : ''}</td></tr>`;
    const inferred = (intel.inferred_joins || []);
    const fks = (d.foreign_keys_out.length || d.foreign_keys_in.length || inferred.length)
      ? `<table class="dbnav-t" style="margin-top:10px;"><thead><tr><th>From</th><th></th><th>To</th><th>Basis</th></tr></thead><tbody>
          ${d.foreign_keys_out.map(f => fkRow(f, 'out')).join('')}${d.foreign_keys_in.map(f => fkRow(f, 'in')).join('')}
          ${inferred.map(j => fkRow(j, j.child_table.toLowerCase() === d.info.name.toLowerCase() ? 'out' : 'in')).join('')}</tbody></table>`
      : `<div class="dbnav-empty" style="padding:6px 0;">No declared relationships${(d.unavailable || []).includes('foreign keys') ? ' (not available from this source)' : ''}; describe sibling tables and name-based join candidates will appear here.</div>`;
    body.innerHTML = `<div class="dbnav-group" style="padding-left:0;">Keys & indexes</div>${idx}<div class="dbnav-group" style="padding-left:0;margin-top:8px;">Relationships</div>${fks}${inferred.length ? `<div class="dbnav-note">${I.warn}<span>An inferred join is a hypothesis from column names — verify it before relying on it.</span></div>` : ''}`;
    body.querySelectorAll('.dbnav-jump').forEach(a => a.onclick = (e) => { e.preventDefault(); S.open.add(a.dataset.db); _select(a.dataset.db, a.dataset.name); });
    body.querySelectorAll('.dbnav-insert-join').forEach(b => b.onclick = async (e) => {
      e.preventDefault();
      const tr = b.closest ? b.closest('tr') : b.parentNode.parentNode;
      let spec = null; try { spec = JSON.parse(tr.dataset.join || (tr.getAttribute && tr.getAttribute('data-join')) || 'null'); } catch (_) {}
      if (!spec) return;
      const ok = await _insertIntoCanvas('\n' + _joinClause(spec.f, spec.dir).clause);
      b.textContent = ok ? 'inserted' : 'no canvas open';
      setTimeout(() => { b.textContent = 'insert join'; }, 1500);
    });
  } else if (S.tab === 'preview') {
    if (!S.preview) { body.innerHTML = `<div class="dbnav-empty" style="padding:6px 0;">Click <strong>Preview rows</strong> to sample this ${_esc(d.info.kind)}. Columns flagged as personal data are masked.</div>`; }
    else if (S.preview.loading) body.innerHTML = `<div class="dbnav-loading">Sampling rows</div>`;
    else if (S.preview.error) body.innerHTML = `<div class="dbnav-empty"><strong>Preview failed.</strong><br>${_esc(S.preview.error)}</div>`;
    else _renderPreview(body, S.preview);
  } else if (S.tab === 'ask') {
    body.innerHTML = `<div class="dbnav-sub" style="margin-bottom:6px;">Questions this object can answer — click one to put it in the composer.</div>${(intel.questions || []).map(q => `<button class="dbnav-q">${_esc(q)}</button>`).join('')}
      <div class="dbnav-group" style="padding-left:0;margin-top:8px;">Sample query</div><div class="dbnav-pre">${_esc(intel.sample_sql || '')}</div>`;
    body.querySelectorAll('.dbnav-q').forEach(b => b.onclick = () => _fillComposer(b.textContent));
  } else if (S.tab === 'definition') {
    body.innerHTML = `<div class="dbnav-pre">${_esc(d.view_text)}</div>`;
  } else if (S.tab === 'ontology') {
    const o = d.ontology || {};
    body.innerHTML = `<div class="dbnav-sub">${o.entity && o.entity.name ? `Logical entity <strong style="color:var(--text-primary)">${_esc(o.entity.name)}</strong>` : ''}${o.entity && o.entity.row_count != null ? ` · validated rows ${_num(o.entity.row_count)}` : ''}${o.entity && o.entity.key_unique != null ? ` · key ${o.entity.key_unique ? 'verified unique' : 'NOT unique'}` : ''}</div>
      ${o.declared_keys && o.declared_keys.length ? `<div class="dbnav-sub">Declared keys: <span class="mono">${_esc(o.declared_keys.join(', '))}</span></div>` : ''}
      ${(o.related || []).length ? `<div class="dbnav-group" style="padding-left:0;margin-top:8px;">Related in the ontology</div>${o.related.map(r => `<div class="dbnav-sub">${_esc(r.kind || 'entity')} <strong style="color:var(--text-primary)">${_esc(r.entity)}</strong>${r.direction ? ` (${r.direction === 'out' ? 'references' : 'referenced by'})` : ''}${r.description ? ` — ${_esc(r.description)}` : ''}</div>`).join('')}` : ''}`;
  }
}

function _renderPreview(body, pv) {
  body.innerHTML = `${pv.masked_columns && pv.masked_columns.length ? `<div class="dbnav-note">${I.shield}<span>Masked personal data: ${_esc(pv.masked_columns.join(', '))}.</span></div>` : ''}
    <div style="overflow:auto;max-height:420px;"><table class="dbnav-t"><thead><tr>${pv.columns.map(c => `<th>${_esc(c)}</th>`).join('')}</tr></thead><tbody>
    ${pv.rows.map(r => `<tr>${r.map(v => `<td class="${typeof v === 'number' ? 'num' : ''} ${v == null ? 'muted' : ''}">${v == null ? 'null' : _esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    <div class="dbnav-sub">${pv.rows.length} row${pv.rows.length === 1 ? '' : 's'}${pv.truncated ? ' (limited)' : ''} · ${pv.elapsed_ms || 0} ms · <span class="mono">${_esc(pv.sql || '')}</span></div>`;
}

async function _action(act, btn) {
  const d = S.detail; if (!d) return;
  const { database, name, qualified } = d.info;
  const intel = d.intelligence || {};
  if (act === 'copy') { await _copy(qualified); btn.textContent = 'Copied'; setTimeout(() => _renderDetail(), 900); return; }
  if (act === 'sql') { await _copy(intel.sample_sql || ''); btn.textContent = 'Copied'; setTimeout(() => _renderDetail(), 900); return; }
  if (act === 'ask') { _fillComposer(`Using ${qualified}, `); return; }
  if (act === 'canvas') {
    _openInCanvas({ sql: intel.sample_sql || `SELECT * FROM ${qualified}`, title: `Query · ${qualified}`, connectionId: _srcConnectionId(),
                    language: _srcLanguage() });
    return;
  }
  if (act === 'chart') { const a = (intel.actions || []).find(x => x.id === 'chart'); _fillComposer(a ? a.payload : `Chart ${qualified}`); return; }
  if (act === 'graph') {
    try {
      const m = await import('/api/v1/components/knowledge_graph/renderer');
      const pid = S.profileId || _profileId();
      if (m.openKGSplitPanel) await m.openKGSplitPanel({ profile_id: pid, focus_names: [qualified, name], title: qualified });
    } catch (e) { console.warn('[dbnav] graph open failed', e); }
    return;
  }
  if (act === 'preview') {
    S.tab = 'preview'; S.preview = { loading: true }; _renderDetail();
    try { S.preview = await _post('/preview', { source: S.source, database, name, limit: 50, profile_id: S.profileId || _profileId() }); }
    catch (err) { S.preview = { error: err.message || String(err) }; }
    _renderDetail(); return;
  }
  if (act === 'count') {
    btn.disabled = true; btn.textContent = 'Counting';
    try {
      const r = await _post('/count', { source: S.source, database, name, profile_id: S.profileId || _profileId() });
      d.info.row_count = r.row_count; d.info.row_count_source = 'exact'; delete intel.stats_age_days;
      const cached = (S.objects[database] || []).find(o => o.name === name); if (cached) { cached.row_count = r.row_count; cached.row_count_source = 'exact'; }
    } catch (err) { alert(`Count failed: ${err.message || err}`); }
    _renderDetail(); _renderTree();
  }
}

// ── panel open / close ────────────────────────────────────────────────────────
let _keyHandler = null;
// Component signals: a catalog refresh from anywhere (a REST call, another tab, an agent turn) re-reads
// the open tree; the panel's own Refresh button is not echoed back onto itself (components/signals.py).
if (typeof document !== 'undefined' && typeof UderiaBus !== 'undefined' && !window.__dbnavFollowBound) {
  window.__dbnavFollowBound = true;
  UderiaBus.onComponent('database_navigator', async () => {
    const p = document.getElementById('dbnav-split-panel');
    if (!p || !p.classList.contains('dbnav-split--open') || S._selfRefresh) return;
    S.objects = {}; S.detail = null;
    try { await _loadDbs(true); } catch (err) { _treeError(err); return; }
    _renderTree(); if (S.selected) _select(S.selected.db, S.selected.name);
  });
}

export async function openDatabaseNavigatorPanel(spec) {
  _css();
  const panel = document.getElementById('dbnav-split-panel'); if (!panel) return;
  S.profileId = (spec && spec.profile_id) || null;
  if (spec && spec.source) S.source = spec.source;
  // The split resizer assigns a 360px default to any newly opened panel with no explicit
  // width — too narrow for a tree + detail. Open at a working width instead (the user's
  // last drag, else ~48% of the viewport bounded to [560, 900]); the resizer keeps it.
  if (!panel.style.width || panel.style.width.endsWith('%')) {
    let saved = 0;
    try { saved = Number((JSON.parse(localStorage.getItem('uderia_split_widths_v3') || '{}') || {})['dbnav-split-panel']) || 0; } catch (_) {}
    const px = saved >= 220 ? saved : Math.max(560, Math.min(900, Math.round(window.innerWidth * 0.48)));
    panel.style.width = px + 'px'; panel.style.flex = 'none'; panel.style.maxWidth = 'none'; panel.style.minWidth = '220px';
  }
  panel.style.display = 'flex'; panel.offsetHeight; panel.classList.add('dbnav-split--open');
  _syncCards(true);
  document.getElementById('dbnav-split-close').onclick = closeDatabaseNavigatorPanel;
  const fs = document.getElementById('dbnav-split-fullscreen'); if (fs) fs.onclick = _toggleFullscreen;
  const rf = document.getElementById('dbnav-split-refresh');
  if (rf) rf.onclick = async () => { S._selfRefresh = true; try { await _post('/refresh', { source: S.source }); } catch (_) {} S.objects = {}; S.detail = null; await _loadDbs(true).catch(_treeError); _renderTree(); if (S.selected) _select(S.selected.db, S.selected.name); setTimeout(() => { S._selfRefresh = false; }, 1000); };
  try { window.SplitPanelResizer?.update(); } catch (_) {}
  if (!_keyHandler) {
    _keyHandler = (e) => {
      const p = document.getElementById('dbnav-split-panel');
      if (!p || !p.classList.contains('dbnav-split--open')) return;
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes((document.activeElement || {}).tagName)) { e.preventDefault(); document.getElementById('dbnav-q')?.focus(); }
    };
    document.addEventListener('keydown', _keyHandler);
  }
  const root = _root(); if (root && !root.querySelector('.dbnav-body')) root.innerHTML = `<div class="dbnav-loading" style="padding:14px;">Resolving catalog sources</div>`;
  try { await _loadSources(); } catch (err) { if (root) root.innerHTML = `<div class="dbnav-empty"><strong>Navigator unavailable.</strong><br>${_esc(err.message || err)}</div>`; return; }
  _renderSkeleton(); _renderTree(); _renderDetail();
  if (S.source) {
    try { await _loadDbs(); } catch (err) { _treeError(err); return; }
    _renderTree();
    const want = spec && spec.database ? { db: spec.database, name: spec.object } : (S.selected || (_loadLast().selected || null));
    if (want && want.db) {
      S.open.add(want.db);
      if (want.name) _select(want.db, want.name, (spec && spec.column) || null, true); else { _renderTree(); _toggleDb(want.db); _toggleDb(want.db); }
    }
  }
}

export function closeDatabaseNavigatorPanel() {
  const panel = document.getElementById('dbnav-split-panel'); if (!panel) return;
  if (S.fullscreen) _toggleFullscreen();
  panel.classList.remove('dbnav-split--open'); _syncCards(false);
  panel.style.width = ''; panel.style.minWidth = ''; panel.style.maxWidth = ''; panel.style.flex = '';
  let hidden = false;
  const finish = () => {
    if (hidden) return; hidden = true; panel.removeEventListener('transitionend', onEnd);
    if (!panel.classList.contains('dbnav-split--open')) { panel.style.display = 'none'; }
    try { window.SplitPanelResizer?.update(); } catch (_) {}
  };
  const onEnd = (e) => { if (e.target === panel && e.propertyName === 'width') finish(); };
  panel.addEventListener('transitionend', onEnd); setTimeout(finish, 350);
}

function _toggleFullscreen() {
  const mainArea = document.getElementById('main-content-area'); if (!mainArea) return;
  S.fullscreen = !S.fullscreen;
  if (S.fullscreen) {
    const topNav = document.querySelector('body > nav');
    document.documentElement.style.setProperty('--dbnav-fullscreen-top', (topNav ? topNav.offsetHeight : 0) + 'px');
    mainArea.classList.add('dbnav-fullscreen');
  } else { mainArea.classList.remove('dbnav-fullscreen'); document.documentElement.style.removeProperty('--dbnav-fullscreen-top'); }
  const fs = document.getElementById('dbnav-split-fullscreen');
  if (fs) { fs.title = S.fullscreen ? 'Exit fullscreen' : 'Fullscreen'; fs.classList.toggle('is-active', S.fullscreen); }
}
// ── Open-in-panel control (shared: static/js/handlers/panelOpenControl.js) ──────
// One navigator panel, so every navigator card opens the same panel ('*'). Only the
// shared control follows the panel state — the "Open in canvas" button beside it is a
// different act and keeps its own label.
let _lastOpenSpec = null;
function _syncCards(open) {
  try { window.PanelOpenControl && window.PanelOpenControl.shown('database_navigator', open ? '*' : null); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['database_navigator', {
    open() { openDatabaseNavigatorPanel(_lastOpenSpec || {}); },
    close() { closeDatabaseNavigatorPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

// ── the query card's shared helpers ───────────────────────────────────────────
// Mirrors dbcatalog.service.mask_value: first character kept, the rest hidden. Masking
// is for the CARD only — the agent read the values it needed to answer.
const MASK = '•••';
function _maskCell(v) {
  if (v == null) return v;
  const s = String(v);
  return s.length > 1 ? s[0] + MASK : MASK;
}

/** Open a statement in the SQL canvas, bound to the same Database Connection. The
 *  canvas is the editor: the user edits and re-runs there with its own execute path. */
async function _openInCanvas({ sql, title, connectionId, language }) {
  try {
    const { renderCanvas } = await import('/api/v1/components/canvas/renderer');
    const tmp = document.createElement('div');
    tmp.id = `dbnav-canvas-${Math.random().toString(36).slice(2, 8)}`;
    tmp.style.display = 'none';
    document.body.appendChild(tmp);
    await Promise.resolve(renderCanvas(tmp.id, { component: 'canvas', action: 'open', view: 'canvas',
      title, content: sql, language: language === 'mongodb' ? 'mongodb' : 'sql',
      connection_id: connectionId || null })).finally(() => tmp.remove());
  } catch (e) { console.warn('[dbnav] open in canvas failed', e); }
}

/** ONE result grid for every face that shows rows — the query card, and the SQL canvas's
 *  console (which imports it). `masked_columns` is decided server-side by the same rule the
 *  navigator's preview uses (`dbcatalog.intelligence.masked_columns`); the grid only applies it.
*  `opts.unmask` shows the values client-side (the canvas offers that toggle — the owner ran the
 *  statement); the SERVER is never asked for an unmasked preview. */
function _resultGrid(s, opts = {}) {
  const cols = s.columns || [], rows = s.rows || [];
  const maxCols = opts.maxCols || cols.length, maxRows = opts.maxRows || rows.length;
  const masked = new Set(opts.unmask ? [] : (s.masked_columns || []).map(c => String(c).toLowerCase()));
  const shownCols = cols.slice(0, maxCols), gridRows = rows.slice(0, maxRows);
  if (!cols.length) return '';
  const maxH = opts.maxHeight || '320px';
  return `<div class="dbnav-mini dbnav-grid" style="overflow:auto;max-height:${maxH};"><table class="dbnav-t"><thead><tr>${shownCols.map(c => `<th class="${masked.has(String(c).toLowerCase()) ? 'is-masked' : ''}">${_esc(c)}</th>`).join('')}${cols.length > shownCols.length ? `<th class="muted">+${cols.length - shownCols.length}</th>` : ''}</tr></thead><tbody>
      ${gridRows.map(r => `<tr>${shownCols.map((c, i) => { const v = masked.has(String(c).toLowerCase()) ? _maskCell(r[i]) : r[i]; return `<td class="${typeof r[i] === 'number' ? 'num' : ''} ${v == null ? 'muted' : ''}">${v == null ? 'null' : _esc(v)}</td>`; }).join('')}${cols.length > shownCols.length ? '<td></td>' : ''}</tr>`).join('')}
    </tbody></table></div>`;
}

/** The join clause the Keys & joins tab writes into the canvas: `JOIN <other> ON a.k = b.k`
 *  — declared or inferred, in the direction the row reads. Pure. */
function _joinClause(f, dir) {
  const child = `${f.child_database}.${f.child_table}`, parent = `${f.parent_database}.${f.parent_table}`;
  const other = dir === 'out' ? parent : child;
  const self = dir === 'out' ? child : parent;
  const on = (f.child_columns || []).map((c, i) => `${child}.${c} = ${parent}.${(f.parent_columns || [])[i] || c}`).join(' AND ');
  return { other, self, clause: `JOIN ${other} ON ${on}` };
}

/** Is the SQL canvas open beside us? — the only time drag-to-insert and "Insert join" mean anything. */
function _canvasOpen() {
  const p = document.getElementById('canvas-split-panel');
  return !!(p && p.classList.contains('canvas-split--open'));
}
async function _insertIntoCanvas(text) {
  try {
    const cv = await import('/api/v1/components/canvas/renderer');
    return !!(cv.insertIntoOpenCanvas && cv.insertIntoOpenCanvas(text));
  } catch (_) { return false; }
}
/** Make a row draggable: the canvas's editor (CodeMirror drop cursor) takes `text/plain`. */
function _draggable(el, text) {
  el.setAttribute('draggable', 'true');
  el.addEventListener('dragstart', (e) => { try { e.dataTransfer.setData('text/plain', text); e.dataTransfer.effectAllowed = 'copy'; } catch (_) {} });
}

function _queryCardBody(s) {
  const src = s.source || {};
  const cols = s.columns || [], rows = s.rows || [];
  const masked = new Set((s.masked_columns || []).map(c => String(c).toLowerCase()));
  const gridRows = rows.slice(0, 20);
  const total = s.row_count != null ? s.row_count : rows.length;
  const facts = [`<strong style="color:var(--text-primary)">${_num(total)}${s.truncated && total === rows.length ? '+' : ''}</strong> ${s.language === 'mongodb' ? 'document' : 'row'}${total === 1 ? '' : 's'}`,
    src.label ? `from <strong style="color:var(--text-primary)">${_esc(src.label)}</strong>` : '',
    src.dialect ? `<span class="dbnav-chip">${_esc(src.dialect)}</span>` : '',
    s.statement ? `<span class="dbnav-chip u-tip" data-tip="Figures traced to this result cite statement ${_esc(s.statement)}">statement ${_esc(s.statement)}</span>` : ''].filter(Boolean).join(' ');
  const rewritten = (s.rewrites || []).length && s.original_sql
    ? `<div class="dbnav-note">${I.warn}<span>Rewritten before it ran — the statement below is what executed. The agent wrote: <span class="mono">${_esc(s.original_sql)}</span></span></div>` : '';
  const maskNote = masked.size ? `<div class="dbnav-note">${I.shield}<span>Personal data masked in this view: ${_esc([...masked].join(', '))}.</span></div>` : '';
  const grid = _resultGrid(s, { maxCols: 10, maxRows: 20 });
  const unit = s.language === 'mongodb' ? 'document' : 'row';
  const more = total > gridRows.length
    ? `<div class="dbnav-sub">Showing ${gridRows.length} of ${_num(total)} ${unit}s${s.shown != null && s.shown !== gridRows.length ? ` · the agent read ${_num(s.shown)}` : ''}${s.truncated ? (s.language === 'mongodb' ? ' · the result was bounded — narrow it or aggregate with $group' : ' · the result was bounded — narrow it or aggregate in SQL') : ''}.</div>` : '';
  return `<div class="dbnav-card-m">${facts}</div>${rewritten}
    <div class="dbnav-pre" style="margin-top:6px;max-height:140px;overflow:auto;">${_esc(s.sql || '')}</div>
    ${maskNote}${grid}${more}`;
}

// ── inline chat card ──────────────────────────────────────────────────────────
export function renderDatabaseNavigator(containerId, payload) {
  _css();
  const container = document.getElementById(containerId); if (!container) return;
  let p = payload; if (typeof p === 'string') { try { p = JSON.parse(p); } catch (_) { p = {}; } }
  const s = (p && p.spec) ? p.spec : (p || {});
  const action = s.action || 'describe';
  const srcLabel = s.source ? `${_esc(s.source.label)} · ${s.source.kind === 'direct' ? 'connection' : 'MCP'}` : '';
  let bodyHtml = '', openSpec = { database: s.database || null, object: s.object || null, source: s.source ? s.source.id : null };
  if (s.error && action === 'query') {
    bodyHtml = `<div class="dbnav-card-m">${I.warn.replace('<svg', '<svg style="width:12px;height:12px;vertical-align:-2px;margin-right:4px;color:var(--status-warning,#d97706)"')}${_esc(s.message || 'The query did not run.')}</div>${s.sql ? `<div class="dbnav-pre" style="margin-top:6px;max-height:140px;overflow:auto;">${_esc(s.sql)}</div>` : ''}`;
  } else if (action === 'query') {
    bodyHtml = _queryCardBody(s);
  } else if (s.error) {
    bodyHtml = `<div class="dbnav-card-m">${I.warn.replace('<svg', '<svg style="width:12px;height:12px;vertical-align:-2px;margin-right:4px;color:var(--status-warning,#d97706)"')}${_esc(s.message || 'The navigator could not answer.')}</div>`;
  } else if (action === 'describe' && s.detail) {
    const d = s.detail, intel = d.intelligence || {};
    const chips = ['key', 'pii', 'text_date', 'measure', 'code'].map(f => (intel.flag_counts || {})[f] ? `<span class="dbnav-chip f-${f}">${(intel.flag_counts || {})[f]} ${f.replace('_', ' ')}</span>` : '').join(' ');
    bodyHtml = `<div class="dbnav-card-m"><strong style="color:var(--text-primary)">${_esc(d.info.qualified)}</strong> — <span class="dbnav-chip role">${_esc(intel.role_label || d.info.kind)}</span> ${d.columns.length} columns${d.info.row_count != null ? ` · ${_num(d.info.row_count)} rows${d.info.row_count_source === 'statistics' ? ' (stats)' : ''}` : ''}${d.foreign_keys_out.length ? ` · references ${d.foreign_keys_out.map(f => f.parent_table).join(', ')}` : ''}${intel.scope === 'out_of_scope' ? ` <span class="dbnav-chip scope-out">outside trusted scope</span>` : ''}</div>
      <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px;">${chips}</div>
      <div class="dbnav-mini"><table class="dbnav-t"><tbody>${d.columns.slice(0, 8).map(c => `<tr><td class="mono">${_esc(c.name)}</td><td class="mono muted">${_esc(c.data_type || '')}</td><td>${_flagChips(c.flags, c.ontology)}</td></tr>`).join('')}${d.columns.length > 8 ? `<tr><td colspan="3" class="muted">+${d.columns.length - 8} more columns in the navigator</td></tr>` : ''}</tbody></table></div>`;
  } else if (action === 'search') {
    const hits = s.hits || [];
    bodyHtml = `<div class="dbnav-card-m">${hits.length ? `${hits.length} match${hits.length === 1 ? '' : 'es'} for <strong style="color:var(--text-primary)">${_esc(s.query)}</strong>` : `Nothing named like <strong style="color:var(--text-primary)">${_esc(s.query)}</strong>`}</div>
      <div class="dbnav-mini">${hits.slice(0, 8).map(h => `<div class="dbnav-hit" data-db="${_esc(h.database)}" data-name="${_esc(h.object || '')}">${h.kind === 'column' ? I.col : h.kind === 'database' ? I.db : _kindIcon(h.kind)}<span>${_esc(h.kind === 'column' ? h.column : (h.object || h.database))}</span><span class="path">${_esc(h.kind === 'column' ? `${h.database}.${h.object}` : (h.kind === 'database' ? '' : h.database))}</span></div>`).join('')}</div>`;
  } else if (action === 'preview' && s.preview) {
    const pv = s.preview;
    bodyHtml = `<div class="dbnav-card-m">${pv.rows.length} sample row${pv.rows.length === 1 ? '' : 's'} from <strong style="color:var(--text-primary)">${_esc(s.database)}.${_esc(s.object)}</strong>${pv.masked_columns && pv.masked_columns.length ? ` · masked: ${_esc(pv.masked_columns.join(', '))}` : ''}</div>
      <div class="dbnav-mini"><table class="dbnav-t"><thead><tr>${pv.columns.slice(0, 8).map(c => `<th>${_esc(c)}</th>`).join('')}</tr></thead><tbody>${pv.rows.slice(0, 5).map(r => `<tr>${r.slice(0, 8).map(v => `<td class="${v == null ? 'muted' : ''}">${v == null ? 'null' : _esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  } else if (action === 'suggest_joins') {
    const rows = [...(s.declared_out || []).map(f => `${f.child_table}.${f.child_columns.join(',')} → ${f.parent_table}.${f.parent_columns.join(',')} <span class="dbnav-chip scope-in">declared</span>`),
      ...(s.declared_in || []).map(f => `${f.child_table}.${f.child_columns.join(',')} → ${f.parent_table}.${f.parent_columns.join(',')} <span class="dbnav-chip scope-in">declared</span>`),
      ...(s.inferred || []).slice(0, 6).map(j => `${j.child_table}.${j.child_columns.join(',')} → ${j.parent_table}.${j.parent_columns.join(',')} <span class="dbnav-chip">inferred · ${Math.round(j.confidence * 100)}%</span>`)];
    bodyHtml = `<div class="dbnav-card-m">Join paths for <strong style="color:var(--text-primary)">${_esc(s.database)}.${_esc(s.object)}</strong></div><div class="dbnav-mini">${rows.length ? rows.map(r => `<div class="dbnav-sub mono">${r}</div>`).join('') : '<div class="dbnav-sub">none declared; describe sibling tables to surface name-based candidates</div>'}</div>`;
  } else {
    bodyHtml = `<div class="dbnav-card-m">${_esc(s.message || 'Opened the Database Navigator.')}</div>`;
  }
  const canvasId = `dbnav-cv-${Math.random().toString(36).slice(2, 8)}`;
  // "Open in canvas": a query result whose statement ran (or failed) against a direct connection
  const canCanvas = action === 'query' && s.sql && s.source && s.source.connection_id;
  container.innerHTML = `<div data-component-id="database_navigator" class="dbnav-card">
      <div class="dbnav-card-h">${I.db}<span class="dbnav-card-t">${action === 'query' ? 'Query' : 'Database Navigator'}</span>${srcLabel ? `<span class="dbnav-chip" style="margin-left:auto">${srcLabel}</span>` : ''}</div>
      ${bodyHtml}
      <div class="dbnav-card-acts">
        ${canCanvas ? `<button type="button" id="${canvasId}" class="dbnav-canvas-btn u-tip" data-tip="Open this statement in the SQL canvas, bound to the same connection — edit and re-run it there">${I.code.replace('<svg', '<svg style="width:12px;height:12px;vertical-align:-2px;margin-right:4px"')}Open in canvas →</button>` : ''}
        ${window.PanelOpenControl ? window.PanelOpenControl.html({ component: 'database_navigator', id: '*', accent: '#84cc16', panelId: 'dbnav-split-panel', name: 'Database Navigator', row: false }) : ''}
      </div></div>`;
  if (canCanvas) {
    document.getElementById(canvasId)?.addEventListener('click', () => _openInCanvas({
      sql: s.sql, title: `Query · ${s.source.label || 'database'}${s.statement ? ` · statement ${s.statement}` : ''}`,
      connectionId: s.source.connection_id, language: s.language || s.source.language || 'sql' }));
  }
  container.querySelectorAll('.dbnav-hit').forEach(el => el.onclick = () => openDatabaseNavigatorPanel({ database: el.dataset.db, object: el.dataset.name || null, source: openSpec.source }));
  _lastOpenSpec = openSpec;
  // Auto-open — never while an old answer is replayed on a session reload (the
  // session's own restore decides whether the panel was open).
  if ((action === 'open' || (window.__canvasSplitMode && !s.error && action !== 'query'))
      && !window.__suppressComponentReplayOpen) setTimeout(() => openDatabaseNavigatorPanel(openSpec), 80);
}


// exposed for gates (executed under a DOM stub) — never for other modules
if (typeof window !== 'undefined') window.__dbnavS = S;
export const _internal = { S, srcLanguage: _srcLanguage, maskCell: _maskCell, queryCardBody: _queryCardBody, resultGrid: _resultGrid, joinClause: _joinClause, css: _css, canvasOpen: _canvasOpen, srcConnectionId: _srcConnectionId, esc: _esc, hl: _hl, num: _num, flagChips: _flagChips, renderDetail: _renderDetail, renderTree: _renderTree, renderHits: _renderHits, renderPreview: _renderPreview, kindIcon: _kindIcon };
