/**
 * Agent Memory component renderer (TDA_Memory).
 * Theme-aware (CSS variables), monochrome inline SVG icons, no emoji.
 *  - renderMemory(): the compact inline chat card (rarely shown — inline render is
 *    suppressed for memory; the prose answer + canvas convey the result).
 *  - openMemorySplitPanel(): the read/write split-screen manager (world-class).
 */

// Unique panel identity accent (teal) — distinct from every other component
// (canvas cyan, KG indigo, scheduler fuchsia, dashboard pink, flow amber) and from
// the conversation/Ideate green. Drives --sp-accent + the card/canvas internals.
const ACCENT_HEX = '#14b8a6';                       // manifest icon_accent — the open control's fallback
const ACCENT = 'var(--sp-accent, #14b8a6)';          // the live component accent everywhere else

const ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"/></svg>';

function _esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function _kindBadge(kind) {
  return `<span style="font-size:0.65rem;text-transform:uppercase;letter-spacing:0.04em;`
    + `padding:1px 6px;border-radius:6px;color:${ACCENT};`
    + `background:color-mix(in srgb,${ACCENT} 12%,transparent);border:1px solid color-mix(in srgb,${ACCENT} 28%,transparent);">${_esc(kind || 'fact')}</span>`;
}

function _itemRow(it) {
  const scope = it.profile_id ? 'profile' : 'global';
  return `<li style="display:flex;align-items:flex-start;gap:0.5rem;padding:0.4rem 0;`
    + `border-bottom:1px solid var(--border-secondary,rgba(148,163,184,0.15));">`
    + `<span style="flex:1;color:var(--text-primary,#e2e8f0);font-size:0.85rem;">${_esc(it.content)}</span>`
    + `${_kindBadge(it.kind)}`
    + `<span style="font-size:0.62rem;color:var(--text-muted,#94a3b8);">${scope}</span></li>`;
}

// Inline chat card — same experience as the other component cards (chart/dashboard/
// flow/scheduler): a compact high-level summary with a "View in Canvas →" toggle that
// opens the Agent Memory split canvas, and auto-opens it when canvas split mode is on.
export function renderMemory(containerId, payload) {
  const container = document.getElementById(containerId);
  if (!container) return;
  // ui.js passes the inline container's data-spec as a RAW STRING — parse it
  // (mirrors renderScheduler/renderDashboard). Without this the spec fields were
  // all undefined → an empty card.
  let p = payload;
  if (typeof p === 'string') { try { p = JSON.parse(p); } catch (_) { p = {}; } }
  const s = (p && p.spec) ? p.spec : (p || {});
  const action = s.action || 'remember';
  const msg = s.message || '';

  // Which owner the canvas opens to + a high-level facts summary.
  let openSpec = { key: 'global' };
  let facts = '';
  const FACT = 'color:var(--text-primary,#e2e8f0);font-size:0.84rem;line-height:1.45;';
  const TAG = 'font-size:0.62rem;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-muted,#94a3b8);margin-top:0.2rem;';
  if (action === 'remember' && s.item) {
    const it = s.item;
    openSpec = it.profile_id ? { key: it.profile_id, profile_id: it.profile_id } : { key: 'global' };
    facts = `<div style="${FACT}">"${_esc(it.content)}"</div>`
      + `<div style="${TAG}">${it.profile_id ? 'This agent' : 'Global'}${it.kind ? ' · ' + _esc(it.kind) : ''}</div>`;
  } else if (action === 'list_memories') {
    const items = Array.isArray(s.items) ? s.items : [];
    facts = items.slice(0, 3).map(it => `<div style="${FACT}">• ${_esc(it.content)}</div>`).join('')
      + (items.length > 3 ? `<div style="${TAG}">+${items.length - 3} more</div>` : '');
  }

  // Stat chips (total / global / local + kind breakdown) — makes the card informative.
  // Prefer server stats; fall back to deriving counts from the items in the spec.
  let st = s.stats;
  if (!st && Array.isArray(s.items)) {
    const g = s.items.filter(i => !i.profile_id).length;
    st = { total: s.items.length, global: g, local: s.items.length - g, kinds: {} };
  }
  st = st || {};
  const chip = (label, val, color) => `<span style="display:inline-flex;align-items:baseline;gap:0.28rem;
      font-size:0.66rem;color:var(--text-muted,#94a3b8);padding:2px 9px;border-radius:999px;
      background:var(--bg-overlay,rgba(15,23,42,0.3));border:1px solid var(--border-secondary,rgba(148,163,184,0.18));">
      <strong style="color:${color || 'var(--text-primary,#e2e8f0)'};font-variant-numeric:tabular-nums;font-size:0.74rem;">${val}</strong>${label}</span>`;
  let stats = '';
  if (st.total != null) {
    const k = st.kinds || {};
    const kindStr = ['preference', 'fact', 'style'].filter(x => k[x]).map(x => `${k[x]} ${x}`).join(' · ');
    const local = (st.local != null) ? st.local : Math.max(0, (st.total || 0) - (st.global || 0));
    stats = `<div style="display:flex;flex-wrap:wrap;gap:0.35rem;margin-top:0.6rem;">
        ${chip('total', st.total, ACCENT)}${chip('global', st.global || 0, '#3b82f6')}${chip('local', local, '#a855f7')}</div>`
      + (kindStr ? `<div style="${TAG};letter-spacing:0;text-transform:none;">${_esc(kindStr)}</div>` : '');
  }

  container.innerHTML = `
    <div data-component-id="memory" style="border:1px solid color-mix(in srgb,${ACCENT} 18%,transparent);
        border-left:3px solid ${ACCENT};border-radius:12px;padding:0.7rem 0.85rem;
        background:color-mix(in srgb,${ACCENT} 4%,transparent);max-width:540px;">
      <div data-ans-restates style="display:flex;align-items:center;gap:0.5rem;color:${ACCENT};">
        ${ICON}<span style="font-weight:600;font-size:0.8rem;color:var(--text-primary,#e2e8f0);">Agent Memory</span>
      </div>
      <div style="color:var(--text-muted,#94a3b8);font-size:0.82rem;margin-top:0.3rem;">${_esc(msg)}</div>
      ${facts ? `<div style="margin-top:0.5rem;display:flex;flex-direction:column;gap:0.15rem;">${facts}</div>` : ''}
      ${stats}
      <div style="margin-top:0.65rem;">${_memOpenControlHtml()}</div>
    </div>`;

  _lastOpenSpec = openSpec;
  // Auto-open when canvas split mode is on (mirrors flow/dashboard/scheduler) — never
  // while an old answer is replayed on a session reload (the session's own restore
  // decides whether the panel was open).
  if (window.__canvasSplitMode && !window.__suppressComponentReplayOpen) {
    setTimeout(() => openMemorySplitPanel(openSpec), 80);
  }
}

// ── Open-in-panel control (shared: static/js/handlers/panelOpenControl.js) ──────
// One memory manager panel, so every memory card opens the same panel ('*').
let _lastOpenSpec = null;
function _memOpenControlHtml() {
  const poc = window.PanelOpenControl;
  if (!poc) return '';
  return poc.html({ component: 'memory', id: '*', accent: ACCENT_HEX,
                    panelId: 'memory-split-panel', name: 'Agent Memory', row: false });
}
function _syncMemCards(open) {
  try { window.PanelOpenControl && window.PanelOpenControl.shown('memory', open ? '*' : null); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['memory', {
    open() { openMemorySplitPanel(_lastOpenSpec || {}); },
    close() { closeMemorySplitPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

export default renderMemory;

/* ════════════════════════════════════════════════════════════════════════
 *  Agent Memory split-screen MANAGER (read/write). Two-pane: owner rail
 *  (Global + every agent) | item manager. Designed to match the platform's
 *  other component panels — restrained, theme-aware, hover-revealed controls.
 * ════════════════════════════════════════════════════════════════════════ */

const IFOC_COLORS = {
  llm_only: '#4ade80', rag_focused: '#3b82f6', tool_enabled: '#9333ea', genie: '#F15F22',
};
function _ownerColor(o) {
  if (!o || o.key === 'global') return ACCENT;
  return o.color || IFOC_COLORS[o.profile_type] || '#94a3b8';
}
// Subtle per-kind tints for the kind chip.
const KIND_TINT = {
  preference: { c: '#0ea5e9', b: 'rgba(14,165,233,0.16)' },
  fact:       { c: '#94a3b8', b: 'rgba(148,163,184,0.16)' },
  style:      { c: '#a855f7', b: 'rgba(168,85,247,0.16)' },
};

const SVG = {
  edit: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M10 4H7v16h3zM17 4h-3v16h3z"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4l14 8-14 8V4z"/></svg>',
  move: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3l4 4-4 4M20 7H9a4 4 0 00-4 4v0M8 21l-4-4 4-4M4 17h11a4 4 0 004-4v0"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m2 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
};

let _selectedOwnerKey = 'global';
let _activeProfileId = null;
let _owners = [];
let _allItems = [];
let _filter = { q: '', kind: 'all' };
let _sort = 'recent';
let _selection = new Set();
let _preview = null;
let _isFullscreen = false;
let _followOff = null;

function _tok() { return localStorage.getItem('tda_auth_token'); }
function _hdrs() { return { 'Authorization': `Bearer ${_tok()}`, 'Content-Type': 'application/json' }; }
async function _memApi(path, opts = {}) {
  const res = await fetch(`/api/v1${path}`, { ...opts, headers: { ..._hdrs(), ...(opts.headers || {}) } });
  if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
  return res.json().catch(() => ({}));
}
function _timeAgo(iso) {
  if (!iso) return '';
  try {
    const d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (d < 60) return 'just now';
    if (d < 3600) return `${Math.floor(d / 60)}m ago`;
    if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
    return `${Math.floor(d / 86400)}d ago`;
  } catch { return ''; }
}

function _injectMemSplitCSS() {
  if (document.getElementById('mem-split-css-v2')) return;
  document.getElementById('mem-split-css')?.remove();
  const s = document.createElement('style');
  s.id = 'mem-split-css-v2';
  s.textContent = `
#memory-split-panel { width:0; min-width:0; max-width:720px; flex-shrink:0; display:flex;
  flex-direction:column; height:100%; overflow:hidden; background:var(--card-bg,rgba(20,22,25,0.6));
  border-left:1px solid var(--border-primary,rgba(255,255,255,0.1)); transition:width .3s ease, min-width .3s ease; }
#memory-split-panel.mem-split--open { width:44%; min-width:360px; }
.mem-fullscreen #memory-split-panel.mem-split--open { position:fixed !important;
  top:var(--mem-fullscreen-top,0px); left:0 !important; right:0 !important; bottom:0 !important;
  width:auto !important; max-width:none; z-index:50; }
.mem-split-body { flex:1; display:flex; flex-direction:row; min-height:0; overflow:hidden; }

/* ── Owner rail ─────────────────────────────────────────── */
.mem-rail { width:166px; flex-shrink:0; overflow-y:auto; padding:0.85rem 0.55rem; display:flex;
  flex-direction:column; gap:1px; border-right:1px solid var(--border-secondary,rgba(148,163,184,0.14)); }
.mem-rail-label { font-size:0.6rem; font-weight:600; letter-spacing:0.09em; text-transform:uppercase;
  color:var(--text-muted,#94a3b8); padding:0 0.55rem 0.5rem; }
.mem-owner { display:flex; align-items:center; gap:0.5rem; padding:0.42rem 0.55rem; border-radius:8px;
  cursor:pointer; font-size:0.82rem; color:var(--text-primary,#e2e8f0); position:relative;
  transition:background var(--duration-fast,.15s) ease; }
.mem-owner:hover { background:var(--hover-bg-strong,rgba(148,163,184,0.10)); }
.mem-owner.active { background:color-mix(in srgb,var(--sp-accent,#14b8a6) 14%,transparent); }
.mem-owner.active::before { content:''; position:absolute; left:0; top:6px; bottom:6px; width:2.5px;
  border-radius:2px; background:var(--sp-accent,#14b8a6); }
.mem-odot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.mem-onm { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mem-oct { font-size:0.68rem; color:var(--text-muted,#94a3b8); font-variant-numeric:tabular-nums; }
.mem-you { font-size:0.54rem; font-weight:700; letter-spacing:0.04em; text-transform:uppercase;
  color:var(--sp-accent,#14b8a6); }

/* ── Item pane ──────────────────────────────────────────── */
.mem-pane { flex:1; overflow-y:auto; min-width:0; }
.mem-pane-in { max-width:660px; margin:0 auto; padding:0.95rem 1.15rem 1.5rem; }
.mem-toolbar { display:flex; gap:0.5rem; margin-bottom:0.6rem; }
.mem-field { background:var(--bg-overlay,rgba(15,23,42,0.35)); border:1px solid var(--border-primary,rgba(148,163,184,0.22));
  border-radius:10px; padding:0 0.75rem; height:36px; color:var(--text-primary,#e2e8f0); font-size:0.84rem;
  outline:none; transition:border-color .15s ease, box-shadow .15s ease; }
.mem-field:focus { border-color:color-mix(in srgb,var(--sp-accent,#14b8a6) 55%,transparent);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--sp-accent,#14b8a6) 12%,transparent); }
select.mem-field { cursor:pointer; padding-right:0.4rem; }
.mem-search-wrap { flex:1; position:relative; display:flex; align-items:center; }
.mem-search-wrap > svg { position:absolute; left:0.7rem; color:var(--text-muted,#94a3b8); pointer-events:none; }
.mem-search-wrap .mem-field { width:100%; padding-left:2rem; }
/* Filter + sort as pill/tag groups (no dropdowns) */
.mem-filterbar { display:flex; align-items:center; gap:0.5rem; margin:0.55rem 0 1rem; flex-wrap:wrap; }
.mem-pills { display:inline-flex; gap:0.3rem; flex-wrap:wrap; }
.mem-pills.mem-sort { margin-left:auto; }
.mem-pill { font-size:0.7rem; font-weight:600; padding:4px 11px; border-radius:999px; cursor:pointer; white-space:nowrap;
  color:var(--text-muted,#94a3b8); background:transparent; border:1px solid var(--border-secondary,rgba(148,163,184,0.22));
  transition:color .15s, background .15s, border-color .15s; }
.mem-pill:hover { color:var(--text-primary,#e2e8f0); border-color:var(--border-primary,rgba(148,163,184,0.4)); }
.mem-pill.active { color:var(--sp-accent,#14b8a6); background:color-mix(in srgb,var(--sp-accent,#14b8a6) 14%,transparent);
  border-color:color-mix(in srgb,var(--sp-accent,#14b8a6) 45%,transparent); }
.mem-pills.mem-sort .mem-pill { font-size:0.66rem; padding:3px 9px; }

.mem-card { position:relative; border:1px solid var(--border-secondary,rgba(148,163,184,0.16)); border-radius:12px;
  padding:0.7rem 0.85rem; margin-bottom:0.5rem; background:var(--bg-overlay,rgba(15,23,42,0.22));
  transition:border-color .15s ease, background .15s ease; }
.mem-card:hover { border-color:var(--border-primary,rgba(148,163,184,0.3)); }
.mem-card.sel { border-color:var(--sp-accent,#14b8a6); box-shadow:0 0 0 1px color-mix(in srgb,var(--sp-accent,#14b8a6) 40%,transparent); }
.mem-card.inactive { opacity:0.5; }
.mem-txt { color:var(--text-primary,#e2e8f0); font-size:0.88rem; line-height:1.5; padding-right:5.5rem; }
.mem-meta { display:flex; align-items:center; gap:0.55rem; margin-top:0.5rem; flex-wrap:wrap; }
.mem-kind { border:1px solid transparent; border-radius:999px; cursor:pointer; font-size:0.6rem; font-weight:700;
  text-transform:uppercase; letter-spacing:0.05em; padding:2px 10px; line-height:1.6; transition:filter .15s ease; }
.mem-kind:hover { filter:brightness(1.18); }
.mem-prov { font-size:0.66rem; color:var(--text-muted,#94a3b8); }
.mem-tag { font-size:0.56rem; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;
  color:var(--text-muted,#94a3b8); border:1px solid var(--border-secondary,rgba(148,163,184,0.25)); border-radius:5px; padding:1px 5px; }
.mem-actions { position:absolute; top:0.55rem; right:0.6rem; display:flex; gap:1px;
  opacity:0; transition:opacity .15s ease; }
.mem-card:hover .mem-actions, .mem-card.sel .mem-actions { opacity:1; }
.mem-actbtn { background:none; border:none; cursor:pointer; color:var(--text-muted,#94a3b8);
  padding:5px; border-radius:7px; display:flex; transition:color .15s, background .15s; }
.mem-actbtn:hover { color:var(--text-primary,#e2e8f0); background:var(--hover-bg-strong,rgba(148,163,184,0.14)); }
.mem-actbtn.danger:hover { color:#ef4444; background:rgba(239,68,68,0.12); }
.mem-check { position:absolute; left:0.55rem; top:0.6rem; opacity:0; transition:opacity .15s ease; cursor:pointer; }
.mem-card:hover .mem-check, .mem-card.sel .mem-check { opacity:0.9; }
.mem-card:has(.mem-check:checked) .mem-txt { padding-left:1.4rem; }
.mem-move-row { margin-top:0.55rem; }

.mem-composer { display:flex; gap:0.5rem; margin-top:1rem; }
.mem-composer .mem-field { flex:1; }
.mem-add { background:color-mix(in srgb,var(--sp-accent,#14b8a6) 16%,transparent); color:var(--sp-accent,#14b8a6);
  border:1px solid color-mix(in srgb,var(--sp-accent,#14b8a6) 35%,transparent); border-radius:9px; height:34px;
  padding:0 0.95rem; font-size:0.82rem; font-weight:600; cursor:pointer; transition:background .15s; }
.mem-add:hover { background:color-mix(in srgb,var(--sp-accent,#14b8a6) 26%,transparent); }
.mem-foot { display:flex; justify-content:flex-end; margin-top:0.7rem; }
.mem-link { background:none; border:none; color:var(--text-muted,#94a3b8); font-size:0.72rem; cursor:pointer; }
.mem-link:hover { color:#ef4444; }

.mem-empty { text-align:center; color:var(--text-muted,#94a3b8); padding:2.6rem 1rem; }
.mem-empty svg { width:30px; height:30px; opacity:0.4; margin:0 auto 0.6rem; }
.mem-empty .h { font-size:0.86rem; color:var(--text-primary,#e2e8f0); margin-bottom:0.2rem; }
.mem-empty .s { font-size:0.76rem; }

.mem-bulk { display:flex; align-items:center; gap:0.5rem; margin-bottom:0.7rem; padding:0.45rem 0.7rem;
  border-radius:9px; background:color-mix(in srgb,var(--sp-accent,#14b8a6) 12%,transparent); font-size:0.76rem; color:var(--text-primary,#e2e8f0); }
.mem-bulk button { background:none; border:none; cursor:pointer; font-size:0.74rem; color:var(--text-muted,#94a3b8); }
.mem-bulk button.danger { color:#ef4444; }

.mem-pvbar { display:flex; align-items:center; gap:0.5rem; margin-bottom:0.85rem; padding:0.5rem 0.75rem;
  border:1px solid color-mix(in srgb,var(--sp-accent,#14b8a6) 30%,transparent); border-radius:10px;
  background:color-mix(in srgb,var(--sp-accent,#14b8a6) 8%,transparent); font-size:0.78rem; color:var(--text-primary,#e2e8f0); }
.mem-pvbar .x { margin-left:auto; cursor:pointer; color:var(--text-muted,#94a3b8); background:none; border:none; font-size:0.74rem; }
.mem-rank { flex-shrink:0; width:1.5rem; font-size:0.7rem; font-weight:700; color:var(--sp-accent,#14b8a6); font-variant-numeric:tabular-nums; }
.mem-pvrow { display:flex; align-items:center; gap:0.5rem; padding:0.55rem 0; border-bottom:1px solid var(--border-secondary,rgba(148,163,184,0.14)); }
.mem-pvrow .t { color:var(--text-primary,#e2e8f0); font-size:0.85rem; }`;
  document.head.appendChild(s);
}

function _ownerById(key) { return _owners.find(o => o.key === key) || null; }
function _selectedOwner() { return _ownerById(_selectedOwnerKey) || _owners[0] || null; }

export async function openMemorySplitPanel(spec) {
  _injectMemSplitCSS();
  const panel = document.getElementById('memory-split-panel');
  if (!panel) return;

  _activeProfileId = (spec && spec.profile_id) || _activeProfileId;
  if (!_activeProfileId) {
    try { const m = await import('/static/js/handlers/nextStepHandler.js');
          _activeProfileId = m.getEffectiveProfileId ? m.getEffectiveProfileId() : null; } catch (_) {}
  }
  if (spec && spec.key === 'global') _selectedOwnerKey = 'global';
  else if (spec && spec.profile_id) _selectedOwnerKey = spec.profile_id;
  else _selectedOwnerKey = _activeProfileId || 'global';
  _preview = null; _selection.clear();

  // --sp-accent (teal) is applied to the aside from the manifest icon_accent by
  // componentHandler.applyComponentPanelAccents() at startup — same as every other
  // panel. No hardcoding here.
  panel.style.display = 'flex';
  panel.offsetHeight;
  panel.classList.add('mem-split--open');
  _syncMemCards(true);
  document.getElementById('memory-split-close').onclick = closeMemorySplitPanel;
  const fsBtn = document.getElementById('memory-split-fullscreen');
  if (fsBtn) fsBtn.onclick = _toggleFullscreen;

  if (!_followOff) {
    // Component signals: the panel follows the memory store itself — an item the turn's extraction
    // wrote, a manual add, an erase from another tab (components/signals.py).
    _followOff = UderiaBus.onComponent('memory', () => {
      const p = document.getElementById('memory-split-panel');
      if (!p || !p.classList.contains('mem-split--open') || _preview) return;
      _loadAndRender();
    });
  }
  await _loadAndRender();
}

export function closeMemorySplitPanel() {
  const panel = document.getElementById('memory-split-panel');
  if (!panel) return;
  if (_isFullscreen) _toggleFullscreen();
  if (_followOff) { _followOff(); _followOff = null; }

  panel.classList.remove('mem-split--open');
  _syncMemCards(false);
  // The split resizer freezes open panels to an explicit inline width — clear it so the
  // CSS collapse (width:0) actually runs and the panel truly closes.
  panel.style.width = ''; panel.style.minWidth = ''; panel.style.maxWidth = ''; panel.style.flex = '';

  let hidden = false;
  const finish = () => {
    if (hidden) return;
    hidden = true;
    panel.removeEventListener('transitionend', onEnd);
    if (!panel.classList.contains('mem-split--open')) {
      panel.style.display = 'none';
      const b = document.getElementById('memory-split-content');
      if (b) b.innerHTML = '';
    }
    try { window.SplitPanelResizer?.update(); } catch (_) {}
  };
  const onEnd = () => finish();
  panel.addEventListener('transitionend', onEnd);
  setTimeout(finish, 400);  // fallback if transitionend never fires
}

function _toggleFullscreen() {
  const mainArea = document.getElementById('main-content-area');
  if (!mainArea) return;
  _isFullscreen = !_isFullscreen;
  if (_isFullscreen) {
    const topNav = document.querySelector('body > nav');
    document.documentElement.style.setProperty('--mem-fullscreen-top', (topNav ? topNav.offsetHeight : 0) + 'px');
    mainArea.classList.add('mem-fullscreen');
  } else {
    mainArea.classList.remove('mem-fullscreen');
    document.documentElement.style.removeProperty('--mem-fullscreen-top');
  }
  const fsBtn = document.getElementById('memory-split-fullscreen');
  if (fsBtn) {
    fsBtn.title = _isFullscreen ? 'Exit fullscreen' : 'Fullscreen';
    fsBtn.classList.toggle('is-active', _isFullscreen);  // title-bar activation (mirrors canvas)
  }
}

async function _loadAndRender() {
  try {
    const [ownersResp, itemsResp] = await Promise.all([
      _memApi(`/memory/owners?active_profile_id=${encodeURIComponent(_activeProfileId || '')}`),
      _memApi('/memory'),
    ]);
    _owners = ownersResp.owners || [];
    _allItems = itemsResp.items || [];
  } catch (e) {
    const body = document.getElementById('memory-split-content');
    if (body) body.innerHTML = `<div class="mem-empty"><div class="s">Could not load memory.</div></div>`;
    return;
  }
  if (!_ownerById(_selectedOwnerKey)) _selectedOwnerKey = (_owners[0] && _owners[0].key) || 'global';
  _render();
}

function _render() {
  const body = document.getElementById('memory-split-content');
  const titleEl = document.getElementById('memory-split-title');
  if (!body) return;
  const owner = _selectedOwner();
  if (titleEl) titleEl.textContent = owner && owner.key !== 'global'
    ? `Agent Memory · @${owner.label}` : 'Agent Memory · Global';
  body.innerHTML = `<div class="mem-rail">${_railHtml()}</div>`
    + `<div class="mem-pane"><div class="mem-pane-in">${_paneHtml()}</div></div>`;
  _wire(body);
}

function _railHtml() {
  const rows = _owners.map(o => `
    <div class="mem-owner ${o.key === _selectedOwnerKey ? 'active' : ''}" data-owner="${_esc(o.key)}">
      <span class="mem-odot" style="background:${_ownerColor(o)}"></span>
      <span class="mem-onm">${o.key === 'global' ? 'Global' : '@' + _esc(o.label)}</span>
      ${o.is_active ? '<span class="mem-you">you</span>' : ''}
      <span class="mem-oct">${o.count}</span>
    </div>`).join('');
  return `<div class="mem-rail-label">Owners</div>${rows}`;
}

function _ownerItems() {
  const owner = _selectedOwner();
  const pid = owner && owner.key !== 'global' ? owner.profile_id : null;
  let items = _allItems.filter(i => (owner && owner.key === 'global') ? !i.profile_id : i.profile_id === pid);
  if (_filter.q) { const q = _filter.q.toLowerCase(); items = items.filter(i => (i.content || '').toLowerCase().includes(q)); }
  if (_filter.kind !== 'all') items = items.filter(i => i.kind === _filter.kind);
  const cmp = {
    recent: (a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''),
    recalled: (a, b) => (b.recall_count || 0) - (a.recall_count || 0),
    confidence: (a, b) => (b.confidence || 0) - (a.confidence || 0),
  }[_sort];
  return items.sort(cmp);
}

function _kindChip(it) {
  const t = KIND_TINT[it.kind] || KIND_TINT.fact;
  return `<button class="mem-kind" data-id="${_esc(it.id)}" data-kind="${_esc(it.kind)}" title="Click to change kind"
    style="color:${t.c};background:${t.b};border-color:color-mix(in srgb,${t.c} 30%,transparent);">${_esc(it.kind)}</button>`;
}

function _itemCard(it, rank) {
  const prov = [
    it.source,
    `used ${it.recall_count || 0}×`,
    it.last_recalled_at ? `recalled ${_timeAgo(it.last_recalled_at)}` : `added ${_timeAgo(it.created_at)}`,
  ].filter(Boolean).join('  ·  ');
  const moveTargets = _owners.filter(o => o.key !== _selectedOwnerKey)
    .map(o => `<option value="${_esc(o.key)}">${o.key === 'global' ? 'Move to Global' : 'Move to @' + _esc(o.label)}</option>`).join('');

  if (rank) {  // preview mode — read-only, ranked
    return `<div class="mem-pvrow"><span class="mem-rank">#${rank}</span>
      <span class="t">${_esc(it.content)}</span>${_kindBadge(it.kind)}</div>`;
  }
  return `<div class="mem-card ${it.active ? '' : 'inactive'} ${_selection.has(it.id) ? 'sel' : ''}" data-id="${_esc(it.id)}">
    <input type="checkbox" class="mem-check" data-id="${_esc(it.id)}" ${_selection.has(it.id) ? 'checked' : ''}>
    <div class="mem-actions">
      <button class="mem-actbtn mem-edit" data-id="${_esc(it.id)}" title="Edit">${SVG.edit}</button>
      <button class="mem-actbtn mem-toggle" data-id="${_esc(it.id)}" title="${it.active ? 'Pause' : 'Activate'}">${it.active ? SVG.pause : SVG.play}</button>
      <button class="mem-actbtn mem-move" data-id="${_esc(it.id)}" title="Move / promote">${SVG.move}</button>
      <button class="mem-actbtn danger mem-del" data-id="${_esc(it.id)}" title="Delete">${SVG.trash}</button>
    </div>
    <div class="mem-txt">${_esc(it.content)}</div>
    <div class="mem-meta">${_kindChip(it)}${it.active ? '' : '<span class="mem-tag">paused</span>'}<span class="mem-prov">${_esc(prov)}</span></div>
    <div class="mem-move-row" data-id="${_esc(it.id)}" style="display:none;">
      <select class="mem-move-target mem-field" style="width:100%;"><option value="">Move / promote to…</option>${moveTargets}</select>
    </div>
  </div>`;
}

function _paneHtml() {
  const owner = _selectedOwner();
  const isGlobal = owner && owner.key === 'global';

  if (_preview) {
    const list = _preview.items.length
      ? _preview.items.map((it, i) => _itemCard(it, i + 1)).join('')
      : `<div class="mem-empty"><div class="s">Nothing would be recalled for this query.</div></div>`;
    return `<div class="mem-pvbar">Recall for <em>"${_esc(_preview.query)}"</em> — ${_preview.items.length} item(s), ranked
      <button class="x" id="mem-preview-clear">Clear</button></div>${list}`;
  }

  const previewBox = isGlobal ? '' : `
    <div class="mem-toolbar">
      <div class="mem-search-wrap" style="flex:1;">${SVG.search}
        <input id="mem-preview-input" class="mem-field" placeholder="Preview: what would @${_esc(owner ? owner.label : '')} recall for…"></div>
      <button id="mem-preview-go" class="mem-add">Preview</button>
    </div>`;

  const kindPills = [['all', 'All'], ['preference', 'Preference'], ['fact', 'Fact'], ['style', 'Style']]
    .map(([v, l]) => {
      const active = _filter.kind === v;
      // Active kind pills carry THEIR kind colour (matching the item kind pills);
      // "All" falls back to the panel accent via .mem-pill.active.
      let style = '';
      if (active && KIND_TINT[v]) {
        const t = KIND_TINT[v];
        style = ` style="color:${t.c};background:color-mix(in srgb,${t.c} 16%,transparent);border-color:color-mix(in srgb,${t.c} 50%,transparent);"`;
      }
      return `<button class="mem-pill ${active ? 'active' : ''}" data-kind="${v}"${style}>${l}</button>`;
    }).join('');
  const sortPills = [['recent', 'Recent'], ['recalled', 'Used'], ['confidence', 'Confidence']]
    .map(([v, l]) => `<button class="mem-pill ${_sort === v ? 'active' : ''}" data-sort="${v}">${l}</button>`).join('');
  const toolbar = `
    <div class="mem-toolbar">
      <div class="mem-search-wrap">${SVG.search}<input id="mem-search" class="mem-field" placeholder="Search memories…" value="${_esc(_filter.q)}"></div>
    </div>
    <div class="mem-filterbar">
      <div class="mem-pills" id="mem-kind-pills">${kindPills}</div>
      <div class="mem-pills mem-sort" id="mem-sort-pills">${sortPills}</div>
    </div>`;

  const items = _ownerItems();
  const list = items.length ? items.map(it => _itemCard(it, null)).join('')
    : `<div class="mem-empty">${ICON}<div class="h">No memories ${isGlobal ? 'shared across agents' : 'for this agent'} yet</div>
        <div class="s">Ask the agent to remember something, or add one below.</div></div>`;

  const bulk = _selection.size ? `<div class="mem-bulk"><span>${_selection.size} selected</span>
      <button class="danger" id="mem-bulk-del" style="margin-left:auto;">Delete</button>
      <button id="mem-bulk-pause">Pause</button></div>` : '';

  const composer = `<div class="mem-composer">
      <input id="mem-add-input" class="mem-field" placeholder="${isGlobal ? 'Remember a shared fact…' : 'Remember a fact for @' + _esc(owner ? owner.label : '') + '…'}">
      <button id="mem-add-btn" class="mem-add">Add</button></div>`;

  const foot = items.length ? `<div class="mem-foot"><button id="mem-clear-view" class="mem-link">Clear all ${items.length} in this view</button></div>` : '';

  return previewBox + bulk + toolbar + `<div>${list}</div>` + composer + foot;
}

function _wire(body) {
  body.querySelectorAll('.mem-owner').forEach(el => el.addEventListener('click', () => {
    _selectedOwnerKey = el.dataset.owner; _preview = null; _selection.clear(); _render();
  }));

  const pv = () => {
    const q = (document.getElementById('mem-preview-input')?.value || '').trim();
    const owner = _selectedOwner();
    if (!q || !owner || owner.key === 'global') return;
    _memApi('/memory/recall-preview', { method: 'POST', body: JSON.stringify({ profile_id: owner.profile_id, query: q }) })
      .then(r => { _preview = { query: q, items: r.items || [] }; _render(); }).catch(() => {});
  };
  document.getElementById('mem-preview-go')?.addEventListener('click', pv);
  document.getElementById('mem-preview-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') pv(); });
  document.getElementById('mem-preview-clear')?.addEventListener('click', () => { _preview = null; _render(); });

  const search = document.getElementById('mem-search');
  if (search) search.addEventListener('input', () => { _filter.q = search.value; _render(); document.getElementById('mem-search')?.focus(); });
  body.querySelectorAll('#mem-kind-pills .mem-pill').forEach(b => b.addEventListener('click', () => { _filter.kind = b.dataset.kind; _render(); }));
  body.querySelectorAll('#mem-sort-pills .mem-pill').forEach(b => b.addEventListener('click', () => { _sort = b.dataset.sort; _render(); }));

  body.querySelectorAll('.mem-check').forEach(cb => cb.addEventListener('change', () => {
    cb.checked ? _selection.add(cb.dataset.id) : _selection.delete(cb.dataset.id); _render();
  }));
  // Per-item kind is a colour pill that cycles preference → fact → style on click.
  const _KINDS = ['preference', 'fact', 'style'];
  body.querySelectorAll('.mem-kind').forEach(b => b.addEventListener('click', () => {
    const next = _KINDS[(_KINDS.indexOf(b.dataset.kind) + 1) % _KINDS.length];
    _put(b.dataset.id, { kind: next });
  }));
  body.querySelectorAll('.mem-toggle').forEach(b => b.addEventListener('click', () => {
    const it = _allItems.find(x => x.id === b.dataset.id); if (it) _put(it.id, { active: !it.active });
  }));
  body.querySelectorAll('.mem-del').forEach(b => b.addEventListener('click', () => _del(b.dataset.id)));
  body.querySelectorAll('.mem-edit').forEach(b => b.addEventListener('click', () => _inlineEdit(b.dataset.id)));
  body.querySelectorAll('.mem-move').forEach(b => b.addEventListener('click', () => {
    const row = body.querySelector(`.mem-move-row[data-id="${CSS.escape(b.dataset.id)}"]`);
    if (row) row.style.display = row.style.display === 'none' ? 'block' : 'none';
  }));
  body.querySelectorAll('.mem-move-target').forEach(sel => sel.addEventListener('change', () => {
    const id = sel.closest('.mem-move-row').dataset.id;
    const owner = _ownerById(sel.value);
    if (owner) _put(id, { profile_id: owner.key === 'global' ? '' : owner.profile_id });
  }));

  document.getElementById('mem-bulk-del')?.addEventListener('click', async () => {
    for (const id of [..._selection]) { try { await _memApi(`/memory/${id}`, { method: 'DELETE' }); } catch (_) {} }
    _selection.clear(); await _loadAndRender();
  });
  document.getElementById('mem-bulk-pause')?.addEventListener('click', async () => {
    for (const id of [..._selection]) { try { await _memApi(`/memory/${id}`, { method: 'PUT', body: JSON.stringify({ active: false }) }); } catch (_) {} }
    _selection.clear(); await _loadAndRender();
  });

  const addInput = document.getElementById('mem-add-input');
  const addBtn = document.getElementById('mem-add-btn');
  const doAdd = async () => {
    const v = (addInput.value || '').trim(); if (!v) return;
    addBtn.disabled = true;
    const owner = _selectedOwner();
    const payload = (owner && owner.key === 'global')
      ? { content: v, scope: 'global' } : { content: v, scope: 'agent', profile_id: owner.profile_id };
    try { await _memApi('/memory', { method: 'POST', body: JSON.stringify(payload) }); await _loadAndRender(); }
    catch (_) { addBtn.disabled = false; }
  };
  addBtn?.addEventListener('click', doAdd);
  addInput?.addEventListener('keydown', e => { if (e.key === 'Enter') doAdd(); });

  document.getElementById('mem-clear-view')?.addEventListener('click', () => {
    const items = _ownerItems();
    const run = async () => { for (const it of items) { try { await _memApi(`/memory/${it.id}`, { method: 'DELETE' }); } catch (_) {} } await _loadAndRender(); };
    if (window.UI?.showConfirmation) {
      window.UI.showConfirmation('Clear this memory view?', `Permanently delete the ${items.length} item(s) shown. This cannot be undone.`, run);
    } else if (confirm('Clear all memory in this view?')) { run(); }
  });
}

function _inlineEdit(id) {
  const card = document.querySelector(`.mem-card[data-id="${CSS.escape(id)}"]`);
  const it = _allItems.find(x => x.id === id);
  if (!card || !it) return;
  const txt = card.querySelector('.mem-txt');
  txt.innerHTML = `<textarea class="mem-field" style="width:100%;min-height:3.2rem;padding:0.45rem 0.6rem;line-height:1.45;">${_esc(it.content)}</textarea>
    <div style="display:flex;gap:0.4rem;margin-top:0.4rem;">
      <button class="mem-add mem-save">Save</button>
      <button class="mem-link mem-cancel" style="color:var(--text-muted,#94a3b8);">Cancel</button></div>`;
  const ta = txt.querySelector('textarea'); ta.focus();
  txt.querySelector('.mem-save').addEventListener('click', () => { const v = ta.value.trim(); if (v) _put(id, { content: v }); else _render(); });
  txt.querySelector('.mem-cancel').addEventListener('click', _render);
}

async function _put(id, fields) {
  try { await _memApi(`/memory/${id}`, { method: 'PUT', body: JSON.stringify(fields) }); await _loadAndRender(); }
  catch (_) { _render(); }
}
async function _del(id) {
  try { await _memApi(`/memory/${id}`, { method: 'DELETE' }); _selection.delete(id); await _loadAndRender(); }
  catch (_) {}
}
