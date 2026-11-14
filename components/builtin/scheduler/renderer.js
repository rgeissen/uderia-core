/**
 * Scheduler Canvas Renderer
 *
 * Follows the exact canvas/knowledge_graph split-panel pattern:
 *   - Inline render: compact confirmation toast (create/update/delete/enable/disable/run_now)
 *   - Split panel: full persistent Task Scheduler panel (list/history)
 *
 * CSS is injected once on first render (same technique as KG renderer).
 * Panels can coexist side-by-side; use the drag handle to resize horizontally.
 */

// ── Constants ─────────────────────────────────────────────────────────────────

// Theme-aware control colours. Consumed only in CSS contexts (inline style /
// .style.color / color-mix); SVG icons use currentColor. NOTE: never concatenate
// hex-alpha onto these (e.g. `${C.fail}20`) — they are var() expressions; use
// color-mix() for tints instead.
const C = {
  accent:  'var(--sp-accent, #d946ef)',
  accentL: 'var(--sp-accent, #d946ef)',
  active:  'var(--status-success, #16a34a)',
  paused:  'var(--text-muted, #94a3b8)',
  running: 'var(--status-info, #0284c7)',
  fail:    'var(--status-error, #dc2626)',
  warn:    'var(--status-warning, #d97706)',
};

// ── CSS injection (once) ──────────────────────────────────────────────────────

function _injectCSS() {
  if (document.getElementById('sched-split-css-v4')) return;
  // Remove stale versions
  document.getElementById('sched-split-css')?.remove();
  document.getElementById('sched-split-css-v2')?.remove();
  document.getElementById('sched-split-css-v3')?.remove();
  const s = document.createElement('style');
  s.id = 'sched-split-css-v4';
  s.textContent = `
/* ── Split panel layout ───────────────────────────────────────────── */
#scheduler-split-panel {
  width: 0;
  min-width: 0;
  max-width: 55%;
  flex-shrink: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--card-bg, rgba(20, 22, 25,0.6));
  border-left: 1px solid var(--border-primary, rgba(255,255,255,0.1));
  transition: width 0.3s ease, min-width 0.3s ease;
}
#scheduler-split-panel.sched-split--open {
  width: 50%;
  min-width: 340px;
}
/* Header chrome (.sp-header/.sp-title/.sp-action-btn) is in static/css/split-panels.css */
.sched-split-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 12px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-secondary, var(--bg-tertiary, #f8fafc));
}

/* ── Inline open button (on chat card) ───────────────────────────── */
.sched-open-ctl-wrap { margin-top: 0.55rem; }

/* ── Task cards ───────────────────────────────────────────────────── */
.sched-canvas { font-family: inherit; }
.sched-canvas * { box-sizing: border-box; border: none; }
.sched-grid { display: flex; flex-direction: column; gap: 10px; }

.sched-card {
  border-radius: 14px;
  border: 1px solid var(--border-secondary, rgba(0,0,0,0.08)) !important;
  background: var(--card-bg, #ffffff);
  box-shadow: 0 2px 8px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  overflow: hidden;
  transition: box-shadow .2s, border-color .2s;
  position: relative;
}
.sched-card::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: color-mix(in srgb, var(--sp-accent, #d946ef) 40%, transparent);
  border-radius: 14px 0 0 14px;
  transition: background .2s;
}
.sched-card:hover {
  box-shadow: 0 4px 16px color-mix(in srgb, var(--sp-accent, #d946ef) 10%, transparent);
  border-color: color-mix(in srgb, var(--sp-accent, #d946ef) 30%, transparent) !important;
}
.sched-card:hover::before { background: var(--sp-accent, #d946ef); }
.sched-card--highlight {
  border-color: color-mix(in srgb, var(--sp-accent, #d946ef) 35%, transparent) !important;
  box-shadow: 0 4px 20px color-mix(in srgb, var(--sp-accent, #d946ef) 12%, transparent);
}
.sched-card--highlight::before { background: var(--sp-accent, #d946ef); }

/* card header */
.sched-card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px 6px 16px;
}
.sched-status-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.sched-status-dot--active {
  background: var(--status-success, #16a34a);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-success, #16a34a) 15%, transparent);
  animation: sched-pulse 2.5s ease-in-out infinite;
}
.sched-status-dot--paused { background: var(--text-muted, #94a3b8); }
.sched-status-dot--fail   { background: var(--status-error, #dc2626); box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-error, #dc2626) 12%, transparent); }
@keyframes sched-pulse { 0%,100%{box-shadow:0 0 0 3px color-mix(in srgb, var(--status-success,#16a34a) 15%, transparent)} 50%{box-shadow:0 0 0 5px color-mix(in srgb, var(--status-success,#16a34a) 5%, transparent)} }

.sched-card-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary, #0f172a);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
  letter-spacing: -0.01em;
}
.sched-card-status-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
  flex-shrink: 0;
}
.sched-card-status-badge--active  { background: color-mix(in srgb, var(--status-success,#16a34a) 12%, transparent); color: var(--status-success,#16a34a); }
.sched-card-status-badge--paused  { background: var(--hover-bg, rgba(100,116,139,.1)); color: var(--text-muted,#64748b); }
.sched-card-status-badge--fail    { background: color-mix(in srgb, var(--status-error,#dc2626) 10%, transparent); color: var(--status-error,#dc2626); }

/* schedule line */
.sched-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 16px 10px 34px;
  font-size: 12px;
  color: var(--sp-accent, #d946ef);
  font-weight: 500;
}

/* next runs */
.sched-next-runs {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 16px 12px 16px;
  flex-wrap: wrap;
}
.sched-next-label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted, #94a3b8);
  margin-right: 2px;
}
.sched-run-chip {
  padding: 3px 9px;
  border-radius: 20px;
  background: color-mix(in srgb, var(--sp-accent, #d946ef) 7%, transparent);
  border: 1px solid color-mix(in srgb, var(--sp-accent, #d946ef) 16%, transparent) !important;
  font-size: 11px;
  color: var(--text-muted, #64748b);
  white-space: nowrap;
}
.sched-run-chip--first {
  background: color-mix(in srgb, var(--sp-accent, #d946ef) 22%, transparent);
  border-color: color-mix(in srgb, var(--sp-accent, #d946ef) 45%, transparent) !important;
  color: var(--sp-accent, #d946ef);
  font-weight: 600;
}

/* badges */
.sched-card-badges { display: flex; gap: 5px; padding: 0 16px 8px 34px; flex-wrap: wrap; }
.sched-badge { display: inline-flex; align-items: center; gap: 3px; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 500; }
.sched-badge--channel { background: color-mix(in srgb, var(--status-info,#2563eb) 9%, transparent); color: var(--status-info,#2563eb); border: 1px solid color-mix(in srgb, var(--status-info,#2563eb) 18%, transparent) !important; }
.sched-badge--session { background: color-mix(in srgb, var(--sp-accent,#d946ef) 8%, transparent); color: var(--sp-accent,#d946ef); border: 1px solid color-mix(in srgb, var(--sp-accent,#d946ef) 18%, transparent) !important; }
.sched-badge--new-sess{ background: var(--hover-bg, rgba(100,116,139,.06)); color: var(--text-muted,#64748b); border: 1px solid var(--border-secondary, rgba(100,116,139,.14)) !important; }
.sched-badge--fail    { background: color-mix(in srgb, var(--status-error,#dc2626) 8%, transparent);  color: var(--status-error,#dc2626); border: 1px solid color-mix(in srgb, var(--status-error,#dc2626) 18%, transparent) !important; }
.sched-badge--success { background: color-mix(in srgb, var(--status-success,#16a34a) 8%, transparent);  color: var(--status-success,#16a34a); border: 1px solid color-mix(in srgb, var(--status-success,#16a34a) 15%, transparent) !important; }

/* action footer */
.sched-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 8px 12px 10px;
  border-top: 1px solid var(--border-secondary, rgba(0,0,0,0.08)) !important;
  background: var(--bg-secondary, rgba(0,0,0,0.015));
  flex-wrap: wrap;
}
.sched-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent !important;
  background: none;
  color: var(--text-muted, #64748b);
  transition: all .15s;
}
.sched-btn:hover {
  background: var(--hover-bg, rgba(0,0,0,0.05));
  color: var(--text-primary, #0f172a);
}
.sched-btn--here:hover { background: color-mix(in srgb, var(--sp-accent,#d946ef) 8%, transparent); color: var(--sp-accent,#d946ef); }
.sched-btn--bg:hover   { background: color-mix(in srgb, var(--status-info,#0284c7) 8%, transparent);  color: var(--status-info,#0284c7); }
.sched-btn--del:hover  { background: color-mix(in srgb, var(--status-error,#dc2626) 6%, transparent);  color: var(--status-error,#dc2626); }
.sched-btn--disabled   { opacity: .45; cursor: not-allowed; pointer-events: none; }
.sched-run-group {
  display: inline-flex;
  border-radius: 8px;
  border: 1px solid var(--border-secondary, rgba(0,0,0,0.08)) !important;
  overflow: hidden;
}
.sched-run-group .sched-btn {
  border-radius: 0 !important;
  border: none !important;
  padding: 5px 9px;
}
.sched-run-group .sched-btn:first-child { border-right: 1px solid var(--border-secondary, rgba(0,0,0,0.08)) !important; }
.sched-btn-spinner { width: 11px; height: 11px; border: 2px solid color-mix(in srgb, var(--sp-accent,#d946ef) 30%, transparent); border-top-color: var(--sp-accent,#d946ef); border-radius: 50%; animation: sched-spin .6s linear infinite; }
@keyframes sched-spin { to{ transform:rotate(360deg); } }
.sched-last-run { margin-left: auto; font-size: 10px; color: var(--text-muted, #94a3b8); font-style: italic; }

/* ── Empty state ──────────────────────────────────────────────────── */
.sched-empty { text-align: center; padding: 40px 16px; color: var(--text-muted, #64748b); }
.sched-empty svg { margin: 0 auto 12px; display: block; opacity: .4; color: var(--text-muted, #94a3b8); }
.sched-empty p { font-size: 13px; }
.sched-empty em { font-style: normal; color: #d946ef; }

/* ── History timeline ─────────────────────────────────────────────── */
.sched-history { display: flex; flex-direction: column; }
.sched-history-item { display: flex; align-items: stretch; gap: 12px; }
.sched-history-item:last-child .sched-history-line { display: none; }
.sched-history-left { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; width: 18px; }
.sched-history-dot  { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
.sched-history-line { flex: 1; width: 2px; background: var(--border-secondary, rgba(0,0,0,0.08)); margin-top: 2px; min-height: 20px; }
.sched-history-content { flex: 1; padding-bottom: 14px; }
.sched-history-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.sched-history-status { font-size: 12px; font-weight: 600; }
.sched-history-time   { font-size: 11px; color: var(--text-muted, #64748b); }
.sched-history-dur    { font-size: 11px; color: var(--text-muted, #64748b); margin-left: auto; }
.sched-history-summary{ font-size: 11px; color: var(--text-muted, #64748b); margin-top: 3px; line-height: 1.5; }
.sched-back-btn { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 8px; font-size: 11px; cursor: pointer; border: 1px solid color-mix(in srgb, var(--sp-accent, #d946ef) 30%, transparent); background: none; color: var(--sp-accent, #d946ef); margin-bottom: 12px; transition: all .15s; }
.sched-back-btn:hover { background: color-mix(in srgb, var(--sp-accent, #d946ef) 8%, transparent); }

/* ── Section header (inside panel body) ──────────────────────────── */
.sched-section-hdr { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.sched-section-hdr-title { font-size: 13px; font-weight: 600; color: var(--text-primary, #1e293b); }
.sched-section-hdr-sub   { font-size: 11px; color: var(--text-muted, #64748b); }

/* ── Error ────────────────────────────────────────────────────────── */
.sched-error { border: 1px solid rgba(220,38,38,.25); border-radius: 10px; padding: 12px 14px; background: rgba(220,38,38,.06); color: #dc2626; font-size: 12px; }

/* ── Inline toast ─────────────────────────────────────────────────── */
.sched-toast { display: flex; align-items: flex-start; gap: 11px; padding: 11px 13px; border-radius: 12px; font-family: inherit; }
.sched-toast-icon { width: 30px; height: 30px; border-radius: 8px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.sched-toast-title { font-size: 13px; font-weight: 600; color: var(--text-primary, #1e293b); }
.sched-toast-sub   { font-size: 11px; color: var(--text-muted, #64748b); margin-top: 3px; display: flex; align-items: center; gap: 4px; }
.sched-toast-badge { display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 20px; font-size: 10px; font-weight: 600; flex-shrink: 0; margin-left: auto; }

/* ── Prompt preview ───────────────────────────────────────────────── */
.sched-prompt {
  padding: 0 16px 10px 34px;
}
.sched-prompt-text {
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-muted, #64748b);
  font-style: italic;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sched-prompt-text.collapsed { -webkit-line-clamp: 2; }
.sched-prompt-expand {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 3px;
  font-size: 10px;
  font-weight: 500;
  color: #d946ef;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  opacity: 0.8;
  transition: opacity .15s;
}
.sched-prompt-expand:hover { opacity: 1; }

/* ── Light mode overrides ─────────────────────────────────────────── */
/* All low-alpha rgba values designed for dark backgrounds need      */
/* higher contrast equivalents when rendered on white/light surfaces  */

[data-theme="light"] .sched-card {
  box-shadow: 0 2px 10px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.06);
  border-color: rgba(148,163,184,0.4) !important;
  background: #ffffff;
}
[data-theme="light"] .sched-card::before {
  background: #d946ef;
}
[data-theme="light"] .sched-card:hover {
  box-shadow: 0 6px 20px rgba(168,85,247,0.14);
  border-color: rgba(168,85,247,0.4) !important;
}

[data-theme="light"] .sched-card-status-badge--active {
  background: rgba(22,163,74,.14);
}
[data-theme="light"] .sched-card-status-badge--paused {
  background: rgba(100,116,139,.14);
}
[data-theme="light"] .sched-card-status-badge--fail {
  background: rgba(220,38,38,.12);
}

[data-theme="light"] .sched-run-chip {
  background: rgba(168,85,247,.08);
  border-color: rgba(168,85,247,.3) !important;
  color: #475569;
}
[data-theme="light"] .sched-run-chip--first {
  background: rgba(168,85,247,.18);
  border-color: rgba(168,85,247,.55) !important;
  color: #5b21b6;
}

[data-theme="light"] .sched-badge--session {
  background: rgba(168,85,247,.12);
  border-color: rgba(168,85,247,.35) !important;
  color: #6d28d9;
}
[data-theme="light"] .sched-badge--new-sess {
  background: rgba(100,116,139,.1);
  border-color: rgba(100,116,139,.3) !important;
  color: #475569;
}
[data-theme="light"] .sched-badge--channel {
  background: rgba(37,99,235,.1);
  border-color: rgba(37,99,235,.3) !important;
  color: #1d4ed8;
}

[data-theme="light"] .sched-actions {
  background: #f8fafc;
  border-top-color: rgba(148,163,184,.25) !important;
}
[data-theme="light"] .sched-run-group {
  border-color: rgba(148,163,184,.35) !important;
}
[data-theme="light"] .sched-run-group .sched-btn:first-child {
  border-right-color: rgba(148,163,184,.3) !important;
}

[data-theme="light"] .sched-btn--here:hover { background: color-mix(in srgb, var(--sp-accent, #d946ef) 12%, transparent); }
[data-theme="light"] .sched-btn--bg:hover   { background: color-mix(in srgb, var(--status-info, #0284c7) 12%, transparent); }
[data-theme="light"] .sched-btn--del:hover  { background: color-mix(in srgb, var(--status-error, #dc2626) 10%, transparent); }


[data-theme="light"] .sched-back-btn {
  border-color: rgba(168,85,247,.45) !important;
}
[data-theme="light"] .sched-error {
  background: rgba(220,38,38,.08);
  border-color: rgba(220,38,38,.35) !important;
}
`;
  document.head.appendChild(s);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function timeAgo(iso) {
  if (!iso) return '—';
  const d = (Date.now() - new Date(iso)) / 1000;
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d/60)}m ago`;
  if (d < 86400) return `${Math.round(d/3600)}h ago`;
  return `${Math.round(d/86400)}d ago`;
}

function timeUntil(iso) {
  if (!iso) return '';
  const d = (new Date(iso) - Date.now()) / 1000;
  if (d <= 0) return 'now';
  if (d < 60) return `in ${Math.floor(d)}s`;
  if (d < 3600) return `in ${Math.round(d/60)}m`;
  if (d < 86400) return `in ${Math.round(d/3600)}h`;
  return `in ${Math.round(d/86400)}d`;
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const mo = d.toLocaleString('en', {month:'short', timeZone:'UTC'});
  const dy = d.getUTCDate();
  const hh = String(d.getUTCHours()).padStart(2,'0');
  const mm = String(d.getUTCMinutes()).padStart(2,'0');
  return `${mo} ${dy}, ${hh}:${mm} UTC`;
}

function _runChips(nextRuns) {
  return (nextRuns || []).map((iso, i) =>
    `<span class="sched-run-chip${i === 0 ? ' sched-run-chip--first' : ''}" data-run-at="${esc(iso)}">${esc(timeUntil(iso))}</span>`
  ).join('');
}

let _chipTickTimer = null;
let _chipTickInterval = 30_000;

function _tickChips() {
  const chips = document.querySelectorAll('.sched-run-chip[data-run-at]');
  if (!chips.length) { clearInterval(_chipTickTimer); _chipTickTimer = null; _chipTickInterval = 30_000; return; }

  let hasSubMinute = false;
  chips.forEach(chip => {
    const text = timeUntil(chip.dataset.runAt);
    if (!text || text === 'now') {
      const row = chip.closest('.sched-next-runs');
      chip.remove();
      if (row && !row.querySelector('.sched-run-chip')) row.remove();
    } else {
      chip.textContent = text;
      if (text.endsWith('s')) hasSubMinute = true;
    }
  });

  // Switch to 1-second ticks when any chip is under a minute, back to 30s otherwise
  const needed = hasSubMinute ? 1_000 : 30_000;
  if (needed !== _chipTickInterval) {
    _chipTickInterval = needed;
    clearInterval(_chipTickTimer);
    _chipTickTimer = setInterval(_tickChips, _chipTickInterval);
  }
}

function _startChipTick() {
  if (_chipTickTimer) return;
  _chipTickTimer = setInterval(_tickChips, _chipTickInterval);
}

function fmtDuration(s, e) {
  if (!s || !e) return '';
  const ms = new Date(e) - new Date(s);
  if (ms < 0) return '';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms/1000).toFixed(1)}s`;
  return `${Math.round(ms/60000)}m ${Math.round((ms%60000)/1000)}s`;
}

function statusColor(st) {
  const m = {success:C.active, error:C.fail, failed:C.fail, running:C.running, skipped:C.warn};
  return m[st] || C.paused;
}
function statusLabel(st) {
  const m = {success:'Success',error:'Error',failed:'Failed',running:'Running',skipped:'Skipped',timeout:'Timeout'};
  return m[st] || (st || 'Pending');
}

const ICONS = {
  clock:   `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>`,
  play:    `<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" fill="currentColor" viewBox="0 0 24 24"><path d="M5 3l14 9L5 21V3z"/></svg>`,
  pause:   `<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>`,
  trash:   `<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>`,
  run:     `<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
  history: `<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-5.1L1 10"/></svg>`,
  check:   `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`,
  x:       `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  arrow:   `<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`,
};

// ── Task card (used inside split panel body) ──────────────────────────────────

function _taskCard(task, highlight) {
  const isEnabled = !!task.enabled;
  const hasFailed = task.last_run_status === 'error';
  const dotMod  = !isEnabled ? 'paused' : hasFailed ? 'fail' : 'active';
  const badgeMod = !isEnabled ? 'paused' : hasFailed ? 'fail' : 'active';
  const badgeTxt = !isEnabled ? 'Paused' : hasFailed ? 'Failed' : 'Active';

  const toggleAction = isEnabled ? 'disable' : 'enable';
  const toggleIcon   = isEnabled ? ICONS.pause : ICONS.play;
  const toggleLabel  = isEnabled ? 'Pause' : 'Resume';

  const channelBadge = task.output_channel
    ? `<span class="sched-badge sched-badge--channel">${esc(task.output_channel)}</span>` : '';

  const isCurrent = task._session_context === 'current';
  const sessionBadge = isCurrent
    ? `<span class="sched-badge sched-badge--session" title="Runs inside the session where this task was created">current session</span>`
    : `<span class="sched-badge sched-badge--new-sess" title="Creates a fresh isolated session on each run">new session</span>`;

  const chips = (task._next_runs || []).map((iso, i) =>
    `<span class="sched-run-chip${i === 0 ? ' sched-run-chip--first' : ''}" data-run-at="${esc(iso)}">${esc(timeUntil(iso))}</span>`
  ).join('');

  const nextRow = chips
    ? `<div class="sched-next-runs"><span class="sched-next-label">Next (UTC)</span>${chips}</div>` : '';

  const badgeContent = [sessionBadge, channelBadge].filter(Boolean).join('');
  const badges = `<div class="sched-card-badges">${badgeContent}</div>`;

  const PREVIEW_LEN = 120;
  const prompt = task.prompt || '';
  const promptNeedsExpand = prompt.length > PREVIEW_LEN;
  const promptPreview = promptNeedsExpand ? prompt.slice(0, PREVIEW_LEN).trimEnd() + '…' : prompt;
  const promptId = `sched-prompt-${esc(task.id)}`;
  const promptRow = prompt ? `
  <div class="sched-prompt">
    <div id="${promptId}" class="sched-prompt-text${promptNeedsExpand ? ' collapsed' : ''}" data-full="${esc(prompt)}" data-preview="${esc(promptPreview)}">${esc(promptPreview)}</div>
    ${promptNeedsExpand ? `<button class="sched-prompt-expand" data-prompt-id="${promptId}" data-expanded="false">
      <svg xmlns="http://www.w3.org/2000/svg" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg>
      Show more
    </button>` : ''}
  </div>` : '';

  return `
<div class="sched-card${highlight ? ' sched-card--highlight' : ''}" id="sched-card-${esc(task.id)}">
  <div class="sched-card-header">
    <span class="sched-status-dot sched-status-dot--${dotMod}"></span>
    <span class="sched-card-name">${esc(task.name)}</span>
    <span class="sched-card-status-badge sched-card-status-badge--${badgeMod}">${badgeTxt}</span>
  </div>
  <div class="sched-card-meta">${ICONS.clock} ${esc(task._human_schedule || task.schedule)}</div>
  ${promptRow}
  ${badges}
  ${nextRow}
  <div class="sched-actions">
    <div class="sched-run-group">
      <button class="sched-btn sched-btn--here" data-action="run_here" data-id="${esc(task.id)}" data-name="${esc(task.name)}" data-prompt="${esc(task.prompt || '')}" title="Run in current chat session">${ICONS.play} Run here</button><button class="sched-btn sched-btn--bg" data-action="run_now" data-id="${esc(task.id)}" data-name="${esc(task.name)}" title="Run as background task (new session)">${ICONS.run} Background</button>
    </div>
    <button class="sched-btn" data-action="${toggleAction}" data-id="${esc(task.id)}" data-name="${esc(task.name)}">${toggleIcon} ${toggleLabel}</button>
    <button class="sched-btn sched-btn--del" data-action="delete" data-id="${esc(task.id)}" data-name="${esc(task.name)}">${ICONS.trash} Delete</button>
    <span class="sched-last-run">${task.last_run_at ? timeAgo(task.last_run_at) : 'never run'}</span>
  </div>
</div>`;
}

// ── History timeline (used inside split panel body) ───────────────────────────

function _renderHistory(runs, taskName, onBack) {
  const backBtn = `<button class="sched-back-btn" id="sched-back-btn">← All Tasks</button>`;

  if (!runs?.length) {
    return `${backBtn}<div class="sched-empty"><p>No runs recorded for "${esc(taskName)}".</p></div>`;
  }
  const items = runs.map(r => {
    const color = statusColor(r.status);
    const dur   = fmtDuration(r.started_at, r.completed_at);
    const summary = r.result_summary ? `<div class="sched-history-summary">${esc(r.result_summary.slice(0,180))}</div>` : '';
    return `
<div class="sched-history-item">
  <div class="sched-history-left">
    <div class="sched-history-dot" style="background:${color};box-shadow:0 0 4px color-mix(in srgb, ${color} 38%, transparent)"></div>
    <div class="sched-history-line"></div>
  </div>
  <div class="sched-history-content">
    <div class="sched-history-row">
      <span class="sched-history-status" style="color:${color}">${statusLabel(r.status)}</span>
      <span class="sched-history-time">${fmtDateTime(r.started_at)}</span>
      ${dur ? `<span class="sched-history-dur">${esc(dur)}</span>` : ''}
    </div>
    ${summary}
  </div>
</div>`;
  }).join('');
  return `${backBtn}<div class="sched-history">${items}</div>`;
}

// ── Split panel public API ────────────────────────────────────────────────────

let _currentSpec = null;

export function openSchedulerSplitPanel(spec) {
  const panel = document.getElementById('scheduler-split-panel');
  if (!panel) return;

  // Prefer preserved server-fetched state over the (potentially stale) toast spec.
  // On the very first open _currentSpec is null so we fall back to spec.
  const renderSpec = _currentSpec || spec;
  _currentSpec = renderSpec;

  // Show panel
  panel.style.display = 'flex';
  panel.offsetHeight; // force reflow
  panel.classList.add('sched-split--open');

  // Wire close button
  const closeBtn = document.getElementById('scheduler-split-close');
  if (closeBtn) closeBtn.onclick = closeSchedulerSplitPanel;

  _reportSchedShown(true);

  _renderPanelBody(renderSpec);
  _refreshPanelFromServer();
}

// Component signals: a task created, changed or deleted anywhere (an agent turn, a REST call,
// another tab) re-reads the open panel (components/signals.py).
if (typeof document !== 'undefined' && typeof UderiaBus !== 'undefined' && !window.__schedFollowBound) {
  window.__schedFollowBound = true;
  UderiaBus.onComponent('scheduler', () => {
    const panel = document.getElementById('scheduler-split-panel');
    if (panel && panel.classList.contains('sched-split--open')) _refreshPanelFromServer();
  });
}

function _refreshPanelFromServer() {
  const token = _getToken();
  const hdrs = token ? { 'Authorization': `Bearer ${token}` } : {};
  fetch('/api/v1/scheduled-tasks', { headers: hdrs })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!data?.tasks) return;
      _currentSpec = _currentSpec
        ? { ..._currentSpec, tasks: data.tasks }
        : { action: 'list', tasks: data.tasks };
      // Only re-render if the panel is still open; if closed, _currentSpec is already
      // up-to-date so the next open will render correctly without a stale flash.
      const panel = document.getElementById('scheduler-split-panel');
      if (panel?.classList.contains('sched-split--open')) _renderPanelBody(_currentSpec);
    })
    .catch(() => {});
}

export function closeSchedulerSplitPanel() {
  const panel = document.getElementById('scheduler-split-panel');
  if (!panel) return;

  // Intentionally NOT clearing _currentSpec — preserved so the next open renders
  // the last-known-good task state instead of a stale LLM-call snapshot.

  _reportSchedShown(false);

  panel.classList.remove('sched-split--open');
  // A fallback timeout guarantees the hide even when 'transitionend' never fires
  // (e.g. closing on a fresh new-chat session that doesn't force a reflow) — else
  // the panel lingers until a page refresh.
  let _hidden = false;
  const finishHide = () => {
    if (_hidden) return;
    _hidden = true;
    panel.removeEventListener('transitionend', onEnd);
    if (!panel.classList.contains('sched-split--open')) {
      panel.style.display = 'none';
      const body = document.getElementById('scheduler-split-content');
      if (body) body.innerHTML = '';
    }
  };
  const onEnd = () => finishHide();
  panel.addEventListener('transitionend', onEnd);
  setTimeout(finishHide, 400);
}

function _renderPanelBody(spec) {
  const body = document.getElementById('scheduler-split-content');
  if (!body) return;
  const { action, tasks = [], affected_task_id, runs = [], error } = spec;

  if (error) {
    body.innerHTML = `<div class="sched-error">${esc(error)}</div>`;
    return;
  }

  if (action === 'history') {
    const taskName = tasks.find(t => t.id === affected_task_id)?.name || '';
    body.innerHTML = `<div class="sched-canvas">${_renderHistory(runs, taskName)}</div>`;
    body.querySelector('#sched-back-btn')?.addEventListener('click', () => {
      if (_currentSpec) _renderPanelBody({ ..._currentSpec, action: 'list', runs: [] });
    });
    return;
  }

  // Task list
  const hdr = `
<div class="sched-section-hdr">
  <span class="sched-section-hdr-title">${tasks.length} Task${tasks.length !== 1 ? 's' : ''}</span>
  <span class="sched-section-hdr-sub">· say "schedule something" to add more</span>
</div>`;

  if (tasks.length === 0) {
    body.innerHTML = `<div class="sched-canvas">
      <div class="sched-empty">
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>
        <p>No scheduled tasks yet.</p>
        <p style="font-size:11px;margin-top:6px">Ask me to schedule something — <em>"Run the weekly report every Monday at 9 AM UTC"</em></p>
      </div>
    </div>`;
    return;
  }

  const sorted = [...tasks].sort((a, b) => {
    if (a.id === affected_task_id) return -1;
    if (b.id === affected_task_id) return 1;
    return (a._next_runs?.[0] || '').localeCompare(b._next_runs?.[0] || '');
  });

  body.innerHTML = `<div class="sched-canvas">${hdr}<div class="sched-grid">${sorted.map(t => _taskCard(t, t.id === affected_task_id)).join('')}</div></div>`;
  _wireCardActions(body, spec);
  _startChipTick();
}

function _wireCardActions(root, spec) {
  // Prompt expand/collapse
  root.querySelectorAll('.sched-prompt-expand').forEach(btn => {
    btn.addEventListener('click', () => {
      const promptId = btn.dataset.promptId;
      const textEl = document.getElementById(promptId);
      if (!textEl) return;
      const expanded = btn.dataset.expanded === 'true';
      if (expanded) {
        textEl.textContent = textEl.dataset.preview;
        textEl.classList.add('collapsed');
        btn.dataset.expanded = 'false';
        btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg> Show more`;
      } else {
        textEl.textContent = textEl.dataset.full;
        textEl.classList.remove('collapsed');
        btn.dataset.expanded = 'true';
        btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="18 15 12 9 6 15"/></svg> Show less`;
      }
    });
  });

  root.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.action;
      const taskId = btn.dataset.id;
      const taskName = btn.dataset.name || taskId;

      const proceed = async () => {
      btn.disabled = true;
      btn.classList.add('sched-btn--disabled');
      const orig = btn.innerHTML;
      btn.innerHTML = `<span class="sched-btn-spinner"></span>`;

      try {
        const token = _getToken();
        const hdrs = { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) };

        if (action === 'run_here') {
          const prompt = btn.dataset.prompt || '';
          if (!prompt) {
            btn.innerHTML = orig; btn.disabled = false; btn.classList.remove('sched-btn--disabled');
            return;
          }
          const submitted = await (window.EventHandlers?.triggerChatQuery?.(prompt) ?? false);
          if (submitted) {
            closeSchedulerSplitPanel();
          } else {
            btn.innerHTML = orig; btn.disabled = false; btn.classList.remove('sched-btn--disabled');
          }
          return;
        }
        if (action === 'run_now') {
          await fetch(`/api/v1/scheduled-tasks/${encodeURIComponent(taskId)}/run-now`, { method: 'POST', headers: hdrs });
          btn.innerHTML = `${ICONS.check} Triggered`;
          btn.style.color = C.active;
          // Refresh the session history panel so the new ephemeral session appears
          UderiaBus.emit('uderia:scheduler-background-run');
          setTimeout(() => { btn.innerHTML = orig; btn.disabled = false; btn.classList.remove('sched-btn--disabled'); }, 2200);
          return;
        }
        if (action === 'delete') {
          await fetch(`/api/v1/scheduled-tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE', headers: hdrs });
          const card = document.getElementById(`sched-card-${taskId}`);
          if (card) { card.style.transition = 'opacity .25s, transform .25s'; card.style.opacity = '0'; card.style.transform = 'translateX(-6px)'; setTimeout(() => card.remove(), 280); }
          // Refresh panel spec and update header count
          if (_currentSpec) {
            _currentSpec = { ..._currentSpec, tasks: (_currentSpec.tasks || []).filter(t => t.id !== taskId) };
            const remaining = _currentSpec.tasks.length;
            const hdrTitle = document.querySelector('#scheduler-split-content .sched-section-hdr-title');
            if (hdrTitle) hdrTitle.textContent = `${remaining} Task${remaining !== 1 ? 's' : ''}`;
          }
          return;
        }
        if (action === 'enable' || action === 'disable') {
          await fetch(`/api/v1/scheduled-tasks/${encodeURIComponent(taskId)}`, {
            method: 'PUT', headers: hdrs, body: JSON.stringify({ enabled: action === 'enable' ? 1 : 0 })
          });
          // Update _currentSpec in memory so re-renders reflect the new state
          if (_currentSpec?.tasks) {
            _currentSpec = {
              ..._currentSpec,
              tasks: _currentSpec.tasks.map(t =>
                t.id === taskId ? { ...t, enabled: action === 'enable' ? 1 : 0 } : t
              ),
            };
          }
          // Re-render the card in place
          const card = document.getElementById(`sched-card-${taskId}`);
          if (card && _currentSpec?.tasks) {
            const updatedTask = _currentSpec.tasks.find(t => t.id === taskId);
            if (updatedTask) {
              const newCard = document.createElement('div');
              newCard.innerHTML = _taskCard(updatedTask, false).trim();
              const newCardEl = newCard.firstChild;
              card.replaceWith(newCardEl);
              // Re-wire actions on the new card element
              _wireCardActions(newCardEl.closest('.sched-grid') || newCardEl.parentElement, _currentSpec);
            }
          }
          return;
        }
      } catch (err) {
        console.error('[Scheduler] action error:', err);
        btn.innerHTML = orig; btn.disabled = false; btn.classList.remove('sched-btn--disabled');
      }
      };

      if (action === 'delete') {
        _schedConfirm(`Delete "${taskName}"? This cannot be undone.`, proceed);
        return;
      }
      proceed();
    });
  });
}

// Theme-compliant confirm (no OS-default dialog). Prefers the app banner,
// falls back to the modal confirm, and only as a last resort the native dialog.
function _schedConfirm(message, onConfirm) {
  if (typeof window.showConfirmBanner === 'function') window.showConfirmBanner(message, onConfirm);
  else if (typeof window.showConfirmation === 'function') window.showConfirmation('Delete Task', `<p>${message}</p>`, onConfirm);
  else if (window.confirm(message)) onConfirm();
}

function _getToken() {
  try { return localStorage.getItem('tda_auth_token') || ''; } catch (_) { return ''; }
}

// ── Inline render (toast in chat) ─────────────────────────────────────────────

const ACTION_META = {
  create:  { label: 'Task Created',  color: C.active,  icon: ICONS.check },
  update:  { label: 'Task Updated',  color: '#c084fc',  icon: ICONS.check },
  delete:  { label: 'Task Deleted',  color: C.fail,    icon: ICONS.trash },
  enable:  { label: 'Task Enabled',  color: C.active,  icon: ICONS.play  },
  disable: { label: 'Task Paused',   color: C.paused,  icon: ICONS.pause },
  run_now: { label: 'Task Triggered',color: C.running, icon: ICONS.run   },
  list:    { label: 'Scheduled Tasks',color:'#c084fc',  icon: ICONS.clock },
  history: { label: 'Run History',   color: '#c084fc',  icon: ICONS.history },
};

function _renderInlineToast(container, spec) {
  const { action, message, tasks = [], affected_task_id, error } = spec;
  if (error) {
    container.innerHTML = `<div class="sched-error">${esc(error)}</div>`;
    return;
  }
  const meta    = ACTION_META[action] || ACTION_META.list;
  // For actions with a specific target use that task; for list/history fall back to
  // the soonest task so the toast always shows upcoming timing.
  const affected = tasks.find(t => t.id === affected_task_id)
    || [...tasks].sort((a, b) => (a._next_runs?.[0] || '').localeCompare(b._next_runs?.[0] || ''))[0]
    || null;
  const chips    = affected?._next_runs?.length
    ? `<div style="margin-top:8px">${_runChips(affected._next_runs)}</div>` : '';


  container.innerHTML = `
<div class="sched-toast" style="border:1px solid color-mix(in srgb, ${meta.color} 16%, transparent);background:color-mix(in srgb, ${meta.color} 3%, transparent);border-radius:12px;">
  <div class="sched-toast-icon" style="background:color-mix(in srgb, ${meta.color} 9%, transparent);border:1px solid color-mix(in srgb, ${meta.color} 19%, transparent);color:${meta.color}">${meta.icon}</div>
  <div style="flex:1;min-width:0">
    <div class="sched-toast-title" data-ans-restates>${esc(message)}</div>
    ${affected ? `<div class="sched-toast-sub" data-ans-restates>${ICONS.clock}&nbsp;${esc(affected._human_schedule || affected.schedule || '')}</div>` : ''}
    ${chips}
    <div class="sched-open-ctl-wrap">${_schedOpenControlHtml()}</div>
  </div>
  <span class="sched-toast-badge" style="background:color-mix(in srgb, ${meta.color} 13%, transparent);border:1px solid color-mix(in srgb, ${meta.color} 31%, transparent);color:var(--text-primary,#f1f5f9)">${esc(meta.label)}</span>
</div>`;
  if (chips) _startChipTick();

  _lastCardSpec = spec;
  // Auto-open for list/history actions — never while an old answer is replayed on a
  // session reload (the session's own restore decides whether the panel was open).
  if ((action === 'list' || action === 'history') && !window.__suppressComponentReplayOpen) {
    setTimeout(() => openSchedulerSplitPanel(_currentSpec || spec), 80);
  }
}

// ── Open-in-panel control (shared: static/js/handlers/panelOpenControl.js) ──────
// One panel lists every task, so every scheduler card opens the same panel ('*').
let _lastCardSpec = null;
function _schedOpenControlHtml() {
  const poc = window.PanelOpenControl;
  if (!poc) return '';
  return poc.html({ component: 'scheduler', id: '*', accent: '#d946ef',
                    panelId: 'scheduler-split-panel', name: 'scheduled tasks', row: false });
}
function _reportSchedShown(open) {
  try { window.PanelOpenControl && window.PanelOpenControl.shown('scheduler', open ? '*' : null); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['scheduler', {
    open() { openSchedulerSplitPanel(_currentSpec || _lastCardSpec || { action: 'list', tasks: [] }); },
    close() { closeSchedulerSplitPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

// ── Main entry point ──────────────────────────────────────────────────────────

export function renderScheduler(containerId, payload) {
  _injectCSS();
  const container = document.getElementById(containerId);
  if (!container) return;

  const spec = typeof payload === 'string' ? JSON.parse(payload) : payload;
  _renderInlineToast(container, spec);

  // Always keep _currentSpec up-to-date so click handlers and subsequent opens
  // always reflect the latest server state (even when the panel is closed).
  _currentSpec = spec;

  // If the panel is already open, refresh it immediately.
  // For list/history: openSchedulerSplitPanel (called via setTimeout in _renderInlineToast)
  // will also re-render shortly, but we update now to eliminate the stale-data window.
  const panel = document.getElementById('scheduler-split-panel');
  if (panel?.classList.contains('sched-split--open')) {
    _renderPanelBody(spec);
  }
}
